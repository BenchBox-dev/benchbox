from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-review-reevaluate.yml"
SCRIPT = "_project/scripts/oracle_review_reevaluate.py"


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _load()["jobs"]["reevaluate"]


def test_runs_only_on_a_schedule_from_trusted_develop_code() -> None:
    workflow = _load()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert set(triggers) == {"schedule"}
    checkout = next(step for step in _job()["steps"] if "actions/checkout" in step.get("uses", ""))
    assert checkout["with"]["ref"] == "develop"
    assert checkout["with"]["persist-credentials"] is False


def test_the_only_write_permission_is_actions() -> None:
    assert _load()["permissions"] == {}
    assert _job()["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    assert "environment" not in _job()
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_sparse_checkout_is_enough_to_import_the_script(tmp_path: Path) -> None:
    import shutil
    import subprocess

    checkout = next(step for step in _job()["steps"] if "actions/checkout" in step.get("uses", ""))
    for entry in checkout["with"]["sparse-checkout"].split():
        source = REPO_ROOT / entry
        target = tmp_path / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    probe = "import sys; sys.path.insert(0, '_project/scripts'); import oracle_review_reevaluate"
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_script_runs_with_the_workflow_token_and_pinned_pyyaml() -> None:
    step = next(step for step in _job()["steps"] if SCRIPT in step.get("run", ""))
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "--with pyyaml==6.0.3" in step["run"]
    names = [step.get("name", "") for step in _job()["steps"]]
    assert names.index("Install uv") < next(i for i, s in enumerate(_job()["steps"]) if SCRIPT in s.get("run", ""))


def test_workflow_has_no_comments() -> None:
    assert not any(line.lstrip().startswith("#") for line in WORKFLOW.read_text(encoding="utf-8").splitlines())
