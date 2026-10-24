from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required to exercise no-project validator execution"),
]


def _minimal_bundle() -> dict:
    return {
        "version": "2.1",
        "export": {"anonymized": True},
        "run": {
            "id": "abc123",
            "timestamp": "2026-04-01T12:00:00",
            "total_duration_ms": 5000,
        },
        "benchmark": {
            "id": "tpch",
            "name": "TPC-H",
            "scale_factor": 0.01,
        },
        "platform": {
            "name": "DuckDB",
            "version": "1.4.3",
        },
        "summary": {
            "validation": "passed",
            "queries": {"total": 22, "passed": 22, "failed": 0},
        },
        "queries": [{"id": f"Q{i}", "ms": 100 + i, "status": "SUCCESS"} for i in range(1, 23)],
    }


def _standalone_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def _copy_slim_validator(repo_root: Path, slim_root: Path) -> None:
    (slim_root / "scripts").mkdir(parents=True)
    (slim_root / "benchbox" / "validation").mkdir(parents=True)
    (slim_root / "benchbox" / "core" / "results").mkdir(parents=True)
    (slim_root / "results-data" / "bundles").mkdir(parents=True)
    shutil.copy2(repo_root / "scripts" / "validate_submission.py", slim_root / "scripts" / "validate_submission.py")
    shutil.copy2(
        repo_root / "benchbox" / "validation" / "bundle.py", slim_root / "benchbox" / "validation" / "bundle.py"
    )
    shutil.copy2(
        repo_root / "benchbox" / "core" / "results" / "query_status.py",
        slim_root / "benchbox" / "core" / "results" / "query_status.py",
    )
    shutil.copy2(
        repo_root / "benchbox" / "core" / "results" / "timing_fields.py",
        slim_root / "benchbox" / "core" / "results" / "timing_fields.py",
    )
    shutil.copy2(
        repo_root / "benchbox" / "core" / "results" / "schema_policy.py",
        slim_root / "benchbox" / "core" / "results" / "schema_policy.py",
    )


@pytest.fixture
def valid_bundle_file(tmp_path: Path) -> Path:
    p = tmp_path / "tpch_result.json"
    p.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    return p


def test_cli_runs_from_develop_checkout_without_project_install(valid_bundle_file: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--",
            "python",
            "-S",
            "scripts/validate_submission.py",
            str(valid_bundle_file),
        ],
        cwd=repo_root,
        env=_standalone_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "Validated 1 bundle(s): 0 error(s)" in result.stdout


def test_cli_runs_in_slim_no_project_checkout(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    slim_root = tmp_path / "published-results"
    _copy_slim_validator(repo_root, slim_root)

    bundle = slim_root / "results-data" / "bundles" / "tpch_result.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--",
            "python",
            "scripts/validate_submission.py",
            str(bundle.relative_to(slim_root)),
        ],
        cwd=slim_root,
        env=_standalone_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "Validated 1 bundle(s): 0 error(s)" in result.stdout


def test_slim_no_project_checkout_rejects_empty_public_result(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    slim_root = tmp_path / "published-results"
    _copy_slim_validator(repo_root, slim_root)
    payload = _minimal_bundle()
    payload["summary"]["queries"] = {"total": 0, "passed": 0, "failed": 0}
    payload["queries"] = []
    bundle = slim_root / "results-data" / "bundles" / "empty.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--",
            "python",
            "scripts/validate_submission.py",
            str(bundle.relative_to(slim_root)),
        ],
        cwd=slim_root,
        env=_standalone_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "queries array must not be empty" in result.stdout


def test_slim_no_project_checkout_rejects_false_clean_measurement(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    slim_root = tmp_path / "published-results"
    _copy_slim_validator(repo_root, slim_root)
    payload = _minimal_bundle()
    payload["queries"][0]["status"] = "FAILED"
    bundle = slim_root / "results-data" / "bundles" / "false_clean.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--",
            "python",
            "scripts/validate_submission.py",
            str(bundle.relative_to(slim_root)),
        ],
        cwd=slim_root,
        env=_standalone_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "contradicts 1 failed measurement query" in result.stdout
