from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="make worktree-* targets are POSIX shell tooling; not supported on Windows",
    ),
]

_ROOT = Path(__file__).resolve().parents[3]
_AUDIT_SCRIPT = _ROOT / "_project" / "scripts" / "worktree_audit.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("_worktree_audit", _AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_worktree_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


audit_mod = _load_audit_module()


def _git(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    res = subprocess.run(["git", *cmd], cwd=cwd, check=True, text=True, capture_output=True, env=full_env)
    return res.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("initial repo\n", encoding="utf-8")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "initial commit"], path)
    _git(["branch", "-M", "develop"], path)
    return path


def add_worktree(repo: Path, branch: str, wt_path: Path) -> Path:
    _git(["worktree", "add", "-b", branch, str(wt_path), "develop"], repo)
    return wt_path


@pytest.fixture(autouse=True)
def mock_github_api_offline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        audit_mod,
        "resolve_repository_identity",
        lambda owner, repo, token, *args, **kwargs: (123456789, f"{owner}/{repo}", None),
    )
    monkeypatch.setattr(
        audit_mod,
        "fetch_prs_for_branch",
        lambda owner, repo, branch, repo_id, token, *args, **kwargs: ([], None),
    )


def test_fixture_clean_active_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    add_worktree(repo, "fix/feature-a", tmp_path / "wt_clean")

    report = audit_mod.audit_worktrees(repo_root=repo)
    assert report["schema_version"] == 1
    assert report["summary"]["finish_candidates"] == 0

    records = [r for r in report["worktrees"] if r["worktree"]["branch"] == "fix/feature-a"]
    assert len(records) == 1
    rec = records[0]
    assert rec["classification"]["state"] == "uncertain"
    assert rec["classification"]["item_classification"] == "active"
    assert "clean" in rec["classification"]["signals"]


def test_fixture_dirty_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-dirty", tmp_path / "wt_dirty")
    (wt / "uncommitted.txt").write_text("modified", encoding="utf-8")

    report = audit_mod.audit_worktrees(repo_root=repo)
    records = [r for r in report["worktrees"] if r["worktree"]["branch"] == "fix/feature-dirty"]
    assert len(records) == 1
    rec = records[0]
    assert rec["classification"]["state"] == "uncertain"
    assert rec["classification"]["item_classification"] == "dirty"
    assert rec["worktree"]["is_dirty"] is True


def test_fixture_locked_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-locked", tmp_path / "wt_locked")
    _git(["worktree", "lock", "--reason", "agent test guard", str(wt)], repo)

    report = audit_mod.audit_worktrees(repo_root=repo)
    records = [r for r in report["worktrees"] if r["worktree"]["branch"] == "fix/feature-locked"]
    assert len(records) == 1
    rec = records[0]
    assert rec["classification"]["state"] == "uncertain"
    assert rec["classification"]["item_classification"] == "locked"
    assert rec["worktree"]["is_locked"] is True
    assert rec["worktree"]["lock_reason"] == "agent test guard"


def test_fixture_detached_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-detached", tmp_path / "wt_detached")
    _git(["switch", "--detach"], wt)

    report = audit_mod.audit_worktrees(repo_root=repo)
    records = [r for r in report["worktrees"] if r["worktree"]["path"] == str(wt)]
    assert len(records) == 1
    rec = records[0]
    assert rec["classification"]["state"] == "uncertain"
    assert rec["classification"]["item_classification"] == "detached"
    assert rec["worktree"]["is_detached"] is True


def test_fixture_gone_upstream_squash_merged(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-squash", tmp_path / "wt_squash")

    (wt / "change.txt").write_text("feature change\n", encoding="utf-8")
    _git(["add", "change.txt"], wt)
    _git(["commit", "-m", "feature change"], wt)
    pr_head_sha = _git(["rev-parse", "HEAD"], wt)

    (repo / "change.txt").write_text("feature change\n", encoding="utf-8")
    _git(["add", "change.txt"], repo)
    _git(["commit", "-m", "Squash merge PR #1914 (#1914)"], repo)
    merge_commit_sha = _git(["rev-parse", "HEAD"], repo)

    canned_prs = [
        {
            "number": 1914,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-26T12:00:00Z",
            "merge_commit_sha": merge_commit_sha,
            "head": {"sha": pr_head_sha},
            "base": {"ref": "develop"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-squash"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )

    assert cls.state == "verified-integrated"
    assert cls.item_classification == "finish candidate"
    assert len(evidence) == 1
    assert evidence[0].pr_merged is True
    assert evidence[0].merge_commit_reachable_from_target_tip is True


def test_fixture_post_merge_commits_unintegrated(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-post-merge", tmp_path / "wt_post")

    (wt / "change1.txt").write_text("change 1\n", encoding="utf-8")
    _git(["add", "change1.txt"], wt)
    _git(["commit", "-m", "feature change 1"], wt)
    pr_head_sha = _git(["rev-parse", "HEAD"], wt)

    (repo / "change1.txt").write_text("change 1\n", encoding="utf-8")
    _git(["add", "change1.txt"], repo)
    _git(["commit", "-m", "Squash merge PR #1920 (#1920)"], repo)
    merge_commit_sha = _git(["rev-parse", "HEAD"], repo)

    (wt / "change2.txt").write_text("unintegrated change 2\n", encoding="utf-8")
    _git(["add", "change2.txt"], wt)
    _git(["commit", "-m", "unintegrated post-merge commit"], wt)

    canned_prs = [
        {
            "number": 1920,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-26T12:00:00Z",
            "merge_commit_sha": merge_commit_sha,
            "head": {"sha": pr_head_sha},
            "base": {"ref": "develop"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-post-merge"][0]

    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )

    assert cls.state == "uncertain"
    assert cls.item_classification == "tip mismatch"
    assert "unintegrated post-merge commits" in cls.reason


def test_fixture_squash_source_tip_not_ancestor(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-ancestry-test", tmp_path / "wt_ancestry")

    (wt / "ancestry.txt").write_text("test\n", encoding="utf-8")
    _git(["add", "ancestry.txt"], wt)
    _git(["commit", "-m", "ancestry test"], wt)
    branch_tip = _git(["rev-parse", "HEAD"], wt)

    (repo / "ancestry.txt").write_text("test\n", encoding="utf-8")
    _git(["add", "ancestry.txt"], repo)
    _git(["commit", "-m", "Squash PR #1925"], repo)
    merge_sha = _git(["rev-parse", "HEAD"], repo)

    is_anc = audit_mod.is_ancestor(branch_tip, merge_sha, repo)
    assert is_anc is False

    canned_prs = [
        {
            "number": 1925,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-26T12:00:00Z",
            "merge_commit_sha": merge_sha,
            "head": {"sha": branch_tip},
            "base": {"ref": "develop"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-ancestry-test"][0]

    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )

    assert cls.state == "verified-integrated"
    assert cls.item_classification == "finish candidate"


def test_make_worktree_audit_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-json", tmp_path / "wt_json")
    monkeypatch.chdir(repo)

    code = audit_mod.main(["--format", "json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, dict)
    assert data["schema_version"] == 1
    assert "worktrees" in data
    assert "report_authority" in data
    assert data["report_authority"]["is_deletion_authority"] is False
    assert "snapshot_path" in data
    paths = [Path(w["worktree"]["path"]).resolve() for w in data["worktrees"]]
    assert paths == [wt.resolve()]


def test_fixture_primary_clone(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-primary-test", tmp_path / "wt_primary")

    (wt / "file.txt").write_text("hello\n", encoding="utf-8")
    _git(["add", "file.txt"], wt)
    _git(["commit", "-m", "commit in linked wt"], wt)

    report_from_primary = audit_mod.audit_worktrees(repo_root=repo)
    paths_from_primary = [w["worktree"]["path"] for w in report_from_primary["worktrees"]]
    assert str(repo.resolve()) not in paths_from_primary
    assert any(wt.resolve() == Path(p).resolve() for p in paths_from_primary)

    report_from_linked = audit_mod.audit_worktrees(repo_root=wt)
    paths_from_linked = [w["worktree"]["path"] for w in report_from_linked["worktrees"]]
    assert str(repo.resolve()) not in paths_from_linked
    assert any(wt.resolve() == Path(p).resolve() for p in paths_from_linked)


def test_fixture_merge_commit_not_yet_reachable(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-unreachable", tmp_path / "wt_unreachable")

    (wt / "work.txt").write_text("work in progress\n", encoding="utf-8")
    _git(["add", "work.txt"], wt)
    _git(["commit", "-m", "work commit"], wt)
    branch_head_sha = _git(["rev-parse", "HEAD"], wt)

    _git(["checkout", "-b", "other-branch"], repo)
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    _git(["add", "other.txt"], repo)
    _git(["commit", "-m", "other commit"], repo)
    unreachable_merge_sha = _git(["rev-parse", "HEAD"], repo)
    _git(["checkout", "develop"], repo)
    _git(["checkout", "fix/feature-unreachable"], wt)

    canned_prs = [
        {
            "number": 2005,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-26T12:00:00Z",
            "merge_commit_sha": unreachable_merge_sha,
            "head": {"sha": branch_head_sha},
            "base": {"ref": "develop"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-unreachable"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )

    assert cls.state == "uncertain"
    assert cls.item_classification == "stale"
    assert "not yet reachable" in cls.reason
    assert len(evidence) == 1
    assert evidence[0].merge_commit_reachable_from_target_tip is False


def test_fixture_gone_upstream_no_pr_evidence(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-q", str(origin)], tmp_path)

    repo = tmp_path / "repo"
    _git(["clone", "-q", str(origin), str(repo)], tmp_path)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "BenchBox Test"], repo)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "initial commit"], repo)
    _git(["branch", "-M", "develop"], repo)
    _git(["push", "-u", "origin", "develop"], repo)

    wt = tmp_path / "wt_gone"
    _git(["worktree", "add", "-b", "fix/feature-gone", str(wt), "develop"], repo)
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(["add", "work.txt"], wt)
    _git(["commit", "-m", "work commit"], wt)
    _git(["push", "-u", "origin", "fix/feature-gone"], wt)

    _git(["push", "origin", "--delete", "fix/feature-gone"], repo)
    _git(["fetch", "--prune", "origin"], repo)

    assert audit_mod.is_branch_gone_upstream("fix/feature-gone", repo) is True

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-gone"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=[],
    )

    assert cls.state == "uncertain"
    assert cls.item_classification == "active"
    assert "gone-upstream" in cls.signals
    assert len(evidence) == 0


def test_fixture_missing_directory(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-missing", tmp_path / "wt_missing")
    import shutil

    shutil.rmtree(wt)

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.path == str(wt)][0]
    assert matched.path_exists is False

    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
    )
    assert cls.state == "unavailable"
    assert cls.item_classification == "missing"


def test_fixture_unregistered_path(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    unregistered_dir = tmp_path / "unregistered_dir"
    unregistered_dir.mkdir()

    wt = audit_mod.WorktreeInfo(
        path=str(unregistered_dir),
        registered=False,
        path_exists=True,
        is_detached=False,
        is_locked=False,
        lock_reason=None,
        is_dirty=False,
        branch="fix/unregistered",
        head_sha="head123",
    )
    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=wt,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
    )
    assert cls.state == "unavailable"
    assert cls.item_classification == "missing"


def test_fixture_prunable_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = audit_mod.WorktreeInfo(
        path=str(tmp_path / "prunable_wt"),
        registered=True,
        path_exists=True,
        is_detached=False,
        is_locked=False,
        lock_reason=None,
        is_dirty=False,
        branch="fix/prunable",
        head_sha="head123",
        prunable=True,
        prunable_reason="gitdir missing",
    )
    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=wt,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
    )
    assert cls.state == "unavailable"
    assert cls.item_classification == "prunable"
    assert "gitdir missing" in cls.reason


def test_fixture_gone_upstream_unmerged_pr(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    add_worktree(repo, "fix/feature-closed-unmerged", tmp_path / "wt_closed")

    canned_prs = [
        {
            "number": 1940,
            "state": "closed",
            "merged": False,
            "merged_at": None,
            "head": {"sha": "head123"},
            "base": {"ref": "develop"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-closed-unmerged"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )
    assert cls.state == "uncertain"
    assert cls.item_classification == "closed-unmerged"


def test_fixture_wrong_base_pr(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-wrong-base", tmp_path / "wt_wrong_base")
    (wt / "change.txt").write_text("change\n", encoding="utf-8")
    _git(["add", "change.txt"], wt)
    _git(["commit", "-m", "change"], wt)
    head_sha = _git(["rev-parse", "HEAD"], wt)

    canned_prs = [
        {
            "number": 1945,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-26T12:00:00Z",
            "merge_commit_sha": "merge1945",
            "head": {"sha": head_sha},
            "base": {"ref": "feature/arbitrary-base"},
        }
    ]

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-wrong-base"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=canned_prs,
    )
    assert cls.state == "uncertain"
    assert cls.item_classification == "wrong base"


def test_fixture_old_but_active_worktree(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = add_worktree(repo, "fix/feature-old", tmp_path / "wt_old")
    (wt / "old.txt").write_text("old work\n", encoding="utf-8")
    _git(["add", "old.txt"], wt)
    _git(
        ["commit", "-m", "old commit", "--date=2026-06-01T00:00:00Z"],
        wt,
        env={"GIT_COMMITTER_DATE": "2026-06-01T00:00:00Z"},
    )

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-old"][0]

    cls, _ = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        canned_prs=[],
    )
    assert cls.state == "uncertain"
    assert cls.item_classification == "active"
    assert "old" in cls.signals
    assert "clean" in cls.signals


def test_fixture_incomplete_collection_error(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    add_worktree(repo, "fix/feature-api-err", tmp_path / "wt_api_err")

    wt_info, _ = audit_mod.get_git_worktrees(repo)
    matched = [w for w in wt_info if w.branch == "fix/feature-api-err"][0]

    cls, evidence = audit_mod.evaluate_and_classify_worktree(
        wt=matched,
        repo_owner="BenchBox-dev",
        repo_name="BenchBox",
        expected_repo_id=None,
        token=None,
        repo_root=repo,
        api_error_override="HTTP Error 503: Service Unavailable",
    )
    assert cls.state == "unavailable"
    assert cls.item_classification == "API error"
    assert "503" in cls.reason
