"""Místní server pro okno aplikace (jen na 127.0.0.1, s tajným klíčem v adrese).

- ``GET /`` a ``/ui/…``: rozhraní (HTML, JS, CSS, ikony a malby),
- ``POST /api/<akce>``: logika aplikace – ``app_api.call(akce, parametry)``, stejně jako na televizi,
- ``POST /local/<akce>``: co je jen ve Windows – přehrávač mpv, otevření odkazu, stahování, konec.

Klíč (``token``) brání tomu, aby se na server dostala jiná stránka otevřená v prohlížeči.
"""

import json
import mimetypes
import os
import posixpath
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/javascript", ".js")


class Backend(object):
    """Co server obsluhuje: logika (``api``), přehrávač, stahování a složka s rozhraním."""

    def __init__(self, api, player, downloads, ui_dir):
        self.api = api
        self.player = player
        self.downloads = downloads
        self.ui_dir = ui_dir
        self.token = secrets.token_urlsafe(16)
        self.alive = time.time()
        self.quit = threading.Event()
        self.closed = 0.0          # kdy okno ohlásilo zavření (při obnovení stránky se zase ozve)
        self.keeper = None         # plnění seznamu na pozadí (background.Keeper)

    def call(self, action, params):
        """Akce logiky; vrací data, nebo ``None`` při chybě (pro přehrávač a stahování)."""
        answer = json.loads(self.api.call(action, json.dumps(params or {})))
        return answer.get("data") if answer.get("ok") else None

    def local(self, action, params):
        if action == "alive":
            self.alive = time.time()
            return {"ok": True}
        if action == "quit":
            self.quit.set()
            return {"ok": True}
        if action == "closed":
            self.closed = time.time()
            return {"ok": True}
        if action == "play":
            style = self.call("subtitle_style", {}) or {}
            mode = style.get("audio_output") or "auto"
            settings = {"mode": mode, "night": bool(style.get("audio_night")),
                        "delay_ms": int((style.get("audio_delays") or {}).get(mode) or 0)}
            return {"ok": True, "data": self.player.start(params.get("stream") or {}, params.get("position") or 0,
                                                          settings, style)}
        if action == "player":
            return {"ok": True, "data": self.player.status()}
        # panel přehrávání (klávesa M v mpv): stopy, titulky a jejich vzhled, zvuk – mění se hned
        if action == "player_tracks":
            data = self.player.tracks()
            data["position"] = self.player.position()
            return {"ok": True, "data": data}
        if action == "player_select":
            return {"ok": self.player.select(params.get("kind"), params.get("id"))}
        if action == "player_style":
            return {"ok": self.player.style(self.call("subtitle_style", {}) or {})}
        if action == "player_audio":
            if "delay_ms" in params:
                self.player.audio_delay(int(params.get("delay_ms") or 0))
            if "night" in params:
                self.player.night(bool(params.get("night")))
            return {"ok": True}
        if action == "player_subfile":
            path = str(params.get("path") or "")
            return {"ok": bool(path) and os.path.isfile(path) and self.player.add_subtitles(path)}
        if action == "player_show":
            return {"ok": self.player.show(params.get("visible", True) is not False)}
        if action == "player_stop":
            self.player.stop()
            return {"ok": True}
        if action == "open":
            url = str(params.get("url") or "")
            if url.startswith(("http://", "https://")):
                webbrowser.open(url)
            return {"ok": True}
        if action == "downloads":
            started = self.downloads.start()
            return {"ok": True, "data": {"started": started, "running": self.downloads.running(),
                                         "message": self.downloads.message}}
        if action == "folder":
            backups = os.path.join(os.path.dirname(self.downloads.default_dir), "Zálohy")
            os.makedirs(backups, exist_ok=True)
            return {"ok": True, "data": {"download_dir": self.downloads.folder(), "backup_dir": backups}}
        return {"ok": False, "error": "Neznámá akce: %s" % action}


def make_handler(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # žádné výpisy do konzole
            pass

        def _send(self, status, body, kind="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.headers.get("X-Token") == backend.token or query.get("t", [""])[0] == backend.token

        def do_GET(self):
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            if path in ("/", "/index.html"):
                if not self._authorized():
                    return self._send(403, "Přístup odepřen.", "text/plain; charset=utf-8")
                path = "/ui/index.html"
            if not path.startswith("/ui/"):
                return self._send(404, "Nenalezeno.", "text/plain; charset=utf-8")
            relative = posixpath.normpath(path[len("/ui/"):])
            if relative.startswith(("..", "/")):
                return self._send(404, "Nenalezeno.", "text/plain; charset=utf-8")
            full = os.path.join(backend.ui_dir, *relative.split("/"))
            if not os.path.isfile(full):
                return self._send(404, "Nenalezeno.", "text/plain; charset=utf-8")
            with open(full, "rb") as source:
                data = source.read()
            if relative == "index.html":
                data = data.replace(b"__TOKEN__", backend.token.encode("ascii"))
            kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
            self._send(200, data, kind + ("; charset=utf-8" if kind.startswith("text/") else ""))

        def do_POST(self):
            if not self._authorized():
                return self._send(403, json.dumps({"ok": False, "error": "Přístup odepřen."}))
            path = urllib.parse.urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                params = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                params = {}
            if path.startswith("/api/"):
                action = path[len("/api/"):]
                answer = backend.api.call(action, json.dumps(params))
                if backend.keeper is not None:
                    backend.keeper.kick(action)
                return self._send(200, answer)
            if path.startswith("/local/"):
                try:
                    answer = backend.local(path[len("/local/"):], params)
                except Exception as error:
                    answer = {"ok": False, "error": str(error)}
                return self._send(200, json.dumps(answer, ensure_ascii=False))
            self._send(404, json.dumps({"ok": False, "error": "Nenalezeno."}))

    return Handler


def serve(backend, port=0):
    """Spustí server na pozadí; vrací ``(server, adresa okna)``."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(backend))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="server")
    thread.daemon = True
    thread.start()
    return server, "http://127.0.0.1:%d/?t=%s" % (server.server_address[1], backend.token)
