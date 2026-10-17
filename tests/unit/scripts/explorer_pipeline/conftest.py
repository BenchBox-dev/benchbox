from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

MINIMAL_BUNDLE: dict = {
    "version": "2.1",
    "run": {
        "id": "test-exec-001",
        "timestamp": "2026-03-15T10:00:00.000000",
        "total_duration_ms": 45000,
        "query_time_ms": 12000,
        "iterations": 1,
        "streams": 1,
    },
    "benchmark": {
        "id": "tpch",
        "name": "TPC-H",
        "scale_factor": 1.0,
        "test_type": "power",
    },
    "platform": {
        "name": "duckdb",
        "version": "1.2.0",
    },
    "config": {},
    "summary": {
        "queries": {
            "total": 2,
            "passed": 2,
            "failed": 0,
        },
        "timing": {
            "total_ms": 12000,
            "mean_ms": 6000.0,
        },
        "validation": "passed",
        "tpc_metrics": {
            "power_at_size": 1234.56,
        },
    },
    "phases": {},
    "queries": [
        {
            "id": "Q1",
            "ms": 8000.0,
            "rows": 100,
            "iter": 1,
            "stream": 0,
            "run_type": "measurement",
            "status": "SUCCESS",
        },
        {
            "id": "Q6",
            "ms": 4000.0,
            "rows": 1,
            "iter": 1,
            "stream": 0,
            "run_type": "measurement",
            "status": "SUCCESS",
        },
    ],
    "environment": {
        "os": "macOS 15.3.0",
        "arch": "arm64",
        "cpu_count": 10,
        "memory_gb": 32.0,
        "python": "3.12.0",
    },
    "execution": {
        "driver_actual_version": "1.2.0",
        "driver_resolved_version": "1.2.0",
        "driver_package": "duckdb",
    },
}


THROUGHPUT_QUERY_IDS: tuple[str, ...] = tuple(str(index) for index in range(1, 23))


def throughput_bundle(
    platform: str = "spark",
    *,
    benchmark: str = "tpch",
    streams: int = 3,
    throughput_at_size: float | None = 3741.26,
    power_at_size: float | None = None,
    scale_factor: float = 1.0,
    compliance_class: str = "official",
    timestamp: str = "2026-10-03T18:42:47.332357",
) -> dict:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["version"] = "2.2"
    data["run"] = {
        "id": "2a8045c8",
        "timestamp": timestamp,
        "total_duration_ms": 119000,
        "query_time_ms": 190191,
        "iterations": 1,
        "streams": streams,
    }
    data["benchmark"] = {
        "id": benchmark,
        "name": benchmark.upper(),
        "scale_factor": scale_factor,
        "test_type": "throughput",
        "compliance_class": compliance_class,
    }
    data["platform"] = {"name": platform, "version": "4.2.0"}
    data["config"] = {"mode": "sql", "seed": 20260709, "tuning_mode": "notuning"}
    data["queries"] = [
        {
            "id": query_id,
            "ms": 1000.0 + 10.0 * stream + int(query_id),
            "rows": 10,
            "iter": 1,
            "stream": stream,
            "run_type": "measurement",
            "status": "SUCCESS",
            "test_type": "throughput",
        }
        for stream in range(1, streams + 1)
        for query_id in THROUGHPUT_QUERY_IDS
    ]
    total = len(data["queries"])
    tpc_metrics: dict = {}
    if throughput_at_size is not None:
        tpc_metrics["throughput_at_size"] = throughput_at_size
    if power_at_size is not None:
        tpc_metrics["power_at_size"] = power_at_size
    data["summary"] = {
        "queries": {"total": total, "passed": total, "failed": 0},
        "validation": "passed",
        "tpc_metrics": tpc_metrics,
    }
    data["phases"] = {
        "power_test": {"status": "NOT_RUN"},
        "throughput_test": {
            "status": "COMPLETED",
            "duration_ms": 63508,
            "stream_results": [{"stream_id": stream, "success": True} for stream in range(1, streams + 1)],
            "stream_numbering": {"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": 1},
        },
    }
    return data


@pytest.fixture()
def bundle_file(tmp_path: Path) -> Path:

    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    bundle_path = bundles_dir / "tpch_duckdb_sf0.1_20260315.json"
    bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
    return bundle_path


@pytest.fixture()
def data_dir(bundle_file: Path) -> Path:

    return bundle_file.parent.parent
