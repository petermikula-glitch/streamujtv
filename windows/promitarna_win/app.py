"""Spuštění Promítárny pro Windows: logika, místní server, přehrávač a okno.

Okno je vestavěný prohlížeč (pywebview s WebView2, které mají Windows 10 a 11). Když není k dispozici,
otevře se Edge v režimu aplikace (bez lišt), a nakonec výchozí prohlížeč. Aplikace skončí po zavření
okna; u prohlížeče, když okno přestane hlásit, že je otevřené (``ALIVE_TIMEOUT``).
"""

import os
import shutil
import subprocess
import sys
import time

from . import paths
from .downloads import Downloads
from .player import Player
from .server import Backend, serve

ALIVE_TIMEOUT = 45      # s bez zprávy od okna = okno je zavřené


def _edge():
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidate = os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(candidate):
                return candidate
    return shutil.which("msedge")


def _window(url, data_dir):
    """Otevře okno; vrací True, když běží „vlastní“ okno (pywebview) a skončilo zavřením."""
    try:
        import webview   # pywebview
        webview.create_window("Promítárna", url, width=1280, height=760, min_size=(900, 560),
                              background_color="#D2C9B4")
        webview.start(private_mode=False, storage_path=os.path.join(data_dir, "okno"))
        return True
    except Exception:
        pass
    edge = _edge()
    if edge:
        subprocess.Popen([edge, "--app=%s" % url, "--user-data-dir=%s" % os.path.join(data_dir, "okno-edge"),
                          "--no-first-run", "--start-maximized"])
    else:
        import webbrowser
        webbrowser.open(url)
    return False


def main():
    data = paths.data_dir()
    os.makedirs(data, exist_ok=True)
    paths.use_logic()
    import app_api   # logika aplikace (stejná jako na Android TV)
    app_api.configure(os.path.join(data, "aplikace"))
    backend = Backend(app_api, None, None, paths.ui_dir())
    backend.player = Player(backend.call, paths.mpv_path())
    backend.downloads = Downloads(backend.call, os.path.join(data, "Stažené"))
    server, url = serve(backend)
    own_window = _window(url, data)
    if not own_window:
        # okno je v prohlížeči: aplikace běží, dokud se okno hlásí
        while not backend.quit.is_set() and time.time() - backend.alive < ALIVE_TIMEOUT:
            backend.quit.wait(5)
    backend.player.stop()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
