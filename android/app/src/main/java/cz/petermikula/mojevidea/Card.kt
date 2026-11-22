package cz.petermikula.mojevidea

import org.json.JSONArray
import org.json.JSONObject

/** Kam karta vede; o cíli rozhoduje logika v Pythonu. */
class Target(val type: String, val call: String, val params: JSONObject, val title: String)

class Card(
    val title: String,
    val subtitle: String,
    val image: String,
    val progress: Int,
    val wide: Boolean,
    val watched: Boolean,
    val target: Target,
) {
    companion object {
        fun from(json: JSONObject): Card {
            val target = json.optJSONObject("target") ?: JSONObject()
            return Card(
                title = json.optString("title"),
                subtitle = json.optString("subtitle"),
                image = json.optString("image"),
                progress = json.optInt("progress"),
                wide = json.optBoolean("wide"),
                watched = json.optBoolean("watched"),
                target = Target(
                    type = target.optString("type"),
                    call = target.optString("call"),
                    params = target.optJSONObject("params") ?: JSONObject(),
                    title = target.optString("title"),
                ),
            )
        }

        fun list(items: JSONArray?): List<Card> {
            if (items == null) return emptyList()
            return (0 until items.length()).mapNotNull { index -> items.optJSONObject(index)?.let { from(it) } }
        }
    }
}
