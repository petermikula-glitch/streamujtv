# -*- coding: utf-8 -*-
"""Pomocné funkce pro Kodi (nastavení, překlady, logování, dialogy)."""
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
ICON = os.path.join(ADDON_PATH, "icon.png")
FANART = os.path.join(ADDON_PATH, "fanart.jpg")
KODI_MAJOR = int((xbmc.getInfoLabel("System.BuildVersion") or "19").split(".")[0] or 19)


def ensure_profile():
    if not xbmcvfs.exists(PROFILE):
        xbmcvfs.mkdirs(PROFILE)
    return PROFILE


def profile_path(name):
    return os.path.join(ensure_profile(), name)


def _(string_id):
    return ADDON.getLocalizedString(string_id)


def setting(key):
    return ADDON.getSetting(key)


def setting_bool(key):
    return ADDON.getSetting(key).lower() == "true"


def setting_int(key, default=0):
    try:
        return int(ADDON.getSetting(key))
    except (TypeError, ValueError):
        return default


def set_setting(key, value):
    ADDON.setSetting(key, str(value))


def log(msg, level=xbmc.LOGDEBUG):
    xbmc.log("[%s] %s" % (ADDON_ID, msg), level)


def log_info(msg):
    log(msg, xbmc.LOGINFO)


def log_error(msg):
    log(msg, xbmc.LOGERROR)


def notify(message, heading=None, icon=ICON, time_ms=5000):
    xbmcgui.Dialog().notification(heading or ADDON_NAME, message, icon, time_ms)


def ok(message, heading=None):
    xbmcgui.Dialog().ok(heading or ADDON_NAME, message)


def yesno(message, heading=None):
    return xbmcgui.Dialog().yesno(heading or ADDON_NAME, message)


def keyboard(heading, default="", hidden=False):
    kb = xbmc.Keyboard(default, heading, hidden)
    kb.doModal()
    if kb.isConfirmed():
        return kb.getText().strip()
    return None


def number(heading, default=""):
    value = xbmcgui.Dialog().numeric(0, heading, str(default or ""))
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def select(heading, options, preselect=-1):
    return xbmcgui.Dialog().select(heading, options, preselect=preselect)


def text_viewer(heading, text):
    xbmcgui.Dialog().textviewer(heading, text)


class Progress(object):
    """Progress dialog, který jde vypnout (služba na pozadí)."""

    def __init__(self, heading, background=False):
        self.background = background
        self.dialog = xbmcgui.DialogProgressBG() if background else xbmcgui.DialogProgress()
        self.dialog.create(heading or ADDON_NAME, "")

    def update(self, percent, message=""):
        percent = max(0, min(100, int(percent)))
        if self.background:
            self.dialog.update(percent, message=message)
        else:
            self.dialog.update(percent, message)

    def cancelled(self):
        if self.background:
            return xbmc.Monitor().abortRequested()
        return self.dialog.iscanceled()

    def close(self):
        self.dialog.close()
