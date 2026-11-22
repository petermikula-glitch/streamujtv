"""Rozhraní Android aplikace nad logikou doplňku (android/app/src/main/python/app_api.py)."""
import json
import os
import sys
import time

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
    monkeypatch.setattr(app_api.provider, "make_client",
                        lambda *args, **kwargs: Fake({"/hledej/": ("<html></html>", None, "", 200)}))

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


def _section(api, name):
    """Řady jedné sekce hlavní obrazovky: {název řady: karty}."""
    section = next(item for item in api("home")["sections"] if item["id"] == name)
    return {row["title"]: row["items"] for row in section["rows"]}


def test_empty_home_leads_to_login_and_always_offers_television_and_tools(api):
    sections = api("home")["sections"]
    assert [(section["id"], section["title"]) for section in sections] == [
        ("prehrajto", "Přehraj.to"), ("ivysilani", "iVysílání"), ("tools", "Nástroje")]
    prehrajto = _section(api, "prehrajto")
    assert list(prehrajto) == ["Začínáme", "Oblíbené", "Objevuj filmy", "Objevuj seriály"]
    assert prehrajto["Začínáme"][0]["target"] == {"type": "settings"}
    assert _titles(prehrajto["Objevuj filmy"]) == ["Novinky", "Populární", "Nejlépe hodnocené", "Podle roku",
                                                    "Podle žánru", "Československé"]
    assert prehrajto["Objevuj seriály"][3]["target"] == {"type": "list", "call": "discover_years",
                                                         "params": {"kind": "tv"}, "title": "Seriály podle roku"}
    television = _section(api, "ivysilani")                  # jen věci z iVysílání
    assert list(television) == ["Živé vysílání", "Kategorie"]
    assert television["Živé vysílání"][0]["target"]["call"] == "ivys_play"
    assert _titles(television["Kategorie"]) == ["Seriály", "Filmy"]
    assert television["Kategorie"][0]["target"] == {"type": "list", "call": "ivys_category",
                                                    "params": {"cid": "3976"}, "title": "Seriály"}
    tools = _section(api, "tools")["Nástroje"]
    assert [item["target"]["type"] for item in tools] == ["search", "list", "command", "command", "settings"]
    assert tools[1]["target"]["call"] == "downloads"


def test_catalogue_rows_lists_and_search(api):
    _fill()
    with app_api._library() as lib:
        lib.record_play(REF, 1200, 6000)
    rows = _section(api, "prehrajto")
    assert "Začínáme" not in rows
    assert _titles(rows["Rozkoukané"]) == ["Pelíšky"] and rows["Rozkoukané"][0]["progress"] == 20
    film = rows["Moje videa – nově přidané filmy"][0]
    assert film == {"title": "Pelíšky", "subtitle": "1999 · 91 % · CZ dabing, EN", "image": "https://img/pelisky.jpg",
                    "progress": 20, "wide": False, "watched": False, "icon": "movies", "menu": film["menu"],
                    "target": {"type": "play", "call": "play", "params": {"vid": REF}}}  # česká verze ve vyšší kvalitě
    assert [item["title"] for item in film["menu"]] == ["Přehrát", "Přehrát od začátku", "Stáhnout na disk",
                                                        "Přidat do oblíbených"]
    assert rows["Moje videa – nově přidané filmy"][-1]["target"]["call"] == "movies"
    assert rows["Moje videa – nově přidané filmy"][-1]["icon"] == "movies"  # dlaždice bez obrázku nese ikonu
    assert rows["Moje videa – seriály"][0]["target"] == {"type": "list", "call": "show", "params": {"key": "show:archa"},
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


def test_web_search_reuses_playback_history_and_downloads(api, monkeypatch):
    from conftest import fixture

    query = "/hledej/Pel%C3%AD%C5%A1ky"
    page_two = query + "?videoListing-page=2"
    listing = fixture("uploads_synthetic.html")
    first_page = listing.replace("/profil/nahrana-videa?videoListing-page=2", page_two)
    episode = "7654321:hra-o-trune-s01e02-cz-tit:aa11bb22cc33dd44"
    client = Fake({page_two: (listing, None, "", 200),
                   query: (first_page, None, "", 200),
                   "/profil": ("ok", None, "", 200)})
    monkeypatch.setattr(app_api.provider, "make_client", lambda *args, **kwargs: client)
    api("settings_set", username="a@b.cz", password="heslo")
    _fill()
    with app_api._library() as lib:
        lib.record_play(REF, 1200, 6000)
    # Skutečný klient a parser Přehraj.to, jen HTML a přehrávací URL jsou testovací.
    monkeypatch.setattr(client, "get_streams", lambda vid: ([{"quality": "1080p", "url": "https://cdn.x/video.mp4"}], ""))
    monkeypatch.setattr(client, "resolve", lambda url: url)

    rows = {row["title"]: row["items"] for row in api("search", q=" Pelíšky ")["rows"]}
    assert _titles(rows["Filmy v katalogu"]) == ["Pelíšky"]
    assert rows["Díly v iVysílání"]
    known, online, more = rows["Videa na Přehraj.to"]
    assert known["progress"] == 20 and known["target"]["params"]["vid"] == REF
    assert online["title"] == "Hra o trůny S01E02 CZ tit"
    assert online["target"]["params"]["vid"] == episode
    assert any(url.endswith(query + "?vp-page=1") for url, _, _ in client.calls)
    assert more["target"]["call"] == "prehrajto_search"
    second = api(more["target"]["call"], **more["target"]["params"])
    assert second["items"] and second["next"] is None and client.calls[-1][0].endswith(page_two)
    with app_api._library() as lib:
        assert lib.find_video(episode) is None

    target = online["target"]
    stream = api(target["call"], **target["params"])
    assert stream["track"] == episode and stream["url"] == "https://cdn.x/video.mp4"
    api("progress_save", track=episode, position=300, total=1200)
    assert api("play", vid=episode)["position"] == 300
    home = _section(api, "prehrajto")
    assert any(card["target"]["params"]["vid"] == episode and card["progress"] == 25
               for card in home["Rozkoukané"])
    assert episode in [card["target"]["params"]["vid"] for card in api("played")["items"]]
    assert _titles(api("shows")["items"]) == ["Archa"]

    download = next(item["target"] for item in online["menu"] if item["target"]["type"] == "download")
    assert api(download["call"], **download["params"])["start"]
    assert api("downloads")["items"][0]["target"]["params"]["vid"] == episode

    with app_api._library() as lib:
        lib.apply_account_snapshot([{"id": episode, "title": online["title"]}], time.time_ns(), True, False)
        assert lib.get_video(episode)["source"] == "account"
    assert "Hra o trůny" in _titles(api("shows")["items"])
    assert api("play", vid=episode)["position"] == 300


@pytest.mark.parametrize("error, detail", [
    (app_api.provider.LoginError("test"), "přihlášení"),
    (app_api.provider.TemporaryError("test"), "Zkuste to znovu"),
])
def test_web_search_failure_keeps_other_sources(api, monkeypatch, error, detail):
    _fill()

    def unavailable(*args):
        raise error

    monkeypatch.setattr(app_api.provider, "search_catalog", unavailable)
    result = api("search", q="archa")
    assert "Přehraj.to" in result["notice"] and detail in result["notice"]
    assert [row["title"] for row in result["rows"]] == [
        "Seriály v katalogu", "Pořady v iVysílání", "Díly v iVysílání"]


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
    settings = api("settings_get")
    assert {key: settings[key] for key in ("username", "has_password", "tmdb_key", "download_dir")} == {
        "username": "a@b.cz", "has_password": True, "tmdb_key": "", "download_dir": ""}
    assert (settings["prefer_language"], settings["prefer_quality"]) == ("dub", "best")     # výchozí předvolby
    assert [item["id"] for item in settings["languages"]] == ["dub", "subs", "any"]
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
    rows = _section(api, "tools")
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
    assert _titles(_section(api, "ivysilani")["Moje pořady"])[-1] == "Moje pořady"

    stream = api("ivys_play", idec="211522160100014")
    assert stream["hls"] and stream["track"] == "ivys:211522160100014" and stream["subtitles"] == ["https://ctfs/s.vtt"]
    assert stream["title"] == "Na plovárně · Terry Pratchett"
    api("progress_save", track=stream["track"], position=400, total=1326)
    assert api("ivys_play", idec="211522160100014")["position"] == 400
    progress = _section(api, "ivysilani")["Rozkoukané"]                   # rozkoukané z iVysílání jen tady
    assert "Rozkoukané" not in _section(api, "prehrajto")
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
    rows = _section(api, "prehrajto")
    assert _titles(rows["Stažené na disku"]) == ["Pelíšky"]
    assert "1 ve frontě" in _section(api, "tools")["Nástroje"][1]["subtitle"]

    (disk / "Pelíšky.mkv").write_bytes(b"x" * 200)
    (disk / "Pelíšky.1.vtt").write_text("WEBVTT")
    assert api("download_finish", vid=REF, size=200, subtitles=[job["subtitles"][0]["path"]]) == {"saved": True}
    stream = api("play", vid=REF)                                            # hraje z disku, bez přihlášení
    assert stream["local"] is True and stream["url"] == job["path"] and stream["subtitles"] == [str(disk / "Pelíšky.1.vtt")]
    rows = _section(api, "prehrajto")
    film = rows["Moje videa – nově přidané filmy"][0]
    assert film["subtitle"].startswith("Staženo · ") and "Smazat stažený soubor" in _titles(film["menu"])
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


def test_favorites_from_catalogue_cards(api):
    _fill()
    assert "Zatím nemáte" in api("favorites")["empty"] and api("favorites")["items"] == []
    placeholder = _section(api, "prehrajto")["Oblíbené"]
    assert _titles(placeholder) == ["Oblíbené"] and placeholder[0]["target"]["call"] == "favorites"
    film = _section(api, "prehrajto")["Moje videa – nově přidané filmy"][0]
    add = film["menu"][-1]
    assert add["title"] == "Přidat do oblíbených" and add["target"]["type"] == "command"
    assert api(add["target"]["call"], **add["target"]["params"]) == {"message": "Přidáno do oblíbených: Pelíšky"}
    series = _section(api, "prehrajto")["Moje videa – seriály"][0]
    assert _titles(series["menu"]) == ["Otevřít", "Přidat do oblíbených"]
    api(series["menu"][1]["target"]["call"], **series["menu"][1]["target"]["params"])

    favorites = _section(api, "prehrajto")["Oblíbené"]              # naposledy přidané první
    assert _titles(favorites) == ["Archa", "Pelíšky"]
    assert favorites[1]["target"] == {"type": "play", "call": "play", "params": {"vid": REF}}
    assert favorites[0]["target"]["call"] == "show"
    remove = favorites[1]["menu"][-1]
    assert remove["title"] == "Odebrat z oblíbených"
    assert api(remove["target"]["call"], **remove["target"]["params"]) == {"message": "Odebráno z oblíbených."}
    assert _titles(api("favorites")["items"]) == ["Archa"]
    assert "Chybí" in api("favorite_toggle")["error"]


def _fake_tmdb(monkeypatch, answers):
    calls = []

    def fetch(self, path, timeout, params):
        calls.append((path, dict(params)))
        if path not in answers:
            raise app_api.TmdbError("TMDB: HTTP 404")   # jako skutečný klient u neznámé adresy
        return answers[path]

    monkeypatch.setattr(app_api.Tmdb, "_fetch", fetch)
    return calls


def test_discover_lists_come_from_tmdb(api, monkeypatch):
    assert "klíč TMDB" in api("discover", kind="movie", category="popular")["error"]
    api("settings_set", tmdb_key="k" * 32)
    film = {"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08",
            "poster_path": "/p.jpg", "vote_average": 8.6}
    calls = _fake_tmdb(monkeypatch, {
        "/movie/popular": {"results": [film, {"title": "bez čísla"}], "total_pages": 3},
        "/discover/tv": {"results": [{"id": 77, "name": "Archa", "first_air_date": "1996-01-01"}], "total_pages": 1},
        "/genre/movie/list": {"genres": [{"id": 35, "name": "Komedie"}, {"id": 18, "name": "Drama"}]},
    })
    listing = api("discover", kind="movie", category="popular")
    check, card = listing["items"]                                      # položka bez čísla TMDB se přeskočí
    assert check["title"] == "Ověřit dostupnost" and check["target"]["call"] == "discover_check"
    assert (card["title"], card["subtitle"], card["wide"]) == ("Pelíšky", "1999 · 86 % · film", False)
    assert card["image"] == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert card["target"] == {"type": "title", "call": "discover_title", "title": "Pelíšky", "params": {
        "kind": "movie", "id": 11, "title": "Pelíšky", "year": 1999, "poster": card["image"], "orig": "Pelisky"}}
    assert _titles(card["menu"]) == ["Otevřít", "Přidat do oblíbených"]
    assert listing["next"] == {"call": "discover", "params": {"kind": "movie", "category": "popular", "page": 2}}
    api("discover", kind="movie", category="popular")                    # podruhé z mezipaměti
    assert [path for path, _ in calls] == ["/movie/popular"]

    czech = api("discover", kind="tv", origin="CZ|SK|XC", year="1990s")
    assert _titles(czech["items"]) == ["Archa"] and czech["items"][0]["subtitle"] == "1996 · seriál"
    assert czech["next"] is None
    path, sent = calls[-1]
    assert path == "/discover/tv" and sent["with_origin_country"] == "CZ|SK|XC"
    assert (sent["first_air_date.gte"], sent["first_air_date.lte"]) == ("1990-01-01", "1999-12-31")

    years = api("discover_years", kind="movie")["items"]
    assert years[0]["title"] == str(app_api.time.localtime().tm_year)
    decade = next(item for item in years if item["title"] == "1990–1999")
    assert decade["target"]["params"] == {"kind": "movie", "year": "1990s"}
    genres = api("discover_genres", kind="movie", origin="CZ|SK|XC")["items"]
    assert _titles(genres) == ["Komedie", "Drama"] and genres[0]["icon"] == "folder:movies"
    assert genres[0]["target"]["params"] == {"kind": "movie", "genre": "35", "origin": "CZ|SK|XC"}
    special = api("discover_czechoslovak", kind="tv")["items"]
    assert _titles(special) == ["Nejoblíbenější", "Podle roku", "Podle žánru"]
    assert special[0]["target"]["params"] == {"kind": "tv", "origin": "CZ|SK|XC"}


def _web(vid, title, duration=0, size="", quality=""):
    return {"id": "%s:video-%s:%016x" % (vid, vid, vid), "title": title, "duration": duration, "size": size,
            "quality": quality, "thumb": ""}


def _catalogue_search(monkeypatch, found):
    asked = []

    def search_catalog(client, query, page, cursor):
        asked.append((query, page))
        return found.get(query, []), ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search_catalog)
    return asked


FILM = {"kind": "movie", "id": 11, "title": "Pelíšky", "year": 1999, "poster": "https://img/p.jpg", "orig": "Pelisky"}
FILM_DETAIL = {"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08",
               "vote_average": 8.6, "runtime": 116, "overview": "Rodinná komedie.", "poster_path": "/p.jpg",
               "backdrop_path": "/b.jpg", "genres": [{"name": "Komedie"}, {"name": "Drama"}]}


def test_title_page_cleans_and_orders_versions(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [
        _web(31, "Pelisky 1999 EN 720p", 2 * hour, "1 GB"),
        _web(32, "Pelisky.1999.CZ.1080p", 2 * hour, "4 GB"),
        _web(33, "Pelisky 2 (2010) CZ", 2 * hour),                        # jiný film
        _web(34, "Pelisky 2005 CZ", 2 * hour),                            # jiný rok
        _web(35, "Pelisky 1999 CZ trailer", 120, "20 MB"),                # ukázka: mnohem kratší než film
        _web(36, "Pelisky.1999.CZ.HDCAM", 2 * hour, "900 MB"),            # kino kopie
        _web(37, "Pelisky 1999 CZ 1080p reup", 2 * hour, "4 GB", "1080p"),   # tentýž soubor nahraný znovu
        _web(38, "Pelisky 1999 CZ tit 2160p", 2 * hour, "9 GB"),
    ]})
    page = api("discover_title", **FILM)
    assert page["detail"] == {"title": "Pelíšky", "meta": "1999 · 86 % · 1 h 56 min · Komedie, Drama",
                              "plot": "Rodinná komedie.", "poster": "https://image.tmdb.org/t/p/w500/p.jpg",
                              "backdrop": "https://image.tmdb.org/t/p/w1280/b.jpg"}
    versions, extras = page["rows"]
    assert versions["title"] == "Verze na Přehraj.to (4)" and extras["title"] == "Ukázky a neúplné soubory"
    # výchozí předvolby: český dabing a nejvyšší kvalita; kino kopie až na konec, duplicita sloučená
    assert _titles(versions["items"]) == ["CZ dabing · 1080p", "CZ titulky · 2160p", "EN · 720p", "CZ dabing"]
    best = versions["items"][0]
    assert best["badges"] == ["CZ", "1080p"] and best["wide"] is True
    assert best["subtitle"] == "4 GB · 2 h 00 min · Pelisky.1999.CZ.1080p"
    assert "kino kopie" in versions["items"][3]["subtitle"] and versions["items"][1]["badges"] == ["TIT", "2160p"]
    assert "ukázka nebo část" in extras["items"][0]["subtitle"]
    assert best["target"]["params"]["choice"] == "movie:11" and "Stáhnout na disk" in _titles(best["menu"])
    play, favourite, manual = page["actions"]
    assert (play["title"], play["subtitle"]) == ("Přehrát", "CZ dabing · 1080p") and play["target"] == best["target"]
    assert favourite["title"] == "Přidat do oblíbených"
    assert manual["target"] == {"type": "search", "call": "search", "params": {"q": "Pelíšky"}}
    assert asked == [("Pelíšky 1999", 1)]

    api("settings_set", prefer_language="subs", prefer_quality="720")       # originál s titulky, nejblíž 720p
    ordered = _titles(api("discover_title", **FILM)["rows"][0]["items"])
    assert ordered == ["CZ titulky · 2160p", "EN · 720p", "CZ dabing · 1080p", "CZ dabing"]
    api("settings_set", prefer_language="any", prefer_quality="small")
    assert _titles(api("discover_title", **FILM)["rows"][0]["items"])[0] == "EN · 720p"   # nejmenší soubor
    api("settings_set", prefer_language="dub", prefer_quality="best")

    api("play_failed", track=best["target"]["params"]["vid"])                # nešla přehrát → příště až za ostatní
    again = api("discover_title", **FILM)["rows"][0]["items"]
    assert again[0]["title"] == "CZ titulky · 2160p" and "nešlo přehrát" in again[2]["subtitle"]
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    english = next(card for card in again if card["title"] == "EN · 720p")
    api("play", **english["target"]["params"])                               # ručně zvolená verze je příště první
    chosen = api("discover_title", **FILM)
    assert chosen["rows"][0]["items"][0]["title"] == "EN · 720p"
    assert "naposledy zvolená" in chosen["rows"][0]["items"][0]["subtitle"]
    assert chosen["actions"][0]["subtitle"] == "EN · 720p"

    api(favourite["target"]["call"], **favourite["target"]["params"])        # oblíbený titul z Objevuj
    favorite = _section(api, "prehrajto")["Oblíbené"][0]
    assert favorite["title"] == "Pelíšky" and favorite["target"]["type"] == "title"
    assert favorite["target"]["params"]["id"] == 11 and favorite["menu"][-1]["title"] == "Odebrat z oblíbených"
    assert api("discover_title", **FILM)["actions"][1]["title"] == "Odebrat z oblíbených"

    nothing = api("discover_title", kind="movie", id=5, title="Neznámý film", year=2001)
    assert nothing["rows"] == [] and [action["title"] for action in nothing["actions"]] == [
        "Přidat do oblíbených", "Hledat ručně"]
    assert "nenašel" in nothing["detail"]["plot"]
    assert asked[-2:] == [("Neznámý film 2001", 1), ("Neznámý film", 1)]     # zkusí i bez roku

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("HTTP 500")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)
    assert "nezdařilo" in api("discover_title", **FILM)["error"]


def test_series_page_seasons_episodes_and_continue(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    asked = _catalogue_search(monkeypatch, {"Archa S01E02": [
        _web(41, "Archa S01E02 CZ"), _web(42, "Archa S01E03 CZ")]})          # druhý výsledek je jiný díl
    _fake_tmdb(monkeypatch, {
        "/tv/77": {"id": 77, "name": "Archa", "first_air_date": "1996-01-01", "overview": "Seriál.",
                   "seasons": [{"season_number": 0, "name": "Speciály"},
                               {"season_number": 1, "name": "1. řada", "episode_count": 8, "poster_path": "/s1.jpg"}]},
        "/tv/77/season/1": {"episodes": [{"episode_number": 2, "name": "Druhý", "overview": "Děj", "still_path": "/e2.jpg"},
                                          {"episode_number": 1, "name": "Pilot"}]},
    })
    show = {"kind": "tv", "id": 77, "title": "Archa", "year": 1996, "poster": "", "orig": ""}
    page = api("discover_title", **show)
    assert page["detail"]["meta"] == "1996 · seriál" and page["detail"]["plot"] == "Seriál."
    assert [action["title"] for action in page["actions"]] == ["Přidat do oblíbených", "Hledat ručně"]
    seasons = page["rows"][0]
    assert seasons["title"] == "Řady" and _titles(seasons["items"]) == ["1. řada"]
    assert seasons["items"][0]["subtitle"] == "8 dílů" and seasons["items"][0]["target"]["call"] == "discover_season"
    episodes = api("discover_season", **seasons["items"][0]["target"]["params"])["items"]
    assert _titles(episodes) == ["1×01 · Pilot", "1×02 · Druhý"] and episodes[1]["wide"] is True
    versions = api("discover_episode", **episodes[1]["target"]["params"])["items"]
    assert _titles(versions) == ["CZ dabing"] and asked[-1] == ("Archa S01E02", 1)
    assert versions[0]["target"]["params"]["choice"] == "tv:77:1:2"
    missing = api("discover_episode", **episodes[0]["target"]["params"])["items"]
    assert missing[0]["target"] == {"type": "search", "call": "search", "params": {"q": "Archa S01E01"}}

    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    stream = api("play", **versions[0]["target"]["params"])                  # rozkoukaný díl je vidět ve výpisu řady
    api("progress_save", track=stream["track"], position=300, total=1200)
    marked = api("discover_season", **seasons["items"][0]["target"]["params"])["items"]
    assert (marked[1]["progress"], marked[0]["progress"]) == (25, 0)
    resume = api("discover_title", **show)["actions"][0]                     # stránka seriálu nabídne pokračování
    assert (resume["title"], resume["subtitle"]) == ("Pokračovat", "1. řada")
    assert resume["target"]["params"]["season"] == 1


def test_availability_check_marks_discovered_films(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)]})
    listing = api("discover", kind="movie", category="popular")["items"]
    check = listing[0]
    assert [card.get("badges") for card in listing[1:]] == [None, None]
    result = api(check["target"]["call"], **check["target"]["params"])
    assert result == {"message": "Ověřeno 2 filmů, na Přehraj.to je 1 z nich."}
    assert api("progress") == {"text": "", "busy": False}
    marked = api("discover", kind="movie", category="popular")["items"]      # dlaždice pro ověření už není potřeba
    assert _titles(marked) == ["Pelíšky", "Kolja"]
    assert [card["badges"] for card in marked] == [["Přehraj.to"], ["Nenalezeno"]]
    count = len(asked)
    api(check["target"]["call"], **check["target"]["params"])
    assert len(asked) == count                                              # už ověřené se neptá znovu


def test_search_offers_titles_and_remembers_queries(api, monkeypatch):
    assert api("search_history") == {"rows": []}
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/search/multi": {"results": [
        {"id": 11, "media_type": "movie", "title": "Pelíšky", "release_date": "1999-04-08", "poster_path": "/p.jpg"},
        {"id": 77, "media_type": "tv", "name": "Archa", "first_air_date": "1996-01-01"},
        {"id": 5, "media_type": "person", "name": "Někdo"}], "total_pages": 1}})
    monkeypatch.setattr(app_api, "prehrajto_search", lambda params: {"items": [], "next": None})
    rows = {row["title"]: row["items"] for row in api("search", q="pelisky")["rows"]}
    titles = rows["Filmy a seriály"]
    assert _titles(titles) == ["Pelíšky", "Archa"]
    assert titles[0]["target"]["type"] == "title" and titles[1]["target"]["params"]["kind"] == "tv"
    api("search", q="archa")
    api("search", q="pelisky")                                              # opakovaný dotaz se jen posune dopředu
    history = api("search_history")["rows"][0]
    assert history["title"] == "Naposledy hledané" and _titles(history["items"]) == ["pelisky", "archa"]
    assert history["items"][0]["target"] == {"type": "search", "call": "search", "params": {"q": "pelisky"}}


def test_stream_variant_follows_preferred_quality():
    streams = [{"quality": token} for token in ("ORIG", "UHD", "FHD", "HD", "SD")]
    pick = lambda quality, items=streams: app_api._pick_stream(items, {"language": "dub", "quality": quality})["quality"]
    assert [pick(q) for q in ("best", "1080", "720", "small")] == ["ORIG", "FHD", "HD", "SD"]
    assert pick("1080", [{"quality": "UHD"}, {"quality": "HD"}]) == "HD"     # nejbližší nižší má přednost
    assert pick("720", [{"quality": "ORIG"}, {"quality": "UHD"}]) == "UHD"   # originál s neznámým rozlišením až nakonec
    assert app_api._size_bytes("4,2 GB") == int(4.2 * 1024 ** 3) and app_api._size_bytes("700 MB") == 700 * 1024 ** 2
    assert app_api._size_bytes("") == 0


def test_protected_ivysilani_programme_uses_device_widevine(api, monkeypatch):
    calls = []

    class Client:
        def __init__(self, plain, protected=None):
            self.plain, self.protected = plain, protected

        def vod(self, idec, drm=False):
            calls.append(drm)
            answer = self.protected if drm else self.plain
            if isinstance(answer, Exception):
                raise answer
            return answer

    stream = {"url": "https://cdn/film/manifest.mpd?t=1", "subtitles": [], "duration": 6000, "title": "Film",
              "show_title": "", "episode_title": "", "show_id": "9", "image": "", "dash": True,
              "drm": {"license": "https://licence.example/wv"}, "shape": "{streams[{drm, url}]}"}
    protected = ivysilani.NotPlayable("Tento pořad nelze v doplňku přehrát (chráněný obsah).")
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(protected, stream))
    result = api("ivys_play", idec="123")
    assert calls == [False, True]                                        # nejdřív nechráněný stream
    assert (result["hls"], result["dash"], result["drm"]) == (False, True, {"license": "https://licence.example/wv"})
    assert result["url"].startswith("https://cdn/film/manifest.mpd") and result["track"] == "ivys:123"

    del calls[:]
    abroad = ivysilani.NotPlayable("ČT bohužel nemá práva pro poskytování tohoto obsahu na vašem území.")
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(abroad, stream))
    assert "území" in api("ivys_play", idec="123")["error"] and calls == [False]   # územní omezení se neobchází

    del calls[:]
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(protected, ivysilani.NotPlayable("Nelze.")))
    assert api("ivys_play", idec="123")["error"] == "Nelze."
    hls = dict(stream, url="https://cdn/film/master.m3u8", drm=None)
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(protected, hls))
    assert "chráněný obsah" in api("ivys_play", idec="123")["error"]     # bez licence a bez DASH nejde nic
