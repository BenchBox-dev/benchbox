#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = CHECKOUT_ROOT / "_project" / "analysis" / "known-stranded-commits.txt"

STRUCTURAL_PREFIXES = ("auto-revert/", "auto/results-mirror/", "auto/results-mirror-")
STRUCTURAL_EXACT = {"main", "release", "published-results", "gh-pages", "HEAD"}
_VERSION_BRANCH_RE = re.compile(r"^v\d")


CLI_DESCRIPTION = (
    "Detect orphaned (stranded) commits left on dead remote branches.\n"
    "\n"
    "Root cause (see _project/analysis/auto-merge-stranding-forensic.md): a commit\n"
    "pushed to a feature branch AFTER that branch's only PR has already\n"
    "squash-merged and closed is never covered by any `pull_request` event, so\n"
    "`auto-merge-on-open.yml` never sees it and its content silently never reaches\n"
    "`develop`. This happened three times in the results-explorer/publication\n"
    "remediation and was only caught by a manual adversarial re-review.\n"
    "\n"
    "This detector is the scheduled backstop. For every remote branch whose PR has\n"
    "merged, it flags commits that sit *after* the PR's pre-squash head (i.e. pushed\n"
    "post-merge) and are not on `develop`. Squash-merge is handled correctly: we do\n"
    "NOT compare patch-ids (a squash rewrites them); we compare against the exact\n"
    "merged PR head SHA reported by the GitHub API, so only genuinely-post-merge\n"
    "commits are flagged.\n"
    "\n"
    "Commits listed in the allowlist (_project/analysis/known-stranded-commits.txt)\n"
    "are historical, already-resolved cases and do not fail the run. Any orphan NOT\n"
    "in the allowlist is a NEW occurrence and exits non-zero.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python _project/scripts/detect_orphaned_commits.py\n"
    "    uv run -- python _project/scripts/detect_orphaned_commits.py --repo owner/name\n"
    "\n"
    "Auth: reads GITHUB_TOKEN from the environment (required to query PRs). Exit 0 =\n"
    "no new orphans; 1 = new orphan(s) found; 2 = usage/IO error.\n"
)


def parse_allowlist(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line.lower())
    return out


def is_acknowledged(sha: str, allowlist: set[str]) -> bool:
    sha = sha.lower()
    return any(sha == a or sha.startswith(a) or a.startswith(sha) for a in allowlist)


def is_structural(branch: str) -> bool:
    return (
        branch in STRUCTURAL_EXACT or branch.startswith(STRUCTURAL_PREFIXES) or bool(_VERSION_BRANCH_RE.match(branch))
    )


def classify(
    branch_orphans: dict[str, list[str]],
    allowlist: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    new: dict[str, list[str]] = {}
    acknowledged: dict[str, list[str]] = {}
    for branch, shas in branch_orphans.items():
        fresh = [s for s in shas if not is_acknowledged(s, allowlist)]
        known = [s for s in shas if is_acknowledged(s, allowlist)]
        if fresh:
            new[branch] = fresh
        if known:
            acknowledged[branch] = known
    return new, acknowledged


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True, cwd=CHECKOUT_ROOT
    ).stdout.strip()


def remote_branches() -> list[str]:
    out = _git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
    branches = []
    for ref in out.splitlines():
        name = ref.removeprefix("origin/")
        if name and name != "HEAD":
            branches.append(name)
    return branches


def commits_after_head(merged_head: str, branch: str) -> list[str]:
    try:
        out = _git("rev-list", f"{merged_head}..origin/{branch}", "^origin/develop")
    except subprocess.CalledProcessError:
        return []
    return [s for s in out.splitlines() if s]


def _api(url: str, token: str) -> list[dict]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "benchbox-orphan-detector",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return []
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
    return []


def merged_head_for_branch(owner: str, repo: str, branch: str, token: str) -> str | None:
    prs = _api(
        f"https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all&per_page=100",
        token,
    )
    if not prs:
        return None
    if any(pr.get("state") == "open" for pr in prs):
        return None
    latest_pr = max(prs, key=lambda pr: pr["number"])
    if not latest_pr.get("merged_at"):
        return None
    return latest_pr["head"]["sha"]


def find_orphans(owner: str, repo: str, token: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for branch in remote_branches():
        if is_structural(branch):
            continue
        head = merged_head_for_branch(owner, repo, branch, token)
        if head is None:
            continue
        orphans = commits_after_head(head, branch)
        if orphans:
            result[branch] = orphans
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "BenchBox-dev/BenchBox"),
        help="owner/name (default: $GITHUB_REPOSITORY or BenchBox-dev/BenchBox)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("::error::GITHUB_TOKEN is required to query PRs", file=sys.stderr)
        return 2
    try:
        owner, repo = args.repo.split("/", 1)
    except ValueError:
        print(f"::error::--repo must be owner/name, got {args.repo!r}", file=sys.stderr)
        return 2

    allowlist = parse_allowlist(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    branch_orphans = find_orphans(owner, repo, token)
    new, acknowledged = classify(branch_orphans, allowlist)

    if acknowledged:
        print("Known/acknowledged orphaned commits (allowlisted, not failing):")
        for branch, shas in sorted(acknowledged.items()):
            for s in shas:
                print(f"  - {branch}: {s[:12]}")

    if not new:
        print("No NEW orphaned commits detected.")
        return 0

    print(
        "::error::New orphaned commit(s) detected — commits pushed to a branch "
        "after its PR merged, never covered by any PR and absent from develop. "
        "Re-land the change under a fresh PR (or, if resolved/intentional, add "
        "the SHA to _project/analysis/known-stranded-commits.txt).",
        file=sys.stderr,
    )
    for branch, shas in sorted(new.items()):
        print(f"  branch origin/{branch}:", file=sys.stderr)
        for s in shas:
            print(f"    {s}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
