# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import polars as pl

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed"),
]


class TestPolarsAdapterBasics:
    def test_initialization_success(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            assert adapter.platform_name == "Polars"
            assert adapter.execution_mode == "lazy"
            assert adapter.get_platform_info()["configuration"]["execution_engine"]["resolution"] == (
                "platform_default"
            )
            assert adapter.rechunk is True

    def test_initialization_custom_config(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(
                working_dir=tmpdir,
                execution_mode="eager",
                n_rows=1000,
                rechunk=False,
            )

            assert adapter.execution_mode == "eager"
            assert adapter.n_rows == 1000
            assert adapter.rechunk is False

    def test_streaming_true_warns_once_per_run_and_keeps_the_default_engine(self, caplog):
        from benchbox.core.deprecation_notices import begin_run
        from benchbox.platforms.polars_platform import PolarsAdapter
        from tests.utilities.deprecation_capture import captured_deprecations

        begin_run()
        with tempfile.TemporaryDirectory() as tmpdir:
            with captured_deprecations(caplog) as seen:
                first = PolarsAdapter(working_dir=tmpdir, streaming=True)
                second = PolarsAdapter(working_dir=tmpdir, streaming=True)

        assert len(seen) == 1
        assert "'streaming'" in seen[0]
        assert "has no effect on the SQL Polars adapter" in seen[0]
        assert "0.6.0" in seen[0]
        for adapter in (first, second):
            receipt = adapter.get_platform_info()["configuration"]["execution_engine"]
            assert receipt["requested"] == "default"
            assert receipt["resolution"] == "platform_default"
            assert receipt["applied"] is None

    def test_streaming_false_is_accepted_silently(self, caplog):
        from benchbox.core.deprecation_notices import begin_run
        from benchbox.platforms.polars_platform import PolarsAdapter
        from tests.utilities.deprecation_capture import captured_deprecations

        begin_run()
        with tempfile.TemporaryDirectory() as tmpdir:
            with captured_deprecations(caplog) as seen:
                PolarsAdapter(working_dir=tmpdir, streaming=False)

        assert seen == []

    def test_sql_adapter_rejects_non_default_engine(self):
        from benchbox.core.execution_engine import UnsupportedExecutionEngineError
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default$"):
                PolarsAdapter(working_dir=tmpdir, execution_engine="duckdb")

    def test_platform_name(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.platform_name == "Polars"

    def test_target_dialect(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.get_target_dialect() == "dataframe"

    def test_external_table_capability_declared(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            assert adapter.supports_external_tables is True

    def test_create_external_tables_delegates_to_load_data(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            benchmark = MagicMock()
            connection = MagicMock()
            expected = ({"lineitem": 100}, 0.3, {"lineitem": {"total_ms": 300.0}})

            with patch.object(adapter, "load_data", return_value=expected) as mock_load_data:
                result = adapter.create_external_tables(benchmark, connection, Path(tmpdir))

            assert result == expected
            mock_load_data.assert_called_once_with(benchmark, connection, Path(tmpdir))


class TestPolarsAdapterFromConfig:
    def test_from_config_basic(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "working_dir": tmpdir,
                "benchmark": "tpch",
                "scale_factor": 1.0,
            }

            adapter = PolarsAdapter.from_config(config)

            assert adapter.platform_name == "Polars"
            assert adapter.execution_mode == "lazy"

    def test_from_config_warns_for_streaming_and_keeps_the_default_engine(self, caplog):
        from benchbox.core.deprecation_notices import begin_run
        from benchbox.platforms.polars_platform import PolarsAdapter
        from tests.utilities.deprecation_capture import captured_deprecations

        begin_run()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"working_dir": tmpdir, "benchmark": "tpch", "scale_factor": 1.0, "streaming": True}
            with captured_deprecations(caplog) as seen:
                adapter = PolarsAdapter.from_config(config)

        assert len(seen) == 1
        assert adapter.requested_execution_engine == "default"

    def test_factory_built_adapter_with_streaming_records_the_default_receipt_in_the_bundle(self, caplog):
        from benchbox.core.deprecation_notices import begin_run
        from benchbox.core.results.models import QueryExecution
        from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
        from benchbox.core.runner.runner import _enrich_normalized_runtime_metadata
        from benchbox.platforms import get_platform_adapter
        from tests.fixtures.result_dict_fixtures import make_benchmark_results
        from tests.utilities.deprecation_capture import captured_deprecations

        begin_run()
        with tempfile.TemporaryDirectory() as tmpdir:
            with captured_deprecations(caplog) as seen:
                adapter = get_platform_adapter("polars", working_dir=tmpdir, streaming=True)
            result = make_benchmark_results(
                platform="polars",
                platform_info=adapter.get_platform_info(),
                query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1)],
            )
            payload = build_result_payload(_enrich_normalized_runtime_metadata(result, adapter=adapter))

        SchemaV2Validator().validate(payload)
        assert len(seen) == 1
        assert payload["platform"]["execution_engine"]["requested"] == "default"
        assert payload["platform"]["execution_engine"]["resolution"] == "platform_default"
        assert payload["platform"]["variant"] == "polars"


class TestPolarsDataFrameContext:
    def test_dataframe_context_creation(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            assert ctx is not None
            assert ctx.get_tables() == []

    def test_register_table(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})

            ctx.register_table("test_table", df)

            assert "test_table" in ctx.get_tables()

    def test_get_table(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame(
                {
                    "id": [1, 2, 3, 4, 5],
                    "value": [10, 20, 30, 40, 50],
                }
            )
            ctx.register_table("numbers", df)

            result = ctx.get_table("numbers")

            assert result is not None
            assert isinstance(result, pl.LazyFrame)

    def test_get_nonexistent_table(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            result = ctx.get_table("nonexistent")

            assert result is None

    def test_unregister_table(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            ctx = PolarsDataFrameContext(adapter)

            df = pl.DataFrame({"id": [1, 2, 3]})
            ctx.register_table("test_table", df)
            assert "test_table" in ctx.get_tables()

            ctx.unregister_table("test_table")
            assert "test_table" not in ctx.get_tables()


class TestPolarsAdapterConnection:
    def test_create_connection(self):
        from benchbox.platforms.polars_platform import PolarsAdapter, PolarsDataFrameContext

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            conn = adapter.create_connection()

            assert isinstance(conn, PolarsDataFrameContext)

    def test_get_platform_info(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            info = adapter.get_platform_info()

            assert info["platform_type"] == "polars"
            assert info["platform_name"] == "Polars"
            assert info["connection_mode"] == "in-memory"
            assert "client_library_version" in info
            assert info["client_library_version"] == pl.__version__
            assert info["configuration"]["execution_engine"]["resolution"] == "platform_default"


class TestPolarsAdapterSchemaCreation:
    def test_create_schema_basic(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            conn = adapter.create_connection()

            mock_benchmark = MagicMock()
            mock_benchmark.get_schema.return_value = {
                "orders": {
                    "name": "orders",
                    "columns": [
                        {"name": "order_id", "type": "INTEGER"},
                        {"name": "customer_id", "type": "INTEGER"},
                        {"name": "order_date", "type": "DATE"},
                    ],
                },
            }

            duration = adapter.create_schema(mock_benchmark, conn)

            assert duration >= 0
            assert "orders" in adapter._table_schemas


class TestPolarsAdapterQueryExecution:
    def test_execute_query_raises_not_implemented(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)
            conn = adapter.create_connection()

            df = pl.DataFrame(
                {
                    "id": [1, 2, 3, 4, 5],
                    "value": [100, 200, 300, 400, 500],
                }
            )
            conn.register_table("test", df)

            with pytest.raises(NotImplementedError) as exc_info:
                adapter.execute_query(
                    connection=conn,
                    query="SELECT * FROM test WHERE value > 200",
                    query_id="Q1",
                    validate_row_count=False,
                )

            assert "SQL mode is not supported" in str(exc_info.value)
            assert "polars-df" in str(exc_info.value)


class TestPolarsAdapterDataLoading:
    def test_detect_csv_format(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            format_type, delim = adapter._detect_file_format([Path("orders.tbl")])
            assert format_type == "csv"
            assert delim == "|"

            format_type, delim = adapter._detect_file_format([Path("data.csv")])
            assert format_type == "csv"
            assert delim == ","

            format_type, delim = adapter._detect_file_format([Path("data.parquet")])
            assert format_type == "parquet"

    def test_load_csv_data(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adapter = PolarsAdapter(working_dir=tmpdir)

            csv_file = tmpdir_path / "test.csv"
            csv_file.write_text("1,Alice,100\n2,Bob,200\n3,Charlie,300\n")

            lf = adapter._load_csv(
                file_paths=[csv_file],
                delimiter=",",
                column_names=["id", "name", "value"],
            )

            df = lf.collect()
            assert len(df) == 3
            assert df.columns == ["id", "name", "value"]


class TestPolarsAdapterValidation:
    def test_validate_platform_capabilities(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            result = adapter.validate_platform_capabilities("tpch")

            assert result.is_valid is True
            assert "polars" in result.details["platform"].lower()
            assert result.details["polars_available"] is True

    def test_validate_platform_capabilities_sql_mode_warning(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            result = adapter.validate_platform_capabilities("tpch")

            assert len(result.warnings) > 0
            assert any("SQL mode is not available" in w for w in result.warnings)
            assert result.details.get("sql_mode") is False

    def test_check_database_exists_empty(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            assert adapter.check_database_exists() is False

    def test_check_database_exists_with_parquet(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adapter = PolarsAdapter(working_dir=tmpdir)

            df = pl.DataFrame({"id": [1, 2, 3]})
            df.write_parquet(tmpdir_path / "test.parquet")

            assert adapter.check_database_exists() is True


class TestPolarsAdapterDatabaseOperations:
    def test_drop_database(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as parent:
            working_dir = Path(parent) / "polars_data"
            working_dir.mkdir()

            (working_dir / "test.parquet").touch()
            (working_dir / "data.csv").touch()

            adapter = PolarsAdapter(working_dir=str(working_dir))
            adapter.drop_database()

            assert not working_dir.exists()


class TestPolarsAdapterCLIArguments:
    def test_add_cli_arguments(self):
        from argparse import ArgumentParser

        from benchbox.platforms.polars_platform import PolarsAdapter

        parser = ArgumentParser()
        PolarsAdapter.add_cli_arguments(parser)

        args = parser.parse_args(
            [
                "--polars-execution-mode",
                "eager",
            ]
        )

        assert args.polars_execution_mode == "eager"
        assert not hasattr(args, "polars_streaming")


class TestPolarsAdapterIntegration:
    def test_full_workflow_data_loading(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir)

            conn = adapter.create_connection()

            df = pl.DataFrame(
                {
                    "l_orderkey": [1, 1, 2, 2, 3],
                    "l_partkey": [100, 101, 102, 103, 104],
                    "l_quantity": [10, 20, 30, 40, 50],
                    "l_extendedprice": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
                }
            )
            conn.register_table("lineitem", df)

            assert "lineitem" in conn.get_tables()

            lf = conn.get_table("lineitem")
            assert lf is not None
            result_df = lf.collect()
            assert len(result_df) == 5

    def test_lazy_execution_mode(self):
        from benchbox.platforms.polars_platform import PolarsAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = PolarsAdapter(working_dir=tmpdir, execution_mode="lazy")
            conn = adapter.create_connection()

            lf = pl.LazyFrame(
                {
                    "id": range(1000),
                    "value": range(1000),
                }
            )
            conn.register_table("large_table", lf)

            table = conn.get_table("large_table")
            assert table is not None

            result = table.select(pl.len().alias("cnt")).collect()
            assert result["cnt"][0] == 1000


class TestPolarsAdapterRegistration:
    def test_adapter_in_platform_mapping(self):
        from benchbox.platforms import get_platform_adapter

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = get_platform_adapter(
                "polars",
                working_dir=tmpdir,
                benchmark="tpch",
                scale_factor=1.0,
            )
            assert adapter.platform_name == "Polars"

    def test_list_available_platforms(self):
        from benchbox.platforms import list_available_platforms

        platforms = list_available_platforms()
        assert "polars" in platforms
        assert platforms["polars"] is True

    def test_get_platform_requirements(self):
        from benchbox.platforms import get_platform_requirements

        req = get_platform_requirements("polars")
        assert "polars" in req.lower()
