ITEMS = []
RESOLVED = []
ENDED = []
CONTENT = []

SORT_METHOD_NONE = 0
SORT_METHOD_LABEL = 1
SORT_METHOD_LABEL_IGNORE_THE = 2
SORT_METHOD_DATE = 3
SORT_METHOD_VIDEO_YEAR = 18
SORT_METHOD_VIDEO_RATING = 19
SORT_METHOD_DATEADDED = 21
SORT_METHOD_GENRE = 16
SORT_METHOD_UNSORTED = 40
SORT_METHOD_EPISODE = 24


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
    ITEMS.append((url, listitem, isFolder))
    return True


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
    ENDED.append(succeeded)


def setResolvedUrl(handle, succeeded, listitem):
    RESOLVED.append((succeeded, listitem))


def setContent(handle, content):
    CONTENT.append(content)


def addSortMethod(handle, method, label2Mask=""):
    pass


def addDirectoryItems(handle, items, totalItems=0):
    for url, listitem, is_folder in items:
        ITEMS.append((url, listitem, is_folder))
    return True
