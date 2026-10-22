from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/oracle_review_recover.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oracle_review_recover", SCRIPT)
assert SPEC and SPEC.loader
oracle_review_recover = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle_review_recover
SPEC.loader.exec_module(oracle_review_recover)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "benchbox/BenchBox"
PR = 41
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BRANCH = "feature/oracle-recovery"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _check(run_id: int, *, conclusion: str = "failure", head: str = HEAD) -> dict[str, Any]:
    return {
        "name": "oracle-review",
        "head_sha": head,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def _run(run_id: int, **overrides: Any) -> dict[str, Any]:
    return {
        "id": run_id,
        "path": oracle_review_recover.WORKFLOW_PATH,
        "event": "pull_request",
        "head_branch": BRANCH,
        "head_sha": HEAD,
        "head_repository": REPO,
        "status": "completed",
        "conclusion": "failure",
        **overrides,
    }


class FakeRunner:
    def __init__(
        self,
        checks: list[dict[str, Any]],
        runs: dict[int, dict[str, Any]],
        *,
        rerun_conclusions: dict[int, str] | None = None,
        pr_views: list[dict[str, Any]] | None = None,
        bad_closed_snapshot: bool = False,
        close_returns_failure: bool = False,
        auto_merge: bool = False,
    ) -> None:
        self.auto_merge = auto_merge
        self.checks = [dict(check) for check in checks]
        self.runs = {run_id: dict(run) for run_id, run in runs.items()}
        self.rerun_conclusions = rerun_conclusions or {}
        self.pr_views = list(pr_views or [])
        self.bad_closed_snapshot = bad_closed_snapshot
        self.close_returns_failure = close_returns_failure
        self.pr_view_count = 0
        self.closed = False
        self.events: list[tuple[str, ...]] = []

    def _pull_request(self) -> dict[str, Any]:
        self.pr_view_count += 1
        if self.pr_views:
            payload = self.pr_views.pop(0)
            return dict(payload)
        payload = {
            "number": PR,
            "state": "CLOSED" if self.closed else "OPEN",
            "baseRefName": "develop",
            "headRefName": BRANCH,
            "headRefOid": HEAD,
            "headRepository": {"nameWithOwner": REPO},
            "isCrossRepository": False,
            "autoMergeRequest": {"mergeMethod": "SQUASH"} if self.auto_merge and not self.closed else None,
        }
        if self.closed and self.bad_closed_snapshot:
            self.bad_closed_snapshot = False
            payload["headRefOid"] = OTHER_HEAD
        return payload

    def _paginate(self, args: tuple[str, ...]) -> str:
        envelope = {
            "total_count": len(self.checks),
            "check_runs": [
                {**check, "id": 1000 + index, "app": {"slug": "github-actions"}}
                for index, check in enumerate(self.checks)
            ],
        }
        assert args[args.index("--jq") + 1].startswith(".check_runs[]")
        keys = ("name", "head_sha", "status", "conclusion", "details_url")
        return "".join(json.dumps({key: run.get(key) for key in keys}) + "\n" for run in envelope["check_runs"])

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        args = tuple(command[1:])
        self.events.append(args)
        if args[:2] == ("pr", "view"):
            return _completed(json.dumps(self._pull_request()))
        if args[:2] == ("api", "--paginate"):
            return _completed(self._paginate(args))
        if len(args) >= 2 and args[0] == "api" and args[1].startswith(f"repos/{REPO}/actions/runs/"):
            run_id = int(args[1].rsplit("/", 1)[1])
            return _completed(json.dumps(self.runs[run_id]))
        if args[:2] == ("run", "rerun"):
            run_id = int(args[2])
            self.runs[run_id]["status"] = "in_progress"
            return _completed()
        if args[:2] == ("run", "watch"):
            run_id = int(args[2])
            self.runs[run_id]["status"] = "completed"
            self.runs[run_id]["conclusion"] = self.rerun_conclusions.get(run_id, "success")
            for check in self.checks:
                if check.get("details_url", "").endswith(f"/runs/{run_id}"):
                    check["status"] = "completed"
                    check["conclusion"] = self.runs[run_id]["conclusion"]
            return _completed()
        if args[:2] == ("pr", "close"):
            self.closed = True
            if self.close_returns_failure:
                return _completed(returncode=1, stderr="simulated API failure")
            return _completed()
        if args[:2] == ("pr", "reopen"):
            self.closed = False
            return _completed()
        raise AssertionError(f"unexpected gh command: {command!r}")


def _standard_pr(**overrides: Any) -> dict[str, Any]:
    return {
        "number": PR,
        "state": "OPEN",
        "baseRefName": "develop",
        "headRefName": BRANCH,
        "headRefOid": HEAD,
        "headRepository": {"nameWithOwner": REPO},
        "isCrossRepository": False,
        **overrides,
    }


def test_unsuccessful_runs_are_rerun_serially_before_pr_close_and_reopen() -> None:
    runner = FakeRunner(
        [_check(12), _check(7), _check(99, conclusion="success")],
        {12: _run(12), 7: _run(7), 99: _run(99, conclusion="success")},
    )

    assert oracle_review_recover.recover(REPO, PR, runner) == (2, True)

    rerun_events = [event for event in runner.events if event[:2] == ("run", "rerun")]
    assert [event[2] for event in rerun_events] == ["7", "12"]
    rerun_7 = runner.events.index(("run", "rerun", "7", "--repo", REPO))
    watch_7 = runner.events.index(("run", "watch", "7", "--repo", REPO, "--exit-status"))
    read_7_success = max(
        index for index, event in enumerate(runner.events) if event[:2] == ("api", f"repos/{REPO}/actions/runs/7")
    )
    rerun_12 = runner.events.index(("run", "rerun", "12", "--repo", REPO))
    close = runner.events.index(("pr", "close", str(PR), "--repo", REPO))
    reopen = runner.events.index(("pr", "reopen", str(PR), "--repo", REPO))
    assert rerun_7 < watch_7 < read_7_success < rerun_12 < close < reopen
    assert not any(event[:2] == ("run", "rerun") and event[2] == "99" for event in runner.events)
    assert runner.closed is False


def test_successful_checks_are_not_rerun() -> None:
    runner = FakeRunner(
        [_check(5, conclusion="success")],
        {5: _run(5, conclusion="success")},
    )

    assert oracle_review_recover.recover(REPO, PR, runner) == (0, True)

    assert not any(event[:2] in (("run", "rerun"), ("run", "watch")) for event in runner.events)
    assert runner.events.index(("pr", "close", str(PR), "--repo", REPO)) < runner.events.index(
        ("pr", "reopen", str(PR), "--repo", REPO)
    )


@pytest.mark.parametrize(
    ("run_overrides", "reason"),
    [
        ({"path": ".github/workflows/unrelated.yml"}, "workflow"),
        ({"head_sha": OTHER_HEAD}, "current PR head"),
        ({"head_repository": "fork/BenchBox"}, "head repository"),
    ],
    ids=["wrong-workflow", "wrong-head", "wrong-source-repository"],
)
def test_foreign_workflow_head_or_source_fails_before_pr_mutation(run_overrides: dict[str, str], reason: str) -> None:
    runner = FakeRunner([_check(8)], {8: _run(8, **run_overrides)})

    with pytest.raises(oracle_review_recover.RecoveryError, match=reason):
        oracle_review_recover.recover(REPO, PR, runner)

    assert not any(event[:2] in (("pr", "close"), ("pr", "reopen")) for event in runner.events)
    assert not any(event[:2] == ("run", "rerun") for event in runner.events)


def test_run_that_remains_failed_blocks_pr_close_and_reopen() -> None:
    runner = FakeRunner(
        [_check(14)],
        {14: _run(14)},
        rerun_conclusions={14: "failure"},
    )

    with pytest.raises(oracle_review_recover.RecoveryError, match="did not pass after its serial rerun"):
        oracle_review_recover.recover(REPO, PR, runner)

    assert ("run", "rerun", "14", "--repo", REPO) in runner.events
    assert not any(event[:2] in (("pr", "close"), ("pr", "reopen")) for event in runner.events)


def test_changed_pr_head_blocks_close_and_reopen() -> None:
    runner = FakeRunner(
        [_check(15, conclusion="success")],
        {15: _run(15, conclusion="success")},
        pr_views=[_standard_pr(), _standard_pr(headRefOid=OTHER_HEAD)],
    )

    with pytest.raises(oracle_review_recover.RecoveryError, match="head changed during recovery"):
        oracle_review_recover.recover(REPO, PR, runner)

    assert not any(event[:2] in (("pr", "close"), ("pr", "reopen")) for event in runner.events)


def test_close_side_validation_failure_attempts_to_reopen_the_pr() -> None:
    runner = FakeRunner(
        [_check(16, conclusion="success")],
        {16: _run(16, conclusion="success")},
        bad_closed_snapshot=True,
    )

    with pytest.raises(oracle_review_recover.RecoveryError, match="head changed during recovery"):
        oracle_review_recover.recover(REPO, PR, runner)

    close = runner.events.index(("pr", "close", str(PR), "--repo", REPO))
    reopen = runner.events.index(("pr", "reopen", str(PR), "--repo", REPO))
    assert close < reopen
    assert runner.closed is False


def test_failed_close_attempts_to_reopen_when_pr_state_is_uncertain() -> None:
    runner = FakeRunner(
        [_check(17, conclusion="success")],
        {17: _run(17, conclusion="success")},
        close_returns_failure=True,
    )

    with pytest.raises(oracle_review_recover.RecoveryError, match="simulated API failure"):
        oracle_review_recover.recover(REPO, PR, runner)

    close = runner.events.index(("pr", "close", str(PR), "--repo", REPO))
    reopen = runner.events.index(("pr", "reopen", str(PR), "--repo", REPO))
    assert close < reopen
    assert runner.closed is False


def test_failed_check_run_in_real_api_envelope_is_found_and_rerun() -> None:
    runner = FakeRunner(
        [_check(21), _check(22, conclusion="success")], {21: _run(21), 22: _run(22, conclusion="success")}
    )

    assert oracle_review_recover._failed_run_ids(runner, REPO, HEAD) == [21]
    assert oracle_review_recover.recover(REPO, PR, runner).reruns == 1
    assert ("run", "rerun", "21", "--repo", REPO) in runner.events


def test_armed_auto_merge_is_not_disarmed_by_recovery() -> None:
    runner = FakeRunner([_check(31)], {31: _run(31)}, auto_merge=True)

    result = oracle_review_recover.recover(REPO, PR, runner)

    assert result == (1, False)
    assert ("run", "rerun", "31", "--repo", REPO) in runner.events
    assert not any(event[:2] in (("pr", "close"), ("pr", "reopen")) for event in runner.events)
    assert not any(event[:2] == ("pr", "merge") for event in runner.events)


def test_main_reports_that_auto_merge_was_kept(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(oracle_review_recover, "recover", lambda repo, pr: oracle_review_recover.Recovery(1, False))

    assert oracle_review_recover.main(["--repo", REPO, "--pr", str(PR)]) == 0

    output = capsys.readouterr().out
    assert "auto-merge" in output and "Closed and reopened" not in output
