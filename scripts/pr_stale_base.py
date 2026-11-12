#!/usr/bin/env python3

from __future__ import annotations

import ast
import sys
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_landing

Runner = Callable[[list[str]], tuple[int, str]]

DEVELOP_COMMIT_LIMIT = 100
DEVELOP_SOURCE_READ_LIMIT = 60
SOURCE_ROOTS = ("", "scripts/", "tests/")
TEST_CONFIG_FILES = ("pytest.ini", "pytest-ci.ini")
FILE_NAMES_JQ = ".filename, (.previous_filename // empty)"
LATEST_CI_RUN_JQ = r'[.workflow_runs[] | select(.display_title == "CI") | .created_at] | max // empty'


def imported_paths(source: str, relative_to: str, exists: Callable[[str], bool]) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    found: set[str] = set()

    def add_module(parts: Sequence[str]) -> None:
        if not parts:
            return
        for depth in range(1, len(parts)):
            package = "/".join(parts[:depth])
            if exists(f"{package}/__init__.py"):
                found.add(f"{package}/__init__.py")
        base = "/".join(parts)
        for root in SOURCE_ROOTS:
            for candidate in (f"{root}{base}.py", f"{root}{base}/__init__.py"):
                if exists(candidate):
                    found.add(candidate)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_module(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            parts = node.module.split(".") if node.module else []
            if node.level:
                anchor = list(PurePosixPath(relative_to).parent.parts)
                drop = node.level - 1
                if drop > len(anchor):
                    continue
                parts = anchor[: len(anchor) - drop] + parts
            add_module(parts)
            for alias in node.names:
                add_module([*parts, alias.name])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if (
                "/" in value
                and value.isprintable()
                and " " not in value
                and not value.startswith("/")
                and exists(value)
            ):
                found.add(value)
    return found


def _lines(run: Runner, command: list[str], what: str) -> list[str]:
    code, out = run(command)
    if code != 0:
        raise pr_landing.LandingError(f"cannot {what}: {out.strip()[:200]}")
    return [line for line in out.splitlines() if line.strip()]


def tested_at(run: Runner, repo: str, head: str) -> str | None:
    stamps = _lines(
        run,
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            LATEST_CI_RUN_JQ,
            f"repos/{repo}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100",
        ],
        f"read the CI runs of {head[:9]}",
    )
    return max(stamps) if stamps else None


def related_develop_changes(
    run: Runner, repo: str, number: int, head: str, base: str, checkout: Path
) -> tuple[str | None, list[str], list[str]]:
    since = tested_at(run, repo, head)
    if since is None:
        return None, [], []
    commits = _lines(
        run,
        [
            "gh",
            "api",
            "--jq",
            ".[].sha",
            f"repos/{repo}/commits?sha={base}&since={since}&per_page={DEVELOP_COMMIT_LIMIT}",
        ],
        f"list the {base} commits after {since}",
    )
    if not commits:
        return since, [], []
    if len(commits) >= DEVELOP_COMMIT_LIMIT:
        return since, [], [f"at least {DEVELOP_COMMIT_LIMIT} commits"]
    pr_files = set(
        _lines(
            run,
            ["gh", "api", "--paginate", "--jq", f".[] | {FILE_NAMES_JQ}", f"repos/{repo}/pulls/{number}/files"],
            f"list the files changed by PR #{number}",
        )
    )
    root = checkout.resolve()

    def exists(path: str) -> bool:
        return (root / path).is_file()

    related = set(pr_files)
    for path in sorted(pr_files):
        if path.endswith(".py") and exists(path):
            related |= imported_paths((root / path).read_text(encoding="utf-8"), path, exists)
    for path in sorted(pr_files):
        if path.startswith("tests/") and path.endswith(".py"):
            for ancestor in PurePosixPath(path).parents:
                conftest = f"{ancestor}/conftest.py"
                if exists(conftest):
                    related.add(conftest)
            related.update(name for name in TEST_CONFIG_FILES if exists(name))
    develop_files: set[str] = set()
    for sha in commits:
        develop_files.update(
            _lines(
                run,
                ["gh", "api", "--paginate", "--jq", f".files[] | {FILE_NAMES_JQ}", f"repos/{repo}/commits/{sha}"],
                f"list the files of {base} commit {sha[:9]}",
            )
        )
    overlap = develop_files & related
    sources = sorted(path for path in develop_files - overlap if path.endswith(".py"))
    if len(sources) > DEVELOP_SOURCE_READ_LIMIT:
        return since, sorted(overlap), [f"{len(sources)} Python files changed on {base}"]
    for path in sources:
        code, text = run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw",
                f"repos/{repo}/contents/{quote(path)}?ref={commits[0]}",
            ]
        )
        if code != 0 and "Not Found" in text:
            continue
        if code != 0:
            raise pr_landing.LandingError(f"cannot read {path} at {base} {commits[0][:9]}: {text.strip()[:200]}")
        if imported_paths(text, path, lambda candidate: candidate in pr_files or exists(candidate)) & pr_files:
            overlap.add(path)
    return since, sorted(overlap), []


def stale_base_refusals(run: Runner, repo: str, number: int, head: str, base: str, checkout: Path) -> list[str]:
    since, files, unbounded = related_develop_changes(run, repo, number, head, base, checkout)
    if since is None:
        return [f"no CI run is recorded for {head[:9]}, so the base it was tested on cannot be established"]
    if not files and not unbounded:
        return []
    if files:
        named = ", ".join(files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "")
        reason = f"{base} changed files related to this PR after its CI run began at {since}: {named}"
    else:
        reason = f"{base} gained {unbounded[0]} after this PR's CI run began at {since}, too many to compare"
    close = ["gh", "pr", "close", str(number), "--repo", repo]
    reopen = ["gh", "pr", "reopen", str(number), "--repo", repo]
    code, out = run(close)
    if code != 0:
        return [f"{reason}; starting CI on a fresh merge ref failed at `gh pr close`: {out.strip()[:200]}"]
    code, out = run(reopen)
    if code != 0:
        code, out = run(reopen)
    if code != 0:
        return [
            f"{reason}; the PR was closed to start CI on a fresh merge ref and could not be reopened, "
            f"run gh pr reopen {number}: {out.strip()[:200]}"
        ]
    return [f"{reason}; started CI on a fresh merge ref, waiting for full CI run on it before arming"]
