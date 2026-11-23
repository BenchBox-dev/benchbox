from __future__ import annotations

import copy
import json
import math
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
POWER_QUERY_IDS: tuple[str, ...] = tuple(f"Q{index}" for index in range(1, 23))
THROUGHPUT_PHASE_MS = 63508
SECONDS_PER_HOUR = 3600


def power_query_timings(
    power: float,
    scale_factor: float = 1.0,
    *,
    ids: tuple[str, ...] = THROUGHPUT_QUERY_IDS,
    pinned: dict[str, dict] | None = None,
    fixed_ms: tuple[float, ...] = (),
    test_type: str | None = None,
) -> list[dict]:
    pinned = pinned or {}
    unknown = set(pinned) - set(ids)
    assert not unknown, f"pinned queries outside the query set: {sorted(unknown)}"
    free = [query_id for query_id in ids if query_id not in pinned]
    target_log_ms = (len(ids) + len(fixed_ms)) * math.log(scale_factor * SECONDS_PER_HOUR * 1000.0 / power)
    pinned_log_ms = sum(math.log(query["ms"]) for query in pinned.values()) + sum(math.log(ms) for ms in fixed_ms)
    filler_ms = math.exp((target_log_ms - pinned_log_ms) / len(free)) if free else None
    extra = {"test_type": test_type} if test_type else {}
    return [
        copy.deepcopy(pinned[query_id])
        if query_id in pinned
        else {
            "id": query_id,
            "ms": filler_ms,
            "rows": 1,
            "iter": 1,
            "stream": 0,
            "run_type": "measurement",
            "status": "SUCCESS",
            **extra,
        }
        for query_id in ids
    ]


def with_power(data: dict, power: float) -> dict:
    scale_factor = data["benchmark"]["scale_factor"]
    pinned = {query["id"]: query for query in data["queries"]}
    data["queries"] = power_query_timings(power, scale_factor, ids=POWER_QUERY_IDS, pinned=pinned)
    total = len(data["queries"])
    data["summary"]["queries"] = {"total": total, "passed": total, "failed": 0}
    data["summary"]["tpc_metrics"] = {}
    return data


def scored_minimal_bundle(power: float = 1234.56) -> dict:
    return with_power(copy.deepcopy(MINIMAL_BUNDLE), power)


def throughput_phase_ms(throughput: float, executed: int, scale_factor: float = 1.0) -> float:
    return executed * scale_factor * SECONDS_PER_HOUR * 1000.0 / throughput


def throughput_bundle(
    platform: str = "spark",
    *,
    benchmark: str = "tpch",
    streams: int = 3,
    throughput_at_size: float | str | None = 3741.26,
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
    executed = {"tpch": 22, "tpcds": 99}.get(benchmark, 0) * streams or len(data["queries"])
    if power_at_size is not None:
        data["queries"] += power_query_timings(power_at_size, scale_factor, test_type="power")
    total = len(data["queries"])
    data["summary"] = {
        "queries": {"total": total, "passed": total, "failed": 0},
        "validation": "passed",
    }
    data["phases"] = {
        "power_test": {"status": "NOT_RUN"},
        "throughput_test": {
            "status": "COMPLETED",
            "num_streams": streams,
            "stream_results": [{"stream_id": stream, "success": True} for stream in range(1, streams + 1)],
            "stream_numbering": {"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": 1},
        },
    }
    if throughput_at_size is not None:
        scorable = isinstance(throughput_at_size, int | float) and math.isfinite(throughput_at_size)
        scorable = scorable and throughput_at_size > 0
        data["phases"]["throughput_test"]["duration_ms"] = (
            throughput_phase_ms(throughput_at_size, executed, scale_factor) if scorable else throughput_at_size
        )
    return data


TPCDS_QUERY_IDS: tuple[str, ...] = tuple(
    identifier
    for index in range(1, 100)
    for identifier in ((f"{index}a", f"{index}b") if index in (14, 23, 24, 39) else (str(index),))
)
TPCDS_POWER_STATEMENTS = len(TPCDS_QUERY_IDS)
TPCDS_STREAMS = 4
MAINTENANCE_PHASE_MS = 5000
POWER_PHASE_MS = 30000
RF_PHASE_MS = 1200.0


def _refresh_function(name: str, update_set: int, **extra: object) -> dict:
    return {"function": name, "status": "SUCCESS", "update_set": update_set, "duration_ms": RF_PHASE_MS, **extra}


def tpcds_load_seconds_for(qph: float, streams: int, power_ms: float, scale_factor: float) -> float:
    q = 99.0 * streams
    quartic = (scale_factor * q / qph) ** 4
    pt = power_ms / 1000.0 * streams / SECONDS_PER_HOUR
    tt = 2 * THROUGHPUT_PHASE_MS / 1000.0 / SECONDS_PER_HOUR
    dm = 2 * MAINTENANCE_PHASE_MS / 1000.0 / SECONDS_PER_HOUR
    ld_hours = quartic / (pt * tt * dm)
    return ld_hours * SECONDS_PER_HOUR / (0.01 * streams)


def combined_bundle(
    platform: str = "platform-a",
    *,
    benchmark: str = "tpch",
    streams: int | None = None,
    qph: float | None = 250.0,
    power: float = 1234.5,
    transactional: bool | None = None,
) -> dict:
    tpcds = benchmark == "tpcds"
    streams = streams if streams is not None else (TPCDS_STREAMS if tpcds else 2)
    query_ids = TPCDS_QUERY_IDS if tpcds else THROUGHPUT_QUERY_IDS
    data = throughput_bundle(platform, benchmark=benchmark, streams=streams, throughput_at_size=None)
    data["benchmark"]["test_type"] = "combined"
    total_streams = streams * (2 if tpcds else 1)
    data["queries"] = [
        {
            "id": query_id,
            "ms": 1000.0 + 10.0 * stream + position,
            "rows": 10,
            "iter": 1,
            "stream": stream,
            "run_type": "measurement",
            "status": "SUCCESS",
            "test_type": "throughput",
        }
        for stream in range(1, total_streams + 1)
        for position, query_id in enumerate(query_ids)
    ]
    executed = len(data["queries"])
    refresh_ms = () if tpcds else (RF_PHASE_MS, RF_PHASE_MS)
    data["queries"] += power_query_timings(power, 1.0, ids=query_ids, fixed_ms=refresh_ms, test_type="power")
    total = len(data["queries"])
    data["summary"]["queries"] = {"total": total, "passed": total, "failed": 0}
    phases = data["phases"]
    flag = {} if transactional is None else {"transactional": transactional}
    if tpcds:
        power_ms = float(POWER_PHASE_MS)
        phases["power_test"] = {
            "status": "COMPLETED",
            "query_count": TPCDS_POWER_STATEMENTS,
            "duration_ms": power_ms,
        }
        phases["throughput_test"]["duration_ms"] = THROUGHPUT_PHASE_MS
        phases["throughput_test_2"] = {
            "status": "COMPLETED",
            "duration_ms": THROUGHPUT_PHASE_MS,
            "num_streams": streams,
            "stream_results": [
                {"stream_id": stream, "success": True} for stream in range(streams + 1, 2 * streams + 1)
            ],
            "stream_numbering": {"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": streams + 1},
        }
        for round_number, name in enumerate(("maintenance_test", "maintenance_test_2"), start=1):
            phases[name] = {
                "status": "COMPLETED",
                "duration_ms": MAINTENANCE_PHASE_MS,
                "operations": [
                    {
                        "operation": operation,
                        "table": table,
                        "status": "SUCCESS",
                        "refresh_set": (round_number - 1) * (streams // 2) + run,
                        "duration_ms": 100.0,
                        **flag,
                    }
                    for run in range(1, streams // 2 + 1)
                    for operation, table in (("DF_SS", "store_sales"), ("LF_SS", "store_sales"))
                ],
            }
        phases["schema_creation"] = {"status": "COMPLETED", "duration_ms": 1000.0}
        if qph is None:
            phases["data_loading"] = {"status": "COMPLETED", "database_reused": False}
        else:
            load_ms = tpcds_load_seconds_for(math.floor(qph) + 0.5, streams, power_ms, 1.0) * 1000.0 - 1000.0
            phases["data_loading"] = {"status": "COMPLETED", "duration_ms": load_ms, "database_reused": False}
    else:
        phases["power_test"] = {
            "status": "COMPLETED",
            "query_count": 22,
            "refresh_first_set": 1,
            "refresh_functions": [_refresh_function("RF1", 1, **flag), _refresh_function("RF2", 1, **flag)],
        }
        if qph is not None:
            phases["throughput_test"]["duration_ms"] = throughput_phase_ms(qph**2 / power, executed)
        phases["throughput_test"]["refresh_stream"] = {
            "status": "COMPLETED",
            "first_set": 2,
            "start_time": "2026-10-03T18:40:00",
            "end_time": "2026-10-03T18:41:00",
            "pair_count": streams,
            "pairs": [
                {
                    "update_set": 2 + offset,
                    "status": "SUCCESS",
                    "functions": [
                        _refresh_function("RF1", 2 + offset, **flag),
                        _refresh_function("RF2", 2 + offset, **flag),
                    ],
                }
                for offset in range(streams)
            ],
        }
    data["summary"]["tpc_metrics"] = {
        "load_scope": {"components": ["schema_creation", "data_loading"], "counted_in_qph": tpcds}
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
