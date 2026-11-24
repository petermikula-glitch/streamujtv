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

    private const val REMOVE_SHOWN = "remove_shown"
    private const val REMOVE_TIMES = 4   // v kolika spuštěních se tip k Rozkoukaným ukáže
    private const val REMOVE_AFTER = 2   // po kolika vybraných rozkoukaných kartách
    private var removable = 0
    private var removeDone = false

    /**
     * Rozkoukané: že se film nebo seriál dá ze seznamu odebrat, není nikde vidět (je to v nabídce
     * karty). Tip se proto ukáže zvlášť, ve chvíli, kdy uživatel rozkoukané karty prochází.
     */
    private fun removeHint(context: Context, card: Card): Boolean {
        if (removeDone || card.menu.none { it.title.startsWith("Odebrat z Rozkoukaných") }) return false
        removable++
        if (removable < REMOVE_AFTER) return false
        removeDone = true
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val shown = prefs.getInt(REMOVE_SHOWN, 0)
        if (shown >= REMOVE_TIMES) return false
        prefs.edit().putInt(REMOVE_SHOWN, shown + 1).apply()
        Toast.makeText(context.applicationContext,
            "Tip: podržte tlačítko OK na kartě a zvolte „Odebrat z Rozkoukaných“ – tak z tohoto seznamu zmizí.",
            Toast.LENGTH_LONG).show()
        return true
    }

    fun cardSelected(context: Context, card: Card) {
        if (removeHint(context, card)) return
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
