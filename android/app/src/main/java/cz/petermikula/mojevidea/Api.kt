package cz.petermikula.mojevidea

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.util.concurrent.Executors

/** Výsledek volání logiky: data, nebo text chyby určený uživateli. */
class ApiResult(val data: JSONObject?, val error: String?, val offline: Boolean = false) {
    val ok: Boolean get() = error == null && data != null
}

/**
 * Most k logice v Pythonu (modul app_api, sdílený s Windows verzí).
 * Všechna volání běží mimo hlavní vlákno; výsledek se vrací na hlavním vlákně.
 */
object Api {
    private const val TAG = "MojeVidea"
    // o něco víc vláken, než kolik je obrazovek: výpisy z Objevuj při ověřování dostupnosti chvíli čekají
    private val pool = Executors.newFixedThreadPool(6)
    // Zápisy (pozice přehrávání) jdou jedním vláknem v pořadí, v jakém vznikly, a čtení na ně počká:
    // hlavní obrazovka po návratu z přehrávače tak vždy vidí už uloženou pozici.
    private val writer = Executors.newSingleThreadExecutor()
    private val writes = java.util.concurrent.ConcurrentLinkedQueue<java.util.concurrent.Future<*>>()
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var ready = false
    private lateinit var appContext: Context

    fun start(context: Context) {
        appContext = context.applicationContext
        pool.execute { ensureReady() }  // Python se spouští asi sekundu – nezdržovat první obrazovku
    }

    @Synchronized
    private fun ensureReady() {
        if (ready) return
        if (!Python.isStarted()) Python.start(AndroidPlatform(appContext))
        Python.getInstance().getModule("app_api")
            .callAttr("configure", appContext.filesDir.absolutePath, systemProxy(), proxyExceptions())
        ready = true
    }

    /** Proxy nastavená v systému (Nastavení sítě); Python ji sám nezjistí, zbytek aplikace ji používá. */
    private fun systemProxy(): String {
        val host = System.getProperty("https.proxyHost").orEmpty()
        val port = System.getProperty("https.proxyPort").orEmpty()
        return if (host.isEmpty() || port.isEmpty()) "" else "http://$host:$port"
    }

    /** Adresy, které jdou mimo systémovou proxy (Java je odděluje svislítkem, urllib čárkou). */
    private fun proxyExceptions(): String =
        System.getProperty("http.nonProxyHosts").orEmpty().replace('|', ',')

    /** Synchronní volání – jen z vlákna na pozadí. */
    fun callBlocking(action: String, params: JSONObject = JSONObject()): ApiResult {
        return try {
            ensureReady()
            val raw = Python.getInstance().getModule("app_api")
                .callAttr("call", action, params.toString()).toString()
            val json = JSONObject(raw)
            if (json.optBoolean("ok")) {
                ApiResult(json.optJSONObject("data") ?: JSONObject(), null)
            } else {
                val message = json.optString("error", "Neznámá chyba")
                // dohledatelné přes adb logcat -s MojeVidea; u výpadku spojení i technický popis
                Log.w(TAG, "$action: " + json.optString("detail").ifEmpty { message })
                ApiResult(null, message, json.optBoolean("offline"))
            }
        } catch (error: Throwable) {
            Log.e(TAG, action, error)
            ApiResult(null, "Chyba aplikace: ${error.message ?: error.javaClass.simpleName}")
        }
    }

    fun call(action: String, params: JSONObject = JSONObject(), done: (ApiResult) -> Unit) {
        pool.execute {
            awaitWrites()
            val result = callBlocking(action, params)
            main.post { done(result) }
        }
    }

    /** Volání, na jehož výsledek nikdo nečeká (ukládání pozice). */
    fun send(action: String, params: JSONObject) {
        writes.removeAll { it.isDone }  // dlouhý film ukládá pozici průběžně: hotové zápisy se nehromadí
        writes.add(writer.submit { callBlocking(action, params) })
    }

    /** Počká (krátce) na rozpracované zápisy, aby čtení nepředběhlo ukládání. */
    private fun awaitWrites() {
        while (true) {
            // nahlédnout, počkat a teprve pak odebrat: na tentýž zápis tak počkají i souběžná čtení
            val write = writes.peek() ?: return
            try {
                write.get(3, java.util.concurrent.TimeUnit.SECONDS)
            } catch (error: Exception) {
                // zápis trvá déle nebo selhal: čtení kvůli němu nestojí
            }
            writes.remove(write)
        }
    }
}
