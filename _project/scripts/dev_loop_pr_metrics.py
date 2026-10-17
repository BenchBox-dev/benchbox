#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "BenchBox-dev/BenchBox"
FAST_LANE_POLICY_PATH = "_project/config/fast_test_lane_policy.json"
REQUIRED_LANE_WORKFLOW_NAMES = ("CI", "Develop PR")
FAST_TEST_JOB_NAME = "test (ubuntu-latest, 3.12)"
MEDIUM_TEST_JOB_NAME = "medium-test"
MEDIUM_SHARD_JOB_NAMES = tuple(f"medium-test (shard {index})" for index in range(4))
API_RETRY_ATTEMPTS = 3
EVENT_FANOUT_SCHEMA = "event_fanout_v1"
LIFECYCLE_SCHEMA = "ci_lifecycle_baseline_v1"
REFRESH_AUDIT_SCHEMA = "refresh_audit_v1"
PROCESS_ACCEPTANCE_SCHEMA = "pr_process_acceptance_v1"
DEFAULT_ACCEPTANCE_PUBLISHED_REF = "origin/develop"
BATCH_DELIVERY_RECEIPT_SCHEMA = "batch_delivery_receipt_v1"
REFRESH_REASON_TIMING = "prior_check_not_success"
REFRESH_REASON_IDENTITY = "prior_check_unbound"
REQUIRED_CONTEXT_NAMES: tuple[str, ...] = (
    "core",
    "explorer",
    "results-data",
    "docs",
    "landing",
    "tooling",
)
LEGACY_REQUIRED_CONTEXT_NAMES: tuple[str, ...] = (
    "ci-required-result",
    "tooling",
    "Results Explorer browser gate",
    "ruleset-drift",
    "Public-site visual acceptance",
)
SYNCHRONIZE_WORKFLOW_NAMES: tuple[str, ...] = (
    "CI",
    "Auto-merge revocation",
    "Develop PR",
    "Results Explorer browser tests",
    "Develop ruleset drift",
    "Documentation",
    "PR base guard",
    "Site and docs",
)


CLI_DESCRIPTION = (
    "Per-PR dev-loop CI-failure baseline metrics for merged `develop` PRs.\n"
    "\n"
    "Dev PRs land via squash auto-merge once the required checks are green, so a\n"
    "merged PR's HEAD-SHA check runs are *always* green (see\n"
    "`_project/scripts/detect_orphaned_commits.py`'s module docstring for the\n"
    "related squash-merge mechanics). That makes head-SHA check-run status useless\n"
    "as a CI-failure signal. The real failure cost shows up upstream of the merge:\n"
    "fix-forward pushes after a PR is opened, PRs that touch the shared fast-test\n"
    "guard file (`_project/config/fast_test_lane_policy.json`, a frequent\n"
    "composition-conflict hotspot), and whether the PR's *first* required-lane\n"
    'workflow run ("CI", `.github/workflows/ci.yml`; "Develop PR", the retired\n'
    "`pr.yml`, for older PRs) went green\n"
    "without a fix-forward push.\n"
    "\n"
    "This script computes, per merged `develop` PR in a trailing window:\n"
    "\n"
    "  - pushes_after_open: commits on the PR after its first commit (a proxy for\n"
    '    fix-forward pushes; the initial push that opened the PR is not a "push\n'
    '    after open").\n'
    "  - open_to_merge_seconds: PR created_at -> merged_at wall time.\n"
    "  - touched_fast_test_lane_policy: whether the PR's file list includes\n"
    "    _project/config/fast_test_lane_policy.json.\n"
    '  - first_pass_green: whether the PR\'s FIRST required-lane ("CI" or, for older\n'
    '    PRs, "Develop PR") workflow run on its head branch concluded "success" -- i.e. the\n'
    "    required lane went green without a fix-forward push. Uses per-branch\n"
    "    Actions run history (event=pull_request), not the always-green head-SHA\n"
    "    check runs.\n"
    '  - fast_test_job_seconds: wall time of the "test (ubuntu-latest, 3.12)" job\n'
    '    within that first "Develop PR" run (used to compute the window\'s average\n'
    "    / p95 fast-test job wall time).\n"
    "\n"
    "Runtime data source, in order of preference: the `gh` CLI (`gh api ...`) when\n"
    "on PATH; otherwise a raw GitHub REST call via `urllib` authenticated with\n"
    '$GITHUB_TOKEN / $GH_TOKEN; otherwise this prints a "SKIPPED: no GitHub API\n'
    'access" notice and exits 0 (mirrors the `|| true` graceful-degradation\n'
    "pattern the existing `dev-loop-metrics` Make target uses for `gh run list`,\n"
    "Makefile ~line 1520). This script performs read-only GitHub API calls; it\n"
    "never mutates PRs, branches, or workflow state.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py --days 28 --json\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py --collect-durations\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py --validate-lifecycle-baseline LIFECYCLE --process-baseline PROCESS\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py --validate-refresh-audit AUDIT --baseline LIFECYCLE\n"
    "    uv run -- python _project/scripts/dev_loop_pr_metrics.py --validate-process-acceptance ACCEPTANCE --process-baseline PROCESS\n"
    "\n"
    "--collect-durations is a separate, local-machine-only mode: it runs the fast\n"
    "test lane under `pytest --durations=20` and prints the slowest tests. It does\n"
    "not touch the GitHub API and is not part of the default run (see the module\n"
    "TODO context: dev-loop-metrics-ci-failure-baseline-2).\n"
)


def resolve_required_contexts(check_runs: list[dict]) -> tuple[str, ...]:

    if any(run.get("name") == "core" for run in check_runs):
        return REQUIRED_CONTEXT_NAMES
    return LEGACY_REQUIRED_CONTEXT_NAMES


PUBLIC_STANDARD_RUNNER_USD = 0.0
_SETUP_STEP_PREFIXES: tuple[str, ...] = (
    "Set up job",
    "Checkout",
    "Set up Python",
    "Set up Node",
    "Install uv",
    "Install dependencies",
    "Install Python dependencies",
    "Install ",
    "Post ",
    "Complete job",
)


@dataclass
class PrMetrics:
    number: int
    title: str
    head_ref: str
    created_at: str
    merged_at: str
    open_to_merge_seconds: float
    pushes_after_open: int
    touched_fast_test_lane_policy: bool
    first_pass_green: bool | None
    fast_test_job_seconds: float | None
    medium_test_job_seconds: float | None
    event_fanout: dict | None = None


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_api(path: str) -> object | None:
    for attempt in range(API_RETRY_ATTEMPTS):
        try:
            proc = subprocess.run(
                ["gh", "api", path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None

        if proc is not None and proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass

        if attempt + 1 < API_RETRY_ATTEMPTS:
            time.sleep(0.5 * (attempt + 1))
    return None


def _urllib_api(path: str, token: str) -> object | None:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "benchbox-dev-loop-pr-metrics",
        },
    )
    for attempt in range(API_RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                try:
                    return json.load(resp)
                except ValueError:
                    return None
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if attempt == API_RETRY_ATTEMPTS - 1:
                return None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == API_RETRY_ATTEMPTS - 1:
                return None
    return None


class ApiFailure(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, use_gh: bool, token: str | None) -> None:
        self.repo = repo
        self.use_gh = use_gh
        self.token = token

    def get(self, path: str) -> object | None:
        if self.use_gh:
            return _gh_api(path)
        if self.token:
            return _urllib_api(path, self.token)
        return None

    def get_paginated(
        self,
        path: str,
        item_key: str | None = None,
        stop: Callable[[dict], bool] | None = None,
    ) -> list[dict]:
        items: list[dict] = []
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            data = self.get(f"{path}{sep}per_page=100&page={page}")
            if data is None:
                raise ApiFailure(f"GitHub API page fetch failed: {path} (page {page})")
            batch = data.get(item_key, []) if item_key else data
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if stop is not None and any(stop(item) for item in batch):
                break
            if len(batch) < 100:
                break
            page += 1
        return items


def _gh_auth_token() -> str | None:
    if not _gh_available():
        return None
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = proc.stdout.strip() if proc.returncode == 0 else ""
    return token or None


def make_client(repo: str) -> GitHubClient | None:
    if _gh_available():
        gh_token = _gh_auth_token()
        if gh_token:
            probe = _urllib_api(f"/repos/{repo}", gh_token)
            if probe is not None:
                return GitHubClient(repo, use_gh=False, token=gh_token)
        probe = _gh_api(f"/repos/{repo}")
        if probe is not None:
            return GitHubClient(repo, use_gh=True, token=None)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        probe = _urllib_api(f"/repos/{repo}", token)
        if probe is not None:
            return GitHubClient(repo, use_gh=False, token=token)
    return None


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_merged_prs(client: GitHubClient, since: datetime) -> list[dict]:
    prs = client.get_paginated(
        f"/repos/{client.repo}/pulls?state=closed&base=develop&sort=updated&direction=desc",
        stop=lambda pr: _iso_to_dt(pr["updated_at"]) < since,
    )
    merged: list[dict] = []
    for pr in prs:
        merged_at = pr.get("merged_at")
        if not merged_at:
            continue
        if _iso_to_dt(merged_at) < since:
            if _iso_to_dt(pr["updated_at"]) < since:
                break
            continue
        merged.append(pr)
    return merged


def pushes_after_open(client: GitHubClient, number: int, created_at: str) -> int:
    commits = client.get_paginated(f"/repos/{client.repo}/pulls/{number}/commits")
    opened = _iso_to_dt(created_at)
    after = 0
    for entry in commits:
        commit = entry.get("commit") or {}
        stamp = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
        if not stamp:
            continue
        try:
            if _iso_to_dt(stamp) > opened:
                after += 1
        except (TypeError, ValueError):
            continue
    return after


def touched_fast_test_lane_policy(client: GitHubClient, number: int) -> bool:
    files = client.get_paginated(f"/repos/{client.repo}/pulls/{number}/files")
    return any(f.get("filename") == FAST_LANE_POLICY_PATH for f in files)


def _partitioned_medium_seconds(jobs: list[dict]) -> float | None:
    names = ("medium-collect", *MEDIUM_SHARD_JOB_NAMES)
    selected = [job for job in jobs if job.get("name") in names or str(job.get("name")).startswith("medium-test (")]
    if len(selected) != len(names) or {job.get("name") for job in selected} != set(names):
        return None
    intervals = {}
    for job in selected:
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            return None
        if not isinstance(job.get("started_at"), str) or not isinstance(job.get("completed_at"), str):
            return None
        try:
            started = _iso_to_dt(job["started_at"])
            completed = _iso_to_dt(job["completed_at"])
            if completed < started:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        intervals[job["name"]] = (started, completed)
    collector_start, collector_end = intervals["medium-collect"]
    try:
        if any(intervals[name][0] < collector_end for name in MEDIUM_SHARD_JOB_NAMES):
            return None
        return (max(intervals[name][1] for name in MEDIUM_SHARD_JOB_NAMES) - collector_start).total_seconds()
    except TypeError:
        return None


def first_pass_green_and_job_seconds(
    client: GitHubClient, head_ref: str
) -> tuple[bool | None, float | None, float | None]:
    runs = client.get_paginated(
        f"/repos/{client.repo}/actions/runs?branch={head_ref}&event=pull_request",
        item_key="workflow_runs",
    )
    candidates = [r for r in runs if r.get("name") in REQUIRED_LANE_WORKFLOW_NAMES]
    if not candidates:
        return None, None, None
    first_run = min(candidates, key=lambda r: _iso_to_dt(r["created_at"]))
    green = first_run.get("conclusion") == "success"

    seconds_by_job: dict[str, float] = {}
    jobs = client.get_paginated(f"/repos/{client.repo}/actions/runs/{first_run['id']}/jobs", item_key="jobs")
    for job in jobs:
        name = job.get("name")
        if (
            name in (FAST_TEST_JOB_NAME, MEDIUM_TEST_JOB_NAME)
            and job.get("conclusion") == "success"
            and job.get("started_at")
            and job.get("completed_at")
        ):
            started = _iso_to_dt(job["started_at"])
            completed = _iso_to_dt(job["completed_at"])
            seconds_by_job.setdefault(name, (completed - started).total_seconds())
    medium_seconds = seconds_by_job.get(MEDIUM_TEST_JOB_NAME)
    if any(str(job.get("name")).startswith("medium-test (") or job.get("name") == "medium-collect" for job in jobs):
        medium_seconds = _partitioned_medium_seconds(jobs)
    return green, seconds_by_job.get(FAST_TEST_JOB_NAME), medium_seconds


def event_fanout_for_pr(client: GitHubClient, pr: dict) -> dict:

    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if not head_sha:
        return event_fanout_metrics(runs=[], jobs=[], check_runs=[], merged_at=pr.get("merged_at"))
    runs = client.get_paginated(
        f"/repos/{client.repo}/actions/runs?head_sha={head_sha}",
        item_key="workflow_runs",
    )
    jobs: list[dict] = []
    for run in runs:
        jobs.extend(client.get_paginated(f"/repos/{client.repo}/actions/runs/{run['id']}/jobs", item_key="jobs"))
    check_runs = client.get_paginated(
        f"/repos/{client.repo}/commits/{head_sha}/check-runs",
        item_key="check_runs",
    )
    return event_fanout_metrics(runs=runs, jobs=jobs, check_runs=check_runs, merged_at=pr.get("merged_at"))


def collect_pr_metrics(client: GitHubClient, pr: dict, *, include_event_fanout: bool = False) -> PrMetrics:
    number = pr["number"]
    head_ref = pr["head"]["ref"]
    created_at = pr["created_at"]
    merged_at = pr["merged_at"]
    open_to_merge = (_iso_to_dt(merged_at) - _iso_to_dt(created_at)).total_seconds()
    first_pass_green, fast_test_seconds, medium_test_seconds = first_pass_green_and_job_seconds(client, head_ref)
    fanout = event_fanout_for_pr(client, pr) if include_event_fanout else None
    return PrMetrics(
        number=number,
        title=pr.get("title", ""),
        head_ref=head_ref,
        created_at=created_at,
        merged_at=merged_at,
        open_to_merge_seconds=open_to_merge,
        pushes_after_open=pushes_after_open(client, number, created_at),
        touched_fast_test_lane_policy=touched_fast_test_lane_policy(client, number),
        first_pass_green=first_pass_green,
        fast_test_job_seconds=fast_test_seconds,
        medium_test_job_seconds=medium_test_seconds,
        event_fanout=fanout,
    )


def _pct(values: list[float], p: int) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, -(-len(s) * p // 100) - 1))
    return s[idx]


MEDIUM_TEST_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
_MEDIUM_TEST_TIMEOUT_RE = re.compile(r"(?ms)^  medium-test:\s*$.*?^\s{4}timeout-minutes:\s*(\d+)\s*$")
MEDIUM_TEST_BUDGET_WARN_FRACTION = 0.75


def _medium_test_timeout_minutes() -> int:
    workflow = MEDIUM_TEST_WORKFLOW_PATH.read_text(encoding="utf-8")
    match = _MEDIUM_TEST_TIMEOUT_RE.search(workflow)
    if match is None:
        raise ValueError(f"Could not find medium-test timeout in {MEDIUM_TEST_WORKFLOW_PATH}")
    return int(match.group(1))


def _elapsed_seconds(started_at: object, completed_at: object) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        return (_iso_to_dt(str(completed_at)) - _iso_to_dt(str(started_at))).total_seconds()
    except (TypeError, ValueError):
        return None


def latest_named_check_runs(check_runs: list[dict], name: str) -> dict | None:

    matches = [run for run in check_runs if run.get("name") == name]
    if not matches:
        return None

    def _key(run: dict) -> tuple[int, datetime]:
        raw = run.get("started_at")
        if not raw:
            return (0, datetime.min.replace(tzinfo=timezone.utc))
        try:
            return (1, _iso_to_dt(str(raw)))
        except (TypeError, ValueError):
            return (0, datetime.min.replace(tzinfo=timezone.utc))

    return max(matches, key=_key)


def required_gate_seconds(
    check_runs: list[dict],
    required: tuple[str, ...] | None = None,
) -> float | None:

    required = required or resolve_required_contexts(check_runs)
    latest: list[dict] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    for name in required:
        run = latest_named_check_runs(check_runs, name)
        if run is None:
            return None
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            return None
        started = run.get("started_at")
        completed = run.get("completed_at")
        if not started or not completed:
            return None
        try:
            starts.append(_iso_to_dt(str(started)))
            ends.append(_iso_to_dt(str(completed)))
        except (TypeError, ValueError):
            return None
        latest.append(run)
    if not starts:
        return None
    return (max(ends) - min(starts)).total_seconds()


def merge_unblock_seconds(
    check_runs: list[dict],
    required: tuple[str, ...] | None = None,
) -> float | None:

    return required_gate_seconds(check_runs, required)


def queue_delay_seconds(required_gate_end: datetime | None, merged_at: str | None) -> float | None:

    if required_gate_end is None or not merged_at:
        return None
    try:
        delay = (_iso_to_dt(merged_at) - required_gate_end).total_seconds()
    except (TypeError, ValueError):
        return None
    return delay if delay >= 0 else None


def required_gate_end(check_runs: list[dict], required: tuple[str, ...] | None = None) -> datetime | None:
    required = required or resolve_required_contexts(check_runs)
    ends: list[datetime] = []
    for name in required:
        run = latest_named_check_runs(check_runs, name)
        if run is None or run.get("conclusion") != "success" or not run.get("completed_at"):
            return None
        try:
            ends.append(_iso_to_dt(str(run["completed_at"])))
        except (TypeError, ValueError):
            return None
    return max(ends) if ends else None


def all_workflow_seconds(runs: list[dict]) -> float | None:

    starts: list[datetime] = []
    ends: list[datetime] = []
    for run in runs:
        started = run.get("run_started_at") or run.get("created_at")
        completed = run.get("updated_at")
        if run.get("status") not in {None, "completed"}:
            return None
        if not started or not completed:
            return None
        try:
            starts.append(_iso_to_dt(str(started)))
            ends.append(_iso_to_dt(str(completed)))
        except (TypeError, ValueError):
            return None
    if not starts:
        return None
    return (max(ends) - min(starts)).total_seconds()


def _is_setup_step(name: object) -> bool:
    text = str(name or "")
    return any(text.startswith(prefix) for prefix in _SETUP_STEP_PREFIXES)


def job_setup_execution_seconds(job: dict) -> tuple[float | None, float | None, float | None]:

    if job.get("conclusion") != "success" or job.get("status") not in {None, "completed"}:
        return None, None, None
    total = _elapsed_seconds(job.get("started_at"), job.get("completed_at"))
    steps = job.get("steps") or []
    setup = 0.0
    execution = 0.0
    saw_timed_step = False
    for step in steps:
        elapsed = _elapsed_seconds(step.get("started_at"), step.get("completed_at"))
        if elapsed is None:
            continue
        saw_timed_step = True
        if _is_setup_step(step.get("name")):
            setup += elapsed
        else:
            execution += elapsed
    if not saw_timed_step:
        return None, None, total
    return setup, execution, total


def runner_minute_report(jobs: list[dict]) -> dict[str, float | int]:

    completed = 0.0
    cancelled = 0.0
    failed = 0.0
    cancelled_count = 0
    failed_count = 0
    incomplete_count = 0
    setup = 0.0
    execution = 0.0
    for job in jobs:
        conclusion = job.get("conclusion")
        if conclusion == "cancelled":
            cancelled_count += 1
            elapsed = _elapsed_seconds(job.get("started_at"), job.get("completed_at"))
            if elapsed is not None:
                cancelled += elapsed / 60.0
            continue
        if conclusion in ("failure", "timed_out"):
            failed_count += 1
            elapsed = _elapsed_seconds(job.get("started_at"), job.get("completed_at"))
            if elapsed is not None:
                failed += elapsed / 60.0
            else:
                incomplete_count += 1
            continue
        if conclusion != "success":
            incomplete_count += 1
            continue
        setup_s, exec_s, total_s = job_setup_execution_seconds(job)
        if total_s is None:
            incomplete_count += 1
            continue
        completed += total_s / 60.0
        if setup_s is not None:
            setup += setup_s / 60.0
        if exec_s is not None:
            execution += exec_s / 60.0
    return {
        "completed_runner_minutes": completed,
        "cancelled_runner_minutes": cancelled,
        "failed_runner_minutes": failed,
        "cancelled_job_count": cancelled_count,
        "failed_job_count": failed_count,
        "incomplete_job_count": incomplete_count,
        "setup_runner_minutes": setup,
        "execution_runner_minutes": execution,
        "public_standard_runner_usd": PUBLIC_STANDARD_RUNNER_USD,
    }


def correlate_runs_by_head(runs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for run in runs:
        head = str(run.get("head_sha") or "")
        if not head:
            continue
        grouped.setdefault(head, []).append(run)
    return grouped


def event_fanout_metrics(
    *,
    runs: list[dict],
    jobs: list[dict],
    check_runs: list[dict],
    merged_at: str | None = None,
    required: tuple[str, ...] | None = None,
) -> dict:

    gate = required_gate_seconds(check_runs, required)
    unblock = merge_unblock_seconds(check_runs, required)
    gate_end = required_gate_end(check_runs, required)
    by_workflow: dict[str, int] = {}
    for run in runs:
        name = str(run.get("name") or "unknown")
        by_workflow[name] = by_workflow.get(name, 0) + 1
    report = runner_minute_report(jobs)
    return {
        "schema": EVENT_FANOUT_SCHEMA,
        "required_gate_seconds": gate,
        "merge_unblock_seconds": unblock,
        "all_workflow_seconds": all_workflow_seconds(runs),
        "queue_delay_seconds": queue_delay_seconds(gate_end, merged_at),
        "workflow_run_counts": by_workflow,
        "retrieval_ids": retrieval_identities(runs, jobs, check_runs),
        **report,
    }


def retrieval_identities(runs: list[dict], jobs: list[dict], check_runs: list[dict]) -> dict[str, list[dict]]:
    return {
        "runs": [
            {"id": run.get("id"), "attempt": run.get("run_attempt"), "name": str(run.get("name") or "")}
            for run in runs
            if isinstance(run, dict)
        ],
        "jobs": [
            {
                "id": job.get("id"),
                "name": str(job.get("name") or ""),
                "conclusion": job.get("conclusion"),
            }
            for job in jobs
            if isinstance(job, dict)
        ],
        "checks": [
            {
                "id": check.get("id"),
                "name": str(check.get("name") or ""),
                "conclusion": check.get("conclusion"),
            }
            for check in check_runs
            if isinstance(check, dict)
        ],
    }


def fetch_cohort_pr_numbers(client: GitHubClient, since_iso: str, until_iso: str) -> list[int]:

    import urllib.parse

    raw_query = f"repo:{client.repo} is:pr is:merged base:develop merged:{since_iso[:10]}..{until_iso[:10]}"
    query = urllib.parse.quote(raw_query, safe="")
    first = client.get(f"/search/issues?q={query}&per_page=1")
    if not isinstance(first, dict) or not isinstance(first.get("total_count"), int):
        raise ApiFailure(f"cohort search failed: {query}")
    items = client.get_paginated(f"/search/issues?q={query}", item_key="items")
    numbers = [int(item["number"]) for item in items if isinstance(item, dict) and item.get("number")]
    if len(numbers) < int(first["total_count"]):
        raise ApiFailure(
            f"cohort search truncated: {len(numbers)} of {first['total_count']} "
            "(1000-result search cap or failed page; narrow the window instead of understating)"
        )
    return numbers


def pr_history_commits(client: GitHubClient, number: int) -> list[dict]:
    return client.get_paginated(f"/repos/{client.repo}/pulls/{number}/commits")


def pr_nontip_shas(commits: list[dict]) -> set[str]:
    by_sha = {str(c.get("sha") or ""): c for c in commits if isinstance(c, dict)}
    children: set[str] = set()
    for commit in by_sha.values():
        parents = commit.get("parents") or []
        for parent in parents:
            if isinstance(parent, dict) and str(parent.get("sha") or "") in by_sha:
                children.add(str(parent.get("sha")))
    return children


def pr_synchronize_heads(client: GitHubClient, number: int) -> list[str]:

    heads: list[str] = []
    for commit in pr_history_commits(client, number):
        sha = str(commit.get("sha") or "") if isinstance(commit, dict) else ""
        if sha and sha not in heads:
            heads.append(sha)
    return heads


def lifecycle_for_pr(client: GitHubClient, pr: dict) -> dict:

    number = pr["number"]
    commits = pr_history_commits(client, number)
    heads = []
    for commit in commits:
        sha = str(commit.get("sha") or "") if isinstance(commit, dict) else ""
        if sha and sha not in heads:
            heads.append(sha)
    nontips = pr_nontip_shas(commits)
    final_sha = str((pr.get("head") or {}).get("sha") or "")
    per_head: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    missing: list[dict] = []
    for sha in heads:
        runs = client.get_paginated(
            f"/repos/{client.repo}/actions/runs?head_sha={sha}",
            item_key="workflow_runs",
        )
        if not runs:
            if sha in nontips:
                missing.append(
                    {
                        "head_sha": sha,
                        "status": "non-tip-commit",
                        "reason": "intra-push commit with a child in the same history; "
                        "only the push tip ever ran CI, so this SHA was never a head",
                    }
                )
            else:
                missing.append(
                    {
                        "head_sha": sha,
                        "status": "missing-artifact",
                        "reason": "no observable workflow runs for head (expired retention or never scheduled)",
                    }
                )
            continue
        jobs: list[dict] = []
        head_attempts = 0
        for run in runs:
            jobs.extend(
                client.get_paginated(
                    f"/repos/{client.repo}/actions/runs/{run['id']}/jobs",
                    item_key="jobs",
                )
            )
            try:
                head_attempts += int(run.get("run_attempt") or 1)
            except (TypeError, ValueError):
                head_attempts += 1
        check_runs = client.get_paginated(
            f"/repos/{client.repo}/commits/{sha}/check-runs",
            item_key="check_runs",
        )
        per_head[sha] = event_fanout_metrics(
            runs=runs,
            jobs=jobs,
            check_runs=check_runs,
            merged_at=pr.get("merged_at") if sha == final_sha else None,
        )
        attempts[sha] = head_attempts
    totals = {
        "head_count": len(heads),
        "observed_head_count": len(per_head),
        "missing_head_count": len([m for m in missing if m.get("status") == "missing-artifact"]),
        "non_tip_head_count": len([m for m in missing if m.get("status") == "non-tip-commit"]),
        "total_attempts": sum(attempts.values()),
        "completed_runner_minutes": sum(
            float(per_head[sha].get("completed_runner_minutes") or 0.0) for sha in per_head
        ),
        "cancelled_runner_minutes": sum(
            float(per_head[sha].get("cancelled_runner_minutes") or 0.0) for sha in per_head
        ),
        "failed_runner_minutes": sum(float(per_head[sha].get("failed_runner_minutes") or 0.0) for sha in per_head),
    }
    return {
        "number": number,
        "heads": heads,
        "per_head": per_head,
        "attempts": attempts,
        "missing": missing,
        "totals": totals,
    }


def _medium_budget_warning(p95_seconds: float | None) -> str | None:
    if p95_seconds is None:
        return None
    timeout_minutes = _medium_test_timeout_minutes()
    budget_seconds = timeout_minutes * 60
    used = p95_seconds / budget_seconds
    if used < MEDIUM_TEST_BUDGET_WARN_FRACTION:
        return None
    return (
        f"medium-test p95 is {p95_seconds / 60:.1f} min, "
        f"{used:.0%} of its {timeout_minutes} min timeout - "
        "resize the timeout (or split the tier) before it starts cancelling jobs"
    )


def summarize(metrics: list[PrMetrics]) -> dict:
    merge_times = [m.open_to_merge_seconds for m in metrics]
    pushes = [m.pushes_after_open for m in metrics]
    fast_job = [m.fast_test_job_seconds for m in metrics if m.fast_test_job_seconds is not None]
    medium_job = [m.medium_test_job_seconds for m in metrics if m.medium_test_job_seconds is not None]
    green_known = [m for m in metrics if m.first_pass_green is not None]
    green_count = sum(1 for m in green_known if m.first_pass_green)
    return {
        "pr_count": len(metrics),
        "open_to_merge_seconds_median": statistics.median(merge_times) if merge_times else None,
        "pushes_after_open_median": statistics.median(pushes) if pushes else None,
        "fast_test_lane_policy_touch_rate": (
            sum(1 for m in metrics if m.touched_fast_test_lane_policy) / len(metrics) if metrics else None
        ),
        "first_pass_green_rate": (green_count / len(green_known)) if green_known else None,
        "first_pass_green_sample_size": len(green_known),
        "fast_test_job_seconds_avg": statistics.fmean(fast_job) if fast_job else None,
        "fast_test_job_seconds_p95": _pct(fast_job, 95),
        "medium_test_job_seconds_avg": statistics.fmean(medium_job) if medium_job else None,
        "medium_test_job_seconds_p95": _pct(medium_job, 95),
        "medium_test_budget_warning": _medium_budget_warning(_pct(medium_job, 95)),
    }


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def validate_lifecycle_baseline(lifecycle: dict, process: dict, process_digest: str) -> list[str]:

    errors: list[str] = []
    if not isinstance(lifecycle, dict) or lifecycle.get("schema") != LIFECYCLE_SCHEMA:
        return [f"lifecycle baseline schema must be {LIFECYCLE_SCHEMA!r}"]
    if not isinstance(process, dict):
        return ["process baseline must be a JSON object"]
    proc_ref = lifecycle.get("process_baseline")
    if not isinstance(proc_ref, dict):
        errors.append("lifecycle baseline must record its process_baseline binding")
    else:
        if proc_ref.get("criteria_version") != process.get("criteria_version"):
            errors.append(
                "process criteria changed after registration: "
                f"lifecycle binds {proc_ref.get('criteria_version')!r}, "
                f"process file declares {process.get('criteria_version')!r} "
                "(bump criteria_version and disclose instead of re-scoring)"
            )
        if proc_ref.get("process_digest") != process_digest:
            errors.append("process baseline content differs from the frozen digest (thresholds or scenarios changed)")
    prs = lifecycle.get("prs")
    if not isinstance(prs, list) or not prs:
        errors.append("lifecycle baseline must list at least one PR entry")
        return errors
    errors.extend(_check_lifecycle_cohort(lifecycle, prs))
    for entry in prs:
        if not isinstance(entry, dict):
            errors.append("PR entry must be an object")
            continue
        errors.extend(_check_lifecycle_entry(entry))
    return errors


def _check_lifecycle_cohort(lifecycle: dict, prs: list) -> list[str]:
    errors: list[str] = []
    cohort = lifecycle.get("cohort")
    gaps = lifecycle.get("collection_gaps")
    if not isinstance(cohort, dict):
        return ["lifecycle cohort must be an object"]
    if not isinstance(gaps, list):
        return ["lifecycle collection_gaps must be a list"]

    counts: dict[str, int] = {}
    for field in ("candidate_prs", "collected_prs"):
        value = cohort.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"lifecycle cohort {field} must be a nonnegative integer")
        else:
            counts[field] = value

    pr_numbers = [entry.get("number") for entry in prs if isinstance(entry, dict)]
    if any(not isinstance(number, int) or isinstance(number, bool) for number in pr_numbers):
        errors.append("lifecycle PR numbers must be integers")
    valid_pr_numbers = [number for number in pr_numbers if isinstance(number, int) and not isinstance(number, bool)]
    if len(valid_pr_numbers) != len(set(valid_pr_numbers)):
        errors.append("lifecycle PR entries contain duplicate numbers")

    gap_numbers: list[int] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            errors.append(f"lifecycle collection gap must be an object: {gap!r}")
            continue
        number = gap.get("number")
        status = gap.get("status")
        if not isinstance(number, int) or isinstance(number, bool):
            errors.append(f"lifecycle collection gap number must be an integer: {gap!r}")
            continue
        if not isinstance(status, str) or not status.strip():
            errors.append(f"lifecycle collection gap status must be non-empty: {gap!r}")
        gap_numbers.append(number)
    if len(gap_numbers) != len(set(gap_numbers)):
        errors.append("lifecycle collection_gaps contain duplicate PR numbers")
    overlap = sorted(set(valid_pr_numbers) & set(gap_numbers))
    if overlap:
        errors.append(f"lifecycle PRs and collection_gaps overlap: {overlap}")
    if counts.get("collected_prs") != len(set(valid_pr_numbers)):
        errors.append(
            f"lifecycle cohort collected_prs {counts.get('collected_prs')!r} "
            f"!= {len(set(valid_pr_numbers))} unique PR entries"
        )
    expected_candidates = len(set(valid_pr_numbers)) + len(set(gap_numbers))
    if counts.get("candidate_prs") != expected_candidates:
        errors.append(
            f"lifecycle cohort candidate_prs {counts.get('candidate_prs')!r} "
            f"!= {len(set(valid_pr_numbers))} collected + {len(set(gap_numbers))} gaps"
        )
    return errors


def _check_lifecycle_entry(entry: dict) -> list[str]:

    errors: list[str] = []
    number = entry.get("number")
    heads = entry.get("heads") or []
    per_head = entry.get("per_head") or {}
    attempts = entry.get("attempts") or {}
    missing = entry.get("missing") or []
    totals = entry.get("totals") or {}
    if len(heads) != len(set(heads)):
        errors.append(f"PR #{number}: duplicate head SHAs in heads list")
    missing_shas = [m.get("head_sha") for m in missing if isinstance(m, dict)]
    if set(per_head) & set(missing_shas):
        errors.append(f"PR #{number}: head recorded as both observed and missing")
    if set(per_head) | set(missing_shas) != set(heads):
        errors.append(
            f"PR #{number}: heads/per_head/missing disagree "
            f"({len(heads)} heads, {len(per_head)} observed, {len(missing_shas)} missing)"
        )
    for m in missing:
        if not isinstance(m, dict) or not m.get("status") or not m.get("reason"):
            errors.append(f"PR #{number}: missing entry must carry explicit status and reason")
    for sha in per_head:
        if sha not in attempts:
            errors.append(f"PR #{number}: observed head {sha[:12]} lacks an attempt count")
    for sha in attempts:
        if sha not in per_head:
            errors.append(f"PR #{number}: attempt count for unobserved head {sha[:12]}")
    for sha in per_head:
        ids = (per_head[sha].get("retrieval_ids") or {}).get("runs") or []
        if not any(isinstance(r, dict) and r.get("id") for r in ids):
            errors.append(
                f"PR #{number}: observed head {sha[:12]} carries no run retrieval IDs; "
                "aggregates without IDs are not replayable"
            )
    errors.extend(_check_lifecycle_totals(number, heads, per_head, attempts, missing, totals))
    return errors


def _check_lifecycle_totals(
    number: object,
    heads: list,
    per_head: dict,
    attempts: dict,
    missing: list,
    totals: dict,
) -> list[str]:

    errors: list[str] = []
    statuses = [m.get("status") if isinstance(m, dict) else None for m in missing]
    for key, expected in (
        ("head_count", len(heads)),
        ("observed_head_count", len(per_head)),
        ("missing_head_count", statuses.count("missing-artifact")),
        ("non_tip_head_count", statuses.count("non-tip-commit")),
        ("total_attempts", sum(int(attempts.get(sha) or 0) for sha in per_head)),
    ):
        if totals.get(key) != expected:
            errors.append(f"PR #{number}: totals.{key} is {totals.get(key)!r}, recomputed {expected!r}")
    for key in ("completed_runner_minutes", "cancelled_runner_minutes", "failed_runner_minutes"):
        expected = sum(float((per_head[sha] or {}).get(key) or 0.0) for sha in per_head)
        actual = totals.get(key)
        if not isinstance(actual, (int, float)) or abs(float(actual) - expected) > 1e-6:
            errors.append(f"PR #{number}: totals.{key} is {actual!r}, recomputed {expected!r}")
    return errors


_REFRESH_AUDIT_FENCE_RE = re.compile(r"(?ms)```json\s*\n(.*?)```")


def _load_refresh_audit_block(audit_text: str) -> tuple[dict | None, str | None]:
    for match in _REFRESH_AUDIT_FENCE_RE.finditer(audit_text or ""):
        try:
            block = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(block, dict) and block.get("schema") == REFRESH_AUDIT_SCHEMA:
            return block, None
    return None, f"no {REFRESH_AUDIT_SCHEMA} JSON block found in refresh audit"


def validate_refresh_audit(audit_text: str, lifecycle: dict, reason_codes: tuple[str, ...]) -> list[str]:

    errors: list[str] = []
    block, block_error = _load_refresh_audit_block(audit_text)
    if block is None or block_error:
        return [block_error or "unreadable refresh audit block"]
    if not isinstance(lifecycle, dict) or lifecycle.get("schema") != LIFECYCLE_SCHEMA:
        return [f"refresh audit baseline must be a {LIFECYCLE_SCHEMA} report"]
    cohort = lifecycle.get("cohort") or {}
    for key in ("window_start", "window_end"):
        if block.get(key) != cohort.get(key):
            errors.append(f"refresh audit {key} {block.get(key)!r} != baseline cohort {cohort.get(key)!r}")
    baseline_prs = set()
    baseline_heads: dict[int, set[str]] = {}
    for entry in lifecycle.get("prs") or []:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        baseline_prs.add(number)
        baseline_heads[number] = set(entry.get("heads") or [])
    denominator = block.get("denominator_prs") or []
    if not isinstance(denominator, list):
        return errors + ["refresh audit denominator_prs must be a list"]
    unknown = [n for n in denominator if n not in baseline_prs]
    if unknown:
        errors.append(f"refresh audit denominator uses PRs outside the frozen baseline: {unknown!r}")
    omitted = sorted(set(baseline_prs) - set(denominator))
    if omitted:
        errors.append(
            f"refresh audit denominator omits frozen baseline PRs (subset audits hide "
            f"unfavorable observations): {omitted!r}"
        )
    observations = block.get("observations") or []
    recomputed = _check_audit_observations(observations, reason_codes, errors)
    _check_audit_counts(block, observations, recomputed, errors)
    _check_audit_missing(block, denominator, baseline_heads, observations, errors)
    return errors


def _check_audit_observations(observations: list, reason_codes: tuple[str, ...], errors: list[str]) -> dict[str, int]:

    seen: set[tuple] = set()
    recomputed: dict[str, int] = {}
    for obs in observations:
        if not isinstance(obs, dict):
            errors.append("refresh observation must be an object")
            continue
        identity = (obs.get("pr"), obs.get("head_sha"), obs.get("run_id"), obs.get("attempt"))
        if None in identity:
            errors.append(f"refresh observation lacks a full pr/head/run/attempt identity: {obs!r}")
            continue
        if identity in seen:
            errors.append(f"duplicate refresh observation identity: {identity!r}")
        seen.add(identity)
        reason = obs.get("reason")
        if reason not in reason_codes:
            errors.append(f"unknown refresh reason code: {reason!r}")
            continue
        recomputed[reason] = recomputed.get(reason, 0) + 1
    return recomputed


def _check_audit_counts(block: dict, observations: list, recomputed: dict[str, int], errors: list[str]) -> None:

    claimed_counts = block.get("reason_counts") or {}
    for required_key in (REFRESH_REASON_TIMING, REFRESH_REASON_IDENTITY):
        if required_key not in claimed_counts:
            errors.append(f"reason_counts must report {required_key!r} separately (zero allowed), not conflate it")
    comparable = {
        key: value
        for key, value in claimed_counts.items()
        if not (value == 0 and key in (REFRESH_REASON_TIMING, REFRESH_REASON_IDENTITY))
    }
    if comparable != dict(recomputed):
        errors.append(f"reason_counts {claimed_counts!r} != recomputed {recomputed!r}")
    full_required = sum(1 for o in observations if isinstance(o, dict) and o.get("verdict") == "full_required")
    timing = recomputed.get(REFRESH_REASON_TIMING, 0)
    expected_share = (timing / full_required) if full_required else 0.0
    actual_share = block.get("timing_only_share")
    if not isinstance(actual_share, (int, float)) or abs(float(actual_share) - expected_share) > 1e-9:
        errors.append(f"timing_only_share is {actual_share!r}, recomputed {expected_share!r}")


def _check_audit_missing(
    block: dict,
    denominator: list,
    baseline_heads: dict[int, set[str]],
    observations: list,
    errors: list[str],
) -> None:

    observed_pairs = {(o.get("pr"), o.get("head_sha")) for o in observations if isinstance(o, dict)}
    missing = block.get("missing") or []
    missing_pairs = {(m.get("pr"), m.get("head_sha")) for m in missing if isinstance(m, dict)}
    for number in denominator:
        for sha in baseline_heads.get(number, set()):
            if (number, sha) not in observed_pairs and (number, sha) not in missing_pairs:
                errors.append(f"PR #{number} head {str(sha)[:12]} neither observed nor listed as missing")
    for m in missing:
        if not isinstance(m, dict) or not m.get("pr") or not m.get("head_sha") or not m.get("status"):
            errors.append(f"missing entry must carry pr/head/status: {m!r}")


def run_validate_lifecycle_baseline(lifecycle_path: str, process_path: str) -> int:

    try:
        lifecycle = json.loads(Path(lifecycle_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"INVALID lifecycle baseline {lifecycle_path}: {exc}")
        return 1
    try:
        process_raw = Path(process_path).read_bytes()
        process = json.loads(process_raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"INVALID process baseline {process_path}: {exc}")
        return 1
    errors = validate_lifecycle_baseline(lifecycle, process, _sha256_bytes(process_raw))
    if errors:
        print(f"INVALID lifecycle baseline {lifecycle_path}:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"VALID lifecycle baseline {lifecycle_path}")
    return 0


def _git_show_bytes(revision: str, path: str) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _is_ancestor(commit: str, head: str = "HEAD") -> bool:
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, head],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _commit_parents(commit: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--parents", "-1", commit],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    tokens = proc.stdout.strip().split()
    if not tokens:
        return None
    return tokens[1:]


def _commit_parent(commit: str) -> str | None:
    parents = _commit_parents(commit)
    return parents[0] if parents else None


def _has_second_parent(commit: str) -> bool | None:
    parents = _commit_parents(commit)
    if parents is None:
        return None
    return len(parents) > 1


def _commit_timestamp(commit: str) -> int | None:
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%ct", commit],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _commit_diff_names(base: str, commit: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "-z", base, commit, "--"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [os.fsdecode(entry) for entry in proc.stdout.split(b"\0") if entry]


def _pinned_registration_pointers(commit: str, acceptance_relpath: str) -> set[str]:
    raw = _git_show_bytes(commit, acceptance_relpath)
    if raw is None:
        return set()
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return set()
    registration = record.get("registration")
    if not isinstance(registration, dict):
        return set()
    return {str(value).lower() for value in (registration.get("commit"), registration.get("original_commit")) if value}


def validate_process_acceptance(
    acceptance: dict,
    process: dict,
    process_digest: str,
    process_relpath: str = "_project/analysis/pr-process-acceptance-baseline.json",
    acceptance_relpath: str = "_project/analysis/pr-process-acceptance.json",
    published_ref: str = DEFAULT_ACCEPTANCE_PUBLISHED_REF,
) -> list[str]:

    errors: list[str] = []
    if not isinstance(acceptance, dict) or acceptance.get("schema") != PROCESS_ACCEPTANCE_SCHEMA:
        return [f"acceptance record schema must be {PROCESS_ACCEPTANCE_SCHEMA!r}"]
    if not isinstance(process, dict):
        return ["process baseline must be a JSON object"]
    errors.extend(
        _check_acceptance_binding(
            acceptance, process, process_digest, process_relpath, acceptance_relpath, published_ref
        )
    )
    errors.extend(_check_acceptance_cohort(acceptance, process))
    errors.extend(_check_acceptance_replays(acceptance, process))
    errors.extend(_check_acceptance_efficiency(acceptance, process))
    return errors


def _check_acceptance_binding(
    acceptance: dict,
    process: dict,
    process_digest: str,
    process_relpath: str,
    acceptance_relpath: str = "_project/analysis/pr-process-acceptance.json",
    published_ref: str = DEFAULT_ACCEPTANCE_PUBLISHED_REF,
) -> list[str]:
    errors: list[str] = []
    binding = acceptance.get("process_binding") or {}
    if binding.get("criteria_version") != process.get("criteria_version"):
        errors.append("acceptance binds a different criteria_version than the process file (criteria changed)")
    if binding.get("process_digest") != process_digest:
        errors.append("process baseline content differs from the bound digest (thresholds or scenarios changed)")
    registration = acceptance.get("registration") or {}
    reg_commit = str(registration.get("commit") or "").lower()
    if not reg_commit:
        errors.append("acceptance must record its preregistration commit")
        return errors
    if re.fullmatch(r"[0-9a-fA-F]{40}", reg_commit) is None:
        errors.append("registration commit must be a full commit SHA (refs are movable)")
        return errors
    frozen = _git_show_bytes(reg_commit, process_relpath)
    if frozen is None:
        errors.append(f"registration commit {reg_commit[:12]} not resolvable in this tree")
    elif _sha256_bytes(frozen) != process_digest:
        errors.append("process file at the registration commit differs from the bound digest")
    if not _is_ancestor(reg_commit):
        errors.append("registration commit is not an ancestor of HEAD (freeze must precede implementation)")
    if not _is_ancestor(reg_commit, published_ref):
        errors.append(
            f"registration commit is not an ancestor of published ref {published_ref} "
            "(durable anchor must predate the candidate branch)"
        )
    original = str(registration.get("original_commit") or "").lower()
    if not original:
        errors.append("acceptance must preserve its original freeze commit (registration.original_commit)")
        return errors
    if re.fullmatch(r"[0-9a-fA-F]{40}", original) is None:
        errors.append("original registration commit must be a full commit SHA (refs are movable)")
        return errors
    errors.extend(
        _check_original_freeze(original, reg_commit, process, process_digest, process_relpath, acceptance_relpath)
    )
    return errors


def _check_original_freeze(
    original: str,
    reg_commit: str,
    process: dict,
    process_digest: str,
    process_relpath: str,
    acceptance_relpath: str,
) -> list[str]:
    errors: list[str] = []
    original_frozen = _git_show_bytes(original, process_relpath)
    if original_frozen is None:
        return [f"original registration commit {original[:12]} not resolvable in this tree"]
    if _sha256_bytes(original_frozen) != process_digest:
        return ["process file at the original registration commit differs from the bound digest"]
    base_freeze = str(process.get("base_commit_at_freeze") or "").lower()
    if not base_freeze or re.fullmatch(r"[0-9a-fA-F]{40}", base_freeze) is None:
        return ["frozen baseline fixes no base_commit_at_freeze (freeze base must be a full commit SHA)"]
    errors.extend(_check_freeze_only_child(original, base_freeze, process_relpath))
    if original != reg_commit:
        errors.extend(_check_history_anchor(original, reg_commit, acceptance_relpath))
    errors.extend(_check_freeze_ordering(original, reg_commit))
    return errors


def _check_freeze_only_child(original: str, base_freeze: str, process_relpath: str) -> list[str]:
    parent = _commit_parent(original)
    if parent is None:
        return [f"original registration commit {original[:12]} parent not resolvable in this tree"]
    if parent != base_freeze:
        return [
            "original registration commit is not the freeze-only child of the bound base_commit_at_freeze"
            " (a bundled squash commit cannot serve as its own freeze proof)"
        ]
    second_parent = _has_second_parent(original)
    if second_parent is None:
        return [f"original registration commit {original[:12]} parent count not resolvable in this tree"]
    if second_parent:
        return ["original registration commit is a merge commit (freeze must be a single-parent commit)"]
    diff_names = _commit_diff_names(base_freeze, original)
    if diff_names is None:
        return [f"original registration commit {original[:12]} diff not resolvable in this tree"]
    if sorted(diff_names) != [process_relpath]:
        return ["original registration commit changes more than the frozen baseline (freeze must be baseline-only)"]
    return []


def _check_history_anchor(original: str, reg_commit: str, acceptance_relpath: str) -> list[str]:
    pinned = _pinned_registration_pointers(reg_commit, acceptance_relpath)
    if not pinned:
        return [
            "durable registration commit pins no registration pointers in its acceptance record"
            " (freeze SHA must be anchored by published history)"
        ]
    if original not in pinned:
        return [
            "original registration commit matches no registration pointer pinned in the durable"
            " commit's own acceptance record (freeze SHA must be anchored by published history)"
        ]
    return []


def _check_freeze_ordering(original: str, reg_commit: str) -> list[str]:
    original_ts = _commit_timestamp(original)
    reg_ts = _commit_timestamp(reg_commit)
    if original_ts is None or reg_ts is None:
        return ["cannot establish freeze ordering for the original registration commit"]
    if original_ts > reg_ts:
        return [
            "original registration commit is newer than the durable registration commit"
            " (freeze must precede integration)"
        ]
    return []


def _check_acceptance_cohort(acceptance: dict, process: dict) -> list[str]:
    errors: list[str] = []
    required_cohort = ((acceptance.get("cohort") or {}).get("required")) or {}
    observed_cohort = ((acceptance.get("cohort") or {}).get("observed")) or {}
    frozen_cohort = process.get("prospective_cohort") or {}
    if observed_cohort.get("window_start") != required_cohort.get("window_start") or observed_cohort.get(
        "window_end"
    ) != required_cohort.get("window_end"):
        errors.append("observed cohort window differs from the preregistered window (unreported cohort change)")
    for dimension in ("prs", "days"):
        try:
            floor = int(frozen_cohort.get(f"min_{dimension}") or 0)
            minimum = int(required_cohort.get(f"min_{dimension}") or 0)
            observed = int(observed_cohort.get(dimension) or 0)
        except (TypeError, ValueError):
            errors.append(f"cohort {dimension} counts must be integers")
            continue
        if minimum < floor:
            errors.append(f"required cohort min_{dimension} {minimum} weakens the frozen floor {floor}")
        if observed < minimum:
            errors.append(f"cohort {dimension} insufficient: {observed} < {minimum}")
    frozen_strata = frozen_cohort.get("strata") or []
    if set(required_cohort.get("strata") or []) != set(frozen_strata):
        errors.append(
            f"required strata {sorted(required_cohort.get('strata') or [])} != frozen strata {sorted(frozen_strata)}"
        )
    for stratum in required_cohort.get("strata") or []:
        if stratum not in (observed_cohort.get("strata") or []):
            errors.append(f"cohort stratum missing: {stratum}")
    deliveries = observed_cohort.get("batch_deliveries")
    try:
        min_deliveries = int((frozen_cohort.get("batch_deliveries") or {}).get("min_deliveries") or 0)
    except (TypeError, ValueError):
        errors.append("frozen batch delivery minimum must be an integer")
        min_deliveries = 0
    if not isinstance(deliveries, list):
        errors.append("batch_deliveries must be a list of bound delivery receipts")
    else:
        errors.extend(_check_delivery_receipts(deliveries, frozen_cohort, min_deliveries))
    if not observed_cohort.get("human_hold"):
        errors.append("human hold flow (operator-approved soundness hold) not evidenced in the observed cohort")
    return errors


def _check_delivery_receipts(deliveries: list, frozen_cohort: dict, min_deliveries: int) -> list[str]:
    errors: list[str] = []
    batch_requirements = frozen_cohort.get("batch_deliveries") or {}
    if not isinstance(batch_requirements, dict):
        return ["frozen batch delivery requirements must be an object"]
    try:
        members_min = int(batch_requirements.get("members_per_delivery_min") or 0)
        dependencies_min = int(batch_requirements.get("internal_implementation_dependency_min") or 0)
    except (TypeError, ValueError):
        return ["frozen batch delivery requirements must be integers"]

    valid_receipts = 0
    dependency_edges: set[tuple[str, str, str]] = set()
    seen_batches: set[str] = set()
    seen_heads: set[str] = set()
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    for index, delivery in enumerate(deliveries):
        prefix = f"batch delivery #{index + 1}"
        if not isinstance(delivery, dict):
            errors.append(f"{prefix} must be a receipt object")
            continue
        receipt_errors: list[str] = []
        if delivery.get("schema") != BATCH_DELIVERY_RECEIPT_SCHEMA:
            receipt_errors.append(f"{prefix} schema must be {BATCH_DELIVERY_RECEIPT_SCHEMA!r}")
        batch_id, integration_head, identity_errors = _check_delivery_identity(
            delivery, prefix, sha_pattern, seen_batches, seen_heads
        )
        receipt_errors.extend(identity_errors)
        member_ids, member_errors = _check_delivery_members(
            delivery, prefix, integration_head, sha_pattern, members_min
        )
        receipt_errors.extend(member_errors)
        attestation_errors, edges = _check_delivery_attestations(delivery, prefix, member_ids, batch_id)
        receipt_errors.extend(attestation_errors)
        dependency_edges.update(edges)
        errors.extend(receipt_errors)
        if not receipt_errors:
            valid_receipts += 1

    if valid_receipts < min_deliveries:
        errors.append(f"batch deliveries insufficient: {valid_receipts} valid distinct receipts < {min_deliveries}")
    if len(dependency_edges) < dependencies_min:
        errors.append(
            f"internal implementation dependency evidence insufficient: {len(dependency_edges)} < {dependencies_min}"
        )
    return errors


def _check_delivery_identity(
    delivery: dict,
    prefix: str,
    sha_pattern: re.Pattern[str],
    seen_batches: set[str],
    seen_heads: set[str],
) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    batch_id = delivery.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        errors.append(f"{prefix} lacks a batch_id")
        batch_id = ""
    elif batch_id in seen_batches:
        errors.append(f"{prefix} duplicates batch_id {batch_id!r}")
    else:
        seen_batches.add(batch_id)
    integration_head = delivery.get("integration_head")
    if not isinstance(integration_head, str) or sha_pattern.fullmatch(integration_head) is None:
        errors.append(f"{prefix} integration_head must be a full Git SHA")
        integration_head = ""
    elif integration_head in seen_heads:
        errors.append(f"{prefix} duplicates integration_head {integration_head}")
    elif not _is_ancestor(integration_head):
        errors.append(f"{prefix} integration_head {integration_head} is not reachable from HEAD")
    else:
        seen_heads.add(integration_head)
    return batch_id, integration_head, errors


def _check_delivery_members(
    delivery: dict,
    prefix: str,
    integration_head: str,
    sha_pattern: re.Pattern[str],
    members_min: int,
) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    members = delivery.get("members")
    if not isinstance(members, list):
        return set(), [f"{prefix} members must be a list"]
    member_ids: list[str] = []
    for member in members:
        if not isinstance(member, dict):
            errors.append(f"{prefix} member must be an object: {member!r}")
            continue
        member_id = member.get("id")
        member_head = member.get("head")
        if not isinstance(member_id, str) or not member_id:
            errors.append(f"{prefix} member lacks a non-empty id")
            continue
        member_ids.append(member_id)
        if not isinstance(member_head, str) or sha_pattern.fullmatch(member_head) is None:
            errors.append(f"{prefix} member {member_id!r} head must be a full Git SHA")
        elif integration_head and not _is_ancestor(member_head, integration_head):
            errors.append(f"{prefix} member {member_id!r} head is not in its integration head")
    unique_ids = set(member_ids)
    if len(member_ids) != len(unique_ids):
        errors.append(f"{prefix} contains duplicate member IDs")
    if len(unique_ids) < members_min:
        errors.append(f"{prefix} has {len(unique_ids)} members; minimum is {members_min}")
    return unique_ids, errors


def _check_delivery_attestations(
    delivery: dict, prefix: str, expected_members: set[str], batch_id: str
) -> tuple[list[str], set[tuple[str, str, str]]]:
    errors: list[str] = []
    ancestry = delivery.get("member_ancestry")
    if not isinstance(ancestry, dict) or set(ancestry) != expected_members:
        errors.append(f"{prefix} member_ancestry keys must exactly match members")
    elif any(value is not True for value in ancestry.values()):
        errors.append(f"{prefix} member_ancestry must be exactly true for every member")
    item_acceptance = delivery.get("acceptance")
    if not isinstance(item_acceptance, dict) or set(item_acceptance) != expected_members:
        errors.append(f"{prefix} acceptance keys must exactly match members")
    elif any(value != "pass" for value in item_acceptance.values()):
        errors.append(f"{prefix} acceptance must be exactly 'pass' for every member")
    dependency_errors, dependency_edges = _check_delivery_dependencies(
        delivery.get("internal_implementation_dependencies"), prefix, expected_members, batch_id
    )
    errors.extend(dependency_errors)
    return errors, dependency_edges


def _check_delivery_dependencies(
    dependencies: object, prefix: str, expected_members: set[str], batch_id: str
) -> tuple[list[str], set[tuple[str, str, str]]]:
    if not isinstance(dependencies, list):
        return [f"{prefix} internal_implementation_dependencies must be a list"], set()
    errors: list[str] = []
    receipt_edges: set[tuple[str, str]] = set()
    bound_edges: set[tuple[str, str, str]] = set()
    for edge in dependencies:
        if not isinstance(edge, dict):
            errors.append(f"{prefix} internal dependency must be an object: {edge!r}")
            continue
        member = edge.get("member")
        depends_on = edge.get("depends_on")
        evidence = edge.get("evidence")
        if not isinstance(member, str) or not isinstance(depends_on, str):
            errors.append(f"{prefix} internal dependency endpoints must be member IDs: {edge!r}")
            continue
        identity = (member, depends_on)
        if member not in expected_members or depends_on not in expected_members or member == depends_on:
            errors.append(f"{prefix} internal dependency endpoints must be distinct members: {edge!r}")
        elif identity in receipt_edges:
            errors.append(f"{prefix} duplicates internal dependency {identity!r}")
        elif not isinstance(evidence, str) or not evidence.strip():
            errors.append(f"{prefix} internal dependency lacks evidence: {edge!r}")
        else:
            receipt_edges.add(identity)
            bound_edges.add((batch_id, member, depends_on))
    return errors, bound_edges


def _check_acceptance_replays(acceptance: dict, process: dict) -> list[str]:
    replays = acceptance.get("incident_replays") or []
    if not replays:
        return ["no incident replays recorded"]
    frozen = process.get("incident_scenarios") or []
    recorded = sorted(r.get("scenario", "?") for r in replays if isinstance(r, dict))
    errors = []
    if recorded != sorted(frozen):
        errors.append(f"replay scenario set {recorded} != frozen set {sorted(frozen)}")
    errors.extend(
        f"incident replay not passing: {replay.get('scenario', '?')}"
        for replay in replays
        if not isinstance(replay, dict) or replay.get("status") != "pass"
    )
    return errors


def _check_acceptance_efficiency(acceptance: dict, process: dict) -> list[str]:
    errors: list[str] = []
    efficiency = acceptance.get("efficiency") or {}
    baseline_actions = efficiency.get("baseline_avoidable_actions")
    observed_actions = efficiency.get("observed_avoidable_actions")
    try:
        frozen_reduction = float((process.get("thresholds") or {}).get("avoidable_action_reduction"))
    except (TypeError, ValueError):
        errors.append("frozen baseline fixes no numeric avoidable-action reduction threshold")
        frozen_reduction = 0.0
    if baseline_actions is None or observed_actions is None:
        errors.append("efficiency unmeasured: baseline or observed avoidable actions missing")
    elif float(baseline_actions) <= 0:
        errors.append("efficiency baseline must be positive")
    elif (float(baseline_actions) - float(observed_actions)) / float(baseline_actions) < frozen_reduction:
        errors.append(f"avoidable-action reduction below frozen {frozen_reduction:.0%}")
    if efficiency.get("added_required_lane_failure"):
        errors.append("added required-lane failure attributable to coordination")
    if efficiency.get("p95_regression_attributable"):
        errors.append("measured p95 regression attributable to coordination")
    return errors


def run_validate_process_acceptance(acceptance_path: str, process_path: str) -> int:

    try:
        acceptance = json.loads(Path(acceptance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"INCOMPLETE acceptance {acceptance_path}: {exc}")
        return 1
    try:
        process_raw = Path(process_path).read_bytes()
        process = json.loads(process_raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"INCOMPLETE process baseline {process_path}: {exc}")
        return 1
    try:
        acceptance_relpath = Path(acceptance_path).resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        acceptance_relpath = "_project/analysis/pr-process-acceptance.json"
    try:
        process_relpath = Path(process_path).resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        process_relpath = "_project/analysis/pr-process-acceptance-baseline.json"
    errors = validate_process_acceptance(
        acceptance,
        process,
        _sha256_bytes(process_raw),
        process_relpath=process_relpath,
        acceptance_relpath=acceptance_relpath,
    )
    if errors:
        print(f"INCOMPLETE acceptance {acceptance_path}:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"ACCEPTED {acceptance_path}")
    return 0


def run_validate_refresh_audit(audit_path: str, baseline_path: str) -> int:

    try:
        audit_text = Path(audit_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"INVALID refresh audit {audit_path}: {exc}")
        return 1
    try:
        lifecycle = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"INVALID lifecycle baseline {baseline_path}: {exc}")
        return 1
    try:
        reason_codes = load_refresh_reason_codes()
    except RuntimeError as exc:
        print(f"INVALID refresh audit {audit_path}: {exc}")
        return 1
    errors = validate_refresh_audit(audit_text, lifecycle, reason_codes)
    if errors:
        print(f"INVALID refresh audit {audit_path}:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"VALID refresh audit {audit_path}")
    return 0


def load_refresh_reason_codes() -> tuple[str, ...]:

    import importlib.util

    path = REPO_ROOT / "scripts" / "pr_refresh_certification.py"
    spec = importlib.util.spec_from_file_location("pr_refresh_certification_codes", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load refresh reason codes from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    codes = getattr(module, "REASON_CODES", None)
    if not codes:
        raise RuntimeError(f"no REASON_CODES in {path}")
    return tuple(str(code) for code in codes)


DURATIONS_PYTEST_CMD = [
    "uv",
    "run",
    "--",
    "python",
    "-m",
    "pytest",
    "tests",
    "-m",
    "fast and not (slow or stress or resource_heavy or live_integration)",
    "--durations=20",
    "-q",
    "--no-header",
    "-p",
    "no:cacheprovider",
]


def run_collect_durations() -> int:
    print(
        "Local-machine measure only (wall-clock durations vary by hardware; "
        "not comparable across CI runners or between machines)."
    )
    print(f"Running: {' '.join(DURATIONS_PYTEST_CMD)}")
    proc = subprocess.run(DURATIONS_PYTEST_CMD, cwd=REPO_ROOT, check=False)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO),
        help=f"owner/name (default: $GITHUB_REPOSITORY or {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=28,
        help="trailing window in days (default: 28)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON (summary + per-PR rows) instead of text",
    )
    parser.add_argument(
        "--event-fanout",
        action="store_true",
        help=(
            f"include a versioned {EVENT_FANOUT_SCHEMA} section that separates "
            "required-gate / merge-unblock wall time from all-workflow fan-out, "
            "runner-minutes, cancellations, setup, and execution. Existing "
            "summary keys are unchanged."
        ),
    )
    parser.add_argument(
        "--collect-durations",
        action="store_true",
        help=(
            "local-machine-only mode: run the fast test lane with "
            "pytest --durations=20 and print the slowest 20 tests. Does not "
            "touch the GitHub API; mutually exclusive with the default mode."
        ),
    )
    parser.add_argument(
        "--validate-lifecycle-baseline",
        metavar="LIFECYCLE_JSON",
        default=None,
        help=(
            "validate a ci_lifecycle_baseline_v1 report (all-head/run-attempt "
            "totals, explicit missingness) against --process-baseline. Local "
            "report check only; touches no API and changes no CI routing."
        ),
    )
    parser.add_argument(
        "--process-baseline",
        metavar="PROCESS_JSON",
        default=None,
        help="frozen preregistered process baseline the lifecycle report binds.",
    )
    parser.add_argument(
        "--validate-refresh-audit",
        metavar="AUDIT_MD",
        default=None,
        help=(
            "recompute the refresh_audit_v1 JSON block embedded in a Markdown "
            "audit from --baseline. Local report check only."
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="LIFECYCLE_JSON",
        default=None,
        help="shared lifecycle baseline the refresh audit reconciles against.",
    )
    parser.add_argument(
        "--validate-process-acceptance",
        metavar="ACCEPTANCE_JSON",
        default=None,
        help=(
            "validate the final integrated acceptance record against the frozen "
            "preregistration (--process-baseline). Exits non-zero listing gaps "
            "until every dimension passes."
        ),
    )
    args = parser.parse_args(argv)

    if args.collect_durations:
        return run_collect_durations()

    if args.validate_process_acceptance:
        if not args.process_baseline:
            print("ERROR: --validate-process-acceptance requires --process-baseline.")
            return 2
        return run_validate_process_acceptance(args.validate_process_acceptance, args.process_baseline)

    if args.validate_lifecycle_baseline or args.process_baseline:
        if not args.validate_lifecycle_baseline or not args.process_baseline:
            print("ERROR: --validate-lifecycle-baseline and --process-baseline must be given together.")
            return 2
        return run_validate_lifecycle_baseline(args.validate_lifecycle_baseline, args.process_baseline)

    if args.validate_refresh_audit or args.baseline:
        if not args.validate_refresh_audit or not args.baseline:
            print("ERROR: --validate-refresh-audit and --baseline must be given together.")
            return 2
        return run_validate_refresh_audit(args.validate_refresh_audit, args.baseline)

    client = make_client(args.repo)
    if client is None:
        print("SKIPPED: no GitHub API access (no `gh` CLI and no usable GITHUB_TOKEN/GH_TOKEN).")
        return 0

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    try:
        prs = fetch_merged_prs(client, since)
        metrics = [collect_pr_metrics(client, pr, include_event_fanout=args.event_fanout) for pr in prs]
    except ApiFailure as exc:
        print(f"SKIPPED: GitHub API access failed mid-collection, metrics would be understated ({exc}).")
        return 0
    summary = summarize(metrics)

    if args.json:
        payload: dict = {
            "repo": args.repo,
            "since": since.isoformat(),
            "summary": summary,
            "prs": [
                ({key: value for key, value in asdict(m).items() if args.event_fanout or key != "event_fanout"})
                for m in metrics
            ],
        }
        if args.event_fanout:
            payload[EVENT_FANOUT_SCHEMA] = {
                "required_contexts": list(REQUIRED_CONTEXT_NAMES),
                "legacy_required_contexts": list(LEGACY_REQUIRED_CONTEXT_NAMES),
                "synchronize_workflows": list(SYNCHRONIZE_WORKFLOW_NAMES),
                "public_standard_runner_usd": PUBLIC_STANDARD_RUNNER_USD,
                "pr_count_with_fanout": sum(m.event_fanout is not None for m in metrics),
            }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Dev-loop PR metrics for {args.repo} since {since.date().isoformat()} ({len(metrics)} merged develop PRs)")
    print(f"  open-to-merge P50: {summary['open_to_merge_seconds_median']!r} seconds")
    print(f"  pushes-after-open P50: {summary['pushes_after_open_median']!r}")
    print(f"  fast_test_lane_policy.json touch rate: {summary['fast_test_lane_policy_touch_rate']!r}")
    print(
        "  first-pass required-lane green rate: "
        f"{summary['first_pass_green_rate']!r} (n={summary['first_pass_green_sample_size']})"
    )
    print(
        f"  fast-test job seconds avg/p95: {summary['fast_test_job_seconds_avg']!r} / {summary['fast_test_job_seconds_p95']!r}"
    )
    print(
        f"  medium-test job seconds avg/p95: {summary['medium_test_job_seconds_avg']!r} / "
        f"{summary['medium_test_job_seconds_p95']!r}"
    )
    if args.event_fanout:
        for metric in metrics:
            print(f"  PR #{metric.number} {EVENT_FANOUT_SCHEMA}: {json.dumps(metric.event_fanout, sort_keys=True)}")
    if summary["medium_test_budget_warning"]:
        print(f"  MEDIUM_TEST_BUDGET_WARNING: {summary['medium_test_budget_warning']}")
    for m in metrics:
        print(
            f"  PR #{m.number}: pushes_after_open={m.pushes_after_open} "
            f"open_to_merge_s={m.open_to_merge_seconds:.0f} "
            f"fast_lane_policy_touched={m.touched_fast_test_lane_policy} "
            f"first_pass_green={m.first_pass_green} "
            f"fast_test_job_s={m.fast_test_job_seconds} "
            f"medium_test_job_s={m.medium_test_job_seconds}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
