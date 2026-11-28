import json
from pathlib import Path

import pytest

from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)
from benchbox.core.expected_results.registry import ExpectedResultsRegistry, get_registry
from benchbox.core.expected_results.row_count_references import (
    load_row_count_reference,
    reference_path,
    reference_scales,
    validate_payload,
)
from benchbox.core.validation.query_validation import QueryValidator
from benchbox.core.validation.stream_checks import QueryGeneration, query_generation_context

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryValidatorExactModeSafeguards:
    @pytest.mark.parametrize(
        ("expected_count", "actual_count", "mode", "valid"),
        [
            (None, 123, ValidationMode.EXACT, False),
            (7, 7, ValidationMode.EXACT, True),
            (7, 8, ValidationMode.EXACT, False),
        ],
        ids=["missing-count-fails-with-named-error", "known-count-matches", "known-count-mismatch-fails"],
    )
    def test_exact_count_lookup_verdict(self, monkeypatch, expected_count, actual_count, mode, valid):
        expected = ExpectedQueryResult(query_id="14a", expected_row_count=7, validation_mode=ValidationMode.EXACT)
        if expected_count is None:
            monkeypatch.setattr(expected, "get_expected_count", lambda scale_factor: None)
        answers = BenchmarkExpectedResults(
            benchmark_name="missing_count_fixture",
            scale_factor=1.0,
            query_results={"14a": expected},
        )
        registry = ExpectedResultsRegistry()
        registry.register_provider("missing_count_fixture", lambda scale_factor: answers)
        validator = QueryValidator()
        validator.registry = registry

        result = validator.validate_query_result(
            benchmark_type="missing_count_fixture",
            query_id="14a",
            actual_row_count=actual_count,
            scale_factor=1.0,
        )

        assert result.is_valid is valid
        assert result.validation_mode == mode
        assert result.query_id == "14a"
        assert result.expected_row_count == expected_count
        assert result.actual_row_count == actual_count
        if expected_count is None:
            assert result.warning_message is None
            assert result.error_message is not None
            assert "no expected count" in result.error_message
            assert "Row-count lookup failed" in result.error_message
        else:
            assert result.warning_message is None
            assert (result.error_message is None) is valid

    def test_integration_with_real_tpcds_variants(self):
        validator = QueryValidator()

        recorded = load_row_count_reference("tpcds", 1.0).streams[0].row_counts

        result = validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="14a",
            actual_row_count=recorded["14a"],
            scale_factor=1.0,
        )

        assert result.is_valid is True, f"Variant 14a validation should not fail: {result.error_message}"
        assert result.validation_mode == ValidationMode.EXACT


class TestQueryValidatorVariantHandling:
    def test_variant_registration_via_loader(self):
        from benchbox.core.expected_results.loader import load_tpcds_expected_results

        results = load_tpcds_expected_results(scale_factor=1.0)

        if "14" in results:
            assert "14a" in results, "Variant 14a should be registered"
            assert "14b" in results, "Variant 14b should be registered"
            assert results["14a"] == results["14"], "Variant should have same count as base"
            assert results["14b"] == results["14"], "Variant should have same count as base"

        if "23" in results:
            assert "23a" in results, "Variant 23a should be registered"
            assert "23b" in results, "Variant 23b should be registered"
            assert results["23a"] == results["23"], "Variant should have same count as base"
            assert results["23b"] == results["23"], "Variant should have same count as base"


class TestQueryValidatorSkipMode:
    def test_skip_mode_always_passes(self):
        test_result = ExpectedQueryResult(
            query_id="test_skip",
            validation_mode=ValidationMode.SKIP,
            scale_independent=True,
        )

        test_registry = BenchmarkExpectedResults(
            benchmark_name="TEST_SKIP_MODE",
            scale_factor=1.0,
            query_results={"test_skip": test_result},
        )

        registry = get_registry()
        if "test_skip_mode" not in registry._cache:
            registry._cache["test_skip_mode"] = {}
        registry._cache["test_skip_mode"][1.0] = test_registry

        try:
            validator = QueryValidator()

            result = validator.validate_query_result(
                benchmark_type="TEST_SKIP_MODE",
                query_id="test_skip",
                actual_row_count=999,
                scale_factor=1.0,
            )

            assert result.is_valid is True
            assert result.unvalidated is False
            assert result.validation_mode == ValidationMode.SKIP
            assert "skipped" in result.warning_message.lower()

        finally:
            registry._cache.pop("test_skip_mode", None)


class TestQueryValidatorNoExpectation:
    def test_query_without_expectation_skips_validation(self):
        validator = QueryValidator()

        result = validator.validate_query_result(
            benchmark_type="NONEXISTENT_BENCHMARK",
            query_id="Q999",
            actual_row_count=1000,
            scale_factor=1.0,
        )

        assert result.is_valid is True
        assert result.unvalidated is True
        assert result.validation_mode == ValidationMode.SKIP
        assert "no reference answers" in result.warning_message.lower()
        assert result.expected_row_count is None


class TestTPCHSubscaleRowCountReference:
    @pytest.fixture(autouse=True)
    def _fresh_registry(self):
        get_registry().clear_cache()
        yield
        get_registry().clear_cache()

    @pytest.mark.parametrize("scale_factor", [0.01, 0.1])
    def test_every_reference_count_passes_exactly(self, scale_factor):
        counts = load_row_count_reference("tpch", scale_factor).streams[0].row_counts
        validator = QueryValidator()

        for query_id, count in counts.items():
            result = validator.validate_query_result("tpch", query_id, count, scale_factor=scale_factor)
            assert result.is_valid, query_id
            assert result.validation_mode == ValidationMode.EXACT
            assert result.expected_row_count == count

    def test_wrong_count_fails(self):
        result = QueryValidator().validate_query_result("tpch", "Q15", 0, scale_factor=0.01)

        assert result.is_valid is False
        assert result.validation_mode == ValidationMode.EXACT
        assert result.expected_row_count == 1

    def test_an_invariant_statement_is_checked_for_other_parameters(self):
        reference = load_row_count_reference("tpch", 0.01)
        query_id, count = next(iter(sorted(reference.integrity.invariant_counts.items(), key=lambda i: int(i[0]))))

        with query_generation_context(QueryGeneration(seed=7)):
            ok = QueryValidator().validate_query_result("tpch", query_id, count, scale_factor=0.01)
            bad = QueryValidator().validate_query_result("tpch", query_id, count + 1, scale_factor=0.01)

        assert ok.is_valid and ok.check in ("invariant", "structural") and ok.seed == 7
        assert not bad.is_valid

    def test_a_varying_statement_must_still_return_rows_for_other_parameters(self):
        reference = load_row_count_reference("tpch", 0.01)
        varying = next(
            query_id
            for query_id in reference.integrity.never_empty
            if query_id not in reference.integrity.invariant_counts
        )

        with query_generation_context(QueryGeneration(seed=7)):
            result = QueryValidator().validate_query_result("tpch", varying, 0, scale_factor=0.01)

        assert not result.is_valid
        assert result.check in ("never_empty", "structural", "range")

    def test_a_statement_is_exact_at_the_default_parameters(self):
        result = QueryValidator().validate_query_result("tpch", "16", 295, scale_factor=0.01)

        assert result.is_valid is False
        assert result.check == "exact"
        assert result.expected_row_count == 296

    def test_unrecorded_scale_factor_is_unvalidated_but_structural_bounds_and_scale_independent_counts_still_run(self):
        validator = QueryValidator()

        unvalidated = validator.validate_query_result("tpch", "4", 5, scale_factor=0.5)
        too_many = validator.validate_query_result("tpch", "3", 11, scale_factor=0.5)
        scale_independent = validator.validate_query_result("tpch", "1", 4, scale_factor=0.5)
        wrong_scale_independent = validator.validate_query_result("tpch", "1", 3, scale_factor=0.5)

        assert unvalidated.unvalidated and unvalidated.validation_mode == ValidationMode.SKIP
        assert not too_many.is_valid and too_many.check == "structural"
        assert scale_independent.validation_mode == ValidationMode.EXACT and scale_independent.is_valid
        assert not wrong_scale_independent.is_valid

    def test_reference_files_are_well_formed_and_bound_to_the_query_generation(self):
        from benchbox.core.expected_results.query_generation import current_query_generation_stamp

        for scale_factor in reference_scales("tpch"):
            payload = json.loads(reference_path("tpch", scale_factor).read_text(encoding="utf-8"))
            validate_payload(payload, f"tpch sf{scale_factor:g}")
            assert payload["provenance"]["generator"]["query_generation"] == current_query_generation_stamp("tpch")
            assert set(payload["provenance"]["engines"]) == {"duckdb", "datafusion"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [("benchmark", "ssb"), ("scale_factor", 1), ("streams", {"0": {"row_counts": {"1": 4}}})],
    )
    def test_malformed_reference_is_rejected(self, field, value):
        source = json.loads(reference_path("tpch", 0.01).read_text(encoding="utf-8"))
        source[field] = value

        with pytest.raises(ValueError):
            validate_payload(source, "tpch_row_counts_sf0.01.json")
