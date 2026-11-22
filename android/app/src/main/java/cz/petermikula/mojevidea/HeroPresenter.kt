package cz.petermikula.mojevidea

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ObjectAdapter
import androidx.leanback.widget.Presenter
import com.bumptech.glide.Glide

/** Řada s úvodním panelem; liší se od běžné řady karet jen tím, jak se kreslí. */
class HeroRow(adapter: ObjectAdapter) : ListRow(adapter)

/**
 * Úvodní panel: velká karta s obrázkem titulu (nebo výchozí ilustrací), názvem, zbývajícím časem
 * a tlačítkem. Nápis tlačítka nese karta v prvním štítku.
 */
class HeroPresenter : Presenter() {
    override fun onCreateViewHolder(parent: ViewGroup): ViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.hero_card, parent, false)
        val density = parent.resources.displayMetrics.density
        val ring = GradientDrawable().apply {
            setColor(Color.TRANSPARENT)
            setStroke((2 * density).toInt(), ContextCompat.getColor(parent.context, R.color.accent))
            cornerRadius = 2 * density
        }
        view.setOnFocusChangeListener { focused, has -> focused.foreground = if (has) ring else null }
        return ViewHolder(view)
    }

    override fun onBindViewHolder(viewHolder: ViewHolder, item: Any?) {
        val card = item as? Card ?: return
        val view = viewHolder.view
        view.findViewById<TextView>(R.id.hero_title).text = card.title
        view.findViewById<TextView>(R.id.hero_subtitle).text = card.subtitle
        view.findViewById<TextView>(R.id.hero_button).text = "▶  " + (card.badges.firstOrNull() ?: "Přehrát")
        view.findViewById<ProgressBar>(R.id.hero_progress).apply {
            progress = card.progress
            visibility = if (card.progress > 0) View.VISIBLE else View.GONE
        }
        val image = view.findViewById<ImageView>(R.id.hero_image)
        val glide = Glide.with(view.context.applicationContext)
        if (card.image.isEmpty()) {
            glide.clear(image)
            image.setImageResource(R.drawable.hero_default)
        } else {
            glide.load(card.image).timeout(15_000).centerCrop().placeholder(R.drawable.hero_default).into(image)
        }
    }

    override fun onUnbindViewHolder(viewHolder: ViewHolder) {
        val image = viewHolder.view.findViewById<ImageView>(R.id.hero_image)
        Glide.with(viewHolder.view.context.applicationContext).clear(image)
        image.setImageDrawable(null)
    }
}
