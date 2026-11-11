from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import release_admitted_dist, trunk_uat
from tests.uat import rubric

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def sha(letter: str) -> str:
    return letter * 40


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class Github:
    def __init__(self) -> None:
        self.uat_runs: list[dict] = []
        self.artifacts: dict[int, list[dict]] = {}
        self.release_runs: list[dict] = []
        self.jobs: dict[int, list[dict]] = {}
        self.calls: list[str] = []
        self.attempt_starts: dict[int, str] = {}
        self.records: dict[int, bytes] = {}
        self._next = 1000

    def uat_run(
        self,
        *,
        executed_sha: str | None = None,
        branch: str = "develop",
        status: str = "completed",
        expired=False,
        verdict: str | None = "green",
        failed_jobs: tuple[str, ...] = (),
    ) -> int:
        self._next += 1
        run_id = self._next
        self.uat_runs.append(
            {
                "id": run_id,
                "status": status,
                "head_branch": branch,
                "created_at": f"2026-11-{run_id - 1000:02d}T02:00:00Z",
            }
        )
        names = (
            [
                f"release-uat-trunk-{executed_sha}-stage1-sqlite",
                f"trunk-uat-gate-{executed_sha}",
                f"trunk-uat-record-{executed_sha}",
            ]
            if executed_sha
            else []
        )
        self.artifacts[run_id] = [
            {"id": run_id * 10 + n, "name": name, "expired": expired} for n, name in enumerate(names)
        ]
        if executed_sha:
            results = dict.fromkeys(("admit", "uat-stage1", "uat-stage2", "uat-stage3", "gate"), "success")
            results.update(dict.fromkeys(failed_jobs, "failure"))
            record = {"verdict": verdict, "job_results": results, "source_sha": executed_sha}
            self.records[run_id * 10 + 2] = zip_bytes({trunk_uat.RECORD_FILE: json.dumps(record).encode()})
        return run_id

    def fetch(self, artifact_id: int) -> bytes:
        return self.records[artifact_id]

    def release_run(self, status: str, **fields) -> int:
        self._next += 1
        self.release_runs.append({"id": self._next, "status": status, **fields})
        return self._next

    def __call__(self, path: str) -> dict:
        self.calls.append(path)
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == "actions/workflows/trunk-uat.yml/runs":
            branch = query.get("branch", [None])[0]
            return {"workflow_runs": [run for run in self.uat_runs if branch in (None, run["head_branch"])]}
        if parsed.path == "actions/workflows/release-v2.yml/runs":
            return {"workflow_runs": list(self.release_runs)}
        if parsed.path.startswith("actions/runs/") and parsed.path.endswith("/artifacts"):
            return {"artifacts": self.artifacts[int(parsed.path.split("/")[2])]}
        if parsed.path.startswith("actions/runs/") and "/attempts/" in parsed.path and "/jobs" not in parsed.path:
            return {"run_started_at": self.attempt_starts[int(parsed.path.rsplit("/", 1)[1])]}
        if parsed.path.startswith("actions/runs/") and "/jobs" in parsed.path:
            return {"jobs": self.jobs.get(int(parsed.path.split("/")[2]), [])}
        raise AssertionError(f"unexpected API path {path}")


class History:
    def __init__(self, commits: dict[str, set[str]]) -> None:
        self.order = list(commits)
        self.files = commits

    def changed(self, base: str, head: str) -> list[str] | None:
        if base not in self.files or head not in self.files:
            return None
        start, end = self.order.index(base), self.order.index(head)
        files: set[str] = set()
        for commit in self.order[start + 1 : end + 1]:
            files |= self.files[commit]
        return sorted(files)


@pytest.fixture
def releasable(monkeypatch):
    state = {"sha": sha("a")}
    monkeypatch.setattr(trunk_uat._releasable, "newest_releasable", lambda api: state["sha"])
    return state


def decide(github: Github, history: History, *, event: str = "schedule", run_id: int = 9999, branch="develop"):
    return trunk_uat.decide(
        event=event, branch=branch, run_id=run_id, api=github, changed=history.changed, fetch=github.fetch
    )


def test_scheduled_run_skips_when_nothing_in_the_skip_path_set_changed(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    history = History({sha("a"): {"benchbox/x.py"}, sha("b"): {"docs/guide.md", "tests/unit/test_x.py"}})
    releasable["sha"] = sha("b")
    decision = decide(github, history)
    assert decision.action == "skip"
    assert decision.reason == f"skipped: unchanged since {sha('a')}"
    assert decision.baseline_sha == sha("a")


@pytest.mark.parametrize("guarded", trunk_uat.SKIP_PATHS)
def test_a_change_to_any_skip_path_runs_the_matrix(releasable, guarded):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    touched = guarded if "." in guarded.rsplit("/", 1)[-1] else f"{guarded}/inner/file.txt"
    history = History({sha("a"): set(), sha("b"): {touched}})
    releasable["sha"] = sha("b")
    decision = decide(github, history)
    assert decision.action == "run"
    assert touched in decision.reason and f"since {sha('a')}" in decision.reason


def test_a_path_that_only_shares_a_prefix_with_a_skip_path_does_not_run(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    history = History({sha("a"): set(), sha("b"): {"benchbox-notes/readme.md", "uv.lock.bak", "tests/uat_other/x.py"}})
    releasable["sha"] = sha("b")
    assert decide(github, history).action == "skip"


@pytest.mark.parametrize("event", ["workflow_dispatch", "push"])
def test_only_scheduled_runs_may_skip(releasable, event):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    history = History({sha("a"): set(), sha("b"): {"README.md"}})
    releasable["sha"] = sha("b")
    decision = decide(github, history, event=event)
    assert decision.action == "run"
    assert decision.source_sha == sha("b")


def test_a_run_without_any_earlier_executed_run_executes(releasable):
    github = Github()
    github.uat_run()
    decision = decide(github, History({sha("a"): set()}))
    assert decision.action == "run"
    assert "no earlier run" in decision.reason


def test_an_unknown_baseline_commit_executes_instead_of_skipping(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("c"))
    decision = decide(github, History({sha("a"): set()}))
    assert decision.action == "run"
    assert "not in the checkout" in decision.reason


def test_a_deferred_run_and_a_skipped_run_never_become_the_skip_baseline(releasable):
    history = History(
        {
            sha("a"): set(),
            sha("b"): {"benchbox/loader.py"},
            sha("c"): {"docs/note.md"},
            sha("d"): {"docs/other.md"},
        }
    )
    github = Github()
    github.uat_run(executed_sha=sha("a"))

    github.release_run("in_progress")
    releasable["sha"] = sha("b")
    deferred = decide(github, history)
    assert deferred.action == "defer"
    github.uat_run()
    github.release_runs.clear()

    releasable["sha"] = sha("c")
    executes = decide(github, history)
    assert executes.action == "run"
    assert executes.baseline_sha == sha("a")
    assert sha("b") not in executes.reason and "benchbox/loader.py" in executes.reason


def test_a_skipped_run_keeps_the_last_executed_run_as_the_baseline(releasable):
    history = History({sha("a"): set(), sha("b"): {"docs/one.md"}, sha("c"): {"docs/two.md"}})
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    releasable["sha"] = sha("b")
    assert decide(github, history).reason == f"skipped: unchanged since {sha('a')}"
    github.uat_run()
    releasable["sha"] = sha("c")
    assert decide(github, history).reason == f"skipped: unchanged since {sha('a')}"


def test_runs_that_recorded_no_executed_cells_or_whose_evidence_expired_are_not_baselines(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    github.uat_run(executed_sha=sha("b"), expired=True)
    github.uat_run()
    history = History({sha("a"): set(), sha("b"): {"benchbox/x.py"}, sha("c"): set()})
    releasable["sha"] = sha("c")
    decision = decide(github, history)
    assert decision.baseline_sha == sha("a")
    assert decision.action == "run"


def test_the_current_run_and_unfinished_runs_are_not_baselines(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    current = github.uat_run(executed_sha=sha("b"))
    github.uat_run(executed_sha=sha("c"), status="in_progress")
    history = History({sha("a"): set(), sha("b"): set(), sha("c"): set(), sha("d"): set()})
    releasable["sha"] = sha("d")
    assert decide(github, history, run_id=current).baseline_sha == sha("a")


@pytest.mark.parametrize(
    ("verdict", "failed_jobs", "named"),
    [
        ("red", (), "verdict red"),
        (None, (), "verdict None"),
        ("green", ("uat-stage2",), "uat-stage2"),
        ("green", ("gate", "admit"), "admit, gate"),
    ],
)
def test_a_scheduled_run_does_not_skip_behind_a_baseline_that_was_not_green(releasable, verdict, failed_jobs, named):
    github = Github()
    github.uat_run(executed_sha=sha("a"), verdict=verdict, failed_jobs=failed_jobs)
    history = History({sha("a"): set(), sha("b"): {"docs/guide.md"}})
    releasable["sha"] = sha("b")
    decision = decide(github, history)
    assert decision.action == "run"
    assert decision.reason.startswith(f"run: baseline run 1001 at {sha('a')} was not green")
    assert named in decision.reason
    assert decision.baseline_sha == sha("a")


def test_a_green_baseline_after_a_red_one_allows_the_next_skip(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"), verdict="red")
    github.uat_run(executed_sha=sha("b"))
    history = History({sha("a"): set(), sha("b"): set(), sha("c"): {"docs/note.md"}})
    releasable["sha"] = sha("c")
    assert decide(github, history).reason == f"skipped: unchanged since {sha('b')}"


def test_the_baseline_is_read_from_the_runs_own_branch(releasable):
    github = Github()
    github.uat_run(executed_sha=sha("a"), branch="feat/calibration")
    decision = decide(github, History({sha("a"): set()}), branch="develop")
    assert decision.action == "run" and "no earlier run on develop" in decision.reason
    assert any("branch=develop" in call for call in github.calls)


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting", "pending", "requested"])
@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_nothing_starts_while_a_release_run_is_in_progress(releasable, status, event):
    github = Github()
    github.release_run("completed")
    active = github.release_run(status)
    decision = decide(github, History({sha("a"): set()}), event=event)
    assert decision.action == "defer"
    assert decision.reason == f"deferred: release-v2 run {active} in progress"


def test_an_in_progress_probe_dispatch_does_not_defer(releasable):
    github = Github()
    probe = github.release_run("in_progress", event="workflow_dispatch", head_branch="fix/x", run_attempt=1)
    github.jobs[probe] = [
        {"name": "admit", "status": "completed", "conclusion": "success"},
        {"name": "probe UAT stub evidence", "status": "in_progress", "conclusion": None},
    ]
    assert decide(github, History({sha("a"): set()}), event="workflow_dispatch").action == "run"


def test_a_tag_push_release_in_progress_defers_even_with_probe_runs_beside_it(releasable):
    github = Github()
    probe = github.release_run("in_progress", event="workflow_dispatch", run_attempt=1)
    github.jobs[probe] = [{"name": "publish dry run", "status": "in_progress", "conclusion": None}]
    release = github.release_run("in_progress", event="push", head_branch="v0.5.0", run_attempt=1)
    decision = decide(github, History({sha("a"): set()}), event="schedule")
    assert decision.reason == f"deferred: release-v2 run {release} in progress"


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [{"name": "admit", "status": "in_progress", "conclusion": None}],
        [{"name": "probe UAT stub evidence", "status": "completed", "conclusion": "skipped"}],
        [{"name": "probe UAT stub evidence", "status": "queued", "conclusion": None}],
        [{"name": "publish dry run", "status": "waiting", "conclusion": None}],
    ],
)
def test_a_dispatch_run_not_proven_to_be_a_probe_defers(releasable, jobs):
    github = Github()
    run = github.release_run("in_progress", event="workflow_dispatch", run_attempt=1)
    github.jobs[run] = jobs
    decision = decide(github, History({sha("a"): set()}), event="schedule")
    assert decision.reason == f"deferred: release-v2 run {run} in progress"


def test_a_run_with_no_event_defers(releasable):
    github = Github()
    run = github.release_run("in_progress")
    assert decide(github, History({sha("a"): set()}), event="schedule").reason == (
        f"deferred: release-v2 run {run} in progress"
    )


def test_completed_release_runs_do_not_defer(releasable):
    github = Github()
    github.release_run("completed")
    assert decide(github, History({sha("a"): set()}), event="workflow_dispatch").action == "run"


def test_no_releasable_commit_fails_with_a_named_reason(monkeypatch):
    monkeypatch.setattr(trunk_uat._releasable, "newest_releasable", lambda api: None)
    with pytest.raises(ValueError, match="no releasable commit among the newest"):
        decide(Github(), History({}), event="workflow_dispatch")


def test_cancel_stops_only_unfinished_trunk_uat_runs():
    github = Github()
    github.uat_run(status="completed")
    running = github.uat_run(status="in_progress")
    queued = github.uat_run(status="queued")
    posted: list[str] = []
    cancelled = trunk_uat.cancel_in_progress(github, posted.append)
    assert sorted(cancelled) == sorted([running, queued])
    assert sorted(posted) == sorted(f"actions/runs/{run}/cancel" for run in (running, queued))
    assert trunk_uat.cancel_in_progress(Github(), posted.append) == []


def test_cancel_command_records_the_cancelled_runs_in_the_summary(monkeypatch, tmp_path, capsys):
    github = Github()
    running = github.uat_run(status="in_progress")
    posted: list[str] = []
    monkeypatch.setattr(trunk_uat._consumer, "github_json", github)
    monkeypatch.setattr(trunk_uat, "_post", posted.append)
    summary = tmp_path / "summary.md"
    assert trunk_uat.main(["cancel", "--reason", "release-v2 run 7 full UAT fallback", "--summary", str(summary)]) == 0
    assert posted == [f"actions/runs/{running}/cancel"]
    assert f"cancelled trunk-uat run {running} (release-v2 run 7 full UAT fallback)" in summary.read_text(
        encoding="utf-8"
    )
    assert "cancelled trunk-uat run" in capsys.readouterr().out


def test_cancel_command_reports_when_no_trunk_uat_run_is_in_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(trunk_uat._consumer, "github_json", Github())
    summary = tmp_path / "summary.md"
    assert trunk_uat.main(["cancel", "--reason", "x", "--summary", str(summary)]) == 0
    assert "no trunk-uat run was in progress" in summary.read_text(encoding="utf-8")


def test_plan_command_writes_the_decision_for_downstream_jobs(monkeypatch, releasable, tmp_path):
    github = Github()
    github.uat_run(executed_sha=sha("a"))
    releasable["sha"] = sha("b")
    monkeypatch.setattr(trunk_uat._consumer, "github_json", github)
    monkeypatch.setattr(trunk_uat, "_fetch_artifact", github.fetch)
    monkeypatch.setattr(trunk_uat, "git_changed_paths", lambda root, base, head: ["docs/x.md"])
    output, summary = tmp_path / "output", tmp_path / "summary.md"
    code = trunk_uat.main(
        [
            "plan",
            "--event",
            "schedule",
            "--branch",
            "develop",
            "--run-id",
            "5",
            "--github-output",
            str(output),
            "--summary",
            str(summary),
        ]
    )
    assert code == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert "action=skip" in lines
    assert f"reason=skipped: unchanged since {sha('a')}" in lines
    assert f"source_sha={sha('b')}" in lines
    assert f"skipped: unchanged since {sha('a')}" in summary.read_text(encoding="utf-8")


def test_git_changed_paths_lists_files_and_reports_unknown_commits(tmp_path):
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "one.txt").write_text("1")
    git("add", "one.txt")
    git("commit", "-q", "-m", "one")
    base = git("rev-parse", "HEAD")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "two.txt").write_text("2")
    git("add", "dir/two.txt")
    git("commit", "-q", "-m", "two")
    head = git("rev-parse", "HEAD")
    assert trunk_uat.git_changed_paths(tmp_path, base, head) == ["dir/two.txt"]
    assert trunk_uat.git_changed_paths(tmp_path, sha("f"), head) is None


def write_shard(root: Path, stage: int, shard: str, rows: list[dict], logs: dict[str, str] | None = None) -> Path:
    run_dir = root / f"release-uat-trunk-{sha('a')}-stage{stage}-{shard}" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "cells.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    for name, text in (logs or {}).items():
        (run_dir / name).write_text(text, encoding="utf-8")
    return run_dir


def passed_row(platform: str, log: str, **extra) -> dict:
    return {
        "platform": platform,
        "benchmark": "tpch",
        "scale": 0.01,
        "status": "passed",
        "terminal_state": "passed",
        "elapsed_s": 12.5,
        "log_path": f"/home/runner/Developer/benchmark_runs/logs/uat_1/{log}",
        **extra,
    }


def job(name: str, start: str, end: str, conclusion: str = "success") -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": f"2026-11-06T{start}:00Z",
        "completed_at": f"2026-11-06T{end}:00Z",
    }


def build(github: Github, tmp_path: Path, **overrides):
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    gate = tmp_path / "gate"
    gate.mkdir(exist_ok=True)
    arguments = {
        "api": github,
        "evidence": evidence,
        "gate": gate,
        "sha": sha("a"),
        "run_id": 42,
        "attempt": 1,
        "event": "schedule",
        "job_results": {"admit": "success", "uat-stage1": "success", "gate": "success"},
    }
    arguments.update(overrides)
    return trunk_uat.build_record(**arguments)


def test_record_lists_every_executed_cell_with_state_elapsed_memory_signature_and_attempt(tmp_path):
    github = Github()
    github.jobs[42] = [
        job("plan", "02:00", "02:05"),
        job("UAT stage 1 sqlite", "02:10", "05:10"),
        job("UAT stage 1 duckdb", "02:10", "04:10", "failure"),
    ]
    evidence = tmp_path / "evidence"
    write_shard(
        evidence,
        1,
        "sqlite",
        [
            passed_row("sqlite", "sqlite.log"),
            {
                "platform": "sqlite",
                "benchmark": "tpcds",
                "scale": 1.0,
                "status": "timed-out",
                "terminal_state": "timed-out",
                "elapsed_s": 3600.0,
                "log_path": "/x/sqlite-tpcds.log",
                "failure_kind": "infrastructure",
                "failure_signature": "timeout",
                "retry_signature": "timeout",
                "retry_outcome": "retry_failed",
                "first_attempt_status": "timed-out",
                "first_attempt_elapsed_s": 3000.0,
                "attempt": 2,
            },
        ],
        {"sqlite.log": "ok\n# UAT_PEAK_RSS_GIB=1.75\n", "sqlite-tpcds.log": "# UAT_PEAK_RSS_GIB=9.50\n"},
    )
    (tmp_path / "gate").mkdir()
    (tmp_path / "gate" / "uat-gate-summary.json").write_text('{"verdict": "red"}')
    (tmp_path / "gate" / "dist-digest.txt").write_bytes(b"deadbeef  benchbox.whl\n")
    record, rows = build(github, tmp_path)
    assert record["outcome"] == "executed"
    assert record["verdict"] == "red"
    assert record["dist_digest"] == hashlib.sha256(b"deadbeef  benchbox.whl\n").hexdigest()
    assert record["cells"] == {"total": 2, "passed": 1, "failed": 1}
    first, second = rows
    assert (first["platform"], first["terminal_state"], first["elapsed_s"], first["peak_rss_gib"]) == (
        "sqlite",
        "passed",
        12.5,
        1.75,
    )
    assert first["failure_signature"] is None and first["attempt"] == 1
    assert second["terminal_state"] == "timed-out" and second["attempt"] == 2 and second["peak_rss_gib"] == 9.5
    assert (second["failure_kind"], second["failure_signature"]) == ("infrastructure", "timeout")
    assert (second["retry_outcome"], second["first_attempt_status"]) == ("retry_failed", "timed-out")
    for row in rows:
        assert row["source_sha"] == sha("a") and row["run_id"] == 42 and row["stage"] == 1 and row["shard"] == "sqlite"
        assert row["dist_digest"] == record["dist_digest"]


def test_record_attributes_each_shards_cells_to_the_attempt_that_produced_them(tmp_path):
    github = Github()
    github.attempt_starts = {1: "2026-11-06T02:00:00Z", 2: "2026-11-06T06:00:00Z"}
    github.jobs[42] = [
        job("UAT stage 1 sqlite", "02:10", "05:10"),
        job("UAT stage 1 pandas-df", "06:05", "06:30"),
    ]
    evidence = tmp_path / "evidence"
    write_shard(evidence, 1, "sqlite", [passed_row("sqlite", "sqlite.log")])
    write_shard(evidence, 1, "pandas-df", [passed_row("pandas-df", "pandas.log")])
    record, rows = build(github, tmp_path, attempt=2)
    assert record["partial_rerun"] is True
    assert record["carried_over_shards"] == ["stage 1 sqlite"]
    assert {row["shard"]: row["run_attempt"] for row in rows} == {"sqlite": 1, "pandas-df": 2}
    assert "Partial re-run: attempt 2 kept the earlier results of stage 1 sqlite" in trunk_uat.render_summary(record)


def test_record_of_a_full_rerun_or_a_first_attempt_is_not_a_partial_rerun(tmp_path):
    github = Github()
    github.attempt_starts = {1: "2026-11-06T02:00:00Z", 2: "2026-11-06T06:00:00Z"}
    github.jobs[42] = [job("UAT stage 1 sqlite", "06:05", "06:30")]
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "sqlite.log")])
    record, rows = build(github, tmp_path, attempt=2)
    assert record["partial_rerun"] is False and rows[0]["run_attempt"] == 2
    record, rows = build(github, tmp_path, attempt=1)
    assert record["partial_rerun"] is False and rows[0]["run_attempt"] == 1


def test_record_reports_wall_time_runner_hours_and_per_shard_state(tmp_path):
    github = Github()
    github.jobs[42] = [
        job("plan", "02:00", "02:30"),
        job("UAT stage 1 sqlite", "03:00", "06:00"),
        job("UAT stage 2 starrocks", "03:00", "04:30", "failure"),
        {"name": "UAT stage 3 olap", "conclusion": None, "started_at": "2026-11-06T03:00:00Z", "completed_at": None},
    ]
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "a.log")])
    record, _ = build(github, tmp_path)
    assert record["runner_hours"] == 5.0
    assert record["shard_runner_hours"] == 4.5
    assert record["wall_hours"] == 4.0
    assert record["longest_shard_hours"] == 3.0
    assert record["shards"] == [
        {"stage": 1, "shard": "sqlite", "state": "success", "hours": 3.0, "step_timeout": False},
        {"stage": 2, "shard": "starrocks", "state": "failure", "hours": 1.5, "step_timeout": False},
    ]
    assert record["over_gate"] is False


def test_record_flags_the_runner_hour_decision_gate(tmp_path):
    github = Github()
    github.jobs[42] = [job(f"UAT stage 1 shard{n}", "00:00", "05:00") for n in range(7)]
    write_shard(tmp_path / "evidence", 1, "shard0", [passed_row("sqlite", "a.log")])
    record, _ = build(github, tmp_path)
    assert record["runner_hours"] == 35.0 and record["over_gate"] is True
    assert "DECISION GATE" in trunk_uat.render_summary(record)


def test_record_flags_a_shard_beyond_the_hosted_job_limit(tmp_path):
    github = Github()
    github.jobs[42] = [job("UAT stage 1 sqlite", "00:00", "06:30")]
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "a.log")])
    record, _ = build(github, tmp_path)
    assert record["over_gate"] is True


def test_a_run_where_zero_cells_executed_fails_with_a_named_reason(monkeypatch, tmp_path):
    github = Github()
    github.jobs[42] = [job("plan", "02:00", "02:05")]
    write_shard(tmp_path / "evidence", 1, "sqlite", [])
    monkeypatch.setattr(trunk_uat._consumer, "github_json", github)
    output, summary = tmp_path / "output", tmp_path / "summary.md"
    code = trunk_uat.main(
        [
            "record",
            "--evidence",
            str(tmp_path / "evidence"),
            "--gate",
            str(tmp_path / "gate"),
            "--sha",
            sha("a"),
            "--run-id",
            "42",
            "--attempt",
            "1",
            "--event",
            "schedule",
            "--job-result",
            "admit=success",
            "--job-result",
            "uat-stage1=failure",
            "--output",
            str(tmp_path / "record"),
            "--github-output",
            str(output),
            "--summary",
            str(summary),
        ]
    )
    assert code == 1
    text = summary.read_text(encoding="utf-8")
    assert "zero cells executed: 1 shard evidence directories held no cells.jsonl rows" in text
    assert "uat-stage1=failure" in text
    assert "executed=false" in output.read_text(encoding="utf-8").splitlines()
    assert not (tmp_path / "record").exists()


def test_a_run_with_no_shard_evidence_at_all_is_a_zero_cell_run(tmp_path):
    github = Github()
    record, rows = build(github, tmp_path, job_results={"admit": "failure", "uat-stage1": "skipped"})
    assert record["outcome"] == "zero-cells"
    assert "admit=failure" in record["reason"] and rows == []


def test_a_run_cancelled_by_a_release_is_recorded_and_never_executed(tmp_path):
    github = Github()
    release = github.release_run("in_progress")
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "a.log")])
    record, rows = build(
        github, tmp_path, job_results={"admit": "success", "uat-stage1": "cancelled", "gate": "skipped"}
    )
    assert record["outcome"] == "cancelled"
    assert record["reason"] == f"cancelled: release-v2 run {release} in progress (full UAT fallback)"
    assert rows == []


def test_executed_record_is_written_for_the_history_artifact(monkeypatch, tmp_path):
    github = Github()
    github.jobs[42] = [job("UAT stage 1 sqlite", "02:00", "03:00")]
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "a.log")])
    monkeypatch.setattr(trunk_uat._consumer, "github_json", github)
    output = tmp_path / "output"
    code = trunk_uat.main(
        [
            "record",
            "--evidence",
            str(tmp_path / "evidence"),
            "--gate",
            str(tmp_path / "gate"),
            "--sha",
            sha("a"),
            "--run-id",
            "42",
            "--attempt",
            "1",
            "--event",
            "workflow_dispatch",
            "--job-result",
            "admit=success",
            "--output",
            str(tmp_path / "record"),
            "--github-output",
            str(output),
        ]
    )
    assert code == 0
    assert "executed=true" in output.read_text(encoding="utf-8").splitlines()
    stored = json.loads((tmp_path / "record" / trunk_uat.RECORD_FILE).read_text(encoding="utf-8"))
    assert stored["outcome"] == "executed" and stored["source_sha"] == sha("a")
    history_text = (tmp_path / "record" / trunk_uat.HISTORY_FILE).read_text(encoding="utf-8")
    history = [json.loads(line) for line in history_text.splitlines()]
    assert [row["platform"] for row in history] == ["sqlite"]


def artifact_payloads(github: Github, runs: tuple[int, ...], build_payload) -> dict[int, bytes]:
    return {artifact["id"]: build_payload(run, artifact["name"]) for run in runs for artifact in github.artifacts[run]}


def test_history_rows_read_executed_runs_newest_first_and_skip_expired_ones():
    github = Github()
    old = github.uat_run(executed_sha=sha("a"))
    skipped = github.uat_run()
    expired = github.uat_run(executed_sha=sha("b"), expired=True)
    new = github.uat_run(executed_sha=sha("c"))
    payloads = artifact_payloads(
        github,
        (old, new),
        lambda run, name: zip_bytes({trunk_uat.HISTORY_FILE: (json.dumps({"run": run}) + "\n").encode()}),
    )
    fetched: list[int] = []

    def fetch(artifact_id: int) -> bytes:
        fetched.append(artifact_id)
        return payloads[artifact_id]

    rows = list(trunk_uat.history_rows(github, fetch))
    assert [row["run"] for row in rows] == [new, old]
    assert skipped not in [row["run"] for row in rows] and expired not in [row["run"] for row in rows]
    assert len(fetched) == 2


def test_fetched_shard_run_directories_load_in_the_uat_rubric_history_reader(tmp_path):
    github = Github()
    older = github.uat_run(executed_sha=sha("a"))
    github.uat_run()
    newer = github.uat_run(executed_sha=sha("b"))
    rows = [passed_row("sqlite", "sqlite.log"), {**passed_row("duckdb", "duckdb.log"), "status": "failed"}]
    payloads = artifact_payloads(
        github,
        (older, newer),
        lambda run, name: zip_bytes(
            {
                "dist-digest.txt": b"digest\n",
                "run/cells.jsonl": "".join(json.dumps(row) + "\n" for row in rows).encode(),
                "run/uat_gate_summary.json": json.dumps({"completed_at": f"2026-11-0{run % 10}T05:00:00"}).encode(),
                "run/sqlite.log": b"log",
            }
        ),
    )
    directories = trunk_uat.fetch_run_directories(tmp_path / "history", github, payloads.__getitem__)
    names = sorted(path.relative_to(tmp_path / "history").as_posix() for path in directories)
    assert names == sorted([f"{newer}-{sha('b')[:12]}/stage1-sqlite", f"{older}-{sha('a')[:12]}/stage1-sqlite"])
    assert (directories[0] / "sqlite.log").read_bytes() == b"log"
    assert not (directories[0] / "dist-digest.txt").exists()
    runs = rubric.load_history(tmp_path / "history")
    assert len(runs) == 2
    assert {obs.cell[0] for run in runs for obs in run.observations} == {"sqlite", "duckdb"}


def test_run_directory_extraction_refuses_members_that_escape_the_destination(tmp_path):
    blob = zip_bytes({"run/cells.jsonl": b"{}\n", "run/../escape.txt": b"x"})
    with pytest.raises(ValueError, match="unsafe evidence member"):
        trunk_uat.extract_run_directory(blob, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_admission_receipt_is_accepted_by_the_release_admitted_dist_verifier(tmp_path):
    version = "0.5.1"
    files = {
        f"benchbox-{version}-py3-none-any.whl": b"wheel bytes",
        f"benchbox-{version}.tar.gz": b"sdist bytes",
    }
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))
    producer = {
        "schema": 1,
        "repository": release_admitted_dist._consumer.REPOSITORY,
        "repository_id": 7,
        "head_repository_id": 7,
        "workflow_path": release_admitted_dist._consumer.WORKFLOW,
        "head_sha": sha("a"),
        "run_id": 11,
        "run_attempt": 2,
        "job_id": 12,
        "job_name": release_admitted_dist._consumer.JOB_NAME,
        "artifact_name": f"dist-{sha('a')}-attempt-2",
        "sha256sums_sha256": hashlib.sha256(sums.encode()).hexdigest(),
        "files": {
            name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()
        },
    }
    artifact = {"id": 5, "digest": "sha256:" + "0" * 64, "expires_at": "2026-12-01T00:00:00Z"}
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    for name, data in files.items():
        (admitted / name).write_bytes(data)
    (admitted / "SHA256SUMS").write_bytes(sums.encode())
    (admitted / "producer-receipt.json").write_text(json.dumps(producer))
    receipt = trunk_uat.admission_receipt(sha("a"), version, producer, artifact)
    (admitted / trunk_uat.ADMISSION_RECEIPT).write_text(json.dumps(receipt))
    verified, payloads = release_admitted_dist.verify_admitted(admitted, receipt["tag"], sha("a"))
    assert verified["version"] == version and set(payloads) == set(files)
    with pytest.raises(ValueError, match="another commit"):
        release_admitted_dist.verify_admitted(admitted, receipt["tag"], sha("b"))


def test_shard_evidence_names_are_checked_by_the_release_evidence_detector(tmp_path):
    from scripts import release_uat_evidence_check

    for stage in (1, 2, 3):
        run_dir = tmp_path / f"release-uat-trunk-{sha('a')}-stage{stage}-shard" / "run"
        run_dir.mkdir(parents=True)
        attempted = 0 if stage == 3 else 4
        (run_dir / release_uat_evidence_check.GATE_SUMMARY_FILENAME).write_text(
            json.dumps({"accounting": {"attempted": attempted}}), encoding="utf-8"
        )
    violations, shards, executed = release_uat_evidence_check.check_evidence(tmp_path, f"trunk-{sha('a')}")
    assert shards == 3 and executed == 8
    assert violations == [
        f"release-uat-trunk-{sha('a')}-stage3-shard: the shard executed zero cells",
    ]
    assert trunk_uat.SHARD_DIRECTORY.fullmatch(f"release-uat-trunk-{sha('a')}-stage3-shard") is not None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _repo_with_version(tmp_path: Path, version: str) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pyproject.toml").write_bytes(f'[project]\nname = "benchbox"\nversion = "{version}"\n'.encode())
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", "version")
    return repo, _git(repo, "rev-parse", "HEAD")


class AdmissionWorld:
    def __init__(self, tmp_path: Path, monkeypatch, version: str, produced: str) -> None:
        self.repo, self.sha = _repo_with_version(tmp_path, version)
        self.output = tmp_path / "out" / "admitted"
        self.selections = 0
        self.changed_producer = False
        self.verified_binaries: list[list[str]] = []
        files = {f"benchbox-{produced}-py3-none-any.whl": b"wheel", f"benchbox-{produced}.tar.gz": b"sdist"}
        self.producer = {"files": {name: {"size": len(data)} for name, data in files.items()}}
        self.run = {"id": 11, "run_attempt": 1}
        self.job = {"id": 12}
        self.artifact = {"id": 5, "digest": "sha256:" + "0" * 64, "expires_at": "2026-12-01T00:00:00Z"}
        consumer = trunk_uat._consumer
        monkeypatch.setattr(consumer, "select_producer", self.select_producer)
        monkeypatch.setattr(consumer, "_download", lambda artifact_id, archive: archive.write_bytes(b"zip"))
        monkeypatch.setattr(consumer, "verify_archive", lambda archive, artifact: files)
        monkeypatch.setattr(consumer, "verify_producer_receipt", lambda directory, run, job: self.producer)
        monkeypatch.setattr(
            consumer,
            "_verify_committed_binaries",
            lambda root, commit, distributions: self.verified_binaries.append(sorted(p.name for p in distributions)),
        )
        self.output.parent.mkdir()
        self.output.parent.chmod(0o700)

    def select_producer(self, sha: str, api):
        self.selections += 1
        run = {**self.run, "id": 99} if self.changed_producer and self.selections > 1 else self.run
        return run, self.job, self.artifact

    def admit(self) -> dict:
        return trunk_uat.admit(self.repo, self.sha, self.output, api=lambda path: {})


def test_admit_publishes_the_verified_distribution_and_a_receipt_for_the_commit_version(tmp_path, monkeypatch):
    world = AdmissionWorld(tmp_path, monkeypatch, "0.5.1", "0.5.1")
    receipt = world.admit()
    assert (receipt["tag"], receipt["version"], receipt["head_sha"]) == ("v0.5.1", "0.5.1", world.sha)
    assert world.selections == 2
    assert world.verified_binaries == [["benchbox-0.5.1-py3-none-any.whl", "benchbox-0.5.1.tar.gz"]]
    published = json.loads((world.output / trunk_uat.ADMISSION_RECEIPT).read_bytes())
    assert published == receipt
    assert (world.output / "benchbox-0.5.1.tar.gz").read_bytes() == b"sdist"
    assert not list(world.output.parent.glob("trunk-uat-admission-*"))


def test_admit_refuses_a_distribution_whose_version_differs_from_the_commit(tmp_path, monkeypatch):
    world = AdmissionWorld(tmp_path, monkeypatch, "0.5.1", "0.5.2")
    with pytest.raises(ValueError, match="distribution package/version mismatch"):
        world.admit()
    assert not world.output.exists()
    assert world.verified_binaries == []


def test_admit_refuses_when_the_producer_changes_during_admission(tmp_path, monkeypatch):
    world = AdmissionWorld(tmp_path, monkeypatch, "0.5.1", "0.5.1")
    world.changed_producer = True
    with pytest.raises(ValueError, match="producer changed during admission"):
        world.admit()
    assert not world.output.exists()


def test_admit_refuses_a_malformed_commit_and_an_existing_output(tmp_path, monkeypatch):
    world = AdmissionWorld(tmp_path, monkeypatch, "0.5.1", "0.5.1")
    with pytest.raises(ValueError, match="invalid commit SHA"):
        trunk_uat.admit(world.repo, "not-a-sha", world.output, api=lambda path: {})
    world.output.mkdir()
    with pytest.raises(ValueError, match="output already exists"):
        world.admit()


def test_changed_paths_list_both_sides_of_a_move_out_of_a_guarded_tree(tmp_path):
    repo, _ = _repo_with_version(tmp_path, "0.5.1")
    (repo / "benchbox").mkdir()
    (repo / "benchbox" / "x.py").write_bytes(b"value = 1\nother = 2\nthird = 3\n" * 20)
    _git(repo, "add", "benchbox/x.py")
    _git(repo, "commit", "-q", "-m", "add")
    middle = _git(repo, "rev-parse", "HEAD")
    (repo / "_project").mkdir()
    _git(repo, "mv", "benchbox/x.py", "_project/x.py")
    _git(repo, "commit", "-q", "-m", "move")
    head = _git(repo, "rev-parse", "HEAD")
    changed = trunk_uat.git_changed_paths(repo, middle, head)
    assert changed is not None and {"benchbox/x.py", "_project/x.py"} <= set(changed)
    assert trunk_uat.skip_path_changes(changed) == ["benchbox/x.py"]


def _timed_out_record(tmp_path: Path, state: str, start: str, end: str):
    github = Github()
    github.jobs[42] = [job("UAT stage 1 sqlite", start, end, state)]
    write_shard(tmp_path / "evidence", 1, "sqlite", [passed_row("sqlite", "a.log")])
    return build(github, tmp_path, job_results={"admit": "success", "uat-stage1": state, "gate": "skipped"})


def test_a_shard_cancelled_at_the_hosted_timeout_is_recorded_as_an_executed_run_past_the_gate(tmp_path):
    record, rows = _timed_out_record(tmp_path, "cancelled", "00:00", "06:00")
    assert record["outcome"] == "executed" and rows
    assert record["over_gate"] is True
    assert record["timed_out_shards"] == ["stage 1 sqlite"]
    assert record["job_results"]["uat-stage1"] == "cancelled"


def test_a_shard_cancelled_early_is_still_a_cancelled_run_not_a_baseline(tmp_path):
    record, rows = _timed_out_record(tmp_path, "cancelled", "00:00", "01:00")
    assert record["outcome"] == "cancelled" and rows == []
    assert "timed_out_shards" not in record


def _step(name: str, start: str, end: str, conclusion: str) -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": f"2026-11-06T{start}:00Z",
        "completed_at": f"2026-11-06T{end}:00Z",
    }


def _shard_with_run_step(tmp_path: Path, job_conclusion: str, step_end: str, step_conclusion: str):
    github = Github()
    shard = job("UAT stage 2 starrocks", "00:00", "05:52", job_conclusion)
    shard["steps"] = [
        _step("Set up job", "00:00", "00:05", "success"),
        _step("Run the stage 2 shard against the trunk distribution", "00:06", step_end, step_conclusion),
        _step("Upload the shard evidence", "05:50", "05:52", "success"),
    ]
    github.jobs[42] = [shard]
    write_shard(tmp_path / "evidence", 2, "starrocks", [passed_row("starrocks", "a.log")])
    return build(github, tmp_path, job_results={"admit": "success", "uat-stage2": job_conclusion, "gate": "failure"})


def test_a_shard_whose_run_step_hit_the_step_timeout_is_an_executed_over_gate_run(tmp_path):
    record, rows = _shard_with_run_step(tmp_path, "failure", "05:51", "failure")
    assert record["outcome"] == "executed" and rows
    assert record["over_gate"] is True
    assert record["timed_out_shards"] == ["stage 2 starrocks"]
    assert "DECISION GATE" in trunk_uat.render_summary(record)


def test_a_failing_run_step_well_before_the_step_timeout_is_a_normal_failure(tmp_path):
    record, rows = _shard_with_run_step(tmp_path, "failure", "01:30", "failure")
    assert record["outcome"] == "executed" and rows
    assert record["over_gate"] is False
    assert record["timed_out_shards"] == []


def test_a_run_step_that_succeeded_is_never_a_timeout_however_long_it_took(tmp_path):
    record, _ = _shard_with_run_step(tmp_path, "success", "05:51", "success")
    assert record["timed_out_shards"] == []


def test_the_step_timeout_the_detector_uses_matches_every_run_step_in_the_workflow():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "trunk-uat.yml").read_text(encoding="utf-8")
    )
    found = 0
    for job_definition in workflow["jobs"].values():
        for step in job_definition.get("steps", []):
            if trunk_uat.RUN_STEP.fullmatch(str(step.get("name"))):
                found += 1
                assert step["timeout-minutes"] == trunk_uat.STEP_TIMEOUT_MINUTES
    assert found == 3
