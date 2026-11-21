# -*- coding: utf-8 -*-
"""Sestaví instalační ZIPy pro Kodi (všechny doplňky) do složky dist/:

    python3 tools/build_zip.py
"""
import os
import re
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ADDONS = ["plugin.video.streamujtv", "plugin.video.prehrajto.mojevidea"]


def main():
    for addon_id in ADDONS:
        build(addon_id)


def build(addon_id):
    ADDON_DIR = os.path.join(ROOT, addon_id)
    with open(os.path.join(ADDON_DIR, "addon.xml"), encoding="utf-8") as f:
        version = re.search(r'<addon[^>]*\bversion="([^"]+)"', f.read()).group(1)
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    target = os.path.join(dist, "%s-%s.zip" % (addon_id, version))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, ROOT))
    print(target)


if __name__ == "__main__":
    main()
