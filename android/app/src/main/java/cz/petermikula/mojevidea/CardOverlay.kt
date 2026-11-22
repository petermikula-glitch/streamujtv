package cz.petermikula.mojevidea

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable

/** Vrstva přes obrázek karty: proužek rozkoukanosti dole a značka zhlédnutí v rohu. */
class CardOverlay(
    private val progress: Int,
    private val watched: Boolean,
    private val density: Float,
    accent: Int,
) : Drawable() {
    private val track = Paint().apply { color = 0xB3000000.toInt() }
    private val bar = Paint().apply { color = accent }
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xD90B1220.toInt() }
    private val tick = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = accent
        style = Paint.Style.STROKE
        strokeWidth = 2.5f * density
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }

    override fun draw(canvas: Canvas) {
        val area = bounds
        if (progress > 0) {
            val height = 4 * density
            val top = area.bottom - height
            canvas.drawRect(area.left.toFloat(), top, area.right.toFloat(), area.bottom.toFloat(), track)
            val end = area.left + area.width() * progress.coerceIn(0, 100) / 100f
            canvas.drawRect(area.left.toFloat(), top, end, area.bottom.toFloat(), bar)
        }
        if (watched) {
            val size = 22 * density
            val margin = 6 * density
            val left = area.right - margin - size
            val top = area.top + margin
            canvas.drawCircle(left + size / 2, top + size / 2, size / 2, disc)
            val path = Path().apply {
                moveTo(left + size * 0.28f, top + size * 0.52f)
                lineTo(left + size * 0.44f, top + size * 0.68f)
                lineTo(left + size * 0.74f, top + size * 0.36f)
            }
            canvas.drawPath(path, tick)
        }
    }

    override fun setAlpha(alpha: Int) = Unit

    override fun setColorFilter(filter: ColorFilter?) = Unit

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}
