from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import scripts.worktree_finish as finish
import scripts.worktree_reap as reap
from scripts.worktree_lifecycle_metadata import init_metadata
from scripts.worktree_reap import (
    AMBIGUOUS,
    KEEP,
    REMOVE,
    ReapError,
    ReapVerdict,
    build_batch_pr_lookup,
    classify_worktrees,
    format_summary,
    list_worktree_entries,
    remove_worktree,
    remove_worktrees,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform == "win32", reason="worktree tooling is POSIX-only"),
]

SLUG = "owner/repo"


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    _git(["add", "README.md"], path)
    _git(["commit", "-q", "-m", "initial commit"], path)
    _git(["branch", "-M", "develop"], path)
    origin = path.parent / f"{path.name}-origin.git"
    _git(["clone", "--bare", "-q", str(path), str(origin)], path.parent)
    _git(["remote", "add", "origin", str(origin)], path)
    _git(["fetch", "-q", "origin"], path)
    return path


def _feature_worktree(repo: Path, name: str, *, managed: bool = True) -> tuple[Path, str]:
    wt = repo.parent / f"wt-{name}"
    branch = f"feat/{name}"
    base_oid = _git(["rev-parse", "develop"], repo)
    _git(["worktree", "add", "-q", "-b", branch, str(wt), "develop"], repo)
    _git(["config", "user.email", "test@example.com"], wt)
    _git(["config", "user.name", "BenchBox Test"], wt)
    (wt / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(["add", f"{name}.txt"], wt)
    _git(["commit", "-q", "-m", f"add {name}"], wt)
    if managed:
        init_metadata(wt, branch=branch, base_ref="origin/develop", base_oid=base_oid)
    return wt, branch


def _merge(repo: Path, branch: str, number: int) -> dict[str, Any]:
    tip = _git(["rev-parse", f"refs/heads/{branch}"], repo)
    _git(["merge", "-q", "--no-ff", "-m", f"Merge PR #{number}", branch], repo)
    merge_oid = _git(["rev-parse", "HEAD"], repo)
    _git(["push", "-q", "origin", "develop"], repo)
    return {
        "number": number,
        "state": "closed",
        "merged_at": "2026-10-01T00:00:00Z",
        "base": {"ref": "develop"},
        "head": {"sha": tip},
        "merge_commit_sha": merge_oid,
        "_historical_head_sha": tip,
    }


def _lookup(prs: dict[str, list[dict[str, Any]]]):
    return lambda branch: (prs.get(branch, []), None)


def _verdicts(repo: Path, lookup, current: Path | None = None) -> dict[Path, ReapVerdict]:
    entries = list_worktree_entries(repo)
    return {Path(v.path): v for v in classify_worktrees(entries, repo, current, SLUG, lookup)}


def _state(repo: Path) -> tuple[str, str, list[str]]:
    worktrees = _git(["worktree", "list", "--porcelain"], repo)
    paths = [line.split(" ", 1)[1] for line in worktrees.splitlines() if line.startswith("worktree ")]
    statuses = [_git(["status", "--porcelain", "--untracked-files=all"], Path(p)) for p in paths]
    return worktrees, _git(["for-each-ref", "refs/heads"], repo), statuses


def _branch_exists(repo: Path, branch: str) -> bool:
    proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    return proc.returncode == 0


def test_dry_run_reports_every_worktree_with_a_verdict_and_changes_nothing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prs: dict[str, list[dict[str, Any]]] = {}

    merged, merged_branch = _feature_worktree(repo, "merged")
    prs[merged_branch] = [_merge(repo, merged_branch, 1)]

    untracked, untracked_branch = _feature_worktree(repo, "untracked")
    prs[untracked_branch] = [_merge(repo, untracked_branch, 2)]
    (untracked / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    modified, modified_branch = _feature_worktree(repo, "modified")
    prs[modified_branch] = [_merge(repo, modified_branch, 3)]
    (modified / "modified.txt").write_text("edited\n", encoding="utf-8")

    unpushed, unpushed_branch = _feature_worktree(repo, "unpushed")
    prs[unpushed_branch] = [_merge(repo, unpushed_branch, 4)]
    (unpushed / "later.txt").write_text("later\n", encoding="utf-8")
    _git(["add", "later.txt"], unpushed)
    _git(["commit", "-q", "-m", "post-merge work"], unpushed)

    locked, locked_branch = _feature_worktree(repo, "locked")
    prs[locked_branch] = [_merge(repo, locked_branch, 5)]
    _git(["worktree", "lock", "--reason", "mount guard active", str(locked)], repo)

    current, current_branch = _feature_worktree(repo, "current")
    prs[current_branch] = [_merge(repo, current_branch, 6)]

    active, _ = _feature_worktree(repo, "active")

    open_pr, open_branch = _feature_worktree(repo, "open")
    prs[open_branch] = [{"number": 7, "state": "open", "merged_at": None, "base": {"ref": "develop"}}]

    abandoned, abandoned_branch = _feature_worktree(repo, "abandoned")
    prs[abandoned_branch] = [{"number": 8, "state": "closed", "merged_at": None, "base": {"ref": "develop"}}]

    legacy, legacy_branch = _feature_worktree(repo, "legacy", managed=False)
    prs[legacy_branch] = [_merge(repo, legacy_branch, 9)]

    detached = tmp_path / "wt-detached"
    _git(["worktree", "add", "-q", "--detach", str(detached), "develop"], repo)

    before = _state(repo)
    verdicts = _verdicts(repo, _lookup(prs), current=current.resolve())
    assert _state(repo) == before

    expected = {
        repo: (KEEP, "primary clone"),
        merged: (REMOVE, "merged via PR #1"),
        untracked: (KEEP, "untracked"),
        modified: (KEEP, "uncommitted"),
        unpushed: (KEEP, "unintegrated post-merge commits"),
        locked: (KEEP, "locked"),
        current: (KEEP, "current worktree"),
        active: (KEEP, "No PRs found"),
        open_pr: (KEEP, "No merged PR"),
        abandoned: (AMBIGUOUS, "No merged PR"),
        legacy: (AMBIGUOUS, "legacy"),
        detached: (AMBIGUOUS, "detached"),
    }
    assert set(verdicts) == {path.resolve() for path in expected}
    for path, (kind, reason) in expected.items():
        verdict = verdicts[path.resolve()]
        assert verdict.verdict == kind, (path, verdict.reason)
        assert reason in verdict.reason, (path, verdict.reason)
    assert verdicts[merged.resolve()].branch == merged_branch
    assert format_summary(list(verdicts.values())) == "Summary: 12 worktrees: remove=1 keep=8 ambiguous=3"


def test_undecidable_pr_evidence_is_ambiguous(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    failing, failing_branch = _feature_worktree(repo, "lookup-fails")
    raising, raising_branch = _feature_worktree(repo, "lookup-raises")
    for branch, number in ((failing_branch, 1), (raising_branch, 2)):
        _merge(repo, branch, number)

    def lookup(branch: str):
        if branch == raising_branch:
            raise RuntimeError("gh exploded")
        return [], "GitHub PR listing failed: HTTP Error 502"

    verdicts = _verdicts(repo, lookup)

    assert verdicts[failing.resolve()].verdict == AMBIGUOUS
    assert "HTTP Error 502" in verdicts[failing.resolve()].reason
    assert verdicts[raising.resolve()].verdict == AMBIGUOUS
    assert "gh exploded" in verdicts[raising.resolve()].reason


def test_apply_removes_only_remove_verdicts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prs: dict[str, list[dict[str, Any]]] = {}
    first, first_branch = _feature_worktree(repo, "first")
    prs[first_branch] = [_merge(repo, first_branch, 1)]
    second, second_branch = _feature_worktree(repo, "second")
    prs[second_branch] = [_merge(repo, second_branch, 2)]
    dirty, dirty_branch = _feature_worktree(repo, "dirty")
    prs[dirty_branch] = [_merge(repo, dirty_branch, 3)]
    (dirty / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    legacy, legacy_branch = _feature_worktree(repo, "legacy", managed=False)
    prs[legacy_branch] = [_merge(repo, legacy_branch, 4)]
    lookup = _lookup(prs)

    verdicts = list(_verdicts(repo, lookup).values())
    outcomes = list(remove_worktrees(verdicts, repo, SLUG, lookup))

    assert sorted((Path(o.path), o.status) for o in outcomes) == sorted(
        [(first.resolve(), "removed"), (second.resolve(), "removed")]
    )
    assert not first.exists() and not _branch_exists(repo, first_branch)
    assert not second.exists() and not _branch_exists(repo, second_branch)
    assert (dirty / "scratch.txt").exists() and _branch_exists(repo, dirty_branch)
    assert legacy.exists() and _branch_exists(repo, legacy_branch)
    assert repo.exists()


def test_remove_refuses_a_verdict_other_than_remove(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt, branch = _feature_worktree(repo, "kept")
    prs = {branch: [_merge(repo, branch, 1)]}
    head = _git(["rev-parse", "HEAD"], wt)
    kept = ReapVerdict(verdict=KEEP, path=str(wt.resolve()), branch=branch, head=head, reason="owner kept it")

    outcome = remove_worktree(kept, repo, SLUG, _lookup(prs))

    assert outcome.status == "failed"
    assert wt.exists() and _branch_exists(repo, branch)


def test_apply_reevaluates_and_continues_past_a_candidate_that_changed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prs: dict[str, list[dict[str, Any]]] = {}
    dirtied, dirtied_branch = _feature_worktree(repo, "dirtied")
    prs[dirtied_branch] = [_merge(repo, dirtied_branch, 1)]
    moved, moved_branch = _feature_worktree(repo, "moved")
    prs[moved_branch] = [_merge(repo, moved_branch, 2)]
    clean, clean_branch = _feature_worktree(repo, "clean")
    prs[clean_branch] = [_merge(repo, clean_branch, 3)]
    lookup = _lookup(prs)

    verdicts = _verdicts(repo, lookup)
    assert {verdicts[p.resolve()].verdict for p in (dirtied, moved, clean)} == {REMOVE}

    (dirtied / "new-work.txt").write_text("unsaved\n", encoding="utf-8")
    (moved / "more.txt").write_text("more\n", encoding="utf-8")
    _git(["add", "more.txt"], moved)
    _git(["commit", "-q", "-m", "keep working"], moved)

    outcomes = {Path(o.path): o for o in remove_worktrees(verdicts.values(), repo, SLUG, lookup)}

    assert outcomes[dirtied.resolve()].status == "failed"
    assert "re-evaluation held" in outcomes[dirtied.resolve()].detail
    assert (dirtied / "new-work.txt").exists() and _branch_exists(repo, dirtied_branch)
    assert outcomes[moved.resolve()].status == "failed"
    assert (moved / "more.txt").exists() and _branch_exists(repo, moved_branch)
    assert outcomes[clean.resolve()].status == "removed"
    assert not clean.exists() and not _branch_exists(repo, clean_branch)


def test_reused_branch_name_at_develop_tip_is_not_removed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    old, branch = _feature_worktree(repo, "reused")
    old_pr = _merge(repo, branch, 1)
    _git(["worktree", "remove", str(old)], repo)
    _git(["branch", "-D", branch], repo)

    fresh = tmp_path / "wt-reused-again"
    _git(["worktree", "add", "-q", "-b", branch, str(fresh), "develop"], repo)
    init_metadata(fresh, branch=branch, base_ref="origin/develop", base_oid=_git(["rev-parse", "develop"], repo))
    lookup = _lookup({branch: [old_pr]})

    verdict = _verdicts(repo, lookup)[fresh.resolve()]
    assert verdict.verdict == KEEP
    assert "possible reuse of branch name" in verdict.reason

    forced = ReapVerdict(verdict=REMOVE, path=verdict.path, branch=branch, head=verdict.head, reason="forced")
    outcome = remove_worktree(forced, repo, SLUG, lookup)
    assert outcome.status == "failed"
    assert "possible reuse of branch name" in outcome.detail
    assert fresh.exists() and _branch_exists(repo, branch)


def test_merged_branch_with_another_open_pr_is_kept(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt, branch = _feature_worktree(repo, "reopened")
    open_pr = {"number": 9, "state": "open", "merged_at": None, "base": {"ref": "develop"}}
    lookup = _lookup({branch: [_merge(repo, branch, 1), open_pr]})

    verdict = _verdicts(repo, lookup)[wt.resolve()]
    assert verdict.verdict == KEEP
    assert "open PR #9" in verdict.reason

    forced = ReapVerdict(verdict=REMOVE, path=verdict.path, branch=branch, head=verdict.head, reason="forced")
    outcome = remove_worktree(forced, repo, SLUG, lookup)
    assert outcome.status == "failed"
    assert "open PR #9" in outcome.detail
    assert wt.exists() and _branch_exists(repo, branch)


def test_each_target_branch_is_refreshed_once_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    prs: dict[str, list[dict[str, Any]]] = {}
    for number, name in enumerate(("one", "two", "three"), start=1):
        _, branch = _feature_worktree(repo, name)
        prs[branch] = [_merge(repo, branch, number)]
    refreshed: list[str] = []
    real_refresh = reap.refresh_target_ref

    def counting_refresh(base: str, root: Path) -> str | None:
        refreshed.append(base)
        return real_refresh(base, root)

    monkeypatch.setattr(reap, "refresh_target_ref", counting_refresh)
    verdicts = _verdicts(repo, _lookup(prs))

    assert [v.verdict for v in verdicts.values()].count(REMOVE) == 3
    assert refreshed == ["develop"]


def _cli_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_prs: bool) -> tuple[Path, Path, str, dict]:
    repo = _init_repo(tmp_path / "repo")
    wt, branch = _feature_worktree(repo, "cli")
    prs = {branch: [_merge(repo, branch, 1)]}
    monkeypatch.chdir(repo)
    monkeypatch.setattr(reap, "build_batch_pr_lookup", lambda root, slug, branches: _lookup(prs))
    monkeypatch.setattr(finish, "resolve_github_token", lambda: None)
    monkeypatch.setattr(finish, "get_remote_repository_slug", lambda root: None)
    monkeypatch.setattr(finish, "resolve_repository_identity", lambda *a, **k: (1, SLUG, None))
    live = prs if live_prs else {}
    monkeypatch.setattr(finish, "fetch_prs_for_branch", lambda owner, name, b, rid, tok: (live.get(b, []), None))
    return repo, wt, branch, prs


def test_cli_dry_run_leaves_the_candidate_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, wt, branch, _ = _cli_repo(tmp_path, monkeypatch, live_prs=True)

    assert reap.main(["--repo", SLUG]) == 0

    out = capsys.readouterr().out
    assert f"remove    {wt.resolve()}  {branch}  merged via PR #1" in out
    assert "Summary: 2 worktrees: remove=1 keep=1 ambiguous=0" in out
    assert wt.exists() and _branch_exists(repo, branch)


def test_cli_apply_removes_the_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, wt, branch, _ = _cli_repo(tmp_path, monkeypatch, live_prs=True)

    assert reap.main(["--apply", "--repo", SLUG]) == 0

    out = capsys.readouterr().out
    assert f"removed   {wt.resolve()}  {branch}" in out
    assert "Applied: removed=1 not_removed=0" in out
    assert not wt.exists() and not _branch_exists(repo, branch)


def test_cli_apply_returns_nonzero_when_reevaluation_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, wt, branch, _ = _cli_repo(tmp_path, monkeypatch, live_prs=False)

    assert reap.main(["--apply", "--repo", SLUG]) == 1

    out = capsys.readouterr().out
    assert f"failed    {wt.resolve()}  {branch}  re-evaluation held: No PRs found" in out
    assert wt.exists() and _branch_exists(repo, branch)


def test_cli_lookup_error_is_ambiguous_and_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, wt, branch, _ = _cli_repo(tmp_path, monkeypatch, live_prs=True)
    monkeypatch.setattr(
        reap, "build_batch_pr_lookup", lambda root, slug, branches: lambda b: ([], "GitHub PR listing failed: 502")
    )

    assert reap.main(["--apply", "--repo", SLUG]) == 0

    out = capsys.readouterr().out
    assert f"ambiguous {wt.resolve()}  {branch}  GitHub PR listing failed: 502" in out
    assert "Applied: removed=0 not_removed=0" in out
    assert wt.exists() and _branch_exists(repo, branch)


def test_batch_lookup_reports_identity_and_listing_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setattr(reap, "resolve_github_token", lambda: None)
    monkeypatch.setattr(reap, "resolve_repository_identity", lambda *a, **k: (None, None, "HTTP 404 Not Found"))
    lookup = build_batch_pr_lookup(repo, SLUG, {"feat/a"})
    assert lookup("feat/a") == ([], "Failed to resolve repository identity for PR verification: HTTP 404 Not Found")

    monkeypatch.setattr(reap, "resolve_repository_identity", lambda *a, **k: (1, SLUG, None))
    monkeypatch.setattr(reap, "fetch_prs_for_branches", lambda *a: ({}, "PR listing pagination truncated"))
    lookup = build_batch_pr_lookup(repo, SLUG, {"feat/a"})
    assert lookup("feat/a") == ([], "GitHub PR listing failed: PR listing pagination truncated")

    listed = {"feat/a": [{"number": 7}], "feat/b": []}
    monkeypatch.setattr(reap, "fetch_prs_for_branches", lambda *a: (listed, None))
    lookup = build_batch_pr_lookup(repo, SLUG, {"feat/a", "feat/b"})
    assert lookup("feat/a") == ([{"number": 7}], None)
    assert lookup("feat/b") == ([], None)

    with pytest.raises(ReapError, match="Invalid repository slug"):
        build_batch_pr_lookup(repo, "no-slash", {"feat/a"})
