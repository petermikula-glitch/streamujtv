import base64
import http.cookiejar
import importlib
import json
import os
import time

import pytest

import prehrajto
import provider
from conftest import fixture

REF = "1234567:pelisky-1999-cz-1080p:4f9a1b2c3d4e5f60"


def jwt(exp):
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return "x.%s.y" % body


def set_cookie(jar, name, value):
    jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, "prehraj.to", True, False, "/", True,
                                         True, None, False, None, None, {}))


def test_refs():
    assert prehrajto.extract_video_id("https://prehraj.to/pelisky-1999/4f9a1b2c3d4e5f60") == ":pelisky-1999:4f9a1b2c3d4e5f60"
    assert prehrajto.extract_video_id(REF) == REF
    assert prehrajto.extract_video_id("https://prehraj.to/profil/nahrana-videa") is None
    assert provider.find_video_id("Pelíšky | https://prehraj.to/pelisky/4f9a1b2c3d4e5f60") == ":pelisky:4f9a1b2c3d4e5f60"
    assert prehrajto.video_page_url(REF) == "https://prehraj.to/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60"


def test_parse_uploads():
    items = prehrajto.parse_video_list(fixture("uploads_synthetic.html"))
    assert [i["id"] for i in items] == [REF, "7654321:hra-o-trune-s01e02-cz-tit:aa11bb22cc33dd44"]
    assert items[0]["title"] == "Pelíšky (1999) CZ 1080p"
    assert items[0]["duration"] == 1 * 3600 + 55 * 60 + 12
    assert items[0]["thumb"] == "https://thumb.prehraj.to/abc.jpg"
    assert items[1]["title"] == "Hra o trůny S01E02 CZ tit"


def test_parse_pagination():
    pages = prehrajto.parse_pagination(fixture("uploads_synthetic.html"), "https://prehraj.to/profil/nahrana-videa")
    assert pages == ["https://prehraj.to/profil/nahrana-videa?videoListing-page=2"]


def test_parse_video_page():
    page = fixture("video_synthetic.html")
    assert prehrajto.parse_sources(page)[0] == ("FHD", "https://cdn.premiumcdn.net/v/abc_1080.mp4?expires=1")
    assert prehrajto.parse_tracks(page) == {"CZ": "https://cdn.premiumcdn.net/s/abc_cz.vtt"}
    assert prehrajto.parse_video_page(page)["title"] == "Pelíšky (1999) CZ 1080p"


def test_jwt_expired():
    assert not prehrajto.jwt_expired(jwt(time.time() + 600))
    assert prehrajto.jwt_expired(jwt(time.time() - 1))
    assert prehrajto.jwt_expired("nesmysl")


class Fake(prehrajto.PrehrajtoClient):
    """Klient s podvrženým HTTP – routes: cesta → (text, finální url, location, stav)."""

    def __init__(self, routes, login_ok=True, **kw):
        super().__init__("a@b.cz", "heslo", **kw)
        self.routes = routes
        self.login_ok = login_ok
        self.calls = []

    def _request(self, url, data=None, headers=None, follow=True):
        if not url.startswith("http"):
            url = prehrajto.BASE + url
        self.calls.append((url, data, follow))
        if "loginForm" in url and data:
            assert data["email"] == "a@b.cz" and data["_do"] == "loginDialog-login-loginForm-submit"
            if self.login_ok:
                set_cookie(self.cookies, "access_token", jwt(time.time() + 600))
                set_cookie(self.cookies, "refresh_token", "r")
            return "{}", url, "", 200
        for prefix, resp in self.routes.items():
            if url.startswith(prehrajto.BASE + prefix) if prefix.startswith("/") else url.startswith(prefix):
                text, final, location, status = resp
                return text, final or url, location, status
        return "", prehrajto.BASE + "/", "", 200


def test_login_and_uploads():
    page2 = fixture("uploads_synthetic.html").replace("4f9a1b2c3d4e5f60", "0000000000000001").replace(
        "aa11bb22cc33dd44", "0000000000000002")
    c = Fake({
        "/profil/nahrana-videa?videoListing-page=2": (page2, None, "", 200),
        "/profil/nahrana-videa": (fixture("uploads_synthetic.html"), None, "", 200),
    })
    videos = c.my_videos()
    assert len(videos) == 4
    assert any("loginForm" in u for u, _, _ in c.calls)


def test_login_failure():
    c = Fake({}, login_ok=False)
    with pytest.raises(prehrajto.LoginError):
        c.login()


def test_check_user_premium():
    c = Fake({"/profil": ("<b>PREMIUM</b> <span>25 dní</span>", None, "", 200)})
    assert c.check_user() == (prehrajto.CHECK_PREMIUM, 25)
    c2 = Fake({"/profil": ("<a>Odhlásit se</a>", None, "", 200)})
    assert c2.check_user() == (prehrajto.CHECK_NO_PREMIUM, None)


def test_streams_and_resolve_original_via_html():
    c = Fake({
        "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60?do=download": ("", None, "https://cdn.premiumcdn.net/orig.mkv?sig=1", 302),
        "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60": (fixture("video_synthetic.html"), None, "", 200),
    })
    c.api = lambda path: (_ for _ in ()).throw(prehrajto.PrehrajtoError("API nedostupné"))
    streams, _ = c.get_streams(REF)
    assert [s["quality"] for s in streams] == ["ORIG", "FHD", "HD"]
    assert streams[0]["subtitles"] == {"CZ": "https://cdn.premiumcdn.net/s/abc_cz.vtt"}
    assert c.resolve(streams[0]["url"]) == "https://cdn.premiumcdn.net/orig.mkv?sig=1"
    assert c.resolve(streams[1]["url"]) == "https://cdn.premiumcdn.net/v/abc_1080.mp4?expires=1"


def test_resolve_original_via_api():
    c = Fake({})
    c.api = lambda path: {"data": {"download_link": "https://cdn.premiumcdn.net/api.mkv"}} if path == "videos/1234567/download" else {}
    assert c.resolve("orig:" + REF) == "https://cdn.premiumcdn.net/api.mkv"


def test_original_without_premium_falls_back():
    c = Fake({
        "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60?do=download": ("", None, "https://prehraj.to/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60", 302),
        "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60": (fixture("video_synthetic.html"), None, "", 200),
    })
    c.api = lambda path: {"data": {}}
    assert c.resolve("orig:" + REF) == "https://cdn.premiumcdn.net/v/abc_1080.mp4?expires=1"


def test_resolve_rejects_non_http():
    with pytest.raises(prehrajto.PrehrajtoError):
        Fake({}).resolve("file:///etc/passwd")


def test_mask_secrets():
    assert "heslo" not in prehrajto.mask_secrets("password=heslo&x=1")
    assert "abc" not in prehrajto.mask_secrets("Authorization: Bearer abc")


# --- celý doplněk s náhradním Kodi ---------------------------------------------

@pytest.fixture
def plugin(tmp_path):
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
    xbmcaddon.PROFILE_DIR[0] = str(tmp_path)
    xbmcaddon.SETTINGS.clear()
    xbmcaddon.SETTINGS.update({"show_rating": "true", "auto_meta": "false", "username": "a@b.cz",
                               "password": "heslo", "pref_quality": "0", "subtitles": "true"})
    for lst in (xbmcplugin.ITEMS, xbmcplugin.RESOLVED, xbmcgui.NOTIFICATIONS, xbmcgui.OK_DIALOGS):
        del lst[:]
    import kodiutils
    importlib.reload(kodiutils)
    import plugin as p
    importlib.reload(p)
    return p


def test_plugin_sync_browse_play(plugin, monkeypatch):
    import xbmcplugin
    import catalog
    assert os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(plugin.__file__)))) == \
        "plugin.video.prehrajto.mojevidea"

    class C(object):
        def my_videos(self, progress=None):
            return prehrajto.parse_video_list(fixture("uploads_synthetic.html"))
    with plugin.open_library() as lib:
        catalog.sync_account(C(), lib)
    xbmcplugin.ITEMS[:] = []
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1", "?action=movies"])
    labels = [it[1].label for it in xbmcplugin.ITEMS]
    assert labels == ["Pelíšky (1999)"]
    url = xbmcplugin.ITEMS[0][0]
    assert "action=play" in url

    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", lambda self, ref: (
        [{"lang": "", "quality": "ORIG", "url": "orig:" + ref, "subtitles": {"CZ": "https://x/s.vtt"}},
         {"lang": "", "quality": "FHD", "url": "https://cdn/1080.mp4", "subtitles": {}}], ""))
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "resolve", lambda self, u: "https://cdn/orig.mkv" if u.startswith("orig:") else u)
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1", "?" + url.split("?", 1)[1]])
    ok, li = xbmcplugin.RESOLVED[-1]
    assert ok and li.path.startswith("https://cdn/orig.mkv|")
    assert li.subtitles == ["https://x/s.vtt"]


@pytest.mark.skipif(not os.environ.get("PREHRAJTO_LIVE"), reason="živý test: PREHRAJTO_LIVE=1")
def test_live_bad_login():
    c = prehrajto.PrehrajtoClient("neexistuje.test.kodi@example.com", "spatneheslo")
    with pytest.raises(prehrajto.LoginError):
        c.login()
    assert c.check_user() == (prehrajto.CHECK_BAD_LOGIN, None)


# --- vylepšení 1.2 ---------------------------------------------------------------

def test_retry_after_429():
    c = Fake({})
    calls = []
    sleeps = []

    def once(url, data=None, headers=None, follow=True):
        calls.append(url)
        if len(calls) == 1:
            raise prehrajto.RateLimited(3)
        return "ok", url, "", 200
    c._request_once = once
    c.sleep = sleeps.append
    assert prehrajto.PrehrajtoClient._request(c, "/neco")[0] == "ok"
    assert sleeps == [3] and len(calls) == 2


def test_retry_gives_up():
    c = Fake({})
    c._request_once = lambda *a, **k: (_ for _ in ()).throw(prehrajto.RateLimited(0))
    c.sleep = lambda s: None
    with pytest.raises(prehrajto.PrehrajtoError):
        prehrajto.PrehrajtoClient._request(c, "/neco")


def test_video_info_completes_ref():
    page = fixture("video_synthetic.html").replace("<body>", '<body><div data-video-id="1234567"></div>')
    c = Fake({"/pelisky/4f9a1b2c3d4e5f60": (page, None, "", 200)})
    info = c.video_info(":pelisky:4f9a1b2c3d4e5f60")
    assert info["id"] == "1234567:pelisky:4f9a1b2c3d4e5f60"


def test_provider_keys():
    assert provider.video_key(REF) == provider.video_key(":x:4f9a1b2c3d4e5f60")
    assert provider.prefer_ref(REF, ":pelisky:4f9a1b2c3d4e5f60") == REF
    assert provider.prefer_ref(":pelisky:4f9a1b2c3d4e5f60", REF) == REF
    assert provider.incomplete_id(":a:4f9a1b2c3d4e5f60") and not provider.incomplete_id(REF)


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


def test_version_label_shows_size(plugin):
    assert plugin._version_label({"lang": "", "quality": "1080p", "size": "4.2 GB"}) == "1080p · 4.2 GB"


def test_web_search_paginates_and_lists_playable_videos(plugin, monkeypatch):
    import xbmcaddon
    import xbmcplugin

    xbmcaddon.SETTINGS["search_result_pages"] = "1"

    query = "/hledej/Pel%C3%AD%C5%A1ky%20CZ"
    next_page = "?videoListing-page=2"
    results = fixture("uploads_synthetic.html").replace(
        "/profil/nahrana-videa?videoListing-page=2", query + next_page)
    client = Fake({query + next_page: (results, None, "", 200),
                   query: (results, None, "", 200)})
    monkeypatch.setattr(plugin, "make_client", lambda: client)

    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1",
                "?action=provider_search&q=Pel%C3%AD%C5%A1ky+CZ"])
    filter_row, video, _, more = xbmcplugin.ITEMS
    assert "action=online_filters" in filter_row[0]
    assert not video[2] and "action=play" in video[0]
    assert video[1].props["IsPlayable"] == "true"
    assert "provider_save" in video[1].context[0][1]
    assert more[2] and "page=2" in more[0]
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "1",
                "?" + more[0].split("?", 1)[1]])
    assert client.calls[-1][0] == prehrajto.BASE + query + next_page
    with pytest.raises(prehrajto.PrehrajtoError):
        client.search_page("Pelíšky CZ", 2, "https://example.com/hledej/Pel%C3%AD%C5%A1ky%20CZ")


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


def test_fallback_quality_is_reported_and_cached(plugin, monkeypatch):
    import xbmcgui
    import xbmcplugin

    client = Fake({"/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60":
                   (fixture("video_synthetic.html").replace("'1080'", "'1072'"), None, "", 200)})
    monkeypatch.setattr(client, "_original", lambda ref: "")
    monkeypatch.setattr(plugin, "make_client", lambda: client)
    for _ in range(2):
        plugin.play({"vid": REF})
        assert xbmcplugin.RESOLVED[-1][0]
        assert "1072p" in xbmcgui.NOTIFICATIONS[-1]


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
    monkeypatch.setattr(plugin, "select", select)
    monkeypatch.setattr(prehrajto.PrehrajtoClient, "get_streams", lambda self, vid: (streams, ""))
    plugin._link_cache_put(cz, "https://cdn/cached.mp4", {}, None)
    plugin.play({"vid": cz, "choose": "1"})
    assert xbmcplugin.RESOLVED[-1][1].path.startswith("https://cdn/1072.mp4|")
    assert plugin._link_cache_get(cz)["url"] == "https://cdn/cached.mp4"


def test_continue_watching_uses_one_episode_per_series(plugin):
    import xbmcaddon
    import xbmcplugin
    import nameparser

    xbmcaddon.SETTINGS["pref_lang"] = "0"
    videos = [
        ("Hra.o.truny.S01E01.CZ", 200, 1),
        ("Hra.o.truny.S01E02.EN", 300, 2),
        ("Hra.o.truny.S01E02.CZ", 0, 3),
        ("Hra.o.truny.S02E03.EN", 0, 4),
        ("Hra.o.truny.S02E03.CZ", 0, 5),
        ("Jiny.serial.S01E01.CZ", 120, 6),
        ("Pelisky.1999.CZ", 150, 7),
    ]
    refs = {}
    with plugin.open_library() as lib:
        for number, (title, seconds, when) in enumerate(videos, start=1):
            vid = "%d:v:%016x" % (number, number)
            refs[title] = vid
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            if seconds:
                lib.record_play(vid, seconds, 600, now=when)

    plugin.in_progress({})
    assert len(xbmcplugin.ITEMS) == 3
    url, item, _ = xbmcplugin.ITEMS[-1]
    assert url == plugin.url_for("play", vid=refs["Hra.o.truny.S01E02.EN"])
    assert item.tag.data["resumepoint"] == (300.0, 600.0)

    # Dokoukání české verze označí celý díl jako zhlédnutý, i když EN zůstala rozkoukaná.
    with plugin.open_library() as lib:
        lib.record_play(refs["Hra.o.truny.S01E02.CZ"], 600, 600, now=8)
    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    assert len(xbmcplugin.ITEMS) == 3
    url, item, _ = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("play", vid=refs["Hra.o.truny.S02E03.CZ"])
    assert "Další díl" in item.label


def test_home_search_and_tmdb_catalogue_links(plugin, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import nameparser
    import xbmc
    import xbmcplugin

    class TmdbSearch:
        enabled = True

        def search_titles(self, query, page):
            assert (query, page) == ("Pelíšky", 1)
            return [
                {"media_type": "movie", "id": 10, "title": "Pelíšky", "release_date": "1999-04-08"},
                {"media_type": "movie", "id": 11, "title": "Pelíšky", "release_date": "2026-01-01"},
            ], 1

    monkeypatch.setattr(plugin, "make_tmdb", TmdbSearch)
    online = []

    def fetch(_client, query, page, cursor):
        online.append((query, page, cursor))
        return [{"id": REF, "title": "Pelisky.1999.CZ"}], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    with plugin.open_library() as lib:
        title = "Pelisky.1999.CZ"
        lib.upsert_video(REF, title, "account", nameparser.parse(title))
    plugin.root({})
    assert [item.label for _, item, _ in xbmcplugin.ITEMS][:5] == [
        "Rozkoukané", "Moje filmy", "Moje seriály", "Hledat", "Objevovat"]
    search_url = xbmcplugin.ITEMS[3][0]
    assert parse_qs(urlsplit(search_url).query) == {"action": ["search"]}

    xbmc.KEYBOARD_INPUT.append("  Pelíšky  ")
    xbmc.BUILTINS.clear()
    xbmcplugin.ITEMS.clear()
    plugin.search({})
    assert xbmc.BUILTINS == [] and xbmcplugin.ENDED[-1]
    urls = [parse_qs(urlsplit(url).query) for url, _, _ in xbmcplugin.ITEMS]
    assert [url["action"][0] for url in urls] == ["online_filters", "play", "tmdb_title", "search_people",
                                                   "provider_search", "tmdb_search"]
    assert online == [("Pelíšky", 1, "")]
    xbmcplugin.ITEMS.clear()

    plugin.tmdb_search({"q": "Pelíšky"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert not folder and url == plugin.url_for("play", vid=REF)
    assert "Přehrát z katalogu" in item.label
    assert xbmcplugin.ITEMS[1][2]  # titul se stejným názvem z jiného roku
    assert "Přehrát z katalogu" not in xbmcplugin.ITEMS[1][1].label


def test_season_watched_and_hidden_progress_preserve_playback_history(plugin):
    import nameparser
    import xbmcgui
    import xbmcplugin

    episodes = {}
    with plugin.open_library() as lib:
        for i, suffix in enumerate(("S01E01.CZ", "S01E01.EN", "S01E03.CZ", "S02E01.CZ"), start=1):
            vid = "%d:serial:%016x" % (i, i)
            title = "Test.Serial." + suffix
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            episodes[suffix] = vid
        first = episodes["S01E01.CZ"]
        key = lib.get_video(first)["meta_key"]
        lib.record_play(first, 125, 480, started=True)

    plugin.show({"key": key})
    season_item = xbmcplugin.ITEMS[0][1]
    assert "Označit celou sérii jako zhlédnutou" in {label for label, _ in season_item.context}
    xbmcgui.YESNO_ANSWERS.append(True)
    plugin.mark_watched({"key": key, "season": "1", "watched": "1"})
    with plugin.open_library() as lib:
        assert lib.watched() == {vid for episode, vid in episodes.items() if episode.startswith("S01")}
        assert len(lib.history_videos()) == 1 and lib.history_videos()[0]["position"] == 125

    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    url, item, _ = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("play", vid=episodes["S02E01.CZ"])
    assert "Skrýt z Rozkoukaných" in {label for label, _ in item.context}
    plugin.progress_visibility({"key": key, "hide": "1"})
    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    assert [item.label for _, item, _ in xbmcplugin.ITEMS] == ["Skryté z Rozkoukaných"]

    xbmcplugin.ITEMS.clear()
    plugin.progress_hidden({})
    assert xbmcplugin.ITEMS[0][1].label.startswith("Znovu zobrazit:")
    with plugin.open_library() as lib:
        backup = lib.export_backup(plugin.ADDON_ID)
        assert backup["tables"]["progress_hidden"] == [{"target": key}]
        assert len(backup["tables"]["watched_marks"]) == 3
        lib.clear()
        lib.restore_backup(backup, plugin.ADDON_ID)
        assert lib.hidden_progress()[0]["key"] == key
        assert lib.history_videos()[0]["position"] == 125

    plugin.progress_visibility({"key": key, "hide": "0"})
    plugin.mark_watched({"key": key, "season": "1", "watched": "0"})
    with plugin.open_library() as lib:
        assert not lib.watched() and not lib.hidden_progress()
        assert lib.resume_points()[first] == (125, 480)
        legacy = lib.export_backup(plugin.ADDON_ID)
        del legacy["tables"]["progress_hidden"]
        del legacy["tables"]["watched_marks"]
        for video in legacy["tables"]["videos"]:
            del video["search_name"]
        lib.restore_backup(legacy, plugin.ADDON_ID)
        assert lib.resume_points()[first] == (125, 480)
        assert lib.get_video(first)["search_name"] == nameparser.normalize(lib.get_video(first)["raw_title"])


def test_item_context_groups_edit_actions(plugin, monkeypatch):
    import nameparser
    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    plugin.movies({})
    item = xbmcplugin.ITEMS[0][1]
    assert [label for label, _ in item.context][:4] == [
        "Vybrat jinou verzi", "Přidat do kolekce", "Označit jako zhlédnuté", "Spravovat položku"]

    calls = []
    monkeypatch.setattr(plugin, "video_edit", lambda params: calls.append(params["vid"]))
    monkeypatch.setattr(plugin, "select", lambda heading, options: options.index(plugin._(30302)))
    plugin.manage_item({"vid": REF})
    assert calls == [REF]


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

    tracker.vid, tracker.total = refs[0], 600
    tracker.onPlayBackEnded()
    xbmcgui.YESNO_ANSWERS.append(True)
    tracker.offer_next_if_due()
    tracker.offer_next_if_due()
    url = plugin.url_for("play", vid=refs[2], continue_play=1)
    assert xbmc.BUILTINS == ["PlayMedia(%s,resume)" % url]
    plugin._link_cache_put(refs[2], "https://cdn/next.mp4", {}, None)
    plugin.play({"vid": refs[2], "continue_play": "1", "restart": "1"})
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (120.0, 600.0)


def test_episode_metadata_cache_and_spoiler_free_browsing(plugin, monkeypatch):
    import nameparser
    import xbmcgui
    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    calls = []

    def fetch(path, **params):
        calls.append((path, params))
        return {"episodes": [{"episode_number": 3, "name": "Zkouška", "runtime": 43,
                              "overview": "Závěr epizody", "still_path": "/episode.jpg"}]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
    with plugin.open_library() as lib:
        title = "Archa.S02E03.CZ"
        lib.upsert_video(REF, title, "account", nameparser.parse(title), duration=2400)
        key = lib.get_video(REF)["meta_key"]
        lib.update_meta(key, {"tmdb_id": 321, "plot": "Popis seriálu"})
        lib.record_play(REF, 300, 2400)
    plugin.season({"key": key, "season": "2"})
    listing = xbmcplugin.ITEMS[-1][1]
    assert listing.label == "S02E03 · Zkouška · 43 min"
    assert listing.art["thumb"].endswith("/episode.jpg")
    assert listing.tag.data["duration"] == 43 * 60
    assert "Závěr epizody" not in listing.tag.data["plot"]
    assert "Popis seriálu" not in listing.tag.data["plot"]
    assert "Detail epizody" in [label for label, _ in listing.context]

    xbmcplugin.ITEMS.clear()
    plugin.in_progress({})
    item = xbmcplugin.ITEMS[0][1]
    assert "S02E03 · Zkouška · 43 min" in item.label
    assert item.tag.data["resumepoint"] == (300.0, 2400.0)
    plugin.episode_detail({"key": key, "season": "2", "episode": "3"})
    assert xbmcgui.OK_DIALOGS[-1] == "Závěr epizody"
    assert calls == [("/tv/321/season/2", {"timeout": 4})]


def test_saved_movie_filter_uses_one_matching_version(plugin, monkeypatch):
    import nameparser
    import xbmc
    import xbmcaddon
    import xbmcplugin

    titles = ["Archa.2004.CZ.720p", "Archa.2004.EN.1080p",
              "Pelisky.1999.CZ.1080p", "Pelisky.1999.EN.2160p", "Vlak.2001.CZ.1080p"]
    vids = ["%d:movie:%016x" % (i, i) for i in range(1, len(titles) + 1)]
    with plugin.open_library() as lib:
        for vid, title in zip(vids, titles):
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
        lib.set_watched([vids[-1]], True)
    criteria = {"language": "CZ", "quality": "FHD", "status": "unwatched"}
    xbmcaddon.SETTINGS["pref_lang"] = "2"  # Obecná preference EN nesmí změnit výsledek filtru.
    choices = iter((1, 2, 1))
    monkeypatch.setattr(plugin, "select", lambda *_args, **_kwargs: next(choices))
    xbmc.BUILTINS.clear()
    plugin.movie_filter_create({"order": "added"})
    assert xbmc.BUILTINS == ["Container.Update(%s)" % plugin.url_for(
        "movies", filtered=1, order="added", **criteria)]
    plugin.movies(dict(criteria, filtered="1"))
    assert [item.label for _, item, _ in xbmcplugin.ITEMS] == ["Uložit tento filtr", "Pelisky (1999)"]
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("play", vid=vids[2])

    xbmc.KEYBOARD_INPUT.append("Nezhlédnuté CZ 1080p")
    plugin.movie_filter_save(criteria)
    with plugin.open_library() as lib:
        chosen = lib.movie_filters()[0]
        snapshot = lib.export_backup(plugin.ADDON_ID)
        assert snapshot["tables"]["saved_movie_filters"][0]["name"] == "Nezhlédnuté CZ 1080p"
    xbmcplugin.ITEMS.clear()
    plugin.movies({"browse": "1"})
    shortcut, label, _ = xbmcplugin.ITEMS[1]
    assert label.label == chosen["name"] and "filter_id=" in shortcut
    xbmcplugin.ITEMS.clear()
    plugin.movies({"filter_id": str(chosen["id"])})
    assert [url for url, _, _ in xbmcplugin.ITEMS] == [plugin.url_for("play", vid=vids[2])]


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
    monkeypatch.setattr(plugin, "select", lambda *_args: next(choices))
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

    monkeypatch.setattr(plugin, "yesno", confirm)
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
    monkeypatch.setattr(plugin, "make_client", lambda: client)
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


def test_search_opens_exact_episode_and_prioritizes_movie_year(plugin):
    from urllib.parse import parse_qs, urlsplit

    import nameparser
    import xbmcplugin

    exact = "2:archa:bbbbbbbbbbbbbbbb"
    other = "3:archa:cccccccccccccccc"
    newer = "4:pelisky:dddddddddddddddd"
    with plugin.open_library() as lib:
        for vid, title in ((other, "Archa.S01E03.CZ"), (exact, "Archa.S02E03.CZ"),
                           (newer, "Pelisky.2026.CZ"), (REF, "Pelisky.1999.CZ")):
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
        lib.record_play(exact, 90, 1800)
    plugin.search({"q": "archa 2x03", "online": "0"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert parse_qs(urlsplit(url).query)["vid"] == [exact]
    assert not folder and "S02E03 · Pokračovat" in item.label
    assert item.tag.data["resumepoint"] == (90.0, 1800.0)
    xbmcplugin.ITEMS.clear()
    plugin.search({"q": "pelisky 1999", "online": "0"})
    assert "(1999)" in xbmcplugin.ITEMS[0][1].label
    assert "(2026)" in xbmcplugin.ITEMS[1][1].label


def test_online_search_merges_verified_matches_and_shows_versions(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import nameparser
    import xbmcplugin

    alt = "2:pelisky:aaaaaaaaaaaaaaaa"
    other = "3:pelisky:bbbbbbbbbbbbbbbb"
    calls = []

    class TmdbSearch:
        enabled = True

        def search_titles(self, query, _page):
            assert query == "Pelíšky"
            return [{"media_type": "movie", "id": 10, "title": query, "release_date": "1999-01-01"},
                    {"media_type": "movie", "id": 11, "title": query, "release_date": "2026-01-01"}], 1

    def provider_search(_client, query, page, cursor):
        calls.append((query, page, cursor))
        return [
            {"id": REF, "title": "Pelisky.1999.CZ", "duration": 6000, "thumb": ""},
            {"id": alt, "title": "Pelisky.1999.EN.1080p", "duration": 6000, "thumb": ""},
            {"id": other, "title": "Pelisky.2026.CZ", "duration": 6000, "thumb": ""},
        ], ""

    monkeypatch.setattr(plugin, "make_tmdb", TmdbSearch)
    monkeypatch.setattr(provider, "search_catalog", provider_search)
    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", nameparser.parse("Pelisky.1999.CZ"))
    plugin.search({"q": "Pelíšky 1999", "online": "1"})
    assert len(xbmcplugin.ITEMS) == 6  # filtry, dva tituly a odkazy na úplné výsledky
    local_label = xbmcplugin.ITEMS[1][1]
    assert "Můj katalog, Přehraj.to, TMDB" in local_label.label
    assert xbmcplugin.ITEMS[1][2] and "action=search_title" in xbmcplugin.ITEMS[1][0]
    assert "(2026)" in xbmcplugin.ITEMS[2][1].label
    detail = local_label.context[0][1][len("Container.Update("):-1]
    xbmcplugin.ITEMS.clear()
    plugin.search_title(dict(parse_qsl(urlsplit(detail).query)))
    rows = [dict(parse_qsl(urlsplit(url).query)) for url, _, _ in xbmcplugin.ITEMS]
    assert [row["vid"] for row in rows if row["action"] == "play"] == [REF, alt]
    assert calls == [("Pelíšky 1999", 1, "")]


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


def test_local_czech_slovak_categories_follow_production_metadata(plugin):
    import nameparser
    import xbmcplugin

    films = [
        (REF, "Pelisky.1999.CZ", ["Czech Republic"]),
        ("2:film:bbbbbbbbbbbbbbbb", "Slovensky.2009.SK", ["Slovensko"]),
        ("3:film:cccccccccccccccc", "Koprodukce.2024.EN", ["CZ", "Slovakia"]),
        ("4:film:dddddddddddddddd", "Dabing.2000.CZ", ["USA"]),
        ("5:film:eeeeeeeeeeeeeeee", "Stary.1984.CZ", ["Československo"]),
    ]
    with plugin.open_library() as lib:
        for vid, title, countries in films:
            lib.upsert_video(vid, title, "account", nameparser.parse(title))
            lib.update_meta(lib.get_video(vid)["meta_key"], {"countries": countries})
    plugin.movies({"browse": "1"})
    labels = {item.label for _, item, _ in xbmcplugin.ITEMS}
    assert "Československé filmy" in labels and not {"České filmy", "Slovenské filmy"} & labels
    for origin, indexes in (("CZSK", {0, 1, 2, 4}), ("CZ", {0, 2}), ("SK", {1, 2}), ("CS", {4})):
        xbmcplugin.ITEMS.clear()
        plugin.movies({"origin": origin})
        assert {url for url, _, _ in xbmcplugin.ITEMS} == {
            plugin.url_for("play", vid=films[i][0]) for i in indexes}


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
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
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


def test_country_search_and_country_selection_preserve_filters(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import xbmc
    import xbmcplugin
    from tmdb import Tmdb

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Film.1999.CZ", "account", None)
        lib.update_meta(lib.get_video(REF)["meta_key"], {"countries": ["FR"]})
    xbmc.KEYBOARD_INPUT.append("Francie")
    plugin.search({})
    assert [(dict(parse_qsl(urlsplit(url).query))["action"], folder) for url, _, folder in xbmcplugin.ITEMS] == [
        ("settings", False), ("tmdb_refine", False), ("play", False)]
    assert "origin=FR" in xbmcplugin.ITEMS[1][0]

    client = Tmdb("test")
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
    calls = []

    def fetch(path, **params):
        if path == "/configuration/countries":
            return [{"iso_3166_1": "FR", "native_name": "France"}]
        assert path == "/discover/movie"
        origin = params["with_origin_country"]
        calls.append((origin, params["with_genres"], params["primary_release_year"]))
        return {"total_pages": 1, "results": [{"id": 123, "title": "Koprodukce"},
                      {"id": 321 if origin == "CZ" else 456, "title": "Film " + origin}]}

    monkeypatch.setattr(client, "_get", fetch)
    choices = iter([0, 1])

    def select(_heading, options, preselect=-1):
        selected = next(choices)
        if selected == 0:
            assert options[0] == "Podle země"
        else:
            assert options[:2] == ["Bez omezení", "Československé filmy"]
            assert options[preselect] == "France"
        return selected

    monkeypatch.setattr(plugin, "select", select)
    xbmc.BUILTINS.clear()
    plugin.tmdb_refine({"kind": "movie", "category": "recent", "genre": "35", "year": "2024", "origin": "FR"})
    update = xbmc.BUILTINS[-1]
    assert update.endswith(",replace)")
    args = dict(parse_qsl(urlsplit(update[len("Container.Update("):-len(",replace)")]).query))
    assert args == {"action": "tmdb_titles", "kind": "movie", "category": "recent", "origin": "CZSK",
                    "genre": "35", "year": "2024"}

    xbmcplugin.ITEMS.clear()
    plugin.tmdb_titles(args)
    assert calls == [("CZ", "35", "2024"), ("SK", "35", "2024")]
    assert len(xbmcplugin.ITEMS) == 5
    assert xbmcplugin.ITEMS[0][1].label == "Upřesnit výběr · Československé filmy · Komedie · 2024"
    assert "action=tmdb_refine" in xbmcplugin.ITEMS[0][0]
    assert "Upřesnit výběr" in dict(xbmcplugin.ITEMS[2][1].context)


def test_actor_search_shows_filmography_after_keyboard(plugin, monkeypatch):
    import xbmc
    import xbmcplugin
    from tmdb import Tmdb

    client = Tmdb("test")
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)

    def fetch(path, **params):
        if path == "/search/multi":
            return {"results": [], "total_pages": 1}
        if path == "/search/person":
            assert params["query"] == "Jiří Kodet"
            return {"results": [{"id": 71, "name": "Jiří Kodet"}], "total_pages": 1}
        assert path == "/person/71/combined_credits"
        title = {"id": 12, "title": "Pelíšky", "release_date": "1999-04-08", "media_type": "movie"}
        return {"cast": [title, dict(title), dict(title, id=13, adult=True)]}

    monkeypatch.setattr(client, "_get", fetch)
    monkeypatch.setattr(provider, "search_catalog", lambda *_: ([], ""))
    xbmc.KEYBOARD_INPUT.append("Jiří Kodet")
    plugin.search({})
    url, item, folder = xbmcplugin.ITEMS[1]
    assert item.label == "Jiří Kodet" and folder
    assert url == plugin.url_for("person_titles", person_id=71)
    xbmcplugin.ITEMS.clear()
    plugin.person_titles({"person_id": "71"})
    assert len(xbmcplugin.ITEMS) == 2
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for(
        "tmdb_movie_versions", tmdb_id=12, title="Pelíšky", original="Pelíšky", year=1999)


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

    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
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
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
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


def test_search_matches_slovak_alias_and_marks_unverified_year(plugin, monkeypatch):
    import xbmcplugin
    from tmdb import Tmdb

    tmdb = Tmdb("test")
    monkeypatch.setattr(plugin, "make_tmdb", lambda: tmdb)
    requests = []
    item = {"id": 30, "title": "Místní název", "original_title": "Original Film",
            "media_type": "movie", "release_date": "1999-08-01"}

    def metadata(path, **_):
        if path == "/search/multi":
            return {"results": [item], "total_pages": 1}
        assert path == "/movie/30/alternative_titles"
        return {"titles": [{"iso_3166_1": "SK", "title": "Slovenský názov"}]}

    def fetch(_client, query, *_):
        requests.append(query)
        if query == "Slovenský názov":
            return [{"id": REF, "title": "Slovensky.nazov.1999.CZ.1080p"}], ""
        if query == "Místní název":
            return [{"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Mistni.nazev.CZ.720p"}], ""
        return [], ""

    monkeypatch.setattr(tmdb, "_get", metadata)
    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.search({"q": "Místní název 1999"})
    assert requests == ["Místní název 1999", "Original Film", "Místní název", "Slovenský názov"]
    assert "(1999)" in xbmcplugin.ITEMS[1][1].label
    assert "vid=" in xbmcplugin.ITEMS[1][0] and "TMDB" in xbmcplugin.ITEMS[1][1].label
    assert "Rok neověřen" in xbmcplugin.ITEMS[2][1].label


def test_search_retry_and_filter_suggestions_keep_query_and_playable_local_results(plugin, monkeypatch):
    from urllib.parse import parse_qsl, urlsplit

    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ", "account", None)
    candidate = {"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Pelisky.1999.720p"}
    calls = []

    def fetch(_client, query, *_):
        calls.append(query)
        if len(calls) == 1:
            raise provider.TemporaryError("dočasná chyba")
        return [candidate], ""

    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.search({"q": "Pelíšky 1999", "audio": "CZ", "quality": "FHD"})
    assert any("action=play" in url for url, _, _ in xbmcplugin.ITEMS)
    retry = next(url for url, item, _ in xbmcplugin.ITEMS if item.label == "Zopakovat online hledání")
    assert len(calls) == 1
    xbmcplugin.ITEMS.clear()
    plugin.run([plugin.BASE_URL, "1", urlsplit(retry).query])
    relax = next(url for url, item, _ in xbmcplugin.ITEMS if "bez omezení kvality a zahrnout" in item.label)
    args = dict(parse_qsl(urlsplit(relax).query))
    assert args["q"] == "Pelíšky 1999" and args["audio"] == "CZ" and args["unknown_audio"] == "1"
    assert "quality" not in args and "retry" not in args
    before_relax = len(calls)
    xbmcplugin.ITEMS.clear()
    plugin.run([plugin.BASE_URL, "1", urlsplit(relax).query])
    assert len(calls) == before_relax
    assert any("action=search_title" in url for url, _, _ in xbmcplugin.ITEMS)


def test_misspelled_title_suggestion_keeps_requested_year(plugin):
    from urllib.parse import parse_qs, urlsplit

    import xbmcplugin

    with plugin.open_library() as lib:
        lib.upsert_video(REF, "Pelíšky.1999.CZ", "account", None)
    plugin.search({"q": "Pelisly 1999", "online": "0"})
    url, item, folder = xbmcplugin.ITEMS[0]
    assert folder and item.label == "Mysleli jste Pelíšky 1999?"
    assert parse_qs(urlsplit(url).query)["q"] == ["Pelíšky 1999"]


def test_exact_online_episode_and_series_browsing_exclude_other_titles(plugin, monkeypatch):
    import xbmcplugin

    def items(_client, _query, *_):
        return ([{"id": REF, "title": "Archa.S02E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "2:film:aaaaaaaaaaaaaaaa", "title": "Archa.S01E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "3:film:bbbbbbbbbbbbbbbb", "title": "Jiny serial.S02E03.CZ", "thumb": "", "duration": 2400},
                 {"id": "4:film:cccccccccccccccc", "title": "Archa.2020.CZ", "thumb": "", "duration": 6400}], "")

    monkeypatch.setattr(provider, "search_catalog", items)
    plugin.search({"q": "Archa 2x03"})
    assert [url for url, _, _ in xbmcplugin.ITEMS if "action=play" in url] == [plugin.url_for("play", vid=REF)]
    xbmcplugin.ITEMS.clear()
    plugin.provider_search({"q": "Archa", "series": "1", "title": "Archa"})
    assert [url for url, _, _ in xbmcplugin.ITEMS if "action=play" in url] == [
        plugin.url_for("play", vid="2:film:aaaaaaaaaaaaaaaa"), plugin.url_for("play", vid=REF)]


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


def test_playback_cache_and_cookies_follow_account(plugin):
    import xbmcaddon

    original = plugin.make_client().cookie_file
    plugin._link_cache_put(REF, "http://example.com/stream.mp4", {}, 0)
    assert plugin._link_cache_get(REF)
    xbmcaddon.SETTINGS["username"] = "other@example.com"
    assert plugin.make_client().cookie_file != original
    assert plugin._link_cache_get(REF) is None


def test_provider_batch_removes_duplicates_and_keeps_retry_cursor(plugin, monkeypatch):
    def page(_client, _query, page, cursor):
        if page == 3:
            assert cursor == "third"
            raise provider.ProviderError("unavailable")
        return ([{"id": "%d:film:%016x" % (page, number)} for number in
                 (range(20) if page == 1 else range(19, 39))], "second" if page == 1 else "third")

    monkeypatch.setattr(plugin, "_result_pages", lambda: 3)
    monkeypatch.setattr(provider, "search_catalog", page)
    items, next_page, cursor, partial = plugin._provider_page_batch(None, "film")
    assert len(items) == 39 and (next_page, cursor, partial) == (3, "third", True)


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
    monkeypatch.setattr(plugin, "make_tmdb", lambda: client)
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


def test_provider_filters_try_verified_original_title(plugin, monkeypatch):
    import xbmcplugin

    calls = []
    items = {
        "Místní název 2020": [
            {"id": REF, "title": "Mistni.nazev.2020.CZ.720p", "duration": 5600, "thumb": ""},
            {"id": "2:film:eeeeeeeeeeeeeeee", "title": "Mistni.nazev.2020.CZ.1080p.Trailer",
             "duration": 90, "thumb": ""},
        ],
        "Original Name": [
            {"id": "3:film:bbbbbbbbbbbbbbbb", "title": "Original.Name.2020.CZ.1080p", "duration": 5600, "thumb": ""},
            {"id": "4:film:cccccccccccccccc", "title": "Original.Name.2020.SK.1080p", "duration": 5600, "thumb": ""},
        ],
    }

    class TmdbSearch:
        enabled = True

        def search_titles(self, title, _page):
            return [{"media_type": "movie", "id": 10, "title": title, "original_title": "Original Name",
                     "release_date": "2020-01-01"}], 1

    def fetch(_client, query, _page, _cursor):
        calls.append(query)
        return items[query], ""

    monkeypatch.setattr(plugin, "make_tmdb", TmdbSearch)
    monkeypatch.setattr(provider, "search_catalog", fetch)
    plugin.provider_search({"q": "Místní název 2020", "audio": "CZ", "quality": "FHD", "hide_previews": "1"})
    assert len(xbmcplugin.ITEMS) == 2
    assert "Český dabing" in xbmcplugin.ITEMS[0][1].label
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("play", vid="3:film:bbbbbbbbbbbbbbbb")
    assert calls == ["Místní název 2020", "Original Name"]

    xbmcplugin.ITEMS.clear()
    plugin.add_provider_result({"id": REF, "title": "Original Name", "duration": 5600, "thumb": ""})
    plugin._flush_items()
    assert "Jazyk neuveden" in xbmcplugin.ITEMS[0][1].label


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
    monkeypatch.setattr(plugin, "make_client", lambda: client)
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
    monkeypatch.setattr(plugin, "select", choose)
    monkeypatch.setattr(plugin, "yesno", lambda prompt, **_kwargs: prompts.append(prompt) or True)
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


def _add_movies_to_catalog(plugin, count, versions=1):
    import nameparser
    with plugin.open_library() as lib, lib.conn:
        for i in range(count):
            for v in range(versions):
                raw = "Film %02d %d %s 1080p" % (i, 1990 + i, "CZ" if v else "EN")
                lib.upsert_video("%d:film-%d:%016x" % (i * 10 + v + 1, i, i * 10 + v + 1), raw, "account",
                                 nameparser.parse(raw))


def test_catalogue_listings_are_paginated(plugin):
    import xbmcaddon
    import xbmcplugin
    _add_movies_to_catalog(plugin, 5, versions=2)
    xbmcaddon.SETTINGS["list_page_size"] = "2"

    plugin.movies({"order": "title", "browse": "1"})
    movies = [item for item in xbmcplugin.ITEMS if "action=play" in item[0]]
    assert len(movies) == 2
    next_page = xbmcplugin.ITEMS[-1]
    assert next_page[2] is True and next_page[1].props["SpecialSort"] == "bottom"
    assert "page=2" in next_page[0] and "action=movies" in next_page[0]
    assert "(2/3)" in next_page[1].label

    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title", "browse": "1", "page": "3"})
    assert len(xbmcplugin.ITEMS) == 1  # bez zkratek filtrů a bez další stránky
    assert "action=play" in xbmcplugin.ITEMS[0][0]

    del xbmcplugin.ITEMS[:]
    plugin.all_videos({"page": "5"})
    assert len(xbmcplugin.ITEMS) == 2 and all("action=play" in item[0] for item in xbmcplugin.ITEMS)

    xbmcaddon.SETTINGS["list_page_size"] = "0"
    del xbmcplugin.ITEMS[:]
    plugin.movies({"order": "title"})
    assert len(xbmcplugin.ITEMS) == 5


def test_url_for_matches_urlencode(plugin):
    import urllib.parse
    params = {"key": "movie:pelisky|1999", "q": "Hra o trůny 2x03", "page": 2, "empty": "", "none": None,
              "vid": REF}
    expected = plugin.BASE_URL + "?" + urllib.parse.urlencode(
        dict([(k, v) for k, v in params.items() if v not in (None, "")] + [("action", "search")]))
    assert plugin.url_for("search", **params) == expected


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


def test_online_search_keeps_recent_queries(plugin, monkeypatch):
    calls = []

    def batch(_client, query, page=1, cursor=""):
        calls.append(query)
        return [], page, "", False

    class NoTmdb:
        enabled = False
        timeout = 0

    monkeypatch.setattr(plugin, "_provider_page_batch", batch)
    monkeypatch.setattr(plugin, "make_tmdb", lambda cached=True: NoTmdb())
    filters = plugin._online_filters({})
    for query in ("Pelíšky", "Samotáři", "Pelíšky", "Samotáři"):
        plugin._online_search(query, query, 0, None, filters)
    assert calls == ["Pelíšky", "Samotáři"]
    plugin._online_search("Pelíšky", "Pelíšky", 0, None, filters, retry=True)
    assert calls == ["Pelíšky", "Samotáři", "Pelíšky"]
