# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import plugin  # noqa: E402

if __name__ == "__main__":
    plugin.run(sys.argv)
