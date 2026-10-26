from __future__ import annotations

import shlex
import tempfile
from pathlib import Path

import pytest
import yaml

from _project.scripts.oracle_reviewers import cli

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_workflow_scans_include_leaks_outside_home_and_runner_temp(tmp_path, monkeypatch) -> None:
    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/oracle-verdict.yml"
    steps = yaml.safe_load(workflow.read_text())["jobs"]["review"]["steps"]
    scans = [step for step in steps if "cli scan-disk" in step.get("run", "")]
    secret = "scratch-fixture-secret-value"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="oracle-scratch-leak-") as directory:
        root = Path(directory)
        (root / "response.txt").write_text(secret)
        assert not root.is_relative_to(tmp_path)
        for step in scans:
            words = shlex.split(step["run"])
            roots = [words[index + 1] for index, word in enumerate(words) if word == "--root"]
            assert "/tmp" in roots
            assert cli.main(["scan-disk", "--root", str(root), "--env", "TYPESAFE_API_KEY"]) == 1
