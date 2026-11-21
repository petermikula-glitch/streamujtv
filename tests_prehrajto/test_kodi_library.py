"""Export do knihovny Kodi a sdílená historie."""

import json

from helpers import REF


def test_sync_exports_incrementally_and_scans_only_pending_changes(plugin, monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from xml.etree import ElementTree

    import xbmc
    import xbmcaddon

    root = tmp_path / "kodi-export"
    xbmcaddon.SETTINGS.update(library_path=str(root), library_auto_export="true", library_auto_scan="true")
    episode = "2:episode:bbbbbbbbbbbbbbbb"
    new_episode = "3:episode:cccccccccccccccc"

    class Client:
        listing_complete = False
        videos = [{"id": REF, "title": "Pelisky.1999.CZ", "duration": 6000},
                  {"id": episode, "title": "Archa.S01E01.CZ", "duration": 2880}]

        def my_videos(self, progress):
            return self.videos

    client = Client()
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)
    xbmc.BUILTINS.clear()
    assert plugin.run_sync()["incomplete"]
    assert not root.exists() and not xbmc.BUILTINS

    client.listing_complete = True
    plugin.run_sync(background=True)
    movie = root / "Filmy" / "Pelisky (1999)" / "Pelisky (1999)"
    season = root / "Serialy" / "Archa" / "Season 01"
    assert parse_qs(urlsplit(movie.with_suffix(".strm").read_text()).query) == {"action": ["play"], "vid": [REF]}
    assert ElementTree.parse(season / "Archa S01E01.nfo").findtext("episode") == "1"
    assert ElementTree.parse(season / "Archa S01E01.nfo").findtext("runtime") == "48"
    assert xbmc.BUILTINS == ["UpdateLibrary(video)"]

    old_mtime = movie.with_suffix(".strm").stat().st_mtime_ns
    plugin.run_sync()
    assert xbmc.BUILTINS == ["UpdateLibrary(video)"]
    assert movie.with_suffix(".strm").stat().st_mtime_ns == old_mtime

    custom_nfo = season / "Archa S01E01.nfo"
    custom_nfo.write_text("<episodedetails><title>Vlastní název</title></episodedetails>")
    with plugin.open_library() as lib:
        lib.update_meta(lib.get_video(REF)["meta_key"], {"title": "Pelíšky – upravený název"})
        lib.update_meta(lib.get_video(episode)["meta_key"], {"title": "Archa – upravený název"})
    client.videos.append({"id": new_episode, "title": "Archa.S01E02.CZ"})
    xbmcaddon.SETTINGS["library_auto_scan"] = "false"
    plugin.run_sync(background=True)
    assert xbmc.BUILTINS == ["UpdateLibrary(video)"]
    assert ElementTree.parse(movie.with_suffix(".nfo")).findtext("title") == "Pelíšky – upravený název"
    assert ElementTree.parse(custom_nfo).findtext("title") == "Vlastní název"
    assert parse_qs(urlsplit((season / "Archa S01E02.strm").read_text()).query) == {
        "action": ["play"], "vid": [new_episode]}
    assert ElementTree.parse(season / "Archa S01E02.nfo").findtext("showtitle") == "Archa – upravený název"
    assert len(list((root / "Filmy").iterdir())) == 1

    xbmcaddon.SETTINGS["library_auto_scan"] = "true"
    plugin.run_sync(background=True)
    assert xbmc.BUILTINS == ["UpdateLibrary(video)", "UpdateLibrary(video)"]


def test_export_retries_failed_file_before_scanning(plugin, monkeypatch, tmp_path):
    import nameparser
    import xbmc
    import xbmcaddon

    root = tmp_path / "kodi-export"
    xbmcaddon.SETTINGS["library_auto_scan"] = "true"
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    replace = plugin.library_export.os.replace

    def fail_nfo(src, dst):
        if dst.endswith(".nfo"):
            raise OSError("simulated write failure")
        return replace(src, dst)

    xbmc.BUILTINS.clear()
    with monkeypatch.context() as patch:
        patch.setattr(plugin.library_export.os, "replace", fail_nfo)
        plugin._export_to_kodi(str(root), automatic=True)
    assert list(root.rglob("*.strm")) and not list(root.rglob("*.nfo")) and not list(root.rglob("*.tmp"))
    assert not xbmc.BUILTINS
    plugin._export_to_kodi(str(root), automatic=True)
    assert list(root.rglob("*.nfo")) and xbmc.BUILTINS == ["UpdateLibrary(video)"]


def test_kodi_wizard_checks_sources_and_only_shares_verified_exports(plugin, monkeypatch, tmp_path):
    import nameparser
    import xbmc
    import xbmcaddon
    import xbmcgui

    root = tmp_path / "kodi"
    foreign = tmp_path / "foreign.strm"
    foreign.write_text("plugin://another.addon/?action=play&vid=" + REF)
    ours = root / "Filmy" / "Pelisky (1999)" / "Pelisky (1999).strm"
    native = {"movieid": 7, "file": str(ours), "playcount": 0,
              "resume": {"position": 600, "total": 6200},
              "lastplayed": "2025-02-03 04:05:06"}
    controls = []
    def execute(raw):
        request = json.loads(raw)
        method, params = request["method"], request["params"]
        controls.append((method, params))
        if method == "Files.GetSources":
            result = {"sources": [{"file": str(root / "Filmy") + "/"}]}
        elif method == "VideoLibrary.GetMovies":
            result = {"movies": [native.copy(), dict(native, movieid=8, file=str(foreign))]}
        elif method == "VideoLibrary.GetEpisodes":
            result = {"episodes": []}
        elif method == "VideoLibrary.GetMovieDetails":
            result = {"moviedetails": native.copy()}
        elif method == "VideoLibrary.SetMovieDetails":
            native.update({key: value for key, value in params.items() if key != "movieid"})
            result = "OK"
        else:
            raise AssertionError(method)
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})

    monkeypatch.setattr(xbmc, "executeJSONRPC", execute, raising=False)
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    xbmcgui.BROWSE_ANSWERS.append(str(root))
    plugin.kodi_choose_folder({})
    assert ours.is_file()
    assert "Přidat videa" in xbmcgui.OK_DIALOGS[-1]
    plugin.kodi_status({})
    assert "Zdroj filmů — nalezen" in xbmcgui.OK_DIALOGS[-1]
    assert "Kodi načetlo 1 z 1" in xbmcgui.OK_DIALOGS[-1]

    xbmcaddon.SETTINGS["library_share_history"] = "true"
    assert plugin._import_kodi_history(force=True) == 1
    with plugin.open_library() as lib:
        assert lib.resume_points() == {REF: (600, 6200)}
        lib.record_play(REF, 900, 6200)
    assert plugin._import_kodi_history(force=True) == 0
    plugin.publish_kodi_history({REF})
    assert native["resume"] == {"position": 900.0, "total": 6200.0}
    assert {params["movieid"] for method, params in controls if method == "VideoLibrary.SetMovieDetails"} == {7}

    native["playcount"] = 1
    plugin.import_kodi_marks({("movie", 7): True})
    with plugin.open_library() as lib:
        assert lib.watched() == {REF}
    native["playcount"] = 0
    plugin.import_kodi_marks({("movie", 7): False})
    with plugin.open_library() as lib:
        assert not lib.watched()

    ours.write_text(foreign.read_text())
    with plugin.open_library() as lib:
        lib.record_play(REF, 1200, 6200)
    plugin.publish_kodi_history({REF})
    assert native["resume"]["position"] == 900.0
