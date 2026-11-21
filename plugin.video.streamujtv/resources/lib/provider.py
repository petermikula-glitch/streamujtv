# -*- coding: utf-8 -*-
"""Poskytovatel videí: streamuj.tv.

Sdílený kód (catalog.py, plugin.py) mluví se službou jen přes tento modul,
takže stejné obrazovky a katalog fungují i v doplňku pro jinou službu.
"""
import streamuj
from streamuj import (CHECK_BAD_LOGIN, CHECK_NO_PREMIUM, CHECK_PREMIUM, LoginError,
                      QUALITY_LABEL, QUALITY_ORDER, USER_AGENT, thumb_url, video_page_url)

__all__ = ["CHECK_BAD_LOGIN", "CHECK_NO_PREMIUM", "CHECK_PREMIUM", "LoginError", "QUALITY_LABEL",
           "QUALITY_ORDER", "USER_AGENT", "thumb_url", "video_page_url", "NAME", "ProviderError",
           "make_client", "find_video_id", "premium_text", "incomplete_id", "video_key",
           "prefer_ref", "diagnostics", "LINK_TTL"]

NAME = "streamuj.tv"
ProviderError = streamuj.StreamujError


def make_client(username, password, location="1", cookie_file=None, log=None):
    return streamuj.StreamujClient(username, password, location=location or "1",
                                   cookie_file=cookie_file, log=log)


def find_video_id(text):
    """ID videa z řádku textu (odkaz nebo holé ID kdekoli v řádku), jinak None."""
    vid = streamuj.extract_video_id(text)
    if vid:
        return vid
    for token in (text or "").replace("|", " ").replace(";", " ").replace("\t", " ").split():
        if streamuj.BARE_ID_RE.match(token):
            return token.lower()
    return None


def premium_text(expiration):
    return expiration or "?"


#: jak dlouho si pamatovat adresu streamu (s); 0 = nepamatovat. Adresy streamuj.tv
#: obsahují časové razítko a jejich platnost není známá.
LINK_TTL = 0


def incomplete_id(vid):
    return False


def video_key(vid):
    return vid


def prefer_ref(new, old):
    return old


def diagnostics(client):
    """Stránky účtu pro ladění: {název souboru: obsah}. Citlivé údaje maže plugin."""
    client.login()
    out = {}
    for name, url in (("moje_videa.html", streamuj.BASE_URL + "my_videos"),
                      ("profil.html", streamuj.BASE_URL + "my_profile")):
        try:
            out[name] = client._request(url)[0]
        except streamuj.StreamujError as e:
            out[name] = "CHYBA: %s" % e
    return out
