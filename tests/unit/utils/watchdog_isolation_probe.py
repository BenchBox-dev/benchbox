from __future__ import annotations

import json
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

vm = namespace["vm_footprint"]
attributor = vm.VmAttributor(
    process_lister=lambda: [vm.VmProcess(1, 1.0)],
    open_paths=lambda pid: ["/x/machine-apiserver/machines/m/rootfs.ext4"],
    footprint_reader=lambda pid: 5 * vm.GIB,
    cmdline_reader=lambda pid: ["bash", "scripts/linux_machine_run.sh", "m"],
)
reading = attributor.attribute([os.getpid()], "m", {})
allowance = None if reading is None or reading.allowance_bytes is None else reading.allowance_bytes / vm.GIB
loaded = sorted(name for name in sys.modules if name == "benchbox" or name.startswith("benchbox."))
print(json.dumps({"allowance_gib": allowance, "loaded": loaded}))
