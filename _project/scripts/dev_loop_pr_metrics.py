#!/usr/bin/env python3

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    "    make ci-usage-report CI_USAGE_SINCE=2026-10-10 CI_USAGE_UNTIL=2026-10-24\n"
    "\n"
    "--collect-durations is a separate, local-machine-only mode: it runs the fast\n"
    "test lane under `pytest --durations=20` and prints the slowest tests. It does\n"
    "not touch the GitHub API and is not part of the default run (see the module\n"
    "TODO context: dev-loop-metrics-ci-failure-baseline-2).\n"
    "\n"
    "--usage-report is the shared CI usage report. It partitions the UTC window into\n"
    "run searches below the API's 1,000-result cap, fetches every run attempt and\n"
    "job, reads required contexts from the live develop ruleset, and reports per\n"
    "workflow and event: run conclusions, rerun outcomes, all-attempt runner\n"
    "minutes, and job created-to-started queue time, plus PR time-to-green and\n"
    "post-merge signal quality. Any retrieval gap marks the report INCOMPLETE and\n"
    "exits 3. Immutable responses are cached under --cache-dir so a rerun resumes.\n"
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


def _gh_text(path: str) -> str | None:
    for attempt in range(API_RETRY_ATTEMPTS):
        try:
            proc = subprocess.run(
                ["gh", "api", "--allow-escape-sequences", path],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0:
            return proc.stdout
        if attempt + 1 < API_RETRY_ATTEMPTS:
            time.sleep(0.5 * (attempt + 1))
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _text_once(req: urllib.request.Request, on_headers: Callable[[object], None] | None) -> tuple[str | None, bool]:
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as resp:
            if on_headers is not None:
                on_headers(resp.headers)
            return resp.read().decode("utf-8", "replace"), True
    except urllib.error.HTTPError as err:
        if on_headers is not None:
            on_headers(err.headers)
        location = err.headers.get("Location")
        if err.code in (301, 302, 303, 307, 308) and location:
            blob = urllib.request.Request(location, headers={"User-Agent": "benchbox-dev-loop-pr-metrics"})
            with urllib.request.urlopen(blob, timeout=120) as resp:
                return resp.read().decode("utf-8", "replace"), True
        return None, err.code in (404, 410)


def _urllib_text(
    path: str,
    token: str,
    *,
    attempts: int = API_RETRY_ATTEMPTS,
    on_headers: Callable[[object], None] | None = None,
) -> str | None:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "benchbox-dev-loop-pr-metrics",
        },
    )
    for attempt in range(attempts):
        try:
            text, final = _text_once(req, on_headers)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
            text, final = None, False
        if text is not None or final:
            return text
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    return None


def _urllib_api(
    path: str,
    token: str,
    *,
    attempts: int = API_RETRY_ATTEMPTS,
    on_headers: Callable[[object], None] | None = None,
) -> object | None:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "benchbox-dev-loop-pr-metrics",
        },
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if on_headers is not None:
                    on_headers(resp.headers)
                try:
                    return json.load(resp)
                except ValueError:
                    return None
        except urllib.error.HTTPError as err:
            if on_headers is not None:
                on_headers(err.headers)
            if err.code == 404:
                return None
            if err.code in (403, 429):
                if err.headers.get("x-ratelimit-remaining") == "0":
                    return None
                retry_after = err.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 120))
                    continue
            if attempt == attempts - 1:
                return None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
            if attempt == attempts - 1:
                return None
        time.sleep(0.5 * (attempt + 1))
    return None


class ApiFailure(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, use_gh: bool, token: str | None) -> None:
        self.repo = repo
        self.use_gh = use_gh
        self.token = token
        self.attempts = API_RETRY_ATTEMPTS
        self.rate_reserve = 0
        self.rate_remaining: int | None = None
        self.rate_reset: int | None = None
        self.waited_seconds = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def _observe(self, headers: Any) -> None:
        try:
            remaining = int(headers.get("x-ratelimit-remaining"))
            reset = int(headers.get("x-ratelimit-reset"))
        except (AttributeError, TypeError, ValueError):
            return
        with self._lock:
            self.rate_remaining = remaining
            self.rate_reset = reset

    def _wait_for_budget(self) -> None:
        with self._lock:
            remaining, reset = self.rate_remaining, self.rate_reset
        if remaining is None or reset is None or remaining > max(self.rate_reserve, 0):
            return
        wait = reset - time.time() + 1
        if wait > 0:
            time.sleep(wait)
            with self._lock:
                self.waited_seconds += wait
        with self._lock:
            self.rate_remaining = None

    def _request(self, path: str) -> object | None:
        if self.use_gh:
            return _gh_api(path)
        if self.token:
            return _urllib_api(path, self.token, attempts=self.attempts, on_headers=self._observe)
        return None

    def get(self, path: str) -> object | None:
        with self._lock:
            self.calls += 1
        for _ in range(3):
            self._wait_for_budget()
            data = self._request(path)
            if data is not None or self.rate_remaining != 0:
                return data
        return None

    def get_text(self, path: str) -> str | None:
        with self._lock:
            self.calls += 1
        for _ in range(3):
            self._wait_for_budget()
            if self.use_gh:
                return _gh_text(path)
            if not self.token:
                return None
            text = _urllib_text(path, self.token, attempts=self.attempts, on_headers=self._observe)
            if text is not None or self.rate_remaining != 0:
                return text
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
    for count_name in ("candidate_prs", "collected_prs"):
        value = cohort.get(count_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"lifecycle cohort {count_name} must be a nonnegative integer")
        else:
            counts[count_name] = value

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


USAGE_REPORT_SCHEMA = "ci_usage_report_v1"
RUN_SEARCH_CAP = 1000
RUN_PAGE_SIZE = 100
USAGE_API_ATTEMPTS = 5
USAGE_DEFAULT_WORKERS = 4
USAGE_DEFAULT_RATE_RESERVE = 1000
USAGE_PROGRESS_EVERY = 500
TRUNK_WORKFLOWS = ("trunk.yml", "trunk-coverage.yml")
TRUNK_COVERAGE_WORKFLOW = "trunk-coverage.yml"
TRUNK_COVERAGE_PRODUCER_JOB = "medium-coverage-map"
TRUNK_COVERAGE_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
RED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
GREEN_CHECK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
TRUNK_MISSING_GRACE = timedelta(minutes=30)
TEXT_EDIT_DISPLAY_TITLE = "CI (text edit)"
CI_WORKFLOW = "ci.yml"


def format_api_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_report_window(since: str, until: str, now: datetime) -> tuple[datetime, datetime]:
    def parse(text: str, end_of_day: bool) -> datetime:
        text = text.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            return day + timedelta(days=1, seconds=-1) if end_of_day else day
        parsed = _iso_to_dt(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0)

    start = parse(since, end_of_day=False)
    end = min(parse(until, end_of_day=True), now.astimezone(timezone.utc).replace(microsecond=0))
    if end < start:
        raise ValueError(f"--until {end.isoformat()} is before --since {start.isoformat()}")
    return start, end


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = _iso_to_dt(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DiskCache:
    def __init__(self, root: Path | None) -> None:
        self.root = root
        self.hits = 0
        self.writes = 0
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        import hashlib

        assert self.root is not None
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str) -> object | None:
        if self.root is None:
            return None
        try:
            payload = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("key") != key:
            return None
        with self._lock:
            self.hits += 1
        return payload.get("value")

    def put(self, key: str, value: object) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(key)
        temp = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"key": key, "value": value}), encoding="utf-8")
        os.replace(temp, target)
        with self._lock:
            self.writes += 1


@dataclass
class UsageCollection:
    repo: str
    since: datetime
    until: datetime
    collected_at: datetime
    runs: list[dict] = field(default_factory=list)
    jobs_by_run: dict[int, list[dict]] = field(default_factory=dict)
    attempts_by_run: dict[int, dict[int, dict]] = field(default_factory=dict)
    prs: list[dict] = field(default_factory=list)
    head_data: dict[str, dict] = field(default_factory=dict)
    develop_commits: list[dict] | None = None
    workflow_created: dict[str, datetime] = field(default_factory=dict)
    heavy_decisions: dict[int, dict] = field(default_factory=dict)
    required: dict | None = None
    effective_from_override: dict[str, datetime] = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def gap(self, scope: str, detail: str, **extra: object) -> None:
        self.gaps.append({"scope": scope, "detail": detail, **extra})

    def bump(self, name: str, amount: int = 1) -> None:
        self.stats[name] = self.stats.get(name, 0) + amount


def _get_object_pages(client: GitHubClient, path: str, item_key: str) -> tuple[list[dict], int | None]:
    sep = "&" if "?" in path else "?"
    items: list[dict] = []
    total: int | None = None
    page = 1
    while True:
        data = client.get(f"{path}{sep}per_page={RUN_PAGE_SIZE}&page={page}")
        if not isinstance(data, dict) or not isinstance(data.get(item_key), list):
            raise ApiFailure(f"GitHub API page fetch failed: {path} (page {page})")
        if total is None and isinstance(data.get("total_count"), int):
            total = int(data["total_count"])
        batch = data[item_key]
        items.extend(batch)
        if len(batch) < RUN_PAGE_SIZE or (total is not None and len(items) >= total):
            return items, total
        page += 1


def _get_list_pages(client: GitHubClient, path: str) -> list[dict]:
    sep = "&" if "?" in path else "?"
    items: list[dict] = []
    page = 1
    while True:
        data = client.get(f"{path}{sep}per_page={RUN_PAGE_SIZE}&page={page}")
        if not isinstance(data, list):
            raise ApiFailure(f"GitHub API page fetch failed: {path} (page {page})")
        items.extend(data)
        if len(data) < RUN_PAGE_SIZE:
            return items
        page += 1


def fetch_required_contexts(client: GitHubClient, branch: str, now: datetime) -> dict:
    endpoint = f"/repos/{client.repo}/rules/branches/{branch}"
    try:
        data = _get_list_pages(client, endpoint)
    except ApiFailure as exc:
        raise ApiFailure(f"required-context ruleset fetch failed: {endpoint} ({exc})") from exc
    contexts: list[str] = []
    integration_ids: dict[str, int | None] = {}
    ruleset_ids: list[int] = []
    for rule in data:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        if rule.get("ruleset_id") is not None and rule["ruleset_id"] not in ruleset_ids:
            ruleset_ids.append(rule["ruleset_id"])
        for entry in (rule.get("parameters") or {}).get("required_status_checks") or []:
            name = str(entry.get("context") or "")
            if name and name not in contexts:
                contexts.append(name)
                integration_ids[name] = entry.get("integration_id")
    if not contexts:
        raise ApiFailure(f"no required_status_checks rule returned by {endpoint}")
    return {
        "contexts": contexts,
        "source": "live_ruleset",
        "endpoint": endpoint,
        "ruleset_ids": ruleset_ids,
        "integration_ids": integration_ids,
        "fetched_at": format_api_timestamp(now),
    }


def collect_runs(client: GitHubClient, collection: UsageCollection) -> None:
    runs: dict[int, dict] = {}
    pending = [(collection.since, collection.until)]
    while pending:
        low, high = pending.pop()
        base = f"/repos/{client.repo}/actions/runs?created={format_api_timestamp(low)}..{format_api_timestamp(high)}"
        first = client.get(f"{base}&per_page={RUN_PAGE_SIZE}&page=1")
        window = {"from": format_api_timestamp(low), "to": format_api_timestamp(high)}
        if not isinstance(first, dict) or not isinstance(first.get("total_count"), int):
            collection.gap("run_partition", "partition request failed; runs in this interval are missing", **window)
            continue
        total = int(first["total_count"])
        if total >= RUN_SEARCH_CAP and high > low:
            midpoint = low + timedelta(seconds=int((high - low).total_seconds()) // 2)
            pending.append((midpoint + timedelta(seconds=1), high))
            pending.append((low, midpoint))
            collection.bump("partition_splits")
            continue
        collection.bump("partitions")
        batch = list(first.get("workflow_runs") or [])
        failed_page = False
        for page in range(2, -(-total // RUN_PAGE_SIZE) + 1):
            data = client.get(f"{base}&per_page={RUN_PAGE_SIZE}&page={page}")
            if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
                collection.gap("run_partition", f"page {page} request failed", **window)
                failed_page = True
                break
            batch.extend(data["workflow_runs"])
        seen_here: set[int] = set()
        for run in batch:
            if not isinstance(run, dict) or not isinstance(run.get("id"), int):
                continue
            seen_here.add(run["id"])
            if run["id"] in runs:
                collection.bump("duplicate_runs_dropped")
                continue
            runs[run["id"]] = run
        if total >= RUN_SEARCH_CAP:
            collection.gap(
                "run_partition",
                f"one-second interval holds {total} runs, at or above the {RUN_SEARCH_CAP}-result cap",
                **window,
            )
        elif len(seen_here) < total and not failed_page:
            collection.gap(
                "run_partition", f"retrieved {len(seen_here)} of {total} runs reported for the interval", **window
            )
    collection.runs = [runs[key] for key in sorted(runs)]
    collection.stats["runs_collected"] = len(collection.runs)


def _fetch_run_jobs(client: GitHubClient, run_id: int) -> list[dict]:
    jobs, total = _get_object_pages(client, f"/repos/{client.repo}/actions/runs/{run_id}/jobs?filter=all", "jobs")
    unique = {job["id"]: job for job in jobs if isinstance(job, dict) and "id" in job}
    if total is not None and len(unique) < total:
        raise ApiFailure(f"run {run_id}: retrieved {len(unique)} of {total} jobs")
    return list(unique.values())


def _collect_one_run(
    client: GitHubClient, cache: DiskCache, run: dict
) -> tuple[list[dict], dict[int, dict], list[str]]:
    run_id = int(run["id"])
    latest = int(run.get("run_attempt") or 1)
    immutable = run.get("status") == "completed"
    errors: list[str] = []
    jobs_key = f"jobs:{client.repo}:{run_id}:{latest}:{run.get('updated_at')}"
    jobs = cache.get(jobs_key) if immutable else None
    if not isinstance(jobs, list):
        try:
            jobs = _fetch_run_jobs(client, run_id)
        except ApiFailure as exc:
            errors.append(f"jobs: {exc}")
            jobs = []
        else:
            if immutable:
                cache.put(jobs_key, jobs)
    attempts: dict[int, dict] = {}
    for number in range(1, latest):
        key = f"attempt:{client.repo}:{run_id}:{number}"
        cached = cache.get(key)
        if isinstance(cached, dict):
            attempts[number] = cached
            continue
        data = client.get(f"/repos/{client.repo}/actions/runs/{run_id}/attempts/{number}")
        if not isinstance(data, dict):
            errors.append(f"attempt {number}: request failed")
            continue
        attempts[number] = data
        cache.put(key, data)
    return jobs, attempts, errors


def collect_run_details(client: GitHubClient, collection: UsageCollection, cache: DiskCache, workers: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def work(run: dict) -> tuple[int, list[dict], dict[int, dict], list[str]]:
        jobs, attempts, errors = _collect_one_run(client, cache, run)
        return int(run["id"]), jobs, attempts, errors

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for run_id, jobs, attempts, errors in pool.map(work, collection.runs):
            collection.jobs_by_run[run_id] = jobs
            collection.attempts_by_run[run_id] = attempts
            for error in errors:
                collection.gap("run_detail", error, run_id=run_id)
            done += 1
            if done % USAGE_PROGRESS_EVERY == 0:
                print(f"  collected jobs and attempts for {done}/{len(collection.runs)} runs", file=sys.stderr)
    collection.stats["runs_in_flight"] = sum(1 for run in collection.runs if run.get("status") != "completed")


def collect_develop_commits(client: GitHubClient, collection: UsageCollection) -> None:
    since = format_api_timestamp(collection.since)
    until = format_api_timestamp(collection.until)
    try:
        collection.develop_commits = _get_list_pages(
            client, f"/repos/{client.repo}/commits?sha=develop&since={since}&until={until}"
        )
    except ApiFailure as exc:
        collection.gap("develop_commits", str(exc))


def collect_workflow_metadata(client: GitHubClient, collection: UsageCollection) -> None:
    for workflow in TRUNK_WORKFLOWS:
        data = client.get(f"/repos/{client.repo}/actions/workflows/{workflow}")
        created = _parse_ts(data.get("created_at")) if isinstance(data, dict) else None
        if created is None:
            collection.gap("workflow_metadata", f"creation time of {workflow} unavailable", workflow=workflow)
        else:
            collection.workflow_created[workflow] = created


def collect_merged_prs(client: GitHubClient, collection: UsageCollection) -> None:
    try:
        prs = fetch_merged_prs(client, collection.since)
    except ApiFailure as exc:
        collection.gap("merged_prs", str(exc))
        return
    collection.prs = [pr for pr in prs if collection.since <= _iso_to_dt(pr["merged_at"]) <= collection.until]


def _run_belongs_to_head(run: dict, ref: str, repo: str | None) -> bool:
    if run.get("event") != "pull_request" or run.get("head_branch") != ref or not run.get("head_sha"):
        return False
    run_repo = (run.get("head_repository") or {}).get("full_name")
    return not (repo and run_repo and str(run_repo).lower() != repo.lower())


def pr_heads(pr: dict, runs: list[dict]) -> list[tuple[str, datetime | None]]:
    ref = str((pr.get("head") or {}).get("ref") or "")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    opened = _parse_ts(pr.get("created_at"))
    merged = _parse_ts(pr.get("merged_at"))
    final = str((pr.get("head") or {}).get("sha") or "")
    seen: dict[str, datetime] = {}
    for run in runs:
        if not _run_belongs_to_head(run, ref, head_repo):
            continue
        created = _parse_ts(run.get("created_at"))
        if created is None or opened is None or merged is None:
            continue
        if created < opened - timedelta(minutes=2) or created > merged:
            continue
        sha = str(run["head_sha"])
        if sha not in seen or created < seen[sha]:
            seen[sha] = created
    ordered: list[tuple[str, datetime | None]] = [(sha, seen[sha]) for sha in sorted(seen, key=lambda sha: seen[sha])]
    if final and final not in seen:
        ordered.append((final, None))
    return ordered


def pr_head_shas(pr: dict, runs: list[dict]) -> list[str]:
    return [sha for sha, _ in pr_heads(pr, runs)]


def _collect_head(client: GitHubClient, cache: DiskCache, sha: str, cacheable: bool) -> dict | None:
    key = f"head:{client.repo}:{sha}"
    cached = cache.get(key) if cacheable else None
    if isinstance(cached, dict):
        return cached
    try:
        check_runs, _ = _get_object_pages(client, f"/repos/{client.repo}/commits/{sha}/check-runs", "check_runs")
        statuses = _get_list_pages(client, f"/repos/{client.repo}/commits/{sha}/statuses")
    except ApiFailure:
        return None
    data = {"check_runs": check_runs, "statuses": statuses}
    if cacheable:
        cache.put(key, data)
    return data


def collect_head_data(client: GitHubClient, collection: UsageCollection, cache: DiskCache, workers: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    stable_before = collection.collected_at - timedelta(hours=1)
    shas: dict[str, bool] = {}
    for pr in collection.prs:
        merged = _parse_ts(pr.get("merged_at"))
        cacheable = merged is not None and merged < stable_before
        for sha in pr_head_shas(pr, collection.runs):
            shas[sha] = shas.get(sha, True) and cacheable
    items = sorted(shas.items())
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda item: _collect_head(client, cache, item[0], item[1]), items))
    for (sha, _), data in zip(items, results, strict=True):
        if data is None:
            collection.gap("head_checks", "check-run or status fetch failed", sha=sha)
        else:
            collection.head_data[sha] = data


HEAVY_REASON_CLASSES: tuple[tuple[str, str], ...] = (
    ("(test-infrastructure carve-out)", "test_infrastructure_carve_out"),
    ("(coverage fail-closed carve-out)", "coverage_fail_closed_carve_out"),
    ("(packaging carve-out)", "packaging_carve_out"),
    ("(soundness carve-out)", "soundness_carve_out"),
    ("lookup failed closed", "lookup_failed_closed"),
    ("not a code-routed tree", "not_code_routed"),
    ("skip the heavy tier", "pull_request_skips_heavy_tier"),
)
_LOG_TIMESTAMP = re.compile(r"^\S+Z ?")


def parse_heavy_decision(log: str) -> dict | None:
    lines = [_LOG_TIMESTAMP.sub("", line) for line in log.splitlines()]
    start = next((i for i, line in enumerate(lines) if "scripts/heavy_tier_needed.py" in line), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("##[endgroup]")), None)
    if end is None:
        return None
    flag = lines[end + 1].strip() if end + 1 < len(lines) else ""
    if flag not in ("heavy-needed=true", "heavy-needed=false"):
        return None
    reason = lines[end + 2].strip() if end + 2 < len(lines) else ""
    reason_class = next((name for needle, name in HEAVY_REASON_CLASSES if needle in reason), "other")
    return {"heavy_needed": flag.endswith("true"), "class": reason_class, "reason": reason[:300]}


def _collect_heavy_decision(client: GitHubClient, cache: DiskCache, job_id: int) -> dict | str:
    key = f"heavy-decision:{client.repo}:{job_id}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached
    log = client.get_text(f"/repos/{client.repo}/actions/jobs/{job_id}/logs")
    if log is None:
        return "log request failed"
    decision = parse_heavy_decision(log)
    if decision is None:
        return "heavy-tier decision not found in the ci-paths log"
    cache.put(key, decision)
    return decision


def _heavy_decision_job(run: dict, jobs: list[dict]) -> int | None:
    if workflow_name(run) != CI_WORKFLOW or run.get("event") != "pull_request":
        return None
    if run.get("display_title") == TEXT_EDIT_DISPLAY_TITLE:
        return None
    for job in jobs:
        if (
            job.get("name") == "ci-paths"
            and int(job.get("run_attempt") or 1) == 1
            and classify_job(job, None) == "executed"
        ):
            return int(job["id"])
    return None


def collect_heavy_decisions(client: GitHubClient, collection: UsageCollection, cache: DiskCache, workers: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    targets = [
        (int(run["id"]), job_id)
        for run in collection.runs
        if (job_id := _heavy_decision_job(run, collection.jobs_by_run.get(int(run["id"]), []))) is not None
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda item: _collect_heavy_decision(client, cache, item[1]), targets))
    for (run_id, job_id), result in zip(targets, results, strict=True):
        if isinstance(result, dict):
            collection.heavy_decisions[run_id] = result
        else:
            collection.gap("heavy_decision", result, run_id=run_id, job_id=job_id)


def collect_usage(
    client: GitHubClient,
    *,
    since: datetime,
    until: datetime,
    now: datetime,
    cache: DiskCache,
    workers: int,
    required_override: list[str] | None = None,
    effective_from_override: dict[str, datetime] | None = None,
    branch: str = "develop",
) -> UsageCollection:
    collection = UsageCollection(repo=client.repo, since=since, until=until, collected_at=now)
    collection.effective_from_override = dict(effective_from_override or {})
    if required_override:
        collection.required = {
            "contexts": list(required_override),
            "source": "command_line_override",
            "endpoint": None,
            "ruleset_ids": [],
            "integration_ids": {},
            "fetched_at": format_api_timestamp(now),
        }
    else:
        try:
            collection.required = fetch_required_contexts(client, branch, now)
        except ApiFailure as exc:
            collection.gap("required_contexts", str(exc))
    collect_runs(client, collection)
    collect_run_details(client, collection, cache, workers)
    collect_develop_commits(client, collection)
    collect_workflow_metadata(client, collection)
    collect_merged_prs(client, collection)
    collect_head_data(client, collection, cache, workers)
    collect_heavy_decisions(client, collection, cache, workers)
    collection.stats["api_calls"] = getattr(client, "calls", 0)
    collection.stats["rate_limit_wait_seconds"] = int(getattr(client, "waited_seconds", 0))
    collection.stats["cache_hits"] = cache.hits
    return collection


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p90": round(float(_pct(values, 90) or 0.0), 3),
        "max": round(max(values), 3),
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return format_api_timestamp(value) if value is not None else None


def workflow_name(run: dict) -> str:
    path = str(run.get("path") or "").split("@", 1)[0]
    base = path.rsplit("/", 1)[-1]
    return base or str(run.get("name") or "unknown")


def job_runner_os(job: dict) -> str:
    labels = " ".join(str(label).lower() for label in job.get("labels") or [])
    for needle, name in (
        ("windows", "windows"),
        ("macos", "macos"),
        ("mac", "macos"),
        ("ubuntu", "linux"),
        ("linux", "linux"),
    ):
        if needle in labels:
            return name
    return "unknown"


def classify_job(job: dict, attempt_start: datetime | None) -> str:
    if job.get("conclusion") == "skipped":
        return "skipped"
    started = _parse_ts(job.get("started_at"))
    completed = _parse_ts(job.get("completed_at"))
    created = _parse_ts(job.get("created_at"))
    if job.get("status") not in (None, "completed") or started is None or completed is None:
        return "in_progress"
    if created is not None and started < created:
        return "reused"
    if created is None and attempt_start is not None and started < attempt_start:
        return "reused"
    if job.get("conclusion") == "cancelled" and "steps" in job and not job["steps"]:
        return "cancelled_before_start"
    return "executed"


def _derived_conclusion(jobs: list[dict]) -> str | None:
    conclusions = {job.get("conclusion") for job in jobs}
    if conclusions & RED_CONCLUSIONS:
        return "failure"
    if "cancelled" in conclusions:
        return "cancelled"
    if "success" in conclusions:
        return "success"
    return None


def _minutes_bucket(conclusion: object) -> str:
    if conclusion in ("success", "neutral"):
        return "completed"
    if conclusion in ("failure", "timed_out"):
        return "failed"
    if conclusion == "cancelled":
        return "cancelled"
    return "other"


def workload_class(run: dict, workflow: str, active_names: set[str]) -> str:
    if workflow != CI_WORKFLOW:
        return "not_ci_workflow"
    if run.get("display_title") == TEXT_EDIT_DISPLAY_TITLE:
        return "ci_text_edit"
    if "medium-collect" in active_names:
        return "ci_heavy_tier"
    if any(name.startswith("TPC-H binary framing") for name in active_names):
        return "ci_framing_only"
    if "lint" in active_names or any(name.startswith("test (") for name in active_names):
        return "ci_code"
    return "ci_non_code"


def run_facts(run: dict, jobs: list[dict], attempt_runs: dict[int, dict]) -> dict:
    latest = int(run.get("run_attempt") or 1)
    by_attempt: dict[int, list[dict]] = {number: [] for number in range(1, latest + 1)}
    for job in jobs:
        by_attempt.setdefault(int(job.get("run_attempt") or latest), []).append(job)
    minutes = {"completed": 0.0, "failed": 0.0, "cancelled": 0.0, "other": 0.0}
    os_minutes: dict[str, float] = {}
    queue: list[float] = []
    kinds: dict[str, int] = {}
    active_names: set[str] = set()
    attempts: list[dict] = []
    derived_attempts = 0
    for number in sorted(by_attempt):
        source = run if number == latest else attempt_runs.get(number)
        attempt_jobs = by_attempt[number]
        conclusion = (source or {}).get("conclusion")
        if source is None:
            conclusion = _derived_conclusion(attempt_jobs)
            derived_attempts += 1
        start = _parse_ts((source or {}).get("run_started_at") or (source or {}).get("created_at"))
        attempts.append(
            {
                "attempt": number,
                "conclusion": conclusion,
                "ended_at": _parse_ts((source or {}).get("updated_at")),
                "derived": source is None,
            }
        )
        for job in attempt_jobs:
            kind = classify_job(job, start)
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind != "skipped":
                active_names.add(str(job.get("name") or ""))
            if kind != "executed":
                continue
            started = _parse_ts(job["started_at"])
            completed = _parse_ts(job["completed_at"])
            created = _parse_ts(job.get("created_at"))
            assert started is not None and completed is not None
            job_minutes = max(0.0, (completed - started).total_seconds()) / 60.0
            minutes[_minutes_bucket(job.get("conclusion"))] += job_minutes
            runner_os = job_runner_os(job)
            os_minutes[runner_os] = os_minutes.get(runner_os, 0.0) + job_minutes
            if created is not None:
                queue.append((started - created).total_seconds())
    workflow = workflow_name(run)
    first = attempts[0]["conclusion"] if attempts else None
    return {
        "id": run.get("id"),
        "workflow": workflow,
        "event": str(run.get("event") or "unknown"),
        "head_sha": str(run.get("head_sha") or ""),
        "head_branch": run.get("head_branch"),
        "head_commit_timestamp": _parse_ts((run.get("head_commit") or {}).get("timestamp")),
        "created_at": _parse_ts(run.get("created_at")),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "latest_attempt": latest,
        "first_attempt_conclusion": first,
        "attempts": attempts,
        "derived_attempts": derived_attempts,
        "minutes": minutes,
        "minutes_total": sum(minutes.values()),
        "os_minutes": os_minutes,
        "queue_seconds": queue,
        "job_kinds": kinds,
        "active_job_names": sorted(active_names),
        "class": workload_class(run, workflow, active_names),
    }


def _summarize_facts(group: list[dict]) -> dict:
    conclusions: dict[str, int] = {}
    attempts: dict[str, int] = {}
    rerun_runs = passed_only_on_rerun = failed_after_rerun = 0
    first_conclusions: dict[str, int] = {}
    rerun_attempt_outcomes: dict[str, dict[str, int]] = {}
    minutes = {"completed": 0.0, "failed": 0.0, "cancelled": 0.0, "other": 0.0}
    queue: list[float] = []
    kinds: dict[str, int] = {}
    for facts in group:
        final = str(facts["conclusion"] or facts["status"] or "unknown")
        conclusions[final] = conclusions.get(final, 0) + 1
        attempts[str(facts["latest_attempt"])] = attempts.get(str(facts["latest_attempt"]), 0) + 1
        first = str(facts["first_attempt_conclusion"] or "unknown")
        first_conclusions[first] = first_conclusions.get(first, 0) + 1
        if facts["latest_attempt"] > 1:
            rerun_runs += 1
            if facts["conclusion"] == "success" and facts["first_attempt_conclusion"] != "success":
                passed_only_on_rerun += 1
            elif facts["conclusion"] != "success":
                failed_after_rerun += 1
            for attempt in facts["attempts"][1:]:
                outcome = str(attempt["conclusion"] or "unknown")
                bucket = rerun_attempt_outcomes.setdefault(str(attempt["attempt"]), {})
                bucket[outcome] = bucket.get(outcome, 0) + 1
        for name, value in facts["minutes"].items():
            minutes[name] += value
        queue.extend(facts["queue_seconds"])
        for name, count in facts["job_kinds"].items():
            kinds[name] = kinds.get(name, 0) + count
    return {
        "runs": len(group),
        "conclusions": dict(sorted(conclusions.items())),
        "attempt_counts": dict(sorted(attempts.items())),
        "first_attempt_conclusions": dict(sorted(first_conclusions.items())),
        "rerun_runs": rerun_runs,
        "passed_only_on_rerun": passed_only_on_rerun,
        "failed_after_rerun": failed_after_rerun,
        "rerun_attempt_outcomes": rerun_attempt_outcomes,
        "runner_minutes": {
            **{key: round(value, 2) for key, value in minutes.items()},
            "total": round(sum(minutes.values()), 2),
        },
        "runner_minutes_per_run": _distribution([facts["minutes_total"] for facts in group]),
        "job_queue_seconds": _distribution(queue),
        "job_kinds": dict(sorted(kinds.items())),
    }


def build_workflow_tables(facts: list[dict]) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in facts:
        groups.setdefault((item["workflow"], item["event"]), []).append(item)
    rows = [
        {"workflow": workflow, "event": event, **_summarize_facts(group)}
        for (workflow, event), group in sorted(groups.items())
    ]
    return {"per_workflow_event": rows, "overall": _summarize_facts(facts)}


def _workload_table(groups: dict[str, list[dict]]) -> dict[str, dict]:
    return {
        name: {
            "runs": len(group),
            "runner_minutes": round(sum(item["minutes_total"] for item in group), 2),
            "cancelled_runner_minutes": round(sum(item["minutes"]["cancelled"] for item in group), 2),
            "runner_minutes_per_run": _distribution([item["minutes_total"] for item in group]),
        }
        for name, group in sorted(groups.items())
    }


def build_workload_mix(facts: list[dict], heavy_decisions: dict[int, dict]) -> dict:
    by_class: dict[str, list[dict]] = {}
    by_event: dict[str, list[dict]] = {}
    by_reason: dict[str, list[dict]] = {}
    os_minutes: dict[str, float] = {}
    for item in facts:
        by_class.setdefault(item["class"], []).append(item)
        by_event.setdefault(item["event"], []).append(item)
        for name, value in item["os_minutes"].items():
            os_minutes[name] = os_minutes.get(name, 0.0) + value
        if item["class"] not in ("not_ci_workflow", "ci_text_edit") and item["event"] == "pull_request":
            decision = heavy_decisions.get(int(item["id"]))
            key = (
                f"{'heavy' if decision['heavy_needed'] else 'light'}:{decision['class']}"
                if decision
                else "not_collected"
            )
            by_reason.setdefault(key, []).append(item)
    total = sum(item["minutes_total"] for item in facts)
    cancelled = sum(item["minutes"]["cancelled"] for item in facts)
    return {
        "by_class": _workload_table(by_class),
        "by_event": _workload_table(by_event),
        "by_heavy_tier_decision": _workload_table(by_reason),
        "by_runner_os_minutes": {name: round(value, 2) for name, value in sorted(os_minutes.items())},
        "cancelled_runner_minutes": round(cancelled, 2),
        "cancelled_share": round(cancelled / total, 4) if total else None,
        "class_definitions": {
            "ci_text_edit": "CI run whose display title is 'CI (text edit)'",
            "ci_heavy_tier": "CI run in which medium-collect executed in any attempt",
            "ci_framing_only": "CI run without medium-collect in which a TPC-H binary framing job executed",
            "ci_code": "CI run with the lint job or a test job executed and no heavy tier",
            "ci_non_code": "other CI runs",
            "not_ci_workflow": "every workflow other than ci.yml",
            "heavy_tier_decision": (
                "heavy-needed flag and reason class parsed from the first attempt's ci-paths job log for pull_request "
                "CI runs; not_collected means the run had no executed ci-paths job"
            ),
        },
    }


def _context_events(head: dict, name: str) -> list[tuple[datetime, bool, str]]:
    events: list[tuple[datetime, bool, str]] = []
    for check in head.get("check_runs") or []:
        if check.get("name") != name:
            continue
        if check.get("status") == "completed":
            stamp = _parse_ts(check.get("completed_at"))
            good = check.get("conclusion") in GREEN_CHECK_CONCLUSIONS
        else:
            stamp = _parse_ts(check.get("started_at"))
            good = False
        if stamp is not None:
            events.append((stamp, good, "check_run"))
    for status in head.get("statuses") or []:
        if status.get("context") != name:
            continue
        stamp = _parse_ts(status.get("created_at"))
        if stamp is not None:
            events.append((stamp, status.get("state") == "success", "status"))
    return sorted(events, key=lambda event: event[0])


def evaluate_context(head: dict, name: str) -> dict:
    events = _context_events(head, name)
    if not events:
        return {"state": "missing", "green_since": None, "source": None}
    if not events[-1][1]:
        return {"state": "not_green", "green_since": None, "source": events[-1][2]}
    index = len(events) - 1
    while index > 0 and events[index - 1][1]:
        index -= 1
    return {"state": "green", "green_since": events[index][0], "source": events[index][2]}


def context_policy(collection: UsageCollection) -> dict:
    required = collection.required
    if required is None:
        return {"available": False}
    head_updates: dict[str, datetime] = {}
    for pr in collection.prs:
        for sha, updated in pr_heads(pr, collection.runs):
            if updated is not None:
                head_updates[sha] = updated
    first_seen: dict[str, datetime] = {}
    sources: dict[str, set[str]] = {}
    for sha, head in collection.head_data.items():
        for name in required["contexts"]:
            events = _context_events(head, name)
            if events:
                stamp = head_updates.get(sha, events[0][0])
                first_seen[name] = min(first_seen.get(name, stamp), stamp)
                sources.setdefault(name, set()).update(event[2] for event in events)
    effective: dict[str, datetime | None] = {}
    for name in required["contexts"]:
        effective[name] = collection.effective_from_override.get(name) or first_seen.get(name)
    return {
        "available": True,
        "contexts": list(required["contexts"]),
        "source": required["source"],
        "endpoint": required["endpoint"],
        "ruleset_ids": required["ruleset_ids"],
        "integration_ids": required["integration_ids"],
        "fetched_at": required["fetched_at"],
        "applicability_rule": (
            "a required context applies to a head only if the head update is at or after the context's effective-from "
            "time: the command-line override when given, else the earliest observation of that context on any head of "
            "the cohort; a context never observed in the cohort applies to every head"
        ),
        "effective_from": {name: _iso_or_none(value) for name, value in effective.items()},
        "effective_from_basis": {
            name: "override"
            if name in collection.effective_from_override
            else ("first_observation" if name in first_seen else "never_observed")
            for name in required["contexts"]
        },
        "observed_sources": {name: sorted(values) for name, values in sources.items()},
        "ci_compute_contexts_rule": "applicable contexts observed as check runs; commit-status contexts are reported as all-required only",
        "_effective": effective,
        "_sources": sources,
    }


def _head_row(sha: str, final: bool, head_update: datetime | None, data: dict | None, policy: dict) -> dict:
    row: dict = {
        "sha": sha,
        "final": final,
        "head_update_at": _iso_or_none(head_update),
        "head_update_source": "first pull_request workflow run created_at for this head (proxy)"
        if head_update
        else None,
    }
    if data is None:
        return {
            **row,
            "status": "no_check_data",
            "_head_update": head_update,
            "_all_ready": None,
            "_ci_ready": None,
        }
    applicable: list[str] = []
    excluded: list[str] = []
    for name in policy["contexts"]:
        effective = policy["_effective"].get(name)
        if effective is None or head_update is None or head_update >= effective:
            applicable.append(name)
        else:
            excluded.append(name)
    results = {name: evaluate_context(data, name) for name in applicable}
    pending = [name for name, result in results.items() if result["state"] != "green"]
    ci_names = [name for name in applicable if "check_run" in policy["_sources"].get(name, set())]
    row.update(
        applicable_contexts=applicable,
        excluded_contexts=excluded,
        missing_contexts=[name for name, result in results.items() if result["state"] == "missing"],
        not_green_contexts=[name for name, result in results.items() if result["state"] == "not_green"],
        ci_compute_contexts=ci_names,
    )
    ci_ready = None
    all_ready = None
    if not pending and applicable:
        all_ready = max(results[name]["green_since"] for name in applicable)
    if ci_names and all(results[name]["state"] == "green" for name in ci_names):
        ci_ready = max(results[name]["green_since"] for name in ci_names)
    row["ci_compute_ready_at"] = _iso_or_none(ci_ready)
    row["all_required_ready_at"] = _iso_or_none(all_ready)
    if head_update is None:
        row["status"] = "missing_head_observation"
    elif all_ready is None:
        row["status"] = "unresolved" if final else "superseded_before_green"
    elif all_ready < head_update:
        row["status"] = "green_before_observed_update"
    else:
        row["status"] = "green"
        row["seconds_to_all_required_ready"] = (all_ready - head_update).total_seconds()
        if ci_ready is not None and ci_ready >= head_update:
            row["seconds_to_ci_compute_ready"] = (ci_ready - head_update).total_seconds()
    row["_all_ready"] = all_ready
    row["_ci_ready"] = ci_ready
    row["_head_update"] = head_update
    return row


def _censor_first_observed_head(rows: list[dict]) -> None:
    for row in rows:
        if row["_head_update"] is not None:
            row["status"] = "opened_before_window"
            row.pop("seconds_to_all_required_ready", None)
            row.pop("seconds_to_ci_compute_ready", None)
            return


def _pr_time_to_green(rows: list[dict], pre_window: bool) -> dict:
    observed = [row["_head_update"] for row in rows if row["_head_update"] is not None]
    final_row = next((row for row in rows if row["final"]), None)
    out: dict = {"heads": len(rows), "first_head_update_at": _iso_or_none(min(observed)) if observed else None}
    if pre_window:
        out["status"] = "opened_before_window"
    elif final_row is None or not observed:
        out["status"] = "missing_head_observation"
    elif final_row["status"] == "no_check_data":
        out["status"] = "no_check_data"
    elif final_row["_all_ready"] is None:
        out["status"] = "unresolved"
    elif final_row["_all_ready"] < min(observed):
        out["status"] = "green_before_observed_update"
    else:
        out["status"] = "green"
        out["seconds_to_all_required_ready"] = (final_row["_all_ready"] - min(observed)).total_seconds()
        if final_row["_ci_ready"] is not None and final_row["_ci_ready"] >= min(observed):
            out["seconds_to_ci_compute_ready"] = (final_row["_ci_ready"] - min(observed)).total_seconds()
    return out


def build_pr_section(collection: UsageCollection, facts: list[dict]) -> dict:
    policy = context_policy(collection)
    if not policy["available"]:
        return {"available": False, "reason": "required contexts could not be resolved"}
    minutes_by_sha: dict[str, list[dict]] = {}
    for item in facts:
        minutes_by_sha.setdefault(item["head_sha"], []).append(item)
    head_rows: list[dict] = []
    pr_rows: list[dict] = []
    attributed_shas: set[str] = set()
    for pr in collection.prs:
        final_sha = str((pr.get("head") or {}).get("sha") or "")
        rows = [
            _head_row(sha, sha == final_sha, updated, collection.head_data.get(sha), policy)
            for sha, updated in pr_heads(pr, collection.runs)
        ]
        pre_window = (_parse_ts(pr.get("created_at")) or collection.since) < collection.since
        if pre_window:
            _censor_first_observed_head(rows)
        shas = [row["sha"] for row in rows]
        for row in rows:
            head_rows.append({"pr": pr["number"], **{k: v for k, v in row.items() if not k.startswith("_")}})
        pr_row: dict = {
            "number": pr["number"],
            "merged_at": pr.get("merged_at"),
            **_pr_time_to_green(rows, pre_window),
        }
        pr_facts = [item for sha in shas for item in minutes_by_sha.get(sha, [])]
        attributed_shas.update(shas)
        pr_row["lifecycle_runs"] = len(pr_facts)
        pr_row["lifecycle_runner_minutes"] = round(sum(item["minutes_total"] for item in pr_facts), 2)
        pr_row["lifecycle_cancelled_runner_minutes"] = round(sum(item["minutes"]["cancelled"] for item in pr_facts), 2)
        pr_rows.append(pr_row)

    def values(rows: list[dict], key: str) -> list[float]:
        return [row[key] for row in rows if key in row]

    def counts(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            out[row["status"]] = out.get(row["status"], 0) + 1
        return dict(sorted(out.items()))

    attributed = sum(item["minutes_total"] for sha in attributed_shas for item in minutes_by_sha.get(sha, []))
    total_minutes = sum(item["minutes_total"] for item in facts)
    merges = len(collection.prs)
    push_minutes = sum(item["minutes_total"] for item in facts if item["event"] == "push")
    lifecycle = [row["lifecycle_runner_minutes"] for row in pr_rows]
    policy_public = {key: value for key, value in policy.items() if not key.startswith("_")}
    return {
        "available": True,
        "context_policy": policy_public,
        "timestamp_source": "head update is the creation time of the earliest pull_request workflow run for the head (proxy); check and status times are API completed_at and created_at",
        "head_status_counts": counts(head_rows),
        "pr_status_counts": counts(pr_rows),
        "heads": len(head_rows),
        "prs": len(pr_rows),
        "per_head": {
            "seconds_to_ci_compute_ready": _distribution(values(head_rows, "seconds_to_ci_compute_ready")),
            "seconds_to_all_required_ready": _distribution(values(head_rows, "seconds_to_all_required_ready")),
        },
        "per_pr": {
            "seconds_to_ci_compute_ready": _distribution(values(pr_rows, "seconds_to_ci_compute_ready")),
            "seconds_to_all_required_ready": _distribution(values(pr_rows, "seconds_to_all_required_ready")),
            "heads_per_pr": _distribution([float(row["heads"]) for row in pr_rows]),
        },
        "runner_minutes": {
            "merged_prs": merges,
            "pr_lifecycle_per_pr": _distribution(lifecycle),
            "pr_lifecycle_attributed_total": round(attributed, 2),
            "all_runs_total": round(total_minutes, 2),
            "not_on_merged_pr_heads": round(total_minutes - attributed, 2),
            "all_runs_per_merge": round(total_minutes / merges, 2) if merges else None,
            "push_event_per_merge": round(push_minutes / merges, 2) if merges else None,
        },
        "pr_rows": pr_rows,
        "head_rows": head_rows,
    }


def _commit_time(commit: dict) -> datetime | None:
    inner = commit.get("commit") or {}
    return _parse_ts((inner.get("committer") or {}).get("date") or (inner.get("author") or {}).get("date"))


def _trunk_outcomes(runs: list[dict]) -> dict:
    out = {"first_red": 0, "first_known": 0, "eventual_ok": 0, "eventual_known": 0, "cancelled_first": 0}
    results: list[tuple[datetime, str, int, str]] = []
    job_kinds: dict[str, int] = {}
    for item in runs:
        first = item["first_attempt_conclusion"]
        if first in RED_CONCLUSIONS:
            out["first_red"] += 1
            out["first_known"] += 1
        elif first == "success":
            out["first_known"] += 1
        elif first == "cancelled":
            out["cancelled_first"] += 1
        final = item["conclusion"]
        if final == "success":
            out["eventual_ok"] += 1
            out["eventual_known"] += 1
        elif final in RED_CONCLUSIONS:
            out["eventual_known"] += 1
        for name, count in item["job_kinds"].items():
            job_kinds[name] = job_kinds.get(name, 0) + count
        for attempt in item["attempts"]:
            color = (
                "red"
                if attempt["conclusion"] in RED_CONCLUSIONS
                else ("green" if attempt["conclusion"] == "success" else None)
            )
            if color and attempt["ended_at"] is not None:
                results.append((attempt["ended_at"], color, int(item["id"]), item["head_sha"]))
    results.sort(key=lambda entry: entry[0])
    return {**out, "results": results, "job_kinds": dict(sorted(job_kinds.items()))}


def _trunk_incidents(
    results: list[tuple[datetime, str, int, str]],
    commit_times: list[datetime] | None,
    reference: datetime,
) -> list[dict]:
    incidents: list[dict] = []
    current: dict | None = None
    for stamp, color, run_id, sha in results:
        if color == "red":
            if current is None:
                current = {"opened_at": stamp, "first_red_run": run_id, "first_red_sha": sha, "red_results": 0}
            current["red_results"] += 1
        elif current is not None:
            incidents.append({**current, "recovered_at": stamp})
            current = None
    if current is not None:
        incidents.append({**current, "recovered_at": None})
    rows: list[dict] = []
    for incident in incidents:
        recovered = incident["recovered_at"]
        end = recovered or reference
        rows.append(
            {
                "opened_at": _iso_or_none(incident["opened_at"]),
                "first_red_run": incident["first_red_run"],
                "first_red_sha": incident["first_red_sha"],
                "red_results": incident["red_results"],
                "recovered_at": _iso_or_none(recovered),
                "seconds_to_recovery": (recovered - incident["opened_at"]).total_seconds() if recovered else None,
                "ongoing": recovered is None,
                "merges_admitted": None
                if commit_times is None
                else sum(1 for stamp in commit_times if incident["opened_at"] < stamp <= end),
            }
        )
    return rows


def _trunk_sha_coverage(
    commits: list[dict], runs: list[dict], reference: datetime, existed_from: datetime | None
) -> dict:
    by_sha: dict[str, set[int]] = {}
    for item in runs:
        by_sha.setdefault(item["head_sha"], set()).add(int(item["id"]))
    first_run_at = min((item["created_at"] for item in runs if item["created_at"] is not None), default=None)
    missing: list[str] = []
    pending_recent = before_existence = 0
    for commit in commits:
        stamp = _commit_time(commit)
        sha = str(commit.get("sha") or "")
        if sha in by_sha:
            continue
        if stamp is not None and existed_from is not None and stamp < existed_from:
            before_existence += 1
        elif stamp is not None and stamp > reference - TRUNK_MISSING_GRACE:
            pending_recent += 1
        else:
            missing.append(sha)
    commit_shas = {str(commit.get("sha") or "") for commit in commits}
    duplicates = {sha: sorted(ids) for sha, ids in by_sha.items() if len(ids) > 1}
    return {
        "first_run_at": _iso_or_none(first_run_at),
        "workflow_created_at": _iso_or_none(existed_from),
        "missing_exact_sha_runs": len(missing),
        "missing_exact_sha_examples": missing[:20],
        "commits_before_workflow_existed": before_existence,
        "commits_pending_recent": pending_recent,
        "duplicate_exact_sha_runs": len(duplicates),
        "duplicate_exact_sha_examples": dict(list(duplicates.items())[:20]),
        "runs_on_shas_outside_commit_list": sum(1 for sha in by_sha if sha not in commit_shas),
    }


def _is_trunk_signal_run(item: dict, workflow: str) -> bool:
    if item["workflow"] != workflow or item["head_branch"] != "develop":
        return False
    if workflow != TRUNK_COVERAGE_WORKFLOW:
        return item["event"] == "push"
    return item["event"] in TRUNK_COVERAGE_EVENTS and TRUNK_COVERAGE_PRODUCER_JOB in item["active_job_names"]


def _trunk_workflow_signal(collection: UsageCollection, workflow: str, facts: list[dict], reference: datetime) -> dict:
    runs = [item for item in facts if _is_trunk_signal_run(item, workflow)]
    outcomes = _trunk_outcomes(runs)
    commits = collection.develop_commits
    commit_times = (
        None
        if commits is None
        else sorted(stamp for stamp in (_commit_time(commit) for commit in commits) if stamp is not None)
    )
    incident_rows = _trunk_incidents(outcomes["results"], commit_times, reference)
    tested = [
        item["head_commit_timestamp"]
        for item in runs
        if item["head_commit_timestamp"] is not None and item["conclusion"] in ({"success"} | RED_CONCLUSIONS)
    ]
    newest_tested = max(tested) if tested else None
    first_known, eventual_known = outcomes["first_known"], outcomes["eventual_known"]
    return {
        "runs": len(runs),
        "first_attempt_red": outcomes["first_red"],
        "first_attempt_completed": first_known,
        "first_attempt_red_rate": round(outcomes["first_red"] / first_known, 4) if first_known else None,
        "first_attempt_cancelled": outcomes["cancelled_first"],
        "eventual_success": outcomes["eventual_ok"],
        "eventual_completed": eventual_known,
        "eventual_success_rate": round(outcomes["eventual_ok"] / eventual_known, 4) if eventual_known else None,
        "incidents": incident_rows,
        "incident_count": len(incident_rows),
        "seconds_to_recovery": _distribution(
            [row["seconds_to_recovery"] for row in incident_rows if row["seconds_to_recovery"] is not None]
        ),
        "newest_tested_commit_at": _iso_or_none(newest_tested),
        "newest_tested_commit_age_seconds": (reference - newest_tested).total_seconds() if newest_tested else None,
        "newest_develop_commit_at": _iso_or_none(max(commit_times)) if commit_times else None,
        "develop_commits": None if commits is None else len(commits),
        **_trunk_sha_coverage(commits or [], runs, reference, collection.workflow_created.get(workflow)),
        "job_kinds": outcomes["job_kinds"],
        "guardrail": "a low red rate counts only if missing runs, skipped jobs and cancellations stay low; compare those figures too",
    }


def build_post_merge_signal(collection: UsageCollection, facts: list[dict]) -> dict:
    reference = min(collection.until, collection.collected_at)
    return {
        "reference_time": format_api_timestamp(reference),
        "incident_rule": (
            "attempt results from every trunk push run on develop are ordered by completion time; an incident opens at "
            "the first red result while none is open and closes at the next successful attempt"
        ),
        "workflows": {
            workflow: _trunk_workflow_signal(collection, workflow, facts, reference) for workflow in TRUNK_WORKFLOWS
        },
    }


def build_usage_report(collection: UsageCollection) -> dict:
    facts = [
        run_facts(
            run, collection.jobs_by_run.get(int(run["id"]), []), collection.attempts_by_run.get(int(run["id"]), {})
        )
        for run in collection.runs
    ]
    qualifications: list[str] = list(collection.notes)
    if collection.stats.get("runs_in_flight"):
        qualifications.append(f"{collection.stats['runs_in_flight']} runs were still in progress at collection time")
    derived = sum(item["derived_attempts"] for item in facts)
    if derived:
        qualifications.append(
            f"{derived} earlier attempts had no attempt record; their outcome is derived from job conclusions"
        )
    qualifications.append(
        "queue time counts only jobs executed in their attempt; jobs reused from an earlier attempt and skipped jobs are excluded"
    )
    qualifications.append(
        "PR lifecycle figures cover PRs merged inside the window; runs created before the window start are not collected, "
        "so PRs opened before the window are reported as opened_before_window and kept out of the time-to-green "
        "distributions, and runs on unmerged PRs, pushes and schedules fall under not_on_merged_pr_heads"
    )
    return {
        "schema": USAGE_REPORT_SCHEMA,
        "repo": collection.repo,
        "window": {
            "since": format_api_timestamp(collection.since),
            "until": format_api_timestamp(collection.until),
            "collected_at": format_api_timestamp(collection.collected_at),
        },
        "completeness": {
            "status": "incomplete" if collection.gaps else "complete",
            "gap_count": len(collection.gaps),
            "gaps": collection.gaps,
            "qualifications": qualifications,
            "stats": dict(sorted(collection.stats.items())),
        },
        "workflows": build_workflow_tables(facts),
        "workload": build_workload_mix(facts, collection.heavy_decisions),
        "pull_requests": build_pr_section(collection, facts),
        "post_merge": build_post_merge_signal(collection, facts),
    }


def _minutes_text(seconds: float | None) -> str:
    return "n/a" if seconds is None else f"{seconds / 60.0:.1f}m"


def _rate_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _render_table(headers: list[str], rows: list[list[object]]) -> list[str]:
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in cells)) if cells else len(headers[i]) for i in range(len(headers))
    ]
    line = "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))) + " |"
    rule = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
    body = ["| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |" for row in cells]
    return [line, rule, *body]


def render_usage_report(report: dict) -> str:
    completeness = report["completeness"]
    window = report["window"]
    lines = [
        f"CI usage report for {report['repo']} from {window['since']} to {window['until']} (collected {window['collected_at']})"
    ]
    if completeness["status"] == "complete":
        lines.append("Collection: COMPLETE (no retrieval gaps)")
    else:
        lines.append(
            f"Collection: INCOMPLETE - {completeness['gap_count']} retrieval gaps; every figure below is a lower bound or partial"
        )
        for gap in completeness["gaps"][:15]:
            detail = {key: value for key, value in gap.items() if key not in ("scope", "detail")}
            lines.append(
                f"  gap [{gap['scope']}] {gap['detail']} {json.dumps(detail, sort_keys=True) if detail else ''}".rstrip()
            )
        if completeness["gap_count"] > 15:
            lines.append(f"  ... {completeness['gap_count'] - 15} more gaps in the JSON output")
    lines.append("Collection stats: " + ", ".join(f"{key}={value}" for key, value in completeness["stats"].items()))
    for note in completeness["qualifications"]:
        lines.append(f"Note: {note}")

    overall = report["workflows"]["overall"]
    lines += [
        "",
        f"Runs: {overall['runs']}, runner minutes (all attempts): {overall['runner_minutes']['total']}, job queue median/p90 seconds: {overall['job_queue_seconds']['median']}/{overall['job_queue_seconds']['p90']} (n={overall['job_queue_seconds']['n']})",
        "",
    ]
    rows = []
    for row in sorted(report["workflows"]["per_workflow_event"], key=lambda item: -item["runner_minutes"]["total"]):
        conclusions = row["conclusions"]
        queue = row["job_queue_seconds"]
        rows.append(
            [
                row["workflow"],
                row["event"],
                row["runs"],
                conclusions.get("success", 0),
                conclusions.get("failure", 0) + conclusions.get("timed_out", 0),
                conclusions.get("cancelled", 0),
                row["rerun_runs"],
                row["passed_only_on_rerun"],
                row["runner_minutes"]["total"],
                row["runner_minutes"]["failed"],
                row["runner_minutes"]["cancelled"],
                queue["n"],
                queue["median"],
                queue["p90"],
            ]
        )
    lines += _render_table(
        [
            "workflow",
            "event",
            "runs",
            "ok",
            "red",
            "cancel",
            "reruns",
            "rerun-ok",
            "min",
            "min-red",
            "min-cancel",
            "jobs-q",
            "q-p50 s",
            "q-p90 s",
        ],
        rows,
    )

    workload = report["workload"]
    lines += ["", "Workload mix (all-attempt runner minutes)"]
    lines += _render_table(
        ["class", "runs", "minutes", "cancelled min", "min/run p50", "min/run p90"],
        [
            [
                name,
                value["runs"],
                value["runner_minutes"],
                value["cancelled_runner_minutes"],
                value["runner_minutes_per_run"]["median"],
                value["runner_minutes_per_run"]["p90"],
            ]
            for name, value in workload["by_class"].items()
        ],
    )
    lines.append(
        "Runner OS minutes: " + ", ".join(f"{name}={value}" for name, value in workload["by_runner_os_minutes"].items())
    )
    lines += ["", "Heavy-tier decision (pull_request CI runs, first-attempt ci-paths log)"]
    lines += _render_table(
        ["decision:reason", "runs", "minutes", "cancelled min", "min/run p50", "min/run p90"],
        [
            [
                name,
                value["runs"],
                value["runner_minutes"],
                value["cancelled_runner_minutes"],
                value["runner_minutes_per_run"]["median"],
                value["runner_minutes_per_run"]["p90"],
            ]
            for name, value in workload["by_heavy_tier_decision"].items()
        ],
    )
    lines.append(
        f"Cancelled work: {workload['cancelled_runner_minutes']} runner minutes ({_rate_text(workload['cancelled_share'])} of total)"
    )

    prs = report["pull_requests"]
    lines += ["", "PR time-to-green"]
    if not prs["available"]:
        lines.append(f"  unavailable: {prs['reason']}")
    else:
        policy = prs["context_policy"]
        lines.append(
            f"  required contexts ({policy['source']}, fetched {policy['fetched_at']}): {', '.join(policy['contexts'])}"
        )
        lines.append(
            "  effective from: "
            + ", ".join(
                f"{name}={value or 'always'} [{policy['effective_from_basis'][name]}]"
                for name, value in policy["effective_from"].items()
            )
        )
        lines.append(f"  head update timestamp: {prs['timestamp_source']}")
        lines.append(
            f"  PRs {prs['prs']} {json.dumps(prs['pr_status_counts'])}; heads {prs['heads']} {json.dumps(prs['head_status_counts'])}"
        )
        rows = []
        for scope, key in (("per PR", "per_pr"), ("per head", "per_head")):
            for metric in ("seconds_to_ci_compute_ready", "seconds_to_all_required_ready"):
                dist = prs[key][metric]
                rows.append(
                    [
                        scope,
                        metric.replace("seconds_to_", "").replace("_", "-"),
                        dist["n"],
                        _minutes_text(dist["median"]),
                        _minutes_text(dist["p90"]),
                        _minutes_text(dist["max"]),
                    ]
                )
        lines += _render_table(["scope", "ready", "n", "median", "p90", "max"], rows)
        usage = prs["runner_minutes"]
        lines.append(
            f"  runner minutes: per PR lifecycle median/p90 {usage['pr_lifecycle_per_pr']['median']}/{usage['pr_lifecycle_per_pr']['p90']}, "
            f"all runs per merge {usage['all_runs_per_merge']}, push-event per merge {usage['push_event_per_merge']}, "
            f"not on merged-PR heads {usage['not_on_merged_pr_heads']} of {usage['all_runs_total']}"
        )

    post = report["post_merge"]
    lines += ["", f"Post-merge signal quality (reference time {post['reference_time']})", f"  {post['incident_rule']}"]
    rows = []
    for name, signal in post["workflows"].items():
        recovery = signal["seconds_to_recovery"]
        rows.append(
            [
                name,
                signal["runs"],
                _rate_text(signal["first_attempt_red_rate"]),
                _rate_text(signal["eventual_success_rate"]),
                signal["incident_count"],
                _minutes_text(recovery["median"]),
                _minutes_text(recovery["max"]),
                _minutes_text(signal["newest_tested_commit_age_seconds"]),
                signal["missing_exact_sha_runs"],
                signal["duplicate_exact_sha_runs"],
                signal["first_attempt_cancelled"],
                signal["job_kinds"].get("skipped", 0),
            ]
        )
    lines += _render_table(
        [
            "workflow",
            "runs",
            "1st-red",
            "eventual-ok",
            "incidents",
            "recov p50",
            "recov max",
            "newest age",
            "missing",
            "dupes",
            "cancelled",
            "skipped jobs",
        ],
        rows,
    )
    for name, signal in post["workflows"].items():
        for incident in signal["incidents"]:
            lines.append(
                f"  {name} incident {incident['opened_at']} -> {incident['recovered_at'] or 'ongoing'}: "
                f"first red run {incident['first_red_run']}, merges admitted {incident['merges_admitted']}"
            )
    return "\n".join(lines)


def _parse_effective_from(entries: list[str]) -> dict[str, datetime]:
    parsed: dict[str, datetime] = {}
    for entry in entries:
        name, _, stamp = entry.partition("=")
        moment = _parse_ts(stamp)
        if not name or moment is None:
            raise ValueError(f"--context-effective-from expects NAME=ISO-8601, got {entry!r}")
        parsed[name] = moment
    return parsed


def run_usage_report(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    if not args.since or not args.until:
        print("ERROR: --usage-report requires --since and --until.")
        return 2
    try:
        since, until = parse_report_window(args.since, args.until, now)
        effective_from = _parse_effective_from(args.context_effective_from or [])
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    client = make_client(args.repo)
    if client is None:
        print(
            "ERROR: --usage-report needs GitHub API access (`gh` CLI or GITHUB_TOKEN/GH_TOKEN); no report was produced."
        )
        return 2
    client.attempts = USAGE_API_ATTEMPTS
    client.rate_reserve = args.rate_limit_reserve
    cache = DiskCache(Path(args.cache_dir) if args.cache_dir else None)
    required = (
        [name.strip() for name in args.required_contexts.split(",") if name.strip()] if args.required_contexts else None
    )
    collection = collect_usage(
        client,
        since=since,
        until=until,
        now=now,
        cache=cache,
        workers=args.workers,
        required_override=required,
        effective_from_override=effective_from,
    )
    report = build_usage_report(collection)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_usage_report(report))
    return 0 if report["completeness"]["status"] == "complete" else 3


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
    parser.add_argument(
        "--usage-report",
        action="store_true",
        help=(
            f"produce the {USAGE_REPORT_SCHEMA} CI usage report for --since/--until: partitioned run collection "
            "below the API's 1,000-result cap, all attempts and jobs, live-ruleset required contexts, PR "
            "time-to-green, runner minutes, job queue time, and post-merge signal quality. Exits 3 when any "
            "retrieval gap makes the report incomplete."
        ),
    )
    parser.add_argument(
        "--since", default=None, help="usage report window start: YYYY-MM-DD (UTC midnight) or ISO-8601"
    )
    parser.add_argument(
        "--until",
        default=None,
        help="usage report window end, inclusive: YYYY-MM-DD (end of that UTC day) or ISO-8601; capped at now",
    )
    parser.add_argument("--output", default=None, help="write the full usage report JSON to this path")
    parser.add_argument(
        "--cache-dir",
        default=str(REPO_ROOT / ".cache" / "ci-usage-report"),
        help="directory caching immutable job, attempt and head-check responses so reruns resume",
    )
    parser.add_argument("--workers", type=int, default=USAGE_DEFAULT_WORKERS, help="concurrent detail requests")
    parser.add_argument(
        "--rate-limit-reserve",
        type=int,
        default=USAGE_DEFAULT_RATE_RESERVE,
        help="pause until the rate-limit window resets when fewer than this many requests remain",
    )
    parser.add_argument(
        "--required-contexts",
        default=None,
        help="comma-separated required contexts overriding the live develop ruleset (recorded as an override)",
    )
    parser.add_argument(
        "--context-effective-from",
        action="append",
        metavar="NAME=ISO",
        help="head-update time from which NAME applies, overriding the first-observation rule; repeatable",
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

    if args.usage_report:
        return run_usage_report(args)

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
