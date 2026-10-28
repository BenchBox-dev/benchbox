from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from benchbox.platforms.base.docker_runtime import (
    DockerCommandResult,
    DockerRuntimeDiscoveryRequest,
    discover_docker_runtime,
    docker_request_from_config,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _runner(responses: dict[tuple[str, ...], DockerCommandResult]):
    def run(args: Sequence[str]) -> DockerCommandResult:
        key = tuple(args)
        if key not in responses:
            raise AssertionError(f"Unexpected Docker command: {key}")
        return responses[key]

    return run


def test_discovers_container_by_host_port_and_image_without_live_docker() -> None:
    inspect_payload = [
        {
            "Id": "abc123",
            "Name": "/benchbox-clickhouse",
            "Config": {"Image": "clickhouse/clickhouse-server:24.1"},
            "State": {"Status": "running"},
            "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 8_589_934_592, "Platform": "linux/amd64"},
            "NetworkSettings": {"Ports": {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8123"}]}},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/tmp/clickhouse",
                    "Destination": "/var/lib/clickhouse",
                    "Mode": "rw",
                    "RW": True,
                },
                {
                    "Type": "volume",
                    "Source": "clickhouse-data",
                    "Destination": "/var/lib/clickhouse/store",
                    "Mode": "z",
                    "RW": True,
                },
            ],
            "Os": "linux",
            "Architecture": "amd64",
            "RepoDigests": ["clickhouse/clickhouse-server@sha256:abc"],
        }
    ]
    run = _runner(
        {
            ("docker", "version", "--format", "{{json .}}"): DockerCommandResult(
                0, stdout=json.dumps({"Server": {"Version": "26.1.0"}})
            ),
            ("docker", "ps", "--format", "{{json .}}"): DockerCommandResult(
                0,
                stdout=json.dumps(
                    {
                        "ID": "abc123",
                        "Image": "clickhouse/clickhouse-server:24.1",
                        "Names": "benchbox-clickhouse",
                        "Ports": "127.0.0.1:8123->8123/tcp",
                        "Labels": "com.docker.compose.service=clickhouse",
                    }
                ),
            ),
            ("docker", "inspect", "abc123"): DockerCommandResult(0, stdout=json.dumps(inspect_payload)),
        }
    )

    result = discover_docker_runtime(
        DockerRuntimeDiscoveryRequest(platform_name="clickhouse-server", host="localhost", port=8123),
        runner=run,
    )

    assert result["platform_runtime"]["runtime_type"] == "docker_container"
    assert result["platform_runtime"]["collection_status"] == "available"
    assert result["platform_runtime"]["os"] == "linux"
    assert result["container"]["docker_engine_version"] == "26.1.0"
    assert result["container"]["image"] == "clickhouse/clickhouse-server:24.1"
    assert result["container"]["image_digest"] == "clickhouse/clickhouse-server@sha256:abc"
    assert result["container"]["cpu_limit"] == 2.0
    assert result["container"]["memory_limit"] == 8_589_934_592
    assert result["container"]["port_mappings"]["8123/tcp"][0]["HostPort"] == "8123"
    assert result["container"]["bind_mounts"][0]["destination"] == "/var/lib/clickhouse"
    assert result["container"]["volumes"][0]["source"] == "clickhouse-data"
    assert result["container"]["state"] == "running"
    assert result["container"]["host_port_matched"] is True


def test_docker_absent_returns_unavailable_metadata_without_failing() -> None:
    def missing_docker(_args: Sequence[str]) -> DockerCommandResult:
        raise FileNotFoundError("docker")

    result = discover_docker_runtime(
        DockerRuntimeDiscoveryRequest(platform_name="postgresql", host="localhost", port=5432),
        runner=missing_docker,
    )

    assert result["platform_runtime"]["collection_status"] == "unavailable"
    assert result["platform_runtime"]["collection_error_class"] == "docker_not_found"
    assert result["container"]["collection_errors"][0]["type"] == "docker_not_found"


def test_permission_denied_returns_unavailable_metadata_with_engine_version() -> None:
    run = _runner(
        {
            ("docker", "version", "--format", "{{json .}}"): DockerCommandResult(
                0, stdout=json.dumps({"Server": {"Version": "26.1.0"}})
            ),
            ("docker", "ps", "--format", "{{json .}}"): DockerCommandResult(
                1, stderr="permission denied while trying to connect to the Docker daemon socket"
            ),
        }
    )

    result = discover_docker_runtime(
        DockerRuntimeDiscoveryRequest(platform_name="trino", host="localhost", port=8080),
        runner=run,
    )

    assert result["platform_runtime"]["collection_error_class"] == "permission_denied"
    assert result["container"]["docker_engine_version"] == "26.1.0"


def test_remote_host_skips_docker_collection() -> None:
    result = discover_docker_runtime(DockerRuntimeDiscoveryRequest(platform_name="postgresql", host="db.example.com"))

    assert result["platform_runtime"]["collection_status"] == "unavailable"
    assert result["platform_runtime"]["collection_error_class"] == "not_localhost"


def test_ipv6_loopback_is_treated_as_localhost_for_docker_discovery() -> None:
    def missing_docker(_args: Sequence[str]) -> DockerCommandResult:
        raise FileNotFoundError("docker")

    result = discover_docker_runtime(
        DockerRuntimeDiscoveryRequest(platform_name="postgresql", host="::1", port=5432),
        runner=missing_docker,
    )

    assert result["platform_runtime"]["collection_error_class"] == "docker_not_found"


def test_request_from_config_includes_known_platform_hints() -> None:
    request = docker_request_from_config(
        "postgresql",
        {"host": "localhost", "port": 5432, "container_name": "pg-test"},
    )

    assert request.host == "localhost"
    assert request.port == 5432
    assert request.container_name == "pg-test"
    assert "postgres" in request.service_names
    assert "postgres" in request.image_names


def test_a_container_matched_by_name_but_not_by_port_is_not_marked_as_serving_the_engine_port() -> None:
    run = _runner(
        {
            ("docker", "version", "--format", "{{json .}}"): DockerCommandResult(
                0, stdout=json.dumps({"Server": {"Version": "26.1.0"}})
            ),
            ("docker", "ps", "--format", "{{json .}}"): DockerCommandResult(
                0,
                stdout=json.dumps(
                    {
                        "ID": "abc123",
                        "Image": "clickhouse/clickhouse-server",
                        "Names": "clickhouse",
                        "Ports": "9000/tcp",
                    }
                ),
            ),
            ("docker", "inspect", "abc123"): DockerCommandResult(0, stdout=json.dumps([{"Id": "abc123"}])),
        }
    )

    result = discover_docker_runtime(
        DockerRuntimeDiscoveryRequest(platform_name="clickhouse-server", host="localhost", port=8123),
        runner=run,
    )

    assert result["platform_runtime"]["collection_status"] == "available"
    assert result["container"]["host_port_matched"] is False
