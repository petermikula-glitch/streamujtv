"""Historie přehrávání, migrace databáze, diagnostika, služba."""
import importlib.util
import os
import sqlite3

import catalog
import xbmc
import xbmcgui
import xbmcplugin
from db import Library
from test_plugin import plugin, run, seed  # noqa: F401 – fixture

HERE = os.path.dirname(os.path.abspath(__file__))


def test_migration_from_v1(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE videos (id TEXT PRIMARY KEY, raw_title TEXT NOT NULL, source TEXT NOT NULL,
            added_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, duration INTEGER DEFAULT 0,
            thumb TEXT DEFAULT '', description TEXT DEFAULT '', kind TEXT NOT NULL, title TEXT NOT NULL,
            year INTEGER DEFAULT 0, season INTEGER DEFAULT 0, episode INTEGER DEFAULT 0, lang TEXT DEFAULT '',
            quality TEXT DEFAULT '', meta_key TEXT NOT NULL, locked INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0);
        INSERT INTO videos (id, raw_title, source, added_at, last_seen, kind, title, meta_key)
            VALUES ('abc', 'Matrix (1999)', 'account', 1, 1, 'movie', 'Matrix', 'movie:matrix|1999');
    """)
    conn.commit()
    conn.close()
    lib = Library(path)
    v = lib.get_video("abc")
    assert v["vkey"] == "abc" and v["size"] == ""
    lib.record_play("abc", 100, 1000, started=True)
    assert lib.resume_points() == {"abc": (100, 1000)}


def test_history_states(tmp_path):
    lib = Library(str(tmp_path / "c.db"))
    from test_catalog import FakeClient, v
    catalog.sync_account(FakeClient([v("a1", "Matrix (1999)"), v("a2", "Pelíšky (1999)"), v("a3", "Alien (1979)")]), lib)
    lib.record_play("a1", 30, 1000, started=True)     # jen začaté – ne rozkoukané
    lib.record_play("a2", 600, 1000, started=True)    # rozkoukané
    lib.record_play("a3", 990, 1000, started=True)    # dokoukané
    assert set(lib.resume_points()) == {"a2"}
    assert lib.watched() == {"a3"}
    assert [x["id"] for x in lib.history_videos(in_progress=True)] == ["a2"]
    assert len(lib.history_videos()) == 3
    lib.remove_history("a2")
    assert lib.resume_points() == {}


def test_in_progress_menu_sets_resume(plugin):  # noqa: F811
    seed(plugin)
    with plugin.open_library() as lib:
        lib.record_play("a1", 600, 1000, started=True)
    plugin._PLAYBACK.clear()
    items = run(plugin, "action=in_progress")
    assert len(items) == 1 and "vid=a1" in items[0][1]
    li = xbmcplugin.ITEMS[0][1]
    assert li.tag.data["resumepoint"] == (600.0, 1000.0)
    labels = [i[0] for i in run(plugin)]
    assert "Rozkoukané" in labels and "Naposledy přehrané" in labels


def test_play_sets_tracking_property(plugin, monkeypatch):  # noqa: F811
    import streamuj
    monkeypatch.setattr(streamuj.StreamujClient, "get_streams", lambda self, vid: (
        [{"lang": "CZ", "quality": "HD", "url": "https://www.streamuj.tv/video/x", "subtitles": {}}], ""))
    monkeypatch.setattr(streamuj.StreamujClient, "resolve", lambda self, url: "https://s1.streamuj.tv/x.mp4")
    run(plugin, "action=play&vid=a1")
    assert xbmcgui.WINDOW_PROPERTIES["plugin.video.streamujtv.playing"] == "a1"


def test_service_tracker_records_position(plugin):  # noqa: F811
    seed(plugin)
    spec = importlib.util.spec_from_file_location(
        "svc", os.path.join(HERE, "..", "plugin.video.streamujtv", "service.py"))
    svc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(svc)
    xbmcgui.WINDOW_PROPERTIES[svc.PLAYING_PROPERTY] = "a1"
    xbmc.Player.PLAYING, xbmc.Player.TIME, xbmc.Player.TOTAL = True, 10.0, 3000.0
    tracker = svc.PlaybackTracker()
    tracker.onAVStarted()
    assert svc.PLAYING_PROPERTY not in xbmcgui.WINDOW_PROPERTIES
    xbmc.Player.TIME = 1500.0
    tracker.update()
    tracker.onPlayBackStopped()
    xbmc.Player.PLAYING = False
    with plugin.open_library() as lib:
        assert lib.resume_points() == {"a1": (1500.0, 3000.0)}
        assert lib.history_videos()[0]["plays"] == 1


def test_sanitize():
    page = ('<a>uzivatel123</a> mail jan.novak@example.com token=abc123 '
            '<input type="hidden" name="_token_" value="tajne"> eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.c2lnbmF0dXJl '
            'heslo123 pass=x:::y')
    clean = catalog.sanitize(page, ["uzivatel123", "heslo123"])
    for secret in ("uzivatel123", "jan.novak", "abc123", "tajne", "eyJhbGci", "heslo123", "x:::y"):
        assert secret not in clean


def test_diagnostics_writes_sanitized_files(plugin, monkeypatch, tmp_path):  # noqa: F811
    import provider
    monkeypatch.setattr(provider, "diagnostics", lambda client: {"moje_videa.html": "<b>user</b> heslo a@b.cz"})
    xbmcgui.BROWSE_ANSWERS.append(str(tmp_path))
    run(plugin, "action=diagnostics")
    folders = [d for d in os.listdir(str(tmp_path)) if d.startswith("diagnostika-")]
    assert len(folders) == 1
    content = open(os.path.join(str(tmp_path), folders[0], "moje_videa.html"), encoding="utf-8").read()
    assert "user" not in content and "heslo" not in content and "a@b.cz" not in content
