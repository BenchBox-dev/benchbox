from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform == "win32", reason="branch pruning uses POSIX Git worktree behavior"),
]

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "branch_prune_merged.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_branch_prune_merged", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_branch_prune_merged"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()
IDENTITY = mod.RepositoryIdentity(name_with_owner="BenchBox-dev/BenchBox", node_id="R_test")


@pytest.fixture(autouse=True)
def _no_quota_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "remaining_request_quota", lambda repo_root: None)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _commit(repo: Path, name: str, contents: str) -> str:
    (repo / name).write_text(contents, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "develop")
    _git(path, "config", "user.name", "BenchBox Test")
    _git(path, "config", "user.email", "test@example.com")
    _commit(path, "README.md", "base\n")
    return path


def _add_merged_candidate(repo: Path, branch: str = "fix/candidate") -> tuple[str, str]:
    _git(repo, "switch", "-c", branch)
    head = _commit(repo, "candidate.txt", f"{branch}\n")
    _git(repo, "switch", "develop")
    merge_commit = _commit(repo, "integrated.txt", f"merged {branch}\n")
    _git(repo, "update-ref", "refs/remotes/origin/develop", merge_commit)
    return head, merge_commit


def _pr(
    branch: str,
    head: str,
    merge_commit: str,
    *,
    number: int = 100,
    merged: bool = True,
    base: str = "develop",
) -> dict[str, Any]:
    return {
        "number": number,
        "state": "closed" if merged else "open",
        "merged_at": "2026-08-31T12:00:00Z" if merged else None,
        "merge_commit_sha": merge_commit if merged else None,
        "head": {"ref": branch, "sha": head, "repo": {"node_id": IDENTITY.node_id}},
        "base": {"ref": base, "repo": {"node_id": IDENTITY.node_id}},
    }


def _stub_evidence(
    monkeypatch: pytest.MonkeyPatch,
    prs_by_branch: dict[str, list[dict[str, Any]]],
    historical_heads: dict[int, str | None],
) -> None:
    monkeypatch.setattr(mod, "resolve_repository_identity", lambda repo_root: IDENTITY)
    monkeypatch.setattr(mod, "fetch_target_branch", lambda repo_root: None)
    monkeypatch.setattr(
        mod,
        "list_pull_requests",
        lambda identity, branches, repo_root: {name: prs_by_branch.get(name, []) for name in branches},
    )
    monkeypatch.setattr(
        mod,
        "get_historical_head_at_merge",
        lambda identity, number, merge_commit, repo_root: historical_heads.get(number),
    )


def test_exact_branch_name_survives_tag_collision(repo: Path) -> None:
    head, _ = _add_merged_candidate(repo, "v0.4.0")
    _git(repo, "tag", "v0.4.0", "develop")

    branches = mod.get_local_branches(repo)

    assert branches["v0.4.0"] == head
    assert "heads/v0.4.0" not in branches


def test_dry_run_reports_candidate_without_deleting(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/candidate"
    head, merge_commit = _add_merged_candidate(repo, branch)
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, merge_commit)]}, {100: head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=True, out=output)

    assert result == mod.PruneResult(deleted=0, would_delete=1, kept=0, failed=0)
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")
    assert f"would delete {branch}" in output.getvalue()


def test_complete_historical_evidence_deletes_branch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/candidate"
    head, merge_commit = _add_merged_candidate(repo, branch)
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, merge_commit)]}, {100: head})

    result = mod.prune_merged_branches(repo, dry_run=False, out=io.StringIO())

    assert result == mod.PruneResult(deleted=1, would_delete=0, kept=0, failed=0)
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False) == ""


def test_current_pr_head_cannot_replace_historical_head(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/reused"
    local_head, merge_commit = _add_merged_candidate(repo, branch)
    historical_head = _git(repo, "rev-parse", "develop~1")
    _stub_evidence(monkeypatch, {branch: [_pr(branch, local_head, merge_commit)]}, {100: historical_head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "is not historical merged head" in output.getvalue()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")


def test_unreachable_merge_commit_is_kept(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/unreachable"
    head, _ = _add_merged_candidate(repo, branch)
    unreachable = _git(repo, "commit-tree", "HEAD^{tree}", "-m", "unreachable merge")
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, unreachable)]}, {100: head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "is not reachable from origin/develop" in output.getvalue()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")


def test_latest_open_pr_prevents_historical_merge_deletion(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/reused"
    head, merge_commit = _add_merged_candidate(repo, branch)
    prs = [_pr(branch, head, merge_commit, number=100), _pr(branch, head, merge_commit, number=101, merged=False)]
    _stub_evidence(monkeypatch, {branch: prs}, {100: head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "latest PR #101 is not merged" in output.getvalue()


def test_newer_pr_to_other_base_prevents_historical_merge_deletion(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/reused"
    head, merge_commit = _add_merged_candidate(repo, branch)
    prs = [
        _pr(branch, head, merge_commit, number=100),
        _pr(branch, head, merge_commit, number=101, merged=False, base="release"),
    ]
    _stub_evidence(monkeypatch, {branch: prs}, {100: head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "latest PR #101 targets release" in output.getvalue()


def test_attached_worktree_branch_is_skipped(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/attached"
    head, merge_commit = _add_merged_candidate(repo, branch)
    worktree = tmp_path / "attached"
    _git(repo, "worktree", "add", str(worktree), branch)
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, merge_commit)]}, {100: head})
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert "attached to a worktree" in output.getvalue()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")


def test_deletion_failure_returns_nonzero_and_is_not_counted(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/candidate"
    head, merge_commit = _add_merged_candidate(repo, branch)
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, merge_commit)]}, {100: head})
    monkeypatch.setattr(mod, "_delete_branch", lambda candidate, repo_root: (False, "cannot lock ref"))
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result == mod.PruneResult(deleted=0, would_delete=0, kept=0, failed=1)
    assert result.exit_code == 1
    assert "failed to delete" in output.getvalue()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")


def test_atomic_deletion_refuses_a_changed_oid(repo: Path) -> None:
    branch = "fix/raced-atomic"
    head, merge_commit = _add_merged_candidate(repo, branch)
    candidate = mod.Candidate(branch=branch, local_oid=head, pr_number=100, merge_commit_oid=merge_commit)
    _git(repo, "branch", "-f", branch, "develop")
    changed_oid = _git(repo, "rev-parse", f"refs/heads/{branch}")

    success, detail = mod._delete_branch(candidate, repo)

    assert success is False
    assert "cannot lock ref" in detail
    assert _git(repo, "rev-parse", f"refs/heads/{branch}") == changed_oid


def test_atomic_deletion_restores_branch_attached_during_race(repo: Path, tmp_path: Path) -> None:
    branch = "fix/raced-attached"
    head, merge_commit = _add_merged_candidate(repo, branch)
    candidate = mod.Candidate(branch=branch, local_oid=head, pr_number=100, merge_commit_oid=merge_commit)
    worktree = tmp_path / "raced-attached"
    _git(repo, "worktree", "add", str(worktree), branch)

    success, detail = mod._delete_branch(candidate, repo)

    assert success is False
    assert "atomic deletion was rolled back" in detail
    assert _git(repo, "rev-parse", f"refs/heads/{branch}") == head
    assert _git(worktree, "status", "--short", "--branch").startswith("## fix/raced-attached")


def test_restore_collision_with_different_oid_fails_closed(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = "fix/restore-collision"
    head, merge_commit = _add_merged_candidate(repo, branch)
    candidate = mod.Candidate(branch=branch, local_oid=head, pr_number=100, merge_commit_oid=merge_commit)
    _git(repo, "worktree", "add", str(tmp_path / "restore-collision"), branch)
    replacement_oid = _git(repo, "rev-parse", "develop")
    original_run = mod._run

    def delete_then_recreate(args: list[str], repo_root: Path, *, check: bool = False):
        proc = original_run(args, repo_root, check=check)
        if args[:3] == ["git", "update-ref", "-d"] and proc.returncode == 0:
            original_run(["git", "update-ref", f"refs/heads/{branch}", replacement_oid], repo_root, check=True)
        return proc

    monkeypatch.setattr(mod, "_run", delete_then_recreate)

    with pytest.raises(mod.PruneError, match="current ref is"):
        mod._delete_branch(candidate, repo)

    assert _git(repo, "rev-parse", f"refs/heads/{branch}") == replacement_oid


def test_merge_base_collection_error_fails_closed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod,
        "_run",
        lambda args, repo_root: subprocess.CompletedProcess(args, 128, "", "fatal: bad object"),
    )

    with pytest.raises(mod.PruneError, match="could not verify merge commit"):
        mod.is_merge_commit_reachable("not-a-commit", repo)


def test_branch_change_after_planning_is_kept(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/raced"
    head, merge_commit = _add_merged_candidate(repo, branch)
    candidate = mod.Candidate(branch=branch, local_oid=head, pr_number=100, merge_commit_oid=merge_commit)
    monkeypatch.setattr(mod, "resolve_repository_identity", lambda repo_root: IDENTITY)
    monkeypatch.setattr(mod, "fetch_target_branch", lambda repo_root: None)

    def plan_then_change(repo_root: Path, out):
        _git(repo, "branch", "-f", branch, "develop")
        return [candidate], 0, 0

    monkeypatch.setattr(mod, "_plan_candidates", plan_then_change)
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "branch changed after evidence collection" in output.getvalue()


def test_worktree_attachment_after_planning_is_kept(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = "fix/raced-worktree"
    head, merge_commit = _add_merged_candidate(repo, branch)
    candidate = mod.Candidate(branch=branch, local_oid=head, pr_number=100, merge_commit_oid=merge_commit)
    monkeypatch.setattr(mod, "resolve_repository_identity", lambda repo_root: IDENTITY)
    monkeypatch.setattr(mod, "fetch_target_branch", lambda repo_root: None)

    def plan_then_attach(repo_root: Path, out):
        _git(repo, "worktree", "add", str(tmp_path / "raced-worktree"), branch)
        return [candidate], 0, 0

    monkeypatch.setattr(mod, "_plan_candidates", plan_then_attach)
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.kept == 1
    assert "became attached to a worktree" in output.getvalue()


def _fail_all_github(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def boom(*args: Any, **kwargs: Any) -> Any:
        calls.append("github")
        raise AssertionError("GitHub must not be contacted")

    monkeypatch.setattr(mod, "fetch_target_branch", lambda repo_root: None)
    monkeypatch.setattr(mod, "resolve_repository_identity", boom)
    monkeypatch.setattr(mod, "list_pull_requests", boom)
    monkeypatch.setattr(mod, "get_historical_head_at_merge", boom)
    return calls


def test_branches_provable_from_local_history_need_no_github_requests(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(repo, "switch", "-q", "-c", "fix/contained")
    _commit(repo, "contained.txt", "c\n")
    _git(repo, "switch", "-q", "develop")
    _git(repo, "merge", "-q", "--ff-only", "fix/contained")

    _git(repo, "switch", "-q", "-c", "fix/squashed")
    _commit(repo, "one.txt", "1\n")
    _commit(repo, "two.txt", "2\n")
    _git(repo, "switch", "-q", "develop")
    _git(repo, "merge", "-q", "--squash", "fix/squashed")
    _git(repo, "commit", "-q", "-m", "squash fix/squashed")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")
    calls = _fail_all_github(monkeypatch)
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result == mod.PruneResult(deleted=2, would_delete=0, kept=0, failed=0)
    assert calls == []
    assert _git(repo, "branch", "--list", "fix/*") == ""
    assert "squash-equivalent" in output.getvalue()


def test_unmerged_work_is_not_locally_proven(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "switch", "-q", "-c", "fix/unmerged")
    _commit(repo, "wip.txt", "wip\n")
    _git(repo, "switch", "-q", "develop")
    _commit(repo, "other.txt", "other\n")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")
    _stub_evidence(monkeypatch, {}, {})
    looked_up: list[str] = []

    def listing(identity, branches, repo_root):
        looked_up.extend(branches)
        return {name: [] for name in branches}

    monkeypatch.setattr(mod, "list_pull_requests", listing)

    result = mod.prune_merged_branches(repo, dry_run=False, out=io.StringIO())

    assert result.deleted == 0
    assert looked_up == ["fix/unmerged"]
    assert _git(repo, "show-ref", "--verify", "refs/heads/fix/unmerged")


def test_one_failing_lookup_keeps_other_deletions_and_exits_nonzero(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heads = {}
    prs: dict[str, list[dict[str, Any]]] = {}
    historical: dict[int, str | None] = {}
    for number, name in enumerate(["fix/a", "fix/flaky", "fix/b"], start=200):
        head, merge_commit = _add_merged_candidate(repo, name)
        heads[name] = head
        prs[name] = [_pr(name, head, merge_commit, number=number)]
        historical[number] = head
    _stub_evidence(monkeypatch, prs, historical)

    def flaky(identity, number, merge_commit, repo_root):
        if number == 201:
            raise mod.PruneError("gh api failed: HTTP 502")
        return historical[number]

    monkeypatch.setattr(mod, "get_historical_head_at_merge", flaky)
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result == mod.PruneResult(deleted=2, would_delete=0, kept=0, failed=1)
    assert result.exit_code == 1
    assert _git(repo, "show-ref", "--verify", "refs/heads/fix/flaky")
    assert _git(repo, "branch", "--list", "fix/a", "fix/b") == ""
    assert "fix/flaky not checked" in output.getvalue()


def test_rate_limit_stops_lookups_but_keeps_proven_deletions(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "switch", "-q", "-c", "fix/contained")
    _commit(repo, "contained.txt", "c\n")
    _git(repo, "switch", "-q", "develop")
    _git(repo, "merge", "-q", "--ff-only", "fix/contained")
    prs: dict[str, list[dict[str, Any]]] = {}
    historical: dict[int, str | None] = {}
    for number, name in enumerate(["fix/one", "fix/two"], start=300):
        head, merge_commit = _add_merged_candidate(repo, name)
        prs[name] = [_pr(name, head, merge_commit, number=number)]
        historical[number] = head
    _stub_evidence(monkeypatch, prs, historical)
    seen: list[int] = []

    def limited(identity, number, merge_commit, repo_root):
        seen.append(number)
        raise mod.PruneError("gh api failed: API rate limit exceeded")

    monkeypatch.setattr(mod, "get_historical_head_at_merge", limited)

    result = mod.prune_merged_branches(repo, dry_run=False, out=io.StringIO())

    assert len(seen) == 1
    assert result.deleted == 1
    assert result.failed == 2
    assert result.exit_code == 1
    assert _git(repo, "branch", "--list", "fix/contained") == ""


def test_whitespace_only_difference_is_not_squash_equivalent(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "mod.py").write_text("if x:\n    pass\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "switch", "-q", "-c", "fix/indented")
    (repo / "mod.py").write_text("if x:\n    pass\n        y()\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "indented call")
    _git(repo, "switch", "-q", "develop")
    (repo / "mod.py").write_text("if x:\n    pass\ny()\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "top-level call")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")
    _stub_evidence(monkeypatch, {}, {})

    assert mod.prove_locally(_git(repo, "rev-parse", "fix/indented"), repo) is None
    result = mod.prune_merged_branches(repo, dry_run=False, out=io.StringIO())

    assert result.deleted == 0
    assert _git(repo, "show-ref", "--verify", "refs/heads/fix/indented")


def test_crlf_difference_is_not_squash_equivalent(repo: Path) -> None:
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "switch", "-q", "-c", "fix/crlf")
    (repo / "b.txt").write_bytes(b"b\r\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "crlf")
    _git(repo, "switch", "-q", "develop")
    (repo / "b.txt").write_bytes(b"b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "lf")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")

    assert mod.prove_locally(_git(repo, "rev-parse", "fix/crlf"), repo) is None


def test_different_binary_content_is_not_squash_equivalent(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"\x00base\x00")
    _git(repo, "add", "blob.bin")
    _git(repo, "commit", "-q", "-m", "binary base")
    _git(repo, "switch", "-q", "-c", "fix/binary")
    (repo / "blob.bin").write_bytes(b"\x00branch\x00")
    _git(repo, "commit", "-q", "-am", "branch binary")
    _git(repo, "switch", "-q", "develop")
    (repo / "blob.bin").write_bytes(b"\x00develop\x00")
    _git(repo, "commit", "-q", "-am", "develop binary")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")

    assert mod.prove_locally(_git(repo, "rev-parse", "fix/binary"), repo) is None


def test_non_utf8_upstream_history_does_not_abort_the_run(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "switch", "-q", "-c", "fix/squashed")
    _commit(repo, "one.txt", "1\n")
    _commit(repo, "two.txt", "2\n")
    _git(repo, "switch", "-q", "develop")
    (repo / "latin1.txt").write_bytes(b"ab\xe9 \xff\xfe\n")
    _git(repo, "add", "latin1.txt")
    _git(repo, "commit", "-q", "-m", "non-utf8 file")
    _git(repo, "merge", "-q", "--squash", "fix/squashed")
    _git(repo, "commit", "-q", "-m", "squash")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")
    _fail_all_github(monkeypatch)

    result = mod.prune_merged_branches(repo, dry_run=False, out=io.StringIO())

    assert result == mod.PruneResult(deleted=1, would_delete=0, kept=0, failed=0)


def test_net_empty_branch_is_not_proven_by_an_empty_upstream_commit(repo: Path) -> None:
    _git(repo, "switch", "-q", "-c", "fix/net-empty")
    _commit(repo, "temp.txt", "t\n")
    _git(repo, "rm", "-q", "temp.txt")
    _git(repo, "commit", "-q", "-m", "undo")
    _git(repo, "switch", "-q", "develop")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty upstream")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "develop")

    assert mod.prove_locally(_git(repo, "rev-parse", "fix/net-empty"), repo) is None


def test_insufficient_quota_refuses_remote_lookups(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = "fix/candidate"
    head, merge_commit = _add_merged_candidate(repo, branch)
    _stub_evidence(monkeypatch, {branch: [_pr(branch, head, merge_commit)]}, {100: head})
    monkeypatch.setattr(mod, "remaining_request_quota", lambda repo_root: 1)
    output = io.StringIO()

    result = mod.prune_merged_branches(repo, dry_run=False, out=output)

    assert result.deleted == 0
    assert result.failed == 1
    assert "requests remain" in output.getvalue()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}")


def test_historical_head_requires_timeline_commit_list_and_merge_commit_agreement(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    historical_head = "a" * 40
    merge_commit = "b" * 40

    def matching_json(args: list[str], repo_root: Path):
        endpoint = args[-1]
        if endpoint.endswith("/timeline"):
            return [[{"event": "committed", "sha": historical_head}, {"event": "merged", "commit_id": merge_commit}]]
        if endpoint.endswith("/commits"):
            return [[{"sha": historical_head}]]
        raise AssertionError(args)

    monkeypatch.setattr(mod, "_run_json", matching_json)
    assert mod.get_historical_head_at_merge(IDENTITY, 100, merge_commit, repo) == historical_head

    def mismatching_json(args: list[str], repo_root: Path):
        endpoint = args[-1]
        if endpoint.endswith("/timeline"):
            return [[{"event": "committed", "sha": historical_head}, {"event": "merged", "commit_id": merge_commit}]]
        if endpoint.endswith("/commits"):
            return [[{"sha": "c" * 40}]]
        raise AssertionError(args)

    monkeypatch.setattr(mod, "_run_json", mismatching_json)
    assert mod.get_historical_head_at_merge(IDENTITY, 100, merge_commit, repo) is None


def test_invalid_dry_run_environment_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DRY_RUN", "true")

    assert mod.main([]) == 2
    assert "DRY_RUN must be unset or exactly 1" in capsys.readouterr().err


def test_worktree_audit_uses_exact_branch_name_with_tag_collision(repo: Path) -> None:
    audit_script = _ROOT / "_project" / "scripts" / "worktree_audit.py"
    spec = importlib.util.spec_from_file_location("_worktree_audit_exact_ref", audit_script)
    assert spec is not None and spec.loader is not None
    audit_mod = importlib.util.module_from_spec(spec)
    sys.modules["_worktree_audit_exact_ref"] = audit_mod
    spec.loader.exec_module(audit_mod)

    _add_merged_candidate(repo, "v0.4.0")
    _git(repo, "tag", "v0.4.0", "develop")

    names = {branch["name"] for branch in audit_mod.get_local_branches(repo)}
    assert "v0.4.0" in names
    assert "heads/v0.4.0" not in names
