from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-v2.yml"
HEAD_SHA = "a" * 40


def _decide_run() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return next(step for step in jobs["uat-reuse"]["steps"] if step.get("id") == "decide")["run"]


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "release_reuse_uat_evidence.py", root / "scripts")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.0.0"\nrequires-python = ">=3.11"\ndependencies = []\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


def _env(tmp_path: Path, path: str) -> dict[str, str]:
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    return {
        **os.environ,
        "PATH": path,
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
        "RELEASE_TAG": "v0.5.0",
        "HEAD_SHA": HEAD_SHA,
        "GH_TOKEN": "unused",
        "GITHUB_REPOSITORY": "owner/repo",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary"),
    }


def _run_step(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    (checkout / "step.sh").write_text(_decide_run(), encoding="utf-8", newline="\n")
    return subprocess.run(
        [bash, "step.sh"], cwd=checkout, env=env, capture_output=True, text=True, timeout=240, check=False
    )


@pytest.mark.skipif(shutil.which("uv") is None or shutil.which("bash") is None, reason="needs uv and bash")
def test_the_decide_step_command_runs_under_the_real_uv_and_reaches_the_decision(checkout, tmp_path):
    lock = subprocess.run(
        ["uv", "lock", "--offline"],
        cwd=checkout,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_PYTHON_DOWNLOADS": "never"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert lock.returncode == 0, lock.stderr
    env = _env(tmp_path, os.environ["PATH"])

    result = _run_step(checkout, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unexpected argument" not in result.stderr
    assert "::warning::" not in result.stdout
    assert Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "reuse=false\n"
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert "| trunk line | FAIL |" in summary
    assert "| release candidate | FAIL |" in summary


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_reuse_decision_that_cannot_run_becomes_a_warned_full_uat_run_not_a_failed_job(checkout, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\necho \"error: unexpected argument '--no-install-project' found\" >&2\nexit 2\n")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    env = _env(tmp_path, f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = _run_step(checkout, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::The reuse decision could not run (exit status 2)" in result.stdout
    assert Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "reuse=false\n"
    assert "could not run (exit status 2)" in Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
