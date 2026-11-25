from __future__ import annotations

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.bundle_json_schema import DEFAULT_OUTPUT
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_execution import QueryExecutionContractError, normalize_row_count_validation
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator, build_result_payload
from benchbox.core.validation import stream_checks as checks_module
from benchbox.validation.bundle import ValidationResult, _validate_row_count_validation, _validate_stream_checks_section

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _evidence(check: str, status: str = "PASSED", **extra) -> dict:
    return {
        "status": status,
        "expected": 5 if check in ("exact", "invariant") else None,
        "actual": 5,
        "check": check,
        "bound": "exactly 5" if check in ("exact", "invariant") else "at least 1 row",
        "seed": 1001,
        "streams_index": None,
        **extra,
    }


def _row(query_id: str, evidence: dict) -> dict:
    return {
        "query_id": query_id,
        "status": "SUCCESS",
        "run_type": "measurement",
        "iteration": 1,
        "execution_time_seconds": 0.25,
        "rows_returned": 5,
        "row_count_validation": evidence,
    }


def _payload() -> dict:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-DS", scale_factor=1.0),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results(
        [
            normalize_query_result(_row("2", _evidence("exact"))),
            normalize_query_result(_row("93", _evidence("never_empty"))),
            normalize_query_result(
                _row(
                    "1",
                    _evidence(
                        "structural", "SKIPPED", warning="parameter sensitive: x", expected=None, bound="at most 100"
                    ),
                )
            ),
        ]
    )
    return build_result_payload(builder.build())


@pytest.fixture(autouse=True)
def _clear_references():
    checks_module.clear_reference_cache()
    yield
    checks_module.clear_reference_cache()


def test_the_writer_records_the_validation_object_and_the_row_evidence_fields() -> None:
    payload = _payload()

    assert payload["version"] == payload["result_schema_version"] == "2.7"
    stream_checks = payload["validation"]["stream_checks"]
    assert stream_checks["checks"] == {"exact": 1, "never_empty": 1, "structural": 1}
    reference = stream_checks["references"][0]
    assert reference["file"] == "tpcds_row_counts_sf1.json" and reference["sample_draws"] >= 512
    evidence = payload["queries"][0]["row_count_validation"]
    assert evidence["check"] == "exact" and evidence["bound"] == "exactly 5" and evidence["seed"] == 1001
    SchemaV2Validator().validate(payload)
    Draft202012Validator(json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))).validate(payload)


def test_a_pass_by_must_return_rows_stays_passed_in_the_written_row() -> None:
    evidence = _payload()["queries"][1]["row_count_validation"]

    assert evidence["status"] == "PASSED" and evidence["check"] == "never_empty" and evidence["expected"] is None


def test_the_stream_check_fields_are_refused_below_schema_2_7() -> None:
    payload = copy.deepcopy(_payload())
    payload["version"] = payload["result_schema_version"] = "2.6"

    with pytest.raises(SchemaV2ValidationError, match="2.7"):
        SchemaV2Validator().validate(payload)
    without_object = {key: value for key, value in payload.items() if key != "validation"}
    with pytest.raises(SchemaV2ValidationError, match="2.7"):
        SchemaV2Validator().validate(without_object)


def test_the_json_schema_refuses_the_fields_below_2_7() -> None:
    payload = copy.deepcopy(_payload())
    payload["version"] = payload["result_schema_version"] = "2.6"
    validator = Draft202012Validator(json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8")))

    assert list(validator.iter_errors(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(check="unknown"),
        lambda e: e.pop("bound"),
        lambda e: e.update(bound=""),
        lambda e: e.update(seed=-1),
        lambda e: e.update(streams_index=True),
    ],
)
def test_malformed_stream_check_evidence_is_refused_by_the_writer_and_the_validator(mutate) -> None:
    evidence = _evidence("exact")
    mutate(evidence)

    with pytest.raises(QueryExecutionContractError):
        normalize_row_count_validation(evidence, rows_returned=5)
    result = ValidationResult("test")
    _validate_row_count_validation(0, {"rows": 5, "row_count_validation": evidence}, "2.7", result)
    assert result.errors


def test_a_structural_check_alone_cannot_pass_a_row() -> None:
    evidence = _evidence("structural", "PASSED", expected=None, bound="at most 100")

    with pytest.raises(QueryExecutionContractError):
        normalize_row_count_validation(evidence, rows_returned=5)


def test_the_bundle_validator_accepts_the_object_and_rejects_a_malformed_one() -> None:
    payload = _payload()
    accepted = ValidationResult("test")
    _validate_stream_checks_section(payload, "2.7", accepted)
    assert not accepted.errors

    broken = copy.deepcopy(payload)
    broken["validation"]["stream_checks"]["references"][0]["sha256"] = "nope"
    rejected = ValidationResult("test")
    _validate_stream_checks_section(broken, "2.7", rejected)
    assert rejected.errors
    early = ValidationResult("test")
    _validate_stream_checks_section(payload, "2.6", early)
    assert early.errors
