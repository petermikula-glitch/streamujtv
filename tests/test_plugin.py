"""Průchod obrazovkami doplňku s náhradním Kodi."""
import importlib

import pytest

import xbmcaddon
import xbmcgui
import xbmcplugin


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    xbmcaddon.PROFILE_DIR[0] = str(tmp_path)
    xbmcaddon.SETTINGS.clear()
    xbmcaddon.SETTINGS.update({"show_rating": "true", "auto_meta": "false", "use_csfd": "true",
                               "username": "user", "password": "heslo", "location": "1",
                               "pref_lang": "0", "pref_quality": "0", "subtitles": "true"})
    for lst in (xbmcplugin.ITEMS, xbmcplugin.RESOLVED, xbmcgui.NOTIFICATIONS, xbmcgui.OK_DIALOGS):
        del lst[:]
    import kodiutils
    importlib.reload(kodiutils)
    import plugin as p
    importlib.reload(p)
    return p


def run(p, query=""):
    del xbmcplugin.ITEMS[:]
    p.run(["plugin://plugin.video.streamujtv/", "1", "?" + query])
    return [(it[1].label, it[0], it[2]) for it in xbmcplugin.ITEMS]


def seed(p):
    from test_catalog import FakeClient, v
    import catalog
    with p.open_library() as lib:
        catalog.sync_account(FakeClient([v("a1", "Matrix (1999) CZ"), v("b1", "Hra o trůny S01E01"),
                                         v("b2", "Hra o trůny S02E03 CZ")]), lib)
        key = lib.movies()[0]["key"]
        lib.update_meta(key, {"rating": 90, "rating_source": "csfd", "status": "ok", "genres": ["Sci-Fi"]})


def test_root_menu(plugin):
    items = run(plugin)
    labels = [i[0] for i in items]
    assert labels[:3] == ["Filmy", "Seriály", "Nově přidané"]
    assert "Nástroje" in labels


def test_movies_and_shows(plugin):
    seed(plugin)
    items = run(plugin, "action=movies&order=title")
    assert len(items) == 1
    label, url, folder = items[0]
    assert label.startswith("Matrix (1999)") and "90 %" in label
    assert "action=play" in url and folder is False
    shows = run(plugin, "action=shows")
    assert len(shows) == 1 and shows[0][2] is True
    seasons = run(plugin, shows[0][1].split("?", 1)[1])
    assert [s[0] for s in seasons] == ["1. série", "2. série"]
    eps = run(plugin, seasons[1][1].split("?", 1)[1])
    assert eps[0][0].startswith("2x03")
    genres = run(plugin, "action=movie_genres")
    assert genres[0][0] == "Sci-Fi (1)"


def test_search(plugin):
    seed(plugin)
    items = run(plugin, "action=search&q=trůny")
    assert len(items) == 1 and items[0][2] is True


def test_play_picks_preferred_stream(plugin, monkeypatch):
    import streamuj

    def fake_streams(self, vid):
        return ([{"lang": "EN", "quality": "FHD", "url": "https://www.streamuj.tv/video/x?q=1", "subtitles": {}},
                 {"lang": "CZ", "quality": "HD", "url": "https://www.streamuj.tv/video/x?q=2",
                  "subtitles": {"CZ": "https://www.streamuj.tv/s.srt"}}], "")
    monkeypatch.setattr(streamuj.StreamujClient, "get_streams", fake_streams)
    monkeypatch.setattr(streamuj.StreamujClient, "resolve", lambda self, url: "https://s1.streamuj.tv/v/x.mp4")
    run(plugin, "action=play&vid=a1")
    ok, li = xbmcplugin.RESOLVED[-1]
    assert ok and li.path.startswith("https://s1.streamuj.tv/v/x.mp4|User-Agent=")
    assert li.subtitles == ["https://www.streamuj.tv/s.srt"]  # CZ má přednost před lepší kvalitou EN


def test_play_error_is_reported(plugin, monkeypatch):
    import streamuj

    def boom(self, vid):
        raise streamuj.StreamujError("bez premia")
    monkeypatch.setattr(streamuj.StreamujClient, "get_streams", boom)
    run(plugin, "action=play&vid=a1")
    assert xbmcplugin.RESOLVED[-1][0] is False
    assert any("bez premia" in n for n in xbmcgui.NOTIFICATIONS)


def test_export(plugin, tmp_path):
    seed(plugin)
    xbmcaddon.SETTINGS["library_path"] = str(tmp_path / "lib")
    run(plugin, "action=export_library")
    strm = tmp_path / "lib" / "Filmy" / "Matrix (1999)" / "Matrix (1999).strm"
    assert strm.read_text().startswith("plugin://plugin.video.streamujtv/?action=play&vid=a1")
    nfo = (tmp_path / "lib" / "Filmy" / "Matrix (1999)" / "Matrix (1999).nfo").read_text()
    assert "<value>9.0</value>" in nfo
    assert (tmp_path / "lib" / "Serialy" / "Hra o trůny" / "Season 02" / "Hra o trůny S02E03.strm").exists()


def test_every_route_has_handler(plugin):
    import re
    import os
    src = open(os.path.join(os.path.dirname(plugin.__file__), "plugin.py"), encoding="utf-8").read()
    used = set(re.findall(r'url_for\("([a-z_]+)"', src)) | set(re.findall(r'add_(?:dir|action)\([^)]*?"([a-z_]+)"', src))
    assert used - set(plugin.ROUTES) == set()


def test_search_keyboard_redirects(plugin):
    import xbmc
    xbmc.KEYBOARD_INPUT.append("matrix")
    del xbmc.BUILTINS[:]
    run(plugin, "action=search")
    assert any("Container.Update" in b and "q=matrix" in b for b in xbmc.BUILTINS)
