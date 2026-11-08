#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.worktree_finish import FinishPreviewResult

ROOT = Path(__file__).resolve().parents[1]

CLI_DESCRIPTION = (
    "Integration-branch batch loop: leader and member steps behind the batch-* make targets.\n"
    "\n"
    "Leader (role=integration worktree): start, member-create, land, advance, status, verify, evidence, finish.\n"
    "Leader recovery (clean checkout of the integration branch at the recorded head): recover.\n"
    "Member (role=member worktree): member-check.\n"
    "\n"
    "The tractable tracker is the authority for recorded heads, receipts and acceptance requirements;\n"
    "the ledger under .todo-batch/<batch>/ is a rebuildable cache and Git is the authority for history.\n"
    "Every tracker write is printed as a payload for the leader to submit; this script only reads the tracker.\n"
)

CONFIG_PREFIX = "benchbox.batch"
ROLE_INTEGRATION = "integration"
ROLE_MEMBER = "member"
DEVELOP_REF = "refs/remotes/origin/develop"
LEDGER_ROOT = ".todo-batch"
LEDGER_SCHEMA = "benchbox_batch_ledger_v1"
EVIDENCE_SCHEMA = "benchbox_batch_evidence_v2"
CHECK_SCHEMA = "benchbox_member_check_v1"
MEMBER_REVIEW_SCHEMA = "benchbox_member_review_v1"
ACCEPTED_REVIEW_OUTCOMES = ("clear", "not-reviewed", "override")
PREFLIGHT_EXIT_REVIEWABLE = 0
PREFLIGHT_EXIT_NOT_REVIEWABLE = 3
PREFLIGHT_REVIEWABLE = "reviewable"
PREFLIGHT_NOT_REVIEWABLE = "not-reviewable"
PREFLIGHT_UNDETERMINED = "undetermined"
TRACTABLE_COMMAND = ["uv", "run", "--project", "_project/scripts", "--locked", "--", "tractable"]
DEFAULT_PREFLIGHT_COMMAND = ["make", "pr-preflight"]
REVIEW_INTERPRETER = "python3"
REVIEW_WRAPPER = "scripts/oracle_local_review.py"
REVIEW_SUPPORT = ("scripts/theseus_cli.py",)
COMPANION_SCOPE = ("changelog.d/*", "docs/internal/dev-loop-property-ledger.md")
FINAL_SUITE = "member-reruns"
LEADER_MEMBER_OPS = frozenset({"take", "release", "renew", "recover"})
POLICY_PATH = ".github/oracle-reviewers.yml"
SECTION_BUDGET = 6000
REVIEW_PRINT_LIMIT = 16000
ALLOWED_VERIFICATION_PREFIXES = (
    ("uv", "run", "--", "python", "-m", "pytest"),
    ("uv", "run", "--", "ruff", "check"),
)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


class BatchError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(argv: list[str], cwd: Path, *, env: dict[str, str] | None = None, timeout: int | None = 3600):
    return subprocess.run(
        argv, cwd=cwd, check=False, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, env=env
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=False, encoding="utf-8", errors="replace", capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise BatchError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _git_raw(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise BatchError(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout


def _git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, timeout=120).returncode == 0


def _config_set(repo: Path, key: str, value: str) -> None:
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", f"{CONFIG_PREFIX}.{key}", value)


def _config_get(repo: Path, key: str) -> str | None:
    proc = subprocess.run(
        ["git", "config", "--worktree", "--get", f"{CONFIG_PREFIX}.{key}"],
        cwd=repo,
        check=False,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git_ok(repo, "merge-base", "--is-ancestor", ancestor, descendant)


def _is_clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""


def _changed_files(repo: Path, base: str, head: str) -> list[str]:
    raw = _git_raw(repo, "diff", "--no-renames", "--name-only", "-z", f"{base}..{head}")
    return sorted({os.fsdecode(part) for part in raw.split(b"\0") if part})


def _branch(repo: Path) -> str:
    return _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")


def _validate_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value) or ".." in value or "--" in value:
        raise BatchError(f"{label} {value!r} is not a valid identifier")
    return value


def integration_branch_name(batch_id: str) -> str:
    return f"feat/{batch_id}"


def member_branch_name(batch_id: str, member: str) -> str:
    return f"feat/{batch_id}--{member}"


def _repo_slug(url: str) -> str:
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    if match is None:
        raise BatchError(f"cannot derive an owner/name repository from {url!r}")
    return match.group(1)


def canonical_repository(value: str) -> str:
    text = value.strip()
    bare = re.fullmatch(r"([^/:\s]+/[^/:\s]+?)(?:\.git)?/?", text)
    return (bare.group(1) if bare else _repo_slug(text)).casefold()


def _primary_name(repo: Path) -> str:
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent.name


def _worktree_for_branch(repo: Path, branch: str) -> Path | None:
    out = _git(repo, "worktree", "list", "--porcelain")
    path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return Path(path)
    return None


def role_of(repo: Path) -> str | None:
    return _config_get(repo, "role")


def require_role(repo: Path, role: str) -> str:
    actual = role_of(repo)
    if actual != role:
        raise BatchError(f"this command runs only in a role={role} worktree; this worktree has role={actual or 'none'}")
    batch_id = _config_get(repo, "id")
    if batch_id is None:
        raise BatchError("worktree has no batch id recorded")
    return batch_id


def scope_digest(scope: dict[str, list[str]]) -> str:
    blob = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _matches_scope(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _with_companions(patterns: list[str]) -> list[str]:
    return [*patterns, *(pattern for pattern in COMPANION_SCOPE if pattern not in patterns)]


def _refuse_outside_scope(changed: list[str], patterns: list[str]) -> None:
    outside = [path for path in changed if not _matches_scope(path, patterns)]
    if outside:
        raise BatchError(f"member changes paths outside its frozen scope: {', '.join(outside[:5])}")


def _primary_checkout(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent


def primary_scope_edits(repo: Path, patterns: list[str]) -> list[str]:
    primary = _primary_checkout(repo)
    if primary.resolve() == repo.resolve() or not (primary / ".git").is_dir():
        return []
    tokens = _git_raw(primary, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(tokens) and tokens[index]:
        entry = os.fsdecode(tokens[index])
        paths.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            paths.add(os.fsdecode(tokens[index + 1]))
            index += 2
        else:
            index += 1
    return sorted(path for path in paths if _matches_scope(path, patterns))


def _refuse_primary_edits(repo: Path, member: str, patterns: list[str]) -> None:
    edits = primary_scope_edits(repo, patterns)
    if edits:
        raise BatchError(
            f"the primary clone {_primary_checkout(repo)} has uncommitted changes in member {member!r}'s scope: "
            f"{', '.join(edits[:10])}; move them into the member worktree or remove them, then run this again"
        )


def primary_tracked_edits(repo: Path) -> list[str]:
    primary = _primary_checkout(repo)
    if primary.resolve() == repo.resolve() or not (primary / ".git").is_dir():
        return []
    tokens = _git_raw(primary, "status", "--porcelain=v1", "-z", "--untracked-files=no").split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(tokens) and tokens[index]:
        entry = os.fsdecode(tokens[index])
        paths.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            paths.add(os.fsdecode(tokens[index + 1]))
            index += 2
        else:
            index += 1
    return sorted(paths)


def _refuse_dirty_primary(repo: Path) -> None:
    edits = primary_tracked_edits(repo)
    if edits:
        shown = ", ".join(edits[:10]) + (f" and {len(edits) - 10} more" if len(edits) > 10 else "")
        raise BatchError(
            f"the primary clone {_primary_checkout(repo)} has uncommitted tracked changes: {shown}; "
            "a session edited it through a path that resolves to the primary clone instead of its own worktree. "
            "Move each change into the worktree that owns it (git -C <primary> diff -- <file> | git -C <worktree> apply, "
            "then git -C <primary> checkout -- <file>) and run this again"
        )


def _ledger_dir(repo: Path, batch_id: str) -> Path:
    return repo / LEDGER_ROOT / batch_id


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    scratch.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def _ledger_path(repo: Path, batch_id: str) -> Path:
    return _ledger_dir(repo, batch_id) / "ledger.json"


def load_ledger(repo: Path, batch_id: str) -> dict | None:
    path = _ledger_path(repo, batch_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BatchError(f"ledger {path} is unreadable; delete it and run batch-status to rebuild") from exc
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
        raise BatchError(f"ledger {path} has an unknown schema; delete it and run batch-status to rebuild")
    return data


def save_ledger(repo: Path, ledger: dict) -> None:
    _write_json(_ledger_path(repo, ledger["batch_id"]), ledger)


def _tractable_json(repo: Path, args: list[str]) -> dict:
    argv = [*TRACTABLE_COMMAND, *args]
    try:
        proc = _run(argv, repo, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BatchError(f"tractable could not run ({exc}); the tracker read failed: {' '.join(argv)}") from exc
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        envelope = None
    if proc.returncode != 0 or not isinstance(envelope, dict) or envelope.get("ok") is not True:
        detail = ""
        if isinstance(envelope, dict):
            detail = f" {envelope.get('code', '')}: {envelope.get('error', '')} {envelope.get('recovery', '')}"
        raise BatchError(f"tracker read failed (exit {proc.returncode}){detail or ' ' + proc.stderr.strip()[:300]}")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise BatchError("tracker read returned no data object")
    return data


def read_tracker(repo: Path, batch_id: str) -> dict:
    summary = _tractable_json(repo, ["batch", "show", batch_id])
    capability = summary.get("capability")
    if not isinstance(capability, dict) or int(capability.get("version") or 0) < 2:
        raise BatchError("the tractable runtime lacks the batch-v2 capability; use the pinned runtime")
    revision = summary.get("rev")
    if summary.get("stale") or summary.get("cached_rev") is not None or not revision:
        raise BatchError("tracker read is stale or carries no revision (offline cache); reconnect and retry")
    sections = summary.get("sections") if isinstance(summary.get("sections"), dict) else {}
    tracker = {key: value for key, value in summary.items() if key not in ("sections", "rev")}
    for name, info in sections.items():
        if name in summary:
            tracker[name] = summary[name]
            continue
        window = ""
        offset = 0
        while True:
            args = ["batch", "show", batch_id, "--field", name, "--offset", str(offset)]
            args += ["--budget", str(SECTION_BUDGET), "--rev", str(revision)]
            page = _tractable_json(repo, args)
            window += str(page.get("window", ""))
            continuation = page.get("continuation")
            if not isinstance(continuation, dict):
                break
            offset = int(continuation["offset"])
        try:
            tracker[name] = json.loads(window) if window else []
        except ValueError as exc:
            raise BatchError(f"tracker section {name!r} could not be assembled from its pages") from exc
    tracker["_rev"] = revision
    return tracker


def tracker_heads(tracker: dict) -> list[str]:
    return [str(entry["head"]) for entry in tracker.get("integration_log", []) if isinstance(entry, dict)]


def _members_in_order(tracker: dict) -> list[str]:
    members = tracker.get("members", [])
    return [str(m["id"]) if isinstance(m, dict) else str(m) for m in members]


def _receipts(tracker: dict) -> dict[str, dict]:
    receipts = tracker.get("receipts", {})
    return receipts if isinstance(receipts, dict) else {}


def _pending_mergecommit(repo: Path, member_head: str) -> str | None:
    out = _git(repo, "rev-list", "--first-parent", "--merges", "--parents", "HEAD")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == member_head:
            return parts[0]
    return None


def _checkout_root_modules():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def non_python_paths(files: list[str]) -> list[str]:
    return [path for path in files if not path.endswith(".py") and not path.startswith("changelog.d/")]


def _validate_trusted_sha(repo: Path, trusted: str, start_head: str | None = None) -> str:
    if not (
        bool(FULL_SHA.fullmatch(trusted)) and _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{trusted}^{{commit}}")
    ):
        raise BatchError(f"trusted ref {trusted!r} is not a valid commit SHA")
    if not _git_ok(repo, "merge-base", "--is-ancestor", trusted, DEVELOP_REF):
        raise BatchError("the recorded trusted commit is not an ancestor of origin/develop")
    if start_head and not _git_ok(repo, "merge-base", "--is-ancestor", start_head, trusted):
        raise BatchError("the recorded trusted commit predates the batch start_head")
    return trusted


def _review_refusal(repo: Path, member_head: str, proc) -> str:
    full = "\n".join(part.strip() for part in (proc.stderr, proc.stdout) if part and part.strip())
    path = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / f"member-review-{member_head[:12]}.txt"
    path.write_text(full + "\n", encoding="utf-8")
    shown = full if len(full) <= REVIEW_PRINT_LIMIT else f"{full[:REVIEW_PRINT_LIMIT]}\n[truncated]"
    return f"member review did not clear this head (full output: {path}):\n{shown}"


def _review_member_patch(repo: Path, base: str, member_head: str, trusted: str) -> dict:
    with tempfile.TemporaryDirectory() as scratch:
        wrapper = Path(scratch) / Path(REVIEW_WRAPPER).name
        wrapper.write_bytes(_git_raw(repo, "show", f"{trusted}:{REVIEW_WRAPPER}"))
        for support in REVIEW_SUPPORT:
            (Path(scratch) / Path(support).name).write_bytes(_git_raw(repo, "show", f"{trusted}:{support}"))
        result_path = Path(scratch) / "member-review.json"
        cmd = [
            REVIEW_INTERPRETER,
            str(wrapper),
            "--policy",
            POLICY_PATH,
            "--member",
            "--result-json",
            str(result_path),
            "--base-ref",
            base,
            "--trusted-ref",
            trusted,
            "--head",
            member_head,
        ]
        proc = _run(cmd, repo)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
        except (OSError, ValueError):
            result = None
        if proc.returncode != 0:
            raise BatchError(_review_refusal(repo, member_head, proc))
        if result is None:
            raise BatchError("member review wrote no readable result")

    if (
        not isinstance(result, dict)
        or result.get("schema") != MEMBER_REVIEW_SCHEMA
        or result.get("outcome") not in ACCEPTED_REVIEW_OUTCOMES
        or result.get("base_sha") != base
        or result.get("head_sha") != member_head
        or result.get("trusted_sha") != trusted
    ):
        raise BatchError("member review result does not bind this base and head or is not accepted")
    return result


def _trusted_develop(repo: Path) -> str:
    try:
        _git(repo, "fetch", "origin", "develop", "--quiet")
    except BatchError as exc:
        raise BatchError(f"cannot pin a trusted develop commit: {exc}") from exc
    return _rev(repo, DEVELOP_REF)


def _develop_is_trusted(repo: Path, sha: str, start_head: str | None = None) -> bool:
    try:
        _validate_trusted_sha(repo, sha, start_head)
        return True
    except BatchError:
        return False


def start_batch(
    repo: Path,
    batch_id: str,
    members: list[str],
    scope: dict[str, list[str]],
    *,
    project_id: str,
    worktree_path: Path | None = None,
    delivery_boundary: str = "final-pr",
    terminal_outcome: str = "deliver",
    confirm_cutover: bool = False,
    dependencies: dict[str, list[str]] | None = None,
) -> dict:
    _validate_id(batch_id, "batch")
    if len(members) < 2:
        raise BatchError(
            "a batch needs at least two members; one member is serial work, so use a standalone branch and PR"
        )
    if len(set(members)) != len(members):
        raise BatchError("members must be a non-empty list without duplicates")
    for member in members:
        _validate_id(member, "member")
    if set(scope) != set(members) or any(
        not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns)
        for patterns in scope.values()
    ):
        raise BatchError("scope must map every member, and only members, to a non-empty list of path patterns")
    dependencies = _validate_dependencies(members, dependencies or {})
    follow_up = _member_metadata_steps(repo, batch_id, members, dependencies)
    _trusted_develop(repo)
    origin = _git(repo, "remote", "get-url", "origin")
    branch = integration_branch_name(batch_id)
    path = (worktree_path or repo.resolve().parent / f"{_primary_name(repo)}.wt-{batch_id}").resolve()
    proc = _run(
        ["make", "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={path}", f"BASE={DEVELOP_REF}"],
        repo,
    )
    if proc.returncode != 0:
        raise BatchError(f"worktree-create failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    if not path.is_dir():
        raise BatchError(f"worktree-create did not produce {path}")
    start_head = _rev(path, "HEAD")
    generation = secrets.token_hex(16)
    _config_set(path, "role", ROLE_INTEGRATION)
    _config_set(path, "id", batch_id)
    scope = {member: _with_companions(patterns) for member, patterns in scope.items()}
    scope_hash = scope_digest(scope)
    ledger = {
        "schema": LEDGER_SCHEMA,
        "batch_id": batch_id,
        "project_id": project_id,
        "repository": origin,
        "integration_branch": branch,
        "integration_worktree": str(path),
        "start_head": start_head,
        "develop_ref": DEVELOP_REF,
        "delivery_boundary": delivery_boundary,
        "terminal_outcome": terminal_outcome,
        "members": list(members),
        "scope": scope,
        "scope_hash": scope_hash,
        "owner_generation": generation,
        "landings": {},
        "advances": [],
        "acceptance": {},
        "created_at": _now(),
    }
    save_ledger(path, ledger)
    arguments = {
        "batch_id": batch_id,
        "project_id": project_id,
        "repository": origin,
        "owner_generation": generation,
        "integration_branch": branch,
        "integration_worktree": str(path),
        "start_head": start_head,
        "members": list(members),
        "scope": scope,
        "scope_hash": scope_hash,
        "delivery_boundary": delivery_boundary,
        "terminal_outcome": terminal_outcome,
        "develop_ref": DEVELOP_REF,
    }
    if confirm_cutover:
        arguments["confirm_schema3_cutover"] = True
    return {
        "worktree": str(path),
        "tool": "register_batch",
        "arguments": arguments,
        "after_register": follow_up,
    }


def parse_dependencies(text: str | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for entry in _parse_csv(text):
        member, separator, deps = entry.partition("=")
        member = member.strip()
        if not separator or not member or member in result:
            raise BatchError(f"dependency entry {entry!r} must read member=dep1+dep2 and name each member once")
        result[member] = [dep.strip() for dep in deps.split("+") if dep.strip()]
        if not result[member]:
            raise BatchError(f"dependency entry {entry!r} names no dependency")
    return result


def _validate_dependencies(members: list[str], dependencies: dict[str, list[str]]) -> dict[str, list[str]]:
    for member, deps in dependencies.items():
        if member not in members or any(dep not in members for dep in deps):
            raise BatchError(f"dependencies of {member!r} must name registered members only: {deps}")
        if member in deps or len(set(deps)) != len(deps):
            raise BatchError(f"dependencies of {member!r} must be unique and not include the member itself")
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise BatchError(f"implementation dependencies form a cycle through {node!r}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in dependencies.get(node, []):
            visit(dep)
        state[node] = 2

    for member in members:
        visit(member)
    return dependencies


def _member_metadata_steps(repo: Path, batch_id: str, members: list[str], dependencies: dict[str, list[str]]) -> dict:
    steps = []
    reminders = []
    for member in members:
        shown = _tractable_json(repo, ["show", member]).get("needs")
        current = shown.get("values") if isinstance(shown, dict) else None
        if not isinstance(current, list) or shown.get("total") != len(current):
            raise BatchError(f"the tracker returned no complete needs list for {member!r}")
        deps = dependencies.get(member, [])
        needs = list(dict.fromkeys([*(str(need) for need in current), *deps]))
        steps.append(
            {
                "tool": "update_item",
                "arguments": {
                    "id": member,
                    "needs": needs,
                    "batch": {"batch_id": batch_id, "member_id": member, "implementation_dependencies": deps},
                },
            }
        )
        if deps:
            reminders.append(
                f"member {member!r} depends on {deps}: take it before batch-land, because the tracker refuses "
                "to take it once the integration head moves past the recorded head"
            )
    return {
        "label": "run these update_item steps after register_batch, in this order",
        "steps": steps,
        "reminders": reminders,
    }


def create_member(repo: Path, member: str, worktree_path: Path | None = None) -> dict:
    _refuse_dirty_primary(repo)
    batch_id = require_role(repo, ROLE_INTEGRATION)
    ledger = _require_ledger(repo, batch_id)
    if member not in ledger["members"]:
        raise BatchError(f"{member!r} is not a registered member of batch {batch_id!r}")
    if _branch(repo) != ledger["integration_branch"]:
        raise BatchError(f"integration worktree is not on {ledger['integration_branch']}")
    head = _rev(repo, "HEAD")
    sessions_mark = _last_op_id(_item_sessions(repo, member))
    branch = member_branch_name(batch_id, member)
    if _git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise BatchError(f"branch {branch} already exists; refusing to recreate member {member!r}")
    path = (worktree_path or repo.resolve().parent / f"{_primary_name(repo)}.wt-{batch_id}--{member}").resolve()
    proc = _run(["make", "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={path}", f"BASE={head}"], repo)
    if proc.returncode != 0:
        raise BatchError(f"worktree-create failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    if not path.is_dir():
        raise BatchError(f"worktree-create did not produce {path}")
    if _rev(path, "HEAD") != head:
        raise BatchError(f"member worktree did not start at the integration head {head}")
    _config_set(path, "role", ROLE_MEMBER)
    _config_set(path, "id", batch_id)
    _config_set(path, "member", member)
    _config_set(path, "source-base", head)
    _config_set(path, "created-at", _now())
    if sessions_mark is not None:
        _config_set(path, "sessions-mark", sessions_mark)
    return {"worktree": str(path), "branch": branch, "batch_id": batch_id, "member": member, "source_base": head}


def _require_ledger(repo: Path, batch_id: str) -> dict:
    ledger = load_ledger(repo, batch_id)
    if ledger is None:
        raise BatchError(f"no ledger for batch {batch_id!r}; run batch-status to rebuild it from the tracker and Git")
    return ledger


def _verification_command(repo: Path, base: str, tests: list[str], changed: list[str]) -> tuple[str, list[str]]:
    proc = _run(
        ["uv", "run", "--", "python", "_project/scripts/preflight_targets.py", "tests", "--base-ref", base], repo
    )
    mapped = sorted(line.strip() for line in proc.stdout.splitlines() if line.strip()) if proc.returncode == 0 else []
    all_tests = sorted(set(mapped) | set(tests))
    if all_tests:
        return "member-tests", ["uv", "run", "--", "python", "-m", "pytest", "-n", "0", "-q", *all_tests]
    python_files = [path for path in changed if path.endswith(".py") and (repo / path).is_file()]
    if python_files:
        return "member-lint", ["uv", "run", "--", "ruff", "check", "--force-exclude", *python_files]
    raise BatchError("no tests map to this change and no Python file changed; name the tests with TESTS=")


def _validate_verification_command(command: object) -> list[str]:
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
        or not any(tuple(command[: len(prefix)]) == prefix for prefix in ALLOWED_VERIFICATION_PREFIXES)
    ):
        raise BatchError(f"verification command {command!r} is not a recognised pytest or ruff invocation")
    return list(command)


def check_member(
    repo: Path,
    tests: list[str],
    *,
    preflight_command: list[str] | None = None,
) -> dict:
    batch_id = require_role(repo, ROLE_MEMBER)
    member = _config_get(repo, "member")
    if member is None:
        raise BatchError("member worktree has no member id recorded")
    branch = integration_branch_name(batch_id)
    if not _git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise BatchError(f"integration branch {branch} is not visible from this repository")
    integration_head = _rev(repo, f"refs/heads/{branch}")
    if not _is_clean(repo):
        raise BatchError("member worktree is not clean; commit or remove changes first")
    integration = _worktree_for_branch(repo, branch)
    ledger = load_ledger(integration, batch_id) if integration is not None else None
    patterns = ledger.get("scope", {}).get(member) if isinstance(ledger, dict) else None
    if not isinstance(patterns, list) or not patterns:
        raise BatchError(f"the integration worktree's ledger records no frozen scope for member {member!r}")
    _refuse_primary_edits(repo, member, patterns)
    _refuse_dirty_primary(repo)
    member_head = _rev(repo, "HEAD")
    if not _is_ancestor(repo, integration_head, member_head):
        raise BatchError(
            f"member does not contain the current integration head {integration_head}; "
            f"merge {branch} into this branch, resolve conflicts, then run batch-member-check again"
        )
    changed = _changed_files(repo, integration_head, member_head)
    if not changed:
        raise BatchError("member has no changes over the integration head")
    _refuse_outside_scope(changed, patterns)
    unnamed = non_python_paths(changed)
    if unnamed and not tests:
        raise BatchError(f"non-Python changes need named tests (TESTS=...): {', '.join(unnamed[:5])}")
    for test in tests:
        if not (repo / test.split("::", 1)[0]).exists():
            raise BatchError(f"named test {test!r} does not exist in this worktree")
    preflight = [*(preflight_command or DEFAULT_PREFLIGHT_COMMAND), f"BASE={integration_head}"]
    proc = _run(preflight, repo)
    if proc.returncode != 0:
        raise BatchError(f"preflight failed: {(proc.stderr or proc.stdout).strip()[-400:]}")
    proc = _run(["make", "-s", "changelog-check"], repo)
    if proc.returncode != 0:
        raise BatchError(f"changelog check failed: {(proc.stderr or proc.stdout).strip()[-400:]}")
    suite, command = _verification_command(repo, integration_head, tests, changed)
    v_proc = _run(command, repo)
    if v_proc.returncode != 0:
        raise BatchError(f"member verification failed: {(v_proc.stderr or v_proc.stdout).strip()[-400:]}")
    trusted = _trusted_develop(repo)
    if _rev(repo, "HEAD") != member_head or not _is_clean(repo):
        raise BatchError("member worktree changed while verification ran; run batch-member-check again")
    record = {
        "schema": CHECK_SCHEMA,
        "batch_id": batch_id,
        "member": member,
        "member_head": member_head,
        "integration_head": integration_head,
        "preflight": preflight,
        "trusted_sha": trusted,
        "verification": {"suite": suite, "command": command},
        "tests": tests,
        "checked_at": _now(),
    }
    _config_set(repo, f"check.{member_head}.at-{integration_head}", json.dumps(record, sort_keys=True))
    return record


def _read_check(member_worktree: Path, member_head: str, integration_head: str) -> dict | None:
    raw = _config_get(member_worktree, f"check.{member_head}.at-{integration_head}")
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    return record if isinstance(record, dict) and record.get("schema") == CHECK_SCHEMA else None


def _rerun(
    repo: Path, ledger: dict, member: str, command: list[str], suite: str, reason: str, tracker: dict | None = None
) -> dict:
    head = _rev(repo, "HEAD")
    clean_before = _is_clean(repo)
    recorded = _validate_verification_command(command)
    ran_command, dropped_paths, exhausted = _develop_deleted_rerun(repo, head, recorded, tracker)
    proc = None if exhausted else _run(ran_command, repo)
    head_after = _rev(repo, "HEAD")
    clean_after = _is_clean(repo)
    passed = proc is not None and proc.returncode == 0 and clean_before and clean_after and head_after == head
    entry = {
        "status": "passed" if passed else "failed",
        "revision": head,
        "clean": clean_before and clean_after,
        "suite": suite,
        "command": list(command),
        "ran_command": ran_command,
        "dropped_paths": dropped_paths,
        "worktree": str(repo.resolve()),
        "reason": reason,
        "at": _now(),
    }
    if proc is None:
        entry["output"] = (
            f"every verification path in member {member!r}'s receipt was removed by a recorded develop merge "
            f"({', '.join(dropped_paths)}); refusing to run pytest without them; re-prepare the member with "
            "current verification"
        )
    elif proc.returncode != 0:
        entry["output"] = (proc.stderr or proc.stdout).strip()[-400:]
    elif not passed:
        entry["output"] = (
            f"the checkout changed during the run (HEAD {head[:12]} -> {head_after[:12]}, clean={clean_after})"
        )
    ledger.setdefault("acceptance", {}).setdefault(member, []).append(entry)
    return entry


def _landing_verification(ledger: dict, member: str, receipts: dict) -> tuple[str, list[str]]:
    receipt = receipts.get(member)
    if isinstance(receipt, dict) and isinstance(receipt.get("verification"), dict):
        verification = receipt["verification"]
        return str(verification.get("suite", "")), _validate_verification_command(verification.get("command"))
    landing = ledger["landings"].get(member)
    if landing is None:
        raise BatchError(f"no recorded verification command for member {member!r}")
    verification = landing["verification"]
    return str(verification["suite"]), _validate_verification_command(verification["command"])


def hunk_warnings(repo: Path, ledger: dict, exclude: str) -> list[str]:
    warnings: list[str] = []
    for member, landing in ledger["landings"].items():
        if member == exclude or not landing.get("source_base") or not landing.get("member_head"):
            continue
        patch = _git_raw(repo, "diff", "--no-renames", "-U0", landing["source_base"], landing["member_head"])
        if not patch:
            continue
        proc = subprocess.run(
            ["git", "apply", "-R", "--check", "--unidiff-zero", "-"],
            cwd=repo,
            input=patch,
            check=False,
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0:
            warnings.append(
                f"member {member!r}: some of its hunks are no longer present in the landed head "
                f"(advisory; the overlap rerun is the guard): {proc.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )
    return warnings


def _prepare_payload(ledger: dict, member: str, landing: dict, member_worktree: str, dependencies: list[str]) -> dict:
    review = landing.get("review")
    if not isinstance(review, dict) or review.get("schema") != MEMBER_REVIEW_SCHEMA:
        raise BatchError(f"member {member!r} landing has no accepted leader review; land it again")
    take = [] if dependencies else [{"tool": "take", "arguments": {"id": member}}]
    generation = (
        "<generation returned by the take made before landing>" if dependencies else "<generation returned by take>"
    )
    return {
        "steps": [
            *take,
            {
                "tool": "prepare",
                "arguments": {
                    "id": member,
                    "generation": generation,
                    "batch_id": ledger["batch_id"],
                    "member_id": member,
                    "source_worktree": member_worktree,
                    "source_revision": landing["member_head"],
                    "source_base": landing["source_base"],
                    "accepted_head": landing["member_head"],
                    "integration_head": landing["merge_head"],
                    "scope_hash": ledger["scope_hash"],
                    "verification": {
                        "status": "passed",
                        "revision": landing["member_head"],
                        "clean": True,
                        "suite": landing["verification"]["suite"],
                        "command": landing["verification"]["command"],
                        "review": review,
                    },
                    "implementation_dependencies": dependencies,
                },
            },
        ]
    }


def _claimed_by_owner(tracker: dict, member: str) -> bool:
    owner = tracker.get("owner")
    for row in tracker.get("members", []):
        if isinstance(row, dict) and row.get("id") == member:
            return owner is not None and row.get("claim_worker") == owner
    return False


def _member_landing_facts(repo: Path, ledger: dict, member: str) -> dict:
    batch_id = ledger["batch_id"]
    branch = member_branch_name(batch_id, member)
    worktree = _worktree_for_branch(repo, branch)
    if worktree is None:
        raise BatchError(f"no worktree has branch {branch} checked out")
    if (
        role_of(worktree) != ROLE_MEMBER
        or _config_get(worktree, "member") != member
        or _config_get(worktree, "id") != batch_id
    ):
        raise BatchError(f"worktree {worktree} is not the recorded member worktree for {member!r}")
    member_head = _rev(worktree, "HEAD")
    if _rev(repo, f"refs/heads/{branch}") != member_head:
        raise BatchError("member branch and member worktree disagree about the head")
    return {"branch": branch, "worktree": worktree, "member_head": member_head}


def _already_merged(
    repo: Path, ledger: dict, tracker: dict, member: str, facts: dict, head: str, dependencies: list[str]
) -> dict | None:
    existing = ledger["landings"].get(member)
    pending = _pending_mergecommit(repo, facts["member_head"])
    if existing is None and pending is None:
        return None
    landing = existing or _reconstruct_landing(repo, ledger, tracker, member, facts, pending)
    if not landing.get("merge_head") or not _is_ancestor(repo, landing["merge_head"], head):
        return None
    ledger["landings"][member] = landing
    save_ledger(repo, ledger)
    receipts = _receipts(tracker)
    earlier = {key: value for key, value in ledger["landings"].items() if key != member}
    reruns, failures = _overlap_reruns(
        repo, ledger, receipts, tracker, earlier, member, landing.get("changed_files", [])
    )
    save_ledger(repo, ledger)
    if failures:
        raise BatchError(
            f"member {member!r} is merged at {landing['merge_head']} but the overlap rerun failed for {failures}; "
            f"reset the integration branch to {landing.get('source_base')} and land again once the combination "
            f"passes; do not prepare {member!r} until the reruns pass"
        )
    if head != landing["merge_head"] and head != tracker.get("integration_head"):
        raise BatchError(f"unrecorded commits after the merge of {member}; record them with batch-advance first")
    bound = {**landing, "merge_head": head}
    return {
        "status": "already-merged",
        "message": (
            f"member {member!r} is already merged at {landing['merge_head']} but has no prepared receipt; "
            "submit the prepare payload below (do not merge again)"
        ),
        "payload": _prepare_payload(ledger, member, bound, str(facts["worktree"]), dependencies),
    }


def _review_and_scope(
    repo: Path, ledger: dict, member: str, base: str, member_head: str, trusted: str
) -> tuple[dict, list[str]]:
    review = _review_member_patch(repo, base, member_head, trusted)
    changed = _changed_files(repo, base, member_head)
    if not changed:
        raise BatchError("member has no changes over the integration head")
    _refuse_outside_scope(changed, ledger["scope"][member])
    return review, changed


def _landing_preconditions(repo: Path, ledger: dict, tracker: dict, member: str, facts: dict, head: str) -> dict:
    if not _is_clean(facts["worktree"]):
        raise BatchError("member worktree is not clean")
    if head not in tracker_heads(tracker):
        raise BatchError(
            f"integration head {head} is not a recorded head; prepare the previously merged member "
            "or record the leader commit with batch-advance first (see batch-status)"
        )
    if tracker.get("integration_head") != head:
        raise BatchError(
            f"tracker integration head {tracker.get('integration_head')} differs from {head}; run batch-status"
        )
    check = _read_check(facts["worktree"], facts["member_head"], head)
    if check is None:
        raise BatchError(
            "the integration head moved since the member synced, or the member never ran batch-member-check at "
            f"this head: merge {ledger['integration_branch']} into the member and run batch-member-check again"
        )
    if not _is_ancestor(repo, head, facts["member_head"]):
        raise BatchError("member does not contain the current integration head")
    trusted = str(check.get("trusted_sha", ""))
    start_head = str(ledger.get("start_head", ""))
    if not _develop_is_trusted(repo, trusted, start_head):
        raise BatchError("the recorded trusted commit is not an ancestor of origin/develop or predates start_head")
    review, changed = _review_and_scope(repo, ledger, member, head, facts["member_head"], trusted)
    return {
        "trusted": trusted,
        "review": review,
        "changed": changed,
        "preflight": [str(part) for part in check.get("preflight", [])],
        "verification": {
            "suite": check["verification"]["suite"],
            "command": _validate_verification_command(check["verification"]["command"]),
        },
    }


def _theseus_launcher(repo: Path) -> tuple[Path, dict[str, str]]:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import theseus_cli

    try:
        program = theseus_cli.executable(repo, DEVELOP_REF)
    except theseus_cli.TheseusError as exc:
        raise BatchError(f"the Theseus install for the review preflight was refused: {exc}") from exc
    return program, theseus_cli.launcher_environment(program)


def review_preflight(repo: Path, head: str) -> dict:
    try:
        program, env = _theseus_launcher(repo)
    except BatchError as exc:
        return {"state": PREFLIGHT_UNDETERMINED, "exit": None, "reasons": [str(exc)], "advisories": []}
    command = [
        str(program),
        "preflight",
        "--policy",
        POLICY_PATH,
        "--base-ref",
        DEVELOP_REF,
        "--head",
        head,
        "--json",
    ]
    proc = _run(command, repo, env=env)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    valid = (
        isinstance(payload, dict)
        and isinstance(payload.get("reasons"), list)
        and isinstance(payload.get("advisories"), list)
        and all(isinstance(item, str) for item in payload["reasons"] + payload["advisories"])
    )
    reasons = list(payload["reasons"]) if valid else []
    advisories = list(payload["advisories"]) if valid else []
    if proc.returncode == PREFLIGHT_EXIT_REVIEWABLE and valid:
        state = PREFLIGHT_REVIEWABLE
    elif proc.returncode == PREFLIGHT_EXIT_NOT_REVIEWABLE and valid:
        state = PREFLIGHT_NOT_REVIEWABLE
    else:
        state = PREFLIGHT_UNDETERMINED
        if not reasons:
            detail = (proc.stderr or proc.stdout or "no output").strip()[-400:]
            reasons = [f"the Theseus preflight exited {proc.returncode} without a usable verdict: {detail}"]
    return {"state": state, "exit": proc.returncode, "reasons": reasons, "advisories": advisories}


def _preflight_gate(repo: Path, head: str) -> list[str]:
    result = review_preflight(repo, head)
    if result["state"] == PREFLIGHT_REVIEWABLE:
        return list(result["advisories"])
    if result["state"] == PREFLIGHT_NOT_REVIEWABLE:
        lead = f"the change at {head[:12]} is not reviewable:"
    else:
        lead = f"the review preflight could not determine whether the change at {head[:12]} is reviewable:"
    raise BatchError("\n".join([lead, *result["reasons"]]))


def _overlap_reruns(
    repo: Path, ledger: dict, receipts: dict, tracker: dict, earlier: dict, member: str, changed: list[str]
) -> tuple[list[dict], list[str]]:
    reruns: list[dict] = []
    failures: list[str] = []
    for other, other_landing in earlier.items():
        shared = sorted(set(changed) & set(other_landing.get("changed_files", [])))
        if not shared:
            continue
        suite, command = _landing_verification(ledger, other, receipts)
        entry = _rerun(repo, ledger, other, command, suite, f"overlap:{member}", tracker)
        reruns.append({"member": other, "paths": shared, "status": entry["status"]})
        if entry["status"] != "passed":
            failures.append(other)
    return reruns, failures


def registered_dependencies(repo: Path, batch_id: str, member: str) -> list[str]:
    batch = _tractable_json(repo, ["show", member]).get("batch")
    if not isinstance(batch, dict) or batch.get("batch_id") != batch_id or batch.get("member_id") != member:
        raise BatchError(f"the tracker carries no batch metadata for member {member!r} of batch {batch_id!r}")
    dependencies = batch.get("implementation_dependencies")
    if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
        raise BatchError(f"the tracker returned no implementation dependency list for member {member!r}")
    return list(dependencies)


def _item_sessions(repo: Path, item: str) -> list[dict]:
    entries: list[dict] = []
    offset = 0
    revision: str | None = None
    while True:
        args = ["show", item, "--field", "sessions", "--offset", str(offset), "--budget", str(SECTION_BUDGET)]
        page = _tractable_json(repo, [*args, "--rev", revision] if revision else args)
        if page.get("stale") or page.get("cached_rev") is not None or not page.get("rev"):
            raise BatchError("tracker read is stale or carries no revision (offline cache); reconnect and retry")
        revision = str(page["rev"])
        window = page.get("window")
        if not isinstance(window, list):
            raise BatchError(f"the tracker returned no session history for {item!r}")
        entries.extend(entry for entry in window if isinstance(entry, dict))
        continuation = page.get("continuation")
        if not isinstance(continuation, dict):
            return entries
        offset = int(continuation["offset"])


def _op_position(entries: list[dict], op_id: str | None) -> int | None:
    if op_id is None:
        return None
    for index, entry in enumerate(entries):
        if entry.get("op_id") == op_id:
            return index
    return None


def _last_op_id(entries: list[dict]) -> str | None:
    return str(entries[-1].get("op_id")) if entries and entries[-1].get("op_id") else None


def member_tracker_writes(
    entries: list[dict],
    since: str,
    until: str | None = None,
    leader_op_ids: frozenset[str] = frozenset(),
    after: str | None = None,
    through: str | None = None,
) -> list[dict]:
    first = _op_position(entries, after)
    last = _op_position(entries, through)
    window = entries[(0 if first is None else first + 1) : (len(entries) if last is None else last + 1)]
    return [
        entry
        for entry in window
        if since <= str(entry.get("at", ""))
        and (until is None or str(entry.get("at", "")) <= until)
        and entry.get("op") not in LEADER_MEMBER_OPS
        and entry.get("op_id") not in leader_op_ids
    ]


def _member_log_entries(tracker: dict, member: str) -> list[dict]:
    return [
        entry
        for entry in tracker.get("integration_log", [])
        if isinstance(entry, dict) and entry.get("kind") == "prepare" and entry.get("member") == member
    ]


def _first_landing(ledger: dict, tracker: dict, member: str) -> str | None:
    times = [ledger["landings"].get(member, {}).get("landed_at")]
    times += [entry.get("at") for entry in _member_log_entries(tracker, member)]
    present = [str(time) for time in times if time]
    return min(present) if present else None


def _check_member_writes(
    repo: Path, member: str, worktree: Path, ledger: dict, tracker: dict, accepted: frozenset[str]
) -> tuple[list, list, str | None]:
    warnings: list[str] = []
    since = _config_get(worktree, "created-at")
    if since is None:
        log = tracker.get("integration_log", [])
        since = str(log[0].get("at") or "") if log and isinstance(log[0], dict) else ""
        if not since:
            raise BatchError(f"member {member!r} has no creation time and the tracker records no batch start time")
        warnings.append(
            f"member {member!r} worktree records no creation time; its tracker writes were checked from {since}"
        )
    landing = ledger["landings"].get(member, {})
    accepted = accepted | frozenset(landing.get("accepted_tracker_writes") or [])
    leader_ops = frozenset(str(entry.get("op_id")) for entry in _member_log_entries(tracker, member))
    through = landing.get("sessions_through")
    entries = _item_sessions(repo, member)
    writes = member_tracker_writes(
        entries,
        since,
        _first_landing(ledger, tracker, member) if through is None else None,
        leader_ops,
        _config_get(worktree, "sessions-mark"),
        through,
    )
    unexplained = [entry for entry in writes if entry.get("op_id") not in accepted]
    if unexplained:
        listed = "; ".join(f"{e.get('op')} {e.get('op_id')} at {e.get('at')} by {e.get('actor')}" for e in unexplained)
        raise BatchError(
            f"member {member!r} has tracker writes between {since} and its first landing ({listed}). "
            "Members never write to the tracker: run each member in its own session with its own tractable identity. "
            "Inspect the writes with show_item, then land again with ACCEPT_WRITES=<op_id,...> to record them as "
            "reviewed"
        )
    return sorted(str(entry.get("op_id")) for entry in writes), warnings, _last_op_id(entries)


def land_member(repo: Path, member: str, *, push: bool = True, accepted_writes: frozenset[str] = frozenset()) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    ledger = _require_ledger(repo, batch_id)
    if member not in ledger["members"]:
        raise BatchError(f"{member!r} is not a registered member of batch {batch_id!r}")
    dependencies = registered_dependencies(repo, batch_id, member)
    tracker = read_tracker(repo, batch_id)
    receipts = _receipts(tracker)
    if member in receipts:
        raise BatchError(f"member {member!r} already has a prepared receipt; nothing to land")
    if _branch(repo) != ledger["integration_branch"]:
        raise BatchError(f"integration worktree is not on {ledger['integration_branch']}")
    if not _is_clean(repo):
        raise BatchError("integration worktree is not clean")
    facts = _member_landing_facts(repo, ledger, member)
    _refuse_primary_edits(repo, member, ledger["scope"][member])
    _check_member_writes(repo, member, facts["worktree"], ledger, tracker, accepted_writes)
    head = _rev(repo, "HEAD")
    merged_already = _already_merged(repo, ledger, tracker, member, facts, head, dependencies)
    if merged_already is not None:
        return merged_already
    if dependencies and not _claimed_by_owner(tracker, member):
        raise BatchError(
            f"member {member!r} has implementation dependencies, so the tracker refuses to take it once the "
            "integration head moves past the recorded head: take it now, before landing, and keep the generation "
            "for the prepare step"
        )
    gate = _landing_preconditions(repo, ledger, tracker, member, facts, head)
    _refuse_primary_edits(repo, member, ledger["scope"][member])
    _, write_warnings, _ = _check_member_writes(repo, member, facts["worktree"], ledger, tracker, accepted_writes)
    warnings = [*write_warnings, *_preflight_gate(repo, facts["member_head"])]
    if gate["preflight"] != [*DEFAULT_PREFLIGHT_COMMAND, f"BASE={head}"]:
        warnings.append(f"member preflight was not the default: {' '.join(gate['preflight']) or 'not recorded'}")
    merged = _git_merge(repo, member, head, facts["member_head"], ledger)
    try:
        reviewed_writes, _, sessions_through = _check_member_writes(
            repo, member, facts["worktree"], ledger, tracker, accepted_writes
        )
    except BatchError:
        _git(repo, "reset", "--hard", head)
        raise
    landing = {
        "member_branch": facts["branch"],
        "member_worktree": str(facts["worktree"]),
        "member_head": facts["member_head"],
        "source_base": head,
        "merge_head": merged,
        "changed_files": gate["changed"],
        "trusted_sha": gate["trusted"],
        "review": gate["review"],
        "review_outcome": gate["review"]["outcome"],
        "verification": gate["verification"],
        "landed_at": _now(),
        "accepted_tracker_writes": reviewed_writes,
        "sessions_through": sessions_through,
    }
    earlier = {key: value for key, value in ledger["landings"].items() if key != member}
    ledger["landings"][member] = landing
    save_ledger(repo, ledger)
    warnings.extend(hunk_warnings(repo, {**ledger, "landings": earlier}, member))
    reruns, failures = _overlap_reruns(repo, ledger, receipts, tracker, earlier, member, gate["changed"])
    save_ledger(repo, ledger)
    if failures:
        raise BatchError(
            f"merged {member!r} at {merged} but the overlap rerun failed for {failures}; "
            f"reset the integration branch to {head} and land again once the combination passes; "
            f"do not prepare {member!r} until the reruns pass"
        )
    backup = _push_backup(repo, ledger["integration_branch"]) if push else "skipped"
    return {
        "status": "merged",
        "merge_head": merged,
        "reruns": reruns,
        "warnings": warnings,
        "backup_push": backup,
        "payload": _prepare_payload(ledger, member, landing, str(facts["worktree"]), dependencies),
    }


def _reconstruct_landing(
    repo: Path, ledger: dict, tracker: dict, member: str, facts: dict, merge_head: str | None
) -> dict:
    if merge_head is None:
        return {}
    parents = _git(repo, "rev-list", "--parents", "-n", "1", merge_head).split()
    if len(parents) != 3 or parents[2] != facts["member_head"]:
        raise BatchError(f"commit {merge_head} is not a two-parent merge of the member head")
    source_base = parents[1]
    merged_tree = _git(repo, "rev-parse", f"{merge_head}^{{tree}}")
    member_tree = _git(repo, "rev-parse", f"{facts['member_head']}^{{tree}}")
    if merged_tree != member_tree:
        raise BatchError(
            f"merge {merge_head} has tree {merged_tree}, not the member tree {member_tree}; it carries edits "
            "the member never checked"
        )
    if source_base not in tracker_heads(tracker):
        raise BatchError(f"merge {merge_head} was made on {source_base}, which is not a recorded integration head")
    check = _read_check(facts["worktree"], facts["member_head"], source_base)
    if check is None:
        raise BatchError(
            f"member {member!r} is merged at {merge_head} but its check record for base {source_base} is gone; "
            "rerun batch-member-check from the member worktree is not possible after merging; "
            "prepare it with the verification command recorded in the member worktree config"
        )
    trusted = str(check.get("trusted_sha", ""))
    start_head = str(ledger.get("start_head", ""))
    if not _develop_is_trusted(repo, trusted, start_head):
        raise BatchError("the recorded trusted commit is not an ancestor of origin/develop or predates start_head")
    review, changed = _review_and_scope(repo, ledger, member, source_base, facts["member_head"], trusted)
    return {
        "member_branch": facts["branch"],
        "member_worktree": str(facts["worktree"]),
        "member_head": facts["member_head"],
        "source_base": source_base,
        "merge_head": merge_head,
        "changed_files": changed,
        "trusted_sha": trusted,
        "review": review,
        "review_outcome": review["outcome"],
        "verification": {
            "suite": check["verification"]["suite"],
            "command": _validate_verification_command(check["verification"]["command"]),
        },
        "landed_at": None,
    }


def _git_merge(repo: Path, member: str, head: str, member_head: str, ledger: dict) -> str:
    message = f"Merge {member} into {ledger['integration_branch']}"
    proc = subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", "-m", message, member_head],
        cwd=repo,
        check=False,
        encoding="utf-8",
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=repo, check=False, capture_output=True, timeout=60)
        raise BatchError(f"merge failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    merged = _rev(repo, "HEAD")
    merged_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    member_tree = _git(repo, "rev-parse", f"{member_head}^{{tree}}")
    if merged_tree != member_tree:
        _git(repo, "reset", "--hard", head)
        raise BatchError(
            f"merged tree {merged_tree} differs from the member tree {member_tree}; the merge was undone "
            f"and the integration head is back at {head}"
        )
    if not _is_clean(repo):
        _git(repo, "reset", "--hard", head)
        raise BatchError(f"the merge left the integration worktree dirty; it was undone and the head is back at {head}")
    return merged


def _push_backup(repo: Path, branch: str) -> str:
    proc = subprocess.run(
        ["git", "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
        cwd=repo,
        check=False,
        encoding="utf-8",
        capture_output=True,
        timeout=300,
    )
    return "pushed" if proc.returncode == 0 else f"failed: {(proc.stderr or proc.stdout).strip()[:200]}"


def _member_files(ledger: dict, receipts: dict, member: str) -> list[str]:
    receipt = receipts.get(member)
    if isinstance(receipt, dict) and isinstance(receipt.get("changed_files"), list):
        return [str(path) for path in receipt["changed_files"]]
    return list(ledger["landings"].get(member, {}).get("changed_files", []))


def _merge_develop(repo: Path, ledger: dict) -> str:
    _git(repo, "fetch", "origin", "develop", "--quiet")
    message = f"Merge develop into {ledger['integration_branch']}"
    proc = subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", "-m", message, DEVELOP_REF],
        cwd=repo,
        check=False,
        encoding="utf-8",
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=repo, check=False, capture_output=True, timeout=60)
        raise BatchError(f"develop merge failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return _rev(repo, "HEAD")


def _resolve_advance(repo: Path, ledger: dict, tracker: dict, reason: str) -> tuple[str, str]:
    receipts = _receipts(tracker)
    recorded_head = str(tracker.get("integration_head"))
    head = _rev(repo, "HEAD")
    entry = next((e for e in tracker.get("integration_log", []) if e.get("head") == head), None)
    if entry is not None and (
        entry.get("kind") not in ("fix", "develop-merge") or (entry.get("kind") != reason and head == recorded_head)
    ):
        entry = None
    if entry is None and head != recorded_head:
        stuck = [
            m
            for m in ledger["members"]
            if m not in receipts and _unprepared_state(repo, ledger, tracker, m)[0] == "merged-unprepared"
        ]
        if stuck:
            raise BatchError(f"members {stuck} are merged but not prepared; prepare them before recording an advance")
    if entry is None and head == recorded_head:
        if reason == "fix":
            raise BatchError("nothing to record: the integration head is the recorded head; commit the fix first")
        head = _merge_develop(repo, ledger)
    elif entry is not None and entry.get("kind") != reason:
        raise BatchError(f"the integration head is already recorded as a {entry.get('kind')}, not a {reason}")
    expected = str(entry["previous_head"]) if entry is not None else recorded_head
    if expected == head or not _is_ancestor(repo, expected, head):
        raise BatchError("the integration head does not descend from the recorded head")
    return expected, head


def _fix_acceptance(
    repo: Path, ledger: dict, tracker: dict, head: str, changed: list[str]
) -> tuple[list[str], list[str]]:
    receipts = _receipts(tracker)
    union = [pattern for patterns in ledger["scope"].values() for pattern in patterns]
    outside = [path for path in changed if not _matches_scope(path, union)]
    if outside:
        raise BatchError(f"a fix cannot widen scope: {', '.join(outside[:5])} match no member scope")
    touched = [m for m in ledger["members"] if set(changed) & set(_member_files(ledger, receipts, m))]
    failures: list[str] = []
    for member in touched:
        suite, command = _landing_verification(ledger, member, receipts)
        if _rerun(repo, ledger, member, command, suite, f"fix:{head}", tracker)["status"] != "passed":
            failures.append(member)
    return touched, failures


def _develop_merge_parent(repo: Path, expected: str, head: str) -> str:
    parents = _git(repo, "rev-list", "--parents", "-n", "1", head).split()[1:]
    if len(parents) != 2 or parents[0] != expected:
        raise BatchError("a develop merge must be a two-parent merge whose first parent is the recorded head")
    if not _is_ancestor(repo, parents[1], DEVELOP_REF):
        raise BatchError("the merged parent is not an ancestor of origin/develop")
    return parents[1]


def advance_head(repo: Path, reason: str, *, push: bool = True) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    ledger = _require_ledger(repo, batch_id)
    if reason not in ("fix", "develop-merge"):
        raise BatchError("reason must be fix or develop-merge")
    tracker = read_tracker(repo, batch_id)
    if _branch(repo) != ledger["integration_branch"]:
        raise BatchError(f"integration worktree is not on {ledger['integration_branch']}")
    if not _is_clean(repo):
        raise BatchError("integration worktree is not clean")
    expected, head = _resolve_advance(repo, ledger, tracker, reason)
    advisories = _preflight_gate(repo, head)
    changed = _changed_files(repo, expected, head)
    result: dict = {
        "reason": reason,
        "expected_head": expected,
        "head": head,
        "changed_files": changed,
        "advisories": advisories,
    }
    failures: list[str] = []
    if reason == "fix":
        touched, failures = _fix_acceptance(repo, ledger, tracker, head, changed)
        result["requires_acceptance"] = touched
        result["reruns"] = {m: ledger["acceptance"][m][-1]["status"] for m in touched}
    else:
        result["merged_parent"] = _develop_merge_parent(repo, expected, head)
        result["requires_acceptance"] = []
    ledger["advances"] = [a for a in ledger["advances"] if a["head"] != head]
    ledger["advances"].append(
        {
            "kind": reason,
            "expected_head": expected,
            "head": head,
            "changed_files": changed,
            "requires_acceptance": result["requires_acceptance"],
            "recorded_at": _now(),
        }
    )
    save_ledger(repo, ledger)
    if failures:
        raise BatchError(f"the fix at {head} broke earlier members {failures}; fix again and rerun batch-advance")
    result["payload"] = {
        "tool": "advance_batch_head",
        "arguments": {
            "batch_id": batch_id,
            "owner_generation": ledger.get("owner_generation") or "<owner generation from the leader's ledger>",
            "expected_head": expected,
            "head": head,
            "reason": reason,
            "changed_files": changed,
        },
    }
    final_pr = tracker.get("final_pr")
    if isinstance(final_pr, dict):
        result["backup_push"] = "skipped (final PR bound; submit advance_batch_head before push)"
        old_head = final_pr.get("head") or expected
        result["next"] = (
            f"1. Withdraw auto-merge: make pr-landing-withdraw PR={final_pr.get('number')} HEAD={old_head}\n"
            f"2. Submit the advance_batch_head payload below to the tracker.\n"
            f"3. Push the exact head: git push origin {head}:refs/heads/{ledger['integration_branch']}\n"
            f"4. Rebind the final PR: bind_batch_pr head={head} expected_previous_head={final_pr.get('head')}\n"
            f"5. Run make batch-evidence.\n"
            f"6. Verify review and CI, then rearm."
        )
    else:
        result["backup_push"] = _push_backup(repo, ledger["integration_branch"]) if push else "skipped"
    return result


def verify_integration(repo: Path) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    ledger = _require_ledger(repo, batch_id)
    tracker = read_tracker(repo, batch_id)
    covered: set[str] = {ledger["start_head"]}
    previous_recorded = ledger["start_head"]
    for entry in tracker.get("integration_log", []):
        entry_head = entry.get("head")
        if not entry_head:
            continue
        if entry.get("kind") == "fix" and _is_ancestor(repo, previous_recorded, entry_head):
            covered.update(_git(repo, "rev-list", "--first-parent", f"{previous_recorded}..{entry_head}").splitlines())
        covered.add(entry_head)
        previous_recorded = entry_head

    head = _rev(repo, "HEAD")
    unrecorded = [
        sha
        for sha in _git(repo, "rev-list", "--first-parent", f"{ledger['start_head']}..{head}").splitlines()
        if sha not in covered
    ]
    receipts = _receipts(tracker)
    ancestry: dict[str, bool | None] = {}
    for member in ledger["members"]:
        accepted = receipts.get(member, {}).get("accepted_head")
        ancestry[member] = _is_ancestor(repo, str(accepted), head) if accepted else None
    committers = sorted(
        set(_git(repo, "log", "--first-parent", f"{ledger['start_head']}..{head}", "--format=%cn <%ce>").splitlines())
    )
    ok = not unrecorded and all(value is not False for value in ancestry.values())
    return {
        "ok": ok,
        "integration_head": head,
        "unrecorded_first_parent_commits": unrecorded,
        "member_ancestry": ancestry,
        "committers_informational": committers,
    }


def _ref_oid(repo: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo,
        check=False,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _remote_branch_oid(repo: Path, branch: str) -> str | None:
    out = _git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}":
            return sha
    return None


def _merged_final_pr(repo: Path, tracker: dict) -> tuple[str, int]:
    if tracker.get("lifecycle") != "completed":
        raise BatchError(f"the batch lifecycle is {tracker.get('lifecycle')!r}, not 'completed'; finish it first")
    final_pr = tracker.get("final_pr")
    if not isinstance(final_pr, dict) or not isinstance(final_pr.get("number"), int):
        raise BatchError("the tracker records no final PR for this batch")
    number = final_pr["number"]
    repository = str(final_pr.get("repository") or "")
    origin = _repo_slug(_git(repo, "remote", "get-url", "origin"))
    try:
        recorded = canonical_repository(repository)
    except BatchError:
        recorded = None
    if recorded != origin.casefold():
        raise BatchError(f"the final PR belongs to {repository!r} but origin is {origin!r}")
    repository = origin
    proc = _run(["gh", "pr", "view", str(number), "--repo", repository, "--json", "number,state"], repo, timeout=120)
    try:
        view = json.loads(proc.stdout)
    except ValueError:
        view = None
    if proc.returncode != 0 or not isinstance(view, dict):
        raise BatchError(f"gh could not read PR #{number}: {(proc.stderr or proc.stdout).strip()[:300]}")
    if view.get("number") != number or view.get("state") != "MERGED":
        raise BatchError(f"final PR #{number} is {view.get('state')!r}, not merged")
    return repository, number


def _fetch_pull_head(repo: Path, number: int) -> str:
    try:
        _git(repo, "fetch", "--no-tags", "--quiet", "origin", f"refs/pull/{number}/head")
    except BatchError as exc:
        raise BatchError(f"refs/pull/{number}/head could not be fetched from origin: {exc}") from exc
    return _rev(repo, "FETCH_HEAD")


def _member_worktree_problem(repo: Path, batch_id: str, member: str, branch: str) -> tuple[Path | None, str | None]:
    worktree = _worktree_for_branch(repo, branch)
    if worktree is None:
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
            if not line.startswith("worktree "):
                continue
            candidate = Path(line.removeprefix("worktree "))
            if candidate.is_dir() and (_config_get(candidate, "id"), _config_get(candidate, "member")) == (
                batch_id,
                member,
            ):
                worktree = candidate
                break
    if worktree is None:
        return None, None
    if not worktree.is_dir():
        return worktree, f"member worktree {worktree} is still registered but missing on disk"
    if worktree.resolve() == repo.resolve():
        return worktree, "the branch is checked out in the integration worktree"
    identity = (_config_get(worktree, "role"), _config_get(worktree, "id"), _config_get(worktree, "member"))
    if identity != (ROLE_MEMBER, batch_id, member):
        return worktree, f"the branch is checked out in {worktree}, which is not this batch's member worktree"
    if not _git_ok(worktree, "symbolic-ref", "--quiet", "HEAD") or _branch(worktree) != branch:
        return worktree, f"member worktree {worktree} no longer holds branch {branch}"
    if not _is_clean(worktree):
        return worktree, f"member worktree {worktree} has uncommitted or untracked changes"
    return worktree, None


def _worktree_locked(repo: Path, worktree: Path) -> bool:
    current: Path | None = None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree "))
        elif (line == "locked" or line.startswith("locked ")) and current is not None:
            if current.resolve() == worktree.resolve():
                return True
    return False


def _plan_member_cleanup(repo: Path, batch_id: str, pull_head: str, member: str, receipts: dict[str, dict]) -> dict:
    branch = member_branch_name(batch_id, member)
    local = _ref_oid(repo, f"refs/heads/{branch}")
    remote = _remote_branch_oid(repo, branch)
    accepted = receipts.get(member, {}).get("accepted_head")
    entry: dict = {
        "batch_id": batch_id,
        "member": member,
        "branch": branch,
        "accepted_head": accepted,
        "local_tip": local,
        "remote_tip": remote,
        "worktree": None,
        "status": "refused",
        "reason": None,
    }
    if local is None and remote is None:
        worktree, problem = _member_worktree_problem(repo, batch_id, member, branch)
        entry["worktree"] = str(worktree) if worktree else None
        if worktree is not None:
            entry["reason"] = problem or f"member worktree {worktree} remains despite absent branch refs"
            return entry
        entry["status"] = "already-deleted"
        return entry
    if not accepted:
        entry["reason"] = "the tracker holds no receipt for this member, so nothing proves what the branch must contain"
        return entry
    if local is not None and local != accepted:
        entry["reason"] = f"branch tip {local} differs from the accepted head {accepted}"
        return entry
    if not _is_ancestor(repo, str(accepted), pull_head):
        entry["reason"] = f"accepted head {accepted} is not an ancestor of the final PR head {pull_head}"
        return entry
    worktree, problem = _member_worktree_problem(repo, batch_id, member, branch)
    entry["worktree"] = str(worktree) if worktree else None
    if not problem and worktree is not None and _worktree_locked(repo, worktree):
        problem = f"member worktree {worktree} is locked"
    if problem:
        entry["reason"] = problem
        return entry
    entry["delete_local"] = local is not None
    entry["delete_remote"] = remote is not None and remote == accepted
    if remote is not None and remote != accepted:
        entry["reason"] = f"remote tip {remote} differs from the accepted head {accepted}"
        return entry
    if not entry["delete_local"] and not entry["delete_remote"]:
        entry["reason"] = entry.get("remote_kept") or "nothing to delete"
        return entry
    entry["status"] = "would-delete"
    return entry


def _recheck_before_removal(repo: Path, entry: dict) -> str | None:
    branch, accepted = entry["branch"], entry["accepted_head"]
    if entry["delete_local"]:
        tip = _ref_oid(repo, f"refs/heads/{branch}")
        if tip != accepted:
            return f"branch tip {tip} no longer equals the accepted head {accepted}"
    worktree, problem = _member_worktree_problem(repo, entry["batch_id"], entry["member"], branch)
    if problem:
        return problem
    if (str(worktree) if worktree else None) != entry["worktree"]:
        return "member worktree path changed since planning"
    if worktree is not None and _worktree_locked(repo, worktree):
        return f"member worktree {worktree} is locked"
    return None


def _execute_member_cleanup(repo: Path, entry: dict) -> None:
    branch, accepted = entry["branch"], entry["accepted_head"]
    lease = f"--force-with-lease=refs/heads/{branch}:{accepted}"
    problem = _recheck_before_removal(repo, entry)
    if problem:
        entry["status"] = "refused"
        entry["reason"] = problem
        return
    try:
        if entry["worktree"]:
            _git(repo, "worktree", "remove", entry["worktree"])
            entry["worktree_removed"] = True
        if entry["delete_local"]:
            _git(repo, "update-ref", "-d", f"refs/heads/{branch}", accepted)
        if entry["delete_remote"]:
            _git(repo, "push", "--no-verify", lease, "origin", f":refs/heads/{branch}")
    except BatchError as exc:
        entry["status"] = "refused"
        entry["reason"] = str(exc)
        return
    entry["status"] = "deleted"


def _integration_finish_preview(
    repo: Path, repository: str, number: int, pull_head: str
) -> tuple[FinishPreviewResult, Path]:
    _checkout_root_modules()
    from scripts import worktree_finish

    primary = worktree_finish.get_primary_clone_path(repo)
    if primary is None:
        raise BatchError("cannot resolve the primary clone for integration cleanup")
    try:
        preview = worktree_finish.evaluate_finish_preview(
            target_path=repo, expected_head_oid=pull_head, repo_root=primary, repo_slug=repository
        )
    except worktree_finish.FinishError as exc:
        raise BatchError(str(exc)) from exc
    if preview.status == "actionable" and (preview.pr_number != number or preview.merged_head != pull_head):
        raise BatchError("integration finish evidence does not match the batch's merged final PR head")
    return preview, primary


def finish_batch(repo: Path, *, execute: bool = False) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    tracker = read_tracker(repo, batch_id)
    integration: dict = {"worktree": str(repo), "status": "blocked", "reason": None}
    report = {
        "batch_id": batch_id,
        "mode": "execute" if execute else "dry-run",
        "final_pr": tracker.get("final_pr"),
        "members": [],
        "integration": integration,
        "ok": False,
    }
    try:
        repository, number = _merged_final_pr(repo, tracker)
    except BatchError as exc:
        if execute:
            raise
        integration["reason"] = str(exc)
        return report
    pull_head = _fetch_pull_head(repo, number)
    report["final_pr"] = {"repository": repository, "number": number, "head": pull_head}
    receipts = _receipts(tracker)
    members = [_plan_member_cleanup(repo, batch_id, pull_head, m, receipts) for m in _members_in_order(tracker)]
    report["members"] = members
    try:
        preview, primary = _integration_finish_preview(repo, repository, number, pull_head)
        integration["preview"] = preview.to_dict()
    except BatchError as exc:
        integration["reason"] = str(exc)
        return report
    if execute:
        for entry in members:
            if entry["status"] == "would-delete":
                _execute_member_cleanup(repo, entry)
        for entry in members:
            if entry["status"] in {"deleted", "already-deleted"}:
                retained, problem = _member_worktree_problem(repo, batch_id, entry["member"], entry["branch"])
                if retained is not None:
                    entry["status"] = "refused"
                    entry["reason"] = problem or f"member worktree {retained} remains after cleanup"
                    continue
                if _ref_oid(repo, f"refs/heads/{entry['branch']}") or _remote_branch_oid(repo, entry["branch"]):
                    entry["status"] = "refused"
                    entry["reason"] = "member branch still exists after cleanup"
    if any(entry["status"] == "refused" for entry in members):
        integration["reason"] = "member cleanup is incomplete"
        return report
    if execute:
        try:
            repository, number = _merged_final_pr(repo, read_tracker(repo, batch_id))
            pull_head = _fetch_pull_head(repo, number)
            report["final_pr"] = {"repository": repository, "number": number, "head": pull_head}
            preview, primary = _integration_finish_preview(repo, repository, number, pull_head)
            integration["preview"] = preview.to_dict()
        except BatchError as exc:
            integration["reason"] = str(exc)
            return report
    if preview.status != "actionable":
        integration["reason"] = preview.hold_reason
        return report
    integration["status"] = "would-finish"
    if execute:
        from scripts.worktree_finish import apply_finish_actions

        os.chdir(primary)
        removed, deleted, actions = apply_finish_actions(preview, primary)
        integration["actions"] = actions
        integration["status"] = "finished" if removed and deleted else "refused"
    report["ok"] = integration["status"] in {"would-finish", "finished"}
    return report


def rebuild_ledger(repo: Path, batch_id: str, tracker: dict) -> dict:
    scope = tracker.get("scope") if isinstance(tracker.get("scope"), dict) else {}
    ledger = {
        "schema": LEDGER_SCHEMA,
        "batch_id": batch_id,
        "project_id": tracker.get("project_id"),
        "repository": tracker.get("repository"),
        "integration_branch": tracker.get("integration_branch"),
        "integration_worktree": tracker.get("integration_worktree"),
        "start_head": tracker.get("start_head"),
        "develop_ref": tracker.get("develop_ref") or DEVELOP_REF,
        "delivery_boundary": tracker.get("delivery_boundary"),
        "terminal_outcome": tracker.get("terminal_outcome"),
        "members": _members_in_order(tracker),
        "scope": scope,
        "scope_hash": tracker.get("scope_hash"),
        "owner_generation": None,
        "landings": {},
        "advances": [],
        "acceptance": {},
        "created_at": _now(),
        "rebuilt": True,
    }
    for member, receipt in _receipts(tracker).items():
        verification = receipt.get("verification") if isinstance(receipt.get("verification"), dict) else {}
        review = verification.get("review") if isinstance(verification.get("review"), dict) else {}
        ledger["landings"][member] = {
            "member_branch": member_branch_name(batch_id, member),
            "member_worktree": receipt.get("source_worktree"),
            "member_head": receipt.get("accepted_head"),
            "source_base": receipt.get("source_base"),
            "merge_head": receipt.get("integration_head"),
            "changed_files": receipt.get("changed_files", []),
            "trusted_sha": review.get("trusted_sha"),
            "review_outcome": review.get("outcome"),
            "verification": {"suite": verification.get("suite"), "command": verification.get("command")},
            "landed_at": None,
        }
    for entry in tracker.get("integration_log", []):
        if entry.get("kind") in ("fix", "develop-merge"):
            ledger["advances"].append(
                {
                    "kind": entry["kind"],
                    "expected_head": entry.get("previous_head"),
                    "head": entry["head"],
                    "changed_files": entry.get("changed_files", []),
                    "requires_acceptance": entry.get("requires_acceptance", []),
                    "recorded_at": entry.get("at"),
                }
            )
    return ledger


def _same_repository(origin: str, registered: object) -> bool:
    try:
        return isinstance(registered, str) and canonical_repository(origin) == canonical_repository(registered)
    except BatchError:
        return False


def _take_over_worktrees(repo: Path, ledger: dict, batch_id: str) -> list[dict[str, str]]:
    _checkout_root_modules()
    from scripts import worktree_lifecycle_metadata as lifecycle

    targets = [repo.resolve()]
    for member in ledger.get("members", []):
        found = _worktree_for_branch(repo, member_branch_name(batch_id, member))
        if found is not None and found.is_dir():
            targets.append(found.resolve())
    taken: list[dict[str, str]] = []
    for target in targets:
        try:
            status = lifecycle.claim_worktree(target, takeover=True)
        except lifecycle.MetadataError as exc:
            status = f"failed: {exc}"
        taken.append({"worktree": str(target), "status": status})
    return taken


def recover_owner(
    repo: Path,
    expected_owner: str,
    expected_owner_generation: str,
    reason: str,
    *,
    relocate: bool = False,
    batch_id: str | None = None,
) -> dict:
    for label, value in (
        ("expected owner", expected_owner),
        ("expected owner generation", expected_owner_generation),
        ("reason", reason),
    ):
        if not value.strip():
            raise BatchError(f"{label} is required")
    branch = _branch(repo)
    marked = _config_get(repo, "id") if role_of(repo) == ROLE_INTEGRATION else None
    if role_of(repo) not in (None, ROLE_INTEGRATION):
        raise BatchError(f"this checkout has role={role_of(repo)}; recover from a clean integration checkout")
    batch_id = _validate_id(batch_id or marked or branch.removeprefix("feat/"), "batch")
    tracker = read_tracker(repo, batch_id)
    if branch != tracker.get("integration_branch"):
        raise BatchError(f"this checkout is on {branch}, not the registered {tracker.get('integration_branch')}")
    origin = _git(repo, "remote", "get-url", "origin")
    if not _same_repository(origin, tracker.get("repository")):
        raise BatchError(f"origin {origin} is not the registered repository {tracker.get('repository')}")
    if not _is_clean(repo):
        raise BatchError("integration checkout is not clean")
    head = _rev(repo, "HEAD")
    if head != tracker.get("integration_head"):
        raise BatchError(f"checkout head {head} is not the tracker integration head {tracker.get('integration_head')}")
    registered_worktree = str(tracker.get("integration_worktree") or "")
    here = str(repo.resolve())
    if relocate and registered_worktree == here:
        raise BatchError("this checkout is already the registered integration worktree; omit RELOCATE")
    if not relocate and registered_worktree != here:
        raise BatchError(
            f"this checkout is not the registered integration worktree {registered_worktree}; "
            "rerun with RELOCATE=1 to move the registration here"
        )
    ledger = load_ledger(repo, batch_id)
    if ledger is None:
        ledger = rebuild_ledger(repo, batch_id, tracker)
    generation = secrets.token_hex(16)
    ledger["owner_generation"] = generation
    ledger["integration_worktree"] = here
    taken = _take_over_worktrees(repo, ledger, batch_id)
    ledger.setdefault("handoffs", []).append(
        {
            "at": _now(),
            "expected_owner": expected_owner,
            "expected_owner_generation": expected_owner_generation,
            "new_owner_generation": generation,
            "reason": reason,
            "worktrees": taken,
        }
    )
    save_ledger(repo, ledger)
    _config_set(repo, "role", ROLE_INTEGRATION)
    _config_set(repo, "id", batch_id)
    arguments = {
        "batch_id": batch_id,
        "expected_owner": expected_owner,
        "expected_owner_generation": expected_owner_generation,
        "new_owner_generation": generation,
        "reason": reason,
    }
    if relocate:
        arguments["expected_integration_worktree"] = registered_worktree
        arguments["expected_integration_head"] = head
        arguments["integration_worktree"] = here
    return {"worktree": here, "tool": "recover_batch_owner", "arguments": arguments, "worktree_takeover": taken}


def batch_status(repo: Path) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    tracker = read_tracker(repo, batch_id)
    ledger = load_ledger(repo, batch_id)
    rebuilt = False
    if ledger is None:
        ledger = rebuild_ledger(repo, batch_id, tracker)
        rebuilt = True
    receipts = _receipts(tracker)
    head = _rev(repo, "HEAD")
    recorded_heads = tracker_heads(tracker)
    members_report: list[dict] = []
    next_steps: list[str] = []
    for member in ledger["members"]:
        if member in receipts:
            ledger["landings"].setdefault(member, {})
            state = "prepared"
        else:
            state, landing, problem = _unprepared_state(repo, ledger, tracker, member)
            if state == "merged-unprepared" and landing is not None:
                ledger["landings"][member] = landing
                next_steps.append(
                    f"member {member!r} is merged at {landing['merge_head']} but not prepared: take it and submit the "
                    "prepare payload from batch-land (running batch-land again prints it without merging)"
                )
            elif state == "merged-unprepared":
                next_steps.append(
                    f"member {member!r} has a merge that cannot be verified, do not prepare it: {problem}"
                )
            elif ledger["landings"].get(member) and not _is_ancestor(
                repo, ledger["landings"][member]["merge_head"], head
            ):
                del ledger["landings"][member]
        members_report.append({"member": member, "state": state})
    if head != tracker.get("integration_head") and not any(m["state"] == "merged-unprepared" for m in members_report):
        next_steps.append(
            f"integration head {head} is not the recorded head {tracker.get('integration_head')}: "
            "run batch-advance to record the leader commit"
        )
    required = tracker.get("acceptance_required") if isinstance(tracker.get("acceptance_required"), dict) else {}
    stale = []
    for member in required:
        entries = ledger.get("acceptance", {}).get(member, [])
        if not entries or entries[-1].get("revision") != head or entries[-1].get("status") != "passed":
            stale.append(member)
    preflight = review_preflight(repo, head)
    save_ledger(repo, ledger)
    return {
        "batch_id": batch_id,
        "ledger": "rebuilt from the tracker and Git" if rebuilt else "present",
        "integration_head": head,
        "tracker_integration_head": tracker.get("integration_head"),
        "recorded_heads": recorded_heads,
        "members": members_report,
        "acceptance_required": sorted(required),
        "acceptance_stale": [m for m in ledger["members"] if m in stale],
        "review_preflight": preflight,
        "final_pr": tracker.get("final_pr"),
        "owner_generation": "present" if ledger.get("owner_generation") else "missing: use recover_batch_owner",
        "next": next_steps,
    }


def _unprepared_state(repo: Path, ledger: dict, tracker: dict, member: str) -> tuple[str, dict | None, str | None]:
    branch = member_branch_name(ledger["batch_id"], member)
    if not _git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        return "pending", None, None
    member_head = _rev(repo, f"refs/heads/{branch}")
    merge_head = _pending_mergecommit(repo, member_head)
    if merge_head is None:
        return "pending", None, None
    landing = ledger["landings"].get(member)
    if landing and landing.get("merge_head") == merge_head:
        return "merged-unprepared", landing, None
    worktree = _worktree_for_branch(repo, branch)
    if worktree is None:
        return "merged-unprepared", None, f"no worktree has branch {branch} checked out"
    facts = {"branch": branch, "worktree": worktree, "member_head": member_head}
    try:
        return "merged-unprepared", _reconstruct_landing(repo, ledger, tracker, member, facts, merge_head), None
    except BatchError as exc:
        return "merged-unprepared", None, str(exc)


def _pr_landing_module():
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    _checkout_root_modules()
    import pr_landing

    return pr_landing


def _excluded_paths(repo: Path, final_head: str, tracker: dict) -> set[str]:
    pr_landing = _pr_landing_module()

    excluded, problems = pr_landing.develop_exclusions(repo, final_head, tracker, _receipts(tracker))
    if problems:
        raise BatchError("develop merges cannot be verified from Git: " + "; ".join(problems))
    return excluded


def _drop_develop_deleted_targets(
    repo: Path, head: str, command: list[str], excluded: set[str], tracker: dict
) -> tuple[list[str], list[str]]:
    pr_landing = _pr_landing_module()
    start = pr_landing.trailing_test_run_start(command) if "pytest" in command else None
    if start is None:
        return list(command), []
    kept = list(command[:start])
    removed: set[str] = set()
    for arg in command[start:]:
        path = arg.split("::", 1)[0]
        if (
            path in excluded
            and not _git_ok(repo, "cat-file", "-e", f"{head}:{path}")
            and pr_landing.develop_plainly_deleted(repo, tracker, path)
        ):
            removed.add(path)
        else:
            kept.append(arg)
    return kept, sorted(removed)


def _develop_deleted_rerun(
    repo: Path, head: str, command: list[str], tracker: dict | None
) -> tuple[list[str], list[str], bool]:
    if tracker is None or "pytest" not in command:
        return list(command), [], False
    pr_landing = _pr_landing_module()
    excluded, problems = pr_landing.develop_exclusions(repo, head, tracker, _receipts(tracker))
    if problems or not excluded:
        return list(command), [], False
    kept, removed = _drop_develop_deleted_targets(repo, head, command, excluded, tracker)
    exhausted = bool(removed) and pr_landing.trailing_test_run_start(kept) is None
    return kept, removed, exhausted


def build_evidence(repo: Path) -> dict:
    batch_id = require_role(repo, ROLE_INTEGRATION)
    ledger = _require_ledger(repo, batch_id)
    tracker = read_tracker(repo, batch_id)
    if not _is_clean(repo):
        raise BatchError("integration worktree is not clean")
    final_head = _rev(repo, "HEAD")
    if tracker.get("integration_head") != final_head:
        raise BatchError(f"tracker integration head {tracker.get('integration_head')} differs from {final_head}")
    final_pr = tracker.get("final_pr")
    if not isinstance(final_pr, dict) or final_pr.get("head") != final_head:
        raise BatchError("the final PR is not bound at the integration head; open the PR and bind_batch_pr first")
    if Path(str(tracker.get("integration_worktree"))).resolve() != repo.resolve():
        raise BatchError("this worktree is not the registered integration worktree")
    _preflight_gate(repo, final_head)
    path = _ledger_dir(repo, batch_id) / "final-evidence.json"
    if path.exists():
        path.unlink()

    receipts = _receipts(tracker)
    members = ledger["members"]
    for member in members:
        receipt = receipts.get(member)
        if not isinstance(receipt, dict) or receipt.get("schema") != "prepared_work_v2":
            raise BatchError(f"member {member!r} lacks a prepared_work_v2 receipt")
        if not _is_ancestor(repo, str(receipt.get("accepted_head")), final_head):
            raise BatchError(f"member {member!r} accepted head is not an ancestor of the final head")

    excluded = _excluded_paths(repo, final_head, tracker)
    current: dict[str, dict] = {}
    dropped: dict[str, list[str]] = {}
    for member in members:
        verification = receipts[member]["verification"]
        command = _validate_verification_command(verification.get("command"))
        rerun_entry = _rerun(
            repo, ledger, member, command, str(verification.get("suite", "")), "final-evidence", tracker
        )
        if rerun_entry["dropped_paths"]:
            dropped[member] = rerun_entry["dropped_paths"]
        save_ledger(repo, ledger)
        if rerun_entry["status"] != "passed":
            raise BatchError(f"rerun of {member!r} failed at the final head: {rerun_entry.get('output', '')}")
        if not _is_clean(repo) or _rev(repo, "HEAD") != final_head:
            raise BatchError(f"rerun of {member!r} left the integration worktree dirty or moved HEAD")
        current[member] = {
            "status": "passed",
            "revision": final_head,
            "clean": True,
            "suite": receipts[member]["verification"]["suite"],
            "command": receipts[member]["verification"]["command"],
            "ran_command": rerun_entry["ran_command"],
            "dropped_paths": rerun_entry["dropped_paths"],
            "worktree": str(repo.resolve()),
        }

    reviews: dict[str, dict] = {}
    for member in members:
        receipt = receipts[member]
        verification = receipt.get("verification", {})
        review = verification.get("review")
        if not isinstance(review, dict) or review.get("outcome") not in ACCEPTED_REVIEW_OUTCOMES:
            raise BatchError(f"member {member!r} receipt has no accepted review record")
        reviews[member] = review
    changed = [p for p in _changed_files(repo, ledger["start_head"], final_head) if p not in excluded]
    tracker_view = {key: value for key, value in tracker.items() if key != "_rev"}
    origin = _git(repo, "remote", "get-url", "origin")
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "batch_id": batch_id,
        "repository": canonical_repository(str(final_pr.get("repository") or origin)),
        "integration_branch": ledger["integration_branch"],
        "integration_worktree": str(repo.resolve()),
        "final_head": final_head,
        "tracker_rev": tracker["_rev"],
        "tracker": tracker_view,
        "current_acceptance": current,
        "dropped_receipt_paths": dropped,
        "changed_files": changed,
        "member_reviews": reviews,
        "generated_at": _now(),
    }
    path = _ledger_dir(repo, batch_id) / "final-evidence.json"
    _write_json(path, evidence)
    final_evidence = {
        "status": "passed",
        "batch_id": batch_id,
        "project_id": tracker["project_id"],
        "repository": tracker["repository"],
        "tree_worktree": str(repo.resolve()),
        "tree_revision": final_head,
        "integration_branch": ledger["integration_branch"],
        "integration_head": final_head,
        "scope_hash": tracker["scope_hash"],
        "suite": FINAL_SUITE,
        "member_heads": {m: receipts[m]["accepted_head"] for m in members},
        "member_ranges": {
            m: {"base": receipts[m]["source_base"], "head": receipts[m]["accepted_head"]} for m in members
        },
        "changed_files": changed,
        "final_pr": final_pr,
        "clean": True,
        "current_acceptance": current,
    }
    finishes = [
        {
            "tool": "finish",
            "arguments": {
                "id": member,
                "generation": "<generation returned by take>",
                "final_evidence": final_evidence,
            },
        }
        for member in members
    ]
    return {"path": str(path), "evidence": evidence, "finish_payloads": finishes}


def _print(value: object) -> None:
    print(json.dumps(value, indent=2))


def _parse_csv(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="create the integration worktree and print the register_batch payload")
    start.add_argument("--batch", required=True)
    start.add_argument("--members", required=True, help="comma-separated, in registered order")
    start.add_argument("--scope-json", type=Path, required=True)
    start.add_argument("--project-id", required=True)
    start.add_argument("--worktree-path", type=Path)
    start.add_argument("--delivery-boundary", default="final-pr")
    start.add_argument("--terminal-outcome", default="deliver")
    start.add_argument("--confirm-cutover", action="store_true")
    start.add_argument("--dependencies", default="", help="member=dep1+dep2,member2=dep3")

    create = sub.add_parser("member-create", help="create a member worktree at the integration head")
    create.add_argument("--member", required=True)
    create.add_argument("--worktree-path", type=Path)

    check = sub.add_parser("member-check", help="check a synced member and record the result")
    check.add_argument("--tests", default="")
    check.add_argument("--preflight-command")

    land = sub.add_parser("land", help="merge a checked member into the integration branch")
    land.add_argument("--member", required=True)
    land.add_argument("--no-push", action="store_true")
    land.add_argument("--accept-writes", default="")

    advance = sub.add_parser("advance", help="record a leader fix commit or develop merge")
    advance.add_argument("--reason", required=True, choices=("fix", "develop-merge"))
    advance.add_argument("--no-push", action="store_true")

    finish = sub.add_parser("finish", help="remove member worktrees and branches that lose nothing (dry run default)")
    finish.add_argument("--execute", action="store_true")

    recover = sub.add_parser("recover", help="mint a new owner generation and print the recover_batch_owner payload")
    recover.add_argument("--expected-owner", required=True)
    recover.add_argument("--expected-owner-generation", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--relocate", action="store_true")
    recover.add_argument("--batch")

    sub.add_parser("status", help="reconcile the ledger with the tracker and Git")
    sub.add_parser("verify", help="check first-parent history, recorded heads and member ancestry")
    sub.add_parser("evidence", help="write final-evidence.json and print finish payloads")

    args = parser.parse_args(argv)
    repo = args.worktree.resolve()
    try:
        if args.command == "start":
            members = _parse_csv(args.members)
            scope = json.loads(args.scope_json.read_text(encoding="utf-8"))
            _print(
                start_batch(
                    repo,
                    args.batch,
                    members,
                    scope,
                    project_id=args.project_id,
                    worktree_path=args.worktree_path,
                    delivery_boundary=args.delivery_boundary,
                    terminal_outcome=args.terminal_outcome,
                    confirm_cutover=args.confirm_cutover,
                    dependencies=parse_dependencies(args.dependencies),
                )
            )
        elif args.command == "member-create":
            _print(create_member(repo, args.member, args.worktree_path))
        elif args.command == "member-check":
            _print(
                check_member(
                    repo,
                    _parse_csv(args.tests),
                    preflight_command=shlex.split(args.preflight_command) if args.preflight_command else None,
                )
            )
        elif args.command == "land":
            accepted = frozenset(_parse_csv(args.accept_writes))
            _print(land_member(repo, args.member, push=not args.no_push, accepted_writes=accepted))
        elif args.command == "advance":
            _print(advance_head(repo, args.reason, push=not args.no_push))
        elif args.command == "finish":
            report = finish_batch(repo, execute=args.execute)
            _print(report)
            return 0 if report["ok"] else 1
        elif args.command == "recover":
            _print(
                recover_owner(
                    repo,
                    args.expected_owner,
                    args.expected_owner_generation,
                    args.reason,
                    relocate=args.relocate,
                    batch_id=args.batch,
                )
            )
        elif args.command == "status":
            _print(batch_status(repo))
        elif args.command == "verify":
            report = verify_integration(repo)
            _print(report)
            return 0 if report["ok"] else 1
        elif args.command == "evidence":
            _print(build_evidence(repo))
        else:
            raise BatchError(f"unknown command {args.command}")
        return 0
    except BatchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
