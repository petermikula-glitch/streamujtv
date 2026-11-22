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
        const val STORAGE = 7L
        const val UPDATE = 8L
        const val LANGUAGE = 9L
        const val QUALITY = 10L
        const val CLEANUP = 11L
    }

    override fun onCreateGuidance(savedInstanceState: Bundle?): GuidanceStylist.Guidance {
        return GuidanceStylist.Guidance(
            "Nastavení",
            "Vyplňte e-mail a heslo k Přehraj.to a zvolte Uložit a ověřit. Klíč TMDB je nepovinný – " +
                "doplní plakáty a hodnocení, když ČSFD neodpovídá. iVysílání přihlášení nepotřebuje.",
            getString(R.string.app_name),
            ContextCompat.getDrawable(requireContext(), R.mipmap.ic_launcher),
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
        actions.add(GuidedAction.Builder(context).id(LANGUAGE).title("Přehraj.to: jazyk").description("").build())
        actions.add(GuidedAction.Builder(context).id(QUALITY).title("Přehraj.to: kvalita").description("").build())
        actions.add(
            GuidedAction.Builder(context).id(STORAGE).title("Úložiště pro stahování")
                .description("Zjišťuji…").build()
        )
        actions.add(
            GuidedAction.Builder(context).id(CLEANUP).title("Mazat stažené po dokoukání").description("").build()
        )
        actions.add(
            GuidedAction.Builder(context).id(UPDATE).title("Zkontrolovat aktualizace")
                .description("Verze ${Updater.currentVersion(context)}").build()
        )
        actions.add(GuidedAction.Builder(context).id(BACK).title("Zpět").build())
    }

    private var downloadDir = ""
    private var choices = JSONObject()
    private var cleanup = false

    private fun showCleanup() {
        setText(CLEANUP, if (cleanup) "Zapnuto – dokoukaný soubor se z disku smaže" else "Vypnuto")
    }

    /** Předvolba přehrávání z Přehraj.to: popis aktuální hodnoty a výběr z nabídky. */
    private fun showChoice(id: Long, list: String, key: String) {
        val options = choices.optJSONArray(list) ?: return
        val current = choices.optString(key)
        for (index in 0 until options.length()) {
            val option = options.optJSONObject(index) ?: continue
            if (option.optString("id") == current) setText(id, option.optString("title"))
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
                    val data = result.data
                    if (!isAdded || data == null) return@call
                    choices = data
                    showChoice(id, list, key)
                }
            }
            .show()
    }

    /** Popis zvoleného úložiště; bez volby se použije první připojený USB disk. */
    private fun showStorage() {
        val volume = Storage.target(requireContext(), downloadDir)
        setText(STORAGE, volume?.description ?: "USB disk není připojený")
    }

    private fun chooseStorage() {
        val context = requireContext()
        val volumes = Storage.volumes(context)
        if (volumes.isEmpty()) {
            Toast.makeText(context, "Žádné úložiště není dostupné.", Toast.LENGTH_LONG).show()
            return
        }
        android.app.AlertDialog.Builder(context)
            .setTitle(if (volumes.any { it.removable }) "Kam stahovat" else "Kam stahovat (USB disk není připojený)")
            .setItems(volumes.map { it.description }.toTypedArray()) { _, which ->
                downloadDir = volumes[which].dir.absolutePath
                Api.call("settings_set", JSONObject().put("download_dir", downloadDir)) {
                    if (!isAdded) return@call
                    showStorage()
                    DownloadService.start(requireContext())  // co čekalo na disk, může začít
                }
            }
            .show()
    }

    override fun onViewCreated(view: android.view.View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        Api.call("settings_get") { result ->
            val data = result.data
            if (!isAdded || data == null) return@call
            setText(EMAIL, data.optString("username"))
            setText(TMDB, data.optString("tmdb_key"))
            downloadDir = data.optString("download_dir")
            showStorage()
            choices = data
            showChoice(LANGUAGE, "languages", "prefer_language")
            showChoice(QUALITY, "qualities", "prefer_quality")
            cleanup = data.optBoolean("delete_watched")
            showCleanup()
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
            LANGUAGE -> choose(LANGUAGE, "languages", "prefer_language", "Čemu dát přednost")
            QUALITY -> choose(QUALITY, "qualities", "prefer_quality", "Jakou kvalitu přehrávat")
            STORAGE -> chooseStorage()
            CLEANUP -> Api.call("settings_set", JSONObject().put("delete_watched", !cleanup)) { result ->
                val data = result.data
                if (!isAdded || data == null) return@call
                cleanup = data.optBoolean("delete_watched")
                showCleanup()
            }
            UPDATE -> Updater.check(activity, force = true)
            BACK -> activity.finish()
        }
    }
}
