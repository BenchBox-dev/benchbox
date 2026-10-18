from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.worktree_lifecycle_metadata import init_metadata
from scripts.worktree_reap import (
    AMBIGUOUS,
    KEEP,
    REMOVE,
    ReapVerdict,
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
