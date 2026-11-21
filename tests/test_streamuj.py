import os

import pytest

import streamuj
from conftest import fixture


def test_extract_video_id():
    assert streamuj.extract_video_id("https://www.streamuj.tv/video/0645c382f89879db00bc") == "0645c382f89879db00bc"
    assert streamuj.extract_video_id("http://streamuj.tv/video/0645C382F89879DB00BC?x=1") == "0645c382f89879db00bc"
    assert streamuj.extract_video_id("0645c382f89879db00bc") == "0645c382f89879db00bc"
    assert streamuj.extract_video_id("https://example.com/") is None


def test_passwords():
    assert streamuj.password_md5("heslo") == "955db0b81ef1989b4a4dfeae8061a9a6"
    assert streamuj.password_md5("955DB0B81EF1989B4A4DFEAE8061A9A6") == "955db0b81ef1989b4a4dfeae8061a9a6"
    assert len(streamuj.password_md5md5("heslo")) == 32


def test_mask_secrets():
    url = "https://www.streamuj.tv/json_api_player.php?login=a&password=abc&pass=a:::b&x=1"
    masked = streamuj.mask_secrets(url)
    assert "abc" not in masked and "a:::b" not in masked and "x=1" in masked


def test_parse_real_listing():
    videos = streamuj.parse_video_list(fixture("streamuj_listing.html"))
    assert [v["id"] for v in videos] == ["7767cb0593f809d1cf9a", "d1aae31d0e2033eef37c",
                                         "eb8f670123fe49f27d9e", "28ef44a0514366652a28",
                                         "90d3ecc3af79b33e4cf0"]
    # nezkrácený název z atributu title, ne "Cristiano Rona..."
    assert videos[3]["title"] == "Cristiano Ronaldo - The Perfect Player 2008"
    assert videos[4]["title"] == "big bang s06E18"
    assert videos[0]["duration"] == 6 * 60 + 20
    assert videos[0]["thumb"].startswith("http")


def test_parse_pagination():
    pages = streamuj.parse_pagination(fixture("streamuj_listing.html"),
                                      "https://www.streamuj.tv/videos/basic/mr")
    assert "https://www.streamuj.tv/videos/basic/mr/2" in pages
    assert "https://www.streamuj.tv/videos/basic/mr/22" in pages


def test_parse_video_page():
    info = streamuj.parse_video_page(fixture("streamuj_video.html"))
    assert info["title"] == "South Park 1x01"
    assert info["description"].startswith("Zatímco čekají kluci")


def test_parse_playlists():
    page = ('<a href="https://www.streamuj.tv/my_playlists">Moje</a>'
            '<a href="https://www.streamuj.tv/playlist/123/filmy">Filmy</a>'
            '<a href="https://www.streamuj.tv/playlist/123/delete">Smazat</a>')
    assert streamuj.parse_playlists(page) == [("Filmy", "https://www.streamuj.tv/playlist/123/filmy")]


class FakeOpener(object):
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        for prefix, (body, final) in self.responses.items():
            if url.startswith(prefix):
                return FakeResponse(body, final or url)
        raise AssertionError("neočekávaný dotaz " + url)


class FakeResponse(object):
    def __init__(self, body, url):
        self.body = body.encode("utf-8")
        self.url = url

    def read(self):
        return self.body

    def geturl(self):
        return self.url

    def close(self):
        pass


def test_get_streams_and_resolve():
    api = ('{"result":1,"URL":{"CZ":{"SD":"https://www.streamuj.tv/video/abc?streamuj=original&location=1",'
           '"HD":"https://www.streamuj.tv/video/abc?streamuj=hd&location=1",'
           '"subtitles":{"cz":"https://www.streamuj.tv/sub.srt"}}},"errormessage":null}')
    opener = FakeOpener({
        streamuj.API_URL: (api, None),
        "https://www.streamuj.tv/video/abc": ("https://s14.streamuj.tv:443/vid/x/abc_hd.mp4\n", None),
    })
    c = streamuj.StreamujClient("user", "heslo", opener=opener)
    streams, msg = c.get_streams("abc")
    assert [s["quality"] for s in streams] == ["HD", "SD"]
    assert streams[0]["subtitles"] == {"CZ": "https://www.streamuj.tv/sub.srt"}
    api_url = opener.requests[0].full_url
    assert "passwordinmd5=1" in api_url and "password=955db0b81ef1989b4a4dfeae8061a9a6" in api_url
    final = c.resolve(streams[0]["url"])
    assert final == "https://s14.streamuj.tv:443/vid/x/abc_hd.mp4"
    assert "pass=user:::" in opener.requests[1].full_url


def test_resolve_rejects_foreign_host():
    c = streamuj.StreamujClient(opener=FakeOpener({}))
    with pytest.raises(streamuj.StreamujError):
        c.resolve("https://evil.example.com/video/abc")


def test_login_and_my_videos():
    listing = fixture("streamuj_listing.html").replace("videos/basic/mr", "my_videos")
    opener = FakeOpener({
        "https://www.streamuj.tv/my_profile": ("<a href='/logout'>Odhlásit</a>", None),
        "https://www.streamuj.tv/my_videos/": (listing, None),
        "https://www.streamuj.tv/my_videos": (listing, None),
    })
    c = streamuj.StreamujClient("user", "heslo", opener=opener)
    videos = c.my_videos()
    assert len(videos) == 5
    visited = [r.full_url for r in opener.requests]
    assert "https://www.streamuj.tv/my_videos/2" in visited


def test_login_failure():
    opener = FakeOpener({
        "https://www.streamuj.tv/my_profile": ("", "https://www.streamuj.tv/signup"),
        "https://www.streamuj.tv/login": ("<form id='loginForm'>", None),
    })
    c = streamuj.StreamujClient("user", "spatne", opener=opener)
    with pytest.raises(streamuj.LoginError):
        c.login()
    post = [r for r in opener.requests if r.data]
    assert post and b"username=user" in post[0].data and b"action_login=" in post[0].data


@pytest.mark.skipif(not os.environ.get("STREAMUJ_LIVE"), reason="živý test: STREAMUJ_LIVE=1")
def test_live_public_video():
    c = streamuj.StreamujClient()
    streams, _ = c.get_streams("0645c382f89879db00bc")
    assert streams and streams[0]["url"].startswith("https://www.streamuj.tv/video/")
    final = c.resolve(streams[0]["url"])
    assert final.startswith("https://") and ".mp4" in final
    info = c.video_info("0645c382f89879db00bc")
    assert info["title"] == "South Park 1x01"
