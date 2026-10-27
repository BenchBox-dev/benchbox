from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, github, reset

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]
REPO = "BenchBox-dev/BenchBox"
HEAD = "a" * 40
OTHER = "b" * 40
START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
OWNER = {"login": "joeharris76", "type": "User"}


def _comment(body: str, minutes: int, *, user: dict[str, str] = OWNER, edited: bool = False) -> dict[str, Any]:
    created = (START + timedelta(minutes=minutes)).isoformat()
    updated = (START + timedelta(minutes=minutes + 1)).isoformat() if edited else created
    return {"user": user, "body": body, "created_at": created, "updated_at": updated}


def _instruction(head: str = HEAD, minutes: int = 0, **kwargs: Any) -> dict[str, Any]:
    return _comment(f"Owner instruction: reset the backoff for {head}", minutes, **kwargs)


def _reset(head: str = HEAD, minutes: int = 5, reason: str = "the gateway was down", **kwargs: Any) -> dict[str, Any]:
    return _comment(reset.reset_body(head, reason), minutes, **kwargs)


def test_a_reset_after_an_owner_instruction_is_accepted() -> None:
    assert reset.reset_moment([_instruction(), _reset()], HEAD) == START + timedelta(minutes=5)


def test_the_latest_valid_reset_wins() -> None:
    comments = [_instruction(), _reset(minutes=5), _reset(minutes=9)]
    assert reset.reset_moment(comments, HEAD) == START + timedelta(minutes=9)


@pytest.mark.parametrize(
    "comments",
    [
        [_reset()],
        [_instruction(OTHER), _reset()],
        [_instruction(minutes=6), _reset()],
        [_instruction(), _reset(OTHER)],
        [_instruction(), _reset(edited=True)],
        [_instruction(edited=True), _reset()],
        [_instruction(), _reset(user={"login": "someone", "type": "User"})],
        [_instruction(), _reset(user={"login": "joeharris76", "type": "Bot"})],
        [_instruction(user={"login": "someone", "type": "User"}), _reset()],
        [_instruction(), _comment(f"Oracle backoff reset: {HEAD}", 5)],
        [_instruction(), _comment(f"Oracle backoff reset: {HEAD}\nReason: why\nmore", 5)],
        [_instruction(), _comment(f"> Oracle backoff reset: {HEAD}\nReason: why", 5)],
    ],
)
def test_anything_short_of_an_owner_reset_with_a_prior_instruction_is_ignored(comments: list[dict[str, Any]]) -> None:
    assert reset.reset_moment(comments, HEAD) is None


@pytest.mark.parametrize(
    ("reason", "valid"),
    [("the gateway was down", True), ("", False), (" padded ", False), ("two\nlines", False), ("x" * 201, False)],
)
def test_a_reason_is_one_trimmed_line(reason: str, valid: bool) -> None:
    assert reset.valid_reason(reason) is valid


def test_the_reset_attesters_are_the_stand_in_attesters() -> None:
    script = REPO_ROOT / "_project" / "scripts" / "oracle_review_check.py"
    sys.path.insert(0, str(script.parent))
    try:
        spec = importlib.util.spec_from_file_location("oracle_review_check_for_attesters", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(script.parent))
    assert reset.ATTESTERS == module.STANDIN_ATTESTERS


class Console:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, comments: list[dict[str, Any]], *, login: str = "joeharris76"):
        self.posted: list[tuple[int, str]] = []
        self.pull = {"state": "open", "head": {"sha": HEAD}}
        monkeypatch.setattr(github, "current_login", lambda: login)
        monkeypatch.setattr(github, "get_json", lambda path: self.pull)
        monkeypatch.setattr(github, "get_paginated", lambda path: comments)
        monkeypatch.setattr(github, "post_comment", lambda repo, pr, body: self.posted.append((pr, body)))

    def run(self, *, head: str = HEAD, reason: str = "the gateway was down") -> int:
        return cli.main(["reset-backoff", "--pr", "7", "--head", head, "--reason", reason, "--repo", REPO])


def test_the_owner_resets_a_backoff_with_the_reason_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(monkeypatch, [_instruction()])
    assert console.run() == 0
    assert console.posted == [(7, f"Oracle backoff reset: {HEAD}\nReason: the gateway was down")]
    posted = _comment(console.posted[0][1], 5)
    assert reset.reset_moment([_instruction(), posted], HEAD) == START + timedelta(minutes=5)


def test_a_reset_without_an_owner_instruction_naming_the_head_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(monkeypatch, [_instruction(OTHER)])
    with pytest.raises(SystemExit, match="Owner instruction"):
        console.run()
    assert console.posted == []


def test_an_account_that_is_not_an_owner_cannot_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(monkeypatch, [_instruction()], login="someone")
    with pytest.raises(SystemExit, match="not an owner"):
        console.run()
    assert console.posted == []


@pytest.mark.parametrize("state", ["closed", "head-moved"])
def test_a_reset_for_a_closed_pull_request_or_a_moved_head_is_refused(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    console = Console(monkeypatch, [_instruction()])
    console.pull = (
        {"state": "closed", "head": {"sha": HEAD}} if state == "closed" else {"state": "open", "head": {"sha": OTHER}}
    )
    with pytest.raises(SystemExit, match="is not open at head"):
        console.run()
    assert console.posted == []


@pytest.mark.parametrize(("head", "reason"), [("abc", "why"), (HEAD, ""), (HEAD, "a\nb")])
def test_a_malformed_head_or_reason_is_refused(monkeypatch: pytest.MonkeyPatch, head: str, reason: str) -> None:
    console = Console(monkeypatch, [_instruction()])
    with pytest.raises(SystemExit):
        console.run(head=head, reason=reason)
    assert console.posted == []
