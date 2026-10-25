#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

CLI_DESCRIPTION = (
    "Report a required CI check on a pull request title or body edit by copying the\n"
    "verdict of the newest full CI run on the same head commit. Fails without polling\n"
    "while a full run is missing or in progress; the refresh command re-runs the failed\n"
    "check when that full run completes."
)

TEXT_EDIT_RUN_NAME = "CI (text edit)"
WORKFLOW_FILE = "ci.yml"
API_TRIES = 3
API_RETRY_SECONDS = 10.0
UNFINISHED_POLLS = 20
UNFINISHED_POLL_SECONDS = 15.0
RUN_ID_IN_URL = re.compile(r"/actions/runs/(\d+)/")

PASS = "pass"
FAIL = "fail"
WAIT = "wait"


def _run_id(check_run: dict[str, Any]) -> int | None:
    match = RUN_ID_IN_URL.search(str(check_run.get("html_url") or check_run.get("details_url") or ""))
    return int(match.group(1)) if match else None


def decide(
    unit: str, current_run_id: int, runs: list[dict[str, Any]], check_runs: list[dict[str, Any]]
) -> tuple[str, str]:
    full_runs = [run for run in runs if run["id"] != current_run_id and run.get("display_title") != TEXT_EDIT_RUN_NAME]
    if not full_runs:
        return WAIT, "no full CI run on this commit yet"
    unfinished = [run["id"] for run in full_runs if run.get("status") != "completed"]
    if unfinished:
        return WAIT, f"full CI run {unfinished[0]} is still in progress"
    newest = max(full_runs, key=lambda run: run["id"])
    attempts = [
        check
        for check in check_runs
        if check.get("name") == unit and _run_id(check) == newest["id"] and check.get("status") == "completed"
    ]
    if not attempts:
        return FAIL, f"full CI run {newest['id']} reported no completed {unit!r} check"
    latest = max(attempts, key=lambda check: (str(check.get("started_at") or ""), int(check.get("id") or 0)))
    conclusion = latest.get("conclusion")
    if conclusion == "success":
        return PASS, f"full CI run {newest['id']} reported {unit!r} as success"
    return FAIL, f"full CI run {newest['id']} reported {unit!r} as {conclusion}"


def _get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _fetch_runs(repo: str, sha: str, token: str) -> list[dict[str, Any]]:
    api = f"https://api.github.com/repos/{repo}"
    runs_query = urllib.parse.urlencode({"head_sha": sha, "event": "pull_request", "per_page": 100})
    return _get(f"{api}/actions/workflows/{WORKFLOW_FILE}/runs?{runs_query}", token)["workflow_runs"]


def _fetch(repo: str, sha: str, unit: str, token: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    api = f"https://api.github.com/repos/{repo}"
    checks_query = urllib.parse.urlencode({"check_name": unit, "filter": "all", "per_page": 100})
    checks = _get(f"{api}/commits/{sha}/check-runs?{checks_query}", token)["check_runs"]
    return _fetch_runs(repo, sha, token), checks


def _is_transient(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return True


def _with_retries(action: Callable[[], T]) -> T:
    for _ in range(API_TRIES - 1):
        try:
            return action()
        except (urllib.error.URLError, TimeoutError) as error:
            if not _is_transient(error):
                raise
            print(f"GitHub API request failed ({error}); retrying", file=sys.stderr, flush=True)
            time.sleep(API_RETRY_SECONDS)
    return action()


def newest_failed_text_edit_run(runs: list[dict[str, Any]]) -> int | None:
    failed = [
        run["id"]
        for run in runs
        if run.get("display_title") == TEXT_EDIT_RUN_NAME
        and run.get("status") == "completed"
        and run.get("conclusion") == "failure"
    ]
    return max(failed, default=None)


def text_edit_run_unfinished(runs: list[dict[str, Any]]) -> bool:
    return any(run.get("display_title") == TEXT_EDIT_RUN_NAME and run.get("status") != "completed" for run in runs)


def _verdict(args: argparse.Namespace) -> int:
    token = os.environ["GH_TOKEN"]
    try:
        runs, checks = _with_retries(lambda: _fetch(args.repo, args.sha, args.unit, token))
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"{args.unit}: GitHub API request failed: {error}", file=sys.stderr)
        return 1
    outcome, reason = decide(args.unit, args.run_id, runs, checks)
    print(f"{args.unit}: {reason}", flush=True)
    if outcome == WAIT:
        print(
            f"{args.unit}: this check is re-run automatically when the full CI run on this commit completes.",
            file=sys.stderr,
        )
    return 0 if outcome == PASS else 1


def _refresh(args: argparse.Namespace) -> int:
    token = os.environ["GH_TOKEN"]
    try:
        runs = _with_retries(lambda: _fetch_runs(args.repo, args.sha, token))
        for _ in range(UNFINISHED_POLLS):
            if not text_edit_run_unfinished(runs):
                break
            print("a text-edit run on this commit is still finishing; waiting for it to complete", flush=True)
            time.sleep(UNFINISHED_POLL_SECONDS)
            runs = _with_retries(lambda: _fetch_runs(args.repo, args.sha, token))
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"GitHub API request failed: {error}", file=sys.stderr)
        return 1
    run_id = newest_failed_text_edit_run(runs)
    if run_id is None:
        print(f"no failed {TEXT_EDIT_RUN_NAME!r} run on {args.sha}")
        return 0
    print(f"re-running failed jobs of {TEXT_EDIT_RUN_NAME!r} run {run_id}", flush=True)
    return subprocess.run(["gh", "run", "rerun", str(run_id), "--failed", "--repo", args.repo], check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    verdict = commands.add_parser("verdict", help="decide one required check once")
    verdict.add_argument("--repo", required=True)
    verdict.add_argument("--sha", required=True)
    verdict.add_argument("--unit", required=True)
    verdict.add_argument("--run-id", type=int, required=True)
    verdict.set_defaults(handler=_verdict)
    refresh = commands.add_parser("refresh", help="re-run the newest failed text-edit run on a commit")
    refresh.add_argument("--repo", required=True)
    refresh.add_argument("--sha", required=True)
    refresh.set_defaults(handler=_refresh)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
