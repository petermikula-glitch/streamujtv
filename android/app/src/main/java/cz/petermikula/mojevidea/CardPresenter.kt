package cz.petermikula.mojevidea

import android.view.ViewGroup
import androidx.leanback.widget.ImageCardView
import androidx.leanback.widget.Presenter
import com.bumptech.glide.Glide

/** Karta s obrázkem: plakát na výšku, nebo širší náhled (díly, živé vysílání, složky). */
class CardPresenter : Presenter() {
    override fun onCreateViewHolder(parent: ViewGroup): ViewHolder {
        val view = ImageCardView(parent.context).apply {
            isFocusable = true
            isFocusableInTouchMode = true
        }
        return ViewHolder(view)
    }

    override fun onBindViewHolder(viewHolder: ViewHolder, item: Any?) {
        val card = item as? Card ?: return
        val view = viewHolder.view as ImageCardView
        val density = view.resources.displayMetrics.density
        val wide = card.wide || card.image.isEmpty()
        val width = ((if (wide) 220 else 150) * density).toInt()
        val height = ((if (wide) 124 else 225) * density).toInt()
        view.setMainImageDimensions(width, height)
        view.titleText = (if (card.watched) "✓ " else "") + card.title
        view.contentText = when {
            card.progress > 0 && card.subtitle.isNotEmpty() -> "${card.progress} % · ${card.subtitle}"
            card.progress > 0 -> "Rozkoukáno ${card.progress} %"
            else -> card.subtitle
        }
        if (card.image.isNotEmpty()) {
            Glide.with(view.context).load(card.image).centerCrop().into(view.mainImageView)
        } else {
            Glide.with(view.context).clear(view.mainImageView)
            view.mainImage = null
        }
    }

    override fun onUnbindViewHolder(viewHolder: ViewHolder) {
        val view = viewHolder.view as ImageCardView
        Glide.with(view.context).clear(view.mainImageView)
        view.mainImage = null
    }
}
