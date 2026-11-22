package cz.petermikula.mojevidea

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.TextView
import androidx.annotation.OptIn
import androidx.fragment.app.FragmentActivity
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import org.json.JSONObject

/**
 * Přehrávač: adresu streamu získá z logiky v Pythonu, pokračuje od uložené pozice
 * a pozici průběžně ukládá (Rozkoukané, zhlédnuto).
 */
@OptIn(UnstableApi::class)
class PlayerActivity : FragmentActivity() {
    private companion object {
        const val SAVE_EVERY_MS = 15_000L
    }

    private var player: ExoPlayer? = null
    private lateinit var playerView: PlayerView
    private lateinit var message: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var call = ""
    private var params = JSONObject()
    private var track = ""
    private var live = false
    private var started = false
    private var retried = false

    private val saver = object : Runnable {
        override fun run() {
            saveProgress(ended = false)
            handler.postDelayed(this, SAVE_EVERY_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_player)
        playerView = findViewById(R.id.player_view)
        message = findViewById(R.id.player_message)
        call = intent.getStringExtra(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}")
        resolve()
    }

    private fun resolve() {
        message.text = "Připravuji přehrávání…"
        message.visibility = View.VISIBLE
        Api.call(call, params) { result ->
            if (isFinishing || isDestroyed) return@call
            val data = result.data
            if (data == null) {
                fail(result.error ?: "Video se nepodařilo spustit.")
            } else {
                play(data)
            }
        }
    }

    private fun play(stream: JSONObject) {
        track = stream.optString("track")
        live = stream.optBoolean("live")
        val headers = HashMap<String, String>()
        stream.optJSONObject("headers")?.let { json ->
            json.keys().forEach { key -> headers[key] = json.optString(key) }
        }
        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(20_000)
            .setReadTimeoutMs(30_000)
        headers["User-Agent"]?.let { http.setUserAgent(it) }
        if (headers.isNotEmpty()) http.setDefaultRequestProperties(headers)

        val subtitles = ArrayList<MediaItem.SubtitleConfiguration>()
        val list = stream.optJSONArray("subtitles")
        for (index in 0 until (list?.length() ?: 0)) {
            val url = list!!.optString(index)
            if (url.isEmpty()) continue
            val mime = if (url.substringBefore('?').endsWith(".srt", true)) MimeTypes.APPLICATION_SUBRIP else MimeTypes.TEXT_VTT
            subtitles.add(
                MediaItem.SubtitleConfiguration.Builder(Uri.parse(url))
                    .setMimeType(mime)
                    .setLanguage("cs")
                    .setSelectionFlags(if (index == 0) C.SELECTION_FLAG_DEFAULT else 0)
                    .build()
            )
        }
        val item = MediaItem.Builder().setUri(stream.optString("url")).setSubtitleConfigurations(subtitles)
        if (stream.optBoolean("hls")) item.setMimeType(MimeTypes.APPLICATION_M3U8)

        release()
        val exo = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(this, http)))
            .build()
        player = exo
        playerView.player = exo
        exo.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) {
                    message.visibility = View.GONE
                    if (!started) {
                        started = true
                        saveProgress(ended = false, first = true)
                    }
                } else if (state == Player.STATE_ENDED) {
                    saveProgress(ended = true)
                    finish()
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                // Zapamatovaná adresa souboru mohla vypršet: jednou ji zahodit a načíst novou.
                if (!retried && !live && track.isNotEmpty() && !track.startsWith("ivys:")) {
                    retried = true
                    val resumeAt = exo.currentPosition
                    Api.call("link_failed", JSONObject().put("track", track)) {
                        if (isFinishing || isDestroyed) return@call
                        if (resumeAt > 0) saveProgress(ended = false)
                        params.put("fresh", true)
                        resolve()
                    }
                } else {
                    fail("Přehrávání selhalo: ${error.errorCodeName}")
                }
            }
        })
        exo.setMediaItem(item.build())
        val position = (stream.optDouble("position", 0.0) * 1000).toLong()
        if (position > 0 && !live) exo.seekTo(position)
        exo.playWhenReady = true
        exo.prepare()
        playerView.requestFocus()
        handler.removeCallbacks(saver)
        handler.postDelayed(saver, SAVE_EVERY_MS)
    }

    private fun saveProgress(ended: Boolean, first: Boolean = false) {
        val exo = player ?: return
        if (live || track.isEmpty()) return
        val total = exo.duration
        if (total == C.TIME_UNSET || total <= 0) return
        Api.send(
            "progress_save",
            JSONObject().put("track", track).put("position", exo.currentPosition / 1000.0)
                .put("total", total / 1000.0).put("ended", ended).put("started", first),
        )
    }

    private fun fail(text: String) {
        release()
        message.text = "$text\n\nZpět se vrátíte tlačítkem Zpět."
        message.visibility = View.VISIBLE
    }

    private fun release() {
        handler.removeCallbacks(saver)
        player?.release()
        player = null
        playerView.player = null
    }

    override fun onPause() {
        super.onPause()
        player?.pause()
        saveProgress(ended = false)
    }

    override fun onStop() {
        super.onStop()
        saveProgress(ended = false)
        if (isFinishing) release()
    }

    override fun onDestroy() {
        release()
        super.onDestroy()
    }
}
