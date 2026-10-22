from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.medium]

SCRIPT = """
import json, os, time
from benchbox.platforms.base import client_region as c
out = {"cpu": os.cpu_count()}
for name in ("_probe_aws_imds", "_probe_gcp_imds", "_probe_azure_imds"):
    t = time.perf_counter()
    try:
        r = getattr(c, name)()
    except Exception as e:
        r = repr(e)
    out[name] = [round(time.perf_counter() - t, 4), r]
c.reset_client_region_cache()
t = time.perf_counter()
r = c.discover_client_region({})
out["total"] = round(time.perf_counter() - t, 4)
out["result"] = r
print("PROBE_TIMING " + json.dumps(out))
"""


@pytest.mark.parametrize("i", range(3))
def test_region_probe_timing(i, record_property):
    p = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120)
    line = [x for x in p.stdout.splitlines() if x.startswith("PROBE_TIMING")]
    msg = line[0] if line else p.stdout + p.stderr
    record_property("probe_timing", msg)
    sys.__stderr__.write(msg + "\n")
