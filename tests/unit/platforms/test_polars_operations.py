from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestPolarsDataFrameContextLifecycle:
    def test_register_eager_dataframe_converts_to_lazyframe(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
            ctx.register_table("test", df)

            result = ctx.get_table("test")
            assert isinstance(result, pl.LazyFrame)

    def test_register_lazyframe_stores_directly(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            lf = pl.LazyFrame({"x": [10, 20, 30]})
            ctx.register_table("numbers", lf)

            result = ctx.get_table("numbers")
            assert isinstance(result, pl.LazyFrame)
            collected = result.collect()
            assert collected["x"].to_list() == [10, 20, 30]

    def test_register_overwrites_existing_table(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            ctx.register_table("t", pl.DataFrame({"v": [1]}))
            ctx.register_table("t", pl.DataFrame({"v": [99]}))

            result = ctx.get_table("t").collect()
            assert result["v"][0] == 99

    def test_get_tables_returns_all_registered(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            ctx.register_table("region", pl.DataFrame({"id": [1]}))
            ctx.register_table("nation", pl.DataFrame({"id": [2]}))
            ctx.register_table("customer", pl.DataFrame({"id": [3]}))

            tables = ctx.get_tables()
            assert set(tables) == {"region", "nation", "customer"}

    def test_unregister_nonexistent_table_is_noop(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            ctx.unregister_table("ghost")

    def test_context_stores_adapter_reference(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)
            assert ctx._adapter is adapter


class TestPolarsResultConversion:
    def test_collect_to_dicts(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame(
                {
                    "id": [1, 2, 3],
                    "name": ["Alice", "Bob", "Charlie"],
                    "score": [90.5, 85.0, 92.3],
                }
            )
            ctx.register_table("students", df)

            lf = ctx.get_table("students")
            rows = lf.collect().to_dicts()

            assert len(rows) == 3
            assert rows[0]["name"] == "Alice"
            assert rows[2]["score"] == 92.3

    def test_collect_filtered_lazyframe(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            lf = pl.LazyFrame({"val": list(range(100))})
            ctx.register_table("big", lf)

            table = ctx.get_table("big")
            filtered = table.filter(pl.col("val") >= 95).collect()
            assert len(filtered) == 5
            assert filtered["val"].to_list() == [95, 96, 97, 98, 99]

    def test_aggregation_on_registered_table(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame(
                {
                    "category": ["A", "B", "A", "B", "A"],
                    "amount": [10, 20, 30, 40, 50],
                }
            )
            ctx.register_table("sales", df)

            lf = ctx.get_table("sales")
            result = lf.group_by("category").agg(pl.col("amount").sum()).sort("category").collect()

            assert len(result) == 2
            assert result["category"].to_list() == ["A", "B"]
            assert result["amount"].to_list() == [90, 60]

    def test_collect_first_row(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            ctx.register_table("t", pl.DataFrame({"x": [42, 99]}))
            lf = ctx.get_table("t")
            first = lf.head(1).collect().row(0)
            assert first == (42,)


class TestPolarsExternalTableSupport:
    def test_scan_parquet_as_external_table(self, tmp_path):

        df = pl.DataFrame({"id": [1, 2, 3], "name": ["x", "y", "z"]})
        pq_path = tmp_path / "test.parquet"
        df.write_parquet(pq_path)

        lf = pl.scan_parquet(pq_path)

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        ctx = PolarsDataFrameContext(adapter)
        ctx.register_table("test", lf)

        result = ctx.get_table("test").collect()
        assert len(result) == 3
        assert result.columns == ["id", "name"]

    def test_scan_csv_as_external_table(self, tmp_path):

        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,name,value\n1,a,10\n2,b,20\n3,c,30\n")

        lf = pl.scan_csv(csv_path)

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        ctx = PolarsDataFrameContext(adapter)
        ctx.register_table("data", lf)

        result = ctx.get_table("data").collect()
        assert len(result) == 3
        assert set(result.columns) == {"id", "name", "value"}

    def test_scan_tbl_pipe_delimited(self, tmp_path):

        tbl_path = tmp_path / "region.tbl"
        tbl_path.write_text("0|AFRICA\n1|AMERICA\n2|ASIA\n")

        lf = pl.scan_csv(
            tbl_path,
            separator="|",
            has_header=False,
            new_columns=["r_regionkey", "r_name"],
        )

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        ctx = PolarsDataFrameContext(adapter)
        ctx.register_table("region", lf)

        result = ctx.get_table("region").collect()
        assert len(result) == 3
        assert result["r_name"].to_list() == ["AFRICA", "AMERICA", "ASIA"]

    def test_create_external_tables_delegates_to_load_data(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.supports_external_tables is True

            assert callable(getattr(adapter, "create_external_tables", None))

    def test_multiple_parquet_files_concat(self, tmp_path):

        for i in range(3):
            df = pl.DataFrame({"id": [i * 10 + j for j in range(5)]})
            df.write_parquet(tmp_path / f"part_{i}.parquet")

        lf = pl.scan_parquet(str(tmp_path / "*.parquet"))

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        ctx = PolarsDataFrameContext(adapter)
        ctx.register_table("multi", lf)

        result = ctx.get_table("multi").collect()
        assert len(result) == 15


class TestPolarsAdapterInitialization:
    def test_working_dir_created_if_missing(self, tmp_path):

        new_dir = tmp_path / "deep" / "nested" / "polars_work"
        assert not new_dir.exists()

        adapter = PolarsAdapter(working_dir=str(new_dir))
        assert new_dir.exists()
        assert adapter.working_dir == new_dir

    def test_default_config_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.execution_mode == "lazy"
            assert adapter.n_rows is None
            assert adapter.rechunk is True

    def test_custom_config_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(
                working_dir=tmpdir,
                execution_mode="eager",
                n_rows=500,
                rechunk=False,
            )
            assert adapter.execution_mode == "eager"
            assert adapter.n_rows == 500
            assert adapter.rechunk is False


class TestPolarsAdapterConnectionAndInfo:
    def test_create_connection_returns_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()
            assert isinstance(ctx, PolarsDataFrameContext)
            assert ctx.get_tables() == []

    def test_platform_info_includes_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            info = adapter.get_platform_info()

            assert info["platform_type"] == "polars"
            assert info["platform_name"] == "Polars"
            assert info["connection_mode"] == "in-memory"
            assert info["client_library_version"] == pl.__version__
            assert info["platform_version"] == pl.__version__

    def test_platform_info_configuration_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir, execution_mode="eager")
            info = adapter.get_platform_info()
            config = info["configuration"]
            assert config["execution_mode"] == "eager"
            assert config["execution_engine"]["resolution"] == "platform_default"
            assert config["result_cache_enabled"] is False


class TestPolarsSchemaCreation:
    def test_schema_extracted_from_benchmark(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            benchmark = MagicMock()
            benchmark.get_schema.return_value = {
                "lineitem": {
                    "columns": [
                        {"name": "l_orderkey", "type": "INTEGER"},
                        {"name": "l_partkey", "type": "INTEGER"},
                        {"name": "l_quantity", "type": "DECIMAL"},
                    ],
                },
                "orders": {
                    "columns": [
                        {"name": "o_orderkey", "type": "INTEGER"},
                        {"name": "o_custkey", "type": "INTEGER"},
                    ],
                },
            }

            duration = adapter.create_schema(benchmark, ctx)
            assert duration >= 0
            assert "lineitem" in adapter._table_schemas
            assert "orders" in adapter._table_schemas
            assert len(adapter._table_schemas["lineitem"]["columns"]) == 3

    def test_schema_empty_when_benchmark_has_no_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            benchmark = MagicMock()
            benchmark.get_schema.return_value = {}

            duration = adapter.create_schema(benchmark, ctx)
            assert duration >= 0
            assert adapter._table_schemas == {}

    def test_schema_handles_missing_get_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            benchmark = MagicMock(spec=[])

            duration = adapter.create_schema(benchmark, ctx)
            assert duration >= 0
            assert adapter._table_schemas == {}


class TestPolarsDataLoadingReal:
    def test_load_parquet_single_file(self, tmp_path):

        df = pl.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})
        pq = tmp_path / "test.parquet"
        df.write_parquet(pq)

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        lf = adapter._load_parquet([pq])
        result = lf.collect()
        assert len(result) == 3
        assert result.columns == ["id", "val"]

    def test_load_csv_single_file(self, tmp_path):

        csv = tmp_path / "data.csv"
        csv.write_text("1,Alice,100\n2,Bob,200\n")

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        lf = adapter._load_csv(
            file_paths=[csv],
            delimiter=",",
            column_names=["id", "name", "value"],
        )
        result = lf.collect()
        assert len(result) == 2
        assert result.columns == ["id", "name", "value"]

    def test_load_csv_pipe_delimited(self, tmp_path):

        tbl = tmp_path / "region.tbl"
        tbl.write_text("0|AFRICA\n1|AMERICA\n")

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        lf = adapter._load_csv(
            file_paths=[tbl],
            delimiter="|",
            column_names=["r_regionkey", "r_name"],
        )
        result = lf.collect()
        assert len(result) == 2
        assert result["r_name"].to_list() == ["AFRICA", "AMERICA"]

    def test_detect_file_format_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            fmt, delim = adapter._detect_file_format([Path("data.csv")])
            assert fmt == "csv"
            assert delim == ","

    def test_detect_file_format_tbl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            fmt, delim = adapter._detect_file_format([Path("lineitem.tbl")])
            assert fmt == "csv"
            assert delim == "|"

    def test_detect_file_format_parquet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            fmt, _delim = adapter._detect_file_format([Path("data.parquet")])
            assert fmt == "parquet"


class TestPolarsSQLNotSupported:
    def test_execute_query_raises_not_implemented(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            with pytest.raises(NotImplementedError, match="SQL mode is not supported"):
                adapter.execute_query(
                    connection=ctx,
                    query="SELECT 1",
                    query_id="q1",
                    validate_row_count=False,
                )

    def test_error_message_mentions_polars_df(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            with pytest.raises(NotImplementedError, match="polars-df"):
                adapter.execute_query(connection=ctx, query="SELECT 1", query_id="q1")


class TestPolarsValidationAndDatabaseOps:
    def test_get_existing_tables_from_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            ctx.register_table("A", pl.DataFrame({"x": [1]}))
            ctx.register_table("B", pl.DataFrame({"x": [2]}))

            tables = adapter._get_existing_tables(ctx)
            assert set(tables) == {"a", "b"}

    def test_get_existing_tables_empty_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()
            tables = adapter._get_existing_tables(ctx)
            assert tables == []

    def test_validate_data_integrity_all_accessible(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            ctx.register_table("t1", pl.DataFrame({"id": [1]}))
            ctx.register_table("t2", pl.DataFrame({"id": [2]}))

            benchmark = MagicMock()
            status, details = adapter._validate_data_integrity(benchmark, ctx, {"t1": 1, "t2": 1})
            assert status == "PASSED"
            assert set(details["accessible_tables"]) == {"t1", "t2"}

    def test_validate_data_integrity_inaccessible_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = adapter.create_connection()

            ctx.register_table("t1", pl.DataFrame({"id": [1]}))

            benchmark = MagicMock()
            status, details = adapter._validate_data_integrity(benchmark, ctx, {"t1": 1, "t2": 1})
            assert status == "FAILED"
            assert "t2" in details["inaccessible_tables"]

    def test_check_database_exists_no_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.check_database_exists() is False

    def test_check_database_exists_with_parquet(self, tmp_path):
        pq = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(pq)

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        assert adapter.check_database_exists() is True

    def test_check_database_exists_with_csv(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("1,2,3\n")

        adapter = PolarsAdapter(working_dir=str(tmp_path))
        assert adapter.check_database_exists() is True

    def test_drop_database_removes_directory(self, tmp_path):
        work_dir = tmp_path / "polars_data"
        work_dir.mkdir()
        (work_dir / "test.parquet").touch()

        adapter = PolarsAdapter(working_dir=str(work_dir))
        adapter.drop_database()
        assert not work_dir.exists()

    def test_driver_isolation_not_applicable(self):
        from benchbox.platforms.base import DriverIsolationCapability

        assert PolarsAdapter.driver_isolation_capability == DriverIsolationCapability.NOT_APPLICABLE

    def test_target_dialect_is_dataframe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.get_target_dialect() == "dataframe"


