"""Promítárna pro Windows: přenosná verze aplikace (bez instalace).

Logika je stejná jako v aplikaci pro Android TV (``app_api`` a sdílené moduly ze složky ``shared``);
okno ji volá přes místní server (``server.py``) a vykresluje stejné obrazovky (``ui/``). Videa
přehrává přibalený přehrávač mpv (``player.py``), stahování na disk obstarává ``downloads.py``.
"""

VERSION = "0.1.8"
