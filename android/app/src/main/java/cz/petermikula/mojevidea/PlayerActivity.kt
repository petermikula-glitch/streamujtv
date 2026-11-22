package cz.petermikula.mojevidea

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.app.AlertDialog
import android.view.KeyEvent
import android.view.View
import android.widget.Button
import android.widget.ImageButton
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.widget.ImageViewCompat
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.TrackSelectionParameters
import androidx.media3.common.Tracks
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
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
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import org.json.JSONObject
import java.io.File

/**
 * Přehrávač: adresu streamu získá z logiky v Pythonu, pokračuje od uložené pozice
 * a pozici průběžně ukládá (Rozkoukané, zhlédnuto).
 */
@OptIn(UnstableApi::class)
class PlayerActivity : FragmentActivity() {
    private companion object {
        const val SAVE_EVERY_MS = 15_000L
        const val NEXT_COUNTDOWN = 10  // sekund, než se další díl spustí sám
        const val ASK_RESUME_AFTER_MS = 60_000L  // kratší rozkoukání se naváže bez ptaní
    }

    private var player: ExoPlayer? = null
    private lateinit var playerView: PlayerView
    private lateinit var message: TextView
    private lateinit var spinner: View
    private lateinit var heading: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var call = ""
    private var params = JSONObject()
    private var track = ""
    private var live = false
    private var local = false
    private var diagnostic = ""
    private var started = false
    private var retried = false
    private var inspected = false   // u chráněného pořadu už proběhl pokus najít adresu licence v manifestu
    private lateinit var top: View
    private lateinit var endTime: TextView
    private lateinit var audioButton: ImageButton
    private lateinit var subtitleButton: ImageButton
    private lateinit var restartButton: ImageButton
    private var tracksKey = ""       // pod čím se pamatuje zvuk a titulky (seriál, film)
    private var audioChoice = ""
    private var subtitleChoice = ""
    private var userChangedTracks = false
    private var seenAudio = ""       // co skutečně hrálo při poslední změně stop
    private var seenSubtitles = ""

    /** Čas konce se počítá průběžně, dokud je lišta vidět. */
    private val clock = object : Runnable {
        override fun run() {
            showEndTime()
            handler.postDelayed(this, 1000)
        }
    }
    private lateinit var nextPanel: View
    private lateinit var nextPlay: Button
    private var nextTarget: Target? = null
    private var countdown = 0

    /** Odpočet do samočinného spuštění dalšího dílu. */
    private val tick = object : Runnable {
        override fun run() {
            if (countdown <= 0) {
                startNext()
                return
            }
            nextPlay.text = "Přehrát ($countdown)"
            countdown -= 1
            handler.postDelayed(this, 1000)
        }
    }

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
        spinner = findViewById(R.id.player_spinner)
        heading = findViewById(R.id.player_title)
        top = findViewById(R.id.player_top)
        endTime = findViewById(R.id.player_end)
        restartButton = findViewById(R.id.player_restart)
        audioButton = findViewById(R.id.player_audio)
        subtitleButton = findViewById(R.id.player_subtitles)
        val tint = ContextCompat.getColorStateList(this, R.color.button_text)
        for (button in listOf(restartButton, audioButton, subtitleButton)) ImageViewCompat.setImageTintList(button, tint)
        restartButton.setOnClickListener { player?.seekTo(0) }
        audioButton.setOnClickListener { chooseTrack(C.TRACK_TYPE_AUDIO) }
        subtitleButton.setOnClickListener { chooseTrack(C.TRACK_TYPE_TEXT) }
        nextPanel = findViewById(R.id.next_panel)
        nextPlay = findViewById(R.id.next_play)
        nextPlay.setOnClickListener { startNext() }
        findViewById<Button>(R.id.next_close).apply {
            setOnClickListener { finish() }
            // kdo přejde na Zavřít, chce se rozhodnout sám: odpočet se zastaví
            setOnFocusChangeListener { _, focused -> if (focused) stopCountdown() }
        }
        // horní lišta (název, čas konce, rychlé volby) se ukazuje spolu s ovládáním
        playerView.setControllerVisibilityListener(
            PlayerView.ControllerVisibilityListener { visibility ->
                val hadFocus = top.hasFocus()
                top.visibility = if (nextPanel.visibility == View.VISIBLE) View.GONE else visibility
                handler.removeCallbacks(clock)
                if (visibility == View.VISIBLE) handler.post(clock) else if (hadFocus) playerView.requestFocus()
            }
        )
        call = intent.getStringExtra(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}")
        resolve()
    }

    private fun resolve() {
        message.text = "Připravuji přehrávání…"
        message.visibility = View.VISIBLE
        spinner.visibility = View.VISIBLE
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

    /** Rozkoukané video: zeptat se, jestli navázat, nebo začít znovu. */
    private fun play(stream: JSONObject) {
        val position = (stream.optDouble("position", 0.0) * 1000).toLong()
        val asks = position >= ASK_RESUME_AFTER_MS && !stream.optBoolean("live") &&
            !params.optBoolean("resume") && !params.optBoolean("fresh") && !params.optBoolean("online") &&
            !params.optBoolean("inspect")
        if (!asks) {
            begin(stream, position)
            return
        }
        spinner.visibility = View.GONE
        message.visibility = View.GONE
        var chosen = false
        AlertDialog.Builder(this)
            .setTitle(stream.optString("title"))
            .setItems(arrayOf("Pokračovat od ${clockText(position)}", "Přehrát od začátku")) { _, which ->
                chosen = true
                begin(stream, if (which == 0) position else 0)
            }
            .setOnDismissListener { if (!chosen && !isFinishing) finish() }  // Zpět: nic se nepřehraje
            .show()
    }

    private fun clockText(ms: Long): String {
        val seconds = ms / 1000
        return if (seconds >= 3600) "%d:%02d:%02d".format(seconds / 3600, seconds / 60 % 60, seconds % 60)
        else "%d:%02d".format(seconds / 60, seconds % 60)
    }

    private fun begin(stream: JSONObject, position: Long) {
        track = stream.optString("track")
        local = stream.optBoolean("local")  // soubor stažený na disku: cesta místo adresy
        fun source(value: String): Uri = if (local) Uri.fromFile(File(value)) else Uri.parse(value)
        heading.text = stream.optString("title")
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
                MediaItem.SubtitleConfiguration.Builder(source(url))
                    .setMimeType(mime)
                    .setLanguage("cs")
                    .setSelectionFlags(if (index == 0) C.SELECTION_FLAG_DEFAULT else 0)
                    .build()
            )
        }
        val item = MediaItem.Builder().setUri(source(stream.optString("url"))).setSubtitleConfigurations(subtitles)
        if (stream.optBoolean("hls")) item.setMimeType(MimeTypes.APPLICATION_M3U8)
        if (stream.optBoolean("dash")) item.setMimeType(MimeTypes.APPLICATION_MPD)
        // Chráněný pořad: dešifruje vestavěný Widevine zařízení. Licenci vydává server, jehož adresu
        // dodal poskytovatel – v odpovědi rozhraní, nebo přímo v manifestu streamu (pak se tu žádná
        // nenastavuje a přehrávač použije tu z manifestu). Nic se neukládá ani neobchází.
        diagnostic = stream.optString("diagnostic")
        stream.optJSONObject("drm")?.let { drm ->
            val config = MediaItem.DrmConfiguration.Builder(C.WIDEVINE_UUID).setMultiSession(true)
            drm.optString("license").takeIf { it.isNotEmpty() }?.let { config.setLicenseUri(it) }
            item.setDrmConfiguration(config.build())
        }

        release()
        playerView.useController = true
        // Dekodéry zařízení mají přednost; softwarové (FFmpeg) nastoupí jen u zvuku, který zařízení neumí.
        val renderers = DefaultRenderersFactory(this)
            .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_ON)
            .setEnableDecoderFallback(true)
        val exo = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(this, http)))
            .build()
        // zvuk a titulky: naposledy zvolené u tohoto seriálu, jinak čeština
        val remembered = stream.optJSONObject("tracks")
        tracksKey = remembered?.optString("key").orEmpty()
        audioChoice = remembered?.optString("audio").orEmpty()
        subtitleChoice = remembered?.optString("subs").orEmpty()
        userChangedTracks = false
        seenAudio = ""
        seenSubtitles = ""
        val selection = exo.trackSelectionParameters.buildUpon()
        if (audioChoice.isNotEmpty()) selection.setPreferredAudioLanguages(audioChoice, "cs", "sk")
        else selection.setPreferredAudioLanguages("cs", "sk")
        if (subtitleChoice == "off") selection.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
        else if (subtitleChoice.isNotEmpty()) selection.setPreferredTextLanguage(subtitleChoice)
        exo.trackSelectionParameters = selection.build()
        audioButton.visibility = View.GONE
        subtitleButton.visibility = View.GONE
        restartButton.visibility = if (live) View.GONE else View.VISIBLE
        player = exo
        playerView.player = exo
        exo.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (player !== exo) return  // přehrávač, který už byl nahrazen dalším videem
                if (state == Player.STATE_READY) {
                    message.visibility = View.GONE
                    spinner.visibility = View.GONE
                    if (!started) {
                        started = true
                        saveProgress(ended = false, first = true)
                    }
                } else if (state == Player.STATE_ENDED) {
                    saveProgress(ended = true)
                    offerNext()
                }
            }

            override fun onTracksChanged(tracks: Tracks) {
                if (player !== exo) return
                // rychlé volby jen tam, kde je z čeho vybírat
                audioButton.visibility = if (groups(C.TRACK_TYPE_AUDIO).size > 1) View.VISIBLE else View.GONE
                subtitleButton.visibility = if (groups(C.TRACK_TYPE_TEXT).isNotEmpty()) View.VISIBLE else View.GONE
                val (audio, subtitles) = selectedTracks()
                if (userChangedTracks) {
                    userChangedTracks = false
                    rememberTracks(audio, subtitles)
                }
                seenAudio = audio
                seenSubtitles = subtitles
            }

            override fun onTrackSelectionParametersChanged(parameters: TrackSelectionParameters) {
                // změna z nabídky ovládání (ne počáteční nastavení výše): zapamatuje se po přepnutí stop
                if (player === exo && started) userChangedTracks = true
            }

            override fun onPlayerError(error: PlaybackException) {
                // Uvolňovaný přehrávač umí ohlásit chybu ještě po nahrazení (např. když se uvolnění
                // protáhne): nesmí shodit video, které už hraje místo něj.
                if (player !== exo) return
                if (!inspected && track.startsWith("ivys:") && error.errorCode in 6000..6999) {
                    // Chráněný pořad a přehrávač licenci nezískal: logika se podívá do manifestu sama
                    // (zná víc zápisů adresy licence) a zkusí se to ještě jednou.
                    inspected = true
                    params.put("inspect", true)
                    resolve()
                    return
                }
                // Zapamatovaná adresa souboru mohla vypršet: jednou ji zahodit a načíst novou.
                if (!retried && !live && track.isNotEmpty() && !track.startsWith("ivys:")) {
                    retried = true
                    val resumeAt = exo.currentPosition
                    Api.call("link_failed", JSONObject().put("track", track)) {
                        if (isFinishing || isDestroyed) return@call
                        if (resumeAt > 0) saveProgress(ended = false)
                        params.put("fresh", true)
                        if (local) params.put("online", true)  // stažený soubor nejde přehrát: zkusit stream
                        resolve()
                    }
                } else {
                    // verze, která nejde přehrát ani s novou adresou: příště se nenabídne jako první
                    if (!live && track.isNotEmpty()) Api.send("play_failed", JSONObject().put("track", track))
                    fail(describe(error))
                }
            }
        })
        exo.setMediaItem(item.build())
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

    private fun groups(type: Int): List<Tracks.Group> =
        player?.currentTracks?.groups?.filter { it.type == type && it.isSupported }.orEmpty()

    private fun label(group: Tracks.Group): String {
        val format = group.getTrackFormat(0)
        val language = format.language?.takeIf { it != "und" }?.let { code ->
            Locale.forLanguageTag(code).getDisplayLanguage(Locale("cs")).replaceFirstChar { it.uppercase() }
        }
        val channels = when {
            group.type != C.TRACK_TYPE_AUDIO -> null
            format.channelCount >= 6 -> "5.1"
            format.channelCount == 2 -> "stereo"
            else -> null
        }
        return listOfNotNull(language ?: format.label ?: "Neoznačená stopa", format.label.takeIf { language != null },
            channels).joinToString(" · ")
    }

    /** Rychlý výběr zvukové stopy nebo titulků; volba se pamatuje pro celý seriál. */
    private fun chooseTrack(type: Int) {
        val exo = player ?: return
        val available = groups(type)
        val text = type == C.TRACK_TYPE_TEXT
        val names = (if (text) listOf("Vypnuto") else emptyList()) + available.map { label(it) }
        val current = available.indexOfFirst { it.isSelected }.let { if (text) it + 1 else it }
        AlertDialog.Builder(this)
            .setTitle(if (text) "Titulky" else "Zvuková stopa")
            .setSingleChoiceItems(names.toTypedArray(), current) { dialog, which ->
                val builder = exo.trackSelectionParameters.buildUpon()
                if (text && which == 0) {
                    builder.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                } else {
                    val group = available[if (text) which - 1 else which]
                    val tracks = (0 until group.length).filter { group.isTrackSupported(it) }
                    builder.setTrackTypeDisabled(type, false)
                        .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, tracks))
                }
                userChangedTracks = true
                exo.trackSelectionParameters = builder.build()
                dialog.dismiss()
            }
            .show()
    }

    /** Jazyk právě hrající zvukové stopy a titulků (`off` = titulky jsou, ale vypnuté). */
    private fun selectedTracks(): Pair<String, String> {
        val audio = groups(C.TRACK_TYPE_AUDIO).firstOrNull { it.isSelected }?.getTrackFormat(0)?.language
            ?.takeIf { it != "und" }.orEmpty()
        val texts = groups(C.TRACK_TYPE_TEXT)
        val subtitles = when {
            texts.isEmpty() -> ""
            texts.none { it.isSelected } -> "off"
            else -> texts.first { it.isSelected }.getTrackFormat(0).language?.takeIf { it != "und" }.orEmpty()
        }
        return audio to subtitles
    }

    /**
     * Uloží uživatelovu volbu, aby ji další díl seriálu použil také. Pamatuje se jen to, co se
     * opravdu změnilo: kdo přepne zvuk, nemá tím zároveň „zvolené“ vypnuté titulky.
     */
    private fun rememberTracks(audio: String, subtitles: String) {
        if (tracksKey.isEmpty()) return
        var changed = false
        if (audio != seenAudio && audio.isNotEmpty() && audio != audioChoice) {
            audioChoice = audio
            changed = true
        }
        if (subtitles != seenSubtitles && subtitles.isNotEmpty() && subtitles != subtitleChoice) {
            subtitleChoice = subtitles
            changed = true
        }
        if (changed) {
            Api.send("track_prefs", JSONObject().put("key", tracksKey).put("audio", audioChoice).put("subs", subtitleChoice))
        }
    }

    private fun showEndTime() {
        val exo = player
        val total = exo?.duration ?: C.TIME_UNSET
        endTime.text = if (exo == null || live || total == C.TIME_UNSET || total <= 0) "" else {
            val left = (total - exo.currentPosition).coerceAtLeast(0)
            "Konec v " + SimpleDateFormat("H:mm", Locale.getDefault()).format(Date(System.currentTimeMillis() + left))
        }
    }

    /** Dokud je výběr v horní liště, ovládání nezmizí pod rukama. */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (top.hasFocus() && event.action == KeyEvent.ACTION_DOWN) playerView.showController()
        return super.dispatchKeyEvent(event)
    }

    /** Po dokoukání: logika navrhne další díl seriálu nebo další film série; jinak se přehrávač zavře. */
    private fun offerNext() {
        if (live || track.isEmpty() || track.startsWith("ivys:")) {
            finish()
            return
        }
        handler.removeCallbacks(saver)
        playerView.hideController()
        playerView.useController = false
        val ended = track
        Api.call("play_next", JSONObject().put("track", ended).put("choice", params.optString("choice"))) { result ->
            if (isFinishing || isDestroyed || ended != track) return@call
            val next = result.data?.optJSONObject("next")
            val target = next?.let { Target.from(it.optJSONObject("target")) }
            if (next == null || target == null || target.type.isEmpty()) {
                finish()
                return@call
            }
            nextTarget = target
            findViewById<TextView>(R.id.next_label).text = next.optString("title")
            findViewById<TextView>(R.id.next_title).text = next.optString("subtitle")
            val plays = target.type == "play"
            nextPlay.text = if (plays) "Přehrát" else "Otevřít"
            top.visibility = View.GONE
            nextPanel.visibility = View.VISIBLE
            nextPlay.requestFocus()
            countdown = if (plays && next.optBoolean("auto")) NEXT_COUNTDOWN else 0
            if (countdown > 0) handler.post(tick)
        }
    }

    private fun stopCountdown() {
        handler.removeCallbacks(tick)
        if (nextPlay.text.startsWith("Přehrát (")) nextPlay.text = "Přehrát"
        countdown = 0
    }

    private fun startNext() {
        val target = nextTarget ?: return
        handler.removeCallbacks(tick)
        countdown = 0
        nextTarget = null
        nextPanel.visibility = View.GONE
        if (target.type == "play") {
            // další video ve stejném přehrávači
            call = target.call
            params = target.params
            started = false
            retried = false
            inspected = false
            resolve()
        } else {
            Navigator.open(this, target, "")
            finish()
        }
    }

    private fun describe(error: PlaybackException): String {
        val text = when (error.errorCode) {
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT ->
                "Nepodařilo se připojit k serveru s videem. Zkontrolujte připojení k internetu."
            PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
            PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND ->
                "Server video nevydal – odkaz přestal platit, nebo video už není dostupné."
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
            PlaybackException.ERROR_CODE_PARSING_MANIFEST_UNSUPPORTED,
            PlaybackException.ERROR_CODE_PARSING_MANIFEST_MALFORMED ->
                "Tento formát souboru přehrávač nezná."
            PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
            PlaybackException.ERROR_CODE_DECODING_FAILED,
            PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED,
            PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES ->
                "Zařízení tento formát videa nebo zvuku nepřehraje."
            in 6000..6999 ->
                "Chráněný pořad se nepodařilo přehrát: licenci server nevydal, nebo ji zařízení nepřijalo."
            else -> "Přehrávání selhalo."
        }
        // u chráněného pořadu pomůže vidět příčinu a to, co rozhraní vrátilo (jen názvy polí)
        val chain = generateSequence(error.cause) { it.cause }.take(4).toList()
        // nejhlubší příčina říká nejvíc; obalující výjimky jen opakují její text
        val cause = chain.lastOrNull()?.let { "${it.javaClass.simpleName}: ${it.message.orEmpty().take(120)}" }.orEmpty()
        val detail = if (diagnostic.isEmpty()) "" else "\n\nDiagnostika: $cause\n${diagnostic.take(600)}"
        if (chain.any { it.message.orEmpty().contains("No license URL") }) {
            return "Česká televize k tomuto pořadu neposlala adresu licenčního serveru – není v odpovědi " +
                "rozhraní ani v popisu streamu. Bez ní aplikace chráněný pořad přehrát neumí.$detail"
        }
        return "$text\n(${error.errorCodeName})$detail"
    }

    private fun fail(text: String) {
        release()
        playerView.hideController()
        playerView.useController = false
        spinner.visibility = View.GONE
        top.visibility = View.GONE
        // do Diagnostiky: co se nepřehrálo a proč
        Api.send("log_error", JSONObject().put("where", "přehrávač").put("text", "${heading.text}: ${text.replace('\n', ' ')}"))
        message.text = "$text\n\nZpět se vrátíte tlačítkem Zpět."
        message.visibility = View.VISIBLE
    }

    private fun release() {
        handler.removeCallbacks(saver)
        val old = player
        player = null  // nejdřív odpojit: posluchač uvolňovaného přehrávače už nic neřídí
        playerView.player = null
        old?.release()
    }

    override fun onPause() {
        super.onPause()
        stopCountdown()  // na pozadí se nic samo nespouští
        player?.pause()
        saveProgress(ended = false)
    }

    override fun onStop() {
        super.onStop()
        saveProgress(ended = false)
        WatchNext.sync(this, delayMs = 1500)  // až se uloží pozice
        if (isFinishing) release()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        handler.removeCallbacks(clock)
        release()
        super.onDestroy()
    }
}
