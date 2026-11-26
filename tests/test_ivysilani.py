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


def test_protected_stream_request_and_licence_detection():
    assert "canPlayDrm=false" in ivysilani.Client._stream_query() and "streamType=hls" in ivysilani.Client._stream_query()
    protected = ivysilani.Client._stream_query(True)
    assert "canPlayDrm=true" in protected and "streamType=dash" in protected
    assert "canPlayDRM=true" in protected                                    # oba zápisy: název není zdokumentovaný
    refusal = {"message": {"status": {"name": "UNSUPPORTED_GEOLOCATION"}, "labels": {"textLong": "Nelze"}}}
    assert ivysilani._playability_status(refusal) == "UNSUPPORTED_GEOLOCATION"
    assert ivysilani._playability_status({}) == "" and ivysilani.NotPlayable("x").status == ""
    # adresa licence se bere jen z odpovědi rozhraní a jen pro Widevine
    listed = {"url": "https://cdn/x.mpd", "drm": [{"type": "fairplay", "laUrl": "https://fp/lic"},
                                                  {"type": "widevine", "laUrl": "https://wv/lic?t=1"}]}
    assert ivysilani.drm_license(listed) == {"license": "https://wv/lic?t=1"}
    named = {"url": "https://cdn/x.mpd", "protection": {"playready": {"licenseUrl": "https://pr/lic"},
                                                        "widevine": {"licenseUrl": "https://wv2/lic"}}}
    assert ivysilani.drm_license(named) == {"license": "https://wv2/lic"}
    plain = {"url": "https://cdn/x.mpd", "subtitles": [{"files": [{"url": "https://s/1.vtt"}]}]}
    assert ivysilani.drm_license(plain) is None
    shape = ivysilani.response_shape({"streams": [dict(listed, subtitles=[])], "title": "t"})
    assert shape == "{streams[{drm[{laUrl, type}], url}], title}"            # jen názvy polí, žádné hodnoty
