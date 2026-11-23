package cz.petermikula.mojevidea

import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.VerticalGridSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.FocusHighlight
import androidx.leanback.widget.OnItemViewClickedListener
import androidx.leanback.widget.OnItemViewSelectedListener
import androidx.leanback.widget.VerticalGridPresenter
import org.json.JSONObject

class ListActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) show(intent.getBooleanExtra(Navigator.EXTRA_POSTER, false), first = null)
    }

    /**
     * Mřížka výpisu v daném tvaru. Počet sloupců se u hotové mřížky měnit nedá (knihovna na tom padá),
     * proto se pro jiný tvar karet vytvoří mřížka nová a už načtená první stránka se jí předá.
     */
    fun show(poster: Boolean, first: JSONObject?, params: JSONObject? = null) {
        if (isFinishing || isDestroyed) return
        val fragment = ListFragment().apply {
            arguments = Bundle(intent.extras ?: Bundle()).apply {
                putBoolean(ListFragment.ARG_POSTER, poster)
                // výpis se změněným filtrem: nová mřížka pokračuje s jeho parametry
                if (params != null) putString(Navigator.EXTRA_PARAMS, params.toString())
            }
            initial = first
        }
        supportFragmentManager.beginTransaction().replace(android.R.id.content, fragment).commitAllowingStateLoss()
    }
}

/** Mřížka karet jednoho výpisu; další stránky se dotahují, jak se uživatel blíží ke konci. */
class ListFragment : VerticalGridSupportFragment() {
    companion object {
        const val ARG_POSTER = "poster"
    }

    /** První stránka načtená předchozí mřížkou jiného tvaru (viz [ListActivity.show]). */
    var initial: JSONObject? = null
    private val presenter = CardPresenter(false) { card -> Navigator.menu(requireActivity(), card) { reload() } }
    private var emptyText = "Nic tu není."
    private var emptyArt = EmptyArt.NOTHING
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private val refresh = Runnable { if (isResumed) reload(quiet = true) }
    private val cards = ArrayObjectAdapter(presenter)
    private var call = ""
    private var params = JSONObject()
    private var next: JSONObject? = null
    private var loading = false
    private var loaded = false  // už se zobrazil skutečný obsah (ne jen zástupné karty)
    // Číslo výpisu: odpověď, která patří staršímu (jiný filtr, nové načtení), se zahodí.
    private var generation = 0
    private var reloadWanted = false  // obnovení, které přišlo během načítání další stránky
    private var emptyBatches = 0  // kolik dávek po sobě nepřineslo žádný titul (výpis „jen dostupné“)
    private var pagesLoaded = 0   // kolik stránek výpisu je načteno (obnova jich načte zase tolik)
    private var status: TextView? = null  // řádek dole: co se právě děje na pozadí (ověřování dostupnosti)
    private var backdrop: Backdrop? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        call = arguments?.getString(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(arguments?.getString(Navigator.EXTRA_PARAMS) ?: "{}")
        title = arguments?.getString(Navigator.EXTRA_TITLE).orEmpty()
        // plakáty: 5 sloupců; náhledy na šířku: 4 sloupce – dané předem, za běhu se nemění
        presenter.poster = arguments?.getBoolean(ARG_POSTER) ?: false
        // bez ztmavení nevybraných karet: obrázky a názvy zůstanou čitelné v celé mřížce
        gridPresenter = VerticalGridPresenter(FocusHighlight.ZOOM_FACTOR_MEDIUM, false).apply {
            numberOfColumns = if (presenter.poster) 5 else 4
        }
        adapter = cards
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            val card = item as? Card ?: return@OnItemViewClickedListener
            if (card.target.type == "filter") showFilters(card.target)
            else Navigator.open(requireActivity(), card) { reload() }
        }
        setOnItemViewSelectedListener(OnItemViewSelectedListener { _, item, _, _ ->
            val index = cards.indexOf(item)
            if (index >= cards.size() - 10) loadNext()
            backdrop?.show((item as? Card)?.image.orEmpty())
            (item as? Card)?.let { Hints.cardSelected(requireContext(), it) }
        })
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val first = initial
        initial = null
        if (first != null) fill(first) else reload()
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

    private var resumed = false

    override fun onResume() {
        super.onResume()
        // po návratu z přehrávače se mění rozkoukanost a zhlédnutí; výpis se obnoví v rozsahu, v jakém
        // je načtený (dlouhé výpisy ne – znamenalo by to řadu dotazů)
        if (resumed && cards.size() <= 80) reload(quiet = true)
        resumed = true
    }

    private fun reload(quiet: Boolean = false) {
        if (loading) {
            reloadWanted = true  // provede se, až doběhne rozpracované načítání
            return
        }
        loading = true
        val ticket = ++generation
        // Obnovovaný výpis se načte ve stejném rozsahu (tolik stránek, kolik jich uživatel načetl): jinak
        // by po akci z nabídky karty nebo po návratu z přehrávače spadl na první stránku a výběr s ním.
        // Počítají se stránky, ne karty: z výpisu „jen dostupné“ karty při ověřování ubývají, a kdyby
        // se dorovnával jejich počet, tahal by si výpis při každé obnově další a další stránky.
        val wanted = if (loaded) pagesLoaded.coerceAtLeast(1) else 1
        if (!loaded) {
            // místo textu „Načítám…“ obrysy karet v budoucím tvaru
            showMessage("")
            cards.setItems(Card.skeletons(if (presenter.poster) 10 else 8), null)
        }
        Api.call(call, params) { result ->
            if (ticket != generation) return@call
            if (!isAdded) {
                loading = false
                return@call
            }
            val data = result.data
            if (data == null) {
                loading = false
                // prázdná obrazovka s chybou musí říct, co se stalo; přes plný výpis stačí krátké upozornění
                if (!loaded) {
                    cards.clear()
                    showMessage(
                        result.error ?: "Výpis se nepodařilo načíst.",
                        if (result.offline) EmptyArt.OFFLINE else "",
                        retry = true,
                    )
                } else if (!quiet) {
                    Toast.makeText(requireContext(), result.error, Toast.LENGTH_LONG).show()
                }
                return@call
            }
            gather(data, ArrayList(Card.list(data.optJSONArray("items"))), data.optJSONObject("next"), wanted, ticket, 1)
        }
    }

    /** Donačte další stránky obnovovaného výpisu, dokud jich nemá tolik jako předtím, a pak ho zobrazí. */
    private fun gather(first: JSONObject, list: ArrayList<Card>, following: JSONObject?, wanted: Int, ticket: Int, pages: Int) {
        if (following == null || pages >= wanted || pages >= 12) {
            loading = false
            fill(first, list, following, pages)
            return
        }
        Api.call(following.optString("call"), following.optJSONObject("params") ?: JSONObject()) { result ->
            if (ticket != generation) return@call
            if (!isAdded) {
                loading = false
                return@call
            }
            val data = result.data
            if (data == null) {
                loading = false
                fill(first, list, following, pages)  // co se načetlo, to se zobrazí; zbytek se dotáhne posunem
                return@call
            }
            list.addAll(Card.list(data.optJSONArray("items")))
            // další stránka se ještě ověřuje: obnovení a stav platí pro celý výpis
            if (data.optInt("refresh") > 0 && first.optInt("refresh") == 0) {
                first.put("refresh", data.optInt("refresh")).put("status", data.optString("status"))
            }
            gather(first, list, data.optJSONObject("next"), wanted, ticket, pages + 1)
        }
    }

    /** Zobrazí výpis; karty jiného tvaru, než má tahle mřížka, předá mřížce nové. */
    private fun fill(
        data: JSONObject,
        list: List<Card> = Card.list(data.optJSONArray("items")),
        following: JSONObject? = data.optJSONObject("next"),
        pages: Int = 1,
    ) {
        loaded = true
        pagesLoaded = pages
        if (list.isNotEmpty() && CardPresenter.posterShape(list) != presenter.poster) {
            handler.removeCallbacks(refresh)
            (activity as? ListActivity)?.show(!presenter.poster, data, params)
            return
        }
        data.optString("title").takeIf { it.isNotEmpty() && title.isNullOrEmpty() }?.let { title = it }
        data.optString("empty").takeIf { it.isNotEmpty() }?.let { emptyText = it }
        data.optString("empty_art").takeIf { it.isNotEmpty() }?.let { emptyArt = it }
        // výpis, který se mění sám (stahování), si říká o obnovení po pár sekundách
        handler.removeCallbacks(refresh)
        val seconds = data.optInt("refresh")
        if (seconds > 0) handler.postDelayed(refresh, seconds * 1000L)
        val same = cards.size() == list.size &&
            (0 until cards.size()).all { (cards.get(it) as? Card)?.key == list[it].key }
        // Výpis z Objevuj se při ověřování dostupnosti obnovuje sám a karty z něj ubývají: mění se na
        // místě, ať výběr neuskočí na začátek. Jinde se při jiném složení staví znovu.
        val known = (0 until cards.size()).mapNotNull { (cards.get(it) as? Card)?.key }.toHashSet()
        val similar = (call == "discover" || call == "discover_person") && list.isNotEmpty() &&
            list.count { it.key in known } * 2 >= list.size
        cards.setItems(list, if (same || similar) Card.DIFF else null)
        next = following
        emptyBatches = 0
        showStatus(data.optString("status"))
        settle()
    }

    /** Řádek u spodního okraje; prázdný text ho skryje. */
    private fun showStatus(text: String) {
        val root = view as? ViewGroup ?: return
        if (status == null) {
            if (text.isEmpty()) return
            val density = resources.displayMetrics.density
            status = TextView(requireContext()).apply {
                textSize = 14f
                setTextColor(ContextCompat.getColor(context, R.color.chip_text))
                setBackgroundColor(ContextCompat.getColor(context, R.color.chip_bg))
                setPadding((16 * density).toInt(), (6 * density).toInt(), (16 * density).toInt(), (6 * density).toInt())
            }
            root.addView(status, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply { bottomMargin = (20 * density).toInt() })
        }
        status?.text = text
        status?.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
    }

    /** Karty, které jsou obsahem (ne jen ovládáním výpisu, jako Filtr). */
    private fun contentCount(): Int =
        (0 until cards.size()).count { (cards.get(it) as? Card)?.target?.type != "filter" }

    /**
     * Po každém načtení: prázdný výpis to řekne (i když v něm zůstává karta Filtr); výpis „jen dostupné“,
     * kterému dávka nepřinesla nic, hledá dál sám – ale ne donekonečna.
     */
    private fun settle() {
        if (reloadWanted) {
            reloadWanted = false
            reload(quiet = true)
            return
        }
        val empty = contentCount() == 0
        if (empty && next != null && emptyBatches < 5) {
            showMessage("Hledám další tituly, které jsou k mání…")
            loadNext()
        } else {
            showMessage(if (empty) emptyText else "", emptyArt)
        }
    }

    private var message: TextView? = null
    private var again: android.widget.Button? = null

    /** Text uprostřed obrazovky (prázdný výpis, chyba); prázdný text ho skryje. U chyby i „Zkusit znovu“. */
    private fun showMessage(text: String, art: String = "", retry: Boolean = false) {
        val root = view as? ViewGroup ?: return
        if (message == null) {
            val context = requireContext()
            val density = resources.displayMetrics.density
            val box = android.widget.LinearLayout(context).apply {
                orientation = android.widget.LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
            }
            val label = TextView(context).apply {
                gravity = Gravity.CENTER
                textSize = 20f
                setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                val side = (96 * density).toInt()
                setPadding(side, 0, side, 0)
            }
            val button = android.widget.Button(android.view.ContextThemeWrapper(context, R.style.TitleButton), null, 0).apply {
                this.text = "Zkusit znovu"
                setOnClickListener { reload() }
            }
            box.addView(label)
            box.addView(button, android.widget.LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = (20 * density).toInt()
            })
            root.addView(box, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
            message = label
            again = button
        }
        val label = message ?: return
        label.text = text
        EmptyArt.show(label, if (text.isEmpty()) "" else art)
        (label.parent as? View)?.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
        again?.visibility = if (retry && text.isNotEmpty()) View.VISIBLE else View.GONE
        if (retry && text.isNotEmpty()) again?.requestFocus()
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        super.onPause()
    }

    override fun onDestroyView() {
        message = null
        again = null
        status = null
        super.onDestroyView()
    }

    /** Filtr výpisu: nabídka filtrů s jejich hodnotami, po výběru hodnoty se výpis načte znovu. */
    private fun showFilters(target: Target) {
        Api.call(target.call, target.params) { result ->
            if (!isAdded) return@call
            val data = result.data
            val filters = data?.optJSONArray("filters")
            if (data == null || filters == null) {
                Toast.makeText(requireContext(), result.error ?: "Filtr se nepodařilo načíst.", Toast.LENGTH_LONG).show()
                return@call
            }
            val groups = (0 until filters.length()).mapNotNull { filters.optJSONObject(it) }
            val reset = data.optJSONObject("reset")
            val names = groups.map { "${it.optString("title")}: ${it.optString("value")}" } +
                (if (reset != null) listOf("Zrušit filtry") else emptyList())
            android.app.AlertDialog.Builder(requireContext())
                .setTitle(data.optString("title").ifEmpty { "Filtr" })
                .setAdapter(Dialogs.items(requireContext(), names)) { _, which ->
                    if (which >= groups.size) applyFilter(reset ?: return@setAdapter) else chooseFilter(groups[which])
                }
                .showStyled()
        }
    }

    private fun chooseFilter(group: JSONObject) {
        val list = group.optJSONArray("options") ?: return
        val options = (0 until list.length()).mapNotNull { list.optJSONObject(it) }
        android.app.AlertDialog.Builder(requireContext())
            .setTitle(group.optString("title"))
            .setSingleChoiceItems(Dialogs.choices(requireContext(), options.map { it.optString("title") }),
                options.indexOfFirst { it.optBoolean("current") }) { dialog, which ->
                dialog.dismiss()
                options[which].optJSONObject("params")?.let { applyFilter(it) }
            }
            .showStyled()
    }

    /** Stejný výpis s jinými parametry, od začátku. */
    private fun applyFilter(chosen: JSONObject) {
        handler.removeCallbacks(refresh)
        params = JSONObject(chosen.toString())
        next = null
        loaded = false
        loading = false
        reloadWanted = false
        generation++  // rozpracované odpovědi starého filtru se zahodí
        reload()
    }

    private fun loadNext() {
        val following = next ?: return
        if (loading) return
        loading = true
        val ticket = generation
        Api.call(following.optString("call"), following.optJSONObject("params") ?: JSONObject()) { result ->
            if (ticket != generation) return@call  // mezitím jiný filtr nebo nové načtení
            loading = false
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
                if (contentCount() == 0) showMessage(emptyText, emptyArt)
                return@call
            }
            val items = Card.list(data.optJSONArray("items"))
            cards.addAll(cards.size(), items)
            pagesLoaded++
            next = data.optJSONObject("next")
            emptyBatches = if (items.isEmpty()) emptyBatches + 1 else 0
            val seconds = data.optInt("refresh")
            if (seconds > 0) {
                // nově načtená stránka se ještě ověřuje: výpis se za chvíli obnoví celý
                showStatus(data.optString("status"))
                handler.removeCallbacks(refresh)
                handler.postDelayed(refresh, seconds * 1000L)
            }
            settle()
        }
    }
}
