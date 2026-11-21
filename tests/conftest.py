import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fake_kodi"))
sys.path.insert(0, os.path.join(HERE, "..", "plugin.video.streamujtv", "resources", "lib"))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()
