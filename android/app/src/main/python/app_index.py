# -*- coding: utf-8 -*-
"""Seznam dostupných titulů: aplikace si ho na pozadí plní sama.

Výpisy v Objevuj ukazují jen tituly, o kterých se ví, že jsou na Přehraj.to nebo v iVysílání
(``app_discover``). Kdyby se ověřovalo až při otevření výpisu, čekalo by se u každého. Tento modul
proto seznamy TMDB – populární, novinky, nejlépe hodnocené, československé a jednotlivé žánry, filmy
i seriály – prochází předem, šetrným tempem, a co zjistí, ukládá do stejných záznamů (``avail:…``).
Výpis, který už je projitý, se pak ukáže hned; a protože se prochází znovu každý den, přibudou do něj
samy i tituly, které se na Přehraj.to objevily nově (řada „Nově na Přehraj.to“).

Prochází se jen, dokud aplikace běží, nikdy během přehrávání ani když na výsledek hledání někdo čeká.

Vedle seznamů TMDB se čte i to, co nabízí samo Přehraj.to (``harvest``): videa z jeho úvodní stránky
a z výpisů, na které odkazuje. Tak se do seznamu dostanou i tituly, které v žebříčcích TMDB nejsou.
"""

import json
import threading
import time

import nameparser
import provider
import search_results

import app_api as api
import app_discover as discover
from tmdb import TmdbError

HARVEST_KEY = "index:harvest"    # co naposledy nabídlo samo Přehraj.to (zpráva pro Diagnostiku)
HARVEST_EVERY = 6 * 3600         # nabídka Přehraj.to se čte nejvýš jednou za tuto dobu
HARVEST_TITLES = 40              # tolik titulů z ní se nejvýš zkusí přiřadit a ověřit
STATE_KEY = "index:state"        # kde se v plánu skončilo a kolik se dnes ověřilo
CYCLE_EVERY = 20 * 3600          # celý plán se prochází znovu nejdřív po této době
DAILY_LIMIT = 1500               # tolik titulů nejvýš se za den ověří na pozadí
CHUNK = 2                        # tolik titulů najednou…
GAP = 2.0                        # …a pak tolik sekund pauza: asi titul za sekundu
START_DELAY = 20                 # s po otevření aplikace, než se začne procházet
FAILURES_MAX = 3                 # po tolika nepovedených dávkách za sebou se procházení odloží
FRESH_DAYS = 30                  # jak dlouho je titul „nově na Přehraj.to“
FRESH_LIMIT = 60                 # tolik titulů nejvýš ve výpisu novinek
# (parametry výpisu, kolik stránek) – nejdřív to, co je vidět na hlavní obrazovce a v novinkách
LISTS = (({"category": "popular"}, 2), ({"category": "recent"}, 2), ({"category": "popular"}, 8),
         ({"category": "recent"}, 4), ({"category": "top_rated"}, 5), ({"origin": discover.CZECHOSLOVAK}, 4))
GENRE_PAGES = 2

_CRAWL = {"thread": None, "stop": False}
_LOCK = threading.Lock()


def _plan():
    """Co všechno se prochází: ``[(druh, parametry výpisu, stránka)]`` v pořadí důležitosti."""
    steps = []
    for params, pages in LISTS:
        for kind in ("movie", "tv"):
            for page in range(1, pages + 1):
                step = (kind, dict(params, kind=kind), page)
                if step not in steps:
                    steps.append(step)
    for kind in ("movie", "tv"):
        try:
            genres = [genre["id"] for genre in discover._tmdb().genres(kind)
                      if isinstance(genre, dict) and genre.get("id")]
        except (TmdbError, api.ApiError):
            genres = []
        for genre in genres:
            steps += [(kind, {"kind": kind, "genre": str(genre)}, page) for page in range(1, GENRE_PAGES + 1)]
    return steps


def _state():
    with api._library() as lib:
        try:
            state = json.loads(lib.get_kv(STATE_KEY) or "{}")
        except ValueError:
            state = {}
    return state if isinstance(state, dict) else {}


def _save(state):
    with api._library() as lib:
        lib.set_kv(STATE_KEY, json.dumps(state))


def _today():
    return time.strftime("%Y-%m-%d")


def _wanted(known, kind, item):
    """Titul, o kterém seznam ještě neví (nebo už údaj zestárl), nebo o kterém neví z iVysílání."""
    entry = known.get("%s:%s" % (kind, item["id"]))
    return entry is None or entry["iv"] is None


def busy():
    """Má teď přednost něco jiného? Hledání, na které někdo čeká, přehrávání, nebo ověřování výpisu,
    na který se člověk dívá."""
    return discover._VERIFY.get("hold", 0) > time.time() or bool(discover._VERIFY["queue"]) \
        or bool(discover._VERIFY["active"])


def step(limit=None):
    """Projde další stránku plánu: ověří tituly, o kterých seznam neví. Vrací ``{"done": plán je celý
    projitý, "checked": kolik titulů se ověřovalo, "failed": ověřit se nepodařilo}``.

    ``limit`` omezí počet ověřovaných titulů (zbytek stránky se dodělá příště)."""
    state, today = _state(), _today()
    if state.get("day") != today:
        state.update(day=today, count=0)
    plan = _plan()
    position = int(state.get("step") or 0)
    if position >= len(plan):
        if time.time() - float(state.get("finished") or 0) < CYCLE_EVERY:
            return {"done": True, "checked": 0, "failed": False}
        position = 0                       # nový průchod: co je ověřené a platí, se přeskočí
        state["started"] = int(time.time())
    kind, params, page = plan[position]
    try:
        items, _pages = discover._list_items(dict(params, page=page))
    except (TmdbError, api.ApiError):
        return {"done": False, "checked": 0, "failed": True}
    items = [item for item in items if isinstance(item, dict) and item.get("id")]
    with api._library() as lib:
        shared = api._state(lib)
        known = discover._known(lib, kind, [item["id"] for item in items])
    todo = [item for item in items if _wanted(known, kind, item)]
    room = max(0, DAILY_LIMIT - int(state.get("count") or 0))
    batch = todo[:min(room, limit) if limit is not None else room]
    checked, failed = 0, False
    for start in range(0, len(batch), CHUNK):
        if _CRAWL["stop"] or (start and busy()):
            batch = batch[:start]
            break
        chunk = batch[start:start + CHUNK]
        fresh = discover._check_available(kind, chunk, shared)
        checked += len(chunk)
        if any("%s:%s" % (kind, item["id"]) not in fresh for item in chunk):
            failed = True                  # služba neodpovídá: nemá cenu pokračovat
            break
        if start + CHUNK < len(batch):
            time.sleep(GAP)
    state["count"] = int(state.get("count") or 0) + checked
    if not failed and len(batch) == len(todo):
        position += 1                      # stránka je hotová
        if position >= len(plan):
            state["finished"] = int(time.time())
    state["step"] = position
    _save(state)
    return {"done": position >= len(plan) or int(state["count"]) >= DAILY_LIMIT,
            "checked": checked, "failed": failed}


def _match(tmdb, group):
    """Titul TMDB, který odpovídá názvu souboru – jen při jisté shodě: stejný název (český nebo
    původní) a u filmu i rok (soubor ho musí uvádět, nejvýš o rok jinak). Jinak ``None``: raději
    titul vynechat než přiřadit cizí."""
    series = group["kind"] == "tv"
    if not series and not group["year"]:
        return None
    target = search_results.normalize(group["title"])
    for item in tmdb.candidates(group["title"], group["year"], want_series=series):
        names = {search_results.normalize(item.get(name) or "")
                 for name in ("title", "name", "original_title", "original_name")}
        date = item.get("release_date") or item.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else 0
        if target in names and (series or abs(year - group["year"]) <= 1):
            return item
    return None


def harvest(force=False):
    """Zařadí do seznamu to, co nabízí samo Přehraj.to (úvodní stránka a výpisy, na které odkazuje).

    Názvy souborů se rozeberou (``nameparser``), přiřadí k titulům TMDB (``_match``) a tituly se
    ověří běžnou cestou – tím se dostanou do výpisů a do řady „Nově na Přehraj.to“. Podobu stránek
    neznáme předem: když na nich videa nejsou nebo je nejde přiřadit, nestane se nic a zpráva
    v Diagnostice řekne, co web nabídl. Vrací zprávu, nebo ``None``, když ještě není čas."""
    with api._library() as lib:
        try:
            last = json.loads(lib.get_kv(HARVEST_KEY) or "{}")
        except ValueError:
            last = {}
        shared = api._state(lib)
    now = time.time()
    if not force and now - float((last or {}).get("ts") or 0) < HARVEST_EVERY:
        return None
    report = {"ts": int(now), "files": 0, "titles": 0, "matched": 0, "pages": []}
    try:
        videos, pages = provider.offered(discover._search_client())
        report["pages"] = [[name, count] for name, _url, count in pages]
        report["files"] = len(videos)
        groups = {}
        for video in videos:
            raw = video.get("title") or ""
            parsed = nameparser.parse(raw)
            if not parsed["title"] or api.CAM_RE.search(raw) or search_results.is_preview(raw, (parsed["title"],)):
                continue
            kind = "tv" if parsed["kind"] == "episode" else "movie"
            key = (kind, search_results.normalize(parsed["title"]), parsed["year"] if kind == "movie" else 0)
            groups.setdefault(key, {"kind": kind, "title": parsed["title"], "year": key[2]})
        report["titles"] = len(groups)
        tmdb = discover._tmdb()
        matched = {"movie": [], "tv": []}
        for group in list(groups.values())[:HARVEST_TITLES]:
            item = _match(tmdb, group)
            if item and item["id"] not in [known["id"] for known in matched[group["kind"]]]:
                matched[group["kind"]].append(dict(item, _fresh=True))
        report["matched"] = len(matched["movie"]) + len(matched["tv"])
        for kind, items in matched.items():
            with api._library() as lib:
                known = discover._known(lib, kind, [item["id"] for item in items])
            todo = [item for item in items if _wanted(known, kind, item)]
            for start in range(0, len(todo), CHUNK):
                if _CRAWL["stop"]:
                    break
                discover._check_available(kind, todo[start:start + CHUNK], shared)
                if start + CHUNK < len(todo):
                    time.sleep(GAP)
    except (provider.ProviderError, TmdbError, api.ApiError) as error:
        report["error"] = str(error)[:200]
    with api._library() as lib:
        lib.set_kv(HARVEST_KEY, json.dumps(report))
    return report


def harvest_text():
    """Co naposledy nabídlo samo Přehraj.to – řádek do Diagnostiky."""
    with api._library() as lib:
        try:
            report = json.loads(lib.get_kv(HARVEST_KEY) or "{}")
        except ValueError:
            report = {}
    if not report:
        return "zatím se nečetla"
    when = time.strftime("%d.%m. %H:%M", time.localtime(report.get("ts") or 0))
    if report.get("error"):
        return "%s – nepodařilo se: %s" % (when, report["error"])
    pages = ", ".join("%s (%s)" % (name, count if count >= 0 else "chyba") for name, count in report.get("pages") or [])
    return "%s – souborů %d, titulů %d, přiřazeno %d; stránky: %s" % (
        when, report.get("files") or 0, report.get("titles") or 0, report.get("matched") or 0, pages or "žádné")


def _loop():
    failures = 0
    try:
        time.sleep(START_DELAY)            # start aplikace má přednost: nejdřív ať se načte, co je na obrazovce
        try:
            if not busy():
                harvest()                  # nejdřív to, co nabízí samo Přehraj.to
        except Exception as error:         # neznámá podoba stránek nesmí zastavit zbytek
            api._remember_error("nabídka Přehraj.to", error)
        while not _CRAWL["stop"]:
            if busy():
                time.sleep(2)
                continue
            try:
                result = step()
            except Exception as error:     # vlákno na pozadí nesmí shodit aplikaci
                api._remember_error("seznam dostupných titulů", error)
                return
            if result["done"]:
                return
            failures = failures + 1 if result["failed"] else 0
            if failures >= FAILURES_MAX:
                return                     # služba neodpovídá: zkusí se to při příštím otevření aplikace
            time.sleep(GAP if not result["failed"] else discover.CHECK_PAUSE / 4.0)
    finally:
        with _LOCK:
            _CRAWL["thread"] = None


def start():
    """Spustí procházení na pozadí, pokud neběží a je co procházet. Volá se po otevření aplikace."""
    if not api._tmdb().enabled or not discover.BACKGROUND:
        return False
    state = _state()
    if state.get("day") == _today() and int(state.get("count") or 0) >= DAILY_LIMIT:
        return False
    with _LOCK:
        if _CRAWL["thread"] is not None:
            return False
        _CRAWL["stop"] = False
        thread = threading.Thread(target=_loop, name="seznam-dostupnych")
        thread.daemon = True
        _CRAWL["thread"] = thread
        thread.start()
    return True


def _entries():
    """Všechny záznamy seznamu: ``[(druh, číslo TMDB, záznam)]``."""
    with api._library() as lib:
        rows = lib.conn.execute("SELECT k, v FROM settings_kv WHERE k LIKE ?",
                                (discover.AVAILABLE_PREFIX + "%",)).fetchall()
    found = []
    for key, value in rows:
        parts = key.split(":")
        try:
            entry = json.loads(value or "null")
        except ValueError:
            continue
        if len(parts) == 3 and parts[2].isdigit() and isinstance(entry, dict):
            found.append((parts[1], int(parts[2]), entry))
    return found


def stats():
    """Stav seznamu pro Diagnostiku."""
    entries = _entries()
    state = _state()
    plan_done = int(state.get("step") or 0)
    return {"known": len(entries), "available": len([1 for _kind, _id, entry in entries if entry.get("n")]),
            "today": int(state.get("count") or 0) if state.get("day") == _today() else 0,
            "finished": int(state.get("finished") or 0), "step": plan_done,
            "running": _CRAWL["thread"] is not None}


def summary_text():
    info = stats()
    when = time.strftime("%d.%m. %H:%M", time.localtime(info["finished"])) if info["finished"] else "ještě ne"
    return "ověřeno %d titulů, k mání %d; dnes ověřeno %d; celý průchod dokončen: %s%s" % (
        info["known"], info["available"], info["today"], when, " (právě běží)" if info["running"] else "")


def _fresh(kind=None):
    """Tituly, které jsou na Přehraj.to nově (viz ``f`` v záznamu), od nejnovějšího."""
    since = time.time() - FRESH_DAYS * 86400
    found = [(entry["f"], item_kind, tmdb_id, entry) for item_kind, tmdb_id, entry in _entries()
             if entry.get("n") and entry.get("f", 0) >= since and entry.get("t") and (not kind or item_kind == kind)]
    found.sort(key=lambda row: (-row[0], row[2]))
    return found[:FRESH_LIMIT]


def _fresh_cards(kind=None, limit=None):
    with api._library() as lib:
        state = api._state(lib)
    rows = _fresh(kind)[:limit]
    known = {"%s:%s" % (item_kind, tmdb_id): {"n": entry.get("n") or 0, "iv": entry.get("iv") or None,
                                               "d": entry.get("d") or "", "q": int(entry.get("q") or 0)}
             for _when, item_kind, tmdb_id, entry in rows}
    discover._mark_known(state, known)
    cards = []
    for _when, item_kind, tmdb_id, entry in rows:
        poster = entry.get("p") or ""
        cards.append(discover._title_card(
            item_kind, tmdb_id, entry["t"], int(entry.get("y") or 0),
            discover.IMG + "w342" + poster if poster.startswith("/") else "", entry.get("o") or "", state,
            rating=int(entry.get("v") or 0)))
    return cards


def discover_fresh(params):
    """Výpis „Nově na Přehraj.to“: tituly, které se tam objevily v posledních týdnech."""
    kind = params.get("kind") if params.get("kind") in ("movie", "tv") else None
    return {"items": _fresh_cards(kind), "next": None,
            "empty": "Zatím tu nic není. Seznam se plní sám na pozadí – nové tituly se objeví, "
                     "jakmile je aplikace na Přehraj.to najde."}


def discover_fresh_row(params):
    """Řada „Nově na Přehraj.to“ na hlavní obrazovce; prázdná se neukazuje (``hide``)."""
    cards = _fresh_cards(limit=api.HOME_ROW)
    if not cards:
        return {"items": [], "hide": True}
    cards.append(api._card("Zobrazit vše", api._target("list", "discover_fresh", {}, "Nově na Přehraj.to"),
                           "Filmy a seriály, které přibyly", icon="all"))
    return {"items": cards}


ACTIONS = {"discover_fresh": discover_fresh, "discover_fresh_row": discover_fresh_row}
