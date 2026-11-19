from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

PROBE_TAG = "v0.0.0rc0"
VARIANTS = ("reuse", "fallback", "guarded-change")
WORKFLOW_FILE = "release-v2.yml"
RUNS_PAGE = 30
PROBE_CONTEXT = "release-v2 probe"
PENDING_PREFIX = "Probe running: "
DESCRIPTION_LIMIT = 140
PROBED_PATHS = (
    ".github/workflows/release-v2.yml",
    ".github/workflows/release-v2-probe.yml",
    ".github/workflows/trunk-uat.yml",
    ".github/workflows/trunk.yml",
    ".github/actions/release-uat-env/*",
    "scripts/release_*.py",
    "scripts/release_requirements.txt",
    "scripts/update_version.py",
    "tests/uat/gate_summary.py",
    "tests/uat/configs/release-gate-*.yaml",
    "tests/uat/_cli.py",
    "tests/uat/config.py",
    "Makefile",
    "make/uat.mk",
)
Gh = Callable[..., Any]
GH_EXECUTABLE = "gh"


def gh(*args: str) -> Any:
    result = subprocess.run([GH_EXECUTABLE, *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    if args[0] != "api" or not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def touches_probed_paths(files: list[str]) -> list[str]:
    return [name for name in files if any(fnmatch.fnmatchcase(name, pattern) for pattern in PROBED_PATHS)]


def changed_files(repository: str, pull_request: int, runner: Gh = gh) -> list[str]:
    files: list[str] = []
    for page in range(1, 31):
        rows = runner("api", f"repos/{repository}/pulls/{pull_request}/files?per_page=100&page={page}")
        for row in rows:
            files.append(str(row["filename"]))
            previous = row.get("previous_filename")
            if previous:
                files.append(str(previous))
        if len(rows) < 100:
            return files
    raise RuntimeError(f"pull request {pull_request} lists more than 3000 changed files")


def dispatch(repository: str, branch: str, variant: str, runner: Gh = gh) -> None:
    runner(
        "workflow",
        "run",
        WORKFLOW_FILE,
        "--repo",
        repository,
        "--ref",
        branch,
        "-f",
        f"tag={PROBE_TAG}",
        "-f",
        f"probe={variant}",
    )


def list_runs(repository: str, branch: str, runner: Gh = gh) -> list[dict[str, Any]]:
    payload = runner(
        "api",
        f"repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page={RUNS_PAGE}"
        f"&branch={branch}",
    )
    return list(payload.get("workflow_runs", []))


def find_new_run(runs: list[dict[str, Any]], known: set[int], head_sha: str) -> dict[str, Any] | None:
    fresh = [run for run in runs if int(run["id"]) not in known and run.get("head_sha") == head_sha]
    return min(fresh, key=lambda run: int(run["id"])) if fresh else None


def launch(
    repository: str,
    branch: str,
    head_sha: str,
    variant: str,
    known: set[int],
    *,
    runner: Gh = gh,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 30,
    interval: float = 5.0,
) -> int:
    dispatch(repository, branch, variant, runner)
    for _ in range(attempts):
        run = find_new_run(list_runs(repository, branch, runner), known, head_sha)
        if run is not None:
            return int(run["id"])
        sleep(interval)
    raise RuntimeError(
        f"no {WORKFLOW_FILE} run at {head_sha[:10]} appeared for the {variant} probe; "
        "the pull request may have moved after the dispatch"
    )


def cancel(repository: str, run_id: int, runner: Gh = gh) -> None:
    try:
        runner("api", "--method", "POST", f"repos/{repository}/actions/runs/{run_id}/cancel")
    except RuntimeError as exc:
        print(f"::warning::could not cancel run {run_id}: {exc}", file=sys.stderr)


def pending_description(started: dict[str, int]) -> str:
    return PENDING_PREFIX + ";".join(f"{variant}={run_id}" for variant, run_id in started.items())


def run_url(repository: str, run_id: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def clip(description: str) -> str:
    if len(description) <= DESCRIPTION_LIMIT:
        return description
    return description[: DESCRIPTION_LIMIT - 1] + "…"


def post_status(
    repository: str,
    head_sha: str,
    state: str,
    description: str,
    *,
    target_url: str | None = None,
    runner: Gh = gh,
) -> dict[str, Any]:
    args = [
        "api",
        "--method",
        "POST",
        f"repos/{repository}/statuses/{head_sha}",
        "-f",
        f"state={state}",
        "-f",
        f"context={PROBE_CONTEXT}",
        "-f",
        f"description={clip(description)}",
    ]
    if target_url is not None:
        args += ["-f", f"target_url={target_url}"]
    return runner(*args)


def start_probes(
    repository: str,
    branch: str,
    head_sha: str,
    *,
    runner: Gh = gh,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    known = {int(run["id"]) for run in list_runs(repository, branch, runner)}
    started: dict[str, int] = {}
    try:
        for variant in VARIANTS:
            run_id = launch(repository, branch, head_sha, variant, known, runner=runner, sleep=sleep)
            known.add(run_id)
            started[variant] = run_id
            print(f"dispatched the {variant} probe as run {run_id}")
    except BaseException:
        for run_id in started.values():
            cancel(repository, run_id, runner)
        raise
    return started


def report_probe(
    repository: str,
    branch: str,
    head_sha: str,
    files: list[str],
    *,
    runner: Gh = gh,
    sleep: Callable[[float], None] = time.sleep,
    reconcile: Callable[[dict[str, int]], None] | None = None,
    existing: Callable[[], str | None] | None = None,
    dispatch_url: str | None = None,
) -> int:
    touched = touches_probed_paths(files)
    if not touched:
        print("no release-v2 probe input changed; nothing to probe")
        post_status(repository, head_sha, "success", "No release input changed", runner=runner)
        return 0
    if existing is not None:
        outcome = existing()
        if outcome is not None:
            print(outcome)
            return 0
    print(f"probing because of: {', '.join(touched[:10])}")
    try:
        started = start_probes(repository, branch, head_sha, runner=runner, sleep=sleep)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        post_status(repository, head_sha, "failure", f"Probe dispatch failed: {exc}", runner=runner)
        return 1
    post_status(
        repository,
        head_sha,
        "pending",
        pending_description(started),
        target_url=dispatch_url or run_url(repository, next(iter(started.values()))),
        runner=runner,
    )
    if reconcile is not None:
        try:
            reconcile(started)
        except RuntimeError as exc:
            print(f"::warning::could not reconcile the probe status yet: {exc}", file=sys.stderr)
    return 0


def fail_probe(repository: str, head_sha: str, message: str, runner: Gh = gh) -> None:
    post_status(repository, head_sha, "failure", f"Probe could not start: {message}", runner=runner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch every release-v2 probe variant for a pull request head.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--branch")
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--fail", help="Record a failed probe status with this message instead of dispatching")
    args = parser.parse_args(argv)
    try:
        if args.fail is not None:
            fail_probe(args.repository, args.head_sha, args.fail)
            return 0
        if args.branch is None or args.pull_request is None:
            parser.error("--branch and --pull-request are required unless --fail is given")
        files = changed_files(args.repository, args.pull_request)
        from scripts import release_probe_verdict

        def reconcile(started: dict[str, int]) -> None:
            print(release_probe_verdict.reconcile(args.repository, started, args.head_sha))

        def existing() -> str | None:
            return release_probe_verdict.reconcile_head(args.repository, args.head_sha)

        dispatch_run = os.environ.get("GITHUB_RUN_ID", "")
        return report_probe(
            args.repository,
            args.branch,
            args.head_sha,
            files,
            reconcile=reconcile,
            existing=existing,
            dispatch_url=run_url(args.repository, int(dispatch_run)) if dispatch_run.isdigit() else None,
        )
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        try:
            fail_probe(args.repository, args.head_sha, str(exc))
        except RuntimeError as inner:
            print(f"::error::{inner}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
