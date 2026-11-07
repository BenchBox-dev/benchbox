# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from benchbox.core.plan_capture_phase import (
    propagate_query_execution_metadata,
)
from benchbox.core.result_receipt import cursor_result_count
from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.tpch.benchmark import binds_answer_set_parameters, has_answer_set, power_stream_seed
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    applied_counts,
    apply_rf1,
    apply_rf2,
    check_rf1_applied,
    check_rf2_applied,
)
from benchbox.core.validation.query_validation import (
    DATA_REFRESHED_REASON,
    clear_data_refreshed_context,
    clear_reference_seed_context,
    set_data_refreshed_context,
    set_reference_seed_context,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

DATA_STATE_BASE = "base"
DATA_STATE_REFRESHED = "refreshed"


def summarize_power_validation(query_results: list[dict[str, Any]], data_state: str) -> dict[str, Any]:
    validated: list[int] = []
    failed: list[int] = []
    skipped: list[int] = []
    for query_result in query_results:
        evidence = query_result.get("row_count_validation")
        status = evidence.get("status") if isinstance(evidence, dict) else None
        if status == "PASSED":
            validated.append(query_result["query_id"])
        elif status == "FAILED":
            failed.append(query_result["query_id"])
        else:
            skipped.append(query_result["query_id"])
    summary: dict[str, Any] = {
        "data_state": data_state,
        "validated_against": "base" if validated or failed else None,
        "validated_queries": sorted(validated),
        "failed_queries": sorted(failed),
        "skipped_queries": sorted(skipped),
    }
    if data_state == DATA_STATE_REFRESHED:
        summary["skip_reason"] = DATA_REFRESHED_REASON
    return summary


def _parse_tpch_query_id(qid: object) -> int:
    text = str(qid).strip()
    if text[:1] in ("Q", "q"):
        text = text[1:]
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid TPC-H query id: {qid!r} (expected 1-22, optionally Q-prefixed)") from exc


@dataclass
class TPCHPowerTestConfig:
    scale_factor: float = 1.0
    seed: Optional[int] = None
    stream_id: int = 0
    timeout: Optional[float] = None
    warm_up: bool = True
    validation: bool = True
    validation_mode: str = "exact"
    verbose: bool = False
    query_subset: Optional[list[str]] = None
    refresh_functions: bool = False
    refresh_first_set: int = 1


@dataclass
class TPCHPowerTestResult:
    config: TPCHPowerTestConfig
    start_time: str
    end_time: str
    total_time: float
    power_at_size: float
    queries_executed: int
    queries_successful: int
    query_results: list[dict[str, Any]]
    success: bool
    errors: list[str]
    refresh_functions: list[dict[str, Any]] | None = None
    validation: dict[str, Any] | None = None
    validation_runs: list[dict[str, Any]] | None = None

    @property
    def scale_factor(self) -> float:
        return self.config.scale_factor

    def to_dict(self) -> dict[str, Any]:
        result = {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_time": self.total_time,
            "power_at_size": self.power_at_size,
            "queries_executed": self.queries_executed,
            "queries_successful": self.queries_successful,
            "query_results": self.query_results,
            "success": self.success,
            "errors": self.errors,
            "config": {
                "scale_factor": self.config.scale_factor,
                "seed": self.config.seed,
                "stream_id": self.config.stream_id,
                "timeout": self.config.timeout,
                "warm_up": self.config.warm_up,
                "validation": self.config.validation,
                "validation_mode": self.config.validation_mode,
                "refresh_functions": self.config.refresh_functions,
                "refresh_first_set": self.config.refresh_first_set,
            },
        }
        if self.refresh_functions is not None:
            result["refresh_functions"] = self.refresh_functions
        if self.validation is not None:
            result["validation"] = self.validation
        return result


class TPCHPowerTest:
    def __init__(
        self,
        benchmark: Any,
        connection: Any,
        scale_factor: float = 1.0,
        seed: Optional[int] = None,
        stream_id: int = 0,
        dialect: str = "standard",
        verbose: bool = False,
        timeout: Optional[float] = None,
        warm_up: bool = True,
        validation: bool = True,
        validation_mode: Optional[str] = None,
        query_subset: Optional[list[str]] = None,
        refresh_update_set: TPCHRefreshUpdateSet | None = None,
        refresh_connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.benchmark = benchmark
        self.connection = connection
        self.dialect = dialect
        self._pregenerated_queries: dict[int, str] = {}
        self.refresh_update_set = refresh_update_set
        self.refresh_connection_factory = refresh_connection_factory
        if refresh_update_set is not None and refresh_connection_factory is None:
            raise ValueError("TPC-H refresh functions require a separate refresh_connection_factory")

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)

        user_provided_seed = seed is not None
        actual_seed = seed
        actual_validation_mode = validation_mode or "exact"

        if user_provided_seed:
            actual_seed = seed
            if validation and has_answer_set(scale_factor):
                if validation_mode is None:
                    actual_validation_mode = "loose"
                    if verbose:
                        self.logger.warning(
                            f"⚠️  Seed {seed} with validation enabled.\n"
                            "   The answer files hold results for qgen's default parameters (no seed).\n"
                            "   Switching to LOOSE validation (±50% tolerance)."
                        )
                elif validation_mode == "exact":
                    if verbose:
                        self.logger.warning(
                            f"⚠️  Seed {seed} with EXACT validation mode.\n"
                            "   The answer files hold results for qgen's default parameters (no seed).\n"
                            "   Queries whose row count depends on the parameters are checked against\n"
                            "   their allowed range instead of the answer-file count."
                        )
        else:
            actual_seed = None
            if validation_mode is None:
                actual_validation_mode = "exact" if validation else "disabled"
            if verbose:
                self.logger.info("Using qgen default parameters (-d flag) for answer file parity")

        if validation_mode == "disabled":
            validation = False

        if stream_id != 0 and validation and validation_mode is None:
            validation = False
            actual_validation_mode = "disabled"
            self.logger.warning(
                "⚠️  Stream 0 was validated against the answer set; "
                "streams > 0 are run for timing only (per TPC-H spec). "
                f"stream_id={stream_id} therefore runs without answer-set validation."
            )

        if not validation:
            actual_validation_mode = "disabled"

        self.config = TPCHPowerTestConfig(
            scale_factor=scale_factor,
            seed=actual_seed,
            stream_id=stream_id,
            timeout=timeout,
            warm_up=warm_up,
            validation=validation,
            validation_mode=actual_validation_mode,
            verbose=verbose,
            query_subset=query_subset,
            refresh_functions=refresh_update_set is not None,
            refresh_first_set=refresh_update_set.set_id if refresh_update_set is not None else 1,
        )

        self.captured_items: list[tuple[str, str]] = []

    def run(self) -> TPCHPowerTestResult:
        start_time = mono_time()
        start_time_str = datetime.now().isoformat()

        result = TPCHPowerTestResult(
            config=self.config,
            start_time=start_time_str,
            end_time="",
            total_time=0.0,
            power_at_size=0.0,
            queries_executed=0,
            queries_successful=0,
            query_results=[],
            success=True,
            errors=[],
            refresh_functions=[] if self.refresh_update_set is not None else None,
        )

        if self.config.verbose:
            self.logger.info("Starting TPC-H Power Test")
            self.logger.info(f"Scale factor: {self.config.scale_factor}")
            self.logger.info(f"Seed: {self.config.seed}")

        if self.config.query_subset:
            query_permutation = [_parse_tpch_query_id(qid) for qid in self.config.query_subset]
            if self.config.verbose:
                self.logger.info(f"Using user-specified query subset: {query_permutation}")
            self.logger.warning(
                "⚠️  query_subset overrides TPC-H stream permutation - results are NOT TPC-H compliant. "
                "Official TPC-H benchmarks require running all 22 queries in the specified stream order."
            )
        else:
            from benchbox.core.tpch.streams import TPCHStreams

            stream_id = getattr(self.config, "stream_id", 0)
            query_permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)]

            if self.config.verbose:
                self.logger.info(f"Using TPC-H stream {stream_id} permutation: {query_permutation}")

        self._pregenerated_queries = {}
        self._preflight_validate_generation(query_permutation)

        refresh_connection = None
        refresh_applied = False
        refresh_rf2_attempted = False
        try:
            if self.refresh_update_set is not None:
                if self.refresh_connection_factory is None:
                    raise RuntimeError("TPC-H refresh functions require a separate connection factory")
                refresh_connection = self.refresh_connection_factory()
                if refresh_connection is self.connection:
                    raise RuntimeError("TPC-H refresh functions require a separate database session")
                rf1_result = self._execute_refresh_function("RF1", refresh_connection)
                result.refresh_functions.append(rf1_result)
                if rf1_result["status"] != "SUCCESS":
                    result.errors.append(f"RF1 failed: {rf1_result['error']}")
                    result.refresh_functions.append(
                        {
                            "function": "RF2",
                            "update_set": self.refresh_update_set.set_id,
                            "duration_ms": 0,
                            "rows": 0,
                            "status": "SKIPPED",
                            "check_status": "NOT_RUN",
                            "error": "RF1 failed; RF2 was not run",
                        }
                    )
                    raise RuntimeError("TPC-H Power Test refresh RF1 failed")
                refresh_applied = True
            self._run_queries(query_permutation, result, data_refreshed=refresh_applied)

            if refresh_applied:
                refresh_rf2_attempted = True
                self._append_refresh_function_result(result, "RF2", refresh_connection)
            total_execution_time = elapsed_seconds(start_time)
            exec_times = [
                qr["execution_time_seconds"]
                for qr in result.query_results
                if qr.get("success", True) and qr.get("execution_time_seconds", 0) > 0
            ]
            from benchbox.core.results.metrics import TPCMetricsCalculator

            if self.refresh_update_set is None and exec_times:
                result.power_at_size = TPCMetricsCalculator.calculate_power_at_size(
                    exec_times,
                    self.config.scale_factor,
                )
            elif (
                len(exec_times) == 22
                and result.queries_successful == 22
                and result.refresh_functions is not None
                and len(result.refresh_functions) == 2
                and all(
                    function["status"] == "SUCCESS" and function["duration_ms"] >= 0
                    for function in result.refresh_functions
                )
            ):
                refresh_times = [max(0.001, function["duration_ms"] / 1000.0) for function in result.refresh_functions]
                result.power_at_size = TPCMetricsCalculator.calculate_power_at_size(
                    [refresh_times[0], *exec_times, refresh_times[1]],
                    self.config.scale_factor,
                )
            result.total_time = total_execution_time
            result.end_time = datetime.now().isoformat()
            result.success = result.queries_successful == len(query_permutation) and all(
                refresh["status"] == "SUCCESS" for refresh in result.refresh_functions or []
            )

            if self.config.verbose:
                self.logger.info(f"Power Test completed in {total_execution_time:.3f}s")
                self.logger.info(f"Successful queries: {result.queries_successful}/{len(query_permutation)}")
                self.logger.info(f"Power@Size: {result.power_at_size:.2f}")

            return result
        except Exception as e:
            if refresh_applied and not refresh_rf2_attempted:
                refresh_rf2_attempted = True
                self._append_refresh_function_result(result, "RF2", refresh_connection)
            result.total_time = elapsed_seconds(start_time)
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Power Test execution failed: {e}")
            if self.config.verbose:
                self.logger.error(f"Power Test failed: {e}")
            return result

        finally:
            if refresh_connection is not None:
                try:
                    refresh_connection.close()
                except Exception:
                    pass

    def _run_queries(
        self, query_permutation: list[int], result: TPCHPowerTestResult, *, data_refreshed: bool = False
    ) -> None:
        for position, query_id in enumerate(query_permutation):
            query_start: float | None = None
            query_result = {
                "query_id": query_id,
                "position": position + 1,
                "stream_id": self.config.stream_id,
                "execution_time_seconds": 0.0,
                "success": False,
                "error": None,
                "result_count": 0,
            }

            try:
                if self.config.verbose:
                    self.logger.info(f"Executing Query {query_id} (position {position + 1}/{len(query_permutation)})")

                stream_seed = power_stream_seed(self.config.seed, self.config.stream_id)
                query_text = self._pregenerated_queries.get(query_id)
                if query_text is None:
                    query_text = self._generate_query(query_id, stream_seed)

                label = f"Position_{position + 1}_Query_{query_id}"
                query_start = mono_time()
                conversion_clock = ConversionClock().start()
                try:
                    if hasattr(self.connection, "set_query_context"):
                        self.connection.set_query_context(query_id, stream_id=self.config.stream_id)

                    set_reference_seed_context(binds_answer_set_parameters(stream_seed))
                    set_data_refreshed_context(data_refreshed)

                    cursor = self.connection.execute(query_text)

                    if hasattr(cursor, "platform_result"):
                        result_dict = cursor.platform_result
                        if isinstance(result_dict.get("row_count_validation"), dict):
                            query_result["row_count_validation"] = result_dict["row_count_validation"]
                        if result_dict.get("status") == "FAILED":
                            error_msg = result_dict.get(
                                "error", result_dict.get("row_count_validation_error", "Query validation failed")
                            )
                            raise RuntimeError(error_msg)
                        propagate_query_execution_metadata(result_dict, query_result)

                    result_count = cursor_result_count(cursor)

                    if hasattr(self.connection, "commit"):
                        self.connection.commit()
                finally:
                    clear_reference_seed_context()
                    clear_data_refreshed_context()
                    self.captured_items.append((label, query_text))

                execution_time = max(0.0, elapsed_seconds(query_start) - conversion_clock.finish())

                query_result.update(
                    {
                        "execution_time_seconds": execution_time,
                        "success": True,
                        "result_count": result_count,
                    }
                )

                query_result["result_digest"] = self._result_digest_from_cursor(cursor)

                result.queries_successful += 1

                if self.config.verbose:
                    self.logger.info(f"Query {query_id} completed in {execution_time:.3f}s")

            except Exception as e:
                execution_time = elapsed_seconds(query_start) if query_start is not None else 0.0
                query_result.update(
                    {
                        "execution_time_seconds": execution_time,
                        "success": False,
                        "error": str(e),
                    }
                )

                result.errors.append(f"Query {query_id} failed: {e}")

                if self.config.verbose:
                    self.logger.error(f"Query {query_id} failed: {e}")

            result.query_results.append(query_result)
            result.queries_executed += 1

        if self.config.validation:
            result.validation = summarize_power_validation(
                result.query_results,
                DATA_STATE_REFRESHED if data_refreshed else DATA_STATE_BASE,
            )

    def _append_refresh_function_result(
        self,
        result: TPCHPowerTestResult,
        function: str,
        connection: Any,
    ) -> None:
        function_result = self._execute_refresh_function(function, connection)
        if result.refresh_functions is None:
            result.refresh_functions = []
        result.refresh_functions.append(function_result)
        if function_result["status"] != "SUCCESS":
            result.errors.append(f"{function} failed: {function_result['error']}")

    def _execute_refresh_function(self, function: str, connection: Any) -> dict[str, Any]:
        if function not in {"RF1", "RF2"}:
            raise ValueError(f"Unsupported TPC-H refresh function: {function}")

        counts: dict[str, int] = {"rows": 0}
        status = "FAILED"
        check_status = "NOT_RUN"
        error = None
        duration_ms = 0
        try:
            if self.refresh_update_set is None or connection is None:
                raise RuntimeError("TPC-H refresh functions require an update set and a separate session")
            start_time = mono_time()
            if function == "RF1":
                applied = apply_rf1(connection, self.refresh_update_set)
            else:
                applied = apply_rf2(connection, self.refresh_update_set)
            duration_ms = max(1, round(elapsed_seconds(start_time) * 1000))
            counts = applied_counts(applied)
            check_status = "FAILED"
            if function == "RF1":
                check_rf1_applied(connection, self.refresh_update_set, counts["rows_inserted"])
            else:
                check_rf2_applied(connection, self.refresh_update_set, counts["orders_deleted"])
            check_status = "PASSED"
            status = "SUCCESS"
        except Exception as exc:
            error = str(exc)

        return {
            "function": function,
            "update_set": self.refresh_update_set.set_id if self.refresh_update_set is not None else None,
            "duration_ms": duration_ms if status == "SUCCESS" else 0,
            **counts,
            "status": status,
            "check_status": check_status,
            "error": error,
        }

    @staticmethod
    def _result_digest_from_cursor(cursor: Any) -> str | None:
        platform_result = getattr(cursor, "platform_result", None)
        if isinstance(platform_result, dict):
            digest = platform_result.get("result_digest")
            if digest is not None:
                return str(digest)
        return None

    def get_all_queries(self) -> dict[str, str]:
        from benchbox.core.tpch.streams import TPCHStreams

        stream_id = getattr(self.config, "stream_id", 0)
        query_permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)]

        queries = {}
        stream_seed = power_stream_seed(self.config.seed, self.config.stream_id)
        for position, query_id in enumerate(query_permutation):
            try:
                query_text = self.benchmark.get_query(
                    query_id,
                    seed=stream_seed,
                    stream_id=self.config.stream_id,
                    scale_factor=self.config.scale_factor,
                    dialect=self.dialect,
                )
                queries[f"Position_{position + 1}_Query_{query_id}"] = query_text
            except Exception as e:
                self.logger.error(f"Failed to get query {query_id}: {e}")
        return queries

    def _generate_query(self, query_id: int, stream_seed: int) -> str:
        return self.benchmark.get_query(
            query_id,
            seed=stream_seed,
            stream_id=self.config.stream_id,
            scale_factor=self.config.scale_factor,
            dialect=self.dialect,
        )

    def _preflight_validate_generation(self, query_permutation: list[int]) -> None:
        failures = []
        self._pregenerated_queries = {}
        stream_seed = power_stream_seed(self.config.seed, self.config.stream_id)
        for position, query_id in enumerate(query_permutation):
            try:
                self._pregenerated_queries[query_id] = self._generate_query(query_id, stream_seed)
            except Exception as e:
                failures.append(f"{query_id}: {e}")
        if failures:
            msg = f"TPC-H PowerTest preflight failed for {len(failures)} queries. Examples: {', '.join(failures[:3])}"
            raise RuntimeError(msg)

    def validate_results(self, result: TPCHPowerTestResult) -> bool:
        if not result.success:
            return False

        if result.queries_successful != 22:
            return False

        if result.power_at_size <= 0:
            return False

        return True
