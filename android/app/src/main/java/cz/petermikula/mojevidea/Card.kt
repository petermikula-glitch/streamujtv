package cz.petermikula.mojevidea

import androidx.leanback.widget.DiffCallback
import org.json.JSONArray
import org.json.JSONObject

/** Kam karta vede; o cíli rozhoduje logika v Pythonu. */
class Target(val type: String, val call: String, val params: JSONObject, val title: String) {
    companion object {
        fun from(json: JSONObject?): Target {
            val target = json ?: JSONObject()
            return Target(
                type = target.optString("type"),
                call = target.optString("call"),
                params = target.optJSONObject("params") ?: JSONObject(),
                title = target.optString("title"),
            )
        }
    }
}

/** Položka nabídky karty (podržení tlačítka OK nebo tlačítko Menu). */
class MenuItem(val title: String, val target: Target)

class Card(
    val title: String,
    val subtitle: String,
    val image: String,
    val progress: Int,
    val wide: Boolean,
    val watched: Boolean,
    val icon: String,
    val target: Target,
    val menu: List<MenuItem>,
    val badges: List<String>,
) {
    /** Totožnost karty (kam vede) – nezávisle na tom, jak je zrovna vykreslená. */
    val key: String get() = target.type + "|" + target.call + "|" + target.params + "|" + title

    fun sameLook(other: Card): Boolean = title == other.title && subtitle == other.subtitle &&
        image == other.image && progress == other.progress && wide == other.wide && watched == other.watched &&
        icon == other.icon && badges == other.badges

    companion object {
        /** Při obnově výpisu se překreslí jen změněné karty a výběr zůstane na místě. */
        val DIFF = object : DiffCallback<Any>() {
            override fun areItemsTheSame(old: Any, new: Any): Boolean =
                old is Card && new is Card && old.key == new.key

            override fun areContentsTheSame(old: Any, new: Any): Boolean =
                old is Card && new is Card && old.sameLook(new)
        }

        fun from(json: JSONObject): Card {
            val entries = json.optJSONArray("menu")
            val menu = (0 until (entries?.length() ?: 0)).mapNotNull { index ->
                entries!!.optJSONObject(index)?.let { MenuItem(it.optString("title"), Target.from(it.optJSONObject("target"))) }
            }
            return Card(
                title = json.optString("title"),
                subtitle = json.optString("subtitle"),
                image = json.optString("image"),
                progress = json.optInt("progress"),
                wide = json.optBoolean("wide"),
                watched = json.optBoolean("watched"),
                icon = json.optString("icon"),
                target = Target.from(json.optJSONObject("target")),
                menu = menu,
                badges = json.optJSONArray("badges")?.let { list ->
                    (0 until list.length()).map { list.optString(it) }.filter { it.isNotEmpty() }
                } ?: emptyList(),
            )
        }

        fun list(items: JSONArray?): List<Card> {
            if (items == null) return emptyList()
            return (0 until items.length()).mapNotNull { index -> items.optJSONObject(index)?.let { from(it) } }
        }
    }
}
