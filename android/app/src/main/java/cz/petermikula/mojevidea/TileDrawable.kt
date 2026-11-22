package cz.petermikula.mojevidea

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils

/** Dlaždice pro kartu bez obrázku (složky, nástroje): barevná plocha s názvem. */
class TileDrawable(private val title: String, density: Float) : Drawable() {
    private companion object {
        val PALETTE = intArrayOf(
            0xFF2E5B7A.toInt(), 0xFF3B6E5E.toInt(), 0xFF7A4E2E.toInt(), 0xFF5E3B6E.toInt(),
            0xFF6E3B4A.toInt(), 0xFF2E6E7A.toInt(), 0xFF4A5E2E.toInt(), 0xFF3B4A7A.toInt(),
        )
    }

    private val background = Paint().apply {
        color = PALETTE[(title.hashCode() and 0x7fffffff) % PALETTE.size]
    }
    private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = 24 * density
        typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
    }
    private val padding = 14 * density

    override fun draw(canvas: Canvas) {
        val area = bounds
        canvas.drawRect(area, background)
        val width = (area.width() - 2 * padding).toInt()
        if (width <= 0 || title.isEmpty()) return
        val layout = StaticLayout.Builder.obtain(title, 0, title.length, text, width)
            .setAlignment(Layout.Alignment.ALIGN_NORMAL)
            .setMaxLines(3)
            .setEllipsize(TextUtils.TruncateAt.END)
            .build()
        canvas.save()
        canvas.translate(area.left + padding, area.bottom - padding - layout.height)
        layout.draw(canvas)
        canvas.restore()
    }

    override fun setAlpha(alpha: Int) {
        background.alpha = alpha
        text.alpha = alpha
    }

    override fun setColorFilter(filter: ColorFilter?) {
        background.colorFilter = filter
        text.colorFilter = filter
    }

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}
