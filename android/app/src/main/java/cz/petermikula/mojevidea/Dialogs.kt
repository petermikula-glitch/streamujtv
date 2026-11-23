package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.content.Context
import android.content.DialogInterface
import android.widget.ArrayAdapter
import android.widget.ListAdapter
import android.widget.TextView
import androidx.core.content.ContextCompat

/**
 * Vzhled oken s nabídkami a hlášeními.
 *
 * Položky nabídek kreslí aplikace sama (vlastní rozvržení a barvy): systémové rozvržení se mezi
 * zařízeními liší a na některých televizích má světlé písmo, které na světlém okně není vidět.
 * Stejně tak se napevno nastavuje barva textu hlášení, tlačítek a zvýraznění vybrané položky.
 */
object Dialogs {
    /** Položky nabídky (`setAdapter`). */
    fun items(context: Context, titles: List<String>): ListAdapter =
        ArrayAdapter(context, R.layout.dialog_item, android.R.id.text1, titles)

    /** Položky výběru jedné možnosti (`setSingleChoiceItems`). */
    fun choices(context: Context, titles: List<String>): ListAdapter =
        ArrayAdapter(context, R.layout.dialog_choice, android.R.id.text1, titles)

    /** Po zobrazení okna: zvýraznění vybrané položky, barva hlášení a tlačítek. */
    fun polish(dialog: AlertDialog): AlertDialog {
        val context = dialog.context
        dialog.listView?.let { list ->
            list.selector = ContextCompat.getDrawable(context, R.drawable.dialog_item_selector)
            list.setDrawSelectorOnTop(false)
        }
        dialog.findViewById<TextView>(android.R.id.message)
            ?.setTextColor(ContextCompat.getColor(context, R.color.text_primary))
        for (which in intArrayOf(DialogInterface.BUTTON_POSITIVE, DialogInterface.BUTTON_NEGATIVE,
            DialogInterface.BUTTON_NEUTRAL)) {
            dialog.getButton(which)?.apply {
                setTextColor(ContextCompat.getColorStateList(context, R.color.dialog_button_text))
                setBackgroundResource(R.drawable.dialog_button_bg)
            }
        }
        return dialog
    }
}

/** Zobrazí okno a sjednotí jeho vzhled. */
fun AlertDialog.Builder.showStyled(): AlertDialog = Dialogs.polish(show())
