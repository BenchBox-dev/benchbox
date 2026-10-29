from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.transaction_primitives.catalog.loader import WriteOperation
from benchbox.core.transaction_primitives.operations import TransactionOperationsManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_benchmark(tmp_path: Path) -> TransactionPrimitivesBenchmark:
    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    bench.operations_manager = MagicMock()
    return bench


def _make_operation(**overrides) -> WriteOperation:
    defaults = {
        "id": "op1",
        "category": "insert",
        "description": "test operation",
        "write_sql": "INSERT INTO t VALUES (1)",
        "requires_setup": False,
        "validation_queries": [],
        "cleanup_sql": None,
        "platform_overrides": {},
    }
    defaults.update(overrides)
    return WriteOperation(**defaults)


def _make_connection(rowcount: int = 1) -> MagicMock:
    conn = MagicMock()
    exec_result = MagicMock()
    exec_result.rowcount = rowcount

    exec_result.fetchall.return_value = []
    conn.execute.return_value = exec_result
    return conn


def test_transaction_commit_large_cleanup_covers_inserted_range() -> None:
    operation = TransactionOperationsManager().get_operation("transaction_commit_large")

    assert "DELETE FROM txn_lineitem WHERE l_comment = 'tx_large'" in operation.write_sql
    assert "9300000 + n" in operation.write_sql
    assert "generate_series(1, 1000)" in operation.write_sql
    assert "l_comment = 'tx_large'" in operation.validation_queries[0].sql
    assert "l_comment = 'tx_large'" in (operation.cleanup_sql or "")


class TestConnectionValidation:
    def test_raises_on_none_connection(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        with pytest.raises(ValueError, match="Connection is None"):
            bench.execute_operation("op1", None)

    def test_raises_on_invalid_connection_type(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        with pytest.raises(ValueError, match="Invalid connection type"):
            bench.execute_operation("op1", "not_a_connection")


class TestBasicExecution:
    def test_returns_success_result(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = _make_connection(rowcount=5)

        result = bench.execute_operation("op1", conn)

        assert result.success is True
        assert result.operation_id == "op1"
        assert result.rows_affected == 5
        assert result.status is None

    def test_executes_write_sql(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="INSERT INTO orders VALUES (99)"
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn)

        conn.execute.assert_called_once_with("INSERT INTO orders VALUES (99)")

    def test_rows_affected_sentinel_when_none(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = MagicMock()
        exec_result = MagicMock(spec=[])
        exec_result.fetchall = MagicMock(return_value=[])
        conn.execute.return_value = exec_result

        result = bench.execute_operation("op1", conn)

        assert result.rows_affected == -1

    def test_returns_failure_result_on_exception(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("DB error")

        result = bench.execute_operation("op1", conn)

        assert result.success is False
        assert result.operation_id == "op1"
        assert "DB error" in (result.error or "")

    def test_rolls_back_after_failed_operation(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("DB error")

        result = bench.execute_operation("op1", conn)

        assert result.success is False
        conn.rollback.assert_called_once()


class TestSqlOverride:
    def test_sql_override_replaces_write_sql(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(write_sql="original SQL")
        conn = _make_connection()

        bench.execute_operation("op1", conn, sql_override="overridden SQL")

        conn.execute.assert_called_once_with("overridden SQL")

    def test_sql_override_takes_priority_over_platform_key(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="original SQL",
            platform_overrides={"datafusion": "platform SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, sql_override="direct override", platform_key="datafusion")

        conn.execute.assert_called_once_with("direct override")


class TestPlatformKeyDispatch:
    def test_uses_platform_override_when_key_matches(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"datafusion": "datafusion-specific SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="datafusion")

        conn.execute.assert_called_once_with("datafusion-specific SQL")

    def test_falls_back_to_write_sql_when_key_not_in_overrides(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"duckdb": "duckdb SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="datafusion")

        conn.execute.assert_called_once_with("generic SQL")

    def test_falls_back_to_write_sql_when_no_platform_key(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"datafusion": "datafusion SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn)

        conn.execute.assert_called_once_with("generic SQL")

    def test_rewrites_generate_series_for_postgres(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="""
                BEGIN TRANSACTION;
                INSERT INTO txn_orders
                SELECT 9200000 + n, 1
                FROM (SELECT unnest(generate_series(1, 100)) AS n) t;
                COMMIT;
            """,
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="postgres")

        executed_sql = conn.execute.call_args.args[0]
        assert "FROM (SELECT generate_series(1, 100) AS n) t" in executed_sql
        assert "unnest(generate_series" not in executed_sql

    @pytest.mark.parametrize(
        ("platform_key", "expected"),
        [
            ("snowflake", "FROM (SELECT value::INT AS n FROM TABLE(FLATTEN(ARRAY_GENERATE_RANGE(1, 100 + 1)))) t"),
            ("bigquery", "FROM (SELECT n FROM UNNEST(GENERATE_ARRAY(1, 100)) AS n) t"),
        ],
    )
    def test_rewrites_generate_series_for_cloud_dialects(self, tmp_path: Path, platform_key: str, expected: str):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="""
                BEGIN TRANSACTION;
                INSERT INTO txn_orders
                SELECT 9200000 + n, 1
                FROM (SELECT unnest(generate_series(1, 100)) AS n) t;
                COMMIT;
            """,
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key=platform_key)

        executed_sql = conn.execute.call_args.args[0]
        assert expected in executed_sql
        assert "generate_series" not in executed_sql

    def test_rewrites_isolation_level_begin_for_postgres(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="""
                SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
                BEGIN TRANSACTION;
                INSERT INTO txn_orders VALUES (1);
                COMMIT;
            """,
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="postgres")

        executed_sql = conn.execute.call_args.args[0]
        assert "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ;" in executed_sql
        assert "SET TRANSACTION ISOLATION LEVEL" not in executed_sql


class TestSkippedStatus:
    def test_returns_skipped_when_override_is_none(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            platform_overrides={"datafusion": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="datafusion")

        assert result.status == "SKIPPED"
        assert result.success is True

    def test_returns_skipped_for_pg_duckdb_savepoint_gap(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(id="transaction_savepoint_nested")
        conn = _make_connection()

        result = bench.execute_operation(
            "transaction_savepoint_nested",
            conn,
            platform_key="postgres",
            platform_name="pg_duckdb",
        )

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "SAVEPOINT is not supported" in (result.skip_reason or "")
        conn.execute.assert_not_called()

    def test_returns_skipped_for_duckdb_explicit_isolation_gap(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            id="transaction_isolation_read_committed",
            write_sql="SET TRANSACTION ISOLATION LEVEL READ COMMITTED; BEGIN TRANSACTION; COMMIT;",
        )
        conn = _make_connection()

        result = bench.execute_operation(
            "transaction_isolation_read_committed",
            conn,
            platform_key="duckdb",
            platform_name="DuckDB",
        )

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "SET TRANSACTION ISOLATION LEVEL" in (result.skip_reason or "")
        conn.execute.assert_not_called()

    def test_returns_skipped_for_timescaledb_non_default_isolation_gap(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            id="transaction_isolation_serializable",
            write_sql="SET TRANSACTION ISOLATION LEVEL SERIALIZABLE; BEGIN TRANSACTION; COMMIT;",
        )
        conn = _make_connection()

        result = bench.execute_operation(
            "transaction_isolation_serializable",
            conn,
            platform_key="postgres",
            platform_name="timescaledb",
        )

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "TimescaleDB" in (result.skip_reason or "")
        conn.execute.assert_not_called()

    def test_skipped_result_has_skip_reason_field(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            id="tricky_op",
            platform_overrides={"datafusion": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("tricky_op", conn, platform_key="datafusion")

        assert result.error is None
        assert result.skip_reason is not None
        assert "tricky_op" in result.skip_reason
        assert "datafusion" in result.skip_reason

    def test_skipped_result_has_zero_duration(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            platform_overrides={"duckdb": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="duckdb")

        assert result.write_duration_ms == 0.0
        assert result.rows_affected == 0

    def test_skipped_does_not_execute_sql(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            platform_overrides={"datafusion": None},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="datafusion")

        conn.execute.assert_not_called()

    def test_non_none_override_is_not_skipped(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            platform_overrides={"datafusion": "SELECT 1"},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="datafusion")

        assert result.status != "SKIPPED"
        assert result.success is True


class TestOperationResultStatus:
    def test_status_none_on_normal_success(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = _make_connection()

        result = bench.execute_operation("op1", conn)

        assert result.status is None

    def test_status_skipped_only_set_for_none_override(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            platform_overrides={"datafusion": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="datafusion")

        assert result.status == "SKIPPED"


class TestAutoSetup:
    def test_auto_setup_triggered_when_requires_setup_and_not_ready(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(requires_setup=True)

        conn = MagicMock()

        bench.is_setup = MagicMock(return_value=False)
        bench.setup = MagicMock()
        exec_result = MagicMock()
        exec_result.rowcount = 1
        exec_result.fetchall.return_value = []
        conn.execute.return_value = exec_result

        result = bench.execute_operation("op1", conn)
        assert result.success is True
        bench.setup.assert_called_once()

    def test_no_setup_when_requires_setup_false(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(requires_setup=False)
        bench.setup = MagicMock()

        conn = _make_connection()
        bench.execute_operation("op1", conn)

        bench.setup.assert_not_called()


class TestPlatformFallbackKey:
    def test_fallback_null_override_skips(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="SAVEPOINT sp1",
            platform_overrides={"duckdb": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="ducklake", platform_fallback_key="duckdb")

        assert result.status == "SKIPPED"
        conn.execute.assert_not_called()

    def test_fallback_string_override_used(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"duckdb": "duckdb SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="ducklake", platform_fallback_key="duckdb")

        conn.execute.assert_called_once_with("duckdb SQL")

    def test_engine_entry_wins_over_fallback(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"ducklake": "engine SQL", "duckdb": None},
        )
        conn = _make_connection()

        result = bench.execute_operation("op1", conn, platform_key="ducklake", platform_fallback_key="duckdb")

        assert result.status is None
        conn.execute.assert_called_once_with("engine SQL")

    def test_no_fallback_keeps_catalog_default(self, tmp_path: Path):
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(
            write_sql="generic SQL",
            platform_overrides={"duckdb": "duckdb SQL"},
        )
        conn = _make_connection()

        bench.execute_operation("op1", conn, platform_key="ducklake")

        conn.execute.assert_called_once_with("generic SQL")


class TestFailedPlatformPayload:
    def test_failed_write_reports_failed(self, tmp_path: Path):
        from types import SimpleNamespace

        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation()
        conn = _make_connection()
        conn.execute.return_value = SimpleNamespace(
            rowcount=0,
            platform_result={"status": "FAILED", "error": "Actual statement count 2 did not match"},
        )

        result = bench.execute_operation("op1", conn)

        assert result.success is False
        assert "Actual statement count 2" in (result.error or "")

    def test_failed_validation_select_fails_validation(self, tmp_path: Path):
        from types import SimpleNamespace

        validation_query = SimpleNamespace(id="cnt", sql="SELECT COUNT(*) FROM t", expected_rows=1)
        bench = _make_benchmark(tmp_path)
        bench.operations_manager.get_operation.return_value = _make_operation(validation_queries=[validation_query])
        conn = _make_connection()
        failed = SimpleNamespace(
            rowcount=0,
            platform_result={"status": "FAILED", "error": "Object does not exist"},
        )
        conn.execute.side_effect = [_make_connection().execute.return_value, failed]

        result = bench.execute_operation("op1", conn)

        assert result.validation_passed is False


@pytest.fixture()
def sqlite_staged(tmp_path: Path):
    from benchbox.core.transaction_primitives.schema import get_all_staging_tables_sql
    from benchbox.platforms.sqlite import SQLiteAdapter

    conn = SQLiteAdapter(database_path=":memory:").create_connection()
    conn.executescript(get_all_staging_tables_sql("sqlite"))
    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    bench.is_setup = lambda connection: True
    yield bench, conn
    conn.close()


@pytest.mark.parametrize(
    ("operation_id", "expected_first_value"),
    [
        ("transaction_commit_small", 9100001),
        ("transaction_commit_medium", 100),
        ("transaction_commit_large", 1000),
        ("transaction_rollback_medium", 0),
        ("transaction_savepoint_deep_nesting", 4),
        ("transaction_isolation_serializable", 2),
        ("transaction_truncate_in_transaction", 1),
        ("transaction_with_cte", 10),
    ],
)
def test_sqlite_runs_multi_statement_operations_repeatably(
    sqlite_staged, operation_id: str, expected_first_value: int
) -> None:
    bench, conn = sqlite_staged
    for _ in range(2):
        result = bench.execute_operation(operation_id, conn, platform_key="sqlite", platform_name="sqlite")
        assert result.status is None, result.error
        assert result.success
        assert result.cleanup_success
        assert result.validation_results[0]["sample"][0][0] == expected_first_value
    assert conn.execute("SELECT COUNT(*) FROM txn_orders WHERE o_orderkey >= 9000000").fetchone() == (0,)


@pytest.mark.parametrize(
    "operation_id", ["transaction_isolation_read_committed", "transaction_isolation_repeatable_read"]
)
def test_sqlite_skips_unsupported_isolation_levels(sqlite_staged, operation_id: str) -> None:
    bench, conn = sqlite_staged
    result = bench.execute_operation(operation_id, conn, platform_key="sqlite", platform_name="sqlite")
    assert result.status == "SKIPPED"
    assert "SERIALIZABLE" in (result.skip_reason or "")
