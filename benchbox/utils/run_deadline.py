from __future__ import annotations

import contextvars
import functools
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.process_group import kill_process_group
from benchbox.utils.resource_limits import kill_started_group

HARD_STOP_GRACE_SECONDS = 30.0
HARD_STOP_EXPORT_MARGIN_SECONDS = 5.0
RUN_TIMEOUT_CONFIG_KEY = "execution.timeout_seconds"
RUN_TIMEOUT_OPTION_KEY = "timeout_seconds"
TIMED_OUT_VALIDATION_STATUS = "TIMED_OUT"
STARTUP_PHASE = "startup"
OFFICIAL_COMPLIANCE_CLASS = "official"
VERDICTS_KEPT_ON_TIMEOUT = frozenset({"FAILED", "ERROR", "INTERRUPTED"})
TIMED_OUT_QUERY_STATUS = "TIMEOUT"
FAILED_QUERY_STATUS = "FAILED"
REMOTE_CLEANUP_WARNING = (
    "A run time limit is set. If the run times out, BenchBox will try to cancel the work it started on {platform}. "
    "This cleanup has not been checked against the live service, so work may keep running and billing."
)
NO_REMOTE_CLEANUP_WARNING = (
    "A run time limit is set. If the run times out, work BenchBox started on {platform} may keep running and "
    "billing: BenchBox cannot cancel it on this platform."
)


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
        self._remote_cleanups: dict[int, tuple[Any, str, Callable[[], None]]] = {}
        self._cleanup_worker: threading.Thread | None = None
        self._cleanup_began: float | None = None
        self._cleanup_unfinished_reported = False
        self.cleanup_errors: list[str] = []

    def register_remote_cleanup(self, owner: Any, cleanup: Callable[[], None], label: str | None = None) -> None:
        with self._lock:
            self._remote_cleanups.setdefault(id(owner), (owner, label or type(owner).__name__, cleanup))

    def start_remote_cleanup(self) -> None:
        with self._lock:
            if self._cleanup_worker is not None:
                return
            cleanups = list(self._remote_cleanups.values())
            began = mono_time()

            def cancel_all() -> None:
                _CLEANUP_WINDOW.ends_at = began + graceful_cleanup_window_seconds()
                for _owner, label, cleanup in cleanups:
                    try:
                        cleanup()
                    except Exception as exc:
                        with self._lock:
                            self.cleanup_errors.append(f"{label}: {type(exc).__name__}: {exc}")

            worker = threading.Thread(target=cancel_all, name="benchbox-remote-cleanup", daemon=True)
            self._cleanup_worker = worker
            self._cleanup_began = began
        worker.start()

    def cancel_remote_work(self, wait_seconds: float | None = None) -> list[str]:
        self.start_remote_cleanup()
        worker = self._cleanup_worker
        assert worker is not None and self._cleanup_began is not None
        budget = graceful_cleanup_window_seconds()
        if wait_seconds is None:
            wait_seconds = max(0.0, budget - elapsed_seconds(self._cleanup_began))
        else:
            budget = wait_seconds
        worker.join(wait_seconds)
        with self._lock:
            if worker.is_alive() and not self._cleanup_unfinished_reported:
                self._cleanup_unfinished_reported = True
                self.cleanup_errors.append(f"Remote cleanup did not finish within {budget:g} seconds")
            return list(self.cleanup_errors)

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
            self.start_remote_cleanup()
        return self.timeout

    def check(self) -> None:
        if self.expired:
            raise self.record_timeout()

    def stop_requested(self) -> bool:
        if not self.expired:
            return False
        self.record_timeout()
        return True

    def failed_query_status(self) -> str:
        return TIMED_OUT_QUERY_STATUS if self.stop_requested() else FAILED_QUERY_STATUS

    def limit_to_remaining(self, seconds: float | None) -> float | None:
        remaining = self.remaining()
        return remaining if seconds is None else min(seconds, remaining)

    def record_overrun(self) -> None:
        if self.timeout is None and self.expired:
            self.timeout = RunTimeoutError(self.timeout_seconds, self.phase)

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
                kill_process_group(process)
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

    @contextmanager
    def stream(self, cmd: Sequence[str], *, tool: str, **popen_kwargs: Any) -> Iterator[subprocess.Popen[Any]]:
        if self.expired:
            raise self.record_timeout(f"{tool} was not started")
        if sys.platform != "win32":
            popen_kwargs.setdefault("start_new_session", True)
        ended = threading.Event()
        with subprocess.Popen(list(cmd), **popen_kwargs) as process, self.tracking(process):

            def end() -> None:
                if process.poll() is None:
                    ended.set()
                    kill_process_group(process)

            timer = threading.Timer(self.remaining(), end)
            timer.daemon = True
            timer.start()
            try:
                yield process
            except BaseException as exc:
                timer.cancel()
                if process.poll() is None:
                    kill_process_group(process)
                if ended.is_set() or self.stopped(process):
                    raise self.record_timeout(f"BenchBox stopped {tool}") from exc
                raise
            finally:
                timer.cancel()
            if ended.is_set():
                raise self.record_timeout(f"BenchBox stopped {tool}")


_ACTIVE: contextvars.ContextVar[RunDeadline | None] = contextvars.ContextVar("benchbox_run_deadline", default=None)

_CLEANUP_WINDOW = threading.local()


def graceful_cleanup_window_seconds() -> float:
    return max(0.0, HARD_STOP_GRACE_SECONDS - HARD_STOP_EXPORT_MARGIN_SECONDS)


def remote_cleanup_seconds_left() -> float | None:
    ends_at = getattr(_CLEANUP_WINDOW, "ends_at", None)
    return None if ends_at is None else max(0.0, ends_at - mono_time())


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


def run_stop_requested() -> bool:
    deadline = current_run_deadline()
    return deadline is not None and deadline.stop_requested()


def failed_query_status() -> str:
    deadline = current_run_deadline()
    return FAILED_QUERY_STATUS if deadline is None else deadline.failed_query_status()


def mark_cut_short_query(result: dict[str, Any]) -> None:
    if result.get("status") in {FAILED_QUERY_STATUS, "ERROR"} and failed_query_status() == TIMED_OUT_QUERY_STATUS:
        result["status"] = TIMED_OUT_QUERY_STATUS


def limit_to_remaining(seconds: float | None) -> float | None:
    deadline = current_run_deadline()
    return seconds if deadline is None else deadline.limit_to_remaining(seconds)


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


@contextmanager
def stream_child(
    cmd: Sequence[str], *, tool: str, deadline: RunDeadline | None, **kwargs: Any
) -> Iterator[subprocess.Popen[Any]]:
    if deadline is None:
        with subprocess.Popen(list(cmd), **kwargs) as process:
            yield process
        return
    with deadline.stream(cmd, tool=tool, **kwargs) as process:
        yield process


def bind_run_deadline(function: Callable[..., Any]) -> Callable[..., Any]:
    deadline = current_run_deadline()

    @functools.wraps(function)
    def bound(*args: Any, **kwargs: Any) -> Any:
        with active_run_deadline(deadline):
            return function(*args, **kwargs)

    return bound


def register_remote_cleanup(owner: Any, cleanup: Callable[[], None], label: str | None = None) -> None:
    deadline = current_run_deadline()
    if deadline is not None:
        deadline.register_remote_cleanup(owner, cleanup, label)


def remote_cleanup_warning(platform: str, cleanup_implemented: bool) -> str:
    template = REMOTE_CLEANUP_WARNING if cleanup_implemented else NO_REMOTE_CLEANUP_WARNING
    return template.format(platform=platform)


def cloud_cleanup_warning(platform: str | None) -> str | None:
    from benchbox.core.platform_manifest import get_all_platform_aliases, get_platform_manifest_entry

    if not platform:
        return None
    key = str(platform).lower()
    entry = get_platform_manifest_entry(get_all_platform_aliases().get(key, key))
    return None if entry is None else _entry_cleanup_warning(entry)


def adapter_cleanup_warning(adapter: Any) -> str | None:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    class_name = type(adapter).__name__
    for entry in PLATFORM_MANIFEST:
        if entry.adapter is not None and entry.adapter.class_name == class_name:
            return _entry_cleanup_warning(entry)
    return None


def _entry_cleanup_warning(entry: Any) -> str | None:
    metadata = entry.metadata_dict()
    if metadata.get("category") != "cloud":
        return None
    return remote_cleanup_warning(
        str(metadata.get("display_name") or entry.key), bool(entry.capabilities.get("run_timeout_cleanup", False))
    )


def timeout_details(deadline: RunDeadline) -> dict[str, Any]:
    timeout = deadline.record_timeout()
    details: dict[str, Any] = {"timeout_seconds": timeout.timeout_seconds, "phase": timeout.phase}
    if timeout.query_id is not None:
        details["query_id"] = timeout.query_id
    if timeout.detail:
        details["detail"] = timeout.detail
    if deadline.cleanup_errors:
        details["cleanup_errors"] = list(deadline.cleanup_errors)
    return details


def end_descendant_processes() -> None:
    import psutil

    for child in psutil.Process().children(recursive=True):
        try:
            child.kill()
        except psutil.Error:
            pass


def mark_timed_out(result: Any, deadline: RunDeadline) -> Any:
    if str(getattr(result, "validation_status", "")).upper() not in VERDICTS_KEPT_ON_TIMEOUT:
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
    hard_stopping = threading.Lock()

    def watch() -> None:
        if finished.wait(deadline.remaining()):
            return
        deadline.start_remote_cleanup()
        deadline.stop_children()
        if finished.wait(grace_seconds):
            return
        with hard_stopping:
            if finished.is_set():
                return
            deadline.record_overrun()
            on_hard_stop(deadline)

    def disarm() -> None:
        finished.set()
        with hard_stopping:
            pass

    threading.Thread(target=watch, name="benchbox-run-deadline", daemon=True).start()
    return disarm
