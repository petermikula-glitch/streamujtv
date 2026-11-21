"""Katalog: synchronizace, slučování, záloha, mezipaměť, Chci vidět a hromadné úpravy."""

import json

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
                                     ("meta", "genres", "{bad"), ("meta", "genres", "[[1]]")):
            broken = json.loads(json.dumps(backup))
            broken["tables"][table][0][column] = value
            with pytest.raises(ValueError):
                lib.restore_backup(broken, "addon")
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
