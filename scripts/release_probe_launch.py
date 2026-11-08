from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

PROBE_TAG = "v0.0.0rc0"
VARIANTS = ("reuse", "fallback")
WORKFLOW_FILE = "release-v2.yml"
RUNS_PAGE = 30
FINISHED = "completed"
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


def wait_for(
    repository: str,
    run_id: int,
    *,
    runner: Gh = gh,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = 1500.0,
    interval: float = 20.0,
) -> dict[str, Any]:
    deadline = clock() + timeout
    while True:
        run = runner("api", f"repos/{repository}/actions/runs/{run_id}")
        if run.get("status") == FINISHED:
            return run
        if clock() >= deadline:
            raise RuntimeError(f"run {run_id} did not finish within {timeout:g}s")
        sleep(interval)


def cancel(repository: str, run_id: int, runner: Gh = gh) -> None:
    try:
        runner("api", "--method", "POST", f"repos/{repository}/actions/runs/{run_id}/cancel")
    except RuntimeError as exc:
        print(f"::warning::could not cancel run {run_id}: {exc}", file=sys.stderr)


def run_probes(
    repository: str,
    branch: str,
    head_sha: str,
    *,
    runner: Gh = gh,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = 1500.0,
) -> int:
    deadline = clock() + timeout
    known = {int(run["id"]) for run in list_runs(repository, branch, runner)}
    started: dict[str, int] = {}
    verdicts: dict[str, dict[str, Any]] = {}
    try:
        for variant in VARIANTS:
            run_id = launch(repository, branch, head_sha, variant, known, runner=runner, sleep=sleep)
            known.add(run_id)
            started[variant] = run_id
            print(f"dispatched the {variant} probe as run {run_id}")
        for variant, run_id in started.items():
            remaining = max(deadline - clock(), 0.0)
            verdicts[variant] = wait_for(repository, run_id, runner=runner, sleep=sleep, clock=clock, timeout=remaining)
    except BaseException:
        for run_id in started.values():
            if run_id not in {int(run["id"]) for run in verdicts.values()}:
                cancel(repository, run_id, runner)
        raise
    failed = False
    for variant, run in verdicts.items():
        conclusion = run.get("conclusion")
        print(f"{variant} probe: {conclusion} ({run.get('html_url')})")
        failed = failed or conclusion != "success"
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run both release-v2 probe variants for a pull request head.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=1500.0)
    args = parser.parse_args(argv)
    try:
        if args.pull_request is not None:
            touched = touches_probed_paths(changed_files(args.repository, args.pull_request))
            if not touched:
                print("no release-v2 probe input changed; nothing to probe")
                return 0
            print(f"probing because of: {', '.join(touched[:10])}")
        return run_probes(args.repository, args.branch, args.head_sha, timeout=args.timeout_seconds)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
