#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from oracle_review_check import (
    API_ROOT,
    ERROR,
    PASS,
    WAITING,
    CheckError,
    changed_paths,
    decide,
    fetch_base_change_date,
    fetch_comments,
    fetch_files,
    fetch_head_oid,
    fetch_pull,
    fetch_reviews,
    fetch_threads,
    path_matcher,
)

GATE_CONTEXT = "oracle-gate"
GATED_BASE = "develop"
DESCRIPTION_LIMIT = 140
PULL_READ_ATTEMPTS = 3
PULL_READ_BACKOFF_SECONDS = 2
HEAD_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
WAITING_EXIT = 3


class UngatedBase(Exception):
    pass


class PullReadFailed(Exception):
    pass


class EvaluationFailed(Exception):
    def __init__(self, head_sha: str, cause: Exception) -> None:
        super().__init__(str(cause) or type(cause).__name__)
        self.head_sha = head_sha


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


def load_policy_rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from oracle_reviewers.policy import load_policy

        classifier = load_policy(Path(".github/oracle-reviewers.yml")).classifier
        return classifier.exempt_paths, classifier.gate_paths
    except Exception as exc:
        raise CheckError(f"cannot load the review policy: {exc}") from exc


def read_pull(repo: str, pr: int, token: str) -> tuple[dict[str, Any], str, str]:
    last: Exception | None = None
    for attempt in range(PULL_READ_ATTEMPTS):
        if attempt:
            time.sleep(PULL_READ_BACKOFF_SECONDS * attempt)
        try:
            pull = fetch_pull(token, repo, pr)
            return pull, pull["base"]["ref"], pull["head"]["sha"]
        except Exception as exc:
            last = exc
    raise PullReadFailed(f"cannot read #{pr} after {PULL_READ_ATTEMPTS} attempts: {last}") from last


def evaluate(repo: str, pr: int, token: str) -> tuple[str, int, str]:
    pull, base, head_sha = read_pull(repo, pr, token)
    if base != GATED_BASE:
        raise UngatedBase(f"#{pr} targets {base}, not {GATED_BASE}; {GATE_CONTEXT} is posted only for {GATED_BASE}")
    try:
        items = fetch_files(token, repo, pr)
        files = changed_paths(items)
        matcher = path_matcher(len(items), pull["changed_files"], *load_policy_rules())
        reviews = fetch_reviews(token, repo, pr)
        threads = fetch_threads(token, repo, pr)
        base_date = fetch_base_change_date(token, repo, pr)
        comments = fetch_comments(token, repo, pr)
        status, message = decide(head_sha, files, reviews, threads, matcher, base_date, comments)
    except Exception as exc:
        raise EvaluationFailed(head_sha, exc) from exc
    return head_sha, status, message


def _head_sha(value: str) -> str:
    if not HEAD_SHA_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must be 40 lowercase hex characters")
    return value


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Post the oracle-gate commit status for a pull request head from the same "
            "decision the shared review checker reads."
        )
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--head-sha", type=_head_sha)
    return parser.parse_args(argv)


def _fail_closed(args: argparse.Namespace, token: str, exc: EvaluationFailed) -> int:
    print(f"oracle-gate: error: {exc}", file=sys.stderr)
    if args.no_post:
        return ERROR
    try:
        post_status(token, args.repo, exc.head_sha, ERROR, f"{GATE_CONTEXT}: evaluation error: {exc}")
    except CheckError as post_exc:
        print(f"oracle-gate: error: {post_exc}", file=sys.stderr)
    return ERROR


def _graphql_head(args: argparse.Namespace, token: str) -> str | None:
    try:
        head_sha = fetch_head_oid(token, args.repo, args.pr)
    except Exception as exc:
        print(f"oracle-gate: error: cannot read the head of #{args.pr} through GraphQL: {exc}", file=sys.stderr)
        return None
    if not isinstance(head_sha, str) or not HEAD_SHA_PATTERN.fullmatch(head_sha):
        print(f"oracle-gate: error: GraphQL returned no usable head for #{args.pr}", file=sys.stderr)
        return None
    return head_sha


def _read_failed(args: argparse.Namespace, token: str, exc: PullReadFailed, known_head: str | None) -> int:
    head_sha = known_head or args.head_sha
    if head_sha is None and not args.no_post:
        head_sha = _graphql_head(args, token)
    if head_sha is None:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    return _fail_closed(args, token, EvaluationFailed(head_sha, exc))


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
    except UngatedBase as exc:
        print(f"oracle-gate: {exc}")
        return PASS
    except PullReadFailed as exc:
        return _read_failed(args, token, exc, None)
    except EvaluationFailed as exc:
        return _fail_closed(args, token, exc)
    except (CheckError, KeyError, ValueError) as exc:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    print(message)
    if args.no_post:
        return PASS if status == PASS else WAITING
    try:
        latest_sha, latest_status, latest_message = evaluate(args.repo, args.pr, token)
    except UngatedBase as exc:
        print(f"oracle-gate: {exc}")
        return PASS
    except PullReadFailed as exc:
        return _read_failed(args, token, exc, head_sha)
    except EvaluationFailed as exc:
        return _fail_closed(args, token, exc)
    except (CheckError, KeyError, ValueError) as exc:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    if (latest_sha, latest_status, latest_message) != (head_sha, status, message):
        print(latest_message)
        head_sha, status, message = latest_sha, latest_status, latest_message
    try:
        post_status(token, args.repo, head_sha, status, message)
    except CheckError as exc:
        print(f"oracle-gate: error: {exc}", file=sys.stderr)
        return ERROR
    return PASS if status == PASS else WAITING


def process_exit(status: int) -> int:
    return WAITING_EXIT if status == WAITING else status


if __name__ == "__main__":
    raise SystemExit(process_exit(main()))
