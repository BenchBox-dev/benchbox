from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from benchbox.cli.exit_codes import ExitCode
from benchbox.core.tpcds.qualification import runner
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.resource_limits import run_text
from benchbox.utils.run_deadline import HARD_STOP_GRACE_SECONDS, RunDeadline

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only"),
]

REPO_ROOT = Path(__file__).resolve().parents[3]

TERM_IGNORING_TREE = (
    "import os, pathlib, psutil, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', "
    "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1, flush=True); time.sleep(300)'], "
    "stdout=subprocess.PIPE)\n"
    "grandchild.stdout.readline()\n"
    "target = pathlib.Path(sys.argv[1])\n"
    "staging = target.with_name(target.name + '.tmp')\n"
    "staging.write_text(f'{os.getpid()} {psutil.Process().create_time()} "
    "{grandchild.pid} {psutil.Process(grandchild.pid).create_time()}')\n"
    "os.replace(staging, target)\n"
    "time.sleep(300)\n"
)

HARD_STOP_LIMIT = 1

HARD_STOP_EXIT_SECONDS = 5.0

HARD_STOP_DRIVER = (
    "import psutil, subprocess, sys, time\n"
    "from benchbox.cli.run_time_limit import hard_stop\n"
    "from benchbox.utils.run_deadline import RunDeadline, arm_hard_stop\n"
    "deadline = RunDeadline(1)\n"
    "began = time.monotonic()\n"
    "def recording_hard_stop(target):\n"
    "    print(f'hard-stop {time.monotonic()}', flush=True)\n"
    "    hard_stop(target)\n"
    "leaf = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1, flush=True); "
    "time.sleep(300)'\n"
    "child = subprocess.Popen([sys.executable, '-c', leaf], stdout=subprocess.PIPE, start_new_session=True)\n"
    "child.stdout.readline()\n"
    "with deadline.tracking(child):\n"
    "    arm_hard_stop(deadline, recording_hard_stop)\n"
    "    print(f'armed {child.pid} {psutil.Process(child.pid).create_time()} {began}', flush=True)\n"
    "    time.sleep(300)\n"
)

SIGKILL = signal.SIGKILL if hasattr(signal, "SIGKILL") else 9


def _wait_for_pids(marker: Path, seconds: float = 30.0) -> list[tuple[int, float]]:
    started = mono_time()
    while not marker.exists():
        assert elapsed_seconds(started) < seconds, "the child never reported that it ignores SIGTERM"
        time.sleep(0.05)
    fields = marker.read_text(encoding="utf-8").split()
    return [(int(pid), float(created)) for pid, created in zip(fields[::2], fields[1::2], strict=True)]


def _owned(pid: int, created: float) -> psutil.Process | None:
    try:
        process = psutil.Process(pid)
        return process if process.create_time() == created else None
    except psutil.NoSuchProcess:
        return None


def _is_gone(pid: int, created: float) -> bool:
    process = _owned(pid, created)
    try:
        return process is None or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _wait_until_gone(owned: list[tuple[int, float]], seconds: float = 10.0) -> bool:
    started = mono_time()
    while not all(_is_gone(pid, created) for pid, created in owned):
        if elapsed_seconds(started) >= seconds:
            return False
        time.sleep(0.05)
    return True


def _reap(*owned: tuple[int, float]) -> None:
    for pid, created in owned:
        process = _owned(pid, created)
        if process is None:
            continue
        try:
            process.kill()
        except psutil.Error:
            pass


def test_run_text_kills_a_child_tree_that_ignores_sigterm(tmp_path: Path) -> None:
    marker = tmp_path / "pids"
    timeout = 4.0
    started = mono_time()
    pids: list[int] = []
    try:
        assert run_text([sys.executable, "-c", TERM_IGNORING_TREE, str(marker)], timeout) is None
        elapsed = elapsed_seconds(started)
        pids = _wait_for_pids(marker, 0.0)
        assert _wait_until_gone(pids)
        assert elapsed < timeout + 3.0
    finally:
        _reap(*pids)


def test_stop_children_kills_a_child_tree_that_ignores_sigterm(tmp_path: Path) -> None:
    marker = tmp_path / "pids"
    deadline = RunDeadline(300)
    child = subprocess.Popen([sys.executable, "-c", TERM_IGNORING_TREE, str(marker)], start_new_session=True)
    pids: list[int] = []
    try:
        with deadline.tracking(child):
            pids = _wait_for_pids(marker)
            assert child.poll() is None
            deadline.stop_children()
            assert child.wait(10) == -SIGKILL
            assert deadline.stopped(child)
        assert _wait_until_gone(pids)
    finally:
        _reap(*pids)
        child.kill()
        child.wait()


def test_the_qualification_worker_kill_ends_a_child_tree_that_ignores_sigterm(tmp_path: Path) -> None:
    marker = tmp_path / "pids"
    process = subprocess.Popen([sys.executable, "-c", TERM_IGNORING_TREE, str(marker)], start_new_session=True)
    pids: list[int] = []
    try:
        pids = _wait_for_pids(marker)
        assert process.poll() is None
        runner._kill_worker(process)
        assert process.returncode == -SIGKILL
        assert _wait_until_gone(pids)
    finally:
        _reap(*pids)
        process.kill()
        process.wait()


def test_a_supervised_worker_that_ignores_sigterm_is_ended_at_the_overall_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "pids"
    pids: list[int] = []
    try:
        code = runner.supervise(
            [sys.executable, "-c", TERM_IGNORING_TREE, str(marker)], tmp_path / "events.jsonl", tmp_path / "log", 60, 4
        )
        pids = _wait_for_pids(marker, 0.0)
        assert code == 1
        assert _wait_until_gone(pids)
    finally:
        _reap(*pids)


@pytest.mark.slow
def test_the_hard_stop_starts_at_limit_plus_grace_and_exits_in_bound_with_a_term_ignoring_child() -> None:
    limit = HARD_STOP_LIMIT
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])}
    driver = subprocess.Popen(
        [sys.executable, "-c", HARD_STOP_DRIVER],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    child_pid: tuple[int, float] | None = None
    try:
        assert driver.stdout is not None
        armed = driver.stdout.readline().split()
        assert armed[0] == "armed"
        child_pid = (int(armed[1]), float(armed[2]))
        began = float(armed[3])
        started = mono_time()
        assert _wait_until_gone([child_pid], limit + 5.0)
        assert elapsed_seconds(started) < limit + 5.0
        assert driver.poll() is None
        assert driver.wait(limit + HARD_STOP_GRACE_SECONDS + HARD_STOP_EXIT_SECONDS + 10.0) == ExitCode.TIMEOUT
        exited = time.monotonic()
        hard_stop_lines = [line.split() for line in driver.stdout.read().splitlines() if line.startswith("hard-stop ")]
        assert len(hard_stop_lines) == 1
        hard_stop_began = float(hard_stop_lines[0][1]) - began
        assert abs(hard_stop_began - (limit + HARD_STOP_GRACE_SECONDS)) <= 0.5, hard_stop_began
        assert exited - began - hard_stop_began < HARD_STOP_EXIT_SECONDS
    finally:
        if child_pid is not None:
            _reap(child_pid)
        if driver.poll() is None:
            driver.kill()
        driver.wait()
        if driver.stdout is not None:
            driver.stdout.close()
