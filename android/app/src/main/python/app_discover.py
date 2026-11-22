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

import nameparser
import provider
import search_results
from tmdb import IMG

import app_api as api

FAVORITES = "favorites.json"
CZECHOSLOVAK = "CZ|SK|XC"        # Česko, Slovensko a někdejší Československo (v TMDB kód XC)
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
    return {"title": "Oblíbené", "items": cards, "next": None,
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
    target = api._target("list", "discover_title", params, title)
    subtitle = " · ".join(part for part in (str(year) if year else "", "%d %%" % rating if rating else "",
                                           KIND_NAMES.get(kind, "")) if part)
    menu = [{"title": "Otevřít", "target": target}, favorite_item(state.get("favorites", {}), entry)]
    return api._card(title, target, subtitle, poster, wide=not poster, menu=menu,
                     icon="series" if kind == "tv" else "movies")


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
    client = _tmdb()
    category = params.get("category") or ""
    if category in ("popular", "top_rated"):
        items, pages = client.browse(kind, category, page)
    elif category == "recent":
        # novinky: filmy za poslední tři měsíce, u seriálů právě vysílané
        items, pages = client.browse(kind, "recent" if kind == "movie" else "on_the_air", page)
    else:
        filters = {"with_genres": params.get("genre") or "", "with_origin_country": params.get("origin") or ""}
        start, end = search_results.period(str(params.get("year") or ""))
        if start and start == end:
            filters["primary_release_year" if kind == "movie" else "first_air_date_year"] = start
        elif start:
            field = "primary_release_date" if kind == "movie" else "first_air_date"
            filters[field + ".gte"], filters[field + ".lte"] = "%d-01-01" % start, "%d-12-31" % end
        items, pages = client.discover(kind, page, **filters)
    with api._library() as lib:
        state = api._state(lib)
    cards = [_item_card(item, kind, state) for item in items if isinstance(item, dict) and item.get("id")]
    following = {"call": "discover", "params": dict(params, page=page + 1)} if page < pages and cards else None
    return {"items": cards, "next": following, "empty": "V tomto výběru TMDB nic nenabízí."}


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
    icon = "folder:series" if kind == "tv" else "folder:movies"
    cards = [api._card(genre["name"], api._target("list", "discover", dict(extra, kind=kind, genre=str(genre["id"])),
                                                  "%s – %s" % (_plural(kind), genre["name"])), icon=icon)
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
                  icon=icon),
        api._card("Populární", api._target("list", "discover", {"kind": kind, "category": "popular"},
                                           "%s – populární" % name), icon="all"),
        api._card("Nejlépe hodnocené", api._target("list", "discover", {"kind": kind, "category": "top_rated"},
                                                   "%s – nejlépe hodnocené" % name), icon="rating"),
        api._card("Podle roku", api._target("list", "discover_years", {"kind": kind}, "%s podle roku" % name),
                  icon="year"),
        api._card("Podle žánru", api._target("list", "discover_genres", {"kind": kind}, "%s podle žánru" % name),
                  icon="folder:" + icon),
        api._card("Československé", api._target("list", "discover_czechoslovak", {"kind": kind},
                                                "Československé %s" % name.lower()), "Česko a Slovensko",
                  icon="folder:past"),
    ]


# ---------------------------------------------------------------------------
# Titul: verze na Přehraj.to, u seriálu řady a díly
# ---------------------------------------------------------------------------

def _versions(names, year, position, state):
    """Verze titulu (nebo dílu) nalezené hledáním na Přehraj.to, nejlepší první."""
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
    found, failed = {}, 0
    for query in queries[:4]:
        try:
            results, _cursor = provider.search_catalog(client, query, 1, "")
        except provider.ProviderError:
            failed += 1
            continue
        for result in results:
            parsed = nameparser.parse(result["title"])
            if search_results.filename_matches(parsed, wanted, 0 if position else year, position, kind):
                found.setdefault(result["id"], (result, parsed))
        if found:
            break
    if not found and failed == len(queries[:4]) and queries:
        raise api.ApiError("Hledání na Přehraj.to se nezdařilo. Zkuste to znovu.")
    videos = []
    for result, parsed in found.values():
        video = dict(parsed, id=result["id"], raw_title=result["title"], thumb=result.get("thumb", ""),
                     size=result.get("size", ""))
        video["quality"] = result.get("quality") or video.get("quality") or ""
        videos.append((video, result))
    videos.sort(key=lambda pair: api._version_rank(pair[0]))
    with api._library() as lib:
        return [api._video_card(lib.find_video(result["id"]) or video, None, state, web_result=result)
                for video, result in videos]


def _names(params):
    return [name for name in (params.get("title") or "", params.get("orig") or "") if name]


def discover_title(params):
    """Film: verze na Přehraj.to. Seriál: jeho řady. Vždy s dlaždicí pro oblíbené."""
    kind = _kind(params)
    title = params.get("title") or ""
    year = int(params.get("year") or 0)
    with api._library() as lib:
        state = api._state(lib)
    favorites_state = state["favorites"]
    entry = favorites_state.get(params.get("fid") or "") or tmdb_favorite(
        kind, params.get("id") or 0, title, year, params.get("poster") or "", params.get("orig") or "")
    toggle = favorite_item(favorites_state, entry)
    extras = [api._card(toggle["title"], toggle["target"], "Oblíbené najdete v sekci Přehraj.to", icon="favorites"),
              api._card("Hledat ručně", api._target("search", "search", {"q": title}), "Jiný název nebo jiná verze",
                        icon="search")]
    if kind == "tv" and params.get("id"):
        seasons = _tmdb().seasons(params["id"])
        cards = [api._card(season["title"], api._target(
            "list", "discover_season", dict(params, season=season["number"]),
            "%s – %s" % (title, season["title"])), "%d dílů" % season["episodes"] if season["episodes"] else "",
            season["poster"] or params.get("poster") or "", icon="series") for season in seasons]
        return {"title": title, "items": cards + extras, "next": None}
    cards = _versions(_names(params), year, None, state)
    if not cards:
        cards = [api._card("Na Přehraj.to jsem nic nenašel", api._target("search", "search", {"q": title}),
                           "Zkuste hledat ručně", icon="search")]
    return {"title": title, "items": cards + extras, "next": None}


def discover_season(params):
    """Díly jedné řady podle TMDB; každý díl vede na své verze na Přehraj.to."""
    season = int(params.get("season") or 0)
    episodes = _tmdb().season_episodes(int(params.get("id") or 0), season)
    cards = []
    for number in sorted(episodes, key=int):
        episode = episodes[number]
        label = "%d×%02d" % (season, int(number))
        name = "%s · %s" % (label, episode["title"]) if episode.get("title") else label
        cards.append(api._card(name, api._target(
            "list", "discover_episode", dict(params, episode=int(number)),
            "%s %s" % (params.get("title") or "", label)), (episode.get("plot") or "")[:120],
            episode.get("still") or "", wide=True, icon="series"))
    return {"items": cards, "next": None, "empty": "TMDB u této řady neuvádí žádné díly."}


def discover_episode(params):
    position = (int(params.get("season") or 0), int(params.get("episode") or 0))
    with api._library() as lib:
        state = api._state(lib)
    cards = _versions(_names(params), 0, position, state)
    query = search_results.format_query(params.get("title") or "", 0, position)
    if not cards:
        cards = [api._card("Na Přehraj.to jsem tento díl nenašel", api._target("search", "search", {"q": query}),
                           "Zkuste hledat ručně", icon="search")]
    return {"items": cards, "next": None}


ACTIONS = {
    "favorites": favorites, "favorite_toggle": favorite_toggle,
    "discover": discover, "discover_years": discover_years, "discover_genres": discover_genres,
    "discover_czechoslovak": discover_czechoslovak, "discover_title": discover_title,
    "discover_season": discover_season, "discover_episode": discover_episode,
}
