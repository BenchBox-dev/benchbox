from __future__ import annotations

import json

import pytest

from benchbox.utils import linux_machine as lm

pytestmark = [pytest.mark.unit, pytest.mark.fast]

C = "/opt/homebrew/bin/container"


@pytest.mark.parametrize(
    ("argv", "default", "expected"),
    [
        ([C, "machine", "run", "-n", "m", "--", "true"], False, True),
        ([C, "machine", "run", "--name", "m", "-e", "A=1", "--", "true"], False, True),
        ([C, "machine", "run", "--name=m", "true"], False, True),
        ([C, "machine", "run", "-nm", "true"], False, True),
        ([C, "machine", "run", "-e", "A=m", "-n", "m", "--", "x"], False, True),
        ([C, "machine", "run", "--", "true"], True, True),
        ([C, "machine", "run"], True, True),
        ([C, "machine", "run", "--", "true"], False, False),
        ([C, "machine", "run", "-d", "--", "sleep", "9"], True, True),
        ([C, "machine", "run", "-d", "-n", "m", "sleep", "9"], False, True),
        ([C, "machine", "run", "-n", "other", "--", "true"], True, False),
        ([C, "machine", "run", "-n", "m2", "--", "true"], False, False),
        ([C, "machine", "run", "-n", "mm", "--", "true"], True, False),
        ([C, "machine", "run", "echo", "-n", "m"], False, False),
        ([C, "machine", "run", "--", "container", "machine", "run", "-n", "m"], False, False),
        ([C, "machine", "create", "img", "--name", "m", "--memory", "6G"], False, True),
        ([C, "machine", "create", "-n", "m", "img"], False, True),
        ([C, "machine", "create", "img"], True, False),
        ([C, "machine", "create", "img", "--name", "other"], True, False),
        ([C, "--debug", "machine", "run", "-n", "m"], False, True),
        ([C, "machine", "stop", "m"], True, False),
        ([C, "machine", "list"], True, False),
        ([C, "list"], True, False),
        (["python", "machine", "run", "-n", "m"], True, False),
        ([], True, False),
    ],
)
def test_targets_machine_matches_real_argv_shapes(argv: list[str], default: bool, expected: bool) -> None:
    assert lm.argv_targets_machine(argv, "m", default) is expected


def test_wrapper_argv_needs_a_shell_running_the_script_with_the_machine() -> None:
    assert lm.argv_runs_wrapper(["/bin/bash", "/r/scripts/linux_machine_run.sh", "m", "--", "x"], "m")
    assert not lm.argv_runs_wrapper(["/bin/bash", "/r/scripts/linux_machine_run.sh", "other", "--"], "m")
    assert not lm.argv_runs_wrapper(["/bin/sh", "-c", "bash scripts/linux_machine_run.sh m -- x"], "m")
    assert not lm.argv_runs_wrapper(["python", "/r/scripts/linux_machine_run.sh", "m"], "m")
    assert not lm.argv_runs_wrapper(["bash", "/r/scripts/linux_machine_run.sh"], "m")


def test_machine_name_reads_the_environment() -> None:
    assert lm.machine_name({}) == lm.DEFAULT_MACHINE
    assert lm.machine_name({lm.MACHINE_ENV: " "}) == lm.DEFAULT_MACHINE
    assert lm.machine_name({lm.MACHINE_ENV: "x"}) == "x"


LIST_JSON = json.dumps(
    [
        {"status": "running", "default": True, "memory": 8 * 2**30, "id": "m"},
        {"status": "stopped", "default": False, "memory": 2 * 2**30, "id": "other"},
    ]
)


def test_read_machine_state_parses_status_memory_and_default() -> None:
    seen: list[tuple[list[str], float]] = []

    def runner(argv, timeout):
        seen.append((list(argv), timeout))
        return LIST_JSON

    state = lm.read_machine_state("m", runner, lambda name: "/bin/container")
    assert state == lm.MachineState("running", 8.0, True)
    assert seen == [(["container", "machine", "list", "--format", "json"], 5.0)]
    other = lm.read_machine_state("other", runner, lambda name: "/bin/container")
    assert other == lm.MachineState("stopped", 2.0, False)


def test_read_machine_state_is_none_when_unavailable() -> None:
    assert lm.read_machine_state("m", lambda a, t: LIST_JSON, lambda n: None) is None
    assert lm.read_machine_state("m", lambda a, t: None, lambda n: "/c") is None
    assert lm.read_machine_state("m", lambda a, t: "not json", lambda n: "/c") is None
    assert lm.read_machine_state("absent", lambda a, t: LIST_JSON, lambda n: "/c") is None


def test_foreign_pid_skips_the_wrapper_and_its_descendants() -> None:
    table = [
        (10, ("/bin/bash", "/r/scripts/linux_machine_run.sh", "m", "--")),
        (11, (C, "machine", "run", "-n", "m")),
        (20, (C, "machine", "run", "-n", "m")),
    ]
    ancestors = {11: frozenset({10, 1}), 20: frozenset({5, 1})}
    found = lm.foreign_machine_pid("m", False, 10, reader=lambda m, d: table, ancestors=lambda p: ancestors[p])
    assert found == 20
    assert (
        lm.foreign_machine_pid("m", False, 10, reader=lambda m, d: table[:2], ancestors=lambda p: ancestors[p]) is None
    )


RUNNING = lm.MachineState("running", 6.0, False)
RUNNING_DEFAULT = lm.MachineState("running", 6.0, True)
STOPPED = lm.MachineState("stopped", 6.0, False)
NAMELESS_RUN = (77, (C, "machine", "run", "--", "uname"))


class StubStop:
    def __init__(self, code: int | None, output: str = "") -> None:
        self.code = code
        self._output = output
        self.killed = False
        self.closed = False

    def poll(self) -> int | None:
        return self.code

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self.closed = True

    def output(self) -> str:
        return self._output


class CleanupRig:
    def __init__(self) -> None:
        self.now = 0.0
        self.lines: list[str] = []
        self.events: list[tuple[str, dict]] = []
        self.states = [RUNNING, STOPPED]
        self.processes: list[tuple[int, tuple[str, ...]]] = []
        self.lost = False
        self.stop = StubStop(0)
        self.stops: list[str] = []
        self.process_defaults: list[bool] = []

    def _state(self, machine: str):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def _processes(self, machine: str, default: bool):
        self.process_defaults.append(default)
        return list(self.processes)

    def _stopper(self, machine: str) -> StubStop:
        self.stops.append(machine)
        return self.stop

    def _sleep(self, seconds: float) -> None:
        self.now += seconds

    def run(self, timeout: float = 60.0) -> str:
        deps = lm.CleanupDeps(
            state_reader=self._state,
            process_reader=self._processes,
            lock_checker=lambda: self.lost,
            stopper=self._stopper,
            recorder=lambda event, **fields: self.events.append((event, fields)),
            clock=lambda: self.now,
            sleeper=self._sleep,
        )
        return lm.cleanup_machine("m", 1, stop_timeout=timeout, emit=self.lines.append, deps=deps)


def test_cleanup_stops_an_unused_machine_and_records_both_events() -> None:
    rig = CleanupRig()
    assert rig.run() == "stopped"
    assert rig.stops == ["m"]
    assert [event for event, _ in rig.events] == ["vm_cleanup_started", "vm_cleanup_finished"]
    assert rig.events[-1][1]["outcome"] == "stopped"
    assert rig.stop.killed and rig.stop.closed


def test_cleanup_leaves_the_machine_alone_for_a_foreign_run_and_a_lost_lock() -> None:
    rig = CleanupRig()
    rig.processes = [(50, (C, "machine", "run", "-n", "m"))]
    assert rig.run() == "left_running"
    assert rig.stops == []
    assert any("in use by pid 50" in line for line in rig.lines)
    lost = CleanupRig()
    lost.lost = True
    assert lost.run() == "lock_lost"
    assert lost.stops == []
    assert lost.events[-1][1]["outcome"] == "lock_lost"


def test_cleanup_rechecks_nameless_default_runs_including_when_the_state_is_unreadable() -> None:
    rig = CleanupRig()
    rig.states = [None, STOPPED]
    rig.processes = [NAMELESS_RUN]
    assert rig.run() == "left_running"
    assert rig.process_defaults == [True]
    default = CleanupRig()
    default.states = [RUNNING_DEFAULT, STOPPED]
    default.processes = [NAMELESS_RUN]
    assert default.run() == "left_running"
    named = CleanupRig()
    named.processes = [NAMELESS_RUN]
    named.run()
    assert named.process_defaults == [False]


def test_a_stop_that_never_finishes_is_killed_at_the_bound_diagnosed_and_not_called_stopped() -> None:
    rig = CleanupRig()
    rig.stop = StubStop(None, "Error: XPC interrupted")
    rig.states = [RUNNING, RUNNING]
    assert rig.run(timeout=5.0) == "still_running"
    assert rig.now >= 5.0
    assert rig.stop.killed and rig.stop.closed
    text = "\n".join(rig.lines)
    assert "Error: XPC interrupted" in text
    assert "still running after 'container machine stop' (timed out after 5s)" in text
    assert "no force-stop" in text
    assert rig.events[-1][1]["outcome"] == "still_running"
    assert rig.events[-1][1]["duration_seconds"] >= 5.0


@pytest.mark.parametrize(
    ("code", "state", "outcome"),
    [
        (0, STOPPED, "stopped"),
        (1, STOPPED, "stopped_after_error"),
        (None, STOPPED, "stopped_after_timeout"),
        (0, RUNNING, "still_running"),
        (1, RUNNING, "still_running"),
        (0, lm.MachineState("stopping", 6.0, False), "unverified"),
        (0, None, "unverified"),
        (None, None, "unverified"),
    ],
)
def test_only_a_verified_stopped_state_counts_as_stopped(code, state, outcome) -> None:
    rig = CleanupRig()
    rig.stop = StubStop(code)
    rig.states = [RUNNING, state]
    assert rig.run(timeout=1.0) == outcome
    assert rig.events[-1][1]["outcome"] == outcome


def test_a_stop_that_cannot_start_is_an_error_outcome() -> None:
    rig = CleanupRig()

    def boom(machine: str):
        raise OSError("no container")

    deps = lm.CleanupDeps(
        state_reader=rig._state,
        process_reader=rig._processes,
        lock_checker=lambda: False,
        stopper=boom,
        recorder=lambda event, **fields: rig.events.append((event, fields)),
    )
    assert lm.cleanup_machine("m", 1, stop_timeout=1.0, emit=rig.lines.append, deps=deps) == "spawn_failed"
    assert any("no container" in line for line in rig.lines)


def test_an_interrupted_cleanup_still_reaps_the_stop_and_records_the_outcome() -> None:
    rig = CleanupRig()
    rig.stop = StubStop(None)

    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    deps = lm.CleanupDeps(
        state_reader=rig._state,
        process_reader=rig._processes,
        lock_checker=lambda: False,
        stopper=rig._stopper,
        recorder=lambda event, **fields: rig.events.append((event, fields)),
        sleeper=interrupt,
    )
    with pytest.raises(KeyboardInterrupt):
        lm.cleanup_machine("m", 1, stop_timeout=60.0, emit=rig.lines.append, deps=deps)
    assert rig.stop.killed and rig.stop.closed
    assert rig.events[-1][1]["outcome"] == "interrupted"


def test_a_run_that_lost_the_host_lock_is_detected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from benchbox.utils import host_lock

    monkeypatch.setenv("BENCHBOX_TEST_LOCK_DIR", str(tmp_path))
    monkeypatch.setenv(host_lock.OWNER_ENV, "123:1.00")
    assert lm.lock_lost()


def test_the_lock_is_not_lost_while_the_owner_still_holds_it(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from benchbox.utils import host_lock

    monkeypatch.setenv("BENCHBOX_TEST_LOCK_DIR", str(tmp_path))
    with host_lock.hold(host_lock.lock_path(), wait_seconds=0, phase="test"):
        monkeypatch.setenv(host_lock.OWNER_ENV, host_lock.process_identity())
        assert not lm.lock_lost()
        monkeypatch.setenv(host_lock.OWNER_ENV, "1:1.00")
        assert lm.lock_lost()


class StopRig:
    def __init__(self, states: list[str | None]) -> None:
        self.states = states
        self.calls: list[str] = []
        self.now = 0.0

    def read(self, machine: str) -> lm.MachineState | None:
        status = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return None if status is None else lm.MachineState(status, 6.0, False)

    def poweroff(self, machine: str, timeout: float) -> None:
        self.calls.append(f"poweroff {machine} {timeout:g}")

    def stop(self, machine: str) -> int:
        self.calls.append(f"stop {machine}")
        return 0

    def run(self, wait: float) -> int:
        def sleep(seconds: float) -> None:
            self.now += seconds

        return lm.stop_sequence(
            "m",
            wait,
            state_reader=self.read,
            request_poweroff=self.poweroff,
            machine_stop=self.stop,
            clock=lambda: self.now,
            sleeper=sleep,
        )


def test_a_guest_that_powers_off_needs_no_machine_stop() -> None:
    rig = StopRig(["running", "running", "running", "stopped"])
    assert rig.run(15) == 0
    assert rig.calls == [f"poweroff m {lm.POWEROFF_REQUEST_TIMEOUT_SECONDS:g}"]


def test_a_guest_that_stays_running_falls_back_to_machine_stop_after_the_wait() -> None:
    rig = StopRig(["running"])
    assert rig.run(3) == 0
    assert rig.calls == [f"poweroff m {lm.POWEROFF_REQUEST_TIMEOUT_SECONDS:g}", "stop m"]
    assert rig.now == 3.0


def test_an_unreadable_state_skips_the_poweroff_and_stops() -> None:
    rig = StopRig([None])
    assert rig.run(15) == 0
    assert rig.calls == ["stop m"]


def test_a_stopped_machine_is_neither_powered_on_nor_stopped() -> None:
    rig = StopRig(["stopped"])
    assert rig.run(15) == 0
    assert rig.calls == []


def test_a_pretty_printed_machine_list_is_still_recognised_as_running() -> None:
    text = json.dumps([{"id": "m", "config": {"nested": {"status": "x"}}, "status": "running"}], indent=2)
    state = lm.parse_machine_state(text, "m")
    assert state is not None and state.status == "running"


def test_the_stop_step_bounds_fit_inside_the_default_stop_timeout() -> None:
    worst_before_stop = (
        lm.PROCESS_TIMEOUT_SECONDS * 2 + lm.POWEROFF_REQUEST_TIMEOUT_SECONDS + lm.GUEST_POWEROFF_WAIT_SECONDS
    )
    assert worst_before_stop <= lm.stop_timeout_seconds({}) - 20


class _FakeProcess:
    def __init__(self, error: BaseException | None, argv: list[str]) -> None:
        self.error = error
        self.argv = argv

    def as_dict(self, attrs: list[str], ad_value: object = None) -> dict[str, object]:
        if self.error is not None:
            raise self.error
        return {"pid": 7, "name": "container", "uids": None, "cmdline": self.argv}


def test_a_process_that_cannot_be_read_does_not_abort_the_machine_scan() -> None:
    import psutil

    wrapper = ["bash", "scripts/linux_machine_run.sh", "m", "--", "true"]
    processes = [
        _FakeProcess(SystemError("proc_cmdline returned a result with an exception set"), []),
        _FakeProcess(psutil.NoSuchProcess(3), []),
        _FakeProcess(OSError("gone"), []),
        _FakeProcess(None, wrapper),
    ]
    assert lm.read_machine_processes("m", False, iterate=lambda: iter(processes)) == [(7, tuple(wrapper))]


