from __future__ import annotations

import sys

from . import area_names, write_recorded

for name in sys.argv[1:] or area_names():
    write_recorded(name)
    print(f"recorded {name}")
