from __future__ import annotations

from typing import Any

import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _TuningConfigSentinel:
    def __repr__(self) -> str:
        return "<TuningConfigSentinel>"


TUNING_CONFIG_SENTINEL = _TuningConfigSentinel()
TUNING_ENABLED_SENTINEL = True
TUNING_SOURCE_SENTINEL = "auto_discovered"
TUNING_SOURCE_FILE_SENTINEL = "tuning/templates/duckdb_tuned.yaml"


def _registered_platform_names() -> list[str]:
    return sorted(PlatformRegistry.get_available_platforms())


def _build_stub_config(tmp_dir: str) -> dict[str, Any]:
    return {
        "benchmark": "tpch",
        "scale_factor": 1,
        "output_dir": tmp_dir,
        "host": "localhost",
        "port": 9999,
        "username": "benchbox",
        "user": "benchbox",
        "password": "benchbox",
        "database": "benchbox_test",
        "schema": "benchbox_test",
        "catalog": "duckdb",
        "account": "dummy_account",
        "warehouse": "COMPUTE_WH",
        "role": "SYSADMIN",
        "project_id": "dummy-project",
        "gcs_staging_dir": "gs://dummy-bucket/staging",
        "s3_staging_dir": "s3://dummy-bucket/staging",
        "s3_bucket": "dummy-bucket",
        "workgroup": "dummy-workgroup",
        "execution_role_arn": "arn:aws:iam::123456789012:role/dummy",
        "application_id": "dummy-app-id",
        "job_role": "arn:aws:iam::123456789012:role/dummy",
        "iam_role": "arn:aws:iam::123456789012:role/dummy",
        "s3_output_location": "s3://dummy-bucket/output",
        "server_hostname": "dummy.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/dummy",
        "access_token": "dummy-token",
        "client_id": "dummy-client-id",
        "client_secret": "dummy-client-secret",
        "account_name": "dummy-account",
        "engine_name": "dummy-engine",
        "api_key": "dummy-api-key",
        "api_endpoint": "https://dummy.onehouse.ai",
        "motherduck_token": "dummy-motherduck-token",
        "workspace_id": "dummy-workspace-id",
        "lakehouse_id": "dummy-lakehouse-id",
        "tenant_id": "dummy-tenant-id",
        "livy_endpoint": "https://dummy.livy.endpoint",
        "workspace_name": "dummy-workspace",
        "spark_pool_name": "dummy-pool",
        "storage_account": "dummystorageaccount",
        "storage_container": "dummy-container",
        "storage_path": "dummy/path",
        "server": "dummy-server.database.windows.net",
        "workspace": "dummy-workspace",
        "onelake_workspace": "dummy-onelake-ws",
        "master": "local[2]",
        "app_name": "benchbox-test",
        "endpoint": "http://localhost:50051",
        "tuning_config": TUNING_CONFIG_SENTINEL,
        "tuning_enabled": TUNING_ENABLED_SENTINEL,
        "unified_tuning_configuration": object(),
        "tuning_source": TUNING_SOURCE_SENTINEL,
        "tuning_source_file": TUNING_SOURCE_FILE_SENTINEL,
    }


@pytest.mark.parametrize("platform_name", _registered_platform_names())
def test_from_config_forwards_tuning_kwargs(platform_name: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter_class = PlatformRegistry.get_adapter_class(platform_name)
    if adapter_class is None:
        pytest.skip(f"{platform_name}: adapter class unavailable in this environment")

    stub_config = _build_stub_config(str(tmp_path))

    original_init = adapter_class.__init__
    with monkeypatch.context() as constructor_patch:
        constructor_patch.setattr(adapter_class, "__init__", PlatformAdapter.__init__)
        instance = adapter_class.from_config(dict(stub_config))

    assert adapter_class.__init__ is original_init

    assert instance.tuning_enabled is TUNING_ENABLED_SENTINEL, (
        f"{platform_name}: from_config() did not forward tuning_enabled onto the adapter"
    )
    assert instance.unified_tuning_configuration is TUNING_CONFIG_SENTINEL, (
        f"{platform_name}: from_config() did not forward tuning_config (live tuning object) onto the adapter"
    )
    assert instance.tuning_source == TUNING_SOURCE_SENTINEL, (
        f"{platform_name}: from_config() did not forward tuning_source onto the adapter"
    )
    assert instance.tuning_source_file == TUNING_SOURCE_FILE_SENTINEL, (
        f"{platform_name}: from_config() did not forward tuning_source_file onto the adapter"
    )
    assert (
        instance.platform_config.get("unified_tuning_configuration") is stub_config["unified_tuning_configuration"]
    ), f"{platform_name}: from_config() did not forward the literal unified_tuning_configuration key"
