# -*- coding: utf-8 -*-
"""Jediný zdroj textů doplňků. Spuštěním se vygenerují strings.po (cs_CZ, en_GB) pro každý doplněk.

``{svc}`` se nahradí názvem služby, ``OVERRIDES`` mění texty jen pro jeden doplněk:

    python3 tools/strings.py
"""
import os

STRINGS = {
    # --- nastavení ---
    30000: ("Account", "Účet"),
    30001: ("Username", "Uživatelské jméno"),
    30002: ("Password", "Heslo"),
    30003: ("Server location", "Lokalita serveru"),
    30004: ("Location 1", "Lokalita 1"),
    30005: ("Location 2", "Lokalita 2"),
    30006: ("Premium valid until (filled by account check)", "Premium platné do (vyplní ověření účtu)"),
    30007: ("Account and login", "Účet a přihlášení"),
    30010: ("Synchronization", "Synchronizace"),
    30011: ("Also load favourite videos", "Načítat i oblíbená videa"),
    30012: ("Import playlists as collections", "Načítat playlisty jako kolekce"),
    30013: ("Remove videos deleted from the account", "Odstraňovat z katalogu videa smazaná z účtu"),
    30014: ("Automatic synchronization in the background", "Automatická synchronizace na pozadí"),
    30015: ("Synchronization interval (hours)", "Interval synchronizace (hodiny)"),
    30016: ("Fetch ratings and plots after synchronization", "Po synchronizaci doplnit hodnocení a popisy"),
    30020: ("Metadata", "Metadata"),
    30021: ("Use ČSFD", "Používat ČSFD"),
    30022: ("TMDB API key (optional fallback source)", "TMDB API klíč (volitelný záložní zdroj)"),
    30023: ("Show rating in titles", "Zobrazovat hodnocení v názvech"),
    30024: ("Ratings and descriptions", "Hodnocení a popisy"),
    30030: ("Playback", "Přehrávání"),
    30031: ("Preferred language", "Preferovaný jazyk"),
    30032: ("Preferred quality", "Preferovaná kvalita"),
    30033: ("Always offer stream selection", "Vždy nabídnout výběr streamu"),
    30034: ("Load subtitles", "Načítat titulky"),
    30035: ("Best available", "Nejlepší dostupná"),
    30036: ("Any", "Libovolný"),
    30037: ("Czech", "Čeština"),
    30038: ("Slovak", "Slovenština"),
    30039: ("English", "Angličtina"),
    30040: ("Kodi library", "Knihovna Kodi"),
    30041: ("Export folder", "Složka pro export"),
    30042: ("Stream", "Stream"),
    30043: ("2160p (UHD)", "2160p (UHD)"),
    30044: ("1080p (Full HD)", "1080p (Full HD)"),
    30045: ("720p (HD)", "720p (HD)"),
    30046: ("480p (SD)", "480p (SD)"),
    # --- menu ---
    30100: ("Movies", "Filmy"),
    30101: ("TV shows", "Seriály"),
    30102: ("Search", "Hledat"),
    30103: ("Collections", "Kolekce"),
    30104: ("Recently added", "Nově přidané"),
    30105: ("Unrecognized titles", "Nerozpoznané tituly"),
    30106: ("Tools", "Nástroje"),
    30107: ("All movies", "Všechny filmy"),
    30108: ("Top rated", "Nejlépe hodnocené"),
    30109: ("By genre", "Podle žánru"),
    30110: ("By decade", "Podle desetiletí"),
    30111: ("By country", "Podle země"),
    30112: ("By rating", "Podle hodnocení"),
    30113: ("All TV shows", "Všechny seriály"),
    30114: ("Season %d", "%d. série"),
    30115: ("Other videos", "Ostatní videa"),
    30117: ("New search", "Nové hledání"),
    30118: ("Search the catalogue (title, actor, director, genre)", "Hledat v katalogu (název, herec, režisér, žánr)"),
    30121: ("Nothing found", "Nic nenalezeno"),
    30122: ("Remove from history", "Odstranit z historie"),
    30123: ("New collection", "Nová kolekce"),
    30124: ("Collection name", "Název kolekce"),
    30125: ("Rename collection", "Přejmenovat kolekci"),
    30126: ("Delete collection", "Smazat kolekci"),
    30127: ("Delete collection \"%s\"?", "Smazat kolekci „%s“?"),
    30128: ("Add to collection", "Přidat do kolekce"),
    30129: ("Remove from collection", "Odebrat z kolekce"),
    30130: ("Added to collection %s", "Přidáno do kolekce %s"),
    30131: ("Hidden videos", "Skrytá videa"),
    30132: ("The catalogue is empty. Run account synchronization in Tools or import links.",
            "Katalog je prázdný. Spusť synchronizaci s účtem v Nástrojích nebo importuj odkazy."),
    30133: ("%d videos", "Počet videí: %d"),
    30134: ("All videos", "Všechna videa"),
    30136: ("Continue watching", "Rozkoukané"),
    30137: ("Recently played", "Naposledy přehrané"),
    30138: ("Remove from playback history", "Odebrat z historie přehrávání"),
    30135: ("%d %% and more", "%d %% a více"),
    # --- nástroje ---
    30200: ("Synchronize with {svc} account", "Synchronizovat s účtem {svc}"),
    30201: ("Import links from file", "Importovat odkazy ze souboru"),
    30202: ("Add link", "Přidat odkaz"),
    30203: ("Fetch missing ratings and plots", "Doplnit chybějící hodnocení a popisy"),
    30204: ("Refresh all metadata", "Obnovit všechna metadata"),
    30205: ("Account status", "Stav účtu"),
    30206: ("Export to Kodi library", "Exportovat do knihovny Kodi"),
    30207: ("Clear catalogue", "Vymazat katalog"),
    30208: ("Settings", "Nastavení"),
    30209: ("Catalogue statistics", "Statistika katalogu"),
    30210: ("Synchronizing…", "Synchronizuji…"),
    30211: ("Page %d, found %d videos", "Stránka %d, nalezeno %d videí"),
    30212: ("Done: %d videos, %d new, %d removed", "Hotovo: %d videí, %d nových, %d odstraněno"),
    30213: ("Fetching ratings and plots", "Stahuji hodnocení a popisy"),
    30214: ("%d / %d: %s", "%d / %d: %s"),
    30215: ("Metadata: %d found (ČSFD %d, TMDB %d), %d not found, %d errors",
            "Metadata: %d nalezeno (ČSFD %d, TMDB %d), %d nenalezeno, %d chyb"),
    30216: ("ČSFD is currently blocking automated requests (anti-bot protection). The add-on does not "
            "circumvent it. Fill in a TMDB API key in settings to get ratings from TMDB, or enter ratings manually "
            "(context menu → Assign ČSFD / TMDB).",
            "ČSFD momentálně blokuje automatické dotazy (ochrana proti botům). Doplněk ji neobchází. "
            "Pro hodnocení z TMDB vyplň v nastavení TMDB API klíč, případně zadej hodnocení ručně "
            "(kontextové menu → Přiřadit ČSFD / TMDB)."),
    30217: ("Paste a {svc} video link (optionally: Title | link)",
            "Vlož odkaz na video z {svc} (případně: Název | odkaz)"),
    30218: ("Invalid link", "Neplatný odkaz"),
    30219: ("Imported: %d new, %d already in the catalogue", "Importováno: %d nových, %d už v katalogu"),
    30220: ("Choose a text file with links", "Vyber textový soubor s odkazy"),
    30221: ("No {svc} links found in the file", "V souboru nejsou žádné odkazy na {svc}"),
    30222: ("Fill in your {svc} login and password in the add-on settings first.",
            "Nejdřív vyplň přihlašovací údaje k {svc} v nastavení doplňku."),
    30223: ("Premium is active until %s", "Premium je aktivní do %s"),
    30224: ("The account has no active premium.", "Účet nemá aktivní premium."),
    30225: ("Wrong username or password.", "Špatné jméno nebo heslo."),
    30226: ("Really clear the whole catalogue? Videos on {svc} stay untouched.",
            "Opravdu vymazat celý katalog? Videa na {svc} zůstanou beze změny."),
    30227: ("Exported %d movies and %d TV shows to %s", "Exportováno %d filmů a %d seriálů do %s"),
    30228: ("Set the export folder in the add-on settings first.", "Nejdřív nastav složku pro export v nastavení doplňku."),
    30229: ("Download metadata again for all titles? It may take a while.",
            "Znovu stáhnout metadata pro všechny tituly? Může to chvíli trvat."),
    30230: ("Videos: %d\nMovies: %d\nTV shows: %d\nWith rating: %d\nUnrecognized: %d",
            "Videí: %d\nFilmů: %d\nSeriálů: %d\nS hodnocením: %d\nNerozpoznaných: %d"),
    30231: ("Last synchronization: %s", "Poslední synchronizace: %s"),
    30232: ("never", "nikdy"),
    30233: ("Update the Kodi video library now?", "Aktualizovat teď knihovnu videí v Kodi?"),
    30234: ("Save diagnostics (for troubleshooting)", "Uložit diagnostiku (pro ladění)"),
    30235: ("Choose a folder for the diagnostic files", "Vyber složku pro diagnostické soubory"),
    30236: ("Diagnostics saved to %s. Login, e-mail, tokens and hidden form fields were removed.",
            "Diagnostika je uložená v %s. Přihlašovací údaje, e-mail, tokeny a skrytá pole formulářů byly odstraněny."),
    30237: ("Clear playback history", "Vymazat historii přehrávání"),
    30238: ("Clear the whole playback history?", "Vymazat celou historii přehrávání?"),
    # --- úpravy ---
    30300: ("Assign ČSFD / TMDB…", "Přiřadit ČSFD / TMDB…"),
    30301: ("Refresh metadata", "Obnovit metadata"),
    30302: ("Edit title and classification…", "Upravit název a zařazení…"),
    30303: ("Hide", "Skrýt"),
    30304: ("Unhide", "Zobrazit"),
    30305: ("Remove from catalogue", "Odebrat z katalogu"),
    30306: ("Search ČSFD", "Hledat na ČSFD"),
    30307: ("Enter ČSFD link or ID", "Zadat odkaz nebo ID z ČSFD"),
    30308: ("Search TMDB", "Hledat na TMDB"),
    30309: ("Enter rating manually", "Zadat hodnocení ručně"),
    30310: ("Search for", "Hledaný výraz"),
    30311: ("Rating (0–100 %)", "Hodnocení (0–100 %)"),
    30312: ("Type", "Typ"),
    30313: ("Movie", "Film"),
    30314: ("TV show episode", "Díl seriálu"),
    30315: ("Title", "Název"),
    30316: ("Year", "Rok"),
    30317: ("Season", "Série"),
    30318: ("Episode", "Díl"),
    30319: ("TV show title", "Název seriálu"),
    30320: ("Saved", "Uloženo"),
    30321: ("Choose stream", "Vyber stream"),
    30322: ("The video has no available streams", "Video nemá žádné dostupné streamy"),
    30323: ("Playback error: %s", "Chyba přehrávání: %s"),
    30325: ("Video details", "Podrobnosti o videu"),
    30326: ("Remove \"%s\" from the catalogue? The video on {svc} stays.",
            "Odebrat „%s“ z katalogu? Video na {svc} zůstane."),
    30327: ("TMDB API key is not set in the add-on settings.", "V nastavení doplňku není vyplněný TMDB API klíč."),
    30328: ("Error: %s", "Chyba: %s"),
    30329: ("Rating", "Hodnocení"),
    30330: ("File", "Soubor"),
    30331: ("Language", "Jazyk"),
    30332: ("Quality", "Kvalita"),
}

HEADER = '''# Kodi Media Center language file
# Addon Name: Streamuj.tv
# Addon id: plugin.video.streamujtv
# Addon Provider: petermikula-glitch
msgid ""
msgstr ""
"Project-Id-Version: plugin.video.streamujtv\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Language: {lang}\\n"
"Plural-Forms: nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;\\n"

'''


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


ADDONS = {
    "plugin.video.streamujtv": {"svc": "streamuj.tv", "name": "Streamuj.tv", "overrides": {}},
}


def main():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    for addon_id, conf in ADDONS.items():
        strings = dict(STRINGS)
        strings.update(conf["overrides"])
        base = os.path.join(root, addon_id, "resources", "language")
        for folder, lang, translate in (("resource.language.en_gb", "en_GB", False),
                                        ("resource.language.cs_cz", "cs_CZ", True)):
            header = HEADER.format(lang=lang).replace("Streamuj.tv", conf["name"]).replace(
                "plugin.video.streamujtv", addon_id)
            if not translate:
                header = header.replace("nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
                                        "nplurals=2; plural=(n != 1);")
            out = [header]
            for sid in sorted(strings):
                en, cs = (t.replace("{svc}", conf["svc"]) for t in strings[sid])
                out.append('msgctxt "#%d"\nmsgid "%s"\nmsgstr "%s"\n\n' % (sid, esc(en), esc(cs) if translate else ""))
            path = os.path.join(base, folder, "strings.po")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(out).rstrip("\n") + "\n")
            print("zapsáno", os.path.normpath(path))


if __name__ == "__main__":
    main()
