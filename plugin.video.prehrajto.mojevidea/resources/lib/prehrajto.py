# -*- coding: utf-8 -*-
"""Klient pro Přehraj.to.

* Přihlášení: formulář Nette ``/?frm=loginDialog-login-loginForm`` (pole ``email``,
  ``password``); po úspěchu server nastaví cookies ``access_token`` (JWT, ~10 min)
  a ``refresh_token``. Ověřeno proti živému webu 2026-11.
* Vlastní nahraná videa: HTML stránka ``/profil/nahrana-videa`` (jen přihlášeným).
* Přehrání:
  - originální soubor (premium): JSON API ``https://prehrajto.cz/api/v2/videos/<id>/download``
    s ``Authorization: Bearer <access_token>``, záložně HTML ``<stránka videa>?do=download``
    (přesměrování na podepsaný odkaz CDN),
  - překódované verze: ``videos.push({src: …, res: '1080'})`` ve stránce videa.

Odkaz na video v katalogu má tvar ``<id>:<slug>:<hash>`` (id může chybět) – stránka
videa je ``https://prehraj.to/<slug>/<hash>``.

Modul nesmí importovat nic z Kodi, aby šel testovat mimo něj.
"""
import base64
import html
import http.client
import http.cookiejar
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://prehraj.to"
API_BASE = "https://prehrajto.cz/api/v2"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
TIMEOUT = 30
UPLOADS_PATH = "/profil/nahrana-videa"

QUALITY_ORDER = ("ORIG", "UHD", "FHD", "HD", "SD")
QUALITY_LABEL = {"ORIG": "originál", "UHD": "2160p", "FHD": "1080p", "HD": "720p", "SD": "480p"}

CHECK_BAD_LOGIN = 0
CHECK_NO_PREMIUM = 1
CHECK_PREMIUM = 2

_RESERVED = {"profil", "public", "assets", "hledej", "api", "faq", "podminky", "front"}
PAGE_URL_RE = re.compile(r"(?:https?://(?:www\.)?prehraj\.to)?/([A-Za-z0-9._-]{1,200})/([0-9a-f]{8,32})(?=[/?#\"']|$)")
REF_RE = re.compile(r"^(\d{0,15}):([A-Za-z0-9._-]{1,200}):([0-9a-f]{8,32})$")

ITEM_RE = re.compile(
    r'data-video-id="(\d+)"[^>]*>\s*<a\b([^>]*)>(.*?)</a>', re.S)
DUR_RE = re.compile(r'video__tag--time[^"]*">\s*([^<]+)')
QUALITY_RE = re.compile(r'format__text[^"]*">\s*([^<]+)')
SIZE_RE = re.compile(r'video__tag--size[^"]*">\s*([^<]+)')
SOURCE_RE = re.compile(r"""videos\.push\(\{\s*src:\s*["']([^"']+)["'].*?res:\s*['"](\d+)['"]""", re.S)
TRACK_BLOCK_RE = re.compile(r"var\s+tracks\s*=\s*\[(.*?)\]\s*;", re.S)
TRACK_ITEM_RE = re.compile(r"\{(.*?)\}", re.S)
TRACK_URL_RE = re.compile(r"""(?:file|src):\s*["']([^"']+\.(?:vtt|srt)[^"']*)["']""")
TRACK_LANG_RE = re.compile(r"""(?:srclang|label):\s*["']([A-Za-z]{2,4})""")
PAGE_VIDEO_ID_RES = [
    re.compile(r'data-video-id="(\d+)"'),
    re.compile(r'["\']?video_?[iI]d["\']?\s*[:=]\s*["\']?(\d{3,15})'),
    re.compile(r'/api/v2/videos/(\d+)'),
]
PREMIUM_RE = re.compile(r"PREMIUM\s*(?:<[^>]*>\s*)*(\d+)\s*dn", re.S)
DAYS_RE = re.compile(r"Vyprší za\s*(?:<[^>]*>\s*)*(\d+)", re.S)


class PrehrajtoError(Exception):
    pass


class LoginError(PrehrajtoError):
    pass


class VideoUnavailableError(PrehrajtoError):
    pass


class TemporaryError(PrehrajtoError):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after=0):
        Exception.__init__(self, "HTTP 429")
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Odkazy
# ---------------------------------------------------------------------------

def make_ref(video_id, slug, hash_):
    return "%s:%s:%s" % (video_id or "", slug, hash_)


def split_ref(ref):
    m = REF_RE.match(ref or "")
    if not m:
        raise PrehrajtoError("neplatný odkaz na video: %s" % ref)
    return m.group(1), m.group(2), m.group(3)


def extract_video_id(text):
    """Odkaz ``https://prehraj.to/<slug>/<hash>`` nebo ref → ref, jinak None."""
    text = (text or "").strip()
    if REF_RE.match(text):
        return text
    for m in PAGE_URL_RE.finditer(text):
        if m.group(1).lower() in _RESERVED:
            continue
        if not text.startswith("/") and "prehraj.to" not in text[:m.end()]:
            continue  # holá cesta bez domény jen na začátku řádku
        return make_ref("", m.group(1), m.group(2))
    return None


def video_page_url(ref):
    _, slug, h = split_ref(ref)
    return "%s/%s/%s" % (BASE, slug, h)


def thumb_url(ref):
    return ""


def same_video(a, b):
    """Dva refy ukazují na stejné video (porovnává se hash)."""
    try:
        return split_ref(a)[2] == split_ref(b)[2]
    except PrehrajtoError:
        return False


def mask_secrets(text):
    text = re.sub(r"((?:password|token|access_token|refresh_token)=)[^&;\s]+", r"\1***", str(text or ""))
    return re.sub(r"(Bearer\s+)\S+", r"\1***", text)


# ---------------------------------------------------------------------------
# Parsování HTML
# ---------------------------------------------------------------------------

_ATTR_RE = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _attrs(tag_inner):
    return dict((m.group(1).lower(), html.unescape(m.group(2) if m.group(2) is not None else m.group(3)))
                for m in _ATTR_RE.finditer(tag_inner or ""))


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_duration(text):
    text = (text or "").strip()
    parts = [p for p in re.split(r"[:\s]+", text) if p.isdigit()]
    if not parts:
        return 0
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def _quality_from_res(res):
    res = int(res)
    if res >= 2000:
        return "UHD"
    if res >= 1000:
        return "FHD"
    if res >= 700:
        return "HD"
    return "SD"


def quality_token(value):
    """Normalizuje kvalitu z názvu souboru, z katalogu nebo ze streamu."""
    text = (value or "").strip()
    if text.upper() in QUALITY_ORDER:
        return text.upper()
    for key, label in QUALITY_LABEL.items():
        if text.casefold() == label.casefold():
            return key
    match = re.fullmatch(r"(\d{3,4})p?", text, re.I)
    return _quality_from_res(match[1]) if match else ""


def strip_comments(page):
    return re.sub(r"<!--.*?-->", "", page or "", flags=re.S)


def parse_video_list(page):
    """Položky výpisu (nahraná videa, výsledky): [{id, title, thumb, duration, quality, size}]."""
    page = strip_comments(page)
    items = []
    seen = set()

    def add(video_id, slug, h, title, body):
        if slug.lower() in _RESERVED or h in seen:
            return
        seen.add(h)
        thumb = ""
        img = re.search(r"<img\b([^>]*)>", body or "")
        if img:
            a = _attrs(img.group(1))
            thumb = a.get("data-src") or a.get("src") or ""
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            elif thumb.startswith("/"):
                thumb = BASE + thumb
            if not title:
                title = a.get("alt", "")
        dm = DUR_RE.search(body or "")
        qm = QUALITY_RE.search(body or "")
        sm = SIZE_RE.search(body or "")
        if not title:
            tm = re.search(r'class="[^"]*video__title[^"]*"[^>]*>(.*?)</', body or "", re.S)
            title = _text(tm.group(1)) if tm else ""
        items.append({"id": make_ref(video_id, slug, h), "title": _text(title) or slug.replace("-", " "),
                      "thumb": thumb, "duration": parse_duration(dm.group(1)) if dm else 0,
                      "quality": _text(qm.group(1)) if qm else "", "size": _text(sm.group(1)) if sm else ""})

    for m in ITEM_RE.finditer(page):
        attrs = _attrs(m.group(2))
        pm = PAGE_URL_RE.match(attrs.get("href", ""))
        if pm:
            add(m.group(1), pm.group(1), pm.group(2), attrs.get("title", ""), m.group(3))
    # záloha: jakýkoli odkaz na stránku videa (jiné rozložení stránky)
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", page, re.S):
        attrs = _attrs(m.group(1))
        pm = PAGE_URL_RE.match(attrs.get("href", ""))
        if pm and pm.group(2) not in seen:
            vid = attrs.get("data-video-id", "")
            add(vid, pm.group(1), pm.group(2), attrs.get("title", "") or _text(m.group(2)), m.group(2))
    return items


def parse_pagination(page, current_url):
    """Další stránky výpisu: ``?page=2``, ``?videoListing-page=2``, ``/nahrana-videa/2``."""
    section = urllib.parse.urlsplit(current_url).path.rstrip("/").split("/")[-1] or "profil"
    if section.isdigit():
        section = urllib.parse.urlsplit(current_url).path.rstrip("/").split("/")[-2]
    out = []
    for m in re.finditer(r"<a\b([^>]*)>", strip_comments(page)):
        href = _attrs(m.group(1)).get("href", "")
        if not href or href.startswith("#"):
            continue
        absolute = urllib.parse.urljoin(current_url, html.unescape(href))
        parts = urllib.parse.urlsplit(absolute)
        if (parts.hostname or "").lower() not in ("prehraj.to", "www.prehraj.to"):
            continue
        if section not in parts.path:
            continue
        if re.search(r"(?:^|&)[\w-]*page=\d+", parts.query) or re.search(r"/\d+/?$", parts.path):
            if absolute not in out and absolute != current_url:
                out.append(absolute)
    return out


def _source_variants(page):
    found = {}
    for url, resolution in SOURCE_RE.findall(page or ""):
        quality = _quality_from_res(resolution)
        previous = found.get(quality)
        if previous is None or int(resolution) > previous[1]:
            found[quality] = (html.unescape(url), int(resolution))
    return [(quality, *found[quality]) for quality in QUALITY_ORDER if quality in found]


def parse_sources(page):
    """Překódované verze ze stránky videa: [(kvalita, url)] od nejlepší."""
    return [(quality, url) for quality, url, _ in _source_variants(page)]


def parse_tracks(page):
    """Titulky ze stránky videa: {jazyk: url}."""
    out = {}
    block = TRACK_BLOCK_RE.search(page or "")
    text = block.group(1) if block else ""
    for item in TRACK_ITEM_RE.findall(text):
        um = TRACK_URL_RE.search(item)
        if not um:
            continue
        lm = TRACK_LANG_RE.search(item)
        lang = (lm.group(1) if lm else "CZ").upper()[:3]
        key = lang
        n = 2
        while key in out:
            key = "%s%d" % (lang, n)
            n += 1
        url = html.unescape(um.group(1))
        out[key] = ("https:" + url) if url.startswith("//") else url
    return out


def parse_video_page(page):
    title = ""
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', page or "", re.I)
    if m:
        title = html.unescape(m.group(1))
    if not title:
        m = re.search(r"<title>(.*?)</title>", page or "", re.S)
        if m:
            title = _text(m.group(1))
    title = re.sub(r"\s*-\s*online ke zhlédnutí.*$", "", title).strip()
    title = re.sub(r"\s*[-|]\s*Přehraj\.to\s*$", "", title).strip()
    thumb = ""
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', page or "", re.I)
    if m:
        thumb = html.unescape(m.group(1))
    desc = ""
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page or "", re.I)
    if m:
        desc = html.unescape(m.group(1)).strip()
    return {"title": title, "thumb": thumb, "description": desc, "video_id": parse_page_video_id(page)}


def parse_page_video_id(page):
    """Číslo videa ze stránky videa (potřebuje ho JSON API pro stažení originálu)."""
    for rx in PAGE_VIDEO_ID_RES:
        m = rx.search(page or "")
        if m:
            return m.group(1)
    return ""


def jwt_expired(token, margin=30):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload.encode("ascii"))).get("exp", 0)
        return time.time() + margin >= float(exp)
    except Exception:  # noqa: BLE001 – nečitelný token = považuj za prošlý
        return True


# ---------------------------------------------------------------------------
# Klient
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PrehrajtoClient(object):
    def __init__(self, email="", password="", cookie_file=None, log=None, opener=None,
                 noredirect_opener=None):
        self.username = (email or "").strip()
        self.password = password or ""
        self.cookie_file = cookie_file
        self.log = log or (lambda m: None)
        if cookie_file:
            self.cookies = http.cookiejar.LWPCookieJar(cookie_file)
            try:
                self.cookies.load(ignore_discard=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            self.cookies = http.cookiejar.CookieJar()
        processor = urllib.request.HTTPCookieProcessor(self.cookies)
        self.opener = opener or urllib.request.build_opener(processor)
        self.noredirect = noredirect_opener or urllib.request.build_opener(processor, _NoRedirect())
        self._logged_in = False
        self._last_video_page = None
        self.sleep = time.sleep
        self.request_timeout = TIMEOUT
        self.request_retries = 2

    @property
    def has_credentials(self):
        return bool(self.username and self.password)

    # --- HTTP -------------------------------------------------------------
    def _request(self, url, data=None, headers=None, follow=True, retries=None):
        """HTTP s opakováním po HTTP 429 (server dočasně omezuje dotazy)."""
        retries = self.request_retries if retries is None else retries
        for attempt in range(retries + 1):
            try:
                return self._request_once(url, data, headers, follow)
            except RateLimited as e:
                if attempt >= retries:
                    raise TemporaryError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")
                wait = min(30, e.retry_after) if e.retry_after > 0 else 5 * (attempt + 1)
                self.log("prehrajto: HTTP 429, čekám %s s" % wait)
                self.sleep(wait)

    def _request_once(self, url, data=None, headers=None, follow=True):
        if not url.startswith("http"):
            url = BASE + url
        hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "cs-CZ,cs;q=0.9"}
        hdrs.update(headers or {})
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        self.log("prehrajto: %s %s" % ("POST" if body else "GET", mask_secrets(url)))
        req = urllib.request.Request(url, data=body, headers=hdrs)
        opener = self.opener if follow else self.noredirect
        try:
            resp = opener.open(req, timeout=self.request_timeout)
        except urllib.error.HTTPError as e:
            if not follow and e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location") or ""
                e.close()
                return "", url, location, e.code
            if e.code == 429:
                try:
                    retry_after = int(e.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry_after = 0
                e.close()
                raise RateLimited(retry_after) from e
            e.close()
            message = "HTTP %s: %s" % (e.code, mask_secrets(url))
            if e.code == 401:
                raise LoginError(message) from e
            if e.code in (404, 410):
                raise VideoUnavailableError(message) from e
            if e.code == 408 or e.code >= 500:
                raise TemporaryError(message) from e
            raise PrehrajtoError(message) from e
        except (OSError, http.client.HTTPException) as e:
            raise TemporaryError("Přehraj.to je nedostupné: %s" % e) from e
        try:
            text = resp.read().decode("utf-8", "replace")
            final = resp.geturl()
            status = getattr(resp, "status", 200)
        except (OSError, http.client.HTTPException) as e:
            raise TemporaryError("Odpověď Přehraj.to nelze načíst: %s" % e) from e
        finally:
            resp.close()
        return text, final, "", status

    def _get(self, url):
        text, final, _, _ = self._request(url)
        return text, final

    def _cookie(self, name):
        for c in self.cookies:
            if c.name == name and c.value:
                return c.value
        return ""

    def _save_cookies(self):
        if self.cookie_file and isinstance(self.cookies, http.cookiejar.FileCookieJar):
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=".prehrajto-cookies-", dir=os.path.dirname(self.cookie_file) or ".")
                os.close(fd)
                self.cookies.save(temporary, ignore_discard=True)
                os.replace(temporary, self.cookie_file)
                temporary = None
            except (OSError, ValueError):
                self.log("prehrajto: cookies nelze uložit")
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass

    # --- přihlášení ---------------------------------------------------------
    def _profile_page(self):
        """Stránka /profil, nebo None, když přihlášení neplatí (přesměruje na úvod)."""
        page, final = self._get("/profil")
        # nepřihlášeného server přesměruje na úvodní stránku (ověřeno: 302 → /)
        if urllib.parse.urlsplit(final).path.startswith("/profil"):
            return page
        return None

    def login(self, force=False):
        """Přihlásí se. Uložené cookies se znovu použijí – každé nové přihlášení
        zakládá na serveru další záznam ve „Správě přihlášených zařízení"."""
        if self._logged_in and not force:
            return True
        if not self.has_credentials:
            raise LoginError("Není vyplněný e-mail nebo heslo k Přehraj.to")
        if not force and (self._cookie("refresh_token") or self._cookie("access_token")):
            if self._profile_page() is not None:
                self._logged_in = True
                self._save_cookies()
                return True
        self._get("/")  # založení relace
        self._request("/?frm=loginDialog-login-loginForm", data={
            "email": self.username,
            "password": self.password,
            "remember_login": "on",
            "login": "Přihlásit se",
            "_do": "loginDialog-login-loginForm-submit",
        }, headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        if not self._cookie("access_token"):
            raise LoginError("Přihlášení k Přehraj.to se nezdařilo – zkontroluj e-mail a heslo")
        self._logged_in = True
        self._save_cookies()
        return True

    def _bearer(self):
        self.login()
        token = self._cookie("access_token")
        if token and not jwt_expired(token):
            return token
        self._get("/")  # server vydá nový access_token z refresh_token
        token = self._cookie("access_token")
        if not token or jwt_expired(token):
            self.login(force=True)
            token = self._cookie("access_token")
        self._save_cookies()
        return token

    def api(self, path):
        token = self._bearer()
        req = urllib.request.Request("%s/%s" % (API_BASE, path.lstrip("/")), headers={
            "User-Agent": USER_AGENT, "Accept": "application/json", "Authorization": "Bearer " + token})
        self.log("prehrajto: API %s" % path)
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            try:
                return json.loads(resp.read().decode("utf-8"))
            finally:
                resp.close()
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401:
                raise LoginError("Přihlášení k API Přehraj.to vypršelo") from e
            if e.code in (408, 429) or e.code >= 500:
                raise TemporaryError("API HTTP %s" % e.code) from e
            raise PrehrajtoError("API HTTP %s" % e.code) from e
        except ValueError as e:
            raise TemporaryError("API vrátilo neplatnou odpověď") from e
        except (OSError, http.client.HTTPException) as e:
            raise TemporaryError("API je nedostupné: %s" % e) from e

    def check_user(self):
        """(stav, text platnosti) – stav CHECK_*."""
        if not self.has_credentials:
            return CHECK_BAD_LOGIN, None
        try:
            self.login()
        except LoginError:
            return CHECK_BAD_LOGIN, None
        page = self._profile_page() or ""
        m = PREMIUM_RE.search(page) or DAYS_RE.search(page)
        days = int(m.group(1)) if m else 0
        if days > 0:
            return CHECK_PREMIUM, days
        return CHECK_NO_PREMIUM, None

    # --- výpisy -------------------------------------------------------------
    def fetch_listing(self, start_url, max_pages=200, progress=None, delay=0.5):
        self.listing_complete = False
        start_url = start_url if start_url.startswith("http") else BASE + start_url
        start_parts = urllib.parse.urlsplit(start_url)
        start_path = start_parts.path.rstrip("/")
        to_visit = [start_url]
        visited = set()
        videos = []
        seen = set()
        skipped_page = False
        while to_visit and len(visited) < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)
            page, final = self._get(url)
            final_parts = urllib.parse.urlsplit(final)
            final_path = final_parts.path.rstrip("/")
            if (final_parts.netloc != start_parts.netloc or
                    (final_path != start_path and not final_path.startswith(start_path + "/"))):
                if len(visited) == 1:
                    raise LoginError("Stránka %s vyžaduje přihlášení" % start_url)
                skipped_page = True
                continue
            items = parse_video_list(page)
            if ((not items and len(visited) > 1)
                    or (items and not any(split_ref(item["id"])[0] for item in items))):
                skipped_page = True
            for item in items:
                h = item["id"].rsplit(":", 1)[-1]
                if h not in seen:
                    seen.add(h)
                    videos.append(item)
            if progress and progress(len(visited), len(videos)):
                return videos
            for nxt in parse_pagination(page, final):
                if nxt not in visited and nxt not in to_visit:
                    to_visit.append(nxt)
            if delay and to_visit:
                self.sleep(delay)
        self.listing_complete = not to_visit and not skipped_page
        return videos

    def search_page(self, query, page=1, cursor=""):
        """Jedna stránka veřejného vyhledávání Přehraj.to."""
        query = query.strip()
        if not query or page < 1:
            return [], None
        if self.has_credentials:
            self.login()
        path = "/hledej/" + urllib.parse.quote(query, safe="")
        search_path = urllib.parse.unquote(path).rstrip("/")
        url = BASE + path + "?vp-page=1"
        if page > 1:
            target = urllib.parse.urlsplit(cursor)
            cursor_path = urllib.parse.unquote(target.path).rstrip("/")
            if (target.scheme != "https" or target.netloc not in ("prehraj.to", "www.prehraj.to")
                    or cursor_path not in (search_path, search_path + "/%d" % page)):
                raise PrehrajtoError("Neplatná stránka výsledků")
            url = cursor
        body, final = self._get(url)
        parts = urllib.parse.urlsplit(final)
        if (parts.scheme != "https" or parts.netloc not in ("prehraj.to", "www.prehraj.to")
                or not parts.path.startswith("/hledej/")):
            raise PrehrajtoError("Přehraj.to nevrátilo stránku výsledků")
        next_page = None
        next_path = search_path + "/%d" % (page + 1)
        for candidate in parse_pagination(body, final):
            target = urllib.parse.urlsplit(candidate)
            params = urllib.parse.parse_qs(target.query)
            target_path = urllib.parse.unquote(target.path).rstrip("/")
            if target.scheme != "https" or target_path not in (search_path, next_path):
                continue
            numbered = any(key.lower().endswith("page") and str(page + 1) in values
                           for key, values in params.items())
            if target_path == next_path or numbered:
                next_page = candidate
                break
        return parse_video_list(body), next_page

    def my_videos(self, progress=None):
        self.login()
        return self.fetch_listing(BASE + UPLOADS_PATH, progress=progress)

    def my_favorites(self, progress=None):
        return []

    def my_playlists(self):
        return []

    def video_info(self, ref):
        page, _ = self._get(video_page_url(ref))
        info = parse_video_page(page)
        vid, slug, h = split_ref(ref)
        info["id"] = make_ref(vid or info.get("video_id"), slug, h)
        return info

    # --- přehrání -----------------------------------------------------------
    def _streams_from_page(self, ref):
        saved = self._last_video_page
        if not saved or saved[0] != ref:
            page, _ = self._get(video_page_url(ref))
            saved = (ref, parse_page_video_id(page), _source_variants(page), parse_tracks(page))
            self._last_video_page = saved
        return saved[1:]

    def get_streams(self, ref):
        streams = []
        login_error = None
        message = ""
        subtitles = {}
        if self.has_credentials:
            try:
                self.login()
            except LoginError as e:
                login_error = e
                message = str(e)
            else:
                streams.append({"lang": "", "quality": "ORIG", "url": "orig:" + ref, "subtitles": {}})
        try:
            _video_id, variants, subtitles = self._streams_from_page(ref)
            for quality, url, height in variants:
                streams.append({"lang": "", "quality": quality, "url": url,
                                "resolution": "%sp" % height, "subtitles": {}})
        except VideoUnavailableError:
            raise
        except PrehrajtoError as e:
            if not streams:
                raise
            message = message or str(e)
        for stream in streams:
            stream["subtitles"] = dict(subtitles)
        if not streams:
            if login_error:
                raise login_error
            raise VideoUnavailableError("Video nemá žádný přehratelný soubor")
        return streams, message

    def _original(self, ref):
        vid, slug, h = split_ref(ref)
        if not vid and self.has_credentials:
            try:
                vid, _sources, _tracks = self._streams_from_page(ref)
            except PrehrajtoError:
                vid = ""
        api_error = None
        if vid:
            try:
                data = (self.api("videos/%s/download" % vid) or {}).get("data") or {}
                if data.get("download_link"):
                    return data["download_link"]
            except PrehrajtoError as e:
                if isinstance(e, (LoginError, TemporaryError)):
                    api_error = e
                self.log("prehrajto: API download selhal: %s" % e)
        _, _, location, status = self._request(video_page_url(ref) + "?do=download", follow=False)
        if location and "prehraj.to/" not in location.split("?")[0][:30]:
            return urllib.parse.urljoin(BASE, location)
        if api_error:
            raise api_error
        return ""

    def resolve(self, url):
        self.fallback_height = None
        if url.startswith("orig:"):
            ref = url[5:]
            try:
                link = self._original(ref)
                original_error = None
            except PrehrajtoError as e:
                self.log("prehrajto: originál není dostupný: %s" % e)
                original_error = e
                link = ""
            if link:
                return link
            # bez premia originál nejde – nejlepší překódovaná verze
            _video_id, sources, _tracks = self._streams_from_page(ref)
            if sources:
                _, source, self.fallback_height = sources[0]
                return source
            if original_error:
                raise original_error
            raise PrehrajtoError("Originální soubor vyžaduje Premium a jiná verze není k dispozici")
        if not url.startswith(("http://", "https://")):
            raise PrehrajtoError("neplatná adresa streamu")
        return url
