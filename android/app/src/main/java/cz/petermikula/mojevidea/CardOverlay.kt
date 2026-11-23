package cz.petermikula.mojevidea

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable

/** Vrstva přes obrázek karty: štítky (jazyk, kvalita) vlevo nahoře, proužek rozkoukanosti dole a značka zhlédnutí. */
class CardOverlay(
    private val progress: Int,
    private val watched: Boolean,
    private val density: Float,
    accent: Int,
    private val badges: List<String> = emptyList(),
) : Drawable() {
    // štítek jako tah tuše: tmavý s písmem v barvě papíru
    private val chip = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xE61D1B18.toInt() }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFF0E9D9.toInt()
        textSize = 12 * density
        typeface = android.graphics.Typeface.create("sans-serif", android.graphics.Typeface.NORMAL)
    }

    private val track = Paint().apply { color = 0x991D1B18.toInt() }
    private val bar = Paint().apply { color = accent }
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xE6F0E9D9.toInt() }
    private val tick = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = accent
        style = Paint.Style.STROKE
        strokeWidth = 2.5f * density
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }

    override fun draw(canvas: Canvas) {
        val area = bounds
        var left = area.left + 6 * density
        val top = area.top + 6 * density
        val height = 18 * density
        // místo vpravo patří značce zhlédnutí – ale jen když tam je; jinak se na plakát vejde i třetí štítek
        val reserve = (if (watched) 30 else 4) * density
        for (badge in badges) {
            val width = label.measureText(badge) + 10 * density
            if (left + width > area.right - reserve) break
            canvas.drawRoundRect(left, top, left + width, top + height, 2 * density, 2 * density, chip)
            canvas.drawText(badge, left + 5 * density, top + height - 5 * density, label)
            left += width + 4 * density
        }
        if (progress > 0) {
            val height = 3 * density
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
