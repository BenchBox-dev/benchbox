from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, github
from _project.scripts.oracle_reviewers.retry import State

from .conftest import POLICY_PATH
from .test_cli_plan import (
    HEAD,
    OTHER_HEAD,
    SOUNDNESS,
    FakeGitHub,
    _backed_off,
    _decisive_state,
    _due_state,
    _plan,
    _pull,
    _reset_comments,
    _stateless_fake,
    _surfaced_sweep,
    _sweep,
    _verdict_run,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_a_failure_on_another_head_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="failure", head=OTHER_HEAD))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_a_failure_with_no_recorded_head_is_not_attributed_to_any_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="failure", head=None))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_a_failed_run_is_attributed_to_its_head_by_the_recorded_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="failure", event="pull_request_target"))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.heads == [HEAD] and fake.reasons == ["stateless-retry"]


def _marked_dispatch(*runs: dict[str, Any], state: State | None = None) -> FakeGitHub:
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off() if state is None else state)
    fake.runs = list(runs)
    return fake


def _dispatch_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub, **inputs: str
) -> tuple[int, dict[str, str], dict[str, Any]]:
    return _plan(
        monkeypatch,
        tmp_path,
        fake,
        event_name="workflow_dispatch",
        event={"inputs": {"pr": "7", **inputs}},
        ref="refs/heads/develop",
    )


def test_a_marked_retry_the_plan_refuses_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _marked_dispatch(_verdict_run(7, conclusion="failure", age=timedelta(hours=2)))
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=HEAD)
    assert code == 1
    assert values["decision"] == "skip" and "backoff" in plan["decision_reason"]
    assert "was refused" in capsys.readouterr().err


def test_an_unmarked_dispatch_the_plan_refuses_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, _marked_dispatch(), head=HEAD)
    assert code == 0
    assert values["decision"] == "skip" and "backoff" in plan["decision_reason"]


def test_a_marked_retry_never_rerolls_a_decisive_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _marked_dispatch(
        _verdict_run(7, conclusion="failure", age=timedelta(hours=2)), state=_decisive_state("failure")
    )
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=HEAD)
    assert code == 1
    assert values["decision"] == "skip" and "backoff" in plan["decision_reason"]


def test_a_marked_retry_is_not_refused_by_the_previous_heads_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    spent = State(
        7,
        "c" * 40,
        "pending",
        now - timedelta(hours=2),
        tuple(now - timedelta(hours=2 + index) for index in range(budget)),
        pending_cause="all-absent",
    )
    fake = _marked_dispatch(_verdict_run(7, conclusion="failure", event="pull_request_target"), state=spent)
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=HEAD)
    assert code == 0
    assert values["decision"] == "review"


def test_a_dispatch_for_a_superseded_head_reviews_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _marked_dispatch(state=_due_state())
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=OTHER_HEAD)
    assert code == 0
    assert values["decision"] == "skip" and "newer head" in plan["decision_reason"]


def test_a_dispatch_for_the_current_head_proceeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, values, _ = _dispatch_plan(monkeypatch, tmp_path, _marked_dispatch(state=_due_state()), head=HEAD)
    assert code == 0 and values["decision"] == "review"


def test_the_state_path_dispatches_a_due_retry_once_more_after_one_failure_without_a_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(hours=1))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.reasons == [None] and fake.heads == [HEAD]
    assert fake.posted == []


def test_the_state_path_surfaces_the_failure_instead_of_redispatching_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(hours=hours)) for hours in (1, 2, 3, 4, 5, 6, 7)]
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []
    assert len(fake.posted) == 1 and fake.posted[0][1]["state"] == "pending"


def test_the_state_path_ignores_failures_older_than_the_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=2), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(hours=hours)) for hours in (5, 6, 7)]
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.posted == []


def test_the_state_path_respects_the_daily_dispatch_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [_verdict_run(7, conclusion="success", age=timedelta(hours=2 + index)) for index in range(budget)]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_dispatches_for_another_head_do_not_spend_this_heads_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [
        _verdict_run(7, conclusion="success", age=timedelta(hours=2 + index), head=OTHER_HEAD)
        for index in range(budget)
    ]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7]


def test_a_stateless_retry_is_not_refused_by_the_previous_heads_budget_and_then_reviews(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    old = State(
        7,
        "c" * 40,
        "pending",
        now - timedelta(hours=2),
        tuple(now - timedelta(hours=2 + index) for index in range(budget)),
        pending_cause="all-absent",
    )
    fake = FakeGitHub(_pull(), SOUNDNESS, old)
    fake.runs = [_verdict_run(7, conclusion="failure", event="pull_request_target", age=timedelta(hours=1))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.reasons == ["stateless-retry"]
    code, values, _ = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=HEAD)
    assert code == 0 and values["decision"] == "review"


def test_a_cancelled_run_is_not_a_failure_without_a_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _stateless_fake(
        _verdict_run(7, conclusion="failure", event="pull_request_target", age=timedelta(minutes=30)),
        _verdict_run(7, conclusion="cancelled", event="issue_comment", age=timedelta(minutes=40)),
    )
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.posted == []


def test_cancelled_runs_alone_leave_nothing_to_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="cancelled", event="pull_request_target"))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_an_owner_reset_ignores_the_daily_budget_in_the_sweep_as_in_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    exhausted = State(
        7,
        HEAD,
        "pending",
        now - timedelta(minutes=40),
        tuple(now - timedelta(hours=1 + index) for index in range(budget)),
        pending_cause="all-absent",
    )
    swept = FakeGitHub(_pull(), SOUNDNESS, exhausted)
    swept.comments = _reset_comments()
    swept.runs = [
        _verdict_run(7, conclusion="success", age=timedelta(minutes=40 + index * 30)) for index in range(budget)
    ]
    assert _sweep(monkeypatch, tmp_path, swept) == 0
    assert swept.dispatched == [7]
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    planned = FakeGitHub(_pull(), SOUNDNESS, exhausted)
    planned.comments = _reset_comments()
    _, values, plan = _plan(monkeypatch, tmp_path, planned, event_name="issue_comment", event=event)
    assert values["decision"] == "review" and "reset by the owner" in plan["decision_reason"]


def test_a_queued_run_does_not_spend_the_owner_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, status="queued", conclusion=None, age=timedelta(minutes=10))]
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "review" and "reset by the owner" in plan["decision_reason"]


def test_a_cancelled_run_does_not_spend_the_owner_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, conclusion="cancelled", age=timedelta(minutes=10))]
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "review" and "reset by the owner" in plan["decision_reason"]


def test_a_later_run_with_no_recorded_head_spends_the_owner_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, conclusion="failure", head=None, age=timedelta(minutes=10))]
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "skip" and "reset" not in plan["decision_reason"]


def test_a_later_run_on_another_head_does_not_spend_the_owner_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, conclusion="failure", head=OTHER_HEAD, age=timedelta(minutes=10))]
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "review" and "reset by the owner" in plan["decision_reason"]


def test_a_capped_runs_listing_takes_the_degraded_path_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, _due_state())
    fake.runs_error = github.RunListingCapped("gh api runs returned 1000 of 1972 runs; narrow the window")
    fake.runs_error_wide_only = True
    assert _sweep(monkeypatch, tmp_path, fake) == 1
    assert fake.dispatched == [7]
    assert "the sweep ran degraded" in capsys.readouterr().err


def test_the_sweep_lists_only_the_recent_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS)
    seen: list[datetime] = []
    original = fake._verdict_runs
    fake._verdict_runs = lambda repo, workflow, since: (seen.append(since), original(repo, workflow, since))[1]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert datetime.now(UTC) - seen[0] == pytest.approx(timedelta(hours=48), abs=timedelta(minutes=1))


def test_a_failed_run_inside_the_backoff_of_a_same_head_state_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    state = State(7, HEAD, "pending", now - timedelta(minutes=10), (now,), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(minutes=5))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_a_marked_retry_whose_state_cannot_be_read_fails_its_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _marked_dispatch(_verdict_run(7, conclusion="failure", age=timedelta(hours=2)))
    fake.state_error = github.GitHubError("artifact unreadable")
    code, values, plan = _dispatch_plan(monkeypatch, tmp_path, fake, reason="stateless-retry", head=HEAD)
    assert code == 1
    assert values["decision"] == "skip" and "could not be read" in plan["decision_reason"]
    assert "was refused" in capsys.readouterr().err


def test_an_unmarked_dispatch_whose_state_cannot_be_read_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _marked_dispatch()
    fake.state_error = github.GitHubError("artifact unreadable")
    code, values, _ = _dispatch_plan(monkeypatch, tmp_path, fake, head=HEAD)
    assert code == 0 and values["decision"] == "skip"


def test_a_cancelled_run_that_hit_its_job_timeout_is_a_failure_without_a_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _verdict_run(7, conclusion="failure", event="pull_request_target", age=timedelta(hours=2))
    timed_out = _verdict_run(7, conclusion="cancelled", age=timedelta(hours=1))
    fake = _stateless_fake(timed_out, first)
    fake.timed_out = {timed_out["id"]}
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [] and len(fake.posted) == 1


def test_a_cancelled_run_that_hit_its_job_timeout_spends_the_owner_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    run = _verdict_run(7, conclusion="cancelled", age=timedelta(minutes=10))
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    fake.runs = [run]
    fake.timed_out = {run["id"]}
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "skip" and "reset" not in plan["decision_reason"]


def test_a_failure_already_surfaced_as_pending_is_not_redispatched_after_the_runs_age_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(days=4), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.statuses = [
        {
            "context": "oracle-verdict",
            "state": "pending",
            "description": github.SWEEP_FAILURE_SURFACE_PREFIX + "Run the attempt loop. Rerun with /oracle-review",
            "target_url": "u",
            "creator": {"login": "benchbox-oracle[bot]", "type": "Bot"},
            "updated_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
        }
    ]
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [] and fake.posted == []


def _surfaced_status() -> dict[str, Any]:
    return {
        "context": "oracle-verdict",
        "state": "pending",
        "description": github.SWEEP_FAILURE_SURFACE_PREFIX + "Run the attempt loop. Rerun with /oracle-review",
        "target_url": "u",
        "creator": {"login": "benchbox-oracle[bot]", "type": "Bot"},
        "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }


def test_an_unused_owner_reset_restarts_a_due_head_whose_failure_was_surfaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    state = State(
        7, HEAD, "pending", now - timedelta(hours=10), (now - timedelta(hours=9),), pending_cause="all-absent"
    )
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.statuses = [_surfaced_status()]
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(hours=hours)) for hours in (5, 6)]
    fake.comments = _reset_comments()
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.posted == []


def test_an_unused_owner_reset_overrides_the_daily_dispatch_budget_for_a_due_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, conclusion="success", age=timedelta(hours=1 + index)) for index in range(budget)]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7] and fake.reasons == [None]


def test_a_spent_owner_reset_leaves_a_surfaced_head_held(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=10), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, state)
    fake.statuses = [_surfaced_status()]
    fake.comments = _reset_comments()
    fake.runs = [_verdict_run(7, conclusion="failure", age=timedelta(minutes=10))]
    assert _surfaced_sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_timeouts_are_looked_up_only_for_a_pull_request_that_is_a_retry_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cancelled = [_verdict_run(7, conclusion="cancelled", age=timedelta(minutes=30 + index)) for index in range(3)]
    decided = FakeGitHub(_pull(), SOUNDNESS, State(7, HEAD, "success", datetime.now(UTC) - timedelta(hours=3)))
    decided.runs = list(cancelled)
    assert _sweep(monkeypatch, tmp_path, decided) == 0
    fresh = FakeGitHub(
        _pull(),
        SOUNDNESS,
        State(7, HEAD, "pending", datetime.now(UTC) - timedelta(minutes=5), pending_cause="all-absent"),
    )
    fresh.runs = list(cancelled)
    assert _sweep(monkeypatch, tmp_path, fresh) == 0
    assert decided.timeout_lookups == [] and fresh.timeout_lookups == []


def test_a_head_with_a_finished_success_newer_than_its_cancelled_runs_needs_no_timeout_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(
        _verdict_run(7, conclusion="success", age=timedelta(minutes=20)),
        _verdict_run(7, conclusion="cancelled", age=timedelta(minutes=40)),
    )
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.timeout_lookups == [] and fake.dispatched == []


def test_a_head_with_no_failed_or_cancelled_run_needs_no_timeout_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="skipped", age=timedelta(minutes=20)))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.timeout_lookups == []
