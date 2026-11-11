from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

FIXTURE_PATH = Path(__file__).parents[3] / "fixtures/golden/results/environment_capture_schema_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    with open(FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)["cases"]


def _case_ids() -> list[str]:
    return [case["id"] for case in _load_cases()]


def _result_from_case(case: dict[str, Any]) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name=case.get("benchmark_name", "TPC-H"),
        platform=case["platform"],
        scale_factor=case.get("scale_factor", 0.01),
        execution_id=f"environment-fixture-{case['id']}",
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
        system_profile=case.get("system_profile"),
        execution_environment=case.get("execution_environment"),
        platform_deployment=case.get("platform_deployment"),
        platform_cloud=case.get("platform_cloud"),
        platform_compute=case.get("platform_compute"),
        platform_storage=case.get("platform_storage"),
        platform_raw_config=case.get("platform_raw_config"),
        platform_raw_metadata=case.get("platform_raw_metadata"),
        execution_metadata=case.get("execution_metadata"),
    )


def _get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in dotted_path.split("."):
        if isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
    return value


@pytest.mark.parametrize("case", _load_cases(), ids=_case_ids())
def test_environment_capture_golden_cases_emit_normalized_and_legacy_blocks(case: dict[str, Any]) -> None:
    payload = build_result_payload(_result_from_case(case))

    for dotted_path, expected in case["assertions"].items():
        assert _get_path(payload, dotted_path) == expected

    assert "environment" in payload
    assert "platform_runtime" in payload["environment"]
    assert "deployment" in payload["platform"]
    assert "cloud" in payload["platform"]
    assert "compute" in payload["platform"]
    assert "storage" in payload["platform"]


@pytest.mark.parametrize("case", _load_cases(), ids=_case_ids())
def test_environment_capture_golden_cases_round_trip_through_loader(case: dict[str, Any]) -> None:
    payload = build_result_payload(_result_from_case(case))

    round_trip = build_result_payload(reconstruct_benchmark_results(payload))

    for dotted_path, expected in case["assertions"].items():
        assert _get_path(round_trip, dotted_path) == expected


@pytest.mark.parametrize(
    "case",
    [case for case in _load_cases() if case.get("public_export")],
    ids=[case["id"] for case in _load_cases() if case.get("public_export")],
)
def test_environment_capture_golden_cases_anonymized_public_export(case: dict[str, Any], tmp_path) -> None:
    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(
        _result_from_case(case),
        formats=["json"],
    )
    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    serialized = json.dumps(payload, sort_keys=True)
    for leaked in case["public_export"]["absent"]:
        assert leaked not in serialized
    for key in case["public_export"].get("absent_keys", []):
        assert f'"{key}"' not in serialized, f"dropped key still present: {key}"
    for dotted_path, prefix in case["public_export"]["prefixes"].items():
        value = _get_path(payload, dotted_path)
        if prefix == "<redacted>":
            assert value == prefix
        else:
            assert value.startswith(prefix)


def test_old_minimal_v2_result_without_environment_fields_still_serializes(tmp_path) -> None:
    legacy_payload = make_v2_result_dict(
        platform="duckdb",
        platform_version="1.0.0",
        total_queries=1,
        passed_queries=1,
        failed_queries=0,
        queries=[
            {
                "id": "Q1",
                "ms": 100.0,
                "rows": 1,
                "status": "SUCCESS",
            }
        ],
    )
    legacy_payload.pop("environment", None)

    reconstructed = reconstruct_benchmark_results(legacy_payload)
    payload = build_result_payload(reconstructed)
    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(reconstructed, formats=["json"])

    assert exported["json"].exists()
    assert payload["environment"]["platform_runtime"]["runtime_type"] == "unknown"
    assert payload["environment"]["platform_runtime"]["collection_status"] == "unavailable"
    assert payload["platform"]["deployment"]["endpoint_class"] == "unknown"
    assert payload["platform"]["deployment"]["collection_status"] == "unavailable"


