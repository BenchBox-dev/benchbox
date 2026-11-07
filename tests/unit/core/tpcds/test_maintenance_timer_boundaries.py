from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS, MAINTENANCE_FUNCTIONS_BY_NAME
from benchbox.core.tpcds.maintenance_test import (
    TPCDSMaintenanceOperation,
    TPCDSMaintenanceTest,
    TPCDSMaintenanceTestConfig,
)
from benchbox.core.tpcds.refresh_data import SOURCE_TABLES, RefreshSet

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LINEITEM_ROWS = 7


class _Cursor:
    def __init__(self, value: int) -> None:
        self._value = value

    def fetchone(self) -> tuple[int]:
        return (self._value,)


class _RecordingConnection:
    def __init__(self, events: list[str], inserted_rows: int = LINEITEM_ROWS, fail_statement: bool = False) -> None:
        self.events = events
        self.counts: dict[str, int] = {}
        self.inserted_rows = inserted_rows
        self.fail_statement = fail_statement

    def execute(self, sql: str) -> _Cursor | None:
        if sql.startswith("SELECT COUNT(*) FROM "):
            table = sql.removeprefix("SELECT COUNT(*) FROM ")
            self.events.append(f"count:{table}")
            return _Cursor(self.counts.get(table, 100))
        if sql == "BEGIN":
            self.events.append("begin")
        elif sql.startswith("DROP TABLE IF EXISTS "):
            self.events.append("drop:" + sql.removeprefix("DROP TABLE IF EXISTS "))
        elif sql.startswith("CREATE TABLE "):
            self.events.append("create:" + sql.removeprefix("CREATE TABLE ").split(" ", 1)[0])
        else:
            self.events.append("statement")
            if self.fail_statement:
                raise RuntimeError("statement boom")
            if sql.startswith("INSERT INTO "):
                table = sql.removeprefix("INSERT INTO ").split(" ", 1)[0]
                self.counts[table] = self.counts.get(table, 100) + self.inserted_rows
            else:
                table = sql.removeprefix("DELETE FROM ").split(" ", 1)[0]
                self.counts[table] = self.counts.get(table, 100) - 3
        return None

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def _refresh_set(set_id: int = 1, lineitem_rows: int = LINEITEM_ROWS) -> RefreshSet:
    files = {table.name: Path(f"/refresh/{table.file_stem}_{set_id}.dat") for table in SOURCE_TABLES}
    counts = {table.name: lineitem_rows for table in SOURCE_TABLES}
    return RefreshSet(set_id=set_id, directory=Path("/refresh"), files=files, row_counts=counts)


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, **connection_options: Any) -> None:
        self.events: list[str] = []
        self.connections: list[_RecordingConnection] = []
        self.generate_calls: list[tuple[Any, ...]] = []
        self.clock_value = 10.0
        self.connection_options = connection_options

        def mono_time() -> float:
            self.events.append("clock")
            self.clock_value += 2.5
            return self.clock_value

        def load_refresh_file(connection, table, path, expected_rows):
            self.events.append(f"load:{table.name}")
            return expected_rows

        monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.mono_time", mono_time)
        monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.load_refresh_file", load_refresh_file)
        self.test = TPCDSMaintenanceTest(benchmark=None, connection_factory=self._open, scale_factor=0.01)

    def _open(self) -> _RecordingConnection:
        self.events.append("open")
        connection = _RecordingConnection(self.events, **self.connection_options)
        self.connections.append(connection)
        return connection

    def patch_generation(self, monkeypatch: pytest.MonkeyPatch, sets: dict[int, RefreshSet]) -> None:
        def generate_refresh_sets(*arguments):
            self.events.append("generate")
            self.generate_calls.append(arguments)
            return sets

        monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.generate_refresh_sets", generate_refresh_sets)

    def interval_bounds(self) -> list[tuple[int, int]]:
        clocks = [index for index, event in enumerate(self.events) if event == "clock"]
        return list(zip(clocks[0::2], clocks[1::2]))


def test_insert_function_event_order_keeps_setup_and_close_outside_interval(monkeypatch):
    harness = _Harness(monkeypatch)

    operation = harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_SS"], _refresh_set())

    assert harness.events == [
        "open",
        "count:store_sales",
        "clock",
        "drop:s_purchase",
        "create:s_purchase",
        "drop:s_purchase_lineitem",
        "create:s_purchase_lineitem",
        "begin",
        "load:s_purchase",
        "load:s_purchase_lineitem",
        "statement",
        "commit",
        "clock",
        "count:store_sales",
        "drop:s_purchase",
        "drop:s_purchase_lineitem",
        "commit",
        "close",
    ]
    assert operation.success is True
    assert operation.rows_affected == LINEITEM_ROWS
    assert operation.rows_by_table == {"store_sales": LINEITEM_ROWS}
    assert operation.statements_executed == 1


def test_delete_function_event_order_covers_every_statement_inside_interval(monkeypatch):
    harness = _Harness(monkeypatch)

    operation = harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["DF_SS"], _refresh_set())

    assert harness.events == [
        "open",
        "count:store_returns",
        "count:store_sales",
        "clock",
        "drop:s_delete",
        "create:s_delete",
        "begin",
        "load:s_delete",
        "statement",
        "statement",
        "commit",
        "clock",
        "count:store_returns",
        "count:store_sales",
        "drop:s_delete",
        "commit",
        "close",
    ]
    assert operation.success is True
    assert operation.rows_by_table == {"store_returns": -3, "store_sales": -3}
    assert operation.rows_affected == 6
    assert operation.statements_executed == 2


def test_duration_equals_end_minus_start_from_patched_clock(monkeypatch):
    harness = _Harness(monkeypatch)

    operation = harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_SS"], _refresh_set())

    assert operation.start_time == 12.5
    assert operation.end_time == 15.0
    assert operation.duration == operation.end_time - operation.start_time == 2.5


def test_interval_starts_before_first_file_read_and_encloses_every_read(monkeypatch):
    harness = _Harness(monkeypatch)

    harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_CS"], _refresh_set())

    ((start, end),) = harness.interval_bounds()
    loads = [index for index, event in enumerate(harness.events) if event.startswith("load:")]
    assert len(loads) == 2
    assert start < loads[0]
    assert all(start < index < end for index in loads)
    assert not any(event == "open" for event in harness.events[start:end])
    assert harness.events.index("open") < start
    assert harness.events.index("close") > end


def test_failing_statement_rolls_back_ends_interval_drops_staging_and_closes(monkeypatch):
    harness = _Harness(monkeypatch, fail_statement=True)

    operation = harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_SS"], _refresh_set())

    assert harness.events == [
        "open",
        "count:store_sales",
        "clock",
        "drop:s_purchase",
        "create:s_purchase",
        "drop:s_purchase_lineitem",
        "create:s_purchase_lineitem",
        "begin",
        "load:s_purchase",
        "load:s_purchase_lineitem",
        "statement",
        "rollback",
        "clock",
        "drop:s_purchase",
        "drop:s_purchase_lineitem",
        "commit",
        "close",
    ]
    assert operation.success is False
    assert operation.error == "statement boom"
    assert operation.duration == operation.end_time - operation.start_time == 2.5
    assert operation.rows_by_table == {}


def test_row_count_mismatch_fails_function_after_the_timer(monkeypatch):
    harness = _Harness(monkeypatch, inserted_rows=LINEITEM_ROWS - 1)

    operation = harness.test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_SS"], _refresh_set())

    assert operation.success is False
    assert "s_purchase_lineitem" in operation.error
    assert f"inserted {LINEITEM_ROWS - 1} rows" in operation.error
    clocks = [index for index, event in enumerate(harness.events) if event == "clock"]
    assert len(clocks) == 2
    assert harness.events[clocks[1] + 1] == "count:store_sales"
    assert harness.events[-3:] == ["drop:s_purchase_lineitem", "commit", "close"]
    assert operation.duration == 2.5


def test_generation_precedes_first_interval_and_never_runs_inside_one(monkeypatch):
    harness = _Harness(monkeypatch)
    harness.patch_generation(monkeypatch, {1: _refresh_set(1)})

    result = harness.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))

    assert result["success"] is True
    assert harness.generate_calls == [(0.01, 1, 1, harness.generate_calls[0][3], None)]
    assert isinstance(harness.generate_calls[0][3], Path)
    assert harness.events.count("generate") == 1
    assert harness.events.index("generate") < harness.events.index("clock")
    bounds = harness.interval_bounds()
    assert len(bounds) == 11
    for start, end in bounds:
        assert "generate" not in harness.events[start:end]


def test_run_stops_at_first_failed_function(monkeypatch):
    harness = _Harness(monkeypatch)
    harness.patch_generation(monkeypatch, {1: _refresh_set(1)})
    executed: list[str] = []

    def execute_function(function, refresh_set):
        executed.append(function.name)
        failed = len(executed) == 3
        return TPCDSMaintenanceOperation(
            operation_type=function.operation_type,
            table_name="",
            start_time=1.0,
            end_time=2.0,
            duration=1.0,
            rows_affected=0 if failed else 1,
            success=not failed,
            error="boom" if failed else None,
            function=function.name,
            refresh_set=refresh_set.set_id,
        )

    monkeypatch.setattr(harness.test, "_execute_function", execute_function)

    result = harness.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))

    assert executed == ["DF_CS", "DF_SS", "DF_WS"]
    assert result["total_operations"] == 3
    assert result["successful_operations"] == 2
    assert result["failed_operations"] == 1
    assert result["success"] is False
    assert result["errors"] == ["DF_WS on refresh set 1 failed: boom"]


def test_two_refresh_runs_execute_every_function_per_set_in_order(monkeypatch):
    harness = _Harness(monkeypatch)
    harness.patch_generation(monkeypatch, {1: _refresh_set(1), 2: _refresh_set(2)})
    executed: list[tuple[str, int]] = []

    def execute_function(function, refresh_set):
        executed.append((function.name, refresh_set.set_id))
        return TPCDSMaintenanceOperation(
            operation_type=function.operation_type,
            table_name="",
            start_time=1.0,
            end_time=2.0,
            duration=1.0,
            rows_affected=1,
            success=True,
            function=function.name,
            refresh_set=refresh_set.set_id,
        )

    monkeypatch.setattr(harness.test, "_execute_function", execute_function)

    result = harness.test.run(TPCDSMaintenanceTestConfig(refresh_runs=2, validate_integrity=False))

    expected_names = ["DF_CS", "DF_SS", "DF_WS", "DF_I", "LF_CR", "LF_CS", "LF_I", "LF_SR", "LF_SS", "LF_WR", "LF_WS"]
    assert [function.name for function in MAINTENANCE_FUNCTIONS] == expected_names
    assert len(executed) == 22
    assert executed == [(name, 1) for name in expected_names] + [(name, 2) for name in expected_names]
    assert result["refresh_sets"] == [1, 2]
    assert harness.generate_calls[0][1:3] == (1, 2)
    assert result["total_operations"] == 22
    assert result["insert_operations"] == 14
    assert result["delete_operations"] == 8
