from __future__ import annotations

import json
import sys
from pathlib import Path

import check_mirror_validator_parity as guard
import corpus_drift
import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]

RULE_FILES = (
    "benchbox/core/results/corpus_rule.py",
    "benchbox/core/results/corpus_rule_data.json",
    "benchbox/core/results/corpus_score.py",
    "benchbox/core/results/comparison_identity.py",
    "benchbox/core/results/comparison_identity.json",
)
VALIDATE_WORKFLOW = guard.CHECKOUT_ROOT / ".github" / "workflows" / "validate-submission.yml"
REJECT_STEP = "Reject validator or workflow changes in a submission PR"


def _rejected_paths() -> set[str]:
    steps = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["validate"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == REJECT_STEP)
    start = script.index("-- \\\n") + len("-- \\\n")
    end = script.index("|| true", start)
    return {line.strip().rstrip("\\").strip() for line in script[start:end].splitlines() if line.strip()}


def _bundles(tmp_path: Path, count: int = 40) -> Path:
    source = sorted(path for path in guard.BUNDLES_DIR.glob("*.json") if ".manifest" not in path.name)
    chosen = [path for path in source if path.name.startswith(("tpch_sf1_", "tpcds_sf1_"))][:count]
    assert chosen
    target = tmp_path / "bundles"
    target.mkdir()
    for path in chosen:
        (target / path.name).write_bytes(path.read_bytes())
    return target


def _slim(tmp_path: Path) -> Path:
    slim = tmp_path / "slim"
    guard.build_slim_checkout(slim)
    return slim


def test_the_mirrored_file_list_is_the_canary_file_list_and_holds_every_rule_file() -> None:
    mirrored = set(guard.mirrored_runtime_paths())
    assert set(RULE_FILES) <= mirrored
    assert mirrored == {path for path in corpus_drift.CANARY_PATHSPECS if path.endswith((".py", ".json", ".txt"))}


def test_every_mirrored_runtime_file_and_the_workflow_itself_are_on_the_reject_list() -> None:
    assert _rejected_paths() == {*guard.mirrored_runtime_paths(), ".github/workflows/validate-submission.yml"}


def test_the_two_validators_agree_on_the_stored_corpus(capsys: pytest.CaptureFixture[str]) -> None:
    assert guard.main([]) == 0
    assert "agree on all" in capsys.readouterr().out


def test_the_mirror_runs_with_the_standard_library_only(tmp_path: Path) -> None:
    report = guard.mirror_report(_slim(tmp_path), _bundles(tmp_path), sys.executable)
    assert report["installed_third_party"] == []
    assert report["benchbox_package_loaded"] is False
    assert report["outcomes"]


def test_a_mirror_whose_rule_data_differs_is_reported_per_bundle(tmp_path: Path) -> None:
    bundles = _bundles(tmp_path)
    slim = _slim(tmp_path)
    data_path = slim / "benchbox" / "core" / "results" / "corpus_rule_data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["scales"]["tpch"]["standard"] = [value for value in data["scales"]["tpch"]["standard"] if value != 1.0]
    data_path.write_text(json.dumps(data), encoding="utf-8")

    problems = guard.differences(
        guard.mirror_report(slim, bundles, sys.executable)["outcomes"],
        guard.develop_report(bundles, sys.executable)["outcomes"],
    )

    assert any(": class " in problem for problem in problems), problems


def test_a_mirror_validator_that_falls_back_when_the_package_is_absent_is_reported(tmp_path: Path) -> None:
    bundles = _bundles(tmp_path, count=200)
    slim = _slim(tmp_path)
    bundle_py = slim / "benchbox" / "validation" / "bundle.py"
    text = bundle_py.read_text(encoding="utf-8")
    seeded = text.replace(
        "MIN_THROUGHPUT_STREAMS = _corpus_rule.min_throughput_streams()",
        "try:\n    from benchbox.core.schemas import MIN_THROUGHPUT_STREAMS\nexcept ImportError:\n    MIN_THROUGHPUT_STREAMS = 99",
    )
    assert seeded != text
    bundle_py.write_text(seeded, encoding="utf-8")

    problems = guard.differences(
        guard.mirror_report(slim, bundles, sys.executable)["outcomes"],
        guard.develop_report(bundles, sys.executable)["outcomes"],
    )

    assert any("throughput requires at least 99 streams" in problem for problem in problems), problems


def test_a_rule_file_missing_from_the_mirror_stops_the_guard(tmp_path: Path) -> None:
    slim = _slim(tmp_path)
    (slim / "benchbox" / "core" / "results" / "corpus_score.py").unlink()

    with pytest.raises(RuntimeError, match="probe failed"):
        guard.mirror_report(slim, _bundles(tmp_path), sys.executable)


def test_an_unlisted_runtime_file_is_not_copied_to_the_mirror(tmp_path: Path) -> None:
    slim = _slim(tmp_path)

    assert not (slim / "benchbox" / "__init__.py").exists()
    assert sorted(path.relative_to(slim).as_posix() for path in slim.rglob("*") if path.is_file()) == sorted(
        guard.mirrored_runtime_paths()
    )
