# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

import pytest

from benchbox.core.tpch.refresh import generate_refresh_update_sets
from benchbox.platforms.base.result_capture import _plan_capture_key
from benchbox.platforms.duckdb import DuckDBAdapter
from tests.integration._tpch_refresh_utils import _delete_keys

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def file_adapter(tmp_path):
    db_path = str(tmp_path / "divergence.duckdb")
    return DuckDBAdapter(database_path=db_path, capture_plans=True)


def _seed(conn, rows: int) -> None:
    conn.execute("CREATE TABLE t (id INTEGER, val INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(i, i % 7) for i in range(rows)])


def _row_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]


def _spy_data_state_at_capture(adapter, monkeypatch) -> dict[str, int]:
    seen: dict[str, int] = {}
    original = adapter.capture_query_plan

    def _spy(connection, sql, capture_key):
        seen[str(capture_key)] = _row_count(connection)
        return original(connection, sql, capture_key)

    monkeypatch.setattr(adapter, "capture_query_plan", _spy)
    return seen


def _begin_phase(adapter) -> None:
    adapter._plan_capture_phase_active = True
    adapter._phase_recorded_queries = {}
    adapter._captured_plans = {}


def _record(adapter, query_id: str, sql: str) -> str:
    key = _plan_capture_key(query_id, sql)
    adapter._phase_recorded_queries[key] = sql
    return key


def test_combined_maintenance_mutation_captures_read_plan_before_mutation(file_adapter, monkeypatch):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 10)
        seen = _spy_data_state_at_capture(file_adapter, monkeypatch)
        _begin_phase(file_adapter)

        read_sql = "SELECT id, val FROM t WHERE val > 2 ORDER BY id"
        read_key = _record(file_adapter, "q_read", read_sql)
        read_row = {"query_id": "q_read", "status": "SUCCESS", "stream_id": 0}

        file_adapter._plan_capture_checkpoint(conn)
        assert seen.get(read_key) == 10, "read plan must be captured before the mutation"

        conn.executemany("INSERT INTO t VALUES (?, ?)", [(100 + i, i % 7) for i in range(90)])
        assert _row_count(conn) == 100

        maint_sql = "DELETE FROM t WHERE id > 95"
        maint_key = _record(file_adapter, "q_maint", maint_sql)
        maint_row = {"query_id": "q_maint", "status": "SUCCESS", "stream_id": 0}

        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(
            conn,
            dict(file_adapter._phase_recorded_queries),
            [read_row, maint_row],
        )

        assert seen[read_key] == 10
        assert seen[maint_key] == 100

        assert read_row.get("plan_fingerprint")
        assert read_row.get("query_plan") is not None
        assert read_row["query_plan"].query_id == "q_read"
        assert maint_row.get("plan_fingerprint")
    finally:
        conn.close()


def test_combined_maintenance_capture_does_not_reexecute_write(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 20)
        _begin_phase(file_adapter)

        maint_sql = "DELETE FROM t WHERE id < 5"
        _record(file_adapter, "q_maint", maint_sql)
        conn.execute(maint_sql)
        after_measured = _row_count(conn)
        assert after_measured == 15

        maint_row = {"query_id": "q_maint", "status": "SUCCESS"}
        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(conn, dict(file_adapter._phase_recorded_queries), [maint_row])

        assert _row_count(conn) == after_measured, "capture must not re-execute the write"
        assert maint_row.get("plan_fingerprint")
    finally:
        conn.close()


def test_read_only_combined_capture_attaches_via_public_id(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 12)
        _begin_phase(file_adapter)

        rows = []
        for qid, sql in (("q1", "SELECT COUNT(*) FROM t"), ("q2", "SELECT id FROM t WHERE val = 3")):
            _record(file_adapter, qid, sql)
            rows.append({"query_id": qid, "status": "SUCCESS"})

        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(conn, dict(file_adapter._phase_recorded_queries), rows)

        for row in rows:
            assert row.get("plan_fingerprint"), f"no fingerprint for {row['query_id']}"
            assert row.get("query_plan") is not None
    finally:
        conn.close()


def test_ambiguous_query_id_variants_not_misattached(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 8)
        _begin_phase(file_adapter)

        _record(file_adapter, "q", "SELECT id FROM t WHERE val > 1")
        _record(file_adapter, "q", "SELECT id FROM t WHERE val > 4")
        rows = [
            {"query_id": "q", "status": "SUCCESS", "stream_id": 0},
            {"query_id": "q", "status": "SUCCESS", "stream_id": 1},
        ]

        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(conn, dict(file_adapter._phase_recorded_queries), rows)

        for row in rows:
            assert row.get("plan_fingerprint") is None, "ambiguous variant must not be guessed"
            assert row.get("query_plan") is None
    finally:
        conn.close()


def test_standard_path_exact_key_attaches_each_variant(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 8)
        _begin_phase(file_adapter)

        sql_a = "SELECT id FROM t WHERE val > 1"
        sql_b = "SELECT id FROM t WHERE val > 4"
        key_a = _record(file_adapter, "q", sql_a)
        key_b = _record(file_adapter, "q", sql_b)
        rows = [
            {"query_id": "q", "status": "SUCCESS", "stream_id": 0, "_plan_capture_key": key_a},
            {"query_id": "q", "status": "SUCCESS", "stream_id": 1, "_plan_capture_key": key_b},
        ]

        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(conn, dict(file_adapter._phase_recorded_queries), rows)

        assert rows[0].get("plan_fingerprint")
        assert rows[1].get("plan_fingerprint")
        assert "_plan_capture_key" not in rows[0]
        assert "_plan_capture_key" not in rows[1]
    finally:
        conn.close()


def test_failed_row_not_annotated_and_key_cleared(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 6)
        _begin_phase(file_adapter)
        sql = "SELECT id FROM t"
        key = _record(file_adapter, "q", sql)
        ok_row = {"query_id": "q", "status": "SUCCESS", "_plan_capture_key": key}
        bad_row = {"query_id": "q", "status": "FAILED", "_plan_capture_key": key}

        file_adapter._plan_capture_phase_active = False
        file_adapter._capture_plans_post_measurement(
            conn, dict(file_adapter._phase_recorded_queries), [ok_row, bad_row]
        )

        assert ok_row.get("plan_fingerprint")
        assert bad_row.get("plan_fingerprint") is None
        assert "_plan_capture_key" not in bad_row
    finally:
        conn.close()


def test_checkpoint_is_noop_when_phase_inactive(file_adapter):
    conn = file_adapter.create_connection()
    try:
        _seed(conn, 5)
        file_adapter._plan_capture_phase_active = False
        file_adapter._phase_recorded_queries = {}
        file_adapter._captured_plans = {}

        file_adapter._plan_capture_checkpoint(conn)
        assert file_adapter._captured_plans == {}, "no capture should happen when phase inactive"
    finally:
        conn.close()


@pytest.mark.slow
def test_real_tpch_power_capture_attaches_plans(tmp_path):
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    db_path = str(tmp_path / "power.duckdb")
    adapter = DuckDBAdapter(database_path=db_path, capture_plans=True)
    conn = adapter.create_connection()
    try:
        bench = TPCHBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"))
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        run_config = {
            "benchmark_name": "tpch",
            "test_execution_type": "power",
            "scale_factor": 0.01,
            "iterations": 1,
            "warm_up_iterations": 0,
            "query_subset": [1, 6],
        }
        results = adapter._execute_queries_by_type(bench, conn, run_config)

        assert results, "power test produced no rows"
        for row in results:
            if row.get("status") != "SUCCESS":
                continue
            assert row.get("plan_fingerprint"), f"power row {row.get('query_id')} got no plan"
            assert row.get("query_plan") is not None
    finally:
        conn.close()


@pytest.mark.slow
def test_combined_power_then_throughput_same_public_id_different_sql(tmp_path):
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    db_path = str(tmp_path / "combined.duckdb")
    adapter = DuckDBAdapter(database_path=db_path, capture_plans=True)
    conn = adapter.create_connection()
    try:
        bench = TPCHBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"))
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        run_config = {
            "benchmark_name": "tpch",
            "test_execution_type": "combined",
            "scale_factor": 0.01,
            "seed": 1,
            "iterations": 1,
            "warm_up_iterations": 0,
            "num_streams": 2,
            "query_subset": [6],
            "options": {"requested_phases": ["power", "throughput"]},
        }
        results = adapter._execute_queries_by_type(bench, conn, run_config)

        power_rows = [r for r in results if r.get("test_type") == "power" and r.get("status") == "SUCCESS"]
        throughput_rows = [r for r in results if r.get("test_type") == "throughput" and r.get("status") == "SUCCESS"]
        assert power_rows, "combined run produced no successful power rows"
        assert throughput_rows, "combined run produced no successful throughput rows"

        for row in power_rows + throughput_rows:
            assert row.get("plan_fingerprint"), (
                f"{row.get('test_type')} row for query {row.get('query_id')} got no plan "
                "(public-id ambiguity from the other phase must not poison this row)"
            )
            assert row.get("query_plan") is not None
            assert "_plan_capture_key" not in row, "the internal key must be consumed, not leaked to the caller"
    finally:
        conn.close()


@pytest.mark.slow
def test_real_combined_power_maintenance_checkpoint_captures_pre_mutation_plan(tmp_path, monkeypatch):
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    db_path = str(tmp_path / "combined_maintenance.duckdb")
    adapter = DuckDBAdapter(database_path=db_path, capture_plans=True)
    conn = adapter.create_connection()
    try:
        bench = TPCHBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"))
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        baseline_lineitem_count = conn.execute("SELECT COUNT(*) FROM lineitem").fetchone()[0]
        refresh_update_set = generate_refresh_update_sets(0.01, 1, 1, tmp_path / "refresh_sets")[1]
        deleted_order_key = _delete_keys(refresh_update_set)[0]

        captured_counts: list[int] = []
        original_capture_query_plan = adapter.capture_query_plan

        def _spy(connection, sql, capture_key):
            if sql.strip().upper().startswith("SELECT") and "lineitem" in sql.lower():
                captured_counts.append(connection.execute("SELECT COUNT(*) FROM lineitem").fetchone()[0])
            return original_capture_query_plan(connection, sql, capture_key)

        monkeypatch.setattr(adapter, "capture_query_plan", _spy)

        run_config = {
            "benchmark_name": "tpch",
            "test_execution_type": "combined",
            "scale_factor": 0.01,
            "iterations": 1,
            "warm_up_iterations": 0,
            "query_subset": [1],
            "maintenance_pairs": 1,
            "rf1_interval": 0.0,
            "rf2_interval": 0.0,
            "validate_integrity": False,
            "output_dir": str(tmp_path / "maintenance_output"),
            "options": {"requested_phases": ["power", "maintenance"]},
        }
        results = adapter._execute_queries_by_type(bench, conn, run_config)

        power_rows = [r for r in results if r.get("test_type") == "power" and r.get("status") == "SUCCESS"]
        assert power_rows, "combined run produced no successful power rows"
        assert power_rows[0].get("plan_fingerprint"), "Q1's power-phase plan was never captured"

        remaining = conn.execute("SELECT COUNT(*) FROM orders WHERE O_ORDERKEY = ?", [deleted_order_key]).fetchone()[0]
        assert remaining == 0, "RF2 did not delete the first order of the refresh delete set - test setup invalid"

        post_run_lineitem_count = conn.execute("SELECT COUNT(*) FROM lineitem").fetchone()[0]
        assert post_run_lineitem_count != baseline_lineitem_count, (
            "lineitem row count did not change net of the mutation - test setup invalid"
        )

        assert captured_counts == [baseline_lineitem_count], (
            f"expected Q1's plan captured exactly once, at the pre-mutation row count "
            f"{baseline_lineitem_count}, but got {captured_counts} (post-mutation count is "
            f"{post_run_lineitem_count}) - the pre-maintenance checkpoint did not fire "
            "before RF1's mutation"
        )
    finally:
        conn.close()
