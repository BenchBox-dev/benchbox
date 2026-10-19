#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

BASE_BRANCH = "develop"
BASE_REF = f"origin/{BASE_BRANCH}"
REPOSITORY_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"

Runner = Callable[[list[str]], tuple[int, str]]


class PrOpenError(Exception):
    pass


@dataclass(frozen=True)
class Commit:
    subject: str
    body: str


@dataclass(frozen=True)
class PublishedPr:
    status: str
    number: int
    url: str


def live_run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)


def _check(run: Runner, cmd: list[str], what: str) -> str:
    code, out = run(cmd)
    if code != 0:
        raise PrOpenError(f"{what} failed: {out.strip()}")
    return out


def _json(out: str, what: str) -> object:
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise PrOpenError(f"unreadable {what} response: {exc}") from exc


def branch_commits(run: Runner, base_ref: str = BASE_REF) -> list[Commit]:
    fmt = f"%s{FIELD_SEPARATOR}%b{RECORD_SEPARATOR}"
    out = _check(run, ["git", "log", "--reverse", f"--format={fmt}", f"{base_ref}..HEAD"], "git log")
    commits = []
    for record in out.split(RECORD_SEPARATOR):
        record = record.lstrip("\n")
        if not record:
            continue
        subject, _, body = record.partition(FIELD_SEPARATOR)
        commits.append(Commit(subject.strip(), body.strip()))
    return commits


def default_title(commits: Sequence[Commit]) -> str:
    if not commits or not commits[0].subject:
        raise PrOpenError(f"no commits ahead of {BASE_REF}; nothing to open a PR for")
    return commits[0].subject


def default_body(commits: Sequence[Commit]) -> str:
    if len(commits) == 1:
        return commits[0].body
    return "\n".join(f"- {commit.subject}" for commit in commits)


def find_open_pr(run: Runner, repo: str, head_owner: str, branch: str) -> dict[str, object] | None:
    cmd = ["gh", "api", "--method", "GET", f"repos/{repo}/pulls", "-f", f"head={head_owner}:{branch}"]
    cmd += ["-f", f"base={BASE_BRANCH}", "-f", "state=open", "-f", "per_page=100"]
    pulls = _json(_check(run, cmd, "open PR lookup"), "open PR lookup")
    if not isinstance(pulls, list):
        raise PrOpenError("unreadable open PR lookup response: expected a list")
    for pull in pulls:
        if not isinstance(pull, dict):
            raise PrOpenError("unreadable open PR lookup response: expected PR objects")
        head = pull.get("head") or {}
        owner = ((head.get("repo") or {}).get("owner") or {}).get("login") or (head.get("user") or {}).get("login")
        if str(owner).lower() == head_owner.lower() and head.get("ref") == branch:
            return pull
    return None


def _pr_from(response: object, status: str) -> PublishedPr:
    if not isinstance(response, dict) or "number" not in response or "html_url" not in response:
        raise PrOpenError(f"unreadable {status} PR response: missing number or html_url")
    return PublishedPr(status, int(response["number"]), str(response["html_url"]))


def publish(
    repo: str,
    head_owner: str,
    branch: str,
    head_spec: str,
    title: str | None,
    body_file: Path | None,
    run: Runner = live_run,
) -> PublishedPr:
    existing = find_open_pr(run, repo, head_owner, branch)
    if existing is not None:
        pr = _pr_from(existing, "reused")
        fields = []
        if title and title.strip() != str(existing.get("title") or "").strip():
            fields += ["-f", f"title={title}"]
        if body_file is not None:
            body = body_file.read_text(encoding="utf-8")
            current_body = str(existing.get("body") or "")
            if body.replace("\r\n", "\n").rstrip() != current_body.replace("\r\n", "\n").rstrip():
                fields += ["-F", f"body=@{body_file}"]
        if fields:
            cmd = ["gh", "api", "--method", "PATCH", f"repos/{repo}/pulls/{pr.number}"]
            _check(run, cmd + fields, "PR text update")
        return pr
    commits = branch_commits(run)
    resolved_title = title or default_title(commits)
    cmd = ["gh", "api", "--method", "POST", f"repos/{repo}/pulls"]
    cmd += ["-f", f"title={resolved_title}", "-f", f"head={head_spec}", "-f", f"base={BASE_BRANCH}"]
    cmd += ["-F", f"body=@{body_file}"] if body_file is not None else ["-f", f"body={default_body(commits)}"]
    return _pr_from(_json(_check(run, cmd, "PR create"), "PR create"), "created")


def main(argv: Sequence[str] | None = None, run: Runner = live_run) -> int:
    parser = argparse.ArgumentParser(description="Find, create, or update the develop PR for a branch over REST.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head-owner", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--head-spec", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--body-file", default="")
    args = parser.parse_args(argv)
    if not re.fullmatch(REPOSITORY_PATTERN, args.repo):
        print(f"pr-open: --repo must be owner/name, got {args.repo!r}", file=sys.stderr)
        return 2
    body_file = Path(args.body_file) if args.body_file else None
    if body_file is not None and not body_file.is_file():
        print(f"PR_BODY_FILE does not exist: {body_file}", file=sys.stderr)
        return 1
    try:
        pr = publish(args.repo, args.head_owner, args.branch, args.head_spec, args.title or None, body_file, run)
    except (PrOpenError, OSError) as exc:
        print(f"pr-open: {exc}", file=sys.stderr)
        return 1
    print(f"{pr.status} {pr.number} {pr.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
