from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
REPO = "BenchBox-dev/benchbox"
ANCHOR = datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc)
NOW = ANCHOR + timedelta(hours=9)


def _load():
    spec = importlib.util.spec_from_file_location("trunk_duration_trend", ROOT / "scripts/trunk_duration_trend.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trend = _load()


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(name: str, seconds: float, conclusion: str = "success") -> dict:
    start = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": _stamp(start),
        "completed_at": _stamp(start + timedelta(seconds=seconds)),
    }


def _zip(test_times: list[float], best: float | None) -> bytes:
    cases = "".join(f'<testcase name="t{i}" time="{t}"/>' for i, t in enumerate(test_times))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("junit.xml", f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
        if best is not None:
            bundle.writestr("calibration.json", json.dumps({"workload": {"best_seconds": best}}))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self, hours_between: float = 0.5, recent_count: int = 48, prior_count: int = 200) -> None:
        self.runs: list[dict] = []
        self.fast_seconds = {}
        self.artifacts: dict[int, bytes] = {}
        self.issues: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.pin_status = 200
        self.job_conclusion = "success"
        self.fast_name = "fast (ubuntu-latest, 3.12)"
        self.next_issue = 100
        for index in range(recent_count + prior_count):
            started = ANCHOR - timedelta(hours=hours_between * index)
            self.runs.append(
                {
                    "id": 1000 + index,
                    "head_sha": f"{index:040x}",
                    "run_started_at": _stamp(started),
                    "created_at": _stamp(started),
                    "conclusion": "success",
                }
            )
        self.run_by_id = {run["id"]: run for run in self.runs}

    def set_fast(self, recent: float, prior: float, recent_count: int | None = None) -> None:
        boundary = ANCHOR - trend.RECENT_WINDOW
        for run in self.runs:
            started = trend.parse_timestamp(run["run_started_at"])
            self.fast_seconds[run["id"]] = recent if started > boundary else prior

    def transport(self, method, url, headers, body):
        parsed = urlparse(url)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.requests.append((method, path))
        payload = json.loads(body) if body else None
        if parsed.netloc == "blob.example":
            return trend.Response(200, {}, self.artifacts[int(path.strip("/"))])
        if path.endswith("/runs") and "workflows/trunk.yml" in path:
            page = int(query["page"])
            size = int(query["per_page"])
            return self._json({"workflow_runs": self.runs[(page - 1) * size : page * size]})
        if path.endswith("/jobs"):
            run_id = int(path.split("/")[-2])
            fast = self.fast_seconds.get(run_id, 600)
            return self._json(
                {
                    "jobs": [
                        _job(self.fast_name, fast, self.job_conclusion),
                        _job("medium-collect", 30),
                        _job("medium-test (shard 0)", 400),
                        _job("medium-test (shard 1)", 410),
                        _job("correctness-gate (a)", 180),
                    ]
                }
            )
        if path.endswith("/artifacts") and "/runs/" in path:
            run_id = int(path.split("/")[-2])
            if run_id in self.artifacts:
                return self._json({"artifacts": [{"id": run_id, "expired": False}]})
            return self._json({"artifacts": []})
        if "/actions/artifacts/" in path and path.endswith("/zip"):
            artifact = int(path.split("/")[-2])
            return trend.Response(302, {"location": f"https://blob.example/{artifact}"}, b"")
        if path.endswith("/graphql"):
            return self._json({}, status=self.pin_status)
        if method == "GET" and path.endswith("/issues"):
            return self._json(self.issues)
        if method == "POST" and path.endswith("/issues"):
            issue = {"number": self.next_issue, "node_id": "NODE", "state": "open", **payload}
            self.issues.append(issue)
            return self._json(issue, status=201)
        if method == "PATCH" and "/issues/" in path:
            number = int(path.rsplit("/", 1)[-1])
            issue = next(item for item in self.issues if item["number"] == number)
            issue.update(payload)
            return self._json(issue)
        raise AssertionError(f"unexpected request {method} {url}")

    @staticmethod
    def _json(data, status: int = 200):
        return trend.Response(status, {}, json.dumps(data).encode("utf-8"))

    def api(self, budget: int = trend.API_CALL_BUDGET):
        return trend.GitHubApi(REPO, "token", "https://api.example", budget, self.transport)


def _sample(metric: str, value: float, hours: float, run_id: int) -> object:
    return trend.Sample(metric, run_id, "a" * 40, ANCHOR - timedelta(hours=hours), value)


def test_p50_comparison_flags_only_rises_over_threshold() -> None:
    prior = [_sample("job", 600, 48 + i, i) for i in range(6)]
    over = [_sample("job", 700, i, 100 + i) for i in range(6)]
    at_limit = [_sample("job", 690, i, 200 + i) for i in range(6)]
    assert trend.compare_metric("job", over, prior).rose
    assert not trend.compare_metric("job", at_limit, prior).rose
    finding = trend.compare_metric("job", over, prior)
    assert finding.rise == pytest.approx(700 / 600 - 1)
    assert finding.recent_p50 == 700 and finding.prior_p50 == 600


def test_p50_ignores_single_slow_outliers() -> None:
    prior = [_sample("job", 600, 48 + i, i) for i in range(7)]
    recent = [_sample("job", 600, i, 100 + i) for i in range(6)] + [_sample("job", 6000, 0.5, 200)]
    assert not trend.compare_metric("job", recent, prior).rose


def test_metric_with_too_few_samples_is_skipped_not_alarmed() -> None:
    prior = [_sample("job", 600, 48 + i, i) for i in range(trend.MIN_JOB_SAMPLES)]
    recent = [_sample("job", 6000, i, 100 + i) for i in range(trend.MIN_JOB_SAMPLES - 1)]
    finding = trend.compare_metric("job", recent, prior)
    assert finding.skipped and not finding.rose


def test_first_rise_names_where_the_tail_turns_high() -> None:
    values = [600, 610, 590, 605, 900, 880, 910, 890, 905, 1000]
    samples = [_sample("job", value, 50 - index * 5, index) for index, value in enumerate(values)]
    finding = trend.compare_metric("job", samples[5:], samples[:5])
    assert finding.rose
    assert finding.first_rise.run_id == 4


def test_windows_follow_run_timestamps_not_the_wall_clock() -> None:
    runs = FakeGitHub().runs
    windows = trend.split_windows(runs)
    assert windows.anchor == ANCHOR
    assert len(windows.recent) == 48
    assert all(trend.run_started(run) > ANCHOR - trend.RECENT_WINDOW for run in windows.recent)
    assert windows.prior and all(trend.run_started(run) <= ANCHOR - trend.RECENT_WINDOW for run in windows.prior)
    assert all(trend.run_started(run) > ANCHOR - trend.RECENT_WINDOW - trend.PRIOR_WINDOW for run in windows.prior)


def test_a_late_scheduler_does_not_shift_the_windows() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    early = trend.evaluate(fake.api(), ANCHOR + timedelta(hours=1))
    late = trend.evaluate(fake.api(), ANCHOR + timedelta(hours=9))
    assert early.anchor == late.anchor == ANCHOR
    assert early.recent_runs == late.recent_runs and early.prior_runs == late.prior_runs


def test_cancelled_runs_do_not_count() -> None:
    fake = FakeGitHub(recent_count=12, prior_count=0)
    for run in fake.runs:
        run["conclusion"] = "cancelled"
    assert trend.split_windows(fake.runs) is None


def test_fires_for_a_step_change_and_opens_one_assigned_issue() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    api = fake.api()
    out: list[str] = []
    assert trend.run(api, NOW, out.append) == 0
    assert len(fake.issues) == 1
    issue = fake.issues[0]
    assert issue["assignees"] == ["joeharris76"]
    assert trend.ISSUE_MARKER in issue["body"]
    assert "fast (ubuntu-latest, 3.12)" in issue["body"]
    assert "medium-test" not in issue["body"].split("| Metric")[0]
    assert "+73.9% RISEN" in issue["body"]
    assert "/actions/runs/" in issue["body"]
    assert "Issue: opened #100" in out[0]
    assert ("POST", "/graphql") in fake.requests


def test_quiet_when_the_durations_are_flat() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    api = fake.api()
    assert trend.run(api, NOW, lambda _text: None) == 0
    assert fake.issues == []
    assert not any(method in ("POST", "PATCH") for method, _path in fake.requests)


def test_existing_open_issue_is_updated_not_duplicated() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    fake.issues.append({"number": 7, "state": "open", "body": f"{trend.ISSUE_MARKER}\nold"})
    assert trend.run(fake.api(), NOW, lambda _text: None) == 0
    assert len(fake.issues) == 1
    assert fake.issues[0]["body"].count("RISEN") == 1
    assert fake.issues[0]["assignees"] == ["joeharris76"]
    assert ("POST", "/repos/BenchBox-dev/benchbox/issues") not in fake.requests


def test_closed_issue_is_reopened_when_the_alarm_returns() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    fake.issues.append({"number": 7, "state": "closed", "body": f"{trend.ISSUE_MARKER}\nold"})
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert fake.issues[0]["state"] == "open"
    assert "Issue: reopened #7" in out[0]


def test_open_issue_is_closed_when_healthy() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    fake.issues.append({"number": 7, "state": "open", "body": f"{trend.ISSUE_MARKER}\nold"})
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert fake.issues[0]["state"] == "closed"
    assert fake.issues[0]["state_reason"] == "completed"
    assert "Issue: closed #7" in out[0]


def test_unrelated_issues_and_pull_requests_are_never_touched() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    fake.issues.append({"number": 3, "state": "open", "body": "something else"})
    fake.issues.append({"number": 4, "state": "open", "body": trend.ISSUE_MARKER, "pull_request": {}})
    assert trend.run(fake.api(), NOW, lambda _text: None) == 0
    assert [issue["state"] for issue in fake.issues] == ["open", "open"]


def test_pin_failure_is_reported_without_failing_the_run() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    fake.pin_status = 403
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert "pin skipped" in out[0]
    assert len(fake.issues) == 1


def test_no_alarm_and_no_issue_calls_under_ten_runs() -> None:
    fake = FakeGitHub(recent_count=trend.MIN_RECENT_RUNS - 1, prior_count=100, hours_between=24 / 9)
    fake.set_fast(recent=3000, prior=600)
    fake.issues.append({"number": 7, "state": "open", "body": f"{trend.ISSUE_MARKER}\nold"})
    out: list[str] = []
    api = fake.api()
    assert trend.run(api, NOW, out.append) == 0
    assert "no alarm under 10 runs" in out[0]
    assert fake.issues[0]["state"] == "open" and fake.issues[0]["body"].endswith("old")
    assert not any("/jobs" in path or "issues" in path for _method, path in fake.requests)


def test_ten_runs_in_the_window_is_enough_to_alarm() -> None:
    fake = FakeGitHub(
        recent_count=trend.MIN_RECENT_RUNS, prior_count=100, hours_between=24 / trend.MIN_RECENT_RUNS - 0.01
    )
    fake.set_fast(recent=1500, prior=600)
    assert trend.run(fake.api(), NOW, lambda _text: None) == 0
    assert len(fake.issues) == 1


def test_a_normal_run_stays_inside_the_api_budget_with_many_runs() -> None:
    fake = FakeGitHub(recent_count=60, prior_count=340, hours_between=0.4)
    fake.set_fast(recent=1200, prior=690)
    api = fake.api()
    assert trend.run(api, NOW, lambda _text: None) == 0
    assert api.calls <= trend.API_CALL_BUDGET
    assert len([1 for _m, path in fake.requests if path.endswith("/jobs")]) > 20


def test_exceeding_the_budget_aborts_with_an_error_and_no_issue_writes() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    api = fake.api(budget=3)
    out: list[str] = []
    assert trend.run(api, NOW, out.append) == 1
    assert "budget of 3 exceeded" in out[0]
    assert api.calls == 3
    assert fake.issues == []


def test_budget_is_a_hard_cap_on_the_client() -> None:
    fake = FakeGitHub()
    api = fake.api(budget=2)
    api.json("GET", "/repos/x/issues")
    api.json("GET", "/repos/x/issues")
    with pytest.raises(trend.BudgetExceeded):
        api.json("GET", "/repos/x/issues")
    assert api.calls == 2 and len(fake.requests) == 2


def test_artifact_download_redirect_is_not_an_api_call() -> None:
    fake = FakeGitHub()
    fake.artifacts[5] = b"payload"
    api = fake.api()
    assert api.download("/repos/x/actions/artifacts/5/zip") == b"payload"
    assert api.calls == 1


def test_junit_artifact_sums_test_time_and_normalizes_by_calibration() -> None:
    assert trend.parse_fast_artifact(_zip([1.5, 2.5, 6.0], 2.0)) == (10.0, 2.0)
    assert trend.parse_fast_artifact(_zip([1.0], None)) == (1.0, None)


def _with_artifacts(
    fake: FakeGitHub, prior_speed: float, recent_speed: float, prior_summed: float, recent_summed: float
):
    boundary = ANCHOR - trend.RECENT_WINDOW
    for run in fake.runs:
        started = trend.parse_timestamp(run["run_started_at"])
        recent = started > boundary
        fake.artifacts[run["id"]] = _zip(
            [(recent_summed if recent else prior_summed)], recent_speed if recent else prior_speed
        )


def test_fast_junit_metrics_are_added_when_the_prior_window_has_them() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    _with_artifacts(fake, prior_speed=1.0, recent_speed=2.0, prior_summed=300.0, recent_summed=330.0)
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert trend.FAST_SUM_METRIC in out[0] and trend.FAST_NORMALIZED_METRIC in out[0]
    assert fake.issues == []


def test_normalized_fast_time_catches_creep_hidden_by_a_faster_runner() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    _with_artifacts(fake, prior_speed=2.0, recent_speed=1.0, prior_summed=300.0, recent_summed=300.0)
    assert trend.run(fake.api(), NOW, lambda _text: None) == 0
    assert len(fake.issues) == 1
    assert trend.FAST_NORMALIZED_METRIC in fake.issues[0]["body"]
    assert "+100.0% RISEN" in fake.issues[0]["body"]


def test_fast_junit_metrics_are_skipped_when_the_prior_window_lacks_artifacts() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    boundary = ANCHOR - trend.RECENT_WINDOW
    for run in fake.runs:
        if trend.parse_timestamp(run["run_started_at"]) > boundary:
            fake.artifacts[run["id"]] = _zip([300.0], 1.0)
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert f"| {trend.FAST_SUM_METRIC} |" not in out[0]
    assert "fast-junit metrics skipped" in out[0]
    assert not any("/zip" in path for _m, path in fake.requests)


def _open_alarm_issue(fake: FakeGitHub) -> None:
    fake.issues.append({"number": 7, "state": "open", "body": f"{trend.ISSUE_MARKER}\nold"})


def test_failed_jobs_in_the_recent_window_do_not_close_an_open_issue() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    _open_alarm_issue(fake)
    boundary = ANCHOR - trend.RECENT_WINDOW
    original = fake.transport

    def failing(method, url, headers, body):
        response = original(method, url, headers, body)
        if url.endswith("filter=latest"):
            run_id = int(urlparse(url).path.split("/")[-2])
            if trend.parse_timestamp(fake.run_by_id[run_id]["run_started_at"]) > boundary:
                data = json.loads(response.body)
                for job in data["jobs"]:
                    job["conclusion"] = "failure"
                return fake._json(data)
        return response

    api = trend.GitHubApi(REPO, "t", "https://api.example", 45, failing)
    out: list[str] = []
    assert trend.run(api, NOW, out.append) == 0
    assert "Status: insufficient" in out[0]
    assert "Issue: none" in out[0]
    assert fake.issues[0]["state"] == "open"
    assert not any(method == "PATCH" for method, _path in fake.requests)


def test_renamed_jobs_do_not_close_an_open_issue() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    fake.fast_name = "fast-lane"
    _open_alarm_issue(fake)
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert "Status: insufficient" in out[0]
    assert "no evaluated metric matches" in out[0]
    assert fake.issues[0]["state"] == "open"


def test_a_tiny_budget_leaves_nothing_evaluated_and_keeps_the_issue_open() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=700, prior=690)
    _open_alarm_issue(fake)
    out: list[str] = []
    assert trend.run(fake.api(budget=9), NOW, out.append) == 0
    assert "Status: insufficient" in out[0]
    assert fake.issues[0]["state"] == "open"


def test_an_empty_prior_window_does_not_close_an_open_issue() -> None:
    fake = FakeGitHub(recent_count=48, prior_count=0)
    fake.set_fast(recent=700, prior=690)
    _open_alarm_issue(fake)
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append) == 0
    assert "Status: insufficient" in out[0]
    assert fake.issues[0]["state"] == "open"


def test_a_risen_job_still_alarms_when_another_metric_is_unevaluated() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    fake.fast_name = "fast (ubuntu-latest, 3.12)"
    assert trend.run(fake.api(), NOW, lambda _text: None) == 0
    assert fake.issues[0]["body"].count("RISEN") == 1


def test_dry_run_never_writes_issues() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    out: list[str] = []
    assert trend.run(fake.api(), NOW, out.append, dry_run=True) == 0
    assert fake.issues == []
    assert "RISEN" in out[0] and "Issue: dry-run" in out[0]


def test_as_of_backtests_a_past_window() -> None:
    fake = FakeGitHub()
    fake.set_fast(recent=1200, prior=690)
    seen: list[str] = []
    original = fake.transport

    def spy(method, url, headers, body):
        seen.append(url)
        return original(method, url, headers, body)

    api = trend.GitHubApi(REPO, "t", "https://api.example", 45, spy)
    as_of = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    trend.evaluate(api, NOW, as_of)
    assert "created=2026-10-15T22%3A00%3A00Z..2026-10-24T22%3A00%3A00Z" in seen[0]


def test_workflow_has_least_privilege_and_a_schedule() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/trunk-duration-trend.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"actions": "read", "issues": "write"}
    triggers = workflow[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"][0]["cron"]
    steps = workflow["jobs"]["trend"]["steps"]
    assert steps[-1]["run"] == "python3 scripts/trunk_duration_trend.py"
    assert steps[-1]["env"] == {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
