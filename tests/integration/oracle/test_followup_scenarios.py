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


def test_dispute_round_keeps_a_blocking_verdict_whose_rebuttal_claim_is_paraphrased(harness: Harness) -> None:
    _first_round(harness, 53)
    harness.github.reply(53, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    maintained = {
        "status": "not_fixed",
        "evidence": f"{CHECKER}:2 still returns a is b, so interning does not make identity equality.",
        "claim": "Dispute: identity is intended because callers intern every value.",
        "concedes": False,
    }
    harness.script_omp(
        [
            {
                "decision": "SHIP_WITH_FIXES",
                "summary": "The defect stays open.",
                "files_examined": [CHECKER],
                "priors": {"D1": maintained},
            },
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
            },
        ]
    )

    head = harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(53)

    assert [(item["id"], item["path"]) for item in run.plan["disputes"]] == [("D1", CHECKER)]
    assert harness.omp_calls() == 1
    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["failure"]
    assert "does not restate one of its rebuttal replies" in harness.github.reviews[53][-1]["body"]


HIDDEN_NOTE = ".github/interning-note.yml"


@pytest.mark.parametrize("status", ["withdrawn", "fixed"])
def test_dispute_round_accepts_a_citation_to_a_hidden_path(harness: Harness, status: str) -> None:
    _first_round(harness, 54)
    harness.github.reply(54, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    prior = {
        "status": status,
        "evidence": f"{HIDDEN_NOTE}:1 records that callers intern every value, so identity equals equality.",
        "claim": REBUTTAL,
        "concedes": True,
    }
    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "The dispute holds.",
                "files_examined": [CHECKER, HIDDEN_NOTE],
                "reads": [HIDDEN_NOTE],
                "priors": {"D1": prior},
            }
        ]
    )

    head = harness.push("feat/checker", {HIDDEN_NOTE: "interned: true\n"}, "record interning")
    run = harness.review(54)

    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


def test_dispute_round_publishes_the_override_note_after_a_maximum_length_maintain(harness: Harness) -> None:
    _first_round(harness, 55)
    harness.github.reply(55, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    lead = f"{CHECKER}:2 still returns a is b. "
    maintained = {
        "status": "not_fixed",
        "evidence": lead + "x" * (1000 - len(lead)),
        "claim": "Dispute: identity is intended because callers intern every value.",
        "concedes": False,
    }
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "Open.", "files_examined": [CHECKER], "priors": {"D1": maintained}}]
    )

    harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    harness.review(55)

    assert "does not restate one of its rebuttal replies" in harness.github.reviews[55][-1]["body"]


@pytest.mark.parametrize("note", ["notes/a+b.md", "notes/with space.md", "notes/naïve.md", "notes/-hidden.md"])
@pytest.mark.parametrize("status", ["withdrawn", "fixed"])
def test_dispute_round_accepts_a_citation_to_any_valid_filename(harness: Harness, status: str, note: str) -> None:
    _first_round(harness, 56)
    harness.github.reply(56, 0, REBUTTAL, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
    prior = {
        "status": status,
        "evidence": f"{note}:1 records that callers intern every value, so identity equals equality.",
        "claim": REBUTTAL,
        "concedes": True,
    }
    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "The dispute holds.",
                "files_examined": [CHECKER, note],
                "reads": [note],
                "priors": {"D1": prior},
            }
        ]
    )

    head = harness.push("feat/checker", {note: "interned: true\n"}, "record interning")
    run = harness.review(56)

    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


@pytest.mark.parametrize(
    "rebuttal", ["Dispute: @@owner is incorrect.", "Dispute: [[notes](x)](y) @@owner [link](https://a.b(c)) is wrong."]
)
def test_dispute_round_replays_a_withdrawal_whose_rebuttal_needs_repeated_sanitizing(
    harness: Harness, rebuttal: str
) -> None:
    _first_round(harness, 57)
    harness.github.reply(57, 0, rebuttal, {**ACCOUNTS[OWNER_TOKEN]}, "OWNER")
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
                        "claim": rebuttal,
                        "concedes": True,
                    }
                },
            }
        ]
    )

    head = harness.push("feat/checker", {"README.md": "seed\n\nCallers intern every value.\n"}, "explain interning")
    run = harness.review(57)

    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
    assert run.attempts()[0]["outcome"] == "verdict"
