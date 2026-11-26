# -*- coding: utf-8 -*-
"""Poskytovatel videí: Přehraj.to.

Sdílený kód (catalog.py, plugin.py) mluví se službou jen přes tento modul.
"""
import prehrajto
from prehrajto import (CHECK_BAD_LOGIN, CHECK_NO_PREMIUM, CHECK_PREMIUM, LoginError,
                       QUALITY_LABEL, QUALITY_ORDER, RateLimited, TemporaryError, USER_AGENT,
                       VideoUnavailableError, quality_token, thumb_url, video_page_url)

__all__ = ["CHECK_BAD_LOGIN", "CHECK_NO_PREMIUM", "CHECK_PREMIUM", "LoginError", "QUALITY_LABEL",
           "QUALITY_ORDER", "USER_AGENT", "thumb_url", "video_page_url", "NAME", "ProviderError",
           "make_client", "find_video_id", "premium_text", "incomplete_id", "video_key",
           "prefer_ref", "diagnostics", "LINK_TTL", "TemporaryError", "VideoUnavailableError", "quality_token",
           "offered"]

NAME = "Přehraj.to"
ProviderError = prehrajto.PrehrajtoError


def make_client(username, password, location="", cookie_file=None, log=None):
    return prehrajto.PrehrajtoClient(username, password, cookie_file=cookie_file, log=log)


def find_video_id(text):
    """Ref videa z řádku textu (odkaz https://prehraj.to/<název>/<hash>), jinak None."""
    vid = prehrajto.extract_video_id(text)
    if vid:
        return vid
    for token in (text or "").replace("|", " ").replace(";", " ").replace("\t", " ").split():
        vid = prehrajto.extract_video_id(token)
        if vid:
            return vid
    return None


def premium_text(days):
    return str(days) if days else "?"


#: podepsaný odkaz na CDN platí zhruba den – pamatuje se 6 hodin
LINK_TTL = 6 * 3600


def incomplete_id(vid):
    """Ref bez čísla videa (z ručně vloženého odkazu) – číslo potřebuje JSON API."""
    return (vid or "").startswith(":")


def video_key(vid):
    """Stejné video = stejný hash, ať je ref s číslem videa, nebo bez něj."""
    return (vid or "").rsplit(":", 1)[-1]


def prefer_ref(new, old):
    return old if not incomplete_id(old) else new


def diagnostics(client):
    """Stránky účtu pro ladění: {název souboru: obsah}. Citlivé údaje maže plugin."""
    client.login()
    out = {}
    first_video = None
    for name, path in (("nahrana_videa.html", prehrajto.UPLOADS_PATH), ("profil.html", "/profil")):
        try:
            page = client._get(path)[0]
            out[name] = page
            if first_video is None:
                items = prehrajto.parse_video_list(page)
                first_video = items[0]["id"] if items else None
        except prehrajto.PrehrajtoError as e:
            out[name] = "CHYBA: %s" % e
    if first_video:
        try:
            out["stranka_videa.html"] = client._get(prehrajto.video_page_url(first_video))[0]
        except prehrajto.PrehrajtoError as e:
            out["stranka_videa.html"] = "CHYBA: %s" % e
    return out


def offered(client, max_pages=prehrajto.OFFER_PAGES):
    """Videa, která web sám nabízí na úvodní stránce a ve výpisech, na které z ní odkazuje."""
    return client.offered(max_pages)


def search_catalog(client, query, page, cursor):
    return client.search_page(query, page, cursor)
