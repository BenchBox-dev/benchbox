from __future__ import annotations

import os
import runpy
import sys
import types
from pathlib import Path

WATCHDOG = Path(sys.argv[1])
namespace = runpy.run_path(str(WATCHDOG), run_name="probe")
read = namespace["default_vm_reader"]()
sample = types.SimpleNamespace(members=[types.SimpleNamespace(pid=os.getpid())])
assert read(sample) is None
print(sorted(name for name in sys.modules if name == "benchbox" or name.startswith("benchbox.")))
