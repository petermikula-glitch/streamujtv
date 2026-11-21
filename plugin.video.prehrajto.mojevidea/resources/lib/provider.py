# -*- coding: utf-8 -*-
"""Poskytovatel videí: Přehraj.to.

Sdílený kód (catalog.py, plugin.py) mluví se službou jen přes tento modul.
"""
import prehrajto
from prehrajto import (CHECK_BAD_LOGIN, CHECK_NO_PREMIUM, CHECK_PREMIUM, LoginError,
                       QUALITY_LABEL, QUALITY_ORDER, USER_AGENT, thumb_url, video_page_url)

__all__ = ["CHECK_BAD_LOGIN", "CHECK_NO_PREMIUM", "CHECK_PREMIUM", "LoginError", "QUALITY_LABEL",
           "QUALITY_ORDER", "USER_AGENT", "thumb_url", "video_page_url", "NAME", "ProviderError",
           "make_client", "find_video_id", "premium_text"]

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
    return "%s dní" % days if days else "?"
