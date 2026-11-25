"""Sestavení přenosné verze pro Windows: ``python windows/build.py [složka s mpv]``.

Běží ve Windows (na GitHubu workflow ``windows.yml``, nebo ručně s Pythonem 3.11 a balíčky
``pyinstaller`` a ``pywebview``). Výsledek: ``windows/dist/Promitarna-<verze>-windows.zip`` se složkou
``Promitarna`` – v ní ``Promitarna.exe``, přibalený přehrávač ``mpv`` a vše ostatní. Nic se neinstaluje;
data aplikace vzniknou při prvním spuštění ve složce ``data`` vedle ``Promitarna.exe``.

Kroky:
1. logika aplikace (stejné soubory jako v aplikaci pro Android) do ``build/logic``,
2. ikony a malby do ``ui/assets`` (``build_assets.py``),
3. PyInstaller: ``Promitarna.exe`` se vším potřebným (Python je uvnitř),
4. mpv do ``Promitarna/mpv`` a celé do zipu.
"""

import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
SHARED = ("catalog.py", "csfd.py", "db.py", "ivysilani.py", "nameparser.py", "prehrajto.py", "provider.py",
          "search_results.py", "tmdb.py")   # sdílené moduly doplňku pro Kodi (jako v android/app/build.gradle.kts)


def version():
    sys.path.insert(0, HERE)
    from promitarna_win import VERSION
    return VERSION


def copy_logic(target):
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target)
    for path in glob.glob(os.path.join(REPO, "android", "app", "src", "main", "python", "*.py")):
        shutil.copy(path, target)
    for name in SHARED:
        shutil.copy(os.path.join(REPO, "plugin.video.prehrajto.mojevidea", "resources", "lib", name), target)


def main(mpv_dir=""):
    logic = os.path.join(BUILD, "logic")
    copy_logic(logic)
    sys.path.insert(0, HERE)
    import build_assets
    build_assets.build()
    separator = ";" if os.name == "nt" else ":"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "Promitarna",
               "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"), "--specpath", BUILD,
               "--paths", HERE, "--paths", logic,
               "--add-data", "%s%slogic" % (logic, separator),
               "--add-data", "%s%sui" % (os.path.join(HERE, "ui"), separator),
               "--icon", os.path.join(HERE, "ui", "assets", "icon.png") if os.name != "nt" else
               os.path.join(BUILD, "promitarna.ico"),
               os.path.join(HERE, "Promitarna.py")]
    if os.name == "nt":
        _icon(os.path.join(HERE, "ui", "assets", "icon.png"), os.path.join(BUILD, "promitarna.ico"))
    # logika se načítá až za běhu (podle cesty): PyInstaller musí moduly, které používá, přibalit sám
    for path in glob.glob(os.path.join(logic, "*.py")):
        command[-1:-1] = ["--hidden-import", os.path.splitext(os.path.basename(path))[0]]
    subprocess.check_call(command)
    folder = os.path.join(DIST, "Promitarna")
    if mpv_dir:
        shutil.copytree(mpv_dir, os.path.join(folder, "mpv"), dirs_exist_ok=True)
    with open(os.path.join(folder, "ČTI MĚ.txt"), "w", encoding="utf-8") as readme:
        readme.write(README % version())
    archive = os.path.join(DIST, "Promitarna-%s-windows.zip" % version())
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                path = os.path.join(root, name)
                target.write(path, os.path.relpath(path, DIST))
    print(archive)
    return archive


def _icon(png, ico):
    """Ikona programu (``build_assets.app_icon``) ve velikostech pro Windows."""
    try:
        from PIL import Image
        Image.open(png).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        shutil.copy(png, ico)


README = """Promítárna %s pro Windows – přenosná verze

Spuštění: Promitarna.exe. Nic se neinstaluje; nastavení, katalog, profily a stažená videa jsou
ve složce data vedle programu. Celou složku Promitarna jde přenést jinam (i na flashku).

Ovládání: myš, nebo klávesnice – šipky, Enter, Esc (zpět), podržený Esc (hlavní nabídka),
klávesa Menu nebo pravé tlačítko myši (nabídka karty), lomítko (hledat).

Videa hraje přibalený přehrávač mpv: mezerník pauza, šipky posun, # zvuková stopa, J titulky,
F celá obrazovka, Q konec, M panel se zvukovou stopou, titulky a jejich vzhledem v okně aplikace.

Chráněné pořady iVysílání (DRM) tahle verze nepřehrává – pusťte je na webu iVysílání.

Nová verze: zip rozbalte přes tuhle složku (složka data zůstane). Smazání: zavřete okno programu
(program pak skončí celý) a složku smažte; kdyby to nešlo, ukončete Promitarna ve Správci úloh.
"""


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
