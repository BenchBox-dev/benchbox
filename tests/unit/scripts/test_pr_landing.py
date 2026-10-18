from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("pr_landing", SCRIPTS / "pr_landing.py")
assert spec is not None and spec.loader is not None
landing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = landing
spec.loader.exec_module(landing)

HEAD = "a" * 40
OTHER = "b" * 40


class FakeRun:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        rc, payload = self.responses.pop(0)
        return rc, payload if isinstance(payload, str) else json.dumps(payload)


def _repo(path: Path) -> Path:
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
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    bare = path.parent / (path.name + "-origin.git")
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "branch", "--set-upstream-to=origin/main", "main"], cwd=path, check=True, capture_output=True
    )
    return path


def _identity(repo: Path, head: str = HEAD, base: str | None = None) -> landing.GitIdentity:
    return landing.GitIdentity(
        repo=str(repo),
        repository="o/r",
        branch="feat/x",
        worktree=str(repo),
        head=head,
        base=base or head,
        upstream="origin/main",
    )


def _green_checks(head: str = HEAD) -> list[dict]:
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


def _evidence(head: str = HEAD, **over: object) -> landing.ReadyEvidence:
    base: dict[str, object] = {
        "expected_head": head,
        "review_decision": "APPROVED",
        "dispositions_complete": True,
        "check_runs": _green_checks(head),
    }
    base.update(over)
    return landing.ReadyEvidence(**base)


def _canonical_batch(repo: Path, head: str) -> dict[str, object]:
    scope = {"a": ["**"]}
    receipt = {
        "schema": landing.PREPARED_RECEIPT_SCHEMA,
        "batch_id": "b",
        "member_id": "a",
        "owner_generation": "1" * 32,
        "source_worktree": str(repo),
        "source_revision": head,
        "source_base": head,
        "accepted_head": head,
        "integration_head": head,
        "scope_hash": landing._scope_digest(scope),
        "changed_files": [],
        "verification": {
            "status": "passed",
            "revision": head,
            "clean": True,
            "suite": "focused",
            "command": ["pytest", "-q"],
        },
    }
    final_pr = {"number": 1, "node_id": "PR_1", "head": head}
    return {
        "batch_id": "b",
        "project_id": "project",
        "repository": "o/r",
        "owner": "worker",
        "owner_generation": "1" * 32,
        "integration_branch": "main",
        "integration_worktree": str(repo),
        "start_head": head,
        "members": ["a"],
        "scope": scope,
        "scope_hash": landing._scope_digest(scope),
        "delivery_boundary": "final-pr",
        "terminal_outcome": "merged",
        "integration_head": head,
        "accepted_members": {"a": head},
        "integrated_members": {"a": {"accepted_head": head, "integration_head": head}},
        "prepared_receipts": {"a": receipt},
        "review_dispositions": {
            "a": {
                "status": "approved",
                "current": True,
                "resolved": True,
                "member_id": "a",
                "head": head,
                "integration_head": head,
            }
        },
        "final_pr": final_pr,
        "final_evidence": {
            "status": "passed",
            "batch_id": "b",
            "project_id": "project",
            "repository": "o/r",
            "tree_worktree": str(repo),
            "tree_revision": head,
            "integration_branch": "main",
            "integration_head": head,
            "scope_hash": landing._scope_digest(scope),
            "member_heads": {"a": head},
            "member_ranges": {"a": {"base": head, "head": head}},
            "changed_files": [],
            "final_pr": final_pr,
            "suite": "combined-tree",
            "clean": True,
        },
    }


def test_resolve_pr_refuses_cross_branch_match() -> None:
    run = FakeRun([(0, [{"number": 9, "headRefName": "feat/other", "headRefOid": HEAD}])])
    with pytest.raises(landing.WrongPR):
        landing.resolve_pr(run, "o/r", "feat/x")


def test_resolve_pr_returns_none_when_absent() -> None:
    assert landing.resolve_pr(FakeRun([(0, [])]), "o/r", "feat/x") is None


def test_withdraw_disables_and_reverifies() -> None:
    run = FakeRun(
        [
            (0, {"number": 3, "state": "OPEN", "autoMergeRequest": {"id": "x"}, "labels": []}),
            (0, {}),
            (0, {"autoMergeRequest": None}),
        ]
    )
    assert landing.withdraw_readiness(run, "o/r", 3)["verified"] is True


def test_withdraw_on_merged_pr_stops_with_race() -> None:
    run = FakeRun([(0, {"number": 3, "state": "MERGED"})])
    with pytest.raises(landing.MergedRace):
        landing.withdraw_readiness(run, "o/r", 3)


def test_withdraw_never_touches_hold_labels() -> None:
    run = FakeRun(
        [(0, {"number": 3, "state": "OPEN", "autoMergeRequest": None, "labels": [{"name": "no-auto-merge"}]})]
    )
    landing.withdraw_readiness(run, "o/r", 3)
    joined = [" ".join(call) for call in run.calls]
    assert not any("add-label" in c or "remove-label" in c or "--label" in c for c in joined)


def test_ready_all_green(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert landing.ready_failures(_identity(repo, head), head, _evidence(head), repo) == []


def test_ready_allows_empty_review_decision_for_non_soundness_pr(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("documentation")
    subprocess.run(["git", "add", "docs/note.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    evidence = _evidence(head, review_decision="")

    assert landing.ready_failures(_identity(repo, head, base), head, evidence, repo) == []


def test_ready_rejects_empty_review_decision_for_soundness_pr(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "publication").mkdir()
    (repo / "publication" / "ledger-seed.json").write_text("{}")
    subprocess.run(["git", "add", "publication/ledger-seed.json"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "soundness"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    failures = landing.ready_failures(_identity(repo, head, base), head, _evidence(head, review_decision=""), repo)

    assert any("not APPROVED" in failure for failure in failures)


def test_batch_ready_requires_active_registered_runtime(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    evidence = _evidence(head, batch=_canonical_batch(repo, head), require_batch=True)

    failures = landing.ready_failures(_identity(repo, head), head, evidence, repo)

    assert any("active todo-db runtime" in failure for failure in failures)


def test_start_identity_is_persisted_and_consumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "r")
    identity = _identity(repo, HEAD)
    monkeypatch.setenv("BENCHBOX_PR_LANDING_DIR", str(tmp_path / "landing-state"))
    path = landing.record_start_identity(repo, identity.branch, identity, {"number": 3, "id": "PR_3"})

    assert path.is_file()
    assert landing.require_start_identity(repo, identity, identity.branch, pr_number=3)["schema"] == (
        landing.START_RECORD_SCHEMA
    )
    identity.head = OTHER
    with pytest.raises(landing.LandingError, match="changed after start"):
        landing.require_start_identity(repo, identity, identity.branch, pr_number=3, require_unchanged_head=True)


def test_ready_rejects_late_review_unpublished_and_stale_checks(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "late.txt").write_text("review feedback arriving")
    failures = landing.ready_failures(
        _identity(repo, head),
        head,
        _evidence(
            head, review_decision="CHANGES_REQUESTED", dispositions_complete=False, check_runs=_green_checks(OTHER)
        ),
        repo,
    )
    assert any("unpublished work" in f for f in failures)
    assert any("CHANGES_REQUESTED" in f for f in failures)
    assert any("dispositions" in f for f in failures)
    assert any("not expected head" in f for f in failures)


def test_ready_holds_soundness_even_with_caller_approval(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "publication").mkdir()
    (repo / "publication" / "ledger-seed.json").write_text("{}")
    subprocess.run(["git", "add", "publication/ledger-seed.json"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "soundness"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    failures = landing.ready_failures(
        _identity(repo, head, base),
        head,
        _evidence(head, hold_labels=["no-auto-merge"], soundness_paths_changed=False),
        repo,
    )
    assert any("no-auto-merge" in f for f in failures)
    assert any("soundness" in f for f in failures)
    still_blocked = landing.ready_failures(
        _identity(repo, head, base),
        head,
        _evidence(head, soundness_paths_changed=False, maintainer_approved=True),
        repo,
    )
    assert any("soundness" in failure for failure in still_blocked)


def test_enqueue_refuses_moved_head() -> None:
    run = FakeRun([])
    with pytest.raises(landing.LandingError, match="moved"):
        landing.enqueue_pr(run, "o/r", 3, HEAD, OTHER)
    assert run.calls == []


def test_enqueue_arms_on_stable_head() -> None:
    run = FakeRun([(0, "")])
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)["enqueued"] is True
    assert "--auto" in run.calls[0]


def test_batch_binding_requires_ancestors_and_quiescence(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    assert landing.check_batch_binding(repo, batch, head) == []
    assert landing.check_batch_binding(repo, {**batch, "active_writers": ["w"]}, head) != []
    assert landing.check_batch_binding(repo, {**batch, "members": ["B"]}, head) != []
    assert landing.check_batch_binding(repo, {**batch, "integration_head": OTHER}, head) != []


def test_batch_binding_rejects_dirty_current_integration_checkout(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    (repo / "dirty.txt").write_text("uncommitted")

    failures = landing.check_batch_binding(repo, batch, head)

    assert any("current integration checkout is dirty" in failure for failure in failures)


def test_batch_binding_rejects_current_head_mismatch(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    declared_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, declared_head)
    (repo / "later.txt").write_text("later integration commit")
    subprocess.run(["git", "add", "later.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "later"], cwd=repo, check=True, capture_output=True)

    failures = landing.check_batch_binding(repo, batch, declared_head)

    assert any("current integration checkout HEAD" in failure for failure in failures)


def test_followup_roundtrip_and_resume(tmp_path: Path) -> None:
    state = landing.FollowupState(
        owner="o", session="s", scope="pr", pr=1, head=HEAD, phase="queue", next_action="watch queue"
    )
    path = landing.record_followup(tmp_path, "k", state)
    assert path.is_file()
    assert landing.resume_followup(landing.load_followup(tmp_path, "k")) == {
        "status": "queue",
        "next_action": "watch queue",
    }
    assert landing.load_followup(tmp_path, "missing") is None


def test_followup_legacy_record_migrates_once_and_loads_idempotently(tmp_path: Path) -> None:
    state = landing.FollowupState(
        owner="owner",
        session="legacy-session",
        scope="pr",
        head=HEAD,
        attempts=1,
        reentries=1,
        phase="queue",
        next_action="watch queue",
    )
    legacy_path = landing._legacy_followup_path(tmp_path, "legacy/key")
    legacy_path.write_text(json.dumps(landing.asdict(state)), encoding="utf-8")
    canonical_path = landing.followup_path(tmp_path, "legacy/key")

    loaded = landing.load_followup(tmp_path, "legacy/key")
    assert loaded == state
    assert canonical_path.is_file()
    assert not legacy_path.exists()
    migrated_bytes = canonical_path.read_bytes()

    assert landing.load_followup(tmp_path, "legacy/key") == state
    assert canonical_path.read_bytes() == migrated_bytes
    assert list(tmp_path.glob("*.json")) == [canonical_path]


def test_followup_legacy_migration_preserves_budget_and_state_on_rerecord(tmp_path: Path) -> None:
    legacy_state = landing.FollowupState(
        owner="owner",
        session="legacy-session",
        scope="pr",
        attempts=1,
        reentries=1,
        phase="queue",
        next_action="watch queue",
    )
    legacy_path = landing._legacy_followup_path(tmp_path, "legacy-key")
    legacy_path.write_text(json.dumps(landing.asdict(legacy_state)), encoding="utf-8")

    path = landing.record_followup(
        tmp_path,
        "legacy-key",
        landing.FollowupState(
            owner="owner",
            session="new-session",
            scope="pr",
            attempts=1,
            reentries=1,
            phase="queue",
            next_action="renew claim",
        ),
    )
    loaded = landing.load_followup(tmp_path, "legacy-key")
    assert path == landing.followup_path(tmp_path, "legacy-key")
    assert loaded is not None
    assert loaded.owner == "owner"
    assert loaded.attempts == 1
    assert loaded.reentries == 1
    assert loaded.phase == "queue"
    assert loaded.next_action == "renew claim"
    assert not legacy_path.exists()


def test_followup_legacy_migration_consumes_safe_name_collision_without_duplicate(tmp_path: Path) -> None:
    state = landing.FollowupState(owner="owner", session="s1", scope="pr", next_action="first")
    legacy_path = landing._legacy_followup_path(tmp_path, "a/b")
    legacy_path.write_text(json.dumps(landing.asdict(state)), encoding="utf-8")

    landing.load_followup(tmp_path, "a/b")
    assert landing.load_followup(tmp_path, "a_b") is None

    landing.record_followup(tmp_path, "a_b", landing.FollowupState(owner="owner", session="s2", scope="pr"))
    assert landing.followup_path(tmp_path, "a/b") != landing.followup_path(tmp_path, "a_b")
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_consume_retry_migrates_legacy_budget_to_canonical_path(tmp_path: Path) -> None:
    state = landing.FollowupState(owner="owner", session="s", scope="pr", head=HEAD)
    legacy_path = landing._legacy_followup_path(tmp_path, "retry-key")
    legacy_path.write_text(json.dumps(landing.asdict(state)), encoding="utf-8")

    assert landing.consume_retry(tmp_path, "retry-key", "rerun", HEAD)["allowed"] is True
    loaded = landing.load_followup(tmp_path, "retry-key")
    assert loaded is not None
    assert loaded.attempts == 1
    assert landing.followup_path(tmp_path, "retry-key").is_file()
    assert not legacy_path.exists()


def test_followup_keys_with_same_safe_name_remain_distinct(tmp_path: Path) -> None:
    first = landing.FollowupState(owner="o", session="s1", scope="pr", next_action="first")
    second = landing.FollowupState(owner="o", session="s2", scope="pr", next_action="second")
    landing.record_followup(tmp_path, "a/b", first)
    landing.record_followup(tmp_path, "a_b", second)

    assert landing.followup_path(tmp_path, "a/b") != landing.followup_path(tmp_path, "a_b")
    assert landing.load_followup(tmp_path, "a/b").session == "s1"
    assert landing.load_followup(tmp_path, "a_b").session == "s2"


def test_followup_never_reports_done_from_emptiness() -> None:
    assert landing.resume_followup(landing.FollowupState(owner="o", session="s", scope="pr"))["status"] == "unknown"
    assert landing.resume_followup(landing.FollowupState(owner="o", session="s", scope="pr", terminal="merged")) == {
        "status": "merged",
        "next_action": "",
    }


def test_retry_bounds() -> None:
    state = landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD)
    assert landing.allow_retry(state, "rerun", HEAD)["allowed"] is True
    assert landing.allow_retry(state, "rerun", OTHER)["allowed"] is False
    assert landing.allow_retry(state, "requeue", HEAD)["allowed"] is True
    spent = landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD, attempts=1, reentries=1)
    assert landing.allow_retry(spent, "rerun", HEAD)["allowed"] is False
    assert landing.allow_retry(spent, "requeue", HEAD)["allowed"] is False


def test_resolve_pr_rejects_non_list_payload() -> None:
    with pytest.raises(landing.LandingError, match="non-list"):
        landing.resolve_pr(FakeRun([(0, {"message": "ok"})]), "o/r", "feat/x")


def test_followup_coerce_refuses_missing_owner(tmp_path: Path) -> None:
    with pytest.raises(landing.LandingError, match="owner"):
        landing.coerce_followup({"session": "s", "scope": "pr"})
    with pytest.raises(landing.LandingError, match="object"):
        landing.coerce_followup([])
    assert landing.load_followup(tmp_path, "absent") is None


def _batch_followup(**over: object) -> landing.FollowupState:
    values: dict[str, object] = {
        "owner": "integrator",
        "session": "session-1",
        "scope": "batch",
        "phase": "member-closeout",
        "next_action": "record member-b receipt",
        "batch_id": "batch-1",
        "owner_generation": "a" * 32,
        "integrator": "integrator",
        "integration_head": HEAD,
        "members": ["member-a", "member-b"],
        "pending_worker_heads": {"member-b": OTHER},
        "accepted_receipts": {
            "member-a": {
                "receipt_id": "receipt-a",
                "batch_id": "batch-1",
                "owner_generation": "a" * 32,
                "member_id": "member-a",
                "accepted_head": HEAD,
                "integration_head": HEAD,
            }
        },
    }
    values.update(over)
    return landing.FollowupState(**values)


def test_followup_batch_roundtrip_preserves_members_and_final_pr(tmp_path: Path) -> None:
    state = _batch_followup(
        final_pr={"number": 41, "node_id": "PR_node_41", "head": HEAD},
        next_action="watch native merge queue",
        phase="queue",
    )
    landing.record_followup(tmp_path, "batch-1", state)
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    assert loaded.batch_id == "batch-1"
    assert loaded.members == ["member-a", "member-b"]
    assert loaded.pending_worker_heads == {"member-b": OTHER}
    assert set(loaded.accepted_receipts or {}) == {"member-a"}
    assert landing.resume_followup(loaded) == {"status": "queue", "next_action": "watch native merge queue"}


@pytest.mark.parametrize(
    "state",
    [
        _batch_followup(members=[]),
        _batch_followup(members=["member-a", "member-a"]),
        _batch_followup(pending_worker_heads={"unknown": HEAD}),
        _batch_followup(final_pr={"number": 41, "node_id": "PR_node_41", "head": OTHER}),
    ],
)
def test_followup_batch_rejects_invalid_members_workers_and_final_binding(
    tmp_path: Path, state: landing.FollowupState
) -> None:
    with pytest.raises(landing.LandingError):
        landing.record_followup(tmp_path, "batch-1", state)


def test_followup_rejects_unknown_state_field() -> None:
    with pytest.raises(landing.LandingError, match="unknown fields"):
        landing.coerce_followup({"owner": "o", "session": "s", "scope": "pr", "surprise": True})


def test_followup_partial_batch_cannot_claim_merged(tmp_path: Path) -> None:
    state = _batch_followup(terminal="merged", next_action="")
    landing.record_followup(tmp_path, "batch-1", state)
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    result = landing.resume_followup(loaded)
    assert result["status"] == "incomplete"
    assert result["missing_members"] == ["member-b"]
    assert "member-b" in result["next_action"]


def test_followup_rerecord_does_not_lose_accepted_member_or_final_pr(tmp_path: Path) -> None:
    complete = _batch_followup(
        accepted_receipts={
            "member-a": {
                "receipt_id": "receipt-a",
                "batch_id": "batch-1",
                "owner_generation": "a" * 32,
                "member_id": "member-a",
                "accepted_head": HEAD,
                "integration_head": HEAD,
            },
            "member-b": {
                "receipt_id": "receipt-b",
                "batch_id": "batch-1",
                "owner_generation": "a" * 32,
                "member_id": "member-b",
                "accepted_head": OTHER,
                "integration_head": HEAD,
            },
        },
        final_pr={"number": 41, "node_id": "PR_node_41", "head": HEAD},
        terminal="merged",
        next_action="",
        phase="queue",
    )
    landing.record_followup(tmp_path, "batch-1", complete)
    landing.record_followup(
        tmp_path,
        "batch-1",
        landing.FollowupState(
            owner="integrator",
            session="session-2",
            scope="batch",
            phase="queue",
            next_action="re-verify final PR",
        ),
    )
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    assert loaded.owner_generation == "a" * 32
    assert loaded.integrator == "integrator"
    assert loaded.integration_head == HEAD
    assert loaded.members == ["member-a", "member-b"]
    assert loaded.pending_worker_heads == {"member-b": OTHER}
    assert set(loaded.accepted_receipts or {}) == {"member-a", "member-b"}
    assert loaded.final_pr == complete.final_pr
    assert loaded.terminal == "merged"


def test_followup_explicit_pending_map_replaces_and_updates_worker_heads(tmp_path: Path) -> None:
    landing.record_followup(
        tmp_path,
        "batch-1",
        _batch_followup(pending_worker_heads={"member-a": HEAD, "member-b": OTHER}),
    )
    landing.record_followup(
        tmp_path,
        "batch-1",
        _batch_followup(pending_worker_heads={"member-a": OTHER}),
    )
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    assert loaded.pending_worker_heads == {"member-a": OTHER}


def test_followup_partial_update_rejects_integration_head_change(tmp_path: Path) -> None:
    landing.record_followup(
        tmp_path,
        "batch-1",
        _batch_followup(final_pr={"number": 41, "node_id": "PR_node_41", "head": HEAD}),
    )
    with pytest.raises(landing.LandingError, match="immutable integration_head"):
        landing.record_followup(
            tmp_path,
            "batch-1",
            _batch_followup(
                integration_head=OTHER,
                accepted_receipts={
                    "member-a": {
                        "receipt_id": "receipt-a-new",
                        "batch_id": "batch-1",
                        "owner_generation": "a" * 32,
                        "member_id": "member-a",
                        "accepted_head": HEAD,
                        "integration_head": OTHER,
                    }
                },
            ),
        )
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    assert loaded.integration_head == HEAD
    assert loaded.final_pr == {"number": 41, "node_id": "PR_node_41", "head": HEAD}


def test_followup_later_binds_an_integration_head(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "batch-1", _batch_followup(integration_head=None, accepted_receipts=None))
    landing.record_followup(
        tmp_path,
        "batch-1",
        landing.FollowupState(owner="integrator", session="session-2", scope="batch", integration_head=HEAD),
    )
    loaded = landing.load_followup(tmp_path, "batch-1")
    assert loaded is not None
    assert loaded.integration_head == HEAD
    assert loaded.members == ["member-a", "member-b"]
    assert loaded.pending_worker_heads == {"member-b": OTHER}


def test_followup_partial_update_rejects_accepted_receipt_replacement(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "batch-1", _batch_followup())
    with pytest.raises(landing.LandingError, match="cannot replace the accepted receipt"):
        landing.record_followup(
            tmp_path,
            "batch-1",
            _batch_followup(
                accepted_receipts={
                    "member-a": {
                        "receipt_id": "receipt-a-replaced",
                        "batch_id": "batch-1",
                        "owner_generation": "a" * 32,
                        "member_id": "member-a",
                        "accepted_head": HEAD,
                        "integration_head": HEAD,
                    }
                }
            ),
        )


@pytest.mark.parametrize("field", ["due_at", "claim_expires_at"])
def test_followup_rejects_timezone_naive_persisted_time(field: str) -> None:
    state = landing.FollowupState(
        owner="o",
        session="s",
        scope="pr",
        next_action="watch queue",
        **{field: "2020-01-01T00:00:00"},
    )
    with pytest.raises(landing.LandingError, match="explicit timezone"):
        landing.resume_followup(state)


def test_followup_expired_claim_is_explicitly_owned() -> None:
    state = landing.FollowupState(
        owner="o",
        session="s",
        scope="pr",
        next_action="watch queue",
        claim_expires_at="2020-01-01T00:00:00Z",
    )
    assert landing.resume_followup(state) == {
        "status": "claim-expired",
        "next_action": "renew or explicitly transfer the expired claim before continuing",
    }


def test_followup_blocked_reason_remains_the_owned_next_action() -> None:
    state = landing.FollowupState(
        owner="o",
        session="s",
        scope="batch",
        phase="blocked",
        blocked_reason="conflict-resolution-failed",
        next_action="preserve the conflict commit and ask the integrator to resolve it",
    )
    assert landing.resume_followup(state) == {
        "status": "blocked",
        "reason": "conflict-resolution-failed",
        "next_action": "preserve the conflict commit and ask the integrator to resolve it",
    }


def test_enqueue_arms_with_match_head_commit() -> None:
    run = FakeRun([(0, "")])
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)["enqueued"] is True
    assert "--match-head-commit" in run.calls[0]
    assert HEAD in run.calls[0]


def test_bound_withdraw_refuses_foreign_pr() -> None:
    owned = [{"number": 7, "headRefName": "feat/x", "headRefOid": HEAD}]
    run = FakeRun([(0, owned)])
    with pytest.raises(landing.WrongPR, match="owns PR #7"):
        landing.bound_withdraw(run, "o/r", "feat/x", 9)
    assert all("disable-auto" not in " ".join(c) for c in run.calls)


def test_bound_withdraw_allows_pre_pr_explicit_number() -> None:
    run = FakeRun(
        [
            (0, []),
            (0, {"number": 9, "state": "OPEN", "autoMergeRequest": {"enabledAt": "t"}, "labels": []}),
            (0, ""),
            (0, {"autoMergeRequest": None}),
        ]
    )
    assert landing.bound_withdraw(run, "o/r", "feat/new", 9)["withdrew"] == "auto-merge"


def test_record_followup_refuses_cross_owner_overwrite(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s", scope="pr"))
    with pytest.raises(landing.LandingError, match="owned by 'o'"):
        landing.record_followup(tmp_path, "k", landing.FollowupState(owner="r", session="t", scope="pr"))
    same = landing.FollowupState(owner="o", session="s2", scope="pr")
    landing.record_followup(tmp_path, "k", same)
    assert landing.load_followup(tmp_path, "k").session == "s2"


def test_consume_retry_persists_spent_budget(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD))
    assert landing.consume_retry(tmp_path, "k", "rerun", HEAD)["allowed"] is True
    second = landing.consume_retry(tmp_path, "k", "rerun", HEAD)
    assert second["allowed"] is False and "exhausted" in second["reason"]


def _live_checks_payload(head: str, conclusion: str = "success") -> dict:
    runs = [
        {
            "name": name,
            "conclusion": conclusion,
            "head_sha": head,
            "status": "completed",
            "started_at": "2026-09-08T19:00:00Z",
        }
        for name in landing.REQUIRED_CONTEXTS
    ]
    return {"total": len(runs), "runs": runs}


def _live_pr(head: str, decision: str = "APPROVED", labels: list[dict] | None = None) -> dict:
    return {
        "number": 3,
        "headRefName": "feat/x",
        "headRefOid": head,
        "labels": labels or [],
        "reviewDecision": decision,
    }


def test_verify_evidence_live_accepts_fresh_evidence() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, _live_threads())])
    landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_verify_evidence_live_refuses_stale_check() -> None:
    stale = _live_checks_payload(HEAD)
    stale["runs"][0] = {
        "name": landing.REQUIRED_CONTEXTS[0],
        "conclusion": "failure",
        "head_sha": HEAD,
    }
    run = FakeRun([(0, stale)])
    with pytest.raises(landing.LandingError, match="live verification"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_verify_evidence_live_refuses_decision_mismatch() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD))])
    with pytest.raises(landing.LandingError, match="review decision"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD, "CHANGES_REQUESTED"), _evidence(HEAD))


def _live_threads(nodes: list[dict] | None = None, *, has_next: bool = False, cursor: str | None = None) -> dict:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": nodes or [],
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    }
                }
            }
        }
    }


def test_verify_evidence_live_refuses_unclaimed_live_hold() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD))])
    with pytest.raises(landing.LandingError, match="durable hold"):
        landing.verify_evidence_live(
            run,
            "o/r",
            _live_pr(HEAD, labels=[{"name": "no-auto-merge"}]),
            _evidence(HEAD, hold_labels=[]),
        )


def test_verify_evidence_live_refuses_unresolved_review_thread() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, _live_threads([{"isResolved": False, "isOutdated": False}]))])
    with pytest.raises(landing.LandingError, match="unresolved"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_review_thread_verification_accepts_resolved_and_outdated_pages() -> None:
    run = FakeRun(
        [
            (0, _live_threads([{"isResolved": True, "isOutdated": False}], has_next=True, cursor="next")),
            (0, _live_threads([{"isResolved": False, "isOutdated": True}])),
        ]
    )
    assert landing.unresolved_review_threads(run, "o/r", 3) is False
    assert "cursor=next" in run.calls[1]


def test_consume_retry_allows_exactly_once_under_concurrency(tmp_path: Path) -> None:
    import threading

    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD))
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(landing.consume_retry(tmp_path, "k", "rerun", HEAD)))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sum(1 for r in results if r["allowed"]) == 1


def test_rerecord_never_restores_spent_budget(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD))
    assert landing.consume_retry(tmp_path, "k", "rerun", HEAD)["allowed"] is True
    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s2", scope="pr", head=HEAD))
    assert landing.load_followup(tmp_path, "k").attempts == 1
    assert landing.consume_retry(tmp_path, "k", "rerun", HEAD)["allowed"] is False


def test_rerecording_a_new_head_starts_a_fresh_retry_budget(tmp_path: Path) -> None:
    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s", scope="pr", head=HEAD))
    assert landing.consume_retry(tmp_path, "k", "rerun", HEAD)["allowed"] is True

    landing.record_followup(tmp_path, "k", landing.FollowupState(owner="o", session="s2", scope="pr", head=OTHER))
    refreshed = landing.load_followup(tmp_path, "k")
    assert refreshed is not None
    assert refreshed.head == OTHER
    assert refreshed.attempts == 0
    assert refreshed.reentries == 0
    assert landing.consume_retry(tmp_path, "k", "rerun", OTHER)["allowed"] is True


def test_followup_new_head_clears_terminal_result_and_final_pr(tmp_path: Path) -> None:
    landing.record_followup(
        tmp_path,
        "k",
        _batch_followup(
            head=HEAD,
            terminal="merged",
            next_action="",
            final_pr={"number": 3, "node_id": "PR_3", "head": HEAD},
        ),
    )
    landing.record_followup(
        tmp_path,
        "k",
        _batch_followup(head=OTHER, session="s2", phase="review"),
    )

    refreshed = landing.load_followup(tmp_path, "k")
    assert refreshed is not None
    assert refreshed.head == OTHER
    assert refreshed.terminal is None
    assert refreshed.final_pr is None
    assert landing.resume_followup(refreshed)["status"] == "review"


def test_live_check_verdicts_uses_get_only_invocation() -> None:

    run = FakeRun([(0, _live_checks_payload(HEAD))])
    landing.live_check_verdicts(run, "o/r", HEAD)
    (cmd,) = run.calls
    assert cmd[:2] == ["gh", "api"]
    assert not any(part in ("-F", "--field", "--raw-field", "--paginate") for part in cmd)
    assert cmd[2].startswith("repos/o/r/commits/") and "per_page=100" in cmd[2]


def test_verify_evidence_live_prefers_latest_run_over_stale_success() -> None:
    payload = _live_checks_payload(HEAD)
    payload["runs"].append(
        {
            "name": landing.REQUIRED_CONTEXTS[0],
            "conclusion": "failure",
            "head_sha": HEAD,
            "status": "completed",
            "started_at": "2026-09-08T20:00:00Z",
        }
    )
    payload["total"] = len(payload["runs"])
    run = FakeRun([(0, payload)])
    with pytest.raises(landing.LandingError, match="live verification"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_bound_withdraw_requires_declared_node_and_head() -> None:
    pr = {
        "number": 3,
        "id": "PR_node_3",
        "headRefName": "feat/x",
        "headRefOid": HEAD,
        "state": "OPEN",
        "autoMergeRequest": {"id": "queue"},
        "labels": [],
    }
    run = FakeRun([(0, [pr])])
    with pytest.raises(landing.WrongPR, match="node id"):
        landing.bound_withdraw(run, "o/r", "feat/x", 3, expected_node_id="PR_other", expected_head=HEAD)
    assert len(run.calls) == 1
    assert all("disable-auto" not in " ".join(call) for call in run.calls)


def test_resolve_pr_rejects_stale_declared_head() -> None:
    run = FakeRun([(0, [{"number": 3, "id": "PR_node_3", "headRefName": "feat/x", "headRefOid": OTHER}])])
    with pytest.raises(landing.WrongPR, match="head"):
        landing.resolve_pr(run, "o/r", "feat/x", expected_number=3, expected_node_id="PR_node_3", expected_head=HEAD)


def test_enqueue_bound_path_rechecks_full_pr_identity() -> None:
    run = FakeRun(
        [
            (
                0,
                {
                    "number": 3,
                    "id": "PR_node_3",
                    "headRefName": "feat/x",
                    "headRefOid": HEAD,
                    "state": "OPEN",
                    "reviewDecision": "APPROVED",
                },
            ),
            (0, _live_threads()),
            (0, ""),
        ]
    )
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD, expected_branch="feat/x", expected_node_id="PR_node_3")[
        "enqueued"
    ]
    assert "--match-head-commit" in run.calls[2]


@pytest.mark.parametrize("decision", ["", "APPROVED"])
def test_enqueue_preserves_the_verified_review_decision(decision: str) -> None:
    pr = {
        "number": 3,
        "id": "PR_node_3",
        "headRefName": "feat/x",
        "headRefOid": HEAD,
        "state": "OPEN",
        "reviewDecision": decision,
    }
    run = FakeRun([(0, pr), (0, _live_threads()), (0, "")])
    result = landing.enqueue_pr(
        run,
        "o/r",
        3,
        HEAD,
        HEAD,
        expected_branch="feat/x",
        expected_node_id="PR_node_3",
        expected_review_decision=decision,
    )
    assert result["enqueued"] is True
    assert "--match-head-commit" in run.calls[-1]


@pytest.mark.parametrize("before,after", [("", "CHANGES_REQUESTED"), ("APPROVED", ""), ("", "APPROVED")])
def test_enqueue_refuses_a_changed_review_decision(before: str, after: str) -> None:
    pr = {
        "number": 3,
        "id": "PR_node_3",
        "headRefName": "feat/x",
        "headRefOid": HEAD,
        "state": "OPEN",
        "reviewDecision": after,
    }
    run = FakeRun([(0, pr)])
    with pytest.raises(landing.LandingError, match="review disposition changed"):
        landing.enqueue_pr(
            run,
            "o/r",
            3,
            HEAD,
            HEAD,
            expected_branch="feat/x",
            expected_node_id="PR_node_3",
            expected_review_decision=before,
        )
    assert len(run.calls) == 1


def test_enqueue_does_not_accept_a_blocking_review_as_the_expected_decision() -> None:
    run = FakeRun([])
    with pytest.raises(landing.LandingError, match="review decision"):
        landing.enqueue_pr(
            run,
            "o/r",
            3,
            HEAD,
            HEAD,
            expected_branch="feat/x",
            expected_node_id="PR_node_3",
            expected_review_decision="CHANGES_REQUESTED",
        )
    assert run.calls == []


@pytest.mark.parametrize("decision", ["", "APPROVED"])
def test_ready_forwards_the_live_verified_review_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    identity = _identity(repo, head)
    pr = {"number": 3, "id": "PR_node_3", "headRefOid": head}
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "delivery_mode": "serial",
                "review_decision": decision,
                "dispositions_complete": True,
                "check_runs": _green_checks(head),
            }
        )
    )
    monkeypatch.setattr(landing, "require_start_identity", lambda *a, **kw: {})
    monkeypatch.setattr(landing, "resolve_pr", lambda *a, **kw: pr)
    monkeypatch.setattr(landing, "view_pr", lambda *a, **kw: pr)
    events: list[str] = []

    def verify(*args: object) -> None:
        assert args[-1].review_decision == decision
        events.append("verified")

    def enqueue(*args: object, **kwargs: object) -> dict:
        assert events == ["verified"]
        assert kwargs["expected_review_decision"] == decision
        events.append("enqueued")
        return {"enqueued": True}

    monkeypatch.setattr(landing, "verify_evidence_live", verify)
    monkeypatch.setattr(landing, "enqueue_pr", enqueue)
    args = argparse.Namespace(
        expected_head=head,
        pr=3,
        pr_node_id=None,
        repo="o/r",
        evidence_json=evidence_path,
        require_batch=False,
        worktree_id=None,
        arm=True,
    )
    assert landing._run_ready(args, identity, identity.branch, repo) == 0
    assert events == ["verified", "enqueued"]


def test_enqueue_bound_path_rejects_new_unresolved_thread() -> None:
    run = FakeRun(
        [
            (
                0,
                {
                    "number": 3,
                    "id": "PR_node_3",
                    "headRefName": "feat/x",
                    "headRefOid": HEAD,
                    "state": "OPEN",
                    "reviewDecision": "APPROVED",
                },
            ),
            (0, _live_threads([{"isResolved": False, "isOutdated": False}])),
        ]
    )
    with pytest.raises(landing.LandingError, match="unresolved"):
        landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD, expected_branch="feat/x", expected_node_id="PR_node_3")


def test_worktree_binding_rejects_a_reused_checkout_identity(tmp_path: Path) -> None:
    identity = landing.GitIdentity(
        repo=str(tmp_path / "repo"),
        repository="o/r",
        branch="feat/x",
        worktree=str(tmp_path / "repo"),
        head=HEAD,
        base=HEAD,
        upstream="origin/feat/x",
        lifecycle_id="worktree-generation-1",
    )
    with pytest.raises(landing.LandingError, match="worktree lifecycle"):
        landing.check_worktree_binding(identity, worktree_id="worktree-generation-2")


def test_batch_binding_rejects_late_member_and_changed_membership(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    base = _canonical_batch(repo, head)
    late = {**base, "members": ["a", "a"]}
    failures = landing.check_batch_binding(repo, late, head)
    assert any("unique" in failure for failure in failures)
    failures = landing.check_batch_binding(repo, {**base, "members": ["A"]}, head)
    assert any("canonical" in failure or "members" in failure for failure in failures)


def test_batch_binding_rejects_conflict_pending_writer_and_stale_review(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = {
        **_canonical_batch(repo, head),
        "pending_writers": ["member-a"],
        "conflict_resolution": True,
    }
    batch["review_dispositions"] = {
        "a": {
            "status": "approved",
            "current": False,
            "resolved": True,
            "member_id": "a",
            "head": head,
            "integration_head": head,
        }
    }
    failures = landing.check_batch_binding(repo, batch, head)
    assert any("conflict resolution" in failure for failure in failures)
    assert any("active or pending" in failure for failure in failures)
    assert any("stale" in failure for failure in failures)


def test_batch_binding_rejects_review_alias_and_unauthorized_final_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    scope = {"a": ["allowed/**"]}
    scope_hash = landing._scope_digest(scope)
    batch["scope"] = scope
    batch["scope_hash"] = scope_hash
    batch["prepared_receipts"]["a"]["scope_hash"] = scope_hash
    batch["final_evidence"]["scope_hash"] = scope_hash
    batch["review_dispositions"]["a"]["status"] = "accepted"
    batch["final_evidence"]["changed_files"] = ["outside.txt"]
    failures = landing.check_batch_binding(repo, batch, head)
    assert any("not passing" in failure for failure in failures)
    assert any("exceed the frozen batch scope" in failure for failure in failures)


def test_batch_binding_rejects_malformed_receipt_without_crashing(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    batch["prepared_receipts"]["a"]["changed_files"] = None
    failures = landing.check_batch_binding(repo, batch, head)
    assert any("changed_files" in failure for failure in failures)


def test_batch_binding_rejects_member_content_changed_since_receipt(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    receipt = batch["prepared_receipts"]["a"]
    assert isinstance(receipt, dict)
    receipt["changed_files"] = ["changed.txt"]
    assert any("changed_files differ" in failure for failure in landing.check_batch_binding(repo, batch, head))


def test_batch_binding_rejects_accepted_content_overwritten_in_final_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    start = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "changed.txt").write_text("accepted", encoding="utf-8")
    subprocess.run(["git", "add", "changed.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "accepted"], cwd=repo, check=True, capture_output=True)
    accepted = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "changed.txt").write_text("overwritten", encoding="utf-8")
    subprocess.run(["git", "add", "changed.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "overwrite"], cwd=repo, check=True, capture_output=True)
    integrated = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    scope = {"a": ["changed.txt"]}
    receipt = {
        "schema": landing.PREPARED_RECEIPT_SCHEMA,
        "batch_id": "b",
        "member_id": "a",
        "owner_generation": "1" * 32,
        "source_worktree": str(repo),
        "source_revision": accepted,
        "source_base": start,
        "accepted_head": accepted,
        "integration_head": integrated,
        "scope_hash": landing._scope_digest(scope),
        "changed_files": ["changed.txt"],
        "verification": {
            "status": "passed",
            "revision": accepted,
            "clean": True,
            "suite": "focused",
            "command": ["pytest", "-q"],
        },
    }
    final_pr = {"number": 1, "node_id": "PR_1", "head": integrated}
    batch = {
        "batch_id": "b",
        "project_id": "project",
        "repository": "o/r",
        "owner": "worker",
        "owner_generation": "1" * 32,
        "integration_branch": "main",
        "integration_worktree": str(repo),
        "start_head": start,
        "members": ["a"],
        "scope": scope,
        "scope_hash": landing._scope_digest(scope),
        "delivery_boundary": "final-pr",
        "terminal_outcome": "merged",
        "integration_head": integrated,
        "accepted_members": {"a": accepted},
        "integrated_members": {"a": {"accepted_head": accepted, "integration_head": integrated}},
        "prepared_receipts": {"a": receipt},
        "review_dispositions": {
            "a": {
                "status": "approved",
                "current": True,
                "resolved": True,
                "member_id": "a",
                "head": accepted,
                "integration_head": integrated,
            }
        },
        "final_pr": final_pr,
        "final_evidence": {
            "status": "passed",
            "batch_id": "b",
            "project_id": "project",
            "repository": "o/r",
            "tree_worktree": str(repo),
            "tree_revision": integrated,
            "integration_branch": "main",
            "integration_head": integrated,
            "scope_hash": landing._scope_digest(scope),
            "member_heads": {"a": accepted},
            "member_ranges": {"a": {"base": start, "head": accepted}},
            "changed_files": ["changed.txt"],
            "final_pr": final_pr,
            "suite": "combined-tree",
            "clean": True,
        },
    }
    failures = landing.check_batch_binding(repo, batch, integrated)
    assert any("accepted content was overwritten" in failure for failure in failures)


def test_batch_binding_rejects_missing_or_placeholder_final_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    batch = _canonical_batch(repo, head)
    batch.pop("prepared_receipts")
    assert any("prepared_receipts" in failure for failure in landing.check_batch_binding(repo, batch, head))
    batch = _canonical_batch(repo, head)
    batch["final_evidence"] = True
    assert any("final evidence" in failure for failure in landing.check_batch_binding(repo, batch, head))


def test_github_repository_normalizes_supported_origin_forms() -> None:
    for origin in (
        "https://github.com/BenchBox-dev/BenchBox.git",
        "git@github.com:BenchBox-dev/BenchBox.git",
        "ssh://git@github.com/BenchBox-dev/BenchBox.git",
    ):
        assert landing.github_repository(origin) == "benchbox-dev/benchbox"
    with pytest.raises(landing.LandingError):
        landing.github_repository("https://BenchBox-dev.github.com/BenchBox.git")
    with pytest.raises(landing.LandingError, match="owner/name"):
        landing.normalize_github_repository("/tmp/checkout")


def test_git_identity_and_cli_bind_repo_to_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "r")
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:BenchBox-dev/BenchBox.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    identity = landing.git_identity(repo)
    assert identity.repository == "benchbox-dev/benchbox"
    monkeypatch.setattr(landing, "live_run", lambda _: pytest.fail("foreign repository reached gh"))
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    for command in (
        ["start"],
        ["withdraw", "--pr", "3"],
        ["ready", "--pr", "3", "--expected-head", "a" * 40, "--evidence-json", str(evidence)],
    ):
        assert landing.main(["--repo", "foreign/repository", "--worktree", str(repo), *command]) == 1


def test_arm_current_pr_refuses_head_race_before_merge(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    identity = landing.GitIdentity(
        repo=str(repo),
        repository="o/r",
        branch="feat/x",
        worktree=str(repo),
        head=head,
        base=head,
        upstream="origin/main",
    )
    run = FakeRun(
        [
            (0, [{"number": 3, "id": "PR_node_3", "headRefName": "feat/x", "headRefOid": head, "labels": []}]),
            (
                0,
                {
                    "number": 3,
                    "id": "PR_node_3",
                    "headRefName": "feat/x",
                    "headRefOid": OTHER,
                    "reviewDecision": "APPROVED",
                },
            ),
        ]
    )
    with pytest.raises(landing.WrongPR, match="head"):
        landing.arm_current_pr(run, identity, repo, 3)
    assert not any("--auto" in " ".join(call) for call in run.calls)


def test_arm_current_pr_rejects_dirty_checkout_before_hosted_lookup(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    identity = _identity(repo, head)
    (repo / "dirty.txt").write_text("review fix not committed")
    run = FakeRun([])

    with pytest.raises(landing.LandingError, match="unpublished work"):
        landing.arm_current_pr(run, identity, repo, 3)

    assert run.calls == []


def test_make_entrypoints_forward_explicit_landing_bindings() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("pr-landing-start", "pr-landing-withdraw", "pr-landing-ready"):
        body = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        assert "--repo" in body and "--worktree ." in body and "--branch" in body
    arm = makefile.split("pr-arm-auto-merge:", 1)[1].split("\n\n", 1)[0]
    ready = makefile.split("pr-ready:", 1)[1].split("\n\n", 1)[0]
    assert "pr-landing-ready" in arm and "--repo" in arm and "--worktree ." in makefile
    assert "PR_NUMBER" in arm and "gh pr view --repo" in arm and 'PR="$$PR_NUMBER"' in arm
    assert "EVIDENCE is required" in arm and "ARM=1" in arm
    assert "URL" in arm and '"$(URL)"' in arm
    assert "pr-arm-auto-merge" in ready
    open_body = makefile.split("pr-open:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/pr_open_rest.py --repo" in open_body and '--title "$${TITLE:-}"' in open_body
    assert "git remote get-url --push origin" in open_body
    assert "HEAD_SPEC" in open_body and '--head-spec "$$HEAD_SPEC"' in open_body
    assert "Next: make pr-arm once the review fixes are pushed" in open_body
    assert "withheld" not in open_body and "held" not in open_body

    evidence_route = open_body.index('elif [ -n "$(EVIDENCE)$(BATCH)" ]')
    plain_route = open_body.index('pr-arm PR="$$PR_NUMBER"', evidence_route)
    assert 'pr-arm PR="$$PR_NUMBER" REPO="$$REPOSITORY" HEAD="$$(git rev-parse HEAD)"' in open_body[plain_route:]

    evidence_branch = open_body[evidence_route:plain_route]
    assert 'pr-ready REPO="$$REPOSITORY" URL="$$URL" HEAD="$$(git rev-parse HEAD)"' in evidence_branch
    assert 'EVIDENCE="$(EVIDENCE)" BATCH="$(BATCH)"' in evidence_branch
    assert "pr-arm" not in evidence_branch.split("else", 1)[1].split("fi", 1)[0]
    executable = "\n".join(line for line in makefile.splitlines() if not line.lstrip().startswith(("#", "@#")))
    assert "gh pr merge --auto --squash" not in executable
    for target in ("pr-landing-ready", "pr-open", "pr-arm-auto-merge"):
        body = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        assert "\\\n\t@[" not in body
        assert "set -euo pipefail" not in body


def test_makefile_has_separate_bounded_all_open_status_view() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PR_STATUS_ALL_OPEN_LIMIT ?= 1000" in makefile
    status = makefile.split("pr-status:", 1)[1].split("\n\n", 1)[0]
    assert "PR_STATUS_LIMIT" in status
    assert "ALL_OPEN" in status
    assert "PR_STATUS_ALL_OPEN_LIMIT" in status
    assert "All open develop PRs" in status
