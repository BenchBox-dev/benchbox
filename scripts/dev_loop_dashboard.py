#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import http.client
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import check_make_targets
import trunk_duration_trend as trend
import trunk_uat

from tests.uat import rubric

CLI_DESCRIPTION = (
    "Write the dev-loop dashboard for a date window.\n"
    "\n"
    "Reads workflow runs, jobs, merged pull requests, release runs, the nightly trunk UAT history and the\n"
    "tracker index through the GitHub API (read-only), counts Make targets and workflows in the checkout,\n"
    "and writes one Markdown report that sets every dev-loop v2 target next to its recorded baseline.\n"
    "\n"
    "Exit status: 0 report written; 1 API error or API-call budget exceeded.\n"
)

DEFAULT_REPOSITORY = "BenchBox-dev/BenchBox"
DEFAULT_DAYS = 7
DEFAULT_BUDGET = 6000
TRANSPORT_ATTEMPTS = 5
JOB_WORKERS = 8
DEFAULT_JOB_RUNS = 100
RETRY_STATUSES = frozenset({500, 502, 503, 504})
BASELINE_FILE = REPO_ROOT / "_project/archive/dev-loop-v2/baseline.md"
WORKFLOW_DIRECTORY = REPO_ROOT / ".github/workflows"
STATE_BRANCH = "todo-state"
RUNS_PER_PAGE = 100
RUN_QUERY_CAP = 1000
MINIMUM_SPLIT_SPAN = timedelta(hours=1)

CI_WORKFLOW = "ci.yml"
TRUNK_WORKFLOW = "trunk.yml"
UAT_WORKFLOW = "trunk-uat.yml"
RELEASE_WORKFLOW = "release-v2.yml"
JOB_WORKFLOWS = (CI_WORKFLOW, TRUNK_WORKFLOW, UAT_WORKFLOW, RELEASE_WORKFLOW)
DEVELOP = "develop"
FAST_JOB = re.compile(r"^test \(")
MEDIUM_JOB = re.compile(r"^medium-test \(shard \d+\)$")
PUBLISH_JOB = re.compile(r"^publish to ")
RED_CONCLUSIONS = frozenset({"failure", "timed_out"})
CLOSED_STATUSES = frozenset({"done", "dropped"})
UAT_NIGHTLY_STREAK = 7
WORKFLOW_PATH_PREFIX = ".github/workflows/"
ATTEMPTED_STATUSES = frozenset({"passed", "failed", "timed-out"})

TARGET_T1_SECONDS = 7 * 60
TARGET_T2_SECONDS = 15 * 60
TARGET_RED_TRUNK = 0
TARGET_FLAKE_RATE = 0.02
TEXT_EDIT_DISPLAY_TITLE = "CI (text edit)"

BASELINE_ROWS = {
    "fast_p95": "Fast-test CI job seconds (avg / p95)",
    "medium_p95": "Medium-test CI job seconds (avg / p95)",
    "red_trunk": "Develop-red incidents (last 30 days)",
    "workflows": "Active GitHub Actions workflows",
    "first_pass": "First-pass required-lane green rate",
    "pr_cycle": "Median PR open->merge wall time",
}


class Api(Protocol):
    repository: str

    def json(self, method: str, path: str, payload: dict | None = None) -> Any: ...

    def download(self, path: str) -> bytes: ...


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    def label(self) -> str:
        return f"{trend.format_timestamp(self.start)} to {trend.format_timestamp(self.end)}"


def resolve_window(days: int | None, since: str | None, until: str | None, now: datetime) -> Window:
    end = _parse_bound(until) if until else now
    if since:
        start = _parse_bound(since)
    else:
        start = end - timedelta(days=days if days is not None else DEFAULT_DAYS)
    if start >= end:
        raise ValueError("the window start must be before its end")
    return Window(start, end)


def _parse_bound(value: str) -> datetime:
    parsed = trend.parse_timestamp(value if "T" in value else f"{value}T00:00:00Z")
    if parsed is None:
        raise ValueError(f"unreadable window bound {value!r}")
    return parsed


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, -(-len(ordered) * int(round(fraction * 100)) // 100) - 1))
    return ordered[index]


def _seconds(start: str | None, end: str | None) -> float | None:
    first, last = trend.parse_timestamp(start), trend.parse_timestamp(end)
    if first is None or last is None:
        return None
    return max(0.0, (last - first).total_seconds())


def _in_window(value: str | None, window: Window) -> bool:
    stamp = trend.parse_timestamp(value)
    return stamp is not None and window.start <= stamp < window.end


def parse_baseline(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and cells[0] not in {"Metric", ""} and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = cells[1]
    return rows


def fetch_runs(api: Api, workflow: str | int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    created = f"{trend.format_timestamp(start)}..{trend.format_timestamp(end)}"
    base = f"/repos/{api.repository}/actions/workflows/{workflow}/runs?created={created}&per_page={RUNS_PER_PAGE}"
    first = api.json("GET", f"{base}&page=1")
    total = int(first.get("total_count", 0))
    if total > RUN_QUERY_CAP and end - start > MINIMUM_SPLIT_SPAN:
        middle = start + (end - start) / 2
        halves = fetch_runs(api, workflow, start, middle) + fetch_runs(api, workflow, middle, end)
        return list({int(run["id"]): run for run in halves}.values())
    runs = list(first.get("workflow_runs", []))
    page = 1
    while len(runs) < min(total, RUN_QUERY_CAP):
        page += 1
        batch = api.json("GET", f"{base}&page={page}").get("workflow_runs", [])
        if not batch:
            break
        runs.extend(batch)
    return runs


def fetch_jobs(api: Api, run_id: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    page = 0
    while True:
        page += 1
        batch = api.json(
            "GET", f"/repos/{api.repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page={page}"
        ).get("jobs", [])
        jobs.extend(batch)
        if len(batch) < 100:
            return jobs


def fetch_merged_prs(api: Api, window: Window) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    page = 0
    while True:
        page += 1
        batch = api.json(
            "GET",
            f"/repos/{api.repository}/pulls?state=closed&base={DEVELOP}&sort=updated&direction=desc"
            f"&per_page=100&page={page}",
        )
        merged.extend(pr for pr in batch if _in_window(pr.get("merged_at"), window))
        oldest = trend.parse_timestamp(batch[-1].get("updated_at")) if batch else None
        if len(batch) < 100 or oldest is None or oldest < window.start:
            return merged


def _concluded(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        run
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") not in trend.EXCLUDED_RUN_CONCLUSIONS
    ]


def _workflow_name(run: Mapping[str, Any]) -> str:
    return str(run.get("path", "")).rsplit("/", 1)[-1].split("@", 1)[0]


@dataclass(frozen=True)
class PrCycle:
    merged: int
    p50_seconds: float | None
    p90_seconds: float | None


def pr_cycle(prs: Sequence[Mapping[str, Any]]) -> PrCycle:
    cycles = [value for pr in prs if (value := _seconds(pr.get("created_at"), pr.get("merged_at"))) is not None]
    return PrCycle(len(cycles), percentile(cycles, 0.5), percentile(cycles, 0.9))


def _pr_key(run: Mapping[str, Any]) -> str:
    pulls = run.get("pull_requests") or []
    return f"pr-{pulls[0]['number']}" if pulls else f"branch-{run.get('head_branch')}"


@dataclass(frozen=True)
class FirstPass:
    pull_requests: int
    green: int

    @property
    def rate(self) -> float | None:
        return self.green / self.pull_requests if self.pull_requests else None


def first_pass_green(ci_runs: Iterable[dict[str, Any]]) -> FirstPass:
    first: dict[str, dict[str, Any]] = {}
    for run in sorted(_concluded(ci_runs), key=lambda item: (str(item.get("created_at")), int(item["id"]))):
        first.setdefault(_pr_key(run), run)
    green = sum(1 for run in first.values() if run["conclusion"] == "success" and int(run.get("run_attempt", 1)) == 1)
    return FirstPass(len(first), green)


@dataclass(frozen=True)
class TierTimes:
    samples: int
    p50_seconds: float | None
    p95_seconds: float | None


def _tier(values: Sequence[float]) -> TierTimes:
    return TierTimes(len(values), percentile(values, 0.5), percentile(values, 0.95))


def time_to_green(ci_runs: Iterable[dict[str, Any]]) -> TierTimes:
    values = [
        value
        for run in _concluded(ci_runs)
        if run["conclusion"] == "success"
        and (value := _seconds(run.get("created_at"), run.get("updated_at"))) is not None
    ]
    return _tier(values)


def job_tier(jobs: Iterable[Mapping[str, Any]], pattern: re.Pattern[str]) -> TierTimes:
    values = [
        value
        for job in jobs
        if pattern.search(str(job.get("name", "")))
        and job.get("conclusion") == "success"
        and (value := _seconds(job.get("started_at"), job.get("completed_at"))) is not None
    ]
    return _tier(values)


@dataclass(frozen=True)
class TrunkHealth:
    runs: int
    red_runs: int
    incidents: tuple[dict[str, Any], ...]


def trunk_health(trunk_runs: Iterable[dict[str, Any]]) -> TrunkHealth:
    pushes = [run for run in trunk_runs if run.get("event") == "push" and run.get("head_branch") == DEVELOP]
    ordered = sorted(_concluded(pushes), key=lambda item: (str(item.get("created_at")), int(item["id"])))
    incidents: list[dict[str, Any]] = []
    open_incident: dict[str, Any] | None = None
    red = 0
    for run in ordered:
        if run["conclusion"] in RED_CONCLUSIONS:
            red += 1
            if open_incident is None:
                open_incident = {"first_run": run["id"], "started_at": run["created_at"], "red_runs": 1}
                incidents.append(open_incident)
            else:
                open_incident["red_runs"] += 1
        elif run["conclusion"] == "success":
            if open_incident is not None:
                open_incident["recovered_at"] = run["created_at"]
            open_incident = None
    return TrunkHealth(len(ordered), red, tuple(incidents))


@dataclass(frozen=True)
class FlakeRate:
    runs: int
    recovered_after_rerun: int

    @property
    def rate(self) -> float | None:
        return self.recovered_after_rerun / self.runs if self.runs else None


def flake_retry_rate(runs: Iterable[dict[str, Any]]) -> FlakeRate:
    concluded = _concluded(runs)
    recovered = sum(1 for run in concluded if run["conclusion"] == "success" and int(run.get("run_attempt", 1)) > 1)
    return FlakeRate(len(concluded), recovered)


@dataclass(frozen=True)
class RunnerUsage:
    runs: int
    wall_hours: float
    job_hours: float | None
    job_runs: int


def runner_hours(
    runs_by_workflow: Mapping[str, Sequence[dict[str, Any]]], jobs_by_run: Mapping[int, Sequence[dict[str, Any]]]
) -> dict[str, RunnerUsage]:
    usage: dict[str, RunnerUsage] = {}
    for workflow, runs in runs_by_workflow.items():
        wall = sum(
            _seconds(run.get("run_started_at") or run.get("created_at"), run.get("updated_at")) or 0.0 for run in runs
        )
        expanded = [run for run in runs if int(run["id"]) in jobs_by_run]
        job_seconds = sum(
            _seconds(job.get("started_at"), job.get("completed_at")) or 0.0
            for run in expanded
            for job in jobs_by_run[int(run["id"])]
        )
        usage[workflow] = RunnerUsage(len(runs), wall / 3600, job_seconds / 3600 if expanded else None, len(expanded))
    return usage


@dataclass(frozen=True)
class ReleaseLead:
    tag: str
    run_id: int
    conclusion: str | None
    ready_seconds: float | None
    approval_wait_seconds: float | None
    publish_completed_seconds: float | None


def release_lead_times(
    runs: Iterable[dict[str, Any]], jobs_by_run: Mapping[int, Sequence[dict[str, Any]]]
) -> list[ReleaseLead]:
    leads: list[ReleaseLead] = []
    for run in sorted(runs, key=lambda item: str(item.get("created_at"))):
        if run.get("event") != "push":
            continue
        publish = next(
            (
                job
                for job in jobs_by_run.get(int(run["id"]), ())
                if PUBLISH_JOB.search(str(job.get("name", "")))
                and job.get("created_at")
                and job.get("conclusion") != "skipped"
            ),
            None,
        )
        leads.append(
            ReleaseLead(
                tag=str(run.get("head_branch")),
                run_id=int(run["id"]),
                conclusion=run.get("conclusion"),
                ready_seconds=_seconds(run.get("created_at"), publish.get("created_at")) if publish else None,
                approval_wait_seconds=_seconds(publish.get("created_at"), publish.get("started_at"))
                if publish
                else None,
                publish_completed_seconds=_seconds(run.get("created_at"), publish.get("completed_at"))
                if publish
                else None,
            )
        )
    return leads


@dataclass(frozen=True)
class UatRun:
    run_id: int
    created_at: str
    event: str
    conclusion: str | None
    outcome: str
    cells: int
    failed_cells: int
    verdict: str | None
    runner_hours: float | None


@dataclass(frozen=True)
class CellRate:
    cell: rubric.CellKey
    runs: int
    infrastructure: int
    product: int

    @property
    def infrastructure_rate(self) -> float:
        return self.infrastructure / self.runs


@dataclass(frozen=True)
class UatSummary:
    runs: tuple[UatRun, ...]
    cell_rates: tuple[CellRate, ...]
    infrastructure_observations: int
    observations: int
    red_shards: dict[str, object]
    nightly_streak: int

    @property
    def infrastructure_rate(self) -> float | None:
        return self.infrastructure_observations / self.observations if self.observations else None


@dataclass(frozen=True)
class UatEvidence:
    run: dict[str, Any]
    record: dict[str, Any] | None
    rows: tuple[dict[str, Any], ...]


def _uat_outcome(evidence: UatEvidence) -> str:
    if evidence.record is not None:
        return str(evidence.record.get("outcome", "recorded"))
    if evidence.run.get("conclusion") == "cancelled":
        return "cancelled"
    if evidence.run.get("conclusion") == "success":
        return "skipped"
    return "no record"


def longest_recent_streak(items: Sequence[UatEvidence]) -> int:
    streak = 0
    scheduled = sorted(
        (item for item in items if item.run.get("event") == "schedule"),
        key=lambda item: str(item.run.get("created_at")),
        reverse=True,
    )
    for item in scheduled:
        executed = item.record is not None and item.record.get("outcome") == "executed" and bool(item.rows)
        if not (executed or _uat_outcome(item) == "skipped"):
            break
        streak += 1
    return streak


def cell_observation(row: Mapping[str, Any], run_id: str, completed_at: str) -> rubric.CellObservation | None:
    if str(row.get("status") or "") not in ATTEMPTED_STATUSES:
        return None
    kind = row.get("failure_kind")
    retry_signature = row.get("retry_signature")
    signatures = [str(value) for value in (retry_signature, row.get("failure_signature")) if value]
    if kind == rubric.OUTCOME_PRODUCT:
        outcome = rubric.OUTCOME_PRODUCT
    elif kind == rubric.OUTCOME_INFRASTRUCTURE or retry_signature:
        outcome = rubric.OUTCOME_INFRASTRUCTURE
    elif row["status"] == "passed":
        outcome = rubric.OUTCOME_PASSED
    else:
        outcome = rubric.OUTCOME_PRODUCT
    return rubric.CellObservation(
        run_id=run_id,
        completed_at=completed_at,
        cell=(str(row["platform"]), str(row["benchmark"]), float(str(row["scale"]))),
        outcome=outcome,
        signatures=tuple(dict.fromkeys(signatures)),
        retried=row.get("retry_outcome") is not None,
    )


def uat_summary(items: Sequence[UatEvidence]) -> UatSummary:
    ordered = sorted(items, key=lambda item: (str(item.run.get("created_at")), int(item.run["id"])))
    grouped: dict[rubric.CellKey, list[rubric.CellObservation]] = {}
    run_records: list[rubric.RunRecord] = []
    uat_runs: list[UatRun] = []
    for item in ordered:
        completed = str(item.run.get("updated_at") or item.run.get("created_at"))
        observations = tuple(
            observation
            for row in item.rows
            if (observation := cell_observation(row, str(item.run["id"]), completed)) is not None
        )
        for observation in observations:
            grouped.setdefault(observation.cell, []).append(observation)
        record = item.record or {}
        if item.record is not None and item.record.get("outcome") == "executed":
            run_records.append(rubric.RunRecord(str(item.run["id"]), completed, record.get("verdict"), observations))
        cells = record.get("cells") or {}
        hours = record.get("runner_hours")
        uat_runs.append(
            UatRun(
                run_id=int(item.run["id"]),
                created_at=str(item.run.get("created_at")),
                event=str(item.run.get("event")),
                conclusion=item.run.get("conclusion"),
                outcome=_uat_outcome(item),
                cells=int(cells.get("total", 0)),
                failed_cells=int(cells.get("failed", 0)),
                verdict=record.get("verdict"),
                runner_hours=float(hours) if isinstance(hours, (int, float)) else None,
            )
        )
    rates = tuple(
        CellRate(
            cell=cell,
            runs=len(observed),
            infrastructure=sum(1 for obs in observed if obs.outcome == rubric.OUTCOME_INFRASTRUCTURE),
            product=sum(1 for obs in observed if obs.outcome == rubric.OUTCOME_PRODUCT),
        )
        for cell, observed in sorted(grouped.items())
    )
    return UatSummary(
        runs=tuple(uat_runs),
        cell_rates=rates,
        infrastructure_observations=sum(rate.infrastructure for rate in rates),
        observations=sum(rate.runs for rate in rates),
        red_shards=rubric.red_shard_product_share(run_records),
        nightly_streak=longest_recent_streak(items),
    )


def fetch_uat_evidence(api: Api, runs: Iterable[dict[str, Any]]) -> list[UatEvidence]:
    evidence: list[UatEvidence] = []
    for run in (run for run in runs if run.get("status") == "completed"):
        artifacts = api.json("GET", f"/repos/{api.repository}/actions/runs/{run['id']}/artifacts?per_page=100")
        live = [
            artifact
            for artifact in artifacts.get("artifacts", [])
            if not artifact.get("expired") and trunk_uat.RECORD_NAME.fullmatch(str(artifact.get("name")))
        ]
        if not live:
            evidence.append(UatEvidence(run, None, ()))
            continue
        blob = api.download(f"/repos/{api.repository}/actions/artifacts/{live[0]['id']}/zip")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            record = json.loads(archive.read(trunk_uat.RECORD_FILE).decode("utf-8"))
            names = set(archive.namelist())
            history = archive.read(trunk_uat.HISTORY_FILE).decode("utf-8") if trunk_uat.HISTORY_FILE in names else ""
        rows = tuple(json.loads(line) for line in history.splitlines() if line.strip())
        evidence.append(UatEvidence(run, record, rows))
    return evidence


def process_blockers(index: Mapping[str, Any], read_links: Any) -> tuple[int, int]:
    titled = [
        (name, entry)
        for name, entry in (index.get("items") or {}).items()
        if str(entry.get("title", "")).startswith(rubric.TITLE_PREFIX)
    ]
    rubric_items = [(name, entry) for name, entry in titled if rubric.RUBRIC_LINK in read_links(name)]
    open_items = [name for name, entry in rubric_items if entry.get("status") not in CLOSED_STATUSES]
    return len(open_items), len(rubric_items)


def fetch_tracker_index(api: Api) -> dict[str, Any]:
    tree = api.json("GET", f"/repos/{api.repository}/git/trees/{STATE_BRANCH}")
    sha = next(entry["sha"] for entry in tree["tree"] if entry["path"] == "index.json")
    blob = api.json("GET", f"/repos/{api.repository}/git/blobs/{sha}")
    return json.loads(base64.b64decode(blob["content"]))


def tracker_links_reader(api: Api):
    def read(name: str) -> list[str]:
        item = api.json("GET", f"/repos/{api.repository}/contents/items/{name}.json?ref={STATE_BRANCH}")
        return list(json.loads(base64.b64decode(item["content"])).get("links") or [])

    return read


def count_make_targets(root: Path = REPO_ROOT) -> int:
    return len(check_make_targets.targets(root))


def count_workflows(directory: Path = WORKFLOW_DIRECTORY) -> int:
    return len(list(directory.glob("*.yml")))


@dataclass(frozen=True)
class Dashboard:
    window: Window
    repository: str
    ci_runs: int
    cycle: PrCycle
    first_pass: FirstPass
    time_to_green_tier: TierTimes
    fast: TierTimes
    medium: TierTimes
    trunk: TrunkHealth
    flake: FlakeRate
    usage: dict[str, RunnerUsage]
    releases: list[ReleaseLead]
    uat: UatSummary
    blockers: tuple[int, int]
    make_targets: int
    workflows: int
    baseline: dict[str, str]
    jobs_unavailable: tuple[int, ...]
    api_calls: int


def collect(
    api: Api, window: Window, baseline: Mapping[str, str], repository: str, max_job_runs: int = DEFAULT_JOB_RUNS
) -> Dashboard:
    listing = api.json("GET", f"/repos/{repository}/actions/workflows?per_page=100").get("workflows", [])
    workflow_ids = {
        str(entry["path"]).rsplit("/", 1)[-1]: entry["id"]
        for entry in listing
        if str(entry["path"]).startswith(WORKFLOW_PATH_PREFIX)
    }
    names = sorted(workflow_ids)
    with ThreadPoolExecutor(max_workers=JOB_WORKERS) as pool:
        listed = list(pool.map(lambda name: fetch_runs(api, workflow_ids[name], window.start, window.end), names))
    runs_by_workflow = {name: runs for name, runs in zip(names, listed) if runs}
    ci_runs = [
        run
        for run in runs_by_workflow.get(CI_WORKFLOW, ())
        if run.get("event") == "pull_request" and run.get("display_title") != TEXT_EDIT_DISPLAY_TITLE
    ]
    trunk_runs = runs_by_workflow.get(TRUNK_WORKFLOW, [])
    uat_runs = runs_by_workflow.get(UAT_WORKFLOW, [])
    release_runs = runs_by_workflow.get(RELEASE_WORKFLOW, [])

    wanted = job_run_ids(ci_runs, trunk_runs, uat_runs, release_runs, max_job_runs)
    jobs_by_run, jobs_unavailable = fetch_all_jobs(api, wanted)
    ci_jobs = [job for run in _concluded(ci_runs) for job in jobs_by_run.get(int(run["id"]), ())]

    index = fetch_tracker_index(api)
    return Dashboard(
        window=window,
        repository=repository,
        jobs_unavailable=tuple(jobs_unavailable),
        ci_runs=len(ci_runs),
        cycle=pr_cycle(fetch_merged_prs(api, window)),
        first_pass=first_pass_green(ci_runs),
        time_to_green_tier=time_to_green(ci_runs),
        fast=job_tier(ci_jobs, FAST_JOB),
        medium=job_tier(ci_jobs, MEDIUM_JOB),
        trunk=trunk_health(trunk_runs),
        flake=flake_retry_rate([*ci_runs, *(run for run in trunk_runs if run.get("event") == "push")]),
        usage=runner_hours(runs_by_workflow, jobs_by_run),
        releases=release_lead_times(release_runs, jobs_by_run),
        uat=uat_summary(fetch_uat_evidence(api, uat_runs)),
        blockers=process_blockers(index, tracker_links_reader(api)),
        make_targets=count_make_targets(),
        workflows=count_workflows(),
        baseline=dict(baseline),
        api_calls=getattr(api, "calls", 0),
    )


def _minutes(seconds: float | None) -> str:
    return "n/a" if seconds is None else f"{seconds / 60:.1f} min"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _verdict(value: float | None, target: float, *, strict: bool = False) -> str:
    if value is None:
        return "no data"
    met = value < target if strict else value <= target
    return "met" if met else "missed"


def _baseline(dashboard: Dashboard, key: str) -> str:
    return dashboard.baseline.get(BASELINE_ROWS[key], "not recorded")


def _cell_label(cell: rubric.CellKey) -> str:
    return f"{cell[0]} / {cell[1]} / SF{cell[2]:g}"


def render_targets(dashboard: Dashboard) -> list[str]:
    rows = [
        (
            "T1 fast tier job p95 (incl. setup)",
            "<= 7 min",
            f"{_minutes(dashboard.fast.p95_seconds)} (n={dashboard.fast.samples})",
            _verdict(dashboard.fast.p95_seconds, TARGET_T1_SECONDS),
            _baseline(dashboard, "fast_p95"),
        ),
        (
            "T2 PR required-check time-to-green p95",
            "<= 15 min",
            f"{_minutes(dashboard.time_to_green_tier.p95_seconds)} (n={dashboard.time_to_green_tier.samples})",
            _verdict(dashboard.time_to_green_tier.p95_seconds, TARGET_T2_SECONDS),
            "not recorded",
        ),
        (
            "Red trunk incidents",
            "0",
            f"{len(dashboard.trunk.incidents)} ({dashboard.trunk.red_runs} red of {dashboard.trunk.runs} runs)",
            _verdict(float(len(dashboard.trunk.incidents)) if dashboard.trunk.runs else None, TARGET_RED_TRUNK),
            _baseline(dashboard, "red_trunk"),
        ),
        (
            "Flaky retry rate",
            "< 2%",
            f"{_percent(dashboard.flake.rate)} ({dashboard.flake.recovered_after_rerun} of {dashboard.flake.runs} runs)",
            _verdict(dashboard.flake.rate, TARGET_FLAKE_RATE, strict=True),
            "not recorded",
        ),
        (
            "Public Make targets",
            f"<= {check_make_targets.TARGET_CEILING} (ceiling)",
            str(dashboard.make_targets),
            _verdict(float(dashboard.make_targets), check_make_targets.TARGET_CEILING),
            f"{check_make_targets.SAME_METHOD_BASELINE} targets (same method)",
        ),
        (
            "Workflow files",
            "no numeric target",
            str(dashboard.workflows),
            "reported",
            _baseline(dashboard, "workflows"),
        ),
    ]
    lines = ["| Metric | Target | Measured | Verdict | Baseline |", "|---|---|---|---|---|"]
    lines.extend(f"| {' | '.join(row)} |" for row in rows)
    return lines


def render_report(dashboard: Dashboard) -> str:
    out: list[str] = [
        "# Dev-loop dashboard",
        "",
        f"Repository: `{dashboard.repository}`",
        f"Window: {dashboard.window.label()} ({dashboard.window.days:.1f} days)",
        f"GitHub API calls: {dashboard.api_calls}",
        f"Runs without a readable job listing: {len(dashboard.jobs_unavailable)}",
        "",
        "## Dev-loop v2 targets against baseline",
        "",
        *render_targets(dashboard),
        "",
        "Baselines come from `_project/archive/dev-loop-v2/baseline.md` (captured 2026-09-28).",
        "",
        "## Pull request loop",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Merged PRs into develop | {dashboard.cycle.merged} ({dashboard.cycle.merged / dashboard.window.days:.1f}/day) |",
        f"| PR open->merge p50 | {_minutes(dashboard.cycle.p50_seconds)} (baseline {_baseline(dashboard, 'pr_cycle')}) |",
        f"| PR open->merge p90 | {_minutes(dashboard.cycle.p90_seconds)} |",
        f"| CI first-pass green | {_percent(dashboard.first_pass.rate)} of {dashboard.first_pass.pull_requests} PRs"
        f" (baseline {_baseline(dashboard, 'first_pass')}) |",
        f"| PR CI time-to-green p50 / p95 | {_minutes(dashboard.time_to_green_tier.p50_seconds)} /"
        f" {_minutes(dashboard.time_to_green_tier.p95_seconds)} |",
        f"| Fast tier job p50 / p95 | {_minutes(dashboard.fast.p50_seconds)} / {_minutes(dashboard.fast.p95_seconds)} |",
        f"| Medium tier shard job p50 / p95 | {_minutes(dashboard.medium.p50_seconds)} /"
        f" {_minutes(dashboard.medium.p95_seconds)} (baseline {_baseline(dashboard, 'medium_p95')}) |",
        "",
        "## Trunk",
        "",
    ]
    if dashboard.trunk.incidents:
        out += ["| Incident start | First red run | Red runs | Recovered |", "|---|---|---|---|"]
        out += [
            f"| {item['started_at']} | {item['first_run']} | {item['red_runs']} | {item.get('recovered_at', 'open')} |"
            for item in dashboard.trunk.incidents
        ]
    else:
        out.append("No red trunk incidents in the window.")
    out += [
        "",
        "## Runner hours by workflow",
        "",
        "| Workflow | Runs | Run wall-clock h | Job runner-h (sampled runs) | Runs sampled |",
        "|---|---|---|---|---|",
    ]
    for name, usage in sorted(dashboard.usage.items(), key=lambda pair: pair[1].wall_hours, reverse=True):
        jobs = "not read" if usage.job_hours is None else f"{usage.job_hours:.1f}"
        out.append(f"| {name} | {usage.runs} | {usage.wall_hours:.1f} | {jobs} | {usage.job_runs} |")
    out += ["", "## Release lead time", ""]
    if dashboard.releases:
        out += [
            "| Tag | Run | Conclusion | Tag to publish job ready | Approval wait | Tag to publish done |",
            "|---|---|---|---|---|---|",
        ]
        out += [
            f"| {lead.tag} | {lead.run_id} | {lead.conclusion} | {_minutes(lead.ready_seconds)} |"
            f" {_minutes(lead.approval_wait_seconds)} | {_minutes(lead.publish_completed_seconds)} |"
            for lead in dashboard.releases
        ]
    else:
        out.append("No release tags were pushed in the window.")
    out += _render_uat(dashboard)
    open_blockers, total_blockers = dashboard.blockers
    out += [
        "",
        "## Process blockers",
        "",
        f"Open `UAT envelope:` tracker items linked `uat-rubric`: {open_blockers} (all time: {total_blockers}).",
        "",
        "## Definitions",
        "",
        "- Percentiles use nearest rank.",
        "- T1 is the p95 duration of the PR fast-test job, setup included; T2 is the p95 time from creation of a successful PR CI run to its completion, re-run attempts included.",
        "- A red trunk incident is a run of consecutive failing `trunk.yml` pushes on develop, ended by the next success.",
        "- Flaky retry rate is the share of completed PR CI and trunk runs that succeeded only after a re-run.",
        "- First-pass green is the share of PRs whose first concluded CI run succeeded on its first attempt.",
        "- Release lead time excludes the approval wait: it ends when the publish job is queued for its environment.",
        "- Job-based metrics (tier p95, job runner-hours, release lead time) read the job listing of up to"
        " `--max-job-runs` evenly sampled runs per workflow, so the table gives runs sampled next to runs.",
        "- Runs whose job listing GitHub kept refusing are left out of job-based metrics and counted above.",
        "- Make targets use `scripts/check_make_targets.py`, the dlv2-84 counting method. The capability-preserving"
        " consolidation decision replaced the original <= 90 goal with this ceiling; the baseline is the same"
        " method re-run on the baseline commit (docs/internal/dev-loop-retirement-audit.md).",
        "",
    ]
    return "\n".join(out)


def _render_uat(dashboard: Dashboard) -> list[str]:
    uat = dashboard.uat
    share = uat.red_shards["share"]
    out = [
        "",
        "## Nightly trunk UAT",
        "",
        f"Consecutive nightly runs with per-cell history or a skip, ending at the latest: {uat.nightly_streak}"
        f" (acceptance threshold {UAT_NIGHTLY_STREAK}).",
        f"Infrastructure-failure rate over all cell observations: {_percent(uat.infrastructure_rate)}"
        f" ({uat.infrastructure_observations} of {uat.observations}).",
        f"Red shards with a product signature: {uat.red_shards['with_product_signature']} of {uat.red_shards['red_shards']}"
        f" ({_percent(share) if isinstance(share, float) else 'n/a'}).",
        "",
        "### Run history",
        "",
    ]
    if uat.runs:
        out += [
            "| Created | Run | Event | Conclusion | Outcome | Verdict | Cells (failed) | Runner h |",
            "|---|---|---|---|---|---|---|---|",
        ]
        out += [
            f"| {run.created_at} | {run.run_id} | {run.event} | {run.conclusion} | {run.outcome} | {run.verdict or 'n/a'} |"
            f" {run.cells} ({run.failed_cells}) | {'n/a' if run.runner_hours is None else f'{run.runner_hours:.1f}'} |"
            for run in sorted(uat.runs, key=lambda item: item.created_at, reverse=True)
        ]
    else:
        out.append("No trunk UAT runs in the window.")
    out += ["", "### Per-cell infrastructure-failure rate", ""]
    flaky = [rate for rate in uat.cell_rates if rate.infrastructure]
    product_only = sum(1 for rate in uat.cell_rates if rate.product and not rate.infrastructure)
    out.append(
        f"Cells observed: {len(uat.cell_rates)}; with an infrastructure failure: {len(flaky)};"
        f" with only product failures: {product_only}."
    )
    if flaky:
        out += [
            "",
            "| Cell | Observations | Infrastructure failures | Rate | Product failures |",
            "|---|---|---|---|---|",
        ]
        out += [
            f"| {_cell_label(rate.cell)} | {rate.runs} | {rate.infrastructure} | {_percent(rate.infrastructure_rate)} |"
            f" {rate.product} |"
            for rate in sorted(flaky, key=lambda item: (-item.infrastructure_rate, item.cell))
        ]
    return out


def sample_runs(runs: Iterable[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    ordered = sorted(_concluded(runs), key=lambda run: (str(run.get("created_at")), int(run["id"])))
    return trend.even_sample(ordered, cap)


def job_run_ids(
    ci_runs: Sequence[dict[str, Any]],
    trunk_runs: Sequence[dict[str, Any]],
    uat_runs: Sequence[dict[str, Any]],
    release_runs: Sequence[dict[str, Any]],
    cap: int,
) -> list[int]:
    release_pushes = [run for run in release_runs if run.get("event") == "push"]
    release_other = [run for run in release_runs if run.get("event") != "push"]
    chosen = [
        *sample_runs(ci_runs, cap),
        *sample_runs(trunk_runs, cap),
        *sample_runs(uat_runs, cap),
        *release_pushes,
        *sample_runs(release_other, cap),
    ]
    return list(dict.fromkeys(int(run["id"]) for run in chosen))


def _fetch_jobs_or_none(api: Api, run_id: int) -> list[dict[str, Any]] | None:
    try:
        return fetch_jobs(api, run_id)
    except trend.BudgetExceeded:
        raise
    except trend.TrendError:
        return None


def fetch_all_jobs(api: Api, run_ids: Sequence[int]) -> tuple[dict[int, list[dict[str, Any]]], list[int]]:
    with ThreadPoolExecutor(max_workers=JOB_WORKERS) as pool:
        fetched = list(pool.map(lambda run_id: _fetch_jobs_or_none(api, run_id), run_ids))
    jobs = {run_id: found for run_id, found in zip(run_ids, fetched) if found is not None}
    return jobs, [run_id for run_id, found in zip(run_ids, fetched) if found is None]


class LockedApi(trend.GitHubApi):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()

    def _spend(self) -> None:
        with self._lock:
            super()._spend()


def open_connection(host: str, timeout: float) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(host, timeout=timeout)


class KeepAliveTransport:
    def __init__(
        self,
        sleep: Callable[[float], None] = time.sleep,
        connect: Callable[[str, float], http.client.HTTPSConnection] = open_connection,
    ) -> None:
        self._local = threading.local()
        self._sleep = sleep
        self._connect = connect

    @property
    def _connections(self) -> dict[str, http.client.HTTPSConnection]:
        if not hasattr(self._local, "connections"):
            self._local.connections = {}
        return self._local.connections

    def _connection(self, host: str) -> http.client.HTTPSConnection:
        if host not in self._connections:
            self._connections[host] = self._connect(host, trend.HTTP_TIMEOUT_SECONDS)
        return self._connections[host]

    def _drop(self, host: str) -> None:
        connection = self._connections.pop(host, None)
        if connection is not None:
            connection.close()

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> trend.Response:
        parts = urllib.parse.urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        for attempt in range(TRANSPORT_ATTEMPTS):
            last = attempt == TRANSPORT_ATTEMPTS - 1
            try:
                connection = self._connection(parts.netloc)
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
            except (OSError, http.client.HTTPException) as error:
                self._drop(parts.netloc)
                if last:
                    raise trend.TrendError(f"GitHub request failed for {url}: {error}") from error
                self._sleep(2.0**attempt)
                continue
            if response.status in RETRY_STATUSES and not last:
                self._drop(parts.netloc)
                self._sleep(2.0**attempt)
                continue
            return trend.Response(response.status, {key.lower(): value for key, value in response.getheaders()}, data)
        raise AssertionError("unreachable")


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise trend.TrendError("set GITHUB_TOKEN or sign in with gh")
    return result.stdout.strip()


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, help=f"Trailing window length in days (default {DEFAULT_DAYS})")
    parser.add_argument("--since", help="Window start, YYYY-MM-DD or an RFC 3339 timestamp")
    parser.add_argument("--until", help="Window end, YYYY-MM-DD or an RFC 3339 timestamp (default now)")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--output", type=Path, default=Path("dev-loop-dashboard.md"))
    parser.add_argument("--max-api-calls", type=int, default=DEFAULT_BUDGET)
    parser.add_argument(
        "--max-job-runs",
        type=int,
        default=DEFAULT_JOB_RUNS,
        help="Runs per workflow whose job listing is read, evenly sampled over the window (release pushes: all)",
    )
    args = parser.parse_args(argv)
    try:
        window = resolve_window(args.days, args.since, args.until, now or datetime.now(timezone.utc))
        api = LockedApi(args.repository, _token(), budget=args.max_api_calls, transport=KeepAliveTransport())
        dashboard = collect(
            api,
            window,
            parse_baseline(BASELINE_FILE.read_text(encoding="utf-8")),
            args.repository,
            args.max_job_runs,
        )
    except (ValueError, OSError, trend.TrendError) as error:
        print(f"dev_loop_dashboard FAILED: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(dashboard), encoding="utf-8", newline="\n")
    print(f"wrote {args.output} ({dashboard.api_calls} API calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
