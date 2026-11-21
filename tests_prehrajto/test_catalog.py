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
