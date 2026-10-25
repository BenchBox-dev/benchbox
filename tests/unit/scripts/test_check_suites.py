from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "_project/scripts"
sys.path.insert(0, str(SCRIPTS))

check_suites = importlib.import_module("check_suites")
required_lane = importlib.import_module("required_lane")

SPEC = importlib.util.spec_from_file_location("green_unmerged_sweep_under_test", SCRIPTS / "green_unmerged_sweep.py")
assert SPEC and SPEC.loader
sweep = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sweep
SPEC.loader.exec_module(sweep)

REPO = "o/r"


def check(name: str, suite: int | None, started: str, conclusion: str | None, run: int | None = None) -> dict[str, Any]:
    run = run if run is not None else 100 + (suite or 0)
    return {
        "id": run * 10,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "started_at": started,
        "head_sha": "a" * 40,
        "details_url": f"https://github.com/{REPO}/actions/runs/{run}/job/{run * 10}",
        "check_suite": {"id": suite},
    }


def lane(conclusions: dict[str, str]) -> list[dict[str, Any]]:
    return [
        check(name, index, "2026-10-24T10:00:00Z", conclusion)
        for index, (name, conclusion) in enumerate(conclusions.items(), 1)
    ]


GREEN = dict.fromkeys(required_lane.REQUIRED_CHECK_NAMES, "success")
STALE_FAILED = check("core", 90, "2026-10-24T09:00:00Z", "failure", run=77)


def test_suite_identity_and_run_url_survive_parsing() -> None:
    text = "\n".join(json.dumps(item) for item in [check("core", 5, "2026-10-24T10:00:00Z", "success")])
    (suite,) = check_suites.suites_by_name(check_suites.parse_check_run_lines(text))["core"]
    assert suite.suite_id == 5
    assert suite.actions_run_id == 105
    assert suite.url.endswith("/actions/runs/105/job/1050")


def test_a_malformed_line_is_rejected() -> None:
    with pytest.raises(ValueError):
        check_suites.parse_check_run_lines("[1]")
    with pytest.raises(ValueError):
        check_suites.parse_check_run_lines("nope")


def test_the_newest_run_of_one_suite_wins() -> None:
    older = check("core", 1, "2026-10-24T09:00:00Z", "failure")
    newer = check("core", 1, "2026-10-24T10:00:00Z", "success")
    (suite,) = check_suites.suites_by_name([newer, older])["core"]
    assert suite.conclusion == "success"


def test_suites_sharing_a_name_stay_separate_newest_first() -> None:
    records = [
        check("oracle-review", 1, "2026-10-24T09:00:00Z", "failure"),
        check("oracle-review", 3, "2026-10-24T11:00:00Z", "success"),
        check("oracle-review", 2, "2026-10-24T10:00:00Z", "failure"),
    ]
    suites = check_suites.suites_by_name(records)["oracle-review"]
    assert [suite.suite_id for suite in suites] == [3, 2, 1]


def test_a_stale_failure_hidden_by_a_newer_success_is_reported_with_its_run() -> None:
    records = [
        check("oracle-review", 1, "2026-10-24T09:00:00Z", "failure", run=501),
        check("oracle-review", 2, "2026-10-24T09:30:00Z", "failure", run=502),
        check("oracle-review", 3, "2026-10-24T10:30:00Z", "success", run=503),
    ]
    evaluation = check_suites.evaluate(records, ["oracle-review"])["oracle-review"]
    assert not evaluation.green
    assert [suite.actions_run_id for suite in evaluation.hidden_failures] == [502, 501]
    reasons = check_suites.hidden_failure_reasons({"oracle-review": evaluation})
    assert len(reasons) == 2
    assert all("make pr-rerun" in reason for reason in reasons)
    assert "actions/runs/501" in reasons[1]


def test_a_newest_failure_is_failed_but_not_hidden() -> None:
    records = [
        check("core", 1, "2026-10-24T09:00:00Z", "success"),
        check("core", 2, "2026-10-24T10:00:00Z", "failure"),
    ]
    evaluation = check_suites.evaluate(records, ["core"])["core"]
    assert len(evaluation.failed) == 1
    assert evaluation.hidden_failures == ()


def test_a_pending_suite_blocks_green_without_being_a_failure() -> None:
    records = [
        check("core", 1, "2026-10-24T09:00:00Z", "success"),
        check("core", 2, "2026-10-24T10:00:00Z", None),
    ]
    evaluation = check_suites.evaluate(records, ["core"])["core"]
    assert not evaluation.green
    assert evaluation.failed == ()
    assert len(evaluation.pending) == 1


def test_a_missing_required_name_is_not_green() -> None:
    assert not check_suites.all_green([check("core", 1, "t", "success")], ["core", "tooling"])
    assert not check_suites.all_green([], [])


def test_plan_reruns_groups_suites_by_actions_run() -> None:
    records = [
        check("oracle-review", 1, "2026-10-24T09:00:00Z", "failure", run=501),
        check("hold", 1, "2026-10-24T09:00:01Z", "failure", run=501),
        check("oracle-review", 2, "2026-10-24T09:30:00Z", "cancelled", run=502),
        check("oracle-review", 3, "2026-10-24T10:30:00Z", "success", run=503),
    ]
    plan = check_suites.plan_reruns(check_suites.evaluate(records, ["oracle-review", "hold"]))
    assert [(item.run_id, item.scope) for item in plan] == [(501, "failed"), (502, "all")]
    assert plan[0].arguments(REPO) == ["run", "rerun", "501", "--repo", REPO, "--failed"]
    assert plan[1].arguments(REPO) == ["run", "rerun", "502", "--repo", REPO]


def test_plan_reruns_refuses_a_failure_it_cannot_rerun() -> None:
    records = [check("core", 1, "t", "action_required")]
    with pytest.raises(check_suites.Unrerunnable, match="action_required"):
        check_suites.plan_reruns(check_suites.evaluate(records, ["core"]))


def test_plan_reruns_refuses_a_failure_without_an_actions_run() -> None:
    record = {**check("core", 1, "t", "failure"), "details_url": "https://ci.example/build/1"}
    with pytest.raises(check_suites.Unrerunnable, match="no Actions run"):
        check_suites.plan_reruns(check_suites.evaluate([record], ["core"]))


def test_verdicts_surface_the_failed_suite_over_a_newer_success() -> None:
    records = [check("core", 1, "2026-10-24T09:00:00Z", "failure", run=77), check("core", 2, "t2", "success")]
    verdict = check_suites.verdicts(records)["core"]
    assert verdict["conclusion"] == "failure"
    assert verdict["hidden_failure"] is True
    assert verdict["suite_id"] == 1


def test_required_contexts_are_deduplicated_and_must_exist() -> None:
    assert check_suites.parse_required_contexts("core\ntooling\ncore\n") == ("core", "tooling")
    with pytest.raises(ValueError):
        check_suites.parse_required_contexts("\n")


def test_required_lane_is_green_only_when_every_suite_of_every_name_passes() -> None:
    assert required_lane.is_required_lane_green(lane(GREEN))
    hidden = [*lane(GREEN), STALE_FAILED]
    assert not required_lane.is_required_lane_green(hidden)


def test_required_lane_still_accepts_runs_without_suite_identity() -> None:
    plain = [{"name": name, "status": "completed", "conclusion": "success", "started_at": "t"} for name in GREEN]
    assert required_lane.is_required_lane_green(plain)
    plain.append({"name": "core", "status": "completed", "conclusion": "failure", "started_at": "u"})
    assert not required_lane.is_required_lane_green(plain)


def _pr(check_runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "number": 5,
        "title": "t",
        "html_url": "u",
        "draft": False,
        "head_pushed_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-20T00:00:00Z",
        "check_runs": check_runs,
    }


NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)


def test_the_sweep_names_a_stale_failed_suite_and_does_not_call_the_pr_green() -> None:
    classified = sweep.classify_pr(_pr([*lane(GREEN), STALE_FAILED]), NOW)
    assert not classified.required_green
    (reason,) = classified.hidden_failures
    assert "actions/runs/77" in reason
    digest = sweep.build_digest([classified], now=NOW, repo=REPO)
    assert "actions/runs/77" in digest and "make pr-rerun" in digest


def test_the_sweep_reports_nothing_hidden_for_a_green_lane() -> None:
    classified = sweep.classify_pr(_pr(lane(GREEN)), NOW)
    assert classified.required_green
    assert classified.hidden_failures == []
    assert "make pr-rerun" not in sweep.build_digest([classified], now=NOW, repo=REPO)


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get_all(self, path: str, params: dict[str, str] | None = None, **_: Any) -> list[dict[str, Any]]:
        self.calls.append((path, params))
        if path.endswith("/pulls"):
            return [{"number": 5, "head": {"sha": "a" * 40}, "title": "t"}]
        if path.endswith("/check-runs"):
            return [STALE_FAILED, *lane(GREEN)]
        return []

    def get_one(self, path: str) -> Any:
        if "/rules/branches/" in path:
            return [
                {"type": "deletion"},
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": name} for name in (*GREEN, "oracle-review")]},
                },
            ]
        return {}


def test_the_sweep_fetch_requests_latest_runs_and_keeps_suite_identity() -> None:
    client = _Client()
    (pr,) = sweep.fetch_open_prs(client, "o", "r")
    assert ("repos/o/r/commits/" + "a" * 40 + "/check-runs", {"filter": "latest"}) in client.calls
    stale = next(item for item in pr["check_runs"] if item["check_suite"]["id"] == 90)
    assert stale["details_url"].endswith("/actions/runs/77/job/770")
    assert not sweep.classify_pr(pr, NOW).required_green


def test_the_sweep_fetch_carries_the_ruleset_required_names() -> None:
    (pr,) = sweep.fetch_open_prs(_Client(), "o", "r")
    assert pr["required_contexts"] == [*GREEN, "oracle-review"]


def test_the_sweep_does_not_call_a_pr_green_when_only_a_ruleset_check_has_a_hidden_failure() -> None:
    records = [
        *lane(GREEN),
        check("oracle-review", 20, "2026-10-24T10:30:00Z", "success"),
        check("oracle-review", 21, "2026-10-24T09:00:00Z", "failure", run=88),
    ]
    pr = {**_pr(records), "required_contexts": [*GREEN, "oracle-review"]}
    classified = sweep.classify_pr(pr, NOW)
    assert not classified.required_green
    assert not classified.stranded
    (reason,) = classified.hidden_failures
    assert "oracle-review" in reason and "actions/runs/88" in reason


def test_rules_without_required_status_checks_are_rejected() -> None:
    with pytest.raises(ValueError):
        check_suites.required_contexts_from_rules([{"type": "deletion"}])
    with pytest.raises(ValueError):
        check_suites.required_contexts_from_rules({"message": "Not Found"})
