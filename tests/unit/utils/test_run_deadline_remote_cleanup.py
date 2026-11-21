from __future__ import annotations

import threading

import pytest

from benchbox.utils.run_deadline import (
    NO_REMOTE_CLEANUP_WARNING,
    REMOTE_CLEANUP_WARNING,
    RunDeadline,
    active_run_deadline,
    register_remote_cleanup,
    remote_cleanup_warning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class Owner:
    pass


def test_each_registered_cleanup_runs_once_however_often_the_timeout_path_asks() -> None:
    deadline = RunDeadline(5)
    calls: list[str] = []
    first, second = Owner(), Owner()
    deadline.register_remote_cleanup(first, lambda: calls.append("first"))
    deadline.register_remote_cleanup(second, lambda: calls.append("second"))

    assert deadline.cancel_remote_work() == []
    assert deadline.cancel_remote_work() == []

    assert calls == ["first", "second"]


def test_an_owner_registered_twice_is_cancelled_once() -> None:
    deadline = RunDeadline(5)
    calls: list[str] = []
    owner = Owner()
    deadline.register_remote_cleanup(owner, lambda: calls.append("a"))
    deadline.register_remote_cleanup(owner, lambda: calls.append("b"))

    deadline.cancel_remote_work()

    assert calls == ["a"]


def test_a_failing_cleanup_is_reported_and_does_not_stop_the_others() -> None:
    deadline = RunDeadline(5)
    calls: list[str] = []

    def fails() -> None:
        raise RuntimeError("the service refused")

    deadline.register_remote_cleanup(Owner(), fails, "Onehouse")
    deadline.register_remote_cleanup(Owner(), lambda: calls.append("second"), "BigQuery")

    errors = deadline.cancel_remote_work()

    assert errors == ["Onehouse: RuntimeError: the service refused"]
    assert deadline.cleanup_errors == errors
    assert calls == ["second"]
    assert deadline.timeout is None


def test_a_cleanup_that_never_returns_is_reported_after_the_wait() -> None:
    deadline = RunDeadline(5)
    release = threading.Event()
    deadline.register_remote_cleanup(Owner(), lambda: release.wait(30), "Dataproc")
    try:
        errors = deadline.cancel_remote_work(wait_seconds=0.2)
    finally:
        release.set()

    assert errors == ["Remote cleanup did not finish within 0.2 seconds"]


def test_a_cleanup_registered_for_the_active_deadline_only() -> None:
    calls: list[str] = []
    register_remote_cleanup(Owner(), lambda: calls.append("no deadline"))
    deadline = RunDeadline(5)
    with active_run_deadline(deadline):
        register_remote_cleanup(Owner(), lambda: calls.append("active"))

    deadline.cancel_remote_work()

    assert calls == ["active"]


def test_the_warnings_are_the_owners_texts() -> None:
    assert remote_cleanup_warning("Onehouse Quanton", True) == (
        "A run time limit is set. If the run times out, BenchBox will try to cancel the work it started on "
        "Onehouse Quanton. This cleanup has not been checked against the live service, so work may keep running "
        "and billing."
    )
    assert remote_cleanup_warning("Snowflake", False) == (
        "A run time limit is set. If the run times out, work BenchBox started on Snowflake may keep running and "
        "billing: BenchBox cannot cancel it on this platform."
    )
    assert "{platform}" in REMOTE_CLEANUP_WARNING
    assert "{platform}" in NO_REMOTE_CLEANUP_WARNING
