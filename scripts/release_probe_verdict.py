from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scripts.release_probe_launch import (
    PENDING_PREFIX,
    PROBE_CONTEXT,
    VARIANTS,
    WORKFLOW_FILE,
    gh,
    post_status,
    run_url,
)

Gh = Callable[..., Any]
RELEASE_WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
STATUSES_PAGE = 100
MAX_PAGES = 30
PENDING = "pending"
COMPLETED = "completed"
JOBS_PAGE = 100
REPORT_JOB_NAME = "probe report"
OUTCOME_JOB_NAME = "release outcome"
NEEDS_FAILURES = frozenset({"failure", "cancelled"})
JOB_FAILURES = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure"})


@dataclass(frozen=True)
class VariantState:
    finished: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    state: str
    description: str
    target_url: str | None = None


def parse_recorded_runs(description: str | None) -> dict[str, int]:
    text = description or ""
    if not text.startswith(PENDING_PREFIX):
        return {}
    recorded: dict[str, int] = {}
    for part in text.removeprefix(PENDING_PREFIX).split(";"):
        variant, _, run_id = part.partition("=")
        if variant in VARIANTS and run_id.isdigit():
            recorded[variant] = int(run_id)
    distinct = len(set(recorded.values())) == len(VARIANTS)
    return recorded if set(recorded) == set(VARIANTS) and distinct else {}


def latest_status(repository: str, head_sha: str, runner: Gh = gh) -> dict[str, Any] | None:
    for page in range(1, MAX_PAGES + 1):
        rows = list(
            runner("api", f"repos/{repository}/commits/{head_sha}/statuses?per_page={STATUSES_PAGE}&page={page}")
        )
        for row in rows:
            if row.get("context") == PROBE_CONTEXT:
                return row
        if len(rows) < STATUSES_PAGE:
            return None
    return None


def open_runs(status: dict[str, Any] | None) -> dict[str, int]:
    if status is None or status.get("state") != PENDING:
        return {}
    return parse_recorded_runs(status.get("description"))


def is_probe_run(run: dict[str, Any], head_sha: str) -> bool:
    return (
        str(run.get("path", "")).split("@", 1)[0] == RELEASE_WORKFLOW_PATH
        and run.get("event") == "workflow_dispatch"
        and run.get("head_sha") == head_sha
    )


def run_jobs(repository: str, run_id: int, runner: Gh = gh) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        payload = runner(
            "api", f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page={JOBS_PAGE}&page={page}"
        )
        rows = list(payload.get("jobs", []))
        jobs.extend(rows)
        if len(rows) < JOBS_PAGE:
            break
    return jobs


def state_from_needs(needs: dict[str, Any]) -> VariantState:
    failures = tuple(
        f"{name}: {info.get('result')}" for name, info in sorted(needs.items()) if info.get("result") in NEEDS_FAILURES
    )
    return VariantState(True, failures)


def state_from_api(repository: str, run: dict[str, Any], runner: Gh = gh) -> VariantState:
    listed = run_jobs(repository, int(run["id"]), runner)
    jobs = [job for job in listed if job.get("name") != REPORT_JOB_NAME]
    settled = bool(jobs) and all(job.get("status") == COMPLETED for job in jobs)
    outcome_done = any(job.get("name") == OUTCOME_JOB_NAME and job.get("status") == COMPLETED for job in jobs)
    finished = run.get("status") == COMPLETED or (settled and outcome_done)
    failures = tuple(f"{job['name']}: {job['conclusion']}" for job in jobs if job.get("conclusion") in JOB_FAILURES)
    conclusion = run.get("conclusion")
    if run.get("status") == COMPLETED and conclusion != "success" and not failures:
        report_failed = any(
            job.get("name") == REPORT_JOB_NAME and job.get("conclusion") in JOB_FAILURES for job in listed
        )
        if conclusion != "failure" or not report_failed:
            failures = (f"run: {conclusion}",)
    return VariantState(finished, failures)


def judge(
    repository: str,
    recorded: dict[str, int],
    head_sha: str,
    runner: Gh = gh,
    local: dict[int, dict[str, Any]] | None = None,
) -> Verdict | None:
    local = local or {}
    runs = {variant: runner("api", f"repos/{repository}/actions/runs/{run_id}") for variant, run_id in recorded.items()}
    foreign = [variant for variant, run in runs.items() if not is_probe_run(run, head_sha)]
    if foreign:
        return Verdict(
            "failure",
            f"Probe records foreign runs, not {WORKFLOW_FILE} dispatches at {head_sha[:10]}: {', '.join(foreign)}",
            run_url(repository, recorded[foreign[0]]),
        )
    states = {
        variant: state_from_needs(local[recorded[variant]])
        if recorded[variant] in local
        else state_from_api(repository, run, runner)
        for variant, run in runs.items()
    }
    if not all(state.finished for state in states.values()):
        return None
    failed = [variant for variant, state in states.items() if state.failures]
    if failed:
        first = failed[0]
        detail = "; ".join(f"{variant}: {', '.join(states[variant].failures)}" for variant in failed)
        return Verdict(
            "failure",
            f"Probe failed: {detail}",
            str(runs[first].get("html_url") or run_url(repository, recorded[first])),
        )
    return Verdict("success", "Probe passed")


def reconcile(
    repository: str,
    recorded: dict[str, int],
    head_sha: str,
    runner: Gh = gh,
    local: dict[int, dict[str, Any]] | None = None,
) -> str:
    verdict = judge(repository, recorded, head_sha, runner, local)
    if verdict is None:
        return "waiting: probe variants still running"
    if open_runs(latest_status(repository, head_sha, runner)) != recorded:
        return "ignored: the probe status is no longer pending for these runs"
    post_status(repository, head_sha, verdict.state, verdict.description, target_url=verdict.target_url, runner=runner)
    return f"set the probe status to {verdict.state}"


def reconcile_head(repository: str, head_sha: str, runner: Gh = gh) -> str | None:
    recorded = open_runs(latest_status(repository, head_sha, runner))
    if not recorded:
        return None
    return reconcile(repository, recorded, head_sha, runner)


def report(repository: str, run_id: int, head_sha: str, needs: dict[str, Any], runner: Gh = gh) -> str:
    recorded = open_runs(latest_status(repository, head_sha, runner))
    if run_id not in recorded.values():
        return "ignored: no pending probe status records this run"
    return reconcile(repository, recorded, head_sha, runner, {run_id: needs})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the release-v2 probe status from a finished variant run.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        needs = json.loads(os.environ.get("NEEDS_JSON", ""))
        print(report(args.repository, args.run, args.head_sha, needs))
    except (RuntimeError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
