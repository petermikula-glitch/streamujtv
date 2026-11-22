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

import csfd
import nameparser
import provider
import search_results
from tmdb import IMG, TmdbError

import app_api as api

FAVORITES = "favorites.json"
CZECHOSLOVAK = "CZ|SK|XC"        # Česko, Slovensko a někdejší Československo (v TMDB kód XC)
AVAILABLE_PREFIX = "avail:"      # kolik verzí titulu se na Přehraj.to naposledy našlo
AVAILABLE_TTL = 7 * 24 * 3600
CHECK_BATCH = 20                 # kolik titulů ověří jedno „Ověřit dostupnost“
FAVORITES_EVERY = 12 * 3600      # hlídání oblíbených nejvýš jednou za tuto dobu
FAVORITES_BATCH = 6              # a nejvýš tolika titulů najednou
SEASON_DOWNLOAD_MAX = 40         # nejvýš tolik dílů při stažení celé řady
# seriálové žánry TMDB bez vlastní ilustrace: nejbližší filmová, nebo dlaždice z iVysílání
GENRE_ART = {10759: "genre_28", 10765: "genre_878", 10768: "genre_10752", 10762: "kids", 10763: "news"}
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


def _note_favorite(fid, count):
    """U oblíbeného titulu si pamatuje, kolik verzí bylo naposledy na Přehraj.to; otevřením mizí štítek „Nově“."""
    with _LOCK:
        items = load_favorites()
        entry = items.get(fid)
        if entry is None:
            return
        entry.update(avail=count, checked=int(time.time()))
        entry.pop("fresh", None)
        _save_favorites(items)


def favorites_check(params):
    """Hlídání oblíbených: objevil se na Přehraj.to film, který tam minule nebyl? ``{"found": [názvy]}``.

    Spouští aplikace po startu; ptá se nejvýš jednou za FAVORITES_EVERY a jen na pár titulů (jedno
    hledání na film). Nalezený titul dostane na kartě štítek „Nově“, dokud ho uživatel neotevře.
    """
    settings = api._settings()
    now = int(time.time())
    if not params.get("force") and now - int(settings.get("favorites_checked") or 0) < FAVORITES_EVERY:
        return {"found": []}
    settings["favorites_checked"] = now
    api._save_settings(settings)
    waiting = sorted((entry for entry in load_favorites().values()
                      if not entry.get("key") and entry.get("kind") == "movie" and entry.get("tmdb_id")
                      and not entry.get("avail")), key=lambda entry: entry.get("checked") or 0)[:FAVORITES_BATCH]
    if not waiting:
        return {"found": []}
    with api._library() as lib:
        state = api._state(lib)
    found = []
    for entry in waiting:
        try:
            versions = _find_versions([entry.get("title") or "", entry.get("orig") or ""],
                                      int(entry.get("year") or 0), None, state, pages=1)
        except api.ApiError:
            break  # hledání teď nejde; zkusí se příště
        good = len([version for version in versions if not version["short"]])
        with _LOCK:
            items = load_favorites()
            current = items.get(entry["id"])
            if current is None:
                continue
            # „nově“ jen u titulu, o kterém se už vědělo, že na Přehraj.to není
            appeared = bool(good) and current.get("avail") == 0
            current.update(avail=good, checked=now)
            if appeared:
                current["fresh"] = True
                found.append(current.get("title") or "")
            _save_favorites(items)
        _remember_availability("movie", entry["tmdb_id"], good)
    return {"found": found}


def favorite_cards(lib, state, limit=None):
    """Karty oblíbených: položky katalogu se chovají jako v katalogu, tituly z TMDB vedou na své verze."""
    # naposledy přidané první (při stejném čase rozhoduje pořadí přidání)
    entries = sorted(reversed(list(state["favorites"].values())), key=lambda entry: -(entry.get("added") or 0))
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
    with api._library() as lib:
        cards = favorite_cards(lib, api._state(lib))
    return {"title": "Oblíbené", "items": cards, "next": None, "empty_art": "favorites",
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
    badges = ["Přehraj.to"] if known else ["Nenalezeno"] if known == 0 else None
    if favorite and favorite.get("fresh"):
        badges = ["Nově na Přehraj.to"]
    return api._card(title, target, subtitle, poster, wide=not poster, menu=menu,
                     icon="series" if kind == "tv" else "movies", badges=badges)


def _availability(lib, kind, ids):
    """Známá dostupnost titulů na Přehraj.to: {"movie:11": počet verzí}; starší než týden se nepočítá."""
    known, now = {}, time.time()
    for tmdb_id in ids:
        key = "%s:%s" % (kind, tmdb_id)
        try:
            entry = json.loads(lib.get_kv(AVAILABLE_PREFIX + key) or "null")
        except ValueError:
            entry = None
        if isinstance(entry, dict) and now - (entry.get("ts") or 0) < AVAILABLE_TTL:
            known[key] = int(entry.get("n") or 0)
    return known


def _remember_availability(kind, tmdb_id, count):
    if tmdb_id:
        with api._library() as lib:
            lib.set_kv(AVAILABLE_PREFIX + "%s:%s" % (kind, tmdb_id), json.dumps({"n": count, "ts": int(time.time())}))


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
    items, pages = _list_items(params)
    items = [item for item in items if isinstance(item, dict) and item.get("id")]
    with api._library() as lib:
        state = api._state(lib)
        state["available"] = _availability(lib, kind, [item["id"] for item in items])
    cards = [_item_card(item, kind, state) for item in items]
    if kind == "movie" and cards and len(state["available"]) < len(cards):
        # dostupnost se neověřuje sama u celého seznamu (desítky hledání), jen na požádání
        cards.insert(0, api._card("Ověřit dostupnost", api._target("command", "discover_check", dict(params, page=page)),
                                  "Které z těchto filmů jsou na Přehraj.to", icon="search"))
    following = {"call": "discover", "params": dict(params, page=page + 1)} if page < pages and cards else None
    return {"items": cards, "next": following, "empty": "V tomto výběru TMDB nic nenabízí."}


def _list_items(params):
    """Tituly jedné stránky seznamu (stejný výběr jako ``discover``)."""
    kind = _kind(params)
    page = max(1, api._offset({"offset": params.get("page")}))
    client = _tmdb()
    category = params.get("category") or ""
    if category in ("popular", "top_rated"):
        return client.browse(kind, category, page)
    if category == "recent":
        # novinky: filmy za poslední tři měsíce, u seriálů právě vysílané
        return client.browse(kind, "recent" if kind == "movie" else "on_the_air", page)
    filters = {"with_genres": params.get("genre") or "", "with_origin_country": params.get("origin") or "",
               "with_cast": params.get("cast") or ""}
    start, end = search_results.period(str(params.get("year") or ""))
    if start and start == end:
        filters["primary_release_year" if kind == "movie" else "first_air_date_year"] = start
    elif start:
        field = "primary_release_date" if kind == "movie" else "first_air_date"
        filters[field + ".gte"], filters[field + ".lte"] = "%d-01-01" % start, "%d-12-31" % end
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
                                          int(date[:4]) if date[:4].isdigit() else 0, None, state, pages=1)
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


def _find_versions(names, year, position, state, runtime=0, pages=2, choice=""):
    """Verze titulu (nebo dílu) na Přehraj.to, vyčištěné a seřazené podle předvoleb.

    Vrací ``[{"video", "result", "short", "cam", "bad", "chosen"}]``. Hledá se více variantami názvu;
    soubor výrazně kratší než film (``runtime`` v minutách) je ukázka nebo část a řadí se až na konec,
    stejně jako kino kopie a verze, které se dřív nepodařilo přehrát. Stejné soubory se slučují.
    """
    client = api._client(allow_anonymous=True)
    client.request_timeout = 8
    client.request_retries = 0
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
    queries = queries[:4]
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
        if found:
            break
    if not found and queries and failed == len(queries):
        raise api.ApiError("Hledání na Přehraj.to se nezdařilo. Zkuste to znovu.")
    prefs, bad = state.get("prefs") or api._prefs(), state.get("bad", ())
    with api._library() as lib:
        chosen = lib.get_kv(api.CHOICE_PREFIX + choice) if choice else ""
    versions, seen = [], set()
    for result, parsed in found.values():
        video = dict(parsed, id=result["id"], raw_title=result["title"], thumb=result.get("thumb", ""),
                     size=result.get("size", ""), duration=result.get("duration") or 0)
        video["quality"] = result.get("quality") or video.get("quality") or ""
        same = (video["duration"], video["size"], video["quality"], video.get("lang"))
        if video["duration"] and video["size"] and same in seen:
            continue  # tentýž soubor nahraný vícekrát
        seen.add(same)
        versions.append({"video": video, "result": result,
                         # ukázka nebo část: výrazně kratší než film, nebo se tak soubor sám jmenuje (trailer…)
                         "short": bool(runtime and video["duration"] and video["duration"] < runtime * 60 * 0.6)
                         or search_results.is_preview(video["raw_title"], names),
                         "cam": bool(api.CAM_RE.search(video["raw_title"])), "bad": video["id"] in bad,
                         "chosen": bool(chosen) and video["id"] == chosen})
    versions.sort(key=lambda version: (version["short"], not version["chosen"]) +
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
                     menu=api._video_menu(state, params, point), icon="movies", badges=[language, quality])


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
                      person["character"], person["photo"], wide=not person["photo"], icon="favorites")
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


def discover_title(params):
    """Stránka titulu: údaje, tlačítka a řady karet.

    ``{"detail": {title, meta, plot, poster, backdrop}, "actions": [{title, subtitle, target}],
    "rows": [{title, items}]}``. U filmu jsou v řadách verze z Přehraj.to a první tlačítko přehraje tu
    nejlepší podle předvoleb; u seriálu jsou v řadě jeho série.
    """
    kind = _kind(params)
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
               {"title": "Hledat ručně", "subtitle": "", "target": api._target("search", "search", {"q": title})}]
    if kind == "tv" and params.get("id"):
        seasons = _tmdb().seasons(params["id"])
        cards = [api._card(season["title"], api._target(
            "list", "discover_season", dict(params, season=season["number"]),
            "%s – %s" % (title, season["title"])), "%d dílů" % season["episodes"] if season["episodes"] else "",
            season["poster"] or params.get("poster") or "", icon="series") for season in seasons]
        if last and any(str(season["number"]) == str(last) for season in seasons):
            season = next(season for season in seasons if str(season["number"]) == str(last))
            actions.insert(0, {"title": "Pokračovat", "subtitle": season["title"], "target": api._target(
                "list", "discover_season", dict(params, season=season["number"]), "%s – %s" % (title, season["title"]))})
        return {"detail": detail, "actions": actions,
                "rows": ([{"title": "Řady", "items": cards}] if cards else []) + _related_rows(kind, params, info, state)}
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
        _note_favorite(entry["id"], len(good))
    if failed:
        pass
    elif good:
        best = _version_card(good[0], state, choice)
        actions.insert(0, {"title": "Přehrát", "subtitle": best["title"], "target": best["target"]})
        rows.append({"title": "Verze na Přehraj.to (%d)" % len(good),
                     "items": [_version_card(version, state, choice) for version in good]})
    else:
        detail["plot"] = ("Na Přehraj.to jsem tento film nenašel. Zkuste hledat ručně pod jiným názvem.\n\n"
                          + detail["plot"]).strip()
    rest = [version for version in versions if version["short"]]
    if rest:
        rows.append({"title": "Ukázky a neúplné soubory", "items": [_version_card(v, state, choice) for v in rest]})
    return {"detail": detail, "actions": actions, "rows": rows + _related_rows(kind, params, info, state)}


def discover_season(params):
    """Díly jedné řady podle TMDB; každý díl vede na své verze na Přehraj.to."""
    season = int(params.get("season") or 0)
    tmdb_id = int(params.get("id") or 0)
    episodes = _tmdb().season_episodes(tmdb_id, season)
    with api._library() as lib:
        state = api._state(lib)
        # verze dílu, kterou si uživatel pustil, nese stav zhlédnutí celého dílu
        played = {number: lib.get_kv(api.CHOICE_PREFIX + "tv:%s:%s:%s" % (tmdb_id, season, number))
                  for number in episodes}
    cards = []
    for number in sorted(episodes, key=int):
        episode = episodes[number]
        label = "%d×%02d" % (season, int(number))
        name = "%s · %s" % (label, episode["title"]) if episode.get("title") else label
        vid = played.get(number)
        cards.append(api._card(name, api._target(
            "list", "discover_episode", dict(params, episode=int(number)),
            "%s %s" % (params.get("title") or "", label)), (episode.get("plot") or "")[:120],
            episode.get("still") or "", api._percent(state["resume"].get(vid)) if vid else 0, wide=True,
            watched=bool(vid) and vid in state["watched"], icon="series"))
    if cards:
        cards.append(api._card("Stáhnout celou řadu", api._target("command", "discover_download_season", params),
                               "Vyhledá a zařadí všechny díly", icon="download"))
    return {"items": cards, "next": None, "empty": "TMDB u této řady neuvádí žádné díly."}


def discover_download_season(params):
    """Stáhne celou řadu seriálu z Objevuj: ke každému dílu vyhledá nejlepší verzi a zařadí ji do fronty."""
    def work():
        season, tmdb_id = int(params.get("season") or 0), int(params.get("id") or 0)
        episodes = _tmdb().season_episodes(tmdb_id, season)
        with api._library() as lib:
            state = api._state(lib)
        numbers = sorted(episodes, key=int)[:SEASON_DOWNLOAD_MAX]
        added, missing = 0, 0
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
        note = " %d dílů jsem na Přehraj.to nenašel." % missing if missing else ""
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
    later = sorted(position for position in best if position > (video["season"], video["episode"]))
    if not later:
        return None
    following = best[later[0]][1]
    return following, _episode_label(lib, following)


def _episode_label(lib, video, position=None):
    season, episode = position or (video["season"], video["episode"])
    meta = lib.get_meta(video["meta_key"]) or {}
    return "%s %d×%02d" % (api._display_title(meta) or video["title"], season, episode)


def _searched_next_episode(lib, video, state):
    """Další díl seriálu, který v katalogu není: vyhledá se na Přehraj.to podle názvu seriálu."""
    following = (video["season"], video["episode"] + 1)
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
    try:
        with api._library() as lib:
            state = api._state(lib)
            video = lib.find_video(track) if track else None
            if choice.startswith("tv:"):
                return {"next": _tmdb_next_episode(lib, choice, state)}
            if video and video["kind"] == "episode" and video["episode"]:
                found = _catalogue_next_episode(lib, video, state)
                if found:
                    return {"next": _next_item("Další díl", found[1],
                                               api._target("play", "play", {"vid": found[0]["id"]}), auto=True)}
                return {"next": _searched_next_episode(lib, video, state)}
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
    for video in lib.history_videos(limit=40, include_search=True):
        if video["kind"] != "episode" or not video["episode"] or video["meta_key"] in seen:
            continue
        seen.add(video["meta_key"])      # rozhoduje naposledy přehraný díl seriálu
        if video["id"] not in state["watched"]:
            continue
        found = _catalogue_next_episode(lib, video, state)
        if found and found[0]["id"] not in state["watched"] and not state["resume"].get(found[0]["id"]):
            meta = lib.get_meta(video["meta_key"])
            card = api._video_card(found[0], meta, state)
            card["subtitle"] = " · ".join(part for part in ("Další díl", card["subtitle"]) if part)
            cards.append(card)
    for tmdb_id in _recent_series(lib):
        info = _title_info(lib, "tv", tmdb_id)
        if info:
            card = _title_card("tv", info.get("id") or tmdb_id, info["title"], int(info.get("year") or 0),
                               info.get("poster") or "", info.get("orig") or "", state)
            card["subtitle"] = "Pokračovat ve sledování"
            cards.append(card)
    return cards[:limit]


def search_rows(query, state):
    """Řada titulů z TMDB pro hledaný výraz (plakáty místo názvů souborů); bez klíče TMDB nic."""
    client = api._tmdb()
    if not client.enabled:
        return []
    try:
        items, _pages = client.search_titles(query)
    except TmdbError:
        return []
    cards = [_item_card(item, "tv" if item.get("media_type") == "tv" else "movie", state)
             for item in items[:20] if isinstance(item, dict) and item.get("id")]
    return [{"title": "Filmy a seriály", "items": cards}] if cards else []


ACTIONS = {
    "favorites": favorites, "favorite_toggle": favorite_toggle,
    "discover": discover, "discover_years": discover_years, "discover_genres": discover_genres,
    "discover_czechoslovak": discover_czechoslovak, "discover_title": discover_title,
    "discover_season": discover_season, "discover_episode": discover_episode, "discover_check": discover_check,
    "play_next": play_next, "title_csfd": title_csfd, "favorites_check": favorites_check,
    "discover_download_season": discover_download_season,
}
