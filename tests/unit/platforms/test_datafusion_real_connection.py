from __future__ import annotations

import pytest

try:
    import datafusion

    HAS_DATAFUSION = True
except ImportError:
    HAS_DATAFUSION = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed"),
]


@pytest.fixture()
def adapter(tmp_path):
    from benchbox.platforms.datafusion import DataFusionAdapter

    return DataFusionAdapter(
        data_format="csv",
        memory_limit="256MB",
        output_dir=str(tmp_path),
    )


@pytest.fixture()
def ctx(adapter):
    conn = adapter.create_connection()
    yield conn
    try:
        del conn
    except Exception:
        pass


class TestDataFusionRealConnection:
    def test_create_connection_returns_usable_context(self, ctx):
        assert hasattr(ctx, "sql") or hasattr(ctx, "execute")

    def test_execute_simple_query(self, adapter, ctx):
        result = adapter.execute_query(
            connection=ctx,
            query="SELECT 1 AS x",
            query_id="test_q1",
            validate_row_count=False,
        )
        assert result["query_id"] == "test_q1"
        assert result["execution_time_seconds"] >= 0
        assert result["rows_returned"] == 1
        assert result.get("error") is None

    def test_execute_query_with_csv_data(self, adapter, ctx, tmp_path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,name,value\n1,alpha,10\n2,beta,20\n3,gamma,30\n")

        ctx.register_csv("test_data", str(csv_path))

        result = adapter.execute_query(
            connection=ctx,
            query="SELECT * FROM test_data WHERE value > 15",
            query_id="csv_q",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 2
        assert result.get("error") is None

    def test_execute_query_with_bad_sql_returns_error(self, adapter, ctx):
        result = adapter.execute_query(
            connection=ctx,
            query="SELECTX GARBAGE",
            query_id="bad_q",
            validate_row_count=False,
        )
        assert result["error"] is not None

    def test_platform_info_returns_version(self, adapter, ctx):
        info = adapter.get_platform_info(ctx)
        assert "platform_version" in info
        assert info["engine_version"] == info["platform_version"]
        assert info["engine_version_source"] == "sql_query"

    def test_execute_aggregation_query(self, adapter, ctx, tmp_path):
        csv_path = tmp_path / "sales.csv"
        csv_path.write_text("region,amount\neast,100\nwest,200\neast,150\nwest,50\n")
        ctx.register_csv("sales", str(csv_path))

        result = adapter.execute_query(
            connection=ctx,
            query="SELECT region, SUM(amount) as total FROM sales GROUP BY region ORDER BY region",
            query_id="agg_q",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 2
        assert result.get("error") is None

    def test_configure_for_benchmark_is_no_op(self, adapter, ctx):
        adapter.configure_for_benchmark(ctx, "olap")
        adapter.configure_for_benchmark(ctx, "tpch")


class TestDataFusionPureFunctions:
    @pytest.fixture()
    def adapter(self, tmp_path):
        from benchbox.platforms.datafusion import DataFusionAdapter

        return DataFusionAdapter(output_dir=str(tmp_path))

    def test_parse_memory_limit_gb(self, adapter):
        result = adapter._parse_memory_limit("16G")
        assert result == str(16 * 1024 * 1024 * 1024)

    def test_parse_memory_limit_gb_with_b_suffix(self, adapter):
        result = adapter._parse_memory_limit("8GB")
        assert result == str(8 * 1024 * 1024 * 1024)

    def test_parse_memory_limit_mb(self, adapter):
        result = adapter._parse_memory_limit("256MB")
        assert result == str(256 * 1024 * 1024)

    def test_parse_memory_limit_kb(self, adapter):
        result = adapter._parse_memory_limit("512KB")
        assert result == str(512 * 1024)

    def test_parse_memory_limit_bytes_passthrough(self, adapter):
        result = adapter._parse_memory_limit("1073741824")
        assert result == "1073741824"

    def test_map_to_arrow_type_integer(self, adapter):
        assert adapter._map_to_arrow_type("INTEGER") == "INT"

    def test_map_to_arrow_type_bigint(self, adapter):
        assert adapter._map_to_arrow_type("BIGINT") == "BIGINT"

    def test_map_to_arrow_type_varchar(self, adapter):
        assert adapter._map_to_arrow_type("VARCHAR") == "VARCHAR"

    def test_map_to_arrow_type_decimal_with_params(self, adapter):
        result = adapter._map_to_arrow_type("DECIMAL(15,2)")
        assert result == "DECIMAL(15,2)"

    def test_map_to_arrow_type_unknown_passthrough(self, adapter):
        assert adapter._map_to_arrow_type("GEOMETRY") == "GEOMETRY"

    def test_is_parquet_file_true(self, adapter, tmp_path):
        f = tmp_path / "orders.parquet"
        f.touch()
        assert adapter._is_parquet_file(f) is True

    def test_is_parquet_file_with_zst_suffix(self, adapter, tmp_path):
        f = tmp_path / "orders.parquet.zst"
        f.touch()
        assert adapter._is_parquet_file(f) is True

    def test_is_parquet_file_false_for_csv(self, adapter, tmp_path):
        f = tmp_path / "orders.csv"
        f.touch()
        assert adapter._is_parquet_file(f) is False

    def test_detect_directory_format_returns_none_for_files(self, adapter, tmp_path):
        f = tmp_path / "data.csv"
        f.touch()
        assert adapter._detect_directory_format([f]) is None

    def test_detect_directory_format_returns_delta(self, adapter, tmp_path):
        delta_dir = tmp_path / "table"
        delta_dir.mkdir()
        (delta_dir / "_delta_log").mkdir()
        assert adapter._detect_directory_format([delta_dir]) == "delta"

    def test_detect_directory_format_returns_iceberg(self, adapter, tmp_path):
        iceberg_dir = tmp_path / "table"
        iceberg_dir.mkdir()
        (iceberg_dir / "metadata").mkdir()
        assert adapter._detect_directory_format([iceberg_dir]) == "iceberg"

    def test_detect_directory_format_returns_none_for_multiple_paths(self, adapter, tmp_path):
        f1 = tmp_path / "a.csv"
        f2 = tmp_path / "b.csv"
        f1.touch()
        f2.touch()
        assert adapter._detect_directory_format([f1, f2]) is None

    def test_build_ctas_sort_sql_generates_correct_sql(self, adapter):
        from unittest.mock import Mock

        col1 = Mock()
        col1.name = "l_shipdate"
        col2 = Mock()
        col2.name = "l_orderkey"

        result = adapter._build_ctas_sort_sql("lineitem", [col1, col2])

        assert result is not None
        assert "CREATE OR REPLACE TABLE lineitem" in result
        assert "ORDER BY l_shipdate, l_orderkey" in result

    def test_detect_csv_format_from_tbl_file(self, adapter, tmp_path):
        f = tmp_path / "orders.tbl"
        f.touch()
        delimiter = adapter._detect_csv_format([f])
        assert delimiter == "|"

    def test_detect_csv_format_empty_list(self, adapter):
        delimiter = adapter._detect_csv_format([])
        assert delimiter == ","


class TestDataFusionCursorCompat:
    def test_insert_requires_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("INSERT INTO t VALUES (1)") is True

    def test_create_requires_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("CREATE TABLE t (id INT)") is True

    def test_select_does_not_require_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("SELECT * FROM t") is False

    def test_comment_prefix_stripped(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        sql = "-- this is a comment\nDROP TABLE t"
        assert DataFusionConnectionCompat._requires_eager_execution(sql) is True
