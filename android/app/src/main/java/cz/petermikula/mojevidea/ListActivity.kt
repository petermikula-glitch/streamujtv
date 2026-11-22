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
import androidx.leanback.widget.VerticalGridView
import org.json.JSONObject

class ListActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) {
            val fragment = ListFragment().apply { arguments = intent.extras }
            supportFragmentManager.beginTransaction().replace(android.R.id.content, fragment).commit()
        }
    }
}

/** Mřížka karet jednoho výpisu; další stránky se dotahují, jak se uživatel blíží ke konci. */
class ListFragment : VerticalGridSupportFragment() {
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
    private var backdrop: Backdrop? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        call = arguments?.getString(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(arguments?.getString(Navigator.EXTRA_PARAMS) ?: "{}")
        title = arguments?.getString(Navigator.EXTRA_TITLE).orEmpty()
        // bez ztmavení nevybraných karet: obrázky a názvy zůstanou čitelné v celé mřížce
        gridPresenter = VerticalGridPresenter(FocusHighlight.ZOOM_FACTOR_MEDIUM, false).apply { numberOfColumns = 4 }
        adapter = cards
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { reload() } }
        }
        setOnItemViewSelectedListener(OnItemViewSelectedListener { _, item, _, _ ->
            val index = cards.indexOf(item)
            if (index >= cards.size() - 10) loadNext()
            backdrop?.show((item as? Card)?.image.orEmpty())
        })
        reload()
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
        if (!quiet && cards.size() == 0) showMessage("Načítám…")
        Api.call(call, params) { result ->
            loading = false
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
                // prázdná obrazovka s chybou musí říct, co se stalo; přes plný výpis stačí krátké upozornění
                if (cards.size() == 0) {
                    showMessage(
                        (result.error ?: "Výpis se nepodařilo načíst.") + "\n\nZkuste to znovu tlačítkem Zpět.",
                        if (result.offline) EmptyArt.OFFLINE else "",
                    )
                } else if (!quiet) {
                    Toast.makeText(requireContext(), result.error, Toast.LENGTH_LONG).show()
                }
                return@call
            }
            data.optString("title").takeIf { it.isNotEmpty() && title.isNullOrEmpty() }?.let { title = it }
            val list = Card.list(data.optJSONArray("items"))
            data.optString("empty").takeIf { it.isNotEmpty() }?.let { emptyText = it }
            data.optString("empty_art").takeIf { it.isNotEmpty() }?.let { emptyArt = it }
            // výpis, který se mění sám (stahování), si říká o obnovení po pár sekundách
            handler.removeCallbacks(refresh)
            val seconds = data.optInt("refresh")
            if (seconds > 0) handler.postDelayed(refresh, seconds * 1000L)
            applyShape(list)
            cards.setItems(list, Card.DIFF)
            next = data.optJSONObject("next")
            showMessage(if (cards.size() == 0) emptyText else "", emptyArt)
        }
    }

    private var message: TextView? = null

    /** Text uprostřed obrazovky (prázdný výpis, chyba); prázdný text ho skryje. */
    private fun showMessage(text: String, art: String = "") {
        val root = view as? ViewGroup ?: return
        val label = message ?: TextView(requireContext()).apply {
            gravity = Gravity.CENTER
            textSize = 20f
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            val side = (96 * resources.displayMetrics.density).toInt()
            setPadding(side, 0, side, 0)
            root.addView(this, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
            message = this
        }
        label.text = text
        EmptyArt.show(label, if (text.isEmpty()) "" else art)
        label.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        super.onPause()
    }

    override fun onDestroyView() {
        message = null
        super.onDestroyView()
    }

    /** Plakáty: 5 sloupců; náhledy na šířku: 4 sloupce. Určí se podle první stránky. */
    private fun applyShape(list: List<Card>) {
        val poster = CardPresenter.posterShape(list)
        val columns = if (poster) 5 else 4
        if (presenter.poster == poster && gridPresenter.numberOfColumns == columns) return
        if (cards.size() > 0) cards.clear()  // karty starého tvaru se musí vytvořit znovu
        presenter.poster = poster
        gridPresenter.numberOfColumns = columns
        view?.findViewById<VerticalGridView>(androidx.leanback.R.id.browse_grid)?.setNumColumns(columns)
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
