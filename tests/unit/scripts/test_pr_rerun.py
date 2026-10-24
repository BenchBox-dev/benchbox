from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/pr_rerun.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("pr_rerun", SCRIPT)
assert SPEC and SPEC.loader
pr_rerun = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_rerun
SPEC.loader.exec_module(pr_rerun)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
PR = 7
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BRANCH = "feature/rerun"
REQUIRED = ["core", "oracle-review"]


def _pr(head: str = HEAD, branch: str = BRANCH) -> dict[str, Any]:
    return {
        "number": PR,
        "state": "OPEN",
        "baseRefName": "develop",
        "headRefName": branch,
        "headRefOid": head,
        "headRepository": {"nameWithOwner": REPO},
        "isCrossRepository": False,
    }


def _run(run_id: int, created: str, *, status: str = "completed", conclusion: str | None = "failure", **extra: Any):
    return {
        "id": run_id,
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "head_branch": BRANCH,
        "head_sha": HEAD,
        "head_repository": REPO,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
        **extra,
    }


def _jobs(*, text_edit: bool = False, cancelled: bool = False) -> list[dict[str, Any]]:
    jobs = [{"name": "ci-paths", "status": "completed", "conclusion": "skipped" if text_edit else "success"}]
    if cancelled:
        jobs.append({"name": "code-test", "status": "completed", "conclusion": "cancelled"})
    return jobs


class FakeGitHub:
    def __init__(
        self,
        runs: list[dict[str, Any]],
        jobs: dict[int, list[dict[str, Any]]],
        *,
        retargets: list[str] | None = None,
        shared: list[int] | None = None,
        pr_reads: list[dict[str, Any]] | None = None,
        shared_after: list[int] | None = None,
        check_runs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.check_runs = check_runs or []
        self.lists = 0
        self.shared_after = shared_after
        self.runs = runs
        self.jobs = jobs
        self.retargets = retargets or []
        self.shared = shared or []
        self.pr_reads = pr_reads or []
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        args = command[1:]
        out: Any = ""
        if args[:2] == ["pr", "view"]:
            out = json.dumps(self.pr_reads.pop(0) if self.pr_reads else _pr())
        elif args[:2] == ["pr", "list"]:
            self.lists += 1
            if self.shared_after is not None and self.lists > 1:
                self.shared = self.shared_after
            listed = [{"number": PR, "headRepositoryOwner": {"login": "BenchBox-dev"}}]
            listed += [{"number": n, "headRepositoryOwner": {"login": "BenchBox-dev"}} for n in self.shared]
            out = json.dumps(listed)
        elif args[0] == "api" and "/rules/branches/" in args[-1]:
            out = "\n".join(REQUIRED)
        elif args[0] == "api" and "/check-runs" in args[-1]:
            assert "filter=latest" in args[-1]
            out = "\n".join(json.dumps(item) for item in self.check_runs)
        elif args[0] == "api" and "/timeline" in args[-1]:
            out = "\n".join(json.dumps({"created_at": when}) for when in self.retargets)
        elif args[0] == "api" and "/workflows/ci.yml/runs" in args[-1]:
            out = "\n".join(json.dumps(run) for run in self.runs)
        elif args[0] == "api" and "/jobs" in args[-1]:
            run_id = int(args[-1].split("/runs/")[1].split("/")[0])
            out = "\n".join(json.dumps(job) for job in self.jobs[run_id])
        return subprocess.CompletedProcess(command, 0, out, "")

    def reruns(self) -> list[list[str]]:
        return [call for call in self.calls if call[1:3] == ["run", "rerun"]]


def test_failure_reruns_only_failed_jobs() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()})
    assert "failed jobs of CI run 11" in pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "11", "--repo", REPO, "--failed"]]


@pytest.mark.parametrize("conclusion", ["cancelled", "failure"])
def test_any_cancelled_job_reruns_the_whole_run(conclusion: str) -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion=conclusion)], {11: _jobs(cancelled=True)})
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "11", "--repo", REPO]]


def test_success_reruns_nothing() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion="success")], {11: _jobs()})
    assert "nothing to re-run" in pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


def test_running_ci_is_refused() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", status="in_progress", conclusion=None)], {11: _jobs()})
    with pytest.raises(pr_rerun.RecoveryError, match="still running"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


@pytest.mark.parametrize("conclusion", ["timed_out", "action_required", "startup_failure"])
def test_other_conclusions_are_refused(conclusion: str) -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion=conclusion)], {11: _jobs()})
    with pytest.raises(pr_rerun.RecoveryError, match=conclusion):
        pr_rerun.rerun(REPO, PR, gh)


@pytest.mark.parametrize(
    ("real", "expected"),
    [
        ({"conclusion": "failure"}, ["--failed"]),
        ({"conclusion": "cancelled"}, []),
    ],
)
def test_a_newer_text_edit_run_is_passed_over(real: dict[str, Any], expected: list[str]) -> None:
    runs = [_run(11, "2026-10-17T10:00:00Z", **real), _run(12, "2026-10-17T10:05:00Z", conclusion="success")]
    gh = FakeGitHub(runs, {11: _jobs(), 12: _jobs(text_edit=True)})
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "11", "--repo", REPO, *expected]]


def test_a_pending_real_run_behind_a_text_edit_run_is_refused() -> None:
    runs = [
        _run(11, "2026-10-17T10:00:00Z", status="in_progress", conclusion=None),
        _run(12, "2026-10-17T10:05:00Z", conclusion="success"),
    ]
    gh = FakeGitHub(runs, {11: _jobs(), 12: _jobs(text_edit=True)})
    with pytest.raises(pr_rerun.RecoveryError, match="still running"):
        pr_rerun.rerun(REPO, PR, gh)


def test_the_newest_real_run_is_chosen() -> None:
    runs = [_run(11, "2026-10-17T09:00:00Z"), _run(13, "2026-10-17T11:00:00Z"), _run(12, "2026-10-17T10:00:00Z")]
    gh = FakeGitHub(runs, {11: _jobs(), 12: _jobs(), 13: _jobs()})
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns()[0][3] == "13"


@pytest.mark.parametrize(
    "mismatch",
    [{"head_branch": "other"}, {"head_repository": "fork/BenchBox"}, {"head_sha": OTHER_HEAD}, {"event": "push"}],
)
def test_runs_for_another_head_are_ignored(mismatch: dict[str, Any]) -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", **mismatch)], {11: _jobs()})
    with pytest.raises(pr_rerun.RecoveryError, match="no CI run found"):
        pr_rerun.rerun(REPO, PR, gh)


def test_another_open_pr_on_the_same_head_is_refused() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, shared=[8])
    with pytest.raises(pr_rerun.RecoveryError, match="#8"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


@pytest.mark.parametrize(
    ("retarget", "allowed"),
    [("2026-10-17T09:59:59Z", True), ("2026-10-17T10:00:00Z", False), ("2026-10-17T10:00:01Z", False)],
)
def test_runs_from_before_a_retarget_are_refused(retarget: str, allowed: bool) -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, retargets=["2026-10-01T00:00:00Z", retarget])
    if allowed:
        pr_rerun.rerun(REPO, PR, gh)
        assert gh.reruns()
    else:
        with pytest.raises(pr_rerun.RecoveryError, match="retargeted"):
            pr_rerun.rerun(REPO, PR, gh)
        assert gh.reruns() == []


def test_a_head_change_before_the_rerun_is_refused() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, pr_reads=[_pr(), _pr(head=OTHER_HEAD)])
    with pytest.raises(pr_rerun.RecoveryError, match="head changed"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


def test_a_new_pr_on_the_same_head_before_the_rerun_is_refused() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, shared_after=[9])
    with pytest.raises(pr_rerun.RecoveryError, match="#9"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


def test_no_runs_is_refused() -> None:
    gh = FakeGitHub([], {})
    with pytest.raises(pr_rerun.RecoveryError, match="push a commit"):
        pr_rerun.rerun(REPO, PR, gh)


def test_main_rejects_a_bad_pr_number(capsys: pytest.CaptureFixture[str]) -> None:
    assert pr_rerun.main([], {"PR_RERUN_PR": "7; rm"}) == 2
    assert "positive number" in capsys.readouterr().err


def _check(
    run_id: int, suite: int, started: str, conclusion: str | None, name: str = "oracle-review"
) -> dict[str, Any]:
    return {
        "id": run_id * 10 + suite,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "started_at": started,
        "head_sha": HEAD,
        "details_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/{run_id * 10 + suite}",
        "check_suite": {"id": suite},
    }


STALE_SUITES = [
    _check(501, 1, "2026-10-17T09:00:00Z", "failure"),
    _check(502, 2, "2026-10-17T09:30:00Z", "failure"),
    _check(503, 3, "2026-10-17T10:30:00Z", "success"),
    _check(600, 9, "2026-10-17T10:00:00Z", "success", name="core"),
]


def test_stale_failed_suites_hidden_by_a_newer_success_are_rerun() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion="success")], {11: _jobs()}, check_runs=STALE_SUITES)
    message = pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [
        ["gh", "run", "rerun", "501", "--repo", REPO, "--failed"],
        ["gh", "run", "rerun", "502", "--repo", REPO, "--failed"],
    ]
    assert "run 501 (stale suite: oracle-review)" in message


def test_a_stale_suite_run_is_rerun_alongside_the_newest_failed_ci_run() -> None:
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, check_runs=STALE_SUITES[:1] + STALE_SUITES[2:])
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [
        ["gh", "run", "rerun", "11", "--repo", REPO, "--failed"],
        ["gh", "run", "rerun", "501", "--repo", REPO, "--failed"],
    ]


def test_a_failed_suite_of_the_chosen_ci_run_is_rerun_once() -> None:
    failed = _check(11, 1, "2026-10-17T10:00:00Z", "failure", name="core")
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z")], {11: _jobs()}, check_runs=[failed])
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "11", "--repo", REPO, "--failed"]]


def test_a_cancelled_stale_suite_reruns_its_whole_run() -> None:
    suites = [_check(501, 1, "2026-10-17T09:00:00Z", "cancelled"), STALE_SUITES[2]]
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion="success")], {11: _jobs()}, check_runs=suites)
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "501", "--repo", REPO]]


def test_a_pending_suite_is_not_rerun() -> None:
    suites = [_check(501, 1, "2026-10-17T09:00:00Z", None), STALE_SUITES[2]]
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion="success")], {11: _jobs()}, check_runs=suites)
    assert "nothing to re-run" in pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


def test_a_stale_suite_is_rerun_when_no_ci_run_exists() -> None:
    gh = FakeGitHub([], {}, check_runs=STALE_SUITES[:1] + STALE_SUITES[2:])
    pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == [["gh", "run", "rerun", "501", "--repo", REPO, "--failed"]]


def test_an_unrerunnable_suite_conclusion_is_refused() -> None:
    suites = [_check(501, 1, "2026-10-17T09:00:00Z", "startup_failure"), STALE_SUITES[2]]
    gh = FakeGitHub([_run(11, "2026-10-17T10:00:00Z", conclusion="success")], {11: _jobs()}, check_runs=suites)
    with pytest.raises(pr_rerun.RecoveryError, match="startup_failure"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []


def test_a_stale_suite_from_before_a_retarget_is_refused() -> None:
    gh = FakeGitHub(
        [_run(11, "2026-10-17T10:00:00Z", conclusion="success")],
        {11: _jobs()},
        check_runs=STALE_SUITES,
        retargets=["2026-10-17T10:45:00Z"],
    )
    with pytest.raises(pr_rerun.RecoveryError, match="retargeted"):
        pr_rerun.rerun(REPO, PR, gh)
    assert gh.reruns() == []
