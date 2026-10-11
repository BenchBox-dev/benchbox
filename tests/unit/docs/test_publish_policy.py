from __future__ import annotations

import runpy
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DOCS = Path(__file__).parents[3] / "docs"
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


@pytest.fixture(scope="module")
def conf() -> dict:
    return runpy.run_path(str(DOCS / "conf.py"))


def test_sphinx_excludes_every_listed_path(conf: dict) -> None:
    assert set(conf["exclude_patterns"]) >= EXCLUDED_ROOTS | EXCLUDED_FILES


def test_sphinx_excludes_every_unlisted_page_in_gated_directories(conf: dict) -> None:
    excluded = set(conf["exclude_patterns"])
    assert gated_pages() - set(ALLOWLIST) <= excluded
    assert not set(ALLOWLIST) & excluded


def test_sphinx_excludes_a_new_unlisted_page(conf: dict, tmp_path: Path) -> None:
    (tmp_path / "development").mkdir()
    (tmp_path / "development" / "kept.md").write_text("# Kept\n")
    (tmp_path / "development" / "new-page.md").write_text("# New\n")
    (tmp_path / "development" / "kept.rst").write_text("Kept\n====\n")
    (tmp_path / "development" / "new-page.rst").write_text("New\n===\n")
    (tmp_path / "publish-allowlist.txt").write_text("# comment\ndevelopment/kept.md\ndevelopment/kept.rst\n")
    exclude_patterns = conf["_unlisted_publish_list_pages"](tmp_path)
    assert exclude_patterns == ["development/new-page.md", "development/new-page.rst"]
    assert sphinx_docnames(tmp_path, exclude_patterns) == {"development/kept"}


def test_missing_publish_list_publishes_nothing_in_gated_directories(conf: dict, tmp_path: Path) -> None:
    (tmp_path / "operations").mkdir()
    (tmp_path / "operations" / "runbook.md").write_text("# Runbook\n")
    assert conf["_unlisted_publish_list_pages"](tmp_path) == ["operations/runbook.md"]


def sphinx_docnames(srcdir: Path, exclude_patterns: list[str]) -> set[str]:
    from sphinx.project import Project

    return Project(str(srcdir), [".md", ".rst"]).discover(exclude_paths=exclude_patterns)


def test_sphinx_discovers_no_excluded_or_unlisted_page(conf: dict) -> None:
    docnames = sphinx_docnames(DOCS, conf["exclude_patterns"])
    assert docnames
    assert sorted(name for name in docnames if name.split("/")[0] in EXCLUDED_ROOTS) == []
    assert (
        sorted(name for name in docnames if any(f"{name}{suffix}" in EXCLUDED_FILES for suffix in (".md", ".rst")))
        == []
    )
    gated = {name for name in docnames if name.split("/")[0] in PUBLISH_LIST_ROOTS}
    assert sorted(name for name in gated if not any(f"{name}{suffix}" in ALLOWLIST for suffix in (".md", ".rst"))) == []


def test_sphinx_excludes_everything_under_a_listed_directory(tmp_path: Path) -> None:
    for relative in ("kept.md", "agent/notes.md", "internal/deep/runbook.md", "guides/user.md"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("# Page\n")
    patterns = [entry.rstrip("/") for entry in ("agent/", "internal/")]
    assert sphinx_docnames(tmp_path, patterns) == {"kept", "guides/user"}
