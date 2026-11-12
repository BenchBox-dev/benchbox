#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

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
PATCH_HEADER = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Edit File|Move to): (.+?)(?: all)?\s*$", re.MULTILINE
)
EDIT_HEADER = re.compile(r"^\[([^\]\n]+?)(?:#[^\]\n]*)?\]\s*$", re.MULTILINE)
MOVE_LINE = re.compile(r"^MV[ \t]+(.+?)\s*$", re.MULTILINE)
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
OWNER_LINE = re.compile(r"^\s*owner\s*=\s*(.*?)\s*$", re.MULTILINE)
OWNER_LOOKUP_SECONDS = 0.5
GIT_TIMEOUT_SECONDS = 5


def _git(args: list[str], cwd: Path) -> str | None:
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX"}}
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout if result.returncode == 0 else None


def _clean_target(raw: str) -> str | None:
    value = raw.strip()
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        value = value[1:-1].rsplit("#", 1)[0] if "#" in value else value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not value:
        return None
    if value.casefold().startswith("file://"):
        return unquote(urlparse(value).path) or None
    if URI_SCHEME.match(value):
        return None
    return value


def target_paths(tool_name: str, tool_input: object) -> list[str]:
    if tool_name.casefold() not in WRITE_TOOLS or not isinstance(tool_input, dict):
        return []
    raw: list[str] = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str):
            raw.append(value)
    paths = tool_input.get("paths")
    if isinstance(paths, list):
        raw.extend(item for item in paths if isinstance(item, str))
    for key in ("command", "input"):
        text = tool_input.get(key)
        if isinstance(text, str):
            raw.extend(PATCH_HEADER.findall(text))
            raw.extend(EDIT_HEADER.findall(text))
            raw.extend(MOVE_LINE.findall(text))
    cleaned = (_clean_target(item) for item in raw)
    return list(dict.fromkeys(item for item in cleaned if item))


def _ancestors(path: Path) -> list[Path]:
    return [path, *path.parents]


def _inside(path: Path, root: Path) -> bool:
    if not root.exists():
        return False
    for ancestor in _ancestors(path):
        try:
            if ancestor.exists() and os.path.samefile(ancestor, root):
                return True
        except OSError:
            continue
    return False


def _nearest_existing(path: Path) -> Path:
    for ancestor in _ancestors(path):
        if ancestor.is_dir():
            return ancestor
    return path.anchor and Path(path.anchor) or Path("/")


def _common_dir(cwd: Path) -> Path | None:
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(common.strip()).resolve() if common and common.strip() else None


def repository_layout(cwd: Path) -> tuple[Path, Path, list[Path]] | None:
    common_dir = _common_dir(cwd)
    listing = _git(["worktree", "list", "--porcelain"], cwd)
    if common_dir is None or listing is None:
        return None
    override = os.environ.get(PRIMARY_ENV)
    primary = Path(override).resolve() if override else common_dir.parent
    worktrees = [
        Path(line[len("worktree ") :]).resolve() for line in listing.splitlines() if line.startswith("worktree ")
    ]
    linked = [path for path in worktrees if not _same(path, primary)]
    return common_dir, primary, linked


def _same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left == right


def _session_ids(payload: dict[str, object]) -> set[str]:
    ids = {os.environ[name].strip() for name in SESSION_ENV if os.environ.get(name, "").strip()}
    session = payload.get("session_id")
    if isinstance(session, str) and session:
        ids.add(session)
    return ids


def _git_dir_of(worktree: Path) -> Path | None:
    marker = worktree / ".git"
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    return Path(text[len("gitdir:") :].strip())


def _owner_session(worktree: Path) -> str | None:
    git_dir = _git_dir_of(worktree)
    if git_dir is None:
        return None
    try:
        text = (git_dir / "config.worktree").read_text(encoding="utf-8")
    except OSError:
        return None
    match = OWNER_LINE.search(text)
    if match is None:
        return None
    value = match.group(1)
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        record = json.loads(value)
    except ValueError:
        return None
    session = record.get("session") if isinstance(record, dict) else None
    return session if isinstance(session, str) else None


def _owned_worktrees(linked: list[Path], sessions: set[str]) -> list[Path]:
    if not sessions:
        return []
    deadline = time.monotonic() + OWNER_LOOKUP_SECONDS
    owned = []
    for path in linked:
        if time.monotonic() > deadline:
            break
        if _owner_session(path) in sessions:
            owned.append(path)
    return owned


def suggested_worktrees(cwd: Path, linked: list[Path], payload: dict[str, object]) -> list[Path]:
    for path in linked:
        if _inside(cwd, path):
            return [path]
    owned = _owned_worktrees(linked, _session_ids(payload))
    return owned or sorted(linked, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:3]


def _known_repositories(cwd: Path) -> list[Path]:
    known = []
    for start in (cwd, Path(__file__).resolve().parent):
        common = _common_dir(start) if start.is_dir() else None
        if common is not None:
            known.append(common)
    return known


def decide(payload: dict[str, object]) -> str | None:
    if any(os.environ.get(name) == "1" for name in OVERRIDE_ENV):
        return None
    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else Path.cwd()
    tool_name = payload.get("tool_name")
    targets = target_paths(tool_name if isinstance(tool_name, str) else "", payload.get("tool_input"))
    if not targets:
        return None
    known = _known_repositories(cwd)
    refused: list[tuple[str, Path, Path, Path, list[Path]]] = []
    for target in targets:
        resolved = (cwd / os.path.expanduser(target)).resolve()
        layout = repository_layout(_nearest_existing(resolved))
        if layout is None:
            continue
        common_dir, primary, linked = layout
        if not linked or not any(_same(common_dir, repo) for repo in known):
            continue
        if any(_inside(resolved, path) for path in linked) or _inside(resolved, common_dir):
            continue
        if _inside(resolved, primary):
            refused.append((target, resolved, primary, common_dir, linked))
    if not refused:
        return None
    primary = refused[0][2]
    linked = refused[0][4]
    suggestions = suggested_worktrees(cwd, linked, payload)
    lines = [
        "Refusing file write into the primary clone "
        f"{primary}: the primary clone is read-only for agent work, and relative paths resolve against it.",
    ]
    for target, resolved, *_ in refused:
        lines.append(f"  target: {target} -> {resolved}")
    lines.append("Use the absolute path inside your linked worktree instead:")
    for _target, resolved, root, *_ in refused:
        relative = _relative_to_root(resolved, root)
        lines.extend(f"  {worktree / relative}" for worktree in suggestions)
    lines.append(
        "Create one with `make worktree-create` if none is yours. Owner override: BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1."
    )
    return "\n".join(lines)


def _relative_to_root(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        for ancestor in _ancestors(path):
            if ancestor.exists() and _same(ancestor, root):
                return Path(*path.parts[len(ancestor.parts) :])
        return Path(path.name)


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
