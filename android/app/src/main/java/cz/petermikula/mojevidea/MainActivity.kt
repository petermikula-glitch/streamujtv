package cz.petermikula.mojevidea

import android.content.Intent
import android.os.Bundle
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.BrowseSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.HeaderItem
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ListRowPresenter
import androidx.leanback.widget.OnItemViewClickedListener

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(android.R.id.content, MainFragment())
                .commit()
        }
    }
}

/** Hlavní obrazovka: řady karet (Rozkoukané, filmy, seriály, iVysílání, Nástroje). */
class MainFragment : BrowseSupportFragment() {
    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private var loadedOnce = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.app_name)
        headersState = HEADERS_ENABLED
        isHeadersTransitionOnBackEnabled = true
        adapter = rows
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { load() } }
        }
        setOnSearchClickedListener {
            startActivity(Intent(requireContext(), SearchActivity::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        load()  // po návratu z přehrávače nebo nastavení se mění Rozkoukané i katalog
    }

    private fun load() {
        if (!loadedOnce) {
            rows.clear()
            rows.add(ListRow(HeaderItem(0, "Načítám…"), ArrayObjectAdapter(CardPresenter())))
        }
        Api.call("home") { result ->
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
                if (!loadedOnce) showMessage(result.error ?: "Nepodařilo se načíst obsah.")
                return@call
            }
            loadedOnce = true
            val fresh = ArrayList<ListRow>()
            val list = data.optJSONArray("rows")
            for (index in 0 until (list?.length() ?: 0)) {
                val row = list!!.optJSONObject(index) ?: continue
                val cards = ArrayObjectAdapter(CardPresenter())
                cards.addAll(0, Card.list(row.optJSONArray("items")))
                fresh.add(ListRow(HeaderItem(index.toLong(), row.optString("title")), cards))
            }
            rows.setItems(fresh, null)
        }
    }

    private fun showMessage(text: String) {
        rows.clear()
        rows.add(ListRow(HeaderItem(0, text), ArrayObjectAdapter(CardPresenter())))
    }
}
