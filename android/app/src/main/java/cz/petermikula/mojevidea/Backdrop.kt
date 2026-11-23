package cz.petermikula.mojevidea

import android.graphics.Bitmap
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.os.Handler
import android.os.Looper
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.BackgroundManager
import com.bumptech.glide.Glide
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition

/**
 * Pozadí obrazovky podle vybrané karty: její obrázek prosvítá papírem jen slabě (jako lavírovaná tuš),
 * aby karty a texty zůstaly čitelné.
 * Mění se s krátkým zpožděním, takže rychlé projíždění karet nic nenačítá.
 */
class Backdrop(private val activity: FragmentActivity) {
    private companion object {
        const val DELAY_MS = 500L
        const val ENABLED = false
    }

    private val manager = BackgroundManager.getInstance(activity)
    private val handler = Handler(Looper.getMainLooper())
    private var wanted = ""
    private var shown = ""
    private var request: CustomTarget<Bitmap>? = null

    init {
        if (!manager.isAttached) manager.attach(activity.window)
        manager.isAutoReleaseOnStop = false  // po návratu z přehrávače zůstane pozadí na místě
    }

    fun show(image: String) {
        // Papírový vzhled má pozadí klidné: obrázek vybrané karty by ho přebarvoval. Třída zůstává,
        // kdyby se pozadí podle karty mělo vrátit (stačí vrátit adresu obrázku).
        val url = if (ENABLED && !image.startsWith("res:")) image else ""
        if (url == wanted) return
        wanted = url
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ apply(url) }, DELAY_MS)
    }

    private fun apply(url: String) {
        if (activity.isFinishing || activity.isDestroyed || url == shown) return
        val glide = Glide.with(activity.applicationContext)
        request?.let { glide.clear(it) }
        request = null
        if (url.isEmpty()) {
            shown = ""
            manager.clearDrawable()
            return
        }
        val target = object : CustomTarget<Bitmap>(960, 540) {
            override fun onResourceReady(bitmap: Bitmap, transition: Transition<in Bitmap>?) {
                if (activity.isFinishing || activity.isDestroyed || url != wanted) return
                shown = url
                // kopie: Glide smí původní bitmapu kdykoli vrátit do zásobníku, pozadí ji ale dál kreslí
                val picture = bitmap.copy(Bitmap.Config.ARGB_8888, false) ?: return
                val scrim = GradientDrawable(
                    GradientDrawable.Orientation.TOP_BOTTOM,
                    intArrayOf(0xD9D9D0BB.toInt(), 0xEBD2C9B4.toInt(), 0xF5C9BFA8.toInt()),
                )
                manager.drawable = LayerDrawable(arrayOf(BitmapDrawable(activity.resources, picture), scrim))
            }

            override fun onLoadCleared(placeholder: Drawable?) = Unit
        }
        request = target
        glide.asBitmap().load(url).timeout(15_000).centerCrop().into(target)
    }

    fun release() {
        handler.removeCallbacksAndMessages(null)
        request?.let { Glide.with(activity.applicationContext).clear(it) }
        request = null
    }
}
