from __future__ import annotations

from typing import Any

import pytest

from _project.scripts.oracle_reviewers.protocol import DO_NOT_SHIP_SUMMARY, decode_marker, encode_marker, judge
from _project.scripts.oracle_reviewers.verdict import validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _defect(line: int = 1, severity: str = "High") -> dict[str, Any]:
    return {"severity": severity, "file": "a.py", "line": line, "end_line": None, "title": f"t{line}", "detail": ""}


def _verdict(decision: str, defects: list[dict[str, Any]], summary: str = "s", **over: Any):
    return validate(
        {
            "status": "complete",
            "incomplete_reason": "",
            "decision": decision,
            "summary": summary,
            "files_examined": ["a.py"],
            "defects": defects,
            "prior_defects": [],
            **over,
        }
    )


@pytest.mark.parametrize(
    ("decision", "count", "expected"),
    [
        ("SHIP", 0, "SHIP"),
        ("SHIP", 1, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 0, "SHIP"),
        ("SHIP_WITH_FIXES", 3, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 10, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 11, "DO_NOT_SHIP"),
        ("SHIP", 11, "DO_NOT_SHIP"),
        ("DO_NOT_SHIP", 0, "DO_NOT_SHIP"),
        ("DO_NOT_SHIP", 2, "DO_NOT_SHIP"),
    ],
)
def test_the_decision_is_computed_and_the_stricter_outcome_wins(decision: str, count: int, expected: str) -> None:
    judgement = judge(_verdict(decision, [_defect(line) for line in range(1, count + 1)]), 10)
    assert judgement.decision == expected
    assert len(judgement.defects) == (count if expected == "SHIP_WITH_FIXES" else 0)


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low"])
def test_a_relabelled_severity_never_produces_ship(severity: str) -> None:
    assert judge(_verdict("SHIP", [_defect(severity=severity)]), 10).decision == "SHIP_WITH_FIXES"


def test_too_many_defects_become_do_not_ship_with_the_count_in_the_summary() -> None:
    verdict = _verdict("SHIP_WITH_FIXES", [_defect(line) for line in range(1, 61)], summary="x" * 3000)
    judgement = judge(verdict, 10)
    assert judgement.decision == "DO_NOT_SHIP" and judgement.defects == ()
    assert judgement.summary.endswith("The review left 60 defects open, more than the 10 allowed.")
    assert len(judgement.summary) <= DO_NOT_SHIP_SUMMARY


def test_do_not_ship_keeps_only_a_short_summary() -> None:
    judgement = judge(_verdict("DO_NOT_SHIP", [_defect()], summary="y" * 4000), 10)
    assert len(judgement.summary) == DO_NOT_SHIP_SUMMARY
    assert judgement.label == "DO NOT SHIP"


def test_an_incomplete_review_cannot_be_judged() -> None:
    verdict = _verdict("NONE", [], status="incomplete", incomplete_reason="no access")
    with pytest.raises(ValueError, match="only a complete review"):
        judge(verdict, 10)


def test_judge_lists_fixed_and_withdrawn_ids_separately() -> None:
    prior = [
        {"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"},
        {"id": "D2", "severity": "High", "file": "a.py", "line": 2, "end_line": None, "title": "t2"},
    ]
    verdict = _verdict(
        "SHIP",
        [],
        prior_defects=[
            {"id": "D1", "status": "fixed", "evidence": "reworked"},
            {"id": "D2", "status": "withdrawn", "evidence": "never a defect: a.py:2"},
        ],
    )
    judgement = judge(verdict, 10, prior)
    assert judgement.decision == "SHIP"
    assert judgement.fixed == ("D1",) and judgement.withdrawn == ("D2",) and judgement.carried == ()


def test_judge_counts_an_unverified_withdrawal_as_open() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict("SHIP", [], prior_defects=[{"id": "D1", "status": "not_fixed", "evidence": "still there"}])
    judgement = judge(verdict, 10, prior)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.fixed == () and judgement.withdrawn == ()


def test_judge_rejudges_fixed_as_open_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    fixed = _verdict("SHIP_WITH_FIXES", [], prior_defects=[{"id": "D1", "status": "fixed", "evidence": "reworked"}])
    judgement = judge(fixed, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.fixed == () and judgement.withdrawn == ()


def test_judge_rejudges_an_uncited_withdrawal_as_open_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict(
        "SHIP_WITH_FIXES", [], prior_defects=[{"id": "D1", "status": "withdrawn", "evidence": "never a defect"}]
    )
    judgement = judge(verdict, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.withdrawn == ()


def test_judge_keeps_a_cited_withdrawal_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict(
        "SHIP", [], prior_defects=[{"id": "D1", "status": "withdrawn", "evidence": "never a defect: a.py:1"}]
    )
    judgement = judge(verdict, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP" and judgement.withdrawn == ("D1",)


def test_judge_merges_identical_defects_and_counts_them_once() -> None:
    judgement = judge(_verdict("SHIP_WITH_FIXES", [_defect(1), _defect(1), _defect(2)]), 10)
    assert len(judgement.defects) == 2 and judgement.merged == 1


def _marker_record(**over: Any) -> dict[str, Any]:
    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": "SHIP",
        "head_sha": "a" * 40,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        **over,
    }
    return record


def test_a_marker_round_trips_an_optional_run_uuid() -> None:
    body = f"line\n{encode_marker(_marker_record(run_uuid='a' * 32))}"
    assert decode_marker(body)["run_uuid"] == "a" * 32


def test_a_marker_without_a_run_uuid_stays_valid() -> None:
    body = f"line\n{encode_marker(_marker_record())}"
    assert decode_marker(body).get("run_uuid") is None


def test_a_marker_rejects_a_malformed_run_uuid() -> None:
    from _project.scripts.oracle_reviewers.protocol import MarkerError

    body = f"line\n{encode_marker(_marker_record(run_uuid='xyz'))}"
    with pytest.raises(MarkerError):
        decode_marker(body)
