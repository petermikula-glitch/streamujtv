package cz.petermikula.mojevidea

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.fragment.app.FragmentActivity
import org.json.JSONObject

/**
 * Upoutávka z YouTube přehraná přímo v aplikaci – ve vloženém přehrávači YouTube (jeho veřejné
 * rozhraní pro vkládání videí do stránek). Po skončení, nebo po tlačítku Zpět, se obrazovka zavře
 * a člověk je zpátky na stránce titulu; z aplikace YouTube se sám nevrátil.
 *
 * Když vložený přehrávač nejde použít – zařízení nemá WebView, video vkládání nedovoluje, nebo se do
 * chvíle nerozběhne – předá se upoutávka aplikaci YouTube jako dřív (`fallback`).
 */
class TrailerActivity : FragmentActivity() {
    private var web: WebView? = null
    private val handler = Handler(Looper.getMainLooper())
    private var started = false      // přehrávání se opravdu rozběhlo
    private var handedOver = false   // upoutávka už byla předána aplikaci YouTube
    private val giveUp = Runnable { if (!started) fallback() }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val key = intent.getStringExtra(EXTRA_KEY).orEmpty()
        if (!KEY.matches(key)) {
            fallback()
            return
        }
        val view = try {
            WebView(this)
        } catch (error: Exception) {   // zařízení bez WebView (nebo se právě aktualizuje)
            fallback()
            return
        }
        web = view
        view.setBackgroundColor(android.graphics.Color.BLACK)
        view.settings.javaScriptEnabled = true
        view.settings.mediaPlaybackRequiresUserGesture = false   // upoutávka se má rozběhnout sama
        view.settings.domStorageEnabled = true
        view.webChromeClient = WebChromeClient()
        // stránka je jen přehrávač: odkazy z ní (logo YouTube, doporučená videa) nikam nevedou
        view.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: android.webkit.WebResourceRequest?): Boolean = true
        }
        view.addJavascriptInterface(Bridge(), "Promitarna")
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(android.graphics.Color.BLACK)
            addView(view, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        })
        // Vložený přehrávač chce vědět, kdo ho vkládá: aplikace se hlásí svým jménem balíčku (tak to
        // YouTube pro aplikace předepisuje), nic se nepředstírá.
        val origin = "https://$packageName"
        view.loadDataWithBaseURL(origin, page(key, origin), "text/html", "utf-8", null)
    }

    private inner class Bridge {
        /** Stav přehrávače: 1 hraje, 0 skončil; `error` je chyba (video vkládání nedovoluje apod.). */
        @JavascriptInterface
        fun state(value: Int) {
            handler.post {
                if (isFinishing || isDestroyed) return@post
                when (value) {
                    PLAYING -> {
                        started = true
                        handler.removeCallbacks(giveUp)
                    }
                    ENDED -> finish()
                }
            }
        }

        @JavascriptInterface
        fun error(code: Int) {
            handler.post { if (!isFinishing && !isDestroyed) fallback() }
        }
    }

    /** Vložený přehrávač to nezvládl: upoutávku otevře aplikace YouTube (jako dřív) a obrazovka se zavře. */
    private fun fallback() {
        if (handedOver || isFinishing) return
        handedOver = true
        handler.removeCallbacks(giveUp)
        intent.getStringExtra(EXTRA_FALLBACK)?.let { Navigator.openOutside(this, JSONObject(it)) }
        finish()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            val script = when (event.keyCode) {
                KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> "toggle()"
                KeyEvent.KEYCODE_MEDIA_PLAY -> "player && player.playVideo()"
                KeyEvent.KEYCODE_MEDIA_PAUSE -> "player && player.pauseVideo()"
                KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> "seek(10)"
                KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_MEDIA_REWIND -> "seek(-10)"
                else -> null
            }
            if (script != null) {
                web?.evaluateJavascript(script, null)
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onPause() {
        // lhůta běží jen, dokud je obrazovka vidět: jinak by se YouTube otevřel sám na pozadí
        handler.removeCallbacks(giveUp)
        super.onPause()
        web?.onPause()
    }

    override fun onResume() {
        super.onResume()
        web?.onResume()
        if (web != null && !started && !handedOver) handler.postDelayed(giveUp, START_TIMEOUT_MS)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        web?.let { view ->
            (view.parent as? ViewGroup)?.removeView(view)
            view.loadUrl("about:blank")
            view.destroy()
        }
        web = null
        super.onDestroy()
    }

    companion object {
        private const val EXTRA_KEY = "trailer_key"
        private const val EXTRA_FALLBACK = "trailer_fallback"
        private const val START_TIMEOUT_MS = 15000L   // do té doby se musí rozběhnout, jinak převezme aplikace YouTube
        private const val PLAYING = 1
        private const val ENDED = 0
        private val KEY = Regex("[A-Za-z0-9_-]{6,20}")   // označení videa na YouTube

        fun start(context: Context, key: String, fallback: JSONObject) {
            context.startActivity(Intent(context, TrailerActivity::class.java)
                .putExtra(EXTRA_KEY, key).putExtra(EXTRA_FALLBACK, fallback.toString()))
        }

        /** Stránka s vloženým přehrávačem přes celou obrazovku; stav hlásí zpátky aplikaci. */
        private fun page(key: String, origin: String) = """
            <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
            <meta name="referrer" content="strict-origin-when-cross-origin">
            <style>html,body{margin:0;height:100%;background:#000;overflow:hidden}#player{width:100%;height:100%}</style>
            </head><body><div id="player"></div><script>
            var player = null;
            function toggle() { if (!player) return; player.getPlayerState() === 1 ? player.pauseVideo() : player.playVideo(); }
            function seek(step) { if (player) player.seekTo(Math.max(0, player.getCurrentTime() + step), true); }
            function onYouTubeIframeAPIReady() {
              player = new YT.Player('player', {
                width: '100%', height: '100%', videoId: '$key',
                playerVars: {autoplay: 1, controls: 1, rel: 0, fs: 0, playsinline: 1, hl: 'cs', cc_lang_pref: 'cs',
                             origin: '$origin'},
                events: {
                  onReady: function (event) { event.target.playVideo(); },
                  onStateChange: function (event) { Promitarna.state(event.data); },
                  onError: function (event) { Promitarna.error(event.data); }
                }
              });
            }
            </script><script src="https://www.youtube.com/iframe_api" onerror="Promitarna.error(-1)"></script>
            </body></html>
        """.trimIndent()
    }
}
