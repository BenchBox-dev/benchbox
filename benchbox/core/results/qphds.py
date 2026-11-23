from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchbox.core.results import corpus_score
from benchbox.core.results.metrics import TPC_QUERIES_PER_STREAM, TPCMetricsCalculator
from benchbox.core.tpcds.streams import MULTI_PART_QUERY_IDS

OFFICIAL_COMPLIANCE_CLASS = "official"
QUERY_STATEMENTS_PER_STREAM = TPC_QUERIES_PER_STREAM["tpcds"] + len(MULTI_PART_QUERY_IDS)


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
    if phase.total_queries_executed != QUERY_STATEMENTS_PER_STREAM * num_streams:
        problems.append(
            f"{label} streams did not execute all {TPC_QUERIES_PER_STREAM['tpcds']} queries "
            f"({QUERY_STATEMENTS_PER_STREAM} statements per stream)"
        )
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
    return corpus_score.refresh_sets_of([operation.refresh_set for operation in phase.maintenance_operations])


LOAD_SCOPE_COMPONENTS = ("schema_creation", "data_loading", "statistics_gathering", "post_load_maintenance")


def load_scope_components(setup: Any) -> list[str] | None:
    if setup is None or getattr(setup, "data_loading", None) is None:
        return None
    components = [name for name in LOAD_SCOPE_COMPONENTS if getattr(setup, name, None) is not None]
    if getattr(setup, "merge_settle", None) is not None:
        components.append("merge_settle")
    return components


def _load_seconds(setup: Any) -> tuple[float | None, list[str]]:
    loading = getattr(setup, "data_loading", None)
    if loading is None:
        return corpus_score.qphds_load_evidence([], data_loading_captured=False, reused=None, settle=None)
    timed = [
        (label, phase.status, phase.duration_ms)
        for label, phase in (
            ("table creation", getattr(setup, "schema_creation", None)),
            ("data loading", loading),
            ("statistics gathering", getattr(setup, "statistics_gathering", None)),
            ("post-load maintenance", getattr(setup, "post_load_maintenance", None)),
        )
        if phase is not None
    ]
    settle = getattr(setup, "merge_settle", None)
    return corpus_score.qphds_load_evidence(
        timed,
        data_loading_captured=True,
        reused=database_load_reused(setup),
        settle=None if settle is None else (settle.settled, settle.waited_seconds),
    )


def database_load_reused(setup: Any) -> bool:
    return bool(getattr(setup, "database_reused", False)) or any(
        stats.status == "REUSED" for stats in setup.data_loading.per_table_stats.values()
    )


def _maintenance_problems(phase: Any, label: str, refresh_runs: int) -> list[str]:
    return corpus_score.qphds_maintenance_problems(
        phase is not None,
        getattr(phase, "success", None),
        _maintenance_refresh_sets(phase) if phase is not None else set(),
        label,
        refresh_runs,
    )


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

    problems.extend(
        corpus_score.qphds_power_problems(
            power is not None, getattr(power, "success", None), getattr(power, "query_count", None)
        )
    )

    problems.extend(_throughput_problems(throughput_1, "Throughput Test 1", 1, stream_count))
    problems.extend(_throughput_problems(throughput_2, "Throughput Test 2", stream_count + 1, stream_count))
    refresh_runs = corpus_score.qphds_refresh_runs(stream_count)
    problems.extend(_maintenance_problems(maintenance_1, "Data Maintenance Test 1", refresh_runs))
    problems.extend(_maintenance_problems(maintenance_2, "Data Maintenance Test 2", refresh_runs))
    if maintenance_1 is not None and maintenance_2 is not None:
        problems.extend(
            corpus_score.qphds_refresh_window_problems(
                _maintenance_refresh_sets(maintenance_1), _maintenance_refresh_sets(maintenance_2)
            )
        )

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
