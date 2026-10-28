from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location(
        "scheduled_workflow_liveness", ROOT / "scripts/scheduled_workflow_liveness.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


liveness = _load()


def _run(run_id: int, age: timedelta, **extra) -> dict:
    created = (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": run_id,
        "created_at": created,
        "run_started_at": created,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "develop",
        **extra,
    }


@pytest.mark.parametrize(
    ("crons", "expected"),
    [
        (["0 6 * * *"], 3),
        (["0 6 * * 1"], 9),
        (["0 6 1 * *"], 35),
        (["0 6 1 1 *"], 100),
        (["0 6 * * 1", "0 6 * * *"], 3),
        (["not a cron"], 3),
        ([], 3),
    ],
)
def test_cadence_window_follows_the_coarsest_field(crons: list[str], expected: int) -> None:
    assert liveness.cadence_window_days(crons) == expected


def test_scheduled_workflows_lists_only_workflows_with_a_schedule_trigger(tmp_path: Path) -> None:
    (tmp_path / "daily.yml").write_text('name: d\non:\n  schedule:\n    - cron: "0 6 * * *"\n', encoding="utf-8")
    (tmp_path / "manual.yml").write_text("name: m\non:\n  workflow_dispatch:\n", encoding="utf-8")
    (tmp_path / "weekly.yaml").write_text("name: w\non:\n  schedule:\n    - cron: '0 6 * * 1'\n", encoding="utf-8")

    assert liveness.scheduled_workflows(tmp_path) == [("daily.yml", ["0 6 * * *"]), ("weekly.yaml", ["0 6 * * 1"])]


def test_newest_run_is_chosen_by_created_at_not_by_position() -> None:
    stale_first = [_run(1, timedelta(days=30)), _run(2, timedelta(hours=5)), _run(3, timedelta(days=2))]

    assert [run["id"] for run in liveness.newest_first(stale_first)] == [2, 3, 1]


def test_rows_without_a_usable_timestamp_are_ignored() -> None:
    rows = [{"id": 1}, {"id": 2, "created_at": "garbage"}, _run(3, timedelta(hours=1))]

    assert [run["id"] for run in liveness.newest_first(rows)] == [3]


def test_a_stale_first_row_does_not_make_a_live_workflow_dead() -> None:
    runs = [_run(1, timedelta(days=30)), _run(2, timedelta(hours=5))]

    verdict = liveness.assess("a.yml", runs, window_days=3, now=NOW, registered_at=None)

    assert verdict.alive
    assert "latest scheduled run" in verdict.message


def test_a_workflow_that_stopped_firing_is_dead_and_lists_its_recent_runs() -> None:
    runs = [_run(index, timedelta(days=10 + index)) for index in range(15)]

    verdict = liveness.assess("a.yml", runs, window_days=3, now=NOW, registered_at=None)

    assert not verdict.alive
    assert "older than its 3-day cadence window" in verdict.message
    assert [run["id"] for run in verdict.recent_runs] == list(range(10))


def test_a_run_exactly_at_the_window_edge_is_still_alive() -> None:
    verdict = liveness.assess("a.yml", [_run(1, timedelta(days=3))], window_days=3, now=NOW, registered_at=None)

    assert verdict.alive


def test_a_newly_registered_workflow_gets_its_grace_period() -> None:
    verdict = liveness.assess("a.yml", [], window_days=9, now=NOW, registered_at=NOW - timedelta(days=2))

    assert verdict.alive
    assert "activation grace" in verdict.message


@pytest.mark.parametrize("registered_at", [None, NOW - timedelta(days=30)])
def test_a_workflow_with_no_runs_outside_its_grace_is_dead(registered_at: datetime | None) -> None:
    verdict = liveness.assess("a.yml", [], window_days=9, now=NOW, registered_at=registered_at)

    assert not verdict.alive
    assert "no scheduled runs at all" in verdict.message


def test_the_bounded_query_reaches_one_day_past_the_cadence_window() -> None:
    requested_since: dict[str, datetime] = {}

    liveness.check_workflows(
        [("daily.yml", ["0 6 * * *"]), ("weekly.yml", ["0 6 * * 1"])],
        lambda name, since: requested_since.setdefault(name, since) and [_run(1, timedelta(hours=1))],
        lambda name: None,
        lambda name: [],
        NOW,
    )

    assert requested_since == {"daily.yml": NOW - timedelta(days=4), "weekly.yml": NOW - timedelta(days=10)}


def test_history_is_fetched_only_when_the_bounded_query_finds_nothing() -> None:
    history_requests: list[str] = []
    runs_by_name = {"live.yml": [_run(1, timedelta(hours=1))], "quiet.yml": []}

    verdicts = liveness.check_workflows(
        [("live.yml", ["0 6 * * *"]), ("quiet.yml", ["0 6 * * *"])],
        lambda name, since: runs_by_name[name],
        lambda name: None,
        lambda name: history_requests.append(name) or [_run(7, timedelta(days=40)), _run(8, timedelta(days=41))],
        NOW,
    )

    assert history_requests == ["quiet.yml"]
    assert [verdict.alive for verdict in verdicts] == [True, False]
    assert [run["id"] for run in verdicts[1].recent_runs] == [7, 8]


def test_a_flagged_workflow_lists_ten_runs_even_when_the_bounded_page_has_fewer() -> None:
    bounded = [_run(1, timedelta(days=3, hours=12))]
    history = [_run(index, timedelta(days=3, hours=12 + index)) for index in range(1, 14)]

    verdicts = liveness.check_workflows(
        [("daily.yml", ["0 6 * * *"])],
        lambda name, since: bounded,
        lambda name: None,
        lambda name: history,
        NOW,
    )

    assert not verdicts[0].alive
    assert [run["id"] for run in verdicts[0].recent_runs] == list(range(1, 11))


def test_a_workflow_with_old_history_gets_no_grace_even_if_its_file_was_edited_recently() -> None:
    verdicts = liveness.check_workflows(
        [("daily.yml", ["0 6 * * *"])],
        lambda name, since: [],
        lambda name: NOW - timedelta(days=1),
        lambda name: [_run(3, timedelta(days=8))],
        NOW,
    )

    assert not verdicts[0].alive
    assert "older than its 3-day cadence window" in verdicts[0].message
    assert [run["id"] for run in verdicts[0].recent_runs] == [3]


def test_a_workflow_with_no_history_at_all_still_gets_grace_when_newly_registered() -> None:
    verdicts = liveness.check_workflows(
        [("new.yml", ["0 6 * * 1"])],
        lambda name, since: [],
        lambda name: NOW - timedelta(days=1),
        lambda name: [],
        NOW,
    )

    assert verdicts[0].alive
    assert "activation grace" in verdicts[0].message


def test_report_prints_each_flagged_workflow_with_its_newest_runs() -> None:
    dead = liveness.Verdict("dead.yml", False, "dead.yml: latest scheduled run is old", (_run(5, timedelta(days=20)),))
    lines: list[str] = []

    code = liveness.report([liveness.Verdict("ok.yml", True, "ok.yml: fine"), dead], lines.append)

    assert code == 1
    assert any("dead.yml: latest scheduled run is old" in line for line in lines)
    detail = next(line for line in lines if "id=5" in line)
    for field in ("created_at=", "run_started_at=", "status=completed", "conclusion=success", "head_branch=develop"):
        assert field in detail


def test_report_passes_when_everything_is_alive() -> None:
    lines: list[str] = []

    assert liveness.report([liveness.Verdict("ok.yml", True, "ok.yml: fine")], lines.append) == 0
    assert lines == [
        "All 1 scheduled workflow(s) alive within their cadence windows (run conclusions are not assessed)."
    ]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_recent_scheduled_run_that_did_not_succeed_is_still_alive(conclusion: str) -> None:
    verdict = liveness.assess(
        "nightly-v2.yml",
        [_run(1, timedelta(hours=2), conclusion=conclusion)],
        window_days=3,
        now=NOW,
        registered_at=None,
    )

    assert verdict.alive
    lines: list[str] = []
    assert liveness.report([verdict], lines.append) == 0
    assert not any("::error::" in line for line in lines)


def test_a_stale_successful_run_is_dead_regardless_of_its_conclusion() -> None:
    verdict = liveness.assess(
        "nightly-v2.yml",
        [_run(1, timedelta(days=5), conclusion="success")],
        window_days=3,
        now=NOW,
        registered_at=None,
    )

    assert not verdict.alive


def test_report_fails_when_no_scheduled_workflow_exists() -> None:
    lines: list[str] = []

    assert liveness.report([], lines.append) == 1
    assert "liveness scan itself is broken" in lines[0]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.invalid", code, "error", Message(), io.BytesIO(b"{}"))


def _api(monkeypatch: pytest.MonkeyPatch, responses: list) -> tuple[object, list[str], list[float]]:
    requested: list[str] = []
    slept: list[float] = []

    def urlopen(request, timeout):
        requested.append(request.full_url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(json.dumps(item).encode())

    monkeypatch.setattr(liveness.urllib.request, "urlopen", urlopen)
    return liveness.GitHubApi("owner/repo", "token", "https://api.invalid", slept.append), requested, slept


def test_the_runs_query_is_bounded_and_filtered_to_scheduled_events(monkeypatch: pytest.MonkeyPatch) -> None:
    api, requested, _ = _api(monkeypatch, [{"workflow_runs": [{"id": 1}]}])

    runs = api.scheduled_runs("nightly-v2.yml", NOW - timedelta(days=4))

    assert runs == [{"id": 1}]
    assert "event=schedule" in requested[0]
    assert "per_page=30" in requested[0]
    assert "created=%3E%3D2026-10-05" in requested[0]


def test_a_missing_workflow_has_no_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, _ = _api(monkeypatch, [_http_error(404)])

    assert api.scheduled_runs("gone.yml", NOW) == []


@pytest.mark.parametrize("code", [401, 403])
def test_auth_and_rate_limit_failures_stop_the_scan(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    api, _, _ = _api(monkeypatch, [_http_error(code)])

    with pytest.raises(liveness.LivenessError, match="cannot assess liveness"):
        api.scheduled_runs("a.yml", NOW)


def test_transient_failures_are_retried_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    api, requested, slept = _api(monkeypatch, [_http_error(502), _http_error(502), {"workflow_runs": []}])

    assert api.scheduled_runs("a.yml", NOW) == []
    assert len(requested) == 3
    assert slept == [5, 10]


def test_persistent_failures_end_the_scan_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    api, requested, _ = _api(monkeypatch, [_http_error(500)] * 3)

    with pytest.raises(liveness.LivenessError, match="unreachable after retries"):
        api.scheduled_runs("a.yml", NOW)
    assert len(requested) == 3


def test_registration_uses_the_latest_of_workflow_creation_and_last_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, _ = _api(
        monkeypatch,
        [
            {"created_at": "2026-10-01T00:00:00Z"},
            [{"commit": {"committer": {"date": "2026-10-08T00:00:00Z"}}}],
        ],
    )

    assert api.registered_at("a.yml") == datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_registration_is_unknown_when_the_api_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, _ = _api(monkeypatch, [_http_error(403), _http_error(403)])

    assert api.registered_at("a.yml") is None


def test_registration_preserves_creation_when_commits_request_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, _ = _api(
        monkeypatch,
        [
            {"created_at": "2026-10-09T00:00:00Z"},
            _http_error(403),
        ],
    )

    assert api.registered_at("a.yml") == datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_registration_preserves_commit_date_when_workflow_request_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    api, _, _ = _api(
        monkeypatch,
        [
            _http_error(403),
            [{"commit": {"committer": {"date": "2026-10-09T00:00:00Z"}}}],
        ],
    )

    assert api.registered_at("a.yml") == datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_the_nightly_job_runs_the_script_instead_of_an_inline_program() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/nightly-v2.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["liveness"]
    run_steps = [step["run"] for step in job["steps"] if "run" in step]

    assert run_steps == ["python3 scripts/scheduled_workflow_liveness.py | tee liveness.log"]
    assert job["permissions"] == {"actions": "read", "contents": "read"}
