package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import androidx.fragment.app.FragmentActivity
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Aktualizace aplikace z vydání na GitHubu: při startu se zeptá logiky, jestli je novější verze,
 * nabídne ji, stáhne APK a předá ho systému k instalaci. Nová verze musí být podepsaná stejným klíčem.
 */
object Updater {
    private const val TAG = "MojeVidea"
    private val main = Handler(Looper.getMainLooper())
    private var askedThisRun = false

    fun currentVersion(context: Context): String = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty()
    } catch (error: Exception) {
        ""
    }

    /** Automatická kontrola: jednou za běh aplikace (a logika se ptá GitHubu nejvýš jednou za pár hodin). */
    fun checkOnStart(activity: FragmentActivity) {
        if (askedThisRun) return
        askedThisRun = true
        check(activity, force = false)
    }

    fun check(activity: FragmentActivity, force: Boolean) {
        val current = currentVersion(activity)
        Api.call("update_check", JSONObject().put("current", current).put("force", force)) { result ->
            if (activity.isFinishing || activity.isDestroyed) return@call
            val data = result.data
            if (data == null) {
                if (force) Toast.makeText(activity, result.error, Toast.LENGTH_LONG).show()
                return@call
            }
            if (!data.optBoolean("available")) {
                if (force) Toast.makeText(activity, "Máte aktuální verzi $current.", Toast.LENGTH_LONG).show()
                return@call
            }
            val notes = data.optString("notes")
            AlertDialog.Builder(activity)
                .setTitle("Nová verze ${data.optString("version")}")
                .setMessage("Máte verzi $current." + if (notes.isEmpty()) "" else "\n\n$notes")
                .setPositiveButton("Aktualizovat") { _, _ -> download(activity, data.optString("url")) }
                .setNegativeButton("Později", null)
                .show()
        }
    }

    private fun download(activity: FragmentActivity, url: String) {
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Aktualizace")
            .setMessage("Stahuji novou verzi…")
            .setCancelable(false)
            .create()
        dialog.show()
        val context = activity.applicationContext
        Thread({
            val file = File(context.cacheDir, "update.apk")
            val error = try {
                fetch(url, file) { percent ->
                    main.post { if (dialog.isShowing) dialog.setMessage("Stahuji novou verzi… $percent %") }
                }
                main.post { if (dialog.isShowing) dialog.setMessage("Předávám systému k instalaci…") }
                install(context, file)
                null
            } catch (problem: Exception) {
                Log.w(TAG, "Aktualizace selhala", problem)
                problem.message ?: problem.javaClass.simpleName
            }
            main.post {
                if (dialog.isShowing && !activity.isDestroyed) dialog.dismiss()
                if (error != null && !activity.isFinishing && !activity.isDestroyed) {
                    AlertDialog.Builder(activity)
                        .setTitle("Aktualizace se nezdařila")
                        .setMessage(error)
                        .setPositiveButton("OK", null)
                        .show()
                }
            }
        }, "update").start()
    }

    private fun fetch(url: String, file: File, progress: (Int) -> Unit) {
        val connection = URL(url).openConnection()
        connection.connectTimeout = 30_000
        connection.readTimeout = 60_000
        connection.setRequestProperty("User-Agent", "MojeVidea-Android")
        try {
            if (connection is HttpURLConnection && connection.responseCode != HttpURLConnection.HTTP_OK) {
                throw java.io.IOException("Server soubor nevydal (HTTP ${connection.responseCode}).")
            }
            val total = connection.contentLengthLong
            var done = 0L
            var shown = -1
            connection.getInputStream().use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(128 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        done += read
                        val percent = if (total > 0) (100 * done / total).toInt() else -1
                        if (percent != shown && percent >= 0) {
                            shown = percent
                            progress(percent)
                        }
                    }
                }
            }
            if (total > 0 && done < total) throw java.io.IOException("Spojení se přerušilo.")
        } finally {
            (connection as? HttpURLConnection)?.disconnect()
        }
    }

    /** Předá APK systémovému instalátoru; ten si podle verze Androidu vyžádá potvrzení. */
    private fun install(context: Context, file: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(context.packageName)
        if (Build.VERSION.SDK_INT >= 31) {
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("update", 0, file.length()).use { output ->
                file.inputStream().use { it.copyTo(output) }
                session.fsync(output)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val callback = PendingIntent.getBroadcast(context, id, Intent(context, UpdateReceiver::class.java), flags)
            session.commit(callback.intentSender)
        }
    }
}

/** Odpověď systémového instalátoru: žádost o potvrzení, nebo výsledek. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    context.startActivity(confirm)
                } catch (error: RuntimeException) {
                    Log.w("MojeVidea", "Potvrzení instalace nelze zobrazit", error)
                }
            }
            PackageInstaller.STATUS_SUCCESS -> Unit  // systém aplikaci sám ukončí a spustí novou verzi příště
            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()
                Log.w("MojeVidea", "Instalace aktualizace selhala: $message")
                Toast.makeText(context, "Aktualizaci se nepodařilo nainstalovat. $message", Toast.LENGTH_LONG).show()
            }
        }
    }
}
