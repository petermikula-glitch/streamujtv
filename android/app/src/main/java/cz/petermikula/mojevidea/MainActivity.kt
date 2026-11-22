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
import androidx.leanback.widget.OnItemViewSelectedListener

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
    private var shown = ""
    private var shapes = emptyList<Boolean>()
    private var backdrop: Backdrop? = null

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
        onItemViewSelectedListener = OnItemViewSelectedListener { _, item, _, _ ->
            backdrop?.show((item as? Card)?.image.orEmpty())
        }
    }

    override fun onActivityCreated(savedInstanceState: Bundle?) {
        @Suppress("DEPRECATION")
        super.onActivityCreated(savedInstanceState)
        backdrop = Backdrop(requireActivity())
    }

    override fun onDestroy() {
        backdrop?.release()
        super.onDestroy()
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
            val raw = data.toString()
            if (loadedOnce && raw == shown) return@call  // beze změny: výběr i pozice zůstanou
            shown = raw
            val titles = ArrayList<String>()
            val items = ArrayList<List<Card>>()
            val list = data.optJSONArray("rows")
            for (index in 0 until (list?.length() ?: 0)) {
                val row = list!!.optJSONObject(index) ?: continue
                titles.add(row.optString("title"))
                items.add(Card.list(row.optJSONArray("items")))
            }
            val posters = items.map { CardPresenter.posterShape(it) }
            val sameRows = loadedOnce && rows.size() == titles.size && posters == shapes &&
                titles.indices.all { (rows.get(it) as? ListRow)?.headerItem?.name == titles[it] }
            if (sameRows) {
                // stejné řady: vyměnit jen změněné karty, ať výběr neuskočí na začátek
                for (index in titles.indices) {
                    ((rows.get(index) as ListRow).adapter as ArrayObjectAdapter).setItems(items[index], Card.DIFF)
                }
            } else {
                val fresh = ArrayList<ListRow>()
                for (index in titles.indices) {
                    val cards = ArrayObjectAdapter(CardPresenter(posters[index]))
                    cards.addAll(0, items[index])
                    fresh.add(ListRow(HeaderItem(index.toLong(), titles[index]), cards))
                }
                rows.setItems(fresh, null)
            }
            shapes = posters
            loadedOnce = true
        }
    }

    private fun showMessage(text: String) {
        rows.clear()
        rows.add(ListRow(HeaderItem(0, text), ArrayObjectAdapter(CardPresenter())))
    }
}
