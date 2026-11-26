package cz.petermikula.mojevidea

import android.Manifest
import android.app.Activity
import android.app.SearchManager
import android.content.pm.PackageManager
import android.speech.RecognitionListener
import android.speech.SpeechRecognizer
import androidx.core.content.ContextCompat
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognizerIntent
import android.view.View
import android.widget.ImageButton
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.TextView
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.RowsSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.HeaderItem
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ListRowPresenter
import androidx.leanback.widget.OnItemViewClickedListener
import org.json.JSONObject

/** Hledání: výsledky z místního katalogu, iVysílání a Přehraj.to. */
class SearchActivity : FragmentActivity() {
    companion object {
        const val EXTRA_VOICE = "voice"   // otevřeno tlačítkem mikrofonu na ovladači: rovnou poslouchat
    }

    /** Mikrofon na ovladači, když je Hledat už otevřené. */
    fun listenNow() {
        val button = findViewById<View>(R.id.search_voice) ?: return
        if (button.visibility == View.VISIBLE) listen(button)
    }

    /** Podržené Zpět: rovnou do hlavní nabídky (viz [Home]). */
    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean =
        Home.dispatch(this, event) || super.dispatchKeyEvent(event)

    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private lateinit var message: TextView
    private var latestSearch = 0
    private lateinit var input: EditText

    /** Výsledek hlasového zadání: rozpoznaný text rovnou hledat. */
    private val voice = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val spoken = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull().orEmpty().trim()
        if (result.resultCode == Activity.RESULT_OK && spoken.isNotEmpty()) {
            input.setText(spoken)
            search(spoken)
        }
    }

    private var recognizer: SpeechRecognizer? = null
    private var listening = false

    /** Povolení mikrofonu (ptá se jen poprvé); bez něj aplikace neslyší. */
    private val microphone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRecognizer()
        else note("Bez povolení mikrofonu aplikace neslyší. Povolit ho jde v Nastavení televize → Aplikace → " +
            "Promítárna → Oprávnění, nebo napište název klávesnicí.")
    }

    /**
     * Hledání hlasem přímo v aplikaci (rozpoznávání řeči od Googlu, text se rovnou hledá). Dřív se volala
     * hlasová obrazovka systému – na Google TV ji ale převzal Asistent a hledal mimo aplikaci. Když
     * rozpoznávání v aplikaci není k dispozici, zkusí se ta systémová obrazovka.
     */
    private fun listen(button: View) {
        if (listening) {
            recognizer?.stopListening()
            return
        }
        if (SpeechRecognizer.isRecognitionAvailable(this)) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                startRecognizer()
            } else {
                microphone.launch(Manifest.permission.RECORD_AUDIO)
            }
            return
        }
        listenWithSystem(button)
    }

    private fun startRecognizer(language: Boolean = true) {
        val speech = recognizer ?: SpeechRecognizer.createSpeechRecognizer(this).also { recognizer = it }
        speech.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                listening = true
                findViewById<View>(R.id.search_voice)?.isActivated = true
                note("Poslouchám… řekněte název filmu, seriálu nebo herce.")
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() = note("Rozpoznávám…")
            override fun onEvent(eventType: Int, params: Bundle?) {}

            override fun onPartialResults(partialResults: Bundle?) {
                spoken(partialResults)?.let { input.setText(it) }
            }

            override fun onResults(results: Bundle?) {
                stopped()
                val text = spoken(results)
                if (text == null) note("Nerozuměl jsem. Zkuste to znovu, nebo napište název klávesnicí.")
                else {
                    input.setText(text)
                    search(text)
                }
            }

            override fun onError(error: Int) {
                stopped()
                android.util.Log.w("Promitarna", "rozpoznávání řeči: chyba $error")
                // čeština jako jazyk rozpoznávání nejde: zkusit jazyk zařízení
                if (language && (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED ||
                        error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE)) {
                    startRecognizer(language = false)
                    return
                }
                note(voiceError(error))
            }
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
        if (language) intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "cs-CZ")
        try {
            speech.startListening(intent)
        } catch (error: RuntimeException) {
            android.util.Log.w("Promitarna", "rozpoznávání řeči se nespustilo", error)
            note(voiceError(SpeechRecognizer.ERROR_CLIENT))
        }
    }

    private fun spoken(results: Bundle?): String? =
        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim()?.ifEmpty { null }

    private fun stopped() {
        listening = false
        findViewById<View>(R.id.search_voice)?.isActivated = false
    }

    private fun voiceError(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
            "Nic jsem neslyšel nebo nerozuměl. Stiskněte mikrofon a mluvte do ovladače."
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT, SpeechRecognizer.ERROR_SERVER ->
            "Rozpoznávání řeči teď nejde (potřebuje internet). Zkuste to za chvíli."
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
            "Aplikace nemá povolený mikrofon: Nastavení televize → Aplikace → Promítárna → Oprávnění."
        SpeechRecognizer.ERROR_AUDIO -> "Mikrofon v ovladači se nepodařilo zapnout. Zkuste to znovu."
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Rozpoznávání řeči je zaneprázdněné. Zkuste to za chvíli."
        else -> "Hledání hlasem se nezdařilo (chyba $error). Napište název klávesnicí."
    }

    override fun onStop() {
        super.onStop()
        if (listening) recognizer?.cancel()
        stopped()
    }

    override fun onDestroy() {
        recognizer?.destroy()
        recognizer = null
        super.onDestroy()
    }

    /** Záloha: hlasová obrazovka systému (zařízení bez rozpoznávání řeči pro aplikace). */
    private fun listenWithSystem(button: View) {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "cs-CZ")
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Řekněte název filmu nebo seriálu")
        try {
            voice.launch(intent)
        } catch (error: ActivityNotFoundException) {
            // zařízení bez hlasového zadávání: tlačítko schovat a psát klávesnicí
            button.visibility = View.GONE
            Toast.makeText(this, "Hlasové zadávání na tomto zařízení není k dispozici.", Toast.LENGTH_LONG).show()
            input.requestFocus()
        }
    }

    private fun showRows(list: org.json.JSONArray?): Int {
        val fresh = ArrayList<ListRow>()
        for (index in 0 until (list?.length() ?: 0)) {
            val row = list!!.optJSONObject(index) ?: continue
            val found = Card.list(row.optJSONArray("items"))
            val cards = ArrayObjectAdapter(CardPresenter(CardPresenter.posterShape(found)) { card ->
                Navigator.menu(this, card)
            })
            cards.addAll(0, found)
            fresh.add(ListRow(HeaderItem(index.toLong(), row.optString("title")), cards))
        }
        rows.setItems(fresh, null)
        return fresh.size
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_search)
        message = findViewById(R.id.search_message)
        val results = RowsSupportFragment()
        results.adapter = rows
        results.onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(this, it) }
        }
        supportFragmentManager.beginTransaction().replace(R.id.search_rows, results).commit()

        input = findViewById(R.id.search_text)
        findViewById<ImageButton>(R.id.search_voice).setOnClickListener { listen(it) }
        input.setOnEditorActionListener { view, actionId, event ->
            // Enter z připojené klávesnice nebo z ovladače v telefonu chodí jako stisk klávesy, ne jako akce
            val enter = actionId == EditorInfo.IME_NULL && event?.keyCode == android.view.KeyEvent.KEYCODE_ENTER
            if (enter && event?.action != android.view.KeyEvent.ACTION_DOWN) {
                true  // puštění klávesy: hledání už spustil stisk
            } else if (actionId == EditorInfo.IME_ACTION_SEARCH || actionId == EditorInfo.IME_ACTION_DONE || enter) {
                val keyboard = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
                keyboard.hideSoftInputFromWindow(view.windowToken, 0)
                search(view.text.toString().trim())
                true
            } else {
                false
            }
        }
        val prepared = preparedQuery(intent)
        if (prepared.isEmpty() && intent.getBooleanExtra(EXTRA_VOICE, false) && savedInstanceState == null) {
            input.post { listenNow() }
        }
        if (prepared.isEmpty()) {
            input.requestFocus()
            // prázdná obrazovka: nabídnout naposledy hledané
            Api.call("search_history") { result ->
                if (isFinishing || isDestroyed || latestSearch != 0) return@call
                showRows(result.data?.optJSONArray("rows"))
            }
        } else {
            // připravený dotaz: rovnou hledat, klávesnice se neotevírá
            input.setText(prepared)
            search(prepared)
        }
    }

    /** Dotaz, se kterým se Hledat otevírá: z aplikace (parametr `q`), nebo od Asistenta Google a systému. */
    private fun preparedQuery(intent: Intent): String {
        val inApp = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}").optString("q").trim()
        return inApp.ifEmpty { intent.getStringExtra(SearchManager.QUERY).orEmpty().trim() }
    }

    /** Hlášení pod hledacím polem; s ilustrací (prázdný výsledek, výpadek spojení) se vystředí. */
    private fun note(text: String, art: String = "") {
        message.text = text
        message.gravity = if (art.isEmpty()) android.view.Gravity.START else android.view.Gravity.CENTER_HORIZONTAL
        val density = resources.displayMetrics.density
        message.setPadding(0, if (art.isEmpty()) 0 else (40 * density).toInt(), if (art.isEmpty()) 0 else (56 * density).toInt(), 0)
        EmptyArt.show(message, art)
    }

    private fun search(query: String) {
        if (query.isEmpty()) return
        val searchId = ++latestSearch
        note("Hledám…")
        Api.call("search", JSONObject().put("q", query)) { result ->
            if (isFinishing || isDestroyed || searchId != latestSearch) return@call
            val data = result.data
            if (data == null) {
                showRows(null)
                note(result.error.orEmpty(), if (result.offline) EmptyArt.OFFLINE else "")
                return@call
            }
            val count = showRows(data.optJSONArray("rows"))
            val notice = data.optString("notice")
            if (count == 0) note(notice.ifEmpty { "Nic jsem nenašel." }, EmptyArt.NOTHING) else note(notice)
            if (count > 0) findViewById<android.view.View>(R.id.search_rows).requestFocus()
        }
    }
}
