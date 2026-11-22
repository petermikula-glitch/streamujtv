"""Rozhraní Android aplikace nad logikou doplňku (android/app/src/main/python/app_api.py)."""
import json
import os
import sys

import pytest

import ivysilani
import nameparser
from helpers import Fake, REF
from test_ivysilani import FakeService

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "android", "app", "src", "main",
                                "python"))
import app_api  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    app_api.configure(str(tmp_path))
    service = FakeService()
    monkeypatch.setattr(ivysilani.urllib.request, "build_opener", lambda *handlers: service)

    def call(action, **params):
        result = json.loads(app_api.call(action, json.dumps(params)))
        return result["data"] if result["ok"] else result
    call.service = service
    call.raw = lambda action, **params: json.loads(app_api.call(action, json.dumps(params)))
    return call


def _fill(count=3):
    with app_api._library() as lib:
        lib.upsert_video(REF, "Pelisky.1999.CZ.1080p", "account", nameparser.parse("Pelisky.1999.CZ.1080p"))
        lib.upsert_video("2:pelisky-en:00000000000000aa", "Pelisky.1999.EN.720p", "account",
                         nameparser.parse("Pelisky.1999.EN.720p"))
        for n in range(1, count + 1):
            raw = "Archa.S01E%02d.CZ" % n
            lib.upsert_video("%d:archa:%016x" % (10 + n, 10 + n), raw, "account", nameparser.parse(raw))
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "year": 1999, "rating": 91,
                                               "poster": "https://img/pelisky.jpg"})


def _titles(items):
    return [item["title"] for item in items]


def test_empty_home_leads_to_login_and_always_offers_television_and_tools(api):
    rows = api("home")["rows"]
    assert [row["title"] for row in rows] == ["Začínáme", "iVysílání České televize", "Nástroje"]
    assert rows[0]["items"][0]["target"] == {"type": "settings"}
    television = rows[1]["items"]
    assert _titles(television) == ["Živé vysílání", "Seriály", "Filmy"]
    assert television[1]["target"] == {"type": "list", "call": "ivys_category", "params": {"cid": "3976"},
                                       "title": "Seriály"}
    assert [item["target"]["type"] for item in rows[2]["items"]] == ["search", "list", "command", "command",
                                                                     "settings"]
    assert rows[2]["items"][1]["target"]["call"] == "downloads"


def test_catalogue_rows_lists_and_search(api):
    _fill()
    with app_api._library() as lib:
        lib.record_play(REF, 1200, 6000)
    rows = {row["title"]: row["items"] for row in api("home")["rows"]}
    assert "Začínáme" not in rows
    assert _titles(rows["Rozkoukané"]) == ["Pelíšky"] and rows["Rozkoukané"][0]["progress"] == 20
    film = rows["Nově přidané filmy"][0]
    assert film == {"title": "Pelíšky", "subtitle": "1999 · 91 % · CZ dabing, EN", "image": "https://img/pelisky.jpg",
                    "progress": 20, "wide": False, "watched": False, "icon": "movies", "menu": film["menu"],
                    "target": {"type": "play", "call": "play", "params": {"vid": REF}}}  # česká verze ve vyšší kvalitě
    assert [item["title"] for item in film["menu"]] == ["Přehrát", "Přehrát od začátku", "Stáhnout na disk"]
    assert rows["Nově přidané filmy"][-1]["target"]["call"] == "movies"
    assert rows["Nově přidané filmy"][-1]["icon"] == "movies"  # dlaždice bez obrázku nese ikonu
    assert rows["Seriály"][0]["target"] == {"type": "list", "call": "show", "params": {"key": "show:archa"},
                                            "title": "Archa"}

    listing = api("movies", order="title")
    assert _titles(listing["items"]) == ["Pelíšky"] and listing["next"] is None
    episodes = api("show", key="show:archa")
    assert episodes["title"] == "Archa" and _titles(episodes["items"]) == ["1×01", "1×02", "1×03"]
    assert episodes["items"][0]["target"]["params"] == {"vid": "11:archa:%016x" % 11}
    assert api("show", key="show:neni") == {"ok": False, "error": "Seriál v katalogu není."}
    assert _titles(api("played")["items"]) == ["Pelíšky"]

    found = {row["title"]: row["items"] for row in api("search", q="archa")["rows"]}
    assert _titles(found["Seriály v katalogu"]) == ["Archa"]
    assert "Pořady v iVysílání" in found and found["Díly v iVysílání"][0]["target"]["call"] == "ivys_play"
    assert api("search", q="  ") == {"rows": []}


def test_long_listings_are_paged(api):
    with app_api._library() as lib, lib.conn:
        for n in range(app_api.PAGE + 5):
            raw = "Film %03d %d CZ" % (n, 1950 + n % 70)
            lib.upsert_video("%d:f:%016x" % (n + 1, n + 1), raw, "account", nameparser.parse(raw))
    first = api("movies", order="title")
    assert len(first["items"]) == app_api.PAGE
    assert first["next"] == {"call": "movies", "params": {"order": "title", "offset": app_api.PAGE}}
    second = api(first["next"]["call"], **first["next"]["params"])
    assert len(second["items"]) == 5 and second["next"] is None
    assert api("movies", order="; DROP TABLE", offset="x")["items"]  # neplatné vstupy se nahradí výchozími


def test_playback_resolves_remembers_link_and_tracks_progress(api, monkeypatch):
    from conftest import fixture
    _fill()
    assert "vyplňte e-mail" in api("play", vid=REF)["error"]
    api("settings_set", username="a@b.cz", password="heslo")
    assert api("settings_get") == {"username": "a@b.cz", "has_password": True, "tmdb_key": "", "download_dir": ""}
    page = fixture("video_synthetic.html")
    clients = []

    def make_client(username, password, cookie_file=None, **_):
        client = Fake({"/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60?do=download": ("", None, "https://cdn.x/orig.mkv?sig=1", 302),
                       "/pelisky-1999-cz-1080p/4f9a1b2c3d4e5f60": (page, None, "", 200),
                       "/profil": ("ok", None, "", 200)})
        client.api = lambda path: (_ for _ in ()).throw(app_api.provider.ProviderError("API HTTP 403"))
        clients.append(client)
        return client

    monkeypatch.setattr(app_api.provider, "make_client", make_client)
    stream = api("play", vid=REF)
    assert stream["url"] == "https://cdn.x/orig.mkv?sig=1" and stream["title"] == "Pelíšky" and stream["track"] == REF
    assert stream["position"] == 0 and stream["headers"]["User-Agent"] and stream["hls"] is False
    assert api("play", vid=REF)["url"] == stream["url"] and len(clients) == 1    # podruhé ze zapamatované adresy

    assert api("progress_save", track=REF, position=1500, total=6000, started=True) == {"saved": True}
    assert api("play", vid=REF)["position"] == 1500
    assert api("play", vid=REF, restart=True)["position"] == 0
    assert api("progress_save", track=REF, position=5990, total=6000, ended=True) == {"saved": True}
    with app_api._library() as lib:
        assert lib.watched() == {REF} and lib.resume_points() == {}
    assert api("progress_save", track="", position=5, total=10) == {"saved": False}

    api("link_failed", track=REF)                       # přehrávač adresu odmítl → příště se načte znovu
    api("play", vid=REF)
    assert len(clients) == 2
    assert "není" in api("play", vid="9:x:00000000000000ff")["error"]


def test_sync_reports_progress_and_blocks_parallel_runs(api, monkeypatch):
    api("settings_set", username="a@b.cz", password="heslo", tmdb_key="k" * 32)
    seen = []

    class Client(object):
        listing_complete = True

        def my_videos(self, progress=None):
            progress(3, 2)
            seen.append(api("progress"))
            assert "Jiná operace" in api("enrich")["error"]      # druhá dlouhá operace se nespustí
            return [{"id": REF, "title": "Pelisky.1999.CZ", "duration": 6000},
                    {"id": "5:archa:00000000000000bb", "title": "Archa.S01E01.CZ", "duration": 2800}]

    monkeypatch.setattr(app_api.provider, "make_client", lambda *a, **k: Client())
    monkeypatch.setattr(app_api.catalog, "enrich", lambda lib, metas, **kw: {
        "ok": len(metas), "notfound": 0, "csfd_blocked": False})
    result = api("sync")
    assert result["message"].startswith("Videí: 2, nových: 2, odebraných: 0.") and "doplněna u 2" in result["message"]
    assert seen == [{"text": "Načítám videa z účtu: stránka 3, 2 videí", "busy": True}]
    assert api("progress") == {"text": "", "busy": False}
    rows = {row["title"]: row["items"] for row in api("home")["rows"]}
    assert "Naposledy:" in rows["Nástroje"][2]["subtitle"] and rows["Nástroje"][4]["subtitle"] == "a@b.cz"

    Client.listing_complete = False
    assert "Nic se neodebralo" in api("sync")["message"]

    def failing(*a, **k):
        raise app_api.provider.LoginError("špatné heslo")

    monkeypatch.setattr(app_api.provider, "make_client", failing)
    assert "Přihlášení" in api("sync")["error"] and api("progress")["busy"] is False


def test_ivysilani_browsing_playback_and_my_shows(api):
    live = api("ivys_live")["items"]
    assert _titles(live)[:2] == ["ČT1 · Pečení na neděli", "ČT sport · SP v rychlobruslení"]
    assert "pak" in live[0]["subtitle"] and live[0]["target"]["params"] == {"channel": "CH_1"}
    assert len(live) == 8                                  # i kanály, ke kterým zrovna není program

    category = api("ivys_category", cid="3947")["items"]
    assert _titles(category)[:2] == ["Všechny pořady", "Krimi"]
    film = next(item for item in category if item["title"].startswith("Krakonošovo"))
    assert film["target"] == {"type": "play", "call": "ivys_play", "params": {"show": "11"}}
    listing = api("ivys_listing", cid="3958")
    assert len(listing["items"]) == 80 and listing["next"]["params"] == {"cid": "3958", "offset": 80}

    seasons = api("ivys_show", id="12")
    assert _titles(seasons["items"]) == ["I. řada", "Filmy o seriálu", "Všechny díly", "Přidat do Mých pořadů"]
    episodes = api("ivys_show", id="12", season="season:1")
    assert len(episodes["items"]) == 41 and episodes["next"]["params"]["offset"] == 40
    assert api("ivys_favorite", id="12") == {"message": "Přidáno do Mých pořadů."}
    assert _titles(api("ivys_show", id="12")["items"])[-1] == "Odebrat z Mých pořadů"
    with app_api._library() as lib:
        lib.ivys_favorite_seen("12", 120)
    assert api("ivys_favorites")["items"][0]["subtitle"] == "nové díly: 3"
    assert "Moje pořady" in _titles(next(row for row in api("home")["rows"]
                                         if row["title"].startswith("iVysílání"))["items"])

    stream = api("ivys_play", idec="211522160100014")
    assert stream["hls"] and stream["track"] == "ivys:211522160100014" and stream["subtitles"] == ["https://ctfs/s.vtt"]
    assert stream["title"] == "Na plovárně · Terry Pratchett"
    api("progress_save", track=stream["track"], position=400, total=1326)
    assert api("ivys_play", idec="211522160100014")["position"] == 400
    progress = next(row for row in api("home")["rows"] if row["title"] == "Rozkoukané")["items"]
    assert progress[0]["title"] == "Na plovárně · Terry Pratchett" and progress[0]["progress"] == 30
    live_stream = api("ivys_play", channel="CH_24", timeshift=True)
    assert live_stream["live"] and live_stream["url"].endswith("token=shift") and live_stream["track"] == ""
    assert api("ivys_play", idec="999")["error"].startswith("ČT bohužel nemá práva")


def test_call_never_raises(api):
    assert api.raw("neexistuje") == {"ok": False, "error": "Neznámá akce: neexistuje"}
    assert json.loads(app_api.call("home", "[1]")) == {"ok": False, "error": "Neplatné parametry."}
    assert json.loads(app_api.call("home", "{rozbité"))["ok"] is False
    assert set(app_api.ACTIONS) >= {"home", "play", "sync", "ivys_play", "progress_save"}


def test_configure_passes_the_system_proxy_to_urllib(tmp_path, monkeypatch):
    # urllib na Androidu systémovou proxy nezjistí; aplikace ji předává při startu
    monkeypatch.setenv("https_proxy", "")
    monkeypatch.setenv("http_proxy", "")
    app_api.configure(str(tmp_path), "http://10.0.0.1:8080")
    assert os.environ["https_proxy"] == "http://10.0.0.1:8080"
    monkeypatch.setenv("https_proxy", "http://jina:1")
    app_api.configure(str(tmp_path))  # bez proxy se prostředí nemění
    assert os.environ["https_proxy"] == "http://jina:1"


def test_cards_in_one_listing_share_a_shape(api):
    # iVysílání: plakát má jen část pořadů, obrázek na šířku všechny – výpis je celý na šířku
    card = app_api._ivys_show_card({"id": "1", "title": "Pořad", "type": "show", "image": "sirka.jpg",
                                    "poster": "plakat.jpg"})
    assert (card["wide"], card["image"]) == (True, "sirka.jpg")
    # katalog: bez plakátu zbývá náhled z Přehraj.to, který je na šířku
    state = {"resume": {}, "watched": set()}
    plain = app_api._movie_card({"key": "movie:a|2000", "title": "A", "thumb": "nahled.jpg", "first_video": REF}, state)
    poster = app_api._movie_card({"key": "movie:b|2000", "title": "B", "poster": "plakat.jpg", "first_video": REF},
                                 state)
    assert (plain["wide"], poster["wide"]) == (True, False)


def _link(vid, url):
    with app_api._library() as lib:
        lib.set_kv(app_api.LINK_PREFIX + vid, json.dumps({"url": url, "subtitles": {"cs": "https://cdn.x/t.vtt"},
                                                         "ts": int(app_api.time.time())}))


def test_download_queue_progress_local_playback_and_removal(api, tmp_path):
    _fill()
    disk = tmp_path / "usb"
    disk.mkdir()
    assert api("downloads")["items"] == [] and "Podržte" in api("downloads")["empty"]
    assert "není připojený" in api("download_next", dir="")["error"]
    assert api("download_next", dir=str(disk)) == {"vid": ""}                 # prázdná fronta
    assert api("download_add", vid=REF) == {"message": "Zařazeno ke stažení: Pelíšky", "start": True}
    assert "není" in api("download_add", vid="9:x:00000000000000ff")["error"]
    _link(REF, "https://cdn.x/Pel%C3%AD%C5%A1ky.1999.mkv?sig=1")

    job = api("download_next", dir=str(disk))
    assert job["vid"] == REF and job["url"].startswith("https://cdn.x/") and job["headers"]["User-Agent"]
    assert job["path"] == str(disk / "Pelíšky.mkv") and job["part"] == job["path"] + ".part"   # přípona podle adresy
    assert job["subtitles"] == [{"url": "https://cdn.x/t.vtt", "path": str(disk / "Pelíšky.1.vtt")}]
    assert api("download_next", dir=str(disk))["path"] == job["path"]        # rozdělaná položka se vrací znovu
    assert api("download_progress", vid=REF, done=50, total=200) == {"cancel": False}
    card = api("downloads")["items"][0]
    assert (card["subtitle"], card["progress"]) == ("Stahuje se · 25 % z 0 MB", 25)
    assert [item["title"] for item in card["menu"]] == ["Přehrát", "Zrušit stahování"]
    rows = {row["title"]: row["items"] for row in api("home")["rows"]}
    assert _titles(rows["Stažené na disku"]) == ["Pelíšky"] and "1 ve frontě" in rows["Nástroje"][1]["subtitle"]

    (disk / "Pelíšky.mkv").write_bytes(b"x" * 200)
    (disk / "Pelíšky.1.vtt").write_text("WEBVTT")
    assert api("download_finish", vid=REF, size=200, subtitles=[job["subtitles"][0]["path"]]) == {"saved": True}
    stream = api("play", vid=REF)                                            # hraje z disku, bez přihlášení
    assert stream["local"] is True and stream["url"] == job["path"] and stream["subtitles"] == [str(disk / "Pelíšky.1.vtt")]
    rows = {row["title"]: row["items"] for row in api("home")["rows"]}
    film = rows["Nově přidané filmy"][0]
    assert film["subtitle"].startswith("Staženo · ") and film["menu"][-1]["title"] == "Smazat stažený soubor"
    assert "už je stažené" in api("download_add", vid=REF)["error"]
    assert api("download_progress", vid=REF, done=1, total=2) == {"cancel": True}   # hotové se už nestahuje

    (disk / "Pelíšky.mkv").rename(disk / "jinde.mkv")                        # disk odpojen: zpět na stream
    assert api("downloads")["items"][0]["subtitle"] == "Disk není připojený"
    assert api("play", vid=REF)["local"] is False
    (disk / "jinde.mkv").rename(disk / "Pelíšky.mkv")
    assert api("download_remove", vid=REF) == {"message": "Smazáno: Pelíšky"}
    assert not (disk / "Pelíšky.mkv").exists() and not (disk / "Pelíšky.1.vtt").exists()
    assert api("downloads")["items"] == [] and api("download_remove", vid=REF) == {"message": ""}


def test_download_failures_and_cancel(api, tmp_path, monkeypatch):
    _fill()
    disk = str(tmp_path)
    api("download_add", vid=REF)
    job_error = api("download_next", dir=disk)                               # bez přihlášení adresu nezíská
    assert job_error == {"vid": ""}
    card = api("downloads")["items"][0]
    assert card["subtitle"].startswith("Chyba: Nejdřív v Nastavení")
    assert [item["title"] for item in card["menu"]] == ["Přehrát", "Zkusit stáhnout znovu", "Odebrat ze stahování"]
    api("download_add", vid=REF)                                             # znovu do fronty
    _link(REF, "https://cdn.x/stream?id=5")
    job = api("download_next", dir=disk)
    assert job["path"].endswith("Pelíšky.mp4")                               # bez přípony v adrese → mp4
    assert api("download_fail", vid=REF, error="Na disku není dost místa") == {"saved": True}
    assert api("downloads")["items"][0]["subtitle"] == "Chyba: Na disku není dost místa"
    api("download_add", vid=REF)
    api("download_next", dir=disk)
    assert api("download_remove", vid=REF) == {"message": "Stahování zrušeno."}
    assert api("download_progress", vid=REF, done=1, total=2) == {"cancel": True}
    assert app_api._safe_name('A/B: "C" <D>? *.') == "A B C D" and app_api._size_text(5 * 1024 ** 3) == "5,0 GB"


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_update_check_finds_newer_release_and_throttles(api, monkeypatch):
    releases = [
        {"tag_name": "v1.4.1", "assets": [{"name": "x.apk", "browser_download_url": "https://x/kodi.apk"}]},
        {"tag_name": "app-v0.4.0", "prerelease": True, "assets": [{"name": "a.apk", "browser_download_url": "u4"}]},
        {"tag_name": "app-v0.3.0", "body": "Stahování na disk", "assets": [
            {"name": "notes.txt", "browser_download_url": "n"},
            {"name": "MojeVidea-0.3.0.apk", "browser_download_url": "https://gh/MojeVidea-0.3.0.apk", "size": 42}]},
        {"tag_name": "app-v0.10.0", "assets": []},                           # vydání bez APK se přeskočí
        {"tag_name": "app-v0.2.0", "assets": [{"name": "old.apk", "browser_download_url": "old"}]},
    ]
    asked = []

    def urlopen(request, timeout=None):
        asked.append(request.full_url)
        if isinstance(releases, Exception):
            raise releases
        return _Response(json.dumps(releases).encode())

    monkeypatch.setattr(app_api.urllib.request, "urlopen", urlopen)
    found = api("update_check", current="0.2.0")
    assert found == {"available": True, "current": "0.2.0", "version": "0.3.0", "size": 42,
                     "url": "https://gh/MojeVidea-0.3.0.apk", "notes": "Stahování na disk"}
    assert asked == [app_api.UPDATE_URL]
    assert api("update_check", current="0.2.0") == {"available": False, "current": "0.2.0"}   # hned znovu se neptá
    assert len(asked) == 1
    assert api("update_check", current="0.2.0", force=True)["available"] is True               # ruční kontrola ano
    assert api("update_check", current="0.3.0", force=True) == {"available": False, "current": "0.3.0"}
    assert api("update_check", current="0.10.0", force=True)["available"] is False             # čísla, ne text
    releases = OSError("bez sítě")
    assert "nepodařilo ověřit" in api("update_check", current="0.2.0", force=True)["error"]
    settings = app_api._settings()
    settings["update_checked"] = 0
    app_api._save_settings(settings)
    assert api("update_check", current="0.2.0") == {"available": False, "current": "0.2.0"}   # automaticky mlčky
