from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import coverage_map_staleness as stale

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)
    return root


def test_decide_flags_a_missing_map_as_stale() -> None:
    assert stale.decide("missing", None, [])[0] == "stale"


def test_decide_flags_a_map_more_than_twenty_commits_behind() -> None:
    assert stale.STALENESS_BEHIND_LIMIT == 20
    state, reason = stale.decide("found", 21, ["success", "success"])
    assert state == "stale"
    assert "21" in reason


def test_decide_flags_three_consecutive_failed_runs() -> None:
    assert stale.STALENESS_FAILED_RUNS == 3
    assert stale.decide("found", 2, ["failure", "failure", "failure"])[0] == "stale"
    assert stale.decide("found", 2, ["failure", "failure", "success"])[0] == "healthy"


def test_decide_stays_unknown_when_the_lookup_fails() -> None:
    assert stale.decide("lookup-failed", None, [])[0] == "unknown"


def test_decide_is_healthy_for_a_current_map_with_passing_runs() -> None:
    assert stale.decide("found", 0, ["success"])[0] == "healthy"


def test_ensure_issue_creates_exactly_one_labelled_issue(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh_calls: list[list[str]] = []
    json_calls: list[list[str]] = []
    monkeypatch.setattr(stale, "open_issues", lambda _repo, _slug, _label: [])
    monkeypatch.setattr(stale, "_gh", _record(gh_calls))
    monkeypatch.setattr(stale, "_gh_json", _record_json(json_calls, {"number": 7}))
    result = stale.ensure_issue(
        repo, "o/r", "ci-coverage-map", "stale", "no coverage map published", None, None, [], "u"
    )
    assert result == {"action": "created", "issue": 7}
    assert stale.STALENESS_LABEL == "ci-coverage-map"
    assert stale.STALENESS_MARKER in stale.render_body("no coverage map published", None, None, [], "u")
    create = next(call for call in json_calls if call[:2] == ["issue", "create"])
    assert "--label" in create and "ci-coverage-map" in create


def test_ensure_issue_closes_the_issue_once_healthy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh_calls: list[list[str]] = []
    monkeypatch.setattr(stale, "open_issues", lambda _repo, _slug, _label: [{"number": 9, "body": "x"}])
    monkeypatch.setattr(stale, "_gh", _record(gh_calls))
    result = stale.ensure_issue(repo, "o/r", "ci-coverage-map", "healthy", "current", None, 0, ["success"], "u")
    assert result == {"action": "closed", "issue": 9}
    assert gh_calls[-1][:3] == ["issue", "close", "9"]


def test_ensure_issue_keeps_a_single_issue_without_duplicates(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh_calls: list[list[str]] = []
    reason = "no coverage map published"
    body = stale.render_body(reason, None, None, [], "u")
    monkeypatch.setattr(stale, "open_issues", lambda _repo, _slug, _label: [{"number": 4, "body": body}])
    monkeypatch.setattr(stale, "_gh", _record(gh_calls))
    result = stale.ensure_issue(repo, "o/r", "ci-coverage-map", "stale", reason, None, None, [], "u")
    assert result == {"action": "none", "issue": 4}
    assert gh_calls == []


def _record(calls: list[list[str]]) -> object:
    def _gh(_repo: Path, *args: str) -> str:
        calls.append(list(args))
        return ""

    return _gh


def _record_json(calls: list[list[str]], payload: object) -> object:
    def _gh_json(_repo: Path, *args: str) -> object:
        calls.append(list(args))
        return payload

    return _gh_json
