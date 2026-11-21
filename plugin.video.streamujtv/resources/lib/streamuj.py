# -*- coding: utf-8 -*-
"""Klient pro streamuj.tv.

Dvě části:

* ``json_api_player.php`` – oficiální JSON API přehrávače (ověřeno 2026-11):
  - ``action=check-user`` → ``result`` 0 = špatné jméno/heslo, 1 = bez premia,
    2 = platné premium (``expiration`` = konec předplatného),
  - ``action=get-video-links&link=<id>`` → ``URL[jazyk][kvalita]`` = mezilehlý odkaz
    (+ případně ``URL[jazyk]["subtitles"][jazyk]``). GET na mezilehlý odkaz vrátí
    v těle odpovědi finální adresu mp4.
* Webové rozhraní (ClipShare): přihlášení formulářem ``/login`` a výpis vlastních
  videí ``/my_videos`` (případně oblíbených a playlistů). API pro výpis vlastních
  videí streamuj.tv nezveřejňuje, takže se čte HTML stránky – parser je proto
  záměrně tolerantní (bere všechny odkazy ``/video/<id>``).

Modul nesmí importovat nic z Kodi, aby šel testovat mimo něj.
"""
import hashlib
import html
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://www.streamuj.tv/"
API_URL = BASE_URL + "json_api_player.php"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36 Kodi plugin.video.streamujtv")
TIMEOUT = 30

QUALITY_ORDER = ("UHD", "FHD", "HD", "SD", "KINORIP", "TVRIP")
QUALITY_LABEL = {"UHD": "2160p", "FHD": "1080p", "HD": "720p", "SD": "480p"}

# ID videa na streamuj.tv je 20 hex znaků (např. 0645c382f89879db00bc)
VIDEO_ID_RE = re.compile(r"(?:streamuj\.tv)?/video/([0-9a-f]{10,40})", re.I)
BARE_ID_RE = re.compile(r"^[0-9a-f]{10,40}$", re.I)

CHECK_BAD_LOGIN = 0
CHECK_NO_PREMIUM = 1
CHECK_PREMIUM = 2


class StreamujError(Exception):
    pass


class LoginError(StreamujError):
    pass


def password_md5(password):
    """API přehrávače bere md5(heslo) s parametrem passwordinmd5=1.
    32 hex znaků se bere jako už zahashované heslo."""
    password = (password or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", password):
        return password.lower()
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def password_md5md5(password):
    """Mezilehlé odkazy berou ``pass=uživatel:::md5(md5(heslo))``."""
    return hashlib.md5(password_md5(password).encode("ascii")).hexdigest()


def extract_video_id(text):
    """Z odkazu (nebo holého ID) vrátí ID videa, jinak None."""
    text = (text or "").strip()
    if not text:
        return None
    m = VIDEO_ID_RE.search(text)
    if m:
        return m.group(1).lower()
    if BARE_ID_RE.match(text):
        return text.lower()
    return None


def is_streamuj_url(url):
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and (host == "streamuj.tv" or host.endswith(".streamuj.tv"))


def mask_secrets(url):
    """Adresa bez hesla – do logu Kodi se nesmí dostat ani hash hesla."""
    return re.sub(r"((?:pass|password)=)[^&]+", r"\1***", str(url or ""))


def _clean_text(value):
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def strip_comments(page_html):
    """Zakomentované bloky (např. starý "Naposledy shlédnuté") nejsou obsah stránky."""
    return re.sub(r"<!--.*?-->", "", page_html or "", flags=re.S)


def _is_truncated(text):
    return text.endswith("...") or text.endswith("…")


# ---------------------------------------------------------------------------
# Parsování HTML výpisů
# ---------------------------------------------------------------------------

_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_ATTR_RE = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_IMG_RE = re.compile(r"<img\b([^>]*)>", re.I)
_DURATION_RE = re.compile(r'class="duration"[^>]*>\s*([0-9:]+)\s*<', re.I)


def _attrs(tag_inner):
    out = {}
    for m in _ATTR_RE.finditer(tag_inner or ""):
        out[m.group(1).lower()] = html.unescape(m.group(2) or m.group(3) or m.group(4) or "")
    return out


def parse_duration(text):
    """'1:02:03' / '53:34' → sekundy."""
    parts = [p for p in (text or "").strip().split(":") if p.isdigit()]
    if not parts:
        return 0
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def parse_video_list(page_html, base_url=BASE_URL):
    """Vytáhne videa ze stránky výpisu (my_videos, playlist, oblíbené, veřejný výpis).

    Vrací seznam dictů ``{"id", "title", "thumb", "duration"}`` v pořadí stránky,
    bez duplicit. Název se bere z atributu ``title`` (nezkrácený), jinak
    z textu odkazu nebo ``alt`` obrázku.
    """
    found = {}
    order = []
    page_html = strip_comments(page_html)
    for m in _ANCHOR_RE.finditer(page_html):
        attrs = _attrs(m.group(1))
        href = attrs.get("href", "")
        vid = None
        vm = VIDEO_ID_RE.search(href)
        if vm:
            vid = vm.group(1).lower()
        if not vid:
            continue
        inner = m.group(2)
        item = found.get(vid)
        if item is None:
            item = {"id": vid, "title": "", "thumb": "", "duration": 0}
            found[vid] = item
            order.append(vid)
        candidates = [attrs.get("title", ""), _clean_text(inner)]
        img = _IMG_RE.search(inner)
        if img:
            iattrs = _attrs(img.group(1))
            src = iattrs.get("src", "")
            if src and not item["thumb"] and "/images/" not in src:
                item["thumb"] = urllib.parse.urljoin(base_url, src)
            candidates.append(iattrs.get("alt", ""))
            candidates.append(iattrs.get("title", ""))
        for cand in candidates:
            cand = _clean_text(cand)
            if not cand or cand.lower() in ("play", "přehrát", "edit", "upravit", "smazat", "delete"):
                continue
            current = item["title"]
            # nezkrácený název má přednost před zkráceným ("Název...")
            if not current or (_is_truncated(current) and not _is_truncated(cand)) or \
                    (_is_truncated(current) == _is_truncated(cand) and len(cand) > len(current)):
                item["title"] = cand
        # délka bývá hned za odkazem v bloku položky
        if not item["duration"]:
            tail = page_html[m.end():m.end() + 600]
            dm = _DURATION_RE.search(tail)
            if dm:
                item["duration"] = parse_duration(dm.group(1))
    for vid in order:
        if not found[vid]["thumb"]:
            found[vid]["thumb"] = thumb_url(vid)
    return [found[v] for v in order]


def parse_pagination(page_html, current_url):
    """Najde odkazy na další stránky stejného výpisu (``/my_videos/2``, ``?page=2``)."""
    section = urllib.parse.urlsplit(current_url).path.strip("/").split("/")[0]
    pages = []
    for m in _ANCHOR_RE.finditer(strip_comments(page_html)):
        href = _attrs(m.group(1)).get("href", "")
        if not href:
            continue
        absolute = urllib.parse.urljoin(current_url, href)
        if not is_streamuj_url(absolute):
            continue
        p = urllib.parse.urlsplit(absolute)
        if section and ("/" + section) not in p.path:
            continue
        if re.search(r"/\d+/?$", p.path) or re.search(r"(?:^|&)(?:page|p|strana)=\d+", p.query):
            if absolute not in pages:
                pages.append(absolute)
    return pages


def parse_playlists(page_html, base_url=BASE_URL):
    """Z ``/my_playlists`` vytáhne odkazy na jednotlivé playlisty: [(název, url)]."""
    out = []
    seen = set()
    for m in _ANCHOR_RE.finditer(strip_comments(page_html)):
        attrs = _attrs(m.group(1))
        href = attrs.get("href", "")
        if "playlist" not in href.lower() or "my_playlists" in href.lower():
            continue
        if re.search(r"(delete|remove|edit|create|add)", href, re.I):
            continue
        url = urllib.parse.urljoin(base_url, href)
        name = attrs.get("title") or _clean_text(m.group(2))
        if not name or url in seen:
            continue
        seen.add(url)
        out.append((name, url))
    return out


def parse_video_page(page_html):
    """Údaje ze stránky videa: název, popis."""
    title = ""
    m = re.search(r"<title>(.*?)</title>", page_html or "", re.I | re.S)
    if m:
        title = _clean_text(m.group(1))
        title = re.sub(r"^Streamuj\.tv\s*-\s*", "", title)
    desc = ""
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page_html or "", re.I)
    if m:
        desc = _clean_text(m.group(1))
        if desc == "HD Video hosting":
            desc = ""
    return {"title": title, "description": desc}


def is_logged_in_page(page_html):
    text = (page_html or "").lower()
    if "logout" in text or "odhlásit" in text or "odhlasit" in text:
        return True
    return False


def thumb_url(video_id):
    return BASE_URL + "thumb/1/%s.jpg" % video_id


def video_page_url(video_id):
    return BASE_URL + "video/%s" % video_id


# ---------------------------------------------------------------------------
# Klient
# ---------------------------------------------------------------------------

class StreamujClient(object):
    def __init__(self, username="", password="", location="1", cookie_file=None, log=None,
                 opener=None):
        self.username = (username or "").strip()
        self.password = password or ""
        self.location = str(location or "1")
        self.cookie_file = cookie_file
        self.log = log or (lambda msg: None)
        if cookie_file:
            self.cookies = http.cookiejar.LWPCookieJar(cookie_file)
            try:
                self.cookies.load(ignore_discard=True)
            except Exception:  # soubor ještě neexistuje / je poškozený
                pass
        else:
            self.cookies = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies))
        self._logged_in = False

    @property
    def has_credentials(self):
        return bool(self.username and self.password)

    # --- HTTP -------------------------------------------------------------
    def _request(self, url, data=None, headers=None):
        hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "cs-CZ,cs;q=0.9"}
        if headers:
            hdrs.update(headers)
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, data=body, headers=hdrs)
        self.log("streamuj: %s %s" % ("POST" if body else "GET", mask_secrets(url)))
        try:
            resp = self.opener.open(req, timeout=TIMEOUT)
            try:
                raw = resp.read()
                final_url = resp.geturl()
            finally:
                resp.close()
        except urllib.error.HTTPError as e:
            raise StreamujError("HTTP %s: %s" % (e.code, mask_secrets(url)))
        except Exception as e:  # noqa: BLE001 – síťové chyby všeho druhu
            raise StreamujError("%s (%s)" % (e, mask_secrets(url)))
        return raw.decode("utf-8", "replace"), final_url

    def _save_cookies(self):
        if self.cookie_file and isinstance(self.cookies, http.cookiejar.FileCookieJar):
            try:
                self.cookies.save(ignore_discard=True)
            except Exception as e:  # noqa: BLE001
                self.log("streamuj: cookies nelze uložit: %s" % e)

    # --- JSON API ---------------------------------------------------------
    def api(self, action, **params):
        query = {"action": action}
        if self.has_credentials:
            query.update({"login": self.username, "password": password_md5(self.password),
                          "passwordinmd5": 1})
        query.update(params)
        text, _ = self._request(API_URL + "?" + urllib.parse.urlencode(query))
        try:
            data = json.loads(text)
        except ValueError:
            raise StreamujError("streamuj.tv vrátil neplatnou odpověď")
        if not isinstance(data, dict):
            raise StreamujError("streamuj.tv vrátil neočekávanou odpověď")
        return data

    def check_user(self):
        """Vrací (stav, expirace). stav = CHECK_* konstanta."""
        if not self.has_credentials:
            return CHECK_BAD_LOGIN, None
        data = self.api("check-user")
        try:
            result = int(data.get("result") or 0)
        except (TypeError, ValueError):
            result = 0
        return result, data.get("expiration") or None

    def get_streams(self, video_id):
        """Seznam dostupných streamů seřazený podle kvality.

        Každý stream: ``{"lang", "quality", "url", "subtitles": {jazyk: url}}``.
        """
        data = self.api("get-video-links", link=video_id, location=self.location)
        streams = []
        message = data.get("errormessage")
        urls = data.get("URL") or {}
        if not isinstance(urls, dict):
            urls = {}
        for lang, quals in urls.items():
            if not isinstance(quals, dict):
                continue
            subs = quals.get("subtitles") if isinstance(quals.get("subtitles"), dict) else {}
            subs = dict((k.upper(), v) for k, v in subs.items() if isinstance(v, str) and v)
            for quality, url in quals.items():
                if quality == "subtitles" or not isinstance(url, str) or not url:
                    continue
                streams.append({"lang": str(lang).upper(), "quality": str(quality).upper(),
                                "url": url, "subtitles": subs})
        streams.sort(key=lambda s: QUALITY_ORDER.index(s["quality"])
                     if s["quality"] in QUALITY_ORDER else len(QUALITY_ORDER))
        if not streams and message:
            raise StreamujError(str(message))
        return streams, (str(message) if message else "")

    def resolve(self, indirect_url):
        """Mezilehlý odkaz → finální adresa streamu."""
        if not is_streamuj_url(indirect_url):
            raise StreamujError("odkaz nevede na streamuj.tv")
        url = indirect_url
        if self.has_credentials and "pass=" not in url:
            url += ("&" if "?" in url else "?") + "pass=" + urllib.parse.quote(
                "%s:::%s" % (self.username, password_md5md5(self.password)), safe=":")
        if "location=" not in url:
            url += "&location=" + self.location
        body, final_url = self._request(url)
        body = body.strip()
        if body.startswith("http"):
            return body.split()[0]
        if final_url and final_url != url and re.search(r"\.(mp4|mkv|m3u8|avi)(\?|$)", final_url):
            return final_url
        raise StreamujError("streamuj.tv nevrátil adresu streamu")

    # --- web --------------------------------------------------------------
    def login(self, force=False):
        if self._logged_in and not force:
            return True
        if not self.has_credentials:
            raise LoginError("Není vyplněno jméno nebo heslo k streamuj.tv")
        if not force:
            # platná session z minula (cookie)?
            page, url = self._request(BASE_URL + "my_profile")
            if "signup" not in url and "/login" not in url and is_logged_in_page(page):
                self._logged_in = True
                return True
        page, url = self._request(BASE_URL + "login", data={
            "username": self.username,
            "password": self.password,
            "login_remember": "on",
            "action_login": "Přihlásit se",
        })
        if not is_logged_in_page(page):
            # některé verze po POSTu přesměrují na úvod – ověř profilem
            page, url = self._request(BASE_URL + "my_profile")
            if "signup" in url or "/login" in url or not is_logged_in_page(page):
                raise LoginError("Přihlášení k streamuj.tv se nezdařilo – zkontroluj jméno a heslo")
        self._logged_in = True
        self._save_cookies()
        return True

    def fetch_listing(self, start_url, max_pages=200, progress=None, delay=0.3):
        """Projde výpis včetně stránkování a vrátí všechna videa."""
        to_visit = [start_url]
        visited = set()
        videos = []
        seen = set()
        while to_visit and len(visited) < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)
            page, final_url = self._request(url)
            if "signup" in final_url or final_url.rstrip("/").endswith("/login"):
                raise LoginError("Stránka %s vyžaduje přihlášení" % start_url)
            new = 0
            for item in parse_video_list(page):
                if item["id"] not in seen:
                    seen.add(item["id"])
                    videos.append(item)
                    new += 1
            if progress and progress(len(visited), len(videos)):
                break  # zrušeno uživatelem
            if new == 0 and len(visited) > 1:
                continue  # stránka bez nových videí – dál z ní nejdeme
            for nxt in parse_pagination(page, final_url):
                if nxt not in visited and nxt not in to_visit:
                    to_visit.append(nxt)
            if delay:
                time.sleep(delay)
        return videos

    def my_videos(self, progress=None):
        self.login()
        return self.fetch_listing(BASE_URL + "my_videos", progress=progress)

    def my_favorites(self, progress=None):
        self.login()
        return self.fetch_listing(BASE_URL + "my_favorites", progress=progress)

    def my_playlists(self):
        self.login()
        page, _ = self._request(BASE_URL + "my_playlists")
        return parse_playlists(page)

    def video_info(self, video_id):
        page, _ = self._request(video_page_url(video_id))
        info = parse_video_page(page)
        info["id"] = video_id
        info["thumb"] = thumb_url(video_id)
        return info
