package cz.petermikula.mojevidea

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.leanback.widget.ImageCardView
import androidx.leanback.widget.Presenter
import com.bumptech.glide.Glide

/**
 * Karta s obrázkem: plakát na výšku, nebo širší náhled (díly, živé vysílání, složky).
 * Tvar je společný pro celou řadu či mřížku – karty různých tvarů vedle sebe mřížku rozhází.
 */
class CardPresenter(var poster: Boolean = false) : Presenter() {
    companion object {
        /** Plakáty na výšku, jakmile je má aspoň jedna karta; jinak náhledy na šířku. */
        fun posterShape(cards: List<Card>): Boolean = cards.any { it.image.isNotEmpty() && !it.wide }
    }

    /** Vybraná karta: světlejší popisek a jantarový rámeček, aby byl výběr vidět i z gauče. */
    private class CardView(context: Context) : ImageCardView(context) {
        private val surface = ContextCompat.getColor(context, R.color.surface)
        private val focused = ContextCompat.getColor(context, R.color.surface_focused)
        private val ring = GradientDrawable().apply {
            val density = context.resources.displayMetrics.density
            setColor(Color.TRANSPARENT)
            setStroke((2 * density).toInt(), ContextCompat.getColor(context, R.color.accent))
            cornerRadius = 2 * density
        }

        init {
            setBackgroundColor(ContextCompat.getColor(context, R.color.card_empty))
            setInfoAreaBackgroundColor(surface)
        }

        override fun setSelected(selected: Boolean) {
            super.setSelected(selected)
            setInfoAreaBackgroundColor(if (selected) focused else surface)
            foreground = if (selected) ring else null
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup): ViewHolder {
        val view = CardView(parent.context).apply {
            isFocusable = true
            isFocusableInTouchMode = true
        }
        return ViewHolder(view)
    }

    override fun onBindViewHolder(viewHolder: ViewHolder, item: Any?) {
        val card = item as? Card ?: return
        val view = viewHolder.view as ImageCardView
        val context = view.context
        val density = view.resources.displayMetrics.density
        // rozměry vycházejí na obrazovku televize širokou 960 dp: 4 náhledy, nebo 5 plakátů vedle sebe
        val width = ((if (poster) 150 else 200) * density).toInt()
        val height = ((if (poster) 225 else 112) * density).toInt()
        view.setMainImageDimensions(width, height)
        val detail = when {
            card.progress > 0 && card.subtitle.isNotEmpty() -> "${card.progress} % · ${card.subtitle}"
            card.progress > 0 -> "Rozkoukáno ${card.progress} %"
            else -> card.subtitle
        }
        // Glide se váže na kontext aplikace: karty se uvolňují i při zavírání obrazovky,
        // kdy by požadavek vázaný na zaniklou aktivitu shodil aplikaci.
        val glide = Glide.with(context.applicationContext)
        if (card.image.isNotEmpty()) {
            view.titleText = card.title
            view.contentText = detail
            // výchozí limit Glide (2,5 s) je na pomalé síti krátký
            glide.load(card.image).timeout(15_000).centerCrop().into(view.mainImageView)
        } else {
            // dlaždice nese název sama; pod ní stačí doplňující řádek
            view.titleText = detail.ifEmpty { card.title }
            view.contentText = ""
            glide.clear(view.mainImageView)
            view.setMainImage(TileDrawable(context, card.title, card.icon), false)
        }
        view.mainImageView.foreground = if (card.progress > 0 || card.watched) {
            CardOverlay(card.progress, card.watched, density, ContextCompat.getColor(context, R.color.accent))
        } else {
            null
        }
    }

    override fun onUnbindViewHolder(viewHolder: ViewHolder) {
        val view = viewHolder.view as ImageCardView
        Glide.with(view.context.applicationContext).clear(view.mainImageView)
        view.mainImage = null
        view.mainImageView.foreground = null
    }
}
