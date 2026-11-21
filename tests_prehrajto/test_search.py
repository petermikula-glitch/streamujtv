"""Hledání v katalogu, na Přehraj.to a v TMDB."""

import pytest

from conftest import fixture
import prehrajto
import provider

from helpers import Fake, REF, view


def test_web_search_paginates_and_lists_playable_videos(plugin, monkeypatch):
    import xbmcaddon
    import xbmcplugin

    xbmcaddon.SETTINGS["search_result_pages"] = "1"

    query = "/hledej/Pel%C3%AD%C5%A1ky%20CZ"
    next_page = "?videoListing-page=2"
    results = fixture("uploads_synthetic.html").replace(
        "/profil/nahrana-videa?videoListing-page=2", query + next_page)
    client = Fake({query + next_page: (results, None, "", 200),
                   query: (results, None, "", 200)})
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)

    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1",
                "?action=provider_search&q=Pel%C3%AD%C5%A1ky+CZ"])
    filter_row, video, _, more = xbmcplugin.ITEMS
    assert "action=online_filters" in filter_row[0]
    assert not video[2] and "action=play" in video[0]
    assert video[1].props["IsPlayable"] == "true"
    assert "provider_save" in video[1].context[0][1]
    assert more[2] and "page=2" in more[0]
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1",
                "?" + more[0].split("?", 1)[1]])
    assert client.calls[-1][0] == prehrajto.BASE + query + next_page
    with pytest.raises(prehrajto.PrehrajtoError):
        client.search_page("Pelíšky CZ", 2, "https://example.com/hledej/Pel%C3%AD%C5%A1ky%20CZ")


def test_search_opens_exact_episode_and_prioritizes_movie_year(plugin):
    from urllib.parse import parse_qs, urlsplit

    import nameparser
    import xbmcplugin

    exact = "2:archa:bbbbbbbbbbbbbbbb"
    other = "3:archa:cccccccccccccccc"
    newer = "4:pelisky:dddddddddddddddd"
    with plugin.open_library() as lib:
        for vid, title in ((other, "Archa.S01E03.CZ"), (exact, "Archa.S02E03.CZ"),
                           (newer, "Pelisky.2026.CZ"), (REF, "Pelisky.1999.CZ")):
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
        lib.record_play(exact, 90, 1800)
    plugin.search({"q": "archa 2x03", "online": "0"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert parse_qs(urlsplit(url).query)["vid"] == [exact]
    assert not folder and "S02E03 · Pokračovat" in item.label
    assert item.tag.data["resumepoint"] == (90.0, 1800.0)
    xbmcplugin.ITEMS.clear()
    plugin.search({"q": "pelisky 1999", "online": "0"})
    assert "(1999)" in xbmcplugin.ITEMS[0][1].label
    assert "(2026)" in xbmcplugin.ITEMS[1][1].label


def test_online_search_merges_verified_matches_and_shows_versions(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import nameparser
    import xbmcplugin

    alt = "2:pelisky:aaaaaaaaaaaaaaaa"
    other = "3:pelisky:bbbbbbbbbbbbbbbb"
    calls = []

    class TmdbSearch:
        enabled = True

        def search_titles(self, query, _page):
            assert query == "Pelíšky"
            return [{"media_type": "movie", "id": 10, "title": query, "release_date": "1999-01-01"},
                    {"media_type": "movie", "id": 11, "title": query, "release_date": "2026-01-01"}], 1

    def provider_search(_client, query, page, cursor):
        calls.append((query, page, cursor))
        return [
            {"id": REF, "title": "Pelisky.1999.CZ", "duration": 6000, "thumb": ""},
            {"id": alt, "title": "Pelisky.1999.EN.1080p", "duration": 6000, "thumb": ""},
            {"id": other, "title": "Pelisky.2026.CZ", "duration": 6000, "thumb": ""},
        ], ""

    monkeypatch.setattr(plugin.ui, "make_tmdb", TmdbSearch)
    monkeypatch.setattr(provider, "search_catalog", provider_search)
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    plugin.search({"q": "Pelíšky 1999", "online": "1"})
    assert len(xbmcplugin.ITEMS) == 6  # filtry, dva tituly a odkazy na úplné výsledky
    local_label = xbmcplugin.ITEMS[1][1]
    assert "Můj katalog, Přehraj.to, TMDB" in local_label.label
    assert xbmcplugin.ITEMS[1][2] and "action=search_title" in xbmcplugin.ITEMS[1][0]
    assert "(2026)" in xbmcplugin.ITEMS[2][1].label
    detail = local_label.context[0][1][len("Container.Update("):-1]
    xbmcplugin.ITEMS.clear()
    plugin.search_title(dict(parse_qsl(urlsplit(detail).query)))
    rows = [dict(parse_qsl(urlsplit(url).query)) for url, _, _ in xbmcplugin.ITEMS]
    assert [row["vid"] for row in rows if row["action"] == "play"] == [REF, alt]
    assert calls == [("Pelíšky 1999", 1, "")]


def test_country_search_and_country_selection_preserve_filters(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import xbmc
    import xbmcplugin
    from tmdb import Tmdb

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Film.1999.CZ", "account", None)
        lib.update_meta(lib.get_video(REF)["meta_key"], {"countries": ["FR"]})
    xbmc.KEYBOARD_INPUT.append("Francie")
    plugin.search({})
    assert [(dict(parse_qsl(urlsplit(url).query))["action"], folder) for url, _, folder in xbmcplugin.ITEMS] == [
        ("settings", False), ("tmdb_refine", False), ("play", False)]
    assert "origin=FR" in xbmcplugin.ITEMS[1][0]

    client = Tmdb("test")
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    calls = []

    def fetch(path, **params):
        if path == "/configuration/countries":
            return [{"iso_3166_1": "FR", "native_name": "France"}]
        assert path == "/discover/movie"
        origin = params["with_origin_country"]
        calls.append((origin, params["with_genres"], params["primary_release_year"]))
        return {"total_pages": 1, "results": [{"id": 123, "title": "Koprodukce"},
                      {"id": 321 if origin == "CZ" else 456, "title": "Film " + origin}]}

    monkeypatch.setattr(client, "_get", fetch)
    choices = iter([0, 1])

    def select(_heading, options, preselect=-1):
        selected = next(choices)
        if selected == 0:
            assert options[0] == "Podle země"
        else:
            assert options[:2] == ["Bez omezení", "Československé filmy"]
            assert options[preselect] == "France"
        return selected

    monkeypatch.setattr(plugin.ui, "select", select)
    xbmc.BUILTINS.clear()
    plugin.tmdb_refine({"kind": "movie", "category": "recent", "genre": "35", "year": "2024", "origin": "FR"})
    update = xbmc.BUILTINS[-1]
    assert update.endswith(",replace)")
    args = dict(parse_qsl(urlsplit(update[len("Container.Update("):-len(",replace)")]).query))
    assert args == {"action": "tmdb_titles", "kind": "movie", "category": "recent", "origin": "CZSK",
                    "genre": "35", "year": "2024"}

    xbmcplugin.ITEMS.clear()
    plugin.tmdb_titles(args)
    assert calls == [("CZ", "35", "2024"), ("SK", "35", "2024")]
    assert len(xbmcplugin.ITEMS) == 5
    assert xbmcplugin.ITEMS[0][1].label == "Upřesnit výběr · Československé filmy · Komedie · 2024"
    assert "action=tmdb_refine" in xbmcplugin.ITEMS[0][0]
    assert "Upřesnit výběr" in dict(xbmcplugin.ITEMS[2][1].context)


def test_actor_search_shows_filmography_after_keyboard(plugin, monkeypatch):
    import xbmc
    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)

    def fetch(path, **params):
        if path == "/search/multi":
            return {"results": [], "total_pages": 1}
        if path == "/search/person":
            assert params["query"] == "Jiří Kodet"
            return {"results": [{"id": 71, "name": "Jiří Kodet"}], "total_pages": 1}
        assert path == "/person/71/combined_credits"
        title = {"id": 12, "title": "Pelíšky", "release_date": "1999-04-08", "media_type": "movie"}
        return {"cast": [title, dict(title), dict(title, id=13, adult=True)]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(provider, "search_catalog", lambda *_: ([], ""))
    xbmc.KEYBOARD_INPUT.append("Jiří Kodet")
    plugin.search({})
    url, item, folder = xbmcplugin.ITEMS[1]
    assert item.label == "Jiří Kodet" and folder
    assert url == plugin.url_for("person_titles", person_id=71)
    xbmcplugin.ITEMS.clear()
    plugin.person_titles({"person_id": "71"})
    assert len(xbmcplugin.ITEMS) == 2
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for(
        "tmdb_movie_versions", tmdb_id=12, title="Pelíšky", original="Pelíšky", year=1999)


def test_search_matches_slovak_alias_and_marks_unverified_year(plugin, monkeypatch):
    import xbmcplugin
    from tmdb import Tmdb

    tmdb = Tmdb("test")
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: tmdb)
    requests = []
    item = {"id": 30, "title": "Místní název", "original_title": "Original Film",
            "media_type": "movie", "release_date": "1999-08-01"}

    def metadata(path, **_):
        if path == "/search/multi":
            return {"results": [item], "total_pages": 1}
        assert path == "/movie/30/alternative_titles"
        return {"titles": [{"iso_3166_1": "SK", "title": "Slovenský názov"}]}

    def fetch(_client, query, *_):
        requests.append(query)
        if query == "Slovenský názov":
            return [{"id": REF, "title": "Slovensky.nazov.1999.CZ.1080p"}], ""
        if query == "Místní název":
            return [{"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Mistni.nazev.CZ.720p"}], ""
        return [], ""

    monkeypatch.setattr(tmdb, "_get", metadata)
    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.search({"q": "Místní název 1999"})
    assert requests == ["Místní název 1999", "Original Film", "Místní název", "Slovenský názov"]
    assert "(1999)" in xbmcplugin.ITEMS[1][1].label
    assert "vid=" in xbmcplugin.ITEMS[1][0] and "TMDB" in xbmcplugin.ITEMS[1][1].label
    assert "Rok neověřen" in xbmcplugin.ITEMS[2][1].label


def test_search_retry_and_filter_suggestions_keep_query_and_playable_local_results(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", None)
    candidate = {"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Pelisky.1999.720p"}
    calls = []

    def fetch(_client, query, *_):
        calls.append(query)
        if len(calls) == 1:
            raise provider.TemporaryError("dočasná chyba")
        return [candidate], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.search({"q": "Pelíšky 1999", "audio": "CZ", "quality": "FHD"})
    assert any("action=play" in url for url, _, _ in xbmcplugin.ITEMS)
    retry = next(url for url, item, _ in xbmcplugin.ITEMS if item.label == "Zopakovat online hledání")
    assert len(calls) == 1
    xbmcplugin.ITEMS.clear()
    plugin.run([plugin.BASE_URL, "1", urlsplit(retry).query])
    relax = next(url for url, item, _ in xbmcplugin.ITEMS if "bez omezení kvality a zahrnout" in item.label)
    args = dict(parse_qsl(urlsplit(relax).query))
    assert args["q"] == "Pelíšky 1999" and args["audio"] == "CZ" and args["unknown_audio"] == "1"
    assert "quality" not in args and "retry" not in args
    before_relax = len(calls)
    xbmcplugin.ITEMS.clear()
    plugin.run([plugin.BASE_URL, "1", urlsplit(relax).query])
    assert len(calls) == before_relax
    assert any("action=search_title" in url for url, _, _ in xbmcplugin.ITEMS)


def test_misspelled_title_suggestion_keeps_requested_year(plugin):
    from urllib.parse import parse_qs, urlsplit

    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelíšky.1999.CZ", "account", None)
    plugin.search({"q": "Pelisly 1999", "online": "0"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert folder and item.label == "Mysleli jste Pelíšky 1999?"
    assert parse_qs(urlsplit(url).query)["q"] == ["Pelíšky 1999"]


def test_exact_online_episode_and_series_browsing_exclude_other_titles(plugin, monkeypatch):
    import xbmcplugin

    def items(_client, _query, *_):
        return ([{"id": REF, "title": "Archa.S02E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Archa.S01E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "3:film:bbbbbbbbbbbbbbbb", "title": "Jiny serial.S02E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "4:film:cccccccccccccccc", "title": "Archa.2020.CZ", "thumb": "", "duration": 6400}], "")

    monkeypatch.setattr(provider, "search_catalog", items)
    plugin.search({"q": "Archa 2x03"})
    assert [url for url, _, _ in xbmcplugin.ITEMS if "action=play" in url] == [plugin.url_for("play", vid=REF)]
    xbmcplugin.ITEMS.clear()
    plugin.provider_search({"q": "Archa", "series": "1", "title": "Archa"})
    assert [url for url, _, _ in xbmcplugin.ITEMS if "action=play" in url] == [
        plugin.url_for("play", vid="2:film:aaaaaaaaaaaaaaaa"), plugin.url_for("play", vid=REF)]


def test_provider_batch_removes_duplicates_and_keeps_retry_cursor(plugin, monkeypatch):
    def page(_client, _query, page, cursor):
        if page == 3:
            assert cursor == "third"
            raise provider.ProviderError("unavailable")
        return ([{"id": "%d:film:%016x" % (page, number)} for number in
                 (range(20) if page == 1 else range(19, 39))], "second" if page == 1 else "third")

    monkeypatch.setattr(view("views_discover"), "_result_pages", lambda: 3)
    monkeypatch.setattr(provider, "search_catalog", page)
    items, next_page, cursor, partial = plugin._provider_page_batch(None, "film")
    assert len(items) == 39 and (next_page, cursor, partial) == (3, "third", True)


def test_provider_filters_try_verified_original_title(plugin, monkeypatch):
    import xbmcplugin

    calls = []
    items = {
        "Místní název 2020": [
            {"id": REF, "title": "Mistni.nazev.2020.CZ.720p", "duration": 5600, "thumb": ""},
            {"id": "2:film:eeeeeeeeeeeeeeee", "title": "Mistni.nazev.2020.CZ.1080p.Trailer",
             "duration": 90, "thumb": ""},
        ],
        "Original Name": [
            {"id": "3:film:bbbbbbbbbbbbbbbb", "title": "Original.Name.2020.CZ.1080p", "duration": 5600, "thumb": ""},
            {"id": "4:film:cccccccccccccccc", "title": "Original.Name.2020.SK.1080p", "duration": 5600, "thumb": ""},
        ],
    }

    class TmdbSearch:
        enabled = True

        def search_titles(self, title, _page):
            return [{"media_type": "movie", "id": 10, "title": title, "original_title": "Original Name",
                     "release_date": "2020-01-01"}], 1

    def fetch(_client, query, _page, _cursor):
        calls.append(query)
        return items[query], ""

    monkeypatch.setattr(plugin.ui, "make_tmdb", TmdbSearch)
    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.provider_search({"q": "Místní název 2020", "audio": "CZ", "quality": "FHD", "hide_previews": "1"})
    assert len(xbmcplugin.ITEMS) == 2
    assert "Český dabing" in xbmcplugin.ITEMS[0][1].label
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("play", vid="3:film:bbbbbbbbbbbbbbbb")
    assert calls == ["Místní název 2020", "Original Name"]

    xbmcplugin.ITEMS.clear()
    plugin.add_provider_result({"id": REF, "title": "Original Name", "duration": 5600, "thumb": ""})
    plugin._flush_items()
    assert "Jazyk neuveden" in xbmcplugin.ITEMS[0][1].label


def test_online_search_keeps_recent_queries(plugin, monkeypatch):
    calls = []

    def batch(_client, query, page=1, cursor=""):
        calls.append(query)
        return [], page, "", False

    class NoTmdb:
        enabled = False
        timeout = 0

    monkeypatch.setattr(view("views_search"), "_provider_page_batch", batch)
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda cached=True: NoTmdb())
    filters = plugin._online_filters({})
    for query in ("Pelíšky", "Samotáři", "Pelíšky", "Samotáři"):
        plugin._online_search(query, query, 0, None, filters)
    assert calls == ["Pelíšky", "Samotáři"]
    plugin._online_search("Pelíšky", "Pelíšky", 0, None, filters, retry=True)
    assert calls == ["Pelíšky", "Samotáři", "Pelíšky"]
