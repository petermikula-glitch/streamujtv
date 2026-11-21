# -*- coding: utf-8 -*-
"""Router a obrazovky doplňku."""
import sys
import time
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

import catalog
import csfd
import exporter
import streamuj
from db import Library
from kodiutils import (_, ADDON, FANART, ICON, KODI_MAJOR, Progress, keyboard, log,
                       log_error, log_info, notify, number, ok, profile_path, select, set_setting,
                       setting, setting_bool, setting_int, text_viewer, yesno)
from tmdb import Tmdb, TmdbError

BASE_URL = sys.argv[0] if len(sys.argv) > 0 else "plugin://plugin.video.streamujtv/"
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1

LANG_OPTIONS = ["CZ", "SK", "EN", ""]
QUALITY_OPTIONS = ["", "UHD", "FHD", "HD", "SD"]


def url_for(action, **params):
    params["action"] = action
    return BASE_URL + "?" + urllib.parse.urlencode(
        dict((k, v) for k, v in params.items() if v is not None and v != ""))


def open_library():
    return Library(profile_path("catalog.db"))


def make_client():
    return streamuj.StreamujClient(setting("username"), setting("password"),
                                   location=setting("location") or "1",
                                   cookie_file=profile_path("cookies.lwp"), log=log)


def make_tmdb():
    return Tmdb(setting("tmdb_key"), log=log)


# ---------------------------------------------------------------------------
# Tvorba položek
# ---------------------------------------------------------------------------

def _rating_label(meta):
    if not meta or not meta.get("rating"):
        return ""
    r = int(meta["rating"])
    color = "FF4CAF50" if r >= 70 else ("FFFFC107" if r >= 30 else "FFF44336")
    return "  [COLOR %s]%d %%[/COLOR]" % (color, r)


def _display_title(meta):
    return meta.get("title") or meta.get("search_title") or ""


def _display_year(meta):
    return meta.get("year") or meta.get("search_year") or 0


def _plot(meta, extra_lines=None):
    lines = []
    if meta.get("rating"):
        src = {"csfd": "ČSFD", "tmdb": "TMDB", "manual": _(30329)}.get(meta.get("rating_source"), "")
        votes = (" (%s)" % meta["votes"]) if meta.get("votes") else ""
        lines.append("[B]%s %s: %d %%[/B]%s" % (_(30329), src, meta["rating"], votes))
    if extra_lines:
        lines.extend(x for x in extra_lines if x)
    if meta.get("plot"):
        lines.append(meta["plot"])
    return "\n".join(lines)


def _set_info(li, info, cast=None):
    """Kodi 20+ používá InfoTagVideo, Kodi 19 setInfo."""
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


def _art(li, poster="", thumb="", fanart=""):
    art = {"icon": ICON, "fanart": fanart or FANART}
    if poster:
        art.update({"poster": poster, "thumb": poster})
    elif thumb:
        art.update({"thumb": thumb, "poster": thumb})
    else:
        art["thumb"] = ICON
    if thumb:
        art["landscape"] = thumb
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


def _meta_context(meta, collection_id=None):
    key = meta["key"]
    items = [
        (_(30300), "RunPlugin(%s)" % url_for("meta_assign", key=key)),
        (_(30301), "RunPlugin(%s)" % url_for("meta_refresh", key=key)),
        (_(30128), "RunPlugin(%s)" % url_for("collection_add", target=key)),
    ]
    if collection_id:
        items.append((_(30129), "RunPlugin(%s)" % url_for("collection_remove", cid=collection_id,
                                                           target=key)))
    if meta.get("first_video"):
        items.append((_(30302), "RunPlugin(%s)" % url_for("video_edit", vid=meta["first_video"])))
    return items


def _video_context(video):
    vid = video["id"]
    return [
        (_(30325), "RunPlugin(%s)" % url_for("video_info", vid=vid)),
        (_(30302), "RunPlugin(%s)" % url_for("video_edit", vid=vid)),
        (_(30304) if video.get("hidden") else _(30303),
         "RunPlugin(%s)" % url_for("video_hide", vid=vid, hide=0 if video.get("hidden") else 1)),
        (_(30305), "RunPlugin(%s)" % url_for("video_delete", vid=vid)),
    ]


def _version_label(video):
    parts = [p for p in (video.get("lang"), video.get("quality")) if p]
    return " · ".join(parts)


def add_movie(meta, collection_id=None):
    show_rating = setting_bool("show_rating")
    year = _display_year(meta)
    label = _display_title(meta) + (" (%d)" % year if year else "")
    if show_rating:
        label += _rating_label(meta)
    count = meta.get("video_count") or 1
    langs = ", ".join(x for x in (meta.get("langs") or "").split(",") if x)
    extra = [langs]
    if count > 1:
        extra.append(_(30133) % count)
    li = xbmcgui.ListItem(label)
    info = _meta_info(meta, "movie")
    info["plot"] = _plot(meta, extra)
    info["duration"] = (meta.get("runtime") or 0) * 60 or meta.get("duration") or 0
    if meta.get("added_at"):
        info["dateadded"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["added_at"]))
    _set_info(li, info, meta.get("cast"))
    _art(li, meta.get("poster"), meta.get("thumb"), meta.get("fanart"))
    li.addContextMenuItems(_meta_context(meta, collection_id))
    if count == 1:
        li.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(HANDLE, url_for("play", vid=meta["first_video"]), li, False)
    else:
        xbmcplugin.addDirectoryItem(HANDLE, url_for("versions", key=meta["key"]), li, True)


def add_show(meta, collection_id=None):
    label = _display_title(meta)
    if setting_bool("show_rating"):
        label += _rating_label(meta)
    li = xbmcgui.ListItem(label)
    info = _meta_info(meta, "tvshow")
    info["tvshowtitle"] = _display_title(meta)
    info["plot"] = _plot(meta, [_(30133) % (meta.get("video_count") or 0)])
    _set_info(li, info, meta.get("cast"))
    _art(li, meta.get("poster"), meta.get("thumb"), meta.get("fanart"))
    li.addContextMenuItems(_meta_context(meta, collection_id))
    xbmcplugin.addDirectoryItem(HANDLE, url_for("show", key=meta["key"]), li, True)


def add_video(video, meta=None, label=None, mediatype="video"):
    meta = meta or {}
    if label is None:
        label = video["raw_title"]
    li = xbmcgui.ListItem(label)
    info = {"title": label, "mediatype": mediatype, "duration": video.get("duration") or 0}
    extra = [_version_label(video), "%s: %s" % (_(30330), video["raw_title"])]
    if meta:
        info.update(_meta_info(meta, mediatype))
        info["title"] = label
        info["plot"] = _plot(meta, extra)
    else:
        info["plot"] = "\n".join([x for x in extra if x] + [video.get("description") or ""])
    if mediatype == "episode":
        info["tvshowtitle"] = _display_title(meta) if meta else video["title"]
        info["season"] = video.get("season") or 0
        info["episode"] = video.get("episode") or 0
    if video.get("added_at"):
        info["dateadded"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(video["added_at"]))
    _set_info(li, info, meta.get("cast") if meta else None)
    _art(li, meta.get("poster") if meta else "", video.get("thumb"), meta.get("fanart") if meta else "")
    li.setProperty("IsPlayable", "true")
    li.addContextMenuItems(_video_context(video))
    xbmcplugin.addDirectoryItem(HANDLE, url_for("play", vid=video["id"]), li, False)


def add_dir(label, action, icon="", context=None, **params):
    li = xbmcgui.ListItem(label)
    li.setArt({"icon": icon or "DefaultFolder.png", "thumb": icon or "DefaultFolder.png",
               "fanart": FANART})
    if context:
        li.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(HANDLE, url_for(action, **params), li, True)


def add_action(label, action, icon="", **params):
    li = xbmcgui.ListItem(label)
    li.setArt({"icon": icon or "DefaultAddonService.png", "thumb": icon or "DefaultAddonService.png",
               "fanart": FANART})
    xbmcplugin.addDirectoryItem(HANDLE, url_for(action, **params), li, False)


def end(content=None, sort=None, cache=True):
    if content:
        xbmcplugin.setContent(HANDLE, content)
    for s in sort or [xbmcplugin.SORT_METHOD_NONE]:
        xbmcplugin.addSortMethod(HANDLE, s)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache)


MOVIE_SORTS = [xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE,
               xbmcplugin.SORT_METHOD_VIDEO_YEAR, xbmcplugin.SORT_METHOD_VIDEO_RATING,
               xbmcplugin.SORT_METHOD_DATEADDED, xbmcplugin.SORT_METHOD_GENRE]


# ---------------------------------------------------------------------------
# Obrazovky
# ---------------------------------------------------------------------------

def root(params):
    with open_library() as lib:
        empty = lib.count_videos() == 0
    add_dir(_(30100), "movies_menu", "DefaultMovies.png")
    add_dir(_(30101), "shows_menu", "DefaultTVShows.png")
    add_dir(_(30104), "recent", "DefaultRecentlyAddedMovies.png")
    add_dir(_(30102), "search_menu", "DefaultAddonsSearch.png")
    add_dir(_(30103), "collections", "DefaultSets.png")
    add_dir(_(30105), "unmatched", "DefaultVideoDeleted.png")
    add_dir(_(30106), "tools", "DefaultAddonProgram.png")
    end(cache=False)
    if empty and not params.get("quiet"):
        notify(_(30132), time_ms=8000)


def movies_menu(params):
    add_dir(_(30107), "movies", "DefaultMovies.png", order="title")
    add_dir(_(30104), "movies", "DefaultRecentlyAddedMovies.png", order="added")
    add_dir(_(30108), "movies", "DefaultMovieTitle.png", order="rating")
    add_dir(_(30109), "movie_genres", "DefaultGenre.png")
    add_dir(_(30110), "movie_decades", "DefaultYear.png")
    add_dir(_(30111), "movie_countries", "DefaultCountry.png")
    add_dir(_(30112), "movie_ratings", "DefaultMovieTitle.png")
    end()


def movies(params):
    order = params.get("order", "title")
    with open_library() as lib:
        items = lib.movies(genre=params.get("genre"), country=params.get("country"),
                           year_from=int(params["year_from"]) if params.get("year_from") else None,
                           year_to=int(params["year_to"]) if params.get("year_to") else None,
                           min_rating=int(params["min_rating"]) if params.get("min_rating") else None,
                           order=order)
    for m in items:
        add_movie(m)
    end("movies", MOVIE_SORTS)


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
        countries = lib.countries("movie")
    for c, count in countries:
        add_dir("%s (%d)" % (c, count), "movies", "DefaultCountry.png", country=c, order="title")
    end()


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
    for v in videos:
        label = _version_label(v) or v["raw_title"]
        add_video(v, meta, label="%s – %s" % (_display_title(meta), label), mediatype="movie")
    end("movies")


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
    for m in items:
        add_show(m)
    end("tvshows", MOVIE_SORTS)


def _season_label(season):
    return _(30115) if not season else _(30114) % season


def show(params):
    key = params["key"]
    with open_library() as lib:
        meta = lib.get_meta(key)
        seasons = lib.seasons(key)
    if meta is None:
        end()
        return
    if len(seasons) == 1:
        params = dict(params, season=seasons[0])
        return season(params)
    for s in seasons:
        li = xbmcgui.ListItem(_season_label(s))
        info = _meta_info(meta, "season")
        info.update({"title": _season_label(s), "season": s, "tvshowtitle": _display_title(meta),
                     "plot": _plot(meta)})
        _set_info(li, info)
        _art(li, meta.get("poster"), "", meta.get("fanart"))
        li.addContextMenuItems(_meta_context(meta))
        xbmcplugin.addDirectoryItem(HANDLE, url_for("season", key=key, season=s), li, True)
    end("seasons")


def season(params):
    key = params["key"]
    s = int(params.get("season") or 0)
    with open_library() as lib:
        meta = lib.get_meta(key)
        videos = lib.videos_for_meta(key, season=s)
    for v in videos:
        ep = v.get("episode") or 0
        if ep:
            label = "%dx%02d" % (s, ep)
            version = _version_label(v)
            if version:
                label += "  [COLOR FF9E9E9E]%s[/COLOR]" % version
        else:
            label = v["raw_title"]
        add_video(v, meta, label=label, mediatype="episode")
    end("episodes", [xbmcplugin.SORT_METHOD_EPISODE, xbmcplugin.SORT_METHOD_LABEL])


def recent(params):
    with open_library() as lib:
        videos = lib.recent_videos(limit=100)
        metas = dict((m["key"], m) for m in (lib.get_meta(k) for k in set(v["meta_key"] for v in videos)) if m)
    for v in videos:
        meta = metas.get(v["meta_key"]) or {}
        if v["kind"] == "episode":
            label = "%s %dx%02d" % (_display_title(meta) or v["title"], v["season"], v["episode"])
        else:
            year = _display_year(meta) if meta else v["year"]
            label = (_display_title(meta) or v["title"]) + (" (%d)" % year if year else "")
        if setting_bool("show_rating"):
            label += _rating_label(meta)
        add_video(v, meta, label=label, mediatype="episode" if v["kind"] == "episode" else "movie")
    end("videos", [xbmcplugin.SORT_METHOD_DATEADDED, xbmcplugin.SORT_METHOD_LABEL])


def unmatched(params):
    with open_library() as lib:
        items = lib.unmatched()
    for m in items:
        if m["kind"] == "show":
            add_show(m)
        else:
            add_movie(m)
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
    with open_library() as lib:
        history = lib.search_history()
    for q in history:
        add_dir(q, "search", "DefaultAddonsSearch.png",
                context=[(_(30122), "RunPlugin(%s)" % url_for("search_delete", q=q))], q=q)
    end(cache=False)


def search(params):
    query = params.get("q")
    if not query:
        # klávesnice jen jednou – výsledky mají vlastní adresu, aby se po návratu
        # zpět neotevírala znovu
        query = keyboard(_(30118))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        if query:
            xbmc.executebuiltin("Container.Update(%s)" % url_for("search", q=query))
        return
    with open_library() as lib:
        lib.add_search(query)
        found_movies, found_shows = lib.search(query)
    for m in found_movies:
        add_movie(m)
    for m in found_shows:
        add_show(m)
    if not found_movies and not found_shows:
        notify(_(30121))
    end("movies", MOVIE_SORTS, cache=False)


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
    for t in keys:
        if t in movies_:
            add_movie(movies_[t], collection_id=cid)
        elif t in shows_:
            add_show(shows_[t], collection_id=cid)
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
    add_action(_(30200), "sync", "DefaultAddonService.png")
    add_action(_(30203), "enrich", "DefaultAddonInfoProvider.png")
    add_action(_(30202), "add_link", "DefaultAddSource.png")
    add_action(_(30201), "import_file", "DefaultFile.png")
    add_action(_(30206), "export_library", "DefaultAddonLibrary.png")
    add_action(_(30205), "account_status", "DefaultUser.png")
    add_action(_(30209), "stats", "DefaultIconInfo.png")
    add_action(_(30204), "refresh_all", "DefaultAddonInfoProvider.png")
    add_action(_(30207), "clear_library", "DefaultIconError.png")
    add_action(_(30208), "settings", "DefaultAddonProgram.png")
    end(cache=False)


def _require_credentials():
    if not setting("username") or not setting("password"):
        ok(_(30222))
        ADDON.openSettings()
        return False
    return True


def run_sync(background=False):
    """Synchronizace účtu + volitelně metadata. Použitelné i ze služby."""
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
    msg = _(30212) % (stats["total"], stats["new"], stats["removed"])
    if background:
        if stats["new"] or stats["removed"]:
            notify(msg)
    else:
        notify(msg, time_ms=7000)
    if setting_bool("auto_meta") and not stats["cancelled"]:
        run_enrich(statuses=("new",), background=background, quiet=True)
    return stats


def sync(params):
    if not _require_credentials():
        return
    try:
        run_sync()
    except streamuj.LoginError as e:
        ok(str(e))
    except streamuj.StreamujError as e:
        log_error("sync: %s" % e)
        ok(_(30328) % e)
    xbmc.executebuiltin("Container.Refresh")


def run_enrich(statuses=("new",), keys=None, background=False, quiet=False):
    use_csfd = setting_bool("use_csfd")
    tmdb = make_tmdb()
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
    except streamuj.StreamujError as e:
        ok(_(30328) % e)
        return
    if result == streamuj.CHECK_PREMIUM:
        set_setting("premium_until", expiration or "?")
        ok(_(30223) % (expiration or "?"))
    elif result == streamuj.CHECK_NO_PREMIUM:
        set_setting("premium_until", "-")
        ok(_(30224))
    else:
        set_setting("premium_until", "")
        ok(_(30225))


def stats(params):
    with open_library() as lib:
        s = lib.stats()
        last = lib.get_kv("last_sync")
    last_text = time.strftime("%d.%m.%Y %H:%M", time.localtime(int(last))) if last else _(30232)
    text_viewer(_(30209), (_(30230) % (s["videos"], s["movies"], s["shows"], s["with_rating"],
                                       s["unmatched"])) + "\n\n" + _(30231) % last_text)


def clear_library(params):
    if yesno(_(30226)):
        with open_library() as lib:
            lib.clear()
        xbmc.executebuiltin("Container.Refresh")


def export_library(params):
    folder = setting("library_path")
    if not folder:
        ok(_(30228))
        ADDON.openSettings()
        return
    with open_library() as lib:
        n_movies, n_shows = exporter.export(lib, folder, BASE_URL)
    notify(_(30227) % (n_movies, n_shows, folder), time_ms=8000)
    if yesno(_(30233)):
        xbmc.executebuiltin("UpdateLibrary(video)")


def open_settings(params):
    ADDON.openSettings()


# --- úpravy titulů ---------------------------------------------------------

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
             "ID: %s" % v["id"], streamuj.video_page_url(v["id"]),
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

def _pick_stream(streams):
    if len(streams) == 1:
        return streams[0]
    if setting_bool("ask_stream"):
        labels = ["%s %s%s" % (s["lang"], streamuj.QUALITY_LABEL.get(s["quality"], s["quality"]),
                               (" + tit. " + ", ".join(s["subtitles"])) if s["subtitles"] else "")
                  for s in streams]
        idx = select(_(30321), labels)
        return streams[idx] if idx >= 0 else None
    pref_lang = LANG_OPTIONS[setting_int("pref_lang", 0)] if setting_int("pref_lang", 0) < len(LANG_OPTIONS) else ""
    pref_q = QUALITY_OPTIONS[setting_int("pref_quality", 0)] if setting_int("pref_quality", 0) < len(QUALITY_OPTIONS) else ""
    order = streamuj.QUALITY_ORDER

    def score(s):
        lang_score = 0 if (not pref_lang or s["lang"] == pref_lang) else 1
        q = order.index(s["quality"]) if s["quality"] in order else len(order)
        if pref_q:
            target = order.index(pref_q)
            # nejbližší nižší nebo stejná kvalita, pak vyšší
            q_score = (q - target) if q >= target else (len(order) + target - q)
        else:
            q_score = q
        return (lang_score, q_score)
    return sorted(streams, key=score)[0]


def play(params):
    vid = params["vid"]
    client = make_client()
    try:
        streams, message = client.get_streams(vid)
        if not streams:
            raise streamuj.StreamujError(message or _(30322))
        if message:
            notify(message)
        stream = _pick_stream(streams)
        if stream is None:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        final = client.resolve(stream["url"])
    except streamuj.StreamujError as e:
        log_error("play %s: %s" % (vid, e))
        notify(_(30323) % e, time_ms=8000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    li = xbmcgui.ListItem(path=final + "|User-Agent=" + urllib.parse.quote(streamuj.USER_AGENT))
    if final.split("?")[0].lower().endswith(".mp4"):
        li.setMimeType("video/mp4")
        li.setContentLookup(False)
    if setting_bool("subtitles") and stream.get("subtitles"):
        li.setSubtitles(list(stream["subtitles"].values()))
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


ROUTES = {
    "root": root, "movies_menu": movies_menu, "movies": movies, "movie_genres": movie_genres,
    "movie_decades": movie_decades, "movie_countries": movie_countries,
    "movie_ratings": movie_ratings, "versions": versions, "shows_menu": shows_menu, "shows": shows,
    "show": show, "season": season, "recent": recent, "unmatched": unmatched, "hidden": hidden,
    "search_menu": search_menu, "search": search, "search_delete": search_delete,
    "collections": collections, "collection": collection, "collection_new": collection_new,
    "collection_rename": collection_rename, "collection_delete": collection_delete,
    "collection_add": collection_add, "collection_remove": collection_remove,
    "tools": tools, "sync": sync, "enrich": enrich, "refresh_all": refresh_all,
    "add_link": add_link, "import_file": import_file, "account_status": account_status,
    "stats": stats, "clear_library": clear_library, "export_library": export_library,
    "settings": open_settings, "meta_assign": meta_assign, "meta_refresh": meta_refresh,
    "video_edit": video_edit, "video_hide": video_hide, "video_delete": video_delete,
    "video_info": video_info, "play": play,
}


def run(argv=None):
    global BASE_URL, HANDLE
    argv = argv or sys.argv
    BASE_URL = argv[0]
    HANDLE = int(argv[1]) if len(argv) > 1 and argv[1].lstrip("-").isdigit() else -1
    query = argv[2][1:] if len(argv) > 2 and argv[2].startswith("?") else (argv[2] if len(argv) > 2 else "")
    params = dict(urllib.parse.parse_qsl(query))
    action = params.get("action", "root")
    log("route %s %s" % (action, dict((k, v) for k, v in params.items() if k != "action")))
    handler = ROUTES.get(action)
    if handler is None:
        log_error("neznámá akce %s" % action)
        return
    handler(params)
