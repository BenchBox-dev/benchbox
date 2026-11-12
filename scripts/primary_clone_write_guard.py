#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REFUSED = 2
OVERRIDE_ENV = ("BENCHBOX_ALLOW_MAIN_CLONE_WRITE", "ALLOW_MAIN_CLONE_WRITE")
PRIMARY_ENV = "BENCHBOX_AGENT_PRIMARY_CLONE"
SESSION_ENV = ("PI_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_SESSION_ID", "AGENT_SESSION_ID")
WRITE_TOOLS = frozenset(
    {
        "write",
        "edit",
        "multiedit",
        "notebookedit",
        "notebook",
        "ast_edit",
        "write_file",
        "replace",
        "apply_patch",
    }
)
PATH_KEYS = ("file_path", "filePath", "path", "notebook_path")
PATCH_HEADER = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)
EDIT_HEADER = re.compile(r"^\[(.+?)#[0-9A-Fa-f]{4}\]\s*$", re.MULTILINE)


def _git(args: list[str], cwd: Path) -> str | None:
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX"}}
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False, env=env
    )
    return result.stdout if result.returncode == 0 else None


def target_paths(tool_name: str, tool_input: object) -> list[str]:
    if tool_name.casefold() not in WRITE_TOOLS or not isinstance(tool_input, dict):
        return []
    found: list[str] = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    paths = tool_input.get("paths")
    if isinstance(paths, list):
        found.extend(item for item in paths if isinstance(item, str) and item)
    for key in ("command", "input"):
        text = tool_input.get(key)
        if isinstance(text, str):
            found.extend(PATCH_HEADER.findall(text))
            found.extend(EDIT_HEADER.findall(text))
    return list(dict.fromkeys(found))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def repository_layout(cwd: Path) -> tuple[Path, Path, list[Path]] | None:
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    listing = _git(["worktree", "list", "--porcelain"], cwd)
    if common is None or listing is None:
        return None
    common_dir = Path(common.strip()).resolve()
    override = os.environ.get(PRIMARY_ENV)
    primary = Path(override).resolve() if override else common_dir.parent
    worktrees = [
        Path(line[len("worktree ") :]).resolve() for line in listing.splitlines() if line.startswith("worktree ")
    ]
    linked = [path for path in worktrees if path != primary]
    return common_dir, primary, linked


def _session_ids(payload: dict[str, object]) -> set[str]:
    ids = {os.environ[name].strip() for name in SESSION_ENV if os.environ.get(name, "").strip()}
    session = payload.get("session_id")
    if isinstance(session, str) and session:
        ids.add(session)
    return ids


def _owned_worktrees(linked: list[Path], sessions: set[str]) -> list[Path]:
    if not sessions:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from worktree_lifecycle_metadata import _read_owner

    owned = []
    for path in linked:
        record = _read_owner(path)
        if record is not None and record["session"] in sessions:
            owned.append(path)
    return owned


def suggested_worktrees(cwd: Path, linked: list[Path], payload: dict[str, object]) -> list[Path]:
    for path in linked:
        if _inside(cwd, path):
            return [path]
    try:
        owned = _owned_worktrees(linked, _session_ids(payload))
    except Exception:
        owned = []
    return owned or sorted(linked, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:3]


def decide(payload: dict[str, object]) -> str | None:
    if any(os.environ.get(name) == "1" for name in OVERRIDE_ENV):
        return None
    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else Path.cwd()
    tool_name = payload.get("tool_name")
    targets = target_paths(tool_name if isinstance(tool_name, str) else "", payload.get("tool_input"))
    if not targets:
        return None
    layout = repository_layout(cwd)
    if layout is None:
        return None
    common_dir, primary, linked = layout
    if not linked:
        return None
    refused = []
    for target in targets:
        resolved = (cwd / os.path.expanduser(target)).resolve()
        if any(_inside(resolved, path) for path in linked) or _inside(resolved, common_dir):
            continue
        if _inside(resolved, primary):
            refused.append((target, resolved))
    if not refused:
        return None
    suggestions = suggested_worktrees(cwd, linked, payload)
    lines = [
        "Refusing file write into the primary clone "
        f"{primary}: the primary clone is read-only for agent work, and relative paths resolve against it.",
    ]
    for target, resolved in refused:
        lines.append(f"  target: {target} -> {resolved}")
    lines.append("Use the absolute path inside your linked worktree instead:")
    for target, resolved in refused:
        relative = resolved.relative_to(primary)
        lines.extend(f"  {worktree / relative}" for worktree in suggestions)
    lines.append(
        "Create one with `make worktree-create` if none is yours. Owner override: BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1."
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        message = decide(payload)
    except Exception as error:
        print(f"primary-clone write guard failed open on internal error: {error!r}", file=sys.stderr)
        return 0
    if message is None:
        return 0
    print(message, file=sys.stderr)
    return REFUSED


if __name__ == "__main__":
    sys.exit(main())
