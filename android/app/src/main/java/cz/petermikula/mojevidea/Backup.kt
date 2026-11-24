package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import android.widget.Toast
import androidx.fragment.app.FragmentActivity
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Záloha dat aplikace na disk a obnova z ní (dlaždice „Záloha a obnova“ v Nástrojích).
 *
 * Co v záloze je a jak se obnovuje, řeší logika (`app_backup.py`); tady se vybírá, kam soubor patří.
 * Bez zvláštního povolení smí aplikace zapisovat jen do své složky na disku – a tu Android při
 * odinstalaci smaže i se zálohou. Aby záloha odinstalaci přežila, musí ležet mimo ni (složka
 * `Promitarna` v kořeni disku), a k tomu je potřeba v systému povolit aplikaci přístup k souborům.
 * Povolení je dobrovolné: bez něj se zálohuje do složky aplikace a okno to řekne.
 */
object Backup {
    private const val FOLDER = "Promitarna"

    /** Smí aplikace zapisovat mimo svou složku? (Před Androidem 11 takové povolení není.) */
    private fun wide(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager()

    /** Složka pro zálohy na daném disku: mimo složku aplikace, když to systém dovolí, jinak v ní. */
    fun folder(volume: Storage.Volume): File {
        if (wide()) {
            val root = volume.dir.absolutePath.substringBefore("/Android/data/", "")
            if (root.isNotEmpty()) return File(root, FOLDER)
        }
        return volume.dir
    }

    fun open(activity: FragmentActivity, onChanged: () -> Unit) {
        val items = ArrayList<Pair<String, () -> Unit>>()
        items.add("Zálohovat na disk" to { export(activity) })
        items.add("Obnovit ze zálohy" to { restore(activity, onChanged) })
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !wide()) {
            items.add("Povolit zálohu, která přežije odinstalaci" to { askAccess(activity) })
        }
        AlertDialog.Builder(activity)
            .setTitle(if (wide()) "Záloha a obnova" else "Záloha a obnova (jen do složky aplikace)")
            .setAdapter(Dialogs.items(activity, items.map { it.first })) { _, which -> items[which].second() }
            .showStyled()
    }

    private fun export(activity: FragmentActivity) {
        val volumes = Storage.volumes(activity)
        if (volumes.isEmpty()) {
            Toast.makeText(activity, "Žádné úložiště není dostupné.", Toast.LENGTH_LONG).show()
            return
        }
        fun write(volume: Storage.Volume) {
            val params = JSONObject().put("dir", folder(volume).absolutePath)
                .put("version", Updater.currentVersion(activity))
            Api.call("backup_export", params) { result ->
                if (activity.isFinishing || activity.isDestroyed) return@call
                val note = if (wide()) "" else "\n\nZáloha leží ve složce aplikace: při odinstalaci aplikace zmizí. " +
                    "Aby zůstala, zvolte „Povolit zálohu, která přežije odinstalaci“ a zálohujte znovu."
                AlertDialog.Builder(activity)
                    .setTitle("Záloha")
                    .setMessage(if (result.ok) result.data?.optString("message").orEmpty() + note
                                else result.error ?: "Zálohu se nepodařilo uložit.")
                    .setPositiveButton("OK", null)
                    .showStyled()
            }
        }
        // vyměnitelný disk první: záloha má smysl hlavně mimo zařízení
        val ordered = volumes.sortedByDescending { it.removable }
        if (ordered.size == 1) {
            write(ordered[0])
            return
        }
        AlertDialog.Builder(activity)
            .setTitle("Kam zálohovat")
            .setAdapter(Dialogs.items(activity, ordered.map { it.description })) { _, which -> write(ordered[which]) }
            .showStyled()
    }

    private fun restore(activity: FragmentActivity, onChanged: () -> Unit) {
        // zálohy se hledají na všech discích, ve složce aplikace i mimo ni
        val places = JSONArray()
        for (volume in Storage.volumes(activity)) {
            for (dir in linkedSetOf(folder(volume), volume.dir)) {
                places.put(JSONObject().put("dir", dir.absolutePath).put("label", volume.label))
            }
        }
        Api.call("backup_list", JSONObject().put("dirs", places)) { result ->
            if (activity.isFinishing || activity.isDestroyed) return@call
            val found = result.data?.optJSONArray("backups")
            val backups = (0 until (found?.length() ?: 0)).mapNotNull { found?.optJSONObject(it) }
            if (backups.isEmpty()) {
                AlertDialog.Builder(activity)
                    .setTitle("Obnovit ze zálohy")
                    .setMessage(result.error ?: ("Na připojených discích žádná záloha není." +
                        if (wide()) "" else " Zálohy uložené mimo složku aplikace uvidí aplikace až po povolení přístupu k souborům."))
                    .setPositiveButton("OK", null)
                    .showStyled()
                return@call
            }
            AlertDialog.Builder(activity)
                .setTitle("Kterou zálohu obnovit")
                .setAdapter(Dialogs.items(activity, backups.map { it.optString("title") })) { _, which ->
                    confirm(activity, backups[which], onChanged)
                }
                .showStyled()
        }
    }

    private fun confirm(activity: FragmentActivity, backup: JSONObject, onChanged: () -> Unit) {
        AlertDialog.Builder(activity)
            .setTitle("Obnovit data ze zálohy?")
            .setMessage(backup.optString("title") + "\n\nKatalog, historie přehrávání, oblíbené, profily a nastavení " +
                "se vrátí do stavu ze zálohy. Co přibylo později, zmizí. Přihlášení k Přehraj.to zůstane.")
            .setNegativeButton("Zpět", null)
            .setPositiveButton("Obnovit") { _, _ ->
                Api.call("backup_import", JSONObject().put("path", backup.optString("path"))) { result ->
                    if (activity.isFinishing || activity.isDestroyed) return@call
                    if (!result.ok) {
                        AlertDialog.Builder(activity)
                            .setTitle("Obnova se nezdařila")
                            .setMessage(result.error ?: "Zálohu se nepodařilo obnovit.")
                            .setPositiveButton("OK", null)
                            .showStyled()
                        return@call
                    }
                    Toast.makeText(activity, result.data?.optString("message").orEmpty(), Toast.LENGTH_LONG).show()
                    onChanged()
                    // všechno, co je na obrazovkách, je teď staré: aplikace začne znovu od hlavní obrazovky
                    activity.startActivity(Intent(activity, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                }
            }
            .showStyled()
            // obnova přepíše data: předvybraný je krok zpět, ať ji nespustí jedno stisknutí navíc
            .getButton(AlertDialog.BUTTON_NEGATIVE)?.requestFocus()
    }

    /** Otevře v systému stránku, kde jde aplikaci povolit přístup k souborům; když tam žádná není, poradí. */
    private fun askAccess(activity: FragmentActivity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        val attempts = listOf(
            Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:${activity.packageName}")),
            Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
        )
        for (intent in attempts) {
            try {
                activity.startActivity(intent)
                Toast.makeText(activity, "Zapněte přístup pro Promítárnu, vraťte se tlačítkem Zpět a zálohujte znovu.",
                    Toast.LENGTH_LONG).show()
                return
            } catch (error: ActivityNotFoundException) {
                continue
            } catch (error: SecurityException) {
                continue
            }
        }
        AlertDialog.Builder(activity)
            .setTitle("Povolení se nepodařilo otevřít")
            .setMessage("Zkuste to v nastavení televize: Aplikace → Zvláštní přístup aplikací → Přístup ke všem souborům → " +
                "Promítárna. Bez povolení se zálohuje do složky aplikace.")
            .setPositiveButton("OK", null)
            .showStyled()
    }
}
