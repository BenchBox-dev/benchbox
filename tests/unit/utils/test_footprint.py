from __future__ import annotations

import os
import subprocess
import sys

import pytest

from benchbox.utils import footprint

pytestmark = pytest.mark.fast


@pytest.fixture
def child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def test_footprint_of_live_child_is_positive(child):
    value = footprint.pid_footprint_bytes(child.pid)
    assert value is not None
    assert value > 0


def test_footprint_of_current_process_is_positive():
    assert (footprint.pid_footprint_bytes(os.getpid()) or 0) > 0


def test_footprint_of_dead_pid_is_none():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert footprint.pid_footprint_bytes(proc.pid) is None


def test_footprint_rejects_non_positive_pid():
    assert footprint.pid_footprint_bytes(0) is None
    assert footprint.pid_footprint_bytes(-5) is None


def test_top_processes_sorted_and_limited(child):
    rows = footprint.top_processes_by_footprint(5)
    assert 0 < len(rows) <= 5
    sizes = [row[2] for row in rows]
    assert sizes == sorted(sizes, reverse=True)
    assert all(isinstance(pid, int) and isinstance(name, str) and size >= 0 for pid, name, size in rows)


def test_top_processes_zero_limit_is_empty():
    assert footprint.top_processes_by_footprint(0) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="libproc only on macOS")
def test_darwin_reads_through_libproc(child):
    lib = footprint._libproc()
    assert lib is not None
    assert footprint._darwin_footprint(lib, child.pid) > 0
    assert any(pid == child.pid for pid, _, _ in footprint.top_processes_by_footprint(100000))
