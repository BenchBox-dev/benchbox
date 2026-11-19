from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.scripts.test_pr_landing import ROOT, _run_pr_open_result, requires_posix_make

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REVERT_TITLES = ["revert: undo a thing", 'Revert "feat(core): add a thing (#12)"']
CULPRIT = "c" * 40
RUNNER = "benchbox/core/runner.py"
FRAGMENT = "changelog.d/revert-fixture-absent.json"
OTHER_FRAGMENT = "changelog.d/revert-fixture-unrelated.json"


def _open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    *,
    changed: list[str],
    reverted_paths: list[str],
    reverted_added: list[str],
    reverts: bool = True,
) -> tuple[int, list[str], bytes]:
    for name, value in {
        "REAL_ROOT": str(ROOT),
        "BASE_SHA": "3" * 40,
        "REVERTS_SHA": CULPRIT if reverts else "",
        "CHANGED_FILES": "\n".join(changed),
        "ADDED_FILES": "",
        "REVERTED_PATHS": "\n".join(reverted_paths),
        "REVERTED_ADDED": "\n".join(reverted_added),
        "CAT_FILE_EXIT": "0",
    }.items():
        monkeypatch.setenv(name, value)
    result, _, pushes = _run_pr_open_result(tmp_path, "created", 0, f"TITLE={title}")
    return result.returncode, pushes, result.stderr


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_accepts_a_revert_that_deletes_the_reverted_changes_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    status, pushes, stderr = _open(
        tmp_path,
        monkeypatch,
        title,
        changed=[RUNNER, FRAGMENT],
        reverted_paths=[RUNNER, FRAGMENT],
        reverted_added=[FRAGMENT],
    )

    assert status == 0, stderr
    assert len(pushes) == 1


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_accepts_a_revert_of_an_internal_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    status, pushes, stderr = _open(
        tmp_path, monkeypatch, title, changed=[RUNNER], reverted_paths=[RUNNER], reverted_added=[]
    )

    assert status == 0, stderr
    assert len(pushes) == 1


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_refuses_a_revert_that_deletes_only_an_unrelated_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    status, pushes, stderr = _open(
        tmp_path,
        monkeypatch,
        title,
        changed=[RUNNER, OTHER_FRAGMENT],
        reverted_paths=[RUNNER],
        reverted_added=[],
    )

    assert status != 0
    assert pushes == []
    assert b"changelog" in stderr.lower()


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_refuses_a_revert_that_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    status, pushes, stderr = _open(
        tmp_path,
        monkeypatch,
        title,
        changed=[RUNNER, FRAGMENT],
        reverted_paths=[RUNNER, FRAGMENT],
        reverted_added=[FRAGMENT],
        reverts=False,
    )

    assert status != 0
    assert pushes == []
    assert b"changelog" in stderr.lower()
