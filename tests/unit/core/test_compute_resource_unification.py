import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import yaml

import benchbox.platforms
from benchbox.core.compute_resource import (
    compute_size_keys,
    compute_value_keys,
    declares_compute,
    get_compute_declaration,
    migrate_saved_compute_keys,
    register_compute_specs,
    resolve_linked_compute_resource,
    resolve_resource_kind,
)
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.security.credentials import CredentialManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


EXPECTED_DECLARATIONS = {
    "snowflake": ("warehouse", ("warehouse",), ("warehouse_size",)),
    "snowpark-connect": ("warehouse", ("warehouse",), ("warehouse_size",)),
    "databend": ("warehouse", ("warehouse",), ()),
    "databricks": ("warehouse", ("warehouse_id",), ()),
    "databricks-df": ("cluster", ("cluster_id",), ()),
    "firebolt": ("engine", ("engine_name",), ("engine_size", "compute_size")),
    "athena": ("workgroup", ("workgroup",), ()),
    "athena-spark": ("workgroup", ("workgroup",), ()),
    "redshift": (
        {"workgroup_name": "workgroup", "cluster_identifier": "cluster"},
        ("workgroup_name", "cluster_identifier"),
        (),
    ),
    "dataproc": ("cluster", ("cluster_name",), ()),
    "synapse-spark": ("pool", ("spark_pool_name",), ()),
    "fabric-spark": ("pool", ("spark_pool_name",), ()),
    "emr-serverless": ("application", ("application_id",), ()),
    "clickhouse-cloud": ("service", ("service_id",), ("compute_size",)),
    "quanton": (None, (), ("cluster_size",)),
}


@pytest.mark.parametrize(("platform", "expected"), sorted(EXPECTED_DECLARATIONS.items()))
def test_manifest_declares_compute_per_mapping(platform: str, expected: tuple) -> None:
    assert declares_compute(platform) is True
    declaration = get_compute_declaration(platform)
    assert declaration is not None
    assert declaration.resource_kind == expected[0]
    assert declaration.native_resource_keys == expected[1]
    assert declaration.native_size_keys == expected[2]


def test_platform_without_compute_declares_nothing() -> None:
    assert declares_compute("duckdb") is False
    assert get_compute_declaration("duckdb") is None
    assert compute_value_keys("duckdb") == ()
    assert compute_size_keys("duckdb") == ()


def test_value_and_size_keys_include_canonical_and_native() -> None:
    assert compute_value_keys("redshift") == ("compute_resource", "workgroup_name", "cluster_identifier")
    assert compute_size_keys("snowflake") == ("compute_size", "warehouse_size")
    assert compute_size_keys("databend") == ("compute_size",)


def test_spec_registration_is_idempotent() -> None:
    register_compute_specs()
    register_compute_specs()


@pytest.mark.parametrize(
    ("platform", "pairs", "canonical"),
    [
        ("databend", [("compute_resource", "CLOUD_WH")], {"compute_resource": "CLOUD_WH"}),
        ("snowflake", [("compute_resource", "ANALYTICS_WH")], {"compute_resource": "ANALYTICS_WH"}),
        ("snowflake", [("compute_size", "XLARGE")], {"compute_size": "XLARGE"}),
        ("databricks", [("compute_resource", "abc123")], {"compute_resource": "abc123"}),
        ("quanton", [("compute_size", "large")], {"compute_size": "large"}),
        ("firebolt", [("compute_resource", "prod_eng")], {"compute_resource": "prod_eng"}),
        ("firebolt", [("compute_size", "M")], {"compute_size": "M"}),
        ("clickhouse-cloud", [("compute_size", "M")], {"compute_size": "M"}),
        ("databricks-df", [("compute_resource", "c-123")], {"compute_resource": "c-123"}),
        ("athena-spark", [("compute_resource", "spark-wg")], {"compute_resource": "spark-wg"}),
        ("synapse-spark", [("compute_resource", "pool1")], {"compute_resource": "pool1"}),
        ("fabric-spark", [("compute_resource", "pool1")], {"compute_resource": "pool1"}),
        ("dataproc", [("compute_resource", "dp-cluster")], {"compute_resource": "dp-cluster"}),
        ("emr-serverless", [("compute_resource", "app-123")], {"compute_resource": "app-123"}),
        ("clickhouse-cloud", [("compute_resource", "svc-1")], {"compute_resource": "svc-1"}),
        ("redshift", [("compute_resource", "wg")], {"compute_resource": "wg"}),
        ("athena", [("compute_resource", "primary")], {"compute_resource": "primary"}),
    ],
)
def test_canonical_key_parses(platform: str, pairs: list, canonical: dict) -> None:
    parsed = PlatformHookRegistry.parse_options(platform, pairs)
    for key, value in canonical.items():
        assert parsed[key] == value


@pytest.mark.parametrize(
    ("platform", "key", "replacement"),
    [
        ("databend", "warehouse", "compute_resource"),
        ("snowflake", "warehouse", "compute_resource"),
        ("snowflake", "warehouse_size", "compute_size"),
        ("databricks", "warehouse_id", "compute_resource"),
        ("databricks-df", "cluster_id", "compute_resource"),
        ("quanton", "cluster_size", "compute_size"),
        ("firebolt", "engine_name", "compute_resource"),
        ("firebolt", "engine_size", "compute_size"),
        ("athena", "workgroup", "compute_resource"),
        ("athena-spark", "workgroup", "compute_resource"),
        ("redshift", "workgroup_name", "compute_resource"),
        ("redshift", "cluster_identifier", "compute_resource"),
        ("dataproc", "cluster_name", "compute_resource"),
        ("synapse-spark", "spark_pool_name", "compute_resource"),
        ("fabric-spark", "spark_pool_name", "compute_resource"),
        ("emr-serverless", "application_id", "compute_resource"),
        ("clickhouse-cloud", "service_id", "compute_resource"),
    ],
)
def test_removed_native_key_is_rejected(platform: str, key: str, replacement: str) -> None:
    with pytest.raises(PlatformOptionError) as raised:
        PlatformHookRegistry.parse_options(platform, [(key, "X")])
    message = str(raised.value)
    assert f"'{key}'" in message
    assert "was removed in BenchBox 0.6.0" in message
    assert f"use '{replacement}' instead" in message


def test_removed_fabric_dw_warehouse_key_is_rejected() -> None:
    with pytest.raises(PlatformOptionError, match="'warehouse'.*removed in BenchBox 0.6.0.*'database'"):
        PlatformHookRegistry.parse_options("fabric_dw", [("warehouse", "WH1")])


def test_compute_size_rejected_where_unsupported() -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options("databend", [("compute_size", "L")])


def test_compute_resource_rejected_on_quanton() -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options("quanton", [("compute_resource", "X")])


@pytest.mark.parametrize("platform", ["databricks", "databricks-df", "athena", "athena-spark", "databend"])
def test_compute_size_rejected_without_size_declaration(platform: str) -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options(platform, [("compute_size", "L")])


@pytest.mark.parametrize(
    ("platform", "saved", "expected"),
    [
        ("snowflake", {"warehouse": "W", "warehouse_size": "L"}, {"compute_resource": "W", "compute_size": "L"}),
        ("firebolt", {"engine_name": "E", "engine_size": "S"}, {"compute_resource": "E", "compute_size": "S"}),
        ("quanton", {"cluster_size": "large"}, {"compute_size": "large"}),
        ("redshift", {"workgroup_name": "wg"}, {"compute_resource": "wg", "_compute_resource_kind": "workgroup"}),
        ("duckdb", {"warehouse": "W"}, {"warehouse": "W"}),
    ],
)
def test_saved_compute_keys_migrate(platform: str, saved: dict, expected: dict) -> None:
    assert migrate_saved_compute_keys(platform, saved) == expected


def test_saved_migration_keeps_canonical_over_native() -> None:
    migrated = migrate_saved_compute_keys("snowflake", {"warehouse": "OLD", "compute_resource": "NEW", "account": "a"})
    assert migrated == {"compute_resource": "NEW", "account": "a"}


def test_saved_migration_leaves_unrelated_keys() -> None:
    saved = {"account": "a", "password": "p"}
    assert migrate_saved_compute_keys("snowflake", saved) == saved


def test_credential_manager_migrates_saved_native_keys(tmp_path) -> None:
    path = tmp_path / "credentials.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "snowflake": {"account": "a", "warehouse": "OLD_WH", "warehouse_size": "LARGE"},
                "firebolt": {"engine_name": "eng"},
                "duckdb": {"warehouse": "untouched"},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    manager = CredentialManager(credentials_path=path)
    snowflake = manager.get_platform_credentials("snowflake")
    assert snowflake == {"account": "a", "compute_resource": "OLD_WH", "compute_size": "LARGE"}
    assert manager.get_platform_credentials("firebolt") == {"compute_resource": "eng"}
    assert manager.get_platform_credentials("duckdb") == {"warehouse": "untouched"}


def test_resolve_linked_compute_resource_takes_first_layer_with_any_key() -> None:
    merged = {"compute_resource": "A", "service_id": "B", "other": 1}
    layers = [{"service_id": "B"}, {"compute_resource": "A"}]
    resolved = resolve_linked_compute_resource(merged, layers, ("service_id",))
    assert resolved == {"other": 1, "service_id": "B"}


def test_resolve_linked_compute_resource_without_layer_drops_keys() -> None:
    merged = {"compute_resource": "A", "other": 1}
    assert resolve_linked_compute_resource(merged, [{}], ("service_id",)) == {"other": 1}


@pytest.mark.parametrize(
    ("platform", "merged", "expected"),
    [
        ("snowflake", {"warehouse": "W"}, "warehouse"),
        ("firebolt", {"engine_name": "E"}, "engine"),
        ("quanton", {"cluster_size": "small"}, None),
        ("redshift", {"workgroup_name": "wg"}, "workgroup"),
        ("redshift", {"cluster_identifier": "c"}, "cluster"),
        ("redshift", {"compute_resource": "wg"}, None),
        ("duckdb", {"warehouse": "W"}, None),
    ],
)
def test_resource_kind_resolution(platform: str, merged: dict, expected: str | None) -> None:
    assert resolve_resource_kind(platform, merged) == expected


class TestSnowflakeComputeUnification:
    @pytest.fixture(autouse=True)
    def _dependencies(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            yield

    def test_canonical_resource_reaches_connection(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="a",
                username="u",
                password="p",
                compute_resource="ANALYTICS_WH",
                compute_size="XLARGE",
            )
        assert adapter.warehouse == "ANALYTICS_WH"
        assert adapter.warehouse_size == "XLARGE"
        params = adapter._connect_params({}, include_database=False)
        assert params["warehouse"] == "ANALYTICS_WH"

    def test_preserved_defaults(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="a", username="u", password="p")
        assert adapter.warehouse == "COMPUTE_WH"
        assert adapter.warehouse_size == "MEDIUM"

    def test_native_keyword_is_rejected(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            with pytest.raises(TypeError, match="'warehouse'.*removed in BenchBox 0.6.0.*'compute_resource'"):
                SnowflakeAdapter(account="a", username="u", password="p", warehouse="LEGACY_WH")

    def test_from_config_accepts_canonical_keys(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter.from_config(
                {
                    "account": "a",
                    "username": "u",
                    "password": "p",
                    "compute_resource": "ANALYTICS_WH",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                }
            )
        assert adapter.warehouse == "ANALYTICS_WH"


class TestDatabricksComputeUnification:
    @pytest.fixture(autouse=True)
    def _dependencies(self):
        with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
            yield

    def test_canonical_resource_resolves(self):
        from benchbox.platforms.databricks.adapter import DatabricksAdapter

        adapter = DatabricksAdapter(
            server_hostname="host",
            http_path="/sql/1.0/warehouses/abc123",
            access_token="tok",
            compute_resource="abc123",
        )
        assert adapter.compute_resource == "abc123"

    def test_http_path_conflict_fails_with_both_values(self):
        from benchbox.platforms.databricks.adapter import DatabricksAdapter

        with pytest.raises(ValueError, match="abc123.*def456|def456.*abc123"):
            DatabricksAdapter(
                server_hostname="host",
                http_path="/sql/1.0/warehouses/abc123",
                access_token="tok",
                compute_resource="def456",
            )


class TestDatabendComputeUnification:
    @pytest.fixture(autouse=True)
    def _dependencies(self):
        with patch("benchbox.platforms.databend.adapter.check_platform_dependencies", return_value=(True, [])):
            yield

    def test_canonical_resource_reaches_dsn(self):
        from benchbox.platforms.databend.adapter import DatabendAdapter

        adapter = DatabendAdapter(host="localhost", compute_resource="CLOUD_WH")
        assert adapter.warehouse == "CLOUD_WH"
        assert "warehouse=CLOUD_WH" in adapter._build_dsn()

    def test_env_fallback_preserved(self, monkeypatch: pytest.MonkeyPatch):
        from benchbox.platforms.databend.adapter import DatabendAdapter

        monkeypatch.setenv("DATABEND_WAREHOUSE", "ENV_WH")
        adapter = DatabendAdapter(host="localhost")
        assert adapter.warehouse == "ENV_WH"


class TestSnowparkComputeUnification:
    def test_canonical_resource_from_config(self):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with patch("benchbox.platforms.snowpark_connect.SNOWPARK_AVAILABLE", True):
            adapter = SnowparkConnectAdapter.from_config(
                {
                    "account": "a",
                    "user": "u",
                    "password": "p",
                    "compute_resource": "ANALYTICS_WH",
                    "compute_size": "XLARGE",
                }
            )
        assert adapter.warehouse == "ANALYTICS_WH"
        assert adapter.warehouse_size == "XLARGE"

    def test_preserved_defaults(self):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with patch("benchbox.platforms.snowpark_connect.SNOWPARK_AVAILABLE", True):
            adapter = SnowparkConnectAdapter.from_config({"account": "a", "user": "u", "password": "p"})
        assert adapter.warehouse == "COMPUTE_WH"
        assert adapter.warehouse_size == "MEDIUM"


class TestFabricDwDatabaseRename:
    @pytest.fixture(autouse=True)
    def _dependencies(self):
        with patch("benchbox.platforms.fabric_warehouse.check_platform_dependencies", return_value=(True, [])):
            yield

    def test_database_is_canonical(self):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        adapter = FabricWarehouseAdapter(server="ws.datawarehouse.fabric.microsoft.com", database="WH1")
        assert adapter.database == "WH1"
        assert adapter.warehouse == "WH1"


class TestCanonicalResultsEmission:
    def test_snowflake_canonical_fields(self):
        from benchbox.platforms.base.runtime_metadata import _compute_metadata

        payload = _compute_metadata(
            "snowflake",
            {"compute_configuration": {"warehouse": "ANALYTICS_WH", "warehouse_size": "XLARGE"}},
            {},
        )
        assert payload["resource"] == "ANALYTICS_WH"
        assert payload["resource_kind"] == "warehouse"
        assert payload["size"] == "XLARGE"
        assert payload["warehouse"] == "ANALYTICS_WH"
        assert payload["warehouse_size"] == "XLARGE"

    def test_quanton_size_without_resource(self):
        from benchbox.platforms.base.runtime_metadata import _compute_metadata

        payload = _compute_metadata("quanton", {}, {"cluster_size": "large"})
        assert payload["size"] == "large"
        assert payload.get("resource") is None
        assert payload.get("resource_kind") is None

    def test_redshift_kind_follows_populated_key(self):
        from benchbox.platforms.base.runtime_metadata import _compute_metadata

        serverless = _compute_metadata("redshift", {}, {"workgroup_name": "wg"})
        assert serverless["resource"] == "wg"
        assert serverless["resource_kind"] == "workgroup"
        provisioned = _compute_metadata("redshift", {}, {"cluster_identifier": "c"})
        assert provisioned["resource_kind"] == "cluster"

    def test_platform_without_compute_declares_no_canonical_fields(self):
        from benchbox.platforms.base.runtime_metadata import _compute_metadata

        assert _compute_metadata("duckdb", {}, {}) == {}


class TestComputeAnonymizationGates:
    def _exported_compute(self, tmp_path, compute: dict):
        from benchbox.core.results.exporter import ResultExporter
        from benchbox.core.results.models import BenchmarkResults

        result = BenchmarkResults(
            benchmark_name="TPC-H",
            platform="redshift",
            scale_factor=0.01,
            execution_id="compute-gate-test",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            query_results=[
                {
                    "query_id": "Q1",
                    "execution_time_seconds": 0.1,
                    "rows_returned": 1,
                    "status": "SUCCESS",
                    "run_type": "measurement",
                }
            ],
            platform_compute=compute,
        )
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        exported = exporter.export_result(result, formats=["json"])

        with open(exported["json"], encoding="utf-8") as handle:
            return json.load(handle)

    def test_canonical_resource_dropped_kind_and_size_readable(self, tmp_path) -> None:
        payload = self._exported_compute(
            tmp_path,
            {
                "resource": "SECRET_WH",
                "resource_kind": "warehouse",
                "size": "XLARGE",
                "warehouse": "SECRET_WH",
            },
        )
        compute = payload["platform"]["compute"]
        assert "resource" not in compute
        assert "SECRET_WH" not in json.dumps(compute)
        assert compute["resource_kind"] == "warehouse"
        assert compute["size"] == "XLARGE"
        assert compute["warehouse"].startswith("warehouse_")

    def test_identifier_gaps_are_hashed(self, tmp_path) -> None:
        payload = self._exported_compute(
            tmp_path,
            {
                "cluster_identifier": "provisioned-cluster",
                "service_id": "svc-123",
                "service_name": "prod-service",
                "spark_pool": "pool01",
            },
        )
        compute = payload["platform"]["compute"]
        assert compute["cluster_identifier"].startswith("cluster_")
        assert compute["service_id"].startswith("service_")
        assert compute["service_name"].startswith("service_")
        assert compute["spark_pool"].startswith("pool_")


class TestDatabricksDfComputeUnification:
    def test_canonical_cluster_reaches_session_builder(self):
        from benchbox.platforms.databricks.dataframe_adapter import DatabricksDataFrameAdapter

        with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
            adapter = DatabricksDataFrameAdapter.from_config(
                {
                    "server_hostname": "host",
                    "http_path": "/sql/1.0/warehouses/abc123",
                    "access_token": "tok",
                    "compute_resource": "cluster-123",
                }
            )
        assert adapter.cluster_id == "cluster-123"


class TestAthenaSparkRuntimeVersion:
    def test_runtime_version_defaults_to_unrequested(self):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        adapter = AthenaSparkAdapter(compute_resource="wg", s3_staging_dir="s3://bucket/path")
        assert adapter.runtime_version is None

    def test_removed_engine_version_is_rejected(self):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        with pytest.raises(TypeError, match="'engine_version'.*removed in BenchBox 0.6.0.*'runtime_version'"):
            AthenaSparkAdapter(
                compute_resource="wg", s3_staging_dir="s3://bucket/path", engine_version="PySpark engine version 2"
            )

    def test_canonical_workgroup_from_config(self):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        adapter = AthenaSparkAdapter.from_config({"compute_resource": "spark-wg", "s3_staging_dir": "s3://bucket/path"})
        assert adapter.workgroup == "spark-wg"


class TestSynapseSparkComputeUnification:
    def test_canonical_pool_from_config(self):
        with (
            patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import SynapseSparkAdapter

            adapter = SynapseSparkAdapter.from_config(
                {
                    "workspace_name": "ws",
                    "compute_resource": "pool1",
                    "storage_account": "sa",
                    "storage_container": "sc",
                }
            )
        assert adapter.spark_pool_name == "pool1"


class TestFabricSparkComputeUnification:
    def test_canonical_pool_from_config(self):
        with (
            patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
            patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
            patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        ):
            from benchbox.platforms.azure import FabricSparkAdapter

            adapter = FabricSparkAdapter.from_config(
                {"workspace_id": "ws", "lakehouse_id": "lh", "compute_resource": "pool1"}
            )
        assert adapter.spark_pool_name == "pool1"


class TestDataprocComputeUnification:
    def test_canonical_cluster_from_config(self):
        from benchbox.platforms.gcp import DataprocAdapter

        with patch("benchbox.platforms.gcp.dataproc_adapter.check_platform_dependencies", return_value=(True, [])):
            adapter = DataprocAdapter.from_config(
                {
                    "project_id": "proj",
                    "compute_resource": "dp-cluster",
                    "gcs_staging_dir": "gs://bucket/path",
                }
            )
            assert adapter.cluster_name == "dp-cluster"


class TestEmrServerlessComputeUnification:
    def test_canonical_application_from_config(self):
        from benchbox.platforms.aws.emr_serverless_adapter import EMRServerlessAdapter

        adapter = EMRServerlessAdapter.from_config(
            {
                "compute_resource": "app-123",
                "s3_staging_dir": "s3://bucket/path",
                "execution_role_arn": "arn:aws:iam::123:role/x",
            }
        )
        assert adapter.application_id == "app-123"


class TestFireboltComputeUnification:
    def test_canonical_resource_and_size(self):
        from benchbox.platforms.firebolt import FireboltAdapter

        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="db",
                compute_resource="eng",
                compute_size="S",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")
        assert adapter.engine_name == "eng"
        assert adapter.engine_size == "S"

    def test_preserved_env_fallback(self, monkeypatch: pytest.MonkeyPatch):
        from benchbox.platforms.firebolt import FireboltAdapter

        monkeypatch.setenv("FIREBOLT_ENGINE_NAME", "ENV_ENG")
        try:
            adapter = FireboltAdapter(url="http://localhost:3473", database="db")
        except ImportError:
            pytest.skip("Firebolt SDK not installed")
        assert adapter.engine_name == "ENV_ENG"


class TestClickhouseCloudComputeUnification:
    def test_canonical_service_from_config(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        adapter = ClickHouseCloudAdapter.from_config(
            {"host": "h", "password": "p", "compute_resource": "svc-1", "compute_size": "M"}
        )
        assert adapter.platform_config.get("compute_resource") == "svc-1"


class TestRedshiftComputeUnification:
    @staticmethod
    def _from_config(host: str, **extra):
        from benchbox.platforms.redshift import RedshiftAdapter

        try:
            return RedshiftAdapter.from_config(
                {
                    "host": host,
                    "database": "dev",
                    "username": "u",
                    "password": "p",
                    "compute_resource": "wg",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                    **extra,
                }
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

    def test_serverless_host_applies_compute_resource_as_workgroup(self):
        adapter = self._from_config("wg.123456789012.us-east-1.redshift-serverless.amazonaws.com")
        assert adapter.compute_resource == "wg"
        assert adapter.workgroup_name == "wg"

    def test_provisioned_host_applies_compute_resource_as_cluster(self):
        adapter = self._from_config("other.abc123.us-east-1.redshift.amazonaws.com")
        assert adapter.cluster_identifier == "wg"

    def test_unrecognised_host_refuses_compute_resource(self):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="Ambiguous compute resource for platform 'redshift'"):
            self._from_config("redshift.internal.example.com")

    @pytest.mark.parametrize(
        "host",
        ["redshift.internal.example.com", "wg.123456789012.us-east-1.redshift-serverless.amazonaws.com"],
    )
    def test_saved_cluster_identifier_keeps_its_kind_after_migration(self, host):
        saved = migrate_saved_compute_keys(
            "redshift", {"host": host, "username": "u", "password": "p", "cluster_identifier": "saved-cluster"}
        )
        assert saved["compute_resource"] == "saved-cluster"

        adapter = self._from_config(**saved)

        assert adapter.cluster_identifier == "saved-cluster"
        assert adapter.workgroup_name != "saved-cluster"


class TestAthenaComputeUnification:
    @pytest.fixture(autouse=True)
    def _aws_credentials(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    def test_canonical_workgroup(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.check_platform_dependencies", return_value=(True, [])):
            adapter = AthenaAdapter.from_config(
                {"compute_resource": "wg", "s3_staging_dir": "s3://bucket/path", "database": "default"}
            )
        assert adapter.workgroup == "wg"

    def test_preserved_default(self):
        from benchbox.platforms.athena import AthenaAdapter

        with patch("benchbox.platforms.athena.check_platform_dependencies", return_value=(True, [])):
            adapter = AthenaAdapter.from_config({"s3_staging_dir": "s3://bucket/path", "database": "default"})
        assert adapter.workgroup == "primary"
