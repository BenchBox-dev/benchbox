# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for
# details.

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.results.schema import build_result_payload
from benchbox.mcp.errors import make_execution_error
from benchbox.utils.dependencies import DEPENDENCY_GROUPS, PLATFORM_TO_EXTRA

pytestmark = [pytest.mark.unit, pytest.mark.medium]

EXPECTED_REGISTERED_PLATFORM_COUNT = 49


def _catalog_required_packages(platform_name: str) -> tuple[str, ...] | None:
    extra_name = PLATFORM_TO_EXTRA.get(platform_name, platform_name)
    dep_info = DEPENDENCY_GROUPS.get(extra_name)
    if dep_info is None:
        return None
    return tuple(dep_info.packages)


_CREDENTIAL_SENTINELS = (
    "EGRESS_PASSWORD_SENTINEL",
    "EGRESS_TOKEN_SENTINEL",
    "EGRESS_API_KEY_SENTINEL",
    "EGRESS_ACCESS_KEY_SENTINEL",
    "EGRESS_SECRET_KEY_SENTINEL",
    "EGRESS_ACCESS_TOKEN_SENTINEL",
    "EGRESS_DSN_PASSWORD_SENTINEL",
    "EGRESS_ACCESS_KEY_ID_SENTINEL",
    "EGRESS_SECRET_ACCESS_KEY_SENTINEL",
)

_SWEEP_URI_GATE = "SWEEP_URI_GATE"
_SWEEP_MCP_GATE = "SWEEP_MCP_GATE"
_SWEEP_TABLE_GATE = "SWEEP_TABLE_GATE"
_SWEEP_COLUMN_GATE = "SWEEP_COLUMN_GATE"


def _sentinel_config(tmp_path: Path) -> dict[str, object]:
    return {
        "database_path": ":memory:",
        "database": "benchbox",
        "db_name": "benchbox",
        "db_path": ":memory:",
        "host": "localhost",
        "port": 5432,
        "username": "EGRESS_USERNAME_SENTINEL",
        "user": "EGRESS_USERNAME_SENTINEL",
        "password": _CREDENTIAL_SENTINELS[0],
        "token": _CREDENTIAL_SENTINELS[1],
        "api_key": _CREDENTIAL_SENTINELS[2],
        "access_key": _CREDENTIAL_SENTINELS[3],
        "secret_key": _CREDENTIAL_SENTINELS[4],
        "access_token": _CREDENTIAL_SENTINELS[5],
        "account": "EGRESS_ACCOUNT_SENTINEL",
        "project_id": "EGRESS_PROJECT_SENTINEL",
        "project": "EGRESS_PROJECT_SENTINEL",
        "region": "us-east-1",
        "warehouse": "EGRESS_WAREHOUSE_SENTINEL",
        "schema": "public",
        "catalog": "duckdb",
        "storage_container": "EGRESS_CONTAINER_SENTINEL",
        "container": "EGRESS_CONTAINER_SENTINEL",
        "bucket": "EGRESS_BUCKET_SENTINEL",
        "output_location": "s3://egress-bucket-sentinel/path/",
        "gcs_staging_dir": "gs://egress-bucket-sentinel/path/",
        "s3_staging_dir": "s3://egress-bucket-sentinel/path/",
        "access_key_id": "EGRESS_ACCESS_KEY_ID_SENTINEL",
        "secret_access_key": "EGRESS_SECRET_ACCESS_KEY_SENTINEL",
        "connection_string": "postgresql://user:EGRESS_DSN_PASSWORD_SENTINEL@localhost/benchbox",
        "endpoint": "http://localhost:8080",
        "server": "localhost",
        "driver": "SQLite3",
        "spark_config": {},
        "spark_conf": {},
        "storage_options": {},
        "catalog_name": "benchbox",
        "mode": "core",
        "deployment": "local",
        "deployment_mode": "local",
        "auth_method": "password",
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "output_dir": str(tmp_path),
        "working_dir": str(tmp_path),
        "workgroup": "EGRESS_WORKGROUP_SENTINEL",
        "workspace_id": "EGRESS_WORKSPACE_ID_SENTINEL",
        "workspace_name": "EGRESS_WORKSPACE_SENTINEL",
        "lakehouse_id": "EGRESS_LAKEHOUSE_ID_SENTINEL",
        "execution_role_arn": "arn:aws:iam::123456789012:role/EGRESS_ROLE_SENTINEL",
        "job_role": "arn:aws:iam::123456789012:role/EGRESS_ROLE_SENTINEL",
        "server_hostname": "EGRESS_HOSTNAME_SENTINEL.azuredatabricks.net",
        "http_path": "/sql/EGRESS_HTTP_PATH_SENTINEL",
        "gcs_bucket": "EGRESS_BUCKET_SENTINEL",
        "gcs_path": "EGRESS_PATH_SENTINEL",
        "aws_region": "us-east-1",
        "aws_access_key_id": "EGRESS_AWS_KEY_SENTINEL",
        "aws_secret_access_key": "EGRESS_AWS_SECRET_SENTINEL",
        "storage_account_name": "EGRESS_STORAGE_SENTINEL",
        "storage_account": "EGRESS_STORAGE_SENTINEL",
        "application_id": "EGRESS_APPLICATION_SENTINEL",
        "create_application": True,
        "spark_pool_name": "EGRESS_SPARK_POOL_SENTINEL",
    }


def _registered_platform_names() -> tuple[str, ...]:
    PlatformRegistry.clear_cache()
    return tuple(sorted(PlatformRegistry.get_available_platforms()))


def _prepare_config_for_platform(platform_name: str, tmp_path: Path) -> dict[str, object]:
    config = _sentinel_config(tmp_path)
    if platform_name in {"clickhouse", "clickhouse-local"}:
        config["deployment"] = "local"
        config["deployment_mode"] = "local"
        config["mode"] = "local"
    elif platform_name == "clickhouse-server":
        config["deployment_mode"] = "server"
        config["mode"] = "server"
    elif platform_name == "clickhouse-cloud":
        config["deployment_mode"] = "cloud"
        config["mode"] = "cloud"
    elif platform_name == "ducklake":
        config["catalog"] = "duckdb"
    elif platform_name in {"pg-duckdb", "timescaledb"}:
        config["deployment_mode"] = "self-hosted"
    elif platform_name == "firebolt" or platform_name == "influxdb":
        config["deployment_mode"] = "core"
    elif platform_name == "snowpark-connect":
        config["user"] = "EGRESS_USERNAME_SENTINEL"
    return config


def _construct_adapter_or_skip_reason(platform_name: str, tmp_path: Path) -> Any | str:
    config = _prepare_config_for_platform(platform_name, tmp_path)
    adapter_class = PlatformRegistry.get_adapter_class(platform_name)
    try:
        return adapter_class(**config)
    except (ImportError, ConfigurationError) as exc:
        message = str(exc)
        if "Missing dependencies" not in message:
            raise
        required_packages = _catalog_required_packages(platform_name)
        if required_packages is None:
            raise AssertionError(
                f"unexpected skip for {platform_name!r}: {message}. This platform has no "
                "optional-dependency catalog entry (benchbox.utils.dependencies); install "
                "the missing extra or register the platform's extra in dependencies.yaml "
                "deliberately."
            ) from exc
        message_lower = message.lower()
        if not any(package.lower() in message_lower for package in required_packages):
            raise AssertionError(
                f"{platform_name} optional skip must cite one of its catalog dependencies "
                f"{required_packages}; got: {message}"
            ) from exc
        return f"skip:optional-dependency:{platform_name}:{message}"


def _assert_no_sentinels_in_text(text: str, sentinels: tuple[str, ...] | list[str], *, context: str) -> None:
    for sentinel in sentinels:
        assert sentinel not in text, f"{context} leaked {sentinel}"


def _assert_no_sentinels_in_bytes(data: bytes, sentinels: tuple[str, ...] | list[str], *, context: str) -> None:
    for sentinel in sentinels:
        assert sentinel.encode() not in data, f"{context} leaked {sentinel}"


def _export_chokepoints(result: BenchmarkResults, tmp_path: Path) -> tuple[str, str, bytes]:
    public = json.dumps(build_result_payload(result, sanitize_platform_secrets=False), default=str, sort_keys=True)
    private = (
        ResultExporter(output_dir=tmp_path / "export", anonymize=False)
        .export_result(result, formats=["json"])["json"]
        .read_text(encoding="utf-8")
    )
    db_path = tmp_path / "results.db"
    ResultDatabase(db_path=db_path).store_result(result)
    return public, private, db_path.read_bytes()


def test_registered_platform_count_is_forty_nine() -> None:
    PlatformRegistry.clear_cache()
    platforms = PlatformRegistry.get_available_platforms()
    assert len(platforms) == EXPECTED_REGISTERED_PLATFORM_COUNT, (
        f"expected {EXPECTED_REGISTERED_PLATFORM_COUNT} registered platforms, got {len(platforms)}: {sorted(platforms)}"
    )


def test_adapter_construct_coverage_accounting(tmp_path: Path) -> None:
    names = _registered_platform_names()
    assert len(names) == EXPECTED_REGISTERED_PLATFORM_COUNT

    constructed: list[str] = []
    skipped: dict[str, str] = {}
    for platform_name in names:
        outcome = _construct_adapter_or_skip_reason(platform_name, tmp_path / platform_name)
        if isinstance(outcome, str):
            assert outcome.startswith("skip:optional-dependency:"), outcome
            assert "Missing dependencies" in outcome
            required_packages = _catalog_required_packages(platform_name)
            assert required_packages is not None, f"non-catalog adapter {platform_name!r} must not skip; got {outcome}"
            skipped[platform_name] = outcome
            continue
        constructed.append(platform_name)

    assert len(constructed) + len(skipped) == EXPECTED_REGISTERED_PLATFORM_COUNT
    assert not (set(constructed) & set(skipped))

    for name, reason in skipped.items():
        required_packages = _catalog_required_packages(name)
        assert required_packages is not None
        reason_lower = reason.lower()
        assert any(package.lower() in reason_lower for package in required_packages), (
            f"{name} optional-dependency skip must cite one of {required_packages}: {reason}"
        )


@pytest.mark.parametrize("platform_name", _registered_platform_names())
def test_registered_adapter_result_payload_redacts_credential_sentinels(platform_name: str, tmp_path: Path) -> None:
    outcome = _construct_adapter_or_skip_reason(platform_name, tmp_path)
    if isinstance(outcome, str):
        assert _catalog_required_packages(platform_name) is not None
        pytest.skip(outcome.removeprefix("skip:optional-dependency:").split(":", 1)[-1])

    adapter = outcome
    result = BenchmarkResults(
        benchmark_name="tpch",
        platform=platform_name,
        scale_factor=0.01,
        execution_id=f"sentinel-{platform_name}",
        timestamp=datetime.now(),
        duration_seconds=1.0,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"configuration": dict(adapter.platform_config)},
        platform_raw_config=dict(adapter.platform_config),
    )

    public, private, database_bytes = _export_chokepoints(result, tmp_path)
    _assert_no_sentinels_in_text(public, _CREDENTIAL_SENTINELS, context=f"{platform_name} public payload")
    _assert_no_sentinels_in_text(private, _CREDENTIAL_SENTINELS, context=f"{platform_name} private JSON export")
    _assert_no_sentinels_in_bytes(database_bytes, _CREDENTIAL_SENTINELS, context=f"{platform_name} results.db")


def test_platform_metadata_blocks_never_egress_distinct_sentinels(tmp_path: Path) -> None:
    gates = {
        "raw_config": "RAW_CONFIG_GATE",
        "raw_metadata": "RAW_METADATA_GATE",
        "deployment": "DEPLOYMENT_GATE",
        "cloud": "CLOUD_GATE",
        "compute": "COMPUTE_GATE",
        "storage": "STORAGE_GATE",
    }
    result = BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id="metadata-blocks-gate",
        timestamp=datetime.now(),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"sort_key": "o_orderkey", "threads": 4},
        platform_raw_config={"password": gates["raw_config"], "threads": 4},
        platform_raw_metadata={"password": gates["raw_metadata"], "partition_key": "l_orderkey"},
        platform_deployment={"token": gates["deployment"], "connection_mode": "embedded"},
        platform_cloud={"access_key": gates["cloud"], "region": "us-east-1"},
        platform_compute={"connection_string": gates["compute"], "warehouse": "BENCH_WH"},
        platform_storage={"secret": gates["storage"], "bucket": "bench-bucket"},
    )

    public, private, database_bytes = _export_chokepoints(result, tmp_path)
    public_sanitized = json.dumps(build_result_payload(result), default=str)

    for source, sentinel in gates.items():
        assert sentinel not in public, f"public payload leaked {source}={sentinel}"
        assert sentinel not in public_sanitized, f"sanitized public payload leaked {source}={sentinel}"
        assert sentinel not in private, f"private export leaked {source}={sentinel}"
        assert sentinel.encode() not in database_bytes, f"results.db leaked {source}={sentinel}"

    assert "o_orderkey" in public
    assert "BENCH_WH" in private
    assert b"bench-bucket" in database_bytes


def test_uri_query_credentials_never_egress_through_options_or_result_chokepoints(tmp_path: Path) -> None:
    values = {
        "url": f"https://example.invalid/x?password={_SWEEP_URI_GATE}",
        "endpoint": f"https://example.invalid/export?access_token={_SWEEP_URI_GATE}&x=1",
        "ssl": f"postgresql://host.example/db?sslpassword={_SWEEP_URI_GATE}&application_name=bb",
        "fragment": f"https://example.invalid/export#access_token={_SWEEP_URI_GATE}&section=results",
        "combined": f"postgresql://u:URI_USERINFO_GATE@example.invalid/db?sslpassword={_SWEEP_URI_GATE}",
    }
    uri_sentinels = (_SWEEP_URI_GATE, "URI_USERINFO_GATE")
    sanitized = sanitize_platform_options(values)
    sanitized_blob = json.dumps(sanitized)
    _assert_no_sentinels_in_text(sanitized_blob, uri_sentinels, context="sanitize_platform_options")
    assert sanitized["url"] == "https://example.invalid/x?password=****"
    assert "application_name=bb" in sanitized["ssl"]
    assert "x=1" in sanitized["endpoint"]

    result = BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id="uri-query-gate",
        timestamp=datetime.now(),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"configuration": dict(values)},
        platform_raw_config=dict(values),
        platform_raw_metadata={"connection_uri": values["ssl"]},
        platform_compute={"jdbc_url": values["url"], "warehouse": "BENCH_WH"},
    )
    public, private, database_bytes = _export_chokepoints(result, tmp_path)
    public_sanitized = json.dumps(build_result_payload(result), default=str)
    for blob, label in (
        (public, "public"),
        (public_sanitized, "sanitized public"),
        (private, "private"),
    ):
        _assert_no_sentinels_in_text(blob, uri_sentinels, context=f"{label} export")
    _assert_no_sentinels_in_bytes(database_bytes, uri_sentinels, context="results.db")
    assert "BENCH_WH" in private


def test_mcp_error_scrub_never_egress_credential_sentinels() -> None:
    cases = (
        f"dsn={_SWEEP_MCP_GATE}",
        f"password={_SWEEP_MCP_GATE}",
        f"connection_string={_SWEEP_MCP_GATE}",
        f"private_key={_SWEEP_MCP_GATE}",
        f"motherduck_token={_SWEEP_MCP_GATE}",
        f'dsn="{_SWEEP_MCP_GATE}"',
        f"sas={_SWEEP_MCP_GATE}",
        f"pat={_SWEEP_MCP_GATE}",
    )
    for text in cases:
        result = make_execution_error(text, exception=Exception(text))
        blob = json.dumps(result)
        case_label = text.split("=", 1)[0]
        assert _SWEEP_MCP_GATE not in blob, f"MCP error leaked sentinel for assignment form {case_label!r}"
        assert _SWEEP_MCP_GATE not in result["message"], f"MCP message leaked sentinel for {case_label!r}"
        assert _SWEEP_MCP_GATE not in result["details"]["exception_message"], (
            f"MCP exception_message leaked sentinel for {case_label!r}"
        )
        assert "****" in result["message"]

    benign = "password field is missing; token expired while connecting"
    clean = make_execution_error(benign, exception=Exception(benign))
    assert clean["details"]["exception_message"] == benign


def test_nested_tuning_identifiers_never_egress(tmp_path: Path) -> None:
    foreign_keys_block = {
        "enabled": True,
        "tables": [
            {
                "table": _SWEEP_TABLE_GATE,
                "columns": [_SWEEP_COLUMN_GATE],
                "referenced_table": _SWEEP_TABLE_GATE,
                "referenced_columns": [_SWEEP_COLUMN_GATE],
            }
        ],
    }
    primary_keys_block = {
        "enabled": True,
        "tables": {_SWEEP_TABLE_GATE: [_SWEEP_COLUMN_GATE]},
    }
    requested_payload = {
        "requested": {
            "constraints": {
                "foreign_keys": foreign_keys_block,
                "primary_keys": primary_keys_block,
                "local_table": f"{_SWEEP_TABLE_GATE}/{_SWEEP_COLUMN_GATE}",
                "references_table": _SWEEP_TABLE_GATE,
                "references_column": _SWEEP_COLUMN_GATE,
            }
        }
    }
    anonymized = AnonymizationManager().anonymize_tuning_payload(requested_payload)
    anonymized_blob = json.dumps(anonymized)
    for gate in (_SWEEP_TABLE_GATE, _SWEEP_COLUMN_GATE):
        assert gate not in anonymized_blob, f"anonymize_tuning_payload leaked {gate}"

    constraints = anonymized["requested"]["constraints"]
    entry = constraints["foreign_keys"]["tables"][0]
    assert entry["table"].startswith("table_")
    assert entry["columns"][0].startswith("column_")
    assert constraints["foreign_keys"]["enabled"] is True
    assert constraints["local_table"].startswith("table_")
    assert constraints["references_table"].startswith("table_")
    assert constraints["references_column"].startswith("column_")
    pk_tables = constraints["primary_keys"]["tables"]
    pk_key = next(iter(pk_tables))
    assert pk_key.startswith("table_")
    assert pk_tables[pk_key][0].startswith("column_")

    tunings_applied = {
        "foreign_keys": foreign_keys_block,
        "primary_keys": primary_keys_block,
    }
    result = BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id="tuning-companion-gate",
        timestamp=datetime.now(),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        tunings_applied=tunings_applied,
        tuning_source="wizard",
        tuning_source_file="examples/tunings/custom.yaml:0123456789abcdef",
    )

    private_export = ResultExporter(output_dir=tmp_path / "private", anonymize=False).export_result(
        result, formats=["json"]
    )
    private_raw = private_export["json"].read_text(encoding="utf-8")
    private_requested = json.loads(private_raw)["platform"]["tuning"]["requested"]
    assert "constraints" in private_requested
    assert "foreign_keys" in private_requested["constraints"]
    assert _SWEEP_TABLE_GATE in private_raw
    assert _SWEEP_COLUMN_GATE in private_raw

    exported = ResultExporter(output_dir=tmp_path / "export", anonymize=True).export_result(result, formats=["json"])
    primary_raw = exported["json"].read_text(encoding="utf-8")
    for gate in (_SWEEP_TABLE_GATE, _SWEEP_COLUMN_GATE):
        assert gate not in primary_raw, f"anonymized primary JSON leaked {gate}"
    for retired in (".tuning.json", ".applied.json"):
        assert not (tmp_path / "export" / f"{exported['json'].stem}{retired}").exists()

    public_constraints = json.loads(primary_raw)["platform"]["tuning"]["requested"]["constraints"]
    public_fk = public_constraints["foreign_keys"]
    assert public_fk["enabled"] is True
    assert public_fk["tables"][0]["table"].startswith("table_")
    assert public_fk["tables"][0]["columns"][0].startswith("column_")
    public_pk_tables = public_constraints["primary_keys"]["tables"]
    public_pk_key = next(iter(public_pk_tables))
    assert public_pk_key.startswith("table_")
    assert public_pk_tables[public_pk_key][0].startswith("column_")


def test_cross_layer_sentinel_gate_is_green() -> None:
    uri = json.dumps(sanitize_platform_options({"url": f"https://example.invalid/x?password={_SWEEP_URI_GATE}"}))
    mcp = json.dumps(make_execution_error(f"dsn={_SWEEP_MCP_GATE}", exception=Exception(f"dsn={_SWEEP_MCP_GATE}")))
    payload = {
        "requested": {
            "constraints": {
                "foreign_keys": {
                    "tables": [{"table": _SWEEP_TABLE_GATE, "columns": [_SWEEP_COLUMN_GATE]}],
                }
            }
        }
    }
    tuning = json.dumps(AnonymizationManager().anonymize_tuning_payload(payload))
    combined = uri + mcp + tuning
    for gate in (_SWEEP_URI_GATE, _SWEEP_MCP_GATE, _SWEEP_TABLE_GATE, _SWEEP_COLUMN_GATE):
        assert gate not in combined, f"cross-layer gate leaked {gate}"
