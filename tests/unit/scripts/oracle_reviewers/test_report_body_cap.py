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
