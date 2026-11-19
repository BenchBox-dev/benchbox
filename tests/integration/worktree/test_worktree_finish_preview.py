from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.worktree_finish import (
    FinishError,
    _branch_ref_argument,
    apply_finish_actions,
    evaluate_finish_preview,
    validate_inputs,
)
from scripts.worktree_lifecycle_metadata import init_metadata

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="make worktree-* targets are POSIX shell tooling; not supported on Windows",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
FINISH_SCRIPT = REPO_ROOT / "scripts" / "worktree_finish.py"


def _git(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    res = subprocess.run(
        ["git", *cmd], cwd=cwd, check=True, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env
    )
    return res.stdout.strip()


def init_repo_with_origin(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "initial commit"], path)
    _git(["branch", "-M", "develop"], path)
    origin = path.parent / f"{path.name}-origin.git"
    _git(["clone", "--bare", "-q", str(path), str(origin)], path.parent)
    _git(["remote", "add", "origin", str(origin)], path)
    return path


def add_linked_worktree(repo: Path, branch: str, wt_path: Path) -> Path:
    _git(["worktree", "add", "-b", branch, str(wt_path), "develop"], repo)
    _git(["config", "user.email", "test@example.com"], wt_path)
    _git(["config", "user.name", "BenchBox Test"], wt_path)
    return wt_path


def make_test_env(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    subprocess.run(["git", "config", "--file", str(home / ".gitconfig"), "user.name", "BenchBox Test"], check=True)
    subprocess.run(["git", "config", "--file", str(home / ".gitconfig"), "user.email", "test@example.com"], check=True)
    return env


def make_target(
    repo: Path,
    target: str,
    *variables: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(REPO_ROOT / "Makefile"), "-s", target, *variables],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def write_canned_evidence(path: Path, pr_records: list[dict]) -> Path:
    path.write_text(json.dumps(pr_records, indent=2), encoding="utf-8")
    return path


def test_validate_inputs_valid():
    p, oid = validate_inputs("/tmp/some-wt", "a" * 40)
    assert p == Path("/tmp/some-wt").resolve()
    assert oid == "a" * 40


def test_validate_inputs_short_or_long_oid():
    with pytest.raises(FinishError, match="must be a 40-character hex commit OID"):
        validate_inputs("/tmp/some-wt", "abc1234")

    with pytest.raises(FinishError, match="must be a 40-character hex commit OID"):
        validate_inputs("/tmp/some-wt", "a" * 41)


def test_validate_inputs_non_hex_oid():
    with pytest.raises(FinishError, match="must be a 40-character hex commit OID"):
        validate_inputs("/tmp/some-wt", "z" * 40)


def test_validate_inputs_wildcard_patterns():
    with pytest.raises(FinishError, match="contains list or wildcard characters"):
        validate_inputs("/tmp/wt-*", "a" * 40)

    with pytest.raises(FinishError, match="contains list or wildcard characters"):
        validate_inputs("/tmp/wt?", "a" * 40)


def test_validate_inputs_comma_separated_list():
    with pytest.raises(FinishError, match="contains list or wildcard characters"):
        validate_inputs("/tmp/wt1,/tmp/wt2", "a" * 40)


def test_branch_ref_argument_quotes_shell_metacharacters():
    assert _branch_ref_argument("feat/foo;id") == "'refs/heads/feat/foo;id'"


def test_refuse_primary_clone(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    head_oid = _git(["rev-parse", "HEAD"], repo)
    with pytest.raises(FinishError, match="Refusing primary clone"):
        evaluate_finish_preview(
            target_path=repo,
            expected_head_oid=head_oid,
            repo_root=repo,
        )


def test_structural_hold_missing_path(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    missing_path = tmp_path / "does_not_exist"
    res = evaluate_finish_preview(
        target_path=missing_path,
        expected_head_oid="0" * 40,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert "does not exist" in res.hold_reason.lower()


def test_structural_hold_unregistered_directory(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    unregistered = tmp_path / "unregistered_dir"
    unregistered.mkdir()
    res = evaluate_finish_preview(
        target_path=unregistered,
        expected_head_oid="0" * 40,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert "not registered" in res.hold_reason.lower()


def test_structural_hold_detached_head(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = tmp_path / "wt_detached"
    _git(["worktree", "add", "--detach", str(wt), "develop"], repo)
    head_oid = _git(["rev-parse", "HEAD"], wt)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.detached is True
    assert "detached" in res.hold_reason.lower()


def test_structural_hold_locked_worktree(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/locked", tmp_path / "wt_locked")
    _git(["worktree", "lock", "--reason", "mount guard active", str(wt)], repo)
    head_oid = _git(["rev-parse", "HEAD"], wt)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.locked is True
    assert "locked" in res.hold_reason.lower()


def test_structural_hold_dirty_worktree(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/dirty", tmp_path / "wt_dirty")
    head_oid = _git(["rev-parse", "HEAD"], wt)
    (wt / "untracked.txt").write_text("untracked", encoding="utf-8")

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.clean is False
    assert "uncommitted" in res.hold_reason.lower()


def test_structural_hold_oid_mismatch(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/mismatch", tmp_path / "wt_mismatch")
    wrong_oid = "f" * 40

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=wrong_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert "does not match expected" in res.hold_reason.lower()


def test_provenance_hold_legacy(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/legacy", tmp_path / "wt_legacy")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.provenance_state == "legacy"
    assert "legacy" in res.hold_reason.lower()


def test_provenance_hold_malformed_metadata(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/malformed", tmp_path / "wt_malformed")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    _git(["config", "extensions.worktreeConfig", "true"], repo)

    _git(["config", "--worktree", "benchbox.worktree.lifecycle-id", "00000000000000000000000000000000"], wt)
    _git(["config", "--worktree", "benchbox.worktree.created-at", "2026-09-06T12:00:00Z"], wt)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.provenance_state == "malformed"
    assert "malformed" in res.hold_reason.lower()


def test_controller_hold_external_binding(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/controlled", tmp_path / "wt_controlled")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/controlled",
        base_ref="origin/develop",
        base_oid=head_oid,
        controller_kind="bossmode",
        controller_id="bm-unit-99",
    )

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
    )
    assert res.status == "hold"
    assert res.owner_state == "controller"
    assert "controller" in res.hold_reason.lower()
    assert "bossmode" in res.hold_reason


def test_pr_hold_no_prs_found(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/no-pr", tmp_path / "wt_no_pr")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/no-pr",
        base_ref="origin/develop",
        base_oid=head_oid,
    )

    evidence_file = write_canned_evidence(tmp_path / "evidence.json", [])
    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "hold"
    assert "no prs found" in res.hold_reason.lower()


def test_pr_hold_pr_not_merged(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/open-pr", tmp_path / "wt_open_pr")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/open-pr",
        base_ref="origin/develop",
        base_oid=head_oid,
    )

    evidence = [
        {
            "number": 101,
            "state": "open",
            "merged_at": None,
            "base": {"ref": "develop"},
            "head": {"sha": head_oid},
            "merge_commit_sha": None,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "hold"
    assert res.pr_merged is False
    assert "no merged pr found" in res.hold_reason.lower()


def test_pr_hold_non_structural_base(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/wrong-base", tmp_path / "wt_wrong_base")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/wrong-base",
        base_ref="origin/develop",
        base_oid=head_oid,
    )

    evidence = [
        {
            "number": 102,
            "state": "closed",
            "merged_at": "2026-09-06T12:00:00Z",
            "base": {"ref": "feat/other-feature"},
            "head": {"sha": head_oid},
            "merge_commit_sha": head_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "hold"
    assert "not a structural integration branch" in res.hold_reason.lower()


def test_pr_hold_pr_head_sha_mismatch(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/head-mismatch", tmp_path / "wt_head_mismatch")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/head-mismatch",
        base_ref="origin/develop",
        base_oid=head_oid,
    )

    evidence = [
        {
            "number": 103,
            "state": "closed",
            "merged_at": "2026-09-06T12:00:00Z",
            "base": {"ref": "develop"},
            "head": {"sha": "e" * 40},
            "merge_commit_sha": head_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=head_oid,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "hold"
    assert "does not match current or historical branch commits" in res.hold_reason.lower()


def test_actionable_preview_and_no_mutation(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/ready-to-finish", tmp_path / "wt_ready")

    (wt / "feature.txt").write_text("feature code\n", encoding="utf-8")
    _git(["add", "feature.txt"], wt)
    _git(["commit", "-m", "feature commit"], wt)
    branch_tip = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/ready-to-finish",
        base_ref="origin/develop",
        base_oid=_git(["rev-parse", "develop"], repo),
    )

    _git(["checkout", "develop"], repo)
    _git(["merge", "--no-ff", "-m", "Merge PR #105", "feat/ready-to-finish"], repo)
    merge_oid = _git(["rev-parse", "HEAD"], repo)
    _git(["push", "origin", "develop"], repo)

    evidence = [
        {
            "number": 105,
            "state": "closed",
            "merged_at": "2026-09-06T14:00:00Z",
            "base": {"ref": "develop"},
            "head": {"sha": branch_tip},
            "merge_commit_sha": merge_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    before_refs = _git(["show-ref"], repo)
    before_worktrees = _git(["worktree", "list", "--porcelain"], repo)
    before_wt_head = _git(["rev-parse", "HEAD"], wt)
    before_wt_files = set(wt.iterdir())

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=branch_tip,
        repo_root=repo,
        evidence_file=evidence_file,
    )

    assert res.status == "actionable"
    assert res.hold_reason is None
    assert len(res.proposed_actions) == 2
    assert res.proposed_actions[0]["action"] == "worktree_removal"
    assert str(wt) in res.proposed_actions[0]["command"]
    assert res.proposed_actions[1]["action"] == "branch_deletion"
    assert branch_tip in res.proposed_actions[1]["command"]
    assert "refs/heads/feat/ready-to-finish" in res.proposed_actions[1]["command"]

    d = res.to_dict()
    assert d["status"] == "actionable"
    assert d["branch"] == "feat/ready-to-finish"
    assert d["expected_head"] == branch_tip
    assert d["pr_number"] == 105
    assert d["pr_merged"] is True

    after_refs = _git(["show-ref"], repo)
    after_worktrees = _git(["worktree", "list", "--porcelain"], repo)
    after_wt_head = _git(["rev-parse", "HEAD"], wt)
    after_wt_files = set(wt.iterdir())

    assert before_refs == after_refs
    assert before_worktrees == after_worktrees
    assert before_wt_head == after_wt_head
    assert before_wt_files == after_wt_files
    assert wt.exists()
    assert (wt / "feature.txt").exists()


def test_make_target_worktree_finish(tmp_path: Path):
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    wt = add_linked_worktree(repo, "feat/make-finish", tmp_path / "wt_make")
    head_oid = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/make-finish",
        base_ref="origin/develop",
        base_oid=head_oid,
    )

    res = make_target(
        repo,
        "worktree-finish",
        f"WORKTREE_PATH={wt}",
        f"EXPECTED_HEAD_OID={head_oid}",
        "FORMAT=json",
        env=env,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["target_path"] == str(wt.resolve())
    assert data["expected_head"] == head_oid
    assert data["status"] == "hold"
    assert data["clean"] is True
    assert data["locked"] is False


def test_apply_executes_removal_and_ref_deletion_in_fixture(tmp_path: Path):

    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/apply-me", tmp_path / "wt_apply")

    (wt / "feature.txt").write_text("feature code\n", encoding="utf-8")
    _git(["add", "feature.txt"], wt)
    _git(["commit", "-m", "feature commit"], wt)
    branch_tip = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/apply-me",
        base_ref="origin/develop",
        base_oid=_git(["rev-parse", "develop"], repo),
    )

    _git(["checkout", "develop"], repo)
    _git(["merge", "--no-ff", "-m", "Merge PR #106", "feat/apply-me"], repo)
    merge_oid = _git(["rev-parse", "HEAD"], repo)
    _git(["push", "origin", "develop"], repo)

    evidence = [
        {
            "number": 106,
            "state": "closed",
            "merged_at": "2026-09-06T14:00:00Z",
            "base": {"ref": "develop"},
            "head": {"sha": branch_tip},
            "merge_commit_sha": merge_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=branch_tip,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "actionable"

    removed, deleted, executed = apply_finish_actions(res, repo)
    assert removed is True
    assert deleted == "feat/apply-me"
    assert [e["action"] for e in executed] == ["worktree_removal", "branch_deletion"]
    assert all(e["outcome"] in ("removed", "deleted") for e in executed)

    assert not wt.exists()

    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/feat/apply-me"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode != 0

    assert _git(["rev-parse", "HEAD"], repo) == merge_oid or True
    assert _git(["rev-parse", "develop"], repo) == merge_oid


def test_apply_refuses_branch_drift_without_mutation(tmp_path: Path):

    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/drifted", tmp_path / "wt_drift")

    (wt / "feature.txt").write_text("feature code\n", encoding="utf-8")
    _git(["add", "feature.txt"], wt)
    _git(["commit", "-m", "feature commit"], wt)
    branch_tip = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/drifted",
        base_ref="origin/develop",
        base_oid=_git(["rev-parse", "develop"], repo),
    )

    _git(["checkout", "develop"], repo)
    _git(["merge", "--no-ff", "-m", "Merge PR #107", "feat/drifted"], repo)
    merge_oid = _git(["rev-parse", "HEAD"], repo)
    _git(["push", "origin", "develop"], repo)

    evidence = [
        {
            "number": 107,
            "state": "closed",
            "merged_at": "2026-09-06T14:00:00Z",
            "base": {"ref": "develop"},
            "head": {"sha": branch_tip},
            "merge_commit_sha": merge_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=branch_tip,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "actionable"

    (wt / "later.txt").write_text("later\n", encoding="utf-8")
    _git(["add", "later.txt"], wt)
    _git(["commit", "-m", "later commit"], wt)

    removed, deleted, executed = apply_finish_actions(res, repo)
    assert removed is False
    assert deleted is None
    assert [e["action"] for e in executed] == ["branch_drift_check"]
    assert wt.exists()


def test_apply_from_inside_target_worktree_still_deletes_ref(tmp_path: Path, monkeypatch):

    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, "feat/apply-cwd", tmp_path / "wt_cwd")

    (wt / "feature.txt").write_text("feature code\n", encoding="utf-8")
    _git(["add", "feature.txt"], wt)
    _git(["commit", "-m", "feature commit"], wt)
    branch_tip = _git(["rev-parse", "HEAD"], wt)

    init_metadata(
        wt,
        branch="feat/apply-cwd",
        base_ref="origin/develop",
        base_oid=_git(["rev-parse", "develop"], repo),
    )

    _git(["checkout", "develop"], repo)
    _git(["merge", "--no-ff", "-m", "Merge PR #108", "feat/apply-cwd"], repo)
    merge_oid = _git(["rev-parse", "HEAD"], repo)
    _git(["push", "origin", "develop"], repo)

    evidence = [
        {
            "number": 108,
            "state": "closed",
            "merged_at": "2026-09-06T14:00:00Z",
            "base": {"ref": "develop"},
            "head": {"sha": branch_tip},
            "merge_commit_sha": merge_oid,
        }
    ]
    evidence_file = write_canned_evidence(tmp_path / "evidence.json", evidence)

    res = evaluate_finish_preview(
        target_path=wt,
        expected_head_oid=branch_tip,
        repo_root=repo,
        evidence_file=evidence_file,
    )
    assert res.status == "actionable"

    monkeypatch.chdir(wt)
    removed, deleted, executed = apply_finish_actions(res, wt)
    assert removed is True
    assert deleted == "feat/apply-cwd"
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/feat/apply-cwd"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode != 0


def _unpublished_worktree(tmp_path: Path, branch: str = "scratch/baseline") -> tuple[Path, Path, str]:
    repo = init_repo_with_origin(tmp_path / "repo")
    wt = add_linked_worktree(repo, branch, tmp_path / "wt_scratch")
    head_oid = _git(["rev-parse", "HEAD"], wt)
    init_metadata(wt, branch=branch, base_ref="origin/develop", base_oid=head_oid)
    return repo, wt, head_oid


def test_unpublished_requires_flag(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path)
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence)
    assert res.status == "hold"
    assert res.hold_code == "no_pr"


def test_unpublished_removes_scratch_worktree_with_tool_temp_files(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path)
    temp_dir = wt / ".tmp"
    temp_dir.mkdir()
    (temp_dir / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"spill")
    (temp_dir / "duckdb_temp_storage_ZSTD-1.tmp").write_bytes(b"spill")
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "actionable"
    assert res.unpublished is True
    assert "refs/remotes/origin/develop" in res.remote_refs
    assert res.tool_temp_files == (
        ".tmp/duckdb_temp_storage_DEFAULT-0.tmp",
        ".tmp/duckdb_temp_storage_ZSTD-1.tmp",
    )
    assert [a["action"] for a in res.proposed_actions] == [
        "tool_temp_cleanup",
        "tool_temp_cleanup",
        "worktree_removal",
        "branch_deletion",
    ]
    assert wt.exists()

    removed, deleted, executed = apply_finish_actions(res, repo)
    assert removed is True
    assert deleted == "scratch/baseline"
    assert not wt.exists()
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/scratch/baseline"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode != 0


def test_unpublished_refuses_commit_not_on_remote(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path)
    (wt / "local.txt").write_text("only here\n", encoding="utf-8")
    _git(["add", "local.txt"], wt)
    _git(["commit", "-m", "local only"], wt)
    tip = _git(["rev-parse", "HEAD"], wt)
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=tip, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "unpushed_commits"
    assert "not reachable from any ref on origin" in res.hold_reason
    assert wt.exists()


def test_unpublished_accepts_tip_pushed_on_own_branch(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path)
    (wt / "pushed.txt").write_text("pushed\n", encoding="utf-8")
    _git(["add", "pushed.txt"], wt)
    _git(["commit", "-m", "pushed"], wt)
    _git(["push", "-q", "origin", "scratch/baseline"], wt)
    tip = _git(["rev-parse", "HEAD"], wt)
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=tip, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "actionable"
    assert res.remote_refs == ("refs/remotes/origin/scratch/baseline",)


def test_unpublished_refuses_deleted_remote_branch_as_proof(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path)
    (wt / "pushed.txt").write_text("pushed\n", encoding="utf-8")
    _git(["add", "pushed.txt"], wt)
    _git(["commit", "-m", "pushed"], wt)
    _git(["push", "-q", "origin", "scratch/baseline"], wt)
    tip = _git(["rev-parse", "HEAD"], wt)
    _git(["fetch", "-q", "origin"], repo)
    origin = tmp_path / "repo-origin.git"
    _git(["update-ref", "-d", "refs/heads/scratch/baseline"], origin)
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=tip, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "unpushed_commits"


def test_unpublished_refuses_tracked_modification(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path)
    (wt / "README.md").write_text("changed\n", encoding="utf-8")
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "dirty"
    assert "tracked files modified or staged: README.md" in res.hold_reason


def test_unpublished_refuses_unknown_untracked_files_and_lists_them(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path)
    (wt / ".tmp").mkdir()
    (wt / ".tmp" / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"spill")
    (wt / ".tmp" / "notes.txt").write_text("keep me\n", encoding="utf-8")
    (wt / "results.json").write_text("{}\n", encoding="utf-8")
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "dirty"
    assert ".tmp/notes.txt" in res.hold_reason
    assert "results.json" in res.hold_reason
    assert "duckdb_temp_storage" not in res.hold_reason
    assert (wt / ".tmp" / "duckdb_temp_storage_DEFAULT-0.tmp").exists()


def test_unpublished_refuses_branch_with_pr(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path)
    evidence = write_canned_evidence(
        tmp_path / "evidence.json",
        [{"number": 7, "state": "open", "base": {"ref": "develop"}, "head": {"sha": head_oid}}],
    )

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "published_branch"
    assert "#7" in res.hold_reason


def test_unpublished_still_requires_expected_head(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path)
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])

    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid="b" * 40, repo_root=repo, evidence_file=evidence, unpublished=True
    )
    assert res.status == "hold"
    assert res.hold_code == "head_mismatch"


def _branch_tip(repo: Path, branch: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def test_keep_branch_removes_clean_worktree_and_keeps_branch(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/keep")
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=head_oid, repo_root=repo, keep_branch=True)
    assert res.status == "actionable"
    assert res.keep_branch is True
    assert [a["action"] for a in res.proposed_actions] == ["worktree_removal"]
    assert wt.exists()

    removed, kept, executed = apply_finish_actions(res, repo)
    assert removed is True
    assert kept == "feat/keep"
    assert [e["action"] for e in executed] == ["worktree_removal", "branch_kept"]
    assert not wt.exists()
    assert _branch_tip(repo, "feat/keep") == head_oid


def test_keep_branch_needs_no_merged_pr(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/keep-nopr")
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])
    res = evaluate_finish_preview(
        target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence, keep_branch=True
    )
    assert res.status == "actionable"


def test_keep_branch_refuses_dirty_worktree(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/keep-dirty")
    (wt / "untracked.txt").write_text("x", encoding="utf-8")
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=head_oid, repo_root=repo, keep_branch=True)
    assert res.status == "hold"
    assert res.hold_code == "dirty"
    assert wt.exists()


def test_keep_branch_refuses_commits_not_on_origin(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path, branch="feat/keep-local")
    (wt / "local.txt").write_text("only here\n", encoding="utf-8")
    _git(["add", "local.txt"], wt)
    _git(["commit", "-m", "local only"], wt)
    tip = _git(["rev-parse", "HEAD"], wt)
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=tip, repo_root=repo, keep_branch=True)
    assert res.status == "hold"
    assert res.hold_code == "unpushed_commits"
    assert wt.exists()
    assert _branch_tip(repo, "feat/keep-local") == tip


def test_keep_branch_refuses_head_mismatch(tmp_path: Path):
    repo, wt, _ = _unpublished_worktree(tmp_path, branch="feat/keep-mismatch")
    res = evaluate_finish_preview(target_path=wt, expected_head_oid="f" * 40, repo_root=repo, keep_branch=True)
    assert res.status == "hold"
    assert "does not match expected" in res.hold_reason.lower()


def test_keep_branch_refuses_locked_worktree(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/keep-locked")
    _git(["worktree", "lock", "--reason", "guard", str(wt)], repo)
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=head_oid, repo_root=repo, keep_branch=True)
    assert res.status == "hold"
    assert res.locked is True


def test_default_mode_unchanged_without_keep_branch(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/default-mode")
    evidence = write_canned_evidence(tmp_path / "evidence.json", [])
    res = evaluate_finish_preview(target_path=wt, expected_head_oid=head_oid, repo_root=repo, evidence_file=evidence)
    assert res.status == "hold"
    assert res.hold_code == "no_pr"
    assert res.keep_branch is False


def test_make_target_keep_branch_applies(tmp_path: Path):
    repo, wt, head_oid = _unpublished_worktree(tmp_path, branch="feat/keep-make")
    env = make_test_env(tmp_path / "home")
    preview = make_target(
        repo, "worktree-finish", f"WORKTREE_PATH={wt}", f"EXPECTED_HEAD_OID={head_oid}", "KEEP_BRANCH=1", env=env
    )
    assert preview.returncode == 0, preview.stderr
    assert "ACTIONABLE" in preview.stdout
    assert wt.exists()
    applied = make_target(
        repo,
        "worktree-finish",
        f"WORKTREE_PATH={wt}",
        f"EXPECTED_HEAD_OID={head_oid}",
        "KEEP_BRANCH=1",
        "APPLY=1",
        env=env,
    )
    assert applied.returncode == 0, applied.stderr
    assert not wt.exists()
    assert _branch_tip(repo, "feat/keep-make") == head_oid
