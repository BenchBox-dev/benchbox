from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchbox.core.plan_capture_phase import propagate_query_execution_metadata
from benchbox.core.results.models import ThroughputTestPhase
from benchbox.core.throughput.entrypoints import finalize_throughput_metrics
from benchbox.core.tpc_spec import refresh_functions_enabled
from benchbox.core.tpch.dataframe_refresh import DataFrameRefresher, execute_refresh_function
from benchbox.core.tpch.dataframe_throughput import DataFrameTPCHThroughputTest
from benchbox.core.tpch.refresh import TPCHRefreshUpdateSet, generate_refresh_update_sets
from benchbox.core.tpch.throughput_test import TPCHThroughputTestConfig
from benchbox.platforms.base.execution import TestDriversMixin, _throughput_config_options
from benchbox.platforms.base.result_capture import ResultCaptureMixin


class _ThroughputPhaseFactory:
    _format_timestamp = staticmethod(ResultCaptureMixin._format_timestamp)
    _infer_query_result_run_type = staticmethod(TestDriversMixin._infer_query_result_run_type)
    create = ResultCaptureMixin._create_throughput_phase


@dataclass
class TpchRefreshPlan:
    refresher: DataFrameRefresher
    first_set: int
    power_update_set: TPCHRefreshUpdateSet
    directory: tempfile.TemporaryDirectory[str]
    power_functions: list[dict[str, Any]] = field(default_factory=list)

    def close(self) -> None:
        self.refresher.discard()
        self.directory.cleanup()


class PowerRefresh:
    def __init__(self, plan: TpchRefreshPlan) -> None:
        self._plan = plan
        self._rf2_attempted = False

    def start(self) -> None:
        rf1 = self._run("RF1")
        if rf1["status"] != "SUCCESS":
            self._plan.power_functions.append(
                {
                    "function": "RF2",
                    "update_set": self._plan.first_set,
                    "duration_ms": 0,
                    "rows": 0,
                    "status": "SKIPPED",
                    "check_status": "NOT_RUN",
                    "error": "RF1 failed; RF2 was not run",
                }
            )
            raise RuntimeError(f"TPC-H Power Test refresh RF1 failed: {rf1['error']}")

    def finish(self) -> None:
        if self._rf2_attempted:
            return
        self._rf2_attempted = True
        rf2 = self._run("RF2")
        if rf2["status"] != "SUCCESS":
            raise RuntimeError(f"TPC-H Power Test refresh RF2 failed: {rf2['error']}")

    def _run(self, function: str) -> dict[str, Any]:
        result = execute_refresh_function(self._plan.refresher, function, self._plan.power_update_set, check=True)
        self._plan.power_functions.append(result)
        return result


def _benchmark_option(benchmark_config: Any, benchmark: Any, name: str, default: Any) -> Any:
    options = (getattr(benchmark_config, "options", None) or {}).get("benchmark_options") or {}
    for key in (name, name.replace("_", "-")):
        if key in options:
            return options[key]
    return getattr(benchmark, name, default)


def resolve_refresh_plan(adapter: Any, benchmark: Any, benchmark_config: Any, ctx: Any) -> TpchRefreshPlan | None:
    if str(getattr(benchmark_config, "name", "")).lower() != "tpch":
        return None
    if not refresh_functions_enabled(getattr(benchmark_config, "options", None), benchmark):
        return None
    backend_factory = getattr(adapter, "tpch_refresh_backend", None)
    backend = backend_factory() if callable(backend_factory) else None
    if backend is None:
        raise NotImplementedError(
            f"TPC-H refresh functions are not supported by DataFrame platform '{adapter.platform_name}'"
        )
    first_set = _benchmark_option(benchmark_config, benchmark, "tpch_refresh_first_set", 1)
    if isinstance(first_set, bool) or not isinstance(first_set, int) or first_set < 1:
        raise ValueError(f"tpch_refresh_first_set must be a positive integer, got {first_set!r}")
    directory = tempfile.TemporaryDirectory(prefix="benchbox-tpch-refresh-")
    try:
        update_set = generate_refresh_update_sets(benchmark_config.scale_factor, first_set, 1, Path(directory.name))[
            first_set
        ]
    except BaseException:
        directory.cleanup()
        raise
    return TpchRefreshPlan(DataFrameRefresher(ctx, backend), first_set, update_set, directory)


def run_throughput(
    adapter: Any,
    ctx: Any,
    benchmark_config: Any,
    plan: TpchRefreshPlan | None,
    verbose: bool,
) -> tuple[ThroughputTestPhase, list[dict[str, Any]]]:
    options = getattr(benchmark_config, "options", None) or {}
    streams = getattr(benchmark_config, "concurrency", None)
    if not isinstance(streams, int) or isinstance(streams, bool) or streams < 1:
        raise ValueError("the DataFrame throughput phase requires --streams")
    run_config = {
        "verbose": verbose,
        "cancel_on_timeout": options.get("cancel_on_timeout", False),
        "stream_timeout_seconds": options.get("stream_timeout_seconds"),
        "query_subset": getattr(benchmark_config, "queries", None),
        "seed": options.get("seed"),
    }
    config = TPCHThroughputTestConfig(
        scale_factor=benchmark_config.scale_factor,
        num_streams=streams,
        refresh_functions=plan is not None,
        refresh_first_set=plan.first_set if plan is not None else 1,
        **_throughput_config_options(run_config),
    )
    test = DataFrameTPCHThroughputTest(
        ctx,
        plan.refresher if plan is not None else None,
        adapter.execute_query,
        scale_factor=config.scale_factor,
        num_streams=streams,
        verbose=verbose,
        refresh_first_set=config.refresh_first_set,
    )
    result = test.run(config=config)
    finalize_throughput_metrics(result, streams, config.query_subset)
    phase = _ThroughputPhaseFactory().create(result)
    rows = []
    for stream in result.stream_results:
        for query_result in stream.query_results:
            row = {
                "query_id": query_result.get("query_id"),
                "execution_time_seconds": query_result.get("execution_time_seconds", 0.0),
                "status": "SUCCESS" if query_result.get("success") else "FAILED",
                "rows_returned": query_result.get("result_count", 0),
                "test_type": "throughput",
                "stream_id": stream.stream_id,
                "iteration": 1,
                "run_type": "measurement",
                "data_refreshed": plan is not None,
            }
            if not query_result.get("success"):
                row["error"] = query_result.get("error", "Unknown error")
            propagate_query_execution_metadata(query_result, row)
            rows.append(row)
    return phase, rows
