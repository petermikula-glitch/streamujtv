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
            val message = if (result.ok) result.data?.optString("message").orEmpty() else result.error.orEmpty()
            if (message.isNotEmpty()) {
                AlertDialog.Builder(activity)
                    .setTitle(title)
                    .setMessage(message)
                    .setPositiveButton("OK", null)
                    .setOnDismissListener { onChanged() }
                    .show()
            } else {
                onChanged()
            }
        }
    }
}
