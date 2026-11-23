package cz.petermikula.mojevidea

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Stahování videí na disk. Běží jako služba v popředí, takže pokračuje i po opuštění aplikace.
 * Frontu a evidenci vede logika v Pythonu (download_next, download_progress, download_finish…);
 * služba přenáší bajty, umí navázat na rozpracovaný soubor a hlásí průběh.
 */
class DownloadService : Service() {
    companion object {
        private const val TAG = "MojeVidea"
        private const val CHANNEL = "downloads"
        private const val NOTIFICATION = 41
        private const val ATTEMPTS = 4
        private const val OFFLINE_WAITS = 20            // tolikrát služba počká na připojení (10 minut)
        private const val OFFLINE_WAIT_MS = 30_000L
        private const val PROGRESS_BYTES = 8L shl 20    // pokus, který stáhl aspoň tolik, nebyl marný
        private const val RESERVE = 64L shl 20          // místo, které má na disku zůstat volné
        private const val FAT_LIMIT = (4L shl 30) - 1   // největší soubor na FAT32
        @Volatile private var working = false

        /** Služba právě stahuje (nebo čeká na připojení). */
        val isWorking: Boolean get() = working

        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, DownloadService::class.java))
            } catch (error: RuntimeException) {
                Log.w(TAG, "Službu stahování se nepodařilo spustit", error)
            }
        }
    }

    private sealed class Outcome {
        class Done(val size: Long, val subtitles: List<String>) : Outcome()
        object Cancelled : Outcome()
        object Paused : Outcome()
        class Failed(val message: String, val retry: Boolean) : Outcome()
    }

    private val main = Handler(Looper.getMainLooper())
    @Volatile private var stopping = false
    private var requested = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        showProgress("Připravuji stahování…", -1)
        requested = true
        if (!working) launch()
        return START_NOT_STICKY
    }

    /** Jedno pracovní vlákno; požadavek, který přijde během dobíhání, spustí další kolo. */
    private fun launch() {
        working = true
        requested = false
        Thread({
            try {
                drain()
            } catch (error: Throwable) {
                Log.e(TAG, "Stahování selhalo", error)
            }
            main.post {
                working = false
                if (requested && !stopping) {
                    launch()
                } else {
                    @Suppress("DEPRECATION")
                    stopForeground(true)
                    stopSelf()
                }
            }
        }, "downloads").start()
    }

    private fun drain() {
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        val wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mojevidea:downloads")
        wakeLock.acquire(12 * 60 * 60 * 1000L)
        try {
            var attempt = 0
            var last = ""
            var waits = 0
            var cleaned = false
            while (!stopping) {
                val preferred = Api.callBlocking("settings_get").data?.optString("download_dir").orEmpty()
                val volume = Storage.target(this, preferred)
                if (!cleaned && volume != null) {
                    // jednou za běh služby: rozpracované soubory, ke kterým už není záznam, jen zabírají místo
                    cleaned = true
                    Api.callBlocking("downloads_cleanup", JSONObject().put("dir", volume.dir.absolutePath))
                }
                val next = Api.callBlocking(
                    "download_next",
                    JSONObject().put("dir", volume?.dir?.absolutePath.orEmpty()).put("fresh", attempt > 0),
                )
                val job = next.data
                if (job == null) {
                    // Výpadek připojení: fronta zůstává, služba chvíli počká a zkusí to znovu.
                    if (next.offline && waits < OFFLINE_WAITS) {
                        waits++
                        showProgress("Čekám na připojení k internetu…", -1)
                        Thread.sleep(OFFLINE_WAIT_MS)
                        continue
                    }
                    toast(next.error ?: "Stahování se nepodařilo spustit.")
                    return
                }
                waits = 0
                val vid = job.optString("vid")
                if (vid.isEmpty()) return
                if (vid != last) {
                    attempt = 0
                    last = vid
                }
                val title = job.optString("title")
                val before = File(job.optString("part")).length()
                when (val outcome = transfer(job)) {
                    is Outcome.Done -> {
                        Api.callBlocking(
                            "download_finish",
                            JSONObject().put("vid", vid).put("size", outcome.size)
                                .put("subtitles", JSONArray(outcome.subtitles)),
                        )
                        toast("Staženo: $title")
                        attempt = 0
                    }
                    Outcome.Cancelled -> {
                        File(job.optString("part")).delete()
                        attempt = 0
                    }
                    Outcome.Paused -> return  // položka zůstává rozpracovaná; naváže se při příštím spuštění
                    is Outcome.Failed -> {
                        // pokus, který kus souboru stáhl, se nepočítá jako marný: velký soubor na slabém
                        // připojení se stáhne na víc navázání
                        if (File(job.optString("part")).length() > before + PROGRESS_BYTES) attempt = 0
                        attempt++
                        Log.w(TAG, "Stahování $vid: ${outcome.message} (pokus $attempt)")
                        if (!outcome.retry || attempt >= ATTEMPTS) {
                            Api.callBlocking("download_fail", JSONObject().put("vid", vid).put("error", outcome.message))
                            toast("$title: ${outcome.message}")
                            attempt = 0
                        } else {
                            showProgress("$title – zkouším znovu…", -1)
                            Thread.sleep(5000L * attempt)
                        }
                    }
                }
            }
        } finally {
            if (wakeLock.isHeld) wakeLock.release()
        }
    }

    private fun transfer(job: JSONObject): Outcome {
        val vid = job.optString("vid")
        val title = job.optString("title")
        val target = File(job.optString("path"))
        val part = File(job.optString("part"))
        val folder = target.parentFile ?: return Outcome.Failed("Neplatné umístění souboru.", false)
        var connection: HttpURLConnection? = null
        try {
            folder.mkdirs()
            var existing = if (part.exists()) part.length() else 0L
            val opened = URL(job.optString("url")).openConnection() as? HttpURLConnection
                ?: return Outcome.Failed("Nepodporovaná adresa souboru.", false)
            connection = opened
            job.optJSONObject("headers")?.let { headers ->
                headers.keys().forEach { key -> opened.setRequestProperty(key, headers.optString(key)) }
            }
            opened.connectTimeout = 30_000
            opened.readTimeout = 60_000
            if (existing > 0) opened.setRequestProperty("Range", "bytes=$existing-")
            val code = opened.responseCode
            if (code == 416) {  // rozpracovaný soubor neodpovídá tomu na serveru – začít znovu
                part.delete()
                return Outcome.Failed("Server odmítl pokračování stahování.", true)
            }
            if (code != HttpURLConnection.HTTP_OK && code != HttpURLConnection.HTTP_PARTIAL) {
                return Outcome.Failed("Server soubor nevydal (HTTP $code).", code in setOf(401, 403, 404, 410, 429) || code >= 500)
            }
            if (code == HttpURLConnection.HTTP_OK) existing = 0  // server pokračování neumí: od začátku
            if (code == HttpURLConnection.HTTP_PARTIAL && existing > 0) {
                // Navazuje se jen na tentýž soubor: jiná celková délka znamená jinou verzi (jiná kvalita,
                // náhradní soubor) a spojením by vznikl poškozený soubor.
                val whole = opened.getHeaderField("Content-Range")?.substringAfter('/', "")?.toLongOrNull() ?: 0L
                val expected = job.optLong("total")
                if (whole > 0 && expected > 0 && whole != expected) {
                    part.delete()
                    return Outcome.Failed("Soubor na serveru se změnil, stahuji ho znovu od začátku.", true)
                }
            }
            val length = opened.contentLengthLong
            val total = if (length > 0) existing + length else 0L
            if (total > FAT_LIMIT && Storage.isFat(folder)) {
                return Outcome.Failed("Soubor má ${Storage.sizeText(total)} a disk ve formátu FAT32 uloží nejvýš 4 GB. Naformátujte disk jako exFAT.", false)
            }
            if (total > 0 && folder.usableSpace < total - existing + RESERVE) {
                return Outcome.Failed("Na disku není dost místa (potřeba ${Storage.sizeText(total - existing)}, volno ${Storage.sizeText(folder.usableSpace)}).", false)
            }
            var done = existing
            if (report(vid, done, total)) return Outcome.Cancelled
            var reported = SystemClock.elapsedRealtime()
            opened.inputStream.use { input ->
                FileOutputStream(part, existing > 0).use { output ->
                    val buffer = ByteArray(256 * 1024)
                    while (true) {
                        if (stopping) return Outcome.Paused
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        done += read
                        val now = SystemClock.elapsedRealtime()
                        if (now - reported >= 1500) {
                            reported = now
                            if (report(vid, done, total)) return Outcome.Cancelled
                            showProgress(title, if (total > 0) (100 * done / total).toInt() else -1)
                        }
                    }
                    output.fd.sync()
                }
            }
            if (total > 0 && done < total) return Outcome.Failed("Spojení se přerušilo.", true)
            if (target.exists()) target.delete()
            if (!part.renameTo(target)) return Outcome.Failed("Soubor se nepodařilo uložit.", false)
            return Outcome.Done(done, subtitles(job))
        } catch (error: IOException) {
            val text = error.message.orEmpty()
            return when {
                "EFBIG" in text || "File too large" in text ->
                    Outcome.Failed("Soubor je větší než 4 GB a disk je ve formátu FAT32. Naformátujte disk jako exFAT.", false)
                "ENOSPC" in text || "No space" in text -> Outcome.Failed("Na disku došlo místo.", false)
                !folder.exists() -> Outcome.Failed("Disk byl odpojen.", false)
                else -> Outcome.Failed("Chyba spojení (${error.javaClass.simpleName}).", true)
            }
        } finally {
            connection?.disconnect()
        }
    }

    /** Titulky jsou malé soubory vedle videa; když se nestáhnou, video zůstane bez nich. */
    private fun subtitles(job: JSONObject): List<String> {
        val saved = ArrayList<String>()
        val list = job.optJSONArray("subtitles") ?: return saved
        for (index in 0 until list.length()) {
            val item = list.optJSONObject(index) ?: continue
            try {
                val connection = URL(item.optString("url")).openConnection()
                connection.connectTimeout = 20_000
                connection.readTimeout = 30_000
                val file = File(item.optString("path"))
                connection.getInputStream().use { input -> file.outputStream().use { input.copyTo(it) } }
                saved.add(file.absolutePath)
            } catch (error: Exception) {
                Log.w(TAG, "Titulky se nestáhly: ${error.message}")
            }
        }
        return saved
    }

    /** Uloží průběh; vrací true, když uživatel stahování mezitím zrušil. */
    private fun report(vid: String, done: Long, total: Long): Boolean {
        val result = Api.callBlocking("download_progress", JSONObject().put("vid", vid).put("done", done).put("total", total))
        return result.data?.optBoolean("cancel") == true
    }

    private fun showProgress(text: String, percent: Int) {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "Stahování", NotificationManager.IMPORTANCE_LOW))
        }
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_tile_download)
            .setContentTitle("Stahování na disk")
            .setContentText(if (percent >= 0) "$text · $percent %" else text)
            .setProgress(100, percent.coerceAtLeast(0), percent < 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION, notification)
        }
    }

    private fun toast(text: String) {
        main.post { Toast.makeText(applicationContext, text, Toast.LENGTH_LONG).show() }
    }

    /** Android 15 omezuje dobu služby tohoto druhu; rozpracované stahování naváže při příštím spuštění. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        stopping = true
        stopSelf()
    }

    override fun onDestroy() {
        stopping = true
        super.onDestroy()
    }
}
