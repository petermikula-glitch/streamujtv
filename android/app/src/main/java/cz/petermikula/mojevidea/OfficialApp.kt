package cz.petermikula.mojevidea

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import org.json.JSONObject

/**
 * Předání pořadu jiné (oficiální) aplikaci – pro pořady, které tato aplikace přehrát neumí.
 *
 * Co se předává, určuje logika (`external` v odpovědi `ivys_play`): balíčky aplikace a veřejná
 * adresa pořadu. Nejdřív se zkusí adresa pořadu – otevře ji jen aktivita, která se k takovým
 * odkazům sama hlásí; když žádná není, aplikace se aspoň spustí.
 */
object OfficialApp {
    enum class Result { OPENED_TITLE, OPENED_APP, MISSING }

    fun open(context: Context, info: JSONObject): Result {
        val packages = info.optJSONArray("packages") ?: return Result.MISSING
        val url = info.optString("url")
        val manager = context.packageManager
        for (index in 0 until packages.length()) {
            val name = packages.optString(index)
            val launch = manager.getLeanbackLaunchIntentForPackage(name) ?: manager.getLaunchIntentForPackage(name)
                ?: continue   // aplikace v zařízení není
            if (url.isNotEmpty()) {
                val view = Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(name)
                    .addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                if (view.resolveActivity(manager) != null && start(context, view)) return Result.OPENED_TITLE
            }
            if (start(context, launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))) return Result.OPENED_APP
        }
        return Result.MISSING
    }

    private fun start(context: Context, intent: Intent): Boolean = try {
        context.startActivity(intent)
        true
    } catch (error: Exception) {
        Log.w("OfficialApp", "Předání se nepodařilo: ${error.message}")
        false
    }
}
