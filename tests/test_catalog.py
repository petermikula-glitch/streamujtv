import catalog
import csfd
import nameparser
from db import Library


class FakeClient(object):
    def __init__(self, videos, favorites=None, playlists=None):
        self.videos = videos
        self.favorites = favorites or []
        self.playlists = playlists or {}

    def my_videos(self, progress=None):
        if progress:
            progress(1, len(self.videos))
        return list(self.videos)

    def my_favorites(self, progress=None):
        return list(self.favorites)

    def my_playlists(self):
        return [(name, url) for name, (url, _) in self.playlists.items()]

    def fetch_listing(self, url, progress=None):
        for name, (u, items) in self.playlists.items():
            if u == url:
                return items
        return []

    def video_info(self, vid):
        return {"id": vid, "title": "Pelíšky (1999) CZ", "description": "popis"}


def v(vid, title):
    return {"id": vid, "title": title, "thumb": "", "duration": 100}


def make_lib(tmp_path):
    return Library(str(tmp_path / "catalog.db"))


def test_sync_groups_movies_and_shows(tmp_path):
    lib = make_lib(tmp_path)
    client = FakeClient([v("a1", "Matrix (1999) CZ"), v("a2", "Matrix.1999.1080p.EN"),
                         v("b1", "Hra o trůny S01E01"), v("b2", "Hra o truny S01E02 CZ"),
                         v("b3", "Hra o trůny S02E01")])
    stats = catalog.sync_account(client, lib)
    assert stats["total"] == 5 and stats["new"] == 5
    movies = lib.movies()
    assert len(movies) == 1 and movies[0]["video_count"] == 2
    shows = lib.shows()
    assert len(shows) == 1 and shows[0]["video_count"] == 3
    assert lib.seasons(shows[0]["key"]) == [1, 2]


def test_sync_removes_deleted_but_never_wipes_on_empty(tmp_path, monkeypatch):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Matrix (1999)"), v("a2", "Pelíšky (1999)")]), lib)
    import time as _t
    real = _t.time
    monkeypatch.setattr(catalog.time, "time", lambda: real() + 10)
    stats = catalog.sync_account(FakeClient([v("a1", "Matrix (1999)")]), lib)
    assert stats["removed"] == 1 and lib.count_videos() == 1
    stats = catalog.sync_account(FakeClient([]), lib)
    assert stats["removed"] == 0 and lib.count_videos() == 1


def test_playlists_become_collections(tmp_path):
    lib = make_lib(tmp_path)
    client = FakeClient([v("a1", "Matrix (1999)")],
                        playlists={"Sci-fi": ("https://www.streamuj.tv/playlist/1", [v("a1", "Matrix (1999)"),
                                                                                       v("c1", "Alien (1979)")])})
    stats = catalog.sync_account(client, lib, include_playlists=True)
    assert stats["playlists"] == 1
    cols = lib.collections()
    assert cols[0]["name"] == "Sci-fi" and cols[0]["item_count"] == 2


def test_parse_link_lines():
    text = """# komentář
https://www.streamuj.tv/video/0645c382f89879db00bc
Matrix (1999) | https://www.streamuj.tv/video/aaaaaaaaaaaaaaaaaaaa
bbbbbbbbbbbbbbbbbbbb
https://www.streamuj.tv/video/0645c382f89879db00bc
nic tu není
"""
    assert catalog.parse_link_lines(text) == [("0645c382f89879db00bc", ""),
                                              ("aaaaaaaaaaaaaaaaaaaa", "Matrix (1999)"),
                                              ("bbbbbbbbbbbbbbbbbbbb", "")]


def test_import_links_fetches_title(tmp_path):
    lib = make_lib(tmp_path)
    stats = catalog.import_links(FakeClient([]), lib, [("cccccccccccccccccccc", "")])
    assert stats["new"] == 1
    video = lib.get_video("cccccccccccccccccccc")
    assert video["title"] == "Pelíšky" and video["year"] == 1999 and video["source"] == "manual"


def test_enrich_csfd_and_fallback(tmp_path, monkeypatch):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Matrix (1999)"), v("a2", "Pelíšky (1999)")]), lib)

    def fake_find(title, year=0, want_series=False, log=None):
        if title == "Matrix":
            return {"csfd_id": 9499, "title": "Matrix", "year": 1999, "rating": 90, "votes": 10,
                    "genres": ["Sci-Fi"], "countries": ["USA"], "directors": ["Wachowski"], "cast": ["Keanu Reeves"],
                    "plot": "děj", "poster": ""}
        return None
    monkeypatch.setattr(csfd, "find", fake_find)
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=True)
    assert stats["csfd"] == 1 and stats["notfound"] == 1
    m = lib.movies(order="rating")[0]
    assert m["title"] == "Matrix" and m["rating"] == 90 and m["genres"] == ["Sci-Fi"]
    # vyhledávání podle herce
    found_movies, _ = lib.search("keanu")
    assert [x["title"] for x in found_movies] == ["Matrix"]
    assert lib.genres("movie") == [("Sci-Fi", 1)]
    assert lib.decades("movie")[0][0] == 1990


def test_enrich_blocked_csfd_uses_tmdb(tmp_path, monkeypatch):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Matrix (1999)"), v("a2", "Pelíšky (1999)")]), lib)

    def blocked(*a, **k):
        raise csfd.CsfdBlocked("anti-bot")
    monkeypatch.setattr(csfd, "find", blocked)

    class FakeTmdb(object):
        enabled = True

        def find(self, title, year=0, want_series=False):
            return {"tmdb_id": 603, "title": title, "year": year, "rating": 82, "genres": ["Akční"]}
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=True, tmdb=FakeTmdb())
    assert stats["csfd_blocked"] and stats["tmdb"] == 2
    assert all(m["rating_source"] == "tmdb" for m in lib.movies())


def test_enrich_blocked_without_fallback_marks_blocked(tmp_path, monkeypatch):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Matrix (1999)"), v("a2", "Pelíšky (1999)")]), lib)
    calls = []

    def blocked(*a, **k):
        calls.append(1)
        raise csfd.CsfdBlocked("anti-bot")
    monkeypatch.setattr(csfd, "find", blocked)
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=True)
    assert stats["csfd_blocked"] and len(calls) == 1  # po zablokování se další dotazy neposílají
    assert len(lib.unmatched()) == 2


def test_edit_video_and_collections(tmp_path):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Nejaky soubor 123")]), lib)
    key = lib.edit_video("a1", "episode", "Přátelé", season=1, episode=1)
    assert key == "show:" + nameparser.show_key("Přátelé")
    assert len(lib.shows()) == 1 and len(lib.movies()) == 0
    # ručně upravené video synchronizace nepřepíše
    catalog.sync_account(FakeClient([v("a1", "Nejaky soubor 123 jinak")]), lib)
    assert lib.get_video("a1")["title"] == "Přátelé"
    cid = lib.create_collection("Oblíbené")
    lib.add_to_collection(cid, key)
    assert lib.collection_targets(cid) == [key]
    lib.delete_collection(cid)
    assert lib.collections() == []


def test_movie_without_year_joins_existing(tmp_path):
    lib = make_lib(tmp_path)
    catalog.sync_account(FakeClient([v("a1", "Pelíšky (1999) CZ"), v("a2", "Pelisky 720p")]), lib)
    movies = lib.movies()
    assert len(movies) == 1 and movies[0]["video_count"] == 2
