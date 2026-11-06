from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from benchbox.utils import host_lock as hl
from benchbox.utils.heavy_preflight_scope import rejection_message, wraps_pr_preflight
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]

HOST_LOCK_FILE = REPO_ROOT / "benchbox" / "utils" / "host_lock.py"
PRELUDE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('benchbox.utils.host_lock', sys.argv[1])\n"
    "hl = importlib.util.module_from_spec(spec)\n"
    "sys.modules[spec.name] = hl\n"
    "spec.loader.exec_module(hl)\n"
)
CLI_PROGRAM = PRELUDE + "raise SystemExit(hl.main(sys.argv[2:]))\n"
HOLDER_PROGRAM = (
    PRELUDE
    + "import time\n"
    + "hl.acquire(hl.lock_path(), wait_seconds=0, phase='unrelated')\n"
    + "print('ready', flush=True)\n"
    + "time.sleep(60)\n"
)


@pytest.mark.parametrize(
    "command",
    [
        ["make", "pr-preflight"],
        ["gmake", "-s", "pr-preflight", "BASE=origin/develop"],
        ["/usr/bin/make", "BASE=x", "pr-preflight"],
        ["make", "-C", "/work/tree", "-f", "Makefile", "-j", "4", "lint", "pr-preflight"],
        ["make", "-sj4", "pr-preflight"],
        ["make", "--directory=/work", "--", "pr-preflight"],
        ["sh", "-c", "make pr-preflight"],
        ["bash", "-c", "cd /work && make -s pr-preflight | tee preflight.log"],
        ["bash", "-lc", "make pr-preflight"],
        ["bash", "-o", "pipefail", "-ec", "make pr-preflight"],
        ["sh", "-c", "sh -c 'FOO=1 make pr-preflight'"],
        ["sh", "-c", "if true; then\n  make pr-preflight\nfi"],
        ["sh", "-c", "echo $(make pr-preflight)"],
        ["sh", "-c", "echo start; (make pr-preflight)"],
        ["sh", "-c", 'make "$@"', "_", "pr-preflight"],
        ["sh", "-c", 'echo "never closed; make pr-preflight'],
        ["env", "A=1", "make", "pr-preflight"],
        ["uv", "run", "--", "make", "pr-preflight"],
        ["timeout", "600", "make", "pr-preflight"],
    ],
)
def test_whole_pipeline_wrappers_are_classified(command: list[str]) -> None:
    assert wraps_pr_preflight(command)
    assert rejection_message(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        ["make", "pr-preflight-fast-tests"],
        ["make", "test-fast"],
        ["make", "-C", "pr-preflight", "lint"],
        ["make", "-f", "pr-preflight", "lint"],
        ["make", "BASE=pr-preflight", "lint"],
        ["sh", "-c", "echo 'make pr-preflight'"],
        ["sh", "-c", 'git commit -m "make pr-preflight"; make lint'],
        ["sh", "-c", "grep -n pr-preflight Makefile"],
        ["bash", "script.sh", "pr-preflight"],
        ["bash", "-c", "make lint", "pr-preflight"],
        ["python", "-m", "pytest", "tests/unit/scripts/test_pr_preflight_base.py", "-m", "slow"],
        ["cat", "make", "pr-preflight"],
        ["bash", "/repo/scripts/linux_machine_run.sh", "benchbox-agent", "--", "pr-preflight"],
    ],
)
def test_safe_commands_are_not_classified(command: list[str]) -> None:
    assert not wraps_pr_preflight(command)
    assert rejection_message(command) is None


@pytest.fixture
def lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "lock"
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(hl.WAIT_ENV, "600")
    monkeypatch.delenv(hl.OWNER_ENV, raising=False)
    return directory / hl.LOCK_NAME


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", CLI_PROGRAM, str(HOST_LOCK_FILE), *args],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_wrapped_preflight_is_rejected_without_waiting_on_a_held_lock(lock_file: Path) -> None:
    handle = hl.acquire(lock_file, wait_seconds=0, phase="unrelated")
    try:
        started = time.monotonic()
        result = _run("run", "--admit", "--guard", "--", "sh", "-c", "make pr-preflight")
        elapsed = time.monotonic() - started
    finally:
        handle.release()

    assert result.returncode == hl.EXIT_USAGE == 2
    assert result.returncode != hl.EXIT_REFUSED
    assert elapsed < 10
    assert "Run `make pr-preflight` directly" in result.stderr
    assert "waiting" not in result.stderr


def test_rejected_payload_never_starts_or_creates_a_holder_file(lock_file: Path, tmp_path: Path) -> None:
    marker = tmp_path / "started"

    result = _run("run", "--", "sh", "-c", f"touch {marker}; make pr-preflight")

    assert result.returncode == hl.EXIT_USAGE
    assert not marker.exists()
    assert not lock_file.exists()
    assert not lock_file.parent.exists()


def test_safe_heavy_commands_still_acquire_the_lock(lock_file: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"

    result = _run("run", "--", "sh", "-c", f"echo 'make pr-preflight' > {marker}")

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").strip() == "make pr-preflight"
    assert lock_file.exists()


def test_safe_heavy_commands_still_wait_on_a_held_lock(lock_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hl.WAIT_ENV, "0")
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER_PROGRAM, str(HOST_LOCK_FILE)],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        result = _run("run", "--", "sh", "-c", "make pr-preflight-fast-tests")
    finally:
        holder.kill()
        holder.wait(timeout=10)
        if holder.stdout is not None:
            holder.stdout.close()

    assert result.returncode == hl.EXIT_REFUSED
