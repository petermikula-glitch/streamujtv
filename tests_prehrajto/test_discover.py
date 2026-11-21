"""Objevování v TMDB, ověřování dostupnosti a mezipaměť TMDB."""

import time

import pytest

import provider

from helpers import REF


def test_tmdb_news_combines_origin_genre_year_and_pages(plugin, monkeypatch):
    from datetime import date, timedelta

    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    calls = []

    def fetch(path, **params):
        calls.append((path, params))
        page = params["page"]
        return {"total_pages": 3, "results": [{"id": 20 * page + i, "title": "Film %d" % i} for i in range(20)]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    plugin.news_menu({})
    assert "Československé novinky (TMDB)" in {item.label for _, item, _ in xbmcplugin.ITEMS}
    xbmcplugin.ITEMS.clear()
    plugin.tmdb_titles({"category": "recent", "kind": "movie", "origin": "SK", "genre": "35", "year": "2026"})
    assert len(xbmcplugin.ITEMS) == 43  # 40 titulů, dva ovládací prvky a další stránka
    assert "page=3" in xbmcplugin.ITEMS[-1][0] and "origin=SK" in xbmcplugin.ITEMS[-1][0]
    assert [args["page"] for _, args in calls] == [1, 2]
    for path, args in calls:
        assert path == "/discover/movie"
        assert args["with_origin_country"] == "SK" and args["with_genres"] == "35"
        assert args["primary_release_year"] == "2026"
        assert args["primary_release_date.gte"] == (date.today() - timedelta(days=90)).isoformat()
        assert args["primary_release_date.lte"] == date.today().isoformat()


def test_discover_query_uses_decade_country_genre_and_keeps_local_titles(plugin, monkeypatch):
    import xbmcplugin
    from tmdb import Tmdb

    for index, title in enumerate(("Stare.kino.1985.CZ", "Nove.kino.1995.CZ"), 1):
        with plugin.open_library() as lib:
            video = "%s:film:%016x" % (index, index)
            lib.upsert_video(video, title, "account", None)
            lib.update_meta(lib.get_video(video)["meta_key"], {"genres": ["Komedie"], "countries": ["ČSSR"]})
    client = Tmdb("test")
    calls = []

    def fetch(path, **params):
        assert path == "/discover/movie"
        assert params["with_genres"] == "35"
        assert params["primary_release_date.gte"] == "1980-01-01"
        assert params["primary_release_date.lte"] == "1989-12-31"
        calls.append(params["with_origin_country"])
        return {"results": [], "total_pages": 0}

    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    monkeypatch.setattr(client, "_get", fetch)
    plugin.search({"q": "československé komedie z 80. let"})
    assert calls == ["CZ", "SK"]
    assert "Komedie · 1980–1989" in xbmcplugin.ITEMS[0][1].label
    assert [url for url, _, _ in xbmcplugin.ITEMS if "action=play" in url] == [plugin.url_for("play", vid="1:film:0000000000000001")]
    calls.clear()
    plugin.search({"q": "československé komedie z 80. let", "online": "0"})
    assert not calls


def test_availability_checks_only_a_batch_and_never_confirms_teasers_or_wrong_year(plugin, monkeypatch):
    import xbmcaddon
    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    titles = [{"id": n, "title": title, "release_date": "1999-01-01", "media_type": "movie"}
              for n, title in enumerate(("První film", "Druhý film", "Třetí film", "Čtvrtý film", "Pátý film"), 1)]
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    monkeypatch.setattr(client, "acting_credits", lambda _: titles)
    calls = []

    def fetch(_client, query, *_):
        calls.append(query)
        suffix = {"První film": ".1999.1080p", "Druhý film": ".2025.1080p",
                  "Třetí film": ".1999.Trailer", "Čtvrtý film": ".1080p", "Pátý film": ".1999.CZ"}[query]
        return [{"id": REF, "title": query + suffix}], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.person_titles({"person_id": "10"})
    assert len(calls) == 4
    assert sum("Nalezeno na Přehraj.to" in item.label for _, item, _ in xbmcplugin.ITEMS) == 1
    assert any(item.label == "Ověřit další tituly na této stránce" for _, item, _ in xbmcplugin.ITEMS)
    xbmcplugin.ITEMS.clear()
    plugin.person_titles({"person_id": "10", "found_only": "1"})
    assert calls[-1] == "Pátý film" and len(calls) == 5
    assert [item.label.split(" (")[0] for _, item, _ in xbmcplugin.ITEMS] == [
        "Zobrazit všechny tituly", "První film", "Pátý film"]
    plugin.person_titles({"person_id": "10"})
    assert len(calls) == 5
    xbmcaddon.SETTINGS["username"] = "other@example.com"
    plugin.person_titles({"person_id": "10"})
    assert len(calls) == 9


def test_tmdb_responses_are_cached_by_path(monkeypatch):
    import tmdb

    class Cache(dict):
        def get(self, key):
            entry = dict.get(self, key)
            return entry[0] if entry else None

        def put(self, key, value, ttl):
            self[key] = (value, ttl)

    cache = Cache()
    client = tmdb.Tmdb("k" * 32, cache=cache)
    calls = []

    def fetch(path, timeout, params):
        calls.append(path)
        if path.startswith("/movie/9"):
            raise tmdb.TmdbError("TMDB: HTTP 500")
        return {"genres": [{"id": 1, "name": "Drama"}], "path": path}

    monkeypatch.setattr(client, "_fetch", fetch)
    assert client.genres("movie") == client.genres("movie") == [{"id": 1, "name": "Drama"}]
    assert calls == ["/genre/movie/list"]
    assert [ttl for _value, ttl in cache.values()] == [7 * 24 * 3600]
    assert all("api_key" not in key and "kkkk" not in key for key in cache)
    with pytest.raises(tmdb.TmdbError):
        client._get("/movie/9")
    assert len(cache) == 1  # chyby se neukládají
    assert tmdb.cache_ttl("/search/multi") == 3600
    assert tmdb.cache_ttl("/discover/movie") == 6 * 3600
    assert tmdb.cache_ttl("/movie/603") == 24 * 3600


def test_availability_checks_run_in_parallel_and_stop_on_rate_limit(plugin, monkeypatch):
    import threading
    titles = [{"id": n, "title": "Film %d" % n, "release_date": "1999-01-01", "media_type": "movie"}
              for n in range(1, 5)]
    active, peak = [0], [0]
    lock = threading.Lock()

    def fetch(_client, query, *_):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return [{"id": REF, "title": query + ".1999.1080p"}], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    found, more, failed = plugin._check_title_availability(titles, "movie")
    assert found == {"movie:1", "movie:2", "movie:3", "movie:4"} and not more and not failed
    assert peak[0] == plugin.AVAILABILITY_WORKERS

    calls = []

    def limited(_client, query, *_):
        calls.append(query)
        raise provider.TemporaryError("HTTP 429")

    monkeypatch.setattr(provider, "search_catalog", limited)
    titles = [dict(item, id=item["id"] + 10, title="Jiný %d" % item["id"]) for item in titles]
    found, more, failed = plugin._check_title_availability(titles, "movie")
    assert failed and more and not found
    assert len(calls) <= plugin.AVAILABILITY_WORKERS  # po 429 se další dotazy neposílají
