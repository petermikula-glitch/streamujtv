package cz.petermikula.mojevidea

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import android.util.Log

class App : Application() {
    private var asked = 0L

    override fun onCreate() {
        super.onCreate()
        Api.start(this)
        watchForResume()
    }

    /**
     * Stahování, které se zastavilo kvůli výpadku připojení nebo odpojenému disku, naváže samo, jakmile
     * se síť nebo disk vrátí – dokud aplikace běží (jinak naváže při příštím spuštění). Když systém
     * spuštění služby na pozadí nedovolí, zůstane fronta čekat.
     */
    private fun watchForResume() {
        try {
            val network = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            network.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = resumeDownloads()
            })
        } catch (error: RuntimeException) {
            Log.w("MojeVidea", "Sledování připojení se nepodařilo zapnout", error)
        }
        try {
            val mounted = IntentFilter(Intent.ACTION_MEDIA_MOUNTED).apply { addDataScheme("file") }
            androidx.core.content.ContextCompat.registerReceiver(this, object : BroadcastReceiver() {
                override fun onReceive(context: Context, intent: Intent) = resumeDownloads()
            }, mounted, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        } catch (error: RuntimeException) {
            Log.w("MojeVidea", "Sledování připojení disku se nepodařilo zapnout", error)
        }
    }

    private fun resumeDownloads() {
        val now = SystemClock.elapsedRealtime()
        if (DownloadService.isWorking || (asked != 0L && now - asked < 20_000)) return
        asked = now
        Api.call("downloads_pending") { result ->
            if (result.data?.optBoolean("pending") == true && !DownloadService.isWorking) DownloadService.start(this)
        }
    }
}
