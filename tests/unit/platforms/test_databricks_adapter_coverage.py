# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _mock_databricks_deps():
    with (
        patch("benchbox.platforms.databricks.adapter.databricks_sql"),
        patch("benchbox.platforms.databricks.check_platform_dependencies", return_value=(True, [])),
    ):
        yield


def _make_adapter(**kwargs):
    from benchbox.platforms.databricks import DatabricksAdapter

    defaults = {
        "server_hostname": "test.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/abc123",
        "access_token": "tok",
    }
    defaults.update(kwargs)
    return DatabricksAdapter(**defaults)


class TestAutoDetectDatabricksConfig:
    def test_returns_none_when_sdk_import_fails(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        with patch.dict("sys.modules", {"databricks.sdk": None}):
            result = DatabricksAdapter._auto_detect_databricks_config()

        assert result is None

    def test_returns_config_when_sdk_available(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        mock_workspace = MagicMock()
        mock_workspace.config.host = "https://test.cloud.databricks.com"
        mock_workspace.config.token = "autodetected-token"

        mock_wh = MagicMock()
        mock_wh.state = MagicMock()
        mock_wh.state.__str__ = lambda s: "RUNNING"
        mock_wh.id = "wh-123"

        mock_warehouses_api = MagicMock()
        mock_warehouses_api.list.return_value = [mock_wh]

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace
        mock_sdk.service.sql.WarehousesAPI.return_value = mock_warehouses_api

        with patch.dict(
            "sys.modules",
            {
                "databricks.sdk": mock_sdk,
                "databricks.sdk.service.sql": mock_sdk.service.sql,
            },
        ):
            result = DatabricksAdapter._auto_detect_databricks_config()

        assert result is not None
        assert "server_hostname" in result
        assert "access_token" in result
        assert result["access_token"] == "autodetected-token"

    def test_returns_none_on_exception(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.side_effect = RuntimeError("auth error")

        with patch.dict(
            "sys.modules",
            {
                "databricks.sdk": mock_sdk,
                "databricks.sdk.service.sql": MagicMock(),
            },
        ):
            result = DatabricksAdapter._auto_detect_databricks_config(very_verbose=True)

        assert result is None


class TestGetPlatformInfoSdkPath:
    @pytest.mark.parametrize(
        ("enable_photon", "applied", "applied_class"),
        [(True, "photon", "native-vectorized"), (False, "standard", "standard")],
    )
    def test_captures_compute_configuration_from_sdk(self, enable_photon, applied, applied_class):
        adapter = _make_adapter(http_path="/sql/1.0/warehouses/abc123")

        mock_warehouse = MagicMock()
        mock_warehouse.id = "abc123"
        mock_warehouse.name = "My Warehouse"
        mock_warehouse.cluster_size = "Medium"
        mock_warehouse.auto_stop_mins = 30
        mock_warehouse.min_num_clusters = 1
        mock_warehouse.max_num_clusters = 4
        mock_warehouse.enable_photon = enable_photon
        mock_warehouse.enable_serverless_compute = False
        mock_warehouse.warehouse_type = MagicMock()
        mock_warehouse.warehouse_type.value = "CLASSIC"
        mock_warehouse.spot_instance_policy = None
        mock_warehouse.channel = None
        mock_warehouse.state = MagicMock()
        mock_warehouse.state.value = "RUNNING"

        mock_workspace = MagicMock()
        mock_workspace.warehouses.get.return_value = mock_warehouse

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert "compute_configuration" in info
        cc = info["compute_configuration"]
        assert cc["warehouse_id"] == "abc123"
        assert cc["warehouse_name"] == "My Warehouse"
        assert cc["warehouse_size"] == "Medium"
        assert cc["enable_photon"] is enable_photon
        assert cc["warehouse_metadata_collection_status"] == "available"
        assert info["configuration"]["execution_engine"] == {
            "requested": "default",
            "applied": applied,
            "applied_class": applied_class,
            "applied_native": {"enable_photon": enable_photon},
            "resolution": "platform_default",
            "observed": "not_captured",
            "observed_source": "none",
        }

    def test_platform_info_sdk_import_error_silenced(self):
        adapter = _make_adapter()

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "databricks"
        assert info["compute_configuration"]["warehouse_id"] == "abc123"
        assert info["compute_configuration"]["warehouse_metadata_collection_status"] == "unavailable"
        assert info["compute_configuration"]["warehouse_metadata_error_class"] in {"ImportError", "ModuleNotFoundError"}
        receipt = info["configuration"]["execution_engine"]
        assert receipt["applied"] is None
        assert receipt["applied_class"] is None
        assert receipt["applied_native"] == {}
        assert receipt["observed"] == "not_captured"
        assert receipt["observed_source"] == "none"

    def test_platform_info_sdk_exception_silenced(self):
        adapter = _make_adapter()

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.side_effect = RuntimeError("SDK error")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "databricks"
        assert info["compute_configuration"]["warehouse_id"] == "abc123"
        assert info["compute_configuration"]["warehouse_metadata_collection_status"] == "unavailable"
        assert info["compute_configuration"]["warehouse_metadata_error_class"] == "RuntimeError"

    def test_unavailable_warehouse_metadata_excludes_identifiers_from_message(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        leaked = "Workspace synth-ws-9f31 warehouse SYNTH_WH host db-9f31.priv.example"
        result = DatabricksAdapter._unavailable_warehouse_metadata("abc123", RuntimeError(leaked))

        assert result["warehouse_metadata_error_class"] == "RuntimeError"
        for value in ("synth-ws-9f31", "SYNTH_WH", "db-9f31.priv.example"):
            assert value not in result["warehouse_metadata_error_message"]


class TestDetectDatabricksRegion:
    def _mock_sdk_with_region(self, region):
        mock_summary = MagicMock()
        mock_summary.region = region
        mock_workspace = MagicMock()
        mock_workspace.metastores.summary.return_value = mock_summary
        mock_workspace.warehouses.get.side_effect = RuntimeError("warehouse lookup disabled")
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace
        return mock_sdk, mock_workspace

    def test_detected_region_fills_unconfigured_region(self):
        adapter = _make_adapter()
        assert adapter.region is None

        mock_sdk, _ = self._mock_sdk_with_region("us-east-2")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert adapter.region == "us-east-2"
        assert info["configuration"]["region"] == "us-east-2"

        metadata = adapter.get_normalized_result_metadata(platform_info=info)
        assert metadata["platform_cloud"]["region"] == "us-east-2"
        assert metadata["platform_cloud"]["region_collection_status"] == "available"

    def test_configured_region_takes_precedence_over_detection(self):
        adapter = _make_adapter(region="us-west-2")

        mock_sdk, mock_workspace = self._mock_sdk_with_region("us-east-2")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert adapter.region == "us-west-2"
        assert info["configuration"]["region"] == "us-west-2"
        mock_workspace.metastores.summary.assert_not_called()

    def test_detection_failure_leaves_region_unset(self):
        adapter = _make_adapter()

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.side_effect = RuntimeError("SDK error")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert adapter.region is None
        assert info["configuration"]["region"] is None

    def test_detection_rejects_blank_region(self):
        adapter = _make_adapter()

        mock_sdk, _ = self._mock_sdk_with_region("   ")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        assert adapter.region is None
        assert info["configuration"]["region"] is None


class TestNormalizedResultMetadata:
    def test_sdk_observed_serverless_warehouse_maps_to_normalized_metadata(self):
        adapter = _make_adapter(
            http_path="/sql/1.0/warehouses/abc123",
            catalog="main",
            schema="bench",
            staging_root="dbfs:/Volumes/main/bench/stage",
            region="us-west-2",
            disable_result_cache=False,
        )

        mock_warehouse = MagicMock()
        mock_warehouse.id = "abc123"
        mock_warehouse.name = "Serverless Warehouse"
        mock_warehouse.cluster_size = "2X-Small"
        mock_warehouse.auto_stop_mins = 10
        mock_warehouse.min_num_clusters = 1
        mock_warehouse.max_num_clusters = 3
        mock_warehouse.enable_photon = True
        mock_warehouse.enable_serverless_compute = True
        mock_warehouse.warehouse_type = MagicMock()
        mock_warehouse.warehouse_type.value = "PRO"
        mock_warehouse.spot_instance_policy = MagicMock()
        mock_warehouse.spot_instance_policy.value = "COST_OPTIMIZED"
        mock_warehouse.channel = MagicMock()
        mock_warehouse.channel.name.value = "CHANNEL_NAME"
        mock_warehouse.channel.dbsql_version = "2024.15"
        mock_warehouse.state = MagicMock()
        mock_warehouse.state.value = "RUNNING"

        mock_workspace = MagicMock()
        mock_workspace.warehouses.get.return_value = mock_warehouse
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": mock_sdk}),
        ):
            info = adapter.get_platform_info(connection=None)

        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "serverless"
        assert metadata["platform_deployment"]["deployment_type"] == "serverless"
        assert metadata["platform_deployment"]["workspace_host"] == "test.cloud.databricks.com"
        assert metadata["platform_deployment"]["warehouse_id"] == "abc123"
        assert metadata["platform_cloud"]["provider"] == "aws"
        assert metadata["platform_cloud"]["region"] == "us-west-2"
        assert metadata["platform_cloud"]["region_collection_status"] == "available"
        assert metadata["platform_cloud"]["workspace"] == "test.cloud.databricks.com"
        assert metadata["platform_compute"]["warehouse"] == "Serverless Warehouse"
        assert metadata["platform_compute"]["warehouse_id"] == "abc123"
        assert metadata["platform_compute"]["warehouse_size"] == "2X-Small"
        assert metadata["platform_compute"]["warehouse_type"] == "SERVERLESS"
        assert metadata["platform_compute"]["serverless"] is True
        assert metadata["platform_compute"]["photon_enabled"] is True
        assert metadata["platform_compute"]["min_cluster_count"] == 1
        assert metadata["platform_compute"]["max_cluster_count"] == 3
        assert metadata["platform_compute"]["spot_instance_policy"] == "COST_OPTIMIZED"
        assert "result_cache_enabled" not in metadata["platform_compute"]
        assert metadata["platform_compute"]["collection_status"] == "available"
        assert metadata["platform_storage"]["table_format"] == "delta"
        assert metadata["platform_storage"]["staging_location"] == "dbfs:/Volumes/main/bench/stage"

    def test_requested_metadata_maps_when_sdk_unavailable(self):
        adapter = _make_adapter(
            http_path="/sql/1.0/warehouses/requested-wh",
            catalog="main",
            schema="bench",
            uc_catalog="main",
            uc_schema="bench",
            uc_volume="stage",
            cluster_size="Large",
            auto_terminate_minutes=45,
            disable_result_cache=True,
        )

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=None)

        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["deployment_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["warehouse_id"] == "requested-wh"
        assert metadata["platform_compute"]["warehouse_id"] == "requested-wh"
        assert metadata["platform_compute"]["warehouse_size"] == "Large"
        assert "serverless" not in metadata["platform_compute"]
        assert metadata["platform_compute"]["auto_stop_mins"] == 45
        assert "result_cache_enabled" not in metadata["platform_compute"]
        assert metadata["platform_compute"]["warehouse_metadata_collection_status"] == "unavailable"
        assert metadata["platform_compute"]["collection_status"] == "partial"
        assert metadata["platform_cloud"]["region_collection_status"] == "unavailable"
        assert metadata["platform_storage"]["staging_location"] == "dbfs:/Volumes/main/bench/stage"
        assert metadata["platform_storage"]["uc_catalog"] == "main"
        assert metadata["platform_storage"]["uc_schema"] == "bench"
        assert metadata["platform_storage"]["uc_volume"] == "stage"


class TestResolveStageRoot:
    def test_uses_dbfs_target_from_databricks_path(self):
        from benchbox.utils.cloud_storage import DatabricksPath

        adapter = _make_adapter()
        dbfs_path = DatabricksPath("/local/data", dbfs_target="dbfs:/Volumes/cat/sch/vol/data")

        root = adapter._resolve_stage_root(dbfs_path)
        assert root == "dbfs:/Volumes/cat/sch/vol/data"

    def test_raises_when_no_staging_available(self, tmp_path):
        adapter = _make_adapter()
        adapter.staging_root = None
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        with pytest.raises(ValueError, match="staging location"):
            adapter._resolve_stage_root(tmp_path)


class TestExternalLocationFromFileUri:
    def test_trailing_slash_stripped(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        result = DatabricksAdapter._external_location_from_file_uri("dbfs:/Volumes/main/benchbox/lineitem.parquet/")
        assert not result.endswith("/")

    def test_non_parquet_raises(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        with pytest.raises(ValueError, match="Parquet"):
            DatabricksAdapter._external_location_from_file_uri("dbfs:/Volumes/main/benchbox/orders.csv")


class TestDatabricksAddCliArguments:
    def test_server_hostname_arg(self):
        import argparse

        from benchbox.platforms.databricks import DatabricksAdapter

        parser = argparse.ArgumentParser()
        DatabricksAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--server-hostname", "test.cloud.databricks.com"])
        assert args.server_hostname == "test.cloud.databricks.com"

    def test_http_path_arg(self):
        import argparse

        from benchbox.platforms.databricks import DatabricksAdapter

        parser = argparse.ArgumentParser()
        DatabricksAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--http-path", "/sql/1.0/warehouses/abc"])
        assert args.http_path == "/sql/1.0/warehouses/abc"

    def test_catalog_default(self):
        import argparse

        from benchbox.platforms.databricks import DatabricksAdapter

        parser = argparse.ArgumentParser()
        DatabricksAdapter.add_cli_arguments(parser)
        args = parser.parse_args([])
        assert args.catalog == "workspace"


class TestResolveStageRootCloudURI:
    def test_s3_uri_returned_as_is(self):
        adapter = _make_adapter()
        adapter.staging_root = "s3://my-bucket/data"
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        result = adapter._resolve_stage_root(Path("/local/data"))
        assert result == "s3://my-bucket/data"

    def test_abfss_uri_returned_as_is(self):
        adapter = _make_adapter()
        adapter.staging_root = "abfss://container@account.dfs.core.windows.net/path"
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        result = adapter._resolve_stage_root(Path("/local/data"))
        assert result == "abfss://container@account.dfs.core.windows.net/path"

    def test_uc_volume_triple_used_when_no_staging_root(self, tmp_path):
        adapter = _make_adapter()
        adapter.staging_root = None
        adapter.uc_catalog = "cat"
        adapter.uc_schema = "sch"
        adapter.uc_volume = "vol"

        result = adapter._resolve_stage_root(tmp_path)
        assert result == "dbfs:/Volumes/cat/sch/vol"

    def test_raises_when_no_cloud_staging_configured(self, tmp_path):
        adapter = _make_adapter()
        adapter.staging_root = None
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        with pytest.raises(ValueError, match="staging location"):
            adapter._resolve_stage_root(tmp_path)


class TestLoadSingleTable:
    def test_raises_runtime_error_when_table_missing_from_existing_tables(self):
        adapter = _make_adapter()
        adapter.uc_catalog = "main"
        adapter.uc_schema = "bench"
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        adapter.enable_delta_optimization = False

        cursor = MagicMock()
        connection = MagicMock()
        benchmark = MagicMock()
        benchmark.get_schema.side_effect = AttributeError("no schema")

        with pytest.raises(RuntimeError, match="ORDERS"):
            adapter._load_single_table(
                cursor=cursor,
                connection=connection,
                benchmark=benchmark,
                table_name="orders",
                file_path=Path("orders.tbl"),
                stage_root="dbfs:/Volumes/main/bench",
                existing_tables={"lineitem", "customer"},
            )

    def test_copy_into_sql_contains_table_and_uri(self):
        from unittest.mock import call, patch

        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        adapter.enable_delta_optimization = False

        cursor = MagicMock()
        cursor.fetchone.return_value = (42,)
        cursor.fetchall.side_effect = [
            [("id", "int", ""), ("amount", "decimal(8,2)", "")],
            [("orders.parquet", "LOADED", "", 42, "", "")],
        ]
        connection = MagicMock()
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {}

        with (
            patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0),
            patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.1),
        ):
            adapter._load_single_table(
                cursor=cursor,
                connection=connection,
                benchmark=benchmark,
                table_name="orders",
                file_path=Path("orders.parquet"),
                stage_root="dbfs:/Volumes/main/bench",
                existing_tables={"orders", "lineitem"},
            )

        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        copy_sql = next((s for s in executed_sqls if "COPY INTO" in s), None)
        assert copy_sql is not None, f"No COPY INTO found in: {executed_sqls}"
        assert "ORDERS" in copy_sql
        assert "dbfs:/Volumes/main/bench/orders.parquet" in copy_sql
        assert "FILEFORMAT = PARQUET" in copy_sql
        assert "CAST(`id` AS int)" in copy_sql

    def _copy_sql_for_csv(self, tmp_stem, metadata):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        adapter.enable_delta_optimization = False

        cursor = MagicMock()
        cursor.fetchone.return_value = (10,)
        cursor.fetchall.return_value = [("id", "int", "")]
        file_path = Path(f"{tmp_stem}.csv")
        ds = resolver_data_source(tmp_stem, file_path, metadata)
        adapter._load_single_table(
            cursor=cursor,
            connection=MagicMock(),
            benchmark=None,
            table_name=tmp_stem,
            file_path=file_path,
            stage_root="dbfs:/Volumes/main/bench",
            existing_tables={tmp_stem},
            data_source=ds,
        )
        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        copy_sql = next((s for s in executed_sqls if "COPY INTO" in s), None)
        assert copy_sql is not None, f"No COPY INTO found in: {executed_sqls}"
        return copy_sql

    def test_copy_into_honors_manifest_header(self):
        copy_sql = self._copy_sql_for_csv(
            "trips",
            {"csv_delimiter": ",", "csv_has_header": True, "csv_null_marker": ""},
        )
        assert "'header'='true'" in copy_sql

    def test_copy_into_header_csv_uses_cast_select(self):
        from unittest.mock import call, patch

        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        adapter.enable_delta_optimization = False

        cursor = MagicMock()
        cursor.fetchone.return_value = (10,)
        cursor.fetchall.return_value = [("id", "int", ""), ("amount", "decimal(8,2)", "")]
        file_path = Path("orders.csv")
        ds = resolver_data_source(
            "orders", file_path, {"csv_delimiter": ",", "csv_has_header": True, "csv_null_marker": ""}
        )
        with (
            patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0),
            patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.1),
        ):
            adapter._load_single_table(
                cursor=cursor,
                connection=MagicMock(),
                benchmark=None,
                table_name="orders",
                file_path=file_path,
                stage_root="dbfs:/Volumes/main/bench",
                existing_tables={"orders"},
                data_source=ds,
            )
        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        copy_sql = next((s for s in executed_sqls if "COPY INTO" in s), None)
        assert copy_sql is not None, f"No COPY INTO found in: {executed_sqls}"
        assert "FILEFORMAT = CSV" in copy_sql
        assert "CAST(`id` AS int)" in copy_sql
        assert "CAST(`amount` AS decimal(8,2))" in copy_sql

    def test_copy_into_defaults_to_no_header(self):
        copy_sql = self._copy_sql_for_csv("orders", {})
        assert "'header'='false'" in copy_sql


class TestCreateExternalTables:
    def test_happy_path_issues_create_table_sql(self):
        from unittest.mock import patch

        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"

        data_files = {"orders": "dbfs:/Volumes/main/bench/orders.parquet"}

        cursor = MagicMock()
        cursor.fetchall.return_value = [("main", "orders", False)]
        cursor.fetchone.return_value = (1500,)
        connection = MagicMock()
        connection.cursor.return_value = cursor

        benchmark = MagicMock()

        with (
            patch.object(adapter, "_resolve_databricks_data_files", return_value=data_files),
            patch.object(adapter, "_resolve_stage_root", return_value="dbfs:/Volumes/main/bench"),
            patch.object(adapter, "_maybe_upload_to_uc_volume", return_value=data_files),
        ):
            table_stats, total_time, _ = adapter.create_external_tables(
                benchmark=benchmark,
                connection=connection,
                data_dir=Path("/local/data"),
            )

        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        create_sql = next((s for s in executed_sqls if "CREATE TABLE" in s and "PARQUET" in s), None)
        assert create_sql is not None, f"No CREATE TABLE USING PARQUET found in: {executed_sqls}"
        assert "ORDERS" in create_sql
        assert "orders" in table_stats


class TestConfigureForBenchmark:
    def test_spark_configs_are_set_via_cursor(self):
        adapter = _make_adapter()
        adapter.disable_result_cache = False
        adapter.spark_configs = {
            "spark.databricks.adaptive.enabled": "true",
            "spark.sql.shuffle.partitions": "200",
        }

        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value = cursor

        adapter.configure_for_benchmark(connection=connection, benchmark_type="tpch")

        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        assert any("spark.databricks.adaptive.enabled" in s for s in executed_sqls)
        assert any("spark.sql.shuffle.partitions" in s for s in executed_sqls)

    def test_disable_result_cache_executes_set_statement(self):
        adapter = _make_adapter()
        adapter.disable_result_cache = True
        adapter.spark_configs = {}

        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value = cursor
        cursor.fetchone.return_value = ("use_cached_result", "false")

        adapter.configure_for_benchmark(connection=connection, benchmark_type="tpch")

        executed_sqls = [str(c.args[0]) for c in cursor.execute.call_args_list]
        assert any("use_cached_result" in s and "false" in s for s in executed_sqls)


class TestExecuteQueryFailurePath:
    def test_returns_failed_dict_on_exception(self):
        from unittest.mock import patch

        adapter = _make_adapter()

        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("query execution error")
        connection = MagicMock()
        connection.cursor.return_value = cursor

        with (
            patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0),
            patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.5),
        ):
            result = adapter.execute_query(
                connection=connection,
                query="SELECT 1",
                query_id="Q1",
            )

        assert result["status"] == "FAILED"
        assert "error" in result
        assert "Databricks session cache control failed" in result["error"]
        assert result["execution_time_seconds"] == 0.0
        assert result["query_id"] == "Q1"


class TestResolveClusteringStrategy:
    def test_returns_none_when_no_effective_config(self):
        adapter = _make_adapter()
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "none"

    def test_returns_none_when_platform_opts_is_none(self):
        adapter = _make_adapter()
        effective_config = MagicMock()
        effective_config.platform_optimizations = None
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "none"

    def test_liquid_enabled_flag_returns_liquid_clustering(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "z_order"
        platform_opts.liquid_clustering_enabled = True
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = False
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "liquid_clustering"

    def test_liquid_columns_present_returns_liquid_clustering(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "z_order"
        platform_opts.liquid_clustering_enabled = False
        platform_opts.liquid_clustering_columns = ["col1", "col2"]
        platform_opts.z_ordering_enabled = False
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "liquid_clustering"

    def test_liquid_rejects_z_order_enabled(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "z_order"
        platform_opts.liquid_clustering_enabled = True
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = True
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config),
            pytest.raises(ValueError, match="Liquid Clustering cannot be combined"),
        ):
            adapter._resolve_databricks_clustering_strategy()

    def test_liquid_auto_strategy_returns_liquid_auto(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "liquid_clustering_auto"
        platform_opts.liquid_clustering_enabled = True
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = False
        platform_opts.z_ordering_columns = []
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "liquid_clustering_auto"

    def test_strategy_none_returns_none(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "none"
        platform_opts.liquid_clustering_enabled = False
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = False
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "none"

    def test_z_ordering_enabled_returns_z_order(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "z_order"
        platform_opts.liquid_clustering_enabled = False
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = True
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "z_order"

    def test_default_fallback_returns_z_order(self):
        adapter = _make_adapter()
        platform_opts = MagicMock()
        platform_opts.databricks_clustering_strategy = "z_order"
        platform_opts.liquid_clustering_enabled = False
        platform_opts.liquid_clustering_columns = []
        platform_opts.z_ordering_enabled = False
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts
        with patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config):
            result = adapter._resolve_databricks_clustering_strategy()
        assert result == "z_order"


class TestFromConfig:
    def test_nested_tuning_key_order_keeps_generated_schema_name(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        tuning_a = {
            "platform_optimizations": {"z_ordering_enabled": True, "auto_optimize_enabled": False},
            "table_tunings": {"lineitem": {"sorting": [{"name": "l_orderkey", "order": 1}]}},
        }
        tuning_b = {
            "table_tunings": {"lineitem": {"sorting": [{"order": 1, "name": "l_orderkey"}]}},
            "platform_optimizations": {"auto_optimize_enabled": False, "z_ordering_enabled": True},
        }
        common = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "catalog": "main",
        }

        adapter_a = DatabricksAdapter.from_config({**common, "tuning_config": tuning_a})
        adapter_b = DatabricksAdapter.from_config({**common, "tuning_config": tuning_b})

        assert adapter_a.schema == adapter_b.schema

    def test_passes_explicit_credentials_to_adapter(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/real123",
            "access_token": "real-token",
            "catalog": "production",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.server_hostname == "real.cloud.databricks.com"
        assert adapter.http_path == "/sql/1.0/warehouses/real123"
        assert adapter.access_token == "real-token"
        assert adapter.catalog == "production"

    def test_skips_placeholder_server_hostname_triggers_autodetect(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "your-workspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/real123",
            "access_token": "real-token",
        }
        auto_config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/real123",
            "access_token": "real-token",
        }
        with patch.object(DatabricksAdapter, "_auto_detect_databricks_config", return_value=auto_config) as mock_detect:
            adapter = DatabricksAdapter.from_config(config)
        mock_detect.assert_called_once()
        assert adapter.http_path == "/sql/1.0/warehouses/real123"

    def test_generates_schema_from_benchmark_context(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "catalog": "main",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.schema is not None
        assert adapter.schema != ""

    def test_passes_runtime_metadata_options_through(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "region": "us-west-2",
            "cluster_size": "Large",
            "auto_terminate_minutes": 45,
            "disable_result_cache": False,
            "enable_delta_optimization": False,
            "delta_auto_optimize": False,
            "delta_auto_compact": False,
        }
        adapter = DatabricksAdapter.from_config(config)

        assert adapter.region == "us-west-2"
        assert adapter.cluster_size == "Large"
        assert adapter.auto_terminate_minutes == 45
        assert adapter.disable_result_cache is False
        assert adapter.enable_delta_optimization is False
        assert adapter.delta_auto_optimize is False
        assert adapter.delta_auto_compact is False

    def test_honors_explicit_non_default_schema(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "schema": "my_custom_schema",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.schema == "my_custom_schema"

    def test_no_benchmark_context_uses_provided_schema(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "schema": "my_schema",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.schema == "my_schema"

    def test_no_schema_no_context_defaults_to_benchbox(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.schema == "benchbox"

    def test_passes_through_uc_and_staging_config(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        config = {
            "server_hostname": "real.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "tok",
            "uc_catalog": "cat",
            "uc_schema": "sch",
            "uc_volume": "vol",
            "staging_root": "s3://bucket/path",
        }
        adapter = DatabricksAdapter.from_config(config)
        assert adapter.uc_catalog == "cat"
        assert adapter.uc_schema == "sch"
        assert adapter.uc_volume == "vol"
        assert adapter.staging_root == "s3://bucket/path"

    def test_auto_detect_fills_in_credentials(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        auto_config = {
            "server_hostname": "auto.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/auto123",
            "access_token": "auto-token",
        }
        config = {}
        with patch.object(DatabricksAdapter, "_auto_detect_databricks_config", return_value=auto_config):
            adapter = DatabricksAdapter.from_config(config)
        assert adapter.server_hostname == "auto.cloud.databricks.com"
        assert adapter.access_token == "auto-token"


class TestCreateAdminConnection:
    def test_connects_with_correct_params(self):
        adapter = _make_adapter()

        mock_connection = MagicMock()
        with patch("benchbox.platforms.databricks.adapter.databricks_sql") as mock_sql:
            mock_sql.connect.return_value = mock_connection
            adapter._create_admin_connection()

        mock_sql.connect.assert_called_once()
        call_kwargs = mock_sql.connect.call_args
        assert call_kwargs.kwargs.get("server_hostname") == "test.cloud.databricks.com"
        assert call_kwargs.kwargs.get("http_path") == "/sql/1.0/warehouses/abc123"
        assert call_kwargs.kwargs.get("access_token") == "tok"

    def test_passes_user_agent(self):
        adapter = _make_adapter()

        with patch("benchbox.platforms.databricks.adapter.databricks_sql") as mock_sql:
            mock_sql.connect.return_value = MagicMock()
            adapter._create_admin_connection()

        call_kwargs = mock_sql.connect.call_args
        assert "BenchBox" in call_kwargs.kwargs.get("user_agent_entry", "")


class TestCheckServerDatabaseExists:
    def test_returns_true_when_catalog_and_schema_exist(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "benchbox"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [
            [("main",), ("hive_metastore",)],
            [("benchbox",), ("default",)],
        ]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is True

    def test_returns_false_when_catalog_missing(self):
        adapter = _make_adapter()
        adapter.catalog = "missing_cat"
        adapter.schema = "benchbox"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("main",)]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is False

    def test_returns_false_when_schema_missing(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "missing_schema"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [
            [("main",)],
            [("other_schema",)],
        ]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is False

    def test_returns_false_on_exception(self):
        adapter = _make_adapter()
        with patch.object(adapter, "_create_admin_connection", side_effect=RuntimeError("connect fail")):
            result = adapter.check_server_database_exists()
        assert result is False


class TestDropDatabase:
    def test_issues_drop_schema_cascade(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "benchbox"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            adapter.drop_database()

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("DROP SCHEMA" in s and "CASCADE" in s for s in executed)

    def test_raises_on_connection_failure(self):
        adapter = _make_adapter()
        with patch.object(adapter, "_create_admin_connection", side_effect=RuntimeError("fail")):
            with pytest.raises(Exception):
                adapter.drop_database()


class TestBuildCtasSortSql:
    def _make_col(self, name):
        col = MagicMock()
        col.name = name
        return col

    def test_returns_none_when_mode_is_off(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", None)):
            result = adapter._build_ctas_sort_sql("ORDERS", [self._make_col("o_orderdate")])
        assert result is None

    def test_ctas_method_generates_select_order_by(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("enabled", "ctas")):
            result = adapter._build_ctas_sort_sql("ORDERS", [self._make_col("o_orderdate")])
        assert result is not None
        assert "CREATE OR REPLACE TABLE" in result
        assert "ORDER BY" in result
        assert "o_orderdate" in result

    def test_z_order_method_generates_optimize_zorder(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("enabled", "z_order")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [self._make_col("l_shipdate")])
        assert result is not None
        assert "OPTIMIZE" in result
        assert "ZORDER BY" in result
        assert "l_shipdate" in result

    def test_liquid_clustering_method_generates_alter_cluster_by(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("enabled", "liquid_clustering")):
            result = adapter._build_ctas_sort_sql("CUSTOMER", [self._make_col("c_custkey")])
        assert result is not None
        assert "ALTER TABLE" in result
        assert "CLUSTER BY" in result

    def test_unknown_method_raises_value_error(self):
        adapter = _make_adapter()
        with (
            patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("enabled", "unknown_method")),
            pytest.raises(ValueError, match="not supported"),
        ):
            adapter._build_ctas_sort_sql("ORDERS", [self._make_col("col1")])


class TestSchemaDdlOptions:
    def _convert(self, adapter, sql):
        from benchbox.utils.dialect_utils import translate_ddl

        return translate_ddl(sql, "databricks", "standard", **adapter._schema_ddl_options(sql))

    def test_adds_using_delta_when_missing(self):
        adapter = _make_adapter()
        (result,) = self._convert(adapter, "CREATE TABLE orders (id INT, val VARCHAR(100))")
        assert "USING DELTA" in result

    def test_adds_or_replace_when_missing(self):
        adapter = _make_adapter()
        (result,) = self._convert(adapter, "CREATE TABLE orders (id INT)")
        assert "CREATE OR REPLACE TABLE" in result

    def test_if_not_exists_kept_without_or_replace(self):
        adapter = _make_adapter()
        (result,) = self._convert(adapter, "CREATE TABLE IF NOT EXISTS DimDate (SK_DateID BIGINT)")
        assert "OR REPLACE" not in result
        assert "IF NOT EXISTS" in result
        assert "USING DELTA" in result

    def test_adds_tblproperties_when_auto_optimize_enabled(self):
        adapter = _make_adapter()
        adapter.delta_auto_optimize = True
        (result,) = self._convert(adapter, "CREATE TABLE orders (id INT)")
        assert "TBLPROPERTIES" in result
        assert "autoOptimize" in result

    def test_every_statement_is_converted(self):
        adapter = _make_adapter()
        sql = (
            "CREATE TABLE orders (id INT, val VARCHAR(100));"
            "CREATE TABLE customers (id INT, name VARCHAR(50));"
            "CREATE TABLE lineitem (l_id INT)"
        )
        results = self._convert(adapter, sql)
        assert len(results) == 3
        assert all("CREATE OR REPLACE TABLE" in r for r in results)
        assert all("USING DELTA" in r for r in results)


class TestOptimizeAndVacuumTable:
    def test_optimize_table_executes_optimize(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.optimize_table(mock_conn, "orders")

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("OPTIMIZE" in s and "ORDERS" in s for s in executed)

    def test_optimize_table_skips_when_disabled(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.optimize_table(mock_conn, "orders")

        mock_cursor.execute.assert_not_called()

    def test_vacuum_table_executes_vacuum_retain(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.vacuum_table(mock_conn, "orders", hours=72)

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("VACUUM" in s and "ORDERS" in s and "72" in s for s in executed)

    def test_vacuum_table_skips_when_disabled(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.vacuum_table(mock_conn, "orders")

        mock_cursor.execute.assert_not_called()


class TestAnalyzeTable:
    def test_executes_analyze_table(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_conn, "lineitem")

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("ANALYZE TABLE" in s and "LINEITEM" in s for s in executed)

    def test_analyze_table_warning_on_failure(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("analyze failed")

        adapter.analyze_table(mock_conn, "lineitem")
        mock_cursor.execute.assert_called_once()


class TestApplyTableTunings:
    def _make_tuning(self, table_name="orders"):
        tuning = MagicMock()
        tuning.table_name = table_name
        tuning.has_any_tuning.return_value = True
        return tuning

    def _make_col(self, name, order=0):
        col = MagicMock()
        col.name = name
        col.order = order
        return col

    def test_no_tuning_returns_early(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.apply_table_tunings(None, mock_conn)
        mock_cursor.execute.assert_not_called()

    def test_liquid_clustering_applied_for_delta_table(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = True

        tuning = self._make_tuning("orders")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("Provider", "DELTA")]

        from unittest.mock import patch as _patch

        col1 = self._make_col("o_orderdate", 0)

        try:
            from benchbox.core.tuning.interface import TuningType

            tuning.get_columns_by_type.side_effect = lambda t: [col1] if t == TuningType.CLUSTERING else []
        except ImportError:
            tuning.get_columns_by_type.return_value = [col1]

        platform_opts = MagicMock()
        platform_opts.liquid_clustering_enabled = True
        platform_opts.liquid_clustering_columns = []
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config),
            patch.object(adapter, "_resolve_databricks_clustering_strategy", return_value="liquid_clustering"),
        ):
            adapter.apply_table_tunings(tuning, mock_conn)

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("ALTER TABLE" in s and "CLUSTER BY" in s for s in executed)

    def test_z_order_applied_for_delta_table(self):
        adapter = _make_adapter()
        adapter.enable_delta_optimization = True

        tuning = self._make_tuning("lineitem")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("Provider", "DELTA")]

        col1 = self._make_col("l_shipdate", 0)

        try:
            from benchbox.core.tuning.interface import TuningType

            tuning.get_columns_by_type.side_effect = lambda t: [col1] if t == TuningType.CLUSTERING else []
        except ImportError:
            tuning.get_columns_by_type.return_value = [col1]

        platform_opts = MagicMock()
        platform_opts.liquid_clustering_enabled = False
        platform_opts.liquid_clustering_columns = []
        effective_config = MagicMock()
        effective_config.platform_optimizations = platform_opts

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=effective_config),
            patch.object(adapter, "_resolve_databricks_clustering_strategy", return_value="z_order"),
        ):
            adapter.apply_table_tunings(tuning, mock_conn)

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("ZORDER BY" in s for s in executed)


class TestEnsureUcVolumeExists:
    def test_creates_schema_and_volume(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter._ensure_uc_volume_exists("dbfs:/Volumes/cat/sch/vol", mock_conn)

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("CREATE SCHEMA" in s and "cat.sch" in s for s in executed)
        assert any("CREATE VOLUME" in s and "cat.sch.vol" in s for s in executed)

    def test_raises_on_invalid_path_no_volumes_prefix(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Invalid UC Volume path"):
            adapter._ensure_uc_volume_exists("dbfs:/BadPath/cat/sch/vol", mock_conn)

    def test_raises_on_short_path(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Invalid UC Volume path"):
            adapter._ensure_uc_volume_exists("dbfs:/Volumes/cat", mock_conn)

    def test_permission_error_raises_value_error(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Permission denied")

        with pytest.raises(ValueError, match="Permission denied"):
            adapter._ensure_uc_volume_exists("dbfs:/Volumes/cat/sch/vol", mock_conn)


class TestUploadManifestToUcVolume:
    def test_uploads_to_volume_root(self, tmp_path):
        adapter = _make_adapter()

        manifest_data = {"tables": {"orders": [{"path": "orders.parquet", "rows": 100}]}}
        manifest_path = tmp_path / "_datagen_manifest.json"
        import json

        manifest_path.write_text(json.dumps(manifest_data))

        mock_workspace = MagicMock()

        adapter._upload_manifest_to_uc_volume(
            manifest_path,
            "dbfs:/Volumes/cat/sch/vol",
            mock_workspace,
        )

        mock_workspace.files.upload.assert_called_once()
        call_args = mock_workspace.files.upload.call_args
        target = call_args.args[0]
        assert "/Volumes/cat/sch/vol" in target
        assert "_datagen_manifest.json" in target

    def test_raises_on_upload_failure(self, tmp_path):
        adapter = _make_adapter()

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_bytes(b'{"tables":{}}')

        mock_workspace = MagicMock()
        mock_workspace.files.upload.side_effect = RuntimeError("upload failed")

        with pytest.raises(RuntimeError, match="Manifest upload failed"):
            adapter._upload_manifest_to_uc_volume(manifest_path, "dbfs:/Volumes/cat/sch/vol", mock_workspace)


class TestUploadSingleFile:
    def test_returns_dbfs_uri_on_success(self, tmp_path):
        adapter = _make_adapter()

        data_file = tmp_path / "orders.parquet"
        data_file.write_bytes(b"fake parquet content")

        mock_workspace = MagicMock()

        uri = adapter._upload_single_file(
            data_file, "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace
        )

        assert uri is not None
        assert uri.startswith("dbfs:")
        assert "orders.parquet" in uri
        mock_workspace.files.upload.assert_called_once()

    def test_skips_empty_file(self, tmp_path):
        adapter = _make_adapter()

        empty_file = tmp_path / "empty.parquet"
        empty_file.write_bytes(b"")

        mock_workspace = MagicMock()

        uri = adapter._upload_single_file(
            empty_file, "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace
        )

        assert uri is None
        mock_workspace.files.upload.assert_not_called()

    def test_raises_runtime_error_on_upload_failure(self, tmp_path):
        adapter = _make_adapter()

        data_file = tmp_path / "orders.parquet"
        data_file.write_bytes(b"content")

        mock_workspace = MagicMock()
        mock_workspace.files.upload.side_effect = RuntimeError("S3 error")

        with pytest.raises(RuntimeError, match="Failed to upload"):
            adapter._upload_single_file(data_file, "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace)


class TestDetectShardedFiles:
    def test_non_sharded_file_returns_false(self, tmp_path):
        data_file = tmp_path / "orders.tbl"
        data_file.write_bytes(b"data")

        adapter = _make_adapter()
        is_sharded, pattern, chunks = adapter._detect_sharded_files(data_file, "orders")

        assert is_sharded is False
        assert chunks == []

    def test_sharded_file_with_numeric_suffix_returns_true(self, tmp_path):
        for i in range(1, 3):
            f = tmp_path / f"orders.tbl.{i}"
            f.write_bytes(b"chunk")

        data_file = tmp_path / "orders.tbl.1"
        adapter = _make_adapter()
        is_sharded, pattern, chunks = adapter._detect_sharded_files(data_file, "orders")

        assert is_sharded is True
        assert len(chunks) == 2

    def test_compressed_sharded_file_detected(self, tmp_path):
        for i in range(1, 3):
            f = tmp_path / f"orders.tbl.{i}.zst"
            f.write_bytes(b"compressed chunk")

        data_file = tmp_path / "orders.tbl.1.zst"
        adapter = _make_adapter()
        is_sharded, pattern, chunks = adapter._detect_sharded_files(data_file, "orders")

        assert is_sharded is True
        assert len(chunks) == 2
        assert "*.zst" in pattern


class TestResolveDatabricksDataFiles:
    def test_delegates_to_resolver(self):
        adapter = _make_adapter()
        benchmark = MagicMock()
        data_source = MagicMock()
        data_source.tables = {"orders": [Path("orders.parquet")]}

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_resolver_cls:
            mock_resolver = mock_resolver_cls.return_value
            mock_resolver.resolve.return_value = data_source

            result = adapter._resolve_databricks_data_files(benchmark, Path("/data"))

        mock_resolver.resolve.assert_called_once_with(benchmark, Path("/data"))
        assert result.tables == {"orders": Path("orders.parquet")}

    def test_raises_when_no_data_files_found(self, tmp_path):
        adapter = _make_adapter()
        benchmark = MagicMock()

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_databricks_data_files(benchmark, tmp_path)

    def test_preserves_multi_file_results(self):
        adapter = _make_adapter()
        benchmark = MagicMock()
        data_source = MagicMock()
        data_source.tables = {"orders": [Path("orders.1.parquet"), Path("orders.2.parquet")]}

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = data_source

            result = adapter._resolve_databricks_data_files(benchmark, Path("/data"))

        assert result.tables == {"orders": [Path("orders.1.parquet"), Path("orders.2.parquet")]}


class TestGetExistingTables:
    def test_returns_non_temp_tables(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("bench", "orders", False),
            ("bench", "lineitem", False),
            ("bench", "tmp_view", True),
        ]

        result = adapter._get_existing_tables(mock_conn)

        assert "orders" in result
        assert "lineitem" in result
        assert "tmp_view" not in result

    def test_returns_empty_list_on_exception(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = RuntimeError("fail")

        result = adapter._get_existing_tables(mock_conn)
        assert result == []


class TestGetPlatformMetadata:
    def test_returns_spark_version_and_catalogs(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ("14.3 LTS",),
            ("main", "benchbox"),
        ]
        mock_cursor.fetchall.side_effect = [
            [("current_database",)],
            [("spark.sql.shuffle.partitions", "200")],
        ]

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            metadata = adapter._get_platform_metadata(mock_conn)

        assert metadata["platform"] == "Databricks"
        assert "spark_version" in metadata

    def test_handles_metadata_exception_gracefully(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("connection lost")

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            metadata = adapter._get_platform_metadata(mock_conn)

        assert "metadata_error" in metadata


class TestApplyPlatformOptimizations:
    def test_no_op_when_config_is_none(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_platform_optimizations(None, mock_conn)
            mock_info.assert_not_called()

    def test_logs_when_config_provided(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        mock_config = MagicMock()
        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_platform_optimizations(mock_config, mock_conn)
            mock_info.assert_called_once()


class TestApplyUnifiedTuning:
    def test_no_op_when_config_is_none(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        with (
            patch.object(adapter, "apply_platform_optimizations") as mock_plat,
            patch.object(adapter, "apply_constraint_configuration") as mock_constraint,
        ):
            adapter.apply_unified_tuning(None, mock_conn)
            mock_plat.assert_not_called()
            mock_constraint.assert_not_called()

    def test_calls_apply_platform_optimizations(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()

        unified_config = MagicMock()
        unified_config.primary_keys = None
        unified_config.foreign_keys = None
        unified_config.table_tunings = {}
        unified_config.platform_optimizations = MagicMock()

        with (
            patch.object(adapter, "apply_platform_optimizations") as mock_plat,
            patch.object(adapter, "apply_constraint_configuration") as mock_constraint,
        ):
            adapter.apply_unified_tuning(unified_config, mock_conn)

        mock_plat.assert_called_once()
        mock_constraint.assert_called_once()


class TestApplyConstraintConfiguration:
    def test_no_op_when_pk_and_fk_are_none(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_constraint_configuration(None, None, mock_conn)
            mock_info.assert_not_called()

    def test_logs_when_pk_enabled(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()

        pk_config = MagicMock()
        pk_config.enabled = True

        fk_config = MagicMock()
        fk_config.enabled = False

        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_constraint_configuration(pk_config, fk_config, mock_conn)
            mock_info.assert_called_once()


class TestIsCloudUri:
    def test_s3_uri_is_cloud(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        assert DatabricksAdapter._is_cloud_uri("s3://bucket/path") is True

    def test_abfss_uri_is_cloud(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        assert DatabricksAdapter._is_cloud_uri("abfss://container@account/path") is True

    def test_dbfs_uri_is_cloud(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        assert DatabricksAdapter._is_cloud_uri("dbfs:/Volumes/cat/sch/vol") is True

    def test_local_path_is_not_cloud(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        assert DatabricksAdapter._is_cloud_uri("/local/path/data") is False


class TestValidateExternalTableRequirements:
    def test_raises_when_no_staging_no_uc_volume(self):
        adapter = _make_adapter()
        adapter.staging_root = None
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        with pytest.raises(ValueError, match="external mode requires cloud staging"):
            adapter.validate_external_table_requirements()

    def test_passes_when_staging_root_set(self):
        adapter = _make_adapter()
        adapter.staging_root = "s3://bucket/path"
        adapter.uc_catalog = None
        adapter.uc_schema = None
        adapter.uc_volume = None

        with patch.object(adapter, "_is_cloud_uri", wraps=adapter._is_cloud_uri) as mock_check:
            adapter.validate_external_table_requirements()
            mock_check.assert_called_once_with("s3://bucket/path")

    def test_passes_when_uc_volume_configured(self):
        adapter = _make_adapter()
        adapter.staging_root = None
        adapter.uc_catalog = "cat"
        adapter.uc_schema = "sch"
        adapter.uc_volume = "vol"

        adapter.validate_external_table_requirements()
        assert adapter.uc_catalog == "cat" and adapter.uc_schema == "sch" and adapter.uc_volume == "vol"


class TestGetPlatformInfoWithConnection:
    def test_captures_version_from_connection(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("14.3 LTS (apache-spark-3.5.0-bin-hadoop3)",)

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_version"] is not None

    def test_handles_version_query_exception(self):
        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("query failed")

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_version"] is None


class TestResolveFileUriAndDelimiter:
    def test_dbfs_volumes_path_used_directly(self):
        adapter = _make_adapter()
        file_uri, filename, delimiter = adapter._resolve_file_uri_and_delimiter(
            "dbfs:/Volumes/cat/sch/vol/orders.parquet", "dbfs:/Volumes/cat/sch/vol"
        )
        assert file_uri == "dbfs:/Volumes/cat/sch/vol/orders.parquet"
        assert "orders" in filename

    def test_local_path_combined_with_stage_root(self):
        adapter = _make_adapter()
        file_uri, filename, delimiter = adapter._resolve_file_uri_and_delimiter(
            Path("orders.tbl"), "dbfs:/Volumes/cat/sch/vol"
        )
        assert file_uri == "dbfs:/Volumes/cat/sch/vol/orders.tbl"
        assert delimiter == "|"

    def test_csv_non_tpc_file_uses_comma_delimiter(self):
        adapter = _make_adapter()
        file_uri, filename, delimiter = adapter._resolve_file_uri_and_delimiter(
            Path("data.csv"), "dbfs:/Volumes/cat/sch/vol"
        )
        assert delimiter == ","

    def test_dbfs_multi_file_list_uses_shared_directory(self):
        adapter = _make_adapter()
        files = [
            "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA/part-00000.parquet",
            "dbfs:/Volumes/cat/sch/vol/orders/region=EUROPE/part-00000.parquet",
        ]
        file_uri, filename, delimiter = adapter._resolve_file_uri_and_delimiter(
            files,
            "dbfs:/Volumes/cat/sch/vol",
        )
        assert file_uri == "dbfs:/Volumes/cat/sch/vol/orders"
        assert filename == "orders"
        assert delimiter == ","
        assert adapter._expand_copy_sources(files, "dbfs:/Volumes/cat/sch/vol", file_uri) == [file_uri]

    def test_flat_multi_file_list_expands_to_exact_uris(self):
        adapter = _make_adapter()
        files = [
            "dbfs:/Volumes/cat/sch/vol/flights_0001_2024_12.csv.gz",
            "dbfs:/Volumes/cat/sch/vol/flights_0002_2024_11.csv.gz",
        ]
        file_uri, _filename, _delimiter = adapter._resolve_file_uri_and_delimiter(
            files,
            "dbfs:/Volumes/cat/sch/vol",
        )

        assert file_uri == "dbfs:/Volumes/cat/sch/vol"
        assert adapter._expand_copy_sources(files, "dbfs:/Volumes/cat/sch/vol", file_uri) == files

    def test_files_within_one_partition_keep_partition_directory_source(self):
        adapter = _make_adapter()
        files = [
            "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA/part-00000.parquet",
            "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA/part-00001.parquet",
        ]
        file_uri, _filename, _delimiter = adapter._resolve_file_uri_and_delimiter(
            files,
            "dbfs:/Volumes/cat/sch/vol",
        )

        assert file_uri == "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA"
        assert adapter._expand_copy_sources(files, "dbfs:/Volumes/cat/sch/vol", file_uri) == [file_uri]

    def test_wildcard_shard_set_expands_to_exact_per_file_uris(self):
        adapter = _make_adapter()
        files = [
            "dbfs:/Volumes/cat/sch/vol/customer_demographics_1_10.dat.gz",
            "dbfs:/Volumes/cat/sch/vol/customer_demographics_2_10.dat.gz",
        ]
        file_uri, filename, delimiter = adapter._resolve_file_uri_and_delimiter(files, "dbfs:/Volumes/cat/sch/vol")
        assert "*" in file_uri
        sources = adapter._expand_copy_sources(files, "dbfs:/Volumes/cat/sch/vol", file_uri)
        assert sources == files
        assert all("*" not in source for source in sources)

    def test_non_wildcard_source_passes_through(self):
        adapter = _make_adapter()
        assert adapter._expand_copy_sources(
            "dbfs:/Volumes/cat/sch/vol/orders.tbl",
            "dbfs:/Volumes/cat/sch/vol",
            "dbfs:/Volumes/cat/sch/vol/orders.tbl",
        ) == ["dbfs:/Volumes/cat/sch/vol/orders.tbl"]


class TestCloseConnection:
    def test_closes_valid_connection(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        adapter.close_connection(mock_conn)
        mock_conn.close.assert_called_once()

    def test_handles_none_connection(self):
        adapter = _make_adapter()
        with patch.object(adapter.logger, "warning") as mock_warn:
            adapter.close_connection(None)
            mock_warn.assert_not_called()

    def test_handles_close_exception(self):
        adapter = _make_adapter()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("close failed")
        with patch.object(adapter.logger, "warning") as mock_warn:
            adapter.close_connection(mock_conn)
            mock_warn.assert_called_once()
            assert "close failed" in mock_warn.call_args[0][0]


class TestBasicProperties:
    def test_get_target_dialect_returns_databricks(self):
        adapter = _make_adapter()
        assert adapter.get_target_dialect() == "databricks"

    def test_platform_name_returns_databricks(self):
        adapter = _make_adapter()
        assert adapter.platform_name == "Databricks"


class TestGetRemoteFileUrisFromManifest:
    def test_single_file_per_table(self):
        adapter = _make_adapter()
        manifest = {
            "tables": {
                "orders": [{"path": "orders.parquet"}],
                "lineitem": [{"path": "lineitem.parquet"}],
            }
        }
        result = adapter._get_remote_file_uris_from_manifest("dbfs:/Volumes/cat/sch/vol", manifest)

        assert "orders" in result
        assert "lineitem" in result
        assert result["orders"] == "dbfs:/Volumes/cat/sch/vol/orders.parquet"

    def test_sharded_files_get_exact_uri_list(self):
        adapter = _make_adapter()
        manifest = {
            "tables": {
                "orders": [
                    {"path": "orders.tbl.1.zst"},
                    {"path": "orders.tbl.2.zst"},
                ]
            }
        }
        result = adapter._get_remote_file_uris_from_manifest("dbfs:/Volumes/cat/sch/vol", manifest)

        assert result["orders"] == [
            "dbfs:/Volumes/cat/sch/vol/orders.tbl.1.zst",
            "dbfs:/Volumes/cat/sch/vol/orders.tbl.2.zst",
        ]

    def test_empty_table_entries_skipped(self):
        adapter = _make_adapter()
        manifest = {
            "tables": {
                "orders": [],
            }
        }
        result = adapter._get_remote_file_uris_from_manifest("dbfs:/Volumes/cat/sch/vol", manifest)
        assert "orders" not in result

    def test_non_sharded_multi_file_entries_preserved_as_list(self):
        adapter = _make_adapter()
        manifest = {
            "tables": {
                "orders": [
                    {"path": "orders/region=ASIA/part-00000.parquet"},
                    {"path": "orders/region=EUROPE/part-00000.parquet"},
                ]
            }
        }
        result = adapter._get_remote_file_uris_from_manifest("dbfs:/Volumes/cat/sch/vol", manifest)

        assert result["orders"] == [
            "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA/part-00000.parquet",
            "dbfs:/Volumes/cat/sch/vol/orders/region=EUROPE/part-00000.parquet",
        ]


class TestSupportsTuningType:
    def test_returns_true_for_clustering(self):
        adapter = _make_adapter()
        try:
            from benchbox.core.tuning.interface import TuningType

            result = adapter.supports_tuning_type(TuningType.CLUSTERING)
            assert result is True
        except ImportError:
            pytest.skip("TuningType not available")

    def test_returns_false_for_import_error(self):
        adapter = _make_adapter()
        with patch.dict("sys.modules", {"benchbox.core.tuning.interface": None}):
            result = adapter.supports_tuning_type("clustering")
        assert isinstance(result, bool)


class TestGenerateTuningClause:
    def test_returns_empty_string_when_no_tuning(self):
        adapter = _make_adapter()
        tuning = MagicMock()
        tuning.has_any_tuning.return_value = False
        result = adapter.generate_tuning_clause(tuning)
        assert result == ""

    def test_returns_empty_string_for_none(self):
        adapter = _make_adapter()
        result = adapter.generate_tuning_clause(None)
        assert result == ""

    def test_includes_using_delta_when_tuning_present(self):
        adapter = _make_adapter()
        tuning = MagicMock()
        tuning.has_any_tuning.return_value = True
        tuning.get_columns_by_type.return_value = []
        result = adapter.generate_tuning_clause(tuning)
        assert "USING DELTA" in result

    def test_includes_partitioned_by_when_partition_columns_present(self):
        adapter = _make_adapter()
        tuning = MagicMock()
        tuning.has_any_tuning.return_value = True

        col = MagicMock()
        col.name = "p_date"
        col.order = 0

        try:
            from benchbox.core.tuning.interface import TuningType

            tuning.get_columns_by_type.side_effect = lambda t: [col] if t == TuningType.PARTITIONING else []
        except ImportError:
            tuning.get_columns_by_type.side_effect = lambda t: [col] if t.__class__.__name__ == "TuningType" else []

        result = adapter.generate_tuning_clause(tuning)
        assert "PARTITIONED BY" in result

    def test_includes_cluster_by_when_cluster_columns_present(self):
        adapter = _make_adapter()
        tuning = MagicMock()
        tuning.has_any_tuning.return_value = True

        col = MagicMock()
        col.name = "c_custkey"
        col.order = 0

        try:
            from benchbox.core.tuning.interface import TuningType

            tuning.get_columns_by_type.side_effect = lambda t: [col] if t == TuningType.CLUSTERING else []
        except ImportError:
            pytest.skip("TuningType not available")

        result = adapter.generate_tuning_clause(tuning)
        assert "CLUSTER BY" in result


class TestCreateConnection:
    def test_sets_catalog_on_new_database(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "benchbox"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("use_cached_result", "false")
        mock_cursor.fetchall.return_value = [(1,)]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "database_was_reused", False, create=True):
                    result = adapter.create_connection()

        assert result is mock_conn
        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("USE CATALOG" in s and "main" in s for s in executed)

    def test_sets_schema_when_database_reused(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench_tpch"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("use_cached_result", "false")
        mock_cursor.fetchall.return_value = [(1,)]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            with patch.object(adapter, "handle_existing_database"):
                adapter.database_was_reused = True
                adapter.create_connection()

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("USE SCHEMA" in s and "bench_tpch" in s for s in executed)

    def test_sets_schema_on_new_database(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench_tpch"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("use_cached_result", "false")
        mock_cursor.fetchall.return_value = [(1,)]

        with patch.object(adapter, "_create_admin_connection", return_value=mock_conn):
            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "database_was_reused", False, create=True):
                    adapter.create_connection()

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("CREATE SCHEMA IF NOT EXISTS" in s and "bench_tpch" in s for s in executed)
        assert any("USE SCHEMA" in s and "bench_tpch" in s for s in executed)

    def test_raises_on_connection_failure(self):
        adapter = _make_adapter()

        with patch.object(adapter, "_create_admin_connection", side_effect=RuntimeError("auth failed")):
            with patch.object(adapter, "handle_existing_database"):
                with pytest.raises(RuntimeError, match="auth failed"):
                    adapter.create_connection()


class TestCreateSchema:
    def test_creates_schema_and_executes_statements(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"
        adapter.create_catalog = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        benchmark = MagicMock()

        with patch.object(adapter, "_get_constraint_configuration", return_value=(False, False)):
            with patch.object(adapter, "_log_constraint_configuration"):
                with patch.object(
                    adapter,
                    "_create_schema_with_tuning",
                    return_value="CREATE TABLE orders (id INT); CREATE TABLE lineitem (id INT)",
                ):
                    with patch.object(adapter, "_execute_schema_statements", return_value=(2, [])):
                        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
                            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.5):
                                duration = adapter.create_schema(benchmark, mock_conn)

        assert duration == 0.5
        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("CREATE SCHEMA" in s for s in executed)

    def test_creates_catalog_when_create_catalog_true(self):
        adapter = _make_adapter()
        adapter.catalog = "newcat"
        adapter.schema = "bench"
        adapter.create_catalog = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        benchmark = MagicMock()

        with patch.object(adapter, "_get_constraint_configuration", return_value=(False, False)):
            with patch.object(adapter, "_log_constraint_configuration"):
                with patch.object(
                    adapter,
                    "_create_schema_with_tuning",
                    return_value="CREATE TABLE orders (id INT)",
                ):
                    with patch.object(adapter, "_execute_schema_statements", return_value=(1, [])):
                        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
                            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.3):
                                adapter.create_schema(benchmark, mock_conn)

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("CREATE CATALOG" in s and "newcat" in s for s in executed)

    def test_raises_runtime_error_for_empty_schema_sql(self):
        adapter = _make_adapter()
        adapter.create_catalog = False

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        benchmark = MagicMock()

        with patch.object(adapter, "_get_constraint_configuration", return_value=(False, False)):
            with patch.object(adapter, "_log_constraint_configuration"):
                with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
                    with pytest.raises(RuntimeError):
                        adapter.create_schema(benchmark, mock_conn)


class TestExecuteQuerySuccessPath:
    def test_returns_dict_with_status_ok(self):
        adapter = _make_adapter()

        cursor = MagicMock()
        cursor.fetchall.return_value = [(1, "data")]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        cursor.fetchone.return_value = ("use_cached_result", "false")

        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.1):
                with patch.object(
                    adapter,
                    "_build_query_result_with_validation",
                    return_value={"query_id": "Q1", "status": "SUCCESS", "rows_returned": 1},
                ):
                    result = adapter.execute_query(
                        connection=connection,
                        query="SELECT 1",
                        query_id="Q1",
                    )

        assert result["status"] == "SUCCESS"
        assert result["query_id"] == "Q1"

    def test_includes_resource_usage_field(self):
        adapter = _make_adapter()

        cursor = MagicMock()
        cursor.fetchall.return_value = [(42,)]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        cursor.fetchone.return_value = ("use_cached_result", "false")

        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.2):
                with patch.object(
                    adapter,
                    "_build_query_result_with_validation",
                    return_value={"query_id": "Q2", "status": "SUCCESS"},
                ):
                    result = adapter.execute_query(
                        connection=connection,
                        query="SELECT count(*) FROM orders",
                        query_id="Q2",
                    )

        assert "resource_usage" in result
        assert "execution_time_seconds" in result["resource_usage"]

    def test_translated_query_field_is_none(self):
        adapter = _make_adapter()

        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor

        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.1):
                with patch.object(
                    adapter,
                    "_build_query_result_with_validation",
                    return_value={"query_id": "Q3", "status": "SUCCESS"},
                ):
                    result = adapter.execute_query(
                        connection=connection,
                        query="SELECT 1",
                        query_id="Q3",
                    )

        assert result.get("translated_query") is None


class TestLoadData:
    def test_loads_all_tables_and_returns_stats(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"

        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("bench", "orders", False),
            ("bench", "lineitem", False),
        ]
        connection = MagicMock()
        connection.cursor.return_value = cursor

        benchmark = MagicMock()

        data_files = {
            "orders": Path("orders.parquet"),
            "lineitem": Path("lineitem.parquet"),
        }

        with patch.object(adapter, "_resolve_databricks_data_files", return_value=data_files):
            with patch.object(adapter, "_resolve_stage_root", return_value="dbfs:/Volumes/cat/sch/vol"):
                with patch.object(adapter, "_maybe_upload_to_uc_volume", return_value=data_files):
                    with patch.object(
                        adapter,
                        "_load_single_table",
                        return_value=(1500, 0.1, 0.0),
                    ):
                        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
                            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=1.0):
                                table_stats, total_time, per_table = adapter.load_data(
                                    benchmark=benchmark,
                                    connection=connection,
                                    data_dir=Path("/data"),
                                )

        assert "orders" in table_stats
        assert "lineitem" in table_stats
        assert table_stats["orders"] == 1500
        assert total_time == 1.0

    def test_handles_individual_table_failure_gracefully(self):
        adapter = _make_adapter()
        adapter.catalog = "main"
        adapter.schema = "bench"

        cursor = MagicMock()
        cursor.fetchall.return_value = [("bench", "orders", False)]
        connection = MagicMock()
        connection.cursor.return_value = cursor

        benchmark = MagicMock()
        data_files = {"orders": Path("orders.parquet")}

        with patch.object(adapter, "_resolve_databricks_data_files", return_value=data_files):
            with patch.object(adapter, "_resolve_stage_root", return_value="dbfs:/Volumes/cat/sch/vol"):
                with patch.object(adapter, "_maybe_upload_to_uc_volume", return_value=data_files):
                    with patch.object(
                        adapter,
                        "_load_single_table",
                        side_effect=RuntimeError("copy into failed"),
                    ):
                        with patch("benchbox.platforms.databricks.adapter.mono_time", return_value=0.0):
                            with patch("benchbox.platforms.databricks.adapter.elapsed_seconds", return_value=0.5):
                                table_stats, total_time, _ = adapter.load_data(
                                    benchmark=benchmark,
                                    connection=connection,
                                    data_dir=Path("/data"),
                                )

        assert table_stats["orders"] == 0


class TestUploadToUcVolumeMainBody:
    def test_single_non_sharded_file_uploaded(self, tmp_path):
        adapter = _make_adapter()
        adapter.server_hostname = "test.cloud.databricks.com"
        adapter.access_token = "tok"

        data_file = tmp_path / "orders.parquet"
        data_file.write_bytes(b"fake parquet")

        data_files = {"orders": data_file}

        mock_workspace = MagicMock()
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        with patch.dict("sys.modules", {"databricks.sdk": mock_sdk}):
            with patch.object(adapter, "_try_reuse_uc_volume_data", return_value=None):
                with patch.object(adapter, "_resolve_uc_manifest_path", return_value=tmp_path / "no_manifest.json"):
                    with patch.object(
                        adapter,
                        "_upload_single_file",
                        return_value="dbfs:/Volumes/cat/sch/vol/orders.parquet",
                    ):
                        result = adapter._upload_to_uc_volume(
                            data_files,
                            "dbfs:/Volumes/cat/sch/vol",
                            tmp_path,
                            force_upload=True,
                        )

        assert "orders" in result
        assert result["orders"] == "dbfs:/Volumes/cat/sch/vol/orders.parquet"

    def test_skips_missing_files_with_warning(self, tmp_path):
        adapter = _make_adapter()

        data_files = {"orders": tmp_path / "orders_missing.parquet"}

        mock_workspace = MagicMock()
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        with patch.dict("sys.modules", {"databricks.sdk": mock_sdk}):
            with patch.object(adapter, "_try_reuse_uc_volume_data", return_value=None):
                with patch.object(adapter, "_resolve_uc_manifest_path", return_value=tmp_path / "no_manifest.json"):
                    result = adapter._upload_to_uc_volume(
                        data_files,
                        "dbfs:/Volumes/cat/sch/vol",
                        tmp_path,
                        force_upload=True,
                    )

        assert "orders" not in result

    def test_reuse_result_returned_early(self, tmp_path):
        adapter = _make_adapter()

        reuse_map = {"orders": "dbfs:/Volumes/cat/sch/vol/orders.parquet"}

        mock_workspace = MagicMock()
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        with patch.dict("sys.modules", {"databricks.sdk": mock_sdk}):
            with patch.object(adapter, "_try_reuse_uc_volume_data", return_value=reuse_map):
                with patch.object(adapter, "_resolve_uc_manifest_path", return_value=tmp_path / "no_manifest.json"):
                    result = adapter._upload_to_uc_volume(
                        {"orders": tmp_path / "orders.parquet"},
                        "dbfs:/Volumes/cat/sch/vol",
                        tmp_path,
                        force_upload=False,
                    )

        assert result == reuse_map

    def test_multi_file_upload_preserves_relative_paths(self, tmp_path):
        adapter = _make_adapter()
        adapter.server_hostname = "test.cloud.databricks.com"
        adapter.access_token = "tok"

        asia_part = tmp_path / "orders" / "region=ASIA" / "part-00000.parquet"
        europe_part = tmp_path / "orders" / "region=EUROPE" / "part-00000.parquet"
        asia_part.parent.mkdir(parents=True)
        europe_part.parent.mkdir(parents=True)
        asia_part.write_bytes(b"asia")
        europe_part.write_bytes(b"europe")

        mock_workspace = MagicMock()
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        uploaded_targets: list[str] = []

        def _upload_single_file(local_path, volume_path, uc_volume_path, workspace, remote_path=None):
            uploaded_targets.append(remote_path)
            return f"dbfs:{volume_path}/{remote_path}"

        with patch.dict("sys.modules", {"databricks.sdk": mock_sdk}):
            with patch.object(adapter, "_try_reuse_uc_volume_data", return_value=None):
                with patch.object(adapter, "_resolve_uc_manifest_path", return_value=tmp_path / "no_manifest.json"):
                    with patch.object(adapter, "_upload_single_file", side_effect=_upload_single_file):
                        result = adapter._upload_to_uc_volume(
                            {"orders": [asia_part, europe_part]},
                            "dbfs:/Volumes/cat/sch/vol",
                            tmp_path,
                            force_upload=True,
                        )

        assert uploaded_targets == [
            "orders/region=ASIA/part-00000.parquet",
            "orders/region=EUROPE/part-00000.parquet",
        ]
        assert result["orders"] == [
            "dbfs:/Volumes/cat/sch/vol/orders/region=ASIA/part-00000.parquet",
            "dbfs:/Volumes/cat/sch/vol/orders/region=EUROPE/part-00000.parquet",
        ]


class TestMaybeUploadToUcVolume:
    def test_uploads_when_data_is_local_and_uc_volume_path(self, tmp_path):
        adapter = _make_adapter()

        data_files = {"orders": tmp_path / "orders.parquet"}
        stage_root = "dbfs:/Volumes/cat/sch/vol"

        mock_conn = MagicMock()
        uploaded = {"orders": "dbfs:/Volumes/cat/sch/vol/orders.parquet"}

        with patch.object(adapter, "_ensure_uc_volume_exists"):
            with patch.object(adapter, "_upload_to_uc_volume", return_value=uploaded):
                result = adapter._maybe_upload_to_uc_volume(data_files, stage_root, tmp_path, mock_conn)

        assert result == uploaded

    def test_skips_upload_when_stage_root_is_s3(self, tmp_path):
        adapter = _make_adapter()

        data_files = {"orders": tmp_path / "orders.parquet"}
        stage_root = "s3://my-bucket/data"

        mock_conn = MagicMock()

        with patch.object(adapter, "_ensure_uc_volume_exists") as mock_ensure:
            result = adapter._maybe_upload_to_uc_volume(data_files, stage_root, tmp_path, mock_conn)

        mock_ensure.assert_not_called()
        assert result == data_files


class TestUploadShardedFiles:
    def test_uploads_all_chunk_files(self, tmp_path):
        adapter = _make_adapter()

        chunks = []
        for i in range(1, 4):
            f = tmp_path / f"orders.tbl.{i}.zst"
            f.write_bytes(b"chunk content")
            chunks.append(f)

        mock_workspace = MagicMock()

        with patch.object(adapter, "_upload_file_content_to_uc") as mock_upload:
            adapter._upload_sharded_files(chunks, "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace)

        assert mock_upload.call_count == 3

    def test_skips_empty_chunks(self, tmp_path):
        adapter = _make_adapter()

        empty_chunk = tmp_path / "orders.tbl.1.zst"
        empty_chunk.write_bytes(b"")

        mock_workspace = MagicMock()

        with patch.object(adapter, "_upload_file_content_to_uc") as mock_upload:
            adapter._upload_sharded_files(
                [empty_chunk], "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace
            )

        mock_upload.assert_not_called()

    def test_raises_on_upload_failure(self, tmp_path):
        adapter = _make_adapter()

        chunk = tmp_path / "orders.tbl.1.zst"
        chunk.write_bytes(b"content")

        mock_workspace = MagicMock()

        with patch.object(adapter, "_upload_file_content_to_uc", side_effect=RuntimeError("upload error")):
            with pytest.raises(RuntimeError, match="Failed to upload"):
                adapter._upload_sharded_files(
                    [chunk], "/Volumes/cat/sch/vol", "dbfs:/Volumes/cat/sch/vol", mock_workspace
                )


class TestUploadSingleTablePath:
    def test_sharded_branch_returns_exact_uri_list(self, tmp_path):
        from pathlib import Path

        adapter = _make_adapter()
        first = tmp_path / "orders.tbl.1.zst"
        first.write_bytes(b"chunk")
        chunks = [tmp_path / "orders.tbl.1.zst", tmp_path / "orders.tbl.2.zst"]
        mock_workspace = MagicMock()

        with (
            patch.object(
                adapter,
                "_detect_sharded_files",
                return_value=(True, "orders.tbl.*.zst", chunks),
            ),
            patch.object(adapter, "_upload_sharded_files", return_value=None),
        ):
            result = adapter._upload_single_table_path(
                "orders",
                first,
                "orders.tbl.1.zst",
                "/Volumes/cat/sch/vol",
                "dbfs:/Volumes/cat/sch/vol",
                mock_workspace,
                tmp_path,
            )

        assert isinstance(result, list)
        assert all("*" not in uri for uri in result)
        assert result == [
            "dbfs:/Volumes/cat/sch/vol/orders.tbl.1.zst",
            "dbfs:/Volumes/cat/sch/vol/orders.tbl.2.zst",
        ]
        assert isinstance(chunks[0], Path)

    def test_expand_copy_sources_raises_on_unexpandable_glob(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="does not accept glob"):
            adapter._expand_copy_sources(
                "dbfs:/Volumes/cat/sch/vol/orders.*.zst",
                "dbfs:/Volumes/cat/sch/vol",
                "dbfs:/Volumes/cat/sch/vol/orders.*.zst",
            )


class TestNormalizeOutputAliases:
    def test_self_join_duplicates_get_suffixes(self):
        adapter = _make_adapter()
        sql = (
            "SELECT a.syear AS syear, a.cnt AS cnt, b.syear AS syear, b.cnt AS cnt "
            "FROM (SELECT syear, COUNT(*) AS cnt FROM t GROUP BY syear) a "
            "JOIN (SELECT syear, COUNT(*) AS cnt FROM t GROUP BY syear) b ON a.syear = b.syear"
        )
        result = adapter._normalize_databricks_query(sql)
        assert "syear_2" in result
        assert "cnt_2" in result
        assert "JOIN" in result

    def test_unique_outputs_unchanged(self):
        adapter = _make_adapter()
        sql = "SELECT a, b, COUNT(*) AS cnt FROM t GROUP BY a, b"
        assert adapter._normalize_databricks_query(sql) == sql


class TestUploadFileContentToUc:
    def test_uploads_content_to_workspace(self, tmp_path):
        adapter = _make_adapter()

        data_file = tmp_path / "orders.parquet"
        data_file.write_bytes(b"parquet content bytes")

        mock_workspace = MagicMock()

        adapter._upload_file_content_to_uc(
            data_file, "/Volumes/cat/sch/vol/orders.parquet", "dbfs:/Volumes/cat/sch/vol", mock_workspace
        )

        mock_workspace.files.upload.assert_called_once()

    def test_raises_on_empty_file(self, tmp_path):
        adapter = _make_adapter()

        empty_file = tmp_path / "empty.parquet"
        empty_file.write_bytes(b"")

        mock_workspace = MagicMock()

        with pytest.raises(RuntimeError, match="Failed to read content"):
            adapter._upload_file_content_to_uc(
                empty_file, "/Volumes/cat/sch/vol/empty.parquet", "dbfs:/Volumes/cat/sch/vol", mock_workspace
            )


class TestSelectDatabricksWarehouse:
    def test_returns_none_for_empty_list(self):
        import logging

        from benchbox.platforms.databricks.adapter import _select_databricks_warehouse

        result = _select_databricks_warehouse([], False, logging.getLogger("test"))
        assert result is None

    def test_prefers_running_warehouse(self):
        import logging

        from benchbox.platforms.databricks.adapter import _select_databricks_warehouse

        wh_stopped = MagicMock()
        wh_stopped.state.__str__ = lambda s: "STOPPED"
        wh_stopped.name = "Stopped Warehouse"

        wh_running = MagicMock()
        wh_running.state.__str__ = lambda s: "RUNNING"
        wh_running.name = "Running Warehouse"

        result = _select_databricks_warehouse([wh_stopped, wh_running], False, logging.getLogger("test"))
        assert result is wh_running

    def test_returns_first_available_when_no_running(self):
        import logging

        from benchbox.platforms.databricks.adapter import _select_databricks_warehouse

        wh_stopped = MagicMock()
        wh_stopped.state.__str__ = lambda s: "STOPPED"
        wh_stopped.name = "Stopped"

        result = _select_databricks_warehouse([wh_stopped], False, logging.getLogger("test"))
        assert result is wh_stopped

    def test_skips_deleted_warehouses(self):
        import logging

        from benchbox.platforms.databricks.adapter import _select_databricks_warehouse

        wh_deleted = MagicMock()
        wh_deleted.state.__str__ = lambda s: "DELETED"
        wh_deleted.name = "Deleted"

        result = _select_databricks_warehouse([wh_deleted], False, logging.getLogger("test"))
        assert result is None

    def test_very_verbose_logging(self):
        import logging

        from benchbox.platforms.databricks.adapter import _select_databricks_warehouse

        wh_running = MagicMock()
        wh_running.state.__str__ = lambda s: "RUNNING"
        wh_running.name = "Running"

        logger = MagicMock()
        result = _select_databricks_warehouse([wh_running], True, logger)
        assert result is wh_running
        logger.info.assert_called()


class TestGetConnectionParams:
    def test_returns_adapter_defaults(self):
        adapter = _make_adapter()
        params = adapter._get_connection_params()
        assert params["server_hostname"] == "test.cloud.databricks.com"
        assert params["http_path"] == "/sql/1.0/warehouses/abc123"
        assert params["access_token"] == "tok"

    def test_overrides_with_connection_config(self):
        adapter = _make_adapter()
        params = adapter._get_connection_params(
            server_hostname="override.databricks.com",
            http_path="/sql/1.0/warehouses/override",
            access_token="override-tok",
        )
        assert params["server_hostname"] == "override.databricks.com"
        assert params["access_token"] == "override-tok"


class TestResolveUcManifestPath:
    def test_returns_manifest_in_data_dir_for_regular_path(self, tmp_path):
        adapter = _make_adapter()
        result = adapter._resolve_uc_manifest_path(tmp_path)
        assert result == tmp_path / "_datagen_manifest.json"

    def test_returns_manifest_from_databricks_path_local_component(self, tmp_path):
        from benchbox.utils.cloud_storage import DatabricksPath

        adapter = _make_adapter()
        dbfs_path = DatabricksPath(tmp_path, dbfs_target="dbfs:/Volumes/cat/sch/vol/data")
        result = adapter._resolve_uc_manifest_path(dbfs_path)
        assert "_datagen_manifest.json" in str(result)


class TestManifestPatternForName:
    def test_compressed_sharded_file(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        base, ext = DatabricksAdapter._manifest_pattern_for_name("orders.tbl.1.zst")
        assert base == "orders.tbl"
        assert ext == ".zst"

    def test_plain_sharded_file(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        base, ext = DatabricksAdapter._manifest_pattern_for_name("orders.tbl.1")
        assert base == "orders.tbl"
        assert ext == ""

    def test_single_file(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        base, ext = DatabricksAdapter._manifest_pattern_for_name("orders.parquet")
        assert "orders" in base

    def test_dsdgen_underscore_chunks_detected(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        names = [
            "customer_demographics_1_10.dat.gz",
            "customer_demographics_5_10.dat.gz",
            "customer_demographics_10_10.dat.gz",
        ]
        for name in names:
            assert DatabricksAdapter._is_manifest_shard_name(name) is True
        assert _make_adapter()._detect_manifest_wildcard(names) == "customer_demographics.*.dat.gz"

    def test_dsdgen_underscore_chunk_rejects_flat_file(self):
        from benchbox.platforms.databricks import DatabricksAdapter

        assert DatabricksAdapter._is_manifest_shard_name("customer_demographics.dat") is False
        assert DatabricksAdapter._is_manifest_shard_name("inventory_3.dat") is False


_LIVE_WAREHOUSE_STRUCT = {
    "dbr_version": None,
    "dbsql_version": "2026.36",
    "u_build_hash": "ae9b8c94bd7756748eadf8dbbb27f84a703ed782",
    "r_build_hash": "85f51ee3c849b3c03c13ad723d1a65378a77d8c2",
}

_LIVE_SPARK_RAW = "4.2.0 0000000000000000000000000000000000000000"


def _mock_version_connection(
    fetchone_values=None,
    execute_side_effect=None,
    fetchall_values=None,
):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    if fetchone_values is not None:
        mock_cursor.fetchone.side_effect = list(fetchone_values)
    if fetchall_values is not None:
        mock_cursor.fetchall.side_effect = list(fetchall_values)
    if execute_side_effect is not None:
        mock_cursor.execute.side_effect = execute_side_effect
    return mock_conn, mock_cursor


class TestSanitizeSparkEngineVersion:
    def test_strips_placeholder_hash(self):
        from benchbox.platforms.databricks.adapter import _sanitize_spark_engine_version

        assert _sanitize_spark_engine_version(_LIVE_SPARK_RAW) == "4.2.0"

    def test_strips_real_commit_hash(self):
        from benchbox.platforms.databricks.adapter import _sanitize_spark_engine_version

        assert _sanitize_spark_engine_version("3.5.0 abcdef1234567890abcdef1234567890abcdef12") == "3.5.0"

    def test_preserves_runtime_string(self):
        from benchbox.platforms.databricks.adapter import _sanitize_spark_engine_version

        assert _sanitize_spark_engine_version("Runtime 14.3 LTS") == "Runtime 14.3 LTS"

    def test_preserves_spark_detail_string(self):
        from benchbox.platforms.databricks.adapter import _sanitize_spark_engine_version

        raw = "14.3 LTS (apache-spark-3.5.0-bin-hadoop3)"
        assert _sanitize_spark_engine_version(raw) == raw

    def test_none_and_blank(self):
        from benchbox.platforms.databricks.adapter import _sanitize_spark_engine_version

        assert _sanitize_spark_engine_version(None) is None
        assert _sanitize_spark_engine_version("") is None
        assert _sanitize_spark_engine_version("   ") is None


class TestFirstColumnScalarGuard:
    def test_bare_string_returned_intact(self):
        from benchbox.platforms.databricks.adapter import _first_column

        assert _first_column("Spark 3.4.1") == "Spark 3.4.1"

    def test_bytes_returned_intact(self):
        from benchbox.platforms.databricks.adapter import _first_column

        assert _first_column(b"3.5.1") == b"3.5.1"

    def test_tuple_still_unpacks(self):
        from benchbox.platforms.databricks.adapter import _first_column

        assert _first_column(("4.2.0",)) == "4.2.0"
        assert _first_column(None) is None


class TestParseCurrentVersionPayload:
    def test_parses_warehouse_dict(self):
        from benchbox.platforms.databricks.adapter import _parse_current_version_payload

        parsed = _parse_current_version_payload(dict(_LIVE_WAREHOUSE_STRUCT))
        assert parsed is not None
        assert parsed["dbsql_version"] == "2026.36"
        assert parsed["dbr_version"] is None
        assert parsed["u_build_hash"] == _LIVE_WAREHOUSE_STRUCT["u_build_hash"]
        assert parsed["r_build_hash"] == _LIVE_WAREHOUSE_STRUCT["r_build_hash"]

    def test_parses_dbr_cluster_dict(self):
        from benchbox.platforms.databricks.adapter import _parse_current_version_payload

        parsed = _parse_current_version_payload({"dbr_version": "14.3 LTS", "dbsql_version": None})
        assert parsed is not None
        assert parsed["dbr_version"] == "14.3 LTS"
        assert parsed["dbsql_version"] is None

    def test_parses_connector_row(self):
        pytest.importorskip("databricks.sql.types")
        from databricks.sql.types import Row

        from benchbox.platforms.databricks.adapter import _parse_current_version_payload

        row = Row(
            dbr_version=None,
            dbsql_version="2026.36",
            u_build_hash=_LIVE_WAREHOUSE_STRUCT["u_build_hash"],
            r_build_hash=_LIVE_WAREHOUSE_STRUCT["r_build_hash"],
        )
        parsed = _parse_current_version_payload(row)
        assert parsed is not None
        assert parsed["dbsql_version"] == "2026.36"

    def test_parses_row_wrapped_in_tuple(self):
        pytest.importorskip("databricks.sql.types")
        from databricks.sql.types import Row

        from benchbox.platforms.databricks.adapter import (
            _parse_current_version_payload,
            _unwrap_current_version_struct,
        )

        row = Row(dbr_version="14.3 LTS", dbsql_version=None, u_build_hash=None, r_build_hash=None)
        unwrapped = _unwrap_current_version_struct((row,))
        parsed = _parse_current_version_payload(unwrapped)
        assert parsed is not None
        assert parsed["dbr_version"] == "14.3 LTS"

    def test_rejects_plain_string(self):
        from benchbox.platforms.databricks.adapter import _parse_current_version_payload

        assert _parse_current_version_payload("4.2.0 0000000000000000000000000000000000000000") is None
        assert _parse_current_version_payload(None) is None
        assert _parse_current_version_payload({}) is None

    def test_prefers_dbsql_over_dbr(self):
        from benchbox.platforms.databricks.adapter import _select_databricks_platform_version

        assert _select_databricks_platform_version({"dbsql_version": "2026.36", "dbr_version": "14.3"}) == "2026.36"
        assert _select_databricks_platform_version({"dbsql_version": None, "dbr_version": "14.3 LTS"}) == "14.3 LTS"
        assert _select_databricks_platform_version({"dbsql_version": None, "dbr_version": None}) is None


class TestGetPlatformInfoCurrentVersion:
    def test_warehouse_dict_struct(self):
        adapter = _make_adapter()
        conn, cursor = _mock_version_connection(fetchone_values=[(dict(_LIVE_WAREHOUSE_STRUCT),)])

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "2026.36"
        assert info["engine_version"] == "2026.36"
        assert info["engine_version_source"] == "current_version"
        assert info["dbsql_version"] == "2026.36"
        assert info["u_build_hash"] == _LIVE_WAREHOUSE_STRUCT["u_build_hash"]
        assert info["r_build_hash"] == _LIVE_WAREHOUSE_STRUCT["r_build_hash"]
        assert cursor.execute.call_args_list[0].args[0] == "SELECT current_version()"
        cursor.close.assert_called()

    def test_connector_row_struct(self):
        pytest.importorskip("databricks.sql.types")
        from databricks.sql.types import Row

        adapter = _make_adapter()
        row = Row(
            dbr_version=None,
            dbsql_version="2026.36",
            u_build_hash=_LIVE_WAREHOUSE_STRUCT["u_build_hash"],
            r_build_hash=_LIVE_WAREHOUSE_STRUCT["r_build_hash"],
        )
        conn, cursor = _mock_version_connection(fetchone_values=[(row,)])

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "2026.36"
        assert info["engine_version_source"] == "current_version"
        assert info["dbsql_version"] == "2026.36"

    def test_dbr_cluster_selects_dbr_version(self):
        adapter = _make_adapter()
        struct = {"dbr_version": "14.3 LTS", "dbsql_version": None, "u_build_hash": None, "r_build_hash": None}
        conn, _ = _mock_version_connection(fetchone_values=[(struct,)])

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "14.3 LTS"
        assert info["engine_version"] == "14.3 LTS"
        assert info["engine_version_source"] == "current_version"

    def test_current_version_error_falls_back_to_sanitized_version(self):
        adapter = _make_adapter()

        def _execute(query, *args, **kwargs):
            if "current_version()" in query:
                raise RuntimeError("no such function current_version")
            return Mock()

        conn, _ = _mock_version_connection(
            fetchone_values=[(_LIVE_SPARK_RAW,)],
            execute_side_effect=_execute,
        )

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "4.2.0"
        assert info["engine_version"] == "4.2.0"
        assert info["engine_version_source"] == "sql_query"

    def test_current_version_none_falls_back_to_version(self):
        adapter = _make_adapter()
        conn, _ = _mock_version_connection(fetchone_values=[None, ("Runtime 14.3 LTS",)])

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "Runtime 14.3 LTS"
        assert info["engine_version_source"] == "sql_query"

    def test_version_fallback_spark_version_query(self):
        adapter = _make_adapter()
        conn, cursor = _mock_version_connection(fetchone_values=[None, None, ("3.5.1",)])

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] == "3.5.1"
        assert info["engine_version_source"] == "sql_query"
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        assert queries[0] == "SELECT current_version()"
        assert queries[1] == "SELECT version()"
        assert queries[2] == "SELECT spark_version() as version"

    def test_total_failure_returns_nones(self):
        adapter = _make_adapter()
        conn, cursor = _mock_version_connection(execute_side_effect=RuntimeError("down"))

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
            patch.dict("sys.modules", {"databricks.sdk": None}),
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["platform_version"] is None
        assert info["engine_version"] is None
        assert info["engine_version_source"] is None
        cursor.close.assert_called()

    def test_no_connection_returns_nones(self):
        adapter = _make_adapter()

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            with patch.dict("sys.modules", {"databricks.sdk": None}):
                info = adapter.get_platform_info(connection=None)

        assert info["platform_version"] is None
        assert info["engine_version"] is None
        assert info["engine_version_source"] is None


class TestGetPlatformMetadataCurrentVersion:
    def test_warehouse_records_dbsql_and_spark_versions(self):
        adapter = _make_adapter()
        conn, cursor = _mock_version_connection(
            fetchone_values=[
                (dict(_LIVE_WAREHOUSE_STRUCT),),
                (_LIVE_SPARK_RAW,),
                ("main", "benchbox"),
            ],
            fetchall_values=[[("current_database",)], [("spark.sql.shuffle.partitions", "200")]],
        )

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            metadata = adapter._get_platform_metadata(conn)

        assert metadata["platform_version"] == "2026.36"
        assert metadata["engine_version"] == "2026.36"
        assert metadata["engine_version_source"] == "current_version"
        assert metadata["dbsql_version"] == "2026.36"
        assert metadata["u_build_hash"] == _LIVE_WAREHOUSE_STRUCT["u_build_hash"]
        assert metadata["r_build_hash"] == _LIVE_WAREHOUSE_STRUCT["r_build_hash"]
        assert metadata["spark_version"] == "4.2.0"
        assert metadata["current_catalog"] == "main"
        assert metadata["current_schema"] == "benchbox"
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        assert queries[0] == "SELECT current_version()"

    def test_row_struct_metadata(self):
        pytest.importorskip("databricks.sql.types")
        from databricks.sql.types import Row

        adapter = _make_adapter()
        row = Row(
            dbr_version=None,
            dbsql_version="2026.36",
            u_build_hash=_LIVE_WAREHOUSE_STRUCT["u_build_hash"],
            r_build_hash=_LIVE_WAREHOUSE_STRUCT["r_build_hash"],
        )
        conn, _ = _mock_version_connection(
            fetchone_values=[(row,), (_LIVE_SPARK_RAW,), ("main", "benchbox")],
            fetchall_values=[[("current_database",)], [("spark.sql.shuffle.partitions", "200")]],
        )

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            metadata = adapter._get_platform_metadata(conn)

        assert metadata["platform_version"] == "2026.36"
        assert metadata["spark_version"] == "4.2.0"

    def test_fallback_metadata_sanitizes_version(self):
        adapter = _make_adapter()

        def _execute(query, *args, **kwargs):
            if "current_version()" in query:
                raise RuntimeError("unsupported")
            return Mock()

        conn, _ = _mock_version_connection(
            fetchone_values=[(_LIVE_SPARK_RAW,), ("main", "benchbox")],
            fetchall_values=[[("current_database",)], [("spark.sql.shuffle.partitions", "200")]],
            execute_side_effect=_execute,
        )

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            metadata = adapter._get_platform_metadata(conn)

        assert metadata["platform_version"] == "4.2.0"
        assert metadata["engine_version_source"] == "sql_query"
        assert metadata["spark_version"] == "4.2.0"


def _make_operation(write_sql: str, overrides: dict | None = None):
    from benchbox.core.write_primitives.catalog import WriteOperation

    return WriteOperation(
        id="test_op",
        category="test",
        description="test operation",
        write_sql=write_sql,
        platform_overrides=overrides or {},
    )


class TestPreprocessOperationSql:
    def test_cast_varchar_rewritten(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql("op", _make_operation("SELECT CAST(n AS VARCHAR)"))

        assert result == "SELECT CAST(n AS STRING)"

    def test_unnest_generate_series_rewritten_to_explode_sequence(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql(
            "op", _make_operation("SELECT n FROM (SELECT unnest(generate_series(1, 100)) AS n) t")
        )

        assert result == "SELECT n FROM (SELECT explode(sequence(1, 100)) AS n) t"

    def test_skip_override_returns_none(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql("op", _make_operation("SELECT 1", {"databricks": None}))

        assert result is None

    def test_other_platform_override_ignored(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql(
            "op", _make_operation("SELECT CAST(n AS VARCHAR)", {"bigquery": "SELECT 1"})
        )

        assert result == "SELECT CAST(n AS STRING)"
