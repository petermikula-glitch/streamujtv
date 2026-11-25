"""Titulky z OpenSubtitles.com (televize i Windows): hledání a stažení českých a slovenských titulků.

Rozhraní je oficiální REST API OpenSubtitles (``api.opensubtitles.com/api/v1``). Klíč k rozhraní, jméno
a heslo zadává uživatel v Nastavení (do repozitáře nepatří žádné): klíč dostane zdarma po registraci
„API consumer“ na opensubtitles.com, přihlášení zvyšuje denní počet stažení. Hledá se podle TMDB
(film, nebo díl seriálu: seriál + řada + díl), jinak podle názvu a roku.

Stažené titulky se uloží do složky aplikace (``titulky``) a zapamatují se k videu: při dalším přehrání
se přidají samy (``saved``). Odpovědi hledání se na hodinu pamatují, ať se denní limit nepromrhá.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import app_api as api

API = "https://api.opensubtitles.com/api/v1"
LANGUAGES = ("cs", "sk")
LANGUAGE_NAMES = {"cs": "Čeština", "sk": "Slovenčina"}
TIMEOUT = 15
TOKEN_TTL = 23 * 3600            # s; přihlášení platí asi den
SEARCH_TTL = 3600                # s; jak dlouho se pamatuje výsledek hledání
RESULTS = 25                     # tolik titulků nejvýš v nabídce
SAVED_PREFIX = "ossubs:"         # stažené titulky k videu (seznam cest)
SAVED_KEEP = 3                   # tolik stažených titulků k jednomu videu se pamatuje
FOLDER = "titulky"
USER_AGENT = "Promitarna v1"     # OpenSubtitles chce poctivý název aplikace a verzi

_SESSION = {"token": "", "at": 0, "base": API, "user": ""}


class SubtitlesError(api.ApiError):
    pass


def configured(settings=None):
    """Je vyplněný klíč k rozhraní (bez něj OpenSubtitles neodpoví)?"""
    settings = settings if settings is not None else api._settings()
    return bool(str(settings.get("os_key") or "").strip())


def _credentials():
    settings = api._settings()
    key = str(settings.get("os_key") or "").strip()
    if not key:
        raise SubtitlesError("Pro titulky z OpenSubtitles vyplňte v Nastavení klíč k rozhraní OpenSubtitles "
                             "(zdarma po registraci na opensubtitles.com), případně i jméno a heslo.")
    return key, str(settings.get("os_user") or "").strip(), str(settings.get("os_password") or "")


def _request(method, path, key, params=None, body=None, token="", base=None):
    """Jeden dotaz na rozhraní; vrací JSON. Parametry seřazené a malými písmeny, jak rozhraní chce
    (jinak přesměrovává)."""
    url = (base or _SESSION["base"]) + "/" + path
    if params:
        pairs = sorted((str(name).lower(), str(value).lower() if isinstance(value, str) else str(value))
                       for name, value in params.items() if value not in (None, ""))
        url += "?" + urllib.parse.urlencode(pairs)
    headers = {"Api-Key": key, "User-Agent": USER_AGENT, "Accept": "application/json",
               "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            detail = {}
        message = str(detail.get("message") or detail.get("errors") or error.reason or "")
        if error.code == 401:
            raise SubtitlesError("OpenSubtitles nepřijal jméno nebo heslo – zkontrolujte je v Nastavení.")
        if error.code == 403:
            raise SubtitlesError("OpenSubtitles odmítl klíč k rozhraní – zkontrolujte ho v Nastavení.")
        if error.code in (406, 407):
            raise SubtitlesError("Dnešní limit stažení titulků z OpenSubtitles je vyčerpaný%s." % (
                " (obnoví se %s)" % detail["reset_time"] if detail.get("reset_time") else ""))
        if error.code == 429:
            raise SubtitlesError("OpenSubtitles teď odmítá příliš mnoho dotazů, zkuste to za chvíli.")
        raise SubtitlesError("OpenSubtitles odpověděl chybou %d%s." % (error.code, ": " + message if message else ""))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SubtitlesError("OpenSubtitles se nepodařilo zastihnout (%s)." % getattr(error, "reason", error))


def _token(key, user, password):
    """Přihlášení (jen když je vyplněné jméno a heslo); token se den pamatuje."""
    if not (user and password):
        return ""
    if _SESSION["token"] and _SESSION["user"] == user and time.time() - _SESSION["at"] < TOKEN_TTL:
        return _SESSION["token"]
    answer = _request("POST", "login", key, body={"username": user, "password": password}, base=API)
    token = str(answer.get("token") or "")
    if not token:
        raise SubtitlesError("Přihlášení k OpenSubtitles se nepovedlo.")
    host = str(answer.get("base_url") or "").strip().strip("/")
    # další dotazy patří na server, který přihlášení vrátilo (např. pro účty VIP)
    base = "https://%s/api/v1" % host if re.match(r"^[a-z0-9.-]+\.opensubtitles\.(com|org)$", host) else API
    _SESSION.update(token=token, at=time.time(), base=base, user=user)
    return token


def _video_info(lib, vid):
    """Co se o videu ví pro hledání: ``{"kind", "tmdb", "season", "episode", "title", "year", "file"}``."""
    video = lib.find_video(vid)
    if not video:
        raise SubtitlesError("Video v katalogu není.")
    meta = lib.get_meta(video["meta_key"]) or {}
    info = {"kind": "episode" if video["kind"] == "episode" else "movie", "tmdb": 0,
            "season": int(video.get("season") or 0), "episode": int(video.get("episode") or 0),
            "title": meta.get("orig_title") or meta.get("title") or video.get("title") or "",
            "year": int(meta.get("year") or 0), "file": video.get("title") or ""}
    choice = lib.get_kv(api.ART_PREFIX + vid) or ""        # titul z Objevuj: movie:<id> nebo tv:<id>:<řada>:<díl>
    parts = choice.split(":")
    if parts[0] == "movie" and len(parts) > 1 and parts[1].isdigit():
        info.update(kind="movie", tmdb=int(parts[1]))
    elif parts[0] == "tv" and len(parts) > 1 and parts[1].isdigit():
        info.update(kind="episode", tmdb=int(parts[1]))
        if len(parts) > 3 and parts[2].isdigit() and parts[3].isdigit():
            info.update(season=int(parts[2]), episode=int(parts[3]))
    elif meta.get("tmdb_id"):
        info["tmdb"] = int(meta["tmdb_id"])
    return info


def _criteria(info):
    languages = ",".join(LANGUAGES)
    if info["kind"] == "episode" and info["season"] and info["episode"]:
        if info["tmdb"]:
            return {"languages": languages, "parent_tmdb_id": info["tmdb"], "season_number": info["season"],
                    "episode_number": info["episode"]}
        return {"languages": languages, "query": info["title"], "season_number": info["season"],
                "episode_number": info["episode"], "type": "episode"}
    if info["tmdb"]:
        return {"languages": languages, "tmdb_id": info["tmdb"], "type": "movie"}
    criteria = {"languages": languages, "query": info["title"] or info["file"], "type": "movie"}
    if info["year"]:
        criteria["year"] = info["year"]
    return criteria


_WORDS = re.compile(r"[a-z0-9]+")


def _similarity(release, file_name):
    """Kolik slov má název titulků společných s názvem souboru (verze 1080p, WEB-DL, skupina…)."""
    words = set(_WORDS.findall(str(release or "").lower()))
    return len(words & set(_WORDS.findall(str(file_name or "").lower())))


def subtitles_search(params):
    """České a slovenské titulky k videu: ``{"title", "items": [{"title", "subtitle", "file_id", "lang"}]}``."""
    vid = str(params.get("track") or params.get("vid") or "")
    if not vid or vid.startswith(api.IVYS):
        raise SubtitlesError("Titulky z OpenSubtitles jdou hledat jen k videím z Přehraj.to.")
    key, user, password = _credentials()
    with api._library() as lib:
        info = _video_info(lib, vid)
        criteria = _criteria(info)
        cache = "ossearch:" + json.dumps(criteria, sort_keys=True)
        found = lib.cache_get(cache)
    if found is None:
        token = _token(key, user, password)
        answer = _request("GET", "subtitles", key, params=criteria, token=token)
        found = []
        for item in answer.get("data") or []:
            attributes = item.get("attributes") or {}
            files = attributes.get("files") or []
            if not files or not files[0].get("file_id"):
                continue
            found.append({"file_id": int(files[0]["file_id"]), "lang": str(attributes.get("language") or ""),
                          "release": str(attributes.get("release") or files[0].get("file_name") or ""),
                          "downloads": int(attributes.get("download_count") or 0),
                          "machine": bool(attributes.get("machine_translated") or attributes.get("ai_translated")),
                          "hearing": bool(attributes.get("hearing_impaired")),
                          "uploader": str((attributes.get("uploader") or {}).get("name") or "")})
        with api._library() as lib:
            lib.cache_put(cache, found, SEARCH_TTL)
    # nejdřív titulky k té samé verzi souboru, pak lidmi přeložené a nejstahovanější
    found = sorted(found, key=lambda item: (item["machine"], -_similarity(item["release"], info["file"]),
                                            LANGUAGES.index(item["lang"]) if item["lang"] in LANGUAGES else 9,
                                            -item["downloads"]))
    items = []
    for item in found[:RESULTS]:
        notes = [LANGUAGE_NAMES.get(item["lang"], item["lang"].upper()), "%d× staženo" % item["downloads"]]
        if item["machine"]:
            notes.append("strojový překlad")
        if item["hearing"]:
            notes.append("pro neslyšící")
        if item["uploader"]:
            notes.append(item["uploader"])
        items.append({"title": item["release"] or "Titulky", "subtitle": " · ".join(notes),
                      "file_id": item["file_id"], "lang": item["lang"]})
    empty = "České ani slovenské titulky k tomuto %s na OpenSubtitles nejsou." % (
        "dílu" if info["kind"] == "episode" else "filmu")
    return {"title": "Titulky z OpenSubtitles", "items": items, "empty": empty if not items else ""}


def _text(raw):
    """Titulky jako UTF-8 (české soubory bývají i ve windows-1250)."""
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def subtitles_download(params):
    """Stáhne zvolené titulky k videu: ``{"path", "lang", "remaining", "toast"}``."""
    vid = str(params.get("track") or params.get("vid") or "")
    try:
        file_id = int(params.get("file_id"))
    except (TypeError, ValueError):
        raise SubtitlesError("Chybí titulky.")
    if not vid:
        raise SubtitlesError("Chybí video.")
    lang = str(params.get("lang") or "cs")[:3]
    key, user, password = _credentials()
    token = _token(key, user, password)
    answer = _request("POST", "download", key, body={"file_id": file_id, "sub_format": "srt"}, token=token)
    link = str(answer.get("link") or "")
    if not link.startswith(("https://", "http://")):
        raise SubtitlesError(str(answer.get("message") or "OpenSubtitles nevrátil odkaz na titulky."))
    try:
        request = urllib.request.Request(link, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(5 * 1024 * 1024)
    except (urllib.error.URLError, OSError) as error:
        raise SubtitlesError("Titulky se nepodařilo stáhnout (%s)." % getattr(error, "reason", error))
    if not raw.strip():
        raise SubtitlesError("OpenSubtitles poslal prázdné titulky.")
    folder = api._path(FOLDER)
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", vid)[-60:]
    path = os.path.join(folder, "%s_%d.%s.srt" % (safe, file_id, lang))
    with open(path, "w", encoding="utf-8") as output:
        output.write(_text(raw))
    with api._library() as lib:
        saved = [item for item in _saved_list(lib, vid) if item != path]
        lib.set_kv(SAVED_PREFIX + vid, json.dumps(([path] + saved)[:SAVED_KEEP]))
    remaining = answer.get("remaining")
    toast = "Titulky jsou stažené."
    if isinstance(remaining, int):
        toast += " Dnes zbývá stažení: %d." % max(0, remaining)
    return {"path": path, "lang": lang, "remaining": remaining, "toast": toast}


def _saved_list(lib, vid):
    try:
        saved = json.loads(lib.get_kv(SAVED_PREFIX + vid) or "[]")
    except ValueError:
        saved = []
    return [item for item in saved if isinstance(item, str)] if isinstance(saved, list) else []


def saved(lib, vid):
    """Dřív stažené titulky k videu, které ještě existují: ``[(cesta, jazyk)]`` (nejnovější první)."""
    result = []
    for path in _saved_list(lib, vid):
        if os.path.isfile(path):
            match = re.search(r"\.([a-z]{2,3})\.srt$", path)
            result.append((path, match.group(1) if match else "cs"))
    return result


ACTIONS = {"subtitles_search": subtitles_search, "subtitles_download": subtitles_download}
