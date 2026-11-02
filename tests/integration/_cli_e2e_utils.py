from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from pathlib import Path

CLI_MODULE = "benchbox.cli.main"
DEFAULT_TIMEOUT = 120.0
PUBLICATION_COMMANDS = frozenset({"export", "submit", "publish"})


def writes_pseudonym_salt(args: Sequence[str]) -> bool:
    command = next((arg for arg in args if not arg.startswith("-")), None)
    return command in PUBLICATION_COMMANDS


@contextmanager
def isolated_cli_home() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="benchbox-cli-home-") as home:
        yield Path(home)


def cli_subprocess_environment(
    *,
    home: Path | str | None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    effective_env: MutableMapping[str, str] = os.environ.copy()
    if home is not None:
        effective_env["HOME"] = str(home)
        effective_env["USERPROFILE"] = str(home)
    effective_env["PYTHONUTF8"] = "1"
    if cwd is not None:
        effective_env["BENCHBOX_OUTPUT_DIR"] = str(Path(cwd) / "benchmark_runs")
    if env:
        effective_env.update(env)
    return effective_env


def run_cli_command(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    home: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    if home is None and writes_pseudonym_salt(args):
        with isolated_cli_home() as default_home:
            return run_cli_command(args, cwd=cwd, env=env, timeout=timeout, home=default_home)

    command = [sys.executable, "-m", CLI_MODULE, *args]

    effective_env = cli_subprocess_environment(home=home, cwd=cwd, env=env)

    resolved_cwd = str(cwd) if cwd is not None else None

    return subprocess.run(
        command,
        cwd=resolved_cwd,
        env=effective_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
