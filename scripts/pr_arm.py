#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_landing

CLI_DESCRIPTION = "Arm a pull request for its exact head after a live check that nothing holds it."

HOLD_LABEL = pr_landing.HOLD_LABEL
REPOSITORY = "BenchBox-dev/BenchBox"
BASE_BRANCH = "develop"
VIEW_FIELDS = "number,state,isDraft,labels,reviewDecision,headRefOid,baseRefName"

Runner = Callable[[list[str]], tuple[int, str]]


def refusals(
    view: dict,
    local_head: str,
    *,
    unpublished: Sequence[str],
    unresolved_threads: bool,
    hidden_failures: Sequence[str] = (),
) -> list[str]:
    reasons: list[str] = []
    if view.get("state") != "OPEN":
        reasons.append(f"PR is {view.get('state')!r}, not OPEN")
    if view.get("isDraft"):
        reasons.append("PR is a draft")
    if view.get("baseRefName") != BASE_BRANCH:
        reasons.append(f"PR targets {view.get('baseRefName')!r}; this path arms only PRs into {BASE_BRANCH!r}")
    labels = {label.get("name") for label in view.get("labels") or []}
    if HOLD_LABEL in labels:
        reasons.append(f"durable hold label {HOLD_LABEL!r} is present; remove it deliberately to release the hold")
    if view.get("reviewDecision") == "CHANGES_REQUESTED":
        reasons.append("a reviewer requested changes")
    if unresolved_threads:
        reasons.append("an unresolved, non-outdated review thread is open")
    reasons.extend(hidden_failures)
    reasons.extend(f"unpublished work: {problem}" for problem in unpublished)
    if view.get("headRefOid") != local_head:
        reasons.append(f"pushed head {str(view.get('headRefOid'))[:9]} is not local HEAD {local_head[:9]}; push first")
    return reasons


def _current_branch_pr(run: Runner, repo: str) -> str:
    code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0 or not branch.strip() or branch.strip() == "HEAD":
        raise pr_landing.LandingError("cannot resolve the current branch; pass PR=<number>")
    code, out = run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch.strip(), "--base", BASE_BRANCH, "--state", "open"]
        + ["--json", "number"]
    )
    if code != 0:
        raise pr_landing.LandingError(f"cannot list pull requests for {branch.strip()!r}: {out.strip()}")
    numbers = [item["number"] for item in json.loads(out)]
    if len(numbers) != 1:
        raise pr_landing.LandingError(
            f"expected exactly one open PR into {BASE_BRANCH!r} from {branch.strip()!r}, found {len(numbers)}; pass PR=<number>"
        )
    return str(numbers[0])


def arm(
    pr: str | None,
    head: str | None,
    repo: str = REPOSITORY,
    run: Runner = pr_landing.live_run,
    checkout: Path = Path("."),
    unpublished: Callable[[Path], list[str]] = pr_landing.unpublished_work,
    threads: Callable[[Runner, str, int], bool] = pr_landing.unresolved_review_threads,
    suites: Callable[[Runner, str, str, str], list[str]] = pr_landing.hidden_failed_suites,
) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        print(f"pr-arm: refusing: --repo must be owner/name, got {repo!r}", file=sys.stderr)
        return 2
    if head is not None and not re.fullmatch(r"[0-9a-f]{40}", head):
        print(f"pr-arm: refusing: --head must be a full lowercase commit SHA, got {head!r}", file=sys.stderr)
        return 2
    try:
        code, out = run(["git", "rev-parse", "HEAD"])
        if code != 0:
            print(f"pr-arm: cannot read local HEAD: {out.strip()}", file=sys.stderr)
            return 1
        local_head = out.strip()
        if head is not None and head != local_head:
            print(f"pr-arm: refusing: requested head {head[:9]} is not local HEAD {local_head[:9]}", file=sys.stderr)
            return 2
        if pr is not None and not re.fullmatch(r"[1-9][0-9]*", pr):
            print(f"pr-arm: refusing: --pr must be a plain PR number, got {pr!r}", file=sys.stderr)
            return 2
        number = pr or _current_branch_pr(run, repo)
        code, out = run(["gh", "pr", "view", number, "--repo", repo, "--json", VIEW_FIELDS])
        if code != 0:
            print(f"pr-arm: cannot read the pull request: {out.strip()}", file=sys.stderr)
            return 1
        view = json.loads(out)
        reasons = refusals(
            view,
            local_head,
            unpublished=unpublished(checkout),
            unresolved_threads=threads(run, repo, int(view["number"])),
            hidden_failures=suites(run, repo, BASE_BRANCH, local_head),
        )
        if reasons:
            for reason in reasons:
                print(f"pr-arm: refusing to arm PR #{view['number']}: {reason}", file=sys.stderr)
            return 2
        code, out = run(
            [
                "gh",
                "pr",
                "merge",
                str(view["number"]),
                "--repo",
                repo,
                "--squash",
                "--auto",
                "--match-head-commit",
                local_head,
            ]
        )
        print(out.strip())
        return code
    except (pr_landing.LandingError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"pr-arm: refusing, live state could not be verified: {exc}", file=sys.stderr)
        return 1


def _environment_selection(env: Mapping[str, str]) -> dict[str, str | None]:
    return {
        name: env.get(f"PR_ARM_{name.upper()}") if env.get(f"PR_ARM_{name.upper()}_SET") == "1" else None
        for name in ("pr", "head", "repo")
    }


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    selected = _environment_selection(os.environ if env is None else env)
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--pr", default=selected["pr"], help="pull request number; default: the current branch's PR")
    parser.add_argument("--head", default=selected["head"], help="expected head SHA; refused unless it is local HEAD")
    parser.add_argument("--repo", default=selected["repo"] if selected["repo"] is not None else REPOSITORY)
    args = parser.parse_args(argv)
    return arm(args.pr, args.head, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
