#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "_project" / "scripts" / "project_references_baseline.txt"

_REFERENCE_RE = re.compile(r"_project/[A-Za-z0-9_./@-]+\.(?:md|yaml|yml|json)(?![A-Za-z0-9_.~-])")

_PLACEHOLDER_STEMS = frozenset({"foo", "bar", "example"})

_SKIP_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".pdf",
        ".zip",
        ".gz",
        ".zst",
        ".parquet",
        ".duckdb",
        ".db",
        ".so",
        ".dylib",
        ".pyc",
    }
)


CLI_DESCRIPTION = (
    "Fail on NEW stale ``_project/`` references from tracked files outside ``_project/``.\n"
    "\n"
    "Docs are sometimes created inside ``_project/`` and linked from outside\n"
    "(``docs/``, ``README.md``, ``CLAUDE.md``, ...). When the ``_project/`` file is\n"
    "moved, archived, or deleted, the outside link goes stale silently. This lint\n"
    "catches new occurrences at CI time.\n"
    "\n"
    "Pre-existing stale references are recorded in a baseline file so this gate\n"
    "blocks *new* breakage immediately without forcing a repo-wide cleanup. The\n"
    "baseline cannot rot: an entry whose target now resolves (or whose scanned\n"
    "file no longer mentions it) is reported as stale and must be removed\n"
    "(regenerate with ``--update-baseline``).\n"
    "\n"
    "Behavior:\n"
    "\n"
    "* Scan every tracked file outside ``_project/`` for ``_project/<path>``\n"
    "  mentions ending in ``.md`` (plus ``.yaml``/``.yml``/``.json``).\n"
    "* Resolve each mention against the repo root. A mention is stale when the\n"
    "  path does not exist in the working tree.\n"
    "* Bare placeholder names (``foo``, ``bar``, ``example``) mark synthetic\n"
    "  fixture strings and are ignored everywhere.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python _project/scripts/check_project_references.py                   # check (CI mode)\n"
    "    uv run -- python _project/scripts/check_project_references.py --update-baseline # regenerate baseline\n"
    "\n"
    "Exit codes:\n"
    "    0 - No new stale references; baseline is current\n"
    "    1 - New stale reference(s) found, or baseline contains stale entries\n"
)


def _tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO_ROOT, text=False)
    return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]


def _normalize(match: str) -> str:
    return match.rstrip("`.,:;!?)]}'\">")


def _is_placeholder(ref: str) -> bool:
    stem = ref.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "." in ref.rsplit("/", 1)[-1] else ref
    return stem in _PLACEHOLDER_STEMS


def find_stale() -> list[str]:
    stale: list[str] = []
    for tracked in _tracked_files():
        if tracked.startswith(("_project/", "tests/fixtures/fast_tier_baseline/")):
            continue
        if Path(tracked).suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            text = (REPO_ROOT / tracked).read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        seen: set[str] = set()
        for raw in _REFERENCE_RE.findall(text):
            ref = _normalize(raw)
            if ref in seen or _is_placeholder(ref):
                continue
            seen.add(ref)
            if not (REPO_ROOT / ref).exists():
                stale.append(f"{tracked}::{ref}")
    return sorted(stale)


def _read_baseline() -> list[str]:
    if not BASELINE_PATH.exists():
        return []
    return sorted(line.strip() for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines() if line.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate the baseline from the current tree.",
    )
    args = parser.parse_args(argv)

    stale = find_stale()
    if args.update_baseline:
        BASELINE_PATH.write_text("\n".join(stale) + ("\n" if stale else ""), encoding="utf-8")
        print(f"baseline updated: {len(stale)} entr(y/ies) in {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    baseline = _read_baseline()
    baseline_set = set(baseline)
    stale_set = set(stale)

    new = sorted(stale_set - baseline_set)
    fixed = sorted(baseline_set - stale_set)

    if not new and not fixed:
        print(f"project references: OK - {len(stale)} known stale reference(s), no new breakage.")
        return 0
    if new:
        print(f"project references: {len(new)} NEW stale _project/ reference(s):")
        for key in new:
            scanned, _, ref = key.partition("::")
            print(f"  {scanned}: {ref}")
    if fixed:
        print(f"project references: {len(fixed)} baseline entr(y/ies) now resolve - regenerate:")
        for key in fixed:
            print(f"  {key}")
        print("\nRun: uv run -- python _project/scripts/check_project_references.py --update-baseline")
    print("\nFix new entries by updating the link, restoring the file, or removing the reference.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
