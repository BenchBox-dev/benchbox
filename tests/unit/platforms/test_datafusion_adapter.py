# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.datafusion import DataFusionAdapter, DataFusionConnectionCompat

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFusionAdapter:
    def test_initialization_success(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(
                working_dir=tmpdir,
                memory_limit="8G",
                target_partitions=4,
                data_format="parquet",
                batch_size=16384,
            )
            assert adapter.platform_name == "DataFusion"
            assert adapter.get_target_dialect() == "datafusion"
            assert str(adapter.working_dir) == tmpdir
            assert adapter.memory_limit == "8G"
            assert adapter.target_partitions == 4
            assert adapter.data_format == "parquet"
            assert adapter.batch_size == 16384

    def test_initialization_with_defaults(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            assert adapter.working_dir == Path(tmpdir)
            assert adapter.memory_limit == "16G"
            assert adapter.target_partitions > 0
            assert adapter.data_format == "parquet"
            assert adapter.batch_size == 8192

    def test_external_table_capability_declared(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            assert adapter.supports_external_tables is True

    def test_create_external_tables_delegates_to_load_data(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            benchmark = Mock()
            connection = Mock()
            expected = ({"customer": 42}, 0.12, {"customer": {"total_ms": 120.0}})

            with patch.object(adapter, "load_data", return_value=expected) as mock_load_data:
                result = adapter.create_external_tables(benchmark, connection, Path(tmpdir))

            assert result == expected
            mock_load_data.assert_called_once_with(benchmark, connection, Path(tmpdir))

    def test_schema_only_materialization_retains_empty_table_creation(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            benchmark = Mock()
            connection = Mock()

            with patch.object(adapter, "_create_empty_schema_tables", return_value={}) as mock_create_empty:
                result = adapter.materialize_schema_only_tables(benchmark, connection)

            assert result == {}
            mock_create_empty.assert_called_once_with(connection)

    def test_initialization_missing_driver(self):
        with patch("benchbox.platforms.datafusion.SessionContext", None):
            with pytest.raises(ImportError, match="DataFusion not installed"):
                DataFusionAdapter()

    def test_get_platform_info(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, memory_limit="8G")

            mock_df_module = Mock()
            mock_df_module.__version__ = "50.1.0"

            with patch.dict(sys.modules, {"datafusion": mock_df_module}):
                info = adapter.get_platform_info()

            assert info["platform_type"] == "datafusion"
            assert info["platform_name"] == "DataFusion"
            assert info["connection_mode"] == "in-memory"
            assert info["configuration"]["memory_limit"] == "8G"
            assert info["client_library_version"] == "50.1.0"

    def test_create_connection(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_config = Mock()
            mock_config.with_target_partitions.return_value = mock_config
            mock_config.with_parquet_pruning.return_value = mock_config
            mock_config.with_repartition_joins.return_value = mock_config
            mock_config.with_repartition_aggregations.return_value = mock_config
            mock_config.with_repartition_windows.return_value = mock_config
            mock_config.with_information_schema.return_value = mock_config
            mock_config.with_batch_size.return_value = mock_config
            mock_config.set.side_effect = Exception("Config not supported")

            mock_ctx = Mock()

            mock_runtime_builder = Mock()
            mock_runtime_builder.build.return_value = Mock()

            with patch("benchbox.platforms.datafusion.SessionConfig", return_value=mock_config):
                with patch("benchbox.platforms.datafusion.SessionContext", return_value=mock_ctx):
                    with patch("benchbox.platforms.datafusion.RuntimeEnv", return_value=mock_runtime_builder):
                        adapter = DataFusionAdapter(
                            working_dir=tmpdir,
                            memory_limit="8G",
                            target_partitions=4,
                            batch_size=16384,
                        )

                        with patch.object(adapter, "handle_existing_database"):
                            connection = adapter.create_connection()

                        assert isinstance(connection, DataFusionConnectionCompat)
                        assert connection._context == mock_ctx

                        mock_config.with_target_partitions.assert_called_once_with(4)
                        mock_config.with_parquet_pruning.assert_called_once_with(True)
                        mock_config.with_repartition_joins.assert_called_once_with(True)
                        mock_config.with_batch_size.assert_called_once_with(16384)

    def test_create_connection_honors_optimization_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_config = Mock()
            mock_config.with_target_partitions.return_value = mock_config
            mock_config.with_parquet_pruning.return_value = mock_config
            mock_config.with_repartition_joins.return_value = mock_config
            mock_config.with_repartition_aggregations.return_value = mock_config
            mock_config.with_repartition_windows.return_value = mock_config
            mock_config.with_information_schema.return_value = mock_config
            mock_config.with_batch_size.return_value = mock_config
            mock_config.set.side_effect = Exception("Config not supported")

            mock_runtime_builder = Mock()
            mock_runtime_builder.build.return_value = Mock()

            with patch("benchbox.platforms.datafusion.SessionConfig", return_value=mock_config):
                with patch("benchbox.platforms.datafusion.SessionContext"):
                    with patch("benchbox.platforms.datafusion.RuntimeEnv", return_value=mock_runtime_builder):
                        adapter = DataFusionAdapter(
                            working_dir=tmpdir,
                            parquet_pushdown=False,
                            repartition_joins=False,
                        )

                        with patch.object(adapter, "handle_existing_database"):
                            adapter.create_connection()

            mock_config.with_parquet_pruning.assert_called_once_with(False)
            mock_config.with_repartition_joins.assert_called_once_with(False)

    def test_create_connection_configures_runtime_builder_without_build_method(self):

        class RuntimeEnvBuilderV53:
            def __init__(self):
                self.memory_bytes = None
                self.disk_manager_enabled = False

            def with_fair_spill_pool(self, size):
                self.memory_bytes = size
                return self

            def with_disk_manager_os(self):
                self.disk_manager_enabled = True
                return self

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("benchbox.platforms.datafusion.RuntimeEnv", RuntimeEnvBuilderV53):
                with patch("benchbox.platforms.datafusion.SessionContext") as session_context:
                    adapter = DataFusionAdapter(working_dir=tmpdir, memory_limit="8G")

                    with patch.object(adapter, "handle_existing_database"):
                        adapter.create_connection()

            runtime = session_context.call_args.args[1]
            assert runtime.memory_bytes == 8 * 1024 * 1024 * 1024
            assert runtime.disk_manager_enabled is True

    def test_create_connection_does_not_report_unavailable_runtime_settings(self):

        class LegacyRuntimeEnv:
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("benchbox.platforms.datafusion.RuntimeEnv", LegacyRuntimeEnv):
                with patch("benchbox.platforms.datafusion.SessionContext"):
                    adapter = DataFusionAdapter(working_dir=tmpdir, memory_limit="8G")

                    with patch.object(adapter, "handle_existing_database"):
                        with patch.object(adapter, "log_operation_complete") as operation_complete:
                            adapter.create_connection()

            details = operation_complete.call_args.kwargs["details"]
            assert "memory_pool=" not in details
            assert "disk_spilling=" not in details

    def test_create_connection_does_not_report_runtime_rejected_by_context(self):

        class RuntimeEnvBuilderV53:
            def with_fair_spill_pool(self, _size):
                return self

            def with_disk_manager_os(self):
                return self

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("benchbox.platforms.datafusion.RuntimeEnv", RuntimeEnvBuilderV53):
                with patch("benchbox.platforms.datafusion.SessionContext", side_effect=[TypeError, Mock()]):
                    adapter = DataFusionAdapter(working_dir=tmpdir, memory_limit="8G")

                    with patch.object(adapter, "handle_existing_database"):
                        with patch.object(adapter, "log_operation_complete") as operation_complete:
                            adapter.create_connection()

            details = operation_complete.call_args.kwargs["details"]
            assert "memory_pool=" not in details
            assert "disk_spilling=" not in details

    def test_from_config_forwards_optimization_flags(self, tmp_path):
        with patch.object(DataFusionAdapter, "__init__", return_value=None) as init:
            DataFusionAdapter.from_config(
                {
                    "working_dir": str(tmp_path),
                    "parquet_pushdown": False,
                    "repartition_joins": False,
                }
            )

        assert init.call_args.kwargs["parquet_pushdown"] is False
        assert init.call_args.kwargs["repartition_joins"] is False

    def test_parse_memory_limit(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            assert adapter._parse_memory_limit("16G") == str(16 * 1024 * 1024 * 1024)
            assert adapter._parse_memory_limit("16GB") == str(16 * 1024 * 1024 * 1024)

            assert adapter._parse_memory_limit("4096M") == str(4096 * 1024 * 1024)
            assert adapter._parse_memory_limit("4096MB") == str(4096 * 1024 * 1024)

            assert adapter._parse_memory_limit("1024K") == str(1024 * 1024)
            assert adapter._parse_memory_limit("1024KB") == str(1024 * 1024)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_create_schema(self, mock_session_context):
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_benchmark.get_schema.return_value = {
            "test": {
                "name": "test",
                "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR(100)"}],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            with patch.object(adapter, "_get_constraint_configuration") as mock_constraints:
                with patch.object(adapter, "_log_constraint_configuration"):
                    mock_constraints.return_value = (False, False)

                    mock_connection = Mock()
                    duration = adapter.create_schema(mock_benchmark, mock_connection)

                    assert duration >= 0
                    assert hasattr(adapter, "_table_schemas")
                    assert isinstance(adapter._table_schemas, dict)
                    assert "test" in adapter._table_schemas
                    assert len(adapter._table_schemas["test"]["columns"]) == 2

    def test_get_benchmark_schema(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_benchmark = Mock()
            mock_benchmark.get_schema.return_value = {
                "customer": {
                    "name": "customer",
                    "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR(100)"}],
                },
                "orders": {
                    "name": "orders",
                    "columns": [{"name": "id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}],
                },
            }

            table_schemas = adapter._get_benchmark_schema(mock_benchmark)
            assert len(table_schemas) == 2
            assert "customer" in table_schemas
            assert "orders" in table_schemas
            assert len(table_schemas["customer"]["columns"]) == 2
            assert table_schemas["customer"]["columns"][0]["name"] == "id"

    def test_get_benchmark_schema_accepts_column_mapping(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            mock_benchmark = Mock()
            mock_benchmark.get_schema.return_value = {
                "trips": {
                    "columns": {
                        "pickup_datetime": {"type": "TIMESTAMP"},
                        "total_amount": {"type": "DOUBLE"},
                    }
                }
            }

            table_schemas = adapter._get_benchmark_schema(mock_benchmark)

            assert table_schemas["trips"]["columns"] == [
                {"name": "pickup_datetime", "type": "TIMESTAMP"},
                {"name": "total_amount", "type": "DOUBLE"},
            ]

    def test_get_benchmark_schema_fallback(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_benchmark = Mock(spec=[])
            mock_benchmark.__class__.__name__ = "CustomBenchmark"

            table_schemas = adapter._get_benchmark_schema(mock_benchmark)
            assert table_schemas == {}

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_csv(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("1,test\n2,data\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="csv")

            mock_conn = Mock()
            mock_result = Mock()
            mock_batch = Mock()
            mock_batch.column.return_value = [2]
            mock_result.collect.return_value = [mock_batch]
            mock_conn.sql.return_value = mock_result

            row_count = adapter._load_table_csv(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert row_count == 2
            assert mock_conn.sql.called

    @patch("benchbox.platforms.datafusion.SessionContext")
    @patch("pyarrow.csv.read_csv")
    @patch("pyarrow.parquet.ParquetWriter")
    def test_load_table_parquet(self, mock_parquet_writer, mock_read_csv, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("1,test\n2,data\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            mock_table = Mock()
            mock_table.num_rows = 2
            mock_read_csv.return_value = mock_table

            mock_conn = Mock()

            row_count = adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert row_count == 2
            mock_parquet_writer.assert_called_once()
            mock_conn.register_parquet.assert_called_once()

    @patch("benchbox.platforms.datafusion.SessionContext")
    @patch("pyarrow.csv.read_csv")
    @patch("pyarrow.parquet.ParquetWriter")
    def test_parquet_conversion_honors_manifest_delimiter_over_format_hint(
        self, mock_parquet_writer, mock_read_csv, mock_session_context
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "tags.csv"
            csv_file.write_text("host,region\nhost_0,us-east-1\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")
            mock_table = Mock()
            mock_table.num_rows = 1
            mock_table.schema = Mock()
            mock_read_csv.return_value = mock_table
            data_source = DataSource(
                source_type="manifest_v2",
                tables={"tags": [csv_file]},
                table_formats={"tags": "tbl"},
                table_metadata={"tags": {"csv_delimiter": ",", "csv_has_header": True}},
            )

            adapter._load_table_parquet(
                Mock(),
                "tags",
                [csv_file],
                Path(tmpdir),
                csv_format="tbl",
                data_source=data_source,
                benchmark=Mock(csv_delimiter=None, csv_has_header=True),
            )

            assert mock_read_csv.call_args.kwargs["parse_options"].delimiter == ","

    def test_is_parquet_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("benchbox.platforms.datafusion.SessionContext"):
                adapter = DataFusionAdapter(working_dir=tmpdir)

            assert adapter._is_parquet_file(Path("customer.parquet")) is True
            assert adapter._is_parquet_file(Path("customer.parquet.zst")) is True
            assert adapter._is_parquet_file(Path("customer.parquet.gz")) is True
            assert adapter._is_parquet_file(Path("customer.parquet.lz4")) is True
            assert adapter._is_parquet_file(Path("customer.parquet.snappy")) is True
            assert adapter._is_parquet_file(Path("customer.csv")) is False
            assert adapter._is_parquet_file(Path("customer.tbl")) is False
            assert adapter._is_parquet_file(Path("customer.tbl.zst")) is False

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_parquet_with_parquet_input(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            import pyarrow as pa
            import pyarrow.parquet as pq

            parquet_file = Path(tmpdir) / "test_table.parquet"
            table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
            pq.write_table(table, parquet_file)

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")
            mock_conn = Mock()

            row_count = adapter._load_table_parquet(mock_conn, "test_table", [parquet_file], Path(tmpdir))

            assert row_count == 3
            mock_conn.register_parquet.assert_called_once_with("test_table", str(parquet_file))

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_parquet_with_multiple_parquet_files(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            import pyarrow as pa
            import pyarrow.parquet as pq

            pq.write_table(pa.table({"id": [1, 2]}), Path(tmpdir) / "part1.parquet")
            pq.write_table(pa.table({"id": [3, 4]}), Path(tmpdir) / "part2.parquet")

            working_dir = Path(tmpdir) / "work"
            working_dir.mkdir()
            adapter = DataFusionAdapter(working_dir=str(working_dir), data_format="parquet")
            mock_conn = Mock()

            file_paths = [Path(tmpdir) / "part1.parquet", Path(tmpdir) / "part2.parquet"]
            row_count = adapter._load_table_parquet(mock_conn, "test_table", file_paths, Path(tmpdir))

            assert row_count == 4
            mock_conn.register_parquet.assert_called_once()
            registered_path = mock_conn.register_parquet.call_args[0][1]
            assert "test_table.parquet" in registered_path

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_data_passes_platform_name_to_resolver(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            mock_benchmark = Mock()
            mock_benchmark.tables = {}

            with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_resolver_cls:
                mock_resolver = Mock()
                mock_resolver.resolve.return_value = None
                mock_resolver_cls.return_value = mock_resolver

                result = adapter.load_data(mock_benchmark, Mock(), Path(tmpdir))

                assert mock_resolver_cls.call_args.kwargs.get("platform_name") == "datafusion"
                assert result == ({}, 0.0, None)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_data_uses_mixed_case_table_format_hint(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "DimCustomer.csv"
            csv_file.write_text("1,test\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="csv")
            mock_conn = Mock()
            mock_benchmark = Mock()
            for table_formats in ({"DimCustomer": "csv"}, {"dimcustomer": "csv"}):
                mock_data_source = Mock()
                mock_data_source.tables = {"DimCustomer": [csv_file]}
                mock_data_source.table_formats = table_formats

                with (
                    patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_resolver_cls,
                    patch.object(adapter, "_load_table_csv", return_value=1) as mock_load_csv,
                ):
                    mock_resolver = Mock()
                    mock_resolver.resolve.return_value = mock_data_source
                    mock_resolver_cls.return_value = mock_resolver

                    result = adapter.load_data(mock_benchmark, mock_conn, Path(tmpdir))

                assert result[0] == {"dimcustomer": 1}
                assert mock_load_csv.call_args.kwargs["csv_format"] == "csv"

    @patch("benchbox.platforms.datafusion.SessionContext")
    @patch("pyarrow.csv.read_csv")
    @patch("pyarrow.parquet.ParquetWriter")
    def test_load_table_parquet_with_csv_input(self, mock_parquet_writer, mock_read_csv, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("1,test\n2,data\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            mock_table = Mock()
            mock_table.num_rows = 2
            mock_read_csv.return_value = mock_table
            mock_conn = Mock()

            row_count = adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert row_count == 2
            mock_read_csv.assert_called_once()
            mock_parquet_writer.assert_called_once()
            mock_conn.register_parquet.assert_called_once()

    def test_detect_directory_format_delta(self, tmp_path):
        delta_dir = tmp_path / "customer"
        delta_dir.mkdir()
        (delta_dir / "_delta_log").mkdir()

        assert DataFusionAdapter._detect_directory_format([delta_dir]) == "delta"

    def test_detect_directory_format_iceberg(self, tmp_path):
        ice_dir = tmp_path / "customer"
        ice_dir.mkdir()
        (ice_dir / "metadata").mkdir()

        assert DataFusionAdapter._detect_directory_format([ice_dir]) == "iceberg"

    def test_detect_directory_format_regular_file(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("a,b\n1,2\n")

        assert DataFusionAdapter._detect_directory_format([csv_file]) is None

    def test_detect_directory_format_multiple_files(self, tmp_path):
        f1 = tmp_path / "part1.parquet"
        f2 = tmp_path / "part2.parquet"
        f1.touch()
        f2.touch()

        assert DataFusionAdapter._detect_directory_format([f1, f2]) is None

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_delta(self, mock_session_context, tmp_path):
        import pyarrow as pa

        adapter = DataFusionAdapter(working_dir=str(tmp_path))
        mock_conn = Mock()

        mock_arrow_table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})

        with patch("benchbox.platforms.datafusion.DataFusionAdapter._load_table_delta") as original:
            original.side_effect = None

        with patch("deltalake.DeltaTable") as mock_delta_cls:
            mock_delta = Mock()
            mock_delta.to_pyarrow_table.return_value = mock_arrow_table
            mock_delta_cls.return_value = mock_delta

            row_count = adapter._load_table_delta(mock_conn, "customer", tmp_path / "customer")

            assert row_count == 3
            mock_conn.register_record_batches.assert_called_once()
            call_args = mock_conn.register_record_batches.call_args
            assert call_args[0][0] == "customer"

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_delta_missing_package(self, mock_session_context, tmp_path):
        adapter = DataFusionAdapter(working_dir=str(tmp_path))
        mock_conn = Mock()

        with patch.dict("sys.modules", {"deltalake": None}):
            with pytest.raises(RuntimeError, match="deltalake"):
                adapter._load_table_delta(mock_conn, "customer", tmp_path / "customer")

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_execute_query_success(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_conn = Mock()
            mock_df = Mock()
            mock_batch = Mock()
            mock_batch.num_rows = 10
            mock_batch.num_columns = 2
            mock_batch.column.side_effect = lambda i: [f"value_{i}"]
            mock_df.collect.return_value = [mock_batch]
            mock_conn.sql.return_value = mock_df

            result = adapter.execute_query(
                mock_conn,
                "SELECT * FROM test",
                "query_1",
                benchmark_type="tpch",
                scale_factor=1.0,
                validate_row_count=False,
            )

            assert result["query_id"] == "query_1"
            assert result["status"] == "SUCCESS"
            assert result["rows_returned"] == 10
            assert result["execution_time_seconds"] >= 0

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_q20_rewrite_is_scoped_to_tpch(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            mock_conn = Mock()
            mock_df = Mock()
            mock_batch = Mock()
            mock_batch.num_rows = 0
            mock_batch.num_columns = 0
            mock_df.collect.return_value = [mock_batch]
            mock_conn.sql.return_value = mock_df
            query = """SELECT ss.s_name
FROM hub_supplier hs
JOIN sat_supplier ss ON hs.hk_supplier = ss.hk_supplier
JOIN sat_part sp ON sp.p_name LIKE 'forest%'
JOIN sat_nation sn ON sn.n_name = 'CANADA'
JOIN sat_lineitem sl ON sl.l_shipdate >= DATE '1994-01-01'
WHERE DATE '1994-01-01' <= DATE '1995-01-01'"""

            result = adapter.execute_query(
                mock_conn,
                query,
                "20",
                benchmark_type="datavault",
                validate_row_count=False,
            )

            assert result["status"] == "SUCCESS"
            assert mock_conn.sql.call_args.args[0] == query

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_execute_query_failure(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_conn = Mock()
            mock_conn.sql.side_effect = Exception("Query failed")

            result = adapter.execute_query(
                mock_conn,
                "SELECT * FROM nonexistent",
                "query_1",
            )

            assert result["query_id"] == "query_1"
            assert result["status"] == "FAILED"
            assert "Query failed" in result["error"]

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_execute_query_dry_run(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            adapter.dry_run_mode = True

            mock_conn = Mock()

            with patch.object(adapter, "capture_sql"):
                result = adapter.execute_query(
                    mock_conn,
                    "SELECT * FROM test",
                    "query_1",
                )

            assert result["query_id"] == "query_1"
            assert result["status"] == "DRY_RUN"
            assert result["dry_run"] is True

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_configure_for_benchmark(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_conn = Mock()
            adapter.configure_for_benchmark(mock_conn, "tpch")

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_apply_platform_optimizations(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_config = Mock()
            mock_conn = Mock()

            adapter.apply_platform_optimizations(mock_config, mock_conn)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_apply_constraint_configuration(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            mock_pk_config = Mock()
            mock_pk_config.enabled = True
            mock_fk_config = Mock()
            mock_fk_config.enabled = True
            mock_conn = Mock()

            adapter.apply_constraint_configuration(mock_pk_config, mock_fk_config, mock_conn)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_check_database_exists(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            assert not adapter.check_database_exists()

            (Path(tmpdir) / "test.parquet").touch()

            assert adapter.check_database_exists()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_drop_database(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir) / "datafusion_working"
            working_dir.mkdir()
            (working_dir / "test.txt").touch()

            adapter = DataFusionAdapter(working_dir=str(working_dir))

            assert working_dir.exists()
            adapter.drop_database()
            assert not working_dir.exists()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_connection_compat_execute_fetchall(self, mock_session_context):
        mock_batch = Mock()
        mock_batch.schema.names = ["a", "b"]
        mock_batch.to_pylist.return_value = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]

        mock_df = Mock()
        mock_df.collect.return_value = [mock_batch]

        mock_ctx = Mock()
        mock_ctx.sql.return_value = mock_df

        compat = DataFusionConnectionCompat(mock_ctx)
        cursor = compat.execute("SELECT * FROM t")
        rows = cursor.fetchall()

        assert rows == [(1, "x"), (2, "y")]
        assert cursor.rowcount == 2

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_connection_compat_execute_is_lazy_for_select(self, mock_session_context):
        mock_batch = Mock()
        mock_batch.schema.names = ["a"]
        mock_batch.to_pylist.return_value = [{"a": 1}]

        mock_df = Mock()
        mock_df.collect.return_value = [mock_batch]

        mock_ctx = Mock()
        mock_ctx.sql.return_value = mock_df

        compat = DataFusionConnectionCompat(mock_ctx)
        cursor = compat.execute("SELECT a FROM t")
        mock_df.collect.assert_not_called()

        assert cursor.fetchall() == [(1,)]
        mock_df.collect.assert_called_once()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_connection_compat_execute_is_eager_for_insert(self, mock_session_context):
        mock_df = Mock()
        mock_df.collect.return_value = []

        mock_ctx = Mock()
        mock_ctx.sql.return_value = mock_df

        compat = DataFusionConnectionCompat(mock_ctx)
        compat.execute("INSERT INTO t VALUES (1)")

        mock_df.collect.assert_called_once()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_connection_compat_execute_splits_multi_statement_batch(self, mock_session_context):
        mock_df = Mock()
        mock_df.collect.return_value = []

        mock_ctx = Mock()
        mock_ctx.sql.return_value = mock_df

        compat = DataFusionConnectionCompat(mock_ctx)
        compat.execute("INSERT INTO t VALUES (1); INSERT INTO t VALUES (2)")

        assert [call.args[0] for call in mock_ctx.sql.call_args_list] == [
            "INSERT INTO t VALUES (1)",
            "INSERT INTO t VALUES (2)",
        ]
        assert mock_df.collect.call_count == 2

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_lifecycle(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            lock_file = adapter._get_working_dir_lock_file()

            assert adapter._acquire_working_dir_lock(timeout_seconds=1)
            assert adapter._acquire_working_dir_lock(timeout_seconds=1)
            assert lock_file.exists()
            adapter._release_working_dir_lock()
            assert lock_file.exists()
            adapter._release_working_dir_lock()
            assert not lock_file.exists()

            assert adapter._acquire_working_dir_lock(timeout_seconds=1)
            adapter._release_working_dir_lock()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_reentrant_does_not_warn(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            with patch.object(adapter.logger, "warning") as mock_warning:
                assert adapter._acquire_working_dir_lock(timeout_seconds=1)
                assert adapter._acquire_working_dir_lock(timeout_seconds=1)
                adapter._release_working_dir_lock()
                adapter._release_working_dir_lock()
            assert mock_warning.call_count == 0

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_is_owner_safe_across_instances(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            owner = DataFusionAdapter(working_dir=tmpdir)
            contender = DataFusionAdapter(working_dir=tmpdir)
            lock_file = owner._get_working_dir_lock_file()

            assert owner._acquire_working_dir_lock(timeout_seconds=1)
            assert lock_file.exists()

            assert contender._acquire_working_dir_lock(timeout_seconds=1)
            assert lock_file.exists()

            contender._release_working_dir_lock()
            assert lock_file.exists()

            owner._release_working_dir_lock()
            assert not lock_file.exists()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_recovers_stale_dead_owner(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            lock_file = adapter._get_working_dir_lock_file()
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text("pid:999999\ntime:0\n", encoding="utf-8")

            with patch.object(adapter, "_is_pid_running", return_value=False):
                assert adapter._acquire_working_dir_lock(timeout_seconds=1)
            adapter._release_working_dir_lock()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_logs_verbose_on_detection(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            lock_file = adapter._get_working_dir_lock_file()
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text("pid:123\ntime:0\n", encoding="utf-8")

            with patch.object(adapter, "log_verbose") as mock_verbose:
                with patch.object(adapter, "_is_pid_running", return_value=False):
                    assert adapter._acquire_working_dir_lock(timeout_seconds=1)

            assert any("lock detected" in str(call.args[0]).lower() for call in mock_verbose.call_args_list)
            adapter._release_working_dir_lock()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_working_dir_lock_logs_warning_when_waiting(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)
            lock_file = adapter._get_working_dir_lock_file()
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text("pid:123\ntime:0\n", encoding="utf-8")

            with patch.object(adapter, "_is_pid_running", return_value=True):
                with patch.object(adapter.logger, "warning") as mock_warning:
                    acquired = adapter._acquire_working_dir_lock(timeout_seconds=0.2)
            assert acquired is False
            assert mock_warning.call_count >= 1
            assert "waiting for release" in str(mock_warning.call_args_list[0].args[0]).lower()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_validate_platform_capabilities(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir)

            result = adapter.validate_platform_capabilities("tpch")

            assert result.is_valid
            assert len(result.errors) == 0

    @patch("benchbox.platforms.datafusion.SessionContext", None)
    def test_validate_platform_capabilities_missing_driver(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ImportError):
                DataFusionAdapter(working_dir=tmpdir)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_from_config(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "output_dir": tmpdir,
                "memory_limit": "8G",
                "partitions": 8,
                "format": "parquet",
                "batch_size": 16384,
            }

            adapter = DataFusionAdapter.from_config(config)

            assert adapter.memory_limit == "8G"
            assert adapter.target_partitions == 8
            assert adapter.data_format == "parquet"
            assert adapter.batch_size == 16384
            assert str(Path(tmpdir) / "databases") in str(adapter.working_dir)

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_from_config_preserves_target_partitions_option(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "benchmark": "tpch",
                "scale_factor": 0.01,
                "output_dir": tmpdir,
                "target_partitions": 7,
            }

            adapter = DataFusionAdapter.from_config(config)

            assert adapter.target_partitions == 7

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_from_config_reads_nested_options_with_top_level_precedence(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "working_dir": tmpdir,
                "memory_limit": "8G",
                "options": {"memory_limit": "4G", "target_partitions": 2},
            }

            adapter = DataFusionAdapter.from_config(config)

            assert adapter.memory_limit == "8G"
            assert adapter.target_partitions == 2

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_add_cli_arguments(self, mock_session_context):
        import argparse

        parser = argparse.ArgumentParser()
        DataFusionAdapter.add_cli_arguments(parser)

        args = parser.parse_args(
            [
                "--datafusion-memory-limit",
                "8G",
                "--datafusion-partitions",
                "4",
                "--datafusion-format",
                "parquet",
            ]
        )

        assert args.datafusion_memory_limit == "8G"
        assert args.datafusion_partitions == 4
        assert args.datafusion_format == "parquet"


class TestDataFusionColumnTypeMapping:
    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_load_table_parquet_with_schema_types(self, mock_session_context):
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "customer_address.csv"
            csv_file.write_text("1|89436|123 Main St\n2|07001|456 Oak Ave\n")

            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {
                "customer_address": {
                    "name": "customer_address",
                    "columns": [
                        {"name": "ca_address_sk", "type": "INTEGER"},
                        {"name": "ca_zip", "type": "CHAR(10)"},
                        {"name": "ca_street", "type": "VARCHAR(60)"},
                    ],
                }
            }

            captured_convert_options = {}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                if convert_options:
                    captured_convert_options["column_types"] = convert_options.column_types
                mock_table = Mock()
                mock_table.num_rows = 2
                return mock_table

            mock_conn = Mock()

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "customer_address", [csv_file], Path(tmpdir))

            assert "column_types" in captured_convert_options
            col_types = captured_convert_options["column_types"]

            assert "ca_zip" in col_types
            assert col_types["ca_zip"] == pa.string()

            assert "ca_street" in col_types
            assert col_types["ca_street"] == pa.string()

            assert "ca_address_sk" in col_types
            assert col_types["ca_address_sk"] == pa.int32()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_column_type_mapping_char_variants(self, mock_session_context):
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {
                "test_table": {
                    "name": "test_table",
                    "columns": [
                        {"name": "col_char", "type": "CHAR(5)"},
                        {"name": "col_char_no_len", "type": "CHAR"},
                        {"name": "col_varchar", "type": "VARCHAR(100)"},
                        {"name": "col_varchar_no_len", "type": "VARCHAR"},
                        {"name": "col_text", "type": "TEXT"},
                        {"name": "col_string", "type": "STRING"},
                    ],
                }
            }

            captured_types = {}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                if convert_options and convert_options.column_types:
                    captured_types.update(convert_options.column_types)
                mock_table = Mock()
                mock_table.num_rows = 1
                return mock_table

            mock_conn = Mock()
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("a|b|c|d|e|f\n")

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            for col_name in [
                "col_char",
                "col_char_no_len",
                "col_varchar",
                "col_varchar_no_len",
                "col_text",
                "col_string",
            ]:
                assert col_name in captured_types, f"{col_name} should be in column_types"
                assert captured_types[col_name] == pa.string(), f"{col_name} should be pa.string()"

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_column_type_mapping_date(self, mock_session_context):
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {
                "test_table": {
                    "name": "test_table",
                    "columns": [
                        {"name": "order_date", "type": "DATE"},
                        {"name": "ship_date", "type": "DATE"},
                    ],
                }
            }

            captured_types = {}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                if convert_options and convert_options.column_types:
                    captured_types.update(convert_options.column_types)
                mock_table = Mock()
                mock_table.num_rows = 1
                return mock_table

            mock_conn = Mock()
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("2024-01-01|2024-01-05\n")

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert "order_date" in captured_types
            assert captured_types["order_date"] == pa.date32()
            assert "ship_date" in captured_types
            assert captured_types["ship_date"] == pa.date32()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_column_type_mapping_decimal(self, mock_session_context):
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {
                "test_table": {
                    "name": "test_table",
                    "columns": [
                        {"name": "price", "type": "DECIMAL(10,2)"},
                        {"name": "tax", "type": "DECIMAL"},
                    ],
                }
            }

            captured_types = {}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                if convert_options and convert_options.column_types:
                    captured_types.update(convert_options.column_types)
                mock_table = Mock()
                mock_table.num_rows = 1
                return mock_table

            mock_conn = Mock()
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("99.99|5.50\n")

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert "price" in captured_types
            assert captured_types["price"] == pa.float64()
            assert "tax" in captured_types
            assert captured_types["tax"] == pa.float64()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_column_type_mapping_integer_uses_inference(self, mock_session_context):
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {
                "test_table": {
                    "name": "test_table",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "quantity", "type": "BIGINT"},
                        {"name": "small_num", "type": "SMALLINT"},
                    ],
                }
            }

            captured_types = {}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                if convert_options and convert_options.column_types:
                    captured_types.update(convert_options.column_types)
                mock_table = Mock()
                mock_table.num_rows = 1
                return mock_table

            mock_conn = Mock()
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("1|100|5\n")

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "test_table", [csv_file], Path(tmpdir))

            assert "id" in captured_types
            assert captured_types["id"] == pa.int32()
            assert "quantity" in captured_types
            assert captured_types["quantity"] == pa.int64()
            assert "small_num" in captured_types
            assert captured_types["small_num"] == pa.int32()

    @patch("benchbox.platforms.datafusion.SessionContext")
    def test_no_schema_uses_no_column_types(self, mock_session_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DataFusionAdapter(working_dir=tmpdir, data_format="parquet")

            adapter._table_schemas = {}

            captured_types = {"called": False}

            def capture_read_csv(file_path, read_options=None, parse_options=None, convert_options=None):
                captured_types["called"] = True
                if convert_options:
                    captured_types["column_types"] = convert_options.column_types
                mock_table = Mock()
                mock_table.num_rows = 1
                return mock_table

            mock_conn = Mock()
            csv_file = Path(tmpdir) / "test.csv"
            csv_file.write_text("1|test|2024-01-01\n")

            with patch("pyarrow.csv.read_csv", side_effect=capture_read_csv):
                with patch("pyarrow.parquet.ParquetWriter"):
                    adapter._load_table_parquet(mock_conn, "unknown_table", [csv_file], Path(tmpdir))

            assert captured_types["called"]
            col_types = captured_types.get("column_types")
            assert col_types is None or len(col_types) == 0


class TestDetectCsvFormatHint:
    @pytest.fixture()
    def adapter(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            yield DataFusionAdapter(working_dir=tmpdir)

    def test_tbl_hint_returns_pipe(self, adapter):
        assert adapter._detect_csv_format([], csv_format="tbl") == "|"

    def test_csv_hint_returns_comma(self, adapter):
        assert adapter._detect_csv_format([], csv_format="csv") == ","

    def test_none_hint_falls_back_to_extension(self, adapter, tmp_path):
        tbl_file = tmp_path / "data.tbl"
        tbl_file.write_text("1|hello\n")
        delimiter = adapter._detect_csv_format([tbl_file], csv_format=None)
        assert delimiter == "|"

    def test_none_hint_no_files_defaults_comma(self, adapter):
        delimiter = adapter._detect_csv_format([], csv_format=None)
        assert delimiter == ","

    def test_tbl_hint_overrides_csv_extension(self, adapter, tmp_path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("1,hello\n")
        delimiter = adapter._detect_csv_format([csv_file], csv_format="tbl")
        assert delimiter == "|"

    def test_csv_hint_overrides_tbl_extension(self, adapter, tmp_path):
        tbl_file = tmp_path / "data.tbl"
        tbl_file.write_text("1|hello\n")
        delimiter = adapter._detect_csv_format([tbl_file], csv_format="csv")
        assert delimiter == ","


class TestCreateEmptySchema:
    @pytest.fixture()
    def adapter(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            a = DataFusionAdapter(working_dir=tmpdir)
            yield a

    def test_returns_empty_dict(self, adapter):
        adapter._table_schemas = {"orders": {"columns": [{"name": "id", "type": "INTEGER"}]}}
        conn = MagicMock()
        result = adapter._create_empty_schema_tables(conn)
        assert result == {}

    def test_executes_create_table_for_each_schema(self, adapter):
        adapter._table_schemas = {
            "orders": {"columns": [{"name": "id", "type": "INTEGER"}]},
            "customers": {"columns": [{"name": "name", "type": "VARCHAR"}]},
        }
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        assert conn.execute.call_count == 2

    def test_ddl_contains_table_name(self, adapter):
        adapter._table_schemas = {"my_table": {"columns": [{"name": "col", "type": "BIGINT"}]}}
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        ddl = conn.execute.call_args[0][0]
        assert "my_table" in ddl

    def test_ddl_maps_integer_to_int(self, adapter):
        adapter._table_schemas = {"t": {"columns": [{"name": "n", "type": "INTEGER"}]}}
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        ddl = conn.execute.call_args[0][0]
        assert '"n" INT' in ddl

    def test_ddl_maps_bigint(self, adapter):
        adapter._table_schemas = {"t": {"columns": [{"name": "n", "type": "BIGINT"}]}}
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        ddl = conn.execute.call_args[0][0]
        assert '"n" BIGINT' in ddl

    def test_ddl_maps_varchar_as_default(self, adapter):
        adapter._table_schemas = {"t": {"columns": [{"name": "s", "type": "TEXT"}]}}
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        ddl = conn.execute.call_args[0][0]
        assert '"s" VARCHAR' in ddl

    def test_ddl_handles_parameterised_decimal(self, adapter):
        adapter._table_schemas = {"t": {"columns": [{"name": "price", "type": "DECIMAL(10,2)"}]}}
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        ddl = conn.execute.call_args[0][0]
        assert '"price" DECIMAL(10,2)' in ddl

    def test_skips_tables_with_no_columns(self, adapter):
        adapter._table_schemas = {
            "empty_table": {"columns": []},
            "real_table": {"columns": [{"name": "id", "type": "INTEGER"}]},
        }
        conn = MagicMock()
        adapter._create_empty_schema_tables(conn)
        assert conn.execute.call_count == 1

    def test_tolerates_execute_exception(self, adapter):
        adapter._table_schemas = {"t": {"columns": [{"name": "id", "type": "INTEGER"}]}}
        conn = MagicMock()
        conn.execute.side_effect = Exception("DDL error")
        result = adapter._create_empty_schema_tables(conn)
        assert result == {}


class TestGetBenchmarkSchemaOBTTable:
    @pytest.fixture()
    def adapter(self):
        with patch("benchbox.platforms.datafusion.SessionContext"), tempfile.TemporaryDirectory() as tmpdir:
            yield DataFusionAdapter(working_dir=tmpdir)

    def test_extracts_columns_from_dataclass_style_table(self, adapter):
        col1 = Mock()
        col1.name = "sale_id"
        col1.sql_type = Mock(return_value="BIGINT")

        col2 = Mock()
        col2.name = "amount"
        col2.sql_type = Mock(return_value="DECIMAL(18,2)")

        table_def = Mock()
        table_def.columns = [col1, col2]

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"sales": table_def}

        schemas = adapter._get_benchmark_schema(mock_benchmark)

        assert "sales" in schemas
        assert len(schemas["sales"]["columns"]) == 2
        assert schemas["sales"]["columns"][0] == {"name": "sale_id", "type": "BIGINT"}
        assert schemas["sales"]["columns"][1] == {"name": "amount", "type": "DECIMAL(18,2)"}

    def test_dataclass_column_without_sql_type_defaults_to_varchar(self, adapter):
        col = Mock(spec=["name"])
        col.name = "notes"

        table_def = Mock()
        table_def.columns = [col]

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"info": table_def}

        schemas = adapter._get_benchmark_schema(mock_benchmark)

        assert schemas["info"]["columns"][0] == {"name": "notes", "type": "VARCHAR"}

    def test_table_name_normalized_to_lowercase(self, adapter):
        col = Mock()
        col.name = "id"
        col.sql_type = Mock(return_value="INTEGER")

        table_def = Mock()
        table_def.columns = [col]

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"DimCustomer": table_def}

        schemas = adapter._get_benchmark_schema(mock_benchmark)

        assert "dimcustomer" in schemas
        assert "DimCustomer" not in schemas
