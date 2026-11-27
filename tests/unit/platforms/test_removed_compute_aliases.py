from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.platforms.adapter_factory import get_adapter
from benchbox.security.credentials import CredentialManager

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REMOVED_KEYWORDS = [
    ("benchbox.platforms.athena", "AthenaAdapter", "workgroup", "compute_resource"),
    ("benchbox.platforms.aws", "AthenaSparkAdapter", "workgroup", "compute_resource"),
    ("benchbox.platforms.aws", "AthenaSparkAdapter", "engine_version", "runtime_version"),
    ("benchbox.platforms.aws", "EMRServerlessAdapter", "application_id", "compute_resource"),
    ("benchbox.platforms.azure", "FabricSparkAdapter", "spark_pool_name", "compute_resource"),
    ("benchbox.platforms.azure", "SynapseSparkAdapter", "spark_pool_name", "compute_resource"),
    ("benchbox.platforms.gcp", "DataprocAdapter", "cluster_name", "compute_resource"),
    ("benchbox.platforms.onehouse", "QuantonAdapter", "cluster_size", "compute_size"),
    ("benchbox.platforms.redshift", "RedshiftAdapter", "workgroup_name", "compute_resource"),
    ("benchbox.platforms.redshift", "RedshiftAdapter", "cluster_identifier", "compute_resource"),
    ("benchbox.platforms.databricks", "DatabricksAdapter", "warehouse_id", "compute_resource"),
    ("benchbox.platforms.databricks.dataframe_adapter", "DatabricksDataFrameAdapter", "cluster_id", "compute_resource"),
]

REMOVED_PLATFORM_OPTIONS = [
    ("athena", "workgroup", "compute_resource"),
    ("athena-spark", "workgroup", "compute_resource"),
    ("athena-spark", "engine_version", "runtime_version"),
    ("emr-serverless", "application_id", "compute_resource"),
    ("fabric-spark", "spark_pool_name", "compute_resource"),
    ("synapse-spark", "spark_pool_name", "compute_resource"),
    ("dataproc", "cluster_name", "compute_resource"),
    ("quanton", "cluster_size", "compute_size"),
    ("redshift", "workgroup_name", "compute_resource"),
    ("redshift", "cluster_identifier", "compute_resource"),
    ("databricks", "warehouse_id", "compute_resource"),
    ("databricks-df", "cluster_id", "compute_resource"),
]


@pytest.mark.parametrize(("module", "name", "key", "replacement"), REMOVED_KEYWORDS)
def test_adapter_constructor_rejects_removed_keyword(module: str, name: str, key: str, replacement: str) -> None:
    adapter_class = getattr(importlib.import_module(module), name)

    with pytest.raises(TypeError, match=f"'{key}'.*'{replacement}'"):
        adapter_class(**{key: "value"})


@pytest.mark.parametrize(("platform", "key", "replacement"), REMOVED_PLATFORM_OPTIONS)
def test_platform_option_rejects_removed_key(platform: str, key: str, replacement: str) -> None:
    with pytest.raises(PlatformOptionError, match=f"'{key}'.*'{replacement}'"):
        PlatformHookRegistry.parse_options(platform, [(key, "value")])


@pytest.mark.parametrize(("platform", "key", "replacement"), REMOVED_PLATFORM_OPTIONS)
def test_get_adapter_rejects_removed_key(platform: str, key: str, replacement: str) -> None:
    with pytest.raises(TypeError, match=f"'{key}'.*'{replacement}'"):
        get_adapter(platform, **{key: "value"})


@pytest.mark.parametrize(
    ("platform", "saved_key"),
    [("athena", "workgroup"), ("snowflake", "warehouse"), ("athena-spark", "workgroup")],
)
def test_saved_credentials_with_removed_compute_key_load_as_compute_resource(
    tmp_path: Path, platform: str, saved_key: str
) -> None:
    path = tmp_path / "credentials.yaml"
    path.write_text(yaml.safe_dump({platform: {saved_key: "primary", "region": "us-east-1"}}), encoding="utf-8")
    path.chmod(0o600)

    loaded = CredentialManager(path).get_platform_credentials(platform)

    assert loaded is not None
    assert loaded["compute_resource"] == "primary"
    assert saved_key not in loaded
    assert loaded["region"] == "us-east-1"
