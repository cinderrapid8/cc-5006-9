"""Pytest bootstrap: make user-site dependencies importable under `python -I`."""

import glob
import os
import site
import sys

for _candidate in glob.glob(os.path.expanduser("~/.local/lib/python*/site-packages")):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        site.addsitedir(_candidate)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
