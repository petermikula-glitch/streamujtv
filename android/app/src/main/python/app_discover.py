# -*- coding: utf-8 -*-
"""Objevuj a Oblíbené – část rozhraní aplikace (akce se registrují v app_api.ACTIONS).

Přehraj.to nemá katalog podle roku ani žánru, jen hledání podle názvu souboru. Seznamy titulů proto
dodává TMDB (podle roku, žánru, země původu) a teprve u vybraného titulu se na Přehraj.to hledají
jeho verze. Oblíbené jsou jen v aplikaci (favorites.json): položka katalogu, nebo titul z TMDB.
"""
import json
import os
import re
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
AVAILABLE_RULES = 2              # pravidla, podle kterých se počítá „je k mání“ (2 = štítek HD není „pod Full HD“)
AVAILABLE_TTL = 14 * 24 * 3600   # nalezený titul: soubory mizí zřídka (a stránka titulu údaj opraví sama)
MISS_TTL = 6 * 3600              # nalezený titul, který se při novém ověření nenašel: zkusit znovu po této době
MISSES_TO_DROP = 2               # po tolika nepovedených ověřeních za sebou se titul přestane nabízet
TV_MISSING_TTL = 7 * 24 * 3600   # „v iVysílání není“ platí déle: seznam titulů se prochází celý a ČT přidává pomalu
MISSING_TTL = 2 * 24 * 3600      # nenalezený titul se ověří znovu dřív: nové soubory přibývají
# … a tím dřív, čím je titul novější (rok uvedení → za jak dlouho znovu): novinky přibývají ze dne na den
MISSING_FRESH = 12 * 3600        # letošní a loňský titul
MISSING_OLD = 10 * 24 * 3600     # titul starší pěti let
LOCAL_NAMES = 3                  # tolik dalších názvů z TMDB (slovenský, jiný český) se nejvýš zkouší
QUERIES_MAX = 6                  # tolik dotazů nejvýš na jedno hledání, na které někdo čeká
PACE_FASTEST = 0.4               # s mezi dotazy ověřování výpisu, na který se někdo dívá
PACE_SLOWEST = 4.0               # když Přehraj.to dotazy omezuje, zpomalí se až na tolik
GENTLE_PACE = 1.5                # s mezi dotazy, když se seznam plní sám na pozadí (nikdo nečeká)
GENTLE_SLOWEST = 8.0             # … a když je Přehraj.to odmítá, zpomalí se až na tolik
# Přehraj.to odmítlo dotaz (HTTP 429): ověřování na pozadí se na chvíli zastaví úplně, aby zbylo na
# hledání, na které někdo čeká. Když odmítne znovu, čeká se pokaždé dvakrát déle.
LIMIT_WAIT = 90                  # s: první přestávka
LIMIT_WAIT_MAX = 1800            # s: nejdelší přestávka
LIMIT_FORGET = 1800              # s bez odmítnutí, po kterých se zase začíná krátkou přestávkou
LIMIT_DISTRUST = 900             # s po odmítnutí: „nenašlo se“ u titulu, který tam byl, se nepočítá
CHECK_WORKERS = 4                # kolik titulů se ověřuje souběžně
ONLY_ENOUGH = 12                 # výpis „jen dostupné“: tolik titulů stačí na jedno načtení…
ONLY_PAGES = 3                   # …a víc stránek TMDB kvůli nim najednou neprojde,
ONLY_SECONDS = 12                # ani když ověřování trvá déle než tolik sekund
SEARCH_RETRY_WAIT = 2.5          # po dočasné chybě hledání se tolik sekund počká a zkusí se to jednou znovu
SEARCH_HOLD = 20                 # tolik sekund po hledání, na které někdo čeká, ověřování na pozadí nezačíná další dávku
CHECK_PAUSE = 120                # služba, která neodpovídá, se tolik sekund nezkouší (výpis na ni nečeká)
CHECK_WAIT = 20                  # déle výpis nečeká, až doběhne jiné ověřování
BACKGROUND = True                # výpis na ověřování nečeká: tituly ukáže hned a ověřuje je na pozadí
VERIFY_QUEUE = 120               # víc titulů na ověření nečeká (starší požadavky propadnou)
RECHECK_REFRESH = 8              # totéž, když se jen potichu přeověřují starší údaje
VERIFY_REFRESH = 2               # po kolika sekundách si má obrazovka říct o čerstvý stav
# Ověřování na pozadí: fronta titulů (naposledy otevřený výpis první), právě ověřované a ty, které se
# ověřit nepodařilo (chvíli se nezkoušejí, aby se výpis neobnovoval donekonečna).
_VERIFY = {"queue": [], "active": set(), "thread": None, "failed": {}, "hold": 0}
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
    movies = [entry for entry in load_favorites().values()
              if not entry.get("key") and entry.get("kind") == "movie" and entry.get("tmdb_id")]
    with api._library() as lib:
        state = api._state(lib)
        # film může mít jazyk a kvalitu nastavené po svém: hlídá se verze podle nich
        states = {entry["id"]: api._title_state(lib, state, "movie:%s" % entry["tmdb_id"]) for entry in movies}
    # čekají tituly bez verze podle (současných) předvoleb; nejdéle neověřené první
    waiting = sorted((entry for entry in movies
                      if not (entry.get("match") == 1
                              and entry.get("match_prefs") == _prefs_mark(states[entry["id"]]["prefs"]))),
                     key=lambda entry: entry.get("checked") or 0)[:FAVORITES_BATCH]
    found = []
    for entry in waiting:
        own = states[entry["id"]]
        prefs, mark = own["prefs"], _prefs_mark(own["prefs"])
        try:
            versions = _find_versions([entry.get("title") or "", entry.get("orig") or ""],
                                      int(entry.get("year") or 0), None, own, pages=1, thorough=False)
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
        if "minimum" not in own["own"]:   # seznam dostupných titulů se řídí nastavením aplikace
            _remember_availability("movie", entry["tmdb_id"], len(good), year=int(entry.get("year") or 0),
                                   extra=_summary(good))
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
    dubbed, best = state.get("summary", {}).get("%s:%s" % (kind, tmdb_id)) or ("", 0)
    if known and badges:
        # co o verzích na Přehraj.to víme: je tam český (jinak slovenský) dabing, je tam 4K
        badges += [code for code in (("CZ" if "cz" in dubbed else "SK" if "sk" in dubbed else ""),
                                     "4K" if best >= 2160 else "") if code]
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
    v iVysílání | "" | None}}``; ``None`` = iVysílání se u titulu ještě neověřovalo.

    Co se jednou zjistilo, zůstává: záznam, kterému vypršela platnost, se vrací dál, jen s ``stale`` –
    titul nalezený na Přehraj.to se tedy ukazuje, dokud nové ověření neřekne, že tam už není, a jen
    se na pozadí ověří znovu. Dřív se takový titul bral jako neznámý: zmizel z výpisu a čekalo se na
    nové ověření. Platnost: nalezený ``AVAILABLE_TTL`` (po jednom nepovedeném ověření ``MISS_TTL``),
    nenalezený podle stáří titulu (``ttl`` v záznamu)."""
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
                now - (entry.get("its") or entry.get("ts") or 0) >= (AVAILABLE_TTL if television else TV_MISSING_TTL):
            television = None
        if not count and entry.get("r") != AVAILABLE_RULES:
            continue              # „není tam“ podle dřívějších pravidel kvality: jako by se neověřovalo
        if count:
            valid = MISS_TTL if entry.get("miss") else AVAILABLE_TTL
        else:
            valid = AVAILABLE_TTL if television else entry.get("ttl") or MISSING_TTL
        known[key] = {"n": count, "iv": television}
        if "d" in entry:   # souhrn verzí (od verze se seznamem dostupných titulů)
            known[key].update(d=entry.get("d") or "", q=int(entry.get("q") or 0), s=int(entry.get("s") or 0))
        if now - (entry.get("ts") or 0) >= valid or (count and "d" not in entry):
            known[key]["stale"] = True   # ověřit znovu (nebo doplnit souhrn verzí), ale dál platí
    return known


def _rewrite_records(change):
    """Projde všechny záznamy o dostupnosti a uloží ty, které ``change(záznam)`` upravila (vrátila True)."""
    with api._library() as lib, lib.conn:
        rows = lib.conn.execute("SELECT k, v FROM settings_kv WHERE k LIKE ?", (AVAILABLE_PREFIX + "%",)).fetchall()
        for key, value in rows:
            try:
                entry = json.loads(value or "null")
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("iv"), str) and "its" not in entry:
                entry["its"] = entry.get("ts") or 0      # údaj o iVysílání má dál své stáří
            if change(entry):
                lib.conn.execute("UPDATE settings_kv SET v=? WHERE k=?", (json.dumps(entry), key))


def minimum_changed(before, after):
    """Uživatel změnil nejnižší nabízenou kvalitu (``before`` → ``after``, v řádcích; 0 = bez omezení).

    Co je „k mání“, se tím počítá jinak – ale co už se zjistilo, se nezahazuje (dřív se seznam smazal
    celý a ověřovalo se od začátku). Nalezené tituly, jejichž nejlepší známá kvalita novému požadavku
    stačí, zůstávají, jak jsou – znovu se neověřují (dřív se ověřovaly všechny a stálo to stovky dotazů,
    které pak chyběly na nové tituly). Při zpřísnění se hned skryjí a ověří znovu ty, jejichž kvalita
    nestačí nebo není známá. Nenalezené se při zmírnění berou jako neověřené (mohou teď vyhovět),
    při zpřísnění platí dál."""
    def change(entry):
        if entry.get("n"):
            if after <= before or int(entry.get("q") or 0) >= after:
                return False                             # vyhovuje dál: není co ověřovat
            entry.update(n=0, x=1, ts=0)                 # skrytý nastavením; nové ověření rozhodne
            entry.pop("miss", None)
        elif after < before:
            if entry.get("iv"):
                entry.update(ts=0, x=1)                  # (``x``: až se najde, není „nově na Přehraj.to“)
            else:
                entry["r"] = 0                           # „není tam“ platilo pro přísnější požadavek
        else:
            return False
        return True

    _rewrite_records(change)


def reverify_all():
    """Všechno se ověří znovu: nalezené tituly zůstávají vidět a přeověří se na pozadí, „nenašlo se“
    přestává platit (titul je zase neověřený). Volá se jednou po aktualizaci, která ověřování mění –
    dřívější záznamy mohly vzniknout v době, kdy Přehraj.to dotazy odmítalo."""
    def change(entry):
        if entry.get("n") or entry.get("iv"):
            entry["ts"] = 0
            entry.pop("miss", None)
            if not entry.get("n"):
                entry["x"] = 1                           # až se najde, není „nově na Přehraj.to“
        else:
            entry["r"] = 0
        return True

    _rewrite_records(change)


def _fresh(known, key):
    """Je údaj o titulu čerstvý a úplný (není ho třeba ověřovat)?"""
    entry = known.get(key)
    return bool(entry) and not entry.get("stale") and entry["iv"] is not None


def _availability(lib, kind, ids):
    """Známá dostupnost titulů na Přehraj.to: {"movie:11": počet verzí}."""
    return {key: entry["n"] for key, entry in _known(lib, kind, ids).items()}


def _mark_known(state, known):
    """Doplní do stavu, kde které tituly jsou – karty podle toho dostanou štítky."""
    state.setdefault("available", {}).update({key: entry["n"] for key, entry in known.items()})
    state.setdefault("television", {}).update({key: entry["iv"] for key, entry in known.items() if entry["iv"]})
    state.setdefault("summary", {}).update({key: (entry["d"], entry["q"]) for key, entry in known.items()
                                            if "d" in entry and entry["n"]})
    state.setdefault("subtitled", set()).update(key for key, entry in known.items() if entry.get("s") and entry["n"])


def _missing_ttl(year):
    """Za jak dlouho se má znovu ověřit titul, který se nenašel: novinka brzy, starý film až za čas."""
    age = time.localtime().tm_year - int(year or 0) if year else 0
    return MISSING_FRESH if age <= 1 else MISSING_OLD if age > 5 else MISSING_TTL


FRESH_RELEASE = 150 * 24 * 3600   # titul uvedený před kratší dobou je při prvním nálezu „nově na Přehraj.to“


def _summary(versions):
    """Souhrn verzí titulu pro štítky, filtr a řazení: ``{"d": "cz,sk" (jaký dabing je k mání), "q":
    nejlepší známé rozlišení v řádcích, "s": 1 když je verze s českými nebo slovenskými titulky}``. Rozlišení jen tam, kde ho soubor říká přesně („1080p“, „4K“) – štítek
    „HD“ z výpisu znamená jen „720p nebo víc“."""
    dubs, best, subtitled = set(), 0, False
    for version in versions:
        if version.get("short"):
            continue
        video = version["video"]
        parts = {part.strip() for part in (video.get("lang") or "").split(",")}
        dubs.update(code.lower() for code in ("CZ", "SK") if code in parts or code + " dabing" in parts)
        token = api._exact_token(video.get("quality"))
        best = max(best, api.TOKEN_LINES.get(token, 0),
                   720 if provider.quality_token(video.get("quality") or "") == "HD" else 0)
        subtitled = subtitled or any("titulky" in part for part in parts)
    return {"d": ",".join(sorted(dubs)), "q": best, "s": int(subtitled)}


def _card_fields(item):
    """Co si seznam dostupných titulů pamatuje o titulu, aby z něj šla postavit karta bez dotazu na TMDB."""
    date = item.get("release_date") or item.get("first_air_date") or ""
    fields = {"t": item.get("title") or item.get("name") or "",
              "o": item.get("original_title") or item.get("original_name") or "",
              "y": int(date[:4]) if date[:4].isdigit() else 0, "rd": date[:10],
              "p": item.get("poster_path") or "", "v": int(round((item.get("vote_average") or 0) * 10))}
    if item.get("_fresh"):
        fields["f"] = int(time.time())   # titul z nabídky samotného Přehraj.to: počítá se jako nově přidaný
    return fields


def _remember_availability(kind, tmdb_id, count, television=None, year=0, extra=None, searched=True):
    """Uloží, co se o titulu zjistilo. ``television`` je pořad v iVysílání, ``""`` když tam není,
    ``None`` když se to nezjišťovalo (dřívější údaj pak zůstane). Podle roku uvedení (``year``) se
    určí, za jak dlouho se nenalezený titul ověří znovu. ``extra`` jsou další údaje záznamu: souhrn
    verzí (``_summary``) a údaje pro kartu (``_card_fields``).

    ``f`` v záznamu říká, odkdy je titul na Přehraj.to: nastaví se, když se titul najde poprvé po tom,
    co tam dřív nebyl, nebo když jde o čerstvě uvedený titul. Podle něj je řada „Nově na Přehraj.to“.

    ``searched=False``: na Přehraj.to se teď nehledalo, ``count`` je dřívější údaj a doplňuje se jen
    iVysílání. Stáří údaje o Přehraj.to (``ts``, ``miss``) se pak nemění – jinak by se titul, u kterého
    se jen znovu ověřuje iVysílání, na Přehraj.to už nikdy nehledal."""
    if not tmdb_id:
        return
    key = AVAILABLE_PREFIX + "%s:%s" % (kind, tmdb_id)
    now = int(time.time())
    entry = {"n": count, "ts": now, "r": AVAILABLE_RULES, "ttl": _missing_ttl(year)}
    with api._library() as lib:
        try:
            old = json.loads(lib.get_kv(key) or "null")
        except ValueError:
            old = None
        old = old if isinstance(old, dict) else {}
        if not searched and old.get("r") == AVAILABLE_RULES and "n" in old:
            kept = dict(old)
            kept.update({name: value for name, value in (extra or {}).items() if name in ("t", "o", "y", "rd", "p", "v")})
            if television is not None:
                kept["iv"], kept["its"] = television, now
            lib.set_kv(key, json.dumps(kept))
            return
        # údaje pro kartu a souhrn zůstávají, dokud je nepřepíše čerstvější
        entry.update({name: old[name] for name in ("t", "o", "y", "rd", "p", "v", "d", "q", "s", "f") if name in old})
        entry.update(extra or {})
        misses = int(old.get("miss") or 0)
        again = bool(misses) and now - int(old.get("ts") or 0) < MISS_TTL / 2
        if not count and old.get("n") and old.get("r") == AVAILABLE_RULES \
                and (misses + 1 < MISSES_TO_DROP or again):
            # Byl tam a teď se nenašel: jedno hledání se může splést (web zrovna nic nevrátil). Titul
            # zůstává, jen se brzy ověří znovu; přestane se nabízet až po dalším neúspěchu – a ten se
            # počítá, jen když přijde s odstupem (dva neúspěchy krátce po sobě jsou tentýž výpadek).
            count = entry["n"] = int(old["n"])
            entry["miss"] = misses if again else misses + 1
            if again:
                entry["ts"] = int(old.get("ts") or now)
            entry.update({name: old.get(name, default) for name, default in (("d", ""), ("q", 0), ("s", 0))})
        if not count:
            entry.pop("f", None)
        elif old.get("n"):
            # už tam byl: „odkdy“ se nemění (ani když ho mezitím nabídlo samo Přehraj.to)
            if "f" in old:
                entry["f"] = old["f"]
            else:
                entry.pop("f", None)
        elif "f" in (extra or {}) or old.get("x"):
            pass   # (``x``: titul skryla změna nejnižší kvality, ne to, že by na Přehraj.to nebyl)
        elif not old.get("n"):
            released = entry.get("rd") or ""
            try:
                fresh = bool(released) and now - time.mktime(time.strptime(released, "%Y-%m-%d")) < FRESH_RELEASE
            except (ValueError, OverflowError):
                fresh = False
            # dřív tam nebyl (ověřeno) a teď je, nebo je to novinka: počítá se jako nově přidaný
            if (old and old.get("r") == AVAILABLE_RULES and "n" in old) or fresh:
                entry["f"] = now
        if television is None:
            if isinstance(old.get("iv"), str):
                entry["iv"], entry["its"] = old["iv"], old.get("its") or old.get("ts") or 0
        else:
            entry["iv"], entry["its"] = television, now
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


def _count_versions(kind, tmdb_id, names, year, state, client, gentle=False):
    """Kolik verzí titulu je na Přehraj.to (u seriálu: kolik jeho dílů se našlo) a jejich souhrn
    (``_summary``). Když se pod českým a původním názvem nenajde nic, zkusí se ještě slovenský a další
    názvy z TMDB. ``gentle``: nikdo na výsledek nečeká, dotazy jdou pomalu (viz ``GENTLE_PACE``)."""
    def search(which):
        versions = _find_versions(which, year if kind == "movie" else 0, None, state, pages=1, client=client,
                                  queries_max=3, strict=True, thorough=False, series=kind == "tv",
                                  gentle=gentle)
        return [version for version in versions if not version["short"]]

    found = search(names)
    if not found and not gentle:
        # Další názvy (slovenský…) jen u výpisu, na který člověk čeká. Při plnění seznamu na pozadí
        # by u každého nenalezeného titulu zdvojnásobily počet dotazů – a Přehraj.to jich za hodinu
        # snese jen omezeně; stránka titulu je při otevření zkusí sama.
        extra = _local_names(kind, tmdb_id, names)
        if extra:
            found = search(extra)
    return len(found), _summary(found)


def search_paused():
    """Kolik sekund se ještě Přehraj.to na pozadí nezkouší (neodpovídá, nebo omezuje dotazy); 0 = jde to."""
    return max(0.0, _PAUSED.get("prehrajto", 0) - time.time(), limited())


def _check_available(kind, items, state, gentle=False, patient=False):
    """Ověří tituly, u kterých aplikace dostupnost nezná: hledání na Přehraj.to (u seriálu jeho
    první díl) a jedno v iVysílání, pár titulů souběžně. Výsledek si pamatuje (viz ``_known``);
    co se zjistit nepodařilo, zůstane neznámé a zkusí se příště. Vrací čerstvý stav všech titulů.
    ``gentle``: seznam se plní sám na pozadí, dotazy jdou pomalu. ``patient``: když právě běží jiné
    ověřování a nepustí, vrací ``None`` (volající to zkusí znovu), ne „co se ví“."""
    ids = [item["id"] for item in items]
    if not _CHECK_LOCK.acquire(timeout=CHECK_WAIT):
        if patient:
            return None
        # jiné ověřování běží moc dlouho (opuštěné výpisy): vrátit, co se ví, a nedržet další vlákno
        with api._library() as lib:
            return _known(lib, kind, ids)
    try:
        with api._library() as lib:
            known = _known(lib, kind, ids)
        todo = [item for item in items if not _fresh(known, "%s:%s" % (kind, item["id"]))]
        if not todo:
            return known
        client = _search_client(timeout=5)   # jeden klient pro všechna hledání (jedno přihlášení)
        # Předvolby se berou čerstvé: vlákno na pozadí si jinak nese ty z doby, kdy začalo, a po změně
        # nejnižší kvality by dál ukládalo výsledky podle té původní.
        prefs = api._prefs()
        state = dict(state, prefs=prefs)

        def paused(service):
            return _PAUSED.get(service, 0) > time.time() or (service == "prehrajto" and limited() > 0)

        def check(item):
            # Služba, která neodpovídá, se dál nezkouší: výpis by na ni čekal u každého titulu zvlášť.
            names, year = _item_names(item)
            television = None
            entry = known.get("%s:%s" % (kind, item["id"]))
            # Přehraj.to už ověřené je a údaj platí: chybí jen iVysílání
            count = entry["n"] if entry and not entry.get("stale") else None
            extra = _card_fields(item)
            searched = False               # hledalo se teď na Přehraj.to? (jinak je ``count`` dřívější údaj)
            if count is None and not paused("prehrajto"):
                try:
                    count, summary = _count_versions(kind, item["id"], names, year, state, client, gentle)
                    extra.update(summary)
                    searched = True
                    if not count and entry and entry["n"] and _distrust():
                        # Byl tam, a krátce po tom, co Přehraj.to odmítalo dotazy, se nenašel:
                        # takovému výsledku se nevěří, titul se ověří později.
                        count = None
                except _Yield:
                    count = None           # plnění seznamu ustoupilo; titul se ověří příště
                except api.ApiError:
                    _PAUSED["prehrajto"] = time.time() + max(CHECK_PAUSE, limited())
            # iVysílání jen tehdy, když se výsledek uloží (údaj o Přehraj.to je): jinak by to byl dotaz nazmar
            if count is not None and not paused("ivysilani"):
                television = _ivys_match(kind, names, year)
                if television is None:
                    _PAUSED["ivysilani"] = time.time() + CHECK_PAUSE
            return item["id"], count, television, year, extra, searched

        with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
            results = list(pool.map(check, todo))
        if api._prefs()["minimum"] != prefs["minimum"]:
            results = []                   # nejnižší kvalita se mezitím změnila: výsledky patří té původní
        for tmdb_id, count, television, year, extra, searched in results:
            # co se zjistilo, to se uloží; chybějící polovina (služba neodpovídala) se doplní příště
            before = known.get("%s:%s" % (kind, tmdb_id))
            if count is not None and (television is not None or before is None or before.get("stale")):
                _remember_availability(kind, tmdb_id, count, television, year, extra, searched=searched)
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
        # Dokud běží hledání, na které někdo čeká, další dávka nezačne: obojí naráz by si navzájem
        # bralo dotazy, které Přehraj.to v krátké době povolí.
        waited = 0
        while _VERIFY.get("hold", 0) > time.time() and waited < SEARCH_HOLD * 2:
            time.sleep(0.5)
            waited += 0.5
        with _VERIFY_LOCK:
            if not _VERIFY["queue"]:
                _VERIFY["thread"] = None
                return
            kind = _VERIFY["queue"][0][0][0]
            batch = [entry for entry in _VERIFY["queue"] if entry[0][0] == kind][:CHECK_WORKERS]
            _VERIFY["queue"] = [entry for entry in _VERIFY["queue"] if entry not in batch]
            _VERIFY["active"] = {key for key, _item in batch}
        try:
            known = _check_available(kind, [item for _key, item in batch], state, patient=True)
        except Exception:   # vlákno na pozadí nesmí spadnout: neověřené tituly se jen chvíli nezkoušejí
            known = {}
        if known is None:
            # Ověřování zrovna drží plnění seznamu na pozadí (ustupuje, ale dotaz musí doběhnout):
            # tituly, na které se člověk dívá, se kvůli tomu neoznačí za neověřitelné – zkusí se hned znovu.
            with _VERIFY_LOCK:
                _VERIFY["queue"][:0] = batch
                _VERIFY["active"] = set()
            continue
        with _VERIFY_LOCK:
            # Co se ověřit nepodařilo – i titul, o kterém se už něco ví –, se chvíli nezkouší: jinak
            # by ho otevřený výpis řadil do fronty znovu při každém obnovení. Když Přehraj.to dotazy
            # odmítá (nebo se jeho výsledku zatím nevěří), čeká se, až to pomine.
            again = time.time() + max(CHECK_PAUSE, limited(), _distrust())
            for key, _item in batch:
                if not _fresh(known, "%s:%s" % key):
                    _VERIFY["failed"][key] = again
            _VERIFY["active"] = set()


def _missing(known, kind, item):
    """Titul se na Přehraj.to hledal a není tam, a aplikace o něm neví ani z iVysílání."""
    entry = known.get("%s:%s" % (kind, item["id"]))
    return bool(entry) and not entry["n"] and not entry["iv"]


def _there(known, kind, item):
    """O titulu se ví, že je k mání: na Přehraj.to, nebo v iVysílání."""
    entry = known.get("%s:%s" % (kind, item["id"]))
    return bool(entry) and bool(entry["n"] or entry["iv"])


def _dubbed(known, kind, item):
    """Titul má český nebo slovenský dabing (co je v iVysílání, je česky)."""
    entry = known.get("%s:%s" % (kind, item["id"])) or {}
    return bool(entry.get("d") or entry.get("iv"))


def _collect(params, state, enough=ONLY_ENOUGH, pages_max=ONLY_PAGES):
    """Tituly výpisu od stránky ``page``: ``(tituly, poslední prošlá stránka, stránek celkem)``.

    Běžně jen ty, o kterých se ví, že jsou k mání na Přehraj.to nebo v iVysílání; protože jich na
    stránce TMDB může být málo, projde se jich víc (nejvýš ``pages_max``), dokud není titulů ``enough``.
    S ``avail=all`` vrací celou stránku bez ověřování.

    Titul, o kterém se zatím nic neví, se ve výpisu neukáže: ověří se na pozadí (``BACKGROUND``), jeho
    klíč je ve ``state["checking"]`` a obrazovka si za chvíli řekne znovu – výpis se tak plní postupně
    a je v něm jen to, co opravdu je k mání. Dál než na stránku, která se ještě ověřuje, se nejde:
    jinak by se ověřovaly desítky titulů z dalších stránek dřív než ty, na které se člověk dívá.
    Co se ověřit nepodařilo (služba neodpovídá), počítá ``state["unverified"]``.
    """
    kind = _kind(params)
    page = max(1, api._offset({"offset": params.get("page")}))
    everything = params.get("avail") == "all"
    found, started = [], time.time()
    checking = state.setdefault("checking", set())
    state.setdefault("unverified", 0)
    for current in range(page, page + (1 if everything else pages_max)):
        items, pages = _list_items(dict(params, page=current))
        items = [item for item in items if isinstance(item, dict) and item.get("id")]
        waiting = False
        if everything or BACKGROUND:
            with api._library() as lib:
                known = _known(lib, kind, [item["id"] for item in items])
            if not everything:
                # Na pozadí se ověří tituly, o kterých se neví nic, i ty, jejichž údaj zestárl nebo je
                # neúplný. Na výpis ale čekají jen ty první: co se už jednou zjistilo, platí dál.
                todo = [item for item in items if not _fresh(known, "%s:%s" % (kind, item["id"]))]
                pending = {"%s:%s" % key for key in _verify_later(kind, todo, state)}
                fresh = {key for key in pending if key not in known}
                checking.update(fresh)
                waiting = bool(fresh)
                state["rechecking"] = state.get("rechecking", 0) + len(pending - fresh)
                state["unverified"] += len([item for item in todo if "%s:%s" % (kind, item["id"]) not in known
                                            and "%s:%s" % (kind, item["id"]) not in pending])
        else:
            known = _check_available(kind, items, state)
            state["unverified"] += len([item for item in items if "%s:%s" % (kind, item["id"]) not in known])
        _mark_known(state, known)
        found += [item for item in items if everything or (
            _there(known, kind, item) and (not params.get("dub") or _dubbed(known, kind, item)))]
        if current >= pages or not items or len(found) >= enough or waiting \
                or time.time() - started > ONLY_SECONDS:
            break
    if not everything:
        found = _preferred_first(found, kind, state)
    return found, current, pages


def _preferred_first(items, kind, state):
    """Tituly v jazyce, kterému dává uživatel přednost, napřed: při předvolbě dabingu nejdřív ty
    s českým nebo slovenským dabingem (a co je v iVysílání), pak originály s titulky, pak ostatní;
    při předvolbě titulků naopak. Jinak zůstává pořadí TMDB (podle oblíbenosti)."""
    prefer = (state.get("prefs") or {}).get("language")
    if prefer not in ("dub", "subs"):
        return items

    def tier(item):
        key = "%s:%s" % (kind, item["id"])
        dubbed = bool((state.get("summary", {}).get(key) or ("", 0))[0]) or bool(state.get("television", {}).get(key))
        subtitled = key in state.get("subtitled", ())
        first, second = (dubbed, subtitled) if prefer == "dub" else (subtitled, dubbed)
        return 0 if first else 1 if second else 2

    return sorted(items, key=tier)   # stabilní: uvnitř skupiny pořadí TMDB


def _checking_note(state):
    """Co obrazovka ukáže a kdy se má zeptat znovu, dokud se část titulů ověřuje."""
    count = len(state.get("checking") or ())
    if count:
        return {"refresh": VERIFY_REFRESH,
                "status": "Hledám, co je k mání na Přehraj.to a v iVysílání – zbývá ověřit %d" % count}
    if state.get("unverified"):
        # nic se neověřuje a přesto něco chybí: služba neodpovídá; neověřené tituly se neukazují
        wait = limited()
        if wait:
            return {"status": "Přehraj.to teď omezuje počet dotazů – zbylé tituly se ověří asi za %s."
                              % _duration_text(wait + 59)}
        return {"status": "Část titulů se teď nepodařilo ověřit (Přehraj.to neodpovídá) – zkuste to za chvíli."}
    if state.get("rechecking"):
        # starší údaje se potichu ověřují znovu: výpis se za chvíli obnoví, kdyby se něco změnilo
        return {"refresh": RECHECK_REFRESH}
    return {}


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
    # „jen dostupné“: další stránka se nabízí, i když z těchto nevyšlo nic – aplikace pak hledá dál sama.
    # Dokud se tato část ověřuje, dál se nejde: obrazovka se obnoví a pokračuje, až bude jasno.
    following = {"call": "discover", "params": dict(params, page=last + 1)} \
        if last < pages and (cards or not everything) and not state.get("checking") else None
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
    if params.get("dub"):
        parts.append("s dabingem")
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
              ("Jazyk", "dub", [("", "Vše"), ("1", "Jen s českým nebo slovenským dabingem")], base.get("dub") or ""),
              ("Dostupnost", "avail", [("", "Jen na Přehraj.to a v iVysílání"), ("all", "Všechny tituly")],
               base.get("avail") or "")]
    filters = []
    for title, key, choices, current in groups:
        listed = options(key, choices, current)
        value = next((option["title"] for option in listed if option["current"]), "Vše")
        filters.append({"title": title, "value": value, "options": listed})
    extra = ("avail", "dub")   # filtry, které neřeší TMDB, ale seznam dostupných titulů
    active = any(base.get(key) for key in FILTER_KEYS + extra if key != "sort") or sort != _default_sort(base)
    reset = {key: value for key, value in base.items() if key not in FILTER_KEYS + extra} if active else None
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
            _remember_availability("movie", item["id"], len(good), year=int(date[:4]) if date[:4].isdigit() else 0,
                                   extra=dict(_card_fields(item), **_summary(good)))
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
    # tři řady po třech: co je na Přehraj.to, výběry a třídění
    return [
        api.app_index.catalogue_tile(kind),
        api._card("Nově na Přehraj.to", api._target("list", "discover_fresh", {"kind": kind},
                                                    "%s nově na Přehraj.to" % name), "Co přibylo v posledních týdnech",
                  icon="discover_new:" + icon),
        api._card("S dabingem", api._target("list", "discover", {"kind": kind, "category": "popular", "dub": "1"},
                                            "%s s dabingem" % name), "Česky nebo slovensky",
                  icon="discover_popular:" + icon),
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


_SEARCH = {"key": None, "client": None, "until": 0}   # společný klient pro hledání
_SEARCH_LOCK = threading.Lock()
ANONYMOUS_FOR = 300              # tolik sekund se po nepovedeném přihlášení hledá bez něj (pak nový pokus)


def _search_client(timeout=8):
    """Klient pro veřejné hledání na Přehraj.to – jeden pro celou aplikaci.

    Dřív si ho každá stránka titulu i každá dávka ověřování na pozadí vytvářela znovu a pokaždé si
    dotazem na profil ověřovala přihlášení: dotaz navíc před každým hledáním, a když zrovna neprošel,
    selhalo s ním celé hledání. Teď se přihlášení ověří jednou a klient se sdílí.

    Hledat jde i bez přihlášení: když přihlášení selže (špatné heslo, výpadek), hledá se nějakou dobu
    bez něj – jinak by každý dotaz zkoušel přihlášení znovu a hledání by vypadalo jako výpadek."""
    settings = api._settings()
    key = (settings.get("username") or "", settings.get("password") or "", api._STATE.get("dir"),
           id(provider.make_client))
    with _SEARCH_LOCK:
        client = _SEARCH["client"] if _SEARCH["key"] == key and _SEARCH["until"] > time.time() else None
        if client is None:
            client, keep = api._client(allow_anonymous=True), 24 * 3600
            if client.has_credentials:
                try:
                    client.login()
                except provider.ProviderError:
                    client, keep = provider.make_client("", ""), ANONYMOUS_FOR
            _SEARCH.update(key=key, client=client, until=time.time() + keep)
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


SEARCH_FAILED = ("Hledání na Přehraj.to se teď nezdařilo%s. Neznamená to, že tam titul není – zkuste to "
                 "za chvíli znovu.")


def _failure_reason(text):
    """Proč hledání selhalo, srozumitelně (technický detail je v Diagnostice)."""
    lowered = (text or "").lower()
    if "429" in lowered or "omezuje" in lowered:
        return ": Přehraj.to dočasně omezuje počet dotazů"
    if "timed out" in lowered or "timeout" in lowered or "nedostupné" in lowered or "nelze načíst" in lowered:
        return ": Přehraj.to neodpovídá nebo je pomalé připojení"
    if "http 5" in lowered:
        return ": Přehraj.to má výpadek"
    return ""


_PACE = {"interval": PACE_FASTEST, "next": 0.0, "gentle": GENTLE_PACE}
_PACE_LOCK = threading.Lock()
# Přehraj.to odmítá dotazy: do kdy se na pozadí nehledá, jak dlouhá byla poslední přestávka, kdy
# odmítlo naposledy a kolikrát celkem (podle toho zpomaluje plnění seznamu, viz ``app_index``).
_LIMITED = {"until": 0.0, "wait": 0, "last": 0.0, "hits": 0}


def _pace(gentle=False):
    """Rozestup dotazů ověřování na pozadí: několik vláken dohromady nepošle víc, než kolik Přehraj.to
    snese. Čeká, až na dotaz přijde řada. ``gentle``: delší rozestup pro plnění seznamu na pozadí."""
    with _PACE_LOCK:
        now = time.time()
        start = max(now, _PACE["next"])
        _PACE["next"] = start + (max(_PACE["interval"], _PACE["gentle"]) if gentle else _PACE["interval"])
    if start > now:
        time.sleep(start - now)


def _pace_result(failed):
    """Přizpůsobí tempo: po odmítnutém nebo nepovedeném dotazu se zpomalí, po povedeném pozvolna zrychlí."""
    with _PACE_LOCK:
        _PACE["interval"] = min(PACE_SLOWEST, max(_PACE["interval"], PACE_FASTEST) * 2) if failed \
            else max(PACE_FASTEST, _PACE["interval"] * 0.95)


def _is_limit(error):
    """Odmítlo Přehraj.to dotaz proto, že jich přišlo moc (HTTP 429)?"""
    text = str(error).lower()
    return "429" in text or "omezuje" in text


def _rate_limited():
    """Přehraj.to právě odmítlo dotaz: ověřování na pozadí si dá přestávku (při opakování delší)."""
    with _PACE_LOCK:
        now = time.time()
        if now < _LIMITED["until"]:
            return                         # přestávka už běží (odmítnutí dotazu, na který čekal člověk)
        again = _LIMITED["wait"] and now - _LIMITED["until"] < LIMIT_FORGET   # od konce minulé přestávky
        _LIMITED["wait"] = min(LIMIT_WAIT_MAX, _LIMITED["wait"] * 2) if again else LIMIT_WAIT
        _LIMITED.update(until=now + _LIMITED["wait"], last=now, hits=_LIMITED["hits"] + 1)
        _PACE["gentle"] = min(GENTLE_SLOWEST, _PACE["gentle"] * 1.5)


def limited():
    """Kolik sekund ještě trvá přestávka po odmítnutí dotazů; 0 = žádná."""
    return max(0.0, _LIMITED["until"] - time.time())


def _distrust():
    """Kolik sekund se ještě nevěří výsledku „nenašlo se“ u titulu, který na Přehraj.to byl: po dobu
    přestávky a ``LIMIT_DISTRUST`` po jejím konci."""
    return max(0.0, _LIMITED["until"] + LIMIT_DISTRUST - time.time()) if _LIMITED["until"] else 0.0


class _Yield(Exception):
    """Plnění seznamu na pozadí ustupuje: někdo čeká na výpis nebo na hledání."""


def _someone_waits():
    return bool(_VERIFY["queue"]) or bool(_VERIFY["active"]) or _VERIFY.get("hold", 0) > time.time()


def gentle_pace(value=None, recover=False):
    """Rozestup dotazů při plnění seznamu na pozadí. ``value`` ho nastaví (uložený z minula),
    ``recover`` ho po povedené dávce o kousek zkrátí – pod ``GENTLE_PACE`` nikdy."""
    with _PACE_LOCK:
        if value is not None:
            _PACE["gentle"] = min(GENTLE_SLOWEST, max(GENTLE_PACE, float(value)))
        elif recover:
            _PACE["gentle"] = max(GENTLE_PACE, _PACE["gentle"] * 0.98)
        return _PACE["gentle"]


def _search_page(client, query, page, cursor, retries, paced=False, gentle=False):
    """Stránka hledání. Dočasnou chybu (omezení dotazů, vypršení času) zkusí po chvíli ještě jednou,
    když na výsledek čeká člověk – jinak by stránka titulu hlásila neúspěch kvůli jedinému zaváhání.
    ``retries`` je počitadlo ``[kolik opakování zbývá]`` společné pro celé hledání titulu, aby se
    při výpadku nečekalo u každého dotazu zvlášť. ``paced``: dotaz ověřování na pozadí, drží rozestup
    (``gentle``: delší) a po dobu, kdy Přehraj.to dotazy odmítá, se vůbec neposílá."""
    if paced:
        if limited():
            raise provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429) – ověřování čeká")
        if gentle and _someone_waits():
            raise _Yield()                 # plnění seznamu drží zámek ověřování: ať ho hned pustí
        _pace(gentle)
        if gentle and _someone_waits():
            raise _Yield()
    try:
        found = provider.search_catalog(client, query, page, cursor)
        if paced:
            _pace_result(False)
        return found
    except provider.TemporaryError as error:
        if _is_limit(error):
            _rate_limited()
        if paced:
            _pace_result(True)
        if retries[0] <= 0:
            raise
        retries[0] -= 1
        time.sleep(SEARCH_RETRY_WAIT)
        return provider.search_catalog(client, query, page, cursor)


def _find_versions(names, year, position, state, runtime=0, pages=2, choice="", client=None,
                   queries_max=QUERIES_MAX,
                   strict=False, thorough=True, series=False, gentle=False):
    """Verze titulu (nebo dílu) na Přehraj.to, vyčištěné a seřazené podle předvoleb.

    Vrací ``[{"video", "result", "short", "cam", "bad", "chosen"}]``. Hledá se více variantami názvu;
    soubor výrazně kratší než film (``runtime`` v minutách) je ukázka nebo část a řadí se až na konec,
    stejně jako kino kopie a verze, které se dřív nepodařilo přehrát. Stejné soubory se slučují.

    ``thorough``: nespokojit se s prvním názvem, pod kterým se něco našlo – když mezi nálezy není
    verze podle předvoleb (jazyk, kvalita, žádná kino kopie), zkusí se i další názvy (původní název
    často najde lepší soubor než český). Ověřování dostupnosti to nepotřebuje: tam stačí, že titul je.

    ``series``: hledá se seriál jako celek – vyhoví kterýkoli jeho díl. Tak se ověřuje, jestli seriál
    na Přehraj.to je: dotaz na jeden díl („S01E01“) by minul soubory pojmenované „1x01“ nebo „1. díl“.
    """
    if client is None:
        client = _search_client()
    wanted = search_results.aliases(*names)
    kind = "episode" if position or series else "movie"
    queries = []
    for name in names:
        if not name:
            continue
        for query in (search_results.format_query(name, 0 if position or series else year, position),
                      search_results.format_query(name, 0, position)):
            if query not in queries:
                queries.append(query)
    if position:
        # díl bývá pojmenovaný i „1x05“: zkusí se, když „S01E05“ nic nenajde
        queries += ["%s %dx%02d" % (name, position[0], position[1]) for name in names if name]
    queries = queries[:queries_max]
    prefs, bad = state.get("prefs") or api._prefs(), state.get("bad", ())
    found, failed, reason = {}, 0, ""
    retries = [0 if strict else 1]
    if not strict:
        # Hledání, na které člověk čeká (stránka titulu, přehrání), má přednost před ověřováním na pozadí.
        _VERIFY["hold"] = time.time() + SEARCH_HOLD
    for query in queries:
        cursor = ""
        for page in range(1, pages + 1):
            try:
                results, cursor = _search_page(client, query, page, cursor, retries, paced=strict, gentle=gentle)
            except provider.ProviderError as error:
                failed += page == 1
                reason = str(error)
                break
            for result in results:
                parsed = nameparser.parse(result["title"])
                if search_results.filename_matches(parsed, wanted, 0 if position else year, position, kind):
                    found.setdefault(result["id"], (result, parsed))
            if not cursor:
                break
        if strict and failed and limited():
            break                          # Přehraj.to odmítá: další názvy by dopadly stejně
        # Ověřování dostupnosti stačí, že titul je – ale jen ve kvalitě, kterou si uživatel přeje vidět.
        usable = [item for item in found.values() if api._meets_minimum(
            dict(item[1], quality=api._known_quality(item[0].get("quality"), item[1].get("quality")),
                 size=item[0].get("size", ""), duration=item[0].get("duration") or 0), prefs)]
        if usable and (not thorough or _good_enough(usable, prefs, names)):
            break
    with api._library() as lib:
        chosen = lib.get_kv(api.CHOICE_PREFIX + choice) if choice else ""
        # co se o souborech ví doopravdy (stránka videa, přehrávání) – má přednost před štítkem z výpisu
        reals = {vid: api._real(lib, vid) for vid in found}
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
    # Nic se nenašlo a aspoň jeden dotaz se nepovedl: to je „nevím“, ne „není tam“ – jinak by stránka
    # titulu po výpadku napsala, že film na Přehraj.to není, a zapamatovala si to.
    if not found and queries and failed:
        if not strict:
            api._remember_error("hledání na Přehraj.to", reason)   # důvod je vidět v Diagnostice
        raise api.ApiError(SEARCH_FAILED % _failure_reason(reason))
    versions, seen = [], set()
    for result, parsed in found.values():
        video = dict(parsed, id=result["id"], raw_title=result["title"], thumb=result.get("thumb", ""),
                     size=result.get("size", ""), duration=result.get("duration") or 0)
        video["quality"] = api._known_quality(result.get("quality"), video.get("quality"))
        if reals.get(result["id"]):
            video["real"] = reals[result["id"]]
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
    return _arrange(versions, state)


def _arrange(versions, state):
    """Verze, které se nabídnou, v pořadí podle předvoleb; ``state["below_minimum"]`` = kolik jich
    vypadlo, protože jsou v nižší kvalitě, než si uživatel přeje vidět (stažená na disku zůstává)."""
    prefs, bad = state.get("prefs") or api._prefs(), state.get("bad", ())
    shown = [version for version in versions if version["saved"] or api._meets_minimum(version["video"], prefs)]
    state["below_minimum"] = len(versions) - len(shown)
    # stažená verze má přednost (hraje z disku, bez čekání a bez internetu), pak naposledy zvolená
    shown.sort(key=lambda version: (version["short"], not version["saved"], not version["chosen"]) +
               api._version_rank(version["video"], prefs, bad))
    return shown


VERIFY_VERSIONS = 3   # u tolika verzí nejvýš se stránka titulu podívá, co Přehraj.to opravdu nabízí


def _verify_best(versions, state):
    """Ověří verzi, kterou pustí tlačítko Přehrát: štítek „HD“ ve výpisu Přehraj.to neříká, jestli jde
    o 720p nebo 1080p, to je vidět až na stránce videa. Když se ukáže, že verze je v nižší kvalitě,
    než si uživatel přeje, vypadne a ověří se další. Stojí to jeden dotaz na verzi a výsledek se
    pamatuje; když dotaz selže, zůstane u verze údaj z výpisu."""
    client = None
    for _attempt in range(VERIFY_VERSIONS):
        best = next((version for version in versions if not version["short"]), None)
        if not best or best["saved"] or best["video"].get("real", {}).get("checked"):
            break
        try:
            client = client or _search_client()
            best["video"]["real"] = api.real_fetch(client, best["video"]["id"])
        except (api.ApiError, provider.ProviderError):
            break
        hidden = state.get("below_minimum") or 0
        versions = _arrange(versions, state)
        state["below_minimum"] += hidden
    return versions


def _version_quality(version, state, full=False):
    """Kvalita verze: ta, ve které se opravdu přehraje, když už je známá (stránka videa, přehrávání);
    jinak údaj z výpisu Přehraj.to nebo z názvu souboru. Na kartu jen rozlišení („4K“), na tlačítko
    Přehrát i to, že jde o původní soubor („originál 4K“)."""
    quality = api._shown_quality(version["video"], state.get("prefs") or api._prefs(), version.get("saved"))
    if full:
        return quality
    return quality.replace("originál", "").replace("z disku", "").strip() or quality


def _version_label(version, state, full=False):
    return " · ".join(part for part in (version["video"].get("lang") or "Jazyk neuveden",
                                        _version_quality(version, state, full)) if part)


def _file_line(version):
    """Soubor, který pustí tlačítko Přehrát: název, jak je na Přehraj.to, velikost a délka."""
    video = version["video"]
    return "%s: %s" % ("Soubor na disku" if version.get("saved") else "Soubor", " · ".join(
        part for part in (video["raw_title"], video.get("size"), _duration_text(video.get("duration"))) if part))


def _download_action(version, state, params):
    """Tlačítko na stažení souboru, který pustí tlačítko Přehrát (stažený soubor už tlačítko nepotřebuje)."""
    entry = state.get("downloads", {}).get(version["video"]["id"])
    if not entry or entry.get("state") == "failed":
        return {"title": "Zkusit stáhnout znovu" if entry else "Stáhnout na disk",
                "subtitle": version["video"].get("size") or "", "target": api._target("download", "download_add", params)}
    if entry.get("state") == "done":
        return None
    return {"title": "Stahuje se" if entry.get("state") == "downloading" else "Čeká na stažení", "subtitle": "",
            "target": api._target("list", "downloads", {}, "Stahování")}


def _version_card(version, state, choice=""):
    video, result = version["video"], version["result"]
    quality = _version_quality(version, state)
    title = _version_label(version, state)
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


def _extras(kind, tmdb_id):
    """Doplňky k titulu z TMDB (``Tmdb.extras``: další názvy, upoutávka, slogan); bez TMDB nebo při
    výpadku prázdné – nic z toho není pro stránku titulu nezbytné."""
    if not tmdb_id:
        return {}
    try:
        return api._tmdb().extras(kind, tmdb_id)
    except (TmdbError, api.ApiError, ValueError):
        return {}


def _local_names(kind, tmdb_id, known=()):
    """Další názvy titulu z TMDB – slovenský a jiné české (viz ``Tmdb.local_titles``) –, které mezi
    ``known`` ještě nejsou. Bez TMDB nebo při výpadku nic: hledá se pod názvy, které jsou."""
    if not tmdb_id:
        return []
    have = search_results.aliases(*known)
    names = _extras(kind, tmdb_id).get("names") or []
    extra = []
    for name in names:
        if search_results.normalize(name) not in have:
            have.add(search_results.normalize(name))
            extra.append(name)
    return extra[:LOCAL_NAMES]


def _names(params, kind=None):
    """Názvy, pod kterými se titul hledá: český a původní; s ``kind`` i slovenský a další z TMDB."""
    names = [name for name in (params.get("title") or "", params.get("orig") or "") if name]
    if kind and params.get("id"):
        names += _local_names(kind, params["id"], names)
    return names


def _detail(params, kind):
    """Údaje o titulu pro jeho stránku; bez TMDB zůstane to, co nese karta."""
    info = {"title": params.get("title") or "", "year": int(params.get("year") or 0), "rating": 0, "genres": [],
            "plot": "", "poster": params.get("poster") or "", "fanart": "", "runtime": 0, "collection_id": 0,
            "orig_title": params.get("orig") or "", "directors": [], "cast": [], "countries": []}
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


YOUTUBE_APPS = ("com.google.android.youtube.tv", "com.google.android.youtube")   # pro televize, pro telefony


def _about(info, detail, extras, kind):
    """Text pro „Více o filmu“: celý popis (na stránce se vejde jen začátek) a údaje, na které tam
    není místo – původní název, režie, obsazení, země."""
    lines = []
    if extras.get("tagline"):
        lines.append("„%s“" % extras["tagline"])
    if detail.get("meta"):
        lines.append(detail["meta"])
    if info.get("plot"):
        lines.append(info["plot"])
    facts = []
    if info.get("orig_title") and search_results.normalize(info["orig_title"]) != search_results.normalize(info["title"]):
        facts.append("Původní název: %s" % info["orig_title"])
    if info.get("directors"):
        facts.append("%s: %s" % ("Tvůrci" if kind == "tv" else "Režie", ", ".join(info["directors"][:4])))
    if info.get("cast"):
        facts.append("Hrají: %s" % ", ".join(info["cast"][:10]))
    if info.get("countries"):
        facts.append("Země: %s" % ", ".join(info["countries"][:4]))
    if info.get("genres"):
        facts.append("Žánr: %s" % ", ".join(info["genres"]))
    if facts:
        lines.append("\n".join(facts))
    return "\n\n".join(lines)


def _extra_actions(params, kind, info, detail):
    """Tlačítka stránky titulu, která nepřehrávají: upoutávka (je-li) a celý popis."""
    extras = _extras(kind, params.get("id"))
    actions = []
    if extras.get("trailer"):
        key = extras["trailer"]
        # Upoutávku přehraje aplikace YouTube v televizi (jinak cokoli, co odkaz umí otevřít). Běžný
        # odkaz youtube.com: zkrácený tvar „vnd.youtube:…“ aplikace YouTube pro televize nesnese.
        actions.append({"title": "Upoutávka", "subtitle": "", "target": {
            "type": "open", "call": "", "title": "", "params": {
                # ``youtube``: aplikace upoutávku přehraje sama ve vloženém přehrávači YouTube a po konci
                # se vrátí na stránku titulu; adresa a aplikace YouTube jsou záloha, kdyby to nešlo
                "youtube": key,
                "url": "https://www.youtube.com/watch?v=" + key, "packages": list(YOUTUBE_APPS),
                "missing": "Upoutávku se nepodařilo otevřít: v televizi chybí aplikace YouTube."}}})
    about = _about(info, detail, extras, kind)
    if about and (info.get("plot") or info.get("directors") or info.get("cast")):   # jen když je co číst
        actions.append({"title": "Více o seriálu" if kind == "tv" else "Více o filmu", "subtitle": "", "target": {
            "type": "info", "call": "", "title": info["title"], "params": {"text": about}}})
    return actions


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
LOW_QUALITY = "Na Přehraj.to je tento film jen v nižší kvalitě než %s (nalezeno: %d). "
LOW_QUALITY_OWN = "Nižší kvalitu jen pro něj povolíte tlačítkem Další → Jazyk a kvalita u tohoto filmu → Nejnižší kvalita."
LOW_QUALITY_APP = "Nižší kvalitu povolíte tlačítkem Jazyk a kvalita → Nejnižší kvalita."
OWN_PREFS = "Jazyk a kvalita u tohoto %s"   # položka pod „Další“ na stránce titulu


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

    ``{"detail": {title, meta, plot, poster, backdrop, file?}, "actions": [{title, subtitle, target}],
    "rows": [{title, items}]}``. U filmu jsou v řadách verze z Přehraj.to a první tlačítko přehraje tu
    nejlepší podle předvoleb; u seriálu jsou v řadě jeho série. Je-li titul i v iVysílání, přibude
    tlačítko, které ho tam přehraje (film) nebo otevře jeho díly.
    """
    kind = _kind(params)
    with ThreadPoolExecutor(max_workers=1) as pool:
        television = pool.submit(_title_television, kind, params)   # souběžně s hledáním na Přehraj.to
        page, playable = _title_page(params, kind)
        show = television.result()
    if not playable:
        # Není co přehrát z Přehraj.to: místa je dost a ruční hledání i předvolby jsou právě to, co je
        # teď potřeba – nabídka „Další“ se rozbalí do samostatných tlačítek.
        page["actions"] = [button for action in page["actions"] for button in (
            [dict(item, subtitle="") for item in action["menu"]] if action.get("menu") else [action])]
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
        # za Přehrát (a Stáhnout) z Přehraj.to; první, když tam titul není (nebo se to neví a nic k přehrání není)
        second = playable and not (entry and not entry["n"])
        if second:
            # vedle tlačítek z Přehraj.to stačí krátký název, ať se všechna vejdou do dvou řádků
            action.update(title="V iVysílání", subtitle="")
            downloads = [index for index, item in enumerate(page["actions"][:2])
                         if item["target"].get("call") in ("download_add", "downloads")]
            page["actions"].insert(downloads[-1] + 1 if downloads else 1, action)
        else:
            page["actions"].insert(0, action)
        if page["detail"].get("plot", "").startswith(NOT_FOUND):
            page["detail"]["plot"] = page["detail"]["plot"].replace(
                NOT_FOUND, "Na Přehraj.to jsem tento film nenašel, je ale v iVysílání České televize.", 1)
    if show is not None and entry and entry["iv"] != show:
        _remember_availability(kind, params["id"], entry["n"], show, int(params.get("year") or 0), searched=False)
    return page


def _title_page(params, kind):
    """Stránka titulu bez iVysílání a zda má tlačítko, které rovnou přehrává."""
    title = params.get("title") or ""
    with api._library() as lib:
        # jazyk a kvalita můžou být u titulu nastavené jinak než v aplikaci (Další → Jazyk a kvalita)
        state = api._title_state(lib, api._state(lib), "%s:%s" % (kind, params.get("id") or 0))
        last = lib.get_kv("last_season:%s" % params.get("id")) if kind == "tv" else None
    favorites_state = state["favorites"]
    entry = favorites_state.get(params.get("fid") or "") or tmdb_favorite(
        kind, params.get("id") or 0, title, int(params.get("year") or 0), params.get("poster") or "",
        params.get("orig") or "")
    toggle = favorite_item(favorites_state, entry)
    info, detail = _detail(params, kind)
    # Tlačítek je víc, než se vejde do dvou řádků: krátký název u oblíbených a méně používané volby
    # (ruční hledání, předvolby) pod „Další“.
    favourite = "Z oblíbených" if toggle["title"].startswith("Odebrat") else "Do oblíbených"
    more = [{"title": "Hledat ručně", "target": api._target("search", "search", {"q": title})}]
    if params.get("id"):
        # jen pro tento titul; nastavení celé aplikace je v Nástrojích
        more.append({"title": OWN_PREFS % ("seriálu" if kind == "tv" else "filmu")
                     + (" (vlastní)" if state.get("own") else ""),
                     "target": api._target("choices", "title_prefs_choices", {"kind": kind, "id": params["id"]})})
    else:
        more.append({"title": "Jazyk a kvalita", "target": api._target("choices", "prefs_choices", {})})
    actions = _extra_actions(params, kind, info, detail) + [
        {"title": favourite, "subtitle": "", "target": toggle["target"]},
        {"title": "Další", "subtitle": "vlastní nastavení" if state.get("own") else "",
         "target": {"type": "menu", "call": "", "title": "", "params": {}}, "menu": more}]
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
        versions = _verify_best(
            _find_versions(_names(params, kind), info["year"], None, state, info["runtime"], choice=choice), state)
    except api.ApiError as error:
        # hledání selhalo: stránka zůstane použitelná – údaje, oblíbené, ruční hledání a nový pokus
        versions, failed = [], str(error)
    good = [version for version in versions if not version["short"]]
    rows = []
    if failed:
        actions.insert(0, {"title": "Zkusit znovu", "subtitle": "", "target": api._target("refresh")})
        detail["plot"] = (failed + "\n\n" + detail["plot"]).strip()
    else:
        if "minimum" not in state["own"]:
            # Seznam dostupných titulů se řídí nastavením aplikace: co je vidět jen díky vlastní
            # nejnižší kvalitě tohoto filmu (nebo kvůli ní vidět není), se do něj nezapisuje.
            _remember_availability("movie", params.get("id"), len(good), year=info["year"], extra=_summary(good))
        _note_favorite(entry["id"], versions, state["prefs"])
    if failed:
        pass
    elif good:
        best = _version_card(good[0], state, choice)
        # tlačítko říká, v jakém jazyce a v jaké kvalitě se film opravdu pustí
        actions.insert(0, {"title": "Přehrát", "subtitle": _version_label(good[0], state, full=True),
                           "target": best["target"]})
        # … a který soubor to je (název a velikost, jak je na Přehraj.to), spolu s tlačítkem na jeho stažení
        detail["file"] = _file_line(good[0])
        download = _download_action(good[0], state, best["target"]["params"])
        if download:
            actions.insert(1, download)
        rows.append({"title": "Verze na Přehraj.to (%d)" % len(good),
                     "items": [_version_card(version, state, choice) for version in good]})
    elif state.get("below_minimum"):
        # film na Přehraj.to je, ale jen v nižší kvalitě, než si uživatel přeje vidět
        wanted = api.MINIMUM_LABEL.get(state["prefs"]["minimum"], "")
        advice = LOW_QUALITY_OWN if params.get("id") else LOW_QUALITY_APP
        detail["plot"] = (LOW_QUALITY % (wanted, state["below_minimum"]) + advice + "\n\n" + detail["plot"]).strip()
    else:
        detail["plot"] = (NOT_FOUND + "\n\n" + detail["plot"]).strip()
    rest = [version for version in versions if version["short"]]
    if rest:
        rows.append({"title": "Ukázky a neúplné soubory", "items": [_version_card(v, state, choice) for v in rest]})
    page = {"detail": detail, "actions": actions, "rows": rows + _related_rows(kind, params, info, state)}
    if failed:
        page["retry"] = True   # obrazovka to za chvíli zkusí sama znovu (výpadek bývá krátký)
    return page, bool(good) and not failed


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
        state = api._title_state(lib, api._state(lib), "tv:%s" % tmdb_id)
        if tmdb_id:
            _remember_title(lib, "tv", params)
    choice = "tv:%s:%s:%s" % (tmdb_id, season, episode) if tmdb_id else ""
    versions = [version for version in _find_versions(_names(params, "tv"), 0, (season, episode), state, choice=choice)
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
        state = api._title_state(lib, api._state(lib), "movie:%s" % tmdb_id)
    choice = "movie:%s" % tmdb_id if tmdb_id else ""
    versions = [version for version in _find_versions(_names(params, "movie"), int(params.get("year") or 0), None, state,
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


OTHER_VERSIONS = 2   # tolikrát nejvýš přehrávač po chybě zkusí jinou verzi téhož titulu


def play_other(params):
    """Verzi ``track`` se nepodařilo přehrát: pustí jinou verzi téhož titulu (``choice`` říká kterého –
    ``movie:<číslo TMDB>`` nebo ``tv:<číslo>:<řada>:<díl>``), nejlepší podle předvoleb. ``tried`` jsou
    verze, které už selhaly. Vrací adresu pro přehrávač a ``switched`` (popis verze pro oznámení)."""
    track, choice = str(params.get("track") or ""), str(params.get("choice") or "")
    tried = {str(vid) for vid in params.get("tried") or []} | {track}
    parts = choice.split(":")
    if len(parts) < 2 or parts[0] not in ("movie", "tv") or not parts[1].isdigit() or len(tried) > OTHER_VERSIONS:
        raise api.ApiError("Jinou verzi se nepodařilo najít.")
    api.play_failed({"track": track})        # příště se tahle verze nenabídne jako první
    kind, tmdb_id = parts[0], int(parts[1])
    position = None
    if kind == "tv":
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
            raise api.ApiError("Jinou verzi se nepodařilo najít.")
        position = (int(parts[2]), int(parts[3]))
    try:
        info = _tmdb().details(tmdb_id, want_series=kind == "tv")
    except TmdbError:
        raise api.ApiError("Jinou verzi se nepodařilo najít (TMDB neodpovídá).")
    title = {"id": tmdb_id, "title": info.get("title") or "", "orig": info.get("orig_title") or "",
             "year": info.get("year") or 0}
    with api._library() as lib:
        state = api._title_state(lib, api._state(lib), choice)
    versions = [version for version in _find_versions(
        _names(title, kind), 0 if kind == "tv" else int(title["year"] or 0), position, state,
        runtime=0 if kind == "tv" else int(info.get("runtime") or 0), choice=choice)
        if not version["short"] and not version["bad"] and version["video"]["id"] not in tried]
    if not versions:
        raise api.ApiError("Jiná verze, která by šla přehrát, na Přehraj.to není.")
    best = versions[0]
    request = {"vid": best["video"]["id"], "web_result": best["result"], "choice": choice, "fresh": True}
    stream = api.play(request)
    stream["choice"] = choice
    stream["switched"] = _version_label(best, state)
    # Čím si přehrávač řekne o tutéž verzi znovu (nová adresa po chybě): jinak by se vrátil k té
    # původní, která nejde přehrát.
    stream["again"] = {"call": "play", "params": request}
    return stream


def discover_season(params):
    """Díly jedné řady podle TMDB; každý díl vede na své verze na Přehraj.to."""
    season = int(params.get("season") or 0)
    tmdb_id = int(params.get("id") or 0)
    episodes = _tmdb().season_episodes(tmdb_id, season)
    with api._library() as lib:
        state = api._title_state(lib, api._state(lib), "tv:%s" % tmdb_id)
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
            state = api._title_state(lib, api._state(lib), "tv:%s" % tmdb_id)
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
            api.download_add({"vid": vid, "web_result": good[0]["result"], "choice": choice})
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
        state = api._title_state(lib, api._state(lib), "tv:%s" % tmdb_id)
        if tmdb_id:
            lib.set_kv("last_season:%s" % tmdb_id, str(season))  # pro „Pokračovat“ na stránce seriálu
            _remember_title(lib, "tv", params)
    choice = "tv:%s:%s:%s" % (tmdb_id, season, episode) if tmdb_id else ""
    versions = _find_versions(_names(params, "tv"), 0, (season, episode), state, choice=choice)
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
    own = api._title_state(lib, state, choice)   # další film může mít jazyk a kvalitu nastavené po svém
    found = [version for version in _find_versions(names, year, None, own, choice=choice) if not version["short"]]
    if found:
        return _next_item("Další film série", label, _version_card(found[0], own, choice)["target"])
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
                # další díl se vybírá podle předvoleb seriálu, pokud nějaké vlastní má
                return {"next": _tmdb_next_episode(lib, choice, api._title_state(lib, state, choice))}
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


DEPARTMENTS = {"Acting": "herectví", "Directing": "režie", "Writing": "scénář", "Production": "produkce"}
PEOPLE_MAX = 8                   # tolik osob nejvýš v řadě Herci a režiséři
PERSON_PAGE = 20                 # tolik titulů filmografie na jedno načtení (a jedno ověřování dostupnosti)
PERSON_ROW = 15                  # tolik nejznámějších titulů osoby rovnou ve výsledcích hledání
# Vystoupení, která do filmografie nepatří: osoba hraje sama sebe, archivní záběry, neuvedená v titulcích
_SELF_RE = re.compile(r"\b(?:self|himself|herself|themselves|s[aá]m sebe|sama sebe)\b|archiv|uncredited|neuveden",
                      re.I)
_NOT_FICTION = {10767, 10763, 10764}   # žánry TMDB: talk show, zpravodajství, reality show
_DOCUMENTARY = 99


def _name_matches(name, query):
    """Odpovídá jméno hledanému výrazu? Každé hledané slovo musí být začátkem některého slova jména
    (bez ohledu na diakritiku): „sverak“ najde Svěráka, „tom han“ Toma Hankse, ale „avatar“ nikoho."""
    words = search_results.normalize(name).split()
    wanted = search_results.normalize(query).split()
    return bool(wanted) and all(any(word.startswith(part) for word in words) for part in wanted)


def _known_for(person, limit=2):
    """Nejznámější tituly osoby, jak je uvádí TMDB – ať je poznat, o koho jde."""
    titles = [(item.get("title") or item.get("name") or "").strip() for item in person.get("known_for") or []
              if isinstance(item, dict)]
    return [title for title in titles if title][:limit]


def find_people(query):
    """Osoby, které odpovídají hledanému výrazu, od nejznámější: ``[osoba z TMDB]``.

    TMDB vrací na jméno i desítky lidí, kteří se jen podobně jmenují nebo o nich nic neví. Zůstanou
    jen ti, jejichž jméno výrazu opravdu odpovídá (``_name_matches``) a kteří mají fotku nebo známé
    tituly; první je přesná shoda jména, pak podle oblíbenosti. Když jménu neodpovídá nikdo (TMDB
    hledá i v přezdívkách a přepisech), nechají se ti nejznámější z toho, co vrátilo."""
    client = api._tmdb()
    if not client.enabled:
        return []
    try:
        people, _pages = client.search_people(query)
    except TmdbError:
        return []
    people = [person for person in people if person.get("profile_path") or person.get("known_for")]
    matching = [person for person in people if _name_matches(person["name"], query)]
    if not matching:
        matching = [person for person in people if (person.get("popularity") or 0) >= 5]
    target = search_results.normalize(query)
    matching.sort(key=lambda person: (search_results.normalize(person["name"]) != target,
                                      -(person.get("popularity") or 0), person["name"]))
    return matching[:PEOPLE_MAX]


def _person_card(person):
    photo = person.get("profile_path") or ""
    department = person.get("known_for_department") or ""
    subtitle = " · ".join(part for part in (DEPARTMENTS.get(department, ""), ", ".join(_known_for(person))) if part)
    return api._card(
        person["name"], api._target("list", "discover_person",
                                    {"id": person["id"], "name": person["name"], "dept": department}, person["name"]),
        subtitle, IMG + "h632" + photo if isinstance(photo, str) and photo.startswith("/") else "",
        wide=not photo, icon="movies")


def people_rows(query, state):
    """Řady pro hledání osob: ``(řady, přesná shoda)``. Řada Herci a režiséři, a když výraz přesně
    odpovídá jménu jedné osoby, i řada jejích nejznámějších titulů – hledá-li někdo „Brad Pitt“,
    chce jeho filmy, ne tituly, které mají to jméno v názvu."""
    people = find_people(query)
    if not people:
        return [], False
    rows = [{"title": "Herci a režiséři", "items": [_person_card(person) for person in people]}]
    first = people[0]
    exact = search_results.normalize(first["name"]) == search_results.normalize(query)
    if exact:
        try:
            credits = _person_credits(first["id"], first.get("known_for_department") or "")
        except (TmdbError, api.ApiError):
            credits = []
        credits = credits[:PERSON_ROW]
        if credits:
            with api._library() as lib:
                for kind in ("movie", "tv"):
                    _mark_known(state, _known(lib, kind, [item["id"] for _role, item in credits
                                                          if _credit_kind(item) == kind]))
            # co je k mání, jde dopředu; jinak pořadí podle známosti
            credits.sort(key=lambda entry: not state.get("available", {}).get(
                "%s:%s" % (_credit_kind(entry[1]), entry[1]["id"])))
            cards = [_credit_card(role, item, state) for role, item in credits]
            cards.append(api._card("Zobrazit vše", api._target(
                "list", "discover_person", {"id": first["id"], "name": first["name"],
                                            "dept": first.get("known_for_department") or ""}, first["name"]),
                "Celá filmografie, jen co je k mání", icon="all"))
            rows.append({"title": "%s – nejznámější tituly" % first["name"], "items": cards})
    return rows, exact


def _credit_kind(item):
    return "tv" if item.get("media_type") == "tv" else "movie"


def _credit_card(role, item, state):
    card = _item_card(item, _credit_kind(item), state)
    card["subtitle"] = " · ".join(part for part in (card["subtitle"], role) if part)
    return card


def _released(item):
    """Titul už byl uveden (má datum a to není v budoucnu)."""
    date = item.get("release_date") or item.get("first_air_date") or ""
    return bool(date) and date[:10] <= time.strftime("%Y-%m-%d")


def _real_role(item):
    """Je to skutečná role ve filmu nebo seriálu? Ne vystoupení v talk show, zpravodajství nebo reality
    show, ne „sám sebe“ v dokumentu, archivní záběry, role neuvedená v titulcích ani hostování
    v jediném dílu seriálu."""
    genres = set(item.get("genre_ids") or [])
    character = item.get("character") or ""
    if genres & _NOT_FICTION or _SELF_RE.search(character):
        return False
    if _DOCUMENTARY in genres and not character:
        return False
    if item.get("media_type") == "tv" and item.get("episode_count") == 1:
        return False
    return not item.get("video")


def _person_credits(person_id, department=""):
    """Filmografie osoby: ``[(role, titul)]`` – jen uvedené tituly a skutečné role (``_real_role``),
    režie u režiséra první (jinak role první), v každé skupině od nejznámějšího titulu."""
    client = _tmdb()
    directed = [("režie", item) for item in client.directing_credits(person_id)
                if _released(item) and not set(item.get("genre_ids") or []) & _NOT_FICTION]
    acted = [(item.get("character") or "", item) for item in client.acting_credits(person_id)
             if _released(item) and _real_role(item)]

    def known(entry):
        item = entry[1]
        return (-(item.get("vote_count") or 0), -(item.get("popularity") or 0),
                item.get("release_date") or item.get("first_air_date") or "")

    groups = [sorted(directed, key=known), sorted(acted, key=known)]
    if department != "Directing" and len(acted) >= len(directed):
        groups.reverse()               # herec: role první, režie až potom
    credits, seen = [], set()
    for group in groups:
        for role, item in group:
            key = (item.get("media_type"), item.get("id"))
            if key not in seen:
                seen.add(key)
                credits.append((role, item))
    return credits


def discover_person(params):
    """Filmy a seriály jedné osoby – jen ty, které jsou k mání na Přehraj.to nebo v iVysílání.

    Filmografie je očištěná (``_person_credits``) a řazená od nejznámějších titulů. Dostupnost se
    ověřuje stejně jako u výpisů Objevuj: neověřený titul se neukáže, ověří se na pozadí a výpis se
    plní postupně; dál než na část, která se ověřuje, se nejde. S ``all`` se ukáže celá filmografie
    (se štítky tam, kde už se dostupnost ví)."""
    person_id = int(params.get("id") or 0)
    if not person_id:
        raise api.ApiError("Chybí osoba.")
    offset = api._offset(params)
    everything = bool(params.get("all"))
    credits = _person_credits(person_id, params.get("dept") or "")
    with api._library() as lib:
        state = api._state(lib)
    window = credits[offset:offset + PERSON_PAGE]
    checking = state.setdefault("checking", set())
    state.setdefault("unverified", 0)
    known = {}
    for kind in ("movie", "tv"):
        items = [item for _role, item in window if _credit_kind(item) == kind]
        if not items:
            continue
        if everything or BACKGROUND:
            with api._library() as lib:
                found = _known(lib, kind, [item["id"] for item in items])
            if not everything:
                todo = [item for item in items if not _fresh(found, "%s:%s" % (kind, item["id"]))]
                pending = {"%s:%s" % key for key in _verify_later(kind, todo, state)}
                checking.update(key for key in pending if key not in found)
                state["rechecking"] = state.get("rechecking", 0) + len([key for key in pending if key in found])
                state["unverified"] += len([item for item in todo if "%s:%s" % (kind, item["id"]) not in found
                                            and "%s:%s" % (kind, item["id"]) not in pending])
        else:
            found = _check_available(kind, items, state)
            state["unverified"] += len([item for item in items if "%s:%s" % (kind, item["id"]) not in found])
        _mark_known(state, found)
        known.update(found)
    cards = [_credit_card(role, item, state) for role, item in window
             if everything or _there(known, _credit_kind(item), item)]
    if offset == 0 and credits:
        base = {key: value for key, value in params.items() if key not in ("offset", "all")}
        cards.insert(0, api._card(
            "Jen co je k mání" if everything else "Celá filmografie",
            api._target("list", "discover_person", base if everything else dict(base, all=1), params.get("name") or ""),
            "Teď: všechny tituly" if everything else "Teď: jen na Přehraj.to a v iVysílání", icon="settings:search"))
    more = offset + PERSON_PAGE < len(credits) and not checking
    following = {"call": "discover_person", "params": dict(params, offset=offset + PERSON_PAGE)} if more else None
    return dict(_checking_note(state), title=params.get("name") or "", items=cards, next=following,
                empty="TMDB u této osoby neuvádí žádné filmy ani seriály.")


ACTIONS = {
    "favorites": favorites, "favorite_toggle": favorite_toggle,
    "discover": discover, "discover_years": discover_years, "discover_genres": discover_genres,
    "discover_czechoslovak": discover_czechoslovak, "discover_title": discover_title,
    "discover_season": discover_season, "discover_episode": discover_episode, "discover_check": discover_check,
    "play_next": play_next, "play_other": play_other, "title_csfd": title_csfd, "favorites_check": favorites_check,
    "discover_download_season": discover_download_season, "discover_filters": discover_filters,
    "discover_person": discover_person, "discover_episode_play": discover_episode_play,
    "discover_movie_play": discover_movie_play, "discover_row": discover_row, "next_up_hide": next_up_hide,
}
