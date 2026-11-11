from __future__ import annotations

import json
from datetime import datetime

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _result_with_sensitive_environment_metadata() -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="snowflake",
        scale_factor=0.01,
        execution_id="public-redaction-test",
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
        system_profile={
            "os_type": "Darwin",
            "os_release": "24.4.0",
            "architecture": "arm64",
            "machine_id": "raw-machine-id",
        },
        execution_environment={
            "client_host": {"hostname": "alice-laptop", "username": "alice"},
            "platform_runtime": {
                "runtime_type": "managed_cloud",
                "engine_host": "acct123.us-east-1.snowflakecomputing.com",
                "collection_status": "partial",
                "source": "requested",
                "collection_error_message": "Docker socket /Users/alice/.docker/run/docker.sock was denied",
            },
            "container": {
                "container_name": "benchbox-clickhouse-dev",
                "bind_mounts": [
                    {
                        "source": "/Users/alice/BenchBox/data",
                        "destination": "/var/lib/clickhouse",
                    }
                ],
            },
        },
        platform_deployment={
            "deployment_type": "managed_cloud",
            "endpoint_class": "cloud_endpoint",
            "api_endpoint": "acct123.us-east-1.snowflakecomputing.com",
            "http_path": "/sql/1.0/warehouses/raw-http-path",
            "collection_status": "partial",
        },
        platform_cloud={
            "provider": "aws",
            "region": "us-east-1",
            "account": "acct123",
            "organization": "acme-org",
            "project": "analytics-project",
            "workspace": "https://workspace.cloud.example.com",
            "collection_status": "partial",
        },
        platform_compute={
            "warehouse": "RAW_WH",
            "cluster_id": "cluster-raw-123",
            "node_type": "c6i.2xlarge",
            "node_count": 2,
            "collection_status": "partial",
        },
        platform_storage={
            "staging_location": "s3://raw-bucket/team/private/",
            "bucket": "raw-bucket",
            "prefix": "team/private/",
            "table_format": "parquet",
            "collection_status": "partial",
        },
        platform_raw_config={
            "host": "warehouse.internal.example.com",
            "database": "BENCH_DB",
            "schema": "PRIVATE_SCHEMA",
            "username": "alice",
            "password": "super-secret-password",
            "access_token": "raw-access-token",
            "credential_file": "/Users/alice/.aws/credentials",
            "connection_string": "postgres://alice:secret@warehouse.internal.example.com/bench",
        },
        platform_raw_metadata={
            "iam_role_arn": "arn:aws:iam::123456789012:role/BenchBoxPrivateRole",
            "machine_id": "raw-worker-machine",
            "query_endpoint": "https://warehouse.internal.example.com/query",
        },
        execution_metadata={
            "mode": "power",
            "run_config": {
                "platform_options": {
                    "warehouse": "RAW_WH",
                    "s3_staging_url": "s3://raw-bucket/team/private/",
                    "client_id": "client-raw-123",
                    "client_secret": "raw-client-secret",
                },
                "platform_option_sources": {
                    "warehouse": "cli_option",
                    "s3_staging_url": "cli_option",
                    "client_id": "saved_config",
                    "client_secret": "saved_config",
                },
            },
        },
    )


def test_anonymized_json_export_hashes_infrastructure_identifiers_and_redacts_secrets(tmp_path) -> None:
    exporter = ResultExporter(output_dir=tmp_path, anonymize=True)

    exported = exporter.export_result(_result_with_sensitive_environment_metadata(), formats=["json"])

    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    serialized = json.dumps(payload, sort_keys=True)
    for leaked in (
        "alice-laptop",
        "raw-machine-id",
        "raw-worker-machine",
        "acct123",
        "acme-org",
        "analytics-project",
        "workspace.cloud.example.com",
        "RAW_WH",
        "cluster-raw-123",
        "raw-bucket",
        "team/private",
        "BENCH_DB",
        "PRIVATE_SCHEMA",
        "super-secret-password",
        "raw-access-token",
        "raw-client-secret",
        "warehouse.internal.example.com",
        "/Users/alice",
        "BenchBoxPrivateRole",
    ):
        assert leaked not in serialized

    assert payload["export"]["anonymized"] is True

    assert "machine_id" not in payload["environment"]
    assert "machine_id" not in payload["environment"]["client_host"]
    assert "engine_host" not in payload["environment"]["platform_runtime"]
    assert "machine_id" not in payload.get("platform", {}).get("raw_metadata", {})
    assert payload["environment"]["client_host"]["hostname"].startswith("host_")
    assert payload["environment"]["client_host"]["username"].startswith("user_")
    assert "path_" in payload["environment"]["platform_runtime"]["collection_error_message"]
    assert payload["environment"]["container"]["container_name"].startswith("container_")
    assert payload["environment"]["container"]["bind_mounts"][0]["source"].startswith("path_")
    assert payload["platform"]["cloud"]["account"].startswith("account_")
    assert payload["platform"]["cloud"]["organization"].startswith("organization_")
    assert payload["platform"]["cloud"]["project"].startswith("project_")
    assert payload["platform"]["cloud"]["workspace"].startswith("workspace_")
    assert payload["platform"]["compute"]["warehouse"].startswith("warehouse_")
    assert payload["platform"]["compute"]["cluster_id"].startswith("cluster_")
    assert payload["platform"]["storage"]["bucket"].startswith("bucket_")
    assert payload["platform"]["storage"]["prefix"].startswith("prefix_")
    assert payload["platform"]["raw_config"]["password"] == "<redacted>"
    assert payload["platform"]["raw_config"]["access_token"] == "<redacted>"

    assert "credential_file" not in payload["platform"]["raw_config"]
    assert payload["platform"]["raw_config"]["connection_string"] == "<redacted>"
    assert payload["config"]["platform_options"]["client_secret"] == "<redacted>"


def test_anonymized_export_preserves_captured_client_host_profile(tmp_path) -> None:
    result = _result_with_sensitive_environment_metadata()
    result.execution_environment["client_host"].update(
        {
            "os": "RemoteOS 1.0",
            "arch": "remote-arch",
            "cpu_count": 64,
            "memory_gb": 512,
            "python": "3.11.9",
        }
    )
    exporter = ResultExporter(output_dir=tmp_path, anonymize=True)

    exported = exporter.export_result(result, formats=["json"])

    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    client_host = payload["environment"]["client_host"]
    assert client_host["os"] == "RemoteOS 1.0"
    assert client_host["arch"] == "remote-arch"
    assert client_host["cpu_count"] == 64
    assert client_host["memory_gb"] == 512
    assert client_host["python"] == "3.11.9"


def test_private_export_preserves_raw_platform_metadata_behind_explicit_option(tmp_path) -> None:
    exporter = ResultExporter(output_dir=tmp_path, anonymize=False)

    exported = exporter.export_result(_result_with_sensitive_environment_metadata(), formats=["json"])

    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    serialized = json.dumps(payload, sort_keys=True)
    assert payload["export"]["anonymized"] is False
    assert "super-secret-password" not in serialized
    assert "raw-access-token" not in serialized
    assert "secret@warehouse.internal.example.com" not in serialized
    assert "warehouse.internal.example.com" in serialized
    assert "raw-bucket" in serialized
    assert payload["platform"]["raw_config"]["username"] == "<redacted>"
    assert payload["platform"]["raw_config"]["password"] == "<redacted>"
    assert payload["platform"]["raw_config"]["access_token"] == "<redacted>"

    assert "super-secret-password" not in json.dumps(payload.get("platform", {}).get("raw_metadata", {}))


