#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from inert_paths import is_inert_path
from soundness_paths import is_soundness_path

ORACLE_LOGIN = "benchbox-oracle"
ORACLE = "oracle"
ORACLE_CONTEXT = "oracle-verdict"
LEGACY_ORACLE_CONTEXT = "oracle-review-shadow"
_ORACLE_VERDICT = re.compile(
    rf"### (?:{ORACLE_CONTEXT}|{LEGACY_ORACLE_CONTEXT}): (?P<state>[a-z]+) for `(?P<sha>[0-9a-f]{{40}})`"
)
STANDIN_ATTESTERS = frozenset({"joeharris76"})
_REFUSED_LINE = "Decision: **REFUSED**."
_STANDIN_MARKER = re.compile(r"Stand-in oracle review: APPROVE ([0-9a-f]{40})")


def _attested_shas(comment: dict[str, Any]) -> list[str]:
    match = _STANDIN_MARKER.fullmatch((comment.get("body") or "").strip())
    return [match.group(1)] if match else []


def _is_standin(comment: dict[str, Any], head_sha: str, not_before: datetime | None) -> bool:
    return (
        comment.get("login") in STANDIN_ATTESTERS
        and comment.get("user_type") == "User"
        and comment.get("updated_at") in (None, comment.get("created_at"))
        and (not_before is None or _parse_time(comment["created_at"]) > not_before)
        and head_sha in _attested_shas(comment)
    )


API_ROOT = "https://api.github.com"
PAGE_SIZE = 100
PASS = 0
WAITING = 1
ERROR = 2

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first: 1) { nodes { author { __typename login } } }
        }
      }
    }
  }
}
"""


class CheckError(RuntimeError):
    pass


def _login(value: str | None) -> str:
    return (value or "").removesuffix("[bot]")


def _is_oracle(login: str | None, account_type: str | None) -> bool:
    return _login(login) == ORACLE_LOGIN and account_type == "Bot"


DECISIVE_VERDICTS = frozenset({"success", "failure"})
Moment = TypeVar("Moment", str, datetime)


def verdict_state(body: str | None, sha: str, pattern: re.Pattern[str] = _ORACLE_VERDICT) -> str | None:
    match = pattern.match(body or "")
    return match.group("state") if match and match.group("sha") == sha else None


def standin_not_before(base_change: Moment | None, verdicts: Iterable[tuple[Moment, str | None]]) -> Moment | None:
    decisive = (at for at, state in verdicts if state in DECISIVE_VERDICTS)
    return max(filter(None, (base_change, *decisive)), default=None)


def _review_verdict(review: dict[str, Any], head_sha: str) -> str | None:
    return verdict_state(review.get("body"), head_sha)


def _oracle_verdict(reviews: list[dict[str, Any]], head_sha: str) -> str | None:
    ordered = sorted(reviews, key=lambda review: _parse_time(review["submitted_at"]))
    return _review_verdict(ordered[-1], head_sha) if ordered else None


def _refused(reviews: list[dict[str, Any]]) -> bool:
    ordered = sorted(reviews, key=lambda review: _parse_time(review["submitted_at"]))
    return bool(ordered) and _REFUSED_LINE in (ordered[-1].get("body") or "").splitlines()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def decide(
    head_sha: str,
    files: Iterable[str],
    reviews: Iterable[dict[str, Any]],
    threads: Iterable[dict[str, Any]],
    paths: Callable[[Iterable[str]], bool],
    base_date: str | None = None,
    comments: Iterable[dict[str, Any]] = (),
) -> tuple[int, str]:
    if not paths(files):
        return PASS, "oracle-review: only exempt paths changed"

    base_time = _parse_time(base_date) if base_date else None
    head_reviews = [
        review
        for review in reviews
        if _is_oracle(review.get("login"), review.get("user_type"))
        and review.get("commit_id") == head_sha
        and review.get("state") not in {"PENDING", "DISMISSED"}
        and (base_time is None or (review.get("submitted_at") and _parse_time(review["submitted_at"]) > base_time))
    ]
    verdict = _oracle_verdict(head_reviews, head_sha)
    standin_after = standin_not_before(
        base_time,
        ((_parse_time(review["submitted_at"]), _review_verdict(review, head_sha)) for review in head_reviews),
    )
    standin = next((comment for comment in comments if _is_standin(comment, head_sha, standin_after)), None)
    if head_reviews and verdict != "success" and not standin:
        if _refused(head_reviews):
            return WAITING, (
                f"oracle-review: the oracle refused to review {head_sha} after repeated DO NOT SHIP decisions; "
                "close this pull request and open a new one, or post a stand-in attestation after that review, "
                "then rerun this check"
            )
        return WAITING, (
            f"oracle-review: the oracle's latest review of {head_sha} reports {verdict or 'no verdict'}, not "
            "success; fix the findings, or post a stand-in attestation after that review, then rerun this check"
        )
    if not (head_reviews or standin):
        return WAITING, (
            f"oracle-review: waiting for the oracle's review of {head_sha}, or a stand-in attestation "
            f"'Stand-in oracle review: APPROVE {head_sha}' from a listed attester; rerun this check after it lands"
        )

    open_threads = sum(
        1
        for thread in threads
        if not thread.get("resolved") and _is_oracle(thread.get("author"), thread.get("author_type"))
    )
    if open_threads:
        return WAITING, (
            f"oracle-review: {open_threads} unresolved oracle review thread(s) on {head_sha}; "
            "resolve them and rerun this check"
        )

    if head_reviews and verdict == "success":
        return PASS, f"oracle-review: pass (oracle review of {head_sha})"
    assert standin is not None
    return PASS, f"oracle-review: pass (stand-in review attested by {standin.get('login')} for {head_sha})"


def _request(token: str, url: str, body: dict[str, Any] | None = None) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "benchbox-oracle-review-check",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise CheckError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CheckError(f"GitHub request to {url} failed: {exc}") from exc


def _paginate(token: str, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = _request(token, f"{API_ROOT}{path}?per_page={PAGE_SIZE}&page={page}")
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


def fetch_pull(token: str, repo: str, pr: int) -> dict[str, Any]:
    return _request(token, f"{API_ROOT}/repos/{repo}/pulls/{pr}")


def changed_paths(items: Iterable[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in items:
        paths.append(item["filename"])
        if item.get("previous_filename"):
            paths.append(item["previous_filename"])
    return paths


def fetch_files(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return _paginate(token, f"/repos/{repo}/pulls/{pr}/files")


def _review_rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from oracle_reviewers.policy import load_policy

        classifier = load_policy(Path(".github/oracle-reviewers.yml")).classifier
        return classifier.exempt_paths, classifier.gate_paths
    except Exception:
        return (), ()


def _path_needs_review(path: str, exempt: tuple[str, ...], gate: tuple[str, ...]) -> bool:
    if is_soundness_path(path):
        return True
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in gate):
        return True
    if is_inert_path(path):
        return False
    return not any(fnmatch.fnmatchcase(path, pattern) for pattern in exempt)


def path_matcher(
    listed_files: int,
    changed_files: int,
    exempt_paths: Iterable[str] = (),
    gate_paths: Iterable[str] = (),
) -> Callable[[Iterable[str]], bool]:
    if listed_files < changed_files:
        return lambda _paths: True
    exempt = tuple(exempt_paths)
    gate = tuple(gate_paths)

    def needs_review(paths: Iterable[str]) -> bool:
        return any(_path_needs_review(path, exempt, gate) for path in paths)

    return needs_review


def fetch_reviews(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return [
        {
            "login": (item.get("user") or {}).get("login"),
            "user_type": (item.get("user") or {}).get("type"),
            "commit_id": item.get("commit_id"),
            "state": item.get("state"),
            "submitted_at": item.get("submitted_at"),
            "body": item.get("body"),
        }
        for item in _paginate(token, f"/repos/{repo}/pulls/{pr}/reviews")
    ]


def fetch_comments(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return [
        {
            "login": (item.get("user") or {}).get("login"),
            "user_type": (item.get("user") or {}).get("type"),
            "body": item.get("body"),
            "created_at": item["created_at"],
            "updated_at": item.get("updated_at"),
        }
        for item in _paginate(token, f"/repos/{repo}/issues/{pr}/comments")
    ]


def latest_base_change(events: Iterable[dict[str, Any]]) -> str | None:
    dates = [event["created_at"] for event in events if event.get("event") == "base_ref_changed"]
    return max(dates, key=_parse_time) if dates else None


def fetch_base_change_date(token: str, repo: str, pr: int) -> str | None:
    return latest_base_change(_paginate(token, f"/repos/{repo}/issues/{pr}/timeline"))


def fetch_threads(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    threads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        payload = _request(
            token,
            f"{API_ROOT}/graphql",
            {"query": _THREADS_QUERY, "variables": {"owner": owner, "name": name, "number": pr, "after": after}},
        )
        if payload.get("errors"):
            raise CheckError(f"GitHub GraphQL returned errors: {payload['errors']}")
        connection = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        for node in connection["nodes"]:
            comments = node["comments"]["nodes"]
            author = (comments[0].get("author") or {}) if comments else {}
            threads.append(
                {"resolved": node["isResolved"], "author": author.get("login"), "author_type": author.get("__typename")}
            )
        if not connection["pageInfo"]["hasNextPage"]:
            return threads
        after = connection["pageInfo"]["endCursor"]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require the oracle's review of the head commit on result-affecting pull requests, or a stand-in "
            "attestation for the exact head commit from a listed attester."
        )
    )
    parser.add_argument("--repo", required=True, help="Repository as OWNER/NAME.")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number.")
    parser.add_argument("--signal", choices=[ORACLE], default=ORACLE, help="Reviewer whose review this check requires.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("oracle-review: error: GITHUB_TOKEN is not set", file=sys.stderr)
        return ERROR
    if args.repo.count("/") != 1:
        print("oracle-review: error: --repo must be OWNER/NAME", file=sys.stderr)
        return ERROR
    try:
        pull = fetch_pull(token, args.repo, args.pr)
        head_sha = pull["head"]["sha"]
        items = fetch_files(token, args.repo, args.pr)
        files = changed_paths(items)
        matcher = path_matcher(len(items), pull["changed_files"], *_review_rules())
        if not matcher(files):
            print("oracle-review: only exempt paths changed")
            return PASS
        reviews = fetch_reviews(token, args.repo, args.pr)
        threads = fetch_threads(token, args.repo, args.pr)
        base_date = fetch_base_change_date(token, args.repo, args.pr)
        comments = fetch_comments(token, args.repo, args.pr)

        status, message = decide(head_sha, files, reviews, threads, matcher, base_date, comments)
    except (CheckError, KeyError, ValueError) as exc:
        print(f"oracle-review: error: {exc}", file=sys.stderr)
        return ERROR
    print(message)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
