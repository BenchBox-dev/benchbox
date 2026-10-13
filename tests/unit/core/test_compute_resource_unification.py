import json
from datetime import datetime
from unittest.mock import patch

import pytest

import benchbox.platforms
from benchbox.core.compute_resource import (
    declares_compute,
    get_compute_declaration,
    normalize_compute_options,
    register_compute_specs,
    resolve_resource_kind,
)
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from tests.utilities.deprecation_capture import captured_deprecations

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
    assert declaration.resource_aliases == expected[1]
    assert declaration.size_aliases == expected[2]


def test_platform_without_compute_declares_nothing() -> None:
    assert declares_compute("duckdb") is False
    assert get_compute_declaration("duckdb") is None
    assert normalize_compute_options("duckdb", {"warehouse": "X"}) == {"warehouse": "X"}


def test_spec_registration_is_idempotent() -> None:
    register_compute_specs()
    register_compute_specs()


@pytest.mark.parametrize(
    ("platform", "pairs", "canonical"),
    [
        ("databend", [("warehouse", "CLOUD_WH")], {"compute_resource": "CLOUD_WH"}),
        ("snowflake", [("warehouse", "ANALYTICS_WH")], {"compute_resource": "ANALYTICS_WH"}),
        ("snowflake", [("warehouse_size", "XLARGE")], {"compute_size": "XLARGE"}),
        ("databricks", [("warehouse_id", "abc123")], {"compute_resource": "abc123"}),
        ("quanton", [("cluster_size", "large")], {"compute_size": "large"}),
        ("firebolt", [("engine_name", "prod_eng")], {"compute_resource": "prod_eng"}),
        ("firebolt", [("engine_size", "S")], {"compute_size": "S"}),
        ("fabric_dw", [("warehouse", "WH1")], {"database": "WH1"}),
    ],
)
def test_native_alias_parses_to_canonical(
    platform: str, pairs: list, canonical: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with captured_deprecations(caplog) as seen:
        parsed = PlatformHookRegistry.parse_options(platform, pairs)
    assert len(seen) == 1
    assert f"'{pairs[0][0]}'" in seen[0]
    assert "0.6.0" in seen[0]
    for key, value in canonical.items():
        assert parsed[key] == value


@pytest.mark.parametrize(
    ("platform", "pairs", "canonical"),
    [
        ("snowflake", [("compute_resource", "ANALYTICS_WH")], {"compute_resource": "ANALYTICS_WH"}),
        ("firebolt", [("compute_size", "M")], {"compute_size": "M"}),
        ("clickhouse-cloud", [("compute_size", "M")], {"compute_size": "M"}),
    ],
)
def test_canonical_key_parses_without_warning(
    platform: str, pairs: list, canonical: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with captured_deprecations(caplog) as seen:
        parsed = PlatformHookRegistry.parse_options(platform, pairs)
    assert seen == []
    for key, value in canonical.items():
        assert parsed[key] == value


def test_compute_size_rejected_where_unsupported() -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options("databend", [("compute_size", "L")])


def test_compute_resource_rejected_on_quanton() -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options("quanton", [("compute_resource", "X")])


def test_deprecated_alias_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    with captured_deprecations(caplog) as seen:
        normalize_compute_options("databend", {"warehouse": "A"})
        normalize_compute_options("databend", {"warehouse": "A"})
    assert len(seen) == 1


def test_canonical_native_disagreement_fails() -> None:
    with pytest.raises(PlatformOptionError, match="Conflicting compute settings"):
        normalize_compute_options("snowflake", {"compute_resource": "NEW_WH", "warehouse": "COMPUTE_WH"})


def test_conflicting_native_aliases_fail() -> None:
    with pytest.raises(PlatformOptionError, match="Conflicting compute settings"):
        normalize_compute_options(
            "redshift", {"workgroup_name": "serverless-wg", "cluster_identifier": "provisioned-cluster"}
        )


def test_matching_native_aliases_agree() -> None:
    resolved = normalize_compute_options("firebolt", {"engine_size": "S", "compute_size": "S"})
    assert resolved["compute_size"] == "S"


@pytest.mark.parametrize(
    ("platform", "merged", "expected"),
    [
        ("snowflake", {"warehouse": "W"}, "warehouse"),
        ("firebolt", {"engine_name": "E"}, "engine"),
        ("quanton", {"cluster_size": "small"}, None),
        ("redshift", {"workgroup_name": "wg"}, "workgroup"),
        ("redshift", {"cluster_identifier": "c"}, "cluster"),
        ("duckdb", {"warehouse": "W"}, None),
    ],
)
def test_resource_kind_resolution(platform: str, merged: dict, expected: str | None) -> None:
    assert resolve_resource_kind(platform, merged) == expected


def test_generic_only_kind_resolution_fails_closed_on_dict_kind() -> None:
    with pytest.raises(PlatformOptionError, match="Ambiguous compute resource"):
        resolve_resource_kind("redshift", {"compute_resource": "wg"})


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
        params = adapter._get_connection_params()
        assert params["warehouse"] == "ANALYTICS_WH"

    def test_preserved_defaults(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="a", username="u", password="p")
        assert adapter.warehouse == "COMPUTE_WH"
        assert adapter.warehouse_size == "MEDIUM"

    def test_native_alias_still_resolves(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="LEGACY_WH")
        assert adapter.warehouse == "LEGACY_WH"

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
            warehouse_id="abc123",
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

    def test_warehouse_alias_warns_and_resolves(self, caplog: pytest.LogCaptureFixture):
        from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

        with captured_deprecations(caplog) as seen:
            adapter = FabricWarehouseAdapter(server="ws.datawarehouse.fabric.microsoft.com", warehouse="WH1")
        assert len(seen) == 1
        assert adapter.database == "WH1"


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


@pytest.mark.parametrize(
    ("platform", "pairs", "canonical"),
    [
        ("databricks-df", [("cluster_id", "c-123")], {"compute_resource": "c-123"}),
        ("athena-spark", [("workgroup", "spark-wg")], {"compute_resource": "spark-wg"}),
        ("synapse-spark", [("spark_pool_name", "pool1")], {"compute_resource": "pool1"}),
        ("fabric-spark", [("spark_pool_name", "pool1")], {"compute_resource": "pool1"}),
        ("dataproc", [("cluster_name", "dp-cluster")], {"compute_resource": "dp-cluster"}),
        ("emr-serverless", [("application_id", "app-123")], {"compute_resource": "app-123"}),
        ("firebolt", [("engine_name", "eng")], {"compute_resource": "eng"}),
        ("clickhouse-cloud", [("service_id", "svc-1")], {"compute_resource": "svc-1"}),
        ("redshift", [("workgroup_name", "wg")], {"compute_resource": "wg"}),
        ("redshift", [("cluster_identifier", "cl")], {"compute_resource": "cl"}),
        ("athena", [("workgroup", "primary")], {"compute_resource": "primary"}),
    ],
)
def test_remaining_native_aliases_parse_to_canonical(
    platform: str, pairs: list, canonical: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with captured_deprecations(caplog) as seen:
        parsed = PlatformHookRegistry.parse_options(platform, pairs)
    assert len(seen) == 1
    for key, value in canonical.items():
        assert parsed[key] == value


@pytest.mark.parametrize("platform", ["databricks", "databricks-df", "athena", "athena-spark", "databend"])
def test_compute_size_rejected_without_size_declaration(platform: str) -> None:
    with pytest.raises(PlatformOptionError, match="Unknown platform option"):
        PlatformHookRegistry.parse_options(platform, [("compute_size", "L")])


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

        adapter = AthenaSparkAdapter(workgroup="wg", s3_staging_dir="s3://bucket/path")
        assert adapter.runtime_version is None

    def test_deprecated_engine_version_warns_and_resolves(self, caplog: pytest.LogCaptureFixture):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        with captured_deprecations(caplog) as seen:
            adapter = AthenaSparkAdapter(
                workgroup="wg", s3_staging_dir="s3://bucket/path", engine_version="PySpark engine version 2"
            )
        assert len(seen) == 1
        assert adapter.runtime_version == "PySpark engine version 2"

    def test_canonical_workgroup_from_config(self):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        adapter = AthenaSparkAdapter.from_config({"compute_resource": "spark-wg", "s3_staging_dir": "s3://bucket/path"})
        assert adapter.workgroup == "spark-wg"


class TestSynapseSparkComputeUnification:
    def test_canonical_pool_from_config(self):
        from unittest.mock import MagicMock

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
        from unittest.mock import MagicMock

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
                deployment_mode="core",
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
            adapter = FireboltAdapter(deployment_mode="core", url="http://localhost:3473", database="db")
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


class TestQuantonComputeUnification:
    def test_canonical_size_from_config(self):
        from benchbox.core.compute_resource import normalize_compute_options

        resolved = normalize_compute_options("quanton", {"compute_size": "large"})
        assert resolved["compute_size"] == "large"
        resolved = normalize_compute_options("quanton", {"cluster_size": "medium"})
        assert resolved["compute_size"] == "medium"


class TestRedshiftComputeUnification:
    def test_canonical_workgroup_from_config(self):
        from benchbox.platforms.redshift import RedshiftAdapter

        try:
            adapter = RedshiftAdapter.from_config(
                {
                    "host": "h",
                    "database": "dev",
                    "username": "u",
                    "password": "p",
                    "workgroup_name": "wg",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                }
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")
        assert adapter.workgroup_name == "wg"


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


_GUIDE_PLATFORM = {
    "snowflake": "snowflake",
    "databricks": "databricks",
    "databricks-dataframe": "databricks-df",
    "athena": "athena",
    "athena-spark": "athena-spark",
    "firebolt": "firebolt",
    "synapse-spark": "synapse-spark",
    "fabric-spark": "fabric-spark",
    "emr-serverless": "emr-serverless",
    "snowpark-connect": "snowpark-connect",
    "microsoft-fabric": "fabric_dw",
    "redshift": "redshift",
}


def test_docs_compute_examples_parse_against_registry() -> None:
    import re
    from pathlib import Path

    from benchbox.core.compute_resource import (
        COMPUTE_RESOURCE_OPTION,
        COMPUTE_SIZE_OPTION,
        get_compute_declaration,
    )

    pattern = re.compile(r"--platform-option\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
    repo_root = Path(__file__).resolve().parents[3]
    failures: list[str] = []
    for guide, platform in _GUIDE_PLATFORM.items():
        declaration = get_compute_declaration(platform)
        compute_keys = {COMPUTE_RESOURCE_OPTION, COMPUTE_SIZE_OPTION}
        if declaration is not None:
            compute_keys.update(declaration.resource_aliases)
            compute_keys.update(declaration.size_aliases)
        if platform == "fabric_dw":
            compute_keys.update({"database", "warehouse"})
        text = (repo_root / "docs" / "platforms" / f"{guide}.md").read_text(encoding="utf-8")
        accepted = set(PlatformHookRegistry.list_option_specs(platform))
        for spec in PlatformHookRegistry._option_specs.get(platform, {}).values():
            accepted.update(getattr(spec, "aliases", ()) or ())
        doc_keys = {match.group(1) for match in pattern.finditer(text)}
        for key in sorted(doc_keys & compute_keys):
            if key not in accepted:
                failures.append(f"{guide}: {key}")
    assert failures == []
