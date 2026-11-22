package cz.petermikula.mojevidea

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.fragment.app.FragmentActivity
import androidx.tvprovider.media.tv.TvContractCompat
import androidx.tvprovider.media.tv.WatchNextProgram
import org.json.JSONObject

/**
 * Řada „Pokračovat ve sledování“ na domovské obrazovce Android TV: rozkoukaná videa aktivního profilu.
 * Položky dodává logika (akce `watch_next`); zařízení bez této řady (telefon, emulátor) se tiše přeskočí.
 */
object WatchNext {
    private const val TAG = "MojeVidea"
    private val handler = Handler(Looper.getMainLooper())
    private var pending: Runnable? = null

    /** Naplánuje obnovu řady; opakovaná volání se slučují. */
    fun sync(context: Context, delayMs: Long = 0) {
        val app = context.applicationContext
        pending?.let { handler.removeCallbacks(it) }
        val work = Runnable {
            Api.call("watch_next") { result ->
                val items = result.data?.optJSONArray("items") ?: return@call
                Thread { write(app, (0 until items.length()).mapNotNull { items.optJSONObject(it) }) }.start()
            }
        }
        pending = work
        handler.postDelayed(work, delayMs)
    }

    private fun write(context: Context, items: List<JSONObject>) {
        try {
            val resolver = context.contentResolver
            // aplikace vidí jen své položky: smazat a zapsat aktuální stav
            resolver.delete(TvContractCompat.WatchNextPrograms.CONTENT_URI, null, null)
            for (item in items) {
                val link = Uri.Builder().scheme("mojevidea").authority("play")
                    .appendQueryParameter("call", item.optString("call"))
                    .appendQueryParameter("params", (item.optJSONObject("params") ?: JSONObject()).toString())
                    .build()
                val program = WatchNextProgram.Builder()
                    .setType(
                        if (item.optBoolean("episode")) TvContractCompat.WatchNextPrograms.TYPE_TV_EPISODE
                        else TvContractCompat.WatchNextPrograms.TYPE_MOVIE
                    )
                    .setWatchNextType(TvContractCompat.WatchNextPrograms.WATCH_NEXT_TYPE_CONTINUE)
                    .setLastEngagementTimeUtcMillis(item.optLong("played") * 1000)
                    .setTitle(item.optString("title"))
                    .setLastPlaybackPositionMillis(item.optInt("position") * 1000)
                    .setDurationMillis(item.optInt("duration") * 1000)
                    .setInternalProviderId(item.optString("id"))
                    .setIntent(Intent(Intent.ACTION_VIEW, link).setPackage(context.packageName))
                item.optString("image").takeIf { it.startsWith("http") }?.let {
                    program.setPosterArtUri(Uri.parse(it))
                    program.setPosterArtAspectRatio(TvContractCompat.PreviewProgramColumns.ASPECT_RATIO_16_9)
                }
                resolver.insert(TvContractCompat.WatchNextPrograms.CONTENT_URI, program.build().toContentValues())
            }
        } catch (error: Exception) {
            // zařízení bez televizního poskytovatele, nebo bez oprávnění: řada prostě nebude
            Log.w(TAG, "watch next: ${error.javaClass.simpleName}: ${error.message}")
        }
    }
}

/** Spuštění rozkoukaného videa z domovské obrazovky televize. Přijímá jen přehrání, nic jiného. */
class LinkActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val data = intent?.data
        val call = data?.getQueryParameter("call").orEmpty()
        if (call == "play" || call == "ivys_play") {
            val params = try {
                JSONObject(data?.getQueryParameter("params") ?: "{}")
            } catch (error: org.json.JSONException) {
                JSONObject()
            }
            params.put("resume", true)  // „Pokračovat“: naváže bez ptaní
            startActivity(
                Intent(this, PlayerActivity::class.java)
                    .putExtra(Navigator.EXTRA_CALL, call)
                    .putExtra(Navigator.EXTRA_PARAMS, params.toString())
            )
        }
        finish()
    }
}
