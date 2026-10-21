from __future__ import annotations

import copy

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.query_execution import (
    QueryExecutionContractError,
    query_execution_from_compact_v2,
    query_execution_to_compact_v2,
)
from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator, build_result_payload
from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION, RUNTIME_SCHEMA_POLICY
from benchbox.validation.bundle import (
    ValidationResult,
    _validate_environment_client_link,
    _validate_net_estimate_status,
    _validate_run_section,
    _validate_single_query,
    _validate_timing_field_versions,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PLATFORM_TIMING = {
    "source": "snowflake.query_history",
    "requests": [
        {
            "id": "01b2c3d4-0000-0000-0000-000000000001",
            "attempt": 1,
            "kind": "query",
            "fields": {"TOTAL_ELAPSED_TIME": 41.0, "COMPILATION_TIME": 12.0, "EXECUTION_TIME": 27.0},
        }
    ],
    "platform_ms": 41.0,
    "compile_ms": 12.0,
    "exec_ms": 27.0,
    "cache_hit": False,
    "status": "valid",
}
RUN_TIMING = {
    "timing_method": 2,
    "net_estimate_status": "probe_unverified",
    "platform_timing": {"source": "snowflake.query_history", "definition": "server_complete", "coverage": 0.95},
}
VALID_RUN = {"id": "r", "timestamp": "2026-10-17T00:00:00", "total_duration_ms": 10, "query_time_ms": 10}


def _execution(**fields: object) -> QueryExecution:
    return QueryExecution(query_id="1", execution_time_ms=58.0, rows_returned=4, status="SUCCESS", **fields)


def _bundle_2_4() -> dict:
    payload = copy.deepcopy(
        build_result_payload(make_benchmark_results(platform="snowflake", query_results=[_execution()]))
    )
    payload["result_schema_version"] = payload["version"] = "2.4"
    payload["run"].update(copy.deepcopy(RUN_TIMING))
    payload["queries"][0].update(
        {"request_count": 1, "result_bytes": 96, "platform_timing": copy.deepcopy(PLATFORM_TIMING)}
    )
    payload.setdefault("environment", {})["client_link"] = {
        "collection_status": "available",
        "probed_at": "2026-10-17T00:00:01+00:00",
    }
    return payload


def test_readers_accept_2_4_before_the_producer_writes_it():
    assert RUNTIME_SCHEMA_POLICY.evaluate("2.4").accepted
    assert CURRENT_SCHEMA_VERSION == "2.3"


def test_timing_fields_round_trip_through_the_compact_entry_in_order():
    execution = _execution(request_count=3, result_bytes=512, platform_timing=PLATFORM_TIMING)
    entry = query_execution_to_compact_v2(execution)
    assert list(entry)[-3:] == ["request_count", "result_bytes", "platform_timing"]
    restored = query_execution_from_compact_v2(entry)
    assert restored.request_count == 3
    assert restored.result_bytes == 512
    assert restored.platform_timing == PLATFORM_TIMING


def test_loading_a_2_4_bundle_keeps_query_and_run_timing_fields():
    bundle = _bundle_2_4()
    SchemaV2Validator().validate(bundle)
    loaded = reconstruct_benchmark_results(bundle)
    query = loaded.query_results[0]
    assert (query["request_count"], query["result_bytes"], query["platform_timing"]) == (1, 96, PLATFORM_TIMING)
    assert loaded.timing_method == 2
    assert loaded.net_estimate_status == "probe_unverified"
    assert loaded.run_platform_timing == RUN_TIMING["platform_timing"]


def test_the_2_3_producer_cannot_write_2_4_fields():
    loaded = reconstruct_benchmark_results(_bundle_2_4())
    with pytest.raises(SchemaV2ValidationError, match="requires schema version 2.4 or later"):
        SchemaV2Validator().validate(build_result_payload(loaded))


def test_a_2_3_bundle_carrying_2_4_fields_is_rejected_on_load():
    bundle = _bundle_2_4()
    bundle["result_schema_version"] = bundle["version"] = "2.3"
    with pytest.raises(ValueError, match="requires schema version 2.4 or later"):
        reconstruct_benchmark_results(bundle)


def test_absent_producers_do_not_fill_timing_fields():
    payload = build_result_payload(make_benchmark_results(platform="duckdb", query_results=[_execution()]))
    assert not {"request_count", "result_bytes", "platform_timing"} & set(payload["queries"][0])
    assert not {"timing_method", "net_estimate_status", "platform_timing"} & set(payload["run"])


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"request_count": -1}, "request_count must be non-negative"),
        ({"result_bytes": -5}, "result_bytes must be non-negative"),
        ({"platform_timing": ["valid"]}, "platform_timing must be an object"),
        ({"platform_timing": {"status": "ok"}}, "status must be one of"),
        ({"platform_timing": {"status": "valid"}}, "status 'valid' requires platform_ms"),
        ({"platform_timing": {"status": "suspect", "platform_ms": 1}}, "status 'suspect' requires a reason"),
        ({"platform_timing": {"status": "valid", "platform_ms": float("nan")}}, "platform_ms must be"),
        ({"platform_timing": {"status": "valid", "platform_ms": 1, "platfrom_ms": 2}}, "unknown field"),
        ({"platform_timing": {"status": "valid", "platform_ms": 1, "requests": [{"kind": "query"}]}}, "attempt"),
        ({"platform_timing": {"status": ["valid"]}}, "status must be one of"),
    ],
)
def test_contract_rejects_malformed_timing_fields(fields, message):
    with pytest.raises(QueryExecutionContractError, match=message):
        _execution(**fields)


def _query_errors(query: dict) -> list[str]:
    vr = ValidationResult(path="<test>")
    _validate_single_query(0, {"id": "1", "ms": 58.0, **query}, "2.4", vr)
    return vr.errors


def test_bundle_validator_accepts_well_formed_query_timing():
    assert _query_errors({"request_count": 1, "result_bytes": 0, "platform_timing": PLATFORM_TIMING}) == []
    assert _query_errors({"platform_timing": {"status": "unavailable", "reason": "lookup failed"}}) == []
    assert _query_errors({"platform_timing": None}) == []


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ({"request_count": True}, "request_count must be a non-negative int"),
        ({"result_bytes": 1.5}, "result_bytes must be a non-negative int"),
        ({"platform_timing": "41"}, "platform_timing must be an object"),
        ({"platform_timing": {"status": "valid", "platform_ms": -1}}, "platform_ms must be"),
        ({"platform_timing": {"status": "valid", "platform_ms": 1, "requests": {}}}, "requests must be a list"),
        ({"platform_timing": {}}, "status must be one of"),
        ({"platform_timing": {"status": "valid"}}, "requires platform_ms"),
        ({"platform_timing": {"status": "unavailable"}}, "requires a reason"),
        ({"platform_timing": {"status": ["valid"]}}, "status must be one of"),
        ({"platform_timing": {"status": "valid", "platform_ms": 1, "extra": 1}}, "unknown field 'extra'"),
    ],
)
def test_bundle_validator_rejects_malformed_query_timing(query, fragment):
    assert any(fragment in error for error in _query_errors(query))


def _run_errors(run_fields: dict) -> list[str]:
    vr = ValidationResult(path="<test>")
    _validate_run_section({**VALID_RUN, **run_fields}, vr)
    return vr.errors


def test_bundle_validator_accepts_well_formed_run_timing():
    assert _run_errors(RUN_TIMING) == []


@pytest.mark.parametrize(
    ("run_fields", "fragment"),
    [
        ({"timing_method": 0}, "run.timing_method"),
        ({"timing_method": 3}, "run.timing_method"),
        ({"timing_method": True}, "run.timing_method"),
        ({"net_estimate_status": 1}, "run.net_estimate_status"),
        ({"net_estimate_status": ""}, "run.net_estimate_status"),
        ({"platform_timing": []}, "run.platform_timing must be an object"),
        ({"platform_timing": {}}, "run.platform_timing.source"),
        ({"platform_timing": {**RUN_TIMING["platform_timing"], "coverage": 1.2}}, "run.platform_timing.coverage"),
        ({"timing_method": [2]}, "run.timing_method"),
        ({"net_estimate_status": {"x": 1}}, "run.net_estimate_status"),
        ({"platform_timing": {**RUN_TIMING["platform_timing"], "definition": "other"}}, "platform_timing.definition"),
    ],
)
def test_bundle_validator_rejects_malformed_run_timing(run_fields, fragment):
    assert any(fragment in error for error in _run_errors(run_fields))


def _estimate_bundle(run_fields: dict, endpoint_class: str) -> dict:
    bundle = _bundle_2_4()
    for key in ("timing_method", "net_estimate_status"):
        bundle["run"].pop(key)
    bundle["run"].update(run_fields)
    bundle["platform"].setdefault("deployment", {})["endpoint_class"] = endpoint_class
    return bundle


@pytest.mark.parametrize(
    ("run_fields", "endpoint_class", "fragment"),
    [
        ({"net_estimate_status": "eligible"}, "cloud_endpoint", "requires timing_method 2 or later, got None"),
        ({"net_estimate_status": "eligible", "timing_method": 1}, "cloud_endpoint", "requires timing_method 2"),
        ({"net_estimate_status": "eligible", "timing_method": 2}, "embedded_process", "not allowed for an embedded"),
    ],
)
def test_validators_reject_an_ineligible_eligible_net_estimate(run_fields, endpoint_class, fragment):
    bundle = _estimate_bundle(run_fields, endpoint_class)
    vr = ValidationResult(path="<test>")
    _validate_net_estimate_status(bundle, vr)
    assert any(error.startswith("run.") and fragment in error for error in vr.errors)
    with pytest.raises(SchemaV2ValidationError, match=fragment):
        SchemaV2Validator().validate(bundle)
    with pytest.raises(ValueError, match=fragment):
        reconstruct_benchmark_results(bundle)


@pytest.mark.parametrize(
    ("run_fields", "endpoint_class"),
    [
        ({"net_estimate_status": "eligible", "timing_method": 2}, "cloud_endpoint"),
        ({"net_estimate_status": "embedded", "timing_method": 2}, "embedded_process"),
        ({"net_estimate_status": "timing_method", "timing_method": 1}, "cloud_endpoint"),
    ],
)
def test_validators_accept_consistent_net_estimate_status(run_fields, endpoint_class):
    bundle = _estimate_bundle(run_fields, endpoint_class)
    vr = ValidationResult(path="<test>")
    _validate_net_estimate_status(bundle, vr)
    assert vr.errors == []
    SchemaV2Validator().validate(bundle)
    assert reconstruct_benchmark_results(bundle).net_estimate_status == run_fields["net_estimate_status"]


def test_bundle_validator_checks_probed_at_type():
    vr = ValidationResult(path="<test>")
    _validate_environment_client_link(
        {"environment": {"client_link": {"collection_status": "available", "probed_at": 1}}}, vr
    )
    assert any("client_link.probed_at" in error for error in vr.errors)


@pytest.mark.parametrize("version", ["2.0", "2.3", None])
def test_bundle_validator_rejects_2_4_fields_in_older_bundles(version):
    vr = ValidationResult(path="<test>")
    _validate_timing_field_versions(_bundle_2_4(), version, vr)
    assert "queries[0].request_count requires schema version 2.4 or later" in vr.errors
    assert "run.timing_method requires schema version 2.4 or later" in vr.errors
    assert "environment.client_link.probed_at requires schema version 2.4 or later" in vr.errors


def test_bundle_validator_allows_2_4_fields_in_2_4_bundles():
    vr = ValidationResult(path="<test>")
    _validate_timing_field_versions(_bundle_2_4(), "2.4", vr)
    assert vr.errors == []


def test_schema_validator_rejects_malformed_query_timing():
    bundle = _bundle_2_4()
    bundle["queries"][0]["platform_timing"] = {"status": "valid"}
    with pytest.raises(SchemaV2ValidationError, match="requires platform_ms"):
        SchemaV2Validator().validate(bundle)


def test_schema_validator_accepts_null_query_platform_timing():
    bundle = _bundle_2_4()
    bundle["queries"][0]["platform_timing"] = None
    SchemaV2Validator().validate(bundle)
