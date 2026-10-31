from __future__ import annotations

import argparse
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from _project.scripts.oracle_reviewers import driver

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_workflow_scans_include_leaks_outside_home_and_runner_temp(tmp_path, monkeypatch) -> None:
    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/oracle-verdict.yml"
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
    scans = [step for step in steps if "cli drive scan" in step.get("run", "")]
    assert len(scans) == 3
    secret = "scratch-fixture-secret-value"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    environment = driver.CiEnvironment.from_environ(
        {"RUNNER_TEMP": str(tmp_path), "GITHUB_WORKSPACE": str(tmp_path), "HOME": str(tmp_path / "home")},
        tmp_path / "policy.yml",
    )
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="oracle-scratch-leak-") as directory:
        root = Path(directory)
        (root / "response.txt").write_text(secret)
        assert not root.is_relative_to(tmp_path)
        assert Path("/tmp") in environment.scan_roots
        assert driver.scan(replace(environment, scan_roots=(root,)), argparse.Namespace()) == 1
