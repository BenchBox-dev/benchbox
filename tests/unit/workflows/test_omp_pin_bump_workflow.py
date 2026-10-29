from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "omp-pin-bump.yml"


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job() -> dict[str, Any]:
    return load()["jobs"]["bump"]


def opener_job() -> dict[str, Any]:
    return load()["jobs"]["open-pr"]


def steps() -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job()["steps"]}


def opener_steps() -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in opener_job()["steps"]}


def all_steps() -> list[dict[str, Any]]:
    return [*job()["steps"], *opener_job()["steps"]]


def run_text() -> str:
    return "\n".join(str(step.get("run", "")) for step in all_steps())


def test_the_bump_check_runs_daily_and_on_demand_and_only_in_the_main_repository() -> None:
    triggers = load()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert re.fullmatch(r"\d+ \d+ \* \* \*", triggers["schedule"][0]["cron"])
    assert job()["if"] == "github.repository == 'BenchBox-dev/BenchBox'"


def test_the_opener_holds_only_actions_contents_and_pull_request_write_and_the_workflow_defaults_to_read() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert job()["permissions"] == {"contents": "read"}
    assert opener_job()["permissions"] == {"actions": "write", "contents": "write", "pull-requests": "write"}


def test_every_action_is_pinned_to_a_commit_and_develop_is_what_gets_checked_out() -> None:
    for step in all_steps():
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    assert steps()["Checkout develop"]["with"]["ref"] == "develop"
    assert opener_steps()["Checkout develop"]["with"]["ref"] == "develop"


def test_the_preparation_runs_trusted_develop_code_with_only_the_policy_parser_installed() -> None:
    prepare = steps()["Check the latest stable omp build against the pin"]
    command = " ".join(str(prepare["run"]).split())
    assert "--isolated --no-project" in command and "--with pyyaml==6.0.3" in command
    assert "_project.scripts.oracle_reviewers.omp_bump prepare" in command
    assert "--policy .github/oracle-reviewers.yml" in command


def test_a_pull_request_opens_only_for_a_ready_candidate_and_never_twice_for_one_version() -> None:
    assert "needs.bump.outputs.state == 'ready'" in opener_job()["if"]
    assert opener_job()["needs"] == "bump"
    opener = opener_steps()["Open the bump pull request"]
    text = opener["run"]
    assert len(re.findall(r"--method POST \"repos/\$\{GITHUB_REPOSITORY\}/pulls\"", text)) == 1
    assert "pulls?state=all&head=" in text
    assert "-f base=develop" in text and 'BRANCH="chore/omp-pin-${VERSION}"' in text


def test_the_opener_starts_the_review_itself_because_the_job_token_cannot_trigger_pull_request_events() -> None:
    text = opener_steps()["Open the bump pull request"]["run"]
    assert len(re.findall(r"gh workflow run oracle-verdict\.yml", text)) == 1
    assert '--ref develop -f pr="$PR" -f head="$HEAD_SHA"' in text
    assert '.context == "oracle-verdict"' in text and "--paginate" in text
    assert text.index("/statuses") < text.index("gh workflow run")
    assert "workflows/oracle-verdict.yml/runs" in text
    assert text.index("workflows/oracle-verdict.yml/runs") < text.index("gh workflow run")
    assert "ORACLE_APP" not in WORKFLOW.read_text(encoding="utf-8")


def test_the_pull_request_changes_only_the_policy_file_and_nothing_merges_or_pushes_to_develop() -> None:
    text = run_text()
    assert re.findall(r"git add (.+)", text) == [".github/oracle-reviewers.yml"]
    assert "gh pr merge" not in text and "git push origin develop" not in text
    assert 'cp "$OUT_DIR/policy.yml" .github/oracle-reviewers.yml' in text


def test_the_only_secret_the_job_reads_is_the_job_token() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}


def test_a_red_candidate_fails_the_run_and_still_reports_its_suite_output() -> None:
    report = steps()["Report the candidate"]
    assert report["if"] == "always() && steps.prepare.outputs.state != ''"
    assert "detail.txt" in report["run"] and "GITHUB_STEP_SUMMARY" in report["run"]
    assert "continue-on-error" not in steps()["Check the latest stable omp build against the pin"]


def test_no_write_token_is_present_while_the_candidate_build_runs() -> None:
    for step in job()["steps"]:
        assert "secrets.GITHUB_TOKEN" not in str(step) and "github.token" not in str(step), step["name"]
    for step in all_steps():
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    opener = opener_steps()["Open the bump pull request"]
    assert opener["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "omp_bump" not in run_text_of(opener_job())


def run_text_of(target: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in target["steps"])
