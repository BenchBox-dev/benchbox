from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.scripts.test_pr_landing import ROOT, _run_pr_open_result, requires_posix_make

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REVERT_TITLES = ["revert: undo a thing", 'Revert "feat(core): add a thing (#12)"']


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_accepts_a_revert_that_deletes_the_unreleased_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    monkeypatch.setenv("REAL_ROOT", str(ROOT))
    monkeypatch.setenv("CHANGED_FILES", "benchbox/core/runner.py\nchangelog.d/revert-fixture-absent.json")
    monkeypatch.setenv("ADDED_FILES", "")
    result, _, pushes = _run_pr_open_result(tmp_path, "created", 0, f"TITLE={title}")

    assert result.returncode == 0, result.stderr
    assert len(pushes) == 1


@requires_posix_make
@pytest.mark.parametrize("title", REVERT_TITLES)
def test_pr_open_refuses_a_revert_of_shipped_code_without_a_fragment_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    monkeypatch.setenv("REAL_ROOT", str(ROOT))
    monkeypatch.setenv("CHANGED_FILES", "benchbox/core/runner.py")
    monkeypatch.setenv("ADDED_FILES", "")
    result, _, pushes = _run_pr_open_result(tmp_path, "created", 0, f"TITLE={title}")

    assert result.returncode != 0
    assert pushes == []
    assert b"changelog" in result.stderr.lower()
