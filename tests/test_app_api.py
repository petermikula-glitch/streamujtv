"""Rozhraní Android aplikace nad logikou doplňku (android/app/src/main/python/app_api.py)."""
import json
import os
import re
import sys
import time

import pytest
from urllib.parse import unquote

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
    app_api.app_discover._PAUSED.clear()   # žádná služba není z dřívějšího testu „mimo provoz“
    # výpisy z Objevuj v testech na ověření dostupnosti čekají; ověřování na pozadí má vlastní test
    monkeypatch.setattr(app_api.app_discover, "BACKGROUND", False)
    # nejnižší nabízená kvalita je v aplikaci zapnutá (Full HD a vyšší); testy ji zapínají jen tam, kde o ni jde
    monkeypatch.setattr(app_api, "MIN_QUALITY_DEFAULT", "")
    app_api.app_discover._VERIFY.update(queue=[], active=set(), thread=None, failed={}, hold=0)
    monkeypatch.setattr(app_api.app_discover, "SEARCH_RETRY_WAIT", 0)   # druhý pokus po dočasné chybě bez čekání
    monkeypatch.setattr(app_api.app_discover, "PACE_FASTEST", 0)        # ověřování na pozadí bez rozestupů
    monkeypatch.setattr(app_api.app_discover, "GENTLE_PACE", 0)
    app_api.app_discover._PACE.update(interval=0, next=0, gentle=0)
    app_api.app_discover._LIMITED.update(until=0.0, wait=0, last=0.0, hits=0)   # Přehraj.to dotazy neomezuje
    app_api.app_index._CRAWL["stop"] = False                                    # (obnova ze zálohy plnění seznamu zastavuje)
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


def _tool(api, title):
    """Dlaždice Nástrojů podle začátku názvu (pořadí dlaždic se mění, název ne)."""
    return next(card for card in _section(api, "tools")["Nástroje"] if card["title"].startswith(title))


def _section(api, name):
    """Řady jedné sekce hlavní obrazovky: {název řady: karty}. Mřížka dlaždic je víc řad pod jedním
    nadpisem (další jsou bez nadpisu) – tady se spojí do jedné."""
    if name in ("movies", "series"):   # složky sekce Objevuj mají vlastní obrazovku
        section = api("discover_home", kind="tv" if name == "series" else "movie")
    else:
        section = next(item for item in api("home")["sections"] if item["id"] == name)
    rows, last = {}, ""
    for row in section["rows"]:
        last = row["title"] or last
        rows.setdefault(last, []).extend(row["items"])
    return rows


def test_empty_home_leads_to_login_and_always_offers_television_and_tools(api):
    home = api("home")
    sections = home["sections"]
    assert [(section["id"], section["title"]) for section in sections] == [
        ("favorites", "Oblíbené"), ("progress", "Rozkoukané"), ("discover", "Objevuj"),
        ("ivysilani", "iVysílání"), ("tools", "Nástroje")]
    assert home["start"] == 2                              # nic oblíbeného ani rozkoukaného: začíná se v Objevuj
    discover = _section(api, "discover")                   # dvě složky a hledání; přihlášení, dokud účet chybí
    assert list(discover) == ["Začínáme", "Objevuj"] and _titles(discover["Objevuj"]) == ["Filmy", "Seriály", "Hledat"]
    assert discover["Začínáme"][0]["target"] == {"type": "settings"}
    assert discover["Objevuj"][0]["target"] == {"type": "page", "call": "discover_home", "params": {"kind": "movie"},
                                                "title": "Filmy"}
    assert discover["Objevuj"][1]["target"]["params"] == {"kind": "tv"} and sections[2]["rows"][1]["tiles"] is True
    assert api("discover_home", kind="tv")["title"] == "Seriály"
    films, series = _section(api, "movies"), _section(api, "series")     # obsah složek: mřížky dlaždic jako dřív
    assert list(films) == ["Objevuj filmy", "Moje filmy"]
    assert list(series) == ["Objevuj seriály", "Moje seriály"]
    grid = [row for row in api("discover_home", kind="movie")["rows"] if row.get("tiles")]   # velké dlaždice po třech
    assert [(row["title"], len(row["items"])) for row in grid] == [
        ("Objevuj filmy", 3), ("", 3), ("", 2), ("Moje filmy", 2)]
    assert _titles(films["Objevuj filmy"])[:3] == ["Vše na Přehraj.to", "Nově na Přehraj.to", "Novinky"]   # bez „S dabingem“
    assert films["Objevuj filmy"][0]["target"] == {"type": "list", "call": "discover_index", "params": {"kind": "movie"},
                                                   "title": "Filmy na Přehraj.to", "poster": True}
    assert films["Objevuj filmy"][0]["subtitle"] == "Seznam se plní sám na pozadí"
    assert _titles(films["Moje filmy"]) == ["Stažené filmy", "Hledat"]           # „z mého účtu“ vlastník nechce
    assert films["Moje filmy"][0]["target"] == {"type": "list", "call": "downloads", "params": {"kind": "movie"},
                                                "title": "Stažené filmy", "poster": True}
    assert _titles(series["Moje seriály"]) == ["Stažené díly", "Hledat"]
    assert _titles(films["Objevuj filmy"]) == ["Vše na Přehraj.to", "Nově na Přehraj.to", "Novinky",
                                                "Populární", "Nejlépe hodnocené", "Podle roku", "Podle žánru",
                                                "Československé"]
    assert series["Objevuj seriály"][5]["target"] == {"type": "list", "call": "discover_years",
                                                      "params": {"kind": "tv"}, "title": "Seriály podle roku"}
    assert _titles(_section(api, "favorites")["Oblíbené"]) == ["Zatím nic"]          # prázdné sekce napoví, co s nimi
    assert _titles(_section(api, "progress")["Rozkoukané"]) == ["Nic rozkoukaného"]
    television = _section(api, "ivysilani")                  # jen věci z iVysílání
    assert list(television) == ["Živé vysílání", "Kategorie"]
    assert television["Živé vysílání"][0]["target"]["call"] == "ivys_play"
    assert _titles(television["Kategorie"]) == ["Seriály", "Filmy"]
    assert television["Kategorie"][0]["target"] == {"type": "list", "call": "ivys_category",
                                                    "params": {"cid": "3976"}, "title": "Seriály"}
    tools = _section(api, "tools")["Nástroje"]
    # Hledání (lupa je nahoře všude), synchronizace a doplnění hodnocení (běží samy; ručně v Nastavení) tu nejsou.
    assert _titles(tools) == ["Návod", "Stahování", "Nastavení a přihlášení", "Jazyk a kvalita přehrávání",
                              "Profil: Hlavní profil", "Záloha a obnova", "Diagnostika",
                              "Smazat historii"]
    assert [item["target"]["type"] for item in tools] == ["list", "list", "settings", "choices", "list", "backup",
                                                          "diagnostics", "command"]
    assert tools[0]["target"]["call"] == "guide" and tools[1]["target"]["call"] == "downloads"
    assert tools[3]["subtitle"] == "Český nebo slovenský dabing · Nejvyšší dostupná"
    assert tools[4]["image"] == "res:avatar_clapper"


def test_catalogue_rows_lists_and_search(api):
    _fill()
    with app_api._library() as lib:
        lib.record_play(REF, 1200, 6000)
    rows = _section(api, "movies")
    assert "Začínáme" not in rows
    unfinished = _section(api, "progress")
    assert list(unfinished) == ["Filmy", "Historie"]                         # rozkoukané mají vlastní sekci
    assert _titles(unfinished["Filmy"]) == ["Pelíšky"] and unfinished["Filmy"][0]["progress"] == 20
    film = rows["Nově přidané z účtu"][0]
    assert film == {"title": "Pelíšky", "subtitle": "1999 · 91 % · CZ dabing, EN", "image": "https://img/pelisky.jpg",
                    "progress": 20, "wide": False, "watched": False, "icon": "movies", "menu": film["menu"],
                    "target": {"type": "play", "call": "play", "params": {"vid": REF}}}  # česká verze ve vyšší kvalitě
    assert [item["title"] for item in film["menu"]] == [
        "Přehrát", "Přehrát od začátku", "Stáhnout na disk", "Označit jako zhlédnuté", "Odebrat z Rozkoukaných",
        "Opravit přiřazení", "Přidat do oblíbených"]
    assert rows["Moje filmy"][0]["icon"] == "download"                # dlaždice bez obrázku nese ikonu
    assert _section(api, "series")["Nově přidané z účtu"][0]["target"] == {"type": "list", "call": "show", "params": {"key": "show:archa"},
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
    known, online, filtered, more = rows["Videa na Přehraj.to"]
    assert filtered["title"] == "Filtrovat videa" and filtered["target"]["params"] == {
        "q": "Pelíšky", "group": True, "listing": True}
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
    home = _section(api, "progress")
    assert any(card["target"]["params"]["vid"] == episode and card["progress"] == 25
               for card in home["Seriály"])
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
    # vypnuto brzy po začátku: i tak se příště naváže (jen pár vteřin se bere jako omyl)
    api("progress_save", track=REF, position=40, total=6000)
    assert api("play", vid=REF)["position"] == 40
    api("progress_save", track=REF, position=8, total=6000)
    assert api("play", vid=REF)["position"] == 0
    assert api("progress_save", track=REF, position=5990, total=6000, ended=True) == {"saved": True}
    with app_api._library() as lib:
        assert lib.watched() == {REF} and lib.resume_points() == {}
    assert api("progress_save", track="", position=5, total=10) == {"saved": False}

    api("link_failed", track=REF)                       # přehrávač adresu odmítl → příště se načte znovu
    api("play", vid=REF)
    assert len(clients) == 2

    # Změna předvolené kvality: zapamatovaná adresa vznikla s jinou předvolbou, varianta se vybere znovu.
    api("settings_set", prefer_quality="720")
    api("play", vid=REF)
    assert len(clients) == 3
    api("play", vid=REF)
    assert len(clients) == 3                            # se stejnou předvolbou zase ze zapamatované adresy
    with app_api._library() as lib:
        assert json.loads(lib.get_kv(app_api.LINK_PREFIX + REF))["quality"] == "720"
    api("settings_set", prefer_quality="best")
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
    wanted, real = [], app_api._enrich
    monkeypatch.setattr(app_api, "_enrich", lambda statuses=("new",), **more: wanted.append(statuses) or real(statuses, **more))
    result = api("sync")
    assert result["message"].startswith("Videí: 2, nových: 2, odebraných: 0.") and "doplněna u 2" in result["message"]
    # ruční synchronizace doplňuje hodnocení a plakáty i tam, kde se to dřív nepovedlo (dlaždice na to už není)
    assert wanted == [("new", "notfound", "error", "blocked")]
    assert seen == [{"text": "Načítám videa z účtu: stránka 3, 2 videí", "busy": True}]
    assert api("progress") == {"text": "", "busy": False}
    assert _tool(api, "Nastavení")["subtitle"] == "a@b.cz"
    assert app_api._settings()["last_sync"] > 0                              # (kdy naposledy, ukazuje Diagnostika)

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
    assert _titles(seasons["items"]) == ["I. řada", "Filmy o seriálu", "Všechny díly", "Přidat do oblíbených"]
    episodes = api("ivys_show", id="12", season="season:1")
    assert len(episodes["items"]) == 41 and episodes["next"]["params"]["offset"] == 40
    assert api("ivys_favorite", id="12")["message"].startswith("Přidáno do oblíbených.")
    assert _titles(api("ivys_show", id="12")["items"])[-1] == "Odebrat z oblíbených"
    with app_api._library() as lib:
        lib.ivys_favorite_seen("12", 120)
    assert api("ivys_favorites")["items"][0]["subtitle"] == "nové díly: 3"
    assert _titles(_section(api, "ivysilani")["Moje pořady"])[-1] == "Moje pořady"

    stream = api("ivys_play", idec="211522160100014")
    assert stream["hls"] and stream["track"] == "ivys:211522160100014" and stream["subtitles"] == ["https://ctfs/s.vtt"]
    assert stream["title"] == "Na plovárně · Terry Pratchett"
    api("progress_save", track=stream["track"], position=400, total=1326)
    assert api("ivys_play", idec="211522160100014")["position"] == 400
    progress = _section(api, "ivysilani")["Rozkoukané"]
    assert progress[0]["title"] == "Na plovárně · Terry Pratchett" and progress[0]["progress"] == 30
    assert _section(api, "progress")["Seriály"] == progress               # a stejná karta i ve společných Rozkoukaných
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
    assert [item["title"] for item in card["menu"]] == ["Přehrát", "Zrušit stahování", "Označit jako zhlédnuté",
                                                        "Opravit přiřazení", "Přidat do oblíbených"]
    favorite = card["menu"][-1]["target"]                                    # stažené video jde dát do oblíbených
    assert api(favorite["call"], **favorite["params"])["message"] == "Přidáno do oblíbených: Pelíšky"
    assert api("downloads")["items"][0]["menu"][-1]["title"] == "Odebrat z oblíbených"
    assert "Pelíšky" in _titles(api("favorites")["items"])
    api(favorite["call"], **favorite["params"])
    rows = _section(api, "movies")                                           # dlaždice vede na stažené filmy
    assert rows["Moje filmy"][0]["title"] == "Stažené filmy (1)" and rows["Moje filmy"][0]["icon"] == "download"
    assert _section(api, "series")["Moje seriály"][0]["title"] == "Stažené díly"
    assert _titles(api("downloads", kind="movie")["items"]) == ["Pelíšky"] and api("downloads", kind="movie")["title"] == "Stažené filmy"
    assert api("downloads", kind="episode")["items"] == [] and api("downloads", kind="episode")["title"] == "Stažené díly"
    assert api("downloads", kind="movie")["items"][0]["image"] == "https://img/pelisky.jpg"   # plakát filmu, ne náhled
    assert "1 ve frontě" in _tool(api, "Stahování")["subtitle"]

    (disk / "Pelíšky.mkv").write_bytes(b"x" * 200)
    (disk / "Pelíšky.1.vtt").write_text("WEBVTT")
    assert api("download_finish", vid=REF, size=200, subtitles=[job["subtitles"][0]["path"]]) == {"saved": True}
    stream = api("play", vid=REF)                                            # hraje z disku, bez přihlášení
    assert stream["local"] is True and stream["url"] == job["path"] and stream["subtitles"] == [str(disk / "Pelíšky.1.vtt")]
    rows = _section(api, "movies")
    film = rows["Nově přidané z účtu"][0]
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
    blocked = api("download_next", dir=disk)                                 # bez přihlášení adresu nezíská
    assert blocked["error"].startswith("Nejdřív v Nastavení")                # fronta ale zůstane, jak je
    assert "Chyba" not in api("downloads")["items"][0]["subtitle"]

    genuine = app_api._stream_link

    def offline(vid, fresh=False):
        try:
            raise app_api.urllib.error.URLError("síť je pryč")
        except app_api.urllib.error.URLError:
            raise app_api.ApiError("Přehraj.to je nedostupné")

    monkeypatch.setattr(app_api, "_stream_link", offline)                    # výpadek sítě: taky nic neselže
    assert api.raw("download_next", dir=disk)["offline"] is True
    assert "Chyba" not in api("downloads")["items"][0]["subtitle"]

    def gone(vid, fresh=False):
        raise app_api.ApiError("Video už na Přehraj.to není dostupné.")

    monkeypatch.setattr(app_api, "_stream_link", gone)                       # chyba jednoho videa: to selže
    assert api("download_next", dir=disk) == {"vid": ""}
    monkeypatch.setattr(app_api, "_stream_link", genuine)
    card = api("downloads")["items"][0]
    assert card["subtitle"].startswith("Chyba: Video už na Přehraj.to není")
    assert [item["title"] for item in card["menu"]] == ["Přehrát", "Zkusit stáhnout znovu", "Odebrat ze stahování",
                                                        "Označit jako zhlédnuté", "Opravit přiřazení",
                                                        "Přidat do oblíbených"]
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
            {"name": "MojeVidea-0.3.0.apk", "browser_download_url": "https://gh/MojeVidea-0.3.0.apk", "size": 42,
             "digest": "sha256:" + "AB" * 32}]},
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
    assert found == {"available": True, "current": "0.2.0", "version": "0.3.0", "size": 42, "sha256": "ab" * 32,
                     "url": "https://gh/MojeVidea-0.3.0.apk", "notes": "Stahování na disk"}   # i s otiskem souboru
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
    placeholder = _section(api, "favorites")["Oblíbené"]
    assert _titles(placeholder) == ["Zatím nic"] and placeholder[0]["target"]["call"] == "favorites"
    film = _section(api, "movies")["Nově přidané z účtu"][0]
    add = film["menu"][-1]
    assert add["title"] == "Přidat do oblíbených" and add["target"]["type"] == "command"
    assert api(add["target"]["call"], **add["target"]["params"]) == {"message": "Přidáno do oblíbených: Pelíšky"}
    series = _section(api, "series")["Nově přidané z účtu"][0]
    assert _titles(series["menu"]) == ["Otevřít", "Přidat do oblíbených", "Opravit přiřazení"]
    api(series["menu"][1]["target"]["call"], **series["menu"][1]["target"]["params"])

    rows = _section(api, "favorites")                               # vlastní sekce, filmy a seriály zvlášť
    assert list(rows) == ["Filmy", "Seriály"]
    assert _titles(rows["Filmy"]) == ["Pelíšky"] and _titles(rows["Seriály"]) == ["Archa"]
    assert not any("Oblíbené" in name for name in list(_section(api, "movies")) + list(_section(api, "series")))
    favorites = rows["Seriály"] + rows["Filmy"]
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
    listing = api("discover", kind="movie", category="popular", avail="all")   # všechny tituly, bez ověřování
    filtering, check, card = listing["items"]                           # položka bez čísla TMDB se přeskočí
    # místo filtru řazení (žánr, rok, dabing mají vlastní dlaždice); karta říká, jak je seřazeno
    assert filtering["title"] == "Seřadit" and filtering["subtitle"] == "Podle oblíbenosti" and filtering["target"] == {
        "type": "filter", "call": "discover_sorts", "params": {"kind": "movie", "category": "popular", "avail": "all"}}
    assert check["title"] == "Ověřit dostupnost" and check["target"]["call"] == "discover_check"
    assert (card["title"], card["subtitle"], card["wide"]) == ("Pelíšky", "1999 · 86 % · film", False)
    assert card["image"] == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert card["target"] == {"type": "title", "call": "discover_title", "title": "Pelíšky", "params": {
        "kind": "movie", "id": 11, "title": "Pelíšky", "year": 1999, "poster": card["image"], "orig": "Pelisky"}}
    assert _titles(card["menu"]) == ["Otevřít", "Přehrát hned", "Přidat do oblíbených"]
    assert card["menu"][1]["target"]["call"] == "discover_movie_play" and card["menu"][1]["target"]["type"] == "play"
    assert listing["next"] == {"call": "discover", "params": {"kind": "movie", "category": "popular", "avail": "all",
                                                              "page": 2}}
    api("discover", kind="movie", category="popular", avail="all")       # podruhé z mezipaměti
    assert [path for path, _ in calls] == ["/movie/popular"]

    czech = api("discover", kind="tv", origin="CZ|SK|XC", year="1990s", avail="all")
    assert _titles(czech["items"]) == ["Seřadit", "Archa"] and czech["items"][1]["subtitle"] == "1996 · seriál"
    assert czech["items"][0]["subtitle"] == "Podle oblíbenosti"
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
    assert _titles(special) == ["Všechny", "Podle roku", "Podle žánru"]                # Všechny (seřadit jde v nich)
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
                              "backdrop": "https://image.tmdb.org/t/p/w1280/b.jpg",
                              # který soubor tlačítko Přehrát pustí
                              "file": "Soubor: Pelisky.1999.CZ.1080p · 4 GB · 2 h 00 min"}
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
    play, download, about, favourite, more = page["actions"]
    # vybraný soubor jde rovnou stáhnout na disk; tlačítko říká, jak je velký
    assert (download["title"], download["subtitle"]) == ("Stáhnout na disk", "4 GB")
    assert download["target"] == {"type": "download", "call": "download_add", "params": best["target"]["params"]}
    # celý popis a další údaje, na které na stránce není místo
    assert about["title"] == "Více o filmu" and about["target"]["type"] == "info" and about["target"]["title"] == "Pelíšky"
    assert about["target"]["params"]["text"] == (
        "1999 · 86 % · 1 h 56 min · Komedie, Drama\n\nRodinná komedie.\n\nŽánr: Komedie, Drama")
    # méně používané volby jsou pod „Další“, aby se tlačítka vešla do dvou řádků
    assert more["title"] == "Další" and _titles(more["menu"]) == ["Hledat ručně", "Jazyk a kvalita u tohoto filmu"]
    manual, prefs = more["menu"]
    # jen pro tento film: nastavení celé aplikace je v Nástrojích
    assert prefs["target"] == {"type": "choices", "call": "title_prefs_choices", "params": {"kind": "movie", "id": 11}}
    assert (play["title"], play["subtitle"]) == ("Přehrát", "CZ dabing · 1080p") and play["target"] == best["target"]
    assert favourite["title"] == "Do oblíbených"
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
    favorite = _section(api, "favorites")["Filmy"][0]
    # oblíbený film se klepnutím rovnou přehraje (stránka filmu je v nabídce karty)
    assert favorite["title"] == "Pelíšky" and favorite["target"]["call"] == "discover_movie_play"
    assert favorite["target"]["params"]["id"] == 11 and favorite["target"]["params"]["resume"] is True
    assert [item["title"] for item in favorite["menu"]] == [
        "Přehrát", "Přehrát od začátku", "Otevřít stránku filmu", "Odebrat z oblíbených"]
    assert favorite["menu"][2]["target"]["type"] == "title"
    assert api("discover_title", **FILM)["actions"][3]["title"] == "Z oblíbených"

    nothing = api("discover_title", kind="movie", id=5, title="Neznámý film", year=2001)
    assert nothing["rows"] == [] and [action["title"] for action in nothing["actions"]] == [
        "Do oblíbených", "Hledat ručně", "Jazyk a kvalita u tohoto filmu"]  # není co přehrát: volby jsou rovnou vidět
    assert "nenašel" in nothing["detail"]["plot"]
    assert asked[-2:] == [("Neznámý film 2001", 1), ("Neznámý film", 1)]     # zkusí i bez roku

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("HTTP 500")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)
    failed = api("discover_title", **FILM)                                   # hledání selhalo: stránka není slepá ulička
    assert failed["detail"]["title"] == "Pelíšky" and failed["detail"]["plot"].startswith(
        "Hledání na Přehraj.to se teď nezdařilo: Přehraj.to má výpadek. Neznamená to, že tam titul není")
    assert "Rodinná komedie." in failed["detail"]["plot"] and failed["rows"] == []
    assert [(action["title"], action["target"]["type"]) for action in failed["actions"]] == [
        ("Zkusit znovu", "refresh"), ("Více o filmu", "info"), ("Z oblíbených", "command"), ("Hledat ručně", "search"),
        ("Jazyk a kvalita u tohoto filmu", "choices")]


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
    assert list(rows) == ["Celá série", "Hrají", "Podobné tituly"]     # herci nad podobnými tituly
    assert _titles(rows["Celá série"]) == ["Pelíšky", "Pelíšky 2"]             # v pořadí uvedení
    assert rows["Celá série"][0]["subtitle"].startswith("Právě otevřený")
    assert rows["Celá série"][1]["target"]["params"]["id"] == 12 and rows["Celá série"][1]["target"]["type"] == "title"
    assert rows["Podobné tituly"][0]["title"] == "Pupendo" and rows["Podobné tituly"][0]["subtitle"] == "2003 · 79 % · film"
    actor, second = rows["Hrají"]
    assert (actor["title"], actor["subtitle"]) == ("Miroslav Donutil", "Šebek")
    assert actor["image"] == "https://image.tmdb.org/t/p/h632/d.jpg" and second["image"] == "" and second["wide"]
    assert actor["target"] == {"type": "list", "call": "discover", "params": {"kind": "movie", "cast": "5"},
                               "title": "Filmy – Miroslav Donutil", "poster": True}
    films = api("discover", avail="all", **actor["target"]["params"])        # filmy s hercem
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


def test_favourite_is_announced_when_a_version_matching_preferences_appears(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = {}
    asked = _catalogue_search(monkeypatch, found)
    page = api("discover_title", **FILM)                                     # film na Přehraj.to zatím není
    favourite = next(action for action in page["actions"] if action["title"] == "Do oblíbených")
    api(favourite["target"]["call"], **favourite["target"]["params"])
    api("discover_title", **FILM)                                            # stránka si u oblíbeného zapamatuje stav
    before = len(asked)
    assert api("favorites_check") == {"found": []} and len(asked) > before   # pořád nic
    before = len(asked)
    assert api("favorites_check") == {"found": []} and len(asked) == before  # podruhé se hned neptá

    found["Pelíšky 1999"] = [_web(60, "Pelisky 1999 EN 1080p", 7000, "4 GB"),     # jiný jazyk, než chci
                             _web(62, "Pelisky 1999 CZ HDCAM", 7000, "1 GB"),       # kino kopie
                             _web(63, "Pelisky 1999 CZ", 7000, "700 MB", "SD")]     # nízká kvalita
    assert api("favorites_check", force=True) == {"found": []}               # verze jsou, ale ne podle předvoleb
    assert "badges" not in _section(api, "favorites")["Filmy"][0]
    found["Pelíšky 1999"].append(_web(61, "Pelisky 1999 CZ 1080p", 7000, "4 GB"))
    announced = api("favorites_check", force=True)["found"]                  # český dabing v dobré kvalitě: upozornit
    assert [(item["title"], item["detail"]) for item in announced] == [("Pelíšky", "CZ dabing · 1080p")]
    assert announced[0]["target"]["type"] == "title" and announced[0]["target"]["params"]["id"] == 11
    card = _section(api, "favorites")["Filmy"][0]
    assert card["badges"] == ["Nově na Přehraj.to"] and card["subtitle"].startswith("CZ dabing · 1080p")
    before = len(asked)
    assert api("favorites_check", force=True) == {"found": []} and len(asked) == before   # vyřízený titul se už nehledá
    api("discover_title", **FILM)                                            # otevřením štítek zmizí
    assert "badges" not in _section(api, "favorites")["Filmy"][0]

    api("settings_set", prefer_language="subs")                              # jiné předvolby: stav se zjišťuje znovu,
    assert api("favorites_check", force=True) == {"found": []}               # první zjištění ale není „novinka“
    found["Pelíšky 1999"].append(_web(64, "Pelisky 1999 CZ titulky 1080p", 7000, "4 GB"))
    again = api("favorites_check", force=True)["found"]
    assert [(item["title"], item["detail"]) for item in again] == [("Pelíšky", "CZ titulky · 1080p")]
    prefs = {"language": "dub", "quality": "1080"}
    assert app_api.app_discover.meets_preferences({"lang": "CZ dabing", "quality": "1080p", "raw_title": "x"}, prefs)
    assert not app_api.app_discover.meets_preferences({"lang": "CZ dabing", "quality": "720p", "raw_title": "x"}, prefs)
    assert app_api.app_discover.meets_preferences({"lang": "EN", "quality": "", "raw_title": "x"},
                                                  {"language": "any", "quality": "small"})


def test_downloaded_version_comes_first_and_film_plays_without_its_page(api, monkeypatch, tmp_path):
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="heslo")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    found = {"Pelíšky 1999": [_web(71, "Pelisky 1999 CZ 1080p", 2 * hour, "4 GB"),
                              _web(72, "Pelisky 1999 EN 720p", 2 * hour, "1 GB")]}
    _catalogue_search(monkeypatch, found)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/%s.mp4" % vid.split(":")[0], {}))
    stream = api("discover_movie_play", **FILM)                              # „Přehrát hned“: nejlepší verze
    assert stream["track"] == _web(71, "")["id"] and stream["choice"] == "movie:11"

    english = _web(72, "")["id"]                                             # anglická verze je stažená na disku
    api("download_add", vid=english, web_result=found["Pelíšky 1999"][1])
    api("play", vid=english, web_result=found["Pelíšky 1999"][1], choice="movie:11")
    waiting = api("discover_title", **FILM)                                  # ve frontě: tlačítko vede na Stahování
    assert waiting["detail"]["file"] == "Soubor: Pelisky 1999 EN 720p · 1 GB · 2 h 00 min"
    assert (waiting["actions"][1]["title"], waiting["actions"][1]["target"]["call"]) == ("Čeká na stažení", "downloads")
    disk = tmp_path / "usb"
    disk.mkdir()
    job = api("download_next", dir=str(disk))
    (disk / os.path.basename(job["path"])).write_bytes(b"x" * 10)
    api("download_finish", vid=english, size=10, subtitles=[])
    api("play", vid=english, web_result=found["Pelíšky 1999"][1], choice="movie:11")
    page = api("discover_title", **FILM)
    first = page["rows"][0]["items"][0]
    assert first["target"]["params"]["vid"] == english and first["badges"][0] == "Na disku"   # stažená má přednost
    assert first["subtitle"].startswith("Staženo") and page["actions"][0]["target"] == first["target"]
    # stránka říká, že se pustí soubor z disku; tlačítko na stažení už není potřeba
    assert page["detail"]["file"] == "Soubor na disku: Pelisky 1999 EN 720p · 1 GB · 2 h 00 min"
    assert "Stáhnout na disk" not in _titles(page["actions"])
    assert api("discover_movie_play", **FILM)["local"] is True               # a hraje z disku
    favorite = api("downloads")["items"][0]["menu"][-1]                      # stažený film z Objevuj → oblíbené
    assert favorite["title"] == "Přidat do oblíbených" and favorite["target"]["params"]["id"] == "tmdb:movie:11"

    del found["Pelíšky 1999"]                                                # z hledání zmizela, na disku zůstala

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("HTTP 500")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)
    offline = api("discover_title", **FILM)                                  # hledání nejde: stažená verze stačí
    assert _titles(offline["actions"])[0] == "Přehrát" and len(offline["rows"][0]["items"]) == 1
    assert offline["rows"][0]["items"][0]["target"]["params"]["vid"] == english


def test_web_results_of_one_title_are_grouped(api, monkeypatch):
    hour = 3600
    found = {"Pelíšky": [_web(71, "Pelisky 1999 EN 720p", 2 * hour, "1 GB"), _web(72, "Jiny film 2001 CZ", 2 * hour),
                         _web(73, "Pelisky.1999.CZ.1080p", 2 * hour, "4 GB"), _web(74, "Pelisky 1999 CZ tit", 2 * hour)]}
    _catalogue_search(monkeypatch, found)
    rows = {row["title"]: row["items"] for row in api("search", q="Pelíšky")["rows"]}
    grouped, other, _filtered = rows["Videa na Přehraj.to"]
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
    assert first["Předvolby"]["value"] == "Český nebo slovenský dabing, Nejvyšší dostupná"

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
        ("Přehrát", "1×01 · Pilot"), ("Více o seriálu", ""), ("Do oblíbených", ""), ("Další", "")]
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
    assert "Další díly" not in _section(api, "progress")
    following = api("play_next", track=first)["next"]
    assert following == {"title": "Další díl", "subtitle": "Archa 1×02", "auto": True,
                         "target": {"type": "play", "call": "play", "params": {"vid": second}}}
    assert api("play_next", track=third)["next"] is None                    # poslední díl: není co nabídnout
    assert api("play_next", track="")["next"] is None and api("play_next", track=REF)["next"] is None

    api("progress_save", track=first, position=1190, total=1200, ended=True)
    # po dokoukání čeká další díl rovnou na kartě seriálu (jedna karta za seriál)
    row = _section(api, "progress")["Seriály"]
    assert len(row) == 1 and row[0]["target"]["params"] == {"vid": second}
    assert row[0]["subtitle"].startswith("Další díl")
    assert "Další díly" not in _section(api, "progress")
    api("progress_save", track=second, position=300, total=1200, started=True)
    assert "Další díly" not in _section(api, "progress")                    # rozkoukaný díl je už mezi rozkoukanými
    api("progress_save", track=first, position=400, total=1200, started=False)   # starší díl znovu rozkoukaný…
    api("progress_save", track=second, position=320, total=1200)
    assert len(_section(api, "progress")["Seriály"]) == 1                    # …ale seriál má pořád jednu kartu
    # karta v Rozkoukaných naváže rovnou, bez dotazu „Pokračovat, nebo od začátku?“
    assert _section(api, "progress")["Seriály"][0]["target"]["params"] == {"vid": second, "resume": True}


def test_next_episode_never_silently_skips_a_missing_one(api, monkeypatch):
    """Katalog má 2. a 4. díl: po druhém se nejdřív hledá třetí; čtvrtý se sám nespustí."""
    _fill(2)
    second, fourth = ["%d:archa:%016x" % (10 + n, 10 + n) for n in (2, 4)]
    with app_api._library() as lib:
        lib.upsert_video(fourth, "Archa.S01E04.CZ", "account", nameparser.parse("Archa.S01E04.CZ"))
    asked = _catalogue_search(monkeypatch, {})
    following = api("play_next", track=second)["next"]
    assert asked and all("S01E03" in query or "1x03" in query.lower() for query, _page in asked)   # hledal se třetí díl
    assert following == {"title": "Další díl v katalogu", "subtitle": "Archa 1×04 · díl 1×03 chybí", "auto": False,
                         "target": {"type": "play", "call": "play", "params": {"vid": fourth}}}

    # třetí díl se na Přehraj.to najde: nabídne se ten a spustí se sám jako obvykle
    found = _web(77, "Archa S01E03 CZ dabing", 2400, "700 MB")
    queries = {query: [found] for query, _page in asked}
    _catalogue_search(monkeypatch, queries)
    following = api("play_next", track=second)["next"]
    assert (following["title"], following["subtitle"], following["auto"]) == ("Další díl", "Archa 1×03", True)
    assert following["target"]["params"]["vid"] == found["id"]

    # první díl další řady na poslední díl v katalogu navazuje (kolik dílů řada má, katalog neví)
    with app_api._library() as lib:
        raw = "Archa.S02E01.CZ"
        lib.upsert_video("30:archa:%016x" % 30, raw, "account", nameparser.parse(raw))
    _catalogue_search(monkeypatch, {})
    crossing = api("play_next", track=fourth)["next"]
    assert (crossing["title"], crossing["subtitle"], crossing["auto"]) == ("Další díl", "Archa 2×01", True)



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

    row = _section(api, "progress")["Další díly"]                            # rozkoukaný seriál z Objevuj
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
    listing = api("discover", kind="movie", category="popular", avail="all")["items"]
    check = listing[1]
    assert [card.get("badges") for card in listing[2:]] == [None, None]
    result = api(check["target"]["call"], **check["target"]["params"])
    assert result == {"message": "Ověřeno 2 filmů, na Přehraj.to je 1 z nich."}
    assert api("progress") == {"text": "", "busy": False}
    marked = api("discover", kind="movie", category="popular", avail="all")["items"]   # dlaždice pro ověření už není potřeba
    assert _titles(marked) == ["Seřadit", "Pelíšky", "Kolja"]
    assert [card["badges"] for card in marked[1:]] == [["Přehraj.to", "CZ"], ["Nenalezeno"]]
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
    assert _titles(rows["Filmy"]) == ["Pelíšky"] and _titles(rows["Seriály"]) == ["Archa"]   # filmy a seriály zvlášť
    assert rows["Filmy"][0]["target"]["type"] == "title" and rows["Seriály"][0]["target"]["params"]["kind"] == "tv"
    assert "badges" not in rows["Filmy"][0]
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


def test_protected_programme_without_licence_is_handed_to_the_official_app(api, monkeypatch):
    class Client:
        def __init__(self, stream):
            self.stream = stream

        def vod(self, idec, drm=False):
            if not drm and self.stream.get("dash"):
                raise ivysilani.NotPlayable("Tento pořad nelze v doplňku přehrát (chráněný obsah).")
            return self.stream

    stream = {"url": "https://cdn/get/?streamType=dash", "subtitles": [], "duration": 3000, "title": "Díl",
              "show_title": "Zahraniční seriál", "episode_title": "Díl 2", "show_id": "10195164142", "image": "",
              "dash": True, "drm": None, "shape": ""}
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(stream))
    result = api("ivys_play", idec="208522161400002")
    # ČT licenci neposlala: přehrávač dostane, komu pořad předat – balíčky oficiální aplikace a stránku dílu
    assert result["external"] == {
        "name": "iVysílání ČT", "title": "Zahraniční seriál · Díl 2", "query": "Zahraniční seriál",
        "packages": ["cz.ceskatelevize.ivysilani.tvapp", "cz.motion.ivysilani"],
        "url": "https://www.ceskatelevize.cz/porady/10195164142/208522161400002/",
        "urls": ["https://www.ceskatelevize.cz/porady/10195164142/208522161400002/",
                 "https://www.ceskatelevize.cz/porady/10195164142/"]}
    assert result["dash"] and result["url"]          # bez oficiální aplikace se přehrání zkusí jako dosud

    # pořad bez známého čísla pořadu: aplikace se jen spustí, žádná vymyšlená adresa
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(dict(stream, show_id="")))
    bare = api("ivys_play", idec="208522161400002")["external"]
    assert (bare["url"], bare["urls"], bare["query"]) == ("", [], "Zahraniční seriál")   # zbývá hledání podle názvu

    # s adresou licence od ČT se hraje v aplikaci, stejně jako nechráněný pořad
    licensed = dict(stream, drm={"license": "https://licence.example/wv"})
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(licensed))
    assert "external" not in api("ivys_play", idec="208522161400002")
    plain = dict(stream, dash=False, url="https://cdn/master.m3u8")
    monkeypatch.setattr(app_api, "_ivys", lambda timeout=None: Client(plain))
    assert "external" not in api("ivys_play", idec="208522161400002")

    # balíčky, na které se aplikace ptá, musí být zapsané i v manifestu
    manifest = open(os.path.join(os.path.dirname(__file__), "..", "android", "app", "src", "main",
                                 "AndroidManifest.xml"), encoding="utf-8").read()
    assert all('<package android:name="%s" />' % name in manifest for name in app_api.IVYS_APPS)


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
    assert avatars[0]["subtitle"] == "Už ho někdo má"
    assert avatars[3]["target"] == {"type": "input", "call": "profile_add", "params": {   # po obrázku se zeptá na jméno
        "avatar": "owl", "value": "Sova", "hint": "Jméno nového profilu"}}
    added = api("profile_add", avatar="owl", text="")                       # bez jména dostane název obrázku
    assert added == {"message": "", "toast": "Profil „Sova“ je připravený.", "close": True}
    assert api("profile_avatars", new=1)["items"][3]["target"]["params"]["value"] == "Sova 2"
    owl = api("profiles")["items"][1]
    assert (owl["title"], owl["subtitle"], owl["image"]) == ("Sova", "Přepnout", "res:avatar_owl")
    assert [item["title"] for item in owl["menu"]] == ["Přejmenovat", "Změnit obrázek", "Smazat profil"]
    rename = owl["menu"][0]["target"]
    assert rename["type"] == "input" and rename["params"]["value"] == "Sova"
    assert api(rename["call"], id=rename["params"]["id"], text="  Jana  ") == {
        "message": "", "toast": "Profil se teď jmenuje „Jana“."}
    assert "prázdné" in api(rename["call"], id=rename["params"]["id"], text=" ")["error"]
    assert api(rename["call"], id=rename["params"]["id"], text="Jana") == {"message": ""}   # stejné jméno: nic

    switched = api(owl["target"]["call"], **owl["target"]["params"])        # druhý profil začíná načisto
    assert switched == {"message": "", "close": True, "toast": "Dívá se: Jana"}
    assert api("home")["profile"] == {"name": "Jana", "avatar": "owl", "count": 2}
    assert list(_section(api, "progress")) == ["Rozkoukané"]                 # jen nápověda, nic rozkoukaného
    assert _titles(_section(api, "favorites")["Oblíbené"]) == ["Zatím nic"]  # ani oblíbeného
    assert api("settings_get")["prefer_language"] == "dub"
    assert "smazat nejde" in api("profile_delete", id=owl["target"]["params"]["id"])["error"]
    api("progress_save", track=first, position=300, total=1200, started=True)
    api("settings_set", prefer_quality="720")
    assert _section(api, "progress")["Seriály"][0]["target"]["params"] == {"vid": first, "resume": True}

    main = api("profiles")["items"][0]
    api(main["target"]["call"], **main["target"]["params"])                 # zpět: původní stav je na místě
    assert [item["target"]["params"] for item in _section(api, "progress")["Filmy"]] == [{"vid": REF, "resume": True}]
    assert _titles(_section(api, "favorites")["Filmy"]) == ["Pelíšky"]
    settings = api("settings_get")
    assert (settings["prefer_language"], settings["prefer_quality"]) == ("subs", "best")
    assert meta["key"] and _titles(api("movies")["items"]) == ["Filtr", "Pelíšky"]   # katalog je společný

    api(main["target"]["call"], **owl["target"]["params"])                  # a druhý profil má své
    assert _section(api, "progress")["Seriály"][0]["target"]["params"] == {"vid": first, "resume": True}
    assert api("settings_get")["prefer_quality"] == "720"
    api(main["target"]["call"], **main["target"]["params"])
    assert "smazaný" in api("profile_delete", id=owl["target"]["params"]["id"])["message"]
    assert _titles(api("profiles")["items"]) == ["Hlavní profil", "Přidat profil"]
    assert "neexistuje" in api("profile_switch", id="p9")["error"]


def test_hero_and_watch_next_follow_the_last_unfinished_video(api):
    _fill(2)
    sections = {section["id"]: section for section in api("home")["sections"]}
    assert not any("hero" in section for section in sections.values())
    assert api("watch_next") == {"items": []}
    with app_api._library() as lib:
        lib.update_meta("movie:pelisky|1999", {"status": "ok", "title": "Pelíšky", "year": 1999,
                                               "poster": "https://img/p.jpg", "fanart": "https://img/f.jpg"})
        lib.record_play(REF, 1500, 6000, now=1000)
        lib.record_play("11:archa:%016x" % 11, 300, 1200, now=2000)
        lib.ivys_touch("777", "9", "Díl 3", "Vyprávěj", "https://img/v.jpg", now=3000)
        lib.ivys_record_play("777", 600, 3000, now=3000)
    sections = {section["id"]: section for section in api("home")["sections"]}
    assert "hero" not in sections["discover"] and "hero" not in sections["tools"]   # tam jsou dlaždice
    television = sections["ivysilani"]["hero"]
    assert (television["title"], television["subtitle"]) == ("Vyprávěj · Díl 3", "iVysílání · zbývá 40 min")
    assert television["image"] == "https://img/v.jpg"
    assert television["target"] == {"type": "play", "call": "ivys_play", "params": {"idec": "777", "resume": True}}
    assert sections["progress"]["hero"] == television      # Rozkoukané: naposledy přehrané, ať je odkudkoli

    items = api("watch_next")["items"]                                       # pro domovskou obrazovku televize
    assert [(item["id"], item["title"], item["call"]) for item in items] == [
        ("ivys:777", "Vyprávěj · Díl 3", "ivys_play"), ("11:archa:%016x" % 11, "Archa 1×01", "play"),
        (REF, "Pelíšky", "play")]
    assert (items[2]["position"], items[2]["duration"], items[2]["image"], items[2]["episode"]) == (
        1500, 6000, "https://img/f.jpg", False)
    assert items[0]["params"] == {"idec": "777"} and items[1]["episode"] is True

    with app_api._library() as lib:
        lib.record_play("11:archa:%016x" % 11, 300, 1200, now=4000)         # naposledy díl z Přehraj.to
    hero = {section["id"]: section for section in api("home")["sections"]}["progress"]["hero"]
    assert (hero["title"], hero["label"], hero["progress"]) == ("Archa 1×01", "Pokračovat", 25)
    assert hero["subtitle"] == "CZ dabing · zbývá 15 min" and hero["image"] == ""
    assert hero["target"] == {"type": "play", "call": "play", "params": {"vid": "11:archa:%016x" % 11, "resume": True}}


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
    assert season["target"] == {"type": "download", "call": "download_season", "params": {"key": "show:archa", "season": 1},
                                # před zařazením celé řady se aplikace zeptá; volné místo doplní sama
                                "confirm": {"title": "Stáhnout 1. řadu?", "bytes": 0,
                                            "text": "3 díly. Velikost souborů není známá."}}
    assert api("download_season", **season["target"]["params"]) == {
        "message": "Zařazeno ke stažení: 3 dílů (1. řada).", "start": True}
    listed = api("downloads")["items"]
    assert len([card for card in listed if card["target"]["type"] == "play"]) == 3
    assert _titles(listed)[-1] == "Kam stahovat" and listed[-1]["target"] == {"type": "storage"}
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
    assert not os.path.exists(job["path"])
    assert len([card for card in api("downloads")["items"] if card["target"]["type"] == "play"]) == 2

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


def test_season_download_states_how_much_space_it_needs(api, monkeypatch):
    with app_api._library() as lib:
        for number, size in ((1, "1.5 GB"), (2, "700 MB"), (3, "")):
            raw = "Archa.S01E%02d.CZ" % number
            lib.upsert_video("%d:archa:%016x" % (10 + number, 10 + number), raw, "account", nameparser.parse(raw),
                             size=size)
    season = api("show", key="show:archa")["items"][-1]
    assert season["target"]["confirm"] == {"title": "Stáhnout 1. řadu?", "bytes": int(1.5 * 1024 ** 3) + 700 * 1024 ** 2,
                                           "text": "3 díly, asi 2,2 GB a 1 díl neznámé velikosti."}
    assert api("download_season", **season["target"]["params"])["message"] == (
        "Zařazeno ke stažení: 3 dílů (1. řada). Potřeba asi 2,2 GB a 1 díl neznámé velikosti.")

    api("settings_set", tmdb_key="k" * 32)                                   # řada z Objevuj: velikost až po vyhledání
    _catalogue_search(monkeypatch, {"Krakonoš S01E01": [_web(81, "Krakonos S01E01 CZ", size="1 GB")],
                                    "Krakonoš S01E02": [_web(82, "Krakonos S01E02 CZ", size="2 GB")]})
    _fake_tmdb(monkeypatch, {"/tv/88/season/1": {"episodes": [{"episode_number": n, "name": "Díl"} for n in (1, 2)]}})
    whole = api("discover_season", kind="tv", id=88, title="Krakonoš", season=1)["items"][-1]
    assert whole["target"]["confirm"]["bytes"] == 0 and whole["target"]["confirm"]["text"].startswith("2 díly. Díly se")
    assert api(whole["target"]["call"], **whole["target"]["params"])["message"] == (
        "Zařazeno ke stažení: 2 dílů. Potřeba asi 3,0 GB.")


def test_quick_sync_after_opening_adds_new_videos_and_never_removes(api, monkeypatch):
    _fill(2)                                                    # katalog: Pelíšky (2 verze) a dva díly Archy
    asked = []

    class Client(object):
        listing_complete = False
        listing_partial = True                                  # výpis skončil u známých videí

        def my_videos(self, progress=None, known=None):
            asked.append(known)
            return [{"id": "50:novinka:00000000000000cc", "title": "Novinka.2024.CZ.1080p", "duration": 5400}]

    made = []
    monkeypatch.setattr(app_api.provider, "make_client", lambda *a, **k: made.append(1) or Client())
    monkeypatch.setattr(app_api.catalog, "enrich", lambda lib, metas, **kw: {
        "ok": len(metas), "notfound": 0, "csfd_blocked": False})
    monkeypatch.setattr(app_api, "_auto_enrich", lambda: 0)     # samočinné doplňování metadat má vlastní test
    assert api("quick_sync") == {"new": 0, "skipped": "account"} and not made     # bez přihlášení se nic neděje
    api("settings_set", username="a@b.cz", password="heslo")
    with app_api._library() as lib:
        before = lib.count_videos()
    result = api("quick_sync")
    assert result == {"new": 1, "toast": "Nová videa z účtu: 1"}
    assert asked and asked[0] and len(asked[0]) == before        # klient dostal, co katalog už má
    with app_api._library() as lib:
        assert lib.count_videos() == before + 1                  # nové přibylo, nic se neodebralo
    assert api("quick_sync") == {"new": 0, "skipped": "recent"} and len(asked) == 1   # hned znovu se neptá
    assert api("progress") == {"text": "", "busy": False}

    def failing(*a, **k):
        raise app_api.provider.LoginError("špatné heslo")

    monkeypatch.setattr(app_api.provider, "make_client", failing)
    assert api("quick_sync", force=True) == {"new": 0, "skipped": "error"}        # potichu, bez chybové hlášky
    assert api("progress")["busy"] is False


def test_quick_sync_leaves_the_first_load_to_the_full_sync(api, monkeypatch):
    api("settings_set", username="a@b.cz", password="heslo")
    monkeypatch.setattr(app_api.provider, "make_client", lambda *a, **k: pytest.fail("prázdný katalog: nic se nestahuje"))
    assert api("quick_sync") == {"new": 0, "skipped": "empty"}


def _ordered_episodes(api):
    """Zkušební služba vrací díly vždy stejně; tady je seřadí podle toho, o co klient žádá."""
    genuine = api.service.graphql

    def graphql(name, variables):
        if name == "GetEpisodes" and variables["programmeId"] in ("pp", "pv"):
            items = list(api.service.episodes)                  # od nejstaršího (Díl 1) po nejnovější (Díl 50)
            if variables["orderBy"] == "newest":
                items.reverse()
            page = items[variables["offset"]:variables["offset"] + variables["limit"]]
            return {"data": {"programme": {"id": "x", "episodes": {"totalCount": len(items), "items": page}}}}
        return genuine(name, variables)

    api.service.graphql = graphql
    return [item["id"] for item in api.service.episodes]


def test_ivysilani_offers_the_next_episode_without_starting_it(api):
    idecs = _ordered_episodes(api)                               # pořad 13 „Na plovárně“ řadí od nejnovějšího
    stream = api("ivys_play", idec=idecs[5])                     # Díl 6
    api("progress_save", track=stream["track"], position=1320, total=1326, ended=True)
    following = api("play_next", track=stream["track"])["next"]
    assert following == {"title": "Další díl", "subtitle": "Na plovárně · 7/50 Díl 7", "auto": False,
                         "target": {"type": "play", "call": "ivys_play", "params": {"idec": idecs[6]}}}
    api("ivys_play", idec=idecs[49])                             # nejnovější díl: další už není
    assert api("play_next", track="ivys:" + idecs[49])["next"] is None
    api("ivys_play", idec=idecs[9])                              # Díl 10 je první na druhé stránce výpisu od nejnovějšího
    assert api("play_next", track="ivys:" + idecs[9])["next"]["target"]["params"] == {"idec": idecs[10]}
    api("ivys_play", idec="211522160100014")                     # díl, který ve výpisu pořadu není
    assert api("play_next", track="ivys:211522160100014")["next"] is None
    assert api("play_next", track="ivys:999")["next"] is None    # o dílu se nic neví

    # pořad řazený od nejstaršího (12 „Vyprávěj“): následuje díl za ním, i přes hranici stránky
    with app_api._library() as lib:
        for idec in (idecs[39], idecs[49]):
            lib.ivys_touch(idec, "12", "Díl", "Vyprávěj")
    assert api("play_next", track="ivys:" + idecs[39])["next"]["target"]["params"] == {"idec": idecs[40]}
    assert api("play_next", track="ivys:" + idecs[49])["next"] is None


def test_ivysilani_show_starts_with_where_to_continue(api):
    idecs = _ordered_episodes(api)
    assert not api("ivys_show", id="13")["items"][0]["title"].startswith("Pokračovat")     # nic se nepřehrávalo
    stream = api("ivys_play", idec=idecs[5])
    api("progress_save", track=stream["track"], position=400, total=1326)
    first = api("ivys_show", id="13")["items"][0]
    assert (first["subtitle"], first["progress"]) == ("Rozkoukaný díl", 30)
    assert first["target"] == {"type": "play", "call": "ivys_play", "params": {"idec": idecs[5]}}
    api("progress_save", track=stream["track"], position=1320, total=1326, ended=True)
    first = api("ivys_show", id="13")["items"][0]
    assert (first["title"], first["subtitle"]) == ("Pokračovat: 7/50 Díl 7", "První nezhlédnutý díl")
    assert first["target"]["params"] == {"idec": idecs[6]}
    assert not api("ivys_show", id="13", offset=40)["items"][0]["title"].startswith("Pokračovat")   # jen na začátku

    # zkratka z oblíbeného pořadu pustí tentýž díl rovnou
    api("ivys_favorite", id="13")
    with app_api._library() as lib:
        card = next(card for card in app_api.ivys_favorite_cards(lib) if card["title"] == "Na plovárně")
    # pořad, ze kterého už něco běželo: klepnutí pokračuje (díly jsou v nabídce karty)
    shortcut = card["target"]
    assert shortcut == {"type": "play", "call": "ivys_play", "params": {"show": "13", "following": True, "resume": True}}
    assert [item["title"] for item in card["menu"]] == ["Pokračovat ve sledování", "Vybrat díl", "Odebrat z oblíbených"]
    assert api("ivys_play", **shortcut["params"])["track"] == "ivys:" + idecs[6]
    assert "nic nedokoukali" in api("ivys_play", show="12", following=True)["error"]


def test_subtitle_style_belongs_to_the_profile(api):
    style = api("subtitle_style")
    assert (style["size"], style["background"], style["scale"]) == ("normal", "solid", 1.0)
    assert [item["id"] for item in style["sizes"]] == ["small", "normal", "large", "huge"]
    assert [item["id"] for item in style["backgrounds"]] == ["solid", "shade", "outline"]
    changed = api("subtitle_style_set", size="large", background="shade")
    assert (changed["size"], changed["background"], changed["scale"]) == ("large", "shade", 1.3)
    assert api("subtitle_style_set", size="obří", background="duha")["size"] == "large"    # neznámé hodnoty nic nezmění
    assert (style["color"], style["rgb"]) == ("white", "#FFFFFF")          # barva písma (Windows verze)
    assert [item["id"] for item in style["colors"]] == ["white", "yellow", "green", "cyan"]
    colored = api("subtitle_style_set", color="yellow")
    assert (colored["color"], colored["rgb"], colored["size"]) == ("yellow", "#FFE94D", "large")
    assert api("subtitle_style_set", color="duha")["color"] == "yellow"

    api("profile_add", avatar="owl", text="Děti")               # jiný profil má vlastní vzhled titulků
    main, other = api("profiles")["items"][:2]
    api(other["target"]["call"], **other["target"]["params"])
    assert (api("subtitle_style")["size"], api("subtitle_style")["background"]) == ("normal", "solid")
    assert api("subtitle_style")["color"] == "white"
    api("subtitle_style_set", size="huge")
    api(main["target"]["call"], **main["target"]["params"])
    assert (api("subtitle_style")["size"], api("subtitle_style")["background"]) == ("large", "shade")
    assert api("subtitle_style")["color"] == "yellow"
    api(other["target"]["call"], **other["target"]["params"])
    assert api("subtitle_style")["size"] == "huge"


def test_double_episode_counts_as_both_and_the_next_one_follows_it(api, monkeypatch):
    first, third = "21:archa:%016x" % 21, "23:archa:%016x" % 23
    with app_api._library() as lib:
        lib.upsert_video(first, "Archa.S01E01-E02.CZ", "account", None)        # dvojdíl v jednom souboru
        lib.upsert_video(third, "Archa.S01E03.CZ", "account", None)
    listed = api("show", key="show:archa")["items"]
    assert _titles(listed)[:2] == ["1×01–02", "1×03"]
    _catalogue_search(monkeypatch, {})
    following = api("play_next", track=first)["next"]                          # po dvojdílu rovnou třetí díl
    assert (following["title"], following["subtitle"], following["auto"]) == ("Další díl", "Archa 1×03", True)
    assert following["target"]["params"] == {"vid": third}


def test_version_search_goes_on_until_it_finds_the_preferred_language(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    english = _web(61, "Pelisky 1999 EN 1080p", 6600, "3 GB")
    dubbed = _web(62, "Cosy Dens 1999 CZ dabing 1080p", 6600, "3 GB")
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [english], "Cosy Dens 1999": [dubbed]})
    with app_api._library() as lib:
        state = app_api._state(lib)
    versions = app_api.app_discover._find_versions(["Pelíšky", "Cosy Dens"], 1999, None, state)
    # pod českým názvem je jen verze bez dabingu: hledá se dál pod původním a dabovaná je první
    assert [query for query, _page in asked] == ["Pelíšky 1999", "Pelíšky", "Cosy Dens 1999"]
    assert [version["video"]["id"] for version in versions] == [dubbed["id"], english["id"]]

    del asked[:]
    quick = app_api.app_discover._find_versions(["Pelíšky", "Cosy Dens"], 1999, None, state, thorough=False)
    assert [query for query, _page in asked] == ["Pelíšky 1999"] and len(quick) == 1   # ověření dostupnosti nehledá dál

    del asked[:]
    api("settings_set", prefer_language="any")
    with app_api._library() as lib:
        state = app_api._state(lib)
    assert len(app_api.app_discover._find_versions(["Pelíšky", "Cosy Dens"], 1999, None, state)) == 1
    assert [query for query, _page in asked] == ["Pelíšky 1999"]                # bez předvolby jazyka stačí první nález


def test_search_listing_filters_by_language_and_quality(api, monkeypatch):
    found = {"Pelíšky": [_web(71, "Pelisky 1999 EN 720p", 7200, "1 GB"), _web(72, "Pelisky.1999.CZ.1080p", 7200, "4 GB"),
                         _web(73, "Pelisky 1999 CZ tit 2160p", 7200), _web(74, "Pelisky 1999 CZ dabing", 7200)]}
    _catalogue_search(monkeypatch, found)
    listing = api("prehrajto_search", q="Pelíšky", listing=True)["items"]
    assert listing[0]["title"] == "Filtr" and listing[0]["subtitle"] == "Jazyk a kvalita" and len(listing) == 5
    assert listing[0]["target"] == {"type": "filter", "call": "search_filters", "params": {"listing": True, "q": "Pelíšky"}}
    filters = api("search_filters", **listing[0]["target"]["params"])
    assert [(group["title"], group["value"]) for group in filters["filters"]] == [("Jazyk", "Vše"), ("Kvalita", "Vše")]
    assert filters["reset"] is None
    dubbed = filters["filters"][0]["options"][1]["params"]
    assert dubbed == {"listing": True, "q": "Pelíšky", "lang": "dub"}
    only = api("prehrajto_search", **dubbed)["items"]
    assert only[0]["subtitle"] == "Český nebo slovenský dabing"
    assert [card["title"] for card in only[1:]] == ["Pelisky.1999.CZ.1080p", "Pelisky 1999 CZ dabing"]
    sharp = api("prehrajto_search", q="Pelíšky", listing=True, lang="dub", quality="1080")["items"]
    assert [card["title"] for card in sharp[1:]] == ["Pelisky.1999.CZ.1080p"]   # soubor bez uvedené kvality neprojde
    subs = api("prehrajto_search", q="Pelíšky", listing=True, lang="subs")["items"]
    assert [card["title"] for card in subs[1:]] == ["Pelisky 1999 CZ tit 2160p"]
    active = api("search_filters", q="Pelíšky", listing=True, lang="dub", quality="1080")
    assert active["reset"] == {"listing": True, "q": "Pelíšky"}
    assert len(api("prehrajto_search", q="Pelíšky")["items"]) == 4              # bez filtru a bez karty Filtr


def test_wrong_assignment_can_be_repaired_by_hand(api, monkeypatch):
    _fill(2)
    api("settings_set", tmdb_key="k" * 32)
    film = _section(api, "movies")["Nově přidané z účtu"][0]
    repair = next(item for item in film["menu"] if item["title"] == "Opravit přiřazení")["target"]
    assert repair == {"type": "list", "call": "repair", "params": {"vid": REF}, "title": "Opravit přiřazení"}

    class Tmdb(object):
        enabled = True

        def search(self, query, want_series=False):
            return [{"id": 11, "title": "Pelíšky", "year": 1999, "orig_title": "Pelíšky"},
                    {"id": 12, "title": query + " 2", "year": 2005, "orig_title": "Cosy Dens 2"}]

        def details(self, tmdb_id, want_series=False):
            return {"title": "Pelíšky 2", "orig_title": "Cosy Dens 2", "year": 2005, "tmdb_id": tmdb_id, "rating": 55,
                    "poster": "https://img/p2.jpg", "genres": ["Komedie"]}

    monkeypatch.setattr(app_api, "_tmdb", lambda: Tmdb())
    cards = api("repair", **repair["params"])["items"]
    assert _titles(cards) == ["Hledat pod jiným názvem", "Pelíšky (1999)", "Pelisky 2 (2005)", "Je to díl seriálu",
                              "Přepsat název ručně"]
    assert cards[0]["target"]["type"] == "input" and cards[0]["target"]["params"]["value"] == "Pelisky"
    assert cards[2]["subtitle"] == "Cosy Dens 2"

    # jiný název: výpis kandidátů se otevře znovu s novým dotazem a ten starý se zavře
    again = api("repair_query", key="movie:pelisky|1999", vid=REF, text="Cosy Dens")
    assert again["close"] is True and again["open"]["params"] == {"key": "movie:pelisky|1999", "vid": REF, "q": "Cosy Dens"}
    assert _titles(api("repair", **again["open"]["params"])["items"])[2] == "Cosy Dens 2 (2005)"
    assert "prázdný" in api("repair_query", key="movie:pelisky|1999", text=" ")["error"]

    # výběr kandidáta: titul dostane název, plakát a hodnocení a jeho soubory se zamknou
    applied = api(cards[2]["target"]["call"], **cards[2]["target"]["params"])
    assert applied == {"message": "", "toast": "Přiřazeno: Pelíšky 2", "close": True}
    with app_api._library() as lib:
        meta = lib.get_meta("movie:pelisky|1999")
        assert (meta["title"], meta["poster"], meta["status"]) == ("Pelíšky 2", "https://img/p2.jpg", "ok")
        assert lib.get_video(REF)["locked"] == 1
    assert "Vrátit automatické rozpoznání" in _titles(api("repair", vid=REF)["items"])

    # oprava souboru: díl, řada, film místo dílu, ruční název
    monkeypatch.setattr(app_api.catalog, "enrich", lambda lib, metas, **kw: {"ok": 0, "notfound": len(metas),
                                                                             "csfd_blocked": False})
    second = "12:archa:%016x" % 12
    episode = api("repair", vid=second)["items"]
    assert ["Změnit číslo dílu", "Změnit řadu", "Je to film, ne díl", "Přepsat název ručně"] == _titles(episode)[-4:]
    assert episode[-4]["subtitle"] == "Teď: 1×02" and episode[-1]["target"]["params"]["value"] == "Archa S01E02"
    assert api("repair_video", vid=second, field="episode", text="7") == {
        "message": "", "toast": "Opraveno: Archa 1×07", "close": True}
    assert api("repair_video", vid=second, field="season", text="3")["toast"] == "Opraveno: Archa 3×07"
    assert "číslo" in api("repair_video", vid=second, field="episode", text="sedm")["error"]
    assert api("repair_video", vid=second, field="movie")["toast"] == "Opraveno: Archa"
    assert api("repair_video", vid=second, field="episode_of", text="2x05")["toast"] == "Opraveno: Archa 2×05"
    assert api("repair_video", vid=second, field="name", text="Jiný seriál 1x09")["toast"] == "Opraveno: Jiný seriál 1×09"
    with app_api._library() as lib:
        fixed = lib.get_video(second)
        assert (fixed["meta_key"], fixed["season"], fixed["episode"], fixed["locked"]) == ("show:jiny serial", 1, 9, 1)
    assert api("repair_video", vid=second, field="auto")["toast"] == "Opraveno: Archa 1×02"
    with app_api._library() as lib:
        assert lib.get_video(second)["locked"] == 0 and lib.get_meta("show:jiny serial") is None

    # číslovaný soubor jako díl: číslo se z názvu seriálu vypustí
    with app_api._library() as lib:
        lib.upsert_video("40:ulice:%016x" % 40, "Ulice 4521", "account", None)
    assert api("repair_video", vid="40:ulice:%016x" % 40, field="episode_of", text="4521")["toast"] == "Opraveno: Ulice 1×4521"
    assert "není" in api("repair", vid="9:x:00000000000000ff")["error"]


def test_best_quality_prefers_4k_however_the_site_labels_it(api, monkeypatch):
    api("settings_set", prefer_language="any")
    full = _web(81, "Duna 2021 CZ", 9000, "9 GB", quality="1080p")
    worded = _web(82, "Duna 2021 CZ", 9000, "24 GB", quality="4K")            # slovní označení z výpisu
    named = _web(83, "Duna 2021 CZ 2160p", 9000, "22 GB", quality="")         # kvalita jen v názvu souboru
    odd = _web(84, "Duna 2021 CZ UHD", 9000, "20 GB", quality="Blu-ray")      # výpisu aplikace nerozumí: platí název
    _catalogue_search(monkeypatch, {"Duna 2021": [full, worded, named, odd]})
    with app_api._library() as lib:
        state = app_api._state(lib)
    versions = app_api.app_discover._find_versions(["Duna"], 2021, None, state)
    order = [version["video"]["id"] for version in versions]
    assert order[-1] == full["id"] and set(order[:3]) == {worded["id"], named["id"], odd["id"]}   # 4K před 1080p
    assert app_api.provider.quality_token("4K") == app_api.provider.quality_token("4k UHD") == "UHD"
    assert app_api.provider.quality_token("Full HD") == "FHD" and app_api.provider.quality_token("HD") == "HD"
    assert app_api.provider.quality_token("xvid") == ""

    api("settings_set", prefer_quality="1080")                                # s předvolbou 1080p je první 1080p
    with app_api._library() as lib:
        state = app_api._state(lib)
    assert app_api.app_discover._find_versions(["Duna"], 2021, None, state)[0]["video"]["id"] == full["id"]


def test_downloaded_files_move_to_another_disk(api, tmp_path):
    _fill()
    first, second = tmp_path / "usb1", tmp_path / "usb2"
    first.mkdir()
    second.mkdir()
    api("download_add", vid=REF)
    _link(REF, "https://cdn.x/pelisky.mkv?sig=1")
    job = api("download_next", dir=str(first))
    (first / os.path.basename(job["path"])).write_bytes(b"x" * 40)
    subtitle = first / "pelisky.cs.vtt"
    subtitle.write_text("WEBVTT")
    api("download_finish", vid=REF, size=40, subtitles=[str(subtitle)])

    listed = api("downloads")["items"]
    assert _titles(listed)[-2:] == ["Kam stahovat", "Přesunout vše na jiný disk"]
    assert listed[-1]["target"] == {"type": "move", "call": "download_move", "params": {"all": True}}
    menu = {item["title"]: item["target"] for item in listed[0]["menu"]}
    assert menu["Přesunout na jiný disk"] == {"type": "move", "call": "download_move", "params": {"vid": REF}}

    plan = api("download_move_plan", vid=REF)
    assert plan == {"count": 1, "bytes": 40, "dirs": [str(first)]}
    assert "není připojený" in api("download_move", vid=REF, dir=str(tmp_path / "nic"))["error"]
    assert api("download_move", vid=REF, dir=str(first)) == {"message": "Soubory už na tomto disku jsou."}
    assert api("move_next") == {"vid": ""}
    started = api("download_move", all=True, dir=str(second))
    assert started["start"] is True and "Přesouvám souborů: 1" in started["toast"]
    assert api("downloads_pending") == {"pending": True}           # po restartu služba přesun dokončí
    assert api("downloads")["items"][0]["subtitle"].startswith("Přesouvá se")
    assert api("play", vid=REF)["url"] == job["path"]              # během přesunu se hraje z původního místa

    move = api("move_next")
    assert (move["vid"], move["from"], move["size"]) == (REF, job["path"], 40)
    assert move["to"] == str(second / os.path.basename(job["path"])) and move["subtitles"] == [str(subtitle)]
    # aplikace soubory zkopíruje a ohlásí nové umístění; teprve pak se původní smažou
    (second / os.path.basename(job["path"])).write_bytes(b"x" * 40)
    moved_subtitle = second / subtitle.name
    moved_subtitle.write_text("WEBVTT")
    assert api("move_finish", vid=REF, path=move["to"], subtitles=[str(moved_subtitle)]) == {"saved": True}
    assert not os.path.exists(job["path"]) and not subtitle.exists() and moved_subtitle.exists()
    assert api("play", vid=REF)["url"] == move["to"] and api("move_next") == {"vid": ""}
    assert api("downloads_pending") == {"pending": False}

    # nepovedený přesun: soubor zůstává na místě a nic se neztratí
    api("download_move", vid=REF, dir=str(first))
    back = api("move_next")
    assert api("move_fail", vid=REF, error="Na disku není dost místa") == {"saved": True}
    assert api("play", vid=REF)["url"] == move["to"] and api("move_next") == {"vid": ""}
    assert not os.path.exists(back["to"])
    # hlášení dokončení bez souboru na cíli nic nepřepíše
    api("download_move", vid=REF, dir=str(first))
    assert api("move_finish", vid=REF, path=back["to"], subtitles=[]) == {"saved": False}
    assert api("play", vid=REF)["url"] == move["to"]
    # cílový disk mezitím zmizel: přesun se sám zruší
    api("download_move", vid=REF, dir=str(first))
    first.rmdir()
    assert api("move_next") == {"vid": ""} and api("downloads_pending") == {"pending": False}
    assert "Není co přesouvat" in api("download_move_plan", vid="9:x:00000000000000ff")["error"]


def test_changing_preferences_changes_the_offered_version_at_once(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    sharp = _web(61, "Pelisky 1999 CZ 1080p", 6600, "4 GB", quality="1080p")
    small = _web(62, "Pelisky 1999 CZ 720p", 6600, "1 GB", quality="720p")
    _catalogue_search(monkeypatch, {"Pelíšky 1999": [sharp, small]})

    def offered():
        with app_api._library() as lib:
            state = app_api._state(lib)
        return app_api.app_discover._find_versions(["Pelíšky"], 1999, None, state, choice="movie:11")[0]["video"]["id"]

    assert offered() == sharp["id"]                                # nejvyšší kvalita: 1080p
    with app_api._library() as lib:
        lib.set_kv(app_api.CHOICE_PREFIX + "movie:11", sharp["id"])   # uživatel si kdysi vybral verzi ručně
    api("settings_set", prefer_quality="720")
    assert offered() == small["id"]                                # změna předvolby platí hned i přes dřívější volbu
    with app_api._library() as lib:
        assert lib.get_kv(app_api.CHOICE_PREFIX + "movie:11") is None
        lib.set_kv(app_api.CHOICE_PREFIX + "movie:11", sharp["id"])
    api("settings_set", prefer_quality="720", tmdb_key="k" * 32)   # stejná předvolba: ruční volby zůstávají
    with app_api._library() as lib:
        assert lib.get_kv(app_api.CHOICE_PREFIX + "movie:11") == sharp["id"]


def test_watch_next_uses_the_sharp_wide_picture_of_the_title(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    detail = {"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08",
              "poster_path": "/p.jpg", "backdrop_path": "/b.jpg", "genres": []}
    calls = _fake_tmdb(monkeypatch, {"/movie/11": detail, "/movie/12": dict(detail, id=12, backdrop_path=None)})
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    wide, tall, plain = (_web(n, "Pelisky 1999 CZ verze %d" % n)["id"] for n in (91, 92, 93))
    for vid in (wide, tall, plain):
        result = {"id": vid, "title": "Pelisky 1999 CZ", "thumb": "https://thumb.prehraj/maly.jpg"}
        choice = {wide: "movie:11", tall: "movie:12", plain: ""}[vid]
        played = api("play", vid=vid, web_result=result, **({"choice": choice} if choice else {}))
        api("progress_save", track=played["track"], position=900, total=6000, started=True)
    items = {item["id"]: item for item in api("watch_next")["items"]}
    # video puštěné ze stránky titulu: obrázek titulu na šířku ve vysokém rozlišení, ne malý náhled souboru
    assert (items[wide]["image"], items[wide]["portrait"]) == (app_api.app_discover.IMG + "w1280/b.jpg", False)
    # titul bez obrázku na šířku: plakát, a ten se ukáže na výšku (neořezává se a nezvětšuje)
    assert (items[tall]["image"], items[tall]["portrait"]) == (app_api.app_discover.IMG + "w500/p.jpg", True)
    # video, o kterém se nic neví: zůstává náhled z Přehraj.to
    assert (items[plain]["image"], items[plain]["portrait"]) == ("https://thumb.prehraj/maly.jpg", False)
    assert {path for path, _params in calls} == {"/movie/11", "/movie/12"}

    # úvodní panel Rozkoukané bere stejný obrázek na šířku
    later = int(app_api.time.time()) + 60
    with app_api._library() as lib:
        lib.record_play(wide, 950, 6000, now=later)
    hero = {section["id"]: section for section in api("home")["sections"]}["progress"]["hero"]
    assert hero["image"] == app_api.app_discover.IMG + "w1280/b.jpg"
    with app_api._library() as lib:
        lib.record_play(plain, 950, 6000, now=later + 60)       # malý náhled se na velký panel nehodí
    assert {section["id"]: section for section in api("home")["sections"]}["progress"]["hero"]["image"] == ""

    # video puštěné ve starší verzi (bez zapamatovaného titulu) se dohledá podle zvolené verze
    with app_api._library() as lib:
        lib.set_kv(app_api.ART_PREFIX + wide, "")
    assert {item["id"]: item for item in api("watch_next")["items"]}[wide]["image"].endswith("w1280/b.jpg")


def test_only_full_hd_and_better_is_offered_when_asked(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32, prefer_language="any")
    hour = 3600
    found = [_web(71, "Duna 2021 CZ", 2 * hour, "9 GB", quality="1080p"),
             _web(72, "Duna 2021 CZ", 2 * hour, "3 GB", quality="720p"),
             _web(73, "Duna 2021 CZ 4K", 2 * hour, "22 GB"),
             _web(74, "Duna 2021 CZ 2K", 2 * hour, "12 GB"),
             _web(75, "Duna 2021 CZ dvdrip", 2 * hour, "1 GB", quality="SD"),
             _web(76, "Duna 2021 CZ", 2 * hour, "4 GB"),          # kvalitu neuvádí: 4 GB na dvě hodiny je Full HD
             _web(77, "Duna 2021 CZ", 2 * hour, "700 MB"),        # kvalitu neuvádí a je malý
             _web(78, "Duna 2021 CZ")]                            # neví se o něm nic
    _catalogue_search(monkeypatch, {"Duna 2021": found, "Duna": found})

    def offered():
        with app_api._library() as lib:
            state = app_api._state(lib)
        versions = app_api.app_discover._find_versions(["Duna"], 2021, None, state)
        return {int(version["video"]["id"].split(":")[0]) for version in versions}, state["below_minimum"]

    assert offered() == ({71, 72, 73, 74, 75, 76, 77, 78}, 0)                 # bez omezení všechno
    settings = api("settings_set", min_quality="1080")
    assert settings["min_quality"] == "1080"
    assert offered() == ({71, 73, 74, 76}, 4)                                # jen Full HD, 2K a 4K
    listed = api("prehrajto_search", q="Duna")["items"]
    assert {int(card["target"]["params"]["vid"].split(":")[0]) for card in listed} == {71, 73, 74, 76}
    choices = api("prefs_choices")["filters"]
    assert [group["title"] for group in choices] == ["Jazyk", "Kvalita", "Nejnižší kvalita"]
    assert choices[2]["value"] == "Jen Full HD a vyšší (1080p, 2K, 4K)"
    assert [(option["title"], option["params"]["min_quality"], option["current"]) for option in choices[2]["options"]] == [
        ("Bez omezení", "", False), ("HD a vyšší (720p, 1080p, 4K)", "720", False),
        ("Jen Full HD a vyšší (1080p, 2K, 4K)", "1080", True), ("Jen 4K", "2160", False)]
    api("settings_set", min_quality="720")                                   # HD a vyšší: vypadne jen SD a co je moc malé
    assert offered() == ({71, 72, 73, 74, 76}, 3)
    api("settings_set", min_quality="2160")
    assert offered() == ({73}, 7)
    api("settings_set", min_quality="1080")

    # varianta souboru k přehrání: úsporná předvolba nesmí sáhnout pod Full HD
    streams = [{"quality": "FHD"}, {"quality": "HD"}, {"quality": "SD"}]
    assert app_api._pick_stream(streams, {"quality": "small", "minimum": "1080"})["quality"] == "FHD"
    assert app_api._pick_stream(streams, {"quality": "small", "minimum": "720"})["quality"] == "HD"
    assert app_api._pick_stream(streams, {"quality": "small", "minimum": ""})["quality"] == "SD"

    # film, který je jen v nižší kvalitě: stránka titulu to řekne a poradí, kde omezení vypnout
    low = [_web(91, "Nizky 2001 CZ", 2 * hour, "3 GB", quality="720p"), _web(92, "Nizky 2001 CZ dvdrip", 2 * hour, quality="SD")]
    _catalogue_search(monkeypatch, {"Nízký 2001": low, "Nízký": low})
    _fake_tmdb(monkeypatch, {})
    page = api("discover_title", kind="movie", id=0, title="Nízký", year=2001)
    assert "jen v nižší kvalitě než Full HD (nalezeno: 2)" in page["detail"]["plot"]
    api("settings_set", min_quality="")
    _catalogue_search(monkeypatch, {"Duna 2021": found, "Duna": found})
    assert offered() == ({71, 72, 73, 74, 75, 76, 77, 78}, 0)
    assert app_api.provider.quality_token("2K") == app_api.provider.quality_token("1440p") == "FHD"
    assert app_api.provider.quality_token("FulHD") == "FHD"


def test_minimum_quality_is_on_by_default_and_keeps_what_was_verified(api, monkeypatch):
    monkeypatch.undo()                                                       # výchozí hodnota jako ve skutečné aplikaci
    discover, prefix = app_api.app_discover, app_api.app_discover.AVAILABLE_PREFIX
    monkeypatch.setattr(discover, "BACKGROUND", False)                       # (seznam se v testu na pozadí neplní)
    assert api("settings_get")["min_quality"] == "720"                       # HD a vyšší
    # „Jen Full HD“ uložené verzí 0.14.0 (tehdy výchozí) se bere jako nezvolené; nová volba platí
    app_api._change_settings(lambda settings: settings.update(min_quality="1080"))
    assert api("settings_get")["min_quality"] == "720"
    now = int(__import__("time").time())
    with app_api._library() as lib:
        lib.set_kv(prefix + "movie:11", json.dumps({"n": 2, "ts": now, "r": 2, "d": "cz", "q": 2160, "s": 0, "t": "Ve 4K"}))
        lib.set_kv(prefix + "movie:12", json.dumps({"n": 1, "ts": now, "r": 2, "d": "cz", "q": 720, "s": 0, "f": now}))
        lib.set_kv(prefix + "movie:13", json.dumps({"n": 0, "ts": now, "r": 2, "ttl": 86400}))
        lib.set_kv(prefix + "movie:14", json.dumps({"n": 0, "ts": now, "r": 2, "iv": "777", "ttl": 86400}))
        lib.set_kv(app_api.CHOICE_PREFIX + "movie:11", "x")
        app_api.app_index._save({"step": 7, "finished": now})

    def known():
        with app_api._library() as lib:
            return discover._known(lib, "movie", [11, 12, 13, 14])

    assert api("settings_set", min_quality="1080")["min_quality"] == "1080"  # přísněji
    assert api("settings_get")["min_quality"] == "1080"
    state = known()
    # Seznam ověřených titulů se nemaže: co novému požadavku podle známé kvality vyhovuje, zůstává a znovu
    # se neověřuje (stály by to stovky dotazů); co nestačí, se skryje, dokud nové ověření nerozhodne;
    # „není tam“ platí dál.
    assert state["movie:11"]["n"] == 2 and not state["movie:11"].get("stale")
    assert state["movie:12"]["n"] == 0 and state["movie:12"]["stale"]
    assert state["movie:13"]["n"] == 0 and not state["movie:13"].get("stale")
    assert state["movie:14"]["iv"] == "777"
    with app_api._library() as lib:
        assert json.loads(lib.get_kv(prefix + "movie:11"))["t"] == "Ve 4K"   # údaje pro kartu zůstaly
    index = app_api.app_index._state()
    assert index["step"] == 0 and "finished" not in index                    # plán se projde znovu od začátku
    # Titul skrytý nastavením se po novém ověření nepočítá jako „nově na Přehraj.to“.
    discover._remember_availability("movie", 12, 1, "", 2010, {"d": "cz", "q": 1080, "s": 0})
    with app_api._library() as lib:
        assert json.loads(lib.get_kv(prefix + "movie:12"))["f"] == now

    assert api("settings_set", min_quality="")["min_quality"] == ""          # vypnutí platí a pamatuje se
    state = known()
    assert state["movie:11"]["n"] == 2 and state["movie:12"]["n"] == 1       # nalezené zůstávají
    assert "movie:13" not in state                                           # nenalezený může teď vyhovět: neověřený
    assert state["movie:14"]["iv"] == "777"                                  # co je v iVysílání, se neztratí
    with app_api._library() as lib:
        assert lib.get_kv(app_api.CHOICE_PREFIX + "movie:11") is None        # ruční volby verzí se zapomenou
    assert api("settings_get")["min_quality"] == ""


def test_first_run_offers_the_guide_and_poster_lists_announce_their_shape(api):
    assert api("home")["welcome"] is True
    api("settings_set", onboarded=True)
    assert api("home")["welcome"] is False
    tiles = _section(api, "movies")
    assert tiles["Objevuj filmy"][0]["target"]["poster"] is True              # výpis plakátů
    grid = [card for row in api("discover_home", kind="movie")["rows"] if row.get("tiles") for card in row["items"]]
    assert next(card for card in grid if card["title"] == "Podle roku")["target"].get("poster") is None   # dlaždice
    assert "poster" not in _tool(api, "Nastavení")["target"]


def test_discover_lists_can_be_filtered(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    film = {"id": 11, "title": "Pelíšky", "release_date": "1999-04-08", "vote_average": 8.6}
    calls = _fake_tmdb(monkeypatch, {
        "/movie/top_rated": {"results": [film], "total_pages": 1},
        "/discover/movie": {"results": [film], "total_pages": 1},
        "/genre/movie/list": {"genres": [{"id": 35, "name": "Komedie"}, {"id": 18, "name": "Drama"}]},
    })
    top = api("discover", kind="movie", category="top_rated")
    card = top["items"][0]
    assert (card["title"], card["subtitle"], card["target"]["call"]) == ("Seřadit", "Podle hodnocení", "discover_sorts")
    assert top["count"] == "0 titulů"                                        # počet titulů do nadpisu výpisu
    sorts = api(card["target"]["call"], **card["target"]["params"])
    assert [(option["title"], option["current"]) for option in sorts["filters"][0]["options"]] == [
        ("Podle hodnocení", True), ("Podle roku (nejnovější)", False), ("Podle oblíbenosti", False)]
    assert sorts["filters"][0]["options"][1]["params"] == {"kind": "movie", "category": "top_rated", "sort": "new"}
    # filtry samotné zůstávají v logice (Windows verze i starší odkazy), jen je výpis nenabízí
    offered = api("discover_filters", **card["target"]["params"])
    assert [(group["title"], group["value"]) for group in offered["filters"]] == [
        ("Žánr", "Vše"), ("Období", "Vše"), ("Hodnocení", "Vše"), ("Země", "Vše"), ("Řazení", "Nejlépe hodnocené"),
        ("Jazyk", "Vše"), ("Dostupnost", "Jen na Přehraj.to a v iVysílání")]
    assert offered["reset"] is None                                          # nic není nastaveno
    dubbed = offered["filters"][5]["options"][1]                             # jen tituly s dabingem
    assert dubbed["title"] == "Jen s českým nebo slovenským dabingem" and dubbed["params"]["dub"] == "1"
    everything = offered["filters"][6]["options"][1]                         # přepnutí na všechny tituly
    assert everything["title"] == "Všechny tituly" and everything["params"] == {
        "kind": "movie", "category": "top_rated", "avail": "all"}
    assert api("discover_filters", **everything["params"])["reset"] == {"kind": "movie", "category": "top_rated"}
    genre = next(option for option in offered["filters"][0]["options"] if option["title"] == "Komedie")
    assert genre["params"] == {"kind": "movie", "category": "top_rated", "genre": "35"} and genre["current"] is False

    api("discover", **genre["params"])                                       # žánr + výchozí řazení kategorie
    path, sent = calls[-1]
    assert path == "/discover/movie" and sent["with_genres"] == "35"
    assert (sent["sort_by"], sent["vote_count.gte"]) == ("vote_average.desc", 200)

    chosen = dict(genre["params"], year="1990s", rating="70", origin="CZ|SK|XC", sort="new")
    listing = api("discover", **chosen)
    assert (listing["items"][0]["subtitle"], listing["count"]) == ("Podle roku (nejnovější)", "0 titulů")
    path, sent = calls[-1]
    assert (sent["primary_release_date.gte"], sent["primary_release_date.lte"]) == ("1990-01-01", "1999-12-31")
    # TMDB dostane výběr ve výchozím pořadí výpisu (tady nejlépe hodnocené); podle roku se řadí až v aplikaci
    assert (sent["vote_average.gte"], sent["sort_by"]) == (7.0, "vote_average.desc")
    assert sent["with_origin_country"] == "CZ|SK|XC"
    count = len(calls)
    api("discover", kind="movie", origin="CZ|SK|XC", sort="rating")       # Československé → Všechny → podle hodnocení
    orders = [sent.get("sort_by") for path, sent in calls[count:] if path == "/discover/movie"]
    assert orders[0] == "popularity.desc"                                    # tytéž tituly jako bez řazení (podle oblíbenosti)
    assert "primary_release_date.desc" in orders                             # … a k nim i nejnovější
    again = api("discover_filters", **chosen)
    assert [group["value"] for group in again["filters"]] == [
        "Komedie", "1990–1999", "70 % a víc", "Česko a Slovensko", "Nejnovější", "Vše",
        "Jen na Přehraj.to a v iVysílání"]
    assert again["reset"] == {"kind": "movie", "category": "top_rated"}      # zrušení vrátí původní výpis
    whole = next(option for option in again["filters"][0]["options"] if option["title"] == "Vše")
    assert "genre" not in whole["params"] and whole["params"]["year"] == "1990s"

    monkeypatch.setattr(app_api.Tmdb, "_fetch", lambda self, path, timeout, params: {"results": [], "total_pages": 0})
    empty = api("discover", kind="movie", genre="99", rating="80")           # prázdný výběr: filtr zůstává po ruce
    assert _titles(empty["items"]) == ["Seřadit"] and empty["next"] is None


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
    found = {"Kolja 1996": [_web(40, "Kolja.1996.CZ.1080p", 6000)],
             "Seriál": [_web(41, "Serial S01E01 CZ", 2700)]}
    asked = _catalogue_search(monkeypatch, found)
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "")
    _fake_tmdb(monkeypatch, {
        "/search/multi": {"results": [{"id": 90, "media_type": "movie", "title": "Svěrák: dokument",
                                       "release_date": "2015-01-01"}], "total_pages": 1},
        "/search/person": {"results": [
            {"id": 7, "name": "Petr Novák", "popularity": 30, "profile_path": "/n.jpg"},      # jméno výrazu neodpovídá
            {"id": 6, "name": "Zdeněk Svěrák", "known_for_department": "Acting", "popularity": 9,
             "known_for": [{"title": "Vesničko má středisková"}, {"title": "Kolja"}, {"title": "Obecná škola"}]},
            {"id": 5, "name": "Jan Svěrák", "known_for_department": "Directing", "profile_path": "/s.jpg",
             "popularity": 4, "known_for": [{"title": "Kolja"}, {"name": "Seriál"}]},
            {"id": 8, "name": "Jan Sverak ml.", "popularity": 0.1},                           # nic o něm: vynechá se
            {"name": "bez čísla"}]},
        "/person/5/combined_credits": {
            "cast": [{"media_type": "movie", "id": 21, "title": "Kuky se vrací", "release_date": "2010-05-20",
                      "character": "vypravěč", "vote_count": 40},
                     {"media_type": "movie", "id": 20, "title": "Kolja", "release_date": "1996-05-15", "character": "host"},
                     {"media_type": "tv", "id": 31, "name": "Talk show", "first_air_date": "2005-01-01",
                      "character": "Self", "genre_ids": [10767], "vote_count": 900},              # sám sebe v talk show
                     {"media_type": "movie", "id": 23, "title": "Dokument o filmu", "release_date": "2000-01-01",
                      "character": "Self (archive footage)", "vote_count": 500},
                     {"media_type": "tv", "id": 32, "name": "Cizí seriál", "first_air_date": "2003-01-01",
                      "character": "host", "episode_count": 1, "vote_count": 800},              # jeden díl
                     {"media_type": "movie", "id": 24, "title": "Chystaný film", "character": "role", "vote_count": 0}],
            "crew": [{"media_type": "movie", "id": 20, "title": "Kolja", "release_date": "1996-05-15", "job": "Director",
                      "vote_average": 7.7, "vote_count": 300},
                     {"media_type": "tv", "id": 30, "name": "Seriál", "first_air_date": "2001-01-01", "job": "Director",
                      "vote_count": 20},
                     {"media_type": "movie", "id": 25, "title": "Film z budoucnosti", "release_date": "2099-01-01",
                      "job": "Director", "vote_count": 999},
                     {"media_type": "movie", "id": 22, "title": "Jiný film", "job": "Producer"}]},
    })
    rows = api("search", q="sverak")["rows"]                                 # i bez diakritiky; jen kdo jménu odpovídá
    assert [row["title"] for row in rows][:2] == ["Filmy", "Herci a režiséři"]
    actor, director = rows[1]["items"]                                       # od známějšího
    assert (actor["title"], actor["subtitle"], actor["image"], actor["wide"]) == (
        "Zdeněk Svěrák", "herectví · Vesničko má středisková, Kolja", "", True)
    assert (director["title"], director["subtitle"], director["image"]) == (
        "Jan Svěrák", "režie · Kolja, Seriál", "https://image.tmdb.org/t/p/h632/s.jpg")
    assert director["target"] == {"type": "list", "call": "discover_person", "title": "Jan Svěrák", "poster": True,
                                  "params": {"id": 5, "name": "Jan Svěrák", "dept": "Directing"}}

    exact = api("search", q="Jan Svěrák")["rows"]                            # přesné jméno: osoba a její tituly napřed
    assert [row["title"] for row in exact][:3] == ["Herci a režiséři", "Jan Svěrák – nejznámější tituly", "Filmy"]
    assert _titles(exact[0]["items"]) == ["Jan Svěrák"]
    assert _titles(exact[1]["items"]) == ["Kolja", "Seriál", "Kuky se vrací", "Zobrazit vše"]
    assert exact[1]["items"][0]["subtitle"] == "1996 · 77 % · film · režie"
    assert exact[1]["items"][-1]["target"]["call"] == "discover_person"

    films = api("discover_person", **director["target"]["params"])           # filmografie: jen co je k mání
    assert films["title"] == "Jan Svěrák" and films["next"] is None and "refresh" not in films
    assert [(card["title"], card["subtitle"]) for card in films["items"]] == [
        ("Celá filmografie", "Teď: jen na Přehraj.to a v iVysílání"),
        ("Kolja", "1996 · 77 % · film · režie"), ("Seriál", "2001 · seriál · režie")]
    assert films["items"][1]["badges"] == ["Přehraj.to", "CZ"] and films["items"][2]["target"]["params"]["kind"] == "tv"
    assert ("Kuky se vrací 2010", 1) in asked and not any("Talk show" in query for query, _page in asked)
    everything = api("discover_person", **films["items"][0]["target"]["params"])
    assert [(card["title"], card["subtitle"]) for card in everything["items"]] == [   # režie první, od nejznámějšího;
        ("Jen co je k mání", "Teď: všechny tituly"),                                    # bez talk show, archivu,
        ("Kolja", "1996 · 77 % · film · režie"), ("Seriál", "2001 · seriál · režie"),   # hostování a neuvedených
        ("Kuky se vrací", "2010 · film · vypravěč")]
    assert everything["items"][3]["badges"] == ["Nenalezeno"]
    assert "Chybí osoba" in api("discover_person")["error"]

    discover = app_api.app_discover
    assert discover._name_matches("Tom Hanks", "tom han") and discover._name_matches("Zdeněk Svěrák", "SVERAK")
    assert not discover._name_matches("Avatar Singh", "avatar 2") and not discover._name_matches("Tom Hanks", "")
    # herec: role první, režie až za nimi
    acting = {"cast": [{"media_type": "movie", "id": 1, "title": "Role", "release_date": "2001-01-01", "character": "Jan",
                        "vote_count": 5}],
              "crew": [{"media_type": "movie", "id": 2, "title": "Režie", "release_date": "2002-01-01", "job": "Director",
                        "vote_count": 500}]}
    _fake_tmdb(monkeypatch, {"/person/9/combined_credits": acting})
    assert [item["title"] for _role, item in discover._person_credits(9, "Acting")] == ["Role", "Režie"]
    assert [item["title"] for _role, item in discover._person_credits(9, "Directing")] == ["Režie", "Role"]


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
        ("Jazyk", "Český nebo slovenský dabing"), ("Kvalita", "Nejvyšší dostupná"), ("Nejnižší kvalita", "Bez omezení")]
    subtitles = offered["filters"][0]["options"][1]
    assert subtitles == {"title": "Originál s titulky", "current": False, "params": {"prefer_language": "subs"}}
    api(offered["apply"], **subtitles["params"])
    assert api("prefs_choices")["filters"][0]["value"] == "Originál s titulky"
    assert "Originál s titulky" in _tool(api, "Jazyk a kvalita")["subtitle"]


FILM2 = {"kind": "movie", "id": 12, "title": "Kolja", "year": 1996, "poster": "", "orig": "Kolja"}
FILM2_DETAIL = dict(FILM_DETAIL, id=12, title="Kolja", original_title="Kolja", release_date="1996-05-15")


def test_language_and_quality_can_be_set_for_one_title_only(api, monkeypatch):
    """Volba jazyka a kvality na stránce titulu platí jen pro ten titul. Dřív otevírala nastavení celé
    aplikace: kdo si u jednoho filmu vybral titulky, změnil pořadí verzí všude."""
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL, "/movie/12": FILM2_DETAIL})
    hour = 3600
    _catalogue_search(monkeypatch, {
        "Pelíšky 1999": [_web(31, "Pelisky 1999 EN 720p", 2 * hour, "1 GB"), _web(32, "Pelisky.1999.CZ.1080p", 2 * hour, "4 GB"),
                         _web(38, "Pelisky 1999 CZ tit 2160p", 2 * hour, "9 GB")],
        "Kolja 1996": [_web(51, "Kolja 1996 EN 720p", 2 * hour, "1 GB"), _web(52, "Kolja.1996.CZ.1080p", 2 * hour, "4 GB"),
                       _web(58, "Kolja 1996 CZ tit 2160p", 2 * hour, "9 GB")]})
    before = api("settings_get")
    page = api("discover_title", **FILM)
    more = page["actions"][-1]
    assert (more["title"], more["subtitle"]) == ("Další", "")
    offered = api(more["menu"][1]["target"]["call"], **more["menu"][1]["target"]["params"])
    assert offered["title"] == "Jazyk a kvalita jen pro tento film" and offered["apply"] == "title_prefs_set"
    # dokud u titulu nic zvoleno není, řídí se nastavením aplikace – a nabídka říká jakým
    assert [(group["title"], group["value"]) for group in offered["filters"]] == [
        ("Jazyk", "podle aplikace (Český nebo slovenský dabing)"), ("Kvalita", "podle aplikace (Nejvyšší dostupná)"),
        ("Nejnižší kvalita", "podle aplikace (Bez omezení)")]
    language = offered["filters"][0]["options"]
    assert [(option["title"], option["current"]) for option in language] == [
        ("Podle nastavení aplikace (Český nebo slovenský dabing)", True), ("Český nebo slovenský dabing", False),
        ("Originál s titulky", False), ("Je mi to jedno", False)]
    assert language[2]["params"] == {"kind": "movie", "id": 11, "language": "subs"}

    api(offered["apply"], **language[2]["params"])                           # u Pelíšků originál s titulky
    assert api("settings_get") == before                                     # nastavení aplikace se nezměnilo
    assert api("prefs_choices")["filters"][0]["value"] == "Český nebo slovenský dabing"
    changed = api("discover_title", **FILM)
    assert _titles(changed["rows"][0]["items"]) == ["CZ titulky · 2160p", "EN · 720p", "CZ dabing · 1080p"]
    assert changed["actions"][0]["subtitle"] == "CZ titulky · 2160p"
    # stránka dává najevo, že titul má vlastní nastavení
    assert changed["actions"][-1]["subtitle"] == "vlastní nastavení"
    assert changed["actions"][-1]["menu"][1]["title"] == "Jazyk a kvalita u tohoto filmu (vlastní)"
    again = api("title_prefs_choices", kind="movie", id=11)["filters"][0]
    assert again["value"] == "Originál s titulky" and [option["current"] for option in again["options"]] == [
        False, False, True, False]
    # jiný film se řídí dál nastavením aplikace
    other = api("discover_title", **FILM2)
    assert _titles(other["rows"][0]["items"]) == ["CZ dabing · 1080p", "CZ titulky · 2160p", "EN · 720p"]
    assert other["actions"][-1]["subtitle"] == "" and other["actions"][0]["subtitle"] == "CZ dabing · 1080p"
    # i přímé přehrání filmu (bez otevření stránky) bere verzi podle jeho nastavení
    played = []
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: (played.append(vid), ("https://cdn.x/v.mp4", {}))[1])
    api("discover_movie_play", **FILM)
    api("discover_movie_play", **FILM2)
    assert [int(vid.split(":")[0]) for vid in played] == [38, 52]

    # nastavení celé aplikace dál platí pro všechno, co vlastní volbu nemá
    api("settings_set", prefer_language="any", prefer_quality="small")
    assert _titles(api("discover_title", **FILM2)["rows"][0]["items"])[0] == "EN · 720p"
    # u Pelíšků platí jejich jazyk (titulky) a z aplikace jen to, co vlastního nemají (úsporná kvalita)
    assert _titles(api("discover_title", **FILM)["rows"][0]["items"])[0] == "CZ titulky · 2160p"
    api("settings_set", prefer_language="dub", prefer_quality="best")

    api("title_prefs_set", kind="movie", id=11, language="app")              # zpět k nastavení aplikace
    back = api("discover_title", **FILM)
    assert _titles(back["rows"][0]["items"])[0] == "CZ dabing · 1080p" and back["actions"][-1]["subtitle"] == ""
    with app_api._library() as lib:
        assert lib.get_kv(app_api.TITLE_PREFS_PREFIX + "movie:11") is None   # nic po něm nezůstane
    assert "tprefs:".startswith(app_api.app_extras.KV_PREFIXES)              # volba patří profilu, jako ostatní předvolby
    with pytest.raises(app_api.ApiError):
        app_api.title_prefs_choices({"kind": "movie", "id": 0})              # titul bez čísla TMDB vlastní volbu nemá


def test_title_quality_decides_which_file_variant_plays(api, monkeypatch):
    """Kvalita zvolená jen pro titul rozhoduje i o tom, která varianta souboru se pustí a stáhne."""
    api("settings_set", tmdb_key="k" * 32, username="u@example.com", password="x")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL, "/movie/12": FILM2_DETAIL})
    hour = 3600
    _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(41, "Pelisky 1999 CZ 1080p", 2 * hour, "6 GB")],
                                    "Kolja 1996": [_web(61, "Kolja 1996 CZ 1080p", 2 * hour, "6 GB")]})
    _stream_pages(monkeypatch, {41: [1080, 720], 61: [1080, 720]})
    api("title_prefs_set", kind="movie", id=11, quality="720")
    assert api("discover_movie_play", **FILM)["url"] == "https://cdn.x/720.mp4"
    assert api("discover_movie_play", **FILM2)["url"].startswith("orig:")    # jinde platí „nejvyšší dostupná“
    # pokračování z Rozkoukaných (bez odkazu na titul) pustí tutéž variantu
    vid = api("discover_title", **FILM)["actions"][0]["target"]["params"]["vid"]
    assert api("play", vid=vid)["url"] == "https://cdn.x/720.mp4"
    api("title_prefs_set", kind="movie", id=11, quality="app")
    assert api("discover_movie_play", **FILM)["url"].startswith("orig:")     # adresa se po změně vybírá znovu


def test_title_minimum_quality_does_not_touch_the_list_of_available_titles(api, monkeypatch):
    """Nižší kvalita povolená jen u jednoho filmu ho ukáže na jeho stránce, ale seznam dostupných
    titulů (ten se řídí nastavením aplikace) se kvůli tomu nemění ani neověřuje znovu."""
    api("settings_set", tmdb_key="k" * 32, min_quality="1080")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    low = [_web(91, "Pelisky 1999 CZ", 2 * hour, "3 GB", quality="720p")]
    _catalogue_search(monkeypatch, {"Pelíšky 1999": low, "Pelíšky": low, "Pelisky 1999": low, "Pelisky": low})
    page = api("discover_title", **FILM)
    assert "jen v nižší kvalitě než Full HD (nalezeno: 1)" in page["detail"]["plot"]
    assert "Další → Jazyk a kvalita u tohoto filmu → Nejnižší kvalita" in page["detail"]["plot"]
    assert _record("movie:11")["n"] == 0
    restarted = []
    monkeypatch.setattr(app_api.app_index, "restart", lambda: restarted.append(1))
    api("title_prefs_set", kind="movie", id=11, minimum="")                  # u tohoto filmu bez omezení
    allowed = api("discover_title", **FILM)
    assert _titles(allowed["rows"][0]["items"]) == ["CZ dabing · 720p"] and allowed["actions"][0]["title"] == "Přehrát"
    assert _record("movie:11")["n"] == 0 and restarted == []                 # seznam dostupných se nemění
    assert app_api._prefs()["minimum"] == "1080"
    lowest = api("title_prefs_choices", kind="movie", id=11)["filters"][2]
    assert lowest["value"] == "Bez omezení" and lowest["options"][0]["title"] == "Podle nastavení aplikace (Jen Full HD a vyšší)"


def test_series_keeps_its_own_language_for_every_episode(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {})
    found = [_web(71, "Simpsonovi S01E02 CZ dabing 720p", 1300, "300 MB"),
             _web(72, "Simpsonovi S01E02 CZ titulky 1080p", 1300, "900 MB")]
    _catalogue_search(monkeypatch, {"Simpsonovi S01E02": found})
    show = {"id": 456, "title": "Simpsonovi", "season": 1, "episode": 2}
    assert _titles(api("discover_episode", **show)["items"]) == ["CZ dabing · 720p", "CZ titulky · 1080p"]
    offered = api("title_prefs_choices", kind="tv", id=456)
    assert offered["title"] == "Jazyk a kvalita jen pro tento seriál"
    api("title_prefs_set", kind="tv", id=456, language="subs")
    assert _titles(api("discover_episode", **show)["items"]) == ["CZ titulky · 1080p", "CZ dabing · 720p"]
    played = []
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: (played.append(vid), ("https://cdn.x/v.mp4", {}))[1])
    api("discover_episode_play", **show)
    assert played[-1].startswith("72:")
    # zapamatovaná verze dílu (nese stav zhlédnutí) změnou nastavení seriálu nemizí
    with app_api._library() as lib:
        assert lib.get_kv("choice:tv:456:1:2").startswith("72:")
    api("title_prefs_set", kind="tv", id=456, language="dub")
    with app_api._library() as lib:
        assert lib.get_kv("choice:tv:456:1:2").startswith("72:")
        assert app_api._title_own(lib, "tv:456:1:2") == {"language": "dub"}  # díl se řídí nastavením seriálu


def test_favourite_film_is_watched_for_the_version_its_own_settings_ask_for(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = [_web(38, "Pelisky 1999 CZ tit 1080p", 7200, "9 GB")]
    _catalogue_search(monkeypatch, {"Pelíšky 1999": found})
    page = api("discover_title", **FILM)
    favourite = next(action for action in page["actions"] if action["title"] == "Do oblíbených")
    api(favourite["target"]["call"], **favourite["target"]["params"])
    api("favorites_check", force=True)
    entry = next(iter(app_api.app_discover.load_favorites().values()))
    assert (entry["match"], entry["match_prefs"]) == (0, "dub|best")          # dabing tu není: hlídá se dál
    api("title_prefs_set", kind="movie", id=11, language="subs")             # u tohoto filmu stačí titulky
    assert api("favorites_check", force=True) == {"found": []}               # jiné zadání není „novinka“
    entry = next(iter(app_api.app_discover.load_favorites().values()))
    assert (entry["match"], entry["match_prefs"]) == (1, "subs|best")


def test_search_marks_titles_that_are_on_prehrajto_and_puts_them_first(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/search/multi": {"results": [
        {"id": 1, "media_type": "movie", "title": "Vesničko má středisková", "release_date": "1985-08-01"},
        {"id": 2, "media_type": "movie", "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08"},
        {"id": 3, "media_type": "movie", "title": "Pelíšky", "release_date": "2010-01-01"},      # jiný film stejného jména
        {"id": 7, "media_type": "tv", "name": "Bez dílů", "first_air_date": "2000-01-01"},
        {"id": 8, "media_type": "tv", "name": "Archa", "first_air_date": "1996-01-01"}], "total_pages": 1}})
    _catalogue_search(monkeypatch, {"pelisky": [_web(91, "Pelisky.1999.CZ.1080p", 7000, "4 GB"),
                                                _web(92, "Pelisky 1999 EN", 7000, "1 GB"),
                                                _web(93, "Archa S01E02 CZ"), _web(94, "Neco jineho 2001")]})
    result = api("search", q="pelisky")
    rows = {row["title"]: row["items"] for row in result["rows"]}
    assert [row["title"] for row in result["rows"]][:3] == ["Filmy", "Seriály", "Videa na Přehraj.to"]   # tituly první
    films = rows["Filmy"]
    assert [(card["title"], card["subtitle"][:4]) for card in films] == [
        ("Pelíšky", "1999"), ("Vesničko má středisková", "1985"), ("Pelíšky", "2010")]   # dostupný první
    assert films[0]["badges"] == ["Přehraj.to"] and "badges" not in films[1] and "badges" not in films[2]
    assert _titles(rows["Seriály"]) == ["Archa", "Bez dílů"] and rows["Seriály"][0]["badges"] == ["Přehraj.to"]
    assert "results" not in api("prehrajto_search", q="pelisky")            # jen pro hledání, ne pro výpis


def test_downloaded_films_are_listed_with_their_posters(api, monkeypatch, tmp_path):
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="heslo")
    calls = _fake_tmdb(monkeypatch, {
        "/movie/11": FILM_DETAIL,
        "/search/movie": {"results": [{"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]},
        "/movie/12": {"id": 12, "title": "Kolja", "release_date": "1996-05-15", "poster_path": "/k.jpg"}})
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    first, second, third = (_web(71, "Pelisky 1999 CZ 1080p", 7000, "4 GB"), _web(72, "Kolja 1996 CZ", 6000, "2 GB"),
                            _web(73, "Uplne nezname video 2001", 6000, "1 GB"))
    api("play", vid=first["id"], web_result=first, choice="movie:11")       # film spuštěný ze stránky titulu
    for result in (first, second, third):
        api("download_add", vid=result["id"], web_result=result)
    cards = {card["title"]: card for card in api("downloads", kind="movie")["items"]}
    assert cards["Pelisky"]["image"] == "https://image.tmdb.org/t/p/w500/p.jpg" and cards["Pelisky"]["wide"] is False
    assert cards["Kolja"]["image"] == "https://image.tmdb.org/t/p/w500/k.jpg"   # dohledaný podle názvu a roku
    unknown = cards["Uplne nezname video"]
    assert unknown["image"] == "" and unknown["wide"] is True and unknown["icon"] == "download"   # bez plakátu dlaždice
    before = len(calls)
    api("downloads", kind="movie")                                           # podruhé se už nic nedohledává
    assert len(calls) == before
    assert app_api._downloads()[second["id"]]["poster"].endswith("/k.jpg")


def test_unfinished_videos_count_after_15_seconds_and_mix_both_sources(api, monkeypatch):
    _fill(2)
    episode = "11:archa:%016x" % 11
    api("progress_save", track=REF, position=10, total=6000, started=True)   # krátké puštění: jen do historie
    assert list(_section(api, "progress")) == ["Rozkoukané", "Historie"]
    api("progress_save", track=REF, position=65, total=12000)                # po 15 s už je rozkoukané
    api("progress_save", track=episode, position=90, total=1200, started=True)
    rows = _section(api, "progress")
    assert _titles(rows["Filmy"]) == ["Pelíšky"] and _titles(rows["Seriály"]) == ["Archa 1x01"]
    assert rows["Filmy"][0]["progress"] == 1                                 # i půl procenta je na kartě vidět
    api("settings_set", username="a@b.cz", password="heslo")
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/v.mp4", {}))
    assert api("play", vid=REF)["position"] == 65                            # a přehrávání naváže, kde skončilo
    assert api("home")["sections"][1]["hero"]["title"] == "Archa 1×01"
    assert [item["position"] for item in api("watch_next")["items"]] == [90, 65]
    with app_api._library() as lib:
        lib.ivys_touch("777", "9", "Díl 3", "Vyprávěj", "")
        lib.ivys_record_play("777", 300, 3000, now=4102444800)               # přehráno jako poslední
        lib.ivys_touch("888", "10", "Krakonošovo tajemství", "Krakonošovo tajemství", "")
        # výslovně dřív než Pelíšky: se stejným časem by pořadí záleželo na tom, jestli mezitím přeskočila vteřina
        lib.ivys_record_play("888", 300, 3000, now=1000)
    rows = _section(api, "progress")                                         # iVysílání ve stejných řadách
    assert _titles(rows["Seriály"]) == ["Vyprávěj · Díl 3", "Archa 1x01"]
    assert _titles(rows["Filmy"]) == ["Pelíšky", "Krakonošovo tajemství"]    # pořad bez dílů je film
    assert rows["Filmy"][1]["subtitle"] == "iVysílání" and "iVysílání" not in rows


def test_favourites_and_start_section_cover_both_sources(api, monkeypatch):
    _fill(2)
    assert api("home")["start"] == 2                                         # nic oblíbeného: začíná se v Objevuj
    film = api("movies")["items"][1]
    toggle = next(item for item in film["menu"] if item["title"] == "Přidat do oblíbených")
    api(toggle["target"]["call"], **toggle["target"]["params"])
    api("ivys_favorite", id="12")                                            # pořad s díly z iVysílání
    with app_api._library() as lib:
        lib.ivys_favorite_add("11", "Krakonošovo tajemství", "https://img/k.jpg", "https://img/kp.jpg")
        app_api._ivys_remember_kind(lib, "11", "movie")                      # film z iVysílání
    assert api("home")["start"] == 0                                         # Oblíbené už mají obsah
    rows = _section(api, "favorites")
    assert list(rows) == ["Filmy", "Seriály"]                                # obě služby ve stejných řadách
    assert _titles(rows["Filmy"]) == ["Pelíšky", "Krakonošovo tajemství"]
    television = rows["Filmy"][1]
    assert television["subtitle"] == "iVysílání" and television["image"] == "https://img/kp.jpg"
    assert television["wide"] is False                                       # plakát, jako ostatní filmy v řadě
    assert television["target"] == {"type": "play", "call": "ivys_play", "params": {"show": "11"}}
    assert _titles(television["menu"]) == ["Přehrát", "Odebrat z oblíbených"]
    programme = rows["Seriály"][0]
    assert programme["target"]["call"] == "ivys_show" and programme["target"]["params"] == {"id": "12"}
    assert _titles(api("favorites", kind="movie")["items"]) == ["Pelíšky", "Krakonošovo tajemství"]
    assert len(api("favorites", kind="tv")["items"]) == 1 and len(api("favorites")["items"]) == 3
    remove = television["menu"][1]["target"]
    assert api(remove["call"], **remove["params"]) == {"message": "Odebráno z oblíbených."}
    assert _titles(_section(api, "favorites")["Filmy"]) == ["Pelíšky"]
    assert "Moje pořady" in _section(api, "ivysilani")                       # a v iVysílání zůstávají taky

    api("progress_save", track=REF, position=600, total=6000, started=True)
    assert api("home")["start"] == 0                                         # pořadí sekcí rozhoduje: Oblíbené


def test_discover_section_loads_popular_rows_on_demand(api, monkeypatch):
    assert [row["title"] for row in api("home")["sections"][2]["rows"]] == ["Začínáme", "Objevuj"]   # bez klíče TMDB
    api("settings_set", tmdb_key="k" * 32)
    lazy = [row for row in api("home")["sections"][2]["rows"] if row.get("call")]
    assert [(row["title"], row["params"], row["poster"], row["items"]) for row in lazy] == [
        ("Nově na Přehraj.to", {}, True, []),
        ("Právě populární filmy", {"kind": "movie", "category": "popular"}, True, []),
        ("Právě populární seriály", {"kind": "tv", "category": "popular"}, True, [])]
    assert api(lazy[0]["call"]) == {"items": [], "hide": True}               # nic nového: řada se neukáže
    lazy = lazy[1:]
    films = [{"id": n, "title": "Film %d" % n, "release_date": "2024-01-01", "poster_path": "/p%d.jpg" % n}
             for n in range(1, 31)]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 3}})
    asked = _catalogue_search(monkeypatch, {"Film %d 2024" % n: [_web(n, "Film.%d.2024.CZ.1080p" % n, 7000)]
                                            for n in range(1, 31) if n != 2})
    row = api(lazy[0]["call"], **lazy[0]["params"])["items"]                 # jen tituly, které jsou k mání
    assert "Film 2" not in _titles(row) and row[0]["badges"] == ["Přehraj.to", "CZ"]
    assert len(row) == 21 and row[0]["title"] == "Film 1" and row[0]["target"]["type"] == "title"
    count = len(asked)
    api(lazy[0]["call"], **lazy[0]["params"])
    assert len(asked) == count                                               # podruhé se už nic neověřuje
    assert row[-1]["title"] == "Zobrazit vše" and row[-1]["target"] == {
        "type": "list", "call": "discover", "params": {"kind": "movie", "category": "popular"},
        "title": "Filmy – populární", "poster": True}


def test_discover_offers_only_titles_available_on_prehrajto_or_ivysilani(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"},
             {"id": 13, "title": "Samotáři", "release_date": "2000-04-20"}]
    shows = [{"id": 77, "name": "Vyprávěj", "first_air_date": "2009-08-31"},
             {"id": 78, "name": "Archa", "first_air_date": "1996-01-01"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/tv/popular": {"results": shows, "total_pages": 1}, "/movie/11": FILM_DETAIL,
                             "/tv/77": {"id": 77, "name": "Vyprávěj", "first_air_date": "2009-08-31",
                                        "seasons": [{"season_number": 1, "name": "1. řada", "episode_count": 2}]},
                             "/tv/77/season/1": {"episodes": [{"episode_number": 1, "name": "Od začátku"}]}})
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)]})
    listing = api("discover", kind="movie", category="popular")
    assert _titles(listing["items"]) == ["Seřadit", "Pelíšky"]                 # co není nikde, v nabídce není
    assert listing["items"][1]["badges"] == ["Přehraj.to", "CZ"] and listing["next"] is None   # je tam i český dabing
    assert (listing["items"][0]["subtitle"], listing["count"]) == ("Podle oblíbenosti", "1 titul")   # kolik titulů výpis má
    count = len(asked)
    api("discover", kind="movie", category="popular")
    assert len(asked) == count                                               # ověřené si aplikace pamatuje

    series = api("discover", kind="tv", category="popular")                  # seriál, který je jen v iVysílání
    assert _titles(series["items"]) == ["Seřadit", "Vyprávěj"] and series["items"][1]["badges"] == ["iVysílání"]
    assert ("Vyprávěj", 1) in asked and ("Vyprávěj S01E01", 1) not in asked   # seriál se hledá jako celek, ne jeden díl
    page = api("discover_title", **series["items"][1]["target"]["params"])
    assert page["actions"][0] == {"title": "Díly v iVysílání", "subtitle": "Česká televize", "target": {
        "type": "list", "call": "ivys_show", "params": {"id": "12"}, "title": "Vyprávěj"}}
    assert "iVysílání" not in " ".join(_titles(api("discover_title", **listing["items"][1]["target"]["params"])["actions"]))

    genuine = app_api.app_discover._ivys_match
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "11")
    film = api("discover_title", kind="movie", id=14, title="Krakonošovo tajemství", year=2022)   # film jen v iVysílání
    assert film["actions"][0] == {"title": "Přehrát v iVysílání", "subtitle": "Česká televize", "target": {
        "type": "play", "call": "ivys_play", "params": {"show": "11"}}}
    assert film["detail"]["plot"].startswith("Na Přehraj.to jsem tento film nenašel, je ale v iVysílání")
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", genuine)

    everything = api("discover", kind="movie", category="popular", avail="all")["items"]   # filtr: všechny tituly
    assert _titles(everything) == ["Seřadit", "Pelíšky", "Kolja", "Samotáři"]
    assert [card["badges"] for card in everything[1:]] == [["Přehraj.to", "CZ"], ["Nenalezeno"], ["Nenalezeno"]]
    empty = api("discover", kind="movie", category="popular", year="1950s")  # TMDB nic nevrátí
    assert "error" in empty or _titles(empty["items"]) == ["Seřadit"]


def test_titles_that_cannot_be_verified_are_not_shown_and_the_list_says_so(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"}]
    more = [{"id": 12, "title": "Kolja", "release_date": "1996-05-15"},
            {"id": 13, "title": "Samotáři", "release_date": "2000-04-20"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/movie/top_rated": {"results": more, "total_pages": 1}})

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("výpadek")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)
    listing = api("discover", kind="movie", category="popular")              # hledání nejde: neověřený titul se neukáže
    assert _titles(listing["items"]) == ["Seřadit"] and "nepodařilo ověřit" in listing["status"]
    assert "refresh" not in listing                                          # a obrazovka se netočí dokola
    asked = _catalogue_search(monkeypatch, {})
    assert len(api("discover", kind="movie", category="popular")["items"]) == 1   # chvíli se služba nezkouší
    assert asked == []
    app_api.app_discover._PAUSED.clear()                                     # po pauze se titul ověří
    assert _titles(api("discover", kind="movie", category="popular")["items"]) == ["Seřadit"]

    # iVysílání neodpovídá: rozhoduje Přehraj.to; iVysílání se doplní, až odpoví (bez nového hledání)
    asked = _catalogue_search(monkeypatch, {"Kolja 1996": [_web(40, "Kolja.1996.CZ.1080p", 6000)]})
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: None)
    partial = api("discover", kind="movie", category="top_rated")["items"]
    assert _titles(partial) == ["Seřadit", "Kolja"] and partial[1]["badges"] == ["Přehraj.to", "CZ"]
    count = len(asked)
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "11" if "Samotáři" in names else "")
    app_api.app_discover._PAUSED.clear()
    later = api("discover", kind="movie", category="top_rated")["items"]
    assert _titles(later) == ["Seřadit", "Kolja", "Samotáři"] and later[2]["badges"] == ["iVysílání"]
    assert len(asked) == count                                               # na Přehraj.to se znovu nehledalo
    assert asked and "Zapnout" not in api("discover", kind="movie", category="popular")["empty"]


def test_account_change_drops_the_previous_login_and_settings_survive_parallel_writes(api, tmp_path):
    api("settings_set", username="a@b.cz", password="heslo", tmdb_key="k" * 32)
    cookies = tmp_path / "cookies.lwp"
    cookies.write_text("#LWP-Cookies-2.0\n")
    api("settings_set", prefer_quality="720")                                # jiná volba přihlášení nechá být
    assert cookies.exists()
    api("settings_set", password="jine")                                     # jiné heslo: staré přihlášení neplatí
    assert not cookies.exists()
    cookies.write_text("#LWP-Cookies-2.0\n")
    api("settings_set", username="c@d.cz")
    assert not cookies.exists()

    import threading
    workers = [threading.Thread(target=lambda n=n: [app_api._remember_search("dotaz %d %d" % (n, i)) or
                                                    app_api._change_settings(lambda s: s.update(last_sync=i))
                                                    for i in range(25)]) for n in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    settings = api("settings_get")                                           # souběžné zápisy nic nesmazaly
    assert settings["username"] == "c@d.cz" and settings["has_password"] and settings["tmdb_key"] == "k" * 32
    assert len(api("search_history")["rows"][0]["items"]) == 12


def test_interrupted_profile_switch_does_not_mix_profiles(api, monkeypatch):
    _fill(2)
    api("progress_save", track=REF, position=1500, total=6000, started=True)
    api("search", q="pelisky")
    api("profile_add", avatar="owl", text="Jana")
    owl = api("profiles")["items"][1]["target"]["params"]["id"]

    def fail(change):
        raise OSError("disk je plný")

    genuine = app_api._change_settings
    monkeypatch.setattr(app_api, "_change_settings", fail)                   # přepnutí spadne uprostřed
    assert "error" in api("profile_switch", id=owl)
    monkeypatch.setattr(app_api, "_change_settings", genuine)
    assert api("home")["profile"]["name"] == "Hlavní profil"                 # nic se nepřepnulo ani nepomíchalo
    assert [item["target"]["params"] for item in _section(api, "progress")["Filmy"]] == [{"vid": REF, "resume": True}]
    api("profile_switch", id=owl)                                            # druhý pokus projde
    assert api("home")["profile"]["name"] == "Jana" and list(_section(api, "progress")) == ["Rozkoukané"]
    assert api("search_history") == {"rows": []}                             # hledané výrazy má každý profil své
    with app_api._library() as lib:                                          # přepnutí přerušené až po výměně
        lib.set_kv("profile_live", "p1")                                     # katalogu: platí, čí stav v něm je
    assert api("home")["profile"]["name"] == "Hlavní profil"
    with app_api._library() as lib:
        lib.set_kv("profile_live", owl)
    api("profile_switch", id="p1")                                           # a původní profil o nic nepřišel
    assert [item["target"]["params"] for item in _section(api, "progress")["Filmy"]] == [{"vid": REF, "resume": True}]
    assert _titles(api("search_history")["rows"][0]["items"]) == ["pelisky"]


def test_search_survives_a_wrong_password_and_partial_failures_are_not_remembered(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="spatne")
    logins = []

    class Client(object):
        def __init__(self, username):
            self.has_credentials = bool(username)

        def login(self):
            logins.append(1)
            raise app_api.provider.LoginError("špatné heslo")

    monkeypatch.setattr(app_api.provider, "make_client", lambda username, password, **kwargs: Client(username))
    films = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    asked = []

    def search_catalog(client, query, page, cursor):
        asked.append((query, client.has_credentials))
        if query == "Kolja 1996":
            raise app_api.provider.ProviderError("vypršel čas")                # první dotaz spadne, druhý nic nenajde
        return ([_web(32, "Pelisky.1999.CZ.1080p", 7000)] if query == "Pelíšky 1999" else []), ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search_catalog)
    listing = api("discover", kind="movie", category="popular")["items"]
    assert len(logins) == 1 and not any(logged for _query, logged in asked)  # jedno přihlášení, pak bez účtu
    assert _titles(listing) == ["Seřadit", "Pelíšky"] and listing[1]["badges"] == ["Přehraj.to", "CZ"]
    with app_api._library() as lib:                                          # Kolja: „nevím“, ne „není tam“ –
        assert "movie:12" not in app_api.app_discover._known(lib, "movie", [11, 12])   # nic se o něm nezapsalo


def test_available_only_list_keeps_looking_and_missing_series_favourite_says_so(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 100 + n, "title": "Neznámý %d" % n, "release_date": "2024-01-01"} for n in range(20)]
    calls = _fake_tmdb(monkeypatch, {"/discover/movie": {"results": films, "total_pages": 9}})
    _catalogue_search(monkeypatch, {})
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "")
    listing = api("discover", kind="movie", category="recent")               # celý výběr prošel a nic k mání
    assert _titles(listing["items"]) == ["Seřadit"] and listing["next"] is None
    assert (listing["items"][0]["subtitle"], listing["count"]) == ("Podle oblíbenosti", "0 titulů")
    assert sorted({params.get("page") or 1 for path, params in calls if path == "/discover/movie"}) == list(range(1, 10))
    assert "primary_release_date.gte" in calls[0][1] and calls[0][1].get("sort_by") in (None, "popularity.desc")

    page = api("discover_title", kind="tv", id=0, title="Archa", year=1996, fid="show:archa")
    assert page["rows"] == [] and page["detail"]["plot"].startswith("Tento seriál už ve vašem katalogu není")
    assert "Hledat ručně" in _titles(page["actions"])


def test_card_menu_marks_watched_and_clears_in_progress(api):
    _fill(3)
    first, second = ["%d:archa:%016x" % (10 + n, 10 + n) for n in (1, 2)]
    api("progress_save", track=REF, position=1500, total=6000, started=True)
    api("progress_save", track=first, position=600, total=1200, started=True)
    film = _section(api, "progress")["Filmy"][0]
    remove = next(item for item in film["menu"] if item["title"] == "Odebrat z Rozkoukaných")["target"]
    assert api(remove["call"], **remove["params"]) == {"message": "", "toast": "Odebráno z Rozkoukaných."}
    assert "Filmy" not in _section(api, "progress")                          # film už rozkoukaný není

    episode = _section(api, "progress")["Seriály"][0]
    mark = next(item for item in episode["menu"] if item["title"] == "Označit jako zhlédnuté")["target"]
    assert api(mark["call"], **mark["params"])["toast"] == "Označeno jako zhlédnuté."
    rows = _section(api, "progress")                                         # díl je zhlédnutý: nabízí se další
    assert rows["Seriály"][0]["target"]["params"] == {"vid": second}       # (karta seriálu teď nabízí další díl)
    assert len(rows["Seriály"]) == 1 and "Další díly" not in rows
    card = next(item for item in api("show", key="show:archa")["items"] if item["target"].get("params") == {"vid": first})
    back = next(item for item in card["menu"] if item["title"] == "Označit jako nezhlédnuté")["target"]
    api(back["call"], **back["params"])
    assert "Další díly" not in _section(api, "progress")
    assert "Chybí" in api("watched_set")["error"] and "Chybí" in api("progress_remove")["error"]

    with app_api._library() as lib:                                          # totéž u pořadu z iVysílání
        lib.ivys_touch("777", "9", "Díl 3", "Vyprávěj", "")
        lib.ivys_record_play("777", 300, 3000)
    television = next(card for card in _section(api, "progress")["Seriály"] if card["subtitle"] == "iVysílání")
    assert _titles(television["menu"]) == ["Pokračovat", "Přehrát od začátku", "Odebrat z Rozkoukaných"]
    api(television["menu"][2]["target"]["call"], **television["menu"][2]["target"]["params"])
    assert all(card["subtitle"] != "iVysílání" for card in _section(api, "progress").get("Seriály", []))


def test_download_records_survive_a_damaged_file_and_stray_parts_are_removed(api, tmp_path):
    _fill()
    disk = tmp_path / "disk"
    disk.mkdir()
    api("download_add", vid=REF)
    _link(REF, "https://cdn.x/pelisky.mkv")
    job = api("download_next", dir=str(disk))
    open(job["path"], "wb").write(b"x" * 10)
    api("download_finish", vid=REF, size=10)
    (tmp_path / "downloads.json").write_text("{poškozeno")                   # výpadek proudu uprostřed zápisu
    assert "Staženo" in api("downloads")["items"][0]["subtitle"]              # záloha z poslední změny stavu
    (disk / "Starý film.mkv.part").write_bytes(b"zbytek")                    # rozpracovaný soubor bez záznamu
    (disk / "Jiný film.mkv").write_bytes(b"hotovo")
    assert api("downloads_cleanup", dir=str(disk)) == {"removed": 1}
    assert sorted(path.name for path in disk.iterdir()) == ["Jiný film.mkv", "Pelíšky.mkv"]
    assert api("downloads_cleanup", dir=str(tmp_path / "neni")) == {"removed": 0}


def test_availability_is_verified_in_the_background_while_the_list_is_shown(api, monkeypatch):
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    release = __import__("threading").Event()
    asked = []

    def search_catalog(client, query, page, cursor):
        release.wait(5)                                                      # hledání „trvá“, dokud ho test nepustí
        asked.append(query)
        return ([_web(32, "Pelisky.1999.CZ.1080p", 7000)] if query == "Pelíšky 1999" else []), ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search_catalog)
    first = api("discover", kind="movie", category="popular")                # výpis je hned, nic nečeká;
    assert _titles(first["items"]) == ["Seřadit"] and asked == []              # neověřené tituly v něm ale nejsou
    assert first["refresh"] == 2 and first["status"].endswith("zbývá ověřit 2")
    assert first["next"] is None                                             # a dál se nejde, dokud není jasno
    row = api("discover_row", kind="movie", category="popular")              # řada na hlavní obrazovce stejně
    assert row["refresh"] == 2 and row["items"] == []
    thread = discover._VERIFY["thread"]
    release.set()
    thread.join(10)
    assert not thread.is_alive() and discover._VERIFY["thread"] is None
    done = api("discover", kind="movie", category="popular")                 # po ověření: jen co je k mání
    assert _titles(done["items"]) == ["Seřadit", "Pelíšky"] and done["items"][1]["badges"] == ["Přehraj.to", "CZ"]
    assert "refresh" not in done and "status" not in done
    assert "refresh" not in api("discover_row", kind="movie", category="popular")

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("výpadek")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)          # služba neodpovídá: žádné točení dokola
    _fake_tmdb(monkeypatch, {"/movie/top_rated": {"results": [
        {"id": 13, "title": "Samotáři", "release_date": "2000-04-20"}], "total_pages": 1}})
    waiting = api("discover", kind="movie", category="top_rated")
    assert waiting["refresh"] == 2
    (discover._VERIFY["thread"] or thread).join(10)                          # mohlo už doběhnout
    settled = api("discover", kind="movie", category="top_rated")
    assert _titles(settled["items"]) == ["Seřadit"] and "refresh" not in settled
    assert "nepodařilo ověřit" in settled["status"]


def _stream_pages(monkeypatch, offered, account=True):
    """Stránky videí na Přehraj.to: ``{číslo videa: [výšky překódovaných verzí]}``; s účtem i původní soubor."""
    asked = []

    def get_streams(vid):
        number = int(vid.split(":")[0])
        asked.append(number)
        if number not in offered:
            raise app_api.provider.VideoUnavailableError("není")
        streams = [{"quality": "ORIG", "url": "orig:" + vid}] if account else []
        streams += [{"quality": app_api.provider.quality_token(str(height)), "url": "https://cdn.x/%d.mp4" % height,
                     "resolution": "%dp" % height} for height in offered[number]]
        return streams, ""

    original = app_api.provider.make_client

    def make_client(*args, **kwargs):
        client = original(*args, **kwargs)
        client.get_streams, client.resolve = get_streams, lambda url: url
        return client

    monkeypatch.setattr(app_api.provider, "make_client", make_client)
    return asked


def test_play_button_shows_the_quality_that_really_plays(api, monkeypatch):
    # Štítek „HD“ ve výpisu Přehraj.to nese 720p i 1080p: tlačítko Přehrát proto ukazuje, co nabízí stránka videa.
    api("settings_set", tmdb_key="k" * 32, username="u@example.com", password="x")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    found = [_web(41, "Pelisky 1999 CZ", 2 * hour, "6 GB", quality="HD"),
             _web(42, "Pelisky 1999 CZ.", 2 * hour, "5 GB", quality="HD"),
             _web(43, "Pelisky 1999 CZ 2160p", 2 * hour, "20 GB", quality="HD")]
    _catalogue_search(monkeypatch, {"Pelíšky 1999": found})
    asked = _stream_pages(monkeypatch, {41: [1080, 720], 42: [720], 43: [1080, 720]})
    page = api("discover_title", **FILM)
    play = page["actions"][0]
    # název souboru říká přesně „2160p“, výpis jen „HD“: platí název; s účtem se hraje původní soubor
    assert (play["title"], play["subtitle"]) == ("Přehrát", "CZ dabing · originál 4K") and asked == [43]
    cards = page["rows"][0]["items"]
    assert _titles(cards) == ["CZ dabing · 4K", "CZ dabing · HD", "CZ dabing · HD"]   # na kartě jen rozlišení
    assert cards[0]["badges"] == ["CZ", "4K"] and cards[1]["badges"] == ["CZ", "HD"]
    assert api("discover_title", **FILM)["actions"][0]["subtitle"] == "CZ dabing · originál 4K" and asked == [43]   # pamatuje si

    # přehrání verze zjistí, co stránka videa nabízí
    api("play", **cards[1]["target"]["params"])
    assert _titles(api("discover_title", **FILM)["rows"][0]["items"]) == [     # puštěná verze je příště první
        "CZ dabing · 1080p", "CZ dabing · 4K", "CZ dabing · HD"]
    # přehrávač nahlásí skutečné rozměry obrazu: „2160p“ v názvu neplatilo, původní soubor je jen Full HD
    api("video_size", track=found[2]["id"], width=1920, height=800)          # nic se nepouštělo: nepočítá se
    assert api("discover_title", **FILM)["rows"][0]["items"][1]["title"] == "CZ dabing · 4K"
    api("play", **cards[0]["target"]["params"])
    assert api("video_size", track=found[2]["id"], width=1920, height=800) == {"lines": 1080}
    assert sorted(_titles(api("discover_title", **FILM)["rows"][0]["items"])) == [
        "CZ dabing · 1080p", "CZ dabing · 1080p", "CZ dabing · HD"]
    assert api("discover_title", **FILM)["actions"][0]["subtitle"] == "CZ dabing · originál 1080p"

    # úsporná předvolba: hraje se překódovaná verze v nejnižším rozlišení
    api("settings_set", prefer_quality="small")
    small = api("discover_title", **FILM)["rows"][0]["items"]
    assert any(title.endswith("· 720p") for title in _titles(small))
    api("settings_set", prefer_quality="best")
    assert api("video_size", track="ivys:1", width=1920, height=1080) == {}


def test_play_button_without_an_account_shows_the_best_transcoded_version(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = [_web(41, "Pelisky 1999 CZ", 7200, "6 GB", quality="HD")]
    _catalogue_search(monkeypatch, {"Pelíšky 1999": found})
    _stream_pages(monkeypatch, {41: [1080, 720]}, account=False)
    assert api("discover_title", **FILM)["actions"][0]["subtitle"] == "CZ dabing · 1080p"

    with app_api._library() as lib:
        lib.set_kv(app_api.REAL_PREFIX + found[0]["id"], "")
    _stream_pages(monkeypatch, {})
    # stránka videa se nenačetla: zůstane údaj z výpisu, stránka titulu kvůli tomu neselže
    assert api("discover_title", **FILM)["actions"][0]["subtitle"] == "CZ dabing · HD"


def test_minimum_quality_follows_the_real_resolution(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32, min_quality="1080")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    found = [_web(51, "Pelisky 1999 CZ a", 2 * hour, "9 GB", quality="HD"),     # „HD“ ve výpisu, ve skutečnosti 720p
             _web(52, "Pelisky 1999 CZ b", 2 * hour, "8 GB", quality="HD"),     # „HD“ ve výpisu, ve skutečnosti 1080p
             _web(53, "Pelisky 1999 CZ c", 2 * hour, "700 MB", quality="HD"),   # malý soubor: Full HD to nebude
             _web(54, "Pelisky 1999 CZ 720p", 2 * hour, "9 GB", quality="HD")]  # název říká přesně 720p
    _catalogue_search(monkeypatch, {"Pelíšky 1999": found})
    asked = _stream_pages(monkeypatch, {51: [720], 52: [1080, 720]}, account=False)
    page = api("discover_title", **FILM)
    # Bez účtu se hraje překódovaná verze: samotné „HD“ soubor nevyřadí, ale první verze se po ověření
    # ukázala jako 720p, a tak vypadla; větší soubor se ověřuje dřív.
    assert asked == [51, 52]
    assert _titles(page["rows"][0]["items"]) == ["CZ dabing · 1080p"]
    assert page["actions"][0]["subtitle"] == "CZ dabing · 1080p"
    again = api("discover_title", **FILM)
    assert asked == [51, 52] and _titles(again["rows"][0]["items"]) == ["CZ dabing · 1080p"]

    # S účtem se hraje původní soubor a ten může být lepší než překódovaná verze 720p: zůstává,
    # dokud přehrávač nezměří, že je opravdu jen 720p.
    api("settings_set", username="u@example.com", password="x")
    _stream_pages(monkeypatch, {51: [720], 52: [1080, 720]})
    kept = api("discover_title", **FILM)
    assert _titles(kept["rows"][0]["items"]) == ["CZ dabing · HD", "CZ dabing · 1080p"]
    assert kept["actions"][0]["subtitle"] == "CZ dabing · originál HD"
    assert api("video_size", track=found[0]["id"], width=1280, height=720) == {"lines": 720}
    with app_api._library() as lib:
        assert "played" not in app_api._real(lib, found[0]["id"])     # nic se nepouštělo: měření se nepočítá
    api("play", vid=found[0]["id"], web_result=found[0])
    api("video_size", track=found[0]["id"], width=1280, height=720)
    measured = api("discover_title", **FILM)
    assert _titles(measured["rows"][0]["items"]) == ["CZ dabing · 1080p"]
    assert measured["actions"][0]["subtitle"] == "CZ dabing · originál 1080p"
    api("play", vid=found[1]["id"], web_result=found[1])
    api("video_size", track=found[1]["id"], width=1280, height=534)
    low = api("discover_title", **FILM)
    assert low["rows"] == [] or "Verze" not in low["rows"][0]["title"]
    assert "jen v nižší kvalitě než Full HD (nalezeno: 4)" in low["detail"]["plot"]

    assert app_api._picture_lines(3840, 1600) == 2160 and app_api._picture_lines(1920, 800) == 1080
    assert app_api._picture_lines(1280, 536) == 720 and app_api._picture_lines(720, 404) == 404
    assert app_api._known_quality("HD", "1080p") == "1080p" and app_api._known_quality("HD", "") == "HD"
    assert app_api._known_quality("1080p", "720p") == "1080p" and app_api._known_quality("", "HD") == "HD"


def test_bigger_file_wins_among_versions_labelled_just_hd(api, monkeypatch):
    # Přehraj.to značí „HD“ 720p i Full HD; při „nejvyšší dostupné“ kvalitě proto rozhoduje datový tok.
    api("settings_set", tmdb_key="k" * 32, min_quality="720")
    hour = 3600
    found = [_web(61, "Duna 2021 CZ a", 2 * hour, "1.4 GB", quality="HD"),
             _web(62, "Duna 2021 CZ b", 2 * hour, "11 GB", quality="HD"),       # velký soubor: nejspíš Full HD
             _web(63, "Duna 2021 CZ 1080p", 2 * hour, "3 GB", quality="HD"),
             _web(64, "Duna 2021 CZ dvdrip", 2 * hour, "1.4 GB"),               # bez štítku HD: neprojde
             _web(65, "Duna 2021 CZ 2160p", 2 * hour, "18 GB", quality="HD")]
    _catalogue_search(monkeypatch, {"Duna 2021": found, "Duna": found})

    def order():
        with app_api._library() as lib:
            state = app_api._state(lib)
        return [int(version["video"]["id"].split(":")[0])
                for version in app_api.app_discover._find_versions(["Duna"], 2021, None, state)]

    assert order() == [65, 62, 63, 61]                 # 4K, pak Full HD podle velikosti, pak HD
    api("settings_set", prefer_quality="small")
    assert order()[0] == 61                            # úsporná předvolba: nejmenší soubor
    api("settings_set", prefer_quality="best", min_quality="")
    assert order() == [65, 62, 63, 61, 64]


def test_title_page_search_retries_a_temporary_failure_and_says_why(api, monkeypatch):
    # Jedno zaváhání Přehraj.to (omezení dotazů, vypršení času) nesmí stránku titulu shodit do „nezdařilo se“.
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = [_web(41, "Pelisky 1999 CZ 1080p", 7200, "6 GB")]
    calls = []

    def flaky(client, query, page, cursor):
        calls.append(query)
        if len(calls) == 1:
            raise app_api.provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")
        return found, ""

    monkeypatch.setattr(app_api.provider, "search_catalog", flaky)
    page = api("discover_title", **FILM)
    assert calls == ["Pelíšky 1999", "Pelíšky 1999"] and page["actions"][0]["title"] == "Přehrát"   # druhý pokus vyšel
    assert app_api.app_discover._VERIFY["hold"] > 0          # ověřování na pozadí mezitím nezačíná další dávku

    def limited(client, query, page, cursor):
        raise app_api.provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")

    monkeypatch.setattr(app_api.provider, "search_catalog", limited)
    failed = api("discover_title", **FILM)
    assert failed["detail"]["plot"].startswith(
        "Hledání na Přehraj.to se teď nezdařilo: Přehraj.to dočasně omezuje počet dotazů. Neznamená to, že tam titul není")
    assert failed["actions"][0]["title"] == "Zkusit znovu" and failed["retry"] is True   # obrazovka to zkusí sama
    assert len(calls) == 2 and "retry" not in page
    assert "HTTP 429" in str(api("diagnostics")["errors"][0])                   # technický důvod je v Diagnostice


def test_search_shares_one_client_and_survives_a_failed_login_check(api, monkeypatch):
    # Dřív si každá stránka titulu ověřovala přihlášení znovu, a když ověření neprošlo, selhalo celé hledání.
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="heslo")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    found = [_web(41, "Pelisky 1999 CZ 1080p", 7200, "6 GB")]
    made, logins = [], []

    class Client(object):
        request_timeout, request_retries = 20, 2

        def __init__(self, username):
            self.has_credentials = bool(username)
            made.append(username)

        def login(self):
            logins.append(1)
            if logins == [1]:
                return True
            raise app_api.provider.TemporaryError("HTTP 503")

        def search_page(self, query, page, cursor):
            return found, ""

        def get_streams(self, vid):
            raise app_api.provider.VideoUnavailableError("není")

    monkeypatch.setattr(app_api.provider, "make_client", lambda username, password, **kwargs: Client(username))
    first = api("discover_title", **FILM)
    again = api("discover_title", **FILM)
    assert first["actions"][0]["title"] == again["actions"][0]["title"] == "Přehrát"
    assert made == ["a@b.cz"] and logins == [1]              # jeden klient, přihlášení ověřené jednou
    assert api("prehrajto_search", q="Pelíšky")["items"] and made == ["a@b.cz"]

    api("settings_set", password="jine")                    # jiný účet: nový klient; ověření přihlášení neprošlo
    page = api("discover_title", **FILM)
    assert page["actions"][0]["title"] == "Přehrát"         # hledá se bez přihlášení, stránka funguje dál
    assert made == ["a@b.cz", "a@b.cz", ""] and len(logins) == 2
    api("discover_title", **FILM)
    assert len(made) == 3 and len(logins) == 2              # a přihlášení se nezkouší před každým hledáním znovu


def test_slovak_dubbing_counts_as_the_preferred_language(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    hour = 3600
    found = [_web(81, "Duna 2021 EN 1080p", 2 * hour, "9 GB"),
             _web(82, "Duna 2021 slovensky dabing 1080p", 2 * hour, "8 GB"),
             _web(83, "Duna 2021 CZ dabing 720p", 2 * hour, "3 GB"),
             _web(84, "Duna 2021 slovenske titulky 1080p", 2 * hour, "7 GB")]
    _catalogue_search(monkeypatch, {"Duna 2021": found, "Duna": found})
    with app_api._library() as lib:
        state = app_api._state(lib)
    versions = app_api.app_discover._find_versions(["Duna"], 2021, None, state)
    # slovenský dabing je stejně dobrý jako český: mezi nimi rozhoduje kvalita; titulky a cizí zvuk až potom
    assert [(int(v["video"]["id"].split(":")[0]), v["video"]["lang"]) for v in versions] == [
        (82, "SK dabing"), (83, "CZ dabing"), (84, "SK titulky"), (81, "EN")]
    assert dict(app_api.LANGUAGE_CHOICES)["dub"] == "Český nebo slovenský dabing"


def test_ratings_and_posters_fill_in_by_themselves(api, monkeypatch):
    _fill(2)                                   # Pelíšky už metadata mají, seriál Archa je nový
    asked = []

    def enrich(lib, metas, **kw):
        asked.append(sorted(meta["key"] for meta in metas))
        for meta in metas:
            lib.set_meta_status(meta["key"], "notfound" if len(asked) == 1 else "ok")
        return {"ok": 0 if len(asked) == 1 else len(metas), "notfound": len(metas), "csfd_blocked": False}

    monkeypatch.setattr(app_api.catalog, "enrich", enrich)
    # po otevření aplikace se doplní samo, i bez přihlášení k Přehraj.to; nic se nenašlo → obrazovka se neobnovuje
    assert api("quick_sync") == {"new": 0, "skipped": "account"}
    assert len(asked) == 1 and len(asked[0]) == 1 and "archa" in asked[0][0]
    assert api("quick_sync") == {"new": 0, "skipped": "account"} and len(asked) == 1   # nenalezené se hned nezkouší znovu
    monkeypatch.setattr(app_api, "ENRICH_NOTFOUND_AFTER", -1)                          # … až po čase
    assert api("quick_sync") == {"new": 0, "skipped": "account", "refresh": True} and len(asked) == 2
    assert api("quick_sync") == {"new": 0, "skipped": "account"} and len(asked) == 2   # hotovo: už se neptá


def test_listing_fills_step_by_step_and_does_not_run_ahead(api, monkeypatch):
    # Ve výpisu je jen to, co se už ověřilo. Výpis zná celý svůj výběr (a počet titulů) hned; neověřené
    # tituly se ověřují po dávkách a výpis se mezitím sám doplňuje – stejné tituly na stejných místech.
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    monkeypatch.setattr(discover, "SORT_VERIFY", 2)
    api("settings_set", tmdb_key="k" * 32)
    first = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    second = [{"id": 13, "title": "Samotáři", "release_date": "2000-04-20"}]
    fourth = [{"id": 14, "title": "Musíme si pomáhat", "release_date": "2000-03-30"}]
    pages = []

    def fetch(self, path, timeout, params):
        if path != "/movie/popular":
            raise app_api.TmdbError("TMDB: HTTP 404")
        page = params.get("page") or 1
        pages.append(page)
        return {"results": first if page == 1 else fourth if page == 4 else second if page == 2 else [],
                "total_pages": 4}

    monkeypatch.setattr(app_api.Tmdb, "_fetch", fetch)
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)],
                                            "Samotáři 2000": [_web(33, "Samotari.2000.CZ.1080p", 7000)]})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")

    def settle():
        thread = discover._VERIFY["thread"]
        if thread:
            thread.join(10)

    listing = api("discover", kind="movie", category="popular")
    assert _titles(listing["items"]) == ["Seřadit"] and listing["next"] is None and listing["refresh"] == 2
    assert listing["count"] == "zatím 0 titulů"                              # ještě se ověřuje
    assert listing["status"].endswith("zbývá ověřit 4") and sorted(set(pages)) == [1, 2, 3, 4]
    settle()
    assert not any("Samot" in query or "Musíme" in query for query, _page in asked)   # jen první dávka
    middle = api("discover", kind="movie", category="popular")
    assert _titles(middle["items"]) == ["Seřadit", "Pelíšky"] and middle["refresh"] == 2
    settle()
    done = api("discover", kind="movie", category="popular")                 # vše ověřené
    assert _titles(done["items"]) == ["Seřadit", "Pelíšky", "Samotáři"] and "refresh" not in done
    assert done["count"] == "2 tituly" and done["next"] is None


def test_missing_titles_are_rechecked_sooner_when_they_are_new(api, monkeypatch):
    discover = app_api.app_discover
    year = __import__("time").localtime().tm_year
    assert discover._missing_ttl(year) == discover._missing_ttl(year - 1) == discover.MISSING_FRESH
    assert discover._missing_ttl(year - 3) == discover.MISSING_TTL
    assert discover._missing_ttl(year - 20) == discover.MISSING_OLD
    discover._remember_availability("movie", 21, 0, "", year)                # letošní film, nenalezený
    discover._remember_availability("movie", 22, 0, "", 1990)                # starý film, nenalezený
    now = __import__("time").time()
    monkeypatch.setattr(discover.time, "time", lambda: now + 24 * 3600)      # o den později
    with app_api._library() as lib:
        known = discover._known(lib, "movie", [21, 22])
    # novinka se ověří znovu, starý film ne; údaj o novince ale mezitím platí dál (jen je označený jako starý)
    assert known == {"movie:21": {"n": 0, "iv": "", "stale": True}, "movie:22": {"n": 0, "iv": ""}}
    assert not discover._fresh(known, "movie:21") and discover._fresh(known, "movie:22")


def test_title_is_found_under_its_slovak_name_too(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    film = {"id": 31, "title": "Ledové království", "original_title": "Frozen", "release_date": "2013-11-27"}
    names = {"translations": {"translations": [
        {"iso_639_1": "sk", "data": {"title": "Ľadové kráľovstvo"}}, {"iso_639_1": "de", "data": {"title": "Die Eiskönigin"}},
        {"iso_639_1": "cs", "data": {"title": "Ledové království"}}]},
        "alternative_titles": {"titles": [{"iso_3166_1": "CZ", "title": "Frozen: Ledové království"},
                                          {"iso_3166_1": "US", "title": "Frozen 3D"}]}}
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": [film], "total_pages": 1}, "/movie/31": names})
    asked = _catalogue_search(monkeypatch, {"Ľadové kráľovstvo 2013": [_web(51, "Ladove kralovstvo 2013 SK dabing 1080p", 6000)]})
    listing = api("discover", kind="movie", category="popular")["items"]
    assert _titles(listing) == ["Seřadit", "Ledové království"] and listing[1]["badges"] == ["Přehraj.to", "SK"]
    assert [query for query, _page in asked] == ["Ledové království 2013", "Ledové království", "Frozen 2013",
                                                 "Ľadové kráľovstvo 2013"]   # další názvy až když první nic nenajdou
    page = api("discover_title", **listing[1]["target"]["params"])           # i stránka titulu ho pod ním najde
    assert page["actions"][0]["title"] == "Přehrát" and page["rows"][0]["items"][0]["title"].startswith("SK dabing")


def test_episode_is_searched_in_the_1x05_form_too(api, monkeypatch):
    with app_api._library() as lib:
        state = app_api._state(lib)
    asked = _catalogue_search(monkeypatch, {"Archa 1x05": [_web(61, "Archa 1x05 CZ dabing", 2700)]})
    versions = app_api.app_discover._find_versions(["Archa"], 0, (1, 5), state)
    assert [query for query, _page in asked] == ["Archa S01E05", "Archa 1x05"] and len(versions) == 1


def test_background_checks_slow_down_when_the_site_pushes_back():
    discover = app_api.app_discover
    discover._PACE.update(interval=discover.PACE_FASTEST, next=0)
    discover._pace_result(True)
    discover._pace_result(True)
    assert discover._PACE["interval"] == discover.PACE_FASTEST * 4           # po odmítnutí se zpomalí
    for _ in range(60):
        discover._pace_result(False)
    assert discover._PACE["interval"] == discover.PACE_FASTEST               # a pozvolna zase zrychlí
    discover._PACE.update(interval=discover.PACE_SLOWEST, next=0)
    discover._pace_result(True)
    assert discover._PACE["interval"] == discover.PACE_SLOWEST               # ale ne donekonečna
    discover._PACE.update(interval=0, next=0)


def test_rate_limit_stops_background_checks_but_not_the_person(api, monkeypatch):
    """HTTP 429: ověřování na pozadí si dá přestávku (při opakování delší) a Přehraj.to mezitím
    nedostává nic; hledání, na které někdo čeká, se zkouší dál. Co bylo nalezené, zůstává."""
    discover, index = app_api.app_discover, app_api.app_index
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/tv/popular": {"results": [], "total_pages": 1}})
    asked = []
    answer = {"limit": True}

    def search(client, query, page, cursor):
        asked.append(query)
        if answer["limit"]:
            raise app_api.provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")
        return ([_web(1, query + " CZ dabing 1080p", 6000)], "")

    monkeypatch.setattr(app_api.provider, "search_catalog", search)
    now = [1000000.0]
    monkeypatch.setattr(discover.time, "time", lambda: now[0])
    monkeypatch.setattr(index.time, "time", lambda: now[0])
    monkeypatch.setattr(discover.time, "sleep", lambda seconds: None)
    assert index.step() == {"done": False, "checked": 0, "failed": True}   # pokus není „ověřeno“
    assert len(asked) <= 2 and index._state()["step"] == 0                   # po odmítnutí se další názvy nezkoušejí
    assert discover.limited() == discover.LIMIT_WAIT and discover.search_paused() >= discover.LIMIT_WAIT
    line = next(line for line in api("diagnostics")["lines"] if line["label"] == "Seznam dostupných titulů")
    assert "omezuje počet dotazů" in line["value"]
    del asked[:]
    assert index.step()["failed"] and asked == []                            # během přestávky se neposílá nic
    listing = api("discover", kind="movie", category="popular")
    assert asked == [] and "omezuje počet dotazů" in listing["status"]       # a výpis řekne proč
    # hledání, na které čeká člověk (stránka titulu), se zkusí i tak – a přestávku neprodlouží
    with app_api._library() as lib:
        state = app_api._state(lib)
    with pytest.raises(app_api.ApiError) as failure:
        discover._find_versions(["Pelíšky"], 1999, None, state)
    assert asked and "omezuje počet dotazů" in str(failure.value) and discover.limited() == discover.LIMIT_WAIT
    # po přestávce se pokračuje; další odmítnutí znamená dvakrát delší přestávku
    discover._PAUSED.clear()
    now[0] += discover.LIMIT_WAIT + 1
    assert index.step()["failed"] and discover.limited() == discover.LIMIT_WAIT * 2
    discover._PAUSED.clear()
    now[0] += discover.LIMIT_WAIT * 2 + 1
    answer["limit"] = False
    assert index.step() == {"done": False, "checked": 2, "failed": False} and index._state()["step"] == 1
    with app_api._library() as lib:
        assert discover._known(lib, "movie", [11, 12])["movie:11"]["n"] == 1


def test_diagnostics_keeps_a_journal_of_the_list(api):
    """Deník seznamu: odkdy se vede a co ho změnilo – aby šlo poznat, proč ubyl nebo začal znovu."""
    index, prefix = app_api.app_index, app_api.app_discover.AVAILABLE_PREFIX

    def line():
        return next(line["value"] for line in api("diagnostics")["lines"] if line["label"] == "Seznam – deník")

    with app_api._library() as lib:
        for number in range(60):
            lib.set_kv(prefix + "movie:%d" % number, json.dumps({"n": 1, "ts": 1, "r": 2, "d": "", "q": 1080, "s": 0}))
    assert line().startswith("veden od ") and line().endswith("; beze změn")
    api("settings_set", min_quality="2160")                                  # změna nastavení se zapíše, seznam zůstává
    assert "změna nejnižší kvality: bez omezení → 4K" in line() and len(index._entries()) == 60
    with app_api._library() as lib, lib.conn:
        lib.conn.execute("DELETE FROM settings_kv WHERE k LIKE ? AND k NOT LIKE ?", (prefix + "%", prefix + "movie:1%"))
    assert "záznamů ubylo z 60 na 11" in line()                              # nečekaný úbytek je vidět i s časem
    assert line().count("záznamů ubylo") == 1                                # a nezapisuje se pořád dokola


def test_update_verifies_the_whole_list_once_again(api, monkeypatch):
    """Po aktualizaci, která mění ověřování, se celý seznam jednou ověří znovu: nalezené tituly zůstávají
    vidět, „nenašlo se“ přestává platit a plán se projde od začátku. Podruhé už se nic neděje."""
    discover, index, prefix = app_api.app_discover, app_api.app_index, app_api.app_discover.AVAILABLE_PREFIX
    now = int(__import__("time").time())
    with app_api._library() as lib:
        lib.set_kv(prefix + "movie:11", json.dumps({"n": 2, "ts": now, "r": 2, "d": "cz", "q": 1080, "s": 0, "miss": 1}))
        lib.set_kv(prefix + "movie:12", json.dumps({"n": 0, "ts": now, "r": 2, "ttl": 864000}))
        lib.set_kv(prefix + "movie:13", json.dumps({"n": 0, "ts": now, "r": 2, "iv": "777", "ttl": 864000}))
    index._save({"step": 9, "finished": now, "day": index._today(), "count": 5})   # stav ze starší verze: bez „gen“
    assert index._renew() is True

    def known():
        with app_api._library() as lib:
            return discover._known(lib, "movie", [11, 12, 13])

    state = known()
    assert state["movie:11"]["n"] == 2 and state["movie:11"]["stale"]        # zůstává vidět, ověří se znovu
    assert "movie:12" not in state                                           # „nenašlo se“ už neplatí
    assert state["movie:13"]["n"] == 0 and state["movie:13"]["stale"]        # (v iVysílání byl: zůstává také)
    with app_api._library() as lib:
        assert "miss" not in json.loads(lib.get_kv(prefix + "movie:11"))
    saved = index._state()
    assert saved["step"] == 0 and saved["gen"] == index.GENERATION and "finished" not in saved and saved["count"] == 5
    line = next(line["value"] for line in api("diagnostics")["lines"] if line["label"] == "Seznam – deník")
    assert "po aktualizaci se celý seznam ověřuje znovu" in line
    discover._remember_availability("movie", 11, 2, "", 1999, {"d": "cz", "q": 1080, "s": 0})
    assert index._renew() is False and not known()["movie:11"].get("stale")  # jen jednou; ověřené se pamatuje
    index._save({})                                                          # nová instalace: není co obnovovat…
    with app_api._library() as lib, lib.conn:
        lib.conn.execute("DELETE FROM settings_kv WHERE k LIKE ?", (prefix + "%",))
    assert index._renew() is False and index._state()["gen"] == index.GENERATION   # …jen se zapamatuje verze


def test_audio_output_offers_ways_to_tame_surround_sound(api):
    style = api("subtitle_style")
    assert style["audio_output"] == "auto"
    assert [choice["id"] for choice in style["audio_outputs"]] == ["auto", "pcm", "pcm_soft", "stereo"]
    assert all(choice["title"] and choice["note"] for choice in style["audio_outputs"])
    assert api("audio_output_set", mode="pcm_soft")["audio_output"] == "pcm_soft"
    assert api("audio_output_set", mode="nesmysl")["audio_output"] == "pcm_soft"   # neznámá volba nic nezmění
    assert api("subtitle_style")["audio_output"] == "pcm_soft"


DAY = 86400


def _clock(monkeypatch, start=1700000000.0):
    """Hodiny, které test posouvá sám (ověřování i plnění seznamu se jimi řídí)."""
    now = [start]
    monkeypatch.setattr(app_api.app_discover.time, "time", lambda: now[0])
    monkeypatch.setattr(app_api.app_index.time, "time", lambda: now[0])
    monkeypatch.setattr(app_api.app_discover.time, "sleep", lambda seconds: None)
    return now


def _record(key):
    with app_api._library() as lib:
        return json.loads(lib.get_kv(app_api.app_discover.AVAILABLE_PREFIX + key) or "null")


def test_titles_are_searched_again_when_their_time_comes(api, monkeypatch):
    """Údaj o iVysílání stárne rychleji než údaj o Přehraj.to. Jeho doplnění nesmí posunout stáří
    údaje o Přehraj.to: jinak by se nalezený titul už nikdy nehledal znovu (a smazaný soubor by zůstal
    „k mání“ navždy) a nenalezený starý film by se nenašel, ani kdyby ho někdo nahrál."""
    discover = app_api.app_discover
    now = _clock(monkeypatch)
    found = {"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"}
    missing = {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}
    asked = _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(1, "Pelisky 1999 CZ 1080p", 7000)]})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    monkeypatch.setattr(discover, "_local_names", lambda kind, tmdb_id, known=(): [])
    with app_api._library() as lib:
        state = app_api._state(lib)
    discover._check_available("movie", [found, missing], state)
    first, stamp = len(asked), _record("movie:11")["ts"]
    now[0] += 8 * DAY                                                        # „není v iVysílání“ zestárlo (7 dní)
    discover._check_available("movie", [found, missing], state)
    assert len(asked) == first and _record("movie:11")["ts"] == stamp        # doplnilo se jen iVysílání
    assert _record("movie:11")["its"] == int(now[0])
    now[0] += 8 * DAY                                                        # 16 dní: nalezený (14) i nenalezený (10)
    discover._check_available("movie", [found, missing], state)
    assert [query for query, _page in asked[first:]] == ["Pelíšky 1999", "Kolja 1996", "Kolja"] or \
        sorted(query for query, _page in asked[first:]) == ["Kolja", "Kolja 1996", "Pelíšky 1999"]
    # stránka titulu doplňuje iVysílání stejně šetrně: nepovedené ověření (miss) tím nezmizí
    discover._remember_availability("movie", 13, 3, "", 1999, {"d": "cz", "q": 1080, "s": 0})
    now[0] += 15 * DAY
    discover._remember_availability("movie", 13, 0, None, 1999)
    stamp = _record("movie:13")["ts"]
    discover._remember_availability("movie", 13, 3, "", 1999, searched=False)
    assert _record("movie:13")["miss"] == 1 and _record("movie:13")["ts"] == stamp


def test_rate_limit_pauses_keep_growing_to_the_cap(api, monkeypatch):
    discover = app_api.app_discover
    now = _clock(monkeypatch)
    waits = []
    for _ in range(9):
        discover._rate_limited()
        waits.append(int(discover.limited()))
        now[0] += discover.limited() + 1                                     # po přestávce hned další odmítnutí
    assert waits == [90, 180, 360, 720, 1440, 1800, 1800, 1800, 1800]        # u stropu zůstává, nezačíná od 90 s
    assert discover._distrust() > 0                                          # „nenašlo se“ se nevěří ještě chvíli po ní
    now[0] += discover.LIMIT_FORGET + 1
    discover._rate_limited()
    assert int(discover.limited()) == discover.LIMIT_WAIT                    # po dlouhém klidu zase krátká


def test_changing_preferences_keeps_series_progress(api):
    """Volba verze u dílu seriálu nese jeho stav zhlédnutí: změna jazyka nebo kvality ji nesmí smazat."""
    with app_api._library() as lib:
        lib.set_kv(app_api.CHOICE_PREFIX + "tv:77:2:5", "5:video-5:0000000000000005")
        lib.set_kv(app_api.CHOICE_PREFIX + "movie:11", "1:film:0000000000000001")
    api("settings_set", prefer_quality="1080")
    with app_api._library() as lib:
        assert app_api.app_discover._played_episodes(lib, 77, 2, ["5"]) == {"5": "5:video-5:0000000000000005"}
        assert lib.get_kv(app_api.CHOICE_PREFIX + "movie:11") is None        # u filmu se ruční volba zapomene


def test_minimum_quality_and_the_list_of_titles_are_shared_by_all_profiles(api, monkeypatch):
    """Seznam dostupných titulů je společný, a tak i nejnižší kvalita, podle které se sestavuje. Dřív ji
    měl každý profil svou: přepnutí profilu seznam přepočítalo, začalo ho znovu ověřovat a Diagnostika
    ukazovala v každém profilu jiná čísla."""
    discover = app_api.app_discover
    monkeypatch.setattr(app_api, "MIN_QUALITY_DEFAULT", "720")
    api("settings_set", min_quality="", prefer_language="subs")              # první profil: bez omezení, titulky
    discover._remember_availability("movie", 11, 2, "", 1999, {"d": "cz", "q": 0, "s": 0})
    restarted = []
    monkeypatch.setattr(app_api.app_index, "restart", lambda: restarted.append(1))
    before = app_api.app_index.stats()
    api("profile_add", avatar="owl", text="Druhý")
    assert api("profile_switch", id="p2")["close"]
    assert app_api._prefs()["minimum"] == ""                                 # nejnižší kvalita zůstala
    assert app_api._prefs()["language"] == "dub"                             # jazyk má profil svůj
    with app_api._library() as lib:
        known = discover._known(lib, "movie", [11])["movie:11"]
    assert not known.get("stale") and known["n"] == 2                        # seznam se nepřepočítal
    assert app_api.app_index.stats() == before and restarted == []           # Diagnostika ukazuje totéž
    api("settings_set", min_quality="1080")                                  # změna v druhém profilu…
    api("profile_switch", id="p1")
    assert app_api._prefs()["minimum"] == "1080"                             # …platí i v prvním
    assert app_api._prefs()["language"] == "subs"


def test_partly_failed_search_is_not_remembered_as_missing(api, monkeypatch):
    """Dva dotazy nic nenašly a další Přehraj.to odmítlo: to není „film tam není“."""
    discover = app_api.app_discover
    _clock(monkeypatch)
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/movie/11": {"id": 11, "title": "Pelíšky", "original_title": "Cosy Dens",
                                           "release_date": "1999-04-08", "runtime": 116}})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    asked = []

    def search(client, query, page, cursor):
        asked.append(query)
        if len(asked) > 2:
            raise app_api.provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")
        return [], ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search)
    page = api("discover_title", kind="movie", id=11, title="Pelíšky", year=1999, orig="Cosy Dens")
    assert page["detail"]["plot"].startswith("Hledání na Přehraj.to se teď nezdařilo") and page["retry"]
    assert _record("movie:11") is None                                       # nic se o titulu nezapsalo


def test_open_list_does_not_hammer_services_while_checks_cannot_run(api, monkeypatch):
    """Tituly, jejichž údaj zestárl, a Přehraj.to má přestávku: výpis je neřadí k ověření při každém
    obnovení znovu a iVysílání se kvůli nim zbytečně neptá."""
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 100 + n, "title": "Film %d" % n, "release_date": "2015-01-01"} for n in range(6)]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    for film in films:
        discover._remember_availability("movie", film["id"], 1, "", 2015, {"d": "cz", "q": 1080, "s": 0})
    discover.reverify_all()
    discover._LIMITED.update(until=__import__("time").time() + 1800, wait=1800, last=__import__("time").time())
    television, asked = [], _catalogue_search(monkeypatch, {})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: television.append(names[0]) or "")
    refreshes = []
    for _ in range(4):
        listing = api("discover", kind="movie", category="popular")
        refreshes.append(listing.get("refresh"))
        thread = discover._VERIFY["thread"]
        if thread:
            thread.join(10)
    assert len(listing["items"]) == 7                                        # nalezené tituly jsou dál vidět
    assert asked == [] and television == []                                  # a nikam se zbytečně neposílá nic
    assert refreshes[0] == discover.RECHECK_REFRESH and refreshes[1:] == [None, None, None]


def test_titles_waiting_only_for_a_setting_change_are_settled_once(api, monkeypatch):
    """Nalezené tituly, které čekaly na nové ověření jen kvůli změně nejnižší kvality (dřív i kvůli
    přepnutí profilu), a nastavení vyhovují, se znovu neověřují – byly to stovky dotazů navíc."""
    prefix = app_api.app_discover.AVAILABLE_PREFIX
    api("settings_set", min_quality="720")
    with app_api._library() as lib:
        lib.set_kv(prefix + "movie:21", json.dumps({"n": 2, "ts": 0, "r": 2, "q": 1080}))   # čeká zbytečně
        lib.set_kv(prefix + "movie:22", json.dumps({"n": 1, "ts": 0, "r": 2, "q": 480}))    # nestačí: ověří se
        lib.set_kv(prefix + "movie:23", json.dumps({"n": 0, "ts": 0, "r": 2, "x": 1, "q": 1080}))   # skrytý
    assert app_api.app_index._settle_waiting() == 1
    with app_api._library() as lib:
        assert json.loads(lib.get_kv(prefix + "movie:21"))["ts"] > 0
        assert json.loads(lib.get_kv(prefix + "movie:22"))["ts"] == 0
        assert json.loads(lib.get_kv(prefix + "movie:23"))["ts"] == 0
        lib.set_kv(prefix + "movie:24", json.dumps({"n": 2, "ts": 0, "r": 2, "q": 1080}))
    assert app_api.app_index._settle_waiting() == 0                          # jen jednou
    assert "už nečeká na nové ověření" in app_api.app_index.journal_text()


def test_background_crawl_does_not_try_extra_names(api, monkeypatch):
    """Na pozadí se u nenalezeného titulu nezkoušejí další názvy (slovenský…): zdvojnásobily by počet
    dotazů. Výpis, na který člověk čeká, je zkusí."""
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "_local_names", lambda kind, tmdb_id, known=(): ["Pelíšky SK"])
    asked = _catalogue_search(monkeypatch, {})
    discover._count_versions("movie", 11, ["Pelíšky"], 1999, {}, None, gentle=True)
    assert all("SK" not in query for query, _page in asked) and asked
    asked[:] = []
    discover._count_versions("movie", 11, ["Pelíšky"], 1999, {}, None)
    assert any("SK" in query for query, _page in asked)


def test_keepalive_restarts_a_stopped_crawl(api, monkeypatch):
    started = []
    monkeypatch.setattr(app_api.app_index, "start", lambda: started.append(1))
    assert api("index_keepalive") == {"active": False} and started == [1]


def test_background_check_follows_a_changed_minimum(api, monkeypatch):
    """Ověřování, které běží, když uživatel změní nejnižší kvalitu, nesmí uložit výsledky podle té původní."""
    import threading
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    monkeypatch.setattr(app_api, "MIN_QUALITY_DEFAULT", "720")
    api("settings_set", tmdb_key="k" * 32, min_quality="1080")
    films = [{"id": 300 + n, "title": "Film %d" % n, "release_date": "2015-01-01"} for n in range(8)]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}, "/tv/popular": {"results": []},
                             "/genre/movie/list": {"genres": []}, "/genre/tv/list": {"genres": []}})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    monkeypatch.setattr(discover, "_local_names", lambda kind, tmdb_id, known=(): [])
    monkeypatch.setattr(app_api.app_index, "start", lambda: False)
    gate = threading.Event()

    def search(client, query, page, cursor):
        gate.wait(5)
        return [_web(abs(hash(query)) % 1000 + 1, query + " CZ 720p", 6000)], ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search)
    api("discover", kind="movie", category="popular")                        # fronta osmi titulů, nejnižší 1080p
    thread = discover._VERIFY["thread"]
    api("settings_set", min_quality="720")                                   # uživatel mezitím požadavek zmírní
    gate.set()
    thread.join(20)
    with app_api._library() as lib:
        known = discover._known(lib, "movie", [film["id"] for film in films])
    assert not [key for key, entry in known.items() if entry["n"] == 0 and not entry.get("stale")]


def test_reverified_title_from_ivysilani_is_not_called_new(api, monkeypatch):
    discover = app_api.app_discover
    now = _clock(monkeypatch)
    discover._remember_availability("movie", 11, 0, "555", 1999, {"t": "Pelíšky", "rd": "1999-04-08", "d": "", "q": 0, "s": 0})
    discover._remember_availability("movie", 12, 0, "556", 1996, {"t": "Kolja", "rd": "1996-05-15", "d": "", "q": 0, "s": 0})
    now[0] += 3600
    discover.reverify_all()
    discover._remember_availability("movie", 11, 2, "555", 1999, {"d": "cz", "q": 1080, "s": 0})
    discover.minimum_changed(1080, 720)
    discover._remember_availability("movie", 12, 2, "556", 1996, {"d": "cz", "q": 720, "s": 0})
    assert "f" not in _record("movie:11") and "f" not in _record("movie:12")
    assert app_api.app_index._fresh("movie") == []


def test_viewed_list_is_not_given_up_when_the_crawl_holds_the_check(api, monkeypatch):
    """Plnění seznamu na pozadí zrovna drží ověřování: tituly otevřeného výpisu se kvůli tomu neoznačí
    za neověřitelné, ověří se hned, jak to jde. A plnění samo ustoupí, jakmile někdo čeká."""
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    monkeypatch.setattr(discover, "CHECK_WAIT", 0.2)
    api("settings_set", tmdb_key="k" * 32)
    films = [{"id": 200 + n, "title": "Film %d" % n, "release_date": "2015-01-01"} for n in range(4)]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1}})
    _catalogue_search(monkeypatch, {"Film 0 2015": [_web(1, "Film 0 2015 CZ 1080p", 6000)]})
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    monkeypatch.setattr(discover, "_local_names", lambda kind, tmdb_id, known=(): [])
    discover._CHECK_LOCK.acquire()
    try:
        api("discover", kind="movie", category="popular")
        __import__("time").sleep(0.6)                                        # pár marných pokusů o zámek
        assert discover._VERIFY["failed"] == {} and discover._VERIFY["thread"] is not None
        with app_api._library() as lib:
            state = app_api._state(lib)
        with pytest.raises(discover._Yield):                                 # plnění seznamu: někdo čeká → ustoupit
            discover._search_page(None, "Cokoli", 1, "", [0], paced=True, gentle=True)
    finally:
        discover._CHECK_LOCK.release()
    discover._VERIFY["thread"].join(10)
    listing = api("discover", kind="movie", category="popular")
    assert _titles(listing["items"]) == ["Seřadit", "Film 0"] and "status" not in listing


def test_audio_delay_is_kept_for_each_output(api):
    style = api("subtitle_style")
    assert style["audio_delays"] == {"auto": 0, "pcm": 0, "pcm_soft": 0, "stereo": 0}
    assert style["audio_delay"] == {"min": -300, "max": 600, "step": 25}
    assert api("audio_delay_set", mode="auto", ms=125)["audio_delays"]["auto"] == 125
    assert api("audio_delay_set", mode="pcm", ms=5000)["audio_delays"]["pcm"] == 600      # v mezích
    assert api("audio_delay_set", mode="stereo", ms=-999)["audio_delays"]["stereo"] == -300
    assert api("audio_delay_set", mode="nesmysl", ms=50)["audio_delays"] == api("subtitle_style")["audio_delays"]
    assert api("audio_delay_set", mode="auto", ms="x")["audio_delays"]["auto"] == 125      # nesmysl nic nezmění
    assert api("subtitle_style")["audio_delays"] == {"auto": 125, "pcm": 600, "pcm_soft": 0, "stereo": -300}


def test_backup_and_restore_bring_everything_back(api, tmp_path, monkeypatch):
    """Záloha nese katalog, historii, seznam dostupných titulů, nastavení, oblíbené i profily – jen ne heslo.
    Obnova vrátí stav z doby zálohy a přihlášení v zařízení nechá být."""
    import zipfile
    discover, backup = app_api.app_discover, app_api.app_backup
    monkeypatch.setattr(discover, "BACKGROUND", False)
    _fill()
    api("settings_set", username="ja@example.cz", password="tajne", prefer_quality="1080", min_quality="1080")
    discover._remember_availability("movie", 11, 2, "", 1999, {"t": "Pelíšky", "d": "cz", "q": 1080, "s": 0})
    discover._save_favorites({"movie:11": {"title": "Pelíšky"}})
    api("profile_add", avatar="owl", text="Druhý")
    api("progress_save", track=REF, position=600, total=6000, started=True)
    disk = tmp_path / "usb" / "Promitarna"
    saved = api("backup_export", dir=str(disk), version="0.24.1")
    assert "Heslo k Přehraj.to v ní není" in saved["message"] and saved["path"].endswith(".zip")
    with zipfile.ZipFile(saved["path"]) as archive:
        names = set(archive.namelist())
        settings = json.loads(archive.read("settings.json").decode("utf-8"))
    assert {"backup.json", "catalog.db", "settings.json", "favorites.json", "profiles.json"} <= names
    assert "password" not in settings and settings["username"] == "ja@example.cz"
    assert not [name for name in os.listdir(str(disk)) if name.endswith(".tmp")]

    listed = api("backup_list", dirs=[{"dir": str(disk), "label": "USB disk"}, {"dir": str(tmp_path / "neni")}])
    assert [entry["path"] for entry in listed["backups"]] == [saved["path"]]
    assert "verze 0.24.1" in listed["backups"][0]["title"] and listed["backups"][0]["title"].endswith("USB disk")

    # po záloze se všechno změní nebo ztratí
    with app_api._library() as lib, lib.conn:
        lib.clear()
        lib.conn.execute("DELETE FROM settings_kv WHERE k LIKE 'avail:%'")
    discover._save_favorites({})
    api("settings_set", password="nove-heslo", prefer_quality="best", min_quality="")
    restored = api("backup_import", path=saved["path"])
    assert restored["restart"] and "jsou obnovená" in restored["message"]
    with app_api._library() as lib:
        assert lib.count_videos() == 5 and lib.resume_points([REF])[REF][0] == 600
        assert discover._known(lib, "movie", [11])["movie:11"]["n"] == 2
    assert list(discover.load_favorites()) == ["movie:11"]
    now = app_api._settings()
    assert now["prefer_quality"] == "1080" and now["min_quality"] == "1080"
    assert now["password"] == "nove-heslo" and now["username"] == "ja@example.cz"   # přihlášení v zařízení zůstalo
    assert [profile["name"] for profile in app_api.app_extras._load()["profiles"]][-1] == "Druhý"

    # co zálohou není, se odmítne a nic nezmění; cizí soubory v archivu se neobnovují
    wrong = tmp_path / "promitarna-zaloha-x.zip"
    with zipfile.ZipFile(str(wrong), "w") as archive:
        archive.writestr("backup.json", json.dumps({"app": "neco-jineho"}))
        archive.writestr("catalog.db", b"x")
    assert api.raw("backup_import", path=str(wrong))["ok"] is False
    assert api("backup_list", dirs=[{"dir": str(tmp_path)}])["backups"] == []
    newer = tmp_path / "promitarna-zaloha-y.zip"
    with zipfile.ZipFile(saved["path"]) as source, zipfile.ZipFile(str(newer), "w") as archive:
        for name in source.namelist():
            data = source.read(name)
            if name == "backup.json":
                data = json.dumps(dict(json.loads(data.decode("utf-8")), schema=999)).encode("utf-8")
            archive.writestr(name, data)
        archive.writestr("../evil.json", "{}")
        archive.writestr("cookies.lwp", "x")
    answer = api.raw("backup_import", path=str(newer))
    assert answer["ok"] is False and "novější verze" in answer["error"]
    assert not os.path.exists(str(tmp_path / "evil.json"))


def test_failed_version_is_replaced_by_another_one(api, monkeypatch):
    """Verze, kterou přehrávač nepřehrál ani napodruhé: místo chyby se pustí další verze téhož titulu."""
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="heslo")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    found = {"Pelíšky 1999": [_web(71, "Pelisky 1999 CZ 1080p", 2 * hour, "4 GB"),
                              _web(72, "Pelisky 1999 CZ 720p", 2 * hour, "2 GB"),
                              _web(73, "Pelisky 1999 EN 720p", 2 * hour, "1 GB")]}
    _catalogue_search(monkeypatch, found)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/%s.mp4" % vid.split(":")[0], {}))
    first = api("discover_movie_play", **FILM)
    assert first["track"] == _web(71, "")["id"]
    second = api("play_other", track=first["track"], choice=first["choice"])
    assert second["track"] == _web(72, "")["id"] and second["choice"] == "movie:11"
    assert second["switched"].startswith("CZ") and "720p" in second["switched"]
    # nový pokus po chybě (nová adresa) musí pustit zase tuhle verzi, ne tu původní
    assert second["again"]["call"] == "play" and second["again"]["params"]["vid"] == second["track"]
    assert api(second["again"]["call"], **second["again"]["params"])["track"] == second["track"]
    with app_api._library() as lib:
        assert first["track"] in app_api._bad_videos(lib)                    # příště se nenabídne jako první
    third = api("play_other", track=second["track"], choice="movie:11", tried=[first["track"]])
    assert third["track"] == _web(73, "")["id"]
    # víc pokusů už ne (a když není z čeho brát, řekne to): přehrávač pak ukáže původní chybu
    again = api.raw("play_other", track=third["track"], choice="movie:11", tried=[first["track"], second["track"]])
    assert again["ok"] is False
    assert api.raw("play_other", track="1:x:0000000000000001", choice="")["ok"] is False   # video mimo Objevuj


def test_catalogue_is_copied_daily_and_a_broken_one_is_replaced(api, tmp_path, monkeypatch):
    health = app_api.app_health
    _fill()
    first = health.snapshot()
    assert first and health._healthy(first) and health.snapshot() is None    # čerstvá kopie: další se nedělá
    real = __import__("time").strftime
    for hour in range(4):                                                    # další dny: drží se jen poslední tři
        monkeypatch.setattr(health.time, "strftime", lambda form, *rest, hour=hour: "catalog-20270101-%02d0000.db" % hour
                            if form.startswith("catalog-") else real(form, *rest))
        assert health.snapshot(force=True)
    monkeypatch.setattr(health.time, "strftime", real)
    assert len(health._snapshots()) == 3 and "uložené" in health.snapshots_text()
    line = next(line for line in api("diagnostics")["lines"] if line["label"] == "Kopie katalogu")
    assert line["value"].startswith("3 uložené")

    catalog = str(tmp_path / "catalog.db")
    assert health.guard(str(tmp_path)) is None                               # zdravý katalog se nechává být
    for suffix in ("-wal", "-shm"):
        if os.path.exists(catalog + suffix):
            os.remove(catalog + suffix)
    with open(catalog, "r+b") as f:                                          # poškození uprostřed souboru
        f.seek(4096)
        f.write(b"\xff" * 8192)
    assert health._healthy(catalog) is False
    assert health.guard(str(tmp_path)) == "restored"
    with app_api._library() as lib:
        assert lib.count_videos() == 5                                       # katalog je zpátky z kopie
    assert os.path.exists(str(tmp_path / health.BROKEN))
    assert "obnoven z kopie" in api("diagnostics")["errors"][0]

    for copy in health._snapshots():                                         # žádná kopie: začne se s prázdným
        os.remove(copy)
    with open(catalog, "wb") as f:
        f.write(b"to neni databaze" * 300)
    assert health.guard(str(tmp_path)) == "emptied"
    with app_api._library() as lib:
        assert lib.count_videos() == 0


def test_site_test_says_whether_prehrajto_still_looks_as_expected(api, monkeypatch):
    discover, health = app_api.app_discover, app_api.app_health

    class Client(object):
        def get_streams(self, vid):
            return [{"quality": "ORIG", "url": "x"}, {"quality": "FHD", "resolution": "1080p", "url": "y"}], ""

    monkeypatch.setattr(discover, "_search_client", lambda timeout=8: Client())
    asked = _catalogue_search(monkeypatch, {query: [_web(n, "%s 1999 CZ 1080p" % query, 6000, "4 GB")]
                                            for n, query in enumerate(health.TEST_QUERIES, 1)})
    result = api("prehrajto_test")
    assert result["ok"] is True and [query for query, _page in asked] == list(health.TEST_QUERIES)
    assert [line["ok"] for line in result["lines"]] == [True, True, True, True, True]
    assert "2 verzí k přehrání" in result["lines"][3]["value"]
    line = next(line for line in api("diagnostics")["lines"] if line["label"] == "Zkouška Přehraj.to")
    assert line["ok"] is True and "odpovídá tak, jak aplikace čeká" in line["value"]

    # web vrací výsledky, ale bez délky a velikosti: jeho podoba se změnila
    _catalogue_search(monkeypatch, {query: [dict(_web(1, query, 0, ""), duration=0, size="")] for query in health.TEST_QUERIES})
    changed = api("prehrajto_test")
    assert changed["ok"] is False and "změnil podobu stránek" in changed["lines"][-1]["value"]
    _catalogue_search(monkeypatch, {})                                       # nevrací vůbec nic
    empty = api("prehrajto_test")
    assert empty["ok"] is False and "Hledání nevrací nic" in empty["lines"][-1]["value"]

    def refused(client, query, page, cursor):
        raise app_api.provider.TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")

    monkeypatch.setattr(app_api.provider, "search_catalog", refused)         # odmítnutí: jeden dotaz a dost
    limited = api("prehrajto_test")
    assert limited["ok"] is False and discover.limited() > 0 and len(limited["lines"]) == 2
    assert "neproběhla" in api("prehrajto_test")["lines"][0]["value"]        # během přestávky se web nezkouší


def test_report_is_saved_and_fits_a_code_for_the_phone(api, tmp_path, monkeypatch):
    health = app_api.app_health
    app_api._remember_error("přehrávač", "Pelíšky: zdroj neodpovídá – příliš dlouhá chyba " + "ž" * 400)
    device = [{"label": "Aplikace", "value": "0.26.0", "ok": None},
              {"label": "Zařízení", "value": "Homatics Box R 4K Plus, Android 14", "ok": None}]
    disk = tmp_path / "usb"
    report = api("diagnostics_report", device=device,
                 dirs=[{"dir": "", "label": "nic"}, {"dir": str(disk), "label": "USB disk"}])
    assert report["saved"].startswith("promitarna-hlaseni-") and report["where"] == "USB disk"
    with open(str(disk / report["saved"]), encoding="utf-8") as f:
        assert f.read() == report["text"]
    assert "Aplikace: 0.26.0" in report["text"] and "Seznam dostupných titulů" in report["text"]
    assert "ž" * 200 in report["text"]                                       # v souboru jsou chyby celé
    code = report["qr"]
    assert len(code) <= health.QR_LIMIT and all(ord(char) < 128 for char in code)   # kód jen ze základních znaků
    # Kód je rozepsaný e-mail bez adresáta: telefon otevře nový e-mail s textem hlášení (iPhone čistý
    # text jen ukáže a nedá zkopírovat; a když v něm najde „Prehraj.to“, nabídne místo textu web).
    assert code.startswith("mailto:?subject=Promitarna%20-%20hlaseni") and code.count("&") == 1
    assert not re.search(r"[\s#?/]", code.split("?", 1)[1])                 # jen znaky, které do odkazu smějí
    body = unquote(code.split("&body=", 1)[1])
    assert body.startswith("Promitarna ") and "Aplikace: 0.26.0" in body and "Pelisky: zdroj neodpovida" in body
    assert "\r\n! Prehraj.to: neprihlaseno" in body                          # co je špatně, je v kódu celé
    assert body.endswith("(zkraceno)")                                        # dlouhé hlášení se uřízne, ale vejde se
    short = health._mail("Kratky text & dalsi = 100 % #1?")
    assert unquote(short.split("&body=", 1)[1]) == "Kratky text & dalsi = 100 % #1?"
    # Fotoaparát iPhonu odkaz bez adresáta nepozná: adresát je e-mail účtu (hlášení si pošlete sami sobě).
    assert health._mail("x", "a@b.cz").startswith("mailto:a@b.cz?subject=")
    assert health._mail("x", "jmeno-bez-zavinace").startswith("mailto:?subject=")   # není e-mail: bez adresáta
    api("settings_set", username="a@b.cz", password="heslo")
    assert api("diagnostics_report", device=device, dirs=[])["qr"].startswith("mailto:a@b.cz?subject=")
    nowhere = api("diagnostics_report", device=device, dirs=[])
    assert nowhere["saved"] == "" and nowhere["qr"]                          # bez disku aspoň kód


def test_guide_describes_the_app_with_the_names_it_really_uses(api, monkeypatch):
    """Návod: každé téma je karta s textem. Názvy dlaždic, voleb a položek nabídek, o které se opírá,
    musejí být ty, které aplikace opravdu ukazuje – po přejmenování je potřeba upravit i návod."""
    guide = api("guide")
    assert guide["next"] is None and len(guide["items"]) >= 10
    assert all(item["target"]["type"] == "info" and len(item["target"]["params"]["text"]) > 200 and item["subtitle"]
               for item in guide["items"])
    assert len({item["title"] for item in guide["items"]}) == len(guide["items"])
    assert all(len(item["subtitle"]) <= 24 for item in guide["items"])       # delší popisek se na kartu nevejde
    text = "\n".join(item["target"]["params"]["text"] for item in guide["items"])
    tools = _section(api, "tools")["Nástroje"]
    for tile in tools:                                                       # dlaždice Nástrojů
        name = tile["title"].split(":")[0]
        if name != "Návod":
            assert name in text, name
    for _value, name in app_api.LANGUAGE_CHOICES + app_api.QUALITY_CHOICES:  # volby jazyka a kvality
        assert name in text, name
    for _value, name in app_api.MINIMUM_CHOICES:
        assert name.split(" (")[0] in text, name
    for choice in api("subtitle_style")["audio_outputs"]:                    # výstupy zvuku (aspoň ten doporučený)
        if choice["id"] == "pcm_soft":
            assert choice["title"] in text
    for words in ("Odebrat z Rozkoukaných", "Opravit přiřazení", "Označit jako zhlédnuté", "Stáhnout na disk",
                  "Verze na Přehraj.to", "Vše na Přehraj.to", "Nově na Přehraj.to", "Hledat ručně",
                  "Kam stahovat", "Přesunout vše na jiný disk", "Zkouška Přehraj.to", "Zkontrolovat aktualizace",
                  "Povolit zálohu, která přežije odinstalaci", "Velikost a pozadí", "Výstup zvuku", "Posun zvuku",
                  app_api.app_discover.OWN_PREFS % "filmu", "Podle nastavení aplikace", "vlastní nastavení"):
        assert words in text, words
    api("settings_set", tmdb_key="k" * 32)                                   # dlaždice výpisů Objevuj
    for tile in app_api.app_discover.home_tiles("movie"):
        assert tile["title"] in text, tile["title"]


def test_found_title_survives_misses_that_come_together(api, monkeypatch):
    """Titul, který na Přehraj.to byl, nevypadne kvůli dvěma neúspěchům krátce po sobě (tentýž výpadek),
    ani kvůli „nenašlo se“ těsně po tom, co Přehraj.to odmítalo dotazy."""
    discover = app_api.app_discover
    now = [2000000.0]
    monkeypatch.setattr(discover.time, "time", lambda: now[0])

    def entry():
        with app_api._library() as lib:
            return json.loads(lib.get_kv(discover.AVAILABLE_PREFIX + "movie:11"))

    discover._remember_availability("movie", 11, 2, "", 1999)                # starý záznam bez souhrnu verzí
    discover._remember_availability("movie", 11, 0, "", 1999)
    discover._remember_availability("movie", 11, 0, "", 1999)                # hned znovu: tentýž výpadek
    assert entry()["n"] == 2 and entry()["miss"] == 1 and entry()["d"] == ""
    with app_api._library() as lib:
        assert not discover._known(lib, "movie", [11])["movie:11"].get("stale")   # ověří se až za čas, ne pořád dokola
    now[0] += discover.MISS_TTL + 1
    discover._remember_availability("movie", 11, 0, "", 1999)                # s odstupem podruhé: ještě zůstává
    assert entry()["n"] == 2 and entry()["miss"] == 2
    now[0] += discover.MISS_TTL + 1
    discover._remember_availability("movie", 11, 0, "", 1999)                # potřetí: už tam není
    assert entry()["n"] == 0

    api("settings_set", tmdb_key="k" * 32)
    discover._remember_availability("movie", 12, 1, "", 1996, {"d": "cz", "q": 1080, "s": 0})
    now[0] += discover.AVAILABLE_TTL + 1                                     # údaj zestárl: ověří se znovu
    _catalogue_search(monkeypatch, {})                                       # hledání nic nevrací
    discover._LIMITED["until"] = now[0] - 60                                 # před minutou skončila přestávka po odmítnutí
    with app_api._library() as lib:
        state = app_api._state(lib)
    item = {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}
    assert discover._check_available("movie", [item], state)["movie:12"]["n"] == 1
    with app_api._library() as lib:
        assert "miss" not in json.loads(lib.get_kv(discover.AVAILABLE_PREFIX + "movie:12"))
    discover._LIMITED["until"] = 0.0
    discover._check_available("movie", [item], state)
    with app_api._library() as lib:
        assert json.loads(lib.get_kv(discover.AVAILABLE_PREFIX + "movie:12"))["miss"] == 1


def test_list_of_available_titles_fills_in_the_background(api, monkeypatch):
    # Aplikace si seznamy TMDB prochází předem: výpis je pak hned a nové tituly přibývají samy.
    index, discover = app_api.app_index, app_api.app_discover
    api("settings_set", tmdb_key="k" * 32)
    monkeypatch.setattr(index, "GAP", 0)
    monkeypatch.setattr(index, "LISTS", (({"category": "popular"}, 1),))
    monkeypatch.setattr(index, "GENRE_PAGES", 0)
    monkeypatch.setattr(index, "YEARS_BACK", {"movie": 0, "tv": 0})
    monkeypatch.setattr(index, "CZECHOSLOVAK_YEARS", 0)
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    today = __import__("time").strftime("%Y-%m-%d")
    films = [{"id": 11, "title": "Pelíšky", "original_title": "Pelisky", "release_date": "1999-04-08", "poster_path": "/p.jpg",
              "vote_average": 8.6},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"},
             {"id": 13, "title": "Novinka", "release_date": today, "poster_path": "/n.jpg"}]
    shows = [{"id": 77, "name": "Archa", "first_air_date": "1996-01-01"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/tv/popular": {"results": shows, "total_pages": 1}})
    found = {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)],
             "Novinka %s" % today[:4]: [_web(33, "Novinka %s SK dabing 2160p" % today[:4], 7000)],
             "Archa": [_web(34, "Archa 1x03 CZ dabing", 2700)]}
    asked = _catalogue_search(monkeypatch, found)
    assert index.step() == {"done": False, "checked": 3, "failed": False}    # filmy
    assert index.step() == {"done": True, "checked": 1, "failed": False}     # seriály; plán je projitý
    count = len(asked)
    listing = api("discover", kind="movie", category="popular")              # výpis je hned, bez ověřování
    assert _titles(listing["items"]) == ["Seřadit", "Pelíšky", "Novinka"] and len(asked) == count
    assert "refresh" not in listing
    assert [card["badges"] for card in listing["items"][1:]] == [["Přehraj.to", "CZ"], ["Přehraj.to", "SK", "4K"]]
    assert _titles(api("discover", kind="tv", category="popular")["items"]) == ["Seřadit", "Archa"]   # seriál podle dílu
    dubbed_list = api("discover", kind="movie", category="popular", dub="1")
    dubbed = dubbed_list["items"]
    assert _titles(dubbed) == ["Seřadit", "Pelíšky", "Novinka"] and dubbed_list["count"] == "2 tituly"
    assert index.step() == {"done": True, "checked": 0, "failed": False}     # do dalšího dne se neprochází znovu

    # novinka je „nově na Přehraj.to“; starý film nalezený poprvé ne
    fresh_list = api("discover_fresh")
    fresh = fresh_list["items"]
    assert _titles(fresh) == ["Seřadit", "Novinka"] and fresh[1]["target"]["params"]["id"] == 13
    assert (fresh[0]["subtitle"], fresh[0]["target"]["call"]) == ("Podle data přidání", "fresh_sorts")
    assert fresh_list["count"] == "1 titul"
    sorts = api("fresh_sorts", **fresh[0]["target"]["params"])["filters"][0]["options"]
    assert [option["title"] for option in sorts] == ["Podle data přidání", "Podle hodnocení", "Podle roku (nejnovější)"]
    assert _titles(api("discover_fresh", **sorts[1]["params"])["items"]) == ["Seřadit", "Novinka"]
    row = api("discover_fresh_row")["items"]
    assert _titles(row) == ["Novinka", "Zobrazit vše"] and row[1]["target"]["call"] == "discover_fresh"
    # Kolja tam nebyl a později přibyl: i ten je nový
    found["Kolja 1996"] = [_web(35, "Kolja.1996.CZ.720p", 6000)]
    now = __import__("time").time()
    monkeypatch.setattr(discover.time, "time", lambda: now + 11 * 86400)     # záznam o nenalezení mezitím zestárl
    monkeypatch.setattr(index.time, "time", lambda: now + 11 * 86400)
    assert index.step()["checked"] >= 1                                      # (iVysílání se u ostatních ptá znovu)
    assert _titles(api("discover_fresh")["items"]) == ["Seřadit", "Kolja", "Novinka"]
    line = next(line for line in api("diagnostics")["lines"] if line["label"] == "Seznam dostupných titulů")
    assert line["value"].startswith("prověřeno 4 titulů: k mání 4, nenalezeno 0; dnes prověřeno ")

    def broken(client, query, page, cursor):
        raise app_api.provider.ProviderError("výpadek")

    monkeypatch.setattr(app_api.provider, "search_catalog", broken)          # služba neodpovídá: stránka se nepřeskočí
    films.append({"id": 14, "title": "Další", "release_date": "2001-01-01"})
    monkeypatch.setattr(discover.time, "time", lambda: now + 13 * 86400)
    monkeypatch.setattr(index.time, "time", lambda: now + 13 * 86400)
    index._save(dict(index._state(), step=0))                                # znovu na stránce filmů
    assert index.step() == {"done": False, "checked": 0, "failed": True} and index._state()["step"] == 0


def test_title_that_always_fails_does_not_hold_the_crawl(api, monkeypatch):
    """Titul, jehož hledání pokaždé skončí chybou, ačkoli Přehraj.to jinak odpovídá, nesmí zadržet plnění
    seznamu na jedné stránce plánu: po několika pokusech se v tomto průchodu přeskočí."""
    index, discover = app_api.app_index, app_api.app_discover
    api("settings_set", tmdb_key="k" * 32)
    monkeypatch.setattr(index, "GAP", 0)
    monkeypatch.setattr(index, "LISTS", (({"category": "popular"}, 1),))
    monkeypatch.setattr(index, "GENRE_PAGES", 0)
    monkeypatch.setattr(index, "YEARS_BACK", {"movie": 0, "tv": 0})
    monkeypatch.setattr(index, "CZECHOSLOVAK_YEARS", 0)
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    films = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Podivný", "release_date": "2001-01-01"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/tv/popular": {"results": [], "total_pages": 1}})

    def search(client, query, page, cursor):
        if "Podivný" in query:
            raise app_api.provider.ProviderError("chyba na stránce výsledků")
        return [_web(32, "Pelisky.1999.CZ.1080p", 7000)] if "Pelíšky" in query else [], ""

    monkeypatch.setattr(app_api.provider, "search_catalog", search)
    results = [index.step() for _ in range(index.STUCK_MAX)]
    assert all(result["failed"] for result in results) and index._state()["step"] == 0
    assert index.step()["failed"] is False and index._state()["step"] >= 1     # stránka je hotová, jde se dál
    assert index._state()["stuck"] == {"movie:12": index.STUCK_MAX}


def test_night_mode_is_remembered_for_the_device(api):
    assert api("subtitle_style")["audio_night"] is False
    assert api("audio_night_set", on=True) == {"audio_night": True}
    assert api("subtitle_style")["audio_night"] is True
    api("profile_add", avatar="owl", text="Druhý")
    api("profile_switch", id="p2")
    assert api("subtitle_style")["audio_night"] is True                       # platí pro celé zařízení
    assert api("audio_night_set", on=False) == {"audio_night": False}


def test_playback_puts_background_checks_on_hold(api):
    discover = app_api.app_discover
    api("progress_save", track="1:x:0000000000000001", position=10, total=100)
    assert discover._VERIFY["hold"] > __import__("time").time() + 60 and app_api.app_index.busy()
    discover._VERIFY["hold"] = 0
    api("progress_save", track="1:x:0000000000000001", position=100, total=100, ended=True)
    assert discover._VERIFY["hold"] == 0                                      # konec přehrávání už nic neodkládá


def test_titles_the_site_itself_offers_join_the_list(api, monkeypatch):
    # Co nabízí samo Přehraj.to (úvodní stránka, výpisy z ní), se přiřadí k titulům a ověří běžnou cestou.
    index, discover = app_api.app_index, app_api.app_discover
    api("settings_set", tmdb_key="k" * 32)
    monkeypatch.setattr(index, "GAP", 0)
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    offered = [_web(71, "Duna 2021 CZ dabing 1080p", 9000, "9 GB", "HD"), _web(72, "Duna.2021.EN.2160p", 9000, "20 GB", "HD"),
               _web(73, "Archa S01E02 CZ", 2700), _web(74, "Neznamy soubor bez roku", 5000),
               _web(75, "Jiny film 1990 CZ", 5000), _web(76, "Duna 2021 CZ trailer", 120)]
    monkeypatch.setattr(app_api.provider, "offered", lambda client, max_pages=3: (
        offered, [("Úvodní stránka", "https://prehraj.to/", 4), ("Nejnovější", "https://prehraj.to/nove", 2)]))
    results = {"/search/movie": {"results": [
        {"id": 500, "title": "Duna", "original_title": "Dune", "release_date": "2021-09-15", "poster_path": "/d.jpg"},
        {"id": 501, "title": "Duna", "original_title": "Dune", "release_date": "1984-12-14"}]},
        "/search/tv": {"results": [{"id": 77, "name": "Archa", "first_air_date": "1996-01-01"}]}}
    calls = _fake_tmdb(monkeypatch, results)
    asked = _catalogue_search(monkeypatch, {"Duna 2021": offered[:2], "Archa": [offered[2]]})
    report = index.harvest()
    assert (report["files"], report["titles"], report["matched"]) == (6, 4, 2) and "error" not in report
    # „Jiny film 1990“ se k Duně (1984/2021) nepřiřadí: název nesedí; soubor bez roku se nepřiřazuje vůbec
    assert {params.get("query") for path, params in calls if path == "/search/movie"} == {"Duna", "Jiny film"}
    with app_api._library() as lib:
        known = discover._known(lib, "movie", [500, 501])
    assert known == {"movie:500": {"n": 2, "iv": "", "d": "cz", "q": 2160, "s": 0}}
    fresh = api("discover_fresh")["items"]                                   # nabídka webu = nově na Přehraj.to
    assert sorted(_titles(fresh)) == ["Archa", "Duna", "Seřadit"] and len(asked) >= 2
    duna = next(card for card in fresh if card["title"] == "Duna")
    assert duna["badges"] == ["Přehraj.to", "CZ", "4K"] and duna["target"]["params"]["id"] == 500
    assert index.harvest() is None                                           # hned znovu se web nečte
    line = next(line for line in api("diagnostics")["lines"] if line["label"] == "Nabídka Přehraj.to")
    assert "souborů 6, titulů 4, přiřazeno 2; stránky: Úvodní stránka (4), Nejnovější (2)" in line["value"]

    def broken(client, max_pages=3):
        raise app_api.provider.ProviderError("HTTP 503")

    monkeypatch.setattr(app_api.provider, "offered", broken)                 # výpadek: jen zpráva, nic se nerozbije
    assert index.harvest(force=True)["error"] == "HTTP 503"
    assert "nepodařilo se: HTTP 503" in index.harvest_text()
    assert sorted(_titles(api("discover_fresh")["items"])) == ["Archa", "Duna", "Seřadit"]


def test_audio_output_is_remembered_for_the_player(api):
    assert api("subtitle_style")["audio_output"] == "auto"                    # výchozí: prostorový zvuk se předává
    assert api("audio_output_set", mode="stereo") == {"audio_output": "stereo"}
    assert api("subtitle_style")["audio_output"] == "stereo"
    assert api("audio_output_set", mode="nesmysl") == {"audio_output": "stereo"}   # neznámá hodnota nic nezmění
    assert api("audio_output_set", mode="auto") == {"audio_output": "auto"}


def test_update_check_survives_the_github_rate_limit(api, monkeypatch):
    # „HTTP Error 403: rate limit exceeded“: rozhraní GitHubu omezuje dotazy na adresu; vydání se pak
    # zjistí z běžné stránky téhož repozitáře.
    import urllib.error
    asked = []

    class Page(_Response):
        def __init__(self, body, url):
            _Response.__init__(self, body)
            self.url = url

        def geturl(self):
            return self.url

    latest = ["https://github.com/petermikula-glitch/streamujtv/releases/tag/app-v0.20.0"]

    def urlopen(request, timeout=None):
        asked.append(request.full_url)
        if request.full_url == app_api.UPDATE_URL:
            raise urllib.error.HTTPError(request.full_url, 403, "rate limit exceeded", {}, None)
        if request.full_url == app_api.UPDATE_PAGE:
            return Page(b"<html></html>", latest[0])
        if request.full_url == app_api.UPDATE_NOTES % "0.20.0":
            return Page("Přepínač výstupu zvuku.\n".encode("utf-8"), request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(app_api.urllib.request, "urlopen", urlopen)
    found = api("update_check", current="0.19.0", force=True)
    assert found == {"available": True, "current": "0.19.0", "version": "0.20.0", "size": 0, "sha256": "",
                     "url": "https://github.com/petermikula-glitch/streamujtv/releases/download/app-v0.20.0/MojeVidea-0.20.0.apk",
                     "notes": "Přepínač výstupu zvuku."}
    assert asked == [app_api.UPDATE_URL, app_api.UPDATE_PAGE, app_api.UPDATE_NOTES % "0.20.0"]
    assert api("update_check", current="0.20.0", force=True) == {"available": False, "current": "0.20.0"}
    assert api("update_check", current="0.19.0") == {"available": False, "current": "0.19.0"}   # samo se hned znovu neptá
    latest[0] = "https://github.com/petermikula-glitch/streamujtv/releases"                    # stránka nic neřekla
    assert "nepodařilo ověřit" in api("update_check", current="0.19.0", force=True)["error"]


def test_title_page_offers_the_trailer_and_the_whole_description(api, monkeypatch):
    api("settings_set", tmdb_key="k" * 32)
    detail = dict(FILM_DETAIL, original_title="Cosy Dens", tagline="Léta šedesátá", production_countries=[{"name": "Česko"}],
                  credits={"cast": [{"name": "Miroslav Donutil"}, {"name": "Jiří Kodet"}],
                           "crew": [{"name": "Jan Hřebejk", "job": "Director"}, {"name": "Někdo", "job": "Producer"}]},
                  videos={"results": [
                      {"site": "YouTube", "key": "teaser1", "type": "Teaser", "iso_639_1": "cs", "official": True},
                      {"site": "YouTube", "key": "anglicky", "type": "Trailer", "iso_639_1": "en", "official": True},
                      {"site": "YouTube", "key": "cesky", "type": "Trailer", "iso_639_1": "cs", "official": False},
                      {"site": "Vimeo", "key": "jinde", "type": "Trailer", "iso_639_1": "cs"},
                      {"site": "YouTube", "key": "zakulisi", "type": "Featurette", "iso_639_1": "cs"}]})
    _fake_tmdb(monkeypatch, {"/movie/11": detail})
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "77")
    _catalogue_search(monkeypatch, {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000, "4 GB")]})
    page = api("discover_title", **FILM)
    assert _titles(page["actions"]) == ["Přehrát", "Stáhnout na disk", "V iVysílání", "Upoutávka", "Více o filmu",
                                        "Do oblíbených", "Další"]
    television, trailer, about = page["actions"][2:5]
    assert television["subtitle"] == "" and television["target"]["call"] == "ivys_play"   # krátký název vedle Přehrát
    assert trailer["target"] == {"type": "open", "call": "", "title": "", "params": {     # český trailer má přednost
        "youtube": "cesky",                                                  # přehraje ho aplikace sama a vrátí se
        "url": "https://www.youtube.com/watch?v=cesky",
        "packages": ["com.google.android.youtube.tv", "com.google.android.youtube"],
        "missing": "Upoutávku se nepodařilo otevřít: v televizi chybí aplikace YouTube."}}
    assert about["target"]["params"]["text"] == (
        "„Léta šedesátá“\n\n1999 · 86 % · 1 h 56 min · Komedie, Drama\n\nRodinná komedie.\n\n"
        "Původní název: Cosy Dens\nRežie: Jan Hřebejk\nHrají: Miroslav Donutil, Jiří Kodet\nZemě: Česko\n"
        "Žánr: Komedie, Drama")

    _catalogue_search(monkeypatch, {})                                       # na Přehraj.to není, jen v iVysílání
    with app_api._library() as lib, lib.conn:
        lib.conn.execute("DELETE FROM settings_kv WHERE k LIKE 'avail:%'")
    only = api("discover_title", **FILM)
    assert _titles(only["actions"]) == ["Přehrát v iVysílání", "Upoutávka", "Více o filmu", "Do oblíbených",
                                        "Hledat ručně", "Jazyk a kvalita u tohoto filmu"]   # není co přehrát z Přehraj.to
    assert only["actions"][0]["subtitle"] == "Česká televize"

    _fake_tmdb(monkeypatch, {"/movie/12": dict(FILM_DETAIL, id=12)})         # bez upoutávky tlačítko není
    assert "Upoutávka" not in _titles(api("discover_title", **dict(FILM, id=12))["actions"])


def test_what_was_verified_once_stays_in_the_list(api, monkeypatch):
    # Jednou nalezený titul se ukazuje dál, i když údaj zestárne; jen se potichu ověří znovu. A jedno
    # ani dvě nepovedená ověření ho ze seznamu nevyhodí – až třetí za sebou (s odstupem).
    discover = app_api.app_discover
    monkeypatch.setattr(discover, "BACKGROUND", True)
    api("settings_set", tmdb_key="k" * 32)
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    films = [{"id": 11, "title": "Pelíšky", "release_date": "1999-04-08"},
             {"id": 12, "title": "Kolja", "release_date": "1996-05-15"}]
    more = [{"id": 13, "title": "Samotáři", "release_date": "2000-04-20"}]

    def fetch(self, path, timeout, params):
        if path != "/movie/popular":
            raise app_api.TmdbError("TMDB: HTTP 404")
        return {"results": films if params.get("page") in (None, 1) else more, "total_pages": 2}

    monkeypatch.setattr(app_api.Tmdb, "_fetch", fetch)
    found = {"Pelíšky 1999": [_web(32, "Pelisky.1999.CZ.1080p", 7000)], "Samotáři 2000": [_web(33, "Samotari 2000 EN titulky", 7000)]}
    asked = _catalogue_search(monkeypatch, found)

    def settle():
        thread = discover._VERIFY["thread"]
        if thread:
            thread.join(10)

    def listing():
        return api("discover", kind="movie", category="popular")

    listing(), settle(), listing(), settle()
    assert _titles(listing()["items"]) == ["Seřadit", "Pelíšky", "Samotáři"]

    now = __import__("time").time()
    monkeypatch.setattr(discover.time, "time", lambda: now + 30 * 86400)     # o měsíc později: všechny údaje zestárly
    count = len(asked)
    later = listing()                                                        # výpis je hned celý, nic nezmizelo
    assert _titles(later["items"]) == ["Seřadit", "Pelíšky", "Samotáři"] and "status" not in later
    assert later["refresh"] == discover.RECHECK_REFRESH and later["next"] is None   # jen se potichu přeověřuje
    settle(), listing(), settle()
    assert len(asked) > count                                                # … a opravdu se ověřilo znovu
    fresh = listing()
    assert _titles(fresh["items"]) == ["Seřadit", "Pelíšky", "Samotáři"] and "refresh" not in fresh

    del found["Pelíšky 1999"]                                                # hledání ho jednou nenašlo (web nic nevrátil)
    monkeypatch.setattr(discover.time, "time", lambda: now + 60 * 86400)
    listing(), settle(), listing(), settle()
    once = listing()
    assert "Pelíšky" in _titles(once["items"])                               # zůstává, brzy se ověří znovu
    with app_api._library() as lib:
        assert discover._known(lib, "movie", [11])["movie:11"]["n"] == 1
    monkeypatch.setattr(discover.time, "time", lambda: now + 61 * 86400)     # i druhé ověření neuspělo: ještě zůstává
    listing(), settle(), listing(), settle()
    assert "Pelíšky" in _titles(listing()["items"])
    monkeypatch.setattr(discover.time, "time", lambda: now + 62 * 86400)     # a třetí: už tam není
    listing(), settle(), listing(), settle()
    assert "Pelíšky" not in _titles(listing()["items"])
    found["Pelíšky 1999"] = [_web(32, "Pelisky.1999.CZ.1080p", 7000)]        # a když se zase objeví, vrátí se
    monkeypatch.setattr(discover.time, "time", lambda: now + 80 * 86400)
    listing(), settle(), listing(), settle()
    assert "Pelíšky" in _titles(listing()["items"])


def test_dubbed_titles_come_first_and_everything_found_can_be_browsed(api, monkeypatch):
    index, discover = app_api.app_index, app_api.app_discover
    api("settings_set", tmdb_key="k" * 32)
    monkeypatch.setattr(discover, "_ivys_match", lambda kind, names, year: "")
    films = [{"id": 1, "title": "Anglicky", "release_date": "2020-01-01", "vote_average": 9.0},
             {"id": 2, "title": "Titulky", "release_date": "2020-01-01", "vote_average": 8.0},
             {"id": 3, "title": "Dabing", "release_date": "2020-01-01", "vote_average": 6.0},
             {"id": 4, "title": "Hvězda", "release_date": "2020-01-01", "vote_average": 5.0},
             {"id": 5, "title": "Nikde", "release_date": "2020-01-01"}]
    _fake_tmdb(monkeypatch, {"/movie/popular": {"results": films, "total_pages": 1},
                             "/tv/popular": {"results": [{"id": 9, "name": "Seriál", "first_air_date": "2019-01-01"}],
                                             "total_pages": 1}})
    _catalogue_search(monkeypatch, {
        "Anglicky 2020": [_web(1, "Anglicky 2020 EN 1080p", 7000)], "Titulky 2020": [_web(2, "Titulky 2020 CZ titulky", 7000)],
        "Dabing 2020": [_web(3, "Dabing 2020 CZ dabing 720p", 7000)],
        "Hvězda 2020": [_web(4, "Hvezda 2020 SK dabing 2160p", 7000)], "Seriál": [_web(9, "Serial S01E01 CZ", 2700)]})
    listing = api("discover", kind="movie", category="popular")["items"]
    # předvolba dabingu: dabované napřed, pak originály s titulky, pak ostatní; uvnitř pořadí TMDB
    assert _titles(listing) == ["Seřadit", "Dabing", "Hvězda", "Titulky", "Anglicky"]
    api("discover", kind="tv", category="popular")

    # seřazeno podle hodnocení: přesně podle procent na kartách, předvolba jazyka do toho nemluví;
    # a tytéž tituly jako bez řazení (dřív řazení vracelo z TMDB úplně jiný výběr)
    rated_list = api("discover", kind="movie", category="popular", sort="rating")
    rated = rated_list["items"]
    assert _titles(rated) == ["Seřadit", "Anglicky", "Titulky", "Dabing", "Hvězda"]
    assert [card["subtitle"] for card in rated[1:]] == ["2020 · 90 % · film", "2020 · 80 % · film",
                                                       "2020 · 60 % · film", "2020 · 50 % · film"]
    assert (rated[0]["subtitle"], rated_list["count"]) == ("Podle hodnocení", "4 tituly")

    everything = api("discover_index", kind="movie")                         # vše, co se našlo: i od nejlepší kvality
    assert _titles(everything["items"]) == ["Seřadit", "Hvězda", "Dabing", "Titulky", "Anglicky"]
    assert everything["next"] is None and everything["items"][0]["target"]["call"] == "index_sorts"
    assert _titles(api("discover_index", kind="movie", sort="rating")["items"]) == [
        "Seřadit", "Anglicky", "Titulky", "Dabing", "Hvězda"]
    everything["items"] = everything["items"][1:]
    assert everything["items"][0]["badges"] == ["Přehraj.to", "SK", "4K"]
    assert _titles(api("discover_index", kind="tv")["items"]) == ["Seřadit", "Seriál"]
    assert len(api("discover_index")["items"]) == 6                          # filmy i seriály dohromady (+ Seřadit)
    tile = index.catalogue_tile("movie")
    assert tile["subtitle"] == "K mání 4 – seznam se plní sám" and tile["target"]["call"] == "discover_index"
    tiles = _section(api, "discover")["Objevuj"]                             # v Objevuj: filmy i seriály dohromady
    assert _titles(tiles) == ["Filmy", "Seriály", "Všechny filmy a seriály", "Hledat"]
    assert tiles[2]["subtitle"] == "K mání 5 · seřadit podle hodnocení nebo roku"
    assert (tiles[2]["target"]["call"], tiles[2]["target"]["params"]) == ("discover_index", {})
    everything_list = api("discover_index")
    assert (everything_list["items"][0]["subtitle"], everything_list["count"]) == ("Doporučené (jazyk a kvalita)", "5 titulů")
    monkeypatch.setattr(index, "INDEX_PAGE", 3)
    first = api("discover_index", kind="movie")
    assert len(first["items"]) == 4 and first["next"]["params"] == {"kind": "movie", "offset": 3}   # + Seřadit
    assert _titles(api("discover_index", **first["next"]["params"])["items"]) == ["Anglicky"]

    api("settings_set", prefer_language="subs")                              # předvolba titulků: ty napřed
    assert _titles(api("discover", kind="movie", category="popular")["items"])[1] == "Titulky"
    assert _titles(api("discover_index", kind="movie")["items"])[1] == "Titulky"
    api("settings_set", prefer_language="any")                               # je to jedno: pořadí TMDB
    assert _titles(api("discover", kind="movie", category="popular")["items"]) == [
        "Seřadit", "Anglicky", "Titulky", "Dabing", "Hvězda"]


def test_background_plan_walks_year_by_year(api, monkeypatch):
    index = app_api.app_index
    api("settings_set", tmdb_key="k" * 32)
    _fake_tmdb(monkeypatch, {"/genre/movie/list": {"genres": [{"id": 35, "name": "Komedie"}]},
                             "/genre/tv/list": {"genres": []}})
    plan = index._plan()
    year = __import__("time").localtime().tm_year
    assert plan[0] == ("movie", {"category": "popular", "kind": "movie"}, 1)          # nejdřív co je na očích
    assert ("movie", {"kind": "movie", "origin": "CZ|SK|XC", "year": str(year)}, 1) in plan   # česká tvorba po letech
    assert ("movie", {"kind": "movie", "genre": "35"}, 3) in plan
    years = [step for step in plan if set(step[1]) == {"kind", "year"}]
    assert years[0] == ("movie", {"kind": "movie", "year": str(year)}, 1) and years[1][0] == "tv"   # od nejnovějších
    assert ("movie", {"kind": "movie", "year": str(year - 39)}, 3) in years
    assert ("tv", {"kind": "tv", "year": str(year - 30)}, 1) not in years and len(plan) == len(set(map(str, plan)))


def test_clearing_history_forgets_searches_and_playback_but_keeps_the_rest(api, monkeypatch):
    """Nástroje → Smazat historii: hledané výrazy, Rozkoukané, Naposledy přehrané, značky zhlédnuto
    a iVysílání zmizí; katalog a oblíbené zůstanou."""
    _fill(2)
    with app_api._library() as lib:
        videos = lib.count_videos(visible_only=True)
    monkeypatch.setattr(app_api, "prehrajto_search", lambda params: {"items": [], "next": None})
    api("search", q="pelisky")
    api("progress_save", track=REF, position=600, total=6000, started=True)
    episode = "11:archa:%016x" % 11
    api("watched_set", vid=episode, watched=True)
    with app_api._library() as lib:
        lib.ivys_touch("777", "9", "Díl 3", "Vyprávěj", "")
        lib.ivys_record_play("777", 300, 3000)
        lib.set_kv(app_api.app_discover.RECENT_SERIES, "[1399]")
    tile = next(card for card in _section(api, "tools")["Nástroje"] if card["title"] == "Smazat historii")
    assert tile["target"]["call"] == "history_clear" and tile["target"]["confirm"]["title"] == "Smazat historii?"
    assert api("history_clear")["toast"] == "Historie hledání a přehrávání je smazaná."
    assert api("search_history") == {"rows": []}
    assert list(_section(api, "progress")) == ["Rozkoukané"]                 # jen nápověda, ani Historie
    with app_api._library() as lib:
        assert lib.resume_points() == {} and lib.watched() == set() and lib.ivys_progress() == []
        assert lib.get_kv(app_api.app_discover.RECENT_SERIES) == "[]"
        assert lib.count_videos(visible_only=True) == videos                # katalog zůstal


def test_favorites_play_at_once_downloaded_film_from_disk_and_series_where_it_stopped(api, monkeypatch, tmp_path):
    """Oblíbené z Objevuj: klepnutí rovnou přehrává. Stažený film ze souboru (štítek „Staženo“, bez hledání
    na Přehraj.to), seriál od dílu, na kterém se skončilo (totéž tlačítko jako na stránce seriálu)."""
    _fill(1)
    file = tmp_path / "pelisky.mkv"
    file.write_bytes(b"x")
    with app_api._library() as lib:
        lib.set_kv(app_api.ART_PREFIX + REF, "movie:11")
    app_api._save_downloads({REF: {"vid": REF, "title": "Pelíšky", "state": "done", "path": str(file),
                                   "subtitles": [], "added": 1}})
    api("favorite_toggle", **app_api.app_discover.tmdb_favorite("movie", 11, "Pelíšky", 1999, "", "Pelisky"))
    api("favorite_toggle", **app_api.app_discover.tmdb_favorite("tv", 77, "Archa", 1996, "", ""))
    rows = _section(api, "favorites")
    film, series = rows["Filmy"][0], rows["Seriály"][0]
    assert film["badges"][0] == "Staženo" and film["target"]["call"] == "discover_movie_play"
    searched = []
    monkeypatch.setattr(app_api.app_discover, "_find_versions", lambda *args, **kwargs: searched.append(args) or [])
    stream = api(film["target"]["call"], **film["target"]["params"])
    assert stream["local"] is True and stream["url"] == str(file) and stream["choice"] == "movie:11"
    assert searched == []                                                   # stažený film se nehledá

    assert series["target"]["call"] == "discover_series_play" and series["target"]["params"]["resume"] is True
    assert [item["title"] for item in series["menu"]][:2] == ["Pokračovat ve sledování", "Vybrat díl"]
    assert series["menu"][1]["target"]["type"] == "title"                  # výběr dílu na stránce seriálu
    episode = {"type": "play", "call": "discover_episode_play",
               "params": {"kind": "tv", "id": 77, "title": "Archa", "season": 2, "episode": 4, "resume": True}}
    monkeypatch.setattr(app_api.app_discover, "_series_rows",
                        lambda params, state, last=None: ([], {"title": "Pokračovat", "target": episode}, None))
    played = []
    monkeypatch.setattr(app_api.app_discover, "discover_episode_play", lambda params: played.append(params) or {"ok": 1})
    api(series["target"]["call"], **series["target"]["params"])
    assert played == [episode["params"]]                                    # rozkoukaný díl 2×04


def test_sorted_list_shows_the_same_titles_in_a_new_order(api, monkeypatch):
    # Seřadit: tytéž tituly jako bez řazení (z několika stránek TMDB), jen jinak seřazené; TMDB se
    # na řazení neptá (vrátilo by jiný výběr) a karty jdou po celých dávkách.
    api("settings_set", tmdb_key="k" * 32, prefer_language="any")
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "")
    pages = {1: [{"id": 1, "title": "Starší", "release_date": "2001-05-01", "vote_average": 7.0, "popularity": 50},
                 {"id": 2, "title": "Nikde", "release_date": "2024-01-01", "vote_average": 9.9, "popularity": 40}],
             2: [{"id": 3, "title": "Nový", "release_date": "2020-02-01", "vote_average": 6.04, "popularity": 30},
                 {"id": 4, "title": "Nejlepší", "release_date": "1990-01-01", "vote_average": 8.5, "popularity": 20},
                 {"id": 5, "title": "Bez data", "vote_average": 5.0, "popularity": 10}]}
    asked = []

    def fetch(self, path, timeout, params):
        asked.append((path, dict(params)))
        if path != "/movie/popular":
            raise app_api.TmdbError("TMDB: HTTP 404")
        return {"results": pages.get(params.get("page") or 1, []), "total_pages": 2}

    monkeypatch.setattr(app_api.Tmdb, "_fetch", fetch)
    _catalogue_search(monkeypatch, {"Starší 2001": [_web(1, "Starsi.2001.CZ.1080p", 7000)],
                                    "Nový 2020": [_web(3, "Novy.2020.CZ.1080p", 7000)],
                                    "Nejlepší 1990": [_web(4, "Nejlepsi.1990.CZ.1080p", 7000)],
                                    "Bez data": [_web(5, "Bez.data.CZ.1080p", 7000)]})
    plain = api("discover", kind="movie", category="popular")
    assert _titles(plain["items"]) == ["Seřadit", "Starší", "Nový", "Nejlepší", "Bez data"]
    rated = api("discover", kind="movie", category="popular", sort="rating")
    assert _titles(rated["items"]) == ["Seřadit", "Nejlepší", "Starší", "Nový", "Bez data"]   # 85, 70, 60, 50 %
    newest = api("discover", kind="movie", category="popular", sort="new")
    assert _titles(newest["items"]) == ["Seřadit", "Nový", "Starší", "Nejlepší", "Bez data"]  # bez data na konec
    assert all("sort_by" not in params for path, params in asked if path == "/movie/popular"), asked
    assert newest["items"][0]["target"]["params"] == {"kind": "movie", "category": "popular", "sort": "new"}
    same = api("discover", kind="movie", category="popular", sort="popular")   # výchozí pořadí = bez řazení
    assert _titles(same["items"]) == _titles(plain["items"])

    monkeypatch.setattr(app_api.app_discover, "SORT_WINDOW", 3)               # karty po dávkách
    first = api("discover", kind="movie", category="popular", sort="rating")
    assert _titles(first["items"]) == ["Seřadit", "Nejlepší", "Starší", "Nový"]
    assert first["next"]["params"] == {"kind": "movie", "category": "popular", "sort": "rating", "offset": 3}
    rest = api("discover", **first["next"]["params"])
    assert _titles(rest["items"]) == ["Bez data"] and rest["next"] is None


def test_title_only_in_ivysilani_stays_listed_while_it_is_rechecked(api, monkeypatch):
    # Prošlý údaj o iVysílání platí dál, jen se ověří znovu: titul, který je jen v iVysílání, z výpisu nezmizí.
    discover = app_api.app_discover
    now = [3000000.0]
    monkeypatch.setattr(discover.time, "time", lambda: now[0])
    discover._remember_availability("tv", 77, 0, "12", 2009)
    now[0] += discover.AVAILABLE_TTL + 1
    with app_api._library() as lib:
        known = discover._known(lib, "tv", [77])
    assert known["tv:77"]["iv"] == "12" and known["tv:77"]["tv_stale"]
    assert discover._there(known, "tv", {"id": 77}) and not discover._fresh(known, "tv:77")


def test_newest_titles_join_a_list_chosen_by_country(api, monkeypatch):
    # Letošní československý seriál má v TMDB zatím malou oblíbenost a mezi prvními stránkami výběru
    # by chyběl; do výpisu „Všechny“ se dostane z nejnovějších titulů a zařadí se podle oblíbenosti.
    api("settings_set", tmdb_key="k" * 32, prefer_language="any")
    monkeypatch.setattr(app_api.app_discover, "_ivys_match", lambda kind, names, year: "")
    year = __import__("time").strftime("%Y")
    old = [{"id": 1, "name": "Vyprávěj", "first_air_date": "2009-08-31", "popularity": 30.0},
           {"id": 2, "name": "Arabela", "first_air_date": "1979-01-01", "popularity": 10.0}]
    new = {"id": 3, "name": "Novinka", "first_air_date": year + "-02-01", "popularity": 20.0}
    asked = []

    def fetch(self, path, timeout, params):
        asked.append(dict(params))
        if path != "/discover/tv":
            raise app_api.TmdbError("TMDB: HTTP 404")
        newest = params.get("sort_by") == "first_air_date.desc"
        return {"results": [new] if newest else old, "total_pages": 1}

    monkeypatch.setattr(app_api.Tmdb, "_fetch", fetch)
    _catalogue_search(monkeypatch, {"Vyprávěj": [_web(1, "Vypravej S01E01 CZ", 3000)],
                                    "Arabela": [_web(2, "Arabela S01E01 CZ", 3000)],
                                    "Novinka": [_web(3, "Novinka S01E01 CZ", 3000)]})
    listing = api("discover", kind="tv", origin="CZ|SK|XC")
    assert _titles(listing["items"]) == ["Seřadit", "Vyprávěj", "Novinka", "Arabela"]   # podle oblíbenosti
    assert (listing["items"][0]["subtitle"], listing["count"]) == ("Podle oblíbenosti", "3 tituly")
    assert any(params.get("first_air_date.lte") for params in asked)         # jen uvedené do dneška
    newest = api("discover", kind="tv", origin="CZ|SK|XC", sort="new")
    assert _titles(newest["items"])[1] == "Novinka"


def test_version_with_other_content_is_remembered_and_replaced(api, monkeypatch):
    """„Tohle není ten film“: verze se k titulu už nenabídne (na stránce je až na konci jako „jiný
    obsah“), zmizí z Rozkoukaných a hned se pustí další nejlepší verze – i opakovaně, bez limitu pokusů."""
    api("settings_set", tmdb_key="k" * 32, username="a@b.cz", password="heslo")
    _fake_tmdb(monkeypatch, {"/movie/11": FILM_DETAIL})
    hour = 3600
    found = {"Pelíšky 1999": [_web(71, "Pelisky 1999 CZ 1080p", 2 * hour, "4 GB"),
                              _web(72, "Pelisky 1999 CZ 720p", 2 * hour, "2 GB"),
                              _web(73, "Pelisky 1999 EN 720p", 2 * hour, "1 GB")]}
    _catalogue_search(monkeypatch, found)
    monkeypatch.setattr(app_api, "_stream_link", lambda vid, fresh=False: ("https://cdn.x/%s.mp4" % vid.split(":")[0], {}))
    first = api("discover_movie_play", **FILM)
    assert first["track"] == _web(71, "")["id"]
    api("progress_save", track=first["track"], position=600, total=7200, started=True)
    second = api("play_wrong", track=first["track"], choice=first["choice"])
    assert second["track"] == _web(72, "")["id"] and second["switched"]
    assert second["again"]["params"]["vid"] == second["track"]
    with app_api._library() as lib:
        assert first["track"] not in lib.resume_points([first["track"]])     # z Rozkoukaných pryč
        assert first["track"] not in app_api._bad_videos(lib)               # nejde o chybu přehrávání
    assert api("discover_movie_play", **FILM)["track"] == second["track"]   # „Přehrát hned“ ji vynechá
    page = api("discover_title", **FILM)
    assert first["track"] not in [card["target"]["params"].get("vid") for card in page["rows"][0]["items"]]
    rest = next(row for row in page["rows"] if row["title"] == "Ukázky, neúplné soubory a jiný obsah")
    assert rest["items"][-1]["target"]["params"]["vid"] == first["track"]    # na stránce až na konci
    assert rest["items"][-1]["subtitle"].startswith("jiný obsah")
    third = api("play_wrong", track=second["track"], choice="movie:11", tried=[])
    assert third["track"] == _web(73, "")["id"]                              # klidně znovu
    nothing = api.raw("play_wrong", track=third["track"], choice="movie:11")
    assert nothing["ok"] is False and "není" in nothing["error"]           # jiná verze už není
    assert app_api.app_discover.wrong_versions("movie:11") == {_web(n, "")["id"] for n in (71, 72, 73)}
    assert app_api.app_discover.wrong_versions("movie:12") == set()          # platí jen pro tento titul
    assert api.raw("play_wrong", track="1:x:0000000000000001", choice="")["ok"] is False   # video mimo Objevuj
