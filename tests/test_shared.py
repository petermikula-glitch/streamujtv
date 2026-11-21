"""Sdílené soubory musí být ve všech doplňcích stejné (tools/sync_shared.py)."""
import filecmp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import sync_shared  # noqa: E402


def test_shared_files_identical():
    root = os.path.join(HERE, "..")
    for target in sync_shared.TARGETS:
        for rel in sync_shared.SHARED:
            a = os.path.join(root, sync_shared.SOURCE, rel)
            b = os.path.join(root, target, rel)
            assert filecmp.cmp(a, b, shallow=False), "%s/%s se liší – spusť tools/sync_shared.py" % (target, rel)
