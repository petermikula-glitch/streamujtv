# -*- coding: utf-8 -*-
"""Metadata a hodnocení z ČSFD (csfd.cz).

ČSFD nemá veřejné API, čtou se tedy běžné HTML stránky – vyhledávání
(``/hledat/?q=``) a přehled filmu (``/film/<id>/prehled/``). Přednostně se
používají strukturovaná data JSON-LD (schema.org Movie/TVSeries), která jsou
stabilnější než CSS třídy; HTML selektory slouží jako záloha.

Od roku 2025 ČSFD chrání web anti-bot ochranou (Anubis). Doplněk ji záměrně
NEOBCHÁZÍ: když místo stránky přijde výzva proti botům, vyhodí se
``CsfdBlocked`` a katalog použije záložní zdroj (TMDB) nebo ruční zadání.
"""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from nameparser import normalize

BASE = "https://www.csfd.cz"
USER_AGENT = "Promitarna (+https://github.com/petermikula-glitch/streamujtv)"
TIMEOUT = 20
MIN_DELAY = 1.5   # s – ohleduplné tempo dotazů


class CsfdError(Exception):
    pass


class CsfdBlocked(CsfdError):
    """ČSFD vrátil výzvu proti botům místo obsahu."""


_last_request = [0.0]


def is_challenge(page):
    head = (page or "")[:6000].lower()
    return ("anubis" in head or "making sure you" in head or "not a bot" in head
            or "cf-challenge" in head or "challenge-platform" in head)


def _get(url, log=None):
    wait = MIN_DELAY - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()
    if log:
        log("csfd: GET %s" % url)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "cs-CZ,cs;q=0.9",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        try:
            page = resp.read().decode("utf-8", "replace")
            final = resp.geturl()
        finally:
            resp.close()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            raise CsfdBlocked("ČSFD odmítl dotaz (HTTP %s)" % e.code)
        raise CsfdError("ČSFD: HTTP %s" % e.code)
    except Exception as e:  # noqa: BLE001
        raise CsfdError("ČSFD je nedostupné: %s" % e)
    if is_challenge(page):
        raise CsfdBlocked("ČSFD vyžaduje ověření prohlížečem (ochrana proti botům)")
    return page, final


def _text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in fragment.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _abs_img(src):
    if not src:
        return ""
    src = html.unescape(src.strip())
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = BASE + src
    return src


def film_id_from_url(url):
    m = re.search(r"/film/(\d+)(?:-[^/]*)?/(?:(\d+)(?:-[^/]*)?/)?", url or "")
    if not m:
        m = re.match(r"^\s*(\d+)\s*$", url or "")
        return int(m.group(1)) if m else 0
    return int(m.group(1))


def film_url(film_id):
    return "%s/film/%s/prehled/" % (BASE, film_id)


# ---------------------------------------------------------------------------
# Vyhledávání
# ---------------------------------------------------------------------------

_SEARCH_ITEM_RE = re.compile(
    r"<a\b[^>]*href=\"(/film/(\d+)-[^\"/]*/)[^\"]*\"[^>]*class=\"[^\"]*film-title-name[^\"]*\"[^>]*>(.*?)</a>"
    r"|<a\b[^>]*class=\"[^\"]*film-title-name[^\"]*\"[^>]*href=\"(/film/(\d+)-[^\"/]*/)[^\"]*\"[^>]*>(.*?)</a>",
    re.S | re.I)

SERIES_WORDS = ("seriál", "série", "tv seriál", "epizoda", "pořad", "tv pořad", "minisérie")


def parse_search(page):
    """Výsledky hledání: [{id, url, title, year, type}] (type: film|seriál|...)."""
    results = []
    seen = set()
    for m in _SEARCH_ITEM_RE.finditer(page or ""):
        path = m.group(1) or m.group(4)
        fid = int(m.group(2) or m.group(5))
        title = _text(m.group(3) or m.group(6))
        if fid in seen or not title:
            continue
        seen.add(fid)
        tail = page[m.end():m.end() + 500]
        # konec položky – další odkaz na film už patří jiné položce
        nxt = tail.find("film-title-name")
        if nxt > 0:
            tail = tail[:nxt]
        year = 0
        ym = re.search(r"\((\d{4})\)", tail)
        if ym:
            year = int(ym.group(1))
        kind = "film"
        for tm in re.finditer(r"\(([^()0-9]{3,30})\)", tail):
            word = _text(tm.group(1)).lower()
            if word:
                kind = word
                break
        results.append({"id": fid, "url": BASE + path, "title": title, "year": year, "type": kind})
    return results


def is_series_type(kind):
    kind = (kind or "").lower()
    return any(w in kind for w in SERIES_WORDS)


def search(query, log=None):
    page, final = _get(BASE + "/hledat/?q=" + urllib.parse.quote(query), log)
    # ČSFD při jednoznačné shodě přesměruje rovnou na film
    if "/film/" in final:
        fid = film_id_from_url(final)
        if fid:
            details = parse_film(page, final)
            return [{"id": fid, "url": final, "title": details.get("title", query),
                     "year": details.get("year", 0), "type": details.get("type", "film")}]
    return parse_search(page)


def best_match(results, title, year=0, want_series=False):
    """Vybere nejlepší výsledek podle shody názvu, roku a typu."""
    target = normalize(title)
    best = None
    best_score = -1
    for idx, r in enumerate(results):
        name = normalize(r["title"])
        score = 0
        if name == target:
            score += 50
        elif target and (name.startswith(target) or target.startswith(name)):
            score += 25
        elif target and target in name:
            score += 10
        else:
            tw = set(target.split())
            nw = set(name.split())
            if tw and nw:
                score += int(20 * len(tw & nw) / max(len(tw), len(nw)))
        if year and r.get("year"):
            diff = abs(int(r["year"]) - int(year))
            score += 30 if diff == 0 else (15 if diff == 1 else -20)
        series = is_series_type(r.get("type"))
        if want_series == series:
            score += 15
        elif r.get("type") in ("epizoda",):
            score -= 30
        else:
            score -= 10
        score -= idx  # pořadí ČSFD (relevance) jako jemné kritérium
        if score > best_score:
            best, best_score = r, score
    if best is None or best_score < 15:
        return None
    return best


# ---------------------------------------------------------------------------
# Detail filmu
# ---------------------------------------------------------------------------

def _jsonld(page):
    for m in re.finditer(r"<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>",
                         page or "", re.S | re.I):
        try:
            data = json.loads(html.unescape(m.group(1).strip()))
        except ValueError:
            try:
                data = json.loads(m.group(1).strip())
            except ValueError:
                continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and "@graph" in it:
                items.extend(it["@graph"])
        for it in items:
            if isinstance(it, dict) and str(it.get("@type", "")).lower() in (
                    "movie", "tvseries", "tvseason", "tvepisode", "creativework"):
                return it
    return {}


def _names(value):
    out = []
    if isinstance(value, dict):
        value = [value]
    for v in value or []:
        if isinstance(v, dict) and v.get("name"):
            out.append(html.unescape(str(v["name"])).strip())
        elif isinstance(v, str):
            out.append(html.unescape(v).strip())
    return [x for x in out if x]


def _section_people(page, label):
    m = re.search(r"<h4>\s*%s\s*:?\s*</h4>(.*?)</div>" % label, page or "", re.S | re.I)
    if not m:
        return []
    return [_text(a) for a in re.findall(r"<a\b[^>]*>(.*?)</a>", m.group(1), re.S)
            if _text(a) and _text(a).lower() != "více"]


def parse_film(page, url=""):
    """Rozbor stránky titulu; neobvyklá (ale platná) data stránky hlásí jako CsfdError,
    aby jedna stránka nepřerušila doplňování celého katalogu."""
    try:
        return _parse_film(page, url)
    except CsfdError:
        raise
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
        raise CsfdError("stránku ČSFD nelze zpracovat: %s" % e)


def _parse_film(page, url=""):
    """Rozparsuje stránku přehledu filmu/seriálu."""
    ld = _jsonld(page)
    info = {"csfd_id": film_id_from_url(url), "csfd_url": url, "title": "", "orig_title": "",
            "year": 0, "rating": 0, "votes": 0, "genres": [], "countries": [], "directors": [],
            "cast": [], "plot": "", "poster": "", "runtime": 0, "type": "film"}

    # název
    m = re.search(r"<div class=\"film-header-name\"[^>]*>\s*<h1[^>]*>(.*?)</h1>", page or "", re.S)
    if m:
        info["title"] = _text(m.group(1))
    if not info["title"] and ld.get("name"):
        info["title"] = html.unescape(str(ld["name"])).strip()
    if not info["title"]:
        m = re.search(r"<title>(.*?)</title>", page or "", re.S)
        if m:
            info["title"] = re.sub(r"\s*[|(].*$", "", _text(m.group(1))).strip()
    if ld.get("@type", "").lower() in ("tvseries", "tvseason"):
        info["type"] = "seriál"
    m = re.search(r"<div class=\"film-header-name\".*?<span class=\"type\"[^>]*>\s*\(([^)]*)\)", page or "", re.S)
    if m:
        info["type"] = _text(m.group(1)).lower() or info["type"]

    # hodnocení
    agg = ld.get("aggregateRating") or {}
    try:
        rv = float(str(agg.get("ratingValue", "0")).replace(",", "."))
        best = float(str(agg.get("bestRating", "100")).replace(",", ".") or 100)
        if rv:
            info["rating"] = int(round(rv * 100.0 / best)) if best and best != 100 else int(round(rv))
        info["votes"] = int(agg.get("ratingCount") or agg.get("reviewCount") or 0)
    except (TypeError, ValueError):
        pass
    if not info["rating"]:
        m = re.search(r"class=\"film-rating-average[^\"]*\"[^>]*>\s*(\d{1,3})\s*%", page or "", re.S)
        if m:
            info["rating"] = int(m.group(1))
    if not info["votes"]:
        m = re.search(r"Hodnocení\s*<span[^>]*>\s*\(?([\d\s\xa0]+)\)?", page or "", re.S)
        if m:
            try:
                info["votes"] = int(re.sub(r"\D", "", m.group(1)))
            except ValueError:
                pass

    # žánry
    m = re.search(r"<div class=\"genres\"[^>]*>(.*?)</div>", page or "", re.S)
    if m:
        info["genres"] = [g.strip() for g in _text(m.group(1)).split("/") if g.strip()]
    if not info["genres"] and ld.get("genre"):
        g = ld["genre"]
        info["genres"] = [html.unescape(x) for x in (g if isinstance(g, list) else [g])]

    # původ: "USA, 1999, 136 min" nebo "Česko / Slovensko, (2005), 95 min"
    m = re.search(r"<div class=\"origin\"[^>]*>(.*?)</div>", page or "", re.S)
    if m:
        origin = _text(m.group(1)).replace("\n", " ")
        parts = [p.strip() for p in origin.split(",")]
        if parts:
            if not re.search(r"\d", parts[0]):
                info["countries"] = [c.strip() for c in parts[0].split("/") if c.strip()]
        ym = re.search(r"\b(1[89]\d\d|20\d\d)\b", origin)
        if ym:
            info["year"] = int(ym.group(1))
        rm = re.search(r"(\d+)\s*h\s*(\d+)\s*min", origin)
        if rm:
            info["runtime"] = int(rm.group(1)) * 60 + int(rm.group(2))
        else:
            rm = re.search(r"(\d+)\s*min", origin)
            if rm:
                info["runtime"] = int(rm.group(1))
    if not info["year"]:
        for k in ("dateCreated", "datePublished", "startDate"):
            if ld.get(k):
                ym = re.search(r"(\d{4})", str(ld[k]))
                if ym:
                    info["year"] = int(ym.group(1))
                    break

    # tvůrci
    info["directors"] = _names(ld.get("director")) or _section_people(page, "Režie")
    info["cast"] = (_names(ld.get("actor")) or _section_people(page, "Hrají"))[:15]

    # děj
    for pat in (r"<div class=\"plot-full[^\"]*\"[^>]*>(.*?)</div>",
                r"<div class=\"plot-preview[^\"]*\"[^>]*>(.*?)</div>",
                r"<div class=\"plots?\b[^\"]*\"[^>]*>(.*?)</div>"):
        m = re.search(pat, page or "", re.S)
        if m:
            plot = _text(re.sub(r"<span class=\"span-more-small\".*?</span>", "", m.group(1), flags=re.S))
            plot = re.sub(r"\(\s*[^()]{1,40}\)\s*$", "", plot).strip()  # podpis autora obsahu
            if plot:
                info["plot"] = plot
                break
    if not info["plot"] and ld.get("description"):
        info["plot"] = html.unescape(str(ld["description"])).strip()

    # plakát
    m = re.search(r"<div class=\"film-posters?[^\"]*\"[^>]*>.*?<img[^>]*?src=\"([^\"]+)\"", page or "", re.S)
    if m and "noimage" not in m.group(1):
        info["poster"] = _abs_img(m.group(1))
    if not info["poster"] and ld.get("image"):
        img = ld["image"]
        if isinstance(img, dict):
            img = img.get("url", "")
        if isinstance(img, list):
            img = img[0] if img else ""
        info["poster"] = _abs_img(str(img))
    # větší varianta plakátu (pmgstatic škáluje podle /cache/resized/w140/ apod.)
    info["poster"] = re.sub(r"/cache/resized/w\d+(?:h\d+)?/", "/cache/resized/w420/", info["poster"])

    # další názvy (původní)
    names = re.search(r"<ul class=\"film-names\"[^>]*>(.*?)</ul>", page or "", re.S)
    if names:
        for li in re.findall(r"<li[^>]*>(.*?)</li>", names.group(1), re.S):
            t = _text(re.sub(r"<img[^>]*>", "", li))
            t = re.sub(r"\s*\((?:více|méně)\)\s*$", "", t)
            if t and normalize(t) != normalize(info["title"]):
                info["orig_title"] = t
                break
    return info


def details(film_id, log=None):
    page, final = _get(film_url(film_id), log)
    info = parse_film(page, final)
    if not info["csfd_id"]:
        info["csfd_id"] = int(film_id)
    return info


def find(title, year=0, want_series=False, log=None):
    """Najde titul a vrátí detail, nebo None."""
    results = search(title, log)
    match = best_match(results, title, year, want_series)
    if match:
        return details(match["id"], log)
    return None
