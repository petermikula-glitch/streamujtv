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
class ApiResult(val data: JSONObject?, val error: String?) {
    val ok: Boolean get() = error == null && data != null
}

/**
 * Most k logice v Pythonu (modul app_api, sdílený s doplňkem pro Kodi).
 * Všechna volání běží mimo hlavní vlákno; výsledek se vrací na hlavním vlákně.
 */
object Api {
    private const val TAG = "MojeVidea"
    private val pool = Executors.newFixedThreadPool(4)
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
            .callAttr("configure", appContext.filesDir.absolutePath, systemProxy())
        ready = true
    }

    /** Proxy nastavená v systému (Nastavení sítě); Python ji sám nezjistí, zbytek aplikace ji používá. */
    private fun systemProxy(): String {
        val host = System.getProperty("https.proxyHost").orEmpty()
        val port = System.getProperty("https.proxyPort").orEmpty()
        return if (host.isEmpty() || port.isEmpty()) "" else "http://$host:$port"
    }

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
                Log.w(TAG, "$action: $message")  // dohledatelné přes adb logcat -s MojeVidea
                ApiResult(null, message)
            }
        } catch (error: Throwable) {
            Log.e(TAG, action, error)
            ApiResult(null, "Chyba aplikace: ${error.message ?: error.javaClass.simpleName}")
        }
    }

    fun call(action: String, params: JSONObject = JSONObject(), done: (ApiResult) -> Unit) {
        pool.execute {
            val result = callBlocking(action, params)
            main.post { done(result) }
        }
    }

    /** Volání, na jehož výsledek nikdo nečeká (ukládání pozice). */
    fun send(action: String, params: JSONObject) {
        pool.execute { callBlocking(action, params) }
    }
}
