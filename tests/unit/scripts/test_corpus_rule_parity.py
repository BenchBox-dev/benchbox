from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results import corpus_rule
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.submit_classification import SubmitTerminalState, classify_result_path
from benchbox.validation.bundle import validate_bundles
from tests.unit.scripts.explorer_pipeline.conftest import combined_bundle, scored_minimal_bundle, throughput_bundle

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_GATE_MESSAGE = "not accepted for public submissions"
NONSTANDARD_WARNING = "nonstandard-scale"
PHASE_BLOCKS = {
    "power": {"power_test": {"status": "COMPLETED"}},
}


@dataclass(frozen=True)
class Case:
    name: str
    benchmark: str
    scale: float
    shape: str
    public: bool
    lanes: tuple[str, ...]
    label: str | None


CASES = (
    Case("power-only", "tpch", 1.0, "power", True, (corpus_rule.LANE_POWER_ONLY,), "Power test only"),
    Case("throughput-only", "tpch", 1.0, "throughput", True, (corpus_rule.LANE_THROUGHPUT,), "Throughput test only"),
    Case("combined", "tpch", 1.0, "combined", True, (corpus_rule.LANE_COMBINED,), "Full specification run"),
    Case("subscale", "tpch", 0.01, "power", False, (corpus_rule.LANE_SUBSCALE,), "Scale below the TPC minimum"),
    Case("subscale-tenth", "tpch", 0.1, "power", False, (corpus_rule.LANE_SUBSCALE,), "Scale below the TPC minimum"),
    Case("tpc-nonstandard-scale", "tpch", 2.0, "power", True, (), corpus_rule.NONSTANDARD_SCALE_LABEL),
    Case("other-nonstandard-scale", "ssb", 2.0, "other", True, (), corpus_rule.NONSTANDARD_SCALE_LABEL),
    Case("other-standard-scale", "ssb", 1.0, "other", True, (corpus_rule.LANE_OWN,), None),
)


def _load_module(name: str, relative: str) -> ModuleType:
    path = REPO_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validator() -> ModuleType:
    return _load_module("validate_corpus_parity", "results-data/validate_corpus.py")


def _stager() -> ModuleType:
    sys.path.insert(0, str(REPO_ROOT / "results-data"))
    try:
        return _load_module("stage_seed_corpus_bundle_parity", "scripts/stage_seed_corpus_bundle.py")
    finally:
        sys.path.remove(str(REPO_ROOT / "results-data"))


def _bundle(case: Case) -> dict[str, Any]:
    if case.shape == "combined":
        data = combined_bundle(benchmark=case.benchmark)
    elif case.shape == "throughput":
        data = throughput_bundle(benchmark=case.benchmark, power_at_size=None, compliance_class="official")
        data["benchmark"].pop("compliance_class")
    elif case.shape == "power":
        data = scored_minimal_bundle()
        data["phases"] = copy.deepcopy(PHASE_BLOCKS["power"])
    else:
        data = scored_minimal_bundle()
        data["version"] = "2.2"
        data["benchmark"]["id"] = case.benchmark
        data["benchmark"]["name"] = case.benchmark
        data["queries"] = [
            {
                "id": str(number),
                "ms": 10.0 + number,
                "rows": 1,
                "iter": 1,
                "stream": 0,
                "run_type": "measurement",
                "status": "SUCCESS",
                "row_count_validation": {"status": "PASSED", "expected": 1, "actual": 1},
            }
            for number in range(1, 14)
        ]
        data["summary"]["queries"] = {"total": 13, "passed": 13, "failed": 0}
        data["summary"].pop("tpc_metrics", None)
        data["phases"] = {}
    data["version"] = "2.2"
    data["phases"]["validation"] = {"status": "PASSED"}
    for query in data["queries"]:
        rows = query["rows"]
        query["row_count_validation"] = {"status": "PASSED", "expected": rows, "actual": rows}
    data["benchmark"]["scale_factor"] = case.scale
    data["export"] = {"anonymized": True}
    return data


def _write(directory: Path, name: str, data: dict[str, Any], *, internal: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    if internal:
        path.with_name(f"{path.stem}.manifest.json").write_text(
            json.dumps(
                {
                    "result_source": "internal",
                    "bundle_file": path.name,
                    "bundle_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    return path


def _gate_errors(path: Path, *, trusted: bool) -> list[str]:
    (result,) = validate_bundles([path], trust_internal_source=trusted, allow_partial_validation=trusted)
    return result.errors


def _publish_admitted(path: Path) -> bool:
    result, _raw = load_result_file(path)
    return publish_admission(result, "community").allowed


def _staged(tmp_path: Path, path: Path, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "parity-salt")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output"))
    monkeypatch.setenv("SEED_STATUS_FILE", str(tmp_path / "seed-status"))
    return _stager().stage_seed_bundle(path, tmp_path / "staged")


@pytest.mark.parametrize("bench_id", ["tpch", "tpcds"])
def test_nonstandard_combined_stages_without_a_ranked_lane(tmp_path, monkeypatch, bench_id):
    data = _bundle(Case("nonstandard", bench_id, 2.0, "combined", True, (), None))
    path = _write(tmp_path / "source", "result.json", data)
    assert _staged(tmp_path, path, monkeypatch)
    staged = json.loads((tmp_path / "staged" / path.name).read_text(encoding="utf-8"))
    validator = _validator()
    assert validator.admission_reason(staged) is None
    assert validator.bundle_lanes(staged) == ()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_every_path_admits_the_same_results_into_the_same_lane_with_the_same_label(
    case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _bundle(case)
    path = _write(tmp_path / "public", "result.json", data)
    trusted_path = _write(tmp_path / "trusted", "result.json", data, internal=True)
    validator = _validator()

    outcome = corpus_rule.derive_outcome(corpus_rule.facts_from_result(data))
    assert outcome.public_admission is case.public
    assert outcome.maintainer_admission is True
    assert outcome.lanes == case.lanes
    assert (case.label is None) or any(label.startswith(case.label) for label in outcome.labels), outcome.labels

    assert classify_result_path(path) is (
        SubmitTerminalState.submittable if case.public else SubmitTerminalState.unofficial
    )
    assert _publish_admitted(path) is case.public
    public_errors = []
    if not case.public:
        public_errors.append(
            f"benchmark scale_factor={case.scale} is not accepted for public submissions (subscale); "
            "only results at scale factor 1 or above that were not routed through a gateway may be submitted"
        )
    if case.benchmark == "ssb":
        public_errors.append(
            "benchmark 'ssb' covers 0 of 13 canonical queries "
            "(missing: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, … (+1 more)); "
            "partial runs remain local artifacts unless validated through the trusted mirror path"
        )
    assert _gate_errors(path, trusted=False) == public_errors
    assert _gate_errors(trusted_path, trusted=True) == []

    assert _staged(tmp_path, path, monkeypatch) is True
    assert validator.admission_reason(data, path) is None
    assert validator.bundle_lanes(data, path) == case.lanes
    assert validator.bundle_rankable(data, path) is bool(case.lanes)
    assert validator.rule_outcome(data, path).labels == outcome.labels


@pytest.mark.parametrize("case", [case for case in CASES if case.lanes], ids=lambda case: case.name)
def test_the_stamp_never_moves_a_result_between_paths(case: Case, tmp_path: Path) -> None:
    validator = _validator()
    for stamp in ("official", "unofficial_subscale", "unofficial_nonstandard"):
        data = _bundle(case)
        data["benchmark"]["compliance_class"] = stamp
        path = _write(tmp_path / stamp, "result.json", data)
        assert validator.bundle_lanes(data, path) == case.lanes, stamp
        errors = _gate_errors(path, trusted=False)
        assert any(PUBLIC_GATE_MESSAGE in error for error in errors) is (not case.public), stamp


@pytest.mark.parametrize("gateway", [{"routed": True}, {"routed": "yes"}], ids=["unnamed", "truthy"])
def test_a_gateway_routed_result_is_refused_publicly_on_every_public_path(tmp_path: Path, monkeypatch, gateway) -> None:
    data = _bundle(CASES[0])
    data["platform"]["gateway"] = gateway
    path = _write(tmp_path / "public", "result.json", data)
    trusted = _write(tmp_path / "trusted", "result.json", data, internal=True)
    gateway_error = "gateway-routed results must declare normalized_cost.cost_status 'not_comparable_gateway'; got None"
    assert classify_result_path(path) is SubmitTerminalState.unofficial
    assert _publish_admitted(path) is False
    assert _gate_errors(path, trusted=False) == [
        "benchmark scale_factor=1.0 is not accepted for public submissions (gateway_routed); "
        "only results at scale factor 1 or above that were not routed through a gateway may be submitted",
        gateway_error,
    ]
    assert _gate_errors(trusted, trusted=True) == [gateway_error]
    assert _staged(tmp_path, path, monkeypatch) is False
    validator = _validator()
    assert validator.admission_reason(data, path) == "gateway_routed"
    assert validator.bundle_lanes(data, path) == ()
    assert "Not ranked: routed through a gateway" in validator.rule_outcome(data, path).labels


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "null"])
def test_missing_phases_are_refused_on_every_path(tmp_path: Path, monkeypatch, missing: bool) -> None:
    data = _bundle(CASES[0])
    if missing:
        del data["phases"]
    else:
        data["phases"] = None
    path = _write(tmp_path / "public", "result.json", data)
    trusted = _write(tmp_path / "trusted", "result.json", data, internal=True)
    outcome = corpus_rule.derive_outcome(corpus_rule.facts_from_result(data))
    assert outcome.class_name == corpus_rule.CLASS_UNDETERMINED
    assert outcome.missing_facts == ("phases",)
    assert classify_result_path(path) is SubmitTerminalState.schema_violation
    assert not _publish_admitted(path)
    assert "result admission is undetermined: missing phases" in _gate_errors(path, trusted=False)
    assert "result admission is undetermined: missing phases" in _gate_errors(trusted, trusted=True)
    assert not _staged(tmp_path, path, monkeypatch)
    assert _validator().admission_reason(data, path) == "not_admitted_undetermined"


def test_failed_validation_is_refused_on_every_path(tmp_path, monkeypatch):
    data = _bundle(CASES[0])
    data["summary"]["validation"] = "failed"
    path = _write(tmp_path / "public", "result.json", data)
    trusted = _write(tmp_path / "trusted", "result.json", data, internal=True)
    assert classify_result_path(path) is SubmitTerminalState.schema_violation
    assert _publish_admitted(path) is False
    assert _gate_errors(path, trusted=False) == [
        "summary.validation must be 'passed' for public submissions (or 'no_reference' when no row-count "
        "reference exists), got 'failed'"
    ]
    assert _gate_errors(trusted, trusted=True) == [
        "summary.validation must be one of ['no_reference', 'not_run', 'partial', 'passed'] "
        "for trusted mirror validation, got 'failed'"
    ]
    assert _staged(tmp_path, path, monkeypatch) is False
    validator = _validator()
    assert validator.admission_reason(data, path) == "validation_not_clean"
    assert validator.bundle_lanes(data, path) == ()
    assert "Not ranked: validation did not pass" in validator.rule_outcome(data, path).labels


@pytest.mark.parametrize("scale,shape,admitted", [(1.0, "power", False), (10.0, "throughput", True)])
def test_no_reference_claims_match_every_path(tmp_path, monkeypatch, scale, shape, admitted):
    data = _bundle(Case("no-reference", "tpch", scale, shape, admitted, (), None))
    data["summary"]["validation"] = "no_reference"
    for query in data["queries"]:
        query["row_count_validation"] = {
            "status": "SKIPPED",
            "expected": None,
            "actual": query["rows"],
            "warning": "no row-count reference at this scale factor",
        }
    path = _write(tmp_path / "public", "result.json", data)
    trusted = _write(tmp_path / "trusted", "result.json", data, internal=True)
    errors = (
        []
        if admitted
        else [
            "summary.validation='no_reference' is only valid where no row-count reference exists, "
            "but a reference is recorded for benchmark 'tpch' at scale factor 1; "
            "record 'passed' with per-query evidence"
        ]
    )
    assert classify_result_path(path) is (
        SubmitTerminalState.submittable if admitted else SubmitTerminalState.unvalidated
    )
    assert _publish_admitted(path) is admitted
    assert _gate_errors(path, trusted=False) == errors
    assert _gate_errors(trusted, trusted=True) == errors
    assert _staged(tmp_path, path, monkeypatch) is admitted
    validator = _validator()
    assert (validator.admission_reason(data, path) is None) is admitted
    assert validator.bundle_lanes(data, path) == ((corpus_rule.LANE_THROUGHPUT,) if admitted else ())
    assert "Row counts not checked" in validator.rule_outcome(data, path).labels


def test_known_defect_is_admitted_but_not_ranked(tmp_path, monkeypatch):
    data = _bundle(CASES[0])
    path = _write(tmp_path / "public", "result.json", data, internal=True)
    manifest_path = path.with_name("result.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["known_defects"] = ["defective-macdbgen-addresses"]
    manifest_path.write_text(json.dumps(manifest))
    validator = _validator()
    assert classify_result_path(path) is SubmitTerminalState.submittable
    assert _publish_admitted(path) is True
    assert _gate_errors(path, trusted=False) == []
    assert _gate_errors(path, trusted=True) == []
    assert _staged(tmp_path, path, monkeypatch) is True
    assert validator.admission_reason(data, path) is None
    assert validator.bundle_lanes(data, path) == ()
    assert validator.exclusion_reason(data, path) == "known_defective_data"
    assert "Not ranked: known defect" in validator.rule_outcome(data, path).labels


def test_a_bundle_the_rule_does_not_admit_to_the_maintainer_path_is_refused_by_admission(tmp_path: Path) -> None:
    validator = _validator()
    undetermined = _bundle(CASES[0])
    undetermined.pop("phases")
    assert validator.admission_reason(undetermined, _write(tmp_path / "a", "result.json", undetermined)) is not None

    tpcdi = _bundle(CASES[0])
    tpcdi["benchmark"]["id"] = "tpcdi"
    assert validator.admission_reason(tpcdi, _write(tmp_path / "b", "result.json", tpcdi)) is not None
    outcome = corpus_rule.derive_outcome(corpus_rule.facts_from_result(tpcdi))
    assert outcome.maintainer_admission is False
