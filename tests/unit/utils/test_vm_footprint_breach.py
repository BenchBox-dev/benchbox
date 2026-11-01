from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from benchbox.utils import heavy_watchdog as hw, vm_footprint as vf

pytestmark = [pytest.mark.unit, pytest.mark.medium]

GIB = 2**30


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_a_vm_breach_terminates_the_holder_tree_but_not_the_holder(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    holder = subprocess.Popen(
        ["sh", "-c", 'sleep 60 & echo $! > "$1"; while :; do sleep 0.2; done', "sh", str(pidfile)],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text(encoding="utf-8").strip()):
            time.sleep(0.05)
        child = int(pidfile.read_text(encoding="utf-8"))
        pol = hw.WatchPolicy(
            holder_pid=holder.pid,
            holder_created=psutil.Process(holder.pid).create_time(),
            budget_bytes=0,
            mode="kill",
            kill_free_disk_gib=0.0,
            grace_seconds=5.0,
        )
        vm = vf.VmReading("benchbox-agent", (5,), 9 * GIB, int(8.8 * GIB))
        out = io.StringIO()
        dog = hw.Watchdog(pol, pressure_reader=lambda: 1, disk_reader=lambda roots: [], vm_reader=lambda s: vm, out=out)
        dog.step()

        def gone() -> bool:
            return not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not gone():
            time.sleep(0.05)
        assert gone()
        assert holder.poll() is None
        assert f"sent SIGTERM to pid {child}" in out.getvalue()
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_the_watchdog_script_does_not_import_the_benchbox_package() -> None:
    probe = Path(__file__).with_name("watchdog_isolation_probe.py")
    result = subprocess.run(
        [sys.executable, "-I", str(probe), str(Path(hw.__file__).resolve())],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"
