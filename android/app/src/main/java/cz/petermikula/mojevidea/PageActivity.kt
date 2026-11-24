package cz.petermikula.mojevidea

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.fragment.app.FragmentActivity
import org.json.JSONArray
import org.json.JSONObject

/** Odkud si stránka s řadami karet bere obsah: hlavní obrazovka (sekce), nebo obrazovka složky. */
interface RowsHost {
    /** Řady sekce pro její stránku. */
    fun rowsOf(index: Int): JSONArray?

    /** Úvodní panel sekce (naposledy rozkoukané video), pokud nějaký má. */
    fun heroOf(index: Int): JSONObject?

    /** Obsah se změnil (příkaz z nabídky karty): načíst znovu. */
    fun load()

    /** Pozadí podle vybrané karty. */
    fun showBackdrop(image: String)

    /** Kolik místa nahoře stránce zabírá název obrazovky (0 = o zarovnání se stará hostitel sám). */
    fun topOffset(): Int = 0

    /** Vybraná řada stránky (od nuly). */
    fun onRowSelected(index: Int) = Unit
}

/**
 * Obrazovka složky ze sekce Objevuj (Filmy, Seriály): stejné řady dlaždic a karet jako sekce hlavní
 * obrazovky, jen na vlastní obrazovce. Obsah dodává logika v Pythonu (akce z cíle karty, `rows`).
 */
class PageActivity : FragmentActivity(), RowsHost {
    /** Podržené Zpět: rovnou do hlavní nabídky (viz [Home]). */
    override fun onKeyLongPress(keyCode: Int, event: android.view.KeyEvent?): Boolean =
        Home.longPress(this, keyCode) || super.onKeyLongPress(keyCode, event)

    private var rows: JSONArray? = null
    private var shown = ""
    private var call = ""
    private var params = JSONObject()
    private var backdrop: Backdrop? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_page)
        call = intent.getStringExtra(Navigator.EXTRA_CALL).orEmpty()
        params = JSONObject(intent.getStringExtra(Navigator.EXTRA_PARAMS) ?: "{}")
        findViewById<TextView>(R.id.page_title).text = intent.getStringExtra(Navigator.EXTRA_TITLE).orEmpty()
        backdrop = Backdrop(this)
        // po otočení obrazovky by obnovená stránka neměla data: vždy se staví znovu z načteného obsahu
        supportFragmentManager.findFragmentById(R.id.page_rows)?.let {
            supportFragmentManager.beginTransaction().remove(it).commitNowAllowingStateLoss()
        }
    }

    override fun onResume() {
        super.onResume()
        load()  // po návratu z výpisu nebo přehrávače se mění počty stažených i nově přidané
    }

    override fun onDestroy() {
        backdrop?.release()
        super.onDestroy()
    }

    override fun rowsOf(index: Int): JSONArray? = rows

    override fun heroOf(index: Int): JSONObject? = null

    override fun showBackdrop(image: String) {
        backdrop?.show(image)
    }

    override fun topOffset(): Int = (104 * resources.displayMetrics.density).toInt()

    /** Název obrazovky je vidět jen nad první řadou; níž by překrýval dlaždice, které pod něj vyjedou. */
    override fun onRowSelected(index: Int) {
        findViewById<TextView>(R.id.page_title).animate().alpha(if (index <= 0) 1f else 0f).setDuration(150).start()
    }

    override fun load() {
        Api.call(call, params) { result ->
            if (isFinishing || isDestroyed) return@call
            val message = findViewById<TextView>(R.id.page_message)
            val list = result.data?.optJSONArray("rows")
            if (list == null) {
                if (rows == null) {
                    message.text = result.error ?: "Obsah se nepodařilo načíst."
                    message.visibility = View.VISIBLE
                }
                return@call
            }
            message.visibility = View.GONE
            result.data.optString("title").takeIf { it.isNotEmpty() }?.let {
                findViewById<TextView>(R.id.page_title).text = it
            }
            val raw = list.toString()
            if (raw == shown) return@call  // beze změny: výběr i pozice zůstanou
            shown = raw
            rows = list
            val page = supportFragmentManager.findFragmentById(R.id.page_rows) as? SectionFragment
            if (page == null) {
                supportFragmentManager.beginTransaction()
                    .replace(R.id.page_rows, SectionFragment.create(0))
                    .commitAllowingStateLoss()
            } else {
                page.refresh(reload = true)
            }
        }
    }
}
