from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import attempts, cli, omp_pin, protocol, render, report, retry, selection
from _project.scripts.oracle_reviewers.absence import Absence
from _project.scripts.oracle_reviewers.classify import Classification
from _project.scripts.oracle_reviewers.dedup import fingerprint, marker
from _project.scripts.oracle_reviewers.diff import commentable_lines
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.recovery import ESCALATE, JEV, RETRY, Recovery
from _project.scripts.oracle_reviewers.retry import ALL_ABSENT, INTEGRITY, OVERSIZE, UNREPORTED
from _project.scripts.oracle_reviewers.selection import Attempt, SelectionInput, Step
from _project.scripts.oracle_reviewers.verdict import Finding, VerdictError, validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def policy(policy: Policy) -> Policy:
    return policy


CHECKER = "benchbox/core/equivalence/checker.py"

RUN_ID = "4242"
OUTSIDE_NOTE = "Defects outside the diff count like the others: fix them before merge."
HEAD = "a" * 40
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
JEV_RETRY = Recovery(RETRY, JEV, RETRY, 0.9, 0.8)
JEV_ESCALATE = Recovery(ESCALATE, JEV, ESCALATE, 0.9, 0.8)
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
        "omp_sha256": omp_pin.sha256_of(Path(sys.executable)),
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
    recovered: Recovery | None = None,
    **over: Any,
) -> None:
    artifact = attempts.build_artifact(
        run_id=RUN_ID,
        pr=7,
        head_sha=HEAD,
        slot=slot,
        reviewer=reviewer,
        verdict=verdict,
        missing=missing,
        recovered=recovered,
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
    assert "small (attempt 1): absent (timeout)" in final.review["body"]


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
    assert step.kind == selection.PENDING and step.reviewer is None
    _write(tmp_path, 1, "small", verdict=_v("", [{"severity": "Severe"}]), recovered=JEV_RETRY)
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert errors == []
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
    assert "small (attempt 1): absent (timeout): slow" in final.body
    assert "medium (attempt 2): absent (quota): limit" in final.body
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


def test_a_finalized_exempt_success_never_satisfies_a_gate_whose_rules_require_a_review(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({**_plan(policy), "decision": "success", "run_uuid": "1" * 32}), encoding="utf-8")
    _run_cli(
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
    status = json.loads((tmp_path / "o" / "status.json").read_text(encoding="utf-8"))
    posted = {
        **status,
        "id": 1,
        "login": f"{policy.bot_login}[bot]",
        "user_type": "Bot",
        "created_at": "2026-10-05T12:00:00Z",
    }
    monkeypatch.syspath_prepend(str(Path(cli.__file__).resolve().parents[1]))
    gate = importlib.import_module("_project.scripts.oracle_review_check")
    rules = gate.path_matcher(2, 2, *gate._review_rules())
    code, message = gate.decide(HEAD, [CHECKER], [posted], [], rules)
    assert code == gate.WAITING and "reports only exempt paths changed" in message
    assert gate.decide(HEAD, ["notes.md"], [posted], [], rules) == (
        gate.PASS,
        "oracle-review: only exempt paths changed",
    )


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
    assert review["comments"] == [] and "small (attempt 1): absent (auth)" in review["body"]


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


def test_a_success_status_carries_the_run_uuid_for_direct_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    from _project.scripts.oracle_reviewers import report

    plan = {"status_context": "oracle-verdict", "run_uuid": "a" * 32}
    final = report.fixed(report.SUCCESS, "large: SHIP")
    payload = report.status_payload(plan, final, "u")
    assert payload["description"] == f"large: SHIP [run {'a' * 32}]"
    assert len(payload["description"]) <= 140


def test_a_long_success_description_still_fits_with_the_run_uuid() -> None:
    from _project.scripts.oracle_reviewers import report

    plan = {"status_context": "oracle-verdict", "run_uuid": "a" * 32}
    final = report.fixed(report.SUCCESS, "x" * 200)
    payload = report.status_payload(plan, final, "u")
    assert payload["description"].endswith(f"[run {'a' * 32}]")
    assert len(payload["description"]) <= 140


def test_a_status_without_a_plan_run_uuid_is_unchanged() -> None:
    from _project.scripts.oracle_reviewers import report

    plan = {"status_context": "oracle-verdict"}
    final = report.fixed(report.SUCCESS, "large: SHIP")
    assert report.status_payload(plan, final, "u")["description"] == "large: SHIP"


def test_selection_maps_the_bucket_through_the_policy(policy: Policy) -> None:
    plan = {**_plan(policy, ("trivial", "small", "medium")), "buckets": {"small": "medium"}}
    chain = cli._selection(plan, _classification("small")).chain
    assert [reviewer.name for reviewer in chain] == ["medium"]


def test_a_follow_up_keeps_a_stronger_previous_reviewer_over_a_smaller_bucket(policy: Policy) -> None:
    plan = {**_plan(policy, ("small", "large")), "follow_up_reviewer": "large"}
    assert [reviewer.name for reviewer in cli._selection(plan, _classification("small")).chain] == ["large"]


def test_a_follow_up_into_a_larger_bucket_starts_at_that_bucket(policy: Policy) -> None:
    plan = {**_plan(policy, ("trivial", "small", "medium", "large")), "follow_up_reviewer": "small"}
    plan["protocol"] = {**plan["protocol"], "kind": "follow-up"}
    gated = Classification(
        size_bucket="large",
        capability="routine",
        template="gate",
        confidence=0.9,
        gate_flags=("_project/scripts/oracle_reviewers/*",),
        files=(),
    )
    assert [reviewer.name for reviewer in cli._selection(plan, gated).chain] == ["large"]


def test_a_follow_up_from_a_retired_reviewer_falls_back_to_the_bucket(policy: Policy) -> None:
    plan = {**_plan(policy, ("small", "large")), "follow_up_reviewer": None}
    plan["protocol"] = {**plan["protocol"], "kind": "follow-up"}
    assert [reviewer.name for reviewer in cli._selection(plan, _classification("small")).chain] == ["small", "large"]


@pytest.mark.parametrize(
    "bucket, follow_up, expected",
    [
        ("small", None, ["small", "medium", "large"]),
        ("trivial", None, ["trivial", "medium", "large"]),
        ("small", "medium", ["medium", "large"]),
    ],
)
def test_selection_starts_at_the_chosen_reviewer_and_escalates_to_other_models(
    policy: Policy, bucket: str, follow_up: str | None, expected: list[str]
) -> None:
    plan = {**_plan(policy, ("trivial", "small", "medium", "large")), "follow_up_reviewer": follow_up}
    assert [reviewer.name for reviewer in cli._selection(plan, _classification(bucket)).chain] == expected


def test_two_invalid_answers_hand_the_run_to_the_next_reviewer(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy, ("trivial", "small", "medium", "large"))
    chosen = cli._selection(plan, _classification("small"))
    _write(tmp_path, 1, "small", missing=Absence("invalid", "each finding must have the keys"), recovered=JEV_RETRY)
    _write(tmp_path, 2, "small", missing=Absence("invalid", "the reviewer output is not JSON"), recovered=JEV_ESCALATE)
    step, errors = attempts.decide(chosen, attempts.load(tmp_path, plan, RUN_ID))
    assert errors == []
    assert step.kind == selection.REVIEW and step.reviewer is not None and step.reviewer.name == "medium"
    _write(tmp_path, 3, "medium", verdict=_v("Reviewed."))
    step, errors = attempts.decide(chosen, attempts.load(tmp_path, plan, RUN_ID))
    assert errors == []
    assert step.kind == selection.PASS and step.reviewer is not None and step.reviewer.name == "medium"


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


def test_the_review_body_gives_each_absent_attempt_its_slot_and_reason(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(
        tmp_path,
        1,
        "small",
        missing=Absence("invalid", "the reviewer output is not JSON: Expecting value"),
        recovered=JEV_RETRY,
    )
    _write(tmp_path, 2, "small", missing=Absence("empty", "the reviewer ended without a final answer"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert (
        "- small (attempt 1): absent (invalid): the reviewer output is not JSON: Expecting value;"
        " recovery: retry (Jev, probability 0.90, confidence 0.80)" in final.body
    )
    assert "- small (attempt 2): absent (empty): the reviewer ended without a final answer" in final.body


def test_a_published_absence_detail_is_redacted_and_bounded(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    secret = "sk-" + "a1B2c3D4" * 6
    detail = f"bad token {secret}\nand more " + "x " * 200
    _write(tmp_path, 1, "small", missing=Absence("invalid", detail), recovered=JEV_RETRY)
    _write(tmp_path, 2, "small", missing=Absence("empty", "no answer"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    line = next(row for row in final.body.splitlines() if row.startswith("- small (attempt 1)"))
    assert secret not in final.body and "[redacted]" in line
    assert "\n" not in line and line.partition(";")[0].endswith("\u2026")
    assert len(line.partition("absent (invalid): ")[2].partition(";")[0]) <= 160


def test_identical_findings_are_listed_once_and_stored_with_unique_ids(policy: Policy, tmp_path: Path) -> None:
    final = _review_final(policy, tmp_path, [_finding("High", 2), _finding("High", 2), _finding("Low", 1)])
    record = protocol.decode_marker(final.body)
    assert record is not None
    ids = [item["id"] for item in record["open_defects"]]
    assert ids == ["D1", "D2"] and record["next_id"] == 3
    assert "1 identical finding(s) were reported more than once and are listed once." in final.body
    assert final.review is not None and len(final.review["comments"]) == 2


def test_a_quota_artifact_round_trips_the_provider(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    reset = NOW + timedelta(hours=2)
    _write(tmp_path, 1, "small", missing=Absence("quota", "429 limit", reset, "google-antigravity"))
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    assert loaded.errors == []
    assert loaded.attempts[0].provider == "google-antigravity"
    assert loaded.attempts[0].reset_at == reset


def test_an_artifact_without_a_provider_loads_as_unknown(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", missing=Absence("quota", "429 limit"))
    path = tmp_path / attempts.artifact_name(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["absence"]["provider"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert attempts.load(tmp_path, plan, RUN_ID).attempts[0].provider == ""


def test_cli_finalize_after_a_failed_install_posts_a_distinct_pending_status_without_attempts(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text(json.dumps(_plan(policy, ("small",))), encoding="utf-8")
    final_dir = tmp_path / "final"
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_dir / "plan.json"),
        "--attempts-dir",
        str(tmp_path / "no-attempts"),
        "--out-dir",
        str(final_dir),
        "--omp-install",
        "failed",
    )
    assert out == {"state": "pending", "comment": "false", "review": "false", "has_state": "true"}
    status = json.loads((final_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "pending"
    assert status["description"] == "the pinned omp build could not be installed; no reviewer ran"
    state = json.loads((final_dir / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["outcome"] == "pending" and state["pending_cause"] == "omp-install"
    assert not (final_dir / "attempt").exists()
    assert not (tmp_path / "no-attempts").exists()


def test_a_manual_rerun_after_an_install_failure_keeps_the_head_non_retryable_until_the_status_posts(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(policy, ("small",))
    plan["manual"] = True
    plan["previous_state"] = {
        "pr": plan["pr"],
        "head_sha": plan["head_sha"],
        "outcome": "pending",
        "updated_at": NOW.isoformat(),
        "retries": [],
        "pending_cause": "omp-install",
        "strikes": 0,
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    final_dir = tmp_path / "final"
    _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(tmp_path / "no-attempts"),
        "--out-dir",
        str(final_dir),
        "--omp-install",
        "failed",
    )
    attempt = retry.State.from_json(json.loads((final_dir / "attempt" / "state.json").read_text(encoding="utf-8")))
    assert attempt.outcome == "pending" and attempt.pending_cause == "omp-install" and len(attempt.retries) == 1
    assert not retry.due_for_retry(attempt, plan["head_sha"], NOW + timedelta(days=2), policy.retry)


def test_cli_finalize_without_the_install_flag_still_decides_from_attempts(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text(json.dumps(_plan(policy, ("small",))), encoding="utf-8")
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()
    out = _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_dir / "plan.json"),
        "--attempts-dir",
        str(attempts_dir),
        "--out-dir",
        str(tmp_path / "final"),
    )
    status = json.loads((tmp_path / "final" / "status.json").read_text(encoding="utf-8"))
    assert out["state"] == "pending"
    assert status["description"] != "the pinned omp build could not be installed; no reviewer ran"


def _tampered(policy: Policy, tmp_path: Path, **over: Any):
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("", []))
    path = tmp_path / attempts.artifact_name(1)
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **over}), encoding="utf-8")
    loaded, step, errors = _decide(policy, plan, tmp_path)
    return plan, loaded, step, errors


@pytest.mark.parametrize(
    "over",
    [
        {"schema": 1},
        {"pr": 8},
        {"outcome": "settled"},
        {"slot": 0},
        {"slot": 3},
        {"slot": "1"},
        {"slot": True},
    ],
)
def test_a_tampered_artifact_is_dropped_and_makes_the_run_pending_for_integrity(
    policy: Policy, tmp_path: Path, over: dict[str, Any]
) -> None:
    plan, loaded, step, errors = _tampered(policy, tmp_path, **over)
    assert loaded.attempts == [] and loaded.verdicts == {}
    assert errors and all(isinstance(error, str) for error in errors)
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending" and final.pending_cause == INTEGRITY


def test_an_artifact_in_the_wrong_file_is_dropped_but_the_rest_still_load(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=_v("", []))
    (tmp_path / attempts.artifact_name(1)).rename(tmp_path / "attempt-1.json.bak")
    (tmp_path / "attempt-1.json.bak").rename(tmp_path / "attempt-9.json")
    _write(tmp_path, 2, "medium", missing=Absence("timeout", "slow"))
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    assert [attempt.slot for attempt in loaded.attempts] == [2]
    assert len(loaded.errors) == 2 and all(isinstance(error, str) for error in loaded.errors)


def test_the_last_permitted_slot_is_accepted(policy: Policy, tmp_path: Path) -> None:
    plan = {**_plan(policy), "max_attempts": 1}
    _write(tmp_path, 1, "small", verdict=_v("", []))
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    assert loaded.errors == [] and [attempt.slot for attempt in loaded.attempts] == [1]


@pytest.mark.parametrize("content", ["{not json", "[]", "null", json.dumps(sorted(attempts.ARTIFACT_KEYS))])
def test_an_unreadable_or_malformed_artifact_is_skipped_and_later_ones_still_load(
    policy: Policy, tmp_path: Path, content: str
) -> None:
    plan = _plan(policy)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / attempts.artifact_name(1)).write_text(content, encoding="utf-8")
    _write(tmp_path, 2, "medium", missing=Absence("timeout", "slow"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert [attempt.slot for attempt in loaded.attempts] == [2]
    assert all(isinstance(error, str) for error in errors) and len(errors) >= 2
    assert report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {}).pending_cause == INTEGRITY


def test_a_missing_slot_makes_the_run_pending_for_integrity(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 2, "medium", missing=Absence("timeout", "slow"))
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert len(errors) >= 1 and all(isinstance(error, str) for error in errors)
    assert report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {}).pending_cause == INTEGRITY


def test_an_artifact_for_an_absence_carries_no_verdict_and_an_empty_diagnostic_by_default() -> None:
    artifact = attempts.build_artifact(
        run_id=RUN_ID,
        pr=7,
        head_sha=HEAD,
        slot=1,
        reviewer="small",
        verdict=_v("ignored"),
        missing=Absence("timeout", "slow"),
    )
    assert artifact["verdict"] is None and artifact["diagnostic"] == ""
    assert artifact["outcome"] == attempts.ABSENT_OUTCOME


def test_an_invalid_verdict_publishes_the_validation_reason_as_the_absence_detail(
    policy: Policy, tmp_path: Path
) -> None:
    plan = _plan(policy)
    bad = _v("", [{"severity": "Severe"}])
    with pytest.raises(VerdictError) as reason:
        validate(bad, trusted=True)
    _write(tmp_path, 1, "small", verdict=bad)
    attempt = attempts.load(tmp_path, plan, RUN_ID).attempts[0]
    assert (attempt.slot, attempt.reviewer, attempt.outcome, attempt.absence) == (1, "small", "absent", "invalid")
    assert attempt.detail == str(reason.value) != ""


def test_a_verdict_contradicting_the_plan_is_absent_with_the_contradiction_as_detail(
    policy: Policy, tmp_path: Path
) -> None:
    plan = _plan(policy)
    contradictory = _v("", [], decision="SHIP_WITH_FIXES")
    problem = protocol.contradiction(validate(contradictory, trusted=True), plan)
    assert problem
    _write(tmp_path, 1, "small", verdict=contradictory)
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    attempt = loaded.attempts[0]
    assert (attempt.slot, attempt.reviewer, attempt.outcome, attempt.absence) == (1, "small", "absent", "invalid")
    assert attempt.detail == problem and loaded.verdicts == {}
    assert attempts.decide(SelectionInput.from_plan(plan, NOW), loaded)[1] == []


@pytest.mark.parametrize("kind", ["ok", "bogus", None])
def test_an_absence_record_of_an_unknown_kind_is_treated_as_invalid(policy: Policy, tmp_path: Path, kind: Any) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", verdict=None, missing=Absence("timeout", "slow"))
    path = tmp_path / attempts.artifact_name(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["absence"]["kind"] = kind
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = attempts.load(tmp_path, plan, RUN_ID)
    attempt = loaded.attempts[0]
    assert (attempt.slot, attempt.reviewer, attempt.outcome, attempt.absence) == (1, "small", "absent", "invalid")
    assert isinstance(attempt.detail, str) and attempt.detail and attempt.reset_at is None


def test_an_absence_without_a_detail_loads_with_an_empty_one(policy: Policy, tmp_path: Path) -> None:
    plan = _plan(policy)
    _write(tmp_path, 1, "small", missing=Absence("timeout", ""))
    path = tmp_path / attempts.artifact_name(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["absence"]["detail"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    assert attempts.load(tmp_path, plan, RUN_ID).attempts[0].detail == ""


def test_an_artifact_that_is_not_utf8_is_reported_unreadable_and_finalizes_pending(
    policy: Policy, tmp_path: Path
) -> None:
    plan = _plan(policy)
    (tmp_path / "attempt-1.json").write_bytes(b"\xff\xfe\x00 not utf-8")
    loaded, step, errors = _decide(policy, plan, tmp_path)
    assert len(errors) == 1 and errors[0].startswith("attempt-1.json: unreadable")
    final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, {})
    assert final.state == "pending"
    assert final.pending_cause == INTEGRITY


def _unpublished_manual_run(
    policy: Policy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous: retry.State | None,
    at: datetime,
    name: str,
) -> retry.State:
    plan = _plan(policy, ("small",))
    plan["manual"] = True
    plan["previous_state"] = previous.to_json() if previous is not None else None
    plan_path = tmp_path / f"{name}-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    final_dir = tmp_path / name
    monkeypatch.setattr(cli, "_now", lambda: at)
    _run_cli(
        monkeypatch,
        tmp_path,
        "finalize",
        "--plan",
        str(plan_path),
        "--attempts-dir",
        str(tmp_path / "no-attempts"),
        "--out-dir",
        str(final_dir),
        "--omp-install",
        "failed",
    )
    return retry.State.from_json(json.loads((final_dir / "attempt" / "state.json").read_text(encoding="utf-8")))


def _rerun_allowed(policy: Policy, plan_head: str, previous: retry.State, now: datetime) -> bool:
    return retry.decide_rerun(
        manual=True, new_diff=False, head_sha=plan_head, previous=previous, now=now, rules=policy.retry
    ).allowed


def test_manual_runs_whose_status_never_posted_exhaust_the_daily_budget_without_prior_state(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _plan(policy, ("small",))["head_sha"]
    start = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    state: retry.State | None = None
    for index in range(policy.retry.daily_budget):
        at = start + timedelta(hours=index * 3)
        state = _unpublished_manual_run(policy, tmp_path, monkeypatch, state, at, f"run{index}")
        assert state.head_sha == head and state.updated_at == at
    assert state is not None and len(state.retries) == policy.retry.daily_budget
    assert not _rerun_allowed(policy, head, state, start + timedelta(hours=20))


def test_unpublished_manual_runs_on_a_new_head_keep_their_budget_instead_of_dropping_it_with_the_old_head(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _plan(policy, ("small",))["head_sha"]
    start = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    state: retry.State | None = retry.State(
        7, "e" * 40, "pending", start - timedelta(hours=1), (), pending_cause="all-absent"
    )
    for index in range(policy.retry.daily_budget):
        state = _unpublished_manual_run(
            policy, tmp_path, monkeypatch, state, start + timedelta(hours=index * 3), f"b{index}"
        )
        assert state.head_sha == head
    assert state is not None
    assert not _rerun_allowed(policy, head, state, start + timedelta(hours=20))


def test_an_unpublished_manual_run_pushes_the_backoff_out_from_its_own_time(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _plan(policy, ("small",))["head_sha"]
    midnight = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    previous = retry.State(7, head, "pending", midnight, (midnight,), pending_cause="all-absent")
    ten = midnight + timedelta(hours=10)
    attempted = _unpublished_manual_run(policy, tmp_path, monkeypatch, previous, ten, "late")
    assert attempted.updated_at == ten
    assert not _rerun_allowed(policy, head, attempted, ten + timedelta(minutes=1))
    assert _rerun_allowed(policy, head, attempted, ten + timedelta(hours=4))


def test_an_unpublished_run_state_is_never_retried_by_the_sweep(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _plan(policy, ("small",))["head_sha"]
    at = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    attempted = _unpublished_manual_run(policy, tmp_path, monkeypatch, None, at, "solo")
    assert attempted.pending_cause == retry.UNPUBLISHED
    assert not retry.due_for_retry(attempted, head, at + timedelta(days=2), policy.retry)
