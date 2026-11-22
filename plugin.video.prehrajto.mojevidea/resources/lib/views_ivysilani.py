# -*- coding: utf-8 -*-
"""iVysílání České televize: živé vysílání, kategorie, pořady, díly, hledání,
rozkoukané díly a oblíbené pořady."""
import json
import time
from hashlib import sha256

import xbmc
import xbmcgui
import xbmcplugin

from kodiutils import FANART, KODI_MAJOR, keyboard, log, notify, setting_bool
from search_results import normalize
import ui
from ui import (_, add_dir, _add_item, _art, end, _fail_directory, _list_item, open_library, PLAYBACK_START_TIMEOUT,
                PLAYING_PROPERTY, _set_info, _set_resume_point, url_for)

ivysilani = ui.LazyModule("ivysilani")

# jak dlouho (s) platí uložené odpovědi katalogu; adresy streamů se neukládají
TTL_MENU = 24 * 3600
TTL_CATEGORY = 3600
TTL_SHOW = 3600
TTL_EPISODES = 15 * 60
TTL_SEARCH = 10 * 60
TTL_LIVE = 60
CACHE_KEEP = 200
SEARCH_EPISODES = 20
LIVE_ICON = "DefaultAddonPVRClient.png"
HISTORY_PREFIX = "ivys:"  # tak služba pozná, že přehrávané video patří iVysílání


def _client():
    return ivysilani.Client(log=log)


def _cached(key, ttl, load):
    key = "ivys:" + key
    with open_library() as lib:
        value = lib.cache_get(key)
    if value is None:
        value = load()
        with open_library() as lib:
            lib.cache_put(key, value, ttl)
            lib.cache_trim("ivys:", CACHE_KEEP)
    return value


def _load(key, ttl, load, quiet=False):
    """Data z mezipaměti nebo z iVysílání; při chybě oznámení a None."""
    try:
        return _cached(key, ttl, load)
    except ivysilani.IvysilaniError as error:
        if not quiet:
            notify(str(error), time_ms=8000)
        return None


def _states():
    with open_library() as lib:
        resume, watched = lib.ivys_states()
        favorites = {item["show_id"] for item in lib.ivys_favorites()}
    return {"resume": resume, "watched": watched, "favorites": favorites}


def _clock(timestamp):
    return time.strftime("%H:%M", time.localtime(timestamp)) if timestamp else ""


# ---------------------------------------------------------------------------
# Položky
# ---------------------------------------------------------------------------

def _add_show(show, note="", state=None, label=None):
    year = show.get("year") or 0
    label = label or show["title"] + (" (%d)" % year if year else "")
    li = _list_item(label)
    plot = "\n".join(part for part in (note, show.get("description")) if part)
    movie = show.get("type") == "movie"
    _set_info(li, {"title": show["title"], "plot": plot, "year": year,
                   "mediatype": "movie" if movie else "tvshow"})
    _art(li, show.get("poster"), show.get("image"))
    favorite = bool(state) and show["id"] in state["favorites"]
    li.addContextMenuItems([(_(30589 if favorite else 30588), "RunPlugin(%s)" % url_for(
        "ivys_favorite", id=show["id"], remove=1 if favorite else None))])
    if movie:  # film je jediné video – přehrát rovnou
        li.setProperty("IsPlayable", "true")
        _add_item(url_for("ivys_play", show=show["id"]), li, False)
    else:
        _add_item(url_for("ivys_show", id=show["id"]), li, True)


def _add_episode(episode, show_title="", poster="", state=None, progress=False):
    title = episode["title"] or show_title
    show_title = episode.get("show_title") or show_title
    label = "%s · %s" % (show_title, title) if episode.get("show_title") and title != show_title else title
    idec = episode["idec"]
    point = state["resume"].get(idec) if state else None
    if progress and point and point[1]:
        label = "%s · %d %%" % (label, int(100 * point[0] / point[1]))
    li = _list_item(label)
    extra = " · ".join(part for part in (episode.get("season"), episode.get("date")) if part)
    plot = "\n".join(part for part in (extra, episode.get("description")) if part)
    _set_info(li, {"title": label, "plot": plot, "duration": episode.get("duration") or 0,
                   "tvshowtitle": show_title, "mediatype": "episode" if show_title else "video"})
    _art(li, poster, episode.get("image"), episode_still=episode.get("image") or "")
    li.setProperty("IsPlayable", "true")
    if state and idec in state["watched"]:
        if KODI_MAJOR >= 20:
            li.getVideoInfoTag().setPlaycount(1)
        else:
            li.setInfo("video", {"playcount": 1})
    elif point:
        _set_resume_point(li, point)
    if progress:
        li.addContextMenuItems([(_(30595), "RunPlugin(%s)" % url_for("ivys_history_remove", idec=idec))])
    _add_item(url_for("ivys_play", idec=idec), li, False)


# ---------------------------------------------------------------------------
# Obrazovky
# ---------------------------------------------------------------------------

def ivys_menu(params):
    with open_library() as lib:
        in_progress = len(lib.ivys_progress())
        favorites = len(lib.ivys_favorites())
    add_dir(_(30580), "ivys_live", LIVE_ICON)
    add_dir(_(30581), "ivys_search", "DefaultAddonsSearch.png")
    if in_progress:
        add_dir("%s (%d)" % (_(30360), in_progress), "ivys_progress", "DefaultInProgressShows.png")
    if favorites:
        add_dir("%s (%d)" % (_(30587), favorites), "ivys_favorites", "DefaultFavourites.png")
    for category in _load("menu:v2", TTL_MENU, lambda: _client().menu()) or []:
        add_dir(category["title"], "ivys_category", "DefaultGenre.png", cid=category["id"])
    end(cache=False)


def ivys_live(params):
    """Kanály s tím, co právě běží. Program je jen doplněk – když ho nelze načíst, kanály zůstanou."""
    channels = _load("live", TTL_LIVE, lambda: _client().live_now(), quiet=True) or []
    listed = {channel["channel"] for channel in channels}
    channels = channels + [{"channel": channel, "name": name} for channel, name in ivysilani.LIVE_CHANNELS
                           if channel not in listed]
    for channel in channels:
        name = channel["name"] or channel["channel"]
        title = channel.get("title") or ""
        li = _list_item("%s · %s" % (name, title) if title else name)
        lines = []
        if channel.get("starts"):
            lines.append("%s–%s" % (_clock(channel["starts"]), _clock(channel.get("ends"))))
        if channel.get("next_title"):
            lines.append(_(30593) % (_clock(channel.get("next_starts")), channel["next_title"]))
        if channel.get("description"):
            lines.append(channel["description"])
        _set_info(li, {"title": title or name, "plot": "\n".join(lines), "mediatype": "video"})
        image = channel.get("image") or ""
        li.setArt({"icon": LIVE_ICON, "thumb": image or LIVE_ICON, "fanart": FANART})
        li.setProperty("IsPlayable", "true")
        li.addContextMenuItems([(_(30592), "PlayMedia(%s)" % url_for(
            "ivys_play", channel=channel["channel"], timeshift=1))])
        _add_item(url_for("ivys_play", channel=channel["channel"]), li, False)
    end("videos", cache=False)


def ivys_category(params):
    """Kategorie: úplný výpis, podkategorie a pod nimi výběr, který nabízí web."""
    cid = params.get("cid", "")
    category = _load("category:" + cid, TTL_CATEGORY, lambda: _client().category(cid))
    if category is None:
        _fail_directory()
        return
    add_dir(_(30591), "ivys_listing", "DefaultFolder.png", cid=cid)
    menu = _load("menu:v2", TTL_MENU, lambda: _client().menu(), quiet=True) or []
    for child in next((item.get("children") or [] for item in menu if item["id"] == cid), []):
        add_dir(child["title"], "ivys_listing", "DefaultGenre.png", cid=child["id"])
    state = _states()
    seen = set()
    for block in category["blocks"]:
        for show in block["shows"]:
            if show["id"] not in seen:
                seen.add(show["id"])
                _add_show(show, block["title"], state)
    end("tvshows", cache=False)


def _offset(params):
    try:
        return max(0, int(params.get("offset") or 0))
    except ValueError:
        return 0


def ivys_listing(params):
    """Všechny pořady kategorie nebo podkategorie, po stránkách."""
    cid = params.get("cid", "")
    offset = _offset(params)
    listing = _load("listing:%s:%d" % (cid, offset), TTL_CATEGORY, lambda: _client().category_listing(cid, offset))
    if listing is None:
        _fail_directory()
        return
    state = _states()
    for show in listing["shows"]:
        _add_show(show, state=state)
    if not listing["shows"]:
        notify(_(30121))
    if listing["shows"] and offset + len(listing["shows"]) < listing["total"]:
        add_dir(_(30341), "ivys_listing", cid=cid, offset=offset + len(listing["shows"]))
    end("tvshows", cache=False)


def ivys_show(params):
    show_id = params.get("id", "")
    show = _load("show:" + show_id, TTL_SHOW, lambda: _client().show(show_id))
    if show is None:
        _fail_directory()
        return
    with open_library() as lib:
        lib.ivys_favorite_seen(show_id, show["episode_count"])  # nové díly už uživatel viděl v seznamu
    season = params.get("season", "")
    if show["seasons"] and not season:
        for item in show["seasons"]:
            add_dir(item["title"] or show["title"], "ivys_show", "DefaultTVShows.png", id=show_id, season=item["id"])
        add_dir(_(30582) % show["episode_count"], "ivys_show", "DefaultTVShows.png", id=show_id, season="all")
        end("seasons", cache=False)
        return
    offset = _offset(params)
    season_id = season if season not in ("", "all") else None
    key = "episodes:%s:%s:%d" % (show["programme_id"], season_id or "", offset)
    page = _load(key, TTL_EPISODES, lambda: dict(zip(("total", "items"), _client().episodes(
        show["programme_id"], season_id, offset, show["default_sort"]))))
    if page is None:
        _fail_directory()
        return
    state = _states()
    for episode in page["items"]:
        _add_episode(episode, show["title"], show["poster"], state)
    if not page["items"] and show["idec"] and show["playable"] and not offset:
        # pořad bez samostatných dílů (např. film nalezený hledáním)
        _add_episode({"idec": show["idec"], "title": show["title"], "description": show["description"],
                      "image": show["image"]}, poster=show["poster"], state=state)
    if offset + len(page["items"]) < page["total"] and page["items"]:
        add_dir(_(30341), "ivys_show", id=show_id, season=season or "all", offset=offset + len(page["items"]))
    end("episodes", cache=False)


def ivys_search(params):
    query = params.get("q", "").strip()
    from_keyboard = not query
    if from_keyboard:
        query = (keyboard(_(30581)) or "").strip()
        if not query:
            _fail_directory()
            return
    offset = _offset(params)

    def search():
        client = _client()
        total, shows = client.search_shows(query, offset)
        episodes = client.search_episodes(query, 0, SEARCH_EPISODES)[1] if not offset else []
        return {"total": total, "shows": shows, "episodes": episodes}

    found = _load("search:%s:%d" % (query.casefold(), offset), TTL_SEARCH, search)
    if found is None:
        _fail_directory()
        return
    state = _states()
    for show in found["shows"]:
        _add_show(show, ", ".join(show.get("genres") or []), state)
    for episode in found["episodes"]:
        _add_episode(episode, state=state)
    if not found["shows"] and not found["episodes"]:
        notify(_(30121))
    if offset + len(found["shows"]) < found["total"] and found["shows"]:
        add_dir(_(30341), "ivys_search", q=query, offset=offset + len(found["shows"]))
    end("videos", cache=from_keyboard)


# ---------------------------------------------------------------------------
# Rozkoukané a Moje pořady
# ---------------------------------------------------------------------------

def add_progress_items(limit=50):
    """Rozkoukané díly z iVysílání (i pro společné Rozkoukané doplňku). Vrací jejich počet."""
    with open_library() as lib:
        rows = lib.ivys_progress(limit)
    if rows:
        state = _states()
        for row in rows:
            _add_episode({"idec": row["idec"], "title": row["title"], "show_title": row["show_title"],
                          "image": row["image"], "duration": int(row["total"] or 0)}, state=state, progress=True)
    return len(rows)


def ivys_progress(params):
    if not add_progress_items():
        notify(_(30121))
    end("videos", cache=False)


def ivys_history_remove(params):
    with open_library() as lib:
        lib.ivys_remove_history(params.get("idec") or None)
    xbmc.executebuiltin("Container.Refresh")


def ivys_favorites(params):
    """Oblíbené pořady; u každého počet dílů, které přibyly od poslední návštěvy."""
    with open_library() as lib:
        favorites = lib.ivys_favorites()
    state = _states()
    for favorite in favorites:
        show = _load("show:" + favorite["show_id"], TTL_SHOW, lambda: _client().show(favorite["show_id"]), quiet=True)
        card = show or {"id": favorite["show_id"], "title": favorite["title"], "image": favorite["image"],
                        "poster": favorite["poster"], "type": "", "year": 0, "description": ""}
        fresh = max(0, show["episode_count"] - favorite["seen_count"]) if show else 0
        label = "%s · %s" % (card["title"], _(30590) % fresh) if fresh else None
        _add_show(dict(card, type=""), state=state, label=label)  # i film se tu otevírá jako pořad
    if not favorites:
        notify(_(30121))
    end("tvshows", cache=False)


def ivys_favorite(params):
    show_id = params.get("id", "")
    if params.get("remove") == "1":
        with open_library() as lib:
            lib.ivys_favorite_remove(show_id)
        notify(_(30597))
    else:
        show = _load("show:" + show_id, TTL_SHOW, lambda: _client().show(show_id))
        if show is None:
            return
        with open_library() as lib:
            lib.ivys_favorite_add(show_id, show["title"], show["image"], show["poster"], show["episode_count"])
        notify(_(30596))
    xbmc.executebuiltin("Container.Refresh")


def find_show(title, year=0):
    """Pořad v iVysílání se stejným názvem (pro detail titulu z TMDB), nebo None."""
    wanted = normalize(title)
    if not wanted:
        return None
    found = _load("find:" + title.casefold(), TTL_SEARCH, lambda: _client().search_shows(title, 0)[1], quiet=True)
    for show in found or []:
        if normalize(show["title"]) == wanted:
            return show
    return None


# ---------------------------------------------------------------------------
# Přehrávání
# ---------------------------------------------------------------------------

def _adaptive_available():
    return setting_bool("ivysilani_adaptive") and bool(
        xbmc.getCondVisibility("System.AddonIsEnabled(inputstream.adaptive)"))


def ivys_play(params):
    window = xbmcgui.Window(10000)
    window.clearProperty(PLAYING_PROPERTY)  # nesmí zůstat záznam o videu z katalogu
    idec = ""
    try:
        client = _client()
        if params.get("channel"):
            stream = client.live(params["channel"], timeshift=params.get("timeshift") == "1")
        else:
            idec = params.get("idec") or client.show(params.get("show", ""))["idec"]
            stream = client.vod(idec)
    except ivysilani.IvysilaniError as error:
        notify(str(error), time_ms=10000)
        xbmcplugin.setResolvedUrl(ui.HANDLE, False, xbmcgui.ListItem())
        return
    li = xbmcgui.ListItem(path=stream["url"])
    li.setMimeType("application/vnd.apple.mpegurl")
    li.setContentLookup(False)
    if _adaptive_available():
        li.setProperty("inputstream", "inputstream.adaptive")
        if KODI_MAJOR < 21:  # novější verze typ manifestu poznají samy
            li.setProperty("inputstream.adaptive.manifest_type", "hls")
    if setting_bool("subtitles") and stream["subtitles"]:
        li.setSubtitles(stream["subtitles"])
    if idec:
        # Pořady (ne živé vysílání) sleduje služba doplňku: pozice, dokoukání, Rozkoukané.
        with open_library() as lib:
            lib.ivys_touch(idec, stream.get("show_id"), stream.get("episode_title") or stream.get("title"),
                           stream.get("show_title"), stream.get("image"))
            point = lib.ivys_states()[0].get(idec)
        if point and params.get("restart") != "1":
            _set_resume_point(li, point)
        window.setProperty(PLAYING_PROPERTY, json.dumps({
            "vid": HISTORY_PREFIX + idec, "cache_id": None, "cached": False, "resume": True,
            "path_hash": sha256(stream["url"].split("|", 1)[0].encode("utf-8")).hexdigest(),
            "expires": time.monotonic() + PLAYBACK_START_TIMEOUT}))
    xbmcplugin.setResolvedUrl(ui.HANDLE, True, li)
