from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
NIGHTLY = REPO_ROOT / ".github/workflows/nightly-v2.yml"
PR_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


def _job_block(workflow: str, job: str, next_job: str) -> str:
    start = workflow.index(f"  {job}:\n")
    end = workflow.index(f"  {next_job}:\n", start)
    return workflow[start:end]


def test_generator_is_nightly_only_and_preserves_required_pr_workflow() -> None:
    nightly = NIGHTLY.read_text(encoding="utf-8")
    pr_workflow = PR_WORKFLOW.read_text(encoding="utf-8")

    assert "  sqlglot-generator:\n" in nightly
    assert "_project/sqlglot-upstream/repros/generator.py" in nightly
    assert "_project/sqlglot-upstream/repros/generator.py" not in pr_workflow


def test_policy_discovery_and_infrastructure_failures_stay_blocking() -> None:
    workflow = NIGHTLY.read_text(encoding="utf-8")
    job = _job_block(workflow, "sqlglot-generator", "report")

    guard = job.index("scripts/check_sqlglot_generator_known_failures.py")
    generator = job.index("_project/sqlglot-upstream/repros/generator.py")
    advisory = job.index('if [ "${status}" -eq 1 ]')
    evidence_validation = job.index("--validate-advisory-evidence")
    infrastructure = job.index('if [ "${status}" -ne 0 ]')
    discovery_branch = job[advisory:infrastructure]

    assert guard < generator < advisory < evidence_validation < infrastructure
    assert "continue-on-error" not in job
    assert "exit 0" not in discovery_branch
    assert 'exit "${status}"' in discovery_branch
    assert 'exit "${status}"' in job


def test_nightly_pilot_is_bounded_and_uploads_replay_evidence() -> None:
    workflow = NIGHTLY.read_text(encoding="utf-8")
    job = _job_block(workflow, "sqlglot-generator", "report")

    assert "timeout-minutes: 10" in job
    assert "--cases 1024" in job
    assert "--deadline-seconds 300" in job
    assert '--seed "${GITHUB_RUN_ID}"' in job
    assert "sqlglot-generator-summary.json" in job
    assert "sqlglot-generator-failure.json" in job


def test_ruleset_drift_runs_trusted_develop_code_with_the_token() -> None:
    workflow = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))
    job = workflow["jobs"]["ruleset-drift"]

    assert "needs" not in job
    assert "continue-on-error" not in job
    checkout = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "develop"
    assert checkout["with"]["persist-credentials"] is False
