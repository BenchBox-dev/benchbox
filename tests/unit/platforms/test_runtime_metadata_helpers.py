from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pytest

from benchbox.core.results.anonymization import AnonymizationConfig, AnonymizationManager
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.runner.runner import _enrich_normalized_runtime_metadata
from benchbox.platforms.base.docker_runtime import DockerCommandResult
from benchbox.platforms.base.runtime_metadata import (
    apply_normalized_result_metadata,
    build_default_normalized_result_metadata,
    collect_normalized_result_metadata,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Adapter:
    platform_name = "unknown"

    def __init__(self, platform_info: dict[str, Any], platform_config: dict[str, Any] | None = None) -> None:
        self._platform_info = platform_info
        self.platform_config = platform_config or {}

    def get_platform_info(self, _connection: Any = None) -> dict[str, Any]:
        return self._platform_info


class _DataFrameAdapter:
    platform_name = "Polars"
    family = "expression"
    platform_config: dict[str, Any] = {}

    def get_platform_info(self) -> dict[str, Any]:
        return {"platform": "Polars", "family": self.family, "version": "1.0.0"}


def _minimal_result(**overrides: Any) -> BenchmarkResults:
    defaults = {
        "benchmark_name": "TPC-H",
        "platform": "Trino",
        "scale_factor": 0.01,
        "execution_id": "runtime-metadata-test",
        "timestamp": datetime(2026, 1, 1, 12, 0, 0),
        "duration_seconds": 1.0,
        "total_queries": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "query_results": [],
    }
    defaults.update(overrides)
    return BenchmarkResults(**defaults)


def test_embedded_platform_maps_to_local_process_runtime() -> None:
    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "duckdb",
                "platform_name": "DuckDB",
                "execution_mode": "sql",
                "connection_mode": "memory",
                "configuration": {"database_path": ":memory:"},
            }
        )
    )

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "local_process"
    assert metadata["platform_deployment"]["deployment_type"] == "embedded"
    assert metadata["platform_deployment"]["endpoint_class"] == "embedded_process"
    assert metadata["platform_raw_config"]["database_path"] == ":memory:"


def test_platform_raw_config_excludes_internal_tuning_keys_and_reprs() -> None:

    class _TuningConfig:
        def __repr__(self) -> str:
            return "UnifiedTuningConfiguration(rules=[...], overrides={...}, " + "x" * 800 + ")"

    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "duckdb",
                "platform_name": "DuckDB",
                "execution_mode": "sql",
                "connection_mode": "memory",
                "configuration": {
                    "database_path": ":memory:",
                    "tuning_config": _TuningConfig(),
                    "unified_tuning_configuration": _TuningConfig(),
                    "tuning_enabled": True,
                    "_explicit_platform_options": {"database_path": ":memory:"},
                },
            }
        )
    )

    raw_config = metadata["platform_raw_config"]
    assert raw_config["database_path"] == ":memory:"
    assert "tuning_config" not in raw_config
    assert "unified_tuning_configuration" not in raw_config
    assert "tuning_enabled" not in raw_config
    assert "_explicit_platform_options" not in raw_config
    for value in raw_config.values():
        assert not (isinstance(value, str) and "UnifiedTuningConfiguration(" in value)


def test_dataframe_adapter_maps_to_dataframe_process_runtime() -> None:
    metadata = build_default_normalized_result_metadata(_DataFrameAdapter(), execution_mode="dataframe")

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "dataframe_process"
    assert metadata["platform_deployment"]["deployment_type"] == "dataframe"
    assert metadata["platform_deployment"]["endpoint_class"] == "embedded_process"


def test_remote_host_maps_to_remote_server_runtime() -> None:
    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "trino",
                "platform_name": "Trino",
                "connection_mode": "remote",
                "host": "trino.internal",
                "port": 8080,
            }
        )
    )

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "remote_server"
    assert metadata["execution_environment"]["platform_runtime"]["engine_host"] == "trino.internal"
    assert metadata["platform_deployment"]["deployment_type"] == "self_hosted"
    assert metadata["platform_deployment"]["endpoint_class"] == "remote_host"


def test_server_connection_mode_without_host_maps_to_self_hosted_remote_runtime() -> None:
    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "spark",
                "platform_name": "Spark",
                "connection_mode": "cluster",
            }
        )
    )

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "remote_server"
    assert metadata["platform_deployment"]["deployment_type"] == "self_hosted"
    assert metadata["platform_deployment"]["endpoint_class"] == "unknown"


def test_localhost_server_uses_docker_metadata_when_container_is_found() -> None:
    inspect_payload = [
        {
            "Id": "abc123",
            "Name": "/benchbox-postgres",
            "Config": {"Image": "postgres:16"},
            "HostConfig": {"NanoCpus": 1_000_000_000, "Memory": 1_073_741_824},
            "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}},
            "Os": "linux",
            "Architecture": "amd64",
        }
    ]
    responses = {
        ("docker", "version", "--format", "{{json .}}"): DockerCommandResult(
            0, stdout=json.dumps({"Server": {"Version": "26.1.0"}})
        ),
        ("docker", "ps", "--format", "{{json .}}"): DockerCommandResult(
            0,
            stdout=json.dumps(
                {
                    "ID": "abc123",
                    "Image": "postgres:16",
                    "Names": "benchbox-postgres",
                    "Ports": "127.0.0.1:5432->5432/tcp",
                }
            ),
        ),
        ("docker", "inspect", "abc123"): DockerCommandResult(0, stdout=json.dumps(inspect_payload)),
    }

    def run(args: Sequence[str]) -> DockerCommandResult:
        return responses[tuple(args)]

    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "postgresql",
                "platform_name": "PostgreSQL",
                "connection_mode": "remote",
                "host": "localhost",
                "port": 5432,
            }
        ),
        docker_runner=run,
    )

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "docker_container"
    assert metadata["execution_environment"]["container"]["image"] == "postgres:16"
    assert metadata["platform_deployment"]["endpoint_class"] == "localhost_port"


def test_serverless_cloud_platform_maps_cloud_and_compute_facets() -> None:
    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "bigquery",
                "platform_name": "BigQuery",
                "connection_mode": "remote",
                "cloud_provider": "GCP",
                "configuration": {
                    "project_id": "benchbox-project",
                    "location": "US",
                    "storage_bucket": "benchbox-data",
                    "query_cache_enabled": "false",
                },
                "compute_configuration": {"slot_capacity": 500},
            }
        )
    )

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "serverless"
    assert metadata["platform_deployment"]["deployment_type"] == "serverless"
    assert metadata["platform_cloud"]["provider"] == "gcp"
    assert metadata["platform_cloud"]["project"] == "benchbox-project"
    assert metadata["platform_compute"]["serverless_slots"] == 500
    assert metadata["platform_compute"]["cache_enabled"] is False
    assert metadata["platform_storage"]["bucket"] == "benchbox-data"


def test_adapter_hook_can_supply_normalized_metadata_without_required_abstract_method() -> None:
    class HookAdapter:
        def get_normalized_result_metadata(self, **_: Any) -> dict[str, Any]:
            return {
                "execution_environment": {
                    "platform_runtime": {
                        "runtime_type": "managed_cloud",
                        "collection_status": "available",
                        "source": "observed",
                    }
                }
            }

    metadata = collect_normalized_result_metadata(HookAdapter())

    assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
    assert metadata["execution_environment"]["platform_runtime"]["collection_status"] == "available"


def test_adapter_hook_failure_returns_unavailable_metadata() -> None:
    class HookAdapter:
        def get_normalized_result_metadata(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("optional metadata API unavailable")

    metadata = collect_normalized_result_metadata(HookAdapter())
    runtime = metadata["execution_environment"]["platform_runtime"]
    deployment = metadata["platform_deployment"]

    assert runtime["runtime_type"] == "unknown"
    assert runtime["collection_status"] == "unavailable"
    assert runtime["collection_error_class"] == "RuntimeError"
    assert runtime["collection_error_message"] == "RuntimeError: result metadata hook failed"
    assert deployment["deployment_type"] == "unknown"
    assert deployment["collection_status"] == "unavailable"


def test_adapter_hook_failure_excludes_identifiers_from_message() -> None:
    leaked = "Account 'synth-acct-9f31' user SYNTH_ADMIN warehouse SYNTH_WH host db-9f31.priv.example"

    class HookAdapter:
        def get_normalized_result_metadata(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError(leaked)

    metadata = collect_normalized_result_metadata(HookAdapter())
    runtime = metadata["execution_environment"]["platform_runtime"]
    message = runtime["collection_error_message"]

    assert runtime["collection_error_class"] == "RuntimeError"
    for value in ("synth-acct-9f31", "SYNTH_ADMIN", "SYNTH_WH", "db-9f31.priv.example"):
        assert value not in message

    anonymized = AnonymizationManager(AnonymizationConfig(machine_id_salt="synth-salt")).anonymize_result_payload(
        metadata
    )
    anonymized_message = anonymized["execution_environment"]["platform_runtime"]["collection_error_message"]
    for value in ("synth-acct-9f31", "SYNTH_ADMIN", "SYNTH_WH", "db-9f31.priv.example"):
        assert value not in anonymized_message


def test_result_merge_replaces_unknown_defaults_but_preserves_explicit_values() -> None:
    result = _minimal_result(
        execution_environment={
            "platform_runtime": {
                "runtime_type": "unknown",
                "collection_status": "unavailable",
                "source": "unavailable",
            }
        },
        platform_deployment={
            "deployment_type": "unknown",
            "endpoint_class": "remote_host",
            "metadata_source": "inferred",
            "collection_status": "partial",
        },
    )
    metadata = build_default_normalized_result_metadata(
        _Adapter(
            {
                "platform_type": "trino",
                "platform_name": "Trino",
                "connection_mode": "remote",
                "host": "trino.internal",
            }
        )
    )

    apply_normalized_result_metadata(result, metadata)

    assert result.execution_environment["platform_runtime"]["runtime_type"] == "remote_server"
    assert result.platform_deployment["deployment_type"] == "self_hosted"
    assert result.platform_deployment["endpoint_class"] == "remote_host"


def test_lifecycle_enrichment_skips_adapter_hook_when_result_already_has_adapter_metadata() -> None:
    class CountingAdapter:
        calls = 0

        def get_normalized_result_metadata(self, **_: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                "execution_environment": {
                    "platform_runtime": {
                        "runtime_type": "remote_server",
                        "collection_status": "partial",
                        "source": "inferred",
                    }
                },
                "platform_deployment": {
                    "deployment_type": "self_hosted",
                    "endpoint_class": "remote_host",
                    "metadata_source": "inferred",
                    "collection_status": "partial",
                },
            }

    adapter = CountingAdapter()
    result = _minimal_result(
        execution_environment={
            "platform_runtime": {
                "runtime_type": "remote_server",
                "collection_status": "partial",
                "source": "inferred",
            }
        },
        platform_deployment={
            "deployment_type": "self_hosted",
            "endpoint_class": "remote_host",
            "metadata_source": "inferred",
            "collection_status": "partial",
        },
    )

    _enrich_normalized_runtime_metadata(result, adapter=adapter)

    assert adapter.calls == 0
