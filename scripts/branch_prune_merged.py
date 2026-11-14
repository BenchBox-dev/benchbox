#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

CLI_DESCRIPTION = "Safely prune worktree-less local branches proven merged into develop."

PROTECTED_BRANCHES = frozenset({"develop", "main", "release", "published-results"})
TARGET_BRANCH = "develop"


class PruneError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepositoryIdentity:
    name_with_owner: str
    node_id: str

    @property
    def owner(self) -> str:
        return self.name_with_owner.split("/", 1)[0]


REQUESTS_PER_LOOKUP = 2
ANCESTOR_PROOF = "tip is contained in origin/develop"
SQUASH_PROOF = "squash-equivalent to a commit on origin/develop"


@dataclass(frozen=True)
class Candidate:
    branch: str
    local_oid: str
    pr_number: int | None
    merge_commit_oid: str | None
    proof: str | None = None

    def describe(self) -> str:
        if self.proof:
            return self.proof
        return f"PR #{self.pr_number} merged from historical head {self.local_oid[:8]}"


@dataclass(frozen=True)
class PruneResult:
    deleted: int
    would_delete: int
    kept: int
    failed: int

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def _run(
    args: list[str],
    repo_root: Path,
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise PruneError(f"{' '.join(args)} could not run: {err}") from err
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise PruneError(f"{' '.join(args)} failed: {detail}")
    return proc


def _run_json(args: list[str], repo_root: Path) -> Any:
    proc = _run(args, repo_root, check=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise PruneError(f"{' '.join(args)} returned invalid JSON: {err}") from err


def resolve_repository_identity(repo_root: Path) -> RepositoryIdentity:
    payload = _run_json(["gh", "repo", "view", "--json", "id,nameWithOwner"], repo_root)
    if not isinstance(payload, dict):
        raise PruneError("gh repo view returned an unexpected payload")
    name_with_owner = payload.get("nameWithOwner")
    node_id = payload.get("id")
    if not isinstance(name_with_owner, str) or "/" not in name_with_owner or not isinstance(node_id, str):
        raise PruneError("gh repo view did not return a complete repository identity")
    return RepositoryIdentity(name_with_owner=name_with_owner, node_id=node_id)


def fetch_target_branch(repo_root: Path) -> None:
    _run(
        ["git", "fetch", "--quiet", "origin", f"{TARGET_BRANCH}:refs/remotes/origin/{TARGET_BRANCH}"],
        repo_root,
        check=True,
    )


def get_current_branch(repo_root: Path) -> str | None:
    proc = _run(["git", "symbolic-ref", "--quiet", "HEAD"], repo_root)
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    prefix = "refs/heads/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def get_local_branches(repo_root: Path) -> dict[str, str]:
    proc = _run(
        ["git", "for-each-ref", "--format=%(refname:lstrip=2)\t%(objectname)", "refs/heads/"],
        repo_root,
        check=True,
    )
    branches: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        branch, separator, oid = line.partition("\t")
        if separator and branch and oid:
            branches[branch] = oid
    return branches


def get_worktree_branches(repo_root: Path) -> set[str]:
    proc = _run(["git", "worktree", "list", "--porcelain"], repo_root, check=True)
    prefix = "branch refs/heads/"
    return {line[len(prefix) :] for line in proc.stdout.splitlines() if line.startswith(prefix)}


def list_pull_requests(
    identity: RepositoryIdentity,
    branches: set[str],
    repo_root: Path,
) -> dict[str, list[dict[str, Any]]]:
    payload = _run_json(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "-X",
            "GET",
            f"repos/{identity.name_with_owner}/pulls",
            "-f",
            "state=all",
            "-f",
            "per_page=100",
        ],
        repo_root,
    )
    by_branch: dict[str, list[dict[str, Any]]] = {branch: [] for branch in branches}
    for pr in _flatten_pages(payload, "PR listing"):
        head = pr.get("head")
        base = pr.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise PruneError("PR listing returned a record without head and base")
        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if not isinstance(base_repo, dict) or base_repo.get("node_id") != identity.node_id:
            raise PruneError("PR listing failed exact repository validation")
        ref = head.get("ref")
        if ref not in by_branch or not isinstance(head_repo, dict) or head_repo.get("node_id") != identity.node_id:
            continue
        by_branch[ref].append(pr)
    return by_branch


def _flatten_pages(payload: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise PruneError(f"{description} returned an unexpected payload")
    records: list[dict[str, Any]] = []
    for page in payload:
        if not isinstance(page, list):
            raise PruneError(f"{description} returned an invalid page")
        for record in page:
            if not isinstance(record, dict):
                raise PruneError(f"{description} returned an invalid record")
            records.append(record)
    return records


def get_historical_head_at_merge(
    identity: RepositoryIdentity,
    pr_number: int,
    expected_merge_commit: str,
    repo_root: Path,
) -> str | None:
    timeline_payload = _run_json(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{identity.name_with_owner}/issues/{pr_number}/timeline",
        ],
        repo_root,
    )
    timeline = _flatten_pages(timeline_payload, f"timeline for PR #{pr_number}")

    timeline_head: str | None = None
    merged_event_seen = False
    for event in timeline:
        event_name = event.get("event")
        if event_name == "committed" and isinstance(event.get("sha"), str):
            timeline_head = event["sha"]
        elif event_name == "head_ref_force_pushed" and isinstance(event.get("commit_id"), str):
            timeline_head = event["commit_id"]
        elif event_name == "merged":
            if event.get("commit_id") != expected_merge_commit:
                return None
            merged_event_seen = True
            break

    if not merged_event_seen or not timeline_head:
        return None

    commits_payload = _run_json(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{identity.name_with_owner}/pulls/{pr_number}/commits",
        ],
        repo_root,
    )
    commits = _flatten_pages(commits_payload, f"commit list for PR #{pr_number}")
    final_commit = commits[-1].get("sha") if commits else None
    if not isinstance(final_commit, str) or final_commit != timeline_head:
        return None
    return timeline_head


def is_merge_commit_reachable(merge_commit: str, repo_root: Path) -> bool:
    proc = _run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            merge_commit,
            f"refs/remotes/origin/{TARGET_BRANCH}",
        ],
        repo_root,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    raise PruneError(f"could not verify merge commit {merge_commit}: {detail}")


def _verbatim_patch_ids(args: list[str], repo_root: Path) -> list[tuple[str, str]]:
    try:
        log = subprocess.run(args, cwd=repo_root, capture_output=True, timeout=120)
        if log.returncode != 0:
            return []
        proc = subprocess.run(
            ["git", "patch-id", "--verbatim"],
            cwd=repo_root,
            input=log.stdout,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    pairs: list[tuple[str, str]] = []
    for line in proc.stdout.decode("ascii", errors="replace").splitlines():
        patch_id, _, commit = line.partition(" ")
        if patch_id:
            pairs.append((patch_id, commit))
    return pairs


def prove_locally(branch_oid: str, repo_root: Path) -> str | None:
    target = f"refs/remotes/origin/{TARGET_BRANCH}"
    ancestor = _run(["git", "merge-base", "--is-ancestor", branch_oid, target], repo_root)
    if ancestor.returncode == 0:
        return ANCESTOR_PROOF
    if ancestor.returncode != 1:
        return None

    base = _run(["git", "merge-base", target, branch_oid], repo_root)
    base_oid = base.stdout.strip()
    if base.returncode != 0 or not base_oid:
        return None
    squashed = _run(
        ["git", "commit-tree", f"{branch_oid}^{{tree}}", "-p", base_oid, "-m", "x"],
        repo_root,
    )
    squashed_oid = squashed.stdout.strip()
    if squashed.returncode != 0 or not squashed_oid:
        return None
    cherry = _run(["git", "cherry", target, squashed_oid], repo_root)
    if cherry.returncode != 0 or not any(line.startswith("-") for line in cherry.stdout.splitlines()):
        return None

    branch_ids = _verbatim_patch_ids(["git", "diff", "--binary", "--no-ext-diff", base_oid, branch_oid], repo_root)
    if len(branch_ids) != 1:
        return None
    wanted = branch_ids[0][0]
    upstream_ids = _verbatim_patch_ids(
        ["git", "log", "-p", "--binary", "--no-merges", "--no-ext-diff", "--format=commit %H", f"{base_oid}..{target}"],
        repo_root,
    )
    if any(patch_id == wanted for patch_id, _ in upstream_ids):
        return SQUASH_PROOF
    return None


def remaining_request_quota(repo_root: Path) -> int | None:
    proc = _run(["gh", "api", "rate_limit", "--jq", ".resources.core.remaining"], repo_root)
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _is_rate_limited(err: PruneError) -> bool:
    text = str(err).lower()
    return "rate limit" in text or "secondary rate" in text or "abuse detection" in text


def _latest_pr(prs: list[dict[str, Any]]) -> dict[str, Any] | None:
    numbered = [pr for pr in prs if isinstance(pr.get("number"), int)]
    return max(numbered, key=lambda pr: pr["number"], default=None)


def _remote_candidate(
    identity: RepositoryIdentity,
    branch: str,
    local_oid: str,
    prs: list[dict[str, Any]],
    repo_root: Path,
    out: TextIO,
) -> tuple[Candidate | None, bool]:
    latest = _latest_pr(prs)
    if latest is None:
        return None, False
    latest_base = latest.get("base", {}).get("ref")
    if latest_base != TARGET_BRANCH:
        print(f"keep {branch}: latest PR #{latest['number']} targets {latest_base or 'an unknown base'}", file=out)
        return None, True
    if not latest.get("merged_at") or latest.get("state") != "closed":
        print(f"keep {branch}: latest PR #{latest['number']} is not merged", file=out)
        return None, True

    merge_commit = latest.get("merge_commit_sha")
    if not isinstance(merge_commit, str) or not merge_commit:
        print(f"keep {branch}: PR #{latest['number']} has no merge commit evidence", file=out)
        return None, True

    historical_head = get_historical_head_at_merge(identity, latest["number"], merge_commit, repo_root)
    if not historical_head:
        print(f"keep {branch}: PR #{latest['number']} merge-time head could not be proven", file=out)
        return None, True
    if local_oid != historical_head:
        print(
            f"keep {branch}: local tip {local_oid[:8]} is not historical merged head {historical_head[:8]}",
            file=out,
        )
        return None, True
    if not is_merge_commit_reachable(merge_commit, repo_root):
        print(
            f"keep {branch}: PR #{latest['number']} merge commit {merge_commit[:8]} is not reachable from "
            f"origin/{TARGET_BRANCH}",
            file=out,
        )
        return None, True

    return (
        Candidate(branch=branch, local_oid=local_oid, pr_number=latest["number"], merge_commit_oid=merge_commit),
        False,
    )


def _plan_candidates(repo_root: Path, out: TextIO) -> tuple[list[Candidate], int, int]:
    branches = get_local_branches(repo_root)
    current = get_current_branch(repo_root)
    worktree_branches = get_worktree_branches(repo_root)
    candidates: list[Candidate] = []
    needs_lookup: dict[str, str] = {}
    kept = 0
    failed = 0

    for branch, local_oid in branches.items():
        if branch in PROTECTED_BRANCHES or branch == current:
            continue
        if branch in worktree_branches:
            print(f"skip {branch}: attached to a worktree (use worktree-remove)", file=out)
            continue
        try:
            proof = prove_locally(local_oid, repo_root)
        except PruneError:
            proof = None
        if proof:
            candidates.append(
                Candidate(branch=branch, local_oid=local_oid, pr_number=None, merge_commit_oid=None, proof=proof)
            )
        else:
            needs_lookup[branch] = local_oid

    if not needs_lookup:
        return candidates, kept, failed

    def skip_unchecked(branch_names: list[str], reason: str) -> int:
        for name in branch_names:
            print(f"error: {name} not checked: {reason}", file=out)
        return len(branch_names)

    pending = list(needs_lookup)
    try:
        identity = resolve_repository_identity(repo_root)
        prs_by_branch = list_pull_requests(identity, set(pending), repo_root)
    except PruneError as err:
        failed += skip_unchecked(pending, str(err))
        return candidates, kept, failed

    with_merged = [name for name in pending if any(pr.get("merged_at") for pr in prs_by_branch.get(name, []))]
    quota = remaining_request_quota(repo_root)
    needed = len(with_merged) * REQUESTS_PER_LOOKUP
    if quota is not None and quota < needed:
        failed += skip_unchecked(
            with_merged,
            f"{quota} GitHub requests remain but {needed} may be needed; wait for the rate limit to reset",
        )
        pending = [name for name in pending if name not in with_merged]

    for index, branch in enumerate(pending):
        try:
            candidate, was_kept = _remote_candidate(
                identity, branch, needs_lookup[branch], prs_by_branch.get(branch, []), repo_root, out
            )
        except PruneError as err:
            if _is_rate_limited(err):
                failed += skip_unchecked(pending[index:], f"GitHub rate limit reached: {err}")
                break
            failed += skip_unchecked([branch], str(err))
            continue
        if candidate:
            candidates.append(candidate)
        if was_kept:
            kept += 1

    return candidates, kept, failed


def _delete_branch(candidate: Candidate, repo_root: Path) -> tuple[bool, str]:
    ref = f"refs/heads/{candidate.branch}"
    proc = _run(["git", "update-ref", "-d", ref, candidate.local_oid], repo_root)
    detail = proc.stderr.strip() or proc.stdout.strip()
    if proc.returncode != 0:
        return False, detail

    occupancy_error: str | None = None
    try:
        became_attached = candidate.branch in get_worktree_branches(repo_root)
    except PruneError as err:
        became_attached = True
        occupancy_error = str(err)

    if not became_attached:
        return True, detail

    zero_oid = "0" * 40
    restore = _run(["git", "update-ref", ref, candidate.local_oid, zero_oid], repo_root)
    if restore.returncode != 0:
        restore_detail = restore.stderr.strip() or restore.stdout.strip() or f"exit {restore.returncode}"
        current_oid = get_local_branches(repo_root).get(candidate.branch)
        if current_oid != candidate.local_oid:
            current_detail = current_oid or "missing"
            raise PruneError(
                f"branch '{candidate.branch}' became attached after the pre-delete check and its ref could not be "
                f"restored to {candidate.local_oid}; current ref is {current_detail}: {restore_detail}"
            )
    reason = (
        "post-delete worktree occupancy check failed" if occupancy_error else "branch became attached to a worktree"
    )
    return False, f"{reason}; atomic deletion was rolled back"


def prune_merged_branches(repo_root: Path, *, dry_run: bool, out: TextIO = sys.stdout) -> PruneResult:
    fetch_target_branch(repo_root)

    try:
        candidates, kept, planning_failed = _plan_candidates(repo_root, out)
    except PruneError:
        raise
    except Exception as err:
        raise PruneError(f"unexpected evidence collection failure: {err}") from err

    if dry_run:
        for candidate in candidates:
            print(f"would delete {candidate.branch} ({candidate.describe()})", file=out)
        print(
            f"Dry run: would delete {len(candidates)} branch(es); {kept} kept; {planning_failed} unchecked.",
            file=out,
        )
        return PruneResult(deleted=0, would_delete=len(candidates), kept=kept, failed=planning_failed)

    deleted = 0
    failed = planning_failed
    for candidate in candidates:
        current_oid = get_local_branches(repo_root).get(candidate.branch)
        if current_oid != candidate.local_oid:
            print(f"keep {candidate.branch}: branch changed after evidence collection", file=out)
            kept += 1
            continue
        if candidate.branch in get_worktree_branches(repo_root):
            print(f"keep {candidate.branch}: branch became attached to a worktree", file=out)
            kept += 1
            continue

        success, detail = _delete_branch(candidate, repo_root)
        if not success:
            print(f"error: failed to delete {candidate.branch}: {detail or 'unknown Git error'}", file=out)
            failed += 1
            continue
        print(f"deleted {candidate.branch} ({candidate.describe()})", file=out)
        deleted += 1

    print(f"Deleted {deleted} branch(es); {kept} kept; {failed} failure(s).", file=out)
    return PruneResult(deleted=deleted, would_delete=0, kept=kept, failed=failed)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry_run_env = os.environ.get("DRY_RUN", "")
    if dry_run_env not in {"", "1"}:
        print("DRY_RUN must be unset or exactly 1", file=sys.stderr)
        return 2
    dry_run = args.dry_run or dry_run_env == "1"
    try:
        result = prune_merged_branches(args.repo_root.resolve(), dry_run=dry_run)
    except PruneError as err:
        print(f"branch-prune-merged refused: {err}", file=sys.stderr)
        return 1
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
