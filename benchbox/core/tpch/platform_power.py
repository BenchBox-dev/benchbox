from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchbox.core.constants import (
    TPCH_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    TPCH_POWER_DEFAULT_WARMUP_ITERATIONS,
)
from benchbox.core.plan_capture_phase import propagate_query_execution_metadata

TPCH_BENCHMARK_ID = "tpch"
PowerConnectionAdapterFactory = Callable[[Any, str, float], Any]


def _power_query_result(
    query_result: dict[str, Any],
    *,
    stream_id: int,
    iteration: int,
    run_type: str,
) -> dict[str, Any]:
    platform_result = {
        "query_id": query_result["query_id"],
        "execution_time_seconds": query_result["execution_time_seconds"],
        "status": "SUCCESS" if query_result["success"] else "FAILED",
        "rows_returned": query_result.get("result_count", 0),
        "test_type": "power",
        "stream_id": query_result.get("stream_id", stream_id),
        "position": query_result.get("position", 0),
        "iteration": iteration,
        "run_type": run_type,
    }
    if query_result.get("result_digest") is not None:
        platform_result["result_digest"] = query_result["result_digest"]
    if not query_result["success"]:
        platform_result["error"] = query_result.get("error", "Unknown error")
    propagate_query_execution_metadata(query_result, platform_result)
    return platform_result


def _power_test_error_result(
    error: str,
    *,
    iteration: int | None = None,
    run_type: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "query_id": "power_test_error",
        "execution_time_seconds": 0.0,
        "status": "FAILED",
        "rows_returned": 0,
        "error": error,
        "test_type": "power",
    }
    if iteration is not None:
        result["iteration"] = iteration
    if run_type is not None:
        result["run_type"] = run_type
    return result


def execute_tpch_power_test(
    adapter: Any,
    benchmark: Any,
    connection: Any,
    run_config: dict[str, Any],
    *,
    make_connection_adapter: PowerConnectionAdapterFactory,
    console: Any,
) -> list[dict[str, Any]]:
    from benchbox.core.tpch.power_test import TPCHPowerTest

    refresh_temp_directory = None
    try:
        scale_factor = run_config.get("scale_factor", 1.0)
        seed = run_config.get("seed")
        validation_mode = run_config.get("validation_mode")
        stream_id = run_config.get("stream_id", 0)
        query_subset = run_config.get("query_subset")
        verbose = run_config.get("verbose", False)
        timeout = run_config.get("timeout")
        iterations = run_config.get("iterations", TPCH_POWER_DEFAULT_MEASUREMENT_ITERATIONS)
        warm_up_iterations = run_config.get("warm_up_iterations", TPCH_POWER_DEFAULT_WARMUP_ITERATIONS)
        fail_fast = run_config.get("power_fail_fast", False)

        refresh_update_set = None
        refresh_connection_factory = None
        refresh_enabled = (
            run_config.get("tpch_refresh_functions", getattr(benchmark, "tpch_refresh_functions", False)) is True
        )
        if refresh_enabled:
            from benchbox.core.tpch.refresh import generate_refresh_update_sets, require_refresh_support
            from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection, open_stream_connection

            require_refresh_support(adapter)
            first_set = run_config.get("tpch_refresh_first_set", getattr(benchmark, "tpch_refresh_first_set", 1))
            refresh_temp_directory = tempfile.TemporaryDirectory(prefix="benchbox-tpch-refresh-")
            refresh_update_set = generate_refresh_update_sets(
                scale_factor,
                first_set,
                1,
                Path(refresh_temp_directory.name),
            )[first_set]

            def refresh_connection_factory():
                stream_connection = open_stream_connection(adapter, connection, "olap")
                if stream_connection is connection:
                    raise RuntimeError("TPC-H refresh functions require a separate database session")
                connection_wrapper = PlatformAdapterConnection(stream_connection, adapter, maintenance_mode=True)
                connection_wrapper.benchmark_type = "tpch"
                connection_wrapper.scale_factor = scale_factor
                return connection_wrapper

        console.print(f"[green]Running TPC-H Power Test (Scale Factor: {scale_factor}, Stream ID: {stream_id})[/green]")
        console.print(f"[green]Warm-up runs: {warm_up_iterations}, Measurement runs: {iterations}[/green]")

        connection_adapter = make_connection_adapter(connection, TPCH_BENCHMARK_ID, scale_factor)
        all_results = []

        for i in range(warm_up_iterations):
            current_stream_id = stream_id
            console.print(f"[cyan]--- Warm-up Run {i + 1}/{warm_up_iterations} ---[/cyan]")
            power_test = TPCHPowerTest(
                benchmark=benchmark,
                connection=connection_adapter,
                scale_factor=scale_factor,
                seed=seed,
                stream_id=current_stream_id,
                verbose=verbose,
                timeout=timeout,
                dialect=adapter.get_target_dialect(),
                validation_mode=validation_mode,
                query_subset=query_subset,
            )
            connection_adapter._validate_row_count = power_test.config.validation
            power_test_result = power_test.run()
            for query_result in power_test_result.query_results:
                all_results.append(
                    _power_query_result(
                        query_result,
                        stream_id=current_stream_id,
                        iteration=0,
                        run_type="warmup",
                    )
                )

        for i in range(iterations):
            current_stream_id = stream_id
            console.print(f"[cyan]--- Measurement Run {i + 1}/{iterations} ---[/cyan]")
            is_final_iteration = i == iterations - 1
            power_test = TPCHPowerTest(
                benchmark=benchmark,
                connection=connection_adapter,
                scale_factor=scale_factor,
                seed=seed,
                stream_id=current_stream_id,
                verbose=verbose,
                timeout=timeout,
                dialect=adapter.get_target_dialect(),
                validation_mode=validation_mode,
                query_subset=query_subset,
                refresh_update_set=refresh_update_set if is_final_iteration else None,
                refresh_connection_factory=refresh_connection_factory if is_final_iteration else None,
            )
            connection_adapter._validate_row_count = power_test.config.validation
            power_test_result = power_test.run()
            adapter._last_power_test_result = power_test_result

            if power_test_result.success:
                success_rate = power_test_result.queries_successful / max(power_test_result.queries_executed, 1)
                console.print(
                    f"[green]✅ TPC-H Power Test completed: Power@Size = {power_test_result.power_at_size:.2f}[/green]"
                )
                console.print(
                    f"  Queries executed: {power_test_result.queries_executed}, Successful: {power_test_result.queries_successful}"
                )
                console.print(f"  Success rate: {success_rate:.1%} (TPC-H requires ≥95%)")
                console.print(f"  Total execution time: {power_test_result.total_time:.2f}s")
            else:
                console.print("[red]❌ TPC-H Power Test failed[/red]")
                for error in power_test_result.errors:
                    console.print(f"  Error: {error}")

            query_results = [
                _power_query_result(
                    query_result,
                    stream_id=current_stream_id,
                    iteration=i + 1,
                    run_type="measurement",
                )
                for query_result in power_test_result.query_results
            ]
            all_results.extend(query_results)

            if not power_test_result.success:
                if power_test_result.queries_successful == 0:
                    console.print("[yellow]⚠️  All queries failed - aborting remaining measurement runs[/yellow]")
                    if not query_results:
                        all_results.append(
                            _power_test_error_result(
                                "; ".join(power_test_result.errors)
                                if power_test_result.errors
                                else "Power test failed",
                                iteration=i + 1,
                                run_type="measurement",
                            )
                        )
                    break
                if fail_fast:
                    console.print(
                        "[yellow]⚠️  Query failures detected (fail_fast enabled) - aborting remaining runs[/yellow]"
                    )
                    break

        return all_results
    except Exception as exc:
        console.print(f"[red]❌ TPC-H Power Test failed: {exc}[/red]")
        return [_power_test_error_result(str(exc))]
    finally:
        if refresh_temp_directory is not None:
            refresh_temp_directory.cleanup()
