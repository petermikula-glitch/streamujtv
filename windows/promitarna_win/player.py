"""Přehrávání přes mpv: spuštění, průběžné ukládání pozice a co se stalo po skončení.

Okno aplikace dostane od logiky adresu videa (stejná data jako přehrávač na Android TV: ``url``,
``headers``, ``subtitles``, ``position``, ``track``, ``choice``…) a předá je sem. mpv se spustí přes
celou obrazovku; ovládá se svými klávesami (mezerník pauza, šipky posun, ``#`` zvuková stopa,
``j`` titulky, ``f`` celá obrazovka, ``q`` konec). Pozice se ukládá do logiky (``progress_save``)
každých ``SAVE_EVERY`` sekund a na konci, takže Rozkoukané fungují stejně jako na televizi.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

SAVE_EVERY = 15          # s: jak často se ukládá, kde se je
ENDED_WITHIN = 15        # s před koncem: video se bere jako dokoukané
NIGHT_FILTER = "lavfi=[acompressor=threshold=0.1:ratio=4:attack=5:release=300:makeup=2.5]"

# výstup zvuku (stejné volby jako na televizi): co se stane s prostorovým zvukem
AUDIO_OPTIONS = {
    "auto": ["--audio-spdif=ac3,eac3,dts,dts-hd,truehd"],   # předat soundbaru / zesilovači (HDMI, S/PDIF)
    "pcm": [],                                              # dekóduje přehrávač, kanály zůstávají
    "pcm_soft": ["--af-append=lavfi=[lowshelf=g=-8:f=120]"],   # totéž, basy tišeji
    "stereo": ["--audio-channels=stereo"],                  # převést na dva kanály
}


def build_args(mpv, stream, position, audio=None, ipc=""):
    """Příkazová řádka pro mpv. ``audio``: ``{"mode", "delay_ms", "night"}`` z nastavení zvuku."""
    audio = audio or {}
    args = [mpv, "--force-window=immediate", "--fs", "--keep-open=no", "--no-terminal",
            "--title=%s" % (stream.get("title") or "Promítárna"), "--osd-level=1",
            "--alang=cs,cze,ces,sk,slk,slo", "--slang=cs,cze,ces,sk,slk,slo"]
    headers = stream.get("headers") or {}
    if headers.get("User-Agent"):
        args.append("--user-agent=%s" % headers["User-Agent"])
    other = ["%s: %s" % (name, value) for name, value in headers.items() if name != "User-Agent"]
    if other:
        args.append("--http-header-fields=%s" % ",".join(item.replace(",", r"\,") for item in other))
    if position and position > 0 and not stream.get("live"):
        args.append("--start=%d" % int(position))
    for subtitle in stream.get("subtitles") or []:
        args.append("--sub-file=%s" % subtitle)
    args += AUDIO_OPTIONS.get(audio.get("mode") or "auto", [])
    if audio.get("delay_ms"):
        # kladné = zvuk ze soundbaru zní později: v mpv se zvuk pustí o tolik dřív
        args.append("--audio-delay=%.3f" % (-int(audio["delay_ms"]) / 1000.0))
    if audio.get("night"):
        args.append("--af-append=%s" % NIGHT_FILTER)
        if (audio.get("mode") or "auto") == "auto":
            args.remove("--audio-spdif=ac3,eac3,dts,dts-hd,truehd")   # zvuk musí zpracovat přehrávač
    if ipc:
        args.append("--input-ipc-server=%s" % ipc)
    args.append(stream["url"])
    return args


class _Ipc(object):
    """Spojení s běžícím mpv (pojmenovaná roura ve Windows, soket jinde): sleduje čas a délku."""

    def __init__(self, path):
        self.path = path
        self.time = 0.0
        self.duration = 0.0
        self.eof = False
        self._stream = None

    def connect(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if sys.platform == "win32":
                    self._stream = open(self.path, "r+b", buffering=0)
                else:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(self.path)
                    self._stream = sock.makefile("rwb", buffering=0)
                break
            except OSError:
                time.sleep(0.2)
        if self._stream is None:
            return False
        for number, name in enumerate(("time-pos", "duration", "eof-reached"), 1):
            self._send({"command": ["observe_property", number, name]})
        return True

    def _send(self, message):
        try:
            self._stream.write((json.dumps(message) + "\n").encode("utf-8"))
        except (OSError, ValueError):
            pass

    def listen(self):
        """Čte zprávy, dokud mpv neskončí."""
        try:
            for line in self._stream:
                try:
                    event = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if event.get("event") != "property-change":
                    continue
                value = event.get("data")
                if event.get("name") == "time-pos" and isinstance(value, (int, float)):
                    self.time = float(value)
                elif event.get("name") == "duration" and isinstance(value, (int, float)):
                    self.duration = float(value)
                elif event.get("name") == "eof-reached" and value:
                    self.eof = True
        except (OSError, ValueError):
            pass


class Player(object):
    """Jedno přehrávání najednou. ``call(action, params)`` je vstup do logiky aplikace."""

    def __init__(self, call, mpv="mpv"):
        self.call = call
        self.mpv = mpv
        self.lock = threading.Lock()
        self.process = None
        self.state = {"playing": False}

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, stream, position=0.0, audio=None):
        """Spustí přehrávání; vrací hned (mpv běží ve vlastním okně)."""
        self.stop()
        name = "promitarna-mpv-%d-%d" % (os.getpid(), int(time.time() * 1000) % 100000)
        ipc = r"\\.\pipe\%s" % name if sys.platform == "win32" else os.path.join(
            os.environ.get("TMPDIR", "/tmp"), name + ".sock")
        args = build_args(self.mpv, stream, position, audio, ipc)
        try:
            process = subprocess.Popen(args)
        except OSError as error:
            raise RuntimeError("Přehrávač mpv se nepodařilo spustit (%s). Je ve složce mpv vedle Promitarna.exe?"
                               % error)
        with self.lock:
            self.process = process
            self.state = {"playing": True, "track": stream.get("track") or "", "choice": stream.get("choice") or "",
                          "title": stream.get("title") or "", "live": bool(stream.get("live")),
                          "position": float(position or 0), "ended": False, "finished": False}
        thread = threading.Thread(target=self._watch, args=(process, ipc, stream), name="mpv")
        thread.daemon = True
        thread.start()
        return self.status()

    def stop(self):
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _save(self, stream, ipc, ended=False, first=False):
        track = stream.get("track") or ""
        if not track or stream.get("live") or ipc.duration <= 0:
            return
        self.call("progress_save", {"track": track, "position": ipc.duration if ended else ipc.time,
                                    "total": ipc.duration, "ended": ended, "started": first})

    def _watch(self, process, path, stream):
        ipc = _Ipc(path)
        if ipc.connect():
            reader = threading.Thread(target=ipc.listen, name="mpv-ipc")
            reader.daemon = True
            reader.start()
        first, last = True, 0.0
        while process.poll() is None:
            time.sleep(1)
            with self.lock:
                self.state["position"] = ipc.time
            if ipc.duration > 0 and (first or time.time() - last >= SAVE_EVERY):
                self._save(stream, ipc, first=first)
                first, last = False, time.time()
        ended = ipc.eof or (ipc.duration > 0 and ipc.duration - ipc.time <= ENDED_WITHIN)
        self._save(stream, ipc, ended=ended)
        with self.lock:
            self.state.update(playing=False, ended=ended, finished=True, position=ipc.time)
        if sys.platform != "win32":
            try:
                os.remove(path)
            except OSError:
                pass
