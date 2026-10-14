from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.platforms.base.runtime_metadata import apply_normalized_result_metadata
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SECRET = "WH_SECRET_123"


def _snowflake() -> tuple[dict[str, Any], str]:
    from benchbox.platforms.snowflake import SnowflakeAdapter

    adapter = SnowflakeAdapter(
        account="acct",
        username="user",
        password="pw",
        warehouse=SECRET,
        compute_resource=SECRET,
        compute_size="LARGE",
        database="BENCH_DB",
    )
    cursor = Mock()
    row = [SECRET, "STARTED", "STANDARD", "LARGE"] + [None] * 27

    def execute(sql: str) -> Mock:
        lowered = sql.lower()
        if "current_version" in lowered:
            cursor.fetchone.return_value = ("8.0.0",)
        elif "current_region" in lowered:
            cursor.fetchone.return_value = ("AWS_US_EAST_1", "AWS")
        elif "SHOW WAREHOUSES" in sql:
            cursor.fetchone.return_value = tuple(row)
        elif "current_account_name" in lowered:
            cursor.fetchone.return_value = ("acct", "org")
        return cursor

    cursor.execute = Mock(side_effect=execute)
    connection = Mock()
    connection.cursor.return_value = cursor
    return _normalized(adapter, connection), "warehouse"


def _redshift() -> tuple[dict[str, Any], str]:
    from benchbox.platforms.redshift import RedshiftAdapter

    adapter = RedshiftAdapter(
        host="wg.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        username="user",
        password="pw",
        database="bench",
        compute_resource=SECRET,
        workgroup_name=SECRET,
    )
    cursor = MagicMock()
    cursor.fetchone.return_value = ("PostgreSQL 8.0.2 on Amazon Redshift 1.0.50481",)
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    serverless = {"workgroup_name": SECRET, "namespace_name": "ns-prod", "base_capacity_rpu": 128}
    with patch.object(adapter, "_get_serverless_metadata_api", return_value=serverless):
        return _normalized(adapter, connection), "workgroup"


def _databricks() -> tuple[dict[str, Any], str]:
    from benchbox.platforms.databricks import DatabricksAdapter

    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path=f"/sql/1.0/warehouses/{SECRET}",
        access_token="tok",
        compute_resource=SECRET,
        compute_size="2X-Small",
    )
    warehouse = MagicMock()
    warehouse.id = SECRET
    warehouse.name = SECRET
    warehouse.cluster_size = "2X-Small"
    warehouse.auto_stop_mins = 10
    warehouse.min_num_clusters = 1
    warehouse.max_num_clusters = 3
    warehouse.enable_photon = True
    warehouse.enable_serverless_compute = True
    warehouse.warehouse_type = MagicMock()
    warehouse.warehouse_type.value = "PRO"
    warehouse.spot_instance_policy = MagicMock()
    warehouse.spot_instance_policy.value = "COST_OPTIMIZED"
    warehouse.channel = MagicMock()
    warehouse.channel.name.value = "CHANNEL_NAME"
    warehouse.channel.dbsql_version = "2024.15"
    warehouse.state = MagicMock()
    warehouse.state.value = "RUNNING"
    workspace = MagicMock()
    workspace.warehouses.get.return_value = warehouse
    sdk = MagicMock()
    sdk.WorkspaceClient.return_value = workspace
    with (
        patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
        patch.dict("sys.modules", {"databricks.sdk": sdk}),
    ):
        return _normalized(adapter, None), "warehouse"


def _firebolt() -> tuple[dict[str, Any], str]:
    from benchbox.platforms.firebolt import FireboltAdapter

    adapter = FireboltAdapter(
        client_id="cid",
        client_secret="csec",
        account_name="acct",
        engine_name=SECRET,
        compute_resource=SECRET,
        compute_size="large",
        database="bench",
    )
    cursor = Mock()
    cursor.fetchone.side_effect = [("3.0.0",), (SECRET, "GENERAL_PURPOSE", "RUNNING")]
    connection = Mock()
    connection.cursor.return_value = cursor
    return _normalized(adapter, connection), "engine"


def _clickhouse_cloud() -> tuple[dict[str, Any], str]:
    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

    adapter = ClickHouseCloudAdapter(
        host="svc.us-east-2.aws.clickhouse.cloud",
        password="pw",
        database="bench",
        compute_resource=SECRET,
        service_id=SECRET,
        service_name=SECRET,
        compute_size="large",
    )
    info = adapter.get_platform_info(connection=None)
    info["compute_configuration"] = {"system_settings": {"max_threads": "12"}}
    return _normalized(adapter, None, info), "service"


def _normalized(adapter: Any, connection: Any, info: dict[str, Any] | None = None) -> dict[str, Any]:
    platform_info = info if info is not None else adapter.get_platform_info(connection=connection)
    metadata = adapter.get_normalized_result_metadata(platform_info=platform_info)
    return {"platform_info": platform_info, "metadata": metadata}


@pytest.fixture
def vendor_sdks() -> Iterator[None]:
    firebolt_modules = {
        name: MagicMock()
        for name in (
            "firebolt",
            "firebolt.client",
            "firebolt.client.auth",
            "firebolt.client.auth.firebolt_core",
            "firebolt.db",
        )
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict("sys.modules", firebolt_modules))
        stack.enter_context(patch("benchbox.platforms.snowflake.snowflake"))
        stack.enter_context(patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])))
        stack.enter_context(patch("benchbox.platforms.redshift.redshift_connector", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.redshift.boto3", MagicMock()))
        stack.enter_context(patch("benchbox.platforms.redshift.check_platform_dependencies", return_value=(True, [])))
        stack.enter_context(patch("benchbox.platforms.redshift.get_dependency_group_packages", return_value=[]))
        stack.enter_context(patch("benchbox.platforms.databricks.adapter.databricks_sql"))
        stack.enter_context(patch("benchbox.platforms.databricks.check_platform_dependencies", return_value=(True, [])))
        yield


READABLE_COMPUTE_FIELDS = {
    "snowflake": {"size": "LARGE", "warehouse_size": "LARGE"},
    "redshift": {"rpu": 128, "service_model": "serverless"},
    "databricks": {"size": "2X-Small", "warehouse_size": "2X-Small"},
    "firebolt": {"size": "large", "engine_type": "GENERAL_PURPOSE"},
    "clickhouse-cloud": {"size": "large", "service_model": "managed"},
}

BUILDERS: dict[str, Callable[[], tuple[dict[str, Any], str]]] = {
    "snowflake": _snowflake,
    "redshift": _redshift,
    "databricks": _databricks,
    "firebolt": _firebolt,
    "clickhouse-cloud": _clickhouse_cloud,
}


def _result_with_normalized_metadata(platform: str, built: dict[str, Any]) -> Any:
    result = make_benchmark_results(platform=platform, platform_info=built["platform_info"])
    return apply_normalized_result_metadata(result, built["metadata"])


def _result_with_raw_platform_metadata(platform: str, built: dict[str, Any]) -> Any:
    return make_benchmark_results(
        platform=platform, platform_info=built["platform_info"], platform_metadata=dict(built["metadata"])
    )


RESULT_CARRIERS = {
    "normalized-blocks": _result_with_normalized_metadata,
    "raw-platform-metadata": _result_with_raw_platform_metadata,
}


@pytest.mark.parametrize("carrier", sorted(RESULT_CARRIERS))
@pytest.mark.parametrize("platform", sorted(BUILDERS))
def test_public_export_drops_the_compute_resource_name(
    tmp_path: Path, vendor_sdks: None, platform: str, carrier: str
) -> None:
    built, expected_kind = BUILDERS[platform]()
    assert built["metadata"]["platform_compute"]["resource"] == SECRET
    result = RESULT_CARRIERS[carrier](platform, built)

    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])
    payload = json.loads(exported["json"].read_text(encoding="utf-8"))

    leaks = _paths_containing(payload, SECRET)
    assert not leaks, leaks
    if carrier == "normalized-blocks":
        compute = payload["platform"]["compute"]
    else:
        compute = payload["platform"]["raw_metadata"]["platform_compute"]
    assert "resource" not in compute
    assert compute["resource_kind"] == expected_kind
    assert READABLE_COMPUTE_FIELDS[platform].items() <= compute.items()


def test_public_export_keeps_observed_execution_engine_readable(tmp_path: Path, vendor_sdks: None) -> None:
    built, _ = _databricks()
    result = _result_with_normalized_metadata("databricks", built)

    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])
    payload = json.loads(exported["json"].read_text(encoding="utf-8"))

    assert SECRET not in json.dumps(payload)
    assert payload["platform"]["execution_engine"]["observed"] == "photon"


def _paths_containing(node: Any, needle: str, path: str = "$") -> list[str]:
    if isinstance(node, dict):
        return [hit for key, child in node.items() for hit in _paths_containing(child, needle, f"{path}.{key}")]
    if isinstance(node, list):
        return [hit for index, child in enumerate(node) for hit in _paths_containing(child, needle, f"{path}[{index}]")]
    return [path] if needle in str(node) else []
