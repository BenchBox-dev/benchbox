from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities import batch_evidence as fx

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


def _soundness_head(tmp_path: Path) -> tuple[Path, str, str]:
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
    return repo, base, head


@pytest.mark.parametrize("decision", ["", "APPROVED"])
def test_ready_accepts_a_soundness_pr_when_the_oracle_gate_passed(tmp_path: Path, decision: str) -> None:
    repo, base, head = _soundness_head(tmp_path)
    evidence = _evidence(head, review_decision=decision, oracle_gate_state="success")

    assert landing.ready_failures(_identity(repo, head, base), head, evidence, repo) == []


@pytest.mark.parametrize("state", [None, "pending", "failure", "error"])
def test_ready_refuses_a_soundness_pr_without_a_passing_oracle_gate(tmp_path: Path, state: str | None) -> None:
    repo, base, head = _soundness_head(tmp_path)
    evidence = _evidence(head, review_decision="", oracle_gate_state=state)

    failures = landing.ready_failures(_identity(repo, head, base), head, evidence, repo)

    assert any("oracle-gate" in failure for failure in failures)


def test_ready_ignores_the_oracle_gate_for_a_non_soundness_pr(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    assert landing.ready_failures(_identity(repo, head), head, _evidence(head, oracle_gate_state=None), repo) == []


def test_ready_still_refuses_a_soundness_pr_with_requested_changes_despite_a_passing_gate(tmp_path: Path) -> None:
    repo, base, head = _soundness_head(tmp_path)
    evidence = _evidence(head, review_decision="CHANGES_REQUESTED", oracle_gate_state="success")

    failures = landing.ready_failures(_identity(repo, head, base), head, evidence, repo)

    assert any("CHANGES_REQUESTED" in failure for failure in failures)


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


def test_ready_keeps_the_hold_label_on_a_soundness_pr_whose_oracle_gate_passed(tmp_path: Path) -> None:
    repo, base, head = _soundness_head(tmp_path)
    failures = landing.ready_failures(
        _identity(repo, head, base),
        head,
        _evidence(head, hold_labels=["no-auto-merge"], oracle_gate_state="success"),
        repo,
    )
    assert any("no-auto-merge" in f for f in failures)


def test_enqueue_refuses_moved_head() -> None:
    run = FakeRun([])
    with pytest.raises(landing.LandingError, match="moved"):
        landing.enqueue_pr(run, "o/r", 3, HEAD, OTHER)
    assert run.calls == []


def test_enqueue_arms_on_stable_head() -> None:
    run = FakeRun([(0, "x.py\n"), (0, "")])
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)["enqueued"] is True
    assert "--auto" in run.calls[1]


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
    run = FakeRun([(0, "x.py\n"), (0, "")])
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)["enqueued"] is True
    assert "--match-head-commit" in run.calls[1]
    assert HEAD in run.calls[1]


def test_enqueue_refuses_an_unreadable_pin_bump_check_without_arming() -> None:
    run = FakeRun([(1, "boom")])
    with pytest.raises(landing.LandingError, match="omp pin bump evidence could not be verified"):
        landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)
    assert all("--auto" not in call for call in run.calls)


def test_enqueue_refuses_a_pin_bump_without_owner_evidence_without_arming() -> None:
    policy = "omp:\n  version: {v}\n  builds:\n    darwin-arm64:\n      url: https://b.example/d\n      sha256: {h}\n"
    shown = {
        "origin/develop:": policy.format(v="1.0.0", h="ab" * 32),
        f"{HEAD}:": policy.format(v="1.0.1", h="cd" * 32),
    }

    def run(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        if args[:2] == ["git", "show"]:
            return 0, shown[f"{HEAD}:"]
        if args[:2] == ["gh", "api"] and "/contents/" in args[-1]:
            return 0, shown["origin/develop:"]
        if "--jq" in args and args[-1].endswith("/files"):
            return 0, ".github/oracle-reviewers.yml\n"
        if args[-1].endswith("/comments"):
            return 0, ""
        return 0, ""

    calls: list[list[str]] = []
    with pytest.raises(landing.LandingError, match="has no owner evidence"):
        landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD)
    assert all("--auto" not in call for call in calls)


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


def _live_check_records(head: str, conclusion: str = "success") -> list[dict]:
    return [
        {
            "id": 1000 + index,
            "name": name,
            "conclusion": conclusion,
            "head_sha": head,
            "status": "completed",
            "started_at": "2026-09-08T19:00:00Z",
            "details_url": f"https://github.com/o/r/actions/runs/{2000 + index}/job/{3000 + index}",
            "check_suite": {"id": 4000 + index},
        }
        for index, name in enumerate(landing.REQUIRED_CONTEXTS)
    ]


def _lines(records: list[dict]) -> str:
    return "\n".join(json.dumps(record) for record in records)


def _ruleset_contexts() -> str:
    return "\n".join([*landing.REQUIRED_CONTEXTS, "oracle-review"])


def _live_checks_payload(head: str, conclusion: str = "success") -> str:
    return _lines(_live_check_records(head, conclusion))


def _live_pr(head: str, decision: str = "APPROVED", labels: list[dict] | None = None) -> dict:
    return {
        "number": 3,
        "headRefName": "feat/x",
        "headRefOid": head,
        "labels": labels or [],
        "reviewDecision": decision,
    }


def test_verify_evidence_live_accepts_fresh_evidence() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, _ruleset_contexts()), (0, _live_threads())])
    landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_verify_evidence_live_refuses_stale_check() -> None:
    records = _live_check_records(HEAD)
    records[0] = {**records[0], "conclusion": "failure"}
    run = FakeRun([(0, _lines(records))])
    with pytest.raises(landing.LandingError, match="live verification"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_verify_evidence_live_refuses_decision_mismatch() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, _ruleset_contexts())])
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
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, _ruleset_contexts())])
    with pytest.raises(landing.LandingError, match="durable hold"):
        landing.verify_evidence_live(
            run,
            "o/r",
            _live_pr(HEAD, labels=[{"name": "no-auto-merge"}]),
            _evidence(HEAD, hold_labels=[]),
        )


def test_verify_evidence_live_refuses_unresolved_review_thread() -> None:
    run = FakeRun(
        [
            (0, _live_checks_payload(HEAD)),
            (0, _ruleset_contexts()),
            (0, _live_threads([{"isResolved": False, "isOutdated": False}])),
        ]
    )
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


def test_live_check_verdicts_reads_every_latest_suite_through_pagination() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD))])
    landing.live_check_verdicts(run, "o/r", HEAD)
    (cmd,) = run.calls
    assert cmd[:3] == ["gh", "api", "--paginate"]
    assert not any(part in ("-F", "--field", "--raw-field") for part in cmd)
    assert cmd[-1].startswith("repos/o/r/commits/") and "filter=latest" in cmd[-1]


def test_a_failed_live_check_read_refuses() -> None:
    with pytest.raises(landing.LandingError, match="live check verification failed"):
        landing.live_check_verdicts(FakeRun([(1, "boom")]), "o/r", HEAD)


def test_a_malformed_live_check_payload_refuses() -> None:
    with pytest.raises(landing.LandingError, match="unparseable live check payload"):
        landing.live_check_verdicts(FakeRun([(0, "not json")]), "o/r", HEAD)


def test_verify_evidence_live_prefers_latest_run_over_stale_success() -> None:
    records = _live_check_records(HEAD)
    records.append(
        {**records[0], "id": 1, "conclusion": "failure", "started_at": "2026-09-08T20:00:00Z"},
    )
    run = FakeRun([(0, _lines(records))])
    with pytest.raises(landing.LandingError, match="live verification"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def _stale_suite_records() -> list[dict]:
    records = _live_check_records(HEAD)
    stale = {
        **records[0],
        "id": 1,
        "conclusion": "failure",
        "started_at": "2026-09-08T18:00:00Z",
        "details_url": "https://github.com/o/r/actions/runs/77/job/1",
        "check_suite": {"id": 99},
    }
    return [*records, stale]


def test_verify_evidence_live_refuses_a_stale_failed_suite_hidden_by_a_newer_success() -> None:
    run = FakeRun([(0, _lines(_stale_suite_records()))])
    with pytest.raises(landing.LandingError, match=r"suite 99.*actions/runs/77"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_verify_evidence_live_accepts_a_cancelled_suite_superseded_by_a_newer_success() -> None:
    records = _stale_suite_records()
    records[-1] = {**records[-1], "conclusion": "cancelled"}
    run = FakeRun([(0, _lines(records)), (0, _ruleset_contexts()), (0, _live_threads())])
    landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_live_check_verdicts_report_the_newest_success_over_a_superseded_cancelled_suite() -> None:
    records = _stale_suite_records()
    records[-1] = {**records[-1], "conclusion": "cancelled"}
    run = FakeRun([(0, _lines(records))])
    verdicts = landing.live_check_verdicts(run, "o/r", HEAD)
    assert {item["conclusion"] for item in verdicts.values()} == {"success"}


def test_verify_evidence_live_refuses_a_hidden_failure_on_a_ruleset_check_outside_the_evidence() -> None:
    records = _live_check_records(HEAD)
    template = records[0]
    records += [
        {**template, "id": 5, "name": "oracle-review", "check_suite": {"id": 51}, "started_at": "2026-09-08T20:00:00Z"},
        {
            **template,
            "id": 6,
            "name": "oracle-review",
            "conclusion": "failure",
            "check_suite": {"id": 52},
            "started_at": "2026-09-08T19:00:00Z",
            "details_url": "https://github.com/o/r/actions/runs/88/job/6",
        },
    ]
    run = FakeRun([(0, _lines(records)), (0, _ruleset_contexts())])
    with pytest.raises(landing.LandingError, match=r"oracle-review.*actions/runs/88.*make pr-rerun"):
        landing.verify_evidence_live(run, "o/r", _live_pr(HEAD), _evidence(HEAD))


def test_hidden_failed_suites_reports_the_run_to_rerun() -> None:
    run = FakeRun([(0, _lines(_stale_suite_records())), (0, "\n".join(landing.REQUIRED_CONTEXTS))])
    (reason,) = landing.hidden_failed_suites(run, "o/r", "develop", HEAD)
    assert "actions/runs/77" in reason and "make pr-rerun" in reason
    assert "rules/branches/develop" in run.calls[1][-1]


def test_hidden_failed_suites_is_empty_when_every_suite_is_green() -> None:
    run = FakeRun([(0, _live_checks_payload(HEAD)), (0, "\n".join(landing.REQUIRED_CONTEXTS))])
    assert landing.hidden_failed_suites(run, "o/r", "develop", HEAD) == []


def test_hidden_failed_suites_refuses_when_the_branch_lists_no_required_checks() -> None:
    with pytest.raises(landing.LandingError, match="no required status checks"):
        landing.hidden_failed_suites(FakeRun([(0, _live_checks_payload(HEAD)), (0, "")]), "o/r", "develop", HEAD)


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
            (0, "x.py\n"),
            (0, ""),
        ]
    )
    assert landing.enqueue_pr(run, "o/r", 3, HEAD, HEAD, expected_branch="feat/x", expected_node_id="PR_node_3")[
        "enqueued"
    ]
    assert "--match-head-commit" in run.calls[3]


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
    run = FakeRun([(0, pr), (0, _live_threads()), (0, "x.py\n"), (0, "")])
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
    monkeypatch.setattr(landing, "live_commit_statuses", lambda *a, **kw: {})
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


requires_posix_make = pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="needs GNU Make and a POSIX shell; the stubs are shell scripts",
)


def _dry_run(tmp_path: Path, *arguments: str) -> list[str]:
    stubs = tmp_path / "dry-run-bin"
    stubs.mkdir()
    (stubs / "gh").write_text('#!/bin/sh\necho "dry run must not call gh: $*" >&2\nexit 99\n', encoding="utf-8")
    (stubs / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(["make", "-n", *arguments], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return shlex.split(result.stdout.replace("\\\n", " "))


def _adjacent(tokens: list[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:], strict=False))


@requires_posix_make
@pytest.mark.parametrize("target", ["pr-landing-start", "pr-landing-withdraw", "pr-landing-ready"])
def test_landing_targets_pass_the_repository_worktree_and_branch(target: str, tmp_path: Path) -> None:
    tokens = _dry_run(tmp_path, target, "REPO=o/r", "BRANCH=feat/x", "PR=3", "HEAD=abc", "EVIDENCE=e.json")

    assert "scripts/pr_landing.py" in tokens
    assert {("--repo", "o/r"), ("--worktree", "."), ("--branch", "feat/x")} <= _adjacent(tokens)


@requires_posix_make
def test_pr_ready_with_evidence_runs_the_readiness_transaction_and_arms_the_exact_head(tmp_path: Path) -> None:
    tokens = _dry_run(tmp_path, "pr-ready", "REPO=o/r", "BRANCH=feat/x", "PR=3", "HEAD=abc", "EVIDENCE=e.json")

    assert "pr-arm-auto-merge" in tokens
    assert {("--expected-head", "abc"), ("--evidence-json", "e.json")} <= _adjacent(tokens)
    assert "--arm" in tokens


@requires_posix_make
def test_pr_ready_without_evidence_arms_through_pr_arm_and_never_calls_gh_merge(tmp_path: Path) -> None:
    tokens = _dry_run(tmp_path, "pr-ready", "REPO=o/r", "PR=3", "HEAD=abc")

    assert "pr-arm" in tokens and "PR=3" in tokens and "HEAD=abc" in tokens
    assert not {"merge", "--auto"} <= set(tokens)


@requires_posix_make
def test_pr_arm_auto_merge_refuses_to_arm_without_readiness_evidence() -> None:
    result = subprocess.run(
        ["make", "pr-arm-auto-merge", "PR=3", "HEAD=abc", "REPO=o/r"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "EVIDENCE" in result.stderr


_RECORDER = """#!{python}
import json, os, sys
with open(os.environ["CALLS"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps([os.path.basename(sys.argv[0]), sys.argv[1:]]) + "\\n")
if "--verify-parent" in sys.argv:
    sys.exit(int(os.environ.get("VERIFY_EXIT", "0")))
if "scripts/oracle_local_review.py" in sys.argv:
    sys.exit(int(os.environ["LOCAL_REVIEW_EXIT"]))
if "scripts/pr_open_rest.py" in sys.argv:
    print(os.environ["PUBLISH"], 7, "https://github.com/BenchBox-dev/BenchBox/pull/7")
"""
_GIT = """#!/bin/sh
[ -z "$GIT_LOG" ] || echo "$*" >> "$GIT_LOG"
case "$1 $2" in
  "branch --show-current") echo feat/x ;;
  "remote get-url") echo git@github.com:BenchBox-dev/BenchBox.git ;;
  "rev-parse HEAD")
    if [ -n "$HEAD_MOVES" ] && [ -f "$GIT_LOG.seen" ]; then echo 2222222222222222222222222222222222222222
    else : > "$GIT_LOG.seen"; echo 1111111111111111111111111111111111111111; fi ;;
esac
exit 0
"""


def _run_pr_open_result(
    tmp_path: Path, publish: str, local_review_exit: int, *variables: str, head_moves: bool = False
) -> tuple[subprocess.CompletedProcess[bytes], list[list[object]], list[str]]:
    stubs = tmp_path / "bin"
    work = tmp_path / "work"
    stubs.mkdir(parents=True)
    work.mkdir(parents=True)
    for name, body in (
        ("git", _GIT),
        ("uv", _RECORDER.format(python=sys.executable)),
        ("python3", _RECORDER.format(python=sys.executable)),
        ("fake-make", _RECORDER.format(python=sys.executable)),
    ):
        (stubs / name).write_text(body, encoding="utf-8")
        (stubs / name).chmod(0o755)
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile.index(".PHONY: pr-open\n")
    (work / "Makefile").write_text(makefile[start : makefile.index("\n\n", start)] + "\n", encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    git_log = tmp_path / "git.log"
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "GIT_LOG": str(git_log),
        "PUBLISH": publish,
        "LOCAL_REVIEW_EXIT": str(local_review_exit),
        "HEAD_MOVES": "1" if head_moves else "",
        "VERIFY_EXIT": os.environ.get("VERIFY_EXIT", "0"),
    }
    result = subprocess.run(
        ["make", "-s", "pr-open", f"MAKE={stubs / 'fake-make'}", *variables],
        cwd=work,
        env=env,
        check=False,
        capture_output=True,
    )
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    pushes = (
        [line for line in git_log.read_text(encoding="utf-8").splitlines() if line.startswith("push")]
        if git_log.exists()
        else []
    )
    return result, recorded, pushes


def _run_pr_open(tmp_path: Path, publish: str, *variables: str) -> list[list[object]]:
    result, recorded, _ = _run_pr_open_result(tmp_path, publish, 0, *variables)
    result.check_returncode()
    return recorded


def _makes(calls: list[list[object]]) -> list[list[str]]:
    return [list(map(str, args)) for name, args in calls if name == "fake-make"]


@requires_posix_make
def test_pr_open_hands_the_title_and_head_to_the_publisher_and_does_not_arm_by_default(tmp_path: Path) -> None:
    title = """fix: it's "quoted" $HOME $(touch sentinel) `touch sentinel2`"""
    calls = _run_pr_open(tmp_path, "created", f"TITLE={title}")

    (publisher,) = [args for name, args in calls if name == "uv" and "scripts/pr_open_rest.py" in args]
    assert {("--title", title), ("--head-spec", "feat/x"), ("--branch", "feat/x")} <= _adjacent(publisher)
    assert not [m for m in _makes(calls) if "pr-arm" in m or "pr-ready" in m]
    assert not (tmp_path / "work" / "sentinel").exists() and not (tmp_path / "work" / "sentinel2").exists()


@requires_posix_make
@pytest.mark.parametrize("publish", ["created", "reused"])
def test_pr_open_ready_arms_the_pushed_head_through_pr_arm(tmp_path: Path, publish: str) -> None:
    makes = _makes(_run_pr_open(tmp_path, publish, "READY=1"))

    (arm,) = [m for m in makes if "pr-arm" in m]
    assert {"PR=7", "HEAD=1111111111111111111111111111111111111111"} <= set(arm)
    assert not [m for m in makes if "pr-ready" in m]


@requires_posix_make
def test_pr_open_with_evidence_arms_only_a_reused_pr(tmp_path: Path) -> None:
    reused = _makes(_run_pr_open(tmp_path / "reused", "reused", "READY=1", "EVIDENCE=e.json"))
    (ready,) = [m for m in reused if "pr-ready" in m]
    assert {"EVIDENCE=e.json", "URL=https://github.com/BenchBox-dev/BenchBox/pull/7"} <= set(ready)
    assert not [m for m in reused if "pr-arm" in m]

    created = _makes(_run_pr_open(tmp_path / "created", "created", "READY=1", "EVIDENCE=e.json"))
    assert not [m for m in created if "pr-arm" in m or "pr-ready" in m]


def test_makefile_has_separate_bounded_all_open_status_view() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PR_STATUS_ALL_OPEN_LIMIT ?= 1000" in makefile
    status = makefile.split("pr-status:", 1)[1].split("\n\n", 1)[0]
    assert "PR_STATUS_LIMIT" in status
    assert "ALL_OPEN" in status
    assert "PR_STATUS_ALL_OPEN_LIMIT" in status
    assert "All open develop PRs" in status


@requires_posix_make
def test_pr_open_runs_the_local_oracle_review_before_any_push(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("body\n", encoding="utf-8")
    result, calls, pushes = _run_pr_open_result(tmp_path, "created", 0, f"PR_BODY_FILE={body}")

    assert result.returncode == 0
    review_index = next(
        index
        for index, (name, args) in enumerate(calls)
        if name == "python3" and "scripts/oracle_local_review.py" in args
    )
    publish_index = next(index for index, (_, args) in enumerate(calls) if "scripts/pr_open_rest.py" in args)
    assert review_index < publish_index
    review = calls[review_index][1]
    assert {
        ("--policy", ".github/oracle-reviewers.yml"),
        ("--base-ref", "origin/develop"),
    } <= set(zip(review, review[1:], strict=False))
    assert ("--head", "1111111111111111111111111111111111111111") in set(zip(review, review[1:], strict=False))
    assert len(pushes) == 1


@requires_posix_make
def test_pr_open_with_a_parent_reviews_against_it_and_opens_a_draft_on_it(tmp_path: Path) -> None:
    result, calls, pushes = _run_pr_open_result(tmp_path, "created", 0, "PARENT=feat/parent")

    assert result.returncode == 0
    names = [(name, args) for name, args in calls if name in ("uv", "python3")]
    verify = next(i for i, (_, args) in enumerate(names) if "--verify-parent" in args)
    review = next(i for i, (_, args) in enumerate(names) if "scripts/oracle_local_review.py" in args)
    publish = next(
        i for i, (_, args) in enumerate(names) if "scripts/pr_open_rest.py" in args and "--verify-parent" not in args
    )
    assert verify < review < publish
    assert {("--parent", "feat/parent"), ("--branch", "feat/x")} <= _adjacent(names[verify][1])
    assert {("--base-ref", "origin/feat/parent"), ("--trusted-ref", "origin/develop")} <= _adjacent(names[review][1])
    assert {("--parent", "feat/parent")} <= _adjacent(names[publish][1])
    assert "--draft" in names[publish][1]
    assert len(pushes) == 1


@requires_posix_make
def test_pr_open_with_a_refused_parent_stops_before_the_review_and_any_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIFY_EXIT", "1")
    result, calls, pushes = _run_pr_open_result(tmp_path, "created", 0, "PARENT=feat/parent")

    assert result.returncode != 0
    assert not [args for name, args in calls if name == "python3" and "scripts/oracle_local_review.py" in args]
    assert not [args for _, args in calls if "scripts/pr_open_rest.py" in args and "--verify-parent" not in args]
    assert pushes == []


@requires_posix_make
@pytest.mark.parametrize("exit_code", [1, 2])
def test_pr_open_refuses_before_any_push_when_the_local_oracle_review_does_not_clear(
    tmp_path: Path, exit_code: int
) -> None:
    result, calls, pushes = _run_pr_open_result(tmp_path, "created", exit_code)

    assert result.returncode != 0
    assert "local oracle review did not clear this head" in result.stderr.decode()
    assert pushes == []
    assert not [args for _, args in calls if "scripts/pr_open_rest.py" in args]


@requires_posix_make
def test_pr_open_pushes_and_arms_the_reviewed_sha_when_the_branch_ref_moves_during_the_review(tmp_path: Path) -> None:
    reviewed = "1111111111111111111111111111111111111111"
    result, calls, pushes = _run_pr_open_result(tmp_path, "created", 0, "READY=1", head_moves=True)

    assert result.returncode == 0
    assert pushes == [f"push origin {reviewed}:refs/heads/feat/x"]
    review = next(args for name, args in calls if name == "python3" and "scripts/oracle_local_review.py" in args)
    assert ("--head", reviewed) in set(zip(review, review[1:], strict=False))
    (arm,) = [m for m in _makes(calls) if "pr-arm" in m]
    assert f"HEAD={reviewed}" in arm


def test_pr_open_never_selects_the_reviewer_copy() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile.index(".PHONY: pr-open\n")
    recipe = makefile[start : makefile.index("\n\n", start)]
    assert "scripts/oracle_local_review.py" in recipe
    assert "--reviewer-ref" not in recipe


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (f"oracle-review: waiting for the oracle's verdict on {'a' * 40}, or a stand-in attestation", "pending"),
        (f"oracle-review: waiting for the oracle's verdict on {'b' * 40}, or a stand-in attestation", "failure"),
        ("oracle-review: the oracle's latest review reports SHIP WITH FIXES, not success", "failure"),
        ("oracle-review: error: could not fetch reviews", "failure"),
        ("", "failure"),
    ],
)
def test_live_statuses_distinguish_waiting_from_failed_oracle(description: str, expected: str) -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        assert "--paginate" in cmd
        assert cmd[-1] == f"repos/o/r/commits/{'a' * 40}/statuses?per_page=100"
        return 0, f"oracle-gate\tfailure\tbenchbox-oracle[bot]\tBot\t{description}\n"

    assert landing.live_commit_statuses(run, "o/r", "a" * 40) == {"oracle-gate": expected}


@pytest.mark.parametrize(("login", "account_type"), [("joeharris76", "User"), ("benchbox-oracle", "User"), ("", "")])
def test_live_statuses_ignore_an_oracle_gate_status_the_app_did_not_create(login: str, account_type: str) -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        return 0, (
            f"oracle-gate\tsuccess\t{login}\t{account_type}\tforged\n"
            "oracle-gate\tfailure\tbenchbox-oracle[bot]\tBot\toracle-review: waiting\n"
        )

    assert landing.live_commit_statuses(run, "o/r", "a" * 40) == {"oracle-gate": "failure"}


def test_live_statuses_without_an_app_oracle_gate_status_omit_the_context() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        return 0, "oracle-gate\tsuccess\tjoeharris76\tUser\tforged\n"

    assert landing.live_commit_statuses(run, "o/r", "a" * 40) == {}


def test_live_statuses_keep_newest_context_across_paginated_results() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        assert "--paginate" in cmd
        return 0, "core\tsuccess\n" + "optional\tsuccess\n" * 100 + "hold\tfailure\ncore\tfailure\nhold\tsuccess\n"

    assert landing.live_commit_statuses(run, "o/r", "a" * 40) == {
        "core": "success",
        "optional": "success",
        "hold": "failure",
    }


def test_live_statuses_keep_contexts_that_contain_spaces() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        return 0, "build / test\tfailure\tboom\nlint\tsuccess\n"

    assert landing.live_commit_statuses(run, "o/r", "a" * 40) == {"build / test": "failure", "lint": "success"}


def _batch(tmp_path: Path, scope: dict[str, list[str]] | None = None) -> fx.BatchFixture:
    repo = _repo(tmp_path / "r")
    batch = fx.BatchFixture(repo, scope or {"a": ["a/**"], "b": ["b/**"]})
    batch.land("a", {"a/x.txt": "a"})
    batch.land("b", {"b/y.txt": "b"})
    return batch


def _verdict(batch: fx.BatchFixture, *, tracker: dict | None = None, evidence: dict | None = None) -> list[str]:
    fresh = tracker if tracker is not None else batch.tracker()
    built = evidence if evidence is not None else batch.evidence()
    return landing.check_batch_evidence(batch.repo, built, fx.head_of(batch.repo), fresh)


def test_batch_evidence_passes_for_members_prepared_at_successive_heads(tmp_path: Path) -> None:
    batch = _batch(tmp_path)

    assert batch.receipts["a"]["source_base"] == batch.start
    assert batch.receipts["b"]["source_base"] == batch.receipts["a"]["integration_head"] != batch.start
    assert _verdict(batch) == []


def test_batch_evidence_refuses_a_receipt_changed_since_the_evidence_was_built(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    fresh = batch.tracker()
    fresh["receipts"]["a"]["verification"]["suite"] = "re-prepared"

    failures = _verdict(batch, tracker=fresh, evidence=evidence)

    assert any("receipts changed" in failure for failure in failures)


def test_batch_evidence_refuses_a_receipt_whose_accepted_head_is_not_in_the_final_head(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.receipts["a"]["accepted_head"] = "c" * 40

    failures = _verdict(batch)

    assert any("member a accepted head is not an ancestor" in failure for failure in failures)


def test_batch_evidence_requires_a_rerun_for_every_member_the_tracker_marks(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.overlap("b", "a", ["a/x.txt"])
    evidence = batch.evidence()
    assert _verdict(batch, evidence=evidence) == []
    del evidence["current_acceptance"]["a"]

    failures = _verdict(batch, evidence=evidence)

    assert any("current acceptance is missing for member a" in failure for failure in failures)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("revision", "d" * 40, "not at the PR head"),
        ("suite", "weaker-suite", "not its own receipt verification"),
        ("command", ["true"], "not its own receipt verification"),
        ("status", "failed", "not passed and clean"),
        ("clean", False, "not passed and clean"),
        ("worktree", "/elsewhere", "outside the integration worktree"),
    ],
)
def test_batch_evidence_refuses_acceptance_that_is_not_the_members_own_pass_at_the_head(
    tmp_path: Path, key: str, value: object, expected: str
) -> None:
    batch = _batch(tmp_path)
    batch.overlap("b", "a", ["a/x.txt"])
    evidence = batch.evidence()
    evidence["current_acceptance"]["a"][key] = value

    failures = _verdict(batch, evidence=evidence)

    assert any(expected in failure for failure in failures)


@pytest.mark.parametrize("binding", ["tracker_head", "final_pr_head", "evidence_head"])
def test_batch_evidence_refuses_a_final_head_the_tracker_does_not_bind(tmp_path: Path, binding: str) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    fresh = batch.tracker()
    earlier = batch.receipts["a"]["integration_head"]
    if binding == "tracker_head":
        fresh["integration_head"] = earlier
    elif binding == "final_pr_head":
        fresh["final_pr"]["head"] = earlier
    else:
        evidence["final_head"] = earlier

    assert _verdict(batch, tracker=fresh, evidence=evidence) != []


def test_batch_evidence_refuses_a_path_no_member_scope_covers(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.fix({"z/other.txt": "z"})

    failures = _verdict(batch)

    assert any("z/other.txt matches no member scope" in failure for failure in failures)


def test_batch_evidence_refuses_changed_files_that_differ_from_the_integration_diff(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    evidence["changed_files"] = ["a/x.txt"]

    failures = _verdict(batch, evidence=evidence)

    assert any("differ from the recomputed integration diff" in failure for failure in failures)


@pytest.mark.parametrize("defect", ["missing", "findings", "other-range"])
def test_batch_evidence_refuses_a_missing_or_unusable_member_review(tmp_path: Path, defect: str) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    if defect == "missing":
        del evidence["member_reviews"]["a"]
    elif defect == "findings":
        evidence["member_reviews"]["a"]["outcome"] = "findings"
    else:
        evidence["member_reviews"]["a"] = evidence["member_reviews"]["b"]

    failures = _verdict(batch, evidence=evidence)

    assert any("member a" in failure and "review" in failure for failure in failures)


@pytest.mark.parametrize("outcome", ["clear", "not-reviewed", "override"])
def test_batch_evidence_accepts_every_landable_review_outcome(tmp_path: Path, outcome: str) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    evidence["member_reviews"]["a"]["outcome"] = outcome

    assert _verdict(batch, evidence=evidence) == []


def test_batch_evidence_passes_a_develop_merge_refresh_without_counting_its_files(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.develop_merge({"docs/dev.txt": "from develop"})
    evidence = batch.evidence()

    assert "docs/dev.txt" not in evidence["changed_files"]
    assert _verdict(batch, evidence=evidence) == []
    evidence["changed_files"] = sorted([*evidence["changed_files"], "docs/dev.txt"])
    assert any("differ from the recomputed" in failure for failure in _verdict(batch, evidence=evidence))


def test_batch_evidence_counts_the_files_of_a_recorded_fix_advance(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.fix({"b/fix.txt": "fixed"})
    evidence = batch.evidence()

    assert "b/fix.txt" in evidence["changed_files"]
    assert _verdict(batch, evidence=evidence) == []
    evidence["changed_files"].remove("b/fix.txt")
    assert any("differ from the recomputed" in failure for failure in _verdict(batch, evidence=evidence))


def test_batch_evidence_passes_a_net_reversion_recorded_by_a_fix_advance(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.fix({"a/x.txt": None})
    evidence = batch.evidence()

    assert "a/x.txt" not in evidence["changed_files"]
    assert _verdict(batch, evidence=evidence) == []
    del evidence["current_acceptance"]["a"]
    assert any("missing for member a" in failure for failure in _verdict(batch, evidence=evidence))


def test_batch_evidence_refuses_v1_receipts(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    batch.receipts["a"]["schema"] = "prepared_work_v1"

    failures = _verdict(batch)

    assert any("member a lacks a prepared_work_v2 receipt" in failure for failure in failures)


def test_batch_evidence_refuses_another_schema_or_batch(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    evidence["schema"] = "benchbox_batch_evidence_v1"
    evidence["batch_id"] = "other"

    failures = _verdict(batch, evidence=evidence)

    assert any("schema" in failure for failure in failures)
    assert any("different batch" in failure for failure in failures)


def test_batch_evidence_refuses_a_checkout_that_is_not_at_the_final_head(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    evidence = batch.evidence()
    fx.git(batch.repo, "checkout", "-q", "--detach", batch.receipts["a"]["integration_head"])

    failures = landing.check_batch_evidence(batch.repo, evidence, evidence["final_head"], batch.tracker())

    assert any("checkout HEAD is not the PR head" in failure for failure in failures)


def test_read_batch_tracker_pages_every_oversized_section(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    tracker = batch.tracker()
    tracker["integration_log"] += [
        {"seq": index, "kind": "fix", "head": "e" * 40, "changed_files": [f"b/f{index}.txt"]} for index in range(1, 200)
    ]
    show = fx.FakeBatchShow(tracker)

    read = landing.read_batch_tracker("b", show)
    assert {key: value for key, value in read.items() if key != "rev"} == tracker
    assert any("--offset" in call and call[call.index("--offset") + 1] != "0" for call in show.calls)


def test_read_batch_tracker_refuses_unknown_batches_and_unparseable_output(tmp_path: Path) -> None:
    show = fx.FakeBatchShow(_batch(tmp_path).tracker())

    with pytest.raises(landing.LandingError, match="unknown batch"):
        landing.read_batch_tracker("other", show)
    with pytest.raises(landing.LandingError, match="unparseable"):
        landing.read_batch_tracker("b", lambda _cmd: (0, "not json"))


def test_read_batch_tracker_refuses_a_section_read_after_the_revision_moved(tmp_path: Path) -> None:
    show = fx.FakeBatchShow(_batch(tmp_path).tracker())

    def moving(cmd: list[str]) -> tuple[int, str]:
        result = show(cmd)
        show.rev = "rev-2"
        return result

    with pytest.raises(landing.LandingError, match="revision moved"):
        landing.read_batch_tracker("b", moving)


def _capability(**overrides: object) -> dict:
    batch_capability = {
        "version": 2,
        "receipt_schema": "prepared_work_v2",
        "store_schema_version": 4,
        "supported_schema_versions": [1, 2, 3, 4],
    }
    batch_capability.update(overrides)
    return {"runtime": {"todo_db_batch": batch_capability, "mcp_handshake": {"result": "pass"}}}


def _ready_batch(tmp_path: Path, rollout: dict | None) -> fx.BatchFixture:
    repo = _repo(tmp_path / "r")
    if rollout is not None:
        fx.write_commit(repo, {"_project/analysis/batch-rollout-evidence.json": json.dumps(rollout)}, "record runtime")
    batch = fx.BatchFixture(repo, {"a": ["a/**"], "b": ["b/**"]})
    batch.land("a", {"a/x.txt": "a"})
    batch.land("b", {"b/y.txt": "b"})
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True, capture_output=True)
    return batch


def _ready(batch: fx.BatchFixture, tracker: object, evidence: dict | None) -> list[str]:
    head = fx.head_of(batch.repo)
    ready = _evidence(
        head,
        repository="o/r",
        branch="feat/x",
        worktree=str(batch.repo),
        require_batch=True,
        batch_evidence=evidence,
    )

    def read(_batch_id: str) -> dict:
        if isinstance(tracker, Exception):
            raise tracker
        assert isinstance(tracker, dict)
        return tracker

    return landing.ready_failures(_identity(batch.repo, head), head, ready, batch.repo, read)


def test_batch_ready_passes_with_the_v2_capability_and_a_fresh_matching_tracker(tmp_path: Path) -> None:
    batch = _ready_batch(tmp_path, _capability())

    assert _ready(batch, batch.tracker(), batch.evidence()) == []


@pytest.mark.parametrize(
    "capability",
    [
        _capability(version=1),
        _capability(receipt_schema="prepared_work_v1"),
        _capability(store_schema_version=3, supported_schema_versions=[1, 2, 3]),
        {"runtime": {"schema_version": 3, "prepared_receipt_schema": "prepared_work_v1"}},
        None,
    ],
)
def test_batch_ready_refuses_a_runtime_without_the_v2_capability(tmp_path: Path, capability: dict | None) -> None:
    batch = _ready_batch(tmp_path, capability)

    failures = _ready(batch, batch.tracker(), batch.evidence())

    assert any("todo_db_batch version 2" in failure for failure in failures)


def test_batch_ready_refuses_when_the_tracker_cannot_be_read(tmp_path: Path) -> None:
    batch = _ready_batch(tmp_path, _capability())

    failures = _ready(batch, landing.LandingError("cannot read tracker batch b: offline"), batch.evidence())

    assert any("offline" in failure for failure in failures)


def test_batch_ready_requires_the_evidence_file(tmp_path: Path) -> None:
    batch = _ready_batch(tmp_path, _capability())

    failures = _ready(batch, batch.tracker(), None)

    assert any("batch evidence is required" in failure for failure in failures)


def test_batch_ready_refuses_evidence_for_another_branch_or_repository(tmp_path: Path) -> None:
    batch = _ready_batch(tmp_path, _capability())
    evidence = batch.evidence()
    evidence["integration_branch"] = "feat/other"
    evidence["repository"] = "o/other"

    failures = _ready(batch, batch.tracker(), evidence)

    assert any("repository does not match" in failure for failure in failures)
    assert any("integration branch does not match" in failure for failure in failures)


def test_batch_evidence_file_is_loaded_relative_to_the_checkout(tmp_path: Path) -> None:
    (tmp_path / "evidence.json").write_text('{"schema": "x"}', encoding="utf-8")

    assert landing._load_batch_evidence(tmp_path, "evidence.json") == {"schema": "x"}
    assert landing._load_batch_evidence(tmp_path, None) is None
    with pytest.raises(landing.LandingError, match="unreadable batch evidence"):
        landing._load_batch_evidence(tmp_path, "missing.json")
