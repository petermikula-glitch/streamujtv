# -*- coding: utf-8 -*-
"""Objevování v TMDB, Chci vidět, další části filmů a ověřování dostupnosti."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import islice

import xbmc
import xbmcgui

import search_results
from kodiutils import notify, setting_int
from nameparser import parse
from search_results import normalize
import ui
from ui import (_, _account_scope, add_action, add_dir, _add_item, _add_movies, add_video, _apply_playback,
                _apply_version_playback, _art, AVAILABILITY_BATCH, AVAILABILITY_PAUSE, AVAILABILITY_WORKERS,
                COUNTRY_QUERY_ALIASES, _display_title, _display_year, end, _fail_directory, FILM_ORIGINS,
                _json_object, _list_item, _meta_info, open_library, _playback_state, PLAYING_PROPERTY,
                _run_action, _search_client, SEARCH_COUNTRIES, _set_info, url_for, _version_label,
                _version_score)

views_progress = ui.LazyModule("views_progress")
views_search = ui.LazyModule("views_search")
_tmdb = ui.LazyModule("tmdb")
provider = ui.LazyModule("provider")


def _add_origin_shortcuts(action, **params):
    add_dir(_(30489), action, "DefaultCountry.png", origin="CZSK", **params)


def tmdb_catalog(params):
    add_dir(_(30374), "watchlist", "DefaultFavourites.png")
    if not ui.make_tmdb().enabled:
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
        options = _tmdb_filter_options(ui.make_tmdb(), kind, field)
    except _tmdb.TmdbError as e:
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
    choice = ui.select(_(30495), [_(label) for _field, label in fields] + [_(30496)])
    if choice < 0:
        return
    if choice == len(fields):
        selection.update(genre="", year="", origin="")
    else:
        field, heading = fields[choice]
        try:
            options = [("", _(30428))] + _tmdb_filter_options(ui.make_tmdb(), selection["kind"], field)
        except _tmdb.TmdbError as error:
            notify(str(error), time_ms=8000)
            return
        current = next((i for i, (value, _label) in enumerate(options)
                        if str(value) == selection[field]), 0)
        picked = ui.select(_(heading), [label for _value, label in options], preselect=current)
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
        except _tmdb.TmdbError:
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
    pause_key = "availability:paused:" + _account_scope()
    if todo:
        with open_library() as lib:
            failed = bool(lib.cache_get(pause_key))  # po chybě (např. HTTP 429) chvíli nezkoušet znovu
    if todo and not failed:
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
        if failed:
            with open_library() as lib:
                lib.cache_put(pause_key, True, AVAILABILITY_PAUSE)
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
        client = ui.make_tmdb()
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
    except (ValueError, _tmdb.TmdbError) as e:
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
    _art(li, poster=_tmdb.IMG + "w500" + poster if poster else "",
         fanart=_tmdb.IMG + "w1280" + backdrop if backdrop else "")
    detail_url = url_for("tmdb_title", kind=kind, tmdb_id=item["id"])
    args = {"kind": kind, "tmdb_id": item["id"], "title": title, "original": original,
            "year": released_year, "poster": _tmdb.IMG + "w500" + poster if poster else ""}
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
    filters = views_search._online_filters(params)
    args.update(filters)
    views_search._add_online_filters("tmdb_movie_versions", dict(params, q=query))
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
        videos, next_page, cursor, partial = views_search._provider_page_batch(
            client, query, page, params.get("cursor", ""))
        tmdb = ui.make_tmdb()
        item = {"media_type": "movie", "id": tmdb_id, "original_title": original}
        if page == 1 and not partial and normalize(query) == normalize(title):
            # U uložených odkazů s ručně zadaným původním názvem nemusí být TMDB k dispozici.
            if not tmdb_id or not tmdb.enabled:
                item["_skip_aliases"] = True
            videos, partial, _metadata_error = views_search._expand_provider_search(
                client, videos, query, title, year, None, filters, tmdb, item)
        names = (title, original, *item.get("_search_aliases", ()))
        filtered = views_search._filter_online_videos(videos, filters, names)
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
            versions.append((views_search._movie_result_key(result, parsed, title, original, year, names[2:]),
                             views_search._provider_version(result, parsed)))
        for sort_key, video in sorted(versions, key=lambda item: item[0]):
            preview, rank = sort_key[:2]
            identity = provider.video_key(video["id"])
            if identity in local_ids:
                continue
            local_ids.add(identity)
            label = _version_label(video) if not preview and rank == 0 else ""
            # A descriptive second line plus the original filename help distinguish versions
            # that happen to have the same language, resolution and file size.
            views_search.add_provider_result(video, label or video["title"])
        if not filtered:
            views_search._add_relaxed_searches(
                "tmdb_movie_versions", dict(args, q=query, page=page, cursor=params.get("cursor")),
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
        client = ui.make_tmdb()
        if not client.enabled:
            tmdb_catalog(params)
            return
        detail = client.details(tmdb_id, want_series)
    except (ValueError, _tmdb.TmdbError) as e:
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
    client = ui.make_tmdb()
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
    except (ValueError, _tmdb.TmdbError) as error:
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
                views_search._search_entry(card, title, "play", {"vid": preferred["id"]}, preferred, direct_resume=True)
            else:
                add_dir(title, "tmdb_movie_versions", tmdb_id=part["id"], title=part["title"],
                        original=part.get("original_title"), year=year)
        for part, _preferred, status, _year in statuses:
            add_tmdb_title(part, "movie", lib, movies, status=status)
    end("movies", cache=False)


def offer_next_movie(vid, player):
    if player.isPlaying() or xbmcgui.Window(10000).getProperty(PLAYING_PROPERTY):
        return
    client = ui.make_tmdb()
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
        views_progress._offer_playback(player, _(30506), next_part["title"], following["id"], following["id"] in resume)


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
            lib.watchlist_add(kind, tmdb_id, params["title"], year, poster if poster.startswith(_tmdb.IMG) else "")
    xbmc.executebuiltin("Container.Refresh")


def tmdb_search(params):
    query = params.get("q", "").strip()
    client = ui.make_tmdb()
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
    except (_tmdb.TmdbError, ValueError) as e:
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
