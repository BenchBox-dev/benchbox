from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "_project/scripts"))

oracle_gate = importlib.import_module("oracle_gate")
reevaluate = importlib.import_module("oracle_review_reevaluate")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/benchbox"
HEAD = "a" * 40
OLD_HEAD = "b" * 40
SOUNDNESS_FILES = [{"filename": "benchbox/core/equivalence/checker.py"}]


def _review(body: str, commit: str = HEAD) -> dict[str, Any]:
    return {
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "commit_id": commit,
        "state": "COMMENTED",
        "submitted_at": "2026-10-01T13:00:00Z",
        "body": body,
    }


def _thread(resolved: bool) -> dict[str, Any]:
    return {"resolved": resolved, "author": "benchbox-oracle", "author_type": "Bot"}


def _run(run_id: int, conclusion: str | None, **over: Any) -> dict[str, Any]:
    run = {
        "id": run_id,
        "head_sha": HEAD,
        "event": "pull_request",
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "head_repository": {"full_name": REPO},
        "run_attempt": 1,
    }
    return {**run, **over}


class Pull:
    def __init__(self, reviews: list[dict[str, Any]], threads: list[dict[str, Any]], runs: list[dict[str, Any]]):
        self.reviews = reviews
        self.threads = threads
        self.runs = runs
        self.head = HEAD
        self.state = "open"
        self.reruns: list[int] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(oracle_gate, "fetch_pull", lambda *_: {"head": {"sha": self.head}, "changed_files": 1})
        monkeypatch.setattr(oracle_gate, "fetch_files", lambda *_: SOUNDNESS_FILES)
        monkeypatch.setattr(oracle_gate, "fetch_reviews", lambda *_: self.reviews)
        monkeypatch.setattr(oracle_gate, "fetch_threads", lambda *_: self.threads)
        monkeypatch.setattr(oracle_gate, "fetch_base_change_date", lambda *_: None)
        monkeypatch.setattr(oracle_gate, "fetch_comments", lambda *_: [])
        monkeypatch.setattr(oracle_gate, "load_policy_rules", lambda: ((), ()))
        monkeypatch.setattr(reevaluate, "fetch_pull", lambda *_: {"state": self.state, "head": {"sha": self.head}})
        monkeypatch.setattr(reevaluate, "fetch_runs", lambda *_: self.runs)
        monkeypatch.setattr(reevaluate, "rerun", lambda _token, _repo, run_id: self.reruns.append(run_id))


def _success() -> dict[str, Any]:
    return _review(f"### oracle-verdict: success for `{HEAD}`")


def test_resolved_threads_on_a_success_verdict_rerun_every_unsuccessful_run(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = Pull([_success()], [_thread(True)], [_run(1, "failure"), _run(2, "cancelled"), _run(3, "success")])
    pull.install(monkeypatch)
    assert reevaluate.reevaluate_pull("t", REPO, 7) == [1, 2]
    assert pull.reruns == [1, 2]


def test_an_unresolved_oracle_thread_is_never_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = Pull([_success()], [_thread(True), _thread(False)], [_run(1, "failure")])
    pull.install(monkeypatch)
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []
    assert pull.reruns == []


@pytest.mark.parametrize("verdict", ["failure", "pending"])
def test_a_non_success_verdict_is_never_rerun(monkeypatch: pytest.MonkeyPatch, verdict: str) -> None:
    pull = Pull([_review(f"### oracle-verdict: {verdict} for `{HEAD}`")], [_thread(True)], [_run(1, "failure")])
    pull.install(monkeypatch)
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []
    assert pull.reruns == []


def test_a_success_verdict_for_a_stale_head_is_never_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _review(f"### oracle-verdict: success for `{OLD_HEAD}`", commit=OLD_HEAD)
    pull = Pull([stale], [], [_run(1, "failure")])
    pull.install(monkeypatch)
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []
    assert pull.reruns == []


def test_a_head_that_moves_during_the_pass_is_left_for_the_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = Pull([_success()], [], [_run(1, "failure")])
    pull.install(monkeypatch)
    monkeypatch.setattr(reevaluate, "fetch_pull", lambda *_: {"state": "open", "head": {"sha": OLD_HEAD}})
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []
    assert pull.reruns == []


def test_a_pull_request_closed_during_the_pass_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = Pull([_success()], [], [_run(1, "failure")])
    pull.install(monkeypatch)
    pull.state = "closed"
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []
    assert pull.reruns == []


def test_a_passing_head_with_no_unsuccessful_run_reruns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = Pull([_success()], [], [_run(1, "success")])
    pull.install(monkeypatch)
    assert reevaluate.reevaluate_pull("t", REPO, 7) == []


def test_only_finished_unsuccessful_runs_of_the_head_in_this_repository_are_selected() -> None:
    runs = [
        _run(1, "failure"),
        _run(2, None),
        _run(3, "failure", head_sha=OLD_HEAD),
        _run(4, "failure", head_repository={"full_name": "someone/fork"}),
        _run(5, "failure", event="schedule"),
        _run(6, "timed_out", event="workflow_dispatch"),
        _run(7, "failure", run_attempt=reevaluate.MAX_ATTEMPTS),
        _run(8, "neutral"),
    ]
    assert reevaluate.unsuccessful_run_ids(runs, REPO, HEAD) == [1, 6]


def test_main_reports_the_pull_requests_that_error_and_still_visits_the_rest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pulls = [
        {"number": 1, "base": {"ref": "develop"}},
        {"number": 2, "base": {"ref": "release"}},
        {"number": 3, "base": {"ref": "develop"}},
    ]
    visited: list[int] = []

    def fake_reevaluate(_token: str, _repo: str, pr: int) -> list[int]:
        visited.append(pr)
        if pr == 1:
            raise reevaluate.CheckError("boom")
        return []

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(reevaluate, "_paginate", lambda *_: pulls)
    monkeypatch.setattr(reevaluate, "reevaluate_pull", fake_reevaluate)
    assert reevaluate.main(["--repo", REPO]) == reevaluate.ERROR
    assert visited == [1, 3]
    assert "#1" in capsys.readouterr().err


def test_main_refuses_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert reevaluate.main(["--repo", REPO]) == reevaluate.ERROR
