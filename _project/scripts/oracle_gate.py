#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from oracle_review_check import (
    API_ROOT,
    ERROR,
    PASS,
    WAITING,
    CheckError,
    _review_rules,
    changed_paths,
    decide,
    fetch_base_change_date,
    fetch_comments,
    fetch_files,
    fetch_pull,
    fetch_reviews,
    fetch_threads,
    path_matcher,
)

GATE_CONTEXT = "oracle-gate"
DESCRIPTION_LIMIT = 140


def gate_state(status: int) -> str:
    return "success" if status == PASS else "failure"


def gate_description(message: str) -> str:
    return message if len(message) <= DESCRIPTION_LIMIT else message[: DESCRIPTION_LIMIT - 1] + "…"


def status_payload(status: int, message: str, target_url: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": gate_state(status),
        "context": GATE_CONTEXT,
        "description": gate_description(message),
    }
    if target_url is not None:
        payload["target_url"] = target_url
    return payload


def target_url(repo: str) -> str | None:
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{repo}/actions/runs/{run_id}"


def post_status(token: str, repo: str, head_sha: str, status: int, message: str) -> None:
    body = json.dumps(status_payload(status, message, target_url(repo))).encode()
    request = urllib.request.Request(
        f"{API_ROOT}/repos/{repo}/statuses/{head_sha}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "benchbox-oracle-gate",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CheckError(f"GitHub request to post {GATE_CONTEXT} for {head_sha} failed: {exc}") from exc


def evaluate(repo: str, pr: int, token: str) -> tuple[str, int, str]:
    pull = fetch_pull(token, repo, pr)
    head_sha = pull["head"]["sha"]
    items = fetch_files(token, repo, pr)
    files = changed_paths(items)
    matcher = path_matcher(len(items), pull["changed_files"], *_review_rules())
    reviews = fetch_reviews(token, repo, pr)
    threads = fetch_threads(token, repo, pr)
    base_date = fetch_base_change_date(token, repo, pr)
    comments = fetch_comments(token, repo, pr)
    status, message = decide(head_sha, files, reviews, threads, matcher, base_date, comments)
    return head_sha, status, message


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Post the oracle-gate commit status for a pull request head from the same "
            "decision the oracle-review check reads."
        )
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--no-post", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("oracle-gate: error: GITHUB_TOKEN is not set", file=sys.stderr)
        return ERROR
    if args.repo.count("/") != 1:
        print("oracle-gate: error: --repo must be OWNER/NAME", file=sys.stderr)
        return ERROR
    try:
        head_sha, status, message = evaluate(args.repo, args.pr, token)
    except (CheckError, KeyError, ValueError) as exc:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    print(message)
    if args.no_post:
        return PASS if status == PASS else WAITING
    try:
        post_status(token, args.repo, head_sha, status, message)
    except CheckError as exc:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    return PASS if status == PASS else WAITING


if __name__ == "__main__":
    raise SystemExit(main())
