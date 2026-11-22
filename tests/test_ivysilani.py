"""iVysílání: klient (dotazy, přehrávací rozhraní) a obrazovky. Bez sítě – odpovědi jsou podvržené."""
import io
import json
import urllib.error

import pytest

import ivysilani


def _show(show_id, title, kind="series", year=None):
    return {"id": show_id, "slug": "%s-x" % show_id, "title": title, "isPlayable": True, "showType": kind,
            "year": year, "shortDescription": "Popis pořadu %s" % title,
            "images": {"card": "https://img/%s-card.jpg" % show_id, "poster": "https://img/%s-poster.jpg" % show_id}}


def _episode(idec, title, season="I. řada"):
    return {"id": idec, "title": title, "description": "Popis dílu", "duration": 1500, "playable": True,
            "season": {"title": season}, "images": {"card": "https://img/%s.jpg" % idec},
            "date": {"datetime": "2026-04-10T19:00:00.000Z", "channelName": "ČT1", "prefixText": "Poslední vysílání"}}


VOD = {"title": "Na plovárně | Terry Pratchett", "duration": 1326, "showTitle": "Na\u00a0plovárně",
       "episodeTitle": "Terry Pratchett | 14", "previewImageUrl": "https://img/preview.jpg", "playability": {"sidp": 13},
       "streams": [{
    "url": "https://cdn.example/vod.m3u8?token=abc", "duration": 1326,
    "subtitles": [{"language": "ces", "files": [{"url": "https://ctfs/s.vtt", "format": "vtt"},
                                                {"url": "https://ctfs/s.ttml", "format": "ttml"}]}]}]}
GEO = {"error": {"type": "ResourceLicenceError"}, "message": {"isPlayable": False, "labels": {
    "textShort": "ČT nemá práva pro vaše území",
    "additionalText": "ČT bohužel nemá práva pro poskytování tohoto obsahu na vašem území."}}}


class FakeService(object):
    """Odpovídá jako rozhraní iVysílání a zapisuje si, na co se klient ptal."""

    def __init__(self):
        self.calls = []
        self.requests = []
        self.episodes = [_episode("2%014d" % n, "%d/50 Díl %d" % (n, n)) for n in range(1, 51)]

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append(request)
        if "/graphql/" in url:
            body = json.loads(request.data.decode("utf-8"))
            self.calls.append((body["operationName"], body["variables"]))
            return io.BytesIO(json.dumps(self.graphql(body["operationName"], body["variables"])).encode("utf-8"))
        self.calls.append(("stream", url))
        if "/media/external/999" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(json.dumps(GEO).encode("utf-8")))
        if "/media/external/500" in url:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO(b"<html>"))
        if "/media/external/777" in url:
            return io.BytesIO(json.dumps({"title": "Chráněný", "streams": []}).encode("utf-8"))
        if "/channel/" in url:
            return io.BytesIO(json.dumps({"title": "ČT24", "streamUrls": {
                "main": "https://cdn.example/live.m3u8?token=live", "isBlocked": False,
                "timeshift": "https://cdn.example/shift.m3u8?token=shift"}}).encode("utf-8"))
        return io.BytesIO(json.dumps(VOD).encode("utf-8"))

    def graphql(self, name, variables):
        if name == "CategoryMenu":
            return {"data": {"menu": [
                {"code": "serialy", "title": "Seriály", "url": None, "categoryId": ["3976"], "children": []},
                {"code": "filmy", "title": "Filmy", "url": None, "categoryId": ["3947"], "children": [
                    {"code": "krimi", "title": "Krimi", "url": None, "categoryId": ["3947", "3958"]},
                    {"code": "x", "title": "Bez čísla", "url": "/x", "categoryId": None}]},
                {"code": "podcasty", "title": "Podcasty", "url": "/ivysilani/vypis/1858/", "categoryId": None}]}}
        if name == "GetCategoryL1Blocks":
            return {"data": {"category": {"id": "g", "title": "Film", "categoryId": variables["categoryId"],
                                          "categoryBlocks": [
                {"title": "Nejsledovanější", "cards": [{"show": _show("11", "Krakonošovo tajemství", "movie", "2022")},
                                                        {"show": _show("12", "Vyprávěj")}]},
                {"title": "Komedie", "cards": [{"show": _show("12", "Vyprávěj")},
                                               {"show": _show("13", "Na plovárně", "magazine")}]},
                {"title": "Prázdný blok", "cards": []}]}}}
        if name == "CategoryProgrammes":
            shows = [_show(str(200 + n), "Krimi %03d" % n, "movie" if n % 2 else "series") for n in range(130)]
            page = shows[variables["offset"]:variables["offset"] + variables["limit"]]
            return {"data": {"category": {"id": "g", "title": "Krimi", "categoryId": variables["categoryId"],
                                          "programmeFind": {"totalCount": len(shows), "items": page}}}}
        if name == "LiveBroadcastFind":
            return {"data": {"liveBroadcastFind": [
                {"id": "CH_1", "slug": "ct1", "current": {
                    "id": "a", "idec": "1", "sidp": "2", "title": "Pečení na\u00a0neděli", "description": "O pečení",
                    "startsAt": "2026-11-22T07:04:00.000Z", "endsAt": "2026-11-22T07:40:08.000Z",
                    "assignedToChannel": {"channelName": "ČT1"}, "previewImage": "https://img/live.jpg"},
                 "next": {"id": "b", "title": "Polopatě", "startsAt": "2026-11-22T07:40:00.000Z", "endsAt": None}},
                {"id": "CH_32", "slug": "ch-32", "current": {
                    "id": "c", "title": "SP v rychlobruslení", "startsAt": "2026-11-22T05:33:00.000Z",
                    "endsAt": "2026-11-22T08:45:00.000Z", "assignedToChannel": {"channelName": "ČT sport"}},
                 "next": None},
                {"id": "nesmysl", "current": None, "next": None}]}}
        if name == "SearchShows":
            if variables["search"] == "Vyprávěj":
                return {"data": {"searchShows": {"totalCount": 2, "items": [
                    {"sidp": "77", "title": "Vyprávěj dál", "playable": True},
                    {"sidp": "12", "title": "Vyprávěj", "playable": True}]}}}
            if variables["search"] == "nic":
                return {"data": {"searchShows": {"totalCount": 0, "items": []}}}
            items = [{"sidp": str(100 + n), "id": "x%d" % n, "code": "p%d" % n, "title": "Pořad %d" % n,
                      "description": "Popis", "playable": True, "imageUrl": "https://img/s%d.jpg" % n,
                      "genres": [{"id": "1", "title": "Zábava"}]}
                     for n in range(variables["offset"], min(55, variables["offset"] + variables["limit"]))]
            return {"data": {"searchShows": {"totalCount": 55, "items": items}}}
        if name == "SearchEpisodes":
            if variables["search"] == "nic":
                return {"data": {"searchEpisodes": {"totalCount": 0, "items": []}}}
            return {"data": {"searchEpisodes": {"totalCount": 1, "items": [{
                "idec": "211522160100014", "sidp": "13", "title": "Terry Pratchett", "showTitle": "Na plovárně",
                "description": "Britský spisovatel", "duration": 1326, "playable": True,
                "previewImage": "https://img/e.jpg"}]}}}
        if name == "Show":
            if variables["id"] == "404":
                return {"data": {"programme": None}}
            if variables["id"] == "11":
                show = dict(_show("11", "Krakonošovo tajemství", "movie", "2022"), programmeId="pm", idec="32129131031",
                            playableEpisodeCount=1, defaultSort="newest", seasons=[], countriesOfOrigin=[{"title": "Česko"}])
            elif variables["id"] == "13":
                show = dict(_show("13", "Na plovárně", "magazine"), programmeId="pp", idec="225542152000025",
                            playableEpisodeCount=50, defaultSort="newest", seasons=[], countriesOfOrigin=None)
            else:
                show = dict(_show("12", "Vyprávěj"), programmeId="pv", idec="213522161400022",
                            playableEpisodeCount=123, defaultSort="oldest", countriesOfOrigin=[{"title": "Česko"}],
                            seasons=[{"id": "season:1", "title": "I. řada"}, {"id": "group:9", "title": "Filmy o seriálu"}])
            return {"data": {"programme": show}}
        if name == "GetEpisodes":
            if variables["programmeId"] == "pm":
                return {"data": {"programme": {"id": "11", "episodes": {"totalCount": 0, "items": []}}}}
            items = self.episodes[variables["offset"]:variables["offset"] + variables["limit"]]
            return {"data": {"programme": {"id": "x", "episodes": {"totalCount": len(self.episodes), "items": items}}}}
        return {"errors": [{"message": "Unknown operation"}]}


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(ivysilani.urllib.request, "build_opener", lambda *handlers: fake)
    return fake


def test_client_reads_catalogue_and_normalises_texts(service):
    client = ivysilani.Client()
    assert client.menu() == [{"id": "3976", "title": "Seriály", "children": []},
                             {"id": "3947", "title": "Filmy", "children": [{"id": "3958", "title": "Krimi"}]}]
    listing = client.category_listing("3958", offset=80)
    assert (listing["title"], listing["total"], len(listing["shows"])) == ("Krimi", 130, 50)
    assert service.calls[-1] == ("CategoryProgrammes", {"categoryId": "3958", "limit": 80, "offset": 80})
    live = client.live_now()
    assert [(c["channel"], c["name"], c["title"], c["next_title"]) for c in live] == [
        ("CH_1", "ČT1", "Pečení na neděli", "Polopatě"), ("CH_32", "ČT sport", "SP v rychlobruslení", "")]
    assert live[0]["starts"] == 1795331040 and live[0]["next_starts"] == 1795333200 and live[1]["next_starts"] == 0
    category = client.category("3947")
    assert [(b["title"], [s["title"] for s in b["shows"]]) for b in category["blocks"]] == [
        ("Nejsledovanější", ["Krakonošovo tajemství", "Vyprávěj"]), ("Komedie", ["Vyprávěj", "Na plovárně"])]
    film = category["blocks"][0]["shows"][0]
    assert (film["type"], film["year"], film["poster"]) == ("movie", 2022, "https://img/11-poster.jpg")
    assert " " not in film["description"]

    total, shows = client.search_shows("pořad")
    assert total == 55 and len(shows) == ivysilani.PAGE and shows[0]["id"] == "100" and shows[0]["genres"] == ["Zábava"]
    total, episodes = client.search_episodes("pratchett")
    assert episodes == [{"idec": "211522160100014", "title": "Terry Pratchett", "show_title": "Na plovárně",
                         "show_id": "13", "description": "Britský spisovatel", "duration": 1326, "playable": True,
                         "image": "https://img/e.jpg", "season": "", "date": ""}]

    show = client.show("12")
    assert (show["programme_id"], show["default_sort"], show["episode_count"]) == ("pv", "oldest", 123)
    assert show["seasons"] == [{"id": "season:1", "title": "I. řada"}, {"id": "group:9", "title": "Filmy o seriálu"}]
    total, episodes = client.episodes("pv", "season:1", offset=40, order="oldest")
    assert total == 50 and len(episodes) == 10 and episodes[0]["season"] == "I. řada" and episodes[0]["date"] == "2026-04-10"
    assert service.calls[-1] == ("GetEpisodes", {"programmeId": "pv", "seasonId": "season:1", "limit": 40, "offset": 40,
                                                 "orderBy": "oldest", "onlyPlayable": True})
    with pytest.raises(ivysilani.IvysilaniError):
        client.show("404")
    with pytest.raises(ivysilani.IvysilaniError, match="Unknown operation"):
        client.graphql("Neznamy", "query Neznamy{x}")


def test_client_identifies_itself_and_never_asks_for_protected_streams(service):
    client = ivysilani.Client()
    stream = client.vod("211522160100014")
    assert stream == {"url": "https://cdn.example/vod.m3u8?token=abc", "subtitles": ["https://ctfs/s.vtt"],
                      "duration": 1326, "title": "Na plovárně | Terry Pratchett", "show_title": "Na plovárně",
                      "episode_title": "Terry Pratchett", "show_id": "13", "image": "https://img/preview.jpg"}
    assert client.live("CH_24")["url"] == "https://cdn.example/live.m3u8?token=live"
    assert client.live("CH_32", timeshift=True)["url"] == "https://cdn.example/shift.m3u8?token=shift"
    for request in service.requests:
        assert request.get_header("User-agent") == ivysilani.USER_AGENT
        assert not request.get_header("Origin") and not request.get_header("Referer")
    for _name, url in [call for call in service.calls if call[0] == "stream"]:
        assert "canPlayDrm=false" in url and "streamType=hls" in url

    with pytest.raises(ivysilani.NotPlayable, match="nemá práva"):
        client.vod("999")
    with pytest.raises(ivysilani.NotPlayable, match="chráněný obsah"):
        client.vod("777")
    with pytest.raises(ivysilani.IvysilaniError, match="HTTP 500"):
        client.vod("500")
    for bad in ("abc", "1 OR 1", "../x"):
        with pytest.raises(ivysilani.IvysilaniError):
            client.vod(bad)
    for bad in ("CH_x", "../CH_1", "CH_1?x=1"):
        with pytest.raises(ivysilani.IvysilaniError):
            client.live(bad)


def _labels():
    import xbmcplugin
    return [item.label for _url, item, _folder in xbmcplugin.ITEMS]


def _reset():
    import xbmcgui
    import xbmcplugin
    del xbmcplugin.ITEMS[:]
    del xbmcplugin.RESOLVED[:]
    del xbmcgui.NOTIFICATIONS[:]


def test_section_is_offered_only_when_enabled(plugin, service):
    import xbmcaddon
    import xbmcplugin
    plugin.root({"quiet": "1"})
    assert not any("ivys_menu" in url for url, _item, _folder in xbmcplugin.ITEMS)
    xbmcaddon.SETTINGS["ivysilani_enabled"] = "true"
    _reset()
    plugin.root({"quiet": "1"})
    assert plugin.url_for("ivys_menu") in [url for url, _item, _folder in xbmcplugin.ITEMS]
    _reset()
    plugin.search({"q": "Pelíšky", "online": "0"})
    assert xbmcplugin.ITEMS[-1][0] == plugin.url_for("ivys_search", q="Pelíšky")


def test_browsing_categories_shows_seasons_and_episode_pages(plugin, service):
    import xbmcplugin
    plugin.ivys_menu({})
    assert _labels() == ["Živé vysílání", "Hledat v iVysílání", "Seriály", "Filmy"]
    _reset()
    plugin.ivys_menu({})
    assert [name for name, _vars in service.calls].count("CategoryMenu") == 1  # podruhé z mezipaměti

    _reset()
    plugin.ivys_live({})
    # kanály s programem (i mimořádný přenos), pak ostatní kanály bez programu
    assert _labels() == ["ČT1 · Pečení na neděli", "ČT sport · SP v rychlobruslení", "ČT2", "ČT24", "ČT sport",
                         "ČT :D", "ČT art", "ČT3"]
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("ivys_play", channel="CH_32")
    first = xbmcplugin.ITEMS[0][1]
    assert "pak" in first.tag.data["plot"] and "Polopatě" in first.tag.data["plot"] and "O pečení" in first.tag.data["plot"]
    assert first.art["thumb"] == "https://img/live.jpg"
    assert first.context == [("Přehrát s časovým posunem", "PlayMedia(%s)" % plugin.url_for(
        "ivys_play", channel="CH_1", timeshift=1))]

    _reset()
    plugin.ivys_category({"cid": "3947"})
    assert [(url, folder) for url, _item, folder in xbmcplugin.ITEMS] == [
        (plugin.url_for("ivys_listing", cid="3947"), True),  # všechny pořady kategorie
        (plugin.url_for("ivys_listing", cid="3958"), True),  # podkategorie Krimi
        (plugin.url_for("ivys_play", show="11"), False),     # film se přehraje rovnou
        (plugin.url_for("ivys_show", id="12"), True),        # pořad uvedený ve dvou blocích jen jednou
        (plugin.url_for("ivys_show", id="13"), True)]
    assert _labels()[:2] == ["Všechny pořady v kategorii", "Krimi"]
    film = xbmcplugin.ITEMS[2][1]
    assert film.label == "Krakonošovo tajemství (2022)" and film.props["IsPlayable"] == "true"
    assert film.art["poster"] == "https://img/11-poster.jpg" and "Nejsledovanější" in film.tag.data["plot"]

    _reset()
    plugin.ivys_show({"id": "12"})
    assert _labels() == ["I. řada", "Filmy o seriálu", "Všechny díly (123)"]
    assert xbmcplugin.ITEMS[0][0] == plugin.url_for("ivys_show", id="12", season="season:1")

    _reset()
    plugin.ivys_show({"id": "12", "season": "season:1"})
    assert len(xbmcplugin.ITEMS) == 41 and xbmcplugin.ITEMS[0][1].label == "1/50 Díl 1"
    assert service.calls[-1][1]["seasonId"] == "season:1" and service.calls[-1][1]["orderBy"] == "oldest"
    assert xbmcplugin.ITEMS[-1][0] == plugin.url_for("ivys_show", id="12", season="season:1", offset=40)
    episode = xbmcplugin.ITEMS[0][1]
    assert episode.tag.data["duration"] == 1500 and episode.tag.data["tvshowtitle"] == "Vyprávěj"
    assert episode.art["thumb"].endswith(".jpg") and "I. řada · 2026-04-10" in episode.tag.data["plot"]

    _reset()
    plugin.ivys_show({"id": "12", "season": "season:1", "offset": "40"})
    assert len(xbmcplugin.ITEMS) == 10 and "action=ivys_show" not in xbmcplugin.ITEMS[-1][0]  # poslední stránka

    _reset()
    plugin.ivys_show({"id": "13"})           # pořad bez řad rovnou vypíše díly
    assert len(xbmcplugin.ITEMS) == 41 and service.calls[-1][1]["seasonId"] is None
    _reset()
    plugin.ivys_show({"id": "11"})           # film otevřený z hledání: jedna přehratelná položka
    assert [url for url, _item, _folder in xbmcplugin.ITEMS] == [plugin.url_for("ivys_play", idec="32129131031")]


def test_search_lists_shows_then_episodes_and_pages(plugin, service):
    import xbmc
    import xbmcgui
    import xbmcplugin
    del xbmc.KEYBOARD_INPUT[:]
    xbmc.KEYBOARD_INPUT.append("Pořad")
    plugin.ivys_search({})
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert urls[0] == plugin.url_for("ivys_show", id="100") and len(urls) == 40 + 1 + 1
    assert urls[40] == plugin.url_for("ivys_play", idec="211522160100014")
    assert xbmcplugin.ITEMS[40][1].label == "Na plovárně · Terry Pratchett"
    assert urls[-1] == plugin.url_for("ivys_search", q="Pořad", offset=40)

    _reset()
    plugin.ivys_search({"q": "Pořad", "offset": "40"})
    assert len(xbmcplugin.ITEMS) == 15 and all("ivys_show" in url for url, _item, _folder in xbmcplugin.ITEMS)

    _reset()
    plugin.ivys_search({"q": "nic"})
    assert not xbmcplugin.ITEMS and xbmcgui.NOTIFICATIONS
    xbmc.KEYBOARD_INPUT.append(None)          # zrušená klávesnice
    plugin.ivys_search({})


def test_playback_resolves_hls_with_subtitles_and_reports_blocked_content(plugin, service):
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
    xbmcgui.Window(10000).setProperty(plugin.PLAYING_PROPERTY, "stary zaznam")
    plugin.ivys_play({"channel": "CH_1"})
    assert not xbmcgui.Window(10000).getProperty(plugin.PLAYING_PROPERTY)  # živé vysílání se nesleduje
    plugin.ivys_play({"channel": "CH_1", "timeshift": "1"})
    assert xbmcplugin.RESOLVED[-1][1].path.endswith("token=shift")
    plugin.ivys_play({"idec": "211522160100014"})
    ok, item = xbmcplugin.RESOLVED[-1]
    assert ok and item.path == "https://cdn.example/vod.m3u8?token=abc" and item.mime == "application/vnd.apple.mpegurl"
    assert item.subtitles == ["https://ctfs/s.vtt"] and "inputstream" not in item.props
    pending = json.loads(xbmcgui.Window(10000).getProperty(plugin.PLAYING_PROPERTY))
    assert pending["vid"] == "ivys:211522160100014" and len(pending["path_hash"]) == 64

    xbmcaddon.SETTINGS.update(ivysilani_adaptive="true", subtitles="false")
    xbmc.CONDITIONS.add("System.AddonIsEnabled(inputstream.adaptive)")
    try:
        plugin.ivys_play({"channel": "CH_24"})
        ok, item = xbmcplugin.RESOLVED[-1]
        assert ok and item.path.endswith("token=live") and item.props["inputstream"] == "inputstream.adaptive"
        plugin.ivys_play({"show": "11"})      # film podle čísla pořadu
        ok, item = xbmcplugin.RESOLVED[-1]
        assert ok and not item.subtitles and service.calls[-1][1].split("?")[0].endswith("/32129131031")
    finally:
        xbmc.CONDITIONS.clear()

    del xbmcgui.NOTIFICATIONS[:]
    plugin.ivys_play({"idec": "999"})
    assert xbmcplugin.RESOLVED[-1][0] is False
    assert xbmcgui.NOTIFICATIONS == ["ČT bohužel nemá práva pro poskytování tohoto obsahu na vašem území."]
    plugin.ivys_play({"channel": "../x"})
    assert xbmcplugin.RESOLVED[-1][0] is False


def test_service_outage_shows_a_message_instead_of_failing(plugin, service, monkeypatch):
    import xbmcgui
    import xbmcplugin

    def down(request, timeout=None):
        raise OSError("síť je dole")

    monkeypatch.setattr(service, "open", down)
    for screen, params in ((plugin.ivys_category, {"cid": "3947"}), (plugin.ivys_show, {"id": "12"}),
                           (plugin.ivys_search, {"q": "x"})):
        _reset()
        screen(params)
        assert not xbmcplugin.ITEMS and "nedostupné" in xbmcgui.NOTIFICATIONS[-1]
    _reset()
    plugin.ivys_menu({})                      # živé vysílání a hledání zůstanou i bez kategorií
    assert _labels() == ["Živé vysílání", "Hledat v iVysílání"]


def test_full_category_listing_is_paged(plugin, service):
    import xbmcplugin
    plugin.ivys_listing({"cid": "3958"})
    assert len(xbmcplugin.ITEMS) == 81 and xbmcplugin.ITEMS[0][1].label == "Krimi 000"
    assert xbmcplugin.ITEMS[1][0] == plugin.url_for("ivys_play", show="201")       # film rovnou k přehrání
    assert xbmcplugin.ITEMS[-1][0] == plugin.url_for("ivys_listing", cid="3958", offset=80)
    _reset()
    plugin.ivys_listing({"cid": "3958", "offset": "80"})
    assert len(xbmcplugin.ITEMS) == 50 and "ivys_listing" not in xbmcplugin.ITEMS[-1][0]


def _service_module(plugin):
    import importlib.util
    from pathlib import Path
    path = Path(plugin.__file__).parents[2] / "service.py"
    spec = importlib.util.spec_from_file_location("prehrajto_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_programmes_are_tracked_resumed_and_listed_as_in_progress(plugin, service, monkeypatch):
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
    idec = "211522160100014"
    tracker = _service_module(plugin).PlaybackTracker()
    monkeypatch.setattr(xbmc.Player, "PLAYING", True)
    monkeypatch.setattr(xbmc.Player, "FILE", "https://cdn.example/vod.m3u8?token=abc")
    monkeypatch.setattr(xbmc.Player, "TIME", 0.0)
    monkeypatch.setattr(xbmc.Player, "TOTAL", 1326.0)

    plugin.ivys_play({"idec": idec})
    tracker.onAVStarted()
    assert tracker.vid == "ivys:" + idec
    monkeypatch.setattr(xbmc.Player, "TIME", 400.0)
    tracker.update()
    xbmc.BUILTINS.clear()
    tracker.onPlayBackStopped()
    with plugin.open_library() as lib:
        assert lib.ivys_states() == ({idec: (400.0, 1326.0)}, set())
        row = lib.ivys_progress()[0]
        assert (row["title"], row["show_title"], row["show_id"], row["image"]) == (
            "Terry Pratchett", "Na plovárně", "13", "https://img/preview.jpg")
        assert lib.resume_points() == {} and lib.history_videos() == []   # katalog Přehraj.to zůstal nedotčený
    assert not tracker.kodi_updates and tracker.next_offer is None

    _reset()
    plugin.ivys_menu({})
    assert "Rozkoukané (1)" in _labels()
    _reset()
    plugin.ivys_progress({})
    url, item, _folder = xbmcplugin.ITEMS[0]
    assert url == plugin.url_for("ivys_play", idec=idec) and item.label == "Na plovárně · Terry Pratchett · 30 %"
    assert item.tag.data["resumepoint"] == (400.0, 1326.0)
    assert item.context == [("Odebrat z rozkoukaných", "RunPlugin(%s)" % plugin.url_for("ivys_history_remove", idec=idec))]

    # stejný díl ve výpisu pořadu a ve společných Rozkoukaných doplňku
    service.episodes[0]["id"] = idec
    _reset()
    plugin.ivys_show({"id": "13"})
    assert xbmcplugin.ITEMS[0][1].tag.data["resumepoint"] == (400.0, 1326.0)
    _reset()
    plugin.in_progress({})
    assert not xbmcplugin.ITEMS
    xbmcaddon.SETTINGS["ivysilani_enabled"] = "true"
    plugin.in_progress({})
    assert [url for url, _item, _folder in xbmcplugin.ITEMS] == [plugin.url_for("ivys_play", idec=idec)]

    # nové spuštění pokračuje, „přehrát od začátku" ne; dokoukání označí díl za zhlédnutý
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "4", "?action=ivys_play&idec=" + idec, "resume:true"])
    assert xbmcplugin.RESOLVED[-1][1].tag.data["resumepoint"] == (400.0, 1326.0)
    plugin.run(["plugin://plugin.video.prehrajto.mojevidea/", "4", "?action=ivys_play&idec=" + idec, "resume:false"])
    assert "resumepoint" not in xbmcplugin.RESOLVED[-1][1].tag.data
    tracker.onAVStarted()
    monkeypatch.setattr(xbmc.Player, "TIME", 1320.0)
    tracker.update()
    xbmcaddon.SETTINGS["offer_next_episode"] = "true"
    tracker.onPlayBackEnded()
    assert tracker.next_offer is None            # další díl se nabízí jen u videí z katalogu
    with plugin.open_library() as lib:
        assert lib.ivys_states() == ({}, {idec})
    _reset()
    plugin.ivys_show({"id": "13"})
    assert xbmcplugin.ITEMS[0][1].tag.data["playcount"] == 1

    # chyba přehrávání pořadu z iVysílání nespouští obnovu odkazu z katalogu
    plugin.ivys_play({"idec": idec})
    xbmc.BUILTINS.clear()
    del xbmcgui.NOTIFICATIONS[:]
    tracker.onPlayBackError()
    assert not xbmc.BUILTINS and not xbmcgui.NOTIFICATIONS

    plugin.ivys_history_remove({"idec": idec})
    with plugin.open_library() as lib:
        assert lib.ivys_states() == ({}, set())


def test_my_shows_report_new_episodes_until_the_show_is_opened(plugin, service):
    import xbmc
    import xbmcplugin
    plugin.ivys_category({"cid": "3947"})
    show_item = next(item for url, item, _f in xbmcplugin.ITEMS if url == plugin.url_for("ivys_show", id="12"))
    assert show_item.context == [("Přidat do Mých pořadů", "RunPlugin(%s)" % plugin.url_for("ivys_favorite", id="12"))]
    xbmc.BUILTINS.clear()
    plugin.ivys_favorite({"id": "12"})
    assert xbmc.BUILTINS == ["Container.Refresh"]
    _reset()
    plugin.ivys_menu({})
    assert "Moje pořady (1)" in _labels()
    _reset()
    plugin.ivys_category({"cid": "3947"})
    show_item = next(item for url, item, _f in xbmcplugin.ITEMS if url == plugin.url_for("ivys_show", id="12"))
    assert show_item.context[0][0] == "Odebrat z Mých pořadů" and "remove=1" in show_item.context[0][1]

    _reset()
    plugin.ivys_favorites({})
    assert _labels() == ["Vyprávěj"]
    with plugin.open_library() as lib:
        lib.ivys_favorite_seen("12", 120)            # od poslední návštěvy přibyly tři díly
    _reset()
    plugin.ivys_favorites({})
    assert _labels() == ["Vyprávěj · nové díly: 3"]
    plugin.ivys_show({"id": "12"})
    _reset()
    plugin.ivys_favorites({})
    assert _labels() == ["Vyprávěj"]

    plugin.ivys_favorite({"id": "12", "remove": "1"})
    with plugin.open_library() as lib:
        assert lib.ivys_favorites() == []


def test_backup_keeps_ivysilani_history_and_favourites(plugin, service):
    with plugin.open_library() as lib:
        lib.ivys_touch("1", "13", "Díl", "Pořad", "https://img/x.jpg")
        lib.ivys_record_play("1", 300, 1200)
        lib.ivys_favorite_add("12", "Vyprávěj", seen_count=5)
        backup = lib.export_backup("addon")
        lib.ivys_remove_history()
        lib.ivys_favorite_remove("12")
        lib.restore_backup(json.loads(json.dumps(backup)), "addon")
        assert lib.ivys_states() == ({"1": (300.0, 1200.0)}, set())
        assert [(f["show_id"], f["seen_count"]) for f in lib.ivys_favorites()] == [("12", 5)]
        old = json.loads(json.dumps(backup))
        del old["tables"]["ivys_history"], old["tables"]["ivys_favorites"]   # záloha ze starší verze
        lib.restore_backup(old, "addon")
        assert lib.ivys_favorites() == [] and lib.ivys_progress() == []


def test_tmdb_detail_links_to_the_same_show_in_ivysilani(plugin, service, monkeypatch):
    import xbmcaddon
    import xbmcplugin

    class Tmdb(object):
        enabled = True

        def details(self, tmdb_id, want_series=False):
            title = "Vyprávěj" if tmdb_id == 1 else "Neznámý seriál"
            return {"tmdb_id": tmdb_id, "title": title, "orig_title": "", "year": 2009, "plot": "", "poster": "",
                    "fanart": "", "cast": [], "genres": [], "countries": [], "directors": [], "rating": 80,
                    "votes": 10, "runtime": 50, "collection_id": 0}

    monkeypatch.setattr(plugin.ui, "make_tmdb", lambda cached=True: Tmdb())
    plugin.tmdb_title({"kind": "tv", "tmdb_id": "1"})
    assert not any("ivys" in url for url, _item, _folder in xbmcplugin.ITEMS)   # sekce je vypnutá
    xbmcaddon.SETTINGS["ivysilani_enabled"] = "true"
    _reset()
    plugin.tmdb_title({"kind": "tv", "tmdb_id": "1"})
    linked = [(item.label, url) for url, item, _folder in xbmcplugin.ITEMS if "ivys" in url]
    assert linked == [("Otevřít v iVysílání: Vyprávěj", plugin.url_for("ivys_show", id="12"))]
    _reset()
    plugin.tmdb_title({"kind": "tv", "tmdb_id": "2"})
    linked = [(item.label, url) for url, item, _folder in xbmcplugin.ITEMS if "ivys" in url]
    assert linked == [("Hledat v iVysílání: Neznámý seriál", plugin.url_for("ivys_search", q="Neznámý seriál"))]
