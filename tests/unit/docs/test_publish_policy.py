from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
DOCS = ROOT / "docs"
PUBLISH_LIST_ROOTS = ("development", "operations")
REQUIRED_EXCLUSIONS = {
    "_project/",
    "agent/",
    "internal/",
    "design/future-state/index.md",
    "design/future-state/contract-index.md",
    "design/future-state/formalize-mcp-internal-apis/README.md",
    "design/future-state/gate-monitoring-behind-optional-extra/README.md",
    "design/future-state/isolate-experimental-core-subsystems/README.md",
    "design/future-state/prune-publishing-subsystem/README.md",
    "design/future-state/remove-release-tooling-from-wheel/README.md",
    "design/future-state/benchmark-family-plugin-seam/README.md",
    "platforms/workaround-index.md",
    "reference/threat-model.md",
    "tpc-licensing-analysis.md",
}


def read_list(name: str) -> list[str]:
    lines = (line.strip() for line in (DOCS / name).read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


ALLOWLIST = read_list("publish-allowlist.txt")
EXCLUSIONS = read_list("publish-exclusions.txt")
EXCLUDED_ROOTS = {entry.rstrip("/") for entry in EXCLUSIONS if entry.endswith("/")}
EXCLUDED_FILES = {entry for entry in EXCLUSIONS if not entry.endswith("/")}
CATALOGUED_PAGES = {
    path.removeprefix("docs/")
    for path in json.loads((ROOT / "quality/docs-ownership.json").read_text(encoding="utf-8"))["files"]
    if path.endswith((".md", ".rst"))
}


def test_every_gated_page_is_published_or_excluded() -> None:
    gated = {page for page in CATALOGUED_PAGES if page.startswith(tuple(f"{root}/" for root in PUBLISH_LIST_ROOTS))}
    assert gated <= set(ALLOWLIST) | EXCLUDED_FILES


def test_lists_have_no_duplicates_and_do_not_overlap() -> None:
    assert len(ALLOWLIST) == len(set(ALLOWLIST))
    assert len(EXCLUSIONS) == len(set(EXCLUSIONS))
    assert not set(ALLOWLIST) & EXCLUDED_FILES
    assert not any(page.startswith(tuple(f"{root}/" for root in EXCLUDED_ROOTS)) for page in ALLOWLIST)


def test_allowlist_names_catalogued_pages_in_gated_directories() -> None:
    assert ALLOWLIST
    assert set(ALLOWLIST) <= CATALOGUED_PAGES
    assert all(page.startswith(tuple(f"{root}/" for root in PUBLISH_LIST_ROOTS)) for page in ALLOWLIST)


def test_excluded_directories_are_top_level_and_excluded_files_are_catalogued() -> None:
    assert set(EXCLUSIONS) >= REQUIRED_EXCLUSIONS
    assert all("/" not in root for root in EXCLUDED_ROOTS)
    assert EXCLUDED_FILES <= CATALOGUED_PAGES
