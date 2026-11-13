from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.core.results.environment import build_host_load_block, host_load_sample
from benchbox.core.results.integrity_validator import ResultIntegrityValidator
from benchbox.core.results.models import BenchmarkResults, QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LOADED = {
    "available": True,
    "timestamp": "2026-11-13T10:00:00",
    "cpu": {"load_average": (7.5, 6.25, 4.0), "frequency_mhz": 3200.0},
    "memory": {"available_mb": 2048.0},
    "swap": {"used_mb": 512.0, "percent": 12.5},
}
QUIET = {**LOADED, "cpu": {"load_average": (0.1, 0.2, 0.3), "frequency_mhz": 3200.0}}
NO_LOAD_AVERAGE = {**LOADED, "cpu": {"load_average": None, "frequency_mhz": None}}


def _result(metadata: dict | None) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="DuckDB",
        scale_factor=0.01,
        execution_id="host-load",
        timestamp=datetime.now(timezone.utc),
        duration_seconds=1.0,
        total_queries=2,
        successful_queries=2,
        failed_queries=0,
        execution_metadata=metadata or {},
        query_results=[
            QueryExecution(query_id="Q1", status="SUCCESS", execution_time_ms=10.0),
            QueryExecution(query_id="Q2", status="SUCCESS", execution_time_ms=20.0),
        ],
    )


def test_sample_reads_the_existing_snapshot_fields():
    assert host_load_sample(LOADED) == {
        "load_average": [7.5, 6.25, 4.0],
        "cpu_frequency_mhz": 3200.0,
        "memory_available_mb": 2048.0,
        "swap_used_mb": 512.0,
        "swap_percent": 12.5,
        "sampled_at": "2026-11-13T10:00:00",
    }


@pytest.mark.parametrize("snapshot", [None, {}, {"available": False, "reason": "psutil not installed"}])
def test_unavailable_snapshot_gives_explicit_nulls(snapshot):
    sample = host_load_sample(snapshot)

    assert set(sample.values()) == {None}
    assert "load_average" in sample


def test_unavailable_load_average_is_null_not_absent():
    assert host_load_sample(NO_LOAD_AVERAGE)["load_average"] is None


def test_bundle_carries_start_and_end_samples():
    payload = build_result_payload(
        _result({"resource_utilization_start": QUIET, "resource_utilization": LOADED}),
    )
    SchemaV2Validator().validate(payload)

    host_load = payload["environment"]["host_load"]
    assert host_load["start"]["load_average"] == [0.1, 0.2, 0.3]
    assert host_load["end"]["load_average"] == [7.5, 6.25, 4.0]


def test_bundle_without_any_snapshot_has_null_samples():
    payload = build_result_payload(_result(None))

    assert payload["environment"]["host_load"] == build_host_load_block(None, None)
    assert payload["environment"]["host_load"]["start"]["load_average"] is None


def test_json_round_trip_keeps_the_nulls():
    payload = json.loads(json.dumps(build_result_payload(_result(None)), default=str))

    assert "load_average" in payload["environment"]["host_load"]["end"]
    assert payload["environment"]["host_load"]["end"]["load_average"] is None


def test_integrity_validator_reports_host_load_as_info():
    payload = build_result_payload(_result({"resource_utilization_start": QUIET, "resource_utilization": LOADED}))

    report = ResultIntegrityValidator().validate(payload)
    check = next(c for c in report.checks if c.name == "host_load")

    assert check.status.value == "INFO"
    assert "start load average 0.1, 0.2, 0.3" in check.message
    assert "end load average 7.5, 6.25, 4" in check.message


def test_integrity_validator_accepts_bundles_without_the_fields():
    payload = build_result_payload(_result(None))
    with_fields = ResultIntegrityValidator().validate(payload)
    payload["environment"].pop("host_load")

    report = ResultIntegrityValidator().validate(payload)
    check = next(c for c in report.checks if c.name == "host_load")

    assert check.status.value == "INFO"
    assert check.message == "Host load not recorded"
    assert report.overall_status == with_fields.overall_status


@pytest.mark.parametrize("with_fields", [True, False])
def test_compare_and_validate_accept_bundles_with_and_without_the_fields(tmp_path: Path, with_fields: bool):
    payload = build_result_payload(_result({"resource_utilization_start": QUIET, "resource_utilization": LOADED}))
    if not with_fields:
        payload["environment"].pop("host_load")
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")

    assert CliRunner().invoke(cli, ["compare", str(path), str(path)]).exit_code == 0
    assert CliRunner().invoke(cli, ["results", "validate", str(path)]).exit_code in (0, 5)
