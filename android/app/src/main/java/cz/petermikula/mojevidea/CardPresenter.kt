package cz.petermikula.mojevidea

import android.view.ViewGroup
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
        val glide = Glide.with(view.context.applicationContext)
        if (card.image.isNotEmpty()) {
            view.titleText = (if (card.watched) "✓ " else "") + card.title
            view.contentText = detail
            // výchozí limit Glide (2,5 s) je na pomalé síti krátký
            glide.load(card.image).timeout(15_000).centerCrop().into(view.mainImageView)
        } else {
            // dlaždice nese název sama; pod ní stačí doplňující řádek
            view.titleText = detail.ifEmpty { card.title }
            view.contentText = ""
            glide.clear(view.mainImageView)
            view.setMainImage(TileDrawable(card.title, density), false)
        }
    }

    override fun onUnbindViewHolder(viewHolder: ViewHolder) {
        val view = viewHolder.view as ImageCardView
        Glide.with(view.context.applicationContext).clear(view.mainImageView)
        view.mainImage = null
    }
}
