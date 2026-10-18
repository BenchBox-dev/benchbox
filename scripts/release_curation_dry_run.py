#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

CLI_DESCRIPTION = "Run tests on a simulated release tree to find tests that read curated-away paths."

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_release_curation import release_cut_rm_commands

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTINE_SELECTION = "(fast or medium) and not (slow or stress or resource_heavy or live_integration)"
NO_TESTS_COLLECTED = 5


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def changed_test_files(root: Path, base: str) -> list[str]:
    out = _git(root, "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD", "--", "tests")
    return [p for p in out.splitlines() if Path(p).name.startswith("test_") and p.endswith(".py")]


@contextmanager
def curated_tree(root: Path, commands: Sequence[Sequence[str]]) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="release-curation-") as tmp:
        tree = Path(tmp) / "tree"
        _git(root, "worktree", "add", "--detach", "--quiet", str(tree), "HEAD")
        try:
            for command in commands:
                subprocess.run(command, cwd=tree, check=True, capture_output=True, text=True)
            yield tree
        finally:
            _git(root, "worktree", "remove", "--force", str(tree))


def pytest_command(targets: Sequence[str], marker: str) -> list[str]:
    parallel = [] if targets else ["-n", "auto"]
    return ["uv", "run", "--", "python", "-m", "pytest", *(targets or ["tests"]), "-m", marker, "-q", *parallel]


def has_collected_tests(tree: Path, targets: Sequence[str], base_cmd: Sequence[str] | None) -> bool:
    argv = (
        [*base_cmd, *targets, "-m", "", "--collect-only", "-q", "-o", "addopts="]
        if base_cmd
        else ["uv", "run", "--", "python", "-m", "pytest", *targets, "-m", "", "--collect-only", "-q", "-o", "addopts="]
    )
    result = subprocess.run(argv, cwd=tree, check=False, capture_output=True, text=True)
    return result.returncode == 0


def run(
    root: Path,
    targets: Sequence[str],
    *,
    makefile: Path | None = None,
    marker: str = ROUTINE_SELECTION,
    command: Sequence[str] | None = None,
) -> int:
    commands = release_cut_rm_commands(makefile or root / "Makefile")
    with curated_tree(root, commands) as tree:
        if targets:
            kept = [t for t in targets if (tree / t).exists()]
            for stripped in sorted(set(targets) - set(kept)):
                print(f"skipped (removed by release-cut): {stripped}")
            if not kept:
                print("release curation dry run: no selected test remains on the release tree")
                return 0
            targets = kept
        argv = [*command, *(targets or ["tests"])] if command else pytest_command(targets, marker)
        result = subprocess.run(argv, cwd=tree, check=False)
        marker_excluded = (
            bool(targets) and result.returncode == NO_TESTS_COLLECTED and has_collected_tests(tree, targets, command)
        )
    if result.returncode == 0:
        print("release curation dry run: OK")
        return 0
    if not targets and result.returncode == NO_TESTS_COLLECTED:
        print("release curation dry run: OK")
        return 0
    if marker_excluded:
        print("release curation dry run: OK (all selected tests are marker-excluded)")
        return 0
    if targets and result.returncode == NO_TESTS_COLLECTED:
        print(
            "release curation dry run: FAILED. Retained test targets were selected, but no tests were collected.",
            file=sys.stderr,
        )
        return 1
    print(
        "release curation dry run: FAILED. A test above fails on the release tree, usually because it reads a "
        "path that release-cut removes. Skip it when that specific file is absent, or add the test to the "
        "release-cut strip list; see 'Tests on the release tree' in docs/internal/release-guide.md.",
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("tests", nargs="*", help="test paths to run; default: every test in the fast and medium tiers")
    parser.add_argument("--changed-since", metavar="REF", help="run only test modules changed since REF")
    args = parser.parse_args(argv)
    targets = list(args.tests)
    if args.changed_since:
        targets += changed_test_files(REPO_ROOT, args.changed_since)
        if not targets:
            print(f"release curation dry run: no test modules changed since {args.changed_since}")
            return 0
    return run(REPO_ROOT, targets)


if __name__ == "__main__":
    sys.exit(main())
