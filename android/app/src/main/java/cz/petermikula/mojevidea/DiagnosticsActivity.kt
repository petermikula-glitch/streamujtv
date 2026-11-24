package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.graphics.Bitmap
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
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import org.json.JSONArray
import org.json.JSONObject

/**
 * Diagnostika: všechno, co je potřeba vědět, když něco nefunguje, na jedné obrazovce (a jedné fotce).
 * Údaje o zařízení zjišťuje aplikace, stav účtu, připojení a poslední chyby dodává logika v Pythonu.
 */
class DiagnosticsActivity : FragmentActivity() {
    /** Podržené Zpět: rovnou do hlavní nabídky (viz [Home]). */
    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean =
        Home.dispatch(this, event) || super.dispatchKeyEvent(event)

    private companion object {
        const val QR_SIZE = 290   // dp: strana kódu QR na obrazovce
    }

    private lateinit var text: TextView
    private var device = JSONArray()   // řádky, které zná jen aplikace (verze, zařízení…): jdou i do hlášení

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_diagnostics)
        text = findViewById(R.id.diag_text)
        findViewById<Button>(R.id.diag_again).apply {
            setOnClickListener { load() }
            requestFocus()
        }
        findViewById<Button>(R.id.diag_site).setOnClickListener { testSite() }
        findViewById<Button>(R.id.diag_report).setOnClickListener { report() }
        load()
    }

    /** Zkouška Přehraj.to: pár hledání a jedna stránka videa; výsledek v okně a pak i v přehledu. */
    private fun testSite() {
        val waiting = AlertDialog.Builder(this).setTitle("Zkouška Přehraj.to")
            .setMessage("Zkouším hledání a stránku videa…").showStyled()
        Api.call("prehrajto_test") { result ->
            if (isFinishing || isDestroyed) return@call
            waiting.dismiss()
            val lines = result.data?.optJSONArray("lines")
            val out = StringBuilder()
            for (index in 0 until (lines?.length() ?: 0)) {
                val item = lines!!.optJSONObject(index) ?: continue
                val mark = if (item.isNull("ok")) "•" else if (item.optBoolean("ok")) "✓" else "✗"
                out.append("$mark ${item.optString("label")}: ${item.optString("value")}\n")
            }
            AlertDialog.Builder(this)
                .setTitle("Zkouška Přehraj.to")
                .setMessage(if (out.isEmpty()) result.error ?: "Zkoušku se nepodařilo provést." else out.toString().trim())
                .setPositiveButton("OK", null)
                .showStyled()
            load()
        }
    }

    /**
     * Hlášení: celá diagnostika do souboru na disku (když je kam) a zkráceně jako kód QR – telefon ho
     * přečte fotoaparátem a text jde rovnou poslat dál, bez focení obrazovky.
     */
    private fun report() {
        val places = JSONArray()
        for (volume in Storage.volumes(this).sortedByDescending { it.removable }) {
            places.put(JSONObject().put("dir", Backup.folder(volume).absolutePath).put("label", volume.label))
        }
        val waiting = AlertDialog.Builder(this).setTitle("Hlášení").setMessage("Připravuji hlášení…").showStyled()
        Api.call("diagnostics_report", JSONObject().put("device", device).put("dirs", places)) { result ->
            if (isFinishing || isDestroyed) return@call
            val data = result.data
            if (data == null) {
                waiting.dismiss()
                AlertDialog.Builder(this).setTitle("Hlášení")
                    .setMessage(result.error ?: "Hlášení se nepodařilo připravit.")
                    .setPositiveButton("OK", null).showStyled()
                return@call
            }
            // kód se počítá mimo hlavní vlákno: na pomalém zařízení by obrazovka na chvíli zamrzla
            Thread {
                val code = qr(data.optString("qr"))
                runOnUiThread {
                    if (isFinishing || isDestroyed) return@runOnUiThread
                    waiting.dismiss()
                    showReport(data, code)
                }
            }.start()
        }
    }

    private fun showReport(data: JSONObject, code: Bitmap?) {
        val density = resources.displayMetrics.density
        val side = (20 * density).toInt()
        // kód nahoře, vysvětlení pod ním přes celou šířku okna (vedle kódu by se nevešlo)
        val content = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER_HORIZONTAL
            setPadding(side, side / 2, side, 0)
        }
        code?.let { picture ->
            content.addView(android.widget.ImageView(this).apply { setImageBitmap(picture) },
                android.widget.LinearLayout.LayoutParams((QR_SIZE * density).toInt(), (QR_SIZE * density).toInt()))
        }
        val saved = data.optString("saved")
        content.addView(TextView(this).apply {
            textSize = 15f
            setTextColor(ContextCompat.getColor(this@DiagnosticsActivity, R.color.text_primary))
            setPadding(0, side / 2, 0, 0)
            text = "Namiřte na kód fotoaparát telefonu a klepněte na „Napsat e-mail“: otevře se nový e-mail " +
                "s textem hlášení, který můžete odeslat nebo zkopírovat.\n" +
                (if (saved.isNotEmpty()) "Celé hlášení je i v souboru $saved (${data.optString("where")})."
                 else "Soubor se neuložil – není disk, na který by šlo zapsat.")
        }, android.widget.LinearLayout.LayoutParams(android.view.ViewGroup.LayoutParams.MATCH_PARENT,
            android.view.ViewGroup.LayoutParams.WRAP_CONTENT))
        AlertDialog.Builder(this).setTitle("Hlášení").setView(content).setPositiveButton("Hotovo", null).showStyled()
    }

    /** Kód QR jako obrázek (černá na bílé, ostré hrany); `null`, když se text zakódovat nepodaří. */
    private fun qr(content: String): Bitmap? = try {
        val hints = mapOf(EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.L, EncodeHintType.MARGIN to 3)
        val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, 0, 0, hints)   // nejmenší možná mřížka
        val pixels = IntArray(matrix.width * matrix.height) { index ->
            if (matrix.get(index % matrix.width, index / matrix.width)) Color.BLACK else Color.WHITE
        }
        val small = Bitmap.createBitmap(pixels, matrix.width, matrix.height, Bitmap.Config.ARGB_8888)
        val scale = maxOf(1, (QR_SIZE * resources.displayMetrics.density).toInt() / matrix.width)
        Bitmap.createScaledBitmap(small, matrix.width * scale, matrix.height * scale, false)
    } catch (error: Exception) {
        null
    }

    /** Úroveň ochrany Widevine: L1 přehraje chráněné pořady v plné kvalitě, L3 jen omezeně. */
    private fun widevine(): Pair<String, Boolean> = try {
        val drm = MediaDrm(C.WIDEVINE_UUID)
        try {
            val level = drm.getPropertyString("securityLevel")
            "$level, verze ${drm.getPropertyString(MediaDrm.PROPERTY_VERSION)}" to true
        } finally {
            if (android.os.Build.VERSION.SDK_INT >= 28) drm.close() else @Suppress("DEPRECATION") drm.release()
        }
    } catch (error: Throwable) {
        "zařízení ho nepodporuje (${error.javaClass.simpleName})" to false
    }

    private fun load() {
        val out = SpannableStringBuilder()
        val secondary = ContextCompat.getColor(this, R.color.text_secondary)

        val known = JSONArray()
        var own = true   // řádky z aplikace (ne z logiky) se pamatují pro hlášení
        fun line(label: String, value: String, ok: Boolean?) {
            if (own) known.put(JSONObject().put("label", label).put("value", value).put("ok", ok ?: JSONObject.NULL))
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
        own = false
        this.device = known
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
            // co z předání chráněných pořadů přijímá oficiální aplikace ČT v tomto zařízení
            data.optJSONObject("external")?.let { external ->
                val (state, usable) = OfficialApp.describe(this, external)
                line("Aplikace ${external.optString("name")}", state, usable)
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
