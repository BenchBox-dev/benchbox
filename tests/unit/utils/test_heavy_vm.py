from __future__ import annotations

import argparse
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
    def __init__(self, codes: list[int | None]) -> None:
        self.codes = codes
        self.killed = False

    def poll(self) -> int | None:
        return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

    def kill(self) -> None:
        self.killed = True


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
        )

    def _state(self, machine: str):
        self.state_calls.append(machine)
        return self.state

    def _stop(self, machine: str) -> FakeStop:
        self.stops.append(machine)
        handle = FakeStop(list(self.handle_codes))
        self.handles.append(handle)
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
    rig.handle_codes = [None]
    rig.watch.check()
    rig.advance(IDLE_SECONDS + 1)
    rig.advance(30)
    assert not rig.handles[0].killed
    rig.advance(40)
    assert rig.handles[0].killed
    assert any("timed out" in line for line in rig.lines)
    rig.advance(IDLE_SECONDS - 60)
    assert len(rig.stops) == 1
    rig.advance(120)
    assert len(rig.stops) == 2


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
