# -*- coding: utf-8 -*-
"""Služba na pozadí:

* automatická synchronizace katalogu s účtem,
* zapisování, kde se ve videu skončilo (seznam Rozkoukané, pokračování ve sledování).
"""
import json
import os
import sys
import threading
import time
from hashlib import sha256

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402
import xbmcgui  # noqa: E402

import kodiutils  # noqa: E402

INCOMPLETE_RETRY_DELAY = 3600
STARTUP_DELAY = 60      # s po startu Kodi, než se poprvé zkusí synchronizace
TICK = 5                # s – jak často se zapisuje pozice přehrávání
SYNC_CHECK_EVERY = 300  # s
WATCHED_SHARE = 0.92    # od tohoto podílu délky se video počítá jako dokoukané (jako v katalogu)
PLAYING_PROPERTY = kodiutils.ADDON_ID + ".playing"


def open_library():
    import db
    import provider
    return db.Library(kodiutils.profile_path("catalog.db"), video_key=provider.video_key,
                      prefer_ref=provider.prefer_ref)


def take_pending_playback():
    window = xbmcgui.Window(10000)
    raw = window.getProperty(PLAYING_PROPERTY)
    window.clearProperty(PLAYING_PROPERTY)
    if raw:
        try:
            pending = json.loads(raw)
            if pending["expires"] >= time.monotonic() and isinstance(pending["vid"], str):
                return pending
        except (ValueError, KeyError, TypeError):
            pass
    return None


class PlaybackTracker(xbmc.Player):
    """Sleduje přehrávání videí spuštěných tímto doplňkem a ukládá pozici."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self.vid = None
        self.replacement = False
        self.next_offer = None
        self.kodi_updates = set()
        self.position = 0.0
        self.total = 0.0
        self.path_hash = None

    def _playing_hash(self):
        try:
            path = self.getPlayingFile().split("|", 1)[0]
        except (RuntimeError, AttributeError):  # Kodi přehrávání mezitím ukončilo
            return None
        return sha256(path.encode("utf-8")).hexdigest()

    def onPlayBackStarted(self):
        # Kodi otevírá další video, aniž by ohlásilo zastavení předchozího: uzavřít ho
        # s poslední známou pozicí dřív, než přehrávač začne hlásit čas nového videa.
        self.finish()

    def onAVStarted(self):
        self.next_offer = None
        pending = take_pending_playback()
        self.finish()
        if not pending:
            return
        if self._playing_hash() != pending["path_hash"]:
            return
        self.path_hash = pending["path_hash"]
        self.vid = pending["vid"]
        self.replacement = pending.get("replacement", False)
        self.position = 0.0
        self.total = 0.0
        self.update()
        self.save(started=True)

    def update(self):
        if not self.vid:
            return
        try:
            if self.isPlayingVideo():
                if self.path_hash and self._playing_hash() != self.path_hash:
                    return  # přehrávač už hlásí čas jiného videa
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
        """Uloží poslední pozici. Vrací True, když video opravdu doběhlo do konce."""
        completed = False
        if self.vid:
            # Kodi hlásí „konec" i při výpadku streamu uprostřed videa – za dokoukané
            # se počítá jen konec poblíž skutečné délky.
            if ended and self.total and self.total - self.position <= max(3 * TICK, self.total * (1 - WATCHED_SHARE)):
                self.position = self.total
                completed = True
            self.save()
            if kodiutils.setting_bool("library_share_history"):
                self.kodi_updates.add(self.vid)
        self.vid = None
        self.path_hash = None
        return completed

    def publish_kodi_updates(self):
        if self.kodi_updates and not self.vid:
            updates, self.kodi_updates = self.kodi_updates, set()
            import plugin
            plugin.publish_kodi_history(updates)

    def onPlayBackStopped(self):
        self.finish()

    def onPlayBackEnded(self):
        vid = self.vid
        completed = self.finish(ended=True)
        if vid and completed and any(kodiutils.setting_bool(key)
                                     for key in ("offer_next_episode", "offer_next_movie")):
            self.next_offer = vid

    def offer_next_if_due(self):
        vid, self.next_offer = self.next_offer, None
        if not vid or self.isPlaying():
            return
        import plugin
        try:
            if kodiutils.setting_bool("offer_next_episode"):
                plugin.offer_next_episode(vid, self)
            if kodiutils.setting_bool("offer_next_movie"):
                plugin.offer_next_movie(vid, self)
        except Exception as e:  # Služba pokračuje i při chybě při čtení katalogu nebo otevření dialogu.
            kodiutils.log_error("nabídku dalšího dílu nelze otevřít: %s" % e)

    def onPlayBackError(self):
        pending = take_pending_playback()
        vid, replacement = self.vid, self.replacement
        self.finish()
        if pending or vid:
            import plugin
            try:
                if pending:
                    plugin.retry_failed_playback(pending)
                else:
                    plugin.playback_failed(vid, kodiutils._(30405), replacement)
            except Exception as e:  # Kodi musí službu nechat běžet i po chybě mezipaměti
                kodiutils.log_error("opakování přehrávání selhalo: %s" % e)
                kodiutils.notify(kodiutils._(30405), time_ms=8000)


class LibraryMonitor(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        self.marks = {}

    def onNotification(self, sender, method, data):
        if method != "VideoLibrary.OnUpdate" or not kodiutils.setting_bool("library_share_history"):
            return
        try:
            event = json.loads(data)
            item = event.get("item") or event
            count = event.get("playcount")
            if item.get("type") in ("movie", "episode") and isinstance(count, int) and count >= 0:
                self.marks[(item["type"], item["id"])] = count > 0
        except (ValueError, TypeError, AttributeError, KeyError):
            pass

    def import_marks(self):
        if self.marks:
            marks, self.marks = self.marks, {}
            import plugin
            plugin.import_kodi_marks(marks)


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


def run_sync():
    """Jedna automatická synchronizace (běží ve vlastním vlákně)."""
    try:
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


class SyncWorker(object):
    """Synchronizace mimo hlavní smyčku služby.

    Může trvat minuty (stahování stránek, metadata). Kdyby běžela v hlavní smyčce,
    Kodi by po tu dobu nedoručilo události přehrávače a právě spuštěné video by se
    přestalo sledovat (pozice, zhlédnutí, nabídka dalšího dílu)."""

    def __init__(self):
        self.thread = None

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start_if_due(self, player):
        if self.running():
            return False
        try:
            if not sync_due() or player.isPlayingVideo():
                return False
        except Exception as e:  # noqa: BLE001 – služba nesmí spadnout
            kodiutils.log_error("automatickou synchronizaci nelze naplánovat: %s" % e)
            return False
        self.thread = threading.Thread(target=run_sync, name="prehrajto-sync")
        self.thread.daemon = True  # při ukončení Kodi se na rozběhnutou synchronizaci nečeká
        self.thread.start()
        return True


def main():
    monitor = LibraryMonitor()
    player = PlaybackTracker()
    sync = SyncWorker()
    started = time.time()
    last_check = 0.0
    last_save = 0.0
    while not monitor.abortRequested():
        now = time.time()
        if not player.vid:
            monitor.import_marks()
            player.publish_kodi_updates()
        player.offer_next_if_due()
        if player.vid:
            player.update()
            if now - last_save >= 30:  # průběžně, kdyby Kodi spadlo
                player.save()
                last_save = now
        elif now - started >= STARTUP_DELAY and now - last_check >= SYNC_CHECK_EVERY:
            last_check = now
            sync.start_if_due(player)
        if monitor.waitForAbort(TICK):
            break
    player.finish()
    player.publish_kodi_updates()


if __name__ == "__main__":
    main()
