from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Callable, Protocol

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.platform_timing import throughput_stream_context
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.run_deadline import current_run_deadline, limit_to_remaining, run_stop_requested

from .containment import record_outstanding_result
from .executor import DaemonStreamExecutor
from .result import (
    THROUGHPUT_FIRST_STREAM_ID,
    ThroughputResult,
    ThroughputStreamResult,
    throughput_result_succeeded,
    throughput_stream_ids,
    throughput_stream_succeeded,
)


class _RunnerConfig(Protocol):
    num_streams: int
    max_workers: int | None
    base_seed: int
    stream_timeout: int
    scale_factor: float
    verbose: bool


def _record_completed_future(
    completed_future: concurrent.futures.Future[Any],
    completed_stream_id: int,
    result: ThroughputResult,
    config: _RunnerConfig,
    logger: logging.Logger,
) -> None:
    if completed_stream_id == 0:
        try:
            auxiliary_result = completed_future.result()
            if getattr(auxiliary_result, "success", True) is False:
                result.errors.append(
                    f"Auxiliary workload failed: {getattr(auxiliary_result, 'error', None) or 'unspecified error'}"
                )
        except Exception as e:
            result.errors.append(f"Auxiliary workload execution failed: {e}")
        return

    try:
        stream_result = completed_future.result()
        result.stream_results.append(stream_result)
        result.streams_executed += 1

        if throughput_stream_succeeded(stream_result):
            result.streams_successful += 1
        else:
            stream_result.success = False
            if not stream_result.error:
                query_errors = [
                    str(query.get("error"))
                    for query in stream_result.query_results
                    if not query.get("success", True) and query.get("error")
                ]
                stream_result.error = "; ".join(query_errors) or (
                    f"{stream_result.queries_successful}/{stream_result.queries_executed} queries succeeded"
                )
            result.errors.append(f"Stream {stream_result.stream_id} failed: {stream_result.error}")

        if config.verbose:
            logger.info(
                f"Stream {stream_result.stream_id}: "
                f"{stream_result.queries_successful}/{stream_result.queries_executed} successful"
            )

    except Exception as e:
        result.streams_executed += 1
        result.errors.append(f"Stream {completed_stream_id} execution failed: {e}")
        if config.verbose:
            logger.error(f"Stream {completed_stream_id} execution failed: {e}")


def wait_for_completed(
    futures: set[concurrent.futures.Future[Any]], timeout: float | None
) -> Iterator[concurrent.futures.Future[Any]]:
    return concurrent.futures.as_completed(futures, timeout=timeout)


def stream_stop_requested(cancel_event: threading.Event | None) -> bool:
    return (cancel_event is not None and cancel_event.is_set()) or run_stop_requested()


def mark_query_in_flight(config: Any, stream_id: int, query_id: Any) -> None:
    inflight = getattr(config, "_stream_inflight_queries", None)
    if isinstance(inflight, dict):
        inflight[stream_id] = str(query_id)


def clear_query_in_flight(config: Any, stream_id: int) -> None:
    inflight = getattr(config, "_stream_inflight_queries", None)
    if isinstance(inflight, dict):
        inflight.pop(stream_id, None)


def stream_cancel_event(config: Any, stream_id: int) -> threading.Event | None:
    cancel_events = getattr(config, "_stream_cancel_events", None)
    if not isinstance(cancel_events, dict):
        return None
    candidate = cancel_events.get(stream_id)
    return candidate if isinstance(candidate, threading.Event) else None


def _warn_if_streams_queue(
    logger: logging.Logger, config: _RunnerConfig, max_workers: int, configured_timeout: float | None
) -> None:
    if max_workers < config.num_streams and configured_timeout is not None:
        logger.warning(
            f"max_workers ({max_workers}) < num_streams ({config.num_streams}) with a "
            f"stream_timeout of {configured_timeout}s set: queued streams share the same overall "
            "deadline as immediately-started ones and may be reported as timed-out before "
            "executing a single query. Set max_workers >= num_streams (or leave it unset) "
            "for the timeout to behave as a true per-stream timeout."
        )


class StreamRunner:
    @staticmethod
    def execute(
        stream_fn: Callable[[int, int, Any], ThroughputStreamResult],
        config: _RunnerConfig,
        result: ThroughputResult,
        logger: logging.Logger,
        auxiliary_fn: Callable[[], Any] | None = None,
    ) -> None:
        max_workers = config.max_workers or config.num_streams
        configured_timeout: float | None = config.stream_timeout if config.stream_timeout > 0 else None
        timeout = limit_to_remaining(configured_timeout)

        _warn_if_streams_queue(logger, config, max_workers, configured_timeout)

        cooperative_cancel = bool(getattr(config, "cancel_on_timeout", False))
        first_stream_id = getattr(config, "first_stream_id", THROUGHPUT_FIRST_STREAM_ID)
        if not isinstance(first_stream_id, int):
            first_stream_id = THROUGHPUT_FIRST_STREAM_ID
        stream_ids = throughput_stream_ids(config.num_streams, first_stream_id)
        cancel_events: dict[int, threading.Event] = {stream_id: threading.Event() for stream_id in stream_ids}
        if auxiliary_fn is not None:
            cancel_events[0] = threading.Event()
        inflight_queries: dict[int, str] = {}
        config._stream_cancel_events = cancel_events
        config._stream_inflight_queries = inflight_queries

        future_to_stream_id: dict[concurrent.futures.Future[Any], int] = {}
        pending: set[concurrent.futures.Future[Any]] = set()
        outstanding_futures: dict[int, concurrent.futures.Future[Any]] = {}

        def _settle_pending(reason: str, *, signal: bool, record_inflight: bool = False) -> None:
            for future in sorted(pending, key=future_to_stream_id.__getitem__):
                stream_id = future_to_stream_id[future]
                label = "Refresh stream" if stream_id == 0 else f"Stream {stream_id}"
                pending.discard(future)

                if signal:
                    cancel_events[stream_id].set()

                if future.cancel():
                    if stream_id != 0:
                        result.cancelled_stream_ids.append(stream_id)
                    result.outstanding_notes.append(f"{label} cancelled before dispatch; it never executed.")
                    cancelled_msg = (
                        f"{label} {reason} without starting: it was still queued behind "
                        "running work and is cancelled before dispatch so it never executes."
                    )
                    result.errors.append(cancelled_msg)
                    logger.warning(cancelled_msg)
                elif future.done():
                    _record_completed_future(future, stream_id, result, config, logger)
                else:
                    if stream_id != 0:
                        result.streams_executed += 1
                    result.outstanding_stream_ids.append(stream_id)
                    outstanding_futures[stream_id] = future
                    inflight_query = inflight_queries.get(stream_id)
                    if record_inflight and inflight_query is not None:
                        result.timed_out_queries.append({"stream_id": stream_id, "query_id": inflight_query})
                    error_msg = (
                        f"{label} {reason} and has not completed. "
                        "Python cannot forcibly cancel a running thread, so this worker "
                        "may still be executing work and holding its database connection in "
                        "the background (leaked)"
                        + (
                            "; cooperative cancellation has been signalled and the stream should "
                            "stop before its next query"
                            if signal
                            else ""
                        )
                        + "."
                    )
                    result.errors.append(error_msg)
                    result.outstanding_notes.append(
                        f"{label} worker still running when it was settled"
                        + ("; cooperative cancellation signalled" if signal else "; no cooperative cancellation")
                        + "."
                    )
                    logger.warning(error_msg)

        executor = DaemonStreamExecutor(max_workers=max_workers + (1 if auxiliary_fn else 0))
        try:
            with throughput_stream_context():
                for stream_id in stream_ids:
                    future = executor.submit(stream_fn, stream_id, config.base_seed + stream_id, config)
                    future_to_stream_id[future] = stream_id
                    pending.add(future)
                if auxiliary_fn:
                    future = executor.submit(auxiliary_fn)
                    future_to_stream_id[future] = 0
                    pending.add(future)

            try:
                for future in wait_for_completed(pending, timeout):
                    pending.discard(future)
                    _record_completed_future(future, future_to_stream_id[future], result, config, logger)
            except concurrent.futures.TimeoutError:
                run_deadline = current_run_deadline()
                if run_deadline is not None and run_deadline.stop_requested():
                    _settle_pending("was stopped at the run time limit", signal=True, record_inflight=True)
                else:
                    _settle_pending(f"timed out after {timeout}s", signal=cooperative_cancel)
        except BaseException:
            _settle_pending("was abandoned after the runner failed", signal=cooperative_cancel)
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            result._outstanding_futures = outstanding_futures
            result.cleanup_state = "outstanding" if result.outstanding_stream_ids else "complete"
            if result.outstanding_stream_ids:
                record_outstanding_result(result)

    @staticmethod
    def compute_metrics(
        result: ThroughputResult,
        config: _RunnerConfig,
        start_time: float,
        queries_per_stream: int | None = None,
    ) -> bool:
        result.end_time = datetime.now().isoformat()

        intervals = [
            (stream.start_time, stream.end_time, stream.start_wall_time, stream.end_wall_time)
            for stream in result.stream_results
        ]
        refresh_stream = getattr(result, "refresh_stream", None)
        if refresh_stream is not None and refresh_stream.start_monotonic is not None:
            intervals.append(
                (
                    refresh_stream.start_monotonic,
                    refresh_stream.end_monotonic or mono_time(),
                    refresh_stream.start_time,
                    refresh_stream.end_time or result.end_time,
                )
            )

        if intervals:
            first_interval = min(intervals, key=lambda interval: interval[0])
            last_interval = max(intervals, key=lambda interval: interval[1])
            total_time = last_interval[1] - first_interval[0]
            result.start_time = first_interval[2] or result.start_time
            result.end_time = last_interval[3] or result.end_time
        else:
            total_time = elapsed_seconds(start_time)

        result.total_time = total_time

        refresh_succeeded = refresh_stream is None or refresh_stream.success
        if not throughput_result_succeeded(result, config.num_streams) or not refresh_succeeded or total_time <= 0:
            result.throughput_at_size = 0.0
            result.query_throughput = 0.0
            result.success = False
            return False

        executed_queries = sum(sr.queries_executed for sr in result.stream_results)
        scored_queries = executed_queries if queries_per_stream is None else queries_per_stream * config.num_streams
        result.throughput_at_size = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=scored_queries,
            total_time_seconds=total_time,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
        )
        result.query_throughput = executed_queries / total_time
        result.success = result.throughput_at_size > 0
        return result.success
