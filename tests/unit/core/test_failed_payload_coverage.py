# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from benchbox.core.primitives_benchmark_utils import (
    failed_platform_error,
    fetch_count_probe,
    table_exists,
)
from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.transaction_primitives.catalog.loader import WriteOperation
from benchbox.core.transactional.benchmark_base import TransactionalBenchmarkBase
from benchbox.core.tuning.metadata import TuningMetadataManager
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class MockFailedCursor:
    def __init__(self, error_message: str = "Simulated platform error"):
        self.platform_result = {"status": "FAILED", "error": error_message}
        self.rowcount = -1

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter([])


class MockOkCursor:
    def __init__(self, rows: list[tuple] | None = None, rowcount: int = 1):
        self.rows = rows or [(1,)]
        self.platform_result = {"status": "SUCCESS", "rows_returned": len(self.rows)}
        self.rowcount = rowcount

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class MockDatabaseConnection:
    def __init__(self, default_cursor: Any = None):
        self.default_cursor = default_cursor or MockOkCursor()
        self.query_responses: dict[str, Any] = {}
        self.executed_queries: list[str] = []

    def execute(self, sql: str, *args, **kwargs):
        self.executed_queries.append(sql)
        for pattern, cursor in self.query_responses.items():
            if pattern in sql:
                return cursor
        return self.default_cursor

    def cursor(self):
        return self


class TestPrimitivesBenchmarkUtilsFailedPayload:
    def test_failed_platform_error_with_cursor_platform_result(self):
        cursor = MockFailedCursor("Query syntax error near line 1")
        assert failed_platform_error(cursor) == "Query syntax error near line 1"

    def test_failed_platform_error_with_direct_dict(self):
        payload = {"status": "FAILED", "error": "Direct dict error"}
        assert failed_platform_error(payload) == "Direct dict error"

    def test_failed_platform_error_with_ok_cursor(self):
        cursor = MockOkCursor([(42,)])
        assert failed_platform_error(cursor) is None

    def test_failed_platform_error_with_none_or_plain_obj(self):
        assert failed_platform_error(None) is None
        assert failed_platform_error(object()) is None
        assert failed_platform_error({"status": "SUCCESS"}) is None

    def test_table_exists_returns_false_on_failed_cursor_table_not_found(self):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Table orders does not exist in catalog"))
        logs = []
        exists = table_exists(conn, "orders", log_verbose=logs.append)
        assert exists is False

    def test_table_exists_returns_false_and_logs_on_failed_cursor_unexpected_error(self):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Internal cluster timeout"))
        logs = []
        exists = table_exists(conn, "orders", log_verbose=logs.append)
        assert exists is False
        assert any("Unexpected error checking table 'orders'" in msg for msg in logs)

    def test_table_exists_returns_true_on_ok_cursor(self):
        conn = MockDatabaseConnection(default_cursor=MockOkCursor())
        exists = table_exists(conn, "orders", log_verbose=lambda _: None)
        assert exists is True

    def test_fetch_count_probe_raises_on_failed_cursor(self):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Syntax error in count"))
        with pytest.raises(RuntimeError, match="Count probe failed: Syntax error in count"):
            fetch_count_probe(conn, "SELECT COUNT(*) FROM orders")

    def test_fetch_count_probe_returns_count_on_ok_cursor(self):
        conn = MockDatabaseConnection(default_cursor=MockOkCursor(rows=[(105,)]))
        assert fetch_count_probe(conn, "SELECT COUNT(*) FROM orders") == 105


class ConcreteTransactionalBenchmark(TransactionalBenchmarkBase[Any]):
    def generate_data(self, *args, **kwargs):
        pass

    def get_data_dir(self, *args, **kwargs):
        return None

    def setup(self, connection, force=False, dialect="standard"):
        pass

    def run_benchmark(self, connection, *args, **kwargs):
        pass

    def teardown(self, connection):
        pass

    def is_setup(self, connection):
        return True

    def execute_operation(self, *args, **kwargs):
        pass


class TestTransactionalBenchmarkBaseFailedPayload:
    @pytest.fixture
    def tx_benchmark(self, tmp_path):
        return ConcreteTransactionalBenchmark(output_dir=tmp_path)

    def test_staging_source_digest_handles_failed_count_probe(self, tx_benchmark):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Failed to count table orders"))
        digest = tx_benchmark._staging_source_digest(conn, ["orders", "lineitem"])
        assert isinstance(digest, str)
        assert len(digest) > 0

    def test_write_staging_manifest_raises_on_create_failure(self, tx_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["CREATE TABLE IF NOT EXISTS"] = MockFailedCursor("DDL error")
        with pytest.raises(RuntimeError, match="Failed to create staging manifest table"):
            tx_benchmark._write_staging_manifest(conn, ["orders"])

    def test_write_staging_manifest_raises_on_delete_failure(self, tx_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["DELETE FROM"] = MockFailedCursor("Delete permission denied")
        with pytest.raises(RuntimeError, match="Failed to delete previous staging manifest entry"):
            tx_benchmark._write_staging_manifest(conn, ["orders"])

    def test_write_staging_manifest_raises_on_insert_failure(self, tx_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["INSERT INTO"] = MockFailedCursor("Insert quota exceeded")
        with pytest.raises(RuntimeError, match="Failed to insert staging manifest entry"):
            tx_benchmark._write_staging_manifest(conn, ["orders"])

    def test_staging_manifest_matches_returns_false_on_failed_probe(self, tx_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["source_digest"] = MockFailedCursor("Catalog lookup failed")
        assert tx_benchmark._staging_manifest_matches(conn, ["orders"]) is False


class TestTransactionPrimitivesFailedPayload:
    @pytest.fixture
    def tp_benchmark(self, tmp_path):
        return TransactionPrimitivesBenchmark(output_dir=tmp_path)

    def test_acquire_setup_lock_fails_on_create_table_failure(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["CREATE TABLE IF NOT EXISTS transaction_primitives_setup_lock"] = MockFailedCursor(
            "Lock table creation denied"
        )
        assert tp_benchmark._acquire_setup_lock(conn, timeout_seconds=1) is False

    def test_acquire_setup_lock_fails_on_insert_unexpected_error(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["INSERT INTO transaction_primitives_setup_lock"] = MockFailedCursor("Disk full")
        assert tp_benchmark._acquire_setup_lock(conn, timeout_seconds=1) is False

    def test_populate_staging_table_raises_on_failure(self, tp_benchmark):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Insert select failed"))
        with pytest.raises(RuntimeError, match="Failed to populate txn_orders from orders"):
            tp_benchmark._populate_staging_table(conn, "txn_orders", "orders")

    def test_setup_raises_when_source_table_probe_fails(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["SELECT 1 FROM orders LIMIT 1"] = MockFailedCursor("orders table missing")
        with pytest.raises(RuntimeError, match="Required TPC-H table 'orders' not found"):
            tp_benchmark.setup(conn)

    def test_setup_raises_when_table_create_fails(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["txn_orders"] = MockFailedCursor("DDL error on txn_orders")
        with pytest.raises(RuntimeError, match="Failed to create txn_orders"):
            tp_benchmark.setup(conn)

    def test_is_setup_returns_false_when_count_probe_fails(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["SELECT COUNT(*) FROM"] = MockFailedCursor("Timeout reading count")
        assert tp_benchmark.is_setup(conn) is False

    def test_operation_cleanup_failure_records_cleanup_warning(self, tp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["INSERT INTO txn_orders"] = MockOkCursor(rowcount=1)
        conn.query_responses["SELECT COUNT(*) FROM txn_orders"] = MockOkCursor(rows=[(1,)])
        conn.query_responses["DELETE FROM txn_orders"] = MockFailedCursor("Cleanup DELETE failed on engine")

        op = WriteOperation(
            id="test_cleanup_failure_op",
            category="insert",
            description="Test Cleanup Failure",
            write_sql="INSERT INTO txn_orders VALUES (1)",
            cleanup_sql="DELETE FROM txn_orders WHERE o_orderkey = 1",
            validation_queries=[],
            requires_setup=False,
        )
        tp_benchmark.operations_manager = MagicMock()
        tp_benchmark.operations_manager.get_operation.return_value = op

        res = tp_benchmark.execute_operation("test_cleanup_failure_op", conn)
        assert res.cleanup_success is False
        assert res.cleanup_warning is not None
        assert "Cleanup DELETE failed on engine" in res.cleanup_warning


class TestWritePrimitivesFailedPayload:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_acquire_setup_lock_fails_on_create_table_failure(self, wp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["CREATE TABLE IF NOT EXISTS write_primitives_setup_lock"] = MockFailedCursor(
            "Lock table creation denied"
        )
        assert wp_benchmark._acquire_setup_lock(conn, timeout_seconds=1) is False

    def test_acquire_setup_lock_fails_on_insert_unexpected_error(self, wp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["INSERT INTO write_primitives_setup_lock"] = MockFailedCursor("Deadlock detected")
        assert wp_benchmark._acquire_setup_lock(conn, timeout_seconds=1) is False

    def test_execute_population_sql_raises_on_failure(self, wp_benchmark):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Population error"))
        wp_benchmark._setup_dialect = "standard"
        with pytest.raises(RuntimeError, match="Population SQL failed: Population error"):
            wp_benchmark._execute_population_sql(conn, "INSERT INTO table SELECT * FROM src")

    def test_setup_raises_when_source_table_probe_fails(self, wp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["SELECT 1 FROM orders LIMIT 1"] = MockFailedCursor("orders table missing")
        with pytest.raises(RuntimeError, match="Required TPC-H table 'orders' not found"):
            wp_benchmark.setup(conn)

    def test_setup_raises_when_table_create_fails(self, wp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["update_ops_orders"] = MockFailedCursor("DDL error on staging table")
        with pytest.raises(RuntimeError, match="Failed to create update_ops_orders"):
            wp_benchmark.setup(conn)

    def test_is_setup_returns_false_when_count_probe_fails(self, wp_benchmark):
        conn = MockDatabaseConnection()
        conn.query_responses["SELECT COUNT(*) FROM"] = MockFailedCursor("Timeout reading count")
        assert wp_benchmark.is_setup(conn) is False

    def test_run_operation_cleanup_sets_cleanup_warning_on_failed_cursor(self, wp_benchmark):
        conn = MockDatabaseConnection(default_cursor=MockFailedCursor("Cleanup error on engine"))
        mock_op = MagicMock()
        mock_op.cleanup_sql = "DELETE FROM update_ops_orders WHERE 1=1"

        success, warning, _ = wp_benchmark._run_operation_cleanup(mock_op, conn, "test_op")
        assert success is False
        assert warning is not None
        assert "Cleanup error on engine" in warning


class TestTuningMetadataManagerFailedPayload:
    @pytest.fixture
    def manager(self):
        mock_adapter = MagicMock()
        mock_adapter.create_connection.return_value = MockDatabaseConnection()
        mock_adapter.close_connection.return_value = None
        return TuningMetadataManager(platform_adapter=mock_adapter)

    def test_execute_sql_raises_on_failed_platform_result(self, manager):
        manager.platform_adapter.execute_query.return_value = {
            "status": "FAILED",
            "error": "Syntax error in metadata statement",
        }
        with pytest.raises(RuntimeError, match="Tuning metadata execution failed: Syntax error in metadata statement"):
            manager._execute_sql(None, "CREATE TABLE test")

    def test_execute_sql_raises_on_failed_cursor_fallback(self, manager):
        del manager.platform_adapter.execute_query
        conn = MagicMock()
        cursor = MockFailedCursor("Cursor level failure")
        conn.cursor.return_value = cursor
        with pytest.raises(RuntimeError, match="Tuning metadata execution failed: Cursor level failure"):
            manager._execute_sql(conn, "CREATE TABLE test")

    def test_fetch_all_raises_on_failed_execute_result(self, manager):
        conn = MagicMock(spec=["execute"])
        conn.execute.return_value = MockFailedCursor("SELECT metadata query failed")
        with pytest.raises(RuntimeError, match="Tuning metadata query failed: SELECT metadata query failed"):
            manager._fetch_all(conn, "SELECT 1")

    def test_fetch_all_raises_on_failed_cursor_result(self, manager):
        conn = MagicMock(spec=["cursor"])
        cursor = MockFailedCursor("SELECT metadata query failed in cursor")
        conn.cursor.return_value = cursor
        with pytest.raises(RuntimeError, match="Tuning metadata query failed: SELECT metadata query failed in cursor"):
            manager._fetch_all(conn, "SELECT 1")

    def test_fetch_one_raises_on_failed_execute_result(self, manager):
        conn = MagicMock(spec=["execute"])
        conn.execute.return_value = MockFailedCursor("SELECT 1 metadata query failed")
        with pytest.raises(RuntimeError, match="Tuning metadata query failed: SELECT 1 metadata query failed"):
            manager._fetch_one(conn, "SELECT 1")

    def test_fetch_one_raises_on_failed_cursor_result(self, manager):
        conn = MagicMock(spec=["cursor"])
        cursor = MockFailedCursor("SELECT 1 metadata query failed in cursor")
        conn.cursor.return_value = cursor
        with pytest.raises(
            RuntimeError, match="Tuning metadata query failed: SELECT 1 metadata query failed in cursor"
        ):
            manager._fetch_one(conn, "SELECT 1")


def test_replace_table_sql_only_rewrites_plain_create():
    from benchbox.core.primitives_benchmark_utils import replace_table_sql, replaces_tables_in_place

    assert replace_table_sql("CREATE TABLE t (a INT);") == "CREATE OR REPLACE TABLE t (a INT);"
    with pytest.raises(ValueError):
        replace_table_sql("CREATE TABLE IF NOT EXISTS t (a INT);")
    assert replaces_tables_in_place("Databricks") is True
    assert replaces_tables_in_place("snowflake") is False
    assert replaces_tables_in_place(None) is False


