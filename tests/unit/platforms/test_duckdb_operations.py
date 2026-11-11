from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from benchbox.platforms.duckdb import (
    DuckDBAdapter,
    DuckDBConnectionWrapper,
    DuckDBCursorWrapper,
    _build_csv_scan_expression,
    _build_duckdb_ctas_sort_sql,
    _build_duckdb_external_scan_expression,
    _normalize_duckdb_version,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestNormalizeDuckDBVersion:
    def test_strips_v_prefix(self):
        assert _normalize_duckdb_version("v1.2.3") == "1.2.3"

    def test_preserves_plain_semver(self):
        assert _normalize_duckdb_version("1.2.3") == "1.2.3"

    def test_returns_none_for_none(self):
        assert _normalize_duckdb_version(None) is None

    def test_returns_none_for_empty_string(self):
        assert _normalize_duckdb_version("") is None

    def test_returns_none_for_whitespace(self):
        assert _normalize_duckdb_version("   ") is None

    def test_strips_whitespace_around_version(self):
        assert _normalize_duckdb_version("  v0.9.2  ") == "0.9.2"

    def test_v_not_followed_by_digit_is_kept(self):

        assert _normalize_duckdb_version("vNext") == "vNext"

    def test_integer_input_coerced_to_string(self):

        assert _normalize_duckdb_version(123) == "123"

    def test_dev_version_string(self):
        assert _normalize_duckdb_version("v1.3.0-dev123") == "1.3.0-dev123"


class TestDuckDBConnectionWrapper:
    def test_execute_in_dry_run_captures_sql(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()

        real_conn = duckdb.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        cursor = wrapper.execute("SELECT 42")

        assert any("SELECT 42" in entry.get("sql", "") for entry in adapter.captured_sql)

        assert isinstance(cursor, DuckDBCursorWrapper)
        real_conn.close()

    def test_execute_in_normal_mode_delegates(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.dry_run_mode = False

        real_conn = duckdb.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        result = wrapper.execute("SELECT 99 AS val")
        row = result.fetchone()
        assert row == (99,)
        real_conn.close()

    def test_commit_noop_in_dry_run(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()

        real_conn = duckdb.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        wrapper.commit()
        real_conn.close()

    def test_close_noop_in_dry_run(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.enable_dry_run()

        real_conn = duckdb.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)
        wrapper.close()

        row = real_conn.execute("SELECT 1").fetchone()
        assert row == (1,)
        real_conn.close()

    def test_getattr_delegates_to_real_connection(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        adapter.dry_run_mode = False

        real_conn = duckdb.connect(":memory:")
        wrapper = DuckDBConnectionWrapper(real_conn, adapter)

        assert wrapper._connection is real_conn
        real_conn.close()


class TestDuckDBCursorWrapper:
    def test_fetchall_returns_provided_rows(self):
        adapter = Mock()
        cursor = DuckDBCursorWrapper([(1,), (2,), (3,)], adapter)
        assert cursor.fetchall() == [(1,), (2,), (3,)]

    def test_fetchone_returns_first_row(self):
        adapter = Mock()
        cursor = DuckDBCursorWrapper([(42,)], adapter)
        assert cursor.fetchone() == (42,)

    def test_fetchone_returns_none_for_empty(self):
        adapter = Mock()
        cursor = DuckDBCursorWrapper([], adapter)
        assert cursor.fetchone() is None

    def test_fetchmany_returns_slice(self):
        adapter = Mock()
        cursor = DuckDBCursorWrapper([(1,), (2,), (3,), (4,)], adapter)
        assert cursor.fetchmany(2) == [(1,), (2,)]

    def test_fetchmany_without_size_returns_all(self):
        adapter = Mock()
        cursor = DuckDBCursorWrapper([(1,), (2,)], adapter)
        assert cursor.fetchmany() == [(1,), (2,)]


class TestBuildDuckdbCtasSortSql:
    def test_single_column_sort(self):
        from benchbox.core.tuning.interface import TuningColumn

        col = TuningColumn(name="l_shipdate", type="DATE", order=1)
        sql = _build_duckdb_ctas_sort_sql("lineitem", [col])
        assert sql == "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate;"

    def test_multi_column_preserves_order(self):
        from benchbox.core.tuning.interface import TuningColumn

        c1 = TuningColumn(name="l_shipdate", type="DATE", order=1)
        c2 = TuningColumn(name="l_orderkey", type="INTEGER", order=2)
        sql = _build_duckdb_ctas_sort_sql("lineitem", [c1, c2])
        assert "ORDER BY l_shipdate, l_orderkey" in sql


class TestBuildExternalScanExpressionParquet:
    def test_single_parquet_file(self, tmp_path):
        pq = tmp_path / "lineitem.parquet"
        pq.touch()

        conn = duckdb.connect(":memory:")
        expr, d, v, i, fmt = _build_duckdb_external_scan_expression(
            conn, [pq], delta_extension_loaded=False, vortex_extension_loaded=False
        )
        assert "read_parquet(" in expr
        assert str(pq) in expr
        assert fmt == "parquet"
        conn.close()

    def test_multiple_parquet_files_produce_array_syntax(self, tmp_path):
        pq1 = tmp_path / "part1.parquet"
        pq2 = tmp_path / "part2.parquet"
        pq1.touch()
        pq2.touch()

        conn = duckdb.connect(":memory:")
        expr, d, v, i, fmt = _build_duckdb_external_scan_expression(
            conn, [pq1, pq2], delta_extension_loaded=False, vortex_extension_loaded=False
        )
        assert "read_parquet([" in expr
        assert str(pq1) in expr
        assert str(pq2) in expr
        assert fmt == "parquet"
        conn.close()

    def test_parquet_directory_scan(self, tmp_path):

        pq_dir = tmp_path / "data"
        pq_dir.mkdir()
        (pq_dir / "chunk_0.parquet").touch()
        (pq_dir / "chunk_1.parquet").touch()

        conn = duckdb.connect(":memory:")
        expr, d, v, i, fmt = _build_duckdb_external_scan_expression(
            conn, [pq_dir], delta_extension_loaded=False, vortex_extension_loaded=False
        )
        assert "read_parquet(" in expr
        assert fmt == "parquet"
        conn.close()


class TestBuildExternalScanExpressionCSV:
    def test_tbl_file_uses_read_csv(self, tmp_path):
        tbl = tmp_path / "orders.tbl"
        tbl.write_text("1|data|here|\n2|more|data|\n")

        conn = duckdb.connect(":memory:")
        expr, d, v, i, fmt = _build_duckdb_external_scan_expression(
            conn,
            [tbl],
            delta_extension_loaded=False,
            vortex_extension_loaded=False,
            column_names=["o_orderkey", "o_name", "o_status"],
        )
        assert "read_csv(" in expr
        assert fmt == "tbl"
        conn.close()

    def test_csv_file_uses_read_csv(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("1,Alice,100\n2,Bob,200\n")

        conn = duckdb.connect(":memory:")
        expr, d, v, i, fmt = _build_duckdb_external_scan_expression(
            conn,
            [csv],
            delta_extension_loaded=False,
            vortex_extension_loaded=False,
            column_names=["id", "name", "value"],
        )
        assert "read_csv(" in expr
        assert fmt == "csv"
        conn.close()

    def test_empty_directory_raises(self, tmp_path):

        empty_dir = tmp_path / "empty_data"
        empty_dir.mkdir()

        (empty_dir / "readme.txt").write_text("not data")

        conn = duckdb.connect(":memory:")
        with pytest.raises(RuntimeError, match="requires Parquet"):
            _build_duckdb_external_scan_expression(
                conn, [empty_dir], delta_extension_loaded=False, vortex_extension_loaded=False
            )
        conn.close()


class TestBuildCsvScanExpression:
    def test_single_file_no_column_names(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("1,2,3\n4,5,6\n")
        expr = _build_csv_scan_expression([csv], column_names=None)
        assert "read_csv(" in expr
        assert "auto_detect=true" in expr

    def test_single_tbl_file_with_column_names(self, tmp_path):
        tbl = tmp_path / "region.tbl"
        tbl.write_text("0|AFRICA|\n1|AMERICA|\n")
        expr = _build_csv_scan_expression([tbl], column_names=["r_regionkey", "r_name"])
        assert "read_csv(" in expr
        assert "r_regionkey" in expr
        assert "r_name" in expr
        assert "header=false" in expr

    def test_multiple_csv_files_produce_array_path(self, tmp_path):
        csv1 = tmp_path / "part1.csv"
        csv2 = tmp_path / "part2.csv"
        csv1.write_text("1,a\n")
        csv2.write_text("2,b\n")
        expr = _build_csv_scan_expression([csv1, csv2], column_names=["id", "name"])
        assert "[" in expr


class TestDuckDBGetQueryPlanJSON:
    @pytest.fixture()
    def adapter(self):
        return DuckDBAdapter(database_path=":memory:", capture_plans=True)

    def test_get_query_plan_returns_json(self, adapter):
        conn = adapter.create_connection()
        try:
            raw = adapter.get_query_plan(conn, "SELECT 1 AS x")
            assert raw is not None
            stripped = raw.strip()
            assert stripped.startswith("{") or stripped.startswith("["), f"Expected JSON from EXPLAIN, got: {raw[:120]}"
        finally:
            conn.close()

    def test_get_query_plan_with_table_scan(self, adapter):
        conn = adapter.create_connection()
        try:
            conn.execute("CREATE TABLE t (id INTEGER, val VARCHAR)")
            conn.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b')")
            raw = adapter.get_query_plan(conn, "SELECT * FROM t WHERE id = 1")
            assert raw is not None
            assert "FILTER" in raw.upper() or "SEQ_SCAN" in raw.upper() or "TABLE_SCAN" in raw.upper() or "Scan" in raw
        finally:
            conn.close()

    def test_analyze_plans_false_omits_timing(self):
        adapter = DuckDBAdapter(database_path=":memory:", capture_plans=True, analyze_plans=False)
        conn = adapter.create_connection()
        try:
            raw = adapter.get_query_plan(conn, "SELECT 1")
            assert raw is not None

            assert "operator_timing" not in raw or '"operator_timing": 0' in raw
        finally:
            conn.close()


class TestDuckDBDryRunExecution:
    def test_dry_run_returns_synthetic_result(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        conn = adapter.create_connection()

        adapter.enable_dry_run()
        try:
            result = adapter.execute_query(
                connection=conn,
                query="SELECT 42 AS answer",
                query_id="dry_q1",
                validate_row_count=False,
            )
            assert result["status"] == "DRY_RUN"
            assert result["dry_run"] is True
            assert result["rows_returned"] == 0
            assert result["execution_time_seconds"] == 0.0
        finally:
            adapter.disable_dry_run()
            conn.close()


class TestDuckDBPlatformInfo:
    def test_platform_info_with_connection(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        conn = adapter.create_connection()
        try:
            info = adapter.get_platform_info(connection=conn)
            assert info["platform_type"] == "duckdb"
            assert info["platform_name"] == "DuckDB"
            assert info["platform_version"] is not None

            assert "." in info["platform_version"]
        finally:
            conn.close()

    def test_platform_info_without_connection(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        info = adapter.get_platform_info(connection=None)
        assert info["platform_type"] == "duckdb"

        assert info["client_library_version"] is not None

    def test_connection_mode_in_platform_info(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        info = adapter.get_platform_info()
        assert info["connection_mode"] == "memory"

    def test_file_connection_mode(self, tmp_path):
        db_path = str(tmp_path / "test.duckdb")
        adapter = DuckDBAdapter(database_path=db_path)
        info = adapter.get_platform_info()
        assert info["connection_mode"] == "file"


class TestDuckDBExternalViewCreationReal:
    def test_create_view_from_parquet(self, tmp_path):

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE source AS SELECT i AS id, i * 10 AS val FROM range(5) t(i)")
        pq_path = tmp_path / "source.parquet"
        conn.execute(f"COPY source TO '{pq_path}' (FORMAT PARQUET)")

        scan_expr, _, _, _, fmt = _build_duckdb_external_scan_expression(
            conn, [pq_path], delta_extension_loaded=False, vortex_extension_loaded=False
        )
        conn.execute(f"CREATE VIEW ext_source AS SELECT * FROM {scan_expr}")
        rows = conn.execute("SELECT COUNT(*) FROM ext_source").fetchone()
        assert rows[0] == 5
        assert fmt == "parquet"
        conn.close()

    def test_create_view_from_csv(self, tmp_path):

        csv_path = tmp_path / "region.csv"
        csv_path.write_text("0,AFRICA\n1,AMERICA\n2,ASIA\n")

        conn = duckdb.connect(":memory:")
        scan_expr, _, _, _, fmt = _build_duckdb_external_scan_expression(
            conn,
            [csv_path],
            delta_extension_loaded=False,
            vortex_extension_loaded=False,
            column_names=["r_regionkey", "r_name"],
        )
        conn.execute(f"CREATE VIEW region AS SELECT * FROM {scan_expr}")
        rows = conn.execute("SELECT r_name FROM region WHERE r_regionkey = 1").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "AMERICA"
        assert fmt == "csv"
        conn.close()


class TestDuckDBSupportsTuningType:
    def test_sorting_is_supported(self):
        from benchbox.core.tuning.interface import TuningType

        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.supports_tuning_type(TuningType.SORTING) is True

    def test_driver_isolation_capability(self):
        from benchbox.platforms.base import DriverIsolationCapability

        assert DuckDBAdapter.driver_isolation_capability == DriverIsolationCapability.SUPPORTED

    def test_external_tables_supported(self):
        assert DuckDBAdapter.supports_external_tables is True


