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
    connection.rollback.assert_called_once()


def test_run_record_names_the_engine_each_timed_query_was_routed_to(adapter):
    connection = _connection({"select 1": PLANS["duckdb"], "select 2": PLANS["duckdb"]})
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.record_query_engine_observation(connection, "select 2", "q2")

    receipt = adapter.get_platform_info(None)["configuration"]["execution_engine"]

    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == ("duckdb", "explain", None)
    assert receipt["applied"] == "duckdb"


def test_run_record_is_mixed_when_pg_duckdb_declines_some_queries(adapter):
    connection = _connection({"select 1": PLANS["duckdb"], "select 2": PLANS["postgres_after_forced_duckdb"]})
    adapter.record_query_engine_observation(connection, "select 1", "q1")
    adapter.record_query_engine_observation(connection, "select 2", "q2")

    assert adapter.get_platform_info(None)["configuration"]["execution_engine"]["observed"] == MIXED


def test_run_record_is_not_captured_when_a_plan_is_unavailable(adapter):
    connection = _connection({"select 1": RuntimeError("syntax")})
    adapter.record_query_engine_observation(connection, "select 1", "q1")

    receipt = adapter.get_platform_info(None)["configuration"]["execution_engine"]

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

    assert adapter.get_platform_info(None)["configuration"]["execution_engine"]["observed"] == "duckdb"
