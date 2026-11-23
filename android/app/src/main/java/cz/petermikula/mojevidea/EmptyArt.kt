package cz.petermikula.mojevidea

import android.widget.TextView
import androidx.core.content.ContextCompat
import kotlin.math.max

/** Ilustrace nad hlášením prázdné obrazovky (obrázek `empty_<název>`). */
object EmptyArt {
    const val NOTHING = "nothing"
    const val OFFLINE = "offline"

    /** Prázdný nebo neznámý název ilustraci odebere. */
    fun show(label: TextView, name: String) {
        val context = label.context
        val id = if (name.isEmpty()) 0 else context.resources.getIdentifier("empty_$name", "drawable", context.packageName)
        val art = if (id != 0) ContextCompat.getDrawable(context, id) else null
        val density = context.resources.displayMetrics.density
        art?.let {
            // ilustrace prázdných stavů se kreslí jednou barvou tuše, ať jsou jakékoli
            it.mutate().setTint(ContextCompat.getColor(context, R.color.text_secondary))
            val height = (150 * density).toInt()
            it.setBounds(0, 0, height * it.intrinsicWidth / max(1, it.intrinsicHeight), height)
        }
        label.compoundDrawablePadding = (8 * density).toInt()
        label.setCompoundDrawables(null, art, null, null)
    }
}
