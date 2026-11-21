# -*- coding: utf-8 -*-
"""Společné stavební prvky obrazovek: URL, položky seznamu, metadata, stav přehrávání."""
import json
import sys
import time
import urllib.parse
from hashlib import sha256

import xbmc
import xbmcgui
import xbmcplugin

import kodiutils
from db import Library
from kodiutils import (ADDON_ID, FANART, ICON, KODI_MAJOR, log, profile_path, select, setting, setting_bool,
                       setting_int, yesno)

# Obrazovky volají dialogy a klienty přes ui.select, ui.make_tmdb… (jedno místo pro náhradu v testech).
SHARED_DIALOGS = (select, yesno)


class LazyModule(object):
    """Modul se načte až při prvním použití – síťové knihovny tak nezdržují start
    obrazovek, které síť nepotřebují (katalog, rozkoukané, kolekce)."""

    def __init__(self, name):
        self.__dict__["_name"] = name

    def _module(self):
        module = self.__dict__.get("_loaded")
        if module is None:
            import importlib
            module = self.__dict__["_loaded"] = importlib.import_module(self.__dict__["_name"])
        return module

    def __getattr__(self, attr):
        return getattr(self._module(), attr)


_tmdb = LazyModule("tmdb")
provider = LazyModule("provider")


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
    # lambda: provider (a s ním síťové knihovny) se načte, až když ho katalog opravdu potřebuje
    return Library(profile_path("catalog.db"), video_key=lambda vid: provider.video_key(vid),
                   prefer_ref=lambda new, old: provider.prefer_ref(new, old))


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
    return _tmdb.Tmdb(setting("tmdb_key"), log=log, cache=_ResponseCache() if cached else None)


def _search_client():
    client = make_client()
    client.request_timeout = 5
    client.request_retries = 0
    return client


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


def _episode_metadata_key(meta, season):
    tmdb_id = (meta or {}).get("tmdb_id")
    if tmdb_id and tmdb_id > 0 and season >= 0:
        return tmdb_id, season
    return None


def _episode_detail_action(key, season, episode):
    return _run_action(30424, "episode_detail", key=key, season=season, episode=episode)


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
