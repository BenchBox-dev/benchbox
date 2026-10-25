#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import coverage_test_selection as sel

STALENESS_BEHIND_LIMIT = sel.MAX_MAP_BEHIND_COMMITS
PRODUCER_JOB = "medium-coverage-map"
RUN_SCAN_LIMIT = 30
STALENESS_FAILED_RUNS = 3
STALENESS_LABEL = "ci-coverage-map"
STALENESS_MARKER = "<!-- coverage-map-staleness -->"
REOPEN_DAYS = 14


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True, timeout=120)
    return proc.stdout


def _gh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["gh", *args], cwd=repo, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise sel.SelectionError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _gh_json(repo: Path, *args: str) -> object:
    return json.loads(_gh(repo, *args))


def _producer_job_conclusion(repo: Path, slug: str, run_id: str) -> str:
    return _gh(
        repo,
        "api",
        f"repos/{slug}/actions/runs/{run_id}/jobs",
        "--method",
        "GET",
        "--jq",
        f'[.jobs[] | select(.name == "{PRODUCER_JOB}") | .conclusion // ""] | first // ""',
    ).strip()


def recent_conclusions(repo: Path, slug: str, limit: int = 5) -> list[str]:
    rows = _gh(
        repo,
        "api",
        f"repos/{slug}/actions/workflows/trunk-coverage.yml/runs",
        "--method",
        "GET",
        "-f",
        "branch=develop",
        "-f",
        f"per_page={RUN_SCAN_LIMIT}",
        "--jq",
        '.workflow_runs[] | select(.status == "completed") | [.id, .event] | @tsv',
    ).splitlines()
    conclusions: list[str] = []
    for row in rows:
        run_id, event = (row.split("\t") + [""])[:2]
        if not run_id.isdigit() or event not in sel.TRUSTED_MAP_EVENTS:
            continue
        conclusion = _producer_job_conclusion(repo, slug, run_id)
        if conclusion not in ("success", "failure"):
            continue
        conclusions.append(conclusion)
        if len(conclusions) >= limit:
            break
    return conclusions


def decide(map_status: str, behind: int | None, conclusions: list[str]) -> tuple[str, str]:
    completed = [conclusion for conclusion in conclusions if conclusion in ("success", "failure")]
    recent = completed[:STALENESS_FAILED_RUNS]
    if map_status == "lookup-failed":
        return "unknown", "coverage map lookup failed; cannot tell whether the map is stale"
    if map_status == "missing":
        return "stale", "no coverage map published"
    if behind is None:
        return "unknown", "coverage map position against develop is unknown"
    if behind > STALENESS_BEHIND_LIMIT:
        return "stale", f"newest coverage map is {behind} develop commits behind (limit {STALENESS_BEHIND_LIMIT})"
    if not completed:
        return "unknown", "no completed map-producing trunk-coverage run was found"
    if len(recent) >= STALENESS_FAILED_RUNS and all(conclusion == "failure" for conclusion in recent):
        return "stale", f"last {STALENESS_FAILED_RUNS} map-producing trunk-coverage runs failed"
    return "healthy", "newest coverage map is current and recent map-producing trunk-coverage runs pass"


def render_body(reason: str, sha: str | None, behind: int | None, conclusions: list[str], run_url: str) -> str:
    lines = [
        "The coverage map is stale.",
        "",
        f"- Reason: {reason}",
        f"- Newest map: {sha or 'none'}",
        f"- Develop commits behind: {'unknown' if behind is None else behind}",
        f"- Recent trunk-coverage conclusions: {', '.join(conclusions) or 'none'}",
        f"- Check: {run_url}",
        "",
        "This issue stays open while the map is stale and closes once a scheduled check finds it healthy.",
        STALENESS_MARKER,
        f"reason:{reason}",
    ]
    return "\n".join(lines) + "\n"


def recorded_reason(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("reason:"):
            return line[len("reason:") :]
    return None


def open_issues(repo: Path, slug: str, label: str) -> list[dict]:
    payload = _gh_json(
        repo,
        "issue",
        "list",
        "--repo",
        slug,
        "--state",
        "open",
        "--label",
        label,
        "--limit",
        "100",
        "--json",
        "number,body",
    )
    return payload if isinstance(payload, list) else []


def closed_issues(repo: Path, slug: str, label: str) -> list[dict]:
    payload = _gh_json(
        repo,
        "issue",
        "list",
        "--repo",
        slug,
        "--state",
        "closed",
        "--label",
        label,
        "--limit",
        "20",
        "--json",
        "number,body,closedAt",
    )
    return payload if isinstance(payload, list) else []


def ensure_issue(
    repo: Path,
    slug: str,
    label: str,
    state: str,
    reason: str,
    sha: str | None,
    behind: int | None,
    conclusions: list[str],
    run_url: str,
) -> dict:
    opened = open_issues(repo, slug, label)
    if state == "healthy":
        if opened:
            number = opened[0]["number"]
            _gh(
                repo,
                "issue",
                "comment",
                str(number),
                "--repo",
                slug,
                "--body",
                f"Coverage map is healthy: {reason}. {run_url}",
            )
            _gh(repo, "issue", "close", str(number), "--repo", slug, "--reason", "completed")
            return {"action": "closed", "issue": number}
        return {"action": "none", "issue": None}
    if state == "unknown":
        if opened:
            number = opened[0]["number"]
            _gh(
                repo,
                "issue",
                "comment",
                str(number),
                "--repo",
                slug,
                "--body",
                f"Coverage map check has no verdict: {reason}. {run_url}",
            )
            return {"action": "commented", "issue": number}
        return {"action": "none", "issue": None}
    body = render_body(reason, sha, behind, conclusions, run_url)
    for issue in opened:
        if recorded_reason(issue.get("body") or "") == reason:
            return {"action": "none", "issue": issue["number"]}
        number = issue["number"]
        _gh(
            repo,
            "issue",
            "comment",
            str(number),
            "--repo",
            slug,
            "--body",
            f"Coverage map is still stale: {reason}. {run_url}",
        )
        return {"action": "commented", "issue": number}
    try:
        _gh(
            repo,
            "label",
            "create",
            label,
            "--repo",
            slug,
            "--description",
            "Coverage map staleness",
            "--color",
            "B60205",
        )
    except sel.SelectionError:
        pass
    created = _gh_json(
        repo,
        "issue",
        "create",
        "--repo",
        slug,
        "--label",
        label,
        "--title",
        "Coverage map is stale",
        "--body",
        body,
        "--json",
        "number",
    )
    number = created.get("number") if isinstance(created, dict) else None
    return {"action": "created", "issue": number}


def resolve_slug(repo: Path, slug: str | None) -> str:
    if slug:
        return slug
    env_slug = os.environ.get("GITHUB_REPOSITORY", "")
    if env_slug:
        return env_slug
    return sel._github_slug(repo)


def run_url(slug: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id:
        return f"{server}/{slug}/actions/runs/{run_id}"
    return f"{server}/{slug}/actions/workflows/trunk-coverage.yml"


def evaluate(repo: Path, slug: str, base_sha: str) -> tuple[str, str, str | None, int | None, list[str]]:
    fetched = sel.fetch_map(repo, base_sha, repo / ".coverage-map-staleness", slug=slug)
    behind: int | None = None
    if fetched["status"] == "found" and fetched["sha"] is not None:
        try:
            behind = sel.behind_commits(repo, fetched["sha"], base_sha)
        except sel.SelectionError:
            behind = None
    conclusions = recent_conclusions(repo, slug)
    state, reason = decide(fetched["status"], behind, conclusions)
    return state, reason, fetched["sha"], behind, conclusions


def main(argv: list[str] | None = None) -> int:
    global STALENESS_BEHIND_LIMIT, STALENESS_FAILED_RUNS
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--slug", default=None)
    parser.add_argument("--label", default=STALENESS_LABEL)
    parser.add_argument("--behind-limit", type=int, default=STALENESS_BEHIND_LIMIT)
    parser.add_argument("--failed-runs", type=int, default=STALENESS_FAILED_RUNS)
    parser.add_argument("--base-sha", default=None)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    STALENESS_BEHIND_LIMIT = args.behind_limit
    STALENESS_FAILED_RUNS = args.failed_runs
    repo = args.repo_root
    try:
        slug = resolve_slug(repo, args.slug)
        base_sha = args.base_sha or _git(repo, "rev-parse", "--verify", "HEAD").strip()
        state, reason, sha, behind, conclusions = evaluate(repo, slug, base_sha)
    except sel.SelectionError as exc:
        print(f"coverage map staleness check failed: {exc}", file=sys.stderr)
        return 1
    print(f"coverage map staleness: {state}: {reason}")
    if args.check_only:
        return 0
    try:
        outcome = ensure_issue(repo, slug, args.label, state, reason, sha, behind, conclusions, run_url(slug))
    except sel.SelectionError as exc:
        print(f"coverage map staleness update failed: {exc}", file=sys.stderr)
        return 1
    print(f"issue {outcome['action']}: {outcome['issue']}")
    return 1 if state == "unknown" else 0


if __name__ == "__main__":
    raise SystemExit(main())
