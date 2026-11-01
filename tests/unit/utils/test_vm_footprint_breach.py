from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import types
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
def test_the_attribution_path_reads_the_allowance_without_importing_the_benchbox_package(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "container"
    stub.write_text(
        '#!/bin/sh\necho \'[{"id":"other","memory":1073741824},{"id":"m","memory":8589934592,"status":"running"}]\'\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    probe = Path(__file__).with_name("watchdog_isolation_probe.py")
    result = subprocess.run(
        [sys.executable, "-I", str(probe), str(Path(hw.__file__).resolve())],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["loaded"] == []
    assert report["allowance_gib"] == pytest.approx(8 * 1.1)


def test_a_raising_configured_reader_leaves_the_watchdog_running_with_an_unmeasured_allowance() -> None:
    def boom(machine: str) -> float:
        raise RuntimeError("container failed")

    attr = vf.VmAttributor(
        process_lister=lambda: [vf.VmProcess(1, 1.0)],
        open_paths=lambda pid: ["/x/machine-apiserver/machines/m/rootfs.ext4"],
        footprint_reader=lambda pid: 9 * GIB,
        configured_gib=boom,
        cmdline_reader=lambda pid: ["bash", "scripts/linux_machine_run.sh", "m"],
    )
    reading = attr.attribute([1], "m", {})
    assert reading is not None and reading.allowance_bytes is None
    assert vf.vm_breach_detail(reading) is None


def test_the_stdlib_configured_reader_returns_none_without_the_container_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert vf.read_configured_gib("m") is None


def test_a_raising_vm_reader_never_stops_the_default_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom(self, tree_pids, machine, environ=None):
        raise RuntimeError("lsof failed")

    monkeypatch.setattr(vf.VmAttributor, "attribute", boom)
    read = hw.default_vm_reader()
    sample = types.SimpleNamespace(members=[types.SimpleNamespace(pid=1)])
    assert read(sample) is None
