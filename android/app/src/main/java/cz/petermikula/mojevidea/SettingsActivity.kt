package cz.petermikula.mojevidea

import android.os.Bundle
import android.text.InputType
import android.widget.Toast
import androidx.fragment.app.FragmentActivity
import androidx.leanback.app.GuidedStepSupportFragment
import androidx.leanback.widget.GuidanceStylist
import androidx.leanback.widget.GuidedAction
import org.json.JSONObject

class SettingsActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) {
            GuidedStepSupportFragment.addAsRoot(this, SettingsFragment(), android.R.id.content)
        }
    }
}

/** Přihlášení k Přehraj.to, klíč TMDB a spuštění synchronizace. */
class SettingsFragment : GuidedStepSupportFragment() {
    private companion object {
        const val EMAIL = 1L
        const val PASSWORD = 2L
        const val TMDB = 3L
        const val SAVE = 4L
        const val SYNC = 5L
        const val BACK = 6L
    }

    override fun onCreateGuidance(savedInstanceState: Bundle?): GuidanceStylist.Guidance {
        return GuidanceStylist.Guidance(
            "Nastavení",
            "Vyplňte e-mail a heslo k Přehraj.to a zvolte Uložit a ověřit. Klíč TMDB je nepovinný – " +
                "doplní plakáty a hodnocení, když ČSFD neodpovídá. iVysílání přihlášení nepotřebuje.",
            getString(R.string.app_name),
            null,
        )
    }

    override fun onCreateActions(actions: MutableList<GuidedAction>, savedInstanceState: Bundle?) {
        val context = requireContext()
        actions.add(
            GuidedAction.Builder(context).id(EMAIL).title("E-mail").description("")
                .descriptionEditable(true)
                .descriptionEditInputType(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS)
                .build()
        )
        actions.add(
            GuidedAction.Builder(context).id(PASSWORD).title("Heslo").description("")
                .descriptionEditable(true)
                .descriptionEditInputType(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
                .descriptionInputType(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
                .build()
        )
        actions.add(
            GuidedAction.Builder(context).id(TMDB).title("Klíč TMDB (nepovinný)").description("")
                .descriptionEditable(true)
                .descriptionEditInputType(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS)
                .build()
        )
        actions.add(GuidedAction.Builder(context).id(SAVE).title("Uložit a ověřit přihlášení").build())
        actions.add(
            GuidedAction.Builder(context).id(SYNC).title("Synchronizovat s účtem")
                .description("Načte nahraná videa a doplní metadata").build()
        )
        actions.add(GuidedAction.Builder(context).id(BACK).title("Zpět").build())
    }

    override fun onViewCreated(view: android.view.View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        Api.call("settings_get") { result ->
            val data = result.data
            if (!isAdded || data == null) return@call
            setText(EMAIL, data.optString("username"))
            setText(TMDB, data.optString("tmdb_key"))
            if (data.optBoolean("has_password")) {
                findActionById(PASSWORD)?.title = "Heslo (uložené – vyplňte jen při změně)"
                notifyActionChanged(findActionPositionById(PASSWORD))
            }
        }
    }

    private fun setText(id: Long, value: String) {
        val action = findActionById(id) ?: return
        // Jen description: Leanback zapisuje upravený text do editDescription, pokud je nastavené,
        // a hodnota čtená z description by pak zůstala stará.
        action.description = value
        notifyActionChanged(findActionPositionById(id))
    }

    private fun text(id: Long): String = findActionById(id)?.description?.toString()?.trim().orEmpty()

    private fun save(done: () -> Unit) {
        val params = JSONObject().put("username", text(EMAIL)).put("tmdb_key", text(TMDB))
        val password = text(PASSWORD)
        if (password.isNotEmpty()) params.put("password", password)
        Api.call("settings_set", params) { result ->
            if (!isAdded) return@call
            if (result.ok) done() else Toast.makeText(requireContext(), result.error, Toast.LENGTH_LONG).show()
        }
    }

    override fun onGuidedActionClicked(action: GuidedAction) {
        val activity = requireActivity()
        when (action.id) {
            SAVE -> save {
                Navigator.runCommand(activity, "Ověření přihlášení", Target("command", "account_status", JSONObject(), "")) {}
            }
            SYNC -> save {
                Navigator.runCommand(activity, "Synchronizace", Target("command", "sync", JSONObject(), "")) {}
            }
            BACK -> activity.finish()
        }
    }
}
