# -*- coding: utf-8 -*-
"""Společné hledání v katalogu, na Přehraj.to a v TMDB."""
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import xbmc

import search_results
from kodiutils import keyboard, notify, setting, setting_bool
from nameparser import parse
from search_results import normalize
import ui
from ui import (_, _account_scope, ACTOR_PAGE_SIZE, add_action, add_dir, _add_item, _add_movies,
                _apply_playback, _apply_version_playback, _art, COUNTRY_QUERY_ALIASES, _display_title,
                _display_year, end, _fail_directory, FILM_ORIGINS, _list_item, _meta_context, MOVIE_SORTS,
                open_library, _playback_state, _search_client, SEARCH_COUNTRIES, _set_info, url_for,
                _version_label, _version_score)

views_catalog = ui.LazyModule("views_catalog")
views_discover = ui.LazyModule("views_discover")
views_tools = ui.LazyModule("views_tools")
_tmdb = ui.LazyModule("tmdb")
provider = ui.LazyModule("provider")


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
    choice = ui.select(_(30507), [_(label) for label in (30496, 30508, 30509, 30510, 30511, 30541)])
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
    for _attempt in range(4):
        if not deferred and item and not item.get("_skip_aliases"):
            try:
                alternatives = tmdb.alternative_titles(item)
            except _tmdb.TmdbError:
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
    tmdb = ui.make_tmdb()
    tmdb.timeout = 8
    payload = {"created": now, "query": query, "filters": filters, "account": account, "errors": []}
    provider_query = search_results.format_query(title, year, position) if position else query
    with ThreadPoolExecutor(max_workers=2) as pool:
        provider_task = pool.submit(_provider_page_batch, client, provider_query)
        tmdb_task = pool.submit(views_discover._tmdb_page_batch, lambda page: tmdb.search_titles(title, page)) if tmdb.enabled else None
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
            except _tmdb.TmdbError:
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
    poster = meta.get("poster") or (_tmdb.IMG + "w500" + poster_path if poster_path else "")
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
    client = ui.make_tmdb()
    if not client.enabled:
        notify(_(30355))
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        people, next_page, partial = views_discover._tmdb_page_batch(lambda page: client.search_people(query, page),
                                                       max(1, int(params.get("page", 1))))
    except (_tmdb.TmdbError, ValueError) as error:
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
    client = ui.make_tmdb()
    if not client.enabled:
        add_action(_(30356), "settings")
        end(cache=False)
        return
    try:
        person_id = int(params.get("person_id", 0))
        page = max(1, int(params.get("page", 1)))
        credits = client.acting_credits(person_id)
    except (_tmdb.TmdbError, ValueError) as error:
        notify(str(error))
        end(cache=False)
        return
    start = (page - 1) * ACTOR_PAGE_SIZE
    titles = credits[start:start + ACTOR_PAGE_SIZE]
    found_only = params.get("found_only") == "1"
    args = {"person_id": person_id, "page": page, "found_only": "1" if found_only else ""}
    found = views_discover._availability_controls("person_titles", args, titles, "movie") if titles else set()
    with open_library() as lib:
        lists = {"movie": lib.movies(), "tv": lib.shows()}
        for item in titles:
            kind = item["media_type"]
            verified = "%s:%s" % (kind, item["id"]) in found
            if not found_only or verified:
                views_discover.add_tmdb_title(item, kind, lib, lists[kind], provider_found=verified)
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
        views_discover.tmdb_titles(dict(discovery or {"origin": origin}, kind="movie", category="discover", catalogue="1",
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
        client = ui.make_tmdb()
        if not year and not position and client.enabled:
            client.timeout = 5
            try:
                people, _pages = client.search_people(title)
            except _tmdb.TmdbError:
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
    if setting_bool("ivysilani_enabled"):
        add_dir(_(30586) % title, "ivys_search", "DefaultAddonPVRClient.png", q=title)
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
    for offset in range(views_discover._result_pages()):
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
        tmdb = ui.make_tmdb()
        tmdb_id = str(params.get("tmdb_id", ""))
        if series and (original or tmdb_id.isdigit()):
            match = {"media_type": "tv", "id": int(tmdb_id) if tmdb_id.isdigit() else 0, "original_name": original,
                     "_skip_aliases": not tmdb.enabled or not tmdb_id.isdigit()}
        elif tmdb.enabled:
            try:
                candidates, _pages = tmdb.search_titles(title, 1)
                match = search_results.matching_title(candidates, title, year, series or bool(position))
            except _tmdb.TmdbError:
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
                label = views_catalog._episode_label(parts, {}) + " · " + (_version_label(_provider_version(first, parts)) or first["title"])
                if year and not parts["year"]:
                    label += " · " + _(30543)
            add_provider_result(first, label)
        else:
            label = parts["title"]
            if parts["kind"] == "episode":
                label += " · " + views_catalog._episode_label(parts, {})
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
            views_tools._import([(vid, params.get("title", ""))])
    except provider.ProviderError as e:
        notify(_(30328) % e)


def search_delete(params):
    with open_library() as lib:
        lib.delete_search(params["q"])
    xbmc.executebuiltin("Container.Refresh")
