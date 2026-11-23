# -*- coding: utf-8 -*-
"""Objevuj a Oblíbené – část rozhraní aplikace (akce se registrují v app_api.ACTIONS).

Přehraj.to nemá katalog podle roku ani žánru, jen hledání podle názvu souboru. Seznamy titulů proto
dodává TMDB (podle roku, žánru, země původu) a teprve u vybraného titulu se na Přehraj.to hledají
jeho verze. Oblíbené jsou jen v aplikaci (favorites.json): položka katalogu, nebo titul z TMDB.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import csfd
import ivysilani
import nameparser
import provider
import search_results
from tmdb import IMG, TmdbError

import app_api as api

FAVORITES = "favorites.json"
CZECHOSLOVAK = "CZ|SK|XC"        # Česko, Slovensko a někdejší Československo (v TMDB kód XC)
AVAILABLE_PREFIX = "avail:"      # kolik verzí titulu se na Přehraj.to naposledy našlo a zda je v iVysílání
AVAILABLE_TTL = 7 * 24 * 3600
MISSING_TTL = 2 * 24 * 3600      # nenalezený titul se ověří znovu dřív: nové soubory přibývají
CHECK_WORKERS = 4                # kolik titulů se ověřuje souběžně
ONLY_ENOUGH = 12                 # výpis „jen dostupné“: tolik titulů stačí na jedno načtení…
ONLY_PAGES = 3                   # …a víc stránek TMDB kvůli nim najednou neprojde,
ONLY_SECONDS = 12                # ani když ověřování trvá déle než tolik sekund
CHECK_PAUSE = 120                # služba, která neodpovídá, se tolik sekund nezkouší (výpis na ni nečeká)
CHECK_WAIT = 20                  # déle výpis nečeká, až doběhne jiné ověřování
BACKGROUND = True                # výpis na ověřování nečeká: tituly ukáže hned a ověřuje je na pozadí
VERIFY_QUEUE = 120               # víc titulů na ověření nečeká (starší požadavky propadnou)
VERIFY_REFRESH = 2               # po kolika sekundách si má obrazovka říct o čerstvý stav
# Ověřování na pozadí: fronta titulů (naposledy otevřený výpis první), právě ověřované a ty, které se
# ověřit nepodařilo (chvíli se nezkoušejí, aby se výpis neobnovoval donekonečna).
_VERIFY = {"queue": [], "active": set(), "thread": None, "failed": {}}
_VERIFY_LOCK = threading.Lock()
_PAUSED = {}                     # {"prehrajto" | "ivysilani": do kdy se nezkouší}
_CHECK_LOCK = threading.Lock()   # ověřování běží nejvýš jedno (řady hlavní obrazovky se nepředbíhají)
CHECK_BATCH = 20                 # kolik titulů ověří jedno „Ověřit dostupnost“
FAVORITES_EVERY = 3 * 3600       # hlídání oblíbených nejvýš jednou za tuto dobu
FAVORITES_BATCH = 6              # a nejvýš tolika titulů najednou
SEASON_DOWNLOAD_MAX = 40         # nejvýš tolik dílů při stažení celé řady
# seriálové žánry TMDB bez vlastní ilustrace: nejbližší filmová, nebo dlaždice z iVysílání
GENRE_ART = {10759: "genre_28", 10765: "genre_878", 10768: "genre_10752", 10762: "kids", 10763: "news",
             10764: "fun", 10766: "genre_10749", 10767: "society"}   # reality show, telenovela, talk show
TITLE_PREFIX = "title:"          # název a plakát titulu z Objevuj (pro další díl a pokračování)
RECENT_SERIES = "recent_series"  # naposledy sledované seriály z Objevuj
KIND_NAMES = {"movie": "film", "tv": "seriál"}
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Oblíbené
# ---------------------------------------------------------------------------

def load_favorites():
    with _LOCK:
        try:
            with open(api._path(FAVORITES), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return {key: entry for key, entry in data.items() if isinstance(entry, dict)} if isinstance(data, dict) else {}


def _save_favorites(items):
    with _LOCK:
        temporary = api._path(FAVORITES + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(temporary, api._path(FAVORITES))


def favorite_item(favorites, entry):
    """Položka nabídky karty: přidat do oblíbených, nebo z nich odebrat."""
    present = entry["id"] in favorites
    return {"title": "Odebrat z oblíbených" if present else "Přidat do oblíbených",
            "target": api._target("command", "favorite_toggle", entry)}


def meta_favorite(meta):
    kind = "tv" if str(meta.get("key", "")).startswith("show:") else "movie"
    return {"id": "meta:%s" % meta["key"], "kind": kind, "key": meta["key"], "title": api._display_title(meta),
            "year": api._year(meta), "poster": meta.get("poster") or meta.get("thumb") or ""}


def tmdb_favorite(kind, tmdb_id, title, year, poster, orig=""):
    return {"id": "tmdb:%s:%s" % (kind, tmdb_id), "kind": kind, "tmdb_id": tmdb_id, "title": title, "year": year,
            "poster": poster, "orig": orig}


def favorite_toggle(params):
    fid = str(params.get("id") or "")
    if not fid:
        raise api.ApiError("Chybí položka k označení.")
    with _LOCK:
        items = load_favorites()
        if fid in items:
            del items[fid]
            message = "Odebráno z oblíbených."
        else:
            items[fid] = dict({key: params.get(key) for key in ("id", "kind", "key", "tmdb_id", "title", "year",
                                                                "poster", "orig") if params.get(key) is not None},
                              added=int(time.time()))
            message = "Přidáno do oblíbených: %s" % (params.get("title") or "")
        _save_favorites(items)
    return {"message": message}


def meets_preferences(video, prefs):
    """Je verze v jazyce a kvalitě, které má uživatel v předvolbách?"""
    if api.CAM_RE.search(video.get("raw_title") or ""):
        return False
    if prefs["language"] != "any" and api._language_rank(video.get("lang"), prefs["language"]) != 0:
        return False
    token = provider.quality_token(video.get("quality"))
    wanted = {"best": ("ORIG", "UHD", "FHD", "HD"), "1080": ("ORIG", "UHD", "FHD"),
              "720": ("ORIG", "UHD", "FHD", "HD")}.get(prefs["quality"])
    return wanted is None or token in wanted


def _preferred(versions, prefs):
    """První verze, která odpovídá předvolbám (bez ukázek a nefunkčních), nebo None."""
    return next((version for version in versions if not version["short"] and not version["bad"]
                 and meets_preferences(version["video"], prefs)), None)


def _prefs_mark(prefs):
    return "%s|%s" % (prefs["language"], prefs["quality"])


def _note_favorite(fid, versions, prefs):
    """U oblíbeného titulu si pamatuje, co bylo naposledy na Přehraj.to; otevřením mizí štítek „Nově“.

    ``avail`` je počet verzí, ``match`` říká, jestli mezi nimi byla verze podle předvoleb
    (``match_prefs`` – pro které předvolby to platí).
    """
    good = [version for version in versions if not version["short"]]
    with _LOCK:
        items = load_favorites()
        entry = items.get(fid)
        if entry is None:
            return
        entry.update(avail=len(good), match=int(bool(_preferred(good, prefs))), match_prefs=_prefs_mark(prefs),
                     checked=int(time.time()))
        entry.pop("fresh", None)
        entry.pop("fresh_note", None)
        _save_favorites(items)


def favorites_check(params):
    """Hlídání oblíbených filmů: objevila se na Přehraj.to verze v preferovaném jazyce a kvalitě?

    ``{"found": [{"title", "detail", "target"}]}`` – tituly, u kterých taková verze minule nebyla a teď je.
    Aplikace se ptá po startu a pak průběžně; logika hledá nejvýš jednou za FAVORITES_EVERY a jen pár
    titulů najednou (jedno hledání na film). Titul, který se ještě neověřoval, se jen zapamatuje –
    upozornění patří až změně. Nalezený titul má na kartě štítek „Nově“, dokud ho uživatel neotevře.
    """
    now = int(time.time())
    if not params.get("force") and now - int(api._settings().get("favorites_checked") or 0) < FAVORITES_EVERY:
        return {"found": []}
    api._change_settings(lambda settings: settings.update(favorites_checked=now))
    with api._library() as lib:
        state = api._state(lib)
    prefs, mark = state["prefs"], _prefs_mark(state["prefs"])
    # čekají tituly bez verze podle (současných) předvoleb; nejdéle neověřené první
    waiting = sorted((entry for entry in load_favorites().values()
                      if not entry.get("key") and entry.get("kind") == "movie" and entry.get("tmdb_id")
                      and not (entry.get("match") == 1 and entry.get("match_prefs") == mark)),
                     key=lambda entry: entry.get("checked") or 0)[:FAVORITES_BATCH]
    found = []
    for entry in waiting:
        try:
            versions = _find_versions([entry.get("title") or "", entry.get("orig") or ""],
                                      int(entry.get("year") or 0), None, state, pages=1, thorough=False)
        except api.ApiError:
            break  # hledání teď nejde; zkusí se příště
        good = [version for version in versions if not version["short"]]
        best = _preferred(good, prefs)
        with _LOCK:
            items = load_favorites()
            current = items.get(entry["id"])
            if current is None:
                continue
            # upozornit jen na změnu: minule (při stejných předvolbách) taková verze nebyla
            appeared = bool(best) and current.get("match") == 0 and current.get("match_prefs") == mark
            current.update(avail=len(good), match=int(bool(best)), match_prefs=mark, checked=now)
            if appeared:
                detail = " · ".join(part for part in (best["video"].get("lang"), best["video"].get("quality")) if part)
                current.update(fresh=True, fresh_note=detail)
                card = _title_card("movie", current.get("tmdb_id") or 0, current.get("title") or "",
                                   current.get("year") or 0, current.get("poster") or "", current.get("orig") or "",
                                   state, favorite=current)
                found.append({"title": current.get("title") or "", "detail": detail, "target": card["target"]})
            _save_favorites(items)
        _remember_availability("movie", entry["tmdb_id"], len(good))
    return {"found": found}


def favorite_cards(lib, state, limit=None, kind=None):
    """Karty oblíbených: položky katalogu se chovají jako v katalogu, tituly z TMDB vedou na své verze.

    ``kind`` (``movie`` / ``tv``) vybere jen filmy, nebo jen seriály.
    """
    # naposledy přidané první (při stejném čase rozhoduje pořadí přidání)
    entries = sorted(reversed(list(state["favorites"].values())), key=lambda entry: -(entry.get("added") or 0))
    if kind:
        entries = [entry for entry in entries if (entry.get("kind") == "tv") == (kind == "tv")]
    cards = []
    for entry in entries[:limit] if limit else entries:
        meta = lib.get_meta(entry["key"]) if entry.get("key") else None
        versions = lib.videos_grouped([meta["key"]]) if meta else {}
        if meta and entry.get("kind") == "tv":
            listed = lib.shows(keys=[meta["key"]])   # výpis seriálů doplňuje počet dílů
            cards.append(api._show_card(listed[0] if listed else meta, state))
        elif meta and versions.get(meta["key"]):
            cards.append(api._movie_card(meta, state, versions))
        else:  # titul z Objevuj, nebo položka, která už v katalogu není
            cards.append(_title_card(entry.get("kind") or "movie", entry.get("tmdb_id") or 0, entry.get("title") or "",
                                     entry.get("year") or 0, entry.get("poster") or "", entry.get("orig") or "",
                                     state, favorite=entry))
    return cards


def favorites(params):
    kind = params.get("kind") if params.get("kind") in ("movie", "tv") else None
    with api._library() as lib:
        # tituly z Přehraj.to a za nimi oblíbené pořady z iVysílání
        cards = favorite_cards(lib, api._state(lib), kind=kind) + api.ivys_favorite_cards(lib, kind)
    title = {"movie": "Oblíbené filmy", "tv": "Oblíbené seriály"}.get(kind, "Oblíbené")
    return {"title": title, "items": cards, "next": None, "empty_art": "favorites",
            "empty": "Zatím nemáte nic oblíbeného.\n\nPodržte tlačítko OK na filmu nebo seriálu "
                     "a zvolte „Přidat do oblíbených“."}


# ---------------------------------------------------------------------------
# Objevuj: seznamy titulů z TMDB
# ---------------------------------------------------------------------------

def _tmdb():
    client = api._tmdb()
    if not client.enabled:
        raise api.ApiError("Objevování potřebuje klíč TMDB. Vyplňte ho v Nastavení – je zdarma na themoviedb.org.")
    return client


def _title_card(kind, tmdb_id, title, year, poster, orig, state, rating=0, favorite=None):
    entry = favorite or tmdb_favorite(kind, tmdb_id, title, year, poster, orig)
    params = {"kind": kind, "id": tmdb_id, "title": title, "year": year, "poster": poster, "orig": orig}
    if not tmdb_id:  # oblíbená položka bez čísla TMDB: hledat rovnou podle názvu
        params["fid"] = entry["id"]
    target = api._target("title", "discover_title", params, title)
    subtitle = " · ".join(part for part in (str(year) if year else "", "%d %%" % rating if rating else "",
                                           KIND_NAMES.get(kind, "")) if part)
    menu = [{"title": "Otevřít", "target": target}, favorite_item(state.get("favorites", {}), entry)]
    known = state.get("available", {}).get("%s:%s" % (kind, tmdb_id))
    television = state.get("television", {}).get("%s:%s" % (kind, tmdb_id))
    # štítek říká, kde titul je; „Nenalezeno“ jen u ověřeného, který není nikde
    badges = [name for name, there in (("Přehraj.to", known), ("iVysílání", television)) if there] \
        or (["Nenalezeno"] if known == 0 else None)
    if "%s:%s" % (kind, tmdb_id) in state.get("checking", ()):
        badges = ["Ověřuji…"]   # dostupnost se právě zjišťuje na pozadí
    if favorite and favorite.get("fresh"):
        badges = ["Nově na Přehraj.to"]
        subtitle = " · ".join(part for part in (favorite.get("fresh_note") or "", subtitle) if part)
    if kind == "movie" and tmdb_id:
        # film jde pustit i bez otevření stránky: nejlepší verze podle předvoleb
        menu.insert(1, {"title": "Přehrát hned", "target": api._target("play", "discover_movie_play", params)})
    return api._card(title, target, subtitle, poster, wide=not poster, menu=menu,
                     icon="series" if kind == "tv" else "movies", badges=badges)


def _known(lib, kind, ids):
    """Co aplikace ví o dostupnosti titulů: ``{"movie:11": {"n": verzí na Přehraj.to, "iv": pořad
    v iVysílání | "" | None}}``; ``None`` = iVysílání se u titulu ještě neověřovalo. Nalezený titul
    platí týden, nenalezený dva dny."""
    known, now = {}, time.time()
    for tmdb_id in ids:
        key = "%s:%s" % (kind, tmdb_id)
        try:
            entry = json.loads(lib.get_kv(AVAILABLE_PREFIX + key) or "null")
        except ValueError:
            entry = None
        if not isinstance(entry, dict):
            continue
        count = int(entry.get("n") or 0)
        television = entry.get("iv") if isinstance(entry.get("iv"), str) else None
        # údaj o iVysílání má vlastní stáří: záznam se přepisuje i tehdy, když se iVysílání neověřovalo
        if television is not None and \
                now - (entry.get("its") or entry.get("ts") or 0) >= (AVAILABLE_TTL if television else MISSING_TTL):
            television = None
        if now - (entry.get("ts") or 0) < (AVAILABLE_TTL if count or television else MISSING_TTL):
            known[key] = {"n": count, "iv": television}
    return known


def _availability(lib, kind, ids):
    """Známá dostupnost titulů na Přehraj.to: {"movie:11": počet verzí}."""
    return {key: entry["n"] for key, entry in _known(lib, kind, ids).items()}


def _mark_known(state, known):
    """Doplní do stavu, kde které tituly jsou – karty podle toho dostanou štítky."""
    state.setdefault("available", {}).update({key: entry["n"] for key, entry in known.items()})
    state.setdefault("television", {}).update({key: entry["iv"] for key, entry in known.items() if entry["iv"]})


def _remember_availability(kind, tmdb_id, count, television=None):
    """Uloží, co se o titulu zjistilo. ``television`` je pořad v iVysílání, ``""`` když tam není,
    ``None`` když se to nezjišťovalo (dřívější údaj pak zůstane)."""
    if not tmdb_id:
        return
    key = AVAILABLE_PREFIX + "%s:%s" % (kind, tmdb_id)
    entry = {"n": count, "ts": int(time.time())}
    with api._library() as lib:
        if television is None:
            try:
                old = json.loads(lib.get_kv(key) or "null")
            except ValueError:
                old = None
            if isinstance(old, dict) and isinstance(old.get("iv"), str):
                entry["iv"], entry["its"] = old["iv"], old.get("its") or old.get("ts") or 0
        else:
            entry["iv"], entry["its"] = television, entry["ts"]
        lib.set_kv(key, json.dumps(entry))


def _ivys_match(kind, names, year):
    """Pořad v iVysílání, který je tímto titulem: jeho číslo, ``""`` když tam není, ``None`` když to
    teď nejde zjistit. Shodovat se musí název, druh (film / pořad s díly) a u filmu i rok."""
    wanted = {search_results.normalize(name) for name in names if name}
    query = next((name for name in names if name), "")
    if not query:
        return ""
    try:
        client = api._ivys(timeout=5)
        for show in client.search_shows(query, 0, 8)[1]:
            if search_results.normalize(show["title"]) not in wanted:
                continue
            detail = api._cached("ivys:show:" + show["id"], api.TTL["show"], lambda: client.show(show["id"]))
            if (detail.get("type") == "movie") != (kind == "movie"):
                continue
            if kind == "movie" and year and detail.get("year") and abs(detail["year"] - year) > 1:
                continue
            with api._library() as lib:
                api._ivys_remember_kind(lib, show["id"], detail.get("type"))
            return show["id"]
    except ivysilani.IvysilaniError:
        return None
    return ""


def _item_names(item):
    title = item.get("title") or item.get("name") or ""
    date = item.get("release_date") or item.get("first_air_date") or ""
    return [title, item.get("original_title") or item.get("original_name") or ""], \
        int(date[:4]) if date[:4].isdigit() else 0


def _check_available(kind, items, state):
    """Ověří tituly, u kterých aplikace dostupnost nezná: hledání na Přehraj.to (u seriálu jeho
    první díl) a jedno v iVysílání, pár titulů souběžně. Výsledek si pamatuje (viz ``_known``);
    co se zjistit nepodařilo, zůstane neznámé a zkusí se příště. Vrací čerstvý stav všech titulů."""
    ids = [item["id"] for item in items]
    if not _CHECK_LOCK.acquire(timeout=CHECK_WAIT):
        # jiné ověřování běží moc dlouho (opuštěné výpisy): vrátit, co se ví, a nedržet další vlákno
        with api._library() as lib:
            return _known(lib, kind, ids)
    try:
        with api._library() as lib:
            known = _known(lib, kind, ids)
        todo = [item for item in items
                if "%s:%s" % (kind, item["id"]) not in known or known["%s:%s" % (kind, item["id"])]["iv"] is None]
        if not todo:
            return known
        client = _search_client(timeout=5)   # jeden klient pro všechna hledání (jedno přihlášení)

        def paused(service):
            return _PAUSED.get(service, 0) > time.time()

        def check(item):
            # Služba, která neodpovídá, se dál nezkouší: výpis by na ni čekal u každého titulu zvlášť.
            names, year = _item_names(item)
            television = None
            entry = known.get("%s:%s" % (kind, item["id"]))
            count = entry["n"] if entry else None   # Přehraj.to už ověřené je; chybí jen iVysílání
            if count is None and not paused("prehrajto"):
                try:
                    versions = _find_versions(names, year if kind == "movie" else 0,
                                              None if kind == "movie" else (1, 1), state, pages=1, client=client,
                                              queries_max=3, strict=True, thorough=False)
                    count = len([version for version in versions if not version["short"]])
                except api.ApiError:
                    _PAUSED["prehrajto"] = time.time() + CHECK_PAUSE
            if not paused("ivysilani"):
                television = _ivys_match(kind, names, year)
                if television is None:
                    _PAUSED["ivysilani"] = time.time() + CHECK_PAUSE
            return item["id"], count, television

        with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
            results = list(pool.map(check, todo))
        for tmdb_id, count, television in results:
            # co se zjistilo, to se uloží; chybějící polovina (služba neodpovídala) se doplní příště
            if count is not None and (television is not None or "%s:%s" % (kind, tmdb_id) not in known):
                _remember_availability(kind, tmdb_id, count, television)
        with api._library() as lib:
            return _known(lib, kind, ids)
    finally:
        _CHECK_LOCK.release()


def _verify_later(kind, items, state):
    """Zařadí tituly k ověření na pozadí. Vrací klíče těch, které na ověření čekají (i z dřívějška)."""
    now = time.time()
    with _VERIFY_LOCK:
        waiting = {key for key, _item in _VERIFY["queue"]} | _VERIFY["active"]
        pending, fresh = set(), []
        for item in items:
            key = (kind, item["id"])
            if key in waiting:
                pending.add(key)
            elif _VERIFY["failed"].get(key, 0) <= now:
                fresh.append((key, item))
                pending.add(key)
        _VERIFY["queue"][:0] = fresh   # naposledy otevřený výpis má přednost
        del _VERIFY["queue"][VERIFY_QUEUE:]
        if _VERIFY["queue"] and _VERIFY["thread"] is None:
            thread = threading.Thread(target=_verify_loop, args=(state,), name="overovani")
            thread.daemon = True
            _VERIFY["thread"] = thread
            thread.start()
    return pending


def _verify_loop(state):
    """Vlákno ověřování: bere z fronty po pár titulech stejného druhu, dokud není prázdná."""
    while True:
        with _VERIFY_LOCK:
            if not _VERIFY["queue"]:
                _VERIFY["thread"] = None
                return
            kind = _VERIFY["queue"][0][0][0]
            batch = [entry for entry in _VERIFY["queue"] if entry[0][0] == kind][:CHECK_WORKERS]
            _VERIFY["queue"] = [entry for entry in _VERIFY["queue"] if entry not in batch]
            _VERIFY["active"] = {key for key, _item in batch}
        try:
            known = _check_available(kind, [item for _key, item in batch], state)
        except Exception:   # vlákno na pozadí nesmí spadnout: neověřené tituly se jen chvíli nezkoušejí
            known = {}
        with _VERIFY_LOCK:
            for key, _item in batch:
                if "%s:%s" % key not in known:
                    _VERIFY["failed"][key] = time.time() + CHECK_PAUSE
            _VERIFY["active"] = set()


def _missing(known, kind, item):
    """Titul se na Přehraj.to hledal a není tam, a aplikace o něm neví ani z iVysílání."""
    entry = known.get("%s:%s" % (kind, item["id"]))
    return bool(entry) and not entry["n"] and not entry["iv"]


def _collect(params, state, enough=ONLY_ENOUGH, pages_max=ONLY_PAGES):
    """Tituly výpisu od stránky ``page``: ``(tituly, poslední prošlá stránka, stránek celkem)``.

    Běžně jen ty, které jsou k mání na Přehraj.to nebo v iVysílání; protože jich na stránce TMDB
    může být málo, projde se jich víc (nejvýš ``pages_max``), dokud není titulů ``enough``.
    S ``avail=all`` vrací celou stránku bez ověřování.

    Na ověřování se nečeká (``BACKGROUND``): tituly, o kterých se zatím nic neví, ve výpisu zůstanou,
    ověřují se na pozadí a jejich klíče jsou ve ``state["checking"]`` – obrazovka si pak řekne znovu.
    """
    kind = _kind(params)
    page = max(1, api._offset({"offset": params.get("page")}))
    everything = params.get("avail") == "all"
    found, started = [], time.time()
    checking = state.setdefault("checking", set())
    for current in range(page, page + (1 if everything else pages_max)):
        items, pages = _list_items(dict(params, page=current))
        items = [item for item in items if isinstance(item, dict) and item.get("id")]
        if everything or BACKGROUND:
            with api._library() as lib:
                known = _known(lib, kind, [item["id"] for item in items])
            if not everything:
                # neznámé tituly a ty, u kterých chybí jen iVysílání, se ověří na pozadí
                todo = [item for item in items
                        if (known.get("%s:%s" % (kind, item["id"])) or {"iv": None})["iv"] is None]
                pending = _verify_later(kind, todo, state)
                checking.update("%s:%s" % key for key in pending if "%s:%s" % key not in known)
        else:
            known = _check_available(kind, items, state)
        _mark_known(state, known)
        found += [item for item in items if everything or not _missing(known, kind, item)]
        if current >= pages or not items or len(found) >= enough or time.time() - started > ONLY_SECONDS:
            break
    return found, current, pages


def _checking_note(state):
    """Co obrazovka ukáže a kdy se má zeptat znovu, dokud se část titulů ověřuje."""
    count = len(state.get("checking") or ())
    if not count:
        return {}
    return {"refresh": VERIFY_REFRESH,
            "status": "Ověřuji, co je k mání na Přehraj.to a v iVysílání – zbývá %d" % count}


def _item_card(item, kind, state):
    title = item.get("title") or item.get("name") or ""
    date = item.get("release_date") or item.get("first_air_date") or ""
    poster = item.get("poster_path") or ""
    return _title_card(kind, item.get("id") or 0, title, int(date[:4]) if date[:4].isdigit() else 0,
                       IMG + "w342" + poster if poster.startswith("/") else "",
                       item.get("original_title") or item.get("original_name") or "", state,
                       rating=int(round((item.get("vote_average") or 0) * 10)))


def _kind(params):
    return "tv" if params.get("kind") == "tv" else "movie"


def discover(params):
    """Seznam titulů: ``category`` (popular, top_rated, recent) nebo filtry ``year``, ``genre``, ``origin``."""
    kind = _kind(params)
    page = max(1, api._offset({"offset": params.get("page")}))
    everything = params.get("avail") == "all"
    with api._library() as lib:
        state = api._state(lib)
    items, last, pages = _collect(params, state)
    cards = [_item_card(item, kind, state) for item in items]
    # „jen dostupné“: další stránka se nabízí, i když z těchto nevyšlo nic – aplikace pak hledá dál sama
    following = {"call": "discover", "params": dict(params, page=last + 1)} \
        if last < pages and (cards or not everything) else None
    if everything and kind == "movie" and cards and len(state["available"]) < len(cards):
        # u výpisu všech titulů se dostupnost neověřuje sama, jen na požádání
        cards.insert(0, api._card("Ověřit dostupnost", api._target("command", "discover_check", dict(params, page=page)),
                                  "Které z těchto filmů jsou na Přehraj.to", icon="search"))
    if page == 1:
        # filtr je vždy první – i u prázdného výběru, aby šel změnit
        base = {key: value for key, value in params.items() if key != "page"}
        cards.insert(0, api._card("Filtr", {"type": "filter", "call": "discover_filters", "params": base},
                                  _filter_summary(base) or "Žánr, období, hodnocení, dostupnost", icon="settings:search"))
    return dict(_checking_note(state), items=cards, next=following,
                empty="V tomto výběru TMDB nic nenabízí." if everything else
                "Z tohoto výběru teď není nic na Přehraj.to ani v iVysílání. Ve Filtru jde zapnout Všechny tituly.")


def discover_row(params):
    """Řada titulů na hlavní obrazovce (sekce Objevuj): prvních pár položek seznamu a karta „Zobrazit vše“.

    Aplikace si ji donačítá sama po zobrazení sekce; bez připojení řada prostě chybí.
    """
    kind = _kind(params)
    with api._library() as lib:
        state = api._state(lib)
    items = _collect(params, state, pages_max=2)[0][:api.HOME_ROW]   # jen tituly, které jsou k mání
    cards = [_item_card(item, kind, state) for item in items]
    note = _checking_note(state)
    if cards:
        name = "%s – populární" % _plural(kind)
        cards.append(api._card("Zobrazit vše", api._target("list", "discover", dict(params), name),
                               "A filtr podle žánru, období a hodnocení", icon="all"))
    return dict(note, items=cards)


RATINGS = (("", "Vše"), ("60", "60 % a víc"), ("70", "70 % a víc"), ("80", "80 % a víc"))
SORTS = (("popular", "Nejoblíbenější"), ("rating", "Nejlépe hodnocené"), ("new", "Nejnovější"))
FILTER_KEYS = ("genre", "year", "origin", "rating", "sort")


def _default_sort(params):
    return {"top_rated": "rating"}.get(params.get("category") or "", "popular")


def _periods():
    """Roky a desetiletí nabízené ve filtru (stejné jako dlaždice Podle roku)."""
    year = time.localtime().tm_year
    values = [(str(value), str(value)) for value in range(year, year - 8, -1)]
    values += [("%ds" % start, "%d–%d" % (start, start + 9)) for start in range((year - 8) // 10 * 10, 1920, -10)]
    return values


def _filter_summary(params):
    """Krátký popis nastavených filtrů pro kartu Filtr."""
    parts = []
    if params.get("genre"):
        name = "žánr"
        try:   # číselník žánrů je v mezipaměti; když není po ruce, stačí obecné slovo
            name = next((genre["name"] for genre in _tmdb().genres(_kind(params))
                         if isinstance(genre, dict) and str(genre.get("id")) == str(params["genre"])), name)
        except (TmdbError, api.ApiError):
            pass
        parts.append(name)
    if params.get("year"):
        parts.append(dict(_periods()).get(str(params["year"]), str(params["year"])))
    if params.get("rating"):
        parts.append("%s %%+" % params["rating"])
    if params.get("origin"):
        parts.append("ČR a SR" if params["origin"] == CZECHOSLOVAK else str(params["origin"]))
    if params.get("sort") and params["sort"] != _default_sort(params):
        parts.append(dict(SORTS).get(params["sort"], "").lower())
    if params.get("avail") == "all":
        parts.append("i nedostupné")
    return " · ".join(part for part in parts if part)


def discover_filters(params):
    """Filtry seznamu z Objevuj: ``{"filters": [{"title", "value", "options": [{"title", "params", "current"}]}],
    "reset": parametry bez filtrů | None}``. Každá volba nese celé parametry pro nový výpis."""
    kind = _kind(params)
    base = {key: value for key, value in params.items() if key != "page"}

    def options(key, choices, current):
        return [{"title": title, "current": str(value) == str(current),
                 "params": dict({k: v for k, v in base.items() if k != key}, **({key: value} if value else {}))}
                for value, title in choices]

    try:
        genres = [(str(genre["id"]), genre["name"]) for genre in _tmdb().genres(kind)
                  if isinstance(genre, dict) and genre.get("id") and genre.get("name")]
    except TmdbError:
        genres = []
    sort = base.get("sort") or _default_sort(base)
    groups = [("Žánr", "genre", [("", "Vše")] + genres, base.get("genre") or ""),
              ("Období", "year", [("", "Vše")] + _periods(), base.get("year") or ""),
              ("Hodnocení", "rating", list(RATINGS), base.get("rating") or ""),
              ("Země", "origin", [("", "Vše"), (CZECHOSLOVAK, "Česko a Slovensko")], base.get("origin") or ""),
              ("Řazení", "sort", list(SORTS), sort),
              ("Dostupnost", "avail", [("", "Jen na Přehraj.to a v iVysílání"), ("all", "Všechny tituly")],
               base.get("avail") or "")]
    filters = []
    for title, key, choices, current in groups:
        listed = options(key, choices, current)
        value = next((option["title"] for option in listed if option["current"]), "Vše")
        filters.append({"title": title, "value": value, "options": listed})
    active = any(base.get(key) for key in FILTER_KEYS + ("avail",) if key != "sort") or sort != _default_sort(base)
    reset = {key: value for key, value in base.items() if key not in FILTER_KEYS + ("avail",)} if active else None
    return {"title": "Filtr", "filters": filters, "reset": reset}


def _list_items(params):
    """Tituly jedné stránky seznamu (stejný výběr jako ``discover``)."""
    kind = _kind(params)
    page = max(1, api._offset({"offset": params.get("page")}))
    client = _tmdb()
    category = params.get("category") or ""
    filtered = any(params.get(key) for key in FILTER_KEYS)
    if category in ("popular", "top_rated") and not filtered:
        return client.browse(kind, category, page)
    if category == "recent" and not filtered and kind == "tv":
        return client.browse(kind, "on_the_air", page)   # novinky u seriálů: právě vysílané
    date = "primary_release_date" if kind == "movie" else "first_air_date"
    filters = {"with_genres": params.get("genre") or "", "with_origin_country": params.get("origin") or "",
               "with_cast": params.get("cast") or ""}
    start, end = search_results.period(str(params.get("year") or ""))
    if start and start == end:
        filters["primary_release_year" if kind == "movie" else "first_air_date_year"] = start
    elif start:
        filters[date + ".gte"], filters[date + ".lte"] = "%d-01-01" % start, "%d-12-31" % end
    rating = str(params.get("rating") or "")
    if rating.isdigit():
        # hodnocení od pár hlasujících nic neříká
        filters["vote_average.gte"], filters["vote_count.gte"] = int(rating) / 10.0, 50
    # Novinky filmů bez dalších filtrů: poslední tři měsíce podle oblíbenosti. Řazení podle data by
    # nabídlo stovky neznámých premiér z celého světa, ze kterých není k mání skoro nic.
    sort = params.get("sort") or (_default_sort(params) if filtered else "popular")
    today = time.strftime("%Y-%m-%d")
    if sort == "rating":
        filters["sort_by"] = "vote_average.desc"
        filters["vote_count.gte"] = 200
    elif sort == "new":
        filters["sort_by"] = date + ".desc"
        filters.setdefault(date + ".lte", today)   # bez titulů ohlášených na příští roky
    if category == "recent" and kind == "movie" and not start:
        filters[date + ".gte"] = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
        filters.setdefault(date + ".lte", today)
    return client.discover(kind, page, **filters)


def discover_check(params):
    """Ověří, které filmy z jedné stránky seznamu jsou na Přehraj.to (jedno hledání na film)."""
    def work():
        items, _pages = _list_items(params)
        items = [item for item in items if isinstance(item, dict) and item.get("id")]
        with api._library() as lib:
            state = api._state(lib)
            known = _availability(lib, "movie", [item["id"] for item in items])
        todo = [item for item in items if "movie:%s" % item["id"] not in known][:CHECK_BATCH]
        found = 0
        for index, item in enumerate(todo, 1):
            title = item.get("title") or ""
            api._STATE["progress"] = "Ověřuji dostupnost: %d/%d – %s" % (index, len(todo), title)
            date = item.get("release_date") or ""
            try:
                versions = _find_versions([title, item.get("original_title") or ""],
                                          int(date[:4]) if date[:4].isdigit() else 0, None, state, pages=1,
                                          thorough=False)
            except api.ApiError:
                continue  # hledání teď nejde; titul zůstane neověřený
            good = [version for version in versions if not version["short"]]
            _remember_availability("movie", item["id"], len(good))
            found += bool(good)
        return {"message": "Ověřeno %d filmů, na Přehraj.to je %d z nich." % (len(todo), found)}

    return api._exclusive(work)


def discover_years(params):
    """Dlaždice: jednotlivé poslední roky a starší desetiletí."""
    kind = _kind(params)
    extra = {"origin": params["origin"]} if params.get("origin") else {}
    year = time.localtime().tm_year
    cards = []
    for value in range(year, year - 8, -1):
        cards.append(api._card(str(value), api._target("list", "discover", dict(extra, kind=kind, year=str(value)),
                                                       "%s – %d" % (_plural(kind), value)), icon="year"))
    for start in range((year - 8) // 10 * 10, 1920, -10):
        label = "%d–%d" % (start, start + 9)
        cards.append(api._card(label, api._target("list", "discover", dict(extra, kind=kind, year="%ds" % start),
                                                  "%s – %s" % (_plural(kind), label)), icon="year"))
    return {"items": cards, "next": None}


def discover_genres(params):
    kind = _kind(params)
    extra = {"origin": params["origin"]} if params.get("origin") else {}
    tint = "series" if kind == "tv" else "movies"
    # ilustrace žánru (tile_genre_<číslo>); žánr bez ilustrace dostane složku v barvě sekce
    cards = [api._card(genre["name"], api._target("list", "discover", dict(extra, kind=kind, genre=str(genre["id"])),
                                                  "%s – %s" % (_plural(kind), genre["name"])),
                       icon="%s:%s" % (GENRE_ART.get(genre["id"], "genre_%s" % genre["id"]), tint))
             for genre in _tmdb().genres(kind) if isinstance(genre, dict) and genre.get("id") and genre.get("name")]
    return {"items": cards, "next": None}


def discover_czechoslovak(params):
    """Česká a slovenská tvorba: nejoblíbenější, podle roku a podle žánru."""
    kind = _kind(params)
    name = "Československé %s" % _plural(kind).lower()
    origin = {"kind": kind, "origin": CZECHOSLOVAK}
    cards = [
        api._card("Nejoblíbenější", api._target("list", "discover", origin, name), icon="rating"),
        api._card("Podle roku", api._target("list", "discover_years", origin, name + " podle roku"), icon="year"),
        api._card("Podle žánru", api._target("list", "discover_genres", origin, name + " podle žánru"),
                  icon="folder:series" if kind == "tv" else "folder:movies"),
    ]
    return {"items": cards, "next": None}


def _plural(kind):
    return "Seriály" if kind == "tv" else "Filmy"


def home_tiles(kind):
    """Dlaždice řady „Objevuj filmy / seriály“ na hlavní obrazovce."""
    name = _plural(kind)
    icon = "series" if kind == "tv" else "movies"
    return [
        api._card("Novinky", api._target("list", "discover", {"kind": kind, "category": "recent"},
                                         "%s – novinky" % name), "Právě vysílané" if kind == "tv" else "Poslední 3 měsíce",
                  icon="discover_new:" + icon),
        api._card("Populární", api._target("list", "discover", {"kind": kind, "category": "popular"},
                                           "%s – populární" % name), icon="discover_popular:" + icon),
        api._card("Nejlépe hodnocené", api._target("list", "discover", {"kind": kind, "category": "top_rated"},
                                                   "%s – nejlépe hodnocené" % name), icon="discover_top:" + icon),
        api._card("Podle roku", api._target("list", "discover_years", {"kind": kind}, "%s podle roku" % name),
                  icon="discover_year:" + icon),
        api._card("Podle žánru", api._target("list", "discover_genres", {"kind": kind}, "%s podle žánru" % name),
                  icon="discover_genre:" + icon),
        api._card("Československé", api._target("list", "discover_czechoslovak", {"kind": kind},
                                                "Československé %s" % name.lower()), "Česko a Slovensko",
                  icon="discover_czechoslovak:" + icon),
    ]


# ---------------------------------------------------------------------------
# Titul: verze na Přehraj.to, u seriálu řady a díly
# ---------------------------------------------------------------------------

def _duration_text(seconds):
    minutes = int(round((seconds or 0) / 60.0))
    if minutes <= 0:
        return ""
    return "%d h %02d min" % divmod(minutes, 60) if minutes >= 60 else "%d min" % minutes


def _search_client(timeout=8):
    """Klient pro veřejné hledání na Přehraj.to. Hledat jde i bez přihlášení: když přihlášení selže
    (špatné heslo), hledá se bez něj – jinak by každý dotaz zkoušel přihlášení znovu a hledání by
    vypadalo jako výpadek."""
    client = api._client(allow_anonymous=True)
    if client.has_credentials:
        try:
            client.login()
        except provider.LoginError:
            client = provider.make_client("", "")
        except provider.ProviderError:
            pass   # výpadek: ohlásí ho až samo hledání
    client.request_timeout = timeout
    client.request_retries = 0
    return client


def _good_enough(found, prefs, names):
    """Je mezi nalezenými soubory aspoň jeden, jaký si uživatel přeje – v předvoleném jazyce a kvalitě,
    žádná kino kopie ani ukázka? Kvalita, kterou hledání neuvádí, se bere jako vyhovující."""
    order = provider.QUALITY_ORDER
    limit = {"best": "FHD", "1080": "FHD", "720": "HD"}.get(prefs["quality"])
    for result, parsed in found:
        raw = result["title"]
        if api.CAM_RE.search(raw) or search_results.is_preview(raw, names):
            continue
        if prefs["language"] != "any" and api._language_rank(parsed.get("lang"), prefs["language"]) != 0:
            continue
        token = provider.quality_token(result.get("quality") or parsed.get("quality") or "")
        if limit and token in order and order.index(token) > order.index(limit):
            continue
        return True
    return False


def _find_versions(names, year, position, state, runtime=0, pages=2, choice="", client=None, queries_max=4,
                   strict=False, thorough=True):
    """Verze titulu (nebo dílu) na Přehraj.to, vyčištěné a seřazené podle předvoleb.

    Vrací ``[{"video", "result", "short", "cam", "bad", "chosen"}]``. Hledá se více variantami názvu;
    soubor výrazně kratší než film (``runtime`` v minutách) je ukázka nebo část a řadí se až na konec,
    stejně jako kino kopie a verze, které se dřív nepodařilo přehrát. Stejné soubory se slučují.

    ``thorough``: nespokojit se s prvním názvem, pod kterým se něco našlo – když mezi nálezy není
    verze podle předvoleb (jazyk, kvalita, žádná kino kopie), zkusí se i další názvy (původní název
    často najde lepší soubor než český). Ověřování dostupnosti to nepotřebuje: tam stačí, že titul je.
    """
    if client is None:
        client = _search_client()
    wanted = search_results.aliases(*names)
    kind = "episode" if position else "movie"
    queries = []
    for name in names:
        if not name:
            continue
        for query in (search_results.format_query(name, 0 if position else year, position),
                      search_results.format_query(name, 0, position)):
            if query not in queries:
                queries.append(query)
    queries = queries[:queries_max]
    prefs, bad = state.get("prefs") or api._prefs(), state.get("bad", ())
    found, failed = {}, 0
    for query in queries:
        cursor = ""
        for page in range(1, pages + 1):
            try:
                results, cursor = provider.search_catalog(client, query, page, cursor)
            except provider.ProviderError:
                failed += page == 1
                break
            for result in results:
                parsed = nameparser.parse(result["title"])
                if search_results.filename_matches(parsed, wanted, 0 if position else year, position, kind):
                    found.setdefault(result["id"], (result, parsed))
            if not cursor:
                break
        # Ověřování dostupnosti stačí, že titul je – ale jen ve kvalitě, kterou si uživatel přeje vidět.
        usable = [item for item in found.values() if api._meets_minimum(
            dict(item[1], quality=api._known_quality(item[0].get("quality"), item[1].get("quality")),
                 size=item[0].get("size", ""), duration=item[0].get("duration") or 0), prefs)]
        if usable and (not thorough or _good_enough(usable, prefs, names)):
            break
    with api._library() as lib:
        chosen = lib.get_kv(api.CHOICE_PREFIX + choice) if choice else ""
        # Verze stažené na disku patří k titulu, i když už je hledání nevrací (nebo zrovna nejde).
        for vid in state.get("downloads", {}):
            if vid in found or not api._saved(state, vid):
                continue
            video = lib.find_video(vid)
            if video and search_results.filename_matches(nameparser.parse(video["raw_title"]), wanted,
                                                         0 if position else year, position, kind):
                found[vid] = ({"id": vid, "title": video["raw_title"], "thumb": video.get("thumb") or "",
                               "size": video.get("size") or "", "duration": video.get("duration") or 0,
                               "quality": video.get("quality") or ""}, nameparser.parse(video["raw_title"]))
    # ``strict`` (ověřování dostupnosti): i jediný nepovedený dotaz znamená „nevím“, ne „není tam“
    if not found and queries and (failed == len(queries) or (strict and failed)):
        raise api.ApiError("Hledání na Přehraj.to se nezdařilo. Zkuste to znovu.")
    versions, seen = [], set()
    for result, parsed in found.values():
        video = dict(parsed, id=result["id"], raw_title=result["title"], thumb=result.get("thumb", ""),
                     size=result.get("size", ""), duration=result.get("duration") or 0)
        video["quality"] = api._known_quality(result.get("quality"), video.get("quality"))
        same = (video["duration"], video["size"], video["quality"], video.get("lang"))
        if video["duration"] and video["size"] and same in seen:
            continue  # tentýž soubor nahraný vícekrát
        seen.add(same)
        versions.append({"video": video, "result": result,
                         # ukázka nebo část: výrazně kratší než film, nebo se tak soubor sám jmenuje (trailer…)
                         "short": bool(runtime and video["duration"] and video["duration"] < runtime * 60 * 0.6)
                         or search_results.is_preview(video["raw_title"], names),
                         "cam": bool(api.CAM_RE.search(video["raw_title"])), "bad": video["id"] in bad,
                         "chosen": bool(chosen) and video["id"] == chosen, "saved": api._saved(state, video["id"])})
    # Verze v nižší kvalitě, než si uživatel přeje vidět, se nenabízejí (stažená na disku zůstává).
    shown = [version for version in versions if version["saved"] or api._meets_minimum(version["video"], prefs)]
    state["below_minimum"] = len(versions) - len(shown)
    versions = shown
    # stažená verze má přednost (hraje z disku, bez čekání a bez internetu), pak naposledy zvolená
    versions.sort(key=lambda version: (version["short"], not version["saved"], not version["chosen"]) +
                  api._version_rank(version["video"], prefs, bad))
    return versions


def _version_card(version, state, choice=""):
    video, result = version["video"], version["result"]
    quality = video.get("quality") or ""
    title = " · ".join(part for part in (video.get("lang") or "Jazyk neuveden", quality) if part)
    notes = [note for flag, note in (("chosen", "naposledy zvolená"), ("short", "ukázka nebo část"),
                                     ("cam", "kino kopie"), ("bad", "nešlo přehrát")) if version[flag]]
    subtitle = " · ".join(part for part in notes + [video.get("size"), _duration_text(video.get("duration")),
                                                    video["raw_title"]] if part)
    params = {"vid": video["id"], "web_result": result}
    if choice:
        params["choice"] = choice
    point = state["resume"].get(video["id"])
    language = "TIT" if "titulky" in (video.get("lang") or "") and "dabing" not in (video.get("lang") or "") \
        else (video.get("lang") or "").split(" ")[0]
    return api._card(title, api._target("play", "play", params), api._mark(state, video["id"], subtitle),
                     video.get("thumb"), api._percent(point), wide=True, watched=video["id"] in state["watched"],
                     menu=api._video_menu(state, params, point), icon="movies",
                     badges=["Na disku" if version.get("saved") else "", language, quality])


def _names(params):
    return [name for name in (params.get("title") or "", params.get("orig") or "") if name]


def _detail(params, kind):
    """Údaje o titulu pro jeho stránku; bez TMDB zůstane to, co nese karta."""
    info = {"title": params.get("title") or "", "year": int(params.get("year") or 0), "rating": 0, "genres": [],
            "plot": "", "poster": params.get("poster") or "", "fanart": "", "runtime": 0, "collection_id": 0,
            "orig_title": params.get("orig") or ""}
    if params.get("id"):
        try:
            found = _tmdb().details(int(params["id"]), want_series=kind == "tv")
            info.update({key: found[key] for key in info if found.get(key)})
        except (TmdbError, api.ApiError, ValueError):
            pass  # stránka se zobrazí i bez podrobností
    meta = " · ".join(part for part in (
        str(info["year"]) if info["year"] else "", "%d %%" % info["rating"] if info["rating"] else "",
        _duration_text(info["runtime"] * 60) if kind == "movie" else KIND_NAMES["tv"],
        ", ".join(info["genres"][:3])) if part)
    return info, {"title": info["title"], "meta": meta, "plot": info["plot"], "poster": info["poster"],
                  "backdrop": info["fanart"] or info["poster"]}


def _related_rows(kind, params, info, state):
    """Další řady stránky titulu: celá filmová série, podobné tituly a herci. Co TMDB nedodá, chybí."""
    rows = []
    if not params.get("id"):
        return rows
    try:
        client = _tmdb()
    except api.ApiError:
        return rows
    tmdb_id = int(params["id"])
    if kind == "movie" and info.get("collection_id"):
        try:
            parts = client.collection_movies(info["collection_id"])
        except TmdbError:
            parts = []
        if len(parts) > 1:
            cards = []
            for part in parts:
                card = _item_card(part, "movie", state)
                if part["id"] == tmdb_id:
                    card["subtitle"] = " · ".join(text for text in ("Právě otevřený", card["subtitle"]) if text)
                cards.append(card)
            rows.append({"title": "Celá série", "items": cards})
    try:
        similar = client.recommendations(kind, tmdb_id)
    except TmdbError:
        similar = []
    if similar:
        rows.append({"title": "Podobné tituly", "items": [_item_card(item, kind, state) for item in similar[:20]]})
    try:
        people = client.cast(tmdb_id, want_series=kind == "tv")
    except TmdbError:
        people = []
    if people:
        rows.append({"title": "Hrají", "items": [
            api._card(person["name"], api._target("list", "discover", {"kind": "movie", "cast": str(person["id"])},
                                                  "Filmy – %s" % person["name"]),
                      person["character"], person["photo"], wide=not person["photo"], icon="movies")
            for person in people]})
    return rows


def title_csfd(params):
    """Hodnocení ČSFD k titulu (načítá se až po zobrazení stránky): ``{"text": "ČSFD 86 %"}`` nebo prázdné.

    ČSFD dotazy často odmítá; výsledek i neúspěch se proto pamatuje, ať se stránka nezdržuje znovu.
    """
    title, year = params.get("title") or "", int(params.get("year") or 0)
    if not title:
        return {"text": ""}
    key = "csfd:%s:%s:%s" % (_kind(params), search_results.normalize(title), year)
    with api._library() as lib:
        cached = lib.cache_get(key)
    if isinstance(cached, dict):
        return {"text": cached.get("text") or ""}
    text, ttl = "", 24 * 3600
    try:
        found = None
        for name in [title, params.get("orig") or ""]:
            if name and not found:
                found = csfd.find(name, year, want_series=_kind(params) == "tv")
        if found and found.get("rating"):
            text, ttl = "ČSFD %d %%" % found["rating"], 7 * 24 * 3600
    except csfd.CsfdError:
        ttl = 3600   # odmítnuto nebo nedostupné: zkusit až za chvíli
    with api._library() as lib:
        lib.cache_put(key, {"text": text}, ttl)
    return {"text": text}


NOT_FOUND = "Na Přehraj.to jsem tento film nenašel. Zkuste hledat ručně pod jiným názvem."
LOW_QUALITY = ("Na Přehraj.to je tento film jen v nižší kvalitě než Full HD (nalezeno: %d). Nižší kvalitu "
               "povolíte tlačítkem Jazyk a kvalita → Nejnižší kvalita.")


def _title_television(kind, params):
    """Pořad v iVysílání k titulu: zapamatovaný údaj, jinak dotaz (viz ``_ivys_match``)."""
    if params.get("id"):
        with api._library() as lib:
            entry = _known(lib, kind, [params["id"]]).get("%s:%s" % (kind, params["id"]))
        if entry and entry["iv"] is not None:
            return entry["iv"]
    return _ivys_match(kind, _names(params), int(params.get("year") or 0))


def discover_title(params):
    """Stránka titulu: údaje, tlačítka a řady karet.

    ``{"detail": {title, meta, plot, poster, backdrop}, "actions": [{title, subtitle, target}],
    "rows": [{title, items}]}``. U filmu jsou v řadách verze z Přehraj.to a první tlačítko přehraje tu
    nejlepší podle předvoleb; u seriálu jsou v řadě jeho série. Je-li titul i v iVysílání, přibude
    tlačítko, které ho tam přehraje (film) nebo otevře jeho díly.
    """
    kind = _kind(params)
    with ThreadPoolExecutor(max_workers=1) as pool:
        television = pool.submit(_title_television, kind, params)   # souběžně s hledáním na Přehraj.to
        page, playable = _title_page(params, kind)
        show = television.result()
    entry = None
    if params.get("id"):
        with api._library() as lib:
            entry = _known(lib, kind, [params["id"]]).get("%s:%s" % (kind, params["id"]))
    if show:
        if kind == "movie":
            action = {"title": "Přehrát v iVysílání", "subtitle": "Česká televize",
                      "target": api._target("play", "ivys_play", {"show": show})}
        else:
            action = {"title": "Díly v iVysílání", "subtitle": "Česká televize",
                      "target": api._target("list", "ivys_show", {"id": show}, params.get("title") or "")}
        # hned za Přehrát z Přehraj.to; první, když tam titul není (nebo se to neví a nic k přehrání není)
        page["actions"].insert(1 if playable and not (entry and not entry["n"]) else 0, action)
        if page["detail"].get("plot", "").startswith(NOT_FOUND):
            page["detail"]["plot"] = page["detail"]["plot"].replace(
                NOT_FOUND, "Na Přehraj.to jsem tento film nenašel, je ale v iVysílání České televize.", 1)
    if show is not None and entry and entry["iv"] != show:
        _remember_availability(kind, params["id"], entry["n"], show)
    return page


def _title_page(params, kind):
    """Stránka titulu bez iVysílání a zda má tlačítko, které rovnou přehrává."""
    title = params.get("title") or ""
    with api._library() as lib:
        state = api._state(lib)
        last = lib.get_kv("last_season:%s" % params.get("id")) if kind == "tv" else None
    favorites_state = state["favorites"]
    entry = favorites_state.get(params.get("fid") or "") or tmdb_favorite(
        kind, params.get("id") or 0, title, int(params.get("year") or 0), params.get("poster") or "",
        params.get("orig") or "")
    toggle = favorite_item(favorites_state, entry)
    info, detail = _detail(params, kind)
    actions = [{"title": toggle["title"], "subtitle": "", "target": toggle["target"]},
               {"title": "Hledat ručně", "subtitle": "", "target": api._target("search", "search", {"q": title})},
               {"title": "Jazyk a kvalita", "subtitle": "", "target": api._target("choices", "prefs_choices", {})}]
    if kind == "tv" and not params.get("id"):
        # oblíbený seriál, který už v katalogu není a TMDB ho nezná: díly nejdou dohledat po řadách
        detail["plot"] = ("Tento seriál už ve vašem katalogu není. Zkuste „Hledat ručně“.\n\n"
                          + (detail.get("plot") or "")).strip()
        return {"detail": detail, "actions": actions, "rows": []}, False
    if kind == "tv" and params.get("id"):
        rows, play, focus = _series_rows(params, state, last)
        if play:
            actions.insert(0, play)
        page = {"detail": detail, "actions": actions, "rows": rows + _related_rows(kind, params, info, state)}
        if focus:
            # díl, kterým se pokračuje: po návratu z přehrávače na něm aplikace nechá výběr
            page["focus"] = {"row": focus[0], "item": focus[1]}
        return page, bool(play)
    choice = "movie:%s" % params["id"] if params.get("id") else ""
    failed = ""
    try:
        versions = _find_versions(_names(params), info["year"], None, state, info["runtime"], choice=choice)
    except api.ApiError as error:
        # hledání selhalo: stránka zůstane použitelná – údaje, oblíbené, ruční hledání a nový pokus
        versions, failed = [], str(error)
    good = [version for version in versions if not version["short"]]
    rows = []
    if failed:
        actions.insert(0, {"title": "Zkusit znovu", "subtitle": "", "target": api._target("refresh")})
        detail["plot"] = (failed + "\n\n" + detail["plot"]).strip()
    else:
        _remember_availability("movie", params.get("id"), len(good))
        _note_favorite(entry["id"], versions, state["prefs"])
    if failed:
        pass
    elif good:
        best = _version_card(good[0], state, choice)
        actions.insert(0, {"title": "Přehrát", "subtitle": best["title"], "target": best["target"]})
        rows.append({"title": "Verze na Přehraj.to (%d)" % len(good),
                     "items": [_version_card(version, state, choice) for version in good]})
    elif state.get("below_minimum"):
        # film na Přehraj.to je, ale jen v nižší kvalitě, než si uživatel přeje vidět
        detail["plot"] = (LOW_QUALITY % state["below_minimum"] + "\n\n" + detail["plot"]).strip()
    else:
        detail["plot"] = (NOT_FOUND + "\n\n" + detail["plot"]).strip()
    rest = [version for version in versions if version["short"]]
    if rest:
        rows.append({"title": "Ukázky a neúplné soubory", "items": [_version_card(v, state, choice) for v in rest]})
    return {"detail": detail, "actions": actions,
            "rows": rows + _related_rows(kind, params, info, state)}, bool(good) and not failed


SEASON_ROWS = 6   # tolik řad ukáže stránka seriálu rovnou s díly; další jsou za kartou řady


def _episode_card(params, season, number, episode, state, vid):
    """Karta dílu: po výběru se rovnou přehraje nejlepší verze; ostatní verze jsou v nabídce karty."""
    label = "%d×%02d" % (season, int(number))
    name = "%s · %s" % (label, episode["title"]) if episode.get("title") else label
    chosen = dict(params, season=season, episode=int(number))
    point = state["resume"].get(vid) if vid else None
    menu = [{"title": "Přehrát", "target": api._target("play", "discover_episode_play", chosen)}]
    if point and point[0] > 0:
        menu.append({"title": "Přehrát od začátku",
                     "target": api._target("play", "discover_episode_play", dict(chosen, restart=True))})
    menu.append({"title": "Vybrat verzi", "target": api._target(
        "list", "discover_episode", chosen, "%s %s" % (params.get("title") or "", label))})
    return api._card(name, api._target("play", "discover_episode_play", chosen), (episode.get("plot") or "")[:120],
                     episode.get("still") or "", api._percent(point), wide=True,
                     watched=bool(vid) and vid in state["watched"], icon="series", menu=menu)


def _played_episodes(lib, tmdb_id, season, numbers):
    """Verze, kterou si uživatel u dílu pustil: nese stav zhlédnutí celého dílu."""
    return {number: lib.get_kv(api.CHOICE_PREFIX + "tv:%s:%s:%s" % (tmdb_id, season, number)) for number in numbers}


def _series_rows(params, state, last=None):
    """Řady stránky seriálu (díly po řadách), tlačítko, které pustí díl, na kterém se skončilo, a kde
    ten díl v řadách je (``(řada, pozice)``).

    Rovnou s díly je jen ``SEASON_ROWS`` řad; u dlouhého seriálu je to okno kolem naposledy sledované
    řady (``last``), aby „Pokračovat“ vidělo i díly ze sedmé a dalších řad.
    """
    client = _tmdb()
    tmdb_id = int(params["id"])
    title = params.get("title") or ""
    seasons = client.seasons(tmdb_id)
    numbers = [season["number"] for season in seasons]
    start = 0
    if str(last or "").isdigit() and int(last) in numbers:
        # okno končí řadou po té naposledy sledované (další díl může být až v ní)
        start = max(0, min(numbers.index(int(last)) + 2, len(seasons)) - SEASON_ROWS)
    shown = seasons[start:start + SEASON_ROWS]
    rows, order = [], []
    for season in shown:
        try:
            episodes = client.season_episodes(tmdb_id, season["number"])
        except TmdbError:
            episodes = {}
        if not episodes:
            continue
        numbers = sorted(episodes, key=int)
        with api._library() as lib:
            played = _played_episodes(lib, tmdb_id, season["number"], numbers)
        cards = []
        for number in numbers:
            card = _episode_card(params, season["number"], number, episodes[number], state, played.get(number))
            order.append((card, played.get(number), (len(rows), len(cards))))
            cards.append(card)
        rows.append({"title": "%s (%d dílů)" % (season["title"], len(cards)), "items": cards})
    rest = [api._card(season["title"], api._target(
        "list", "discover_season", dict(params, season=season["number"]), "%s – %s" % (title, season["title"])),
        "%d dílů" % season["episodes"] if season["episodes"] else "",
        season["poster"] or params.get("poster") or "", icon="series")
        for season in seasons[:start] + seasons[start + SEASON_ROWS:]]
    if rest:
        rows.append({"title": "Další řady", "items": rest})
    if not order:
        return rows, None, None
    # rozkoukaný díl má přednost; jinak díl po posledním zhlédnutém; jinak první
    unfinished = [index for index, entry in enumerate(order) if entry[1] and state["resume"].get(entry[1])]
    watched = [index for index, entry in enumerate(order) if entry[1] and entry[1] in state["watched"]]
    if unfinished:
        chosen, verb = order[unfinished[-1]], "Pokračovat"
    elif watched and watched[-1] + 1 < len(order):
        chosen, verb = order[watched[-1] + 1], "Přehrát další díl"
    else:
        chosen, verb = order[0], "Přehrát"
    card = chosen[0]
    target = dict(card["target"], params=dict(card["target"]["params"], resume=True)) if unfinished else card["target"]
    return rows, {"title": verb, "subtitle": card["title"], "target": target}, chosen[2]


def discover_episode_play(params):
    """Přehraje díl seriálu z Objevuj rovnou: nejlepší verzi podle předvoleb (nebo tu naposledy zvolenou)."""
    season, episode = int(params.get("season") or 0), int(params.get("episode") or 0)
    tmdb_id = params.get("id") or 0
    with api._library() as lib:
        state = api._state(lib)
        if tmdb_id:
            _remember_title(lib, "tv", params)
    choice = "tv:%s:%s:%s" % (tmdb_id, season, episode) if tmdb_id else ""
    versions = [version for version in _find_versions(_names(params), 0, (season, episode), state, choice=choice)
                if not version["short"]]
    if not versions:
        raise api.ApiError("Na Přehraj.to jsem tento díl nenašel.\n\nPodržte OK na dílu a zvolte „Vybrat verzi“, "
                           "nebo ho zkuste najít hledáním.")
    versions.sort(key=lambda version: version["bad"])   # verze, která nešla přehrát, až po ostatních
    best = versions[0]
    request = {"vid": best["video"]["id"], "web_result": best["result"]}
    if choice:
        request["choice"] = choice
    for key in ("restart", "fresh", "online"):
        if params.get(key):
            request[key] = params[key]
    stream = api.play(request)
    stream["choice"] = choice   # přehrávač podle něj po dokoukání nabídne další díl
    return stream


def discover_movie_play(params):
    """Přehraje film z Objevuj rovnou, bez otevření jeho stránky: nejlepší verzi podle předvoleb."""
    tmdb_id = params.get("id") or 0
    with api._library() as lib:
        state = api._state(lib)
    choice = "movie:%s" % tmdb_id if tmdb_id else ""
    versions = [version for version in _find_versions(_names(params), int(params.get("year") or 0), None, state,
                                                      choice=choice) if not version["short"]]
    if not versions:
        raise api.ApiError("Na Přehraj.to jsem tento film nenašel.\n\nOtevřete jeho stránku a zkuste „Hledat ručně“.")
    versions.sort(key=lambda version: version["bad"])
    best = versions[0]
    request = {"vid": best["video"]["id"], "web_result": best["result"]}
    if choice:
        request["choice"] = choice
    for key in ("restart", "fresh", "online"):
        if params.get(key):
            request[key] = params[key]
    stream = api.play(request)
    stream["choice"] = choice
    return stream


def discover_season(params):
    """Díly jedné řady podle TMDB; každý díl vede na své verze na Přehraj.to."""
    season = int(params.get("season") or 0)
    tmdb_id = int(params.get("id") or 0)
    episodes = _tmdb().season_episodes(tmdb_id, season)
    with api._library() as lib:
        state = api._state(lib)
        played = _played_episodes(lib, tmdb_id, season, episodes)
    cards = [_episode_card(params, season, number, episodes[number], state, played.get(number))
             for number in sorted(episodes, key=int)]
    if cards:
        target = api._target("command", "discover_download_season", params)
        target["confirm"] = api.app_extras.space_question(
            "Stáhnout celou řadu?", min(len(cards), SEASON_DOWNLOAD_MAX),
            note="Díly se nejdřív vyhledají na Přehraj.to; kolik místa zaberou, se ukáže potom.")
        cards.append(api._card("Stáhnout celou řadu", target, "Vyhledá a zařadí všechny díly", icon="download"))
    return {"items": cards, "next": None, "empty": "TMDB u této řady neuvádí žádné díly."}


def discover_download_season(params):
    """Stáhne celou řadu seriálu z Objevuj: ke každému dílu vyhledá nejlepší verzi a zařadí ji do fronty."""
    def work():
        season, tmdb_id = int(params.get("season") or 0), int(params.get("id") or 0)
        episodes = _tmdb().season_episodes(tmdb_id, season)
        with api._library() as lib:
            state = api._state(lib)
        numbers = sorted(episodes, key=int)[:SEASON_DOWNLOAD_MAX]
        added, missing, known, unknown = 0, 0, 0, 0
        for index, number in enumerate(numbers, 1):
            api._STATE["progress"] = "Hledám díly: %d/%d" % (index, len(numbers))
            choice = "tv:%s:%s:%s" % (tmdb_id, season, number)
            try:
                versions = _find_versions(_names(params), 0, (season, int(number)), state, pages=1, choice=choice)
            except api.ApiError:
                missing += 1
                continue
            good = [version for version in versions if not version["short"]]
            if not good:
                missing += 1
                continue
            vid = good[0]["video"]["id"]
            entry = state["downloads"].get(vid) or {}
            if entry.get("state") in ("queued", "downloading") or api._saved(state, vid):
                continue
            api.download_add({"vid": vid, "web_result": good[0]["result"]})
            added += 1
            size = api._size_bytes(good[0]["result"].get("size"))
            known, unknown = known + size, unknown + (0 if size else 1)
        note = " %d dílů jsem na Přehraj.to nenašel." % missing if missing else ""
        estimate = api.app_extras.size_note(known, unknown)
        note += " Potřeba %s." % estimate if estimate and added else ""
        return {"message": "Zařazeno ke stažení: %d dílů.%s" % (added, note), "start": bool(added)}

    return api._exclusive(work)


def _remember_title(lib, kind, params):
    if params.get("id"):
        lib.set_kv(TITLE_PREFIX + "%s:%s" % (kind, params["id"]), json.dumps({
            key: params.get(key) or "" for key in ("id", "title", "orig", "year", "poster")}))


def _title_info(lib, kind, tmdb_id):
    try:
        info = json.loads(lib.get_kv(TITLE_PREFIX + "%s:%s" % (kind, tmdb_id)) or "null")
    except ValueError:
        info = None
    return info if isinstance(info, dict) and info.get("title") else None


def _recent_series(lib):
    try:
        recent = json.loads(lib.get_kv(RECENT_SERIES) or "[]")
    except ValueError:
        recent = []
    return [item for item in recent if isinstance(item, (int, str))] if isinstance(recent, list) else []


def remember_series(lib, choice):
    """Přehrává se díl seriálu z Objevuj (``tv:<id>:<řada>:<díl>``): seriál patří mezi rozkoukané."""
    _prefix, tmdb_id, season = (choice.split(":") + ["", ""])[:3]
    if not tmdb_id.isdigit() or not season.isdigit():
        return
    lib.set_kv("last_season:%s" % tmdb_id, season)
    recent = [item for item in _recent_series(lib) if str(item) != tmdb_id]
    lib.set_kv(RECENT_SERIES, json.dumps(([int(tmdb_id)] + recent)[:10]))


def discover_episode(params):
    season, episode = int(params.get("season") or 0), int(params.get("episode") or 0)
    tmdb_id = params.get("id") or 0
    with api._library() as lib:
        state = api._state(lib)
        if tmdb_id:
            lib.set_kv("last_season:%s" % tmdb_id, str(season))  # pro „Pokračovat“ na stránce seriálu
            _remember_title(lib, "tv", params)
    choice = "tv:%s:%s:%s" % (tmdb_id, season, episode) if tmdb_id else ""
    versions = _find_versions(_names(params), 0, (season, episode), state, choice=choice)
    cards = [_version_card(version, state, choice) for version in versions]
    query = search_results.format_query(params.get("title") or "", 0, (season, episode))
    if not cards:
        cards = [api._card("Na Přehraj.to jsem tento díl nenašel", api._target("search", "search", {"q": query}),
                           "Zkuste hledat ručně", icon="search")]
    return {"items": cards, "next": None}


# ---------------------------------------------------------------------------
# Po dokoukání: další díl seriálu, nebo další film série
# ---------------------------------------------------------------------------

def _next_item(title, subtitle, target, auto=False):
    """Návrh pro přehrávač; ``auto`` = spustit sám po odpočtu (jen další díl, ne celý další film)."""
    return {"title": title, "subtitle": subtitle, "target": target, "auto": auto}


def _catalogue_next_episode(lib, video, state):
    """Další díl seriálu z vlastního katalogu (nejlepší verze podle předvoleb)."""
    best = {}
    for other in lib.videos_for_meta(video["meta_key"]):
        if not other["episode"]:
            continue
        position = (other["season"], other["episode"])
        rank = api._version_rank(other, state["prefs"], state["bad"])
        if position not in best or rank < best[position][0]:
            best[position] = (rank, other)
    # dvojdíl (1×01–02) končí druhým dílem: další je až ten za ním
    later = sorted(position for position in best if position > (video["season"], api._last_episode(video)))
    if not later:
        return None
    following = best[later[0]][1]
    return following, _episode_label(lib, following)


def _missing_between(video, following):
    """Díl, který mezi dokoukaným a nabízeným chybí: ``(řada, díl)``, nebo None, když na sebe navazují.

    Navazuje díl s číslem o jedna vyšším a první díl další řady (kolik dílů řada má, katalog neví)."""
    position, later = (video["season"], api._last_episode(video)), (following["season"], following["episode"])
    if later == (position[0], position[1] + 1) or later == (position[0] + 1, 1):
        return None
    return position[0], position[1] + 1


def _episode_label(lib, video, position=None):
    meta = lib.get_meta(video["meta_key"]) or {}
    code = "%d×%02d" % position if position else api._episode_code(video)
    return "%s %s" % (api._display_title(meta) or video["title"], code)


def _searched_next_episode(lib, video, state):
    """Další díl seriálu, který v katalogu není: vyhledá se na Přehraj.to podle názvu seriálu."""
    following = (video["season"], api._last_episode(video) + 1)
    meta = lib.get_meta(video["meta_key"]) or {}
    names = [api._display_title(meta) or video["title"], meta.get("orig_title") or "", video["title"]]
    versions = [version for version in _find_versions(names, 0, following, state, pages=1) if not version["short"]]
    if not versions:
        return None
    return _next_item("Další díl", _episode_label(lib, video, following),
                      _version_card(versions[0], state)["target"], auto=True)


def _tmdb_next_episode(lib, choice, state):
    """Další díl seriálu z Objevuj: stejná řada, nebo první díl následující."""
    _prefix, tmdb_id, season, episode = (choice.split(":") + ["", "", ""])[:4]
    info = _title_info(lib, "tv", tmdb_id)
    if not info or not season.isdigit() or not episode.isdigit():
        return None
    client = _tmdb()
    season, episode = int(season), int(episode)
    following, episodes = (season, episode + 1), client.season_episodes(int(tmdb_id), season)
    if str(episode + 1) not in episodes:
        if not any(item["number"] == season + 1 for item in client.seasons(int(tmdb_id))):
            return None
        following, episodes = (season + 1, 1), client.season_episodes(int(tmdb_id), season + 1)
        if "1" not in episodes:
            return None
    new_choice = "tv:%s:%d:%d" % (tmdb_id, following[0], following[1])
    versions = [version for version in _find_versions([info["title"], info.get("orig") or ""], 0, following, state,
                                                      choice=new_choice) if not version["short"]]
    if not versions:
        return None
    card = _version_card(versions[0], state, new_choice)
    label = "%s %d×%02d" % (info["title"], following[0], following[1])
    name = (episodes.get(str(following[1])) or {}).get("title")
    return _next_item("Další díl", " · ".join(part for part in (label, name) if part), card["target"], auto=True)


def _next_film(lib, tmdb_id, state):
    """Další film stejné série (kolekce v TMDB) podle data uvedení."""
    client = _tmdb()
    collection = client.details(int(tmdb_id)).get("collection_id")
    if not collection:
        return None
    parts = client.collection_movies(collection)
    index = next((i for i, part in enumerate(parts) if part["id"] == int(tmdb_id)), None)
    if index is None or index + 1 >= len(parts):
        return None
    part = parts[index + 1]
    date = part.get("release_date") or ""
    year = int(date[:4]) if date[:4].isdigit() else 0
    names = [part["title"], part.get("original_title") or ""]
    choice = "movie:%s" % part["id"]
    label = "%s (%d)" % (part["title"], year) if year else part["title"]
    # nejdřív vlastní katalog, pak hledání na Přehraj.to; když není nikde, nabídne se stránka titulu
    wanted = search_results.aliases(*names)
    for meta in lib.search(part["title"])[0]:
        if search_results.normalize(api._display_title(meta)) in wanted and (not year or api._year(meta) == year):
            versions = lib.videos_grouped([meta["key"]])
            if versions.get(meta["key"]):
                return _next_item("Další film série", label, api._movie_card(meta, state, versions)["target"])
    found = [version for version in _find_versions(names, year, None, state, choice=choice) if not version["short"]]
    if found:
        return _next_item("Další film série", label, _version_card(found[0], state, choice)["target"])
    poster = part.get("poster_path") or ""
    return _next_item("Další film série", label, api._target("title", "discover_title", {
        "kind": "movie", "id": part["id"], "title": part["title"], "year": year,
        "poster": IMG + "w342" + poster if poster.startswith("/") else "",
        "orig": part.get("original_title") or ""}, part["title"]))


def play_next(params):
    """Co nabídnout po dokoukání videa: ``{"next": {"title", "subtitle", "target"} | None}``.

    Díl seriálu → další díl (z katalogu, nebo u seriálu z Objevuj vyhledaný na Přehraj.to).
    Film → další film stejné série podle TMDB. Když nic dalšího není nebo to nejde zjistit, nic.
    """
    track = str(params.get("track") or "")
    choice = str(params.get("choice") or "")
    if track.startswith(api.IVYS):
        try:
            return {"next": api.ivys_next(track[len(api.IVYS):])}   # další díl téhož pořadu z iVysílání
        except api.ApiError:
            return {"next": None}
    try:
        with api._library() as lib:
            state = api._state(lib)
            video = lib.find_video(track) if track else None
            if choice.startswith("tv:"):
                return {"next": _tmdb_next_episode(lib, choice, state)}
            if video and video["kind"] == "episode" and video["episode"]:
                found = _catalogue_next_episode(lib, video, state)
                gap = _missing_between(video, found[0]) if found else None
                if found and not gap:
                    return {"next": _next_item("Další díl", found[1],
                                               api._target("play", "play", {"vid": found[0]["id"]}), auto=True)}
                # Bezprostředně následující díl v katalogu není: nejdřív ho zkusit najít na Přehraj.to.
                try:
                    searched = _searched_next_episode(lib, video, state)
                except api.ApiError:
                    searched = None
                if searched or not found:
                    return {"next": searched}
                # Katalog má až pozdější díl: nabídnout ho, ale nespouštět sám a říct, který chybí.
                return {"next": _next_item("Další díl v katalogu", "%s · díl %d×%02d chybí" % ((found[1],) + gap),
                                           api._target("play", "play", {"vid": found[0]["id"]}))}
            tmdb_id = choice[len("movie:"):] if choice.startswith("movie:") else ""
            if not tmdb_id and video and api._tmdb().enabled:
                meta = lib.get_meta(video["meta_key"]) or {}
                tmdb_id = meta.get("tmdb_id") or ""
                if not tmdb_id and meta.get("status") != "ok":
                    # film z hledání bez metadat: dohledat podle názvu a roku ze jména souboru
                    found = api._tmdb().find(video["title"], video["year"] or 0)
                    tmdb_id = (found or {}).get("tmdb_id") or ""
            if str(tmdb_id).isdigit() and int(tmdb_id) > 0:
                return {"next": _next_film(lib, tmdb_id, state)}
    except (api.ApiError, TmdbError):
        pass  # návrh je jen pohodlí: když se nepovede, přehrávač se prostě zavře
    return {"next": None}


def next_up_cards(lib, state, limit=10):
    """Karty „Další díly“: po dokoukaném dílu seriálu z katalogu ten následující, u seriálů z Objevuj stránka."""
    cards, seen = [], set()
    hidden = {meta["key"] for meta in lib.hidden_progress()}   # seriály, u kterých si uživatel další díly nepřál
    for video in lib.history_videos(limit=40, include_search=True):
        if video["kind"] != "episode" or not video["episode"] or video["meta_key"] in seen:
            continue
        seen.add(video["meta_key"])      # rozhoduje naposledy přehraný díl seriálu
        if video["id"] not in state["watched"] or video["meta_key"] in hidden:
            continue
        found = _catalogue_next_episode(lib, video, state)
        if found and found[0]["id"] not in state["watched"] and not state["resume"].get(found[0]["id"]):
            meta = lib.get_meta(video["meta_key"])
            card = api._video_card(found[0], meta, state)
            gap = _missing_between(video, found[0])
            note = "Další v katalogu, %d×%02d chybí" % gap if gap else "Další díl"
            card["subtitle"] = " · ".join(part for part in (note, card["subtitle"]) if part)
            card["menu"] = (card.get("menu") or []) + [{"title": "Nenabízet další díly", "target": api._target(
                "command", "next_up_hide", {"key": video["meta_key"]})}]
            cards.append(card)
    for tmdb_id in _recent_series(lib):
        info = _title_info(lib, "tv", tmdb_id)
        if info:
            card = _title_card("tv", info.get("id") or tmdb_id, info["title"], int(info.get("year") or 0),
                               info.get("poster") or "", info.get("orig") or "", state)
            card["subtitle"] = "Pokračovat ve sledování"
            card["menu"] = (card.get("menu") or []) + [{"title": "Nenabízet další díly", "target": api._target(
                "command", "next_up_hide", {"tmdb": tmdb_id})}]
            cards.append(card)
    return cards[:limit]


def next_up_hide(params):
    """Seriál z řady „Další díly“ zmizí, dokud se z něj zase něco nepustí."""
    with api._library() as lib:
        if params.get("key"):
            lib.set_progress_hidden(str(params["key"]), True)
        elif params.get("tmdb"):
            recent = [item for item in _recent_series(lib) if str(item) != str(params["tmdb"])]
            lib.set_kv(RECENT_SERIES, json.dumps(recent))
        else:
            raise api.ApiError("Chybí seriál.")
    return {"message": "", "toast": "Další díly tohoto seriálu se nabízet nebudou, dokud si z něj něco nepustíte."}


def search_titles(query):
    """Filmy a seriály z TMDB pro hledaný výraz; bez klíče TMDB nebo při chybě nic."""
    client = api._tmdb()
    if not client.enabled:
        return []
    try:
        items, _pages = client.search_titles(query)
    except TmdbError:
        return []
    return [item for item in items[:20] if isinstance(item, dict) and item.get("id")]


def title_rows(items, state, results):
    """Řady Filmy a Seriály z výsledků TMDB. ``results`` jsou soubory, které na stejný dotaz vrátilo
    Přehraj.to: titul, který mezi nimi je, dostane štítek a jde dopředu – je vidět hned, co se dá pustit."""
    parsed = [nameparser.parse(result["title"]) for result in results
              if isinstance(result, dict) and result.get("title")]
    rows = []
    for kind, name in (("movie", "Filmy"), ("tv", "Seriály")):
        listed = [item for item in items if (item.get("media_type") == "tv") == (kind == "tv")]
        available = {}
        for item in listed:
            names = search_results.aliases(item.get("title") or item.get("name") or "",
                                           item.get("original_title") or item.get("original_name") or "")
            date = item.get("release_date") or item.get("first_air_date") or ""
            year = int(date[:4]) if date[:4].isdigit() else 0
            if kind == "movie":
                count = len([entry for entry in parsed if search_results.filename_matches(entry, names, year, None, "movie")])
            else:
                count = len([entry for entry in parsed
                             if entry["kind"] == "episode" and search_results.normalize(entry["title"]) in names])
            if count:
                available["%s:%s" % (kind, item["id"])] = count
        listed.sort(key=lambda item: "%s:%s" % (kind, item["id"]) not in available)   # dostupné první, jinak pořadí TMDB
        marked = dict(state, available=available)
        cards = [_item_card(item, kind, marked) for item in listed]
        if cards:
            rows.append({"title": name, "items": cards})
    return rows


def people_row(query):
    """Řada Herci a režiséři pro hledaný výraz."""
    client = api._tmdb()
    if not client.enabled:
        return []
    try:
        people, _pages = client.search_people(query)
    except TmdbError:
        return []
    persons = []
    for person in people[:12]:
        photo = person.get("profile_path") or ""
        persons.append(api._card(
            person["name"], api._target("list", "discover_person", {"id": person["id"], "name": person["name"]},
                                        person["name"]),
            DEPARTMENTS.get(person.get("known_for_department") or "", ""),
            IMG + "h632" + photo if isinstance(photo, str) and photo.startswith("/") else "",
            wide=not photo, icon="movies"))
    return [{"title": "Herci a režiséři", "items": persons}] if persons else []


DEPARTMENTS = {"Acting": "herectví", "Directing": "režie", "Writing": "scénář", "Production": "produkce"}
PERSON_PAGE = 40


def discover_person(params):
    """Filmy a seriály jedné osoby: nejdřív ty, které režírovala, pak ty, kde hraje; od nejnovějších."""
    person_id = int(params.get("id") or 0)
    if not person_id:
        raise api.ApiError("Chybí osoba.")
    offset = api._offset(params)
    client = _tmdb()
    credits, seen = [], set()
    for role, items in (("režie", client.directing_credits(person_id)), ("", client.acting_credits(person_id))):
        for item in items:
            key = (item.get("media_type"), item.get("id"))
            if key not in seen:
                seen.add(key)
                credits.append((role or item.get("character") or "", item))
    with api._library() as lib:
        state = api._state(lib)
    cards = []
    for role, item in credits[offset:offset + PERSON_PAGE]:
        card = _item_card(item, "tv" if item.get("media_type") == "tv" else "movie", state)
        card["subtitle"] = " · ".join(part for part in (card["subtitle"], role) if part)
        cards.append(card)
    following = {"call": "discover_person", "params": dict(params, offset=offset + PERSON_PAGE)} \
        if offset + PERSON_PAGE < len(credits) else None
    return {"title": params.get("name") or "", "items": cards, "next": following,
            "empty": "TMDB u této osoby neuvádí žádné filmy ani seriály."}


ACTIONS = {
    "favorites": favorites, "favorite_toggle": favorite_toggle,
    "discover": discover, "discover_years": discover_years, "discover_genres": discover_genres,
    "discover_czechoslovak": discover_czechoslovak, "discover_title": discover_title,
    "discover_season": discover_season, "discover_episode": discover_episode, "discover_check": discover_check,
    "play_next": play_next, "title_csfd": title_csfd, "favorites_check": favorites_check,
    "discover_download_season": discover_download_season, "discover_filters": discover_filters,
    "discover_person": discover_person, "discover_episode_play": discover_episode_play,
    "discover_movie_play": discover_movie_play, "discover_row": discover_row, "next_up_hide": next_up_hide,
}
