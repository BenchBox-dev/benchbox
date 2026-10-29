from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers import omp_watch

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "omp-pin-watch.yml"


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def watch_job() -> dict[str, Any]:
    return load()["jobs"]["watch"]


def opener_job() -> dict[str, Any]:
    return load()["jobs"]["open-revert"]


def steps(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job["steps"]}


def all_steps() -> list[dict[str, Any]]:
    return [*watch_job()["steps"], *opener_job()["steps"]]


def run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_the_watch_runs_every_few_hours_and_on_demand_and_only_in_the_main_repository() -> None:
    triggers = load()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert re.fullmatch(r"\d+ \*/\d+ \* \* \*", triggers["schedule"][0]["cron"])
    assert watch_job()["if"] == "github.repository == 'BenchBox-dev/BenchBox'"
    assert "github.repository == 'BenchBox-dev/BenchBox'" in opener_job()["if"]


def test_the_evaluation_reads_and_only_the_revert_job_holds_write_permissions() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert watch_job()["permissions"] == {"actions": "read", "contents": "read"}
    assert opener_job()["permissions"] == {"actions": "write", "contents": "write", "pull-requests": "write"}


def test_every_action_is_pinned_to_a_commit_and_develop_is_what_gets_checked_out() -> None:
    for step in all_steps():
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["ref"] == "develop" and step["with"]["persist-credentials"] is False
    assert steps(watch_job())["Checkout develop"]["with"]["fetch-depth"] == 0


def test_the_evaluation_runs_trusted_develop_code_with_only_the_policy_parser_installed() -> None:
    command = " ".join(
        str(steps(watch_job())["Compare the first reviews on the pin with the previous week"]["run"]).split()
    )
    assert "--isolated --no-project" in command and "--with pyyaml==6.0.3" in command
    assert "_project.scripts.oracle_reviewers.omp_watch evaluate" in command
    assert '--repo "$GITHUB_REPOSITORY"' in command


def test_the_ledger_outlives_the_seven_day_attempt_artifacts_and_is_kept_for_every_watched_state() -> None:
    keep = steps(watch_job())["Keep the attempt ledger for the next watch"]
    assert keep["with"]["name"] == "omp-watch-ledger"
    assert keep["with"]["retention-days"] >= 30 and keep["with"]["overwrite"] is True
    assert "!= 'idle'" in keep["if"]


def test_a_revert_opens_only_for_the_revert_state_and_never_twice_for_one_version() -> None:
    assert opener_job()["needs"] == "watch"
    assert "needs.watch.outputs.state == 'revert'" in opener_job()["if"]
    text = steps(opener_job())["Open the revert pull request"]["run"]
    assert len(re.findall(r"--method POST \"repos/\$\{GITHUB_REPOSITORY\}/pulls\"", text)) == 1
    assert "pulls?state=all&head=" in text
    assert "-f base=develop" in text and 'BRANCH="revert/omp-pin-${VERSION}"' in text
    assert "A closed pull request for ${BRANCH} already exists" in text


def test_the_revert_commit_subject_is_the_one_the_watch_recognises_as_a_revert() -> None:
    text = run_text(opener_job())
    assert 'git commit -m "revert(oracle): restore omp ${PREVIOUS}"' in text
    assert 'title="revert(oracle): restore omp ${PREVIOUS}"' in text


def test_the_revert_changes_only_the_policy_file_and_nothing_merges_or_pushes_to_develop() -> None:
    text = run_text(opener_job())
    assert re.findall(r"git add (.+)", text) == [".github/oracle-reviewers.yml"]
    assert 'cp "$OUT_DIR/policy.yml" .github/oracle-reviewers.yml' in text
    assert "gh pr merge" not in text and "git push origin develop" not in text


def test_the_revert_job_refuses_a_version_that_is_not_a_release_number() -> None:
    text = steps(opener_job())["Open the revert pull request"]["run"]
    assert "refusing a version that is not a release number" in text
    assert 'for value in "$VERSION" "$PREVIOUS"' in text


def test_the_opener_starts_the_review_itself_because_the_job_token_cannot_trigger_pull_request_events() -> None:
    text = steps(opener_job())["Open the revert pull request"]["run"]
    assert len(re.findall(r"gh workflow run oracle-verdict\.yml", text)) == 1
    assert '--ref develop -f pr="$PR" -f head="$HEAD_SHA"' in text
    assert text.index("/statuses") < text.index("gh workflow run")
    assert text.index("workflows/oracle-verdict.yml/runs") < text.index("gh workflow run")


def test_the_only_secret_the_workflow_reads_is_the_job_token_and_the_evaluation_gets_it_only_as_read_access() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}
    assert steps(watch_job())["Compare the first reviews on the pin with the previous week"]["env"]["GH_TOKEN"] == (
        "${{ github.token }}"
    )
    assert "secrets.GITHUB_TOKEN" not in str(watch_job())
    assert "omp_watch" not in run_text(opener_job())


def test_the_run_summary_reports_the_watch_even_when_the_evaluation_fails() -> None:
    report = steps(watch_job())["Report the watch"]
    assert report["if"] == "always() && steps.evaluate.outputs.state != ''"
    assert "assessment.txt" in report["run"] and "GITHUB_STEP_SUMMARY" in report["run"]
    assert "continue-on-error" not in steps(watch_job())["Compare the first reviews on the pin with the previous week"]


def test_the_revert_commit_carries_the_author_the_watch_uses_to_recognise_its_own_revert() -> None:
    text = run_text(opener_job())
    assert f'git config user.email "{omp_watch.BOT_AUTHOR_EMAIL}"' in text
    assert 'git config user.name "github-actions[bot]"' in text
