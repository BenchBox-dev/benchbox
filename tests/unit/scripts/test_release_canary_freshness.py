from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "release_canary_freshness", Path(__file__).resolve().parents[3] / "scripts" / "release_canary_freshness.py"
)
assert SPEC is not None and SPEC.loader is not None
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **GIT_IDENTITY},
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.summaries: dict[int, dict | None] = {}

    def add(
        self,
        run_id: int,
        hours_ago: float,
        *,
        head_sha: str,
        tested: str | None = None,
        conclusion: str = "success",
        status: str = "completed",
        results: dict[str, str] | None = None,
    ) -> None:
        self.runs.append(
            {
                "id": run_id,
                "status": status,
                "conclusion": conclusion,
                "created_at": _stamp(hours_ago + 1),
                "run_started_at": _stamp(hours_ago),
                "head_sha": head_sha,
            }
        )
        if tested is not None:
            summary = dict.fromkeys(canary.SUMMARY_RESULT_FIELDS, "success")
            summary.update(results or {})
            self.summaries[run_id] = {"commit_sha": tested, **summary}

    def json(self, *args: str):
        endpoint = args[0]
        if "/actions/workflows/release-canary.yml/runs" in endpoint:
            return {"workflow_runs": self.runs}
        if "/actions/runs/" in endpoint and endpoint.endswith("/artifacts?per_page=100"):
            run_id = int(endpoint.split("/runs/")[1].split("/")[0])
            if self.summaries.get(run_id) is None:
                return {"artifacts": []}
            return {"artifacts": [{"id": run_id, "name": canary.SUMMARY_ARTIFACT, "expired": False}]}
        raise AssertionError(endpoint)

    def download(self, artifact_id: int) -> dict[str, bytes]:
        return {canary.SUMMARY_MEMBER: json.dumps(self.summaries[artifact_id]).encode()}


@pytest.fixture
def history(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    older = _commit(repo, "older")
    middle = _commit(repo, "middle")
    tagged = _commit(repo, "tagged")
    _git(repo, "checkout", "-q", "-b", "side", middle)
    side = _commit(repo, "side")
    fake = FakeGitHub()
    monkeypatch.setattr(canary._reuse, "_gh_json", fake.json)
    monkeypatch.setattr(canary._reuse, "_download_artifact", lambda repository, artifact_id: fake.download(artifact_id))

    state = SimpleNamespace(repo=repo, fake=fake, older=older, middle=middle, tagged=tagged, side=side)
    state.evaluate = lambda: canary.evaluate("owner/repo", tagged, repo, NOW)
    return state


def test_a_green_canary_on_an_ancestor_within_48_hours_passes(history):
    history.fake.add(1, 10, head_sha=history.middle, tested=history.middle)
    verdict = history.evaluate()
    assert verdict.ok
    assert "run 1" in verdict.reason and "10.0 h old" in verdict.reason


def test_the_tagged_commit_itself_counts_as_an_ancestor(history):
    history.fake.add(1, 1, head_sha=history.tagged, tested=history.tagged)
    assert history.evaluate().ok


def test_a_canary_exactly_48_hours_old_passes_and_one_minute_older_is_stale(history):
    history.fake.add(1, 48, head_sha=history.middle, tested=history.middle)
    assert history.evaluate().ok
    history.fake.runs.clear()
    history.fake.add(2, 48 + 1 / 60, head_sha=history.middle, tested=history.middle)
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("stale:")
    assert "limit is 48 h" in verdict.reason


def test_no_canary_run_at_all_is_missing(history):
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("missing:")


def test_a_run_that_is_still_in_progress_is_not_a_verdict(history):
    history.fake.add(1, 1, head_sha=history.middle, tested=history.middle, status="in_progress", conclusion="")
    assert history.evaluate().reason.startswith("missing:")


def test_a_red_newest_canary_on_an_ancestor_refuses_even_when_an_older_run_was_green(history):
    history.fake.add(1, 30, head_sha=history.middle, tested=history.middle)
    history.fake.add(2, 5, head_sha=history.middle, tested=history.middle, conclusion="failure")
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("red:")
    assert "run 2" in verdict.reason and "'failure'" in verdict.reason


def test_a_canary_whose_recorded_results_are_not_all_success_is_red(history):
    history.fake.add(1, 5, head_sha=history.middle, tested=history.middle, results={"ruleset_drift_result": "failure"})
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("red:")
    assert "ruleset_drift_result='failure'" in verdict.reason


def test_a_canary_that_tested_a_non_ancestor_commit_does_not_count(history):
    history.fake.add(1, 5, head_sha=history.side, tested=history.side)
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("non-ancestor:")
    assert history.side[:10] in verdict.rows[0]


def test_the_newest_run_on_an_ancestor_decides_after_skipping_runs_on_other_lines(history):
    history.fake.add(1, 30, head_sha=history.middle, tested=history.middle)
    history.fake.add(2, 2, head_sha=history.side, tested=history.side, conclusion="failure")
    verdict = history.evaluate()
    assert verdict.ok and "run 1" in verdict.reason
    assert len(verdict.rows) == 1 and "run 2" in verdict.rows[0]


def test_a_cancelled_run_carries_no_verdict_and_the_next_run_decides(history):
    history.fake.add(1, 30, head_sha=history.middle, tested=history.middle)
    history.fake.add(2, 2, head_sha=history.middle, tested=history.middle, conclusion="cancelled")
    verdict = history.evaluate()
    assert verdict.ok and "run 1" in verdict.reason


def test_the_commit_the_canary_tested_overrides_the_commit_the_run_started_on(history):
    history.fake.add(1, 5, head_sha=history.side, tested=history.middle)
    assert history.evaluate().ok
    history.fake.runs.clear()
    history.fake.add(2, 5, head_sha=history.middle, tested=history.side)
    assert history.evaluate().reason.startswith("non-ancestor:")


def test_a_run_without_a_summary_is_judged_at_its_head_commit(history):
    history.fake.add(1, 5, head_sha=history.middle)
    assert history.evaluate().ok
    history.fake.runs.clear()
    history.fake.add(2, 5, head_sha=history.middle, conclusion="failure")
    assert history.evaluate().reason.startswith("red:")


def test_an_unreadable_start_time_fails_closed(history):
    history.fake.add(1, 5, head_sha=history.middle, tested=history.middle)
    history.fake.runs[0]["run_started_at"] = "not a time"
    history.fake.runs[0]["created_at"] = None
    verdict = history.evaluate()
    assert not verdict.ok and verdict.reason.startswith("error:")


def _check(history, tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = canary.main(
        [
            "check",
            "--head-sha",
            history.tagged,
            "--github-repository",
            "owner/repo",
            "--repo-dir",
            str(history.repo),
        ]
    )
    return code, capsys.readouterr(), summary.read_text(encoding="utf-8")


def test_the_check_command_exits_zero_for_a_fresh_green_canary(history, tmp_path, monkeypatch, capsys):
    history.fake.add(1, 1, head_sha=history.middle, tested=history.middle)
    code, output, summary = _check(history, tmp_path, monkeypatch, capsys)
    assert code == 0 and "fresh green canary" in summary


def test_the_check_command_refuses_a_stale_canary_with_a_named_error(history, tmp_path, monkeypatch, capsys):
    history.fake.add(1, 100, head_sha=history.middle, tested=history.middle)
    code, output, summary = _check(history, tmp_path, monkeypatch, capsys)
    assert code == 1
    assert "::error::Release canary check failed, stale:" in output.err
    assert "release refused" in summary


def test_the_check_command_fails_closed_when_github_cannot_be_read(history, tmp_path, monkeypatch, capsys):
    def broken(*args: str):
        raise ValueError("gh api failed")

    monkeypatch.setattr(canary._reuse, "_gh_json", broken)
    code, output, _ = _check(history, tmp_path, monkeypatch, capsys)
    assert code == 1
    assert "error: ValueError: gh api failed" in output.err
