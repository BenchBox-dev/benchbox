from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import protocol, report
from _project.scripts.oracle_reviewers.policy import Policy

from .test_attempts import CHECKER, _v
from .test_rounds import OTHER, _final, _protocol_plan, _record

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GITHUB_LIMIT = 65536


def _big(count: int, detail: int = 4000, outside: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "severity": "High" if index % 2 else "Medium",
            "file": OTHER if index >= count - outside else CHECKER,
            "line": 10 + index,
            "title": f"Distinct problem number {index}",
            "detail": f"{index}:" + "x" * (detail - len(str(index)) - 1),
        }
        for index in range(count)
    ]


def _follow_up_plan(policy: Policy) -> dict[str, Any]:
    return _protocol_plan(policy, kind="follow-up", round=2, changed=[CHECKER], next_id=1)


def test_an_oversized_review_body_is_capped_and_keeps_every_defect(policy: Policy, tmp_path: Path) -> None:
    findings = _big(15, outside=5)
    final = _final(policy, tmp_path, _follow_up_plan(policy), _v("S" * 4000, findings))
    assert final.review is not None
    body = final.review["body"]
    assert len(body) <= GITHUB_LIMIT
    assert body.startswith("### oracle-verdict: failure for `")
    assert "Decision: **SHIP WITH FIXES**." in body
    assert "Size limit: the detail of 15 of 15 listed defect(s) was shortened" in body
    assert report.TRUNCATED in body
    for index in range(15):
        assert f"Distinct problem number {index}" in body
    record = _record(final)
    assert [item["id"] for item in record["open_defects"]] == [f"D{n}" for n in range(1, 11)]
    assert body.count(protocol.MARKER_OPEN) == 1
    assert protocol.decode_marker(body) == record


def test_the_same_input_uncapped_exceeds_the_github_limit(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "BODY_LIMIT", 10**9)
    final = _final(policy, tmp_path, _follow_up_plan(policy), _v("S" * 4000, _big(15, outside=5)))
    assert final.review is not None
    assert len(final.review["body"]) > GITHUB_LIMIT
    assert "Size limit" not in final.review["body"]


def test_a_body_that_fits_is_left_untouched(policy: Policy, tmp_path: Path) -> None:
    final = _final(policy, tmp_path, _protocol_plan(policy), _v("bad", _big(3, 500)))
    assert final.review is not None
    assert "Size limit" not in final.review["body"] and report.TRUNCATED not in final.review["body"]
    assert "x" * 400 in final.review["body"]


def test_enormous_line_numbers_are_clamped_and_the_finding_is_kept() -> None:
    from _project.scripts.oracle_reviewers import verdict

    huge = 10**4000
    found = verdict._finding(
        {"severity": "High", "file": "a/b.py", "line": huge, "end_line": huge + 5, "title": "t", "detail": "d"}
    )
    assert found.line == verdict.MAX_LINE and found.end_line is None
    ranged = verdict._finding(
        {"severity": "High", "file": "a/b.py", "line": 3, "end_line": huge, "title": "t", "detail": "d"}
    )
    assert (ranged.line, ranged.end_line) == (3, verdict.MAX_LINE)


def test_a_review_with_thousand_digit_line_numbers_still_fits_and_lists_every_defect(
    policy: Policy, tmp_path: Path
) -> None:
    findings = [
        {**finding, "line": 10**3000 + index, "end_line": 10**3000 + index + 7}
        for index, finding in enumerate(_big(15, outside=5))
    ]
    final = _final(policy, tmp_path, _follow_up_plan(policy), _v("S" * 4000, findings))
    assert final.review is not None
    body = final.review["body"]
    assert len(body) <= GITHUB_LIMIT
    for index in range(15):
        assert f"Distinct problem number {index}" in body
    assert protocol.decode_marker(body) == _record(final)


@pytest.mark.parametrize("harness", ["text", "omp", "claude"])
def test_a_number_beyond_the_integer_conversion_limit_is_invalid_reviewer_output(harness: str) -> None:
    from _project.scripts.oracle_reviewers import verdict

    text = '{"status": "complete", "line": ' + "9" * 5000 + "}"
    with pytest.raises(verdict.VerdictError, match="not JSON"):
        verdict.parse_output(harness, text)
