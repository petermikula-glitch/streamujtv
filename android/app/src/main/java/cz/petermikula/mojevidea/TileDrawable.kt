package cz.petermikula.mojevidea

import android.content.Context
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import androidx.core.content.ContextCompat
import kotlin.math.max
import kotlin.math.min

/**
 * Dlaždice pro kartu bez obrázku (složky, kategorie, nástroje): list papíru s motivem tuší a názvem.
 *
 * Název `icon` určuje motiv: existuje-li malba `tile_<název>` (tuš na průhledném pozadí, motiv vpravo),
 * položí se na papír; jinak se vpravo nahoře nakreslí obrysová ikona `ic_tile_<název>`. Zápis
 * `ikona:barva` (např. `folder:movies`) zůstává kvůli datům z logiky – použije se jen část před dvojtečkou;
 * barevné odstíny kategorií tento vzhled nemá.
 */
class TileDrawable(context: Context, private val title: String, icon: String) : Drawable() {
    private val density = context.resources.displayMetrics.density
    private val name = icon.substringBefore(':').ifEmpty { "folder" }
    private val glyph: Drawable?
    private val art: Drawable?
    private val fill = Paint().apply { color = ContextCompat.getColor(context, R.color.surface) }
    private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.ink)
        typeface = Typeface.create("sans-serif", Typeface.NORMAL)
    }
    private val padding = 14 * density

    init {
        val resources = context.resources
        val packageName = context.packageName
        val artId = resources.getIdentifier("tile_$name", "drawable", packageName)
        art = if (artId != 0) ContextCompat.getDrawable(context, artId)?.mutate() else null
        val glyphId = resources.getIdentifier("ic_tile_$name", "drawable", packageName)
            .takeIf { it != 0 } ?: R.drawable.ic_tile_folder
        glyph = if (art == null) ContextCompat.getDrawable(context, glyphId)?.mutate() else null
        glyph?.setTint(ContextCompat.getColor(context, R.color.text_secondary))
        glyph?.alpha = 170
    }

    override fun draw(canvas: Canvas) {
        val area = bounds
        if (area.isEmpty) return
        canvas.save()
        canvas.clipRect(area)
        canvas.drawRect(area, fill)
        // název čitelný z dálky: na velké dlaždici větší
        val small = area.width() < SMALL * density
        text.textSize = (if (small) 20 else 24) * density
        val width = (area.width() - 2 * padding).toInt()
        val layout = if (width > 0 && title.isNotEmpty()) {
            StaticLayout.Builder.obtain(title, 0, title.length, text, width)
                .setAlignment(Layout.Alignment.ALIGN_NORMAL)
                .setMaxLines(3)
                .setEllipsize(TextUtils.TruncateAt.END)
                .build()
        } else null
        if (art != null) {
            // malba vyplní dlaždici na výšku a drží se pravého okraje (motiv je v pravé polovině)
            val width = max(1, art.intrinsicWidth)
            val height = max(1, art.intrinsicHeight)
            // Na kartě na výšku (plakát bez obrázku) se malba vejde celá na šířku; jinak vyplní dlaždici.
            val portrait = area.height() > area.width()
            val scale = if (portrait) area.width() / width.toFloat()
            else max(area.width() / width.toFloat(), area.height() / height.toFloat())
            // na malé kartě (verze, díly) je motiv tlumenější, ať nepřebíjí název; víceřádkový název
            // (jméno souboru) přes něj jde celý, proto ještě víc
            art.alpha = if (!small) 255 else if ((layout?.lineCount ?: 1) > 1) 60 else 150
            val w = (width * scale).toInt()
            val h = (height * scale).toInt()
            val left = area.right - w
            val top = area.top + (area.height() - h) / 2
            art.setBounds(left, top, left + w, top + h)
            art.draw(canvas)
        } else {
            glyph?.let {
                // obrysová ikona v pravém horním rohu; název má vlevo dole volné místo
                var size = min(area.width() * 0.26f, area.height() * 0.44f).toInt()
                val right = area.right - (padding * 1.3f).toInt()
                val top = area.top + (padding * 1.2f).toInt()
                // Název přes dva nebo tři řádky sahá až pod ikonu a písmo se s ní překrývalo:
                // ikona se pak zmenší do místa, které nad názvem zbývá (a když žádné není, vynechá se).
                if (layout != null) {
                    val textTop = area.bottom - padding - layout.height
                    val reaches = (0 until layout.lineCount).any { line ->
                        textTop + layout.getLineTop(line) < top + size &&
                            area.left + padding + layout.getLineRight(line) > right - size - GAP * density
                    }
                    if (reaches) size = min(size, (textTop - GAP * density - top).toInt())
                }
                if (size >= MIN_GLYPH * density) {
                    it.setBounds(right - size, top, right, top + size)
                    it.draw(canvas)
                }
            }
        }
        if (layout != null) {
            canvas.translate(area.left + padding, area.bottom - padding - layout.height)
            layout.draw(canvas)
        }
        canvas.restore()
    }

    override fun setAlpha(alpha: Int) {
        fill.alpha = alpha
        text.alpha = alpha
    }

    override fun setColorFilter(filter: ColorFilter?) {
        fill.colorFilter = filter
        text.colorFilter = filter
    }

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.OPAQUE

    private companion object {
        const val SMALL = 240   // šířka (dp), pod kterou se dlaždice bere jako malá karta
        const val GAP = 6       // dp: mezera mezi ikonou a názvem
        const val MIN_GLYPH = 18   // dp: menší ikona by byla jen skvrna, to ať raději není
    }
}
