"""Spuštění Promítárny pro Windows: logika, místní server, přehrávač a okno.

Okno je vestavěný prohlížeč (pywebview s WebView2, které mají Windows 10 a 11). Když není k dispozici,
otevře se Edge v režimu aplikace (bez lišt), a nakonec výchozí prohlížeč. Aplikace skončí po zavření
okna; u prohlížeče, když okno ohlásí zavření, nebo se přestane hlásit (``ALIVE_TIMEOUT``).

Při konci se ukončí všechno, co program spustil (přehrávač, Edge), a proces skončí hned – nic z něj
nezůstane běžet na pozadí a nedrží soubory ve složce otevřené (jinak by složka nešla smazat bez
povolení správce).
"""

import os
import shutil
import subprocess
import sys
import time

from . import paths
from .background import Keeper
from .downloads import Downloads
from .player import Player
from .server import Backend, serve

ALIVE_TIMEOUT = 45      # s bez zprávy od okna = okno je zavřené
CLOSE_GRACE = 4         # s po ohlášeném zavření okna: obnovená stránka se mezitím zase ozve


def window_open(backend, now=None):
    """Běží ještě okno v prohlížeči? Ne po ``quit``, po ohlášeném zavření (bez nové zprávy do
    ``CLOSE_GRACE``) ani po ``ALIVE_TIMEOUT`` bez zprávy."""
    now = now or time.time()
    if backend.quit.is_set() or now - backend.alive >= ALIVE_TIMEOUT:
        return False
    return not (backend.closed and backend.alive <= backend.closed and now - backend.closed >= CLOSE_GRACE)


def _edge():
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidate = os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(candidate):
                return candidate
    return shutil.which("msedge")


def _window(url, data_dir):
    """Otevře okno; vrací ``(vlastní okno, proces prohlížeče)``. Vlastní okno (pywebview) už skončilo
    zavřením; jinak okno běží v Edge (proces se při konci ukončí) nebo ve výchozím prohlížeči."""
    try:
        import webview   # pywebview
        webview.create_window("Promítárna", url, width=1280, height=760, min_size=(900, 560),
                              background_color="#D2C9B4", maximized=True)
        webview.start(private_mode=False, storage_path=os.path.join(data_dir, "okno"))
        return True, None
    except Exception:
        pass
    edge = _edge()
    if edge:
        # vlastní profil Edge ve složce data: tenhle proces je celý prohlížeč, při konci se ukončí
        return False, subprocess.Popen([edge, "--app=%s" % url, "--user-data-dir=%s" % os.path.join(data_dir, "okno-edge"),
                                        "--no-first-run", "--start-maximized"])
    import webbrowser
    webbrowser.open(url)
    return False, None


def _close(process):
    """Ukončí proces (Edge s oknem programu), pokud ještě běží."""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def main():
    data = paths.data_dir()
    os.makedirs(data, exist_ok=True)
    paths.use_logic()
    import app_api   # logika aplikace (stejná jako na Android TV)
    app_api.configure(os.path.join(data, "aplikace"))
    backend = Backend(app_api, None, None, paths.ui_dir())
    backend.player = Player(backend.call, paths.mpv_path(), conf_dir=data)
    backend.downloads = Downloads(backend.call, os.path.join(data, "Stažené"))
    backend.keeper = Keeper(backend.call)
    backend.keeper.start()
    server, url = serve(backend)
    own_window, browser = _window(url, data)
    if not own_window:
        # okno je v prohlížeči: aplikace běží, dokud se okno hlásí
        while window_open(backend):
            backend.quit.wait(1)
    backend.keeper.stop()
    backend.player.stop()
    _close(browser)
    server.shutdown()
    return 0


def run():
    """Spuštění programu: po zavření okna proces skončí hned. Vlákna na pozadí (ověřování seznamu,
    stahování) by ho jinak mohla chvíli držet – a s ním otevřené soubory ve složce. Data se ukládají
    průběžně, nic se tím neztratí."""
    code = main()
    for stream in (sys.stdout, sys.stderr):   # program bez konzole je nemá (None)
        if stream is not None:
            stream.flush()
    os._exit(code or 0)


if __name__ == "__main__":
    run()
