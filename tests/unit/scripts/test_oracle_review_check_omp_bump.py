from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import omp_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/oracle_review_check.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oracle_review_check", SCRIPT)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check
SPEC.loader.exec_module(check)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
POLICY_PATH = ".github/oracle-reviewers.yml"
POLICY = (
    "retry: 1\nomp:\n  version: {version}\n  builds:\n    darwin-arm64:\n      url: https://b.example/mac\n"
    "      sha256: {mac}\n    linux-x64:\n      url: https://b.example/linux\n      sha256: {linux}\n"
)
OLD = POLICY.format(version="18.12.1", mac="a1" * 32, linux="b2" * 32)
NEW = POLICY.format(version="18.13.2", mac="c3" * 32, linux="d4" * 32)
NEW_DIGEST = omp_pin.pin_digest(omp_pin.pin_from_text(NEW))


def _evidence(
    version: str = "18.13.2", sha: str = "c3" * 32, pin: str = NEW_DIGEST, head: str = HEAD, **fields: Any
) -> dict[str, Any]:
    line = (
        f"Omp candidate check: version={version} platform=darwin-arm64 sha256={sha} pin={pin} head={head} "
        "result=releasable"
    )
    return {
        "login": "joeharris76",
        "user_type": "User",
        "body": f"from the Mac\n{line}\n",
        "created_at": "2026-10-27T20:00:00Z",
        "updated_at": "2026-10-27T20:00:00Z",
        **fields,
    }


def _pull(changed: int = 1) -> dict[str, Any]:
    return {"changed_files": changed, "base": {"ref": "develop"}, "head": {"sha": HEAD, "repo": {"full_name": "o/r"}}}


@pytest.fixture
def texts(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    served = {"develop": OLD, HEAD: NEW}
    monkeypatch.setattr(check, "fetch_text", lambda _token, _repo, _path, ref: served[ref])
    return served


def _gate(comments: list[dict[str, Any]], files: list[str] | None = None) -> tuple[int, str]:
    files = [POLICY_PATH] if files is None else files
    refusals = check.fetch_bump_refusals("t", "o/r", _pull(len(files)), files, len(files), comments)
    return check.decide(HEAD, files, [], [], lambda _f: True, None, [], bump_refusals=refusals)


def test_a_pin_bump_with_no_owner_evidence_is_held(texts: dict[str, str]) -> None:
    status, message = _gate([])
    assert status == check.WAITING
    assert "omp pin bump refused" in message and "has no owner evidence" in message


@pytest.mark.parametrize(
    "comment",
    [
        _evidence(pin="9" * 64),
        _evidence(version="18.13.1"),
        _evidence(head="b" * 40),
        _evidence(sha="e9" * 32),
        _evidence(updated_at="2026-10-27T21:00:00Z"),
        _evidence(login="someone-else"),
        _evidence(user_type="Bot"),
    ],
)
def test_evidence_that_is_wrong_edited_or_not_the_owners_does_not_release_the_bump(
    texts: dict[str, str], comment: dict[str, Any]
) -> None:
    status, message = _gate([comment])
    assert status == check.WAITING and "has no owner evidence" in message


def test_matching_unedited_owner_evidence_releases_the_bump(texts: dict[str, str]) -> None:
    status, message = _gate([_evidence()])
    assert status == check.WAITING
    assert "omp pin bump refused" not in message and "waiting for the oracle's verdict" in message


def test_a_bump_that_changes_other_files_is_held_even_with_evidence(texts: dict[str, str]) -> None:
    status, message = _gate([_evidence()], [POLICY_PATH, "benchbox/core/x.py"])
    assert status == check.WAITING and "also changes benchbox/core/x.py" in message


def test_a_pull_request_that_leaves_the_pin_alone_is_not_held(texts: dict[str, str]) -> None:
    texts[HEAD] = OLD.replace("retry: 1", "retry: 2")
    assert check.fetch_bump_refusals("t", "o/r", _pull(), [POLICY_PATH], 1, []) == []


def test_a_pull_request_without_the_policy_file_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object) -> str:
        raise AssertionError("no read expected")

    monkeypatch.setattr(check, "fetch_text", boom)
    assert check.fetch_bump_refusals("t", "o/r", _pull(), ["README.md"], 1, []) == []


def test_a_truncated_file_list_still_checks_the_pin(texts: dict[str, str]) -> None:
    reasons = " ".join(check.fetch_bump_refusals("t", "o/r", _pull(5000), ["README.md"], 3000, []))
    assert "has no owner evidence" in reasons and "file list is incomplete" in reasons


def test_a_truncated_file_list_holds_the_bump_even_with_matching_owner_evidence(texts: dict[str, str]) -> None:
    reasons = check.fetch_bump_refusals("t", "o/r", _pull(5000), [POLICY_PATH], 3000, [_evidence()])
    assert len(reasons) == 1 and "file list is incomplete" in reasons[0]


def test_evidence_for_an_earlier_head_does_not_survive_a_later_push(texts: dict[str, str]) -> None:
    later = "f" * 40
    pull = {**_pull(), "head": {"sha": later, "repo": {"full_name": "o/r"}}}
    texts[later] = NEW
    reasons = check.fetch_bump_refusals("t", "o/r", pull, [POLICY_PATH], 1, [_evidence()])
    assert len(reasons) == 1 and "has no owner evidence" in reasons[0] and later[:9] in reasons[0]


def test_an_unreadable_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(*_args: object) -> str:
        raise check.CheckError("HTTP 500")

    monkeypatch.setattr(check, "fetch_text", unreadable)
    with pytest.raises(check.CheckError):
        check.fetch_bump_refusals("t", "o/r", _pull(), [POLICY_PATH], 1, [])


def test_the_attester_lists_agree() -> None:
    from oracle_reviewers import omp_bump_gate

    assert omp_bump_gate.ATTESTERS == check.STANDIN_ATTESTERS
