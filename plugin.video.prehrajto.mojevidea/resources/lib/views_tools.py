# -*- coding: utf-8 -*-
"""Nástroje: synchronizace, metadata, import odkazů, stav účtu, záloha a diagnostika."""
import json
import sqlite3
import time

import xbmc
import xbmcgui
import xbmcvfs

from kodiutils import (ADDON, ADDON_ID, Progress, keyboard, log, log_error, log_info, notify, ok,
                       set_setting, setting, setting_bool, text_viewer)
import ui
from ui import (_, add_action, add_dir, end, open_library, _PLAYBACK)

views_discover = ui.LazyModule("views_discover")
views_kodi = ui.LazyModule("views_kodi")
catalog = ui.LazyModule("catalog")
provider = ui.LazyModule("provider")


def diagnostics(params):
    """Uloží stránky účtu (bez přihlašovacích údajů) pro ladění parseru."""
    if not _require_credentials():
        return
    folder = xbmcgui.Dialog().browse(3, _(30235), "files")
    if not folder:
        return
    try:
        pages = provider.diagnostics(ui.make_client())
    except provider.ProviderError as e:
        ok(_(30328) % e)
        return
    secrets = [setting("username"), setting("password")]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sep = "\\" if "\\" in folder and "/" not in folder else "/"
    base = folder.rstrip("/\\") + sep + "diagnostika-%s-%s" % (ADDON_ID, stamp)
    xbmcvfs.mkdirs(base)
    for name, content in pages.items():
        f = xbmcvfs.File(base + sep + name, "w")
        try:
            f.write(catalog.sanitize(content, secrets))
        finally:
            f.close()
    ok(_(30236) % base)


def tools(params):
    with open_library() as lib:
        last = lib.get_kv("last_sync")
    last_text = _sync_time(int(last)) if last else _(30232)
    add_action(_(30231) % last_text, "stats", "DefaultIconInfo.png")
    add_dir(_(30105), "unmatched", "DefaultVideoDeleted.png")
    add_action(_(30445), "batch_episodes", "DefaultTVShows.png")
    add_action(_(30200), "sync", "DefaultAddonService.png")
    add_action(_(30203), "enrich", "DefaultAddonInfoProvider.png")
    add_action(_(30202), "add_link", "DefaultAddSource.png")
    add_action(_(30201), "import_file", "DefaultFile.png")
    add_dir(_(30470), "kodi_setup", "DefaultAddonLibrary.png")
    add_action(_(30206), "export_library", "DefaultAddonLibrary.png")
    add_action(_(30205), "account_status", "DefaultUser.png")
    add_action(_(30204), "refresh_all", "DefaultAddonInfoProvider.png")
    add_action(_(30237), "history_clear", "DefaultIconError.png")
    add_action(_(30234), "diagnostics", "DefaultIconInfo.png")
    add_action(_(30345), "backup_catalog", "DefaultAddonLibrary.png")
    add_action(_(30346), "restore_catalog", "DefaultAddonLibrary.png")
    add_action(_(30207), "clear_library", "DefaultIconError.png")
    add_action(_(30208), "settings", "DefaultAddonProgram.png")
    end(cache=False)


def _require_credentials():
    if not setting("username") or not setting("password"):
        ok(_(30222))
        ADDON.openSettings()
        return False
    return True


def _sync_time(timestamp):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


def run_sync(background=False):
    """Synchronizace účtu + volitelně metadata. Použitelné i ze služby."""
    views_discover._refresh_watchlist_availability(after_sync=False)
    progress = Progress(_(30210), background=background)
    try:
        client = ui.make_client()

        def on_progress(stage, pages, count):
            progress.update(min(95, pages * 5), _(30211) % (pages, count))
            return progress.cancelled()

        with open_library() as lib:
            stats = catalog.sync_account(
                client, lib, include_favorites=setting_bool("sync_favorites"),
                include_playlists=setting_bool("sync_playlists"),
                remove_missing=setting_bool("remove_missing"), progress=on_progress, log=log)
    finally:
        progress.close()
    log_info("sync: %s" % stats)
    if stats.get("superseded"):
        return stats
    msg = _(30212) % (stats["total"], stats["new"], stats["removed"])
    if stats["incomplete"] and not stats["cancelled"]:
        notify(_(30343) % stats["total"], time_ms=8000)
    elif not stats["cancelled"] and (not background or stats["new"] or stats["removed"]):
        notify("%s · %s" % (_sync_time(time.time()), msg), time_ms=7000)
    if setting_bool("auto_meta") and not stats["cancelled"]:
        run_enrich(statuses=("new",), background=background, quiet=True)
    if not stats["incomplete"] and not stats["cancelled"]:
        fresh = views_discover._refresh_watchlist_availability(after_sync=True)
        if fresh:
            notify(_(30515) % fresh, time_ms=8000)
        folder = setting("library_path")
        if folder and setting_bool("library_auto_export"):
            views_kodi._export_to_kodi(folder, automatic=True)
    return stats


def sync(params):
    if not _require_credentials():
        return
    try:
        run_sync()
    except provider.LoginError as e:
        ok(str(e))
    except provider.ProviderError as e:
        log_error("sync: %s" % e)
        ok(_(30328) % e)
    xbmc.executebuiltin("Container.Refresh")


def run_enrich(statuses=("new",), keys=None, background=False, quiet=False):
    use_csfd = setting_bool("use_csfd")
    tmdb = ui.make_tmdb(cached=False)  # doplnění a obnova metadat vždy čtou aktuální data
    with open_library() as lib:
        if keys:
            metas = [m for m in (lib.get_meta(k) for k in keys) if m]
        else:
            metas = lib.meta_to_fetch(statuses)
        if not metas:
            if not quiet:
                notify(_(30121))
            return None
        progress = Progress(_(30213), background=background)
        try:
            def on_progress(idx, total, title):
                progress.update(idx * 100 // max(1, total), _(30214) % (idx + 1, total, title))
                return progress.cancelled()
            stats = catalog.enrich(lib, metas, use_csfd=use_csfd, tmdb=tmdb, progress=on_progress, log=log)
        finally:
            progress.close()
    log_info("metadata: %s" % stats)
    if stats["csfd_blocked"]:
        if not tmdb.enabled and not background:
            ok(_(30216))
        else:
            log_info("ČSFD blokuje dotazy, použito TMDB")
    if stats["last_error"] and not stats["ok"] and not stats["csfd_blocked"] and not background:
        notify(_(30328) % stats["last_error"])
    if not (background and quiet) or stats["ok"]:
        notify(_(30215) % (stats["ok"], stats["csfd"], stats["tmdb"], stats["notfound"], stats["errors"]),
               time_ms=7000)
    return stats


def enrich(params):
    run_enrich(statuses=("new", "notfound", "error", "blocked"))
    xbmc.executebuiltin("Container.Refresh")


def refresh_all(params):
    if not ui.yesno(_(30229)):
        return
    with open_library() as lib:
        keys = [m["key"] for m in lib.meta_to_fetch(("new", "ok", "notfound", "error", "blocked"))]
    run_enrich(keys=keys)
    xbmc.executebuiltin("Container.Refresh")


def add_link(params):
    text = keyboard(_(30217))
    if not text:
        return
    links = catalog.parse_link_lines(text)
    if not links:
        ok(_(30218))
        return
    _import(links)


def import_file(params):
    path = xbmcgui.Dialog().browse(1, _(30220), "files", ".txt|.csv|.m3u|.list")
    if not path:
        return
    f = xbmcvfs.File(path)
    try:
        content = f.read()
    finally:
        f.close()
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    links = catalog.parse_link_lines(content)
    if not links:
        ok(_(30221))
        return
    _import(links)


def _import(links):
    progress = Progress(_(30201))
    try:
        def on_progress(idx, total, title):
            progress.update(idx * 100 // max(1, total), title)
            return progress.cancelled()
        with open_library() as lib:
            stats = catalog.import_links(ui.make_client(), lib, links, progress=on_progress, log=log)
    finally:
        progress.close()
    notify(_(30219) % (stats["new"], stats["existing"]))
    if stats["new"] and setting_bool("auto_meta"):
        run_enrich(statuses=("new",), quiet=True)
    xbmc.executebuiltin("Container.Refresh")


def account_status(params):
    if not _require_credentials():
        return
    try:
        result, expiration = ui.make_client().check_user()
    except provider.ProviderError as e:
        ok(_(30328) % e)
        return
    if result == provider.CHECK_PREMIUM:
        set_setting("premium_until", provider.premium_text(expiration))
        ok(_(30223) % provider.premium_text(expiration))
    elif result == provider.CHECK_NO_PREMIUM:
        set_setting("premium_until", "-")
        ok(_(30224))
    else:
        set_setting("premium_until", "")
        ok(_(30225))


def stats(params):
    with open_library() as lib:
        s = lib.stats()
        last = lib.get_kv("last_sync")
    last_text = _sync_time(int(last)) if last else _(30232)
    text_viewer(_(30209), (_(30230) % (s["videos"], s["movies"], s["shows"], s["with_rating"],
                                       s["unmatched"])) + "\n\n" + _(30231) % last_text)


def clear_library(params):
    if ui.yesno(_(30226)):
        with open_library() as lib:
            lib.clear()
        xbmc.executebuiltin("Container.Refresh")


def backup_catalog(params):
    folder = xbmcgui.Dialog().browse(3, _(30345), "files")
    if not folder:
        return
    name = "%s-catalog-%s.json" % (ADDON_ID, time.strftime("%Y%m%d-%H%M%S"))
    destination = folder.rstrip("/\\") + "/" + name
    try:
        with open_library() as lib:
            data = json.dumps(lib.export_backup(ADDON_ID), ensure_ascii=False, allow_nan=False)
        file = xbmcvfs.File(destination, "w")
        try:
            if not file.write(data):
                raise OSError("Kodi could not write the backup")
        finally:
            file.close()
    except (OSError, ValueError, sqlite3.Error) as e:
        ok(_(30328) % e)
        return
    notify(_(30348) % destination, time_ms=8000)


def restore_catalog(params):
    path = xbmcgui.Dialog().browse(1, _(30346), "files", ".json")
    if not path or not ui.yesno(_(30347)):
        return
    try:
        file = xbmcvfs.File(path)
        try:
            data = json.loads(file.read())
        finally:
            file.close()
        with open_library() as lib:
            lib.restore_backup(data, ADDON_ID)
    except (OSError, ValueError, OverflowError, sqlite3.Error) as e:
        ok(_(30328) % e)
        return
    _PLAYBACK.clear()
    notify(_(30349))
    xbmc.executebuiltin("Container.Refresh")


def open_settings(params):
    ADDON.openSettings()
