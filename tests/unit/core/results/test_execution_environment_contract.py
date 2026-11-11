from __future__ import annotations

import json
from datetime import datetime

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.environment import (
    ClientLinkEnvironment,
    ContainerEnvironment,
    NormalizedExecutionEnvironment,
    PlatformCloudMetadata,
    PlatformComputeMetadata,
    PlatformDeploymentMetadata,
    PlatformRuntimeEnvironment,
    PlatformStorageMetadata,
)
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _minimal_result(**overrides):
    defaults = {
        "benchmark_name": "TPC-H",
        "platform": "DuckDB",
        "scale_factor": 0.01,
        "execution_id": "env-contract-test",
        "timestamp": datetime(2026, 1, 1, 12, 0, 0),
        "duration_seconds": 1.2,
        "total_queries": 1,
        "successful_queries": 1,
        "failed_queries": 0,
        "query_results": [
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        ],
    }
    defaults.update(overrides)
    return BenchmarkResults(**defaults)


def _builder_result(platform: PlatformInfoInput | None = None, system_profile: dict[str, object] | None = None):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=0.01, benchmark_id="tpch"),
        platform=platform or PlatformInfoInput(name="DuckDB", platform_version="1.0.0", client_library_version="1.0.0"),
        execution_id="builder-env-test",
    )
    if system_profile is not None:
        builder.set_system_profile(system_profile)
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        )
    )
    return builder.build()


def test_builder_carries_client_host_and_default_platform_runtime_on_result_object() -> None:
    result = _builder_result(
        system_profile={
            "os_type": "Darwin",
            "os_release": "24.4.0",
            "architecture": "arm64",
            "cpu_count": 12,
        }
    )

    assert result.execution_environment["client_host"]["os"] == "Darwin 24.4.0"
    assert result.execution_environment["client_host"]["arch"] == "arm64"
    assert result.execution_environment["platform_runtime"] == {
        "runtime_type": "unknown",
        "collection_status": "unavailable",
        "source": "unavailable",
    }

    payload = build_result_payload(result)
    assert payload["environment"]["client_host"]["os"] == "Darwin 24.4.0"
    assert payload["environment"]["platform_runtime"]["runtime_type"] == "unknown"


def test_builder_carries_conservative_platform_deployment_from_legacy_platform_info() -> None:
    result = _builder_result(
        platform=PlatformInfoInput(
            name="ClickHouse Server",
            platform_version="24.1",
            client_library_version="0.8",
            connection_mode="server",
            config={"host": "localhost", "port": 8123},
        )
    )

    assert result.platform_deployment["deployment_type"] == "unknown"
    assert result.platform_deployment["endpoint_class"] == "localhost_port"
    assert result.platform_deployment["metadata_source"] == "inferred"
    assert result.platform_raw_config == {"host": "localhost", "port": 8123}


def test_environment_block_keeps_legacy_host_keys_and_adds_normalized_client_host() -> None:
    result = _minimal_result(
        system_profile={
            "os_type": "Darwin",
            "os_release": "24.4.0",
            "architecture": "arm64",
            "cpu_count": 12,
            "memory_gb": 64,
            "python_version": "3.12.8",
            "machine_id": "machine-test",
        },
        platform_info={
            "platform_version": "1.0.0",
            "client_library_version": "1.0.0",
            "connection_mode": "in-memory",
            "configuration": {"threads": 4},
        },
    )

    payload = build_result_payload(result)

    assert payload["environment"]["os"] == "Darwin 24.4.0"
    assert payload["environment"]["arch"] == "arm64"
    assert payload["environment"]["client_host"] == {
        "os": "Darwin 24.4.0",
        "arch": "arm64",
        "cpu_count": 12,
        "memory_gb": 64,
        "python": "3.12.8",
        "machine_id": "machine-test",
    }
    assert payload["environment"]["platform_runtime"] == {
        "runtime_type": "unknown",
        "collection_status": "unavailable",
        "source": "unavailable",
    }
    assert payload["platform"]["config"]["threads"] == 4
    assert payload["platform"]["deployment"]["connection_mode"] == "in-memory"
    assert payload["platform"]["deployment"]["endpoint_class"] == "embedded_process"
    assert payload["platform"]["raw_config"]["threads"] == 4


def test_partial_explicit_client_host_merges_with_system_profile_for_legacy_compatibility() -> None:
    result = _minimal_result(
        system_profile={
            "os_type": "Darwin",
            "os_release": "24.4.0",
            "architecture": "arm64",
            "cpu_count": 12,
        },
        execution_environment=NormalizedExecutionEnvironment(client_host={"machine_id": "explicit-machine"}),
    )

    payload = build_result_payload(result)

    assert payload["environment"]["os"] == "Darwin 24.4.0"
    assert payload["environment"]["arch"] == "arm64"
    assert payload["environment"]["client_host"]["os"] == "Darwin 24.4.0"
    assert payload["environment"]["client_host"]["machine_id"] == "explicit-machine"


def test_url_form_localhost_endpoint_is_classified_as_localhost_port() -> None:
    result = _minimal_result(
        platform_info={
            "platform_version": "24.1",
            "client_library_version": "0.8",
            "configuration": {"endpoint": "http://localhost:8123"},
        },
    )

    payload = build_result_payload(result)

    assert payload["platform"]["deployment"]["endpoint_class"] == "localhost_port"
    assert payload["platform"]["deployment"]["collection_status"] == "partial"


def test_explicit_runtime_container_and_platform_facets_serialize_to_canonical_blocks() -> None:
    result = _minimal_result(
        platform="ClickHouse Server",
        execution_environment=NormalizedExecutionEnvironment(
            platform_runtime=PlatformRuntimeEnvironment(
                runtime_type="docker_container",
                collection_status="available",
                source="observed",
                os="Linux",
                arch="amd64",
                engine_host="container:clickhouse",
            ),
            container=ContainerEnvironment(
                collection_status="available",
                source="observed",
                docker_engine_version="26.1.0",
                image="clickhouse/clickhouse-server:24.1",
                image_digest="sha256:abc123",
                container_id="container-123",
                container_name="benchbox-clickhouse",
                os="linux",
                arch="amd64",
                requested_platform="linux/amd64",
                cpu_limit=4,
                memory_limit="8g",
                port_mappings={"8123/tcp": [{"host_ip": "127.0.0.1", "host_port": "8123"}]},
                bind_mounts=[{"source": "/tmp/data", "destination": "/var/lib/clickhouse"}],
            ),
        ),
        platform_deployment=PlatformDeploymentMetadata(
            deployment_type="self_hosted",
            connection_mode="localhost",
            endpoint_class="localhost_port",
            metadata_source="observed",
            collection_status="available",
        ),
        platform_cloud=PlatformCloudMetadata(
            provider="aws",
            region="us-east-1",
            account="account-hash",
            source="requested",
            collection_status="partial",
        ),
        platform_compute=PlatformComputeMetadata(
            node_type="c6i.2xlarge",
            node_count=1,
            cache_enabled=False,
            source="observed",
            collection_status="available",
        ),
        platform_storage=PlatformStorageMetadata(
            table_format="native",
            compression="zstd",
            cleanup_policy="drop",
            source="requested",
            collection_status="partial",
        ),
        platform_raw_config={"host": "localhost", "port": 8123},
        platform_raw_metadata={"server_version": "24.1"},
    )

    payload = build_result_payload(result)

    assert payload["environment"]["platform_runtime"]["runtime_type"] == "docker_container"
    assert payload["environment"]["platform_runtime"]["os"] == "Linux"
    assert payload["environment"]["container"]["image"] == "clickhouse/clickhouse-server:24.1"
    assert payload["environment"]["container"]["port_mappings"]["8123/tcp"][0]["host_port"] == "8123"
    assert payload["platform"]["deployment"]["endpoint_class"] == "localhost_port"
    assert payload["platform"]["cloud"]["region"] == "us-east-1"
    assert payload["platform"]["compute"]["node_type"] == "c6i.2xlarge"
    assert payload["platform"]["compute"]["cache_enabled"] is False
    assert payload["platform"]["storage"]["compression"] == "zstd"
    assert payload["platform"]["raw_config"] == {"host": "localhost", "port": 8123}
    assert payload["platform"]["raw_metadata"] == {"server_version": "24.1"}


def test_loader_preserves_normalized_environment_metadata_for_round_trip() -> None:
    payload = build_result_payload(
        _minimal_result(
            system_profile={"os_type": "Linux", "os_release": "6.8", "architecture": "x86_64"},
            execution_environment=NormalizedExecutionEnvironment(
                platform_runtime=PlatformRuntimeEnvironment(
                    runtime_type="remote_server",
                    collection_status="partial",
                    source="inferred",
                    engine_host="db.internal",
                )
            ),
            platform_deployment=PlatformDeploymentMetadata(
                deployment_type="self_hosted",
                endpoint_class="remote_host",
                metadata_source="inferred",
                collection_status="partial",
            ),
        )
    )

    reconstructed = reconstruct_benchmark_results(payload)
    round_trip = build_result_payload(reconstructed)

    assert reconstructed.system_profile["os_type"] == "Linux"
    assert reconstructed.execution_environment["platform_runtime"]["runtime_type"] == "remote_server"
    assert reconstructed.platform_deployment["endpoint_class"] == "remote_host"
    assert round_trip["environment"]["client_host"]["os"] == "Linux 6.8"
    assert round_trip["environment"]["platform_runtime"]["engine_host"] == "db.internal"
    assert round_trip["platform"]["deployment"]["endpoint_class"] == "remote_host"


def test_loader_uses_legacy_flat_host_fields_when_client_host_is_partial() -> None:
    payload = build_result_payload(
        _minimal_result(system_profile={"os_type": "Linux", "os_release": "6.8", "architecture": "x86_64"})
    )
    payload["environment"]["client_host"] = {"machine_id": "nested-machine"}

    reconstructed = reconstruct_benchmark_results(payload)

    assert reconstructed.system_profile["os_type"] == "Linux"
    assert reconstructed.system_profile["architecture"] == "x86_64"
    assert reconstructed.system_profile["machine_id"] == "nested-machine"


def test_anonymized_export_omits_machine_id_in_flat_and_normalized_client_host(tmp_path) -> None:
    payload = build_result_payload(
        _minimal_result(
            system_profile={
                "os_type": "Linux",
                "architecture": "x86_64",
                "machine_id": "raw-machine-id",
            }
        )
    )

    ResultExporter(output_dir=tmp_path, anonymize=True)._apply_anonymization(payload)

    assert "machine_id" not in payload["environment"]
    assert "machine_id" not in payload["environment"].get("client_host", {})
    assert "raw-machine-id" not in json.dumps(payload)


def test_client_link_environment_defaults() -> None:
    link = ClientLinkEnvironment()
    assert link.collection_status == "unavailable"
    assert link.source == "unavailable"
    assert link.client_region is None
    assert link.client_cloud is None
    assert link.statement_overhead_ms is None
    assert link.collection_error_class is None
    assert link.collection_error_message is None
    assert link.to_dict() == {
        "collection_status": "unavailable",
        "source": "unavailable",
    }


def test_client_link_environment_populated() -> None:
    link = ClientLinkEnvironment(
        collection_status="available",
        source="observed",
        client_region="us-east-1",
        client_cloud="aws",
        statement_overhead_ms={"samples": 5, "min": 1.25, "median": 2.1},
    )
    assert link.to_dict() == {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 1.25, "median": 2.1},
    }


def test_build_environment_payload_preserves_client_link_without_flat_pollution() -> None:
    from benchbox.core.results.environment import build_environment_payload

    link = ClientLinkEnvironment(
        collection_status="available",
        source="observed",
        client_region="us-east-1",
        client_cloud="aws",
        statement_overhead_ms={"samples": 5, "min": 1.25, "median": 1.4},
    )
    env = NormalizedExecutionEnvironment(client_link=link)
    payload = build_environment_payload(system_profile={"os_type": "Darwin"}, execution_environment=env)

    assert "client_link" in payload
    assert payload["client_link"] == {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 1.25, "median": 1.4},
    }

    assert "client_region" not in payload
    assert "client_cloud" not in payload
    assert "statement_overhead_ms" not in payload


def test_builder_and_loader_client_link_round_trip() -> None:
    link = ClientLinkEnvironment(
        collection_status="available",
        source="observed",
        client_region="us-west-2",
        client_cloud="aws",
        statement_overhead_ms={"samples": 5, "min": 0.8, "median": 1.1},
    )
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=0.01, benchmark_id="tpch"),
        platform=PlatformInfoInput(name="DuckDB", platform_version="1.0.0", client_library_version="1.0.0"),
        execution_id="builder-client-link-test",
    )
    builder.set_execution_environment(NormalizedExecutionEnvironment(client_link=link))
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        )
    )
    result = builder.build()

    assert "client_link" in result.execution_environment
    assert result.execution_environment["client_link"]["client_region"] == "us-west-2"
    assert result.execution_environment["client_link"]["client_cloud"] == "aws"

    payload = build_result_payload(result)
    assert "client_link" in payload["environment"]
    assert payload["environment"]["client_link"]["client_region"] == "us-west-2"

    reconstructed = reconstruct_benchmark_results(payload)
    assert "client_link" in reconstructed.execution_environment
    assert reconstructed.execution_environment["client_link"]["client_region"] == "us-west-2"

    round_trip = build_result_payload(reconstructed)
    assert round_trip["environment"]["client_link"]["client_region"] == "us-west-2"
    assert round_trip["environment"]["client_link"]["statement_overhead_ms"] == {
        "samples": 5,
        "min": 0.8,
        "median": 1.1,
    }


