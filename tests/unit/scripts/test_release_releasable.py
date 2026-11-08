from __future__ import annotations

import copy
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import release_artifact_consumer as consumer, release_releasable as releasable

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPOSITORY = {"id": 1135770337, "full_name": consumer.REPOSITORY}


def sha(letter: str) -> str:
    return letter * 40


class TrunkHistory:
    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.jobs: dict[int, list[dict]] = {}
        self.artifacts: dict[int, list[dict]] = {}
        self.calls: list[str] = []
        self._next_id = 100

    def push(self, commit: str, conclusion: str = "success", dist: bool = True) -> dict:
        self._next_id += 1
        run_id = self._next_id
        run = {
            "id": run_id,
            "run_attempt": 1,
            "head_sha": commit,
            "event": "push",
            "head_branch": "develop",
            "path": consumer.WORKFLOW,
            "status": "completed",
            "conclusion": conclusion,
            "created_at": f"2026-11-06T00:{run_id - 100:02d}:00Z",
            "repository": copy.deepcopy(REPOSITORY),
            "head_repository": copy.deepcopy(REPOSITORY),
        }
        self.runs.append(run)
        self.jobs[run_id] = [
            {
                "id": run_id * 10,
                "name": "dist-artifact",
                "run_id": run_id,
                "run_attempt": 1,
                "head_sha": commit,
                "status": "completed",
                "conclusion": "success" if dist else "skipped",
            }
        ]
        self.artifacts[run_id] = (
            [
                {
                    "id": run_id * 10 + 1,
                    "name": f"dist-{commit}-attempt-1",
                    "expired": False,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "digest": "sha256:" + "b" * 64,
                    "size_in_bytes": 1000,
                    "workflow_run": {
                        "id": run_id,
                        "head_sha": commit,
                        "repository_id": REPOSITORY["id"],
                        "head_repository_id": REPOSITORY["id"],
                    },
                }
            ]
            if dist
            else []
        )
        return run

    def api(self, path: str) -> dict:
        self.calls.append(path)
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if not path:
            return copy.deepcopy(REPOSITORY)
        if parsed.path == "actions/workflows/trunk.yml/runs":
            rows = [run for run in self.runs if "head_sha" not in query or run["head_sha"] == query["head_sha"][0]]
            if "status" in query:
                rows = [run for run in rows if run["conclusion"] == query["status"][0]]
            page = int(query.get("page", ["1"])[0])
            return {"total_count": len(rows), "workflow_runs": copy.deepcopy(rows[(page - 1) * 100 : page * 100])}
        parts = parsed.path.split("/")
        if parsed.path.startswith("actions/runs/") and parsed.path.endswith("/jobs"):
            rows = self.jobs[int(parts[2])]
            return {"total_count": len(rows), "jobs": copy.deepcopy(rows)}
        if parsed.path.startswith("actions/runs/") and parsed.path.endswith("/artifacts"):
            rows = self.artifacts[int(parts[2])]
            return {"total_count": len(rows), "artifacts": copy.deepcopy(rows)}
        if parsed.path.startswith("actions/runs/"):
            return copy.deepcopy(next(run for run in self.runs if run["id"] == int(parts[2])))
        if parsed.path.startswith("actions/artifacts/"):
            artifact_id = int(parts[2])
            return copy.deepcopy(next(a for rows in self.artifacts.values() for a in rows if a["id"] == artifact_id))
        raise AssertionError(f"unexpected API path: {path}")


@pytest.fixture
def history() -> TrunkHistory:
    return TrunkHistory()


def test_a_commit_with_a_successful_trunk_run_and_dist_is_releasable(history):
    history.push(sha("a"))
    assert releasable.releasable_problems(sha("a"), history.api) == []


def test_a_failed_trunk_run_fails_naming_the_newest_releasable_commit(history):
    history.push(sha("a"))
    history.push(sha("b"))
    history.push(sha("c"), conclusion="failure")
    (problem,) = releasable.releasable_problems(sha("c"), history.api)
    assert sha("c") in problem
    assert "latest producer is not successful" in problem
    assert f"newest releasable commit on develop: {sha('b')}" in problem


def test_the_newest_releasable_commit_skips_successful_runs_without_a_dist(history):
    history.push(sha("a"))
    history.push(sha("b"), dist=False)
    history.push(sha("c"), conclusion="failure")
    assert releasable.newest_releasable(history.api) == sha("a")
    (problem,) = releasable.releasable_problems(sha("c"), history.api)
    assert f"newest releasable commit on develop: {sha('a')}" in problem


def test_the_newest_releasable_commit_is_the_newest_run_not_the_first_listed(history):
    history.push(sha("a"))
    history.push(sha("b"))
    history.runs.reverse()
    assert releasable.newest_releasable(history.api) == sha("b")


def test_the_commit_being_checked_is_never_named_as_the_alternative(history):
    history.push(sha("a"), dist=False)
    history.push(sha("b"))
    (problem,) = releasable.releasable_problems(sha("a"), history.api)
    assert f"newest releasable commit on develop: {sha('b')}" in problem
    assert releasable.newest_releasable(history.api, exclude=sha("b")) is None


def test_a_commit_that_never_ran_trunk_is_unreleasable(history):
    history.push(sha("a"))
    (problem,) = releasable.releasable_problems(sha("d"), history.api)
    assert "no trunk producer on develop" in problem
    assert sha("a") in problem


def test_with_no_releasable_commit_the_message_says_so(history):
    history.push(sha("a"), conclusion="failure")
    (problem,) = releasable.releasable_problems(sha("a"), history.api)
    assert "no releasable commit among the newest" in problem


def test_only_the_newest_candidates_are_searched(history):
    history.push(sha("a"))
    for index in range(releasable.CANDIDATE_LIMIT):
        history.push(f"{index:040x}", dist=False)
    assert releasable.newest_releasable(history.api) is None


@pytest.mark.parametrize("value", ["abc", "A" * 40, "g" * 40, ""])
def test_a_malformed_commit_is_rejected_without_looking_up_its_run(history, value):
    (problem,) = releasable.releasable_problems(value, history.api)
    assert "40-character" in problem
    assert not any("head_sha=" in call for call in history.calls)


def test_cli_check_exits_nonzero_and_names_the_newest_commit(history, monkeypatch, capsys):
    history.push(sha("a"))
    history.push(sha("b"), conclusion="failure")
    monkeypatch.setattr(releasable._consumer, "github_json", history.api)
    assert releasable.main(["check", "--sha", sha("b")]) == 1
    assert f"newest releasable commit on develop: {sha('a')}" in capsys.readouterr().err
    assert releasable.main(["check", "--sha", sha("a")]) == 0
    assert releasable.main(["newest"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == sha("a")
