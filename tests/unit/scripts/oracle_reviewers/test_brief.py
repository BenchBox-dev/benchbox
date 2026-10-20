from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.brief import (
    FILE_LIST_DIFF_LINE,
    FULL_DIFF_LINE,
    FULL_DIFF_PLACEHOLDER,
    with_full_diff,
    write_private,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_with_full_diff_fills_first_placeholder_only(tmp_path: Path) -> None:
    path = tmp_path / "full.diff"
    text = f"a {FULL_DIFF_PLACEHOLDER} b {FULL_DIFF_PLACEHOLDER}"
    filled = with_full_diff(text, path)
    assert filled == f"a {path} b {FULL_DIFF_PLACEHOLDER}"


def test_with_full_diff_drops_full_line_without_path() -> None:
    text = f"before\n{FULL_DIFF_LINE}after\n"
    assert with_full_diff(text, None) == "before\nafter\n"


def test_with_full_diff_drops_file_list_line_without_path() -> None:
    text = f"before\n{FILE_LIST_DIFF_LINE}after\n"
    assert with_full_diff(text, None) == "before\nafter\n"


def test_with_full_diff_removes_only_line_holding_first_placeholder() -> None:
    text = FILE_LIST_DIFF_LINE + FILE_LIST_DIFF_LINE
    assert with_full_diff(text, None) == FILE_LIST_DIFF_LINE


def test_with_full_diff_leaves_text_without_placeholder_unchanged() -> None:
    assert with_full_diff("no placeholders here", None) == "no placeholders here"


def test_with_full_diff_leaves_bare_placeholder_outside_known_lines() -> None:
    text = f"bare {FULL_DIFF_PLACEHOLDER} here"
    assert with_full_diff(text, None) == text


def test_write_private_creates_parents_with_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dirs" / "brief.md"
    write_private(path, "secret")
    assert path.read_text(encoding="utf-8") == "secret"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_private_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "brief.md"
    write_private(path, "first")
    with pytest.raises(FileExistsError):
        write_private(path, "second")
    assert path.read_text(encoding="utf-8") == "first"
