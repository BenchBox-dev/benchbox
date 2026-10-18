from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.uat import timeouts

pytestmark = pytest.mark.medium


def test_run_with_timeout_zero_disables():
    result = timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=0)
    assert result.exit_code == 0
    assert result.timed_out is False


def test_run_with_timeout_success_under_cap():
    result = timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=5)
    assert result.exit_code == 0
    assert result.timed_out is False


def test_run_with_timeout_nonzero_exit():
    result = timeouts.run_with_timeout(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        timeout_s=5,
    )
    assert result.exit_code == 7
    assert result.timed_out is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only kill ladder")
def test_run_with_timeout_kills_runaway_child():
    result = timeouts.run_with_timeout(
        [sys.executable, "-c", "import time; time.sleep(3)"],
        timeout_s=1,
    )
    assert result.timed_out is True
    assert result.exit_code == timeouts.EXIT_TIMEOUT
    assert 0.5 <= result.elapsed_s <= 2.5


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only process-group assertion")
def test_run_with_timeout_kills_grandchild_process(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    script = f"""
import pathlib
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
"""

    result = timeouts.run_with_timeout([sys.executable, "-c", script], timeout_s=1)

    assert result.timed_out is True
    assert result.exit_code == timeouts.EXIT_TIMEOUT
    grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
    _assert_process_terminated(grandchild_pid, role="grandchild")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX timeout semantics")
def test_run_with_timeout_drains_noisy_pipe_on_timeout():
    script = """
import sys
import time

for _ in range(20000):
    print("x" * 100)
sys.stdout.flush()
time.sleep(30)
"""

    result = timeouts.run_with_timeout(
        [sys.executable, "-c", script],
        timeout_s=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.timed_out is True
    assert result.exit_code == timeouts.EXIT_TIMEOUT
    assert result.stdout


def test_kill_process_group_falls_back_to_proc_kill_without_os_killpg(monkeypatch):
    monkeypatch.delattr(timeouts.os, "killpg", raising=False)
    mock_proc = Mock(pid=12345)

    timeouts._kill_process_group(mock_proc)

    mock_proc.kill.assert_called_once()


def test_kill_process_group_uses_killpg_when_available(monkeypatch):
    sigterm = object()
    sigkill = object()
    calls: list[tuple[int, object]] = []

    def fake_killpg(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(timeouts.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGKILL", sigkill, raising=False)
    mock_proc = Mock(pid=999)

    timeouts._kill_process_group(mock_proc)

    assert calls == [(999, sigterm), (999, sigkill)]
    mock_proc.kill.assert_not_called()


def test_run_with_timeout_kills_group_on_base_exception_before_reraising(monkeypatch):

    class _Cancelled(KeyboardInterrupt):
        pass

    class FakeProc:
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            raise _Cancelled("simulated sweep cancellation")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    sigterm = object()
    sigkill = object()
    killpg_calls: list[tuple[int, object]] = []

    monkeypatch.setattr(timeouts.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(timeouts.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)), raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGKILL", sigkill, raising=False)

    with pytest.raises(_Cancelled):
        timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=5)

    assert killpg_calls[0] == (4242, sigterm)
    assert killpg_calls[-1] == (4242, sigkill)


def test_run_with_timeout_kills_group_on_a_non_keyboardinterrupt_base_exception(monkeypatch):

    class _Unwound(BaseException):
        pass

    class FakeProc:
        pid = 5150
        returncode = None

        def communicate(self, timeout=None):
            raise _Unwound("simulated non-KeyboardInterrupt unwind")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    sigterm = object()
    sigkill = object()
    killpg_calls: list[tuple[int, object]] = []

    monkeypatch.setattr(timeouts.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(timeouts.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)), raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGKILL", sigkill, raising=False)

    with pytest.raises(_Unwound):
        timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=5)

    assert killpg_calls, (
        "a BaseException that is not a KeyboardInterrupt escaped run_with_timeout without reaping the "
        "process group -- the guard has been narrowed from `except BaseException` and the child tree is "
        "orphaned on this path."
    )
    assert killpg_calls[0] == (5150, sigterm)
    assert killpg_calls[-1] == (5150, sigkill)


def test_timeout_path_drains_using_the_reap_timeout_constant(monkeypatch):
    monkeypatch.setattr(timeouts, "_REAP_TIMEOUT_S", 7.5)
    drain_timeouts: list[float | None] = []

    class FakeProc:
        pid = 6060
        returncode = -9

        def __init__(self) -> None:
            self._calls = 0

        def communicate(self, timeout=None):
            self._calls += 1
            if self._calls == 1:
                raise subprocess.TimeoutExpired(cmd="probe", timeout=timeout)
            drain_timeouts.append(timeout)
            return (b"", b"")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(timeouts.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(timeouts.os, "killpg", lambda pid, sig: None, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGTERM", object(), raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGKILL", object(), raising=False)

    result = timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=1)

    assert result.timed_out
    assert drain_timeouts == [7.5], (
        f"the post-SIGKILL drain used {drain_timeouts!r} instead of the patched _REAP_TIMEOUT_S (7.5) -- "
        "it is back to a hardcoded literal, so tuning the constant no longer moves this site."
    )


def test_cancellation_cleanup_retries_wait_when_interrupted(monkeypatch):

    class _Interrupted(BaseException):
        pass

    class FakeProc:
        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise _Interrupted("simulated signal during wait")
            return 0

    proc = FakeProc()
    monkeypatch.setattr(timeouts, "_kill_process_group", lambda _proc: None)

    timeouts._kill_and_reap_process_group(proc)

    assert proc.wait_calls == 2


def test_run_with_timeout_zero_timeout_kills_group_on_base_exception(monkeypatch):

    class _Cancelled(KeyboardInterrupt):
        pass

    class FakeProc:
        pid = 4343
        returncode = None

        def communicate(self, timeout=None):
            raise _Cancelled("simulated sweep cancellation")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    sigterm = object()
    sigkill = object()
    killpg_calls: list[tuple[int, object]] = []

    monkeypatch.setattr(timeouts.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(timeouts.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)), raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(timeouts.signal, "SIGKILL", sigkill, raising=False)

    with pytest.raises(_Cancelled):
        timeouts.run_with_timeout([sys.executable, "-c", "pass"], timeout_s=0)

    assert killpg_calls[0] == (4343, sigterm)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only kill ladder / SIGALRM")
def test_cancellation_kills_trapping_child_and_grandchild(tmp_path):
    probe = _run_cancelled_probe(tmp_path, timeout_s=_PRODUCTION_LIKE_TIMEOUT_S)

    assert probe.sigterm_path.exists(), "SIGTERM rung never reached the child"
    _assert_process_terminated(probe.grandchild_pid, role="grandchild")
    assert _await_process_state(probe.child_pid, {_PROC_GONE}) == _PROC_GONE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only kill ladder / SIGALRM")
def test_zero_timeout_cancellation_kills_trapping_child_and_grandchild(tmp_path):
    probe = _run_cancelled_probe(tmp_path, timeout_s=0)

    assert probe.sigterm_path.exists(), "SIGTERM rung never reached the child"
    _assert_process_terminated(probe.grandchild_pid, role="grandchild")
    assert _await_process_state(probe.child_pid, {_PROC_GONE}) == _PROC_GONE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only kill ladder / SIGALRM")
def test_kill_ladder_completes_sigkill_when_interrupted_during_grace(tmp_path):
    probe = _run_cancelled_probe(tmp_path, timeout_s=_PRODUCTION_LIKE_TIMEOUT_S, deliveries=2)

    _assert_process_terminated(probe.grandchild_pid, role="grandchild")
    assert _await_process_state(probe.child_pid, {_PROC_GONE}) == _PROC_GONE


_PROC_GONE = "gone"
_PROC_ZOMBIE = "zombie"
_PROC_LIVE = "live"
_PROC_TERMINATED = {_PROC_GONE, _PROC_ZOMBIE}

_PRODUCTION_LIKE_TIMEOUT_S = 30

_PROBE_TICK_S = 0.02
_PROBE_MAX_TICKS = 500
_STATE_TIMEOUT_S = 5.0

_MIN_GRACE_TO_TICK_RATIO = 5.0


def test_cancel_probe_tick_leaves_margin_inside_the_kill_ladder_grace_window():
    ratio = timeouts._KILL_GRACE_S / _PROBE_TICK_S
    assert ratio >= _MIN_GRACE_TO_TICK_RATIO, (
        f"the cancellation probe ticks every {_PROBE_TICK_S * 1000:.0f} ms against a "
        f"{timeouts._KILL_GRACE_S * 1000:.0f} ms kill-ladder grace window (ratio {ratio:.1f}, minimum "
        f"{_MIN_GRACE_TO_TICK_RATIO:.1f}). Gate 2's follow-up signal can no longer be relied on to land "
        "inside the grace window, so the interrupted-ladder tests would keep passing without exercising "
        "the path they exist to cover. Shorten _PROBE_TICK_S or restore the grace window."
    )


_GRANDCHILD_SRC = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"

_CANCEL_PROBE_SRC = """
import os
import pathlib
import signal
import subprocess
import sys
import time

child_pid_path, grandchild_pid_path, sigterm_path, ready_path, grandchild_src = sys.argv[1:6]


def _trap_sigterm(signum, frame):
    # Acknowledge and survive, the way a database server runs its own
    # shutdown: the ladder must escalate to SIGKILL to end this process.
    pathlib.Path(sigterm_path).write_text("1", encoding="utf-8")


signal.signal(signal.SIGTERM, _trap_sigterm)

grandchild = subprocess.Popen([sys.executable, "-c", grandchild_src])
pathlib.Path(grandchild_pid_path).write_text(str(grandchild.pid), encoding="utf-8")
pathlib.Path(child_pid_path).write_text(str(os.getpid()), encoding="utf-8")
pathlib.Path(ready_path).write_text("1", encoding="utf-8")

while True:
    time.sleep(0.05)
"""


class _Cancelled(KeyboardInterrupt):
    pass


@dataclass(frozen=True)
class _ProbeOutcome:
    child_pid: int
    grandchild_pid: int
    sigterm_path: Path
    cancelled: pytest.ExceptionInfo[_Cancelled]


def _run_cancelled_probe(tmp_path: Path, *, timeout_s: int, deliveries: int = 1) -> _ProbeOutcome:
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    sigterm_path = tmp_path / "child.sigterm"
    ready_path = tmp_path / "child.ready"
    argv = [
        sys.executable,
        "-c",
        _CANCEL_PROBE_SRC,
        str(child_pid_path),
        str(grandchild_pid_path),
        str(sigterm_path),
        str(ready_path),
        _GRANDCHILD_SRC,
    ]
    gates = [ready_path, sigterm_path][:deliveries]
    counters = {"ticks": 0, "delivered": 0}

    def _on_alarm(signum: int, frame: object) -> None:
        counters["ticks"] += 1
        if counters["ticks"] >= _PROBE_MAX_TICKS:
            signal.setitimer(signal.ITIMER_REAL, 0)
            raise AssertionError(f"probe never reached gate {gates[counters['delivered']].name}")
        if not gates[counters["delivered"]].exists():
            return
        counters["delivered"] += 1
        if counters["delivered"] >= deliveries:
            signal.setitimer(signal.ITIMER_REAL, 0)
        raise _Cancelled("simulated sweep cancellation")

    assert len(gates) == deliveries, "no gate defined for that many deliveries"
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, _PROBE_TICK_S, _PROBE_TICK_S)
    try:
        with pytest.raises(_Cancelled) as cancelled:
            timeouts.run_with_timeout(argv, timeout_s=timeout_s)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert counters["delivered"] == deliveries
    return _ProbeOutcome(
        child_pid=int(child_pid_path.read_text(encoding="utf-8")),
        grandchild_pid=int(grandchild_pid_path.read_text(encoding="utf-8")),
        sigterm_path=sigterm_path,
        cancelled=cancelled,
    )


def _process_state(pid: int) -> str:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return _PROC_GONE
    except PermissionError:
        return _PROC_LIVE
    linux_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = linux_stat.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _bsd_process_state(pid)
    except OSError:
        return _PROC_LIVE
    fields = raw.rpartition(")")[2].split()
    return _PROC_ZOMBIE if fields and fields[0] == "Z" else _PROC_LIVE


def _bsd_process_state(pid: int) -> str:
    try:
        completed = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return _PROC_LIVE
    state = completed.stdout.strip()
    if not state:
        return _PROC_GONE
    return _PROC_ZOMBIE if state.startswith("Z") else _PROC_LIVE


def _await_process_state(pid: int, accepted: set[str], *, timeout_s: float = _STATE_TIMEOUT_S) -> str:
    deadline = time.monotonic() + timeout_s
    state = _process_state(pid)
    while state not in accepted and time.monotonic() < deadline:
        time.sleep(0.02)
        state = _process_state(pid)
    return state


def _assert_process_terminated(pid: int, *, role: str) -> None:
    state = _await_process_state(pid, _PROC_TERMINATED)
    assert state != _PROC_LIVE, f"{role} remained live after teardown (state={state})"
