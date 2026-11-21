"""Minimální náhrada modulu xbmc pro testy mimo Kodi."""
LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
LOG = []
BUILTINS = []
KEYBOARD_INPUT = []


def log(msg, level=LOGDEBUG):
    LOG.append((level, msg))


def getInfoLabel(label):
    return "21.1 (21.1.0) Git:test" if label == "System.BuildVersion" else ""


def executebuiltin(cmd, wait=False):
    BUILTINS.append(cmd)


class Keyboard(object):
    def __init__(self, default="", heading="", hidden=False):
        self.text = default
        self.confirmed = False

    def doModal(self):
        if KEYBOARD_INPUT:
            self.text = KEYBOARD_INPUT.pop(0)
            self.confirmed = self.text is not None

    def isConfirmed(self):
        return self.confirmed

    def getText(self):
        return self.text or ""


class Monitor(object):
    def abortRequested(self):
        return False

    def waitForAbort(self, timeout=0):
        return True


class Player(object):
    def isPlayingVideo(self):
        return False


class Actor(object):
    def __init__(self, name="", role="", order=-1, thumbnail=""):
        self.name = name
