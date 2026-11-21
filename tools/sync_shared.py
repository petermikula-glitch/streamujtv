# -*- coding: utf-8 -*-
"""Zkopíruje sdílené soubory z doplňku streamuj.tv (kanonická verze) do ostatních doplňků.

Kodi doplněk musí být samostatný, proto se kód kopíruje místo importu.
Test tests/test_shared.py hlídá, že kopie jsou shodné.

    python3 tools/sync_shared.py
"""
import os
import shutil

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SOURCE = "plugin.video.streamujtv"
TARGETS = ["plugin.video.prehrajto.mojevidea"]
SHARED = [
    "default.py", "LICENSE.txt", "resources/lib/csfd.py", "resources/lib/exporter.py",
    "resources/lib/kodiutils.py", "resources/lib/nameparser.py",
]


def main():
    for target in TARGETS:
        for rel in SHARED:
            src = os.path.join(ROOT, SOURCE, rel)
            dst = os.path.join(ROOT, target, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        print("synchronizováno:", target)


if __name__ == "__main__":
    main()
