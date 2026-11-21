import os


def translatePath(path):
    return path


def exists(path):
    return os.path.exists(path)


def mkdirs(path):
    os.makedirs(path, exist_ok=True)
    return True


class File(object):
    def __init__(self, path, mode="r"):
        self.f = open(path, "w" if mode == "w" else "r", encoding="utf-8")

    def read(self):
        return self.f.read()

    def write(self, data):
        self.f.write(data)
        return True

    def close(self):
        self.f.close()
