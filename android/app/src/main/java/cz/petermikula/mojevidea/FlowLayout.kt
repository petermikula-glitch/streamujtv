package cz.petermikula.mojevidea

import android.content.Context
import android.util.AttributeSet
import android.view.View
import android.view.ViewGroup
import kotlin.math.max

/**
 * Tlačítka vedle sebe; která se na řádek nevejdou, pokračují na dalším.
 *
 * Stránka titulu jich mívá čtyři i víc a na jeden řádek se nevešla: poslední bylo uříznuté okrajem
 * obrazovky. Tady je každé vidět celé; tlačítko delší než celý řádek se zkrátí výpustkou.
 */
class FlowLayout @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : ViewGroup(context, attrs) {
    private val gapX = (12 * resources.displayMetrics.density).toInt()
    private val gapY = (8 * resources.displayMetrics.density).toInt()

    /** Kolik řádků tlačítka zabírají. */
    var rowCount = 0
        private set

    /** Projde děti a každému určí místo; vrací (šířka nejdelšího řádku, celková výška, počet řádků). */
    private fun arrange(limit: Int, place: ((View, Int, Int) -> Unit)?): Triple<Int, Int, Int> {
        var x = 0
        var y = 0
        var rowHeight = 0
        var rows = 0
        var widest = 0
        for (index in 0 until childCount) {
            val child = getChildAt(index)
            if (child.visibility == GONE) continue
            if (x > 0 && x + child.measuredWidth > limit) {
                x = 0
                y += rowHeight + gapY
                rowHeight = 0
            }
            if (x == 0) rows++
            place?.invoke(child, x, y)
            x += child.measuredWidth + gapX
            widest = max(widest, x - gapX)
            rowHeight = max(rowHeight, child.measuredHeight)
        }
        return Triple(widest, y + rowHeight, rows)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val limit = if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED) Int.MAX_VALUE / 2
        else MeasureSpec.getSize(widthMeasureSpec) - paddingLeft - paddingRight
        val childWidth = if (limit >= Int.MAX_VALUE / 2) MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED)
        else MeasureSpec.makeMeasureSpec(limit, MeasureSpec.AT_MOST)
        val childHeight = MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED)
        for (index in 0 until childCount) {
            val child = getChildAt(index)
            if (child.visibility != GONE) child.measure(childWidth, childHeight)
        }
        val (width, height, rows) = arrange(limit, null)
        setMeasuredDimension(
            resolveSize(width + paddingLeft + paddingRight, widthMeasureSpec),
            resolveSize(height + paddingTop + paddingBottom, heightMeasureSpec))
        rowCount = rows
    }

    /**
     * Pohyb ovladačem: vpravo z konce řádku na začátek dalšího (a vlevo zpět), dolů a nahoru na
     * nejbližší tlačítko sousedního řádku. Bez toho by se na druhý řádek dalo dostat jen náhodou.
     */
    override fun focusSearch(focused: View?, direction: Int): View? {
        val index = indexOfChild(focused)
        if (focused == null || index < 0) return super.focusSearch(focused, direction)
        fun visible(position: Int) = getChildAt(position)?.takeIf { it.visibility == VISIBLE && it.isFocusable }
        when (direction) {
            FOCUS_RIGHT -> (index + 1 until childCount).firstNotNullOfOrNull { visible(it) }?.let { return it }
            FOCUS_LEFT -> (index - 1 downTo 0).firstNotNullOfOrNull { visible(it) }?.let { return it }
            FOCUS_DOWN, FOCUS_UP -> {
                val others = (0 until childCount).mapNotNull { visible(it) }
                    .filter { if (direction == FOCUS_DOWN) it.top > focused.top else it.top < focused.top }
                val row = if (direction == FOCUS_DOWN) others.minOfOrNull { it.top } else others.maxOfOrNull { it.top }
                val middle = focused.left + focused.width / 2
                others.filter { it.top == row }
                    .minByOrNull { kotlin.math.abs(it.left + it.width / 2 - middle) }?.let { return it }
            }
        }
        return super.focusSearch(focused, direction)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        arrange(right - left - paddingLeft - paddingRight) { child, x, y ->
            child.layout(paddingLeft + x, paddingTop + y,
                paddingLeft + x + child.measuredWidth, paddingTop + y + child.measuredHeight)
        }
    }
}
