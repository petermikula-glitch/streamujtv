# -*- coding: utf-8 -*-
"""Klient pro iVysílání České televize (veřejně dostupné pořady, bez přihlášení).

* Katalog: GraphQL ``https://api.ceskatelevize.cz/graphql/`` – stejné dotazy, jaké posílá
  web iVysílání (hledání pořadů a dílů, pořad, díly, bloky kategorie, nabídka kategorií).
* Přehrání: ``…/video/v1/playlist-vod/v1/stream-data/media/external/<idec>`` vrací adresu
  HLS streamu a titulky; živé vysílání ``…/playlist-live/v1/stream-data/channel/<kanál>``.
  Ověřeno proti živému webu 2026-11.

Chráněný obsah (DRM) doplněk nepřehrává: o stream žádá s ``canPlayDrm=false`` a když ho
Česká televize odmítne vydat (práva, území), předá uživateli její vysvětlení.

Modul nesmí importovat nic z Kodi, aby šel testovat mimo něj.
"""
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.ceskatelevize.cz"
GRAPHQL = API + "/graphql/"
VOD = API + "/video/v1/playlist-vod/v1/stream-data/media/external/%s"
LIVE = API + "/video/v1/playlist-live/v1/stream-data/channel/%s"
USER_AGENT = "Kodi add-on plugin.video.prehrajto.mojevidea (iVysilani)"
TIMEOUT = 20
PAGE = 40  # tolik položek na dotaz web povoluje

# kanály živého vysílání (ověřeno 2026-11)
LIVE_CHANNELS = (("CH_1", "ČT1"), ("CH_2", "ČT2"), ("CH_24", "ČT24"), ("CH_4", "ČT sport"),
                 ("CH_5", "ČT :D"), ("CH_6", "ČT art"), ("CH_7", "ČT3"))

Q_MENU = "query CategoryMenu{menu{children{code title url categoryId}code title url categoryId}}"
Q_CATEGORY = (
    "query GetCategoryL1Blocks($categoryId:String!){category(categoryId:$categoryId){id title categoryId "
    "categoryBlocks{title categoryId slug hasMoreItems cards{show{id slug title isPlayable showType year "
    "shortDescription images{card(width:480 height:270) poster(width:480)}}}}}}")
Q_SEARCH_SHOWS = (
    "query SearchShows($limit:PaginationAmount!$offset:Int!$search:String!$onlyPlayable:Boolean){"
    "searchShows(limit:$limit offset:$offset keyword:$search onlyPlayable:$onlyPlayable){totalCount "
    "items{code id description genres{id title}imageUrl(width:480 height:270)sidp title playable "
    "cardLabels{topLeft center}}}}")
Q_SEARCH_EPISODES = (
    "query SearchEpisodes($limit:PaginationAmount!$offset:Int!$search:String!$onlyPlayable:Boolean){"
    "searchEpisodes(limit:$limit offset:$offset keyword:$search onlyPlayable:$onlyPlayable){totalCount "
    "items{id description idec sidp playable previewImage(width:480)title duration genres{id title}code "
    "showTitle cardLabels{topLeft center bottomRight}labels{icon text textLong:text(short:false)}}}}")
Q_SHOW = (
    "query Show($id:String!){programme(legacySidpOrIdp:$id){id programmeId idec slug title showType year "
    "isPlayable playableEpisodeCount defaultSort description shortDescription durationText "
    "seasons{id title} countriesOfOrigin{title} images{card(width:480 height:270) poster(width:480)}}}")
Q_EPISODES = (
    "query GetEpisodes($programmeId:String!$seasonId:String$limit:PaginationAmount!$offset:Int!"
    "$orderBy:EpisodeOrderByType!$onlyPlayable:Boolean){programme(programmeId:$programmeId){id "
    "episodes(seasonId:$seasonId limit:$limit offset:$offset orderBy:$orderBy onlyPlayable:$onlyPlayable)"
    "{totalCount items{id title description duration playable season{title} "
    "images{card(width:480 height:270)} date{datetime channelName prefixText}}}}}")


class IvysilaniError(Exception):
    pass


class NotPlayable(IvysilaniError):
    """Česká televize stream nevydala (práva pro území, vypršelá licence, chráněný obsah)."""


def _image(images, *names):
    for name in names:
        value = (images or {}).get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _text(value):
    """Text bez pevných mezer (web je používá v názvech i popisech)."""
    return value.replace("\u00a0", " ").strip() if isinstance(value, str) else ""


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _show_card(show):
    """Pořad z bloku kategorie nebo z detailu → jednotný tvar pro obrazovky."""
    images = show.get("images") or {}
    return {
        "id": str(show.get("id") or ""),
        "title": _text(show.get("title")),
        "description": _text(show.get("shortDescription")) or _text(show.get("description")),
        "playable": bool(show.get("isPlayable")),
        "type": _text(show.get("showType")),
        "year": _int(show.get("year")),
        "image": _image(images, "card"),
        "poster": _image(images, "poster"),
    }


def _playability_message(payload):
    """Vysvětlení České televize, proč video nejde přehrát (z chybové odpovědi)."""
    message = payload.get("message") if isinstance(payload, dict) else None
    labels = (message or {}).get("labels") if isinstance(message, dict) else None
    if isinstance(labels, dict):
        for key in ("additionalText", "textLong", "textShort"):
            if _text(labels.get(key)):
                return _text(labels[key])
    return ""


class Client(object):
    def __init__(self, log=None, opener=None, timeout=TIMEOUT):
        self.log = log or (lambda m: None)
        self.opener = opener or urllib.request.build_opener()
        self.timeout = timeout

    # --- HTTP -------------------------------------------------------------
    def _request(self, url, payload=None):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        self.log("ivysilani: %s %s" % ("POST" if data else "GET", url.split("?")[0]))
        try:
            resp = self.opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=self.timeout)
            try:
                body = resp.read().decode("utf-8", "replace")
            finally:
                resp.close()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace"))
            except (ValueError, OSError, http.client.HTTPException):
                detail = {}
            finally:
                e.close()
            reason = _playability_message(detail)
            if reason:
                raise NotPlayable(reason) from e
            raise IvysilaniError("iVysílání odpovědělo chybou HTTP %s" % e.code) from e
        except (OSError, http.client.HTTPException) as e:
            raise IvysilaniError("iVysílání je nedostupné: %s" % e) from e
        try:
            parsed = json.loads(body)
        except ValueError as e:
            raise IvysilaniError("iVysílání vrátilo neplatnou odpověď") from e
        if not isinstance(parsed, dict):
            raise IvysilaniError("iVysílání vrátilo neočekávanou odpověď")
        return parsed

    def graphql(self, name, query, variables=None):
        data = self._request(GRAPHQL, {"operationName": name, "query": query, "variables": variables or {}})
        errors = data.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            raise IvysilaniError("iVysílání: %s" % (_text((first or {}).get("message")) or "chyba dotazu"))
        result = data.get("data")
        if not isinstance(result, dict):
            raise IvysilaniError("iVysílání vrátilo neočekávanou odpověď")
        return result

    # --- katalog ----------------------------------------------------------
    def menu(self):
        """Hlavní kategorie, které mají vlastní stránku s bloky pořadů."""
        items = []
        for entry in self.graphql("CategoryMenu", Q_MENU).get("menu") or []:
            ids = entry.get("categoryId") if isinstance(entry, dict) else None
            if isinstance(ids, list) and ids and _text(entry.get("title")):
                items.append({"id": str(ids[-1]), "title": _text(entry["title"])})
        return items

    def category(self, category_id):
        """{"title", "blocks": [{"title", "shows": [...]}]} – pořady, které web v kategorii nabízí."""
        category = self.graphql("GetCategoryL1Blocks", Q_CATEGORY,
                                {"categoryId": str(category_id)}).get("category") or {}
        blocks = []
        for block in category.get("categoryBlocks") or []:
            shows = [_show_card(card["show"]) for card in block.get("cards") or []
                     if isinstance(card, dict) and isinstance(card.get("show"), dict)]
            shows = [show for show in shows if show["id"] and show["title"]]
            if shows:
                blocks.append({"title": _text(block.get("title")), "shows": shows})
        return {"title": _text(category.get("title")), "blocks": blocks}

    def search_shows(self, text, offset=0, limit=PAGE):
        result = self.graphql("SearchShows", Q_SEARCH_SHOWS, {
            "limit": limit, "offset": offset, "search": text, "onlyPlayable": True}).get("searchShows") or {}
        shows = []
        for item in result.get("items") or []:
            if not isinstance(item, dict) or not item.get("sidp"):
                continue
            shows.append({"id": str(item["sidp"]), "title": _text(item.get("title")),
                          "description": _text(item.get("description")), "playable": bool(item.get("playable")),
                          "type": "", "year": 0, "image": _text(item.get("imageUrl")), "poster": "",
                          "genres": [_text(g.get("title")) for g in item.get("genres") or [] if isinstance(g, dict)]})
        return _int(result.get("totalCount")), shows

    def search_episodes(self, text, offset=0, limit=PAGE):
        result = self.graphql("SearchEpisodes", Q_SEARCH_EPISODES, {
            "limit": limit, "offset": offset, "search": text, "onlyPlayable": True}).get("searchEpisodes") or {}
        episodes = []
        for item in result.get("items") or []:
            if not isinstance(item, dict) or not item.get("idec"):
                continue
            episodes.append({"idec": str(item["idec"]), "title": _text(item.get("title")),
                             "show_title": _text(item.get("showTitle")), "show_id": str(item.get("sidp") or ""),
                             "description": _text(item.get("description")), "duration": _int(item.get("duration")),
                             "playable": bool(item.get("playable")), "image": _text(item.get("previewImage")),
                             "season": "", "date": ""})
        return _int(result.get("totalCount")), episodes

    def show(self, show_id):
        programme = self.graphql("Show", Q_SHOW, {"id": str(show_id)}).get("programme")
        if not isinstance(programme, dict):
            raise IvysilaniError("Pořad v iVysílání neexistuje")
        show = _show_card(programme)
        show.update({
            "programme_id": _text(programme.get("programmeId")),
            "idec": _text(programme.get("idec")),
            "episode_count": _int(programme.get("playableEpisodeCount")),
            "default_sort": _text(programme.get("defaultSort")) or "newest",
            "seasons": [{"id": _text(s.get("id")), "title": _text(s.get("title"))}
                        for s in programme.get("seasons") or [] if isinstance(s, dict) and _text(s.get("id"))],
            "countries": [_text(c.get("title")) for c in programme.get("countriesOfOrigin") or []
                          if isinstance(c, dict) and _text(c.get("title"))],
        })
        return show

    def episodes(self, programme_id, season_id=None, offset=0, order="newest", limit=PAGE):
        programme = self.graphql("GetEpisodes", Q_EPISODES, {
            "programmeId": programme_id, "seasonId": season_id or None, "limit": limit, "offset": offset,
            "orderBy": order or "newest", "onlyPlayable": True}).get("programme") or {}
        result = programme.get("episodes") or {}
        episodes = []
        for item in result.get("items") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            date = item.get("date") if isinstance(item.get("date"), dict) else {}
            episodes.append({"idec": str(item["id"]), "title": _text(item.get("title")), "show_title": "",
                             "show_id": "", "description": _text(item.get("description")),
                             "duration": _int(item.get("duration")), "playable": bool(item.get("playable")),
                             "image": _image(item.get("images"), "card"),
                             "season": _text((item.get("season") or {}).get("title")),
                             "date": _text(date.get("datetime"))[:10]})
        return _int(result.get("totalCount")), episodes

    # --- přehrání ---------------------------------------------------------
    @staticmethod
    def _stream_query():
        return "?" + urllib.parse.urlencode({"canPlayDrm": "false", "quality": "web", "streamType": "hls"})

    def vod(self, idec):
        """{"url", "subtitles": [adresy .vtt], "duration", "title"} nebo NotPlayable."""
        if not str(idec).isdigit():
            raise IvysilaniError("neplatné číslo pořadu")
        data = self._request(VOD % idec + self._stream_query())
        streams = [s for s in data.get("streams") or [] if isinstance(s, dict) and _text(s.get("url"))]
        if not streams:
            raise NotPlayable(_playability_message(data) or "Tento pořad nelze v doplňku přehrát "
                              "(chráněný obsah nebo dočasně nedostupné video).")
        stream = streams[0]
        subtitles = []
        for track in stream.get("subtitles") or []:
            for item in (track.get("files") or []) if isinstance(track, dict) else []:
                if isinstance(item, dict) and item.get("format") == "vtt" and _text(item.get("url")):
                    subtitles.append(_text(item["url"]))
        return {"url": _text(stream["url"]), "subtitles": subtitles,
                "duration": _int(stream.get("duration") or data.get("duration")), "title": _text(data.get("title"))}

    def live(self, channel):
        if channel not in dict(LIVE_CHANNELS):
            raise IvysilaniError("neznámý kanál")
        data = self._request(LIVE % channel + self._stream_query())
        urls = data.get("streamUrls") if isinstance(data.get("streamUrls"), dict) else {}
        if urls.get("isBlocked") or not _text(urls.get("main")):
            raise NotPlayable(_playability_message(data) or "Živé vysílání tohoto kanálu teď není dostupné.")
        return {"url": _text(urls["main"]), "subtitles": [], "duration": 0,
                "title": _text(data.get("title")) or dict(LIVE_CHANNELS)[channel]}
