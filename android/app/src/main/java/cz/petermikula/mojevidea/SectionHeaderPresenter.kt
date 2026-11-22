package cz.petermikula.mojevidea

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.leanback.widget.Presenter
import androidx.leanback.widget.Row
import androidx.leanback.widget.RowHeaderPresenter

/**
 * Položka postranní nabídky s ikonou sekce (`ic_<název>`, zlatá jako ostatní obrysové ikony).
 * Název ikony k položce dodává hlavní obrazovka; položka bez ikony ukáže jen text.
 */
class SectionHeaderPresenter(private val iconOf: (Long) -> String) : RowHeaderPresenter() {
    private var dimmed = 0.5f

    override fun onCreateViewHolder(parent: ViewGroup): Presenter.ViewHolder {
        dimmed = parent.resources.getFraction(androidx.leanback.R.fraction.lb_browse_header_unselect_alpha, 1, 1)
        val view = LayoutInflater.from(parent.context).inflate(R.layout.section_header, parent, false)
        view.alpha = dimmed
        return ViewHolder(view)
    }

    override fun onBindViewHolder(viewHolder: Presenter.ViewHolder, item: Any?) {
        val header = (item as? Row)?.headerItem
        val view = viewHolder.view
        val context = view.context
        view.findViewById<TextView>(R.id.section_label).text = header?.name.orEmpty()
        val name = header?.let { iconOf(it.id) }.orEmpty()
        val id = if (name.isEmpty()) 0 else context.resources.getIdentifier("ic_$name", "drawable", context.packageName)
        val icon = view.findViewById<ImageView>(R.id.section_icon)
        if (id == 0) {
            icon.visibility = View.GONE
        } else {
            icon.setImageDrawable(ContextCompat.getDrawable(context, id)?.mutate()?.apply {
                setTint(ContextCompat.getColor(context, R.color.accent))
            })
            icon.visibility = View.VISIBLE
        }
    }

    override fun onUnbindViewHolder(viewHolder: Presenter.ViewHolder) {
        // nic k uvolnění: ikona je vektor z prostředků
    }

    override fun onSelectLevelChanged(holder: ViewHolder) {
        holder.view.alpha = dimmed + holder.selectLevel * (1f - dimmed)
    }
}
