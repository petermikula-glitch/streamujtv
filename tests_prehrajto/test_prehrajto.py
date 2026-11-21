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
