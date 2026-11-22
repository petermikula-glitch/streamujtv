package cz.petermikula.mojevidea

import android.content.Context
import android.os.Bundle
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

/** Hledání: textové pole nahoře, pod ním řady výsledků z katalogu a z iVysílání. */
class SearchActivity : FragmentActivity() {
    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private lateinit var message: TextView

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

        val input = findViewById<EditText>(R.id.search_text)
        input.setOnEditorActionListener { view, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH || actionId == EditorInfo.IME_ACTION_DONE) {
                val keyboard = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
                keyboard.hideSoftInputFromWindow(view.windowToken, 0)
                search(view.text.toString().trim())
                true
            } else {
                false
            }
        }
        input.requestFocus()
    }

    private fun search(query: String) {
        if (query.isEmpty()) return
        message.text = "Hledám…"
        Api.call("search", JSONObject().put("q", query)) { result ->
            if (isFinishing || isDestroyed) return@call
            val data = result.data
            if (data == null) {
                message.text = result.error
                return@call
            }
            val fresh = ArrayList<ListRow>()
            val list = data.optJSONArray("rows")
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
            message.text = if (fresh.isEmpty()) "Nic jsem nenašel." else ""
            if (fresh.isNotEmpty()) findViewById<android.view.View>(R.id.search_rows).requestFocus()
        }
    }
}
