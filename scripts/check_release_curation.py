#!/usr/bin/env python3

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISION_DOC = REPO_ROOT / "_project" / "decisions" / "single-repo-migration.md"
MAKEFILE = REPO_ROOT / "Makefile"
DEVELOPMENT_TREE_TARGETS_VARIABLE = "DEVELOPMENT_TREE_ONLY_TARGETS"
CURATED_RESUMABLE_PROJECT_TARGETS = {"release-cut"}
REQUIRED_CURATED_PATHS = frozenset(
    {
        ".github/workflows/results-explorer-browser.yml",
        ".github/workflows/seed-corpus.yml",
        ".github/workflows/sync-results-data-to-published.yml",
        ".github/workflows/validate-submission.yml",
    }
)
REQUIRED_RELEASE_PATHS = frozenset(
    {
        "results-data",
        "_project/scripts/explorer_pipeline",
        "_project/scripts/explorer_publish.py",
        "_project/scripts/results_explorer_snapshot_invariants.py",
    }
)


def parse_main_only_allowlist(doc: Path) -> set[str]:
    text = doc.read_text(encoding="utf-8")
    matches = re.findall(
        r"\*\*`main` only\*\*[^:\n]*:(.*?)(?=\n\s*-\s*\*\*|\n\n|\s*\Z)",
        text,
        re.DOTALL,
    )
    if not matches:
        sys.exit(f"ERROR: could not find any 'main only' bullet in {doc}")
    paths: set[str] = set()
    for body in matches:
        paths.update(re.findall(r"`([^`]+)`", body))
    return {p.rstrip("/") for p in paths}


def release_cut_rm_commands(makefile: Path) -> list[list[str]]:
    text = makefile.read_text(encoding="utf-8")
    match = re.search(
        r"^release-cut:[^\n]*\n((?:[ \t].*\n|\n)+)",
        text,
        re.MULTILINE,
    )
    if not match:
        sys.exit(f"ERROR: could not find release-cut: target in {makefile}")
    commands: list[list[str]] = []
    for line in match.group(1).splitlines():
        rm_match = re.search(r"git rm (?:-rf|-f)(?: --ignore-unmatch)? (.+?)$", line.strip())
        if rm_match:
            commands.append(shlex.split(rm_match.group(0)))
    return commands


def parse_curation_list(makefile: Path) -> set[str]:
    paths: set[str] = set()
    for command in release_cut_rm_commands(makefile):
        paths.update(p for p in command[2:] if not p.startswith("-"))
    return paths


def parse_development_tree_targets(makefile: Path) -> set[str]:
    lines = makefile.read_text(encoding="utf-8").splitlines()
    values: list[str] = []
    collecting = False
    for line in lines:
        if not collecting:
            prefix = f"{DEVELOPMENT_TREE_TARGETS_VARIABLE} :="
            if not line.startswith(prefix):
                continue
            line = line[len(prefix) :].strip()
            collecting = True
        continued = line.endswith("\\")
        values.extend(line.removesuffix("\\").split())
        if not continued:
            break
    return set(values)


def project_dependent_make_targets(root: Path) -> set[str]:
    targets: set[str] = set()
    for path in [root / "Makefile", *sorted((root / "make").glob("*.mk"))]:
        target: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if (
                line
                and not line[0].isspace()
                and ":" in line
                and ":=" not in line
                and not line.startswith(("#", "define", "endef"))
            ):
                target = line.split(":", 1)[0].strip()
            recipe = line.lstrip("\t")
            if (
                not line.startswith("\t")
                or not target
                or target in {".development-tree-required", ".release-cut-tree-required"}
                or "_project/" not in recipe
            ):
                continue
            if recipe.lstrip("@").startswith(("#", "echo ")):
                continue
            targets.add(target)
    return targets


def development_tree_target_findings(root: Path) -> list[str]:
    declared = parse_development_tree_targets(root / "Makefile")
    required = project_dependent_make_targets(root) - CURATED_RESUMABLE_PROJECT_TARGETS
    return [
        f"Make target {target!r} references _project/ but is not declared development-only"
        for target in sorted(required - declared)
    ]


def list_tracked_top_level() -> set[str]:
    result = subprocess.run(
        ["git", "ls-tree", "HEAD", "--name-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def main() -> int:
    main_only = parse_main_only_allowlist(DECISION_DOC)
    curated = parse_curation_list(MAKEFILE)
    tracked = list_tracked_top_level()

    missing_release_paths = sorted(REQUIRED_RELEASE_PATHS - main_only)
    if missing_release_paths:
        print("ERROR: the curated-preview release scope is missing required shipped paths:")
        for path in missing_release_paths:
            print(f"  - {path}")
        print()
        print("Add each path to the release-shipped amendment in single-repo-migration.md.")
        return 1

    missing_required = sorted(REQUIRED_CURATED_PATHS - curated)
    if missing_required:
        print("ERROR: release-cut is missing required deferred-path curation:")
        for path in missing_required:
            print(f"  - {path}")
        print()
        print("These paths stay on develop but must be git-rm'd from release branches.")
        return 1

    accounted = main_only | curated
    unaccounted = sorted(tracked - accounted)

    if not unaccounted:
        print(
            f"OK: all {len(tracked)} top-level tracked paths are accounted for "
            f"({len(main_only)} main-only, {len(curated)} curated)."
        )
        return 0

    print("ERROR: the following top-level paths are not accounted for:")
    for path in unaccounted:
        print(f"  - {path}")
    print()
    print("Each unaccounted-for path must be added to ONE of:")
    print("  (a) the A3 'main only' allowlist in _project/decisions/single-repo-migration.md (if it ships to main), OR")
    print(
        "  (b) the curation list in the release-cut: target in Makefile "
        "(if it is dev-only and should be git-rm'd from the release branch)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
