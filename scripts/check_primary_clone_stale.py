from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REMOTE_REF = "origin/develop"
WATCHED_PATHS = (
    ".claude/settings.json",
    ".codex/hooks.json",
    ".gemini/settings.json",
    ".omp/hooks",
    ".omp/extensions",
    ".mcp.json",
    "scripts/primary_clone_write_guard.py",
    "_project/scripts/pyproject.toml",
    "_project/scripts/uv.lock",
    "_project/scripts/vendor",
)
TRACKER_PACKAGE = "tractable"
SCRIPTS_PROJECT = "_project/scripts"
UPDATE_DOC = "primary-clone-update.md in the agent docs"


def _git(primary: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(primary), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def watched_paths_behind(primary: Path, remote_ref: str = REMOTE_REF) -> list[str] | None:
    if _git(primary, "rev-parse", "--verify", "--quiet", remote_ref) is None:
        return None
    output = _git(primary, "log", "--name-only", "--format=", f"HEAD..{remote_ref}", "--", *WATCHED_PATHS)
    if output is None:
        return None
    return sorted({line for line in output.splitlines() if line})


def pinned_version(primary: Path, remote_ref: str = REMOTE_REF) -> str | None:
    lock = _git(primary, "show", f"{remote_ref}:{SCRIPTS_PROJECT}/uv.lock")
    if lock is None:
        return None
    match = re.search(rf'^name = "{TRACKER_PACKAGE}"\nversion = "([^"]+)"', lock, re.MULTILINE)
    return match.group(1) if match else None


def installed_version(primary: Path) -> str | None:
    site_packages = sorted((primary / SCRIPTS_PROJECT / ".venv").glob("lib/python*/site-packages"))
    site_packages += [primary / SCRIPTS_PROJECT / ".venv" / "Lib" / "site-packages"]
    for directory in site_packages:
        for dist_info in directory.glob(f"{TRACKER_PACKAGE}-*.dist-info"):
            return dist_info.name.removeprefix(f"{TRACKER_PACKAGE}-").removesuffix(".dist-info")
    return None


def stale_warnings(primary: Path, remote_ref: str = REMOTE_REF) -> list[str]:
    warnings = []
    behind = watched_paths_behind(primary, remote_ref)
    if behind:
        listed = "\n".join(f"    {path}" for path in behind)
        warnings.append(
            f"The primary clone is behind {remote_ref} on paths that agent sessions load from it:\n{listed}"
        )
    pinned = pinned_version(primary, remote_ref)
    installed = installed_version(primary)
    if pinned and installed and pinned != installed:
        warnings.append(
            f"The primary clone's tracker runtime is {TRACKER_PACKAGE} {installed}, but {remote_ref} pins {pinned}."
        )
    return warnings


def message(primary: Path, warnings: list[str]) -> str:
    return (
        "WARNING: sessions started in the primary clone may run outdated hooks or an outdated tracker server.\n"
        + "\n".join(warnings)
        + "\nThe owner updates it (an agent only with explicit owner approval in the current task), then restarts sessions:\n"
        f"  git -C {primary} pull --ff-only origin develop\n"
        f"  uv sync --project {primary / SCRIPTS_PROJECT} --locked\n"
        f"Procedure: {UPDATE_DOC}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_primary_clone_stale.py <primary-clone>", file=sys.stderr)
        return 2
    primary = Path(argv[1])
    warnings = stale_warnings(primary)
    if warnings:
        print(message(primary, warnings), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
