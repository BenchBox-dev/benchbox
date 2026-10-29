from __future__ import annotations

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.schema import build_result_payload
from benchbox.core.runner.runner import (
    _attach_translation_metadata,
    _finalize_validation_metadata,
    _resolve_strict_translation_mode,
)
from benchbox.utils.dialect_utils import SqlTranslationOutcome
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_translation_fallback_metadata_exports_and_marks_validation_uncertain() -> None:
    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={"row_count_matches": True},
        execution_metadata={},
    )
    outcomes = [
        SqlTranslationOutcome(
            source_dialect="netezza",
            target_dialect="duckdb",
            normalized_source_dialect="postgres",
            normalized_target_dialect="duckdb",
            translator="sqlglot",
            status="fallback",
            strict_mode=False,
            warning_category="translation_failed",
            message="synthetic failure",
        )
    ]

    enriched = _attach_translation_metadata(result, outcomes, strict_mode=False)
    payload = build_result_payload(enriched)

    assert enriched.validation_status == "UNCERTAIN"
    assert payload["summary"]["validation"] == "uncertain"
    assert payload["execution"]["translation"]["status"] == "fallback"
    assert payload["execution"]["translation"]["warning_categories"] == ["translation_failed"]
    assert payload["execution"]["translation"]["outcomes"][0]["count"] == 1


def test_loader_preserves_translation_metadata_on_reconstruct() -> None:
    result = make_benchmark_results(
        validation_status="UNCERTAIN",
        validation_details={"warnings": ["translation fallback"]},
        execution_metadata={
            "translation": {
                "status": "fallback",
                "strict_mode": False,
                "attempt_count": 1,
                "fallback_count": 1,
                "success_count": 0,
                "failed_count": 0,
                "translators": ["sqlglot"],
                "source_dialects": ["netezza"],
                "target_dialects": ["duckdb"],
            }
        },
    )
    payload = build_result_payload(result)

    reconstructed = reconstruct_benchmark_results(payload)

    assert reconstructed.validation_status == "uncertain"
    assert reconstructed.execution_metadata == {"translation": payload["execution"]["translation"]}


def test_no_validation_records_do_not_leave_default_passed_status() -> None:
    result = make_benchmark_results(validation_status="PASSED", validation_details=None)

    finalized = _finalize_validation_metadata(result, [])

    assert finalized.validation_status == "NOT_RUN"
    assert finalized.validation_details == {"stages": [], "error_count": 0, "warning_count": 0}


@pytest.mark.parametrize(
    "options",
    [
        {"strict_translation": True},
        {"platform_options": {"translation_strict": "true"}},
        {"benchmark_options": {"sql_translation_strict": "strict"}},
    ],
)
def test_strict_translation_mode_resolves_from_supported_option_scopes(options: dict) -> None:
    assert _resolve_strict_translation_mode(options) is True


def test_variant_comparability_metadata_exports_to_payload() -> None:
    from benchbox.core.runner.runner import _attach_variant_comparability_metadata

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={},
        execution_metadata={},
    )

    class _Benchmark:
        def get_benchmark_info(self):
            return {"variant_comparability": {"comparable": True, "issue_count": 0, "total_queries": 3}}

    enriched = _attach_variant_comparability_metadata(result, _Benchmark())
    payload = build_result_payload(enriched)

    assert payload["execution"]["variant_comparability"]["comparable"] is True
    assert payload["execution"]["variant_comparability"]["issue_count"] == 0


def test_variant_comparability_attach_leaves_other_benchmarks_untouched() -> None:
    from benchbox.core.runner.runner import _attach_variant_comparability_metadata

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={},
        execution_metadata={},
    )

    class _Benchmark:
        pass

    enriched = _attach_variant_comparability_metadata(result, _Benchmark())
    assert "variant_comparability" not in (enriched.execution_metadata or {})


def test_variant_comparability_survives_reconstruction() -> None:
    from benchbox.core.results.loader import reconstruct_benchmark_results

    result = make_benchmark_results(
        validation_status="PASSED",
        validation_details={},
        execution_metadata={"variant_comparability": {"comparable": True, "issue_count": 0}},
    )
    payload = build_result_payload(result)
    rebuilt = reconstruct_benchmark_results(payload)

    assert rebuilt.execution_metadata is not None
    assert rebuilt.execution_metadata["variant_comparability"]["comparable"] is True
    assert build_result_payload(rebuilt)["execution"]["variant_comparability"]["issue_count"] == 0


def _vendored_runtime_pin() -> dict:
    import json
    from pathlib import Path

    import benchbox

    pin_path = Path(benchbox.__file__).parent / "_vendor" / "sqlglot.json"
    return json.loads(pin_path.read_text(encoding="utf-8"))


def _translation_outcome() -> SqlTranslationOutcome:
    return SqlTranslationOutcome(
        source_dialect="duckdb",
        target_dialect="sqlite",
        normalized_source_dialect="duckdb",
        normalized_target_dialect="sqlite",
        translator="sqlglot",
        status="success",
        strict_mode=False,
    )


def test_translation_runtime_equals_the_vendored_runtime_pin() -> None:
    pin = _vendored_runtime_pin()
    result = make_benchmark_results(execution_metadata={})

    enriched = _attach_translation_metadata(result, [_translation_outcome()], strict_mode=False)
    payload = build_result_payload(enriched)

    assert payload["execution"]["translation"]["runtime"] == {
        "distribution": pin["distribution"],
        "version": pin["version"],
        "fork_sha": pin["fork_sha"],
        "upstream_sha": pin["upstream_sha"],
        "wheel_sha256": pin["sha256"],
    }
    assert payload["result_schema_version"] == build_result_payload(make_benchmark_results())["result_schema_version"]


def test_runs_without_translation_outcomes_carry_no_runtime() -> None:
    result = make_benchmark_results(platform="polars-df", execution_metadata={"mode": "dataframe"})

    enriched = _attach_translation_metadata(result, [], strict_mode=False)
    payload = build_result_payload(enriched)

    assert "translation" not in payload.get("execution", {})


def test_loader_round_trips_the_translation_runtime() -> None:
    result = make_benchmark_results(execution_metadata={})
    enriched = _attach_translation_metadata(result, [_translation_outcome()], strict_mode=False)
    payload = build_result_payload(enriched)

    reconstructed = reconstruct_benchmark_results(payload)

    assert reconstructed.execution_metadata["translation"]["runtime"] == payload["execution"]["translation"]["runtime"]
