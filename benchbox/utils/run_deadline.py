from __future__ import annotations

import contextvars
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.resource_limits import kill_started_group

HARD_STOP_GRACE_SECONDS = 30.0
RUN_TIMEOUT_CONFIG_KEY = "execution.timeout_seconds"
RUN_TIMEOUT_OPTION_KEY = "timeout_seconds"
TIMED_OUT_VALIDATION_STATUS = "TIMED_OUT"
STARTUP_PHASE = "startup"
OFFICIAL_COMPLIANCE_CLASS = "official"


def validate_timeout_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"timeout_seconds must be a whole number of seconds; got {value!r}")
    if value < 0:
        raise ValueError(f"timeout_seconds must be 0 (no limit) or a positive number of seconds; got {value}")
    return value or None


def resolve_timeout_seconds(explicit: Any, configured: Any = None) -> int | None:
    return validate_timeout_seconds(configured if explicit is None else explicit)


class RunTimeoutError(Exception):
    def __init__(self, timeout_seconds: int, phase: str, query_id: str | None = None, detail: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.phase = phase
        self.query_id = query_id
        self.detail = detail
        self.partial_result: Any = None
        where = phase if query_id is None else f"{phase}, at query {query_id}"
        message = f"The run reached its {timeout_seconds}-second limit (timeout_seconds) during {where}"
        super().__init__(f"{message}: {detail}" if detail else message)


class RunDeadline:
    def __init__(self, timeout_seconds: int, *, started: bool = True):
        validated = validate_timeout_seconds(timeout_seconds)
        if validated is None:
            raise ValueError("timeout_seconds is required")
        self.timeout_seconds = validated
        self.phase = STARTUP_PHASE
        self.query_id: str | None = None
        self.timeout: RunTimeoutError | None = None
        self.partial_query_results: list[dict[str, Any]] | None = None
        self.write_partial_result: Callable[[], None] | None = None
        self.run_timed_out = False
        self._started: float | None = mono_time() if started else None
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen[Any]] = set()
        self._stopped_children: set[int] = set()

    def start(self) -> None:
        if self._started is None:
            self._started = mono_time()

    def remaining(self) -> float:
        if self._started is None:
            return float(self.timeout_seconds)
        return max(0.0, self.timeout_seconds - elapsed_seconds(self._started))

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def enter_phase(self, phase: str) -> None:
        self.phase = phase
        self.query_id = None

    def enter_query(self, query_id: Any) -> None:
        self.query_id = str(query_id)

    def record_timeout(self, detail: str | None = None) -> RunTimeoutError:
        if self.timeout is None:
            self.timeout = RunTimeoutError(self.timeout_seconds, self.phase, self.query_id, detail)
        return self.timeout

    def check(self) -> None:
        if self.expired:
            raise self.record_timeout()

    def stop_requested(self) -> bool:
        if not self.expired:
            return False
        self.record_timeout()
        return True

    @contextmanager
    def tracking(self, process: subprocess.Popen[Any]) -> Iterator[None]:
        with self._lock:
            self._children.add(process)
        try:
            yield
        finally:
            with self._lock:
                self._children.discard(process)

    def stop_children(self) -> None:
        with self._lock:
            children = list(self._children)
            self._stopped_children.update(child.pid for child in children)
        for child in children:
            if child.poll() is None:
                kill_started_group(child)

    def stopped(self, process: subprocess.Popen[Any]) -> bool:
        with self._lock:
            return process.pid in self._stopped_children

    def run(
        self,
        cmd: Sequence[str],
        *,
        tool: str,
        check: bool = False,
        capture_output: bool = False,
        **popen_kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        if self.expired:
            raise self.record_timeout(f"{tool} was not started")
        if capture_output:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.PIPE
        if sys.platform != "win32":
            popen_kwargs.setdefault("start_new_session", True)
        with subprocess.Popen(list(cmd), **popen_kwargs) as process, self.tracking(process):
            try:
                stdout, stderr = process.communicate(timeout=self.remaining())
            except subprocess.TimeoutExpired:
                kill_started_group(process)
                process.communicate()
                raise self.record_timeout(f"BenchBox stopped {tool}") from None
            except BaseException:
                kill_started_group(process)
                raise
            returncode = process.poll()
            if returncode and self.stopped(process):
                raise self.record_timeout(f"BenchBox stopped {tool}")
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, list(cmd), output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(list(cmd), returncode, stdout, stderr)


_ACTIVE: contextvars.ContextVar[RunDeadline | None] = contextvars.ContextVar("benchbox_run_deadline", default=None)


def current_run_deadline() -> RunDeadline | None:
    return _ACTIVE.get()


@contextmanager
def active_run_deadline(deadline: RunDeadline | None) -> Iterator[RunDeadline | None]:
    token = _ACTIVE.set(deadline)
    try:
        yield deadline
    finally:
        _ACTIVE.reset(token)


@contextmanager
def own_run_deadline(timeout_seconds: Any) -> Iterator[RunDeadline | None]:
    seconds = validate_timeout_seconds(timeout_seconds)
    if seconds is None or current_run_deadline() is not None:
        yield None
        return
    deadline = RunDeadline(seconds)
    with active_run_deadline(deadline):
        yield deadline


def enter_run_phase(phase: str) -> None:
    deadline = current_run_deadline()
    if deadline is not None:
        deadline.enter_phase(phase)
        deadline.check()


def stop_requested_before_query(query_id: Any) -> bool:
    deadline = current_run_deadline()
    if deadline is None:
        return False
    deadline.enter_query(query_id)
    return deadline.stop_requested()


def share_partial_query_results(results: list[dict[str, Any]]) -> None:
    deadline = current_run_deadline()
    if deadline is not None:
        deadline.partial_query_results = results


def run_child(
    cmd: Sequence[str], *, tool: str, deadline: RunDeadline | None, **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    if deadline is None:
        return subprocess.run(list(cmd), **kwargs)
    return deadline.run(cmd, tool=tool, **kwargs)


def timeout_details(deadline: RunDeadline) -> dict[str, Any]:
    timeout = deadline.record_timeout()
    details: dict[str, Any] = {"timeout_seconds": timeout.timeout_seconds, "phase": timeout.phase}
    if timeout.query_id is not None:
        details["query_id"] = timeout.query_id
    if timeout.detail:
        details["detail"] = timeout.detail
    return details


def mark_timed_out(result: Any, deadline: RunDeadline) -> Any:
    result.validation_status = TIMED_OUT_VALIDATION_STATUS
    existing = result.validation_details if isinstance(result.validation_details, Mapping) else {}
    result.validation_details = {**existing, "timeout": timeout_details(deadline)}
    if getattr(result, "compliance_class", None) == OFFICIAL_COMPLIANCE_CLASS:
        result.compliance_class = None
    return result


def arm_hard_stop(
    deadline: RunDeadline,
    on_hard_stop: Callable[[RunDeadline], None],
    *,
    grace_seconds: float = HARD_STOP_GRACE_SECONDS,
) -> Callable[[], None]:
    finished = threading.Event()

    def watch() -> None:
        if finished.wait(deadline.remaining()):
            return
        deadline.record_timeout()
        deadline.stop_children()
        if finished.wait(grace_seconds):
            return
        deadline.stop_children()
        on_hard_stop(deadline)

    threading.Thread(target=watch, name="benchbox-run-deadline", daemon=True).start()
    return finished.set
