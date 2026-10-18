from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.plan_capture_phase import run_plan_capture_phase
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@dataclass(frozen=True)
class EngineCase:
    platform: str
    queries: dict[str, str]
    insert_sql: str
    count_sql: str


def _duckdb_case(tmp_path: Path) -> tuple[EngineCase, DuckDBAdapter, Any]:
    adapter = DuckDBAdapter(database_path=str(tmp_path / "plan-capture-gate.duckdb"), capture_plans=False)
    connection = adapter.create_connection()
    connection.execute("CREATE TABLE orders (id INTEGER, amount DOUBLE)")
    connection.execute("INSERT INTO orders VALUES (1, 99.9), (2, 49.9), (3, 25.0)")
    case = EngineCase(
        platform="duckdb",
        queries={
            "scan": "SELECT * FROM orders WHERE amount > 30",
            "aggregate": "SELECT id, SUM(amount) FROM orders GROUP BY id",
        },
        insert_sql="INSERT INTO orders VALUES (4, 40.0)",
        count_sql="SELECT COUNT(*) FROM orders",
    )
    return case, adapter, connection


def _sqlite_case() -> tuple[EngineCase, SQLiteAdapter, Any]:
    adapter = SQLiteAdapter(capture_plans=False)
    connection = adapter.create_connection()
    connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
    connection.executemany("INSERT INTO orders VALUES (?, ?)", [(1, 99.9), (2, 49.9), (3, 25.0)])
    case = EngineCase(
        platform="sqlite",
        queries={
            "scan": "SELECT * FROM orders WHERE amount > 30",
            "aggregate": "SELECT id, SUM(amount) FROM orders GROUP BY id",
        },
        insert_sql="INSERT INTO orders SELECT 4, 40.0",
        count_sql="SELECT COUNT(*) FROM orders",
    )
    return case, adapter, connection


@pytest.fixture(params=["duckdb", "sqlite"])
def engine_case(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "duckdb":
        case, adapter, connection = _duckdb_case(tmp_path)
    else:
        case, adapter, connection = _sqlite_case()

    try:
        yield case, adapter, connection
    finally:
        adapter.close_connection(connection)


def _row_count(connection: Any, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def _plant_defect_if_requested(monkeypatch: pytest.MonkeyPatch, adapter: Any) -> None:
    defect = os.environ.get("BENCHBOX_PLANT_PLAN_CAPTURE_GATE_DEFECT")
    if defect in (None, ""):
        return
    if defect != "drop_fingerprint":
        raise AssertionError(f"unknown planted plan-capture defect: {defect}")

    original_capture = adapter.capture_query_plan

    def broken_capture_query_plan(*args: Any, **kwargs: Any) -> tuple[Any, float]:
        plan, capture_ms = original_capture(*args, **kwargs)
        if plan is not None:
            plan.plan_fingerprint = None
        return plan, capture_ms

    monkeypatch.setattr(adapter, "capture_query_plan", broken_capture_query_plan)


def test_plan_capture_gate_parses_plans_and_stabilizes_fingerprints(engine_case, monkeypatch) -> None:
    case, adapter, connection = engine_case
    _plant_defect_if_requested(monkeypatch, adapter)

    first = run_plan_capture_phase(adapter, case.queries, connection=connection)
    second = run_plan_capture_phase(adapter, case.queries, connection=connection)

    assert first.failed == 0, f"{case.platform} failed to capture plans: {first}"
    assert second.failed == 0, f"{case.platform} failed to recapture plans: {second}"
    assert first.captured == len(case.queries)
    assert second.captured == len(case.queries)
    assert set(first.plans) == set(case.queries)
    assert set(first.fingerprints) == set(case.queries)
    assert first.fingerprints == second.fingerprints
    for query_id, plan in first.plans.items():
        assert plan.logical_root is not None, f"{case.platform} {query_id} produced an unparsed plan"
        assert plan.plan_fingerprint, f"{case.platform} {query_id} produced no fingerprint"


def test_plan_capture_gate_dml_is_not_double_executed(engine_case) -> None:
    case, adapter, connection = engine_case
    before = _row_count(connection, case.count_sql)

    connection.execute(case.insert_sql)
    after_measurement = _row_count(connection, case.count_sql)
    assert after_measurement == before + 1

    result = run_plan_capture_phase(adapter, {"insert": case.insert_sql}, connection=connection)

    after_capture = _row_count(connection, case.count_sql)
    assert after_capture == after_measurement, f"{case.platform} capture phase re-executed DML"
    assert result.failed == 0, f"{case.platform} failed to structurally capture DML"
    assert result.captured == 1
    assert result.fingerprints["insert"]


def test_plan_capture_gate_catches_planted_defect() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    node = (
        "tests/integration/test_plan_capture_gate.py::"
        "test_plan_capture_gate_parses_plans_and_stabilizes_fingerprints[duckdb]"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_XDIST", "COV_CORE")) and key != "PYTEST_CURRENT_TEST"
    }
    env["BENCHBOX_PLANT_PLAN_CAPTURE_GATE_DEFECT"] = "drop_fingerprint"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-n", "0", "-p", "no:cacheprovider", "-q"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "plan-capture gate PASSED with a planted dropped-fingerprint defect; "
        f"the catch capability has regressed.\n{combined}"
    )
    assert "produced no fingerprint" in combined, (
        "plan-capture gate failed for an unexpected reason rather than the dropped "
        f"fingerprint; the negative test is no longer load-bearing.\n{combined}"
    )
