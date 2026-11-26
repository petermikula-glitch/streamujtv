import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# sdílená logika aplikace (Android TV i Windows)
sys.path.insert(0, os.path.join(HERE, "..", "shared"))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()
