"""Klient Přehraj.to: odkazy, parsování, přihlášení, přehrávací odkazy, HTTP."""

import os
import time

import pytest

from conftest import fixture
import prehrajto
import provider

from helpers import Fake, REF, jwt, set_cookie


class _FakeHTTPSConnection(object):
    debuglevel = 0
    created = []
    fail_next_reuse = False

    def __init__(self, host, timeout=None, context=None):
        self.host = host
        self.sock = None
        self.timeout = timeout
        self.requests = []
        self.closed = False
        self.tunnel = None
        _FakeHTTPSConnection.created.append(self)

    def set_tunnel(self, host, headers=None):
        self.tunnel = (host, headers)

    def request(self, method, selector, data, headers):
        if self.requests and _FakeHTTPSConnection.fail_next_reuse:
            _FakeHTTPSConnection.fail_next_reuse = False
            raise ConnectionResetError("server closed idle connection")
        self.requests.append((method, selector, headers))

    def getresponse(self):
        import email.message
        selector = self.requests[-1][1]
        msg = email.message.Message()
        msg["Content-Type"] = "video/mp4" if selector.endswith(".mp4") else "text/html; charset=utf-8"
        msg["Set-Cookie"] = "access_token=abc; Path=/"

        class Resp(object):
            status = 200
            reason = "OK"
            will_close = False

            def __init__(self):
                self.msg = msg

            def getheader(self, name, default=None):
                return msg.get(name, default)

            def read(self):
                return ("<html>%s</html>" % selector).encode("utf-8")

        return Resp()

    def close(self):
        self.closed = True


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


@pytest.mark.skipif(not os.environ.get("PREHRAJTO_LIVE"), reason="živý test: PREHRAJTO_LIVE=1")
def test_live_bad_login():
    c = prehrajto.PrehrajtoClient("neexistuje.test.kodi@example.com", "spatneheslo")
    with pytest.raises(prehrajto.LoginError):
        c.login()
    assert c.check_user() == (prehrajto.CHECK_BAD_LOGIN, None)


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


def test_keepalive_reuses_prehrajto_connection(monkeypatch):
    monkeypatch.setattr(prehrajto.KeepAliveHTTPSHandler, "connection_class", _FakeHTTPSConnection)
    _FakeHTTPSConnection.created = []
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    client = prehrajto.PrehrajtoClient()
    for name in ("a", "b", "c"):
        page, final = client._get("/hledej/" + name)
        assert page == "<html>/hledej/%s</html>" % name and final == "https://prehraj.to/hledej/" + name
    assert len(_FakeHTTPSConnection.created) == 1
    headers = _FakeHTTPSConnection.created[0].requests[0][2]
    assert headers["Connection"] == "keep-alive"
    assert client._cookie("access_token") == "abc"  # cookies z odpovědi se dál ukládají

    _FakeHTTPSConnection.fail_next_reuse = True
    assert client._get("/hledej/d")[0] == "<html>/hledej/d</html>"
    assert len(_FakeHTTPSConnection.created) == 2 and _FakeHTTPSConnection.created[0].closed

    # binární odpověď se nenačítá do paměti a spojení se do zásoby nevrací
    resp = client.keepalive.https_open(__import__("urllib.request").request.Request("https://prehraj.to/x.mp4"))
    assert resp.read().endswith(b"x.mp4</html>")
    client._get("/hledej/e")
    assert len(_FakeHTTPSConnection.created) == 3

    # jiný host jde standardní cestou
    calls = []
    monkeypatch.setattr(__import__("urllib.request").request.HTTPSHandler, "https_open",
                        lambda self, req: calls.append(req.host))
    client.keepalive.https_open(__import__("urllib.request").request.Request("https://image.tmdb.org/a.jpg"))
    assert calls == ["image.tmdb.org"]


def test_concurrent_logins_send_one_login_form():
    import threading

    class Slow(Fake):
        def _request(self, url, data=None, headers=None, follow=True):
            if "loginForm" in url and data:
                time.sleep(0.05)
            return super()._request(url, data, headers, follow)

    client = Slow({})
    threads = [threading.Thread(target=client.login) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for url, data, _ in client.calls if "loginForm" in url and data) == 1


def test_rejected_password_is_not_hidden_by_stale_token():
    client = Fake({}, login_ok=False)
    set_cookie(client.cookies, "access_token", jwt(time.time() - 60))
    set_cookie(client.cookies, "refresh_token", "old")
    with pytest.raises(prehrajto.LoginError):
        client.login()
    assert not client._cookie("access_token") and not client._logged_in
    assert client.check_user() == (prehrajto.CHECK_BAD_LOGIN, None)


def test_keepalive_never_repeats_a_request_after_timeout(monkeypatch):
    import socket
    import urllib.error
    import urllib.request

    class Conn(_FakeHTTPSConnection):
        fail = None

        def getresponse(self):
            if Conn.fail and len(self.requests) > 1:
                error, Conn.fail = Conn.fail, None
                raise error
            return super().getresponse()

    monkeypatch.setattr(prehrajto.KeepAliveHTTPSHandler, "connection_class", Conn)
    _FakeHTTPSConnection.created = []
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    handler = prehrajto.KeepAliveHTTPSHandler()
    opener = urllib.request.build_opener(handler)
    opener.open("https://prehraj.to/")
    Conn.fail = socket.timeout("timed out")
    with pytest.raises(urllib.error.URLError):
        opener.open(urllib.request.Request("https://prehraj.to/?frm=login", data=b"email=a"), timeout=1)
    sent = [request[:2] for conn in _FakeHTTPSConnection.created for request in conn.requests]
    assert sent == [("GET", "/"), ("POST", "/?frm=login")]  # přihlášení se neposlalo podruhé

    # mrtvé spojení ze zásoby: druhý pokus jde vždy na nové spojení, ne na další ze zásoby
    _FakeHTTPSConnection.created = []
    handler = prehrajto.KeepAliveHTTPSHandler()
    stale = [Conn("prehraj.to"), Conn("prehraj.to")]
    for conn in stale:
        conn.requests.append(("GET", "/old", {}))
        handler._give(("prehraj.to", None), conn)
    Conn.fail = ConnectionResetError("closed")
    response = urllib.request.build_opener(handler).open("https://prehraj.to/nove")
    assert response.read() == b"<html>/nove</html>"
    assert len(_FakeHTTPSConnection.created) == 3 and _FakeHTTPSConnection.created[2].requests[0][1] == "/nove"


def test_original_ignores_redirect_back_to_the_site():
    page = fixture("video_synthetic.html")
    for location, expected in (("/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60", ""),
                               ("https://prehraj.to/?login", ""),
                               ("https://cdn.example.net/file.mkv?sig=1", "https://cdn.example.net/file.mkv?sig=1")):
        client = Fake({"/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60?do=download": ("", None, location, 302),
                       "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60": (page, None, "", 200)})
        client.api = lambda path: (_ for _ in ()).throw(prehrajto.PrehrajtoError("API HTTP 403"))
        assert client._original(REF) == expected
