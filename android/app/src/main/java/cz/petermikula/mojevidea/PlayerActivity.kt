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
import android.widget.Toast
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
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.common.text.Cue
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView
import androidx.media3.ui.SubtitleView
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
        const val HEALTHY_AFTER_MS = 120_000L    // po takové době hraní se chyba bere jako nová, ne opakovaná
        const val STILL_WATCHING_AFTER = 3       // tolik dílů se spustí samo bez jediného stisku ovladače
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
    private var reportedSize = ""   // rozměry obrazu, které už logika zná
    private var diagnostic = ""
    private var subtitleStyle: JSONObject? = null   // vzhled titulků z profilu a nabídka možností
    private var externalName = ""   // oficiální aplikace, která by chráněný pořad přehrála, ale v zařízení není
    private var started = false
    private var retried = false
    private var visible = false          // obrazovka je vidět (mezi onStart a onStop)
    private var forcedPosition = -1L     // nový pokus po chybě naváže přesně tam, kde přehrávač byl
    private var lastStream: JSONObject? = null
    private var subtitleUrls = emptyList<String>()
    private var withoutSubtitles = false // titulky se nepodařilo načíst: video běží bez nich
    private var unattended = 0           // kolik dílů po sobě se spustilo samo, aniž kdo sáhl na ovladač

    /** Přehrávání chvíli běží bez potíží: další výpadek má zase nárok na nový pokus. */
    private val healthy = Runnable { retried = false }
    private var choice = ""          // titul z Objevuj, ke kterému video patří (pro návrh dalšího dílu)
    private var inspected = false   // u chráněného pořadu už proběhl pokus najít adresu licence v manifestu
    private lateinit var top: View
    private lateinit var endTime: TextView
    private lateinit var audioButton: ImageButton
    private lateinit var subtitleButton: ImageButton
    private lateinit var restartButton: ImageButton
    private var tracksKey = ""       // pod čím se pamatuje zvuk a titulky (seriál, film)
    private var audioChoice = ""
    private var audioMode = "auto"        // výstup zvuku: auto (prostorový zvuk se předává) | stereo (převádí aplikace)
    private var appliedAudioMode = ""     // s jakým výstupem je sestavený přehrávač, který právě hraje
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
                unattended++
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
        val tint = ContextCompat.getColorStateList(this, R.color.player_button_text)
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
        // Zvuk a titulky se volí tlačítky v horní liště; vestavěná tlačítka přehrávače dole (titulky
        // a nabídka se zvukovou stopou) by byla totéž podruhé. Titulky vypíná rozvržení, nabídku tady.
        playerView.findViewById<View>(androidx.media3.ui.R.id.exo_settings)?.visibility = View.GONE
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
        Api.call("subtitle_style") { result ->
            if (isFinishing || isDestroyed) return@call
            result.data?.let { style ->
                applySubtitleStyle(style)
                audioMode = style.optString("audio_output").ifEmpty { "auto" }
                // přehrávač už mezitím hraje s jiným výstupem zvuku: sestavit ho znovu na stejném místě
                if (player != null && audioMode != appliedAudioMode) rebuild()
            }
        }
        resolve()
    }

    private fun resolve() {
        message.text = "Připravuji přehrávání…"
        message.visibility = View.VISIBLE
        spinner.visibility = View.VISIBLE
        Api.call(call, params) { result ->
            if (isFinishing || isDestroyed) return@call
            if (!visible) {
                // uživatel mezitím odešel (tlačítko Domů): nic se nespouští, jinak by video hrálo na pozadí
                finish()
                return@call
            }
            val data = result.data
            if (data == null) {
                fail(result.error ?: "Video se nepodařilo spustit.")
            } else if (!handOver(data)) {
                play(data)
            }
        }
    }

    /**
     * Chráněný pořad, který aplikace nepřehraje (`external` v odpovědi logiky): otevře se rovnou
     * v oficiální aplikaci, je-li v zařízení. Vrací true, když se předání povedlo.
     */
    private fun handOver(stream: JSONObject): Boolean {
        val external = stream.optJSONObject("external") ?: return false
        val name = external.optString("name")
        when (OfficialApp.open(this, external)) {
            OfficialApp.Result.OPENED_TITLE ->
                Toast.makeText(this, "Chráněný pořad – otevírám v aplikaci $name.", Toast.LENGTH_LONG).show()
            OfficialApp.Result.OPENED_SEARCH ->
                Toast.makeText(this, "Chráněný pořad – hledám ho v aplikaci $name: ${external.optString("query")}",
                    Toast.LENGTH_LONG).show()
            OfficialApp.Result.OPENED_APP ->
                Toast.makeText(this, "Chráněný pořad – v aplikaci $name vyhledejte: ${external.optString("title")}",
                    Toast.LENGTH_LONG).show()
            OfficialApp.Result.MISSING -> {
                // bez oficiální aplikace se přehrání zkusí zde; případná chyba poradí, co nainstalovat
                externalName = name
                return false
            }
        }
        finish()
        return true
    }

    override fun onStart() {
        super.onStart()
        visible = true
    }

    /** Rozkoukané video: zeptat se, jestli navázat, nebo začít znovu. */
    private fun play(stream: JSONObject) {
        val forced = forcedPosition
        forcedPosition = -1
        if (forced >= 0) {
            begin(stream, forced)  // nový pokus po chybě: pozici určuje přehrávač, ne uložený stav
            return
        }
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
            .setAdapter(Dialogs.items(this, listOf("Pokračovat od ${clockText(position)}", "Přehrát od začátku"))) { _, which ->
                chosen = true
                begin(stream, if (which == 0) position else 0)
            }
            .setOnDismissListener { if (!chosen && !isFinishing) finish() }  // Zpět: nic se nepřehraje
            .showStyled()
    }

    private fun clockText(ms: Long): String {
        val seconds = ms / 1000
        return if (seconds >= 3600) "%d:%02d:%02d".format(seconds / 3600, seconds / 60 % 60, seconds % 60)
        else "%d:%02d".format(seconds / 60, seconds % 60)
    }

    private fun begin(stream: JSONObject, position: Long) {
        lastStream = stream
        track = stream.optString("track")
        choice = stream.optString("choice").ifEmpty { params.optString("choice") }
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
        val languages = stream.optJSONArray("subtitle_langs")  // jazyk každých titulků, pokud ho logika zná
        val urls = ArrayList<String>()
        for (index in 0 until (list?.length() ?: 0)) {
            val url = list!!.optString(index)
            if (url.isEmpty()) continue
            urls.add(url)
            val mime = if (url.substringBefore('?').endsWith(".srt", true)) MimeTypes.APPLICATION_SUBRIP else MimeTypes.TEXT_VTT
            subtitles.add(
                MediaItem.SubtitleConfiguration.Builder(source(url))
                    .setMimeType(mime)
                    .setLanguage(languages?.optString(index).orEmpty().ifEmpty { "cs" })
                    .setSelectionFlags(if (index == 0) C.SELECTION_FLAG_DEFAULT else 0)
                    .build()
            )
        }
        subtitleUrls = urls
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
        // Výstup „Stereo“: zvuk vždy dekóduje aplikace a prostorový převede na dva kanály (viz StereoAudio.kt).
        appliedAudioMode = audioMode
        val renderers = (if (audioMode == "stereo") StereoRenderersFactory(this) else DefaultRenderersFactory(this))
            .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_ON)
            .setEnableDecoderFallback(true)
        val exo = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(this, http)))
            // zvuk si přehrávač hlídá se systémem: když začne hrát jiná aplikace, video se pozastaví
            .setAudioAttributes(
                androidx.media3.common.AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(),
                true,
            )
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
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (player !== exo) return
                keepAwake(exo)
                handler.removeCallbacks(healthy)
                if (isPlaying) handler.postDelayed(healthy, HEALTHY_AFTER_MS)
            }

            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (player === exo) keepAwake(exo)
            }

            override fun onVideoSizeChanged(videoSize: androidx.media3.common.VideoSize) {
                // skutečné rozměry obrazu: stránka titulu podle nich příště ukáže, v jaké kvalitě soubor je
                if (player !== exo || live || videoSize.width <= 0 || videoSize.height <= 0) return
                val size = "$track ${videoSize.width}x${videoSize.height}"
                if (track.isEmpty() || size == reportedSize) return
                reportedSize = size
                Api.call("video_size", JSONObject().put("track", track).put("width", videoSize.width)
                    .put("height", videoSize.height).put("local", local)) { }
            }

            override fun onPlaybackStateChanged(state: Int) {
                if (player !== exo) return  // přehrávač, který už byl nahrazen dalším videem
                keepAwake(exo)
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
                // zvuk: i u jediné stopy, kvůli volbě výstupu (prostorový zvuk / stereo)
                audioButton.visibility = if (groups(C.TRACK_TYPE_AUDIO).isNotEmpty()) View.VISIBLE else View.GONE
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
                val resumeAt = exo.currentPosition.coerceAtLeast(0)
                // Nenačetly se titulky (zvláštní soubor vedle videa): kvůli nim video nepadne, pustí se bez nich.
                val broken = generateSequence<Throwable>(error) { it.cause }.take(6)
                    .filterIsInstance<HttpDataSource.HttpDataSourceException>().firstOrNull()?.dataSpec?.uri?.toString()
                val stream = lastStream
                if (!withoutSubtitles && stream != null && broken != null && broken in subtitleUrls) {
                    withoutSubtitles = true
                    android.widget.Toast.makeText(this@PlayerActivity, "Titulky se nepodařilo načíst, pouštím bez nich.",
                        android.widget.Toast.LENGTH_LONG).show()
                    val plain = JSONObject(stream.toString()).put("subtitles", org.json.JSONArray())
                    handler.post { if (!isFinishing && !isDestroyed && visible) begin(plain, resumeAt) }
                    return
                }
                if (!inspected && track.startsWith("ivys:") && error.errorCode in 6000..6999) {
                    // Chráněný pořad a přehrávač licenci nezískal: logika se podívá do manifestu sama
                    // (zná víc zápisů adresy licence) a zkusí se to ještě jednou.
                    inspected = true
                    params.put("inspect", true)
                    forcedPosition = resumeAt
                    resolve()
                    return
                }
                if (live && error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
                    // živé vysílání po dlouhé pauze: vrátit se na živý okraj a pokračovat
                    exo.seekToDefaultPosition()
                    exo.prepare()
                    return
                }
                if (!retried && (live || track.startsWith("ivys:")) && error.errorCode !in 6000..6999) {
                    // iVysílání a živé vysílání: adresa streamu po pauze vyprší a krátký výpadek sítě
                    // přehrávání ukončí – jednou si říct o novou adresu a navázat
                    retried = true
                    if (!live && resumeAt > 0) saveProgress(ended = false)
                    forcedPosition = if (live) -1 else resumeAt
                    resolve()
                    return
                }
                // Zapamatovaná adresa souboru mohla vypršet: jednou ji zahodit a načíst novou.
                if (!retried && !live && track.isNotEmpty() && !track.startsWith("ivys:")) {
                    retried = true
                    Api.call("link_failed", JSONObject().put("track", track)) {
                        if (isFinishing || isDestroyed) return@call
                        if (resumeAt > 0) saveProgress(ended = false)
                        params.put("fresh", true)
                        params.remove("restart")  // „od začátku“ platilo pro první spuštění, ne pro nový pokus
                        if (local) params.put("online", true)  // stažený soubor nejde přehrát: zkusit stream
                        // navázat tam, kde přehrávač byl (uložený stav může být jinde, nebo žádný)
                        forcedPosition = resumeAt
                        resolve()
                    }
                } else {
                    // Verze, která nejde přehrát ani s novou adresou: příště se nenabídne jako první.
                    // Výpadek připojení za to nemůže – ten o souboru nic neříká.
                    val network = error.errorCode == PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED ||
                        error.errorCode == PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT
                    if (!live && track.isNotEmpty() && !network) Api.send("play_failed", JSONObject().put("track", track))
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

    /** Obrazovka zůstává rozsvícená, jen když video opravdu hraje nebo se načítá – ne při pauze či chybě. */
    private fun keepAwake(exo: ExoPlayer) {
        playerView.keepScreenOn = exo.playWhenReady &&
            (exo.playbackState == Player.STATE_READY || exo.playbackState == Player.STATE_BUFFERING)
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
            .setTitle(if (text) "Titulky" else if (audioMode == "stereo") "Zvuková stopa · výstup: stereo"
                      else "Zvuková stopa · výstup: prostorový zvuk")
            .apply {
                if (text) setNeutralButton("Velikost a pozadí") { _, _ -> chooseSubtitleStyle() }
                else setNeutralButton(if (audioMode == "stereo") "Vrátit prostorový zvuk" else "Přepnout na stereo") { _, _ ->
                    switchAudioOutput()
                }
            }
            .setSingleChoiceItems(Dialogs.choices(this, names), current) { dialog, which ->
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
            .showStyled()
    }

    /**
     * Přepne výstup zvuku mezi prostorovým (předá se televizi nebo soundbaru) a stereem (převede ho
     * aplikace) a přehrávání naváže na stejném místě. Volba se pamatuje pro všechna videa.
     */
    private fun switchAudioOutput() {
        audioMode = if (audioMode == "stereo") "auto" else "stereo"
        Api.send("audio_output_set", JSONObject().put("mode", audioMode))
        Toast.makeText(this, if (audioMode == "stereo") "Zvuk: stereo – prostorový zvuk převádí aplikace"
                             else "Zvuk: prostorový – předává se televizi", Toast.LENGTH_LONG).show()
        rebuild()
    }

    /** Sestaví přehrávač znovu (jiný výstup zvuku) a pokračuje tam, kde se hrálo. */
    private fun rebuild() {
        val stream = lastStream ?: return
        val position = player?.currentPosition ?: 0L
        begin(stream, if (live) 0L else position)
    }

    /** Vzhled titulků podle profilu: velikost jako násobek výchozí, pozadí černé, poloprůhledné, nebo jen obrys. */
    private fun applySubtitleStyle(style: JSONObject) {
        subtitleStyle = style
        val view = playerView.subtitleView ?: return
        val scale = style.optDouble("scale", 1.0).toFloat()
        view.setApplyEmbeddedFontSizes(false)   // velikost určuje uživatel, ne soubor s titulky
        view.setFractionalTextSize(SubtitleView.DEFAULT_TEXT_SIZE_FRACTION * scale)
        val white = android.graphics.Color.WHITE
        val black = android.graphics.Color.BLACK
        val clear = android.graphics.Color.TRANSPARENT
        view.setStyle(when (style.optString("background")) {
            "outline" -> CaptionStyleCompat(white, clear, clear, CaptionStyleCompat.EDGE_TYPE_OUTLINE, black, null)
            "shade" -> CaptionStyleCompat(white, 0x99000000.toInt(), clear, CaptionStyleCompat.EDGE_TYPE_NONE, black, null)
            else -> CaptionStyleCompat(white, black, clear, CaptionStyleCompat.EDGE_TYPE_NONE, black, null)
        })
    }

    /**
     * Velikost a pozadí titulků s okamžitým náhledem: každé stisknutí přepne na další možnost a hned
     * ji ukáže na titulcích pod oknem (když zrovna žádné nejsou, na ukázkové větě). Volba se ukládá k profilu.
     */
    private fun chooseSubtitleStyle() {
        val style = subtitleStyle ?: return
        val view = playerView.subtitleView
        fun options(name: String) = style.optJSONArray(name)?.let { list ->
            (0 until list.length()).mapNotNull { list.optJSONObject(it) }
        }.orEmpty()
        val sizes = options("sizes")
        val backgrounds = options("backgrounds")
        if (sizes.isEmpty() || backgrounds.isEmpty()) return
        var size = sizes.indexOfFirst { it.optString("id") == style.optString("size") }.coerceAtLeast(0)
        var background = backgrounds.indexOfFirst { it.optString("id") == style.optString("background") }.coerceAtLeast(0)
        val padding = (20 * resources.displayMetrics.density).toInt()
        // tlačítka ve vzhledu aplikace: na televizi musí být poznat, které je vybrané
        val sizeButton = Button(this, null, 0, R.style.TitleButton)
        val backgroundButton = Button(this, null, 0, R.style.TitleButton)
        fun show() {
            sizeButton.text = "Velikost: ${sizes[size].optString("title")}"
            backgroundButton.text = "Pozadí: ${backgrounds[background].optString("title")}"
            val current = JSONObject(style.toString()).put("size", sizes[size].optString("id"))
                .put("scale", sizes[size].optDouble("scale", 1.0)).put("background", backgrounds[background].optString("id"))
            applySubtitleStyle(current)
            if (player?.currentCues?.cues.isNullOrEmpty()) {
                view?.setCues(listOf(Cue.Builder().setText("Ukázka titulků: takhle budou vypadat.").build()))
            }
        }
        fun save() = Api.send("subtitle_style_set", JSONObject().put("size", sizes[size].optString("id"))
            .put("background", backgrounds[background].optString("id")))
        sizeButton.setOnClickListener { size = (size + 1) % sizes.size; show(); save() }
        backgroundButton.setOnClickListener { background = (background + 1) % backgrounds.size; show(); save() }
        val content = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(padding, padding / 2, padding, 0)
            val spaced = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { bottomMargin = padding / 2 }
            addView(sizeButton, spaced)
            addView(backgroundButton, spaced)
        }
        show()
        val dialog = AlertDialog.Builder(this)
            .setTitle("Vzhled titulků")
            .setView(content)
            .setPositiveButton("Hotovo", null)
            .setOnDismissListener { view?.setCues(player?.currentCues?.cues ?: emptyList()) }
            .create()
        // okno nahoře a bez ztmavení obrazu, aby na titulky dole bylo dobře vidět
        dialog.window?.setGravity(android.view.Gravity.TOP)
        dialog.window?.setDimAmount(0f)
        dialog.show()
        Dialogs.polish(dialog)
        sizeButton.requestFocus()
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
        if (event.action == KeyEvent.ACTION_DOWN) unattended = 0  // někdo se dívá
        if (top.hasFocus() && event.action == KeyEvent.ACTION_DOWN) playerView.showController()
        return super.dispatchKeyEvent(event)
    }

    /** Po dokoukání: logika navrhne další díl seriálu nebo další film série; jinak se přehrávač zavře. */
    private fun offerNext() {
        if (live || track.isEmpty()) {
            finish()
            return
        }
        handler.removeCallbacks(saver)
        playerView.hideController()
        playerView.useController = false
        val ended = track
        Api.call("play_next", JSONObject().put("track", ended).put("choice", choice)) { result ->
            if (isFinishing || isDestroyed || ended != track) return@call
            if (!visible) {
                finish()  // uživatel mezitím odešel: další díl se na pozadí nenabízí ani nespouští
                return@call
            }
            val next = result.data?.optJSONObject("next")
            val target = next?.let { Target.from(it.optJSONObject("target")) }
            if (next == null || target == null || target.type.isEmpty()) {
                finish()
                return@call
            }
            nextTarget = target
            val plays = target.type == "play"
            // Několik dílů se spustilo samo a nikdo nesáhl na ovladač: další se už sám nepustí, aby seriál
            // nedohrál do konce při vypnuté televizi (a díly se neoznačily jako zhlédnuté).
            val asks = plays && next.optBoolean("auto") && unattended >= STILL_WATCHING_AFTER
            findViewById<TextView>(R.id.next_label).text =
                if (asks) "Ještě se díváte? Další díl se sám nespustí." else next.optString("title")
            findViewById<TextView>(R.id.next_title).text = next.optString("subtitle")
            nextPlay.text = if (plays) "Přehrát" else "Otevřít"
            top.visibility = View.GONE
            nextPanel.visibility = View.VISIBLE
            nextPlay.requestFocus()
            countdown = if (plays && next.optBoolean("auto") && !asks) NEXT_COUNTDOWN else 0
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
            withoutSubtitles = false
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
        // chráněný pořad by přehrála oficiální aplikace: poradit, ať si ji uživatel nainstaluje
        val advice = if (externalName.isEmpty()) "" else
            "\n\nNainstalujte si z Google Play aplikaci $externalName – chráněné pořady se pak otevřou rovnou v ní."
        if (chain.any { it.message.orEmpty().contains("No license URL") }) {
            return "Česká televize k tomuto pořadu neposlala adresu licenčního serveru – není v odpovědi " +
                "rozhraní ani v popisu streamu. Bez ní aplikace chráněný pořad přehrát neumí.$advice$detail"
        }
        return "$text\n(${error.errorCodeName})$advice$detail"
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
        handler.removeCallbacks(healthy)
        playerView.keepScreenOn = false
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
        visible = false
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
