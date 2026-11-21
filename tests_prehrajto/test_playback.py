"""Přehrávání: odkazy, náhradní verze, pokračování, další díl a další část."""

import json

from conftest import fixture
import prehrajto

from helpers import Fake, REF


def test_play_uses_link_cache(plugin, monkeypatch):
    import xbmcplugin
    calls = []

    def streams(self, ref):
        calls.append(ref)
        return [{"lang": "", "quality": "ORIG", "url": "orig:" + ref, "subtitles": {}}], ""
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", streams)
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "resolve", lambda self, u: "https://cdn/orig.mkv")
    for _ in range(2):
        plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1", "?action=play&vid=" + REF])
        assert xbmcplugin.RESOLVED[-1][1].path.startswith("https://cdn/orig.mkv|")
    assert len(calls) == 1  # podruhé z paměti


def test_fallback_quality_is_reported_and_cached(plugin, monkeypatch):
    import xbmcgui
    import xbmcplugin

    client = Fake({"/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60":
                   (fixture("video_synthetic.html").replace("'1080'", "'1072'"), None, "", 200)})
    monkeypatch.setattr(client, "_original", lambda ref: "")
    monkeypatch.setattr(plugin.ui, "make_client", lambda: client)
    for _ in range(2):
        plugin.play({"vid": REF})
        assert xbmcplugin.RESOLVED[-1][0]
        assert "1072p" in xbmcgui.NOTIFICATIONS[-1]


def test_cached_playback_is_retried_once_on_kodi_error(plugin, monkeypatch):
    import importlib.util
    import xbmc
    import xbmcgui
    import xbmcplugin
    from pathlib import Path

    service_path = Path(plugin.__file__).parents[2] / "service.py"
    spec = importlib.util.spec_from_file_location("prehrajto_service", service_path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    tracker = service.PlaybackTracker()
    xbmc.BUILTINS.clear()
    calls = []

    def streams(self, ref):
        calls.append(ref)
        return [{"lang": "", "quality": "FHD", "url": "https://cdn/new.mp4", "subtitles": {}}], ""

    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", streams)
    plugin._link_cache_put(REF, "https://cdn/expired.mp4", {}, None)
    with plugin.open_library() as lib:
        lib.record_play(REF, 120, 600)
    plugin.play({"vid": REF})
    assert xbmcplugin.RESOLVED[-1][1].path.startswith("https://cdn/expired.mp4|")

    tracker.onPlayBackError()
    assert xbmc.BUILTINS == ["PlayMedia(%s,resume)" % plugin.url_for("play", vid=REF, retry=1)]
    assert plugin._link_cache_get(REF) is None
    plugin.play({"vid": REF, "retry": "1"})
    ok, item = xbmcplugin.RESOLVED[-1]
    assert ok and item.path.startswith("https://cdn/new.mp4|")
    assert item.tag.data["resumepoint"] == (120.0, 600.0)
    assert calls == [REF]

    tracker.onPlayBackError()
    assert len(xbmc.BUILTINS) == 1
    assert plugin._link_cache_get(REF) is None
    assert "nepodařilo spustit" in xbmcgui.NOTIFICATIONS[-1]

    plugin.play({"vid": REF})
    monkeypatch.setattr(tracker, "getPlayingFile", lambda: xbmcplugin.RESOLVED[-1][1].path, raising=False)
    monkeypatch.setattr(xbmc.Player, "PLAYING", True)
    monkeypatch.setattr(xbmc.Player, "TIME", 120.0)
    monkeypatch.setattr(xbmc.Player, "TOTAL", 600.0)
    tracker.onAVStarted()
    assert tracker.vid == REF
    tracker.onPlayBackError()
    assert len(xbmc.BUILTINS) == 1  # chyba v průběhu běžícího videa není chyba při startu
    with plugin.open_library() as lib:
        assert lib.resume_points()[REF] == (120.0, 600.0)


def test_film_plays_preferred_file_and_offers_stream_selection(plugin, monkeypatch):
    import xbmcaddon
    import xbmcplugin
    import nameparser

    en = "1:film-en:0123456789abcdef"
    cz = "2:film-cz:1123456789abcdef"
    xbmcaddon.SETTINGS.update(pref_lang="0", pref_quality="0")
    with plugin.open_library() as lib:
        for vid, title, size, quality in [(en, "Pelisky.1999.EN.2160p", "7 GB", "2160p"),
                                          (cz, "Pelisky.1999.CZ", "4.2 GB", "1080p")]:
            lib.upsert_video(vid, title, "account", nameparser.parse(title), size=size, quality=quality)
        key = lib.movies()[0]["key"]
    plugin.movies({})
    url, item, folder = xbmcplugin.ITEMS[-1]
    assert url == plugin.url_for("play", vid=cz) and not folder
    assert item.context[0] == ("Vybrat jinou verzi", "Container.Update(%s)" % plugin.url_for("versions", key=key))

    xbmcplugin.ITEMS.clear()
    plugin.versions({"key": key})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("play", vid=cz) and not folder
    assert item.label == "CZ dabing · 1080p · 4.2 GB · Nahraný soubor"
    assert item.context[0] == ("Vybrat stream", "PlayMedia(%s)" % plugin.url_for("play", vid=cz, choose=1))

    def select(heading, options):
        assert options == ["CZ dabing · 1080p · 4.2 GB · Originál (Premium) · Titulky: CZ",
                           "CZ dabing · 1072p · Překódovaná verze"]
        return 1

    streams = [{"lang": "", "quality": "ORIG", "url": "orig:" + cz, "subtitles": {"CZ": "s.vtt"}},
               {"lang": "", "quality": "FHD", "url": "https://cdn/1072.mp4", "resolution": "1072p"}]
    monkeypatch.setattr(plugin.ui, "select", select)
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", lambda self, vid: (streams, ""))
    plugin._link_cache_put(cz, "https://cdn/cached.mp4", {}, None)
    plugin.play({"vid": cz, "choose": "1"})
    assert xbmcplugin.RESOLVED[-1][1].path.startswith("https://cdn/1072.mp4|")
    assert plugin._link_cache_get(cz)["url"] == "https://cdn/cached.mp4"


def test_next_episode_offer_only_on_natural_end_and_resumes(plugin, monkeypatch):
    from pathlib import Path
    import importlib.util

    import nameparser
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcplugin

    path = Path(plugin.__file__).parents[2] / "service.py"
    spec = importlib.util.spec_from_file_location("prehrajto_service", path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    tracker = service.PlaybackTracker()
    monkeypatch.setattr(tracker, "isPlaying", lambda: False, raising=False)
    xbmcgui.WINDOW_PROPERTIES.clear()
    xbmc.BUILTINS.clear()
    with plugin.open_library() as lib:
        refs = []
        for index, suffix in enumerate(("S01E01.CZ", "S01E02.CZ", "S02E01.CZ"), start=1):
            vid = "%d:serial:%016x" % (index, index)
            title = "Test.Serial." + suffix
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            refs.append(vid)
        lib.set_watched([refs[1]], True)
        lib.record_play(refs[2], 120, 600, started=True)
    tracker.vid, tracker.total = refs[0], 600
    tracker.onPlayBackEnded()
    assert tracker.next_offer is None  # nabídka je ve výchozím stavu vypnutá

    xbmcaddon.SETTINGS["offer_next_episode"] = "true"
    tracker.vid, tracker.total = refs[0], 600
    tracker.onPlayBackStopped()
    assert tracker.next_offer is None

    tracker.vid, tracker.total, tracker.position = refs[0], 600, 250
    tracker.onPlayBackEnded()  # výpadek streamu uprostřed dílu Kodi hlásí také jako konec
    assert tracker.next_offer is None
    with plugin.open_library() as lib:
        assert lib.resume_points([refs[0]]) == {refs[0]: (250.0, 600.0)} and not lib.watched([refs[0]])

    tracker.vid, tracker.total, tracker.position = refs[0], 600, 596
    tracker.onPlayBackEnded()
    with plugin.open_library() as lib:
        assert lib.watched([refs[0]]) == {refs[0]}
    xbmcgui.YESNO_ANSWERS.append(True)
    tracker.offer_next_if_due()
    tracker.offer_next_if_due()
    url = plugin.url_for("play", vid=refs[2], continue_play=1)
    assert xbmc.BUILTINS == ["PlayMedia(%s,resume)" % url]
    plugin._link_cache_put(refs[2], "https://cdn/next.mp4", {}, None)
    plugin.play({"vid": refs[2], "continue_play": "1", "restart": "1"})
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (120.0, 600.0)


def test_resume_between_movie_versions_through_kodi_route_and_retry(plugin, monkeypatch):
    from urllib.parse import urlsplit

    import nameparser
    import xbmc
    import xbmcplugin

    alt = "2:pelisky-en:bbbbbbbbbbbbbbbb"
    with plugin.open_library() as lib:
        for vid, title, duration in ((REF, "Pelisky.1999.CZ.1080p", 6000),
                                     (alt, "Pelisky.1999.EN.720p", 6050)):
            lib.upsert_video(vid, title, "account", nameparser.parse(title), duration=duration)
        key = lib.get_video(REF)["meta_key"]
        lib.record_play(REF, 1120, 6000)
        lib.record_play(alt, 10, 6050)
    plugin.versions({"key": key})
    url, item, folder = next((url, item, folder) for url, item, folder in xbmcplugin.ITEMS
                             if url == plugin.url_for("play", vid=alt, offer_transfer=1))
    assert not folder
    assert item.tag.data["resumepoint"] == (1120.0, 6050.0)
    assert "Pokračovat z jiné verze" in [label for label, _ in item.context]

    plugin._link_cache_put(alt, "https://cdn/alt.mp4", {}, None)
    argv = ["plugin://%s/" % plugin.ADDON_ID, "1", "?" + urlsplit(url).query]
    plugin.run(argv + ["resume:false"])
    assert "resumepoint" not in xbmcplugin.RESOLVED[-1][1].tag.data
    plugin.run(argv + ["resume:true"])
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (1120.0, 6050.0)

    import xbmcgui
    pending = json.loads(xbmcgui.WINDOW_PROPERTIES[plugin.PLAYING_PROPERTY])
    xbmc.BUILTINS.clear()
    plugin.retry_failed_playback(pending)
    retry_url = plugin.url_for("play", vid=alt, retry=1, transferred_position=1120.0, transferred_total=6050)
    assert xbmc.BUILTINS == ["PlayMedia(%s,resume)" % retry_url]
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", lambda *_args: (
        [{"lang": "EN", "quality": "HD", "url": "https://cdn/fresh.mp4", "subtitles": {}}], ""))
    plugin.run(argv[:2] + ["?" + urlsplit(retry_url).query, "resume:false"])
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (1120.0, 6050.0)
    with plugin.open_library() as lib:
        assert lib.resume_points() == {REF: (1120.0, 6000.0)}


def test_transfer_confirms_different_runtime_and_matches_exact_episode(plugin, monkeypatch):
    import nameparser
    import xbmcplugin

    target = "2:second:bbbbbbbbbbbbbbbb"
    other_episode = "3:third:cccccccccccccccc"
    with plugin.open_library() as lib:
        for vid, title, duration in ((REF, "Archa.S01E02.CZ", 2400),
                                     (target, "Archa.S01E02.EN", 1800),
                                     (other_episode, "Archa.S02E02.CZ", 2400)):
            lib.upsert_video(vid, title, "account", nameparser.parse(title), duration=duration)
        lib.record_play(REF, 300, 2400)
    plugin._link_cache_put(target, "https://cdn/target.mp4", {}, None)
    plugin._link_cache_put(other_episode, "https://cdn/other.mp4", {}, None)
    decisions = iter((False, True))
    prompts = []

    def confirm(message, heading=None):
        prompts.append((message, heading))
        return next(decisions)

    monkeypatch.setattr(plugin.ui, "yesno", confirm)
    plugin.play({"vid": target, "transfer": "1", "restart": "1"})
    assert not xbmcplugin.RESOLVED[-1][0]
    plugin.play({"vid": target, "transfer": "1", "restart": "1"})
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (300.0, 1800.0)
    assert all("0:40:00" in message and "0:30:00" in message and "0:05:00" in message
               for message, _ in prompts)
    plugin.play({"vid": other_episode, "transfer": "1"})
    assert not xbmcplugin.RESOLVED[-1][0]
    plugin.run(["plugin://%s/" % plugin.ADDON_ID, "1", "?action=play&vid=" + other_episode, "resume:true"])
    assert xbmcplugin.RESOLVED[-1][0]
    assert "resumepoint" not in xbmcplugin.RESOLVED[-1][1].tag.data
    assert len(prompts) == 2


def test_playback_cache_and_cookies_follow_account(plugin):
    import xbmcaddon

    original = plugin.make_client().cookie_file
    plugin._link_cache_put(REF, "http://example.com/stream.mp4", {}, 0)
    assert plugin._link_cache_get(REF)
    xbmcaddon.SETTINGS["username"] = "other@example.com"
    assert plugin.make_client().cookie_file != original
    assert plugin._link_cache_get(REF) is None


def test_next_film_uses_tmdb_collection_order_and_local_availability(plugin, monkeypatch):
    import nameparser
    import xbmc
    import xbmcgui
    import xbmcplugin
    from tmdb import Tmdb

    next_vid = "2:next:bbbbbbbbbbbbbbbb"
    client = Tmdb("test")

    def fetch(path, **kwargs):
        if path == "/movie/10":
            return {"id": 10, "title": "Série", "release_date": "2000-01-01", "belongs_to_collection": {"id": 4}}
        assert path == "/collection/4"
        return {"parts": [{"id": 12, "title": "Třetí", "release_date": "2008-01-01"},
                          {"id": 10, "title": "Série", "release_date": "2000-01-01"},
                          {"id": 11, "title": "Druhá", "release_date": "2005-01-01"}]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda: client)
    with plugin.open_library() as lib:
        for vid, title, tmdb_id in ((REF, "Serie.2000.CZ", 10), (next_vid, "Druha.2005.CZ", 11)):
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            lib.update_meta(lib.get_video(vid)["meta_key"], {"tmdb_id": tmdb_id})
        lib.record_play(next_vid, 90, 3600)
        lib.set_watched([REF], True)

    plugin.tmdb_title({"kind": "movie", "tmdb_id": "10"})
    assert "Další části" in {item.label for _, item, _ in xbmcplugin.ITEMS}
    xbmcplugin.ITEMS.clear()
    plugin.movie_parts({"collection_id": "4", "tmdb_id": "10"})
    assert len(xbmcplugin.ITEMS) == 4
    assert xbmcplugin.ITEMS[0][0] == plugin.url_for("play", vid=next_vid, continue_play=1)
    assert "První nezhlédnutá část" in xbmcplugin.ITEMS[0][1].label
    assert "Zhlédnuté" in xbmcplugin.ITEMS[1][1].label
    assert "Rozkoukáno" in xbmcplugin.ITEMS[2][1].label
    assert xbmcplugin.ITEMS[3][2]  # třetí film vede k online hledání
    xbmcgui.WINDOW_PROPERTIES.clear()
    xbmcgui.YESNO_ANSWERS.append(True)
    xbmc.BUILTINS.clear()
    plugin.offer_next_movie(REF, type("IdlePlayer", (), {"isPlaying": lambda self: False})())
    assert xbmc.BUILTINS == ["PlayMedia(%s,resume)" % plugin.url_for("play", vid=next_vid, continue_play=1)]


def test_failed_playback_offers_only_another_version_of_same_episode(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import nameparser
    import xbmc
    import xbmcplugin

    next_video = "2:film:bbbbbbbbbbbbbbbb"
    other_episode = "3:film:cccccccccccccccc"
    with plugin.open_library() as lib:
        for vid, title, duration in ((REF, "Archa.S01E02.CZ", 2400),
                                     (next_video, "Archa.S01E02.SK", 1800),
                                     (other_episode, "Archa.S01E03.CZ", 2400)):
            lib.upsert_video(vid, title, "account", nameparser.parse(title), duration=duration)
        lib.record_play(REF, 300, 2400)
    watched_options = []

    def choose(_heading, options):
        watched_options.extend(options)
        return 0

    prompts = []
    monkeypatch.setattr(plugin.ui, "select", choose)
    monkeypatch.setattr(plugin.ui, "yesno", lambda prompt, **_kwargs: prompts.append(prompt) or True)
    xbmc.BUILTINS.clear()
    plugin.playback_failed(REF, "Video je nedostupné")
    assert len(watched_options) == 1 and "S01E02.SK" in watched_options[0]
    assert "0:40:00" in prompts[0] and "0:30:00" in prompts[0]
    command = xbmc.BUILTINS[-1]
    params = dict(parse_qsl(urlsplit(command[len("PlayMedia("):-len(",noresume)")]).query))
    assert params["vid"] == next_video and params["replacement"] == "1"
    plugin._link_cache_put(next_video, "https://cdn/alt.mp4", {}, None)
    plugin.play(params)
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (300.0, 1800.0)
    plugin.playback_failed(next_video, "Přehrávání se nezdařilo", replacement=True)
    assert len(xbmc.BUILTINS) == 1


def _load_service(plugin):
    import importlib.util
    from pathlib import Path
    path = Path(plugin.__file__).parents[2] / "service.py"
    spec = importlib.util.spec_from_file_location("prehrajto_service", path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    return service


def test_switching_videos_cannot_overwrite_the_previous_position(plugin, monkeypatch):
    import nameparser
    import xbmc
    import xbmcgui
    other = "2:film-b:00000000000000bb"
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Film.A.1999.CZ", "account", nameparser.parse("Film.A.1999.CZ"))
        lib.upsert_video(other, "Film.B.2001.CZ", "account", nameparser.parse("Film.B.2001.CZ"))
    plugin._link_cache_put(REF, "https://cdn/a.mp4", {}, None)
    plugin._link_cache_put(other, "https://cdn/b.mp4", {}, None)
    service = _load_service(plugin)
    tracker = service.PlaybackTracker()
    monkeypatch.setattr(xbmc.Player, "PLAYING", True)

    plugin.play({"vid": REF})
    monkeypatch.setattr(xbmc.Player, "FILE", "https://cdn/a.mp4|User-Agent=x")
    monkeypatch.setattr(xbmc.Player, "TIME", 3000.0)
    monkeypatch.setattr(xbmc.Player, "TOTAL", 6000.0)
    tracker.onAVStarted()
    tracker.update()
    assert (tracker.vid, tracker.position) == (REF, 3000.0)

    # uživatel pustí film B: přehrávač už hlásí čas B, ale onAVStarted ještě nepřišlo
    plugin.play({"vid": other})
    monkeypatch.setattr(xbmc.Player, "FILE", "https://cdn/b.mp4|User-Agent=x")
    monkeypatch.setattr(xbmc.Player, "TIME", 0.0)
    tracker.update()
    assert tracker.position == 3000.0
    tracker.onPlayBackStarted()
    assert tracker.vid is None
    assert xbmcgui.Window(10000).getProperty(plugin.PLAYING_PROPERTY)  # záznam pro B zůstal
    tracker.onAVStarted()
    assert tracker.vid == other
    with plugin.open_library() as lib:
        assert lib.resume_points([REF]) == {REF: (3000.0, 6000.0)}


def test_play_records_history_under_the_catalogue_id(plugin):
    import nameparser
    import xbmcgui
    import json
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Film.A.1999.CZ", "account", nameparser.parse("Film.A.1999.CZ"))
    plugin._link_cache_put(REF, "https://cdn/a.mp4", {}, None)
    plugin.play({"vid": ":" + REF.split(":", 1)[1]})  # starší tvar bez čísla videa (.strm, oblíbené)
    pending = json.loads(xbmcgui.Window(10000).getProperty(plugin.PLAYING_PROPERTY))
    assert pending["vid"] == REF


def test_auto_sync_runs_outside_the_service_loop(plugin, monkeypatch):
    import threading
    import xbmc
    service = _load_service(plugin)
    started, release, ran_in = threading.Event(), threading.Event(), []

    def slow_sync():
        ran_in.append(threading.current_thread().name)
        started.set()
        release.wait(5)

    monkeypatch.setattr(service, "run_sync", slow_sync)
    monkeypatch.setattr(service, "sync_due", lambda: True)
    worker = service.SyncWorker()
    player = service.PlaybackTracker()
    monkeypatch.setattr(xbmc.Player, "PLAYING", True)
    assert worker.start_if_due(player) is False  # během přehrávání se nezačíná
    monkeypatch.setattr(xbmc.Player, "PLAYING", False)
    assert worker.start_if_due(player) is True
    assert started.wait(5) and worker.running()
    assert worker.start_if_due(player) is False  # druhá se nespustí, smyčka služby běží dál
    release.set()
    worker.thread.join(5)
    assert not worker.running() and ran_in == ["prehrajto-sync"]


def test_film_finished_on_another_version_leaves_in_progress(plugin):
    import nameparser
    import xbmcplugin
    other = "2:pelisky-1999-sk:00000000000000bb"
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"), duration=6000)
        lib.upsert_video(other, "Pelisky.1999.SK", "account", nameparser.parse("Pelisky.1999.SK"), duration=6000)
        lib.record_play(REF, 1200, 6000, now=1000)
        lib.record_play(other, 6000, 6000, now=2000)
    plugin.in_progress({})
    assert not [url for url, _item, _folder in xbmcplugin.ITEMS if "action=play" in url]
    with plugin.open_library() as lib:
        lib.record_play(REF, 1500, 6000, now=3000)  # znovu puštěná první verze je zase rozkoukaná
    del xbmcplugin.ITEMS[:]
    plugin.ui._PLAYBACK.clear()
    plugin.in_progress({})
    assert [url for url, _item, _folder in xbmcplugin.ITEMS if "action=play" in url] == [plugin.url_for("play", vid=REF)]
