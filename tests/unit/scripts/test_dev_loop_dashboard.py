from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
REPO = "BenchBox-dev/BenchBox"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("dev_loop_dashboard", ROOT / "scripts/dev_loop_dashboard.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dash = _load()


def _stamp(base: datetime, **delta: float) -> str:
    return (base + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


T0 = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)


def _run(
    run_id: int, minutes: float = 0, *, conclusion: str = "success", attempt: int = 1, duration: float = 600, **extra
):
    started = T0 + timedelta(minutes=minutes)
    run = {
        "id": run_id,
        "status": "completed",
        "conclusion": conclusion,
        "run_attempt": attempt,
        "created_at": _stamp(started),
        "run_started_at": _stamp(started),
        "updated_at": _stamp(started, seconds=duration),
        "event": "pull_request",
        "head_branch": f"branch-{run_id}",
        "pull_requests": [],
    }
    run.update(extra)
    return run


def _job(name: str, seconds: float, conclusion: str = "success", queued: float = 0.0):
    return {
        "name": name,
        "conclusion": conclusion,
        "created_at": _stamp(T0),
        "started_at": _stamp(T0, seconds=queued),
        "completed_at": _stamp(T0, seconds=queued + seconds),
    }


def test_percentile_uses_nearest_rank():
    values = [float(n) for n in range(1, 11)]
    assert dash.percentile(values, 0.5) == 5.0
    assert dash.percentile(values, 0.9) == 9.0
    assert dash.percentile(values, 0.95) == 10.0
    assert dash.percentile([], 0.5) is None
    assert dash.percentile([7.0], 0.95) == 7.0


def test_window_resolution():
    window = dash.resolve_window(30, None, None, NOW)
    assert window.end == NOW
    assert window.start == NOW - timedelta(days=30)
    explicit = dash.resolve_window(None, "2026-11-01", "2026-11-08", NOW)
    assert explicit.start == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert explicit.days == 7
    assert dash.resolve_window(None, None, None, NOW).days == dash.DEFAULT_DAYS
    with pytest.raises(ValueError):
        dash.resolve_window(None, "2026-11-08", "2026-11-01", NOW)


def test_parse_baseline_reads_the_table_rows():
    text = "\n".join(
        [
            "| Metric | Baseline Value | Measurement Command |",
            "|---|---|---|",
            "| Makefile declared targets | 216 targets | `jq` |",
            "| Develop-red incidents (last 30 days) | 0 incidents | note |",
            "not a row",
        ]
    )
    assert dash.parse_baseline(text) == {
        "Makefile declared targets": "216 targets",
        "Develop-red incidents (last 30 days)": "0 incidents",
    }


def test_recorded_baseline_holds_every_row_the_report_compares():
    baseline = dash.parse_baseline(dash.BASELINE_FILE.read_text(encoding="utf-8"))
    assert set(dash.BASELINE_ROWS.values()) <= set(baseline)


def test_pr_cycle_percentiles():
    prs = [
        {"created_at": _stamp(T0), "merged_at": _stamp(T0, hours=hours)} for hours in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    ]
    cycle = dash.pr_cycle(prs)
    assert cycle.merged == 10
    assert cycle.p50_seconds == 5 * 3600
    assert cycle.p90_seconds == 9 * 3600
    assert dash.pr_cycle([]) == dash.PrCycle(0, None, None)


def test_first_pass_green_counts_each_pull_request_once():
    runs = [
        _run(1, 0, pull_requests=[{"number": 7}]),
        _run(2, 5, conclusion="failure", pull_requests=[{"number": 7}]),
        _run(3, 0, conclusion="failure", pull_requests=[{"number": 8}]),
        _run(4, 5, pull_requests=[{"number": 8}]),
        _run(5, 0, conclusion="success", attempt=2, pull_requests=[{"number": 9}]),
        _run(6, 0, conclusion="cancelled", pull_requests=[{"number": 10}]),
        _run(7, 9, pull_requests=[{"number": 10}]),
    ]
    result = dash.first_pass_green(runs)
    assert (result.pull_requests, result.green) == (4, 2)
    assert result.rate == 0.5
    assert dash.first_pass_green([]).rate is None


def test_first_pass_green_falls_back_to_the_head_branch():
    runs = [_run(1, 0, head_branch="fork-a"), _run(2, 3, conclusion="failure", head_branch="fork-a")]
    assert dash.first_pass_green(runs).pull_requests == 1


def test_time_to_green_uses_successful_runs_only():
    runs = [_run(index, duration=minutes * 60) for index, minutes in enumerate([5, 6, 7, 8, 20], start=1)]
    runs.append(_run(9, conclusion="failure", duration=3000))
    tier = dash.time_to_green(runs)
    assert tier.samples == 5
    assert tier.p50_seconds == 7 * 60
    assert tier.p95_seconds == 20 * 60


def test_job_tier_selects_by_name_and_success():
    jobs = [
        _job("test (ubuntu-latest, 3.14)", 300),
        _job("test (ubuntu-latest, 3.14)", 420),
        _job("test (ubuntu-latest, 3.14)", 900, conclusion="failure"),
        _job("medium-test (shard 0)", 700),
        _job("medium-test (shard 1)", 800),
        _job("medium-collect", 60),
    ]
    fast = dash.job_tier(jobs, dash.FAST_JOB)
    assert (fast.samples, fast.p95_seconds) == (2, 420)
    medium = dash.job_tier(jobs, dash.MEDIUM_JOB)
    assert (medium.samples, medium.p50_seconds, medium.p95_seconds) == (2, 700, 800)


def test_trunk_health_merges_consecutive_red_runs_into_one_incident():
    runs = [
        _run(1, 0, event="push", head_branch="develop"),
        _run(2, 10, event="push", head_branch="develop", conclusion="failure"),
        _run(3, 20, event="push", head_branch="develop", conclusion="failure"),
        _run(4, 25, event="push", head_branch="develop", conclusion="cancelled"),
        _run(5, 30, event="push", head_branch="develop"),
        _run(6, 40, event="push", head_branch="develop", conclusion="timed_out"),
        _run(7, 50, event="pull_request", head_branch="develop", conclusion="failure"),
        _run(8, 50, event="push", head_branch="feature", conclusion="failure"),
    ]
    health = dash.trunk_health(runs)
    assert (health.runs, health.red_runs) == (5, 3)
    assert len(health.incidents) == 2
    assert health.incidents[0]["first_run"] == 2
    assert health.incidents[0]["red_runs"] == 2
    assert health.incidents[0]["recovered_at"] == _stamp(T0, minutes=30)
    assert "recovered_at" not in health.incidents[1]


def test_trunk_health_without_failures_has_no_incidents():
    health = dash.trunk_health([_run(1, 0, event="push", head_branch="develop")])
    assert health.incidents == ()


def test_flake_retry_rate_counts_runs_that_passed_after_a_rerun():
    runs = [
        _run(1),
        _run(2),
        _run(3, attempt=2),
        _run(4, attempt=2, conclusion="failure"),
        _run(5, conclusion="cancelled", attempt=2),
    ]
    rate = dash.flake_retry_rate(runs)
    assert (rate.runs, rate.recovered_after_rerun) == (4, 1)
    assert rate.rate == 0.25
    assert dash.flake_retry_rate([]).rate is None


def test_runner_hours_separates_wall_clock_from_expanded_job_time():
    runs = {
        "ci.yml": [_run(1, duration=3600), _run(2, duration=1800)],
        "other.yml": [_run(3, duration=7200)],
    }
    jobs = {1: [_job("a", 3600), _job("b", 1800)], 2: [_job("c", 900)]}
    usage = dash.runner_hours(runs, jobs)
    assert usage["ci.yml"] == dash.RunnerUsage(2, 1.5, 1.75, 2)
    assert usage["other.yml"] == dash.RunnerUsage(1, 2.0, None, 0)


def test_release_lead_time_excludes_the_approval_wait():
    run = _run(10, event="push", head_branch="v0.5.2rc3", duration=7200)
    publish = {
        "name": "publish to testpypi",
        "created_at": _stamp(T0, minutes=20),
        "started_at": _stamp(T0, minutes=50),
        "completed_at": _stamp(T0, minutes=55),
    }
    dry = {"name": "publish dry run", "created_at": None, "started_at": None, "completed_at": None}
    probe = _run(11, event="workflow_dispatch", head_branch="main")
    leads = dash.release_lead_times([run, probe], {10: [dry, publish]})
    assert len(leads) == 1
    lead = leads[0]
    assert lead.tag == "v0.5.2rc3"
    assert lead.ready_seconds == 20 * 60
    assert lead.approval_wait_seconds == 30 * 60
    assert lead.publish_completed_seconds == 55 * 60


def test_release_lead_time_without_a_publish_job_is_empty():
    leads = dash.release_lead_times([_run(10, event="push", head_branch="v0.5.2rc2", conclusion="failure")], {10: []})
    assert leads[0].ready_seconds is None
    assert leads[0].conclusion == "failure"


def _row(platform: str, status: str = "passed", **extra):
    row = {"platform": platform, "benchmark": "tpch", "scale": 1.0, "status": status}
    row.update(extra)
    return row


def test_cell_observation_uses_the_recorded_failure_class():
    passed = dash.cell_observation(_row("duckdb"), "1", "t")
    assert passed is not None and passed.outcome == "passed"
    infra = dash.cell_observation(
        _row("a", "failed", failure_kind="infrastructure", failure_signature="timeout"), "1", "t"
    )
    assert infra is not None and (infra.outcome, infra.signatures) == ("infrastructure", ("timeout",))
    product = dash.cell_observation(_row("a", "failed", failure_kind="product", failure_signature="crash"), "1", "t")
    assert product is not None and product.outcome == "product"
    absorbed = dash.cell_observation(_row("a", retry_signature="timeout", retry_outcome="passed"), "1", "t")
    assert absorbed is not None and absorbed.outcome == "infrastructure" and absorbed.retried
    unclassified = dash.cell_observation(_row("a", "failed"), "1", "t")
    assert unclassified is not None and unclassified.outcome == "product"
    assert dash.cell_observation(_row("a", "skipped"), "1", "t") is None


def _evidence(run_id: int, day: int, rows: list[dict], **record):
    run = _run(run_id, day * 1440, event="schedule", head_branch="develop")
    payload = {
        "outcome": "executed",
        "verdict": "green",
        "cells": {"total": len(rows), "failed": 0},
        "runner_hours": 4.0,
    }
    payload.update(record)
    return dash.UatEvidence(run, payload, tuple(rows))


def test_uat_summary_rates_cells_and_red_shards():
    items = [
        _evidence(1, 0, [_row("a"), _row("b")]),
        _evidence(2, 1, [_row("a", "failed", failure_kind="infrastructure", failure_signature="timeout"), _row("b")]),
        _evidence(
            3,
            2,
            [_row("a", "failed", failure_kind="product", failure_signature="crash"), _row("b")],
            verdict="red",
            cells={"total": 2, "failed": 1},
        ),
        _evidence(
            4, 3, [_row("a", "failed", failure_kind="infrastructure", failure_signature="timeout")], verdict="red"
        ),
    ]
    summary = dash.uat_summary(items)
    rates = {rate.cell[0]: rate for rate in summary.cell_rates}
    assert (rates["a"].runs, rates["a"].infrastructure, rates["a"].product) == (4, 2, 1)
    assert rates["a"].infrastructure_rate == 0.5
    assert (rates["b"].runs, rates["b"].infrastructure) == (3, 0)
    assert (summary.infrastructure_observations, summary.observations) == (2, 7)
    assert summary.infrastructure_rate == pytest.approx(2 / 7)
    assert summary.red_shards == {"red_shards": 2, "with_product_signature": 1, "share": 0.5}
    assert [run.outcome for run in summary.runs] == ["executed"] * 4
    assert summary.runs[2].failed_cells == 1
    assert summary.runs[0].runner_hours == 4.0


def test_uat_summary_without_history_is_empty():
    summary = dash.uat_summary([])
    assert summary.infrastructure_rate is None
    assert summary.nightly_streak == 0
    assert summary.red_shards["share"] is None


def test_nightly_streak_counts_executed_and_skipped_runs_back_from_the_latest():
    def skipped(run_id: int, day: int):
        return dash.UatEvidence(_run(run_id, day * 1440, event="schedule", head_branch="develop"), None, ())

    def broken(run_id: int, day: int):
        run = _run(run_id, day * 1440, event="schedule", head_branch="develop", conclusion="failure")
        return dash.UatEvidence(run, None, ())

    def dispatched(run_id: int, day: int):
        run = _run(run_id, day * 1440, event="workflow_dispatch", head_branch="develop", conclusion="failure")
        return dash.UatEvidence(run, None, ())

    items = [
        skipped(1, 0),
        broken(2, 1),
        _evidence(3, 2, [_row("a")]),
        skipped(4, 3),
        dispatched(5, 3),
        _evidence(6, 4, [_row("a")], outcome="zero-cells"),
    ]
    assert dash.longest_recent_streak(items) == 0
    assert dash.longest_recent_streak(items[:5]) == 2
    assert dash.longest_recent_streak(items[:4]) == 2
    assert dash.longest_recent_streak([skipped(1, 0), skipped(2, 1)]) == 2


def test_process_blockers_count_open_rubric_items():
    index = {
        "items": {
            "a": {"title": "UAT envelope: investigate x", "status": "open"},
            "b": {"title": "UAT envelope: investigate y", "status": "done"},
            "c": {"title": "UAT envelope: investigate z", "status": "blocked"},
            "d": {"title": "Something else", "status": "open"},
        }
    }
    links = {"a": ["uat-rubric"], "b": ["uat-rubric"], "c": ["elsewhere"]}
    assert dash.process_blockers(index, lambda name: links.get(name, [])) == (1, 2)
    assert dash.process_blockers({}, lambda name: []) == (0, 0)


def test_count_workflows_counts_yaml_files(tmp_path):
    for name in ("a.yml", "b.yml", "notes.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert dash.count_workflows(tmp_path) == 2


def test_count_make_targets_uses_the_repository_rules():
    assert dash.count_make_targets(ROOT) > 0


class FakeApi:
    repository = REPO

    def __init__(self, runs: dict[str, list[dict]] | None = None) -> None:
        self.runs = runs or {}
        self.requests: list[str] = []
        self.calls = 0
        self.archives: dict[int, bytes] = {}
        self.artifacts: dict[int, list[dict]] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.prs: list[dict] = []
        self.workflows = [
            {"id": 1, "path": ".github/workflows/ci.yml"},
            {"id": 2, "path": ".github/workflows/trunk.yml"},
            {"id": 3, "path": ".github/workflows/trunk-uat.yml"},
            {"id": 4, "path": ".github/workflows/release-v2.yml"},
            {"id": 5, "path": "dynamic/update-graph"},
        ]
        self.tracker = {"items": {"x": {"title": "UAT envelope: y", "status": "open"}}}

    def json(self, method: str, path: str, payload: dict | None = None):
        self.calls += 1
        self.requests.append(path)
        parsed = urlparse(path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        segments = parsed.path.split("/")
        if parsed.path.endswith("/actions/workflows"):
            return {"workflows": self.workflows}
        if "/actions/workflows/" in parsed.path and parsed.path.endswith("/runs"):
            workflow = {w["id"]: w["path"].rsplit("/", 1)[-1] for w in self.workflows}[int(segments[-2])]
            runs = self._in_range(self.runs.get(workflow, []), query["created"])
            page, size = int(query["page"]), int(query["per_page"])
            return {"total_count": len(runs), "workflow_runs": runs[(page - 1) * size : page * size]}
        if parsed.path.endswith("/jobs"):
            return {"jobs": self.jobs.get(int(segments[-2]), [])}
        if parsed.path.endswith("/artifacts"):
            return {"artifacts": self.artifacts.get(int(segments[-2]), [])}
        if parsed.path.endswith("/pulls"):
            page = int(query["page"])
            return self.prs[(page - 1) * 100 : page * 100]
        if parsed.path.endswith(f"/git/trees/{dash.STATE_BRANCH}"):
            return {"tree": [{"path": "index.json", "sha": "abc"}]}
        if parsed.path.endswith("/git/blobs/abc"):
            return {"content": base64.b64encode(json.dumps(self.tracker).encode()).decode()}
        if "/contents/items/" in parsed.path:
            return {"content": base64.b64encode(json.dumps({"links": ["uat-rubric"]}).encode()).decode()}
        raise AssertionError(f"unexpected request {path}")

    def download(self, path: str) -> bytes:
        self.calls += 1
        return self.archives[int(path.split("/")[-2])]

    @staticmethod
    def _in_range(runs: list[dict], created: str) -> list[dict]:
        start, end = (datetime.fromisoformat(part.replace("Z", "+00:00")) for part in created.split(".."))
        return [run for run in runs if start <= datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")) <= end]


def test_fetch_runs_bisects_a_window_that_exceeds_the_query_cap(monkeypatch):
    monkeypatch.setattr(dash, "RUN_QUERY_CAP", 10)
    runs = [_run(index, index * 60, event="push") for index in range(1, 21)]
    runs.append(_run(99, 630, event="push"))
    api = FakeApi({"ci.yml": runs})
    fetched = dash.fetch_runs(api, 1, T0, T0 + timedelta(hours=21))
    assert sorted(run["id"] for run in fetched) == [*range(1, 21), 99]


def test_fetch_runs_pages_through_a_large_result(monkeypatch):
    monkeypatch.setattr(dash, "RUNS_PER_PAGE", 3)
    runs = [_run(index, index, event="push") for index in range(1, 8)]
    api = FakeApi({"ci.yml": runs})
    assert len(dash.fetch_runs(api, 1, T0, T0 + timedelta(hours=1))) == 7


def test_fetch_merged_prs_stops_at_the_window_start():
    window = dash.Window(T0, T0 + timedelta(days=2))
    api = FakeApi()
    api.prs = [
        {"merged_at": _stamp(T0, hours=30), "updated_at": _stamp(T0, hours=30), "created_at": _stamp(T0)},
        {"merged_at": None, "updated_at": _stamp(T0, hours=20), "created_at": _stamp(T0)},
        {"merged_at": _stamp(T0, hours=-5), "updated_at": _stamp(T0, hours=-5), "created_at": _stamp(T0, hours=-9)},
    ]
    merged = dash.fetch_merged_prs(api, window)
    assert len(merged) == 1


def _record_zip(record: dict, rows: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("trunk-uat-record.json", json.dumps(record))
        if rows:
            bundle.writestr("cells-history.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
    return buffer.getvalue()


def test_fetch_uat_evidence_reads_the_record_artifact():
    sha = "a" * 40
    api = FakeApi()
    run = _run(50, event="schedule", head_branch="develop")
    api.artifacts[50] = [
        {"id": 9, "name": f"trunk-uat-record-{sha}", "expired": False},
        {"id": 10, "name": "release-uat-trunk-x", "expired": False},
    ]
    api.archives[9] = _record_zip({"outcome": "executed"}, [_row("duckdb")])
    skipped = _run(51, event="schedule", head_branch="develop")
    expired = _run(52, event="schedule", head_branch="develop", conclusion="failure")
    api.artifacts[52] = [{"id": 11, "name": f"trunk-uat-record-{sha}", "expired": True}]
    evidence = dash.fetch_uat_evidence(api, [run, skipped, expired])
    assert evidence[0].record == {"outcome": "executed"}
    assert len(evidence[0].rows) == 1
    assert evidence[1].record is None and dash._uat_outcome(evidence[1]) == "skipped"
    assert dash._uat_outcome(evidence[2]) == "no record"


def test_fetch_uat_evidence_keeps_cancelled_runs_and_they_break_the_streak():
    api = FakeApi()
    cancelled = _run(60, 2 * 1440, event="schedule", head_branch="develop", conclusion="cancelled")
    evidence = dash.fetch_uat_evidence(api, [cancelled])
    assert [dash._uat_outcome(item) for item in evidence] == ["cancelled"]
    items = [_evidence(1, 0, [_row("a")]), evidence[0], _evidence(3, 3, [_row("a")])]
    assert dash.longest_recent_streak(items) == 1
    assert dash.uat_summary(items).runs[1].outcome == "cancelled"


def test_collect_and_render_report_end_to_end(monkeypatch):
    monkeypatch.setattr(dash, "count_make_targets", lambda root=ROOT: 88)
    monkeypatch.setattr(dash, "count_workflows", lambda directory=ROOT: 21)
    window = dash.Window(T0 - timedelta(days=1), T0 + timedelta(days=10))
    ci = [
        _run(1, 0, pull_requests=[{"number": 7}], duration=480),
        _run(2, 30, conclusion="failure", pull_requests=[{"number": 8}]),
        _run(6, 31, pull_requests=[{"number": 8}], duration=2, display_title="CI (text edit)"),
    ]
    trunk = [_run(3, 40, event="push", head_branch="develop", conclusion="failure")]
    uat = [_run(4, 50, event="schedule", head_branch="develop")]
    release = [_run(5, 60, event="push", head_branch="v0.5.2rc3", duration=3600)]
    api = FakeApi({"ci.yml": ci, "trunk.yml": trunk, "trunk-uat.yml": uat, "release-v2.yml": release})
    api.jobs = {
        1: [_job("test (ubuntu-latest, 3.14)", 300), _job("medium-test (shard 0)", 700)],
        2: [_job("test (ubuntu-latest, 3.14)", 200, conclusion="failure")],
        3: [_job("fast (ubuntu-latest, 3.12)", 100)],
        5: [
            {
                "name": "publish to testpypi",
                "created_at": _stamp(T0, minutes=70),
                "started_at": _stamp(T0, minutes=80),
                "completed_at": _stamp(T0, minutes=85),
            }
        ],
    }
    api.artifacts[4] = [{"id": 9, "name": f"trunk-uat-record-{'b' * 40}", "expired": False}]
    api.archives[9] = _record_zip(
        {"outcome": "executed", "verdict": "green", "cells": {"total": 1, "failed": 0}}, [_row("duckdb")]
    )
    api.prs = [{"merged_at": _stamp(T0, hours=2), "updated_at": _stamp(T0, hours=2), "created_at": _stamp(T0)}]
    baseline = dash.parse_baseline(dash.BASELINE_FILE.read_text(encoding="utf-8"))

    dashboard = dash.collect(api, window, baseline, REPO)
    assert dashboard.ci_runs == 2
    assert dashboard.time_to_green_tier.samples == 1
    assert dashboard.make_targets == 88 and dashboard.workflows == 21
    assert len(dashboard.trunk.incidents) == 1
    assert dashboard.blockers == (1, 1)
    assert dashboard.fast.samples == 1 and dashboard.medium.samples == 1
    assert dashboard.releases[0].ready_seconds == 10 * 60
    assert not any("dynamic" in request for request in api.requests)

    report = dash.render_report(dashboard)
    assert "| T1 fast tier job p95 (incl. setup) | <= 7 min | 5.0 min (n=1) | met | 1,108 s / 1,458 s" in report
    assert "| T2 PR required-check time-to-green p95 | <= 15 min | 8.0 min (n=1) | met | not recorded |" in report
    assert "| Public Make targets | <= 90 | 88 | met | 216 targets |" in report
    assert "| Red trunk incidents | 0 | 1 (1 red of 1 runs) | missed | 0 incidents |" in report
    assert "| Workflow files | no numeric target | 21 | reported | 46 files |" in report
    assert "## Nightly trunk UAT" in report
    assert "Per-cell infrastructure-failure rate" in report
    assert "v0.5.2rc3" in report
    assert "Open `UAT envelope:` tracker items linked `uat-rubric`: 1" in report


def test_main_writes_the_report(tmp_path, monkeypatch):
    seen = {}

    def fake_collect(api, window, baseline, repository, max_job_runs):
        seen["window"] = window
        seen["max_job_runs"] = max_job_runs
        seen["repository"] = repository
        return _empty_dashboard(window, repository)

    monkeypatch.setattr(dash, "collect", fake_collect)
    monkeypatch.setattr(dash, "_token", lambda: "token")
    output = tmp_path / "out" / "report.md"
    code = dash.main(
        [
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-08",
            "--max-job-runs",
            "7",
            "--output",
            str(output),
            "--repository",
            REPO,
        ],
        now=NOW,
    )
    assert code == 0
    assert seen["window"].days == 7 and seen["repository"] == REPO
    assert seen["max_job_runs"] == 7
    assert output.read_text(encoding="utf-8").startswith("# Dev-loop dashboard")


def test_main_reports_api_failure(tmp_path, monkeypatch, capsys):
    def failing(api, window, baseline, repository, max_job_runs):
        raise dash.trend.TrendError("boom")

    monkeypatch.setattr(dash, "collect", failing)
    monkeypatch.setattr(dash, "_token", lambda: "token")
    assert dash.main(["--days", "1", "--output", str(tmp_path / "r.md")], now=NOW) == 1
    assert "boom" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def _empty_dashboard(window, repository):
    nothing = dash.TierTimes(0, None, None)
    return dash.Dashboard(
        window=window,
        repository=repository,
        ci_runs=0,
        cycle=dash.PrCycle(0, None, None),
        first_pass=dash.FirstPass(0, 0),
        time_to_green_tier=nothing,
        fast=nothing,
        medium=nothing,
        trunk=dash.TrunkHealth(0, 0, ()),
        flake=dash.FlakeRate(0, 0),
        usage={},
        releases=[],
        uat=dash.uat_summary([]),
        blockers=(0, 0),
        make_targets=190,
        workflows=43,
        baseline={},
        jobs_unavailable=(),
        api_calls=0,
    )


def test_report_with_no_data_says_so():
    report = dash.render_report(_empty_dashboard(dash.Window(T0, T0 + timedelta(days=1)), REPO))
    assert "no data" in report
    assert "No red trunk incidents in the window." in report
    assert "| Red trunk incidents | 0 | 0 (0 red of 0 runs) | no data | not recorded |" in report
    assert "No release tags were pushed in the window." in report
    assert "No trunk UAT runs in the window." in report
    assert "| Public Make targets | <= 90 | 190 | missed | not recorded |" in report


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def getheaders(self):
        return [("Content-Type", "application/json")]


class _FakeConnection:
    script: list = []
    opened: list = []

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.closed = False
        self.requests: list[tuple[str, str]] = []
        _FakeConnection.opened.append(self)

    def request(self, method, target, body=None, headers=None) -> None:
        self.requests.append((method, target))
        step = _FakeConnection.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self._response = step

    def getresponse(self):
        return self._response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_connections():
    _FakeConnection.script = []
    _FakeConnection.opened = []
    return _FakeConnection


def _transport(pauses=None):
    return dash.KeepAliveTransport(
        sleep=(lambda seconds: None) if pauses is None else pauses.append, connect=_FakeConnection
    )


def test_transport_reuses_one_connection_per_host(fake_connections):
    fake_connections.script = [_FakeResponse(200, b"a"), _FakeResponse(200, b"b")]
    transport = _transport()
    first = transport("GET", "https://api.github.com/repos/x?page=1", {}, None)
    second = transport("GET", "https://api.github.com/repos/y", {}, None)
    assert (first.body, second.body) == (b"a", b"b")
    assert first.headers == {"content-type": "application/json"}
    assert len(fake_connections.opened) == 1
    assert fake_connections.opened[0].requests == [("GET", "/repos/x?page=1"), ("GET", "/repos/y")]


def test_transport_retries_server_errors_and_dropped_connections(fake_connections):
    fake_connections.script = [_FakeResponse(502), ConnectionResetError("reset"), _FakeResponse(200, b"ok")]
    pauses: list[float] = []
    transport = _transport(pauses)
    assert transport("GET", "https://api.github.com/x", {}, None).body == b"ok"
    assert pauses == [1.0, 2.0]
    assert len(fake_connections.opened) == 3
    assert all(connection.closed for connection in fake_connections.opened[:2])


def test_transport_returns_client_errors_without_retrying(fake_connections):
    fake_connections.script = [_FakeResponse(404)]
    assert _transport()("GET", "https://api.github.com/x", {}, None).status == 404


def test_transport_gives_up_after_the_attempt_limit(fake_connections):
    fake_connections.script = [_FakeResponse(503)] * (dash.TRANSPORT_ATTEMPTS - 1) + [_FakeResponse(503)]
    assert _transport()("GET", "https://api.github.com/x", {}, None).status == 503
    fake_connections.script = [OSError("down")] * dash.TRANSPORT_ATTEMPTS
    with pytest.raises(dash.trend.TrendError):
        _transport()("GET", "https://api.github.com/x", {}, None)


def test_fetch_all_jobs_skips_runs_whose_listing_keeps_failing():
    class FlakyApi(FakeApi):
        def json(self, method, path, payload=None):
            if "/runs/2/jobs" in path:
                raise dash.trend.TrendError("GitHub API GET failed with status 502")
            return super().json(method, path, payload)

    api = FlakyApi()
    api.jobs = {1: [_job("a", 5)], 3: [_job("b", 5)]}
    jobs, unavailable = dash.fetch_all_jobs(api, [1, 2, 3])
    assert sorted(jobs) == [1, 3]
    assert unavailable == [2]


def test_fetch_all_jobs_stops_when_the_call_budget_is_spent():
    class SpentApi(FakeApi):
        def json(self, method, path, payload=None):
            raise dash.trend.BudgetExceeded("budget")

    with pytest.raises(dash.trend.BudgetExceeded):
        dash.fetch_all_jobs(SpentApi(), [1, 2])


def test_locked_api_counts_every_call_across_threads():
    api = dash.LockedApi(REPO, "token", budget=10_000, transport=lambda *args: dash.trend.Response(200, {}, b"{}"))
    with dash.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: api.json("GET", "/x"), range(400)))
    assert api.calls == 400


def test_report_lists_cells_with_infrastructure_failures_and_counts_product_only_cells():
    window = dash.Window(T0, T0 + timedelta(days=1))
    items = [
        _evidence(1, 0, [_row("flaky"), _row("broken"), _row("steady")]),
        _evidence(
            2,
            1,
            [
                _row("flaky", "failed", failure_kind="infrastructure", failure_signature="timeout"),
                _row("broken", "failed", failure_kind="product", failure_signature="crash"),
                _row("steady"),
            ],
        ),
    ]
    dashboard = _empty_dashboard(window, REPO)
    report = dash.render_report(dashboard.__class__(**{**dashboard.__dict__, "uat": dash.uat_summary(items)}))
    assert "Cells observed: 3; with an infrastructure failure: 1; with only product failures: 1." in report
    assert "| flaky / tpch / SF1 | 2 | 1 | 50.0% | 0 |" in report
    assert "broken / tpch" not in report


def test_job_run_ids_sample_busy_workflows_and_keep_every_release_push():
    ci = [_run(index, index) for index in range(1, 11)]
    trunk = [_run(100 + index, index, event="push") for index in range(1, 4)]
    uat = [_run(200, 0, event="schedule")]
    releases = [
        _run(300, 0, event="push", head_branch="v1"),
        _run(301, 1, event="push", head_branch="v2"),
        *[_run(310 + index, index, event="workflow_dispatch") for index in range(6)],
    ]
    ids = dash.job_run_ids(ci, trunk, uat, releases, 3)
    assert {run_id for run_id in ids if run_id < 100} == {1, 5, 10}
    assert {101, 102, 103, 200, 300, 301} <= set(ids)
    assert len([run_id for run_id in ids if run_id >= 310]) == 3
    assert len(ids) == len(set(ids))


def test_sample_runs_is_ordered_and_ignores_unfinished_runs():
    runs = [_run(3, 30), _run(1, 10), _run(2, 20, conclusion="cancelled"), _run(4, 40)]
    assert [run["id"] for run in dash.sample_runs(runs, 10)] == [1, 3, 4]
    assert [run["id"] for run in dash.sample_runs(runs, 2)] == [1, 4]
    assert dash.sample_runs(runs, 0) == []


def test_job_run_ids_include_release_pushes_that_are_pending_or_cancelled():
    pending = _run(400, 0, event="push", head_branch="v3", status="in_progress", conclusion=None)
    cancelled = _run(401, 1, event="push", head_branch="v4", conclusion="cancelled")
    ids = dash.job_run_ids([], [], [], [pending, cancelled], 1)
    assert {400, 401} <= set(ids)


def test_release_lead_time_measures_a_publish_job_still_awaiting_approval():
    run = _run(10, event="push", head_branch="v0.5.3rc1", status="in_progress", conclusion=None)
    waiting = {
        "name": "publish to testpypi",
        "created_at": _stamp(T0, minutes=15),
        "started_at": None,
        "completed_at": None,
    }
    lead = dash.release_lead_times([run], {10: [waiting]})[0]
    assert lead.ready_seconds == 15 * 60
    assert lead.approval_wait_seconds is None
