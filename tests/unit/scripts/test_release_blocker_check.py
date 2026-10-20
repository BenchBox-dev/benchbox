from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "release_blocker_check", Path(__file__).resolve().parents[3] / "scripts" / "release_blocker_check.py"
)
assert SPEC is not None and SPEC.loader is not None
blockers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(blockers)


def _issue(number: int, state: str, labels: list[str]) -> dict:
    return {"number": number, "state": state, "labels": [{"name": name} for name in labels]}


def test_open_release_blocking_issue_blocks():
    found = blockers.blocking_issues([_issue(7, "open", ["t3:drift", "release-blocking"])])
    assert [issue["number"] for issue in found] == [7]


def test_unrelated_open_issue_does_not_block():
    assert blockers.blocking_issues([_issue(8, "open", ["t3:drift"])]) == []


def test_closed_blocking_issue_does_not_block():
    assert blockers.blocking_issues([_issue(9, "closed", ["release-blocking"])]) == []


def test_malformed_issue_record_is_refused():
    with pytest.raises(ValueError, match="malformed"):
        blockers.blocking_issues([{"number": 1, "state": "open", "labels": "release-blocking"}])
