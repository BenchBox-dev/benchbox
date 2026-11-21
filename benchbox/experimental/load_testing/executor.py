from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from benchbox.experimental.load_testing.patterns import SteadyPattern, WorkloadPattern, WorkloadPhase
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.run_deadline import limit_to_remaining, run_stop_requested

logger = logging.getLogger(__name__)


@dataclass
class QueryExecution:
    query_id: str
    stream_id: int
    start_time: float
    end_time: float
    success: bool
    error: str | None = None
    rows_returned: int | None = None
    queue_wait_time: float = 0.0
    duration_seconds: float | None = None

    @property
    def latency_seconds(self) -> float:
        if self.duration_seconds is not None:
            return self.duration_seconds
        return self.end_time - self.start_time


@dataclass
class StreamResult:
    stream_id: int
    queries_executed: int
    queries_succeeded: int
    queries_failed: int
    total_time_seconds: float
    query_executions: list[QueryExecution] = field(default_factory=list)
    error: str | None = None

    @property
    def success_rate(self) -> float:
        if self.queries_executed == 0:
            return 0.0
        return (self.queries_succeeded / self.queries_executed) * 100

    @property
    def throughput(self) -> float:
        if self.total_time_seconds == 0:
            return 0.0
        return self.queries_executed / self.total_time_seconds


@dataclass
class ConcurrentLoadConfig:
    query_factory: Callable[[int], tuple[str, str]]

    role_factories: dict[str, Callable[[int], tuple[str, str]]] | None = field(default=None, kw_only=True)

    connection_factory: Callable[[], Any]

    execute_query: Callable[[Any, str], tuple[bool, int | None, str | None]]

    pattern: WorkloadPattern = field(default_factory=lambda: SteadyPattern(1, 60))

    queries_per_stream: int = 10

    query_timeout_seconds: float | None = None

    collect_resource_metrics: bool = True

    resource_sample_interval: float = 1.0

    track_queue_times: bool = True

    def __post_init__(self) -> None:
        if self.query_timeout_seconds is not None and not self.query_timeout_seconds > 0:
            raise ValueError("query_timeout_seconds must be greater than zero")


@dataclass
class ConcurrentLoadResult:
    start_time: float
    end_time: float
    total_duration_seconds: float

    streams: list[StreamResult]
    total_streams_executed: int
    total_streams_succeeded: int

    total_queries_executed: int
    total_queries_succeeded: int
    total_queries_failed: int

    overall_throughput: float

    queue_metrics: dict[str, float] = field(default_factory=dict)

    resource_metrics: dict[str, Any] = field(default_factory=dict)

    pattern_name: str = ""
    max_concurrency_reached: int = 0

    abandoned_stream_ids: list[int] = field(default_factory=list)
    outstanding_stream_ids: list[int] = field(default_factory=list)
    cleanup_state: str = "complete"

    @property
    def success_rate(self) -> float:
        if self.total_queries_executed == 0:
            return 0.0
        return (self.total_queries_succeeded / self.total_queries_executed) * 100

    def get_percentile_latency(self, percentile: float) -> float:
        latencies = []
        for stream in self.streams:
            for execution in stream.query_executions:
                latencies.append(execution.latency_seconds)

        if not latencies:
            return 0.0

        latencies.sort()
        index = int((percentile / 100) * len(latencies))
        index = min(index, len(latencies) - 1)
        return latencies[index]


class ConcurrentLoadExecutor:
    def __init__(self, config: ConcurrentLoadConfig):
        self._config = config
        self._lock = threading.Lock()
        self._stream_results: list[StreamResult] = []
        self._active_streams = 0
        self._active_roles: dict[str, int] = {}
        self._max_concurrency_reached = 0
        self._queue: deque[tuple[int, float]] = deque()
        self._resource_samples: list[dict[str, float]] = []
        self._stop_monitoring = threading.Event()
        self._stream_stops: dict[int, threading.Event] = {}
        self._abandoned: dict[int, Future] = {}

    def run(self) -> ConcurrentLoadResult:
        start_time = time.time()
        start_mono = mono_time()
        pattern = self._config.pattern
        self._validate_role_factories(pattern)

        logger.info(
            f"Starting concurrent load test: pattern={pattern.__class__.__name__}, "
            f"max_concurrency={pattern.max_concurrency}, "
            f"duration={pattern.total_duration}s"
        )

        monitor_thread = None
        if self._config.collect_resource_metrics:
            monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
            monitor_thread.start()

        try:
            self._execute_pattern(pattern)
        finally:
            self._stop_monitoring.set()
            if monitor_thread:
                monitor_thread.join(timeout=2.0)

        end_time = time.time()
        total_duration = elapsed_seconds(start_mono)

        total_queries = sum(s.queries_executed for s in self._stream_results)
        total_succeeded = sum(s.queries_succeeded for s in self._stream_results)
        total_failed = sum(s.queries_failed for s in self._stream_results)
        streams_succeeded = sum(1 for s in self._stream_results if s.error is None)

        queue_metrics = self._calculate_queue_metrics()
        outstanding_stream_ids = sorted(stream_id for stream_id, future in self._abandoned.items() if not future.done())

        resource_metrics = self._calculate_resource_metrics()

        result = ConcurrentLoadResult(
            start_time=start_time,
            end_time=end_time,
            total_duration_seconds=total_duration,
            streams=self._stream_results,
            total_streams_executed=len(self._stream_results),
            total_streams_succeeded=streams_succeeded,
            total_queries_executed=total_queries,
            total_queries_succeeded=total_succeeded,
            total_queries_failed=total_failed,
            overall_throughput=total_queries / total_duration if total_duration > 0 else 0,
            queue_metrics=queue_metrics,
            resource_metrics=resource_metrics,
            pattern_name=pattern.__class__.__name__,
            max_concurrency_reached=self._max_concurrency_reached,
            abandoned_stream_ids=sorted(self._abandoned),
            outstanding_stream_ids=outstanding_stream_ids,
            cleanup_state="outstanding" if outstanding_stream_ids else "complete",
        )

        logger.info(
            f"Load test complete: {total_queries} queries in {total_duration:.2f}s "
            f"({result.overall_throughput:.2f} qps), "
            f"success_rate={result.success_rate:.1f}%"
        )

        return result

    def _validate_role_factories(self, pattern: WorkloadPattern) -> None:
        declared_roles: set[str] = set()
        for phase in pattern.iter_phases():
            if phase.roles:
                for role, target in phase.roles.items():
                    if target < 0:
                        raise ValueError(f"phase '{phase.phase_name}': negative target for role '{role}'")
                declared_roles.update(phase.roles)
        if declared_roles:
            missing = declared_roles - set(self._config.role_factories or {})
            if missing:
                raise ValueError(
                    f"pattern declares roles {sorted(declared_roles)} but role_factories is missing {sorted(missing)}"
                )

    def _role_targets(self, phase: WorkloadPhase) -> dict[str, int]:
        if phase.roles:
            return {role: max(0, target - self._active_roles.get(role, 0)) for role, target in phase.roles.items()}
        return {"": max(0, phase.concurrency - self._active_streams)}

    def _execute_pattern(self, pattern: WorkloadPattern) -> None:
        stream_counter = 0

        executor = ThreadPoolExecutor(max_workers=pattern.max_concurrency)
        try:
            futures: dict[Future, tuple[int, str]] = {}
            phase_streams: dict[str, int] = {}
            phases = list(pattern.iter_phases())

            for index, phase in enumerate(phases):
                phase_start = mono_time()
                phase_streams[phase.phase_name] = 0

                logger.debug(
                    f"Starting phase '{phase.phase_name}': "
                    f"concurrency={phase.concurrency}, duration={phase.duration_seconds}s"
                )

                while elapsed_seconds(phase_start) < phase.duration_seconds:
                    with self._lock:
                        current_active = self._active_streams
                        if current_active > self._max_concurrency_reached:
                            self._max_concurrency_reached = current_active
                        targets = self._role_targets(phase)

                    for role, streams_to_launch in targets.items():
                        for _ in range(max(0, streams_to_launch)):
                            stream_id = stream_counter
                            stream_counter += 1
                            phase_streams[phase.phase_name] += 1

                            enqueue_time = mono_time() if self._config.track_queue_times else 0.0

                            with self._lock:
                                self._active_streams += 1
                                self._active_roles[role] = self._active_roles.get(role, 0) + 1
                                if self._config.track_queue_times:
                                    self._queue.append((stream_id, enqueue_time))

                            self._stream_stops[stream_id] = threading.Event()
                            future = executor.submit(self._execute_stream, stream_id, enqueue_time, role)
                            futures[future] = (stream_id, role)

                    completed = [f for f in futures if f.done()]
                    for future in completed:
                        self._record_stream_future(future, *futures[future])
                        del futures[future]

                    time.sleep(0.1)

                upcoming = phases[index + 1].roles if index + 1 < len(phases) else None
                self._await_role_drain(futures, next_phase_roles=upcoming)

            self._drain_streams(futures, list(futures))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _record_stream_future(
        self,
        future: Future,
        stream_id: int,
        role: str,
        *,
        abandoned: bool = False,
        waited: float | None = None,
    ) -> None:
        try:
            if abandoned:
                self._stream_stops[stream_id].set()
                self._abandoned[stream_id] = future
                raise TimeoutError(f"Stream {stream_id} did not finish within {waited:g}s; worker abandoned")
            self._stream_results.append(future.result())
        except Exception as e:
            self._stream_results.append(
                StreamResult(
                    stream_id=stream_id,
                    queries_executed=0,
                    queries_succeeded=0,
                    queries_failed=0,
                    total_time_seconds=0,
                    error=str(e),
                )
            )
        finally:
            with self._lock:
                self._active_streams -= 1
                self._decrement_role_locked(role)

    def _stream_wait_seconds(self) -> float | None:
        configured = None
        if self._config.query_timeout_seconds is not None:
            configured = self._config.query_timeout_seconds * max(1, self._config.queries_per_stream)
        return limit_to_remaining(configured)

    def _drain_streams(self, futures: dict[Future, tuple[int, str]], draining: list[Future]) -> None:
        waited = self._stream_wait_seconds()
        _, pending = wait(draining, timeout=waited)
        if pending:
            run_stop_requested()
        for future in draining:
            stream_id, role = futures.pop(future)
            self._record_stream_future(future, stream_id, role, abandoned=future in pending, waited=waited)

    def _decrement_role_locked(self, role: str) -> None:
        remaining = self._active_roles.get(role, 0) - 1
        if remaining > 0:
            self._active_roles[role] = remaining
        else:
            self._active_roles.pop(role, None)

    def _decrement_role(self, role: str) -> None:
        with self._lock:
            self._decrement_role_locked(role)

    def _await_role_drain(
        self,
        futures: dict[Future, tuple[int, str]],
        next_phase_roles: dict[str, int] | None,
    ) -> None:
        if not next_phase_roles:
            return
        draining = [f for f, (_, role) in futures.items() if role not in next_phase_roles]
        self._drain_streams(futures, draining)

    def _execute_stream(self, stream_id: int, enqueue_time: float, role: str = "") -> StreamResult:
        stream_start = mono_time()
        queue_wait = stream_start - enqueue_time if enqueue_time > 0 else 0
        query_factory = self._query_factory_for(role)
        stop = self._stream_stops.setdefault(stream_id, threading.Event())

        with self._lock:
            self._queue = deque((sid, ts) for sid, ts in self._queue if sid != stream_id)

        executions: list[QueryExecution] = []
        queries_succeeded = 0
        queries_failed = 0

        try:
            connection = self._config.connection_factory()

            try:
                for i in range(self._config.queries_per_stream):
                    if stop.is_set():
                        break
                    query_id, sql = query_factory(i)
                    query_start = time.time()
                    query_mono = mono_time()

                    try:
                        success, rows, error = self._config.execute_query(connection, sql)
                        query_end = time.time()
                        query_seconds = elapsed_seconds(query_mono)

                        if success:
                            queries_succeeded += 1
                        else:
                            queries_failed += 1

                        executions.append(
                            QueryExecution(
                                query_id=query_id,
                                stream_id=stream_id,
                                start_time=query_start,
                                end_time=query_end,
                                duration_seconds=query_seconds,
                                success=success,
                                error=error,
                                rows_returned=rows,
                                queue_wait_time=queue_wait if i == 0 else 0,
                            )
                        )

                    except Exception as e:
                        query_end = time.time()
                        query_seconds = elapsed_seconds(query_mono)
                        queries_failed += 1
                        executions.append(
                            QueryExecution(
                                query_id=query_id,
                                stream_id=stream_id,
                                start_time=query_start,
                                end_time=query_end,
                                duration_seconds=query_seconds,
                                success=False,
                                error=str(e),
                                queue_wait_time=queue_wait if i == 0 else 0,
                            )
                        )

            finally:
                if hasattr(connection, "close"):
                    connection.close()

        except Exception as e:
            return StreamResult(
                stream_id=stream_id,
                queries_executed=len(executions),
                queries_succeeded=queries_succeeded,
                queries_failed=queries_failed,
                total_time_seconds=elapsed_seconds(stream_start),
                query_executions=executions,
                error=f"Stream error: {e}",
            )

        return StreamResult(
            stream_id=stream_id,
            queries_executed=len(executions),
            queries_succeeded=queries_succeeded,
            queries_failed=queries_failed,
            total_time_seconds=elapsed_seconds(stream_start),
            query_executions=executions,
        )

    def _query_factory_for(self, role: str) -> Callable[[int], tuple[str, str]]:
        if role and self._config.role_factories and role in self._config.role_factories:
            return self._config.role_factories[role]
        return self._config.query_factory

    def _monitor_resources(self) -> None:
        try:
            import psutil
        except ImportError:
            logger.warning("psutil not available, skipping resource monitoring")
            return

        while not self._stop_monitoring.is_set():
            try:
                sample = {
                    "timestamp": time.time(),
                    "cpu_percent": psutil.cpu_percent(interval=None),
                    "memory_percent": psutil.virtual_memory().percent,
                    "active_streams": self._active_streams,
                }
                self._resource_samples.append(sample)
            except Exception as e:
                logger.debug(f"Resource monitoring error: {e}")

            self._stop_monitoring.wait(self._config.resource_sample_interval)

    def _calculate_queue_metrics(self) -> dict[str, float]:
        wait_times = []
        for stream in self._stream_results:
            for execution in stream.query_executions:
                if execution.queue_wait_time > 0:
                    wait_times.append(execution.queue_wait_time)

        if not wait_times:
            return {}

        wait_times.sort()
        return {
            "min_queue_wait_ms": min(wait_times) * 1000,
            "max_queue_wait_ms": max(wait_times) * 1000,
            "avg_queue_wait_ms": sum(wait_times) / len(wait_times) * 1000,
            "p50_queue_wait_ms": wait_times[len(wait_times) // 2] * 1000,
            "p95_queue_wait_ms": wait_times[int(len(wait_times) * 0.95)] * 1000
            if len(wait_times) > 1
            else wait_times[0] * 1000,
            "p99_queue_wait_ms": wait_times[int(len(wait_times) * 0.99)] * 1000
            if len(wait_times) > 1
            else wait_times[0] * 1000,
        }

    def _calculate_resource_metrics(self) -> dict[str, Any]:
        if not self._resource_samples:
            return {}

        cpu_values = [s["cpu_percent"] for s in self._resource_samples]
        memory_values = [s["memory_percent"] for s in self._resource_samples]
        stream_counts = [s["active_streams"] for s in self._resource_samples]

        return {
            "cpu_avg_percent": sum(cpu_values) / len(cpu_values),
            "cpu_max_percent": max(cpu_values),
            "memory_avg_percent": sum(memory_values) / len(memory_values),
            "memory_max_percent": max(memory_values),
            "max_active_streams": max(stream_counts),
            "sample_count": len(self._resource_samples),
        }
