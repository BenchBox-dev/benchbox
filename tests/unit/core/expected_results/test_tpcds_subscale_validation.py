import pytest

from benchbox.core.dataframe.query_validation import validate_dataframe_query_results
from benchbox.core.expected_results.models import ValidationMode, row_count_validation_record
from benchbox.core.expected_results.row_count_references import (
    KIND_ENGINE_AGREEMENT,
    QUERY_IDS,
    load_row_count_reference,
    reference_scales,
)
from benchbox.core.expected_results.tpcds_results import get_tpcds_expected_results, tpcds_known_query_ids
from benchbox.core.tpcds.power_test import power_parameter_seed
from benchbox.core.validation.query_validation import QueryValidator
from benchbox.core.validation.stream_checks import QueryGeneration, query_generation_context

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SCALE = 0.01


def _reference(scale: float = SCALE):
    return load_row_count_reference("tpcds", scale)


def _stream_counts(stream: int = 0, scale: float = SCALE) -> dict[str, int]:
    return dict(_reference(scale).streams[stream].row_counts)


def _unconstrained_query(scale: float = SCALE) -> str:
    reference = _reference(scale)
    return next(
        qid
        for qid in QUERY_IDS["tpcds"]
        if qid not in reference.integrity.invariant_counts and qid not in reference.integrity.never_empty
    )


def _invariant_query(scale: float = SCALE) -> str:
    return sorted(_reference(scale).integrity.invariant_counts, key=lambda q: (len(q), q))[0]


def _measurement(query_id: str, rows: int, stream_id: int = 0) -> dict[str, object]:
    return {
        "query_id": query_id,
        "status": "SUCCESS",
        "run_type": "measurement",
        "stream_id": stream_id,
        "iteration": 1,
        "rows_returned": rows,
    }


def _sql(query: str, rows: int, generation: QueryGeneration, scale: float = SCALE):
    with query_generation_context(generation):
        return QueryValidator().validate_query_result("tpcds", query, rows, scale_factor=scale)


@pytest.mark.parametrize("scale_factor", reference_scales("tpcds"))
def test_tpcds_provider_returns_recorded_counts(scale_factor: float) -> None:
    results = get_tpcds_expected_results(scale_factor=scale_factor)

    assert results is not None
    assert results.benchmark_name == "tpcds"
    assert results.scale_factor == scale_factor
    assert {qid: result.expected_row_count for qid, result in results.query_results.items()} == _stream_counts(
        0, scale_factor
    )
    assert {result.validation_mode for result in results.query_results.values()} == {ValidationMode.EXACT}
    assert {result.reference_kind for result in results.query_results.values()} == {KIND_ENGINE_AGREEMENT}
    assert results.metadata["provenance"]["data_checksum"]["sha256"]


@pytest.mark.parametrize("scale_factor", reference_scales("tpcds"))
def test_tpcds_references_cover_every_query_with_no_engine_disagreement_omitted(scale_factor: float) -> None:
    for stream in _reference(scale_factor).streams.values():
        assert set(stream.row_counts) == set(QUERY_IDS["tpcds"])
    assert tpcds_known_query_ids() >= set(QUERY_IDS["tpcds"])


def test_tpcds_provider_returns_none_for_unrecorded_scale() -> None:
    assert get_tpcds_expected_results(scale_factor=0.5) is None


@pytest.mark.parametrize("stream", [0, 1, 2, 3])
def test_a_seeded_row_is_exact_for_the_reference_seed_of_its_stream(stream: int) -> None:
    reference = _reference()
    exact_stream = reference.exact_stream(power_parameter_seed(None, stream), None)
    query = next(q for q in QUERY_IDS["tpcds"] if q not in reference.integrity.invariant_counts)

    result = _sql(query, exact_stream.row_counts[query], QueryGeneration(seed=exact_stream.seed))

    assert result.is_valid and result.check == "exact"
    assert result.expected_row_count == exact_stream.row_counts[query]
    assert result.seed == exact_stream.seed


def test_a_row_with_other_parameters_is_checked_not_excluded() -> None:
    query = _invariant_query()
    count = _reference().integrity.invariant_counts[query]

    passed = _sql(query, count, QueryGeneration(seed=987654321))
    failed = _sql(query, count + 5, QueryGeneration(seed=987654321))

    assert passed.is_valid and passed.check in ("invariant", "structural")
    assert not failed.is_valid
    assert failed.validation_mode == ValidationMode.EXACT


def test_a_statement_with_no_bound_records_a_documented_parameter_sensitive_skip() -> None:
    query = _unconstrained_query()

    result = _sql(query, _stream_counts()[query] + 3, QueryGeneration(seed=987654321))
    record = row_count_validation_record(result, _stream_counts()[query] + 3)

    assert result.is_valid and record["status"] == "SKIPPED"
    assert record["warning"].startswith("parameter sensitive:")


def test_tpcds_variant_normalization() -> None:
    result = _sql("Q14A", _stream_counts()["14a"], QueryGeneration(seed=1001))

    assert result.is_valid and result.check == "exact"


def test_tpcds_validation_mode_opt_out() -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    query = _invariant_query()
    set_validation_mode_context(ValidationMode.SKIP)
    try:
        result = _sql(query, 10**6, QueryGeneration(seed=1001))
    finally:
        set_validation_mode_context(None)

    assert result.is_valid and result.validation_mode == ValidationMode.SKIP


def test_tpcds_invalid_env_mode_does_not_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "bogus")

    result = _sql(_invariant_query(), 10**6, QueryGeneration(seed=1001))

    assert not result.is_valid


def test_tpcds_unrecorded_scale_is_unvalidated_and_structural_bounds_still_run() -> None:
    unvalidated = _sql("3", 999, QueryGeneration(seed=1), scale=0.5)
    too_many = _sql("1", 150, QueryGeneration(seed=1), scale=0.5)

    assert unvalidated.unvalidated and unvalidated.validation_mode == ValidationMode.SKIP
    assert not too_many.is_valid and too_many.check == "structural"


@pytest.mark.parametrize("seed", [None, 1])
def test_tpcds_dataframe_row_passes_at_the_reference_seed(seed: int | None) -> None:
    reference = _reference()
    query = next(q for q in QUERY_IDS["tpcds"] if q not in reference.integrity.invariant_counts)
    measurements = [_measurement(query_id=f"Q{query}", rows=_stream_counts()[query])]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpcds", scale_factor=SCALE, seed=seed)

    assert summary.status == "PASSED"
    assert summary.details["provider"] == "tpcds_row_count_reference"
    assert summary.details["reference_kinds"][KIND_ENGINE_AGREEMENT]["rows"] == 1
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}
    assert measurements[0]["row_count_validation"]["check"] == "exact"
    assert measurements[0]["row_count_validation"]["seed"] == power_parameter_seed(seed, 0)


def test_tpcds_dataframe_mutated_count_fails() -> None:
    reference = _reference()
    query = next(q for q in QUERY_IDS["tpcds"] if q not in reference.integrity.invariant_counts)
    wrong = _measurement(query_id=query, rows=_stream_counts()[query] + 1)

    summary = validate_dataframe_query_results([wrong], benchmark_name="tpcds", scale_factor=SCALE)

    assert summary.status == "FAILED"
    assert summary.details["failed"] == 1
    assert wrong["row_count_validation"]["status"] == "FAILED"
    assert wrong["row_count_validation"]["expected"] == _stream_counts()[query]


def test_tpcds_dataframe_row_with_other_parameters_is_checked_against_the_integrity_section() -> None:
    query = _invariant_query()
    count = _reference().integrity.invariant_counts[query]
    ok = _measurement(query_id=query, rows=count)
    bad = _measurement(query_id=query, rows=count + 9)

    validate_dataframe_query_results([ok, bad], benchmark_name="tpcds", scale_factor=SCALE, seed=7)

    assert ok["row_count_validation"]["status"] in ("PASSED", "SKIPPED")
    assert bad["row_count_validation"]["status"] == "FAILED"


@pytest.mark.parametrize("stream", [1, 2, 3])
def test_tpcds_dataframe_later_streams_are_checked_against_their_own_counts(stream: int) -> None:
    reference = _reference()
    query = next(q for q in QUERY_IDS["tpcds"] if q not in reference.integrity.invariant_counts)
    measurement = _measurement(query_id=query, rows=_stream_counts(stream)[query], stream_id=stream)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)

    assert summary.status == "PASSED"
    assert measurement["row_count_validation"]["expected"] == _stream_counts(stream)[query]


def test_tpcds_dataframe_unrecorded_scale_has_no_reference() -> None:
    measurement = _measurement(query_id="1", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=0.5)

    assert summary.status != "PASSED"
    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"


def test_tpcds_dataframe_validation_mode_skip_opt_out() -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    measurement = _measurement(query_id=_invariant_query(), rows=10**6)
    set_validation_mode_context(ValidationMode.SKIP)
    try:
        validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)
    finally:
        set_validation_mode_context(None)

    assert measurement["row_count_validation"]["status"] == "SKIPPED"


@pytest.mark.parametrize("seed", [None, 1, 7])
@pytest.mark.parametrize("stream", [0, 1])
def test_the_sql_and_dataframe_paths_record_the_same_evidence_for_the_same_seed_and_stream(seed, stream) -> None:
    reference = _reference()
    counts = _stream_counts(stream)
    queries = list(QUERY_IDS["tpcds"])[:40]
    rows = [_measurement(query_id=q, rows=counts[q], stream_id=stream) for q in queries]
    validate_dataframe_query_results(rows, benchmark_name="tpcds", scale_factor=SCALE, seed=seed)

    generation = QueryGeneration(seed=power_parameter_seed(seed, stream))
    for query, row in zip(queries, rows, strict=True):
        sql_record = row_count_validation_record(_sql(query, counts[query], generation), counts[query])
        assert {k: v for k, v in row["row_count_validation"].items() if k != "warning"} == {
            k: v for k, v in sql_record.items() if k != "warning"
        }, query
    assert reference.data_stamp
