from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from benchbox.core.write_primitives.benchmark import OperationResult, WritePrimitivesBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.fixture()
def wp(tmp_path: Path) -> WritePrimitivesBenchmark:
    return WritePrimitivesBenchmark(output_dir=tmp_path)


class TestExtractRowsAffected:
    def test_normal_rowcount(self, wp: WritePrimitivesBenchmark):
        result = SimpleNamespace(rowcount=42)
        assert wp._extract_rows_affected(result, "OP_1") == 42

    def test_rowcount_none_returns_sentinel(self, wp: WritePrimitivesBenchmark):
        result = SimpleNamespace()
        assert wp._extract_rows_affected(result, "OP_1") == -1

    def test_rowcount_minus_one_passthrough(self, wp: WritePrimitivesBenchmark):
        result = SimpleNamespace(rowcount=-1)
        assert wp._extract_rows_affected(result, "OP_1") == -1

    def test_rowcount_zero(self, wp: WritePrimitivesBenchmark):
        result = SimpleNamespace(rowcount=0)
        assert wp._extract_rows_affected(result, "OP_1") == 0


class TestRunOperationValidation:
    def test_passes_when_no_validation_queries(self, wp: WritePrimitivesBenchmark):
        operation = SimpleNamespace(validation_queries=[])
        conn = MagicMock()
        passed, results, duration_ms = wp._run_operation_validation(operation, conn, "OP_1")
        assert passed is True
        assert results == []
        assert duration_ms >= 0

    def test_passes_with_matching_row_count(self, wp: WritePrimitivesBenchmark):
        val_query = SimpleNamespace(
            id="V1",
            sql="SELECT COUNT(*) FROM t",
            expected_rows=3,
            expected_rows_min=None,
            expected_rows_max=None,
            expected_values=None,
            check_expression=None,
            platform_overrides={},
        )
        operation = SimpleNamespace(validation_queries=[val_query])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,), (2,), (3,)]
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1")
        assert passed is True
        assert results[0]["actual_rows"] == 3
        assert results[0]["passed"] is True

    def test_fails_with_wrong_row_count(self, wp: WritePrimitivesBenchmark):
        val_query = SimpleNamespace(
            id="V1",
            sql="SELECT * FROM t",
            expected_rows=10,
            expected_rows_min=None,
            expected_rows_max=None,
            expected_values=None,
            check_expression=None,
            platform_overrides={},
        )
        operation = SimpleNamespace(validation_queries=[val_query])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,)]
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1")
        assert passed is False
        assert results[0]["passed"] is False


class TestRunOperationCleanup:
    def test_no_cleanup_sql_succeeds(self, wp: WritePrimitivesBenchmark):
        operation = SimpleNamespace(cleanup_sql=None)
        conn = MagicMock()
        success, warning, duration_ms = wp._run_operation_cleanup(operation, conn, "OP_1")
        assert success is True
        assert warning is None
        conn.execute.assert_not_called()

    def test_cleanup_sql_executes(self, wp: WritePrimitivesBenchmark):
        operation = SimpleNamespace(cleanup_sql="DELETE FROM t WHERE 1=1")
        conn = MagicMock()
        success, warning, _ = wp._run_operation_cleanup(operation, conn, "OP_1")
        assert success is True
        assert warning is None
        conn.execute.assert_called_once_with("DELETE FROM t WHERE 1=1")

    def test_cleanup_failure_returns_warning(self, wp: WritePrimitivesBenchmark):
        operation = SimpleNamespace(cleanup_sql="DROP TABLE missing")
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("table not found")
        success, warning, _ = wp._run_operation_cleanup(operation, conn, "OP_1")
        assert success is False
        assert "cleanup failed" in warning
        assert "table not found" in warning


class TestGetPopulationSql:
    def test_returns_string(self, wp: WritePrimitivesBenchmark):
        sql = wp._get_population_sql("orders_stage", "orders")
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_contains_table_names(self, wp: WritePrimitivesBenchmark):
        sql = wp._get_population_sql("orders_stage", "orders")
        assert "orders_stage" in sql.lower() or "orders" in sql.lower()


class TestLoadData:
    def test_returns_dict(self, wp: WritePrimitivesBenchmark):
        conn = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "benchbox.core.write_primitives.benchmark.table_exists",
                lambda conn, name, *args, **kwargs: True,
            )
            conn.execute.return_value.fetchone.return_value = (100,)
            result = wp.load_data(conn)
            assert isinstance(result, dict)


class TestOperationResultEdgeCases:
    def test_skipped_status(self):
        result = OperationResult(
            operation_id="SKIP_1",
            success=True,
            write_duration_ms=0.0,
            rows_affected=0,
            validation_duration_ms=0.0,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=0.0,
            cleanup_success=True,
            status="SKIPPED",
        )
        assert result.status == "SKIPPED"

    def test_with_cleanup_warning(self):
        result = OperationResult(
            operation_id="WARN_1",
            success=True,
            write_duration_ms=10.0,
            rows_affected=5,
            validation_duration_ms=2.0,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=1.0,
            cleanup_success=False,
            cleanup_warning="Cleanup SQL failed",
        )
        assert result.cleanup_warning == "Cleanup SQL failed"
        assert result.cleanup_success is False


class TestOperationManagement:
    def test_get_operations_by_category_returns_subset(self, wp: WritePrimitivesBenchmark):
        categories = wp.get_operation_categories()
        if categories:
            ops = wp.get_operations_by_category(categories[0])
            assert isinstance(ops, dict)
            for op in ops.values():
                assert op.category == categories[0]

    def test_get_operations_by_unknown_category(self, wp: WritePrimitivesBenchmark):
        ops = wp.get_operations_by_category("nonexistent_category")
        assert ops == {}

    def test_supports_dataframe_mode(self, wp: WritePrimitivesBenchmark):
        assert wp.supports_dataframe_mode() is True

    def test_skip_dataframe_data_loading(self, wp: WritePrimitivesBenchmark):
        assert wp.skip_dataframe_data_loading() is True

    def test_get_data_source_benchmark_returns_tpch(self, wp: WritePrimitivesBenchmark):
        assert wp.get_data_source_benchmark() == "tpch"


class TestEffectiveWriteSqlSkipOrder:
    def test_null_override_skips_even_with_sql_override(self, wp: WritePrimitivesBenchmark):
        op = wp.get_operation("bulk_load_upsert_mode")
        assert op.platform_overrides.get("datafusion", "MISSING") is None
        effective_sql, skip_reason = wp._get_effective_write_sql(
            op,
            platform_key="datafusion",
            sql_override="CREATE EXTERNAL TABLE rewritten AS SELECT 1",
        )
        assert effective_sql is None
        assert skip_reason is not None
        assert "unsupported on platform 'datafusion'" in skip_reason

    def test_sql_override_wins_when_op_is_runnable(self, wp: WritePrimitivesBenchmark):

        op = wp.get_operation("merge_scd_type2_basic")
        assert not getattr(op, "file_dependencies", None)
        assert "spark" not in (op.platform_overrides or {})
        rewritten = "UPDATE rewritten SET a = 1"
        effective_sql, skip_reason = wp._get_effective_write_sql(op, platform_key="spark", sql_override=rewritten)
        assert skip_reason is None
        assert effective_sql == rewritten

    def test_scd2_null_override_skips_on_datafusion(self, wp: WritePrimitivesBenchmark):
        op = wp.get_operation("merge_scd_type2_basic")
        effective_sql, skip_reason = wp._get_effective_write_sql(op, platform_key="datafusion")
        assert effective_sql is None
        assert skip_reason is not None and "unsupported on platform 'datafusion'" in skip_reason


SKETCH_PARAMETER_SWEEPS = (
    "sketch_query_theta_union_merge_lgk10",
    "sketch_query_theta_union_merge_lgk14",
    "sketch_query_kll_quantiles_merge_k100",
    "sketch_query_kll_quantiles_merge_k1000",
    "sketch_query_topk_combine_lgmm8",
    "sketch_query_topk_combine_lgmm10",
)


@pytest.mark.parametrize("operation_id", SKETCH_PARAMETER_SWEEPS)
@pytest.mark.parametrize("platform", ["clickhouse", "trino"])
def test_unsupported_sketch_parameter_sweeps_skip_adapter_overrides(
    wp: WritePrimitivesBenchmark, operation_id: str, platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = wp.get_operation(operation_id)

    sql, reason = wp._get_effective_write_sql(operation, platform_key=platform, sql_override="SELECT 1")

    assert sql is None
    assert reason is not None and f"unsupported on platform '{platform}'" in reason

    connection = Mock()
    monkeypatch.setattr(wp, "is_setup", lambda connection: True)
    result = wp.execute_operation(operation_id, connection, platform_key=platform, sql_override="SELECT 1")
    assert result.status == "SKIPPED"
    assert result.validation_results == []
    connection.execute.assert_not_called()


TRINO_UNSUPPORTED_SKETCH_HEADLINES = (
    "sketch_ddl_create_persistent_table",
    "sketch_insert_theta_per_partition",
    "sketch_insert_kll_per_partition",
    "sketch_insert_topk_per_shard",
    "sketch_query_theta_union_merge",
    "sketch_query_kll_quantiles_merge",
    "sketch_query_topk_combine",
    "sketch_drop_persistent_table",
)


@pytest.mark.parametrize("operation_id", TRINO_UNSUPPORTED_SKETCH_HEADLINES)
def test_trino_sketch_headlines_skip_before_execution(
    wp: WritePrimitivesBenchmark, operation_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = Mock()
    monkeypatch.setattr(wp, "is_setup", lambda connection: True)

    result = wp.execute_operation(operation_id, connection, platform_key="trino", sql_override="SELECT 1")

    assert result.status == "SKIPPED"
    assert "unsupported on platform 'trino'" in (result.skip_reason or "")
    assert result.validation_results == []
    connection.execute.assert_not_called()


@pytest.mark.parametrize("operation_id", SKETCH_PARAMETER_SWEEPS)
def test_duckdb_sketch_parameter_sweeps_keep_catalog_sql(wp: WritePrimitivesBenchmark, operation_id: str) -> None:
    operation = wp.get_operation(operation_id)

    sql, reason = wp._get_effective_write_sql(operation, platform_key="duckdb")

    assert reason is None
    assert sql == operation.write_sql


class TestEffectiveWriteSqlFallbackKey:
    def _op(self, **kw):
        base = {
            "id": "op1",
            "category": "insert",
            "write_sql": "INSERT INTO t VALUES (1)",
            "platform_overrides": {},
            "aggregate_state": None,
            "file_dependencies": [],
        }
        base.update(kw)
        return SimpleNamespace(**base)

    def test_fallback_null_override_skips(self, wp: WritePrimitivesBenchmark):
        op = self._op(platform_overrides={"duckdb": None})
        sql, reason = wp._get_effective_write_sql(op, platform_key="ducklake", platform_fallback_key="duckdb")
        assert sql is None
        assert reason is not None and "ducklake" in reason

    def test_fallback_string_override_used(self, wp: WritePrimitivesBenchmark):
        op = self._op(platform_overrides={"duckdb": "INSERT INTO t VALUES (2)"})
        sql, reason = wp._get_effective_write_sql(op, platform_key="ducklake", platform_fallback_key="duckdb")
        assert (sql, reason) == ("INSERT INTO t VALUES (2)", None)

    def test_engine_entry_wins_over_fallback(self, wp: WritePrimitivesBenchmark):
        op = self._op(platform_overrides={"ducklake": "INSERT INTO t VALUES (3)", "duckdb": None})
        sql, reason = wp._get_effective_write_sql(op, platform_key="ducklake", platform_fallback_key="duckdb")
        assert (sql, reason) == ("INSERT INTO t VALUES (3)", None)

    def test_duckdb_merge_gate_applies_via_fallback(self, wp: WritePrimitivesBenchmark):
        op = self._op(write_sql="MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = 1")
        sql, reason = wp._get_effective_write_sql(op, platform_key="ducklake", platform_fallback_key="duckdb")
        assert sql is None
        assert reason is not None and "DuckDB" in reason

    def test_no_fallback_preserves_exact_match(self, wp: WritePrimitivesBenchmark):
        op = self._op(platform_overrides={"duckdb": "INSERT INTO t VALUES (2)"})
        sql, reason = wp._get_effective_write_sql(op, platform_key="ducklake")
        assert (sql, reason) == ("INSERT INTO t VALUES (1)", None)
