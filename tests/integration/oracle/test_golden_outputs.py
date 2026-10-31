from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .fake_github import ACCOUNTS, OWNER_TOKEN
from .golden import assert_golden, logical_steps, posted, run_capture
from .harness import CHECKER, REPO_ROOT, Harness, ReviewRun
from .test_carry_scenarios import _rebase, _reviewed
from .test_evidence_scenarios import FILLER, LARGE_CHECKER, NOTES
from .test_followup_scenarios import FIXED, REBUTTAL, _first_round
from .test_sweep_scenarios import UNAVAILABLE, VERDICT, _failed_run, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.medium]


FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}
RAW_REPLY = {"kind": "text", "text": "RAW-REPLY-SENTINEL looks fine to me, nothing to report"}
ANSWERS = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "jev_recovery_answers.json"
WITHDRAWN = {
    "decision": "SHIP",
    "summary": "The dispute holds; D1 is withdrawn.",
    "files_examined": [CHECKER],
    "priors": {
        "D1": {
            "status": "withdrawn",
            "evidence": f"{CHECKER}:2 compares interned values, so identity equals equality here.",
            "claim": REBUTTAL,
            "concedes": True,
        }
    },
}


def _check(name: str, harness: Harness, run: ReviewRun, pr: int, head: str | None = None) -> None:
    assert_golden(name, run_capture(harness, run, pr, head or run.plan.get("head_sha", "")), harness)


def _open(harness: Harness, pr: int, files: dict[str, str] | None = None, **extra: Any) -> str:
    head = harness.branch("feat/checker", files or {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(pr, "feat/checker", **extra)
    return head


def _recorded(case: str) -> dict[str, Any]:
    cases = json.loads(ANSWERS.read_text(encoding="utf-8"))["cases"]
    return next(item["answer"] for item in cases if item["case"] == case)


def test_golden_first_review_ships(harness: Harness) -> None:
    _open(harness, 41)
    harness.script_omp([SHIP])
    _check("first_review_ships", harness, harness.review(41), 41)


def test_golden_first_review_with_a_defect(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: "def checker(a, b):\n    return a is b\n"}, "compare by identity")
    harness.github.open_pull(42, "feat/checker")
    defect = {
        "severity": "High",
        "file": CHECKER,
        "line": 2,
        "title": "Identity comparison replaces equality",
        "detail": "checker returns a is b, so equal values held in distinct objects compare unequal.",
    }
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "One defect.", "files_examined": [CHECKER], "defects": [defect]}]
    )
    run = harness.review(42)
    assert run.plan["head_sha"] == head
    _check("first_review_with_a_defect", harness, run, 42)


def test_golden_follow_up_after_a_fix(harness: Harness) -> None:
    _first_round(harness, 51)
    harness.push("feat/checker", {CHECKER: FIXED}, "compare by equality")
    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "D1 is fixed.",
                "files_examined": [CHECKER],
                "priors": {"D1": {"status": "fixed", "evidence": f"{CHECKER}:2 now compares by equality and type."}},
            }
        ]
    )
    _check("follow_up_after_a_fix", harness, harness.review(51), 51)


def test_golden_dispute_round(harness: Harness) -> None:
    _first_round(harness, 52)
    harness.github.reply(52, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    harness.script_omp([WITHDRAWN])
    harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(52)
    assert any('"concedes": null' in prompt for prompt in harness.omp_prompts())
    _check("dispute_round", harness, run, 52)


def test_golden_carry(harness: Harness) -> None:
    _reviewed(harness, 71, "feat/identical")
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    _rebase(harness, "feat/identical")
    _check("carry", harness, harness.review(71), 71)


def test_golden_edited_prior_review_holds(harness: Harness) -> None:
    _reviewed(harness, 73, "feat/edited")
    count = len(harness.github.reviews[73])
    review = harness.github.reviews[73][-1]
    harness.github.edit_review(73, count - 1, review["body"].replace("SHIP", "SHIP (approved)"))
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    _rebase(harness, "feat/edited")
    _check("edited_prior_review_holds", harness, harness.review(73), 73)


def test_golden_carry_that_disagrees_with_the_app_status_holds(harness: Harness) -> None:
    first = _reviewed(harness, 75, "feat/disagree")
    for status in harness.github.statuses[first]:
        if status["context"] == "oracle-verdict":
            status["state"] = "failure"
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    _rebase(harness, "feat/disagree")
    _check("carry_disagrees_with_status_holds", harness, harness.review(75), 75)


def test_golden_unreadable_edit_state_posts_nothing(harness: Harness) -> None:
    first = _reviewed(harness, 76, "feat/unreadable")
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    head = _rebase(harness, "feat/unreadable")
    assert head != first
    harness.github.fail("POST", r"/graphql", 500, 50)
    posted_before = len(harness.github.reviews[76])
    run = harness.review(76)
    assert len(harness.github.reviews[76]) == posted_before
    assert harness.github.oracle_statuses(head) == []
    _check("unreadable_edit_state_posts_nothing", harness, run, 76, head)


def test_golden_fork_pull_request(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(
        81, "feat/checker", head={"ref": "feat/checker", "repo": {"full_name": "someone/BenchBox"}}
    )
    _check("fork", harness, harness.review(81), 81)


def test_golden_draft_pull_request(harness: Harness) -> None:
    _open(harness, 82, draft=True)
    _check("draft", harness, harness.review(82), 82)


def test_golden_oversize_brief_is_a_file_list(harness: Harness) -> None:
    harness.branch(
        "feat/large",
        {CHECKER: LARGE_CHECKER, "benchbox/core/equivalence/cells.py": FILLER, NOTES: "A note about the cells.\n"},
        "add the cell table",
    )
    harness.github.open_pull(83, "feat/large")
    harness.script_omp([SHIP, {**SHIP, "files_examined": [CHECKER, "benchbox/core/equivalence/cells.py"]}])
    _check("oversize_file_list", harness, harness.review(83), 83)


def test_golden_every_attempt_absent(harness: Harness) -> None:
    _open(harness, 84)
    harness.script_omp([UNAVAILABLE])
    _check("every_attempt_absent", harness, harness.review(84), 84)


def test_golden_omp_install_failure(harness: Harness) -> None:
    _open(harness, 85)
    harness.script_omp([SHIP])
    _check("omp_install_failure", harness, harness.review(85, installable=False), 85)


def test_golden_unreadable_attempt_is_an_integrity_error(harness: Harness) -> None:
    _open(harness, 86)
    harness.script_omp([SHIP])

    def damage(attempts_dir: Path) -> None:
        for path in attempts_dir.glob("attempt-*.json"):
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["head_sha"] = "0" * 40
            path.write_text(json.dumps(artifact), encoding="utf-8")

    _check("integrity_error", harness, harness.review(86, tamper_attempts=damage), 86)


def test_golden_app_not_configured_posts_nothing(harness: Harness) -> None:
    _open(harness, 87)
    harness.script_omp([SHIP])
    _check("app_missing", harness, harness.review(87, app=False), 87)


def test_golden_guard_that_cannot_read_history_fails_the_run(harness: Harness) -> None:
    _open(harness, 88)
    harness.script_omp([SHIP])
    run = harness.review(88, before_guard=lambda: harness.github.fail("GET", r"/pulls/88/reviews", 500, 50))
    assert logical_steps(run)["publish"]["code"] == 2
    _check("guard_gives_up", harness, run, 88)


@pytest.mark.parametrize(
    ("name", "case", "replies"),
    [
        ("recovery_stop", "3453 contract, medium attempt 1, two earlier on small", [RAW_REPLY, SHIP]),
        ("recovery_escalate", "format: prose instead of JSON, small attempt 2", [RAW_REPLY, SHIP]),
        ("recovery_retry", "format: prose instead of JSON, small attempt 1", [RAW_REPLY, SHIP]),
        ("recovery_fallback", None, [RAW_REPLY, RAW_REPLY, SHIP]),
    ],
)
def test_golden_invalid_attempt_recovery(
    harness: Harness, name: str, case: str | None, replies: list[dict[str, Any]]
) -> None:
    _open(harness, 90 + len(name))
    harness.script_omp(replies)
    harness.jev.script([_recorded(case)] if case else [])
    _check(name, harness, harness.review(90 + len(name)), 90 + len(name))


def test_golden_stale_same_head_state_is_redispatched_once_then_surfaced(harness: Harness) -> None:
    head, run = _pending_review(harness, 62, "feat/pending")
    harness.github.age(run, 600)
    _failed_run(harness, 62, head, "workflow_dispatch", 90, "Run the attempt loop")
    first = harness.sweep()
    _failed_run(harness, 62, head, "workflow_dispatch", 30, "Run the attempt loop")
    second = harness.sweep()
    third = harness.sweep()
    assert_golden(
        "stale_same_head_state",
        {
            "sweeps": [{"code": step.code, "summary": step.outputs["summary"]} for step in (first, second, third)],
            "posted": posted(harness, 62, head),
            "dispatches": [item for item in harness.github.dispatches if item["workflow"] == VERDICT],
        },
        harness,
    )
