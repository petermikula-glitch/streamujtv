# -*- coding: utf-8 -*-
"""Propojení s knihovnou Kodi (export, zdroje, sdílená historie)."""
import sqlite3
import time

import xbmc
import xbmcgui

from kodiutils import (ADDON, ADDON_ID, log, log_error, notify, ok, profile_path, set_setting, setting, setting_bool,
                       text_viewer)
import ui
from ui import (_, add_action, end, open_library, _PLAYBACK)

kodi_library = ui.LazyModule("kodi_library")
library_export = ui.LazyModule("library_export")


def _export_to_kodi(folder, automatic=False, prompt_scan=True):
    state_file = profile_path("library-export.db")
    try:
        with open_library() as lib:
            result = library_export.export(lib, folder, "plugin://%s/" % ADDON_ID, state_file)
        if result.failed:
            notify(_(30461) % result.failed, time_ms=8000)
        if result.removed:
            try:
                kodi_library.remove_items(result.removed)
            except kodi_library.KodiLibraryError:
                log_error("položky smazaných souborů se z knihovny Kodi nepodařilo odebrat")
        if result.protected and not automatic:
            notify(_(30462) % result.protected, time_ms=8000)
        if not automatic:
            notify(_(30227) % (result.movies, result.shows, folder), time_ms=8000)
        scan = (result.scan_pending and setting_bool("library_auto_scan") if automatic
                else prompt_scan and not result.failed and ui.yesno(_(30233)))
        if not result.failed and scan:
            xbmc.executebuiltin("UpdateLibrary(video)")
            library_export.mark_scanned(state_file, result)
        return result
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        log_error("export do Kodi selhal: %s" % type(error).__name__)
        notify(_(30463), time_ms=8000)


def export_library(params):
    folder = setting("library_path")
    if not folder:
        ok(_(30228))
        ADDON.openSettings()
        return
    _export_to_kodi(folder)


def _kodi_history_parameters():
    folder = setting("library_path")
    if folder and setting_bool("library_share_history"):
        return folder, profile_path("library-export.db"), ADDON_ID
    return None


def _import_kodi_history(force=False):
    args = _kodi_history_parameters()
    if not args:
        return None
    try:
        with open_library() as lib:
            now = int(time.time())
            if force or now - int(lib.get_kv("kodi:last_import", 0)) >= 30:
                changed = kodi_library.import_history(lib, *args)
                lib.set_kv("kodi:last_import", now)
                _PLAYBACK.clear()
                return changed
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: history import failed (%s)" % type(error).__name__)
    return None


def publish_kodi_history(vids, mark=None):
    args = _kodi_history_parameters()
    if not args:
        return
    try:
        with open_library() as lib:
            kodi_library.publish_history(lib, *args, vids, mark=mark)
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: history update failed (%s)" % type(error).__name__)


def import_kodi_marks(marks):
    args = _kodi_history_parameters()
    if not args:
        return
    try:
        with open_library() as lib:
            for (kind, item_id), watched in marks.items():
                kodi_library.accept_kodi_mark(lib, *args, kind, item_id, watched)
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError) as error:
        log("kodi: watched state update failed (%s)" % type(error).__name__)


def kodi_setup(params):
    folder = setting("library_path")
    add_action(_(30471), "kodi_choose_folder", "DefaultFolder.png")
    if folder:
        add_action(_(30472), "kodi_instructions", "DefaultAddonLibrary.png")
        add_action(_(30473), "kodi_sources", "DefaultFolder.png")
        add_action(_(30474), "kodi_scan", "DefaultRecentlyAddedMovies.png")
        add_action(_(30475), "kodi_status", "DefaultIconInfo.png")
        if setting_bool("library_share_history"):
            add_action(_(30476), "kodi_import", "DefaultRecentlyAddedEpisodes.png")
        else:
            add_action(_(30477), "kodi_enable_history", "DefaultInProgressShows.png")
    end(cache=False)


def kodi_choose_folder(params):
    current = setting("library_path")
    folder = xbmcgui.Dialog().browse(0, _(30471), "files", defaultt=current)
    if not folder:
        return
    set_setting("library_path", folder)
    result = _export_to_kodi(folder, prompt_scan=False)
    if result and not result.failed:
        kodi_instructions(params)
    xbmc.executebuiltin("Container.Refresh")


def kodi_instructions(params):
    folder = setting("library_path")
    if folder:
        root = library_export.export_folder(folder)
        text_viewer(_(30470), _(30478) % (library_export._join(root, "Filmy"),
                                         library_export._join(root, "Serialy")))


def kodi_sources(params):
    xbmc.executebuiltin("ActivateWindow(Videos,Files,return)")


def kodi_scan(params):
    folder = setting("library_path")
    if not folder:
        notify(_(30228))
        return
    result = _export_to_kodi(folder, prompt_scan=False)
    if result and not result.failed:
        xbmc.executebuiltin("UpdateLibrary(video)")
        try:
            library_export.mark_scanned(profile_path("library-export.db"), result)
        except (OSError, sqlite3.Error):
            log("kodi: scan status could not be saved")


def kodi_status(params):
    folder = setting("library_path")
    if not folder:
        ok(_(30228))
        return
    state_file = profile_path("library-export.db")
    try:
        movies, shows = kodi_library.source_status(folder)
        indexed = list(kodi_library.indexed_links(folder, state_file, ADDON_ID))
        exported = len(library_export.exported_streams(state_file, folder))
    except (ValueError, OSError, sqlite3.Error, kodi_library.KodiLibraryError):
        ok(_(30479))
        return
    status = _(30480) % (_(30481 if movies else 30482), _(30481 if shows else 30482), len(indexed), exported)
    text_viewer(_(30475), status)


def kodi_import(params):
    changed = _import_kodi_history(force=True)
    notify(_(30479) if changed is None else _(30483) % changed)
    xbmc.executebuiltin("Container.Refresh")


def kodi_enable_history(params):
    if ui.yesno(_(30484)):
        folder = setting("library_path")
        result = _export_to_kodi(folder, automatic=True) if folder else None
        if result and not result.failed:
            set_setting("library_share_history", "true")
            kodi_import(params)
