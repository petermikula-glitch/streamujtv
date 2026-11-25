"""Kde co je: složka programu, data vedle něj (přenosná verze) a logika aplikace.

Přenosná verze nic neinstaluje a nic nezapisuje mimo svou složku: katalog, nastavení, profily
i stažená videa jsou ve složce ``data`` vedle ``Promitarna.exe`` – celou složku jde přenést jinam
(i na flashku). Při spuštění ze zdrojů (vývoj, testy) jsou data v ``windows/data``.
"""

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))   # spuštěno jako sestavený Promitarna.exe


def program_dir():
    """Složka s ``Promitarna.exe`` (sestavená verze), jinak složka ``windows`` ve zdrojích."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir():
    """Kde jsou přibalené soubory (logika, rozhraní, mpv): v sestavené verzi rozbalené PyInstallerem."""
    if FROZEN:
        return getattr(sys, "_MEIPASS", program_dir())
    return program_dir()


def repo_dir():
    return os.path.dirname(program_dir())


def data_dir():
    override = os.environ.get("PROMITARNA_DATA")
    return override or os.path.join(program_dir(), "data")


def ui_dir():
    return os.path.join(bundle_dir(), "ui")


def logic_dirs():
    """Složky s logikou aplikace: v sestavené verzi přibalená kopie, ve zdrojích přímo z repozitáře
    (stejné soubory jako v aplikaci pro Android – jeden zdroj pravdy)."""
    packed = os.path.join(bundle_dir(), "logic")
    if os.path.isdir(packed):
        return [packed]
    repo = repo_dir()
    return [os.path.join(repo, "android", "app", "src", "main", "python"),
            os.path.join(repo, "plugin.video.prehrajto.mojevidea", "resources", "lib")]


def use_logic():
    for directory in reversed(logic_dirs()):
        if directory not in sys.path:
            sys.path.insert(0, directory)


def mpv_path():
    """Přibalený přehrávač mpv; ve zdrojích ten v ``windows/mpv``, jinak z PATH."""
    for candidate in (os.path.join(program_dir(), "mpv", "mpv.exe"), os.path.join(bundle_dir(), "mpv", "mpv.exe"),
                      os.path.join(program_dir(), "mpv", "mpv")):
        if os.path.exists(candidate):
            return candidate
    return "mpv"
