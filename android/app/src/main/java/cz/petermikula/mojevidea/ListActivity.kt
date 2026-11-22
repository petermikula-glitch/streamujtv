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
    fun show(poster: Boolean, first: JSONObject?) {
        if (isFinishing || isDestroyed) return
        val fragment = ListFragment().apply {
            arguments = Bundle(intent.extras ?: Bundle()).apply { putBoolean(ListFragment.ARG_POSTER, poster) }
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
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { reload() } }
        }
        setOnItemViewSelectedListener(OnItemViewSelectedListener { _, item, _, _ ->
            val index = cards.indexOf(item)
            if (index >= cards.size() - 10) loadNext()
            backdrop?.show((item as? Card)?.image.orEmpty())
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
        // po návratu z přehrávače se mění rozkoukanost a zhlédnutí; první stránku stačí načíst znovu
        if (resumed && next == null && cards.size() <= 80) reload(quiet = true)
        resumed = true
    }

    private fun reload(quiet: Boolean = false) {
        if (loading) return
        loading = true
        if (!loaded) {
            // místo textu „Načítám…“ obrysy karet v budoucím tvaru
            showMessage("")
            cards.setItems(Card.skeletons(if (presenter.poster) 10 else 8), null)
        }
        Api.call(call, params) { result ->
            loading = false
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
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
            fill(data)
        }
    }

    /** Zobrazí první stránku výpisu; karty jiného tvaru, než má tahle mřížka, předá mřížce nové. */
    private fun fill(data: JSONObject) {
        val list = Card.list(data.optJSONArray("items"))
        loaded = true
        if (list.isNotEmpty() && CardPresenter.posterShape(list) != presenter.poster) {
            handler.removeCallbacks(refresh)
            (activity as? ListActivity)?.show(!presenter.poster, data)
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
        cards.setItems(list, if (same) Card.DIFF else null)
        next = data.optJSONObject("next")
        showMessage(if (cards.size() == 0) emptyText else "", emptyArt)
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
        super.onDestroyView()
    }

    private fun loadNext() {
        val following = next ?: return
        if (loading) return
        loading = true
        Api.call(following.optString("call"), following.optJSONObject("params") ?: JSONObject()) { result ->
            loading = false
            if (!isAdded) return@call
            val data = result.data ?: return@call
            cards.addAll(cards.size(), Card.list(data.optJSONArray("items")))
            next = data.optJSONObject("next")
        }
    }
}
