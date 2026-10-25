from __future__ import annotations

import os
import signal
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

from tests.uat import runner

pytestmark = [pytest.mark.unit, pytest.mark.medium]

_CELL_TIMEOUT_S = 2
_REAP_GRACE_S = 10.0

_CHILD_LIFETIME_S = 3000

_RETURN_CEILING_S = 90.0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _await_death(pid: int, timeout_s: float = _REAP_GRACE_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _run_cell_bounded(tmp_path, *, timeout_s: int = _CELL_TIMEOUT_S) -> tuple[bool, dict]:
    outcome: dict = {}

    def _call() -> None:
        try:
            outcome["result"] = runner.run_cell(
                "duckdb",
                "tpch",
                0.01,
                timeout_s=timeout_s,
                log_dir=tmp_path / "logs",
                benchmark_runs_dir=tmp_path / "runs",
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()
    worker.join(timeout=_RETURN_CEILING_S)
    return (not worker.is_alive()), outcome


def _pids_from(script_output: Path) -> list[int]:
    if not script_output.exists():
        return []
    return [int(tok) for tok in script_output.read_text(encoding="utf-8").split() if tok.isdigit()]


@pytest.mark.timeout(300)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_run_cell_timeout_kills_the_whole_process_group_not_just_the_child(tmp_path, monkeypatch):
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "cell.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"bash -c 'echo $$ > {pid_file}; exec sleep {_CHILD_LIFETIME_S}' &\n"
        f"sleep {_CHILD_LIFETIME_S}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "benchbox_run_argv", lambda *a, **k: ["bash", str(script)])

    returned, outcome = _run_cell_bounded(tmp_path)

    grandchild = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else None
    try:
        assert returned, (
            f"run_cell had not returned {_RETURN_CEILING_S:.0f}s into a {_CELL_TIMEOUT_S}s timeout. The "
            "grandchild survived and still holds the inherited stdout pipe, so the wrapper is blocked "
            "draining it -- the process group was never reaped."
        )
        if "error" in outcome:
            raise AssertionError(f"run_cell raised: {outcome['error']!r}")

        result = outcome["result"]
        assert result.status == "timed-out", f"fixture assumption: the cell must time out, got {result.status!r}"
        assert grandchild is not None, "fixture assumption: the grandchild never recorded its pid"
        assert _await_death(grandchild), (
            f"grandchild pid {grandchild} survived a timed-out run_cell -- run_cell is not reaching the "
            "real process-group teardown in tests/uat/timeouts.py (setsid + killpg ladder). Signalling "
            "only the direct child leaves this process orphaned to init."
        )
    finally:
        if grandchild is not None and _alive(grandchild):
            with suppress(OSError):
                os.kill(grandchild, signal.SIGKILL)


@pytest.mark.timeout(300)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_run_cell_runs_the_child_in_its_own_session(tmp_path, monkeypatch):
    pgid_file = tmp_path / "pgid.txt"
    script = tmp_path / "cell.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s %s\\n' \"$$\" \"$(ps -o pgid= -p $$ | tr -d ' ')\" > {pgid_file}\n"
        f"sleep {_CHILD_LIFETIME_S}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "benchbox_run_argv", lambda *a, **k: ["bash", str(script)])

    _returned, _outcome = _run_cell_bounded(tmp_path)

    try:
        assert pgid_file.exists(), "fixture assumption: the child never recorded its pgid"
        child_pid, child_pgid = (int(v) for v in pgid_file.read_text(encoding="utf-8").split())
        assert child_pid == child_pgid, (
            f"run_cell's child (pid {child_pid}) is in process group {child_pgid}, not its own -- "
            "preexec_fn=os.setsid is not reaching the subprocess, so the timeout ladder's killpg would "
            "signal the sweep's own process group instead of the cell's."
        )
        assert child_pgid != os.getpgid(0), "child shares the test runner's process group"
    finally:
        for pid in _pids_from(pgid_file):
            if _alive(pid):
                with suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
