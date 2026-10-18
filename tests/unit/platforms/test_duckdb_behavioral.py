from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture()
def adapter():
    from benchbox.platforms.duckdb import DuckDBAdapter

    return DuckDBAdapter(database_path=":memory:", memory_limit="256MB")


@pytest.fixture()
def conn(adapter):
    connection = adapter.create_connection()
    yield connection
    try:
        connection.close()
    except Exception:
        pass


def _write_parquet(path: Path, table: pa.Table) -> Path:
    pq.write_table(table, str(path))
    return path


class TestNormalizeDuckdbVersion:
    def test_strips_v_prefix(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version("v1.2.3") == "1.2.3"

    def test_no_prefix(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version("1.2.3") == "1.2.3"

    def test_none_returns_none(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version(None) is None

    def test_empty_string_returns_none(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version("") is None

    def test_whitespace_only_returns_none(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version("   ") is None

    def test_v_only_no_digit_keeps_string(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version("vbeta") == "vbeta"

    def test_integer_input(self):
        from benchbox.platforms.duckdb import _normalize_duckdb_version

        assert _normalize_duckdb_version(123) == "123"


class TestBuildDuckdbCtasSortSql:
    def test_single_column(self):
        from benchbox.core.tuning.interface import TuningColumn
        from benchbox.platforms.duckdb import _build_duckdb_ctas_sort_sql

        col = TuningColumn(name="l_shipdate", type="DATE", order=1)
        result = _build_duckdb_ctas_sort_sql("lineitem", [col])
        assert result == "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate;"

    def test_multiple_columns(self):
        from benchbox.core.tuning.interface import TuningColumn
        from benchbox.platforms.duckdb import _build_duckdb_ctas_sort_sql

        col_a = TuningColumn(name="l_shipdate", type="DATE", order=1)
        col_b = TuningColumn(name="l_orderkey", type="INTEGER", order=2)
        result = _build_duckdb_ctas_sort_sql("lineitem", [col_a, col_b])
        assert "ORDER BY l_shipdate, l_orderkey" in result


class TestExplainOutput:
    def test_explain_returns_json_string(self, adapter, conn):
        conn.execute("CREATE TABLE test_explain (pk INT, val VARCHAR)")
        conn.execute("INSERT INTO test_explain VALUES (1, 'a'), (2, 'b')")

        plan = adapter.get_query_plan(conn, "SELECT * FROM test_explain WHERE pk = 1")
        assert plan is not None

        parsed = json.loads(plan)
        assert isinstance(parsed, (dict, list))

    def test_explain_with_aggregation(self, adapter, conn):
        conn.execute("CREATE TABLE agg_test (category VARCHAR, amount INT)")
        conn.execute("INSERT INTO agg_test VALUES ('a', 10), ('a', 20), ('b', 30)")

        plan = adapter.get_query_plan(conn, "SELECT category, SUM(amount) FROM agg_test GROUP BY category")
        assert plan is not None
        parsed = json.loads(plan)
        assert isinstance(parsed, (dict, list))

    def test_explain_invalid_query_returns_none(self, adapter, conn):
        plan = adapter.get_query_plan(conn, "SELECTX INVALID")
        assert plan is None


class TestExternalTableMode:
    def test_parquet_view_creation(self, conn, tmp_path):

        table = pa.table({"region_key": [0, 1, 2], "region_name": ["AFRICA", "AMERICA", "ASIA"]})
        parquet_path = _write_parquet(tmp_path / "region.parquet", table)

        conn.execute(f"CREATE VIEW region AS SELECT * FROM read_parquet('{parquet_path}')")
        rows = conn.execute("SELECT COUNT(*) FROM region").fetchone()
        assert rows == (3,)

    def test_parquet_view_query(self, conn, tmp_path):
        table = pa.table({"order_id": [100, 200, 300], "status": ["open", "closed", "open"]})
        parquet_path = _write_parquet(tmp_path / "orders.parquet", table)

        conn.execute(f"CREATE VIEW orders AS SELECT * FROM read_parquet('{parquet_path}')")
        rows = conn.execute("SELECT order_id FROM orders WHERE status = 'open' ORDER BY order_id").fetchall()
        assert rows == [(100,), (300,)]

    def test_multiple_parquet_files_view(self, conn, tmp_path):
        table1 = pa.table({"val": [1, 2]})
        table2 = pa.table({"val": [3, 4]})
        path1 = _write_parquet(tmp_path / "part1.parquet", table1)
        path2 = _write_parquet(tmp_path / "part2.parquet", table2)

        conn.execute(f"CREATE VIEW combined AS SELECT * FROM read_parquet(['{path1}', '{path2}'])")
        row = conn.execute("SELECT COUNT(*) FROM combined").fetchone()
        assert row == (4,)


class TestExtensionsAndVersion:
    def test_version_detection(self, adapter, conn):

        version = adapter._detect_connection_version(conn)
        assert version is not None
        assert "." in version

    def test_version_detection_none_connection(self, adapter):
        assert adapter._detect_connection_version(None) is None

    def test_httpfs_extension_loads(self, conn):

        try:
            conn.execute("INSTALL httpfs")
            conn.execute("LOAD httpfs")
        except Exception:
            pytest.skip("httpfs extension not available in this DuckDB build")

        rows = conn.execute("SELECT extension_name FROM duckdb_extensions() WHERE loaded = true").fetchall()
        extension_names = [row[0] for row in rows]
        assert "httpfs" in extension_names

    def test_json_extension_loads(self, conn):

        try:
            conn.execute("INSTALL json")
            conn.execute("LOAD json")
        except Exception:
            pytest.skip("json extension not available in this DuckDB build")

        rows = conn.execute("SELECT extension_name FROM duckdb_extensions() WHERE loaded = true").fetchall()
        extension_names = [row[0] for row in rows]
        assert "json" in extension_names


class TestSortedLoading:
    def test_ctas_sort_reorders_data(self, conn):

        conn.execute("CREATE TABLE unsorted (pk INT, val VARCHAR)")
        conn.execute("INSERT INTO unsorted VALUES (3, 'c'), (1, 'a'), (2, 'b')")

        conn.execute("CREATE OR REPLACE TABLE unsorted AS SELECT * FROM unsorted ORDER BY pk")

        rows = conn.execute("SELECT pk, val FROM unsorted").fetchall()
        pkeys = [row[0] for row in rows]
        assert pkeys == [1, 2, 3]

    def test_ctas_sort_multiple_columns(self, conn):
        conn.execute("CREATE TABLE multi_sort (category VARCHAR, priority INT, label VARCHAR)")
        conn.execute("INSERT INTO multi_sort VALUES ('b', 2, 'x'), ('a', 1, 'y'), ('a', 2, 'z'), ('b', 1, 'w')")

        conn.execute("CREATE OR REPLACE TABLE multi_sort AS SELECT * FROM multi_sort ORDER BY category, priority")

        rows = conn.execute("SELECT category, priority FROM multi_sort").fetchall()
        assert rows == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]


class TestDuckDBConnectionWrapper:
    def test_dry_run_execute_returns_cursor(self):
        from benchbox.platforms.duckdb import DuckDBAdapter, DuckDBConnectionWrapper

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()
        real_conn = adapter._duckdb_module.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        cursor = wrapper.execute("SELECT 1")
        assert cursor.fetchone() is None
        assert cursor.fetchall() == []
        real_conn.close()

    def test_dry_run_commit_is_noop(self):
        from benchbox.platforms.duckdb import DuckDBAdapter, DuckDBConnectionWrapper

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()
        real_conn = adapter._duckdb_module.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        wrapper.commit()
        real_conn.close()

    def test_dry_run_close_is_noop(self):
        from benchbox.platforms.duckdb import DuckDBAdapter, DuckDBConnectionWrapper

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()
        real_conn = adapter._duckdb_module.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)
        wrapper.close()

        result = real_conn.execute("SELECT 1").fetchone()
        assert result == (1,)
        real_conn.close()


class TestDuckDBCursorWrapper:
    def test_fetchall_returns_rows(self):
        from benchbox.platforms.duckdb import DuckDBCursorWrapper

        adapter = MagicMock()
        cursor = DuckDBCursorWrapper([(1, "a"), (2, "b")], adapter)
        assert cursor.fetchall() == [(1, "a"), (2, "b")]

    def test_fetchone_returns_first_row(self):
        from benchbox.platforms.duckdb import DuckDBCursorWrapper

        adapter = MagicMock()
        cursor = DuckDBCursorWrapper([(42, "x")], adapter)
        assert cursor.fetchone() == (42, "x")

    def test_fetchone_empty_returns_none(self):
        from benchbox.platforms.duckdb import DuckDBCursorWrapper

        adapter = MagicMock()
        cursor = DuckDBCursorWrapper([], adapter)
        assert cursor.fetchone() is None

    def test_fetchmany_with_size(self):
        from benchbox.platforms.duckdb import DuckDBCursorWrapper

        adapter = MagicMock()
        cursor = DuckDBCursorWrapper([(1,), (2,), (3,)], adapter)
        assert cursor.fetchmany(2) == [(1,), (2,)]

    def test_fetchmany_without_size(self):
        from benchbox.platforms.duckdb import DuckDBCursorWrapper

        adapter = MagicMock()
        cursor = DuckDBCursorWrapper([(1,), (2,)], adapter)
        assert cursor.fetchmany() == [(1,), (2,)]


class TestGetDatabasePath:
    def test_connection_config_takes_priority(self, adapter):
        assert adapter.get_database_path(database_path="/tmp/override.db") == "/tmp/override.db"

    def test_falls_back_to_instance_path(self, adapter):
        assert adapter.get_database_path() == ":memory:"

    def test_final_fallback_to_memory(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter()
        adapter.database_path = None
        assert adapter.get_database_path() == ":memory:"


class TestExecuteQueryDryRun:
    def test_dry_run_returns_synthetic_result(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()
        conn = adapter.create_connection()

        result = adapter.execute_query(
            connection=conn,
            query="SELECT 1",
            query_id="dry_q1",
            validate_row_count=False,
        )
        assert result["query_id"] == "dry_q1"
        assert result["status"] == "DRY_RUN"
        assert result["execution_time_seconds"] == 0.0
        assert result["dry_run"] is True
