package cz.petermikula.mojevidea

import android.graphics.Color
import android.media.MediaDrm
import android.os.Build
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.widget.Button
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.media3.common.C

/**
 * Diagnostika: všechno, co je potřeba vědět, když něco nefunguje, na jedné obrazovce (a jedné fotce).
 * Údaje o zařízení zjišťuje aplikace, stav účtu, připojení a poslední chyby dodává logika v Pythonu.
 */
class DiagnosticsActivity : FragmentActivity() {
    private lateinit var text: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_diagnostics)
        text = findViewById(R.id.diag_text)
        findViewById<Button>(R.id.diag_again).apply {
            setOnClickListener { load() }
            requestFocus()
        }
        load()
    }

    /** Úroveň ochrany Widevine: L1 přehraje chráněné pořady v plné kvalitě, L3 jen omezeně. */
    private fun widevine(): Pair<String, Boolean> = try {
        val drm = MediaDrm(C.WIDEVINE_UUID)
        try {
            val level = drm.getPropertyString("securityLevel")
            "$level, verze ${drm.getPropertyString(MediaDrm.PROPERTY_VERSION)}" to true
        } finally {
            drm.close()
        }
    } catch (error: Exception) {
        "zařízení ho nepodporuje (${error.javaClass.simpleName})" to false
    }

    private fun load() {
        val out = SpannableStringBuilder()
        val secondary = ContextCompat.getColor(this, R.color.text_secondary)

        fun line(label: String, value: String, ok: Boolean?) {
            val mark = when (ok) { true -> "✓ "; false -> "✗ "; else -> "•  " }
            val start = out.length
            out.append(mark)
            val color = when (ok) { true -> Color.rgb(76, 201, 122); false -> Color.rgb(255, 107, 107); else -> secondary }
            out.setSpan(ForegroundColorSpan(color), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            out.append("$label: $value\n")
        }

        line("Aplikace", "${Updater.currentVersion(this)}", null)
        line("Zařízení", "${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})", null)
        val (drm, supported) = widevine()
        line("Widevine", drm, supported)
        line("Úložiště pro stahování", Storage.target(this, "")?.description ?: "USB disk není připojený", null)
        val device = SpannableStringBuilder(out)
        text.text = SpannableStringBuilder(device).append("\nZjišťuji stav účtu a připojení…")
        Api.call("diagnostics") { result ->
            if (isFinishing || isDestroyed) return@call
            val data = result.data
            if (data == null) {
                text.text = SpannableStringBuilder(device).append("\n${result.error}")
                return@call
            }
            val lines = data.optJSONArray("lines")
            for (index in 0 until (lines?.length() ?: 0)) {
                val item = lines!!.optJSONObject(index) ?: continue
                line(item.optString("label"), item.optString("value"), if (item.isNull("ok")) null else item.optBoolean("ok"))
            }
            val errors = data.optJSONArray("errors")
            out.append(if ((errors?.length() ?: 0) == 0) "\nŽádné zaznamenané chyby.\n" else "\nPoslední chyby (nejnovější nahoře):\n")
            for (index in 0 until (errors?.length() ?: 0)) {
                val start = out.length
                out.append("${errors!!.optString(index)}\n")
                out.setSpan(ForegroundColorSpan(secondary), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            text.text = out
        }
    }
}
