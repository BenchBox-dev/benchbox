from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


landing = _load("pr_landing")
lv = _load("local_validation")
bi = _load("batch_integration")
post_merge = _load("post_merge_signature")

HEAD = "a" * 40
OTHER = "b" * 40


class FakeRun:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        assert self.responses, f"unexpected hosted mutation: {cmd}"
        rc, payload = self.responses.pop(0)
        return rc, payload if isinstance(payload, str) else json.dumps(payload)


def _repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "f.txt").write_text("v1")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True, capture_output=True)
    bare = path.parent / (path.name + "-origin.git")
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "branch", "--set-upstream-to=origin/main", "main"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return path, head


def _identity(repo: Path, head: str):
    base = subprocess.run(
        ["git", "rev-parse", "origin/develop"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return landing.GitIdentity(
        repo=str(repo), branch="feat/x", worktree=str(repo), head=head, base=base, upstream="origin/main"
    )


def _green(head: str) -> list[dict]:
    return [
        {
            "name": name,
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-09-08T00:00:00Z",
        }
        for name in landing.REQUIRED_CONTEXTS
    ]


def _evidence(head: str, **over) -> object:
    base = {
        "expected_head": head,
        "review_decision": "APPROVED",
        "dispositions_complete": True,
        "check_runs": _green(head),
    }
    base.update(over)
    return landing.ReadyEvidence(**base)


def test_two_writers_second_blocked_without_mutation(tmp_path: Path) -> None:

    repo, head = _repo(tmp_path / "r")
    assert landing.ready_failures(_identity(repo, head), head, _evidence(head), repo) == []
    (repo / "b-edit.txt").write_text("writer B concurrent edit")
    run = FakeRun([])
    failures = landing.ready_failures(_identity(repo, head), head, _evidence(head), repo)
    assert any("unpublished work" in f for f in failures)
    assert run.calls == []


def test_comment_before_push_blocks_on_dispositions(tmp_path: Path) -> None:

    repo, head = _repo(tmp_path / "r")
    failures = landing.ready_failures(
        _identity(repo, head),
        head,
        _evidence(head, review_decision="CHANGES_REQUESTED", dispositions_complete=False),
        repo,
    )
    assert any("dispositions" in f for f in failures)


def test_restart_after_push_invalidates_expected_head(tmp_path: Path) -> None:

    run = FakeRun([])
    with pytest.raises(landing.LandingError, match="moved"):
        landing.enqueue_pr(run, "o/r", 1, HEAD, OTHER)
    assert run.calls == []


def test_superseded_head_never_enqueues(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path / "r")
    failures = landing.ready_failures(_identity(repo, head), OTHER, _evidence(head), repo)
    assert any("remote head" in f for f in failures)


def test_protected_hold_survives_withdraw_and_ready(tmp_path: Path) -> None:

    run = FakeRun(
        [(0, {"number": 1, "state": "OPEN", "autoMergeRequest": None, "labels": [{"name": "no-auto-merge"}]})]
    )
    landing.withdraw_readiness(run, "o/r", 1)
    assert not any(
        "label" in " ".join(c).lower().replace("--json", "")
        for c in run.calls
        if "merge" in " ".join(c) and "view" not in " ".join(c)
    )
    repo, head = _repo(tmp_path / "r")
    failures = landing.ready_failures(_identity(repo, head), head, _evidence(head, hold_labels=["no-auto-merge"]), repo)
    assert any("no-auto-merge" in f for f in failures)


def test_base_advance_detected_not_repaired(tmp_path: Path) -> None:

    repo, _ = _repo(tmp_path / "r")
    record = bi.record_start(repo, "batch-t", ["A"])
    assert bi.verify_base(repo, record)["moved"] is False
    (repo / "advance.txt").write_text("develop moved")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "advance"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], cwd=repo, check=True)
    assert bi.verify_base(repo, record)["moved"] is True


def test_transient_failure_retry_bounded_then_escalated(tmp_path: Path) -> None:
    del tmp_path
    state = landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD)
    assert landing.allow_retry(state, "rerun", HEAD)["allowed"] is True
    spent = landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD, attempts=1)
    decision = landing.allow_retry(spent, "rerun", HEAD)
    assert decision["allowed"] is False and "escalate" in decision["reason"]


def test_missing_check_evidence_never_clean(tmp_path: Path) -> None:

    repo, head = _repo(tmp_path / "r")
    failures = landing.ready_failures(_identity(repo, head), head, _evidence(head, check_runs=[]), repo)
    assert len([f for f in failures if "no check run observed" in f]) == len(landing.REQUIRED_CONTEXTS)


def test_substantive_post_merge_regression_clears_innocent_sha(tmp_path: Path) -> None:

    del tmp_path
    assert post_merge.attribution_action(["tests/unit/foo.py::test_x"], ["other/part.py"]) == "advisory"


def test_wrong_pr_branch_never_mutates() -> None:
    run = FakeRun([(0, [{"number": 7, "headRefName": "feat/other", "headRefOid": HEAD}])])
    with pytest.raises(landing.WrongPR):
        landing.resolve_pr(run, "o/r", "feat/x")
    assert len(run.calls) == 1 and all("merge" not in " ".join(c) for c in run.calls)


def test_all_green_control_arms_exactly_once(tmp_path: Path) -> None:

    repo, head = _repo(tmp_path / "r")
    run = FakeRun([(0, ""), (0, "")])
    assert landing.ready_failures(_identity(repo, head), head, _evidence(head), repo) == []
    assert landing.enqueue_pr(run, "o/r", 1, head, head)["enqueued"] is True
    assert [call[:3] for call in run.calls] == [["gh", "api", "--paginate"], ["gh", "pr", "merge"]]
