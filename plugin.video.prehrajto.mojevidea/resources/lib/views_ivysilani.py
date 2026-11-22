# -*- coding: utf-8 -*-
"""iVysílání České televize: živé vysílání, kategorie, pořady, díly a hledání."""
import xbmc
import xbmcgui
import xbmcplugin

from kodiutils import FANART, KODI_MAJOR, keyboard, log, notify, setting_bool
import ui
from ui import (_, add_dir, _add_item, _art, end, _fail_directory, _list_item, open_library, PLAYING_PROPERTY,
                _set_info, url_for)

ivysilani = ui.LazyModule("ivysilani")

# jak dlouho (s) platí uložené odpovědi katalogu; adresy streamů se neukládají
TTL_MENU = 24 * 3600
TTL_CATEGORY = 3600
TTL_SHOW = 3600
TTL_EPISODES = 15 * 60
TTL_SEARCH = 10 * 60
CACHE_KEEP = 200
SEARCH_EPISODES = 20
LIVE_ICON = "DefaultAddonPVRClient.png"


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


def _load(key, ttl, load):
    """Data z mezipaměti nebo z iVysílání; při chybě oznámení a None."""
    try:
        return _cached(key, ttl, load)
    except ivysilani.IvysilaniError as error:
        notify(str(error), time_ms=8000)
        return None


def _add_show(show, note=""):
    year = show.get("year") or 0
    label = show["title"] + (" (%d)" % year if year else "")
    li = _list_item(label)
    plot = "\n".join(part for part in (note, show.get("description")) if part)
    movie = show.get("type") == "movie"
    _set_info(li, {"title": show["title"], "plot": plot, "year": year,
                   "mediatype": "movie" if movie else "tvshow"})
    _art(li, show.get("poster"), show.get("image"))
    if movie:  # film je jediné video – přehrát rovnou
        li.setProperty("IsPlayable", "true")
        _add_item(url_for("ivys_play", show=show["id"]), li, False)
    else:
        _add_item(url_for("ivys_show", id=show["id"]), li, True)


def _add_episode(episode, show_title="", poster=""):
    title = episode["title"] or show_title
    show_title = episode.get("show_title") or show_title
    label = "%s · %s" % (show_title, title) if episode.get("show_title") and title != show_title else title
    li = _list_item(label)
    extra = " · ".join(part for part in (episode.get("season"), episode.get("date")) if part)
    plot = "\n".join(part for part in (extra, episode.get("description")) if part)
    _set_info(li, {"title": label, "plot": plot, "duration": episode.get("duration") or 0,
                   "tvshowtitle": show_title, "mediatype": "episode" if show_title else "video"})
    _art(li, poster, episode.get("image"), episode_still=episode.get("image") or "")
    li.setProperty("IsPlayable", "true")
    _add_item(url_for("ivys_play", idec=episode["idec"]), li, False)


def ivys_menu(params):
    add_dir(_(30580), "ivys_live", LIVE_ICON)
    add_dir(_(30581), "ivys_search", "DefaultAddonsSearch.png")
    for category in _load("menu", TTL_MENU, lambda: _client().menu()) or []:
        add_dir(category["title"], "ivys_category", "DefaultGenre.png", cid=category["id"])
    end(cache=False)


def ivys_live(params):
    for channel, name in ivysilani.LIVE_CHANNELS:
        li = _list_item(name)
        _set_info(li, {"title": name, "mediatype": "video"})
        li.setArt({"icon": LIVE_ICON, "thumb": LIVE_ICON, "fanart": FANART})
        li.setProperty("IsPlayable", "true")
        _add_item(url_for("ivys_play", channel=channel), li, False)
    end("videos", cache=False)


def ivys_category(params):
    cid = params.get("cid", "")
    category = _load("category:" + cid, TTL_CATEGORY, lambda: _client().category(cid))
    if category is None:
        _fail_directory()
        return
    seen = set()
    for block in category["blocks"]:
        for show in block["shows"]:
            if show["id"] not in seen:
                seen.add(show["id"])
                _add_show(show, block["title"])
    if not seen:
        notify(_(30121))
    end("tvshows", cache=False)


def ivys_show(params):
    show_id = params.get("id", "")
    show = _load("show:" + show_id, TTL_SHOW, lambda: _client().show(show_id))
    if show is None:
        _fail_directory()
        return
    season = params.get("season", "")
    if show["seasons"] and not season:
        for item in show["seasons"]:
            add_dir(item["title"] or show["title"], "ivys_show", "DefaultTVShows.png", id=show_id, season=item["id"])
        add_dir(_(30582) % show["episode_count"], "ivys_show", "DefaultTVShows.png", id=show_id, season="all")
        end("seasons", cache=False)
        return
    try:
        offset = max(0, int(params.get("offset") or 0))
    except ValueError:
        offset = 0
    season_id = season if season not in ("", "all") else None
    key = "episodes:%s:%s:%d" % (show["programme_id"], season_id or "", offset)
    page = _load(key, TTL_EPISODES, lambda: dict(zip(("total", "items"), _client().episodes(
        show["programme_id"], season_id, offset, show["default_sort"]))))
    if page is None:
        _fail_directory()
        return
    for episode in page["items"]:
        _add_episode(episode, show["title"], show["poster"])
    if not page["items"] and show["idec"] and show["playable"] and not offset:
        # pořad bez samostatných dílů (např. film nalezený hledáním)
        _add_episode({"idec": show["idec"], "title": show["title"], "description": show["description"],
                      "image": show["image"]}, poster=show["poster"])
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
    try:
        offset = max(0, int(params.get("offset") or 0))
    except ValueError:
        offset = 0

    def search():
        client = _client()
        total, shows = client.search_shows(query, offset)
        episodes = client.search_episodes(query, 0, SEARCH_EPISODES)[1] if not offset else []
        return {"total": total, "shows": shows, "episodes": episodes}

    found = _load("search:%s:%d" % (query.casefold(), offset), TTL_SEARCH, search)
    if found is None:
        _fail_directory()
        return
    for show in found["shows"]:
        _add_show(show, ", ".join(show.get("genres") or []))
    for episode in found["episodes"]:
        _add_episode(episode)
    if not found["shows"] and not found["episodes"]:
        notify(_(30121))
    if offset + len(found["shows"]) < found["total"] and found["shows"]:
        add_dir(_(30341), "ivys_search", q=query, offset=offset + len(found["shows"]))
    end("videos", cache=from_keyboard)


def _adaptive_available():
    return setting_bool("ivysilani_adaptive") and bool(
        xbmc.getCondVisibility("System.AddonIsEnabled(inputstream.adaptive)"))


def ivys_play(params):
    # Přehrávání z iVysílání služba doplňku nesleduje; nesmí zůstat záznam o videu z katalogu.
    xbmcgui.Window(10000).clearProperty(PLAYING_PROPERTY)
    try:
        client = _client()
        if params.get("channel"):
            stream = client.live(params["channel"])
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
    xbmcplugin.setResolvedUrl(ui.HANDLE, True, li)
