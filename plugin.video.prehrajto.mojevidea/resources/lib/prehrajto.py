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
import http.cookiejar
import json
import re
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
PREMIUM_RE = re.compile(r"PREMIUM\s*(?:<[^>]*>\s*)*(\d+)\s*dn", re.S)
DAYS_RE = re.compile(r"Vyprší za\s*(?:<[^>]*>\s*)*(\d+)", re.S)


class PrehrajtoError(Exception):
    pass


class LoginError(PrehrajtoError):
    pass


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


def parse_sources(page):
    """Překódované verze ze stránky videa: [(kvalita, url)] od nejlepší."""
    found = {}
    for url, res in SOURCE_RE.findall(page or ""):
        q = _quality_from_res(res)
        found.setdefault(q, html.unescape(url))
    return sorted(found.items(), key=lambda kv: QUALITY_ORDER.index(kv[0]))


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
    return {"title": title, "thumb": thumb, "description": desc}


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

    @property
    def has_credentials(self):
        return bool(self.username and self.password)

    # --- HTTP -------------------------------------------------------------
    def _request(self, url, data=None, headers=None, follow=True):
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
            resp = opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if not follow and e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location") or ""
                e.close()
                return "", url, location, e.code
            if e.code == 429:
                raise PrehrajtoError("Přehraj.to dočasně omezuje dotazy (HTTP 429), zkus to za chvíli")
            raise PrehrajtoError("HTTP %s: %s" % (e.code, mask_secrets(url)))
        except Exception as e:  # noqa: BLE001
            raise PrehrajtoError("Přehraj.to je nedostupné: %s" % e)
        try:
            text = resp.read().decode("utf-8", "replace")
            final = resp.geturl()
            status = getattr(resp, "status", 200)
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
            try:
                self.cookies.save(ignore_discard=True)
            except Exception as e:  # noqa: BLE001
                self.log("prehrajto: cookies nelze uložit: %s" % e)

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
            raise PrehrajtoError("API HTTP %s" % e.code)
        except ValueError:
            raise PrehrajtoError("API vrátilo neplatnou odpověď")
        except Exception as e:  # noqa: BLE001
            raise PrehrajtoError("API je nedostupné: %s" % e)

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
        start_url = start_url if start_url.startswith("http") else BASE + start_url
        start_path = urllib.parse.urlsplit(start_url).path.rstrip("/")
        to_visit = [start_url]
        visited = set()
        videos = []
        seen = set()
        while to_visit and len(visited) < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)
            page, final = self._get(url)
            if not urllib.parse.urlsplit(final).path.startswith(start_path):
                if len(visited) == 1:
                    raise LoginError("Stránka %s vyžaduje přihlášení" % start_url)
                continue
            new = 0
            for item in parse_video_list(page):
                h = item["id"].rsplit(":", 1)[-1]
                if h not in seen:
                    seen.add(h)
                    videos.append(item)
                    new += 1
            if progress and progress(len(visited), len(videos)):
                break
            if new == 0 and len(visited) > 1:
                continue
            for nxt in parse_pagination(page, final):
                if nxt not in visited and nxt not in to_visit:
                    to_visit.append(nxt)
            if delay:
                time.sleep(delay)
        return videos

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
        info["id"] = ref
        return info

    # --- přehrání -----------------------------------------------------------
    def get_streams(self, ref):
        vid, slug, h = split_ref(ref)
        streams = []
        message = ""
        subtitles = {}
        if self.has_credentials:
            streams.append({"lang": "", "quality": "ORIG", "url": "orig:" + ref, "subtitles": {}})
            try:
                self.login()
            except LoginError as e:
                streams = []
                message = str(e)
        try:
            page, _ = self._get(video_page_url(ref))
            subtitles = parse_tracks(page)
            for quality, url in parse_sources(page):
                streams.append({"lang": "", "quality": quality, "url": url, "subtitles": {}})
        except PrehrajtoError as e:
            if not streams:
                raise
            message = message or str(e)
        for s in streams:
            s["subtitles"] = dict(subtitles)
        if not streams:
            raise PrehrajtoError(message or "Video nemá žádný přehratelný soubor")
        return streams, message

    def _original(self, ref):
        vid, slug, h = split_ref(ref)
        if vid:
            try:
                data = (self.api("videos/%s/download" % vid) or {}).get("data") or {}
                if data.get("download_link"):
                    return data["download_link"]
            except PrehrajtoError as e:
                self.log("prehrajto: API download selhal: %s" % e)
        _, _, location, status = self._request(video_page_url(ref) + "?do=download", follow=False)
        if location and "prehraj.to/" not in location.split("?")[0][:30]:
            return urllib.parse.urljoin(BASE, location)
        return ""

    def resolve(self, url):
        if url.startswith("orig:"):
            ref = url[5:]
            link = self._original(ref)
            if link:
                return link
            # bez premia originál nejde – nejlepší překódovaná verze
            page, _ = self._get(video_page_url(ref))
            sources = parse_sources(page)
            if sources:
                return sources[0][1]
            raise PrehrajtoError("Originální soubor vyžaduje Premium a jiná verze není k dispozici")
        if not url.startswith(("http://", "https://")):
            raise PrehrajtoError("neplatná adresa streamu")
        return url
