#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trunk_revert

REPOSITORY = trunk_revert.REPOSITORY
BASE_BRANCH = trunk_revert.BASE_BRANCH
REVERT_PREFIX = trunk_revert.REVERT_PREFIX
Runner = trunk_revert.Runner
TrunkError = trunk_revert.TrunkError

LIST_FIELDS = "number,headRefName,autoMergeRequest,id"
LIST_LIMIT = 1000
DISABLE_MUTATION = (
    "mutation($pullRequestId:ID!){disablePullRequestAutoMerge"
    "(input:{pullRequestId:$pullRequestId}){pullRequest{number autoMergeRequest{enabledAt}}}}"
)


def armed_pull_requests(run: Runner, repo: str) -> list[dict]:
    code, out = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--base",
            BASE_BRANCH,
            "--state",
            "open",
            "--limit",
            str(LIST_LIMIT),
            "--json",
            LIST_FIELDS,
        ]
    )
    if code != 0:
        raise TrunkError(f"listing open {BASE_BRANCH} PRs failed: {out.strip()}")
    try:
        items = json.loads(out) if out.strip() else []
        if not isinstance(items, list):
            raise ValueError("expected a JSON array")
        armed = [
            item
            for item in items
            if isinstance(item, dict) and isinstance(item.get("number"), int) and item.get("autoMergeRequest")
        ]
        for item in armed:
            if not isinstance(item.get("id"), str) or not item["id"].strip():
                raise ValueError(f"PR #{item['number']} is missing its node id")
            if not isinstance(item.get("headRefName"), str) or not item["headRefName"].strip():
                raise ValueError(f"PR #{item['number']} is missing its head branch")
        return armed
    except (ValueError, AttributeError) as exc:
        raise TrunkError(f"unreadable open {BASE_BRANCH} PR listing: {exc}") from exc


def select_for_disarm(run: Runner, repo: str) -> tuple[list[dict], dict | None]:
    try:
        failed = trunk_revert.red_streak_start(run, repo)
    except trunk_revert.NoTrunkRunsError:
        return [], None
    if failed is None:
        return [], None
    if trunk_revert.culprit_reverted(run, repo, failed):
        return [], failed
    selected: list[dict] = []
    for item in armed_pull_requests(run, repo):
        ref = str(item["number"])
        branch = item["headRefName"]
        try:
            pr = trunk_revert.pr_head(ref, run, repo)
            refusal = trunk_revert.arm_refusal(ref, pr, failed, run, repo, reverted=False)
        except TrunkError as exc:
            if branch.startswith(REVERT_PREFIX):
                continue
            selected.append(
                {
                    "number": item["number"],
                    "branch": branch,
                    "node": item["id"],
                    "reason": f"could not evaluate PR {ref} ({exc}); disarming fail-closed",
                }
            )
            continue
        if refusal is not None:
            selected.append({"number": item["number"], "branch": branch, "node": item["id"], "reason": refusal})
    return selected, failed


def select_undetermined(run: Runner, repo: str, reason: str) -> list[dict]:
    return [
        {
            "number": item["number"],
            "branch": item["headRefName"],
            "node": item["id"],
            "reason": f"{reason}; disarming fail-closed",
        }
        for item in armed_pull_requests(run, repo)
        if not item["headRefName"].startswith(REVERT_PREFIX)
    ]


def disarm_undetermined(run: Runner, repo: str, reason: str) -> int:
    selected = select_undetermined(run, repo, reason)
    failures: list[int] = []
    for item in selected:
        try:
            disable_auto_merge(run, repo, item["number"], item["node"])
            print(f"disarmed PR #{item['number']} ({item['branch']}): {item['reason']}")
        except TrunkError as exc:
            print(f"trunk-red-disarm: PR #{item['number']}: {exc}", file=sys.stderr)
            failures.append(item["number"])
    if failures:
        print(f"trunk-red-disarm: failed to disarm {len(failures)} PR(s): {failures}", file=sys.stderr)
        return 1
    print(f"{BASE_BRANCH} is red with an undetermined culprit; disarmed {len(selected)} PR(s)")
    return 0


def read_pr_state(run: Runner, repo: str, number: int) -> dict | None:
    code, out = run(["gh", "pr", "view", str(number), "--repo", repo, "--json", "number,state,autoMergeRequest"])
    if code != 0:
        return None
    try:
        view = json.loads(out) if out.strip() else None
        return view if isinstance(view, dict) else None
    except ValueError:
        return None


def disable_auto_merge(run: Runner, repo: str, number: int, node: str) -> None:
    code, out = run(["gh", "api", "graphql", "-F", f"pullRequestId={node}", "-f", f"query={DISABLE_MUTATION}"])
    if code == 0:
        reverified = read_pr_state(run, repo, number)
        if reverified is None:
            raise TrunkError(f"could not re-read PR #{number} after disabling auto-merge")
        if reverified.get("autoMergeRequest"):
            raise TrunkError(f"auto-merge is still armed on PR #{number} after disable")
        return
    settled = read_pr_state(run, repo, number)
    if settled is not None and (settled.get("state") != "OPEN" or not settled.get("autoMergeRequest")):
        return
    raise TrunkError(f"could not disable auto-merge on PR #{number}: {out.strip()[:200]}")


def disarm_all(run: Runner = trunk_revert.live_run, repo: str = REPOSITORY) -> int:
    try:
        selected, failed = select_for_disarm(run, repo)
    except trunk_revert.UndeterminedCulpritError as exc:
        try:
            return disarm_undetermined(run, repo, str(exc))
        except TrunkError as inner:
            print(f"trunk-red-disarm: could not list armed PRs ({inner}); disarming nothing", file=sys.stderr)
            return 1
    except TrunkError as exc:
        print(f"trunk-red-disarm: could not establish the red streak ({exc}); disarming nothing", file=sys.stderr)
        return 1
    if failed is None:
        print(f"{BASE_BRANCH} is green or its culprit is reverted; disarming nothing")
        return 0
    named, _ = trunk_revert.describe_culprit(run, repo, failed)
    run_url = failed.get("url") or ""
    if not selected:
        print(f"{BASE_BRANCH} is red at {named} ({run_url}); no armed PR needs disarming")
        return 0
    failures: list[int] = []
    for item in selected:
        try:
            disable_auto_merge(run, repo, item["number"], item["node"])
            print(f"disarmed PR #{item['number']} ({item['branch']}) while {named} ({run_url}): {item['reason']}")
        except TrunkError as exc:
            print(f"trunk-red-disarm: PR #{item['number']}: {exc}", file=sys.stderr)
            failures.append(item["number"])
    if failures:
        print(f"trunk-red-disarm: failed to disarm {len(failures)} PR(s): {failures}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None, run: Runner = trunk_revert.live_run) -> int:
    parser = argparse.ArgumentParser(description="Disarm auto-merge on armed PRs while trunk is red.")
    parser.add_argument("--repo", default=REPOSITORY)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        print(f"trunk-red-disarm: --repo must be owner/name, got {args.repo!r}", file=sys.stderr)
        return 2
    return disarm_all(run, repo=args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
