from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from benchbox.core.results.models import QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.runner.runner import _enrich_normalized_runtime_metadata
from benchbox.platforms import get_adapter
from benchbox.utils.runtime_env import DriverResolution
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _dependencies_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.platforms.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved="local-dev",
            actual="local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )
    for module in ("snowflake", "firebolt", "databricks.adapter", "redshift"):
        monkeypatch.setattr(
            f"benchbox.platforms.{module}.check_platform_dependencies", lambda *_args: (True, []), raising=False
        )


class _SnowflakeCursor:
    def __init__(self) -> None:
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str) -> _SnowflakeCursor:
        if sql.startswith("SHOW WAREHOUSES"):
            self._row = ("BIG_WH", "STARTED", "STANDARD", "Large", 1, 1)
        elif "CURRENT_REGION" in sql:
            self._row = ("AWS_US_EAST_1",)
        else:
            self._row = ("9.1.0",)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def close(self) -> None:
        return None


class _SnowflakeConnection:
    def cursor(self) -> _SnowflakeCursor:
        return _SnowflakeCursor()


def _bundle_compute(platform: str, adapter: Any, connection: Any = None) -> dict[str, Any]:
    result = make_benchmark_results(
        platform=platform,
        platform_info=adapter.get_platform_info(connection),
        query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1)],
    )
    enriched = _enrich_normalized_runtime_metadata(result, adapter=adapter)
    payload = build_result_payload(enriched)
    SchemaV2Validator().validate(payload)
    return payload["platform"]["compute"]


def test_snowflake_bundle_records_canonical_compute() -> None:
    adapter = get_adapter(
        "snowflake",
        account="acct",
        username="u",
        password="p",
        compute_resource="BIG_WH",
        compute_size="Large",
        benchmark="tpch",
        scale_factor=0.01,
    )

    compute = _bundle_compute("snowflake", adapter, _SnowflakeConnection())

    assert (compute["resource"], compute["resource_kind"], compute["size"]) == ("BIG_WH", "warehouse", "Large")
    assert compute["warehouse"] == "BIG_WH"


def test_firebolt_cloud_bundle_records_canonical_compute() -> None:
    adapter = get_adapter(
        "firebolt",
        deployment="cloud",
        client_id="c",
        client_secret="s",
        account_name="acct",
        compute_resource="eng1",
        compute_size="M",
        database="db",
        benchmark="tpch",
        scale_factor=0.01,
    )

    compute = _bundle_compute("firebolt", adapter)

    assert (compute["resource"], compute["resource_kind"], compute["size"]) == ("eng1", "engine", "M")


def test_clickhouse_cloud_bundle_records_canonical_compute() -> None:
    adapter = get_adapter(
        "clickhouse-cloud",
        host="svc1.us-east-2.aws.clickhouse.cloud",
        password="p",
        compute_resource="svc1",
        compute_size="16",
        benchmark="tpch",
        scale_factor=0.01,
    )

    compute = _bundle_compute("clickhouse-cloud", adapter)

    assert (compute["resource"], compute["resource_kind"], compute["size"]) == ("svc1", "service", "16")


class _DatabricksWorkspace:
    def __init__(self, host: str | None = None, token: str | None = None) -> None:
        self.warehouses = types.SimpleNamespace(get=self._warehouse)
        self.metastores = types.SimpleNamespace(summary=lambda: types.SimpleNamespace(region="us-east-1"))

    @staticmethod
    def _warehouse(warehouse_id: str) -> Any:
        return types.SimpleNamespace(
            id=warehouse_id,
            name="bench",
            cluster_size="Small",
            warehouse_type=types.SimpleNamespace(value="PRO"),
            enable_serverless_compute=True,
            auto_stop_mins=10,
            min_num_clusters=1,
            max_num_clusters=1,
            enable_photon=True,
            spot_instance_policy=None,
            channel=None,
            state=types.SimpleNamespace(value="RUNNING"),
        )


@pytest.fixture(autouse=True)
def _databricks_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = _DatabricksWorkspace
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)


def _databricks(**config: Any) -> Any:
    return get_adapter(
        "databricks",
        server_hostname="dbc-1.cloud.databricks.com",
        access_token="dapi-test",
        benchmark="tpch",
        scale_factor=0.01,
        **config,
    )


def test_databricks_bundle_records_canonical_compute() -> None:
    adapter = _databricks(compute_resource="abc123")

    compute = _bundle_compute("databricks", adapter)

    assert (compute["resource"], compute["resource_kind"], compute["size"]) == ("abc123", "warehouse", "Small")


def test_databricks_compute_resource_derives_the_warehouse_http_path() -> None:
    assert _databricks(compute_resource="bbb222").http_path == "/sql/1.0/warehouses/bbb222"


def test_databricks_compute_resource_wins_over_an_autodetected_http_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.platforms.databricks.adapter import DatabricksAdapter

    monkeypatch.setattr(
        DatabricksAdapter,
        "_auto_detect_databricks_config",
        classmethod(lambda cls, very_verbose=False: {"http_path": "/sql/1.0/warehouses/aaa111"}),
    )

    assert _databricks(compute_resource="bbb222").http_path == "/sql/1.0/warehouses/bbb222"


@pytest.mark.parametrize(
    "http_path",
    ["/sql/1.0/warehouses/aaa111", "/sql/1.0/endpoints/aaa111", "/sql/1.0/warehouses/aaa111?o=123"],
)
def test_databricks_matching_http_path_forms_are_accepted(http_path: str) -> None:
    adapter = _databricks(compute_resource="aaa111", http_path=http_path)

    assert adapter.http_path == http_path
    assert adapter.compute_resource == "aaa111"


@pytest.mark.parametrize("http_path", ["/sql/1.0/warehouses/aaa111", "/sql/1.0/endpoints/aaa111?o=1"])
def test_databricks_conflicting_http_path_fails_with_both_values(http_path: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        _databricks(compute_resource="bbb222", http_path=http_path)

    assert "compute_resource='bbb222'" in str(excinfo.value)
    assert f"http_path='{http_path}'" in str(excinfo.value)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("wg.1.us-east-1.redshift-serverless.amazonaws.com", ("workgroup_name", "workgroup")),
        ("cl.abc.us-east-1.redshift.amazonaws.com", ("cluster_identifier", "cluster")),
    ],
)
def test_redshift_bare_compute_resource_follows_the_deployment(host: str, expected: tuple[str, str]) -> None:
    adapter = get_adapter(
        "redshift", host=host, username="u", password="p", compute_resource="res1", benchmark="tpch", scale_factor=0.01
    )
    key, kind = expected

    assert getattr(adapter, key) == "res1"
    other = "cluster_identifier" if key == "workgroup_name" else "workgroup_name"
    assert getattr(adapter, other) is None
    compute = _bundle_compute("redshift", adapter)
    assert (compute["resource"], compute["resource_kind"]) == ("res1", kind)


def test_redshift_cluster_identifier_is_not_used_as_the_workgroup() -> None:
    adapter = get_adapter(
        "redshift",
        host="wg.1.us-east-1.redshift-serverless.amazonaws.com",
        username="u",
        password="p",
        cluster_identifier="c1",
        benchmark="tpch",
        scale_factor=0.01,
    )

    assert adapter.cluster_identifier == "c1"
    assert adapter.workgroup_name is None
