# -*- coding: utf-8 -*-
"""Spolehlivost: zkouška Přehraj.to, hlášení z Diagnostiky a ochrana katalogu.

- **Zkouška Přehraj.to** (``prehrajto_test``): aplikace čte web Přehraj.to, a když web změní podobu
  stránek, hledání přestane fungovat bez varování. Zkouška pustí pár běžných hledání a podívá se na
  jednu stránku videa; řekne, jestli výsledky vypadají tak, jak je aplikace čeká.
- **Hlášení** (``diagnostics_report``): všechno, co je v Diagnostice, jako text – celý do souboru na
  disku a zkráceně pro kód QR, který jde přečíst telefonem.
- **Ochrana katalogu** (``snapshot``, ``guard``): jednou denně si aplikace uloží ověřenou kopii
  katalogu (poslední tři). Při startu katalog zkontroluje, a když je poškozený, nahradí ho poslední
  dobrou kopií – jinak by každá obrazovka skončila chybou databáze.
"""

import json
import os
import re
import shutil
import sqlite3
import time
import unicodedata

import provider

import app_api as api

SNAPSHOTS = "snapshots"            # složka s kopiemi katalogu (uvnitř dat aplikace)
SNAPSHOT_EVERY = 20 * 3600         # nová kopie nejdřív po této době
SNAPSHOT_KEEP = 3
SNAPSHOT_NAME = re.compile(r"^catalog-\d{8}-\d{6}\.db$")
BROKEN = "catalog-poskozeny.db"    # poškozený katalog odložený stranou (jen ten poslední)
SITE_KEY = "health:site"           # výsledek poslední zkoušky Přehraj.to
TEST_QUERIES = ("Pelíšky", "Shrek", "Harry Potter")   # běžné tituly: něco k nim na Přehraj.to bývá vždy
REPORT_PREFIX = "promitarna-hlaseni-"
QR_LIMIT = 900                     # tolik znaků se vejde do kódu, který jde z televize spolehlivě přečíst
QR_ERRORS = 4


# ---------------------------------------------------------------------------
# Ochrana katalogu
# ---------------------------------------------------------------------------

def _healthy(path):
    """Je databáze v souboru v pořádku? ``None`` = nejde zjistit (zamčená, chyba čtení z disku…) –
    za poškozenou se bere jen tehdy, když to řekne sama databáze, ne kvůli přechodné chybě."""
    try:
        connection = sqlite3.connect(path, timeout=5)
        try:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        text = str(error).lower()
        return False if ("malformed" in text or "not a database" in text or "corrupt" in text) else None


def _snapshot_dir(directory=None):
    return os.path.join(directory or api._STATE["dir"], SNAPSHOTS)


def _snapshots(directory=None):
    """Uložené kopie katalogu, nejnovější první."""
    folder = _snapshot_dir(directory)
    try:
        names = sorted((name for name in os.listdir(folder) if SNAPSHOT_NAME.match(name)), reverse=True)
    except OSError:
        return []
    return [os.path.join(folder, name) for name in names]


def snapshot(force=False):
    """Uloží kopii katalogu, pokud ta poslední není čerstvá. Kopie se před uložením zkontroluje:
    poškozený katalog se mezi dobré kopie nedostane. Vrací cestu nové kopie, nebo ``None``."""
    existing = _snapshots()
    if existing and not force:
        try:
            if time.time() - os.path.getmtime(existing[0]) < SNAPSHOT_EVERY:
                return None
        except OSError:
            pass
    folder = _snapshot_dir()
    os.makedirs(folder, exist_ok=True)
    temporary = os.path.join(folder, "catalog.tmp")
    try:
        with api._library() as lib:
            destination = sqlite3.connect(temporary)
            try:
                lib.conn.backup(destination)
            finally:
                destination.close()
        if _healthy(temporary) is not True:
            raise sqlite3.DatabaseError("kopie katalogu neprošla kontrolou")
        path = os.path.join(folder, time.strftime("catalog-%Y%m%d-%H%M%S.db"))
        os.replace(temporary, path)
    except (OSError, sqlite3.Error) as error:
        _remove(temporary)
        api._remember_error("kopie katalogu", error)
        return None
    for old in _snapshots()[SNAPSHOT_KEEP:]:
        _remove(old)
    return path


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def guard(directory):
    """Při startu aplikace: je-li katalog poškozený, nahradí ho poslední dobrou kopií (není-li žádná,
    začne se s prázdným). Poškozený soubor zůstane odložený stranou. Vrací, co se stalo: ``None``
    (v pořádku nebo nejde zjistit), ``"restored"``, nebo ``"emptied"``."""
    path = os.path.join(directory, "catalog.db")
    if not os.path.exists(path) or _healthy(path) is not False:
        return None
    time.sleep(0.3)
    if _healthy(path) is not False:      # napodruhé v pořádku: byla to přechodná chyba, ne poškození
        return None
    try:
        os.replace(path, os.path.join(directory, BROKEN))
    except OSError:
        return None
    for suffix in ("-wal", "-shm"):
        _remove(path + suffix)
    for copy in _snapshots(directory):
        if _healthy(copy):
            try:
                shutil.copyfile(copy, path)
            except OSError:
                continue
            stamp = time.strftime("%d.%m. %H:%M", time.localtime(os.path.getmtime(copy)))
            api._remember_error("katalog", "Katalog byl poškozený; obnoven z kopie z %s." % stamp)
            return "restored"
    api._remember_error("katalog", "Katalog byl poškozený a žádná kopie není; začíná se s prázdným.")
    return "emptied"


def snapshots_text():
    """Řádek do Diagnostiky."""
    copies = _snapshots()
    if not copies:
        return "zatím žádná (první vznikne po otevření aplikace)"
    try:
        newest = time.strftime("%d.%m. %H:%M", time.localtime(os.path.getmtime(copies[0])))
    except OSError:
        newest = "?"
    return "%d uložené, poslední %s" % (len(copies), newest)


# ---------------------------------------------------------------------------
# Zkouška Přehraj.to
# ---------------------------------------------------------------------------

def prehrajto_test(params):
    """Zkusí pár hledání a jednu stránku videa. Vrací ``{"lines": [{"label", "value", "ok"}], "ok"}``."""
    import app_discover as discover
    lines = []

    def add(label, value, ok=None):
        lines.append({"label": label, "value": value, "ok": ok})

    wait = discover.limited()
    if wait:
        add("Zkouška Přehraj.to", "neproběhla: Přehraj.to teď omezuje počet dotazů, zkuste to asi za %s"
            % discover._duration_text(wait + 59))
        return {"lines": lines, "ok": None}
    client = discover._search_client(timeout=8)
    found, answered, sample = 0, 0, None
    for query in TEST_QUERIES:
        try:
            results, _cursor = provider.search_catalog(client, query, 1, "")
        except provider.ProviderError as error:
            if discover._is_limit(error):
                discover._rate_limited()
                add("Hledání „%s“" % query, "Přehraj.to odmítlo dotaz (omezuje jejich počet)", False)
                break
            add("Hledání „%s“" % query, "selhalo: %s" % str(error)[:120], False)
            continue
        answered += 1
        good = [result for result in results if result.get("id") and result.get("title")]
        found += len(good)
        timed = len([result for result in good if result.get("duration")])
        sized = len([result for result in good if result.get("size")])
        labelled = len([result for result in good if result.get("quality")])
        # výsledky mají mít aspoň u poloviny souborů délku i velikost: bez nich aplikace neumí vybírat verze
        usable = bool(good) and timed * 2 >= len(good) and sized * 2 >= len(good)
        add("Hledání „%s“" % query, "%d souborů; délku má %d, velikost %d, štítek kvality %d" % (
            len(good), timed, sized, labelled) if good else "nic nenalezeno", usable)
        sample = sample or (good[0] if good else None)
    page_ok = None
    if sample:
        try:
            streams, _message = client.get_streams(sample["id"])
            kinds = ", ".join(str(stream.get("resolution") or stream.get("quality") or "?") for stream in streams[:6])
            page_ok = bool(streams)
            add("Stránka videa", "%d verzí k přehrání (%s)" % (len(streams), kinds) if streams
                else "žádná verze k přehrání", page_ok)
        except provider.ProviderError as error:
            page_ok = False
            add("Stránka videa", "nejde načíst: %s" % str(error)[:120], False)
    searches_ok = bool(answered) and all(line["ok"] for line in lines if line["label"].startswith("Hledání"))
    if searches_ok and page_ok:
        verdict, ok = "Přehraj.to odpovídá tak, jak aplikace čeká.", True
    elif answered and not found:
        verdict, ok = ("Hledání nevrací nic. Buď se změnila podoba webu Přehraj.to, nebo je zařízení mimo "
                       "Česko a Slovensko, nebo web dotazy omezuje."), False
    elif not answered:
        verdict, ok = "Přehraj.to neodpovídá – zkontrolujte připojení a zkuste to znovu.", False
    else:
        verdict, ok = ("Část zkoušky neprošla: web Přehraj.to nejspíš změnil podobu stránek. Pošlete hlášení, "
                       "ať se dá aplikace upravit."), False
    add("Výsledek zkoušky", verdict, ok)
    with api._library() as lib:
        lib.set_kv(SITE_KEY, json.dumps({"ts": int(time.time()), "ok": ok, "lines": lines}))
    return {"lines": lines, "ok": ok}


def site_text():
    """Výsledek poslední zkoušky Přehraj.to – řádek do Diagnostiky: ``(text, v pořádku?)``."""
    with api._library() as lib:
        try:
            last = json.loads(lib.get_kv(SITE_KEY) or "{}")
        except ValueError:
            last = {}
    if not isinstance(last, dict) or not last.get("ts"):
        return "zatím neproběhla (tlačítko dole)", None
    verdict = next((line.get("value") for line in last.get("lines") or [] if line.get("label") == "Výsledek zkoušky"), "")
    return "%s – %s" % (time.strftime("%d.%m. %H:%M", time.localtime(last["ts"])), verdict), last.get("ok")


def _site_lines():
    with api._library() as lib:
        try:
            last = json.loads(lib.get_kv(SITE_KEY) or "{}")
        except ValueError:
            last = {}
    return [line for line in (last.get("lines") or []) if isinstance(line, dict)] if isinstance(last, dict) else []


# ---------------------------------------------------------------------------
# Hlášení
# ---------------------------------------------------------------------------

def _plain(text):
    """Text bez diakritiky a zvláštních znaků: v kódu QR pak zabere každý znak jeden bajt."""
    folded = unicodedata.normalize("NFKD", text.replace("„", '"').replace("“", '"').replace("–", "-")
                                   .replace("·", "-").replace("→", "->").replace("×", "x"))
    return "".join(char for char in folded if not unicodedata.combining(char) and (char == "\n" or 32 <= ord(char) < 127))


def _mark(ok):
    return "+" if ok is True else "!" if ok is False else "-"


def diagnostics_report(params):
    """Hlášení z Diagnostiky: ``{"text": celé, "qr": zkrácené pro kód QR, "saved": soubor, "where": disk}``.

    ``device`` jsou řádky, které zná jen aplikace (verze, zařízení, Widevine…), ``dirs`` složky na
    discích, kam smí zapisovat (``[{"dir", "label"}]``) – hlášení se uloží do první, kde to jde."""
    state = api.diagnostics({})
    rows = [row for row in params.get("device") or [] if isinstance(row, dict)] + state["lines"]
    tested = [dict(row, label="Zkouška: " + str(row.get("label") or "")) for row in _site_lines()
              if row.get("label") != "Výsledek zkoušky"]
    stamp = time.strftime("%d.%m.%Y %H:%M")
    full = ["Promítárna – hlášení z %s" % stamp, ""]
    full += ["%s %s: %s" % (_mark(row.get("ok")), row.get("label"), row.get("value")) for row in rows + tested]
    full += ["", "Poslední chyby (nejnovější nahoře):" if state["errors"] else "Žádné zaznamenané chyby."]
    full += state["errors"]
    text = "\n".join(full) + "\n"
    # Do kódu QR jen to podstatné a v pořadí důležitosti (konec se při délce uřízne): co je špatně
    # celé, pak poslední chyby, a až nakonec zkráceně ostatní řádky.
    everything = rows + tested
    short = ["Promitarna %s" % stamp]
    short += [("%s %s: %s" % (_mark(row.get("ok")), row.get("label"), row.get("value")))[:160]
              for row in everything if row.get("ok") is False]
    short += [error[:150] for error in state["errors"][:QR_ERRORS]]
    short += [("%s %s: %s" % (_mark(row.get("ok")), row.get("label"), row.get("value")))[:70]
              for row in everything if row.get("ok") is not False]
    code = _plain("\n".join(short))
    if len(code) > QR_LIMIT:
        code = code[:QR_LIMIT - 12].rstrip() + "\n(zkraceno)"
    saved, where = "", ""
    name = REPORT_PREFIX + time.strftime("%Y%m%d-%H%M%S") + ".txt"
    for place in params.get("dirs") or []:
        folder = str((place or {}).get("dir") or "")
        if not folder:
            continue
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            continue
        saved, where = name, str((place or {}).get("label") or "disk")
        break
    return {"text": text, "qr": code, "saved": saved, "where": where}


ACTIONS = {"prehrajto_test": prehrajto_test, "diagnostics_report": diagnostics_report}
