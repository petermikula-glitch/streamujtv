package cz.petermikula.mojevidea

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.view.KeyEvent
import android.widget.Toast
import androidx.core.content.edit

/**
 * Rychlý návrat do hlavní nabídky: podržení tlačítka Zpět na kterékoli hlubší obrazovce (výpis,
 * stránka titulu, hledání…) zavře všechny obrazovky nad hlavní a otevře postranní nabídku. Bez toho
 * by se z hloubky Objevuj (Filmy → Podle žánru → Komedie → titul) muselo vracet krok po kroku.
 */
object Home {
    const val EXTRA_MENU = "menu"
    const val EXTRA_DEPTH = "depth"   // kolik obrazovek je nad hlavní (hlavní = 0)

    private const val PREFS = "hints"
    private const val HINT_SHOWN = "home_shown"
    private const val HINT_TIMES = 3   // kolikrát nejvýš se tip ukáže
    private const val HINT_DEPTH = 3   // od kolikáté obrazovky nad hlavní
    private var hinted = false         // v jednom spuštění nejvýš jednou
    private const val LONG_REPEATS = 8 // ovladač, který neoznačí podržení: tolik opakování stisku (asi půl sekundy)

    /** Zavře obrazovky nad hlavní a otevře na ní postranní nabídku. */
    fun go(activity: Activity) {
        activity.startActivity(
            Intent(activity, MainActivity::class.java)
                .putExtra(EXTRA_MENU, true)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
    }

    private var held = false   // podržené Zpět už vrátilo do nabídky: jeho uvolnění se nesmí počítat jako Zpět

    /**
     * Pro `dispatchKeyEvent` obrazovek (stisk se zachytí dřív, než ho dostane výpis nebo jiný prvek):
     * podržené Zpět = hlavní nabídka. Krátký stisk projde dál a vrací o krok jako dřív.
     */
    fun dispatch(activity: Activity, event: KeyEvent): Boolean {
        if (event.keyCode != KeyEvent.KEYCODE_BACK || activity is MainActivity) return false
        return when (event.action) {
            KeyEvent.ACTION_DOWN -> when {
                event.repeatCount == 0 -> { held = false; false }
                event.isLongPress || event.repeatCount >= LONG_REPEATS -> {
                    if (!held) {
                        held = true
                        go(activity)
                    }
                    true
                }
                else -> true   // opakování během držení: čeká se, jestli jde o podržení
            }
            KeyEvent.ACTION_UP -> if (held) { held = false; true } else false
            else -> false
        }
    }

    /** Hloubka obrazovky, kterou `activity` právě otevírá. */
    fun nextDepth(activity: Activity): Int = activity.intent.getIntExtra(EXTRA_DEPTH, 0) + 1

    /** Tip při procházení do hloubky: že se jde vrátit najednou. */
    fun hint(context: Context, depth: Int) {
        if (hinted || depth < HINT_DEPTH) return
        hinted = true
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val shown = prefs.getInt(HINT_SHOWN, 0)
        if (shown >= HINT_TIMES) return
        prefs.edit { putInt(HINT_SHOWN, shown + 1) }
        Toast.makeText(context.applicationContext,
            "Tip: podržením tlačítka Zpět se vrátíte rovnou do hlavní nabídky.", Toast.LENGTH_LONG).show()
    }
}
