# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytest

from benchbox.core.results.models import BenchmarkResults, ExecutionPhases, PowerTestPhase, QueryExecution, SetupPhase


def make_v2_result_dict(
    *,
    version: str = "2.1",
    benchmark_id: str = "tpch",
    benchmark_name: str = "TPC-H",
    platform: str = "duckdb",
    platform_version: str | None = None,
    scale_factor: float = 0.01,
    execution_id: str = "test-run-001",
    timestamp: str = "2026-01-01T12:00:00",
    total_duration_ms: float = 1000,
    query_time_ms: float = 1000,
    iterations: int = 1,
    streams: int = 1,
    total_queries: int = 22,
    passed_queries: int = 22,
    failed_queries: int = 0,
    total_ms: float = 5000,
    execution_mode: str | None = None,
    queries: list[dict[str, Any]] | None = None,
    **extras: Any,
) -> dict[str, Any]:
    platform_block: dict[str, Any] = {"name": platform}
    if platform_version:
        platform_block["version"] = platform_version

    result: dict[str, Any] = {
        "version": version,
        "run": {
            "id": execution_id,
            "timestamp": timestamp,
            "total_duration_ms": total_duration_ms,
            "query_time_ms": query_time_ms,
            "iterations": iterations,
            "streams": streams,
        },
        "benchmark": {
            "id": benchmark_id,
            "name": benchmark_name,
            "scale_factor": scale_factor,
        },
        "platform": platform_block,
        "summary": {
            "queries": {
                "total": total_queries,
                "passed": passed_queries,
                "failed": failed_queries,
            },
            "timing": {"total_ms": total_ms},
        },
        "queries": queries if queries is not None else [],
    }

    if execution_mode:
        result.setdefault("config", {})["mode"] = execution_mode

    for key, value in extras.items():
        result[key] = value

    return result


def write_v2_result_file(path: Path, **kwargs: Any) -> dict[str, Any]:
    data = make_v2_result_dict(**kwargs)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def make_normalized_result(
    *,
    platform: str = "DuckDB",
    benchmark: str = "tpch",
    scale_factor: float | int = 1,
    execution_id: str | None = None,
    timestamp: Any = None,
    total_time_ms: float | None = None,
    avg_time_ms: float | None = None,
    success_rate: float | None = None,
    cost_total: float | None = None,
    execution_mode: str | None = None,
    power_at_size: float | None = None,
    queries: list[Any] | None = None,
    source_path: Any = None,
    raw: dict[str, Any] | None = None,
    platform_version: str | None = None,
) -> Any:
    from benchbox.core.visualization.result_plotter import NormalizedResult

    effective_raw = dict(raw) if raw is not None else {}
    if platform_version and "platform" not in effective_raw:
        effective_raw["platform"] = {"name": platform, "version": platform_version}

    return NormalizedResult(
        benchmark=benchmark,
        platform=platform,
        scale_factor=scale_factor,
        execution_id=execution_id,
        timestamp=timestamp,
        total_time_ms=total_time_ms,
        avg_time_ms=avg_time_ms,
        success_rate=success_rate,
        cost_total=cost_total,
        execution_mode=execution_mode,
        power_at_size=power_at_size,
        queries=queries or [],
        source_path=source_path,
        raw=effective_raw,
    )


@pytest.fixture
def v2_result_dict():
    return make_v2_result_dict


@pytest.fixture
def v2_result_file(tmp_path: Path):
    counter = [0]

    def _create(**kwargs: Any) -> tuple[Path, dict[str, Any]]:
        counter[0] += 1
        path = tmp_path / f"result_{counter[0]}.json"
        data = write_v2_result_file(path, **kwargs)
        return path, data

    return _create


def make_power_only_phases(
    timestamp: Optional[datetime] = None,
    query_executions: Optional[list[QueryExecution]] = None,
    power_at_size: float = 1000.0,
) -> ExecutionPhases:
    ts = (timestamp or datetime.now()).isoformat()
    return ExecutionPhases(
        setup=SetupPhase(),
        power_test=PowerTestPhase(
            start_time=ts,
            end_time=ts,
            duration_ms=125,
            query_executions=list(query_executions or []),
            geometric_mean_time=0.1,
            power_at_size=power_at_size,
        ),
    )


def make_benchmark_results(
    *,
    benchmark_id: str = "test-benchmark",
    benchmark_name: str = "Test Benchmark",
    execution_id: str = "test-exec",
    platform: str = "cli",
    scale_factor: float = 0.01,
    duration_seconds: float = 0.0,
    timestamp: Optional[datetime] = None,
    total_queries: int = 0,
    successful_queries: int = 0,
    failed_queries: int = 0,
    query_results: Optional[Iterable[Any]] = None,
    validation_status: str = "UNKNOWN",
    validation_details: Optional[dict[str, Any]] = None,
    execution_metadata: Optional[dict[str, Any]] = None,
    summary_metrics: Optional[dict[str, Any]] = None,
    query_subset: Optional[list[str]] = None,
    concurrency_level: Optional[int] = None,
    **extras: Any,
) -> BenchmarkResults:

    field_names = set(BenchmarkResults.__dataclass_fields__.keys())

    init_kwargs: dict[str, Any] = {
        "benchmark_name": benchmark_name,
        "platform": platform,
        "scale_factor": scale_factor,
        "execution_id": execution_id,
        "timestamp": timestamp or datetime.now(),
        "duration_seconds": duration_seconds,
        "total_queries": total_queries,
        "successful_queries": successful_queries,
        "failed_queries": failed_queries,
        "query_results": list(query_results or []),
        "validation_status": validation_status,
        "validation_details": validation_details or {},
        "execution_metadata": execution_metadata or {},
    }

    for key, value in extras.items():
        if key in field_names:
            init_kwargs[key] = value

    result = BenchmarkResults(**init_kwargs)

    result._benchmark_id_override = benchmark_id
    result.summary_metrics = summary_metrics or {}
    if query_subset is not None:
        result.query_subset = query_subset
    if concurrency_level is not None:
        result.concurrency_level = concurrency_level

    for key, value in extras.items():
        if key not in field_names:
            setattr(result, key, value)

    return result


@pytest.fixture
def make_results():
    return make_benchmark_results
