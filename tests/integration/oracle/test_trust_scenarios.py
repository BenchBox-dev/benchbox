from __future__ import annotations

import pytest

from .fake_github import ACCOUNTS, OWNER_TOKEN
from .harness import CHECKER, Harness
from .test_followup_scenarios import REBUTTAL, _first_round

pytestmark = [pytest.mark.integration, pytest.mark.medium]

CHATTER = 60
UNREAD_CITATION = "README.md:1 shows that callers intern every value."
WITHDRAWN = {
    "D1": {
        "status": "withdrawn",
        "evidence": f"{CHECKER}:2 compares interned values, so identity equals equality here.",
        "claim": REBUTTAL,
        "concedes": True,
    }
}


def _withdraw(evidence: str, examined: list[str]) -> dict:
    prior = {**WITHDRAWN["D1"], "evidence": evidence}
    return {"decision": "SHIP", "summary": "The dispute holds.", "files_examined": examined, "priors": {"D1": prior}}


def test_a_dispute_beyond_the_first_page_of_a_long_thread_is_still_judged(harness: Harness) -> None:
    _first_round(harness, 101)
    for _ in range(CHATTER):
        harness.github.reply(101, 0, "Thanks, looking into it.", {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    harness.github.reply(101, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    harness.script_omp([_withdraw(WITHDRAWN["D1"]["evidence"], [CHECKER])])

    head = harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(101)

    assert len(harness.github.threads[101][0]["comments"]) > 50
    assert [(item["id"], item["path"]) for item in run.plan["disputes"]] == [("D1", CHECKER)], run.plan[
        "decision_reason"
    ]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


def test_a_withdrawal_citing_a_file_the_reviewer_only_listed_is_not_credited(harness: Harness) -> None:
    _first_round(harness, 102)
    harness.github.reply(102, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    harness.script_omp([_withdraw(UNREAD_CITATION, [CHECKER, "README.md"])])

    head = harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(102)

    assert run.plan["protocol"]["kind"] == "follow-up", run.plan["decision_reason"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] != ["success"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["failure"]
    body = harness.github.reviews[102][-1]["body"]
    assert "- D1 (maintained): README.md:1 shows that callers intern every value." in body
    assert "the withdrawal names no examined head-commit file and line that refutes it" in body
