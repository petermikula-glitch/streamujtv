package cz.petermikula.mojevidea

import android.content.Intent
import android.os.Bundle
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.BrowseSupportFragment
import androidx.leanback.app.RowsSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.ClassPresenterSelector
import androidx.leanback.widget.FocusHighlight
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
    private class Section(val title: String, val icon: String, val rows: JSONArray, val hero: org.json.JSONObject?)

    private var rail: android.widget.LinearLayout? = null

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
        // sbalená nabídka: místo ní zůstane vlevo úzká lišta ikon sekcí
        setBrowseTransitionListener(object : BrowseTransitionListener() {
            override fun onHeadersTransitionStart(withHeaders: Boolean) {
                showRail(!withHeaders)
            }
        })
    }

    override fun onViewCreated(view: android.view.View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val density = resources.displayMetrics.density
        val strip = android.widget.LinearLayout(requireContext()).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER
            visibility = android.view.View.GONE
            background = android.graphics.drawable.GradientDrawable(
                android.graphics.drawable.GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(0xF20B1220.toInt(), 0xE60B1220.toInt(), 0x000B1220),
            )
        }
        (view as? android.view.ViewGroup)?.addView(strip, android.widget.FrameLayout.LayoutParams(
            (52 * density).toInt(), android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.Gravity.START))
        rail = strip
    }

    override fun onDestroyView() {
        rail = null
        super.onDestroyView()
    }

    /** Lišta ikon sekcí; vybraná sekce je zlatá, ostatní tlumené. */
    private fun showRail(visible: Boolean) {
        val strip = rail ?: return
        val context = context ?: return
        strip.removeAllViews()
        val density = resources.displayMetrics.density
        val size = (24 * density).toInt()
        sections.forEachIndexed { index, section ->
            val id = if (section.icon.isEmpty()) 0 else resources.getIdentifier("ic_" + section.icon, "drawable", context.packageName)
            if (id == 0) return@forEachIndexed
            val current = index == selectedPosition
            val icon = android.widget.ImageView(context).apply {
                setImageDrawable(androidx.core.content.ContextCompat.getDrawable(context, id)?.mutate()?.apply {
                    setTint(androidx.core.content.ContextCompat.getColor(context, if (current) R.color.accent else R.color.text_secondary))
                })
                alpha = if (current) 1f else 0.6f
            }
            strip.addView(icon, android.widget.LinearLayout.LayoutParams(size, size).apply {
                topMargin = (14 * density).toInt()
                bottomMargin = (14 * density).toInt()
            })
        }
        strip.visibility = if (visible && strip.childCount > 0) android.view.View.VISIBLE else android.view.View.GONE
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

    /** Úvodní panel sekce (naposledy rozkoukané video), pokud nějaký má. */
    fun heroOf(index: Int): org.json.JSONObject? = sections.getOrNull(index)?.hero

    fun load() {
        if (!loadedOnce && pages.size() == 0) {
            // než dorazí obsah: obrysy karet
            val waiting = ArrayObjectAdapter(CardPresenter())
            waiting.addAll(0, Card.skeletons(5))
            pages.add(ListRow(HeaderItem(0, "Načítám…"), waiting))
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
                    Section(it.optString("title"), it.optString("icon"), it.optJSONArray("rows") ?: JSONArray(),
                        it.optJSONObject("hero"))
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
            WatchNext.sync(requireContext())  // řada „Pokračovat ve sledování“ na domovské obrazovce televize
            if (!loadedOnce) {
                // po startu: navázat na rozpracované stahování a zeptat se na novou verzi
                if (data.optBoolean("pending")) DownloadService.start(requireContext())
                Updater.checkOnStart(requireActivity())
                when {
                    // úplně první spuštění: průvodce účtem, klíčem a předvolbami
                    data.optBoolean("welcome") -> startActivity(Intent(requireContext(), WelcomeActivity::class.java))
                    // víc profilů: zeptat se, kdo se dívá
                    (data.optJSONObject("profile")?.optInt("count") ?: 1) > 1 -> startActivity(
                        Intent(requireContext(), ListActivity::class.java)
                            .putExtra(Navigator.EXTRA_CALL, "profiles")
                            .putExtra(Navigator.EXTRA_TITLE, "Kdo se dívá?")
                            .putExtra(Navigator.EXTRA_POSTER, true)
                    )
                }
            }
            loadedOnce = true
            watchFavorites()
        }
    }

    private var favoritesAsked = 0L

    /**
     * Hlídání oblíbených filmů: objevila se na Přehraj.to verze v preferovaném jazyce a kvalitě?
     * Ptá se po startu a pak průběžně při návratech na hlavní obrazovku (logika hledá nejvýš jednou
     * za pár hodin); na pozadí, když aplikace neběží, se nehledá.
     */
    private fun watchFavorites() {
        val now = android.os.SystemClock.elapsedRealtime()
        if (favoritesAsked != 0L && now - favoritesAsked < 10 * 60_000L) return
        favoritesAsked = now
        Api.call("favorites_check") { check ->
            val found = check.data?.optJSONArray("found")
            val activity = activity
            if (!isAdded || activity == null || found == null || found.length() == 0) return@call
            val items = (0 until found.length()).mapNotNull { found.optJSONObject(it) }
            val text = items.joinToString("\n") { item ->
                listOf(item.optString("title"), item.optString("detail")).filter { it.isNotEmpty() }.joinToString(" – ")
            }
            android.app.AlertDialog.Builder(activity)
                .setTitle(if (items.size == 1) "Oblíbený film je nově na Přehraj.to" else "Oblíbené filmy jsou nově na Přehraj.to")
                .setMessage(text)
                .setPositiveButton("Otevřít") { _, _ ->
                    Navigator.open(activity, Target.from(items[0].optJSONObject("target")), items[0].optString("title")) { load() }
                }
                .setNegativeButton("Později", null)
                .show()
            load()  // karta v Oblíbených dostane štítek „Nově“
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
        private const val HERO = "\u0000hero"  // označení řady s úvodním panelem

        fun create(index: Int) = SectionFragment().apply { arguments = Bundle().apply { putInt(INDEX, index) } }
    }

    private val rows = ArrayObjectAdapter(ClassPresenterSelector().apply {
        // úvodní panel: bez zvětšení při výběru (je přes celou šířku) a bez nadpisu řady
        addClassPresenter(HeroRow::class.java, ListRowPresenter(FocusHighlight.ZOOM_FACTOR_NONE).apply {
            shadowEnabled = false
        })
        addClassPresenter(ListRow::class.java, ListRowPresenter())
    })
    private var titles = emptyList<String>()
    private var shapes = emptyList<Boolean>()
    private var grids = emptyList<Boolean>()

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
        val tiles = ArrayList<Boolean>()  // mřížka velkých dlaždic
        host()?.heroOf(requireArguments().getInt(INDEX))?.let { hero ->
            // úvodní panel je první řada; nápis tlačítka nese karta ve štítku
            names.add(HERO)
            tiles.add(false)
            items.add(listOf(Card.from(org.json.JSONObject(hero.toString())
                .put("badges", JSONArray().put(hero.optString("label"))).put("wide", true))))
        }
        for (index in 0 until list.length()) {
            val row = list.optJSONObject(index) ?: continue
            names.add(row.optString("title"))
            tiles.add(row.optBoolean("tiles"))
            items.add(Card.list(row.optJSONArray("items")))
        }
        val posters = items.map { CardPresenter.posterShape(it) }
        if (names == titles && posters == shapes && tiles == grids && rows.size() == names.size) {
            // stejné řady: vyměnit jen změněné karty, ať výběr neuskočí na začátek
            for (index in names.indices) {
                ((rows.get(index) as ListRow).adapter as ArrayObjectAdapter).setItems(items[index], Card.DIFF)
            }
        } else {
            rows.setItems(names.indices.map { index ->
                if (names[index] == HERO) {
                    val cards = ArrayObjectAdapter(HeroPresenter())
                    cards.addAll(0, items[index])
                    return@map HeroRow(cards)
                }
                val cards = ArrayObjectAdapter(CardPresenter(posters[index], tiles[index]) { card ->
                    Navigator.menu(requireActivity(), card) { host()?.load() }
                })
                cards.addAll(0, items[index])
                // další řada téže mřížky nemá nadpis: bez něj na sebe řady těsně navazují
                if (names[index].isEmpty()) ListRow(cards) else ListRow(HeaderItem(index.toLong(), names[index]), cards)
            }, null)
        }
        titles = names
        shapes = posters
        grids = tiles
    }
}
