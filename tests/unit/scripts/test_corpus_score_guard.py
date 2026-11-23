from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import corpus_compliance_snapshot as script
import pytest

from benchbox.core.results import corpus_rule, corpus_score
from benchbox.validation.bundle import COMPANION_SUFFIXES

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = script.CHECKOUT_ROOT
ROOTS = {
    "corpus": REPO_ROOT / "results-data" / "bundles",
    "fixtures": REPO_ROOT / "tests" / "fixtures" / "explorer_browser" / "source" / "bundles",
    "combined": REPO_ROOT / "tests" / "fixtures" / "corpus_score_guard" / "combined" / "bundles",
}
CLASS_VALUES = ("official", "unofficial_subscale", "unofficial_nonstandard")
TEST_TYPES = ("power", "throughput", "combined", "standard")

Mutation = Callable[[dict[str, Any]], None]


def _other(current: Any, values: tuple[str, ...]) -> str:
    return next(value for value in values if value != current)


def _section(data: dict[str, Any], *path: str) -> dict[str, Any]:
    node = data
    for key in path:
        node = node.setdefault(key, {})
    return node


def _change_compliance_class(data: dict[str, Any]) -> None:
    benchmark = _section(data, "benchmark")
    benchmark["compliance_class"] = _other(benchmark.get("compliance_class"), CLASS_VALUES)


def _remove_compliance_class(data: dict[str, Any]) -> None:
    _section(data, "benchmark").pop("compliance_class", None)


def _change_conformance_class(data: dict[str, Any]) -> None:
    benchmark = _section(data, "benchmark")
    benchmark["conformance_class"] = _other(benchmark.get("conformance_class"), CLASS_VALUES)


def _remove_conformance_class(data: dict[str, Any]) -> None:
    _section(data, "benchmark").pop("conformance_class", None)


def _change_tpc_metrics_to_suppressed(data: dict[str, Any]) -> None:
    _section(data, "summary")["tpc_metrics"] = {"suppressed": True, "reason": "compliance_class=unofficial_nonstandard"}


def _change_tpc_metrics_to_other_scores(data: dict[str, Any]) -> None:
    _section(data, "summary")["tpc_metrics"] = {
        "power_at_size": 1.0,
        "throughput_at_size": 1.0,
        "qphh_at_size": 1.0,
        "qphds_at_size": 1.0,
        "refresh_included": False,
        "unofficial": True,
    }


def _remove_tpc_metrics(data: dict[str, Any]) -> None:
    _section(data, "summary").pop("tpc_metrics", None)


def _change_execution_official(data: dict[str, Any]) -> None:
    execution = _section(data, "execution")
    execution["official"] = not execution.get("official", False)


def _remove_execution_official(data: dict[str, Any]) -> None:
    _section(data, "execution").pop("official", None)


def _change_recorded_power(data: dict[str, Any]) -> None:
    block = (data.get("phases") or {}).get("power_test")
    if isinstance(block, dict):
        block["power_at_size"] = (block.get("power_at_size") or 1.0) * 3.0


def _remove_recorded_power(data: dict[str, Any]) -> None:
    block = (data.get("phases") or {}).get("power_test")
    if isinstance(block, dict):
        block.pop("power_at_size", None)


def _change_test_type(data: dict[str, Any]) -> None:
    benchmark = _section(data, "benchmark")
    benchmark["test_type"] = _other(benchmark.get("test_type"), TEST_TYPES)


def _remove_test_type(data: dict[str, Any]) -> None:
    _section(data, "benchmark").pop("test_type", None)


def _validation_with_class_fields(data: dict[str, Any]) -> None:
    summary = _section(data, "summary")
    recorded = summary.get("validation")
    status = recorded.get("status") if isinstance(recorded, dict) else recorded
    summary["validation"] = {
        "status": status,
        "compliance_class": _other(None, CLASS_VALUES),
        "conformance_class": "unofficial_nonstandard",
        "class": "official",
    }


def _validation_without_class_fields(data: dict[str, Any]) -> None:
    summary = _section(data, "summary")
    recorded = summary.get("validation")
    if isinstance(recorded, dict):
        summary["validation"] = recorded.get("status")


MUTATIONS: dict[str, Mutation] = {
    "benchmark.compliance_class changed": _change_compliance_class,
    "benchmark.compliance_class removed": _remove_compliance_class,
    "benchmark.conformance_class changed": _change_conformance_class,
    "benchmark.conformance_class removed": _remove_conformance_class,
    "summary.tpc_metrics suppressed": _change_tpc_metrics_to_suppressed,
    "summary.tpc_metrics other scores": _change_tpc_metrics_to_other_scores,
    "summary.tpc_metrics removed": _remove_tpc_metrics,
    "execution.official changed": _change_execution_official,
    "execution.official removed": _remove_execution_official,
    "phases.power_test.power_at_size changed": _change_recorded_power,
    "phases.power_test.power_at_size removed": _remove_recorded_power,
    "benchmark.test_type changed": _change_test_type,
    "benchmark.test_type removed": _remove_test_type,
    "summary.validation class fields added": _validation_with_class_fields,
    "summary.validation class fields removed": _validation_without_class_fields,
}


def _result_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.json"))
        if not path.name.endswith(COMPANION_SUFFIXES) and path.name != "submission-manifest.json"
    ]


def _mutated_copy(root: Path, target: Path, mutation: Mutation) -> Path:
    shutil.copytree(root, target)
    for path in _result_files(target):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "benchmark" in data:
            mutation(data)
            path.write_text(json.dumps(data), encoding="utf-8")
    return target


def _rule_outcomes(snapshot: script.Snapshot) -> dict[str, Any]:
    return {name: entry["rule"] for name, entry in snapshot.items()}


@pytest.fixture(scope="module", params=sorted(ROOTS))
def baseline(request: pytest.FixtureRequest) -> tuple[Path, dict[str, Any]]:
    root = ROOTS[request.param]
    return root, _rule_outcomes(script.compute_snapshot(root))


@pytest.mark.parametrize("label", sorted(MUTATIONS))
def test_class_lane_score_and_rank_ignore_every_recorded_field(
    baseline: tuple[Path, dict[str, Any]], label: str, tmp_path: Path
) -> None:
    root, expected = baseline
    mutated = _mutated_copy(root, tmp_path / "bundles", MUTATIONS[label])

    actual = _rule_outcomes(script.compute_snapshot(mutated))

    changed = sorted(name for name in expected if expected[name] != actual.get(name))
    assert changed == [], f"{label} moved the outcome of {len(changed)} result(s): {changed[:5]}"


def _validator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "validate_corpus_score_guard", REPO_ROOT / "results-data" / "validate_corpus.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("label", sorted(MUTATIONS))
@pytest.mark.parametrize("root_name", sorted(ROOTS))
def test_validator_scores_ignore_every_recorded_field(root_name: str, label: str) -> None:
    validator = _validator()
    for path in _result_files(ROOTS[root_name]):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "benchmark" not in data:
            continue
        mutated = copy.deepcopy(data)
        MUTATIONS[label](mutated)
        assert validator._power_score(mutated) == validator._power_score(data), path.name
        assert validator._throughput_score(mutated) == validator._throughput_score(data), path.name
        assert validator._combined_score(mutated) == validator._combined_score(data), path.name


@pytest.mark.parametrize("label", sorted(MUTATIONS))
@pytest.mark.parametrize("root_name", sorted(ROOTS))
def test_score_functions_ignore_every_recorded_field(root_name: str, label: str) -> None:
    for path in _result_files(ROOTS[root_name]):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "benchmark" not in data:
            continue
        mutated = copy.deepcopy(data)
        MUTATIONS[label](mutated)
        assert corpus_score.power_at_size(mutated) == corpus_score.power_at_size(data), path.name
        assert corpus_score.throughput_at_size(mutated) == corpus_score.throughput_at_size(data), path.name
        assert corpus_score.combined_score(mutated) == corpus_score.combined_score(data), path.name


def test_the_combined_fixtures_have_a_score_for_every_result() -> None:
    scores = {
        path.name: corpus_score.combined_score(json.loads(path.read_text(encoding="utf-8")))
        for path in _result_files(ROOTS["combined"])
    }

    assert len(scores) == 4
    assert all(score is not None and score > 0 for score in scores.values()), scores


def _throughput_fixture() -> tuple[Path, dict[str, Any]]:
    snapshot = script.load_snapshot(REPO_ROOT / script.SNAPSHOT_PATH)
    name = next(
        name for name, entry in snapshot.items() if entry["rule"].get("class") == corpus_rule.CLASS_THROUGHPUT_ONLY
    )
    path = ROOTS["corpus"] / name
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_stream_count_comes_from_stream_results_not_run_streams(tmp_path: Path) -> None:
    path, data = _throughput_fixture()
    executed = corpus_rule.executed_stream_count(data)
    assert executed is not None
    data["run"]["streams"] = executed + 5
    data.setdefault("config", {})["streams"] = executed + 7

    assert corpus_rule.executed_stream_count(data) == executed
    assert corpus_rule.facts_from_result(data).streams == executed

    root = tmp_path / "bundles"
    target = root / path.relative_to(ROOTS["corpus"])
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(data), encoding="utf-8")
    entry = script.compute_snapshot(root)[path.relative_to(ROOTS["corpus"]).as_posix()]
    assert entry["rule"]["class"] == corpus_rule.CLASS_THROUGHPUT_ONLY
    assert entry["rule"]["ranks"][corpus_rule.LANE_THROUGHPUT]["cohort"].endswith(f"/streams={executed}")


def test_run_streams_is_never_read_for_the_class() -> None:
    _, data = _throughput_fixture()
    baseline_facts = corpus_rule.facts_from_result(data)
    for configured in (0, 1, 2, 17):
        changed = copy.deepcopy(data)
        changed["run"]["streams"] = configured
        assert corpus_rule.facts_from_result(changed) == baseline_facts
        assert corpus_rule.derive_outcome(corpus_rule.facts_from_result(changed)) == corpus_rule.derive_outcome(
            baseline_facts
        )
