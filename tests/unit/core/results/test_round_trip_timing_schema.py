from __future__ import annotations

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.query_execution import (
    QueryExecutionContractError,
    query_execution_from_compact_v2,
    query_execution_to_compact_v2,
)
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION, RUNTIME_SCHEMA_POLICY
from benchbox.validation.bundle import (
    ValidationResult,
    _validate_environment_client_link,
    _validate_run_section,
    _validate_single_query,
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

VALID_RUN = {"id": "r", "timestamp": "2026-10-17T00:00:00", "total_duration_ms": 10, "query_time_ms": 10}


def _execution(**fields: object) -> QueryExecution:
    return QueryExecution(query_id="1", execution_time_ms=58.0, rows_returned=4, status="SUCCESS", **fields)


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


def test_timing_fields_survive_build_load_build():
    result = make_benchmark_results(
        platform="snowflake",
        query_results=[_execution(request_count=1, result_bytes=96, platform_timing=PLATFORM_TIMING)],
    )
    payload = build_result_payload(result)
    SchemaV2Validator().validate(payload)
    query = payload["queries"][0]
    assert query["request_count"] == 1
    assert query["platform_timing"] == PLATFORM_TIMING
    rebuilt = build_result_payload(reconstruct_benchmark_results(payload))
    assert rebuilt["queries"][0]["result_bytes"] == 96
    assert rebuilt["queries"][0]["platform_timing"] == PLATFORM_TIMING


def test_absent_producers_do_not_fill_timing_fields():
    entry = query_execution_to_compact_v2(_execution())
    assert not {"request_count", "result_bytes", "platform_timing"} & set(entry)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"request_count": -1}, "request_count must be non-negative"),
        ({"result_bytes": -5}, "result_bytes must be non-negative"),
        ({"platform_timing": ["valid"]}, "platform_timing must be an object"),
        ({"platform_timing": {"status": "ok"}}, "platform_timing.status must be one of"),
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


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ({"request_count": True}, "request_count must be a non-negative int"),
        ({"result_bytes": 1.5}, "result_bytes must be a non-negative int"),
        ({"platform_timing": "41"}, "platform_timing must be a dict"),
        ({"platform_timing": {"status": "valid", "platform_ms": -1}}, "platform_timing.platform_ms"),
        ({"platform_timing": {"status": "valid", "requests": {}}}, "platform_timing.requests must be a list"),
        ({"platform_timing": {}}, "platform_timing.status must be one of"),
    ],
)
def test_bundle_validator_rejects_malformed_query_timing(query, fragment):
    assert any(fragment in error for error in _query_errors(query))


def _run_errors(run_fields: dict) -> list[str]:
    vr = ValidationResult(path="<test>")
    _validate_run_section({**VALID_RUN, **run_fields}, vr)
    return vr.errors


def test_bundle_validator_accepts_well_formed_run_timing():
    run_fields = {
        "timing_method": 2,
        "net_estimate_status": "eligible",
        "platform_timing": {"source": "snowflake.query_history", "definition": "server_complete", "coverage": 0.95},
    }
    assert _run_errors(run_fields) == []


@pytest.mark.parametrize(
    ("run_fields", "fragment"),
    [
        ({"timing_method": 0}, "run.timing_method"),
        ({"timing_method": True}, "run.timing_method"),
        ({"net_estimate_status": 1}, "run.net_estimate_status"),
        ({"platform_timing": []}, "run.platform_timing' must be a dict"),
        ({"platform_timing": {"coverage": 1.2}}, "run.platform_timing.coverage"),
        ({"platform_timing": {"definition": 3}}, "run.platform_timing.definition"),
    ],
)
def test_bundle_validator_rejects_malformed_run_timing(run_fields, fragment):
    assert any(fragment in error for error in _run_errors(run_fields))


def test_bundle_validator_checks_probed_at_type():
    vr = ValidationResult(path="<test>")
    _validate_environment_client_link(
        {"environment": {"client_link": {"collection_status": "available", "probed_at": 1}}}, vr
    )
    assert any("client_link.probed_at" in error for error in vr.errors)
