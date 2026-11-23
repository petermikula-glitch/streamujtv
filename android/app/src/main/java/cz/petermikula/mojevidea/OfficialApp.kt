package cz.petermikula.mojevidea

import android.app.SearchManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import android.util.Log
import org.json.JSONObject
import org.xmlpull.v1.XmlPullParser

/**
 * Předání pořadu jiné (oficiální) aplikaci – pro pořady, které tato aplikace přehrát neumí.
 *
 * Co se předává, určuje logika (`external` v odpovědi `ivys_play`): balíčky aplikace, veřejné adresy
 * pořadu a jeho název. Zkouší se postupně jen to, k čemu se oficiální aplikace sama hlásí:
 * 1. adresa dílu nebo pořadu (otevře ji aktivita, která takové odkazy přijímá),
 * 2. „přehraj z hledání“ a hledání podle názvu pořadu (běžné způsoby, jak aplikaci na televizi říct, co má najít),
 * 3. když nic z toho neumí, aplikace se aspoň spustí.
 */
object OfficialApp {
    enum class Result { OPENED_TITLE, OPENED_SEARCH, OPENED_APP, MISSING }

    private const val ANDROID = "http://schemas.android.com/apk/res/android"
    private const val GMS_SEARCH = "com.google.android.gms.actions.SEARCH_ACTION"

    /** Způsoby předání v pořadí, v jakém se zkoušejí: popis, výsledek a záměr. */
    private fun ways(info: JSONObject, name: String): List<Triple<String, Result, Intent>> {
        val list = ArrayList<Triple<String, Result, Intent>>()
        val urls = info.optJSONArray("urls")
        val addresses = (0 until (urls?.length() ?: 0)).map { urls!!.optString(it) }.filter { it.isNotEmpty() }
            .ifEmpty { listOf(info.optString("url")).filter { it.isNotEmpty() } }
        for ((index, url) in addresses.withIndex()) {
            list += Triple(if (index == 0) "odkaz na díl" else "odkaz na pořad", Result.OPENED_TITLE,
                Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(name).addCategory(Intent.CATEGORY_BROWSABLE))
        }
        val query = info.optString("query")
        if (query.isNotEmpty()) {
            list += Triple("přehrání z hledání", Result.OPENED_SEARCH,
                Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).setPackage(name)
                    .putExtra(SearchManager.QUERY, query)
                    .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/video")
                    .putExtra(MediaStore.EXTRA_MEDIA_TITLE, query))
            list += Triple("hledání", Result.OPENED_SEARCH,
                Intent(Intent.ACTION_SEARCH).setPackage(name).putExtra(SearchManager.QUERY, query))
            list += Triple("hledání (Google)", Result.OPENED_SEARCH,
                Intent(GMS_SEARCH).setPackage(name).putExtra(SearchManager.QUERY, query))
        }
        return list
    }

    private fun packages(info: JSONObject): List<String> {
        val list = info.optJSONArray("packages") ?: return emptyList()
        return (0 until list.length()).map { list.optString(it) }.filter { it.isNotEmpty() }
    }

    fun open(context: Context, info: JSONObject): Result {
        val manager = context.packageManager
        for (name in packages(info)) {
            val launch = manager.getLeanbackLaunchIntentForPackage(name) ?: manager.getLaunchIntentForPackage(name)
                ?: continue   // aplikace v zařízení není
            for ((_, result, intent) in ways(info, name)) {
                if (intent.resolveActivity(manager) != null &&
                    start(context, intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))) return result
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

    /**
     * Pro Diagnostiku: je oficiální aplikace v zařízení, co z předání přijímá a k jakým odkazům se
     * sama hlásí (její veřejné zápisy v manifestu: akce a adresy, žádný kód ani údaje).
     */
    fun describe(context: Context, info: JSONObject): Pair<String, Boolean> {
        val manager = context.packageManager
        for (name in packages(info)) {
            val version = try {
                manager.getPackageInfo(name, 0).versionName ?: "?"
            } catch (error: Exception) {
                continue
            }
            val accepted = ways(info, name).filter { it.third.resolveActivity(manager) != null }.map { it.first }
            val summary = if (accepted.isEmpty()) "pořad jí předat nejde, jen se spustí" else "přijímá: ${accepted.joinToString(", ")}"
            val declared = declaredLinks(context, name)
            return "$name $version – $summary" + (if (declared.isEmpty()) "" else "\n      hlásí se k: $declared") to accepted.isNotEmpty()
        }
        return "není nainstalovaná (chráněné pořady se pak nemají kde přehrát)" to false
    }

    /** Akce a adresy, ke kterým se aplikace hlásí ve svém manifestu (mimo běžné spuštění). */
    private fun declaredLinks(context: Context, name: String): String = try {
        val found = LinkedHashSet<String>()
        val parser = context.createPackageContext(name, 0).assets.openXmlResourceParser("AndroidManifest.xml")
        var actions = ArrayList<String>()
        var data = ArrayList<String>()
        var event = parser.eventType
        while (event != XmlPullParser.END_DOCUMENT) {
            if (event == XmlPullParser.START_TAG) {
                when (parser.name) {
                    "intent-filter" -> { actions = ArrayList(); data = ArrayList() }
                    "action" -> parser.getAttributeValue(ANDROID, "name")?.let { actions.add(it.substringAfterLast('.')) }
                    "data" -> {
                        val parts = listOf("scheme", "host", "path", "pathPrefix", "pathPattern", "mimeType")
                            .mapNotNull { key -> parser.getAttributeValue(ANDROID, key)?.let { "$key=$it" } }
                        if (parts.isNotEmpty()) data.add(parts.joinToString(" "))
                    }
                }
            } else if (event == XmlPullParser.END_TAG && parser.name == "intent-filter") {
                val interesting = actions.filter { it != "MAIN" }
                if (interesting.isNotEmpty()) {
                    found.add(interesting.joinToString("/") + if (data.isEmpty()) "" else " [" + data.joinToString("; ") + "]")
                }
            }
            event = parser.next()
        }
        parser.close()
        found.joinToString(" | ").take(900)
    } catch (error: Exception) {
        ""   // manifest cizí aplikace nejde přečíst: diagnostika se obejde bez něj
    }
}
