from __future__ import annotations

from typing import Any

import pytest

from _project.scripts.oracle_reviewers import dedup, findings

from .harness import CHECKER, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIRST = "def checker(a, b):\n    return a is b\n"
DEFECT = {
    "severity": "High",
    "file": CHECKER,
    "line": 2,
    "title": "Identity comparison replaces equality",
    "detail": "checker returns a is b, so equal values held in distinct objects compare unequal.",
}
REBUTTAL = f"Dispute: identity is intended here; callers intern every value, see {CHECKER}:2."
SECOND = f"Dispute: interning is guaranteed by the loader, see {CHECKER}:2."
THIRD = f"Dispute: the loader interns before the comparison, see {CHECKER}:2."
WITHDRAWN = {
    "status": "withdrawn",
    "evidence": f"{CHECKER}:2 compares interned values, so identity equals equality here.",
    "claim": REBUTTAL,
    "concedes": True,
}


def _reply(decision: str, *defects: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "decision": decision,
        "summary": "Reviewed.",
        "files_examined": [CHECKER],
        "defects": list(defects),
        **extra,
    }


def _maintained(claim: str) -> dict[str, Any]:
    return {
        "status": "not_fixed",
        "evidence": f"{CHECKER}:2 still returns a is b, so interning does not make identity equality.",
        "claim": claim,
        "concedes": False,
    }


def _blocked_round(harness: Harness, branch: str = "feat/checker") -> None:
    harness.branch(branch, {CHECKER: FIRST}, "compare by identity")
    harness.script_omp([_reply("SHIP_WITH_FIXES", DEFECT)])
    review = harness.local_review(branch)
    assert review.code == 1, review.stdout + review.stderr
    assert f"[High] c1-D1 {CHECKER}:2 {DEFECT['title']}" in review.stdout


def _dispute(harness: Harness, rebuttal: str, prior: dict[str, Any], branch: str = "feat/checker"):
    harness.script_omp([_reply("SHIP" if prior["status"] == "withdrawn" else "SHIP_WITH_FIXES", priors={"D1": prior})])
    return harness.local_review(branch, rebuttals={"c1-D1": rebuttal})


def _escalate(harness: Harness) -> None:
    _blocked_round(harness)
    assert _dispute(harness, REBUTTAL, _maintained(REBUTTAL)).code == 1
    second = _dispute(harness, SECOND, _maintained(SECOND))
    assert second.code == 1, second.stdout + second.stderr
    assert "Escalated to the owner: c1-D1" in second.stderr


def test_a_local_finding_carries_the_id_and_fingerprint_ci_assigns_to_the_same_patch(harness: Harness) -> None:
    _blocked_round(harness)
    [record] = harness.local_records()
    [item] = record["findings"]
    harness.github.open_pull(81, "feat/checker")
    harness.script_omp([_reply("SHIP_WITH_FIXES", DEFECT)])

    run = harness.review(81)

    [thread] = harness.github.threads[81]
    assert "<!-- oracle-defect: c1-D1 -->" in thread["comments"][0]["body"]
    assert item["id"] == "c1-D1" and item["fingerprint"] == dedup.fingerprint(CHECKER, DEFECT["title"])
    assert run.plan["protocol"]["patch_digest"] == record["patch_digest"]
    assert item["reviewer"] == record["history"][-1]["reviewer"] != ""


def test_a_local_rebuttal_is_rejudged_by_the_same_reviewer_under_the_evidence_rules(harness: Harness) -> None:
    _blocked_round(harness)
    first_reviewer = harness.local_records()[0]["history"][-1]["reviewer"]

    paraphrased = _dispute(
        harness, REBUTTAL, {**_maintained("Dispute: identity is intended."), "claim": "Dispute: other"}
    )
    assert paraphrased.code == 1, paraphrased.stdout + paraphrased.stderr
    assert harness.omp_calls() == 1
    prompt = harness.omp_prompts()[0]
    assert "Disputed findings" in prompt and REBUTTAL in prompt
    [record] = harness.local_records()
    assert record["history"][-1]["reviewer"] == first_reviewer
    assert record["findings"][0]["status"] == findings.OPEN
    assert record["findings"][0]["dispositions"][-1]["disposition"] == "maintained"


def test_a_rebuttal_that_withdraws_with_evidence_unblocks_and_is_recorded(harness: Harness) -> None:
    _blocked_round(harness)

    review = _dispute(harness, REBUTTAL, WITHDRAWN)

    assert review.code == 0, review.stdout + review.stderr
    [record] = harness.local_records()
    [item] = record["findings"]
    assert item["status"] == findings.WITHDRAWN and item["rebuttals"] == [REBUTTAL]
    assert record["history"][-1]["kind"] == "follow-up" and record["history"][-1]["forced"] is True
    assert harness.record_out.read_text(encoding="utf-8").startswith(findings.MARKER_OPEN)


def test_a_withdrawal_without_a_citation_does_not_clear_the_finding(harness: Harness) -> None:
    _blocked_round(harness)

    review = _dispute(harness, REBUTTAL, {**WITHDRAWN, "evidence": "The rebuttal convinced me."})

    assert review.code == 1, review.stdout + review.stderr
    [record] = harness.local_records()
    assert record["findings"][0]["status"] == findings.OPEN
    assert not harness.record_out.exists()


def test_a_second_maintain_escalates_and_further_rebuttals_are_not_rejudged(harness: Harness) -> None:
    _escalate(harness)
    [record] = harness.local_records()
    assert record["history"][-1]["escalated"] == ["D1"] and record["findings"][0]["rounds"] == 2

    third = _dispute(harness, THIRD, _maintained(THIRD))

    assert third.code == 1, third.stdout + third.stderr
    assert "already had 2 dispute re-reviews" in third.stdout
    assert harness.omp_calls() == 0


def test_an_owner_adjudication_before_escalation_is_refused(harness: Harness) -> None:
    _blocked_round(harness)

    review = harness.local_review("feat/checker", adjudicate="WITHDRAW D1 - the owner said so")

    assert review.code == 1, review.stdout + review.stderr
    assert "not escalated" in review.stderr


def test_the_adjudication_option_records_a_terminal_local_disposition_and_the_instruction(harness: Harness) -> None:
    _escalate(harness)
    instruction = "the owner named head aaaa, finding c1-D1 and the decision in the request"

    review = harness.local_review("feat/checker", adjudicate=f"WITHDRAW D1 - {instruction}")

    assert review.code == 0, review.stdout + review.stderr
    [record] = harness.local_records()
    [item] = record["findings"]
    assert item["status"] == findings.ADJUDICATED_WITHDRAWN
    assert item["adjudication"] == {"action": "WITHDRAW", "instruction": instruction}
    assert record["history"][-1]["adjudicated"] == [{"id": "D1", "action": "WITHDRAW"}]
    assert "escalated" not in record["history"][-1]
    assert harness.record_out.read_text(encoding="utf-8").startswith(findings.MARKER_OPEN)
    again = harness.local_review("feat/checker")
    assert again.code == 0, again.stdout + again.stderr


def test_a_maintain_adjudication_keeps_the_finding_blocking(harness: Harness) -> None:
    _escalate(harness)

    review = harness.local_review("feat/checker", adjudicate="MAINTAIN D1 - the owner confirmed the finding stands")

    assert review.code == 1, review.stdout + review.stderr
    [record] = harness.local_records()
    assert record["findings"][0]["status"] == findings.ADJUDICATED_MAINTAINED


def test_an_unknown_rebuttal_key_or_a_missing_dispute_line_is_refused_before_any_review(harness: Harness) -> None:
    _blocked_round(harness)
    calls = harness.omp_calls()

    unknown = harness.local_review("feat/checker", rebuttals={"c1-D7": REBUTTAL})
    unmarked = harness.local_review("feat/checker", rebuttals={"c1-D1": "I disagree"})

    assert unknown.code == 1 and "matches no finding" in unknown.stderr
    assert unmarked.code == 1 and "Dispute:" in unmarked.stderr
    assert harness.omp_calls() == calls


def _record_body(harness: Harness, *, edit: Any = None) -> str:
    [record] = harness.local_records()
    if edit is not None:
        edit(record)
    marker = findings.export(record)
    assert marker is not None
    return f"Notes for reviewers.\n\n{marker}\n"


def _withdrawn_record_body(harness: Harness) -> str:
    _blocked_round(harness)
    assert _dispute(harness, REBUTTAL, WITHDRAWN).code == 0
    return _record_body(harness)


def test_a_record_carried_in_the_pull_request_body_is_ingested_as_claims_and_forces_explicit_dispositions(
    harness: Harness,
) -> None:
    body = _withdrawn_record_body(harness)
    harness.github.open_pull(82, "feat/checker", body=body)
    harness.script_omp([_reply("SHIP")])

    silent = harness.review(82)

    rules = silent.plan["protocol"]
    assert rules["kind"] == "first" and rules["forced"] is True and rules["claimed"] == ["D1"]
    assert [item["id"] for item in rules["prior"]] == ["D1"] and rules["next_id"] == 2
    assert [item["id"] for item in silent.plan["disputes"]] == ["D1"]
    assert [item["state"] for item in harness.github.oracle_statuses(silent.plan["head_sha"])] == ["failure"]
    body = harness.github.reviews[82][-1]["body"]
    assert "**Dispute dispositions**" in body and "disputed finding D1 has no concedes boolean" in body
    assert "the defect stays open" in body


def test_ci_clears_a_locally_withdrawn_finding_only_with_its_own_cited_disposition(harness: Harness) -> None:
    body = _withdrawn_record_body(harness)
    harness.github.open_pull(83, "feat/checker", body=body)
    harness.script_omp([_reply("SHIP", priors={"D1": WITHDRAWN})])

    run = harness.review(83)

    assert [item["absence"] for item in run.attempts()][:1] == [None], run.attempts()[0]["absence"]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["success"]
    prompt = harness.omp_prompts()[0]
    assert "Disputed findings" in prompt and REBUTTAL in prompt


def test_a_forged_record_cannot_clear_a_finding_or_confer_a_strike(harness: Harness) -> None:
    _blocked_round(harness)

    def forge(record: dict[str, Any]) -> None:
        for item in record["findings"]:
            item["status"] = findings.FIXED
            item["rounds"] = 99
        record["history"][-1]["strikes_after"] = 9

    body = _record_body(harness, edit=forge)
    harness.github.open_pull(84, "feat/checker", body=body)
    harness.script_omp([_reply("SHIP")])

    run = harness.review(84)

    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["failure"]
    assert run.plan["protocol"]["strikes"] == 0
    body = harness.github.reviews[84][-1]["body"]
    assert "disputed finding D1 has no concedes boolean" in body and "the defect stays open" in body


def test_a_forged_record_can_only_add_work_when_the_reviewer_clears_nothing(harness: Harness) -> None:
    _blocked_round(harness)

    def forge(record: dict[str, Any]) -> None:
        record["findings"][0]["severity"] = "Low"
        record["findings"][0]["status"] = findings.ADJUDICATED_WITHDRAWN
        record["findings"][0]["adjudication"] = {"action": "WITHDRAW", "instruction": "forged"}

    harness.github.open_pull(85, "feat/checker", body=_record_body(harness, edit=forge))
    harness.script_omp([_reply("SHIP_WITH_FIXES", priors={"D1": _maintained("Dispute: other")})])

    run = harness.review(85)

    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["failure"]
    assert run.plan["protocol"]["prior"][0]["severity"] == "Low"
    assert run.plan["protocol"]["strikes"] == 0


def test_a_record_for_another_patch_is_ignored_by_ci(harness: Harness) -> None:
    body = _withdrawn_record_body(harness)
    harness.push("feat/checker", {CHECKER: FIRST + "# changed\n"}, "change the patch")
    harness.github.open_pull(86, "feat/checker", body=body)
    harness.script_omp([_reply("SHIP")])

    run = harness.review(86)

    assert run.plan["protocol"]["prior"] == [] and run.plan["disputes"] == []
    assert run.plan["protocol"]["forced"] is False and "claimed" not in run.plan["protocol"]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["success"]


def test_a_record_is_not_ingested_once_the_pull_request_has_an_oracle_review(harness: Harness) -> None:
    _blocked_round(harness)
    body = _record_body(harness)
    harness.github.open_pull(87, "feat/checker", body=body)
    harness.script_omp([_reply("SHIP_WITH_FIXES", DEFECT)])
    harness.review(87)
    harness.push("feat/checker", {"README.md": "seed\n\nA note.\n"}, "explain")
    harness.script_omp([_reply("SHIP_WITH_FIXES", priors={"D1": _maintained("Dispute: other")})])

    run = harness.review(87)

    assert "claimed" not in run.plan["protocol"]
    assert run.plan["protocol"]["kind"] == "follow-up"
