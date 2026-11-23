package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.fragment.app.FragmentActivity
import org.json.JSONObject

/** Otevře cíl karty: výpis, přehrávač, hledání, nastavení, nebo provede příkaz. */
object Navigator {
    const val EXTRA_CALL = "call"
    const val EXTRA_PARAMS = "params"
    const val EXTRA_TITLE = "title"
    const val EXTRA_POSTER = "poster"

    fun open(activity: FragmentActivity, card: Card, onChanged: () -> Unit = {}) {
        open(activity, card.target, card.title, onChanged)
    }

    /** Nabídka karty: podržení tlačítka OK nebo tlačítko Menu na ovladači. */
    fun menu(activity: FragmentActivity, card: Card, onChanged: () -> Unit = {}) {
        if (card.menu.isEmpty() || activity.isFinishing || activity.isDestroyed) return
        AlertDialog.Builder(activity)
            .setTitle(card.title)
            .setItems(card.menu.map { it.title }.toTypedArray()) { _, which ->
                open(activity, card.menu[which].target, card.title, onChanged)
            }
            .show()
    }

    fun open(activity: FragmentActivity, target: Target, title: String, onChanged: () -> Unit = {}) {
        if (target.confirm != null) {
            // nejdřív se zeptat (a ukázat volné místo); po potvrzení tentýž cíl bez dotazu
            askSpace(activity, target.confirm) {
                open(activity, Target(target.type, target.call, target.params, target.title, target.poster), title, onChanged)
            }
            return
        }
        when (target.type) {
            "list" -> activity.startActivity(
                Intent(activity, ListActivity::class.java)
                    .putExtra(EXTRA_CALL, target.call)
                    .putExtra(EXTRA_PARAMS, target.params.toString())
                    .putExtra(EXTRA_TITLE, target.title.ifEmpty { title })
                    .putExtra(EXTRA_POSTER, target.poster)
            )
            // obrazovka s řadami dlaždic a karet (složky Filmy a Seriály ze sekce Objevuj)
            "page" -> activity.startActivity(
                Intent(activity, PageActivity::class.java)
                    .putExtra(EXTRA_CALL, target.call)
                    .putExtra(EXTRA_PARAMS, target.params.toString())
                    .putExtra(EXTRA_TITLE, target.title.ifEmpty { title })
            )
            "title" -> activity.startActivity(
                Intent(activity, TitleActivity::class.java)
                    .putExtra(EXTRA_CALL, target.call)
                    .putExtra(EXTRA_PARAMS, target.params.toString())
                    .putExtra(EXTRA_TITLE, target.title.ifEmpty { title })
            )
            "play" -> activity.startActivity(
                Intent(activity, PlayerActivity::class.java)
                    .putExtra(EXTRA_CALL, target.call)
                    .putExtra(EXTRA_PARAMS, target.params.toString())
            )
            // hledání lze otevřít i s připraveným dotazem (např. „Hledat ručně“ u titulu z Objevuj)
            "search" -> activity.startActivity(
                Intent(activity, SearchActivity::class.java).putExtra(EXTRA_PARAMS, target.params.toString())
            )
            "settings" -> activity.startActivity(Intent(activity, SettingsActivity::class.java))
            "diagnostics" -> activity.startActivity(Intent(activity, DiagnosticsActivity::class.java))
            "refresh" -> onChanged()  // „Zkusit znovu“: obrazovka si obsah načte znovu
            "input" -> askText(activity, title, target, onChanged)
            "choices" -> showChoices(activity, target, onChanged)
            "command" -> runCommand(activity, title, target, onChanged)
            "download" -> Api.call(target.call, target.params) { result ->
                if (activity.isFinishing || activity.isDestroyed) return@call
                val message = if (result.ok) result.data?.optString("message").orEmpty() else result.error.orEmpty()
                if (message.isNotEmpty()) Toast.makeText(activity, message, Toast.LENGTH_LONG).show()
                if (result.ok) {
                    DownloadService.start(activity)
                    onChanged()
                }
            }
        }
    }

    /**
     * Dotaz před stažením víc souborů najednou: co se stáhne, kolik místa to zabere (ví logika)
     * a kolik je na disku pro stahování volno (ví aplikace).
     */
    private fun askSpace(activity: FragmentActivity, confirm: JSONObject, proceed: () -> Unit) {
        Api.call("settings_get") { result ->
            if (activity.isFinishing || activity.isDestroyed) return@call
            val volume = Storage.target(activity, result.data?.optString("download_dir").orEmpty())
            val needed = confirm.optLong("bytes")
            val space = when {
                volume == null -> "Disk pro stahování teď není připojený – stahování počká, až ho připojíte."
                needed > 0 && needed > volume.free ->
                    "${volume.description}.\nNa disku není dost místa: stáhne se jen část."
                else -> "${volume.description}."
            }
            AlertDialog.Builder(activity)
                .setTitle(confirm.optString("title"))
                .setMessage("${confirm.optString("text")}\n\n$space")
                .setPositiveButton("Stáhnout") { _, _ -> proceed() }
                .setNegativeButton("Zrušit", null)
                .show()
        }
    }

    /**
     * Nabídka voleb (předvolby přehrávání): skupiny s aktuální hodnotou, po výběru hodnoty se její
     * parametry pošlou akci `apply` a obrazovka se obnoví.
     */
    private fun showChoices(activity: FragmentActivity, target: Target, onChanged: () -> Unit) {
        Api.call(target.call, target.params) { result ->
            if (activity.isFinishing || activity.isDestroyed) return@call
            val data = result.data
            val list = data?.optJSONArray("filters")
            if (data == null || list == null) {
                Toast.makeText(activity, result.error ?: "Nabídku se nepodařilo načíst.", Toast.LENGTH_LONG).show()
                return@call
            }
            val groups = (0 until list.length()).mapNotNull { list.optJSONObject(it) }
            AlertDialog.Builder(activity)
                .setTitle(data.optString("title"))
                .setItems(groups.map { "${it.optString("title")}: ${it.optString("value")}" }.toTypedArray()) { _, which ->
                    val found = groups[which].optJSONArray("options") ?: return@setItems
                    val options = (0 until found.length()).mapNotNull { found.optJSONObject(it) }
                    AlertDialog.Builder(activity)
                        .setTitle(groups[which].optString("title"))
                        .setSingleChoiceItems(options.map { it.optString("title") }.toTypedArray(),
                            options.indexOfFirst { it.optBoolean("current") }) { dialog, chosen ->
                            dialog.dismiss()
                            val params = options[chosen].optJSONObject("params") ?: return@setSingleChoiceItems
                            Api.call(data.optString("apply"), params) { applied ->
                                if (activity.isFinishing || activity.isDestroyed) return@call
                                if (!applied.ok) Toast.makeText(activity, applied.error, Toast.LENGTH_LONG).show()
                                onChanged()
                            }
                        }
                        .show()
                }
                .show()
        }
    }

    /**
     * Krátký text od uživatele (jméno profilu); odešle se příkazu jako `text`.
     *
     * Potvrzení na klávesnici televize text rovnou uloží. Bez toho klávesnice nabízí „Další“, výběr
     * skočí na tlačítko Zrušit a napsané jméno se zahodí. Pole začíná prázdné a dosavadní hodnotu
     * (`value`) ukazuje jen šedě: nové jméno se píše rovnou, bez mazání starého po písmenech, a prázdné
     * pole znamená „nechat, jak je“.
     */
    private fun askText(activity: FragmentActivity, title: String, target: Target, onChanged: () -> Unit) {
        val input = android.widget.EditText(activity).apply {
            hint = target.params.optString("value").ifEmpty { target.params.optString("hint") }
            setSingleLine()
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE
        }
        var saved = false
        val save = {
            if (!saved) {
                saved = true
                val text = input.text.toString().ifBlank { target.params.optString("value") }
                val params = org.json.JSONObject(target.params.toString()).put("text", text)
                runCommand(activity, title, Target("command", target.call, params, ""), onChanged)
            }
        }
        val dialog = AlertDialog.Builder(activity)
            .setTitle(target.params.optString("hint").ifEmpty { title })
            .setView(input)
            .setPositiveButton("Uložit") { _, _ -> save() }
            .setNegativeButton("Zrušit", null)
            .create()
        input.setOnEditorActionListener { _, action, event ->
            val enter = event?.keyCode == android.view.KeyEvent.KEYCODE_ENTER &&
                event.action == android.view.KeyEvent.ACTION_DOWN
            if (action == android.view.inputmethod.EditorInfo.IME_ACTION_DONE || enter) {
                dialog.dismiss()
                save()
                true
            } else {
                false
            }
        }
        dialog.show()
    }

    /** Dlouhá operace (synchronizace, metadata): okno s průběhem, na konci výsledek. */
    fun runCommand(activity: FragmentActivity, title: String, target: Target, onChanged: () -> Unit) {
        if (activity.isFinishing || activity.isDestroyed) return
        val dialog = AlertDialog.Builder(activity)
            .setTitle(title)
            .setMessage("Pracuji…")
            .setCancelable(false)
            .create()
        dialog.show()
        val handler = Handler(Looper.getMainLooper())
        var finished = false
        val poll = object : Runnable {
            override fun run() {
                if (finished) return
                Api.call("progress") { result ->
                    val text = result.data?.optString("text").orEmpty()
                    if (!finished && text.isNotEmpty() && dialog.isShowing) dialog.setMessage(text)
                }
                handler.postDelayed(this, 1000)
            }
        }
        handler.postDelayed(poll, 1000)
        Api.call(target.call, target.params) { result ->
            finished = true
            handler.removeCallbacks(poll)
            if (activity.isFinishing || activity.isDestroyed) {
                // obrazovka mezitím zanikla: okno zavřít, ať po ní nezůstane viset
                try {
                    if (dialog.isShowing) dialog.dismiss()
                } catch (error: IllegalArgumentException) {
                    // okno už systém odstranil s obrazovkou
                }
                return@call
            }
            if (dialog.isShowing) dialog.dismiss()
            val data = result.data
            val message = if (result.ok) data?.optString("message").orEmpty() else result.error.orEmpty()
            if (result.ok && data?.optBoolean("start") == true) DownloadService.start(activity)
            data?.optString("toast")?.takeIf { it.isNotEmpty() }?.let {
                Toast.makeText(activity, it, Toast.LENGTH_LONG).show()
            }
            // příkaz, který vede na další obrazovku (kandidáti po zadání jiného názvu): otevře se hned
            if (result.ok) data?.optJSONObject("open")?.let { open(activity, Target.from(it), title) }
            // příkaz, kterým výběr končí (zvolený profil, obrázek): výpis se zavře
            val finished = {
                if (result.ok && data?.optBoolean("close") == true && activity is ListActivity) activity.finish()
                else onChanged()
            }
            if (message.isNotEmpty()) {
                AlertDialog.Builder(activity)
                    .setTitle(title)
                    .setMessage(message)
                    .setPositiveButton("OK", null)
                    .setOnDismissListener { finished() }
                    .show()
            } else {
                finished()
            }
        }
    }
}
