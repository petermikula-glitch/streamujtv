package cz.petermikula.mojevidea

import android.content.Context
import android.widget.Toast

/**
 * Jednorázové tipy. Nabídka karty (podržení OK) není na obrazovce nijak vidět, proto ji aplikace
 * při prvních spuštěních jednou připomene – ve chvíli, kdy uživatel prochází karty, které ji mají.
 */
object Hints {
    private const val PREFS = "hints"
    private const val MENU_SHOWN = "menu_shown"
    private const val MENU_TIMES = 3     // v kolika spuštěních se tip ukáže
    private const val MENU_AFTER = 4     // po kolika vybraných kartách s nabídkou
    private var seen = 0
    private var done = false

    fun cardSelected(context: Context, card: Card) {
        if (done || card.menu.isEmpty()) return
        seen++
        if (seen < MENU_AFTER) return
        done = true
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val shown = prefs.getInt(MENU_SHOWN, 0)
        if (shown >= MENU_TIMES) return
        prefs.edit().putInt(MENU_SHOWN, shown + 1).apply()
        Toast.makeText(context.applicationContext,
            "Tip: podržením tlačítka OK na kartě otevřete další možnosti – stažení, oblíbené, zhlédnuto.",
            Toast.LENGTH_LONG).show()
    }
}
