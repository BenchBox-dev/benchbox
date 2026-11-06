from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pytest

from benchbox.utils import heavy_sentinel as hs, heavy_status, linux_machine as lm, sentinel_launchd

pytestmark = [pytest.mark.unit, pytest.mark.fast]

C = "/opt/homebrew/bin/container"
RUNNING = lm.MachineState("running", 8.0, True)
STOPPED = lm.MachineState("stopped", 8.0, True)
WRAPPER = (10, ("/bin/bash", "/r/scripts/linux_machine_run.sh", "m", "--", "x"))
RUN = (11, (C, "machine", "run", "-n", "m", "--", "x"))
FOREIGN = (50, (C, "machine", "run", "-n", "m"))
IDLE_SECONDS = 600.0


class FakeStop:
    def __init__(self, codes: list[int | None], output: str = "") -> None:
        self.codes = codes
        self.killed = False
        self.closed = False
        self._output = output

    def poll(self) -> int | None:
        return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

    def kill(self) -> None:
        self.killed = True

    def output(self) -> str:
        return self._output

    def close(self) -> None:
        self.closed = True


class FakeLock:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class Rig:
    def __init__(self, mode: str = "kill", machine: str = "m", state=RUNNING) -> None:
        self.now = 0.0
        self.lines: list[str] = []
        self.state = state
        self.procs: list[tuple[int, tuple[str, ...]]] = []
        self.held: frozenset[int] = frozenset()
        self.stops: list[str] = []
        self.handles: list[FakeStop] = []
        self.handle_codes: list[int | None] = [0]
        self.stop_lands = True
        self.stop_output = ""
        self.lock_free = True
        self.locks: list[FakeLock] = []
        self.events: list[tuple[str, dict]] = []
        self.state_calls: list[str] = []
        policy = hs.SentinelPolicy(mode=mode, budget_bytes=None, vm_machine=machine, vm_idle_seconds=IDLE_SECONDS)
        self.watch = hs.VmWatch(
            policy,
            log=self.lines.append,
            holder_reader=lambda: self.held,
            state_reader=self._state,
            process_reader=lambda m, d: list(self.procs),
            stopper=self._stop,
            clock=lambda: self.now,
            event_recorder=lambda event, **fields: self.events.append((event, fields)),
            lock_acquirer=self._lock,
        )

    def _lock(self, machine: str):
        if not self.lock_free:
            return None
        lock = FakeLock()
        self.locks.append(lock)
        return lock

    def _state(self, machine: str):
        self.state_calls.append(machine)
        return self.state

    def _stop(self, machine: str) -> FakeStop:
        self.stops.append(machine)
        handle = FakeStop(list(self.handle_codes), self.stop_output)
        self.handles.append(handle)
        if self.stop_lands:
            self.state = STOPPED
        return handle

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.watch.check()


def test_an_idle_machine_past_the_window_is_stopped_once() -> None:
    rig = Rig()
    rig.watch.check()
    assert rig.stops == []
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == ["m"]
    rig.advance(30)
    rig.advance(30)
    assert rig.stops == ["m"]
    assert any("stopped idle linux machine 'm'" in line for line in rig.lines)
    assert any("VM idle" in line for line in rig.lines)


def test_inside_the_window_nothing_is_stopped() -> None:
    rig = Rig()
    rig.watch.check()
    rig.advance(IDLE_SECONDS - 30)
    assert rig.stops == []


def test_a_holder_running_an_unrelated_command_does_not_protect_the_machine() -> None:
    rig = Rig()
    rig.held = frozenset({1, 2, 3})
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == ["m"]


@pytest.mark.parametrize(
    ("procs", "held"),
    [
        ([WRAPPER, RUN], frozenset({10, 11})),
        ([RUN], frozenset({11})),
        ([FOREIGN], frozenset()),
        ([WRAPPER], frozenset()),
    ],
)
def test_a_holder_tree_with_the_wrapper_or_a_live_matching_process_protects_the_machine(procs, held) -> None:
    rig = Rig()
    rig.procs = procs
    rig.held = held
    rig.watch.check()
    rig.advance(IDLE_SECONDS * 3)
    assert rig.stops == []


def test_a_matching_process_that_ends_restarts_the_idle_window() -> None:
    rig = Rig()
    rig.procs = [FOREIGN]
    rig.watch.check()
    rig.advance(IDLE_SECONDS * 2)
    rig.procs = []
    rig.advance(30)
    rig.advance(IDLE_SECONDS - 60)
    assert rig.stops == []
    rig.advance(120)
    assert rig.stops == ["m"]


def test_a_process_appearing_just_before_the_stop_cancels_it() -> None:
    rig = Rig()
    rig.watch.check()
    calls = {"n": 0}
    original = rig.watch._process_reader

    def late(machine: str, is_default: bool):
        calls["n"] += 1
        return [FOREIGN] if calls["n"] == 2 else original(machine, is_default)

    rig.watch._process_reader = late
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == []


def test_warn_mode_reports_and_stops_nothing() -> None:
    rig = Rig(mode="warn")
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == []
    assert [line for line in rig.lines if "VM idle" in line and "warn mode" in line] != []
    assert len([line for line in rig.lines if "VM idle" in line]) == 1


def test_off_mode_does_nothing() -> None:
    rig = Rig(mode="off")
    rig.advance(IDLE_SECONDS * 2)
    assert rig.state_calls == []


def test_a_stopped_or_unreadable_machine_is_left_alone() -> None:
    for state in (STOPPED, None):
        rig = Rig(state=state)
        rig.watch.check()
        rig.advance(IDLE_SECONDS * 2)
        assert rig.stops == []


def test_only_the_named_machine_is_read_and_stopped() -> None:
    rig = Rig(machine="m")
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    assert set(rig.state_calls) == {"m"}
    assert rig.stops == ["m"]


def test_a_stop_failure_is_logged_and_retried_only_after_the_next_window() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [1]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == ["m"]
    rig.advance(1)
    assert any("could not stop linux machine 'm': exit 1" in line for line in rig.lines)
    rig.advance(IDLE_SECONDS - 60)
    assert rig.stops == ["m"]
    rig.advance(120)
    assert rig.stops == ["m", "m"]


def test_a_stop_timeout_kills_the_stop_logs_and_retries_after_the_window() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [None]
    rig.stop_output = "Error: XPC timeout"
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(30)
    assert not rig.handles[0].killed
    rig.advance(40)
    assert rig.handles[0].killed
    assert any("timed out" in line and "Error: XPC timeout" in line for line in rig.lines)
    rig.advance(IDLE_SECONDS - 60)
    assert len(rig.stops) == 1
    rig.advance(120)
    assert len(rig.stops) == 2


def test_a_nonzero_stop_diagnoses_its_output_and_a_machine_already_stopped_counts_as_stopped() -> None:
    rig = Rig()
    rig.handle_codes = [1]
    rig.stop_output = "Error: machine is stopping"
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(1)
    assert any("reported exit 1 but the machine is 'stopped'" in line for line in rig.lines)
    assert any("Error: machine is stopping" in line for line in rig.lines)
    assert rig.events[-1][1]["outcome"] == "stopped_after_error"
    rig.advance(IDLE_SECONDS * 2)
    assert rig.stops == ["m"]


def test_exit_zero_with_the_machine_still_running_is_a_failure_not_a_success() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [0]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(1)
    assert any("exit 0; machine state 'running', not confirmed stopped" in line for line in rig.lines)
    assert not any("stopped idle linux machine" in line for line in rig.lines)
    assert rig.events[-1][1]["outcome"] == "still_running"


@pytest.mark.parametrize("after", [lm.MachineState("stopping", 8.0, True), None])
def test_a_transitional_or_unreadable_state_is_not_a_confirmed_stop_and_retries(after) -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [0]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.state = after
    rig.advance(1)
    assert any("could not stop linux machine" in line and "not confirmed stopped" in line for line in rig.lines)
    assert not any("stopped idle linux machine" in line for line in rig.lines)
    assert rig.events[-1][1]["outcome"] == "unverified"
    rig.state = RUNNING
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(IDLE_SECONDS + 1)
    assert len(rig.stops) == 2


def test_a_stop_that_times_out_but_lands_is_reconciled_to_the_actual_state() -> None:
    rig = Rig()
    rig.handle_codes = [None]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(61)
    assert rig.handles[0].killed
    assert rig.handles[0].closed
    assert any("timed out after 60s but the machine is 'stopped'" in line for line in rig.lines)
    assert not any("could not stop" in line for line in rig.lines)
    assert rig.events[-1][1]["outcome"] == "stopped_after_timeout"


def test_repeated_failures_escalate_to_stuck_but_keep_retrying_every_idle_window() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [1]
    rig.stop_output = "Error: vm unresponsive"
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    attempts = hs.VM_STOP_ESCALATE_FAILURES + 2
    for _ in range(attempts):
        rig.advance(1)
        rig.advance(IDLE_SECONDS + 1)
    assert len(rig.stops) == attempts + 1
    stuck = [line for line in rig.lines if "STUCK" in line]
    assert len(stuck) == attempts - hs.VM_STOP_ESCALATE_FAILURES + 1
    assert "no force-stop" in stuck[0]
    assert "container machine logs" in stuck[0]
    assert any("Error: vm unresponsive" in line for line in rig.lines)


def test_a_success_resets_the_consecutive_failure_count() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [1]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(1)
    assert rig.events[-1][1]["details"]["consecutive_failures"] == 1
    rig.stop_lands = True
    rig.handle_codes = [0]
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(1)
    assert rig.events[-1][1]["outcome"] == "stopped"
    assert rig.events[-1][1]["details"]["consecutive_failures"] == 0


def test_the_lock_is_still_held_while_the_final_state_is_read() -> None:
    rig = Rig()
    rig.handle_codes = [0]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    seen: list[bool] = []
    original = rig.watch._state_reader

    def reading(machine: str):
        seen.append(rig.locks[-1].released)
        return original(machine)

    rig.watch._state_reader = reading
    rig.advance(1)
    assert seen == [False]
    assert rig.locks[-1].released


def test_a_foreign_run_that_appears_after_the_idle_window_is_rechecked_with_fresh_state() -> None:
    rig = Rig()
    rig.watch.check()
    rig.state = lm.MachineState("running", 8.0, False)
    reads: list[bool] = []
    original = rig.watch._process_reader

    def reader(machine: str, is_default: bool):
        reads.append(is_default)
        return [FOREIGN] if len(reads) >= 2 else original(machine, is_default)

    rig.watch._process_reader = reader
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == []
    assert reads[-1] is False


def test_an_unguarded_run_is_reported_once_and_never_signalled() -> None:
    rig = Rig()
    rig.procs = [FOREIGN]
    rig.watch.check()
    rig.advance(30)
    rig.advance(30)
    assert [line for line in rig.lines if "UNGUARDED VM RUN pid 50" in line] != []
    assert len([line for line in rig.lines if "UNGUARDED" in line]) == 1


def test_a_guarded_run_is_not_reported_unguarded() -> None:
    rig = Rig()
    rig.procs = [WRAPPER, RUN]
    rig.held = frozenset({10, 11})
    rig.watch.check()
    assert not any("UNGUARDED" in line for line in rig.lines)


def test_the_sentinel_survives_a_failing_vm_check() -> None:
    lines: list[str] = []
    rig = Rig()

    def boom(machine: str):
        raise RuntimeError("x")

    rig.watch._state_reader = boom
    sentinel = hs.Sentinel(
        hs.SentinelPolicy(mode="kill", budget_bytes=None),
        log=lines.append,
        process_reader=list,
        worktree_lister=lambda: [Path("/work/BenchBox.wt-a")],
        pressure_reader=lambda: 0,
        sleeper=lambda seconds: None,
        git_reader=lambda roots: [],
        total_ram=lambda: 16 * 2**30,
        environ={},
        vm_watch=rig.watch,
    )
    sentinel.step()
    assert any("could not check the linux machine" in line for line in lines)


def test_policy_reads_the_machine_and_idle_window_from_the_environment() -> None:
    args = argparse.Namespace(interval=None)
    policy = hs.policy_from_env({"BENCHBOX_HEAVY_LINUX_MACHINE": "x", "BENCHBOX_HEAVY_VM_IDLE_MINUTES": "2"}, args)
    assert (policy.vm_machine, policy.vm_idle_seconds) == ("x", 120.0)
    default = hs.policy_from_env({}, args)
    assert (default.vm_machine, default.vm_idle_seconds) == ("benchbox-agent", 600.0)


def test_launchd_forwards_the_machine_settings() -> None:
    env = {"BENCHBOX_HEAVY_LINUX_MACHINE": "x", "BENCHBOX_HEAVY_VM_IDLE_MINUTES": "3", "OTHER": "1"}
    assert sentinel_launchd.forwarded_environment(env) == {
        "BENCHBOX_HEAVY_LINUX_MACHINE": "x",
        "BENCHBOX_HEAVY_VM_IDLE_MINUTES": "3",
    }


def _reading(vm) -> heavy_status.StatusReading:
    sentinel = heavy_status.SentinelState(False, None, Path("x/s.pid"), False)
    return heavy_status.StatusReading(Path("x/test.lock"), None, 0, None, None, (), (), sentinel, None, vm)


def test_status_flags_an_idle_running_vm_with_the_remedy() -> None:
    text = heavy_status.render(_reading(lm.assess("m", RUNNING, [], frozenset())))
    assert "WARNING VM running with no lock holder: machine 'm' state running, configured memory 8.00 GiB" in text
    assert "container machine stop m" in text


def test_status_does_not_flag_a_protected_or_stopped_vm() -> None:
    held = lm.assess("m", RUNNING, [WRAPPER, RUN], frozenset({10, 11}))
    assert "no lock holder" not in heavy_status.render(_reading(held))
    assert "no lock holder" not in heavy_status.render(_reading(lm.assess("m", STOPPED, [], frozenset())))
    assert "no lock holder" not in heavy_status.render(_reading(None))


def test_status_reports_an_unguarded_run() -> None:
    text = heavy_status.render(_reading(lm.assess("m", RUNNING, [FOREIGN], frozenset())))
    assert "UNGUARDED VM RUN pid 50" in text
    assert "no lock holder" not in text


def test_collect_reads_the_vm_through_the_injected_reader(tmp_path: Path) -> None:
    vm = lm.assess("m", RUNNING, [], frozenset())
    reading = heavy_status.collect(
        {"BENCHBOX_TEST_LOCK_DIR": str(tmp_path)},
        runner=lambda argv, timeout: "[]",
        pressure_reader=lambda: None,
        git_flood_reader=lambda: None,
        top_reader=lambda: (),
        vm_reader=lambda: vm,
        vm_attributor=FakeAttributor(),
    )
    assert reading.vm is vm


def test_the_default_vm_reading_is_absent_without_the_container_cli() -> None:
    assert heavy_status._read_vm({}, lambda argv, timeout: "[]", lambda name: None) is None


class FakeAttributor:
    def __init__(self, attributed=None, plain=None) -> None:
        self.attributed = attributed
        self.plain = plain
        self.calls: list[str] = []

    def attribute(self, held, machine, environ=None):
        self.calls.append("attribute")
        return self.attributed

    def read(self, machine, environ=None):
        self.calls.append("read")
        return self.plain


def _reading_of(gib: float):
    from benchbox.utils import vm_footprint

    return vm_footprint.VmReading("m", (7,), int(gib * 2**30), 7 * 2**30)


def test_a_holder_owned_vm_footprint_is_attributed() -> None:
    attr = FakeAttributor(attributed=_reading_of(5.0))
    found = heavy_status.read_vm_footprint(attr, "m", {1}, {}, "darwin")
    assert found is not None and found[1] is True
    assert "VM m footprint 5.00 GiB" in heavy_status.describe_vm_footprint(found)
    assert "[attributed to the lock holder]" in heavy_status.describe_vm_footprint(found)
    assert attr.calls == ["attribute"]


def test_an_unowned_vm_footprint_is_read_unattributed() -> None:
    attr = FakeAttributor(plain=_reading_of(5.0))
    found = heavy_status.read_vm_footprint(attr, "m", set(), {}, "darwin")
    assert found is not None and found[1] is False
    assert "[not attributed to a lock holder]" in heavy_status.describe_vm_footprint(found)
    assert attr.calls == ["attribute", "read"]


def test_the_vm_footprint_is_skipped_off_macos_or_when_unmeasured() -> None:
    attr = FakeAttributor(plain=_reading_of(5.0))
    assert heavy_status.read_vm_footprint(attr, "m", set(), {}, "linux") is None
    assert attr.calls == []
    assert heavy_status.read_vm_footprint(FakeAttributor(), "m", set(), {}, "darwin") is None


def test_status_prints_the_vm_footprint_line_only_for_a_running_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(heavy_status.sys, "platform", "darwin")
    attr = FakeAttributor(plain=_reading_of(5.0))
    kwargs = {
        "runner": lambda argv, timeout: "[]",
        "pressure_reader": lambda: None,
        "git_flood_reader": lambda: None,
        "top_reader": lambda: (),
        "vm_attributor": attr,
    }
    running = heavy_status.collect(
        {"BENCHBOX_TEST_LOCK_DIR": str(tmp_path)}, vm_reader=lambda: lm.assess("m", RUNNING, [], frozenset()), **kwargs
    )
    assert "VM m footprint 5.00 GiB" in heavy_status.render(running)
    attr.calls.clear()
    stopped = heavy_status.collect(
        {"BENCHBOX_TEST_LOCK_DIR": str(tmp_path)}, vm_reader=lambda: lm.assess("m", STOPPED, [], frozenset()), **kwargs
    )
    assert stopped.vm_line is None and attr.calls == []


def test_the_sentinel_logs_the_vm_footprint_when_it_changes() -> None:
    rig = Rig()
    lines: list[str | None] = ["VM m footprint 5.01 GiB x", "VM m footprint 5.04 GiB x", "VM m footprint 6.00 GiB x"]
    rig.watch._footprint_reader = lambda machine, held: lines.pop(0)
    for _ in range(3):
        rig.advance(30)
    assert [line for line in rig.lines if line.startswith("VM m footprint")] == [
        "VM m footprint 5.01 GiB x",
        "VM m footprint 6.00 GiB x",
    ]


def test_the_stop_waits_for_a_held_host_lock_and_never_blocks_the_sentinel() -> None:
    rig = Rig()
    rig.lock_free = False
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(IDLE_SECONDS)
    assert rig.stops == []
    assert len([line for line in rig.lines if "host lock is held by a run" in line]) == 1
    rig.lock_free = True
    rig.advance(30)
    assert rig.stops == ["m"]


def test_the_host_lock_is_held_for_the_whole_bounded_stop_and_released_after_it_is_reaped() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [None]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    assert [lock.released for lock in rig.locks] == [False]
    rig.advance(30)
    assert [lock.released for lock in rig.locks] == [False]
    rig.advance(40)
    assert rig.handles[0].killed
    assert [lock.released for lock in rig.locks] == [True]


def test_the_host_lock_is_released_when_the_stop_finishes_or_cannot_start() -> None:
    rig = Rig()
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(1)
    assert [lock.released for lock in rig.locks] == [True]

    def boom(machine: str) -> FakeStop:
        raise OSError("no container")

    failing = Rig()
    failing.watch._stopper = boom
    failing.watch.check()
    failing.advance(IDLE_SECONDS + 1)
    assert [lock.released for lock in failing.locks] == [True]
    assert any("no container" in line for line in failing.lines)


def test_a_run_that_started_while_the_lock_was_taken_cancels_the_stop_and_frees_the_lock() -> None:
    rig = Rig()
    rig.watch.check()
    calls = {"n": 0}
    original = rig.watch._process_reader

    def late(machine: str, is_default: bool):
        calls["n"] += 1
        return [FOREIGN] if calls["n"] >= 2 else original(machine, is_default)

    rig.watch._process_reader = late
    rig.advance(IDLE_SECONDS + 1)
    assert rig.stops == []
    assert [lock.released for lock in rig.locks] == [True]


def test_closing_the_sentinel_kills_an_inflight_stop_and_frees_the_lock() -> None:
    rig = Rig()
    rig.stop_lands = False
    rig.handle_codes = [None]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.watch.close()
    assert rig.handles[0].killed
    assert rig.handles[0].closed
    assert rig.events[-1][1]["outcome"] == "cancelled"
    assert [lock.released for lock in rig.locks] == [True]


def test_the_real_stop_process_is_group_killed_with_its_output_kept_and_no_leaked_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "container"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "Error: apiserver not responding"\n'
        f"sleep 300 & echo $! > {tmp_path}/worker.pid\n"
        "trap '' TERM\n"
        "wait\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(lm, "STOP_KILL_STEP_SECONDS", 0.3)
    handle = hs.start_machine_stop("m")
    deadline = time.monotonic() + 10
    while not (tmp_path / "worker.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    worker = int((tmp_path / "worker.pid").read_text(encoding="utf-8"))
    assert handle.poll() is None
    handle.kill()
    assert handle.poll() is not None
    assert "Error: apiserver not responding" in handle.output()
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(worker, 0)


def test_the_stop_lock_is_exclusive_and_visible_as_a_held_lock(tmp_path: Path) -> None:
    from benchbox.utils import host_lock

    path = tmp_path / "host.lock"
    first = hs.acquire_stop_lock(path, "m")
    assert first is not None
    assert host_lock.lock_is_held(path)
    assert hs.acquire_stop_lock(path, "m") is None
    first.release()
    assert not host_lock.lock_is_held(path)
    second = hs.acquire_stop_lock(path, "m")
    assert second is not None
    second.release()
