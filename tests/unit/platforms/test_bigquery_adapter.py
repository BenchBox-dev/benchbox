# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.platforms.base import DriverIsolationCapability
from benchbox.platforms.bigquery import BigQueryAdapter
from benchbox.utils.dialect_utils import unqualified_table_names
from benchbox.utils.iceberg_layout import local_iceberg_location

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.cloud_import,
]


@pytest.fixture
def dependencies_available():

    with patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])):
        yield


def _make_real_iceberg_table(table_dir: Path) -> None:
    pytest.importorskip("pyiceberg", reason="iceberg staging tests need pyiceberg")
    pa = pytest.importorskip("pyarrow", reason="iceberg staging tests need pyarrow")
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField, StringType

    catalog = SqlCatalog("bq-test", uri=f"sqlite:///{table_dir.parent}/cat.db", warehouse=str(table_dir.parent))
    try:
        catalog.create_namespace_if_not_exists("ns")
        table = catalog.create_table(
            "ns.t",
            schema=Schema(NestedField(1, "id", LongType()), NestedField(2, "name", StringType())),
            location=local_iceberg_location(table_dir),
            properties={"format-version": "2"},
        )
        table.overwrite(pa.table({"id": [1], "name": ["a"]}))
    finally:
        catalog.engine.dispose()


@pytest.mark.usefixtures("dependencies_available")
class TestBigQueryAdapter:
    def test_initialization_success(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                location="US",
                credentials_path="/path/to/service-account.json",
            )
            assert adapter.platform_name == "BigQuery"
            assert adapter.get_target_dialect() == "bigquery"
            assert adapter.project_id == "test-project"
            assert adapter.dataset_id == "test_dataset"
            assert adapter.location == "US"

    def test_initialization_with_defaults(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
            assert adapter.location == "US"
            assert adapter.maximum_bytes_billed is None
            assert adapter.query_cache is False
            assert adapter.dry_run is False

    def test_external_table_capability_declared(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
            assert adapter.supports_external_tables is True

    def test_initialization_missing_driver(self):
        with (
            patch(
                "benchbox.platforms.bigquery.check_platform_dependencies",
                return_value=(
                    False,
                    [
                        "google-cloud-bigquery",
                        "google-cloud-storage",
                    ],
                ),
            ),
            pytest.raises(ImportError, match="Missing dependencies for bigquery platform"),
        ):
            BigQueryAdapter()

    def test_initialization_missing_required_config(self, dependencies_available):
        from benchbox.core.exceptions import ConfigurationError

        with patch("benchbox.platforms.bigquery.bigquery"):
            with pytest.raises(ConfigurationError, match="BigQuery configuration requires"):
                BigQueryAdapter()

    @patch("benchbox.platforms.bigquery.service_account")
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_admin_client_with_credentials_path(
        self, mock_bigquery, mock_service_account, dependencies_available
    ):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client
        mock_credentials = Mock()
        mock_service_account.Credentials.from_service_account_file.return_value = mock_credentials

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            credentials_path="/path/to/service-account.json",
        )

        client = adapter._create_admin_client()

        assert client == mock_client
        mock_service_account.Credentials.from_service_account_file.assert_called_once_with(
            "/path/to/service-account.json"
        )
        mock_bigquery.Client.assert_called_once_with(
            project="test-project", location="US", credentials=mock_credentials
        )

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_admin_client_with_default_credentials(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client
        mock_credentials = Mock()

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with patch.object(adapter, "_load_credentials", return_value=mock_credentials):
            client = adapter._create_admin_client()

        assert client == mock_client
        mock_bigquery.Client.assert_called_once_with(
            project="test-project", location="US", credentials=mock_credentials
        )

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_check_server_database_exists_true(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_dataset = Mock()
        mock_dataset.dataset_id = "test_dataset"
        mock_client.list_datasets.return_value = [mock_dataset]
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with patch.object(adapter, "_load_credentials", return_value=Mock()):
            exists = adapter.check_server_database_exists()

        assert exists is True
        mock_client.list_datasets.assert_called_once()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_check_server_database_exists_false(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_other_dataset = Mock()
        mock_other_dataset.dataset_id = "other_dataset"
        mock_client.list_datasets.return_value = [mock_other_dataset]
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with patch.object(adapter, "_load_credentials", return_value=Mock()):
            exists = adapter.check_server_database_exists()

        assert exists is False

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_drop_database(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_dataset_ref = Mock()
        mock_client.dataset.return_value = mock_dataset_ref
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with patch.object(adapter, "_load_credentials", return_value=Mock()):
            adapter.drop_database()

        mock_client.dataset.assert_called_once_with("test_dataset")
        mock_client.delete_dataset.assert_called_once_with(mock_dataset_ref, delete_contents=True, not_found_ok=True)

    @patch("benchbox.platforms.bigquery.bigquery")
    @patch("benchbox.platforms.bigquery.storage")
    def test_create_connection_success(self, mock_storage, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_storage_client = Mock()
        mock_bigquery.Client.return_value = mock_client
        mock_storage.Client.return_value = mock_storage_client
        mock_credentials = Mock()

        mock_query_job = Mock()
        mock_query_job.result.return_value = [(1,)]
        mock_client.query.return_value = mock_query_job

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "_load_credentials", return_value=mock_credentials),
        ):
            connection = adapter.create_connection()

        assert connection == mock_client

        mock_client.query.assert_called_once_with("SELECT 1 as test")

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_connection_failure(self, mock_bigquery, dependencies_available):
        mock_credentials = Mock()
        mock_bigquery.Client.side_effect = Exception("Authentication failed")

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "_load_credentials", return_value=mock_credentials),
        ):
            with pytest.raises(Exception, match="Authentication failed"):
                adapter.create_connection()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_schema(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        mock_table = Mock()
        mock_client.create_table.return_value = mock_table

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INT64, name STRING);
            -- Generated staging load tables
            CREATE TABLE orders_stage (id INT64);
            CREATE TABLE table2 (id INT64, data STRING);
        """

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with patch.object(adapter, "translate_sql") as mock_translate:
            mock_translate.return_value = (
                "CREATE TABLE table1 (id INT64, name STRING);\n"
                "-- Generated staging load tables\n"
                "CREATE TABLE orders_stage (id INT64);\n"
                "CREATE TABLE table2 (id INT64, data STRING);"
            )

            schema_time = adapter.create_schema(mock_benchmark, mock_client)

        assert isinstance(schema_time, float)
        assert schema_time >= 0

        query_calls = list(mock_client.query.call_args_list)
        assert len(query_calls) == 3
        executed_ddl = [call.args[0] for call in query_calls]
        assert any("`test-project.test_dataset.TABLE1`" in ddl for ddl in executed_ddl)
        assert any("`test-project.test_dataset.ORDERS_STAGE`" in ddl for ddl in executed_ddl)
        assert any("`test-project.test_dataset.TABLE2`" in ddl for ddl in executed_ddl)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_data_with_csv_upload(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        mock_load_job = Mock()
        mock_load_job.result.return_value = None
        mock_load_job.output_rows = 100
        mock_client.load_table_from_file.return_value = mock_load_job

        mock_query_job = Mock()
        mock_query_job.result.return_value = [(100,)]
        mock_client.query.return_value = mock_query_job

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("id,name\n1,test1\n2,test2\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_client, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert load_time >= 0
            assert "TEST_TABLE" in table_stats
            assert table_stats["TEST_TABLE"] == 100

            mock_client.load_table_from_file.assert_called_once()
            job_config_kwargs = mock_bigquery.LoadJobConfig.call_args.kwargs
            assert job_config_kwargs["source_format"] == mock_bigquery.SourceFormat.CSV
            assert job_config_kwargs["field_delimiter"] == ","

        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_data_with_direct_parquet_upload(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        mock_load_job = Mock()
        mock_load_job.result.return_value = None
        mock_client.load_table_from_file.return_value = mock_load_job

        mock_query_job = Mock()
        mock_query_job.result.return_value = [(100,)]
        mock_client.query.return_value = mock_query_job

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet", delete=False) as f:
            f.write(b"PAR1")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_client, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert table_stats["TEST_TABLE"] == 100

            job_config_kwargs = mock_bigquery.LoadJobConfig.call_args.kwargs
            assert job_config_kwargs["source_format"] == mock_bigquery.SourceFormat.PARQUET
            assert "field_delimiter" not in job_config_kwargs
        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_data_with_compressed_parquet_upload(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        mock_load_job = Mock()
        mock_load_job.result.return_value = None
        mock_client.load_table_from_file.return_value = mock_load_job

        mock_query_job = Mock()
        mock_query_job.result.return_value = [(100,)]
        mock_client.query.return_value = mock_query_job

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet.gz", delete=False) as f:
            f.write(b"\x1f\x8b")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_client, Path("/tmp"))

            assert table_stats["TEST_TABLE"] == 100

            job_config_kwargs = mock_bigquery.LoadJobConfig.call_args.kwargs
            assert job_config_kwargs["source_format"] == mock_bigquery.SourceFormat.PARQUET
            assert "field_delimiter" not in job_config_kwargs
        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_data_with_gcs_parquet_upload(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        mock_load_job = Mock()
        mock_load_job.result.return_value = None
        mock_client.load_table_from_uri.return_value = mock_load_job

        mock_query_job = Mock()
        mock_query_job.result.return_value = [(100,)]
        mock_client.query.return_value = mock_query_job

        mock_benchmark = Mock()
        mock_bucket = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet", delete=False) as f:
            f.write(b"PAR1")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                storage_bucket="benchbox-bucket",
            )

            with patch.object(adapter, "_create_storage_bucket", return_value=mock_bucket):
                table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_client, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert table_stats["TEST_TABLE"] == 100

            job_config_kwargs = mock_bigquery.LoadJobConfig.call_args.kwargs
            assert job_config_kwargs["source_format"] == mock_bigquery.SourceFormat.PARQUET
            assert "field_delimiter" not in job_config_kwargs
            mock_client.load_table_from_uri.assert_called_once()
        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_build_load_job_config_warns_on_unrecognized_format(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with tempfile.NamedTemporaryFile(suffix=".vortex", delete=False) as f:
            temp_path = Path(f.name)

        try:
            mock_inner_logger = Mock(spec=logging.Logger)
            adapter.logger = mock_inner_logger
            adapter._build_load_job_config(
                temp_path,
                write_disposition=mock_bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            mock_inner_logger.warning.assert_called_once()
            warning_msg = mock_inner_logger.warning.call_args[0][0]
            assert "Unrecognized data format" in warning_msg
            assert "falling back to CSV" in warning_msg
            job_config_kwargs = mock_bigquery.LoadJobConfig.call_args.kwargs
            assert job_config_kwargs["source_format"] == mock_bigquery.SourceFormat.CSV
        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_validate_external_table_requirements_requires_bucket(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with pytest.raises(ValueError, match="requires a GCS bucket"):
            adapter.validate_external_table_requirements()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_external_tables_generates_external_table_sql(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )

        mock_connection = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = []
        mock_connection.query.return_value = mock_query_job

        with (
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": [Path("/tmp/lineitem.parquet")]}),
            patch.object(
                adapter,
                "_prepare_external_parquet_uris",
                return_value=["gs://benchbox-bucket/benchbox-data/lineitem/lineitem.parquet"],
            ),
            patch.object(adapter, "_create_storage_bucket", return_value=Mock()),
            patch.object(adapter, "_get_table_row_count", return_value=55),
        ):
            table_stats, load_time, per_table_timings = adapter.create_external_tables(
                benchmark=Mock(),
                connection=mock_connection,
                data_dir=Path("/tmp"),
            )

        assert table_stats == {"LINEITEM": 55}
        assert isinstance(load_time, float)
        assert per_table_timings is None
        query_sql = str(mock_connection.query.call_args[0][0])
        assert "CREATE OR REPLACE EXTERNAL TABLE `test-project.test_dataset.LINEITEM`" in query_sql
        assert "format = 'PARQUET'" in query_sql
        assert "gs://benchbox-bucket/benchbox-data/lineitem/lineitem.parquet" in query_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_external_tables_generates_delta_biglake_sql(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
            biglake_connection="test-project.us.benchbox",
        )

        mock_connection = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = []
        mock_connection.query.return_value = mock_query_job

        with (
            patch.object(
                adapter,
                "_prepare_external_table_uris",
                return_value=("DELTA_LAKE", ["gs://benchbox-bucket/benchbox-data/lineitem/"]),
            ),
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": [Path("/tmp/lineitem")]}),
            patch.object(adapter, "_create_storage_bucket", return_value=Mock()),
            patch.object(adapter, "_get_table_row_count", return_value=66),
        ):
            table_stats, _, _ = adapter.create_external_tables(
                benchmark=Mock(), connection=mock_connection, data_dir=Path("/tmp")
            )

        assert table_stats == {"LINEITEM": 66}
        query_sql = str(mock_connection.query.call_args[0][0])
        assert "WITH CONNECTION `test-project.us.benchbox`" in query_sql
        assert "format = 'DELTA_LAKE'" in query_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_external_tables_generates_iceberg_biglake_sql(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
            biglake_connection="test-project.us.benchbox",
        )

        mock_connection = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = []
        mock_connection.query.return_value = mock_query_job

        with (
            patch.object(
                adapter,
                "_prepare_external_table_uris",
                return_value=("ICEBERG", ["gs://benchbox-bucket/benchbox-data/lineitem/"]),
            ),
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": [Path("/tmp/lineitem")]}),
            patch.object(adapter, "_create_storage_bucket", return_value=Mock()),
            patch.object(adapter, "_get_table_row_count", return_value=77),
        ):
            table_stats, _, _ = adapter.create_external_tables(
                benchmark=Mock(), connection=mock_connection, data_dir=Path("/tmp")
            )

        assert table_stats == {"LINEITEM": 77}
        query_sql = str(mock_connection.query.call_args[0][0])
        assert "WITH CONNECTION `test-project.us.benchbox`" in query_sql
        assert "format = 'ICEBERG'" in query_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_external_tables_iceberg_requires_biglake_connection(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            iceberg_dir = Path(tmpdir) / "lineitem"
            (iceberg_dir / "metadata").mkdir(parents=True)
            (iceberg_dir / "metadata" / "v1.metadata.json").write_text("{}")

            with pytest.raises(ValueError, match="biglake_connection"):
                adapter._prepare_external_table_uris(Mock(), "lineitem", [iceberg_dir])

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_prepare_iceberg_uris_points_at_metadata_file(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
            biglake_connection="test-project.us.benchbox",
        )

        mock_bucket = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            iceberg_dir = Path(tmpdir) / "lineitem"
            _make_real_iceberg_table(iceberg_dir)

            uris = adapter._prepare_external_iceberg_uris(mock_bucket, "lineitem", [iceberg_dir])

        assert len(uris) == 1
        assert uris[0].startswith("gs://benchbox-bucket/benchbox-data/lineitem/metadata/")
        assert uris[0].endswith(".metadata.json")
        uploaded = {call.args[0] for call in mock_bucket.blob.call_args_list}
        assert any(name.endswith(".parquet") for name in uploaded)
        assert any(name.endswith(".metadata.json") for name in uploaded)
        assert any(name.endswith(".avro") for name in uploaded)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_prepare_iceberg_uris_accepts_cloud_metadata_file(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
            biglake_connection="test-project.us.benchbox",
        )

        metadata_uri = "gs://other-bucket/table/metadata/00003-ccc.metadata.json"
        uris = adapter._prepare_external_iceberg_uris(Mock(), "lineitem", [metadata_uri])
        assert uris == [metadata_uri]

        uris = adapter._prepare_external_iceberg_uris(Mock(), "lineitem", ["gs://other-bucket/table/"])
        assert uris == []

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_prepare_iceberg_uris_rejects_metadata_dir_without_file(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
            biglake_connection="test-project.us.benchbox",
        )

        with pytest.raises(ValueError, match=r"\*\.metadata\.json file URI"):
            adapter._prepare_external_iceberg_uris(Mock(), "lineitem", ["gs://other-bucket/table/metadata/"])

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_create_external_tables_delta_requires_biglake_connection(self, mock_bigquery, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )

        with (
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": [Path("/tmp/lineitem")]}),
            patch.object(adapter, "_create_storage_bucket", return_value=Mock()),
            patch.object(
                adapter,
                "_prepare_external_delta_uris",
                return_value=["gs://benchbox-bucket/benchbox-data/lineitem/"],
            ),
            pytest.raises(ValueError, match="requires --platform-option biglake_connection"),
        ):
            adapter.create_external_tables(benchmark=Mock(), connection=Mock(), data_dir=Path("/tmp"))

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_configure_for_benchmark_olap(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        adapter.configure_for_benchmark(mock_client, "olap")
        assert mock_client._default_job_config is not None
        assert mock_client._default_job_config.use_legacy_sql is False

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_dry_run_disable_resets_cached_connection_job_config(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_job_config = Mock()
        mock_job_config.dry_run = False
        mock_bigquery.QueryJobConfig.return_value = mock_job_config
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        adapter.enable_dry_run()
        adapter.configure_for_benchmark(mock_client, "olap")
        assert mock_job_config.dry_run is True

        adapter.disable_dry_run()
        assert adapter.dry_run is False
        assert mock_job_config.dry_run is False

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_execute_query_success(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = [(1, "test"), (2, "test2")]
        mock_query_job.job_id = "job_123"
        mock_query_job.total_bytes_processed = 1024
        mock_query_job.total_bytes_billed = 512
        mock_query_job.slot_millis = 1000
        mock_client.query.return_value = mock_query_job
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        result = adapter.execute_query(mock_client, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

        assert result["job_id"] == "job_123"
        assert result["job_statistics"]["bytes_processed"] == 1024
        assert result["job_statistics"]["bytes_billed"] == 512
        assert result["job_statistics"]["slot_ms"] == 1000

        mock_client.query.assert_called_once()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_execute_query_with_job_config(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = [(1, "test")]
        mock_query_job.job_id = "job_123"
        mock_query_job.total_bytes_processed = 1024
        mock_query_job.total_bytes_billed = 512
        mock_query_job.slot_millis = 1000
        mock_query_job.created = None
        mock_query_job.started = None
        mock_query_job.ended = None
        mock_client.query.return_value = mock_query_job
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            maximum_bytes_billed=1000000,
        )

        result = adapter.execute_query(mock_client, "SELECT * FROM test", "q1")

        assert result["status"] == "SUCCESS"
        assert result["job_statistics"]["bytes_processed"] == 1024

        mock_client.query.assert_called_once()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_execute_query_failure(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_client.query.side_effect = Exception("Query failed")
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        result = adapter.execute_query(mock_client, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"
        assert isinstance(result["execution_time_seconds"], float)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_get_platform_metadata(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_client.project = "test-project"
        mock_client.location = "US"

        mock_query_job = Mock()
        mock_query_job.result.return_value = [("test_dataset", "US", "2024-01-01 00:00:00", "BenchBox test dataset")]
        mock_client.query.return_value = mock_query_job

        mock_tables = [Mock(), Mock()]
        mock_tables[0].table_id = "table1"
        mock_tables[0].num_rows = 1000
        mock_tables[0].num_bytes = 1024000
        mock_tables[1].table_id = "table2"
        mock_tables[1].num_rows = 2000
        mock_tables[1].num_bytes = 2048000
        mock_client.list_tables.return_value = mock_tables

        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        metadata = adapter._get_platform_metadata(mock_client)

        assert metadata["platform"] == "BigQuery"
        assert metadata["project_id"] == "test-project"
        assert metadata["dataset_id"] == "test_dataset"
        assert metadata["location"] == "US"
        assert "dataset_info" in metadata
        assert "tables" in metadata
        assert len(metadata["tables"]) == 2

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_close_connection(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        adapter.close_connection(mock_client)
        mock_client.close.assert_called_once()

    def test_supports_tuning_type(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
                mock_tuning_type.PARTITIONING = "partitioning"
                mock_tuning_type.CLUSTERING = "clustering"
                mock_tuning_type.SORTING = "sorting"

                assert adapter.supports_tuning_type(mock_tuning_type.PARTITIONING) is True
                assert adapter.supports_tuning_type(mock_tuning_type.CLUSTERING) is True
                assert adapter.supports_tuning_type(mock_tuning_type.SORTING) is False

    def test_generate_tuning_clause_with_partitioning(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            mock_tuning = Mock()
            mock_tuning.has_any_tuning.return_value = True

            mock_column = Mock()
            mock_column.name = "partition_date"
            mock_column.order = 1
            mock_column.type = "DATE"

            with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
                mock_tuning_type.PARTITIONING = "partitioning"
                mock_tuning_type.CLUSTERING = "clustering"

                def mock_get_columns_by_type(tuning_type):
                    if tuning_type == mock_tuning_type.PARTITIONING:
                        return [mock_column]
                    return []

                mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

                clause = adapter.generate_tuning_clause(mock_tuning)

                assert "PARTITION BY partition_date" in clause

    def test_generate_tuning_clause_with_clustering(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            mock_tuning = Mock()
            mock_tuning.has_any_tuning.return_value = True

            mock_column1 = Mock()
            mock_column1.name = "cluster_key1"
            mock_column1.order = 1
            mock_column2 = Mock()
            mock_column2.name = "cluster_key2"
            mock_column2.order = 2

            with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
                mock_tuning_type.PARTITIONING = "partitioning"
                mock_tuning_type.CLUSTERING = "clustering"

                def mock_get_columns_by_type(tuning_type):
                    if tuning_type == mock_tuning_type.CLUSTERING:
                        return [mock_column1, mock_column2]
                    return []

                mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

                clause = adapter.generate_tuning_clause(mock_tuning)

                assert "CLUSTER BY cluster_key1, cluster_key2" in clause

    def test_generate_tuning_clause_none(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            clause = adapter.generate_tuning_clause(None)
            assert clause == ""

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_apply_table_tunings_with_partitioning(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        mock_tuning = Mock()
        mock_tuning.table_name = "test_table"
        mock_tuning.has_any_tuning.return_value = True

        mock_column = Mock()
        mock_column.name = "partition_date"
        mock_column.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.CLUSTERING = "clustering"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.DISTRIBUTION = "distribution"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.PARTITIONING:
                    return [mock_column]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            adapter.apply_table_tunings(mock_tuning, mock_client)
            mock_client.get_table.assert_called_once()

    def test_apply_unified_tuning(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            mock_client = Mock()
            mock_unified_config = Mock()
            mock_unified_config.primary_keys = Mock()
            mock_unified_config.foreign_keys = Mock()
            mock_unified_config.platform_optimizations = Mock()
            mock_unified_config.table_tunings = {}

            with (
                patch.object(adapter, "apply_constraint_configuration") as mock_constraint,
                patch.object(adapter, "apply_platform_optimizations") as mock_platform,
            ):
                adapter.apply_unified_tuning(mock_unified_config, mock_client)
                mock_constraint.assert_called_once()
                mock_platform.assert_called_once()

    def test_apply_constraint_configuration(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            mock_client = Mock()
            mock_primary_key_config = Mock()
            mock_primary_key_config.enabled = True
            mock_foreign_key_config = Mock()
            mock_foreign_key_config.enabled = False

            with patch.object(adapter.logger, "info") as mock_info:
                adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, mock_client)
                mock_info.assert_called_once()
                assert "Primary key" in mock_info.call_args[0][0]

    def test_cost_control_features(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                maximum_bytes_billed=1000000,
                query_cache=True,
                dry_run=True,
            )

            assert adapter.maximum_bytes_billed == 1000000
            assert adapter.query_cache is True
            assert adapter.dry_run is True

    def test_authentication_methods(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter1 = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                credentials_path="/path/to/key.json",
            )
            assert adapter1.credentials_path == "/path/to/key.json"

            adapter2 = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
            assert adapter2.credentials_path is None

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_job_configuration_options(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = [(1,)]
        mock_client.query.return_value = mock_query_job
        mock_bigquery.Client.return_value = mock_client

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            maximum_bytes_billed=1000000,
            use_legacy_sql=False,
            job_timeout_ms=600000,
        )

        result = adapter.execute_query(mock_client, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"

        mock_client.query.assert_called_once()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_data_location_and_regional_settings(self, mock_bigquery, dependencies_available):
        mock_client = Mock()
        mock_bigquery.Client.return_value = mock_client

        adapter_us = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset", location="US")
        assert adapter_us.location == "US"

        adapter_eu = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset", location="EU")
        assert adapter_eu.location == "EU"

        adapter_asia = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            location="asia-northeast1",
        )
        assert adapter_asia.location == "asia-northeast1"

    def test_file_format_detection_for_chunked_files(self, dependencies_available):
        from pathlib import Path

        test_cases = [
            (Path("customer.tbl"), True, "Simple .tbl file"),
            (Path("customer.tbl.1"), True, "Chunked .tbl file"),
            (Path("lineitem.tbl.10"), True, "Chunked .tbl file (2 digits)"),
            (Path("orders.tbl.gz"), True, "Compressed .tbl file"),
            (Path("part.tbl.1.gz"), True, "Chunked compressed .tbl file"),
            (Path("nation.tbl.zst"), True, "zstd compressed .tbl file"),
            (Path("partsupp.dat"), True, "TPC-DS .dat file"),
            (Path("partsupp.dat.1"), True, "Chunked TPC-DS .dat file"),
            (Path("region.csv"), False, "CSV file"),
            (Path("supplier.csv.gz"), False, "Compressed CSV file"),
        ]

        for file_path, should_be_tbl, description in test_cases:
            file_str = str(file_path.name)
            is_tbl = ".tbl" in file_str or ".dat" in file_str
            assert is_tbl == should_be_tbl, f"Failed for {description}: {file_path.name}"

    def test_validate_data_integrity_with_client_api(self, dependencies_available):
        from unittest.mock import Mock

        mock_client = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = [(1,)]
        mock_client.query.return_value = mock_query_job

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        mock_benchmark = Mock()

        table_stats = {
            "customer": 150,
            "lineitem": 6005,
            "nation": 25,
            "region": 5,
        }

        status, details = adapter._validate_data_integrity(mock_benchmark, mock_client, table_stats)

        assert mock_client.query.called, "Should use client.query() not client.cursor()"
        assert not hasattr(mock_client, "cursor") or not mock_client.cursor.called

        assert status == "PASSED"
        assert "accessible_tables" in details
        assert len(details["accessible_tables"]) == 4

    def test_validate_data_integrity_handles_inaccessible_tables(self, dependencies_available):
        from unittest.mock import Mock

        mock_client = Mock()

        def query_side_effect(sql):
            mock_job = Mock()
            if "CUSTOMER" in sql or "LINEITEM" in sql:
                mock_job.result.side_effect = Exception("404 Table not found")
            else:
                mock_job.result.return_value = [(1,)]
            return mock_job

        mock_client.query.side_effect = query_side_effect

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        mock_benchmark = Mock()

        table_stats = {"customer": 0, "lineitem": 0, "nation": 25, "region": 5}

        status, details = adapter._validate_data_integrity(mock_benchmark, mock_client, table_stats)

        assert status == "FAILED"
        assert "inaccessible_tables" in details
        assert "customer" in details["inaccessible_tables"]
        assert "lineitem" in details["inaccessible_tables"]

    def test_get_table_row_count_uses_query_api(self, dependencies_available):
        from unittest.mock import Mock

        mock_client = Mock()
        mock_query_job = Mock()
        mock_query_job.result.return_value = [(150,)]
        mock_client.query.return_value = mock_query_job

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        row_count = adapter.get_table_row_count(mock_client, "customer")

        assert mock_client.query.called, "Should use client.query() not client.cursor()"
        assert not hasattr(mock_client, "cursor") or not mock_client.cursor.called

        assert row_count == 150

    def test_get_table_row_count_handles_errors(self, dependencies_available):
        from unittest.mock import Mock

        mock_client = Mock()
        mock_client.query.side_effect = Exception("Table not found")

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        row_count = adapter.get_table_row_count(mock_client, "nonexistent_table")
        assert row_count == 0

    def test_storage_client_not_created_in_connection(self, dependencies_available):
        from unittest.mock import Mock, patch

        with (
            patch("benchbox.platforms.bigquery.bigquery") as mock_bigquery,
            patch("benchbox.platforms.bigquery.storage") as mock_storage,
        ):
            mock_client = Mock(spec=["query", "close"])
            mock_bigquery.Client.return_value = mock_client

            mock_query_job = Mock()
            mock_query_job.result.return_value = []
            mock_client.query.return_value = mock_query_job

            adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

            with patch.object(adapter, "_load_credentials", return_value=Mock()):
                adapter.create_connection()

            assert not mock_storage.Client.called, "Storage client should not be created in create_connection"

    def test_close_connection_handles_credential_errors(self, dependencies_available):
        from unittest.mock import Mock

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        mock_client = Mock()
        mock_client.close.side_effect = Exception("Anonymous credentials cannot be refreshed")

        adapter.close_connection(mock_client)

        assert mock_client.close.called

    def test_close_connection_warns_on_other_errors(self, dependencies_available):
        from unittest.mock import Mock

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        mock_client = Mock()
        mock_client.close.side_effect = Exception("Network timeout")

        with patch.object(adapter.logger, "warning") as mock_warning:
            adapter.close_connection(mock_client)
            assert mock_warning.called

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_direct_loading_with_chunked_files(self, mock_bigquery, dependencies_available):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, Mock

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"

        job_configs = []

        def create_job_config(**kwargs):
            config = Mock()
            for k, v in kwargs.items():
                setattr(config, k, v)
            job_configs.append(config)
            return config

        mock_bigquery.LoadJobConfig = create_job_config

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            chunk1 = tmpdir / "customer_1.dat"
            chunk2 = tmpdir / "customer_2.dat"
            chunk1.write_text("1|John|100\n2|Jane|200\n")
            chunk2.write_text("3|Bob|300\n4|Alice|400\n")

            single = tmpdir / "nation.dat"
            single.write_text("1|USA\n2|Canada\n")

            mock_benchmark = Mock()
            mock_benchmark.tables = {
                "customer": [chunk1, chunk2],
                "nation": [single],
            }

            mock_connection = MagicMock()
            mock_dataset = Mock()
            mock_table_ref = Mock()
            mock_connection.dataset.return_value = mock_dataset
            mock_dataset.table.return_value = mock_table_ref

            mock_load_job = Mock()
            mock_load_job.result.return_value = None
            mock_connection.load_table_from_file.return_value = mock_load_job

            mock_query_job = Mock()
            mock_connection.query.return_value = mock_query_job

            def mock_result():
                if not hasattr(mock_result, "call_count"):
                    mock_result.call_count = 0
                mock_result.call_count += 1
                if mock_result.call_count == 1:
                    return [(4,)]
                else:
                    return [(2,)]

            mock_query_job.result.side_effect = [mock_result(), mock_result()]

            table_stats, total_time, per_table_timings = adapter.load_data(mock_benchmark, mock_connection, tmpdir)

            assert "CUSTOMER" in table_stats
            assert "NATION" in table_stats

            assert set(per_table_timings) == set(table_stats)
            assert per_table_timings["CUSTOMER"]["total_ms"] >= 0
            assert per_table_timings["NATION"]["total_ms"] >= 0

            assert mock_connection.load_table_from_file.call_count == 3

            assert job_configs[0].write_disposition == "WRITE_TRUNCATE"
            assert job_configs[1].write_disposition == "WRITE_APPEND"
            assert job_configs[2].write_disposition == "WRITE_TRUNCATE"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_direct_loading_with_single_paths(self, mock_bigquery, dependencies_available):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, Mock

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"

        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            test_file = tmpdir / "nation.dat"
            test_file.write_text("1|USA\n2|Canada\n")

            mock_benchmark = Mock()
            mock_benchmark.tables = {
                "nation": test_file,
            }

            mock_connection = MagicMock()
            mock_dataset = Mock()
            mock_table_ref = Mock()
            mock_connection.dataset.return_value = mock_dataset
            mock_dataset.table.return_value = mock_table_ref

            mock_load_job = Mock()
            mock_load_job.result.return_value = None
            mock_connection.load_table_from_file.return_value = mock_load_job

            mock_query_job = Mock()
            mock_query_job.result.return_value = [(2,)]
            mock_connection.query.return_value = mock_query_job

            table_stats, total_time, per_table_timings = adapter.load_data(mock_benchmark, mock_connection, tmpdir)

            assert "NATION" in table_stats
            assert table_stats["NATION"] == 2

            assert mock_connection.load_table_from_file.call_count == 1

            assert set(per_table_timings) == set(table_stats)
            assert per_table_timings["NATION"]["total_ms"] >= 0

    def test_validate_database_compatibility_detects_empty_tables(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
            )

            mock_client = Mock()
            mock_dataset_ref = Mock()
            mock_client.dataset.return_value = mock_dataset_ref

            mock_table_info1 = Mock()
            mock_table_info1.reference = Mock()
            mock_table_info2 = Mock()
            mock_table_info2.reference = Mock()
            mock_table_info3 = Mock()
            mock_table_info3.reference = Mock()
            mock_table_info4 = Mock()
            mock_table_info4.reference = Mock()

            mock_client.list_tables.return_value = [
                mock_table_info1,
                mock_table_info2,
                mock_table_info3,
                mock_table_info4,
            ]

            mock_table1 = Mock()
            mock_table1.num_rows = 0
            mock_table1.table_id = "table1"

            mock_table2 = Mock()
            mock_table2.num_rows = 0
            mock_table2.table_id = "table2"

            mock_table3 = Mock()
            mock_table3.num_rows = 0
            mock_table3.table_id = "table3"

            mock_table4 = Mock()
            mock_table4.num_rows = 100
            mock_table4.table_id = "table4"

            mock_client.get_table.side_effect = [mock_table1, mock_table2, mock_table3, mock_table4]

            with patch.object(adapter, "_create_admin_client", return_value=mock_client):
                mock_base_result = Mock()
                mock_base_result.is_valid = True
                mock_base_result.can_reuse = True
                mock_base_result.issues = []
                mock_base_result.warnings = []

                with patch(
                    "benchbox.platforms.base.validation.DatabaseValidator.validate",
                    return_value=mock_base_result,
                ):
                    result = adapter._validate_database_compatibility()

                    assert result.is_valid is False
                    assert result.can_reuse is False
                    assert len(result.issues) == 1
                    assert "Empty tables detected: 3/4" in result.issues[0]
                    assert "indicates failed previous load" in result.issues[0]

    def test_validate_database_compatibility_allows_few_empty_tables(self, dependencies_available):
        with patch("benchbox.platforms.bigquery.bigquery"):
            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
            )

            mock_client = Mock()
            mock_dataset_ref = Mock()
            mock_client.dataset.return_value = mock_dataset_ref

            mock_table_info1 = Mock()
            mock_table_info1.reference = Mock()
            mock_table_info2 = Mock()
            mock_table_info2.reference = Mock()
            mock_table_info3 = Mock()
            mock_table_info3.reference = Mock()
            mock_table_info4 = Mock()
            mock_table_info4.reference = Mock()

            mock_client.list_tables.return_value = [
                mock_table_info1,
                mock_table_info2,
                mock_table_info3,
                mock_table_info4,
            ]

            mock_table1 = Mock()
            mock_table1.num_rows = 0
            mock_table1.table_id = "table1"

            mock_table2 = Mock()
            mock_table2.num_rows = 100
            mock_table2.table_id = "table2"

            mock_table3 = Mock()
            mock_table3.num_rows = 100
            mock_table3.table_id = "table3"

            mock_table4 = Mock()
            mock_table4.num_rows = 100
            mock_table4.table_id = "table4"

            mock_client.get_table.side_effect = [mock_table1, mock_table2, mock_table3, mock_table4]

            with patch.object(adapter, "_create_admin_client", return_value=mock_client):
                mock_base_result = Mock()
                mock_base_result.is_valid = True
                mock_base_result.can_reuse = True
                mock_base_result.issues = []
                mock_base_result.warnings = []

                with patch(
                    "benchbox.platforms.base.validation.DatabaseValidator.validate",
                    return_value=mock_base_result,
                ):
                    result = adapter._validate_database_compatibility()

                    assert result.is_valid is True
                    assert result.can_reuse is True
                    assert len(result.issues) == 0


class TestBigQueryCaseNormalization:
    def test_normalize_single_table(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        result = adapter._normalize_table_names_case("SELECT * FROM `customer`")
        assert "`CUSTOMER`" in result
        assert "`customer`" not in result

    def test_normalize_multiple_tables(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        query = "SELECT * FROM `customer` JOIN `store_sales` ON `customer`.c_id = `store_sales`.c_id"
        result = adapter._normalize_table_names_case(query)
        assert "`CUSTOMER`" in result
        assert "`STORE_SALES`" in result
        assert "`customer`" not in result
        assert "`store_sales`" not in result

    def test_normalize_table_with_underscores(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        result = adapter._normalize_table_names_case("SELECT * FROM `date_dim` WHERE `date_dim`.d_year = 2000")
        assert "`DATE_DIM`" in result
        assert "`date_dim`" not in result

    def test_preserves_uppercase_tables(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        result = adapter._normalize_table_names_case("SELECT * FROM `CUSTOMER`")
        assert "`CUSTOMER`" in result

    def test_ignores_string_literals(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        result = adapter._normalize_table_names_case("SELECT * FROM `customer` WHERE name = 'customer_name'")
        assert "`CUSTOMER`" in result
        assert "'customer_name'" in result

    def test_handles_empty_query(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        assert adapter._normalize_table_names_case("") == ""

    def test_handles_query_without_backticks(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        query = "SELECT * FROM CUSTOMER WHERE id = 1"
        assert adapter._normalize_table_names_case(query) == query

    def test_complex_query_with_joins_and_subqueries(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")
        query = """
        SELECT c.c_id, ss.ss_sales_price
        FROM `customer` c
        JOIN (
            SELECT * FROM `store_sales`
            WHERE ss_sold_date_sk IN (SELECT d_date_sk FROM `date_dim`)
        ) ss ON c.c_customer_sk = ss.ss_customer_sk
        """
        result = adapter._normalize_table_names_case(query)
        assert "`CUSTOMER`" in result
        assert "`STORE_SALES`" in result
        assert "`DATE_DIM`" in result


class TestBigQueryStagingRoot:
    def test_staging_root_parsing_simple(self):
        adapter = BigQueryAdapter(
            project_id="test-project", dataset_id="test_dataset", staging_root="gs://test-bucket/"
        )
        assert adapter.storage_bucket == "test-bucket"
        assert adapter.storage_prefix == "benchbox-data"

    def test_staging_root_parsing_with_path(self):
        adapter = BigQueryAdapter(
            project_id="test-project", dataset_id="test_dataset", staging_root="gs://test-bucket/custom/path/"
        )
        assert adapter.storage_bucket == "test-bucket"
        assert adapter.storage_prefix == "custom/path"

    def test_staging_root_parsing_no_trailing_slash(self):
        adapter = BigQueryAdapter(
            project_id="test-project", dataset_id="test_dataset", staging_root="gs://test-bucket/data"
        )
        assert adapter.storage_bucket == "test-bucket"
        assert adapter.storage_prefix == "data"

    def test_staging_root_overrides_explicit_bucket(self):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="old-bucket",
            storage_prefix="old-prefix",
            staging_root="gs://new-bucket/new-prefix",
        )
        assert adapter.storage_bucket == "new-bucket"
        assert adapter.storage_prefix == "new-prefix"

    def test_no_staging_root_uses_explicit_config(self):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="explicit-bucket",
            storage_prefix="explicit-prefix",
        )
        assert adapter.storage_bucket == "explicit-bucket"
        assert adapter.storage_prefix == "explicit-prefix"

    def test_staging_root_invalid_provider_raises_error(self):
        with pytest.raises(ValueError, match="BigQuery requires GCS"):
            BigQueryAdapter(project_id="test-project", dataset_id="test_dataset", staging_root="s3://aws-bucket/path")

    def test_staging_root_empty_bucket(self):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset", staging_root="gs://b")
        assert adapter.storage_bucket == "b"
        assert adapter.storage_prefix == "benchbox-data"


@pytest.mark.usefixtures("dependencies_available")
class TestBigQuerySqlGenerationHelpers:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_configure_for_benchmark_sets_default_dataset_and_olap_flags(self, mock_bigquery, dependencies_available):
        mock_connection = Mock()
        mock_job_config = Mock()
        mock_bigquery.QueryJobConfig.return_value = mock_job_config
        mock_bigquery.QueryPriority.INTERACTIVE = "INTERACTIVE"

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            maximum_bytes_billed=123456,
            query_cache=False,
            dry_run=True,
        )

        adapter.configure_for_benchmark(mock_connection, "tpch")

        mock_bigquery.QueryJobConfig.assert_called_once_with(
            priority="INTERACTIVE",
            use_query_cache=False,
            dry_run=True,
        )
        assert mock_job_config.maximum_bytes_billed == 123456
        assert mock_job_config.default_dataset == "test-project.test_dataset"
        assert mock_job_config.use_legacy_sql is False
        assert mock_job_config.flatten_results is False
        assert mock_connection._default_job_config is mock_job_config

    def test_convert_to_bigquery_table_adds_dataset_partition_and_cluster(self, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            partitioning_field="order_date",
            clustering_fields=["customer_id", "order_id"],
        )

        converted = adapter._convert_to_bigquery_table("CREATE TABLE orders (id INT64)")

        assert converted.startswith("CREATE OR REPLACE TABLE `test-project.test_dataset.ORDERS` (id INT64)")
        assert "PARTITION BY DATE(order_date)" in converted
        assert "CLUSTER BY customer_id, order_id" in converted

    def test_qualify_table_names_adds_fully_qualified_tpch_tables(self, dependencies_available):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        qualified = adapter._qualify_table_names(
            "SELECT * FROM customer JOIN orders ON customer.c_custkey = orders.o_custkey"
        )

        assert "`test-project.test_dataset.CUSTOMER`" in qualified
        assert "`test-project.test_dataset.ORDERS`" in qualified

    def test_qualify_table_names_handles_merge_into_and_using(self, dependencies_available):
        adapter = BigQueryAdapter(project_id="test-project", dataset_id="test_dataset")

        qualified = adapter._qualify_table_names(
            "MERGE INTO merge_ops_target AS target "
            "USING (SELECT * FROM orders WHERE o_orderkey BETWEEN 1 AND 10) AS source "
            "ON target.o_orderkey = source.o_orderkey "
            "WHEN NOT MATCHED THEN INSERT VALUES (source.o_orderkey)"
        )

        assert qualified.startswith("MERGE INTO `test-project.test_dataset.MERGE_OPS_TARGET` AS target")
        assert "FROM `test-project.test_dataset.ORDERS`" in qualified
        assert "USING (" in qualified

    def test_prepare_external_parquet_uris_uploads_local_and_preserves_cloud(self, dependencies_available):
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        uploaded_blobs = {}

        def create_blob(blob_name):
            blob = Mock()
            uploaded_blobs[blob_name] = blob
            return blob

        bucket.blob.side_effect = create_blob

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            parquet_path = tmpdir_path / "orders.parquet"
            parquet_path.write_text("parquet")
            csv_path = tmpdir_path / "orders.csv"
            csv_path.write_text("id,name\n1,alpha\n")

            uris = adapter._prepare_external_parquet_uris(
                bucket,
                "orders",
                [parquet_path, csv_path, "gs://cloud-bucket/orders/part-000.parquet"],
            )

        assert uris == [
            "gs://benchbox-bucket/benchbox-data/orders/orders.parquet",
            "gs://cloud-bucket/orders/part-000.parquet",
        ]
        uploaded_blobs["benchbox-data/orders/orders.parquet"].upload_from_filename.assert_called_once_with(
            str(parquet_path)
        )
        assert "benchbox-data/orders/orders.csv" not in uploaded_blobs

    def test_prepare_external_table_uris_requires_biglake_for_delta(self, dependencies_available):
        bucket = Mock()
        uploaded_blobs = {}

        def create_blob(blob_name):
            blob = Mock()
            uploaded_blobs[blob_name] = blob
            return blob

        bucket.blob.side_effect = create_blob

        with tempfile.TemporaryDirectory() as tmpdir:
            delta_dir = Path(tmpdir) / "orders_delta"
            (delta_dir / "_delta_log").mkdir(parents=True)
            (delta_dir / "_delta_log" / "00000000000000000000.json").write_text("{}")
            (delta_dir / "part-000.parquet").write_text("parquet")

            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                storage_bucket="benchbox-bucket",
                storage_prefix="benchbox-data",
            )

            with pytest.raises(ValueError, match="biglake_connection"):
                adapter._prepare_external_table_uris(bucket, "orders", [delta_dir])

            adapter.biglake_connection = "test-project.us.benchbox"
            source_format, uris = adapter._prepare_external_table_uris(bucket, "orders", [delta_dir])

        assert source_format == "DELTA_LAKE"
        assert uris == ["gs://benchbox-bucket/benchbox-data/orders/"]
        assert sorted(uploaded_blobs) == [
            "benchbox-data/orders/_delta_log/00000000000000000000.json",
            "benchbox-data/orders/part-000.parquet",
        ]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_table_via_cloud_storage_uses_one_job_for_all_files(self, mock_bigquery, dependencies_available):
        from threading import Barrier

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
        mock_bigquery.SourceFormat.CSV = "CSV"

        job_configs = []

        def create_job_config(**kwargs):
            config = Mock()
            for key, value in kwargs.items():
                setattr(config, key, value)
            config.to_api_repr.return_value = kwargs
            job_configs.append(config)
            return config

        mock_bigquery.LoadJobConfig.side_effect = create_job_config

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        uploads_started = Barrier(2, timeout=10)

        def make_blob(_name):
            blob = Mock()
            blob.upload_from_filename.side_effect = lambda _path: uploads_started.wait()
            return blob

        bucket.blob.side_effect = make_blob
        mock_connection = Mock()
        mock_dataset = Mock()
        mock_table_ref = Mock()
        mock_connection.dataset.return_value = mock_dataset
        mock_dataset.table.return_value = mock_table_ref
        mock_load_job = Mock()
        mock_connection.load_table_from_uri.return_value = mock_load_job

        with tempfile.TemporaryDirectory() as tmpdir:
            chunk1 = Path(tmpdir) / "customer_1.dat"
            chunk2 = Path(tmpdir) / "customer_2.dat"
            chunk1.write_text("1|alpha\n")
            chunk2.write_text("2|beta\n")

            with patch.object(adapter, "_get_table_row_count", return_value=2):
                row_count = adapter._load_table_via_cloud_storage(
                    mock_connection,
                    bucket,
                    "customer",
                    [chunk1, chunk2],
                )

        assert row_count == 2
        mock_connection.load_table_from_uri.assert_called_once()
        uris, _table_ref = mock_connection.load_table_from_uri.call_args.args
        assert uris == [
            "gs://benchbox-bucket/benchbox-data/customer_0.dat",
            "gs://benchbox-bucket/benchbox-data/customer_1.dat",
        ]
        assert bucket.blob.call_count == 2
        assert job_configs[0].write_disposition == "WRITE_TRUNCATE"
        assert job_configs[1].write_disposition == "WRITE_TRUNCATE"
        assert job_configs[0].field_delimiter == "|"
        assert job_configs[1].field_delimiter == "|"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_cloud_load_rejects_mixed_file_settings_before_upload(
        self, mock_bigquery, dependencies_available, tmp_path
    ):
        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
        )
        first = tmp_path / "one.csv"
        second = tmp_path / "two.tbl"
        first.write_text("x\n")
        second.write_text("x|\n")
        configs = [Mock(), Mock()]
        configs[0].to_api_repr.return_value = {"sourceFormat": "CSV", "fieldDelimiter": ","}
        configs[1].to_api_repr.return_value = {"sourceFormat": "CSV", "fieldDelimiter": "|"}
        bucket = Mock()
        connection = Mock()
        with patch.object(adapter, "_build_load_job_config", side_effect=configs):
            with pytest.raises(ValueError, match="incompatible load settings"):
                adapter._load_table_via_cloud_storage(connection, bucket, "customer", [first, second])
        bucket.blob.assert_not_called()
        connection.load_table_from_uri.assert_not_called()

    @patch("benchbox.platforms.bigquery.time.sleep")
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_table_via_cloud_storage_retries_on_rate_limit_then_succeeds(
        self, mock_bigquery, mock_sleep, dependencies_available
    ):
        from google.api_core.exceptions import TooManyRequests

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
        mock_bigquery.SourceFormat.CSV = "CSV"
        mock_bigquery.LoadJobConfig.side_effect = lambda **kwargs: Mock(**kwargs)

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        mock_connection = Mock()
        mock_connection.dataset.return_value.table.return_value = Mock()

        succeeding_job = Mock()
        mock_connection.load_table_from_uri.side_effect = [
            TooManyRequests("rate limit exceeded"),
            TooManyRequests("rate limit exceeded"),
            succeeding_job,
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            chunk = Path(tmpdir) / "customer_0.dat"
            chunk.write_text("1|alpha\n")

            with patch.object(adapter, "_get_table_row_count", return_value=1):
                row_count = adapter._load_table_via_cloud_storage(mock_connection, bucket, "customer", [chunk])

        assert row_count == 1
        assert mock_connection.load_table_from_uri.call_count == 3
        succeeding_job.result.assert_called_once()
        mock_sleep.assert_any_call(2.5)
        mock_sleep.assert_any_call(5.0)

    @patch("benchbox.platforms.bigquery.time.sleep")
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_table_via_cloud_storage_repolls_same_job_on_polling_rate_limit(
        self, mock_bigquery, mock_sleep, dependencies_available
    ):
        from google.api_core.exceptions import TooManyRequests

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
        mock_bigquery.SourceFormat.CSV = "CSV"
        mock_bigquery.LoadJobConfig.side_effect = lambda **kwargs: Mock(**kwargs)

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        mock_connection = Mock()
        mock_connection.dataset.return_value.table.return_value = Mock()

        accepted_job = Mock()
        accepted_job.result.side_effect = [
            TooManyRequests("polling rate limit exceeded"),
            TooManyRequests("polling rate limit exceeded"),
            None,
        ]
        mock_connection.load_table_from_uri.return_value = accepted_job

        with tempfile.TemporaryDirectory() as tmpdir:
            chunk = Path(tmpdir) / "customer_0.dat"
            chunk.write_text("1|alpha\n")

            with patch.object(adapter, "_get_table_row_count", return_value=1):
                row_count = adapter._load_table_via_cloud_storage(mock_connection, bucket, "customer", [chunk])

        assert row_count == 1
        assert mock_connection.load_table_from_uri.call_count == 1
        assert accepted_job.result.call_count == 3

    @patch("benchbox.platforms.bigquery.time.sleep")
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_table_via_cloud_storage_raises_after_exhausting_retries(
        self, mock_bigquery, mock_sleep, dependencies_available
    ):
        from google.api_core.exceptions import TooManyRequests

        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
        mock_bigquery.SourceFormat.CSV = "CSV"
        mock_bigquery.LoadJobConfig.side_effect = lambda **kwargs: Mock(**kwargs)

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        mock_connection = Mock()
        mock_connection.dataset.return_value.table.return_value = Mock()
        mock_connection.load_table_from_uri.side_effect = TooManyRequests("rate limit exceeded")

        with tempfile.TemporaryDirectory() as tmpdir:
            chunk = Path(tmpdir) / "customer_0.dat"
            chunk.write_text("1|alpha\n")

            with pytest.raises(TooManyRequests):
                adapter._load_table_via_cloud_storage(mock_connection, bucket, "customer", [chunk])

        assert mock_connection.load_table_from_uri.call_count == 5
        mock_sleep.assert_any_call(2.5)
        mock_sleep.assert_any_call(5.0)
        mock_sleep.assert_any_call(10.0)
        mock_sleep.assert_any_call(20.0)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_load_table_via_cloud_storage_does_not_retry_other_errors(self, mock_bigquery, dependencies_available):
        mock_bigquery.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
        mock_bigquery.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
        mock_bigquery.SourceFormat.CSV = "CSV"
        mock_bigquery.LoadJobConfig.side_effect = lambda **kwargs: Mock(**kwargs)

        adapter = BigQueryAdapter(
            project_id="test-project",
            dataset_id="test_dataset",
            storage_bucket="benchbox-bucket",
            storage_prefix="benchbox-data",
        )
        bucket = Mock()
        mock_connection = Mock()
        mock_connection.dataset.return_value.table.return_value = Mock()
        mock_connection.load_table_from_uri.side_effect = ValueError("malformed schema")

        with tempfile.TemporaryDirectory() as tmpdir:
            chunk = Path(tmpdir) / "customer_0.dat"
            chunk.write_text("1|alpha\n")

            with pytest.raises(ValueError, match="malformed schema"):
                adapter._load_table_via_cloud_storage(mock_connection, bucket, "customer", [chunk])

        assert mock_connection.load_table_from_uri.call_count == 1


@pytest.mark.usefixtures("dependencies_available")
class TestConvertToBigqueryTable:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_non_create_table_passes_through(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("INSERT INTO t VALUES (1)")
        assert result == "INSERT INTO t VALUES (1)"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_adds_or_replace(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE orders (id INT64)")
        assert "CREATE OR REPLACE TABLE" in result
        assert "CREATE TABLE " not in result.replace("CREATE OR REPLACE TABLE", "")

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_existing_or_replace(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE OR REPLACE TABLE orders (id INT64)")
        assert result.count("OR REPLACE") == 1

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_with_project_and_dataset(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-project", dataset_id="my_dataset")
        result = adapter._convert_to_bigquery_table("CREATE TABLE lineitem (l_orderkey INT64)")
        assert "`my-project.my_dataset." in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_partitioning_when_field_not_set(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "PARTITION BY" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_clustering_when_fields_not_set(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "CLUSTER BY" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_ctas_without_pk_returns_promptly_and_qualified(self, mock_bigquery):
        import time

        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        sql = (
            "CREATE TABLE orders_1995 AS\nSELECT *\nFROM orders\n"
            "WHERE o_orderdate >= DATE '1995-01-01'\n  AND o_orderdate < DATE '1996-01-01'\n"
        )
        start = time.perf_counter()
        result = adapter._convert_to_bigquery_table(sql)
        assert time.perf_counter() - start < 5
        assert "CREATE OR REPLACE TABLE `proj.ds.ORDERS_1995` AS" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_table_primary_key_marked_not_enforced(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64, PRIMARY KEY (id))")
        assert "PRIMARY KEY (id) NOT ENFORCED" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_foreign_key_reference_qualified_and_not_enforced(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table(
            "CREATE TABLE `link_nation_region` (`hk_nation` STRING(64) NOT NULL, `hk_region` STRING(64) NOT NULL, "
            "PRIMARY KEY (`hk_nation`), FOREIGN KEY (`hk_nation`) REFERENCES `hub_nation` (`hk_nation`), "
            "FOREIGN KEY (`hk_region`) REFERENCES hub_region (`hk_region`))"
        )
        assert "REFERENCES `proj.ds.HUB_NATION` (`hk_nation`) NOT ENFORCED" in result
        assert "REFERENCES `proj.ds.HUB_REGION` (`hk_region`) NOT ENFORCED" in result
        assert "`hub_nation`" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_foreign_key_already_not_enforced_is_unchanged(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        sql = "CREATE TABLE t (a INT64, FOREIGN KEY (a) REFERENCES `proj.ds.P` (a) NOT ENFORCED)"
        result = adapter._convert_to_bigquery_table(sql)
        assert result.count("NOT ENFORCED") == 1
        assert "REFERENCES `proj.ds.P` (a) NOT ENFORCED" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_foreign_key_rewrite_ignores_string_literals(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        sql = "CREATE TABLE t (note STRING DEFAULT 'REFERENCES parent(id)', a INT64, FOREIGN KEY (a) REFERENCES p (a))"
        result = adapter._convert_to_bigquery_table(sql)
        assert "DEFAULT 'REFERENCES parent(id)'" in result
        assert "REFERENCES `proj.ds.P` (a) NOT ENFORCED" in result

    def test_unqualified_table_names_skips_earlier_sibling_ctes(self):
        query = (
            "WITH first_cte AS (SELECT a FROM base_one), "
            "second_cte AS (SELECT f.a FROM orders o JOIN first_cte f ON o.a = f.a) "
            "SELECT * FROM second_cte"
        )
        assert unqualified_table_names(query, "bigquery") == ["BASE_ONE", "ORDERS"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualify_aliases_table_referenced_with_backticked_prefix(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "SELECT SUM(`l_quantity`) FROM `lineitem`, `part` WHERE `p_partkey` = `lineitem`.`l_partkey`"
        result = adapter._qualify_single_statement(query)
        assert "FROM `proj.ds.LINEITEM` AS lineitem," in result
        assert "`proj.ds.PART` AS" not in result

    def test_unqualified_table_names_keeps_self_shadowed_base_table(self):
        query = "WITH orders AS (SELECT * FROM orders WHERE a > 1) SELECT * FROM orders"
        assert unqualified_table_names(query, "bigquery") == ["ORDERS"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_inline_pk_survives_parameterized_types(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table(
            "CREATE TABLE t (id INT, price DECIMAL(10, 2) PRIMARY KEY, col3 STRING)"
        )
        assert "PRIMARY KEY (price) NOT ENFORCED" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_inline_pk_ignores_parens_in_comments(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table(
            "CREATE TABLE t (id INT PRIMARY KEY, val INT /* note ) */, name STRING)"
        )
        assert "/* note ) */" in result
        assert result.rstrip().endswith("PRIMARY KEY (id) NOT ENFORCED)")

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_numeric_decimal_literals_skip_strings_and_identifiers(self, mock_bigquery):
        sql = "UPDATE t SET p = p * 1.05, c = 'v1.5', q = 1000.0 * n WHERE k2 = 3 AND x = 0.02"
        result = BigQueryAdapter._numeric_decimal_literals(sql)
        assert result == (
            "UPDATE t SET p = p * NUMERIC '1.05', c = 'v1.5', q = NUMERIC '1000.0' * n "
            "WHERE k2 = 3 AND x = NUMERIC '0.02'"
        )

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualify_leaves_script_temp_tables_bare(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        sql = (
            "BEGIN TRANSACTION;\n"
            "CREATE TEMP TABLE IF NOT EXISTS temp_orders_1 AS SELECT * FROM orders WHERE 1=0;\n"
            "INSERT INTO temp_orders_1 VALUES (1);\n"
            "INSERT INTO txn_orders SELECT * FROM temp_orders_1;\n"
            "DROP TABLE IF EXISTS temp_orders_1;\n"
            "COMMIT;\n"
        )
        result = adapter._qualify_table_names(sql)
        assert "`proj.ds.ORDERS`" in result
        assert "INSERT INTO `proj.ds.TXN_ORDERS` SELECT * FROM temp_orders_1" in result
        assert "TEMP_ORDERS_1" not in result
        assert adapter._batch_temp_tables == frozenset()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preprocess_operation_sql_rewrites_bq_gaps(self, mock_bigquery):
        from types import SimpleNamespace

        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        operation = SimpleNamespace(
            write_sql="SELECT CAST(n AS VARCHAR), o_orderdate + INTERVAL '7' DAY",
            platform_overrides={},
        )
        result = adapter.preprocess_operation_sql("q", operation)
        assert "CAST(n AS STRING)" in result
        assert "INTERVAL 7 DAY" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preprocess_operation_sql_respects_overrides(self, mock_bigquery):
        from types import SimpleNamespace

        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        over = SimpleNamespace(write_sql="SELECT 1", platform_overrides={"bigquery": "SELECT CAST(x AS VARCHAR)"})
        assert "CAST(x AS STRING)" in adapter.preprocess_operation_sql("q", over)
        skipped = SimpleNamespace(write_sql="SELECT 1", platform_overrides={"bigquery": None})
        assert adapter.preprocess_operation_sql("q", skipped) is None

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_comment_prefixed_create_still_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        chunk = "-- Generated staging load tables\nCREATE TABLE orders_stage (id INT64)"
        result = adapter._convert_to_bigquery_table(chunk)
        assert result.startswith("-- Generated staging load tables\n")
        assert "CREATE OR REPLACE TABLE `proj.ds.ORDERS_STAGE`" in result
        assert "CREATE TABLE orders_stage" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_adds_partitioning_clause(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", partitioning_field="created_at")
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "PARTITION BY DATE(created_at)" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_adds_clustering_clause(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", clustering_fields=["a", "b", "c"])
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "CLUSTER BY a, b, c" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_skips_partition_if_already_present(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", partitioning_field="ts")
        sql = "CREATE TABLE t (id INT64) PARTITION BY DATE(ts)"
        result = adapter._convert_to_bigquery_table(sql)
        assert result.count("PARTITION BY") == 1

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_skips_cluster_if_already_present(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", clustering_fields=["x"])
        sql = "CREATE TABLE t (id INT64) CLUSTER BY x"
        result = adapter._convert_to_bigquery_table(sql)
        assert result.count("CLUSTER BY") == 1

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_varchar_mapped_to_string(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._convert_to_bigquery_table(
            "CREATE TABLE update_ops_orders (o_orderkey INT64, o_comment VARCHAR(79), flag VARCHAR)"
        )
        assert "VARCHAR" not in result
        assert "o_comment STRING" in result
        assert "flag STRING" in result
        assert result.startswith("CREATE OR REPLACE TABLE `p1.d1.UPDATE_OPS_ORDERS`")

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_backticked_table_name_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE `orders` (id INT64)")
        assert result == "CREATE OR REPLACE TABLE `my-proj.my_ds.ORDERS` (id INT64)"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_already_qualified_table_name(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE `my_ds.orders` (id INT64)")
        assert result == "CREATE OR REPLACE TABLE `my_ds.ORDERS` (id INT64)"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_table_level_primary_key_not_enforced(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE trips (\n    id INT64,\n    PRIMARY KEY (id)\n)")
        assert "PRIMARY KEY (id) NOT ENFORCED" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_not_enforced_not_duplicated(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64,\n    PRIMARY KEY (id) NOT ENFORCED)")
        assert result.count("NOT ENFORCED") == 1

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_if_not_exists(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE IF NOT EXISTS orders (id INT64)")
        assert result == "CREATE TABLE IF NOT EXISTS `proj.ds.ORDERS` (id INT64)"
        assert "OR REPLACE" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_if_not_exists_ctas(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        sql = (
            "CREATE TABLE IF NOT EXISTS orders_1995 AS\nSELECT *\nFROM orders\nWHERE o_orderdate >= DATE '1995-01-01'\n"
        )
        result = adapter._convert_to_bigquery_table(sql)
        assert "CREATE TABLE IF NOT EXISTS `proj.ds.ORDERS_1995` AS" in result
        assert "OR REPLACE" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_if_not_exists_with_comment_prefix(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        chunk = "-- Generated staging table\nCREATE TABLE IF NOT EXISTS orders_stage (id INT64)"
        result = adapter._convert_to_bigquery_table(chunk)
        assert result.startswith("-- Generated staging table\n")
        assert "CREATE TABLE IF NOT EXISTS `proj.ds.ORDERS_STAGE` (id INT64)" in result
        assert "OR REPLACE" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_if_not_exists_ctas_with_comment_prefix(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        chunk = "-- Generated staging CTAS\nCREATE TABLE IF NOT EXISTS orders_stage AS SELECT 1"
        result = adapter._convert_to_bigquery_table(chunk)
        assert result.startswith("-- Generated staging CTAS\n")
        assert "CREATE TABLE IF NOT EXISTS `proj.ds.ORDERS_STAGE` AS SELECT 1" in result
        assert "OR REPLACE" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_if_not_exists_already_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE IF NOT EXISTS `my_ds.orders` (id INT64)")
        assert result == "CREATE TABLE IF NOT EXISTS `my_ds.ORDERS` (id INT64)"
        assert "OR REPLACE" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_staging_manifest_ddl_preserves_if_not_exists(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        manifest_ddl = (
            "CREATE TABLE IF NOT EXISTS benchbox_staging_manifest ("
            "benchmark VARCHAR, scale VARCHAR, spec_version VARCHAR, "
            "source_digest VARCHAR, created_at VARCHAR)"
        )
        result = adapter._convert_to_bigquery_table(manifest_ddl)
        expected = (
            "CREATE TABLE IF NOT EXISTS `my-proj.my_ds.BENCHBOX_STAGING_MANIFEST` ("
            "benchmark STRING, scale STRING, spec_version STRING, "
            "source_digest STRING, created_at STRING)"
        )
        assert result == expected
        assert "OR REPLACE" not in result


@pytest.mark.usefixtures("dependencies_available")
class TestBigQueryCreateSemanticsStagingAndManifest:
    class _SimulatedBigQueryConnection:
        def __init__(self, adapter: BigQueryAdapter):
            self.adapter = adapter
            self.tables: dict[str, list[tuple]] = {}
            self.executed_queries: list[str] = []

        def execute(self, sql: str, parameters: Any = None):
            import re

            converted = self.adapter._convert_to_bigquery_table(sql)
            self.executed_queries.append(converted)
            clean = re.sub(r"/\*.*?\*/|--[^\n]*", "", converted).strip()

            def _target(name: str) -> str:
                return name.split(".")[-1].strip("`").upper()

            or_replace = re.match(
                r"^CREATE\s+OR\s+REPLACE\s+TABLE\s+(`?[a-zA-Z0-9_.]+`?)",
                clean,
                flags=re.IGNORECASE,
            )
            if or_replace:
                self.tables[_target(or_replace.group(1))] = []
                return self._cursor([])

            if_not_exists = re.match(
                r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(`?[a-zA-Z0-9_.]+`?)",
                clean,
                flags=re.IGNORECASE,
            )
            if if_not_exists:
                table = _target(if_not_exists.group(1))
                if table not in self.tables:
                    self.tables[table] = []
                return self._cursor([])

            insert = re.match(
                r"^INSERT\s+INTO\s+(`?[a-zA-Z0-9_.]+`?)\s*(?:\([^)]*\))?\s*VALUES\s*(.*)",
                clean,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if insert:
                table = _target(insert.group(1))
                values = tuple(re.findall(r"'([^']*)'", insert.group(2)))
                if table not in self.tables:
                    self.tables[table] = []
                self.tables[table].append(values)
                return self._cursor([])

            delete = re.match(
                r"^DELETE\s+FROM\s+(`?[a-zA-Z0-9_.]+`?)(?:\s+WHERE\s+(.*))?",
                clean,
                flags=re.IGNORECASE,
            )
            if delete:
                table = _target(delete.group(1))
                where = delete.group(2)
                if table in self.tables and where:
                    bench_match = re.search(r"benchmark\s*=\s*'([^']*)'", where)
                    if bench_match:
                        target_bench = bench_match.group(1)
                        self.tables[table] = [r for r in self.tables[table] if not (r and r[0] == target_bench)]
                    else:
                        self.tables[table] = []
                return self._cursor([])

            count = re.match(
                r"^SELECT\s+COUNT\(\*\)\s+FROM\s+(`?[a-zA-Z0-9_.]+`?)(?:\s+WHERE\s+(.*))?",
                clean,
                flags=re.IGNORECASE,
            )
            if count:
                table = _target(count.group(1))
                where = count.group(2)
                rows = self.tables.get(table, [])
                if where:
                    bench_match = re.search(r"benchmark\s*=\s*'([^']*)'", where)
                    if bench_match:
                        target_bench = bench_match.group(1)
                        rows = [r for r in rows if r and r[0] == target_bench]
                return self._cursor([(len(rows),)])

            return self._cursor([])

        def _cursor(self, rows: list[tuple]):
            class _Cursor:
                def __init__(self, data: list[tuple]):
                    self.data = data

                def fetchone(self):
                    return self.data[0] if self.data else None

                def fetchall(self):
                    return self.data

            return _Cursor(rows)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_reuse_of_populated_staging_tables_keeps_rows(self, mock_bigquery):
        from benchbox.core.write_primitives.benchmark import get_create_table_sql

        adapter = BigQueryAdapter(project_id="my-project", dataset_id="my_ds")
        conn = self._SimulatedBigQueryConnection(adapter)

        target_table = "UPDATE_OPS_ORDERS"
        initial_rows = [("1", "comment 1"), ("2", "comment 2"), ("3", "comment 3")]
        conn.tables[target_table] = list(initial_rows)

        create_sql = get_create_table_sql("update_ops_orders", dialect="bigquery", if_not_exists=True)
        conn.execute(create_sql)

        last_query = conn.executed_queries[-1]
        assert "CREATE TABLE IF NOT EXISTS `my-project.my_ds.UPDATE_OPS_ORDERS`" in last_query
        assert "OR REPLACE" not in last_query

        assert len(conn.tables[target_table]) == 3
        assert conn.tables[target_table] == initial_rows

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_shared_manifest_keeps_both_benchmarks_provenance_rows(self, mock_bigquery):
        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        adapter = BigQueryAdapter(project_id="my-project", dataset_id="my_ds")
        conn = self._SimulatedBigQueryConnection(adapter)

        conn.tables["ORDERS"] = [(1,)]
        conn.tables["LINEITEM"] = [(1,)]
        conn.tables["CUSTOMER"] = [(1,)]

        bench1 = TransactionPrimitivesBenchmark(scale_factor=0.01)
        bench1._setup_dialect = "bigquery"
        bench2 = WritePrimitivesBenchmark(scale_factor=0.01)
        bench2._setup_dialect = "bigquery"

        source_tables = ["orders", "lineitem", "customer"]

        bench1._write_staging_manifest(conn, source_tables)
        assert bench1._staging_manifest_matches(conn, source_tables) is True

        manifest_table = bench1._STAGING_MANIFEST_TABLE.upper()
        assert len(conn.tables[manifest_table]) == 1
        assert conn.tables[manifest_table][0][0] == "Transaction Primitives"

        bench2._write_staging_manifest(conn, source_tables)
        assert bench2._staging_manifest_matches(conn, source_tables) is True

        assert bench1._staging_manifest_matches(conn, source_tables) is True
        assert len(conn.tables[manifest_table]) == 2
        benchmarks_in_manifest = {r[0] for r in conn.tables[manifest_table]}
        assert benchmarks_in_manifest == {"Transaction Primitives", "Write Primitives"}


@pytest.mark.usefixtures("dependencies_available")
class TestQualifyTableNames:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_all_tpch_tables(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        tpch_tables = ["REGION", "NATION", "CUSTOMER", "SUPPLIER", "PART", "PARTSUPP", "ORDERS", "LINEITEM"]
        for table in tpch_tables:
            result = adapter._qualify_table_names(f"SELECT * FROM {table}")
            assert f"`p1.d1.{table}`" in result, f"{table} not qualified"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_case_insensitive(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT * FROM customer")
        assert "`p1.d1.CUSTOMER`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_multiple_references_in_join(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        sql = "SELECT * FROM lineitem l JOIN orders o ON l.l_orderkey = o.o_orderkey"
        result = adapter._qualify_table_names(sql)
        assert "`p1.d1.LINEITEM`" in result
        assert "`p1.d1.ORDERS`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_non_tpch_tables(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT * FROM my_custom_table")
        assert result == "SELECT * FROM `p1.d1.MY_CUSTOM_TABLE`"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_project_scoped_information_schema_view_stays_unqualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        sql = "SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name"
        assert adapter._qualify_table_names(sql) == sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_dataset_scoped_information_schema_view_is_still_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT table_name FROM INFORMATION_SCHEMA.COLUMNS")
        assert result == "SELECT table_name FROM `p1.d1.INFORMATION_SCHEMA.COLUMNS`"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_insert_into_target(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("INSERT INTO insert_ops_lineitem SELECT * FROM lineitem")
        assert "INSERT INTO `p1.d1.INSERT_OPS_LINEITEM`" in result
        assert "FROM `p1.d1.LINEITEM`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_update_and_drop_targets(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("UPDATE update_ops_orders SET o_comment = 'x'")
        assert "UPDATE `p1.d1.UPDATE_OPS_ORDERS`" in result
        result = adapter._qualify_table_names("DROP TABLE IF EXISTS txn_orders")
        assert "DROP TABLE IF EXISTS `p1.d1.TXN_ORDERS`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_backtick_quoted_names(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT COUNT(*) FROM `ORDERS`")
        assert result == "SELECT COUNT(*) FROM `p1.d1.ORDERS`"
        result = adapter._qualify_table_names("SELECT COUNT(*) FROM `orders`")
        assert result == "SELECT COUNT(*) FROM `p1.d1.ORDERS`"
        result = adapter._qualify_table_names("INSERT INTO `update_ops_orders` SELECT * FROM `orders`")
        assert "INSERT INTO `p1.d1.UPDATE_OPS_ORDERS`" in result
        assert "FROM `p1.d1.ORDERS`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_backtick_columns_not_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT `o_orderkey`, `o_custkey` FROM `orders`")
        assert "`o_orderkey`" in result
        assert "`o_custkey`" in result
        assert "FROM `p1.d1.ORDERS`" in result
        assert "`p1.d1.O_ORDERKEY`" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_fallback_rewrite_when_disallowed(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        assert adapter._qualify_table_names("", allow_fallback=False) == ""

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_comma_identifiers_outside_from_are_not_tables(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names(
            "SELECT count(*) AS total, customer FROM lineitem JOIN customer ON lineitem.l_custkey = customer.c_custkey"
        )
        assert ", customer FROM" in result
        assert "`p1.d1.CUSTOMER` AS customer" in result
        result = adapter._qualify_table_names("SELECT coalesce(c_name, customer) FROM customer")
        assert "coalesce(c_name, customer)" in result
        result = adapter._qualify_table_names("INSERT INTO customer (c_custkey, customer) VALUES (1, 'v')")
        assert "(c_custkey, customer)" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_join_predicate_commas_are_not_table_separators(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names(
            "SELECT customer.c_name FROM customer JOIN lineitem ON COALESCE(lineitem.x, customer) IS NOT NULL"
        )
        assert "COALESCE(lineitem.x, customer) IS NOT NULL" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_backtick_and_commented_aliases_are_not_duplicated(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT orders.o_orderkey FROM orders `o`")
        assert result.count("`o`") == 1
        assert "AS orders" not in result
        long_comment = "/* " + "x" * 60 + " */"
        result = adapter._qualify_table_names(f"SELECT o FROM orders {long_comment} o")
        assert "AS orders" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_view_targets_are_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("CREATE VIEW orders_view AS SELECT * FROM orders")
        assert "CREATE VIEW `p1.d1.ORDERS_VIEW` AS" in result
        result = adapter._qualify_table_names("DROP VIEW IF EXISTS orders_view")
        assert result == "DROP VIEW IF EXISTS `p1.d1.ORDERS_VIEW`"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_materialized_view_targets_are_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("CREATE MATERIALIZED VIEW mv AS SELECT * FROM orders")
        assert "CREATE MATERIALIZED VIEW `p1.d1.MV` AS" in result
        result = adapter._qualify_table_names("DROP MATERIALIZED VIEW IF EXISTS mv")
        assert result == "DROP MATERIALIZED VIEW IF EXISTS `p1.d1.MV`"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_parenthesized_join_tables_are_qualified(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names(
            "SELECT * FROM (customer JOIN orders ON customer.c_custkey = 1) JOIN nation ON 1=1"
        )
        assert "(`p1.d1.CUSTOMER`" in result
        assert "SELECT coalesce(a, b)" in adapter._qualify_table_names("SELECT coalesce(a, b) FROM customer")

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_keyword_spelled_backtick_aliases_are_not_duplicated(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT orders.o_orderkey FROM orders `order` WHERE 1=1")
        assert result.count("`order`") == 1
        assert "AS orders" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_subquery_and_mixed_join_commas_qualify(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT * FROM (SELECT 1 FROM lineitem) sub, customer WHERE 1=1")
        assert ", `p1.d1.CUSTOMER`" in result
        result = adapter._qualify_table_names(
            "SELECT * FROM customer JOIN orders ON customer.c_custkey = 1, nation WHERE 1=1"
        )
        assert ", `p1.d1.NATION`" in result
        result = adapter._qualify_table_names("DELETE FROM customer USING orders, lineitem WHERE 1=1")
        assert ", `p1.d1.LINEITEM`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_later_from_commas_qualify_after_earlier_commas(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        result = adapter._qualify_table_names("SELECT a, b FROM customer, orders")
        assert "FROM `p1.d1.CUSTOMER`, `p1.d1.ORDERS`" in result
        result = adapter._qualify_table_names("SELECT * FROM customer JOIN lineitem ON COALESCE(a, b) = c, orders")
        assert "ON COALESCE(a, b) = c, `p1.d1.ORDERS`" in result
        result = adapter._qualify_table_names(
            "SELECT customer.c_name FROM customer JOIN lineitem ON COALESCE(lineitem.x, customer) IS NOT NULL, orders"
        )
        assert "COALESCE(lineitem.x, customer) IS NOT NULL" in result
        assert ", `p1.d1.ORDERS`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_qualifies_multistatement_batch_with_later_statement_table(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        batch_sql = (
            "CREATE TABLE stage_1 AS SELECT * FROM source_early;\nINSERT INTO target_late SELECT * FROM source_late;\n"
        )
        result = adapter._qualify_table_names(batch_sql)
        assert "CREATE TABLE `p1.d1.STAGE_1` AS SELECT * FROM `p1.d1.SOURCE_EARLY`" in result
        assert "INSERT INTO `p1.d1.TARGET_LATE` SELECT * FROM `p1.d1.SOURCE_LATE`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_withholds_alias_on_insert_target_with_same_named_references(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        sql = "INSERT INTO target (c1) SELECT target.c1 FROM source AS target"
        result = adapter._qualify_table_names(sql)
        assert result.startswith("INSERT INTO `p1.d1.TARGET` (c1)")
        assert "INSERT INTO `p1.d1.TARGET` AS target" not in result
        assert "FROM `p1.d1.SOURCE` AS target" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_withholds_alias_on_create_target_with_same_named_references(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        sql = "CREATE TABLE target AS SELECT target.c1 FROM source AS target"
        result = adapter._qualify_table_names(sql)
        assert result.startswith("CREATE TABLE `p1.d1.TARGET` AS SELECT")
        assert "CREATE TABLE `p1.d1.TARGET` AS target" not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_withholds_alias_on_drop_alter_truncate_targets(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")

        drop_sql = "DROP TABLE IF EXISTS target"
        assert adapter._qualify_table_names(drop_sql) == "DROP TABLE IF EXISTS `p1.d1.TARGET`"

        alter_sql = "ALTER TABLE target ADD COLUMN target_col INT64"
        assert adapter._qualify_table_names(alter_sql) == "ALTER TABLE `p1.d1.TARGET` ADD COLUMN target_col INT64"

        truncate_sql = "TRUNCATE TABLE target"
        assert adapter._qualify_table_names(truncate_sql) == "TRUNCATE TABLE `p1.d1.TARGET`"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_permits_alias_on_update_target_with_same_named_references(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="p1", dataset_id="d1")
        sql = "UPDATE target SET target.c = 1"
        result = adapter._qualify_table_names(sql)
        assert result == "UPDATE `p1.d1.TARGET` AS target SET target.c = 1"


@pytest.mark.usefixtures("dependencies_available")
class TestNormalizeTableNamesCase:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_normalizes_mixed_case_identifiers(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "SELECT `col_a`, `COL_B` FROM `my_table`"
        result = adapter._normalize_table_names_case(query)
        assert "`COL_A`" in result
        assert "`COL_B`" in result
        assert "`MY_TABLE`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_preserves_numeric_suffixes(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._normalize_table_names_case("SELECT * FROM `table123`")
        assert "`TABLE123`" in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_normalizes_mixed_case_tpcdi_identifiers(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._normalize_table_names_case("SELECT * FROM `DimCustomer` WHERE `BatchID` = 1")
        assert result == "SELECT * FROM `DIMCUSTOMER` WHERE `BATCHID` = 1"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_change_for_already_uppercase(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "SELECT * FROM `CUSTOMER` WHERE `CUSTOMER`.`C_ID` = 1"
        result = adapter._normalize_table_names_case(query)
        assert "`CUSTOMER`" in result
        assert result == query


@pytest.mark.usefixtures("dependencies_available")
class TestApplySafeDivision:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_division_routed_through_fork_rewrite(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._apply_safe_division("SELECT a / b FROM `T`")
        assert "SAFE_DIVIDE(a, b)" in result
        assert " / " not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_nested_division_fully_rewritten(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._apply_safe_division("SELECT a / (b / c) FROM t")
        assert result.count("SAFE_DIVIDE") == 2
        assert " / " not in result

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_query_without_division_unchanged(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "SELECT a FROM `T` WHERE b = 1"
        assert adapter._apply_safe_division(query) == query

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_slash_outside_division_leaves_text_unchanged(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "select  a  from `T` where b = 'N/A' and c = 'x/y'"
        assert adapter._apply_safe_division(query) == query

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_unparseable_query_unchanged(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        query = "SELEC ((( / b"
        assert adapter._apply_safe_division(query) == query


@pytest.mark.usefixtures("dependencies_available")
class TestGenerateTuningClause:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_timestamp_partitioning_uses_date_wrapper(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col = Mock()
        mock_col.name = "ts_col"
        mock_col.order = 1
        mock_col.type = "TIMESTAMP"

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.PARTITIONING:
                return [mock_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert "PARTITION BY DATE(ts_col)" in clause

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_date_partitioning_uses_direct_column(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col = Mock()
        mock_col.name = "order_date"
        mock_col.order = 1
        mock_col.type = "DATE"

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.PARTITIONING:
                return [mock_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert clause == "PARTITION BY order_date"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_integer_partitioning_uses_range_bucket(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col = Mock()
        mock_col.name = "id_col"
        mock_col.order = 1
        mock_col.type = "INT64"

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.PARTITIONING:
                return [mock_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert "RANGE_BUCKET(id_col, GENERATE_ARRAY(0, 1000000, 10000))" in clause

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_clustering_limited_to_four_columns(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        cols = []
        for i, name in enumerate(["a", "b", "c", "d", "e"]):
            c = Mock()
            c.name = name
            c.order = i
            cols.append(c)

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.CLUSTERING:
                return cols
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert "CLUSTER BY a, b, c, d" in clause
        assert "e" not in clause

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_combined_partitioning_and_clustering(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        part_col = Mock()
        part_col.name = "event_date"
        part_col.order = 1
        part_col.type = "DATE"

        cluster_col = Mock()
        cluster_col.name = "user_id"
        cluster_col.order = 1

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.PARTITIONING:
                return [part_col]
            if tt == TuningType.CLUSTERING:
                return [cluster_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert "PARTITION BY event_date" in clause
        assert "CLUSTER BY user_id" in clause

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_tuning_returns_empty(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False
        assert adapter.generate_tuning_clause(mock_tuning) == ""

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_default_partitioning_for_unknown_type(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col = Mock()
        mock_col.name = "weird_col"
        mock_col.order = 1
        mock_col.type = "STRING"

        from benchbox.core.tuning.interface import TuningType

        def get_cols(tt):
            if tt == TuningType.PARTITIONING:
                return [mock_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols
        clause = adapter.generate_tuning_clause(mock_tuning)
        assert "PARTITION BY DATE(weird_col)" in clause


@pytest.mark.usefixtures("dependencies_available")
class TestQueryCacheConfig:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_query_cache_explicit_true(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", query_cache=True)
        assert adapter.query_cache is True

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_query_cache_explicit_false(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", query_cache=False)
        assert adapter.query_cache is False

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_disable_result_cache_true_sets_query_cache_false(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", disable_result_cache=True)
        assert adapter.query_cache is False

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_disable_result_cache_false_sets_query_cache_true(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", disable_result_cache=False)
        assert adapter.query_cache is True

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_default_query_cache_is_disabled(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter.query_cache is False


@pytest.mark.usefixtures("dependencies_available")
class TestGetPlatformInfo:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_basic_info_without_connection(self, mock_bigquery):
        adapter = BigQueryAdapter(
            project_id="my-project",
            dataset_id="bench_ds",
            location="EU",
            storage_bucket="my-bucket",
            job_priority="BATCH",
            query_cache=True,
        )

        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "bigquery"
        assert info["platform_name"] == "BigQuery"
        assert info["connection_mode"] == "remote"
        assert info["cloud_provider"] == "GCP"
        assert info["configuration"]["project_id"] == "my-project"
        assert info["configuration"]["dataset_id"] == "bench_ds"
        assert info["configuration"]["location"] == "EU"
        assert info["configuration"]["storage_bucket"] == "my-bucket"
        assert info["configuration"]["job_priority"] == "BATCH"
        assert info["configuration"]["query_cache_enabled"] is True
        assert info["platform_version"] is None

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_info_with_connection_metadata_error(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.get_dataset.side_effect = Exception("access denied")
        info = adapter.get_platform_info(connection=mock_conn)
        assert info["platform_type"] == "bigquery"
        assert info["configuration"]["project_id"] == "proj"


@pytest.mark.usefixtures("dependencies_available")
class TestExecuteQuerySqlTransformation:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_backtick_query_normalizes_case(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(1,)]
        mock_job.job_id = "j1"
        mock_job.total_bytes_processed = 0
        mock_job.total_bytes_billed = 0
        mock_job.slot_millis = 0
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_conn.query.return_value = mock_job

        result = adapter.execute_query(mock_conn, "SELECT * FROM `customer`", "q1")

        assert result["status"] == "SUCCESS"
        called_sql = mock_conn.query.call_args[0][0]
        assert "`proj.ds.CUSTOMER`" in called_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_setup_create_uses_uppercase_convention(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = []
        mock_job.job_id = "j0"
        mock_job.total_bytes_processed = 0
        mock_job.total_bytes_billed = 0
        mock_job.slot_millis = 0
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_conn.query.return_value = mock_job

        result = adapter.execute_query(
            mock_conn,
            "CREATE TABLE update_ops_orders (o_orderkey INT64)",
            "setup",
            validate_row_count=False,
        )

        assert result["status"] == "SUCCESS"
        called_sql = mock_conn.query.call_args[0][0]
        assert "CREATE OR REPLACE TABLE `proj.ds.UPDATE_OPS_ORDERS`" in called_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_non_backtick_query_qualifies_tables(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(1,)]
        mock_job.job_id = "j2"
        mock_job.total_bytes_processed = 100
        mock_job.total_bytes_billed = 100
        mock_job.slot_millis = 50
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_conn.query.return_value = mock_job

        result = adapter.execute_query(mock_conn, "SELECT * FROM CUSTOMER", "q1")

        assert result["status"] == "SUCCESS"
        called_sql = mock_conn.query.call_args[0][0]
        assert "`proj.ds.CUSTOMER`" in called_sql

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_translated_query_stored_when_different(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = []
        mock_job.job_id = "j3"
        mock_job.total_bytes_processed = 0
        mock_job.total_bytes_billed = 0
        mock_job.slot_millis = 0
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_conn.query.return_value = mock_job

        original_sql = "SELECT * FROM REGION"
        result = adapter.execute_query(mock_conn, original_sql, "q1")

        assert result["translated_query"] is not None
        assert "`proj.ds.REGION`" in result["translated_query"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_job_statistics_in_result(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(42,)]
        mock_job.job_id = "job_abc"
        mock_job.total_bytes_processed = 2048
        mock_job.total_bytes_billed = 1024
        mock_job.slot_millis = 500
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_conn.query.return_value = mock_job

        result = adapter.execute_query(mock_conn, "SELECT 42", "q1")

        assert result["job_id"] == "job_abc"
        assert result["job_statistics"]["bytes_processed"] == 2048
        assert result["job_statistics"]["bytes_billed"] == 1024
        assert result["job_statistics"]["slot_ms"] == 500
        assert result["resource_usage"] == result["job_statistics"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_execute_query_error_returns_error_dict(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.query.side_effect = RuntimeError("Quota exceeded")

        result = adapter.execute_query(mock_conn, "SELECT 1", "q_fail")

        assert result["status"] == "FAILED"
        assert result["query_id"] == "q_fail"
        assert result["rows_returned"] == 0
        assert "Quota exceeded" in result["error"]
        assert result["error_type"] == "RuntimeError"


@pytest.mark.usefixtures("dependencies_available")
class TestCloseConnectionEdgeCases:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_close_none_connection(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        with patch.object(adapter.logger, "warning") as mock_warn:
            adapter.close_connection(None)
            mock_warn.assert_not_called()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_close_suppresses_token_error(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("token expired during refresh")
        with (
            patch.object(adapter, "log_very_verbose") as mock_log,
            patch.object(adapter.logger, "warning") as mock_warn,
        ):
            adapter.close_connection(mock_conn)
            mock_log.assert_called_once()
            mock_warn.assert_not_called()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_close_suppresses_auth_error(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("auth credentials invalid")
        with (
            patch.object(adapter, "log_very_verbose") as mock_log,
            patch.object(adapter.logger, "warning") as mock_warn,
        ):
            adapter.close_connection(mock_conn)
            mock_log.assert_called_once()
            mock_warn.assert_not_called()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_close_transport_cleanup(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("Network error")
        mock_transport = Mock()
        mock_conn._transport = mock_transport

        with patch.object(adapter.logger, "warning"):
            adapter.close_connection(mock_conn)

        mock_transport.close.assert_called_once()


@pytest.mark.usefixtures("dependencies_available")
class TestValidateCompressionSupport:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_zstd_file_raises_error(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_benchmark = Mock()
        mock_benchmark.name = "tpch"
        mock_benchmark.scale_factor = 1
        mock_benchmark.data_dir = "/tmp/data"

        with (
            patch("benchbox.platforms.bigquery.detect_compression", return_value="zstd"),
            pytest.raises(ValueError, match="Zstd"),
        ):
            adapter._validate_compression_support({"lineitem": [Path("/tmp/lineitem.csv.zst")]}, mock_benchmark)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_zstd_file_raises_with_output_dir(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_benchmark = Mock(spec=["name", "scale_factor", "output_dir"])
        mock_benchmark.name = "tpch"
        mock_benchmark.scale_factor = 1
        mock_benchmark.output_dir = "/tmp/bench_out"

        with (
            patch("benchbox.platforms.bigquery.detect_compression", return_value="zstd"),
            pytest.raises(ValueError, match="rm -rf /tmp/bench_out"),
        ):
            adapter._validate_compression_support({"lineitem": [Path("/tmp/lineitem.csv.zst")]}, mock_benchmark)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_gzip_file_passes(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_benchmark = Mock()

        with patch("benchbox.platforms.bigquery.detect_compression", return_value="gzip") as mock_detect:
            adapter._validate_compression_support({"lineitem": [Path("/tmp/lineitem.csv.gz")]}, mock_benchmark)
            mock_detect.assert_called_once_with(Path("/tmp/lineitem.csv.gz"))

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_uncompressed_file_passes(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_benchmark = Mock()

        with patch("benchbox.platforms.bigquery.detect_compression", return_value="none") as mock_detect:
            adapter._validate_compression_support({"nation": [Path("/tmp/nation.tbl")]}, mock_benchmark)
            mock_detect.assert_called_once_with(Path("/tmp/nation.tbl"))


@pytest.mark.usefixtures("dependencies_available")
class TestEnsureFileListAndFilterValidFiles:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_ensure_file_list_wraps_single(self, mock_bigquery):
        result = BigQueryAdapter._ensure_file_list(Path("/tmp/file.csv"))
        assert result == [Path("/tmp/file.csv")]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_ensure_file_list_preserves_list(self, mock_bigquery):
        paths = [Path("/tmp/a.csv"), Path("/tmp/b.csv")]
        result = BigQueryAdapter._ensure_file_list(paths)
        assert result == paths

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_filter_valid_files_rejects_nonexistent(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._filter_valid_files([Path("/nonexistent/file.csv")], allow_cloud=False)
        assert result == []

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_filter_valid_files_accepts_cloud_paths(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._filter_valid_files(["gs://bucket/file.parquet"], allow_cloud=True)
        assert result == ["gs://bucket/file.parquet"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_filter_valid_files_rejects_cloud_when_not_allowed(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        result = adapter._filter_valid_files(["gs://bucket/file.parquet"], allow_cloud=False)
        assert result == []


@pytest.mark.usefixtures("dependencies_available")
class TestGetExistingTables:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_returns_lowercase_table_names(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_t1 = Mock()
        mock_t1.table_id = "CUSTOMER"
        mock_t2 = Mock()
        mock_t2.table_id = "LINEITEM"
        mock_conn.list_tables.return_value = [mock_t1, mock_t2]

        result = adapter._get_existing_tables(mock_conn)

        assert result == ["customer", "lineitem"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_returns_empty_on_error(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_conn.list_tables.side_effect = Exception("dataset not found")
        mock_conn.dataset.side_effect = Exception("dataset not found")

        result = adapter._get_existing_tables(mock_conn)
        assert result == []


@pytest.mark.usefixtures("dependencies_available")
class TestBuildCtasSortSql:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_returns_none_when_off(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "none")):
            result = adapter._build_ctas_sort_sql("orders", [])
        assert result is None

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_raises_when_not_off(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        with (
            patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("auto", "ctas")),
            pytest.raises(ValueError, match="not yet executable"),
        ):
            adapter._build_ctas_sort_sql("orders", [Mock()])


@pytest.mark.usefixtures("dependencies_available")
class TestGetTableRowCount:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_generates_correct_count_sql(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(999,)]
        mock_conn.query.return_value = mock_job

        count = adapter.get_table_row_count(mock_conn, "lineitem")

        assert count == 999
        called_sql = mock_conn.query.call_args[0][0]
        assert called_sql == "SELECT COUNT(*) FROM `my-proj.my_ds.LINEITEM`"


@pytest.mark.usefixtures("dependencies_available")
class TestValidateDataIntegritySql:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_generates_correct_accessibility_sql(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(1,)]
        mock_conn.query.return_value = mock_job

        status, details = adapter._validate_data_integrity(Mock(), mock_conn, {"region": 5})

        assert status == "PASSED"
        called_sql = mock_conn.query.call_args[0][0]
        assert called_sql == "SELECT 1 FROM `proj.ds.REGION` LIMIT 1"


@pytest.mark.usefixtures("dependencies_available")
class TestBuildBigqueryConfig:
    def test_basic_config_construction(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {}

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "test-proj", "location": "EU"},
                overrides={},
                info=mock_info,
            )

        assert config.type == "bigquery"
        assert config.name == "Google BigQuery"
        assert config.project_id == "test-proj"
        assert config.location == "EU"

    def test_staging_root_extracts_bucket_and_prefix(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {}

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "proj"},
                overrides={"staging_root": "gs://my-bucket/my-prefix"},
                info=mock_info,
            )

        assert config.storage_bucket == "my-bucket"
        assert config.storage_prefix == "my-prefix"
        assert config.staging_root == "gs://my-bucket/my-prefix"

    def test_saved_creds_merged_with_options(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {
                "project_id": "saved-proj",
                "location": "US",
            }

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "cli-proj"},
                overrides={},
                info=mock_info,
            )

        assert config.project_id == "saved-proj"
        assert config.location == "US"

    def test_default_output_location_used_as_staging_fallback(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {
                "project_id": "proj",
                "default_output_location": "gs://default-bucket/default-path",
            }

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={},
                info=mock_info,
            )

        assert config.staging_root == "gs://default-bucket/default-path"
        assert config.storage_bucket == "default-bucket"

    def test_overrides_take_precedence_over_options(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {
                "project_id": "saved-proj",
            }

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "option-proj"},
                overrides={"project_id": "override-proj", "driver_version": "3.25.0"},
                info=mock_info,
            )

        assert config.project_id == "override-proj"
        assert config.driver_version == "3.25.0"

    def test_none_info_uses_defaults(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {}

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "proj"},
                overrides={},
                info=None,
            )

        assert config.name == "Google BigQuery"
        assert config.driver_package == "google-cloud-bigquery"


@pytest.mark.usefixtures("dependencies_available")
class TestConnectionParams:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_returns_adapter_defaults(self, mock_bigquery):
        adapter = BigQueryAdapter(
            project_id="proj",
            dataset_id="ds",
            location="EU",
            credentials_path="/path/to/creds.json",
        )
        params = adapter._get_connection_params()
        assert params["project_id"] == "proj"
        assert params["location"] == "EU"
        assert params["credentials_path"] == "/path/to/creds.json"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_overrides_from_connection_config(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds", location="US")
        params = adapter._get_connection_params(project_id="other-proj", location="asia-northeast1")
        assert params["project_id"] == "other-proj"
        assert params["location"] == "asia-northeast1"


@pytest.mark.usefixtures("dependencies_available")
class TestCleanupEmptyTables:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_no_tables_does_nothing(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        mock_client = Mock()
        mock_client.list_tables.return_value = []
        adapter._cleanup_empty_tables_if_needed(mock_client)
        mock_client.delete_table.assert_not_called()

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_majority_empty_triggers_cleanup(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        adapter.database_was_reused = True

        mock_client = Mock()
        mock_dataset_ref = Mock()
        mock_client.dataset.return_value = mock_dataset_ref

        table_infos = []
        table_objs = []
        for i in range(4):
            ti = Mock()
            ti.reference = Mock()
            ti.table_id = f"table{i}"
            table_infos.append(ti)
            to = Mock()
            to.num_rows = 0 if i < 3 else 100
            to.table_id = f"table{i}"
            table_objs.append(to)

        mock_client.list_tables.return_value = table_infos
        mock_client.get_table.side_effect = table_objs

        adapter._cleanup_empty_tables_if_needed(mock_client)

        assert adapter.database_was_reused is False
        assert mock_client.delete_table.call_count == 3

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_minority_empty_does_not_cleanup(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        adapter.database_was_reused = True

        mock_client = Mock()
        mock_dataset_ref = Mock()
        mock_client.dataset.return_value = mock_dataset_ref

        table_infos = []
        table_objs = []
        for i in range(4):
            ti = Mock()
            ti.reference = Mock()
            ti.table_id = f"table{i}"
            table_infos.append(ti)
            to = Mock()
            to.num_rows = 0 if i == 0 else 100
            to.table_id = f"table{i}"
            table_objs.append(to)

        mock_client.list_tables.return_value = table_infos
        mock_client.get_table.side_effect = table_objs

        adapter._cleanup_empty_tables_if_needed(mock_client)

        assert adapter.database_was_reused is True
        mock_client.delete_table.assert_not_called()


@pytest.mark.usefixtures("dependencies_available")
class TestPrepareExternalDeltaUris:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_cloud_delta_log_path_extracts_root(self, mock_bigquery):
        adapter = BigQueryAdapter(
            project_id="proj",
            dataset_id="ds",
            storage_bucket="bucket",
            storage_prefix="prefix",
        )
        bucket = Mock()

        uris = adapter._prepare_external_delta_uris(
            bucket,
            "orders",
            ["gs://cloud-bucket/data/orders/_delta_log/00000.json"],
        )

        assert uris == ["gs://cloud-bucket/data/orders/"]

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_non_delta_local_dir_ignored(self, mock_bigquery):
        adapter = BigQueryAdapter(
            project_id="proj",
            dataset_id="ds",
            storage_bucket="bucket",
            storage_prefix="prefix",
        )
        bucket = Mock()

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            regular_dir = Path(tmpdir) / "orders"
            regular_dir.mkdir()

            uris = adapter._prepare_external_delta_uris(bucket, "orders", [regular_dir])

        assert uris == []

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_cloud_path_without_delta_log_ignored(self, mock_bigquery):
        adapter = BigQueryAdapter(
            project_id="proj",
            dataset_id="ds",
            storage_bucket="bucket",
            storage_prefix="prefix",
        )
        bucket = Mock()

        uris = adapter._prepare_external_delta_uris(
            bucket,
            "orders",
            ["gs://cloud-bucket/data/orders/part-000.parquet"],
        )

        assert uris == []


@pytest.mark.usefixtures("dependencies_available")
class TestAddCliArguments:
    def test_adds_bigquery_arguments(self):
        import argparse

        parser = argparse.ArgumentParser()
        BigQueryAdapter.add_cli_arguments(parser)

        args = parser.parse_args(
            [
                "--project-id",
                "test-proj",
                "--dataset-id",
                "test_ds",
                "--location",
                "EU",
                "--credentials-path",
                "/path/to/creds.json",
                "--storage-bucket",
                "my-bucket",
            ]
        )

        assert args.project_id == "test-proj"
        assert args.dataset_id == "test_ds"
        assert args.location == "EU"
        assert args.credentials_path == "/path/to/creds.json"
        assert args.storage_bucket == "my-bucket"


@pytest.mark.usefixtures("dependencies_available")
class TestGetTargetDialect:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_returns_bigquery(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter.get_target_dialect() == "bigquery"


@pytest.mark.usefixtures("dependencies_available")
class TestPlatformNameAndProperties:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_platform_name_is_bigquery(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter.platform_name == "BigQuery"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_driver_isolation_capability(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter.driver_isolation_capability == DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_supports_external_tables(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter.supports_external_tables is True

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_internal_dialect_is_bigquery(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")
        assert adapter._dialect == "bigquery"


@pytest.mark.usefixtures("dependencies_available")
class TestBigQueryTableResolution:
    @patch("benchbox.platforms.bigquery.bigquery")
    def test_convert_to_bigquery_table_uppercase_flag(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        result = adapter._convert_to_bigquery_table("CREATE TABLE customer (id INT64)")
        assert result == "CREATE OR REPLACE TABLE `my-proj.my_ds.CUSTOMER` (id INT64)"

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_resolve_target_table_prefers_uppercase(self, mock_bigquery):
        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        conn = MagicMock()
        dataset_ref = conn.dataset.return_value
        table_ref_upper = dataset_ref.table.return_value
        conn.get_table.return_value = MagicMock()

        resolved_name, table_ref = adapter._resolve_target_table(conn, "customer")
        assert resolved_name == "CUSTOMER"
        conn.get_table.assert_called_once_with(table_ref_upper)

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_resolve_target_table_falls_back_to_exact_case(self, mock_bigquery):
        from google.cloud.exceptions import NotFound

        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        conn = MagicMock()
        dataset_ref = conn.dataset.return_value
        table_upper = MagicMock()
        table_lower = MagicMock()
        dataset_ref.table.side_effect = lambda name: table_upper if name == "CUSTOMER" else table_lower

        def fake_get_table(ref):
            if ref is table_upper:
                raise NotFound("table CUSTOMER not found")
            return table_lower

        conn.get_table.side_effect = fake_get_table
        resolved_name, table_ref = adapter._resolve_target_table(conn, "customer")
        assert resolved_name == "customer"
        assert table_ref is table_lower

    @patch("benchbox.platforms.bigquery.bigquery")
    def test_resolve_target_table_propagates_non_not_found_errors(self, mock_bigquery):
        from google.api_core.exceptions import Forbidden

        adapter = BigQueryAdapter(project_id="my-proj", dataset_id="my_ds")
        conn = MagicMock()
        conn.get_table.side_effect = Forbidden("caller lacks bigquery.tables.get permission")

        with pytest.raises(Forbidden):
            adapter._resolve_target_table(conn, "customer")
