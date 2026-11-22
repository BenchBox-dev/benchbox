from __future__ import annotations

import io
import os
import sys

import psutil
import pytest

from benchbox.utils import heavy_watchdog as hw, process_tree as pt, vm_footprint

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")]

GIB = 2**30
BUDGET_GIB = 8.0
BROWSER = hw.OutsideProcess(4242, "Browser", "Browser (arguments redacted)", int(6 * GIB))
EDITOR = hw.OutsideProcess(4343, "editor", "editor (arguments redacted)", int(2 * GIB))
CONTAINER = hw.OutsideProcess(5151, vm_footprint.VM_PROCESS_NAME, "", int(3 * GIB))
SECOND_CONTAINER = hw.OutsideProcess(5252, vm_footprint.VM_PROCESS_NAME, "", int(1 * GIB))


def tree_of(total_gib: float) -> pt.TreeSample:
    total = int(total_gib * GIB)
    member = pt.TreeMember(os.getpid(), os.getppid(), "python", psutil.Process().create_time(), 0, total)
    return pt.TreeSample(os.getpid(), (member,), total)


class Harness:
    def __init__(
        self,
        *,
        tree_gib: float,
        pressure: int | None,
        outside: hw.OutsideReading,
        swap: float | None,
        mode: str = "kill",
    ) -> None:
        self.out = io.StringIO()
        self.killed = 0
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self._read_fd = read_fd
        self.policy = hw.WatchPolicy(
            holder_pid=os.getpid(),
            holder_created=psutil.Process().create_time(),
            budget_bytes=int(BUDGET_GIB * GIB),
            mode=mode,
            kill_free_disk_gib=20.0,
            disks=(("boot volume", "/"),),
        )
        self.dog = hw.Watchdog(
            self.policy,
            sampler=lambda pid, exclude=(): tree_of(tree_gib),
            pressure_reader=lambda: pressure,
            disk_reader=lambda roots: [hw.DiskFree(label, path, 500.0) for label, path in roots],
            vm_reader=lambda sample: None,
            outside_reader=lambda sample: outside,
            swap_reader=lambda: swap,
            out=self.out,
            flag_fd=write_fd,
        )
        self.dog._kill_tree = lambda sample: setattr(self, "killed", self.killed + 1)

    def step(self) -> tuple[str, bytes]:
        assert self.dog.step() is True
        try:
            flags = os.read(self._read_fd, 64)
        except BlockingIOError:
            flags = b""
        return self.out.getvalue(), flags


def reading(*processes: hw.OutsideProcess, containers: tuple[hw.OutsideProcess, ...] = ()) -> hw.OutsideReading:
    return hw.OutsideReading(tuple(processes), containers)


class Proc:
    def __init__(self, pid: int, name: str, cmdline: list[str]) -> None:
        self.info = {"pid": pid, "name": name}
        self._cmdline = cmdline

    def cmdline(self) -> list[str]:
        return self._cmdline


def test_a_memory_pressure_breach_is_attributed_and_still_kills_the_tree() -> None:
    harness = Harness(tree_gib=0.5, pressure=4, outside=reading(BROWSER, EDITOR), swap=92.0)
    log, flags = harness.step()
    assert harness.killed == 1
    assert flags == b""
    assert "action: kill" in log
    assert "pid 4242 Browser 6.00 GiB" in log
    assert "holder tree 0.50 GiB (6% of the 8.00 GiB budget)" in log
    assert "swap used 92%" in log
    assert "not proof of cause" in log


def test_a_holder_caused_breach_does_not_read_outside_processes_and_kills() -> None:
    def forbidden(sample: pt.TreeSample) -> hw.OutsideReading:
        raise AssertionError("outside processes are read only on a memory pressure breach")

    harness = Harness(tree_gib=9.0, pressure=1, outside=reading(), swap=50.0)
    harness.dog._outside_reader = forbidden
    log, _ = harness.step()
    assert harness.killed == 1
    assert "BREACH tree footprint" in log
    assert "attribution" not in log


def test_a_warn_mode_breach_is_attributed_and_never_kills() -> None:
    harness = Harness(tree_gib=0.5, pressure=4, outside=reading(BROWSER), swap=92.0, mode="warn")
    log, _ = harness.step()
    assert harness.killed == 0
    assert "action: warn only" in log
    assert "pid 4242 Browser" in log


def test_no_breach_neither_kills_nor_reads_outside_processes() -> None:
    def forbidden(sample: pt.TreeSample) -> hw.OutsideReading:
        raise AssertionError("outside processes are read only on a memory pressure breach")

    harness = Harness(tree_gib=0.5, pressure=1, outside=reading(), swap=50.0)
    harness.dog._outside_reader = forbidden
    log, flags = harness.step()
    assert (log, flags, harness.killed) == ("", b"", 0)


def test_attribution_excludes_the_holder_tree_and_ranks_by_footprint(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes = {100: 1 * GIB, 200: 5 * GIB, 300: 3 * GIB, 400: 0}
    monkeypatch.setattr(hw.process_tree.footprint, "pid_footprint_bytes", lambda pid: sizes[pid])
    inside = pt.TreeSample(1, (pt.TreeMember(100, 1, "p100", 0.0, 1, GIB),), GIB)
    found = hw.read_outside_processes(
        inside, limit=2, process_iter=lambda attrs: [Proc(pid, f"p{pid}", [f"p{pid}"]) for pid in sizes]
    )
    assert [(item.pid, item.footprint_bytes) for item in found.processes] == [(200, 5 * GIB), (300, 3 * GIB)]
    assert found.containers == ()


def test_another_sessions_command_line_is_shown_as_its_executable_basename_with_arguments_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hw.process_tree.footprint, "pid_footprint_bytes", lambda pid: 2 * GIB)
    secret = "--token=hunter2 --password=s3cret"
    procs = [Proc(10, "node", ["/opt/tools/bin/node", "server.js", secret])]
    inside = pt.TreeSample(1, (), 0)
    found = hw.read_outside_processes(inside, process_iter=lambda attrs: procs)
    assert [item.command for item in found.processes] == ["node (arguments redacted)"]

    log = Harness(tree_gib=0.5, pressure=4, outside=found, swap=92.0).step()[0]
    assert "node (arguments redacted)" in log
    assert "hunter2" not in log
    assert "s3cret" not in log
    assert "server.js" not in log
    assert "/opt/tools/bin" not in log


def test_a_command_line_that_cannot_be_read_still_shows_only_the_name_and_the_redaction_note() -> None:
    class Denied(Proc):
        def cmdline(self) -> list[str]:
            raise psutil.AccessDenied(10)

    assert hw.redacted_command(Denied(10, "daemon", []), "daemon") == "daemon (arguments redacted)"
    assert hw.redacted_command(Proc(10, "daemon", []), "daemon") == "daemon (arguments redacted)"


def test_container_runtime_processes_are_set_apart_from_other_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes = {200: 5 * GIB, 501: 3 * GIB, 502: 1 * GIB}
    monkeypatch.setattr(hw.process_tree.footprint, "pid_footprint_bytes", lambda pid: sizes[pid])
    procs = [
        Proc(200, "Browser", ["Browser"]),
        Proc(501, vm_footprint.VM_PROCESS_NAME, ["vm", "--secret"]),
        Proc(502, vm_footprint.VM_PROCESS_NAME, ["vm"]),
    ]
    found = hw.read_outside_processes(pt.TreeSample(1, (), 0), process_iter=lambda attrs: procs)
    assert [item.pid for item in found.processes] == [200]
    assert [item.pid for item in found.containers] == [501, 502]


def test_containers_are_reported_on_their_own_line_and_counted_for_neither_side() -> None:
    harness = Harness(
        tree_gib=0.5,
        pressure=4,
        outside=reading(BROWSER, containers=(CONTAINER, SECOND_CONTAINER)),
        swap=92.0,
    )
    log, _ = harness.step()
    lines = log.splitlines()
    container_lines = [line for line in lines if "containers (origin unknown)" in line]
    assert len(container_lines) == 1
    assert "2 process(es), 4.00 GiB, pid 5151,5252" in container_lines[0]
    assert "counted for neither the holder tree nor the other sessions" in container_lines[0]
    other_session_lines = [line for line in lines if line.startswith("  pid ")]
    assert not any("5151" in line or "5252" in line for line in other_session_lines if "Browser" not in line)
    assert "holder tree 0.50 GiB" in log


def test_no_container_line_when_there_are_no_container_processes() -> None:
    log, _ = Harness(tree_gib=0.5, pressure=4, outside=reading(BROWSER), swap=92.0).step()
    assert "containers (origin unknown)" not in log
