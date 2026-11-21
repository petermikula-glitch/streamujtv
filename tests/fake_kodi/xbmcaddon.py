import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..",
                    os.environ.get("FAKE_KODI_ADDON", "plugin.video.streamujtv"))
with open(os.path.join(ROOT, "addon.xml"), encoding="utf-8") as _f:
    ADDON_ID = re.search(r'<addon[^>]*\bid="([^"]+)"', _f.read()).group(1)
SETTINGS = {}
PROFILE_DIR = [None]


def _load_strings():
    path = os.path.join(ROOT, "resources", "language", "resource.language.cs_cz", "strings.po")
    out = {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for m in re.finditer(r'msgctxt "#(\d+)"\nmsgid "(.*)"\nmsgstr "(.*)"', text):
        out[int(m.group(1))] = (m.group(3) or m.group(2)).encode().decode("unicode_escape").encode(
            "latin-1").decode("utf-8")
    return out


STRINGS = _load_strings()


class Addon(object):
    def __init__(self, id=None):
        pass

    def getAddonInfo(self, key):
        return {"id": ADDON_ID, "name": ADDON_ID, "path": ROOT,
                "profile": PROFILE_DIR[0] or "/tmp/streamujtv-test-profile", "version": "1.0.0"}[key]

    def getSetting(self, key):
        return SETTINGS.get(key, "")

    def setSetting(self, key, value):
        SETTINGS[key] = value

    def getLocalizedString(self, sid):
        return STRINGS.get(sid, "#%d" % sid)

    def openSettings(self):
        pass
