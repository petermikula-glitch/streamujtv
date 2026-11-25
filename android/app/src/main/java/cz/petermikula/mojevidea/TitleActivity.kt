package cz.petermikula.mojevidea

import android.os.Bundle
import android.view.ContextThemeWrapper
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.RowsSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.HeaderItem
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ListRowPresenter
import androidx.leanback.widget.OnItemViewClickedListener
import com.bumptech.glide.Glide
import org.json.JSONObject

/**
 * Stránka titulu z Objevuj: obrázek na pozadí, plakát, údaje, tlačítka (přehrát nejlepší verzi,
 * oblíbené, hledat ručně) a dole řady karet – verze nalezené na Přehraj.to, nebo řady seriálu.
 * Obsah dodává logika v Pythonu: {"detail", "actions", "rows"}.
 */
class TitleActivity : FragmentActivity() {
    /** Podržené Zpět: rovnou do hlavní nabídky (viz [Home]). */
    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean =
        Home.dispatch(this, event) || super.dispatchKeyEvent(event)

    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private lateinit var actions: FlowLayout
    private lateinit var spinner: View
    private lateinit var plot: TextView
    private lateinit var file: TextView
    private var call = ""
    private var params = JSONObject()
    private var shown = ""
    private var loadedOnce = false
    private var meta = ""
    private var csfd = ""
    private var expanded = false
    private var fitted = ""   // pro jaké rozměry už je popis zkrácený
    private var autoRetries = 0   // kolikrát už stránka sama zopakovala nepovedené hledání
    private var rowTitles = emptyList<String>()
    private var rowShapes = emptyList<Boolean>()
    private var results: RowsSupportFragment? = null
    private var played = false  // z této stránky se právě něco přehrávalo

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_title)
        actions = findViewById(R.id.title_actions)
        spinner = findViewById(R.id.title_spinner)
        plot = findViewById(R.id.title_plot)
        file = findViewById(R.id.title_file)
        // popis dostane jen tolik řádků, kolik nad řadami karet zbývá: název, soubor a tlačítka mají přednost
        window.decorView.viewTreeObserver.addOnGlobalLayoutListener { fitPlot() }
        call = intent.getStringExtra(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}")
        findViewById<TextView>(R.id.title_name).text = intent.getStringExtra(Navigator.EXTRA_TITLE).orEmpty()
        val results = RowsSupportFragment()
        this.results = results
        results.adapter = rows
        // karty vybrané řady vždy stejně hluboko pod horním okrajem: nad nimi se vejde celý nadpis řady
        // (s větším písmem ho horní okraj uřezával)
        results.setAlignment((ROW_TOP * resources.displayMetrics.density).toInt())
        results.onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let {
                if (it.target.type == "play") played = true
                Navigator.open(this, it) { load() }
            }
        }
        supportFragmentManager.beginTransaction().replace(R.id.title_rows, results).commit()
        val area = findViewById<View>(R.id.title_rows)
        window.decorView.viewTreeObserver.addOnGlobalFocusChangeListener { _, focused ->
            // Než se stránka poprvé naplní, výběr skáče, jak přibývají tlačítka a řady: rozložení se
            // podle něj nemění (dřív se stránka krátce ukázala v rozložení pro procházení karet).
            if (!loadedOnce) return@addOnGlobalFocusChangeListener
            var view: View? = focused
            while (view != null && view !== area) view = view.parent as? View
            expand(view === area)
        }
    }

    /** Výběr v řadách: plakát a popis ustoupí, řady dostanou víc místa. Zpět u tlačítek se vrátí. */
    private fun expand(rowsFocused: Boolean) {
        if (rowsFocused == expanded) return
        expanded = rowsFocused
        val hidden = if (rowsFocused) View.GONE else View.VISIBLE
        findViewById<View>(R.id.title_poster).visibility = hidden
        plot.visibility = hidden
        file.visibility = if (rowsFocused || file.text.isEmpty()) View.GONE else View.VISIBLE
        fitted = ""   // po návratu k tlačítkům se místo pro popis spočítá znovu
        val area = findViewById<View>(R.id.title_rows)
        area.layoutParams = area.layoutParams.apply {
            height = ((if (rowsFocused) ROWS_EXPANDED else ROWS_HEIGHT) * resources.displayMetrics.density).toInt()
        }
    }

    override fun onResume() {
        super.onResume()
        load()  // po návratu z přehrávače se mění rozkoukanost i naposledy zvolená verze
    }

    private fun load() {
        if (!loadedOnce) spinner.visibility = View.VISIBLE
        Api.call(call, params) { result ->
            if (isFinishing || isDestroyed) return@call
            spinner.visibility = View.GONE
            val data = result.data
            if (data == null) {
                if (!loadedOnce) showFailure(result.error ?: "Stránku titulu se nepodařilo načíst.")
                return@call
            }
            // Hledání na Přehraj.to se nezdařilo (krátký výpadek, omezení dotazů): stránka to za chvíli
            // zkusí sama, nejvýš dvakrát; tlačítko Zkusit znovu funguje dál.
            if (data.optBoolean("retry") && autoRetries < AUTO_RETRIES) {
                autoRetries++
                spinner.visibility = View.VISIBLE
                window.decorView.postDelayed({
                    if (!isFinishing && !isDestroyed) load()
                }, RETRY_AFTER_MS * autoRetries)
            }
            val raw = data.toString()
            if (raw == shown) return@call  // beze změny: výběr zůstane na místě
            shown = raw
            bind(data)
            if (loadedOnce && played && expanded) {
                // po dokoukaném dílu: výběr v řadách se přesune na díl, kterým se pokračuje
                data.optJSONObject("focus")?.let { focus ->
                    val row = focus.optInt("row", -1)
                    if (row in 0 until rows.size()) {
                        results?.setSelectedPosition(row, true,
                            ListRowPresenter.SelectItemViewHolderTask(focus.optInt("item")))
                    }
                }
            }
            played = false
            if (!loadedOnce) {
                loadedOnce = true
                // řady si po naplnění umí vzít výběr samy: na první stránce má být na tlačítku Přehrát
                window.decorView.post {
                    if (isFinishing || isDestroyed || actions.childCount == 0) return@post
                    if (currentFocus?.let { inside(it, findViewById(R.id.title_rows)) } == true) {
                        actions.getChildAt(0)?.requestFocus()
                    }
                    expand(false)
                }
            }
        }
    }

    private fun inside(view: View, area: View): Boolean {
        var current: View? = view
        while (current != null && current !== area) current = current.parent as? View
        return current === area
    }

    /** Stránka se vůbec nenačetla: chyba musí být vidět (popis se při výběru v řadách skrývá) a jít zopakovat. */
    private fun showFailure(text: String) {
        expand(false)
        plot.text = text
        actions.removeAllViews()
        val button = Button(ContextThemeWrapper(this, R.style.TitleButton), null, 0).apply {
            this.text = "Zkusit znovu"
            setOnClickListener { load() }
        }
        actions.addView(button)
        button.requestFocus()
    }

    private fun bind(data: JSONObject) {
        val detail = data.optJSONObject("detail") ?: JSONObject()
        detail.optString("title").takeIf { it.isNotEmpty() }?.let { findViewById<TextView>(R.id.title_name).text = it }
        meta = detail.optString("meta")
        showMeta()
        if (!loadedOnce) {
            // hodnocení ČSFD se ptá zvlášť, aby stránku nezdržovalo (ČSFD dotazy často odmítá)
            Api.call("title_csfd", params) { result ->
                if (isFinishing || isDestroyed) return@call
                csfd = result.data?.optString("text").orEmpty()
                showMeta()
            }
        }
        plot.text = detail.optString("plot")
        file.text = detail.optString("file")
        file.visibility = if (file.text.isEmpty()) View.GONE else View.VISIBLE
        val glide = Glide.with(applicationContext)
        detail.optString("poster").takeIf { it.isNotEmpty() }?.let {
            glide.load(it).timeout(15_000).centerCrop().transition(com.bumptech.glide.load.resource.drawable.DrawableTransitionOptions.withCrossFade(160))
                .into(findViewById<ImageView>(R.id.title_poster))
        }
        // obrázek na pozadí jen jako šedá lavírovaná tuš pod papírem, ne barevná plocha
        findViewById<ImageView>(R.id.title_backdrop).colorFilter = android.graphics.ColorMatrixColorFilter(
            android.graphics.ColorMatrix().apply { setSaturation(0f) })
        detail.optString("backdrop").takeIf { it.isNotEmpty() }?.let {
            glide.load(it).timeout(15_000).centerCrop().transition(com.bumptech.glide.load.resource.drawable.DrawableTransitionOptions.withCrossFade(300))
                .into(findViewById<ImageView>(R.id.title_backdrop))
        }

        // tlačítka: zachovat, které bylo vybrané (po přidání do oblíbených se stránka obnovuje)
        val focused = (0 until actions.childCount).firstOrNull { actions.getChildAt(it).isFocused }
        actions.removeAllViews()
        val list = data.optJSONArray("actions")
        for (index in 0 until (list?.length() ?: 0)) {
            val action = list!!.optJSONObject(index) ?: continue
            val target = Target.from(action.optJSONObject("target"))
            val subtitle = action.optString("subtitle")
            val button = Button(ContextThemeWrapper(this, R.style.TitleButton), null, 0).apply {
                text = if (subtitle.isEmpty()) action.optString("title") else "${action.optString("title")}  ·  $subtitle"
                setOnClickListener {
                    val menu = action.optJSONArray("menu")
                    if (menu != null && menu.length() > 0) showMenu(action.optString("title"), menu)
                    else Navigator.open(this@TitleActivity, target, action.optString("title")) { load() }
                }
            }
            actions.addView(button)
        }
        if (!loadedOnce || focused != null) {
            actions.getChildAt((focused ?: 0).coerceAtMost(actions.childCount - 1))?.requestFocus()
        }

        val names = ArrayList<String>()
        val found = ArrayList<List<Card>>()
        val items = data.optJSONArray("rows")
        for (index in 0 until (items?.length() ?: 0)) {
            val row = items!!.optJSONObject(index) ?: continue
            names.add(row.optString("title"))
            found.add(Card.list(row.optJSONArray("items")))
        }
        val shapes = found.map { CardPresenter.posterShape(it) }
        if (names == rowTitles && shapes == rowShapes && rows.size() == names.size) {
            // stejné řady (po návratu z přehrávače): vyměnit jen změněné karty, výběr zůstane na svém dílu
            for (index in names.indices) {
                ((rows.get(index) as ListRow).adapter as ArrayObjectAdapter).setItems(found[index], Card.DIFF)
            }
        } else {
            rows.setItems(names.indices.map { index ->
                val cards = ArrayObjectAdapter(CardPresenter(shapes[index]) { card ->
                    Navigator.menu(this, card) { load() }
                })
                cards.addAll(0, found[index])
                ListRow(HeaderItem(index.toLong(), names[index]), cards)
            }, null)
        }
        rowTitles = names
        rowShapes = shapes
    }

    /**
     * Zkrátí popis tak, aby se údaje i tlačítka vešly nad řady karet. Tlačítka bývají ve dvou řádcích
     * a přibyl řádek se souborem; bez toho by spodní tlačítka zmizela pod řadami.
     */
    private fun fitPlot() {
        if (expanded) return
        val column = findViewById<View>(R.id.title_column)
        val pane = column.parent as View
        val free = pane.height - pane.paddingTop - pane.paddingBottom
        if (free <= 0 || column.width <= 0) return
        val state = listOf(free, column.width, actions.rowCount, file.text, plot.text,
            findViewById<TextView>(R.id.title_name).lineCount, findViewById<TextView>(R.id.title_meta).text).joinToString("|")
        if (state == fitted) return
        fitted = state
        val width = View.MeasureSpec.makeMeasureSpec(column.width, View.MeasureSpec.EXACTLY)
        val any = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        var lines = if (plot.text.isEmpty()) 0 else PLOT_LINES
        while (true) {
            plot.visibility = if (lines == 0) View.GONE else View.VISIBLE
            if (lines > 0) plot.maxLines = lines
            column.measure(width, any)
            if (lines == 0 || column.measuredHeight <= free) break
            lines--
        }
        column.requestLayout()
    }

    /** Tlačítko s nabídkou („Další“): méně používané volby, aby tlačítek na stránce nebylo moc. */
    private fun showMenu(title: String, menu: org.json.JSONArray) {
        val items = (0 until menu.length()).mapNotNull { menu.optJSONObject(it) }
        android.app.AlertDialog.Builder(this)
            .setTitle(title)
            .setAdapter(Dialogs.items(this, items.map { it.optString("title") })) { _, which ->
                val chosen = items[which]
                Navigator.open(this, Target.from(chosen.optJSONObject("target")), chosen.optString("title")) { load() }
            }
            .showStyled()
    }

    private fun showMeta() {
        findViewById<TextView>(R.id.title_meta).text = listOf(meta, csfd).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    override fun onDestroy() {
        Glide.with(applicationContext).clear(findViewById<ImageView>(R.id.title_poster))
        Glide.with(applicationContext).clear(findViewById<ImageView>(R.id.title_backdrop))
        super.onDestroy()
    }

    private companion object {
        const val AUTO_RETRIES = 2      // tolikrát nejvýš stránka sama zopakuje nepovedené hledání
        const val RETRY_AFTER_MS = 5000L   // první opakování po této době, druhé po dvojnásobku
        const val PLOT_LINES = 4        // tolik řádků popisu nejvýš
        const val ROWS_HEIGHT = 250     // dp: řady karet pod údaji o titulu
        const val ROWS_EXPANDED = 390   // dp: řady, když je v nich výběr (plakát a popis ustoupí)
        const val ROW_TOP = 48          // dp: horní okraj karet pod horním okrajem řad
    }
}
