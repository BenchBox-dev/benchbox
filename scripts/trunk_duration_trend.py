from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

CLI_DESCRIPTION = (
    "Alarm on Trunk CI duration creep.\n"
    "\n"
    "Reads recent trunk.yml push runs on develop, samples their jobs, and compares the\n"
    "p50 job duration of the last day with the prior seven days. Jobs whose p50 rose by\n"
    "more than the threshold are written to one issue assigned to the repository owner;\n"
    "the issue is closed again when no job is over the threshold. Windows are anchored\n"
    "on the newest run's start time, never on the wall clock, so a late scheduler does\n"
    "not shift them. Fast-tier junit artifacts add summed-test-time and\n"
    "runner-speed-normalized metrics when the prior window has them.\n"
    "\n"
    "Exit status: 0 evaluated (alarm or not); 1 API error or API-call budget exceeded.\n"
)

WORKFLOW_FILE = "trunk.yml"
BRANCH = "develop"
ISSUE_MARKER = "<!-- trunk-duration-trend-alarm -->"
ISSUE_TITLE = "Trunk CI job durations are rising"
DEFAULT_ASSIGNEE = "joeharris76"
ISSUE_AUTHOR = "github-actions[bot]"

API_CALL_BUDGET = 45
RISE_THRESHOLD = 0.15
RECENT_WINDOW = timedelta(days=1)
PRIOR_WINDOW = timedelta(days=7)
MIN_RECENT_RUNS = 10
MIN_JOB_SAMPLES = 5
MIN_ARTIFACT_SAMPLES = 3
ARTIFACT_PRIOR_ATTEMPTS = 4
RUNS_PER_PAGE = 100
MAX_RUN_PAGES = 6
ISSUE_CALL_RESERVE = 3
RECENT_JOB_SHARE = 0.56
QUERY_LOOKBACK = timedelta(days=9)
EXCLUDED_RUN_CONCLUSIONS = frozenset({"cancelled", "skipped", "stale"})

TRACKED_JOBS = (
    re.compile(r"^fast \("),
    re.compile(r"^medium-test \(shard \d+\)$"),
    re.compile(r"^correctness-gate \([^)]+\)$"),
)

FAST_SUM_METRIC = "fast-junit summed test time"
FAST_NORMALIZED_METRIC = "fast-junit summed test time / runner calibration"
USER_AGENT = "benchbox-trunk-duration-trend"
HTTP_TIMEOUT_SECONDS = 60


class TrendError(RuntimeError):
    pass


class BudgetExceeded(TrendError):
    pass


class Response(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return Response(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, error.read())
    except (urllib.error.URLError, TimeoutError) as error:
        raise TrendError(f"GitHub request failed for {url}: {error}") from error


class GitHubApi:
    def __init__(
        self,
        repository: str,
        token: str,
        api_url: str = "https://api.github.com",
        budget: int = API_CALL_BUDGET,
        transport: Transport = urllib_transport,
    ):
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.budget = budget
        self.transport = transport
        self.calls = 0

    @property
    def remaining(self) -> int:
        return self.budget - self.calls

    def _spend(self) -> None:
        if self.calls >= self.budget:
            raise BudgetExceeded(f"GitHub API call budget of {self.budget} exceeded")
        self.calls += 1

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, path: str, payload: dict | None = None) -> Response:
        self._spend()
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = self._headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.transport(method, f"{self.api_url}{path}", headers, body)
        if response.status >= 400:
            raise TrendError(f"GitHub API {method} {path} failed with status {response.status}")
        return response

    def json(self, method: str, path: str, payload: dict | None = None):
        return json.loads(self.request(method, path, payload).body or b"null")

    def download(self, path: str) -> bytes:
        response = self.request("GET", path)
        location = response.headers.get("location")
        if response.status not in (301, 302, 303, 307, 308) or not location:
            return response.body
        blob = self.transport("GET", location, {"User-Agent": USER_AGENT}, None)
        if blob.status >= 400:
            raise TrendError(f"Artifact download failed with status {blob.status}")
        return blob.body


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_started(run: dict) -> datetime | None:
    return parse_timestamp(run.get("run_started_at") or run.get("created_at"))


def even_sample(items: Sequence, count: int) -> list:
    if count <= 0:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(index * step)] for index in range(count)]


@dataclass(frozen=True)
class Sample:
    metric: str
    run_id: int
    sha: str
    started: datetime
    value: float


@dataclass
class Windows:
    anchor: datetime
    recent: list[dict]
    prior: list[dict]


def split_windows(runs: Sequence[dict]) -> Windows | None:
    usable = [
        run for run in runs if run.get("conclusion") not in EXCLUDED_RUN_CONCLUSIONS and run_started(run) is not None
    ]
    if not usable:
        return None
    ordered = sorted(usable, key=lambda run: (run_started(run), run["id"]))
    anchor = run_started(ordered[-1])
    recent_start = anchor - RECENT_WINDOW
    prior_start = recent_start - PRIOR_WINDOW
    recent = [run for run in ordered if recent_start < run_started(run) <= anchor]
    prior = [run for run in ordered if prior_start < run_started(run) <= recent_start]
    return Windows(anchor, recent, prior)


def tracked_job_seconds(job: dict) -> tuple[str, float] | None:
    name = job.get("name", "")
    if job.get("conclusion") != "success" or not any(pattern.match(name) for pattern in TRACKED_JOBS):
        return None
    started = parse_timestamp(job.get("started_at"))
    completed = parse_timestamp(job.get("completed_at"))
    if started is None or completed is None or completed <= started:
        return None
    return name, (completed - started).total_seconds()


def junit_summed_seconds(xml_bytes: bytes) -> float:
    total = 0.0
    for _event, element in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if element.tag == "testcase":
            total += float(element.get("time") or 0.0)
            element.clear()
    return total


def parse_fast_artifact(archive: bytes) -> tuple[float, float | None] | None:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = {name.rsplit("/", 1)[-1]: name for name in bundle.namelist()}
        if "junit.xml" not in names:
            return None
        summed = junit_summed_seconds(bundle.read(names["junit.xml"]))
        best = None
        if "calibration.json" in names:
            workload = json.loads(bundle.read(names["calibration.json"])).get("workload") or {}
            best = workload.get("best_seconds")
    if not isinstance(best, (int, float)) or best <= 0:
        best = None
    return summed, best


def p50(values: Sequence[float]) -> float:
    return float(statistics.median(values))


@dataclass
class Finding:
    metric: str
    recent_p50: float | None
    prior_p50: float | None
    recent_count: int
    prior_count: int
    rise: float | None
    rose: bool
    first_rise: Sample | None = None
    skipped: str | None = None


def first_rise_sample(samples: Sequence[Sample], prior_p50: float, threshold: float) -> Sample | None:
    limit = prior_p50 * (1 + threshold)
    ordered = sorted(samples, key=lambda sample: (sample.started, sample.run_id))
    for index, sample in enumerate(ordered):
        if sample.value > limit and p50([later.value for later in ordered[index:]]) > limit:
            return sample
    return None


def compare_metric(
    metric: str,
    recent: Sequence[Sample],
    prior: Sequence[Sample],
    threshold: float = RISE_THRESHOLD,
    min_samples: int = MIN_JOB_SAMPLES,
) -> Finding:
    if len(recent) < min_samples or len(prior) < min_samples:
        return Finding(
            metric,
            p50([s.value for s in recent]) if recent else None,
            p50([s.value for s in prior]) if prior else None,
            len(recent),
            len(prior),
            None,
            False,
            skipped=f"needs {min_samples} samples in each window",
        )
    recent_p50 = p50([s.value for s in recent])
    prior_p50 = p50([s.value for s in prior])
    if prior_p50 <= 0:
        return Finding(metric, recent_p50, prior_p50, len(recent), len(prior), None, False, skipped="prior p50 is zero")
    rise = recent_p50 / prior_p50 - 1
    rose = rise > threshold
    first = first_rise_sample([*prior, *recent], prior_p50, threshold) if rose else None
    return Finding(metric, recent_p50, prior_p50, len(recent), len(prior), rise, rose, first)


@dataclass
class Report:
    anchor: datetime
    recent_start: datetime
    recent_runs: int
    prior_runs: int
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    status: str = "healthy"

    @property
    def risen(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.rose]


def fetch_runs(api: GitHubApi, now: datetime, as_of: datetime | None) -> tuple[list[dict], bool]:
    if as_of is None:
        created = f">={format_timestamp(now - QUERY_LOOKBACK)}"
    else:
        created = f"{format_timestamp(as_of - QUERY_LOOKBACK)}..{format_timestamp(as_of)}"
    runs: list[dict] = []
    truncated = False
    for page in range(1, MAX_RUN_PAGES + 1):
        query = urllib.parse.urlencode(
            {
                "branch": BRANCH,
                "event": "push",
                "status": "completed",
                "per_page": RUNS_PER_PAGE,
                "page": page,
                "created": created,
            }
        )
        payload = api.json("GET", f"/repos/{api.repository}/actions/workflows/{WORKFLOW_FILE}/runs?{query}")
        batch = list((payload or {}).get("workflow_runs", []))
        runs.extend(batch)
        if len(batch) < RUNS_PER_PAGE:
            break
    else:
        truncated = True
    return runs, truncated


def fetch_job_samples(api: GitHubApi, run: dict) -> list[Sample]:
    payload = api.json("GET", f"/repos/{api.repository}/actions/runs/{run['id']}/jobs?per_page=100&filter=latest")
    started = run_started(run)
    samples = []
    for job in (payload or {}).get("jobs", []):
        tracked = tracked_job_seconds(job)
        if tracked:
            samples.append(Sample(tracked[0], run["id"], run["head_sha"], started, tracked[1]))
    return samples


def fetch_artifact_samples(api: GitHubApi, run: dict) -> list[Sample] | None:
    name = f"fast-junit-trunk-{run['head_sha']}"
    query = urllib.parse.urlencode({"name": name})
    payload = api.json("GET", f"/repos/{api.repository}/actions/runs/{run['id']}/artifacts?{query}")
    live = [item for item in (payload or {}).get("artifacts", []) if not item.get("expired")]
    if not live:
        return None
    parsed = parse_fast_artifact(api.download(f"/repos/{api.repository}/actions/artifacts/{live[0]['id']}/zip"))
    if parsed is None:
        return None
    summed, best = parsed
    started = run_started(run)
    samples = [Sample(FAST_SUM_METRIC, run["id"], run["head_sha"], started, summed)]
    if best:
        samples.append(Sample(FAST_NORMALIZED_METRIC, run["id"], run["head_sha"], started, summed / best))
    return samples


def group_by_metric(samples: Sequence[Sample]) -> dict[str, list[Sample]]:
    grouped: dict[str, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.metric, []).append(sample)
    return grouped


def evaluate(
    api: GitHubApi,
    now: datetime,
    as_of: datetime | None = None,
    threshold: float = RISE_THRESHOLD,
) -> Report | None:
    runs, truncated = fetch_runs(api, now, as_of)
    windows = split_windows(runs)
    if windows is None:
        return None
    recent_start = windows.anchor - RECENT_WINDOW
    report = Report(windows.anchor, recent_start, len(windows.recent), len(windows.prior))
    if truncated:
        report.notes.append(f"run listing stopped at {MAX_RUN_PAGES} pages; the oldest prior runs are missing")
    if len(windows.recent) < MIN_RECENT_RUNS:
        report.status = "insufficient"
        report.notes.append(f"{len(windows.recent)} runs in the 1-day window; no alarm under {MIN_RECENT_RUNS} runs")
        return report

    prior_artifacts: list[Sample] = []
    successful_prior = [run for run in windows.prior if run.get("conclusion") == "success"]
    attempts = 0
    hits = 0
    for run in reversed(successful_prior):
        if attempts >= ARTIFACT_PRIOR_ATTEMPTS or hits >= MIN_ARTIFACT_SAMPLES:
            break
        attempts += 1
        found = fetch_artifact_samples(api, run)
        if found is None:
            break
        hits += 1
        prior_artifacts.extend(found)
    artifacts_usable = hits >= MIN_ARTIFACT_SAMPLES
    if not artifacts_usable:
        report.notes.append("fast-junit metrics skipped: the prior window lacks fast-junit artifacts")
    recent_artifact_calls = 2 * MIN_ARTIFACT_SAMPLES if artifacts_usable else 0

    available = max(api.remaining - ISSUE_CALL_RESERVE - recent_artifact_calls, 0)
    recent_jobs = min(len(windows.recent), math.ceil(available * RECENT_JOB_SHARE))
    prior_jobs = min(len(windows.prior), available - recent_jobs)
    recent_jobs = min(len(windows.recent), available - prior_jobs)
    recent_pool = even_sample(windows.recent, recent_jobs)
    prior_pool = even_sample(windows.prior, prior_jobs)
    recent_samples = [s for run in recent_pool for s in fetch_job_samples(api, run)]
    prior_samples = [s for run in prior_pool for s in fetch_job_samples(api, run)]
    if len(prior_pool) < len(windows.prior) or len(recent_pool) < len(windows.recent):
        report.notes.append(
            f"sampled {len(recent_pool)} of {len(windows.recent)} recent runs and "
            f"{len(prior_pool)} of {len(windows.prior)} prior runs to stay inside the API budget"
        )

    if artifacts_usable:
        recent_candidates = [run for run in windows.recent if run.get("conclusion") == "success"]
        for run in even_sample(recent_candidates, MIN_ARTIFACT_SAMPLES):
            found = fetch_artifact_samples(api, run)
            if found:
                recent_samples.extend(found)

    recent_by_metric = group_by_metric(recent_samples)
    prior_by_metric = group_by_metric(prior_samples + prior_artifacts)
    for metric in sorted(set(recent_by_metric) | set(prior_by_metric)):
        is_artifact = metric in (FAST_SUM_METRIC, FAST_NORMALIZED_METRIC)
        report.findings.append(
            compare_metric(
                metric,
                recent_by_metric.get(metric, []),
                prior_by_metric.get(metric, []),
                threshold,
                MIN_ARTIFACT_SAMPLES if is_artifact else MIN_JOB_SAMPLES,
            )
        )
    report.status = "alarm" if report.risen else "healthy"
    return report


def format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 120:
        return f"{value / 60:.1f} min"
    return f"{value:.1f} s"


def run_url(repository: str, run_id: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def render_report(report: Report, repository: str) -> str:
    lines = [
        f"Window anchor (newest Trunk run start): {format_timestamp(report.anchor)}",
        f"1-day window: after {format_timestamp(report.recent_start)} ({report.recent_runs} runs); "
        f"prior 7 days: {report.prior_runs} runs",
        f"Status: {report.status}",
    ]
    lines.extend(f"Note: {note}" for note in report.notes)
    if report.findings:
        lines.extend(
            [
                "",
                "| Metric | 7-day p50 | 1-day p50 | Change | Samples (7d / 1d) | First sampled run where the rise appears |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
    for finding in report.findings:
        if finding.skipped:
            change = f"skipped: {finding.skipped}"
        else:
            change = f"{finding.rise:+.1%}" + (" RISEN" if finding.rose else "")
        first = ""
        if finding.first_rise is not None:
            sample = finding.first_rise
            first = (
                f"[{sample.run_id}]({run_url(repository, sample.run_id)}) "
                f"{sample.sha[:9]} {format_timestamp(sample.started)}"
            )
        lines.append(
            f"| {finding.metric} | {format_seconds(finding.prior_p50)} | {format_seconds(finding.recent_p50)} | "
            f"{change} | {finding.prior_count} / {finding.recent_count} | {first} |"
        )
    return "\n".join(lines)


def issue_body(report: Report, repository: str, threshold: float) -> str:
    if report.status == "alarm":
        names = ", ".join(finding.metric for finding in report.risen)
        head = f"These Trunk metrics rose more than {threshold:.0%} over the prior week: {names}."
    else:
        head = f"No Trunk metric is more than {threshold:.0%} above its prior-week p50; this issue is closed."
    return f"{ISSUE_MARKER}\n{head}\n\n{render_report(report, repository)}\n"


@dataclass
class IssueOutcome:
    action: str
    number: int | None = None
    notes: list[str] = field(default_factory=list)


def find_issue(api: GitHubApi, assignee: str) -> dict | None:
    query = urllib.parse.urlencode(
        {"state": "all", "creator": ISSUE_AUTHOR, "assignee": assignee, "per_page": 100, "sort": "created"}
    )
    issues = api.json("GET", f"/repos/{api.repository}/issues?{query}") or []
    for issue in issues:
        if "pull_request" not in issue and ISSUE_MARKER in (issue.get("body") or ""):
            return issue
    return None


PIN_MUTATION = "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { number } } }"


def pin_issue(api: GitHubApi, node_id: str | None) -> str:
    if not node_id:
        return "pin skipped: no issue node id"
    try:
        api.json("POST", "/graphql", {"query": PIN_MUTATION, "variables": {"id": node_id}})
    except BudgetExceeded:
        raise
    except TrendError as error:
        return f"pin skipped: {error}"
    return "pinned"


def sync_issue(api: GitHubApi, report: Report, assignee: str, threshold: float = RISE_THRESHOLD) -> IssueOutcome:
    if report.status not in ("alarm", "healthy"):
        return IssueOutcome("none")
    existing = find_issue(api, assignee)
    body = issue_body(report, api.repository, threshold)
    if report.status == "alarm":
        if existing is None:
            created = api.json(
                "POST",
                f"/repos/{api.repository}/issues",
                {"title": ISSUE_TITLE, "body": body, "assignees": [assignee]},
            )
            pinned = pin_issue(api, created.get("node_id"))
            return IssueOutcome("opened", created.get("number"), [pinned])
        reopened = existing.get("state") != "open"
        updated = api.json(
            "PATCH",
            f"/repos/{api.repository}/issues/{existing['number']}",
            {"title": ISSUE_TITLE, "body": body, "state": "open", "assignees": [assignee]},
        )
        notes = [pin_issue(api, updated.get("node_id"))] if reopened else []
        return IssueOutcome("reopened" if reopened else "updated", existing["number"], notes)
    if existing is not None and existing.get("state") == "open":
        api.json(
            "PATCH",
            f"/repos/{api.repository}/issues/{existing['number']}",
            {"body": body, "state": "closed", "state_reason": "completed"},
        )
        return IssueOutcome("closed", existing["number"])
    return IssueOutcome("none")


def run(
    api: GitHubApi,
    now: datetime,
    emit: Callable[[str], None] = print,
    as_of: datetime | None = None,
    dry_run: bool = False,
    assignee: str = DEFAULT_ASSIGNEE,
    threshold: float = RISE_THRESHOLD,
    summary: Callable[[str], None] | None = None,
) -> int:
    try:
        report = evaluate(api, now, as_of, threshold)
        if report is None:
            emit("No completed Trunk runs found in the lookback window; nothing to evaluate.")
            emit(f"GitHub API calls used: {api.calls}/{api.budget}")
            return 0
        outcome = IssueOutcome("dry-run") if dry_run else sync_issue(api, report, assignee, threshold)
    except BudgetExceeded as error:
        emit(f"::error::{error}; aborted after {api.calls} calls without finishing")
        return 1
    except TrendError as error:
        emit(f"::error::{error}")
        return 1
    text = render_report(report, api.repository)
    text += f"\nIssue: {outcome.action}" + (f" #{outcome.number}" if outcome.number else "")
    text += "".join(f" ({note})" for note in outcome.notes)
    text += f"\nGitHub API calls used: {api.calls}/{api.budget}"
    emit(text)
    if summary is not None:
        summary(text)
    return 0


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--as-of", help="Evaluate Trunk runs started at or before this UTC timestamp (backtest).")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and print without touching issues.")
    parser.add_argument("--budget", type=int, default=API_CALL_BUDGET)
    parser.add_argument("--threshold", type=float, default=RISE_THRESHOLD)
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    api = GitHubApi(
        env["GITHUB_REPOSITORY"],
        env.get("GITHUB_TOKEN", ""),
        env.get("GITHUB_API_URL", "https://api.github.com"),
        args.budget,
    )
    summary_path = env.get("GITHUB_STEP_SUMMARY")

    def summary(text: str) -> None:
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")

    return run(
        api,
        datetime.now(timezone.utc),
        as_of=parse_timestamp(args.as_of),
        dry_run=args.dry_run,
        assignee=env.get("TREND_ISSUE_ASSIGNEE", DEFAULT_ASSIGNEE),
        threshold=args.threshold,
        summary=summary,
    )


if __name__ == "__main__":
    sys.exit(main())
