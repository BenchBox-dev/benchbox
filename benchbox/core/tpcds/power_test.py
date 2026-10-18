# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from benchbox.core.connection import DatabaseConnection
from benchbox.core.plan_capture_phase import (
    propagate_query_execution_metadata,
)
from benchbox.core.validation.query_validation import (
    clear_reference_seed_context,
    set_reference_seed_context,
)
from benchbox.utils.clock import elapsed_seconds, mono_time


def power_parameter_seed(seed: Optional[int], stream_id: int) -> int:
    return (seed or 1) + stream_id + 1000


def _parse_tpcds_query_id(qid: object) -> int:
    text = str(qid).strip()
    if text[:1] in ("Q", "q"):
        text = text[1:]
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid TPC-DS query id: {qid!r} (expected 1-99, optionally Q-prefixed)") from exc


@dataclass
class TPCDSPowerTestConfig:
    scale_factor: float = 1.0
    seed: int = 1
    stream_id: int = 0
    timeout: Optional[float] = None
    warm_up: bool = True
    validation: bool = True
    verbose: bool = False
    query_subset: Optional[list[str]] = None


@dataclass
class TPCDSPowerTestResult:
    config: TPCDSPowerTestConfig
    start_time: str
    end_time: str
    total_time: float
    power_at_size: float
    queries_executed: int
    queries_successful: int
    query_results: list[dict[str, Any]]
    success: bool
    errors: list[str]

    @property
    def scale_factor(self) -> float:
        return self.config.scale_factor


class TPCDSPowerTest:
    def __init__(
        self,
        benchmark: Any,
        connection_factory: Optional[Callable[[], Any]] = None,
        scale_factor: float = 1.0,
        seed: Optional[int] = None,
        stream_id: int = 0,
        verbose: bool = False,
        timeout: Optional[float] = None,
        connection_string: Optional[str] = None,
        dialect: Optional[str] = None,
        warm_up: bool = True,
        validation: bool = True,
        query_subset: Optional[list[str]] = None,
    ) -> None:
        if scale_factor <= 0:
            raise ValueError("scale_factor must be a positive number")

        self.benchmark = benchmark

        if connection_string is not None:
            conn_str = connection_string
            self.connection_factory = lambda: self._create_connection_from_string(conn_str, dialect)
        elif connection_factory is not None:
            self.connection_factory = connection_factory
        else:
            self.connection_factory = lambda: DatabaseConnection(sqlite3.connect(":memory:"), dialect="sqlite")

        self.config = TPCDSPowerTestConfig(
            scale_factor=scale_factor,
            seed=seed or 1,
            stream_id=stream_id,
            timeout=timeout,
            warm_up=warm_up,
            validation=validation,
            verbose=verbose,
            query_subset=query_subset,
        )

        self.target_dialect = dialect

        self.connection = None
        self.test_running = False
        self.current_query = None

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)
        self.captured_items: list[tuple[str, str]] = []

    def run(self) -> TPCDSPowerTestResult:
        start_time = mono_time()
        result = TPCDSPowerTestResult(
            config=self.config,
            start_time=datetime.now().isoformat(),
            end_time="",
            total_time=0.0,
            power_at_size=0.0,
            queries_executed=0,
            queries_successful=0,
            query_results=[],
            success=True,
            errors=[],
        )

        if self.config.verbose:
            self.logger.info("Starting TPC-DS Power Test")
            self.logger.info(f"Scale factor: {self.config.scale_factor}")
            self.logger.info(f"Seed: {self.config.seed}")

        available_query_ids = self._determine_available_query_ids()
        if self.config.verbose:
            self.logger.info(f"Found {len(available_query_ids)} queries to execute")

        queries_to_execute = self._build_queries_to_execute(available_query_ids)

        if not queries_to_execute:
            error_msg = (
                "TPC-DS power test generated zero queries - "
                "dsqgen binary may be missing, the stream is empty, or "
                "query generation failed for all queries. "
                "Check dsqgen availability and benchmark configuration."
            )
            result.success = False
            result.errors.append(error_msg)
            result.end_time = datetime.now().isoformat()
            result.total_time = elapsed_seconds(start_time)
            self.logger.error(error_msg)
            return result

        self._preflight_validate_generation(available_query_ids)

        try:
            connection = self.connection_factory()
            stream_param_seed = power_parameter_seed(self.config.seed, self.config.stream_id)
            if self.config.verbose:
                self.logger.info(f"Using parameter seed {stream_param_seed} for stream {self.config.stream_id}")

            for position, query_info in enumerate(queries_to_execute):
                self._execute_one_query(position, query_info, connection, stream_param_seed, result)

            connection.close()
            self._finalize_power_metrics(result, start_time)
            return result
        except Exception as e:
            result.total_time = elapsed_seconds(start_time)
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Power Test execution failed: {e}")
            if self.config.verbose:
                self.logger.error(f"Power Test failed: {e}")
            return result

    def _determine_available_query_ids(self) -> list[int]:
        try:
            all_queries = self.benchmark.get_queries()
            available = sorted(int(k) for k in all_queries if k.isdigit())
            return available if available else list(range(1, 100))
        except Exception:
            return list(range(1, 100))

    def _build_queries_to_execute(self, available_query_ids: list[int]) -> list[tuple]:
        if self.config.query_subset:
            queries = [(_parse_tpcds_query_id(qid), None) for qid in self.config.query_subset]
            if self.config.verbose:
                self.logger.info(f"Using user-specified query subset: {[q[0] for q in queries]}")
            self.logger.warning(
                "⚠️  query_subset overrides standard query sequence - results may not be compliant. "
                "Official TPC-DS benchmarks require running all queries in the specified order."
            )
            return queries

        if hasattr(self, "_query_sequence"):
            if self.config.verbose:
                self.logger.info(f"Using custom query sequence: {self._query_sequence}")
            return self._query_sequence

        query_manager = self._resolve_query_manager()
        if query_manager is None:
            if self.config.verbose:
                self.logger.warning("No query_manager found, using sequential execution")
            return [(q, None) for q in available_query_ids]

        from benchbox.core.tpcds.streams import create_standard_streams

        stream_id = getattr(self.config, "stream_id", 0)
        stream_manager = create_standard_streams(
            query_manager=query_manager,
            num_streams=1,
            query_ids=available_query_ids or None,
            query_range=(1, 99),
            base_seed=self.config.seed + stream_id,
        )
        streams = stream_manager.generate_streams()
        stream_queries = streams.get(0, [])
        queries = [(sq.query_id, sq.variant) for sq in stream_queries]
        if self.config.verbose:
            self.logger.info(f"Using TPC-DS stream {stream_id} with {len(queries)} queries")
            self.logger.info(f"Query order (first 10): {queries[:10]}")
        return queries

    def _resolve_query_manager(self) -> Any:
        if hasattr(self.benchmark, "query_manager"):
            return self.benchmark.query_manager
        impl = getattr(self.benchmark, "_impl", None)
        if impl is not None and hasattr(impl, "query_manager"):
            return impl.query_manager
        return None

    def _execute_one_query(
        self,
        position: int,
        query_info: Any,
        connection: Any,
        stream_param_seed: int,
        result: TPCDSPowerTestResult,
    ) -> None:
        if isinstance(query_info, tuple):
            query_id, variant = query_info
            query_display_id = f"{query_id}{variant}" if variant else str(query_id)
        else:
            query_id = query_info
            variant = None
            query_display_id = str(query_id)

        query_start = mono_time()
        query_result: dict[str, Any] = {
            "query_id": query_display_id,
            "position": position + 1,
            "stream_id": self.config.stream_id,
            "execution_time_seconds": 0.0,
            "success": False,
            "error": None,
            "result_count": 0,
        }

        try:
            if self.config.verbose:
                self.logger.info(f"Executing Query {query_display_id} (position {position + 1})")

            get_query_kwargs: dict[str, Any] = {
                "seed": stream_param_seed,
                "scale_factor": self.config.scale_factor,
                "dialect": self.target_dialect,
            }
            if variant is not None:
                get_query_kwargs["variant"] = variant
            query_text = self.benchmark.get_query(query_id, **get_query_kwargs)

            label = f"Position_{position + 1}_Query_{query_display_id}"
            try:
                if hasattr(connection, "set_query_context"):
                    connection.set_query_context(query_display_id, stream_id=self.config.stream_id)
                from benchbox.core.expected_results.tpcds_results import tpcds_subscale_reference_seed

                set_reference_seed_context(tpcds_subscale_reference_seed(self.config.seed, self.config.stream_id))
                try:
                    cursor = connection.execute(query_text)

                    if hasattr(cursor, "platform_result"):
                        result_dict = cursor.platform_result
                        if result_dict.get("status") == "FAILED":
                            error_msg = result_dict.get(
                                "error", result_dict.get("row_count_validation_error", "Query validation failed")
                            )
                            raise RuntimeError(error_msg)
                        propagate_query_execution_metadata(result_dict, query_result)

                    result_count = self._query_result_count(cursor)

                    if hasattr(connection, "commit"):
                        connection.commit()
                finally:
                    clear_reference_seed_context()
            finally:
                self.captured_items.append((label, query_text))

            execution_time = elapsed_seconds(query_start)
            query_result.update(
                {
                    "execution_time_seconds": execution_time,
                    "success": True,
                    "result_count": result_count,
                }
            )
            result.queries_successful += 1
            if self.config.verbose:
                self.logger.info(f"Query {query_id} completed in {execution_time:.3f}s")

        except Exception as e:
            execution_time = elapsed_seconds(query_start)
            query_result.update(
                {
                    "execution_time_seconds": execution_time,
                    "success": False,
                    "error": str(e),
                }
            )
            if "Template substitution error" not in str(e):
                result.errors.append(f"Query {query_id} failed: {e}")
            if self.config.verbose:
                self.logger.warning(f"Query {query_id} failed: {e}")

        result.query_results.append(query_result)
        result.queries_executed += 1

    @staticmethod
    def _query_result_count(cursor: Any) -> int:
        platform_result = getattr(cursor, "platform_result", None)
        if isinstance(platform_result, dict):
            reported = platform_result.get("rows_returned")
            if isinstance(reported, int) and reported >= 0:
                return reported
        if hasattr(cursor, "fetchall"):
            return len(cursor.fetchall())
        return 0

    def _finalize_power_metrics(self, result: TPCDSPowerTestResult, start_time: float) -> None:
        total_execution_time = elapsed_seconds(start_time)
        exec_times = [
            qr["execution_time_seconds"]
            for qr in result.query_results
            if qr.get("success", True) and qr.get("execution_time_seconds", 0) > 0
        ]
        if exec_times:
            from benchbox.core.results.metrics import TPCMetricsCalculator

            result.power_at_size = TPCMetricsCalculator.calculate_power_at_size(exec_times, self.config.scale_factor)

        result.total_time = total_execution_time
        result.end_time = datetime.now().isoformat()
        success_rate = result.queries_successful / max(result.queries_executed, 1)
        result.success = success_rate >= 0.7

        if self.config.verbose:
            self.logger.info(f"Power Test completed in {total_execution_time:.3f}s")
            self.logger.info(f"Successful queries: {result.queries_successful}/{result.queries_executed}")
            self.logger.info(f"Success rate: {success_rate:.2%}")
            self.logger.info(f"Power@Size: {result.power_at_size:.2f}")

    def _build_query_sequence(self, available_query_ids: list[int]) -> list[tuple]:
        if hasattr(self, "_query_sequence"):
            return [(q, None) if not isinstance(q, tuple) else q for q in self._query_sequence]
        from benchbox.core.tpcds.streams import create_standard_streams

        query_manager = None
        if hasattr(self.benchmark, "query_manager"):
            query_manager = self.benchmark.query_manager
        elif hasattr(self.benchmark, "_impl") and hasattr(self.benchmark._impl, "query_manager"):
            query_manager = self.benchmark._impl.query_manager
        if query_manager is None:
            return [(q, None) for q in available_query_ids]
        stream_id = getattr(self.config, "stream_id", 0)
        stream_manager = create_standard_streams(
            query_manager=query_manager,
            num_streams=1,
            query_ids=available_query_ids if available_query_ids else None,
            query_range=(1, 99),
            base_seed=self.config.seed + stream_id,
        )
        streams = stream_manager.generate_streams()
        stream_queries = streams.get(0, [])
        sequence: list[tuple] = []
        for sq in stream_queries:
            if sq.variant is None:
                sequence.append((sq.query_id, None))
            else:
                try:
                    composite_id = f"{sq.query_id}{sq.variant}"
                    if hasattr(query_manager, "validate_query_id") and query_manager.validate_query_id(composite_id):
                        sequence.append((sq.query_id, sq.variant))
                    else:
                        sequence.append((sq.query_id, None))
                except Exception:
                    sequence.append((sq.query_id, None))
        return sequence

    def _preflight_validate_generation(self, available_query_ids: list[int]) -> None:
        sequence = self._build_query_sequence(available_query_ids)
        failures = []

        stream_param_seed = power_parameter_seed(self.config.seed, self.config.stream_id)

        for position, item in enumerate(sequence):
            query_id, variant = item if isinstance(item, tuple) else (item, None)
            try:
                if variant is not None:
                    _ = self.benchmark.get_query(
                        query_id,
                        seed=stream_param_seed,
                        scale_factor=self.config.scale_factor,
                        variant=variant,
                        dialect=self.target_dialect,
                    )
                else:
                    _ = self.benchmark.get_query(
                        query_id,
                        seed=stream_param_seed,
                        scale_factor=self.config.scale_factor,
                        dialect=self.target_dialect,
                    )
            except Exception as e:
                failures.append(f"{query_id}{variant or ''}: {e}")
        if failures:
            msg = f"TPC-DS PowerTest preflight failed for {len(failures)} queries. Examples: {', '.join(failures[:3])}"
            raise RuntimeError(msg)

    def validate_results(self, result: TPCDSPowerTestResult) -> bool:
        if not result.success:
            return False

        if result.queries_executed > 0:
            success_rate = result.queries_successful / result.queries_executed
            if success_rate < 0.7:
                return False

        return not result.power_at_size <= 0

    def _calculate_power_at_size(self, exec_times: list[float]) -> float:
        if not exec_times:
            return 0.0
        from benchbox.core.results.metrics import TPCMetricsCalculator

        return TPCMetricsCalculator.calculate_power_at_size(exec_times, self.config.scale_factor)

    @property
    def query_sequence(self) -> list:
        if hasattr(self, "_query_sequence"):
            return self._query_sequence
        base_queries = list(range(1, 100))
        variants = ["14a", "14b", "23a", "23b", "24a", "24b", "39a", "39b"]
        return base_queries + variants

    @query_sequence.setter
    def query_sequence(self, value: list):
        self._query_sequence = value

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.test_running,
            "current_query": self.current_query,
            "scale_factor": self.config.scale_factor,
            "seed": self.config.seed,
            "dialect": "standard",
            "query_sequence_length": len(self.query_sequence),
        }

    def _connect_database(self) -> None:
        self.connection = self.connection_factory()

    def _create_connection_from_string(self, connection_string: str, dialect: Optional[str]) -> DatabaseConnection:
        if connection_string in {"sqlite::memory:", ":memory:", "sqlite://:memory:"}:
            return DatabaseConnection(sqlite3.connect(":memory:"), dialect=dialect or "sqlite")

        if connection_string.startswith("sqlite:///"):
            db_path = connection_string.replace("sqlite:///", "", 1)
            return DatabaseConnection(sqlite3.connect(db_path), dialect=dialect or "sqlite")

        raise ValueError(
            "Unsupported connection_string for TPCDSPowerTest. Provide connection_factory for non-SQLite backends."
        )

    def _disconnect_database(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def _warm_up_database(self) -> None:
        if not self.connection:
            return

        warm_up_queries = [
            "SELECT 1",
            "SELECT COUNT(*) FROM (SELECT 1 UNION SELECT 2)",
            "SELECT AVG(x) FROM (SELECT 1 as x UNION SELECT 2 UNION SELECT 3)",
            "SELECT MAX(x), MIN(x) FROM (SELECT 1 as x UNION SELECT 2 UNION SELECT 3)",
            "SELECT x, COUNT(*) FROM (SELECT 1 as x UNION SELECT 1 UNION SELECT 2) GROUP BY x",
        ]

        for query in warm_up_queries:
            try:
                cursor = self.connection.execute(query)
                counter = getattr(cursor, "row_count", None)
                if callable(counter):
                    counter()
                elif hasattr(cursor, "fetchall"):
                    cursor.fetchall()
            except Exception:
                pass

    def _execute_query(self, query_id: Any, query_text: str) -> dict[str, Any]:
        start_time = mono_time()
        result = {
            "query_id": query_id,
            "status": "success",
            "execution_time_seconds": 0.0,
            "result_count": 0,
            "error": None,
        }

        try:
            cursor = self.connection.execute(query_text)
            result["result_count"] = self._query_result_count(cursor)
            self.connection.commit()
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        result["execution_time_seconds"] = elapsed_seconds(start_time)
        return result

    def _validate_results(self, result: TPCDSPowerTestResult) -> bool:
        if not self.config.validation:
            return True

        executed_queries = set()
        for query_result in result.query_results:
            if hasattr(query_result, "get"):
                executed_queries.add(query_result.get("query_id"))
            else:
                executed_queries.add(query_result)

        expected_queries = set(self.query_sequence)
        missing_queries = expected_queries - executed_queries

        if missing_queries:
            result.errors.extend([f"Missing query: {q}" for q in missing_queries])
            return False

        return True

    def _get_database_info(self) -> dict[str, Any]:
        connection_string = ""
        if self.connection:
            connection_string = getattr(self.connection, "connection_string", "sqlite::memory:")

        return {
            "connection_string": connection_string,
            "dialect": "standard",
            "timestamp": datetime.now().isoformat(),
        }

    def export_results(self, result: TPCDSPowerTestResult, output_file: str) -> None:
        import dataclasses
        import json

        result_dict = dataclasses.asdict(result)

        query_results_dict = {}
        for query_result in result.query_results:
            query_result_data: dict[str, Any] | None = None
            if isinstance(query_result, dict):
                query_result_data = query_result
            elif dataclasses.is_dataclass(query_result):
                query_result_data = dataclasses.asdict(query_result)
            elif hasattr(query_result, "__dict__"):
                query_result_data = dict(query_result.__dict__)

            if query_result_data is None:
                continue

            query_id = query_result_data.get("query_id")
            if query_id is not None:
                query_results_dict[str(query_id)] = query_result_data

        result_dict["query_results"] = query_results_dict

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2)

    def compare_results(self, result1: TPCDSPowerTestResult, result2: TPCDSPowerTestResult) -> dict[str, Any]:
        comparison = {
            "power_at_size": {
                "result1": result1.power_at_size,
                "result2": result2.power_at_size,
                "improvement": ((result2.power_at_size - result1.power_at_size) / result1.power_at_size) * 100
                if result1.power_at_size > 0
                else 0,
            },
            "total_time": {
                "result1": result1.total_time,
                "result2": result2.total_time,
                "improvement": ((result1.total_time - result2.total_time) / result1.total_time) * 100
                if result1.total_time > 0
                else 0,
            },
            "query_improvements": {},
        }

        results1_dict = {}
        for qr in result1.query_results:
            if isinstance(qr, dict):
                query_id = qr.get("query_id")
                if query_id is not None:
                    results1_dict[query_id] = qr

        results2_dict = {}
        for qr in result2.query_results:
            if isinstance(qr, dict):
                query_id = qr.get("query_id")
                if query_id is not None:
                    results2_dict[query_id] = qr

        for query_id in set(results1_dict.keys()).intersection(results2_dict.keys()):
            time1 = results1_dict[query_id].get("execution_time_seconds", 0)
            time2 = results2_dict[query_id].get("execution_time_seconds", 0)

            improvement = 0
            if time1 > 0:
                improvement = ((time1 - time2) / time1) * 100

            comparison["query_improvements"][query_id] = {
                "time1": time1,
                "time2": time2,
                "improvement": improvement,
            }

        return comparison


PowerTestResult = TPCDSPowerTestResult
