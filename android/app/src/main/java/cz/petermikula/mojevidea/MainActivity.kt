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
 * Hlavní obrazovka: postranní nabídka se sekcemi (Oblíbené, Rozkoukané, Objevuj, iVysílání, Nástroje).
 * Každá sekce je stránka s vlastními řadami karet. Obsah i názvy sekcí dodává logika v Pythonu
 * (akce `home`). Vedle tlačítka hledání je aktivní profil; kliknutím se přepíná.
 */
class MainFragment : BrowseSupportFragment(), RowsHost {
    private companion object {
        /** Úvodní kroky už proběhly (obrazovku umí systém vytvořit znovu, třeba po připojení ovladače). */
        var started = false
    }

    private class Section(val title: String, val icon: String, val rows: JSONArray, val hero: org.json.JSONObject?)

    private var rail: android.widget.LinearLayout? = null
    private var profile: android.widget.LinearLayout? = null

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
        brandColor = 0x00D2C9B4  // nabídka nemá vlastní plochu: leží na stejném papíře jako obsah
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
                intArrayOf(0xF2D2C9B4.toInt(), 0xE6D2C9B4.toInt(), 0x00D2C9B4),
            )
        }
        (view as? android.view.ViewGroup)?.addView(strip, android.widget.FrameLayout.LayoutParams(
            (52 * density).toInt(), android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.Gravity.START))
        rail = strip
        addProfile()
    }

    override fun onDestroyView() {
        (titleView as? android.view.ViewGroup)?.viewTreeObserver?.removeOnPreDrawListener(profileSync)
        rail = null
        profile = null
        super.onDestroyView()
    }

    /**
     * Aktivní profil (obrázek a jméno) v záhlaví vedle tlačítka hledání; kliknutí otevře výběr profilu.
     * Záhlaví předává šipky rovnou obsahu, proto si přechod mezi hledáním a profilem řeší obě tlačítka sama.
     */
    private fun addProfile() {
        val bar = titleView as? android.view.ViewGroup ?: return
        val context = requireContext()
        val density = resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()
        val idle = android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = 22 * density
            setColor(0x00000000)
        }
        val focused = android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = 22 * density
            setColor(androidx.core.content.ContextCompat.getColor(context, R.color.surface_focused))
            setStroke(dp(2), androidx.core.content.ContextCompat.getColor(context, R.color.accent))
        }
        val chip = android.widget.LinearLayout(context).apply {
            orientation = android.widget.LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            isFocusable = true
            isFocusableInTouchMode = true
            visibility = android.view.View.GONE  // dokud nedorazí jméno profilu
            setPadding(dp(4), dp(4), dp(14), dp(4))
            background = android.graphics.drawable.StateListDrawable().apply {
                addState(intArrayOf(android.R.attr.state_focused), focused)
                addState(intArrayOf(), idle)
            }
            contentDescription = "Profil"
        }
        val avatar = android.widget.ImageView(context).apply {
            scaleType = android.widget.ImageView.ScaleType.CENTER_CROP
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: android.view.View, outline: android.graphics.Outline) {
                    outline.setOval(0, 0, view.width, view.height)
                }
            }
            clipToOutline = true
        }
        val name = android.widget.TextView(context).apply {
            setTextColor(androidx.core.content.ContextCompat.getColor(context, R.color.text_primary))
            textSize = 18f
            typeface = android.graphics.Typeface.create("sans-serif-condensed", android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            maxWidth = dp(150)
        }
        chip.addView(avatar, android.widget.LinearLayout.LayoutParams(dp(36), dp(36)))
        chip.addView(name, android.widget.LinearLayout.LayoutParams(
            android.view.ViewGroup.LayoutParams.WRAP_CONTENT, android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { marginStart = dp(8) })
        chip.setOnFocusChangeListener { view, has ->
            view.animate().scaleX(if (has) 1.06f else 1f).scaleY(if (has) 1.06f else 1f).setDuration(120).start()
        }
        chip.setOnClickListener {
            startActivity(Intent(requireContext(), ListActivity::class.java)
                .putExtra(Navigator.EXTRA_CALL, "profiles")
                .putExtra(Navigator.EXTRA_TITLE, "Kdo se dívá?")
                .putExtra(Navigator.EXTRA_POSTER, true))
        }
        // první v pořadí: šipka nahoru z obsahu tak dál míří na hledání, ne na profil
        bar.addView(chip, 0, android.widget.FrameLayout.LayoutParams(
            android.view.ViewGroup.LayoutParams.WRAP_CONTENT, android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
            android.view.Gravity.START or android.view.Gravity.CENTER_VERTICAL,
        ).apply { marginStart = dp(64) })
        val orb = titleViewAdapter?.searchAffordanceView
        orb?.setOnKeyListener { _, code, event ->
            val right = code == android.view.KeyEvent.KEYCODE_DPAD_RIGHT && chip.visibility == android.view.View.VISIBLE
            if (right && event.action == android.view.KeyEvent.ACTION_DOWN) chip.requestFocus()
            right
        }
        chip.setOnKeyListener { _, code, event ->
            val left = code == android.view.KeyEvent.KEYCODE_DPAD_LEFT && orb != null
            if (left && event.action == android.view.KeyEvent.ACTION_DOWN) orb?.requestFocus()
            left
        }
        profile = chip
        // Knihovna tlačítko hledání skrývá, když je v nabídce vybraná nižší sekce (nabídka pak vyjede
        // na jeho místo). Profil se řídí jím, jinak by nabídku překrýval.
        bar.viewTreeObserver.addOnPreDrawListener(profileSync)
    }

    private val profileSync = android.view.ViewTreeObserver.OnPreDrawListener {
        val chip = profile
        val orb = titleViewAdapter?.searchAffordanceView
        if (chip != null && orb != null && profileNamed) {
            val wanted = if (orb.visibility == android.view.View.VISIBLE) android.view.View.VISIBLE
            else android.view.View.INVISIBLE
            if (chip.visibility != wanted) {
                // profil se zrovna skrývá i s výběrem: ten patří tlačítku hledání, ne ztracenému prvku
                if (wanted != android.view.View.VISIBLE && chip.hasFocus()) orb.requestFocus()
                chip.visibility = wanted
            }
        }
        true
    }
    private var profileNamed = false

    private fun showProfile(data: org.json.JSONObject?) {
        val chip = profile ?: return
        val context = context ?: return
        val name = data?.optString("name").orEmpty()
        if (name.isEmpty()) return
        (chip.getChildAt(1) as? android.widget.TextView)?.text = name
        val id = resources.getIdentifier("avatar_" + data?.optString("avatar"), "drawable", context.packageName)
        (chip.getChildAt(0) as? android.widget.ImageView)?.setImageResource(if (id != 0) id else R.drawable.avatar_clapper)
        chip.contentDescription = "Profil: $name"
        profileNamed = true
        val orb = titleViewAdapter?.searchAffordanceView
        chip.visibility = if (orb == null || orb.visibility == android.view.View.VISIBLE) android.view.View.VISIBLE
        else android.view.View.INVISIBLE
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
    override fun showBackdrop(image: String) {
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
        quickSync()
    }

    /**
     * Po otevření aplikace se na pozadí zkontroluje, jestli na účtu nepřibyla videa, a titulům bez
     * hodnocení a plakátu se doplní. Jak často se smí ptát, hlídá logika; když se něco změnilo,
     * obrazovka se načte znovu, jinak se nestane nic.
     */
    private fun quickSync() {
        Api.call("quick_sync") { result ->
            if (!isAdded) return@call
            val data = result.data ?: return@call
            // nová videa z účtu, nebo se samo doplnilo hodnocení a plakáty: obrazovka se načte znovu
            if (data.optInt("new") <= 0 && !data.optBoolean("refresh")) return@call
            data.optString("toast").takeIf { it.isNotEmpty() }?.let {
                android.widget.Toast.makeText(requireContext(), it, android.widget.Toast.LENGTH_LONG).show()
            }
            load()
        }
    }

    override fun rowsOf(index: Int): JSONArray? = sections.getOrNull(index)?.rows

    override fun heroOf(index: Int): org.json.JSONObject? = sections.getOrNull(index)?.hero

    override fun load() {
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
                if (!loadedOnce) {
                    showMessage(result.error ?: "Nepodařilo se načíst obsah.")
                    handler.removeCallbacks(refresh)
                    handler.postDelayed(refresh, 5000)  // zkusit znovu samo: z prázdné obrazovky není jak
                }
                return@call
            }
            // dokud se stahuje, obnovovat řadu Stažené (stejná data nic nepřekreslí)
            handler.removeCallbacks(refresh)
            if (data.optBoolean("pending")) handler.postDelayed(refresh, 3000)
            val raw = data.toString()
            if (loadedOnce && raw == shown) return@call  // beze změny: výběr i pozice zůstanou
            shown = raw
            showProfile(data.optJSONObject("profile"))
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
                childFragmentManager.fragments.filterIsInstance<SectionFragment>().forEach { it.refresh(reload = true) }
            } else {
                pages.setItems(fresh.mapIndexed { index, section ->
                    PageRow(HeaderItem(index.toLong(), section.title))
                }, null)
                // po spuštění rovnou sekce, ve které něco je (Oblíbené, jinak Rozkoukané, jinak Objevuj)
                val start = data.optInt("start")
                if (!loadedOnce && !started && start in 1 until fresh.size) setSelectedPosition(start, false)
            }
            WatchNext.sync(requireContext())  // řada „Pokračovat ve sledování“ na domovské obrazovce televize
            if (!loadedOnce && !started) {
                started = true
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
                .showStyled()
            load()  // karta v Oblíbených dostane štítek „Nově“
        }
    }

    private fun showMessage(text: String) {
        pages.clear()
        pages.add(ListRow(HeaderItem(0, text), ArrayObjectAdapter(CardPresenter())))
    }
}

/**
 * Stránka s řadami karet: sekce hlavní obrazovky, nebo obrazovka složky. Data si bere od hostitele.
 *
 * Řada s `call` (právě populární tituly) se donačítá až tady, aby hlavní obrazovka nečekala na síť:
 * do té doby ukazuje naposledy načtené karty, jinak obrysy; když se načíst nepodaří, nabídne to zkusit znovu.
 */
class SectionFragment : RowsSupportFragment() {
    companion object {
        private const val INDEX = "index"
        private const val HERO = "\u0000hero"  // označení řady s úvodním panelem

        /** Naposledy načtené karty donačítaných řad: při návratu do sekce jsou vidět hned. */
        private val remembered = HashMap<String, List<Card>>()

        // Stránka sekce vzniká znovu při každém výběru v nabídce. Stav načítání je proto společný:
        // rozpracovaný dotaz se nespouští podruhé (jen se na něj počká) a čerstvý výsledek se neptá znovu.
        private val fetchedAt = HashMap<String, Long>()
        private val failed = HashSet<String>()
        private val waiting = HashMap<String, MutableList<() -> Unit>>()
        private const val FRESH_MS = 30_000L

        fun create(index: Int) = SectionFragment().apply { arguments = Bundle().apply { putInt(INDEX, index) } }
    }

    private var forced = false  // po příkazu uživatele: donačítané řady načíst znovu, i když jsou čerstvé
    private val later = android.os.Handler(android.os.Looper.getMainLooper())

    override fun onDestroy() {
        later.removeCallbacksAndMessages(null)
        super.onDestroy()
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

    private fun host(): RowsHost? = parentFragment as? RowsHost ?: activity as? RowsHost

    /** Po příkazu z nabídky karty (oblíbené, stažení): obsah hostitele i donačítané řady znovu. */
    private fun changed() {
        forced = true
        host()?.load()
        refresh(reload = true)
    }

    private fun fetch(key: String, call: String, params: org.json.JSONObject) {
        val done = { if (isAdded) refresh() }
        waiting[key]?.let {
            it.add(done)  // tentýž dotaz už běží (jiná stránka téže sekce): jen počkat na výsledek
            return
        }
        waiting[key] = mutableListOf(done)
        Api.call(call, params) { result ->
            val cards = Card.list(result.data?.optJSONArray("items"))
            val seconds = result.data?.optInt("refresh") ?: 0
            fetchedAt[key] = android.os.SystemClock.elapsedRealtime()
            if (cards.isNotEmpty()) {
                failed.remove(key)
                remembered[key] = cards
            } else if (result.data?.optBoolean("hide") == true) {
                // řada, která nemá co ukázat (zatím nic „nově na Přehraj.to“): není to chyba, jen se neukáže
                failed.remove(key)
                remembered.remove(key)
            } else if (seconds > 0) {
                // tituly řady se teprve ověřují (ukazují se jen ty, které opravdu jsou k mání):
                // řada zatím zůstává jako obrysy karet, není to chyba
                failed.remove(key)
            } else {
                failed.add(key)
            }
            waiting.remove(key)?.forEach { it() }
            // řada, jejíž tituly se ještě ověřují na pozadí, si za chvíli řekne o čerstvý stav
            if (seconds > 0) later.postDelayed({ if (isAdded) fetch(key, call, params) }, seconds * 1000L)
        }
    }

    override fun onViewCreated(view: android.view.View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        // na vlastní obrazovce (složka) začínají řady pod jejím názvem; v sekci je zarovnává hlavní obrazovka
        host()?.topOffset()?.takeIf { it > 0 }?.let { setAlignment(it) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        adapter = rows
        // Stránka sekce si kliknutí a výběr obsluhuje sama: hlavní obrazovka své posluchače
        // předává jen obyčejným řadám, ne stránkám.
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { changed() } }
        }
        onItemViewSelectedListener = OnItemViewSelectedListener { _, item, _, row ->
            host()?.showBackdrop((item as? Card)?.image.orEmpty())
            host()?.onRowSelected(rows.indexOf(row))
            (item as? Card)?.let { card -> context?.let { Hints.cardSelected(it, card) } }
        }
        refresh()
    }

    fun refresh(reload: Boolean = false) {
        val list = host()?.rowsOf(requireArguments().getInt(INDEX)) ?: return
        val force = forced
        forced = false
        val now = android.os.SystemClock.elapsedRealtime()
        val names = ArrayList<String>()
        val items = ArrayList<List<Card>>()
        val tiles = ArrayList<Boolean>()  // mřížka velkých dlaždic
        val posters = ArrayList<Boolean>()  // plakáty na výšku
        host()?.heroOf(requireArguments().getInt(INDEX))?.let { hero ->
            // úvodní panel je první řada; nápis tlačítka nese karta ve štítku
            names.add(HERO)
            tiles.add(false)
            posters.add(false)
            items.add(listOf(Card.from(org.json.JSONObject(hero.toString())
                .put("badges", JSONArray().put(hero.optString("label"))).put("wide", true))))
        }
        for (index in 0 until list.length()) {
            val row = list.optJSONObject(index) ?: continue
            val call = row.optString("call")
            if (call.isEmpty()) {
                names.add(row.optString("title"))
                tiles.add(row.optBoolean("tiles"))
                val cards = Card.list(row.optJSONArray("items"))
                items.add(cards)
                posters.add(CardPresenter.posterShape(cards))
                continue
            }
            // donačítaná řada: tvar karet je daný předem, aby se řada při načtení nepřestavovala
            val params = row.optJSONObject("params") ?: org.json.JSONObject()
            val key = "$call|$params"
            // načíst: poprvé, po příkazu uživatele, a při změně obsahu jen když je výsledek starší
            val age = fetchedAt[key]?.let { now - it }
            if (age == null || force || (reload && age > FRESH_MS)) fetch(key, call, params)
            val known = remembered[key]
            // nepovinná řada se ukáže, až když má co nabídnout (jinak by probliklo prázdné místo)
            if (row.optBoolean("optional") && known == null) continue
            names.add(row.optString("title"))
            tiles.add(row.optBoolean("tiles"))
            items.add(when {
                known != null -> known
                key in failed && key !in waiting -> listOf(Card.from(org.json.JSONObject()
                    .put("title", "Nepodařilo se načíst").put("subtitle", "Zkusit znovu").put("icon", "sync")
                    .put("target", org.json.JSONObject().put("type", "refresh"))))
                else -> Card.skeletons(6)
            })
            posters.add(row.optBoolean("poster"))
        }
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
                    Navigator.menu(requireActivity(), card) { changed() }
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
