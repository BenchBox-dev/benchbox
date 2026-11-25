from __future__ import annotations

import random

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.query_status import row_counts_checked, row_evidence_roll_up
from benchbox.core.results.schema import build_result_payload
from benchbox.validation.bundle import ValidationResult, _validate_execution_consistency

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROW_KINDS = ("PASSED", "SKIPPED_DOCUMENTED", "SKIPPED_UNDOCUMENTED", "UNVALIDATED", "FAILED")
RUN_TYPES = ("measurement", "warmup")


def _query(index: int, kind: str, run_type: str) -> dict:
    evidence: dict = {"expected": 4 if kind == "PASSED" else None, "actual": 4}
    if kind == "PASSED":
        evidence["status"] = "PASSED"
    elif kind == "SKIPPED_DOCUMENTED":
        evidence.update(status="SKIPPED", warning="data refreshed: base-data answers do not apply")
    elif kind == "SKIPPED_UNDOCUMENTED":
        evidence["status"] = "SKIPPED"
    elif kind == "UNVALIDATED":
        evidence["status"] = "UNVALIDATED"
    else:
        evidence.update(status="FAILED", expected=7)
    return {
        "query_id": str(index),
        "status": "SUCCESS",
        "run_type": run_type,
        "iteration": 0 if run_type == "warmup" else 1,
        "execution_time_seconds": 0.5,
        "rows_returned": 4,
        "row_count_validation": evidence,
    }


def _mixes(count: int) -> list[list[tuple[str, str]]]:
    rng = random.Random(20261125)
    return [[(rng.choice(ROW_KINDS), rng.choice(RUN_TYPES)) for _ in range(rng.randint(1, 6))] for _ in range(count)]


def _write(mix: list[tuple[str, str]], scale_factor: float):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=scale_factor),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results(
        [normalize_query_result(_query(index, kind, run_type)) for index, (kind, run_type) in enumerate(mix, 1)]
    )
    return builder.build()


def _accepts(payload: dict, claim: str) -> bool:
    claimed = {**payload, "summary": {**payload["summary"], "validation": claim}}
    validation = ValidationResult("test")
    _validate_execution_consistency(claimed, validation)
    return not validation.errors


@pytest.mark.parametrize("scale_factor", [1.0, 10.0])
def test_validator_accepts_exactly_what_the_writer_records_as_passed_or_no_reference(scale_factor):
    outcomes = set()
    for mix in _mixes(400):
        result = _write(mix, scale_factor)
        payload = build_result_payload(result)
        recorded = payload["summary"]["validation"]
        outcomes.add(recorded)
        for claim in ("passed", "no_reference"):
            assert _accepts(payload, claim) is (recorded == claim), (mix, recorded, claim)
    assert {"passed", "failed"} <= outcomes or {"no_reference", "failed"} <= outcomes


@pytest.mark.parametrize("scale_factor", [1.0, 10.0])
def test_roll_up_judges_warmup_and_measurement_rows_alike_for_failures(scale_factor):
    rows = [_query(1, "PASSED", "measurement"), _query(2, "FAILED", "warmup")]

    status, _ = row_evidence_roll_up(rows, reference_exists=scale_factor == 1.0)

    assert status == "FAILED"


def test_passed_needs_a_measurement_row_and_a_warmup_pass_does_not_count():
    rows = [_query(1, "PASSED", "warmup"), _query(2, "SKIPPED_DOCUMENTED", "measurement")]

    assert row_evidence_roll_up(rows, reference_exists=True)[0] == "NOT_VALIDATED"
    assert row_evidence_roll_up(rows, reference_exists=False)[0] == "NO_REFERENCE"
    assert not row_counts_checked(rows)


def test_undocumented_skip_blocks_passed_on_warmup_rows_too():
    rows = [_query(1, "PASSED", "measurement"), _query(2, "SKIPPED_UNDOCUMENTED", "warmup")]

    assert row_evidence_roll_up(rows, reference_exists=True)[0] == "NOT_VALIDATED"


def test_failed_queries_without_failed_evidence_keep_partial():
    rows = [_query(1, "PASSED", "measurement")]

    assert row_evidence_roll_up(rows, reference_exists=True, failed_queries=1)[0] == "PARTIAL"
