from __future__ import annotations

import json

import pytest

from benchbox.core.expected_results import row_count_references as references
from benchbox.core.expected_results.models import row_count_validation_record
from benchbox.core.results.query_execution import normalize_row_count_validation
from benchbox.core.results.query_status import (
    NO_REFERENCE_VALIDATION_STATUS,
    PASSED_VALIDATION_STATUS,
    row_count_skip_is_documented,
    row_evidence_roll_up,
)
from benchbox.core.validation import stream_checks
from benchbox.core.validation.stream_checks import QueryGeneration, decide_row_count
from benchbox.utils.datagen_version import compute_base_constants_hash, current_datagen_stamp
from benchbox.utils.query_generation import current_query_generation_stamp

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TPCDS_COUNTS = {"1": 100, "2": 2500, "3": 7, "93": 100}
THROUGHPUT_STREAM_COUNT = 9


def _row_counts(benchmark: str, overrides: dict[str, int]) -> dict[str, int]:
    return {query_id: overrides.get(query_id, 5) for query_id in references.QUERY_IDS[benchmark]}


def _groups(benchmark: str) -> list[dict]:
    if benchmark == "tpcds":
        return [
            {"group": "reference", "path": "per_template", "seeds": [1001, 1002, 1003, 1004]},
            {"group": "seedless", "path": "streams", "seeds": [42], "streams": 9},
            {"group": "spread", "path": "per_template", "seeds": list(range(2000, 2188))},
            {"group": "spread", "path": "streams", "seeds": list(range(3000, 3064)), "streams": 4},
            {"group": "load_end_time", "path": "streams", "seeds": list(range(4000, 4008)), "streams": 8},
        ]
    return [
        {"group": "seedless", "path": "qgen", "seeds": [None]},
        {"group": "reference", "path": "qgen", "seeds": list(range(1, 11))},
        {"group": "spread", "path": "qgen", "seeds": list(range(100, 600))},
        {"group": "load_end_time", "path": "qgen", "seeds": list(range(7000, 7010))},
    ]


def _payload(benchmark: str, scale: float, *, invariants: dict[str, int], never_empty: list[str]) -> dict:
    stamp = current_datagen_stamp(benchmark)
    generator = {
        "data_generator": "test",
        "query_generator": "test",
        "data_stamp": stamp,
        "query_generation": current_query_generation_stamp(benchmark),
    }
    checksum = {"algorithm": "test", "sha256": "ab" * 32, "tables": {}}
    streams = {
        "0": {
            "query_parameters": "reference stream",
            "seed": 1001 if benchmark == "tpcds" else None,
            "streams_index": None,
            "row_counts": _row_counts(benchmark, TPCDS_COUNTS if benchmark == "tpcds" else {}),
        }
    }
    if benchmark == "tpcds":
        streams["1"] = {
            "query_parameters": "power stream 1",
            "seed": 1002,
            "streams_index": None,
            "row_counts": _row_counts(benchmark, {"3": 7}),
        }
        streams["5"] = {
            "query_parameters": "throughput stream 1",
            "seed": 42,
            "streams_index": 1,
            "row_counts": _row_counts(benchmark, {"3": THROUGHPUT_STREAM_COUNT}),
        }
    draws = sum(len(g["seeds"]) * g.get("streams", 1) for g in _groups(benchmark))
    engines = {"duckdb": "2.0.0", "datafusion": "55.0.0"}
    return {
        "schema_version": references.SCHEMA_VERSION,
        "benchmark": benchmark,
        "scale_factor": scale,
        "reference_kind": references.KIND_ENGINE_AGREEMENT,
        "streams": streams,
        "provenance": {
            "generator": generator,
            "data_checksum": checksum,
            "engines": engines,
            "generated_on": "2026-11-25",
            "agreement": {"result": "identical", "engines": list(engines), "streams": sorted(int(k) for k in streams)},
            "regenerate": "test",
        },
        "integrity": {
            "sample": {
                "groups": _groups(benchmark),
                "draws": draws,
                "engines": engines,
                "agreement": {"result": "identical", "engines": list(engines), "draws": 64, "statements": 1},
                "data_stamp": stamp,
                "query_generation": generator["query_generation"],
                "data_checksum": checksum["sha256"],
                "benchbox_version": "test",
                "generated_on": "2026-11-25",
                "regenerate": "test",
            },
            "invariant_counts": invariants,
            "never_empty": never_empty,
        },
    }


@pytest.fixture
def reference_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(references, "REFERENCE_DIR", tmp_path)
    for benchmark, scale in (("tpcds", 1.0), ("tpch", 1.0), ("tpch", 0.01)):
        invariants = {"1": 100, "93": 100, "3": 7} if benchmark == "tpcds" else {"1": 4, "5": 5}
        never_empty = ["1", "2", "3", "93"] if benchmark == "tpcds" else ["1", "5", "11", "13", "16", "18"]
        payload = _payload(benchmark, scale, invariants=invariants, never_empty=never_empty)
        (tmp_path / f"{benchmark}_row_counts_sf{scale:g}.json").write_text(json.dumps(payload), encoding="utf-8")
    stream_checks.clear_reference_cache()
    yield tmp_path
    stream_checks.clear_reference_cache()


def _decide(benchmark: str, query: str, rows: int, scale: float = 1.0, **kwargs):
    return decide_row_count(benchmark, query, rows, scale, **kwargs)


def test_exact_reference_is_keyed_by_seed_and_streams_index(reference_dir) -> None:
    ok = _decide("tpcds", "2", 2500, generation=QueryGeneration(seed=1001))
    bad = _decide("tpcds", "2", 2499, generation=QueryGeneration(seed=1001))

    assert ok.is_valid and ok.check == "exact" and ok.bound == "exactly 2500" and ok.seed == 1001
    assert not bad.is_valid and bad.check == "exact"
    assert "tpcds query 2" in bad.error_message and "exactly 2500" in bad.error_message


def test_a_throughput_stream_number_never_matches_a_power_reference_stream(reference_dir) -> None:
    collided = _decide("tpcds", "3", 7, generation=QueryGeneration(seed=77, streams_index=1))
    throughput = _decide("tpcds", "3", THROUGHPUT_STREAM_COUNT, generation=QueryGeneration(seed=42, streams_index=1))
    wrong = _decide("tpcds", "3", 7, generation=QueryGeneration(seed=42, streams_index=1))

    assert collided.check == "invariant" and collided.bound == "exactly 7"
    assert throughput.is_valid and throughput.check == "exact" and throughput.streams_index == 1
    assert not wrong.is_valid and wrong.check == "exact"


def test_a_wrong_invariant_count_fails_the_row_and_the_run(reference_dir) -> None:
    result = _decide("tpcds", "93", 99, generation=QueryGeneration(seed=12345, streams_index=2))
    record = row_count_validation_record(result, 99)
    rolled, reason = row_evidence_roll_up(
        [{"run_type": "measurement", "row_count_validation": record}], reference_exists=True
    )

    assert not result.is_valid and result.check == "invariant"
    assert record["status"] == "FAILED" and record["check"] == "invariant"
    assert rolled == "FAILED" and "failed" in reason
    assert "sample of 521 draws" in result.error_message
    assert current_datagen_stamp("tpcds")["base_constants_hash"][:12] in result.error_message
    assert "docs/agent/maintainer-bundles.md" in result.error_message and len(result.error_message) < 500


def test_an_empty_must_return_rows_answer_fails_the_row(reference_dir) -> None:
    result = _decide("tpcds", "2", 0, generation=QueryGeneration(seed=999))

    assert not result.is_valid and result.check == "never_empty" and result.bound == "at least 1 row"


def test_an_injected_dropped_limit_fails_the_row_before_any_other_check(reference_dir) -> None:
    result = _decide("tpcds", "1", 250, generation=QueryGeneration(seed=1001))

    assert not result.is_valid and result.check == "structural" and result.bound == "at most 100"
    one_row = _decide("tpcds", "13", 4, generation=QueryGeneration(seed=1))
    assert not one_row.is_valid and one_row.bound == "exactly 1"


def test_a_stamp_mismatch_is_a_documented_skip_and_structural_checks_still_run(reference_dir) -> None:
    other = "f" * 64
    skipped = _decide("tpcds", "2", 2500, generation=QueryGeneration(seed=1001), run_data_stamp=other)
    failed = _decide("tpcds", "1", 250, generation=QueryGeneration(seed=1001), run_data_stamp=other)
    record = row_count_validation_record(skipped, 2500)

    assert skipped.is_valid and record["status"] == "SKIPPED"
    assert row_count_skip_is_documented(record) and record["warning"].startswith("reference data differs:")
    assert not failed.is_valid and failed.check == "structural"
    rolled, reason = row_evidence_roll_up(
        [{"run_type": "measurement", "row_count_validation": record}], reference_exists=True
    )
    assert rolled == NO_REFERENCE_VALIDATION_STATUS and "reference data differs" in reason


def test_a_changed_query_generation_is_a_documented_skip(reference_dir) -> None:
    skipped = _decide("tpcds", "2", 2500, generation=QueryGeneration(seed=1001), run_query_generation_hash="0" * 64)

    assert skipped.is_valid and skipped.warning_message.startswith("reference data differs:")


def test_a_scale_without_a_reference_is_unvalidated_but_structural_checks_run(reference_dir) -> None:
    unvalidated = _decide("tpch", "2", 100, scale=10.0)
    failed = _decide("tpch", "3", 11, scale=10.0)

    assert unvalidated.unvalidated and unvalidated.check == "structural"
    assert not failed.is_valid and failed.bound == "at most 10"


def test_the_enumerated_tpch_ranges_apply_at_scale_one_only(reference_dir) -> None:
    inside = _decide("tpch", "13", 42, generation=QueryGeneration(seed=3))
    outside = _decide("tpch", "13", 43, generation=QueryGeneration(seed=3))
    loose_inside = _decide("tpch", "16", 5, generation=QueryGeneration(seed=9))
    other_scale = _decide("tpch", "13", 7, scale=0.01, generation=QueryGeneration(seed=3))

    assert inside.is_valid and inside.check == "range" and inside.bound == "41 to 42"
    assert not outside.is_valid and outside.check == "range"
    assert loose_inside.is_valid and loose_inside.check == "range"
    assert other_scale.check == "never_empty"


def test_the_tpch_default_parameters_are_checked_exactly(reference_dir) -> None:
    exact = _decide("tpch", "1", 4)
    wrong = _decide("tpch", "1", 3)

    assert exact.check == "exact" and exact.seed is None
    assert not wrong.is_valid and wrong.check == "exact"


def test_an_unconstrained_statement_records_a_documented_parameter_sensitive_skip(reference_dir) -> None:
    result = _decide("tpcds", "31", 17, generation=QueryGeneration(seed=31))
    record = row_count_validation_record(result, 17)

    assert result.is_valid and record["status"] == "SKIPPED"
    assert row_count_skip_is_documented(record) and record["warning"].startswith("parameter sensitive:")
    assert record["seed"] == 31 and record["streams_index"] is None and "check" not in record


def test_refreshed_data_keeps_the_checks_that_do_not_depend_on_the_base_data(reference_dir) -> None:
    invariant = _decide("tpch", "1", 4, data_refreshed=True)
    never_empty = _decide("tpch", "13", 43, data_refreshed=True)
    wrong = _decide("tpch", "1", 3, data_refreshed=True)
    empty = _decide("tpch", "13", 0, data_refreshed=True)

    assert invariant.is_valid and invariant.check == "invariant" and invariant.bound == "exactly 4"
    assert never_empty.is_valid and never_empty.check == "never_empty"
    assert not wrong.is_valid and wrong.check == "invariant"
    assert not empty.is_valid and empty.check == "never_empty"


def test_refreshed_data_skips_exact_range_and_unconstrained_counts_with_a_documented_reason(reference_dir) -> None:
    unconstrained = _decide("tpch", "2", 99, data_refreshed=True)
    no_reference = _decide("tpch", "2", 99, scale=10.0, data_refreshed=True)
    other_stamp = _decide("tpch", "1", 99, run_data_stamp="f" * 64, data_refreshed=True)

    for result in (unconstrained, no_reference, other_stamp):
        record = row_count_validation_record(result, 99)
        assert result.is_valid and record["status"] == "SKIPPED"
        assert record["warning"].startswith("data refreshed:") and row_count_skip_is_documented(record)


@pytest.mark.parametrize(
    ("suite", "query", "rows", "generation"),
    [
        ("tpcds", "2", 2500, QueryGeneration(seed=1001)),
        ("tpcds", "93", 100, QueryGeneration(seed=5)),
        ("tpcds", "2", 3, QueryGeneration(seed=5)),
        ("tpcds", "4", 17, QueryGeneration(seed=5)),
        ("tpcds", "1", 250, QueryGeneration(seed=5)),
        ("tpch", "13", 42, QueryGeneration(seed=3)),
        ("tpch", "2", 100, QueryGeneration(seed=None)),
    ],
)
def test_every_decision_round_trips_through_the_evidence_contract(
    reference_dir, suite, query, rows, generation
) -> None:
    result = _decide(suite, query, rows, generation=generation)
    record = row_count_validation_record(result, rows)
    normalized = normalize_row_count_validation(record, rows_returned=rows)

    assert normalized["status"] == record["status"]
    assert normalized["seed"] == generation.seed
    assert len(record.get("error", "")) <= 500 and len(record.get("warning", "")) <= 500


def test_a_pass_by_range_or_must_return_rows_stays_passed(reference_dir) -> None:
    record = row_count_validation_record(_decide("tpcds", "2", 3, generation=QueryGeneration(seed=5)), 3)
    normalized = normalize_row_count_validation(record, rows_returned=3)

    assert record["status"] == "PASSED" and record["check"] == "never_empty" and record["expected"] is None
    assert normalized["status"] == "PASSED" and normalized["bound"] == "at least 1 row"


def test_a_run_that_passed_a_check_rolls_up_passed(reference_dir) -> None:
    rows = [
        {
            "run_type": "measurement",
            "row_count_validation": row_count_validation_record(
                _decide("tpcds", query, rows, generation=QueryGeneration(seed=5)), rows
            ),
        }
        for query, rows in (("2", 3), ("93", 100), ("4", 12))
    ]

    status, _ = row_evidence_roll_up(rows, reference_exists=True)
    record = stream_checks.stream_checks_record("tpcds", 1.0, rows)

    assert status == PASSED_VALIDATION_STATUS
    assert record["checks"] == {"invariant": 1, "never_empty": 1, "structural": 1}
    assert record["references"][0]["sample_draws"] == 521
    assert record["references"][0]["data_stamp"] == compute_base_constants_hash("tpcds")


def test_stream_checks_are_not_defined_for_other_benchmarks(reference_dir) -> None:
    with pytest.raises(ValueError):
        decide_row_count("ssb", "1.1", 5, 1.0)
