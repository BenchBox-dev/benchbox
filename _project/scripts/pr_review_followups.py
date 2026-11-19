#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_REVIEW_AUTHORS = ("benchbox-review[bot]",)
ACTION_MARKER = "benchbox-pr-review-followup-actioned"
ACTION_MARKER_REGEX = re.compile(rf"(?m)^<!--\s*{re.escape(ACTION_MARKER)}\b")
CODEX_USAGE_LIMIT_REVIEW_TEXT = "You have reached your Codex usage limits for code reviews"
CODEX_REVIEW_TRIGGER_BODY = "@codex review"
CODEX_REVIEW_RESULT_MARKERS = ("### 💡 Codex Review", "Codex Review:")
DEFAULT_COMMIT_MESSAGE = "fix: address stale PR review follow-ups"
PER_COMMENT_COMMIT_PREFIX = "fix(pr-followup)"
MAX_REPLY_SUMMARY_CHARS = 8000
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "pr_review_followup.md"
MIN_EXECUTOR_VERSION = (0, 20, 0)
PER_COMMENT_COMMIT_SUBJECT_REGEX = re.compile(rf"^{re.escape(PER_COMMENT_COMMIT_PREFIX)}: PR #(\d+) comment (\d+) ")
GH_API_RETRY_ATTEMPTS = 3
GH_API_RETRY_BACKOFFS: tuple[int, ...] = (1, 4, 9)
HOOK_AUTOFIX_STDERR_MARKER = "files were modified by this hook"


CLI_DESCRIPTION = (
    "Action stale bot/agent review comments left on merged PRs.\n"
    "\n"
    "The script is intentionally an orchestrator, not a static fixer. It gathers\n"
    "candidate inline review comments from configured authors, skips comments that\n"
    "already carry the BenchBox action marker reply, asks the local executor (the\n"
    "`codex` CLI) to assess and fix each remaining finding against the current\n"
    "tree, replies to the source comment, and then optionally submits one batched\n"
    "PR through the existing Make workflow. The reviewer set is configurable via\n"
    "`--author`; the executor is currently codex but is isolated behind the\n"
    "`--executor-*` flags so it can be swapped without touching the orchestration.\n"
)


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    merged_at: str
    url: str
    head_ref_name: str | None = None


@dataclass(frozen=True)
class ReviewComment:
    id: int
    body: str
    path: str
    html_url: str
    user_login: str
    created_at: str
    diff_hunk: str = ""
    in_reply_to_id: int | None = None
    original_commit_id: str | None = None
    commit_id: str | None = None
    line: int | None = None
    original_line: int | None = None
    thread_id: str | None = None
    thread_is_resolved: bool | None = None
    thread_is_outdated: bool | None = None


@dataclass(frozen=True)
class IssueComment:
    id: int
    body: str
    html_url: str
    user_login: str
    created_at: str


@dataclass(frozen=True)
class PullRequestReview:
    id: int
    body: str
    user_login: str
    submitted_at: str
    commit_id: str | None = None


@dataclass(frozen=True)
class PendingComment:
    pr: PullRequest
    comment: ReviewComment
    replies: tuple[ReviewComment, ...]


@dataclass(frozen=True)
class ReviewInventory:
    actionable: tuple[PendingComment, ...]
    resolved_for_audit: tuple[PendingComment, ...]


@dataclass(frozen=True)
class UsageLimitReviewRetry:
    pr: PullRequest
    usage_comment: IssueComment
    trigger_comment: IssueComment | None = None

    @property
    def needs_trigger(self) -> bool:
        return self.trigger_comment is None


@dataclass(frozen=True)
class ActionResult:
    pending: PendingComment
    disposition: str
    summary: str


class CommandError(RuntimeError):
    def __init__(self, args: Sequence[str], result: subprocess.CompletedProcess[str]) -> None:
        output = (result.stderr or result.stdout or "").strip()
        command = " ".join(args)
        super().__init__(f"Command failed ({result.returncode}): {command}\n{output}")
        self.args_list = tuple(args)
        self.result = result


class CommandRunner:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def run(self, args: Sequence[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            cwd=self.cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )


def checked(
    runner: CommandRunner, args: Sequence[str], input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = runner.run(args, input_text=input_text)
    if result.returncode != 0:
        raise CommandError(args, result)
    return result


def parse_github_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_bound(value: str | None, *, end_of_day: bool = False) -> dt.datetime | None:
    if not value:
        return None
    if len(value) == 10:
        parsed_date = dt.date.fromisoformat(value)
        parsed_time = dt.time.max if end_of_day else dt.time.min
        return dt.datetime.combine(parsed_date, parsed_time, tzinfo=dt.timezone.utc)
    return parse_github_time(value)


def gh_json(runner: CommandRunner, args: Sequence[str]) -> Any:
    result = checked(runner, args)
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def gh_json_lines(runner: CommandRunner, args: Sequence[str]) -> list[dict[str, Any]]:
    result = checked(runner, args)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
        else:
            raise ValueError(f"Expected JSON object from gh output, got {type(parsed).__name__}")
    return rows


def resolve_repo(runner: CommandRunner, repo: str | None) -> str:
    if repo:
        return repo
    result = checked(runner, ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    resolved = result.stdout.strip()
    if not resolved:
        raise RuntimeError("Could not resolve GitHub repository from gh repo view")
    return resolved


def discover_merged_pull_requests(
    runner: CommandRunner,
    *,
    repo: str,
    base: str,
    limit: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
) -> list[PullRequest]:
    raw = gh_json(
        runner,
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--base",
            base,
            "--limit",
            str(limit),
            "--json",
            "number,title,mergedAt,url,headRefName",
        ],
    )
    if not isinstance(raw, list):
        raise ValueError("gh pr list did not return a JSON list")

    pull_requests: list[PullRequest] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        merged_at = str(item.get("mergedAt") or "")
        merged_time = parse_github_time(merged_at)
        if merged_time is None:
            continue
        if since is not None and merged_time < since:
            continue
        if until is not None and merged_time > until:
            continue
        pull_requests.append(
            PullRequest(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                merged_at=merged_at,
                url=str(item.get("url") or ""),
                head_ref_name=item.get("headRefName"),
            )
        )
    return sorted(pull_requests, key=lambda pr: pr.number)


def review_comment_from_api(item: dict[str, Any]) -> ReviewComment:
    user = item.get("user")
    user_login = user.get("login") if isinstance(user, dict) else ""
    return ReviewComment(
        id=int(item["id"]),
        body=str(item.get("body") or ""),
        path=str(item.get("path") or ""),
        html_url=str(item.get("html_url") or ""),
        user_login=str(user_login or ""),
        created_at=str(item.get("created_at") or ""),
        diff_hunk=str(item.get("diff_hunk") or ""),
        in_reply_to_id=item.get("in_reply_to_id"),
        original_commit_id=item.get("original_commit_id"),
        commit_id=item.get("commit_id"),
        line=item.get("line"),
        original_line=item.get("original_line"),
        thread_id=item.get("thread_id"),
        thread_is_resolved=item.get("thread_is_resolved"),
        thread_is_outdated=item.get("thread_is_outdated"),
    )


def issue_comment_from_api(item: dict[str, Any]) -> IssueComment:
    user = item.get("user")
    user_login = user.get("login") if isinstance(user, dict) else ""
    return IssueComment(
        id=int(item["id"]),
        body=str(item.get("body") or ""),
        html_url=str(item.get("html_url") or ""),
        user_login=str(user_login or ""),
        created_at=str(item.get("created_at") or ""),
    )


def pull_request_review_from_api(item: dict[str, Any]) -> PullRequestReview:
    user = item.get("user")
    user_login = user.get("login") if isinstance(user, dict) else ""
    return PullRequestReview(
        id=int(item["id"]),
        body=str(item.get("body") or ""),
        user_login=str(user_login or ""),
        submitted_at=str(item.get("submitted_at") or ""),
        commit_id=item.get("commit_id"),
    )


def fetch_pr_review_comments(runner: CommandRunner, *, repo: str, pr_number: int) -> list[ReviewComment]:
    rows = gh_json_lines(
        runner,
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{pr_number}/comments?per_page=100",
            "--jq",
            ".[] | @json",
        ],
    )
    return [review_comment_from_api(row) for row in rows]


def fetch_pr_issue_comments(runner: CommandRunner, *, repo: str, pr_number: int) -> list[IssueComment]:
    rows = gh_json_lines(
        runner,
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
            "--jq",
            ".[] | @json",
        ],
    )
    return [issue_comment_from_api(row) for row in rows]


def fetch_pr_reviews(runner: CommandRunner, *, repo: str, pr_number: int) -> list[PullRequestReview]:
    rows = gh_json_lines(
        runner,
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100",
            "--jq",
            ".[] | @json",
        ],
    )
    return [pull_request_review_from_api(row) for row in rows]


REVIEW_COMMENTS_GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              body
              path
              url
              createdAt
              diffHunk
              line
              originalLine
              originalCommit { oid }
              commit { oid }
              replyTo { databaseId }
              author { __typename login }
            }
          }
        }
      }
    }
  }
}
""".strip()

REVIEW_THREAD_COMMENTS_GRAPHQL_QUERY = """
query($thread: ID!, $cursor: String!) {
  node(id: $thread) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          databaseId
          body
          path
          url
          createdAt
          diffHunk
          line
          originalLine
          originalCommit { oid }
          commit { oid }
          replyTo { databaseId }
          author { __typename login }
        }
      }
    }
  }
}
""".strip()


def _graphql_login(author: dict[str, Any]) -> str:
    login = str(author.get("login") or "")
    if author.get("__typename") == "Bot" and login and not login.endswith("[bot]"):
        return f"{login}[bot]"
    return login


def _graphql_comment_to_api_shape(node: dict[str, Any], *, thread: dict[str, Any]) -> dict[str, Any]:
    author = node.get("author") or {}
    reply_to = node.get("replyTo") or {}
    original_commit = node.get("originalCommit") or {}
    commit = node.get("commit") or {}
    return {
        "id": node.get("databaseId"),
        "body": node.get("body") or "",
        "path": node.get("path") or "",
        "html_url": node.get("url") or "",
        "user": {"login": _graphql_login(author)},
        "created_at": node.get("createdAt") or "",
        "diff_hunk": node.get("diffHunk") or "",
        "in_reply_to_id": reply_to.get("databaseId"),
        "original_commit_id": original_commit.get("oid"),
        "commit_id": commit.get("oid"),
        "line": node.get("line"),
        "original_line": node.get("originalLine"),
        "thread_id": thread.get("id"),
        "thread_is_resolved": thread.get("isResolved"),
        "thread_is_outdated": thread.get("isOutdated"),
    }


def _fetch_additional_thread_comment_nodes(
    runner: CommandRunner,
    *,
    thread: dict[str, Any],
    initial_connection: dict[str, Any],
) -> list[dict[str, Any]] | None:
    nodes = list(initial_connection.get("nodes") or [])
    page_info = initial_connection.get("pageInfo") or {}
    while page_info.get("hasNextPage"):
        thread_id = thread.get("id")
        cursor = page_info.get("endCursor")
        if not thread_id or not cursor:
            return None
        result = runner.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={REVIEW_THREAD_COMMENTS_GRAPHQL_QUERY}",
                "-F",
                f"thread={thread_id}",
                "-F",
                f"cursor={cursor}",
            ]
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            return None
        connection = ((payload.get("data") or {}).get("node") or {}).get("comments") or {}
        nodes.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
    return nodes


def fetch_review_comments_via_graphql(
    runner: CommandRunner, *, repo: str, pr_number: int
) -> list[ReviewComment] | None:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return None

    cursor: str | None = None
    rows: list[dict[str, Any]] = []
    while True:
        args = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={REVIEW_COMMENTS_GRAPHQL_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor is not None:
            args.extend(["-F", f"cursor={cursor}"])
        result = runner.run(args)
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            return None
        pr = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        threads = pr.get("reviewThreads") or {}
        for thread in threads.get("nodes") or []:
            if (
                not thread.get("id")
                or not isinstance(thread.get("isResolved"), bool)
                or not isinstance(thread.get("isOutdated"), bool)
            ):
                return None
            comment_nodes = _fetch_additional_thread_comment_nodes(
                runner,
                thread=thread,
                initial_connection=thread.get("comments") or {},
            )
            if comment_nodes is None:
                return None
            for node in comment_nodes:
                if node.get("databaseId") is None:
                    continue
                rows.append(_graphql_comment_to_api_shape(node, thread=thread))
        page_info = threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    return [review_comment_from_api(row) for row in rows]


def load_pr_review_comments(
    runner: CommandRunner,
    *,
    repo: str,
    pr_number: int,
    require_thread_state: bool = False,
) -> list[ReviewComment]:
    via_graphql = fetch_review_comments_via_graphql(runner, repo=repo, pr_number=pr_number)
    if via_graphql is not None:
        return via_graphql
    if require_thread_state:
        raise RuntimeError(
            f"GraphQL review-thread state is unavailable for PR #{pr_number}; "
            "refusing a write-mode sweep because REST comments cannot establish resolution state."
        )
    return fetch_pr_review_comments(runner, repo=repo, pr_number=pr_number)


def has_action_marker(replies: Sequence[ReviewComment]) -> bool:
    return any(ACTION_MARKER_REGEX.search(reply.body) for reply in replies)


def comment_precedes_merge(comment: ReviewComment, pr: PullRequest) -> bool:
    comment_time = parse_github_time(comment.created_at)
    merged_time = parse_github_time(pr.merged_at)
    if merged_time is None:
        raise ValueError(f"PR #{pr.number} is missing mergedAt; should be filtered upstream")
    if comment_time is None:
        return False
    return comment_time < merged_time


def review_inventory_for_pr(
    pr: PullRequest,
    comments: Sequence[ReviewComment],
    *,
    author_logins: set[str],
    include_post_merge: bool = False,
) -> ReviewInventory:
    replies_by_parent: dict[int, list[ReviewComment]] = {}
    for comment in comments:
        if comment.in_reply_to_id is not None:
            replies_by_parent.setdefault(int(comment.in_reply_to_id), []).append(comment)

    actionable: list[PendingComment] = []
    resolved_for_audit: list[PendingComment] = []
    for comment in comments:
        if comment.in_reply_to_id is not None:
            continue
        if comment.user_login not in author_logins:
            continue
        if not include_post_merge and not comment_precedes_merge(comment, pr):
            continue
        item = PendingComment(
            pr=pr,
            comment=comment,
            replies=tuple(replies_by_parent.get(comment.id, [])),
        )
        if comment.thread_is_resolved is True:
            resolved_for_audit.append(item)
            continue
        if has_action_marker(item.replies):
            continue
        actionable.append(item)
    return ReviewInventory(tuple(actionable), tuple(resolved_for_audit))


def pending_comments_for_pr(
    pr: PullRequest,
    comments: Sequence[ReviewComment],
    *,
    author_logins: set[str],
) -> list[PendingComment]:
    return list(review_inventory_for_pr(pr, comments, author_logins=author_logins).actionable)


def _timestamp_sort_key(timestamp: str, identifier: int) -> tuple[dt.datetime, int]:
    parsed = parse_github_time(timestamp) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (parsed, identifier)


def _comment_sort_key(comment: IssueComment) -> tuple[dt.datetime, int]:
    return _timestamp_sort_key(comment.created_at, comment.id)


def _review_sort_key(review: PullRequestReview) -> tuple[dt.datetime, int]:
    return _timestamp_sort_key(review.submitted_at, review.id)


def is_usage_limit_review_comment(comment: IssueComment, *, author_logins: set[str]) -> bool:
    return comment.user_login in author_logins and CODEX_USAGE_LIMIT_REVIEW_TEXT in comment.body


def is_review_trigger_comment(comment: IssueComment) -> bool:
    return comment.body.strip().lower() == CODEX_REVIEW_TRIGGER_BODY


def is_review_result_comment(comment: IssueComment, *, author_logins: set[str]) -> bool:
    return comment.user_login in author_logins and any(marker in comment.body for marker in CODEX_REVIEW_RESULT_MARKERS)


def is_review_result_review(review: PullRequestReview, *, author_logins: set[str]) -> bool:
    return review.user_login in author_logins and any(marker in review.body for marker in CODEX_REVIEW_RESULT_MARKERS)


def usage_limit_review_retry_for_pr(
    pr: PullRequest,
    comments: Sequence[IssueComment],
    *,
    author_logins: set[str],
    reviews: Sequence[PullRequestReview] = (),
) -> UsageLimitReviewRetry | None:
    usage_comments = [
        comment for comment in comments if is_usage_limit_review_comment(comment, author_logins=author_logins)
    ]
    if not usage_comments:
        return None

    latest_usage = max(usage_comments, key=_comment_sort_key)
    usage_time, usage_id = _comment_sort_key(latest_usage)
    latest_trigger: IssueComment | None = None
    result_reviews = [review for review in reviews if is_review_result_review(review, author_logins=author_logins)]
    for comment in comments:
        comment_time, comment_id = _comment_sort_key(comment)
        if (comment_time, comment_id) <= (usage_time, usage_id):
            continue
        if is_review_result_comment(comment, author_logins=author_logins):
            return None
        if is_review_trigger_comment(comment):
            if latest_trigger is None or _comment_sort_key(comment) > _comment_sort_key(latest_trigger):
                latest_trigger = comment
    if any(_review_sort_key(review) > (usage_time, usage_id) for review in result_reviews):
        return None
    return UsageLimitReviewRetry(pr=pr, usage_comment=latest_usage, trigger_comment=latest_trigger)


def discover_usage_limit_review_retries(
    runner: CommandRunner,
    *,
    repo: str,
    base: str,
    limit_prs: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
    author_logins: set[str],
) -> list[UsageLimitReviewRetry]:
    retries: list[UsageLimitReviewRetry] = []
    pull_requests = discover_merged_pull_requests(
        runner,
        repo=repo,
        base=base,
        limit=limit_prs,
        since=since,
        until=until,
    )
    for pr in pull_requests:
        comments = fetch_pr_issue_comments(runner, repo=repo, pr_number=pr.number)
        usage_comments = [
            comment for comment in comments if is_usage_limit_review_comment(comment, author_logins=author_logins)
        ]
        reviews = fetch_pr_reviews(runner, repo=repo, pr_number=pr.number) if usage_comments else []
        retry = usage_limit_review_retry_for_pr(pr, comments, author_logins=author_logins, reviews=reviews)
        if retry is not None:
            retries.append(retry)
    return retries


def discover_review_inventory(
    runner: CommandRunner,
    *,
    repo: str,
    base: str,
    limit_prs: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
    author_logins: set[str],
    require_thread_state: bool = False,
    include_post_merge: bool = False,
) -> ReviewInventory:
    actionable: list[PendingComment] = []
    resolved_for_audit: list[PendingComment] = []
    pull_requests = discover_merged_pull_requests(
        runner,
        repo=repo,
        base=base,
        limit=limit_prs,
        since=since,
        until=until,
    )
    for pr in pull_requests:
        comments = load_pr_review_comments(
            runner,
            repo=repo,
            pr_number=pr.number,
            require_thread_state=require_thread_state,
        )
        inventory = review_inventory_for_pr(
            pr,
            comments,
            author_logins=author_logins,
            include_post_merge=include_post_merge,
        )
        actionable.extend(inventory.actionable)
        resolved_for_audit.extend(inventory.resolved_for_audit)
    return ReviewInventory(tuple(actionable), tuple(resolved_for_audit))


def discover_pending_comments(
    runner: CommandRunner,
    *,
    repo: str,
    base: str,
    limit_prs: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
    author_logins: set[str],
    include_post_merge: bool = False,
) -> list[PendingComment]:
    return list(
        discover_review_inventory(
            runner,
            repo=repo,
            base=base,
            limit_prs=limit_prs,
            since=since,
            until=until,
            author_logins=author_logins,
            include_post_merge=include_post_merge,
        ).actionable
    )


def first_body_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return "(empty comment)"


def print_pending_table(pending: Sequence[PendingComment]) -> None:
    if not pending:
        print("No pending PR review comments found; this is not a completion signal.")
        print(
            "Reconcile all open PRs with `make pr-status ALL_OPEN=1` and resume state "
            "with `make pr-followup-resume KEY=...`."
        )
        return
    print(f"{'PR':>6}  {'Comment':>12}  {'Thread':<24}  {'Resolved':<9}  {'Outdated':<9}  {'Path':<36}  Finding")
    print(f"{'-' * 6}  {'-' * 12}  {'-' * 24}  {'-' * 9}  {'-' * 9}  {'-' * 36}  {'-' * 40}")
    for item in pending:
        path = item.comment.path[:36]
        resolved = (
            "unknown" if item.comment.thread_is_resolved is None else str(item.comment.thread_is_resolved).lower()
        )
        outdated = (
            "unknown" if item.comment.thread_is_outdated is None else str(item.comment.thread_is_outdated).lower()
        )
        print(
            f"#{item.pr.number:<5}  {item.comment.id:>12}  {(item.comment.thread_id or 'unknown')[:24]:<24}  "
            f"{resolved:<9}  {outdated:<9}  {path:<36}  {first_body_line(item.comment.body)}"
        )


def print_resolved_audit_table(candidates: Sequence[PendingComment]) -> None:
    if not candidates:
        print("\nNo resolved PR review threads require phantom-resolution audit.")
        return
    print("\nResolved PR review threads for phantom-resolution audit:")
    print_pending_table(candidates)


def print_usage_limit_retry_table(retries: Sequence[UsageLimitReviewRetry]) -> None:
    if not retries:
        print("No PRs need usage-limit review follow-up.")
        return
    print("\nPRs needing usage-limit review follow-up:")
    print(f"{'PR':>6}  {'Comment':>12}  {'Status':<22}  {'Merged':<20}  Title")
    print(f"{'-' * 6}  {'-' * 12}  {'-' * 22}  {'-' * 20}  {'-' * 40}")
    for item in retries:
        status = "needs-trigger" if item.needs_trigger else "awaiting-review-result"
        print(
            f"#{item.pr.number:<5}  {item.usage_comment.id:>12}  "
            f"{status:<22}  {item.pr.merged_at[:20]:<20}  {item.pr.title[:80]}"
        )


def select_usage_limit_retry_triggers(
    retries: Sequence[UsageLimitReviewRetry],
    *,
    max_comments: int,
    pending_count: int,
) -> list[UsageLimitReviewRetry]:
    candidates = [retry for retry in retries if retry.needs_trigger]
    if max_comments <= 0:
        return candidates
    remaining_budget = max(max_comments - pending_count, 0)
    return candidates[:remaining_budget]


def git_state_snapshot(runner: CommandRunner) -> str:
    head = checked(runner, ["git", "rev-parse", "HEAD"]).stdout.strip()
    status = checked(runner, ["git", "status", "--porcelain"]).stdout
    return f"HEAD {head}\n{status}"


def git_changed_paths(runner: CommandRunner) -> list[str]:
    paths: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = checked(runner, cmd)
        paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(paths)


def local_commit_count(runner: CommandRunner, base_ref: str) -> int:
    result = checked(runner, ["git", "rev-list", "--count", f"{base_ref}..HEAD"])
    return int(result.stdout.strip() or "0")


def discover_locally_committed_pairs(runner: CommandRunner, base_ref: str) -> set[tuple[int, int]]:
    result = checked(runner, ["git", "log", "--format=%s", f"{base_ref}..HEAD"])
    pairs: set[tuple[int, int]] = set()
    for line in result.stdout.splitlines():
        match = PER_COMMENT_COMMIT_SUBJECT_REGEX.match(line)
        if match:
            pairs.add((int(match.group(1)), int(match.group(2))))
    return pairs


def current_branch(runner: CommandRunner) -> str:
    return checked(runner, ["git", "branch", "--show-current"]).stdout.strip()


def truncate_summary(text: str, limit: int = MAX_REPLY_SUMMARY_CHARS) -> str:
    normalized = textwrap.dedent(text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 15].rstrip() + "\n[truncated]"


def load_prompt_template(path: Path = PROMPT_TEMPLATE_PATH) -> str:
    return path.read_text(encoding="utf-8")


def build_executor_prompt(
    item: PendingComment,
    *,
    repo: str,
    base: str,
    template: str | None = None,
) -> str:
    comment = item.comment
    pr = item.pr
    rendered = (template or load_prompt_template()).format(
        repo=repo,
        base=base,
        pr_number=pr.number,
        pr_title=pr.title,
        pr_url=pr.url,
        pr_merged_at=pr.merged_at,
        comment_html_url=comment.html_url,
        comment_id=comment.id,
        comment_path=comment.path,
        comment_line=comment.line,
        comment_original_line=comment.original_line,
        comment_commit_id=comment.commit_id,
        comment_original_commit_id=comment.original_commit_id,
        comment_diff_hunk=comment.diff_hunk,
        comment_body=comment.body,
    )
    return rendered.strip()


def _executor_command() -> str:
    if os.name != "nt":
        return "codex"
    resolved = shutil.which("codex")
    if resolved is None:
        raise FileNotFoundError("codex")
    return resolved


def check_executor_version(
    runner: CommandRunner, *, minimum: tuple[int, int, int] = MIN_EXECUTOR_VERSION
) -> tuple[int, int, int]:
    try:
        command = _executor_command()
        result = runner.run([command, "--version"])
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Executor binary `codex` not found on PATH. Install codex-cli "
            f">= {'.'.join(str(p) for p in minimum)} (https://github.com/openai/codex)."
        ) from exc
    if result.returncode != 0:
        raise CommandError([command, "--version"], result)
    text = (result.stdout or result.stderr or "").strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise RuntimeError(f"Could not parse executor version from output: {text!r}")
    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if parsed < minimum:
        raise RuntimeError(
            f"Executor codex-cli {'.'.join(str(p) for p in parsed)} is below the required "
            f">= {'.'.join(str(p) for p in minimum)}. Upgrade the executor."
        )
    return parsed


def run_executor_for_comment(
    runner: CommandRunner,
    item: PendingComment,
    *,
    repo: str,
    base: str,
    executor_model: str | None,
    executor_sandbox: str,
    executor_approval: str,
) -> ActionResult:
    before = git_state_snapshot(runner)
    prompt = build_executor_prompt(item, repo=repo, base=base)
    cmd = [
        _executor_command(),
        "exec",
        "--cd",
        str(runner.cwd),
        "--sandbox",
        executor_sandbox,
        "-c",
        f"approval_policy={executor_approval}",
    ]
    if executor_model:
        cmd.extend(["--model", executor_model])
    cmd.append("-")

    print(f"\n==> PR #{item.pr.number} comment {item.comment.id}: {item.comment.html_url}", flush=True)
    result = runner.run(cmd, input_text=prompt)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode != 0:
        raise CommandError(cmd, result)

    after = git_state_snapshot(runner)
    disposition = "fixed" if after != before else "no-current-action"
    summary = result.stdout.strip() or result.stderr.strip() or f"Disposition: {disposition}"
    return ActionResult(pending=item, disposition=disposition, summary=summary)


def commit_message_for_result(result: ActionResult) -> str:
    pr = result.pending.pr
    comment = result.pending.comment
    headline = first_body_line(comment.body)
    subject = f"{PER_COMMENT_COMMIT_PREFIX}: PR #{pr.number} comment {comment.id} — {headline}"
    if len(subject) > 100:
        keep = 100 - (len(subject) - len(headline)) - 1
        subject = f"{PER_COMMENT_COMMIT_PREFIX}: PR #{pr.number} comment {comment.id} — {headline[: max(keep, 1)]}…"
    body = f"Disposition: {result.disposition}\nSource: {comment.html_url}\nPath: {comment.path}\n"
    return f"{subject}\n\n{body}"


def _porcelain_paths_with_worktree_mods(porcelain: str) -> set[str]:
    paths: set[str] = set()
    for line in porcelain.splitlines():
        if len(line) < 4 or line[2] != " ":
            continue
        worktree_status = line[1]
        if worktree_status == " ":
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return paths


def commit_changes_for_result(runner: CommandRunner, result: ActionResult) -> bool:
    if result.disposition != "fixed":
        return False
    paths = git_changed_paths(runner)
    if not paths:
        return False
    print("==> Staging changed paths for this comment")
    for path in paths:
        print(f"  {path}")
    stage_paths(runner, paths)
    staged_paths = checked(runner, ["git", "diff", "--cached", "--name-only"]).stdout.splitlines()
    if not staged_paths:
        return False
    commit_args = ["git", "commit", "-m", commit_message_for_result(result)]
    commit_result = runner.run(commit_args)
    if commit_result.returncode == 0:
        return True
    stderr = commit_result.stderr or ""
    if HOOK_AUTOFIX_STDERR_MARKER not in stderr:
        raise CommandError(commit_args, commit_result)
    porcelain = checked(runner, ["git", "status", "--porcelain"]).stdout
    auto_fixed = _porcelain_paths_with_worktree_mods(porcelain)
    if not auto_fixed.intersection(staged_paths):
        raise CommandError(commit_args, commit_result)
    print("==> pre-commit hook auto-fixed staged paths; re-staging and retrying once")
    stage_paths(runner, staged_paths)
    checked(runner, commit_args)
    return True


def build_reply_body(result: ActionResult, *, branch: str) -> str:
    comment = result.pending.comment
    pr = result.pending.pr
    summary = truncate_summary(result.summary)
    return textwrap.dedent(
        f"""
        <!-- {ACTION_MARKER}: pr={pr.number} comment_id={comment.id} branch={branch} -->
        Follow-up sweep actioned this PR review comment.

        Disposition: {result.disposition}
        Branch: `{branch}`

        Summary:
        {summary}

        Future sweeps skip comments that already have this marker reply.
        """
    ).strip()


_TRANSIENT_GH_STDERR_REGEX = re.compile(
    r"error connecting to|check your internet connection|\bHTTP 5\d\d\b|\bHTTP 429\b",
    re.IGNORECASE,
)


def _is_transient_gh_failure(stderr: str) -> bool:
    if not stderr:
        return False
    return bool(_TRANSIENT_GH_STDERR_REGEX.search(stderr))


def _gh_command_with_retry(
    runner: CommandRunner,
    args: Sequence[str],
    *,
    attempts: int = GH_API_RETRY_ATTEMPTS,
    backoffs: Sequence[int] = GH_API_RETRY_BACKOFFS,
    sleeper: Callable[[float], None] = time.sleep,
) -> subprocess.CompletedProcess[str]:
    for attempt in range(1, attempts + 1):
        result = runner.run(args)
        if result.returncode == 0:
            return result
        stderr = result.stderr or ""
        if not _is_transient_gh_failure(stderr) or attempt >= attempts:
            raise CommandError(args, result)
        backoff = backoffs[attempt - 1] if attempt - 1 < len(backoffs) else backoffs[-1]
        print(
            f"Transient GitHub CLI failure on attempt {attempt}/{attempts}; retrying in {backoff}s.",
            file=sys.stderr,
            flush=True,
        )
        sleeper(backoff)
    raise RuntimeError("_gh_command_with_retry exited without returning or raising")


def reply_to_comment(
    runner: CommandRunner,
    *,
    repo: str,
    result: ActionResult,
    branch: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    pr_number = result.pending.pr.number
    comment_id = result.pending.comment.id
    body = build_reply_body(result, branch=branch)
    _gh_command_with_retry(
        runner,
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{repo}/pulls/{pr_number}/comments/{comment_id}/replies",
            "-f",
            f"body={body}",
        ],
        sleeper=sleeper,
    )
    print(f"Replied to PR #{pr_number} comment {comment_id}.")


def trigger_usage_limit_review_retry(
    runner: CommandRunner,
    *,
    repo: str,
    retry: UsageLimitReviewRetry,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    _gh_command_with_retry(
        runner,
        [
            "gh",
            "pr",
            "comment",
            str(retry.pr.number),
            "--repo",
            repo,
            "--body",
            CODEX_REVIEW_TRIGGER_BODY,
        ],
        sleeper=sleeper,
    )
    print(f"Triggered Codex review retry on PR #{retry.pr.number}.")


def ensure_clean_start(runner: CommandRunner, *, allow_dirty: bool, base_ref: str) -> None:
    changed = git_changed_paths(runner)
    local_commits = local_commit_count(runner, base_ref)
    if (changed or local_commits) and not allow_dirty:
        joined = "\n".join(f"  - {path}" for path in changed) if changed else "  (none)"
        raise RuntimeError(
            "Refusing to start with an in-progress branch. Commit/stash unrelated work or rerun with "
            f"--allow-dirty.\nChanged paths:\n{joined}\nLocal commits ahead of {base_ref}: {local_commits}"
        )


def ensure_action_branch(branch: str, *, no_submit: bool) -> None:
    if no_submit:
        return
    if branch in {"", "develop", "release"}:
        raise RuntimeError(
            f"Refusing to action comments from branch {branch or '(detached)'} because the routine submits a PR. "
            "Claim a feature worktree first or rerun with --no-submit."
        )


def stage_paths(runner: CommandRunner, paths: Sequence[str]) -> None:
    if not paths:
        return
    checked(runner, ["git", "add", "--", *paths])


def finalize_changes(
    runner: CommandRunner,
    *,
    base_ref: str,
    commit_message: str,
    no_submit: bool,
) -> None:
    paths = git_changed_paths(runner)
    commits = local_commit_count(runner, base_ref)
    if not paths and commits == 0:
        print("No file changes or local commits were produced; skipping PR submission.")
        return

    if paths:
        print("\n==> Sweeping leftover changed paths into fallback commit")
        for path in paths:
            print(f"  {path}")
        stage_paths(runner, paths)
        staged = checked(runner, ["git", "diff", "--cached", "--name-only"]).stdout.splitlines()
        if staged:
            checked(runner, ["git", "commit", "-m", commit_message])

    print("\n==> Running pr-preflight")
    checked(runner, ["make", "pr-preflight"])

    if no_submit:
        print("Skipping PR submission because --no-submit was set.")
        return

    print("\n==> Opening PR through existing make pr-open flow")
    checked(runner, ["make", "pr-open"])


def run_action_loop(args: argparse.Namespace, runner: CommandRunner) -> int:
    repo = resolve_repo(runner, args.repo)
    since = parse_bound(args.since)
    until = parse_bound(args.until, end_of_day=True)
    authors = set(args.author)
    branch = ""
    resume = bool(getattr(args, "resume", False))
    if args.command == "run":
        if not args.dry_run:
            branch = current_branch(runner)
            ensure_action_branch(branch, no_submit=args.no_submit)
            check_executor_version(runner)
        ensure_clean_start(
            runner,
            allow_dirty=args.allow_dirty or args.dry_run or resume,
            base_ref=f"origin/{args.base}",
        )
    require_thread_state = (args.command == "run" and not args.dry_run) or bool(
        getattr(args, "include_resolved", False) or getattr(args, "fail_on_pending", False)
    )
    inventory = discover_review_inventory(
        runner,
        repo=repo,
        base=args.base,
        limit_prs=args.limit_prs,
        since=since,
        until=until,
        author_logins=authors,
        require_thread_state=require_thread_state,
        include_post_merge=bool(getattr(args, "include_post_merge", False)),
    )
    all_pending = list(inventory.actionable)
    pending = list(all_pending)
    usage_limit_retries: list[UsageLimitReviewRetry] = []
    if args.retry_usage_limit_reviews:
        usage_limit_retries = discover_usage_limit_review_retries(
            runner,
            repo=repo,
            base=args.base,
            limit_prs=args.limit_prs,
            since=since,
            until=until,
            author_logins=authors,
        )
    if resume and args.command == "run":
        already = discover_locally_committed_pairs(runner, f"origin/{args.base}")
        if already:
            before = len(pending)
            pending = [item for item in pending if (item.pr.number, item.comment.id) not in already]
            print(f"--resume: skipping {before - len(pending)} comment(s) already committed locally on this branch.")
    if args.max_comments > 0:
        pending = pending[: args.max_comments]

    print_pending_table(pending)
    if bool(getattr(args, "include_resolved", False)):
        print_resolved_audit_table(inventory.resolved_for_audit)
    print_usage_limit_retry_table(usage_limit_retries)
    if args.command == "list" or args.dry_run:
        if bool(getattr(args, "fail_on_pending", False)) and (all_pending or usage_limit_retries):
            return 1
        return 0
    retried_usage_limit_reviews = select_usage_limit_retry_triggers(
        usage_limit_retries,
        max_comments=args.max_comments,
        pending_count=len(pending),
    )
    for retry in retried_usage_limit_reviews:
        trigger_usage_limit_review_retry(runner, repo=repo, retry=retry)
    if retried_usage_limit_reviews:
        print(f"\nTriggered {len(retried_usage_limit_reviews)} usage-limit review retry request(s).")
    if not pending:
        finalize_changes(
            runner,
            base_ref=f"origin/{args.base}",
            commit_message=args.commit_message,
            no_submit=args.no_submit,
        )
        return 0

    results: list[ActionResult] = []
    for item in pending:
        result = run_executor_for_comment(
            runner,
            item,
            repo=repo,
            base=args.base,
            executor_model=args.executor_model,
            executor_sandbox=args.executor_sandbox,
            executor_approval=args.executor_approval,
        )
        commit_changes_for_result(runner, result)
        results.append(result)
        if not args.no_reply:
            reply_to_comment(runner, repo=repo, result=result, branch=branch)

    print(f"\nActioned {len(results)} PR review comment(s).")
    finalize_changes(
        runner,
        base_ref=f"origin/{args.base}",
        commit_message=args.commit_message,
        no_submit=args.no_submit,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "run"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--repo", default=os.environ.get("PR_REVIEW_REPO"))
        sub.add_argument("--base", default=os.environ.get("PR_REVIEW_BASE", "develop"))
        sub.add_argument("--limit-prs", type=int, default=int(os.environ.get("PR_REVIEW_PR_LIMIT", "1000")))
        sub.add_argument("--max-comments", type=int, default=int(os.environ.get("PR_REVIEW_MAX_COMMENTS", "0")))
        sub.add_argument("--since", default=os.environ.get("PR_REVIEW_SINCE"))
        sub.add_argument("--until", default=os.environ.get("PR_REVIEW_UNTIL"))
        sub.add_argument("--author", action="append", default=list(DEFAULT_REVIEW_AUTHORS))
        sub.add_argument(
            "--include-post-merge",
            default=os.environ.get("PR_REVIEW_INCLUDE_POST_MERGE", "0").lower() in {"1", "true", "yes"},
            action=argparse.BooleanOptionalAction,
            help="Include matching review comments posted after the PR merged (needed for retriggered reviews).",
        )
        sub.add_argument(
            "--no-usage-limit-review-retry",
            dest="retry_usage_limit_reviews",
            action="store_false",
            default=os.environ.get("PR_REVIEW_USAGE_LIMIT_RETRY", "1").lower() not in {"0", "false", "no"},
            help=(
                "Do not scan top-level PR comments for Codex usage-limit review failures. "
                "By default the list command reports them and the run command posts a fresh "
                "`@codex review` trigger when no later trigger or review result exists."
            ),
        )
    list_parser = subparsers.choices["list"]
    list_parser.add_argument(
        "--include-resolved",
        action="store_true",
        default=os.environ.get("PR_REVIEW_INCLUDE_RESOLVED", "0").lower() in {"1", "true", "yes"},
        help="Include resolved, unmarked threads for the separate phantom-resolution audit.",
    )
    list_parser.add_argument(
        "--fail-on-pending",
        action="store_true",
        default=os.environ.get("PR_REVIEW_FAIL_ON_PENDING", "0").lower() in {"1", "true", "yes"},
        help="Exit non-zero when the filtered review-comment queue is non-empty.",
    )
    run_parser = subparsers.choices["run"]
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--allow-dirty", action="store_true")
    run_parser.add_argument("--no-reply", action="store_true")
    run_parser.add_argument("--no-submit", action="store_true")
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip pending comments whose per-comment fix already landed locally "
            "(parses `git log origin/<base>..HEAD` for the per-comment commit "
            "subject). Implies --allow-dirty so a branch with prior commits is "
            "accepted. Use this after a crashed sweep to re-drive the routine."
        ),
    )
    run_parser.add_argument(
        "--executor-model",
        default=os.environ.get("PR_REVIEW_EXECUTOR_MODEL"),
        help="Optional model name passed through to the executor (`codex --model`).",
    )
    run_parser.add_argument(
        "--executor-sandbox",
        default=os.environ.get("PR_REVIEW_EXECUTOR_SANDBOX", "workspace-write"),
        help="Sandbox profile passed through to the executor (`codex --sandbox`).",
    )
    run_parser.add_argument(
        "--executor-approval",
        default=os.environ.get("PR_REVIEW_EXECUTOR_APPROVAL", "never"),
        help="Approval policy passed through to the executor (`-c approval_policy=...`).",
    )
    run_parser.add_argument("--commit-message", default=DEFAULT_COMMIT_MESSAGE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runner = CommandRunner(Path.cwd())
    try:
        return run_action_loop(args, runner)
    except (CommandError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
