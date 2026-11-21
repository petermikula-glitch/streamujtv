NOTIFICATIONS = []
OK_DIALOGS = []
SELECT_ANSWERS = []
YESNO_ANSWERS = []
NUMERIC_ANSWERS = []
BROWSE_ANSWERS = []


class _Tag(object):
    def __init__(self):
        self.data = {}

    def __getattr__(self, name):
        if name.startswith("set"):
            def setter(*args):
                self.data[name[3:].lower()] = args[0] if len(args) == 1 else args
            return setter
        raise AttributeError(name)


class ListItem(object):
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label = label
        self.path = path
        self.art = {}
        self.props = {}
        self.info = {}
        self.context = []
        self.subtitles = []
        self.tag = _Tag()
        self.mime = None

    def getVideoInfoTag(self):
        return self.tag

    def setInfo(self, kind, data):
        self.info.update(data)

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, k, v):
        self.props[k] = v

    def addContextMenuItems(self, items, replaceItems=False):
        self.context.extend(items)

    def setSubtitles(self, subs):
        self.subtitles = subs

    def setMimeType(self, mime):
        self.mime = mime

    def setContentLookup(self, value):
        pass

    def setRating(self, *a, **k):
        pass

    def setCast(self, cast):
        pass

    def getLabel(self):
        return self.label


class Dialog(object):
    def notification(self, heading, message, icon=None, time=5000, sound=True):
        NOTIFICATIONS.append(message)

    def ok(self, heading, message):
        OK_DIALOGS.append(message)
        return True

    def yesno(self, heading, message, *a, **k):
        return YESNO_ANSWERS.pop(0) if YESNO_ANSWERS else False

    def select(self, heading, options, *a, **k):
        return SELECT_ANSWERS.pop(0) if SELECT_ANSWERS else -1

    def numeric(self, kind, heading, default=""):
        return NUMERIC_ANSWERS.pop(0) if NUMERIC_ANSWERS else ""

    def browse(self, *a, **k):
        return BROWSE_ANSWERS.pop(0) if BROWSE_ANSWERS else ""

    def textviewer(self, heading, text):
        OK_DIALOGS.append(text)


class DialogProgress(object):
    def create(self, heading, message=""):
        pass

    def update(self, percent, message=""):
        pass

    def iscanceled(self):
        return False

    def close(self):
        pass


class DialogProgressBG(DialogProgress):
    def update(self, percent=0, heading=None, message=None):
        pass
