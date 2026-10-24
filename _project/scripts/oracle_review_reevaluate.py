#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from oracle_gate import evaluate
from oracle_review_check import API_ROOT, ERROR, PAGE_SIZE, PASS, CheckError, _paginate, _request, fetch_pull

WORKFLOW_FILE = "oracle-review.yml"
RERUN_EVENTS = frozenset({"pull_request", "pull_request_review", "workflow_dispatch"})
UNSUCCESSFUL = frozenset({"failure", "cancelled", "timed_out"})
MAX_ATTEMPTS = 5


def unsuccessful_run_ids(runs: list[dict[str, Any]], repo: str, head_sha: str) -> list[int]:
    return sorted(
        run["id"]
        for run in runs
        if run.get("head_sha") == head_sha
        and run.get("event") in RERUN_EVENTS
        and run.get("status") == "completed"
        and run.get("conclusion") in UNSUCCESSFUL
        and ((run.get("head_repository") or {}).get("full_name") or "").casefold() == repo.casefold()
        and (run.get("run_attempt") or 1) < MAX_ATTEMPTS
    )


def fetch_runs(token: str, repo: str, head_sha: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _request(
            token,
            f"{API_ROOT}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
            f"?head_sha={head_sha}&per_page={PAGE_SIZE}&page={page}",
        )
        batch = payload["workflow_runs"]
        runs.extend(batch)
        if len(batch) < PAGE_SIZE:
            return runs
        page += 1


def rerun(token: str, repo: str, run_id: int) -> None:
    request = urllib.request.Request(
        f"{API_ROOT}/repos/{repo}/actions/runs/{run_id}/rerun",
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "benchbox-oracle-review-reevaluate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CheckError(f"GitHub request to re-run oracle-review run {run_id} failed: {exc}") from exc


def reevaluate_pull(token: str, repo: str, pr: int) -> list[int]:
    head_sha, status, message = evaluate(repo, pr, token)
    if status != PASS:
        print(f"#{pr}: not passing, nothing to re-run ({message})")
        return []
    run_ids = unsuccessful_run_ids(fetch_runs(token, repo, head_sha), repo, head_sha)
    if not run_ids:
        print(f"#{pr}: passing with no unsuccessful oracle-review run on {head_sha}")
        return []
    pull = fetch_pull(token, repo, pr)
    if pull["state"] != "open" or pull["head"]["sha"] != head_sha:
        print(f"#{pr}: head or state changed during evaluation; leaving it to the next pass")
        return []
    for run_id in run_ids:
        rerun(token, repo, run_id)
        print(f"#{pr}: re-ran oracle-review run {run_id} for {head_sha}")
    return run_ids


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the unsuccessful oracle-review runs of every open develop pull request whose current "
            "head the oracle-review decision now passes, so resolving oracle threads clears the check."
        )
    )
    parser.add_argument("--repo", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("oracle-review-reevaluate: error: GITHUB_TOKEN is not set", file=sys.stderr)
        return ERROR
    if args.repo.count("/") != 1:
        print("oracle-review-reevaluate: error: --repo must be OWNER/NAME", file=sys.stderr)
        return ERROR
    try:
        pulls = _paginate(token, f"/repos/{args.repo}/pulls")
    except CheckError as exc:
        print(f"oracle-review-reevaluate: error: {exc}", file=sys.stderr)
        return ERROR
    failed: list[int] = []
    for pull in pulls:
        if pull["base"]["ref"] != "develop":
            continue
        try:
            reevaluate_pull(token, args.repo, pull["number"])
        except (CheckError, KeyError, ValueError) as exc:
            print(f"oracle-review-reevaluate: error: #{pull['number']}: {exc}", file=sys.stderr)
            failed.append(pull["number"])
    if failed:
        print(f"oracle-review-reevaluate: failed for: {' '.join(f'#{n}' for n in failed)}", file=sys.stderr)
        return ERROR
    return PASS


if __name__ == "__main__":
    raise SystemExit(main())
