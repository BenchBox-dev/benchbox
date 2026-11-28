from __future__ import annotations

import copy
import json

import pytest

from benchbox.core.expected_results import row_count_references as references
from benchbox.core.expected_results.registry import LoadOutcome, get_registry
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_reference_seed_context,
    set_reference_seed_context,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ALL_REFERENCES = [
    (benchmark, scale)
    for benchmark in references.REFERENCE_BENCHMARKS
    for scale in references.reference_scales(benchmark)
]


@pytest.fixture(autouse=True)
def _fresh_registry():
    get_registry().clear_cache()
    yield
    clear_reference_seed_context()
    get_registry().clear_cache()


def _payload(benchmark: str = "ssb", scale: float = 0.01) -> dict:
    path = references.reference_path(benchmark, scale)
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("benchmark_id", references.REFERENCE_BENCHMARKS)
def test_every_benchmark_has_references_at_the_small_standard_scales(benchmark_id: str) -> None:
    assert {0.01, 0.1, 1.0} <= set(references.reference_scales(benchmark_id))


@pytest.mark.parametrize(("benchmark_id", "scale"), ALL_REFERENCES)
def test_every_reference_lists_every_query_on_every_stream_with_provenance(benchmark_id: str, scale: float) -> None:
    reference = references.load_row_count_reference(benchmark_id, scale)

    assert reference.kind == references.KIND_ENGINE_AGREEMENT
    assert reference.streams
    for stream in reference.streams.values():
        assert set(stream.row_counts) == set(references.QUERY_IDS[benchmark_id])
    provenance = reference.provenance
    assert set(provenance["engines"]) == {"duckdb", "datafusion"}
    assert len(provenance["data_checksum"]["sha256"]) == 64
    assert provenance["agreement"]["result"] == "identical"
    assert provenance["agreement"]["streams"] == sorted(reference.streams)
    assert provenance["generator"]["data_generator"]
    assert provenance["generated_on"]


@pytest.mark.parametrize("scale", [1.0])
def test_tpc_streams_cover_the_minimum_stream_counts(scale: float) -> None:
    assert set(references.load_row_count_reference("tpch", scale).streams) >= {0, 1, 2}
    assert set(references.load_row_count_reference("tpcds", scale).streams) >= {0, 1, 2, 3}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version=1),
        lambda p: p.update(reference_kind="unlabelled"),
        lambda p: p["provenance"].pop("data_checksum"),
        lambda p: p["provenance"]["engines"].pop("datafusion"),
        lambda p: p["provenance"]["agreement"].update(result="disagree"),
        lambda p: p["streams"]["0"]["row_counts"].pop("1.1"),
        lambda p: p["streams"]["0"]["row_counts"].update({"1.1": -1}),
        lambda p: p["streams"].clear(),
    ],
)
def test_malformed_references_are_rejected(mutate) -> None:
    payload = copy.deepcopy(_payload("ssb", 0.01))
    mutate(payload)

    with pytest.raises(ValueError):
        references.validate_payload(payload, "ssb_row_counts_sf0.01.json")


def test_a_missing_reference_is_reported_not_invented() -> None:
    assert references.has_reference("tpch", 0.5) is False
    with pytest.raises(FileNotFoundError):
        references.load_row_count_reference("tpch", 0.5)


def test_registry_serves_every_recorded_stream_and_labels_the_kind() -> None:
    registry = get_registry()
    reference = references.load_row_count_reference("tpch", 0.01)

    for stream in reference.streams:
        expected, outcome = registry.get_expected_result_detailed("tpch", "4", 0.01, stream)
        assert outcome is LoadOutcome.HIT
        assert expected.expected_row_count == reference.streams[stream].row_counts["4"]
        assert expected.reference_kind == references.KIND_ENGINE_AGREEMENT
        assert expected.reference_label.startswith("agreement with duckdb")


def test_registry_skips_a_stream_with_no_recorded_reference() -> None:
    expected, outcome = get_registry().get_expected_result_detailed("tpcds", "3", 0.01, 99)

    assert expected is None
    assert outcome is LoadOutcome.STREAM_NOT_REFERENCE_VALIDATED


def test_tpch_scale_one_stream_zero_is_the_tpc_answer_set() -> None:
    expected, outcome = get_registry().get_expected_result_detailed("tpch", "4", 1.0, 0)

    assert outcome is LoadOutcome.HIT
    assert expected.reference_kind == references.KIND_TPC_ANSWER_SET


def test_validator_names_the_reference_kind_on_a_pass_and_on_a_failure() -> None:
    validator = QueryValidator()
    count = references.load_row_count_reference("ssb", 0.01).streams[0].row_counts["2.1"]

    passed = validator.validate_query_result("ssb", "2.1", count, scale_factor=0.01)
    failed = validator.validate_query_result("ssb", "2.1", count + 1, scale_factor=0.01)

    assert passed.is_valid and passed.reference_kind == references.KIND_ENGINE_AGREEMENT
    assert not failed.is_valid
    assert failed.reference_kind == references.KIND_ENGINE_AGREEMENT
    assert "Reference: agreement with duckdb" in failed.error_message


def test_validator_checks_a_later_tpcds_stream_against_that_streams_counts() -> None:
    reference = references.load_row_count_reference("tpcds", 0.01)
    differing = next(
        query_id
        for query_id, count in reference.streams[0].row_counts.items()
        if reference.streams[1].row_counts[query_id] != count
    )
    set_reference_seed_context(True)
    validator = QueryValidator()

    wrong_stream_count = reference.streams[0].row_counts[differing]
    result = validator.validate_query_result("tpcds", differing, wrong_stream_count, scale_factor=0.01, stream_id=1)

    assert not result.is_valid
    assert result.expected_row_count == reference.streams[1].row_counts[differing]


def _write_reference(tmp_path, monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(references, "REFERENCE_DIR", tmp_path)
    references.reference_path(payload["benchmark"], payload["scale_factor"]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.mark.parametrize("benchmark_id", references.REFERENCE_BENCHMARKS)
def test_loader_refuses_a_reference_without_provenance(benchmark_id: str, tmp_path, monkeypatch) -> None:
    payload = copy.deepcopy(_payload(benchmark_id, 0.01))
    del payload["provenance"]
    _write_reference(tmp_path, monkeypatch, payload)

    with pytest.raises(ValueError, match="provenance"):
        references.load_row_count_reference(benchmark_id, 0.01)


@pytest.mark.parametrize("benchmark_id", references.REFERENCE_BENCHMARKS)
@pytest.mark.parametrize("field", ["data_generation_version", "base_constants_hash"])
def test_loader_refuses_a_reference_with_a_stale_data_stamp(
    benchmark_id: str, field: str, tmp_path, monkeypatch
) -> None:
    payload = copy.deepcopy(_payload(benchmark_id, 0.01))
    payload["provenance"]["generator"]["data_stamp"][field] = "stale" if field.endswith("hash") else 0
    _write_reference(tmp_path, monkeypatch, payload)

    with pytest.raises(ValueError, match="data stamp"):
        references.load_row_count_reference(benchmark_id, 0.01)


def test_loader_refuses_a_reference_with_no_data_stamp(tmp_path, monkeypatch) -> None:
    payload = copy.deepcopy(_payload("tpch", 0.01))
    del payload["provenance"]["generator"]["data_stamp"]
    _write_reference(tmp_path, monkeypatch, payload)

    with pytest.raises(ValueError, match="data stamp"):
        references.load_row_count_reference("tpch", 0.01)


def test_a_four_seed_sensitivity_list_never_fails_a_seeded_tpcds_row() -> None:
    from benchbox.core.dataframe.query_validation import validate_dataframe_query_results

    reference = references.load_row_count_reference("tpcds", 0.01)
    assert reference.parameter_sensitive_query_ids
    for stream, entry in reference.streams.items():
        results = references.expected_query_results(reference, stream)
        assert not any(result.reference_parameters_only for result in results.values())
        rows = [
            {
                "query_id": query_id,
                "status": "SUCCESS",
                "run_type": "measurement",
                "stream_id": stream,
                "rows_returned": entry.row_counts[query_id],
            }
            for query_id in sorted(reference.parameter_sensitive_query_ids)
        ]
        summary = validate_dataframe_query_results(rows, benchmark_name="tpcds", scale_factor=0.01)
        assert summary.status == "PASSED"
        assert all(row["row_count_validation"]["status"] == "PASSED" for row in rows)

    for seed_context in (None, True, False):
        set_reference_seed_context(seed_context)
        for query_id in sorted(reference.parameter_sensitive_query_ids):
            result = QueryValidator().validate_query_result(
                "tpcds", query_id, reference.streams[0].row_counts[query_id], scale_factor=0.01, stream_id=0
            )
            assert result.is_valid
