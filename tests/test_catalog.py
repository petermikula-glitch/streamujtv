"""Katalog: synchronizace, slučování, záloha, mezipaměť, Chci vidět a hromadné úpravy."""

import json
import time

import pytest

from conftest import fixture
import prehrajto
import provider

from helpers import Fake, REF


def _lib(tmp_path):
    from db import Library
    return Library(str(tmp_path / "c.db"), video_key=provider.video_key, prefer_ref=provider.prefer_ref)


def test_manual_link_then_sync_merges(tmp_path):
    import catalog
    lib = _lib(tmp_path)

    class C(object):
        def video_info(self, ref):
            return {"id": ref, "title": "Pelíšky (1999)"}  # číslo videa stránka neprozradila

        def my_videos(self, progress=None):
            return prehrajto.parse_video_list(fixture("uploads_synthetic.html"))
    catalog.import_links(C(), lib, [(":pelisky:4f9a1b2c3d4e5f60", "")])
    cid = lib.create_collection("Chci vidět")
    lib.add_to_collection(cid, "v::pelisky:4f9a1b2c3d4e5f60")
    lib.record_play(":pelisky:4f9a1b2c3d4e5f60", 600, 1000)
    catalog.sync_account(C(), lib)
    assert lib.count_videos() == 2  # Pelíšky jen jednou + Hra o trůny
    v = lib.get_video(REF)
    assert v is not None and v["size"] == "4.2 GB" and v["quality"] == "1080p"
    assert lib.collection_targets(cid) == ["v:" + REF]
    assert lib.resume_points() == {REF: (600, 1000)}


def test_sync_then_manual_link_is_existing(tmp_path):
    import catalog
    lib = _lib(tmp_path)

    class C(object):
        def video_info(self, ref):
            return {"id": ref, "title": "x"}

        def my_videos(self, progress=None):
            return prehrajto.parse_video_list(fixture("uploads_synthetic.html"))
    catalog.sync_account(C(), lib)
    stats = catalog.import_links(C(), lib, [(":jiny-nazev:4f9a1b2c3d4e5f60", "")])
    assert stats["existing"] == 1 and lib.count_videos() == 2
    assert lib.get_video(REF) is not None


def test_existing_duplicates_merged_on_open(tmp_path):
    from db import Library
    path = str(tmp_path / "d.db")
    plain = Library(path)  # bez klíče – duplicity vzniknou
    import nameparser
    plain.upsert_video(":pelisky:4f9a1b2c3d4e5f60", "Pelíšky", "manual", nameparser.parse("Pelíšky"))
    plain.upsert_video(REF, "Pelíšky", "account", nameparser.parse("Pelíšky"))
    plain.conn.execute("UPDATE videos SET vkey=''")
    plain.conn.execute("PRAGMA user_version=0")
    plain.conn.commit()
    plain.close()
    lib = Library(path, video_key=provider.video_key, prefer_ref=provider.prefer_ref)
    assert [r[0] for r in lib.conn.execute("SELECT id FROM videos")] == [REF]


def test_incomplete_account_listing_cannot_remove_saved_video(plugin, monkeypatch):
    import catalog

    old_ref = ":older-video:12345678abcdef00"
    client = Fake({"/profil/nahrana-videa": (fixture("uploads_synthetic.html"), None, "", 200)})
    monkeypatch.setattr(client, "my_videos", lambda progress: client.fetch_listing(
        prehrajto.UPLOADS_PATH, max_pages=1, progress=progress, delay=0))
    with plugin.open_library() as lib:
        lib.upsert_video(old_ref, "Starší film", "account", catalog._parsed("Starší film"), now=1)
        lib.set_kv("last_sync", 1)
        stats = catalog.sync_account(client, lib)
        assert stats["incomplete"] and stats["total"] == 2 and stats["removed"] == 0
        assert lib.get_video(old_ref)
        assert lib.get_kv("last_sync") == "1"


def test_catalogue_backup_round_trip_and_rollback(plugin, tmp_path):
    import sqlite3

    import catalog
    import xbmcgui

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelíšky", "account", catalog._parsed("Pelíšky"))
        lib.create_collection("Moje kolekce")
        lib.set_kv("link:" + REF, "https://cdn.example/signed-secret")
        snapshot = lib.export_backup(plugin.ADDON_ID)
    xbmcgui.BROWSE_ANSWERS.append(str(tmp_path))
    plugin.backup_catalog({})
    backup = next(tmp_path.glob("*.json"))
    assert "signed-secret" not in backup.read_text()
    assert json.loads(backup.read_text()) == snapshot
    with plugin.open_library() as lib:
        lib.clear()
    xbmcgui.BROWSE_ANSWERS.append(str(backup))
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.restore_catalog({})
    with plugin.open_library() as lib:
        assert lib.export_backup(plugin.ADDON_ID) == snapshot
        assert lib.get_kv("link:" + REF) is None
        snapshot["tables"]["videos"].append(snapshot["tables"]["videos"][0])
        with pytest.raises(sqlite3.IntegrityError):
            lib.restore_backup(snapshot, plugin.ADDON_ID)
        assert lib.get_video(REF)


def test_sync_announces_new_episode_once_per_number(plugin):
    from urllib.parse import parse_qsl, urlsplit

    import catalog
    import nameparser
    import xbmcplugin

    class Client:
        listing_complete = True

        def __init__(self, videos):
            self.videos = videos

        def my_videos(self, progress):
            return self.videos

    titles = ["Archa.S01E01.CZ", "Archa.S01E02.CZ", "Archa.S01E02.EN",
              "Archa.S02E01.CZ", "Archa.S02E02.CZ"]
    ids = ["%d:archa:%016x" % (i, i) for i in range(1, len(titles) + 1)]
    items = [{"id": vid, "title": title} for vid, title in zip(ids, titles)]
    with plugin.open_library() as lib:
        lib.upsert_video(ids[0], titles[0], "account", nameparser.parse(titles[0]))
        key = lib.get_video(ids[0])["meta_key"]
        assert not lib.unread_episode_counts()
        catalog.sync_account(Client(items[:-1]), lib, remove_missing=False)
        catalog.sync_account(Client(items[:-1]), lib, remove_missing=False)
        assert lib.unread_episode_counts() == {key: 2}

    plugin.shows({})
    assert "Nové díly (2)" in xbmcplugin.ITEMS[0][1].label
    xbmcplugin.ITEMS.clear()
    plugin.show({"key": key})
    url, item, _ = xbmcplugin.ITEMS[0]
    assert item.label == "Nové díly (2)"
    with plugin.open_library() as lib:
        assert not lib.unread_episode_counts()
        catalog.sync_account(Client(items), lib, remove_missing=False)
        assert lib.unread_episode_counts() == {key: 1}
    xbmcplugin.ITEMS.clear()
    plugin.new_episodes(dict(parse_qsl(urlsplit(url).query)))
    assert len(xbmcplugin.ITEMS) == 2
    assert [item.label.split(" · ")[0] for _, item, _ in xbmcplugin.ITEMS] == ["S01E02", "S02E01"]


def test_watchlist_ready_uses_local_matches_and_skips_ambiguity(plugin):
    import nameparser
    import xbmcplugin

    with plugin.open_library() as lib:
        for vid, title, tmdb_id in [
            (REF, "Pelisky.1999.CZ", 10),
            ("2:archa:aaaaaaaaaaaaaaaa", "Archa.S01E01.CZ", 20),
            ("3:film:bbbbbbbbbbbbbbbb", "Film.2001.CZ", 30),
            ("4:film:cccccccccccccccc", "Jiny.Film.2001.CZ", 30),
        ]:
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            key = lib.get_video(vid)["meta_key"]
            lib.update_meta(key, {"tmdb_id": tmdb_id})
        lib.watchlist_add("movie", 10, "Pelíšky", 1999, "")
        lib.watchlist_add("tv", 20, "Archa", 2020, "")
        lib.watchlist_add("movie", 30, "Film", 2001, "")
        lib.watchlist_add("movie", 40, "Jinde", 2020, "")
    plugin.watchlist({})
    assert xbmcplugin.ITEMS[0][1].label == "Připravené k přehrání"
    assert len(xbmcplugin.ITEMS) == 5
    xbmcplugin.ITEMS.clear()
    plugin.watchlist({"ready": "1"})
    assert len(xbmcplugin.ITEMS) == 3
    title_cards = {item.label: (url, folder) for url, item, folder in xbmcplugin.ITEMS[1:]}
    assert title_cards["Pelíšky (1999) · Přehrát z katalogu"] == (plugin.url_for("play", vid=REF), False)
    assert any("action=show" in url and folder for url, folder in title_cards.values())


def test_batch_episode_assignment_keeps_progress_and_rolls_back_on_stale_selection(plugin, monkeypatch):
    from db import meta_key_for

    import nameparser
    import xbmc
    import xbmcgui

    second = "2:archiv:bbbbbbbbbbbbbbbb"
    with plugin.open_library() as lib:
        for vid, title in [(REF, "Archiv.A.S01E02.CZ"), (second, "Archiv.B.CZ")]:
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
        lib.record_play(REF, 150, 360)
        cid = lib.create_collection("Archiv")
        lib.add_to_collection(cid, lib.get_video(REF)["meta_key"])
    monkeypatch.setattr(xbmcgui.Dialog, "multiselect", lambda *_args: [0, 1], raising=False)
    xbmc.KEYBOARD_INPUT.extend(["Archiv", "Archa"])
    xbmcgui.NUMERIC_ANSWERS.extend(["2", "3"])
    choices = iter((2, 0))
    monkeypatch.setattr(plugin.ui, "select", lambda *_args: next(choices))
    plugin.batch_episodes({})

    key = meta_key_for("episode", "Archa", 0)
    with plugin.open_library() as lib:
        applied = lib.videos_for_meta(key)
        assert [(video["season"], video["episode"], video["locked"]) for video in applied] == [(2, 2, 1), (2, 3, 1)]
        assert lib.resume_points()[REF] == (150, 360)
        assert lib.collection_targets(cid) == [key]
        lib.edit_video(second, "episode", "Změněno", season=1, episode=1)
        with pytest.raises(ValueError, match="changed"):
            lib.edit_episodes([(applied[0], 8), (applied[1], 9)], "Elsewhere", 3)
        assert lib.get_video(REF)["meta_key"] == key
        assert lib.get_video(REF)["episode"] == 2


def test_stale_account_sync_cannot_restore_removed_videos(tmp_path):
    lib = _lib(tmp_path)
    first = {"id": REF, "title": "Pelíšky"}
    second = {"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Film"}
    started = 1800000000000000000
    assert lib.apply_account_snapshot([first, second], started, True, True) == (2, 0)
    assert lib.apply_account_snapshot([second], started + 1, False, True) == (0, 0)
    assert lib.apply_account_snapshot([second], started + 3, True, True) == (0, 1)
    assert lib.apply_account_snapshot([first], started + 2, True, True) is None
    assert lib.get_video(REF) is None and lib.get_video(second["id"])


def test_watchlist_notifies_only_after_complete_sync_and_remembers_acknowledgement(plugin, monkeypatch):
    import xbmcgui
    import xbmcplugin

    with plugin.open_library() as lib:
        lib.watchlist_add("movie", 10, "Pelíšky", 1999, "")

    class Client:
        listing_complete = False

        def my_videos(self, progress):
            return [{"id": REF, "title": "Pelisky.1999.CZ"}]

    client = Client()
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)
    plugin.run_sync(background=True)
    with plugin.open_library() as lib:
        assert not lib.fresh_watchlist()
    client.listing_complete = True
    plugin.run_sync(background=True)
    assert "Nově dostupné tituly z Chci vidět (1)" in xbmcgui.NOTIFICATIONS
    plugin.watchlist({})
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("play", vid=REF)
    assert "Nově dostupný" in xbmcplugin.ITEMS[1][1].label
    plugin.watchlist_acknowledge({"kind": "movie", "tmdb_id": 10})
    plugin.run_sync(background=True)
    assert xbmcgui.NOTIFICATIONS.count("Nově dostupné tituly z Chci vidět (1)") == 1
    with plugin.open_library() as lib:
        assert not lib.fresh_watchlist()


def test_library_response_cache_expiry_and_trim(tmp_path):
    import db
    with db.Library(str(tmp_path / "c.db")) as lib:
        lib.cache_put("tmdb:/a", {"x": 1}, 10, now=1000)
        assert lib.cache_get("tmdb:/a", now=1005) == {"x": 1}
        assert lib.cache_get("tmdb:/a", now=1011) is None
        for i in range(5):
            lib.cache_put("search:online:%d" % i, [i], 600, now=2000 + i)
        lib.cache_put("tmdb:/b", {"y": 2}, 600, now=2000)
        lib.cache_trim("search:online:", 2)
        keys = [row[0] for row in lib.conn.execute("SELECT key FROM cache ORDER BY key")]
        assert keys == ["search:online:3", "search:online:4", "tmdb:/b"]
        lib.cache_clear("search:online:")
        assert [row[0] for row in lib.conn.execute("SELECT key FROM cache")] == ["tmdb:/b"]


def _old_parse(lib, vid, title, year=0, kind="movie", season=0, episode=0):
    """Vrátí video do stavu, v jakém ho uložila verze se starými pravidly rozpoznávání."""
    import db
    key = db.meta_key_for(kind, title, year)
    lib.conn.execute("UPDATE videos SET kind=?, title=?, year=?, season=?, episode=?, meta_key=? WHERE id=?",
                     (kind, title, year, season, episode, key, vid))
    lib._ensure_meta_for_video(vid)
    lib._cleanup_meta()
    return key


def test_upgrade_refiles_titles_with_corrected_parsing_and_keeps_collections(tmp_path):
    import db
    import nameparser
    path = str(tmp_path / "c.db")
    names = {"1:a:%016x" % 1: "Johnny English (2003) CZ dabing", "2:b:%016x" % 2: "Johnny English Reborn CZ",
             "3:c:%016x" % 3: "Ulice 4012. díl", "4:d:%016x" % 4: "Ulice 4013. díl",
             "5:e:%016x" % 5: "Pelisky.1999.CZ", "6:f:%016x" % 6: "The English Patient (1996)"}
    with db.Library(path) as lib, lib.conn:
        for vid, raw in names.items():
            lib.upsert_video(vid, raw, "account", nameparser.parse(raw))
        ids = list(names)
        old = _old_parse(lib, ids[0], "Johnny", 2003)
        _old_parse(lib, ids[1], "Johnny", 0)
        _old_parse(lib, ids[2], "Ulice 4012 díl")
        _old_parse(lib, ids[3], "Ulice 4013 díl")
        _old_parse(lib, ids[5], "The", 1996)
        lib.conn.execute("UPDATE videos SET locked=1 WHERE id=?", (ids[5],))  # ruční úprava se nemění
        cid = lib.create_collection("Komedie")
        lib.add_to_collection(cid, old)
        lib.record_play(ids[0], 600, 6000)
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "rating": 91})
        lib.conn.execute("PRAGMA user_version = 7")

    with db.Library(path) as lib:
        videos = {v["id"]: v for v in lib.all_videos()}
        assert videos[ids[0]]["meta_key"] == "movie:johnny english|2003"
        assert videos[ids[1]]["meta_key"] == "movie:johnny english reborn|"
        assert [(videos[i]["kind"], videos[i]["meta_key"], videos[i]["episode"]) for i in ids[2:4]] == [
            ("episode", "show:ulice", 4012), ("episode", "show:ulice", 4013)]
        assert videos[ids[5]]["meta_key"] == "movie:the|1996"
        assert lib.collection_targets(cid) == ["movie:johnny english|2003"]
        assert lib.resume_points() == {ids[0]: (600.0, 6000.0)}
        assert lib.get_meta("movie:pelisky|1999")["rating"] == 91  # nedotčené tituly si metadata nechají
        assert lib.get_meta(old) is None and lib.get_meta("movie:johnny english|2003")["status"] == "new"
        assert lib.conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert lib._reparse_unlocked() == 0


def test_retitled_upload_keeps_collection_and_hidden_progress(tmp_path):
    import db
    import nameparser
    vid = "7:matrix:%016x" % 7
    with db.Library(str(tmp_path / "c.db")) as lib:
        lib.upsert_video(vid, "Matrix 1999 CZ", "account", nameparser.parse("Matrix 1999 CZ"))
        cid = lib.create_collection("Sci-fi")
        lib.add_to_collection(cid, "movie:matrix|1999")
        lib.set_progress_hidden("movie:matrix|1999", True)
        lib.upsert_video(vid, "The Matrix (1999) CZ", "account", nameparser.parse("The Matrix (1999) CZ"))
        assert lib.collection_targets(cid) == ["movie:the matrix|1999"]
        assert [meta["key"] for meta in lib.hidden_progress()] == ["movie:the matrix|1999"]
        key = lib.edit_video(vid, "movie", "Matrix", 1999)
        assert lib.collection_targets(cid) == [key]


def test_restore_rejects_wrong_value_types_and_repairs_missing_titles(tmp_path):
    import db
    import nameparser
    with db.Library(str(tmp_path / "c.db")) as lib:
        lib.upsert_video("8:film:%016x" % 8, "Film 2001 CZ", "account", nameparser.parse("Film 2001 CZ"))
        backup = lib.export_backup("addon")
        for table, column, value in (("videos", "season", "x"), ("videos", "raw_title", 5),
                                     ("meta", "genres", "{bad"), ("meta", "genres", "5")):
            broken = json.loads(json.dumps(backup))
            broken["tables"][table][0][column] = value
            with pytest.raises(ValueError):
                lib.restore_backup(broken, "addon")
        odd = json.loads(json.dumps(backup))
        odd["tables"]["meta"][0]["genres"] = '[["vnořený"], "Drama"]'  # neobvyklé položky se jen přeskočí
        lib.restore_backup(odd, "addon")
        assert lib.genres() == [("Drama", 1)]
        without_meta = json.loads(json.dumps(backup))
        without_meta["tables"]["meta"] = []
        lib.restore_backup(without_meta, "addon")
        assert [m["key"] for m in lib.movies()] == ["movie:film|2001"]
        assert lib.genres() == []


def test_yearless_film_lookup_uses_the_index(tmp_path):
    import db
    import nameparser
    with db.Library(str(tmp_path / "c.db")) as lib:
        lib.upsert_video("1:a:%016x" % 1, "Avatar 2009 CZ", "account", nameparser.parse("Avatar 2009 CZ"))
        lib.upsert_video("2:b:%016x" % 2, "Avatar EN", "account", nameparser.parse("Avatar EN"))
        lib.upsert_video("3:c:%016x" % 3, "Avatarka", "account", nameparser.parse("Avatarka"))
        keys = {v["id"]: v["meta_key"] for v in lib.all_videos()}
        assert keys == {"1:a:%016x" % 1: "movie:avatar|2009", "2:b:%016x" % 2: "movie:avatar|2009",
                        "3:c:%016x" % 3: "movie:avatarka|"}
        plan = " ".join(row[3] for row in lib.conn.execute(
            "EXPLAIN QUERY PLAN SELECT key FROM meta WHERE kind='movie' AND key > ? AND key < ? AND key != ?",
            ("movie:a|", "movie:a|￿", "x")))
        assert "SEARCH" in plan


def test_large_key_sets_do_not_exceed_sqlite_parameter_limit(tmp_path):
    import db
    import nameparser
    with db.Library(str(tmp_path / "c.db")) as lib, lib.conn:
        for i in range(12):
            raw = "Film %02d %d CZ" % (i, 1990 + i)
            lib.upsert_video("%d:f:%016x" % (i + 1, i + 1), raw, "account", nameparser.parse(raw))
            raw = "Serial %02d S01E01 CZ" % i
            lib.upsert_video("%d:s:%016x" % (i + 100, i + 100), raw, "account", nameparser.parse(raw))
        lib.conn.setlimit(__import__("sqlite3").SQLITE_LIMIT_VARIABLE_NUMBER, 999) \
            if hasattr(lib.conn, "setlimit") else None
        keys = [m["key"] for m in lib.movies()] + ["movie:neexistuje %d|" % i for i in range(1500)]
        assert [m["key"] for m in lib.movies(keys=keys)] == [m["key"] for m in lib.movies()]
        assert len(lib.movies(keys=keys, limit=5, offset=10)) == 2
        show_keys = [m["key"] for m in lib.shows()] + ["show:neexistuje %d" % i for i in range(1500)]
        assert len(lib.shows(keys=show_keys)) == 12


class _TmdbStub(object):
    enabled = True

    def __init__(self, fail=()):
        import threading
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.calls = []
        self.fail = dict(fail)

    def find(self, title, year=0, want_series=False):
        import tmdb
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(title)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        if title in self.fail:
            raise tmdb.TmdbError(self.fail[title])
        if title.endswith("neznamy"):
            return None
        return {"title": title.title(), "year": year, "tmdb_id": len(title), "rating": 70, "genres": ["Drama"]}


def _catalogue_with_films(tmp_path, count):
    import db
    import nameparser
    lib = db.Library(str(tmp_path / "c.db"))
    for i in range(count):
        raw = "Film %02d %d CZ" % (i, 1990 + i) if i != 3 else "Film neznamy 1993 CZ"
        lib.upsert_video("%d:f:%016x" % (i + 1, i + 1), raw, "account", nameparser.parse(raw))
    return lib


def test_tmdb_only_enrichment_runs_in_parallel_and_writes_in_order(tmp_path):
    import catalog
    lib = _catalogue_with_films(tmp_path, 20)
    stub = _TmdbStub()
    seen = []
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=False, tmdb=stub,
                           progress=lambda index, total, title: seen.append(index) and False)
    assert stats["ok"] == stats["tmdb"] == 19 and stats["notfound"] == 1 and not stats["errors"]
    assert 1 < stub.peak <= catalog.TMDB_WORKERS
    assert seen == sorted(seen) and seen[-1] == 19
    assert lib.get_meta("movie:film 05|1995")["status"] == "ok"
    assert lib.get_meta("movie:film neznamy|1993")["status"] == "notfound"
    assert not lib.meta_to_fetch()
    lib.close()


def test_parallel_enrichment_stops_on_cancel_and_on_invalid_key(tmp_path):
    import catalog
    lib = _catalogue_with_films(tmp_path, 30)
    stub = _TmdbStub()
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=False, tmdb=stub,
                           progress=lambda index, total, title: index >= 8)
    assert stats["cancelled"] and len(stub.calls) == 8 and len(lib.meta_to_fetch()) == 22

    metas = lib.meta_to_fetch()
    stub = _TmdbStub(fail={metas[2]["search_title"]: "TMDB: neplatný API klíč"})
    stats = catalog.enrich(lib, metas, use_csfd=False, tmdb=stub)
    assert stats["errors"] == 1 and len(stub.calls) == catalog.TMDB_WORKERS * 2
    statuses = [lib.get_meta(meta["key"])["status"] for meta in metas]
    assert statuses[2] == "error" and set(statuses[catalog.TMDB_WORKERS * 2:]) == {"blocked"}
    lib.close()


def test_quick_sync_stops_at_known_page_and_never_prunes(plugin, monkeypatch):
    import xbmcaddon

    def page(ids, next_page=None):
        items = "".join('<div data-video-id="%d"><a href="/film-%d/%016x" title="Film %d 2001 CZ">Film %d</a></div>'
                        % (i, i, i, i, i) for i in ids)
        link = '<ul class="pagination"><li><a href="/profil/nahrana-videa?videoListing-page=%d">x</a></li></ul>' \
            % next_page if next_page else ""
        return "<html>%s%s</html>" % (items, link)

    pages = {1: page([9, 8, 7], 2), 2: page([6, 5, 4], 3), 3: page([3, 2, 1])}

    class Client(Fake):
        def _request(self, url, data=None, headers=None, follow=True):
            if "/profil/nahrana-videa" in url:
                self.calls.append((url, data, follow))
                number = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
                return pages[number], url, "", 200
            return super()._request(url, data, headers, follow)

    client = Client({"/profil": ("ok", None, "", 200)})
    client.sleep = lambda seconds: None
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)

    def listing_requests():
        return sum(1 for url, _data, _follow in client.calls if "nahrana-videa" in url)

    assert plugin.run_sync(background=True)["total"] == 9 and listing_requests() == 3
    xbmcaddon.SETTINGS.update(quick_sync="true", remove_missing="true")
    # nové video na první stránce, jedno starší mezitím z účtu zmizelo
    pages[1], pages[3] = page([10, 9, 8], 2), page([3, 2])
    pages[2] = page([7, 6, 5, 4], 3)
    client.calls.clear()
    stats = plugin.run_sync(background=True)
    assert stats["quick"] and not stats["incomplete"] and stats["new"] == 1 and stats["removed"] == 0
    assert listing_requests() == 2  # skončila na druhé stránce, kde už nebylo nic nového
    with plugin.open_library() as lib:
        assert lib.count_videos() == 10
        full = int(lib.get_kv("last_full_sync"))
        lib.set_kv("last_full_sync", full - 25 * 3600)

    client.calls.clear()
    stats = plugin.run_sync(background=True)  # po dni zase úplná: projde vše a odebere smazané
    assert not stats["quick"] and listing_requests() == 3 and stats["removed"] == 1
    client.calls.clear()
    assert not plugin.run_sync(background=False)["quick"] and listing_requests() == 3  # ruční je vždy úplná


def _snapshot(lib, names, started, durations=None):
    videos = [{"id": "%d:v:%016x" % (number, number), "title": raw, "duration": (durations or {}).get(number, 0)}
              for number, raw in names.items()]
    return lib.apply_account_snapshot(videos, started, True, False)


def test_files_numbered_only_by_number_become_a_series_as_a_group(tmp_path):
    lib = _lib(tmp_path)
    names = {1: "Ulice 4521", 2: "Ulice 4522", 3: "Ulice 4523",            # čísla, jaká pokračování filmů nemívají
             4: "Rocky 2", 5: "Rocky 3", 6: "Rocky 4",                     # filmová pokračování: zůstávají filmy
             7: "Kutil 1", 8: "Kutil 2", 9: "Kutil 3",                     # malá čísla, ale délka dílu
             10: "Naruto 045", 11: "Naruto 046",                           # jen dva soubory: zatím nic
             12: "Blade Runner 2049 1080p CZ"}
    durations = {4: 7200, 5: 7200, 6: 7200, 7: 1500, 8: 1500, 9: 1500}
    _snapshot(lib, names, 10, durations)
    videos = {int(v["id"].split(":")[0]): v for v in lib.all_videos()}
    assert [(videos[n]["kind"], videos[n]["meta_key"], videos[n]["season"], videos[n]["episode"]) for n in (1, 2, 3)] == [
        ("episode", "show:ulice", 1, 4521), ("episode", "show:ulice", 1, 4522), ("episode", "show:ulice", 1, 4523)]
    assert all(videos[n]["kind"] == "movie" for n in (4, 5, 6, 10, 11, 12))
    assert [(videos[n]["meta_key"], videos[n]["episode"]) for n in (7, 8, 9)] == [
        ("show:kutil", 1), ("show:kutil", 2), ("show:kutil", 3)]
    assert lib.get_meta("show:ulice")["title"] == "Ulice" and lib.get_meta("movie:ulice 4521|") is None

    # další soubor se k existujícímu seriálu přidá sám; třetí Naruto z dvojice udělá seriál
    cid = lib.create_collection("Anime")
    lib.add_to_collection(cid, "movie:naruto 045|")
    lib.record_play("10:v:%016x" % 10, 300, 1400)
    names.update({13: "Ulice 4524", 14: "Naruto 047", 15: "Ulice 2"})
    _snapshot(lib, names, 20, durations)
    videos = {int(v["id"].split(":")[0]): v for v in lib.all_videos()}
    assert (videos[13]["meta_key"], videos[13]["episode"]) == ("show:ulice", 4524)
    assert [videos[n]["meta_key"] for n in (10, 11, 14)] == ["show:naruto"] * 3
    assert videos[15]["kind"] == "movie"                                   # „Ulice 2“ bez délky: nejisté, nechá se
    assert lib.collection_targets(cid) == ["show:naruto"]                  # kolekce jde s titulem
    assert lib.resume_points() == {"10:v:%016x" % 10: (300.0, 1400.0)}     # rozkoukané zůstává u videa

    # ruční úprava má přednost a přepočet katalogu seskupení nerozbije
    lib.edit_video("1:v:%016x" % 1, "movie", "Ulice film", 2005)
    assert lib._reparse_unlocked() == 0
    videos = {int(v["id"].split(":")[0]): v for v in lib.all_videos()}
    assert videos[1]["meta_key"] == "movie:ulice film|2005" and videos[2]["meta_key"] == "show:ulice"
    lib.close()


def test_upgrade_to_schema_10_reparses_names_and_adds_double_episodes(tmp_path):
    import db
    import nameparser
    path = str(tmp_path / "c.db")
    names = {"1:a:%016x" % 1: "Vratné lahve - celý film CZ HD", "2:b:%016x" % 2: "Přátelé S01E01-E02",
             "3:c:%016x" % 3: "Pán prstenů: Dvě věže / The Lord of the Rings: The Two Towers (2002)",
             "4:d:%016x" % 4: "Ulice 4521", "5:e:%016x" % 5: "Ulice 4522", "6:f:%016x" % 6: "Ulice 4523"}
    with db.Library(path) as lib, lib.conn:
        for vid, raw in names.items():
            lib.upsert_video(vid, raw, "account", nameparser.parse(raw))
        ids = list(names)
        old = _old_parse(lib, ids[0], "Vratné lahve celý film")
        _old_parse(lib, ids[2], "Pán prstenů: Dvě věže / The Lord of the Rings: The Two Towers", 2002)
        for vid, title in zip(ids[3:], ("Ulice 4521", "Ulice 4522", "Ulice 4523")):
            _old_parse(lib, vid, title)
        lib.conn.execute("UPDATE videos SET episode_end=0")
        cid = lib.create_collection("České")
        lib.add_to_collection(cid, old)
        lib.conn.execute("PRAGMA user_version = 9")

    with db.Library(path) as lib:
        videos = {v["id"]: v for v in lib.all_videos()}
        assert videos[ids[0]]["meta_key"] == "movie:vratne lahve|" and lib.collection_targets(cid) == ["movie:vratne lahve|"]
        assert (videos[ids[1]]["episode"], videos[ids[1]]["episode_end"]) == (1, 2)
        assert videos[ids[2]]["meta_key"] == "movie:pan prstenu dve veze|2002"
        assert [videos[vid]["meta_key"] for vid in ids[3:]] == ["show:ulice"] * 3
        assert lib.conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 10


def test_backup_made_before_double_episodes_still_restores(tmp_path):
    lib = _lib(tmp_path)
    lib.upsert_video(REF, "Pratele S01E01-E02", "account", None)
    backup = lib.export_backup("plugin.test")
    assert backup["tables"]["videos"][0]["episode_end"] == 2
    for row in backup["tables"]["videos"]:
        del row["episode_end"]                                             # záloha ze starší verze sloupec nemá
    lib.clear()
    lib.restore_backup(backup, "plugin.test")
    assert lib.get_video(REF)["episode_end"] == 2                          # přepočet po obnově ho doplní
    lib.close()


def test_metadata_lookup_tries_the_other_title_from_the_file_name(tmp_path):
    import catalog
    lib = _lib(tmp_path)
    raw = "Dvě věže neznamy / The Two Towers (2002)"
    lib.upsert_video(REF, raw, "account", None)
    stub = _TmdbStub()
    stats = catalog.enrich(lib, lib.meta_to_fetch(), use_csfd=False, tmdb=stub)
    assert stub.calls == ["Dvě věže neznamy", "The Two Towers"]             # český název nic, původní ano
    assert stats["ok"] == 1 and lib.get_meta("movie:dve veze neznamy|2002")["status"] == "ok"
    lib.close()
