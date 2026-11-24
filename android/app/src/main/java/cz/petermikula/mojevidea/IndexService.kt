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
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject

/**
 * Drží aplikaci naživu, dokud se plní seznam dostupných titulů (`app_index.py`). Bez toho by Android
 * aplikaci, která běží jen na pozadí (spořič obrazovky, noc s puštěnou televizí), po pár minutách
 * uspal a procházení by stálo až do dalšího otevření.
 *
 * Každých pár minut se zeptá logiky (`index_keepalive`): ta procházení znovu spustí, pokud se po
 * výpadku spojení zastavilo, a řekne, jestli ještě běží. Když neběží (seznam je projitý, nebo je
 * dnešní limit vyčerpaný), služba skončí sama. Nejdéle běží `MAX_HOURS` hodin od spuštění.
 */
class IndexService : Service() {
    companion object {
        private const val TAG = "IndexService"
        private const val CHANNEL = "index"
        private const val NOTIFICATION = 2
        private const val CHECK_EVERY_MS = 5 * 60_000L
        private const val MAX_HOURS = 10

        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, IndexService::class.java))
            } catch (error: RuntimeException) {
                // systém může spuštění služby z pozadí odmítnout; seznam se pak plní jen při otevřené aplikaci
                Log.w(TAG, "Službu seznamu titulů se nepodařilo spustit", error)
            }
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var started = 0L
    private val check = object : Runnable {
        override fun run() {
            if (System.currentTimeMillis() - started > MAX_HOURS * 3_600_000L) {
                stopSelf()
                return
            }
            Api.call("index_keepalive", JSONObject()) { result ->
                if (result.data?.optBoolean("active") == true) handler.postDelayed(this, CHECK_EVERY_MS)
                else stopSelf()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (started == 0L) {
            started = System.currentTimeMillis()
            showNotification()
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mojevidea:index").apply {
                setReferenceCounted(false)
                acquire(MAX_HOURS * 3_600_000L)
            }
            handler.postDelayed(check, CHECK_EVERY_MS)
        }
        return START_NOT_STICKY
    }

    private fun showNotification() {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "Seznam titulů", NotificationManager.IMPORTANCE_MIN))
        }
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_tile_help)
            .setContentTitle("Promítárna")
            .setContentText("Doplňuje seznam filmů a seriálů na Přehraj.to")
            .setOngoing(true)
            .setSilent(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION, notification)
        }
    }

    override fun onDestroy() {
        handler.removeCallbacks(check)
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }
}
