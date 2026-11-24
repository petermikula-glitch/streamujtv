# -*- coding: utf-8 -*-
"""Rozhraní mezi Android aplikací a logikou doplňku (katalog Přehraj.to, iVysílání).

Aplikace volá jedinou funkci ``call(akce, parametry_json)`` a dostane JSON:
``{"ok": true, "data": …}`` nebo ``{"ok": false, "error": "text pro uživatele"}``.
O tom, kam která položka vede, rozhoduje tento modul – každá karta nese ``target``:

* ``{"type": "list", "call": akce, "params": {…}, "title": …}`` – otevřít výpis,
* ``{"type": "page", "call": akce, "params": {…}, "title": …}`` – otevřít obrazovku s řadami karet
  (akce vrací ``{"title", "rows"}`` jako sekce hlavní obrazovky),
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
import search_results
from tmdb import Tmdb, TmdbError

PAGE = 60               # položek katalogu na stránku výpisu
HOME_ROW = 20           # položek v řadě na hlavní obrazovce
LINK_PREFIX = "link:"   # zapamatované adresy souborů (jako v doplňku)
IVYS = "ivys:"          # označení pořadu z iVysílání při ukládání pozice
# Oficiální aplikace iVysílání (pro Android TV a pro telefony) a stránka pořadu na webu ČT: chráněné
# pořady, které aplikace sama nepřehraje, se jim předávají. Balíčky musí být i v AndroidManifest.xml
# (<queries>), jinak je systém aplikaci neukáže.
IVYS_APPS = ("cz.ceskatelevize.ivysilani.tvapp", "cz.motion.ivysilani")
IVYS_WEB = "https://www.ceskatelevize.cz/porady/%s/"
TTL = {"menu": 24 * 3600, "category": 3600, "show": 3600, "episodes": 900, "search": 600, "live": 60}

DOWNLOADS = "downloads.json"   # evidence souborů stažených na disk (jen v aplikaci)
ERRORS = "errors.json"         # posledních pár chyb pro obrazovku Diagnostika
ERRORS_KEEP = 15
VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".webm", ".ts", ".mov", ".m4v", ".mpg", ".mpeg", ".wmv")
UPDATE_URL = "https://api.github.com/repos/petermikula-glitch/streamujtv/releases?per_page=20"
UPDATE_TAG = "app-v"           # vydání aplikace: značka app-v<verze>, příloha .apk
# Záloha pro případ, že rozhraní GitHubu odmítne dotaz (omezuje jejich počet na adresu – „rate limit
# exceeded“ – a tu může sdílet víc domácností): tatáž vydání téhož repozitáře přes běžné stránky GitHubu.
UPDATE_PAGE = "https://github.com/petermikula-glitch/streamujtv/releases/latest"
UPDATE_ASSET = "https://github.com/petermikula-glitch/streamujtv/releases/download/app-v%s/MojeVidea-%s.apk"
UPDATE_NOTES = "https://raw.githubusercontent.com/petermikula-glitch/streamujtv/app-v%s/android/RELEASE_NOTES.md"
UPDATE_EVERY = 6 * 3600        # automatická kontrola nové verze nejvýš jednou za tuto dobu

_STATE = {"dir": "", "progress": "", "busy": False}
_DOWNLOADS_LOCK = threading.RLock()
_LOCK = threading.Lock()   # dlouhé operace (synchronizace, metadata) běží nejvýš jedna


class ApiError(Exception):
    """Chyba s textem určeným uživateli."""


class Blocked(ApiError):
    """Chyba, která se netýká jednoho videa: chybí nebo selhalo přihlášení, služba je mimo provoz."""


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
    try:
        import app_health
        app_health.guard(directory)   # poškozený katalog se hned nahradí poslední dobrou kopií
    except Exception:                 # kontrola nesmí zabránit startu aplikace
        pass
    _STATE["quick_sync"] = 0   # nová složka s daty (nebo nový start aplikace): účet se zkontroluje znovu
    if proxy:
        os.environ["https_proxy"] = os.environ["http_proxy"] = str(proxy)
        if no_proxy:
            os.environ["no_proxy"] = str(no_proxy)
    return "ok"


def _path(name):
    if not _STATE["dir"]:
        raise ApiError("Aplikace není připravena (chybí složka pro data).")
    return os.path.join(_STATE["dir"], name)


_SETTINGS_LOCK = threading.RLock()   # nastavení mění víc akcí souběžně (hledání, synchronizace, aktualizace)


def _settings():
    with _SETTINGS_LOCK:
        try:
            with open(_path("settings.json"), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def _save_settings(values):
    with _SETTINGS_LOCK:
        temporary = _path("settings.json.%d.tmp" % threading.get_ident())
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False)
        os.replace(temporary, _path("settings.json"))


def _change_settings(change):
    """Přečte nastavení, upraví je funkcí ``change`` a uloží – celé pod zámkem, aby se souběžné změny
    (hledání a synchronizace, aktualizace…) navzájem nepřepsaly. Vrací nový stav."""
    with _SETTINGS_LOCK:
        settings = _settings()
        change(settings)
        _save_settings(settings)
        return settings


def _library():
    return db.Library(_path("catalog.db"), video_key=provider.video_key, prefer_ref=provider.prefer_ref)


def _client(allow_anonymous=False):
    settings = _settings()
    if not settings.get("username") or not settings.get("password"):
        if allow_anonymous:
            return provider.make_client("", "")
        raise Blocked("Nejdřív v Nastavení vyplňte e-mail a heslo k Přehraj.to.")
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

POSTER_LISTS = ("discover", "favorites", "movies", "shows", "profiles", "profile_avatars", "discover_person",
                "downloads", "discover_fresh", "discover_index")


def _target(kind, call=None, params=None, title=None):
    target = {"type": kind}
    if call:
        target["call"] = call
        target["params"] = params or {}
    if title:
        target["title"] = title
    if kind == "list" and call in POSTER_LISTS:
        target["poster"] = True   # výpis plakátů: aplikace rovnou připraví mřížku správného tvaru
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
    """Kolik procent je přehráno; rozkoukané video má aspoň 1 %, aby to na kartě bylo vidět."""
    if not point or not point[1]:
        return 0
    return max(1, int(100 * point[0] / point[1])) if point[0] > 0 else 0


def _languages(values):
    return ", ".join(part for part in (values or "").split(",") if part)


LANGUAGE_CHOICES = (("dub", "Český nebo slovenský dabing"), ("subs", "Originál s titulky"),
                    ("any", "Je mi to jedno"))
QUALITY_CHOICES = (("best", "Nejvyšší dostupná"), ("1080", "1080p"), ("720", "720p"),
                   ("small", "Úsporná (nejmenší soubor)"))
# Nejnižší kvalita, která se z Přehraj.to ještě nabízí: na velké televizi nižší rozlišení nevypadá dobře.
MINIMUM_CHOICES = (("", "Bez omezení"), ("720", "HD a vyšší (720p, 1080p, 4K)"),
                   ("1080", "Jen Full HD a vyšší (1080p, 2K, 4K)"), ("2160", "Jen 4K"))
MIN_QUALITY_DEFAULT = "720"
MINIMUM_LABEL = {"720": "HD", "1080": "Full HD", "2160": "4K"}
# Přehraj.to ve výpisu rozlišení neuvádí (jen štítek „HD“). Kde chybí i v názvu souboru, odhaduje se
# z datového toku (velikost / délka): od kolika b/s se soubor bere jako HD a Full HD.
# 4K se neodhaduje: velký soubor bývá stejně často Full HD ve vysokém toku.
ESTIMATE_BITRATE = {720: 1500000, 1080: 2000000}
LIKELY_FULL_HD = 6000000    # b/s: soubor označený jen „HD“ se od tohoto toku řadí mezi Full HD
TOKEN_LINES = {"UHD": 2160, "FHD": 1080, "HD": 720, "SD": 480}
# kopie natočená v kině: obraz i zvuk jsou výrazně horší, nabízí se až jako poslední
CAM_RE = re.compile(r"(?<![a-z0-9])(?:cam|camrip|hdcam|ts|hdts|telesync|tc|telecine|kinorip)(?![a-z0-9])", re.I)
BAD_VIDEOS = "bad_videos"       # verze, které se nepodařilo přehrát
CHOICE_PREFIX = "choice:"       # verze, kterou si uživatel u titulu sám vybral
REAL_PREFIX = "real:"           # skutečné rozlišení souboru (stránka videa, přehrávání)
ART_PREFIX = "art:"             # titul z Objevuj, ze kterého se video pustilo (kvůli obrázku na šířku)


def _prefs(settings=None):
    """Předvolby přehrávání z Přehraj.to: jazyk, kvalita a nejnižší kvalita, která se ještě nabízí."""
    settings = _settings() if settings is None else settings
    language, quality = settings.get("prefer_language"), settings.get("prefer_quality")
    minimum = settings.get("min_quality")
    if minimum == "1080" and not settings.get("min_quality_chosen"):
        # Verze 0.14.0 měla „jen Full HD“ jako výchozí a hodně filmů kvůli tomu nenabízela. Hodnota
        # uložená tehdy se bere jako nezvolená; kdo si Full HD vybere teď, tomu zůstane.
        minimum = None
    return {"language": language if language in dict(LANGUAGE_CHOICES) else "dub",
            "quality": quality if quality in dict(QUALITY_CHOICES) else "best",
            "minimum": minimum if minimum in dict(MINIMUM_CHOICES) else MIN_QUALITY_DEFAULT,
            # s vyplněným účtem Přehraj.to nabízí i původní soubor (jinak jen překódované verze)
            "account": bool(settings.get("username") and settings.get("password"))}


def _exact_token(quality):
    """Kvalita souboru, jen když ji údaj říká přesně. Samotné „HD“ přesné není: tak Přehraj.to ve výpisu
    označuje 720p i 1080p (a stejně volně to slovo píší lidé do názvů souborů)."""
    token = provider.quality_token(quality or "")
    return "" if token == "HD" and not re.search(r"\d", quality or "") else token


def _picture_lines(width, height):
    """Rozlišení podle skutečných rozměrů obrazu jako počet řádků (2160, 1440, 1080, 720…).
    Rozhoduje i šířka: širokoúhlý film 1920×800 je Full HD, ne „800p“."""
    for min_width, min_height, lines in ((3200, 1700, 2160), (2400, 1300, 1440), (1800, 950, 1080), (1200, 650, 720)):
        if width >= min_width or height >= min_height:
            return lines
    return max(int(height), 0)


def _lines_label(lines):
    return "4K" if lines >= 2160 else "%dp" % lines


def _real(lib, vid):
    """Co se o souboru ví doopravdy: ``heights`` = překódované verze, které stránka videa nabízí,
    ``played`` = rozlišení původního souboru změřené při přehrávání, ``local`` = totéž u staženého."""
    try:
        real = json.loads(lib.get_kv(REAL_PREFIX + vid) or "{}")
    except ValueError:
        return {}
    return real if isinstance(real, dict) else {}


def _real_note(vid, **changes):
    with _library() as lib:
        real = dict(_real(lib, vid), **changes)
        lib.set_kv(REAL_PREFIX + vid, json.dumps(real))
    return real


def _real_from_streams(vid, streams):
    """Zapamatuje si, v jakých rozlišeních Přehraj.to soubor opravdu nabízí."""
    heights = set()
    for stream in streams:
        digits = re.match(r"\d+", str(stream.get("resolution") or ""))
        if stream.get("quality") != "ORIG" and digits:
            heights.add(int(digits.group()))
    return _real_note(vid, heights=sorted(heights, reverse=True), checked=int(time.time()))


def real_fetch(client, vid):
    """Podívá se na stránku videa, co Přehraj.to opravdu nabízí (jeden dotaz; výsledek se pamatuje)."""
    streams, _message = client.get_streams(vid)
    return _real_from_streams(vid, streams)


def _measured_lines(video, prefs=None):
    """Rozlišení toho, co si uživatel pustí, pokud je známé jistě (počet řádků), jinak 0.

    Bez účtu se hraje překódovaná verze a ty stránka videa vyjmenuje. S účtem se hraje původní soubor
    a jeho rozlišení stránka neuvádí: jisté je až po prvním přehrání, kdy ho změří přehrávač."""
    real = video.get("real") or {}
    if (prefs or {}).get("account"):
        return real.get("played") or 0
    heights = real.get("heights") or []
    return max(heights) if heights else 0


def _least_lines(video, prefs=None):
    """Rozlišení, které soubor má přinejmenším: nejvyšší překódovaná verze na stránce videa (původní
    soubor nemůže být horší) a přesný údaj v názvu souboru („1080p“, „4K“)."""
    measured = _measured_lines(video, prefs)
    if measured:
        return measured
    heights = (video.get("real") or {}).get("heights") or []
    return max(heights + [TOKEN_LINES.get(_exact_token(video.get("quality")), 0)])


def _bitrate(video):
    """Datový tok souboru v b/s podle velikosti a délky; 0, když jedno z toho chybí."""
    size, duration = _size_bytes(video.get("size")), video.get("duration") or 0
    return size * 8.0 / duration if size and duration else 0


def _playing_quality(video, prefs, saved=False):
    """Kvalita, ve které se soubor opravdu přehraje („1080p“, „originál 4K“); "" dokud se neví."""
    real = video.get("real") or {}
    if saved:
        return "z disku %s" % _lines_label(real["local"]) if real.get("local") else ""
    heights = real.get("heights") or []
    streams = [{"quality": provider.quality_token(str(height)), "lines": height} for height in heights]
    if prefs.get("account") and (real.get("checked") or real.get("played")):
        streams.insert(0, {"quality": "ORIG"})
    if not streams:
        return ""
    chosen = _pick_stream(streams, prefs)
    if chosen["quality"] != "ORIG":
        return "%dp" % chosen["lines"]
    lines = _least_lines(video, prefs)
    if lines >= 1080 or _measured_lines(video, prefs):
        return "originál %s" % _lines_label(lines)
    # Nabízí-li stránka jen 720p, o původním souboru to říká jen „HD nebo lepší“.
    return "originál HD" if lines >= 720 or provider.quality_token(video.get("quality") or "") == "HD" else "originál"


def _shown_quality(video, prefs, saved=False):
    """Kvalita na kartě verze: skutečná, když je známá; jinak údaj z výpisu nebo z názvu souboru."""
    return _playing_quality(video, prefs, saved) or video.get("quality") or ""


def _need_lines(prefs):
    minimum = str((prefs or {}).get("minimum") or "")
    return int(minimum) if minimum.isdigit() else 0


def _meets_minimum(video, prefs):
    """Vyhovuje soubor nejnižší kvalitě, kterou si uživatel přeje vidět (HD / Full HD / 4K a vyšší)?

    Rozhoduje, co je jisté: změřené rozlišení, překódované verze na stránce videa a přesný údaj v názvu
    souboru („1080p“, „4K“). Štítek „HD“ z výpisu Přehraj.to znamená jen „720p nebo víc“ – nese ho
    i Full HD a 4K –, a tak se u něj (stejně jako u souboru bez údaje) vyšší kvalita odhaduje
    z velikosti a délky. Soubor, o kterém se neví nic, neprojde."""
    need = _need_lines(prefs)
    if not need:
        return True
    measured = _measured_lines(video, prefs)
    if measured:
        return measured >= need
    if _least_lines(video, prefs) >= need:
        return True
    exact = _exact_token(video.get("quality"))
    if exact:
        return TOKEN_LINES.get(exact, 0) >= need
    if need <= 720 and provider.quality_token(video.get("quality") or "") == "HD":
        return True
    estimate = ESTIMATE_BITRATE.get(need)
    return bool(estimate) and _bitrate(video) >= estimate


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
    """Pořadí verze podle předvolby jazyka. Český a slovenský dabing jsou si rovny (stejně jako české
    a slovenské titulky): mezi nimi pak rozhoduje kvalita."""
    parts = {part.strip() for part in (lang or "").split(",") if part.strip()}
    dubbed = bool(parts & {"CZ", "CZ dabing", "SK", "SK dabing"})
    subtitled = bool(parts & {"CZ titulky", "SK titulky"})
    if prefer == "subs":   # původní zvuk s titulky, pak cokoli s titulky, dabing až nakonec
        return 0 if subtitled and not dubbed else 1 if subtitled else 2 if not dubbed else 3
    if prefer == "dub":
        return 0 if dubbed else 1 if subtitled else 2
    return 0


def _known_quality(listed, named):
    """Kvalita souboru: údaj z výpisu Přehraj.to, a když mu aplikace nerozumí, kvalita z názvu souboru."""
    if _exact_token(listed):
        return listed
    if _exact_token(named):
        return named       # výpis říká jen „HD“ (nebo nic), název souboru přesně „1080p“
    if provider.quality_token(listed or ""):
        return listed
    return named if provider.quality_token(named or "") else (listed or named or "")


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
    lines, rate = _least_lines(video, prefs), _bitrate(video)
    quality = video.get("quality")
    if lines >= 1080 or _measured_lines(video, prefs):
        quality = "%dp" % lines
    elif not _exact_token(quality) and rate >= LIKELY_FULL_HD:
        quality = "1080p"   # jen „HD“ nebo nic, ale velký soubor: řadí se mezi Full HD
    # Při stejné kvalitě má u „nejvyšší dostupné“ přednost soubor s vyšším datovým tokem (větší na
    # minutu = lepší obraz); dřív rozhodovala abeceda.
    return (bool(CAM_RE.search(name)), video.get("id") in bad, _language_rank(video.get("lang"), prefs["language"]),
            _quality_rank(quality, video.get("size"), prefs["quality"]),
            -int(rate) if prefs["quality"] == "best" else 0, name)


def _pick_stream(streams, prefs):
    """Varianta souboru k přehrání (originál, 2160p, 1080p…) podle předvolené kvality."""
    order = provider.QUALITY_ORDER
    quality = prefs["quality"]
    need = _need_lines(prefs)   # nejnižší povolená kvalita má přednost před úspornou předvolbou
    if need >= 1080 and quality in ("720", "small"):
        quality = "1080"
    elif need >= 720 and quality == "small":
        quality = "720"

    def rank(stream):
        token = stream.get("quality")
        index = order.index(token) if token in order else len(order)
        if quality == "best":
            return index
        if quality == "small":
            return -index if token != "ORIG" and index < len(order) else 1
        wanted = order.index("FHD" if quality == "1080" else "HD")
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
    menu = [{"title": "Otevřít", "target": target}] + _favorite_menu(state, meta) + [
        {"title": "Opravit přiřazení", "target": _target("list", "repair", {"key": meta["key"]}, "Opravit přiřazení")}
    ] if state else None
    return _card(title, target, subtitle, meta.get("poster") or meta.get("thumb"), wide=not meta.get("poster"),
                 icon="series", menu=menu)


def _video_card(video, meta, state, prefix="", web_result=None):
    meta = meta or {}
    title = _display_title(meta) or video["title"]
    if video["kind"] == "episode":
        label = "%s %s" % (title, _episode_code(video, "x")) if video["episode"] else video["raw_title"]
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
    """Nabídka po podržení OK na videu z Přehraj.to: přehrání, stahování na disk, zhlédnuto a Rozkoukané."""
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
        items.append({"title": "Přesunout na jiný disk", "target": _target("move", "download_move", {"vid": vid})})
    elif entry.get("state") == "failed":
        items.append({"title": "Zkusit stáhnout znovu", "target": _target("download", "download_add", params)})
        items.append({"title": "Odebrat ze stahování", "target": remove})
    else:
        items.append({"title": "Zrušit stahování", "target": remove})
    if not params.get("web_result"):   # jen video, které aplikace zná z katalogu nebo z dřívějšího přehrání
        seen = vid in state.get("watched", ())
        items.append({"title": "Označit jako nezhlédnuté" if seen else "Označit jako zhlédnuté",
                      "target": _target("command", "watched_set", {"vid": vid, "watched": not seen})})
    if point and point[0] > 0:
        items.append({"title": "Odebrat z Rozkoukaných", "target": _target("command", "progress_remove", {"vid": vid})})
    if not params.get("web_result"):
        items.append({"title": "Opravit přiřazení", "target": _target("list", "repair", {"vid": vid}, "Opravit přiřazení")})
    return items


# ---------------------------------------------------------------------------
# Hlavní obrazovka
# ---------------------------------------------------------------------------

def _in_progress(lib, state, limit=HOME_ROW):
    """Rozkoukaná videa z Přehraj.to."""
    videos = lib.history_videos(in_progress=True, limit=limit, include_search=True)
    metas = lib.get_metas(video["meta_key"] for video in videos)
    return [_video_card(video, metas.get(video["meta_key"]), state) for video in videos]


def _ivys_label(row):
    """Název rozkoukaného videa z iVysílání: „pořad · díl“, u filmu jen název."""
    if row["show_title"] and row["title"] and row["show_title"] != row["title"]:
        return "%s · %s" % (row["show_title"], row["title"])
    return row["title"] or row["show_title"]


def _ivys_progress_card(row):
    point = (row["position"], row["total"])
    target = _target("play", "ivys_play", {"idec": row["idec"]})
    menu = [{"title": "Pokračovat", "target": target},
            {"title": "Přehrát od začátku", "target": _target("play", "ivys_play", {"idec": row["idec"], "restart": True})},
            {"title": "Odebrat z Rozkoukaných", "target": _target("command", "progress_remove", {"idec": row["idec"]})}]
    return _card(_ivys_label(row), target, "iVysílání", row["image"], _percent(point), wide=True, menu=menu)


def _ivys_in_progress(lib, limit=HOME_ROW):
    """Rozkoukané pořady z iVysílání."""
    return [_ivys_progress_card(row) for row in lib.ivys_progress(limit)]


IVYS_KIND = "ivys_kind:"   # zapamatovaný druh pořadu z iVysílání: movie (jediné video), nebo series


def _ivys_remember_kind(lib, show_id, kind):
    """Zapamatuje, jestli je pořad film: podle toho se řadí v Oblíbených a Rozkoukaných."""
    value = "movie" if kind == "movie" else "series"
    if show_id and kind and lib.get_kv(IVYS_KIND + show_id) != value:
        lib.set_kv(IVYS_KIND + show_id, value)


def _ivys_is_film(lib, show_id, title="", show_title=""):
    """Je pořad z iVysílání film? Zapamatovaný druh, jinak údaj z mezipaměti, jinak odhad podle názvů
    (díl má vedle názvu pořadu i svůj vlastní). Bez dotazu na síť – používá to hlavní obrazovka."""
    kind = lib.get_kv(IVYS_KIND + show_id) if show_id else None
    if not kind and show_id:
        cached = lib.cache_get("app:ivys:show:" + show_id)
        kind = cached.get("type") if isinstance(cached, dict) else None
    if kind:
        return kind == "movie"
    return bool(title or show_title) and (not title or not show_title or title == show_title)


def _ivys_favorite_card(item, film):
    """Oblíbený pořad z iVysílání v sekci Oblíbené: film se rovnou přehraje, pořad otevře své díly."""
    if film:
        target = _target("play", "ivys_play", {"show": item["show_id"]})
    else:
        target = _target("list", "ivys_show", {"id": item["show_id"]}, item["title"])
    menu = [{"title": "Přehrát" if film else "Otevřít", "target": target}]
    if not film:
        menu.append({"title": "Pokračovat dalším dílem",
                     "target": _target("play", "ivys_play", {"show": item["show_id"], "following": True})})
    menu += [{"title": "Odebrat z oblíbených",
              "target": _target("command", "ivys_favorite", {"id": item["show_id"], "remove": True})}]
    return _card(item["title"], target, "iVysílání", item["poster"] or item["image"], wide=not item["poster"],
                 icon="favorites", menu=menu)


def ivys_favorite_cards(lib, kind=None, limit=None):
    """Oblíbené pořady z iVysílání; ``kind`` (``movie`` / ``tv``) vybere jen filmy, nebo jen pořady s díly."""
    items = [(item, _ivys_is_film(lib, item["show_id"])) for item in lib.ivys_favorites()]
    cards = [_ivys_favorite_card(item, film) for item, film in items
             if kind is None or film == (kind == "movie")]
    return cards[:limit] if limit else cards


def home(params):
    """Hlavní obrazovka: sekce postranní nabídky, každá se svými řadami karet.

    ``{"sections": [{"id", "title", "rows": [{"title", "items"}]}], "start", "pending"}``. Sekce: Oblíbené
    a Rozkoukané (filmy a seriály z Přehraj.to i z iVysílání pohromadě), Objevuj (složky Filmy a Seriály),
    iVysílání a Nástroje. ``start`` je sekce, kterou aplikace po spuštění otevře: první, ve které něco je.
    """
    settings = _settings()
    with _library() as lib:
        state = _state(lib)
        empty = not lib.count_videos(visible_only=True)
        own = {"favorites": _favorite_rows(lib, state), "progress": _progress_rows(lib, state),
               "discover": _discover_rows(empty and not settings.get("username"))}
        # prázdná sekce má jen nápovědu: po spuštění se otevře první, ve které něco je
        filled = [name for name in ("favorites", "progress") if not own[name][0].get("hint")]
        television = _ivysilani_rows(lib)
        # velký panel „Pokračovat“ mají sekce Rozkoukané a iVysílání
        heroes = {name: app_extras.hero(lib, state, name) for name in ("progress", "ivysilani")}
    profile = app_extras.current()
    last = settings.get("last_sync")
    tools = [{"title": "Nástroje", "items": [
        # Hledání tu není: lupa je nahoře na každé obrazovce. Synchronizace s účtem je v Nastavení
        # (nová videa a chybějící hodnocení si aplikace doplňuje sama po otevření).
        app_guide.tile(),
        _card("Stahování", _target("list", "downloads", {}, "Stahování"), _downloads_summary(state["downloads"]),
              icon="download"),
        _card("Nastavení a přihlášení", _target("settings"), settings.get("username") or "Nepřihlášeno",
              icon="settings"),
        _card("Jazyk a kvalita přehrávání", _target("choices", "prefs_choices", {}),
              "%s · %s" % (dict(LANGUAGE_CHOICES)[state["prefs"]["language"]],
                           dict(QUALITY_CHOICES)[state["prefs"]["quality"]]), icon="settings:movies"),
        _card("Profil: %s" % profile["name"], _target("list", "profiles", {}, "Kdo se dívá?"),
              "Přepnout nebo přidat profil", "res:avatar_" + profile["avatar"], wide=True),
        # záloha: kam se uloží, vybírá aplikace (zná připojené disky); co v ní je, řeší ``app_backup``
        _card("Záloha a obnova", _target("backup"), "Data aplikace do souboru na disku a zpět",
              icon="folder:history"),
        _card("Diagnostika", _target("diagnostics"), "Stav účtu, připojení a poslední chyby",
              icon="settings:history"),
    ]}]
    # "pending": ve frontě něco čeká – aplikace po startu znovu spustí službu stahování
    sections = [{"id": "favorites", "title": "Oblíbené", "icon": "section_favorites", "rows": own["favorites"]},
                {"id": "progress", "title": "Rozkoukané", "icon": "section_progress", "rows": own["progress"]},
                {"id": "discover", "title": "Objevuj", "icon": "section_discover", "rows": own["discover"]},
                {"id": "ivysilani", "title": "iVysílání", "icon": "section_ivysilani", "rows": television},
                {"id": "tools", "title": "Nástroje", "icon": "section_tools", "rows": tools}]
    for section in sections:
        if heroes.get(section["id"]):
            section["hero"] = heroes[section["id"]]   # velký panel s naposledy rozkoukaným videem
    return {"sections": sections,
            "start": [section["id"] for section in sections].index((filled + ["discover"])[0]),
            "profile": {"name": profile["name"], "avatar": profile["avatar"], "count": profile["count"]},
            # první spuštění bez účtu a katalogu: aplikace nabídne průvodce
            "welcome": bool(empty and not settings.get("username") and not settings.get("onboarded")),
            "pending": any(entry.get("state") in ("queued", "downloading")
                           for entry in state["downloads"].values())}


def _hint(title, text, icon, target=None):
    return [_card(title, target or _target("refresh"), text, icon=icon)]


def _discover_rows(newcomer):
    """Sekce Objevuj: složky Filmy a Seriály (každá otevře svou obrazovku dlaždic) a hledání; pod nimi
    řady právě populárních titulů, které si aplikace donačte sama (``call``), aby hlavní obrazovka
    nečekala na síť."""
    rows = []
    if newcomer:
        rows.append({"title": "Začínáme", "items": [
            _card("Přihlásit se k Přehraj.to", _target("settings"), "E-mail a heslo, pak Synchronizovat",
                  icon="login")]})
    rows += _tile_rows("Objevuj", [
        _card("Filmy", _target("page", "discover_home", {"kind": "movie"}, "Filmy"),
              "Novinky, žánry, stažené a moje filmy", icon="movies"),
        _card("Seriály", _target("page", "discover_home", {"kind": "tv"}, "Seriály"),
              "Novinky, žánry, stažené díly a moje seriály", icon="series"),
        _card("Hledat", _target("search"), "Film, seriál, herec nebo režisér", icon="search"),
    ])
    if _tmdb().enabled:
        # co aplikace na Přehraj.to našla nově; dokud nic takového není, řada se neukazuje
        rows.append({"title": "Nově na Přehraj.to", "items": [], "poster": True, "call": "discover_fresh_row",
                     "params": {}, "optional": True})
        for kind, title in (("movie", "Právě populární filmy"), ("tv", "Právě populární seriály")):
            rows.append({"title": title, "items": [], "poster": True, "call": "discover_row",
                         "params": {"kind": kind, "category": "popular"}})
    return rows


def discover_home(params):
    """Obrazovka složky Filmy / Seriály ze sekce Objevuj: ``{"title", "rows"}`` jako sekce hlavní obrazovky."""
    series = params.get("kind") == "tv"
    with _library() as lib:
        state = _state(lib)
        rows = _show_rows(lib, state) if series else _movie_rows(lib, state)
    return {"title": "Seriály" if series else "Filmy", "rows": rows}


TILES_PER_ROW = 3   # velké dlaždice (sekce Objevuj, složky Filmy a Seriály): tři vedle sebe přes celou šířku


def _tile_rows(title, tiles):
    """Mřížka velkých dlaždic: řady po třech, nadpis jen u první."""
    return [{"title": title if index == 0 else "", "items": tiles[index:index + TILES_PER_ROW], "tiles": True}
            for index in range(0, len(tiles), TILES_PER_ROW)]


def _saved_count(lib, state, episodes):
    """Kolik stažených videí jsou filmy / díly seriálů (hotové i rozpracované)."""
    return len([entry for entry in state["downloads"].values()
                if ((lib.find_video(entry.get("vid") or "") or {}).get("kind") == "episode") == episodes])


def _counted(title, count):
    return "%s (%d)" % (title, count) if count else title


def _movie_rows(lib, state):
    """Složka Filmy: mřížka dlaždic (objevování, stažené, vlastní filmy, hledání) a nově přidané filmy z účtu."""
    rows = _tile_rows("Objevuj filmy", app_discover.home_tiles("movie"))
    rows += _tile_rows("Moje filmy", [
        _card(_counted("Stažené filmy", _saved_count(lib, state, False)),
              _target("list", "downloads", {"kind": "movie"}, "Stažené filmy"), "Hrají i bez internetu", icon="download"),
        _card(_counted("Filmy z mého účtu", lib.count_movies()),
              _target("list", "movies", {"order": "added"}, "Filmy z mého účtu"), "Nahrané na Přehraj.to", icon="movies"),
        _card("Hledat", _target("search"), "Film, herec nebo režisér", icon="search"),
    ])
    movies = lib.movies(order="added", limit=HOME_ROW)
    if movies:
        versions = lib.videos_grouped([m["key"] for m in movies if (m.get("video_count") or 1) > 1])
        rows.append({"title": "Nově přidané z účtu", "items": [_movie_card(meta, state, versions) for meta in movies]})
    return rows


def _show_rows(lib, state):
    """Složka Seriály: mřížka dlaždic (objevování, stažené díly, vlastní seriály, hledání) a seriály z účtu."""
    rows = _tile_rows("Objevuj seriály", app_discover.home_tiles("tv"))
    shows = lib.shows(order="added", limit=HOME_ROW)
    rows += _tile_rows("Moje seriály", [
        _card(_counted("Stažené díly", _saved_count(lib, state, True)),
              _target("list", "downloads", {"kind": "episode"}, "Stažené díly"), "Hrají i bez internetu", icon="download"),
        _card("Seriály z mého účtu", _target("list", "shows", {"order": "title"}, "Seriály z mého účtu"),
              "Nahrané na Přehraj.to", icon="series"),
        _card("Hledat", _target("search"), "Seriál, herec nebo režisér", icon="search"),
    ])
    if shows:
        rows.append({"title": "Nově přidané z účtu", "items": [_show_card(meta, state) for meta in shows]})
    return rows


def _favorite_rows(lib, state):
    """Sekce Oblíbené: filmy a seriály, v každé řadě tituly z Přehraj.to i pořady z iVysílání."""
    rows = []
    for kind, name in (("movie", "Filmy"), ("tv", "Seriály")):
        count = len([entry for entry in state["favorites"].values() if (entry.get("kind") == "tv") == (kind == "tv")])
        television = ivys_favorite_cards(lib, kind)
        cards = app_discover.favorite_cards(lib, state, HOME_ROW, kind) + television[:HOME_ROW]
        if count > HOME_ROW or len(television) > HOME_ROW:
            cards.append(_card("Všechny", _target("list", "favorites", {"kind": kind}, "Oblíbené – " + name.lower()),
                               "%d položek" % (count + len(television)), icon="favorites"))
        if cards:
            rows.append({"title": name, "items": cards})
    if not rows:
        rows.append({"title": "Oblíbené", "hint": True, "items": _hint(
            "Zatím nic", "Podržte OK na filmu nebo seriálu a zvolte Přidat do oblíbených", "favorites",
            _target("list", "favorites", {}, "Oblíbené"))})
    return rows


def _progress_rows(lib, state):
    """Sekce Rozkoukané: nedokoukané filmy a díly seriálů, z Přehraj.to i z iVysílání pohromadě."""
    rows = []
    videos = lib.history_videos(in_progress=True, limit=2 * HOME_ROW, include_search=True)
    metas = lib.get_metas(video["meta_key"] for video in videos)
    television = lib.ivys_progress(2 * HOME_ROW)
    for name, episodes in (("Filmy", False), ("Seriály", True)):
        found = [(video["last_played"] or 0, _video_card(video, metas.get(video["meta_key"]), state))
                 for video in videos if (video["kind"] == "episode") == episodes]
        found += [(row["last_played"] or 0, _ivys_progress_card(row)) for row in television
                  if _ivys_is_film(lib, row["show_id"], row["title"], row["show_title"]) != episodes]
        found.sort(key=lambda entry: -entry[0])   # naposledy přehrané první, ať jsou odkudkoli
        if found:
            rows.append({"title": name, "items": [card for _played, card in found[:HOME_ROW]]})
    following = app_discover.next_up_cards(lib, state)
    if following:
        rows.append({"title": "Další díly", "items": following})
    if not rows:
        rows.append({"title": "Rozkoukané", "hint": True, "items": _hint(
            "Nic rozkoukaného", "Sem se uloží film nebo díl, který nedokoukáte", "history",
            _target("list", "played", {}, "Naposledy přehrané"))})
    if lib.history_videos(limit=1, include_search=True):
        rows.append({"title": "Historie", "items": [
            _card("Naposledy přehrané", _target("list", "played", {}, "Naposledy přehrané"), icon="history")]})
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
        for category in _ivys_quick("ivys:menu", TTL["menu"], lambda: _ivys(timeout=4).menu()):
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


ORDERS = (("title", "Podle názvu"), ("added", "Nově přidané"), ("rating", "Podle hodnocení"), ("year", "Podle roku"))
CATALOG_RATINGS = (("", "Vše"), ("60", "60 % a víc"), ("70", "70 % a víc"), ("80", "80 % a víc"))


def _catalog_filter(params):
    """Platné filtry výpisu vlastního katalogu: řazení, žánr, desetiletí, nejnižší hodnocení."""
    order = params.get("order") if params.get("order") in dict(ORDERS) else "title"
    chosen = {"order": order}
    if params.get("genre"):
        chosen["genre"] = str(params["genre"])
    if str(params.get("decade") or "").isdigit():
        chosen["decade"] = str(params["decade"])
    if str(params.get("rating") or "").isdigit():
        chosen["rating"] = str(params["rating"])
    return chosen


def _catalog_filter_card(call, chosen):
    parts = [chosen.get("genre") or "", "%s–%d" % (chosen["decade"], int(chosen["decade"]) + 9) if chosen.get("decade") else "",
             "%s %%+" % chosen["rating"] if chosen.get("rating") else ""]
    summary = " · ".join(part for part in parts if part)
    return _card("Filtr", {"type": "filter", "call": "catalog_filters", "params": dict(chosen, list=call)},
                 summary or "Žánr, období, hodnocení, řazení", icon="settings:search")


def catalog_filters(params):
    """Filtry výpisu filmů nebo seriálů z vlastního katalogu (stejný tvar jako ``discover_filters``)."""
    call = "shows" if params.get("list") == "shows" else "movies"
    kind = "show" if call == "shows" else "movie"
    chosen = _catalog_filter(params)
    with _library() as lib:
        genres = [("", "Vše")] + [(name, "%s (%d)" % (name, count)) for name, count in lib.genres(kind)]
        decades = [("", "Vše")] + [(str(decade), "%d–%d (%d)" % (decade, decade + 9, count))
                                   for decade, count in lib.decades(kind) if decade]

    def options(key, choices, current):
        return [{"title": title, "current": str(value) == str(current),
                 "params": dict({k: v for k, v in chosen.items() if k != key}, **({key: value} if value else {}))}
                for value, title in choices]

    groups = [("Žánr", "genre", genres, chosen.get("genre", ""))]
    if call == "movies":
        groups.append(("Období", "decade", decades, chosen.get("decade", "")))
    groups += [("Hodnocení", "rating", list(CATALOG_RATINGS), chosen.get("rating", "")),
               ("Řazení", "order", list(ORDERS), chosen["order"])]
    filters = []
    for title, key, choices, current in groups:
        listed = options(key, choices, current)
        filters.append({"title": title, "value": next((o["title"] for o in listed if o["current"]), "Vše"),
                        "options": listed})
    active = any(chosen.get(key) for key in ("genre", "decade", "rating"))
    return {"title": "Filtr", "filters": filters, "reset": {"order": chosen["order"]} if active else None}


def movies(params):
    chosen = _catalog_filter(params)
    offset = _offset(params)
    decade = int(chosen["decade"]) if chosen.get("decade") else None
    limits = {"genre": chosen.get("genre"), "year_from": decade, "year_to": decade + 9 if decade is not None else None,
              "min_rating": int(chosen["rating"]) if chosen.get("rating") else None}
    with _library() as lib:
        state = _state(lib)
        total = lib.count_movies(**limits)
        items = lib.movies(order=chosen["order"], limit=PAGE, offset=offset, **limits)
        versions = lib.videos_grouped([m["key"] for m in items if (m.get("video_count") or 1) > 1])
        cards = [_movie_card(meta, state, versions) for meta in items]
    following = _next("movies", chosen, offset, len(cards), total)
    if not offset:
        cards.insert(0, _catalog_filter_card("movies", chosen))
    return {"items": cards, "next": following}


def shows(params):
    chosen = _catalog_filter(params)
    chosen.pop("decade", None)
    offset = _offset(params)
    with _library() as lib:
        items = lib.shows(genre=chosen.get("genre"), min_rating=int(chosen["rating"]) if chosen.get("rating") else None,
                          order=chosen["order"])
        state = _state(lib)
    page = items[offset:offset + PAGE]
    cards = [_show_card(meta, state) for meta in page]
    following = _next("shows", chosen, offset, len(page), len(items))
    if not offset:
        cards.insert(0, _catalog_filter_card("shows", chosen))
    return {"items": cards, "next": following}


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
        label = _episode_code(video) if video["episode"] else video["raw_title"]
        details = " · ".join(part for part in (video.get("lang"), video.get("quality")) if part)
        point = state["resume"].get(video["id"])
        cards.append(_card(label, _target("play", "play", {"vid": video["id"]}), _mark(state, video["id"], details),
                           video.get("thumb") or meta.get("poster"), _percent(point),
                           wide=True, watched=video["id"] in state["watched"],
                           menu=_video_menu(state, {"vid": video["id"]}, point), icon="series"))
    sizes = app_extras.season_sizes(best.values())
    for season, count in sorted(app_extras.season_counts(best.values()).items()):
        target = _target("download", "download_season", {"key": key, "season": season})
        # před zařazením celé řady se aplikace zeptá a ukáže, kolik místa je potřeba a kolik je volno
        target["confirm"] = app_extras.space_question("Stáhnout %d. řadu?" % season, count, *sizes[season])
        cards.append(_card("Stáhnout %d. řadu" % season, target, "%d dílů na disk" % count, icon="download"))
    return {"title": title, "items": cards, "next": None}


def played(params):
    with _library() as lib:
        state = _state(lib)
        videos = lib.history_videos(limit=PAGE, include_search=True)
        metas = lib.get_metas(video["meta_key"] for video in videos)
        cards = [_video_card(video, metas.get(video["meta_key"]), state) for video in videos]
    return {"items": cards, "next": None}


def search(params):
    """Hledání: vlastní katalog, tituly a osoby z TMDB, soubory na Přehraj.to a iVysílání."""
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
    # Všechny zdroje se ptají najednou: čeká se jen na ten nejpomalejší, ne na součet všech.
    with ThreadPoolExecutor(max_workers=4) as pool:
        web = pool.submit(prehrajto_search, {"q": query, "group": True})
        titles = pool.submit(app_discover.search_titles, query)
        people = pool.submit(app_discover.people_rows, query, dict(state))
        television = pool.submit(_ivys_search_rows, query)
        try:
            results = web.result()
        except ApiError as error:
            response["notice"] = str(error)
            results = {"items": [], "next": None}
        # nejdřív tituly (s vyznačením, co na Přehraj.to opravdu je), pak osoby, soubory a iVysílání;
        # když výraz přesně odpovídá jménu herce nebo režiséra, jde napřed on a jeho tituly
        found_titles = app_discover.title_rows(titles.result(), state, results.get("results") or [])
        found_people, exact = people.result()
        rows.extend(found_people + found_titles if exact else found_titles + found_people)
        cards = results["items"]
        if cards:
            cards.append(_card("Filtrovat videa", _target("list", "prehrajto_search",
                                                          {"q": query, "group": True, "listing": True},
                                                          "Videa na Přehraj.to: %s" % query),
                               "Podle jazyka a kvality", icon="settings:search"))
        if results["next"]:
            cards.append(_card("Další výsledky na Přehraj.to",
                               _target("list", "prehrajto_search", results["next"]["params"],
                                       "Videa na Přehraj.to"), icon="search"))
        if cards:
            rows.append({"title": "Videa na Přehraj.to", "items": cards})
        rows.extend(television.result())
    return response


def _remember_search(query):
    def change(settings):
        history = [item for item in settings.get("search_history") or [] if isinstance(item, str) and item != query]
        settings["search_history"] = ([query] + history)[:12]

    _change_settings(change)


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


SEARCH_LANGUAGES = (("", "Vše"), ("dub", "Český nebo slovenský dabing"), ("subs", "S titulky"))
SEARCH_QUALITIES = (("", "Vše"), ("1080", "1080p a lepší"), ("720", "720p a lepší"))


def _search_filter(params):
    """Zvolený filtr výpisu videí z Přehraj.to (a příznaky výpisu, které se nesou mezi stránkami)."""
    chosen = {key: True for key in ("group", "listing") if params.get(key)}
    if params.get("lang") in dict(SEARCH_LANGUAGES) and params["lang"]:
        chosen["lang"] = params["lang"]
    if params.get("quality") in dict(SEARCH_QUALITIES) and params["quality"]:
        chosen["quality"] = params["quality"]
    return chosen


def _search_filter_passes(video, chosen):
    lang = video.get("lang") or ""
    if chosen.get("lang") == "dub" and _language_rank(lang, "dub") != 0:
        return False
    if chosen.get("lang") == "subs" and "titulky" not in lang:
        return False
    if chosen.get("quality"):
        order = provider.QUALITY_ORDER
        token = provider.quality_token(video.get("quality") or "")
        if token not in order or order.index(token) > order.index("FHD" if chosen["quality"] == "1080" else "HD"):
            return False   # kvalita, kterou výsledek neuvádí, filtrem neprojde
    return True


def search_filters(params):
    """Filtr výpisu videí z Přehraj.to: jazyk a kvalita (stejný tvar jako ``catalog_filters``)."""
    query = (params.get("q") or "").strip()
    chosen = _search_filter(params)
    filters = []
    for title, key, choices in (("Jazyk", "lang", SEARCH_LANGUAGES), ("Kvalita", "quality", SEARCH_QUALITIES)):
        listed = [{"title": name, "current": value == chosen.get(key, ""),
                   "params": dict({k: v for k, v in chosen.items() if k != key}, q=query, **({key: value} if value else {}))}
                  for value, name in choices]
        filters.append({"title": title, "value": next(o["title"] for o in listed if o["current"]), "options": listed})
    active = bool(chosen.get("lang") or chosen.get("quality"))
    reset = dict({k: v for k, v in chosen.items() if k in ("group", "listing")}, q=query)
    return {"title": "Filtr", "filters": filters, "reset": reset if active else None}


def prehrajto_search(params):
    query = (params.get("q") or "").strip()
    page = max(1, _offset({"offset": params.get("page")}))
    client = app_discover._search_client(timeout=5)   # společný klient: přihlášení se neověřuje před každým hledáním
    try:
        results, cursor = provider.search_catalog(client, query, page, params.get("cursor", ""))
    except provider.LoginError:
        raise ApiError("Hledání na Přehraj.to se nezdařilo. Zkontrolujte přihlášení v Nastavení.")
    except provider.ProviderError:
        raise ApiError("Hledání na Přehraj.to se nezdařilo. Zkuste to znovu.")
    cards, groups = [], {}
    only = params.get("only") or ""
    chosen = _search_filter(params)
    with _library() as lib:
        state = _state(lib)
        for result in results:
            video = lib.find_video(result["id"])
            meta = lib.get_meta(video["meta_key"]) if video else None
            if not video:
                title = result["title"]
                video = dict(nameparser.parse(title), id=result["id"], raw_title=title,
                             thumb=result.get("thumb", ""), size=result.get("size", ""))
                video["quality"] = _known_quality(result.get("quality"), video["quality"])
                video["duration"] = result.get("duration") or 0
                if not _meets_minimum(video, state["prefs"]):
                    continue   # nižší kvalita, než jakou si uživatel přeje vidět
            if not _search_filter_passes(video, chosen):
                continue
            key = "|".join(str(part) for part in (search_results.normalize(video.get("title") or ""),
                                                  video.get("year") or 0, video.get("season") or 0,
                                                  video.get("episode") or 0))
            if only and key != only:
                continue
            card = _video_card(video, meta, state, web_result=result)
            if params.get("group"):
                groups.setdefault(key, []).append((_version_rank(video, state["prefs"], state["bad"]), len(cards)))
            cards.append(card)
    if params.get("group"):
        # stejný titul nahraný vícekrát: jedna karta s nejlepší verzí podle předvoleb, ostatní v její nabídce
        grouped = []
        for key, members in groups.items():
            best = min(members)[1]
            card = cards[best]
            if len(members) > 1:
                card = dict(card, subtitle=" · ".join(part for part in ("%d verze" % len(members) if len(members) < 5
                                                                        else "%d verzí" % len(members),
                                                                        card["subtitle"]) if part))
                card["menu"] = list(card.get("menu") or []) + [{"title": "Všechny verze (%d)" % len(members), "target": _target(
                    "list", "prehrajto_search", {"q": query, "page": page, "cursor": params.get("cursor", ""),
                                                 "only": key}, card["title"])}]
            grouped.append((min(index for _rank, index in members), card))
        cards = [card for _index, card in sorted(grouped, key=lambda item: item[0])]
    following = {"call": "prehrajto_search", "params": dict(chosen, q=query, page=page + 1, cursor=cursor)} \
        if cursor and not only else None
    if params.get("listing") and page == 1 and not only:
        # samostatný výpis videí: první karta je filtr jazyka a kvality
        summary = " · ".join(title for key, choices in (("lang", SEARCH_LANGUAGES), ("quality", SEARCH_QUALITIES))
                             for value, title in choices if value and value == chosen.get(key))
        cards.insert(0, _card("Filtr", {"type": "filter", "call": "search_filters", "params": dict(chosen, q=query)},
                              summary or "Jazyk a kvalita", icon="settings:search"))
    response = {"items": cards, "next": following}
    if params.get("group"):
        response["results"] = results   # hledání podle nich vyznačí, které tituly na Přehraj.to jsou
    return response


# ---------------------------------------------------------------------------
# Přehrávání
# ---------------------------------------------------------------------------

def _link_get(lib, vid, quality=None):
    """Zapamatovaná adresa souboru, dokud platí. S ``quality`` jen ta, která vznikla se stejnou
    předvolbou kvality – po změně předvolby se varianta souboru vybírá znovu."""
    try:
        cached = json.loads(lib.get_kv(LINK_PREFIX + vid) or "{}")
    except ValueError:
        return None
    if not (isinstance(cached, dict) and cached.get("url")
            and time.time() - cached.get("ts", 0) < provider.LINK_TTL):
        return None
    if quality and cached.get("quality") and cached["quality"] != quality:
        return None
    return cached


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
        choice = str(params.get("choice") or "")
        if choice:
            # verze spuštěná ze stránky titulu: příště bude první a u dílu seriálu nese stav zhlédnutí
            lib.set_kv(CHOICE_PREFIX + choice, vid)
            lib.set_kv(ART_PREFIX + vid, choice)   # podle titulu se pak najde obrázek pro Pokračovat ve sledování
            app_discover.remember_series(lib, choice)
        if video["kind"] == "episode" and video.get("meta_key"):
            lib.set_progress_hidden(video["meta_key"], False)   # seriál se zase sleduje: další díly se nabízejí
        # zvuk a titulky se pamatují pro celý seriál (u filmu pro film)
        tracks = app_extras.tracks(lib, ":".join(choice.split(":")[:2]) if choice.startswith("tv:")
                                   else video["meta_key"])
    title = _video_title(video, meta)
    position = point[0] if point and not params.get("restart") else 0
    entry = _downloads().get(vid)
    if entry and entry.get("state") == "done" and os.path.exists(entry.get("path") or "") \
            and not params.get("online"):
        # stažený soubor: hraje z disku, bez internetu i bez přihlášení
        return {"url": entry["path"], "local": True, "headers": {},
                "subtitles": [name for name in entry.get("subtitles") or [] if os.path.exists(name)],
                "position": position, "title": title, "track": vid, "live": False, "hls": False, "tracks": tracks}
    url, subtitles = _stream_link(vid, bool(params.get("fresh")))
    return {"url": url, "local": False, "headers": {"User-Agent": provider.USER_AGENT},
            "subtitles": list(subtitles.values()), "subtitle_langs": [_subtitle_lang(key) for key in subtitles],
            "position": position, "title": title,
            "track": vid, "live": False, "hls": False, "tracks": tracks}


def _episode_code(video, separator="×"):
    """Označení dílu „1×05“; soubor s víc díly najednou „1×01–02“."""
    code = "%d%s%02d" % (video["season"], separator, video["episode"])
    last = video.get("episode_end") or 0
    return code + ("–%02d" % last if last > video["episode"] else "")


def _last_episode(video):
    """Poslední díl, který soubor obsahuje (u dvojdílu druhý)."""
    return max(video["episode"], video.get("episode_end") or 0)


def _video_title(video, meta):
    title = _display_title(meta or {}) or video["title"]
    if video["kind"] == "episode" and video["episode"]:
        title = "%s %s" % (title, _episode_code(video))
    return title


def _subtitle_lang(label):
    """Kód jazyka titulků pro přehrávač z označení na Přehraj.to (CZ, EN, SK…)."""
    code = str(label or "").strip().lower()[:3]
    return {"cz": "cs", "cze": "cs", "ces": "cs", "en": "en", "eng": "en", "sk": "sk", "slo": "sk",
            "slk": "sk"}.get(code, code if code.isalpha() else "")


def _stream_link(vid, fresh=False):
    """Adresa nejlepší verze souboru a titulky; zapamatovaná adresa se použije, dokud platí."""
    prefs = _prefs()
    if not fresh:
        with _library() as lib:
            cached = _link_get(lib, vid, prefs["quality"])
        if cached:
            return cached["url"], cached.get("subtitles") or {}
    client = _client()
    try:
        streams, _message = client.get_streams(vid)
        if not streams:
            raise provider.VideoUnavailableError("Video nemá žádný přehratelný soubor")
        _real_from_streams(vid, streams)
        stream = _pick_stream(streams, prefs)
        url = client.resolve(stream["url"])
        subtitles = stream.get("subtitles") or {}
    except provider.LoginError:
        raise Blocked("Přihlášení k Přehraj.to se nezdařilo. Zkontrolujte e-mail a heslo v Nastavení.")
    except provider.VideoUnavailableError:
        raise ApiError("Video už na Přehraj.to není dostupné.")
    except (provider.TemporaryError, provider.RateLimited) as error:
        raise Blocked("Přehraj.to: %s" % error)
    except provider.ProviderError as error:
        raise ApiError("Přehraj.to: %s" % error)
    with _library() as lib:
        lib.set_kv(LINK_PREFIX + vid, json.dumps({"url": url, "subtitles": subtitles, "ts": int(time.time()),
                                                  "quality": prefs["quality"], "stream": stream.get("quality")}))
    return url, subtitles


def video_size(params):
    """Přehrávač hlásí skutečné rozměry obrazu: podle nich aplikace ví, v jaké kvalitě soubor opravdu je.

    Počítá se jen původní soubor (ne překódovaná verze, ta je vidět na stránce videa) a stažený soubor."""
    vid = str(params.get("track") or "")
    lines = _picture_lines(int(params.get("width") or 0), int(params.get("height") or 0))
    if not vid or vid.startswith("ivys:") or lines <= 0:
        return {}
    if params.get("local"):
        _real_note(vid, local=lines)
        return {"lines": lines}
    with _library() as lib:
        cached = _link_get(lib, vid)
    if cached and cached.get("stream") == "ORIG":
        _real_note(vid, played=lines)
    return {"lines": lines}


PLAYBACK_HOLD = 120   # s – tak dlouho po posledním uložení pozice přehrávání se na pozadí nic neověřuje


def progress_save(params):
    """Uloží pozici přehrávání (aplikace volá průběžně a při zastavení)."""
    track = params.get("track") or ""
    position = float(params.get("position") or 0)
    total = float(params.get("total") or 0)
    if not params.get("ended"):
        # přehrává se: procházení seznamu dostupných titulů na pozadí počká, ať nebere připojení
        app_discover._VERIFY["hold"] = max(app_discover._VERIFY.get("hold", 0), time.time() + PLAYBACK_HOLD)
    if not track or total <= 0:
        return {"saved": False}
    if params.get("ended") and total - position <= max(15, total * 0.08):
        position = total  # doběhlo do konce → zhlédnuto
    with _library() as lib:
        if track.startswith(IVYS):
            lib.ivys_record_play(track[len(IVYS):], position, total)
        else:
            lib.record_play(track, position, total, started=bool(params.get("started")))
    if position >= total and not track.startswith(IVYS) and _settings().get("delete_watched"):
        entry = _downloads().get(track)
        if entry and entry.get("state") == "done":
            download_remove({"vid": track})   # dokoukaný stažený soubor uvolní místo na disku
    return {"saved": True}


def progress_remove(params):
    """Odebere video z Rozkoukaných (zapomene, kde se skončilo); ``vid`` z Přehraj.to, ``idec`` z iVysílání."""
    with _library() as lib:
        if params.get("idec"):
            lib.ivys_remove_history(str(params["idec"]))
        elif params.get("vid"):
            lib.remove_history(str(params["vid"]))
        else:
            raise ApiError("Chybí video.")
    return {"message": "", "toast": "Odebráno z Rozkoukaných."}


def watched_set(params):
    """Ruční označení videa jako zhlédnutého (zmizí z Rozkoukaných, nabídne se další díl), nebo zpět."""
    vid = str(params.get("vid") or "")
    if not vid:
        raise ApiError("Chybí video.")
    watched = bool(params.get("watched"))
    with _library() as lib:
        lib.set_watched([vid], watched)
    return {"message": "", "toast": "Označeno jako zhlédnuté." if watched else "Označeno jako nezhlédnuté."}


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
            "min_quality": prefs["minimum"],
            "delete_watched": bool(settings.get("delete_watched")),
            "languages": [{"id": key, "title": title} for key, title in LANGUAGE_CHOICES],
            "qualities": [{"id": key, "title": title} for key, title in QUALITY_CHOICES]}


def prefs_choices(params):
    """Předvolby přehrávání z Přehraj.to jako nabídka: jazyk a kvalita (stejný tvar jako filtry výpisů).

    ``apply`` je akce, které aplikace pošle parametry zvolené hodnoty.
    """
    prefs = _prefs()
    groups = []
    for title, key, choices, current in (("Jazyk", "prefer_language", LANGUAGE_CHOICES, prefs["language"]),
                                         ("Kvalita", "prefer_quality", QUALITY_CHOICES, prefs["quality"]),
                                         ("Nejnižší kvalita", "min_quality", MINIMUM_CHOICES, prefs["minimum"])):
        groups.append({"title": title, "value": dict(choices)[current],
                       "options": [{"title": name, "current": value == current, "params": {key: value}}
                                   for value, name in choices]})
    return {"title": "Čemu dát přednost na Přehraj.to", "filters": groups, "reset": None, "apply": "settings_set"}


def settings_set(params):
    account = []

    def change(settings):
        before = (settings.get("username"), settings.get("password"))
        for key in ("username", "password", "tmdb_key", "download_dir", "prefer_language", "prefer_quality",
                    "min_quality"):
            if key in params and params[key] is not None:
                settings[key] = str(params[key]).strip()
        if params.get("min_quality") is not None:
            settings["min_quality_chosen"] = True
        for key in ("delete_watched", "onboarded"):   # přepínače
            if key in params and params[key] is not None:
                settings[key] = bool(params[key])
        account.append(before != (settings.get("username"), settings.get("password")))
        account.append(wanted != tuple(settings.get(key) for key in watched))
        account.append(lowest != _prefs(settings)["minimum"])

    watched = ("prefer_language", "prefer_quality", "min_quality")
    wanted = tuple(_settings().get(key) for key in watched)
    lowest = _prefs()["minimum"]
    need = _need_lines(_prefs())
    _change_settings(change)
    if account[1]:
        # Jiná předvolba jazyka nebo kvality: verze, které si uživatel dřív u titulů vybral ručně,
        # by jinak zůstaly první a nabídka by se nezměnila. Nově platí pořadí podle předvoleb.
        # U dílů seriálu volba zůstává: nese stav zhlédnutí dílu (který soubor se u něj pustil),
        # a smazat ji by znamenalo ztratit rozkoukané řady.
        with _library() as lib, lib.conn:
            lib.conn.execute("DELETE FROM settings_kv WHERE k LIKE ? AND k NOT LIKE ?",
                             (CHOICE_PREFIX + "%", CHOICE_PREFIX + "tv:%"))
    if account[2]:
        # Jiná nejnižší kvalita: co je „k mání“, se počítá jinak. Seznam ověřených titulů se kvůli tomu
        # nemaže – tituly se přeověří na pozadí a do té doby platí, co se o nich ví.
        app_discover.minimum_changed(need, _need_lines(_prefs()))
        app_index.note("změna nejnižší kvality: %s → %s (seznam zůstává, tituly se ověří znovu)" % (
            MINIMUM_LABEL.get(lowest, lowest) or "bez omezení", MINIMUM_LABEL.get(_prefs()["minimum"]) or "bez omezení"))
        app_index.restart()
    if account[0]:
        # Jiný účet nebo heslo: uložené přihlášení patřilo tomu předchozímu. Kdyby zůstalo, aplikace
        # by dál pracovala s původním účtem a i špatné heslo by vypadalo jako správné.
        try:
            os.remove(_path("cookies.lwp"))
        except OSError:
            pass
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
        _change_settings(lambda settings: settings.update(last_sync=int(time.time())))
        # Ruční synchronizace doplní hodnocení a plakáty všemu, co je nemá – i titulům, u kterých se to
        # dřív nepovedlo (samo se to zkouší znovu až po čase).
        enriched = _enrich(("new", "notfound", "error", "blocked"))
        return {"message": "Videí: %d, nových: %d, odebraných: %d. %s" % (
            stats["total"], stats["new"], stats["removed"], enriched)}
    return _exclusive(work)


QUICK_SYNC_EVERY = 15 * 60   # s – častěji se účet po otevření aplikace nekontroluje


AUTO_ENRICH_BATCH = 40                # tolik titulů nejvýš doplní jedno samočinné kolo (ať netrvá dlouho)
ENRICH_RETRY_AFTER = 6 * 3600         # titul, u kterého doplnění selhalo (výpadek, ČSFD odmítlo), se zkusí znovu po…
ENRICH_NOTFOUND_AFTER = 7 * 24 * 3600   # …a nenalezený po týdnu (v databázích mohl mezitím přibýt)


def quick_sync(params):
    """Po otevření aplikace: zkontroluje účet (nová videa) a doplní chybějící hodnocení a plakáty.

    Obojí běží potichu na pozadí. ``refresh`` ve výsledku říká, že se něco doplnilo a obrazovka se
    má načíst znovu."""
    result = _account_check(params)
    if _auto_enrich():
        result["refresh"] = True
    try:
        app_index.start()   # seznam dostupných titulů se plní na pozadí, dokud aplikace běží
    except (ApiError, TmdbError):
        pass
    try:
        app_health.snapshot()   # jednou denně ověřená kopie katalogu pro případ poškození
    except Exception as error:  # kopie je jen pojistka: nesmí nic zastavit
        _remember_error("kopie katalogu", error)
    return result


def _auto_enrich():
    """Doplní hodnocení a plakáty titulům, které je nemají – samo, bez ruční synchronizace.

    Nové tituly hned; ty, u kterých to dřív nevyšlo, znovu až po čase (aby se při každém otevření
    aplikace neptala na totéž). Vrací, kolika titulům se něco doplnilo; když běží jiná operace, nedělá nic."""
    with _library() as lib:
        waiting = (lib.meta_to_fetch(("new",))
                   + lib.meta_to_fetch(("error", "blocked"), retry_after=ENRICH_RETRY_AFTER)
                   + lib.meta_to_fetch(("notfound",), retry_after=ENRICH_NOTFOUND_AFTER))[:AUTO_ENRICH_BATCH]
    if not waiting or not _LOCK.acquire(False):
        return 0
    _STATE["busy"] = True
    try:
        done = {}
        _enrich(metas=waiting, result=done)
        return done.get("ok", 0)
    except (ApiError, TmdbError, provider.ProviderError, OSError) as error:
        _remember_error("doplnění hodnocení a plakátů", error)   # jen do Diagnostiky
        return 0
    finally:
        _STATE["busy"] = False
        _STATE["progress"] = ""
        _LOCK.release()


def _account_check(params):
    """Rychlá kontrola účtu po otevření aplikace: přidá nová videa a doplní k nim metadata.

    Výpis účtu se čte jen po první stránku, na které není nic nového, takže se podle něj nic
    neodebírá – to dělá jen úplná synchronizace. Běží potichu: bez přihlašovacích údajů, s prázdným
    katalogem (první načtení je úplné), při výpadku nebo když běží jiná operace jen vrátí
    ``{"new": 0, "skipped": důvod}``.
    """
    settings, now = _settings(), time.time()
    if not (settings.get("username") and settings.get("password")):
        return {"new": 0, "skipped": "account"}
    latest = max(float(_STATE.get("quick_sync") or 0), float(settings.get("last_sync") or 0))
    if 0 <= now - latest < QUICK_SYNC_EVERY and not params.get("force"):
        return {"new": 0, "skipped": "recent"}
    if not _LOCK.acquire(False):
        return {"new": 0, "skipped": "busy"}
    _STATE["busy"] = True
    _STATE["quick_sync"] = now          # i po chybě: špatné heslo se nemá zkoušet při každém otevření
    try:
        with _library() as lib:
            known = lib.account_video_keys()
            if not known:
                return {"new": 0, "skipped": "empty"}
            _STATE["progress"] = "Kontroluji nová videa na účtu"
            stats = catalog.sync_account(_client(), lib, remove_missing=False, known=known)
        new = 0 if stats.get("superseded") else int(stats.get("new") or 0)
        if new:
            _enrich()
        return {"new": new, "toast": "Nová videa z účtu: %d" % new if new else ""}
    except (provider.ProviderError, ApiError) as error:
        _remember_error("quick_sync", error)   # jen do Diagnostiky; uživatele po otevření neruší
        return {"new": 0, "skipped": "error"}
    finally:
        _STATE["busy"] = False
        _STATE["progress"] = ""
        _LOCK.release()


# ---------------------------------------------------------------------------
# Ruční oprava přiřazení
# ---------------------------------------------------------------------------

def _canonical_name(video):
    """Jak by se video jmenovalo zapsané „správně“ – předloha pro ruční přepis."""
    if video["kind"] == "episode":
        return "%s S%02dE%02d" % (video["title"], video["season"], video["episode"])
    return "%s %d" % (video["title"], video["year"]) if video["year"] else video["title"]


def repair(params):
    """Oprava špatně rozpoznaného titulu: výběr správného filmu nebo seriálu a oprava řady a dílu."""
    vid = str(params.get("vid") or "")
    with _library() as lib:
        video = lib.find_video(vid) if vid else None
        key = params.get("key") or (video or {}).get("meta_key") or ""
        meta = lib.get_meta(key) if key else None
    if not meta:
        raise ApiError("Tento titul už v katalogu není.")
    series = meta["kind"] == "show"
    query = (params.get("q") or meta.get("search_title") or "").strip()
    ref = {"key": key, "vid": vid}
    cards = [_card("Hledat pod jiným názvem",
                   _target("input", "repair_query", dict(ref, value=query, hint="Název filmu nebo seriálu")),
                   "Teď hledám: %s" % query, icon="search")]
    tmdb = _tmdb()
    if tmdb.enabled:
        try:
            candidates = tmdb.search(query, series) if query else []
        except TmdbError as error:
            raise ApiError(str(error))
        for item in candidates:
            name = "%s (%s)" % (item["title"], item["year"] or "?")
            other = item["orig_title"] if item.get("orig_title") and item["orig_title"] != item["title"] else ""
            current = str(meta.get("tmdb_id") or "") == str(item["id"])
            cards.append(_card(name, _target("command", "repair_apply", dict(ref, id=item["id"])),
                               " · ".join(part for part in ("teď přiřazeno" if current else "", other) if part),
                               icon="series" if series else "movies"))
        if not candidates:
            cards.append(_card("Nic nenalezeno", _target("refresh"), "Zkuste jiný název", icon="search"))
    else:
        cards.append(_card("Plakáty a názvy potřebují klíč TMDB", _target("settings"), "Otevřít Nastavení",
                           icon="settings"))
    if video:
        if video["kind"] == "episode":
            cards.append(_card("Změnit číslo dílu", _target("input", "repair_video", dict(
                ref, field="episode", value=str(video["episode"]), hint="Číslo dílu")), "Teď: %s" % _episode_code(video),
                icon="series"))
            cards.append(_card("Změnit řadu", _target("input", "repair_video", dict(
                ref, field="season", value=str(video["season"]), hint="Číslo řady")), "Teď: %d. řada" % video["season"],
                icon="series"))
            cards.append(_card("Je to film, ne díl", _target("command", "repair_video", dict(ref, field="movie")),
                               icon="movies"))
        else:
            cards.append(_card("Je to díl seriálu", _target("input", "repair_video", dict(
                ref, field="episode_of", value="", hint="Řada a díl, např. 1x05")), "Zadejte řadu a díl", icon="series"))
        cards.append(_card("Přepsat název ručně", _target("input", "repair_video", dict(
            ref, field="name", value=_canonical_name(video), hint="Např. Pelíšky 1999 nebo Ulice 1x4521")),
            "Soubor: %s" % video["raw_title"], icon="settings"))
        if video.get("locked"):
            cards.append(_card("Vrátit automatické rozpoznání", _target("command", "repair_video", dict(ref, field="auto")),
                               "Zruší ruční úpravu tohoto souboru", icon="sync"))
    return {"title": "Opravit přiřazení", "items": cards, "next": None,
            "empty": "K tomuto titulu není co opravit."}


def repair_query(params):
    """Jiný název pro hledání správného titulu: výpis kandidátů se otevře znovu s novým dotazem."""
    text = str(params.get("text") or "").strip()
    if not text:
        raise ApiError("Název nesmí být prázdný.")
    target = _target("list", "repair", {"key": params.get("key") or "", "vid": params.get("vid") or "", "q": text},
                     "Opravit přiřazení")
    return {"message": "", "open": target, "close": True}


def repair_apply(params):
    """Přiřadí titulu z katalogu vybraný film nebo seriál z TMDB (název, plakát, hodnocení)."""
    key = params.get("key") or ""
    with _library() as lib:
        meta = lib.get_meta(key)
    if not meta:
        raise ApiError("Tento titul už v katalogu není.")
    info = _tmdb().details(int(params.get("id") or 0), meta["kind"] == "show")
    with _library() as lib:
        catalog.apply_details(lib, key, info, "tmdb", lock_videos=True)
    return {"message": "", "toast": "Přiřazeno: %s" % (info.get("title") or meta["search_title"]), "close": True}


_EPISODE_OF_RE = re.compile(r"^\s*(?:s\s*)?(\d{1,2})\s*(?:x|×|e|\s)\s*(\d{1,4})\s*$", re.I)


def _whole(text, low, high, what):
    try:
        value = int(str(text).strip())
    except ValueError:
        raise ApiError("%s musí být číslo." % what)
    if not low <= value <= high:
        raise ApiError("%s musí být od %d do %d." % (what, low, high))
    return value


def repair_video(params):
    """Oprava jednoho souboru: číslo dílu, řada, film místo dílu a naopak, nebo celý název ručně.

    Ruční úprava video „zamkne“ – synchronizace ani přepočet katalogu ji už nepřepíše."""
    vid, field, text = str(params.get("vid") or ""), params.get("field") or "", str(params.get("text") or "").strip()
    with _library() as lib:
        video = lib.find_video(vid)
        if not video:
            raise ApiError("Tento soubor už v katalogu není.")
        vid = video["id"]
        kind, title, year = video["kind"], video["title"], video["year"]
        season, episode = video["season"], video["episode"]
        if field == "auto":
            key = lib.unlock_video(vid)
        else:
            if field == "episode":
                kind, episode = "episode", _whole(text, 1, 9999, "Číslo dílu")
            elif field == "season":
                kind, season = "episode", _whole(text, 0, 99, "Číslo řady")
            elif field == "movie":
                kind, season, episode = "movie", 0, 0
                year = nameparser.parse(video["raw_title"])["year"]
            elif field == "episode_of":
                found = _EPISODE_OF_RE.match(text)
                if found:
                    season, episode = int(found.group(1)), int(found.group(2))
                else:
                    season, episode = 1, _whole(text, 1, 9999, "Číslo dílu")
                kind, year = "episode", 0
                # „Ulice 4521“ jako díl 4521: číslo do názvu seriálu nepatří
                base = nameparser.numbered(title)
                if base and int(base[1]) == episode:
                    title = base[0]
            elif field == "name":
                parsed = nameparser.parse(text)
                if not parsed["title"]:
                    raise ApiError("Název nesmí být prázdný.")
                kind, title, year = parsed["kind"], parsed["title"], parsed["year"]
                season, episode = parsed["season"], parsed["episode"]
            else:
                raise ApiError("Neznámá oprava.")
            if kind == "episode" and episode <= 0:
                raise ApiError("U dílu seriálu musí být číslo dílu.")
            key = lib.edit_video(vid, kind, title, year, season, episode)
        fixed = lib.get_video(vid)
        label = _video_title(fixed, lib.get_meta(key))
        fresh = (lib.get_meta(key) or {}).get("status") == "new"
    if fresh:
        try:
            _enrich()        # nový titul si hned dotáhne plakát a hodnocení
        except (ApiError, TmdbError):
            pass
    return {"message": "", "toast": "Opraveno: %s" % label, "close": True}


def _enrich(statuses=("new",), metas=None, result=None):
    tmdb = _tmdb()
    with _library() as lib:
        metas = lib.meta_to_fetch(statuses) if metas is None else metas
        if not metas:
            return "Metadata jsou doplněná."

        def on_progress(index, total, title):
            _STATE["progress"] = "Doplňuji metadata %d/%d: %s" % (index + 1, total, title)
            return False

        stats = catalog.enrich(lib, metas, use_csfd=True, tmdb=tmdb, progress=on_progress)
    if result is not None:
        result.update(stats)
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


_IVYS_DOWN = {"until": 0}   # iVysílání neodpovídalo: hlavní obrazovka se na ně chvíli neptá
IVYS_DOWN_FOR = 90


def _ivys_quick(key, ttl, load):
    """Data iVysílání pro hlavní obrazovku: z mezipaměti, jinak krátký dotaz; po výpadku se půldruhé
    minuty neptá vůbec, aby se hlavní obrazovka nezdržovala při každém návratu."""
    if _IVYS_DOWN["until"] > time.time():
        with _library() as lib:
            value = lib.cache_get("app:" + key)
        if value is None:
            raise ivysilani.IvysilaniError("iVysílání teď neodpovídá")
        return value
    try:
        return _cached(key, ttl, load)
    except ivysilani.IvysilaniError:
        _IVYS_DOWN["until"] = time.time() + IVYS_DOWN_FOR
        raise


def ivys_live(params):
    try:
        # "quick": řada na hlavní obrazovce – program se nesmí načítat desítky sekund
        if params.get("quick"):
            channels = _ivys_quick("ivys:live", TTL["live"], lambda: _ivys(timeout=4).live_now())
        else:
            channels = _cached("ivys:live", TTL["live"], lambda: _ivys().live_now())
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
        _ivys_remember_kind(lib, show_id, programme.get("type"))
        state = _ivys_state(lib)
        # zkratka na začátku výpisu: rozkoukaný díl, nebo první nezhlédnutý po naposledy dokoukaném
        resume = [] if _offset(params) else [card for card in [_ivys_continue_card(lib, programme, state)] if card]
    favorite = show_id in state["favorites"]
    toggle = _card("Odebrat z oblíbených" if favorite else "Přidat do oblíbených",
                   _target("command", "ivys_favorite", {"id": show_id, "remove": favorite}), icon="favorites")
    if programme["seasons"] and not season:
        cards = [_card(item["title"] or programme["title"],
                       _target("list", "ivys_show", {"id": show_id, "season": item["id"]},
                               "%s – %s" % (programme["title"], item["title"])), wide=True, icon="series")
                 for item in programme["seasons"]]
        cards.append(_card("Všechny díly", _target("list", "ivys_show", {"id": show_id, "season": "all"},
                                                   programme["title"]), "%d dílů" % programme["episode_count"], wide=True,
                           icon="all"))
        return {"title": programme["title"], "items": resume + cards + [toggle], "next": None}
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
    return {"title": programme["title"], "items": resume + cards, "next": following}


IVYS_NEXT_PAGES = 5   # tolik stránek výpisu dílů se v každém směru nejvýš projde při hledání dalšího dílu


def _ivys_episodes(programme, offset=0, order=None, season_id=None):
    """Stránka dílů pořadu (``{"total", "items"}``) z mezipaměti; ``order`` jinak podle pořadu."""
    default = programme["default_sort"]
    order = order or default
    key = "ivys:episodes:%s:%s:%d" % (programme["programme_id"], season_id or "", offset)
    if order != default:
        key += ":" + order
    return _cached(key, TTL["episodes"], lambda: dict(zip(("total", "items"), _ivys().episodes(
        programme["programme_id"], season_id, offset, order))))


def _ivys_following(programme, idec):
    """Díl, který v pořadu následuje po ``idec`` (časově další), nebo None.

    Výpis dílů jde od nejnovějšího, nebo od nejstaršího: následující díl je v prvním případě ten
    před hledaným, v druhém ten za ním. Nejdřív se hledá v pořadí, jaké má pořad sám (to už bývá
    v mezipaměti z výpisu), pak v opačném; v každém nejvýš ``IVYS_NEXT_PAGES`` stránek. U hodně
    dlouhých pořadů se tak díl uprostřed nemusí najít a nenabídne se nic.
    """
    default = programme["default_sort"]
    for order in (default, "oldest" if default == "newest" else "newest"):
        previous, offset = None, 0
        for _page in range(IVYS_NEXT_PAGES):
            page = _ivys_episodes(programme, offset, order)
            items = page["items"]
            for index, item in enumerate(items):
                if item["idec"] != idec:
                    previous = item
                    continue
                if order == "newest":
                    return previous                      # nejnovější díl už žádný další nemá
                if index + 1 < len(items):
                    return items[index + 1]
                later = _ivys_episodes(programme, offset + len(items), order)["items"] \
                    if offset + len(items) < page["total"] else []
                return later[0] if later else None
            offset += len(items)
            if not items or offset >= page["total"]:
                return None                              # prošel se celý výpis: díl v něm není
    return None


def _ivys_programme(show_id):
    return _cached("ivys:show:" + show_id, TTL["show"], lambda: _ivys().show(show_id))


def ivys_next(idec):
    """Návrh pro přehrávač po dokoukání dílu z iVysílání: další díl téhož pořadu, nebo None.

    Sám se nespouští – přehrávač nabídne tlačítko. Když se pořad nebo jeho díly nepodaří zjistit,
    nenabídne se nic (návrh je jen pohodlí)."""
    with _library() as lib:
        entry = lib.ivys_entry(str(idec)) or {}
    show_id = str(entry.get("show_id") or "")
    if not show_id:
        return None
    try:
        programme = _ivys_programme(show_id)
        if programme.get("type") == "movie" or programme["episode_count"] < 2:
            return None
        following = _ivys_following(programme, str(idec))
    except ivysilani.IvysilaniError:
        return None
    if not following:
        return None
    parts = [programme["title"]] + ([following["title"]] if following["title"] != programme["title"] else [])
    label = " · ".join(part for part in parts if part)
    return {"title": "Další díl", "subtitle": label, "auto": False,
            "target": _target("play", "ivys_play", {"idec": following["idec"]})}


def _ivys_continue(lib, programme, state):
    """Kterým dílem pořadu pokračovat: ``(díl, poznámka)``, nebo None, když se z něj nic nepřehrávalo.

    Rozkoukaný díl má přednost; jinak první nezhlédnutý díl po tom naposledy dokoukaném."""
    for row in lib.ivys_show_history(programme["id"]):
        if row["idec"] in state["resume"]:
            return {"idec": row["idec"], "title": row.get("title") or programme["title"],
                    "image": row.get("image") or ""}, "Rozkoukaný díl"
        if row["idec"] in state["watched"]:
            following = _ivys_following(programme, row["idec"])
            for _step in range(5):                       # díly zhlédnuté mimo pořadí se přeskočí
                if not following or following["idec"] not in state["watched"]:
                    break
                following = _ivys_following(programme, following["idec"])
            if following and following["idec"] not in state["watched"]:
                return following, "První nezhlédnutý díl"
            return None
    return None


def _ivys_continue_card(lib, programme, state):
    """Karta „Pokračovat“ na začátku výpisu pořadu; když není čím pokračovat nebo to nejde zjistit, None."""
    try:
        found = _ivys_continue(lib, programme, state)
    except (ivysilani.IvysilaniError, ApiError):
        return None
    if not found:
        return None
    item, note = found
    title = item.get("title") or programme["title"]
    return _card("Pokračovat: %s" % title, _target("play", "ivys_play", {"idec": item["idec"]}), note,
                 item.get("image") or programme["image"], _percent(state["resume"].get(item["idec"])), wide=True)


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
        return {"message": "Odebráno z oblíbených."}
    programme = _ivys_call(lambda: _cached("ivys:show:" + show_id, TTL["show"], lambda: _ivys().show(show_id)))
    with _library() as lib:
        lib.ivys_favorite_add(show_id, programme["title"], programme["image"], programme["poster"],
                              programme["episode_count"])
        _ivys_remember_kind(lib, show_id, programme.get("type"))
    return {"message": "Přidáno do oblíbených. Najdete je v sekci Oblíbené i v iVysílání."}


def ivys_play(params):
    client = _ivys()
    if params.get("channel"):
        stream = _ivys_call(lambda: client.live(str(params["channel"]), timeshift=bool(params.get("timeshift"))))
        return {"url": stream["url"], "headers": {}, "subtitles": [], "position": 0, "title": stream["title"],
                "track": "", "live": True, "hls": True}
    idec = str(params.get("idec") or "")
    if not idec and params.get("following"):
        # zkratka z oblíbeného pořadu: rozkoukaný díl, nebo první nezhlédnutý po naposledy dokoukaném
        programme = _ivys_call(lambda: _ivys_programme(str(params.get("show") or "")))
        with _library() as lib:
            found = _ivys_call(lambda: _ivys_continue(lib, programme, _ivys_state(lib)))
        if not found:
            raise ApiError("Z tohoto pořadu jste zatím nic nedokoukali – otevřete ho a vyberte díl.")
        idec = found[0]["idec"]
    idec = idec or _ivys_call(lambda: client.show(str(params.get("show") or "")))["idec"]
    stream = _ivys_stream(client, idec, inspect=bool(params.get("inspect")))
    with _library() as lib:
        if params.get("show") and not params.get("idec") and not params.get("following"):
            _ivys_remember_kind(lib, stream["show_id"], "movie")   # přehrání celého pořadu jedním videem: film
        lib.ivys_touch(idec, stream["show_id"], stream["episode_title"] or stream["title"], stream["show_title"],
                       stream["image"])
        point = lib.ivys_states()[0].get(idec)
        tracks = app_extras.tracks(lib, IVYS + stream["show_id"] if stream["show_id"] else "")
    title = " · ".join(part for part in (stream["show_title"], stream["episode_title"]) if part) or stream["title"]
    result = {"url": stream["url"], "headers": {}, "subtitles": stream["subtitles"],
              "position": point[0] if point and not params.get("restart") else 0, "title": title,
              "track": IVYS + idec, "live": False, "hls": not stream.get("dash"), "dash": bool(stream.get("dash")),
              "drm": stream.get("drm"), "tracks": tracks,
              # chráněný pořad: když se nepřehraje, přehrávač ukáže, co rozhraní vrátilo (jen názvy polí)
              "diagnostic": (stream.get("shape") or "") if stream.get("dash") else ""}
    if stream.get("dash") and not (stream.get("drm") or {}).get("license"):
        # Chráněný pořad bez adresy licence aplikace přehrát neumí: předá ho oficiální aplikaci ČT,
        # je-li v zařízení (rozhodne přehrávač; jinak zkusí přehrát sám a ukáže, proč to nejde).
        result["external"] = _ivys_official(stream["show_id"], idec, title, stream["show_title"] or stream["title"])
    return result


def _ivys_official(show_id, idec, title, query=""):
    """Předání pořadu oficiální aplikaci iVysílání: její balíčky, veřejné adresy pořadu na webu ČT
    a název pořadu pro hledání.

    Adresy jsou běžné stránky (``/porady/<pořad>/<díl>/`` a ``/porady/<pořad>/``; web ČT si je sám
    doplní o název). Aplikace ČT je otevře, pokud se k takovým odkazům hlásí; jinak ji přehrávač
    požádá, ať pořad podle názvu najde (``query``), a když ani to neumí, aspoň ji spustí."""
    show = str(show_id or "") if str(show_id or "").isdigit() else ""
    urls = []
    if show:
        if str(idec or "").isdigit():
            urls.append(IVYS_WEB % ("%s/%s" % (show, idec)))
        urls.append(IVYS_WEB % show)
    return {"name": "iVysílání ČT", "packages": list(IVYS_APPS), "title": title, "query": query or title,
            "url": urls[0] if urls else "", "urls": urls}


def _ivys_stream(client, idec, inspect=False):
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
        if inspect and not stream.get("drm"):
            # Přehrávač adresu licence nenašel: podívat se do manifestu samy (umí víc zápisů než
            # přehrávač) a ke zprávě přidat, co manifest o ochraně říká.
            try:
                found, summary = ivysilani.manifest_protection(client.manifest(stream["url"]))
            except ivysilani.IvysilaniError as error:
                found, summary = None, str(error)
            shape = "%s; %s" % (summary, stream.get("shape") or "")
            try:
                stream = client.vod(idec, drm=True)   # čerstvá adresa pro přehrávač
            except ivysilani.IvysilaniError:
                pass
            stream["drm"], stream["shape"] = stream.get("drm") or found, shape
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
    """Evidence stahování. Když je soubor poškozený (výpadek proudu při zápisu), použije se záloha
    z poslední změny stavu – jinak by stažené soubory na disku zůstaly bez záznamu."""
    with _DOWNLOADS_LOCK:
        for name in (DOWNLOADS, DOWNLOADS + ".bak"):
            try:
                with open(_path(name), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return {vid: entry for vid, entry in data.items() if isinstance(entry, dict)}
        return {}


def _save_downloads(items, durable=True):
    """Uloží evidenci. ``durable`` (změna stavu, ne jen průběh): data se vynutí na disk a uloží se
    i záloha, aby evidence přežila výpadek proudu."""
    with _DOWNLOADS_LOCK:
        temporary = _path(DOWNLOADS + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
            if durable:
                f.flush()
                os.fsync(f.fileno())
        os.replace(temporary, _path(DOWNLOADS))
        if durable:
            backup = _path(DOWNLOADS + ".bak.tmp")
            with open(backup, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(backup, _path(DOWNLOADS + ".bak"))


def downloads_pending(params):
    """Čeká ve frontě něco ke stažení? (Aplikace se ptá, když se vrátí připojení nebo disk.)"""
    return {"pending": any(entry.get("state") in ("queued", "downloading") or entry.get("move_to")
                           for entry in _downloads().values())}


def downloads_cleanup(params):
    """Úklid složky stahování při startu služby: rozpracované soubory (``.part``), ke kterým už není
    záznam (zrušené stahování, ztracená evidence), jen zabírají místo. Hotových souborů se nedotýká."""
    directory = params.get("dir") or ""
    removed = 0
    if directory and os.path.isdir(directory):
        wanted = {(entry.get("path") or "") + ".part" for entry in _downloads().values()}
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if name.endswith(".part") and path not in wanted and os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return {"removed": removed}


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
        status = "Staženo · %s" % _size_text(total) if total else "Staženo"
        return "Přesouvá se · " + status if entry.get("move_to") else status
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


def _download_poster(lib, vid, video):
    """Plakát ke staženému videu: z metadat katalogu, podle titulu z Objevuj, nebo dohledaný podle názvu.

    Vrací adresu, prázdný text (plakát není), nebo None, když se to teď nepodařilo zjistit.
    """
    meta = (lib.get_meta(video["meta_key"]) or {}) if video else {}
    if meta.get("poster"):
        return meta["poster"]
    client = _tmdb()
    if not client.enabled:
        return None
    try:
        # video přehrané ze stránky titulu si nese, ke kterému titulu patří
        row = lib.conn.execute("SELECT k FROM settings_kv WHERE k LIKE 'choice:%' AND v=? LIMIT 1", (vid,)).fetchone()
        parts = row[0].split(":") if row else []
        if len(parts) >= 3 and parts[2].isdigit():
            return client.details(int(parts[2]), want_series=parts[1] == "tv").get("poster") or ""
        if video:
            found = client.find(video["title"], video["year"] or 0, want_series=video["kind"] == "episode")
            return (found or {}).get("poster") or ""
    except TmdbError:
        return None
    return ""


def _download_cards(lib, state, limit=None, episodes=None):
    """Karty stažených videí s plakáty; ``episodes`` True/False vybere jen díly seriálů / jen filmy."""
    cards, posters = [], {}
    items = sorted(state["downloads"].values(), key=lambda entry: -(entry.get("added") or 0))
    videos = {entry.get("vid"): lib.find_video(entry.get("vid") or "") for entry in items}
    if episodes is not None:
        items = [entry for entry in items if ((videos.get(entry.get("vid")) or {}).get("kind") == "episode") == episodes]
    for entry in items[:limit] if limit else items:
        vid = entry.get("vid") or ""
        total = entry.get("total") or 0
        percent = 100 * (entry.get("done") or 0) // total if total and entry.get("state") != "done" else 0
        point = state["resume"].get(vid)
        poster = entry.get("poster")
        if poster is None:   # ještě nezjišťováno: dohledat a zapamatovat si
            poster = _download_poster(lib, vid, videos.get(vid))
            if poster is not None:
                posters[vid] = poster
        image = poster or (entry.get("image") if not entry.get("wide") else "")
        cards.append(_card(entry.get("title") or vid, _target("play", "play", {"vid": vid}), _download_status(entry),
                           image or entry.get("image"), percent or _percent(point), wide=not image,
                           watched=vid in state["watched"], menu=_video_menu(state, {"vid": vid}, point), icon="download"))
    if posters:
        with _DOWNLOADS_LOCK:
            saved = _downloads()
            for vid, poster in posters.items():
                if vid in saved:
                    saved[vid]["poster"] = poster
            _save_downloads(saved)
    return cards


def downloads(params):
    """Stažená videa; ``kind`` ``movie`` / ``episode`` vybere jen filmy / jen díly seriálů."""
    episodes = {"movie": False, "episode": True}.get(params.get("kind"))
    with _library() as lib:
        cards = _download_cards(lib, _state(lib), episodes=episodes)
    title = {False: "Stažené filmy", True: "Stažené díly"}.get(episodes, "Stahování")
    if cards and episodes is None:
        # správa úložiště na konci výpisu: kam se stahuje a přesun stažených souborů mezi disky
        cards.append(_card("Kam stahovat", _target("storage"), "Vybrat disk pro nová stahování", icon="settings"))
        if any(entry.get("state") == "done" for entry in _downloads().values()):
            cards.append(_card("Přesunout vše na jiný disk", _target("move", "download_move", {"all": True}),
                               "Všechny stažené soubory", icon="download"))
    return {"title": title, "items": cards, "next": None,
            # dokud se stahuje nebo přesouvá, výpis se sám obnovuje
            "refresh": 3 if any(entry.get("state") in ("queued", "downloading") or entry.get("move_to")
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
            if isinstance(error, Blocked) or _offline(error):
                # Výpadek spojení nebo přihlášení se netýká jednoho videa: fronta zůstane, jak je,
                # a služba to zkusí později. Jinak by krátký výpadek označil za nepovedené všechno.
                raise
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
                # známá velikost rozpracovaného souboru: navazuje se jen na tentýž soubor
                "total": int(entry.get("total") or 0), "subtitles": tracks}


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
        _save_downloads(items, durable=False)   # průběh se zapisuje každou chvíli: bez vynucení na disk
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


# ---------------------------------------------------------------------------
# Přesun stažených souborů mezi disky
# ---------------------------------------------------------------------------

def _movable(items, vid=""):
    """Stažené soubory, které jde přesunout: hotové a právě dostupné (jejich disk je připojený)."""
    return [(key, entry) for key, entry in items.items()
            if entry.get("state") == "done" and os.path.exists(entry.get("path") or "") and (not vid or key == vid)]


def download_move_plan(params):
    """Co by se přesouvalo: počet souborů, jejich velikost a složky, kde teď leží (pro výběr cílového disku)."""
    found = _movable(_downloads(), str(params.get("vid") or ""))
    if not found:
        raise ApiError("Není co přesouvat – žádný stažený soubor teď není dostupný.")
    size = 0
    for _key, entry in found:
        try:
            size += os.path.getsize(entry["path"])
        except OSError:
            pass
    return {"count": len(found), "bytes": size, "dirs": sorted({os.path.dirname(entry["path"]) for _key, entry in found})}


def download_move(params):
    """Zařadí stažené soubory k přesunu na jiný disk (``dir``); přenáší je služba aplikace."""
    directory = str(params.get("dir") or "").rstrip("/")
    if not directory or not os.path.isdir(directory):
        raise ApiError("Cílový disk není připojený.")
    count = 0
    with _DOWNLOADS_LOCK:
        items = _downloads()
        for _key, entry in _movable(items, str(params.get("vid") or "")):
            if os.path.dirname(entry["path"]) != directory:
                entry["move_to"] = directory
                count += 1
        if count:
            _save_downloads(items)
    if not count:
        return {"message": "Soubory už na tomto disku jsou."}
    return {"message": "", "toast": "Přesouvám souborů: %d. Průběh je v oznámení." % count, "start": True}


def move_next(params):
    """Další soubor k přesunu: ``{"vid", "title", "from", "to", "size", "subtitles"}``, nebo ``{"vid": ""}``.

    Přesun, který nejde provést (zdroj zmizel, cílový disk není připojený), se ruší."""
    with _DOWNLOADS_LOCK:
        items = _downloads()
        job, changed = None, False
        for vid, entry in items.items():
            target = entry.get("move_to")
            if not target:
                continue
            source = entry.get("path") or ""
            if entry.get("state") != "done" or not os.path.exists(source) or not os.path.isdir(target):
                entry.pop("move_to", None)
                changed = True
                continue
            job = {"vid": vid, "title": entry.get("title") or "", "from": source,
                   "to": os.path.join(target, os.path.basename(source)), "size": os.path.getsize(source),
                   "subtitles": [name for name in entry.get("subtitles") or [] if os.path.exists(name)]}
            break
        if changed:
            _save_downloads(items)
    return job or {"vid": ""}


def move_finish(params):
    """Soubor je na novém místě: evidence dostane novou cestu a původní soubory se smažou."""
    vid, path = str(params.get("vid") or ""), str(params.get("path") or "")
    kept = [str(name) for name in params.get("subtitles") or []]
    with _DOWNLOADS_LOCK:
        items = _downloads()
        entry = items.get(vid)
        if not entry or not path or not os.path.exists(path):
            if entry:
                entry.pop("move_to", None)
                _save_downloads(items)
            return {"saved": False}
        old = [entry.get("path") or ""] + list(entry.get("subtitles") or [])
        entry.update({"path": path, "subtitles": kept})
        entry.pop("move_to", None)
        _save_downloads(items)
    for name in old:
        if name and name != path and name not in kept:
            try:
                os.remove(name)
            except OSError:
                pass   # původní disk mezitím zmizel: soubor na něm zůstane, evidence už ukazuje na nový
    return {"saved": True}


def move_fail(params):
    """Přesun se nepovedl: soubor zůstává, kde byl."""
    vid = str(params.get("vid") or "")
    with _DOWNLOADS_LOCK:
        items = _downloads()
        if vid in items:
            items[vid].pop("move_to", None)
            _save_downloads(items)
    _remember_error("přesun", params.get("error") or "přesun se nepodařil")
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


def _update_from_page(current):
    """Nejnovější vydání aplikace zjištěné bez rozhraní GitHubu: stránka „nejnovější vydání“ přesměruje
    na jeho značku (``…/releases/tag/app-v<verze>``) a adresa souboru APK se z verze složí. Vrací totéž
    co ``update_check``, nebo ``None``, když to nejde zjistit ani takhle.

    Otisk souboru se tudy zjistit nedá (``sha256`` zůstane prázdný); že jde o pravou aktualizaci, hlídá
    při instalaci Android sám – přijme jen soubor podepsaný stejným klíčem jako nainstalovaná aplikace."""
    headers = {"User-Agent": "MojeVidea-Android/%s" % (current or "0")}
    try:
        with urllib.request.urlopen(urllib.request.Request(UPDATE_PAGE, headers=headers), timeout=15) as response:
            final = response.geturl()
    except (OSError, ValueError):
        return None
    match = re.search(r"/releases/tag/%s(\d+(?:\.\d+)*)/?$" % re.escape(UPDATE_TAG), final or "")
    if not match:
        return None
    version = match.group(1)
    if _version(version) <= _version(current):
        return {"available": False, "current": current}
    notes = ""
    try:
        with urllib.request.urlopen(urllib.request.Request(UPDATE_NOTES % version, headers=headers),
                                    timeout=8) as response:
            notes = response.read().decode("utf-8", "replace").strip()[:800]
    except (OSError, ValueError):
        pass   # bez popisu změn to jde také
    return {"available": True, "current": current, "version": version, "url": UPDATE_ASSET % (version, version),
            "size": 0, "notes": notes, "sha256": ""}


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
        # rozhraní GitHubu dotaz odmítlo (nejčastěji omezení počtu dotazů): zkusit běžnou stránku vydání
        found = None if settings.get("update_url") else _update_from_page(current)
        if found is not None:
            _change_settings(lambda settings: settings.update(update_checked=int(time.time())))
            return found
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
    _change_settings(lambda settings: settings.update(update_checked=int(time.time())))
    if best is None or _version(best[0]) <= _version(current):
        return {"available": False, "current": current}
    version, asset, release = best
    # GitHub u přílohy uvádí její otisk; aplikace si podle něj stažený soubor před instalací ověří
    digest = str(asset.get("digest") or "")
    return {"available": True, "current": current, "version": version, "url": asset["browser_download_url"],
            "size": int(asset.get("size") or 0), "notes": str(release.get("body") or "").strip()[:800],
            "sha256": digest[7:].lower() if digest.startswith("sha256:") and len(digest) == 71 else ""}


# ---------------------------------------------------------------------------

import app_discover  # noqa: E402 – až po definicích výše: modul používá pomocné funkce odsud
import app_extras  # noqa: E402
import app_index  # noqa: E402
import app_backup  # noqa: E402
import app_health  # noqa: E402
import app_guide  # noqa: E402

ACTIONS = dict(app_discover.ACTIONS)
ACTIONS.update(app_extras.ACTIONS)
ACTIONS.update(app_index.ACTIONS)
ACTIONS.update(app_backup.ACTIONS)
ACTIONS.update(app_health.ACTIONS)
ACTIONS.update(app_guide.ACTIONS)
ACTIONS.update({
    "downloads": downloads, "download_add": download_add, "download_next": download_next,
    "download_progress": download_progress, "download_finish": download_finish, "download_fail": download_fail,
    "download_remove": download_remove, "update_check": update_check,
    "download_move_plan": download_move_plan, "download_move": download_move, "move_next": move_next,
    "move_finish": move_finish, "move_fail": move_fail,
    "progress_remove": progress_remove, "watched_set": watched_set, "downloads_cleanup": downloads_cleanup,
    "downloads_pending": downloads_pending,
    "home": home, "discover_home": discover_home, "movies": movies, "shows": shows, "show": show, "played": played,
    "search": search,
    "prehrajto_search": prehrajto_search, "search_history": search_history, "play_failed": play_failed,
    "play": play, "progress_save": progress_save, "link_failed": link_failed, "video_size": video_size,
    "settings_get": settings_get, "settings_set": settings_set, "account_status": account_status,
    "progress": progress, "sync": sync, "quick_sync": quick_sync, "enrich": enrich,
    "repair": repair, "repair_query": repair_query, "repair_apply": repair_apply, "repair_video": repair_video,
    "search_filters": search_filters,
    "ivys_live": ivys_live, "ivys_category": ivys_category, "ivys_listing": ivys_listing, "ivys_show": ivys_show,
    "ivys_favorites": ivys_favorites, "ivys_favorite": ivys_favorite, "ivys_play": ivys_play,
})


def _register(**actions):
    """Akce definované až pod tabulkou výše."""
    ACTIONS.update(actions)


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


def _errors():
    try:
        with open(_path(ERRORS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, ApiError):
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def _remember_error(action, text):
    """Posledních pár chyb pro Diagnostiku; zápis nesmí nikdy shodit odpověď."""
    try:
        entries = ([{"time": int(time.time()), "action": str(action)[:40], "text": str(text)[:300]}] + _errors())
        with open(_path(ERRORS), "w", encoding="utf-8") as f:
            json.dump(entries[:ERRORS_KEEP], f, ensure_ascii=False)
    except (OSError, ApiError):
        pass


def log_error(params):
    """Chyba, která vznikla v aplikaci mimo logiku (přehrávač): ať je vidět v Diagnostice."""
    _remember_error(params.get("where") or "aplikace", params.get("text") or "")
    return {"saved": True}


def diagnostics(params):
    """Stav pro obrazovku Diagnostika: ``{"lines": [{"label", "value", "ok"}], "errors": [text]}``.

    ``ok`` je True/False, nebo None u pouhé informace. Každá zkouška má krátký limit a selhání
    jedné nezastaví ostatní.
    """
    settings = _settings()
    lines = []

    def add(label, value, ok=None):
        lines.append({"label": label, "value": value, "ok": ok})

    if not settings.get("username"):
        add("Přehraj.to", "nepřihlášeno (vyplňte e-mail a heslo v Nastavení)", False)
    else:
        try:
            add("Přehraj.to", "%s – %s" % (settings["username"], account_status({})["message"]), True)
        except ApiError as error:
            add("Přehraj.to", "%s – %s" % (settings["username"], error), False)
    tmdb = Tmdb(settings.get("tmdb_key", ""), timeout=8)   # bez mezipaměti: ověřuje se klíč i spojení
    if not tmdb.enabled:
        add("TMDB", "klíč není vyplněný (Objevuj a plakáty nebudou fungovat)", False)
    else:
        try:
            add("TMDB", "klíč platí, %d filmových žánrů" % len(tmdb.genres("movie")), True)
        except TmdbError as error:
            add("TMDB", str(error), False)
    try:
        add("iVysílání", "dostupné, %d kategorií" % len(_ivys(timeout=8).menu()), True)
    except ivysilani.IvysilaniError as error:
        add("iVysílání", str(error), False)
    with _library() as lib:
        add("Katalog", "%d videí, %d filmů, %d seriálů" % (
            lib.count_videos(visible_only=True), lib.count_movies(), len(lib.shows())))
    downloads = _downloads()
    add("Stahování", "%s; úložiště: %s" % (_downloads_summary(downloads), settings.get("download_dir") or "automaticky"))
    last = settings.get("last_sync")
    add("Synchronizace", time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "zatím neproběhla")
    prefs = _prefs(settings)
    add("Předvolby", "%s, %s" % (dict(LANGUAGE_CHOICES)[prefs["language"]], dict(QUALITY_CHOICES)[prefs["quality"]]))
    add("Seznam dostupných titulů", app_index.summary_text())
    add("Seznam – deník", app_index.journal_text())
    add("Nabídka Přehraj.to", app_index.harvest_text())
    add("Zkouška Přehraj.to", *app_health.site_text())
    add("Kopie katalogu", app_health.snapshots_text())
    errors = ["%s  %s: %s" % (time.strftime("%d.%m. %H:%M", time.localtime(entry.get("time") or 0)),
                              entry.get("action") or "", entry.get("text") or "") for entry in _errors()]
    # zkušební předání oficiální aplikaci ČT: obrazovka podle něj zjistí, co aplikace v zařízení přijímá
    return {"lines": lines, "errors": errors,
            "external": _ivys_official("10195164142", "208522161400008", "Vyprávěj", "Vyprávěj")}


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
        _remember_error(action, error)
        return _failure(str(error), error)
    except Exception as error:  # noqa: BLE001 – aplikace musí dostat odpověď, ne spadnout
        text = "Neočekávaná chyba: %s: %s" % (type(error).__name__, error)
        _remember_error(action, text)
        return _failure(text, error)


_register(diagnostics=diagnostics, log_error=log_error, catalog_filters=catalog_filters,
          prefs_choices=prefs_choices)
