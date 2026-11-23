from __future__ import annotations

import copy
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
    data["benchmark"]["scale_factor"] = case.scale
    data["export"] = {"anonymized": True}
    return data


def _write(directory: Path, name: str, data: dict[str, Any], *, internal: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    if internal:
        path.with_name(f"{path.stem}.manifest.json").write_text(
            json.dumps({"result_source": "internal", "bundle_file": path.name}), encoding="utf-8"
        )
    return path


def _gate_outcome(path: Path, *, trusted: bool) -> tuple[bool, list[str]]:
    (result,) = validate_bundles([path], trust_internal_source=trusted)
    admitted = not any(PUBLIC_GATE_MESSAGE in error for error in result.errors)
    warnings = [warning for warning in result.warnings if warning.startswith(NONSTANDARD_WARNING)]
    return admitted, warnings


def _submit_admitted(path: Path) -> bool:
    return classify_result_path(path) is not SubmitTerminalState.unofficial


def _publish_admitted(path: Path) -> bool:
    result, _raw = load_result_file(path)
    return publish_admission(result, "community").allowed


def _staged(tmp_path: Path, path: Path, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "parity-salt")
    return _stager().stage_seed_bundle(path, tmp_path / "staged")


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

    assert _submit_admitted(path) is case.public
    assert _publish_admitted(path) is case.public
    public_admitted, public_warnings = _gate_outcome(path, trusted=False)
    assert public_admitted is case.public
    trusted_admitted, trusted_warnings = _gate_outcome(trusted_path, trusted=True)
    assert trusted_admitted is True

    assert _staged(tmp_path, path, monkeypatch) is True
    assert validator.admission_reason(data, path) is None
    assert validator.bundle_lanes(data, path) == case.lanes
    assert validator.bundle_rankable(data, path) is bool(case.lanes)

    unranked_label = case.label == corpus_rule.NONSTANDARD_SCALE_LABEL
    if case.public:
        assert bool(public_warnings) is unranked_label
    assert bool(trusted_warnings) is unranked_label


@pytest.mark.parametrize("case", [case for case in CASES if case.lanes], ids=lambda case: case.name)
def test_the_stamp_never_moves_a_result_between_paths(case: Case, tmp_path: Path) -> None:
    validator = _validator()
    for stamp in ("official", "unofficial_subscale", "unofficial_nonstandard"):
        data = _bundle(case)
        data["benchmark"]["compliance_class"] = stamp
        path = _write(tmp_path / stamp, "result.json", data)
        assert validator.bundle_lanes(data, path) == case.lanes, stamp
        assert _gate_outcome(path, trusted=False)[0] is case.public, stamp


def test_a_gateway_routed_result_is_refused_publicly_on_every_public_path(tmp_path: Path) -> None:
    data = _bundle(CASES[0])
    data["platform"]["gateway"] = {"name": "proxy", "routed": True}
    path = _write(tmp_path, "result.json", data)

    assert _submit_admitted(path) is False
    assert _publish_admitted(path) is False
    assert _gate_outcome(path, trusted=False)[0] is False
    assert _validator().admission_reason(data, path) == "gateway_routed"
