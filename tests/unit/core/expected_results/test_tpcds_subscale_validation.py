import pytest

from benchbox.core.dataframe.query_validation import validate_dataframe_query_results
from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.row_count_references import (
    KIND_ENGINE_AGREEMENT,
    QUERY_IDS,
    load_row_count_reference,
    reference_scales,
)
from benchbox.core.expected_results.tpcds_results import (
    get_tpcds_expected_results,
    tpcds_known_query_ids,
    tpcds_reference_seed,
)
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_reference_seed_context,
    set_reference_seed_context,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SCALE = 0.01


def _reference(scale: float = SCALE):
    return load_row_count_reference("tpcds", scale)


def _stream_counts(stream: int = 0, scale: float = SCALE) -> dict[str, int]:
    return dict(_reference(scale).streams[stream].row_counts)


def _fixed_query(scale: float = SCALE) -> str:
    reference = _reference(scale)
    return next(qid for qid in QUERY_IDS["tpcds"] if qid not in reference.parameter_sensitive_query_ids)


def _sensitive_query(scale: float = SCALE) -> str:
    return sorted(_reference(scale).parameter_sensitive_query_ids)[0]


@pytest.fixture
def validator():
    from benchbox.core.expected_results.tpcds_results import set_query_validation_mode

    set_query_validation_mode(ValidationMode.EXACT)
    yield QueryValidator()
    set_query_validation_mode(None)


@pytest.fixture(autouse=True)
def _clear_reference_seed():
    clear_reference_seed_context()
    yield
    clear_reference_seed_context()


def _measurement(query_id: str, rows: int, stream_id: int = 0) -> dict[str, object]:
    return {
        "query_id": query_id,
        "status": "SUCCESS",
        "run_type": "measurement",
        "stream_id": stream_id,
        "iteration": 1,
        "rows_returned": rows,
    }


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


def test_tpcds_reference_seed_follows_the_stream() -> None:
    for stream in range(4):
        assert tpcds_reference_seed(None, stream)
        assert tpcds_reference_seed(1, stream)
        assert not tpcds_reference_seed(2, stream)


def test_tpcds_sensitive_queries_are_those_that_vary_across_streams() -> None:
    reference = _reference()
    varying = {
        qid for qid in QUERY_IDS["tpcds"] if len({stream.row_counts[qid] for stream in reference.streams.values()}) > 1
    }

    assert reference.parameter_sensitive_query_ids == varying
    assert varying


def test_tpcds_sql_fixed_query_validates(validator) -> None:
    query = _fixed_query()
    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id=query, actual_row_count=_stream_counts()[query], scale_factor=SCALE
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT
    assert result.expected_row_count == _stream_counts()[query]
    assert result.reference_kind == KIND_ENGINE_AGREEMENT


def test_tpcds_sql_mutated_count_fails_and_names_the_reference(validator) -> None:
    query = _fixed_query()
    expected = _stream_counts()[query]
    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id=query, actual_row_count=expected + 1, scale_factor=SCALE
    )

    assert not result.is_valid
    assert result.expected_row_count == expected
    assert "Reference: agreement with duckdb" in result.error_message


@pytest.mark.parametrize("stream", [0, 1, 2, 3])
def test_tpcds_sql_sensitive_query_validates_against_its_own_stream(validator, stream: int) -> None:
    set_reference_seed_context(True)
    query = _sensitive_query()
    expected = _stream_counts(stream)[query]

    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id=query, actual_row_count=expected, scale_factor=SCALE, stream_id=stream
    )

    assert result.is_valid
    assert result.expected_row_count == expected
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_sql_non_reference_seed_skips_validation(validator) -> None:
    set_reference_seed_context(False)
    query = _fixed_query()

    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id=query, actual_row_count=_stream_counts()[query] + 5, scale_factor=SCALE
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None


def test_tpcds_sql_stream_without_a_reference_is_skipped(validator) -> None:
    set_reference_seed_context(True)
    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id="3", actual_row_count=1, scale_factor=SCALE, stream_id=40
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP


def test_tpcds_variant_normalization(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id="Q14A", actual_row_count=_stream_counts()["14a"], scale_factor=SCALE
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_default_mode_is_exact_without_configuration() -> None:
    query = _fixed_query()
    result = QueryValidator().validate_query_result("tpcds", query, _stream_counts()[query], SCALE)

    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_default_mode_is_exact_at_scale_one() -> None:
    query = _fixed_query(1.0)
    result = QueryValidator().validate_query_result("tpcds", query, _stream_counts(0, 1.0)[query] + 1, 1.0)

    assert not result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_validation_mode_opt_out(validator) -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    query = _fixed_query()
    set_validation_mode_context(ValidationMode.SKIP)
    try:
        result = validator.validate_query_result(
            benchmark_type="tpcds", query_id=query, actual_row_count=_stream_counts()[query] + 1, scale_factor=SCALE
        )
    finally:
        set_validation_mode_context(None)

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP


def test_tpcds_invalid_env_mode_does_not_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "bogus")
    query = _fixed_query()

    result = QueryValidator().validate_query_result("tpcds", query, _stream_counts()[query] + 1, SCALE)

    assert not result.is_valid
    assert result.validation_mode == ValidationMode.EXACT


def test_tpcds_unrecorded_scale_skips_not_fails(validator) -> None:
    result = validator.validate_query_result(
        benchmark_type="tpcds", query_id="3", actual_row_count=999, scale_factor=0.5
    )

    assert result.is_valid
    assert result.validation_mode == ValidationMode.SKIP
    assert result.expected_row_count is None


@pytest.mark.parametrize("seed", [None, 1])
def test_tpcds_dataframe_fixed_query_passes_at_reference_seed(seed: int | None) -> None:
    query = _fixed_query()
    measurements = [_measurement(query_id=f"Q{query}", rows=_stream_counts()[query])]

    summary = validate_dataframe_query_results(measurements, benchmark_name="tpcds", scale_factor=SCALE, seed=seed)

    assert summary.status == "PASSED"
    assert summary.details["provider"] == "tpcds_row_count_reference"
    assert summary.details["checked"] == 1
    assert summary.details["reference_kinds"][KIND_ENGINE_AGREEMENT]["rows"] == 1
    assert summary.details["reference_kinds"][KIND_ENGINE_AGREEMENT]["label"].startswith("agreement with duckdb")
    assert {row["row_count_validation"]["status"] for row in measurements} == {"PASSED"}


def test_tpcds_dataframe_mutated_count_fails() -> None:
    query = _fixed_query()
    correct = _measurement(query_id="86", rows=_stream_counts()["86"])
    wrong = _measurement(query_id=query, rows=_stream_counts()[query] + 1)

    summary = validate_dataframe_query_results([correct, wrong], benchmark_name="tpcds", scale_factor=SCALE)

    assert summary.status == "FAILED"
    assert summary.details["failed"] == 1
    assert wrong["row_count_validation"]["status"] == "FAILED"
    assert wrong["row_count_validation"]["expected"] == _stream_counts()[query]


def test_tpcds_dataframe_non_reference_seed_skips() -> None:
    query = _fixed_query()
    measurement = _measurement(query_id=query, rows=_stream_counts()[query])

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE, seed=7)

    assert summary.status != "PASSED"
    assert measurement["row_count_validation"]["status"] == "SKIPPED"


@pytest.mark.parametrize("stream", [1, 2, 3])
def test_tpcds_dataframe_later_streams_are_checked_against_their_own_counts(stream: int) -> None:
    query = _sensitive_query()
    measurement = _measurement(query_id=query, rows=_stream_counts(stream)[query], stream_id=stream)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)

    assert summary.status == "PASSED"
    assert measurement["row_count_validation"]["expected"] == _stream_counts(stream)[query]


def test_tpcds_dataframe_stream_without_a_reference_is_unvalidated() -> None:
    measurement = _measurement(query_id="3", rows=1, stream_id=40)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)

    assert summary.status != "PASSED"
    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"


def test_tpcds_dataframe_throughput_rows_never_use_stream_references() -> None:
    query = _fixed_query()
    measurement = {**_measurement(query_id=query, rows=_stream_counts()[query]), "test_type": "throughput"}

    validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)

    assert measurement["row_count_validation"]["status"] == "SKIPPED"


def test_tpcds_dataframe_unrecorded_scale_has_no_reference() -> None:
    measurement = _measurement(query_id="1", rows=1)

    summary = validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=0.5)

    assert summary.status != "PASSED"
    assert measurement["row_count_validation"]["status"] == "UNVALIDATED"


def test_tpcds_dataframe_validation_mode_skip_opt_out() -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    query = _fixed_query()
    measurement = _measurement(query_id=query, rows=_stream_counts()[query] + 1)
    set_validation_mode_context(ValidationMode.SKIP)
    try:
        validate_dataframe_query_results([measurement], benchmark_name="tpcds", scale_factor=SCALE)
    finally:
        set_validation_mode_context(None)

    assert measurement["row_count_validation"]["status"] == "SKIPPED"
