#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = (
    "Compare develop's mirrored corpus paths with published-results, allowing a fresh validated mirror PR."
)

PUBLISHED_BRANCH = "published-results"
MIRROR_BRANCH_PREFIX = "auto/results-mirror-"
MIRROR_TITLE_PREFIX = "chore(corpus): mirror results-data changes from develop@"
MIRROR_AUTHOR = "github-actions[bot]"
VALIDATE_STATUS_CONTEXT = "corpus-mirror/validate-bundles"
DEFAULT_MAX_AGE_HOURS = 24.0
MAX_LISTED_PATHS = 40
PULLS_PAGE_SIZE = 100
MAX_PULLS_PAGES = 10
ZERO_OID = "0" * 40
FIELD_SEPARATOR = "\x01"

SYNC_WORKFLOW_COMMAND = "gh workflow run sync-results-data-to-published.yml --ref develop"

CANARY_PATHSPECS = (
    "results-data/bundles",
    "results-data/CORPUS_NOTES.md",
    "results-data/SEED_CORPUS_SPEC.md",
    "results-data/README.md",
    "results-data/validate_corpus.py",
    "benchbox/validation/bundle.py",
    "benchbox/core/results/query_status.py",
    "benchbox/core/results/schema_policy.py",
    "benchbox/core/results/subscale_policy.py",
    "benchbox/core/results/timing_fields.py",
    "scripts/validate_submission.py",
    "scripts/publication/validator_parity.py",
    "scripts/generate_corpus_inventory.py",
)

NIGHTLY_PATHSPECS = (
    "results-data/bundles",
    "results-data/CORPUS_NOTES.md",
    "results-data/SEED_CORPUS_SPEC.md",
    "results-data/README.md",
    "results-data/validate_corpus.py",
)

PROFILES = {
    "canary": CANARY_PATHSPECS,
    "nightly": NIGHTLY_PATHSPECS,
}


class LookupFailure(Exception):
    pass


GitHubGet = Callable[[str], Any]


@dataclass(frozen=True)
class Classification:
    develop_ahead: tuple[str, ...]
    published_only: tuple[str, ...]
    content_changed: tuple[str, ...]

    @property
    def stale(self) -> tuple[str, ...]:
        return self.content_changed + self.develop_ahead


@dataclass(frozen=True)
class MirrorCandidate:
    number: int
    head_sha: str
    base_sha: str


@dataclass
class GraceVerdict:
    covered: bool
    pr_number: int | None = None
    oldest_age_hours: float | None = None
    reasons: list[str] = field(default_factory=list)


def run_git(repo: Path, *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise LookupFailure(f"git {' '.join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def object_exists(repo: Path, spec: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", spec],
            cwd=repo,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def diff_names(
    repo: Path, diff_filter: str, published_ref: str, develop_ref: str, pathspecs: Sequence[str]
) -> tuple[str, ...]:
    raw = run_git(
        repo,
        "diff",
        "--name-only",
        f"--diff-filter={diff_filter}",
        published_ref,
        develop_ref,
        "--",
        *pathspecs,
    )
    return tuple(line for line in raw.splitlines() if line)


def classify(repo: Path, published_ref: str, develop_ref: str, pathspecs: Sequence[str]) -> Classification:
    return Classification(
        develop_ahead=diff_names(repo, "A", published_ref, develop_ref, pathspecs),
        published_only=diff_names(repo, "D", published_ref, develop_ref, pathspecs),
        content_changed=diff_names(repo, "M", published_ref, develop_ref, pathspecs),
    )


def blob_ids(repo: Path, ref: str, paths: Sequence[str]) -> dict[str, str | None]:
    if not paths:
        return {}
    requests = "".join(f"{ref}:{path}\n" for path in paths)
    lines = run_git(repo, "cat-file", "--batch-check", stdin=requests).splitlines()
    if len(lines) != len(paths):
        raise LookupFailure(f"git cat-file returned {len(lines)} rows for {len(paths)} paths at {ref}")
    ids: dict[str, str | None] = {}
    for path, line in zip(paths, lines, strict=True):
        parts = line.split()
        ids[path] = parts[0] if len(parts) == 3 and parts[1] == "blob" else None
    return ids


def shallow_boundary(repo: Path) -> frozenset[str]:
    shallow_file = Path(run_git(repo, "rev-parse", "--git-path", "shallow").strip())
    if not shallow_file.is_absolute():
        shallow_file = repo / shallow_file
    if not shallow_file.is_file():
        return frozenset()
    return frozenset(shallow_file.read_text(encoding="utf-8").split())


@dataclass(frozen=True)
class PathChange:
    timestamp: int
    old_oid: str
    new_oid: str


def develop_path_changes(
    repo: Path,
    develop_ref: str,
    pathspecs: Sequence[str],
    wanted: frozenset[str],
) -> dict[str, list[PathChange]]:
    boundary = shallow_boundary(repo)
    raw = run_git(
        repo,
        "log",
        "--first-parent",
        "-m",
        "--raw",
        "--no-renames",
        "--no-abbrev",
        f"--format={FIELD_SEPARATOR}%H\t%ct",
        develop_ref,
        "--",
        *pathspecs,
    )
    changes: dict[str, list[PathChange]] = {}
    commit = ""
    timestamp = 0
    for line in raw.splitlines():
        if line.startswith(FIELD_SEPARATOR):
            commit, _, stamp = line[1:].partition("\t")
            timestamp = int(stamp)
        elif line.startswith(":") and "\t" in line:
            meta, path = line.split("\t", 1)
            if commit in boundary or path not in wanted:
                continue
            fields = meta.split()
            changes.setdefault(path, []).append(PathChange(timestamp, fields[2], fields[3]))
    return changes


def unpublished_since(changes: Sequence[PathChange], published_oid: str | None) -> int | None:
    published = published_oid or ZERO_OID
    oldest: int | None = None
    for change in changes:
        if change.new_oid == published:
            return oldest
        oldest = change.timestamp
    if changes and changes[-1].old_oid == published:
        return oldest
    return None


def oldest_unpublished_change(
    repo: Path,
    published_ref: str,
    develop_ref: str,
    pathspecs: Sequence[str],
    stale: Sequence[str],
) -> int:
    changes = develop_path_changes(repo, develop_ref, pathspecs, frozenset(stale))
    published = blob_ids(repo, published_ref, stale)
    oldest: int | None = None
    for path in stale:
        since = unpublished_since(changes.get(path, []), published[path])
        if since is None:
            raise LookupFailure(
                f"cannot establish when {path} diverged from published-results in available develop history"
            )
        oldest = since if oldest is None else min(oldest, since)
    if oldest is None:
        raise LookupFailure("no stale paths to age")
    return oldest


def gh_api_get(path: str) -> Any:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LookupFailure(f"gh api {path} failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LookupFailure(f"gh api {path} returned invalid JSON: {exc}") from exc


def open_mirror_candidates(
    get: GitHubGet, repo_slug: str, published_tip: str
) -> tuple[list[MirrorCandidate], list[str]]:
    candidates: list[MirrorCandidate] = []
    reasons: list[str] = []
    for page in range(1, MAX_PULLS_PAGES + 1):
        pulls = get(
            f"repos/{repo_slug}/pulls?state=open&base={PUBLISHED_BRANCH}&per_page={PULLS_PAGE_SIZE}&page={page}"
        )
        if not isinstance(pulls, list):
            raise LookupFailure("pull request listing was not a list")
        for pull in pulls:
            head = pull.get("head") or {}
            base = pull.get("base") or {}
            head_repo = (head.get("repo") or {}).get("full_name")
            is_mirror = (
                str(head.get("ref", "")).startswith(MIRROR_BRANCH_PREFIX)
                and str(pull.get("title", "")).startswith(MIRROR_TITLE_PREFIX)
                and (pull.get("user") or {}).get("login") == MIRROR_AUTHOR
                and head_repo == repo_slug
                and base.get("ref") == PUBLISHED_BRANCH
            )
            if not is_mirror:
                continue
            number = int(pull["number"])
            if base.get("sha") != published_tip:
                reasons.append(
                    f"mirror PR #{number} is based on {str(base.get('sha'))[:12]}, not the published-results tip {published_tip[:12]}"
                )
                continue
            candidates.append(
                MirrorCandidate(number=number, head_sha=str(head.get("sha")), base_sha=str(base.get("sha")))
            )
        if len(pulls) < PULLS_PAGE_SIZE:
            return candidates, reasons
    raise LookupFailure(f"more than {MAX_PULLS_PAGES * PULLS_PAGE_SIZE} open pull requests target {PUBLISHED_BRANCH}")


def ensure_commit_available(repo: Path, sha: str) -> None:
    if object_exists(repo, f"{sha}^{{commit}}"):
        return
    run_git(repo, "fetch", "--no-tags", "--depth=1", "origin", sha)
    if not object_exists(repo, f"{sha}^{{commit}}"):
        raise LookupFailure(f"mirror head {sha[:12]} is not available after fetch")


def validate_status_state(get: GitHubGet, repo_slug: str, head_sha: str) -> str | None:
    combined = get(f"repos/{repo_slug}/commits/{head_sha}/status")
    for status in combined.get("statuses", []):
        if status.get("context") == VALIDATE_STATUS_CONTEXT:
            return str(status.get("state"))
    return None


def candidate_shortfall(
    repo: Path,
    get: GitHubGet,
    repo_slug: str,
    candidate: MirrorCandidate,
    stale: Sequence[str],
    develop_blobs: dict[str, str | None],
) -> str | None:
    ensure_commit_available(repo, candidate.head_sha)
    head_blobs = blob_ids(repo, candidate.head_sha, stale)
    mismatched = [path for path in stale if head_blobs[path] is None or head_blobs[path] != develop_blobs[path]]
    if mismatched:
        return f"mirror PR #{candidate.number} does not carry develop's content for {len(mismatched)} stale path(s), e.g. {mismatched[0]}"
    state = validate_status_state(get, repo_slug, candidate.head_sha)
    if state != "success":
        return f"mirror PR #{candidate.number} has {VALIDATE_STATUS_CONTEXT} status {state or 'missing'}, not success"
    return None


def evaluate_grace(
    repo: Path,
    get: GitHubGet,
    repo_slug: str,
    published_ref: str,
    develop_ref: str,
    pathspecs: Sequence[str],
    stale: Sequence[str],
    max_age_hours: float,
    now: float,
) -> GraceVerdict:
    verdict = GraceVerdict(covered=False)
    try:
        published_tip = run_git(repo, "rev-parse", "--verify", f"{published_ref}^{{commit}}").strip()
        candidates, verdict.reasons = open_mirror_candidates(get, repo_slug, published_tip)
        if not candidates and not verdict.reasons:
            verdict.reasons.append(f"no open mirror PR targets {PUBLISHED_BRANCH}")
        develop_blobs = blob_ids(repo, develop_ref, stale)
        covering: MirrorCandidate | None = None
        for candidate in candidates:
            shortfall = candidate_shortfall(repo, get, repo_slug, candidate, stale, develop_blobs)
            if shortfall is None:
                covering = candidate
                break
            verdict.reasons.append(shortfall)
        if covering is None:
            return verdict
        oldest = oldest_unpublished_change(repo, published_ref, develop_ref, pathspecs, stale)
    except (LookupFailure, KeyError, TypeError, ValueError) as exc:
        verdict.reasons.append(f"lookup failed, failing closed: {exc}")
        return verdict
    verdict.oldest_age_hours = (now - oldest) / 3600.0
    if verdict.oldest_age_hours >= max_age_hours:
        verdict.reasons.append(
            f"oldest unpublished develop change is {verdict.oldest_age_hours:.1f}h old, not under {max_age_hours:g}h"
        )
        return verdict
    verdict.covered = True
    verdict.pr_number = covering.number
    return verdict


def print_paths(paths: Sequence[str]) -> None:
    print("\n".join(paths[:MAX_LISTED_PATHS]))


def report_published_only(profile: str, published_only: Sequence[str]) -> None:
    if not published_only:
        return
    count = len(published_only)
    if profile == "nightly":
        print(
            f"::notice::published-results has {count} path(s) not on develop (accepted archive submissions; not drift)."
        )
        print_paths(published_only)
        return
    print(f"::notice::published-results has {count} path(s) not on develop (published-only).")
    print("These are expected accepted archive submissions that are not in")
    print("develop's curated seed. Do NOT run a full mirror")
    print("to clear this list — that would delete public content.")
    print(f"Published-only paths (first {MAX_LISTED_PATHS}):")
    print_paths(published_only)
    print("")


def report_in_sync(profile: str, published_only: Sequence[str]) -> None:
    if profile == "nightly":
        print("No develop-ahead corpus drift.")
    elif published_only:
        print("No develop-ahead drift. published-only paths were reported above (non-fatal).")
    else:
        print("Corpus is in sync between develop and published-results.")


def report_stale_failure(profile: str, stale: Sequence[str], published_only: Sequence[str]) -> None:
    print(
        f"::error::published-results corpus is {len(stale)} path(s) behind develop (develop-ahead or content-changed)."
    )
    if profile == "nightly":
        print_paths(stale)
        print("Mirror develop-ahead paths with the non-destructive union overlay:")
        print(f"  {SYNC_WORKFLOW_COMMAND}")
        print("Never use a wipe-based full mirror: published-only community submissions would be deleted.")
        return
    print("The public corpus is stale. If develop has since sanitized or")
    print("corrected bundles, the public branch is still serving the old")
    print("content.")
    print("")
    print(f"Develop-ahead / content-changed paths (first {MAX_LISTED_PATHS}):")
    print_paths(stale)
    print("")
    if published_only:
        print("Published-only paths also exist (see notice above).")
        print("Develop-ahead can be mirrored with the union-overlay sync that")
        print("overwrites develop's files under results-data/bundles without")
        print("deleting published-only community submissions.")
        print("Do NOT use a wipe-based full mirror (git rm -rf results-data/bundles)")
        print("while published-only paths exist — that would delete public content.")
        print("Run the non-destructive overlay mirror:")
    else:
        print("Mirror develop-ahead onto published-results (union overlay for")
        print("results-data/bundles; no wipe of published-only content):")
    print(f"  {SYNC_WORKFLOW_COMMAND}")


def check_corpus(
    repo: Path,
    profile: str,
    published_ref: str,
    develop_ref: str,
    repo_slug: str,
    max_age_hours: float,
    get: GitHubGet,
    now: float,
) -> int:
    pathspecs = PROFILES[profile]
    if not object_exists(repo, f"{published_ref}:results-data"):
        print(f"::error::{PUBLISHED_BRANCH} has no results-data/ tree")
        return 2

    classification = classify(repo, published_ref, develop_ref, pathspecs)
    report_published_only(profile, classification.published_only)
    stale = classification.stale
    if not stale:
        report_in_sync(profile, classification.published_only)
        return 0

    verdict = evaluate_grace(repo, get, repo_slug, published_ref, develop_ref, pathspecs, stale, max_age_hours, now)
    if verdict.covered:
        print(
            f"::notice::{len(stale)} stale path(s) are covered by validated mirror PR #{verdict.pr_number} "
            f"awaiting maintainer publication (oldest unpublished develop change {verdict.oldest_age_hours:.1f}h old, "
            f"limit {max_age_hours:g}h)."
        )
        print_paths(stale)
        return 0

    for reason in verdict.reasons:
        print(f"Mirror PR grace not applied: {reason}")
    report_stale_failure(profile, stale, classification.published_only)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--published-ref", default=f"origin/{PUBLISHED_BRANCH}")
    parser.add_argument("--develop-ref", default="HEAD")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.repository:
        print("::error::--repository or GITHUB_REPOSITORY is required to look up the mirror PR")
        return 2
    return check_corpus(
        args.repo_root,
        args.profile,
        args.published_ref,
        args.develop_ref,
        args.repository,
        args.max_age_hours,
        gh_api_get,
        time.time(),
    )


if __name__ == "__main__":
    sys.exit(main())
