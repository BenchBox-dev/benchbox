#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPOSITORY = "BenchBox-dev/BenchBox"
BASE_BRANCH = "develop"
TRUNK_WORKFLOW = "trunk.yml"
REVERT_PREFIX = "fix/revert-"
GRACE = timedelta(minutes=30)
RED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
RUN_FIELDS = "conclusion,status,createdAt,updatedAt,databaseId,url,headSha"
RUN_WINDOW = 30

Runner = Callable[[list[str]], tuple[int, str]]


class TrunkError(Exception):
    pass


class NoTrunkRunsError(TrunkError):
    pass


def live_run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)


def _check(run: Runner, cmd: list[str], what: str) -> str:
    code, out = run(cmd)
    if code != 0:
        raise TrunkError(f"{what} failed: {out.strip()}")
    return out


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def trunk_runs(run: Runner = live_run, repo: str = REPOSITORY) -> list[dict]:
    code, out = run(
        ["gh", "run", "list", "--repo", repo, "--workflow", TRUNK_WORKFLOW, "--branch", BASE_BRANCH]
        + ["--limit", str(RUN_WINDOW), "--json", RUN_FIELDS]
    )
    if code != 0:
        raise TrunkError(f"gh run list failed: {out.strip()}")
    try:
        runs = json.loads(out) if out.strip() else []
        if not runs:
            raise NoTrunkRunsError(f"no {TRUNK_WORKFLOW} runs on {BASE_BRANCH} yet")
        for item in runs:
            _parse_time(item["updatedAt"])
            if not isinstance(item["url"], str) or not item["url"].strip():
                raise ValueError("missing run URL")
        return sorted(runs, key=lambda item: (_parse_time(item["createdAt"]), int(item["databaseId"])), reverse=True)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise TrunkError(f"unreadable gh run list output: {exc}") from exc


def trunk_gate(
    branch: str,
    run: Runner = live_run,
    repo: str = REPOSITORY,
    now: datetime | None = None,
    grace: timedelta = GRACE,
) -> str | None:
    if branch.startswith(REVERT_PREFIX):
        return None
    try:
        newest = trunk_runs(run, repo)[0]
    except TrunkError as exc:
        print(f"trunk-gate: warning: could not read {TRUNK_WORKFLOW} runs ({exc}); not blocking", file=sys.stderr)
        return None
    if newest.get("status") != "completed" or newest.get("conclusion") not in RED_CONCLUSIONS:
        return None
    red_since = _parse_time(newest["updatedAt"])
    age = (now or datetime.now(UTC)) - red_since
    if age <= grace:
        return None
    minutes = int(age.total_seconds() // 60)
    return (
        f"develop has been red for {minutes} minutes (newest {TRUNK_WORKFLOW} run: {newest['url']}; "
        f"failed at {red_since.isoformat()}). Fix it or revert the culprit with `make trunk-revert PR=<number>`; "
        f"branches named {REVERT_PREFIX}* are exempt."
    )


def red_streak_start(run: Runner, repo: str) -> dict | None:
    start = None
    for item in trunk_runs(run, repo):
        if item.get("status") != "completed":
            continue
        if item.get("conclusion") in RED_CONCLUSIONS:
            start = item
        elif item.get("conclusion") == "success":
            break
    return start


def culprit_pr(run: Runner, repo: str, sha: str) -> int | None:
    code, out = run(["gh", "api", f"repos/{repo}/commits/{sha}/pulls"])
    if code != 0:
        return None
    try:
        merged = [
            int(item["number"])
            for item in json.loads(out)
            if item.get("merged_at") and (item.get("base") or {}).get("ref") == BASE_BRANCH
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return merged[0] if merged else None


def is_reverted(run: Runner, repo: str, sha: str) -> bool:
    out = _check(
        run,
        ["gh", "api", f"repos/{repo}/commits?sha={BASE_BRANCH}&per_page=100"],
        f"listing recent {BASE_BRANCH} commits",
    )
    try:
        return any(f"This reverts commit {sha}" in (item["commit"]["message"] or "") for item in json.loads(out))
    except (ValueError, KeyError, TypeError) as exc:
        raise TrunkError(f"unreadable {BASE_BRANCH} commit list: {exc}") from exc


def pr_head(ref: str, run: Runner, repo: str) -> tuple[str, list[str]]:
    view = _check(run, ["gh", "pr", "view", ref, "--repo", repo, "--json", "headRefName,commits"], f"reading PR {ref}")
    try:
        data = json.loads(view)
        branch = data["headRefName"]
        messages = [f"{item['messageHeadline']}\n{item['messageBody']}" for item in data["commits"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise TrunkError(f"unreadable PR {ref}: {exc}") from exc
    if not isinstance(branch, str) or not branch:
        raise TrunkError(f"unreadable PR {ref} head branch")
    return branch, messages


def arm_gate(ref: str, run: Runner = live_run, repo: str = REPOSITORY) -> str | None:
    branch, messages = pr_head(ref, run, repo)
    try:
        failed = red_streak_start(run, repo)
    except NoTrunkRunsError:
        return None
    if failed is None:
        return None
    sha = failed.get("headSha") or ""
    if sha and is_reverted(run, repo, sha):
        return None
    if sha and branch.startswith(REVERT_PREFIX) and any(f"This reverts commit {sha}" in text for text in messages):
        return None
    culprit = culprit_pr(run, repo, sha) if sha else None
    named = f"PR #{culprit}" if culprit else f"commit {sha[:9] or 'unknown'}"
    revert_hint = f"make trunk-revert PR={culprit}" if culprit else "make trunk-revert PR=<number>"
    return (
        f"{BASE_BRANCH} is red: the first failing {TRUNK_WORKFLOW} run since the last green one failed at {named} ({failed['url']}). "
        f"Merge a revert ({revert_hint}) or get {BASE_BRANCH} green before arming other PRs; "
        f"only a {REVERT_PREFIX}* branch whose commits carry `This reverts commit {sha[:9] or '<sha>'}...` is exempt."
    )


def revert_commands(
    number: int, oid: str, title: str, worktree: str, run_url: str, repo: str = REPOSITORY, head: str | None = None
) -> list[list[str]]:
    branch = f"{REVERT_PREFIX}{number}"
    subject = f'Revert "{title}" (#{number})'
    return [
        ["git", "fetch", "origin", BASE_BRANCH, "--quiet"],
        ["make", "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={worktree}"],
        ["git", "-C", worktree, "revert", "--no-edit", oid],
        ["git", "-C", worktree, "commit", "--amend", "-m", subject, "-m", f"This reverts commit {oid}."],
        ["git", "-C", worktree, "push", "-u", "origin", branch],
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            BASE_BRANCH,
            "--head",
            head or branch,
            "--title",
            subject,
            "--body",
            f"Reverts #{number} to address the failed {TRUNK_WORKFLOW} run: {run_url}",
        ],
    ]


def origin_owner(url: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        url.strip(),
    )
    return (match.group(1), match.group(2)) if match else None


def head_spec(run: Runner, repo: str, branch: str) -> str:
    url = _check(run, ["git", "remote", "get-url", "--push", "origin"], "resolving the origin remote")
    origin = origin_owner(url)
    if origin is None:
        raise TrunkError("origin is not a supported GitHub URL or SSH form")
    if f"{origin[0]}/{origin[1]}".lower() == repo.lower():
        return branch
    return f"{origin[0]}:{branch}"


def revert(number: int, run: Runner = live_run, repo: str = REPOSITORY) -> int:
    try:
        view = json.loads(
            _check(
                run,
                ["gh", "pr", "view", str(number), "--repo", repo, "--json", "state,mergeCommit,title"],
                f"reading PR #{number}",
            )
        )
        if view.get("state") != "MERGED":
            raise TrunkError(f"PR #{number} is {view.get('state')!r}, not MERGED; there is nothing to revert")
        oid = (view.get("mergeCommit") or {}).get("oid") or ""
        if not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise TrunkError(f"PR #{number} has no usable merge commit ({oid!r})")
        title = view.get("title") or ""
        if not title.strip():
            raise TrunkError(f"PR #{number} has no title")
        failed = next(
            (
                item
                for item in trunk_runs(run, repo)
                if item.get("status") == "completed" and item.get("conclusion") in RED_CONCLUSIONS
            ),
            None,
        )
        if failed is None:
            raise TrunkError(f"no failed {TRUNK_WORKFLOW} run on {BASE_BRANCH} to name in the revert PR")
        branch = f"{REVERT_PREFIX}{number}"
        head = head_spec(run, repo, branch)
        top = _check(run, ["git", "rev-parse", "--show-toplevel"], "locating this worktree").strip()
        worktree = str(Path(top).parent / f"BenchBox.wt-revert-{number}")
        fetch, create_worktree, revert_cmd, amend, push, create = revert_commands(
            number, oid, title, worktree, failed["url"], repo, head
        )
        _check(run, fetch, "fetching origin/develop")
        _check(
            run,
            ["git", "merge-base", "--is-ancestor", oid, f"origin/{BASE_BRANCH}"],
            f"confirming {oid[:9]} is on origin/{BASE_BRANCH}",
        )
        _check(run, create_worktree, f"creating the {branch} worktree")
        try:
            code, out = run(revert_cmd)
            if code != 0:
                raise TrunkError(f"git revert failed (the revert conflicts with later changes?): {out.strip()}")
            _check(run, amend, "writing the revert commit message")
            _check(run, push, "pushing the revert branch")
        except TrunkError:
            run(["git", "-C", worktree, "revert", "--abort"])
            run(["git", "worktree", "remove", "--force", worktree])
            run(["git", "branch", "-D", branch])
            raise
        code, out = run(create)
        if code != 0:
            raise TrunkError(
                f"opening the revert PR failed: {out.strip()}; {branch} is pushed from {worktree}, open it with: "
                + shlex.join(create)
            )
        print(out.strip())
        print(f"Revert worktree: {worktree}")
    except (TrunkError, ValueError, AttributeError) as exc:
        print(f"trunk-revert: refusing: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None, run: Runner = live_run) -> int:
    parser = argparse.ArgumentParser(description="Revert a merged PR on develop, or gate new PRs while trunk is red.")
    sub = parser.add_subparsers(dest="command", required=True)
    rev = sub.add_parser("revert", help="open a revert PR for a merged PR")
    rev.add_argument("--pr", type=int, required=True)
    rev.add_argument("--repo", default=REPOSITORY)
    gate = sub.add_parser("gate", help="refuse a new branch while trunk is red")
    gate.add_argument("--branch", required=True)
    gate.add_argument("--repo", default=REPOSITORY)
    arm = sub.add_parser("arm-gate", help="refuse to arm a PR while trunk is red")
    arm.add_argument("--pr", type=int)
    arm.add_argument("--branch")
    arm.add_argument("--repo", default=REPOSITORY)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        print(f"trunk: --repo must be owner/name, got {args.repo!r}", file=sys.stderr)
        return 2
    if args.command == "revert" and args.pr <= 0:
        print("trunk-revert: --pr must be a positive PR number", file=sys.stderr)
        return 2
    if args.command == "revert":
        return revert(args.pr, repo=args.repo)
    if args.command == "arm-gate":
        if (args.pr is None) == (not args.branch) or (args.pr is not None and args.pr <= 0):
            print("trunk-arm-gate: pass exactly one of --pr <positive number> or --branch", file=sys.stderr)
            return 2
        try:
            ref = args.branch or str(args.pr)
            refusal = arm_gate(ref, run, args.repo)
        except TrunkError as exc:
            print(f"trunk-arm-gate: refusing: {exc}", file=sys.stderr)
            return 1
        if refusal:
            print(f"Refusing to arm {ref}: {refusal}", file=sys.stderr)
            return 1
        return 0
    refusal = trunk_gate(args.branch, repo=args.repo)
    if refusal:
        print(f"Refusing to open PR: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
