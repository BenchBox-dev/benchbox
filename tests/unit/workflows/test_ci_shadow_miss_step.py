from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _misses_step_and_checkout() -> tuple[dict[str, Any], dict[str, Any]]:
    steps = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["core"]["steps"]
    index = next(i for i, step in enumerate(steps) if "coverage_test_selection.py misses" in step.get("run", ""))
    checkout = next(
        step
        for step in reversed(steps[:index])
        if "actions/checkout" in step.get("uses", "") and "sparse-checkout" in step.get("with", {})
    )
    return steps[index], checkout


def test_misses_step_runs_from_its_sparse_checkout(tmp_path: Path) -> None:
    _, checkout = _misses_step_and_checkout()
    entries = checkout["with"]["sparse-checkout"].split()
    if any(not (REPO_ROOT / entry).exists() for entry in entries):
        pytest.skip("sparse checkout files are absent from this tree")
    for entry in entries:
        source = REPO_ROOT / entry
        target = tmp_path / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    (tmp_path / "tests/unit/a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests/unit/a/test_one.py").write_text("", encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"selected": ["tests/unit/a/test_one.py::test_in"]}), encoding="utf-8")
    junit = tmp_path / "evidence" / "shard-0-junit.xml"
    junit.parent.mkdir()
    junit.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.unit.a.test_one" name="test_in"><failure/></testcase>'
        '<testcase classname="tests.unit.a.test_one" name="test_out"><failure/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/coverage_test_selection.py",
            "--repo-root",
            str(tmp_path),
            "misses",
            "--selection-in",
            str(selection),
            "--junit",
            str(junit.parent),
            "--expected-shards",
            "1",
            "--summary",
            str(summary),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    text = summary.read_text(encoding="utf-8")
    assert "tests/unit/a/test_one.py::test_out" in text
    assert "- tests/unit/a/test_one.py::test_in" not in text
