from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from benchbox.utils import heavy_status, heavy_watchdog

LAUNCHCTL_TIMEOUT_SECONDS = 30.0
THROTTLE_SECONDS = 30
DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"


def build_plist(python: str, repo: Path, environ: Mapping[str, str]) -> dict[str, object]:
    variables = {"PATH": DEFAULT_PATH}
    mode = (environ.get(heavy_watchdog.MODE_ENV) or "").strip().lower()
    if mode in heavy_watchdog.MODES:
        variables[heavy_watchdog.MODE_ENV] = mode
    return {
        "Label": heavy_status.LAUNCH_AGENT_LABEL,
        "ProgramArguments": [python, "-m", "benchbox.utils.host_lock", "sentinel"],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": variables,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": os.devnull,
        "StandardErrorPath": os.devnull,
    }


def _launchctl(argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            list(argv), capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(environ: Mapping[str, str], *, repo: Path, python: str | None, launchctl: str = "launchctl") -> int:
    interpreter = python or (
        str(repo / ".venv" / "bin" / "python") if (repo / ".venv" / "bin" / "python").is_file() else sys.executable
    )
    plist_path = heavy_status.launch_agent_path(environ)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.is_file():
        _launchctl([launchctl, "bootout", f"{_domain()}/{heavy_status.LAUNCH_AGENT_LABEL}"])
    staging = plist_path.with_suffix(".plist.tmp")
    staging.write_bytes(plistlib.dumps(build_plist(interpreter, repo, environ)))
    os.replace(staging, plist_path)
    result = _launchctl([launchctl, "bootstrap", _domain(), str(plist_path)])
    if result is None or result.returncode != 0:
        detail = "launchctl unavailable" if result is None else result.stderr.strip()
        sys.stderr.write(f"launchctl bootstrap failed for {plist_path}: {detail}\n")
        return 1
    sys.stdout.write(f"installed {plist_path} (python {interpreter}, checkout {repo})\n")
    return 0


def uninstall(environ: Mapping[str, str], *, launchctl: str = "launchctl") -> int:
    plist_path = heavy_status.launch_agent_path(environ)
    result = _launchctl([launchctl, "bootout", f"{_domain()}/{heavy_status.LAUNCH_AGENT_LABEL}"])
    if result is None:
        sys.stderr.write("launchctl unavailable; removing the plist only\n")
    try:
        plist_path.unlink()
        sys.stdout.write(f"removed {plist_path}\n")
    except FileNotFoundError:
        sys.stdout.write(f"{plist_path} was not installed\n")
    return 0
