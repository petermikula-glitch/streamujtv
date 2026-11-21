# -*- coding: utf-8 -*-
"""Služba na pozadí: automatická synchronizace katalogu s účtem streamuj.tv."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402

import kodiutils  # noqa: E402

STARTUP_DELAY = 60      # s po startu Kodi
CHECK_EVERY = 300       # s


def due():
    if not kodiutils.setting_bool("auto_sync"):
        return False
    if not kodiutils.setting("username") or not kodiutils.setting("password"):
        return False
    hours = max(1, kodiutils.setting_int("auto_sync_hours", 12))
    import db
    with db.Library(kodiutils.profile_path("catalog.db")) as lib:
        last = int(lib.get_kv("last_sync", 0) or 0)
    return time.time() - last >= hours * 3600


def main():
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(STARTUP_DELAY):
        return
    while not monitor.abortRequested():
        try:
            if due() and not xbmc.Player().isPlayingVideo():
                import plugin
                kodiutils.log_info("automatická synchronizace")
                plugin.run_sync(background=True)
        except Exception as e:  # noqa: BLE001 – služba nesmí spadnout
            kodiutils.log_error("automatická synchronizace selhala: %s" % e)
            # nezkoušet hned znovu – posun času poslední synchronizace o hodinu
            try:
                import db
                with db.Library(kodiutils.profile_path("catalog.db")) as lib:
                    hours = max(1, kodiutils.setting_int("auto_sync_hours", 12))
                    lib.set_kv("last_sync", int(time.time()) - hours * 3600 + 3600)
            except Exception:  # noqa: BLE001
                pass
        if monitor.waitForAbort(CHECK_EVERY):
            break


if __name__ == "__main__":
    main()
