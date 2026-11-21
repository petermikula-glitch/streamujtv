# -*- coding: utf-8 -*-
"""Vlastní katalog: hlavní nabídka, filmy, seriály, série a díly."""
import json
import time
from collections import Counter
from itertools import islice

import xbmc
import xbmcplugin

from kodiutils import keyboard, log, notify, text_viewer
from search_results import normalize
import ui
from ui import (_, add_action, add_dir, _add_item, add_movie, _add_movies, _add_next_page, add_show,
                add_video, _apply_playback, _art, _display_title, _display_year, end, EPISODE_CACHE_RETRY,
                EPISODE_CACHE_TTL, _episode_detail_action, _episode_metadata_key, _fail_directory,
                FILM_ORIGINS, _first_page, _list_item, _list_page_size, _meta_context, _meta_info,
                MOVIE_FILTER_OPTIONS, MOVIE_SORTS, open_library, _paginate, _playback_state, _plot,
                _run_action, _set_info, url_for, _version_label, _version_score, _watch_action)

views_discover = ui.LazyModule("views_discover")
_tmdb = ui.LazyModule("tmdb")
provider = ui.LazyModule("provider")


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
    if ui.make_tmdb().enabled:
        add_dir(_(30492), "tmdb_titles", "DefaultRecentlyAddedMovies.png", kind="movie", category="recent")
        add_dir(_(30522), "tmdb_titles", "DefaultRecentlyAddedMovies.png", kind="movie",
                category="recent", origin="CZSK")
    end(cache=False)


def movies_menu(params):
    views_discover._add_origin_shortcuts("movies", order="title")
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
        index = ui.select(_(heading), [_(label) for label in options.values()], preselect=0)
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
    if item and ui.yesno(_(30444) % item["name"]):
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
            views_discover._add_origin_shortcuts("movies", order="title")
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
    views_discover._add_origin_shortcuts("movies", order="title")
    if ui.make_tmdb().enabled:
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
            client = ui.make_tmdb()
            if client.enabled:
                for key, cache_key in pending[:max_fetches]:
                    refresh_at = now + EPISODE_CACHE_TTL
                    try:
                        result[key] = client.season_episodes(*key)
                    except _tmdb.TmdbError:
                        log("tmdb: metadata pro seriál %d, sérii %d nejsou dostupná" % key)
                        refresh_at = now + EPISODE_CACHE_RETRY
                    lib.set_kv(cache_key, json.dumps({"episodes": result[key], "refresh_at": refresh_at}))
    return result


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
