import json
from pathlib import Path

import pytest

from benchbox.core.expected_results.loader import (
    TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS,
    load_tpch_subscale_row_counts,
)
from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)
from benchbox.core.expected_results.registry import ExpectedResultsRegistry, get_registry
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_reference_seed_context,
    set_reference_seed_context,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryValidatorExactModeSafeguards:
    @pytest.mark.parametrize(
        ("expected_count", "actual_count", "mode", "valid"),
        [
            (None, 123, ValidationMode.SKIP, True),
            (7, 7, ValidationMode.EXACT, True),
            (7, 8, ValidationMode.EXACT, False),
        ],
        ids=["missing-count-skips-with-warning", "known-count-matches", "known-count-mismatch-fails"],
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
            assert result.warning_message is not None
            assert "EXACT validation mode but no expected count available" in result.warning_message
            assert "Validation skipped" in result.warning_message
            assert result.error_message is None
        else:
            assert result.warning_message is None
            assert (result.error_message is None) is valid

    def test_integration_with_real_tpcds_variants(self):
        from benchbox.core.expected_results.loader import load_tpcds_expected_results

        validator = QueryValidator()

        tpcds_results = load_tpcds_expected_results(scale_factor=1.0)

        if "14" in tpcds_results and "14a" in tpcds_results:
            result = validator.validate_query_result(
                benchmark_type="tpcds",
                query_id="14a",
                actual_row_count=tpcds_results["14a"],
                scale_factor=1.0,
            )

            assert result.is_valid is True, f"Variant 14a validation should not fail: {result.error_message}"
            assert result.validation_mode in (ValidationMode.EXACT, ValidationMode.SKIP, ValidationMode.RANGE)


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
        if "TEST_SKIP_MODE" not in registry._cache:
            registry._cache["TEST_SKIP_MODE"] = {}
        registry._cache["TEST_SKIP_MODE"][1.0] = test_registry

        try:
            validator = QueryValidator()

            result = validator.validate_query_result(
                benchmark_type="TEST_SKIP_MODE",
                query_id="test_skip",
                actual_row_count=999,
                scale_factor=1.0,
            )

            assert result.is_valid is True
            assert result.validation_mode == ValidationMode.SKIP
            assert "skipped" in result.warning_message.lower()

        finally:
            registry._cache.pop("TEST_SKIP_MODE", None)


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
        assert result.validation_mode == ValidationMode.SKIP
        assert "no expected" in result.warning_message.lower() or "validation skipped" in result.warning_message.lower()
        assert result.expected_row_count is None


class TestTPCHSubscaleRowCountReference:
    @pytest.fixture(autouse=True)
    def _fresh_registry(self):
        get_registry().clear_cache()
        yield
        clear_reference_seed_context()
        get_registry().clear_cache()

    @pytest.mark.parametrize("scale_factor", [0.01, 0.1])
    def test_every_reference_count_passes_exactly(self, scale_factor):
        counts = load_tpch_subscale_row_counts(scale_factor).row_counts
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

    def test_parameter_sensitive_query_is_skipped_for_other_parameters(self):
        reference = load_tpch_subscale_row_counts(0.01)
        assert {"2", "9", "21"} <= reference.parameter_sensitive_query_ids
        set_reference_seed_context(False)

        result = QueryValidator().validate_query_result("tpch", "2", 3, scale_factor=0.01)

        assert result.is_valid is True
        assert result.validation_mode == ValidationMode.SKIP
        assert "qgen default parameters" in result.warning_message

    def test_fixed_cardinality_query_is_still_checked_for_other_parameters(self):
        set_reference_seed_context(False)

        result = QueryValidator().validate_query_result("tpch", "4", 4, scale_factor=0.01)

        assert result.is_valid is False
        assert result.validation_mode == ValidationMode.EXACT

    def test_parameter_sensitive_query_is_exact_at_the_reference_parameters(self):
        set_reference_seed_context(True)

        result = QueryValidator().validate_query_result("tpch", "16", 295, scale_factor=0.01)

        assert result.is_valid is False
        assert result.expected_row_count == 296

    def test_unrecorded_scale_factor_keeps_the_scale_independent_fallback(self):
        validator = QueryValidator()

        assert validator.validate_query_result("tpch", "1", 4, scale_factor=0.5).validation_mode == ValidationMode.EXACT
        assert validator.validate_query_result("tpch", "4", 5, scale_factor=0.5).validation_mode == ValidationMode.SKIP

    def test_reference_files_are_well_formed_and_query_template_bound(self):
        import hashlib

        from benchbox.core.expected_results.loader import tpch_subscale_row_count_reference_path

        root = Path(__file__).resolve().parents[3] / "_sources" / "tpc-h" / "dbgen"
        for scale_factor in TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS:
            payload = json.loads(tpch_subscale_row_count_reference_path(scale_factor).read_text(encoding="utf-8"))
            template = payload["provenance"]["query_template_digest"]
            digest = hashlib.sha256()
            for relative in template["templates"]:
                digest.update(relative.encode("utf-8") + b"\0" + (root / relative).read_bytes())
            assert digest.hexdigest() == template["sha256"], scale_factor
            assert set(payload["provenance"]["engines"]) == {"duckdb", "datafusion"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [("reference_seed", 7), ("scale_factor", 0.1), ("row_counts", {"1": 4})],
    )
    def test_malformed_reference_is_rejected(self, monkeypatch, tmp_path, field, value):
        from benchbox.core.expected_results import loader

        source = json.loads(loader.tpch_subscale_row_count_reference_path(0.01).read_text(encoding="utf-8"))
        source[field] = value
        target = tmp_path / "tpch_row_counts_sf0.01.json"
        target.write_text(json.dumps(source), encoding="utf-8")
        monkeypatch.setattr(loader, "tpch_subscale_row_count_reference_path", lambda scale_factor: target)

        with pytest.raises(ValueError):
            loader.load_tpch_subscale_row_counts(0.01)
