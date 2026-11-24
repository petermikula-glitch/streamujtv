package cz.petermikula.mojevidea

import android.app.Activity
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
    /** Podržené Zpět: rovnou do hlavní nabídky (viz [Home]). */
    override fun onKeyLongPress(keyCode: Int, event: android.view.KeyEvent?): Boolean =
        Home.longPress(this, keyCode) || super.onKeyLongPress(keyCode, event)

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

    private fun listen(button: View) {
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
        val prepared = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}").optString("q").trim()
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
