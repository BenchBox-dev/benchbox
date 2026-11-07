from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchbox.core.results.metrics import TPC_QUERIES_PER_STREAM, TPCMetricsCalculator

OFFICIAL_COMPLIANCE_CLASS = "official"


@dataclass
class QphDSEvaluation:
    qphds_at_size: float | None = None
    terms: dict[str, float] | None = None
    unavailable: list[str] = field(default_factory=list)


def _seconds(duration_ms: Any) -> float | None:
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    return duration_ms / 1000.0


def _throughput_problems(phase: Any, label: str, expected_first_stream: int, num_streams: int) -> list[str]:
    if phase is None:
        return [f"{label} did not run"]
    problems: list[str] = []
    if not phase.success:
        problems.append(f"{label} did not complete successfully")
    if phase.num_streams != num_streams:
        problems.append(f"{label} ran {phase.num_streams} streams instead of {num_streams}")
    if phase.total_queries_executed != TPC_QUERIES_PER_STREAM["tpcds"] * num_streams:
        problems.append(f"{label} streams did not execute all {TPC_QUERIES_PER_STREAM['tpcds']} queries")
    if phase.outstanding_work:
        problems.append(f"{label} left outstanding stream work")
    expected_ids = list(range(expected_first_stream, expected_first_stream + num_streams))
    description = f"streams {expected_ids[0]} to {expected_ids[-1]}" if expected_ids else "no streams"
    numbering = phase.stream_numbering or {}
    if numbering.get("first_stream_id") != expected_first_stream:
        problems.append(f"{label} did not use {description}")
    if sorted(stream.stream_id for stream in phase.streams) != expected_ids:
        problems.append(f"{label} stream numbers are not {description}")
    return problems


def _maintenance_refresh_sets(phase: Any) -> set[int]:
    return {
        operation.refresh_set
        for operation in phase.maintenance_operations
        if isinstance(operation.refresh_set, int) and not isinstance(operation.refresh_set, bool)
    }


def _contiguous(values: set[int]) -> bool:
    return bool(values) and max(values) - min(values) + 1 == len(values)


def _consecutive_windows(first: set[int], second: set[int]) -> bool:
    return _contiguous(first) and _contiguous(second) and min(second) == max(first) + 1


_LOAD_SUCCESS_STATUSES = frozenset({"SUCCESS", "COMPLETED"})


def _load_seconds(setup: Any) -> tuple[float | None, list[str]]:
    loading = getattr(setup, "data_loading", None)
    if loading is None:
        return None, ["database load time was not captured"]
    problems: list[str] = []
    total_ms = 0.0
    if getattr(setup, "database_reused", False):
        problems.append("database load was reused, so its time was not measured in this run")
    timed = (
        ("table creation", getattr(setup, "schema_creation", None)),
        ("data loading", loading),
        ("statistics gathering", getattr(setup, "statistics_gathering", None)),
        ("post-load maintenance", getattr(setup, "post_load_maintenance", None)),
    )
    for label, phase in timed:
        if phase is None:
            continue
        if phase.status not in _LOAD_SUCCESS_STATUSES:
            problems.append(f"database load {label} did not complete successfully ({phase.status})")
        duration_ms = phase.duration_ms
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
            problems.append(f"database load {label} duration was not captured")
        else:
            total_ms += duration_ms
    settle = getattr(setup, "merge_settle", None)
    if settle is not None:
        if not settle.settled:
            problems.append("database load merge settle did not complete")
        total_ms += settle.waited_seconds * 1000.0
    if any(stats.status == "REUSED" for stats in loading.per_table_stats.values()):
        problems.append("database load was reused, so its time was not measured in this run")
    return total_ms / 1000.0, problems


def _maintenance_problems(phase: Any, label: str, refresh_runs: int) -> list[str]:
    if phase is None:
        return [f"{label} did not run"]
    problems: list[str] = []
    if not phase.success:
        problems.append(f"{label} did not complete successfully")
    refresh_sets = _maintenance_refresh_sets(phase)
    if len(refresh_sets) != refresh_runs:
        problems.append(f"{label} ran {len(refresh_sets)} refresh runs instead of {refresh_runs}")
    return problems


def evaluate_qphds(
    phases: Any,
    *,
    scale_factor: float,
    compliance_class: str | None,
    failed_query_count: int,
    query_subset: list[str] | None,
) -> QphDSEvaluation:
    problems: list[str] = []
    if compliance_class != OFFICIAL_COMPLIANCE_CLASS:
        problems.append(f"results are not official compliance ({compliance_class or 'unspecified'})")
    if failed_query_count:
        problems.append(f"{failed_query_count} queries failed")
    if query_subset:
        problems.append("a query subset was selected")

    power = getattr(phases, "power_test", None)
    throughput_1 = getattr(phases, "throughput_test", None)
    throughput_2 = getattr(phases, "throughput_test_2", None)
    maintenance_1 = getattr(phases, "maintenance_test", None)
    maintenance_2 = getattr(phases, "maintenance_test_2", None)

    num_streams = getattr(throughput_1, "num_streams", None)
    stream_count = num_streams if isinstance(num_streams, int) and not isinstance(num_streams, bool) else 0

    if power is None:
        problems.append("Power Test did not run")
    else:
        if power.success is not True:
            problems.append("Power Test did not complete successfully")
        if power.query_count != TPC_QUERIES_PER_STREAM["tpcds"]:
            problems.append(f"Power Test ran {power.query_count} queries instead of {TPC_QUERIES_PER_STREAM['tpcds']}")

    problems.extend(_throughput_problems(throughput_1, "Throughput Test 1", 1, stream_count))
    problems.extend(_throughput_problems(throughput_2, "Throughput Test 2", stream_count + 1, stream_count))
    refresh_runs = max(stream_count // 2, 1)
    problems.extend(_maintenance_problems(maintenance_1, "Data Maintenance Test 1", refresh_runs))
    problems.extend(_maintenance_problems(maintenance_2, "Data Maintenance Test 2", refresh_runs))
    if maintenance_1 is not None and maintenance_2 is not None:
        first_sets = _maintenance_refresh_sets(maintenance_1)
        second_sets = _maintenance_refresh_sets(maintenance_2)
        if first_sets and second_sets and not _consecutive_windows(first_sets, second_sets):
            problems.append("Data Maintenance Test 2 did not use the refresh sets that follow Data Maintenance Test 1")

    load_seconds, load_problems = _load_seconds(getattr(phases, "setup", None))
    problems.extend(load_problems)
    power_seconds = _seconds(getattr(power, "duration_ms", None))
    throughput_seconds = [_seconds(getattr(phase, "duration_ms", None)) for phase in (throughput_1, throughput_2)]
    maintenance_seconds = [_seconds(getattr(phase, "duration_ms", None)) for phase in (maintenance_1, maintenance_2)]
    problems.extend(
        TPCMetricsCalculator.qphds_input_problems(
            scale_factor, stream_count, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
        )
    )

    if problems:
        return QphDSEvaluation(unavailable=list(dict.fromkeys(problems)))

    terms = TPCMetricsCalculator.calculate_qphds_terms(
        scale_factor, stream_count, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
    )
    qphds = TPCMetricsCalculator.calculate_qphds_at_size(
        scale_factor, stream_count, power_seconds, throughput_seconds, maintenance_seconds, load_seconds
    )
    return QphDSEvaluation(qphds_at_size=qphds, terms=terms)
