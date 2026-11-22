package cz.petermikula.mojevidea

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import android.os.SystemClock
import kotlin.math.sin

/** Zástupná plocha karty při načítání: jemně pulzuje, dokud je vidět. */
class SkeletonDrawable(color: Int) : Drawable() {
    private val paint = Paint().apply { this.color = color }
    private val refresh = Runnable { invalidateSelf() }

    override fun draw(canvas: Canvas) {
        val phase = (SystemClock.uptimeMillis() % 1400L) / 1400.0
        paint.alpha = (150 + 80 * sin(phase * 2 * Math.PI)).toInt().coerceIn(0, 255)
        canvas.drawRect(bounds, paint)
        scheduleSelf(refresh, SystemClock.uptimeMillis() + 70)  // kreslí se jen na viditelné kartě
    }

    override fun setAlpha(alpha: Int) = Unit

    override fun setColorFilter(filter: ColorFilter?) = Unit

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}
