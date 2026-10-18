#!/usr/bin/env python3

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA_VERSION = 1
STRUCTURAL_BASES = {"develop", "release", "published-results"}
_EVIDENCE_SOURCE_KEY = "_benchbox_evidence_source"

REPORT_AUTHORITY = {
    "is_deletion_authority": False,
    "note": (
        "A snapshot describes state observed at generated_at. It expires the instant "
        "new commits, pushes, PR activity, or task/run events occur. No automation may treat "
        "any record in this file as license to act; an operator reads it and, if they choose to act, "
        "re-verifies the specific worktree by hand immediately before acting."
    ),
}


CLI_DESCRIPTION = (
    "Bounded read-only audit of registered worktrees and local branches.\n"
    "\n"
    "Combines live Git structure, exact GitHub PR evidence, structural-branch\n"
    "policy, and controller ownership evidence.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python _project/scripts/worktree_audit.py\n"
    "    uv run -- python _project/scripts/worktree_audit.py --format json\n"
    "    uv run -- python _project/scripts/worktree_audit.py --repo BenchBox-dev/BenchBox\n"
    "\n"
    "Guarantees:\n"
    "- Read-only: never mutates Git state, never prunes, never unlocks, never deletes.\n"
    "- Fail-closed: incomplete collection, API errors, missing objects, and ambiguous states resolve to 'unavailable' or 'uncertain'.\n"
    "- Zero deletion authority: output is a snapshot report for human inspection.\n"
)


@dataclasses.dataclass
class WorktreeInfo:
    path: str
    registered: bool
    path_exists: bool
    is_detached: bool
    is_locked: bool
    lock_reason: Optional[str]
    is_dirty: bool
    branch: Optional[str]
    head_sha: Optional[str]
    prunable: bool = False
    prunable_reason: Optional[str] = None


@dataclasses.dataclass
class IntegrationEvidence:
    target_branch: str
    pr_number: int
    pr_head_sha: str
    pr_base_ref: str
    pr_merged: bool
    merge_commit_sha: Optional[str]
    merge_commit_reachable_from_target_tip: bool
    checked_at: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Classification:
    state: str
    item_classification: str
    reason: str
    signals: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "item_classification": self.item_classification,
            "reason": self.reason,
            "signals": self.signals,
        }


class GitCollectionError(RuntimeError):
    pass


def _run_git(args: List[str], cwd: Path, timeout: float = 30.0) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired as exc:
        raise GitCollectionError(f"git command timed out after {timeout}s: git {' '.join(args)}") from exc
    except Exception as exc:
        raise GitCollectionError(f"git command failed before producing a result: git {' '.join(args)}: {exc}") from exc


def get_primary_clone_path(repo_root: Path) -> Optional[Path]:
    code, out, _ = _run_git(["rev-parse", "--git-common-dir"], repo_root)
    if code != 0 or not out:
        return None
    p = Path(out)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    else:
        p = p.resolve()
    return p.parent if p.name == ".git" else p


def _parse_worktree_porcelain_entries(stdout: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    current_entry: Dict[str, Any] = {}

    def maybe_append() -> None:
        if current_entry and "worktree" in current_entry:
            entries.append(current_entry)

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            maybe_append()
            current_entry = {}
            continue

        parts = line.split(" ", 1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else ""

        if key == "worktree":
            maybe_append()
            current_entry = {"worktree": val}
        elif key == "HEAD":
            current_entry["HEAD"] = val
        elif key == "branch":
            branch_ref = val
            if branch_ref.startswith("refs/heads/"):
                branch_ref = branch_ref.removeprefix("refs/heads/")
            current_entry["branch"] = branch_ref
        elif key == "detached":
            current_entry["detached"] = True
        elif key == "locked":
            current_entry["locked"] = True
            current_entry["lock_reason"] = val if val else None
        elif key == "prunable":
            current_entry["prunable"] = True
            current_entry["prunable_reason"] = val if val else None

    maybe_append()
    return entries


def get_git_worktrees(repo_root: Path, include_primary: bool = False) -> Tuple[List[WorktreeInfo], Optional[str]]:
    code, stdout, stderr = _run_git(["worktree", "list", "--porcelain"], repo_root)
    if code != 0:
        return [], f"Failed to list git worktrees (exit {code}): {stderr or 'unknown error'}"
    if not stdout:
        return [], None

    entries = _parse_worktree_porcelain_entries(stdout)
    if not entries:
        return [], None

    primary_clone = get_primary_clone_path(repo_root) if not include_primary else None
    worktrees: List[WorktreeInfo] = []

    for idx, entry in enumerate(entries):
        info = _build_worktree_info(entry)
        if not include_primary:
            if primary_clone is not None:
                try:
                    if Path(info.path).resolve() == primary_clone.resolve():
                        continue
                except Exception:
                    pass
            elif idx == 0:
                continue
        worktrees.append(info)

    return worktrees, None


def _build_worktree_info(entry: Dict[str, Any]) -> WorktreeInfo:
    path_str = entry["worktree"]
    path_obj = Path(path_str)
    path_exists = path_obj.exists() and path_obj.is_dir()
    is_detached = entry.get("detached", False) or "branch" not in entry
    branch = entry.get("branch") if not is_detached else None
    head_sha = entry.get("HEAD")

    is_dirty = False
    if path_exists:
        code, status_out, _ = _run_git(["status", "--porcelain"], path_obj)
        if code != 0 or status_out:
            is_dirty = True

    return WorktreeInfo(
        path=path_str,
        registered=True,
        path_exists=path_exists,
        is_detached=is_detached,
        is_locked=entry.get("locked", False),
        lock_reason=entry.get("lock_reason"),
        is_dirty=is_dirty,
        branch=branch,
        head_sha=head_sha,
        prunable=entry.get("prunable", False),
        prunable_reason=entry.get("prunable_reason"),
    )


def get_local_branches(repo_root: Path) -> List[Dict[str, Any]]:
    fmt = "%(refname:lstrip=2)|%(objectname)|%(upstream:short)|%(upstream:track)|%(committerdate:iso8601)"
    code, stdout, _ = _run_git(["for-each-ref", f"--format={fmt}", "refs/heads/"], repo_root)
    if code != 0 or not stdout:
        return []

    branches: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        name, sha, upstream, track, committerdate = parts[0], parts[1], parts[2], parts[3], parts[4]
        is_gone = "[gone]" in track
        branches.append(
            {
                "name": name,
                "head_sha": sha,
                "upstream": upstream if upstream else None,
                "is_gone_upstream": is_gone,
                "last_committed_at": committerdate,
            }
        )
    return branches


def get_ref_commit_sha(ref: str, repo_root: Path) -> Optional[str]:
    code, stdout, _ = _run_git(["rev-parse", "--verify", ref], repo_root)
    return stdout if code == 0 else None


def is_ancestor(ancestor_sha: str, descendant_sha: str, repo_root: Path) -> bool:
    code, _, _ = _run_git(["merge-base", "--is-ancestor", ancestor_sha, descendant_sha], repo_root)
    return code == 0


def get_reflog_shas(branch: str, repo_root: Path) -> Set[str]:
    code, stdout, _ = _run_git(["reflog", "show", "--format=%H", branch], repo_root)
    if code != 0 or not stdout:
        return set()
    return {line.strip().lower() for line in stdout.splitlines() if line.strip()}


def are_descendants_integrated(pr_head_sha: str, current_head_sha: str, target_tip: str, repo_root: Path) -> bool:
    if pr_head_sha.lower() == current_head_sha.lower():
        return True
    code, stdout, _ = _run_git(["rev-list", f"{pr_head_sha}..{current_head_sha}", f"^{target_tip}"], repo_root)
    if code != 0:
        return False
    return len(stdout.strip()) == 0


def is_branch_gone_upstream(branch: str, repo_root: Path) -> bool:
    code, stdout, _ = _run_git(["for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}"], repo_root)
    return code == 0 and "[gone]" in stdout


def _github_api_request(
    url: str, token: Optional[str]
) -> Tuple[Optional[Any], Optional[Dict[str, str]], Optional[str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "benchbox-worktree-audit",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.load(resp), resp_headers, None
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None, None, "HTTP 404 Not Found"
        return None, None, f"HTTP Error {err.code}: {err.reason}"
    except urllib.error.URLError as err:
        return None, None, f"URL Error: {err.reason}"
    except Exception as err:
        return None, None, f"Unexpected error: {err}"


def resolve_github_token() -> Optional[str]:
    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(variable, "").strip()
        if token:
            return token

    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            token = proc.stdout.strip()
            return token if token else None
    except Exception:
        pass
    return None


def get_remote_repository_slug(repo_root: Path, preferred_remote: str = "origin") -> Optional[str]:
    code, stdout, _ = _run_git(["remote", "get-url", preferred_remote], repo_root)
    if code != 0 or not stdout:
        code, remotes_out, _ = _run_git(["remote"], repo_root)
        if code == 0 and remotes_out:
            first_remote = remotes_out.splitlines()[0].strip()
            code, stdout, _ = _run_git(["remote", "get-url", first_remote], repo_root)
            if code != 0 or not stdout:
                return None
        else:
            return None

    url = stdout.strip().removesuffix(".git")
    if "github.com/" in url:
        return url.split("github.com/", 1)[1].strip("/")
    if "github.com:" in url:
        return url.split("github.com:", 1)[1].strip("/")
    return None


def resolve_repository_identity(
    owner: str,
    repo: str,
    token: Optional[str],
    remote_slug: Optional[str] = None,
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    encoded_owner = urllib.parse.quote(owner, safe="")
    encoded_repo = urllib.parse.quote(repo, safe="")
    url = f"https://api.github.com/repos/{encoded_owner}/{encoded_repo}"
    data, _, err = _github_api_request(url, token)
    if err or not isinstance(data, dict):
        return None, None, err or "Invalid repository payload"
    repo_id = data.get("id")
    if repo_id is None:
        return None, None, "Repository payload missing 'id'"
    full_name = data.get("full_name", f"{owner}/{repo}")

    if remote_slug:
        if "/" not in remote_slug:
            return None, None, f"Invalid GitHub remote repository slug: '{remote_slug}'"
        remote_owner, remote_repo = remote_slug.split("/", 1)
        encoded_remote_owner = urllib.parse.quote(remote_owner, safe="")
        encoded_remote_repo = urllib.parse.quote(remote_repo, safe="")
        remote_url = f"https://api.github.com/repos/{encoded_remote_owner}/{encoded_remote_repo}"
        remote_data, _, remote_err = _github_api_request(remote_url, token)
        if remote_err or not isinstance(remote_data, dict):
            return None, None, f"Failed to resolve remote repository '{remote_slug}': {remote_err or 'invalid payload'}"
        remote_repo_id = remote_data.get("id")
        if remote_repo_id is None:
            return None, None, f"Remote repository payload for '{remote_slug}' missing 'id'"
        if remote_repo_id != repo_id:
            return (
                None,
                None,
                (
                    f"Repository identity mismatch: remote '{remote_slug}' resolved to id {remote_repo_id}, "
                    f"but requested repository '{full_name}' resolved to id {repo_id}"
                ),
            )

    return repo_id, full_name, None


def is_pr_merged(pr: Dict[str, Any]) -> bool:
    if pr.get("merged") is True:
        return True
    return bool(pr.get("merged_at"))


def fetch_prs_for_branch(
    owner: str,
    repo: str,
    branch: str,
    expected_repo_id: Optional[int],
    token: Optional[str],
    max_pages: int = 10,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    encoded_owner = urllib.parse.quote(owner, safe="")
    encoded_repo = urllib.parse.quote(repo, safe="")
    all_prs: List[Tuple[Any, str]] = []
    page = 1

    while True:
        params = urllib.parse.urlencode(
            {
                "head": f"{owner}:{branch}",
                "state": "all",
                "per_page": 100,
                "page": page,
            }
        )
        url = f"https://api.github.com/repos/{encoded_owner}/{encoded_repo}/pulls?{params}"
        data, headers, err = _github_api_request(url, token)
        if err:
            return [], err
        if not isinstance(data, list):
            return [], "Expected list of PRs from API"

        all_prs.extend((pr, url) for pr in data)

        link_header = headers.get("link", "") if headers else ""
        has_next = 'rel="next"' in link_header
        if not has_next:
            break

        page += 1
        if page > max_pages:
            return [], f"PR listing pagination truncated: exceeded {max_pages} pages for branch '{branch}'"

    if not all_prs:
        return [], None

    validated_prs: List[Dict[str, Any]] = []
    for pr, source_url in all_prs:
        if not isinstance(pr, dict):
            continue
        base_repo = pr.get("base", {}).get("repo")
        if expected_repo_id is not None:
            base_repo_id = base_repo.get("id") if isinstance(base_repo, dict) else None
            if base_repo_id != expected_repo_id:
                continue
        validated_pr = dict(pr)
        validated_pr[_EVIDENCE_SOURCE_KEY] = f"GET {source_url}"
        validated_prs.append(validated_pr)

    if all_prs and not validated_prs:
        return [], (
            f"All {len(all_prs)} PRs returned for branch '{branch}' failed repository identity validation "
            f"(expected repo id {expected_repo_id})"
        )

    return validated_prs, None


def fetch_prs_for_branches(
    owner: str,
    repo: str,
    branches: Set[str],
    expected_repo_id: Optional[int],
    token: Optional[str],
    max_pages: int = 200,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    encoded_owner = urllib.parse.quote(owner, safe="")
    encoded_repo = urllib.parse.quote(repo, safe="")
    by_branch: Dict[str, List[Dict[str, Any]]] = {branch: [] for branch in branches}
    page = 1

    while True:
        params = urllib.parse.urlencode({"state": "all", "per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{encoded_owner}/{encoded_repo}/pulls?{params}"
        data, headers, err = _github_api_request(url, token)
        if err:
            return {}, err
        if not isinstance(data, list):
            return {}, "Expected list of PRs from API"

        for pr in data:
            if not isinstance(pr, dict):
                continue
            head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
            head_owner, _, head_ref = str(head.get("label") or "").partition(":")
            if head_owner.lower() != owner.lower() or head_ref not in by_branch:
                continue
            base_repo = pr.get("base", {}).get("repo")
            base_repo_id = base_repo.get("id") if isinstance(base_repo, dict) else None
            if expected_repo_id is not None and base_repo_id != expected_repo_id:
                return {}, (
                    f"PR #{pr.get('number')} for branch '{head_ref}' failed repository identity validation "
                    f"(expected repo id {expected_repo_id})"
                )
            validated_pr = dict(pr)
            validated_pr[_EVIDENCE_SOURCE_KEY] = f"GET {url}"
            by_branch[head_ref].append(validated_pr)

        link_header = headers.get("link", "") if headers else ""
        if 'rel="next"' not in link_header:
            return by_branch, None

        page += 1
        if page > max_pages:
            return {}, f"PR listing pagination truncated: exceeded {max_pages} pages"


def _check_worktree_boundary_states(wt: WorktreeInfo, signals: List[str]) -> Optional[Classification]:
    if not wt.path_exists or not wt.registered:
        return Classification(
            state="unavailable",
            item_classification="missing",
            reason=f"Worktree path '{wt.path}' does not exist on disk or is not registered",
            signals=signals,
        )

    if wt.prunable:
        reason = f"Worktree is prunable ({wt.prunable_reason})" if wt.prunable_reason else "Worktree is prunable"
        return Classification(
            state="unavailable",
            item_classification="prunable",
            reason=reason,
            signals=signals,
        )

    if wt.is_detached or not wt.branch:
        return Classification(
            state="uncertain",
            item_classification="detached",
            reason="Worktree is in detached HEAD state (no symbolic branch ref)",
            signals=signals,
        )

    if wt.is_locked:
        reason = f"Worktree is locked ({wt.lock_reason})" if wt.lock_reason else "Worktree is locked"
        return Classification(
            state="uncertain",
            item_classification="locked",
            reason=reason,
            signals=signals,
        )

    if wt.is_dirty:
        return Classification(
            state="uncertain",
            item_classification="dirty",
            reason="Worktree has uncommitted modifications, staged changes, or untracked files",
            signals=signals,
        )

    return None


def _build_integration_evidence_list(
    prs: List[Dict[str, Any]],
    repo_owner: str,
    repo_name: str,
    repo_root: Path,
) -> List[IntegrationEvidence]:
    evidence_list: List[IntegrationEvidence] = []
    checked_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for pr in prs:
        pr_number = pr.get("number", 0)
        pr_head_sha = pr.get("head", {}).get("sha", "")
        pr_base_ref = pr.get("base", {}).get("ref", "")
        is_merged = is_pr_merged(pr)
        merge_commit_sha = pr.get("merge_commit_sha")

        target_tip = get_ref_commit_sha(f"origin/{pr_base_ref}", repo_root) or get_ref_commit_sha(
            pr_base_ref, repo_root
        )
        merge_reachable = False
        if merge_commit_sha and target_tip:
            merge_reachable = is_ancestor(merge_commit_sha, target_tip, repo_root)

        evidence_list.append(
            IntegrationEvidence(
                target_branch=pr_base_ref,
                pr_number=pr_number,
                pr_head_sha=pr_head_sha,
                pr_base_ref=pr_base_ref,
                pr_merged=is_merged,
                merge_commit_sha=merge_commit_sha,
                merge_commit_reachable_from_target_tip=merge_reachable,
                checked_at=checked_at,
                source=str(pr.get(_EVIDENCE_SOURCE_KEY, "canned PR evidence")),
            )
        )
    return evidence_list


def _evaluate_merged_pr_integration(
    latest_pr: Dict[str, Any],
    wt: WorktreeInfo,
    signals: List[str],
    repo_root: Path,
) -> Classification:
    latest_base = latest_pr.get("base", {}).get("ref", "")
    if latest_base not in STRUCTURAL_BASES:
        return Classification(
            state="uncertain",
            item_classification="wrong base",
            reason=(
                f"PR #{latest_pr.get('number')} targets '{latest_base}', which is not a structural "
                f"integration branch ({', '.join(sorted(STRUCTURAL_BASES))})"
            ),
            signals=signals,
        )

    latest_head_sha = latest_pr.get("head", {}).get("sha", "")
    current_head_sha = wt.head_sha or ""
    reflog_shas = get_reflog_shas(wt.branch or "", repo_root)

    historical_matched = False
    if latest_head_sha and (
        latest_head_sha.lower() == current_head_sha.lower() or latest_head_sha.lower() in reflog_shas
    ):
        historical_matched = True

    if not historical_matched:
        return Classification(
            state="uncertain",
            item_classification="tip mismatch",
            reason=f"PR head {latest_head_sha[:12]} does not match current or historical branch commits",
            signals=signals,
        )

    target_tip = get_ref_commit_sha(f"origin/{latest_base}", repo_root) or get_ref_commit_sha(latest_base, repo_root)
    if not target_tip:
        return Classification(
            state="uncertain",
            item_classification="missing object",
            reason=f"Target structural branch '{latest_base}' tip could not be resolved locally",
            signals=signals,
        )

    if not are_descendants_integrated(latest_head_sha, current_head_sha, target_tip, repo_root):
        return Classification(
            state="uncertain",
            item_classification="tip mismatch",
            reason=(
                f"Worktree contains unintegrated post-merge commits on branch '{wt.branch}' "
                f"beyond merged PR head {latest_head_sha[:12]}"
            ),
            signals=signals,
        )

    latest_merge_commit = latest_pr.get("merge_commit_sha")
    if not latest_merge_commit or not is_ancestor(latest_merge_commit, target_tip, repo_root):
        return Classification(
            state="uncertain",
            item_classification="stale",
            reason=f"Merge commit for PR #{latest_pr.get('number')} is not yet reachable from '{latest_base}' tip",
            signals=signals,
        )

    return Classification(
        state="verified-integrated",
        item_classification="finish candidate",
        reason=(
            f"Branch '{wt.branch}' was merged via PR #{latest_pr.get('number')} into '{latest_base}' "
            "and current contents are fully integrated"
        ),
        signals=signals,
    )


def get_branch_age_days(branch: str, repo_root: Path) -> Optional[float]:
    code, stdout, _ = _run_git(["log", "-1", "--format=%ct", f"refs/heads/{branch}"], repo_root)
    if code != 0 or not stdout:
        return None
    try:
        commit_ts = int(stdout.strip())
        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        return max(0.0, (now_ts - commit_ts) / 86400.0)
    except Exception:
        return None


def evaluate_and_classify_worktree(
    wt: WorktreeInfo,
    repo_owner: str,
    repo_name: str,
    expected_repo_id: Optional[int],
    token: Optional[str],
    repo_root: Path,
    canned_prs: Optional[List[Dict[str, Any]]] = None,
    api_error_override: Optional[str] = None,
    repo_identity_error: Optional[str] = None,
) -> Tuple[Classification, List[IntegrationEvidence]]:
    signals: List[str] = []
    if wt.path_exists and not wt.is_dirty:
        signals.append("clean")
    if wt.branch and is_branch_gone_upstream(wt.branch, repo_root):
        signals.append("gone-upstream")
    if wt.branch:
        age_days = get_branch_age_days(wt.branch, repo_root)
        if age_days is not None and age_days >= 30.0:
            signals.append("old")

    if repo_identity_error:
        return (
            Classification(
                state="unavailable",
                item_classification="API error",
                reason=f"Failed to resolve repository identity: {repo_identity_error}",
                signals=signals,
            ),
            [],
        )

    boundary_cls = _check_worktree_boundary_states(wt, signals)
    if boundary_cls is not None:
        return boundary_cls, []

    prs: List[Dict[str, Any]] = []
    api_error: Optional[str] = api_error_override

    if canned_prs is not None:
        prs = canned_prs
    elif not api_error:
        prs, api_error = fetch_prs_for_branch(repo_owner, repo_name, wt.branch or "", expected_repo_id, token)

    if api_error:
        return (
            Classification(
                state="unavailable",
                item_classification="API error",
                reason=f"GitHub PR API error: {api_error}",
                signals=signals,
            ),
            [],
        )

    if not prs:
        return (
            Classification(
                state="uncertain",
                item_classification="active",
                reason=f"No GitHub PR found for branch '{wt.branch}'",
                signals=signals,
            ),
            [],
        )

    open_prs = [p for p in prs if p.get("state") == "open"]
    if open_prs:
        return (
            Classification(
                state="uncertain",
                item_classification="open PR",
                reason=f"Branch '{wt.branch}' has open PR #{open_prs[0].get('number')}",
                signals=signals,
            ),
            [],
        )

    sorted_prs = sorted(prs, key=lambda p: p.get("number", 0), reverse=True)
    evidence_list = _build_integration_evidence_list(sorted_prs, repo_owner, repo_name, repo_root)

    merged_prs = [p for p in sorted_prs if is_pr_merged(p)]
    if merged_prs:
        latest_merged_cls: Optional[Classification] = None
        for p in merged_prs:
            cls = _evaluate_merged_pr_integration(p, wt, list(signals), repo_root)
            if latest_merged_cls is None:
                latest_merged_cls = cls
            if cls.state == "verified-integrated":
                return cls, evidence_list
        assert latest_merged_cls is not None
        return latest_merged_cls, evidence_list

    latest_pr = sorted_prs[0]
    return (
        Classification(
            state="uncertain",
            item_classification="closed-unmerged",
            reason=f"Latest PR #{latest_pr.get('number')} for branch '{wt.branch}' closed without merge",
            signals=signals,
        ),
        evidence_list,
    )


def audit_worktrees(
    repo_root: Path,
    repo_slug: str = "BenchBox-dev/BenchBox",
    token: Optional[str] = None,
    initial_collection_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if token is None:
        token = resolve_github_token()

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    owner, repo = repo_slug.split("/", 1) if "/" in repo_slug else ("BenchBox-dev", repo_slug)
    collection_errors = list(initial_collection_errors or [])
    repo_id: Optional[int] = None
    full_name: Optional[str] = repo_slug
    effective_repo_err: Optional[str] = None
    worktrees_info: List[WorktreeInfo] = []
    local_branches: List[Dict[str, Any]] = []
    wt_err: Optional[str] = None

    try:
        remote_slug = get_remote_repository_slug(repo_root)
        repo_id, full_name, repo_err = resolve_repository_identity(owner, repo, token, remote_slug=remote_slug)
        effective_repo_err = repo_err if (repo_err or repo_id is None) else None
        if repo_id is None and not effective_repo_err:
            effective_repo_err = "Repository ID unavailable"
        if effective_repo_err:
            collection_errors.append(f"Repository identity collection failed: {effective_repo_err}")

        worktrees_info, wt_err = get_git_worktrees(repo_root)
        if wt_err:
            collection_errors.append(f"Git worktree enumeration failed: {wt_err}")
        local_branches = get_local_branches(repo_root)
    except GitCollectionError as err:
        wt_err = str(err)
        collection_errors.append(f"Git collection failed: {err}")

    worktree_records: List[Dict[str, Any]] = []
    finish_candidates = 0
    uncertain_count = 0
    unavailable_count = 0

    if wt_err:
        record = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "repository": {
                "id": repo_id,
                "full_name_observed": full_name or repo_slug,
                "resolved_via": f"https://api.github.com/repos/{owner}/{repo}",
            },
            "worktree": {
                "path": str(repo_root),
                "registered": False,
                "path_exists": repo_root.exists(),
                "is_detached": False,
                "is_locked": False,
                "lock_reason": None,
                "is_dirty": False,
                "branch": None,
                "head_sha": None,
                "is_prunable": False,
                "prunable_reason": None,
            },
            "integration_evidence": [],
            "task_run_evidence": {
                "available": False,
                "reason": "no published Bossmode public JSON contract yet (see §8)",
            },
            "classification": {
                "state": "unavailable",
                "item_classification": "Git error",
                "reason": f"Git worktree enumeration failed: {wt_err}",
                "signals": [],
            },
        }
        worktree_records.append(record)
        unavailable_count = 1
    else:
        for wt in worktrees_info:
            try:
                classification, evidence = evaluate_and_classify_worktree(
                    wt=wt,
                    repo_owner=owner,
                    repo_name=repo,
                    expected_repo_id=repo_id,
                    token=token,
                    repo_root=repo_root,
                    repo_identity_error=effective_repo_err,
                )
            except GitCollectionError as err:
                error = f"Git collection failed for worktree '{wt.path}': {err}"
                collection_errors.append(error)
                classification = Classification(
                    state="unavailable",
                    item_classification="Git error",
                    reason=error,
                    signals=[],
                )
                evidence = []

            if classification.state == "verified-integrated":
                finish_candidates += 1
            elif classification.state == "unavailable":
                unavailable_count += 1
            else:
                uncertain_count += 1

            record = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": generated_at,
                "repository": {
                    "id": repo_id,
                    "full_name_observed": full_name or repo_slug,
                    "resolved_via": f"https://api.github.com/repos/{owner}/{repo}",
                },
                "worktree": {
                    "path": wt.path,
                    "registered": wt.registered,
                    "path_exists": wt.path_exists,
                    "is_detached": wt.is_detached,
                    "is_locked": wt.is_locked,
                    "lock_reason": wt.lock_reason,
                    "is_dirty": wt.is_dirty,
                    "branch": wt.branch,
                    "head_sha": wt.head_sha,
                    "is_prunable": wt.prunable,
                    "prunable_reason": wt.prunable_reason,
                },
                "integration_evidence": [ev.to_dict() for ev in evidence],
                "task_run_evidence": {
                    "available": False,
                    "reason": "no published Bossmode public JSON contract yet (see §8)",
                },
                "classification": classification.to_dict(),
            }
            worktree_records.append(record)

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repository": {
            "id": repo_id,
            "full_name_observed": full_name or repo_slug,
            "resolved_via": f"https://api.github.com/repos/{owner}/{repo}",
        },
        "report_authority": REPORT_AUTHORITY,
        "collection_errors": collection_errors,
        "worktrees": worktree_records,
        "local_branches": local_branches,
        "summary": {
            "total_worktrees": len(worktree_records),
            "total_local_branches": len(local_branches),
            "finish_candidates": finish_candidates,
            "uncertain": uncertain_count,
            "unavailable": unavailable_count,
            "collection_errors": len(collection_errors),
        },
    }

    snapshot_error = _write_report_snapshot(report, repo_root)
    if snapshot_error:
        report["snapshot_path"] = None
        collection_errors.append(snapshot_error)
        report["collection_errors"] = collection_errors
        report["summary"]["collection_errors"] = len(collection_errors)

    return report


def _write_report_snapshot(report: Dict[str, Any], repo_root: Path) -> Optional[str]:
    report_dir = repo_root / "_project" / "reports" / "worktree-lifecycle"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        ts_slug = str(report["generated_at"]).replace(":", "-")
        snapshot_file = report_dir / f"{ts_slug}-{uuid.uuid4().hex}.json"
        report["snapshot_path"] = str(snapshot_file)
        with snapshot_file.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    except Exception as err:
        report["snapshot_path"] = None
        return f"Snapshot persistence failed: {err}"
    return None


def render_text_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=== BenchBox Worktree Lifecycle Audit ===")
    lines.append(f"Generated at: {report.get('generated_at')}")
    lines.append(f"Repository:   {report.get('repository', {}).get('full_name_observed')}")
    if report.get("snapshot_path"):
        lines.append(f"Snapshot:     {report.get('snapshot_path')}")
    lines.append("")

    worktrees = report.get("worktrees", [])
    if not worktrees:
        lines.append("No registered worktrees found.")
    else:
        lines.append(f"Registered Worktrees ({len(worktrees)}):")
        lines.append(f"{'Path':<50} | {'Branch':<30} | {'State':<20} | {'Classification':<20}")
        lines.append("-" * 128)
        for r in worktrees:
            wt = r.get("worktree", {})
            cls = r.get("classification", {})
            path = wt.get("path", "")
            if len(path) > 48:
                path = "..." + path[-45:]
            branch = wt.get("branch") or "(detached)"
            state = cls.get("state", "uncertain")
            item_cls = cls.get("item_classification", "")
            lines.append(f"{path:<50} | {branch:<30} | {state:<20} | {item_cls:<20}")

    lines.append("")
    summary = report.get("summary", {})
    lines.append("Summary:")
    lines.append(f"  Total worktrees:      {summary.get('total_worktrees', 0)}")
    lines.append(f"  Finish candidates:    {summary.get('finish_candidates', 0)}")
    lines.append(f"  Uncertain:            {summary.get('uncertain', 0)}")
    lines.append(f"  Unavailable:          {summary.get('unavailable', 0)}")
    lines.append(f"  Collection errors:    {summary.get('collection_errors', 0)}")
    lines.append(f"  Local branches:       {summary.get('total_local_branches', 0)}")
    for error in report.get("collection_errors", []):
        lines.append(f"  - {error}")
    lines.append("")
    lines.append(f"Authority Note: {report.get('report_authority', {}).get('note')}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: 'text' (default) or 'json'",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "BenchBox-dev/BenchBox"),
        help="GitHub repository slug owner/name (default: $GITHUB_REPOSITORY or BenchBox-dev/BenchBox)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any worktree is unavailable or collection fails",
    )
    args = parser.parse_args(argv)

    token = resolve_github_token()
    repo_root = Path.cwd()

    initial_collection_errors: List[str] = []
    try:
        code, root_out, _ = _run_git(["rev-parse", "--show-toplevel"], repo_root)
        if code == 0 and root_out:
            repo_root = Path(root_out)
    except GitCollectionError as err:
        initial_collection_errors.append(f"Git repository root collection failed: {err}")

    report = audit_worktrees(
        repo_root=repo_root,
        repo_slug=args.repo,
        token=token,
        initial_collection_errors=initial_collection_errors,
    )

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_text_report(report))

    if args.strict and (
        report.get("summary", {}).get("unavailable", 0) > 0 or report.get("summary", {}).get("collection_errors", 0) > 0
    ):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
