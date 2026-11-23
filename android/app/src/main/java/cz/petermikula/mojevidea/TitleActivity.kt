package cz.petermikula.mojevidea

import android.os.Bundle
import android.view.ContextThemeWrapper
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
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
    private val rows = ArrayObjectAdapter(ListRowPresenter())
    private lateinit var actions: LinearLayout
    private lateinit var spinner: View
    private lateinit var plot: TextView
    private var call = ""
    private var params = JSONObject()
    private var shown = ""
    private var loadedOnce = false
    private var meta = ""
    private var csfd = ""
    private var expanded = false
    private var rowTitles = emptyList<String>()
    private var rowShapes = emptyList<Boolean>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_title)
        actions = findViewById(R.id.title_actions)
        spinner = findViewById(R.id.title_spinner)
        plot = findViewById(R.id.title_plot)
        call = intent.getStringExtra(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}")
        findViewById<TextView>(R.id.title_name).text = intent.getStringExtra(Navigator.EXTRA_TITLE).orEmpty()
        val results = RowsSupportFragment()
        results.adapter = rows
        results.onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(this, it) { load() } }
        }
        supportFragmentManager.beginTransaction().replace(R.id.title_rows, results).commit()
        val area = findViewById<View>(R.id.title_rows)
        window.decorView.viewTreeObserver.addOnGlobalFocusChangeListener { _, focused ->
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
        val area = findViewById<View>(R.id.title_rows)
        area.layoutParams = area.layoutParams.apply {
            height = ((if (rowsFocused) 390 else 250) * resources.displayMetrics.density).toInt()
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
            val raw = data.toString()
            if (raw == shown) return@call  // beze změny: výběr zůstane na místě
            shown = raw
            bind(data)
            loadedOnce = true
        }
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
        val glide = Glide.with(applicationContext)
        detail.optString("poster").takeIf { it.isNotEmpty() }?.let {
            glide.load(it).timeout(15_000).centerCrop().transition(com.bumptech.glide.load.resource.drawable.DrawableTransitionOptions.withCrossFade(160))
                .into(findViewById<ImageView>(R.id.title_poster))
        }
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
                    Navigator.open(this@TitleActivity, target, action.optString("title")) { load() }
                }
            }
            val place = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            place.marginEnd = (12 * resources.displayMetrics.density).toInt()
            actions.addView(button, place)
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

    private fun showMeta() {
        findViewById<TextView>(R.id.title_meta).text = listOf(meta, csfd).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    override fun onDestroy() {
        Glide.with(applicationContext).clear(findViewById<ImageView>(R.id.title_poster))
        Glide.with(applicationContext).clear(findViewById<ImageView>(R.id.title_backdrop))
        super.onDestroy()
    }
}
