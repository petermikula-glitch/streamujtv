import importlib
import os
import sys

import pytest

os.environ["FAKE_KODI_ADDON"] = "plugin.video.prehrajto.mojevidea"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fake_kodi"))
sys.path.insert(0, os.path.join(HERE, "..", "plugin.video.prehrajto.mojevidea", "resources", "lib"))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


PLUGIN_MODULES = ("plugin", "ui", "views_catalog", "views_progress", "views_discover", "views_search",
                  "views_collections", "views_tools", "views_kodi", "views_edit", "playback")


@pytest.fixture
def plugin(tmp_path):
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
    xbmcaddon.PROFILE_DIR[0] = str(tmp_path)
    xbmcaddon.SETTINGS.clear()
    xbmcaddon.SETTINGS.update({"show_rating": "true", "auto_meta": "false", "username": "a@b.cz",
                               "password": "heslo", "pref_quality": "0", "subtitles": "true"})
    for lst in (xbmcplugin.ITEMS, xbmcplugin.RESOLVED, xbmcgui.NOTIFICATIONS, xbmcgui.OK_DIALOGS):
        del lst[:]
    import kodiutils
    importlib.reload(kodiutils)
    for name in PLUGIN_MODULES:
        sys.modules.pop(name, None)
    import plugin as p
    return p
