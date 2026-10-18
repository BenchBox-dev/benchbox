#!/usr/bin/env python3

from __future__ import annotations

import argparse
import dataclasses
import functools
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from _project.scripts.worktree_audit import (
    _parse_worktree_porcelain_entries,
    fetch_prs_for_branches,
    get_primary_clone_path,
    get_remote_repository_slug,
    resolve_github_token,
    resolve_repository_identity,
)
from scripts.worktree_finish import (
    FinishPreviewResult,
    PrLookup,
    TargetRefresher,
    apply_finish_actions,
    evaluate_finish_preview,
    refresh_target_ref,
)

REMOVE = "remove"
KEEP = "keep"
AMBIGUOUS = "ambiguous"
VERDICTS = (REMOVE, KEEP, AMBIGUOUS)
KEEP_HOLD_CODES = frozenset({"locked", "dirty", "no_pr", "open_pr", "unintegrated_commits"})


class ReapError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ReapVerdict:
    verdict: str
    path: str
    branch: Optional[str]
    head: Optional[str]
    reason: str


@dataclasses.dataclass(frozen=True)
class RemovalOutcome:
    status: str
    path: str
    branch: Optional[str]
    detail: str


def _run_git(args: List[str], cwd: Path) -> Tuple[int, str, str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def resolve_current_worktree(cwd: Path) -> Path:
    code, top, stderr = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if code != 0 or not top:
        raise ReapError(f"Not inside a Git worktree: {stderr or cwd}")
    return Path(top).resolve()


def list_worktree_entries(repo_root: Path) -> List[Dict[str, Any]]:
    code, stdout, stderr = _run_git(["worktree", "list", "--porcelain"], repo_root)
    if code != 0:
        raise ReapError(f"Failed to list git worktrees: {stderr or f'exit {code}'}")
    return _parse_worktree_porcelain_entries(stdout)


def build_batch_pr_lookup(repo_root: Path, repo_slug: str, branches: Set[str]) -> PrLookup:
    if "/" not in repo_slug:
        raise ReapError(f"Invalid repository slug: '{repo_slug}'")
    owner, repo_name = repo_slug.split("/", 1)
    token = resolve_github_token()
    remote_slug = get_remote_repository_slug(repo_root)
    repo_id, _, id_err = resolve_repository_identity(owner, repo_name, token, remote_slug=remote_slug)
    if id_err:
        error = f"Failed to resolve repository identity for PR verification: {id_err}"
        return lambda branch: ([], error)
    prs_by_branch, list_err = fetch_prs_for_branches(owner, repo_name, branches, repo_id, token)
    if list_err:
        error = f"GitHub PR listing failed: {list_err}"
        return lambda branch: ([], error)
    return lambda branch: (prs_by_branch.get(branch, []), None)


def once_per_base(repo_root: Path) -> TargetRefresher:
    return functools.cache(lambda base: refresh_target_ref(base, repo_root))


def merged_head_mismatch(res: FinishPreviewResult) -> Optional[str]:
    if res.merged_head and res.current_head == res.merged_head:
        return None
    return (
        f"HEAD {(res.current_head or 'unknown')[:12]} differs from merged PR #{res.pr_number} head "
        f"{(res.merged_head or 'unknown')[:12]}; possible reuse of branch name"
    )


def classify_worktree(
    entry: Dict[str, Any],
    repo_root: Path,
    primary_clone: Optional[Path],
    current_worktree: Optional[Path],
    repo_slug: str,
    pr_lookup: Optional[PrLookup],
    refresh_target: Optional[TargetRefresher] = None,
) -> ReapVerdict:
    path = Path(entry["worktree"]).resolve()
    branch = None if entry.get("detached") else entry.get("branch")
    head = str(entry.get("HEAD") or "").lower() or None

    def verdict(kind: str, reason: str) -> ReapVerdict:
        return ReapVerdict(verdict=kind, path=str(path), branch=branch, head=head, reason=reason)

    if primary_clone is not None and path == primary_clone:
        return verdict(KEEP, "primary clone is never removed")
    if current_worktree is not None and path == current_worktree:
        return verdict(KEEP, "current worktree is never removed")

    try:
        res = evaluate_finish_preview(
            target_path=path,
            expected_head_oid=head or "",
            repo_root=repo_root,
            repo_slug=repo_slug,
            pr_lookup=pr_lookup,
            refresh_target=refresh_target,
        )
    except Exception as err:
        return verdict(AMBIGUOUS, f"finish evaluation failed: {err}")

    if res.status == "actionable":
        mismatch = merged_head_mismatch(res)
        if mismatch:
            return verdict(KEEP, mismatch)
        return verdict(
            REMOVE,
            f"merged via PR #{res.pr_number} into '{res.pr_base}'; HEAD is the merged head, clean, nothing unpushed",
        )
    reason = res.hold_reason or "finish evaluation held without a reason"
    if res.hold_code in KEEP_HOLD_CODES:
        return verdict(KEEP, reason)
    return verdict(AMBIGUOUS, reason)


def classify_worktrees(
    entries: Iterable[Dict[str, Any]],
    repo_root: Path,
    current_worktree: Optional[Path],
    repo_slug: str,
    pr_lookup: Optional[PrLookup],
) -> Iterator[ReapVerdict]:
    primary_clone = get_primary_clone_path(repo_root)
    refresh_target = once_per_base(repo_root)
    for entry in entries:
        yield classify_worktree(entry, repo_root, primary_clone, current_worktree, repo_slug, pr_lookup, refresh_target)


def remove_worktree(
    candidate: ReapVerdict,
    repo_root: Path,
    repo_slug: str,
    pr_lookup: Optional[PrLookup] = None,
    refresh_target: Optional[TargetRefresher] = None,
) -> RemovalOutcome:
    def outcome(status: str, detail: str) -> RemovalOutcome:
        return RemovalOutcome(status=status, path=candidate.path, branch=candidate.branch, detail=detail)

    if candidate.verdict != REMOVE or not candidate.head:
        return outcome("failed", f"verdict is '{candidate.verdict}'; only '{REMOVE}' verdicts are removed")
    try:
        res = evaluate_finish_preview(
            target_path=Path(candidate.path),
            expected_head_oid=candidate.head,
            repo_root=repo_root,
            repo_slug=repo_slug,
            pr_lookup=pr_lookup,
            refresh_target=refresh_target,
        )
        if res.status != "actionable":
            return outcome("failed", f"re-evaluation held: {res.hold_reason}")
        mismatch = merged_head_mismatch(res)
        if mismatch:
            return outcome("failed", f"re-evaluation held: {mismatch}")
        worktree_removed, branch_deleted, executed = apply_finish_actions(res, repo_root)
    except Exception as err:
        return outcome("failed", f"removal raised: {err}")

    last_outcome = executed[-1]["outcome"] if executed else "no action executed"
    if branch_deleted:
        return outcome("removed", "worktree and local branch removed")
    if worktree_removed:
        return outcome("partial", f"worktree removed but branch ref preserved: {last_outcome}")
    return outcome("failed", last_outcome)


def remove_worktrees(
    verdicts: Iterable[ReapVerdict],
    repo_root: Path,
    repo_slug: str,
    pr_lookup: Optional[PrLookup] = None,
) -> Iterator[RemovalOutcome]:
    refresh_target = once_per_base(repo_root)
    for candidate in verdicts:
        if candidate.verdict == REMOVE:
            yield remove_worktree(candidate, repo_root, repo_slug, pr_lookup, refresh_target)


def format_verdict(v: ReapVerdict) -> str:
    return f"{v.verdict:<9} {v.path}  {v.branch or '(detached)'}  {v.reason}"


def format_outcome(o: RemovalOutcome) -> str:
    return f"{o.status:<9} {o.path}  {o.branch or '(detached)'}  {o.detail}"


def format_summary(verdicts: List[ReapVerdict]) -> str:
    counts = Counter(v.verdict for v in verdicts)
    tallies = " ".join(f"{kind}={counts.get(kind, 0)}" for kind in VERDICTS)
    return f"Summary: {len(verdicts)} worktrees: {tallies}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify every registered worktree as remove, keep, or ambiguous; optionally remove the merged ones."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Re-evaluate each 'remove' verdict with worktree-finish checks and remove it. Dry run is the default.",
    )
    parser.add_argument(
        "--repo",
        default="BenchBox-dev/BenchBox",
        help="GitHub repository slug (default: BenchBox-dev/BenchBox).",
    )
    args = parser.parse_args(argv)

    try:
        current_worktree = resolve_current_worktree(Path.cwd())
        repo_root = get_primary_clone_path(current_worktree) or current_worktree
        entries = list_worktree_entries(repo_root)
        branches = {entry["branch"] for entry in entries if entry.get("branch") and not entry.get("detached")}
        pr_lookup = build_batch_pr_lookup(repo_root, args.repo, branches)
    except ReapError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    verdicts: List[ReapVerdict] = []
    for verdict in classify_worktrees(entries, repo_root, current_worktree, args.repo, pr_lookup):
        print(format_verdict(verdict), flush=True)
        verdicts.append(verdict)
    print(format_summary(verdicts))

    if not args.apply:
        print(
            "Dry run: no worktrees or branches changed; only remote-tracking refs for target branches were refreshed. "
            "Re-run with APPLY=1 to remove the 'remove' verdicts."
        )
        return 0

    outcomes: List[RemovalOutcome] = []
    for result in remove_worktrees(verdicts, repo_root, args.repo):
        print(format_outcome(result), flush=True)
        outcomes.append(result)
    removed = sum(1 for o in outcomes if o.status == "removed")
    print(f"Applied: removed={removed} not_removed={len(outcomes) - removed}")
    return 0 if removed == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
