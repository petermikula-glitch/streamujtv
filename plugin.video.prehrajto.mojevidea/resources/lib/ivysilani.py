# -*- coding: utf-8 -*-
"""Klient pro iVysílání České televize (veřejně dostupné pořady, bez přihlášení).

* Katalog: GraphQL ``https://api.ceskatelevize.cz/graphql/`` – stejné dotazy, jaké posílá
  web iVysílání (hledání pořadů a dílů, pořad, díly, bloky kategorie, nabídka kategorií).
* Přehrání: ``…/video/v1/playlist-vod/v1/stream-data/media/external/<idec>`` vrací adresu
  HLS streamu a titulky; živé vysílání ``…/playlist-live/v1/stream-data/channel/<kanál>``.
  Ověřeno proti živému webu 2026-11.

Chráněný obsah (DRM) doplněk pro Kodi nepřehrává: o stream žádá s ``canPlayDrm=false`` a když ho
Česká televize odmítne vydat (práva, území), předá uživateli její vysvětlení. Aplikace pro Android TV
může požádat i o chráněný stream (``vod(idec, drm=True)``) a přehrát ho vestavěným Widevine zařízení –
výhradně s adresou licence, kterou rozhraní samo vrátí. Územní omezení se neobchází.

Modul nesmí importovat nic z Kodi, aby šel testovat mimo něj.
"""
import calendar
import http.client
import json
import re
import time
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
LISTING_PAGE = 80  # úplný výpis kategorie stránkuje web po osmdesáti
CHANNEL_RE = re.compile(r"CH_\d{1,3}$")

# kanály živého vysílání (ověřeno 2026-11)
LIVE_CHANNELS = (("CH_1", "ČT1"), ("CH_2", "ČT2"), ("CH_24", "ČT24"), ("CH_4", "ČT sport"),
                 ("CH_5", "ČT :D"), ("CH_6", "ČT art"), ("CH_7", "ČT3"))

Q_MENU = "query CategoryMenu{menu{children{code title url categoryId}code title url categoryId}}"
Q_CATEGORY = (
    "query GetCategoryL1Blocks($categoryId:String!){category(categoryId:$categoryId){id title categoryId "
    "categoryBlocks{title categoryId slug hasMoreItems cards{show{id slug title isPlayable showType year "
    "shortDescription images{card(width:480 height:270) poster(width:480)}}}}}}")
Q_LISTING = (
    "query CategoryProgrammes($categoryId:String!$limit:PaginationAmount!$offset:Int!){category(categoryId:$categoryId)"
    "{id title categoryId programmeFind(limit:$limit offset:$offset){totalCount items{id slug title isPlayable "
    "showType year shortDescription images{card(width:480 height:270) poster(width:480)}}}}}")
Q_LIVE = (
    "query LiveBroadcastFind{liveBroadcastFind{id slug current{id idec sidp title description startsAt endsAt "
    "assignedToChannel{channelName} previewImage(width:480)} next{id title startsAt endsAt}}}")
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
    """Česká televize stream nevydala (práva pro území, vypršelá licence, chráněný obsah).

    ``status`` je označení stavu z odpovědi rozhraní (např. ``UNSUPPORTED_GEOLOCATION``), je-li známé.
    """

    def __init__(self, message, status=""):
        super(NotPlayable, self).__init__(message)
        self.status = status


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


def _timestamp(value):
    """ISO čas z rozhraní (UTC) → unixový čas, 0 když chybí."""
    try:
        return calendar.timegm(time.strptime(_text(value)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
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


def _playability_status(payload):
    """Označení stavu z chybové odpovědi (``message.status.name``) – pro diagnostiku."""
    message = payload.get("message") if isinstance(payload, dict) else None
    status = (message or {}).get("status") if isinstance(message, dict) else None
    return _text(status.get("name")) if isinstance(status, dict) else ""


def drm_license(payload):
    """Adresa licenčního serveru Widevine, pokud ji odpověď rozhraní obsahuje.

    Tvar odpovědi u chráněných pořadů není zdokumentovaný, proto se hledá obecně: objekt, který se
    hlásí k Widevine (názvem pole nebo hodnotou), a v něm adresa https pod polem pro licenci.
    """
    def address(node):
        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key).lower()
                if isinstance(value, str) and value.startswith("https://") and (
                        "licen" in name or name in ("laurl", "la_url", "serverurl", "server_url", "url")):
                    return value
            for value in node.values():
                found = address(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = address(value)
                if found:
                    return found
        return ""

    def walk(node):
        if isinstance(node, dict):
            marked = any("widevine" in str(key).lower() for key in node) or any(
                isinstance(value, str) and "widevine" in value.lower() for value in node.values())
            if marked:
                widevine = [value for key, value in node.items() if "widevine" in str(key).lower()]
                found = address(widevine) or address({k: v for k, v in node.items()
                                                      if str(k).lower() not in ("url", "subtitles")})
                if found:
                    return found
            for key, value in node.items():
                if str(key).lower() == "subtitles":
                    continue
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found:
                    return found
        return ""

    found = walk(payload) or _plain_license(payload)
    return {"license": found} if found else None


def _plain_license(node):
    """Záložně: adresa pod polem pojmenovaným po licenci, i když se objekt nehlásí k Widevine.

    Větve označené jinou ochranou (FairPlay, PlayReady) se přeskakují.
    """
    if isinstance(node, dict):
        text = " ".join(str(key).lower() for key in node) + " " + " ".join(
            value.lower() for value in node.values() if isinstance(value, str))
        if ("fairplay" in text or "playready" in text) and "widevine" not in text:
            return ""
        for key, value in node.items():
            name = str(key).lower()
            if isinstance(value, str) and value.startswith("https://") and (
                    "licen" in name or name in ("laurl", "la_url")):
                return value
        for key, value in node.items():
            if str(key).lower() not in ("subtitles", "analytics", "adocean"):
                found = _plain_license(value)
                if found:
                    return found
    elif isinstance(node, list):
        for value in node:
            found = _plain_license(value)
            if found:
                return found
    return ""


PROTECTION_SCHEMES = {"edef8ba9-79d6-4ace-a3c8-27dcd51d21ed": "widevine", "9a04f079-9840-4286-ab92-e65be0885f95": "playready",
                      "94ce86fb-07ff-4f43-adb8-93d2fa968ca2": "fairplay", "urn:mpeg:dash:mp4protection:2011": "cenc",
                      "e2719d58-a985-b3c9-781a-b030af78d30e": "clearkey"}


def manifest_protection(text):
    """Co o ochraně říká manifest DASH: ``({"license": adresa} | None, popis pro diagnostiku)``.

    Adresa licence se bere jen z prvku ochrany Widevine (nebo společného), z podprvku či atributu
    pojmenovaného po licenci. Popis nese jen názvy schémat, prvků a atributů, žádné hodnoty.
    """
    import xml.etree.ElementTree as ET

    def local(name):
        return name.rsplit("}", 1)[-1]

    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError, TypeError):
        return None, "manifest nejde přečíst"
    license_url, seen = "", []
    for element in root.iter():
        if local(element.tag) != "ContentProtection":
            continue
        scheme = (element.get("schemeIdUri") or "").lower().replace("urn:uuid:", "")
        name = PROTECTION_SCHEMES.get(scheme, scheme[-12:] or "?")
        parts = sorted({local(child.tag) for child in element.iter() if child is not element}
                       | {"@" + local(key) for key in element.attrib if local(key) not in ("schemeIdUri", "value")})
        summary = "%s(%s)" % (name, ", ".join(parts)) if parts else name
        if summary not in seen:
            seen.append(summary)
        if license_url or name in ("playready", "fairplay"):
            continue
        for node in element.iter():
            candidates = [(local(key), value) for key, value in node.attrib.items()]
            candidates.append((local(node.tag), (node.text or "").strip()))
            for key, value in candidates:
                key = key.lower()
                if value.startswith("https://") and ("licen" in key or key in ("laurl", "la_url")):
                    license_url = value
                    break
            if license_url:
                break
    if not seen:
        return None, "manifest bez ochrany"
    return ({"license": license_url} if license_url else None), "manifest: " + "; ".join(seen)


def response_shape(payload, depth=0):
    """Jen názvy polí odpovědi (bez hodnot) – pro diagnostiku, když se chráněný pořad nepřehraje."""
    if isinstance(payload, dict) and depth < 7:
        # streamy první, měření a reklama vůbec: na obrazovku se vejde jen začátek
        return "{%s}" % ", ".join(
            "%s%s" % (key, response_shape(value, depth + 1) if isinstance(value, (dict, list)) else "")
            for key, value in sorted(payload.items(), key=lambda item: (item[0] != "streams", item[0]))
            if key not in ("subtitles", "chapters", "audioTracks", "analytics", "adOcean", "availableQualities"))
    if isinstance(payload, list) and payload and depth < 7:
        return "[%s]" % response_shape(payload[0], depth + 1)
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
                raise NotPlayable(reason, _playability_status(detail)) from e
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
        """Hlavní kategorie s podkategoriemi: [{"id", "title", "children": [{"id", "title"}]}]."""
        def node(entry):
            ids = entry.get("categoryId") if isinstance(entry, dict) else None
            if isinstance(ids, list) and ids and _text(entry.get("title")):
                return {"id": str(ids[-1]), "title": _text(entry["title"])}
            return None

        items = []
        for entry in self.graphql("CategoryMenu", Q_MENU).get("menu") or []:
            item = node(entry)
            if item:
                item["children"] = [child for child in map(node, entry.get("children") or []) if child]
                items.append(item)
        return items

    def category_listing(self, category_id, offset=0, limit=LISTING_PAGE):
        """Úplný výpis kategorie nebo podkategorie: {"title", "total", "shows"}."""
        category = self.graphql("CategoryProgrammes", Q_LISTING, {
            "categoryId": str(category_id), "limit": limit, "offset": offset}).get("category") or {}
        found = category.get("programmeFind") or {}
        shows = [_show_card(show) for show in found.get("items") or [] if isinstance(show, dict)]
        return {"title": _text(category.get("title")), "total": _int(found.get("totalCount")),
                "shows": [show for show in shows if show["id"] and show["title"]]}

    def live_now(self):
        """Co právě běží: [{"channel", "name", "title", "description", "starts", "ends", "image",
        "next_title", "next_starts"}] – včetně mimořádných přenosů, které web právě nabízí."""
        channels = []
        for entry in self.graphql("LiveBroadcastFind", Q_LIVE).get("liveBroadcastFind") or []:
            if not isinstance(entry, dict) or not CHANNEL_RE.match(_text(entry.get("id"))):
                continue
            current = entry.get("current") if isinstance(entry.get("current"), dict) else {}
            following = entry.get("next") if isinstance(entry.get("next"), dict) else {}
            name = _text((current.get("assignedToChannel") or {}).get("channelName")) \
                or dict(LIVE_CHANNELS).get(entry["id"], "")
            channels.append({"channel": entry["id"], "name": name, "title": _text(current.get("title")),
                             "description": _text(current.get("description")),
                             "starts": _timestamp(current.get("startsAt")), "ends": _timestamp(current.get("endsAt")),
                             "image": _text(current.get("previewImage")),
                             "next_title": _text(following.get("title")),
                             "next_starts": _timestamp(following.get("startsAt"))})
        return channels

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
    def _stream_query(drm=False):
        if drm:
            # Název parametru není zdokumentovaný a u volných pořadů se oba zápisy chovají stejně,
            # proto se posílají oba; neznámý parametr rozhraní přechází.
            return "?" + urllib.parse.urlencode([("canPlayDRM", "true"), ("canPlayDrm", "true"),
                                                 ("quality", "web"), ("streamType", "dash")])
        return "?" + urllib.parse.urlencode({"canPlayDrm": "false", "quality": "web", "streamType": "hls"})

    def manifest(self, url, limit=512 * 1024):
        """Text manifestu streamu (pro zjištění, co říká o ochraně)."""
        self.log("ivysilani: GET manifest")
        try:
            resp = self.opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                    timeout=self.timeout)
            try:
                return resp.read(limit).decode("utf-8", "replace")
            finally:
                resp.close()
        except (OSError, http.client.HTTPException) as e:
            raise IvysilaniError("manifest se nepodařilo načíst: %s" % e) from e

    def vod(self, idec, drm=False):
        """{"url", "subtitles": [adresy .vtt], "duration", "title"} nebo NotPlayable.

        ``drm=True`` žádá o stream DASH i pro chráněné pořady; výsledek pak nese navíc ``"dash": True``,
        ``"drm"`` (``{"license": adresa}`` nebo ``None``) a ``"shape"`` – názvy polí odpovědi a parametrů
        adresy streamu pro diagnostiku (bez hodnot). Adresa streamu nekončí na ``.mpd``: manifest se
        pozná až podle typu obsahu, proto se formát řídí tím, o co se žádalo.
        """
        if not str(idec).isdigit():
            raise IvysilaniError("neplatné číslo pořadu")
        data = self._request(VOD % idec + self._stream_query(drm))
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
        playability = data.get("playability") if isinstance(data.get("playability"), dict) else {}
        protection = {}
        if drm:
            query = urllib.parse.parse_qsl(urllib.parse.urlsplit(_text(stream["url"])).query)
            # z adresy jen názvy parametrů a příznak drmOnly – žádné tokeny
            names = ", ".join("%s=%s" % (key, value) if key == "drmOnly" else key for key, value in query)
            protection = {"dash": True, "drm": drm_license(stream) or drm_license(data),
                          "shape": "%s; adresa: %s" % (response_shape(data), names)}
        return dict(protection, **{"url": _text(stream["url"]), "subtitles": subtitles,
                "duration": _int(stream.get("duration") or data.get("duration")), "title": _text(data.get("title")),
                "show_title": _text(data.get("showTitle")),
                # rozhraní za název dílu přidává pořadové číslo ("… | 14")
                "episode_title": re.sub(r"\s*\|\s*\d+\s*$", "", _text(data.get("episodeTitle"))),
                "show_id": _text(str(playability.get("sidp") or "")), "image": _text(data.get("previewImageUrl"))})

    def live(self, channel, timeshift=False):
        """Živý stream kanálu; ``timeshift`` = varianta, ve které lze přetáčet zpět."""
        if not CHANNEL_RE.match(str(channel)):
            raise IvysilaniError("neznámý kanál")
        data = self._request(LIVE % channel + self._stream_query())
        urls = data.get("streamUrls") if isinstance(data.get("streamUrls"), dict) else {}
        url = _text(urls.get("timeshift")) if timeshift else ""
        url = url or _text(urls.get("main"))
        if urls.get("isBlocked") or not url:
            raise NotPlayable(_playability_message(data) or "Živé vysílání tohoto kanálu teď není dostupné.")
        return {"url": url, "subtitles": [], "duration": 0,
                "title": _text(data.get("title")) or dict(LIVE_CHANNELS).get(channel, channel)}
