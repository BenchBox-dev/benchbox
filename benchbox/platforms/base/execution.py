from __future__ import annotations

import contextlib
import dataclasses
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from benchbox.core.constants import (
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
)
from benchbox.core.errors import PlanCaptureError
from benchbox.core.operations import OperationExecutor
from benchbox.core.plan_capture_phase import (
    propagate_query_execution_metadata,
)
from benchbox.core.power_harnesses import (
    resolve_combined_harness,
    resolve_maintenance_harness,
    resolve_power_harness,
    resolve_throughput_harness,
)
from benchbox.core.results.builder import benchmark_family, normalize_benchmark_id
from benchbox.core.results.models import QUERY_RUN_TYPE_MEASUREMENT, QUERY_RUN_TYPE_WARMUP
from benchbox.core.schemas import MIN_THROUGHPUT_STREAMS
from benchbox.core.throughput.containment import await_quiescence, check_phase_boundary
from benchbox.core.throughput.entrypoints import (
    finalize_throughput_metrics,
    require_stream_minimum,
    warn_legacy_throughput_api,
)
from benchbox.core.tpch.platform_power import _power_query_result, _power_test_error_result
from benchbox.platforms.base.connection_wrappers import (
    PlatformAdapterConnection,
    _make_stream_cursor,
    open_stream_connection,
    require_throughput_stream_capability,
)
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import SQLTranslationError, UnknownSqlDialectError
from benchbox.utils.printing import quiet_console

__all__ = ["TestDriversMixin", "_power_query_result", "_power_test_error_result"]


def _resolve_requested_stream_count(run_config: dict, default: int = MIN_THROUGHPUT_STREAMS) -> int:
    for key in ("num_streams", "stream_count", "streams"):
        value = run_config.get(key)
        if value is not None:
            return require_stream_minimum(int(value), key)
    value = run_config.get("concurrent_streams")
    if value is None:
        return default
    return require_stream_minimum(int(value), "concurrent_streams")


def _throughput_benchmark_id(benchmark: Any) -> str:
    name = getattr(benchmark, "benchmark_name", None)
    return normalize_benchmark_id(name) if isinstance(name, str) else ""


def _legacy_throughput_run_config(benchmark: Any, benchmark_id: str, options: dict[str, Any]) -> dict[str, Any]:
    run_config = dict(options)
    for legacy, current in (("base_seed", "seed"), ("stream_timeout", "stream_timeout_seconds")):
        if legacy in run_config:
            run_config.setdefault(current, run_config.pop(legacy))
    if run_config.pop("enable_validation", True) is False:
        run_config.setdefault("validation_mode", "disabled")
    run_config.setdefault("scale_factor", getattr(benchmark, "scale_factor", 1.0))
    run_config["benchmark_name"] = benchmark_id
    return run_config


def _format_throughput_metric(result: Any) -> str:
    if result.throughput_at_size is None:
        return "Throughput@Size not reported (query_subset runs are not TPC-compliant)"
    return f"Throughput@Size = {result.throughput_at_size:.2f}"


def _describe_stream_timeout(cfg: Any, run_config: dict) -> str:
    timeout = "no timeout" if cfg.stream_timeout == 0 else f"{cfg.stream_timeout}s"
    if run_config.get("stream_timeout_seconds") is None:
        source = "benchmark default"
    else:
        source = run_config.get("stream_timeout_source") or "run option"
    return f"Stream timeout: {timeout} ({source})"


def _throughput_config_options(run_config: dict) -> dict[str, Any]:
    options: dict[str, Any] = {
        "verbose": run_config.get("verbose", False),
        "cancel_on_timeout": bool(run_config.get("cancel_on_timeout", False)),
    }
    timeout = run_config.get("stream_timeout_seconds")
    if timeout is not None:
        if int(timeout) < 0:
            raise ValueError(f"stream_timeout_seconds must be >= 0 (0 disables the timeout); got {timeout}")
        options["stream_timeout"] = int(timeout)
    query_subset = run_config.get("query_subset")
    if query_subset:
        options["query_subset"] = [str(query_id) for query_id in query_subset]
    seed = run_config.get("seed")
    if seed is not None:
        options["base_seed"] = int(seed)
    return options


class _CapturedPlan(NamedTuple):
    plan: Any
    fingerprint: str | None
    capture_ms: float | None
    normalized_fingerprint: str | None = None


class TestDriversMixin:
    def _make_power_connection_adapter(self, connection: Any, benchmark_id: str, scale_factor: float):
        connection_adapter = PlatformAdapterConnection(_make_stream_cursor(connection), self)
        connection_adapter.benchmark_type = benchmark_id
        connection_adapter.scale_factor = scale_factor
        return connection_adapter

    def _make_direct_power_connection_adapter(self, connection: Any, benchmark_id: str, scale_factor: float):
        connection_adapter = PlatformAdapterConnection(connection, self)
        connection_adapter.benchmark_type = benchmark_id
        connection_adapter.scale_factor = scale_factor
        return connection_adapter

    def _make_tpch_refresh_connection_adapter(self, connection: Any, scale_factor: float):
        stream_connection = open_stream_connection(self, connection, "olap")
        if stream_connection is connection:
            raise RuntimeError("TPC-H refresh functions require a separate database session")
        connection_adapter = PlatformAdapterConnection(stream_connection, self, maintenance_mode=True)
        connection_adapter.benchmark_type = "tpch"
        connection_adapter.scale_factor = scale_factor
        return connection_adapter

    def _execute_tpch_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        from benchbox.core.tpch.platform_power import execute_tpch_power_test

        return execute_tpch_power_test(
            self,
            benchmark,
            connection,
            run_config,
            make_connection_adapter=self._make_direct_power_connection_adapter,
            make_refresh_connection_adapter=self._make_tpch_refresh_connection_adapter,
            console=quiet_console,
        )

    def _execute_generic_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        console = quiet_console

        iterations = run_config.get("iterations", GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS)
        warm_up_iterations = run_config.get("warm_up_iterations", GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS)
        fail_fast = run_config.get("power_fail_fast", False)
        run_config.get("verbose", False)

        benchmark_name = run_config.get("benchmark_name", "")
        scale_factor = run_config.get("scale_factor", getattr(benchmark, "scale_factor", 1.0))

        console.print(f"[green]Running {benchmark_name} Power Test (Scale Factor: {scale_factor})[/green]")
        console.print(f"[green]Warm-up runs: {warm_up_iterations}, Measurement runs: {iterations}[/green]")

        all_measurement_results = []

        for i in range(warm_up_iterations):
            console.print(f"[cyan]--- Warm-up Run {i + 1}/{warm_up_iterations} ---[/cyan]")
            warmup_results = self._execute_all_queries(benchmark, connection, run_config)
            for result in warmup_results:
                result["iteration"] = 0
                result["stream_id"] = 0
                result["run_type"] = "warmup"
                all_measurement_results.append(result)

        for i in range(iterations):
            console.print(f"[cyan]--- Measurement Run {i + 1}/{iterations} ---[/cyan]")
            iteration_results = self._execute_all_queries(benchmark, connection, run_config)

            for result in iteration_results:
                result["iteration"] = i + 1
                result["stream_id"] = i + 1
                result["run_type"] = "measurement"

            all_measurement_results.extend(iteration_results)

            successful = sum(1 for r in iteration_results if r.get("status") == "SUCCESS")
            if iteration_results and successful == 0:
                console.print("[yellow]⚠️  All queries failed - aborting remaining measurement runs[/yellow]")
                break
            if fail_fast and successful < len(iteration_results):
                console.print(
                    "[yellow]⚠️  Query failures detected (fail_fast enabled) - aborting remaining runs[/yellow]"
                )
                break

        if all_measurement_results:
            total_queries = len([r for r in all_measurement_results if r.get("iteration") == 1])
            successful = len([r for r in all_measurement_results if r.get("status") == "SUCCESS"])
            total_exec = len(all_measurement_results)
            success_rate = (successful / total_exec * 100) if total_exec > 0 else 0

            console.print("[green]✅ Power Test completed[/green]")
            console.print(f"  Queries: {total_queries}, Iterations: {iterations}")
            console.print(f"  Total executions: {total_exec}, Successful: {successful}")
            console.print(f"  Success rate: {success_rate:.1f}%")

        return all_measurement_results

    def _execute_tpcds_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        from benchbox.core.tpcds.platform_power import execute_tpcds_power_test

        return execute_tpcds_power_test(
            self,
            benchmark,
            connection,
            run_config,
            make_connection_adapter=self._make_power_connection_adapter,
            console=quiet_console,
        )

    def _execute_tpcds_throughput_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        from benchbox.core.expected_results.tpcds_results import parse_validation_mode, set_config_validation_mode
        from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig

        console = quiet_console

        try:
            scale_factor = run_config.get("scale_factor", 1.0)
            validation_mode = run_config.get("validation_mode")
            num_streams = _resolve_requested_stream_count(run_config)
            verbose = run_config.get("verbose", False)

            run_validation_mode = parse_validation_mode(validation_mode)
            set_config_validation_mode(validation_mode)

            console.print(
                f"[green]Running TPC-DS Throughput Test (Scale Factor: {scale_factor}, Streams: {num_streams})[/green]"
            )

            require_throughput_stream_capability(self, platform_name=self.platform_name, connection=connection)
            benchmark_type = run_config.get("benchmark_type", "olap")

            def connection_factory():
                stream_connection = open_stream_connection(self, connection, benchmark_type)
                conn_wrapper = PlatformAdapterConnection(
                    stream_connection,
                    self,
                    validation_mode=run_validation_mode,
                )
                conn_wrapper.benchmark_type = "tpcds"
                conn_wrapper.scale_factor = scale_factor
                return conn_wrapper

            throughput_test = TPCDSThroughputTest(
                benchmark=benchmark,
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                num_streams=num_streams,
                verbose=verbose,
                dialect=self.get_target_dialect(),
            )

            cfg = TPCDSThroughputTestConfig(
                scale_factor=scale_factor,
                num_streams=num_streams,
                **_throughput_config_options(run_config),
            )
            console.print(f"[dim]{_describe_stream_timeout(cfg, run_config)}[/dim]")
            throughput_test_result = throughput_test.run(config=cfg)

            finalize_throughput_metrics(throughput_test_result, num_streams, cfg.query_subset)

            if self.very_verbose:
                with contextlib.suppress(Exception):
                    console.print(
                        f"[dim]Target dialect: {getattr(self, 'get_target_dialect', lambda: 'standard')()} | Detailed per-query results:[/dim]"
                    )
                for stream_result in throughput_test_result.stream_results:
                    for qr in stream_result.query_results:
                        qname = f"q{qr.get('query_id')}"
                        dur = qr.get("execution_time_seconds", 0.0)
                        status = "SUCCESS" if qr.get("success") else "FAILED"
                        rows = qr.get("result_count", 0)
                        console.print(
                            f"  • {qname} [stream {stream_result.stream_id}]: {dur:.2f}s, {status}, rows={rows}"
                        )

            self._last_throughput_test_result = throughput_test_result

            if throughput_test_result.success:
                console.print(
                    f"[green]✅ TPC-DS Throughput Test completed: {_format_throughput_metric(throughput_test_result)}[/green]"
                )
                console.print(
                    f"  Streams executed: {throughput_test_result.streams_executed}, Successful: {throughput_test_result.streams_successful}"
                )
                console.print(f"  Total execution time: {throughput_test_result.total_time:.2f}s")

                for stream_result in throughput_test_result.stream_results:
                    success_rate = stream_result.queries_successful / max(stream_result.queries_executed, 1)
                    console.print(
                        f"  Stream {stream_result.stream_id}: {stream_result.queries_successful}/{stream_result.queries_executed} queries ({success_rate:.1%})"
                    )

            else:
                console.print("[red]❌ TPC-DS Throughput Test failed[/red]")
                for error in throughput_test_result.errors:
                    console.print(f"  Error: {error}")

            query_results = []
            for stream_result in throughput_test_result.stream_results:
                for query_result in stream_result.query_results:
                    platform_result = {
                        "query_id": query_result["query_id"],
                        "execution_time_seconds": query_result["execution_time_seconds"],
                        "status": "SUCCESS" if query_result["success"] else "FAILED",
                        "rows_returned": query_result.get("result_count", 0),
                        "test_type": "throughput",
                        "stream_id": stream_result.stream_id,
                    }
                    if not query_result["success"]:
                        platform_result["error"] = query_result.get("error", "Unknown error")
                    propagate_query_execution_metadata(query_result, platform_result)
                    query_results.append(platform_result)

            return query_results

        except Exception as e:
            console.print(f"[red]❌ TPC-DS Throughput Test failed: {e}[/red]")
            self._last_throughput_test_result = None
            return [
                {
                    "query_id": "throughput_test_error",
                    "execution_time_seconds": 0.0,
                    "status": "FAILED",
                    "rows_returned": 0,
                    "error": str(e),
                    "test_type": "throughput",
                }
            ]

    def _execute_tpch_throughput_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        from benchbox.core.expected_results.models import ValidationMode
        from benchbox.core.expected_results.tpcds_results import parse_validation_mode
        from benchbox.core.tpch.throughput_test import (
            TPCHThroughputTest,
            TPCHThroughputTestConfig,
        )

        console = quiet_console

        try:
            scale_factor = run_config.get("scale_factor", 1.0)
            num_streams = _resolve_requested_stream_count(run_config)
            verbose = run_config.get("verbose", False)

            console.print(
                f"[green]Running TPC-H Throughput Test (Scale Factor: {scale_factor}, Streams: {num_streams})[/green]"
            )

            require_throughput_stream_capability(self, platform_name=self.platform_name, connection=connection)
            benchmark_type = run_config.get("benchmark_type", "olap")

            validate_row_counts = parse_validation_mode(run_config.get("validation_mode")) is not ValidationMode.SKIP

            def connection_factory():
                stream_connection = open_stream_connection(self, connection, benchmark_type)
                conn_wrapper = PlatformAdapterConnection(stream_connection, self)
                conn_wrapper._validate_row_count = validate_row_counts
                conn_wrapper.benchmark_type = "tpch"
                conn_wrapper.scale_factor = scale_factor
                return conn_wrapper

            refresh_enabled = (
                run_config.get("tpch_refresh_functions", getattr(benchmark, "tpch_refresh_functions", False)) is True
            )
            refresh_connection_factory = None
            if refresh_enabled:
                from benchbox.core.tpch.refresh import require_refresh_support

                require_refresh_support(self)

                def refresh_connection_factory():
                    stream_connection = open_stream_connection(self, connection, benchmark_type)
                    if stream_connection is connection:
                        raise RuntimeError("TPC-H refresh functions require a separate database session")
                    connection_wrapper = PlatformAdapterConnection(stream_connection, self, maintenance_mode=True)
                    connection_wrapper.benchmark_type = "tpch"
                    connection_wrapper.scale_factor = scale_factor
                    return connection_wrapper

            throughput_test = TPCHThroughputTest(
                benchmark=benchmark,
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                num_streams=num_streams,
                verbose=verbose,
                dialect=self.get_target_dialect(),
                refresh_enabled=refresh_enabled,
                refresh_first_set=run_config.get(
                    "tpch_refresh_first_set", getattr(benchmark, "tpch_refresh_first_set", 1)
                ),
                refresh_connection_factory=refresh_connection_factory,
            )

            cfg = TPCHThroughputTestConfig(
                scale_factor=scale_factor,
                num_streams=num_streams,
                refresh_functions=refresh_enabled,
                refresh_first_set=run_config.get(
                    "tpch_refresh_first_set", getattr(benchmark, "tpch_refresh_first_set", 1)
                ),
                **_throughput_config_options(run_config),
            )
            console.print(f"[dim]{_describe_stream_timeout(cfg, run_config)}[/dim]")
            throughput_test_result = throughput_test.run(config=cfg)

            finalize_throughput_metrics(throughput_test_result, num_streams, cfg.query_subset)
            self._last_throughput_test_result = throughput_test_result

            if throughput_test_result.success:
                console.print(
                    f"[green]✅ TPC-H Throughput Test completed: {_format_throughput_metric(throughput_test_result)}[/green]"
                )
                console.print(
                    f"  Streams executed: {throughput_test_result.streams_executed}, Successful: {throughput_test_result.streams_successful}"
                )
                console.print(f"  Total execution time: {throughput_test_result.total_time:.2f}s")
            else:
                console.print("[red]❌ TPC-H Throughput Test failed[/red]")
                for error in throughput_test_result.errors:
                    console.print(f"  Error: {error}")

            query_results = []
            for stream_result in throughput_test_result.stream_results:
                for qr in stream_result.query_results:
                    platform_result = {
                        "query_id": qr.get("query_id"),
                        "execution_time_seconds": qr.get("execution_time_seconds", 0.0),
                        "status": "SUCCESS" if qr.get("success") else "FAILED",
                        "rows_returned": qr.get("result_count", 0),
                        "test_type": "throughput",
                        "stream_id": stream_result.stream_id,
                    }
                    if not qr.get("success"):
                        platform_result["error"] = qr.get("error", "Unknown error")
                    propagate_query_execution_metadata(qr, platform_result)
                    query_results.append(platform_result)

            return query_results

        except Exception as e:
            console.print(f"[red]❌ TPC-H Throughput Test failed: {e}[/red]")
            self._last_throughput_test_result = None
            return [
                {
                    "query_id": "throughput_test_error",
                    "execution_time_seconds": 0.0,
                    "status": "FAILED",
                    "rows_returned": 0,
                    "error": str(e),
                    "test_type": "throughput",
                }
            ]

    def _execute_tpcds_maintenance_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:

        from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest

        self._plan_capture_checkpoint(connection)

        console = quiet_console

        try:
            scale_factor = run_config.get("scale_factor", 1.0)
            verbose = run_config.get("verbose", False)
            output_dir = run_config.get("output_dir", Path.cwd() / "tpcds_maintenance_test")

            console.print(f"[green]Running TPC-DS Maintenance Test (Scale Factor: {scale_factor})[/green]")

            benchmark_type = run_config.get("benchmark_type", "olap")

            def connection_factory():
                stream_connection = open_stream_connection(self, connection, benchmark_type)
                conn_wrapper = PlatformAdapterConnection(stream_connection, self)
                conn_wrapper.benchmark_type = "tpcds"
                conn_wrapper.scale_factor = scale_factor
                return conn_wrapper

            maintenance_test = TPCDSMaintenanceTest(
                benchmark=benchmark,
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                output_dir=Path(output_dir) if isinstance(output_dir, str) else output_dir,
                verbose=verbose,
                dialect=self.get_target_dialect(),
            )

            maintenance_test_result = maintenance_test.run()

            if maintenance_test_result["success"]:
                console.print("[green]✅ TPC-DS Maintenance Test completed[/green]")
                console.print(f"  Insert operations: {maintenance_test_result['insert_operations']}")
                console.print(f"  Update operations: {maintenance_test_result['update_operations']}")
                console.print(f"  Delete operations: {maintenance_test_result['delete_operations']}")
                console.print(
                    f"  Total operations: {maintenance_test_result['total_operations']}, Successful: {maintenance_test_result['successful_operations']}"
                )
                console.print(f"  Total execution time: {maintenance_test_result['total_time']:.2f}s")
                console.print(f"  Throughput: {maintenance_test_result['overall_throughput']:.2f} ops/sec")

            else:
                console.print("[red]❌ TPC-DS Maintenance Test failed[/red]")
                for error in maintenance_test_result["errors"]:
                    console.print(f"  Error: {error}")

            query_results = []
            for operation in maintenance_test_result["operations"]:
                platform_result = {
                    "query_id": f"{operation.operation_type.lower()}_{operation.table_name}",
                    "execution_time_seconds": operation.duration,
                    "status": "SUCCESS" if operation.success else "FAILED",
                    "rows_returned": operation.rows_affected,
                    "test_type": "maintenance",
                    "operation_type": operation.operation_type,
                    "table_name": operation.table_name,
                }
                if not operation.success:
                    platform_result["error"] = operation.error or "Unknown error"
                query_results.append(platform_result)

            return query_results

        except Exception as e:
            console.print(f"[red]❌ TPC-DS Maintenance Test failed: {e}[/red]")
            return [
                {
                    "query_id": "maintenance_test_error",
                    "execution_time_seconds": 0.0,
                    "status": "FAILED",
                    "rows_returned": 0,
                    "error": str(e),
                    "test_type": "maintenance",
                }
            ]

    def _execute_tpch_maintenance_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:

        from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
        from benchbox.core.tpch.refresh import refresh_unsupported_reason

        console = quiet_console

        unsupported_reason = refresh_unsupported_reason(self)
        if unsupported_reason is not None:
            console.print(f"[yellow]TPC-H Maintenance Test skipped: {unsupported_reason}[/yellow]")
            return [
                {
                    "query_id": "maintenance_not_supported",
                    "execution_time_seconds": 0.0,
                    "status": "SKIPPED",
                    "rows_returned": 0,
                    "error": unsupported_reason,
                    "test_type": "maintenance",
                }
            ]

        self._plan_capture_checkpoint(connection)

        try:
            scale_factor = run_config.get("scale_factor", 1.0)
            verbose = run_config.get("verbose", False)
            maintenance_pairs = run_config.get("maintenance_pairs", 1)
            rf1_interval = run_config.get("rf1_interval", 0.0)
            rf2_interval = run_config.get("rf2_interval", 0.0)
            validate_integrity = run_config.get("validate_integrity", True)
            first_set = run_config.get("tpch_refresh_first_set", getattr(benchmark, "tpch_refresh_first_set", 1))
            output_dir = run_config.get("output_dir", Path.cwd() / "tpch_maintenance_test")

            console.print(f"[green]Running TPC-H Maintenance Test (Scale Factor: {scale_factor})[/green]")

            benchmark_type = run_config.get("benchmark_type", "olap")

            def connection_factory():
                stream_connection = open_stream_connection(self, connection, benchmark_type)
                conn_wrapper = PlatformAdapterConnection(stream_connection, self, maintenance_mode=True)
                conn_wrapper.benchmark_type = "tpch"
                conn_wrapper.scale_factor = scale_factor
                return conn_wrapper

            maintenance_test = TPCHMaintenanceTest(
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                output_dir=Path(output_dir) if isinstance(output_dir, str) else output_dir,
                verbose=verbose,
                first_set=first_set,
            )

            result = maintenance_test.run_maintenance_test(
                maintenance_pairs=maintenance_pairs,
                concurrent_with_queries=False,
                rf1_interval=rf1_interval,
                rf2_interval=rf2_interval,
                validate_integrity=validate_integrity,
            )

            if result.success:
                console.print("[green]✅ TPC-H Maintenance Test completed[/green]")
                console.print(
                    f"  Operations: {result.total_operations}, Successful: {result.successful_operations}, Failed: {result.failed_operations}"
                )
                console.print(
                    f"  Total time: {result.total_time:.2f}s, Overall throughput: {result.overall_throughput:.2f} ops/s"
                )
            else:
                console.print("[red]❌ TPC-H Maintenance Test failed[/red]")
                for err in result.errors:
                    console.print(f"  Error: {err}")

            query_results = []
            for op in result.operations:
                query_results.append(
                    {
                        "query_id": op.operation_type,
                        "execution_time_seconds": op.duration,
                        "status": "SUCCESS" if op.success else "FAILED",
                        "rows_returned": op.rows_affected,
                        "test_type": "maintenance",
                        **({} if op.success else {"error": getattr(op, "error", None) or "Unknown error"}),
                    }
                )

            return query_results

        except Exception as e:
            console.print(f"[red]❌ TPC-H Maintenance Test failed: {e}[/red]")
            return [
                {
                    "query_id": "maintenance_test_error",
                    "execution_time_seconds": 0.0,
                    "status": "FAILED",
                    "rows_returned": 0,
                    "error": str(e),
                    "test_type": "maintenance",
                }
            ]

    def _execute_queries_by_type(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        phase_eligible = (
            bool(getattr(self, "capture_plans", False))
            and getattr(self, "plan_capture_phase_eligible", False)
            and not getattr(self, "dry_run_mode", False)
        )
        self._last_power_workload_timing = None
        if phase_eligible:
            self._plan_capture_phase_active = True
            self._phase_recorded_queries = {}
            self._captured_plans = {}
        workload_start_time = datetime.now().isoformat()
        workload_start = mono_time()
        try:
            results = self._dispatch_queries_by_type(benchmark, connection, run_config)
        finally:
            if phase_eligible:
                self._plan_capture_phase_active = False
        workload_duration_ms = int(elapsed_seconds(workload_start) * 1000)
        workload_end_time = datetime.now().isoformat()
        effective_type = run_config.get("_effective_execution_type") or run_config.get(
            "test_execution_type", "standard"
        )
        if effective_type in {"standard", "power"} and not self.is_dry_run:
            self._last_power_workload_timing = (workload_start_time, workload_end_time, workload_duration_ms)

        if not self._contain_outstanding_throughput_work(run_config):
            return results
        if phase_eligible:
            self._capture_plans_post_measurement(connection, dict(self._phase_recorded_queries), results)
        return results

    def _contain_outstanding_throughput_work(self, run_config: dict[str, Any]) -> bool:
        result = getattr(self, "_last_throughput_test_result", None)
        if not getattr(result, "outstanding_stream_ids", None):
            return True

        try:
            cleanup_timeout = float(run_config.get("stream_cleanup_timeout_seconds", 5.0))
        except (TypeError, ValueError):
            cleanup_timeout = 5.0
        cleanup_timeout = max(0.0, cleanup_timeout)

        if await_quiescence(result, timeout=cleanup_timeout):
            return True

        self._post_measurement_contained = True
        self._contained_throughput_result = result
        return False

    def _dispatch_queries_by_type(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        test_execution_type = run_config.get("test_execution_type", "standard")

        if test_execution_type == "power":
            return self._ensure_query_results_run_type(self._execute_power_test(benchmark, connection, run_config))
        elif test_execution_type == "throughput":
            return self._ensure_query_results_run_type(self._execute_throughput_test(benchmark, connection, run_config))
        elif test_execution_type == "maintenance":
            return self._ensure_query_results_run_type(
                self._execute_maintenance_test(benchmark, connection, run_config)
            )
        elif test_execution_type == "combined":
            return self._ensure_query_results_run_type(self._execute_combined_test(benchmark, connection, run_config))
        else:
            return self._ensure_query_results_run_type(self._execute_all_queries(benchmark, connection, run_config))

    @staticmethod
    def _infer_query_result_run_type(result: dict[str, Any]) -> str:
        explicit_run_type = result.get("run_type")
        if explicit_run_type:
            return str(explicit_run_type)

        if result.get("is_warmup"):
            return QUERY_RUN_TYPE_WARMUP

        iteration = result.get("iteration")
        if iteration is not None:
            with contextlib.suppress(TypeError, ValueError):
                if int(iteration) == 0:
                    return QUERY_RUN_TYPE_WARMUP

        return QUERY_RUN_TYPE_MEASUREMENT

    @staticmethod
    def _ensure_query_results_run_type(query_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for result in query_results:
            if isinstance(result, dict) and ("run_type" not in result or not result.get("run_type")):
                result["run_type"] = TestDriversMixin._infer_query_result_run_type(result)
        return query_results

    @staticmethod
    def _resolve_benchmark_slug(benchmark, run_config: dict) -> str:
        return run_config.get("benchmark_name") or ""

    def _execute_power_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        benchmark_name = self._resolve_benchmark_slug(benchmark, run_config)
        benchmark_id = normalize_benchmark_id(benchmark_name)

        harness = resolve_power_harness(benchmark_id)
        if harness is not None:
            return getattr(self, harness.adapter_method)(benchmark, connection, run_config)
        return self._execute_generic_power_test(benchmark, connection, run_config)

    def _execute_throughput_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        console = quiet_console

        benchmark_name = self._resolve_benchmark_slug(benchmark, run_config)
        benchmark_id = normalize_benchmark_id(benchmark_name)

        harness = resolve_throughput_harness(benchmark_id)
        if harness is not None:
            return getattr(self, harness.adapter_method)(benchmark, connection, run_config)
        console.print(f"[yellow]⚠️ Throughput test not supported for benchmark: {benchmark_name}[/yellow]")
        console.print("[yellow]  Falling back to standard query execution[/yellow]")
        run_config["_effective_execution_type"] = "power"
        return self._execute_all_queries(benchmark, connection, run_config)

    def _execute_maintenance_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        console = quiet_console

        benchmark_name = self._resolve_benchmark_slug(benchmark, run_config)
        benchmark_id = normalize_benchmark_id(benchmark_name)

        harness = resolve_maintenance_harness(benchmark_id)
        if harness is not None:
            return getattr(self, harness.adapter_method)(benchmark, connection, run_config)
        console.print(f"[yellow]⚠️ Maintenance test not supported for benchmark: {benchmark_name}[/yellow]")
        console.print("[yellow]  Falling back to standard query execution[/yellow]")
        run_config["_effective_execution_type"] = "power"
        return self._execute_all_queries(benchmark, connection, run_config)

    def _execute_combined_test(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        console = quiet_console
        requested_phases = set((run_config.get("options") or {}).get("requested_phases") or [])
        if not requested_phases:
            requested_phases = {"power", "throughput", "maintenance"}

        benchmark_name = self._resolve_benchmark_slug(benchmark, run_config)
        benchmark_id = normalize_benchmark_id(benchmark_name)

        combined = resolve_combined_harness(benchmark_id)
        if combined is None:
            console.print(f"[yellow]⚠️ Combined test not supported for benchmark: {benchmark_name}[/yellow]")
            console.print("[yellow]  Falling back to standard query execution[/yellow]")
            run_config["_effective_execution_type"] = "power"
            return self._execute_all_queries(benchmark, connection, run_config)

        console.print(f"[blue]Running combined {combined.label} test[/blue]")

        all_results: list[dict[str, Any]] = []
        phases = (
            ("power", "Power Test", combined.power_method),
            ("throughput", "Throughput Test", combined.throughput_method),
            ("maintenance", "Maintenance Test", combined.maintenance_method),
        )
        for phase_key, phase_label, method_name in phases:
            if phase_key in requested_phases:
                if phase_key == "maintenance":
                    boundary = check_phase_boundary(getattr(self, "_last_throughput_test_result", None))
                    if not boundary.proceed:
                        refusal = f"Maintenance Test refused: {boundary.reason}"
                        console.print(f"[red]❌ {refusal}[/red]")
                        all_results.append(
                            {
                                "query_id": "maintenance_test_contained",
                                "execution_time_seconds": 0.0,
                                "status": "FAILED",
                                "rows_returned": 0,
                                "error": refusal,
                                "test_type": "maintenance",
                                "contained": True,
                                "outstanding_stream_ids": list(boundary.outstanding_stream_ids),
                            }
                        )
                        continue
                console.print(f"[cyan]Phase: {phase_label}[/cyan]")
                all_results.extend(getattr(self, method_name)(benchmark, connection, run_config))
        return all_results

    def _get_runtime_platform_version(self, connection: Any | None) -> str | None:
        if connection is None:
            return None
        try:
            platform_info = self.get_platform_info(connection)
        except Exception:
            return None

        version = platform_info.get("platform_version")
        if version in (None, "", "unknown"):
            return None
        return str(version)

    def _get_dialect_queries(
        self,
        benchmark,
        benchmark_slug: str,
        connection: Any | None = None,
        *,
        strict_translation: bool = False,
    ) -> dict:
        if hasattr(self, "get_target_dialect") and hasattr(benchmark, "get_queries"):
            try:
                import inspect

                sig = inspect.signature(benchmark.get_queries)
                params = sig.parameters
                target = self.get_target_dialect()

                bench_family_id = normalize_benchmark_id(benchmark_slug) if benchmark_slug else "generic"
                bench_family = benchmark_family(bench_family_id)
                base = self.get_tpc_base_dialect(bench_family)
                platform_version = self._get_runtime_platform_version(connection)

                if "dialect" in params and "base_dialect" in params:
                    query_kwargs: dict[str, Any] = {"dialect": target, "base_dialect": base}
                    if "platform_version" in params:
                        query_kwargs["platform_version"] = platform_version
                    return benchmark.get_queries(**query_kwargs)
                elif "dialect" in params:
                    query_kwargs = {"dialect": target}
                    if "platform_version" in params:
                        query_kwargs["platform_version"] = platform_version
                    return benchmark.get_queries(**query_kwargs)
                else:
                    return benchmark.get_queries()
            except (SQLTranslationError, UnknownSqlDialectError):
                raise
            except Exception as exc:
                if strict_translation:
                    raise RuntimeError(
                        f"Dialect query extraction failed for benchmark {benchmark_slug or 'generic'}"
                    ) from exc
                return benchmark.get_queries()
        return benchmark.get_queries()

    def _filter_queries(self, queries: dict, benchmark, benchmark_name: str, run_config: dict) -> dict:
        query_subset = run_config.get("query_subset")
        categories = run_config.get("categories")

        if query_subset and categories:
            raise ValueError(
                "Cannot specify both 'query_subset' and 'categories'. "
                "Use query_subset to select specific queries by ID, or categories to select by category, but not both."
            )

        try:
            if query_subset:
                queries = self._apply_query_subset(queries, query_subset, benchmark_name)
            elif categories:
                filtered_queries = {}
                for category in categories:
                    if hasattr(benchmark, "get_queries_by_category"):
                        cat_queries = benchmark.get_queries_by_category(category)
                        filtered_queries.update(cat_queries)
                queries = filtered_queries
        except ValueError as e:
            raise RuntimeError(f"Query filtering failed: {e}") from e

        if hasattr(benchmark, "get_platform_skip_queries"):
            platform_skip = {str(s).strip().lower() for s in benchmark.get_platform_skip_queries(self.platform_name)}
            if platform_skip:
                queries = {qid: sql for qid, sql in queries.items() if str(qid).strip().lower() not in platform_skip}

        return queries

    def _apply_query_subset(self, queries: dict, query_subset: list, benchmark_name: str) -> dict:

        def _resolve(qid: object) -> object | None:
            if qid in queries:
                return qid
            qid_str = str(qid)
            if qid_str in queries:
                return qid_str
            if len(qid_str) > 1 and qid_str[0] in ("Q", "q") and qid_str[1:].isdigit():
                stripped = qid_str[1:]
                if stripped in queries:
                    return stripped
            return None

        invalid_queries: list[str] = []
        resolved: list[object] = []
        for i, query_id in enumerate(query_subset):
            actual_key = _resolve(query_id)
            if actual_key is None:
                invalid_queries.append(str(query_id))
            else:
                resolved.append(actual_key)
            if len(invalid_queries) >= 10:
                remaining = len(query_subset) - i - 1
                if remaining > 0:
                    invalid_queries.append(f"...and {remaining} more")
                break

        if invalid_queries:
            available_queries = sorted(str(k) for k in queries.keys())
            if len(available_queries) > 20:
                available_display = ", ".join(available_queries[:20]) + ", ..."
            else:
                available_display = ", ".join(available_queries)
            raise ValueError(
                f"Invalid query IDs specified: {', '.join(invalid_queries)}. "
                f"Available queries for {benchmark_name}: {available_display}"
            )

        ordered_queries = {}
        for actual_key in resolved:
            ordered_queries[actual_key] = queries[actual_key]
        return ordered_queries

    def _execute_single_query(
        self,
        benchmark,
        connection: Any,
        query_id: str,
        query_sql: str,
        benchmark_type: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(benchmark, OperationExecutor):
            return self._execute_operation_query(benchmark, connection, query_id)

        scale_factor = getattr(benchmark, "scale_factor", None)
        validator = getattr(benchmark, "validate_query_result", None)
        if callable(validator):
            from benchbox.platforms.base.result_capture import materialized_result_validation

            with materialized_result_validation(validator):
                return self.execute_query(
                    connection,
                    query_sql,
                    query_id,
                    benchmark_type=benchmark_type,
                    scale_factor=scale_factor,
                    validate_row_count=self.enable_validation,
                )

        return self.execute_query(
            connection,
            query_sql,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=self.enable_validation,
        )

    def _execute_operation_query(self, benchmark, connection: Any, query_id: str) -> dict[str, Any]:
        if not hasattr(connection, "execute"):
            connection = PlatformAdapterConnection(connection, self)
        op_kwargs: dict[str, Any] = {}
        op_kwargs["platform_key"] = getattr(self, "operation_platform_key", None) or self.get_target_dialect()
        op_kwargs["platform_fallback_key"] = getattr(self, "operation_platform_fallback_key", None)
        op_kwargs["platform_name"] = self.platform_name

        operation = None
        if hasattr(self, "preprocess_operation_sql") and hasattr(benchmark, "get_operation"):
            with contextlib.suppress(Exception):
                operation = benchmark.get_operation(str(query_id))
        if operation is not None:
            preprocessed = self.preprocess_operation_sql(str(query_id), operation)
            if preprocessed is not None:
                op_kwargs["sql_override"] = preprocessed

        op_result = benchmark.execute_operation(query_id, connection, **op_kwargs)
        op_status = getattr(op_result, "status", None) or ("SUCCESS" if op_result.success else "FAILED")

        phase_active = getattr(self, "_plan_capture_phase_active", False)
        executed_sql = getattr(op_result, "executed_sql", None)
        plan_capture_key = None
        if phase_active and op_status == "SUCCESS" and executed_sql:
            from benchbox.platforms.base.result_capture import _plan_capture_key

            plan_capture_key = _plan_capture_key(query_id, executed_sql)
            with self._plan_capture_lock:
                self._phase_recorded_queries.setdefault(plan_capture_key, executed_sql)

        result: dict[str, Any] = {
            "query_id": str(query_id),
            "status": op_status,
            "execution_time_seconds": op_result.write_duration_ms / 1000.0,
            "rows_returned": op_result.rows_affected if op_result.rows_affected > 0 else 0,
            "error": op_result.error,
            "validation_time": op_result.validation_duration_ms / 1000.0,
            "validation_passed": op_result.validation_passed,
            "cleanup_time": op_result.cleanup_duration_ms / 1000.0,
        }
        if plan_capture_key is not None:
            result["_plan_capture_key"] = plan_capture_key
        if op_result.skip_reason:
            result["skip_reason"] = op_result.skip_reason
        return result

    def _log_query_result(self, result: dict[str, Any], index: int, total: int, query_id: str) -> None:
        console = quiet_console
        status = result.get("status", "SUCCESS")

        if status == "FAILED":
            error_msg = result.get("error", "Unknown error")
            error_preview = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
            console.print(f"[red]❌ Query {index}/{total}: {query_id} FAILED - {error_preview}[/red]")
        elif status == "VALIDATION_FAILED" or result.get("validation_passed") is False:
            error_msg = result.get("error") or "post-condition validation failed"
            error_preview = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
            console.print(f"[red]❌ Query {index}/{total}: {query_id} VALIDATION FAILED - {error_preview}[/red]")
        elif status == "SKIPPED":
            skip_reason = result.get("skip_reason") or result.get("error") or "Operation not supported on this platform"
            reason_preview = skip_reason[:80] + "..." if len(skip_reason) > 80 else skip_reason
            console.print(f"[yellow]⏭️ Query {index}/{total}: {query_id} SKIPPED - {reason_preview}[/yellow]")
        else:
            execution_time = result.get("execution_time_seconds", 0)
            rows_returned = result.get("rows_returned", 0)
            time_display = self._format_execution_time(execution_time)
            validation_status = result.get("row_count_validation_status")

            if validation_status == "PASSED":
                console.print(
                    f"[green]✅ Query {index}/{total}: {query_id} completed in {time_display} ({rows_returned:,} rows) [validation: PASSED][/green]"
                )
            elif validation_status == "SKIPPED":
                console.print(
                    f"[green]✅ Query {index}/{total}: {query_id} completed in {time_display} ({rows_returned:,} rows) [validation: SKIPPED][/green]"
                )
            else:
                console.print(
                    f"[green]✅ Query {index}/{total}: {query_id} completed in {time_display} ({rows_returned:,} rows)[/green]"
                )

    def _log_execution_summary(self, results: list[dict[str, Any]], total_queries: int, cancelled: bool) -> None:
        console = quiet_console
        if cancelled:
            console.print(f"[yellow]Benchmark cancelled. Processed {len(results)}/{total_queries} queries.[/yellow]")
            return

        successful = len(
            [r for r in results if r.get("status") == "SUCCESS" and r.get("validation_passed") is not False]
        )
        skipped = len([r for r in results if r.get("status") == "SKIPPED"])
        failed = len(
            [
                r
                for r in results
                if r.get("status") in ("FAILED", "VALIDATION_FAILED") or r.get("validation_passed") is False
            ]
        )
        if failed > 0:
            console.print(
                f"[yellow]Completed {total_queries} queries: {successful} passed, {skipped} skipped, {failed} failed.[/yellow]"
            )
        elif skipped > 0:
            console.print(f"[green]Completed {total_queries} queries: {successful} passed, {skipped} skipped.[/green]")
        else:
            console.print(f"[green]Completed all {total_queries} queries.[/green]")

    def _execute_all_queries(self, benchmark, connection: Any, run_config: dict) -> list[dict[str, Any]]:

        console = quiet_console

        queries = self._get_dialect_queries(
            benchmark,
            benchmark_slug=run_config.get("benchmark_name", ""),
            connection=connection,
        )
        benchmark_name = run_config.get("benchmark_name", "")
        queries = self._filter_queries(queries, benchmark, benchmark_name, run_config)

        results = []
        total_queries = len(queries)

        cancelled = False

        def signal_handler(sig, frame):
            nonlocal cancelled
            cancelled = True
            console.print("\n[yellow]⚠️️  Cancellation requested. Will stop after current query completes.[/yellow]")
            console.print("[yellow]   Partial results will be saved.[/yellow]")

        original_sigint = signal.signal(signal.SIGINT, signal_handler)
        original_sigterm = None
        if hasattr(signal, "SIGTERM"):
            original_sigterm = signal.signal(signal.SIGTERM, signal_handler)

        try:
            console.print(
                f"[cyan]Running {total_queries} {benchmark_name} queries. Press Ctrl+C to cancel (will stop after current query).[/cyan]"
            )

            for i, (query_id, query_sql) in enumerate(queries.items(), 1):
                if cancelled:
                    break

                console.print(f"[blue]Executing query {i}/{total_queries}: {query_id}[/blue]")

                try:
                    result = self._execute_single_query(
                        benchmark,
                        connection,
                        query_id,
                        query_sql,
                        benchmark_type=benchmark_name or None,
                    )

                    if "run_type" not in result or not result.get("run_type"):
                        result["run_type"] = QUERY_RUN_TYPE_MEASUREMENT

                    results.append(result)
                    self._log_query_result(result, i, total_queries, query_id)

                except PlanCaptureError:
                    raise
                except Exception as e:
                    error_result = {
                        "query_id": str(query_id),
                        "status": "FAILED",
                        "execution_time_seconds": 0.0,
                        "rows_returned": 0,
                        "error": str(e),
                        "run_type": QUERY_RUN_TYPE_MEASUREMENT,
                    }
                    results.append(error_result)
                    console.print(f"[red]❌ Query {i}/{total_queries}: {query_id} failed - {str(e)[:100]}[/red]")

        finally:
            signal.signal(signal.SIGINT, original_sigint)
            if hasattr(signal, "SIGTERM") and original_sigterm is not None:
                signal.signal(signal.SIGTERM, original_sigterm)

        self._log_execution_summary(results, total_queries, cancelled)

        return results

    def _capture_plans_post_measurement(
        self,
        connection: Any,
        queries: dict[str, str],
        results: list[dict[str, Any]],
    ) -> None:
        self._capture_recorded_plans(connection, queries)
        self._attach_captured_plans(results)

    def _capture_recorded_plans(self, connection: Any, queries: dict[str, str]) -> None:
        from benchbox.core.plan_capture_phase import run_plan_capture_phase

        captured = self._ensure_plan_capture_accumulator()
        pending = {key: sql for key, sql in queries.items() if key not in captured}
        if not pending:
            return

        phase = run_plan_capture_phase(
            self,
            pending,
            connection=connection,
            analyze_plans=getattr(self, "analyze_plans", True),
        )

        for capture_key in pending:
            captured[capture_key] = _CapturedPlan(
                plan=phase.plans.get(capture_key),
                fingerprint=phase.fingerprints.get(capture_key),
                capture_ms=phase.per_query_capture_ms.get(capture_key),
                normalized_fingerprint=phase.normalized_fingerprints.get(capture_key),
            )

    def _attach_captured_plans(self, results: list[dict[str, Any]]) -> None:
        from benchbox.platforms.base.result_capture import _plan_capture_public_id

        captured = self._ensure_plan_capture_accumulator()

        by_public_id: dict[str, _CapturedPlan | None] = {}
        for capture_key, entry in captured.items():
            public_id = _plan_capture_public_id(capture_key)
            by_public_id[public_id] = None if public_id in by_public_id else entry

        for result in results:
            if result.get("status") != "SUCCESS":
                result.pop("_plan_capture_key", None)
                continue
            query_id = str(result.get("query_id"))
            row_key = result.pop("_plan_capture_key", None)
            entry = captured.get(str(row_key)) if row_key else by_public_id.get(query_id)
            if entry is None:
                continue
            if entry.plan is not None:
                entry.plan.query_id = query_id
                result["query_plan"] = entry.plan
                result["plan_fingerprint"] = entry.fingerprint
                if entry.normalized_fingerprint is not None:
                    result["plan_fingerprint_normalized"] = entry.normalized_fingerprint
            if entry.capture_ms is not None:
                result["plan_capture_time_ms"] = entry.capture_ms

    def _ensure_plan_capture_accumulator(self) -> dict[str, _CapturedPlan]:
        captured = getattr(self, "_captured_plans", None)
        if captured is None:
            captured = {}
            self._captured_plans = captured
        return captured

    def _plan_capture_checkpoint(self, connection: Any) -> None:
        if not getattr(self, "_plan_capture_phase_active", False):
            return
        self._capture_recorded_plans(connection, dict(self._phase_recorded_queries))

    def enable_dry_run(self, connection: Any = None) -> None:
        self.dry_run = True
        self.dry_run_mode = True
        self.captured_sql = []
        self.query_counter = 0
        if connection is not None and hasattr(connection, "_default_job_config"):
            default_config = connection._default_job_config
            if hasattr(default_config, "dry_run"):
                default_config.dry_run = True
        self.logger.info("Dry-run mode enabled - SQL will be captured instead of executed")

    def disable_dry_run(self, connection: Any = None) -> None:
        self.dry_run = bool(getattr(self, "_initial_dry_run", False))
        self.dry_run_mode = False
        self._reset_cached_dry_run_state(connection)
        self.logger.info("Dry-run mode disabled - returning to normal execution")

    def _reset_cached_dry_run_state(self, connection: Any = None) -> None:
        if connection is not None and hasattr(connection, "_default_job_config"):
            default_config = connection._default_job_config
            if hasattr(default_config, "dry_run"):
                default_config.dry_run = bool(getattr(self, "dry_run", False))

    def capture_sql(self, sql: str, operation_type: str = "query", table_name: str | None = None) -> None:
        if not self.dry_run_mode:
            return

        self.query_counter += 1
        entry = {
            "order": self.query_counter,
            "sql": sql,
            "operation_type": operation_type,
            "table_name": table_name,
            "timestamp": datetime.now().isoformat(),
        }
        self.captured_sql.append(entry)

        truncated_sql = sql if len(sql) <= 100 else f"{sql[:100]}..."
        self.logger.debug("Captured SQL (%s): %s", operation_type, truncated_sql)

    def get_captured_sql(self) -> dict[str, str]:
        return {str(entry["order"]): entry["sql"] for entry in self.captured_sql}

    def run_power_test(self, benchmark, **kwargs) -> dict[str, Any]:
        self.log_operation_start("Power test execution", f"benchmark: {benchmark.__class__.__name__}")

        if hasattr(benchmark, "run_power_test") and callable(benchmark.run_power_test):
            connection = kwargs.pop("connection", None)
            if connection is None:
                raise ValueError("TPC benchmarks require a connection object for power tests")

            if "dialect" not in kwargs:
                kwargs["dialect"] = self.get_target_dialect()

            return benchmark.run_power_test(connection, **kwargs)
        else:
            return self.run_benchmark(benchmark, **kwargs).__dict__

    def run_throughput_test(self, benchmark, **kwargs) -> dict[str, Any]:
        benchmark_id = _throughput_benchmark_id(benchmark)
        warn_legacy_throughput_api("PlatformAdapter.run_throughput_test", benchmark_id)
        if resolve_throughput_harness(benchmark_id) is None:
            return self.run_power_test(benchmark, **kwargs)
        options = dict(kwargs)
        connection = options.pop("connection", None)
        if connection is None:
            raise ValueError("TPC benchmarks require a connection object for throughput tests")
        result = self._run_routed_throughput(benchmark, connection, options)
        return {
            **vars(result),
            "total_duration": result.total_time,
            "stream_results": [dataclasses.asdict(stream) for stream in result.stream_results],
            "error": "; ".join(result.errors) or None,
        }

    def _run_routed_throughput(self, benchmark, connection: Any, options: dict[str, Any]) -> Any:
        if options.get("connection_factory") is not None:
            raise TypeError(
                "connection_factory is not supported: per-stream sessions come from the platform adapter "
                "(new_stream_connection). Pass the shared `connection` instead."
            )
        benchmark_id = _throughput_benchmark_id(benchmark)
        harness = resolve_throughput_harness(benchmark_id)
        if harness is None:
            raise ValueError(f"No supported throughput driver for benchmark '{benchmark_id or benchmark}'")
        run_config = _legacy_throughput_run_config(benchmark, benchmark_id, options)
        require_throughput_stream_capability(self, platform_name=self.platform_name)
        _resolve_requested_stream_count(run_config)
        self._last_throughput_test_result = None
        rows = getattr(self, harness.adapter_method)(benchmark, connection, run_config)
        result = self._last_throughput_test_result
        self._last_throughput_test_result = None
        if result is None:
            detail = "; ".join(str(row.get("error")) for row in rows if row.get("error")) or "no result produced"
            raise RuntimeError(f"Throughput test did not run: {detail}")
        return result

    def run_maintenance_test(self, benchmark, **kwargs) -> dict[str, Any]:
        return self.run_benchmark(benchmark, **kwargs).__dict__
