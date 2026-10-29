from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .harness import REPO_ROOT, Harness, Step
from .runs import SCAN_COMMAND, scan_step, step_by_name

pytestmark = [pytest.mark.integration, pytest.mark.medium]

ISOLATION_STEP = "Verify Linux process isolation"
JOIN_SCAN_STEP = "Scan the disk for secret values after the tailnet join"
REQUEST_TOKEN = "eyJhbGciOi" + "Pq7Rs8Tu" * 6
OAUTH_SECRET = "tskey-client-" + "kXq7Lm2NpR4tVw9-Zb3Yc8Wd5Xe"


def _run_step(harness: Harness, name: str, extra: dict[str, str]) -> Step:
    temp = harness.root / "step"
    temp.mkdir(exist_ok=True)
    workspace = temp / "workspace"
    (workspace / "trusted").mkdir(parents=True, exist_ok=True)
    env = {
        **harness.base_env(temp),
        "PATH": f"{Path(sys.executable).parent}:{harness.base_env(temp)['PATH']}",
        "PYTHONPATH": str(REPO_ROOT),
        "GITHUB_WORKSPACE": str(workspace),
        "TMPDIR": str(temp / "tmp"),
        **extra,
    }
    (temp / "tmp").mkdir(exist_ok=True)
    command = step_by_name(name)["run"]
    if name == JOIN_SCAN_STEP:
        assert command == SCAN_COMMAND
        return scan_step(harness, name, env)
    return harness.run(name, ["bash", "-e", "-c", command], env, cwd=REPO_ROOT)


def test_a_job_holding_the_oidc_request_token_fails_the_isolation_step_before_any_reviewer_runs(
    harness: Harness,
) -> None:
    step = _run_step(harness, ISOLATION_STEP, {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_TOKEN})

    assert step.code == 1
    assert "isolation prerequisite failed: ACTIONS_ID_TOKEN_REQUEST_TOKEN must not be present" in step.stderr
    assert REQUEST_TOKEN not in step.stderr + step.stdout
    assert harness.omp_calls() == 0


def test_the_scan_after_the_tailnet_join_fails_when_the_oauth_secret_is_on_disk(harness: Harness) -> None:
    temp = harness.root / "step"
    temp.mkdir(exist_ok=True)
    (temp / "tailscaled.log").write_text(f"up --authkey={OAUTH_SECRET}\n", encoding="utf-8")

    step = _run_step(harness, JOIN_SCAN_STEP, {"TS_OAUTH_CI_SECRET": OAUTH_SECRET, "HOME": str(harness.root / "none")})

    assert step.code == 1
    assert "the value of TS_OAUTH_CI_SECRET is on disk at" in step.stderr
    assert OAUTH_SECRET not in step.stderr + step.stdout


def test_the_scan_after_the_tailnet_join_passes_when_the_oauth_secret_is_not_on_disk(harness: Harness) -> None:
    step = _run_step(harness, JOIN_SCAN_STEP, {"TS_OAUTH_CI_SECRET": OAUTH_SECRET, "HOME": str(harness.root / "none")})

    assert step.code == 0, step.stderr
