import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from benchbox.core.connection import DatabaseConnection
from benchbox.core.plan_capture_phase import (
    propagate_query_execution_metadata,
)
from benchbox.core.result_receipt import cursor_result_count
from benchbox.core.results.metrics import TPC_QUERIES_PER_STREAM
from benchbox.core.throughput.result import (
    THROUGHPUT_FIRST_STREAM_ID,
    ThroughputResult,
    ThroughputStreamResult,
    throughput_stream_ids,
)
from benchbox.core.throughput.runner import StreamRunner
from benchbox.core.validation.query_validation import (
    clear_reference_seed_context,
    reset_stream_seed_override_warnings,
    set_reference_seed_context,
)
from benchbox.utils.clock import elapsed_seconds, mono_time


@dataclass
class TPCDSThroughputTestConfig:
    scale_factor: float = 1.0
    num_streams: int = 4
    base_seed: int = 42
    stream_timeout: int = 7200
    max_workers: Optional[int] = None
    verbose: bool = False
    cancel_on_timeout: bool = False
    query_subset: Optional[list[str]] = None
    queries_per_stream: Optional[int] = None
    enable_preflight: bool = True
    min_success_rate: float = 0.70


TPCDSThroughputStreamResult = ThroughputStreamResult


def _query_token(value: Any) -> str:
    token = str(value).strip().lower()
    return token[1:] if token.startswith("q") else token


def _apply_query_subset(stream_queries: list[Any], query_subset: Optional[list[str]]) -> list[Any]:
    if not isinstance(query_subset, (list, tuple)) or not query_subset:
        return stream_queries
    wanted = {_query_token(query_id) for query_id in query_subset}
    selected = [
        stream_query
        for stream_query in stream_queries
        if str(stream_query.query_id) in wanted or f"{stream_query.query_id}{stream_query.variant or ''}" in wanted
    ]
    available = {str(stream_query.query_id) for stream_query in stream_queries} | {
        f"{stream_query.query_id}{stream_query.variant or ''}" for stream_query in stream_queries
    }
    unknown = sorted(wanted - available)
    if unknown:
        raise ValueError(f"Invalid TPC-DS query ids in query_subset: {unknown}")
    return selected


def _scored_queries_per_stream(config: TPCDSThroughputTestConfig) -> int:
    if config.queries_per_stream is None:
        return TPC_QUERIES_PER_STREAM["tpcds"]
    return min(config.queries_per_stream, TPC_QUERIES_PER_STREAM["tpcds"])


@dataclass
class TPCDSThroughputTestResult(ThroughputResult):
    config: TPCDSThroughputTestConfig = field(kw_only=True)

    @property
    def scale_factor(self) -> float:
        return self.config.scale_factor


class TPCDSThroughputTest:
    def __init__(
        self,
        benchmark: Any,
        connection_factory: Optional[Callable[[], Any]] = None,
        scale_factor: float = 1.0,
        num_streams: int = 4,
        verbose: bool = False,
        connection_string: Optional[str] = None,
        dialect: Optional[str] = None,
    ) -> None:
        self.benchmark = benchmark

        if connection_string is not None:
            conn_str = connection_string
            self.connection_factory = lambda: self._create_connection_from_string(conn_str)
        elif connection_factory is not None:
            self.connection_factory = connection_factory
        else:
            self.connection_factory = lambda: DatabaseConnection(sqlite3.connect(":memory:"), dialect="sqlite")

        self.config = TPCDSThroughputTestConfig(scale_factor=scale_factor, num_streams=num_streams, verbose=verbose)

        self.target_dialect = dialect

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)
        self.captured_items: list[tuple[str, str]] = []

        self._pregenerated_queries: Optional[dict[int, list[tuple[Any, Any]]]] = None

        self._capture_lock = threading.Lock()

    def _create_connection_from_string(self, connection_string: str) -> DatabaseConnection:
        if connection_string in {"sqlite::memory:", ":memory:", "sqlite://:memory:"}:
            return DatabaseConnection(sqlite3.connect(":memory:"), dialect="sqlite")

        if connection_string.startswith("sqlite:///"):
            db_path = connection_string.replace("sqlite:///", "", 1)
            return DatabaseConnection(sqlite3.connect(db_path), dialect="sqlite")

        raise ValueError(
            "Unsupported connection_string for TPCDSThroughputTest. Provide connection_factory for non-SQLite backends."
        )

    def run(self, config: Optional[TPCDSThroughputTestConfig] = None) -> TPCDSThroughputTestResult:
        if config is None:
            config = self.config

        start_time = mono_time()
        start_time_str = datetime.now().isoformat()

        result = TPCDSThroughputTestResult(
            config=config,
            start_time=start_time_str,
            end_time="",
            total_time=0.0,
            throughput_at_size=0.0,
            streams_executed=0,
            streams_successful=0,
            stream_results=[],
            query_throughput=0.0,
            success=True,
            errors=[],
        )

        try:
            if config.verbose:
                self.logger.info("Starting TPC-DS Throughput Test")
                self.logger.info(f"Number of streams: {config.num_streams}")
                self.logger.info(f"Scale factor: {config.scale_factor}")

        except Exception:
            raise

        reset_stream_seed_override_warnings()
        self._pregenerated_queries = None
        if config.enable_preflight:
            self._pregenerated_queries = self._pregenerate_stream_queries(config)

        try:
            StreamRunner.execute(self._execute_stream, config, result, self.logger)

            result.success = StreamRunner.compute_metrics(
                result, config, start_time, queries_per_stream=_scored_queries_per_stream(config)
            )

            success_rate = result.streams_successful / max(config.num_streams, 1)

            if config.verbose:
                self.logger.info(f"Throughput Test completed in {result.total_time:.3f}s")
                self.logger.info(f"Successful streams: {result.streams_successful}/{config.num_streams}")
                self.logger.info(f"Stream success rate: {success_rate:.2%}")
                self.logger.info(f"Throughput@Size: {result.throughput_at_size:.2f}")
                self.logger.info(f"Query throughput: {result.query_throughput:.2f} queries/sec")

            return result

        except Exception as e:
            result.total_time = elapsed_seconds(start_time)
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Throughput Test execution failed: {e}")

            if config.verbose:
                self.logger.error(f"Throughput Test failed: {e}")

            return result

    def _preflight_validate_generation(self, config: TPCDSThroughputTestConfig) -> None:
        failures = []

        if config.verbose:
            self.logger.info(
                f"Preflight validation: checking all 99 queries × {config.num_streams} streams "
                f"= {99 * config.num_streams} total validations"
            )

        try:
            available_query_ids = list(range(1, 100))

            for stream_id in throughput_stream_ids(config.num_streams):
                for position, query_id in enumerate(available_query_ids):
                    stream_seed = config.base_seed + stream_id * 1000 + position
                    try:
                        _ = self.benchmark.get_query(
                            query_id, seed=stream_seed, scale_factor=config.scale_factor, dialect=self.target_dialect
                        )
                    except Exception as e:
                        failures.append(f"stream {stream_id} q{query_id} pos {position + 1}: {e}")
        except Exception as e:
            raise RuntimeError(f"Throughput preflight internal error: {e}") from e

        if failures:
            msg = (
                f"TPC-DS ThroughputTest preflight failed for {len(failures)} queries. "
                f"Examples: {', '.join(failures[:3])}"
            )
            raise RuntimeError(msg)

    def _pregenerate_stream_queries(self, config: TPCDSThroughputTestConfig) -> dict[int, list[tuple[Any, str]]]:
        from benchbox.core.tpcds.streams import DSQGenStreamsError, generate_dsqgen_streams

        try:
            all_streams = generate_dsqgen_streams(
                num_streams=config.num_streams + THROUGHPUT_FIRST_STREAM_ID,
                scale_factor=config.scale_factor,
                seed=config.base_seed,
            )
        except (DSQGenStreamsError, ValueError) as e:
            raise RuntimeError(f"TPC-DS ThroughputTest dsqgen -STREAMS generation failed: {e}") from e

        stream_queries: dict[int, list[tuple[Any, str]]] = {}
        failures: list[str] = []

        for stream_id in throughput_stream_ids(config.num_streams):
            query_subset = _apply_query_subset(all_streams.get(stream_id, []), config.query_subset)
            if config.queries_per_stream is not None:
                query_subset = query_subset[: min(config.queries_per_stream, len(query_subset))]

            entries: list[tuple[Any, str]] = []
            for position, stream_query in enumerate(query_subset):
                try:
                    sql_text = self._translate_stream_query_sql(stream_query)
                    entries.append((stream_query, sql_text))
                except Exception as e:
                    failures.append(f"stream {stream_id} q{stream_query.query_id} pos {position + 1}: {e}")

            stream_queries[stream_id] = entries

        if failures:
            msg = (
                f"TPC-DS ThroughputTest preflight failed for {len(failures)} queries. "
                f"Examples: {', '.join(failures[:3])}"
            )
            raise RuntimeError(msg)

        return stream_queries

    def _translate_stream_query_sql(self, stream_query: Any) -> str:
        raw_sql = stream_query.sql
        target = (self.target_dialect or "netezza").lower()

        implementation = getattr(self.benchmark, "_impl", None)
        translate_fn = getattr(self.benchmark, "translate_query_text", None)
        if not callable(translate_fn) and implementation is not None:
            translate_fn = getattr(implementation, "translate_query_text", None)
        translated = translate_fn(raw_sql, "netezza", target) if callable(translate_fn) else raw_sql

        override_fn = getattr(self.benchmark, "_apply_target_dialect_overrides", None)
        if not callable(override_fn) and implementation is not None:
            override_fn = getattr(implementation, "_apply_target_dialect_overrides", None)
        if callable(override_fn):
            translated = override_fn(stream_query.query_id, translated, target)

        return translated

    def _resolve_available_query_ids(self) -> list[int]:
        try:
            all_queries = self.benchmark.get_queries()
            ids = [int(k) for k in all_queries if k.isdigit()]
            return ids if ids else list(range(1, 100))
        except Exception:
            return list(range(1, 100))

    def _resolve_query_manager(self):
        if hasattr(self.benchmark, "query_manager"):
            return self.benchmark.query_manager
        if hasattr(self.benchmark, "_impl") and hasattr(self.benchmark._impl, "query_manager"):
            return self.benchmark._impl.query_manager
        raise RuntimeError("No query_manager found - ThroughputTest requires a TPCDSBenchmark instance")

    def _build_stream_queries(self, stream_id: int, seed: int, config: TPCDSThroughputTestConfig) -> list:
        from benchbox.core.tpcds.streams import create_standard_streams

        available_query_ids = self._resolve_available_query_ids()
        query_manager = self._resolve_query_manager()

        query_range = (min(available_query_ids), max(available_query_ids)) if available_query_ids else (1, 99)
        stream_manager = create_standard_streams(
            query_manager=query_manager,
            num_streams=config.num_streams + THROUGHPUT_FIRST_STREAM_ID,
            query_range=query_range,
            base_seed=config.base_seed,
        )

        streams = stream_manager.generate_streams()
        all_queries = _apply_query_subset(streams.get(stream_id, []), config.query_subset)

        if config.queries_per_stream is not None:
            subset = all_queries[: min(config.queries_per_stream, len(all_queries))]
            if config.verbose:
                self.logger.info(
                    f"Stream {stream_id} using TPC-DS permutation with {len(subset)} queries "
                    f"(limited by queries_per_stream={config.queries_per_stream})"
                )
            return subset
        if config.verbose:
            self.logger.info(
                f"Stream {stream_id} using TPC-DS permutation with {len(all_queries)} queries (full query set)"
            )
        return all_queries

    def _cached_query_text(self, stream_id: int, position: int) -> Optional[str]:
        if self._pregenerated_queries is None:
            return None
        entries = self._pregenerated_queries.get(stream_id)
        if entries is None or position >= len(entries):
            return None
        return entries[position][1]

    def _get_stream_query_text(self, query_id: int, variant, stream_seed: int, scale_factor) -> str:
        if variant is not None:
            return self.benchmark.get_query(
                query_id,
                seed=stream_seed,
                scale_factor=scale_factor,
                variant=variant,
                dialect=self.target_dialect,
            )
        return self.benchmark.get_query(
            query_id, seed=stream_seed, scale_factor=scale_factor, dialect=self.target_dialect
        )

    def _run_single_stream_query(
        self, connection, query_text: str, query_display_id: str, stream_id: int
    ) -> tuple[dict[str, Any] | None, int]:
        if hasattr(connection, "set_query_context"):
            connection.set_query_context(query_display_id, stream_id=stream_id)
        set_reference_seed_context(False)
        try:
            cursor = connection.execute(query_text)
        finally:
            clear_reference_seed_context()
        platform_result = getattr(cursor, "platform_result", None)
        if isinstance(platform_result, dict) and platform_result.get("status") == "FAILED":
            raise RuntimeError(
                platform_result.get("error", platform_result.get("row_count_validation_error", "Query failed"))
            )
        row_count = cursor_result_count(cursor)
        if hasattr(connection, "commit"):
            connection.commit()
        return platform_result, row_count

    def _execute_single_query(
        self,
        connection,
        stream_id: int,
        position: int,
        stream_query,
        seed: int,
        config: TPCDSThroughputTestConfig,
        stream_result: TPCDSThroughputStreamResult,
    ) -> None:
        query_id = stream_query.query_id
        variant = stream_query.variant
        query_display_id = f"{query_id}{variant}" if variant else str(query_id)
        query_start = mono_time()
        query_result = {
            "query_id": query_display_id,
            "position": position + 1,
            "stream_id": stream_id,
            "execution_time_seconds": 0.0,
            "success": False,
            "error": None,
            "result_count": 0,
        }

        try:
            cached_text = self._cached_query_text(stream_id, position)
            if cached_text is not None:
                query_text = cached_text
            else:
                stream_seed = seed + stream_id * 1000 + position
                query_text = self._get_stream_query_text(query_id, variant, stream_seed, config.scale_factor)
            label = f"Stream_{stream_id}_Position_{position + 1}_Query_{query_display_id}"
            try:
                platform_result, row_count = self._run_single_stream_query(
                    connection, query_text, query_display_id, stream_id
                )
            finally:
                with self._capture_lock:
                    self.captured_items.append((label, query_text))

            if platform_result is not None:
                propagate_query_execution_metadata(platform_result, query_result)

            query_result.update(
                {
                    "execution_time_seconds": elapsed_seconds(query_start),
                    "success": True,
                    "result_count": row_count,
                }
            )
            stream_result.queries_successful += 1
        except Exception as e:
            query_result.update(
                {
                    "execution_time_seconds": elapsed_seconds(query_start),
                    "success": False,
                    "error": str(e),
                }
            )
            stream_result.queries_failed += 1
            if "Template substitution error" not in str(e) and config.verbose:
                self.logger.warning(f"Stream {stream_id} Query {query_id} failed: {e}")

        stream_result.query_results.append(query_result)
        stream_result.queries_executed += 1

    def _finalize_stream_success(
        self,
        stream_id: int,
        stream_result: TPCDSThroughputStreamResult,
        config: TPCDSThroughputTestConfig,
        cancelled: bool = False,
    ) -> None:
        if cancelled:
            stream_result.success = False
            stream_result.error = stream_result.error or (
                f"Stream cancelled cooperatively after timeout ({stream_result.queries_executed} queries completed)"
            )
            if config.verbose:
                self.logger.info(f"Stream {stream_id} completed: cancelled after timeout")
            return

        if stream_result.queries_executed == 0:
            stream_result.success = False
            if config.verbose:
                self.logger.info(f"Stream {stream_id} completed: no queries executed")
            return

        success_rate = stream_result.queries_successful / stream_result.queries_executed
        stream_result.success = stream_result.queries_failed == 0
        if config.verbose:
            self.logger.info(
                f"Stream {stream_id} completed: "
                f"{stream_result.queries_successful}/{stream_result.queries_executed} successful "
                f"(success rate: {success_rate:.2%})"
            )

    def _close_stream_connection(self, connection, stream_id: int, config: TPCDSThroughputTestConfig) -> None:
        if connection is None:
            return
        try:
            connection.close()
        except Exception as close_error:
            if config.verbose:
                self.logger.warning(f"Failed to close connection for stream {stream_id}: {close_error}")

    def _execute_stream(
        self, stream_id: int, seed: int, config: TPCDSThroughputTestConfig
    ) -> TPCDSThroughputStreamResult:
        start_time = mono_time()
        stream_result = TPCDSThroughputStreamResult(
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

            cached_entries = (
                self._pregenerated_queries.get(stream_id) if self._pregenerated_queries is not None else None
            )
            if cached_entries is not None:
                query_subset = [stream_query for stream_query, _sql in cached_entries]
            else:
                query_subset = self._build_stream_queries(stream_id, seed, config)

            cancel_event = None
            if getattr(config, "cancel_on_timeout", False) is True:
                cancel_events = getattr(config, "_stream_cancel_events", None)
                if isinstance(cancel_events, dict):
                    candidate = cancel_events.get(stream_id)
                    if isinstance(candidate, threading.Event):
                        cancel_event = candidate

            cancelled = False
            for position, stream_query in enumerate(query_subset):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    if config.verbose:
                        self.logger.warning(
                            f"Stream {stream_id} cooperative cancel signalled; stopping after "
                            f"{position}/{len(query_subset)} queries"
                        )
                    break
                self._execute_single_query(connection, stream_id, position, stream_query, seed, config, stream_result)

            self._finalize_stream_success(stream_id, stream_result, config, cancelled=cancelled)
        except Exception as e:
            stream_result.error = str(e)
            stream_result.success = False
            if config.verbose:
                self.logger.error(f"Stream {stream_id} failed: {e}")
        finally:
            stream_result.end_time = mono_time()
            stream_result.end_wall_time = datetime.now().isoformat()
            stream_result.duration = stream_result.end_time - stream_result.start_time
            self._close_stream_connection(connection, stream_id, config)

        return stream_result

    def validate_results(self, result: TPCDSThroughputTestResult) -> bool:
        if not result.success:
            return False

        if result.streams_executed != result.config.num_streams:
            return False

        if result.streams_successful != result.config.num_streams:
            return False

        return not result.errors and result.throughput_at_size is not None and result.throughput_at_size > 0
