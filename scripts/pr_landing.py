#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import BinaryIO, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _project.scripts import check_suites
from _project.scripts.soundness_paths import any_soundness_path

CLI_DESCRIPTION = (
    "One cohesive revision/readiness/queue/follow-up helper behind the Make PR targets.\n"
    "\n"
    "Session history shows the failure modes this closes: queued-branch push\n"
    "rejection after readiness was armed, unpublished fixes riding along, wrong-PR\n"
    "operations from an inferred branch, and late-fix merges ahead of corrections.\n"
    "The contract:\n"
    "\n"
    "* Every mutation names the exact repository, PR number/node, expected head,\n"
    "  branch, and worktree. A PR resolved from a reused branch name alone is\n"
    "  refused (wrong-PR protection).\n"
    "* Readiness is withdrawn (auto-merge disabled and verified) before the first\n"
    "  edit. If the merge wins the race, the helper stops and preserves commits\n"
    "  for a correctly identified follow-up instead of modifying a closed PR.\n"
    "* Ready requires local/remote head agreement on the exact expected head, no\n"
    "  unpublished work, completed review evidence on that head, required checks\n"
    "  green at that head, and no durable hold. Enqueue re-checks the remote head\n"
    "  immediately before arming; a head change invalidates readiness.\n"
    "* Feature-batch readiness additionally binds batch id/version, member set,\n"
    "  owner generation, and integration head; late members or content edits\n"
    "  invalidate it. Member SHAs must be ancestors of the integration head.\n"
    "* Follow-up state (pre-PR assembly through post-merge) persists with an\n"
    "  explicit owner and next action; retries are bounded to evidenced transient\n"
    "  failures on unchanged heads; terminal state is explicit, never inferred\n"
    "  from an empty PR list.\n"
    "\n"
    "All GitHub access goes through an injectable runner so tests replay races\n"
    "with fake hosted events. The live runner shells out to `gh`.\n"
)

HOLD_LABEL = "no-auto-merge"
REQUIRED_CONTEXTS: tuple[str, ...] = (
    "core",
    "explorer",
    "results-data",
    "docs",
    "tooling",
)
TERMINAL_STATUS_FAILURES = frozenset({"failure", "error"})
STATUS_CONTEXTS_JQ = r'.[] | [.context, .state, (.description // "")] | @tsv'
REQUIRED_BATCH_TOOLS = frozenset({"register_batch", "prepare", "bind_batch_pr", "abort_batch"})
MAX_RERUNS_PER_JOB = 1
MAX_REENTRIES_PER_HEAD = 1
PREPARED_RECEIPT_SCHEMA = "prepared_work_v1"
FULL_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SCOPE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
TODO_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
MAX_TODO_ID_LEN = 128
VALID_REVIEW_DISPOSITIONS = frozenset({"approved"})
FOLLOWUP_SCHEMA = "pr_followup_v2"
START_RECORD_SCHEMA = "pr_landing_start_v1"
FOLLOWUP_PHASES = frozenset(
    {"prepare", "pre-pr", "merge", "push", "review", "queue", "member-closeout", "post-merge", "blocked"}
)
MAX_FOLLOWUP_KEY_LEN = 128
MAX_FOLLOWUP_TEXT_LEN = 512
MAX_FOLLOWUP_MEMBERS = 64
MAX_FOLLOWUP_RECEIPTS = 64
MAX_FOLLOWUP_PROCESSED = 256
MAX_FOLLOWUP_RETRY_COUNT = 16
MAX_FOLLOWUP_BYTES = 64 * 1024


class LandingError(RuntimeError):
    pass


class WrongPR(LandingError):
    pass


class MergedRace(LandingError):
    pass


def state_dir(repo: Path) -> Path:
    override = os.environ.get("BENCHBOX_PR_LANDING_DIR")
    if override:
        return Path(override).expanduser()
    slug = hashlib.sha1(str(repo.resolve()).encode()).hexdigest()[:16]
    return Path.home() / ".benchbox" / "pr-landing" / slug


def start_record_path(repo: Path, branch: str) -> Path:
    if not branch or len(branch) > 512 or any(ord(char) < 32 for char in branch):
        raise LandingError("branch is invalid for a start-identity record")
    digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:32]
    return state_dir(repo) / f"start-{digest}.json"


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def record_start_identity(repo: Path, branch: str, identity: GitIdentity, pr: dict | None) -> Path:
    path = start_record_path(repo, branch)
    record = {
        "schema": START_RECORD_SCHEMA,
        "identity": asdict(identity),
        "pr": (pr or {}).get("number"),
        "pr_node_id": _pr_node_id(pr or {}) or None,
        "remote_head": (pr or {}).get("headRefOid"),
    }
    _write_json_atomic(path, record)
    return path


def load_start_identity(repo: Path, branch: str) -> dict | None:
    path = start_record_path(repo, branch)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == START_RECORD_SCHEMA else None


def require_start_identity(
    repo: Path,
    identity: GitIdentity,
    branch: str,
    *,
    pr_number: int | None = None,
    pr_node_id: str | None = None,
    require_unchanged_head: bool = False,
) -> dict:
    record = load_start_identity(repo, branch)
    if record is None or not isinstance(record.get("identity"), dict):
        raise LandingError("start identity is missing; run pr-landing-start before this transition")
    declared = record["identity"]
    for key, actual in (
        ("repository", identity.repository),
        ("branch", branch),
        ("worktree", identity.worktree),
        ("base", identity.base),
    ):
        if declared.get(key) != actual:
            raise LandingError(f"persisted start identity field {key!r} does not match the current checkout")
    recorded_lifecycle = declared.get("lifecycle_id")
    if recorded_lifecycle is not None and recorded_lifecycle != identity.lifecycle_id:
        raise LandingError("persisted start identity lifecycle does not match the current checkout")
    if require_unchanged_head and declared.get("head") != identity.head:
        raise LandingError("the checkout changed after start; withdraw must run before the first edit")
    recorded_pr = record.get("pr")
    if recorded_pr is not None and recorded_pr != pr_number:
        raise WrongPR(f"persisted start identity binds PR #{recorded_pr}, not declared PR #{pr_number}")
    recorded_node = record.get("pr_node_id")
    if pr_node_id is not None and recorded_node is not None and recorded_node != pr_node_id:
        raise WrongPR("persisted start identity PR node id does not match the declared PR identity")
    return record


def batch_runtime_capability_available(repo: Path) -> bool:
    evidence_path = repo / "_project" / "analysis" / "batch-rollout-evidence.json"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(evidence, dict):
        return False
    runtime = evidence.get("runtime")
    active = runtime.get("active_benchbox") if isinstance(runtime, dict) else None
    if not isinstance(active, dict):
        return False
    schema_version = active.get("schema_version")
    supported = active.get("supported_schema_versions")
    tools = active.get("registered_batch_tools")
    handshake = active.get("mcp_handshake")
    return (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version >= 3
        and isinstance(supported, list)
        and 3 in supported
        and isinstance(tools, list)
        and REQUIRED_BATCH_TOOLS.issubset(tools)
        and isinstance(handshake, dict)
        and handshake.get("result") == "pass"
    )


def batch_mode_failures(evidence: ReadyEvidence, repo: Path) -> list[str]:
    if not evidence.require_batch:
        return []
    failures: list[str] = []
    if not isinstance(evidence.batch, dict):
        failures.append("batch evidence is required for batch-mode readiness")
    if not batch_runtime_capability_available(repo):
        failures.append(
            "batch delivery requires the active todo-db runtime to advertise schema 3 and registered-batch tools"
        )
    return failures


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True, timeout=60)
    return proc.stdout.strip()


@dataclass
class GitIdentity:
    repo: str
    branch: str
    worktree: str
    head: str
    base: str | None
    upstream: str | None
    repository: str = ""
    lifecycle_id: str | None = None


def git_identity(repo: Path) -> GitIdentity:
    branch = _git(repo, "branch", "--show-current")
    if not branch:
        raise LandingError("detached HEAD has no branch identity; refuse to guess the PR")
    try:
        upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except subprocess.CalledProcessError:
        upstream = None
    try:
        base = _git(repo, "rev-parse", "origin/develop")
    except subprocess.CalledProcessError:
        base = None
    try:
        lifecycle_id = _git(repo, "config", "--worktree", "--get", "benchbox.worktree.lifecycle-id")
    except subprocess.CalledProcessError:
        lifecycle_id = None
    repository = github_repository(_git(repo, "config", "--get", "remote.origin.url"))
    return GitIdentity(
        repo=str(repo.resolve()),
        repository=repository,
        branch=branch,
        worktree=_git(repo, "rev-parse", "--show-toplevel"),
        head=_git(repo, "rev-parse", "HEAD"),
        base=base,
        upstream=upstream,
        lifecycle_id=lifecycle_id,
    )


Runner = Callable[[list[str]], tuple[int, str]]


def live_run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120, check=False)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def normalize_github_repository(value: object) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise LandingError("repository must be a GitHub owner/name identity")
    parts = value.split("/")
    if len(parts) != 2 or not all(REPOSITORY_PART_RE.fullmatch(part) for part in parts):
        raise LandingError(f"repository must be a GitHub owner/name identity, got {value!r}")
    return "/".join(part.lower() for part in parts)


def github_repository(origin_url: str) -> str:
    if not isinstance(origin_url, str) or not origin_url or any(ord(char) < 32 for char in origin_url):
        raise LandingError("origin remote is missing or malformed; refusing repository binding")
    match = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?", origin_url, re.IGNORECASE)
    if match:
        return normalize_github_repository(f"{match.group(1)}/{match.group(2)}")
    match = re.fullmatch(
        r"(?:https|ssh)://(?:git@)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?", origin_url, re.IGNORECASE
    )
    if match:
        return normalize_github_repository(f"{match.group(1)}/{match.group(2)}")
    raise LandingError(f"origin remote is not a supported GitHub URL or SSH form: {origin_url!r}")


def _pr_node_id(pr: dict) -> str:
    return str(pr.get("node_id") or pr.get("id") or "")


def _check_pr_identity(
    pr: dict,
    *,
    branch: str | None = None,
    number: int | None = None,
    node_id: str | None = None,
    head: str | None = None,
) -> None:
    if branch is not None and pr.get("headRefName") != branch:
        raise WrongPR(
            f"resolved PR #{pr.get('number')} points at {pr.get('headRefName')!r}, "
            f"not declared branch {branch!r}; refusing wrong-PR operation"
        )
    if number is not None and pr.get("number") != number:
        raise WrongPR(f"resolved PR #{pr.get('number')} is not declared PR #{number}")
    if node_id is not None and _pr_node_id(pr) != node_id:
        raise WrongPR(f"PR #{pr.get('number')} node id does not match the declared PR identity")
    if head is not None and pr.get("headRefOid") != head:
        raise WrongPR(f"PR #{pr.get('number')} head does not match the declared PR identity")


def _require_revision(value: object, label: str) -> str:
    if not isinstance(value, str) or not FULL_REVISION_RE.fullmatch(value):
        raise LandingError(f"{label} must be a full lowercase commit SHA")
    return value


def _same_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, RuntimeError):
        return os.path.abspath(left) == os.path.abspath(right)


def check_worktree_binding(
    identity: GitIdentity,
    *,
    repository: str | None = None,
    branch: str | None = None,
    worktree: str | None = None,
    worktree_id: str | None = None,
) -> None:
    if repository is not None and normalize_github_repository(repository) != identity.repository:
        raise LandingError(f"repository binding {repository!r} does not match {identity.repository!r}")
    if branch is not None and branch != identity.branch:
        raise LandingError(f"branch binding {branch!r} does not match {identity.branch!r}")
    if worktree is not None and not _same_path(worktree, identity.worktree):
        raise LandingError(f"worktree binding {worktree!r} does not match {identity.worktree!r}")
    if worktree_id is not None and worktree_id not in {identity.worktree, identity.lifecycle_id}:
        raise LandingError("worktree lifecycle identity does not match the current checkout")


def resolve_pr(
    run: Runner,
    repo_full: str,
    branch: str,
    *,
    expected_number: int | None = None,
    expected_node_id: str | None = None,
    expected_head: str | None = None,
) -> dict | None:
    repo_full = normalize_github_repository(repo_full)
    if expected_head is not None:
        _require_revision(expected_head, "expected head")
    rc, out = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo_full,
            "--base",
            "develop",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,id,url,headRefName,headRefOid,labels,reviewDecision",
        ]
    )
    if rc != 0:
        raise LandingError(f"gh pr list failed for branch {branch!r}")
    try:
        matches = json.loads(out or "[]")
    except ValueError as exc:
        raise LandingError(f"unparseable gh pr list output: {exc}") from exc
    if not isinstance(matches, list):
        raise LandingError("gh pr list returned a non-list payload; refusing to guess")
    if not matches:
        return None
    if len(matches) != 1:
        raise WrongPR(f"branch {branch!r} has {len(matches)} open develop PRs; refusing to guess")
    pr = matches[0]
    _check_pr_identity(
        pr,
        branch=branch,
        number=expected_number,
        node_id=expected_node_id,
        head=expected_head,
    )
    return pr


def unpublished_work(repo: Path) -> list[str]:
    problems: list[str] = []
    if _git(repo, "status", "--porcelain=v1"):
        problems.append("uncommitted working-tree changes")
    try:
        ahead = int(_git(repo, "rev-list", "--count", "@{u}..HEAD") or 0)
        behind = int(_git(repo, "rev-list", "--count", "HEAD..@{u}") or 0)
    except subprocess.CalledProcessError:
        return problems + ["no upstream tracking branch to compare"]
    if ahead:
        problems.append(f"{ahead} local commit(s) not pushed to upstream")
    if behind:
        problems.append(f"upstream is {behind} commit(s) ahead of local HEAD")
    return problems


def view_pr(
    run: Runner,
    repo_full: str,
    pr_number: int,
    *,
    expected_branch: str | None = None,
    expected_node_id: str | None = None,
    expected_head: str | None = None,
) -> dict:
    repo_full = normalize_github_repository(repo_full)
    if expected_head is not None:
        _require_revision(expected_head, "expected head")
    rc, out = run(
        [
            "gh",
            "pr",
            "view",
            "--repo",
            repo_full,
            str(pr_number),
            "--json",
            "number,state,autoMergeRequest,labels,id,headRefName,headRefOid,reviewDecision",
        ]
    )
    if rc != 0:
        raise LandingError(f"gh pr view failed for PR #{pr_number}")
    try:
        pr = json.loads(out or "{}")
    except ValueError as exc:
        raise LandingError(f"unparseable gh pr view output: {exc}") from exc
    if not isinstance(pr, dict):
        raise LandingError(f"gh pr view returned a non-object payload for PR #{pr_number}")
    _check_pr_identity(
        pr,
        branch=expected_branch,
        number=pr_number if expected_branch or expected_node_id or expected_head else None,
        node_id=expected_node_id,
        head=expected_head,
    )
    return pr


def withdraw_readiness(
    run: Runner,
    repo_full: str,
    pr_number: int,
    *,
    expected_branch: str | None = None,
    expected_node_id: str | None = None,
    expected_head: str | None = None,
) -> dict:
    pr = view_pr(
        run,
        repo_full,
        pr_number,
        expected_branch=expected_branch,
        expected_node_id=expected_node_id,
        expected_head=expected_head,
    )
    if str(pr.get("state", "")).upper() in ("MERGED", "CLOSED"):
        raise MergedRace(
            f"PR #{pr_number} is {pr.get('state')}; merge won the race. "
            "Stop modifying; preserve commits for a correctly identified follow-up."
        )
    if pr.get("autoMergeRequest"):
        rc, out = run(["gh", "pr", "merge", "--repo", repo_full, "--disable-auto", str(pr_number)])
        if rc != 0:
            raise LandingError(f"could not disable auto-merge on PR #{pr_number}: {out.strip()[:200]}")
        bound_node_id = expected_node_id or _pr_node_id(pr) or None
        if expected_node_id is not None or expected_head is not None:
            reverified = view_pr(
                run,
                repo_full,
                pr_number,
                expected_branch=expected_branch,
                expected_node_id=bound_node_id,
                expected_head=expected_head,
            )
        else:
            rc, out = run(["gh", "pr", "view", "--repo", repo_full, str(pr_number), "--json", "autoMergeRequest"])
            if rc != 0:
                raise LandingError(f"could not re-read PR #{pr_number} after disabling auto-merge")
            try:
                reverified = json.loads(out or "{}")
            except ValueError as exc:
                raise LandingError(f"unparseable re-verification output: {exc}") from exc
            if not isinstance(reverified, dict):
                raise LandingError(f"unparseable re-verification output for PR #{pr_number}")
        if reverified.get("autoMergeRequest"):
            raise LandingError(f"auto-merge still armed on PR #{pr_number} after disable; refusing revision")
        return {"pr": pr_number, "withdrew": "auto-merge", "verified": True}
    return {"pr": pr_number, "withdrew": "nothing-armed", "verified": True}


@dataclass
class ReadyEvidence:
    expected_head: str
    review_decision: str
    dispositions_complete: bool
    check_runs: list
    required: tuple = REQUIRED_CONTEXTS
    hold_labels: list | None = None
    soundness_paths_changed: bool = False
    maintainer_approved: bool = False
    batch: dict | None = None
    require_batch: bool = False
    repository: str | None = None
    branch: str | None = None
    worktree: str | None = None
    worktree_id: str | None = None
    pr_number: int | None = None
    pr_node_id: str | None = None


def live_check_runs(run: Runner, repo_full: str, head: str) -> list[dict]:
    rc, out = run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            check_suites.LATEST_JQ,
            check_suites.latest_check_runs_endpoint(repo_full, head),
        ]
    )
    if rc != 0:
        raise LandingError(f"live check verification failed for {head[:12]}")
    try:
        return check_suites.parse_check_run_lines(out)
    except ValueError as exc:
        raise LandingError(f"unparseable live check payload: {exc}") from exc


def live_check_verdicts(run: Runner, repo_full: str, head: str) -> dict[str, dict]:
    return check_suites.verdicts(live_check_runs(run, repo_full, head))


def ruleset_required_contexts(run: Runner, repo_full: str, branch: str) -> tuple[str, ...]:
    rc, out = run(
        [
            "gh",
            "api",
            "--jq",
            check_suites.REQUIRED_CONTEXTS_JQ,
            check_suites.required_contexts_endpoint(repo_full, branch),
        ]
    )
    if rc != 0:
        raise LandingError(f"could not read the required checks of {branch!r}: {out.strip()[:200]}")
    try:
        return check_suites.parse_required_contexts(out)
    except ValueError as exc:
        raise LandingError(str(exc)) from exc


def _hidden_failures(run: Runner, repo_full: str, branch: str, records: list[dict]) -> list[str]:
    required = ruleset_required_contexts(run, repo_full, branch)
    return check_suites.hidden_failure_reasons(check_suites.evaluate(records, required, check_suites.NON_BLOCKING))


def hidden_failed_suites(run: Runner, repo_full: str, branch: str, head: str) -> list[str]:
    return _hidden_failures(run, repo_full, branch, live_check_runs(run, repo_full, head))


def live_commit_statuses(run: Runner, repo_full: str, head: str) -> dict[str, str]:
    rc, out = run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            STATUS_CONTEXTS_JQ,
            f"repos/{repo_full}/commits/{head}/statuses?per_page=100",
        ]
    )
    if rc != 0:
        raise LandingError(f"live status verification failed for {head[:12]}")
    statuses: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.rstrip("\n").split("\t", 2)
        if len(parts) >= 2 and parts[0] not in statuses:
            name, state = parts[:2]
            description = parts[2] if len(parts) == 3 else ""
            if (
                name == "oracle-gate"
                and state == "failure"
                and description.startswith(f"oracle-review: waiting for the oracle's verdict on {head},")
            ):
                state = "pending"
            statuses[name] = state
    return statuses


def visible_required_failure_reasons(
    run: Runner, repo_full: str, branch: str, head: str, waiting: frozenset[str] = frozenset()
) -> list[str]:
    required = ruleset_required_contexts(run, repo_full, branch)
    evaluations = check_suites.evaluate(live_check_runs(run, repo_full, head), required, check_suites.NON_BLOCKING)
    statuses = live_commit_statuses(run, repo_full, head)
    reasons: list[str] = []
    for name in required:
        suites = evaluations[name].suites
        if (
            name not in waiting
            and suites
            and suites[0].completed
            and suites[0].conclusion not in check_suites.NON_BLOCKING
        ):
            reasons.append(
                f"required check {name!r} completed with conclusion {suites[0].conclusion!r} "
                f"on {head[:9]}; pending checks do not block arming"
            )
        state = statuses.get(name)
        if state in TERMINAL_STATUS_FAILURES:
            reasons.append(
                f"required status {name!r} completed with state {state!r} "
                f"on {head[:9]}; pending checks do not block arming"
            )
    return reasons


def verify_evidence_live(run: Runner, repo_full: str, pr: dict, evidence: ReadyEvidence) -> None:
    head = evidence.expected_head
    _check_pr_identity(
        pr,
        branch=evidence.branch,
        number=evidence.pr_number,
        node_id=evidence.pr_node_id,
        head=head,
    )
    records = live_check_runs(run, repo_full, head)
    verdicts = check_suites.verdicts(records)
    for name in evidence.required:
        live = verdicts.get(name)
        if live is None:
            raise LandingError(f"live verification: required context {name!r} has no check run at {head[:12]}")
        if str(live.get("head_sha") or "") != head or live.get("conclusion") != "success":
            raise LandingError(
                f"live verification: {name!r} is {live.get('conclusion')} "
                f"at {str(live.get('head_sha') or '')[:12]}, not success at {head[:12]}"
                f" (suite {live.get('suite_id')}, {live.get('url') or 'no run URL'})"
            )
    for reason in _hidden_failures(run, repo_full, str(pr.get("baseRefName") or "develop"), records):
        raise LandingError(f"live verification: {reason}")
    live_decision = str(pr.get("reviewDecision") or "")
    if live_decision != evidence.review_decision:
        raise LandingError(
            f"live verification: review decision is {live_decision!r}, evidence claims {evidence.review_decision!r}"
        )
    live_labels = {str(label.get("name") or "") for label in pr.get("labels") or []}
    if HOLD_LABEL in live_labels:
        raise LandingError(f"live verification: durable hold label {HOLD_LABEL!r} is present")
    for claimed in evidence.hold_labels or []:
        if claimed not in live_labels:
            raise LandingError(f"live verification: claimed label {claimed!r} absent from live labels")
    if unresolved_review_threads(run, repo_full, int(pr.get("number") or 0)):
        raise LandingError("live verification: unresolved, non-outdated review threads remain")


def unresolved_review_threads(run: Runner, repo_full: str, pr_number: int) -> bool:
    return any(
        isinstance(thread, dict) and not thread.get("isResolved") and not thread.get("isOutdated")
        for thread in review_threads(run, repo_full, pr_number)
    )


def review_threads(
    run: Runner, repo_full: str, pr_number: int, fields: str = "id isResolved isOutdated"
) -> Iterator[object]:
    try:
        owner, name = repo_full.split("/", 1)
    except ValueError as exc:
        raise LandingError(f"repository must be owner/name, got {repo_full!r}") from exc
    if not owner or not name or not pr_number:
        raise LandingError("live review-thread verification lacks a repository or PR number")
    query = (
        """
      query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
        repository(owner:$owner, name:$name) {
          pullRequest(number:$number) {
            reviewThreads(first:100, after:$cursor) {
              nodes { """
        + fields
        + """ }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    """
    )
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor is not None:
            cmd.extend(["-F", f"cursor={cursor}"])
        rc, out = run(cmd)
        if rc != 0:
            raise LandingError(f"live review-thread verification failed for PR #{pr_number}")
        try:
            payload = json.loads(out or "{}")
            threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = threads["nodes"]
            page = threads["pageInfo"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LandingError(f"unparseable live review-thread payload for PR #{pr_number}") from exc
        if not isinstance(nodes, list):
            raise LandingError(f"live review threads for PR #{pr_number} are not a list")
        yield from nodes
        if not page.get("hasNextPage"):
            return
        next_cursor = str(page.get("endCursor") or "")
        if not next_cursor or next_cursor in seen_cursors:
            raise LandingError("live review-thread pagination did not advance; refusing")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def checks_green_at_head(check_runs: list, required: tuple, head: str) -> list[str]:
    failures: list[str] = []
    for name in required:
        matches = [run for run in check_runs if run.get("name") == name]
        if not matches:
            failures.append(f"{name}: no check run observed")
            continue
        latest = max(matches, key=lambda run: str(run.get("started_at") or ""))
        if latest.get("head_sha") != head:
            failures.append(f"{name}: latest run bound to {str(latest.get('head_sha'))[:12]}, not expected head")
        elif latest.get("status") != "completed" or latest.get("conclusion") != "success":
            failures.append(f"{name}: {latest.get('status')}/{latest.get('conclusion')}")
    return failures


def _canonical_member_ids(value: object) -> list[str] | None:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or not item or len(item) > MAX_TODO_ID_LEN or not TODO_ID_RE.fullmatch(item)
            for item in value
        )
    ):
        return None
    member_ids = cast(list[str], value)
    if len(set(member_ids)) != len(member_ids):
        return None
    return member_ids


def _canonical_files(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(
        not isinstance(path, str)
        or not path
        or os.path.isabs(path)
        or any(part in {"", ".", ".."} for part in Path(path).parts)
        or any(ord(char) < 32 for char in path)
        for path in value
    ):
        return None
    files = cast(list[str], value)
    if files != sorted(set(files)):
        return None
    return files


def _git_changed_files(repo: Path, base: str, head: str) -> set[str]:
    return set(_git(repo, "diff", "--name-only", "--no-renames", f"{base}..{head}").splitlines())


def _git_blob(repo: Path, revision: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _scope_digest(scope: dict[str, list[str]]) -> str:
    blob = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check_receipt_verification(member_id: str, receipt: dict) -> list[str]:
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        return [f"member {member_id} receipt has invalid verification evidence"]
    failures: list[str] = []
    if verification.get("status") != "passed":
        failures.append(f"member {member_id} receipt verification did not pass")
    if verification.get("revision") != receipt.get("source_revision"):
        failures.append(f"member {member_id} receipt verification has a stale revision")
    if verification.get("clean") is not True:
        failures.append(f"member {member_id} receipt verification is not clean")
    if not isinstance(verification.get("suite"), str) or not verification["suite"].strip():
        failures.append(f"member {member_id} receipt verification lacks a suite")
    command = verification.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
        failures.append(f"member {member_id} receipt verification lacks a command")
    return failures


def _check_receipt_git_scope(repo: Path, member_id: str, receipt: dict, files: list[str]) -> list[str]:
    source_base = receipt.get("source_base")
    accepted_head = receipt.get("accepted_head")
    if not isinstance(source_base, str) or not isinstance(accepted_head, str):
        return []
    try:
        actual_files = _git_changed_files(repo, source_base, accepted_head)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return [f"member {member_id} source changed files could not be read from Git"]
    if actual_files != set(files):
        return [f"member {member_id} receipt changed_files differ from the accepted Git diff"]
    return []


def _check_prepared_receipt(
    repo: Path, batch: dict, member_id: str, receipt: object, accepted_head: str, integration_head: str
) -> list[str]:
    failures: list[str] = []
    if not isinstance(receipt, dict):
        return [f"member {member_id} lacks a canonical prepared receipt"]
    required = (
        "schema",
        "batch_id",
        "member_id",
        "owner_generation",
        "source_worktree",
        "source_revision",
        "source_base",
        "accepted_head",
        "integration_head",
        "scope_hash",
        "changed_files",
        "verification",
    )
    failures.extend(f"member {member_id} receipt lacks {key}" for key in required if key not in receipt)
    if receipt.get("schema") != PREPARED_RECEIPT_SCHEMA:
        failures.append(f"member {member_id} receipt has an unsupported schema")
    if receipt.get("batch_id") != batch.get("batch_id"):
        failures.append(f"member {member_id} receipt is bound to a different batch")
    if receipt.get("member_id") != member_id:
        failures.append(f"member {member_id} receipt identifies a different member")
    generation = receipt.get("owner_generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        failures.append(f"member {member_id} receipt has an invalid owner generation")
    elif generation != batch.get("owner_generation"):
        failures.append(f"member {member_id} receipt has a stale owner generation")
    source_worktree = receipt.get("source_worktree")
    if not isinstance(source_worktree, str) or not os.path.isabs(source_worktree):
        failures.append(f"member {member_id} receipt requires an absolute source_worktree")
    revisions = ("source_revision", "source_base", "accepted_head", "integration_head")
    for key in revisions:
        revision = receipt.get(key)
        if not isinstance(revision, str) or not FULL_REVISION_RE.fullmatch(revision):
            failures.append(f"member {member_id} receipt has an invalid {key}")
    if receipt.get("source_revision") != receipt.get("accepted_head"):
        failures.append(f"member {member_id} receipt does not bind accepted_head to source_revision")
    if receipt.get("accepted_head") != accepted_head:
        failures.append(f"member {member_id} receipt is not bound to its accepted head")
    if receipt.get("integration_head") != integration_head:
        failures.append(f"member {member_id} receipt is not bound to the integration head")
    if receipt.get("scope_hash") != batch.get("scope_hash") or not SCOPE_HASH_RE.fullmatch(
        str(receipt.get("scope_hash"))
    ):
        failures.append(f"member {member_id} receipt has a foreign scope_hash")
    integrated_members = batch.get("integrated_members")
    integrated = integrated_members.get(member_id) if isinstance(integrated_members, dict) else None
    if not isinstance(integrated, dict) or receipt.get("integration_head") != integrated.get("integration_head"):
        failures.append(f"member {member_id} receipt has an unregistered integration head")
    files = _canonical_files(receipt.get("changed_files"))
    if files is None:
        failures.append(f"member {member_id} receipt changed_files are not canonical")
    else:
        failures.extend(_check_receipt_git_scope(repo, member_id, receipt, files))
    failures.extend(_check_receipt_verification(member_id, receipt))
    return failures


def _check_member_disposition(batch: dict, member_id: str, disposition: object, accepted_head: str) -> list[str]:
    if not isinstance(disposition, dict):
        return [f"member {member_id} review disposition is missing or not an object"]
    failures: list[str] = []
    if disposition.get("status") not in VALID_REVIEW_DISPOSITIONS:
        failures.append(f"member {member_id} review disposition is not passing")
    if disposition.get("current") is not True:
        failures.append(f"member {member_id} review disposition is stale")
    if disposition.get("resolved") is not True:
        failures.append(f"member {member_id} review disposition is unresolved")
    if disposition.get("member_id") != member_id:
        failures.append(f"member {member_id} review disposition names a different member")
    if disposition.get("head") != accepted_head:
        failures.append(f"member {member_id} review disposition has a stale head")
    if disposition.get("integration_head") != batch.get("integration_head"):
        failures.append(f"member {member_id} review disposition has a stale integration head")
    return failures


def _check_final_metadata(repo: Path, batch: dict, evidence: dict) -> list[str]:
    failures: list[str] = []
    required = (
        "batch_id",
        "project_id",
        "repository",
        "tree_worktree",
        "tree_revision",
        "integration_branch",
        "integration_head",
        "scope_hash",
        "member_heads",
        "member_ranges",
        "changed_files",
        "final_pr",
        "suite",
        "clean",
    )
    failures.extend(f"final evidence lacks {key}" for key in required if key not in evidence)
    if evidence.get("batch_id") != batch.get("batch_id"):
        failures.append("final evidence is bound to a different batch")
    if evidence.get("project_id") != batch.get("project_id") or evidence.get("repository") != batch.get("repository"):
        failures.append("final evidence names a different project or repository")
    if evidence.get("integration_branch") != batch.get("integration_branch"):
        failures.append("final evidence names a different integration branch")
    if evidence.get("integration_head") != batch.get("integration_head"):
        failures.append("final evidence is not at the current integration head")
    if evidence.get("scope_hash") != batch.get("scope_hash"):
        failures.append("final evidence has a foreign scope_hash")
    tree_worktree = evidence.get("tree_worktree")
    if not isinstance(tree_worktree, str) or not os.path.isabs(tree_worktree):
        failures.append("final evidence requires an absolute tree_worktree")
    elif not _same_path(tree_worktree, str(repo)) or not _same_path(
        tree_worktree, str(batch.get("integration_worktree", ""))
    ):
        failures.append("final evidence tree_worktree is not the current integration worktree")
    for key in ("tree_revision", "integration_head"):
        if not isinstance(evidence.get(key), str) or not FULL_REVISION_RE.fullmatch(evidence[key]):
            failures.append(f"final evidence has an invalid {key}")
    if evidence.get("tree_revision") != evidence.get("integration_head"):
        failures.append("final evidence tree_revision does not equal integration_head")
    if not isinstance(evidence.get("suite"), str) or not evidence["suite"].strip():
        failures.append("final evidence lacks a suite")
    if evidence.get("clean") is not True:
        failures.append("final evidence does not certify a clean tree")
    final_pr = evidence.get("final_pr")
    if (
        not isinstance(final_pr, dict)
        or not isinstance(final_pr.get("number"), int)
        or isinstance(final_pr.get("number"), bool)
        or final_pr.get("number") <= 0
    ):
        failures.append("final evidence has an invalid final PR number")
    if not isinstance(final_pr, dict) or not isinstance(final_pr.get("node_id"), str) or not final_pr.get("node_id"):
        failures.append("final evidence has an invalid final PR node id")
    if not isinstance(final_pr, dict) or final_pr.get("head") != evidence.get("integration_head"):
        failures.append("final evidence final PR is not bound to the integration head")
    if final_pr != batch.get("final_pr"):
        failures.append("final evidence is not bound to the declared final PR")
    return failures


def _check_final_members(
    repo: Path, batch: dict, evidence: dict, members: list[str], receipts: dict[str, object]
) -> list[str]:
    failures: list[str] = []
    heads = evidence.get("member_heads")
    ranges = evidence.get("member_ranges")
    if not isinstance(heads, dict) or set(heads) != set(members):
        failures.append("final evidence member_heads do not exactly match the declared members")
    if not isinstance(ranges, dict) or set(ranges) != set(members):
        failures.append("final evidence member_ranges do not exactly match the declared members")
    expected_files: set[str] = set()
    accepted_members = batch.get("accepted_members")
    accepted = accepted_members if isinstance(accepted_members, dict) else {}
    for member in members:
        receipt = receipts.get(member)
        receipt_data = receipt if isinstance(receipt, dict) else {}
        receipt_files = _canonical_files(receipt_data.get("changed_files"))
        if receipt_files is not None:
            expected_files.update(receipt_files)
        accepted_head = accepted.get(member)
        if isinstance(heads, dict) and heads.get(member) != accepted_head:
            failures.append(f"final evidence member {member} is not bound to its accepted head")
        member_range = ranges.get(member) if isinstance(ranges, dict) else None
        if (
            not isinstance(member_range, dict)
            or member_range.get("base") != receipt_data.get("source_base")
            or member_range.get("head") != accepted_head
        ):
            failures.append(f"final evidence member {member} range is not bound to its prepared receipt")
        if isinstance(accepted_head, str) and FULL_REVISION_RE.fullmatch(accepted_head):
            for path in receipt_files or []:
                try:
                    accepted_blob = _git_blob(repo, accepted_head, path)
                    final_blob = _git_blob(repo, str(evidence.get("integration_head") or ""), path)
                except (OSError, subprocess.TimeoutExpired):
                    failures.append(f"member {member} accepted content could not be read for {path}")
                    continue
                if accepted_blob != final_blob:
                    failures.append(f"member {member} accepted content was overwritten for {path}")
    final_files = _canonical_files(evidence.get("changed_files"))
    if final_files is None:
        failures.append("final evidence changed_files are not canonical")
    else:
        if set(final_files) != expected_files:
            failures.append("final evidence changed_files differ from the prepared member content")
        start_head = batch.get("start_head")
        integration_head = evidence.get("integration_head")
        if (
            isinstance(start_head, str)
            and FULL_REVISION_RE.fullmatch(start_head)
            and isinstance(integration_head, str)
            and FULL_REVISION_RE.fullmatch(integration_head)
        ):
            try:
                actual_final_files = _git_changed_files(repo, start_head, integration_head)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                failures.append("final evidence changed files could not be read from Git")
            else:
                if actual_final_files != set(final_files):
                    failures.append("final evidence changed_files differ from the actual integration Git diff")
        scope = batch.get("scope")
        allowed = (
            [pattern for member in members for pattern in scope.get(member, [])] if isinstance(scope, dict) else []
        )
        if any(not any(fnmatch.fnmatch(path, pattern) for pattern in allowed) for path in final_files):
            failures.append("final evidence changed_files exceed the frozen batch scope")
    return failures


def _check_final_evidence(repo: Path, batch: dict, members: list[str], receipts: dict[str, object]) -> list[str]:
    evidence = batch.get("final_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "passed":
        return ["batch final evidence is missing or does not have status passed"]
    failures = _check_final_metadata(repo, batch, evidence)
    failures.extend(_check_final_members(repo, batch, evidence, members, receipts))
    if evidence.get("conflict_resolution"):
        failures.append("final evidence contains conflict-resolution markers")
    return failures


def _check_batch_header(repo: Path, batch: dict, members: list[str], integration_head: str) -> list[str]:
    failures: list[str] = []
    batch_id = batch.get("batch_id")
    if (
        not isinstance(batch_id, str)
        or not batch_id
        or len(batch_id) > MAX_TODO_ID_LEN
        or not TODO_ID_RE.fullmatch(batch_id)
    ):
        failures.append("batch_id must be a canonical todo-db ID")
    for key in ("project_id", "repository", "owner", "integration_branch", "delivery_boundary", "terminal_outcome"):
        value = batch.get(key)
        if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
            failures.append(f"batch {key} must be a non-empty single-line string")
    generation = batch.get("owner_generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        failures.append("batch owner_generation must be a 32-hex value")
    worktree = batch.get("integration_worktree")
    if not isinstance(worktree, str) or not os.path.isabs(worktree):
        failures.append("batch integration_worktree must be absolute")
    elif not _same_path(worktree, str(repo)):
        failures.append("batch integration_worktree is not the current checkout")
    for key in ("start_head", "integration_head"):
        if not isinstance(batch.get(key), str) or not FULL_REVISION_RE.fullmatch(batch[key]):
            failures.append(f"batch {key} must be a full lowercase commit SHA")
    if batch.get("integration_head") != integration_head:
        failures.append("batch integration head moved since readiness was evaluated")
    scope = batch.get("scope")
    valid_scope = (
        isinstance(scope, dict)
        and set(scope) == set(members)
        and all(
            isinstance(paths, list) and bool(paths) and all(isinstance(path, str) and path for path in paths)
            for paths in scope.values()
        )
    )
    if not valid_scope:
        failures.append("batch scope must cover exactly the declared members")
    scope_hash = batch.get("scope_hash")
    if (
        not isinstance(scope_hash, str)
        or not SCOPE_HASH_RE.fullmatch(scope_hash)
        or (isinstance(scope, dict) and scope_hash != _scope_digest(scope))
    ):
        failures.append("batch scope_hash does not match the frozen scope")
    return failures


def _check_batch_maps(
    batch: dict, members: list[str]
) -> tuple[list[str], dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    failures: list[str] = []
    member_set = set(members)
    maps: list[tuple[str, object]] = [
        ("accepted_members", batch.get("accepted_members")),
        ("integrated_members", batch.get("integrated_members")),
        ("prepared_receipts", batch.get("prepared_receipts")),
        ("review_dispositions", batch.get("review_dispositions")),
    ]
    result: list[dict[str, object]] = []
    for name, value in maps:
        if not isinstance(value, dict) or set(value) != member_set:
            failures.append(f"{name} do not exactly match the declared members")
            result.append({})
        else:
            result.append(value)
    return failures, result[0], result[1], result[2], result[3]


def _check_batch_member_maps(
    repo: Path,
    batch: dict,
    members: list[str],
    accepted: dict[str, object],
    integrated: dict[str, object],
    receipts: dict[str, object],
    dispositions: dict[str, object],
    integration_head: str,
) -> list[str]:
    failures: list[str] = []
    for member in members:
        accepted_head = accepted.get(member)
        if (
            not isinstance(accepted_head, str)
            or not FULL_REVISION_RE.fullmatch(accepted_head)
            or not isinstance(integration_head, str)
            or not FULL_REVISION_RE.fullmatch(integration_head)
        ):
            failures.append(f"accepted head for member {member} is invalid")
        integration = integrated.get(member)
        if (
            not isinstance(integration, dict)
            or integration.get("accepted_head") != accepted_head
            or integration.get("integration_head") != integration_head
        ):
            failures.append(f"integration evidence for member {member} is stale")
        failures.extend(
            _check_prepared_receipt(repo, batch, member, receipts.get(member), accepted_head or "", integration_head)
        )
        if not isinstance(accepted_head, str) or not FULL_REVISION_RE.fullmatch(accepted_head):
            failures.append(f"member {member} accepted head is not in the integration head")
        else:
            try:
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", accepted_head, integration_head],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                failures.append(f"member {member} accepted head is not in the integration head")
        failures.extend(_check_member_disposition(batch, member, dispositions.get(member), accepted_head or ""))
    return failures


def check_batch_binding(repo: Path, batch: dict, integration_head: str) -> list[str]:
    if not isinstance(batch, dict):
        return ["batch binding must be an object"]
    failures: list[str] = []
    try:
        current_head = _git(repo, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        failures.append("could not read the current integration checkout HEAD")
    else:
        if current_head != integration_head:
            failures.append("current integration checkout HEAD does not match integration_head")
    try:
        if _git(repo, "status", "--porcelain"):
            failures.append("current integration checkout is dirty")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        failures.append("could not verify that the current integration checkout is clean")
    required = (
        "batch_id",
        "project_id",
        "repository",
        "owner",
        "owner_generation",
        "integration_branch",
        "integration_worktree",
        "start_head",
        "members",
        "scope",
        "scope_hash",
        "delivery_boundary",
        "terminal_outcome",
        "integration_head",
        "accepted_members",
        "integrated_members",
        "prepared_receipts",
        "review_dispositions",
        "final_pr",
        "final_evidence",
    )
    failures.extend(f"batch binding lacks {key}" for key in required if key not in batch)
    members = _canonical_member_ids(batch.get("members"))
    if members is None:
        failures.append("batch members must be a unique non-empty ordered list")
        members = []
    failures.extend(_check_batch_header(repo, batch, members, integration_head))
    map_failures, accepted, integrated, receipts, dispositions = _check_batch_maps(batch, members)
    failures.extend(map_failures)
    failures.extend(
        _check_batch_member_maps(repo, batch, members, accepted, integrated, receipts, dispositions, integration_head)
    )
    final_pr = batch.get("final_pr")
    if (
        not isinstance(final_pr, dict)
        or not isinstance(final_pr.get("number"), int)
        or isinstance(final_pr.get("number"), bool)
        or not isinstance(final_pr.get("node_id"), str)
        or not final_pr.get("node_id")
        or not isinstance(final_pr.get("head"), str)
        or not FULL_REVISION_RE.fullmatch(final_pr["head"])
        or final_pr.get("number") <= 0
        or final_pr.get("head") != integration_head
    ):
        failures.append("batch final_pr is invalid or stale")
    if any(batch.get(key) for key in ("active_writers", "pending_writers")):
        failures.append("batch has active or pending writers or conflict resolution markers")
    if batch.get("conflict_resolution"):
        failures.append("batch has active or pending writers or conflict resolution markers")
    failures.extend(_check_final_evidence(repo, batch, members, receipts))
    return failures


def ready_failures(
    identity: GitIdentity,
    remote_head: str,
    evidence: ReadyEvidence,
    repo: Path,
) -> list[str]:
    failures: list[str] = []
    expected_head = evidence.expected_head if isinstance(evidence.expected_head, str) else ""
    head_valid = bool(FULL_REVISION_RE.fullmatch(expected_head))
    try:
        _require_revision(expected_head, "expected head")
    except LandingError as exc:
        failures.append(str(exc))
    for field, declared, actual in (
        ("repository", evidence.repository, identity.repository),
        ("branch", evidence.branch, identity.branch),
        ("worktree", evidence.worktree, identity.worktree),
    ):
        if declared is not None and (not _same_path(declared, actual) if field == "worktree" else declared != actual):
            failures.append(f"{field} binding does not match the current checkout")
    if evidence.worktree_id is not None and evidence.worktree_id not in {identity.worktree, identity.lifecycle_id}:
        failures.append("worktree lifecycle identity does not match the current checkout")
    if evidence.pr_number is not None and evidence.pr_number <= 0:
        failures.append("PR number binding is invalid")
    if evidence.pr_node_id is not None and not evidence.pr_node_id:
        failures.append("PR GraphQL node id binding is empty")
    if identity.head != expected_head:
        failures.append(f"local head {identity.head[:12]} != expected {expected_head[:12]}")
    if remote_head != expected_head:
        failures.append(f"remote head {remote_head[:12]} != expected {expected_head[:12]}")
    failures.extend(f"unpublished work: {problem}" for problem in unpublished_work(repo))
    soundness_changed = head_valid and soundness_paths_changed(repo, identity.base, expected_head)
    if evidence.review_decision != "APPROVED" and (evidence.review_decision != "" or soundness_changed):
        failures.append(f"review decision is {evidence.review_decision!r}, not APPROVED")
    if not evidence.dispositions_complete:
        failures.append("review dispositions incomplete (every top-level finding needs evidence)")
    failures.extend(checks_green_at_head(evidence.check_runs, evidence.required, expected_head))
    holds = evidence.hold_labels or []
    if HOLD_LABEL in holds:
        failures.append(f"durable hold label {HOLD_LABEL!r} present; a human removes it, never this helper")
    failures.extend(batch_mode_failures(evidence, repo))
    if soundness_changed:
        failures.append("soundness paths changed; auto-enqueue is forbidden and requires manual maintainer merge")
    if head_valid and evidence.batch is not None:
        if evidence.batch.get("repository") != evidence.repository:
            failures.append("batch repository does not match the current checkout")
        if evidence.batch.get("integration_branch") != evidence.branch:
            failures.append("batch integration branch does not match the current checkout")
        if evidence.batch.get("integration_worktree") and not _same_path(
            str(evidence.batch["integration_worktree"]), identity.worktree
        ):
            failures.append("batch integration worktree does not match the current checkout")
        failures.extend(check_batch_binding(repo, evidence.batch, expected_head))
    return failures


def soundness_paths_changed(repo: Path, base: str | None, head: str) -> bool:
    if not base:
        raise LandingError("origin/develop is unavailable; cannot classify soundness paths")
    if base == head:
        return False
    try:
        paths = _git(repo, "diff", "--name-only", "--no-renames", f"{base}...{head}").splitlines()
    except subprocess.CalledProcessError as exc:
        raise LandingError("could not derive soundness paths from the exact base and head") from exc
    return any_soundness_path(paths)


def enqueue_pr(
    run: Runner,
    repo_full: str,
    pr_number: int,
    expected_head: str,
    remote_head: str,
    *,
    expected_branch: str | None = None,
    expected_node_id: str | None = None,
    expected_review_decision: str = "APPROVED",
) -> dict:
    repo_full = normalize_github_repository(repo_full)
    _require_revision(expected_head, "expected head")
    if expected_review_decision not in {"", "APPROVED"}:
        raise LandingError("expected review decision is not ready for enqueue")
    if remote_head != expected_head:
        raise LandingError(
            f"remote head moved to {remote_head[:12]} during enqueue; "
            "readiness is invalid, re-evaluate instead of arming"
        )
    if expected_branch is not None or expected_node_id is not None:
        if expected_branch is None or expected_node_id is None:
            raise LandingError("bound enqueue requires both branch and GraphQL node id")
        current = view_pr(
            run,
            repo_full,
            pr_number,
            expected_branch=expected_branch,
            expected_node_id=expected_node_id,
            expected_head=expected_head,
        )
        if any(
            str(label.get("name") or "") == HOLD_LABEL
            for label in current.get("labels") or []
            if isinstance(label, dict)
        ):
            return {"pr": pr_number, "withheld": HOLD_LABEL, "verified": True}
        state = str(current.get("state") or "").upper()
        if state in {"MERGED", "CLOSED"}:
            raise MergedRace(
                f"PR #{pr_number} is {state}; merge won the race. "
                "Readiness is invalid and no enqueue will be attempted."
            )
        if state != "OPEN":
            raise LandingError(f"PR #{pr_number} state {state!r} is not OPEN; readiness is invalid")
        if str(current.get("reviewDecision") or "") != expected_review_decision:
            raise LandingError("PR review disposition changed before enqueue; readiness is invalid")
        if unresolved_review_threads(run, repo_full, pr_number):
            raise LandingError("PR review threads changed before enqueue; unresolved, non-outdated threads remain")
    rc, out = run(
        [
            "gh",
            "pr",
            "merge",
            "--repo",
            repo_full,
            "--auto",
            "--squash",
            "--match-head-commit",
            expected_head,
            str(pr_number),
        ]
    )
    if rc != 0:
        raise LandingError(f"enqueue refused for PR #{pr_number}: {out.strip()[:300]}")
    return {"pr": pr_number, "enqueued": True, "note": "API success is not proof of merge"}


TERMINAL_OUTCOMES = ("merged", "closed-merged", "abandoned", "superseded")


@dataclass
class FollowupState:
    owner: str
    session: str
    scope: str
    pr: int | None = None
    head: str | None = None
    phase: str = "review"
    processed: list | None = None
    attempts: int = 0
    reentries: int = 0
    due_at: str | None = None
    next_action: str = ""
    terminal: str | None = None
    schema: str = FOLLOWUP_SCHEMA
    batch_id: str | None = None
    owner_generation: str | None = None
    integrator: str | None = None
    integration_head: str | None = None
    members: list[str] | None = None
    pending_worker_heads: dict[str, str] | None = None
    accepted_receipts: dict[str, dict] | None = None
    final_pr: dict | None = None
    claim_expires_at: str | None = None
    blocked_reason: str | None = None


def followup_path(directory: Path, key: str) -> Path:
    if not isinstance(key, str) or not key or len(key) > MAX_FOLLOWUP_KEY_LEN:
        raise LandingError("followup key must be a bounded non-empty string")
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in key)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return directory / f"{safe}-{digest}.json"


def _legacy_followup_path(directory: Path, key: str) -> Path:
    if not isinstance(key, str) or not key or len(key) > MAX_FOLLOWUP_KEY_LEN:
        raise LandingError("followup key must be a bounded non-empty string")
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in key)
    return directory / f"{safe}.json"


def _followup_locked(directory: Path, key: str) -> BinaryIO:
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    handle = open(_legacy_followup_path(directory, key).with_suffix(".lock"), "a+b")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _reject_followup_conflicts(key: str, existing: FollowupState, state: FollowupState) -> None:
    for field in ("batch_id", "owner_generation", "integrator", "members", "integration_head"):
        old_value = getattr(existing, field)
        new_value = getattr(state, field)
        if old_value is not None and new_value is not None and new_value != old_value:
            raise LandingError(f"followup {key!r} cannot change immutable {field}")
    if existing.accepted_receipts is not None and isinstance(state.accepted_receipts, dict):
        for member, old_receipt in existing.accepted_receipts.items():
            if member in state.accepted_receipts and state.accepted_receipts[member] != old_receipt:
                raise LandingError(f"followup {key!r} cannot replace the accepted receipt for {member!r}")
    if existing.final_pr is not None and state.final_pr is not None and state.final_pr != existing.final_pr:
        raise LandingError(f"followup {key!r} cannot change its final PR binding")
    if existing.terminal is not None and state.terminal not in (None, existing.terminal):
        raise LandingError(f"followup {key!r} cannot regress its terminal outcome")


def _merge_accepted_receipts(existing: dict[str, dict] | None, incoming: object) -> object:
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    if isinstance(incoming, dict):
        return {**existing, **incoming}
    return incoming


def _merge_followup_state(existing: FollowupState, state: FollowupState) -> FollowupState:
    head_changed = existing.head is not None and state.head is not None and existing.head != state.head
    preserved = {
        field: getattr(existing, field)
        for field in ("batch_id", "owner_generation", "integrator", "members", "integration_head", "head")
        if getattr(state, field) is None and getattr(existing, field) is not None
    }
    merged = replace(state, **preserved)
    if existing.pending_worker_heads is not None and merged.pending_worker_heads is None:
        merged = replace(merged, pending_worker_heads=existing.pending_worker_heads)
    if existing.accepted_receipts is not None:
        merged = replace(
            merged, accepted_receipts=_merge_accepted_receipts(existing.accepted_receipts, merged.accepted_receipts)
        )
    if not head_changed and existing.final_pr is not None and merged.final_pr is None:
        merged = replace(merged, final_pr=existing.final_pr)
    if not head_changed and existing.terminal is not None and merged.terminal is None:
        merged = replace(merged, terminal=existing.terminal)
    if head_changed:
        merged = replace(merged, final_pr=None, terminal=None)
    if head_changed:
        attempts = 0
        reentries = 0
    else:
        attempts = merged.attempts
        if isinstance(attempts, int) and not isinstance(attempts, bool):
            attempts = max(attempts, existing.attempts)
        reentries = merged.reentries
        if isinstance(reentries, int) and not isinstance(reentries, bool):
            reentries = max(reentries, existing.reentries)
    return replace(merged, attempts=attempts, reentries=reentries)


def record_followup(directory: Path, key: str, state: FollowupState) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = followup_path(directory, key)
    with _followup_locked(directory, key):
        existing = _load_followup_unlocked(directory, key)
        if existing is not None and existing.owner != state.owner:
            raise LandingError(f"followup {key!r} is owned by {existing.owner!r}; refusing cross-owner overwrite")
        if existing is not None:
            _reject_followup_conflicts(key, existing, state)
            merged = _merge_followup_state(existing, state)
        else:
            merged = state
        validate_followup(merged)
        _write_followup_atomic(path, merged)
    return path


def _write_followup_atomic(path: Path, state: FollowupState) -> None:
    encoded = json.dumps(asdict(state), indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_FOLLOWUP_BYTES:
        raise LandingError("followup state exceeds its size bound")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _valid_text(value: object, label: str, *, required: bool = False) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, str) or (required and not value.strip()):
        return [f"followup {label} must be a non-empty string"]
    if len(value) > MAX_FOLLOWUP_TEXT_LEN or any(ord(char) < 32 for char in value):
        return [f"followup {label} is too long or contains control characters"]
    return []


def _parse_followup_time(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        return [f"followup {label} must be an ISO-8601 timestamp"]
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return [f"followup {label} must be an ISO-8601 timestamp"]
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return [f"followup {label} must include an explicit timezone"]
    return []


def _valid_revision_map(value: object, label: str, members: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"followup {label} must be an object"]
    if len(value) > MAX_FOLLOWUP_MEMBERS:
        return [f"followup {label} exceeds the member bound"]
    failures: list[str] = []
    for member, head in value.items():
        if not isinstance(member, str) or member not in members:
            failures.append(f"followup {label} names an undeclared member")
        if not isinstance(head, str) or not FULL_REVISION_RE.fullmatch(head):
            failures.append(f"followup {label} contains an invalid worker head")
    return failures


def _validate_followup_receipts(state: FollowupState, members: set[str]) -> list[str]:
    receipts = state.accepted_receipts
    if receipts is None:
        return []
    if not isinstance(receipts, dict) or len(receipts) > MAX_FOLLOWUP_RECEIPTS:
        return ["followup accepted_receipts exceeds its bound"]
    failures: list[str] = []
    if receipts and state.integration_head is None:
        failures.append("followup accepted_receipts require an integration_head")
    for member, receipt in receipts.items():
        if member not in members:
            failures.append("followup accepted_receipts names an undeclared member")
        try:
            receipt_size = len(json.dumps(receipt, separators=(",", ":")))
        except (TypeError, ValueError):
            receipt_size = MAX_FOLLOWUP_BYTES
        if not isinstance(receipt, dict) or receipt_size > 8192:
            failures.append("followup accepted_receipts contains an invalid or oversized receipt")
            continue
        if receipt.get("batch_id") != state.batch_id:
            failures.append(f"followup receipt for {member} has a foreign batch_id")
        if receipt.get("owner_generation") != state.owner_generation:
            failures.append(f"followup receipt for {member} has a stale owner generation")
        if receipt.get("member_id") != member:
            failures.append(f"followup receipt for {member} identifies a different member")
        if receipt.get("integration_head") != state.integration_head:
            failures.append(f"followup receipt for {member} has a stale integration head")
        accepted_head = receipt.get("accepted_head")
        if not isinstance(accepted_head, str) or not FULL_REVISION_RE.fullmatch(accepted_head):
            failures.append(f"followup receipt for {member} has an invalid accepted head")
    return failures


def _validate_followup_final_pr(state: FollowupState) -> list[str]:
    final_pr = state.final_pr
    if final_pr is None:
        return []
    if (
        not isinstance(final_pr, dict)
        or not isinstance(final_pr.get("number"), int)
        or isinstance(final_pr.get("number"), bool)
        or final_pr.get("number", 0) <= 0
        or not isinstance(final_pr.get("node_id"), str)
        or not final_pr.get("node_id")
        or not isinstance(final_pr.get("head"), str)
        or not FULL_REVISION_RE.fullmatch(final_pr["head"])
        or (state.integration_head is not None and final_pr["head"] != state.integration_head)
    ):
        return ["followup final_pr binding is invalid"]
    return []


def _validate_followup_batch(state: FollowupState) -> list[str]:
    failures: list[str] = []
    if (
        not isinstance(state.batch_id, str)
        or len(state.batch_id) > MAX_TODO_ID_LEN
        or not TODO_ID_RE.fullmatch(state.batch_id or "")
    ):
        failures.append("followup batch_id must be a canonical todo-db ID")
    if not isinstance(state.owner_generation, str) or not re.fullmatch(r"[0-9a-f]{32}", state.owner_generation):
        failures.append("followup owner_generation must be a 32-hex value")
    failures.extend(_valid_text(state.integrator, "integrator", required=True))
    if state.integration_head is not None and (
        not isinstance(state.integration_head, str) or not FULL_REVISION_RE.fullmatch(state.integration_head)
    ):
        failures.append("followup integration_head must be a full lowercase commit SHA")
    members = state.members
    if (
        not isinstance(members, list)
        or not members
        or len(members) > MAX_FOLLOWUP_MEMBERS
        or any(not isinstance(member, str) or not TODO_ID_RE.fullmatch(member) for member in members)
        or len(set(members)) != len(members)
    ):
        failures.append("followup members must be a unique non-empty ordered list")
        member_set: set[str] = set()
    else:
        member_set = set(members)
    failures.extend(_valid_revision_map(state.pending_worker_heads, "pending_worker_heads", member_set))
    failures.extend(_validate_followup_receipts(state, member_set))
    failures.extend(_validate_followup_final_pr(state))
    return failures


def validate_followup(state: FollowupState) -> None:
    failures: list[str] = []
    for field in ("owner", "session", "scope"):
        failures.extend(_valid_text(getattr(state, field, None), field, required=True))
    if state.schema not in {"", FOLLOWUP_SCHEMA}:
        failures.append(f"followup schema {state.schema!r} is unsupported")
    if state.pr is not None and (not isinstance(state.pr, int) or isinstance(state.pr, bool) or state.pr <= 0):
        failures.append("followup pr must be a positive integer")
    value = state.head
    if value is not None and (not isinstance(value, str) or not FULL_REVISION_RE.fullmatch(value)):
        failures.append("followup head must be a full lowercase commit SHA")
    if state.phase not in FOLLOWUP_PHASES:
        failures.append(f"followup phase {state.phase!r} is unsupported")
    failures.extend(_valid_text(state.next_action, "next_action"))
    failures.extend(_valid_text(state.blocked_reason, "blocked_reason"))
    if state.terminal is not None and state.terminal not in TERMINAL_OUTCOMES:
        failures.append(f"followup terminal outcome {state.terminal!r} is unsupported")
    for field in ("attempts", "reentries"):
        count = getattr(state, field)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_FOLLOWUP_RETRY_COUNT:
            failures.append(f"followup {field} is outside its bound")
    if state.processed is not None and (
        not isinstance(state.processed, list) or len(state.processed) > MAX_FOLLOWUP_PROCESSED
    ):
        failures.append("followup processed exceeds its bound")
    failures.extend(_parse_followup_time(state.due_at, "due_at"))
    failures.extend(_parse_followup_time(state.claim_expires_at, "claim_expires_at"))
    batch_fields = (state.batch_id, state.owner_generation, state.integrator, state.integration_head, state.members)
    has_batch = any(value is not None for value in batch_fields) or any(
        value is not None for value in (state.pending_worker_heads, state.accepted_receipts, state.final_pr)
    )
    if has_batch:
        failures.extend(_validate_followup_batch(state))
    if failures:
        raise LandingError("; ".join(failures))


def coerce_followup(data: object) -> FollowupState:
    if not isinstance(data, dict):
        raise LandingError("followup state must be an object")
    aliases = {
        "batch_identity": "batch_id",
        "generation": "owner_generation",
        "declared_members": "members",
        "accepted_integration_receipts": "accepted_receipts",
        "final_pr_binding": "final_pr",
    }
    normalized: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise LandingError("followup state field names must be strings")
        normalized_key = aliases.get(key, key)
        if normalized_key in normalized:
            raise LandingError(f"followup state duplicates field {normalized_key!r}")
        normalized[normalized_key] = value
    fields = set(FollowupState.__dataclass_fields__)
    unknown = sorted(set(normalized) - fields)
    if unknown:
        raise LandingError(f"followup state has unknown fields: {', '.join(unknown[:4])}")
    try:
        state = FollowupState(**{k: value for k, value in normalized.items() if k in fields})
    except TypeError as exc:
        raise LandingError(f"followup state has an unexpected shape: {exc}") from exc
    validate_followup(state)
    return state


def load_followup(directory: Path, key: str) -> FollowupState | None:
    with _followup_locked(directory, key):
        return _load_followup_unlocked(directory, key)


def _read_followup(path: Path) -> tuple[bool, FollowupState | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, None
    except (OSError, ValueError):
        return True, None
    try:
        return True, coerce_followup(data)
    except LandingError:
        return True, None


def _load_followup_unlocked(directory: Path, key: str) -> FollowupState | None:
    path = followup_path(directory, key)
    present, state = _read_followup(path)
    if present:
        return state

    legacy_path = _legacy_followup_path(directory, key)
    legacy_present, legacy_state = _read_followup(legacy_path)
    if not legacy_present or legacy_state is None:
        return None

    if path.exists():
        return _read_followup(path)[1]
    try:
        legacy_path.replace(path)
    except FileNotFoundError:
        return _read_followup(path)[1]
    except OSError as exc:
        raise LandingError(f"could not migrate legacy followup {key!r}: {exc}") from exc
    return legacy_state


def resume_followup(state: FollowupState) -> dict:
    validate_followup(state)
    if state.claim_expires_at:
        expires = dt.datetime.fromisoformat(state.claim_expires_at.replace("Z", "+00:00"))
        if expires <= dt.datetime.now(dt.timezone.utc):
            return {
                "status": "claim-expired",
                "next_action": "renew or explicitly transfer the expired claim before continuing",
            }
    if state.blocked_reason:
        return {
            "status": "blocked",
            "reason": state.blocked_reason,
            "next_action": state.next_action or "resolve the recorded blocker with the owner",
        }
    if state.members:
        accepted = set((state.accepted_receipts or {}).keys())
        missing = [member for member in state.members if member not in accepted]
        if state.terminal in {"merged", "closed-merged"} and missing:
            return {
                "status": "incomplete",
                "next_action": f"record accepted integration receipts for {', '.join(missing[:4])}",
                "missing_members": missing,
            }
        if state.terminal in {"merged", "closed-merged"} and state.final_pr is None:
            return {
                "status": "incomplete",
                "next_action": "bind the eventual final PR before declaring the batch merged",
            }
    if state.terminal is not None:
        return {"status": state.terminal, "next_action": ""}
    if not state.next_action:
        return {"status": "unknown", "next_action": "re-verify PR/head state; an empty queue is not completion"}
    return {"status": state.phase, "next_action": state.next_action}


def allow_retry(state: FollowupState, kind: str, head: str) -> dict:
    if state.head != head:
        return {"allowed": False, "reason": "head changed; re-evaluate instead of retrying"}
    if kind == "rerun":
        if state.attempts >= MAX_RERUNS_PER_JOB:
            return {"allowed": False, "reason": "rerun budget exhausted; escalate with owner"}
        return {"allowed": True, "reason": "one rerun of the failed job on the unchanged head"}
    if kind == "requeue":
        if state.reentries >= MAX_REENTRIES_PER_HEAD:
            return {"allowed": False, "reason": "re-entry budget exhausted; escalate with owner"}
        return {"allowed": True, "reason": "one queue re-entry on the unchanged head"}
    return {"allowed": False, "reason": f"unknown retry kind {kind!r}"}


def consume_retry(directory: Path, key: str, kind: str, head: str) -> dict:
    with _followup_locked(directory, key):
        state = _load_followup_unlocked(directory, key)
        if state is None:
            raise LandingError(f"no followup {key!r} recorded; record state before retrying")
        decision = allow_retry(state, kind, head)
        if not decision["allowed"]:
            return decision
        if kind == "rerun":
            state.attempts += 1
        else:
            state.reentries += 1
        _write_followup_atomic(followup_path(directory, key), state)
    return {**decision, "remaining": True}


def bound_withdraw(
    run: Runner,
    repo_full: str,
    branch: str,
    pr_number: int,
    *,
    expected_branch: str | None = None,
    expected_node_id: str | None = None,
    expected_head: str | None = None,
) -> dict:
    owned = resolve_pr(
        run,
        repo_full,
        branch,
        expected_number=pr_number if expected_node_id or expected_head else None,
        expected_node_id=expected_node_id,
        expected_head=expected_head,
    )
    if owned is not None and owned.get("number") != pr_number:
        raise WrongPR(f"branch {branch} owns PR #{owned.get('number')}, not PR #{pr_number}")
    bound_node_id = expected_node_id or (_pr_node_id(owned) if owned else None)
    return withdraw_readiness(
        run,
        repo_full,
        pr_number,
        expected_branch=expected_branch,
        expected_node_id=bound_node_id,
        expected_head=expected_head,
    )


def arm_current_pr(
    run: Runner,
    identity: GitIdentity,
    repo: Path,
    requested_pr: int | None = None,
) -> dict:
    problems = unpublished_work(repo)
    if problems:
        raise LandingError(f"unpublished work: {'; '.join(problems)}")
    pr = resolve_pr(
        run,
        identity.repository,
        identity.branch,
        expected_number=requested_pr,
        expected_head=identity.head,
    )
    if pr is None:
        raise WrongPR(f"branch {identity.branch} has no open PR in {identity.repository}")
    node_id = _pr_node_id(pr)
    if not node_id:
        raise WrongPR(f"PR #{pr.get('number')} has no GraphQL node id; refusing to arm")
    labels = {str(label.get("name") or "") for label in pr.get("labels") or [] if isinstance(label, dict)}
    if HOLD_LABEL in labels:
        return {"pr": pr.get("number"), "withheld": HOLD_LABEL, "verified": True}
    if soundness_paths_changed(repo, identity.base, identity.head):
        return {"pr": pr.get("number"), "withheld": "soundness-path", "verified": True}
    return enqueue_pr(
        run,
        identity.repository,
        int(pr["number"]),
        identity.head,
        str(pr.get("headRefOid") or ""),
        expected_branch=identity.branch,
        expected_node_id=node_id,
    )


def _run_start(args: argparse.Namespace, identity: GitIdentity, branch: str) -> int:
    pr = resolve_pr(
        live_run,
        args.repo,
        branch,
        expected_node_id=args.pr_node_id,
        expected_head=getattr(args, "expected_head", None),
    )
    path = record_start_identity(Path(identity.repo), branch, identity, pr)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["start_record"] = str(path)
    print(json.dumps(record, indent=2))
    return 0


def _run_withdraw(args: argparse.Namespace, identity: GitIdentity, branch: str) -> int:
    expected_head = args.expected_head or identity.head
    _require_revision(expected_head, "expected head")
    require_start_identity(
        Path(identity.repo),
        identity,
        branch,
        pr_number=args.pr,
        pr_node_id=args.pr_node_id,
        require_unchanged_head=True,
    )
    if expected_head != identity.head:
        raise LandingError("withdraw expected head does not match the current checkout")
    result = bound_withdraw(
        live_run,
        args.repo,
        branch,
        args.pr,
        expected_branch=branch,
        expected_node_id=args.pr_node_id,
        expected_head=expected_head,
    )
    print(json.dumps(result, indent=2))
    return 0


def _run_arm(args: argparse.Namespace, identity: GitIdentity, repo: Path) -> int:
    raise LandingError("direct arm is disabled; use the evidence-backed ready transition")


def _validate_evidence_identity(data: dict, identity: GitIdentity, branch: str, expected_head: str) -> None:
    declared = data.get("identity")
    if declared is None:
        return
    if not isinstance(declared, dict):
        raise LandingError("readiness identity must be a JSON object")
    for key, actual in (
        ("repository", identity.repository),
        ("branch", branch),
        ("worktree", identity.worktree),
        ("head", expected_head),
    ):
        if key not in declared:
            continue
        matches = _same_path(str(declared[key]), actual) if key == "worktree" else declared[key] == actual
        if not matches:
            raise LandingError(f"readiness identity field {key!r} does not match current binding")


def _run_ready(args: argparse.Namespace, identity: GitIdentity, branch: str, repo: Path) -> int:
    _require_revision(args.expected_head, "expected head")
    start_record = require_start_identity(repo, identity, branch, pr_number=args.pr, pr_node_id=args.pr_node_id)
    pr = resolve_pr(
        live_run,
        args.repo,
        branch,
        expected_number=args.pr,
        expected_node_id=args.pr_node_id,
        expected_head=args.expected_head,
    )
    if pr is None:
        raise WrongPR(f"branch {branch} does not own PR #{args.pr}")
    node_id = args.pr_node_id or _pr_node_id(pr)
    if not node_id:
        raise WrongPR(f"PR #{args.pr} has no GraphQL node id; refusing unbound readiness")
    recorded_node_id = start_record.get("pr_node_id")
    if recorded_node_id is not None and recorded_node_id != node_id:
        raise WrongPR("persisted start identity PR node id does not match the live PR")
    pr = view_pr(
        live_run,
        args.repo,
        args.pr,
        expected_branch=branch,
        expected_node_id=node_id,
        expected_head=args.expected_head,
    )
    try:
        evidence_data = json.loads(args.evidence_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LandingError(f"unreadable evidence file: {exc}") from exc
    if not isinstance(evidence_data, dict):
        raise LandingError("readiness evidence must be a JSON object")
    delivery_mode = evidence_data.get("delivery_mode")
    if not isinstance(delivery_mode, str) or delivery_mode not in {"serial", "batch"}:
        raise LandingError("readiness evidence must declare delivery_mode as 'serial' or 'batch'")
    _validate_evidence_identity(evidence_data, identity, branch, args.expected_head)
    evidence = ReadyEvidence(
        expected_head=args.expected_head,
        review_decision=evidence_data.get("review_decision", "REVIEW_REQUIRED"),
        dispositions_complete=bool(evidence_data.get("dispositions_complete")),
        check_runs=evidence_data.get("check_runs", []),
        hold_labels=evidence_data.get("hold_labels", []),
        soundness_paths_changed=bool(evidence_data.get("soundness_paths_changed")),
        maintainer_approved=bool(evidence_data.get("maintainer_approved")),
        batch=evidence_data.get("batch"),
        require_batch=args.require_batch or delivery_mode == "batch",
        repository=identity.repository,
        branch=branch,
        worktree=identity.worktree,
        worktree_id=args.worktree_id or identity.lifecycle_id or identity.worktree,
        pr_number=args.pr,
        pr_node_id=node_id,
    )
    verify_evidence_live(live_run, args.repo, pr, evidence)
    failures = ready_failures(identity, str(pr.get("headRefOid") or ""), evidence, repo)
    if failures:
        print("NOT READY:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    if args.arm:
        result = enqueue_pr(
            live_run,
            args.repo,
            args.pr,
            args.expected_head,
            str(pr.get("headRefOid") or ""),
            expected_branch=branch,
            expected_node_id=node_id,
            expected_review_decision=evidence.review_decision,
        )
        print(json.dumps(result, indent=2))
    else:
        print(f"READY at {args.expected_head[:12]} (enqueue withheld without --arm)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--branch", default=None)
    parser.add_argument("--worktree-id", default=None, help="worktree path or lifecycle id")
    parser.add_argument("--pr-node-id", default=None, help="PR GraphQL node id")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="record the start-revision identity")

    withdraw = sub.add_parser("withdraw", help="withdraw readiness before a revision")
    withdraw.add_argument("--pr", type=int, required=True)
    withdraw.add_argument("--expected-head", default=None)

    ready = sub.add_parser("ready", help="verify readiness and enqueue on success")
    ready.add_argument("--pr", type=int, required=True)
    ready.add_argument("--expected-head", required=True)
    ready.add_argument("--evidence-json", type=Path, required=True)
    ready.add_argument("--arm", action="store_true", help="enqueue when all checks pass")
    ready.add_argument("--require-batch", action="store_true", help="require a complete prepared-batch binding")

    arm = sub.add_parser("arm", help="arm the exact current checkout PR")
    arm.add_argument("--pr", type=int, default=None)

    rec = sub.add_parser("followup-record", help="persist continuation state")
    rec.add_argument("--key", required=True)
    rec.add_argument("--state-json", type=Path, required=True)

    res = sub.add_parser("followup-resume", help="print the owned next action")
    res.add_argument("--key", required=True)

    retry = sub.add_parser("followup-allow-retry", help="bounded retry decision")
    retry.add_argument("--key", required=True)
    retry.add_argument("--kind", required=True, choices=["rerun", "requeue"])
    retry.add_argument("--head", required=True)
    args = parser.parse_args(argv)

    repo = args.worktree.resolve()
    try:
        identity = None
        branch = None
        if args.command in {"start", "withdraw", "ready", "arm"}:
            identity = git_identity(repo)
            args.repo = args.repo or identity.repository
            check_worktree_binding(
                identity,
                repository=args.repo,
                branch=args.branch,
                worktree=str(repo),
                worktree_id=args.worktree_id,
            )
            branch = args.branch or identity.branch
        if args.command == "start":
            assert identity is not None and branch is not None
            return _run_start(args, identity, branch)
        if args.command == "withdraw":
            assert identity is not None and branch is not None
            return _run_withdraw(args, identity, branch)
        if args.command == "ready":
            assert identity is not None and branch is not None
            return _run_ready(args, identity, branch, repo)
        if args.command == "arm":
            assert identity is not None
            return _run_arm(args, identity, repo)
        directory = state_dir(repo)
        if args.command == "followup-record":
            try:
                data = json.loads(args.state_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LandingError(f"unreadable state file: {exc}") from exc
            path = record_followup(directory, args.key, coerce_followup(data))
            print(f"recorded {path}")
            return 0
        state = load_followup(directory, args.key)
        if state is None:
            print("unknown: no continuation state; re-verify, never assume done")
            return 2
        if args.command == "followup-resume":
            print(json.dumps(resume_followup(state), indent=2))
            return 0
        print(json.dumps(consume_retry(directory, args.key, args.kind, args.head), indent=2))
        return 0
    except (LandingError, MergedRace, WrongPR) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
