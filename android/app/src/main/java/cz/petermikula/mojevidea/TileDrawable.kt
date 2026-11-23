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
        textSize = 20 * density
        typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)
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
        if (art != null) {
            // malba vyplní dlaždici na výšku a drží se pravého okraje (motiv je v pravé polovině)
            val width = max(1, art.intrinsicWidth)
            val height = max(1, art.intrinsicHeight)
            // Na kartě na výšku (plakát bez obrázku) se malba vejde celá na šířku; jinak vyplní dlaždici.
            val portrait = area.height() > area.width()
            val scale = if (portrait) area.width() / width.toFloat()
            else max(area.width() / width.toFloat(), area.height() / height.toFloat())
            // na malé kartě (verze, díly) je motiv jen v pozadí, ať nepřebíjí název
            art.alpha = if (area.width() < 240 * density) 90 else 255
            val w = (width * scale).toInt()
            val h = (height * scale).toInt()
            val left = area.right - w
            val top = area.top + (area.height() - h) / 2
            art.setBounds(left, top, left + w, top + h)
            art.draw(canvas)
        } else {
            glyph?.let {
                // obrysová ikona v pravém horním rohu; název má vlevo dole volné místo
                val size = min(area.width() * 0.26f, area.height() * 0.44f).toInt()
                val left = area.right - (padding * 1.3f).toInt() - size
                val top = area.top + (padding * 1.2f).toInt()
                it.setBounds(left, top, left + size, top + size)
                it.draw(canvas)
            }
        }
        val width = (area.width() - 2 * padding).toInt()
        if (width > 0 && title.isNotEmpty()) {
            val layout = StaticLayout.Builder.obtain(title, 0, title.length, text, width)
                .setAlignment(Layout.Alignment.ALIGN_NORMAL)
                .setMaxLines(3)
                .setEllipsize(TextUtils.TruncateAt.END)
                .build()
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
}
