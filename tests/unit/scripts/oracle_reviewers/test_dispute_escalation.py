from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import adjudication, protocol, report
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.retry import State

from .test_attempts import _v
from .test_cli_plan import (
    CHECKER,
    DEFECT,
    HEAD,
    SOUNDNESS,
    TWO_FILE_DIFF,
    TWO_FILES,
    FakeGitHub,
    _acked_thread,
    _decided_head,
    _disputed_thread,
    _plan,
    _pull,
    _record,
    _review,
    _sweep,
    _thread_fake,
    _verdict_run,
)
from .test_rounds import _decided, _final, _forced, _plan_same_head, _prior, _protocol_plan, _with_priors

pytestmark = [pytest.mark.unit, pytest.mark.fast]

COMMENT_EVENT = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
RULING = "Oracle adjudication: WITHDRAW D1 - the helper keeps the row, see pkg/kept.py:12"
REVIEWED_AT = "2026-10-08T10:00:00Z"
AFTER_REVIEW = "2026-10-09T10:00:00Z"


def _exhausted() -> State:
    now = datetime.now(UTC)
    return State(7, HEAD, "failure", now, tuple(now - timedelta(minutes=minute) for minute in range(1, 12)))


def _plan_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub, event: dict[str, Any]
) -> tuple[int, dict[str, str], dict[str, Any]]:
    return _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)


def _comment_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub
) -> tuple[dict[str, str], dict[str, Any]]:
    _, values, plan = _plan_with(monkeypatch, tmp_path, fake, COMMENT_EVENT)
    return values, plan


def test_a_dispute_round_runs_although_the_manual_budget_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _thread_fake(_decided_head(), [_disputed_thread()])
    fake.state = _exhausted()
    values, plan = _comment_plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["protocol"]["forced"] is True
    assert plan["manual"] is False
    assert "exempt from the retry budget" in plan["decision_reason"]


def test_a_rerun_without_a_dispute_runs_no_reviewer_even_with_the_budget_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _thread_fake(_decided_head(), [_acked_thread()])
    fake.state = _exhausted()
    values, plan = _comment_plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry"
    assert plan["chain"] == []


def test_an_untrusted_dispute_reply_gets_no_exemption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _thread_fake(_decided_head(), [_disputed_thread(association="NONE")])
    fake.state = _exhausted()
    values, plan = _comment_plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry"
    assert plan["chain"] == [] and plan["manual"] is True


def _forced_review(number: int) -> dict[str, Any]:
    record = _record(
        TWO_FILE_DIFF,
        "SHIP_WITH_FIXES",
        head_sha=HEAD,
        open_defects=[DEFECT],
        next_id=2,
        kind="follow-up",
        forced=True,
        round=number,
    )
    return _review(record, id=number, submitted_at=f"2026-10-08T1{number}:00:00Z")


def test_a_third_dispute_round_on_one_patch_is_refused_even_with_budget_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeGitHub(_pull(), TWO_FILES, threads=[_disputed_thread()], diff=TWO_FILE_DIFF)
    first = _review(_record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", head_sha=HEAD, open_defects=[DEFECT], next_id=2))
    fake.reviews = [first, _forced_review(2), _forced_review(3)]
    values, plan = _comment_plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "skip" and values["post"] == "false"
    assert "already had 2 dispute re-reviews" in plan["decision_reason"]


def test_the_dispute_limit_names_the_adjudication_grammar_for_an_escalated_finding() -> None:
    last = {**_forced(HEAD, 3), "open_defects": _decided(HEAD)["open_defects"], "escalated": ["D1"]}
    history = protocol.History([_decided(HEAD), _forced(HEAD, 2), last])
    refused = _plan_same_head(history, force=True)
    assert refused.kind == protocol.SKIP
    assert "D1 escalated to the owner" in refused.reason and adjudication.GRAMMAR in refused.reason


def test_a_maintained_finding_stops_counting_as_a_live_dispute() -> None:
    latest = {**_decided(HEAD), "adjudicated": [{"id": "D1", "action": "MAINTAIN"}]}
    disputes = [{"id": "D1", "cycle": 1}]
    assert protocol.live_disputes(disputes, _decided(HEAD)) == disputes
    assert protocol.live_disputes(disputes, latest) == []


REBUTTAL = "Dispute: pkg/kept.py:12 keeps the row, so the defect is wrong."


def _dispute_of(defect_id: str) -> dict[str, Any]:
    return {
        "id": defect_id,
        "cycle": 1,
        "path": "pkg/a.py",
        "title": "t",
        "replies": [{"author": "joe", "body": REBUTTAL}],
        "claim_hashes": [protocol.claim_hash(protocol.canonical_claim(REBUTTAL))],
    }


def _disposition(defect_id: str, disputed: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": defect_id, "status": "not_fixed", "evidence": f"{defect_id} evidence"}
    if disputed:
        entry.update(claim=REBUTTAL, concedes=False)
    return entry


def _escalation_final(policy: Policy, tmp_path: Path, maintained: dict[str, int]) -> report.Final:
    plan = _protocol_plan(
        policy,
        kind="follow-up",
        round=3,
        forced=True,
        prior=[_prior("D1"), _prior("D2")],
        changed=[CHECKER],
        maintained=maintained,
        next_id=3,
    )
    plan["findings_delivery"] = "review"
    plan["disputes"] = [_dispute_of("D1")]
    verdict = {**_v("s"), "prior_defects": [_disposition("D1", True), _disposition("D2", False)]}
    return _final(policy, tmp_path, plan, verdict)


def test_a_finding_disputed_and_maintained_twice_on_one_patch_is_escalated(policy: Policy, tmp_path: Path) -> None:
    final = _escalation_final(policy, tmp_path, {"D1": 1, "D2": 1})
    record = protocol.decode_marker(final.review["body"])
    assert record["escalated"] == ["D1"]
    assert record["disputed"] == ["D1"]
    assert final.description.endswith("; escalated D1")
    assert "adjudication: WITHDRAW|MAINTAIN D<n> - <reason>" in final.review["body"]


def test_a_finding_that_was_never_disputed_is_not_escalated(policy: Policy, tmp_path: Path) -> None:
    final = _escalation_final(policy, tmp_path, {"D1": 1, "D2": 1})
    assert "D2" not in protocol.decode_marker(final.review["body"])["escalated"]


def test_only_disputed_rounds_count_toward_escalation() -> None:
    open_defects = [{"id": "D1"}, {"id": "D2"}]
    base = {"patch_digest": "d", "kind": "follow-up", "forced": True, "open_defects": open_defects}
    records = [{**base, "disputed": ["D1"]}, {**base}]
    assert protocol.maintained_counts(records, "d") == {"D1": 1}


def test_a_finding_maintained_once_is_not_escalated(policy: Policy, tmp_path: Path) -> None:
    plan = _protocol_plan(
        policy,
        kind="follow-up",
        round=2,
        forced=True,
        prior=[_prior("D1")],
        changed=[CHECKER],
        maintained={},
        next_id=2,
    )
    plan["findings_delivery"] = "review"
    final = _final(policy, tmp_path, plan, _with_priors(_v("s"), D1="not_fixed"))
    assert "escalated" not in protocol.decode_marker(final.review["body"])
    assert "escalated" not in final.description


class _CommentFake(FakeGitHub):
    comment: dict[str, Any]

    def _json(self, path: str) -> dict[str, Any]:
        if "/issues/comments/" in path:
            return self.comment
        return super()._json(path)


def _owner_comment(body: str = RULING, **over: Any) -> dict[str, Any]:
    return {
        "id": 55,
        "user": {"login": "joeharris76", "type": "User"},
        "created_at": AFTER_REVIEW,
        "updated_at": AFTER_REVIEW,
        "body": body,
        **over,
    }


def _escalated_head(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "head_sha": HEAD,
        "open_defects": [DEFECT],
        "next_id": 2,
        "escalated": ["D1"],
        "kind": "follow-up",
        "forced": True,
        "round": 3,
        **over,
    }
    return _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", **fields)


def _adjudication_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    comment: dict[str, Any],
    record: dict[str, Any] | None = None,
    state: State | None = None,
) -> tuple[dict[str, str], dict[str, Any], _CommentFake]:
    fake = _CommentFake(_pull(), TWO_FILES, state, threads=[], diff=TWO_FILE_DIFF)
    fake.reviews = [_review(record or _escalated_head())]
    fake.comment = comment
    event = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {}},
        "comment": {"id": 55, "body": comment["body"]},
    }
    _, values, plan = _plan_with(monkeypatch, tmp_path, fake, event)
    return values, plan, fake


def test_an_owner_adjudication_is_redecided_without_a_reviewer_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, plan, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment(), state=_exhausted())
    assert values["decision"] == "carry" and values["post"] == "true"
    assert plan["adjudication"] == {
        "action": "WITHDRAW",
        "id": "D1",
        "reason": "the helper keeps the row, see pkg/kept.py:12",
    }
    assert plan["chain"] == [] and plan["manual"] is False


def _finalized(plan: dict[str, Any]) -> tuple[report.Final, dict[str, Any]]:
    final = report.adjudicated({**plan, "findings_delivery": "review"})
    assert final.review is not None
    record = protocol.decode_marker(final.review["body"])
    assert record is not None
    return final, record


def test_a_withdrawal_stops_the_finding_blocking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, plan, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment())
    final, record = _finalized(plan)
    assert final.state == "success"
    assert record["decision"] == "SHIP" and record["open_defects"] == []
    assert record["adjudicated"] == [{"id": "D1", "action": "WITHDRAW"}]
    assert "escalated" not in record and record["carried"] is True
    assert final.description.startswith("owner adjudication: WITHDRAW D1")
    assert final.review["body"].startswith("### oracle-verdict: success for `")


def test_maintaining_a_finding_closes_its_dispute_and_keeps_it_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "Oracle adjudication: MAINTAIN D1 - the row is still dropped"
    _, plan, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment(body))
    final, record = _finalized(plan)
    assert final.state == "failure"
    assert record["decision"] == "SHIP_WITH_FIXES" and [item["id"] for item in record["open_defects"]] == ["D1"]
    assert record["adjudicated"] == [{"id": "D1", "action": "MAINTAIN"}]
    assert "escalated" not in record
    assert protocol.live_disputes([{"id": "D1", "cycle": 1}], record) == []


@pytest.mark.parametrize(
    ("comment", "why"),
    [
        (_owner_comment(updated_at="2026-10-09T11:00:00Z"), "edited"),
        (_owner_comment(created_at="2026-10-08T09:00:00Z", updated_at="2026-10-08T09:00:00Z"), "late"),
        (_owner_comment(user={"login": "someone", "type": "User"}), "not an attester"),
        (_owner_comment(user={"login": "joeharris76", "type": "Bot"}), "bot account"),
        (_owner_comment("Oracle adjudication: WITHDRAW D1"), "no reason"),
        (_owner_comment("Oracle adjudication: DISMISS D1 - nope"), "unknown action"),
        (_owner_comment("Oracle adjudication: WITHDRAW D9 - not escalated"), "finding not escalated"),
    ],
)
def test_an_invalid_adjudication_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, comment: dict[str, Any], why: str
) -> None:
    values, plan, _ = _adjudication_plan(monkeypatch, tmp_path, comment)
    assert values["decision"] == "skip" and values["post"] == "false", why
    assert plan["protocol"] is None


def test_an_adjudication_for_another_head_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values, plan, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment(), _escalated_head(head_sha="c" * 40))
    assert values["decision"] == "skip"
    assert "not for the current head" in plan["decision_reason"]


def test_an_adjudication_on_an_unescalated_head_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plain = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", head_sha=HEAD, open_defects=[DEFECT], next_id=2)
    values, plan, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment(), plain)
    assert values["decision"] == "skip" and "not escalated" in plan["decision_reason"]


def test_a_malformed_adjudication_comment_never_plans_a_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values, _, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment("Oracle adjudication:please rerun"))
    assert values["decision"] == "skip" and values["post"] == "false"


def test_the_adjudication_grammar_is_exact() -> None:
    assert adjudication.parse(RULING) == adjudication.Adjudication(
        "WITHDRAW", "D1", "the helper keeps the row, see pkg/kept.py:12"
    )
    assert adjudication.parse("Oracle adjudication: MAINTAIN D12 - still wrong\nextra line") is None
    assert adjudication.parse("oracle adjudication: MAINTAIN D12 - still wrong") is None
    assert adjudication.parse("Oracle adjudication: MAINTAIN D0 - nope") is None


def _failed_state() -> State:
    return State(7, HEAD, "failure", datetime.now(UTC) - timedelta(hours=3))


def _answerable_thread(created_at: str) -> dict[str, Any]:
    thread = _disputed_thread()
    thread["replies"][0]["created_at"] = created_at
    return thread


def test_the_sweep_redispatches_a_head_with_an_unanswered_dispute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reply = datetime.now(UTC) - timedelta(minutes=30)
    fake = FakeGitHub(_pull(), SOUNDNESS, _failed_state(), threads=[_answerable_thread(reply.isoformat())])
    fake.runs = [_verdict_run(7, age=timedelta(hours=2))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7]


def test_the_sweep_does_not_redispatch_a_dispute_a_later_run_already_saw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reply = datetime.now(UTC) - timedelta(hours=2)
    fake = FakeGitHub(_pull(), SOUNDNESS, _failed_state(), threads=[_answerable_thread(reply.isoformat())])
    fake.runs = [_verdict_run(7, age=timedelta(minutes=30))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


@pytest.mark.parametrize(
    "thread",
    [_acked_thread(), _disputed_thread(association="NONE")],
    ids=["acknowledgement", "untrusted"],
)
def test_the_sweep_ignores_replies_that_are_not_trusted_disputes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, thread: dict[str, Any]
) -> None:
    thread["replies"][0]["created_at"] = datetime.now(UTC).isoformat()
    fake = FakeGitHub(_pull(), SOUNDNESS, _failed_state(), threads=[thread])
    fake.runs = [_verdict_run(7, age=timedelta(hours=2))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_the_sweep_leaves_a_success_head_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = State(7, HEAD, "success", datetime.now(UTC) - timedelta(hours=3))
    thread = _answerable_thread(datetime.now(UTC).isoformat())
    fake = FakeGitHub(_pull(), SOUNDNESS, state, threads=[thread])
    fake.runs = [_verdict_run(7, age=timedelta(hours=2))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_the_trigger_prefix_matches_case_insensitively_yet_the_grammar_stays_exact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "oracle adjudication: WITHDRAW D1 - lower case"
    values, _, _ = _adjudication_plan(monkeypatch, tmp_path, _owner_comment(body))
    assert values["decision"] == "skip" and values["post"] == "false"
