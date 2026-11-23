package cz.petermikula.mojevidea

import android.os.Bundle
import android.text.InputType
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.GuidedStepSupportFragment
import androidx.leanback.widget.GuidanceStylist
import androidx.leanback.widget.GuidedAction
import org.json.JSONObject

/**
 * Průvodce prvním spuštěním: účet Přehraj.to, klíč TMDB a předvolby přehrávání. Každý krok jde
 * přeskočit; všechno se dá později změnit v Nastavení. Podruhé se už sám nenabídne.
 */
class WelcomeActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) {
            GuidedStepSupportFragment.addAsRoot(this, AccountStep(), android.R.id.content)
        }
    }

    override fun onDestroy() {
        if (isFinishing) Api.send("settings_set", JSONObject().put("onboarded", true))
        super.onDestroy()
    }
}

abstract class WelcomeStep : GuidedStepSupportFragment() {
    protected companion object {
        const val NEXT = 100L
        const val SKIP = 101L
    }

    protected fun guidance(title: String, text: String, step: String, art: Int) = GuidanceStylist.Guidance(
        title, text, "${getString(R.string.app_name)} · $step", ContextCompat.getDrawable(requireContext(), art))

    protected fun field(actions: MutableList<GuidedAction>, id: Long, title: String, type: Int) {
        val builder = GuidedAction.Builder(requireContext()).id(id).title(title).description("")
            .descriptionEditable(true).descriptionEditInputType(InputType.TYPE_CLASS_TEXT or type)
        if (type == InputType.TYPE_TEXT_VARIATION_PASSWORD) builder.descriptionInputType(InputType.TYPE_CLASS_TEXT or type)
        actions.add(builder.build())
    }

    protected fun button(actions: MutableList<GuidedAction>, id: Long, title: String, description: String = "") {
        actions.add(GuidedAction.Builder(requireContext()).id(id).title(title).description(description).build())
    }

    protected fun text(id: Long): String = findActionById(id)?.description?.toString()?.trim().orEmpty()

    protected fun go(step: GuidedStepSupportFragment) {
        val manager = requireActivity().supportFragmentManager
        // odpověď dorazila až po odchodu z aplikace (tlačítko Domů): krok se neotevírá, jinak by to spadlo
        if (!manager.isStateSaved) add(manager, step, android.R.id.content)
    }
}

class AccountStep : WelcomeStep() {
    override fun onCreateGuidance(savedInstanceState: Bundle?) = guidance(
        "Vítejte",
        "Přihlaste se svým účtem Přehraj.to – aplikace pak načte videa, která na něm máte, a bude je umět " +
            "přehrát. iVysílání České televize funguje i bez přihlášení.",
        "krok 1 ze 3", R.drawable.guide_account,
    )

    override fun onCreateActions(actions: MutableList<GuidedAction>, savedInstanceState: Bundle?) {
        field(actions, 1, "E-mail", InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS)
        field(actions, 2, "Heslo", InputType.TYPE_TEXT_VARIATION_PASSWORD)
        button(actions, NEXT, "Pokračovat")
        button(actions, SKIP, "Přeskočit", "Účet doplníte kdykoli v Nastavení")
    }

    override fun onGuidedActionClicked(action: GuidedAction) {
        when (action.id) {
            SKIP -> go(CatalogStep())
            NEXT -> {
                if (text(1).isEmpty() || text(2).isEmpty()) {
                    Toast.makeText(requireContext(), "Vyplňte e-mail i heslo, nebo krok přeskočte.", Toast.LENGTH_LONG).show()
                    return
                }
                Api.call("settings_set", JSONObject().put("username", text(1)).put("password", text(2))) { result ->
                    if (!isAdded) return@call
                    if (result.ok) go(CatalogStep()) else Toast.makeText(requireContext(), result.error, Toast.LENGTH_LONG).show()
                }
            }
        }
    }
}

class CatalogStep : WelcomeStep() {
    override fun onCreateGuidance(savedInstanceState: Bundle?) = guidance(
        "Plakáty a objevování",
        "Klíč TMDB doplní plakáty a hodnocení a zapne Objevuj – filmy a seriály podle roku, žánru nebo " +
            "novinek. Je zdarma: založte si účet na themoviedb.org a v jeho nastavení zvolte API.",
        "krok 2 ze 3", R.drawable.guide_catalog,
    )

    override fun onCreateActions(actions: MutableList<GuidedAction>, savedInstanceState: Bundle?) {
        field(actions, 1, "Klíč TMDB", InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS)
        button(actions, NEXT, "Pokračovat")
        button(actions, SKIP, "Přeskočit", "Bez klíče zůstane Objevuj prázdné")
    }

    override fun onGuidedActionClicked(action: GuidedAction) {
        when (action.id) {
            SKIP -> go(PreferencesStep())
            NEXT -> Api.call("settings_set", JSONObject().put("tmdb_key", text(1))) {
                if (isAdded) go(PreferencesStep())
            }
        }
    }
}

class PreferencesStep : WelcomeStep() {
    private companion object {
        const val LANGUAGE = 1L
        const val QUALITY = 2L
    }

    private var choices = JSONObject()

    override fun onCreateGuidance(savedInstanceState: Bundle?) = guidance(
        "Jak chcete přehrávat",
        "Když je film na Přehraj.to ve více verzích, aplikace nabídne jako první tu, která odpovídá vaší volbě.",
        "krok 3 ze 3", R.drawable.guide_prefs,
    )

    override fun onCreateActions(actions: MutableList<GuidedAction>, savedInstanceState: Bundle?) {
        button(actions, LANGUAGE, "Jazyk")
        button(actions, QUALITY, "Kvalita")
        button(actions, NEXT, "Dokončit", "Jste-li přihlášeni, načtou se vaše videa")
    }

    override fun onViewCreated(view: android.view.View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        Api.call("settings_get") { result ->
            if (!isAdded) return@call
            choices = result.data ?: return@call
            show(LANGUAGE, "languages", "prefer_language")
            show(QUALITY, "qualities", "prefer_quality")
        }
    }

    private fun show(id: Long, list: String, key: String) {
        val options = choices.optJSONArray(list) ?: return
        val current = choices.optString(key)
        for (index in 0 until options.length()) {
            val option = options.optJSONObject(index) ?: continue
            if (option.optString("id") == current) {
                findActionById(id)?.description = option.optString("title")
                notifyActionChanged(findActionPositionById(id))
            }
        }
    }

    private fun choose(id: Long, list: String, key: String, title: String) {
        val options = choices.optJSONArray(list) ?: return
        val names = (0 until options.length()).map { options.optJSONObject(it)?.optString("title").orEmpty() }
        android.app.AlertDialog.Builder(requireContext())
            .setTitle(title)
            .setItems(names.toTypedArray()) { _, which ->
                val value = options.optJSONObject(which)?.optString("id").orEmpty()
                Api.call("settings_set", JSONObject().put(key, value)) { result ->
                    if (!isAdded) return@call
                    choices = result.data ?: return@call
                    show(id, list, key)
                }
            }
            .show()
    }

    override fun onGuidedActionClicked(action: GuidedAction) {
        val activity = requireActivity()
        when (action.id) {
            LANGUAGE -> choose(LANGUAGE, "languages", "prefer_language", "Čemu dát přednost")
            QUALITY -> choose(QUALITY, "qualities", "prefer_quality", "Jakou kvalitu přehrávat")
            NEXT -> if (choices.optString("username").isEmpty()) activity.finish() else {
                Navigator.runCommand(activity, "Načítám vaše videa", Target("command", "sync", JSONObject(), "")) {
                    activity.finish()
                }
            }
        }
    }
}
