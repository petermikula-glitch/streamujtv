package cz.petermikula.mojevidea

import android.app.AlertDialog
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.fragment.app.FragmentActivity

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
        when (target.type) {
            "list" -> activity.startActivity(
                Intent(activity, ListActivity::class.java)
                    .putExtra(EXTRA_CALL, target.call)
                    .putExtra(EXTRA_PARAMS, target.params.toString())
                    .putExtra(EXTRA_TITLE, target.title.ifEmpty { title })
                    .putExtra(EXTRA_POSTER, target.poster)
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

    /** Krátký text od uživatele (jméno profilu); odešle se příkazu jako `text`. */
    private fun askText(activity: FragmentActivity, title: String, target: Target, onChanged: () -> Unit) {
        val input = android.widget.EditText(activity).apply {
            setText(target.params.optString("value"))
            hint = target.params.optString("hint")
            setSingleLine()
            setSelection(text.length)
        }
        AlertDialog.Builder(activity)
            .setTitle(target.params.optString("hint").ifEmpty { title })
            .setView(input)
            .setPositiveButton("Uložit") { _, _ ->
                val params = org.json.JSONObject(target.params.toString()).put("text", input.text.toString())
                runCommand(activity, title, Target("command", target.call, params, ""), onChanged)
            }
            .setNegativeButton("Zrušit", null)
            .show()
    }

    /** Dlouhá operace (synchronizace, metadata): okno s průběhem, na konci výsledek. */
    fun runCommand(activity: FragmentActivity, title: String, target: Target, onChanged: () -> Unit) {
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
            if (activity.isFinishing || activity.isDestroyed) return@call
            if (dialog.isShowing) dialog.dismiss()
            val data = result.data
            val message = if (result.ok) data?.optString("message").orEmpty() else result.error.orEmpty()
            if (result.ok && data?.optBoolean("start") == true) DownloadService.start(activity)
            data?.optString("toast")?.takeIf { it.isNotEmpty() }?.let {
                Toast.makeText(activity, it, Toast.LENGTH_LONG).show()
            }
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
