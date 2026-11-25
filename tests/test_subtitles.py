"""Titulky z OpenSubtitles.com (``app_subtitles``): hledání, stažení, zapamatování k videu – proti
falešnému serveru se stejným tvarem odpovědí jako oficiální rozhraní."""
import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request

import pytest

from test_app_api import api, _fill  # noqa: F401 – stejná příprava logiky jako ostatní testy aplikace
from helpers import REF

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "android", "app", "src", "main",
                                "python"))
import app_api  # noqa: E402
import app_subtitles  # noqa: E402

EPISODE = "12:archa:%016x" % 12


class OpenSubtitles(http.server.BaseHTTPRequestHandler):
    seen = []
    remaining = 19

    def log_message(self, *args):
        pass

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        self.seen.append(("GET", url.path, url.query, dict(self.headers)))
        if url.path.startswith("/api/") and self.headers.get("Api-Key") != "klic":   # soubor ke stažení klíč nechce
            return self._json(403, {"message": "You cannot consume this service"})
        if url.path == "/api/v1/subtitles":
            query = dict(urllib.parse.parse_qsl(url.query))
            data = []
            if query.get("tmdb_id") == "11" or query.get("parent_tmdb_id") == "77":
                data = [
                    {"attributes": {"language": "sk", "release": "Pelisky.1999.720p.BluRay", "download_count": 900,
                                    "uploader": {"name": "jano"}, "files": [{"file_id": 3, "file_name": "a.srt"}]}},
                    {"attributes": {"language": "cs", "release": "Pelisky.1999.1080p.WEB-DL.CZ", "download_count": 50,
                                    "files": [{"file_id": 1, "file_name": "b.srt"}]}},
                    {"attributes": {"language": "cs", "release": "Pelisky 1999 DVDRip", "download_count": 5000,
                                    "machine_translated": True, "files": [{"file_id": 2, "file_name": "c.srt"}]}},
                    {"attributes": {"language": "cs", "release": "bez souboru", "files": []}}]
            return self._json(200, {"total_count": len(data), "data": data})
        if url.path == "/soubor/1.srt":
            body = "1\r\n00:00:01,000 --> 00:00:02,000\r\nŽluťoučký kůň\r\n".encode("cp1250")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.seen.append(("POST", self.path, body, dict(self.headers)))
        if self.path == "/api/v1/login":
            if body != {"username": "petr", "password": "tajne"}:
                return self._json(401, {"message": "Error, invalid username/password"})
            return self._json(200, {"token": "TOKEN", "user": {"allowed_downloads": 20}, "status": 200})
        if self.path == "/api/v1/download":
            if self.headers.get("Authorization") != "Bearer TOKEN":
                return self._json(406, {"message": "No session"})
            if OpenSubtitles.remaining <= 0:
                return self._json(406, {"message": "limit", "reset_time": "23 hodin"})
            OpenSubtitles.remaining -= 1
            host = "http://127.0.0.1:%d" % self.server.server_address[1]
            return self._json(200, {"link": host + "/soubor/%d.srt" % body["file_id"], "file_name": "x.srt",
                                    "requests": 1, "remaining": OpenSubtitles.remaining})
        self._json(404, {"message": "not found"})


@pytest.fixture
def server(monkeypatch):
    OpenSubtitles.seen = []
    OpenSubtitles.remaining = 19
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OpenSubtitles)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/api/v1" % httpd.server_address[1]
    monkeypatch.setattr(app_subtitles, "API", base)
    app_subtitles._SESSION.update(token="", at=0, base=base, user="")
    # bez systémové proxy: falešný server běží na tomto počítači. Spojení se skládá ručně, protože příprava
    # testů aplikace nahrazuje build_opener falešnou službou iVysílání.
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler({}), urllib.request.HTTPHandler(),
                    urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    monkeypatch.setattr(urllib.request, "urlopen", opener.open)
    yield OpenSubtitles
    httpd.shutdown()


def test_without_a_key_the_player_does_not_offer_opensubtitles(api, server, monkeypatch):
    _fill(2)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    assert api("play", vid=REF)["subtitle_search"] is False
    error = api("subtitles_search", track=REF)["error"]
    assert "klíč k rozhraní OpenSubtitles" in error and server.seen == []      # bez klíče se nikam neposílá


def test_search_download_and_remember_czech_subtitles(api, server, monkeypatch):
    _fill(2)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {"CZ": "https://cdn.x/cz.vtt"}))
    with app_api._library() as lib:
        lib.set_kv(app_api.ART_PREFIX + REF, "movie:11")           # pustilo se z Objevuj: známe TMDB
    api("settings_set", os_key="klic", os_user="petr", os_password="tajne")
    settings = api("settings_get")
    assert (settings["os_key"], settings["os_user"], settings["has_os_password"]) == ("klic", "petr", True)
    assert "os_password" not in settings                                    # heslo se nevrací
    assert api("play", vid=REF)["subtitle_search"] is True

    found = api("subtitles_search", track=REF)
    assert found["title"] == "Titulky z OpenSubtitles" and found["empty"] == ""
    # nejdřív titulky ke stejné verzi souboru, strojový překlad nakonec, položka bez souboru vynechaná
    assert [item["file_id"] for item in found["items"]] == [1, 3, 2]
    assert found["items"][0]["subtitle"] == "Čeština · 50× staženo"
    assert found["items"][1]["subtitle"] == "Slovenčina · 900× staženo · jano"
    assert "strojový překlad" in found["items"][2]["subtitle"]
    method, path, query, headers = next(item for item in server.seen if item[1] == "/api/v1/subtitles")
    assert query == "languages=cs%2Csk&tmdb_id=11&type=movie"               # seřazené, malými písmeny
    assert headers["Api-Key"] == "klic" and headers["User-Agent"].startswith("Promitarna")
    assert headers["Authorization"] == "Bearer TOKEN"
    searches = len([item for item in server.seen if item[1] == "/api/v1/subtitles"])
    api("subtitles_search", track=REF)
    assert len([item for item in server.seen if item[1] == "/api/v1/subtitles"]) == searches   # z paměti

    got = api("subtitles_download", track=REF, file_id=1, lang="cs")
    assert got["toast"] == "Titulky jsou stažené. Dnes zbývá stažení: 18." and got["lang"] == "cs"
    with open(got["path"], encoding="utf-8") as text:
        assert "Žluťoučký kůň" in text.read()                              # z windows-1250 do UTF-8
    stream = api("play", vid=REF)                                           # příště se přidají samy, jako první
    assert stream["subtitles"] == [got["path"], "https://cdn.x/cz.vtt"] and stream["subtitle_langs"] == ["cs", "cs"]
    assert len([item for item in server.seen if item[1] == "/api/v1/login"]) == 1   # přihlášení se pamatuje

    server.remaining = 0
    assert "limit stažení" in api("subtitles_download", track=REF, file_id=1, lang="cs")["error"]


def test_episode_is_searched_by_series_season_and_episode(api, server):
    _fill(2)
    with app_api._library() as lib:
        lib.set_kv(app_api.ART_PREFIX + EPISODE, "tv:77:1:2")
    api("settings_set", os_key="klic")                                      # bez jména a hesla: hledat jde
    found = api("subtitles_search", track=EPISODE)
    assert [item["file_id"] for item in found["items"]] == [1, 3, 2]
    query = next(item[2] for item in server.seen if item[1] == "/api/v1/subtitles")
    assert query == "episode_number=2&languages=cs%2Csk&parent_tmdb_id=77&season_number=1"
    assert not any(item[1] == "/api/v1/login" for item in server.seen)
    assert "Authorization" not in next(item[3] for item in server.seen if item[1] == "/api/v1/subtitles")


def test_unknown_title_falls_back_to_name_and_explains_empty_results(api, server):
    _fill(2)
    api("settings_set", os_key="klic")
    found = api("subtitles_search", track=REF)
    query = dict(urllib.parse.parse_qsl(next(item[2] for item in server.seen if item[1] == "/api/v1/subtitles")))
    assert query["query"] == "pelíšky" and query["year"] == "1999" and query["type"] == "movie"
    assert found["items"] == [] and found["empty"].startswith("České ani slovenské titulky k tomuto filmu")


def test_wrong_key_and_password_are_explained_in_czech(api, server):
    _fill(2)
    api("settings_set", os_key="spatny")
    assert "odmítl klíč" in api("subtitles_search", track=REF)["error"]
    api("settings_set", os_key="klic", os_user="petr", os_password="spatne")
    assert "nepřijal jméno nebo heslo" in api("subtitles_search", track=REF)["error"]
    assert "jen k videím z Přehraj.to" in api("subtitles_search", track="ivys:123")["error"]


def test_backup_leaves_out_the_opensubtitles_password():
    import app_backup
    assert "os_password" in app_backup.SECRET and "password" in app_backup.SECRET
