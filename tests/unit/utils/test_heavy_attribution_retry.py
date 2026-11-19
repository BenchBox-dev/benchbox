from __future__ import annotations

import io
import os
import sys

import psutil
import pytest

from benchbox.utils import heavy_watchdog as hw, host_lock as hl, process_tree as pt
from benchbox.utils.vm_footprint import VmReading

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")]

GIB = 2**30
BUDGET_GIB = 8.0
BROWSER = hw.OutsideProcess(4242, "Browser", "/Applications/Browser --renderer", int(6 * GIB))
EDITOR = hw.OutsideProcess(4343, "editor", "editor --serve", int(2 * GIB))


def tree_of(total_gib: float) -> pt.TreeSample:
    total = int(total_gib * GIB)
    member = pt.TreeMember(os.getpid(), os.getppid(), "python", psutil.Process().create_time(), 0, total)
    return pt.TreeSample(os.getpid(), (member,), total)


class Harness:
    def __init__(
        self,
        *,
        retry: bool,
        tree_gib: float,
        pressure: int | None,
        outside: list,
        swap: float | None,
        vm: VmReading | None = None,
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
            mode="kill",
            kill_free_disk_gib=20.0,
            disks=(("boot volume", "/"),),
            retry_idempotent=retry,
        )
        self.free_gib = 500.0
        self.dog = hw.Watchdog(
            self.policy,
            sampler=lambda pid, exclude=(): tree_of(tree_gib),
            pressure_reader=lambda: pressure,
            disk_reader=lambda roots: [hw.DiskFree(label, path, self.free_gib) for label, path in roots],
            vm_reader=lambda sample: vm,
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


def test_foreign_heavy_breach_of_a_small_tree_is_stopped_for_retry_when_opted_in() -> None:
    log, flags = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[BROWSER, EDITOR], swap=92.0).step()
    assert flags == hw.FLAG_RETRYABLE
    assert "action: stop for bounded retry" in log
    assert "pid 4242 Browser 6.00 GiB" in log
    assert "holder tree 0.50 GiB (6% of the 8.00 GiB budget)" in log
    assert "not proof of cause" in log


def test_the_same_breach_without_opt_in_is_killed_without_a_retry_flag_but_still_attributed() -> None:
    harness = Harness(retry=False, tree_gib=0.5, pressure=4, outside=[BROWSER], swap=92.0)
    log, flags = harness.step()
    assert flags == b""
    assert harness.killed == 1
    assert "action: kill" in log
    assert "pid 4242 Browser" in log


def test_a_holder_caused_breach_is_never_retryable() -> None:
    harness = Harness(retry=True, tree_gib=9.0, pressure=4, outside=[BROWSER], swap=92.0)
    log, flags = harness.step()
    assert flags == b""
    assert harness.killed == 1
    assert "BREACH tree footprint" in log
    assert "stop for bounded retry" not in log


def test_a_breach_driven_by_the_holders_own_vm_is_never_retryable() -> None:
    vm = VmReading("benchbox-agent", (BROWSER.pid,), BROWSER.footprint_bytes, None)
    harness = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[BROWSER, EDITOR], swap=92.0, vm=vm)
    log, flags = harness.step()
    assert flags == b""
    assert harness.killed == 1
    assert "stop for bounded retry" not in log


def test_a_large_tree_under_critical_pressure_is_not_retryable_even_with_foreign_load() -> None:
    harness = Harness(retry=True, tree_gib=3.0, pressure=4, outside=[BROWSER], swap=92.0)
    _, flags = harness.step()
    assert flags == b""
    assert harness.killed == 1


def test_a_small_tree_beside_small_foreign_usage_is_not_retryable() -> None:
    small = hw.OutsideProcess(1, "tiny", "tiny", int(0.3 * GIB))
    _, flags = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[small], swap=92.0).step()
    assert flags == b""


def test_disk_exhaustion_is_never_retryable() -> None:
    harness = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[BROWSER], swap=92.0)
    harness.free_gib = 5.0
    log, flags = harness.step()
    assert flags == b""
    assert "BREACH free disk" in log
    assert harness.killed == 1


def test_swap_exhaustion_is_never_retryable() -> None:
    _, flags = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[BROWSER], swap=99.5).step()
    assert flags == b""


def test_unmeasured_swap_is_never_retryable() -> None:
    _, flags = Harness(retry=True, tree_gib=0.5, pressure=4, outside=[BROWSER], swap=None).step()
    assert flags == b""


def test_no_breach_neither_stops_nor_reads_outside_processes() -> None:
    def forbidden(sample: pt.TreeSample) -> list:
        raise AssertionError("outside processes are read only on a memory pressure breach")

    harness = Harness(retry=True, tree_gib=0.5, pressure=1, outside=[], swap=50.0)
    harness.dog._outside_reader = forbidden
    log, flags = harness.step()
    assert (log, flags, harness.killed) == ("", b"", 0)


def test_attribution_excludes_the_holder_tree_and_ranks_by_footprint(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        def __init__(self, pid: int, name: str) -> None:
            self.info = {"pid": pid, "name": name}

        def cmdline(self) -> list[str]:
            return [self.info["name"], "--flag"]

    sizes = {100: 1 * GIB, 200: 5 * GIB, 300: 3 * GIB, 400: 0}
    monkeypatch.setattr(hw.process_tree.footprint, "pid_footprint_bytes", lambda pid: sizes[pid])
    inside = pt.TreeSample(1, (pt.TreeMember(100, 1, "p100", 0.0, 1, GIB),), GIB)
    found = hw.read_outside_processes(
        inside, limit=2, process_iter=lambda attrs: [Proc(pid, f"p{pid}") for pid in sizes]
    )
    assert [(item.pid, item.footprint_bytes) for item in found] == [(200, 5 * GIB), (300, 3 * GIB)]
    assert found[0].command == "p200 --flag"


def guard_with(flags: bytes) -> hl.Guard:
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    os.write(write_fd, flags)
    os.close(write_fd)
    return hl.Guard(None, read_fd)


def test_guard_reports_retryable_only_when_the_holder_was_not_terminated() -> None:
    assert guard_with(b"KR").retryable is True
    assert guard_with(b"K").retryable is False
    assert guard_with(b"KRH").retryable is False


class Attempts:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[int, bool]]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.now = 0.0
        monkeypatch.setattr(hl, "_run_attempt", self.attempt)
        monkeypatch.setattr(hl, "_now", lambda: self.now)

    def attempt(self, command, **kwargs):
        self.calls.append(kwargs)
        self.now += 60.0
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def test_an_opted_in_command_is_retried_after_a_retryable_stop_until_it_succeeds(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(79, True), (79, True), (0, False)])
    assert hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True, retry_max=2) == 0
    assert len(attempts.calls) == 3
    assert all(call["environ"][hl.RETRY_ENV] == "1" for call in attempts.calls)
    assert attempts.calls[1]["admit_wait_seconds"] > 0


def test_retries_stop_at_the_attempt_bound(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(79, True)])
    assert hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True, retry_max=2) == 79
    assert len(attempts.calls) == 3


def test_retries_stop_at_the_total_time_bound(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(79, True)])
    code = hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True, retry_max=10, retry_total_seconds=100.0)
    assert code == 79
    assert len(attempts.calls) == 2


def test_each_retry_waits_only_for_the_retry_time_still_left(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(79, True)])
    hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True, retry_max=3, retry_total_seconds=1800.0)
    waits = [call["admit_wait_seconds"] for call in attempts.calls[1:]]
    assert waits == [1740.0, 1680.0, 1620.0]


def test_a_retry_never_waits_longer_than_the_admission_wait_limit(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(79, True)])
    hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True, retry_max=1, retry_total_seconds=30000.0)
    assert attempts.calls[1]["admit_wait_seconds"] == hl.ADMIT_WAIT_MAX_MINUTES * 60.0


def test_a_non_idempotent_command_is_run_once_and_never_receives_the_retry_opt_in(monkeypatch) -> None:
    monkeypatch.setenv(hl.RETRY_ENV, "1")
    attempts = Attempts(monkeypatch, [(79, True)])
    assert hl.run_command(["x"], admit=True, guard=True) == 79
    assert len(attempts.calls) == 1
    assert hl.RETRY_ENV not in attempts.calls[0]["environ"]


def test_a_non_retryable_failure_is_returned_without_a_second_attempt(monkeypatch) -> None:
    attempts = Attempts(monkeypatch, [(3, False)])
    assert hl.run_command(["x"], admit=True, guard=True, retry_idempotent=True) == 3
    assert len(attempts.calls) == 1


def test_retry_requires_admission_and_guard() -> None:
    with pytest.raises(SystemExit) as raised:
        hl.main(["run", "--retry-idempotent", "--", "true"])
    assert raised.value.code == 2


def test_the_watchdog_and_the_wrapper_agree_on_the_opt_in_and_flag_names() -> None:
    assert hw.RETRY_ENV == hl.RETRY_ENV
    assert hw.FLAG_RETRYABLE == hl.WATCHDOG_FLAG_RETRYABLE
    assert hw.FLAG_HOLDER_TERMINATED == hl.WATCHDOG_FLAG_HOLDER


def test_the_watchdog_policy_enables_retry_only_from_the_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    args = hw.build_parser().parse_args(["--holder-pid", "1", "--holder-created", "0.0"])
    monkeypatch.delenv(hw.RETRY_ENV, raising=False)
    assert hw.policy_from_args(args).retry_idempotent is False
    monkeypatch.setenv(hw.RETRY_ENV, "1")
    assert hw.policy_from_args(args).retry_idempotent is True
    monkeypatch.setenv(hw.RETRY_ENV, "yes")
    assert hw.policy_from_args(args).retry_idempotent is False
