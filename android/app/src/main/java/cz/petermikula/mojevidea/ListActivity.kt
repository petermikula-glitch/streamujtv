package cz.petermikula.mojevidea

import android.os.Bundle
import android.widget.Toast
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.VerticalGridSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.OnItemViewClickedListener
import androidx.leanback.widget.OnItemViewSelectedListener
import androidx.leanback.widget.VerticalGridPresenter
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
    private val cards = ArrayObjectAdapter(CardPresenter())
    private var call = ""
    private var params = JSONObject()
    private var next: JSONObject? = null
    private var loading = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        call = arguments?.getString(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(arguments?.getString(Navigator.EXTRA_PARAMS) ?: "{}")
        title = arguments?.getString(Navigator.EXTRA_TITLE).orEmpty()
        gridPresenter = VerticalGridPresenter().apply { numberOfColumns = 5 }
        adapter = cards
        onItemViewClickedListener = OnItemViewClickedListener { _, item, _, _ ->
            (item as? Card)?.let { Navigator.open(requireActivity(), it) { reload() } }
        }
        setOnItemViewSelectedListener(OnItemViewSelectedListener { _, item, _, _ ->
            val index = cards.indexOf(item)
            if (index >= cards.size() - 10) loadNext()
        })
        reload()
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
        Api.call(call, params) { result ->
            loading = false
            if (!isAdded) return@call
            val data = result.data
            if (data == null) {
                if (!quiet) Toast.makeText(requireContext(), result.error, Toast.LENGTH_LONG).show()
                return@call
            }
            data.optString("title").takeIf { it.isNotEmpty() && title.isNullOrEmpty() }?.let { title = it }
            cards.setItems(Card.list(data.optJSONArray("items")), null)
            next = data.optJSONObject("next")
            if (cards.size() == 0 && !quiet) {
                Toast.makeText(requireContext(), "Nic tu není.", Toast.LENGTH_LONG).show()
            }
        }
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
