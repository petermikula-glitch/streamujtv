"""Stahování na disk (Windows): totéž, co na televizi dělá služba ``DownloadService``.

Frontu a evidenci vede logika (``download_next``, ``download_progress``, ``download_finish``,
``download_fail``); tady se jen přenášejí data. Rozpracovaný soubor má příponu ``.part`` a po
přerušení se navazuje (hlavička ``Range``). Kam se stahuje, říká nastavení (``download_dir``);
když není zvolené, stahuje se do ``data/Stažené`` vedle programu.
"""

import os
import threading
import time
import urllib.request

CHUNK = 256 * 1024
REPORT_EVERY = 1.0       # s mezi hlášeními průběhu (logika při nich řekne, jestli se stahování nezrušilo)
ATTEMPTS = 4


class Downloads(object):
    def __init__(self, call, default_dir):
        self.call = call
        self.default_dir = default_dir
        self.thread = None
        self.lock = threading.Lock()
        self.message = ""
        self.opener = urllib.request.build_opener()   # vlastní spojení, nezávislé na nastavení jinde v programu

    def folder(self):
        settings = self.call("settings_get", {}) or {}
        folder = settings.get("download_dir") or self.default_dir
        os.makedirs(folder, exist_ok=True)
        return folder

    def start(self):
        """Spustí stahování fronty, pokud už neběží."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.thread = threading.Thread(target=self._run, name="stahovani")
            self.thread.daemon = True
            self.thread.start()
            return True

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def _run(self):
        attempt, last = 0, ""
        while True:
            job = self.call("download_next", {"dir": self.folder(), "fresh": attempt > 0})
            if not job or not job.get("vid"):
                return
            if job["vid"] != last:
                attempt, last = 0, job["vid"]
            outcome = self._transfer(job)
            if outcome == "done":
                subtitles = self._subtitles(job)
                self.call("download_finish", {"vid": job["vid"], "size": os.path.getsize(job["path"]),
                                              "subtitles": subtitles})
                self.message = "Staženo: %s" % job.get("title", "")
                attempt = 0
            elif outcome == "cancelled":
                try:
                    os.remove(job["part"])
                except OSError:
                    pass
            else:
                attempt += 1
                if attempt >= ATTEMPTS:
                    self.call("download_fail", {"vid": job["vid"], "error": outcome})
                    attempt = 0
                else:
                    time.sleep(5 * attempt)

    def _transfer(self, job):
        part = job["part"]
        existing = os.path.getsize(part) if os.path.exists(part) else 0
        request = urllib.request.Request(job["url"], headers=dict(job.get("headers") or {}))
        if existing:
            request.add_header("Range", "bytes=%d-" % existing)
        try:
            with self.opener.open(request, timeout=30) as response:
                if existing and response.status != 206:
                    existing = 0          # server navázání neumí: od začátku
                length = int(response.headers.get("Content-Length") or 0)
                total = existing + length if length else int(job.get("total") or 0)
                done, reported = existing, 0.0
                with open(part, "ab" if existing else "wb") as output:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        output.write(block)
                        done += len(block)
                        if time.time() - reported >= REPORT_EVERY:
                            reported = time.time()
                            answer = self.call("download_progress", {"vid": job["vid"], "done": done, "total": total})
                            if (answer or {}).get("cancel"):
                                return "cancelled"
            if total and os.path.getsize(part) < total:
                return "Spojení se přerušilo (staženo %d z %d B)." % (os.path.getsize(part), total)
            os.replace(part, job["path"])
            return "done"
        except Exception as error:   # síť, disk: zkusí se znovu, nakonec se položka označí jako nepovedená
            return "Stahování se nepodařilo: %s" % error

    def _subtitles(self, job):
        saved = []
        for track in job.get("subtitles") or []:
            try:
                request = urllib.request.Request(track["url"], headers=dict(job.get("headers") or {}))
                with self.opener.open(request, timeout=30) as response, open(track["path"], "wb") as output:
                    output.write(response.read())
                saved.append(track["path"])
            except Exception:
                continue          # titulky nejsou nutné: video se uloží i bez nich
        return saved
