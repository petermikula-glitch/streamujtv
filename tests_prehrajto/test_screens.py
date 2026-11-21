"""Obrazovky katalogu: procházení, stránkování, kontextové nabídky, router."""

import os

from conftest import fixture
import prehrajto
import provider

from helpers import REF, view


def _add_movies_to_catalog(plugin, count, versions=1):
    import nameparser
    with plugin.open_library() as lib, lib.conn:
        for i in range(count):
            for v in range(versions):
                raw = "Film %02d %d %s 1080p" % (i, 1990 + i, "CZ" if v else "EN")
                lib.upsert_video("%d:film-%d:%016x" % (i * 10 + v + 1, i, i * 10 + v + 1), raw, "account",
                                 nameparser.parse(raw))


def test_plugin_sync_browse_play(plugin, monkeypatch):
    import xbmcplugin
    import catalog
    assert os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(plugin.__file__)))) == \
        "plugin.video.prehrajto.mojevidea"

    class C(object):
        def my_videos(self, progress=None):
            return prehrajto.parse_video_list(fixture("uploads_synthetic.html"))
    with plugin.open_library() as lib:
        catalog.sync_account(C(), lib)
    xbmcplugin.ITEMS[:] = []
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1", "?action=movies"])
    labels = [it[1].label for it in xbmcplugin.ITEMS]
    assert labels == ["Pelíšky (1999)"]
    url = xbmcplugin.ITEMS[0][0]
    assert "action=play" in url

    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", lambda self, ref: (
        [{"lang": "", "quality": "ORIG", "url": "orig:" + ref, "subtitles": {"CZ": "https://x/s.vtt"}},
         {"lang": "", "quality": "FHD", "url": "https://cdn/1080.mp4", "subtitles": {}}], ""))
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "resolve", lambda self, u: "https://cdn/orig.mkv" if u.startswith("orig:") else u)
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1", "?" + url.split("?", 1)[1]])
    ok, li = xbmcplugin.RESOLVED[-1]
    assert ok and li.path.startswith("https://cdn/orig.mkv|")
    assert li.subtitles == ["https://x/s.vtt"]


def test_version_label_shows_size(plugin):
    assert plugin._version_label({"lang": "", "quality": "1080p", "size": "4.2 GB"}) == "1080p · 4.2 GB"


def test_continue_watching_uses_one_episode_per_series(plugin):
    import xbmcaddon
    import xbmcplugin
    import nameparser

    xbmcaddon.SETTINGS["pref_lang"] = "0"
    videos = [
        ("Hra.o.truny.S01E01.CZ", 200, 1),
        ("Hra.o.truny.S01E02.EN", 300, 2),
        ("Hra.o.truny.S01E02.CZ", 0, 3),
        ("Hra.o.truny.S02E03.EN", 0, 4),
        ("Hra.o.truny.S02E03.CZ", 0, 5),
        ("Jiny.serial.S01E01.CZ", 120, 6),
        ("Pelisky.1999.CZ", 150, 7),
    ]
    refs = {}
    with plugin.open_library() as lib:
        for number, (title, seconds, when) in enumerate(videos, start=1):
            vid = "%d:v:%016x" % (number, number)
            refs[title] = vid
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            if seconds:
                lib.record_play(vid, seconds, 600, now=when)

    plugin.in_progress({})
    assert len(xbmcplugin.ITEMS) == 3
    url, item, _ = xbmcplugin.ITEMS[-1]
    assert url == plugin.url_for("play", vid=refs["Hra.o.truny.S01E02.EN"])
    assert item.tag.data["resumepoint"] == (300.0, 600.0)

    # Dokoukání české verze označí celý díl jako zhlédnutý, i když EN zůstala rozkoukaná.
    with plugin.open_library() as lib:
        lib.record_play(refs["Hra.o.truny.S01E02.CZ"], 600, 600, now=8)
    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    assert len(xbmcplugin.ITEMS) == 3
    url, item, _ = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("play", vid=refs["Hra.o.truny.S02E03.CZ"])
    assert "Další díl" in item.label


def test_home_search_and_tmdb_catalogue_links(plugin, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import nameparser
    import xbmc
    import xbmcplugin

    class TmdbSearch:
        enabled = True

        def search_titles(self, query, page):
            assert (query, page) == ("Pelíšky", 1)
            return [
                {"media_type": "movie", "id": 10, "title": "Pelíšky", "release_date": "1999-04-08"},
                {"media_type": "movie", "id": 11, "title": "Pelíšky", "release_date": "2026-01-01"},
            ], 1

    monkeypatch.setattr(plugin.ui, "make_tmdb", TmdbSearch)
    online = []

    def fetch(_client, query, page, cursor):
        online.append((query, page, cursor))
        return [{"id": REF, "title": "Pelisky.1999.CZ"}], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    with plugin.open_library() as lib:
        title = "Pelisky.1999.CZ"
        lib.upsert_video(REF, title, "account", nameparser.parse(title))
    plugin.root({})
    assert [item.label for _, item, _ in xbmcplugin.ITEMS][:5] == [
        "Rozkoukané", "Moje filmy", "Moje seriály", "Hledat", "Objevovat"]
    search_url = xbmcplugin.ITEMS[3][0]
    assert parse_qs(urlsplit(search_url).query) == {"action": ["search"]}

    xbmc.KEYBOARD_INPUT.append("  Pelíšky  ")
    xbmc.BUILTINS.clear()
    xbmcplugin.ITEMS.clear()
    plugin.search({})
    assert xbmc.BUILTINS == [] and xbmcplugin.ENDED[-1]
    urls = [parse_qs(urlsplit(url).query) for url, _, _ in xbmcplugin.ITEMS]
    assert [url["action"][0] for url in urls] == ["online_filters", "play", "tmdb_title", "search_people",
                                                   "provider_search", "tmdb_search"]
    assert online == [("Pelíšky", 1, "")]
    xbmcplugin.ITEMS.clear()

    plugin.tmdb_search({"q": "Pelíšky"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert not folder and url == plugin.url_for("play", vid=REF)
    assert "Přehrát z katalogu" in item.label
    assert xbmcplugin.ITEMS[1][2]  # titul se stejným názvem z jiného roku
    assert "Přehrát z katalogu" not in xbmcplugin.ITEMS[1][1].label


def test_season_watched_and_hidden_progress_preserve_playback_history(plugin):
    import nameparser
    import xbmcgui
    import xbmcplugin

    episodes = {}
    with plugin.open_library() as lib:
        for i, suffix in enumerate(("S01E01.CZ", "S01E01.EN", "S01E03.CZ", "S02E01.CZ"), start=1):
            vid = "%d:serial:%016x" % (i, i)
            title = "Test.Serial." + suffix
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            episodes[suffix] = vid
        first = episodes["S01E01.CZ"]
        key = lib.get_video(first)["meta_key"]
        lib.record_play(first, 125, 480, started=True)

    plugin.show({"key": key})
    season_item = xbmcplugin.ITEMS[0][1]
    assert "Označit celou sérii jako zhlédnutou" in {label for label, _ in season_item.context}
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.mark_watched({"key": key, "season": "1", "watched": "1"})
    with plugin.open_library() as lib:
        assert lib.watched() == {vid for episode, vid in episodes.items() if episode.startswith("S01")}
        assert len(lib.history_videos()) == 1 and lib.history_videos()[0]["position"] == 125

    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    url, item, _ = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("play", vid=episodes["S02E01.CZ"])
    assert "Skrýt z Rozkoukaných" in {label for label, _ in item.context}
    plugin.progress_visibility({"key": key, "hide": "1"})
    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    assert [item.label for _, item, _ in xbmcplugin.ITEMS] == ["Skryté z Rozkoukaných"]

    xbmcplugin.ITEMS.clear()
    plugin.progress_hidden({})
    assert xbmcplugin.ITEMS[0][1].label.startswith("Znovu zobrazit:")
    with plugin.open_library() as lib:
        backup = lib.export_backup(plugin.ADDON_ID)
        assert backup["tables"]["progress_hidden"] == [{"target": key}]
        assert len(backup["tables"]["watched_marks"]) == 3
        lib.clear()
        lib.restore_backup(backup, plugin.ADDON_ID)
        assert lib.hidden_progress()[0]["key"] == key
        assert lib.history_videos()[0]["position"] == 125

    plugin.progress_visibility({"key": key, "hide": "0"})
    plugin.mark_watched({"key": key, "season": "1", "watched": "0"})
    with plugin.open_library() as lib:
        assert not lib.watched() and not lib.hidden_progress()
        assert lib.resume_points()[first] == (125, 480)
        legacy = lib.export_backup(plugin.ADDON_ID)
        del legacy["tables"]["progress_hidden"]
        del legacy["tables"]["watched_marks"]
        for video in legacy["tables"]["videos"]:
            del video["search_name"]
        lib.restore_backup(legacy, plugin.ADDON_ID)
        assert lib.resume_points()[first] == (125, 480)
        assert lib.get_video(first)["search_name"] == nameparser.normalize(lib.get_video(first)["raw_title"])


def test_item_context_groups_edit_actions(plugin, monkeypatch):
    import nameparser
    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    plugin.movies({})
    item = xbmcplugin.ITEMS[0][1]
    assert [label for label, _ in item.context][:4] == [
        "Vybrat jinou verzi", "Přidat do kolekce", "Označit jako zhlédnuté", "Spravovat položku"]

    calls = []
    monkeypatch.setattr(view("views_edit"), "video_edit", lambda params: calls.append(params["vid"]))
    monkeypatch.setattr(plugin.ui, "select", lambda heading, options: options.index(plugin._(30302)))
    plugin.manage_item({"vid": REF})
    assert calls == [REF]


def test_episode_metadata_cache_and_spoiler_free_browsing(plugin, monkeypatch):
    import nameparser
    import xbmcgui
    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    calls = []

    def fetch(path, **params):
        calls.append((path, params))
        return {"episodes": [{"episode_number": 3, "name": "Zkouška", "runtime": 43,
                              "overview": "Závěr epizody", "still_path": "/episode.jpg"}]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    with plugin.open_library() as lib:
        title = "Archa.S02E03.CZ"
        lib.upsert_video(REF, title, "account", nameparser.parse(title), duration=2400)
        key = lib.get_video(REF)["meta_key"]
        lib.update_meta(key, {"tmdb_id": 321, "plot": "Popis seriálu"})
        lib.record_play(REF, 300, 2400)
    plugin.season({"key": key, "season": "2"})
    listing = xbmcplugin.ITEMS[-1][1]
    assert listing.label == "S02E03 · Zkouška · 43 min"
    assert listing.art["thumb"].endswith("/episode.jpg")
    assert listing.tag.data["duration"] == 43 * 60
    assert "Závěr epizody" not in listing.tag.data["plot"]
    assert "Popis seriálu" not in listing.tag.data["plot"]
    assert "Detail epizody" in [label for label, _ in listing.context]

    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    item = xbmcplugin.ITEMS[0][1]
    assert "S02E03 · Zkouška · 43 min" in item.label
    assert item.tag.data["resumepoint"] == (300.0, 2400.0)
    plugin.episode_detail({"key": key, "season": "2", "episode": "3"})
    assert xbmcgui.OK_DIALOGS[-1] == "Závěr epizody"
    assert calls == [("/tv/321/season/2", {"timeout": 4})]


def test_saved_movie_filter_uses_one_matching_version(plugin, monkeypatch):
    import nameparser
    import xbmc
    import xbmcaddon
    import xbmcplugin

    titles = ["Archa.2004.CZ.720p", "Archa.2004.EN.1080p",
              "Pelisky.1999.CZ.1080p", "Pelisky.1999.EN.2160p", "Vlak.2001.CZ.1080p"]
    vids = ["%d:movie:%016x" % (i, i) for i in range(1, len(titles) + 1)]
    with plugin.open_library() as lib:
        for vid, title in zip(vids, titles):
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
        lib.set_watched([vids[-1]], True)
    criteria = {"language": "CZ", "quality": "FHD", "status": "unwatched"}
    xbmcaddon.SETTINGS["pref_lang"] = "2"  # Obecná preference EN nesmí změnit výsledek filtru.
    choices = iter((1, 2, 1))
    monkeypatch.setattr(plugin.ui, "select", lambda *_args, **_kwargs: next(choices))
    xbmc.BUILTINS.clear()
    plugin.movie_filter_create({"order": "added"})
    assert xbmc.BUILTINS == ["Container.Update(%s)" % plugin.url_for(
        "movies", filtered=1, order="added", **criteria)]
    plugin.movies(dict(criteria, filtered="1"))
    assert [item.label for _, item, _ in xbmcplugin.ITEMS] == ["Uložit tento filtr", "Pelisky (1999)"]
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("play", vid=vids[2])

    xbmc.KEYBOARD_INPUT.append("Nezhlédnuté CZ 1080p")
    plugin.movie_filter_save(criteria)
    with plugin.open_library() as lib:
        chosen = lib.movie_filters()[0]
        snapshot = lib.export_backup(plugin.ADDON_ID)
        assert snapshot["tables"]["saved_movie_filters"][0]["name"] == "Nezhlédnuté CZ 1080p"
    xbmcplugin.ITEMS.clear()
    plugin.movies({"browse": "1"})
    shortcut, label, _ = xbmcplugin.ITEMS[1]
    assert label.label == chosen["name"] and "filter_id=" in shortcut
    xbmcplugin.ITEMS.clear()
    plugin.movies({"filter_id": str(chosen["id"])})
    assert [url for url, _, _ in xbmcplugin.ITEMS] == [plugin.url_for("play", vid=vids[2])]


def test_local_czech_slovak_categories_follow_production_metadata(plugin):
    import nameparser
    import xbmcplugin

    films = [
        (REF, "Pelisky.1999.CZ", ["Czech Republic"]),
        ("2:film:bbbbbbbbbbbbbbbb", "Slovensky.2009.SK", ["Slovensko"]),
        ("3:film:cccccccccccccccc", "Koprodukce.2024.EN", ["CZ", "Slovakia"]),
        ("4:film:dddddddddddddddd", "Dabing.2000.CZ", ["USA"]),
        ("5:film:eeeeeeeeeeeeeeee", "Stary.1984.CZ", ["Československo"]),
    ]
    with plugin.open_library() as lib:
        for vid, title, countries in films:
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            lib.update_meta(lib.get_video(vid)["meta_key"], {"countries": countries})
    plugin.movies({"browse": "1"})
    labels = {item.label for _, item, _ in xbmcplugin.ITEMS}
    assert "Československé filmy" in labels and not {"České filmy", "Slovenské filmy"} & labels
    for origin, indexes in (("CZSK", {0, 1, 2, 4}), ("CZ", {0, 2}), ("SK", {1, 2}), ("CS", {4})):
        xbmcplugin.ITEMS.clear()
        plugin.movies({"origin": origin})
        assert {url for url, _, _ in xbmcplugin.ITEMS} == {
            plugin.url_for("play", vid=films[i][0]) for i in indexes}


def test_catalogue_listings_are_paginated(plugin):
    import xbmcaddon
    import xbmcplugin
    _add_movies_to_catalog(plugin, 5, versions=2)
    xbmcaddon.SETTINGS["list_page_size"] = "2"

    plugin.movies({"order": "title", "browse": "1"})
    movies = [item for item in xbmcplugin.ITEMS if "action=play" in item[0]]
    assert len(movies) == 2
    next_page = xbmcplugin.ITEMS[-1]
    assert next_page[2] is True and next_page[1].props["SpecialSort"] == "bottom"
    assert "page=2" in next_page[0] and "action=movies" in next_page[0]
    assert "(2/3)" in next_page[1].label

    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title", "browse": "1", "page": "3"})
    assert len(xbmcplugin.ITEMS) == 1  # bez zkratek filtrů a bez další stránky
    assert "action=play" in xbmcplugin.ITEMS[0][0]

    del xbmcplugin.ITEMS[:]
    plugin.all_videos({"page": "5"})
    assert len(xbmcplugin.ITEMS) == 2 and all("action=play" in item[0] for item in xbmcplugin.ITEMS)

    xbmcaddon.SETTINGS["list_page_size"] = "0"
    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title"})
    assert len(xbmcplugin.ITEMS) == 5


def test_url_for_matches_urlencode(plugin):
    import urllib.parse
    params = {"key": "movie:pelisky|1999", "q": "Hra o trůny 2x03", "page": 2, "empty": "", "none": None,
              "vid": REF}
    expected = plugin.BASE_URL + "?" + urllib.parse.urlencode(
        dict([(k, v) for k, v in params.items() if v not in (None, "")] + [("action", "search")]))
    assert plugin.url_for("search", **params) == expected


def test_router_loads_only_the_module_of_the_action(plugin):
    import importlib
    import sys
    import xbmcplugin
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "7", "?action=root"])
    assert xbmcplugin.ITEMS and "views_catalog" in sys.modules
    assert not {"views_search", "views_discover", "playback", "views_kodi"} & set(sys.modules)
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "7", "?action=neexistuje"])  # jen zápis do logu
    for action, target in plugin.ROUTES.items():
        module, function = target.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), function)), action
    # jména obrazovek jsou dál dostupná jako plugin.<jméno> (služba na pozadí)
    assert plugin.run_sync is view("views_tools").run_sync
    assert plugin.open_library is view("ui").open_library


def test_catalogue_screens_use_a_constant_number_of_queries(plugin, monkeypatch):
    import sqlite3
    import xbmcplugin
    import db
    _add_movies_to_catalog(plugin, 300, versions=2)
    statements = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", connect)
    for screen, params in ((plugin.movies, {"order": "title"}), (plugin.movies, {"order": "added", "page": "2"}),
                           (plugin.movies, {"language": "CZ", "filtered": "1"}), (plugin.all_videos, {}),
                           (plugin.recent, {}), (plugin.in_progress, {})):
        del statements[:]
        del xbmcplugin.ITEMS[:]
        screen(params)
        # počet dotazů nesmí růst s počtem položek (dřív jeden dotaz na každý film s více verzemi)
        assert len(statements) < 25, (screen.__name__, params, len(statements))
