"""Klient TMDB: mezipaměť odpovědí a hledání titulu."""

import pytest


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


def test_tmdb_rejects_and_never_caches_unexpected_shapes(monkeypatch):
    import tmdb

    stored = {}

    class Cache:
        def get(self, key):
            return stored.get(key)

        def put(self, key, value, ttl):
            stored[key] = value

    client = tmdb.Tmdb("k" * 32, cache=Cache())
    monkeypatch.setattr(client, "_fetch", lambda path, timeout, params: [])
    with pytest.raises(tmdb.TmdbError):
        client.genres("movie")
    assert not stored
    assert client.countries() == [] and len(stored) == 1  # číselník zemí je seznam
    # poškozený záznam v mezipaměti se nepoužije
    stored["tmdb:/genre/movie/list?language=cs-CZ"] = []
    monkeypatch.setattr(client, "_fetch", lambda path, timeout, params: {"genres": [{"id": 1, "name": "Drama"}]})
    assert client.genres("movie") == [{"id": 1, "name": "Drama"}]


def test_tmdb_find_rejects_a_clear_mismatch(monkeypatch):
    import tmdb
    client = tmdb.Tmdb("k" * 32)
    results = {"results": [{"id": 7, "title": "Úplně jiný film", "original_title": "Something Else",
                            "release_date": "2015-05-01", "vote_count": 5000}]}
    monkeypatch.setattr(client, "_get", lambda path, **params: results)
    monkeypatch.setattr(client, "details", lambda tmdb_id, want_series=False: {"tmdb_id": tmdb_id})
    assert client.find("Pelíšky", 1999) is None                    # nesedí název ani rok
    assert client.find("Uplne jiny film", 1999) == {"tmdb_id": 7}  # název sedí, rok je v názvu souboru špatně
    assert client.find("Jiný film", 2016) == {"tmdb_id": 7}        # rok o jedna vedle (premiéra v jiné zemi)
    assert client.find("Jiný film") == {"tmdb_id": 7}              # bez roku se chování nemění
