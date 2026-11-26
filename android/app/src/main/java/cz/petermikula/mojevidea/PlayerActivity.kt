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
        const val OTHER_VERSIONS = 2             // tolikrát nejvýš se po chybě zkusí jiná verze téhož titulu
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
    // verze titulu z Objevuj, které se nepodařilo přehrát: přehrávač pak sám zkusí jinou (viz play_other)
    private val failedVersions = ArrayList<String>()
    private var failedChoice = ""
    private var visible = false          // obrazovka je vidět (mezi onStart a onStop)
    private var forcedPosition = -1L     // nový pokus po chybě naváže přesně tam, kde přehrávač byl
    private var lastStream: JSONObject? = null
    private var subtitleUrls = emptyList<String>()
    private var withoutSubtitles = false // titulky se nepodařilo načíst: video běží bez nich
    private var subtitleSearch = false   // jde hledat titulky na OpenSubtitles (vyplněný klíč, video z Přehraj.to)
    private var subtitleOffer = false    // video z Přehraj.to: tlačítko Titulky je vždy (i bez vlastních titulků)
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
    // výstup zvuku: auto (prostorový zvuk se předává) | pcm, pcm_soft (dekóduje aplikace, 5.1 zůstává) | stereo
    private var audioMode = "auto"
    private var nightMode = false          // noční režim zvuku (viz NightAudioProcessor)
    private var appliedNight = false
    // posun zvuku vůči obrazu (ms; kladné = zvuk zní později): pro každý výstup zvlášť, platí hned
    private var audioDelays = JSONObject()
    @Volatile private var audioDelayMs = 0
    private var appliedAudioMode = ""     // s jakým výstupem je sestavený přehrávač, který právě hraje
    // Co si nově sestavený přehrávač (jiný výstup zvuku) převezme od dosavadního: zvolenou stopu
    // a titulky, a jestli se hrálo nebo stálo.
    private var carriedTracks: androidx.media3.common.TrackSelectionParameters? = null
    private var carriedPlaying: Boolean? = null
    private var carriedChanged = false
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
                nightMode = style.optBoolean("audio_night")
                audioDelays = style.optJSONObject("audio_delays") ?: JSONObject()
                audioDelayMs = audioDelays.optInt(audioMode)
                // přehrávač už mezitím hraje s jiným výstupem zvuku: sestavit ho znovu na stejném místě
                if (player != null && (audioMode != appliedAudioMode || nightMode != appliedNight)) rebuild()
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
        // časovače zastavené při odchodu na pozadí (viz onStop) zase běží
        if (player != null && !live) {
            handler.removeCallbacks(saver)
            handler.postDelayed(saver, SAVE_EVERY_MS)
        }
        if (playerView.isControllerFullyVisible) {
            handler.removeCallbacks(clock)
            handler.post(clock)
        }
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
        // OpenSubtitles se nabízí u každého videa z Přehraj.to; bez klíče v Nastavení volba řekne, co vyplnit
        subtitleOffer = !stream.optBoolean("live") && track.isNotEmpty() && !track.startsWith("ivys:")
        subtitleSearch = stream.optBoolean("subtitle_search") && subtitleOffer
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
                // titulky stažené z OpenSubtitles jsou soubor v zařízení (cesta), ostatní adresa
                MediaItem.SubtitleConfiguration.Builder(if (url.startsWith("/")) Uri.fromFile(File(url)) else source(url))
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
        // Ostatní výstupy zvuku: dekóduje vždy aplikace a ven jde PCM – 5.1 beze změny, s tišším basovým
        // kanálem, nebo převedené na dva kanály (viz StereoAudio.kt).
        appliedAudioMode = audioMode
        appliedNight = nightMode
        val renderers = AudioRenderersFactory(this, audioMode, nightMode) { audioDelayMs * 1000L }
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
        exo.trackSelectionParameters = carriedTracks ?: selection.build()
        if (carriedTracks != null) userChangedTracks = carriedChanged
        carriedTracks = null
        audioButton.visibility = View.GONE
        subtitleButton.visibility = if (subtitleOffer) View.VISIBLE else View.GONE
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
                subtitleButton.visibility =
                    if (groups(C.TRACK_TYPE_TEXT).isNotEmpty() || subtitleOffer) View.VISIBLE else View.GONE
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
                    if (failedChoice != choice) {
                        failedVersions.clear()
                        failedChoice = choice
                    }
                    // Titul z Objevuj mívá na Přehraj.to víc verzí: místo chyby se zkusí další nejlepší
                    // (nejvýš dvakrát). Logika si tu nepovedenou sama poznačí.
                    if (!live && !network && choice.isNotEmpty() && track.isNotEmpty() && failedVersions.size < OTHER_VERSIONS) {
                        val reason = describe(error)
                        val request = JSONObject().put("track", track).put("choice", choice)
                            .put("tried", org.json.JSONArray(failedVersions))
                        failedVersions.add(track)
                        if (resumeAt > 0) saveProgress(ended = false)
                        release()
                        message.text = "Tuto verzi se nepodařilo přehrát, hledám jinou…"
                        message.visibility = View.VISIBLE
                        spinner.visibility = View.VISIBLE
                        Api.call("play_other", request) { result ->
                            if (isFinishing || isDestroyed) return@call
                            val other = result.data
                            if (other == null || !visible) {
                                fail(reason)   // jiná verze není (nebo už se nikdo nedívá): původní chyba
                                return@call
                            }
                            android.widget.Toast.makeText(this@PlayerActivity,
                                "Verzi se nepodařilo přehrát – pouštím jinou: ${other.optString("switched")}",
                                android.widget.Toast.LENGTH_LONG).show()
                            // další pokusy (nová adresa po chybě) patří téhle verzi, ne té původní
                            other.optJSONObject("again")?.let { again ->
                                call = again.optString("call").ifEmpty { call }
                                params = again.optJSONObject("params") ?: params
                            }
                            retried = false
                            forcedPosition = resumeAt
                            if (!handOver(other)) play(other)
                        }
                        return
                    }
                    if (!live && track.isNotEmpty() && !network) Api.send("play_failed", JSONObject().put("track", track))
                    fail(describe(error))
                }
            }
        })
        exo.setMediaItem(item.build())
        if (position > 0 && !live) exo.seekTo(position)
        // obrazovka, která není vidět (uživatel mezitím odešel), nezačne hrát sama na pozadí
        exo.playWhenReady = (carriedPlaying ?: true) && visible
        carriedPlaying = null
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
        if (player == null) return
        val available = groups(type)
        val text = type == C.TRACK_TYPE_TEXT
        if (!text) {
            chooseAudio(available)
            return
        }
        val search = if (subtitleOffer) listOf("Najít titulky (OpenSubtitles)…") else emptyList()
        val names = listOf("Vypnuto") + available.map { label(it) } + search
        val current = available.indexOfFirst { it.isSelected } + 1
        AlertDialog.Builder(this)
            .setTitle("Titulky")
            .setNeutralButton("Velikost a pozadí") { _, _ -> chooseSubtitleStyle() }
            .setSingleChoiceItems(Dialogs.choices(this, names), current) { dialog, which ->
                dialog.dismiss()
                if (search.isNotEmpty() && which == names.size - 1) {
                    if (subtitleSearch) findSubtitles() else explainSubtitleSearch()
                    return@setSingleChoiceItems
                }
                // přehrávač se mezitím mohl vyměnit (další díl, nový pokus): volba patří tomu, který hraje
                val playing = player ?: return@setSingleChoiceItems
                val builder = playing.trackSelectionParameters.buildUpon()
                if (text && which == 0) {
                    builder.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                } else {
                    val group = available[if (text) which - 1 else which]
                    val tracks = (0 until group.length).filter { group.isTrackSupported(it) }
                    builder.setTrackTypeDisabled(type, false)
                        .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, tracks))
                }
                userChangedTracks = true
                playing.trackSelectionParameters = builder.build()
            }
            .showStyled()
    }

    /** Hledání na OpenSubtitles bez klíče: co a kde vyplnit (rovnou s cestou do Nastavení). */
    private fun explainSubtitleSearch() {
        AlertDialog.Builder(this)
            .setTitle("Titulky z OpenSubtitles")
            .setMessage("Hledání titulků potřebuje klíč k rozhraní OpenSubtitles. Je zdarma: na webu " +
                "opensubtitles.com se zaregistrujte a v profilu (API Consumers) si vytvořte klíč. Pak ho " +
                "vyplňte v Nastavení v položce „Klíč OpenSubtitles“, případně i jméno a heslo (víc stažení denně).")
            .setPositiveButton("Otevřít Nastavení") { _, _ ->
                startActivity(android.content.Intent(this, SettingsActivity::class.java))
            }
            .setNegativeButton("Zavřít", null)
            .showStyled()
    }

    /** Titulky z OpenSubtitles: nabídka nalezených českých a slovenských, zvolené se stáhnou a hned zapnou. */
    private fun findSubtitles() {
        val searched = track
        android.widget.Toast.makeText(this, "Hledám titulky na OpenSubtitles…", android.widget.Toast.LENGTH_SHORT).show()
        Api.call("subtitles_search", JSONObject().put("track", searched)) { result ->
            if (isFinishing || isDestroyed || track != searched) return@call
            val data = result.data
            val items = data?.optJSONArray("items")
            if (data == null || items == null || items.length() == 0) {
                val text = data?.optString("empty")?.ifEmpty { null } ?: result.error?.ifEmpty { null } ?: "Nic se nenašlo."
                android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_LONG).show()
                return@call
            }
            val list = (0 until items.length()).map { items.getJSONObject(it) }
            AlertDialog.Builder(this)
                .setTitle(data.optString("title"))
                .setAdapter(Dialogs.items(this, list.map { it.optString("title") + "\n" + it.optString("subtitle") })) { _, which ->
                    val item = list[which]
                    val request = JSONObject().put("track", searched).put("file_id", item.optInt("file_id"))
                        .put("lang", item.optString("lang"))
                    Api.call("subtitles_download", request) { got ->
                        if (isFinishing || isDestroyed || track != searched) return@call
                        val file = got.data
                        if (file == null) {
                            android.widget.Toast.makeText(this, got.error ?: "Titulky se nepodařilo stáhnout.", android.widget.Toast.LENGTH_LONG).show()
                            return@call
                        }
                        android.widget.Toast.makeText(this, file.optString("toast"), android.widget.Toast.LENGTH_LONG).show()
                        addSubtitle(file.optString("path"), file.optString("lang").ifEmpty { "cs" })
                    }
                }
                .showStyled()
        }
    }

    /** Stažené titulky přidá k videu a pustí je od místa, kde se právě hraje (přehrávač se sestaví znovu). */
    private fun addSubtitle(path: String, lang: String) {
        val stream = lastStream ?: return
        val exo = player ?: return
        if (path.isEmpty()) return
        val position = exo.currentPosition.coerceAtLeast(0)
        val copy = JSONObject(stream.toString())
        val subtitles = org.json.JSONArray().put(path)
        val languages = org.json.JSONArray().put(lang)
        val oldSubtitles = stream.optJSONArray("subtitles")
        val oldLanguages = stream.optJSONArray("subtitle_langs")
        for (index in 0 until (oldSubtitles?.length() ?: 0)) {
            val url = oldSubtitles!!.optString(index)
            if (url.isEmpty() || url == path) continue
            subtitles.put(url)
            languages.put(oldLanguages?.optString(index).orEmpty())
        }
        copy.put("subtitles", subtitles).put("subtitle_langs", languages)
        // nové titulky jsou první a výchozí; jejich jazyk se zapamatuje k titulu
        copy.put("tracks", (copy.optJSONObject("tracks") ?: JSONObject()).put("subs", lang))
        if (tracksKey.isNotEmpty()) {
            Api.send("track_prefs", JSONObject().put("key", tracksKey).put("audio", audioChoice).put("subs", lang))
        }
        carriedPlaying = exo.playWhenReady
        withoutSubtitles = false
        begin(copy, position)
    }

    /**
     * Zvuk: jazykové stopy a pod nimi řádky Výstup zvuku (co se stane s prostorovým zvukem), Posun
     * zvuku a Noční režim. Dřív byly jen tlačítkem pod seznamem, které se na televizi snadno přehlédne.
     */
    private fun chooseAudio(available: List<Tracks.Group>) {
        val current = available.indexOfFirst { it.isSelected }
        val names = available.mapIndexed { index, group -> (if (index == current) "✓  " else "     ") + label(group) } +
            listOf("Výstup zvuku: " + audioOutputTitle(audioMode).substringBefore(" –") + "  ›",
                "Posun zvuku: " + delayText(audioDelayMs) + "  ›",
                "Noční režim: " + if (nightMode) "zapnutý" else "vypnutý")
        AlertDialog.Builder(this)
            .setTitle("Zvuk")
            .setAdapter(Dialogs.items(this, names)) { _, which ->
                when (which) {
                    available.size -> chooseAudioOutput()
                    available.size + 1 -> chooseAudioDelay()
                    available.size + 2 -> toggleNight()
                    else -> {
                        val playing = player ?: return@setAdapter
                        val group = available[which]
                        val tracks = (0 until group.length).filter { group.isTrackSupported(it) }
                        userChangedTracks = true
                        playing.trackSelectionParameters = playing.trackSelectionParameters.buildUpon()
                            .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, false)
                            .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, tracks))
                            .build()
                    }
                }
            }
            .showStyled()
    }

    /**
     * Noční režim zapnout nebo vypnout: přehrávač se sestaví znovu a naváže na stejném místě. Zvuk
     * zpracovává aplikace, a tak se i u výstupu „předat soundbaru“ dekóduje v aplikaci (5.1 zůstává).
     */
    private fun toggleNight() {
        nightMode = !nightMode
        Api.send("audio_night_set", JSONObject().put("on", nightMode))
        Toast.makeText(this, if (nightMode) "Noční režim zapnutý: hlasité scény tišeji, řeč výrazněji"
            else "Noční režim vypnutý", Toast.LENGTH_LONG).show()
        rebuild()
    }

    /** Možnosti výstupu zvuku (název a krátké vysvětlení) tak, jak je dodala logika. */
    private fun audioOutputs(): List<JSONObject> = subtitleStyle?.optJSONArray("audio_outputs")?.let { list ->
        (0 until list.length()).mapNotNull { list.optJSONObject(it) }
    }.orEmpty()

    private fun audioOutputTitle(mode: String): String =
        audioOutputs().firstOrNull { it.optString("id") == mode }?.optString("title").orEmpty()
            .ifEmpty { if (mode == "stereo") "Stereo" else "Prostorový" }

    /**
     * Výstup zvuku: co se s prostorovým zvukem (5.1) stane, než odejde do televize a soundbaru –
     * předat zakódovaný, dekódovat v aplikaci (i s tišším basovým kanálem), nebo převést na stereo.
     * Přehrávání naváže na stejném místě a volba se pamatuje pro všechna videa.
     */
    private fun chooseAudioOutput() {
        val options = audioOutputs()
        if (options.isEmpty()) return
        val names = options.map { option ->
            // název a pod ním menším písmem, k čemu volba je
            val title = option.optString("title")
            val note = option.optString("note")
            android.text.SpannableString(if (note.isEmpty()) title else "$title\n$note").apply {
                if (note.isNotEmpty()) {
                    setSpan(android.text.style.RelativeSizeSpan(0.72f), title.length + 1, length, 0)
                }
            }
        }
        val adapter = android.widget.ArrayAdapter<CharSequence>(this, R.layout.dialog_choice, android.R.id.text1, names)
        AlertDialog.Builder(this)
            .setTitle("Výstup zvuku")
            .setSingleChoiceItems(adapter, options.indexOfFirst { it.optString("id") == audioMode }) { dialog, which ->
                dialog.dismiss()
                val mode = options[which].optString("id")
                if (mode.isEmpty() || mode == audioMode) return@setSingleChoiceItems
                audioMode = mode
                audioDelayMs = audioDelays.optInt(mode)
                Api.send("audio_output_set", JSONObject().put("mode", mode))
                Toast.makeText(this, "Zvuk: " + options[which].optString("title"), Toast.LENGTH_LONG).show()
                rebuild()
            }
            .setNeutralButton("Posun zvuku: " + delayText(audioDelayMs)) { _, _ -> chooseAudioDelay() }
            .showStyled()
    }

    private fun delayText(ms: Int): String = if (ms == 0) "žádný" else (if (ms > 0) "+" else "−") + "${kotlin.math.abs(ms)} ms"

    /**
     * Posun zvuku vůči obrazu: soundbar zvuk zpracovává, a tak bývá slyšet později, než se hýbou ústa.
     * Každé stisknutí posune o krok a změna platí hned, takže se dá ladit při řeči na obraze. Ukládá
     * se pro zvolený výstup zvuku (předaný a dekódovaný zvuk zpožďuje soundbar různě).
     */
    private fun chooseAudioDelay() {
        val limits = subtitleStyle?.optJSONObject("audio_delay")
        val step = limits?.optInt("step", 25) ?: 25
        val lowest = limits?.optInt("min", -300) ?: -300
        val highest = limits?.optInt("max", 600) ?: 600
        val padding = (20 * resources.displayMetrics.density).toInt()
        val value = android.widget.TextView(this).apply {
            textSize = 18f
            setTextColor(androidx.core.content.ContextCompat.getColor(this@PlayerActivity, R.color.text_primary))
            setPadding(0, 0, 0, padding / 2)
        }
        val later = Button(this, null, 0, R.style.TitleButton).apply { text = "Zvuk je slyšet pozdě  →  +$step ms" }
        val sooner = Button(this, null, 0, R.style.TitleButton).apply { text = "Zvuk je slyšet brzy  →  −$step ms" }
        val none = Button(this, null, 0, R.style.TitleButton).apply { text = "Bez posunu" }
        fun show() {
            value.text = "Posun: ${delayText(audioDelayMs)}" + when {
                audioDelayMs > 0 -> " – obraz čeká na zvuk"
                audioDelayMs < 0 -> " – obraz jde napřed"
                else -> ""
            }
        }
        fun set(ms: Int) {
            audioDelayMs = ms.coerceIn(lowest, highest)   // přehrávač si hodnotu čte průběžně: platí hned
            audioDelays.put(audioMode, audioDelayMs)
            Api.send("audio_delay_set", JSONObject().put("mode", audioMode).put("ms", audioDelayMs))
            show()
        }
        later.setOnClickListener { set(audioDelayMs + step) }
        sooner.setOnClickListener { set(audioDelayMs - step) }
        none.setOnClickListener { set(0) }
        val content = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(padding, padding / 2, padding, 0)
            val spaced = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { bottomMargin = padding / 2 }
            addView(value)
            addView(later, spaced)
            addView(sooner, spaced)
            addView(none, spaced)
        }
        show()
        val dialog = AlertDialog.Builder(this)
            .setTitle("Posun zvuku · " + audioOutputTitle(audioMode).substringBefore(" –"))
            .setView(content)
            .setPositiveButton("Hotovo", null)
            .create()
        // okno u kraje a bez ztmavení obrazu, aby bylo vidět na ústa
        dialog.window?.setGravity(android.view.Gravity.BOTTOM or android.view.Gravity.END)
        dialog.window?.setDimAmount(0f)
        dialog.show()
        Dialogs.polish(dialog)
        later.requestFocus()
    }

    /** Sestaví přehrávač znovu (jiný výstup zvuku) a pokračuje tam, kde se hrálo. */
    private fun rebuild() {
        val stream = lastStream ?: return
        // přehrávač už není (skončil chybou): není na co navázat, nový výstup se použije při příštím spuštění
        val exo = player ?: return
        carriedTracks = exo.trackSelectionParameters   // zvolená stopa a titulky zůstávají
        carriedChanged = userChangedTracks
        carriedPlaying = exo.playWhenReady             // pozastavené video zůstane stát
        begin(stream, if (live) 0L else exo.currentPosition)
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
        // Obrazovka už není vidět (tlačítko Domů, vypnutí televize): video nesmí hrát dál na pozadí –
        // ani to, které se stihlo spustit těsně předtím. Po návratu čeká pozastavené.
        if (!isFinishing) player?.pause()
        saveProgress(ended = false)
        // Na pozadí nemají časovače co dělat: pozastavené video se nemusí každých 15 s ukládat a čas
        // konce na liště nikdo nevidí. Dřív běžely dál, dokud systém aplikaci neukončil.
        handler.removeCallbacks(saver)
        handler.removeCallbacks(clock)
        handler.removeCallbacks(healthy)
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
