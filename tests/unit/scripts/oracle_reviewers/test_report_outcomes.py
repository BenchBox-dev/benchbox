from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import credentials, dedup, protocol, report, selection
from _project.scripts.oracle_reviewers.diff import commentable_lines
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.retry import INTEGRITY, MARKER_LIMIT, OVERSIZE, UNREPORTED
from _project.scripts.oracle_reviewers.selection import Attempt, Step
from _project.scripts.oracle_reviewers.verdict import Finding, validate

from .test_attempts import CHECKER, DIFF, HEAD, _finding, _plan, _v
from .test_dispute_keyed_evidence import _dispute
from .test_rounds import OTHER, _prior, _protocol_plan, _record

pytestmark = [pytest.mark.unit, pytest.mark.fast]

COMMENTABLE = commentable_lines(DIFF)
OLD = "c" * 40


def _defect(title: str, line: int = 2, detail: str = "", file: str = CHECKER, severity: str = "Medium", **extra: Any):
    return {"severity": severity, "file": file, "line": line, "title": title, "detail": detail, **extra}


def _finalize(
    policy: Policy,
    plan: dict[str, Any],
    verdict: dict[str, Any] | None,
    *,
    kind: str = selection.PASS,
    attempts: Sequence[Attempt] | None = None,
    reasons: Sequence[str] = (),
    errors: Sequence[str] = (),
    reviewer: str | None = "small",
    commentable: Any = COMMENTABLE,
) -> report.Final:
    spec = policy.reviewers[reviewer] if reviewer is not None else None
    step = Step(kind, spec, tuple(reasons))
    if attempts is None:
        attempts = [Attempt(1, reviewer, kind)] if verdict is not None and reviewer is not None else []
    verdicts = {1: validate(verdict)} if verdict is not None else {}
    return report.finalize(plan, step, list(errors), list(attempts), verdicts, commentable)


def _comment_plan(policy: Policy, **over: Any) -> dict[str, Any]:
    return {**_protocol_plan(policy, **over), "findings_delivery": "comment"}


def _assert_shape(text: str) -> None:
    lines = [line for line in text.split("\n") if not line.startswith(protocol.MARKER_OPEN)]
    listing = lambda line: line.startswith(("- ", "  "))
    for current, following in zip(lines, lines[1:], strict=False):
        if not current:
            continue
        if listing(current):
            assert not following or listing(following), (current, following)
        else:
            assert following == "", (current, following)
    assert "None" not in "\n".join(lines) and "XX" not in "\n".join(lines)


def _kitchen_plan(policy: Policy, **over: Any) -> dict[str, Any]:
    prior = [_prior(f"D{n}") for n in range(1, 7)]
    prior[4] = {**_prior("D5", line=1), "end_line": 2}
    plan = _comment_plan(
        policy, kind="follow-up", round=2, prior=prior, changed=[CHECKER], next_id=7, strikes=1, **over
    )
    plan["open_findings"] = {dedup.fingerprint(CHECKER, "Already open problem"): "Medium"}
    plan.update(scope="changed", reviewed_head=OLD)
    return plan


def _kitchen_verdict() -> dict[str, Any]:
    inline = _defect("Inline problem", 2, "inline detail")
    verdict = _v(
        "Overall summary",
        [
            inline,
            dict(inline),
            _defect("Outside problem", 40),
            _defect("Already open problem", 1),
            _defect("Elsewhere problem", 9, file=OTHER),
        ],
    )
    statuses = {"D1": "fixed", "D2": "fixed", "D3": "withdrawn", "D4": "withdrawn"}
    verdict["prior_defects"] = [{"id": key, "status": value, "evidence": "e"} for key, value in statuses.items()]
    return verdict


def _kitchen(policy: Policy, **over: Any) -> report.Final:
    attempts = [
        Attempt(1, "small", selection.FAIL),
        Attempt(2, "max", selection.ABSENT, absence="timeout", detail="took  too\nlong"),
    ]
    return _finalize(
        policy,
        _kitchen_plan(policy, **over),
        _kitchen_verdict(),
        kind=selection.FAIL,
        attempts=attempts,
        reasons=("small: waits until 2026-10-05T12:00:00", "medium: not selected: pool blocked"),
    )


def test_a_follow_up_body_lists_every_part_of_the_decision_in_order(policy: Policy) -> None:
    final = _kitchen(policy)
    body = final.body
    _assert_shape(body)
    lines = body.split("\n")
    assert lines[0] == f"### oracle-verdict: failure for `{HEAD}`"
    for line in (
        f"- **Medium** `{CHECKER}:2`: D7: Inline problem",
        "  inline detail",
        f"- **Medium** `{CHECKER}:40`: D8: Outside problem",
        f"- **Medium** `{CHECKER}`: D9: Already open problem",
        f"- **High** `{CHECKER}:1-2`: D5: Old",
        f"- **High** `{CHECKER}:2`: D6: Old",
        f"- **Medium** `{OTHER}:9`: Elsewhere problem",
    ):
        assert line in lines
    assert "Decision: **SHIP WITH FIXES**." in lines
    assert "Overall summary" in lines
    assert "Reviewer: small (role review, omp @smol)." in lines
    assert any("Verified fixed: D1, D2." in line for line in lines)
    assert any("Withdrawn as never defects: D3, D4." in line for line in lines)
    assert any(line.startswith("1 identical finding(s)") for line in lines)
    assert any(f"`{OLD}`" in line and line.startswith("Scope:") for line in lines)
    assert any("1 of 3" in line and "DO NOT SHIP" in line for line in lines)
    assert "- max (attempt 2): absent (timeout): took too long" in lines
    assert "- medium: not selected: pool blocked" in lines
    assert not any(line.startswith("- small: waits") for line in lines)
    assert any(line.startswith("Shadow mode") for line in lines)
    opener = next(index for index, line in enumerate(lines) if line.startswith("<details>"))
    outside = lines.index(f"- **Medium** `{CHECKER}:40`: D8: Outside problem")
    elsewhere = lines.index(f"- **Medium** `{OTHER}:9`: Elsewhere problem")
    assert outside < opener < elsewhere < lines.index("</details>")
    assert final.body.split("\n\n")[-1].startswith(protocol.MARKER_OPEN)
    assert final.reviewer == "small" and final.strikes == 1 and final.pending_cause is None
    assert final.review is None and final.description == "small: SHIP WITH FIXES, 5 defect(s) to fix"


def test_a_follow_up_records_every_open_defect_with_its_next_id(policy: Policy) -> None:
    plan = {**_kitchen_plan(policy), "findings_delivery": "review", "run_id": "a" * 32}
    final = _finalize(policy, plan, _kitchen_verdict(), kind=selection.FAIL)
    record = _record(final)
    assert [item["id"] for item in record["open_defects"]] == ["D5", "D6", "D7", "D8", "D9"]
    assert record["next_id"] == 10 and record["run_uuid"] == "a" * 32
    assert record["forced"] is False and record["summary"] == "" and record["carried"] is False
    assert final.review is not None
    assert sorted(item["body"].split("\n")[0] for item in final.review["comments"]) == ["**Medium**: Inline problem"]


def test_a_forced_round_records_that_it_was_forced(policy: Policy) -> None:
    plan = {**_protocol_plan(policy, forced=True), "disputes": []}
    final = _finalize(policy, plan, _v("fine"))
    record = _record(final)
    assert record["forced"] is True and record["run_uuid"] == plan["run_id"]
    assert _record(_finalize(policy, _protocol_plan(policy), _v("fine")))["forced"] is False


def test_the_review_sections_follow_the_delivery_mode(policy: Policy) -> None:
    verdict = _v("s", [_defect("Inline problem", 2), _defect("Outside problem", 40)])
    comment = _finalize(policy, _comment_plan(policy), verdict)
    review = _finalize(policy, _protocol_plan(policy), verdict)
    assert comment.review is None and review.review is not None
    assert f"- **Medium** `{CHECKER}:2`: D1: Inline problem" in comment.body.split("\n")
    assert f"- **Medium** `{CHECKER}:2`: D1: Inline problem" not in review.body.split("\n")
    assert f"- **Medium** `{CHECKER}:40`: D2: Outside problem" in review.body.split("\n")
    assert "Defects on diff lines" in comment.body and "Defects on diff lines" not in review.body
    assert "Defects without a review thread" in review.body
    assert "Defects outside the diff" in comment.body
    for final in (comment, review):
        _assert_shape(final.body)


def test_a_plan_without_protocol_or_delivery_uses_the_documented_defaults(policy: Policy) -> None:
    plan = _plan(policy, ("small",))
    plan.pop("findings_delivery")
    plan.pop("open_findings")
    plan["protocol"] = {}
    final = _finalize(policy, plan, _v("s", [_finding("Medium")]), kind=selection.FAIL)
    assert final.review is None and final.strikes == 0 and final.pending_cause is None
    assert f"- **Medium** `{CHECKER}:2`: D1: Medium issue" in final.body.split("\n")
    assert protocol.MARKER_OPEN not in final.body
    review = _finalize(policy, {**plan, "findings_delivery": "review"}, _v("s", [_finding("Medium")]))
    assert review.review is not None
    assert "<!-- oracle-defect: c1-D1 -->" in review.review["comments"][0]["body"]


def test_a_protocol_without_counters_starts_them_at_their_defaults(policy: Policy) -> None:
    plan = _protocol_plan(policy)
    for key in ("next_id", "strikes"):
        plan["protocol"].pop(key)
    final = _finalize(policy, plan, _v("s", [_finding("Medium")]), kind=selection.FAIL)
    record = _record(final)
    assert final.strikes == 0 and record["strikes_after"] == 0
    assert [item["id"] for item in record["open_defects"]] == ["D1"] and record["next_id"] == 2
    assert final.review is not None and "c1-D1" in final.review["comments"][0]["body"]
    pending = _finalize(policy, plan, None, kind=selection.PENDING, reviewer=None, reasons=("none: out",))
    assert "DO NOT SHIP decisions" not in pending.body and pending.strikes is None


def test_a_pending_result_with_strikes_repeats_the_count(policy: Policy) -> None:
    plan = _protocol_plan(policy, strikes=2)
    pending = _finalize(policy, plan, None, kind=selection.PENDING, reviewer=None, reasons=("x: out",))
    assert "DO NOT SHIP decisions on this pull request: 2 of 3." in pending.body.split("\n")
    quiet = _finalize(policy, _protocol_plan(policy, strikes=0), None, kind=selection.PENDING, reviewer=None)
    assert "DO NOT SHIP decisions" not in quiet.body
    unlimited = _protocol_plan(policy, strikes=2, max_do_not_ship=0)
    assert "DO NOT SHIP decisions" not in _finalize(policy, unlimited, None, kind=selection.PENDING, reviewer=None).body


def test_a_clean_ship_posts_an_empty_body_but_every_other_result_posts_one(policy: Policy) -> None:
    plan = _protocol_plan(policy)
    ship = _finalize(policy, plan, _v("fine"))
    assert ship.state == "success" and ship.body == "" and ship.reviewer == "small"
    assert ship.review is not None and ship.review["body"].startswith("### oracle-verdict: success")
    fixes = _finalize(policy, plan, _v("s", [_defect("Problem")]), kind=selection.FAIL)
    assert fixes.body != "" and fixes.state == "failure"
    refused = _v("no", [], decision="DO_NOT_SHIP")
    forced_state = _finalize(policy, plan, refused, kind=selection.PASS)
    assert forced_state.state == "success" and forced_state.body != ""
    pending = _finalize(policy, plan, None, kind=selection.PENDING, reviewer=None)
    assert pending.body != "" and pending.state == "pending" and pending.reviewer is None
    assert pending.body.endswith("\n") and not pending.body.endswith("\n\n")
    shipped_pending = _finalize(policy, plan, _v("fine"), kind=selection.PENDING)
    assert shipped_pending.state == "pending" and shipped_pending.body != ""


def test_a_do_not_ship_explains_that_no_defects_are_listed(policy: Policy) -> None:
    verdict = _v("Rework everything", [_defect("Problem")], decision="DO_NOT_SHIP")
    final = _finalize(policy, _protocol_plan(policy, strikes=1), verdict, kind=selection.FAIL)
    lines = final.body.split("\n")
    _assert_shape(final.body)
    assert "Rework everything" in lines and "Decision: **DO NOT SHIP**." in lines
    assert any(line.startswith("No defects are listed for this decision") for line in lines)
    assert f"{CHECKER}:2" not in final.body
    ship = _finalize(policy, _protocol_plan(policy), _v("fine"))
    assert ship.review is not None
    assert "No defects are listed" not in ship.review["body"]
    assert not any(line.startswith("Rework") for line in ship.review["body"].split("\n"))


def test_a_failing_attempt_after_an_absence_uses_the_failing_attempt(policy: Policy) -> None:
    attempts = [Attempt(1, "small", selection.ABSENT, absence="timeout"), Attempt(2, "small", selection.FAIL)]
    verdicts = {2: validate(_v("s", [_defect("Problem")]))}
    step = Step(selection.FAIL, policy.reviewers["small"], ())
    final = report.finalize(_comment_plan(policy), step, [], attempts, verdicts, COMMENTABLE)
    assert final.state == "failure" and "D1: Problem" in final.body and final.reviewer == "small"


def test_a_pass_followed_by_another_reviewer_keeps_the_terminal_reviewers_verdict(policy: Policy) -> None:
    attempts = [Attempt(1, "max", selection.FAIL), Attempt(2, "small", selection.PASS)]
    verdicts = {1: validate(_v("s", [_defect("Wrong")])), 2: validate(_v("fine"))}
    step = Step(selection.PASS, policy.reviewers["small"], ())
    final = report.finalize(_comment_plan(policy), step, [], attempts, verdicts, COMMENTABLE)
    assert final.state == "success" and final.body == ""


def test_unavailable_reviewers_are_listed_after_every_attempt(policy: Policy, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-ant-api03-supersecretvalue123456"
    monkeypatch.setenv(credentials.NAMES[0], secret)
    attempts = [
        Attempt(1, "small", selection.FAIL),
        Attempt(2, "max", selection.ABSENT, absence="quota", detail=f"token {secret} rejected"),
        Attempt(3, "max", selection.ABSENT, absence="timeout"),
    ]
    reasons = ("small: pool blocked until 2026-10-05T12:00:00", "medium: out of budget", "max: retry at 12:00:00")
    verdict = _v("s", [_defect("Problem")])
    final = _finalize(policy, _comment_plan(policy), verdict, kind=selection.FAIL, attempts=attempts, reasons=reasons)
    lines = final.body.split("\n")
    assert secret not in final.body
    assert any(line.startswith("- max (attempt 2): absent (quota): token ") for line in lines)
    assert "- max (attempt 3): absent (timeout)" in lines
    assert "- medium: out of budget" in lines
    assert not any(line.startswith(("- small: pool", "- max: retry")) for line in lines)
    _assert_shape(final.body)


def test_a_pending_selection_that_never_reported_names_the_selected_reviewer(policy: Policy) -> None:
    final = _finalize(
        policy, _comment_plan(policy), None, kind=selection.REVIEW, reviewer="small", attempts=[], reasons=()
    )
    assert final.state == "pending" and final.pending_cause == UNREPORTED
    assert "- small: selected but did not report" in final.body.split("\n")
    assert final.description == "pending: small: selected but did not report"
    nameless = report.finalize(_comment_plan(policy), Step(selection.REVIEW, None, ()), [], [], {}, COMMENTABLE)
    assert nameless.state == "pending" and nameless.pending_cause == UNREPORTED


def test_a_pending_selection_without_reasons_still_says_why(policy: Policy) -> None:
    final = _finalize(policy, _comment_plan(policy), None, kind=selection.PENDING, reviewer=None)
    assert final.description.startswith("pending: ") and len(final.description) > len("pending: ")
    two = _finalize(policy, _comment_plan(policy), None, kind=selection.PENDING, reviewer=None, reasons=("a", "b"))
    assert two.description == "pending: a; b"


def test_an_oversize_plan_keeps_its_header_before_the_size_note(policy: Policy) -> None:
    plan = {**_protocol_plan(policy), "brief_mode": "oversize"}
    final = _finalize(policy, plan, None, kind=selection.PENDING, reviewer=None)
    assert final.pending_cause == OVERSIZE and final.description == report.OVERSIZE_DESCRIPTION
    lines = final.body.split("\n")
    assert lines[0] == f"### oracle-verdict: pending for `{HEAD}`"
    assert report.OVERSIZE_NOTE in lines
    _assert_shape(final.body)


def test_withheld_artifacts_are_listed_in_a_pending_review(policy: Policy) -> None:
    errors = ["attempt 1: wrong run", "attempt 2: wrong head"]
    final = _finalize(policy, _protocol_plan(policy), _v("fine"), errors=errors)
    assert final.state == "pending" and final.pending_cause == INTEGRITY and final.reviewer is None
    assert final.strikes is None and final.description
    lines = final.body.split("\n")
    assert lines[0] == f"### oracle-verdict: pending for `{HEAD}`"
    assert all(error in lines for error in errors)
    assert final.review is not None and final.review["body"] == final.body
    assert set(final.review) == {"commit_id", "event", "body", "comments"}
    assert final.review["event"] == "COMMENT" and final.review["commit_id"] == HEAD
    assert final.review["comments"] == []
    assert _finalize(policy, _comment_plan(policy), _v("fine"), errors=errors).review is None


def test_review_threads_carry_the_defect_and_fingerprint_markers_in_order(policy: Policy) -> None:
    final = _finalize(policy, _protocol_plan(policy), _v("s", [_defect("No detail")]))
    assert final.review is not None
    assert set(final.review) == {"commit_id", "event", "body", "comments"} and final.review["event"] == "COMMENT"
    finding = Finding("Medium", CHECKER, 2, "No detail", "")
    assert final.review["comments"][0]["body"] == (
        f"**Medium**: No detail\n\n<!-- oracle-defect: c1-D1 -->\n\n{dedup.marker(finding)}"
    )
    detailed = _finalize(policy, _protocol_plan(policy), _v("s", [_defect("With detail", 2, "more")]))
    assert detailed.review is not None
    assert detailed.review["comments"][0]["body"].startswith("**Medium**: With detail\n\nmore\n\n<!-- oracle-defect")


def test_a_marker_that_cannot_be_recorded_withholds_the_decision(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "MARKER_LIMIT", 10)
    plan = _protocol_plan(policy, strikes=1)
    plan["open_findings"] = {dedup.fingerprint(CHECKER, "Already open problem"): "Medium"}
    verdict = _v("s", [_defect("New problem", 2, "d"), _defect("Already open problem", 1), _defect("Far", 40)])
    final = _finalize(policy, plan, verdict)
    assert final.state == "pending" and final.pending_cause == MARKER_LIMIT and final.strikes is None
    assert final.description == report.MARKER_LIMIT_DESCRIPTION
    assert final.review is not None
    lines = final.review["body"].split("\n")
    assert lines[0] == f"### oracle-verdict: pending for `{HEAD}`"
    assert report.MARKER_LIMIT_NOTE in lines and protocol.MARKER_OPEN not in final.review["body"]
    assert f"- **Medium** `{CHECKER}`: Already open problem" in lines
    assert f"- **Medium** `{CHECKER}:40`: Far" in lines
    assert any("DO NOT SHIP decisions on this pull request: 1 of 3." in line for line in lines)
    assert not any("oracle-defect" in item["body"] for item in final.review["comments"])
    thread = Finding("Medium", CHECKER, 2, "New problem", "d")
    assert final.review["comments"][0]["body"] == f"**Medium**: New problem\n\nd\n\n{dedup.marker(thread)}"
    _assert_shape(final.review["body"])


def test_the_marker_limit_with_a_do_not_ship_keeps_the_previous_strike_count(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "MARKER_LIMIT", 10)
    verdict = _v("no", [], decision="DO_NOT_SHIP")
    final = _finalize(policy, _protocol_plan(policy, strikes=1), verdict, kind=selection.FAIL)
    assert final.pending_cause == MARKER_LIMIT and final.strikes is None
    assert any("1 of 3" in line for line in final.body.split("\n"))


def _runs(body: str, char: str) -> list[int]:
    return [len(run) for run in re.findall(f"{char}+", body)]


def test_a_capped_body_shortens_every_listed_detail_alike(policy: Policy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "BODY_LIMIT", 3600)
    verdict = _v(
        "s",
        [
            _defect("Inline", 2, "x" * 1000),
            _defect("Outside", 40, "y" * 1000),
            _defect("Elsewhere", 9, "z" * 1000, file=OTHER),
        ],
    )
    plan = _comment_plan(policy, kind="follow-up", round=2, changed=[CHECKER], next_id=1)
    final = _finalize(policy, plan, verdict)
    runs = [max(_runs(final.body, char)) for char in "xyz"]
    assert len(set(runs)) == 1 and 0 < runs[0] < 1000
    assert len(final.body) <= 3600 and report.TRUNCATED in final.body
    assert "Size limit: the detail of 3 of 3 listed defect(s) was shortened" in final.body
    for title in ("Inline", "Outside", "Elsewhere"):
        assert title in final.body
    assert f"`{CHECKER}:40`: D2: Outside" in final.body and f"`{OTHER}:9`: Elsewhere" in final.body
    _assert_shape(final.body)


def test_a_body_with_no_room_for_detail_drops_dispute_evidence_too(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = "e" * 1000
    plan = _protocol_plan(policy, kind="follow-up", round=2, prior=[_prior("D1")], changed=[CHECKER], next_id=2)
    plan["disputes"] = [_dispute("D1")]
    verdict = {**_v("s"), "prior_defects": [{"id": "D1", "status": "fixed", "evidence": evidence}]}
    monkeypatch.setattr(report, "BODY_LIMIT", HUGE)
    full = _finalize(policy, plan, verdict).review["body"]
    assert evidence in full
    limit = len(full) - 300
    monkeypatch.setattr(report, "BODY_LIMIT", limit)
    final = _finalize(policy, plan, verdict)
    assert final.review is not None
    body = final.review["body"]
    assert len(body) <= limit
    line = next(line for line in body.split("\n") if line.startswith("- D1 "))
    assert line.endswith(report.TRUNCATED)
    assert 0 < max(_runs(line, "e")) < 999


def test_description_clipping_keeps_exact_boundaries() -> None:
    assert report.fixed("success", "d" * 140).description == "d" * 140
    clipped = report.fixed("success", "d" * 141).description
    assert clipped == "d" * 139 + "…"


def test_a_fixed_decision_has_no_body_or_review() -> None:
    final = report.fixed("success", "exempt")
    assert (final.state, final.description, final.body, final.review, final.reviewer) == (
        "success",
        "exempt",
        "",
        None,
        None,
    )


def _sized_body(policy: Policy, monkeypatch: pytest.MonkeyPatch, limit: int, details: Sequence[str]) -> str:
    monkeypatch.setattr(report, "BODY_LIMIT", limit)
    verdict = _v(
        "s",
        [
            _defect("Inline", 2, details[0]),
            _defect("Outside", 40, details[1]),
            _defect("Elsewhere", 9, details[2], file=OTHER),
        ],
    )
    plan = _comment_plan(policy, kind="follow-up", round=2, changed=[CHECKER], next_id=1)
    return _finalize(policy, plan, verdict).body


HUGE = 10**9


def _visible(body: str) -> str:
    return "\n".join(line for line in body.split("\n") if not line.startswith(protocol.MARKER_OPEN))


def test_a_body_exactly_at_the_limit_is_left_alone(policy: Policy, monkeypatch: pytest.MonkeyPatch) -> None:
    details = ("Q" * 1000, "J" * 4000, "")
    full = _sized_body(policy, monkeypatch, HUGE, details)
    assert "Q" * 1000 in full and "J" * 4000 in full and report.TRUNCATED not in full
    assert _sized_body(policy, monkeypatch, len(full), details) == full
    shortened = _sized_body(policy, monkeypatch, len(full) - 1, details)
    assert len(shortened) <= len(full) - 1 and report.TRUNCATED in shortened and "Size limit" in shortened


def test_a_capped_body_keeps_the_longest_detail_that_fits(policy: Policy, monkeypatch: pytest.MonkeyPatch) -> None:
    details = ("Q" * 1000, "J" * 1000, "Z" * 1000)
    full = len(_sized_body(policy, monkeypatch, HUGE, details))
    for limit in range(full - 1, full - 400, -7):
        body = _sized_body(policy, monkeypatch, limit, details)
        runs = [max(_runs(body, char), default=0) for char in "QJZ"]
        assert len(set(runs)) == 1 and runs[0] < 1000
        assert limit - 3 < len(body) <= limit


def test_a_body_too_small_for_any_detail_still_lists_every_defect(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _sized_body(policy, monkeypatch, 10, ("Q" * 1000, "J" * 1000, "Z" * 1000))
    visible = _visible(body)
    assert not any(char in visible for char in "QJZ")
    assert body.count(report.TRUNCATED) == 3
    assert "Size limit: the detail of 3 of 3 listed defect(s)" in body
    for title in ("Inline", "Outside", "Elsewhere"):
        assert title in body


def test_the_size_note_counts_only_the_details_that_were_shortened(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = ("J" * 10, "Q" * 3000, "")
    full = len(_sized_body(policy, monkeypatch, HUGE, details))
    body = _sized_body(policy, monkeypatch, full - 500, details)
    assert "Size limit: the detail of 1 of 3 listed defect(s)" in body
    assert "J" * 10 in body and "Q" * 3000 not in body


def test_a_shortened_detail_never_keeps_trailing_whitespace_before_the_marker(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = ("J" * 50 + " " * 300 + "Q" * 600, "", "")
    full = len(_sized_body(policy, monkeypatch, HUGE, details))
    bodies = [_sized_body(policy, monkeypatch, limit, details) for limit in range(full - 1, full - 1000, -3)]
    assert all(f"J {report.TRUNCATED}" not in body for body in bodies)
    assert any(f"{'J' * 50}{report.TRUNCATED}" in body for body in bodies)


def test_dispute_evidence_is_shortened_to_the_longest_run_that_fits(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = len(_evidence_body(policy, monkeypatch, HUGE))
    for limit in range(full - 1, full - 400, -9):
        body = _evidence_body(policy, monkeypatch, limit)
        run = max(_runs(body, "Q"), default=0)
        assert 0 < run < 1000
        assert limit - 1 < len(body) <= limit


def test_dispute_evidence_is_dropped_when_nothing_else_can_give(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _evidence_body(policy, monkeypatch, 10)
    assert "Q" not in _visible(body) and report.TRUNCATED in body


def _evidence_body(policy: Policy, monkeypatch: pytest.MonkeyPatch, limit: int) -> str:
    monkeypatch.setattr(report, "BODY_LIMIT", limit)
    plan = _protocol_plan(policy, kind="follow-up", round=2, prior=[_prior("D1")], changed=[CHECKER], next_id=2)
    plan["disputes"] = [_dispute("D1")]
    verdict = {**_v("s"), "prior_defects": [{"id": "D1", "status": "fixed", "evidence": "Q" * 1000}]}
    final = _finalize(policy, plan, verdict)
    assert final.review is not None
    return final.review["body"]


def _dispute_plan(policy: Policy, *, forced: bool = False, ids: Sequence[str] = ("D1", "D2", "D3", "D4", "D5")):
    prior = [_prior(item) for item in ids]
    extra = {"forced": True} if forced else {}
    plan = _protocol_plan(policy, kind="follow-up", round=2, prior=prior, changed=[CHECKER], next_id=9, **extra)
    plan["disputes"] = [_dispute(item) for item in ids]
    return plan


def _dispute_verdict() -> dict[str, Any]:
    entries = [
        ("D1", "fixed", "ev one"),
        ("D2", "withdrawn", "no citation here"),
        ("D3", "withdrawn", "see pkg/kept.py:12 for proof"),
        ("D4", "not_fixed", ""),
    ]
    return {
        **_v("s"),
        "prior_defects": [{"id": key, "status": value, "evidence": text} for key, value, text in entries],
    }


def _dispositions(final: report.Final) -> dict[str, str]:
    body = final.review["body"] if final.review else final.body
    lines = body.split("\n")
    start = lines.index("**Dispute dispositions**")
    found = {}
    for line in lines[start + 2 :]:
        if not line:
            break
        key, _, rest = line[2:].partition(" ")
        found[key.rstrip(":")] = rest
    return found


def test_dispute_dispositions_report_each_disputed_finding_with_its_effective_status(policy: Policy) -> None:
    final = _finalize(policy, _dispute_plan(policy), _dispute_verdict())
    assert _dispositions(final) == {
        "D1": "(fixed): ev one",
        "D2": "(withdrawn): no citation here",
        "D3": "(withdrawn): see pkg/kept.py:12 for proof",
        "D4": "(maintained)",
        "D5": "no disposition recorded",
    }
    _assert_shape(final.review["body"])


def test_a_forced_round_counts_unproven_fixes_and_withdrawals_as_maintained(policy: Policy) -> None:
    final = _finalize(policy, _dispute_plan(policy, forced=True), _dispute_verdict())
    assert _dispositions(final) == {
        "D1": "(maintained): ev one",
        "D2": "(maintained): no citation here",
        "D3": "(withdrawn): see pkg/kept.py:12 for proof",
        "D4": "(maintained)",
        "D5": "no disposition recorded",
    }


def test_disputes_without_a_verdict_list_no_dispositions(policy: Policy) -> None:
    final = _finalize(policy, _dispute_plan(policy), None, kind=selection.PENDING, reviewer=None)
    assert "Dispute dispositions" not in final.body and final.state == "pending"
    no_disputes = _finalize(policy, _protocol_plan(policy), _v("fine"))
    assert "Dispute dispositions" not in no_disputes.review["body"]


def _previous(decision: str = "SHIP_WITH_FIXES", summary: str = "") -> dict[str, Any]:
    return {
        "v": 1,
        "cycle": 2,
        "round": 3,
        "kind": "follow-up",
        "decision": decision,
        "head_sha": OLD,
        "base_ref": "develop",
        "reviewer": "small",
        "tier": "small",
        "strikes_after": 2,
        "open_defects": [{**_prior("D4", line=1), "end_line": 2}, _prior("D5"), _prior("D4")],
        "next_id": 6,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": summary,
    }


def _carry_plan(policy: Policy, previous: dict[str, Any], **over: Any) -> dict[str, Any]:
    plan = {
        **_protocol_plan(policy, kind="carry", cycle=2, round=3, previous=previous, strikes=2),
        "decision": "carry",
        "reviewed_head": OLD,
    }
    plan.update(over)
    return plan


def test_a_carried_decision_repeats_the_previous_decision_without_running_a_reviewer(policy: Policy) -> None:
    final = report.carried(_carry_plan(policy, _previous(summary="Rework it"), findings_delivery="comment"))
    lines = final.body.split("\n")
    _assert_shape(final.body)
    assert lines[0] == f"### oracle-verdict: failure for `{HEAD}`"
    assert f"Decision: **SHIP WITH FIXES**, carried from head `{OLD}`." in lines
    assert f"- **High** `{CHECKER}:1-2`: D4: Old" in lines and f"- **High** `{CHECKER}:2`: D5: Old" in lines
    assert sum("D4: Old" in line for line in lines) == 1
    assert any("2 of 3" in line and "DO NOT SHIP" in line for line in lines)
    assert final.description == f"carried from {OLD[:12]}: SHIP WITH FIXES"
    assert (final.state, final.strikes, final.reviewer, final.pending_cause) == ("failure", 2, None, None)
    assert final.review is None
    assert final.body.split("\n")[-1].startswith(protocol.MARKER_OPEN)


def test_a_carried_decision_in_review_mode_posts_an_empty_review_with_the_record(policy: Policy) -> None:
    dns = _previous("DO_NOT_SHIP", summary="Rework it")
    final = report.carried(_carry_plan(policy, dns, findings_delivery="review"))
    assert final.review is not None and final.review["body"] == final.body
    assert set(final.review) == {"commit_id", "event", "body", "comments"}
    assert final.review["event"] == "COMMENT" and final.review["commit_id"] == HEAD
    assert final.review["comments"] == []
    record = _record(final)
    assert record["summary"] == "Rework it" and record["carried"] is True and record["kind"] == "carry"
    assert record["reviewer"] == "small" and record["strikes_after"] == 2 and record["next_id"] == 6
    ship = report.carried(_carry_plan(policy, _previous("SHIP"), findings_delivery="review"))
    assert ship.state == "success" and ship.body.startswith(f"### oracle-verdict: success for `{HEAD}`")


def test_a_refusal_states_the_strike_count_and_records_no_reviewer(policy: Policy) -> None:
    plan = {**_protocol_plan(policy, kind="refused", strikes=3, round=0), "decision": "refused"}
    plan["protocol"].pop("next_id")
    final = report.refused(plan)
    lines = final.body.split("\n")
    _assert_shape(final.body)
    assert lines[0] == f"### oracle-verdict: failure for `{HEAD}`"
    assert any("3 of 3" in line and "DO NOT SHIP" in line for line in lines)
    assert (final.state, final.strikes, final.reviewer, final.pending_cause) == ("failure", 3, None, None)
    assert "refused" in final.description and "3" in final.description
    record = _record(final)
    assert record["reviewer"] == "" and record["tier"] == "" and record["next_id"] == 1
    assert record["decision"] == "REFUSED" and record["open_defects"] == [] and record["summary"] == ""
    assert final.review is not None and final.review["body"] == final.body
    assert set(final.review) == {"commit_id", "event", "body", "comments"} and final.review["event"] == "COMMENT"
    assert final.review["commit_id"] == HEAD
    assert report.refused({**plan, "findings_delivery": "comment"}).review is None


def test_the_success_status_carries_the_run_identity_inside_the_description_limit() -> None:
    plan = {"status_context": "oracle-verdict", "run_id": "a" * 32}
    suffix = f" [run {'a' * 32}]"
    room = report.DESCRIPTION_LIMIT - len(suffix)
    exact = report.Final("success", "d" * room, "", None, None)
    assert report.status_payload(plan, exact, "url")["description"] == "d" * room + suffix
    over = report.Final("success", "d" * (room + 1), "", None, None)
    clipped = report.status_payload(plan, over, "url")["description"]
    assert clipped == "d" * (room - 1) + "…" + suffix and len(clipped) == report.DESCRIPTION_LIMIT


@pytest.mark.parametrize(
    ("state", "plan"),
    [("failure", {"run_id": "u"}), ("pending", {"run_id": "u"}), ("success", {"run_id": ""})],
)
def test_only_a_success_with_a_run_identity_gets_the_run_suffix(state: str, plan: dict[str, str]) -> None:
    payload = report.status_payload({"status_context": "ctx", **plan}, report.Final(state, "desc", "", None, None), "t")
    assert payload["description"] == "desc"


def test_a_reviewer_effort_is_named_only_when_the_chain_sets_one(policy: Policy) -> None:
    plan = _comment_plan(policy)
    plan["chain"] = [{**plan["chain"][0], "effort": "high"}]
    verdict = _v("s", [_defect("Problem")])
    final = _finalize(policy, plan, verdict, kind=selection.FAIL)
    assert "Reviewer: small (role review, omp @smol, effort high)." in final.body.split("\n")
    plan["chain"] = [{**plan["chain"][0], "effort": ""}]
    quiet = _finalize(policy, plan, verdict, kind=selection.FAIL)
    assert "Reviewer: small (role review, omp @smol)." in quiet.body.split("\n")
    plan["chain"] = [{**plan["chain"][0], "name": "other"}]
    unlisted = _finalize(policy, plan, verdict, kind=selection.FAIL)
    assert "Reviewer:" not in unlisted.body


def test_an_unpatterned_credential_from_the_environment_never_reaches_the_absence_list(
    policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "plain-credential-value-77"
    monkeypatch.setenv(credentials.NAMES[0], secret)
    attempts = [
        Attempt(1, "small", selection.FAIL),
        Attempt(2, "max", selection.ABSENT, absence="error", detail=f"auth {secret} refused"),
    ]
    verdict = _v("s", [_defect("Problem")])
    final = _finalize(policy, _comment_plan(policy), verdict, kind=selection.FAIL, attempts=attempts)
    assert secret not in final.body and "- max (attempt 2): absent (error): auth " in final.body


def test_a_withheld_review_lists_its_errors_under_a_real_title(policy: Policy) -> None:
    final = _finalize(policy, _protocol_plan(policy), _v("fine"), errors=["attempt 1: wrong run"])
    lines = final.body.split("\n")
    assert "None" not in final.body
    title = lines.index("attempt 1: wrong run") - 2
    assert lines[title].startswith("**") and lines[title].endswith("**") and lines[title + 1] == ""


def test_the_thread_markers_carry_the_cycle_of_the_plan(policy: Policy) -> None:
    final = _finalize(policy, _protocol_plan(policy, cycle=4), _v("s", [_defect("Problem")]), kind=selection.FAIL)
    assert final.review is not None
    assert "<!-- oracle-defect: c4-D1 -->" in final.review["comments"][0]["body"]
    assert _record(final)["cycle"] == 4


def test_a_refusal_keeps_the_next_defect_number_of_the_plan(policy: Policy) -> None:
    plan = {**_protocol_plan(policy, kind="refused", strikes=3, round=0, next_id=7), "decision": "refused"}
    record = _record(report.refused(plan))
    assert record["next_id"] == 7


@pytest.mark.parametrize("build", ["carried", "refused"])
def test_a_protocol_decision_separates_its_text_from_the_record_by_one_blank_line(policy: Policy, build: str) -> None:
    if build == "carried":
        final = report.carried(_carry_plan(policy, _previous(), findings_delivery="comment"))
    else:
        final = report.refused({**_protocol_plan(policy, kind="refused", strikes=3, round=0), "decision": "refused"})
    lines = final.body.split("\n")
    assert lines[-1].startswith(protocol.MARKER_OPEN) and lines[-2] == "" and lines[-3] != ""
