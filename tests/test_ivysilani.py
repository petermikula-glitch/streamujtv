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


VOD = {"title": "Na plovárně | Terry Pratchett", "duration": 1326, "streams": [{
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
                "main": "https://cdn.example/live.m3u8?token=live", "isBlocked": False}}).encode("utf-8"))
        return io.BytesIO(json.dumps(VOD).encode("utf-8"))

    def graphql(self, name, variables):
        if name == "CategoryMenu":
            return {"data": {"menu": [
                {"code": "serialy", "title": "Seriály", "url": None, "categoryId": ["3976"], "children": []},
                {"code": "filmy", "title": "Filmy", "url": None, "categoryId": ["3947"], "children": []},
                {"code": "podcasty", "title": "Podcasty", "url": "/ivysilani/vypis/1858/", "categoryId": None}]}}
        if name == "GetCategoryL1Blocks":
            return {"data": {"category": {"id": "g", "title": "Film", "categoryId": variables["categoryId"],
                                          "categoryBlocks": [
                {"title": "Nejsledovanější", "cards": [{"show": _show("11", "Krakonošovo tajemství", "movie", "2022")},
                                                        {"show": _show("12", "Vyprávěj")}]},
                {"title": "Komedie", "cards": [{"show": _show("12", "Vyprávěj")},
                                               {"show": _show("13", "Na plovárně", "magazine")}]},
                {"title": "Prázdný blok", "cards": []}]}}}
        if name == "SearchShows":
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
    assert client.menu() == [{"id": "3976", "title": "Seriály"}, {"id": "3947", "title": "Filmy"}]
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
                      "duration": 1326, "title": "Na plovárně | Terry Pratchett"}
    assert client.live("CH_24")["url"] == "https://cdn.example/live.m3u8?token=live"
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
    with pytest.raises(ivysilani.IvysilaniError):
        client.live("CH_99")


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
    assert _labels() == ["ČT1", "ČT2", "ČT24", "ČT sport", "ČT :D", "ČT art", "ČT3"]
    assert xbmcplugin.ITEMS[2][0] == plugin.url_for("ivys_play", channel="CH_24")

    _reset()
    plugin.ivys_category({"cid": "3947"})
    assert [(url, folder) for url, _item, folder in xbmcplugin.ITEMS] == [
        (plugin.url_for("ivys_play", show="11"), False),   # film se přehraje rovnou
        (plugin.url_for("ivys_show", id="12"), True),      # pořad uvedený ve dvou blocích jen jednou
        (plugin.url_for("ivys_show", id="13"), True)]
    film = xbmcplugin.ITEMS[0][1]
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
    plugin.ivys_play({"idec": "211522160100014"})
    ok, item = xbmcplugin.RESOLVED[-1]
    assert ok and item.path == "https://cdn.example/vod.m3u8?token=abc" and item.mime == "application/vnd.apple.mpegurl"
    assert item.subtitles == ["https://ctfs/s.vtt"] and "inputstream" not in item.props
    assert not xbmcgui.Window(10000).getProperty(plugin.PLAYING_PROPERTY)  # služba nesleduje cizí přehrávání

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
    plugin.ivys_play({"channel": "CH_99"})
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
