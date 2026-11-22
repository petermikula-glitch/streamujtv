# -*- coding: utf-8 -*-
"""Rozhraní mezi Android aplikací a logikou doplňku (katalog Přehraj.to, iVysílání).

Aplikace volá jedinou funkci ``call(akce, parametry_json)`` a dostane JSON:
``{"ok": true, "data": …}`` nebo ``{"ok": false, "error": "text pro uživatele"}``.
O tom, kam která položka vede, rozhoduje tento modul – každá karta nese ``target``:

* ``{"type": "list", "call": akce, "params": {…}, "title": …}`` – otevřít výpis,
* ``{"type": "play", "call": akce, "params": {…}}`` – přehrát,
* ``{"type": "command", "call": akce, "params": {…}}`` – provést a zobrazit zprávu,
* ``{"type": "settings"}`` / ``{"type": "search"}`` – obrazovky aplikace.

Modul nesmí importovat nic z Androidu, aby šel testovat mimo něj.
"""
import json
import os
import re
import socket
import threading
import time

import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import catalog
import db
import ivysilani
import nameparser
import provider
from tmdb import Tmdb, TmdbError

PAGE = 60               # položek katalogu na stránku výpisu
HOME_ROW = 20           # položek v řadě na hlavní obrazovce
LINK_PREFIX = "link:"   # zapamatované adresy souborů (jako v doplňku)
IVYS = "ivys:"          # označení pořadu z iVysílání při ukládání pozice
TTL = {"menu": 24 * 3600, "category": 3600, "show": 3600, "episodes": 900, "search": 600, "live": 60}

DOWNLOADS = "downloads.json"   # evidence souborů stažených na disk (jen v aplikaci)
VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".webm", ".ts", ".mov", ".m4v", ".mpg", ".mpeg", ".wmv")
UPDATE_URL = "https://api.github.com/repos/petermikula-glitch/streamujtv/releases?per_page=20"
UPDATE_TAG = "app-v"           # vydání aplikace: značka app-v<verze>, příloha .apk
UPDATE_EVERY = 6 * 3600        # automatická kontrola nové verze nejvýš jednou za tuto dobu

_STATE = {"dir": "", "progress": "", "busy": False}
_DOWNLOADS_LOCK = threading.RLock()
_LOCK = threading.Lock()   # dlouhé operace (synchronizace, metadata) běží nejvýš jedna


class ApiError(Exception):
    """Chyba s textem určeným uživateli."""


# ---------------------------------------------------------------------------
# Prostředí a nastavení
# ---------------------------------------------------------------------------

def configure(directory, proxy="", no_proxy=""):
    """Složka aplikace pro katalog, nastavení a přihlášení. Volá se jednou při startu.

    ``proxy`` je systémová proxy zařízení (``http://host:port``), pokud je nastavená:
    urllib ji na Androidu sám nezjistí. ``no_proxy`` jsou adresy, které jdou mimo ni (oddělené čárkou).
    """
    os.makedirs(directory, exist_ok=True)
    _STATE["dir"] = directory
    if proxy:
        os.environ["https_proxy"] = os.environ["http_proxy"] = str(proxy)
        if no_proxy:
            os.environ["no_proxy"] = str(no_proxy)
    return "ok"


def _path(name):
    if not _STATE["dir"]:
        raise ApiError("Aplikace není připravena (chybí složka pro data).")
    return os.path.join(_STATE["dir"], name)


def _settings():
    try:
        with open(_path("settings.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(values):
    temporary = _path("settings.json.tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)
    os.replace(temporary, _path("settings.json"))


def _library():
    return db.Library(_path("catalog.db"), video_key=provider.video_key, prefer_ref=provider.prefer_ref)


def _client(allow_anonymous=False):
    settings = _settings()
    if not settings.get("username") or not settings.get("password"):
        if allow_anonymous:
            return provider.make_client("", "")
        raise ApiError("Nejdřív v Nastavení vyplňte e-mail a heslo k Přehraj.to.")
    return provider.make_client(settings["username"], settings["password"], cookie_file=_path("cookies.lwp"))


class _TmdbCache(object):
    """Odpovědi TMDB v mezipaměti katalogu: seznamy v Objevuj se při návratu nenačítají znovu."""

    @staticmethod
    def get(key):
        with _library() as lib:
            return lib.cache_get(key)

    @staticmethod
    def put(key, data, ttl):
        with _library() as lib:
            lib.cache_put(key, data, ttl)
            lib.cache_trim("tmdb:", 300)


def _tmdb():
    return Tmdb(_settings().get("tmdb_key", ""), cache=_TmdbCache())


def _ivys(timeout=None):
    return ivysilani.Client(timeout=timeout or ivysilani.TIMEOUT)


def _cached(key, ttl, load):
    key = "app:" + key
    with _library() as lib:
        value = lib.cache_get(key)
    if value is None:
        value = load()
        with _library() as lib:
            lib.cache_put(key, value, ttl)
            lib.cache_trim("app:", 200)
    return value


# ---------------------------------------------------------------------------
# Karty
# ---------------------------------------------------------------------------

def _target(kind, call=None, params=None, title=None):
    target = {"type": kind}
    if call:
        target["call"] = call
        target["params"] = params or {}
    if title:
        target["title"] = title
    return target


def _card(title, target, subtitle="", image="", progress=0, wide=False, watched=False, icon="", menu=None,
          badges=None):
    """Karta; ``icon`` je název ikony dlaždice pro karty bez obrázku (složky, nástroje).

    ``menu`` je nabídka po podržení tlačítka OK: seznam ``{"title", "target"}``.
    """
    card = {"title": title or "", "subtitle": subtitle or "", "image": image or "", "progress": int(progress or 0),
            "wide": bool(wide), "watched": bool(watched), "icon": icon or "", "target": target}
    if menu:
        card["menu"] = menu
    if badges:
        card["badges"] = [badge for badge in badges if badge]  # krátké štítky přes obrázek (jazyk, kvalita)
    return card


# Ikony hlavních kategorií iVysílání (podle názvu v nabídce ČT); neznámá kategorie dostane obecnou.
IVYS_ICONS = {
    "Seriály": "series", "Filmy": "movies", "Dokumenty": "docs", "Zpravodajství": "news", "Sport": "sport",
    "Pro děti": "kids", "Zábava": "fun", "Historie": "past", "Kultura": "culture", "Rady a recepty": "advice",
    "Příroda": "nature", "Společnost": "society", "Spiritualita": "spirit", "Archiv": "archive",
}


def _display_title(meta):
    return meta.get("title") or meta.get("search_title") or ""


def _year(meta):
    return meta.get("year") or meta.get("search_year") or 0


def _percent(point):
    return int(100 * point[0] / point[1]) if point and point[1] else 0


def _languages(values):
    return ", ".join(part for part in (values or "").split(",") if part)


LANGUAGE_CHOICES = (("dub", "Český dabing"), ("subs", "Originál s titulky"), ("any", "Je mi to jedno"))
QUALITY_CHOICES = (("best", "Nejvyšší dostupná"), ("1080", "1080p"), ("720", "720p"),
                   ("small", "Úsporná (nejmenší soubor)"))
# kopie natočená v kině: obraz i zvuk jsou výrazně horší, nabízí se až jako poslední
CAM_RE = re.compile(r"(?<![a-z0-9])(?:cam|camrip|hdcam|ts|hdts|telesync|tc|telecine|kinorip)(?![a-z0-9])", re.I)
BAD_VIDEOS = "bad_videos"       # verze, které se nepodařilo přehrát
CHOICE_PREFIX = "choice:"       # verze, kterou si uživatel u titulu sám vybral


def _prefs(settings=None):
    """Předvolby přehrávání z Přehraj.to: jazyk a kvalita."""
    settings = _settings() if settings is None else settings
    language, quality = settings.get("prefer_language"), settings.get("prefer_quality")
    return {"language": language if language in dict(LANGUAGE_CHOICES) else "dub",
            "quality": quality if quality in dict(QUALITY_CHOICES) else "best"}


def _size_bytes(text):
    match = re.match(r"\s*([\d.,]+)\s*([kmgt])?b?", str(text or "").lower())
    if not match:
        return 0
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return 0
    return int(value * 1024 ** " kmgt".index(match.group(2) or " "))


def _language_rank(lang, prefer):
    parts = {part.strip() for part in (lang or "").split(",") if part.strip()}
    dubbed = bool(parts & {"CZ", "CZ dabing", "SK", "SK dabing"})
    subtitled = bool(parts & {"CZ titulky", "SK titulky"})
    if prefer == "subs":   # původní zvuk s titulky, pak cokoli s titulky, dabing až nakonec
        return 0 if subtitled and not dubbed else 1 if subtitled else 2 if not dubbed else 3
    if prefer == "dub":
        return 0 if dubbed else 1 if subtitled else 2
    return 0


def _quality_rank(quality, size, prefer):
    order = provider.QUALITY_ORDER
    token = provider.quality_token(quality)
    index = order.index(token) if token in order else len(order)
    if prefer == "small":
        return _size_bytes(size) or (1 << 60)
    if prefer in ("1080", "720"):
        wanted = order.index("FHD" if prefer == "1080" else "HD")
        if index == len(order) or token == "ORIG":
            return 50                       # neznámé rozlišení až za známými
        # přesná shoda, pak nejbližší nižší, vyšší až potom
        return index - wanted if index >= wanted else 10 + (wanted - index)
    return index


def _version_rank(video, prefs=None, bad=()):
    """Pořadí verzí filmu nebo dílu podle předvoleb; kino kopie a nefunkční verze až na konec."""
    prefs = prefs or _prefs()
    name = video.get("raw_title") or ""
    return (bool(CAM_RE.search(name)), video.get("id") in bad, _language_rank(video.get("lang"), prefs["language"]),
            _quality_rank(video.get("quality"), video.get("size"), prefs["quality"]), name)


def _pick_stream(streams, prefs):
    """Varianta souboru k přehrání (originál, 2160p, 1080p…) podle předvolené kvality."""
    order = provider.QUALITY_ORDER
    def rank(stream):
        token = stream.get("quality")
        index = order.index(token) if token in order else len(order)
        if prefs["quality"] == "best":
            return index
        if prefs["quality"] == "small":
            return -index if token != "ORIG" and index < len(order) else 1
        wanted = order.index("FHD" if prefs["quality"] == "1080" else "HD")
        if token == "ORIG" or index == len(order):
            return 50
        return index - wanted if index >= wanted else 10 + (wanted - index)
    return min(streams, key=rank)


def _bad_videos(lib):
    try:
        return set(json.loads(lib.get_kv(BAD_VIDEOS) or "[]"))
    except (TypeError, ValueError):
        return set()


def _movie_card(meta, state, versions=None):
    year = _year(meta)
    rating = "%d %%" % meta["rating"] if meta.get("rating") else ""
    subtitle = " · ".join(part for part in (str(year) if year else "", rating, _languages(meta.get("langs"))) if part)
    videos = (versions or {}).get(meta["key"]) or []
    vid = min(videos, key=lambda video: _version_rank(video, state.get("prefs"), state.get("bad", ())))["id"] \
        if videos else meta.get("first_video")
    ids = [video["id"] for video in videos] or [vid]
    point = next((state["resume"][i] for i in ids if i in state["resume"]), None)
    saved = next((i for i in ids if _saved(state, i)), None)
    if saved:  # stažená verze má přednost: hraje i bez internetu
        vid = saved
    return _card(_display_title(meta), _target("play", "play", {"vid": vid}), _mark(state, vid, subtitle),
                 meta.get("poster") or meta.get("thumb"), _percent(point), wide=not meta.get("poster"),
                 watched=any(i in state["watched"] for i in ids),
                 menu=_video_menu(state, {"vid": vid}, point) + _favorite_menu(state, meta), icon="movies")


def _favorite_menu(state, meta):
    """Položka nabídky pro oblíbené (film nebo seriál z katalogu)."""
    if "favorites" not in state or not meta.get("key"):
        return []
    return [app_discover.favorite_item(state["favorites"], app_discover.meta_favorite(meta))]


def _show_card(meta, state=None):
    count = meta.get("video_count") or 0
    rating = "%d %%" % meta["rating"] if meta.get("rating") else ""
    subtitle = " · ".join(part for part in (rating, "%d dílů" % count if count else "") if part)
    title = _display_title(meta)
    target = _target("list", "show", {"key": meta["key"]}, title)
    menu = [{"title": "Otevřít", "target": target}] + _favorite_menu(state, meta) if state else None
    return _card(title, target, subtitle, meta.get("poster") or meta.get("thumb"), wide=not meta.get("poster"),
                 icon="series", menu=menu)


def _video_card(video, meta, state, prefix="", web_result=None):
    meta = meta or {}
    title = _display_title(meta) or video["title"]
    if video["kind"] == "episode":
        label = "%s %dx%02d" % (title, video["season"], video["episode"]) if video["episode"] else video["raw_title"]
    else:
        label = title
    details = " · ".join(part for part in (video.get("lang"), video.get("quality"), video.get("size")) if part)
    vid = video["id"]
    params = {"vid": vid}
    if web_result:
        params["web_result"] = web_result
        label = web_result["title"]
    point = state["resume"].get(vid)
    return _card(prefix + label, _target("play", "play", params), _mark(state, vid, details),
                 meta.get("poster") or video.get("thumb"), _percent(point),
                 wide=not meta.get("poster"), watched=vid in state["watched"], menu=_video_menu(state, params, point),
                 icon="series" if video["kind"] == "episode" else "movies")


def _state(lib):
    return {"resume": lib.resume_points(), "watched": lib.watched(), "downloads": _downloads(),
            "favorites": app_discover.load_favorites(), "prefs": _prefs(), "bad": _bad_videos(lib)}


def _saved(state, vid):
    """Je video stažené a soubor je právě dostupný (disk je připojený)?"""
    entry = state.get("downloads", {}).get(vid)
    return bool(entry and entry.get("state") == "done" and os.path.exists(entry.get("path") or ""))


def _mark(state, vid, subtitle):
    return " · ".join(part for part in ("Staženo" if _saved(state, vid) else "", subtitle) if part)


def _video_menu(state, params, point=None):
    """Nabídka po podržení OK na videu z Přehraj.to: přehrání a stahování na disk."""
    vid = params["vid"]
    items = [{"title": "Přehrát", "target": _target("play", "play", params)}]
    if point and point[0] > 0:
        items.append({"title": "Přehrát od začátku", "target": _target("play", "play", dict(params, restart=True))})
    entry = state.get("downloads", {}).get(vid)
    remove = _target("command", "download_remove", {"vid": vid})
    if not entry:
        items.append({"title": "Stáhnout na disk", "target": _target("download", "download_add", params)})
    elif entry.get("state") == "done":
        items.append({"title": "Smazat stažený soubor", "target": remove})
    elif entry.get("state") == "failed":
        items.append({"title": "Zkusit stáhnout znovu", "target": _target("download", "download_add", params)})
        items.append({"title": "Odebrat ze stahování", "target": remove})
    else:
        items.append({"title": "Zrušit stahování", "target": remove})
    return items


# ---------------------------------------------------------------------------
# Hlavní obrazovka
# ---------------------------------------------------------------------------

def _in_progress(lib, state, limit=HOME_ROW):
    """Rozkoukaná videa z Přehraj.to."""
    videos = lib.history_videos(in_progress=True, limit=limit, include_search=True)
    metas = lib.get_metas(video["meta_key"] for video in videos)
    return [_video_card(video, metas.get(video["meta_key"]), state) for video in videos]


def _ivys_in_progress(lib, limit=HOME_ROW):
    """Rozkoukané pořady z iVysílání."""
    cards = []
    for row in lib.ivys_progress(limit):
        point = (row["position"], row["total"])
        label = "%s · %s" % (row["show_title"], row["title"]) if row["show_title"] and row["title"] else \
            row["title"] or row["show_title"]
        cards.append(_card(label, _target("play", "ivys_play", {"idec": row["idec"]}), "iVysílání", row["image"],
                           _percent(point), wide=True))
    return cards


def home(params):
    """Hlavní obrazovka: sekce postranní nabídky, každá se svými řadami karet.

    ``{"sections": [{"id", "title", "rows": [{"title", "items"}]}], "pending"}``. Sekce Přehraj.to
    a iVysílání obsahují jen to, co se týká dané služby; Nástroje jsou společné.
    """
    settings = _settings()
    with _library() as lib:
        state = _state(lib)
        prehrajto = _prehrajto_rows(lib, state, settings)
        television = _ivysilani_rows(lib)
    last = settings.get("last_sync")
    tools = [{"title": "Nástroje", "items": [
        _card("Hledat", _target("search"), "Moje videa, iVysílání a Přehraj.to", icon="search"),
        _card("Stahování", _target("list", "downloads", {}, "Stahování"), _downloads_summary(state["downloads"]),
              icon="download"),
        _card("Synchronizovat s účtem", _target("command", "sync", {}),
              "Naposledy: %s" % time.strftime("%d.%m. %H:%M", time.localtime(last)) if last else "Zatím neproběhla",
              icon="sync"),
        _card("Doplnit hodnocení a plakáty", _target("command", "enrich", {}), "ČSFD, záložně TMDB", icon="enrich"),
        _card("Nastavení a přihlášení", _target("settings"), settings.get("username") or "Nepřihlášeno",
              icon="settings"),
    ]}]
    # "pending": ve frontě něco čeká – aplikace po startu znovu spustí službu stahování
    return {"sections": [{"id": "prehrajto", "title": "Přehraj.to", "icon": "section_prehrajto", "rows": prehrajto},
                         {"id": "ivysilani", "title": "iVysílání", "icon": "section_ivysilani", "rows": television},
                         {"id": "tools", "title": "Nástroje", "icon": "section_tools", "rows": tools}],
            "pending": any(entry.get("state") in ("queued", "downloading")
                           for entry in state["downloads"].values())}


def _prehrajto_rows(lib, state, settings):
    rows = []
    total = lib.count_videos(visible_only=True)
    if not total and not settings.get("username"):
        rows.append({"title": "Začínáme", "items": [
            _card("Přihlásit se k Přehraj.to", _target("settings"), "E-mail a heslo, pak Synchronizovat",
                  icon="login")]})
    progress = _in_progress(lib, state)
    if progress:
        rows.append({"title": "Rozkoukané", "items": progress})
    following = app_discover.next_up_cards(lib, state)
    if following:
        rows.append({"title": "Další díly", "items": following})
    favorites = app_discover.favorite_cards(lib, state, HOME_ROW)
    if len(state["favorites"]) > HOME_ROW:
        favorites.append(_card("Všechny oblíbené", _target("list", "favorites", {}, "Oblíbené"),
                               "%d položek" % len(state["favorites"]), icon="favorites"))
    if not favorites:
        favorites = [_card("Oblíbené", _target("list", "favorites", {}, "Oblíbené"),
                           "Podržte OK na filmu a zvolte Přidat do oblíbených", icon="favorites")]
    rows.append({"title": "Oblíbené", "items": favorites})
    rows.append({"title": "Objevuj filmy", "items": app_discover.home_tiles("movie")})
    rows.append({"title": "Objevuj seriály", "items": app_discover.home_tiles("tv")})
    saved = _download_cards(lib, state, HOME_ROW)
    if saved:
        rows.append({"title": "Stažené na disku", "items": saved})
    movies = lib.movies(order="added", limit=HOME_ROW)
    if movies:
        versions = lib.videos_grouped([m["key"] for m in movies if (m.get("video_count") or 1) > 1])
        cards = [_movie_card(meta, state, versions) for meta in movies]
        cards.append(_card("Všechny filmy", _target("list", "movies", {"order": "title"}, "Filmy"),
                           "%d filmů" % lib.count_movies(), icon="movies"))
        rows.append({"title": "Moje videa – nově přidané filmy", "items": cards})
    shows = lib.shows(order="added", limit=HOME_ROW)
    if shows:
        cards = [_show_card(meta, state) for meta in shows]
        cards.append(_card("Všechny seriály", _target("list", "shows", {"order": "title"}, "Seriály"),
                           icon="series"))
        rows.append({"title": "Moje videa – seriály", "items": cards})
    library_cards = []
    if total:
        library_cards = [
            _card("Filmy podle názvu", _target("list", "movies", {"order": "title"}, "Filmy"), icon="movies"),
            _card("Filmy podle hodnocení", _target("list", "movies", {"order": "rating"}, "Nejlépe hodnocené filmy"),
                  icon="rating"),
            _card("Filmy podle roku", _target("list", "movies", {"order": "year"}, "Filmy podle roku"),
                  icon="year"),
            _card("Seriály podle názvu", _target("list", "shows", {"order": "title"}, "Seriály"), icon="series"),
        ]
    if total or lib.history_videos(limit=1, include_search=True):
        library_cards.append(
            _card("Naposledy přehrané", _target("list", "played", {}, "Naposledy přehrané"), icon="history"))
        rows.append({"title": "Moje videa", "items": library_cards})
    return rows


def _ivysilani_rows(lib):
    rows = [{"title": "Živé vysílání", "items": ivys_live({"quick": True})["items"]}]
    progress = _ivys_in_progress(lib)
    if progress:
        rows.append({"title": "Rozkoukané", "items": progress})
    shows = [_card(item["title"], _target("list", "ivys_show", {"id": item["show_id"]}, item["title"]), "",
                   item["image"] or item["poster"], wide=True, icon="favorites")
             for item in lib.ivys_favorites()[:HOME_ROW]]
    if shows:
        shows.append(_card("Moje pořady", _target("list", "ivys_favorites", {}, "Moje pořady"), "S počty nových dílů",
                           icon="favorites"))
        rows.append({"title": "Moje pořady", "items": shows})
    categories = []
    try:
        # krátký limit: hlavní obrazovka nesmí bez připojení čekat desítky sekund
        for category in _cached("ivys:menu", TTL["menu"], lambda: _ivys(timeout=4).menu()):
            categories.append(_card(category["title"], _target(
                "list", "ivys_category", {"cid": category["id"]}, category["title"]), "iVysílání",
                icon=IVYS_ICONS.get(category["title"], "folder")))
    except ivysilani.IvysilaniError:
        pass  # bez připojení zůstane aspoň živé vysílání
    if categories:
        rows.append({"title": "Kategorie", "items": categories})
    return rows


# ---------------------------------------------------------------------------
# Katalog Přehraj.to
# ---------------------------------------------------------------------------

def _offset(params):
    try:
        return max(0, int(params.get("offset") or 0))
    except (TypeError, ValueError):
        return 0


def _next(call, params, offset, shown, total):
    if shown and offset + shown < total:
        return {"call": call, "params": dict(params, offset=offset + shown)}
    return None


def movies(params):
    order = params.get("order") if params.get("order") in ("title", "added", "rating", "year") else "title"
    offset = _offset(params)
    with _library() as lib:
        state = _state(lib)
        total = lib.count_movies()
        items = lib.movies(order=order, limit=PAGE, offset=offset)
        versions = lib.videos_grouped([m["key"] for m in items if (m.get("video_count") or 1) > 1])
        cards = [_movie_card(meta, state, versions) for meta in items]
    return {"items": cards, "next": _next("movies", {"order": order}, offset, len(cards), total)}


def shows(params):
    order = params.get("order") if params.get("order") in ("title", "added", "rating", "year") else "title"
    offset = _offset(params)
    with _library() as lib:
        items = lib.shows(order=order)
        state = _state(lib)
    page = items[offset:offset + PAGE]
    return {"items": [_show_card(meta, state) for meta in page],
            "next": _next("shows", {"order": order}, offset, len(page), len(items))}


def show(params):
    """Díly seriálu seřazené po řadách; každá verze dílu jen jednou (nejlepší)."""
    key = params.get("key", "")
    with _library() as lib:
        meta = lib.get_meta(key)
        if not meta:
            raise ApiError("Seriál v katalogu není.")
        state = _state(lib)
        best = {}
        for video in lib.videos_for_meta(key):
            position = (video["season"], video["episode"]) if video["episode"] else (video["season"], video["id"])
            if position not in best or _version_rank(video, state["prefs"], state["bad"]) < \
                    _version_rank(best[position], state["prefs"], state["bad"]):
                best[position] = video
    title = _display_title(meta)
    cards = []
    for position in sorted(best, key=lambda item: (item[0], str(item[1]).zfill(12))):
        video = best[position]
        label = "%d×%02d" % (video["season"], video["episode"]) if video["episode"] else video["raw_title"]
        details = " · ".join(part for part in (video.get("lang"), video.get("quality")) if part)
        point = state["resume"].get(video["id"])
        cards.append(_card(label, _target("play", "play", {"vid": video["id"]}), _mark(state, video["id"], details),
                           video.get("thumb") or meta.get("poster"), _percent(point),
                           wide=True, watched=video["id"] in state["watched"],
                           menu=_video_menu(state, {"vid": video["id"]}, point), icon="series"))
    return {"title": title, "items": cards, "next": None}


def played(params):
    with _library() as lib:
        state = _state(lib)
        videos = lib.history_videos(limit=PAGE, include_search=True)
        metas = lib.get_metas(video["meta_key"] for video in videos)
        cards = [_video_card(video, metas.get(video["meta_key"]), state) for video in videos]
    return {"items": cards, "next": None}


def search(params):
    """Hledání v katalogu, iVysílání a online v katalogu Přehraj.to."""
    query = (params.get("q") or "").strip()
    rows = []
    if not query:
        return {"rows": rows}
    with _library() as lib:
        state = _state(lib)
        found_movies, found_shows = lib.search(query)
        versions = lib.videos_grouped([m["key"] for m in found_movies[:PAGE] if (m.get("video_count") or 1) > 1])
        if found_movies:
            rows.append({"title": "Filmy v katalogu", "items": [
                _movie_card(meta, state, versions) for meta in found_movies[:PAGE]]})
        if found_shows:
            rows.append({"title": "Seriály v katalogu", "items": [_show_card(meta, state) for meta in found_shows[:PAGE]]})
    response = {"rows": rows}
    _remember_search(query)
    rows.extend(app_discover.search_rows(query, state))
    with ThreadPoolExecutor(max_workers=1) as pool:
        web = pool.submit(prehrajto_search, {"q": query})
        rows.extend(_ivys_search_rows(query))
        try:
            results = web.result()
        except ApiError as error:
            response["notice"] = str(error)
        else:
            cards = results["items"]
            if results["next"]:
                cards.append(_card("Další výsledky na Přehraj.to",
                                   _target("list", "prehrajto_search", results["next"]["params"],
                                           "Videa na Přehraj.to"), icon="search"))
            if cards:
                rows.append({"title": "Videa na Přehraj.to", "items": cards})
    return response


def _remember_search(query):
    settings = _settings()
    history = [item for item in settings.get("search_history") or [] if isinstance(item, str) and item != query]
    settings["search_history"] = ([query] + history)[:12]
    _save_settings(settings)


def search_history(params):
    """Naposledy hledané výrazy jako dlaždice (pro prázdnou obrazovku hledání)."""
    history = [item for item in _settings().get("search_history") or [] if isinstance(item, str)]
    cards = [_card(item, _target("search", "search", {"q": item}), icon="history") for item in history]
    return {"rows": [{"title": "Naposledy hledané", "items": cards}] if cards else []}


def _ivys_search_rows(query):
    rows = []
    try:
        client = _ivys()
        programmes = client.search_shows(query)[1]
        episodes = client.search_episodes(query, 0, 20)[1]
        with _library() as lib:
            marks = _ivys_state(lib)
        if programmes:
            rows.append({"title": "Pořady v iVysílání", "items": [_ivys_show_card(item) for item in programmes]})
        if episodes:
            rows.append({"title": "Díly v iVysílání", "items": [_ivys_episode_card(item, marks) for item in episodes]})
    except ivysilani.IvysilaniError:
        pass  # katalog se prohledá i bez připojení k iVysílání
    return rows


def prehrajto_search(params):
    query = (params.get("q") or "").strip()
    page = max(1, _offset({"offset": params.get("page")}))
    client = _client(allow_anonymous=True)
    client.request_timeout = 5
    client.request_retries = 0
    try:
        results, cursor = provider.search_catalog(client, query, page, params.get("cursor", ""))
    except provider.LoginError:
        raise ApiError("Hledání na Přehraj.to se nezdařilo. Zkontrolujte přihlášení v Nastavení.")
    except provider.ProviderError:
        raise ApiError("Hledání na Přehraj.to se nezdařilo. Zkuste to znovu.")
    cards = []
    with _library() as lib:
        state = _state(lib)
        for result in results:
            video = lib.find_video(result["id"])
            if video:
                cards.append(_video_card(video, lib.get_meta(video["meta_key"]), state, web_result=result))
            else:
                title = result["title"]
                video = dict(nameparser.parse(title), id=result["id"], raw_title=title,
                             thumb=result.get("thumb", ""), size=result.get("size", ""))
                video["quality"] = result.get("quality") or video["quality"]
                cards.append(_video_card(video, None, state, web_result=result))
    following = {"call": "prehrajto_search", "params": {"q": query, "page": page + 1, "cursor": cursor}} if cursor else None
    return {"items": cards, "next": following}


# ---------------------------------------------------------------------------
# Přehrávání
# ---------------------------------------------------------------------------

def _link_get(lib, vid):
    try:
        cached = json.loads(lib.get_kv(LINK_PREFIX + vid) or "{}")
    except ValueError:
        return None
    if isinstance(cached, dict) and cached.get("url") and time.time() - cached.get("ts", 0) < provider.LINK_TTL:
        return cached
    return None


def _playable_video(lib, params):
    vid = params.get("vid", "")
    video = lib.find_video(vid)
    result = params.get("web_result")
    if not video and isinstance(result, dict) and vid and provider.find_video_id(vid) == vid:
        title = result.get("title")
        if isinstance(title, str) and title:
            # Uloží se až vybraný výsledek, aby šel použít stávající přehrávač, historie a stahování.
            lib.upsert_video(vid, title, "search", nameparser.parse(title), duration=result.get("duration", 0),
                             thumb=result.get("thumb", ""), size=result.get("size", ""), quality=result.get("quality", ""))
            video = lib.find_video(vid)
    if not video:
        raise ApiError("Video v katalogu není. Zkuste synchronizaci.")
    return video


def play(params):
    """Adresa souboru pro přehrávač: {"url", "headers", "subtitles", "position", "title", "track"}."""
    with _library() as lib:
        video = _playable_video(lib, params)
        vid = video["id"]
        meta = lib.get_meta(video["meta_key"]) or {}
        point = lib.resume_points([vid]).get(vid)
        if params.get("choice"):
            # verze spuštěná ze stránky titulu: příště bude první a u dílu seriálu nese stav zhlédnutí
            lib.set_kv(CHOICE_PREFIX + str(params["choice"]), vid)
            app_discover.remember_series(lib, str(params["choice"]))
    title = _video_title(video, meta)
    position = point[0] if point and not params.get("restart") else 0
    entry = _downloads().get(vid)
    if entry and entry.get("state") == "done" and os.path.exists(entry.get("path") or "") \
            and not params.get("online"):
        # stažený soubor: hraje z disku, bez internetu i bez přihlášení
        return {"url": entry["path"], "local": True, "headers": {},
                "subtitles": [name for name in entry.get("subtitles") or [] if os.path.exists(name)],
                "position": position, "title": title, "track": vid, "live": False, "hls": False}
    url, subtitles = _stream_link(vid, bool(params.get("fresh")))
    return {"url": url, "local": False, "headers": {"User-Agent": provider.USER_AGENT},
            "subtitles": list(subtitles.values()), "position": position, "title": title,
            "track": vid, "live": False, "hls": False}


def _video_title(video, meta):
    title = _display_title(meta or {}) or video["title"]
    if video["kind"] == "episode" and video["episode"]:
        title = "%s %d×%02d" % (title, video["season"], video["episode"])
    return title


def _stream_link(vid, fresh=False):
    """Adresa nejlepší verze souboru a titulky; zapamatovaná adresa se použije, dokud platí."""
    if not fresh:
        with _library() as lib:
            cached = _link_get(lib, vid)
        if cached:
            return cached["url"], cached.get("subtitles") or {}
    client = _client()
    try:
        streams, _message = client.get_streams(vid)
        if not streams:
            raise provider.VideoUnavailableError("Video nemá žádný přehratelný soubor")
        stream = _pick_stream(streams, _prefs())
        url = client.resolve(stream["url"])
        subtitles = stream.get("subtitles") or {}
    except provider.LoginError:
        raise ApiError("Přihlášení k Přehraj.to se nezdařilo. Zkontrolujte e-mail a heslo v Nastavení.")
    except provider.VideoUnavailableError:
        raise ApiError("Video už na Přehraj.to není dostupné.")
    except provider.ProviderError as error:
        raise ApiError("Přehraj.to: %s" % error)
    with _library() as lib:
        lib.set_kv(LINK_PREFIX + vid, json.dumps({"url": url, "subtitles": subtitles, "ts": int(time.time())}))
    return url, subtitles


def progress_save(params):
    """Uloží pozici přehrávání (aplikace volá průběžně a při zastavení)."""
    track = params.get("track") or ""
    position = float(params.get("position") or 0)
    total = float(params.get("total") or 0)
    if not track or total <= 0:
        return {"saved": False}
    if params.get("ended") and total - position <= max(15, total * 0.08):
        position = total  # doběhlo do konce → zhlédnuto
    with _library() as lib:
        if track.startswith(IVYS):
            lib.ivys_record_play(track[len(IVYS):], position, total)
        else:
            lib.record_play(track, position, total, started=bool(params.get("started")))
    return {"saved": True}


def play_failed(params):
    """Verzi se nepodařilo přehrát ani napodruhé – příště se nenabídne jako první."""
    track = params.get("track") or ""
    if not track or track.startswith(IVYS):
        return {"saved": False}
    with _library() as lib:
        bad = [vid for vid in _bad_videos(lib) if vid != track][-199:] + [track]
        lib.set_kv(BAD_VIDEOS, json.dumps(bad))
    return {"saved": True}


def link_failed(params):
    """Přehrávač adresu nepřehrál – zapomenout ji, aby se příště načetla nová."""
    with _library() as lib:
        lib.set_kv(LINK_PREFIX + (params.get("track") or ""), "")
    return {"forgotten": True}


# ---------------------------------------------------------------------------
# Účet, synchronizace, metadata
# ---------------------------------------------------------------------------

def settings_get(params):
    settings = _settings()
    prefs = _prefs(settings)
    return {"username": settings.get("username", ""), "has_password": bool(settings.get("password")),
            "tmdb_key": settings.get("tmdb_key", ""), "download_dir": settings.get("download_dir", ""),
            "prefer_language": prefs["language"], "prefer_quality": prefs["quality"],
            "languages": [{"id": key, "title": title} for key, title in LANGUAGE_CHOICES],
            "qualities": [{"id": key, "title": title} for key, title in QUALITY_CHOICES]}


def settings_set(params):
    settings = _settings()
    for key in ("username", "password", "tmdb_key", "download_dir", "prefer_language", "prefer_quality"):
        if key in params and params[key] is not None:
            settings[key] = str(params[key]).strip()
    _save_settings(settings)
    return settings_get({})


def account_status(params):
    try:
        result, days = _client().check_user()
    except provider.ProviderError as error:
        raise ApiError("Přehraj.to: %s" % error)
    if result == provider.CHECK_PREMIUM:
        return {"message": "Přihlášení v pořádku, Premium ještě %s dní." % provider.premium_text(days)}
    if result == provider.CHECK_NO_PREMIUM:
        return {"message": "Přihlášení v pořádku, účet nemá Premium (přehrají se překódované verze)."}
    raise ApiError("Přihlášení se nezdařilo. Zkontrolujte e-mail a heslo.")


def progress(params):
    """Stav běžící dlouhé operace (aplikace se ptá, zatímco čeká)."""
    return {"text": _STATE["progress"], "busy": _STATE["busy"]}


def _exclusive(work):
    if not _LOCK.acquire(False):
        raise ApiError("Jiná operace ještě běží, počkejte na její dokončení.")
    _STATE["busy"] = True
    try:
        return work()
    finally:
        _STATE["busy"] = False
        _STATE["progress"] = ""
        _LOCK.release()


def sync(params):
    def work():
        def on_progress(_stage, pages, count):
            _STATE["progress"] = "Načítám videa z účtu: stránka %d, %d videí" % (pages, count)
            return False

        _STATE["progress"] = "Přihlašuji se k Přehraj.to"
        try:
            client = _client()
            with _library() as lib:
                stats = catalog.sync_account(client, lib, remove_missing=True, progress=on_progress)
        except provider.LoginError:
            raise ApiError("Přihlášení k Přehraj.to se nezdařilo. Zkontrolujte e-mail a heslo v Nastavení.")
        except provider.ProviderError as error:
            raise ApiError("Synchronizace selhala: %s" % error)
        if stats.get("incomplete"):
            return {"message": "Výpis se nepodařilo načíst celý (%d videí). Nic se neodebralo, zkuste to znovu."
                    % stats["total"]}
        settings = _settings()
        settings["last_sync"] = int(time.time())
        _save_settings(settings)
        enriched = _enrich()
        return {"message": "Videí: %d, nových: %d, odebraných: %d. %s" % (
            stats["total"], stats["new"], stats["removed"], enriched)}
    return _exclusive(work)


def _enrich(statuses=("new",)):
    tmdb = _tmdb()
    with _library() as lib:
        metas = lib.meta_to_fetch(statuses)
        if not metas:
            return "Metadata jsou doplněná."

        def on_progress(index, total, title):
            _STATE["progress"] = "Doplňuji metadata %d/%d: %s" % (index + 1, total, title)
            return False

        stats = catalog.enrich(lib, metas, use_csfd=True, tmdb=tmdb, progress=on_progress)
    text = "Metadata doplněna u %d titulů, nenalezeno %d." % (stats["ok"], stats["notfound"])
    if stats["csfd_blocked"] and not tmdb.enabled:
        text += " ČSFD automatické dotazy blokuje – vyplňte v Nastavení klíč TMDB."
    return text


def enrich(params):
    return _exclusive(lambda: {"message": _enrich(("new", "notfound", "error", "blocked"))})


# ---------------------------------------------------------------------------
# iVysílání
# ---------------------------------------------------------------------------

def _ivys_state(lib):
    resume, watched = lib.ivys_states()
    return {"resume": resume, "watched": watched, "favorites": {item["show_id"] for item in lib.ivys_favorites()}}


def _ivys_show_card(item, note=""):
    year = " (%d)" % item["year"] if item.get("year") else ""
    subtitle = note or item.get("description") or ""
    if item.get("type") == "movie":  # film je jediné video – přehrát rovnou
        target = _target("play", "ivys_play", {"show": item["id"]})
    else:
        target = _target("list", "ivys_show", {"id": item["id"]}, item["title"])
    # obrázek na šířku má v iVysílání každý pořad, plakát jen některé: jednotný tvar karet ve výpisu
    return _card(item["title"] + year, target, subtitle[:120], item.get("image") or item.get("poster"), wide=True)


def _ivys_episode_card(item, state, show_title=""):
    title = item["title"] or show_title
    if item.get("show_title") and item["show_title"] != title:
        title = "%s · %s" % (item["show_title"], title)
    subtitle = " · ".join(part for part in (item.get("season"), item.get("date")) if part)
    return _card(title, _target("play", "ivys_play", {"idec": item["idec"]}), subtitle, item.get("image"),
                 _percent(state["resume"].get(item["idec"])), wide=True, watched=item["idec"] in state["watched"])


def _clock(timestamp):
    return time.strftime("%H:%M", time.localtime(timestamp)) if timestamp else ""


def _ivys_call(work):
    try:
        return work()
    except ivysilani.IvysilaniError as error:
        raise ApiError(str(error))


def ivys_live(params):
    try:
        # "quick": řada na hlavní obrazovce – program se nesmí načítat desítky sekund
        timeout = 4 if params.get("quick") else None
        channels = _cached("ivys:live", TTL["live"], lambda: _ivys(timeout=timeout).live_now())
    except ivysilani.IvysilaniError:
        channels = []  # program je jen doplněk – kanály zůstanou i bez něj
    listed = {channel["channel"] for channel in channels}
    channels = channels + [{"channel": channel, "name": name} for channel, name in ivysilani.LIVE_CHANNELS
                           if channel not in listed]
    cards = []
    for channel in channels:
        name = channel["name"] or channel["channel"]
        title = "%s · %s" % (name, channel["title"]) if channel.get("title") else name
        parts = []
        if channel.get("starts"):
            parts.append("%s–%s" % (_clock(channel["starts"]), _clock(channel.get("ends"))))
        if channel.get("next_title"):
            parts.append("pak %s %s" % (_clock(channel.get("next_starts")), channel["next_title"]))
        elapsed = 0
        if channel.get("starts") and channel.get("ends") and channel["ends"] > channel["starts"]:
            elapsed = max(1, min(100, int(100 * (time.time() - channel["starts"]) / (channel["ends"] - channel["starts"]))))
        cards.append(_card(title, _target("play", "ivys_play", {"channel": channel["channel"]}), " · ".join(parts),
                           channel.get("image"), elapsed, wide=True, icon="live"))
    return {"items": cards, "next": None}


def ivys_category(params):
    cid = str(params.get("cid") or "")
    category = _ivys_call(lambda: _cached("ivys:category:" + cid, TTL["category"], lambda: _ivys().category(cid)))
    menu = _ivys_call(lambda: _cached("ivys:menu", TTL["menu"], lambda: _ivys().menu()))
    cards = [_card("Všechny pořady", _target("list", "ivys_listing", {"cid": cid}, category["title"] or "Všechny pořady"),
                   "Úplný výpis kategorie", icon="all")]
    parent = next((item for item in menu if item["id"] == cid), {})
    # podkategorie: složka v barvě své kategorie (ilustraci má jen hlavní dlaždice)
    icon = "folder:" + IVYS_ICONS.get(parent.get("title"), "folder")
    for child in parent.get("children") or []:
        cards.append(_card(child["title"], _target("list", "ivys_listing", {"cid": child["id"]}, child["title"]),
                           "Podkategorie", icon=icon))
    seen = set()
    for block in category["blocks"]:
        for item in block["shows"]:
            if item["id"] not in seen:
                seen.add(item["id"])
                cards.append(_ivys_show_card(item, block["title"]))
    return {"items": cards, "next": None}


def ivys_listing(params):
    cid = str(params.get("cid") or "")
    offset = _offset(params)
    listing = _ivys_call(lambda: _cached("ivys:listing:%s:%d" % (cid, offset), TTL["category"],
                                         lambda: _ivys().category_listing(cid, offset)))
    cards = [_ivys_show_card(item) for item in listing["shows"]]
    return {"items": cards, "next": _next("ivys_listing", {"cid": cid}, offset, len(cards), listing["total"])}


def ivys_show(params):
    show_id = str(params.get("id") or "")
    programme = _ivys_call(lambda: _cached("ivys:show:" + show_id, TTL["show"], lambda: _ivys().show(show_id)))
    season = params.get("season") or ""
    with _library() as lib:
        lib.ivys_favorite_seen(show_id, programme["episode_count"])
        state = _ivys_state(lib)
    favorite = show_id in state["favorites"]
    toggle = _card("Odebrat z Mých pořadů" if favorite else "Přidat do Mých pořadů",
                   _target("command", "ivys_favorite", {"id": show_id, "remove": favorite}), icon="favorites")
    if programme["seasons"] and not season:
        cards = [_card(item["title"] or programme["title"],
                       _target("list", "ivys_show", {"id": show_id, "season": item["id"]},
                               "%s – %s" % (programme["title"], item["title"])), wide=True, icon="series")
                 for item in programme["seasons"]]
        cards.append(_card("Všechny díly", _target("list", "ivys_show", {"id": show_id, "season": "all"},
                                                   programme["title"]), "%d dílů" % programme["episode_count"], wide=True,
                           icon="all"))
        return {"title": programme["title"], "items": cards + [toggle], "next": None}
    offset = _offset(params)
    season_id = season if season not in ("", "all") else None
    key = "ivys:episodes:%s:%s:%d" % (programme["programme_id"], season_id or "", offset)
    page = _ivys_call(lambda: _cached(key, TTL["episodes"], lambda: dict(zip(("total", "items"), _ivys().episodes(
        programme["programme_id"], season_id, offset, programme["default_sort"])))))
    cards = [_ivys_episode_card(item, state, programme["title"]) for item in page["items"]]
    if not cards and programme["idec"] and programme["playable"] and not offset:
        cards.append(_ivys_episode_card({"idec": programme["idec"], "title": programme["title"],
                                         "image": programme["image"]}, state))
    following = _next("ivys_show", {"id": show_id, "season": season or "all"}, offset, len(page["items"]), page["total"])
    if not offset:
        cards.append(toggle)
    return {"title": programme["title"], "items": cards, "next": following}


def ivys_favorites(params):
    with _library() as lib:
        favorites = lib.ivys_favorites()
    cards = []
    for favorite in favorites:
        try:
            programme = _cached("ivys:show:" + favorite["show_id"], TTL["show"],
                                lambda: _ivys().show(favorite["show_id"]))
        except ivysilani.IvysilaniError:
            programme = None
        fresh = max(0, programme["episode_count"] - favorite["seen_count"]) if programme else 0
        title = favorite["title"] or (programme or {}).get("title", "")
        cards.append(_card(title, _target("list", "ivys_show", {"id": favorite["show_id"]}, title),
                           "nové díly: %d" % fresh if fresh else "", favorite["image"] or favorite["poster"],
                           wide=True))
    return {"items": cards, "next": None}


def ivys_favorite(params):
    show_id = str(params.get("id") or "")
    if params.get("remove"):
        with _library() as lib:
            lib.ivys_favorite_remove(show_id)
        return {"message": "Odebráno z Mých pořadů."}
    programme = _ivys_call(lambda: _cached("ivys:show:" + show_id, TTL["show"], lambda: _ivys().show(show_id)))
    with _library() as lib:
        lib.ivys_favorite_add(show_id, programme["title"], programme["image"], programme["poster"],
                              programme["episode_count"])
    return {"message": "Přidáno do Mých pořadů."}


def ivys_play(params):
    client = _ivys()
    if params.get("channel"):
        stream = _ivys_call(lambda: client.live(str(params["channel"]), timeshift=bool(params.get("timeshift"))))
        return {"url": stream["url"], "headers": {}, "subtitles": [], "position": 0, "title": stream["title"],
                "track": "", "live": True, "hls": True}
    idec = str(params.get("idec") or "") or _ivys_call(lambda: client.show(str(params.get("show") or "")))["idec"]
    stream = _ivys_stream(client, idec)
    with _library() as lib:
        lib.ivys_touch(idec, stream["show_id"], stream["episode_title"] or stream["title"], stream["show_title"],
                       stream["image"])
        point = lib.ivys_states()[0].get(idec)
    title = " · ".join(part for part in (stream["show_title"], stream["episode_title"]) if part) or stream["title"]
    return {"url": stream["url"], "headers": {}, "subtitles": stream["subtitles"],
            "position": point[0] if point and not params.get("restart") else 0, "title": title,
            "track": IVYS + idec, "live": False, "hls": not stream.get("dash"), "dash": bool(stream.get("dash")),
            "drm": stream.get("drm"),
            # chráněný pořad: když se nepřehraje, přehrávač ukáže, co rozhraní vrátilo (jen názvy polí)
            "diagnostic": (stream.get("shape") or "") if stream.get("dash") else ""}


def _ivys_stream(client, idec):
    """Stream pořadu; chráněný pořad se zkusí přes vestavěný Widevine zařízení.

    Nejdřív běžný (nechráněný) stream. Když ho ČT nevydá, zkusí se chráněný (DASH). Licenci si
    přehrávač vyžádá jen na adrese, kterou dodá ČT: v odpovědi rozhraní, nebo přímo v manifestu
    streamu. Územní omezení se neobchází: taková odpověď se rovnou předá.
    """
    try:
        return client.vod(idec)
    except ivysilani.NotPlayable as plain:
        if "území" in str(plain):
            raise ApiError(str(plain))
        try:
            stream = client.vod(idec, drm=True)
        except ivysilani.NotPlayable as protected:
            # ani chráněný stream ČT nevydala: k její hlášce přidat označení stavu, ať je vidět proč
            status = " / ".join(part for part in (getattr(plain, "status", ""), getattr(protected, "status", ""))
                                if part)
            raise ApiError(str(protected) + ("\n\n(iVysílání: %s)" % status if status else ""))
        except ivysilani.IvysilaniError as error:
            raise ApiError(str(error))
        # Adresa licence z odpovědi, je-li tam; jinak ji přehrávač vezme z manifestu streamu.
        stream["drm"] = stream.get("drm") or {"license": ""}
        return stream
    except ivysilani.IvysilaniError as error:
        raise ApiError(str(error))


# ---------------------------------------------------------------------------
# Vstupní bod
# ---------------------------------------------------------------------------
# Stahování na disk
# ---------------------------------------------------------------------------
# Evidenci vede Python (downloads.json), bajty přenáší služba v aplikaci: ptá se na další položku
# (download_next), hlásí průběh (download_progress) a výsledek (download_finish / download_fail).

def _downloads():
    with _DOWNLOADS_LOCK:
        try:
            with open(_path(DOWNLOADS), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return {vid: entry for vid, entry in data.items() if isinstance(entry, dict)} if isinstance(data, dict) else {}


def _save_downloads(items):
    with _DOWNLOADS_LOCK:
        temporary = _path(DOWNLOADS + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(temporary, _path(DOWNLOADS))


def _size_text(size):
    size = float(size or 0)
    if size >= 1024 ** 3:
        return ("%.1f GB" % (size / 1024 ** 3)).replace(".", ",")
    return "%d MB" % round(size / 1024 ** 2)


def _safe_name(title):
    """Název souboru použitelný i na FAT/exFAT."""
    name = "".join(" " if char in '\\/:*?"<>|' or ord(char) < 32 else char for char in title or "")
    return " ".join(name.split()).strip(" .")[:120] or "video"


def _download_status(entry):
    state = entry.get("state")
    total = entry.get("total") or 0
    if state == "done":
        if not os.path.exists(entry.get("path") or ""):
            return "Disk není připojený"
        return "Staženo · %s" % _size_text(total) if total else "Staženo"
    if state == "failed":
        return "Chyba: %s" % (entry.get("error") or "stahování selhalo")
    if state == "downloading":
        if total:
            return "Stahuje se · %d %% z %s" % (100 * (entry.get("done") or 0) // total, _size_text(total))
        return "Stahuje se · %s" % _size_text(entry.get("done"))
    return "Čeká na disk" if _STATE.get("no_disk") else "Ve frontě"


def _downloads_summary(items):
    done = sum(1 for entry in items.values() if entry.get("state") == "done")
    waiting = sum(1 for entry in items.values() if entry.get("state") in ("queued", "downloading"))
    parts = ["%d stažených" % done if done else "", "%d ve frontě" % waiting if waiting else ""]
    return " · ".join(part for part in parts if part) or "Podržte OK na filmu a zvolte Stáhnout"


def _download_cards(lib, state, limit=None):
    cards = []
    items = sorted(state["downloads"].values(), key=lambda entry: -(entry.get("added") or 0))
    for entry in items[:limit] if limit else items:
        vid = entry.get("vid") or ""
        total = entry.get("total") or 0
        percent = 100 * (entry.get("done") or 0) // total if total and entry.get("state") != "done" else 0
        point = state["resume"].get(vid)
        cards.append(_card(entry.get("title") or vid, _target("play", "play", {"vid": vid}), _download_status(entry),
                           entry.get("image"), percent or _percent(point), wide=bool(entry.get("wide")),
                           watched=vid in state["watched"], menu=_video_menu(state, {"vid": vid}, point), icon="download"))
    return cards


def downloads(params):
    with _library() as lib:
        cards = _download_cards(lib, _state(lib))
    return {"title": "Stahování", "items": cards, "next": None,
            "refresh": 3 if any(entry.get("state") in ("queued", "downloading")
                                for entry in _downloads().values()) else 0,
            "empty": "Zatím tu nic není.\n\nPodržte tlačítko OK na filmu nebo dílu a zvolte „Stáhnout na disk“. "
                     "Stažené filmy pak hrají i bez internetu."}


def download_add(params):
    """Zařadí video do fronty; stahovat začne služba aplikace."""
    with _library() as lib:
        video = _playable_video(lib, params)
        meta = lib.get_meta(video["meta_key"]) or {}
    vid = video["id"]
    with _DOWNLOADS_LOCK:
        items = _downloads()
        entry = items.get(vid) or {}
        if entry.get("state") == "done" and os.path.exists(entry.get("path") or ""):
            raise ApiError("Tohle video už je stažené.")
        title = _video_title(video, meta)
        items[vid] = {"vid": vid, "title": title, "image": meta.get("poster") or video.get("thumb") or "",
                      "wide": not meta.get("poster"), "state": "queued", "path": entry.get("path") or "",
                      "done": 0, "total": 0, "error": "", "subtitles": [], "added": int(time.time())}
        _save_downloads(items)
    return {"message": "Zařazeno ke stažení: %s" % title, "start": True}


def download_next(params):
    """Další položka pro službu: {"vid", "url", "headers", "path", "part", "subtitles"}; prázdné vid = hotovo."""
    directory = params.get("dir") or ""
    _STATE["no_disk"] = not directory
    if not directory:
        raise ApiError("Disk pro stahování není připojený. Připojte USB disk, nebo zvolte úložiště v Nastavení.")
    while True:
        with _DOWNLOADS_LOCK:
            items = _downloads()
            waiting = sorted((entry for entry in items.values() if entry.get("state") in ("downloading", "queued")),
                             key=lambda entry: (entry.get("state") != "downloading", entry.get("added") or 0))
        if not waiting:
            return {"vid": ""}
        entry = waiting[0]
        vid = entry["vid"]
        try:
            url, subtitles = _stream_link(vid, bool(params.get("fresh")))
        except ApiError as error:
            download_fail({"vid": vid, "error": str(error)})
            continue
        suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        name = _safe_name(entry.get("title")) + (suffix if suffix in VIDEO_SUFFIXES else ".mp4")
        target = entry.get("path") or ""
        if os.path.dirname(target) != directory:  # nová položka, nebo mezitím jiný disk
            target = os.path.join(directory, name)
            taken = {other.get("path") for other in items.values() if other.get("vid") != vid}
            if target in taken:
                base, extension = os.path.splitext(target)
                target = "%s [%s]%s" % (base, _safe_name(vid)[:12], extension)
        base = os.path.splitext(target)[0]
        tracks = []
        for index, link in enumerate(subtitles.values()):
            ending = ".srt" if urllib.parse.urlparse(link).path.lower().endswith(".srt") else ".vtt"
            tracks.append({"url": link, "path": "%s.%d%s" % (base, index + 1, ending)})
        with _DOWNLOADS_LOCK:
            items = _downloads()
            if vid not in items:
                continue  # mezitím zrušeno
            items[vid].update({"state": "downloading", "path": target, "error": ""})
            _save_downloads(items)
        return {"vid": vid, "title": entry.get("title") or "", "url": url,
                "headers": {"User-Agent": provider.USER_AGENT}, "path": target, "part": target + ".part",
                "subtitles": tracks}


def download_progress(params):
    """Průběh od služby; odpověď říká, jestli uživatel stahování mezitím nezrušil."""
    vid = params.get("vid") or ""
    with _DOWNLOADS_LOCK:
        items = _downloads()
        entry = items.get(vid)
        if not entry or entry.get("state") != "downloading":
            return {"cancel": True}
        entry["done"] = int(params.get("done") or 0)
        entry["total"] = int(params.get("total") or 0)
        _save_downloads(items)
    return {"cancel": False}


def download_finish(params):
    vid = params.get("vid") or ""
    with _DOWNLOADS_LOCK:
        items = _downloads()
        entry = items.get(vid)
        if not entry:
            return {"saved": False}
        size = int(params.get("size") or 0)
        entry.update({"state": "done", "done": size, "total": size, "error": "",
                      "subtitles": [str(name) for name in params.get("subtitles") or []]})
        _save_downloads(items)
    return {"saved": True}


def download_fail(params):
    vid = params.get("vid") or ""
    with _DOWNLOADS_LOCK:
        items = _downloads()
        if vid in items:
            items[vid].update({"state": "failed", "error": str(params.get("error") or "stahování selhalo")[:200]})
            _save_downloads(items)
    return {"saved": True}


def download_remove(params):
    """Zruší stahování nebo smaže stažený soubor (i rozpracovaný a titulky)."""
    vid = params.get("vid") or ""
    with _DOWNLOADS_LOCK:
        items = _downloads()
        entry = items.pop(vid, None)
        if entry is None:
            return {"message": ""}
        _save_downloads(items)
    target = entry.get("path") or ""
    for name in [target, target + ".part"] + list(entry.get("subtitles") or []):
        if name and name != ".part":
            try:
                os.remove(name)
            except OSError:
                pass  # soubor už není, nebo disk není připojený – evidence se smaže i tak
    return {"message": "Smazáno: %s" % entry.get("title") if entry.get("state") == "done" else "Stahování zrušeno."}


# ---------------------------------------------------------------------------
# Aktualizace aplikace
# ---------------------------------------------------------------------------

def _version(text):
    return tuple(int(part) for part in re.findall(r"\d+", str(text or ""))[:4])


def update_check(params):
    """Je na GitHubu novější verze aplikace? {"available", "version", "url", "size", "notes"}.

    Bez ``force`` se ptá nejvýš jednou za UPDATE_EVERY a potíže se sítí mlčky přejde.
    """
    current = params.get("current") or ""
    force = bool(params.get("force"))
    settings = _settings()
    if not force and time.time() - (settings.get("update_checked") or 0) < UPDATE_EVERY:
        return {"available": False, "current": current}
    request = urllib.request.Request(settings.get("update_url") or UPDATE_URL, headers={
        "User-Agent": "MojeVidea-Android/%s" % (current or "0"), "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            releases = json.loads(response.read().decode("utf-8"))
        if not isinstance(releases, list):
            raise ValueError("neočekávaná odpověď")
    except (OSError, ValueError) as error:
        if force:
            raise ApiError("Novou verzi se nepodařilo ověřit: %s" % error)
        return {"available": False, "current": current}
    best = None
    for release in releases:
        tag = str(release.get("tag_name") or "") if isinstance(release, dict) else ""
        if not tag.startswith(UPDATE_TAG) or release.get("draft") or release.get("prerelease"):
            continue
        asset = next((item for item in release.get("assets") or []
                      if str(item.get("name") or "").lower().endswith(".apk")), None)
        version = tag[len(UPDATE_TAG):]
        if asset and asset.get("browser_download_url") and (best is None or _version(version) > _version(best[0])):
            best = (version, asset, release)
    settings = _settings()
    settings["update_checked"] = int(time.time())
    _save_settings(settings)
    if best is None or _version(best[0]) <= _version(current):
        return {"available": False, "current": current}
    version, asset, release = best
    return {"available": True, "current": current, "version": version, "url": asset["browser_download_url"],
            "size": int(asset.get("size") or 0), "notes": str(release.get("body") or "").strip()[:800]}


# ---------------------------------------------------------------------------

import app_discover  # noqa: E402 – až po definicích výše: modul používá pomocné funkce odsud

ACTIONS = dict(app_discover.ACTIONS)
ACTIONS.update({
    "downloads": downloads, "download_add": download_add, "download_next": download_next,
    "download_progress": download_progress, "download_finish": download_finish, "download_fail": download_fail,
    "download_remove": download_remove, "update_check": update_check,
    "home": home, "movies": movies, "shows": shows, "show": show, "played": played, "search": search,
    "prehrajto_search": prehrajto_search, "search_history": search_history, "play_failed": play_failed,
    "play": play, "progress_save": progress_save, "link_failed": link_failed,
    "settings_get": settings_get, "settings_set": settings_set, "account_status": account_status,
    "progress": progress, "sync": sync, "enrich": enrich,
    "ivys_live": ivys_live, "ivys_category": ivys_category, "ivys_listing": ivys_listing, "ivys_show": ivys_show,
    "ivys_favorites": ivys_favorites, "ivys_favorite": ivys_favorite, "ivys_play": ivys_play,
})


def _offline(error):
    """Chyba vznikla výpadkem spojení (i když ji mezitím kód přeložil do srozumitelné hlášky)."""
    for _ in range(6):
        if error is None:
            break
        if isinstance(error, (urllib.error.URLError, ConnectionError, socket.timeout, socket.gaierror)) \
                and not isinstance(error, urllib.error.HTTPError):
            return True
        error = error.__cause__ or error.__context__
    return False


OFFLINE_TEXT = "Nepodařilo se připojit k serveru. Zkontrolujte připojení k internetu a zkuste to znovu."


def _failure(text, error):
    result = {"ok": False, "error": text}
    if _offline(error):
        # srozumitelná hláška a ilustrace „bez připojení“; technický popis jde jen do logu
        result.update(error=OFFLINE_TEXT, offline=True, detail=text)
    return json.dumps(result, ensure_ascii=False)


def call(action, params_json="{}"):
    """Jediný vstup pro aplikaci; nikdy nevyhazuje výjimku."""
    try:
        handler = ACTIONS.get(action)
        if handler is None:
            raise ApiError("Neznámá akce: %s" % action)
        params = json.loads(params_json or "{}")
        if not isinstance(params, dict):
            raise ApiError("Neplatné parametry.")
        return json.dumps({"ok": True, "data": handler(params)}, ensure_ascii=False)
    except (ApiError, TmdbError) as error:
        return _failure(str(error), error)
    except Exception as error:  # noqa: BLE001 – aplikace musí dostat odpověď, ne spadnout
        return _failure("Neočekávaná chyba: %s: %s" % (type(error).__name__, error), error)
