#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RECOVERY_SUFFIX = "dispatch site-inputs.yml with parent_core_sha set to the last deployed core_sha"


class ParentResolutionError(RuntimeError):
    pass


def _git(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *cmd], cwd=str(ROOT), check=False, text=True, capture_output=True)


def _gh(args: Sequence[str]) -> str:
    proc = subprocess.run(["gh", *args], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise ParentResolutionError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc.stdout


def resolve_commit(ref: str) -> str:
    proc = _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not SHA_RE.match(sha):
        raise ParentResolutionError("parent_core_sha does not name a commit")
    return sha


def is_ancestor(parent: str, core: str) -> bool:
    return _git("merge-base", "--is-ancestor", parent, core).returncode == 0


def decide_parent(
    core_sha: str,
    dispatch_parent: str | None,
    bundled_shas: Sequence[str],
    is_ancestor_fn: Callable[[str, str], bool],
    parent_state_fn: Callable[[str], str],
) -> tuple[str, str]:
    if dispatch_parent:
        if dispatch_parent == core_sha or not is_ancestor_fn(dispatch_parent, core_sha):
            raise ParentResolutionError(f"parent_core_sha {dispatch_parent} is not an ancestor of {core_sha}")
        state = parent_state_fn(dispatch_parent)
        if state == "bundle":
            return (dispatch_parent, "bundle")
        if state == "expired":
            raise ParentResolutionError(f"site-inputs bundle for parent {dispatch_parent} is expired")
        pooled = [sha for sha in bundled_shas if sha.strip()]
        if not pooled:
            return (dispatch_parent, "dispatch")
        raise ParentResolutionError(f"no site-inputs bundle attested on parent {dispatch_parent}")
    for candidate in bundled_shas:
        if candidate and candidate != core_sha and is_ancestor_fn(candidate, core_sha):
            return (candidate, "bundle")
    raise ParentResolutionError(f"no unexpired develop bundle is an ancestor of {core_sha}; {RECOVERY_SUFFIX}")


def decide_certified_by(
    core_sha: str,
    run_id: str,
    trunk_runs: Sequence[dict],
) -> str:
    if run_id.strip():
        return run_id.strip()
    for run in trunk_runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("event")) == "push" and run.get("headSha") == core_sha:
            return str(run.get("databaseId"))
    raise ParentResolutionError(f"no successful trunk.yml push run on {core_sha}")


def recent_trunk_runs(repo: str, limit: int = 20) -> list[dict]:
    raw = _gh(
        [
            "run",
            "list",
            "--workflow",
            "trunk.yml",
            "--branch",
            "develop",
            "--status",
            "success",
            "--json",
            "databaseId,headSha,event",
            "--limit",
            str(limit),
        ]
    )
    try:
        rows = json.loads(raw)
    except ValueError as error:
        raise ParentResolutionError("cannot list trunk.yml runs") from error
    if not isinstance(rows, list):
        raise ParentResolutionError("cannot list trunk.yml runs")
    return [row for row in rows if isinstance(row, dict)]


def recent_bundled_shas(repo: str, limit: int = 100) -> list[str]:
    raw = _gh(
        [
            "run",
            "list",
            "--workflow",
            "site-inputs.yml",
            "--branch",
            "develop",
            "--status",
            "success",
            "--json",
            "databaseId",
            "--limit",
            str(limit),
            "-q",
            ".[].databaseId",
        ]
    )
    run_ids = [line.strip() for line in raw.split() if line.strip()]
    ordered: list[str] = []
    seen: set[str] = set()
    for run_id in run_ids:
        names = _gh(
            [
                "api",
                f"repos/{repo}/actions/runs/{run_id}/artifacts",
                "-q",
                '.artifacts[] | select(.expired == false and (.name | startswith("site-inputs-")))'
                ' | .name | ltrimstr("site-inputs-")',
            ]
        )
        for name in [item.strip() for item in names.split() if item.strip()]:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def parent_bundle_state(parent_sha: str, repo: str) -> str:
    expected = f"site-inputs-{parent_sha}"
    runs_raw = _gh(
        [
            "api",
            f"repos/{repo}/actions/workflows/site-inputs.yml/runs?branch=develop&status=success"
            f"&per_page=100&head_sha={parent_sha}",
            "-q",
            ".workflow_runs[].id",
        ]
    )
    run_ids = [line.strip() for line in runs_raw.split() if line.strip()]
    if not run_ids:
        return "missing"
    expired_seen = False
    for run_id in run_ids:
        rows = _gh(
            [
                "api",
                f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
                "-q",
                f'.artifacts[] | select(.name == "{expected}") | "\\(.name) \\(.expired)"',
            ]
        )
        for line in rows.splitlines():
            parts = line.strip().split()
            if len(parts) != 2 or parts[0] != expected:
                continue
            if parts[1] == "false":
                return "bundle"
            expired_seen = True
    if expired_seen:
        return "expired"
    return "missing"


def resolve_and_decide(
    core_sha: str,
    dispatch_parent_raw: str,
    repo: str,
    limit: int,
    run_id: str,
) -> tuple[str, str, str]:
    dispatch_parent = resolve_commit(dispatch_parent_raw) if dispatch_parent_raw.strip() else None
    bundled = recent_bundled_shas(repo, limit)
    parent_sha, parent_source = decide_parent(
        core_sha, dispatch_parent, bundled, is_ancestor, lambda sha: parent_bundle_state(sha, repo)
    )
    certified_by = decide_certified_by(core_sha, run_id, recent_trunk_runs(repo))
    return (parent_sha, parent_source, certified_by)


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Resolve the site-inputs bundle parent by SHA.")
    parser.add_argument("--core-sha", default="")
    parser.add_argument("--dispatch-parent", default=env.get("DISPATCH_PARENT", ""))
    parser.add_argument("--repo", default=env.get("REPO", env.get("GITHUB_REPOSITORY", "")))
    parser.add_argument("--run-id", default=env.get("RUN_ID", ""))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if not args.repo:
            raise ParentResolutionError("missing repository for parent lookup")
        if args.core_sha:
            core_sha = args.core_sha.strip()
            if not SHA_RE.match(core_sha):
                raise ParentResolutionError("core_sha is not a full commit SHA")
        else:
            proc = _git("rev-parse", "HEAD")
            core_sha = proc.stdout.strip()
            if proc.returncode != 0 or not SHA_RE.match(core_sha):
                raise ParentResolutionError("cannot resolve HEAD to a full commit SHA")
        parent_sha, parent_source, certified_by = resolve_and_decide(
            core_sha, args.dispatch_parent, args.repo, args.limit, args.run_id
        )
    except ParentResolutionError as error:
        print(str(error), file=sys.stderr)
        return 1
    output = env.get("GITHUB_OUTPUT", "")
    lines = [f"parent_sha={parent_sha}", f"parent_source={parent_source}", f"certified_by={certified_by}"]
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
