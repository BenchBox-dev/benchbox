from __future__ import annotations

import contextlib
from typing import Any

from benchbox.core.constants import (
    TPCDS_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    TPCDS_POWER_DEFAULT_WARMUP_ITERATIONS,
)
from benchbox.core.execution_engine import unmeasured_statements
from benchbox.core.tpch.platform_power import (
    PowerConnectionAdapterFactory,
    _power_query_result,
    _power_test_error_result,
)
from benchbox.utils.run_deadline import run_stop_requested

TPCDS_BENCHMARK_ID = "tpcds"


def execute_tpcds_power_test(
    adapter: Any,
    benchmark: Any,
    connection: Any,
    run_config: dict[str, Any],
    *,
    make_connection_adapter: PowerConnectionAdapterFactory,
    console: Any,
) -> list[dict[str, Any]]:
    from benchbox.core.expected_results.tpcds_results import set_config_validation_mode
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    try:
        scale_factor = run_config.get("scale_factor", 1.0)
        seed = run_config.get("seed", 1)
        validation_mode = run_config.get("validation_mode")
        stream_id = run_config.get("stream_id", 0)
        query_subset = run_config.get("query_subset")
        dialect = getattr(adapter, "get_target_dialect", lambda: "standard")()
        verbose = run_config.get("verbose", False)
        timeout = run_config.get("timeout")
        iterations = run_config.get("iterations", TPCDS_POWER_DEFAULT_MEASUREMENT_ITERATIONS)
        warm_up_iterations = run_config.get("warm_up_iterations", TPCDS_POWER_DEFAULT_WARMUP_ITERATIONS)
        fail_fast = run_config.get("power_fail_fast", False)

        set_config_validation_mode(validation_mode)

        console.print(
            f"[green]Running TPC-DS Power Test (Scale Factor: {scale_factor}, Stream ID: {stream_id})[/green]"
        )
        console.print(f"[green]Warm-up runs: {warm_up_iterations}, Measurement runs: {iterations}[/green]")

        def connection_factory():
            return make_connection_adapter(connection, TPCDS_BENCHMARK_ID, scale_factor)

        all_results = []

        for i in range(warm_up_iterations):
            if run_stop_requested():
                break
            current_stream_id = i
            console.print(f"[cyan]--- Warm-up Run {i + 1}/{warm_up_iterations} ---[/cyan]")
            power_test = TPCDSPowerTest(
                benchmark=benchmark,
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                seed=seed,
                stream_id=current_stream_id,
                verbose=verbose,
                timeout=timeout,
                dialect=adapter.get_target_dialect(),
                query_subset=query_subset,
            )
            with unmeasured_statements():
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
            if run_stop_requested():
                break
            current_stream_id = warm_up_iterations + i
            console.print(f"[cyan]--- Measurement Run {i + 1}/{iterations} ---[/cyan]")
            power_test = TPCDSPowerTest(
                benchmark=benchmark,
                connection_factory=connection_factory,
                scale_factor=scale_factor,
                seed=seed,
                stream_id=current_stream_id,
                verbose=verbose,
                timeout=timeout,
                dialect=adapter.get_target_dialect(),
                query_subset=query_subset,
            )
            power_test_result = power_test.run()
            adapter._last_power_test_result = power_test_result

            if getattr(adapter, "very_verbose", False):
                with contextlib.suppress(Exception):
                    console.print(f"[dim]Target dialect: {dialect} | Detailed per-query results:[/dim]")
                for qr in power_test_result.query_results:
                    qname = f"q{qr.get('query_id')}"
                    dur = qr.get("execution_time_seconds", 0.0)
                    status = "SUCCESS" if qr.get("success") else "FAILED"
                    rows = qr.get("result_count", 0)
                    console.print(f"  • {qname}: {dur:.2f}s, {status}, rows={rows}")

            if power_test_result.success:
                success_rate = power_test_result.queries_successful / max(power_test_result.queries_executed, 1)
                console.print(
                    f"[green]✅ TPC-DS Power Test completed: Power@Size = {power_test_result.power_at_size:.2f}[/green]"
                )
                console.print(
                    f"  Queries executed: {power_test_result.queries_executed}, Successful: {power_test_result.queries_successful}"
                )
                console.print(f"  Success rate: {success_rate:.1%}")
                console.print(f"  Total execution time: {power_test_result.total_time:.2f}s")
            else:
                console.print("[red]❌ TPC-DS Power Test failed[/red]")
                for error in power_test_result.errors:
                    console.print(f"  Error: {error}")

            query_results = []
            for query_result in power_test_result.query_results:
                query_results.append(
                    _power_query_result(
                        query_result,
                        stream_id=current_stream_id,
                        iteration=i + 1,
                        run_type="measurement",
                    )
                )
            all_results.extend(query_results)

            if not power_test_result.success:
                if power_test_result.queries_successful == 0:
                    console.print("[yellow]⚠️  All queries failed - aborting remaining measurement runs[/yellow]")
                    if not query_results and not run_stop_requested():
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

    except Exception as e:
        console.print(f"[red]❌ TPC-DS Power Test failed: {e}[/red]")
        return [_power_test_error_result(str(e))]
