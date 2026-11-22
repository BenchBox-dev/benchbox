from __future__ import annotations

import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from benchbox.core.exceptions import RunTimeoutError
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import (
    HARD_STOP_GRACE_SECONDS,
    TIMED_OUT_VALIDATION_STATUS,
    RunDeadline,
    active_run_deadline,
    arm_hard_stop,
    current_run_deadline,
    enter_run_phase,
    mark_timed_out,
    own_run_deadline,
    resolve_timeout_seconds,
    stop_requested_before_query,
    validate_timeout_seconds,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(run_deadline, "mono_time", fake)
    monkeypatch.setattr(run_deadline, "elapsed_seconds", lambda started: fake.now - started)
    return fake


@pytest.mark.parametrize(("value", "expected"), [(None, None), (0, None), (1, 1), (600, 600)])
def test_zero_and_unset_mean_no_limit(value, expected) -> None:
    assert validate_timeout_seconds(value) == expected


@pytest.mark.parametrize("value", [-1, 1.5, "30", True])
def test_negative_fractional_and_non_integer_values_are_refused(value) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        validate_timeout_seconds(value)


@pytest.mark.parametrize(
    ("explicit", "configured", "expected"),
    [(None, None, None), (None, 300, 300), (60, 300, 60), (0, 300, None), (None, 0, None)],
)
def test_an_explicit_value_wins_over_the_configured_one(explicit, configured, expected) -> None:
    assert resolve_timeout_seconds(explicit, configured) == expected


def test_the_hard_stop_grace_is_thirty_seconds() -> None:
    assert HARD_STOP_GRACE_SECONDS == 30


def test_a_deadline_expires_on_the_monotonic_clock_and_names_phase_and_query(clock) -> None:
    deadline = RunDeadline(10)
    deadline.enter_phase("execute")
    deadline.enter_query("Q7")
    clock.now += 9.5
    deadline.check()
    assert deadline.remaining() == pytest.approx(0.5)

    clock.now += 1
    with pytest.raises(RunTimeoutError) as excinfo:
        deadline.check()

    assert (excinfo.value.timeout_seconds, excinfo.value.phase, excinfo.value.query_id) == (10, "execute", "Q7")
    assert "10-second limit" in str(excinfo.value)
    assert "execute, at query Q7" in str(excinfo.value)
    assert deadline.timeout is excinfo.value


def test_the_first_recorded_timeout_is_kept(clock) -> None:
    deadline = RunDeadline(1)
    deadline.enter_phase("generate")
    clock.now += 2
    first = deadline.record_timeout("BenchBox stopped dsdgen")
    deadline.enter_phase("load")

    assert deadline.record_timeout() is first
    assert first.phase == "generate"


def test_no_active_deadline_means_no_check_and_no_stop_request() -> None:
    assert current_run_deadline() is None
    enter_run_phase("load")
    assert stop_requested_before_query("Q1") is False


def test_entering_a_phase_after_the_limit_raises_for_that_phase(clock) -> None:
    deadline = RunDeadline(5)
    with active_run_deadline(deadline):
        enter_run_phase("generate")
        clock.now += 6
        with pytest.raises(RunTimeoutError) as excinfo:
            enter_run_phase("load")
    assert excinfo.value.phase == "load"
    assert current_run_deadline() is None


def test_a_stop_request_before_a_query_records_that_query(clock) -> None:
    deadline = RunDeadline(5)
    with active_run_deadline(deadline):
        deadline.enter_phase("execute")
        assert stop_requested_before_query("Q1") is False
        clock.now += 6
        assert stop_requested_before_query("Q2") is True
    assert deadline.timeout is not None
    assert deadline.timeout.query_id == "Q2"


def test_an_outer_deadline_governs_a_nested_request() -> None:
    with own_run_deadline(60) as outer:
        assert current_run_deadline() is outer
        with own_run_deadline(1) as inner:
            assert inner is None
            assert current_run_deadline() is outer
    with own_run_deadline(0) as unset:
        assert unset is None


def test_marking_a_result_records_the_limit_phase_and_query(clock) -> None:
    deadline = RunDeadline(3)
    deadline.enter_phase("execute")
    deadline.enter_query("Q4")
    clock.now += 4
    result = SimpleNamespace(validation_status="PASSED", validation_details={"kept": True})

    mark_timed_out(result, deadline)

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert result.validation_details == {
        "kept": True,
        "timeout": {"timeout_seconds": 3, "phase": "execute", "query_id": "Q4"},
    }


def test_a_child_process_is_ended_at_the_remaining_time() -> None:
    deadline = RunDeadline(1)
    deadline.enter_phase("generate")

    with pytest.raises(RunTimeoutError) as excinfo:
        deadline.run(SLEEPER, tool="dsdgen")

    assert excinfo.value.detail == "BenchBox stopped dsdgen"
    assert excinfo.value.phase == "generate"
    assert deadline._children == set()


def test_a_child_is_not_started_after_the_limit(clock) -> None:
    deadline = RunDeadline(1)
    clock.now += 2
    with pytest.raises(RunTimeoutError, match="dbgen was not started"):
        deadline.run(SLEEPER, tool="dbgen")


def test_a_child_that_finishes_in_time_returns_its_output() -> None:
    deadline = RunDeadline(30)
    completed = deadline.run([sys.executable, "-c", "print('done')"], tool="qgen", capture_output=True, text=True)
    assert (completed.returncode, completed.stdout.strip()) == (0, "done")


def test_a_failing_child_raises_its_own_error_when_checked() -> None:
    deadline = RunDeadline(30)
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        deadline.run([sys.executable, "-c", "import sys; sys.exit(3)"], tool="dsqgen", check=True)
    assert excinfo.value.returncode == 3
    assert deadline.timeout is None


def test_the_hard_stop_ends_tracked_children_then_calls_back_after_the_grace() -> None:
    deadline = RunDeadline(1)
    stopped = threading.Event()
    child = subprocess.Popen(SLEEPER, start_new_session=sys.platform != "win32")
    with deadline.tracking(child):
        disarm = arm_hard_stop(deadline, lambda _deadline: stopped.set(), grace_seconds=0.2)
        try:
            assert stopped.wait(10)
            assert child.wait(10) != 0
            assert deadline.timeout is not None
            assert deadline.stopped(child)
        finally:
            disarm()


def test_a_run_that_finishes_in_time_disarms_the_hard_stop() -> None:
    deadline = RunDeadline(1)
    called = threading.Event()
    disarm = arm_hard_stop(deadline, lambda _deadline: called.set(), grace_seconds=0.1)
    disarm()
    assert called.wait(1.5) is False
    assert deadline.timeout is None


def test_a_run_that_returns_during_the_hard_stop_waits_for_it_to_finish() -> None:
    deadline = RunDeadline(1)
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def slow_hard_stop(_deadline: RunDeadline) -> None:
        entered.set()
        release.wait(10)
        order.append("hard stop finished")

    disarm = arm_hard_stop(deadline, slow_hard_stop, grace_seconds=0.1)
    assert entered.wait(10)
    threading.Timer(0.3, release.set).start()
    disarm()
    order.append("run returned")

    assert order == ["hard stop finished", "run returned"]


def test_a_run_that_stops_within_the_grace_is_not_hard_stopped() -> None:
    deadline = RunDeadline(1)
    called = threading.Event()
    disarm = arm_hard_stop(deadline, lambda _deadline: called.set(), grace_seconds=5)
    waited = threading.Event()
    for _ in range(200):
        if deadline.expired:
            break
        waited.wait(0.05)
    disarm()
    assert deadline.expired
    assert deadline.timeout is None
    assert called.wait(0.5) is False


def test_without_process_groups_the_direct_child_is_still_ended(monkeypatch) -> None:
    monkeypatch.delattr(os, "killpg")
    deadline = RunDeadline(1)
    stopped = threading.Event()
    child = subprocess.Popen(SLEEPER)
    with deadline.tracking(child):
        disarm = arm_hard_stop(deadline, lambda _deadline: stopped.set(), grace_seconds=0.2)
        try:
            assert stopped.wait(10)
            assert child.wait(10) != 0
        finally:
            disarm()
