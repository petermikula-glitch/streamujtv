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
    assert [item["target"]["type"] for item in tools] == ["search", "list", "command", "command", "settings",
                                                          "choices", "list", "diagnostics"]
    assert tools[5]["title"] == "Jazyk a kvalita přehrávání" and tools[5]["subtitle"] == "Český dabing · Nejvyšší dostupná"
    assert tools[6]["title"] == "Profil: Hlavní profil" and tools[6]["image"] == "res:avatar_clapper"
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
    assert _titles(listing["items"]) == ["Filtr", "Pelíšky"] and listing["next"] is None
    episodes = api("show", key="show:archa")
    assert episodes["title"] == "Archa" and _titles(episodes["items"]) == ["1×01", "1×02", "1×03", "Stáhnout 1. řadu"]
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
    assert _titles(api("shows")["items"]) == ["Filtr", "Archa"]

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
    assert len(first["items"]) == app_api.PAGE + 1 and first["items"][0]["title"] == "Filtr"   # filtr jen na první stránce
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
    assert set(app_api.ACTIONS) >= {"home", "play", "sync", "ivys_play", "progress_save", "play_next"}


def test_connection_failures_are_marked_for_the_offline_illustration(api, monkeypatch):
    import urllib.error

    def unreachable(params):
        raise urllib.error.URLError("Network is unreachable")

    def translated(params):
        try:
            unreachable(params)
        except OSError:
            raise app_api.ApiError("Server neodpovídá.")

    def refused(params):
        raise urllib.error.HTTPError("https://x", 404, "Not Found", None, None)

    monkeypatch.setitem(app_api.ACTIONS, "a", unreachable)
    monkeypatch.setitem(app_api.ACTIONS, "b", translated)
    monkeypatch.setitem(app_api.ACTIONS, "c", refused)
    plain = api.raw("a")                                   # uživatel dostane srozumitelnou hlášku, detail jde do logu
    assert plain["offline"] is True and plain["error"] == app_api.OFFLINE_TEXT and "unreachable" in plain["detail"]
    assert api.raw("b") == {"ok": False, "error": app_api.OFFLINE_TEXT, "offline": True, "detail": "Server neodpovídá."}
    assert "offline" not in api.raw("c") and "offline" not in api.raw("neexistuje")   # odpověď serveru není výpadek


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

    rows = _section(api, "prehrajto")                               # filmy a seriály mají každé svou řadu
    assert "Oblíbené" not in rows
    assert _titles(rows["Oblíbené filmy"]) == ["Pelíšky"] and _titles(rows["Oblíbené seriály"]) == ["Archa"]
    favorites = rows["Oblíbené seriály"] + rows["Oblíbené filmy"]
    assert favorites[1]["target"] == {"type": "play", "call": "play", "params": {"vid": REF}}
    assert favorites[0]["target"]["call"] == "show"
    assert _titles(api("favorites", kind="tv")["items"]) == ["Archa"] and api("favorites", kind="tv")["title"] == "Oblíbené seriály"
    assert _titles(api("favorites", kind="movie")["items"]) == ["Pelíšky"]
    assert _titles(api("favorites")["items"]) == ["Archa", "Pelíšky"]
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
    filtering, check, card = listing["items"]                           # položka bez čísla TMDB se přeskočí
    assert filtering["title"] == "Filtr" and filtering["target"] == {
        "type": "filter", "call": "discover_filters", "params": {"kind": "movie", "category": "popular"}}
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
    assert _titles(czech["items"]) == ["Filtr", "Archa"] and czech["items"][1]["subtitle"] == "1996 · seriál"
    assert czech["items"][0]["subtitle"] == "1990–1999 · ČR a SR"            # nastavené filtry jsou vidět na kartě
    assert czech["next"] is None
    path, sent = calls[-1]
    assert path == "/discover/tv" and sent["with_origin_country"] == "CZ|SK|XC"
    assert (sent["first_air_date.gte"], sent["first_air_date.lte"]) == ("1990-01-01", "1999-12-31")

    years = api("discover_years", kind="movie")["items"]
    assert years[0]["title"] == str(app_api.time.localtime().tm_year)
    decade = next(item for item in years if item["title"] == "1990–1999")
    assert decade["target"]["params"] == {"kind": "movie", "year": "1990s"}
    genres = api("discover_genres", kind="movie", origin="CZ|SK|XC")["items"]
    assert _titles(genres) == ["Komedie", "Drama"] and genres[0]["icon"] == "genre_35:movies"   # ilustrace žánru
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
    play, favourite, manual, prefs = page["actions"]
    assert prefs["title"] == "Jazyk a kvalita" and prefs["target"] == {"type": "choices", "call": "prefs_choices", "params": {}}
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
    favorite = _section(api, "prehrajto")["Oblíbené filmy"][0]
    assert favorite["title"] == "Pelíšky" and favorite["target"]["type"] == "title"
    assert favorite["target"]["params"]["id"] == 11 and favorite["menu"][-1]["title"] == "Odebrat z oblíbených"
    assert api("discover_title", **FILM)["actions"][1]["title"] == "Odebrat z oblíbených"

    nothing = api("discover_title", kind="movie", id=5, title="Neznámý film", year=2001)
    assert nothing["rows"] == [] and [action["title"] for action in nothing["actions"]] == [
        "Přidat do oblíbených", "Hledat ručně", "Jazyk a kvalita"]
    assert "nenašel" in nothing["detail"]["plot"]
    assert asked[-2:] == [("Neznámý film 2001", 1), ("Neznámý film", 1)]     # zkusí i bez roku

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("HTTP 500")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)
    failed = api("discover_title", **FILM)                                   # hledání selhalo: stránka není slepá ulička
    assert failed["detail"]["title"] == "Pelíšky" and failed["detail"]["plot"].startswith("Hledání na Přehraj.to se nezdařilo")
    assert "Rodinná komedie." in failed["detail"]["plot"] and failed["rows"] == []
    assert [(action["title"], action["target"]["type"]) for action in failed["actions"]] == [
        ("Zkusit znovu", "refresh"), ("Odebrat z oblíbených", "command"), ("Hledat ručně", "search"),
        ("Jazyk a kvalita", "choices")]


def test_title_page_offers_the_series_similar_titles_cast_and_csfd(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _catalogue_search(monkeypatch, {})
    detail = dict(FILM_DETAIL, belongs_to_collection={"id": 900}, credits={"cast": [
        {"id": 5, "name": "Miroslav Donutil", "character": "Šebek", "profile_path": "/d.jpg"},
        {"id": 6, "name": "Jiří Kodet", "character": "Kraus"}, {"name": "bez čísla"}]})
    calls = _fake_tmdb(monkeypatch, {
        "/movie/11": detail,
        "/collection/900": {"parts": [{"id": 12, "title": "Pelíšky 2", "release_date": "2010-05-05"},
                                      {"id": 11, "title": "Pelíšky", "release_date": "1999-04-08", "poster_path": "/p.jpg"}]},
        "/movie/11/recommendations": {"results": [{"id": 21, "title": "Pupendo", "release_date": "2003-03-27",
                                                   "vote_average": 7.9, "poster_path": "/pu.jpg"}]},
        "/discover/movie": {"results": [{"id": 31, "title": "Dědictví", "release_date": "1992-01-01"}], "total_pages": 1},
    })
    rows = {row["title"]: row["items"] for row in api("discover_title", **FILM)["rows"]}
    assert list(rows) == ["Celá série", "Podobné tituly", "Hrají"]
    assert _titles(rows["Celá série"]) == ["Pelíšky", "Pelíšky 2"]             # v pořadí uvedení
    assert rows["Celá série"][0]["subtitle"].startswith("Právě otevřený")
    assert rows["Celá série"][1]["target"]["params"]["id"] == 12 and rows["Celá série"][1]["target"]["type"] == "title"
    assert rows["Podobné tituly"][0]["title"] == "Pupendo" and rows["Podobné tituly"][0]["subtitle"] == "2003 · 79 % · film"
    actor, second = rows["Hrají"]
    assert (actor["title"], actor["subtitle"]) == ("Miroslav Donutil", "Šebek")
    assert actor["image"] == "https://image.tmdb.org/t/p/w185/d.jpg" and second["image"] == "" and second["wide"]
    assert actor["target"] == {"type": "list", "call": "discover", "params": {"kind": "movie", "cast": "5"},
                               "title": "Filmy – Miroslav Donutil", "poster": True}
    films = api("discover", **actor["target"]["params"])                     # filmy s hercem
    assert "Dědictví" in _titles(films["items"]) and calls[-1][1]["with_cast"] == "5"
    alone = api("discover_title", kind="movie", id=5, title="Neznámý film", year=2001)
    assert alone["rows"] == []                                               # TMDB nic nedodal: žádné prázdné řady

    asked = []

    def find(title, year=0, want_series=False, log=None):
        asked.append((title, year, want_series))
        if title == "Pelisky":
            return {"rating": 91}
        if title == "Nedostupný":
            raise app_api.app_discover.csfd.CsfdBlocked("ČSFD vyžaduje ověření")
        return None

    monkeypatch.setattr(app_api.app_discover.csfd, "find", find)
    assert api("title_csfd", **FILM) == {"text": "ČSFD 91 %"}                # nejdřív český název, pak původní
    assert asked == [("Pelíšky", 1999, False), ("Pelisky", 1999, False)]
    assert api("title_csfd", **FILM) == {"text": "ČSFD 91 %"} and len(asked) == 2    # podruhé z mezipaměti
    assert api("title_csfd", kind="movie", title="Nedostupný", year=2000) == {"text": ""}
    assert api("title_csfd", kind="movie", title="", year=2000) == {"text": ""}


def test_favourite_that_appears_on_prehrajto_is_announced(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = {}
    asked = _catalogue_search(monkeypatch, found)
    page = api("discover_title", **FILM)                                     # film na Přehraj.to zatím není
    favourite = next(action for action in page["actions"] if action["title"] == "Přidat do oblíbených")
    api(favourite["target"]["call"], **favourite["target"]["params"])
    api("discover_title", **FILM)                                            # stránka si u oblíbeného zapamatuje „není“
    before = len(asked)
    assert api("favorites_check") == {"found": []} and len(asked) > before   # pořád nic
    before = len(asked)
    assert api("favorites_check") == {"found": []} and len(asked) == before  # podruhé se hned neptá
    found["Pelíšky 1999"] = [_web(61, "Pelisky 1999 CZ 1080p", 7000, "4 GB")]
    assert api("favorites_check", force=True) == {"found": ["Pelíšky"]}
    card = _section(api, "prehrajto")["Oblíbené filmy"][0]
    assert card["badges"] == ["Nově na Přehraj.to"]
    assert api("favorites_check", force=True) == {"found": []}               # známý titul se znovu neohlašuje
    api("discover_title", **FILM)                                            # otevřením štítek zmizí
    assert "badges" not in _section(api, "prehrajto")["Oblíbené filmy"][0]


def test_web_results_of_one_title_are_grouped(api, monkeypatch):
    hour = 3600
    found = {"Pelíšky": [_web(71, "Pelisky 1999 EN 720p", 2 * hour, "1 GB"), _web(72, "Jiny film 2001 CZ", 2 * hour),
                         _web(73, "Pelisky.1999.CZ.1080p", 2 * hour, "4 GB"), _web(74, "Pelisky 1999 CZ tit", 2 * hour)]}
    _catalogue_search(monkeypatch, found)
    rows = {row["title"]: row["items"] for row in api("search", q="Pelíšky")["rows"]}
    grouped, other = rows["Videa na Přehraj.to"]
    assert grouped["target"]["params"]["vid"] == _web(73, "")["id"]          # nejlepší verze podle předvoleb
    assert grouped["subtitle"].startswith("3 verze") and other["title"].startswith("Jiny film")
    versions = grouped["menu"][-1]
    assert versions["title"] == "Všechny verze (3)" and versions["target"]["call"] == "prehrajto_search"
    listed = api("prehrajto_search", **versions["target"]["params"])
    assert len(listed["items"]) == 3 and listed["next"] is None
    assert len(api("prehrajto_search", q="Pelíšky")["items"]) == 4            # samotný výpis zůstává po souborech


def test_diagnostics_reports_state_and_recent_errors(api, monkeypatch):
    first = {line["label"]: line for line in api("diagnostics")["lines"]}
    assert first["Přehraj.to"]["ok"] is False and "nepřihlášeno" in first["Přehraj.to"]["value"]
    assert first["TMDB"]["ok"] is False and first["iVysílání"]["ok"] is True
    assert first["Katalog"] == {"label": "Katalog", "value": "0 videí, 0 filmů, 0 seriálů", "ok": None}
    assert first["Předvolby"]["value"] == "Český dabing, Nejvyšší dostupná"

    api("settings_set", username="a@b.cz", password="heslo", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/genre/movie/list": {"genres": [{"id": 35, "name": "Komedie"}]}})
    monkeypatch.setattr(app_api, "account_status", lambda params: {"message": "Přihlášení v pořádku."})
    api.raw("neexistuje")                                                    # chyby se pamatují, nejnovější první
    api("log_error", where="přehrávač", text="Zařízení formát nepřehraje")
    report = api("diagnostics")
    lines = {line["label"]: line for line in report["lines"]}
    assert lines["Přehraj.to"] == {"label": "Přehraj.to", "value": "a@b.cz – Přihlášení v pořádku.", "ok": True}
    assert lines["TMDB"] == {"label": "TMDB", "value": "klíč platí, 1 filmových žánrů", "ok": True}
    assert "přehrávač: Zařízení formát nepřehraje" in report["errors"][0]
    assert "neexistuje: Neznámá akce: neexistuje" in report["errors"][1]
    for _ in range(40):
        api.raw("neexistuje")
    assert len(api("diagnostics")["errors"]) == app_api.ERRORS_KEEP


def test_series_page_lists_episodes_that_play_at_once(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    asked = _catalogue_search(monkeypatch, {
        "Archa S01E01": [_web(40, "Archa S01E01 trailer")],                  # jen ukázka: díl na Přehraj.to není
        "Archa S01E02": [_web(41, "Archa S01E02 EN"), _web(42, "Archa S01E02 CZ"),
                         _web(43, "Archa S01E03 CZ")]})                      # poslední výsledek je jiný díl
    _fake_tmdb(monkeypatch, {
        "/tv/77": {"id": 77, "name": "Archa", "first_air_date": "1996-01-01", "overview": "Seriál.",
                   "seasons": [{"season_number": 0, "name": "Speciály"},
                               {"season_number": 1, "name": "1. řada", "episode_count": 3, "poster_path": "/s1.jpg"}]},
        "/tv/77/season/1": {"episodes": [{"episode_number": 2, "name": "Druhý", "overview": "Děj", "still_path": "/e2.jpg"},
                                          {"episode_number": 1, "name": "Pilot"}, {"episode_number": 3, "name": "Třetí"}]},
    })
    show = {"kind": "tv", "id": 77, "title": "Archa", "year": 1996, "poster": "", "orig": ""}
    page = api("discover_title", **show)
    assert page["detail"]["meta"] == "1996 · seriál" and page["detail"]["plot"] == "Seriál."
    assert [(action["title"], action["subtitle"]) for action in page["actions"]] == [
        ("Přehrát", "1×01 · Pilot"), ("Přidat do oblíbených", ""), ("Hledat ručně", ""), ("Jazyk a kvalita", "")]
    row = page["rows"][0]                                                    # díly jsou přímo na stránce seriálu
    assert row["title"] == "1. řada (3 dílů)" and _titles(row["items"]) == ["1×01 · Pilot", "1×02 · Druhý", "1×03 · Třetí"]
    second = row["items"][1]
    assert second["wide"] is True and second["image"].endswith("/e2.jpg") and second["subtitle"] == "Děj"
    assert second["target"] == {"type": "play", "call": "discover_episode_play", "params": dict(show, season=1, episode=2)}
    assert _titles(second["menu"]) == ["Přehrát", "Vybrat verzi"] and second["menu"][1]["target"]["call"] == "discover_episode"
    assert page["actions"][0]["target"] == row["items"][0]["target"]

    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    stream = api("discover_episode_play", **second["target"]["params"])      # výběr dílu rovnou hraje nejlepší verzi
    assert stream["track"] == _web(42, "")["id"] and stream["url"] == "https://cdn.x/v.mp4"   # česká podle předvoleb
    assert stream["choice"] == "tv:77:1:2" and stream["tracks"]["key"] == "tv:77" and asked[-1] == ("Archa S01E02", 1)
    missing = api("discover_episode_play", **row["items"][0]["target"]["params"])
    assert "nenašel" in missing["error"] and "Vybrat verzi" in missing["error"]
    versions = api("discover_episode", **second["menu"][1]["target"]["params"])["items"]   # ruční výběr zůstává
    assert _titles(versions) == ["CZ dabing", "EN"] and "naposledy zvolená" in versions[0]["subtitle"]
    assert versions[1]["target"]["params"]["choice"] == "tv:77:1:2"
    listed = api("discover_season", **dict(show, season=1))["items"]         # stejné karty i ve výpisu řady
    assert _titles(listed) == ["1×01 · Pilot", "1×02 · Druhý", "1×03 · Třetí", "Stáhnout celou řadu"]
    assert listed[1]["target"] == second["target"]

    api("progress_save", track=stream["track"], position=300, total=1200)    # rozkoukaný díl: tlačítko naváže
    page = api("discover_title", **show)
    resume = page["actions"][0]
    assert (resume["title"], resume["subtitle"]) == ("Pokračovat", "1×02 · Druhý")
    assert resume["target"]["params"] == dict(show, season=1, episode=2, resume=True)
    assert page["rows"][0]["items"][1]["progress"] == 25
    assert _titles(page["rows"][0]["items"][1]["menu"]) == ["Přehrát", "Přehrát od začátku", "Vybrat verzi"]
    api("progress_save", track=stream["track"], position=1190, total=1200, ended=True)   # dokoukáno: nabídne další
    following = api("discover_title", **show)["actions"][0]
    assert (following["title"], following["subtitle"]) == ("Přehrát další díl", "1×03 · Třetí")
    assert api("discover_title", **show)["rows"][0]["items"][1]["watched"] is True

    api("play_failed", track=stream["track"])                                # verze, která nejde přehrát, se příště přeskočí
    assert api("discover_episode_play", **second["target"]["params"])["track"] == _web(41, "")["id"]


def test_next_episode_from_the_catalogue_follows_a_finished_one(api):
    _fill(3)
    first, second, third = ["%d:archa:%016x" % (10 + n, 10 + n) for n in (1, 2, 3)]
    assert "Další díly" not in _section(api, "prehrajto")
    following = api("play_next", track=first)["next"]
    assert following == {"title": "Další díl", "subtitle": "Archa 1×02", "auto": True,
                         "target": {"type": "play", "call": "play", "params": {"vid": second}}}
    assert api("play_next", track=third)["next"] is None                    # poslední díl: není co nabídnout
    assert api("play_next", track="")["next"] is None and api("play_next", track=REF)["next"] is None

    api("progress_save", track=first, position=1190, total=1200, ended=True)
    row = _section(api, "prehrajto")["Další díly"]                          # po dokoukání čeká další díl na hlavní obrazovce
    assert len(row) == 1 and row[0]["target"]["params"] == {"vid": second}
    assert row[0]["subtitle"].startswith("Další díl")
    api("progress_save", track=second, position=300, total=1200, started=True)
    assert "Další díly" not in _section(api, "prehrajto")                   # rozkoukaný díl je už v Rozkoukaných
    assert _section(api, "prehrajto")["Rozkoukané"][0]["target"]["params"] == {"vid": second}


def test_next_episode_of_a_discovered_series_is_searched(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    asked = _catalogue_search(monkeypatch, {
        "Archa S01E01": [_web(40, "Archa S01E01 CZ")],
        "Archa S01E02": [_web(41, "Archa S01E02 EN"), _web(42, "Archa S01E02 CZ"), _web(43, "Archa S01E03 CZ")],
        "Archa S02E01": [_web(44, "Archa S02E01 CZ")]})
    _fake_tmdb(monkeypatch, {
        "/tv/77": {"id": 77, "name": "Archa", "seasons": [{"season_number": 1, "name": "1. řada"},
                                                          {"season_number": 2, "name": "2. řada"}]},
        "/tv/77/season/1": {"episodes": [{"episode_number": 1, "name": "Pilot"},
                                          {"episode_number": 2, "name": "Druhý"}]},
        "/tv/77/season/2": {"episodes": [{"episode_number": 1, "name": "Návrat"}]},
    })
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    episode = {"kind": "tv", "id": 77, "title": "Archa", "year": 1996, "poster": "https://img/a.jpg", "orig": "",
               "season": 1, "episode": 1}
    version = api("discover_episode", **episode)["items"][0]
    stream = api("play", **version["target"]["params"])
    following = api("play_next", track=stream["track"], choice="tv:77:1:1")["next"]
    assert (following["title"], following["subtitle"], following["auto"]) == ("Další díl", "Archa 1×02 · Druhý", True)
    assert following["target"]["params"]["choice"] == "tv:77:1:2"
    assert following["target"]["params"]["vid"] == _web(42, "")["id"]         # česká verze podle předvoleb
    assert asked[-1] == ("Archa S01E02", 1)
    api("play", **following["target"]["params"])
    crossing = api("play_next", track=following["target"]["params"]["vid"], choice="tv:77:1:2")["next"]
    assert crossing["subtitle"] == "Archa 2×01 · Návrat"                     # po posledním dílu řady první díl další
    assert crossing["target"]["params"]["choice"] == "tv:77:2:1"
    assert api("play_next", track="x", choice="tv:77:2:1")["next"] is None   # konec seriálu
    assert api("play_next", track="x", choice="tv:99:1:1")["next"] is None   # neznámý seriál

    row = _section(api, "prehrajto")["Další díly"]                           # rozkoukaný seriál z Objevuj
    assert _titles(row) == ["Archa"] and row[0]["subtitle"] == "Pokračovat ve sledování"
    assert row[0]["target"]["type"] == "title" and row[0]["target"]["params"]["id"] == 77
    resume = api("discover_title", kind="tv", id=77, title="Archa")["actions"][0]
    assert (resume["title"], resume["subtitle"]) == ("Přehrát", "1×01 · Pilot")   # nic rozkoukaného ani zhlédnutého
    api("progress_save", track=following["target"]["params"]["vid"], position=400, total=1200)
    resume = api("discover_title", kind="tv", id=77, title="Archa")["actions"][0]
    assert (resume["title"], resume["subtitle"]) == ("Pokračovat", "1×02 · Druhý")   # navazuje na rozkoukaný díl


def test_next_film_of_a_series_follows_a_finished_one(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    hour = 3600
    asked = _catalogue_search(monkeypatch, {
        "Pelíšky 2 2010": [_web(51, "Pelisky 2 2010 CZ 1080p", 2 * hour, "4 GB"),
                           _web(52, "Pelisky 2 2010 EN", 2 * hour, "2 GB")]})
    saga = {"parts": [{"id": 13, "title": "Pelíšky 3", "release_date": "2020-01-01", "poster_path": "/p3.jpg"},
                      {"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
                      {"id": 12, "title": "Pelíšky 2", "original_title": "Pelisky 2", "release_date": "2010-05-05"}]}
    _fake_tmdb(monkeypatch, {
        "/movie/11": dict(FILM_DETAIL, belongs_to_collection={"id": 900}),
        "/movie/12": {"id": 12, "title": "Pelíšky 2", "belongs_to_collection": {"id": 900}},
        "/movie/13": {"id": 13, "title": "Pelíšky 3", "belongs_to_collection": {"id": 900}},
        "/movie/14": {"id": 14, "title": "Samotář"},
        "/collection/900": saga,
    })
    following = api("play_next", track="x", choice="movie:11")["next"]       # film spuštěný ze stránky titulu
    assert (following["title"], following["subtitle"], following["auto"]) == (
        "Další film série", "Pelíšky 2 (2010)", False)
    assert following["target"]["type"] == "play" and following["target"]["params"] == {
        "vid": _web(51, "")["id"], "web_result": _web(51, "Pelisky 2 2010 CZ 1080p", 2 * hour, "4 GB"),
        "choice": "movie:12"}
    assert asked == [("Pelíšky 2 2010", 1)]
    missing = api("play_next", track="x", choice="movie:12")["next"]         # další díl na Přehraj.to není: stránka titulu
    assert missing["subtitle"] == "Pelíšky 3 (2020)" and missing["target"]["type"] == "title"
    assert missing["target"]["params"] == {"kind": "movie", "id": 13, "title": "Pelíšky 3", "year": 2020,
                                           "poster": "https://image.tmdb.org/t/p/w342/p3.jpg", "orig": ""}
    assert api("play_next", track="x", choice="movie:13")["next"] is None    # poslední film série
    assert api("play_next", track="x", choice="movie:14")["next"] is None    # film bez pokračování
    assert api("play_next", track="x", choice="movie:15")["next"] is None    # TMDB neodpověděl: žádný návrh, žádná chyba

    _fill(0)                                                                 # film z vlastního katalogu s číslem TMDB
    with app_api._library() as lib:
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "year": 1999, "tmdb_id": 11})
        lib.upsert_video("7:pelisky-2:0000000000000077", "Pelisky.2.2010.CZ", "account",
                         nameparser.parse("Pelisky.2.2010.CZ"))
        key = lib.find_video("7:pelisky-2:0000000000000077")["meta_key"]
        lib.update_meta(key, {"status": "ok", "title": "Pelíšky 2", "year": 2010, "tmdb_id": 12})
    own = api("play_next", track=REF)["next"]                                # pokračování mám v katalogu: hraje odtud
    assert own["subtitle"] == "Pelíšky 2 (2010)" and own["target"]["params"] == {"vid": "7:pelisky-2:0000000000000077"}
    api("settings_set", tmdb_key="")
    assert api("play_next", track=REF)["next"] is None                       # bez klíče TMDB se série nepozná


def test_availability_check_marks_discovered_films(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)]})
    listing = api("discover", kind="movie", category="popular")["items"]
    check = listing[1]
    assert [card.get("badges") for card in listing[2:]] == [None, None]
    result = api(check["target"]["call"], **check["target"]["params"])
    assert result == {"message": "Ověřeno 2 filmů, na Přehraj.to je 1 z nich."}
    assert api("progress") == {"text": "", "busy": False}
    marked = api("discover", kind="movie", category="popular")["items"]      # dlaždice pro ověření už není potřeba
    assert _titles(marked) == ["Filtr", "Pelíšky", "Kolja"]
    assert [card["badges"] for card in marked[1:]] == [["Přehraj.to"], ["Nenalezeno"]]
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
    refused = ivysilani.NotPlayable("Nainstalujte si prohlížeč, který ochranu DRM podporuje.", "DRM_REQUIRED")
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(refused, refused))
    assert api("ivys_play", idec="123")["error"] == (                    # stav podle ČT je vidět na obrazovce
        "Nainstalujte si prohlížeč, který ochranu DRM podporuje.\n\n(iVysílání: DRM_REQUIRED / DRM_REQUIRED)")

    # Skutečná adresa streamu nekončí na .mpd a odpověď licenci nést nemusí: pořad se i tak předá
    # přehrávači jako DASH s Widevine; adresu licence si vezme z manifestu.
    bare = dict(stream, url="https://cdn/cdn/uri/get/?token=x&streamType=dash&drmOnly=true", drm=None,
                shape="{streams[{url}]}; adresa: token, streamType, drmOnly=true")
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(protected, bare))
    result = api("ivys_play", idec="123")
    assert (result["dash"], result["hls"], result["drm"]) == (True, False, {"license": ""})
    assert result["diagnostic"] == "{streams[{url}]}; adresa: token, streamType, drmOnly=true"


def test_profiles_keep_progress_favourites_and_preferences_apart(api, monkeypatch):
    _fill(3)
    first = "11:archa:%016x" % 11
    api("progress_save", track=REF, position=1500, total=6000, started=True)
    api("settings_set", prefer_language="subs")
    with app_api._library() as lib:
        meta = lib.get_meta("movie:pelisky|1999")
    card = api("movies")["items"][1]
    toggle = next(item for item in card["menu"] if item["title"] == "Přidat do oblíbených")
    api(toggle["target"]["call"], **toggle["target"]["params"])
    home = api("home")
    assert home["profile"] == {"name": "Hlavní profil", "avatar": "clapper", "count": 1}
    listed = api("profiles")
    assert listed["title"] == "Kdo se dívá?" and _titles(listed["items"]) == ["Hlavní profil", "Přidat profil"]
    assert listed["items"][0]["subtitle"] == "Právě se dívá" and listed["items"][0]["image"] == "res:avatar_clapper"
    avatars = api("profile_avatars", new=1)["items"]
    assert _titles(avatars) == ["Klapka", "Popcorn", "Koruna", "Sova", "Kočka", "Raketa"]
    assert avatars[0]["subtitle"] == "Už ho někdo má" and avatars[3]["target"]["params"] == {"avatar": "owl"}
    added = api(avatars[3]["target"]["call"], **avatars[3]["target"]["params"])
    assert "Sova" in added["message"] and added["close"] is True
    owl = api("profiles")["items"][1]
    assert (owl["title"], owl["subtitle"], owl["image"]) == ("Sova", "Přepnout", "res:avatar_owl")
    assert [item["title"] for item in owl["menu"]] == ["Přejmenovat", "Změnit obrázek", "Smazat profil"]
    rename = owl["menu"][0]["target"]
    assert rename["type"] == "input" and rename["params"]["value"] == "Sova"
    api(rename["call"], id=rename["params"]["id"], text="  Jana  ")
    assert "prázdné" in api(rename["call"], id=rename["params"]["id"], text=" ")["error"]

    switched = api(owl["target"]["call"], **owl["target"]["params"])        # druhý profil začíná načisto
    assert switched == {"message": "", "close": True, "toast": "Dívá se: Jana"}
    assert api("home")["profile"] == {"name": "Jana", "avatar": "owl", "count": 2}
    rows = _section(api, "prehrajto")
    assert "Rozkoukané" not in rows and rows["Oblíbené"][0]["icon"] == "favorites"   # jen nápověda, nic oblíbeného
    assert api("settings_get")["prefer_language"] == "dub"
    assert "smazat nejde" in api("profile_delete", id=owl["target"]["params"]["id"])["error"]
    api("progress_save", track=first, position=300, total=1200, started=True)
    api("settings_set", prefer_quality="720")
    assert _section(api, "prehrajto")["Rozkoukané"][0]["target"]["params"] == {"vid": first}

    main = api("profiles")["items"][0]
    api(main["target"]["call"], **main["target"]["params"])                 # zpět: původní stav je na místě
    rows = _section(api, "prehrajto")
    assert [item["target"]["params"] for item in rows["Rozkoukané"]] == [{"vid": REF}]
    assert _titles(rows["Oblíbené filmy"]) == ["Pelíšky"]
    settings = api("settings_get")
    assert (settings["prefer_language"], settings["prefer_quality"]) == ("subs", "best")
    assert meta["key"] and _titles(api("movies")["items"]) == ["Filtr", "Pelíšky"]   # katalog je společný

    api(main["target"]["call"], **owl["target"]["params"])                  # a druhý profil má své
    assert _section(api, "prehrajto")["Rozkoukané"][0]["target"]["params"] == {"vid": first}
    assert api("settings_get")["prefer_quality"] == "720"
    api(main["target"]["call"], **main["target"]["params"])
    assert "smazaný" in api("profile_delete", id=owl["target"]["params"]["id"])["message"]
    assert _titles(api("profiles")["items"]) == ["Hlavní profil", "Přidat profil"]
    assert "neexistuje" in api("profile_switch", id="p9")["error"]


def test_hero_and_watch_next_follow_the_last_unfinished_video(api):
    _fill(2)
    sections = {section["id"]: section for section in api("home")["sections"]}
    assert "hero" not in sections["prehrajto"] and "hero" not in sections["ivysilani"]
    assert api("watch_next") == {"items": []}
    with app_api._library() as lib:
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "year": 1999,
                                               "poster": "https://img/p.jpg", "fanart": "https://img/f.jpg"})
        lib.record_play(REF, 1500, 6000, now=1000)
        lib.record_play("11:archa:%016x" % 11, 300, 1200, now=2000)
        lib.ivys_touch("777", "9", "Díl 3", "Vyprávěj", "https://img/v.jpg", now=3000)
        lib.ivys_record_play("777", 600, 3000, now=3000)
    sections = {section["id"]: section for section in api("home")["sections"]}
    hero = sections["prehrajto"]["hero"]                                     # naposledy přehrané rozkoukané video
    assert (hero["title"], hero["label"], hero["progress"]) == ("Archa 1×01", "Pokračovat", 25)
    assert hero["subtitle"] == "CZ dabing · zbývá 15 min" and hero["image"] == ""
    assert hero["target"] == {"type": "play", "call": "play", "params": {"vid": "11:archa:%016x" % 11, "resume": True}}
    television = sections["ivysilani"]["hero"]
    assert (television["title"], television["subtitle"]) == ("Vyprávěj · Díl 3", "iVysílání · zbývá 40 min")
    assert television["image"] == "https://img/v.jpg" and "hero" not in sections["tools"]

    items = api("watch_next")["items"]                                       # pro domovskou obrazovku televize
    assert [(item["id"], item["title"], item["call"]) for item in items] == [
        ("ivys:777", "Vyprávěj · Díl 3", "ivys_play"), ("11:archa:%016x" % 11, "Archa 1×01", "play"),
        (REF, "Pelíšky", "play")]
    assert (items[2]["position"], items[2]["duration"], items[2]["image"], items[2]["episode"]) == (
        1500, 6000, "https://img/f.jpg", False)
    assert items[0]["params"] == {"idec": "777"} and items[1]["episode"] is True


def test_audio_and_subtitle_choice_is_remembered_per_series(api, monkeypatch):
    _fill(2)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    api("settings_set", username="a@b.cz", password="heslo")
    first, second = ["%d:archa:%016x" % (10 + n, 10 + n) for n in (1, 2)]
    tracks = api("play", vid=first)["tracks"]
    assert tracks == {"key": "show:archa", "audio": "", "subs": ""}
    assert api("track_prefs", key=tracks["key"], audio="en", subs="cs") == {"saved": True}
    assert api("play", vid=second)["tracks"] == {"key": "show:archa", "audio": "en", "subs": "cs"}   # další díl
    assert api("play", vid=REF)["tracks"]["audio"] == ""                     # jiný titul má vlastní volbu
    assert api("track_prefs", key="", audio="en") == {"saved": False}
    assert api("play", vid=second, choice="tv:77:1:2")["tracks"]["key"] == "tv:77"   # seriál z Objevuj


def test_whole_season_is_queued_and_watched_downloads_can_be_removed(api, tmp_path, monkeypatch):
    _fill(3)
    season = api("show", key="show:archa")["items"][-1]
    assert (season["title"], season["subtitle"]) == ("Stáhnout 1. řadu", "3 dílů na disk")
    assert season["target"] == {"type": "download", "call": "download_season", "params": {"key": "show:archa", "season": 1}}
    assert api("download_season", **season["target"]["params"]) == {
        "message": "Zařazeno ke stažení: 3 dílů (1. řada).", "start": True}
    assert len(api("downloads")["items"]) == 3
    assert "už jsou stažené" in api("download_season", **season["target"]["params"])["message"]
    assert "žádné díly" in api("download_season", key="show:archa", season=4)["error"]

    first = "11:archa:%016x" % 11
    disk = tmp_path / "usb"
    disk.mkdir()
    _link(first, "https://cdn.x/archa1.mkv?sig=1")
    job = api("download_next", dir=str(disk))
    assert job["vid"] == first
    (disk / os.path.basename(job["path"])).write_bytes(b"x" * 10)
    api("download_finish", vid=first, size=10, subtitles=[])
    assert api("settings_get")["delete_watched"] is False
    api("progress_save", track=first, position=1190, total=1200, ended=True)
    assert os.path.exists(job["path"])                                       # bez volby v Nastavení se nic nemaže
    assert api("settings_set", delete_watched=True)["delete_watched"] is True
    api("progress_save", track=first, position=600, total=1200)
    assert os.path.exists(job["path"])                                       # rozkoukaný soubor zůstává
    api("progress_save", track=first, position=1190, total=1200, ended=True)
    assert not os.path.exists(job["path"]) and len(api("downloads")["items"]) == 2

    api("settings_set", tmdb_key="k" * 32)                                   # řada seriálu z Objevuj
    _catalogue_search(monkeypatch, {"Krakonoš S01E01": [_web(81, "Krakonos S01E01 CZ")],
                                    "Krakonoš S01E02": [_web(82, "Krakonos S01E02 trailer")],
                                    "Krakonoš S01E03": [_web(83, "Krakonos S01E03 CZ"), _web(84, "Krakonos S01E03 EN")]})
    _fake_tmdb(monkeypatch, {"/tv/88/season/1": {"episodes": [{"episode_number": n, "name": "Díl"} for n in (1, 2, 3)]}})
    listing = api("discover_season", kind="tv", id=88, title="Krakonoš", season=1)["items"]
    whole = listing[-1]
    assert whole["title"] == "Stáhnout celou řadu" and whole["target"]["type"] == "command"
    result = api(whole["target"]["call"], **whole["target"]["params"])
    assert result == {"message": "Zařazeno ke stažení: 2 dílů. 1 dílů jsem na Přehraj.to nenašel.", "start": True}
    queued = [entry["vid"] for entry in app_api._downloads().values()]
    assert _web(81, "")["id"] in queued and _web(83, "")["id"] in queued and _web(84, "")["id"] not in queued


def test_first_run_offers_the_guide_and_poster_lists_announce_their_shape(api):
    assert api("home")["welcome"] is True
    api("settings_set", onboarded=True)
    assert api("home")["welcome"] is False
    tiles = _section(api, "prehrajto")
    assert tiles["Objevuj filmy"][0]["target"]["poster"] is True              # výpis plakátů
    assert "poster" not in tiles["Objevuj filmy"][3]["target"]               # Podle roku: dlaždice
    assert "poster" not in _section(api, "tools")["Nástroje"][1]["target"]


def test_discover_lists_can_be_filtered(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    film = {"id": 11, "title": "Pelíšky", "release_date": "1999-04-08", "vote_average": 8.6}
    calls = _fake_tmdb(monkeypatch, {
        "/movie/top_rated": {"results": [film], "total_pages": 1},
        "/discover/movie": {"results": [film], "total_pages": 1},
        "/genre/movie/list": {"genres": [{"id": 35, "name": "Komedie"}, {"id": 18, "name": "Drama"}]},
    })
    card = api("discover", kind="movie", category="top_rated")["items"][0]
    assert card["subtitle"] == "Žánr, období, hodnocení, řazení" and card["target"]["type"] == "filter"
    offered = api(card["target"]["call"], **card["target"]["params"])
    assert [(group["title"], group["value"]) for group in offered["filters"]] == [
        ("Žánr", "Vše"), ("Období", "Vše"), ("Hodnocení", "Vše"), ("Země", "Vše"), ("Řazení", "Nejlépe hodnocené")]
    assert offered["reset"] is None                                          # nic není nastaveno
    genre = next(option for option in offered["filters"][0]["options"] if option["title"] == "Komedie")
    assert genre["params"] == {"kind": "movie", "category": "top_rated", "genre": "35"} and genre["current"] is False

    api("discover", **genre["params"])                                       # žánr + výchozí řazení kategorie
    path, sent = calls[-1]
    assert path == "/discover/movie" and sent["with_genres"] == "35"
    assert (sent["sort_by"], sent["vote_count.gte"]) == ("vote_average.desc", 200)

    chosen = dict(genre["params"], year="1990s", rating="70", origin="CZ|SK|XC", sort="new")
    listing = api("discover", **chosen)
    assert listing["items"][0]["subtitle"] == "Komedie · 1990–1999 · 70 %+ · ČR a SR · nejnovější"
    path, sent = calls[-1]
    assert (sent["primary_release_date.gte"], sent["primary_release_date.lte"]) == ("1990-01-01", "1999-12-31")
    assert (sent["vote_average.gte"], sent["sort_by"]) == (7.0, "primary_release_date.desc")
    assert sent["with_origin_country"] == "CZ|SK|XC" and sent["vote_count.gte"] == 50
    again = api("discover_filters", **chosen)
    assert [group["value"] for group in again["filters"]] == [
        "Komedie", "1990–1999", "70 % a víc", "Česko a Slovensko", "Nejnovější"]
    assert again["reset"] == {"kind": "movie", "category": "top_rated"}      # zrušení vrátí původní výpis
    whole = next(option for option in again["filters"][0]["options"] if option["title"] == "Vše")
    assert "genre" not in whole["params"] and whole["params"]["year"] == "1990s"

    monkeypatch.setattr(app_api.Tmdb, "_fetch", lambda self, path, timeout, params: {"results": [], "total_pages": 0})
    empty = api("discover", kind="movie", genre="99", rating="80")           # prázdný výběr: filtr zůstává po ruce
    assert _titles(empty["items"]) == ["Filtr"] and empty["next"] is None


def test_catalogue_lists_can_be_filtered(api):
    _fill(2)
    with app_api._library() as lib:
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "year": 1999, "rating": 91,
                                               "genres": ["Komedie", "Drama"]})
        lib.upsert_video("5:kolja:0000000000000055", "Kolja.1996.CZ", "account", nameparser.parse("Kolja.1996.CZ"))
        key = lib.find_video("5:kolja:0000000000000055")["meta_key"]
        lib.update_meta(key, {"status": "ok", "title": "Kolja", "year": 1996, "rating": 86, "genres": ["Drama"]})
        lib.upsert_video("6:vratne:0000000000000066", "Vratne.lahve.2007.CZ", "account", nameparser.parse("Vratne.lahve.2007.CZ"))
        key = lib.find_video("6:vratne:0000000000000066")["meta_key"]
        lib.update_meta(key, {"status": "ok", "title": "Vratné lahve", "year": 2007, "rating": 75, "genres": ["Komedie"]})
    listing = api("movies", order="rating")
    assert _titles(listing["items"]) == ["Filtr", "Pelíšky", "Kolja", "Vratné lahve"]
    card = listing["items"][0]
    assert card["target"] == {"type": "filter", "call": "catalog_filters", "params": {"order": "rating", "list": "movies"}}
    offered = api("catalog_filters", **card["target"]["params"])
    assert [(group["title"], group["value"]) for group in offered["filters"]] == [
        ("Žánr", "Vše"), ("Období", "Vše"), ("Hodnocení", "Vše"), ("Řazení", "Podle hodnocení")]
    assert [option["title"] for option in offered["filters"][0]["options"]] == ["Vše", "Drama (2)", "Komedie (2)"]
    assert [option["title"] for option in offered["filters"][1]["options"]] == ["Vše", "2000–2009 (1)", "1990–1999 (2)"]
    comedy = offered["filters"][0]["options"][2]["params"]
    assert comedy == {"order": "rating", "genre": "Komedie"}
    assert _titles(api("movies", **comedy)["items"]) == ["Filtr", "Pelíšky", "Vratné lahve"]
    narrowed = api("movies", genre="Komedie", decade="1990", rating="80", order="title")
    assert _titles(narrowed["items"]) == ["Filtr", "Pelíšky"] and narrowed["items"][0]["subtitle"] == "Komedie · 1990–1999 · 80 %+"
    assert api("catalog_filters", list="movies", genre="Komedie", decade="1990")["reset"] == {"order": "title"}
    assert _titles(api("movies", rating="95")["items"]) == ["Filtr"]         # nic nevyhovuje: filtr zůstává

    with app_api._library() as lib:
        lib.update_meta("show:archa", {"status": "ok", "title": "Archa", "rating": 70, "genres": ["Rodinný"]})
    series = api("catalog_filters", list="shows")
    assert [group["title"] for group in series["filters"]] == ["Žánr", "Hodnocení", "Řazení"]   # seriály bez období
    assert _titles(api("shows", genre="Rodinný")["items"]) == ["Filtr", "Archa"]
    assert _titles(api("shows", genre="Krimi")["items"]) == ["Filtr"]


def test_search_finds_people_and_lists_their_films(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _catalogue_search(monkeypatch, {})
    _fake_tmdb(monkeypatch, {
        "/search/multi": {"results": [], "total_pages": 0},
        "/search/person": {"results": [
            {"id": 5, "name": "Jan Svěrák", "known_for_department": "Directing", "profile_path": "/s.jpg"},
            {"id": 6, "name": "Zdeněk Svěrák", "known_for_department": "Acting"}, {"name": "bez čísla"}]},
        "/person/5/combined_credits": {
            "cast": [{"media_type": "movie", "id": 21, "title": "Kuky se vrací", "release_date": "2010-05-20",
                      "character": "vypravěč"},
                     {"media_type": "movie", "id": 20, "title": "Kolja", "release_date": "1996-05-15", "character": "host"}],
            "crew": [{"media_type": "movie", "id": 20, "title": "Kolja", "release_date": "1996-05-15", "job": "Director",
                      "vote_average": 7.7},
                     {"media_type": "tv", "id": 30, "name": "Seriál", "first_air_date": "2001-01-01", "job": "Director"},
                     {"media_type": "movie", "id": 22, "title": "Jiný film", "job": "Producer"}]},
    })
    rows = {row["title"]: row["items"] for row in api("search", q="Svěrák")["rows"]}
    director, actor = rows["Herci a režiséři"]
    assert (director["title"], director["subtitle"], director["image"]) == (
        "Jan Svěrák", "režie", "https://image.tmdb.org/t/p/w185/s.jpg")
    assert (actor["subtitle"], actor["image"], actor["wide"]) == ("herectví", "", True)
    assert director["target"] == {"type": "list", "call": "discover_person", "params": {"id": 5, "name": "Jan Svěrák"},
                                  "title": "Jan Svěrák", "poster": True}
    films = api("discover_person", **director["target"]["params"])
    assert films["title"] == "Jan Svěrák" and films["next"] is None
    assert [(card["title"], card["subtitle"]) for card in films["items"]] == [   # režie první, pak role; bez opakování
        ("Seriál", "2001 · seriál · režie"), ("Kolja", "1996 · 77 % · film · režie"),
        ("Kuky se vrací", "2010 · film · vypravěč")]
    assert films["items"][0]["target"]["params"]["kind"] == "tv"
    assert "Chybí osoba" in api("discover_person")["error"]


MANIFEST = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" xmlns:dashif="https://dashif.org/"
  xmlns:mspr="urn:microsoft:playready"><Period><AdaptationSet>
  <ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc" cenc:default_KID="00"/>
  <ContentProtection schemeIdUri="urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95"><mspr:pro>AAAA</mspr:pro>
    <dashif:laurl>https://pr.example/licence</dashif:laurl></ContentProtection>
  <ContentProtection schemeIdUri="urn:uuid:EDEF8BA9-79D6-4ACE-A3C8-27DCD51D21ED">%s</ContentProtection>
</AdaptationSet></Period></MPD>"""


def test_licence_address_is_looked_up_in_the_manifest_when_the_player_finds_none(api, monkeypatch):
    found, summary = ivysilani.manifest_protection(MANIFEST % "<cenc:pssh>AAAA</cenc:pssh><x:LicenseUrl xmlns:x=\"u\">https://wv.example/l?a=1</x:LicenseUrl>")
    assert found == {"license": "https://wv.example/l?a=1"}                  # jen z prvku Widevine, ne PlayReady
    assert summary == "manifest: cenc(@default_KID); playready(laurl, pro); widevine(LicenseUrl, pssh)"
    bare = ivysilani.manifest_protection(MANIFEST % "<cenc:pssh>AAAA</cenc:pssh>")
    assert bare == (None, "manifest: cenc(@default_KID); playready(laurl, pro); widevine(pssh)")
    assert ivysilani.manifest_protection("<MPD><Period/></MPD>") == (None, "manifest bez ochrany")
    assert ivysilani.manifest_protection("není xml") == (None, "manifest nejde přečíst")
    # adresa pod polem pojmenovaným po licenci, i když se objekt nehlásí k Widevine; cizí ochrana se přeskočí
    assert ivysilani.drm_license({"streams": [{"url": "https://cdn/x", "licenseUrl": "https://wv.example/a"}]}) == {
        "license": "https://wv.example/a"}
    assert ivysilani.drm_license({"streams": [{"fairplay": {"licenseUrl": "https://fp.example/a"}}]}) is None
    shape = ivysilani.response_shape({"title": "t", "analytics": {"a": 1}, "streams": [{"url": "u"}], "adOcean": {}})
    assert shape == "{streams[{url}], title}"                                # streamy první, měření vůbec

    asked = []

    class Client:
        def vod(self, idec, drm=False):
            asked.append(drm)
            if not drm:
                raise ivysilani.NotPlayable("Nainstalujte si prohlížeč, který ochranu DRM podporuje.")
            return {"url": "https://cdn/get/?token=%d" % len(asked), "subtitles": [], "duration": 60, "title": "Film",
                    "show_title": "", "episode_title": "", "show_id": "9", "image": "", "dash": True, "drm": None,
                    "shape": "{streams[{url}]}"}

        def manifest(self, url):
            asked.append(url)
            return self.text

    client = Client()
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: client)
    first = api("ivys_play", idec="123")                                     # napoprvé nechá hledat přehrávač
    assert first["drm"] == {"license": ""} and asked == [False, True]
    del asked[:]
    client.text = MANIFEST % "<x:LicenseUrl xmlns:x=\"u\">https://wv.example/l</x:LicenseUrl>"
    second = api("ivys_play", idec="123", inspect=True)                      # po chybě přehrávače se podívá sama
    assert second["drm"] == {"license": "https://wv.example/l"}
    assert asked == [False, True, "https://cdn/get/?token=2", True] and second["url"].endswith("token=4")   # čerstvá adresa
    client.text = MANIFEST % "<cenc:pssh>AAAA</cenc:pssh>"
    third = api("ivys_play", idec="123", inspect=True)                       # adresa není ani tam: aspoň je vidět proč
    assert third["drm"] == {"license": ""}
    assert third["diagnostic"].startswith("manifest: cenc(@default_KID); playready(laurl, pro); widevine(pssh); {streams")


def test_playback_preferences_are_offered_as_choices(api):
    offered = api("prefs_choices")
    assert offered["apply"] == "settings_set" and offered["reset"] is None
    assert [(group["title"], group["value"]) for group in offered["filters"]] == [
        ("Jazyk", "Český dabing"), ("Kvalita", "Nejvyšší dostupná")]
    subtitles = offered["filters"][0]["options"][1]
    assert subtitles == {"title": "Originál s titulky", "current": False, "params": {"prefer_language": "subs"}}
    api(offered["apply"], **subtitles["params"])
    assert api("prefs_choices")["filters"][0]["value"] == "Originál s titulky"
    assert "Originál s titulky" in _section(api, "tools")["Nástroje"][5]["subtitle"]

