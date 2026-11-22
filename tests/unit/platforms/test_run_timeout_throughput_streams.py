from __future__ import annotations

import threading

import pytest

from benchbox.utils.run_deadline import RunDeadline, active_run_deadline
from tests.unit.platforms.test_run_timeout_execution_phases import (
    _run_tpcds_stream_driver,
    _run_tpch_stream_driver,
    _StreamDriver,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestThroughputStopsAtTheDeadline:
    def test_tpch_streams_stop_at_the_deadline_with_the_in_flight_query_marked(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, rows = _run_tpch_stream_driver(driver)
        finally:
            gate.set()

        assert deadline.timeout is not None
        assert sorted(result.outstanding_stream_ids) == [1, 2]
        timed_out = [row for row in rows if row["status"] == "TIMEOUT"]
        assert sorted(row["stream_id"] for row in timed_out) == [1, 2]
        assert len(rows) == 2
        assert len(driver.calls) == 2

    def test_tpcds_streams_stop_at_the_deadline_with_the_in_flight_query_marked(self, monkeypatch) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, rows = _run_tpcds_stream_driver(monkeypatch, driver)
        finally:
            gate.set()

        assert deadline.timeout is not None
        assert sorted(result.outstanding_stream_ids) == [1, 2]
        assert sorted((row["stream_id"], row["status"]) for row in rows) == [(1, "TIMEOUT"), (2, "TIMEOUT")]
        assert len(driver.calls) == 2

    def test_the_stream_events_are_set_at_the_deadline_with_cancel_on_timeout_off(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, _rows = _run_tpch_stream_driver(driver, cancel_on_timeout=False)
        finally:
            gate.set()

        assert result.config.cancel_on_timeout is False
        events = result.config._stream_cancel_events
        assert sorted(events) == [1, 2]
        assert all(event.is_set() for event in events.values())

    def test_a_stream_stops_before_its_next_query_once_the_event_is_set(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, _rows = _run_tpch_stream_driver(driver)
            driver.calls.clear()
            gate.set()
            for future in list(result._outstanding_futures.values()):
                future.result(timeout=10)
        finally:
            gate.set()

        assert driver.calls == []

    def test_a_user_stream_limit_leaves_the_events_alone_without_cancel_on_timeout(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)

        try:
            result, rows = _run_tpch_stream_driver(driver, stream_timeout_seconds=1)
        finally:
            gate.set()

        assert sorted(result.outstanding_stream_ids) == [1, 2]
        assert all(not event.is_set() for event in result.config._stream_cancel_events.values())
        assert [row["status"] for row in rows] == []

    def test_a_deadline_shorter_than_the_stream_limit_is_the_limit_that_applies(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, _rows = _run_tpch_stream_driver(driver, stream_timeout_seconds=600)
        finally:
            gate.set()

        assert any("run time limit" in error for error in result.errors)
        assert all(event.is_set() for event in result.config._stream_cancel_events.values())

    def test_a_stream_limit_shorter_than_the_deadline_is_the_limit_that_applies(self) -> None:
        gate = threading.Event()
        driver = _StreamDriver(gate)
        deadline = RunDeadline(600)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                result, _rows = _run_tpch_stream_driver(driver, stream_timeout_seconds=1)
        finally:
            gate.set()

        assert any("timed out after 1" in error for error in result.errors)
        assert deadline.timeout is None
