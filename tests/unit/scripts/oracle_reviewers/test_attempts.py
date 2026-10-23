from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import attempts, cli, report, retry, selection
from _project.scripts.oracle_reviewers.absence import Absence
from _project.scripts.oracle_reviewers.classify import Classification
from _project.scripts.oracle_reviewers.dedup import fingerprint, marker
from _project.scripts.oracle_reviewers.diff import commentable_lines
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.retry import ALL_ABSENT, INTEGRITY, OVERSIZE, UNREPORTED
from _project.scripts.oracle_reviewers.selection import Attempt, SelectionInput, Step
from _project.scripts.oracle_reviewers.verdict import Finding, validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def policy(policy: Policy) -> Policy:
    return policy


CHECKER = "benchbox/core/equivalence/checker.py"

RUN_ID = "4242"
OUTSIDE_NOTE = "Defects outside the diff count like the others: fix them before merge."
HEAD = "a" * 40
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DIFF = """diff --git a/benchbox/core/equivalence/checker.py b/benchbox/core/equivalence/checker.py
--- a/benchbox/core/equivalence/checker.py
+++ b/benchbox/core/equivalence/checker.py
@@ -1,1 +1,2 @@
 a = 1
+b = 2
"""


def _plan(policy: Policy, buckets: tuple[str, ...] = ("small", "medium")) -> dict:
    return {
        "schema": 1,
        "mode": "shadow",
        "status_context": policy.status_context,
        "bot_login": policy.bot_login,
        "findings_delivery": "comment",
        "repo": "BenchBox-dev/BenchBox",
        "pr": 7,
        "run_id": RUN_ID,
        "base_sha": "b" * 40,
        "base_ref": "develop",
        "head_sha": HEAD,
        "decision": "review",
        "decision_reason": "",
        "max_defects": policy.protocol.max_defects,
        "brief_max_bytes": policy.brief_max_bytes,
        "evidence_files": [CHECKER],
        "chain": [policy.reviewers[bucket].to_json() for bucket in buckets],
        "buckets": dict(policy.buckets),
        "follow_up_reviewer": None,
        "scope_paths": [CHECKER],
        "gate_paths": [],
        "scope_reasons": [],
        "reviewed_files": [
            {"path": CHECKER, "previous_path": CHECKER, "additions": 1, "deletions": 0, "status": "modified"}
        ],
        "unchanged_files": [],
        "untracked_files": [],
        "restart_summary": None,
        "diff_inline": DIFF,
        "brief_mode": "inline",
        "scope": "full",
        "reviewed_head": None,
        "open_findings": {},
        "protocol": {
            "kind": "first",
            "reason": "first review of this pull request",
            "cycle": 1,
            "round": 1,
            "base_ref": "develop",
            "patch_map": {},
            "patch_digest": "d" * 32,
            "strikes": 0,
            "max_do_not_ship": 3,
            "prior": [],
            "next_id": 1,
            "changed": None,
            "previous": None,
        },
        "max_attempts": policy.max_attempts,
        "pool_blocked_until": {},
        "manual": False,
        "previous_state": None,
    }


def _classification(bucket: str = "small") -> Classification:
    return Classification(
        size_bucket=bucket, capability="routine", template="standard", confidence=0.9, gate_flags=(), files=()
    )


def _finding(severity: str, line: int = 2) -> dict[str, Any]:
    return {
        "severity": severity,
        "file": "benchbox/core/equivalence/checker.py",
        "line": line,
        "title": f"{severity} issue",
        "detail": "see @someone at https://example.com",
    }


def _v(summary: str = "", findings: list[dict[str, Any]] | None = None, decision: str | None = None) -> dict:
    defects = findings or []
    return {
        "status": "complete",
        "incomplete_reason": "",
        "decision": decision or ("SHIP_WITH_FIXES" if defects else "SHIP"),
        "summary": summary,
        "files_examined": [CHECKER],
        "defects": defects,
        "prior_defects": [],
    }


def _write(
    directory: Path,
    slot: int,
    reviewer: str,
    *,
    verdict: dict | None = None,
    missing: Absence | None = None,
    **over: Any,
) -> None:
    artifact = attempts.build_artifact(
        run_id=RUN_ID, pr=7, head_sha=HEAD, slot=slot, reviewer=reviewer, verdict=verdict, missing=missing
    )
    artifact.update(over)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / attempts.artifact_name(slot)).write_text(json.dumps(artifact), encoding="utf-8")


def _decide(policy: Policy, plan: dict, directory: Path):
    loaded = attempts.load(directory, plan, RUN_ID)
    step, errors = attempts.decide(SelectionInput.from_plan(plan, NOW), loaded)
    return loaded, step, errors


def test_absence_then_clean_verdict_passes(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "findings_delivery": "review"}
    _write(tmp_path, 1, "small", missing=Absence("timeout", "slow"))
    _write(tmp_path, 2, "medium", verdict=_v("ok"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert errors == []
    assert step.kind == selection.PASS
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "success"
    assert final.description.endswith(": SHIP")
    assert final.review is not None and final.review["comments"] == []
    assert "Decision: **SHIP**." in final.review["body"]
    assert "small: absent (timeout)" in final.review["body"]


def test_ship_with_fixes_fails_and_splits_defects(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    findings = [_finding("High", 2), _finding("Low", 40)]
    _write(tmp_path, 1, "small", verdict=_v("bad", findings))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert step.kind == selection.FAIL
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.state == "failure"
    assert final.description == "small: SHIP WITH FIXES, 2 defect(s) to fix"
    inline, outside = final.body.split("**Defects outside the diff**")
    assert "checker.py:2`" in inline and "checker.py:40`" in outside
    assert "@someone" not in final.body and "https://" not in final.body
    assert "Shadow mode" in final.body


def test_review_delivery_builds_line_threads_only_inside_the_diff(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "findings_delivery": "review"}
    _write(tmp_path, 1, "small", verdict=_v("bad", [_finding("High", 2), _finding("Low", 40)]))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.review is not None
    assert final.review["commit_id"] == HEAD
    assert [(item["path"], item["line"], item["side"]) for item in final.review["comments"]] == [
        ("benchbox/core/equivalence/checker.py", 2, "RIGHT")
    ]
    assert "checker.py:40`" in final.review["body"]


def test_artifact_from_another_run_or_head_is_rejected(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("", []), run_id="1")
    _, _, errors = _decide(policy, plan, tmp_path)
    assert any("another run" in error for error in errors)
    other = tmp_path / "head"
    _write(other, 1, "small", verdict=_v("", []), head_sha="c" * 40)
    loaded, step, errors = _decide(policy, plan, other)
    assert any("another head" in error for error in errors)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending"
    assert final.pending_cause == INTEGRITY


def test_out_of_order_reviewer_fails_the_replay(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "medium", verdict=_v("", []))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert errors == ["slot 1 ran medium, but the policy selects small"]
    assert report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {}).state == "pending"


def test_reviewer_outside_the_chain_is_rejected(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy, ("max",))
    _write(tmp_path, 1, "small", verdict=_v("", []))
    _, _, errors = _decide(policy, plan, tmp_path)
    assert any("outside the bucket chain" in error for error in errors)


def test_invalid_verdict_counts_as_absent(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("", [{"severity": "Severe"}]))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert errors == []
    assert loaded.attempts[0].absence == "invalid"
    assert step.reviewer is not None and step.reviewer.name == "small"


def test_an_incomplete_verdict_in_an_artifact_never_passes(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    incomplete = {**_v("could not read"), "status": "incomplete", "decision": "NONE", "incomplete_reason": "no access"}
    _write(tmp_path, 1, "small", verdict=incomplete)
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert errors == []
    assert loaded.attempts[0] == Attempt(1, "small", selection.ABSENT, "incomplete", "no access")
    assert loaded.verdicts == {}
    assert step.kind == selection.REVIEW and step.reviewer is not None and step.reviewer.name == "small"


def test_all_absent_is_pending_and_names_every_reviewer(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", missing=Absence("timeout", "slow"))
    _write(tmp_path, 2, "medium", missing=Absence("quota", "limit"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending"
    assert final.description.startswith("pending: small: absent (timeout")
    assert len(final.description) <= 140
    assert "small: absent (timeout)" in final.body and "medium: absent (quota)" in final.body
    assert final.pending_cause == ALL_ABSENT


def test_selected_reviewer_that_never_reported_is_pending(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert step.kind == selection.REVIEW
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending"
    assert "selected but did not report" in final.body
    assert final.pending_cause == UNREPORTED


def _run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *argv: str) -> dict[str, str]:
    output = tmp_path / "github-output.txt"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli.main(list(argv)) == 0
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_cli_select_finalize_and_ensure_attempt(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text(json.dumps(_plan(policy, ("small",))), encoding="utf-8")
    (plan_dir / "diff.patch").write_text(DIFF, encoding="utf-8")
    (plan_dir / "classification.json").write_text(json.dumps(_classification("small").to_json()), encoding="utf-8")
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()
    plan_path = str(plan_dir / "plan.json")
    classification_path = str(plan_dir / "classification.json")
    monkeypatch.setattr(cli, "_classify_plan", lambda plan: _classification("small"))
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "select",
        "--plan",
        plan_path,
        "--attempts-dir",
        str(attempts_dir),
        "--slot",
        "1",
        "--classification",
        classification_path,
    )
    assert out == {"reviewer": "small", "harness": "omp"}
    _run_cli(
        monkeypatch,
        tmp_path,
        "ensure-attempt",
        "--plan",
        plan_path,
        "--slot",
        "1",
        "--reviewer",
        "small",
        "--out-dir",
        str(attempts_dir),
    )
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "select",
        "--plan",
        plan_path,
        "--attempts-dir",
        str(attempts_dir),
        "--slot",
        "2",
        "--classification",
        classification_path,
    )
    assert out == {"reviewer": "", "harness": ""}
    _write(attempts_dir, 1, "small", verdict=_v("", [_finding("Critical")]))
    final_dir = tmp_path / "final"
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        plan_path,
        "--attempts-dir",
        str(attempts_dir),
        "--out-dir",
        str(final_dir),
        "--classification",
        classification_path,
    )
    assert out == {"state": "failure", "comment": "true", "review": "false", "has_state": "true"}
    status = json.loads((final_dir / "status.json").read_text(encoding="utf-8"))
    assert status["context"] == "oracle-verdict"
    assert status["state"] == "failure"
    assert status["target_url"].endswith(f"/actions/runs/{RUN_ID}")
    state = json.loads((final_dir / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["head_sha"] == HEAD and state["outcome"] == "failure"
    assert state["pending_cause"] is None
    comment = json.loads((final_dir / "comment.json").read_text(encoding="utf-8"))
    assert "Defects on diff lines" in comment["body"]


def test_cli_finalize_keeps_carriage_returns_inside_diff_lines(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text(json.dumps(_plan(policy, ("small",))), encoding="utf-8")
    with (plan_dir / "diff.patch").open("w", encoding="utf-8", newline="") as handle:
        handle.write(DIFF.replace("@@ -1,1 +1,2 @@", "@@ -1,1 +1,3 @@").replace("+b = 2\n", "+b = 2\r+c = 3\n"))
    (plan_dir / "classification.json").write_text(json.dumps(_classification("small").to_json()), encoding="utf-8")
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()
    _write(attempts_dir, 1, "small", verdict=_v("", [_finding("Critical", line=3)]))
    final_dir = tmp_path / "final"
    _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_dir / "plan.json"),
        "--attempts-dir",
        str(attempts_dir),
        "--out-dir",
        str(final_dir),
        "--classification",
        str(plan_dir / "classification.json"),
    )
    comment = json.loads((final_dir / "comment.json").read_text(encoding="utf-8"))
    assert "Defects on diff lines" not in comment["body"]


def test_cli_finalize_refuses_a_plan_from_another_run(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({**_plan(policy), "run_id": "1"}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    with pytest.raises(SystemExit, match="another run"):
        cli.main(
            ["finalize", "--plan", str(plan_path), "--attempts-dir", str(tmp_path), "--out-dir", str(tmp_path / "o")]
        )


@pytest.mark.parametrize(("decision", "state"), [("success", "success"), ("fork", "pending")])
def test_cli_finalize_fixed_decisions(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, state: str
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({**_plan(policy), "decision": decision}), encoding="utf-8")
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(tmp_path),
        "--out-dir",
        str(tmp_path / "o"),
    )
    assert out["state"] == state and out["has_state"] == "false" and out["comment"] == "false"
    status = json.loads((tmp_path / "o" / "status.json").read_text(encoding="utf-8"))
    assert status["description"] == ("only exempt paths changed" if decision == "success" else "fork: owner review")


def test_verdict_comes_from_the_terminal_reviewer_slot(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    decisive = validate(_v("decisive", [_finding("High", 2)]))
    later = validate(_v("later", [_finding("Low", 40)]))
    small = Step(selection.FAIL, policy.reviewers["small"], ("small: blocking findings",))
    attempts_list = [Attempt(1, "small", selection.FAIL), Attempt(2, "medium", selection.PASS)]
    final = report.finalize(plan, small, [], attempts_list, {1: decisive, 2: later}, commentable_lines(DIFF))
    assert final.state == "failure"
    assert "decisive" in final.body and "later" not in final.body
    assert "checker.py:2`" in final.body and "checker.py:40`" not in final.body
    assert final.pending_cause is None


def test_defect_outside_the_diff_still_fails(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("bad", [_finding("Critical", 400)]))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert step.kind == selection.FAIL
    assert final.state == "failure"
    assert "Defects on diff lines" not in final.body
    assert "checker.py:400`" in final.body.split("**Defects outside the diff**")[1]
    assert OUTSIDE_NOTE in final.body


def _review_final(policy: Policy, tmp_path: Path, findings: list[dict[str, Any]], bucket: str = "small"):
    plan = {**_plan(policy, (bucket,)), "findings_delivery": "review"}
    _write(tmp_path, 1, bucket, verdict=_v("s", findings))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    return report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))


def test_review_delivery_opens_a_thread_for_every_defect_on_a_diff_line(policy: Policy, tmp_path: Path) -> None:
    findings = [_finding("Critical", 2), _finding("High", 1), _finding("Medium", 2), _finding("Low", 1)]
    final = _review_final(policy, tmp_path, findings)
    assert final.review is not None
    assert [item["body"].split("\n", 1)[0] for item in final.review["comments"]] == [
        "**Critical**: Critical issue",
        "**High**: High issue",
        "**Medium**: Medium issue",
        "**Low**: Low issue",
    ]
    assert OUTSIDE_NOTE not in final.body


@pytest.mark.parametrize("bucket", ["small", "medium"])
def test_severity_never_gates_a_listed_defect(policy: Policy, tmp_path: Path, bucket: str) -> None:
    final = _review_final(policy, tmp_path, [_finding("Low", 2)], bucket=bucket)
    assert final.state == "failure"
    assert "Decision: **SHIP WITH FIXES**." in final.body


def test_review_delivery_keeps_a_defect_outside_the_diff_in_the_body(policy: Policy, tmp_path: Path) -> None:
    final = _review_final(policy, tmp_path, [_finding("High", 400)])
    assert final.review is not None and final.review["comments"] == []
    assert final.state == "failure"
    assert OUTSIDE_NOTE in final.body


def test_comment_delivery_still_lists_every_finding_on_a_diff_line(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("s", [_finding("High", 2), _finding("Low", 1)]))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.review is None
    assert "Defects on diff lines" in final.body
    assert "Defects outside the diff" not in final.body and "Defects without a review thread" not in final.body
    assert "checker.py:1`: D2: Low issue" in final.body


def test_review_comment_spans_a_fully_commentable_range(policy: Policy, tmp_path: Path) -> None:
    final = _review_final(policy, tmp_path, [{**_finding("High", 1), "end_line": 2}])
    assert final.review is not None
    comment = final.review["comments"][0]
    assert {key: comment[key] for key in ("path", "start_line", "start_side", "line", "side")} == {
        "path": CHECKER,
        "start_line": 1,
        "start_side": "RIGHT",
        "line": 2,
        "side": "RIGHT",
    }


@pytest.mark.parametrize("end_line", [3, 40, 5000])
def test_review_comment_falls_back_to_one_line_when_the_range_leaves_the_diff(
    policy: Policy, tmp_path: Path, end_line: int
) -> None:
    final = _review_final(policy, tmp_path, [{**_finding("High", 1), "end_line": end_line}])
    assert final.review is not None
    comment = final.review["comments"][0]
    assert (comment["line"], comment["side"]) == (1, "RIGHT")
    assert "start_line" not in comment and "start_side" not in comment


def test_a_finding_without_end_line_posts_one_line(policy: Policy, tmp_path: Path) -> None:
    final = _review_final(policy, tmp_path, [_finding("High", 2)])
    assert final.review is not None
    assert "start_line" not in final.review["comments"][0]


def test_finding_lines_show_the_range_in_the_body(policy: Policy, tmp_path: Path) -> None:
    final = _review_final(policy, tmp_path, [{**_finding("High", 400), "end_line": 410}])
    assert f"`{CHECKER}:400-410`" in final.body


def test_the_fingerprint_ignores_the_line_range() -> None:
    single = Finding("High", CHECKER, 5, "Wrong constant", "d")
    ranged = Finding("High", CHECKER, 9, "Wrong constant", "d", end_line=14)
    assert marker(single) == marker(ranged)


def test_review_threads_carry_a_fingerprint_marker(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "findings_delivery": "review"}
    _write(tmp_path, 1, "small", verdict=_v("bad", [_finding("High", 2)]))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.review is not None
    assert f"<!-- oracle-finding: {fingerprint(CHECKER, 'High issue')} -->" in final.review["comments"][0]["body"]


def test_findings_already_open_are_not_posted_again_but_still_block(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "findings_delivery": "review", "open_findings": [fingerprint(CHECKER, "High issue")]}
    findings = [_finding("High", 2), _finding("Critical", 2)]
    _write(tmp_path, 1, "small", verdict=_v("bad", findings))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.state == "failure"
    assert final.review is not None
    assert [item["body"].split("\n", 1)[0] for item in final.review["comments"]] == ["**Critical**: Critical issue"]
    assert "**Defects already open as review threads, not posted again**" in final.body
    assert f"- **High** `{CHECKER}`: D1: High issue" in final.body


def test_second_run_with_every_finding_open_adds_no_thread(policy: Policy, tmp_path: Path) -> None:
    findings = [_finding("High", 2), _finding("Low", 40)]
    prints = [fingerprint(CHECKER, "High issue"), fingerprint(CHECKER, "Low issue")]
    plan = {**_plan(policy), "findings_delivery": "review", "open_findings": prints}
    _write(tmp_path, 1, "small", verdict=_v("bad", findings))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable_lines(DIFF))
    assert final.review is not None and final.review["comments"] == []
    assert "checker.py:40`" not in final.body


def test_a_pending_run_posts_its_diagnostics_as_a_review(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan = {**_plan(policy, ("small",)), "findings_delivery": "review"}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    classification_path = tmp_path / "classification.json"
    classification_path.write_text(json.dumps(_classification("small").to_json()), encoding="utf-8")
    attempts_dir = tmp_path / "attempts"
    _write(attempts_dir, 1, "small", missing=Absence("auth", "bad key"))
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(attempts_dir),
        "--out-dir",
        str(tmp_path / "o"),
        "--classification",
        str(classification_path),
    )
    assert out["state"] == "pending" and out["review"] == "true" and out["comment"] == "false"
    review = json.loads((tmp_path / "o" / "review.json").read_text(encoding="utf-8"))
    assert review["body"].startswith(f"### oracle-verdict: pending for `{HEAD}`")
    assert review["comments"] == [] and "small: absent (auth)" in review["body"]


def test_withheld_result_is_posted_as_a_pending_review(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "findings_delivery": "review"}
    _write(tmp_path, 1, "small", verdict=_v("", []), run_id="1")
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending" and final.review is not None
    assert final.review["body"].startswith(f"### oracle-verdict: pending for `{HEAD}`")


def test_an_oversize_plan_finalizes_pending_for_its_size_and_names_the_ways_forward(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {**_plan(policy, ("max",)), "brief_mode": "oversize", "findings_delivery": "review"}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(attempts_dir),
        "--out-dir",
        str(tmp_path / "o"),
    )
    assert out["state"] == "pending" and out["review"] == "true"
    state = json.loads((tmp_path / "o" / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["pending_cause"] == OVERSIZE
    status = json.loads((tmp_path / "o" / "status.json").read_text(encoding="utf-8"))
    assert status["description"] == report.OVERSIZE_DESCRIPTION
    assert len(status["description"]) <= 140 and "stand-in" in status["description"]
    body = json.loads((tmp_path / "o" / "review.json").read_text(encoding="utf-8"))["body"]
    assert "Split it into smaller pull requests" in body and "stand-in review of this head" in body
    previous = retry.State.from_json(state)
    for manual in (True, False):
        decision = retry.decide_rerun(
            manual=manual, new_diff=False, head_sha=HEAD, previous=previous, now=NOW, rules=policy.retry
        )
        assert not decision.allowed and "pending for oversize" in decision.reason
    later = NOW + timedelta(days=1)
    assert not retry.due_for_retry(previous, HEAD, later, policy.retry)
    assert retry.decide_rerun(
        manual=False, new_diff=True, head_sha=HEAD, previous=previous, now=NOW, rules=policy.retry
    ).allowed


def test_an_inline_plan_with_every_reviewer_absent_stays_retryable(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", missing=Absence("timeout", "slow"))
    _write(tmp_path, 2, "medium", missing=Absence("auth", "bad key"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.pending_cause == ALL_ABSENT
    assert report.OVERSIZE_NOTE not in final.body


def test_a_held_plan_finalizes_to_a_pending_status_with_no_decision_or_marker(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = "the oracle's review history could not be read: down"
    plan = {**_plan(policy), "findings_delivery": "review", "decision": "hold", "decision_reason": reason}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(tmp_path),
        "--out-dir",
        str(tmp_path / "o"),
    )
    assert out == {"state": "pending", "comment": "false", "review": "false", "has_state": "false"}
    status = json.loads((tmp_path / "o" / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "pending" and status["description"] == reason
    assert sorted(path.name for path in (tmp_path / "o").iterdir()) == ["status.json"]


def test_a_long_status_description_is_cut_to_the_status_limit(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", missing=Absence("timeout", "limit " * 60))
    _write(tmp_path, 2, "medium", missing=Absence("auth", "bad key"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    full = "pending: " + "; ".join(step.reasons)
    assert len(full) > report.DESCRIPTION_LIMIT
    assert len(final.description) == report.DESCRIPTION_LIMIT == 140
    assert final.description == full[:139] + "…"
    assert report.status_payload(plan, final, "u")["description"] == final.description


def test_selection_maps_the_bucket_through_the_policy(policy: Policy) -> None:
    plan = {**_plan(policy, ("trivial", "small", "medium")), "buckets": {"small": "medium"}}
    chain = cli._selection(plan, _classification("small")).chain
    assert [reviewer.name for reviewer in chain] == ["medium"]


def test_a_follow_up_keeps_the_previous_reviewer_and_ignores_the_bucket(policy: Policy) -> None:
    plan = {**_plan(policy, ("small", "large")), "follow_up_reviewer": "large"}
    assert [reviewer.name for reviewer in cli._selection(plan, _classification("small")).chain] == ["large"]


def test_a_follow_up_from_a_retired_reviewer_falls_back_to_the_bucket(policy: Policy) -> None:
    plan = {**_plan(policy, ("small", "large")), "follow_up_reviewer": None}
    plan["protocol"] = {**plan["protocol"], "kind": "follow-up"}
    assert [reviewer.name for reviewer in cli._selection(plan, _classification("small")).chain] == ["small"]


@pytest.mark.parametrize("error", [KeyError("size"), ValueError("unknown size bucket 'xl'"), TypeError("bad")])
def test_a_malformed_jev_answer_leaves_the_slot_pending(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(policy, ("small",))), encoding="utf-8")

    def broken(plan: dict[str, Any]) -> Classification:
        raise error

    monkeypatch.setattr(cli, "_classify_plan", broken)
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "select",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(tmp_path / "attempts"),
        "--slot",
        "1",
        "--classification",
        str(tmp_path / "classification.json"),
    )
    assert out == {"reviewer": "", "harness": ""}
