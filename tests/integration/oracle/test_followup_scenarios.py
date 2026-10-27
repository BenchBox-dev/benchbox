from __future__ import annotations

import pytest

from .fake_github import ACCOUNTS, OWNER_TOKEN
from .harness import CHECKER, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIRST = "def checker(a, b):\n    return a is b\n"
FIXED = "def checker(a, b):\n    return a == b and type(a) is type(b)\n"
DEFECT = {
    "severity": "High",
    "file": CHECKER,
    "line": 2,
    "title": "Identity comparison replaces equality",
    "detail": "checker returns a is b, so equal values held in distinct objects compare unequal.",
}
REBUTTAL = f"Dispute: identity is intended here; callers intern every value, see {CHECKER}:2."


def _first_round(harness: Harness, pr: int) -> str:
    harness.branch("feat/checker", {CHECKER: FIRST}, "compare by identity")
    harness.github.open_pull(pr, "feat/checker")
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "One defect.", "files_examined": [CHECKER], "defects": [DEFECT]}]
    )
    run = harness.review(pr)
    head = run.plan["head_sha"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["failure"]
    assert [thread["path"] for thread in harness.github.threads[pr]] == [CHECKER]
    return head


def test_follow_up_with_a_fixed_prior_defect_ships(harness: Harness) -> None:
    _first_round(harness, 51)
    head = harness.push("feat/checker", {CHECKER: FIXED}, "compare by equality")
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

    run = harness.review(51)

    assert run.plan["protocol"]["kind"] == "follow-up"
    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
    assert f"### oracle-verdict: success for `{head}`" in harness.github.reviews[51][-1]["body"]


def test_dispute_round_rejudges_the_disputed_finding_and_ships(harness: Harness) -> None:
    _first_round(harness, 52)
    harness.github.reply(52, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    harness.script_omp(
        [
            {
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
        ]
    )

    head = harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(52)

    assert run.plan["protocol"]["kind"] == "follow-up", run.plan["decision_reason"]
    assert [(item["id"], item["path"]) for item in run.plan["disputes"]] == [("D1", CHECKER)]
    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
