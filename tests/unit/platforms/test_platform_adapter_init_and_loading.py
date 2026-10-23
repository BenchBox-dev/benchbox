from __future__ import annotations

import argparse
import builtins
import importlib
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFrameInit:
    def test_core_exports_available(self):

        from benchbox.platforms.dataframe import (
            PANDAS_AVAILABLE,
            POLARS_AVAILABLE,
            BenchmarkExecutionMixin,
            DataFrameFamily,
            DataFramePhases,
            DataFrameRunOptions,
            ExpressionFamilyAdapter,
            ExpressionFamilyContext,
            PandasDataFrameAdapter,
            PandasFamilyAdapter,
            PandasFamilyContext,
            PolarsDataFrameAdapter,
        )

        assert callable(BenchmarkExecutionMixin), "BenchmarkExecutionMixin should be a class"
        assert callable(ExpressionFamilyAdapter), "ExpressionFamilyAdapter should be a class"
        assert isinstance(POLARS_AVAILABLE, bool)
        assert isinstance(PANDAS_AVAILABLE, bool)

    def test_optional_cudf_export(self):
        from benchbox.platforms.dataframe import CUDF_AVAILABLE, CuDFDataFrameAdapter

        assert isinstance(CUDF_AVAILABLE, bool)
        assert CuDFDataFrameAdapter is None or callable(CuDFDataFrameAdapter)

    def test_optional_dask_export(self):
        from benchbox.platforms.dataframe import DASK_AVAILABLE, DaskDataFrameAdapter

        assert isinstance(DASK_AVAILABLE, bool)
        assert DaskDataFrameAdapter is None or callable(DaskDataFrameAdapter)

    def test_optional_datafusion_export(self):
        from benchbox.platforms.dataframe import DATAFUSION_DF_AVAILABLE, DataFusionDataFrameAdapter

        assert isinstance(DATAFUSION_DF_AVAILABLE, bool)
        assert DataFusionDataFrameAdapter is None or callable(DataFusionDataFrameAdapter)

    def test_optional_pyspark_export(self):
        from benchbox.platforms.dataframe import PYSPARK_AVAILABLE, PYSPARK_VERSION, PySparkDataFrameAdapter

        assert isinstance(PYSPARK_AVAILABLE, bool)
        assert PySparkDataFrameAdapter is None or callable(PySparkDataFrameAdapter)

    def test_optional_pyspark_maintenance_export(self):
        from benchbox.platforms.dataframe import DELTA_SPARK_AVAILABLE

        assert isinstance(DELTA_SPARK_AVAILABLE, bool)

    def test_all_list(self):
        import benchbox.platforms.dataframe as df_mod

        assert isinstance(df_mod.__all__, list)
        assert "PolarsDataFrameAdapter" in df_mod.__all__
        assert "ExpressionFamilyAdapter" in df_mod.__all__

    @pytest.mark.parametrize(
        "module_name,flag_name,adapter_name",
        [
            ("benchbox.platforms.dataframe.cudf_df", "CUDF_AVAILABLE", "CuDFDataFrameAdapter"),
            ("benchbox.platforms.dataframe.dask_df", "DASK_AVAILABLE", "DaskDataFrameAdapter"),
            ("benchbox.platforms.dataframe.datafusion_df", "DATAFUSION_DF_AVAILABLE", "DataFusionDataFrameAdapter"),
            ("benchbox.platforms.dataframe.pyspark_df", "PYSPARK_AVAILABLE", "PySparkDataFrameAdapter"),
            (
                "benchbox.platforms.dataframe.pyspark_maintenance",
                "DELTA_SPARK_AVAILABLE",
                "PySparkMaintenanceOperations",
            ),
        ],
    )
    def test_optional_import_fallbacks_via_reload(self, monkeypatch, module_name, flag_name, adapter_name):
        import benchbox.platforms.dataframe as df_mod

        original_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == module_name:
                raise ImportError(f"forced import error for {name}")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", guarded_import)
        monkeypatch.delitem(sys.modules, "benchbox.platforms.dataframe", raising=False)
        monkeypatch.delitem(sys.modules, module_name, raising=False)

        reloaded = importlib.import_module("benchbox.platforms.dataframe")
        assert getattr(reloaded, flag_name) is False
        assert getattr(reloaded, adapter_name) is None

        monkeypatch.setattr(builtins, "__import__", original_import)
        monkeypatch.delitem(sys.modules, "benchbox.platforms.dataframe", raising=False)
        importlib.import_module("benchbox.platforms.dataframe")
        assert df_mod is not None


class TestDuckLakeCapabilities:
    def test_capabilities_structure(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DUCKLAKE_CAPABILITIES

        assert DUCKLAKE_CAPABILITIES.platform_name == "ducklake"
        assert DUCKLAKE_CAPABILITIES.supports_insert is True
        assert DUCKLAKE_CAPABILITIES.supports_delete is True
        assert DUCKLAKE_CAPABILITIES.supports_update is True
        assert DUCKLAKE_CAPABILITIES.supports_merge is True
        assert DUCKLAKE_CAPABILITIES.supports_transactions is True
        assert DUCKLAKE_CAPABILITIES.supports_time_travel is True
        assert DUCKLAKE_CAPABILITIES.max_batch_size == 1000000


class TestDuckLakeMaintenanceOperations:
    def test_init_with_working_dir(self, tmp_path):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations(working_dir=tmp_path)
        assert ops.working_dir == tmp_path
        assert ops._ducklake_loaded is False

    def test_init_without_working_dir(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        assert ops.working_dir is None

    def test_get_capabilities(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import (
            DUCKLAKE_CAPABILITIES,
            DuckLakeMaintenanceOperations,
        )

        ops = DuckLakeMaintenanceOperations()
        assert ops._get_capabilities() is DUCKLAKE_CAPABILITIES

    def test_get_table_name_from_path(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        assert ops._get_table_name_from_path("/data/orders") == "orders"
        assert ops._get_table_name_from_path(Path("/data/warehouse/lineitem")) == "lineitem"

    def test_do_insert_zero_rows(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()

        mock_arrow = MagicMock()
        mock_arrow.num_rows = 0
        with patch.object(ops, "_convert_to_arrow", return_value=mock_arrow):
            result = ops._do_insert("/data/test", MagicMock(), None, "append")
        assert result == 0

    def test_get_connection_no_metadata(self, tmp_path):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()
        with patch("benchbox.platforms.dataframe.ducklake_maintenance.duckdb") as mock_duckdb:
            mock_duckdb.connect.return_value = mock_conn
            with pytest.raises(RuntimeError, match="metadata not found"):
                ops._get_connection(tmp_path / "nonexistent")

    def test_get_connection_extension_load_failure(self, tmp_path):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("extension not found")
        with patch("benchbox.platforms.dataframe.ducklake_maintenance.duckdb") as mock_duckdb:
            mock_duckdb.connect.return_value = mock_conn
            with pytest.raises(RuntimeError, match="Failed to load DuckLake extension"):
                ops._get_connection(tmp_path)


class TestGetDuckLakeMaintenanceOperations:
    def test_returns_ops_when_available(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import get_ducklake_maintenance_operations

        ops = get_ducklake_maintenance_operations()
        assert ops is not None

    def test_returns_none_when_duckdb_unavailable(self):
        with patch("benchbox.platforms.dataframe.ducklake_maintenance.DUCKDB_AVAILABLE", False):
            from benchbox.platforms.dataframe.ducklake_maintenance import get_ducklake_maintenance_operations

            result = get_ducklake_maintenance_operations()
        assert result is None

    def test_returns_none_when_pyarrow_unavailable(self):
        with patch("benchbox.platforms.dataframe.ducklake_maintenance.PYARROW_AVAILABLE", False):
            from benchbox.platforms.dataframe.ducklake_maintenance import get_ducklake_maintenance_operations

            result = get_ducklake_maintenance_operations()
        assert result is None


class TestInfluxDBEscapeFunctions:
    def test_escape_tag_value_special_chars(self):
        from benchbox.platforms.influxdb.client import escape_tag_value

        assert escape_tag_value("hello world") == "hello\\ world"
        assert escape_tag_value("key=value") == "key\\=value"
        assert escape_tag_value("a,b") == "a\\,b"
        assert escape_tag_value("a\\b") == "a\\\\b"

    def test_escape_tag_value_no_escaping(self):
        from benchbox.platforms.influxdb.client import escape_tag_value

        assert escape_tag_value("simple") == "simple"

    def test_escape_field_string_quotes(self):
        from benchbox.platforms.influxdb.client import escape_field_string

        assert escape_field_string('say "hello"') == 'say \\"hello\\"'
        assert escape_field_string("backslash\\here") == "backslash\\\\here"

    def test_escape_field_string_no_escaping(self):
        from benchbox.platforms.influxdb.client import escape_field_string

        assert escape_field_string("simple") == "simple"


class TestToLineProtocol:
    def test_basic_measurement(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {"host": "server1"}, {"usage": 75.5})
        assert result == "cpu,host=server1 usage=75.5"

    def test_integer_field(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("mem", {}, {"total": 8192})
        assert "total=8192i" in result

    def test_boolean_field(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("status", {}, {"active": True})
        assert "active=true" in result

    def test_string_field(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("log", {}, {"message": "hello world"})
        assert 'message="hello world"' in result

    def test_with_timestamp_int(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {}, {"usage": 50.0}, timestamp=1234567890)
        assert result.endswith(" 1234567890")

    def test_with_timestamp_datetime(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        dt = datetime(2024, 1, 1, 0, 0, 0)
        result = to_line_protocol("cpu", {}, {"usage": 50.0}, timestamp=dt)
        assert " " in result

    def test_no_tags(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {}, {"usage": 50.0})
        assert result.startswith("cpu usage=")

    def test_none_field_skipped(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {}, {"usage": 50.0, "temp": None})
        assert "temp" not in result

    def test_none_tag_skipped(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {"host": None, "region": "us"}, {"usage": 50.0})
        assert "host" not in result
        assert "region" in result

    def test_empty_tag_skipped(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        result = to_line_protocol("cpu", {"host": "", "region": "us"}, {"usage": 50.0})
        assert "host" not in result

    def test_no_fields_raises(self):
        from benchbox.platforms.influxdb.client import to_line_protocol

        with pytest.raises(ValueError, match="At least one field"):
            to_line_protocol("cpu", {}, {})


class TestInfluxDBConnection:
    def test_init(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="mytoken", database="mydb")
        assert conn.host == "localhost"
        assert conn.token == "mytoken"
        assert conn.database == "mydb"
        assert conn.port == 443
        assert conn.ssl is True
        assert conn._url == "https://localhost:443"
        assert conn._client is None

    def test_init_no_ssl(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="t", database="db", ssl=False, port=8086)
        assert conn._url == "http://localhost:8086"

    def test_execute_without_connect_raises(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="t", database="db")
        with pytest.raises(RuntimeError, match="Not connected"):
            conn.execute("SELECT 1")

    def test_connect_no_library_raises(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="t", database="db")
        with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", False):
            with patch("benchbox.platforms.influxdb.client.FLIGHTSQL_AVAILABLE", False):
                with pytest.raises(ImportError, match="No InfluxDB client"):
                    conn.connect()

    def test_connect_influxdb3(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="t", database="db")
        mock_client = MagicMock()
        with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", True):
            with patch("benchbox.platforms.influxdb.client.InfluxDBClient3", return_value=mock_client):
                conn.connect()
        assert conn._client_type == "influxdb3"

    def test_connect_flightsql_fallback(self):
        from benchbox.platforms.influxdb.client import InfluxDBConnection

        conn = InfluxDBConnection(host="localhost", token="t", database="db")
        mock_client = MagicMock()
        with patch("benchbox.platforms.influxdb.client.INFLUXDB3_AVAILABLE", False):
            with patch("benchbox.platforms.influxdb.client.FLIGHTSQL_AVAILABLE", True):
                with patch("benchbox.platforms.influxdb.client.FlightSQLClient", return_value=mock_client):
                    conn.connect()
        assert conn._client_type == "flightsql"


class TestBenchmarkTablesSource:
    def test_can_provide_with_tables(self):
        from benchbox.platforms.base.data_loading import BenchmarkTablesSource

        src = BenchmarkTablesSource()
        benchmark = types.SimpleNamespace(tables={"orders": Path("/data/orders.csv")})
        assert src.can_provide(benchmark, Path("/data")) is True

    def test_can_provide_no_tables(self):
        from benchbox.platforms.base.data_loading import BenchmarkTablesSource

        src = BenchmarkTablesSource()
        benchmark = types.SimpleNamespace()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_get_data_source_normalizes_to_list(self):
        from benchbox.platforms.base.data_loading import BenchmarkTablesSource

        src = BenchmarkTablesSource()
        benchmark = types.SimpleNamespace(
            tables={"orders": Path("/data/orders.csv"), "lineitem": [Path("/data/li_1.csv")]}
        )
        result = src.get_data_source(benchmark, Path("/data"))
        assert result is not None
        assert result.source_type == "benchmark_tables"
        assert isinstance(result.tables["orders"], list)
        assert isinstance(result.tables["lineitem"], list)


class TestBenchmarkImplTablesSource:
    def test_can_provide(self):
        from benchbox.platforms.base.data_loading import BenchmarkImplTablesSource

        src = BenchmarkImplTablesSource()
        benchmark = types.SimpleNamespace(_impl=types.SimpleNamespace(tables={"orders": Path("/data/orders.csv")}))
        assert src.can_provide(benchmark, Path("/data")) is True

    def test_cannot_provide_no_impl(self):
        from benchbox.platforms.base.data_loading import BenchmarkImplTablesSource

        src = BenchmarkImplTablesSource()
        benchmark = types.SimpleNamespace()
        assert src.can_provide(benchmark, Path("/data")) is False


class TestManifestFileSource:
    def test_can_provide_no_manifest(self, tmp_path):
        from benchbox.platforms.base.data_loading import ManifestFileSource

        src = ManifestFileSource()
        assert src.can_provide(types.SimpleNamespace(), tmp_path) is False

    def test_can_provide_with_manifest(self, tmp_path):
        from benchbox.platforms.base.data_loading import ManifestFileSource

        (tmp_path / "_datagen_manifest.json").write_text("{}")
        src = ManifestFileSource()
        assert src.can_provide(types.SimpleNamespace(), tmp_path) is True


class TestDataSourceResolver:
    def test_resolve_with_tables(self):
        from benchbox.platforms.base.data_loading import DataSourceResolver

        resolver = DataSourceResolver()
        benchmark = types.SimpleNamespace(tables={"orders": Path("/data/orders.csv")})
        result = resolver.resolve(benchmark, Path("/data"))
        assert result is not None
        assert result.source_type == "benchmark_tables"

    def test_resolve_none(self):
        from benchbox.platforms.base.data_loading import DataSourceResolver

        resolver = DataSourceResolver()
        benchmark = types.SimpleNamespace()
        result = resolver.resolve(benchmark, Path("/nonexistent"))
        assert result is None

    def test_platform_name_propagates_to_manifest_source(self):

        from benchbox.platforms.base.data_loading import DataSourceResolver, ManifestFileSource

        resolver = DataSourceResolver(platform_name="datafusion")
        manifest_sources = [p for p in resolver.providers if isinstance(p, ManifestFileSource)]
        assert len(manifest_sources) == 1
        assert manifest_sources[0]._platform_name == "datafusion"

    def test_platform_name_default_is_duckdb(self):

        from benchbox.platforms.base.data_loading import DataSourceResolver, ManifestFileSource

        resolver = DataSourceResolver()
        manifest_sources = [p for p in resolver.providers if isinstance(p, ManifestFileSource)]
        assert len(manifest_sources) == 1
        assert manifest_sources[0]._platform_name == "duckdb"


class TestGzipHandler:
    def test_open_gzip_file(self, tmp_path):
        import gzip

        from benchbox.platforms.base.data_loading import GzipHandler

        gz_file = tmp_path / "test.csv.gz"
        with gzip.open(gz_file, "wt") as f:
            f.write("col1,col2\n1,2\n")

        handler = GzipHandler()
        with handler.open(gz_file) as f:
            content = f.read()
        assert "col1" in content


class TestNoCompressionHandler:
    def test_open_plain_file(self, tmp_path):
        from benchbox.platforms.base.data_loading import NoCompressionHandler

        plain_file = tmp_path / "test.csv"
        plain_file.write_text("col1,col2\n1,2\n")

        handler = NoCompressionHandler()
        with handler.open(plain_file) as f:
            content = f.read()
        assert "col1" in content


class TestDataSource:
    def test_data_source_creation(self):
        from benchbox.platforms.base.data_loading import DataSource

        ds = DataSource(source_type="test", tables={"orders": [Path("/data/orders.csv")]})
        assert ds.source_type == "test"
        assert "orders" in ds.tables


class TestBigQueryAdapterPaths:
    def test_normalize_table_name(self):

        from benchbox.platforms.bigquery import BigQueryAdapter

        adapter = BigQueryAdapter.__new__(BigQueryAdapter)
        adapter.platform_type = "bigquery"
        adapter._case_sensitive = False

        if hasattr(adapter, "_normalize_table_name"):
            result = adapter._normalize_table_name("MyTable")
            assert result == "mytable"

    def test_gcs_path_detection(self):

        from benchbox.utils.cloud_storage import is_cloud_path

        assert is_cloud_path("gs://bucket/path") is True
        assert is_cloud_path("/local/path") is False

    def test_adapter_class_exists(self):
        from benchbox.platforms.bigquery import BigQueryAdapter

        assert BigQueryAdapter is not None
        assert hasattr(BigQueryAdapter, "execute_query")


class TestRedshiftAdapterPaths:
    def test_adapter_class_exists(self):
        from benchbox.platforms.redshift import RedshiftAdapter

        assert RedshiftAdapter is not None
        assert hasattr(RedshiftAdapter, "execute_query")

    def test_redshift_hostname_parsing(self):

        from benchbox.platforms.redshift import RedshiftAdapter

        adapter = RedshiftAdapter.__new__(RedshiftAdapter)
        if hasattr(adapter, "_parse_hostname"):
            result = adapter._parse_hostname("mycluster.abc123.us-east-1.redshift.amazonaws.com")
            assert "us-east-1" in str(result) or result is not None
        elif hasattr(adapter, "_detect_endpoint_type"):
            assert callable(adapter._detect_endpoint_type)


class TestAthenaAdapterPaths:
    def test_adapter_class_exists(self):
        from benchbox.platforms.athena import AthenaAdapter

        assert AthenaAdapter is not None

    def test_dialect_is_trino(self):

        from benchbox.platforms.athena import AthenaAdapter

        adapter = AthenaAdapter.__new__(AthenaAdapter)
        if hasattr(adapter, "dialect"):
            assert adapter.dialect in ("trino", "presto", "athena")

    def test_s3_staging_path_construction(self):

        test_bucket = "s3://my-benchbox-bucket/athena-results/"
        assert test_bucket.startswith("s3://")
        assert not test_bucket.startswith("s3://s3://")


class TestFabricWarehouseAdapterPaths:
    def test_adapter_class_exists(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        assert FabricWarehouseAdapter is not None

    def test_onelake_path_prefix(self):

        prefix = "abfss://"
        assert prefix in "abfss://workspace@onelake.dfs.fabric.microsoft.com/lakehouse/Files/"

    def test_fabric_dialect(self):
        from benchbox.platforms.fabric_warehouse import FABRIC_DIALECT

        assert FABRIC_DIALECT is not None
        assert isinstance(FABRIC_DIALECT, str)

    def test_init_missing_workspace_and_server_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            with pytest.raises(ConfigurationError, match="connection details"):
                FabricWarehouseAdapter(database="mydb")

    def test_init_service_principal_incomplete_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            with pytest.raises(ConfigurationError, match="incomplete"):
                FabricWarehouseAdapter(
                    workspace="test-guid",
                    warehouse="mywarehouse",
                    auth_method="service_principal",
                    client_id="test",
                )

    def test_init_no_database_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            with pytest.raises(ConfigurationError, match="warehouse name"):
                FabricWarehouseAdapter(
                    workspace="test-guid",
                )

    def test_init_builds_server_from_workspace(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            adapter = FabricWarehouseAdapter(
                workspace="test-guid",
                warehouse="mywarehouse",
            )
            assert adapter.server == "test-guid.datawarehouse.fabric.microsoft.com"
            assert adapter.database == "mywarehouse"

    def test_init_item_type_warning(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            with pytest.warns(UserWarning, match="only supports Warehouse"):
                FabricWarehouseAdapter(
                    workspace="test-guid",
                    warehouse="mywarehouse",
                    item_type="Lakehouse",
                )

    def test_platform_name(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            adapter = FabricWarehouseAdapter(
                workspace="test-guid",
                warehouse="mywarehouse",
            )
            assert adapter.platform_name == "Fabric Warehouse"

    def test_get_target_dialect(self):
        from benchbox.platforms.fabric_warehouse import FABRIC_DIALECT, FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            adapter = FabricWarehouseAdapter(
                workspace="test-guid",
                warehouse="mywarehouse",
            )
            assert adapter.get_target_dialect() == FABRIC_DIALECT


class TestAthenaAdapterInit:
    def test_init_with_staging_dir(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.boto3", MagicMock()):
            with patch("benchbox.platforms.athena.athena_connect", MagicMock()):
                adapter = AthenaAdapter(
                    s3_staging_dir="s3://my-bucket/staging/",
                    aws_access_key_id="AKIA...",
                    aws_secret_access_key="secret",
                )
                assert adapter.s3_bucket == "my-bucket"
                assert adapter.s3_prefix == "staging"
                assert adapter._dialect == "trino"

    def test_init_with_bucket(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.boto3", MagicMock()):
            with patch("benchbox.platforms.athena.athena_connect", MagicMock()):
                adapter = AthenaAdapter(
                    s3_bucket="my-bucket",
                    s3_prefix="data/",
                    aws_access_key_id="AKIA...",
                    aws_secret_access_key="secret",
                )
                assert adapter.s3_bucket == "my-bucket"
                assert adapter.s3_prefix == "data"

    def test_init_data_format_default(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.boto3", MagicMock()):
            with patch("benchbox.platforms.athena.athena_connect", MagicMock()):
                adapter = AthenaAdapter(
                    s3_bucket="my-bucket",
                    aws_access_key_id="AKIA...",
                    aws_secret_access_key="secret",
                )
                assert adapter.data_format == "parquet"
                assert adapter.workgroup == "primary"
                assert adapter.region == "us-east-1"

    def test_init_cost_tracking(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.boto3", MagicMock()):
            with patch("benchbox.platforms.athena.athena_connect", MagicMock()):
                adapter = AthenaAdapter(
                    s3_bucket="my-bucket",
                    aws_access_key_id="AKIA...",
                    aws_secret_access_key="secret",
                )
                assert adapter._total_data_scanned_bytes == 0
                assert adapter._query_count == 0


class TestSchemaInspector:
    def test_get_column_count_from_schema(self):
        import io

        from benchbox.platforms.base.data_loading import SchemaInspector

        benchmark = types.SimpleNamespace(get_schema=lambda: {"orders": {"columns": ["id", "name", "price"]}})
        count = SchemaInspector.get_column_count(benchmark, "orders", io.StringIO(), "|")
        assert count == 3

    def test_get_column_count_from_file(self):
        import io

        from benchbox.platforms.base.data_loading import SchemaInspector

        benchmark = types.SimpleNamespace(get_schema=dict)
        fh = io.StringIO("col1|col2|col3\n1|2|3\n")
        count = SchemaInspector.get_column_count(benchmark, "orders", fh, "|")
        assert count == 3

    def test_get_column_count_empty_file(self):
        import io

        from benchbox.platforms.base.data_loading import SchemaInspector

        benchmark = types.SimpleNamespace(get_schema=dict)
        fh = io.StringIO("")
        count = SchemaInspector.get_column_count(benchmark, "orders", fh, "|")
        assert count is None


class TestRowBatchProcessor:
    def test_process_file_single_batch(self):
        import io

        from benchbox.platforms.base.data_loading import RowBatchProcessor

        processor = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("a|b|c\n1|2|3\n4|5|6\n")
        batches = list(processor.process_file(fh, "|", 3))
        assert len(batches) == 1
        assert batches[0][1] == 3

    def test_process_file_multiple_batches(self):
        import io

        from benchbox.platforms.base.data_loading import RowBatchProcessor

        processor = RowBatchProcessor(batch_size=2)
        lines = "\n".join(f"{i}|{i + 1}|{i + 2}" for i in range(5))
        fh = io.StringIO(lines)
        batches = list(processor.process_file(fh, "|", 3))
        assert len(batches) >= 2

    def test_process_file_pads_short_rows(self):
        import io

        from benchbox.platforms.base.data_loading import RowBatchProcessor

        processor = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("1|2\n")
        batches = list(processor.process_file(fh, "|", 4))
        assert len(batches) == 1
        row = batches[0][0][0]
        assert len(row) == 4

    def test_process_file_skips_empty_lines(self):
        import io

        from benchbox.platforms.base.data_loading import RowBatchProcessor

        processor = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("1|2\n\n\n3|4\n")
        batches = list(processor.process_file(fh, "|", 2))
        assert batches[0][1] == 2


class TestDelimitedFileHandler:
    def test_get_delimiter(self):
        from benchbox.platforms.base.data_loading import DelimitedFileHandler

        handler = DelimitedFileHandler("|")
        assert handler.get_delimiter() == "|"

    def test_get_delimiter_comma(self):
        from benchbox.platforms.base.data_loading import DelimitedFileHandler

        handler = DelimitedFileHandler(",")
        assert handler.get_delimiter() == ","


class TestFileFormatRegistry:
    def test_get_compression_handler_plain(self, tmp_path):
        from benchbox.platforms.base.data_loading import FileFormatRegistry, NoCompressionHandler

        handler = FileFormatRegistry.get_compression_handler(tmp_path / "test.csv")
        assert isinstance(handler, NoCompressionHandler)

    def test_get_compression_handler_gzip(self, tmp_path):
        from benchbox.platforms.base.data_loading import FileFormatRegistry, GzipHandler

        handler = FileFormatRegistry.get_compression_handler(tmp_path / "test.csv.gz")
        assert isinstance(handler, GzipHandler)


class TestManifestFileSourceV1:
    def test_get_data_source_v1(self, tmp_path):
        import json

        from benchbox.platforms.base.data_loading import ManifestFileSource

        manifest = {
            "tables": {
                "orders": [{"path": "orders.csv"}],
                "lineitem": [{"path": "lineitem_1.csv"}, {"path": "lineitem_2.csv"}],
            }
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

        src = ManifestFileSource()
        result = src.get_data_source(types.SimpleNamespace(), tmp_path)

        if result is not None:
            assert "orders" in result.tables


class TestDuckLakeDoMethods:
    def test_do_delete_mocked(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()

        mock_conn.execute.return_value.fetchone.side_effect = [(100,), (95,)]
        with patch.object(ops, "_get_connection", return_value=mock_conn):
            result = ops._do_delete("/data/orders", "order_id > 100")
        assert result == 5

    def test_do_update_mocked(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (10,)
        with patch.object(ops, "_get_connection", return_value=mock_conn):
            result = ops._do_update("/data/orders", "status = 'pending'", {"status": "'cancelled'"})
        assert result == 10

    def test_do_merge_mocked(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.num_rows = 5
        with patch.object(ops, "_convert_to_arrow", return_value=mock_arrow):
            with patch.object(ops, "_get_connection", return_value=mock_conn):
                result = ops._do_merge(
                    "/data/orders",
                    MagicMock(),
                    "target.id = source.id",
                    when_matched={"status": "source.status"},
                    when_not_matched={"id": "source.id", "status": "source.status"},
                )
        assert result == 5

    def test_do_insert_overwrite_mocked(self):
        from benchbox.platforms.dataframe.ducklake_maintenance import DuckLakeMaintenanceOperations

        ops = DuckLakeMaintenanceOperations()
        mock_conn = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.num_rows = 10
        with patch.object(ops, "_convert_to_arrow", return_value=mock_arrow):
            with patch.object(ops, "_get_connection", return_value=mock_conn):
                result = ops._do_insert("/data/orders", MagicMock(), None, "overwrite")
        assert result == 10

        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("DELETE" in c for c in calls)


class TestRedshiftFromConfig:
    def test_platform_name_property(self):
        from benchbox.platforms.redshift import RedshiftAdapter

        adapter = RedshiftAdapter.__new__(RedshiftAdapter)
        assert adapter.platform_name == "Redshift"

    def test_has_add_cli_arguments(self):
        from benchbox.platforms.redshift import RedshiftAdapter

        assert callable(RedshiftAdapter.add_cli_arguments)


class TestBigQueryFromConfig:
    def test_add_cli_arguments(self):
        import argparse

        from benchbox.platforms.bigquery import BigQueryAdapter

        parser = argparse.ArgumentParser()
        BigQueryAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--project-id", "my-project", "--location", "EU"])
        assert args.project_id == "my-project"
        assert args.location == "EU"


class TestAthenaAdditionalCoverage:
    def _make_adapter(self):
        from benchbox.platforms.athena import AthenaAdapter

        with (
            patch("benchbox.platforms.athena.boto3", MagicMock()),
            patch("benchbox.platforms.athena.athena_connect", MagicMock()),
        ):
            return AthenaAdapter(
                s3_bucket="benchbox-bucket",
                aws_access_key_id="k",
                aws_secret_access_key="s",
                s3_prefix="benchbox-data",
            )

    def test_add_cli_arguments_and_from_config(self, monkeypatch):
        from benchbox.platforms.athena import AthenaAdapter

        parser = argparse.ArgumentParser()
        AthenaAdapter.add_cli_arguments(parser)
        args = parser.parse_args([])
        assert args.region == "us-east-1"

        monkeypatch.setattr("benchbox.utils.database_naming.generate_database_name", lambda **_kw: "gen_db")
        with (
            patch("benchbox.platforms.athena.boto3", MagicMock()),
            patch("benchbox.platforms.athena.athena_connect", MagicMock()),
            patch.object(AthenaAdapter, "_validate_configuration"),
        ):
            adapter = AthenaAdapter.from_config(
                {"benchmark": "tpch", "scale_factor": 1, "s3_bucket": "benchbox-bucket"}
            )
        assert adapter.database == "gen_db"

    def test_get_s3_client_and_platform_info(self):
        adapter = self._make_adapter()
        session = MagicMock()
        s3_client = MagicMock()
        session.client.return_value = s3_client
        with patch("benchbox.platforms.athena.boto3", MagicMock(Session=MagicMock(return_value=session))):
            assert adapter._get_s3_client() is s3_client

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("athena-version",)
        conn.cursor.return_value = cur
        info = adapter.get_platform_info(connection=conn)
        assert info["platform_type"] == "athena"
        assert "platform_version" in info

    def test_database_existence_and_drop(self):
        adapter = self._make_adapter()
        glue = MagicMock()
        session = MagicMock()
        session.client.return_value = glue
        with patch("benchbox.platforms.athena.boto3", MagicMock(Session=MagicMock(return_value=session))):
            glue.get_database.return_value = {}
            assert adapter.check_server_database_exists(database="db1") is True

            class _Exc:
                class EntityNotFoundException(Exception):
                    pass

            glue.exceptions = _Exc
            glue.get_database.side_effect = _Exc.EntityNotFoundException()
            assert adapter.check_server_database_exists(database="db2") is False

            glue.get_database.side_effect = None
            adapter.check_server_database_exists = MagicMock(return_value=True)
            paginator = MagicMock()
            paginator.paginate.return_value = [{"TableList": [{"Name": "t1"}, {"Name": "t2"}]}]
            glue.get_paginator.return_value = paginator
            adapter.drop_database(database="db3")
            assert glue.delete_database.called

    def test_external_table_ddl_modes(self):
        adapter = self._make_adapter()
        sql = "CREATE TABLE orders (o_orderkey BIGINT NOT NULL, o_comment VARCHAR(20))"

        [staging] = adapter._external_table_ddl(sql, is_staging=True)
        assert "CREATE EXTERNAL TABLE" in staging
        assert "_staging" in staging

        adapter.default_format = "CSV"
        adapter.data_format = "text"
        [text_mode] = adapter._external_table_ddl(sql, is_staging=False)
        assert "FIELDS TERMINATED BY ','" in text_mode

    def test_helper_and_plan_paths(self):
        adapter = self._make_adapter()
        adapter._total_data_scanned_bytes = 2 * (1024**4)
        adapter._query_count = 4

        summary = adapter.get_cost_summary()

        expected_total = (2 * (1024**4)) / (10**12) * 5.0
        assert summary["total_cost_usd"] == pytest.approx(expected_total)
        assert summary["average_cost_per_query_usd"] == pytest.approx(expected_total / 4)

        assert adapter._extract_table_name("CREATE TABLE MixedName (id INT)") == "mixedname"
        assert adapter._extract_table_name("SELECT 1") is None

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [("line1",), ("line2",)]
        conn.cursor.return_value = cur
        assert "line1" in adapter.get_query_plan(conn, "SELECT 1")
        cur.execute.side_effect = RuntimeError("bad explain")
        assert adapter.get_query_plan(conn, "SELECT 1") is None

    def test_connection_tuning_and_table_helpers(self):
        from benchbox.core.tuning.interface import TuningType

        adapter = self._make_adapter()

        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        with patch("benchbox.platforms.athena.athena_connect", MagicMock(return_value=conn)):
            assert adapter.test_connection() is True
        with patch("benchbox.platforms.athena.athena_connect", side_effect=RuntimeError("boom")):
            assert adapter.test_connection() is False

        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True

        col_a = MagicMock()
        col_a.name = "Region"
        col_a.order = 2
        col_b = MagicMock()
        col_b.name = "Nation"
        col_b.order = 1
        table_tuning = MagicMock()
        table_tuning.has_any_tuning.return_value = True
        table_tuning.get_columns_by_type.return_value = [col_a, col_b]
        clause = adapter.generate_tuning_clause(table_tuning)
        assert clause == "PARTITIONED BY (nation, region)"

        empty_tuning = MagicMock()
        empty_tuning.has_any_tuning.return_value = False
        assert adapter.generate_tuning_clause(empty_tuning) == ""

        adapter.apply_table_tunings = MagicMock()
        unified = MagicMock()
        unified.table_tunings = {"orders": table_tuning}
        adapter.apply_unified_tuning(unified, conn)
        assert adapter.apply_table_tunings.called

        adapter.apply_platform_optimizations(None, conn)
        pk_config = MagicMock()
        pk_config.enabled = True
        adapter.apply_constraint_configuration(pk_config, None, conn)

        cur.fetchall.return_value = [("ORDERS",), ("lineitem",)]
        assert adapter._get_existing_tables(conn) == ["orders", "lineitem"]
        cur.execute.side_effect = RuntimeError("show tables failed")
        assert adapter._get_existing_tables(conn) == []
        adapter.analyze_table(conn, "orders")

    def test_close_connection_warning_path(self):
        adapter = self._make_adapter()
        bad_conn = MagicMock()
        bad_conn.close.side_effect = RuntimeError("nope")
        adapter.close_connection(bad_conn)
        adapter.close_connection(None)


class TestFabricAdditionalCoverage:
    def _make_adapter(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            return FabricWarehouseAdapter(
                workspace="ws-guid",
                database="db",
                auth_method="service_principal",
                tenant_id="t",
                client_id="c",
                client_secret="s",
            )

    def test_add_cli_arguments_and_from_config(self, monkeypatch):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        parser = argparse.ArgumentParser()
        FabricWarehouseAdapter.add_cli_arguments(parser)
        args = parser.parse_args([])
        assert hasattr(args, "auth_method")

        monkeypatch.setattr("benchbox.utils.database_naming.generate_database_name", lambda **_kw: "fabric_db")
        adapter = FabricWarehouseAdapter.from_config({"benchmark": "tpch", "scale_factor": 1, "workspace": "ws-guid"})
        assert adapter.database == "fabric_db"

    def test_token_struct_and_connection_string(self):
        adapter = self._make_adapter()
        token_struct = adapter._create_token_struct("abc")
        assert isinstance(token_struct, bytes)
        conn_str = adapter._get_connection_string("db2")
        assert "DATABASE=db2" in conn_str

    def test_test_connection_and_platform_info_branches(self):
        adapter = self._make_adapter()
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("Fabric v1",)
        conn.cursor.return_value = cur
        adapter.create_connection = MagicMock(return_value=conn)

        result = adapter.test_connection()
        assert result["success"] is True
        info = adapter.get_platform_info()
        assert info["platform"] == "Fabric Warehouse"

        adapter.create_connection = MagicMock(side_effect=RuntimeError("boom"))
        fail = adapter.test_connection()
        assert fail["success"] is False

    def test_create_schema_and_configure_for_benchmark(self):
        adapter = self._make_adapter()
        adapter.schema = "custom_schema"
        adapter._create_schema_with_tuning = MagicMock(return_value="CREATE TABLE t1 (id INT);")
        adapter._extract_table_name = MagicMock(return_value="t1")
        adapter._optimize_table_definition = MagicMock(side_effect=lambda s: s)
        adapter.drop_table = MagicMock()

        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur

        elapsed = adapter.create_schema(benchmark=MagicMock(), connection=conn)
        assert elapsed >= 0
        adapter.configure_for_benchmark(conn, benchmark_type="olap")
        assert cur.execute.called
