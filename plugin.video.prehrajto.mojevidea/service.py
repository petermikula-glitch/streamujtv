# -*- coding: utf-8 -*-
"""Služba na pozadí:

* automatická synchronizace katalogu s účtem,
* zapisování, kde se ve videu skončilo (seznam Rozkoukané, pokračování ve sledování).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402
import xbmcgui  # noqa: E402

import kodiutils  # noqa: E402

INCOMPLETE_RETRY_DELAY = 3600
STARTUP_DELAY = 60      # s po startu Kodi, než se poprvé zkusí synchronizace
TICK = 5                # s – jak často se zapisuje pozice přehrávání
SYNC_CHECK_EVERY = 300  # s
PLAYING_PROPERTY = kodiutils.ADDON_ID + ".playing"


def open_library():
    import db
    import provider
    return db.Library(kodiutils.profile_path("catalog.db"), video_key=provider.video_key,
                      prefer_ref=provider.prefer_ref)


class PlaybackTracker(xbmc.Player):
    """Sleduje přehrávání videí spuštěných tímto doplňkem a ukládá pozici."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self.vid = None
        self.position = 0.0
        self.total = 0.0

    def onAVStarted(self):
        window = xbmcgui.Window(10000)
        vid = window.getProperty(PLAYING_PROPERTY)
        window.clearProperty(PLAYING_PROPERTY)
        self.vid = vid or None
        self.position = 0.0
        self.total = 0.0
        if self.vid:
            self.update()
            self.save(started=True)

    def update(self):
        if not self.vid:
            return
        try:
            if self.isPlayingVideo():
                self.position = self.getTime()
                self.total = self.getTotalTime() or self.total
        except RuntimeError:  # přehrávání právě skončilo
            pass

    def save(self, started=False):
        if not self.vid:
            return
        try:
            with open_library() as lib:
                lib.record_play(self.vid, self.position, self.total, started=started)
        except Exception as e:  # noqa: BLE001 – služba nesmí spadnout
            kodiutils.log_error("historii přehrávání nelze uložit: %s" % e)

    def finish(self, ended=False):
        if self.vid:
            if ended and self.total:
                self.position = self.total
            self.save()
        self.vid = None

    def onPlayBackStopped(self):
        self.finish()

    def onPlayBackEnded(self):
        self.finish(ended=True)

    def onPlayBackError(self):
        self.finish()


def sync_due():
    if not kodiutils.setting_bool("auto_sync"):
        return False
    if not kodiutils.setting("username") or not kodiutils.setting("password"):
        return False
    hours = max(1, kodiutils.setting_int("auto_sync_hours", 12))
    with open_library() as lib:
        last = int(lib.get_kv("last_sync", 0) or 0)
        retry_after = int(lib.get_kv("sync_retry_after", 0) or 0)
    now = time.time()
    return now >= retry_after and now - last >= hours * 3600


def try_sync(player):
    try:
        if sync_due() and not player.isPlayingVideo():
            import plugin
            kodiutils.log_info("automatická synchronizace")
            stats = plugin.run_sync(background=True)
            if stats["incomplete"] or stats["cancelled"]:
                with plugin.open_library() as lib:
                    lib.set_kv("sync_retry_after", int(time.time()) + INCOMPLETE_RETRY_DELAY)
    except Exception as e:  # noqa: BLE001 – služba nesmí spadnout
        kodiutils.log_error("automatická synchronizace selhala: %s" % e)
        # po chybě počkat před dalším pokusem
        try:
            with open_library() as lib:
                lib.set_kv("sync_retry_after", int(time.time()) + INCOMPLETE_RETRY_DELAY)
        except Exception:  # noqa: BLE001
            pass


def main():
    monitor = xbmc.Monitor()
    player = PlaybackTracker()
    started = time.time()
    last_check = 0.0
    last_save = 0.0
    while not monitor.abortRequested():
        now = time.time()
        if player.vid:
            player.update()
            if now - last_save >= 30:  # průběžně, kdyby Kodi spadlo
                player.save()
                last_save = now
        elif now - started >= STARTUP_DELAY and now - last_check >= SYNC_CHECK_EVERY:
            last_check = now
            try_sync(player)
        if monitor.waitForAbort(TICK):
            break
    player.finish()


if __name__ == "__main__":
    main()
