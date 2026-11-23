from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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


def _timed_queries(benchmark: str) -> list[dict]:
    from benchbox.validation.bundle import CANONICAL_LOGICAL_QUERY_IDS

    canonical = sorted(CANONICAL_LOGICAL_QUERY_IDS.get(benchmark, frozenset({"Q1", "Q2"})))
    return [
        {"id": query_id, "ms": 10.0 + index, "iter": 1, "run_type": "measurement", "status": "SUCCESS"}
        for index, query_id in enumerate(canonical)
    ]


def _bundle_payload(benchmark: str, scale: float, platform: str, *, compliance_class: str | None = None) -> dict:
    queries = _timed_queries(benchmark)
    payload = {
        "benchmark": {"id": benchmark, "scale_factor": scale, "test_type": "power"},
        "platform": {"name": platform},
        "run": {"timestamp": "2026-11-01T12:00:00"},
        "export": {"anonymized": True},
        "summary": {
            "queries": {"total": len(queries), "passed": len(queries), "failed": 0},
            "validation": "passed",
        },
        "queries": queries,
        "phases": {"power_test": {"status": "COMPLETED"}},
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    return payload


@pytest.mark.parametrize("bench_id", ["tpch", "tpcds"])
def test_nonstandard_combined_run_is_admitted_without_metric(bench_id):
    payload = _bundle_payload(bench_id, 2.0, "example")
    payload["benchmark"]["test_type"] = "combined"
    validator = _load_validator()
    assert validator.rule_outcome(payload).lanes == ()
    assert validator.admission_reason(payload) is None
    assert validator.exclusion_reason(payload) == "missing_primary_metric"


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


_PASSED_ROW_COUNT = {"rows": 5, "row_count_validation": {"status": "PASSED", "expected": 5, "actual": 5}}


def _submission_bundle(benchmark: str, scale: float, **benchmark_fields: object) -> dict:
    from benchbox.validation.bundle import CANONICAL_LOGICAL_QUERY_IDS

    canonical = sorted(CANONICAL_LOGICAL_QUERY_IDS.get(benchmark, frozenset({"1", "2"})))
    if benchmark == "tpcds":
        variants = {"14": ("14a", "14b"), "23": ("23a", "23b"), "24": ("24a", "24b"), "39": ("39a", "39b")}
        canonical = sorted({variant for query_id in canonical for variant in variants.get(query_id, (query_id,))})
    return {
        "version": "2.2",
        "run": {"id": "abc123", "timestamp": "2026-04-01T12:00:00", "total_duration_ms": 5000},
        "benchmark": {"id": benchmark, "name": benchmark, "scale_factor": scale, **benchmark_fields},
        "platform": {"name": "DuckDB", "version": "1.4.3"},
        "summary": {
            "validation": "passed",
            "queries": {"total": len(canonical), "passed": len(canonical), "failed": 0},
        },
        "phases": {"validation": {"status": "PASSED"}},
        "queries": [
            {"id": qid, "ms": 100 + index * 10, "status": "SUCCESS", **(_PASSED_ROW_COUNT if index == 0 else {})}
            for index, qid in enumerate(canonical)
        ],
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
    expected = "not_admitted_undetermined" if benchmark_id == "tpcdi" else "not_ranked_nonstandard_scale"
    for path in paths:
        assert validator.exclusion_reason(json.loads(path.read_text(encoding="utf-8"))) == expected


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


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb", "clickbench", "joinorder", "metadata_primitives"])
def test_submission_accepts_an_off_list_bundle_and_discloses_the_exclusion(benchmark_id: str) -> None:
    vr = _validate(_submission_bundle(benchmark_id, _off_list_scale(benchmark_id)))

    assert vr.ok, vr.errors
    (warning,) = _nonstandard_warnings(vr)
    assert "accepted and not ranked" in warning
    assert "Non-standard scale" in warning


def test_submission_accepts_the_stamped_class_at_an_off_list_scale() -> None:
    vr = _validate(_submission_bundle("tpch", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS))

    assert vr.ok, vr.errors
    assert len(_nonstandard_warnings(vr)) == 1


def test_submission_refuses_a_tpc_bundle_below_the_minimum_scale_whether_or_not_the_scale_is_listed() -> None:
    vr = _validate(_submission_bundle("tpch", 0.5, compliance_class="unofficial_subscale"))

    assert not vr.ok
    assert any("not accepted for public submissions" in error for error in vr.errors)


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


@pytest.mark.parametrize("compliance_class", [None, "official", "unofficial_subscale"])
def test_submission_refuses_a_sub_sf1_tpc_bundle_whatever_it_is_stamped(compliance_class: str | None) -> None:
    kwargs = {} if compliance_class is None else {"compliance_class": compliance_class}
    vr = _validate(_submission_bundle("tpch", 0.1, **kwargs))

    assert not vr.ok
    assert any("not accepted for public submissions" in error for error in vr.errors)
    assert not _nonstandard_warnings(vr)


@pytest.mark.parametrize("compliance_class", [NONSTANDARD_COMPLIANCE_CLASS, "unofficial_subscale"])
def test_a_stamp_does_not_refuse_a_bundle_at_a_standard_scale(compliance_class: str) -> None:
    vr = _validate(_submission_bundle("tpch", 1.0, compliance_class=compliance_class))

    assert vr.ok, vr.errors


@pytest.mark.parametrize("benchmark_id", ["tpch", "ssb", "joinorder"])
def test_submission_of_a_standard_scale_bundle_has_no_exclusion_warning(benchmark_id: str) -> None:
    scale = min(value for value in get_scale_factor_rule(benchmark_id).standard if value >= 1.0)
    vr = _validate(_submission_bundle(benchmark_id, scale))

    assert vr.ok, vr.errors
    assert not any("unofficial_compliance" in warning for warning in vr.warnings)


def test_submission_still_refuses_gateway_routed_bundles_at_an_off_list_scale() -> None:
    routed_payload = _submission_bundle("ssb", 2.0)
    routed_payload["platform"]["gateway"] = {"name": "espresso", "routed": True}
    routed = _validate(routed_payload)

    assert not routed.ok
    assert any("gateway_routed" in error for error in routed.errors)


@pytest.mark.parametrize("compliance_class", [None, "official", NONSTANDARD_COMPLIANCE_CLASS, "unofficial_subscale"])
def test_the_submission_outcome_at_an_off_list_scale_does_not_depend_on_the_stamp(
    compliance_class: str | None,
) -> None:
    kwargs = {} if compliance_class is None else {"compliance_class": compliance_class}

    ssb = _validate(_submission_bundle("ssb", 2.0, **kwargs))
    assert ssb.ok, ssb.errors
    assert len(_nonstandard_warnings(ssb)) == 1

    routed_payload = _submission_bundle("ssb", 2.0, **kwargs)
    routed_payload["platform"]["gateway"] = {"name": "espresso", "routed": True}
    assert not _validate(routed_payload).ok


MIRRORED_RUNTIME = (
    "scripts/validate_submission.py",
    "benchbox/validation/bundle.py",
    "benchbox/core/results/query_status.py",
    "benchbox/core/results/timing_fields.py",
    "benchbox/core/results/schema_policy.py",
    "benchbox/core/results/subscale_policy.py",
    "benchbox/core/results/corpus_rule.py",
    "benchbox/core/results/corpus_rule_data.json",
    "benchbox/core/results/corpus_score.py",
    "benchbox/core/results/comparison_identity.py",
    "benchbox/core/results/comparison_identity.json",
)
MIRROR_CASES = [
    ("tpch", 2.0, None),
    ("tpch", 2.0, "official"),
    ("tpch", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("tpch", 0.5, "unofficial_subscale"),
    ("tpch", 0.5, None),
    ("tpch", 1.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("tpch", 1.0, None),
    ("tpcds", 2.0, None),
    ("tpcds", 2.0, "official"),
    ("tpcds", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("tpcds", 30.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("tpcds", 0.5, "unofficial_subscale"),
    ("tpcds", 1000.0, None),
    ("ssb", 2.0, None),
    ("ssb", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("ssb", 1.0, NONSTANDARD_COMPLIANCE_CLASS),
    ("clickbench", 10.0, None),
    ("joinorder", 2.0, None),
]


def _slim_mirror(root: Path, *, with_projection: bool = True) -> Path:
    for relative in MIRRORED_RUNTIME:
        if relative.endswith("corpus_rule_data.json") and not with_projection:
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / relative).read_bytes())
    (root / "results-data" / "bundles").mkdir(parents=True, exist_ok=True)
    return root


def _run_mirror(root: Path, payload: dict, *, partial: bool) -> subprocess.CompletedProcess[str]:
    payload = {**payload, "export": {"anonymized": True}}
    bundle = root / "results-data" / "bundles" / "candidate.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    command = [sys.executable, "-I", "-S", "scripts/validate_submission.py", str(bundle.relative_to(root))]
    if partial:
        command.append("--allow-partial-validation")
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=60)


@pytest.fixture(scope="module")
def slim_mirror(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _slim_mirror(tmp_path_factory.mktemp("published-results"))


@pytest.mark.parametrize("partial", [False, True], ids=["strict", "partial"])
@pytest.mark.parametrize(("benchmark_id", "scale", "compliance_class"), MIRROR_CASES)
def test_the_registry_less_validator_reaches_the_same_verdict_and_warning_as_the_registry(
    slim_mirror: Path, tmp_path: Path, benchmark_id: str, scale: float, compliance_class: str | None, partial: bool
) -> None:
    fields = {} if compliance_class is None else {"compliance_class": compliance_class}
    payload = _submission_bundle(benchmark_id, scale, **fields)
    payload["export"] = {"anonymized": True}
    core_path = tmp_path / "results-data" / "bundles" / "candidate.json"
    core_path.parent.mkdir(parents=True)
    core_path.write_text(json.dumps(payload), encoding="utf-8")
    (core,) = validate_bundles([core_path], allow_partial_validation=partial)

    mirror = _run_mirror(slim_mirror, payload, partial=partial)

    assert (mirror.returncode == 0) == core.ok, mirror.stdout + mirror.stderr
    assert ("nonstandard-scale" in mirror.stdout) == bool(_nonstandard_warnings(core))
    assert ("is not accepted for public submissions" in mirror.stdout) == any(
        "is not accepted for public submissions" in error for error in core.errors
    )


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds"])
def test_the_registry_less_validator_accepts_an_off_list_tpc_bundle_stamped_nonstandard(
    slim_mirror: Path, benchmark_id: str
) -> None:
    payload = _submission_bundle(benchmark_id, 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS)

    mirror = _run_mirror(slim_mirror, payload, partial=False)

    assert mirror.returncode == 0, mirror.stdout + mirror.stderr
    assert "nonstandard-scale" in mirror.stdout


def test_the_registry_less_validator_still_refuses_a_gateway_routed_off_list_bundle(slim_mirror: Path) -> None:
    payload = _submission_bundle("ssb", 2.0, compliance_class=NONSTANDARD_COMPLIANCE_CLASS)
    payload["platform"]["gateway"] = {"name": "espresso", "routed": True}

    assert _run_mirror(slim_mirror, payload, partial=False).returncode == 1


@pytest.mark.parametrize("partial", [False, True], ids=["strict", "partial"])
def test_the_registry_less_validator_fails_closed_without_the_projection(tmp_path: Path, partial: bool) -> None:
    root = _slim_mirror(tmp_path, with_projection=False)

    mirror = _run_mirror(root, _submission_bundle("tpch", 2.0), partial=partial)

    assert mirror.returncode != 0
    assert "corpus_rule_data.json is missing or unreadable" in mirror.stdout + mirror.stderr


@pytest.mark.parametrize(
    ("benchmark_id", "scale", "compliance_class", "expected"),
    [
        ("tpch", 2.0, None, "not_ranked_nonstandard_scale"),
        ("tpch", 2.0, "official", "not_ranked_nonstandard_scale"),
        ("tpcds", 30.0, None, "not_ranked_nonstandard_scale"),
        ("ssb", 2.0, None, "not_ranked_nonstandard_scale"),
        ("tpch", 1.0, None, "missing_primary_metric"),
        ("tpcds", 1000.0, None, "missing_primary_metric"),
    ],
)
def test_the_registry_less_exclusion_reason_sees_an_off_list_scale(
    slim_mirror: Path, benchmark_id: str, scale: float, compliance_class: str | None, expected: str | None
) -> None:
    destination = slim_mirror / "results-data" / "validate_corpus.py"
    destination.write_bytes(VALIDATOR.read_bytes())
    code = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('validate_corpus', 'results-data/validate_corpus.py')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['validate_corpus'] = module\n"
        "spec.loader.exec_module(module)\n"
        "payload = json.loads(sys.argv[1])\n"
        "print(json.dumps(module.exclusion_reason(payload)))\n"
    )
    payload = _submission_bundle(
        benchmark_id, scale, **({} if compliance_class is None else {"compliance_class": compliance_class})
    )
    if expected == "not_ranked_nonstandard_scale":
        payload = _bundle_payload(benchmark_id, scale, "DuckDB", compliance_class=compliance_class)

    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, json.dumps(payload)],
        cwd=slim_mirror,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert expected == _load_validator().exclusion_reason(payload)


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
        query_results=[
            {
                "query_id": "1",
                "execution_time_ms": 1.0,
                "status": "SUCCESS",
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ],
        total_execution_time=1.0,
        average_query_time=1.0,
        validation_status="PASSED",
        platform_info={"platform_name": "DuckDB"},
        compliance_class=compliance_class,
    )


@pytest.mark.parametrize("benchmark_name", ["ssb", "clickbench", "joinorder"])
def test_new_bundles_at_an_off_list_scale_record_the_unranked_class(benchmark_name: str) -> None:
    result = _result(benchmark_name, _off_list_scale(benchmark_name))

    payload = build_result_payload(result)
    assert payload["benchmark"]["compliance_class"] == NONSTANDARD_COMPLIANCE_CLASS


@pytest.mark.parametrize(
    ("benchmark_name", "scale", "recorded_class"),
    [
        ("ssb", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
        ("tpch", 0.5, "unofficial_subscale"),
        ("tpch", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
    ],
)
def test_off_list_bundles_keep_their_scores_and_never_suppress_them(
    benchmark_name: str, scale: float, recorded_class: str
) -> None:
    result = _result(benchmark_name, scale)
    result.power_at_size = 10.0

    payload = build_result_payload(result)

    assert payload["benchmark"]["compliance_class"] == recorded_class
    assert payload["summary"]["tpc_metrics"] == {
        "unofficial": True,
        "reason": f"compliance_class={recorded_class}",
        "power_at_size": 10.0,
    }


def test_new_bundles_at_a_standard_scale_record_no_class() -> None:
    result = _result("ssb", 1.0)

    assert "compliance_class" not in build_result_payload(result)["benchmark"]


def test_cli_submit_and_publish_accept_an_off_list_result_and_refuse_the_rest() -> None:
    for off_list in (
        _result("ssb", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 2.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 1.0, NONSTANDARD_COMPLIANCE_CLASS),
        _result("tpch", 1.0),
    ):
        assert classify_loaded_result(off_list) is SubmitTerminalState.submittable
        assert publish_admission(off_list, "community").allowed

    routed = _result("ssb", 2.0)
    routed.platform_info["gateway"] = {"name": "espresso", "routed": True}
    assert classify_loaded_result(routed) is SubmitTerminalState.unofficial
    assert not publish_admission(routed, "community").allowed

    for refused in (
        _result("tpch", 0.5),
        _result("tpch", 0.1, "unofficial_subscale"),
        _result("tpcds", 0.01, "official"),
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
