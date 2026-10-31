from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from benchbox.utils import heavy_status, host_lock

LAUNCHCTL_TIMEOUT_SECONDS = 30.0
PREFLIGHT_TIMEOUT_SECONDS = 60.0
THROTTLE_SECONDS = 30
DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
FORWARDED_PREFIXES = ("BENCHBOX_HEAVY_", "BENCHBOX_GIT_FLOOD_")
FORWARDED_KEYS = (host_lock.LOCK_DIR_ENV,)
NOT_LOADED_CODES = frozenset({3, 113})
NOT_LOADED_MARKERS = ("no such process", "could not find service", "not loaded")


def forwarded_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(environ.items())
        if value and (key.startswith(FORWARDED_PREFIXES) or key in FORWARDED_KEYS)
    }


def build_plist(python: str, repo: Path, environ: Mapping[str, str]) -> dict[str, object]:
    return {
        "Label": heavy_status.LAUNCH_AGENT_LABEL,
        "ProgramArguments": [python, "-m", "benchbox.utils.host_lock", "sentinel"],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {"PATH": DEFAULT_PATH, **forwarded_environment(environ)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": os.devnull,
        "StandardErrorPath": str(heavy_status.sentinel_launchd_err_path()),
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


def _preflight(interpreter: str, repo: Path) -> str | None:
    try:
        result = subprocess.run(
            [interpreter, "-m", "benchbox.utils.host_lock", "sentinel", "--help"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None if result.returncode == 0 else (result.stderr.strip() or f"exit {result.returncode}")


def _not_loaded(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout} {result.stderr}".lower()
    return result.returncode in NOT_LOADED_CODES or any(marker in text for marker in NOT_LOADED_MARKERS)


def install(environ: Mapping[str, str], *, repo: Path, python: str | None, launchctl: str = "launchctl") -> int:
    repo = repo.resolve()
    default_python = repo / ".venv" / "bin" / "python"
    interpreter = (
        os.path.abspath(os.path.expanduser(python))
        if python
        else str(default_python if default_python.is_file() else sys.executable)
    )
    failure = _preflight(interpreter, repo)
    if failure is not None:
        sys.stderr.write(
            f"sentinel preflight failed ({interpreter} -m benchbox.utils.host_lock sentinel --help in {repo}): "
            f"{failure}\n"
        )
        return 1
    plist_path = heavy_status.launch_agent_path(environ)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    heavy_status.sentinel_launchd_err_path().parent.mkdir(parents=True, exist_ok=True)
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
    if result is None or (result.returncode != 0 and not _not_loaded(result)):
        detail = "launchctl unavailable" if result is None else result.stderr.strip()
        sys.stderr.write(f"launchctl bootout failed; keeping {plist_path}: {detail}\n")
        return 1
    try:
        plist_path.unlink()
        sys.stdout.write(f"removed {plist_path}\n")
    except FileNotFoundError:
        sys.stdout.write(f"{plist_path} was not installed\n")
    return 0
