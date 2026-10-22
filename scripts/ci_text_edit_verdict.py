#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

CLI_DESCRIPTION = (
    "Report a required CI check on a pull request title or body edit by copying the\n"
    "verdict of the newest full CI run on the same head commit. Waits while a full run\n"
    "is still in progress and fails when no full run reports the check."
)

TEXT_EDIT_RUN_NAME = "CI (text edit)"
WORKFLOW_FILE = "ci.yml"
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


def _fetch(repo: str, sha: str, unit: str, token: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    api = f"https://api.github.com/repos/{repo}"
    runs_query = urllib.parse.urlencode({"head_sha": sha, "event": "pull_request", "per_page": 100})
    runs = _get(f"{api}/actions/workflows/{WORKFLOW_FILE}/runs?{runs_query}", token)["workflow_runs"]
    checks_query = urllib.parse.urlencode({"check_name": unit, "filter": "all", "per_page": 100})
    checks = _get(f"{api}/commits/{sha}/check-runs?{checks_query}", token)["check_runs"]
    return runs, checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=110)
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args(argv)
    token = os.environ["GH_TOKEN"]
    outcome, reason = WAIT, "not checked"
    for attempt in range(args.attempts):
        try:
            runs, checks = _fetch(args.repo, args.sha, args.unit, token)
        except (urllib.error.URLError, TimeoutError) as error:
            outcome, reason = WAIT, f"GitHub API request failed: {error}"
        else:
            outcome, reason = decide(args.unit, args.run_id, runs, checks)
        print(f"{args.unit}: {reason}", flush=True)
        if outcome != WAIT:
            break
        if attempt + 1 < args.attempts:
            time.sleep(args.interval)
    if outcome == WAIT:
        print(f"{args.unit}: gave up waiting: {reason}", file=sys.stderr)
    return 0 if outcome == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
