"""Přehrávání přes mpv: spuštění, průběžné ukládání pozice a co se stalo po skončení.

Okno aplikace dostane od logiky adresu videa (stejná data jako přehrávač na Android TV: ``url``,
``headers``, ``subtitles``, ``position``, ``track``, ``choice``…) a předá je sem. mpv se spustí přes
celou obrazovku; ovládá se svými klávesami (mezerník pauza, šipky posun, ``#`` zvuková stopa,
``j`` titulky, ``f`` celá obrazovka, ``q`` konec) a klávesou ``M``: film se zmenší a v okně aplikace
je panel se zvukovou stopou, titulky (i z OpenSubtitles), jejich vzhledem a nastavením zvuku. Pozice se ukládá do logiky (``progress_save``)
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
MENU_KEYS = ("m", "M")   # klávesy v mpv: film se zmenší a v okně aplikace se ukáže panel se zvukem a titulky
LANG_CODES = {"cs": "cs,cze,ces", "sk": "sk,slk,slo", "en": "en,eng"}
# pozadí titulků (mpv 0.38+: --sub-border-style); barva pozadí jako r/g/b/a
SUB_BACKGROUNDS = {
    "solid": {"sub-border-style": "background-box", "sub-back-color": "0.0/0.0/0.0/1.0"},
    "shade": {"sub-border-style": "background-box", "sub-back-color": "0.0/0.0/0.0/0.55"},
    "outline": {"sub-border-style": "outline-and-shadow", "sub-back-color": "0.0/0.0/0.0/0.0"},
}
ENDED_WITHIN = 15        # s před koncem: video se bere jako dokoukané
NIGHT_FILTER = "lavfi=[acompressor=threshold=0.1:ratio=4:attack=5:release=300:makeup=2.5]"

# výstup zvuku (stejné volby jako na televizi): co se stane s prostorovým zvukem
AUDIO_OPTIONS = {
    "auto": ["--audio-spdif=ac3,eac3,dts,dts-hd,truehd"],   # předat soundbaru / zesilovači (HDMI, S/PDIF)
    "pcm": [],                                              # dekóduje přehrávač, kanály zůstávají
    "pcm_soft": ["--af-append=lavfi=[lowshelf=g=-8:f=120]"],   # totéž, basy tišeji
    "stereo": ["--audio-channels=stereo"],                  # převést na dva kanály
}


def style_properties(style):
    """Vlastnosti mpv pro vzhled titulků: ``style`` = ``{"scale", "rgb", "background"}`` (z ``subtitle_style``)."""
    style = style or {}
    props = {"sub-scale": "%.2f" % float(style.get("scale") or 1.0)}
    rgb = str(style.get("rgb") or "#FFFFFF")
    if len(rgb) == 7 and rgb.startswith("#"):
        props["sub-color"] = rgb
    props.update(SUB_BACKGROUNDS.get(style.get("background") or "solid", SUB_BACKGROUNDS["solid"]))
    return props


def _langs(code, default):
    """Seznam jazyků pro --alang/--slang: zvolený jazyk první, pak čeština a slovenština."""
    first = LANG_CODES.get(code, code) if code and code != "off" else ""
    return ",".join(part for part in (first, default) if part)


def build_args(mpv, stream, position, audio=None, ipc="", style=None, input_conf="", log_file=""):
    """Příkazová řádka pro mpv. ``audio``: ``{"mode", "delay_ms", "night"}`` z nastavení zvuku,
    ``style``: vzhled titulků; zvuk a titulky naposledy zvolené u titulu jsou v ``stream["tracks"]``."""
    audio = audio or {}
    tracks = stream.get("tracks") or {}
    default = "cs,cze,ces,sk,slk,slo"
    # --ytdl=no: adresy jsou přímé soubory a streamy, doplněk pro stahování z webů by jen zdržoval a chyboval
    args = [mpv, "--force-window=immediate", "--fs", "--keep-open=no", "--no-terminal", "--ytdl=no",
            "--title=%s" % (stream.get("title") or "Promítárna"), "--osd-level=1",
            "--alang=%s" % _langs(tracks.get("audio"), default)]
    if tracks.get("subs") == "off":
        args.append("--sid=no")
    else:
        args.append("--slang=%s" % _langs(tracks.get("subs"), default))
    args += ["--%s=%s" % (name, value) for name, value in sorted(style_properties(style).items())]
    if input_conf:
        args.append("--input-conf=%s" % input_conf)
    if log_file:
        args.append("--log-file=%s" % log_file)     # když video nejde přehrát, je z čeho poznat proč
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
        args.append("--af-append=@noc:%s" % NIGHT_FILTER)
        if (audio.get("mode") or "auto") == "auto":
            args.remove("--audio-spdif=ac3,eac3,dts,dts-hd,truehd")   # zvuk musí zpracovat přehrávač
    if ipc:
        args.append("--input-ipc-server=%s" % ipc)
    args.append(stream["url"])
    return args


FAILURE_WORDS = ("error", "failed", "fail", "cannot", "unable", "forbidden", "denied", "not found", "403", "404")


def failure(log_file, returncode=None):
    """Proč se video nepřehrálo: poslední chybové řádky ze záznamu mpv (nebo kód ukončení)."""
    lines = []
    try:
        with open(log_file, encoding="utf-8", errors="replace") as log:
            for line in log:
                text = line.strip()
                low = text.lower()
                if any(word in low for word in FAILURE_WORDS) and "ytdl" not in low:
                    lines.append(text[-200:])
    except (OSError, TypeError):
        pass
    if lines:
        return "\n".join(lines[-4:])
    if returncode:
        return "Přehrávač mpv skončil s kódem %s." % returncode
    return ""


class _Ipc(object):
    """Spojení s běžícím mpv (pojmenovaná roura ve Windows, soket jinde): sleduje čas a délku."""

    def __init__(self, path):
        self.path = path
        self.time = 0.0
        self.duration = 0.0
        self.eof = False
        self.menu = 0            # kolikrát uživatel v mpv stiskl klávesu panelu
        self._stream = None
        self._lock = threading.Lock()
        self._next = 100
        self._waiting = {}       # request_id → [Event, odpověď]

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
            with self._lock:
                self._stream.write((json.dumps(message) + "\n").encode("utf-8"))
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def request(self, command, timeout=3.0):
        """Pošle příkaz a počká na odpověď: ``(ok, data)``."""
        with self._lock:
            self._next += 1
            number = self._next
        slot = [threading.Event(), None]
        self._waiting[number] = slot
        if not self._send({"command": command, "request_id": number}) or not slot[0].wait(timeout):
            self._waiting.pop(number, None)
            return False, None
        answer = slot[1] or {}
        return answer.get("error") == "success", answer.get("data")

    def listen(self):
        """Čte zprávy, dokud mpv neskončí."""
        try:
            for line in self._stream:
                try:
                    event = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if "request_id" in event and "event" not in event:
                    slot = self._waiting.pop(event.get("request_id"), None)
                    if slot:
                        slot[1] = event
                        slot[0].set()
                    continue
                if event.get("event") == "client-message" and (event.get("args") or [""])[0] == "promitarna-menu":
                    self.menu += 1
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

    def __init__(self, call, mpv="mpv", conf_dir=""):
        self.call = call
        self.mpv = mpv
        self.conf_dir = conf_dir         # kam se zapíše input.conf s klávesou panelu
        self.lock = threading.Lock()
        self.process = None
        self.ipc = None
        self.log_file = ""
        self.audio = {}
        self.state = {"playing": False}

    def status(self):
        with self.lock:
            state = dict(self.state)
        if self.ipc is not None:
            state["menu"] = self.ipc.menu
        return state

    def _input_conf(self):
        if not self.conf_dir:
            return ""
        path = os.path.join(self.conf_dir, "mpv-input.conf")
        try:
            os.makedirs(self.conf_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as conf:
                for key in MENU_KEYS:
                    conf.write("%s script-message promitarna-menu\n" % key)
            return path
        except OSError:
            return ""

    def start(self, stream, position=0.0, audio=None, style=None):
        """Spustí přehrávání; vrací hned (mpv běží ve vlastním okně)."""
        self.stop()
        name = "promitarna-mpv-%d-%d" % (os.getpid(), int(time.time() * 1000) % 100000)
        ipc = r"\\.\pipe\%s" % name if sys.platform == "win32" else os.path.join(
            os.environ.get("TMPDIR", "/tmp"), name + ".sock")
        self.audio = dict(audio or {})
        self.log_file = os.path.join(self.conf_dir, "mpv.log") if self.conf_dir else ""
        if self.log_file:
            try:
                os.remove(self.log_file)          # záznam jen z tohoto přehrávání
            except OSError:
                pass
        args = build_args(self.mpv, stream, position, audio, ipc, style, self._input_conf(), self.log_file)
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

    # --- ovládání za běhu (panel v okně aplikace) -------------------------------------------------

    def _command(self, *command):
        ipc = self.ipc
        if ipc is None:
            return False, None
        return ipc.request(list(command))

    def tracks(self):
        """Zvukové stopy a titulky přehrávaného videa: ``{"audio": [...], "subs": [...]}``."""
        ok, data = self._command("get_property", "track-list")
        result = {"audio": [], "subs": []}
        for track in data if ok and isinstance(data, list) else []:
            kind = {"audio": "audio", "sub": "subs"}.get(track.get("type"))
            if not kind:
                continue
            parts = [track.get("title") or "", (track.get("lang") or "").upper()]
            if kind == "audio" and track.get("demux-channel-count"):
                parts.append("%d kanálů" % track["demux-channel-count"])
            if track.get("codec"):
                parts.append(str(track["codec"]))
            if track.get("external"):
                parts.append("ze souboru")
            label = " · ".join(part for part in parts if part) or "Stopa %s" % track.get("id")
            result[kind].append({"id": track.get("id"), "title": label, "lang": track.get("lang") or "",
                                 "selected": bool(track.get("selected")), "external": bool(track.get("external"))})
        return result

    def select(self, kind, track_id):
        """Zvolí zvukovou stopu (``audio``) nebo titulky (``subs``); titulky ``no`` = vypnout."""
        name = "aid" if kind == "audio" else "sid"
        return self._command("set_property", name, track_id if track_id not in (None, "") else "no")[0]

    def add_subtitles(self, path):
        """Přidá titulky ze souboru (např. stažené z OpenSubtitles) a rovnou je zapne."""
        return self._command("sub-add", path, "select")[0]

    def style(self, style):
        ok = True
        for name, value in style_properties(style).items():
            ok = self._command("set_property", name, value)[0] and ok
        return ok

    def audio_delay(self, delay_ms):
        self.audio["delay_ms"] = int(delay_ms)
        return self._command("set_property", "audio-delay", -int(delay_ms) / 1000.0)[0]

    def night(self, on):
        self.audio["night"] = bool(on)
        self._command("af", "remove", "@noc")
        return self._command("af", "add", "@noc:" + NIGHT_FILTER)[0] if on else True

    def show(self, visible=True):
        """Film zpátky přes celou obrazovku (``visible``), nebo zmenšit, ať je vidět okno aplikace."""
        if visible:
            self._command("set_property", "window-minimized", False)
            return self._command("set_property", "fullscreen", True)[0]
        self._command("set_property", "fullscreen", False)
        return self._command("set_property", "window-minimized", True)[0]

    def position(self):
        return self.ipc.time if self.ipc is not None else 0.0

    def _watch(self, process, path, stream):
        ipc = _Ipc(path)
        if ipc.connect():
            self.ipc = ipc
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
        # video se vůbec nerozběhlo (žádná délka): důvod ze záznamu mpv pro okno a Diagnostiku
        error = "" if ended or ipc.duration > 0 else failure(self.log_file, process.returncode)
        with self.lock:
            if self.process is process:      # mezitím se nespustilo jiné přehrávání (nový výstup zvuku…)
                self.state.update(playing=False, ended=ended, finished=True, position=ipc.time, error=error)
            if self.ipc is ipc:
                self.ipc = None
        if sys.platform != "win32":
            try:
                os.remove(path)
            except OSError:
                pass
