from __future__ import annotations

import concurrent.futures
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Protocol

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.utils.clock import elapsed_seconds, mono_time

from .containment import record_outstanding_result
from .executor import DaemonStreamExecutor
from .result import (
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
        timeout: float | None = config.stream_timeout if config.stream_timeout > 0 else None

        if max_workers < config.num_streams and timeout is not None:
            logger.warning(
                f"max_workers ({max_workers}) < num_streams ({config.num_streams}) with a "
                f"stream_timeout of {timeout}s set: queued streams share the same overall "
                "deadline as immediately-started ones and may be reported as timed-out before "
                "executing a single query. Set max_workers >= num_streams (or leave it unset) "
                "for the timeout to behave as a true per-stream timeout."
            )

        cooperative_cancel = bool(getattr(config, "cancel_on_timeout", False))
        cancel_events: dict[int, threading.Event] = (
            {stream_id: threading.Event() for stream_id in throughput_stream_ids(config.num_streams)}
            if cooperative_cancel
            else {}
        )
        config._stream_cancel_events = cancel_events

        future_to_stream_id: dict[concurrent.futures.Future[Any], int] = {}
        pending: set[concurrent.futures.Future[Any]] = set()
        outstanding_futures: dict[int, concurrent.futures.Future[Any]] = {}

        def _settle_pending(reason: str) -> None:
            for future in sorted(pending, key=future_to_stream_id.__getitem__):
                stream_id = future_to_stream_id[future]
                label = "Refresh stream" if stream_id == 0 else f"Stream {stream_id}"
                pending.discard(future)

                if stream_id in cancel_events:
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
                    error_msg = (
                        f"{label} {reason} and has not completed. "
                        "Python cannot forcibly cancel a running thread, so this worker "
                        "may still be executing work and holding its database connection in "
                        "the background (leaked)"
                        + (
                            "; cooperative cancellation has been signalled and the stream should "
                            "stop before its next query"
                            if stream_id in cancel_events
                            else ""
                        )
                        + "."
                    )
                    result.errors.append(error_msg)
                    result.outstanding_notes.append(
                        f"{label} worker still running when it was settled"
                        + (
                            "; cooperative cancellation signalled"
                            if stream_id in cancel_events
                            else "; no cooperative cancellation"
                        )
                        + "."
                    )
                    logger.warning(error_msg)

        executor = DaemonStreamExecutor(max_workers=max_workers + (1 if auxiliary_fn else 0))
        try:
            for stream_id in throughput_stream_ids(config.num_streams):
                future = executor.submit(stream_fn, stream_id, config.base_seed + stream_id, config)
                future_to_stream_id[future] = stream_id
                pending.add(future)
            if auxiliary_fn:
                future = executor.submit(auxiliary_fn)
                future_to_stream_id[future] = 0
                pending.add(future)

            try:
                for future in concurrent.futures.as_completed(pending, timeout=timeout):
                    pending.discard(future)
                    _record_completed_future(future, future_to_stream_id[future], result, config, logger)
            except concurrent.futures.TimeoutError:
                _settle_pending(f"timed out after {timeout}s")
        except BaseException:
            _settle_pending("was abandoned after the runner failed")
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
