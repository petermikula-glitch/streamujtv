# -*- coding: utf-8 -*-
"""Router a obrazovky doplňku."""
import json
import sqlite3
import sys
import time
import urllib.parse
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
from itertools import islice
from math import isfinite

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

import catalog
import csfd
import kodi_library
import library_export
from nameparser import parse
import provider
import search_results
from search_results import normalize
from db import Library
import kodiutils
from kodiutils import (ADDON, ADDON_ID, FANART, ICON, KODI_MAJOR, Progress, keyboard, log,
                       log_error, log_info, notify, number, ok, profile_path, select, set_setting,
                       setting, setting_bool, setting_int, text_viewer, yesno)
from tmdb import IMG, Tmdb, TmdbError

BASE_URL = "plugin://%s/" % ADDON_ID
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1

LANG_OPTIONS = ["CZ", "SK", "EN", ""]
QUALITY_OPTIONS = ["", "UHD", "FHD", "HD", "SD"]
MOVIE_FILTER_OPTIONS = {
    "language": {"": 30428, "CZ": 30383, "SK": 30384, "EN": 30385, "CZ_SUB": 30429, "SK_SUB": 30450},
    "quality": dict(zip(QUALITY_OPTIONS, (30428, 30043, 30044, 30045, 30046))),
    "status": {"": 30428, "unwatched": 30430, "watched": 30431},
}
EPISODE_CACHE_TTL = 7 * 86400
EPISODE_CACHE_RETRY = 15 * 60
FILM_ORIGINS = {
    "CZ": (30487, {"cz", "cze", "cesko", "ceska republika", "czechia", "czech republic"}),
    "SK": (30488, {"sk", "svk", "slovensko", "slovenska republika", "slovakia", "slovak republic"}),
    "CS": (30489, {"cs", "csk", "xc", "ceskoslovensko", "czechoslovakia", "cssr", "csfr"}),
}

FILM_ORIGINS["CZSK"] = (30489, set().union(*(aliases for _label, aliases in FILM_ORIGINS.values())))
SEARCH_COUNTRIES = {
    "CZSK": {"ceskoslovenske", "ceske a slovenske", "ceskoslovenska", "cesko a slovensko"},
    "CZ": {"ceske", "ceska", "czech"},
    "SK": {"slovenske", "slovenska", "slovak"},
    "US": {"usa", "amerika", "spojene staty", "spojene staty americke", "united states",
           "united states of america", "americke"},
    "GB": {"velka britanie", "velke britanie", "spojene kralovstvi", "united kingdom", "uk", "britske"},
    "FR": {"francie", "france", "francouzske"},
    "DE": {"nemecko", "nemecka", "germany", "nemecke"},
    "PL": {"polsko", "poland", "polske"},
    "IT": {"italie", "italy", "italske"},
    "JP": {"japonsko", "japan", "japonske"},
}
COUNTRY_QUERY_ALIASES = {
    alias: code for code, names in SEARCH_COUNTRIES.items()
    for alias in names | FILM_ORIGINS.get(code, (0, set()))[1]
    if len(alias) > 2
}
AVAILABILITY_BATCH = 4
AVAILABILITY_WORKERS = 2  # Přehraj.to rychle omezuje souběžné dotazy (HTTP 429)
ACTOR_PAGE_SIZE = 20


_URL_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~")


def _url_value(value):
    text = value if isinstance(value, str) else str(value)
    if _URL_SAFE.issuperset(text):
        return text
    return urllib.parse.quote_plus(text)


def url_for(action, **params):
    """Stejný výsledek jako ``urlencode`` (action na konci), jen bez zbytečného kódování."""
    parts = ["%s=%s" % (_url_value(k), _url_value(v)) for k, v in params.items()
             if v is not None and v != "" and k != "action"]
    parts.append("action=" + _url_value(action))
    return BASE_URL + "?" + "&".join(parts)


_STRINGS = {}


def _(string_id):
    """Přeložené řetězce se v jednom běhu doplňku nemění – stačí je načíst jednou."""
    try:
        return _STRINGS[string_id]
    except KeyError:
        value = _STRINGS[string_id] = kodiutils._(string_id)
        return value


_PENDING_ITEMS = []
LIST_CAST_LIMIT = 6


def _list_item(label="", label2="", path=""):
    """Položka seznamu mimo GUI vlákno (rychlejší tvorba dlouhých výpisů)."""
    return xbmcgui.ListItem(label, label2, path, offscreen=True)


def _add_item(url, li, folder):
    """Položky se odešlou najednou v ``end()`` přes addDirectoryItems."""
    _PENDING_ITEMS.append((url, li, bool(folder)))


def _flush_items():
    if _PENDING_ITEMS:
        items = list(_PENDING_ITEMS)
        del _PENDING_ITEMS[:]
        xbmcplugin.addDirectoryItems(HANDLE, items, len(items))


def _fail_directory():
    del _PENDING_ITEMS[:]
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


PLAYING_PROPERTY = ADDON_ID + ".playing"
PLAYBACK_START_TIMEOUT = 120


def open_library():
    return Library(profile_path("catalog.db"), video_key=provider.video_key, prefer_ref=provider.prefer_ref)


_PLAYBACK = {}


def _playback_state():
    """{"resume": {vid: (pozice, délka)}, "watched": set()} – načte se jednou za běh."""
    if not _PLAYBACK:
        with open_library() as lib:
            _PLAYBACK["resume"] = lib.resume_points()
            _PLAYBACK["watched"] = lib.watched()
    return _PLAYBACK


def _apply_playback(li, vid, watched=False, suggested=None):
    state = _playback_state()
    watched = watched or vid in state["watched"]
    if watched:
        if KODI_MAJOR >= 20:
            li.getVideoInfoTag().setPlaycount(1)
        else:
            li.setInfo("video", {"playcount": 1})
        return
    point = state["resume"].get(vid) or suggested
    if point:
        _set_resume_point(li, point)


def _set_resume_point(li, point):
    position, total = point
    if KODI_MAJOR >= 20:
        li.getVideoInfoTag().setResumePoint(float(position), float(total))
    else:
        li.setProperty("ResumeTime", str(int(position)))
        li.setProperty("TotalTime", str(int(total)))


def _version_group(video):
    if video and video.get("meta_key"):
        if video["kind"] == "movie":
            return (video["meta_key"],)
        if video["kind"] == "episode" and video["episode"] > 0:
            return video["meta_key"], video["season"], video["episode"]
    return None


def _resume_groups(lib):
    groups = {}
    for video in lib.history_videos(in_progress=True, limit=-1):
        group = _version_group(video)
        if group:
            groups.setdefault(group, []).append(video)
    return groups


def _transfer_position(target, other_versions):
    for source in other_versions:
        if source["id"] == target["id"]:
            continue
        target_duration = target["duration"] or 0
        position, source_duration = source["position"], source["total"]
        if target_duration and position >= target_duration:
            continue
        tolerance = min(90, max(20, source_duration * 0.03))
        uncertain = not target_duration or abs(target_duration - source_duration) > tolerance
        return (position, target_duration or source_duration), uncertain, source_duration
    return None


def _apply_version_playback(li, video, context, watched=False):
    state = _playback_state()
    vid = video["id"]
    group = _version_group(video)
    candidate = None
    if group and not watched and vid not in state["watched"] and vid not in state["resume"] and state["resume"]:
        if "transfer_groups" not in state:
            with open_library() as lib:
                state["transfer_groups"] = _resume_groups(lib)
        candidate = _transfer_position(video, state["transfer_groups"].get(group, []))
    if candidate:
        context.insert(1, (_(30454), "PlayMedia(%s,noresume)" % url_for("play", vid=vid, transfer=1)))
    _apply_playback(li, vid, watched=watched, suggested=candidate[0] if candidate else None)
    return 1 if candidate else None


def _account_scope():
    username = setting("username").strip().casefold()
    return sha256(username.encode("utf-8")).hexdigest() if username else "guest"


def make_client():
    scope = _account_scope()
    cookie_file = profile_path("cookies-" + scope + ".lwp") if scope != "guest" else None
    return provider.make_client(setting("username"), setting("password"),
                                location=setting("location") or "1", cookie_file=cookie_file, log=log)


class _ResponseCache(object):
    """Mezipaměť odpovědí v katalogu; každé volání má vlastní spojení (volá se i z vláken)."""

    def get(self, key):
        with open_library() as lib:
            return lib.cache_get(key)

    def put(self, key, value, ttl):
        with open_library() as lib:
            lib.cache_put(key, value, ttl)


def make_tmdb(cached=True):
    return Tmdb(setting("tmdb_key"), log=log, cache=_ResponseCache() if cached else None)


def _search_client():
    client = make_client()
    client.request_timeout = 5
    client.request_retries = 0
    return client


# ---------------------------------------------------------------------------
# Tvorba položek
# ---------------------------------------------------------------------------

def _display_title(meta):
    return meta.get("title") or meta.get("search_title") or ""


def _display_year(meta):
    return meta.get("year") or meta.get("search_year") or 0


def _plot(meta, extra_lines=None):
    lines = []
    if meta.get("rating") and setting_bool("show_rating"):
        src = {"csfd": "ČSFD", "tmdb": "TMDB", "manual": _(30329)}.get(meta.get("rating_source"), "")
        votes = (" (%s)" % meta["votes"]) if meta.get("votes") else ""
        lines.append("[B]%s %s: %d %%[/B]%s" % (_(30329), src, meta["rating"], votes))
    if extra_lines:
        lines.extend(x for x in extra_lines if x)
    if meta.get("plot"):
        lines.append(meta["plot"])
    return "\n".join(lines)


def _set_info(li, info, cast=None, cast_limit=LIST_CAST_LIMIT):
    """Kodi 20+ používá InfoTagVideo, Kodi 19 setInfo.

    Ve výpisech stačí hlavní herci (``cast_limit``); úplné obsazení má detail titulu."""
    if cast and cast_limit:
        cast = cast[:cast_limit]
    if KODI_MAJOR >= 20:
        tag = li.getVideoInfoTag()
        setters = {
            "title": tag.setTitle, "originaltitle": tag.setOriginalTitle, "plot": tag.setPlot,
            "mediatype": tag.setMediaType, "tvshowtitle": tag.setTvShowTitle,
            "dateadded": tag.setDateAdded,
        }
        for k, fn in setters.items():
            if info.get(k):
                fn(info[k])
        if info.get("year"):
            tag.setYear(int(info["year"]))
        if info.get("genre"):
            tag.setGenres(list(info["genre"]))
        if info.get("country"):
            tag.setCountries(list(info["country"]))
        if info.get("director"):
            tag.setDirectors(list(info["director"]))
        if info.get("duration"):
            tag.setDuration(int(info["duration"]))
        if info.get("season") is not None and info.get("mediatype") in ("season", "episode"):
            tag.setSeason(int(info["season"]))
        if info.get("episode"):
            tag.setEpisode(int(info["episode"]))
        if info.get("rating"):
            tag.setRating(float(info["rating"]), int(info.get("votes") or 0),
                          info.get("rating_type") or "csfd", True)
        if cast:
            tag.setCast([xbmc.Actor(name) for name in cast])
    else:
        data = dict((k, v) for k, v in info.items() if k not in ("rating_type",) and v)
        if "genre" in data:
            data["genre"] = list(data["genre"])
        li.setInfo("video", data)
        if info.get("rating"):
            li.setRating(info.get("rating_type") or "csfd", float(info["rating"]),
                         int(info.get("votes") or 0), True)
        if cast:
            li.setCast([{"name": n} for n in cast])


def _art(li, poster="", thumb="", fanart="", episode_still=""):
    art = {"icon": ICON, "fanart": fanart or FANART}
    if poster:
        art.update({"poster": poster, "thumb": poster})
    elif thumb:
        art.update({"thumb": thumb, "poster": thumb})
    else:
        art["thumb"] = ICON
    if thumb:
        art["landscape"] = thumb
    if episode_still:
        art.update(thumb=episode_still, landscape=episode_still)
    li.setArt(art)


def _meta_info(meta, mediatype):
    rating = meta.get("rating") or 0
    return {
        "title": _display_title(meta), "originaltitle": meta.get("orig_title") or "",
        "year": _display_year(meta), "genre": meta.get("genres") or [],
        "country": meta.get("countries") or [], "director": meta.get("directors") or [],
        "rating": rating / 10.0 if rating else 0, "votes": meta.get("votes") or 0,
        "rating_type": meta.get("rating_source") or "csfd",
        "mediatype": mediatype,
    }


def _run_action(label, action, **params):
    return (_(label), "RunPlugin(%s)" % url_for(action, **params))


def _watch_action(watched, label=30410, **params):
    return _run_action(30411 if watched else label, "mark_watched", watched=int(not watched), **params)


def _meta_context(meta, collection_id=None, **scope):
    key = meta["key"]
    items = [_run_action(30128, "collection_add", target=key),
             _run_action(30409, "manage_item", key=key, **scope)]
    if collection_id:
        items.insert(1, _run_action(30129, "collection_remove", cid=collection_id, target=key))
    return items


def _video_context(video, choose_stream, watched):
    vid = video["id"]
    key = video.get("meta_key")
    if choose_stream:
        choice = (_(30396), "PlayMedia(%s)" % url_for("play", vid=vid, choose=1))
    elif key and video["kind"] == "movie":
        choice = (_(30395), "Container.Update(%s)" % url_for("versions", key=key))
    elif key and video["episode"]:
        choice = (_(30395), "Container.Update(%s)" % url_for(
            "season", key=key, season=video["season"], episode=video["episode"]))
    else:
        choice = (_(30396), "PlayMedia(%s)" % url_for("play", vid=vid, choose=1))
    return [choice, _run_action(30128, "collection_add", target="v:" + vid),
            _watch_action(watched, vid=vid),
            _run_action(30409, "manage_item", vid=vid)]


def _version_label(video, playback_type=""):
    quality = video.get("quality") or ""
    quality = provider.QUALITY_LABEL.get(quality, quality)
    parts = (video.get("lang"), quality, video.get("size"), playback_type)
    return " · ".join(part for part in parts if part)


def _movie_versions(lib, movies):
    """Verze všech filmů s více videi jedním dotazem (místo dotazu pro každý film)."""
    keys = [movie["key"] for movie in movies if (movie.get("video_count") or 1) > 1]
    return lib.videos_grouped(keys) if keys else {}


def _add_movies(lib, movies, **kwargs):
    movies = list(movies)
    versions = _movie_versions(lib, movies)
    for movie in movies:
        add_movie(movie, lib, versions=versions, **kwargs)


def add_movie(meta, lib, collection_id=None, repair=False, matching=None, versions=None):
    year = _display_year(meta)
    label = _display_title(meta) + (" (%d)" % year if year else "")
    count = meta.get("video_count") or 1
    langs = ", ".join(x for x in (meta.get("langs") or "").split(",") if x)
    extra = [langs]
    if count > 1:
        extra.append(_(30133) % count)
    li = _list_item(label)
    info = _meta_info(meta, "movie")
    info["plot"] = _plot(meta, extra)
    info["duration"] = (meta.get("runtime") or 0) * 60 or meta.get("duration") or 0
    if meta.get("added_at"):
        info["dateadded"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["added_at"]))
    _set_info(li, info, meta.get("cast"))
    _art(li, meta.get("poster"), meta.get("thumb"), meta.get("fanart"))
    context = _meta_context(meta, collection_id)
    if repair:
        li.addContextMenuItems(context)
        _add_item(url_for("repair_title", key=meta["key"]), li, True)
        return
    vids = [meta["first_video"]]
    vid = vids[0]
    preferred = None
    if matching is not None:
        videos, candidates = matching
        preferred = min(candidates, key=_version_score)
        vid = preferred["id"]
        vids = [video["id"] for video in videos]
    elif count > 1:
        if versions is not None and meta["key"] in versions:
            videos = versions[meta["key"]]
        else:
            videos = lib.videos_for_meta(meta["key"])
        preferred = min(videos, key=_version_score, default=None)
        if not preferred:
            return
        vid = preferred["id"]
        vids = [video["id"] for video in videos]
    watched = not _playback_state()["watched"].isdisjoint(vids)
    context.insert(0, (_(30395), "Container.Update(%s)" % url_for("versions", key=meta["key"])))
    context.insert(2, _watch_action(watched, key=meta["key"]))
    if setting("tmdb_key").strip() and (meta.get("tmdb_id") or year):
        context.insert(3, (_(30503), "Container.Update(%s)" % url_for("movie_parts", key=meta["key"])))
    li.setProperty("IsPlayable", "true")
    offer_transfer = None
    if preferred:
        offer_transfer = _apply_version_playback(li, preferred, context, watched=watched)
    else:
        _apply_playback(li, vid, watched=watched)
    li.addContextMenuItems(context)
    _add_item(url_for("play", vid=vid, offer_transfer=offer_transfer), li, False)


def add_show(meta, collection_id=None, repair=False, new_count=0):
    year = _display_year(meta)
    label = _display_title(meta) + (" (%d)" % year if year else "")
    if new_count:
        label += " · " + _(30433) % new_count
    li = _list_item(label)
    info = _meta_info(meta, "tvshow")
    info["tvshowtitle"] = _display_title(meta)
    info["plot"] = _plot(meta, [_(30133) % (meta.get("video_count") or 0)])
    _set_info(li, info, meta.get("cast"))
    _art(li, meta.get("poster"), meta.get("thumb"), meta.get("fanart"))
    li.addContextMenuItems(_meta_context(meta, collection_id))
    _add_item(url_for("repair_title" if repair else "show", key=meta["key"]), li, True)


def add_video(video, meta=None, label=None, mediatype="video", progress="", choose_stream=False,
              progress_key=None, watched=False, episode_data=None):
    meta = meta or {}
    watched = watched or video["id"] in _playback_state()["watched"]
    episode_data = episode_data or {}
    if label is None:
        label = video["raw_title"]
    li = _list_item(label)
    info = {"title": label, "mediatype": mediatype, "duration": video.get("duration") or 0}
    extra = [progress, _version_label(video), "%s: %s" % (_(30330), video["raw_title"])]
    if meta:
        info.update(_meta_info(meta, mediatype))
        info["title"] = label
        info["plot"] = _plot(meta, extra)
    else:
        info["plot"] = "\n".join([x for x in extra if x] + [video.get("description") or ""])
    if mediatype == "episode":
        info["plot"] = "\n".join(part for part in extra if part)
        info["duration"] = episode_data.get("duration") or info["duration"]
        info["tvshowtitle"] = _display_title(meta) if meta else video["title"]
        info["season"] = video.get("season") or 0
        info["episode"] = video.get("episode") or 0
    if video.get("added_at"):
        info["dateadded"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(video["added_at"]))
    _set_info(li, info, meta.get("cast") if meta else None)
    _art(li, meta.get("poster"), video.get("thumb"), meta.get("fanart"), episode_data.get("still"))
    li.setProperty("IsPlayable", "true")
    context = _video_context(video, choose_stream, watched)
    offer_transfer = _apply_version_playback(li, video, context, watched=watched)
    if progress_key:
        context.insert(3, _run_action(30413, "progress_visibility", key=progress_key, hide=1))
    if mediatype == "episode" and _episode_metadata_key(meta, video["season"]) and video["episode"]:
        context.insert(-1, _episode_detail_action(meta["key"], video["season"], video["episode"]))
    li.addContextMenuItems(context)
    url = url_for("play", vid=video["id"], offer_transfer=offer_transfer)
    _add_item(url, li, False)


def add_dir(label, action, icon="", context=None, **params):
    li = _list_item(label)
    li.setArt({"icon": icon or "DefaultFolder.png", "thumb": icon or "DefaultFolder.png",
               "fanart": FANART})
    if context:
        li.addContextMenuItems(context)
    _add_item(url_for(action, **params), li, True)


def add_action(label, action, icon="", **params):
    li = _list_item(label)
    li.setArt({"icon": icon or "DefaultAddonService.png", "thumb": icon or "DefaultAddonService.png",
               "fanart": FANART})
    _add_item(url_for(action, **params), li, False)


def end(content=None, sort=None, cache=True):
    if content:
        xbmcplugin.setContent(HANDLE, content)
    _flush_items()
    for s in sort or [xbmcplugin.SORT_METHOD_NONE]:
        xbmcplugin.addSortMethod(HANDLE, s)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache)


MOVIE_SORTS = [xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE,
               xbmcplugin.SORT_METHOD_VIDEO_YEAR, xbmcplugin.SORT_METHOD_VIDEO_RATING,
               xbmcplugin.SORT_METHOD_DATEADDED, xbmcplugin.SORT_METHOD_GENRE]


# ---------------------------------------------------------------------------
# Obrazovky
# ---------------------------------------------------------------------------

def _list_page_size():
    """Počet položek katalogu na stránku; 0 = vše najednou."""
    return max(0, setting_int("list_page_size", 250))


def _paginate(items, params):
    """Vrátí (položky aktuální stránky, (stránka, počet stránek) nebo None)."""
    items = list(items)
    size = _list_page_size()
    if not size or len(items) <= size:
        return items, None
    pages = (len(items) + size - 1) // size
    try:
        page = min(max(1, int(params.get("page") or 1)), pages)
    except ValueError:
        page = 1
    return items[(page - 1) * size:page * size], (page, pages)


def _add_next_page(action, params, paging):
    """Položka „Další stránka“ – vždy na konci seznamu, i po seřazení v Kodi."""
    if not paging or paging[0] >= paging[1]:
        return
    page, pages = paging
    li = _list_item(_(30570) % (page + 1, pages))
    li.setArt({"icon": "DefaultFolder.png", "thumb": "DefaultFolder.png", "fanart": FANART})
    li.setProperty("SpecialSort", "bottom")
    _add_item(url_for(action, **dict(params, page=page + 1)), li, True)


def _first_page(params):
    return (params.get("page") or "1") == "1"


def add_saved_movies(label):
    filters = [(_(30394), "Container.Update(%s)" % url_for("movies_menu"))]
    add_dir(label, "movies", "DefaultMovies.png", context=filters, order="added", browse=1)


def root(params):
    with open_library() as lib:
        empty = lib.count_videos() == 0
    add_dir(_(30360), "in_progress", "DefaultInProgressShows.png")
    add_saved_movies(_(30393))
    show_filters = [(_(30422), "Container.Update(%s)" % url_for("shows_menu"))]
    add_dir(_(30406), "shows", "DefaultTVShows.png", context=show_filters)
    search_history = [(_(30408), "Container.Update(%s)" % url_for("search_menu"))]
    add_dir(_(30407), "search", "DefaultAddonsSearch.png", context=search_history)
    add_dir(_(30421), "tmdb_catalog", "DefaultMovies.png")
    add_dir(_(30490), "news_menu", "DefaultRecentlyAddedMovies.png")
    add_dir(_(30420), "my_videos_menu", "DefaultVideo.png")
    add_dir(_(30103), "collections", "DefaultSets.png")
    add_dir(_(30106), "tools", "DefaultAddonProgram.png")
    end(cache=False)
    if empty and not params.get("quiet"):
        notify(_(30132), time_ms=8000)


def my_videos_menu(params):
    add_dir(_(30104), "recent", "DefaultRecentlyAddedMovies.png")
    add_dir(_(30134), "all_videos", "DefaultVideo.png")
    add_dir(_(30137), "played", "DefaultRecentlyAddedEpisodes.png")
    end()


def news_menu(params):
    add_dir(_(30491), "recent", "DefaultRecentlyAddedEpisodes.png")
    if make_tmdb().enabled:
        add_dir(_(30492), "tmdb_titles", "DefaultRecentlyAddedMovies.png", kind="movie", category="recent")
        add_dir(_(30522), "tmdb_titles", "DefaultRecentlyAddedMovies.png", kind="movie",
                category="recent", origin="CZSK")
    end(cache=False)


def _add_origin_shortcuts(action, **params):
    add_dir(_(30489), action, "DefaultCountry.png", origin="CZSK", **params)


def tmdb_catalog(params):
    add_dir(_(30374), "watchlist", "DefaultFavourites.png")
    if not make_tmdb().enabled:
        notify(_(30355), time_ms=8000)
        add_action(_(30356), "settings")
    else:
        _add_origin_shortcuts("tmdb_titles", kind="movie", category="discover")
        add_dir(_(30111), "tmdb_filters", "DefaultCountry.png", kind="movie", filter_by="origin")
        add_dir(_(30390), "tmdb_titles", "DefaultMovies.png", kind="movie", category="popular")
        add_dir(_(30100), "tmdb_categories", "DefaultMovies.png", kind="movie")
        add_dir(_(30101), "tmdb_categories", "DefaultTVShows.png", kind="tv")
    end(cache=False)


def tmdb_categories(params):
    kind = params.get("kind", "movie")
    if kind == "movie":
        _add_origin_shortcuts("tmdb_titles", kind=kind, category="discover")
        add_dir(_(30492), "tmdb_titles", kind=kind, category="recent")
    add_dir(_(30351), "tmdb_titles", kind=kind, category="popular")
    add_dir(_(30352), "tmdb_titles", kind=kind, category="top_rated")
    add_dir(_(30353 if kind == "movie" else 30354), "tmdb_titles", kind=kind,
            category="upcoming" if kind == "movie" else "on_the_air")
    add_dir(_(30495), "tmdb_facets", kind=kind)
    end()


def _tmdb_selection(params):
    selection = {"kind": params.get("kind", "movie"),
                 "category": "recent" if params.get("category") == "recent" else "discover",
                 "genre": params.get("genre", ""), "year": params.get("year", ""),
                 "origin": params.get("origin", "")}
    selection.update({flag: "1" for flag in ("catalogue", "found_only", "local_only") if params.get(flag) == "1"})
    return selection


def tmdb_facets(params):
    selection = _tmdb_selection(params)
    for field, label in (("genre", 30109), ("year", 30357), ("origin", 30111)):
        if field != "origin" or selection["kind"] == "movie":
            add_dir(_(label), "tmdb_filters", filter_by=field, **selection)
    if any(selection[field] for field in ("genre", "year", "origin")):
        add_dir(_(30496), "tmdb_titles", **dict(selection, genre="", year="", origin=""))
    end(cache=False)


def _tmdb_filter_options(client, kind, field):
    if field == "genre":
        if not client.enabled:
            return sorted(((code, _(genre[0])) for code, genre in search_results.GENRES.items()),
                          key=lambda entry: normalize(entry[1]))
        return [(genre["id"], genre["name"]) for genre in client.genres(kind)]
    if field == "origin":
        names = {country["iso_3166_1"]: country.get("native_name") or country["english_name"]
                 for country in client.countries()} if client.enabled else {
                     code: code for code in SEARCH_COUNTRIES if len(code) == 2}
        options = [(origin, _(FILM_ORIGINS[origin][0])) for origin in ("CZSK", "CZ", "SK")]
        options.extend(sorted(((code, name) for code, name in names.items() if code not in ("CZ", "SK")),
                              key=lambda entry: normalize(entry[1])))
        return options
    current_year = date.today().year
    decades = [("%ds" % year, "%d–%d" % (year, year + 9)) for year in range(current_year // 10 * 10, 1879, -10)]
    return decades + [(year, str(year)) for year in range(current_year, 1873, -1)]


def tmdb_filters(params):
    selection = _tmdb_selection(params)
    kind = selection["kind"]
    field = params.get("filter_by")
    if field not in ("genre", "year", "origin") or (field == "origin" and kind != "movie"):
        end(cache=False)
        return
    try:
        options = _tmdb_filter_options(make_tmdb(), kind, field)
    except TmdbError as e:
        notify(str(e), time_ms=8000)
        _fail_directory()
        return
    if selection[field]:
        options.insert(0, ("", _(30428)))
    for value, label in options:
        add_dir(label, "tmdb_titles", **dict(selection, **{field: value}))
    end()


def tmdb_refine(params):
    selection = _tmdb_selection(params)
    fields = [("genre", 30109), ("year", 30357)]
    if selection["kind"] == "movie":
        fields.insert(0, ("origin", 30111))
    choice = select(_(30495), [_(label) for _field, label in fields] + [_(30496)])
    if choice < 0:
        return
    if choice == len(fields):
        selection.update(genre="", year="", origin="")
    else:
        field, heading = fields[choice]
        try:
            options = [("", _(30428))] + _tmdb_filter_options(make_tmdb(), selection["kind"], field)
        except TmdbError as error:
            notify(str(error), time_ms=8000)
            return
        current = next((i for i, (value, _label) in enumerate(options)
                        if str(value) == selection[field]), 0)
        picked = select(_(heading), [label for _value, label in options], preselect=current)
        if picked < 0:
            return
        selection[field] = options[picked][0]
    xbmc.executebuiltin("Container.Update(%s,replace)" % url_for("tmdb_titles", **selection))


def _result_pages():
    return min(3, max(1, setting_int("search_result_pages", 2)))


def _tmdb_page_batch(load, page=1):
    items = {}
    for offset in range(_result_pages()):
        try:
            titles, total_pages = load(page)
        except TmdbError:
            if not offset:
                raise
            return list(items.values()), page, True
        for item in titles:
            if item.get("id"):
                items.setdefault((item.get("media_type"), item["id"]), item)
        if page >= total_pages:
            return list(items.values()), None, False
        page += 1
    return list(items.values()), page, False


def _discovery_summary(selection):
    labels = []
    origin = selection["origin"]
    if origin:
        labels.append(_(FILM_ORIGINS[origin][0]) if origin in FILM_ORIGINS else origin)
    genre = search_results.GENRES.get(selection["genre"])
    if genre:
        labels.append(_(genre[0]))
    start, end_year = search_results.period(selection["year"])
    if start:
        labels.append(str(start) if start == end_year else "%d–%d" % (start, end_year))
    return " · ".join([_(30495), *labels])


def _availability_cache(lib, key, now):
    saved = _json_object(lib.get_kv(key))
    return {name: entry for name, entry in saved.items() if isinstance(entry, list) and len(entry) == 2
            and isinstance(entry[0], (int, float)) and isinstance(entry[1], bool)
            and 0 <= now - entry[0] < (1200 if entry[1] else 300)}


def _check_title_availability(titles, default_kind):
    key = "search:availability:" + _account_scope()
    with open_library() as lib:
        cached = _availability_cache(lib, key, time.time())
    candidates = {}
    for item in titles:
        kind = item.get("media_type") or default_kind
        card = search_results.remote_card(item, kind, None)
        if not card or not card["year"]:
            continue
        cache_id = "%s:%s" % (kind, item["id"])
        candidates[cache_id] = card
    todo = [(cache_id, card) for cache_id, card in candidates.items() if cache_id not in cached]
    checked = {}
    failed = False
    if todo:
        client = _search_client()
        stop = []

        def check(card):
            item = card["tmdb"]
            original = item.get("original_title") or item.get("original_name") or ""
            expected_kind = "movie" if card["kind"] == "movie" else "episode"
            for title in dict.fromkeys((card["title"], original)):
                if not title or stop:
                    continue
                try:
                    videos, _cursor = provider.search_catalog(client, title, 1, "")
                except (provider.ProviderError, provider.RateLimited):
                    stop.append(True)  # např. HTTP 429 – další dotazy už neposílat
                    raise
                if any(search_results.filename_matches(parse(video["title"]), card["names"], card["year"],
                                                       kind=expected_kind, require_year=True)
                       and not search_results.is_preview(video["title"], (card["title"], original))
                       for video in videos):
                    return True
            if stop:
                raise provider.ProviderError("ověřování přerušeno")
            return False

        batch = list(islice(todo, AVAILABILITY_BATCH))
        with ThreadPoolExecutor(max_workers=AVAILABILITY_WORKERS) as pool:
            tasks = [(cache_id, pool.submit(check, card)) for cache_id, card in batch]
            for cache_id, task in tasks:
                try:
                    checked[cache_id] = [time.time(), task.result()]
                except (provider.ProviderError, provider.RateLimited):
                    failed = True
    if checked:
        with open_library() as lib, lib.conn:
            lib.conn.execute("BEGIN IMMEDIATE")
            saved = _availability_cache(lib, key, time.time())
            saved.update(checked)
            newest = sorted(saved, key=lambda name: saved[name][0], reverse=True)[:250]
            lib.set_kv(key, json.dumps({name: saved[name] for name in newest}))
        cached.update(checked)
    found_ids = {cache_id for cache_id in candidates if cached.get(cache_id, (0, False))[1]}
    return found_ids, len(todo) > len(checked), failed


def _availability_controls(action, params, titles, default_kind):
    found, pending, failed = _check_title_availability(titles, default_kind)
    found_only = params.get("found_only") == "1"
    flip = dict(params, found_only="" if found_only else "1")
    add_dir(_(30537 if found_only else 30536), action, "DefaultGenre.png", **flip)
    if pending:
        add_dir(_(30542 if failed else 30538), action, "DefaultAddonsSearch.png", **params)
    if failed:
        notify(_(30469) % "Přehraj.to")
    elif found_only and not found:
        notify(_(30539))
    return found


def tmdb_titles(params, *, cache=False):
    selection = _tmdb_selection(params)
    kind = selection["kind"]
    category = params.get("category", "popular")
    origin = selection["origin"]
    allowed = ("popular", "top_rated", "discover", "upcoming", "recent") if kind == "movie" else (
        "popular", "top_rated", "discover", "on_the_air")
    catalogue = selection.get("catalogue") == "1"
    local_only = catalogue and selection.get("local_only") == "1"
    found_only = selection.get("found_only") == "1"
    titles, next_page, partial = [], None, False
    try:
        page = int(params.get("page", 1))
        if kind not in ("movie", "tv") or category not in allowed or not 1 <= page <= 500:
            raise ValueError(_(30358))
        if origin and (kind != "movie" or
                       (origin != "CZSK" and (len(origin) != 2 or not origin.isascii() or not origin.isalpha()))):
            raise ValueError(_(30358))
        filters = {"genre": selection["genre"], "year": selection["year"]}
        if origin:
            filters["origin"] = origin.upper()
        client = make_tmdb()
        if not client.enabled and not local_only:
            notify(_(30355))
            add_action(_(30356), "settings")

        def load(current):
            if origin != "CZSK":
                return client.browse(kind, category, current, **filters)
            # Dotazy s jednou zemí používají dokumentovaný parametr. Koprodukce se sloučí níže.
            cz, cz_pages = client.browse(kind, category, current, **dict(filters, origin="CZ"))
            sk, sk_pages = client.browse(kind, category, current, **dict(filters, origin="SK"))
            sort = "release_date" if category == "recent" else "popularity"
            titles = sorted(cz + sk, key=lambda item: item.get(sort) or ("" if sort == "release_date" else 0),
                            reverse=True)
            return titles, max(cz_pages, sk_pages)

        if client.enabled and not local_only:
            titles, next_page, partial = _tmdb_page_batch(load, page)
        elif not catalogue:
            end(cache=False)
            return
    except (ValueError, TmdbError) as e:
        notify(str(e), time_ms=8000)
        if not catalogue or isinstance(e, ValueError):
            _fail_directory()
            return

    refine = None
    if category in ("discover", "recent"):
        add_action(_discovery_summary(selection), "tmdb_refine", "DefaultGenre.png", **selection)
        refine = _run_action(30495, "tmdb_refine", **selection)
    if local_only:
        add_dir(_(30464), "tmdb_titles", "DefaultAddonsSearch.png", **dict(selection, local_only=""))
    found = set()
    if titles and kind == "movie" and (origin or catalogue or found_only):
        found = _availability_controls("tmdb_titles", dict(selection, category=category, page=page), titles, kind)
    with open_library() as lib:
        library_items = lib.movies() if kind == "movie" else lib.shows()
        shown = set()
        for item in titles:
            verified = "%s:%s" % (kind, item["id"]) in found
            if not found_only or verified:
                key = add_tmdb_title(item, kind, lib, library_items, refine=refine, provider_found=verified)
                if key:
                    shown.add(key)
        if catalogue and page == 1 and not found_only:
            start, end_year = search_results.period(selection["year"])
            genre = search_results.GENRES.get(selection["genre"])
            countries = {alias for alias, code in COUNTRY_QUERY_ALIASES.items() if code == origin}
            countries |= FILM_ORIGINS.get(origin, (0, set()))[1] | {origin.lower()}
            matching = []
            for movie in library_items:
                if movie["key"] in shown or (start and not start <= _display_year(movie) <= end_year):
                    continue
                if origin and not any(normalize(name) in countries for name in movie["countries"]):
                    continue
                if genre and not any(normalize(name) in genre[1] | {normalize(_(genre[0]))} for name in movie["genres"]):
                    continue
                matching.append(movie)
                shown.add(movie["key"])
            _add_movies(lib, matching)
    if partial:
        notify(_(30497))
    if next_page:
        add_dir(_(30341), "tmdb_titles", page=next_page, **dict(selection, category=category))
    if not titles and not shown and client.enabled:
        notify(_(30121))
    end("movies" if kind == "movie" else "tvshows", cache=cache)


def add_tmdb_title(item, kind, lib, library_items, status=None, refine=None, provider_found=False):
    title = item.get("title") or item.get("name")
    if not title or not item.get("id"):
        return
    original = item.get("original_title") or item.get("original_name") or title
    release = item.get("release_date") or item.get("first_air_date") or ""
    released_year = int(release[:4]) if release[:4].isdigit() else 0
    label = "%s (%d)" % (title, released_year) if released_year else title
    match = _library_match(library_items, {"tmdb_id": item["id"], "title": title,
                                            "orig_title": original, "year": released_year})
    videos = lib.videos_for_meta(match["key"]) if match and kind == "movie" else []
    resume = _playback_state()["resume"] if status and videos else {}
    video = min(videos, key=lambda version: (version["id"] not in resume, _version_score(version)), default=None)
    if status:
        label += " · " + _(status)
    elif video:
        label += " · " + _(30363)
    elif match and kind == "tv":
        label += " · " + _(30364)
    if provider_found:
        label += " · " + _(30535)
    li = _list_item(label)
    _set_info(li, {"title": title, "originaltitle": original, "year": released_year,
                   "plot": item.get("overview") or "", "rating": item.get("vote_average") or 0,
                   "votes": item.get("vote_count") or 0, "rating_type": "tmdb",
                   "mediatype": "movie" if kind == "movie" else "tvshow"})
    poster = item.get("poster_path")
    backdrop = item.get("backdrop_path")
    _art(li, poster=IMG + "w500" + poster if poster else "",
         fanart=IMG + "w1280" + backdrop if backdrop else "")
    detail_url = url_for("tmdb_title", kind=kind, tmdb_id=item["id"])
    args = {"kind": kind, "tmdb_id": item["id"], "title": title, "original": original,
            "year": released_year, "poster": IMG + "w500" + poster if poster else ""}
    context = [(_(30391), "Container.Update(%s)" % detail_url),
               (_(30375), "RunPlugin(%s)" % url_for("watchlist_change", **args))]
    if original != title:
        context.append((_(30359), "Container.Update(%s)" % url_for("provider_search", q=original)))
    if refine:
        context.append(refine)
    target = (url_for("tmdb_movie_versions", tmdb_id=item["id"], title=title, original=original,
                      year=released_year) if kind == "movie" else detail_url)
    folder = True
    if video:
        target = url_for("play", vid=video["id"])
        folder = False
        context.insert(0, (_(30363), "PlayMedia(%s)" % target))
        context.append((_(30395), "Container.Update(%s)" % url_for("versions", key=match["key"])))
        context.append((_(30340), "Container.Update(%s)" % url_for(
            "tmdb_movie_versions", tmdb_id=item["id"], title=title, original=original, year=released_year)))
        li.setProperty("IsPlayable", "true")
        watched = any(version["id"] in _playback_state()["watched"] for version in videos)
        offer_transfer = _apply_version_playback(li, video, context, watched=watched)
        target = url_for("play", vid=video["id"], offer_transfer=offer_transfer)
    elif match and kind == "tv":
        target = url_for("show", key=match["key"])
        context.insert(0, (_(30364), "Container.Update(%s)" % target))
    li.addContextMenuItems(context)
    _add_item(target, li, folder)
    return match["key"] if match else None


def _online_filters(params):
    return {"audio": params.get("audio") if params.get("audio") in ("CZ", "SK") else "",
            "quality": "FHD" if params.get("quality") == "FHD" else "",
            "hide_previews": "1" if params.get("hide_previews") == "1" else "",
            "unknown_audio": "1" if params.get("unknown_audio") == "1" else ""}


def _provider_matches_filter(video, parsed, filters, titles=()):
    version = _provider_version(video, parsed)
    audio = filters["audio"]
    if audio:
        spoken = {part.strip() for part in version["lang"].split(",")
                  if part.strip() and not part.strip().endswith(" titulky")}
        if not ({audio, audio + " dabing"} & spoken) and (spoken or not filters["unknown_audio"]):
            return False
    if filters["quality"] and provider.quality_token(version["quality"]) not in ("FHD", "UHD"):
        return False
    return not filters["hide_previews"] or not search_results.is_preview(video["title"], titles)


def _filter_online_videos(videos, filters, titles=()):
    return [video for video in videos if _provider_matches_filter(video, parse(video["title"]), filters, titles)]


def _add_online_filters(action, params):
    args = {key: params.get(key) for key in ("q", "title", "original", "year", "tmdb_id", "online", "series")}
    filters = _online_filters(params)
    active = [_(30508 if filters["audio"] == "CZ" else 30509)] if filters["audio"] else []
    if active and filters["unknown_audio"]:
        active.append(_(30512))
    if filters["quality"]:
        active.append(_(30510))
    if filters["hide_previews"]:
        active.append(_(30511))
    label = _(30507) + (" · " + ", ".join(active) if active else "")
    add_dir(label, "online_filters", "DefaultGenre.png", target=action, **args, **filters)


def online_filters(params):
    target = params.get("target")
    if target not in ("provider_search", "tmdb_movie_versions", "search"):
        end(cache=False)
        return
    options = ({}, {"audio": "CZ"}, {"audio": "SK"}, {"quality": "FHD"},
               {"hide_previews": "1"}, {"unknown_audio": "1"})
    choice = select(_(30507), [_(label) for label in (30496, 30508, 30509, 30510, 30511, 30541)])
    _fail_directory()
    if choice < 0:
        return
    filters = _online_filters(params) if choice else {}
    for field, value in options[choice].items():
        filters[field] = "" if filters.get(field) == value else value
    args = {key: params.get(key) for key in ("q", "title", "original", "year", "tmdb_id", "online", "series")}
    xbmc.executebuiltin("Container.Update(%s,replace)" % url_for(target, **args, **filters))


def _suitable_provider_video(videos, titles, year, position, filters, *, require_year=False):
    names = search_results.filename_names(titles)
    for video in videos:
        parsed = parse(video["title"])
        if not search_results.filename_matches(parsed, names, year, position, require_year=require_year):
            continue
        if not search_results.is_preview(video["title"], titles) and _provider_matches_filter(video, parsed, filters, titles):
            return True
    return False


def _expand_provider_search(client, videos, query, title, year, position, filters, tmdb, item=None):
    titles = [title]
    if item:
        original = item.get("original_title") or item.get("original_name") or ""
        if original and normalize(original) != normalize(title):
            titles.append(original)
    if _suitable_provider_video(videos, titles, year, position, filters, require_year=True):
        return videos, False, False

    merged = {provider.video_key(video["id"]): video for video in videos}
    tried = {normalize(query)}
    tmdb_error = False
    deferred = list(titles[1:]) + [title]
    for _ in range(4):
        if not deferred and item and not item.get("_skip_aliases"):
            try:
                alternatives = tmdb.alternative_titles(item)
            except TmdbError:
                tmdb_error = True
                break
            deferred = [name for name in alternatives if normalize(name) not in search_results.aliases(*titles)][:2]
            titles.extend(deferred)
            item["_search_aliases"] = deferred.copy()
            item = None
        if not deferred:
            break
        alternate = search_results.format_query(deferred.pop(0), position=position)
        key = normalize(alternate)
        if key in tried:
            continue
        tried.add(key)
        try:
            entries, _cursor = provider.search_catalog(client, alternate, 1, "")
        except (provider.ProviderError, provider.RateLimited):
            return list(merged.values()), True, tmdb_error
        names = search_results.filename_names(titles)
        for video in entries:
            if search_results.filename_matches(parse(video["title"]), names, year, position):
                merged.setdefault(provider.video_key(video["id"]), video)
        if _suitable_provider_video(merged.values(), titles, year, position, filters, require_year=True):
            break
    return list(merged.values()), False, tmdb_error


def _add_relaxed_searches(action, params, videos, filters, titles=()):
    args = {key: value for key, value in params.items() if key != "retry"}
    if action == "search":
        args["reuse"] = 1
    suggested = False
    for field, value, label in (("quality", "", 30540), ("unknown_audio", "1", 30541)):
        change = filters["quality"] if field == "quality" else filters["audio"] and not filters["unknown_audio"]
        relaxed = dict(filters, **{field: value})
        if change and _filter_online_videos(videos, relaxed, titles):
            add_dir(_(label), action, "DefaultAddonsSearch.png", **dict(args, **relaxed))
            suggested = True
    if not suggested and filters["quality"] and filters["audio"] and not filters["unknown_audio"]:
        relaxed = dict(filters, quality="", unknown_audio="1")
        if _filter_online_videos(videos, relaxed, titles):
            add_dir(_(30564), action, "DefaultAddonsSearch.png", **dict(args, **relaxed))


def _movie_result_key(video, parsed, title, original, year, alternatives=()):
    # Handles punctuation and bilingual names in either order. Use the raw
    # filename to detect trailers even when parse() strips everything after the year.
    titles = (title, original, *alternatives)
    preview = search_results.is_preview(video["title"], titles)
    exact = search_results.filename_matches(parsed, search_results.filename_names(titles), kind="movie")
    rank = 2
    if exact and (not year or parsed["year"] == year):
        rank = 0
    elif exact and not parsed["year"]:
        rank = 1
    version = _provider_version(video, parsed)
    return (preview, rank, _version_score(version))


def tmdb_movie_versions(params):
    title = params.get("title", "").strip()
    original = params.get("original", "").strip()
    query = params.get("q", "").strip() or title
    if not query:
        end(cache=False)
        return
    try:
        tmdb_id = int(params.get("tmdb_id") or 0)
        year = int(params.get("year") or 0)
        page = int(params.get("page") or 1)
    except ValueError:
        end(cache=False)
        return
    args = {key: params.get(key) for key in ("tmdb_id", "title", "original", "year")}
    local_ids = set()
    filters = _online_filters(params)
    args.update(filters)
    _add_online_filters("tmdb_movie_versions", dict(params, q=query))
    if page == 1:
        with open_library() as lib:
            match = _library_match(lib.movies(), {"tmdb_id": tmdb_id, "title": title,
                                                   "orig_title": original, "year": year})
            local = lib.videos_for_meta(match["key"]) if match else []
        for video in sorted(local, key=_version_score):
            label = _version_label(video) or video["raw_title"]
            add_video(video, match, label="%s · %s" % (_(30392), label), mediatype="movie")
            local_ids.add(provider.video_key(video["id"]))
    videos, cursor, partial = [], None, False
    try:
        client = _search_client()
        videos, next_page, cursor, partial = _provider_page_batch(client, query, page, params.get("cursor", ""))
        tmdb = make_tmdb()
        item = {"media_type": "movie", "id": tmdb_id, "original_title": original}
        if page == 1 and not partial and normalize(query) == normalize(title):
            # U uložených odkazů s ručně zadaným původním názvem nemusí být TMDB k dispozici.
            if not tmdb_id or not tmdb.enabled:
                item["_skip_aliases"] = True
            videos, partial, _metadata_error = _expand_provider_search(
                client, videos, query, title, year, None, filters, tmdb, item)
        names = (title, original, *item.get("_search_aliases", ()))
        filtered = _filter_online_videos(videos, filters, names)
    except provider.LoginError:
        notify(_(30368), time_ms=8000)
        add_action(_(30356), "settings")
    except (ValueError, provider.ProviderError, provider.RateLimited) as e:
        notify(_(30369) % e, time_ms=8000)
        add_dir(_(30542), "tmdb_movie_versions", q=query, page=page, cursor=params.get("cursor"), **args)
    else:
        versions = []
        for result in filtered:
            parsed = parse(result["title"])
            versions.append((_movie_result_key(result, parsed, title, original, year, names[2:]),
                             _provider_version(result, parsed)))
        for sort_key, video in sorted(versions, key=lambda item: item[0]):
            preview, rank = sort_key[:2]
            identity = provider.video_key(video["id"])
            if identity in local_ids:
                continue
            local_ids.add(identity)
            label = _version_label(video) if not preview and rank == 0 else ""
            # A descriptive second line plus the original filename help distinguish versions
            # that happen to have the same language, resolution and file size.
            add_provider_result(video, label or video["title"])
        if not filtered:
            _add_relaxed_searches("tmdb_movie_versions", dict(args, q=query, page=page, cursor=params.get("cursor")),
                                  videos, filters, names)
        if not filtered and not local_ids and not cursor:
            notify(_(30370))
    if partial:
        notify(_(30497) if cursor else _(30469) % "Přehraj.to")
        add_dir(_(30542), "tmdb_movie_versions", q=query, page=page, cursor=params.get("cursor"), **args)
    if cursor:
        add_dir(_(30341), "tmdb_movie_versions", q=query, page=next_page, cursor=cursor, **args)
    if page == 1 and original and normalize(original) != normalize(query):
        add_dir(_(30359), "tmdb_movie_versions", q=original, **args)
    end("videos", cache=False)


def _library_match(items, detail):
    exact = [item for item in items if item.get("tmdb_id") == detail["tmdb_id"]]
    if exact:
        return exact[0] if len(exact) == 1 else None
    titles = {normalize(t) for t in (detail["title"], detail["orig_title"]) if t}
    if not detail["year"] or not titles:
        return None
    matches = [
        item for item in items
        if not item.get("tmdb_id") and _display_year(item) == detail["year"]
        and titles.intersection(normalize(t) for t in (_display_title(item), item.get("orig_title")) if t)
    ]
    return matches[0] if len(matches) == 1 else None


def tmdb_title(params):
    want_series = params.get("kind") == "tv"
    try:
        tmdb_id = int(params.get("tmdb_id", 0))
        if tmdb_id < 1:
            raise ValueError(_(30358))
        client = make_tmdb()
        if not client.enabled:
            tmdb_catalog(params)
            return
        detail = client.details(tmdb_id, want_series)
    except (ValueError, TmdbError) as e:
        notify(str(e), time_ms=8000)
        _fail_directory()
        return
    detail["rating_source"] = "tmdb"
    with open_library() as lib:
        match = _library_match(lib.shows() if want_series else lib.movies(), detail)
        videos = lib.videos_for_meta(match["key"]) if match and not want_series else []
        kind = "tv" if want_series else "movie"
        saved = lib.watchlist_has(kind, tmdb_id)

    def add_option(label, action, folder=True, **route_params):
        li = _list_item(label)
        info = _meta_info(detail, "tvshow" if want_series else "movie")
        info["title"] = label
        info["plot"] = detail["plot"]
        _set_info(li, info, detail["cast"], cast_limit=None)
        _art(li, poster=detail["poster"], fanart=detail["fanart"])
        if not folder:
            li.setProperty("IsPlayable", "true")
            _apply_playback(li, route_params["vid"])
        _add_item(url_for(action, **route_params), li, folder)

    if match and want_series:
        add_option(_(30364), "show", key=match["key"])
    elif videos:
        preferred = min(videos, key=_version_score)
        add_option(_(30363), "play", folder=False, vid=preferred["id"])
        add_option(_(30395), "versions", key=match["key"])
    if not want_series and detail.get("collection_id"):
        add_option(_(30503), "movie_parts", collection_id=detail["collection_id"], tmdb_id=tmdb_id)
    title = detail["title"] or detail["orig_title"]
    if title:
        if want_series:
            add_option(_(30486), "provider_search", q=title, series=1, tmdb_id=tmdb_id, title=title,
                       original=detail["orig_title"], year=detail["year"])
        else:
            add_option(_(30340), "tmdb_movie_versions", title=title, original=detail["orig_title"],
                       year=detail["year"], tmdb_id=tmdb_id)
    if detail["orig_title"] and detail["orig_title"] != title:
        args = {"series": 1, "title": title, "original": detail["orig_title"], "year": detail["year"]} if want_series else {}
        add_option(_(30359), "provider_search", q=detail["orig_title"], **args)
    add_action(_(30376 if saved else 30375), "watchlist_change", kind=kind, tmdb_id=tmdb_id,
               remove=int(saved), title=title, year=detail["year"], poster=detail["poster"])
    end("tvshows" if want_series else "movies", cache=False)


def _movie_tmdb_id(client, meta):
    if meta.get("tmdb_id"):
        return meta["tmdb_id"]
    title = _display_title(meta)
    year = _display_year(meta)
    if not title or not year:
        return None
    names = search_results.aliases(title, meta.get("orig_title"), meta.get("search_title"))
    matches = {item["id"] for item in client.search(title)
               if item["year"] == year and not names.isdisjoint(
                   search_results.aliases(item["title"], item.get("orig_title")))}
    return next(iter(matches)) if len(matches) == 1 else None


def movie_parts(params):
    client = make_tmdb()
    if not client.enabled:
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        tmdb_id = int(params.get("tmdb_id", 0))
        collection_id = int(params.get("collection_id", 0))
        if "key" in params:
            with open_library() as lib:
                meta = lib.get_meta(params["key"])
            tmdb_id = _movie_tmdb_id(client, meta) if meta and meta["kind"] == "movie" else None
            collection_id = client.details(tmdb_id).get("collection_id", 0) if tmdb_id else 0
        parts = client.collection_movies(collection_id) if collection_id > 0 else []
    except (ValueError, TmdbError) as error:
        notify(str(error))
        end(cache=False)
        return
    if not parts:
        notify(_(30504))
        end("movies", cache=False)
        return
    with open_library() as lib:
        movies = lib.movies()
        watched, resume = lib.watched(), lib.resume_points()
        statuses = []
        for part in parts:
            released = part.get("release_date") or ""
            year = int(released[:4]) if released[:4].isdigit() else 0
            match = _library_match(movies, {"tmdb_id": part["id"], "title": part["title"],
                                           "orig_title": part.get("original_title"), "year": year})
            videos = lib.videos_for_meta(match["key"]) if match else []
            done = any(video["id"] in watched for video in videos)
            preferred = min(videos, key=lambda video: (video["id"] not in resume, _version_score(video)), default=None)
            label = 30431 if done else (30516 if preferred and preferred["id"] in resume else
                                        30517 if preferred else 30340)
            statuses.append((part, preferred, label, year))
        next_part = next((status for status in statuses if status[2] != 30431), None)
        if next_part:
            part, preferred, label, year = next_part
            title = _(30518) % part["title"]
            if preferred:
                card = search_results.new_card("movie", part["title"], year, tmdb=part)
                _search_entry(card, title, "play", {"vid": preferred["id"]}, preferred, direct_resume=True)
            else:
                add_dir(title, "tmdb_movie_versions", tmdb_id=part["id"], title=part["title"],
                        original=part.get("original_title"), year=year)
        for part, _preferred, status, _year in statuses:
            add_tmdb_title(part, "movie", lib, movies, status=status)
    end("movies", cache=False)


def offer_next_movie(vid, player):
    if player.isPlaying() or xbmcgui.Window(10000).getProperty(PLAYING_PROPERTY):
        return
    client = make_tmdb()
    if not client.enabled:
        return
    with open_library() as lib:
        current = lib.find_video(vid)
        meta = lib.get_meta(current["meta_key"]) if current and current["kind"] == "movie" else None
    if not meta:
        return
    tmdb_id = _movie_tmdb_id(client, meta)
    collection_id = client.details(tmdb_id).get("collection_id", 0) if tmdb_id else 0
    if not collection_id:
        return
    parts = client.collection_movies(collection_id)
    next_part = next((part for previous, part in zip(parts, parts[1:]) if previous["id"] == tmdb_id), None)
    if not next_part:
        return
    try:
        release = date.fromisoformat(next_part.get("release_date") or "")
    except ValueError:
        return
    if release > date.today():
        return
    detail = {"tmdb_id": next_part["id"], "title": next_part["title"],
              "orig_title": next_part.get("original_title") or "", "year": release.year}
    with open_library() as lib:
        match = _library_match(lib.movies(), detail)
        if not match:
            return
        resume = lib.resume_points()
        following = min(lib.videos_for_meta(match["key"]),
                        key=lambda video: (video["id"] not in resume, _version_score(video)), default=None)
    if following:
        _offer_playback(player, _(30506), next_part["title"], following["id"], following["id"] in resume)


def _refresh_watchlist_availability(after_sync):
    with open_library() as lib, lib.conn:
        lib.conn.execute("BEGIN IMMEDIATE")
        entries = lib.watchlist()
        if not entries:
            return 0
        library = {"movie": lib.movies(), "tv": lib.shows()}
        fresh = 0
        for entry in entries:
            key = entry["kind"], entry["tmdb_id"]
            match = _library_match(library[key[0]], dict(entry, orig_title=""))
            ready = bool(match)
            old = lib.conn.execute("SELECT ready, fresh FROM watchlist_availability "
                                   "WHERE kind=? AND tmdb_id=?", key).fetchone()
            if old is None:
                lib.conn.execute("INSERT INTO watchlist_availability VALUES (?, ?, ?, 0)", (*key, int(ready)))
            elif after_sync:
                became_ready = ready and not old["ready"]
                fresh += became_ready
                lib.conn.execute("UPDATE watchlist_availability SET ready=?, fresh=? WHERE kind=? AND tmdb_id=?",
                                 (int(ready), int(ready and (became_ready or old["fresh"])), *key))
    return fresh


def watchlist_acknowledge(params):
    with open_library() as lib:
        lib.acknowledge_watchlist(params["kind"], params["tmdb_id"])
    xbmc.executebuiltin("Container.Refresh")


def watchlist(params):
    ready_only = params.get("ready") == "1"
    switch_label = 30437 if ready_only else 30436
    add_dir(_(switch_label), "watchlist", "DefaultFavourites.png", ready=0 if ready_only else 1)
    found = False
    with open_library() as lib:
        library = {"movie": lib.movies(), "tv": lib.shows()}
        new_titles = lib.fresh_watchlist()
        for entry in lib.watchlist():
            kind = entry["kind"]
            match = _library_match(library[kind], dict(entry, orig_title=""))
            videos = lib.videos_for_meta(match["key"]) if match and kind == "movie" else []
            preferred = min(videos, key=_version_score, default=None)
            ready = bool(preferred or (match and kind == "tv"))
            if ready_only and not ready:
                continue
            found = True
            name = entry["title"]
            if entry["year"]:
                name += " (%d)" % entry["year"]
            fresh = ready and (kind, entry["tmdb_id"]) in new_titles
            if ready:
                name += " · " + _(30513 if fresh else 30363 if preferred else 30364)
            item = _list_item(name)
            _art(item, poster=entry["poster"])
            args = {"kind": kind, "tmdb_id": entry["tmdb_id"]}
            detail_url = url_for("tmdb_title", **args)
            context = [(_(30391), "Container.Update(%s)" % detail_url),
                       _run_action(30376, "watchlist_change", remove=1, **args)]
            target = detail_url
            if fresh:
                context.append(_run_action(30514, "watchlist_acknowledge", **args))
            if preferred:
                target = url_for("play", vid=preferred["id"])
                context.insert(0, (_(30363), "PlayMedia(%s)" % target))
                context.insert(1, (_(30395), "Container.Update(%s)" % url_for("versions", key=match["key"])))
                item.setProperty("IsPlayable", "true")
                watched = any(video["id"] in _playback_state()["watched"] for video in videos)
                offer_transfer = _apply_version_playback(item, preferred, context, watched=watched)
                target = url_for("play", vid=preferred["id"], offer_transfer=offer_transfer)
            elif ready:
                target = url_for("show", key=match["key"])
                context.insert(0, (_(30364), "Container.Update(%s)" % target))
            item.addContextMenuItems(context)
            _add_item(target, item, not preferred)
    if not found:
        notify(_(30438 if ready_only else 30377))
    end("videos", cache=False)


def watchlist_change(params):
    kind = params.get("kind")
    try:
        tmdb_id = int(params.get("tmdb_id", 0))
        year = int(params.get("year", 0))
    except ValueError:
        return
    if kind not in ("movie", "tv") or tmdb_id <= 0:
        return
    with open_library() as lib:
        if params.get("remove") == "1":
            lib.watchlist_remove(kind, tmdb_id)
        elif params.get("title"):
            poster = params.get("poster", "")
            lib.watchlist_add(kind, tmdb_id, params["title"], year, poster if poster.startswith(IMG) else "")
    xbmc.executebuiltin("Container.Refresh")


def tmdb_search(params):
    query = params.get("q", "").strip()
    client = make_tmdb()
    if not client.enabled:
        notify(_(30355), time_ms=8000)
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        page = int(params.get("page", 1))
        if not query or not 1 <= page <= 500:
            raise ValueError(_(30358))
        titles, next_page, partial = _tmdb_page_batch(lambda current: client.search_titles(query, current), page)
    except (TmdbError, ValueError) as e:
        notify(str(e), time_ms=8000)
        _fail_directory()
        return
    with open_library() as lib:
        library_items = {"movie": lib.movies(), "tv": lib.shows()}
        for item in titles:
            kind = item["media_type"]
            if kind in library_items:
                add_tmdb_title(item, kind, lib, library_items[kind])
    if partial:
        notify(_(30497))
    if next_page:
        add_dir(_(30341), "tmdb_search", q=query, page=next_page)
    elif not titles:
        notify(_(30121))
    end("videos", cache=False)


def movies_menu(params):
    _add_origin_shortcuts("movies", order="title")
    with open_library() as lib:
        _add_movie_filter_shortcuts(lib, params.get("order", "added"))
    add_dir(_(30107), "movies", "DefaultMovies.png", order="title")
    add_dir(_(30104), "movies", "DefaultRecentlyAddedMovies.png", order="added")
    add_dir(_(30108), "movies", "DefaultMovieTitle.png", order="rating")
    add_dir(_(30109), "movie_genres", "DefaultGenre.png")
    add_dir(_(30110), "movie_decades", "DefaultYear.png")
    add_dir(_(30111), "movie_countries", "DefaultCountry.png")
    add_dir(_(30112), "movie_ratings", "DefaultMovieTitle.png")
    end()


def _add_movie_filter_shortcuts(lib, order):
    add_dir(_(30427), "movie_filter_create", "DefaultGenre.png", order=order)
    for entry in lib.movie_filters():
        context = [_run_action(30441, "movie_filter_delete", filter_id=entry["id"])]
        add_dir(entry["name"], "movies", "DefaultFavourites.png", context=context,
                filter_id=entry["id"], order=order)


def _movie_filter_params(params):
    return {field: params.get(field, "") if params.get(field) in allowed else ""
            for field, allowed in MOVIE_FILTER_OPTIONS.items()}


def _filter_versions(videos, criteria, watched):
    is_watched = any(video["id"] in watched for video in videos)
    if criteria["status"] and is_watched != (criteria["status"] == "watched"):
        return []
    language = criteria["language"]
    if language.endswith("_SUB"):
        match_languages = {language[:2] + " titulky"}
    else:
        match_languages = {language, language + " dabing"}
    quality = criteria["quality"]
    return [video for video in videos
            if (not language or any(value.strip() in match_languages for value in (video["lang"] or "").split(",")))
            and (not quality or provider.quality_token(video["quality"]) == quality)]


def movie_filter_create(params):
    criteria = {}
    for field, heading in (("language", 30331), ("quality", 30332), ("status", 30432)):
        options = MOVIE_FILTER_OPTIONS[field]
        index = select(_(heading), [_(label) for label in options.values()], preselect=0)
        if index < 0:
            _fail_directory()
            return
        criteria[field] = list(options)[index]
    # Nová adresa dovolí vrátit se z výsledků bez opětovného otevírání dialogů.
    _fail_directory()
    xbmc.executebuiltin("Container.Update(%s)" % url_for(
        "movies", filtered=1, order=params.get("order", "added"), **criteria))


def movie_filter_save(params):
    criteria = _movie_filter_params(params)
    if not any(criteria.values()):
        return
    name = keyboard(_(30440))
    if not name:
        return
    with open_library() as lib:
        saved = lib.save_movie_filter(name, **criteria)
    notify(_(30442 if saved else 30443))


def movie_filter_delete(params):
    with open_library() as lib:
        item = lib.get_movie_filter(params["filter_id"])
    if item and yesno(_(30444) % item["name"]):
        with open_library() as lib:
            lib.delete_movie_filter(item["id"])
        xbmc.executebuiltin("Container.Refresh")


def movies(params):
    order = params.get("order", "title")
    with open_library() as lib:
        saved = lib.get_movie_filter(params["filter_id"]) if "filter_id" in params else None
        if "filter_id" in params and not saved:
            end("movies", MOVIE_SORTS, cache=False)
            return
        criteria = _movie_filter_params(saved or params)
        if params.get("browse") == "1" and _first_page(params):
            _add_movie_filter_shortcuts(lib, order)
            _add_origin_shortcuts("movies", order="title")
        if (params.get("filtered") == "1" and not saved and any(criteria.values())
                and _first_page(params)):
            add_action(_(30439), "movie_filter_save", "DefaultFavourites.png", **criteria)
        filters = {"genre": params.get("genre"), "country": params.get("country"),
                   "year_from": int(params["year_from"]) if params.get("year_from") else None,
                   "year_to": int(params["year_to"]) if params.get("year_to") else None,
                   "min_rating": int(params["min_rating"]) if params.get("min_rating") else None}
        origin = FILM_ORIGINS.get(params.get("origin"))
        size = _list_page_size()
        if size and not origin and not any(criteria.values()):
            # Bez filtrů v Pythonu stačí z databáze načíst jen aktuální stránku.
            total = lib.count_movies(**filters)
            pages = max(1, (total + size - 1) // size)
            try:
                page = min(max(1, int(params.get("page") or 1)), pages)
            except ValueError:
                page = 1
            items = lib.movies(order=order, limit=size, offset=(page - 1) * size, **filters)
            _add_movies(lib, items)
            _add_next_page("movies", params, (page, pages))
            end("movies", MOVIE_SORTS, cache=False)
            return
        items = lib.movies(order=order, **filters)
        if origin:
            items = [movie for movie in items if any(normalize(country) in origin[1]
                                                     for country in movie["countries"])]
            if not items:
                notify(_(30498))
        paging = None
        if any(criteria.values()):
            grouped = lib.movie_filter_versions()
            watched = lib.watched()
            matches = []
            for movie in items:
                videos = grouped.get(movie["key"], [])
                variants = _filter_versions(videos, criteria, watched)
                if variants:
                    matches.append((movie, videos, variants))
            if not matches:
                notify(_(30121))
            matches, paging = _paginate(matches, params)
            for movie, videos, variants in matches:
                add_movie(movie, lib, matching=(videos, variants))
        else:
            items, paging = _paginate(items, params)
            _add_movies(lib, items)
        _add_next_page("movies", params, paging)
    end("movies", MOVIE_SORTS, cache=False)


def movie_genres(params):
    kind = params.get("kind", "movie")
    with open_library() as lib:
        genres = lib.genres(kind)
    for g, count in genres:
        if kind == "movie":
            add_dir("%s (%d)" % (g, count), "movies", "DefaultGenre.png", genre=g, order="title")
        else:
            add_dir("%s (%d)" % (g, count), "shows", "DefaultGenre.png", genre=g, order="title")
    end()


def movie_decades(params):
    with open_library() as lib:
        decades = lib.decades("movie")
    for d, count in decades:
        add_dir("%d–%d (%d)" % (d, d + 9, count), "movies", "DefaultYear.png", year_from=d,
                year_to=d + 9, order="year")
    end()


def movie_countries(params):
    with open_library() as lib:
        countries = lib.countries()
    _add_origin_shortcuts("movies", order="title")
    if make_tmdb().enabled:
        add_dir(_(30526), "tmdb_filters", "DefaultCountry.png", kind="movie", filter_by="origin")
    for c, count in countries:
        add_dir("%s (%d)" % (c, count), "movies", "DefaultCountry.png", country=c, order="title")
    if not countries:
        notify(_(30527))
        add_action(_(30203), "enrich", "DefaultAddonInfo.png")
    end(cache=False)


def movie_ratings(params):
    kind = params.get("kind", "movie")
    for r in (90, 80, 70, 60, 50):
        add_dir(_(30135) % r, "movies" if kind == "movie" else "shows", "DefaultMovieTitle.png",
                min_rating=r, order="rating")
    end()


def versions(params):
    with open_library() as lib:
        meta = lib.get_meta(params["key"])
        videos = lib.videos_for_meta(params["key"])
    ordered = sorted(videos, key=_version_score)
    watched = any(video["id"] in _playback_state()["watched"] for video in videos)
    labels = [_version_label(video) or video["raw_title"] for video in ordered]
    counts = Counter(labels)
    for video, label in zip(ordered, labels):
        if counts[label] > 1 and label != video["raw_title"]:
            label += " · " + video["raw_title"]
        add_video(video, meta, label=label + " · " + _(30397), mediatype="movie", choose_stream=True,
                  watched=watched)
    end("movies", cache=False)


def shows_menu(params):
    add_dir(_(30113), "shows", "DefaultTVShows.png", order="title")
    add_dir(_(30104), "shows", "DefaultRecentlyAddedEpisodes.png", order="added")
    add_dir(_(30108), "shows", "DefaultTVShowTitle.png", order="rating")
    add_dir(_(30109), "movie_genres", "DefaultGenre.png", kind="show")
    add_dir(_(30112), "movie_ratings", "DefaultTVShowTitle.png", kind="show")
    end()


def shows(params):
    with open_library() as lib:
        items = lib.shows(genre=params.get("genre"),
                          min_rating=int(params["min_rating"]) if params.get("min_rating") else None,
                          order=params.get("order", "title"))
        counts = lib.unread_episode_counts()
    items, paging = _paginate(items, params)
    for m in items:
        add_show(m, new_count=counts.get(m["key"], 0))
    _add_next_page("shows", params, paging)
    end("tvshows", MOVIE_SORTS, cache=False)


def _season_label(season):
    return _(30115) if not season else _(30114) % season


def _season_summary(videos, watched):
    available = sorted({v["episode"] for v in videos if v["episode"] > 0})
    completed = {v["episode"] for v in videos if v["episode"] > 0 and v["id"] in watched}
    text = _(30378) % (len(available), len(completed))
    gaps = (number for previous, current in zip([0] + available, available)
            for number in range(previous + 1, current))
    missing = list(islice(gaps, 13))
    if missing:
        numbers = ", ".join(str(number) for number in missing[:12])
        text += "\n" + _(30379) % (numbers + ("…" if len(missing) > 12 else ""))
    return text


def show(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key)
        watched = lib.watched(v["id"] for v in videos)
        batch = lib.visit_episodes(key) if meta else (0, 0, 0, False)
    if meta is None:
        end()
        return
    after, through, count, unread = batch
    if count:
        add_dir(_(30433 if unread else 30434) % count, "new_episodes", "DefaultRecentlyAddedEpisodes.png",
                key=key, after=after, through=through)
    seasons = {}
    for video in videos:
        seasons.setdefault(video["season"], []).append(video)
    for number, entries in seasons.items():
        title = _season_label(number)
        summary = _season_summary(entries, watched)
        li = _list_item("%s · %s" % (title, summary.split("\n")[0]))
        info = _meta_info(meta, "season")
        info.update({"title": title, "season": number, "tvshowtitle": _display_title(meta),
                     "plot": _plot(meta, [summary])})
        _set_info(li, info)
        _art(li, meta.get("poster"), "", meta.get("fanart"))
        context = _meta_context(meta, season=number)
        episode_keys = {video["episode"] or video["id"] for video in entries}
        completed_keys = {video["episode"] or video["id"] for video in entries if video["id"] in watched}
        complete = episode_keys == completed_keys
        context.insert(1, _watch_action(complete, label=30412, key=key, season=number))
        li.addContextMenuItems(context)
        _add_item(url_for("season", key=key, season=number), li, True)
    end("seasons", cache=False)


def _episode_metadata_key(meta, season):
    tmdb_id = (meta or {}).get("tmdb_id")
    if tmdb_id and tmdb_id > 0 and season >= 0:
        return tmdb_id, season
    return None


def _cached_episode_metadata(keys, max_fetches):
    result = {}
    pending = []
    now = int(time.time())
    keys = tuple(dict.fromkeys(key for key in keys if key))
    if not keys:
        return result
    with open_library() as lib:
        for key in keys:
            cache_key = "tmdb:season:%d:%d" % key
            try:
                cache = json.loads(lib.get_kv(cache_key) or "{}")
            except ValueError:
                cache = {}
            if not isinstance(cache, dict):
                cache = {}
            cached_episodes = cache.get("episodes")
            result[key] = cached_episodes if isinstance(cached_episodes, dict) else {}
            refresh_at = cache.get("refresh_at", 0)
            if not isinstance(refresh_at, (int, float)) or refresh_at <= now:
                pending.append((key, cache_key))
        if pending and max_fetches:
            client = make_tmdb()
            if client.enabled:
                for key, cache_key in pending[:max_fetches]:
                    refresh_at = now + EPISODE_CACHE_TTL
                    try:
                        result[key] = client.season_episodes(*key)
                    except TmdbError:
                        log("tmdb: metadata pro seriál %d, sérii %d nejsou dostupná" % key)
                        refresh_at = now + EPISODE_CACHE_RETRY
                    lib.set_kv(cache_key, json.dumps({"episodes": result[key], "refresh_at": refresh_at}))
    return result


def _episode_detail_action(key, season, episode):
    return _run_action(30424, "episode_detail", key=key, season=season, episode=episode)


def _episode_label(video, data):
    number = video["episode"]
    if not number:
        return video["raw_title"]
    label = "S%02dE%02d" % (video["season"], number)
    title = data.get("title")
    if title:
        label += " · " + title
    duration = data.get("duration") or video.get("duration")
    if duration:
        label += " · " + _(30426) % max(1, round(duration / 60))
    return label


def _add_episode_groups(meta, videos, watched, episode_data):
    groups = {}
    for video in videos:
        groups.setdefault((video["season"], video["episode"] or video["id"]), []).append(video)
    key = meta["key"]
    for group in groups.values():
        video = min(group, key=_version_score)
        number, episode = video["season"], video["episode"]
        season_key = _episode_metadata_key(meta, number)
        data = episode_data.get(season_key, {}).get(str(episode), {})
        label = _episode_label(video, data)
        complete = any(version["id"] in watched for version in group)
        if len(group) == 1:
            add_video(video, meta, label=label, mediatype="episode", episode_data=data)
            continue
        li = _list_item("%s · %s" % (label, _(30133) % len(group)))
        info = _meta_info(meta, "episode")
        info.update(title=label, season=number, episode=episode, tvshowtitle=_display_title(meta),
                    duration=data.get("duration") or video.get("duration") or 0)
        _set_info(li, info)
        _art(li, meta.get("poster"), video["thumb"], meta.get("fanart"), data.get("still"))
        if complete:
            _apply_playback(li, video["id"], watched=True)
        versions_url = url_for("season", key=key, season=number, episode=episode)
        context = [(_(30395), "Container.Update(%s)" % versions_url),
                   _run_action(30128, "collection_add", target="v:" + video["id"]),
                   _watch_action(complete, key=key, season=number, episode=episode),
                   _run_action(30409, "manage_item", key=key, season=number, episode=episode)]
        if season_key and episode:
            context.insert(-1, _episode_detail_action(key, number, episode))
        li.addContextMenuItems(context)
        _add_item(versions_url, li, True)


def season(params):
    key = params["key"]
    number = int(params.get("season") or 0)
    requested = int(params.get("episode") or 0)
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key, season=number)
        watched = lib.watched(v["id"] for v in videos)
    if not meta or not videos:
        end("episodes", cache=False)
        return
    season_key = _episode_metadata_key(meta, number)
    metadata = _cached_episode_metadata([season_key], max_fetches=1)
    if requested:
        group = [video for video in videos if video["episode"] == requested]
        complete = any(video["id"] in watched for video in group)
        data = metadata.get(season_key, {}).get(str(requested), {})
        for video in sorted(group, key=_version_score):
            label = _episode_label(video, data)
            version = _version_label(video) or video["raw_title"]
            add_video(video, meta, label=label + " · " + version, mediatype="episode",
                      choose_stream=True, watched=complete, episode_data=data)
    else:
        summary = _season_summary(videos, watched)
        add_action(summary.replace("\n", " · "), "season_status", key=key, season=number)
        _add_episode_groups(meta, videos, watched, metadata)
    end("episodes", cache=False)


def episode_detail(params):
    key = params["key"]
    number = int(params["season"])
    episode = int(params["episode"])
    with open_library() as lib:
        meta = lib.get_meta(key)
    season_key = _episode_metadata_key(meta, number)
    data = _cached_episode_metadata([season_key], max_fetches=1).get(season_key, {}).get(str(episode), {})
    if data.get("plot"):
        text_viewer(data.get("title") or "S%02dE%02d" % (number, episode), data["plot"])
    else:
        notify(_(30425))


def new_episodes(params):
    key = params["key"]
    after = int(params["after"])
    through = int(params["through"])
    with open_library() as lib:
        meta = lib.get_meta(key)
        positions = lib.new_episode_positions(key, after, through)
        videos = [v for v in lib.videos_for_meta(key) if (v["season"], v["episode"]) in positions]
        watched = lib.watched(v["id"] for v in videos)
    if meta and videos:
        keys = (_episode_metadata_key(meta, v["season"]) for v in videos)
        _add_episode_groups(meta, videos, watched, _cached_episode_metadata(keys, max_fetches=2))
    else:
        notify(_(30435))
    end("episodes", cache=False)


def season_status(params):
    with open_library() as lib:
        videos = lib.videos_for_meta(params["key"], season=int(params.get("season") or 0))
        watched = lib.watched(v["id"] for v in videos)
    text_viewer(_season_label(int(params.get("season") or 0)), _season_summary(videos, watched))


def recent(params):
    _video_list(lambda lib: lib.recent_videos(limit=100),
                [xbmcplugin.SORT_METHOD_DATEADDED, xbmcplugin.SORT_METHOD_LABEL])


def all_videos(params):
    """Všechna videa z účtu v jednom seznamu (filmy i díly), řaditelná."""
    _video_list(lambda lib: lib.all_videos(),
                [xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE, xbmcplugin.SORT_METHOD_DATEADDED,
                 xbmcplugin.SORT_METHOD_VIDEO_YEAR, xbmcplugin.SORT_METHOD_VIDEO_RATING],
                paging=("all_videos", params))


def _episode_position(video):
    season, episode = video["season"], video["episode"]
    return (season, episode) if episode > 0 else None


def _series_progress(lib, history, resume, watched):
    latest = history[0]
    videos = lib.videos_for_meta(latest["meta_key"])
    completed = {_episode_position(video) for video in videos if video["id"] in watched}
    completed.discard(None)
    position = _episode_position(latest)
    if latest["id"] in resume and position not in completed:
        return latest
    if position is None:
        return next((video for video in history if video["id"] in resume
                     and _episode_position(video) not in completed), None)

    following = _next_episode(videos, position, completed, resume)
    if following:
        partial = next((video for video in history if video["id"] == following["id"] and video["id"] in resume),
                       None)
        return partial or dict(following, next_episode=True, last_played=latest["last_played"])
    return None


def _next_episode(videos, position, completed, resume):
    following = []
    for video in videos:
        candidate = _episode_position(video)
        if candidate is None or candidate <= position or candidate in completed:
            continue
        if following and candidate != _episode_position(following[0]):
            break  # videos_for_meta() řadí podle série a dílu
        following.append(video)
    return min(following, key=lambda video: (video["id"] not in resume, _version_score(video)), default=None)


def offer_next_episode(vid, player):
    with open_library() as lib:
        current = lib.find_video(vid)
        if not current or current["kind"] != "episode" or not _episode_position(current):
            return
        videos = lib.videos_for_meta(current["meta_key"])
        watched = lib.watched()
        resume = lib.resume_points()
        completed = {_episode_position(video) for video in videos if video["id"] in watched}
        following = _next_episode(videos, _episode_position(current), completed, resume)
        meta = lib.get_meta(current["meta_key"]) or {}
    if following:
        episode = "%s %dx%02d" % (_display_title(meta) or following["title"], *_episode_position(following))
        _offer_playback(player, _(30418), episode, following["id"], following["id"] in resume)


def _offer_playback(player, heading, title, vid, resume):
    window = xbmcgui.Window(10000)
    if player.isPlaying() or window.getProperty(PLAYING_PROPERTY):
        return
    if xbmcgui.Dialog().yesno(heading, title, yeslabel=_(30419), autoclose=15000):
        if not player.isPlaying() and not window.getProperty(PLAYING_PROPERTY):
            mode = "resume" if resume else "noresume"
            url = url_for("play", vid=vid, continue_play=1 if resume else None)
            xbmc.executebuiltin("PlayMedia(%s,%s)" % (url, mode))


def in_progress(params):
    _import_kodi_history()
    with open_library() as lib:
        resume = lib.resume_points()
        watched = lib.watched()
        entries = []
        series = {}
        movies = set()
        hidden = {meta["key"] for meta in lib.hidden_progress()}
        for video in lib.progress_history():
            if video["meta_key"] in hidden:
                continue
            vid = video["id"]
            if video["kind"] == "episode" and video["meta_key"]:
                if vid in resume or vid in watched:
                    series.setdefault(video["meta_key"], []).append(video)
            elif vid in resume:
                key = video["meta_key"] or vid
                if key not in movies:
                    entries.append(video)
                    movies.add(key)
        for history in series.values():
            video = _series_progress(lib, history, resume, watched)
            if video:
                entries.append(video)
        entries.sort(key=lambda video: video["last_played"], reverse=True)
        entries = entries[:100]
        found = lib.get_metas(v["meta_key"] for v in entries)
        metas = {v["meta_key"]: found.get(v["meta_key"]) or {} for v in entries}

    keys = (_episode_metadata_key(metas[v["meta_key"]], v["season"]) for v in entries
            if v["kind"] == "episode" and v["episode"])
    metadata = _cached_episode_metadata(keys, max_fetches=2)
    for video in entries:
        meta = metas[video["meta_key"]]
        title = _display_title(meta) or video["title"] or video["raw_title"]
        is_episode = video["kind"] == "episode"
        episode_data = {}
        if is_episode:
            season_key = _episode_metadata_key(meta, video["season"])
            episode_data = metadata.get(season_key, {}).get(str(video["episode"]), {})
            title = "%s · %s" % (title, _episode_label(video, episode_data)) if video["episode"] else video["raw_title"]
        if video.get("next_episode"):
            label = _(30365) % title
            progress = ""
        else:
            position, total = video["position"], video["total"]
            progress = _(30366) % (int(100 * position / total), max(1, int((total - position + 59) // 60)))
            label = "%s · %s" % (title, progress)
        add_video(video, meta, label=label, mediatype="episode" if is_episode else "movie",
                  progress=progress, progress_key=video["meta_key"], episode_data=episode_data)
    if hidden:
        add_dir(_(30414), "progress_hidden", "DefaultVideoDeleted.png")
    elif not entries:
        notify(_(30367))
    end("videos", cache=False)


def progress_hidden(params):
    with open_library() as lib:
        items = lib.hidden_progress()
    for item in items:
        add_action(_(30415) % _display_title(item), "progress_visibility", key=item["key"], hide=0)
    end(cache=False)


def progress_visibility(params):
    with open_library() as lib:
        lib.set_progress_hidden(params["key"], params.get("hide") == "1")
    xbmc.executebuiltin("Container.Refresh")


def _item_videos(lib, params):
    season = int(params["season"]) if "season" in params else None
    episode = int(params["episode"]) if "episode" in params else None
    videos = lib.videos_for_meta(params["key"], season=season)
    return [video for video in videos if episode is None or video["episode"] == episode]


def mark_watched(params):
    watched = params.get("watched") == "1"
    if "season" in params and "episode" not in params and watched and not yesno(_(30416)):
        return
    with open_library() as lib:
        if "vid" in params:
            video = lib.find_video(params["vid"])
            if not video:
                return
            videos = [video]
            if video["kind"] == "movie":
                videos += lib.videos_for_meta(video["meta_key"])
            elif video["episode"]:
                videos += _item_videos(lib, {"key": video["meta_key"], "season": video["season"],
                                             "episode": video["episode"]})
        else:
            videos = _item_videos(lib, params)
        lib.set_watched(dict.fromkeys(video["id"] for video in videos), watched)
    _PLAYBACK.clear()
    if videos:
        publish_kodi_history({v["id"] for v in videos}, mark=watched)
    xbmc.executebuiltin("Container.Refresh")


def manage_item(params):
    with open_library() as lib:
        if "vid" in params:
            video = lib.find_video(params["vid"])
            videos = [video] if video else []
            key = video["meta_key"] if video else ""
            has_history = bool(video and lib.has_play_history(video["id"]))
        else:
            videos = _item_videos(lib, params)
            key = params["key"]
            has_history = False
        meta = lib.get_meta(key) if key else None
    options = []
    if meta:
        options.extend([(30373, tmdb_repair), (30300, meta_assign), (30301, meta_refresh)])
    metadata_options = len(options)
    if videos:
        options.extend([(30325, video_info), (30302, video_edit),
                        (30304 if all(video["hidden"] for video in videos) else 30303, video_hide),
                        (30305, video_delete)])
        if has_history:
            options.append((30138, history_remove))
    if not options:
        return
    selection = select(_(30409), [_(label) for label, _handler in options])
    if selection < 0:
        return
    handler = options[selection][1]
    if selection < metadata_options:
        handler({"key": key})
        return
    index = 0
    if len(videos) > 1:
        index = select(_(30409), [video["raw_title"] for video in videos])
    if index >= 0:
        video = videos[index]
        handler({"vid": video["id"], "hide": str(int(not video["hidden"]))})


def played(params):
    _video_list(lambda lib: lib.history_videos(), [xbmcplugin.SORT_METHOD_UNSORTED])


def history_remove(params):
    with open_library() as lib:
        lib.remove_history(params.get("vid"))
    xbmc.executebuiltin("Container.Refresh")


def history_clear(params):
    if yesno(_(30238)):
        with open_library() as lib:
            lib.remove_history()


def diagnostics(params):
    """Uloží stránky účtu (bez přihlašovacích údajů) pro ladění parseru."""
    if not _require_credentials():
        return
    folder = xbmcgui.Dialog().browse(3, _(30235), "files")
    if not folder:
        return
    try:
        pages = provider.diagnostics(make_client())
    except provider.ProviderError as e:
        ok(_(30328) % e)
        return
    secrets = [setting("username"), setting("password")]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sep = "\\" if "\\" in folder and "/" not in folder else "/"
    base = folder.rstrip("/\\") + sep + "diagnostika-%s-%s" % (ADDON_ID, stamp)
    xbmcvfs.mkdirs(base)
    for name, content in pages.items():
        f = xbmcvfs.File(base + sep + name, "w")
        try:
            f.write(catalog.sanitize(content, secrets))
        finally:
            f.close()
    ok(_(30236) % base)


def _video_list(load, sorts, paging=None):
    page = None
    with open_library() as lib:
        videos = load(lib)
        if paging:
            videos, page = _paginate(videos, paging[1])
        metas = lib.get_metas(v["meta_key"] for v in videos)
    for v in videos:
        meta = metas.get(v["meta_key"]) or {}
        if v["kind"] == "episode":
            label = "%s %dx%02d" % (_display_title(meta) or v["title"], v["season"], v["episode"])
        else:
            year = _display_year(meta) if meta else v["year"]
            label = (_display_title(meta) or v["title"]) + (" (%d)" % year if year else "")
        add_video(v, meta, label=label, mediatype="episode" if v["kind"] == "episode" else "movie")
    if paging:
        _add_next_page(paging[0], paging[1], page)
    end("videos", sorts)


def unmatched(params):
    add_action(_(30445), "batch_episodes", "DefaultTVShows.png")
    with open_library() as lib:
        items = lib.unmatched()
        for m in items:
            if m["kind"] == "show":
                add_show(m, repair=True)
            else:
                add_movie(m, lib, repair=True)
    add_dir("[I]%s[/I]" % _(30131), "hidden", "DefaultVideoDeleted.png")
    end("videos")


def hidden(params):
    with open_library() as lib:
        videos = lib.hidden_videos()
    for v in videos:
        add_video(v)
    end("videos")


# --- hledání ---------------------------------------------------------------

def search_menu(params):
    add_dir("[B]%s[/B]" % _(30117), "search", "DefaultAddonsSearch.png")
    add_dir(_(30111), "movie_countries", "DefaultCountry.png")
    with open_library() as lib:
        history = lib.search_history()
    for q in history:
        add_dir(q, "search", "DefaultAddonsSearch.png",
                context=[(_(30122), "RunPlugin(%s)" % url_for("search_delete", q=q))], q=q)
    end(cache=False)


def _search_cards(query, include_online, filters=None, *, retry=False, reuse=False):
    filters = filters or _online_filters({})
    title, year, position = search_results.split_query(query)
    with open_library() as lib:
        movies, shows = lib.search(title)
        if year and query != title:
            literal_movies, literal_shows = lib.search(query)
            movies = list({item["key"]: item for item in literal_movies + movies}.values())
            shows = list({item["key"]: item for item in literal_shows + shows}.values())
        grouped = lib.videos_grouped((meta["key"] for meta in (shows if position else movies)),
                                     season=position[0] if position else None)
        candidates = []
        for meta in ([] if position else movies) + shows:
            kind = "movie" if meta["kind"] == "movie" else "tv"
            videos = grouped.get(meta["key"], [])
            if position:
                videos = [v for v in videos if v["episode"] == position[1] and (not year or v["year"] in (0, year))]
            candidates.append(search_results.new_card(
                kind, _display_title(meta), _display_year(meta), position,
                (meta.get("orig_title"), meta.get("search_title")), meta, videos))
    online = _online_search(query, title, year, position, filters, retry=retry, reuse=reuse) if include_online else {}
    videos = _filter_online_videos(online.get("provider", []), filters, (title,))
    if position:
        series = search_results.matching_title(online.get("tmdb", []), title, year, episodic=True) or {}
        names = search_results.filename_names((title, series.get("original_name"), *series.get("_search_aliases", ())))
        videos = [video for video in videos if search_results.filename_matches(parse(video["title"]), names, year,
                                                                               position, kind="episode")]
    cards = search_results.combine(candidates, online.get("tmdb", []), videos,
                                   position, provider.video_key)
    cards.sort(key=lambda card: (
        bool(position and not card["videos"] and not card["provider"]),
        search_results.compare_title(title, card["names"]),
        0 if not year or card["year"] == year else (2 if card["year"] else 1),
        not bool(card["local"]),
        not bool(card["provider"]),
        normalize(card["title"])))
    return cards, online


ONLINE_SEARCH_KEEP = 20
ONLINE_SEARCH_TTL = 600
ONLINE_SEARCH_ERROR_TTL = 20


def _online_cache_key(account, query):
    return "search:online:" + sha256(("%s\n%s" % (account, query)).encode("utf-8")).hexdigest()


def _online_search(query, title, year, position, filters, *, retry=False, reuse=False):
    account = _account_scope()
    cache_key = _online_cache_key(account, query)
    with open_library() as lib:
        cached = lib.cache_get(cache_key)
    now = time.time()
    if cached and not retry:
        try:
            payload = cached
            ttl = ONLINE_SEARCH_ERROR_TTL if payload.get("errors") else ONLINE_SEARCH_TTL
            same_filters = payload.get("filters") == filters
            if (payload.get("account") == account and payload["query"] == query
                    and (same_filters or (reuse and not payload.get("errors"))) and 0 <= now - payload["created"] < ttl):
                if not same_filters:
                    payload["filters"] = filters
                    with open_library() as lib:
                        lib.cache_put(cache_key, payload, ttl - (now - payload["created"]))
                return payload
        except (TypeError, KeyError, ValueError, AttributeError):
            pass

    client = _search_client()
    tmdb = make_tmdb()
    tmdb.timeout = 8
    payload = {"created": now, "query": query, "filters": filters, "account": account, "errors": []}
    provider_query = search_results.format_query(title, year, position) if position else query
    with ThreadPoolExecutor(max_workers=2) as pool:
        provider_task = pool.submit(_provider_page_batch, client, provider_query)
        tmdb_task = pool.submit(_tmdb_page_batch, lambda page: tmdb.search_titles(title, page)) if tmdb.enabled else None
        try:
            videos, _page, _cursor, partial = provider_task.result()
            payload["provider"] = videos
            if partial:
                payload["errors"].append("Přehraj.to")
        except provider.LoginError:
            payload["provider_login_failed"] = True
            payload["errors"].append("Přehraj.to")
        except (provider.ProviderError, provider.RateLimited):
            payload["errors"].append("Přehraj.to")
        if tmdb_task:
            try:
                titles, _next, partial = tmdb_task.result()
                payload["tmdb"] = titles
                if partial:
                    payload["errors"].append("TMDB")
            except TmdbError:
                payload["errors"].append("TMDB")
    if "provider" in payload and "Přehraj.to" not in payload["errors"]:
        match = search_results.matching_title(payload.get("tmdb", []), title, year, bool(position))
        payload["provider"], partial, alias_error = _expand_provider_search(
            client, payload["provider"], provider_query, title, year, position, filters, tmdb, match)
        if partial:
            payload["errors"].append("Přehraj.to")
        if alias_error and "TMDB" not in payload["errors"]:
            payload["errors"].append("TMDB")
    with open_library() as lib:
        lib.cache_put(cache_key, payload, ONLINE_SEARCH_ERROR_TTL if payload["errors"] else ONLINE_SEARCH_TTL)
        lib.cache_trim("search:online:", ONLINE_SEARCH_KEEP)
    return payload


def _main_search_action(card):
    resume = _playback_state()["resume"]
    if card["videos"]:
        preferred = min(card["videos"], key=lambda v: (v["id"] not in resume, _version_score(v)))
        has_resume = preferred["id"] in resume
        return (30466 if has_resume else 30465), "play", {"vid": preferred["id"]}, preferred
    if card["kind"] == "tv" and not card["position"]:
        if card["local"]:
            return 30364, "show", {"key": card["local"]["key"]}, None
        if card["provider"] or card["tmdb"]:
            tmdb = card["tmdb"] or {}
            return 30486, "provider_search", {"q": card["title"], "series": 1, "title": card["title"],
                                               "original": tmdb.get("original_name"), "year": card["year"],
                                               "tmdb_id": tmdb.get("id")}, None
    if card["provider"]:
        videos = [_provider_version(video, parse(video["title"])) for video in card["provider"]]
        preferred = min(videos, key=lambda v: (v["id"] not in resume, _version_score(v)))
        return (30466 if preferred["id"] in resume else 30465), "play", {"vid": preferred["id"]}, None
    if card["local"]:
        return 30364, "show", {"key": card["local"]["key"]}, None
    return 30391, "tmdb_title", {"kind": card["kind"], "tmdb_id": card["tmdb"]["id"]}, None


def _search_entry(card, label, action, args, video, context=(), direct_resume=False):
    li = _list_item(label)
    meta = card["local"] or {}
    poster_path = (card["tmdb"] or {}).get("poster_path")
    poster = meta.get("poster") or (IMG + "w500" + poster_path if poster_path else "")
    thumb = meta.get("thumb") or next((v.get("thumb") for v in card["provider"] if v.get("thumb")), "")
    _art(li, poster, thumb, meta.get("fanart"))
    _set_info(li, {"title": label, "year": card["year"],
                   "mediatype": "episode" if card["position"] else
                   ("movie" if card["kind"] == "movie" else "tvshow")})
    items = list(context)
    if action == "play":
        vid = args["vid"]
        li.setProperty("IsPlayable", "true")
        if video:
            offer = _apply_version_playback(li, video, items)
            args = dict(args, offer_transfer=offer)
        else:
            _apply_playback(li, vid)
        if direct_resume and vid in _playback_state()["resume"]:
            args = dict(args, continue_play=1)
    if items:
        li.addContextMenuItems(items)
    _add_item(url_for(action, **args), li, action != "play")


def _search_label(card, requested_year=0):
    title = card["title"]
    if card["position"]:
        title += " S%02dE%02d" % card["position"]
    if card["year"]:
        title += " (%d)" % card["year"]
    elif requested_year:
        title += " · " + _(30543)
    return title


def _country_query(query, lib):
    name = normalize(query)
    if name in ("ceskoslovenske filmy", "ceske a slovenske filmy", "ceskoslovensko"):
        return "CZSK", FILM_ORIGINS["CZSK"][1]
    words = name.split()
    if words and words[-1] in ("filmy", "film"):
        name = " ".join(words[:-1])
    explicit = name.startswith(("filmy z ", "zeme ", "stat "))
    if explicit:
        name = name.split(" ", 2)[2] if name.startswith("filmy z ") else name.split(" ", 1)[1]
    for code, aliases in SEARCH_COUNTRIES.items():
        known = aliases | FILM_ORIGINS.get(code, (0, set()))[1]
        if name in known or (explicit and name == code.lower()) or query.strip() == code:
            return code, known | {code.lower()}
    if len(name) <= 2 and not explicit and query != query.upper():
        return "", set()
    country = next((value for value, _count in lib.countries() if normalize(value) == name), "")
    return "", {name} if country else set()


def _add_people(people):
    for person in people:
        add_dir(person["name"], "person_titles", "DefaultActor.png", person_id=person["id"])


def search_people(params):
    query = params.get("q", "").strip()
    client = make_tmdb()
    if not client.enabled:
        notify(_(30355))
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        people, next_page, partial = _tmdb_page_batch(lambda page: client.search_people(query, page),
                                                       max(1, int(params.get("page", 1))))
    except (TmdbError, ValueError) as error:
        notify(str(error))
        end(cache=False)
        return
    _add_people(people)
    if next_page:
        add_dir(_(30341), "search_people", q=query, page=next_page)
    if partial:
        notify(_(30497))
    elif not people:
        notify(_(30524))
    end(cache=False)


def person_titles(params):
    client = make_tmdb()
    if not client.enabled:
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        person_id = int(params.get("person_id", 0))
        page = max(1, int(params.get("page", 1)))
        credits = client.acting_credits(person_id)
    except (TmdbError, ValueError) as error:
        notify(str(error))
        end(cache=False)
        return
    start = (page - 1) * ACTOR_PAGE_SIZE
    titles = credits[start:start + ACTOR_PAGE_SIZE]
    found_only = params.get("found_only") == "1"
    args = {"person_id": person_id, "page": page, "found_only": "1" if found_only else ""}
    found = _availability_controls("person_titles", args, titles, "movie") if titles else set()
    with open_library() as lib:
        lists = {"movie": lib.movies(), "tv": lib.shows()}
        for item in titles:
            kind = item["media_type"]
            verified = "%s:%s" % (kind, item["id"]) in found
            if not found_only or verified:
                add_tmdb_title(item, kind, lib, lists[kind], provider_found=verified)
    if start + ACTOR_PAGE_SIZE < len(credits):
        add_dir(_(30341), "person_titles", **dict(args, page=page + 1))
    if not titles:
        notify(_(30121))
    end("videos", cache=False)


def _add_spelling_suggestions(title, year, position, cards, params):
    if len(normalize(title)) < 4:
        return
    names = [entry["title"] for entry in cards if (not year or entry["year"] in (year, 0))]
    with open_library() as lib:
        items = lib.shows() if position else lib.movies(year_from=year, year_to=year) + lib.shows()
    names.extend(name for entry in items for name in (_display_title(entry), entry.get("orig_title")) if name)
    for suggestion in search_results.spelling_suggestions(title, names):
        corrected = search_results.format_query(suggestion, year, position)
        add_dir(_(30532) % corrected, "search", "DefaultAddonsSearch.png", **dict(params, q=corrected))


def search(params):
    query = params.get("q", "").strip()
    from_keyboard = not query
    if from_keyboard:
        query = (keyboard(_(30118)) or "").strip()
        if not query:
            _fail_directory()
            return
    include_online = params.get("online", "0" if setting("search_online") == "false" else "1") == "1"
    discovery = search_results.discover_query(query, COUNTRY_QUERY_ALIASES)
    with open_library() as lib:
        lib.add_search(query)
        origin, countries = ("", set()) if discovery else _country_query(query, lib)
    if discovery or origin:
        tmdb_titles(dict(discovery or {"origin": origin}, kind="movie", category="discover", catalogue="1",
                         local_only="" if include_online else "1"),
                    cache=from_keyboard)
        return
    if countries:
        with open_library() as lib:
            _add_movies(lib, (movie for movie in lib.movies()
                              if any(normalize(country) in countries for country in movie["countries"])))
        end("movies", MOVIE_SORTS, cache=from_keyboard)
        return
    filters = _online_filters(params)
    cards, online = _search_cards(query, include_online, filters, retry=params.get("retry") == "1",
                                  reuse=params.get("reuse") == "1")
    errors = online.get("errors", []).copy()
    search_args = dict(filters, q=query, online=int(include_online))
    title, year, position = search_results.split_query(query)
    if include_online:
        _add_online_filters("search", search_args)
        unfiltered = online.get("provider", [])
        if not _filter_online_videos(unfiltered, filters, (title,)):
            _add_relaxed_searches("search", search_args, unfiltered, filters, (title,))

    exact_title = any(search_results.compare_title(title, card["names"]) == 0
                      and (not year or card["year"] == year)
                      and (not position or card["videos"] or card["provider"]) for card in cards)
    people = []
    if not exact_title:
        client = make_tmdb()
        if not year and not position and client.enabled:
            client.timeout = 5
            try:
                people, _pages = client.search_people(title)
            except TmdbError:
                if "TMDB" not in errors:
                    errors.append("TMDB")
        _add_people(people)
        _add_spelling_suggestions(title, year, position, cards, search_args)
    for card in cards:
        name = _search_label(card, year)
        label, action, entry_args, video = _main_search_action(card)
        if action == "provider_search":
            entry_args.update(filters)
        if action == "play":
            name += " · " + _(label)
        if include_online:
            sources = ([_(30467)] if card["local"] else [])
            sources += [source for present, source in ((card["provider"], "Přehraj.to"), (card["tmdb"], "TMDB"))
                        if present]
            if sources:
                name += " · " + ", ".join(sources)
        versions = {provider.video_key(video["id"]) for video in card["videos"] + card["provider"]}
        detail_args = dict(search_args, card=search_results.card_id(card))
        detail = url_for("search_title", **detail_args)
        context = [(_(30468), "Container.Update(%s)" % detail)]
        if card["local"]:
            context.extend(_meta_context(card["local"]))
        if include_online and len(versions) > 1 and (card["kind"] == "movie" or position):
            name = _search_label(card, year) + " · " + _(30133) % len(versions) + " · " + ", ".join(sources)
            action, entry_args, video = "search_title", detail_args, None
        _search_entry(card, name, action, entry_args, video, context)
    if not cards and not people:
        notify(_(30121 if include_online else 30389))
    add_dir(_(30523), "search_people", "DefaultActor.png", q=query)
    if errors:
        notify(_(30469) % ", ".join(errors))
        if include_online:
            if online.get("provider_login_failed"):
                add_action(_(30356), "settings")
            add_dir(_(30542), "search", "DefaultAddonsSearch.png", retry=1, **search_args)
    if not include_online:
        add_dir(_(30464), "search", "DefaultAddonsSearch.png", **dict(search_args, online=1))
    else:
        add_dir(_(30340), "provider_search", "DefaultAddonsSearch.png", q=query, **filters)
        add_dir(_(30372), "tmdb_search", "DefaultAddonsSearch.png", q=title)
    end("videos", cache=from_keyboard)


def search_title(params):
    cards, _online = _search_cards(params["q"], params.get("online") == "1", _online_filters(params))
    card = next((entry for entry in cards if search_results.card_id(entry) == params["card"]), None)
    if not card:
        end("videos", cache=False)
        return
    label, action, args, video = _main_search_action(card)
    _search_entry(card, _(label), action, args, video, direct_resume=True)
    used = {provider.video_key(args["vid"])} if action == "play" else set()
    for entry in sorted(card["videos"], key=_version_score):
        identity = provider.video_key(entry["id"])
        if identity not in used:
            used.add(identity)
            _search_entry(card, _version_label(entry) or entry["raw_title"], "play", {"vid": entry["id"]}, entry)
    for entry in sorted(card["provider"], key=lambda video: _version_score(_provider_version(video, parse(video["title"])))):
        identity = provider.video_key(entry["id"])
        if identity not in used:
            used.add(identity)
            parsed = parse(entry["title"])
            entry_info = _provider_version(entry, parsed)
            label = _version_label(entry_info) or entry["title"]
            if card["kind"] == "tv" and not card["position"] and parsed["episode"]:
                label = "S%02dE%02d · %s" % (parsed["season"], parsed["episode"], label)
            add_provider_result(entry, "Přehraj.to · " + label)
    if card["tmdb"] and action != "tmdb_title":
        add_dir(_(30391), "tmdb_title", kind=card["kind"], tmdb_id=card["tmdb"]["id"])
    if card["local"] and card["kind"] == "tv" and action != "show":
        add_dir(_(30364), "show", key=card["local"]["key"])
    end("videos", cache=False)


def _provider_page_batch(client, query, page=1, cursor=""):
    videos = {}
    seen_cursors = {cursor}
    for offset in range(_result_pages()):
        try:
            entries, next_cursor = provider.search_catalog(client, query, page, cursor)
        except (provider.ProviderError, provider.RateLimited):
            if not offset:
                raise
            return list(videos.values()), page, cursor, True
        for video in entries:
            videos.setdefault(provider.video_key(video["id"]), video)
        if not next_cursor or next_cursor in seen_cursors:
            return list(videos.values()), page, "", False
        seen_cursors.add(next_cursor)
        page += 1
        cursor = next_cursor
    return list(videos.values()), page, cursor, False


def provider_search(params):
    if not hasattr(provider, "search_catalog"):
        end(cache=False)
        return
    query = params.get("q", "").strip()
    from_keyboard = not query
    if from_keyboard:
        query = (keyboard(_(30340)) or "").strip()
        if not query:
            _fail_directory()
            return
        params = dict(params, q=query)
    series = params.get("series") == "1"
    if not series:
        with open_library() as lib:
            _origin, countries = _country_query(query, lib)
        if countries or search_results.discover_query(query, COUNTRY_QUERY_ALIASES):
            search({"q": query, "online": "1"})
            return
    filters = _online_filters(params)
    title, year, position = search_results.split_query(query)
    original = params.get("original", "") if series else ""
    route_args = dict(params, q=query)
    if series:
        title = params.get("title") or title
        start, end_year = search_results.period(str(params.get("year", "")))
        year = start if start and start == end_year else year
    try:
        page = int(params.get("page", 1))
        client = _search_client()
        videos, next_page, cursor, partial = _provider_page_batch(client, query, page, params.get("cursor", ""))
    except provider.LoginError:
        notify(_(30368), time_ms=8000)
        add_action(_(30356), "settings")
        end(cache=False)
        return
    except (ValueError, provider.ProviderError, provider.RateLimited) as e:
        notify(_(30369) % e, time_ms=8000)
        add_dir(_(30542), "provider_search", "DefaultAddonsSearch.png", **params)
        end(cache=False)
        return
    match = None
    if page == 1 and not partial and not _suitable_provider_video(videos, (title, original), year, position, filters,
                                                                  require_year=not series):
        tmdb = make_tmdb()
        tmdb_id = str(params.get("tmdb_id", ""))
        if series and (original or tmdb_id.isdigit()):
            match = {"media_type": "tv", "id": int(tmdb_id) if tmdb_id.isdigit() else 0, "original_name": original,
                     "_skip_aliases": not tmdb.enabled or not tmdb_id.isdigit()}
        elif tmdb.enabled:
            try:
                candidates, _pages = tmdb.search_titles(title, 1)
                match = search_results.matching_title(candidates, title, year, series or bool(position))
            except TmdbError:
                pass
        videos, partial, _tmdb_error = _expand_provider_search(
            client, videos, query, title, year, position, filters, tmdb, match)
    titles = (title, original, *((match or {}).get("_search_aliases", ())))
    names = search_results.filename_names(titles)
    parsed = [(video, parse(video["title"])) for video in videos]
    if series or position:
        parsed = [(video, parts) for video, parts in parsed if (parts["episode"] or series)
                  and search_results.filename_matches(parts, names, year, position, kind="episode")]
    matches = [(video, parts) for video, parts in parsed if _provider_matches_filter(video, parts, filters, titles)]
    if series or position:
        matches.sort(key=lambda pair: (pair[1]["season"], pair[1]["episode"],
                                       _version_score(_provider_version(*pair))))
    else:
        matches.sort(key=lambda pair: _movie_result_key(*pair, title, original, year, titles[2:]))
    if not params.get("group"):
        _add_online_filters("provider_search", route_args)
    groups = {}
    for video, parts in matches:
        name = normalize(parts["title"])
        if name and ((parts["kind"] == "movie" and parts["year"])
                     or (parts["episode"] and (parts["year"] or series or position))):
            key = (parts["kind"], name, parts["year"], parts["season"], parts["episode"])
        else:
            key = (video["id"],)
        groups.setdefault(key, []).append((video, parts))
    selected = params.get("group")
    for group in groups.values():
        first, parts = group[0]
        if selected:
            if any(video["id"] == selected for video, _ in group):
                for video, parts in sorted(group, key=lambda pair: _version_score(_provider_version(*pair))):
                    add_provider_result(video, _version_label(_provider_version(video, parts)) or video["title"])
                end("videos", cache=False)
                return
        elif len(group) == 1:
            label = None
            if (series or position) and parts["episode"]:
                label = _episode_label(parts, {}) + " · " + (_version_label(_provider_version(first, parts)) or first["title"])
                if year and not parts["year"]:
                    label += " · " + _(30543)
            add_provider_result(first, label)
        else:
            label = parts["title"]
            if parts["kind"] == "episode":
                label += " · " + _episode_label(parts, {})
            if parts["year"]:
                label += " (%d)" % parts["year"]
            add_dir("%s · %s" % (label, _(30133) % len(group)), "provider_search", first["thumb"],
                    **dict(route_args, page=page, group=first["id"]))
    if selected:
        notify(_(30370))
        end("videos", cache=False)
        return
    if partial:
        notify(_(30497))
        add_dir(_(30542), "provider_search", "DefaultAddonsSearch.png", **params)
    if cursor:
        add_dir(_(30341), "provider_search", **dict(route_args, page=next_page, cursor=cursor))
    if not matches and not cursor:
        notify(_(30370))
    if not matches:
        _add_relaxed_searches("provider_search", route_args, [video for video, _parts in parsed], filters, titles)
    end("videos", cache=from_keyboard)


def _provider_version(video, parsed):
    return {**video, "lang": parsed["lang"], "quality": video.get("quality") or parsed["quality"]}


def add_provider_result(video, label=None):
    label = label or video["title"]
    language = _provider_version(video, parse(video["title"]))["lang"]
    if not any(part.strip() and not part.strip().endswith(" titulky") for part in language.split(",")):
        label += " · " + _(30512)
    li = _list_item(label)
    description = "\n".join(filter(None, (_version_label(video), video["title"])))
    _set_info(li, {"title": label or video["title"], "mediatype": "video", "duration": video["duration"],
                   "plot": description})
    _art(li, thumb=video["thumb"])
    li.setProperty("IsPlayable", "true")
    save_url = url_for("provider_save", vid=video["id"], title=video["title"])
    li.addContextMenuItems([(_(30342), "RunPlugin(%s)" % save_url)])
    _add_item(url_for("play", vid=video["id"]), li, False)


def provider_save(params):
    if not hasattr(provider, "search_catalog"):
        return
    try:
        vid = params.get("vid", "")
        if provider.find_video_id(provider.video_page_url(vid)):
            _import([(vid, params.get("title", ""))])
    except provider.ProviderError as e:
        notify(_(30328) % e)


def search_delete(params):
    with open_library() as lib:
        lib.delete_search(params["q"])
    xbmc.executebuiltin("Container.Refresh")


# --- kolekce ---------------------------------------------------------------

def collections(params):
    add_action("[B]%s[/B]" % _(30123), "collection_new", "DefaultAddSource.png")
    with open_library() as lib:
        cols = lib.collections()
    for c in cols:
        ctx = [(_(30125), "RunPlugin(%s)" % url_for("collection_rename", cid=c["id"])),
               (_(30126), "RunPlugin(%s)" % url_for("collection_delete", cid=c["id"]))]
        add_dir("%s (%d)" % (c["name"], c["item_count"]), "collection", "DefaultSets.png", context=ctx,
                cid=c["id"])
    end(cache=False)


def collection(params):
    cid = int(params["cid"])
    with open_library() as lib:
        targets = lib.collection_targets(cid)
        keys = [t for t in targets if not t.startswith("v:")]
        movies_ = dict((m["key"], m) for m in lib.movies(keys=keys))
        shows_ = dict((m["key"], m) for m in lib.shows(keys=keys))
        videos = [lib.get_video(t[2:]) for t in targets if t.startswith("v:")]
        counts = lib.unread_episode_counts() if shows_ else {}
        versions = _movie_versions(lib, movies_.values())
        for t in keys:
            if t in movies_:
                add_movie(movies_[t], lib, collection_id=cid, versions=versions)
            elif t in shows_:
                add_show(shows_[t], collection_id=cid, new_count=counts.get(t, 0))
    for v in videos:
        if v:
            add_video(v)
    end("movies", MOVIE_SORTS, cache=False)


def collection_new(params):
    name = keyboard(_(30124))
    if not name:
        return None
    with open_library() as lib:
        cid = lib.create_collection(name)
    xbmc.executebuiltin("Container.Refresh")
    return cid


def collection_rename(params):
    cid = int(params["cid"])
    with open_library() as lib:
        current = next((c["name"] for c in lib.collections() if c["id"] == cid), "")
        name = keyboard(_(30125), current)
        if name:
            lib.rename_collection(cid, name)
    xbmc.executebuiltin("Container.Refresh")


def collection_delete(params):
    cid = int(params["cid"])
    with open_library() as lib:
        current = next((c["name"] for c in lib.collections() if c["id"] == cid), "")
        if yesno(_(30127) % current):
            lib.delete_collection(cid)
    xbmc.executebuiltin("Container.Refresh")


def collection_add(params):
    target = params["target"]
    with open_library() as lib:
        cols = lib.collections()
        names = ["[B]%s[/B]" % _(30123)] + [c["name"] for c in cols]
        idx = select(_(30128), names)
        if idx < 0:
            return
        if idx == 0:
            name = keyboard(_(30124))
            if not name:
                return
            cid = lib.create_collection(name)
        else:
            cid = cols[idx - 1]["id"]
            name = cols[idx - 1]["name"]
        lib.add_to_collection(cid, target)
    notify(_(30130) % name)


def collection_remove(params):
    with open_library() as lib:
        lib.remove_from_collection(int(params["cid"]), params["target"])
    xbmc.executebuiltin("Container.Refresh")


# --- nástroje --------------------------------------------------------------

def tools(params):
    with open_library() as lib:
        last = lib.get_kv("last_sync")
    last_text = _sync_time(int(last)) if last else _(30232)
    add_action(_(30231) % last_text, "stats", "DefaultIconInfo.png")
    add_dir(_(30105), "unmatched", "DefaultVideoDeleted.png")
    add_action(_(30445), "batch_episodes", "DefaultTVShows.png")
    add_action(_(30200), "sync", "DefaultAddonService.png")
    add_action(_(30203), "enrich", "DefaultAddonInfoProvider.png")
    add_action(_(30202), "add_link", "DefaultAddSource.png")
    add_action(_(30201), "import_file", "DefaultFile.png")
    add_dir(_(30470), "kodi_setup", "DefaultAddonLibrary.png")
    add_action(_(30206), "export_library", "DefaultAddonLibrary.png")
    add_action(_(30205), "account_status", "DefaultUser.png")
    add_action(_(30204), "refresh_all", "DefaultAddonInfoProvider.png")
    add_action(_(30237), "history_clear", "DefaultIconError.png")
    add_action(_(30234), "diagnostics", "DefaultIconInfo.png")
    add_action(_(30345), "backup_catalog", "DefaultAddonLibrary.png")
    add_action(_(30346), "restore_catalog", "DefaultAddonLibrary.png")
    add_action(_(30207), "clear_library", "DefaultIconError.png")
    add_action(_(30208), "settings", "DefaultAddonProgram.png")
    end(cache=False)


def _require_credentials():
    if not setting("username") or not setting("password"):
        ok(_(30222))
        ADDON.openSettings()
        return False
    return True


def _sync_time(timestamp):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


def run_sync(background=False):
    """Synchronizace účtu + volitelně metadata. Použitelné i ze služby."""
    _refresh_watchlist_availability(after_sync=False)
    progress = Progress(_(30210), background=background)
    try:
        client = make_client()

        def on_progress(stage, pages, count):
            progress.update(min(95, pages * 5), _(30211) % (pages, count))
            return progress.cancelled()

        with open_library() as lib:
            stats = catalog.sync_account(
                client, lib, include_favorites=setting_bool("sync_favorites"),
                include_playlists=setting_bool("sync_playlists"),
                remove_missing=setting_bool("remove_missing"), progress=on_progress, log=log)
    finally:
        progress.close()
    log_info("sync: %s" % stats)
    if stats.get("superseded"):
        return stats
    msg = _(30212) % (stats["total"], stats["new"], stats["removed"])
    if stats["incomplete"] and not stats["cancelled"]:
        notify(_(30343) % stats["total"], time_ms=8000)
    elif not stats["cancelled"] and (not background or stats["new"] or stats["removed"]):
        notify("%s · %s" % (_sync_time(time.time()), msg), time_ms=7000)
    if setting_bool("auto_meta") and not stats["cancelled"]:
        run_enrich(statuses=("new",), background=background, quiet=True)
    if not stats["incomplete"] and not stats["cancelled"]:
        fresh = _refresh_watchlist_availability(after_sync=True)
        if fresh:
            notify(_(30515) % fresh, time_ms=8000)
        folder = setting("library_path")
        if folder and setting_bool("library_auto_export"):
            _export_to_kodi(folder, automatic=True)
    return stats


def sync(params):
    if not _require_credentials():
        return
    try:
        run_sync()
    except provider.LoginError as e:
        ok(str(e))
    except provider.ProviderError as e:
        log_error("sync: %s" % e)
        ok(_(30328) % e)
    xbmc.executebuiltin("Container.Refresh")


def run_enrich(statuses=("new",), keys=None, background=False, quiet=False):
    use_csfd = setting_bool("use_csfd")
    tmdb = make_tmdb(cached=False)  # doplnění a obnova metadat vždy čtou aktuální data
    with open_library() as lib:
        if keys:
            metas = [m for m in (lib.get_meta(k) for k in keys) if m]
        else:
            metas = lib.meta_to_fetch(statuses)
        if not metas:
            if not quiet:
                notify(_(30121))
            return None
        progress = Progress(_(30213), background=background)
        try:
            def on_progress(idx, total, title):
                progress.update(idx * 100 // max(1, total), _(30214) % (idx + 1, total, title))
                return progress.cancelled()
            stats = catalog.enrich(lib, metas, use_csfd=use_csfd, tmdb=tmdb, progress=on_progress, log=log)
        finally:
            progress.close()
    log_info("metadata: %s" % stats)
    if stats["csfd_blocked"]:
        if not tmdb.enabled and not background:
            ok(_(30216))
        else:
            log_info("ČSFD blokuje dotazy, použito TMDB")
    if stats["last_error"] and not stats["ok"] and not stats["csfd_blocked"] and not background:
        notify(_(30328) % stats["last_error"])
    if not (background and quiet) or stats["ok"]:
        notify(_(30215) % (stats["ok"], stats["csfd"], stats["tmdb"], stats["notfound"], stats["errors"]),
               time_ms=7000)
    return stats


def enrich(params):
    run_enrich(statuses=("new", "notfound", "error", "blocked"))
    xbmc.executebuiltin("Container.Refresh")


def refresh_all(params):
    if not yesno(_(30229)):
        return
    with open_library() as lib:
        keys = [m["key"] for m in lib.meta_to_fetch(("new", "ok", "notfound", "error", "blocked"))]
    run_enrich(keys=keys)
    xbmc.executebuiltin("Container.Refresh")


def add_link(params):
    text = keyboard(_(30217))
    if not text:
        return
    links = catalog.parse_link_lines(text)
    if not links:
        ok(_(30218))
        return
    _import(links)


def import_file(params):
    path = xbmcgui.Dialog().browse(1, _(30220), "files", ".txt|.csv|.m3u|.list")
    if not path:
        return
    f = xbmcvfs.File(path)
    try:
        content = f.read()
    finally:
        f.close()
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    links = catalog.parse_link_lines(content)
    if not links:
        ok(_(30221))
        return
    _import(links)


def _import(links):
    progress = Progress(_(30201))
    try:
        def on_progress(idx, total, title):
            progress.update(idx * 100 // max(1, total), title)
            return progress.cancelled()
        with open_library() as lib:
            stats = catalog.import_links(make_client(), lib, links, progress=on_progress, log=log)
    finally:
        progress.close()
    notify(_(30219) % (stats["new"], stats["existing"]))
    if stats["new"] and setting_bool("auto_meta"):
        run_enrich(statuses=("new",), quiet=True)
    xbmc.executebuiltin("Container.Refresh")


def account_status(params):
    if not _require_credentials():
        return
    try:
        result, expiration = make_client().check_user()
    except provider.ProviderError as e:
        ok(_(30328) % e)
        return
    if result == provider.CHECK_PREMIUM:
        set_setting("premium_until", provider.premium_text(expiration))
        ok(_(30223) % provider.premium_text(expiration))
    elif result == provider.CHECK_NO_PREMIUM:
        set_setting("premium_until", "-")
        ok(_(30224))
    else:
        set_setting("premium_until", "")
        ok(_(30225))


def stats(params):
    with open_library() as lib:
        s = lib.stats()
        last = lib.get_kv("last_sync")
    last_text = _sync_time(int(last)) if last else _(30232)
    text_viewer(_(30209), (_(30230) % (s["videos"], s["movies"], s["shows"], s["with_rating"],
                                       s["unmatched"])) + "\n\n" + _(30231) % last_text)


def clear_library(params):
    if yesno(_(30226)):
        with open_library() as lib:
            lib.clear()
        xbmc.executebuiltin("Container.Refresh")


def backup_catalog(params):
    folder = xbmcgui.Dialog().browse(3, _(30345), "files")
    if not folder:
        return
    name = "%s-catalog-%s.json" % (ADDON_ID, time.strftime("%Y%m%d-%H%M%S"))
    destination = folder.rstrip("/\\") + "/" + name
    try:
        with open_library() as lib:
            data = json.dumps(lib.export_backup(ADDON_ID), ensure_ascii=False, allow_nan=False)
        file = xbmcvfs.File(destination, "w")
        try:
            if not file.write(data):
                raise OSError("Kodi could not write the backup")
        finally:
            file.close()
    except (OSError, ValueError, sqlite3.Error) as e:
        ok(_(30328) % e)
        return
    notify(_(30348) % destination, time_ms=8000)


def restore_catalog(params):
    path = xbmcgui.Dialog().browse(1, _(30346), "files", ".json")
    if not path or not yesno(_(30347)):
        return
    try:
        file = xbmcvfs.File(path)
        try:
            data = json.loads(file.read())
        finally:
            file.close()
        with open_library() as lib:
            lib.restore_backup(data, ADDON_ID)
    except (OSError, ValueError, OverflowError, sqlite3.Error) as e:
        ok(_(30328) % e)
        return
    _PLAYBACK.clear()
    notify(_(30349))
    xbmc.executebuiltin("Container.Refresh")


def _export_to_kodi(folder, automatic=False, prompt_scan=True):
    state_file = profile_path("library-export.db")
    try:
        with open_library() as lib:
            result = library_export.export(lib, folder, "plugin://%s/" % ADDON_ID, state_file)
        if result.failed:
            notify(_(30461) % result.failed, time_ms=8000)
        if result.protected and not automatic:
            notify(_(30462) % result.protected, time_ms=8000)
        if not automatic:
            notify(_(30227) % (result.movies, result.shows, folder), time_ms=8000)
        scan = (result.scan_pending and setting_bool("library_auto_scan") if automatic
                else prompt_scan and not result.failed and yesno(_(30233)))
        if not result.failed and scan:
            xbmc.executebuiltin("UpdateLibrary(video)")
            library_export.mark_scanned(state_file, result)
        return result
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        log_error("export do Kodi selhal: %s" % type(error).__name__)
        notify(_(30463), time_ms=8000)


def export_library(params):
    folder = setting("library_path")
    if not folder:
        ok(_(30228))
        ADDON.openSettings()
        return
    _export_to_kodi(folder)


def _kodi_history_parameters():
    folder = setting("library_path")
    if folder and setting_bool("library_share_history"):
        return folder, profile_path("library-export.db"), ADDON_ID
    return None


def _import_kodi_history(force=False):
    args = _kodi_history_parameters()
    if not args:
        return None
    try:
        with open_library() as lib:
            now = int(time.time())
            if force or now - int(lib.get_kv("kodi:last_import", 0)) >= 30:
                changed = kodi_library.import_history(lib, *args)
                lib.set_kv("kodi:last_import", now)
                _PLAYBACK.clear()
                return changed
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: history import failed (%s)" % type(error).__name__)
    return None


def publish_kodi_history(vids, mark=None):
    args = _kodi_history_parameters()
    if not args:
        return
    try:
        with open_library() as lib:
            kodi_library.publish_history(lib, *args, vids, mark=mark)
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: history update failed (%s)" % type(error).__name__)


def import_kodi_marks(marks):
    args = _kodi_history_parameters()
    if not args:
        return
    try:
        with open_library() as lib:
            for (kind, item_id), watched in marks.items():
                kodi_library.accept_kodi_mark(lib, *args, kind, item_id, watched)
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: watched state update failed (%s)" % type(error).__name__)


def kodi_setup(params):
    folder = setting("library_path")
    add_action(_(30471), "kodi_choose_folder", "DefaultFolder.png")
    if folder:
        add_action(_(30472), "kodi_instructions", "DefaultAddonLibrary.png")
        add_action(_(30473), "kodi_sources", "DefaultFolder.png")
        add_action(_(30474), "kodi_scan", "DefaultRecentlyAddedMovies.png")
        add_action(_(30475), "kodi_status", "DefaultIconInfo.png")
        if setting_bool("library_share_history"):
            add_action(_(30476), "kodi_import", "DefaultRecentlyAddedEpisodes.png")
        else:
            add_action(_(30477), "kodi_enable_history", "DefaultInProgressShows.png")
    end(cache=False)


def kodi_choose_folder(params):
    current = setting("library_path")
    folder = xbmcgui.Dialog().browse(0, _(30471), "files", defaultt=current)
    if not folder:
        return
    set_setting("library_path", folder)
    result = _export_to_kodi(folder, prompt_scan=False)
    if result and not result.failed:
        kodi_instructions(params)
    xbmc.executebuiltin("Container.Refresh")


def kodi_instructions(params):
    folder = setting("library_path")
    if folder:
        root = library_export.export_folder(folder)
        text_viewer(_(30470), _(30478) % (library_export._join(root, "Filmy"),
                                         library_export._join(root, "Serialy")))


def kodi_sources(params):
    xbmc.executebuiltin("ActivateWindow(Videos,Files,return)")


def kodi_scan(params):
    folder = setting("library_path")
    if not folder:
        notify(_(30228))
        return
    result = _export_to_kodi(folder, prompt_scan=False)
    if result and not result.failed:
        xbmc.executebuiltin("UpdateLibrary(video)")
        try:
            library_export.mark_scanned(profile_path("library-export.db"), result)
        except (OSError, sqlite3.Error):
            log("kodi: scan status could not be saved")


def kodi_status(params):
    folder = setting("library_path")
    if not folder:
        ok(_(30228))
        return
    state_file = profile_path("library-export.db")
    try:
        movies, shows = kodi_library.source_status(folder)
        indexed = list(kodi_library.indexed_links(folder, state_file, ADDON_ID))
        exported = len(library_export.exported_streams(state_file, folder))
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError):
        ok(_(30479))
        return
    status = _(30480) % (_(30481 if movies else 30482), _(30481 if shows else 30482), len(indexed), exported)
    text_viewer(_(30475), status)


def kodi_import(params):
    changed = _import_kodi_history(force=True)
    notify(_(30479) if changed is None else _(30483) % changed)
    xbmc.executebuiltin("Container.Refresh")


def kodi_enable_history(params):
    if yesno(_(30484)):
        folder = setting("library_path")
        result = _export_to_kodi(folder, automatic=True) if folder else None
        if result and not result.failed:
            set_setting("library_share_history", "true")
            kodi_import(params)


def open_settings(params):
    ADDON.openSettings()


# --- úpravy titulů ---------------------------------------------------------

def repair_title(params):
    key = params["key"]
    add_action(_(30373), "tmdb_repair", key=key)
    add_action(_(30300), "meta_assign", key=key)
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key)
    for video in videos:
        add_video(video, meta)
    end("videos", cache=False)


def tmdb_repair(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key)
    if not meta or not videos:
        return
    client = make_tmdb()
    if not client.enabled:
        ok(_(30355))
        ADDON.openSettings()
        return
    raw_title = videos[0]["raw_title"]
    query = keyboard(_(30380) % raw_title, meta["search_title"])
    if not query or not query.strip():
        return
    series = meta["kind"] == "show"
    try:
        candidates = client.search(query.strip(), series)
        if not candidates:
            notify(_(30121))
            return
        labels = ["%s (%s)%s" % (item["title"], item["year"] or "?",
                  " · " + item["orig_title"] if item["orig_title"] and item["orig_title"] != item["title"] else "")
                  for item in candidates]
        choice = select(_(30380) % raw_title, labels)
        if choice < 0:
            return
        info = client.details(candidates[choice]["id"], series)
    except TmdbError as e:
        ok(str(e))
        return
    with open_library() as lib:
        catalog.apply_details(lib, key, info, "tmdb", lock_videos=True)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def meta_assign(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
    if not meta:
        return
    want_series = meta["kind"] == "show"
    options = [_(30306), _(30307), _(30308), _(30309)]
    idx = select(_(30300), options)
    if idx < 0:
        return
    try:
        if idx == 0:
            query = keyboard(_(30310), meta["search_title"])
            if not query:
                return
            results = csfd.search(query, log=log)
            if not results:
                notify(_(30121))
                return
            labels = ["%s (%s) – %s" % (r["title"], r["year"] or "?", r["type"]) for r in results]
            pick = select(_(30306), labels)
            if pick < 0:
                return
            info = csfd.details(results[pick]["id"], log=log)
            source = "csfd"
        elif idx == 1:
            text = keyboard(_(30307), meta.get("csfd_url") or "")
            fid = csfd.film_id_from_url(text or "")
            if not fid:
                return
            info = csfd.details(fid, log=log)
            source = "csfd"
        elif idx == 2:
            tmdb = make_tmdb()
            if not tmdb.enabled:
                ok(_(30327))
                return
            query = keyboard(_(30310), meta["search_title"])
            if not query:
                return
            results = tmdb.search(query, want_series)
            if not results:
                notify(_(30121))
                return
            labels = ["%s (%s)%s" % (r["title"], r["year"] or "?",
                                     (" – " + r["orig_title"]) if r["orig_title"] != r["title"] else "")
                      for r in results]
            pick = select(_(30308), labels)
            if pick < 0:
                return
            info = tmdb.details(results[pick]["id"], want_series)
            source = "tmdb"
        else:
            value = number(_(30311), meta.get("rating") or "")
            if value is None:
                return
            info = {"rating": max(0, min(100, value)), "votes": 0}
            source = "manual"
    except csfd.CsfdBlocked:
        ok(_(30216))
        return
    except (csfd.CsfdError, TmdbError) as e:
        ok(_(30328) % e)
        return
    with open_library() as lib:
        if source == "manual":
            lib.update_meta(key, {"rating": info["rating"], "rating_source": "manual",
                                  "status": meta["status"] if meta["status"] == "ok" else "manual"})
        else:
            catalog.apply_details(lib, key, info, source)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def meta_refresh(params):
    key = params["key"]
    with open_library() as lib:
        lib.reset_meta(key)
    run_enrich(keys=[key])
    xbmc.executebuiltin("Container.Refresh")


def batch_episodes(params):
    with open_library() as lib:
        videos = lib.all_videos()
    if not videos:
        notify(_(30121))
        return
    query = keyboard(_(30453))
    if query is None:
        return
    if query:
        words = normalize(query).split()
        matching = []
        for video in videos:
            filename = normalize(video["raw_title"])
            if all(word in filename for word in words):
                matching.append(video)
        videos = matching
    if not videos:
        notify(_(30121))
        return
    selection = xbmcgui.Dialog().multiselect(_(30446), [video["raw_title"] for video in videos])
    if not selection:
        return
    selected = [videos[index] for index in selection]
    default_title = selected[0]["title"] if all(v["title"] == selected[0]["title"] for v in selected) else ""
    title = keyboard(_(30319), default_title)
    if not title:
        return
    seasons = {video["season"] for video in selected if video["kind"] == "episode"}
    default_season = next(iter(seasons)) if len(seasons) == 1 else 1
    season_number = number(_(30317), str(default_season))
    if season_number is None or season_number < 0:
        return
    episodes = [video["episode"] or parse(video["raw_title"])["episode"] for video in selected]
    while True:
        labels = [_(30447) if all(episodes) else _(30448)]
        labels += ["S%02dE%02d · %s" % (season_number, episode, video["raw_title"]) if episode else
                   "? · " + video["raw_title"] for video, episode in zip(selected, episodes)]
        index = select(title, labels)
        if index < 0:
            return
        if index == 0:
            if all(episodes):
                break
            continue
        episode = number(_(30449) % selected[index - 1]["raw_title"], episodes[index - 1])
        if episode and episode > 0:
            episodes[index - 1] = episode
    try:
        with open_library() as lib:
            key = lib.edit_episodes(list(zip(selected, episodes)), title, season_number)
            meta = lib.get_meta(key)
    except ValueError:
        notify(_(30451))
        return
    if meta and meta["status"] == "new" and setting_bool("auto_meta"):
        run_enrich(keys=[key], quiet=True)
    notify(_(30452) % len(selected))
    xbmc.executebuiltin("Container.Refresh")


def video_edit(params):
    vid = params["vid"]
    with open_library() as lib:
        v = lib.get_video(vid)
    if not v:
        return
    kind_idx = select(_(30312), [_(30313), _(30314)], preselect=1 if v["kind"] == "episode" else 0)
    if kind_idx < 0:
        return
    title = keyboard(_(30319) if kind_idx == 1 else _(30315), v["title"])
    if not title:
        return
    year = season_ = episode = 0
    if kind_idx == 0:
        year = number(_(30316), v["year"] or "") or 0
        kind = "movie"
    else:
        season_ = number(_(30317), v["season"] or 1) or 1
        episode = number(_(30318), v["episode"] or "") or 0
        kind = "episode"
    with open_library() as lib:
        key = lib.edit_video(vid, kind, title, year, season_, episode)
        meta = lib.get_meta(key)
    if meta and meta["status"] == "new" and setting_bool("auto_meta"):
        run_enrich(keys=[key], quiet=True)
    notify(_(30320))
    xbmc.executebuiltin("Container.Refresh")


def video_hide(params):
    with open_library() as lib:
        lib.set_hidden(params["vid"], params.get("hide", "1") == "1")
    xbmc.executebuiltin("Container.Refresh")


def video_delete(params):
    with open_library() as lib:
        v = lib.get_video(params["vid"])
        if v and yesno(_(30326) % v["raw_title"]):
            lib.delete_video(v["id"])
    xbmc.executebuiltin("Container.Refresh")


def video_info(params):
    with open_library() as lib:
        v = lib.get_video(params["vid"])
        meta = lib.get_meta(v["meta_key"]) if v else None
    if not v:
        return
    lines = ["%s: %s" % (_(30330), v["raw_title"]),
             "ID: %s" % v["id"], provider.video_page_url(v["id"]),
             "%s: %s" % (_(30331), v["lang"] or "?"), "%s: %s" % (_(30332), v["quality"] or "?")]
    if meta:
        lines.append("")
        lines.append("%s: %s (%s)" % (_(30315), _display_title(meta), _display_year(meta) or "?"))
        if meta.get("csfd_url"):
            lines.append("ČSFD: %s" % meta["csfd_url"])
        if meta.get("rating"):
            lines.append("%s: %d %%" % (_(30329), meta["rating"]))
    text_viewer(_(30325), "\n".join(lines))


# --- přehrávání ------------------------------------------------------------

def _pick_stream(streams, video, choose):
    if not choose:
        return min(streams, key=_version_score)
    labels = []
    for stream in streams:
        original = stream["quality"] == "ORIG"
        details = {
            "lang": stream.get("lang") or video.get("lang"),
            "quality": video.get("quality") if original else stream.get("resolution") or stream["quality"],
            "size": video.get("size") if original else None,
        }
        label = _version_label(details, _(30398) if original else _(30399))
        if stream.get("subtitles"):
            label += " · " + _(30400) % ", ".join(stream["subtitles"])
        labels.append(label)
    idx = select(_(30321), labels)
    return streams[idx] if idx >= 0 else None


def _version_score(video):
    lang = setting_int("pref_lang", 3)
    quality = setting_int("pref_quality", 0)
    pref_lang = LANG_OPTIONS[lang] if 0 <= lang < len(LANG_OPTIONS) else ""
    pref_quality = QUALITY_OPTIONS[quality] if 0 <= quality < len(QUALITY_OPTIONS) else ""
    order = provider.QUALITY_ORDER
    token = provider.quality_token(video.get("quality"))
    q = order.index(token) if token in order else len(order)
    if pref_quality and q < len(order):
        target = order.index(pref_quality)
        q = q - target if q >= target else len(order) + target - q
    elif pref_quality:
        q = len(order) * 2
    languages = {lang.strip() for lang in (video.get("lang") or "").split(",")}
    preferred_audio = {pref_lang, pref_lang + " dabing"}
    return (bool(pref_lang and languages.isdisjoint(preferred_audio)), q)


def _json_object(raw):
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _link_cache_get(vid):
    with open_library() as lib:
        data = _json_object(lib.get_kv("link:" + vid))
    if (isinstance(data.get("url"), str) and isinstance(data.get("ts"), (int, float))
            and data.get("account") == _account_scope()
            and 0 <= time.time() - data["ts"] < provider.LINK_TTL
            and data.get("quality_preference", 0) == setting_int("pref_quality", 0)):
        return data
    return None


def _link_cache_put(vid, final, subtitles, fallback_height):
    cache_id = uuid.uuid4().hex
    payload = {
        "url": final, "subtitles": subtitles, "fallback_height": fallback_height,
        "cache_id": cache_id, "ts": int(time.time()),
        "quality_preference": setting_int("pref_quality", 0), "account": _account_scope(),
    }
    with open_library() as lib:
        lib.set_kv("link:" + vid, json.dumps(payload))
    return cache_id


def _replacement_offer(vid, message, previous_position=None):
    with open_library() as lib:
        source = lib.find_video(vid)
        group = _version_group(source)
        if not group:
            return False
        season = source["season"] if source["kind"] == "episode" else None
        variants = [video for video in lib.videos_for_meta(source["meta_key"], season=season)
                    if _version_group(video) == group and video["id"] != source["id"]]
        resume = lib.resume_points()
        point = resume.get(source["id"]) or previous_position
    if not variants:
        return False
    variants.sort(key=_version_score)
    options = ["%s · %s" % (label, v["raw_title"]) if label else v["raw_title"]
               for v in variants for label in [_version_label(v)]]
    chosen = select("%s · %s" % (_(30519), message), options)
    if chosen < 0:
        return True
    target = variants[chosen]
    args = {"vid": target["id"], "replacement": 1}
    if target["id"] in resume and yesno(_(30521) % _format_position(resume[target["id"]][0]), heading=_(30519)):
        args["continue_play"] = 1
    elif target["id"] not in resume and point:
        source_progress = {"id": source["id"], "position": point[0], "total": point[1]}
        transfer = _transfer_position(target, [source_progress])
        if transfer:
            (position, total), uncertain, source_duration = transfer
            target_duration = target["duration"]
            if uncertain and target_duration:
                prompt = _(30456) % (_format_position(source_duration), _format_position(target_duration),
                                     _format_position(position))
            else:
                prompt = _(30457 if uncertain else 30521) % _format_position(position)
            if yesno(prompt, heading=_(30519)):
                args.update(replacement_source=source["id"], transferred_position=position, transferred_total=total)
    xbmc.executebuiltin("PlayMedia(%s,noresume)" % url_for("play", **args))
    return True


def playback_failed(vid, message, replacement=False, transferred=None):
    if replacement or not _replacement_offer(vid, message, transferred):
        notify(message, time_ms=8000)


def retry_failed_playback(pending):
    """Po chybě Kodi zahodí nefunkční odkaz a znovu spustí pouze pokus z mezipaměti."""
    vid = pending["vid"]
    cache_id = pending.get("cache_id")
    if cache_id is not None:
        with open_library() as lib:
            key = "link:" + vid
            raw = lib.get_kv(key)
            cached = _json_object(raw)
            if cached.get("cache_id", cached.get("ts")) == cache_id:
                lib.delete_kv(key, expected=raw)
    if pending["cached"]:
        notify(_(30401))
        resume = pending["resume"]
        position, total = pending.get("transferred") or (None, None)
        url = url_for("play", vid=vid, retry=1, restart=None if resume else 1,
                      transferred_position=position, transferred_total=total,
                      replacement=1 if pending.get("replacement") else None)
        xbmc.executebuiltin("PlayMedia(%s,%s)" % (url, "resume" if resume else "noresume"))
    else:
        playback_failed(vid, _(30405), pending.get("replacement", False), pending.get("transferred"))


def _resume_request(vid, params):
    """Vrátí (bod přenesený z jiné verze, spustit od uložené pozice, zrušit přehrání)."""
    retry = params.get("retry") == "1"
    continue_play = params.get("continue_play") == "1"
    requested = params.get("resume_requested") == "1" or params.get("transfer") == "1"
    if retry or params.get("replacement_source"):
        try:
            position = float(params["transferred_position"])
            total = float(params["transferred_total"])
            if isfinite(total) and 0 < position < total:
                if retry:
                    return (position, total), True, False
                with open_library() as lib:
                    target = lib.find_video(vid)
                    source = lib.find_video(params["replacement_source"])
                    group = _version_group(target)
                    if group and not target["hidden"] and _version_group(source) == group:
                        return (position, total), True, False
        except (KeyError, ValueError, TypeError):
            pass
    resume = continue_play or requested or params.get("restart") != "1"
    if not requested or retry:
        return None, continue_play or (retry and resume), False
    with open_library() as lib:
        target = lib.find_video(vid)
        canonical = target["id"] if target else vid
        own = lib.resume_points([canonical]).get(canonical)
        if own:
            return None, True, False
        group = _version_group(target)
        candidate = (_transfer_position(target, _resume_groups(lib).get(group, []))
                     if group and not lib.watched([canonical]) else None)
    if not candidate:
        notify(_(30458))
        return None, False, True
    point, uncertain, source_duration = candidate
    if uncertain:
        target_duration = target["duration"]
        position = _format_position(point[0])
        message = (_(30456) % (_format_position(source_duration), _format_position(target_duration), position)
                   if target_duration else _(30457) % position)
        if not yesno(message, heading=_(30455)):
            return None, False, True
    return point, True, False


def _format_position(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "%d:%02d:%02d" % (hours, minutes, seconds)


def play(params):
    vid = params["vid"]
    xbmcgui.Window(10000).clearProperty(PLAYING_PROPERTY)
    retry = params.get("retry") == "1"
    replacement = params.get("replacement") == "1"
    transferred, force_resume, cancelled = _resume_request(vid, params)
    if cancelled:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    resume = force_resume or params.get("restart") != "1"
    manual = params.get("choose") == "1"
    ask = not retry and (manual or setting_bool("ask_stream"))
    cached = _link_cache_get(vid) if not (ask or retry) else None
    if cached:
        log("play %s: adresa z paměti" % vid)
        cache_id = cached.get("cache_id", cached["ts"])
        _resolved(vid, cached["url"], cached.get("subtitles") or {}, cached.get("fallback_height"),
                  cache_id=cache_id, cached=True, resume=resume, force_resume=force_resume,
                  transferred=transferred, replacement=replacement)
        return
    client = make_client()
    try:
        streams, message = client.get_streams(vid)
        if not streams:
            raise provider.VideoUnavailableError(message or _(30322))
        if message:
            notify(message)
        choose = ask and (manual or len(streams) > 1)
        video = {}
        if choose:
            with open_library() as lib:
                video = lib.find_video(vid) or {}
        stream = _pick_stream(streams, video, choose)
        if stream is None:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        final = client.resolve(stream["url"])
    except provider.ProviderError as exc:
        log_error("play %s: %s" % (vid, exc))
        if isinstance(exc, provider.LoginError):
            message = _(30402)
        elif isinstance(exc, provider.VideoUnavailableError):
            message = _(30403)
        elif isinstance(exc, provider.TemporaryError):
            message = _(30404)
        else:
            message = _(30323) % exc
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        if isinstance(exc, provider.LoginError):
            notify(message, time_ms=8000)
        else:
            playback_failed(vid, message, replacement, transferred)
        return
    subtitles = stream.get("subtitles") or {}
    fallback_height = getattr(client, "fallback_height", None)
    cache_id = _link_cache_put(vid, final, subtitles, fallback_height) if not choose else None
    _resolved(vid, final, subtitles, fallback_height, cache_id=cache_id,
              force_resume=force_resume, resume=resume, transferred=transferred, replacement=replacement)


def _resolved(vid, final, subtitles, fallback_height=None, *, cache_id=None, cached=False,
              force_resume=False, resume=True, transferred=None, replacement=False):
    if fallback_height:
        notify(_(30344) % fallback_height)
    li = xbmcgui.ListItem(path=final + "|User-Agent=" + urllib.parse.quote(provider.USER_AGENT))
    if transferred:
        _set_resume_point(li, transferred)
    elif force_resume:
        _apply_playback(li, vid)
    if final.split("?")[0].lower().endswith(".mp4"):
        li.setMimeType("video/mp4")
        li.setContentLookup(False)
    if setting_bool("subtitles") and subtitles:
        li.setSubtitles(list(subtitles.values()))
    pending = {"vid": vid, "cache_id": cache_id, "cached": cached, "resume": resume,
               "transferred": transferred, "replacement": replacement, "path_hash": sha256(final.split("|", 1)[0].encode("utf-8")).hexdigest(),
               "expires": time.monotonic() + PLAYBACK_START_TIMEOUT}
    xbmcgui.Window(10000).setProperty(PLAYING_PROPERTY, json.dumps(pending))
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


ROUTES = {
    "root": root, "movies_menu": movies_menu, "movies": movies, "movie_genres": movie_genres,
    "movie_decades": movie_decades, "movie_countries": movie_countries,
    "movie_ratings": movie_ratings, "versions": versions, "shows_menu": shows_menu, "shows": shows,
    "show": show, "season": season, "recent": recent, "all_videos": all_videos, "unmatched": unmatched, "hidden": hidden,
    "search_menu": search_menu, "search": search, "search_delete": search_delete, "search_title": search_title,
    "search_people": search_people, "person_titles": person_titles,
    "provider_search": provider_search, "provider_save": provider_save, "online_filters": online_filters,
    "tmdb_catalog": tmdb_catalog, "tmdb_categories": tmdb_categories,
    "tmdb_filters": tmdb_filters, "tmdb_titles": tmdb_titles, "tmdb_title": tmdb_title,
    "tmdb_facets": tmdb_facets, "tmdb_refine": tmdb_refine, "news_menu": news_menu, "movie_parts": movie_parts,
    "my_videos_menu": my_videos_menu, "tmdb_search": tmdb_search,
    "tmdb_movie_versions": tmdb_movie_versions,
    "watchlist": watchlist, "watchlist_change": watchlist_change,
    "watchlist_acknowledge": watchlist_acknowledge,
    "repair_title": repair_title, "tmdb_repair": tmdb_repair, "season_status": season_status,
    "backup_catalog": backup_catalog, "restore_catalog": restore_catalog,
    "collections": collections, "collection": collection, "collection_new": collection_new,
    "collection_rename": collection_rename, "collection_delete": collection_delete,
    "collection_add": collection_add, "collection_remove": collection_remove,
    "tools": tools, "sync": sync, "enrich": enrich, "refresh_all": refresh_all,
    "add_link": add_link, "import_file": import_file, "account_status": account_status,
    "stats": stats, "clear_library": clear_library, "export_library": export_library,
    "settings": open_settings, "meta_assign": meta_assign, "meta_refresh": meta_refresh,
    "video_edit": video_edit, "video_hide": video_hide, "video_delete": video_delete,
    "video_info": video_info, "play": play, "in_progress": in_progress, "played": played,
    "history_remove": history_remove, "history_clear": history_clear, "diagnostics": diagnostics,
    "manage_item": manage_item, "mark_watched": mark_watched,
    "progress_hidden": progress_hidden, "progress_visibility": progress_visibility,
    "movie_filter_create": movie_filter_create, "movie_filter_save": movie_filter_save,
    "movie_filter_delete": movie_filter_delete, "episode_detail": episode_detail,
    "new_episodes": new_episodes, "batch_episodes": batch_episodes,
    "kodi_setup": kodi_setup, "kodi_choose_folder": kodi_choose_folder, "kodi_instructions": kodi_instructions,
    "kodi_sources": kodi_sources, "kodi_scan": kodi_scan, "kodi_status": kodi_status,
    "kodi_import": kodi_import, "kodi_enable_history": kodi_enable_history,
}


def run(argv=None):
    global BASE_URL, HANDLE
    argv = argv or sys.argv
    BASE_URL = argv[0]
    _PLAYBACK.clear()
    del _PENDING_ITEMS[:]
    HANDLE = int(argv[1]) if len(argv) > 1 and argv[1].lstrip("-").isdigit() else -1
    query = argv[2][1:] if len(argv) > 2 and argv[2].startswith("?") else (argv[2] if len(argv) > 2 else "")
    params = dict(urllib.parse.parse_qsl(query))
    action = params.pop("action", "root")
    if action == "play" and params.get("retry") != "1" and len(argv) > 3:
        if argv[3] == "resume:false":
            params["restart"] = "1"
        elif argv[3] == "resume:true" and params.get("offer_transfer") == "1":
            params["resume_requested"] = "1"
    log("route %s %s" % (action, params))
    handler = ROUTES.get(action)
    if handler is None:
        log_error("neznámá akce %s" % action)
        return
    handler(params)
