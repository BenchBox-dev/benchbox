from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
DOCS = ROOT / "docs"
SPEC = importlib.util.spec_from_file_location("check_doc_external_links", ROOT / "scripts/check_doc_external_links.py")
assert SPEC and SPEC.loader
links = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = links
SPEC.loader.exec_module(links)
PUBLISH_LIST_ROOTS = ("development", "operations")


def read_list(name: str) -> list[str]:
    lines = (line.strip() for line in (DOCS / name).read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def gated_pages() -> set[str]:
    return {
        page.relative_to(DOCS).as_posix()
        for root in PUBLISH_LIST_ROOTS
        for suffix in ("*.md", "*.rst")
        for page in (DOCS / root).rglob(suffix)
    }


ALLOWLIST = read_list("publish-allowlist.txt")
EXCLUSIONS = read_list("publish-exclusions.txt")
EXCLUDED_ROOTS = {entry.rstrip("/") for entry in EXCLUSIONS if entry.endswith("/")}
EXCLUDED_FILES = {entry for entry in EXCLUSIONS if not entry.endswith("/")}

HOW_TO_FIX = (
    "Decide whether each page is for people who use BenchBox or for outside contributors. "
    "If it is, add it to docs/publish-allowlist.txt. If not, move it to docs/internal/ "
    "or list it in docs/publish-exclusions.txt."
)


def test_every_gated_page_is_published_or_excluded() -> None:
    unclassified = sorted(gated_pages() - set(ALLOWLIST) - EXCLUDED_FILES)
    assert unclassified == [], HOW_TO_FIX


def test_lists_have_no_duplicates_and_do_not_overlap() -> None:
    assert len(ALLOWLIST) == len(set(ALLOWLIST))
    assert len(EXCLUSIONS) == len(set(EXCLUSIONS))
    assert not set(ALLOWLIST) & EXCLUDED_FILES


def test_allowlist_names_existing_pages_in_gated_directories() -> None:
    assert ALLOWLIST
    assert [page for page in ALLOWLIST if not (DOCS / page).is_file()] == []
    assert [page for page in ALLOWLIST if not page.startswith(tuple(f"{root}/" for root in PUBLISH_LIST_ROOTS))] == []


def test_excluded_directories_are_top_level_and_excluded_files_exist() -> None:
    assert {"agent", "internal"} <= EXCLUDED_ROOTS
    assert [root for root in EXCLUDED_ROOTS if "/" in root] == []
    assert [path for path in EXCLUDED_FILES if not (DOCS / path).is_file()] == []


def published(docs: Path = DOCS) -> set[str]:
    return {page.relative_to(docs).as_posix() for page in links.published_pages(docs)}


def test_published_pages_leave_out_every_listed_path() -> None:
    pages = published()
    assert pages
    assert sorted(page for page in pages if page.split("/", 1)[0] in EXCLUDED_ROOTS) == []
    assert sorted(pages & EXCLUDED_FILES) == []


def test_published_pages_in_gated_directories_are_allowlisted() -> None:
    gated = {page for page in published() if page.split("/", 1)[0] in PUBLISH_LIST_ROOTS}
    assert gated <= set(ALLOWLIST)
    assert {page for page in ALLOWLIST if page.endswith(".md")} <= published()


def test_a_new_unlisted_page_in_a_gated_directory_is_not_published(tmp_path: Path) -> None:
    (tmp_path / "development").mkdir()
    (tmp_path / "development" / "kept.md").write_text("# Kept\n")
    (tmp_path / "development" / "new-page.md").write_text("# New\n")
    (tmp_path / "publish-allowlist.txt").write_text("# comment\ndevelopment/kept.md\n")
    assert published(tmp_path) == {"development/kept.md"}


def test_missing_publish_list_publishes_nothing_in_gated_directories(tmp_path: Path) -> None:
    (tmp_path / "operations").mkdir()
    (tmp_path / "operations" / "runbook.md").write_text("# Runbook\n")
    assert published(tmp_path) == set()


def test_everything_under_a_listed_directory_is_excluded(tmp_path: Path) -> None:
    for relative in ("kept.md", "agent/notes.md", "internal/deep/runbook.md", "guides/user.md"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("# Page\n")
    (tmp_path / "publish-exclusions.txt").write_text("agent/\ninternal/\n")
    assert published(tmp_path) == {"kept.md", "guides/user.md"}
