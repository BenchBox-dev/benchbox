from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

from benchbox.core.benchmark_registry import (
    get_scale_factor_rule,
    is_standard_scale_factor,
    list_benchmark_ids,
)
from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from benchbox.core.results.submit_classification import SubmitTerminalState, classify_loaded_result
from benchbox.core.results.subscale_policy import (
    NONSTANDARD_COMPLIANCE_CLASS,
    effective_compliance_class,
    is_nonstandard_scale,
    result_recorded_compliance_class,
)
from benchbox.validation.bundle import ValidationResult, validate_bundles

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "results-data" / "validate_corpus.py"
BUNDLES = REPO_ROOT / "results-data" / "bundles"
PLATFORMS = ("DuckDB", "DataFusion", "SQLite")
TOLERANCE_INSIDE = 1e-10
TOLERANCE_OUTSIDE = 1e-6
FIXED_SCALE_BENCHMARKS = ("joinorder", "metadata_primitives")


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_corpus_nonstandard", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _off_list_scale(benchmark_id: str) -> float:
    rule = get_scale_factor_rule(benchmark_id)
    candidate = max(rule.standard) + 1.0
    assert not is_standard_scale_factor(benchmark_id, candidate)
    return candidate


def _bundle_payload(benchmark: str, scale: float, platform: str, *, compliance_class: str | None = None) -> dict:
    payload = {
        "benchmark": {"id": benchmark, "scale_factor": scale, "test_type": "power"},
        "platform": {"name": platform},
        "run": {"timestamp": "2026-11-01T12:00:00"},
        "export": {"anonymized": True},
        "summary": {
            "queries": {"total": 2, "passed": 2, "failed": 0},
            "validation": "passed",
            "tpc_metrics": {"power_at_size": 1.0},
        },
        "queries": [
            {"id": "Q1", "ms": 10.0, "run_type": "measurement", "status": "SUCCESS"},
            {"id": "Q2", "ms": 12.0, "run_type": "measurement", "status": "SUCCESS"},
        ],
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    return payload


def _write_cohort(directory: Path, benchmark: str, scale: float, *, compliance_class: str | None = None) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for platform in PLATFORMS:
        path = directory / f"{benchmark}_{platform.lower()}.json"
        path.write_text(
            json.dumps(_bundle_payload(benchmark, scale, platform, compliance_class=compliance_class)),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def _submission_bundle(benchmark: str, scale: float, **benchmark_fields: object) -> dict:
    from benchbox.validation.bundle import CANONICAL_LOGICAL_QUERY_IDS

    canonical = sorted(CANONICAL_LOGICAL_QUERY_IDS.get(benchmark, frozenset({"1", "2"})))
    return {
        "version": "2.1",
        "run": {"id": "abc123", "timestamp": "2026-04-01T12:00:00", "total_duration_ms": 5000},
        "benchmark": {"id": benchmark, "name": benchmark, "scale_factor": scale, **benchmark_fields},
        "platform": {"name": "DuckDB", "version": "1.4.3"},
        "summary": {
            "validation": "passed",
            "queries": {"total": len(canonical), "passed": len(canonical), "failed": 0},
        },
        "phases": {"validation": {"status": "PASSED"}},
        "queries": [{"id": qid, "ms": 100 + index * 10, "status": "SUCCESS"} for index, qid in enumerate(canonical)],
    }


def _validate(payload: dict) -> ValidationResult:
    from benchbox.validation.bundle import _validate_bundle

    vr = ValidationResult("test")
    _validate_bundle(payload, vr)
    return vr


def _nonstandard_warnings(vr: ValidationResult) -> list[str]:
    return [warning for warning in vr.warnings if warning.startswith("nonstandard-scale")]


@pytest.mark.parametrize("benchmark_id", list_benchmark_ids())
def test_every_registry_standard_scale_is_standard_and_every_other_value_is_not(benchmark_id: str) -> None:
    rule = get_scale_factor_rule(benchmark_id)

    for value in rule.standard:
        assert is_standard_scale_factor(benchmark_id, value)
        assert not is_nonstandard_scale(benchmark_id, value)
        assert is_standard_scale_factor(benchmark_id, value + TOLERANCE_INSIDE)
        assert not is_standard_scale_factor(benchmark_id, value + TOLERANCE_OUTSIDE)
        assert not is_standard_scale_factor(benchmark_id, value - TOLERANCE_OUTSIDE)
        assert is_nonstandard_scale(benchmark_id, value + TOLERANCE_OUTSIDE)

    assert is_nonstandard_scale(benchmark_id, _off_list_scale(benchmark_id))
    assert is_nonstandard_scale(benchmark_id, float("nan"))
    assert is_nonstandard_scale(benchmark_id, str(_off_list_scale(benchmark_id)))
    assert not is_nonstandard_scale(benchmark_id, None)
    assert not is_nonstandard_scale(benchmark_id, "not-a-number")
    assert not is_nonstandard_scale(benchmark_id, True)


def test_unknown_benchmark_ids_are_left_to_the_recorded_class() -> None:
    assert not is_nonstandard_scale("not-a-registered-benchmark", 2.0)
    assert effective_compliance_class("not-a-registered-benchmark", 2.0, None) is None


@pytest.mark.parametrize("benchmark_id", FIXED_SCALE_BENCHMARKS)
def test_fixed_scale_benchmarks_have_exactly_their_registry_scale(benchmark_id: str) -> None:
    rule = get_scale_factor_rule(benchmark_id)
    assert rule.exact

    for value in rule.standard:
        assert effective_compliance_class(benchmark_id, value, None) is None
    for value in (rule.minimum / 2, rule.maximum + 1, rule.maximum * 2):
        assert effective_compliance_class(benchmark_id, value, None) == NONSTANDARD_COMPLIANCE_CLASS


def test_off_list_scale_overrides_a_recorded_official_class() -> None:
    assert effective_compliance_class("tpch", 2.0, "official") == NONSTANDARD_COMPLIANCE_CLASS
    assert effective_compliance_class("tpch", 1.0, "official") == "official"


def test_standard_subscale_tpc_scales_keep_the_subscale_class_and_off_list_ones_do_not() -> None:
    assert effective_compliance_class("tpch", 0.1, None) == "unofficial_subscale"
    assert effective_compliance_class("tpch", 0.5, None) == NONSTANDARD_COMPLIANCE_CLASS
    assert effective_compliance_class("tpcds", 0.5, "unofficial_subscale") == NONSTANDARD_COMPLIANCE_CLASS


@pytest.mark.parametrize("benchmark_id", list_benchmark_ids())
def test_three_platform_off_list_cohort_never_ranks(tmp_path: Path, benchmark_id: str) -> None:
    validator = _load_validator()
    scale = _off_list_scale(benchmark_id)
    paths = _write_cohort(tmp_path, benchmark_id, scale)

    cohorts = validator.cohort_platforms(paths)

    assert cohorts == {(benchmark_id, f"{scale}"): set()}
    assert validator.unranked_cohorts(cohorts) == [(benchmark_id, f"{scale}")]
    assert validator.shallow_cohorts(cohorts) == {}
    for path in paths:
        assert validator.exclusion_reason(json.loads(path.read_text(encoding="utf-8"))) == "unofficial_compliance"


@pytest.mark.parametrize("benchmark_id", list_benchmark_ids())
def test_recorded_class_does_not_rescue_an_off_list_cohort(tmp_path: Path, benchmark_id: str) -> None:
    validator = _load_validator()
    scale = _off_list_scale(benchmark_id)
    paths = _write_cohort(tmp_path, benchmark_id, scale, compliance_class="official")

    assert validator.cohort_platforms(paths) == {(benchmark_id, f"{scale}"): set()}


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb", "clickbench", "joinorder", "metadata_primitives"])
def test_standard_three_platform_cohort_stays_ranked(tmp_path: Path, benchmark_id: str) -> None:
    validator = _load_validator()
    scale = min(value for value in get_scale_factor_rule(benchmark_id).standard if value >= 1.0)
    paths = _write_cohort(tmp_path, benchmark_id, scale)

    cohorts = validator.cohort_platforms(paths)

    assert cohorts == {(benchmark_id, f"{scale}"): {"duckdb", "datafusion", "sqlite"}}
    assert validator.shallow_cohorts(cohorts) == {}


def test_an_off_list_cohort_does_not_change_the_standard_cohort_beside_it(tmp_path: Path) -> None:
    validator = _load_validator()
    standard = _write_cohort(tmp_path / "standard", "ssb", 1.0)
    off_list = _write_cohort(tmp_path / "off", "ssb", 2.0)

    cohorts = validator.cohort_platforms([*standard, *off_list])

    assert cohorts == {("ssb", "1.0"): {"duckdb", "datafusion", "sqlite"}, ("ssb", "2.0"): set()}


@pytest.mark.parametrize("benchmark_id", ["tpch", "ssb", "clickbench", "joinorder", "metadata_primitives"])
def test_submission_accepts_an_off_list_bundle_and_discloses_the_exclusion(benchmark_id: str) -> None:
    vr = _validate(_submission_bundle(benchmark_id, _off_list_scale(benchmark_id)))

    assert vr.ok, vr.errors
    (warning,) = _nonstandard_warnings(vr)
    assert "unofficial_compliance" in warning
    assert "excluded from ranking" in warning


def test_submission_accepts_the_stamped_class_at_an_off_list_scale() -> None:
    vr = _validate(_submission_bundle("tpch", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS))

    assert vr.ok, vr.errors
    assert len(_nonstandard_warnings(vr)) == 1


def test_submission_accepts_a_recorded_subscale_class_at_an_off_list_scale() -> None:
    vr = _validate(_submission_bundle("tpch", 0.5, compliance_class="unofficial_subscale"))

    assert vr.ok, vr.errors


@pytest.mark.parametrize("compliance_class", ["garbage", "", "Official", 7, ["official"]])
@pytest.mark.parametrize(("benchmark_id", "scale"), [("tpch", 2.0), ("ssb", 2.0), ("tpch", 1.0)])
def test_submission_refuses_an_unknown_compliance_class_at_any_scale(
    benchmark_id: str, scale: float, compliance_class: object
) -> None:
    vr = _validate(_submission_bundle(benchmark_id, scale, compliance_class=compliance_class))

    assert not vr.ok
    assert any("not a recognised compliance class" in error for error in vr.errors)
    assert not _nonstandard_warnings(vr)


def test_unknown_compliance_class_is_refused_under_partial_validation_and_internal_source() -> None:
    from benchbox.validation.bundle import _validate_compliance_section

    for flags in ({"allow_partial_validation": True}, {"internal_source": True}):
        for scale in (1.0, 2.0):
            vr = ValidationResult("test")
            _validate_compliance_section(
                {"id": "tpch", "scale_factor": scale, "compliance_class": "garbage"}, vr, **flags
            )
            assert any("not a recognised compliance class" in error for error in vr.errors), (flags, scale)


def test_unknown_compliance_class_on_a_gateway_routed_bundle_is_refused() -> None:
    payload = _submission_bundle("ssb", 2.0, compliance_class="garbage")
    payload["platform"]["gateway"] = {"name": "espresso", "routed": True}

    assert any("not a recognised compliance class" in error for error in _validate(payload).errors)


@pytest.mark.parametrize("compliance_class", [NONSTANDARD_COMPLIANCE_CLASS, "unofficial_subscale"])
def test_submission_still_refuses_unofficial_classes_at_a_standard_scale(compliance_class: str) -> None:
    scale = 1.0 if compliance_class == NONSTANDARD_COMPLIANCE_CLASS else 0.1
    vr = _validate(_submission_bundle("tpch", scale, compliance_class=compliance_class))

    assert not vr.ok
    assert any("compliance_class" in error for error in vr.errors)
    assert not _nonstandard_warnings(vr)


@pytest.mark.parametrize("benchmark_id", ["tpch", "ssb", "joinorder"])
def test_submission_of_a_standard_scale_bundle_has_no_exclusion_warning(benchmark_id: str) -> None:
    scale = get_scale_factor_rule(benchmark_id).standard[0]
    vr = _validate(_submission_bundle(benchmark_id, scale))

    assert vr.ok, vr.errors
    assert not any("unofficial_compliance" in warning for warning in vr.warnings)


def test_submission_still_refuses_gateway_routed_bundles_at_an_off_list_scale() -> None:
    routed_payload = _submission_bundle("ssb", 2.0)
    routed_payload["platform"]["gateway"] = {"name": "espresso", "routed": True}
    routed = _validate(routed_payload)

    assert not routed.ok
    assert any("unofficial_nonstandard" in error for error in routed.errors)


def test_a_registry_less_validator_accepts_a_stamped_non_tpc_bundle_and_refuses_a_stamped_tpc_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchbox.validation.bundle as bundle_module

    monkeypatch.setattr(bundle_module, "is_standard_scale_factor", None)

    stamped_ssb = _validate(_submission_bundle("ssb", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS))
    assert stamped_ssb.ok, stamped_ssb.errors
    assert any("unofficial_compliance" in warning for warning in stamped_ssb.warnings)

    unstamped_ssb = _validate(_submission_bundle("ssb", 2.0))
    assert unstamped_ssb.ok, unstamped_ssb.errors

    stamped_tpch = _validate(_submission_bundle("tpch", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS))
    assert not stamped_tpch.ok

    routed_payload = _submission_bundle("ssb", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS)
    routed_payload["platform"]["gateway"] = {"name": "espresso", "routed": True}
    assert not _validate(routed_payload).ok


def test_validate_bundles_accepts_an_off_list_file(tmp_path: Path) -> None:
    path = tmp_path / "ssb_sf2.json"
    path.write_text(json.dumps(_submission_bundle("ssb", 2.0)), encoding="utf-8")

    (vr,) = validate_bundles([path])

    assert vr.ok, vr.errors


def _result(benchmark_name: str, scale: float, compliance_class: str | None = None) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name=benchmark_name,
        platform="DuckDB",
        scale_factor=scale,
        execution_id="nonstandard-scale",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[],
        total_execution_time=1.0,
        average_query_time=1.0,
        validation_status="PASSED",
        platform_info={"platform_name": "DuckDB"},
        compliance_class=compliance_class,
    )


@pytest.mark.parametrize("benchmark_name", ["ssb", "clickbench", "joinorder"])
def test_new_bundles_at_an_off_list_scale_record_the_unranked_class(benchmark_name: str) -> None:
    result = _result(benchmark_name, _off_list_scale(benchmark_name))

    assert result_recorded_compliance_class(result) == NONSTANDARD_COMPLIANCE_CLASS
    payload = build_result_payload(result)
    assert payload["benchmark"]["compliance_class"] == NONSTANDARD_COMPLIANCE_CLASS


@pytest.mark.parametrize(
    ("benchmark_name", "recorded"), [("ssb", None), ("tpch", "unofficial_subscale"), ("tpch", "official")]
)
def test_off_list_bundles_suppress_tpc_metrics_in_step_with_the_recorded_class(
    benchmark_name: str, recorded: str | None
) -> None:
    result = _result(benchmark_name, 2.0 if recorded != "unofficial_subscale" else 0.5, recorded)
    result.power_at_size = 10.0

    summary = build_result_payload(result)["summary"]

    assert summary["tpc_metrics"] == {
        "suppressed": True,
        "reason": f"compliance_class={NONSTANDARD_COMPLIANCE_CLASS}",
    }


def test_new_bundles_at_a_standard_scale_record_no_class() -> None:
    result = _result("ssb", 1.0)

    assert result_recorded_compliance_class(result) is None
    assert "compliance_class" not in build_result_payload(result)["benchmark"]


def test_cli_submit_and_publish_accept_an_off_list_result_and_refuse_the_rest() -> None:
    for off_list in (
        _result("ssb", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 0.5, "unofficial_subscale"),
    ):
        assert classify_loaded_result(off_list) is SubmitTerminalState.submittable
        assert publish_admission(off_list, "community").allowed

    routed = _result("ssb", 2.0)
    routed.platform_info["gateway"] = {"name": "espresso", "routed": True}
    assert classify_loaded_result(routed) is SubmitTerminalState.unofficial
    assert not publish_admission(routed, "community").allowed

    for refused in (
        _result("tpch", 1.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 0.1, "unofficial_subscale"),
    ):
        assert classify_loaded_result(refused) is SubmitTerminalState.unofficial
        assert not publish_admission(refused, "community").allowed


def test_every_ranked_committed_cohort_is_at_a_registry_standard_scale() -> None:
    validator = _load_validator()
    cohorts = validator.cohort_platforms(validator.discover_bundles(BUNDLES))

    ranked = {key for key, platforms in cohorts.items() if platforms}
    assert ranked, "no ranked cohorts found - this gate would be vacuous"
    off_list = {key for key in ranked if not is_standard_scale_factor(key[0], float(key[1].split("#", maxsplit=1)[0]))}
    assert off_list == set()
