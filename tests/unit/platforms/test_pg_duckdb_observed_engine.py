from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import benchbox.platforms.pg_duckdb as pg_duckdb_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.execution_engine import MIXED, NOT_CAPTURED, ObservationNotRecognizedError, QueryObservationTarget
from benchbox.platforms.pg_duckdb import PgDuckDBAdapter, classify_explain_plan

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PLANS = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "observed_engine" / "pg_duckdb_explain_plans.json").read_text(
        encoding="utf-8"
    )
)["plans"]


@pytest.fixture()
def adapter(monkeypatch):
    stub = Mock()
    stub.__version__ = "3.1.0"
    monkeypatch.setattr(pg_duckdb_module, "psycopg", stub)
    monkeypatch.setattr(postgresql_module, "psycopg", stub)
    return PgDuckDBAdapter(execution_engine="duckdb")


def _connection(plan_by_query: dict[str, list[str]]) -> Mock:
    connection = Mock()

    def cursor():
        current = Mock()

        def execute(statement):
            query = statement.removeprefix("EXPLAIN ")
            plan = plan_by_query[query]
            if isinstance(plan, Exception):
                raise plan
            current.fetchall.return_value = [(line,) for line in plan]

        current.execute.side_effect = execute
        return current

    connection.cursor.side_effect = cursor
    return connection


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("duckdb", "duckdb"),
        ("postgres_after_forced_duckdb", "postgres"),
        ("postgres_over_duckdb", MIXED),
        ("unrecognized", None),
        ("empty", None),
    ],
)
def test_plan_classification(name, expected):
    assert classify_explain_plan("\n".join(PLANS[name])) == expected


def test_the_configured_engine_is_not_the_observation(adapter):
    connection = _connection({"select 1": PLANS["postgres_after_forced_duckdb"]})
    assert adapter.force_execution is True
    assert adapter.observe_execution_engine(QueryObservationTarget(connection, "select 1", "q1")) == "postgres"


def test_unrecognized_plan_is_reported_and_the_transaction_is_left_usable(adapter):
    connection = _connection({"select 1": PLANS["unrecognized"], "select 2": RuntimeError("syntax")})
    with pytest.raises(ObservationNotRecognizedError):
        adapter.observe_execution_engine(QueryObservationTarget(connection, "select 1", "q1"))
    with pytest.raises(RuntimeError):
        adapter.observe_execution_engine(QueryObservationTarget(connection, "select 2", "q2"))
    connection.rollback.assert_not_called()


def test_run_record_names_the_engine_each_timed_query_was_routed_to(adapter):
    connection = _connection({"select 1": PLANS["duckdb"], "select 2": PLANS["duckdb"]})
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.record_query_engine_observation(connection, "select 2", "q2")

    receipt = adapter.get_platform_info(connection)["configuration"]["execution_engine"]

    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == ("duckdb", "explain", None)
    assert receipt["applied"] == "duckdb"


def test_run_record_is_mixed_when_pg_duckdb_declines_some_queries(adapter):
    connection = _connection({"select 1": PLANS["duckdb"], "select 2": PLANS["postgres_after_forced_duckdb"]})
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.record_query_engine_observation(connection, "select 2", "q2")

    assert adapter.get_platform_info(connection)["configuration"]["execution_engine"]["observed"] == MIXED


def test_run_record_is_not_captured_when_a_plan_is_unavailable(adapter):
    connection = _connection({"select 1": RuntimeError("syntax")})
    adapter.record_query_engine_observation(connection, "select 1", "q1")

    receipt = adapter.get_platform_info(connection)["configuration"]["execution_engine"]

    assert (receipt["observed"], receipt["observed_source"]) == (NOT_CAPTURED, "none")
    assert receipt["applied"] == "duckdb"


def test_a_successful_timed_query_is_observed_and_a_failed_one_is_not(adapter, monkeypatch):
    outcomes = iter([{"status": "SUCCESS"}, {"status": "FAILED"}])
    monkeypatch.setattr(
        postgresql_module.PostgreSQLAdapter, "execute_query", lambda self, *args, **kwargs: next(outcomes)
    )
    connection = _connection({"select 1": PLANS["duckdb"]})

    adapter.execute_query(connection, "select 1", "q1")
    adapter.execute_query(connection, "select 2", "q2")

    assert adapter.get_platform_info(connection)["configuration"]["execution_engine"]["observed"] == "duckdb"


class _RecordingConnection:
    def __init__(self, status: int, failing: tuple[str, ...] = ()):
        self.statements: list[str] = []
        self.rollbacks = 0
        self.status = status
        self.failing = failing
        self.info = type("Info", (), {})()
        self.info.transaction_status = status

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, statement):
                connection.statements.append(statement)
                if statement.startswith("EXPLAIN") and any(token in statement for token in connection.failing):
                    connection.info.transaction_status = 3
                    raise RuntimeError("explain refused")
                if statement.startswith("ROLLBACK TO SAVEPOINT"):
                    connection.info.transaction_status = 2
                if statement.startswith("EXPLAIN") and connection.status == 0:
                    connection.info.transaction_status = 2
                self.rows = [(line,) for line in PLANS["duckdb"]]

            def fetchall(self):
                return self.rows

            def close(self):
                pass

        return Cursor()

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = 0


def test_a_timed_phase_issues_no_explain_between_timed_queries(adapter, monkeypatch):
    monkeypatch.setattr(
        postgresql_module.PostgreSQLAdapter, "execute_query", lambda self, *args, **kwargs: {"status": "SUCCESS"}
    )
    connection = _RecordingConnection(status=2)

    for index in range(3):
        adapter.execute_query(connection, f"select {index}", f"q{index}")

    assert connection.statements == []
    receipt = adapter.get_platform_info(connection)["configuration"]["execution_engine"]
    assert [s for s in connection.statements if s.startswith("EXPLAIN")] == [f"EXPLAIN select {i}" for i in range(3)]
    assert receipt["observed"] == "duckdb"


def test_a_failed_observation_leaves_the_open_transaction_intact(adapter):
    connection = _RecordingConnection(status=2, failing=("select 1",))
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.record_query_engine_observation(connection, "select 2", "q2")

    receipt = adapter.get_platform_info(connection)["configuration"]["execution_engine"]

    assert connection.rollbacks == 0
    assert connection.info.transaction_status == 2
    assert "ROLLBACK TO SAVEPOINT benchbox_engine_observation" in connection.statements
    assert (receipt["observed"], receipt["observed_source"]) == (NOT_CAPTURED, "none")
    assert receipt["observed_reason"]


def test_an_observation_never_runs_in_an_aborted_transaction(adapter):
    connection = _RecordingConnection(status=3)
    adapter.record_query_engine_observation(connection, "select 1", "q1")

    receipt = adapter.get_platform_info(connection)["configuration"]["execution_engine"]

    assert not [s for s in connection.statements if s.startswith(("EXPLAIN", "SAVEPOINT"))]
    assert receipt["observed"] == NOT_CAPTURED


def test_an_observation_outside_a_transaction_leaves_no_transaction_behind(adapter):
    connection = _RecordingConnection(status=0)
    adapter.record_query_engine_observation(connection, "select 1", "q1")

    adapter.get_platform_info(connection)

    assert connection.info.transaction_status == 0


def test_observation_state_does_not_leak_between_runs(adapter):
    first = _RecordingConnection(status=2, failing=("select 1",))
    adapter.record_query_engine_observation(first, "select 1", "q1")
    adapter.get_platform_info(first)

    adapter._reset_run_scoped_state()
    adapter.open_engine_observation_window()
    second = _RecordingConnection(status=2)
    adapter.record_query_engine_observation(second, "select 2", "q2")
    adapter.close_engine_observation_window()
    receipt = adapter.get_platform_info(second)["configuration"]["execution_engine"]

    assert [s for s in second.statements if s.startswith("EXPLAIN")] == ["EXPLAIN select 2"]
    assert (receipt["observed"], receipt["observed_reason"]) == ("duckdb", None)


def test_a_contained_run_never_touches_the_recorded_connections(adapter):
    connection = _RecordingConnection(status=2)
    adapter.reset_query_engine_observation()
    adapter.open_engine_observation_window()
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.close_engine_observation_window()

    receipt = adapter.get_platform_info(None)["configuration"]["execution_engine"]

    assert connection.statements == []
    assert (receipt["observed"], receipt["observed_source"]) == (NOT_CAPTURED, "none")
    assert "contained" in receipt["observed_reason"]


def test_two_platform_info_calls_after_a_run_observe_once(adapter):
    connection = _RecordingConnection(status=2)
    adapter.reset_query_engine_observation()
    adapter.open_engine_observation_window()
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.get_platform_info(connection)
    adapter.close_engine_observation_window()

    first = adapter.get_platform_info(connection)["configuration"]["execution_engine"]
    second = adapter.get_platform_info(connection)["configuration"]["execution_engine"]

    assert [s for s in connection.statements if s.startswith("EXPLAIN")] == ["EXPLAIN select 1"]
    assert first == second
    assert first["observed"] == "duckdb"
