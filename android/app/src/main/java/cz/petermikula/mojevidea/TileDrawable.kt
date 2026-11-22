package cz.petermikula.mojevidea

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.Shader
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
 * Dlaždice pro kartu bez obrázku (složky, kategorie, nástroje): barevný přechod, velká ikona a název.
 *
 * Ikona je obrázek `ic_tile_<název>`. Existuje-li obrázek `tile_<název>` (vlastní ilustrace bez textu),
 * použije se místo přechodu s ikonou a název se vykreslí přes něj.
 */
class TileDrawable(context: Context, private val title: String, icon: String) : Drawable() {
    private companion object {
        /** Základní barva podle ikony; kategorie stejného druhu drží stejný odstín. */
        val COLORS = mapOf(
            "live" to 0xFFD9482B, "series" to 0xFF2563EB, "movies" to 0xFF7C3AED, "docs" to 0xFF0D9488,
            "news" to 0xFF0369A1, "sport" to 0xFF15803D, "kids" to 0xFFDB2777, "fun" to 0xFFD97706,
            "past" to 0xFF92400E, "culture" to 0xFF9333EA, "advice" to 0xFFB45309, "nature" to 0xFF4D7C0F,
            "society" to 0xFF475569, "spirit" to 0xFFA16207, "archive" to 0xFF57534E,
            "login" to 0xFFC98A00, "search" to 0xFF0E7490, "sync" to 0xFF1D4ED8, "enrich" to 0xFF6D28D9,
            "settings" to 0xFF475569, "all" to 0xFF4338CA, "history" to 0xFF0F766E, "favorites" to 0xFFBE123C,
            "rating" to 0xFFB7791F, "year" to 0xFF1E40AF, "folder" to 0xFF334155, "download" to 0xFF047857,
        )

        fun mix(color: Int, target: Int, amount: Float): Int {
            fun channel(shift: Int): Int {
                val from = (color shr shift) and 0xFF
                val to = (target shr shift) and 0xFF
                return (from + (to - from) * amount).toInt().coerceIn(0, 255)
            }
            return Color.rgb(channel(16), channel(8), channel(0))
        }
    }

    private val density = context.resources.displayMetrics.density
    private val name = icon.ifEmpty { "folder" }
    private val base: Int
    private val glyph: Drawable?
    private val art: Drawable?
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shade = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = 21 * density
        typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
        setShadowLayer(3 * density, 0f, density, 0x66000000)
    }
    private val padding = 12 * density

    init {
        val resources = context.resources
        val packageName = context.packageName
        // podkategorie sdílejí ikonu své kategorie; podle názvu se o kousek liší odstínem a tónem,
        // aby mřížka stejných dlaždic nepůsobila jednolitě
        val seed = title.hashCode() and 0x7fffffff
        val hsv = FloatArray(3)
        Color.colorToHSV((COLORS[name] ?: COLORS.getValue("folder")).toInt(), hsv)
        hsv[0] = (hsv[0] + (seed % 5 - 2) * 9f + 360f) % 360f
        base = mix(Color.HSVToColor(hsv), Color.BLACK, (seed / 5 % 3) * 0.08f)
        val artId = resources.getIdentifier("tile_$name", "drawable", packageName)
        art = if (artId != 0) ContextCompat.getDrawable(context, artId)?.mutate() else null
        val glyphId = resources.getIdentifier("ic_tile_$name", "drawable", packageName)
            .takeIf { it != 0 } ?: R.drawable.ic_tile_folder
        glyph = if (art == null) ContextCompat.getDrawable(context, glyphId)?.mutate() else null
        glyph?.alpha = 60
    }

    override fun onBoundsChange(bounds: Rect) {
        fill.shader = LinearGradient(
            bounds.left.toFloat(), bounds.top.toFloat(), bounds.right.toFloat(), bounds.bottom.toFloat(),
            mix(base, Color.WHITE, 0.10f), mix(base, Color.BLACK, 0.58f), Shader.TileMode.CLAMP,
        )
        // ztmavení spodní části, aby byl název čitelný i přes ilustraci
        shade.shader = LinearGradient(
            0f, bounds.top + bounds.height() * 0.35f, 0f, bounds.bottom.toFloat(),
            0x00000000, 0xB3000000.toInt(), Shader.TileMode.CLAMP,
        )
    }

    override fun draw(canvas: Canvas) {
        val area = bounds
        if (area.isEmpty) return
        canvas.save()
        canvas.clipRect(area)
        if (art != null) {
            val width = max(1, art.intrinsicWidth)
            val height = max(1, art.intrinsicHeight)
            val scale = max(area.width() / width.toFloat(), area.height() / height.toFloat())
            val w = (width * scale).toInt()
            val h = (height * scale).toInt()
            val left = area.left + (area.width() - w) / 2
            val top = area.top + (area.height() - h) / 2
            art.setBounds(left, top, left + w, top + h)
            art.draw(canvas)
            canvas.drawRect(area, shade)
        } else {
            canvas.drawRect(area, fill)
            glyph?.let {
                // velká poloprůhledná ikona vpravo, zčásti přes okraj
                val size = min(area.width() * 0.66f, area.height() * 1.05f).toInt()
                val left = area.right - (size * 0.86f).toInt()
                val top = area.top + ((area.height() - size) * 0.36f).toInt()
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
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}
