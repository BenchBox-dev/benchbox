from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
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
        data_format="parquet",
        memory_limit="256MB",
        working_dir=str(tmp_path / "df_working"),
        batch_size=1024,
    )


@pytest.fixture()
def ctx(adapter):
    conn = adapter.create_connection()
    yield conn
    try:
        del conn
    except Exception:
        pass


def _write_parquet(path: Path, table: pa.Table) -> Path:
    pq.write_table(table, str(path))
    return path


class TestDataFusionConnectionCompat:
    def test_execute_returns_cursor_compat(self, ctx):
        cursor = ctx.execute("SELECT 42 AS answer")
        assert hasattr(cursor, "fetchone")
        assert hasattr(cursor, "fetchall")

    def test_fetchone_returns_tuple(self, ctx):
        cursor = ctx.execute("SELECT 42 AS answer")
        row = cursor.fetchone()
        assert row == (42,)

    def test_fetchall_returns_list_of_tuples(self, ctx):
        cursor = ctx.execute("SELECT 1 AS x UNION ALL SELECT 2")
        rows = cursor.fetchall()
        values = sorted(row[0] for row in rows)
        assert values == [1, 2]

    def test_rowcount_set_after_materialize(self, ctx):
        cursor = ctx.execute("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
        assert cursor.rowcount == -1
        cursor.fetchall()
        assert cursor.rowcount == 3

    def test_sql_method_available(self, ctx):
        df = ctx.sql("SELECT 100 AS val")
        batches = df.collect()
        assert len(batches) > 0


class TestRequiresEagerExecution:
    def test_select_not_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("SELECT 1") is False

    def test_create_table_is_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("CREATE TABLE t (id INT)") is True

    def test_insert_is_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("INSERT INTO t VALUES (1)") is True

    def test_drop_is_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("DROP TABLE t") is True

    def test_copy_is_eager(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("COPY t TO 'file.parquet'") is True

    def test_comment_lines_before_select(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        query = "-- This is a comment\n-- Another comment\nSELECT 1"
        assert DataFusionConnectionCompat._requires_eager_execution(query) is False

    def test_comment_lines_before_create(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        query = "-- comment\nCREATE TABLE t (id INT)"
        assert DataFusionConnectionCompat._requires_eager_execution(query) is True

    def test_single_line_comment_only(self):
        from benchbox.platforms.datafusion import DataFusionConnectionCompat

        assert DataFusionConnectionCompat._requires_eager_execution("-- just a comment") is False


class TestSessionCreation:
    def test_connection_has_information_schema(self, ctx):
        cursor = ctx.execute("SELECT * FROM information_schema.tables LIMIT 1")
        rows = cursor.fetchall()
        assert isinstance(rows, list)

    def test_adapter_platform_name(self, adapter):
        assert adapter.platform_name == "DataFusion"

    def test_adapter_target_dialect(self, adapter):
        assert adapter.get_target_dialect() == "datafusion"

    def test_platform_info_contains_version(self, adapter, ctx):
        info = adapter.get_platform_info(ctx)
        assert "platform_version" in info
        assert info["platform_version"] is not None


class TestParquetTableRegistration:
    def test_register_parquet_and_query(self, ctx, tmp_path):
        table = pa.table({"product_id": [1, 2, 3], "price": [9.99, 19.99, 29.99]})
        parquet_path = _write_parquet(tmp_path / "products.parquet", table)

        ctx.register_parquet("products", str(parquet_path))
        cursor = ctx.execute("SELECT COUNT(*) AS cnt FROM products")
        row = cursor.fetchone()
        assert row == (3,)

    def test_register_parquet_preserves_schema(self, ctx, tmp_path):
        table = pa.table(
            {
                "order_id": pa.array([100, 200], type=pa.int64()),
                "amount": pa.array([1.5, 2.5], type=pa.float64()),
            }
        )
        parquet_path = _write_parquet(tmp_path / "orders.parquet", table)
        ctx.register_parquet("orders", str(parquet_path))

        cursor = ctx.execute("SELECT order_id, amount FROM orders ORDER BY order_id")
        rows = cursor.fetchall()
        assert rows == [(100, 1.5), (200, 2.5)]


class TestSqlExecution:
    def test_execute_aggregation(self, adapter, ctx, tmp_path):
        table = pa.table({"dept": ["eng", "eng", "sales", "sales"], "salary": [100, 150, 90, 110]})
        parquet_path = _write_parquet(tmp_path / "employees.parquet", table)
        ctx.register_parquet("employees", str(parquet_path))

        result = adapter.execute_query(
            connection=ctx,
            query="SELECT dept, SUM(salary) AS total FROM employees GROUP BY dept ORDER BY dept",
            query_id="agg_test",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 2
        assert result.get("error") is None

    def test_execute_join(self, adapter, ctx, tmp_path):
        regions = pa.table({"region_id": [1, 2], "region_name": ["east", "west"]})
        sales = pa.table({"region_id": [1, 1, 2], "amount": [10, 20, 30]})

        _write_parquet(tmp_path / "regions.parquet", regions)
        _write_parquet(tmp_path / "sales.parquet", sales)
        ctx.register_parquet("regions", str(tmp_path / "regions.parquet"))
        ctx.register_parquet("sales", str(tmp_path / "sales.parquet"))

        result = adapter.execute_query(
            connection=ctx,
            query="SELECT r.region_name, SUM(s.amount) AS total FROM regions r JOIN sales s ON r.region_id = s.region_id GROUP BY r.region_name ORDER BY r.region_name",
            query_id="join_test",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 2
        assert result.get("error") is None

    def test_execute_bad_sql_returns_error(self, adapter, ctx):
        result = adapter.execute_query(
            connection=ctx,
            query="SELECTX INVALID SYNTAX",
            query_id="bad_sql",
            validate_row_count=False,
        )
        assert result["error"] is not None

    def test_execute_empty_result(self, adapter, ctx, tmp_path):
        table = pa.table({"val": [1, 2, 3]})
        _write_parquet(tmp_path / "nums.parquet", table)
        ctx.register_parquet("nums", str(tmp_path / "nums.parquet"))

        result = adapter.execute_query(
            connection=ctx,
            query="SELECT * FROM nums WHERE val > 100",
            query_id="empty_q",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 0
        assert result.get("error") is None


class TestParseMemoryLimit:
    def setup_method(self):
        from benchbox.platforms.datafusion import DataFusionAdapter

        self.adapter = DataFusionAdapter(memory_limit="256MB", data_format="parquet")

    def test_gigabytes_short(self):
        assert self.adapter._parse_memory_limit("2G") == str(2 * 1024 * 1024 * 1024)

    def test_gigabytes_long(self):
        assert self.adapter._parse_memory_limit("2GB") == str(2 * 1024 * 1024 * 1024)

    def test_megabytes(self):
        assert self.adapter._parse_memory_limit("512MB") == str(512 * 1024 * 1024)

    def test_megabytes_short(self):
        assert self.adapter._parse_memory_limit("512M") == str(512 * 1024 * 1024)

    def test_kilobytes(self):
        assert self.adapter._parse_memory_limit("1024KB") == str(1024 * 1024)

    def test_plain_bytes(self):
        assert self.adapter._parse_memory_limit("1048576") == "1048576"


class TestFromConfig:
    def test_from_config_creates_adapter(self, tmp_path):
        from benchbox.platforms.datafusion import DataFusionAdapter

        adapter = DataFusionAdapter.from_config(
            {
                "benchmark": "tpch",
                "scale_factor": 0.01,
                "output_dir": str(tmp_path),
                "memory_limit": "128MB",
                "format": "parquet",
                "batch_size": 2048,
            }
        )
        conn = adapter.create_connection()
        cursor = conn.execute("SELECT 1 + 1 AS result")
        assert cursor.fetchone() == (2,)

    def test_from_config_default_format(self, tmp_path):
        from benchbox.platforms.datafusion import DataFusionAdapter

        adapter = DataFusionAdapter.from_config(
            {
                "benchmark": "ssb",
                "scale_factor": 0.01,
                "output_dir": str(tmp_path),
            }
        )
        assert adapter.data_format == "parquet"


class TestExternalTableSupport:
    def test_supports_external_tables_flag(self, adapter):
        assert adapter.supports_external_tables is True

    def test_create_external_tables_aliases_load_data(self, adapter):
        assert hasattr(adapter, "create_external_tables")
        assert callable(adapter.create_external_tables)


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def sql(self, statement: str) -> _FakeResult:
        self.statements.append(statement)
        return _FakeResult()


class _FakeResult:
    def collect(self) -> list[_FakeBatch]:
        return [_FakeBatch()]


class _FakeBatch:
    def column(self, _index: int) -> list[int]:
        return [7]


class TestMultiFileCsvEmptyPrefixGuard:
    def test_empty_common_prefix_registers_per_shard_via_union(self, adapter, tmp_path):
        shard_dir = tmp_path / "csvshards"
        shard_dir.mkdir()
        shard_a = shard_dir / "abc-data.csv"
        shard_b = shard_dir / "def-data.csv"
        shard_c = shard_dir / "999-data.csv"
        for shard in (shard_a, shard_b, shard_c):
            shard.write_text("col1,col2\n1,2\n", encoding="utf-8")

        conn = _FakeConnection()
        adapter._create_external_table_union(
            conn,
            "tbl",
            [shard_a, shard_b, shard_c],
            schema_clause="(col1 INT, col2 INT)",
            delimiter=",",
            has_header=True,
        )

        joined = "\n".join(conn.statements)
        assert f"{shard_dir}/*" not in joined
        assert "/*'" not in joined
        assert str(shard_a) in joined
        assert str(shard_b) in joined
        assert str(shard_c) in joined
        assert "CREATE EXTERNAL TABLE tbl__shard_0" in joined
        assert "CREATE EXTERNAL TABLE tbl__shard_1" in joined
        assert "CREATE EXTERNAL TABLE tbl__shard_2" in joined
        assert "CREATE OR REPLACE VIEW tbl AS" in joined
        assert "UNION ALL" in joined

    def test_load_table_csv_routes_empty_prefix_shards_through_union(self, adapter, tmp_path):
        shard_dir = tmp_path / "csvshards_routing"
        shard_dir.mkdir()
        shards = [
            shard_dir / "abc-data.csv",
            shard_dir / "def-data.csv",
            shard_dir / "999-data.csv",
        ]
        for shard in shards:
            shard.write_text("col1,col2\n1,2\n", encoding="utf-8")

        conn = _FakeConnection()
        adapter._table_schemas["tbl"] = {
            "columns": [
                {"name": "col1", "type": "INT"},
                {"name": "col2", "type": "INT"},
            ]
        }

        adapter._load_table_csv(
            conn,
            "tbl",
            shards,
            data_dir=shard_dir,
        )

        joined = "\n".join(conn.statements)
        assert f"{shard_dir}/*" not in joined, (
            "regression: _load_table_csv re-introduced a parent_dir/* glob for "
            "empty-common-prefix shards. This is the exact bug w9 fixed."
        )
        assert "CREATE OR REPLACE VIEW tbl AS" in joined
        assert "UNION ALL" in joined
        for shard in shards:
            assert str(shard) in joined

    def test_load_table_csv_keeps_glob_when_common_prefix_exists(self, adapter, tmp_path):
        shard_dir = tmp_path / "csvshards_prefix"
        shard_dir.mkdir()
        shards = [
            shard_dir / "lineitem.csv.001",
            shard_dir / "lineitem.csv.002",
            shard_dir / "lineitem.csv.003",
        ]
        for shard in shards:
            shard.write_text("col1,col2\n1,2\n", encoding="utf-8")

        conn = _FakeConnection()
        adapter._table_schemas["lineitem"] = {
            "columns": [
                {"name": "col1", "type": "INT"},
                {"name": "col2", "type": "INT"},
            ]
        }

        adapter._load_table_csv(
            conn,
            "lineitem",
            shards,
            data_dir=shard_dir,
        )

        joined = "\n".join(conn.statements)
        assert "lineitem.csv." in joined and "*" in joined
        assert "UNION ALL" not in joined
        assert "CREATE OR REPLACE VIEW lineitem AS" not in joined


