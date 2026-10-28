#!/usr/bin/env python3

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import fcntl
except ImportError:
    fcntl = None

CLI_DESCRIPTION = (
    "Minimal durable lifecycle metadata for BenchBox disposable worktrees.\n"
    "\n"
    "Extends worktree lifecycle operations so every newly created worktree carries\n"
    "immutable provenance in per-worktree Git configuration:\n"
    "- lifecycle ID (UUID4)\n"
    "- creation timestamp (ISO 8601 UTC)\n"
    "- branch name\n"
    "- base ref and base commit OID\n"
    "- initial head commit OID\n"
    "- optional controller kind and stable controller ID (e.g. Bossmode)\n"
    "\n"
    "Separation of Concerns:\n"
    "- Immutable provenance is recorded locally at creation time via `git config --worktree`.\n"
    "- Mutable lifecycle state (task status, claims, runs, leases, liveness, evaluation)\n"
    "  belongs to the controller (e.g. Bossmode) and is NEVER duplicated locally.\n"
    "- Non-controller caller-owned worktrees support a non-destructive manual owner-release\n"
    "  record (`manual_released_at`, `manual_released_by`).\n"
    "- `claim` / `session-release` record which live agent session (by harness session id and\n"
    "  pid + process start time, never wall-clock age) currently owns a worktree.\n"
    "- Legacy (uninstrumented) and foreign worktrees evaluate to unknown owner.\n"
    "- Zero Deletion Authority: No metadata field alone authorizes deletion. Worktree removal\n"
    "  remains strictly gated on explicit checks (clean, attached, unlocked).\n"
)

CONFIG_PREFIX = "benchbox.worktree"
KNOWN_CONTROLLER_KINDS = {"bossmode", "manual", "none"}

SESSION_ENV_VARS = (
    "PI_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_SESSION_ID",
    "AGENT_SESSION_ID",
)
WRAPPER_COMMANDS = {
    "sh",
    "bash",
    "zsh",
    "dash",
    "fish",
    "make",
    "gmake",
    "uv",
    "env",
    "python",
    "python3",
    "xargs",
    "git",
    "pre-commit",
}


def current_session_id() -> Optional[str]:
    for name in SESSION_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _ps_field(pid: int, field: str) -> Optional[str]:
    try:
        res = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except OSError:
        return None
    value = " ".join(res.stdout.split())
    return value if res.returncode == 0 and value else None


def process_start(pid: int) -> Optional[str]:
    return _ps_field(pid, "lstart")


def harness_pid() -> int:
    pid = os.getppid()
    outermost = pid
    while pid > 1:
        comm = _ps_field(pid, "comm")
        if comm is None:
            break
        outermost = pid
        name = Path(comm).name.lower().lstrip("-")
        if name not in WRAPPER_COMMANDS and not name.startswith("python"):
            return pid
        parent = _ps_field(pid, "ppid")
        if not parent or not parent.isdigit():
            break
        pid = int(parent)
    return outermost


def _owner_alive(pid_text: Optional[str], start: Optional[str]) -> bool:
    if not pid_text or not pid_text.isdigit():
        return False
    pid = int(pid_text)
    if start:
        return process_start(pid) == start
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _clear_owner(worktree_path: Path) -> None:
    for key in ("owner-session", "owner-pid", "owner-pid-start", "owner-claimed-at"):
        _run_git(["config", "--worktree", "--unset", f"{CONFIG_PREFIX}.{key}"], worktree_path)


def claim_worktree(worktree_path: Path, takeover: bool = False) -> str:
    if current_session_id() is None:
        return "no-session"
    git_dir = _run_git(["rev-parse", "--absolute-git-dir"], worktree_path).stdout.strip()
    if not git_dir:
        raise MetadataError(f"Path is not a valid git worktree: {worktree_path}")
    with open(Path(git_dir) / "benchbox-owner.lock", "a", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _claim_locked(worktree_path, takeover)


def _claim_locked(worktree_path: Path, takeover: bool) -> str:
    session = current_session_id()
    if session is None:
        return "no-session"

    ext_check = _run_git(["config", "--get", "extensions.worktreeConfig"], worktree_path)
    if ext_check.stdout.strip() != "true":
        _run_git(["config", "extensions.worktreeConfig", "true"], worktree_path)

    owner = _get_worktree_config("owner-session", worktree_path)
    if owner and owner != session and not takeover:
        if _owner_alive(
            _get_worktree_config("owner-pid", worktree_path),
            _get_worktree_config("owner-pid-start", worktree_path),
        ):
            raise MetadataError(
                f"Worktree {worktree_path} is owned by live session {owner} "
                f"(pid {_get_worktree_config('owner-pid', worktree_path)}); this session is {session}.\n"
                "Create your own worktree (make worktree-create), or, if the other session is gone, "
                "take it over with BENCHBOX_WORKTREE_TAKEOVER=1 make agent-write-preflight."
            )

    if owner == session and _owner_alive(
        _get_worktree_config("owner-pid", worktree_path),
        _get_worktree_config("owner-pid-start", worktree_path),
    ):
        return "renewed"

    pid = harness_pid()
    start = process_start(pid)
    _set_worktree_config("owner-session", session, worktree_path)
    _set_worktree_config("owner-pid", str(pid), worktree_path)
    if start:
        _set_worktree_config("owner-pid-start", start, worktree_path)
    else:
        _run_git(["config", "--worktree", "--unset", f"{CONFIG_PREFIX}.owner-pid-start"], worktree_path)
    _set_worktree_config("owner-claimed-at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), worktree_path)
    if not owner:
        return "claimed"
    return "renewed" if owner == session else "taken-over"


def release_session_claim(worktree_path: Path, force: bool = False) -> str:
    owner = _get_worktree_config("owner-session", worktree_path)
    if not owner:
        return "unowned"
    session = current_session_id()
    if owner != session and not force:
        raise MetadataError(
            f"Worktree {worktree_path} is owned by session {owner}; only the owner can release it. "
            "Pass --force (BENCHBOX_WORKTREE_TAKEOVER=1 for make) to override."
        )
    _clear_owner(worktree_path)
    return "released"


class MetadataError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class WorktreeLifecycleMetadata:
    lifecycle_id: Optional[str]
    created_at: Optional[str]
    branch: Optional[str]
    base_ref: Optional[str]
    base_oid: Optional[str]
    initial_head: Optional[str]
    controller_kind: Optional[str] = None
    controller_id: Optional[str] = None
    manual_released_at: Optional[str] = None
    manual_released_by: Optional[str] = None
    provenance_state: str = "unknown"
    owner_state: str = "unknown"
    deletion_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _get_worktree_config(key: str, cwd: Path) -> Optional[str]:
    res = _run_git(["config", "--worktree", "--get", f"{CONFIG_PREFIX}.{key}"], cwd)
    val = res.stdout.strip()
    return val if res.returncode == 0 and val else None


def _set_worktree_config(key: str, value: str, cwd: Path) -> None:
    res = _run_git(["config", "--worktree", f"{CONFIG_PREFIX}.{key}", value], cwd)
    if res.returncode != 0:
        raise MetadataError(f"Failed to write git config {CONFIG_PREFIX}.{key}: {res.stderr.strip()}")


def init_metadata(
    worktree_path: Path,
    branch: str,
    base_ref: str = "origin/develop",
    base_oid: Optional[str] = None,
    controller_kind: Optional[str] = None,
    controller_id: Optional[str] = None,
) -> WorktreeLifecycleMetadata:
    if not worktree_path.is_dir():
        raise MetadataError(f"Worktree path does not exist or is not a directory: {worktree_path}")

    res = _run_git(["rev-parse", "--is-inside-work-tree"], worktree_path)
    if res.returncode != 0:
        raise MetadataError(f"Path is not a valid git worktree: {worktree_path}")

    ext_check = _run_git(["config", "--get", "extensions.worktreeConfig"], worktree_path)
    if ext_check.stdout.strip() != "true":
        _run_git(["config", "extensions.worktreeConfig", "true"], worktree_path)

    head_res = _run_git(["rev-parse", "HEAD"], worktree_path)
    if head_res.returncode != 0 or not head_res.stdout.strip():
        raise MetadataError(f"Could not resolve HEAD in worktree: {worktree_path}")
    initial_head = head_res.stdout.strip()

    if not base_oid:
        base_res = _run_git(["rev-parse", base_ref], worktree_path)
        if base_res.returncode != 0 or not base_res.stdout.strip():
            raise MetadataError(f"Could not resolve base ref {base_ref} in worktree: {worktree_path}")
        base_oid = base_res.stdout.strip()

    existing_id = _get_worktree_config("lifecycle-id", worktree_path)
    if existing_id:
        raise MetadataError(f"Worktree already carries immutable lifecycle ID: {existing_id}")

    lifecycle_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _set_worktree_config("lifecycle-id", lifecycle_id, worktree_path)
    _set_worktree_config("created-at", created_at, worktree_path)
    _set_worktree_config("branch", branch, worktree_path)
    _set_worktree_config("base-ref", base_ref, worktree_path)
    _set_worktree_config("base-oid", base_oid, worktree_path)
    _set_worktree_config("initial-head", initial_head, worktree_path)

    clean_kind = controller_kind.strip().lower() if controller_kind else None
    clean_id = controller_id.strip() if controller_id else None

    if clean_kind:
        if clean_kind not in KNOWN_CONTROLLER_KINDS:
            raise MetadataError(
                f"Unknown controller kind '{clean_kind}'. Expected one of: {sorted(KNOWN_CONTROLLER_KINDS)}"
            )
        if clean_kind not in {"none", "manual"}:
            _set_worktree_config("controller-kind", clean_kind, worktree_path)
    if clean_id:
        _set_worktree_config("controller-id", clean_id, worktree_path)

    return read_metadata(worktree_path)


def read_metadata(worktree_path: Path) -> WorktreeLifecycleMetadata:
    if not worktree_path.is_dir():
        return WorktreeLifecycleMetadata(
            lifecycle_id=None,
            created_at=None,
            branch=None,
            base_ref=None,
            base_oid=None,
            initial_head=None,
            provenance_state="malformed",
            owner_state="unknown",
            deletion_authorized=False,
        )

    is_wt = _run_git(["rev-parse", "--is-inside-work-tree"], worktree_path)
    if is_wt.returncode != 0:
        return WorktreeLifecycleMetadata(
            lifecycle_id=None,
            created_at=None,
            branch=None,
            base_ref=None,
            base_oid=None,
            initial_head=None,
            provenance_state="unknown",
            owner_state="unknown",
            deletion_authorized=False,
        )

    lifecycle_id = _get_worktree_config("lifecycle-id", worktree_path)
    created_at = _get_worktree_config("created-at", worktree_path)
    branch = _get_worktree_config("branch", worktree_path)
    base_ref = _get_worktree_config("base-ref", worktree_path)
    base_oid = _get_worktree_config("base-oid", worktree_path)
    initial_head = _get_worktree_config("initial-head", worktree_path)
    controller_kind = _get_worktree_config("controller-kind", worktree_path)
    controller_id = _get_worktree_config("controller-id", worktree_path)
    manual_released_at = _get_worktree_config("manual-released-at", worktree_path)
    manual_released_by = _get_worktree_config("manual-released-by", worktree_path)

    if not lifecycle_id:
        return WorktreeLifecycleMetadata(
            lifecycle_id=None,
            created_at=None,
            branch=None,
            base_ref=None,
            base_oid=None,
            initial_head=None,
            provenance_state="legacy",
            owner_state="unknown",
            deletion_authorized=False,
        )

    missing_required = not all([created_at, branch, base_ref, base_oid, initial_head])
    valid_timestamp = False
    if created_at:
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            valid_timestamp = True
        except ValueError:
            valid_timestamp = False

    valid_oid = bool(base_oid and re.match(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$", base_oid))
    valid_head = bool(initial_head and re.match(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$", initial_head))

    current_branch_res = _run_git(["branch", "--show-current"], worktree_path)
    current_branch = current_branch_res.stdout.strip()
    branch_matches = (not current_branch) or (current_branch == branch)

    if missing_required or not valid_timestamp or not valid_oid or not valid_head or not branch_matches:
        return WorktreeLifecycleMetadata(
            lifecycle_id=lifecycle_id,
            created_at=created_at,
            branch=branch,
            base_ref=base_ref,
            base_oid=base_oid,
            initial_head=initial_head,
            controller_kind=controller_kind,
            controller_id=controller_id,
            manual_released_at=manual_released_at,
            manual_released_by=manual_released_by,
            provenance_state="malformed",
            owner_state="unknown",
            deletion_authorized=False,
        )

    if controller_kind and controller_kind not in KNOWN_CONTROLLER_KINDS:
        return WorktreeLifecycleMetadata(
            lifecycle_id=lifecycle_id,
            created_at=created_at,
            branch=branch,
            base_ref=base_ref,
            base_oid=base_oid,
            initial_head=initial_head,
            controller_kind=controller_kind,
            controller_id=controller_id,
            manual_released_at=manual_released_at,
            manual_released_by=manual_released_by,
            provenance_state="foreign",
            owner_state="unknown",
            deletion_authorized=False,
        )

    if manual_released_at:
        owner_state = "released"
    elif controller_kind and controller_kind not in {"none", "manual"}:
        owner_state = "controller"
    else:
        owner_state = "caller-owned"

    return WorktreeLifecycleMetadata(
        lifecycle_id=lifecycle_id,
        created_at=created_at,
        branch=branch,
        base_ref=base_ref,
        base_oid=base_oid,
        initial_head=initial_head,
        controller_kind=controller_kind,
        controller_id=controller_id,
        manual_released_at=manual_released_at,
        manual_released_by=manual_released_by,
        provenance_state="managed",
        owner_state=owner_state,
        deletion_authorized=False,
    )


def release_worktree(
    worktree_path: Path,
    released_by: Optional[str] = None,
) -> WorktreeLifecycleMetadata:
    meta = read_metadata(worktree_path)

    if meta.provenance_state != "managed":
        raise MetadataError(
            f"Cannot manually release worktree with provenance state '{meta.provenance_state}' (must be 'managed')"
        )

    if meta.controller_kind and meta.controller_kind not in {"none", "manual"}:
        raise MetadataError(
            f"Refusing manual release: worktree is controller-owned ({meta.controller_kind}). "
            "The controller manages release for controller-owned worktrees."
        )

    if meta.manual_released_at:
        return meta

    if not released_by:
        ident_res = _run_git(["config", "--get", "user.name"], worktree_path)
        released_by = ident_res.stdout.strip() or os.environ.get("USER", "unknown-caller")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_worktree_config("manual-released-by", released_by, worktree_path)
    _set_worktree_config("manual-released-at", now, worktree_path)

    return read_metadata(worktree_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Publish immutable metadata for a new worktree")
    init_parser.add_argument("--worktree-path", type=Path, required=True, help="Path to the created worktree")
    init_parser.add_argument("--branch", required=True, help="Created branch name")
    init_parser.add_argument("--base-ref", default="origin/develop", help="Base ref")
    init_parser.add_argument("--base-oid", help="Base commit OID (optional, autodetected)")
    init_parser.add_argument("--controller-kind", help="Controller kind (e.g. bossmode)")
    init_parser.add_argument("--controller-id", help="Controller stable ID")

    read_parser = subparsers.add_parser("read", help="Read worktree metadata")
    read_parser.add_argument("--worktree-path", type=Path, default=Path.cwd(), help="Path to worktree")
    read_parser.add_argument("--json", action="store_true", help="Output JSON")

    release_parser = subparsers.add_parser("release", help="Record manual owner-release")
    release_parser.add_argument("--worktree-path", type=Path, required=True, help="Path to worktree")
    release_parser.add_argument("--released-by", help="Operator or actor name")

    claim_parser = subparsers.add_parser("claim", help="Claim session ownership of a worktree")
    claim_parser.add_argument("--worktree-path", type=Path, default=Path.cwd(), help="Path to worktree")
    claim_parser.add_argument("--takeover", action="store_true", help="Replace a live owner")

    session_release_parser = subparsers.add_parser("session-release", help="Give up session ownership")
    session_release_parser.add_argument("--worktree-path", type=Path, default=Path.cwd(), help="Path to worktree")
    session_release_parser.add_argument("--force", action="store_true", help="Release another session's claim")

    args = parser.parse_args()

    try:
        if args.command == "init":
            meta = init_metadata(
                worktree_path=args.worktree_path,
                branch=args.branch,
                base_ref=args.base_ref,
                base_oid=args.base_oid,
                controller_kind=args.controller_kind,
                controller_id=args.controller_id,
            )
            print(
                f"Initialized lifecycle metadata for worktree: {args.worktree_path} (lifecycle_id: {meta.lifecycle_id})"
            )
            return 0

        elif args.command == "read":
            meta = read_metadata(args.worktree_path)
            if args.json:
                print(json.dumps(meta.to_dict(), indent=2))
            else:
                print(f"Worktree: {args.worktree_path.resolve()}")
                print(f"  Provenance State : {meta.provenance_state}")
                print(f"  Owner State      : {meta.owner_state}")
                print(f"  Lifecycle ID     : {meta.lifecycle_id or '<none>'}")
                print(f"  Created At       : {meta.created_at or '<none>'}")
                print(f"  Branch           : {meta.branch or '<none>'}")
                print(f"  Base Ref         : {meta.base_ref or '<none>'}")
                print(f"  Base OID         : {meta.base_oid or '<none>'}")
                print(f"  Initial Head     : {meta.initial_head or '<none>'}")
                if meta.controller_kind:
                    print(f"  Controller Kind  : {meta.controller_kind}")
                if meta.controller_id:
                    print(f"  Controller ID    : {meta.controller_id}")
                if meta.manual_released_at:
                    print(f"  Released At      : {meta.manual_released_at}")
                    print(f"  Released By      : {meta.manual_released_by or '<none>'}")
                print(f"  Deletion Auth    : {meta.deletion_authorized}")
            return 0

        elif args.command == "release":
            meta = release_worktree(args.worktree_path, released_by=args.released_by)
            print(
                f"Manual owner release recorded for worktree: {args.worktree_path} (released_at: {meta.manual_released_at})"
            )
            return 0

        elif args.command == "claim":
            claim_worktree(args.worktree_path, takeover=args.takeover)
            return 0

        elif args.command == "session-release":
            print(
                f"Session ownership {release_session_claim(args.worktree_path, force=args.force)}: {args.worktree_path}"
            )
            return 0

    except MetadataError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
