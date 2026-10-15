import concurrent.futures
import contextvars
import logging
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from benchbox.core.plan_capture_phase import (
    propagate_query_execution_metadata,
)
from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult, throughput_stream_ids
from benchbox.core.throughput.runner import StreamRunner
from benchbox.core.tpch.power_test import _parse_tpch_query_id
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_preconditions,
    check_rf2_applied,
    check_rf2_preconditions,
    generate_refresh_update_sets,
)
from benchbox.core.validation.query_validation import (
    clear_reference_seed_context,
    set_reference_seed_context,
)
from benchbox.utils.clock import elapsed_seconds, mono_time


@dataclass
class TPCHThroughputTestConfig:
    scale_factor: float = 1.0
    num_streams: int = 2
    base_seed: int = 42
    stream_timeout: int = 3600
    max_workers: Optional[int] = None
    verbose: bool = False
    cancel_on_timeout: bool = False
    query_subset: Optional[list[str]] = None
    min_success_rate: float = 0.99
    refresh_functions: bool = False
    refresh_first_set: int = 1


TPCHThroughputStreamResult = ThroughputStreamResult


@dataclass
class TPCHRefreshStreamResult:
    first_set: int
    pairs: list[dict[str, Any]] = field(default_factory=list)
    start_time: str | None = None
    end_time: str | None = None
    start_monotonic: float | None = None
    end_monotonic: float | None = None
    status: str = "PENDING"
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "COMPLETED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_set": self.first_set,
            "pair_count": len(self.pairs),
            "pairs": self.pairs,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "error": self.error,
        }


def _derive_query_seed(seed: int, stream_id: int, position: int) -> int:
    return seed + stream_id * 1000 + position


def _stream_permutation(stream_id: int, query_subset: Optional[list[str]]) -> list[int]:
    from benchbox.core.tpch.streams import TPCHStreams

    permutation = list(TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)])
    if not isinstance(query_subset, (list, tuple)) or not query_subset:
        return permutation
    wanted = {_parse_tpch_query_id(query_id) for query_id in query_subset}
    unknown = sorted(wanted - set(permutation))
    if unknown:
        raise ValueError(f"Invalid TPC-H query ids in query_subset: {unknown} (expected 1-22)")
    return [query_id for query_id in permutation if query_id in wanted]


def _count_cursor_rows(cursor: Any) -> int:
    counter = getattr(cursor, "row_count", None)
    if callable(counter):
        return counter()

    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0:
        return rowcount
    return len(cursor.fetchall())


@dataclass
class TPCHThroughputTestResult(ThroughputResult):
    config: TPCHThroughputTestConfig = field(kw_only=True)
    refresh_stream: TPCHRefreshStreamResult | None = None

    @property
    def scale_factor(self) -> float:
        return self.config.scale_factor


class TPCHThroughputTest:
    def __init__(
        self,
        benchmark: Any,
        connection_factory: Callable[[], Any],
        scale_factor: float = 1.0,
        num_streams: int = 2,
        verbose: bool = False,
        dialect: Optional[str] = None,
        refresh_enabled: bool = False,
        refresh_first_set: int = 1,
        refresh_connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.benchmark = benchmark
        self.connection_factory = connection_factory
        self.target_dialect = dialect
        self.refresh_connection_factory = refresh_connection_factory
        self.config = TPCHThroughputTestConfig(
            scale_factor=scale_factor,
            num_streams=num_streams,
            verbose=verbose,
            refresh_functions=refresh_enabled,
            refresh_first_set=refresh_first_set,
        )

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)

        self.captured_items: list[tuple[str, str]] = []

        self._pregenerated_queries: Optional[dict[int, list[Any]]] = None

    def run(self, config: Optional[TPCHThroughputTestConfig] = None) -> TPCHThroughputTestResult:
        if config is None:
            config = self.config

        start_time = mono_time()
        result = TPCHThroughputTestResult(
            config=config,
            start_time=datetime.now().isoformat(),
            end_time="",
            total_time=0.0,
            throughput_at_size=0.0,
            streams_executed=0,
            streams_successful=0,
            query_throughput=0.0,
        )
        refresh_temp_directory = None

        try:
            refresh_temp_directory, update_sets = self._prepare_refresh_sets(config, result)

            if config.verbose:
                self.logger.info("Starting TPC-H Throughput Test")
                self.logger.info(f"Number of streams: {config.num_streams}")
                self.logger.info(f"Scale factor: {config.scale_factor}")

            self._pregenerated_queries = self._pregenerate_stream_queries(config)
            start_time = mono_time()
            result.start_time = datetime.now().isoformat()
            auxiliary_fn = None
            if config.refresh_functions:
                auxiliary_fn = lambda: self._execute_refresh_stream(
                    update_sets,
                    config.num_streams,
                    result.refresh_stream,
                )

            StreamRunner.execute(self._execute_stream, config, result, self.logger, auxiliary_fn=auxiliary_fn)

            if result.refresh_stream is not None and result.refresh_stream.status == "PENDING":
                result.refresh_stream.status = "FAILED"
                result.refresh_stream.error = "refresh stream did not start"
                result.refresh_stream.end_time = datetime.now().isoformat()

            result.success = StreamRunner.compute_metrics(result, config, start_time)

            if config.verbose:
                self.logger.info(f"Throughput Test completed in {result.total_time:.3f}s")
                self.logger.info(f"Successful streams: {result.streams_successful}/{config.num_streams}")
                if config.num_streams > 0:
                    success_rate = result.streams_successful / config.num_streams
                    self.logger.info(f"Stream success rate: {success_rate:.2%}")
                self.logger.info(f"Throughput@Size: {result.throughput_at_size:.2f}")
                self.logger.info(f"Query throughput: {result.query_throughput:.2f} queries/sec")

            return result

        except Exception as e:
            result.total_time = elapsed_seconds(start_time)
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Throughput Test execution failed: {e}")
            if config.refresh_functions:
                if result.refresh_stream is None:
                    result.refresh_stream = TPCHRefreshStreamResult(first_set=max(1, config.refresh_first_set + 1))
                if result.refresh_stream.status == "PENDING":
                    result.refresh_stream.status = "FAILED"
                    result.refresh_stream.error = str(e)
                    result.refresh_stream.end_time = result.end_time

            if config.verbose:
                self.logger.error(f"Throughput Test failed: {e}")

            return result

        finally:
            if refresh_temp_directory is not None:
                outstanding_future = getattr(result, "_outstanding_futures", {}).get(0)
                if outstanding_future is not None and not outstanding_future.done():
                    outstanding_future.add_done_callback(lambda _future: refresh_temp_directory.cleanup())
                else:
                    refresh_temp_directory.cleanup()

    def _prepare_refresh_sets(
        self, config: TPCHThroughputTestConfig, result: TPCHThroughputTestResult
    ) -> tuple[tempfile.TemporaryDirectory[str] | None, dict[int, TPCHRefreshUpdateSet]]:
        if not config.refresh_functions:
            return None, {}
        if config.num_streams < 1:
            raise ValueError("TPC-H refresh throughput requires at least one query stream")
        if isinstance(config.refresh_first_set, bool) or not isinstance(config.refresh_first_set, int):
            raise TypeError("tpch_refresh_first_set must be an integer")
        if config.refresh_first_set < 1:
            raise ValueError(f"tpch_refresh_first_set must be positive, got {config.refresh_first_set}")
        if self.refresh_connection_factory is None:
            raise RuntimeError("TPC-H refresh throughput requires a separate connection factory")
        temporary = tempfile.TemporaryDirectory(prefix="benchbox-tpch-refresh-")
        refresh_first_set = config.refresh_first_set + 1
        try:
            update_sets = generate_refresh_update_sets(
                scale_factor=config.scale_factor,
                first_set=refresh_first_set,
                count=config.num_streams,
                output_dir=Path(temporary.name),
            )
        except BaseException:
            temporary.cleanup()
            raise
        result.refresh_stream = TPCHRefreshStreamResult(first_set=refresh_first_set)
        return temporary, update_sets

    def _execute_refresh_stream(
        self,
        update_sets: dict[int, TPCHRefreshUpdateSet],
        num_streams: int,
        refresh_stream: TPCHRefreshStreamResult | None,
    ) -> TPCHRefreshStreamResult:
        if refresh_stream is None:
            raise RuntimeError("TPC-H refresh stream result was not initialized")

        refresh_stream.start_monotonic = mono_time()
        refresh_stream.start_time = datetime.now().isoformat()
        refresh_stream.status = "RUNNING"
        connection = None
        try:
            if self.refresh_connection_factory is None:
                raise RuntimeError("TPC-H refresh stream requires a separate connection factory")
            connection = self.refresh_connection_factory()
            deleted_lineitems = {}
            for offset in range(num_streams):
                update_set_id = refresh_stream.first_set + offset
                update_set = update_sets[update_set_id]
                pair = {"update_set": update_set_id, "functions": [], "status": "RUNNING", "error": None}
                refresh_stream.pairs.append(pair)
                try:
                    check_rf1_preconditions(connection, update_set)
                except Exception as exc:
                    self._record_refresh_precondition_failure(refresh_stream, pair, "RF1", update_set, exc)
                    return refresh_stream
                try:
                    deleted_lineitems[update_set_id] = check_rf2_preconditions(connection, update_set)
                except Exception as exc:
                    self._record_refresh_precondition_failure(refresh_stream, pair, "RF2", update_set, exc)
                    return refresh_stream

            for offset in range(num_streams):
                update_set_id = refresh_stream.first_set + offset
                update_set = update_sets[update_set_id]
                pair = refresh_stream.pairs[offset]
                for function in ("RF1", "RF2"):
                    function_result = self._execute_refresh_function(
                        function,
                        update_set,
                        connection,
                        deleted_lineitems.get(update_set_id),
                        prevalidated=True,
                    )
                    pair["functions"].append(function_result)
                    if function_result["status"] != "SUCCESS":
                        pair["status"] = "FAILED"
                        pair["error"] = function_result["error"]
                        refresh_stream.error = (
                            f"refresh set {update_set_id} {function} failed: {function_result['error']}"
                        )
                        refresh_stream.status = "FAILED"
                        return refresh_stream

                pair["status"] = "SUCCESS"

            for offset in range(num_streams):
                update_set_id = refresh_stream.first_set + offset
                try:
                    check_rf2_applied(connection, update_sets[update_set_id])
                except Exception as exc:
                    pair = refresh_stream.pairs[offset]
                    rf2_result = pair["functions"][1]
                    rf2_result["duration_ms"] = 0
                    rf2_result["status"] = "FAILED"
                    rf2_result["error"] = str(exc)
                    pair["status"] = "FAILED"
                    pair["error"] = str(exc)
                    refresh_stream.error = f"refresh set {update_set_id} RF2 failed: {exc}"
                    refresh_stream.status = "FAILED"
                    return refresh_stream

            refresh_stream.status = "COMPLETED"
            return refresh_stream
        except Exception as exc:
            refresh_stream.status = "FAILED"
            refresh_stream.error = str(exc)
            return refresh_stream
        finally:
            refresh_stream.end_monotonic = mono_time()
            refresh_stream.end_time = datetime.now().isoformat()
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _record_refresh_precondition_failure(
        refresh_stream: TPCHRefreshStreamResult,
        pair: dict[str, Any],
        function: str,
        update_set: TPCHRefreshUpdateSet,
        error: Exception,
    ) -> None:
        detail = str(error)
        pair["functions"].append(
            {
                "function": function,
                "update_set": update_set.set_id,
                "duration_ms": 0,
                "rows": 0,
                "status": "FAILED",
                "error": detail,
            }
        )
        pair["status"] = "FAILED"
        pair["error"] = detail
        refresh_stream.error = f"refresh set {update_set.set_id} {function} failed: {detail}"
        refresh_stream.status = "FAILED"

    def _execute_refresh_function(
        self,
        function: str,
        update_set: TPCHRefreshUpdateSet,
        connection: Any,
        deleted_lineitems: int | None = None,
        prevalidated: bool = False,
    ) -> dict[str, Any]:
        if function not in {"RF1", "RF2"}:
            raise ValueError(f"Unsupported TPC-H refresh function: {function}")

        rows = 0
        status = "FAILED"
        error = None
        duration_ms = 0
        try:
            if function == "RF1":
                if not prevalidated:
                    check_rf1_preconditions(connection, update_set)
                start_time = mono_time()
                rows = apply_rf1(connection, update_set)
            else:
                if deleted_lineitems is None:
                    deleted_lineitems = check_rf2_preconditions(connection, update_set)
                start_time = mono_time()
                rows = apply_rf2(connection, update_set, deleted_lineitems)
            duration_ms = max(1, round(elapsed_seconds(start_time) * 1000))
            if function == "RF2" and not prevalidated:
                check_rf2_applied(connection, update_set)
            status = "SUCCESS"
        except Exception as exc:
            error = str(exc)

        return {
            "function": function,
            "update_set": update_set.set_id,
            "duration_ms": duration_ms if status == "SUCCESS" else 0,
            "rows": rows,
            "status": status,
            "error": error,
        }

    def _pregenerate_stream_queries(self, config: TPCHThroughputTestConfig) -> dict[int, list[Any]]:

        def _generate_one_stream(stream_id: int) -> tuple[int, list[Any]]:
            seed = config.base_seed + stream_id
            query_permutation = _stream_permutation(stream_id, config.query_subset)
            sql_list: list[Any] = []
            for position, query_id in enumerate(query_permutation):
                stream_seed = _derive_query_seed(seed, stream_id, position)
                try:
                    sql_list.append(self._get_stream_query(query_id, stream_seed, stream_id, config.scale_factor))
                except Exception as exc:
                    sql_list.append(exc)
            return stream_id, sql_list

        stream_queries: dict[int, list[Any]] = {}
        if config.num_streams <= 0:
            return stream_queries

        max_workers = max(1, config.max_workers or config.num_streams)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: list[concurrent.futures.Future[tuple[int, list[Any]]]] = [
                executor.submit(contextvars.copy_context().run, _generate_one_stream, sid)
                for sid in throughput_stream_ids(config.num_streams)
            ]
            for future in concurrent.futures.as_completed(futures):
                stream_id, sql_list = future.result()
                stream_queries[stream_id] = sql_list

        return stream_queries

    def _get_stream_query(self, query_id: int, stream_seed: int, stream_id: int, scale_factor: float) -> str:
        from benchbox.core.tpch.streams import TPCHStreams

        return self.benchmark.get_query(
            query_id,
            seed=stream_seed,
            params={"stream_id": stream_id % len(TPCHStreams.PERMUTATION_MATRIX)},
            scale_factor=scale_factor,
            dialect=self.target_dialect,
        )

    def _resolve_query_text(
        self,
        pregenerated: Optional[list[Any]],
        position: int,
        stream_id: int,
        seed: int,
        query_id: int,
        config: TPCHThroughputTestConfig,
    ) -> str:
        if pregenerated is not None:
            pre = pregenerated[position]
            if isinstance(pre, BaseException):
                raise pre
            return pre

        stream_seed = _derive_query_seed(seed, stream_id, position)
        return self._get_stream_query(query_id, stream_seed, stream_id, config.scale_factor)

    def _resolve_cancel_event(self, config: TPCHThroughputTestConfig, stream_id: int) -> Optional[threading.Event]:
        if getattr(config, "cancel_on_timeout", False) is not True:
            return None
        cancel_events = getattr(config, "_stream_cancel_events", None)
        if not isinstance(cancel_events, dict):
            return None
        cancel_event = cancel_events.get(stream_id)
        return cancel_event if isinstance(cancel_event, threading.Event) else None

    def _cooperative_cancel_requested(
        self,
        cancel_event: Optional[threading.Event],
        config: TPCHThroughputTestConfig,
        stream_id: int,
        position: int,
        total_queries: int,
    ) -> bool:
        if cancel_event is None or not cancel_event.is_set():
            return False
        if config.verbose:
            self.logger.warning(
                f"Stream {stream_id} cooperative cancel signalled; stopping after {position}/{total_queries} queries"
            )
        return True

    def _finalize_stream_outcome(
        self, stream_result: TPCHThroughputStreamResult, cancelled: bool, total_queries: int
    ) -> None:
        if cancelled:
            stream_result.success = False
            stream_result.error = (
                f"Stream cancelled cooperatively after timeout "
                f"({stream_result.queries_executed}/{total_queries} queries completed)"
            )
        else:
            stream_result.success = stream_result.queries_failed == 0

    def _execute_stream(
        self, stream_id: int, seed: int, config: TPCHThroughputTestConfig
    ) -> TPCHThroughputStreamResult:
        start_time = mono_time()

        stream_result = TPCHThroughputStreamResult(
            stream_id=stream_id,
            start_time=start_time,
            end_time=0.0,
            duration=0.0,
            queries_executed=0,
            queries_successful=0,
            queries_failed=0,
            start_wall_time=datetime.now().isoformat(),
        )

        connection = None
        try:
            if config.verbose:
                self.logger.info(f"Starting stream {stream_id} with seed {seed}")

            connection = self.connection_factory()
            stream_result.start_time = mono_time()
            stream_result.start_wall_time = datetime.now().isoformat()

            query_permutation = _stream_permutation(stream_id, config.query_subset)

            if config.verbose:
                self.logger.info(f"Stream {stream_id} using TPC-H permutation: {query_permutation}")

            pregenerated = self._pregenerated_queries.get(stream_id) if self._pregenerated_queries is not None else None

            cancel_event = self._resolve_cancel_event(config, stream_id)

            cancelled = False
            for position, query_id in enumerate(query_permutation):
                if self._cooperative_cancel_requested(
                    cancel_event, config, stream_id, position, len(query_permutation)
                ):
                    cancelled = True
                    break

                query_start = mono_time()
                query_result: dict[str, Any] = {
                    "query_id": query_id,
                    "position": position + 1,
                    "stream_id": stream_id,
                    "execution_time_seconds": 0.0,
                    "success": False,
                    "error": None,
                    "result_count": 0,
                }

                try:
                    query_text = self._resolve_query_text(pregenerated, position, stream_id, seed, query_id, config)

                    label = f"Stream_{stream_id}_Position_{position + 1}_Query_{query_id}"
                    try:
                        if hasattr(connection, "set_query_context"):
                            connection.set_query_context(query_id)

                        set_reference_seed_context(False)

                        cursor = connection.execute(query_text)

                        if hasattr(cursor, "platform_result"):
                            result_dict = cursor.platform_result
                            if result_dict.get("status") == "FAILED":
                                error_msg = result_dict.get(
                                    "error", result_dict.get("row_count_validation_error", "Query validation failed")
                                )
                                raise RuntimeError(error_msg)
                            propagate_query_execution_metadata(result_dict, query_result)

                        if hasattr(connection, "commit"):
                            connection.commit()
                    finally:
                        clear_reference_seed_context()
                        if hasattr(self, "captured_items"):
                            self.captured_items.append((label, query_text))

                    result_count = _count_cursor_rows(cursor)
                    execution_time = elapsed_seconds(query_start)

                    query_result.update(
                        {
                            "execution_time_seconds": execution_time,
                            "success": True,
                            "result_count": result_count,
                        }
                    )

                    stream_result.queries_successful += 1

                except Exception as e:
                    execution_time = elapsed_seconds(query_start)
                    query_result.update(
                        {
                            "execution_time_seconds": execution_time,
                            "success": False,
                            "error": str(e),
                        }
                    )

                    stream_result.queries_failed += 1

                    if config.verbose:
                        self.logger.error(f"Stream {stream_id} Query {query_id} failed: {e}")

                stream_result.query_results.append(query_result)
                stream_result.queries_executed += 1

            self._finalize_stream_outcome(stream_result, cancelled, len(query_permutation))

            if config.verbose:
                self.logger.info(
                    f"Stream {stream_id} completed: "
                    f"{stream_result.queries_successful}/{stream_result.queries_executed} successful"
                )

        except Exception as e:
            stream_result.error = str(e)
            stream_result.success = False

            if config.verbose:
                self.logger.error(f"Stream {stream_id} failed: {e}")

        finally:
            stream_result.end_time = mono_time()
            stream_result.end_wall_time = datetime.now().isoformat()
            stream_result.duration = stream_result.end_time - stream_result.start_time

            if connection is not None:
                try:
                    connection.close()
                except Exception as close_error:
                    if config.verbose:
                        self.logger.warning(f"Failed to close connection for stream {stream_id}: {close_error}")

        return stream_result

    def validate_results(self, result: TPCHThroughputTestResult) -> bool:
        if not result.success:
            return False

        if result.streams_executed != result.config.num_streams:
            return False

        if result.streams_successful != result.config.num_streams:
            return False

        if result.errors or result.throughput_at_size is None or result.throughput_at_size <= 0:
            return False

        return all(
            stream_result.queries_executed
            == len(_stream_permutation(stream_result.stream_id, result.config.query_subset))
            for stream_result in result.stream_results
        )
