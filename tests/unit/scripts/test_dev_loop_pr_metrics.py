from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "_project" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("dev_loop_pr_metrics", SCRIPTS / "dev_loop_pr_metrics.py")
assert spec is not None and spec.loader is not None
metrics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = metrics
spec.loader.exec_module(metrics)


class _NoRunsClient:
    repo = "owner/repo"

    def get_paginated(self, *_args, **_kwargs):
        return []


class _FakeClient:
    repo = "joeharris76/BenchBox"

    def __init__(self, runs: list[dict], jobs: list[dict] | None = None, checks: list[dict] | None = None) -> None:
        self.runs = runs
        self.jobs = jobs or []
        self.checks = checks or []

    def get_paginated(self, path: str, *, item_key: str) -> list[dict]:
        if item_key == "workflow_runs":
            return self.runs
        if item_key == "jobs":
            return self.jobs
        assert item_key == "check_runs"
        return self.checks


def test_gh_api_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            return SimpleNamespace(returncode=1, stdout="", stderr="temporary failure")
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(metrics.subprocess, "run", fake_run)
    monkeypatch.setattr(metrics.time, "sleep", lambda _seconds: None)

    assert metrics._gh_api("/repos/example/project") == {"ok": True}
    assert calls == 3


def test_empty_first_pass_run_returns_all_metric_slots() -> None:
    assert metrics.first_pass_green_and_job_seconds(_NoRunsClient(), "branch") == (None, None, None)


def test_runtime_metrics_only_include_successful_jobs() -> None:
    runs = [
        {
            "id": 42,
            "name": "Develop PR",
            "created_at": "2026-07-27T10:00:00+00:00",
            "conclusion": "success",
        }
    ]
    jobs = [
        {
            "name": metrics.FAST_TEST_JOB_NAME,
            "conclusion": "cancelled",
            "started_at": "2026-07-27T10:01:00+00:00",
            "completed_at": "2026-07-27T10:09:00+00:00",
        },
        {
            "name": metrics.FAST_TEST_JOB_NAME,
            "conclusion": "success",
            "started_at": "2026-07-27T10:10:00+00:00",
            "completed_at": "2026-07-27T10:10:20+00:00",
        },
        {
            "name": metrics.MEDIUM_TEST_JOB_NAME,
            "conclusion": "failure",
            "started_at": "2026-07-27T10:11:00+00:00",
            "completed_at": "2026-07-27T10:11:30+00:00",
        },
        {
            "name": metrics.MEDIUM_TEST_JOB_NAME,
            "conclusion": "success",
            "started_at": "2026-07-27T10:12:00+00:00",
            "completed_at": "2026-07-27T10:12:45+00:00",
        },
    ]

    result = metrics.first_pass_green_and_job_seconds(_FakeClient(runs, jobs), "feature/metrics")

    assert result == (True, 20.0, 45.0)


def _partitioned_jobs() -> list[dict]:
    return [
        _check("medium-collect", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:02:00Z"),
        _check("medium-test (shard 0)", started="2026-07-27T10:03:00Z", completed="2026-07-27T10:18:00Z"),
        _check("medium-test (shard 1)", started="2026-07-27T10:02:30Z", completed="2026-07-27T10:14:00Z"),
        _check("medium-test (shard 2)", started="2026-07-27T10:03:30Z", completed="2026-07-27T10:13:00Z"),
        _check("medium-test (shard 3)", started="2026-07-27T10:03:45Z", completed="2026-07-27T10:12:30Z"),
    ]


def _partitioned_result(jobs: list[dict]) -> tuple:
    runs = [{"id": 42, "name": "CI", "created_at": "2026-07-27T10:00:00Z", "conclusion": "success"}]
    return metrics.first_pass_green_and_job_seconds(_FakeClient(runs, jobs), "feature/partitions")


def test_partitioned_medium_metric_includes_collector_and_wait_without_summing_parallel_shards() -> None:
    jobs = _partitioned_jobs()
    jobs.append(_check(metrics.FAST_TEST_JOB_NAME, started="2026-07-27T10:00:00Z", completed="2026-07-27T10:05:00Z"))
    assert _partitioned_result(list(reversed(jobs))) == (True, 300.0, 1080.0)
    assert metrics._medium_budget_warning(1080.0) is None


@pytest.mark.parametrize("missing", [0, 1, 2, 3, 4])
def test_partitioned_medium_metric_requires_collector_and_every_shard(missing: int) -> None:
    jobs = _partitioned_jobs()
    jobs.pop(missing)
    assert _partitioned_result(jobs)[2] is None


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", None])
def test_partitioned_medium_metric_excludes_censored_observations(index: int, conclusion: str | None) -> None:
    jobs = _partitioned_jobs()
    jobs[index]["conclusion"] = conclusion
    assert _partitioned_result(jobs)[2] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "in_progress"),
        ("started_at", None),
        ("completed_at", "invalid"),
        ("completed_at", "2026-07-27T09:00:00Z"),
        ("started_at", "2026-07-27T09:00:00Z"),
    ],
)
def test_partitioned_medium_metric_rejects_incomplete_or_invalid_intervals(field: str, value: str | None) -> None:
    jobs = _partitioned_jobs()
    jobs[1][field] = value
    assert _partitioned_result(jobs)[2] is None


@pytest.mark.parametrize("name", ["medium-test (shard 0)", "medium-test (shard 4)"])
def test_partitioned_medium_metric_rejects_duplicate_or_unaccounted_shards(name: str) -> None:
    jobs = _partitioned_jobs()
    jobs.append(_check(name, started="2026-07-27T10:03:00Z", completed="2026-07-27T10:25:00Z"))
    assert _partitioned_result(jobs)[2] is None


def test_partitioned_medium_metric_does_not_fall_back_to_stale_literal_job() -> None:
    jobs = _partitioned_jobs()[:2]
    jobs.append(_check("medium-test", started="2026-07-27T10:03:00Z", completed="2026-07-27T10:05:00Z"))
    assert _partitioned_result(jobs)[2] is None


def test_partitioned_medium_metric_reaches_existing_timeout_warning() -> None:
    jobs = _partitioned_jobs()
    jobs[1]["completed_at"] = "2026-07-27T10:39:00Z"
    seconds = _partitioned_result(jobs)[2]
    assert seconds == 2340.0
    assert "39.0 min" in metrics._medium_budget_warning(seconds)


def test_event_fanout_for_pr_fetches_same_head_runs_jobs_and_checks() -> None:
    checks = [
        _check("ci-required-result", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:05:00Z"),
        _check("tooling", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:00:30Z"),
        _check("Results Explorer browser gate", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:01:00Z"),
        _check("ruleset-drift", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:02:00Z"),
        _check("Public-site visual acceptance", started="2026-07-27T10:00:00Z", completed="2026-07-27T10:03:00Z"),
    ]
    runs = [
        {
            "id": 7,
            "name": "Develop PR",
            "head_sha": "abc",
            "run_started_at": "2026-07-27T10:00:00Z",
            "updated_at": "2026-07-27T10:05:00Z",
            "status": "completed",
        }
    ]
    jobs = [
        {
            "name": "Install results-explorer dependencies",
            "conclusion": "success",
            "status": "completed",
            "started_at": "2026-07-27T10:00:00Z",
            "completed_at": "2026-07-27T10:01:00Z",
            "steps": [],
        }
    ]
    fanout = metrics.event_fanout_for_pr(
        _FakeClient(runs, jobs, checks),
        {"head": {"sha": "abc"}, "merged_at": "2026-07-27T10:06:00Z"},
    )
    assert fanout["required_gate_seconds"] == 300.0
    assert fanout["all_workflow_seconds"] == 300.0
    assert fanout["queue_delay_seconds"] == 60.0
    assert fanout["workflow_run_counts"] == {"Develop PR": 1}


def test_medium_budget_reads_workflow_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = tmp_path / "pr.yml"
    workflow.write_text("jobs:\n  medium-test:\n    timeout-minutes: 55\n", encoding="utf-8")
    monkeypatch.setattr(metrics, "MEDIUM_TEST_WORKFLOW_PATH", workflow)

    warning = metrics._medium_budget_warning(55 * 60)

    assert warning is not None
    assert "55 min timeout" in warning


def _check(
    name: str,
    *,
    conclusion: str = "success",
    started: str,
    completed: str,
) -> dict:
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": started,
        "completed_at": completed,
    }


def test_required_gate_uses_latest_rerun_and_ignores_stale_failure() -> None:
    checks = [
        _check(
            "ci-required-result", conclusion="failure", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:10:00Z"
        ),
        _check("ci-required-result", started="2026-08-14T00:12:00Z", completed="2026-08-14T00:20:00Z"),
        _check("tooling", started="2026-08-14T00:12:00Z", completed="2026-08-14T00:12:30Z"),
        _check("Results Explorer browser gate", started="2026-08-14T00:12:00Z", completed="2026-08-14T00:13:00Z"),
        _check("ruleset-drift", started="2026-08-14T00:12:00Z", completed="2026-08-14T00:12:30Z"),
        _check("Public-site visual acceptance", started="2026-08-14T00:12:00Z", completed="2026-08-14T00:13:00Z"),
    ]
    assert metrics.required_gate_seconds(checks) == 8 * 60.0
    assert metrics.merge_unblock_seconds(checks) == 8 * 60.0


def test_required_gate_resolves_six_unit_contexts_when_core_is_present() -> None:
    checks = [
        _check("core", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:09:00Z"),
        _check("explorer", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:02:00Z"),
        _check("results-data", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:01:00Z"),
        _check("docs", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:03:00Z"),
        _check("landing", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:01:00Z"),
        _check("tooling", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:01:30Z"),
    ]

    assert metrics.resolve_required_contexts(checks) == metrics.REQUIRED_CONTEXT_NAMES
    assert metrics.required_gate_seconds(checks) == 9 * 60.0
    assert metrics.required_gate_end(checks) is not None


def test_required_gate_does_not_count_a_partial_six_unit_head_as_green() -> None:
    checks = [
        _check("core", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:09:00Z"),
        _check("tooling", started="2026-09-29T00:00:00Z", completed="2026-09-29T00:01:30Z"),
    ]

    assert metrics.required_gate_seconds(checks) is None


def test_missing_required_context_yields_no_merge_unblock() -> None:
    checks = [
        _check("ci-required-result", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:05:00Z"),
        _check("Results Explorer browser gate", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:01:00Z"),
    ]
    assert metrics.required_gate_seconds(checks) is None
    assert metrics.merge_unblock_seconds(checks) is None


def test_required_context_rename_is_a_missing_check() -> None:
    checks = [
        _check("ci-required-result", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:05:00Z"),
        _check("Results Explorer browser gate", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:01:00Z"),
        _check("ruleset-drift-old-name", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:01:00Z"),
    ]
    assert metrics.required_gate_seconds(checks) is None


def test_all_workflow_seconds_span_multiple_synchronize_workflows() -> None:
    runs = [
        {
            "name": "Develop PR",
            "head_sha": "abc",
            "run_started_at": "2026-08-14T00:00:00Z",
            "updated_at": "2026-08-14T00:30:00Z",
            "status": "completed",
        },
        {
            "name": "Documentation",
            "head_sha": "abc",
            "run_started_at": "2026-08-14T00:00:05Z",
            "updated_at": "2026-08-14T00:07:00Z",
            "status": "completed",
        },
        {
            "name": "Results Explorer browser tests",
            "head_sha": "abc",
            "run_started_at": "2026-08-14T00:00:02Z",
            "updated_at": "2026-08-14T00:00:40Z",
            "status": "completed",
        },
    ]
    assert metrics.all_workflow_seconds(runs) == 30 * 60.0
    grouped = metrics.correlate_runs_by_head(runs)
    assert list(grouped) == ["abc"]
    assert len(grouped["abc"]) == 3


def test_in_progress_workflow_does_not_close_all_workflow_window() -> None:
    runs = [
        {
            "name": "Develop PR",
            "run_started_at": "2026-08-14T00:00:00Z",
            "updated_at": "2026-08-14T00:10:00Z",
            "status": "in_progress",
        },
    ]
    assert metrics.all_workflow_seconds(runs) is None


def test_cancelled_jobs_are_excluded_from_completed_runner_minutes() -> None:
    jobs = [
        {
            "name": "lint",
            "conclusion": "success",
            "status": "completed",
            "started_at": "2026-08-14T00:00:00Z",
            "completed_at": "2026-08-14T00:05:00Z",
            "steps": [
                {"name": "Checkout code", "started_at": "2026-08-14T00:00:00Z", "completed_at": "2026-08-14T00:00:30Z"},
                {"name": "Install uv", "started_at": "2026-08-14T00:00:30Z", "completed_at": "2026-08-14T00:01:00Z"},
                {
                    "name": "Lint and format check with ruff",
                    "started_at": "2026-08-14T00:01:00Z",
                    "completed_at": "2026-08-14T00:05:00Z",
                },
            ],
        },
        {
            "name": "medium-test",
            "conclusion": "cancelled",
            "status": "completed",
            "started_at": "2026-08-14T00:00:00Z",
            "completed_at": "2026-08-14T00:20:00Z",
        },
    ]
    setup, execution, total = metrics.job_setup_execution_seconds(jobs[0])
    assert setup == 60.0
    assert execution == 240.0
    assert total == 300.0
    report = metrics.runner_minute_report(jobs)
    assert report["completed_runner_minutes"] == 5.0
    assert report["cancelled_runner_minutes"] == 20.0
    assert report["cancelled_job_count"] == 1
    assert report["public_standard_runner_usd"] == 0.0
    assert metrics.job_setup_execution_seconds(jobs[1]) == (None, None, None)


def test_event_fanout_separates_required_gate_from_documentation() -> None:
    checks = [
        _check("ci-required-result", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:10:00Z"),
        _check("tooling", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:00:15Z"),
        _check("Results Explorer browser gate", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:00:20Z"),
        _check("ruleset-drift", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:00:30Z"),
        _check("Public-site visual acceptance", started="2026-08-14T00:00:00Z", completed="2026-08-14T00:05:00Z"),
    ]
    runs = [
        {
            "name": "Develop PR",
            "run_started_at": "2026-08-14T00:00:00Z",
            "updated_at": "2026-08-14T00:10:00Z",
            "status": "completed",
        },
        {
            "name": "Documentation",
            "run_started_at": "2026-08-14T00:00:00Z",
            "updated_at": "2026-08-14T00:07:00Z",
            "status": "completed",
        },
    ]
    jobs = [
        {
            "name": "build",
            "conclusion": "success",
            "status": "completed",
            "started_at": "2026-08-14T00:00:00Z",
            "completed_at": "2026-08-14T00:06:00Z",
        }
    ]
    fanout = metrics.event_fanout_metrics(
        runs=runs,
        jobs=jobs,
        check_runs=checks,
        merged_at="2026-08-14T00:12:00Z",
    )
    assert fanout["schema"] == metrics.EVENT_FANOUT_SCHEMA
    assert fanout["required_gate_seconds"] == 600.0
    assert fanout["merge_unblock_seconds"] == 600.0
    assert fanout["all_workflow_seconds"] == 600.0
    assert fanout["queue_delay_seconds"] == 120.0
    assert fanout["workflow_run_counts"]["Documentation"] == 1


def test_event_fanout_schema_is_versioned_and_help_documents_flag() -> None:
    assert metrics.EVENT_FANOUT_SCHEMA == "event_fanout_v1"
    with pytest.raises(SystemExit) as exc:
        metrics.main(["--help"])
    assert exc.value.code == 0


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "dev_loop_metrics"
REASONS = (
    "not_synchronize",
    "missing_event_sha",
    "not_exactly_two_parents",
    "chained_refresh",
    "prior_check_not_success",
    "prior_check_unbound",
    "prior_certification_not_full",
)


class _LifecycleClient:
    repo = "owner/repo"

    def __init__(
        self,
        commits: list[dict],
        runs_by_sha: dict[str, list[dict]],
        jobs_by_run: dict[int, list[dict]],
        checks_by_sha: dict[str, list[dict]],
        timeline: list[dict] | None = None,
    ) -> None:
        self.commits = commits
        self.runs_by_sha = runs_by_sha
        self.jobs_by_run = jobs_by_run
        self.checks_by_sha = checks_by_sha
        self.timeline = timeline or []

    def get_paginated(self, path: str, item_key: str | None = None) -> list[dict]:
        if "pulls/" in path and path.endswith("/commits"):
            return self.commits
        if "/timeline" in path:
            return self.timeline
        if "actions/runs?head_sha=" in path:
            return self.runs_by_sha.get(path.rsplit("head_sha=", 1)[1], [])
        if path.endswith("/jobs"):
            run_id = int(path.rsplit("/runs/", 1)[1].split("/jobs", 1)[0])
            return self.jobs_by_run.get(run_id, [])
        assert "check-runs" in path
        return self.checks_by_sha.get(path.rsplit("/commits/", 1)[1].split("/", 1)[0], [])


def _timed_job(minutes: float) -> dict:
    del minutes
    return {
        "conclusion": "success",
        "status": "completed",
        "started_at": "2026-08-14T00:00:00Z",
        "completed_at": "2026-08-14T00:06:00Z",
        "steps": [],
    }


def test_lifecycle_enumerates_superseded_heads_and_explicit_missing() -> None:
    heads = ["a" * 40, "b" * 40, "c" * 40]
    client = _LifecycleClient(
        commits=[{"sha": sha} for sha in heads],
        runs_by_sha={
            heads[0]: [{"id": 1, "run_attempt": 2, "name": "Develop PR"}],
            heads[2]: [{"id": 3, "name": "Develop PR"}],
        },
        jobs_by_run={1: [_timed_job(6.0)], 3: [_timed_job(6.0)]},
        checks_by_sha={},
    )
    pr = {"number": 7, "head": {"sha": heads[2]}, "merged_at": None}
    lifecycle = metrics.lifecycle_for_pr(client, pr)
    assert lifecycle["heads"] == heads
    assert set(lifecycle["per_head"]) == {heads[0], heads[2]}
    assert lifecycle["attempts"] == {heads[0]: 2, heads[2]: 1}
    assert [m["head_sha"] for m in lifecycle["missing"]] == [heads[1]]
    assert lifecycle["missing"][0]["status"] == "missing-artifact"
    assert lifecycle["totals"]["head_count"] == 3
    assert lifecycle["totals"]["total_attempts"] == 3
    assert lifecycle["totals"]["completed_runner_minutes"] == pytest.approx(12.0)


def _process_doc(version: str = "1.0.0") -> tuple[dict, str]:
    doc = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": version}
    import hashlib
    import json as _json

    return doc, hashlib.sha256(_json.dumps(doc).encode()).hexdigest()


def test_lifecycle_validator_accepts_consistent_report() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    assert metrics.validate_lifecycle_baseline(lifecycle, process, "digest-of-frozen-process-baseline") == []


def test_lifecycle_validator_rejects_tampered_totals() -> None:
    import copy
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_tampered_totals.json").read_text())
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, process, "digest-of-frozen-process-baseline")
    assert any("totals.completed_runner_minutes" in e for e in errors)


def test_lifecycle_validator_rejects_omitted_superseded_head() -> None:
    import copy
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    lifecycle = copy.deepcopy(lifecycle)
    entry = lifecycle["prs"][0]
    dropped = entry["heads"][0]
    del entry["per_head"][dropped]
    del entry["attempts"][dropped]
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, process, "digest-of-frozen-process-baseline")
    assert any("disagree" in e for e in errors)


def test_lifecycle_validator_rejects_changed_thresholds() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    changed = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.1.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, changed, "digest-of-frozen-process-baseline")
    assert any("criteria changed" in e for e in errors)
    same_version = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, same_version, "different-digest")
    assert any("frozen digest" in e for e in errors)


def test_lifecycle_validator_reconciles_cohort_identities() -> None:
    import copy
    import json as _json

    source = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}

    truncated = copy.deepcopy(source)
    truncated["prs"] = truncated["prs"][:1]
    errors = metrics.validate_lifecycle_baseline(truncated, process, "digest-of-frozen-process-baseline")
    assert any("collected_prs" in error for error in errors)

    duplicate = copy.deepcopy(source)
    duplicate["prs"][1]["number"] = duplicate["prs"][0]["number"]
    errors = metrics.validate_lifecycle_baseline(duplicate, process, "digest-of-frozen-process-baseline")
    assert any("duplicate" in error for error in errors)

    overlap = copy.deepcopy(source)
    overlap["cohort"]["candidate_prs"] = 3
    overlap["collection_gaps"] = [{"number": overlap["prs"][0]["number"], "status": "outside-window"}]
    errors = metrics.validate_lifecycle_baseline(overlap, process, "digest-of-frozen-process-baseline")
    assert any("overlap" in error for error in errors)


def test_refresh_audit_recompute_accepts_consistent_block() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    audit = (FIXTURES / "refresh_audit_sample.md").read_text()
    assert metrics.validate_refresh_audit(audit, lifecycle, REASONS) == []


def test_refresh_audit_rejects_duplicate_identity() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    audit = (FIXTURES / "refresh_audit_sample.md").read_text()
    block_text = audit.replace(
        '"head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",\n      "run_id": 12,',
        '"head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n      "run_id": 11,',
    )
    errors = metrics.validate_refresh_audit(block_text, lifecycle, REASONS)
    assert any("duplicate" in e for e in errors)


def test_refresh_audit_rejects_conflated_timing_and_identity() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    audit = (FIXTURES / "refresh_audit_sample.md").read_text()
    merged = audit.replace('"prior_check_unbound": 0,\n    ', "")
    errors = metrics.validate_refresh_audit(merged, lifecycle, REASONS)
    assert any("prior_check_unbound" in e for e in errors)


def test_refresh_audit_rejects_unknown_denominator_and_changed_window() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    audit = (FIXTURES / "refresh_audit_sample.md").read_text()
    errors = metrics.validate_refresh_audit(audit.replace("[101, 102]", "[101, 999]"), lifecycle, REASONS)
    assert any("outside the frozen baseline" in e for e in errors)
    errors = metrics.validate_refresh_audit(audit.replace("[101, 102]", "[101]"), lifecycle, REASONS)
    assert any("omits frozen baseline PRs" in e for e in errors)
    errors = metrics.validate_refresh_audit(
        audit.replace("2026-08-11T00:00:00+00:00", "2026-08-01T00:00:00+00:00"), lifecycle, REASONS
    )
    assert any("window_start" in e for e in errors)


def test_refresh_reason_codes_come_from_classifier() -> None:
    codes = metrics.load_refresh_reason_codes()
    assert metrics.REFRESH_REASON_TIMING in codes
    assert metrics.REFRESH_REASON_IDENTITY in codes


class _SearchClient:
    repo = "owner/repo"

    def __init__(self, total: int, items: list[dict]) -> None:
        self.total = total
        self.items = items

    def get(self, _path: str) -> dict:
        return {"total_count": self.total, "items": self.items[:1]}

    def get_paginated(self, _path: str, item_key: str | None = None) -> list[dict]:
        assert item_key == "items"
        return self.items


def test_cohort_enumeration_is_complete_against_total_count() -> None:
    client = _SearchClient(3, [{"number": 7}, {"number": 8}, {"number": 9}])
    assert metrics.fetch_cohort_pr_numbers(client, "2026-08-11T00:00:00+00:00", "2026-09-08T00:00:00+00:00") == [
        7,
        8,
        9,
    ]


def test_cohort_enumeration_fails_closed_on_short_page() -> None:
    client = _SearchClient(3, [{"number": 7}])
    with pytest.raises(metrics.ApiFailure, match="truncated"):
        metrics.fetch_cohort_pr_numbers(client, "2026-08-11T00:00:00+00:00", "2026-09-08T00:00:00+00:00")


def _acceptance_doc(**overrides: object) -> dict:
    doc: dict = {
        "schema": "pr_process_acceptance_v1",
        "process_binding": {"criteria_version": "1.0.0", "process_digest": "digest"},
        "registration": {"commit": "abc123", "time": "2026-09-08T12:33:43Z"},
        "incident_replays": [{"scenario": "s1", "status": "pass"}],
        "cohort": {
            "required": {"window_start": "W0", "window_end": "W1", "min_prs": 10, "min_days": 7, "strata": ["product"]},
            "observed": {
                "window_start": "W0",
                "window_end": "W1",
                "prs": 10,
                "days": 7,
                "strata": ["product"],
                "batch_deliveries": [],
            },
        },
        "efficiency": {
            "baseline_avoidable_actions": 65,
            "observed_avoidable_actions": 20,
            "added_required_lane_failure": False,
            "p95_regression_attributable": False,
        },
    }
    doc.update(overrides)
    return doc


def _process_doc() -> dict:
    return {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}


def _valid_delivery_receipts() -> list[dict]:
    import subprocess

    repo_root = Path(metrics.__file__).resolve().parents[2]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    parent = "1" * 40

    def receipt(batch_id: str, integration_head: str, member_heads: list[str], dependency: bool) -> dict:
        members = [
            {"id": f"{batch_id}-member-{index}", "head": member_head}
            for index, member_head in enumerate(member_heads, start=1)
        ]
        member_ids = [member["id"] for member in members]
        dependencies = (
            [
                {
                    "member": member_ids[1],
                    "depends_on": member_ids[0],
                    "evidence": f"tracker edge for {batch_id}",
                }
            ]
            if dependency
            else []
        )
        return {
            "schema": "batch_delivery_receipt_v1",
            "batch_id": batch_id,
            "integration_head": integration_head,
            "members": members,
            "member_ancestry": dict.fromkeys(member_ids, True),
            "acceptance": dict.fromkeys(member_ids, "pass"),
            "internal_implementation_dependencies": dependencies,
        }

    return [
        receipt("batch-one", head, [head, parent], True),
        receipt("batch-two", parent, [parent, parent], False),
    ]


def test_acceptance_validator_rejects_changed_criteria_and_cohort() -> None:
    acceptance = _acceptance_doc()
    errors = metrics.validate_process_acceptance(acceptance, {"criteria_version": "2.0.0"}, "digest")
    assert any("criteria" in e for e in errors)
    errors = metrics.validate_process_acceptance(acceptance, _process_doc(), "other-digest")
    assert any("bound digest" in e for e in errors)
    moved = _acceptance_doc()
    moved["cohort"]["observed"]["window_end"] = "W2"
    errors = metrics.validate_process_acceptance(moved, _process_doc(), "digest")
    assert any("unreported cohort change" in e for e in errors)


def test_acceptance_validator_holds_incomplete_cohort_and_replays() -> None:
    acceptance = _acceptance_doc()
    acceptance["cohort"]["observed"]["prs"] = 4
    acceptance["incident_replays"] = [{"scenario": "s1", "status": "pending"}]
    acceptance["efficiency"]["observed_avoidable_actions"] = None
    errors = metrics.validate_process_acceptance(acceptance, _process_doc(), "digest")
    assert any("prs" in e for e in errors)
    assert any("not passing" in e for e in errors)
    assert any("unmeasured" in e for e in errors)


def test_acceptance_validator_accepts_complete_record(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy
    import hashlib
    import json as _json2
    import subprocess

    repo_root = Path(metrics.__file__).resolve().parents[2]
    process_path = repo_root / "_project" / "analysis" / "pr-process-acceptance-baseline.json"
    process_raw = process_path.read_bytes()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    process = _json2.loads(process_raw.decode("utf-8"))
    frozen = process["prospective_cohort"]
    acceptance = _acceptance_doc()
    acceptance["process_binding"] = {
        "criteria_version": process["criteria_version"],
        "process_digest": hashlib.sha256(process_raw).hexdigest(),
    }
    acceptance["registration"] = {"commit": head, "original_commit": head, "time": "2026-09-08T12:33:43Z"}
    acceptance["incident_replays"] = [{"scenario": name, "status": "pass"} for name in process["incident_scenarios"]]
    acceptance["cohort"]["required"]["min_prs"] = frozen["min_prs"]
    acceptance["cohort"]["required"]["min_days"] = frozen["min_days"]
    acceptance["cohort"]["required"]["strata"] = list(frozen["strata"])
    acceptance["cohort"]["observed"]["prs"] = frozen["min_prs"]
    acceptance["cohort"]["observed"]["days"] = frozen["min_days"]
    acceptance["cohort"]["observed"]["strata"] = list(frozen["strata"])
    acceptance["cohort"]["observed"]["human_hold"] = True
    receipts = _valid_delivery_receipts()
    synthetic_heads = {receipt["integration_head"] for receipt in receipts[1:]}
    real_is_ancestor = metrics._is_ancestor
    monkeypatch.setattr(
        metrics,
        "_is_ancestor",
        lambda commit, ref="HEAD": (
            True if commit in synthetic_heads or commit == head else real_is_ancestor(commit, ref)
        ),
    )
    base_freeze = process.get("base_commit_at_freeze")
    real_commit_parent = metrics._commit_parent
    monkeypatch.setattr(
        metrics,
        "_commit_parent",
        lambda commit: base_freeze if commit == head else real_commit_parent(commit),
    )
    real_has_second_parent = metrics._has_second_parent
    monkeypatch.setattr(
        metrics,
        "_has_second_parent",
        lambda commit: False if commit == head else real_has_second_parent(commit),
    )
    monkeypatch.setattr(metrics, "_pinned_registration_pointers", lambda commit, path: {head})
    monkeypatch.setattr(metrics, "_commit_timestamp", lambda commit: 100)
    monkeypatch.setattr(
        metrics, "_commit_diff_names", lambda base, commit: ["_project/analysis/pr-process-acceptance-baseline.json"]
    )
    acceptance["cohort"]["observed"]["batch_deliveries"] = copy.deepcopy(receipts)
    acceptance["efficiency"]["observed_avoidable_actions"] = 20
    errors = metrics.validate_process_acceptance(acceptance, process, hashlib.sha256(process_raw).hexdigest())
    assert errors == []


def test_acceptance_validator_rejects_weakened_frozen_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy
    import hashlib
    import json as _json2

    repo_root = Path(metrics.__file__).resolve().parents[2]
    process_path = repo_root / "_project" / "analysis" / "pr-process-acceptance-baseline.json"
    process_raw = process_path.read_bytes()
    process = _json2.loads(process_raw.decode("utf-8"))
    digest = hashlib.sha256(process_raw).hexdigest()
    frozen = process["prospective_cohort"]
    receipts = _valid_delivery_receipts()
    synthetic_heads = {receipt["integration_head"] for receipt in receipts[1:]}
    real_is_ancestor = metrics._is_ancestor
    monkeypatch.setattr(
        metrics,
        "_is_ancestor",
        lambda commit, head="HEAD": True if commit in synthetic_heads else real_is_ancestor(commit, head),
    )

    def conforming() -> dict:
        acceptance = _acceptance_doc()
        acceptance["process_binding"] = {
            "criteria_version": process["criteria_version"],
            "process_digest": digest,
        }
        acceptance["incident_replays"] = [
            {"scenario": name, "status": "pass"} for name in process["incident_scenarios"]
        ]
        acceptance["cohort"]["required"]["min_prs"] = frozen["min_prs"]
        acceptance["cohort"]["required"]["min_days"] = frozen["min_days"]
        acceptance["cohort"]["required"]["strata"] = list(frozen["strata"])
        acceptance["cohort"]["observed"]["prs"] = frozen["min_prs"]
        acceptance["cohort"]["observed"]["days"] = frozen["min_days"]
        acceptance["cohort"]["observed"]["strata"] = list(frozen["strata"])
        acceptance["cohort"]["observed"]["human_hold"] = True
        acceptance["cohort"]["observed"]["batch_deliveries"] = copy.deepcopy(receipts)
        return acceptance

    weakened = conforming()
    weakened["cohort"]["required"]["min_prs"] = frozen["min_prs"] - 1
    errors = metrics.validate_process_acceptance(weakened, process, digest)
    assert any("weakens the frozen floor" in e for e in errors)

    dropped = conforming()
    dropped["incident_replays"] = dropped["incident_replays"][:-1]
    errors = metrics.validate_process_acceptance(dropped, process, digest)
    assert any("replay scenario set" in e for e in errors)

    invented = conforming()
    invented["incident_replays"].append({"scenario": "invented-easy", "status": "pass"})
    errors = metrics.validate_process_acceptance(invented, process, digest)
    assert any("replay scenario set" in e for e in errors)

    few = conforming()
    few["cohort"]["observed"]["batch_deliveries"] = receipts[:1]
    errors = metrics.validate_process_acceptance(few, process, digest)
    assert any("batch deliveries insufficient" in e for e in errors)

    no_hold = conforming()
    del no_hold["cohort"]["observed"]["human_hold"]
    errors = metrics.validate_process_acceptance(no_hold, process, digest)
    assert any("human hold" in e for e in errors)

    weak_days = conforming()
    weak_days["cohort"]["required"]["min_days"] = frozen["min_days"] - 1
    errors = metrics.validate_process_acceptance(weak_days, process, digest)
    assert any("weakens the frozen floor" in e for e in errors)

    changed_strata = conforming()
    changed_strata["cohort"]["required"]["strata"] = list(frozen["strata"]) + ["invented"]
    errors = metrics.validate_process_acceptance(changed_strata, process, digest)
    assert any("frozen strata" in e for e in errors)

    duplicate = conforming()
    duplicate_receipt = receipts[0]
    duplicate["cohort"]["observed"]["batch_deliveries"] = [duplicate_receipt, duplicate_receipt]
    errors = metrics.validate_process_acceptance(duplicate, process, digest)
    assert any("duplicates batch_id" in e or "duplicates integration_head" in e for e in errors)

    unbound = conforming()
    unbound["cohort"]["observed"]["batch_deliveries"][0]["integration_head"] = "f" * 40
    errors = metrics.validate_process_acceptance(unbound, process, digest)
    assert any("not reachable" in e for e in errors)

    failed_member = conforming()
    first_acceptance = failed_member["cohort"]["observed"]["batch_deliveries"][0]["acceptance"]
    first_acceptance[next(iter(first_acceptance))] = "fail"
    errors = metrics.validate_process_acceptance(failed_member, process, digest)
    assert any("exactly 'pass'" in e for e in errors)

    no_dependency = conforming()
    for receipt in no_dependency["cohort"]["observed"]["batch_deliveries"]:
        receipt["internal_implementation_dependencies"] = []
    errors = metrics.validate_process_acceptance(no_dependency, process, digest)
    assert any("dependency evidence insufficient" in e for e in errors)

    weak_reduction = conforming()
    weak_reduction["efficiency"]["observed_avoidable_actions"] = 60
    errors = metrics.validate_process_acceptance(weak_reduction, process, digest)
    assert any("below frozen 50%" in e for e in errors)


def test_acceptance_binding_preserves_original_freeze_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json4

    DURABLE = "a" * 40
    FREEZE = "b" * 40
    TAMPERED_SHA = "c" * 40
    BUNDLED = "d" * 40
    FORGED = "e" * 40
    BASE_REF = "f" * 40
    OTHER_PARENT = "09" * 20

    frozen_bytes = b'{"frozen": true}'
    pinned_bytes = _json4.dumps({"registration": {"commit": DURABLE, "original_commit": FREEZE}}).encode()
    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: pinned_bytes if path == "_project/analysis/pr-process-acceptance.json" else frozen_bytes,
    )
    monkeypatch.setattr(metrics, "_is_ancestor", lambda commit, head="HEAD": commit == DURABLE)
    monkeypatch.setattr(metrics, "_sha256_bytes", lambda raw: "digest")
    monkeypatch.setattr(metrics, "_commit_timestamp", lambda commit: 100)
    monkeypatch.setattr(metrics, "_commit_diff_names", lambda base, commit: ["baseline.json"])
    monkeypatch.setattr(metrics, "_commit_parent", lambda commit: BASE_REF if commit == FREEZE else None)
    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: False)

    base = _acceptance_doc()
    base["process_binding"] = {"criteria_version": "1.0.0", "process_digest": "digest"}
    base["registration"] = {"commit": DURABLE, "time": "2026-09-08T12:33:43Z"}
    process = _process_doc()
    process["base_commit_at_freeze"] = BASE_REF

    preserved = dict(base)
    preserved["registration"] = {
        "commit": DURABLE,
        "original_commit": FREEZE,
        "time": "2026-09-08T12:33:43Z",
    }
    assert metrics._check_acceptance_binding(preserved, process, "digest", "baseline.json") == []

    tampered = dict(base)
    tampered["registration"] = {
        "commit": DURABLE,
        "original_commit": TAMPERED_SHA,
        "time": "2026-09-08T12:33:43Z",
    }
    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: (
            pinned_bytes
            if path == "_project/analysis/pr-process-acceptance.json"
            else (b'{"tampered": true}' if revision == TAMPERED_SHA else frozen_bytes)
        ),
    )
    monkeypatch.setattr(
        metrics,
        "_sha256_bytes",
        lambda raw: "tampered-digest" if raw == b'{"tampered": true}' else "digest",
    )
    errors = metrics._check_acceptance_binding(tampered, process, "digest", "baseline.json")
    assert any("original registration commit" in e for e in errors)

    monkeypatch.setattr(metrics, "_git_show_bytes", lambda revision, path: None)
    errors = metrics._check_acceptance_binding(preserved, process, "digest", "baseline.json")
    assert any("not resolvable" in e for e in errors)

    dropped = dict(base)
    dropped["registration"] = {"commit": DURABLE, "time": "2026-09-08T12:33:43Z"}
    errors = metrics._check_acceptance_binding(dropped, process, "digest", "baseline.json")
    assert any("original_commit" in e for e in errors)

    based_process = dict(process)
    based_process["base_commit_at_freeze"] = BASE_REF
    parents = {FREEZE: BASE_REF, BUNDLED: OTHER_PARENT}
    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: pinned_bytes if path == "_project/analysis/pr-process-acceptance.json" else frozen_bytes,
    )
    monkeypatch.setattr(metrics, "_sha256_bytes", lambda raw: "digest")
    monkeypatch.setattr(metrics, "_commit_parent", lambda commit: parents.get(commit))
    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: False)
    assert metrics._check_acceptance_binding(preserved, based_process, "digest", "baseline.json") == []

    bundled = dict(base)
    bundled["registration"] = {
        "commit": DURABLE,
        "original_commit": BUNDLED,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(bundled, based_process, "digest", "baseline.json")
    assert any("freeze-only child" in e for e in errors)

    self_certified = dict(base)
    self_certified["registration"] = {
        "commit": BUNDLED,
        "original_commit": BUNDLED,
        "time": "2026-09-08T12:33:43Z",
    }
    monkeypatch.setattr(metrics, "_is_ancestor", lambda commit, head="HEAD": True)
    errors = metrics._check_acceptance_binding(self_certified, based_process, "digest", "baseline.json")
    assert any("freeze-only child" in e for e in errors)

    import json as _json3

    pinned_record = _json3.dumps({"registration": {"commit": DURABLE, "original_commit": FREEZE}}).encode()
    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: pinned_record if path == "acceptance.json" else frozen_bytes,
    )
    timestamps = {FREEZE: 100, DURABLE: 200, FORGED: 300}
    monkeypatch.setattr(metrics, "_commit_timestamp", lambda commit: timestamps.get(commit))
    anchored = dict(base)
    anchored["registration"] = {
        "commit": DURABLE,
        "original_commit": FREEZE,
        "time": "2026-09-08T12:33:43Z",
    }
    assert (
        metrics._check_acceptance_binding(anchored, based_process, "digest", "baseline.json", "acceptance.json") == []
    )

    forged = dict(base)
    forged["registration"] = {
        "commit": DURABLE,
        "original_commit": FORGED,
        "time": "2026-09-08T12:33:43Z",
    }
    monkeypatch.setattr(
        metrics,
        "_commit_parent",
        lambda commit: BASE_REF if commit in (FREEZE, FORGED) else None,
    )
    errors = metrics._check_acceptance_binding(forged, based_process, "digest", "baseline.json", "acceptance.json")
    assert any("anchored by published history" in e for e in errors)
    assert any("newer than the durable" in e for e in errors)

    monkeypatch.setattr(
        metrics, "_git_show_bytes", lambda revision, path: None if path == "acceptance.json" else frozen_bytes
    )
    errors = metrics._check_acceptance_binding(anchored, based_process, "digest", "baseline.json", "acceptance.json")
    assert any("pins no registration pointers" in e for e in errors)

    bundled_diff_sha = "cd" * 20
    monkeypatch.setattr(
        metrics,
        "_commit_diff_names",
        lambda base, commit: (
            ["baseline.json", "scripts/pr_landing.py"] if commit == bundled_diff_sha else ["baseline.json"]
        ),
    )
    monkeypatch.setattr(metrics, "_commit_parent", lambda commit: BASE_REF)
    monkeypatch.setattr(metrics, "_is_ancestor", lambda commit, head="HEAD": True)
    bundled_diff = dict(base)
    bundled_diff["registration"] = {
        "commit": DURABLE,
        "original_commit": bundled_diff_sha,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(
        bundled_diff, based_process, "digest", "baseline.json", "acceptance.json"
    )
    assert any("more than the frozen baseline" in e for e in errors)

    same_commit = dict(base)
    same_commit["registration"] = {
        "commit": FREEZE,
        "original_commit": FREEZE,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(same_commit, based_process, "digest", "baseline.json", "acceptance.json")
    assert errors == []

    merge_sha = "ab" * 20
    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: commit == merge_sha)
    monkeypatch.setattr(
        metrics,
        "_commit_parent",
        lambda commit: BASE_REF if commit in (FREEZE, FORGED, merge_sha) else None,
    )
    merge_freeze = dict(base)
    merge_freeze["registration"] = {
        "commit": DURABLE,
        "original_commit": merge_sha,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(
        merge_freeze, based_process, "digest", "baseline.json", "acceptance.json"
    )
    assert any("merge commit" in e for e in errors)

    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: None)
    errors = metrics._check_acceptance_binding(
        merge_freeze, based_process, "digest", "baseline.json", "acceptance.json"
    )
    assert any("parent count not resolvable" in e for e in errors)

    refname = dict(base)
    refname["registration"] = {
        "commit": DURABLE,
        "original_commit": "freeze-v1",
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(refname, based_process, "digest", "baseline.json", "acceptance.json")
    assert any("full commit SHA" in e for e in errors)

    upper = dict(base)
    upper["registration"] = {
        "commit": DURABLE,
        "original_commit": "B" * 40,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(upper, based_process, "digest", "baseline.json", "acceptance.json")
    assert errors and not any("full commit SHA" in e for e in errors)

    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: pinned_record if path == "acceptance.json" else frozen_bytes,
    )
    monkeypatch.setattr(metrics, "_commit_parent", lambda commit: BASE_REF)
    monkeypatch.setattr(metrics, "_commit_timestamp", lambda commit: 100)
    monkeypatch.setattr(metrics, "_commit_diff_names", lambda base, commit: ["baseline.json"])
    monkeypatch.setattr(metrics, "_is_ancestor", lambda commit, head="HEAD": True)
    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: False)
    cased = dict(base)
    cased["registration"] = {
        "commit": DURABLE,
        "original_commit": "B" * 40,
        "time": "2026-09-08T12:33:43Z",
    }
    errors = metrics._check_acceptance_binding(cased, based_process, "digest", "baseline.json", "acceptance.json")
    assert errors == []

    upper_base = dict(based_process)
    upper_base["base_commit_at_freeze"] = BASE_REF.upper()
    errors = metrics._check_acceptance_binding(preserved, upper_base, "digest", "baseline.json", "acceptance.json")
    assert errors == []


def test_acceptance_binding_rejects_branch_local_durable_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json5

    DURABLE = "a" * 40
    FREEZE = "b" * 40
    BASE_REF = "c" * 40
    frozen_bytes = b'{"frozen": true}'
    pinned_bytes = _json5.dumps({"registration": {"commit": DURABLE, "original_commit": FREEZE}}).encode()

    monkeypatch.setattr(
        metrics,
        "_git_show_bytes",
        lambda revision, path: pinned_bytes if path == "acceptance.json" else frozen_bytes,
    )
    monkeypatch.setattr(metrics, "_sha256_bytes", lambda raw: "digest")
    monkeypatch.setattr(metrics, "_commit_parent", lambda commit: BASE_REF if commit == FREEZE else None)
    monkeypatch.setattr(metrics, "_has_second_parent", lambda commit: False)
    monkeypatch.setattr(metrics, "_commit_diff_names", lambda base, commit: ["baseline.json"])
    monkeypatch.setattr(metrics, "_commit_timestamp", lambda commit: 100)
    monkeypatch.setattr(
        metrics,
        "_is_ancestor",
        lambda commit, head="HEAD": head == "HEAD" and commit == DURABLE,
    )

    acceptance = _acceptance_doc()
    acceptance["process_binding"] = {"criteria_version": "1.0.0", "process_digest": "digest"}
    acceptance["registration"] = {"commit": DURABLE, "original_commit": FREEZE}
    process = _process_doc()
    process["base_commit_at_freeze"] = BASE_REF

    errors = metrics._check_acceptance_binding(
        acceptance, process, "digest", "baseline.json", "acceptance.json", "origin/develop"
    )
    assert any("published ref origin/develop" in error for error in errors)

    acceptance["registration"]["original_commit"] = DURABLE
    errors = metrics._check_acceptance_binding(
        acceptance, process, "digest", "baseline.json", "acceptance.json", "origin/develop"
    )
    assert any("published ref origin/develop" in error for error in errors)


def test_acceptance_cli_forwards_selected_acceptance_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}
    real_binding = metrics._check_acceptance_binding

    def binding_spy(acceptance, process, digest, process_relpath, acceptance_relpath, published_ref):
        seen["acceptance_relpath"] = acceptance_relpath
        seen["process_relpath"] = process_relpath
        seen["published_ref"] = published_ref
        return real_binding(acceptance, process, digest, process_relpath, acceptance_relpath, published_ref)

    monkeypatch.setattr(metrics, "_check_acceptance_binding", binding_spy)
    metrics.validate_process_acceptance(
        _acceptance_doc(),
        _process_doc(),
        "digest",
        process_relpath="custom/base.json",
        acceptance_relpath="custom/accept.json",
    )
    assert seen["acceptance_relpath"] == "custom/accept.json"
    assert seen["process_relpath"] == "custom/base.json"
    assert seen["published_ref"] == "origin/develop"

    captured: dict = {}

    def validate_spy(acceptance, process, digest, process_relpath=None, acceptance_relpath=None):
        captured["acceptance_relpath"] = acceptance_relpath
        captured["process_relpath"] = process_relpath
        return []

    monkeypatch.setattr(metrics, "validate_process_acceptance", validate_spy)
    repo_root = Path(metrics.__file__).resolve().parents[2]
    acceptance_path = repo_root / "_project" / "analysis" / "pr-process-acceptance.json"
    process_path = repo_root / "_project" / "analysis" / "pr-process-acceptance-baseline.json"
    assert metrics.run_validate_process_acceptance(str(acceptance_path), str(process_path)) == 0
    assert captured["acceptance_relpath"] == "_project/analysis/pr-process-acceptance.json"
    assert captured["process_relpath"] == "_project/analysis/pr-process-acceptance-baseline.json"

    outside = tmp_path / "acceptance.json"
    outside.write_text(acceptance_path.read_text(encoding="utf-8"), encoding="utf-8")
    outside_base = tmp_path / "baseline.json"
    outside_base.write_bytes(process_path.read_bytes())
    assert metrics.run_validate_process_acceptance(str(outside), str(outside_base)) == 0
    assert captured["acceptance_relpath"] == "_project/analysis/pr-process-acceptance.json"
    assert captured["process_relpath"] == "_project/analysis/pr-process-acceptance-baseline.json"


def test_commit_diff_names_parses_nul_delimited_output(monkeypatch: pytest.MonkeyPatch) -> None:
    assert metrics._commit_diff_names("0" * 40, "0" * 40) is None

    class _Proc:
        returncode = 0
        stdout = "custom/\u00e9.json\x00plain\rname.json\x00".encode()

    monkeypatch.setattr(metrics.subprocess, "run", lambda *args, **kwargs: _Proc())
    assert metrics._commit_diff_names("base", "commit") == ["custom/\u00e9.json", "plain\rname.json"]


def test_lifecycle_validator_rejects_unbound_attempts() -> None:
    import copy
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text(encoding="utf-8"))
    lifecycle = copy.deepcopy(lifecycle)
    lifecycle["prs"][0]["attempts"]["dddddddddddddddddddddddddddddddddddddddd"] = 1
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, process, "digest-of-frozen-process-baseline")
    assert any("unobserved head" in e for e in errors)


def test_refresh_audit_rejects_non_list_denominator() -> None:
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text())
    audit = (
        (FIXTURES / "refresh_audit_sample.md")
        .read_text()
        .replace('"denominator_prs": [101, 102]', '"denominator_prs": 101')
    )
    errors = metrics.validate_refresh_audit(audit, lifecycle, REASONS)
    assert any("must be a list" in e for e in errors)


def _failed_job() -> dict:
    return {
        "conclusion": "failure",
        "status": "completed",
        "started_at": "2026-08-14T00:00:00Z",
        "completed_at": "2026-08-14T00:04:00Z",
        "steps": [],
    }


def test_lifecycle_nontip_split_and_failed_bucket() -> None:
    tip, mid, root = "a" * 40, "b" * 40, "c" * 40
    commits = [
        {"sha": root, "parents": []},
        {"sha": mid, "parents": [{"sha": root}]},
        {"sha": tip, "parents": [{"sha": mid}]},
    ]
    client = _LifecycleClient(
        commits=commits,
        runs_by_sha={tip: [{"id": 1, "run_attempt": 1, "name": "Develop PR"}]},
        jobs_by_run={1: [_timed_job(6.0), _failed_job()]},
        checks_by_sha={},
    )
    pr = {"number": 7, "head": {"sha": tip}, "merged_at": None}
    lifecycle = metrics.lifecycle_for_pr(client, pr)
    assert lifecycle["heads"] == [root, mid, tip]
    assert set(lifecycle["per_head"]) == {tip}
    statuses = {m["head_sha"]: m["status"] for m in lifecycle["missing"]}
    assert statuses == {root: "non-tip-commit", mid: "non-tip-commit"}
    assert lifecycle["totals"]["missing_head_count"] == 0
    assert lifecycle["totals"]["non_tip_head_count"] == 2
    assert lifecycle["totals"]["failed_runner_minutes"] == pytest.approx(4.0)
    assert lifecycle["totals"]["completed_runner_minutes"] == pytest.approx(6.0)


def test_lifecycle_rejects_observed_head_without_retrieval_ids() -> None:
    import copy
    import json as _json

    lifecycle = _json.loads((FIXTURES / "lifecycle_sample.json").read_text(encoding="utf-8"))
    lifecycle = copy.deepcopy(lifecycle)
    first = lifecycle["prs"][0]
    sha = next(iter(first["per_head"]))
    del first["per_head"][sha]["retrieval_ids"]
    process = {"schema": "pr_process_acceptance_baseline_v1", "criteria_version": "1.0.0"}
    errors = metrics.validate_lifecycle_baseline(lifecycle, process, "digest-of-frozen-process-baseline")
    assert any("no run retrieval IDs" in e for e in errors)


def test_medium_shard_job_names_cover_the_workflow_matrix() -> None:
    from scripts.release_canary_sharding import MEDIUM_SHARD_COUNT

    assert tuple(f"medium-test (shard {i})" for i in range(MEDIUM_SHARD_COUNT)) == metrics.MEDIUM_SHARD_JOB_NAMES


import json
import re as _re
import urllib.parse as _urlparse
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
USAGE_NOW = datetime(2026, 10, 10, 13, 0, 0, tzinfo=UTC)
USAGE_DAY = datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _at(hours: int, minutes: int = 0, seconds: int = 0) -> str:
    return _stamp(USAGE_DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds))


def _usage_run(run_id: int, created: str, **overrides: object) -> dict:
    run = {
        "id": run_id,
        "path": ".github/workflows/ci.yml",
        "name": "CI",
        "display_title": "CI",
        "event": "pull_request",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "head_sha": f"sha{run_id}",
        "head_branch": f"branch{run_id}",
        "created_at": created,
        "run_started_at": created,
        "updated_at": created,
    }
    run.update(overrides)
    return run


def _usage_job(
    job_id: int, name: str, attempt: int, created: str, started: str, completed: str, conclusion: str = "success"
) -> dict:
    return {
        "id": job_id,
        "name": name,
        "run_attempt": attempt,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created,
        "started_at": started,
        "completed_at": completed,
        "labels": ["ubuntu-latest"],
        "steps": [{"name": "step"}] if conclusion != "skipped" else [],
    }


class _UsageApi:
    repo = "o/r"
    get_paginated = metrics.GitHubClient.get_paginated

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.jobs: dict[int, list[dict]] = {}
        self.attempts: dict[tuple[int, int], dict] = {}
        self.ruleset: object = [
            {
                "type": "required_status_checks",
                "ruleset_id": 9,
                "parameters": {
                    "required_status_checks": [
                        {"context": "core"},
                        {"context": "hold"},
                        {"context": "oracle-gate", "integration_id": 5},
                    ]
                },
            }
        ]
        self.commits: list[dict] = []
        self.prs: list[dict] = []
        self.check_runs: dict[str, list[dict]] = {}
        self.statuses: dict[str, list[dict]] = {}
        self.failing: set[str] = set()
        self.fuzzy_boundary = False
        self.requests: list[str] = []
        self.workflows: dict[str, str] = {
            "trunk.yml": "2026-01-01T00:00:00Z",
            "trunk-coverage.yml": "2026-01-01T00:00:00Z",
        }

    def get(self, path: str) -> object | None:
        self.requests.append(path)
        if any(marker in path for marker in self.failing):
            return None
        parts = _urlparse.urlsplit(path)
        query = _urlparse.parse_qs(parts.query)
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])

        def paged(items: list) -> list:
            return items[(page - 1) * per_page : page * per_page]

        route = parts.path
        if route == "/repos/o/r/actions/runs":
            low_text, high_text = query["created"][0].split("..")
            low, high = metrics._iso_to_dt(low_text), metrics._iso_to_dt(high_text)
            if self.fuzzy_boundary:
                low, high = low - timedelta(seconds=1), high + timedelta(seconds=1)
            matching = [run for run in self.runs if low <= metrics._iso_to_dt(run["created_at"]) <= high]
            matching.sort(key=lambda run: (run["created_at"], run["id"]), reverse=True)
            return {"total_count": len(matching), "workflow_runs": paged(matching[:1000])}
        jobs_match = _re.fullmatch(r"/repos/o/r/actions/runs/(\d+)/jobs", route)
        if jobs_match:
            jobs = self.jobs.get(int(jobs_match.group(1)), [])
            return {"total_count": len(jobs), "jobs": paged(jobs)}
        attempt_match = _re.fullmatch(r"/repos/o/r/actions/runs/(\d+)/attempts/(\d+)", route)
        if attempt_match:
            return self.attempts.get((int(attempt_match.group(1)), int(attempt_match.group(2))))
        workflow_match = _re.fullmatch(r"/repos/o/r/actions/workflows/([^/]+)", route)
        if workflow_match:
            created = self.workflows.get(workflow_match.group(1))
            return None if created is None else {"created_at": created}
        if route == "/repos/o/r/rules/branches/develop":
            return self.ruleset
        if route == "/repos/o/r/commits":
            return paged(self.commits)
        if route == "/repos/o/r/pulls":
            return paged(self.prs)
        checks_match = _re.fullmatch(r"/repos/o/r/commits/([^/]+)/check-runs", route)
        if checks_match:
            items = self.check_runs.get(checks_match.group(1), [])
            return {"total_count": len(items), "check_runs": paged(items)}
        statuses_match = _re.fullmatch(r"/repos/o/r/commits/([^/]+)/statuses", route)
        if statuses_match:
            return paged(self.statuses.get(statuses_match.group(1), []))
        return None


def _collect(api: _UsageApi, since: datetime, until: datetime, cache=None, **kwargs):
    return metrics.collect_usage(
        api,
        since=since,
        until=until,
        now=USAGE_NOW,
        cache=cache or metrics.DiskCache(None),
        workers=1,
        **kwargs,
    )


def _day_runs(count: int, burst_at: tuple[int, ...] = ()) -> list[dict]:
    runs = [_usage_run(1000 + i, _stamp(USAGE_DAY + timedelta(seconds=i * 30))) for i in range(count)]
    for offset, second in enumerate(burst_at):
        for i in range(100):
            runs.append(_usage_run(50000 + offset * 1000 + i, _stamp(USAGE_DAY + timedelta(seconds=second))))
    return runs


def test_parse_report_window_is_inclusive_and_capped_at_now() -> None:
    since, until = metrics.parse_report_window("2026-10-10", "2026-10-10", USAGE_NOW + timedelta(days=5))
    assert since == USAGE_DAY
    assert until == datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)
    _, capped = metrics.parse_report_window("2026-10-10", "2026-10-12", USAGE_NOW)
    assert capped == USAGE_NOW
    with pytest.raises(ValueError):
        metrics.parse_report_window("2026-10-12", "2026-10-10", USAGE_NOW + timedelta(days=5))


def test_collect_runs_partitions_below_the_cap_and_keeps_every_boundary_run() -> None:
    api = _UsageApi()
    api.runs = _day_runs(2400, burst_at=(43199, 43200))
    until = datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)
    collection = _collect(api, USAGE_DAY, until)
    assert len(api.runs) == 2600
    assert sorted(run["id"] for run in collection.runs) == sorted(run["id"] for run in api.runs)
    assert collection.gaps == []
    assert collection.stats["partition_splits"] >= 2
    assert collection.stats["partitions"] >= 3
    report = metrics.build_usage_report(collection)
    assert report["completeness"]["status"] == "complete"
    assert report["workflows"]["overall"]["runs"] == 2600


def test_collect_runs_drops_duplicates_from_overlapping_partitions() -> None:
    api = _UsageApi()
    api.fuzzy_boundary = True
    api.runs = _day_runs(2400, burst_at=(43199, 43200))
    collection = _collect(api, USAGE_DAY, datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC))
    assert len(collection.runs) == 2600
    assert len({run["id"] for run in collection.runs}) == 2600
    assert collection.stats["duplicate_runs_dropped"] > 0
    assert collection.gaps == []


def test_collect_runs_reports_an_unsplittable_interval_as_a_gap() -> None:
    api = _UsageApi()
    api.runs = [_usage_run(i, _at(1)) for i in range(1001)]
    collection = _collect(api, USAGE_DAY + timedelta(hours=1), USAGE_DAY + timedelta(hours=1))
    assert len(collection.runs) == 1000
    assert [gap["scope"] for gap in collection.gaps] == ["run_partition"]
    assert metrics.build_usage_report(collection)["completeness"]["status"] == "incomplete"


def test_collect_runs_reports_failed_partition_and_failed_page_as_gaps() -> None:
    failing = _UsageApi()
    failing.runs = [_usage_run(i, _at(1, 0, i)) for i in range(5)]
    failing.failing = {"created="}
    lost = _collect(failing, USAGE_DAY, USAGE_DAY + timedelta(hours=2))
    assert lost.runs == []
    assert lost.gaps[0]["scope"] == "run_partition"
    paging = _UsageApi()
    paging.runs = [_usage_run(i, _at(1, 0, i % 60)) for i in range(150)]
    paging.failing = {"page=2"}
    partial = _collect(paging, USAGE_DAY, USAGE_DAY + timedelta(hours=2))
    assert len(partial.runs) == 100
    assert any("page 2" in gap["detail"] for gap in partial.gaps)


def test_failed_job_retrieval_yields_a_qualified_report_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _UsageApi()
    api.runs = [_usage_run(5, _at(1)), _usage_run(6, _at(2))]
    api.jobs = {5: [], 6: [_usage_job(1, "lint", 1, _at(2), _at(2, 1), _at(2, 3))]}
    api.failing = {"/runs/5/jobs"}
    collection = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=5))
    assert [gap["run_id"] for gap in collection.gaps] == [5]
    text = metrics.render_usage_report(metrics.build_usage_report(collection))
    assert "INCOMPLETE" in text and "COMPLETE (no retrieval gaps)" not in text

    monkeypatch.setattr(metrics, "make_client", lambda _repo: api)
    output = tmp_path / "report.json"
    code = metrics.main(
        ["--usage-report", "--since", "2026-10-10", "--until", "2026-10-10", "--cache-dir", "", "--output", str(output)]
    )
    assert code == 3
    assert json.loads(output.read_text())["completeness"]["status"] == "incomplete"
    assert "INCOMPLETE" in capsys.readouterr().out
    api.failing = set()
    assert metrics.main(["--usage-report", "--since", "2026-10-10", "--until", "2026-10-10", "--cache-dir", ""]) == 0


def test_usage_report_requires_a_window_and_api_access(monkeypatch: pytest.MonkeyPatch) -> None:
    assert metrics.main(["--usage-report"]) == 2
    monkeypatch.setattr(metrics, "make_client", lambda _repo: None)
    assert metrics.main(["--usage-report", "--since", "2026-10-10", "--until", "2026-10-10"]) == 2


def test_rerun_wait_is_not_queue_time_and_reused_jobs_are_not_recounted() -> None:
    api = _UsageApi()
    api.runs = [
        _usage_run(
            1,
            _at(18, 59, 28),
            run_attempt=2,
            run_started_at=_at(19, 54, 48),
            updated_at=_at(20, 9, 25),
        )
    ]
    api.attempts[(1, 1)] = {
        "run_attempt": 1,
        "conclusion": "failure",
        "created_at": _at(18, 59, 28),
        "run_started_at": _at(18, 59, 28),
        "updated_at": _at(19, 53, 50),
    }
    api.jobs[1] = [
        _usage_job(1, "ci-paths", 1, _at(18, 59, 29), _at(19, 7, 26), _at(19, 8, 44)),
        _usage_job(2, "core", 1, _at(19, 51, 27), _at(19, 53, 40), _at(19, 53, 49), "failure"),
        _usage_job(3, "ci-paths", 2, _at(19, 54, 57), _at(19, 7, 26), _at(19, 8, 44)),
        _usage_job(4, "core", 2, _at(20, 3, 42), _at(20, 9, 16), _at(20, 9, 24)),
        _usage_job(5, "docs", 2, _at(19, 54, 50), _at(19, 54, 50), _at(18, 59, 29), "skipped"),
    ]
    report = metrics.build_usage_report(_collect(api, USAGE_DAY, USAGE_DAY + timedelta(days=1) - timedelta(seconds=1)))
    row = report["workflows"]["per_workflow_event"][0]
    assert row["job_queue_seconds"]["n"] == 3
    assert row["job_queue_seconds"]["median"] == 334
    assert row["job_queue_seconds"]["max"] == 477
    assert row["job_kinds"] == {"executed": 3, "reused": 1, "skipped": 1}
    assert row["runner_minutes"]["completed"] == pytest.approx((78 + 8) / 60, abs=0.01)
    assert row["runner_minutes"]["failed"] == pytest.approx(9 / 60, abs=0.01)
    assert row["rerun_runs"] == 1
    assert row["passed_only_on_rerun"] == 1
    assert row["first_attempt_conclusions"] == {"failure": 1}
    assert row["rerun_attempt_outcomes"] == {"2": {"success": 1}}


def test_partial_rerun_without_attempt_record_is_flagged_and_derived() -> None:
    api = _UsageApi()
    api.runs = [_usage_run(1, _at(1), run_attempt=2)]
    api.jobs[1] = [
        _usage_job(1, "core", 1, _at(1), _at(1, 1), _at(1, 2), "failure"),
        _usage_job(2, "core", 2, _at(1, 30), _at(1, 31), _at(1, 32)),
    ]
    collection = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=3))
    assert [gap["detail"] for gap in collection.gaps] == ["attempt 1: request failed"]
    report = metrics.build_usage_report(collection)
    assert report["completeness"]["status"] == "incomplete"
    assert any("derived from job conclusions" in note for note in report["completeness"]["qualifications"])
    assert report["workflows"]["overall"]["first_attempt_conclusions"] == {"failure": 1}


def _pr(number: int, created: str, merged: str, sha: str) -> dict:
    return {
        "number": number,
        "created_at": created,
        "merged_at": merged,
        "updated_at": merged,
        "head": {"ref": f"pr{number}", "sha": sha},
    }


def _green_check(name: str, start: str, end: str) -> dict:
    return {"name": name, "status": "completed", "conclusion": "success", "started_at": start, "completed_at": end}


def _two_pr_api() -> _UsageApi:
    api = _UsageApi()
    api.prs = [_pr(2, _at(12), _at(13), "B"), _pr(1, _at(10), _at(11), "A")]
    api.runs = [
        _usage_run(1, _at(10, 1), head_sha="A", head_branch="pr1"),
        _usage_run(2, _at(12, 1), head_sha="B", head_branch="pr2"),
    ]
    api.jobs = {1: [_usage_job(1, "lint", 1, _at(10, 1), _at(10, 2), _at(10, 12))], 2: []}
    api.check_runs = {
        "A": [_green_check("core", _at(10, 2), _at(10, 20)), _green_check("hold", _at(10, 3), _at(10, 5))],
        "B": [_green_check("core", _at(12, 2), _at(12, 20)), _green_check("hold", _at(12, 3), _at(12, 5))],
    }
    api.statuses = {"B": [{"context": "oracle-gate", "state": "success", "created_at": _at(12, 40)}]}
    return api


def test_required_contexts_come_from_the_ruleset_and_historical_heads_use_the_stated_rule() -> None:
    api = _two_pr_api()
    collection = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=14))
    assert collection.required["contexts"] == ["core", "hold", "oracle-gate"]
    assert collection.required["contexts"] != list(metrics.REQUIRED_CONTEXT_NAMES)
    assert collection.required["source"] == "live_ruleset"
    assert collection.required["endpoint"] == "/repos/o/r/rules/branches/develop"
    assert collection.required["ruleset_ids"] == [9]
    assert collection.required["integration_ids"]["oracle-gate"] == 5

    section = metrics.build_usage_report(collection)["pull_requests"]
    policy = section["context_policy"]
    assert policy["effective_from_basis"] == {
        "core": "first_observation",
        "hold": "first_observation",
        "oracle-gate": "first_observation",
    }
    assert policy["effective_from"]["oracle-gate"] == _at(12, 1)
    assert "proxy" in section["timestamp_source"]
    heads = {row["sha"]: row for row in section["head_rows"]}
    assert heads["A"]["excluded_contexts"] == ["oracle-gate"]
    assert heads["A"]["status"] == "green"
    assert heads["A"]["seconds_to_all_required_ready"] == 19 * 60
    assert heads["B"]["seconds_to_ci_compute_ready"] == 19 * 60
    assert heads["B"]["seconds_to_all_required_ready"] == 39 * 60
    assert heads["B"]["ci_compute_contexts"] == ["core", "hold"]
    assert section["per_pr"]["seconds_to_all_required_ready"]["n"] == 2

    collection.effective_from_override = {"oracle-gate": USAGE_DAY}
    overridden = metrics.build_usage_report(collection)["pull_requests"]
    assert overridden["context_policy"]["effective_from_basis"]["oracle-gate"] == "override"
    assert {row["sha"]: row["status"] for row in overridden["head_rows"]} == {"A": "unresolved", "B": "green"}
    assert overridden["pr_status_counts"] == {"green": 1, "unresolved": 1}


def test_unobserved_heads_and_missing_ruleset_are_reported_not_hidden() -> None:
    api = _two_pr_api()
    api.runs = [run for run in api.runs if run["head_sha"] != "A"]
    collection = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=14))
    rows = {row["sha"]: row for row in metrics.build_usage_report(collection)["pull_requests"]["head_rows"]}
    assert rows["A"]["status"] == "missing_head_observation"
    assert "seconds_to_all_required_ready" not in rows["A"]

    broken = _two_pr_api()
    broken.ruleset = {"message": "not found"}
    lost = _collect(broken, USAGE_DAY, USAGE_DAY + timedelta(hours=14))
    assert any(gap["scope"] == "required_contexts" for gap in lost.gaps)
    report = metrics.build_usage_report(lost)
    assert report["pull_requests"]["available"] is False
    assert report["completeness"]["status"] == "incomplete"

    override = _collect(_two_pr_api(), USAGE_DAY, USAGE_DAY + timedelta(hours=14), required_override=["core"])
    assert override.required["source"] == "command_line_override"
    assert metrics.build_usage_report(override)["pull_requests"]["context_policy"]["contexts"] == ["core"]


def test_pr_runner_minutes_cover_the_lifecycle_and_unmerged_work() -> None:
    api = _two_pr_api()
    api.runs.append(_usage_run(3, _at(5), head_sha="Z", head_branch="closed"))
    api.jobs[3] = [_usage_job(9, "lint", 1, _at(5), _at(5, 1), _at(5, 11))]
    section = metrics.build_usage_report(_collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=14)))["pull_requests"]
    usage = section["runner_minutes"]
    assert usage["all_runs_total"] == 20.0
    assert usage["pr_lifecycle_attributed_total"] == 10.0
    assert usage["not_on_merged_pr_heads"] == 10.0
    assert usage["all_runs_per_merge"] == 10.0
    rows = {row["number"]: row for row in section["pr_rows"]}
    assert rows[1]["lifecycle_runner_minutes"] == 10.0
    assert rows[2]["lifecycle_runner_minutes"] == 0.0


def _trunk_run(run_id: int, sha: str, commit_at: str, conclusion: str, ended: str, **extra: object) -> dict:
    return _usage_run(
        run_id,
        commit_at,
        path=".github/workflows/trunk.yml",
        event="push",
        head_branch="develop",
        head_sha=sha,
        conclusion=conclusion,
        updated_at=ended,
        head_commit={"timestamp": commit_at},
        **extra,
    )


def test_post_merge_signal_counts_incidents_missing_and_duplicate_runs() -> None:
    api = _UsageApi()
    times = {"c1": _at(10), "c2": _at(10, 30), "c3": _at(11), "c4": _at(11, 30), "c5": _at(12)}
    api.commits = [{"sha": sha, "commit": {"committer": {"date": when}}} for sha, when in times.items()]
    api.runs = [
        _trunk_run(1, "c1", times["c1"], "success", _at(10, 20)),
        _trunk_run(2, "c2", times["c2"], "success", _at(11, 20), run_attempt=2),
        _trunk_run(3, "c3", times["c3"], "failure", _at(11, 40)),
        _trunk_run(5, "c5", times["c5"], "success", _at(12, 30)),
        _trunk_run(6, "c5", times["c5"], "success", _at(12, 35)),
    ]
    api.attempts[(2, 1)] = {
        "run_attempt": 1,
        "conclusion": "failure",
        "created_at": times["c2"],
        "updated_at": _at(10, 50),
    }
    api.jobs = {
        1: [_usage_job(1, "unit", 1, times["c1"], _at(10, 1), _at(10, 11))],
        3: [
            _usage_job(2, "unit", 1, times["c3"], _at(11, 1), _at(11, 9)),
            _usage_job(3, "extra", 1, times["c3"], times["c3"], times["c3"], "skipped"),
        ],
    }
    collection = _collect(api, USAGE_DAY + timedelta(hours=10), USAGE_DAY + timedelta(hours=13))
    signal = metrics.build_usage_report(collection)["post_merge"]["workflows"]["trunk.yml"]
    assert signal["runs"] == 5
    assert signal["first_attempt_red"] == 2
    assert signal["first_attempt_red_rate"] == pytest.approx(0.4)
    assert signal["eventual_success_rate"] == pytest.approx(0.8)
    assert signal["incident_count"] == 2
    first, second = signal["incidents"]
    assert first["opened_at"] == _at(10, 50) and first["recovered_at"] == _at(11, 20)
    assert first["seconds_to_recovery"] == 1800
    assert first["merges_admitted"] == 1
    assert second["opened_at"] == _at(11, 40) and second["recovered_at"] == _at(12, 30)
    assert second["merges_admitted"] == 1
    assert signal["missing_exact_sha_runs"] == 1
    assert signal["missing_exact_sha_examples"] == ["c4"]
    assert signal["duplicate_exact_sha_runs"] == 1
    assert signal["duplicate_exact_sha_examples"] == {"c5": [5, 6]}
    assert signal["newest_tested_commit_age_seconds"] == 3600
    assert signal["job_kinds"]["skipped"] == 1
    coverage = metrics.build_usage_report(collection)["post_merge"]["workflows"]["trunk-coverage.yml"]
    assert coverage["runs"] == 0 and coverage["first_attempt_red_rate"] is None


def test_open_red_incident_is_reported_as_ongoing() -> None:
    api = _UsageApi()
    api.commits = [
        {"sha": "c1", "commit": {"committer": {"date": _at(10)}}},
        {"sha": "c2", "commit": {"committer": {"date": _at(11)}}},
    ]
    api.runs = [_trunk_run(1, "c1", _at(10), "failure", _at(10, 20))]
    collection = _collect(api, USAGE_DAY + timedelta(hours=10), USAGE_DAY + timedelta(hours=13))
    incident = metrics.build_usage_report(collection)["post_merge"]["workflows"]["trunk.yml"]["incidents"][0]
    assert incident["ongoing"] is True and incident["seconds_to_recovery"] is None
    assert incident["merges_admitted"] == 1


def test_commits_without_runs_are_missing_unless_the_workflow_did_not_exist_yet() -> None:
    api = _UsageApi()
    times = {"c1": _at(10), "c2": _at(11), "c3": _at(12)}
    api.commits = [{"sha": sha, "commit": {"committer": {"date": when}}} for sha, when in times.items()]
    api.runs = [_trunk_run(1, "c3", times["c3"], "success", _at(12, 20))]
    collection = _collect(api, USAGE_DAY + timedelta(hours=10), USAGE_DAY + timedelta(hours=13))
    signal = metrics.build_usage_report(collection)["post_merge"]["workflows"]["trunk.yml"]
    assert signal["missing_exact_sha_runs"] == 2
    assert signal["commits_before_workflow_existed"] == 0

    api.workflows["trunk.yml"] = _at(11, 30)
    created = _collect(api, USAGE_DAY + timedelta(hours=10), USAGE_DAY + timedelta(hours=13))
    signal = metrics.build_usage_report(created)["post_merge"]["workflows"]["trunk.yml"]
    assert signal["missing_exact_sha_runs"] == 0
    assert signal["commits_before_workflow_existed"] == 2
    assert signal["workflow_created_at"] == _at(11, 30)

    del api.workflows["trunk.yml"]
    unknown = _collect(api, USAGE_DAY + timedelta(hours=10), USAGE_DAY + timedelta(hours=13))
    assert [gap["scope"] for gap in unknown.gaps] == ["workflow_metadata"]
    signal = metrics.build_usage_report(unknown)["post_merge"]["workflows"]["trunk.yml"]
    assert signal["missing_exact_sha_runs"] == 2


def test_failed_head_check_fetch_is_a_gap_and_does_not_break_the_pr_section() -> None:
    api = _two_pr_api()
    api.failing = {"/commits/B/check-runs"}
    collection = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=14))
    assert [gap["scope"] for gap in collection.gaps] == ["head_checks"]
    report = metrics.build_usage_report(collection)
    assert report["completeness"]["status"] == "incomplete"
    section = report["pull_requests"]
    assert section["pr_status_counts"] == {"no_check_data": 1, "unresolved": 1}
    assert {row["sha"]: row["status"] for row in section["head_rows"]} == {"A": "unresolved", "B": "no_check_data"}
    assert "INCOMPLETE" in metrics.render_usage_report(report)


def test_workload_mix_separates_text_edits_heavy_tier_and_cancelled_work() -> None:
    api = _UsageApi()
    api.runs = [
        _usage_run(1, _at(1), display_title="CI (text edit)"),
        _usage_run(2, _at(2)),
        _usage_run(3, _at(3), conclusion="cancelled"),
        _usage_run(4, _at(4), path=".github/workflows/pr-hold.yml"),
    ]
    api.jobs = {
        1: [_usage_job(1, "core", 1, _at(1), _at(1, 1), _at(1, 2))],
        2: [
            _usage_job(2, "medium-collect", 1, _at(2), _at(2, 1), _at(2, 11)),
            _usage_job(3, "lint", 1, _at(2), _at(2, 1), _at(2, 4)),
        ],
        3: [_usage_job(4, "lint", 1, _at(3), _at(3, 1), _at(3, 6), "cancelled")],
        4: [_usage_job(5, "hold", 1, _at(4), _at(4, 1), _at(4, 2))],
    }
    workload = metrics.build_usage_report(_collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=6)))["workload"]
    assert set(workload["by_class"]) == {"ci_text_edit", "ci_heavy_tier", "ci_code", "not_ci_workflow"}
    assert workload["by_class"]["ci_heavy_tier"]["runner_minutes"] == 13.0
    assert workload["by_class"]["ci_code"]["cancelled_runner_minutes"] == 5.0
    assert workload["cancelled_runner_minutes"] == 5.0
    assert workload["by_runner_os_minutes"] == {"linux": 20.0}
    assert "heavy_tier_reasons" in workload["class_definitions"]


def test_disk_cache_lets_a_rerun_skip_immutable_requests(tmp_path: Path) -> None:
    api = _UsageApi()
    api.runs = [_usage_run(1, _at(1), run_attempt=2)]
    api.attempts[(1, 1)] = {"run_attempt": 1, "conclusion": "failure", "updated_at": _at(1, 10)}
    api.jobs[1] = [_usage_job(1, "core", 2, _at(1, 20), _at(1, 21), _at(1, 22))]
    cache = metrics.DiskCache(tmp_path)
    _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=3), cache=cache)
    before = len([path for path in api.requests if "/jobs" in path or "/attempts/" in path])
    second = _collect(api, USAGE_DAY, USAGE_DAY + timedelta(hours=3), cache=cache)
    after = len([path for path in api.requests if "/jobs" in path or "/attempts/" in path])
    assert before == after == 2
    assert second.stats["cache_hits"] >= 2


def test_client_waits_for_the_rate_limit_reset_before_the_reserve_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = metrics.GitHubClient("o/r", use_gh=False, token="t")
    client.rate_reserve = 10
    client.rate_remaining = 3
    client.rate_reset = 1000
    slept: list[float] = []
    monkeypatch.setattr(metrics.time, "time", lambda: 990.0)
    monkeypatch.setattr(metrics.time, "sleep", slept.append)
    client._wait_for_budget()
    assert slept == [11.0]
    assert client.waited_seconds == 11.0
    client._wait_for_budget()
    assert slept == [11.0]


def test_make_target_and_help_expose_the_usage_report() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\nci-usage-report:\n" in makefile
    assert "--usage-report --since" in makefile
    assert "ci-usage-report" in (ROOT / "make" / "help.mk").read_text(encoding="utf-8")


def test_urllib_api_retries_truncated_bodies_and_reports_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client
    import io

    class _Response(io.BytesIO):
        headers = {"x-ratelimit-remaining": "42", "x-ratelimit-reset": "99"}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    outcomes = [http.client.IncompleteRead(b"", 10), _Response(b'{"ok": true}')]
    seen: list[dict] = []

    def fake_urlopen(_request, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(metrics.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(metrics.time, "sleep", lambda _seconds: None)
    assert metrics._urllib_api("/x", "t", attempts=3, on_headers=lambda headers: seen.append(dict(headers))) == {
        "ok": True
    }
    assert seen == [{"x-ratelimit-remaining": "42", "x-ratelimit-reset": "99"}]

    outcomes[:] = [http.client.IncompleteRead(b"", 10)] * 2
    assert metrics._urllib_api("/x", "t", attempts=2) is None
