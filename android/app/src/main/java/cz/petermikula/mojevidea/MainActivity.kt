package cz.petermikula.mojevidea

import android.content.Intent
import android.os.Bundle
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.BrowseSupportFragment
import androidx.leanback.app.RowsSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.HeaderItem
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ListRowPresenter
import androidx.leanback.widget.OnItemViewClickedListener
import androidx.leanback.widget.OnItemViewSelectedListener
import androidx.leanback.widget.PageRow
import androidx.leanback.widget.Presenter
import androidx.leanback.widget.PresenterSelector
import org.json.JSONArray

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

/**
 * Hlavní obrazovka: postranní nabídka se sekcemi (Přehraj.to, iVysílání, Nástroje). Každá sekce je
 * stránka s vlastními řadami karet. Obsah i názvy sekcí dodává logika v Pythonu (akce `home`).
 */
class MainFragment : BrowseSupportFragment() {
    private class Section(val title: String, val icon: String, val rows: JSONArray)

    private val pages = ArrayObjectAdapter(ListRowPresenter())
    private var sections = emptyList<Section>()
    private var loadedOnce = false
    private var shown = ""
    private var backdrop: Backdrop? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private val refresh = Runnable { if (isResumed) load() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.app_name)
        badgeDrawable = androidx.core.content.ContextCompat.getDrawable(requireContext(), R.drawable.logo)
        headersState = HEADERS_ENABLED
        isHeadersTransitionOnBackEnabled = true
        mainFragmentRegistry.registerFragment(PageRow::class.java, object : FragmentFactory<SectionFragment>() {
            override fun createFragment(row: Any?): SectionFragment =
                SectionFragment.create((row as PageRow).headerItem.id.toInt())
        })
        // postranní nabídka: u každé sekce její ikona
        val header = SectionHeaderPresenter { id -> sections.getOrNull(id.toInt())?.icon.orEmpty() }
        setHeaderPresenterSelector(object : PresenterSelector() {
            override fun getPresenter(item: Any?): Presenter = header
        })
        adapter = pages
        setOnSearchClickedListener {
            startActivity(Intent(requireContext(), SearchActivity::class.java))
        }
    }

    /** Pozadí podle vybrané karty (volají stránky sekcí). */
    fun showBackdrop(image: String) {
        backdrop?.show(image)
    }

    override fun onActivityCreated(savedInstanceState: Bundle?) {
        @Suppress("DEPRECATION")
        super.onActivityCreated(savedInstanceState)
        backdrop = Backdrop(requireActivity())
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        super.onPause()
    }

    override fun onDestroy() {
        backdrop?.release()
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        load()  // po návratu z přehrávače nebo nastavení se mění Rozkoukané i katalog
    }

    /** Řady sekce pro její stránku. */
    fun rowsOf(index: Int): JSONArray? = sections.getOrNull(index)?.rows

    fun load() {
        if (!loadedOnce && pages.size() == 0) {
            pages.add(ListRow(HeaderItem(0, "Načítám…"), ArrayObjectAdapter(CardPresenter())))
        }
        Api.call("home") { result ->
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
                if (!loadedOnce) showMessage(result.error ?: "Nepodařilo se načíst obsah.")
                return@call
            }
            // dokud se stahuje, obnovovat řadu Stažené (stejná data nic nepřekreslí)
            handler.removeCallbacks(refresh)
            if (data.optBoolean("pending")) handler.postDelayed(refresh, 3000)
            val raw = data.toString()
            if (loadedOnce && raw == shown) return@call  // beze změny: výběr i pozice zůstanou
            shown = raw
            val list = data.optJSONArray("sections")
            val fresh = (0 until (list?.length() ?: 0)).mapNotNull { index ->
                list!!.optJSONObject(index)?.let {
                    Section(it.optString("title"), it.optString("icon"), it.optJSONArray("rows") ?: JSONArray())
                }
            }
            val sameMenu = loadedOnce && fresh.map { it.title } == sections.map { it.title }
            sections = fresh
            if (sameMenu) {
                // stejné sekce: stránky si jen vymění změněné karty, výběr zůstane na místě
                childFragmentManager.fragments.filterIsInstance<SectionFragment>().forEach { it.refresh() }
            } else {
                pages.setItems(fresh.mapIndexed { index, section ->
                    PageRow(HeaderItem(index.toLong(), section.title))
                }, null)
            }
            if (!loadedOnce) {
                // po startu: navázat na rozpracované stahování a zeptat se na novou verzi
                if (data.optBoolean("pending")) DownloadService.start(requireContext())
                Updater.checkOnStart(requireActivity())
            }
            loadedOnce = true
        }
    }

    private fun showMessage(text: String) {
        pages.clear()
        pages.add(ListRow(HeaderItem(0, text), ArrayObjectAdapter(CardPresenter())))
    }
}

/** Stránka jedné sekce: její řady karet. Data si bere z hlavní obrazovky. */
class SectionFragment : RowsSupportFragment() {
    companion object {
        private const val INDEX = "index"

        fun create(index: Int) = SectionFragment().apply { arguments = Bundle().apply { putInt(INDEX, index) } }
    }

    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private var titles = emptyList<String>()
    private var shapes = emptyList<Boolean>()

    private fun host() = parentFragment as? MainFragment

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        adapter = rows
        // Stránka sekce si kliknutí a výběr obsluhuje sama: hlavní obrazovka své posluchače
        // předává jen obyčejným řadám, ne stránkám.
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { host()?.load() } }
        }
        onItemViewSelectedListener = OnItemViewSelectedListener { _, item, _, _ ->
            host()?.showBackdrop((item as? Card)?.image.orEmpty())
        }
        refresh()
    }

    fun refresh() {
        val list = host()?.rowsOf(requireArguments().getInt(INDEX)) ?: return
        val names = ArrayList<String>()
        val items = ArrayList<List<Card>>()
        for (index in 0 until list.length()) {
            val row = list.optJSONObject(index) ?: continue
            names.add(row.optString("title"))
            items.add(Card.list(row.optJSONArray("items")))
        }
        val posters = items.map { CardPresenter.posterShape(it) }
        if (names == titles && posters == shapes && rows.size() == names.size) {
            // stejné řady: vyměnit jen změněné karty, ať výběr neuskočí na začátek
            for (index in names.indices) {
                ((rows.get(index) as ListRow).adapter as ArrayObjectAdapter).setItems(items[index], Card.DIFF)
            }
        } else {
            rows.setItems(names.indices.map { index ->
                val cards = ArrayObjectAdapter(CardPresenter(posters[index]) { card ->
                    Navigator.menu(requireActivity(), card) { host()?.load() }
                })
                cards.addAll(0, items[index])
                ListRow(HeaderItem(index.toLong(), names[index]), cards)
            }, null)
        }
        titles = names
        shapes = posters
    }
}
