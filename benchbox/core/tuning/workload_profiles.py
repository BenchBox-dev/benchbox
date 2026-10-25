from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILE_ROOT = Path(__file__).resolve().parent / "profiles"
DEFAULT_TPC_PROFILE = PROFILE_ROOT / "tpc.yaml"

ACCEPTED = "accepted"
EXISTING_BASELINE = "existing_baseline"
DROPPED_LOW_EVIDENCE = "dropped_low_evidence"
VALID_STATUSES = frozenset({ACCEPTED, EXISTING_BASELINE, DROPPED_LOW_EVIDENCE})

TEMPORAL_PARTITION = "temporal_partition"
HIGH_SELECTIVITY_FILTER = "high_selectivity_filter"
JOIN_LOCALITY = "join_locality"
FACT_DIMENSION_JOIN = "fact_dimension_join"
GROUP_ORDER_LOCALITY = "group_order_locality"
DISTRIBUTION_CANDIDATE = "distribution_candidate"
VALID_ROLES = frozenset(
    {
        TEMPORAL_PARTITION,
        HIGH_SELECTIVITY_FILTER,
        JOIN_LOCALITY,
        FACT_DIMENSION_JOIN,
        GROUP_ORDER_LOCALITY,
        DISTRIBUTION_CANDIDATE,
    }
)


@dataclass(frozen=True)
class WorkloadTuningCandidate:
    benchmark: str
    table: str
    column: str
    type: str
    roles: tuple[str, ...]
    query_count: int
    query_ids: tuple[str, ...]
    status: str
    rationale: str
    evidence_source: str
    fact_table: bool = True

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.benchmark, self.table, self.column)

    @property
    def is_required_for_mapping(self) -> bool:
        return self.status in {ACCEPTED, EXISTING_BASELINE}

    @property
    def is_dropped(self) -> bool:
        return self.status == DROPPED_LOW_EVIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "table": self.table,
            "column": self.column,
            "type": self.type,
            "roles": list(self.roles),
            "query_count": self.query_count,
            "query_ids": list(self.query_ids),
            "status": self.status,
            "rationale": self.rationale,
            "evidence_source": self.evidence_source,
            "fact_table": self.fact_table,
        }


@dataclass(frozen=True)
class WorkloadTuningProfile:
    id: str
    version: str
    description: str
    candidates_by_benchmark: dict[str, tuple[WorkloadTuningCandidate, ...]]

    def benchmarks(self) -> tuple[str, ...]:
        return tuple(sorted(self.candidates_by_benchmark))

    def candidates(self, benchmark: str) -> tuple[WorkloadTuningCandidate, ...]:
        return self.candidates_by_benchmark.get(benchmark.lower(), ())

    def required_candidates(self, benchmark: str) -> tuple[WorkloadTuningCandidate, ...]:
        return tuple(candidate for candidate in self.candidates(benchmark) if candidate.is_required_for_mapping)

    def dropped_candidates(self, benchmark: str) -> tuple[WorkloadTuningCandidate, ...]:
        return tuple(candidate for candidate in self.candidates(benchmark) if candidate.is_dropped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "benchmarks": {
                benchmark: [candidate.to_dict() for candidate in candidates]
                for benchmark, candidates in self.candidates_by_benchmark.items()
            },
        }


def load_workload_tuning_profile(path: Path = DEFAULT_TPC_PROFILE) -> WorkloadTuningProfile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile_data = data.get("profile")
    if not isinstance(profile_data, dict):
        raise ValueError(f"{path} does not contain a profile mapping")

    profile_id = _required_str(profile_data, "id")
    version = _required_str(profile_data, "version")
    description = _required_str(profile_data, "description")
    benchmarks_data = profile_data.get("benchmarks")
    if not isinstance(benchmarks_data, dict) or not benchmarks_data:
        raise ValueError(f"{path} profile must define at least one benchmark")

    candidates_by_benchmark: dict[str, tuple[WorkloadTuningCandidate, ...]] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    for benchmark, benchmark_data in benchmarks_data.items():
        benchmark_key = str(benchmark).lower()
        if not isinstance(benchmark_data, dict):
            raise ValueError(f"{benchmark_key} profile entry must be a mapping")
        evidence_source = _required_str(benchmark_data, "evidence_source")
        fact_tables = _parse_fact_tables(benchmark_key, benchmark_data.get("fact_tables"))
        raw_candidates = benchmark_data.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise ValueError(f"{benchmark_key} profile entry must define candidates")

        candidates = tuple(
            _parse_candidate(benchmark_key, raw_candidate, evidence_source, seen_keys, fact_tables)
            for raw_candidate in raw_candidates
        )
        candidates_by_benchmark[benchmark_key] = candidates

    return WorkloadTuningProfile(
        id=profile_id,
        version=version,
        description=description,
        candidates_by_benchmark=candidates_by_benchmark,
    )


@lru_cache(maxsize=1)
def load_tpc_tuning_profile() -> WorkloadTuningProfile:
    return load_workload_tuning_profile(DEFAULT_TPC_PROFILE)


def _parse_fact_tables(benchmark: str, raw: Any) -> frozenset[str]:
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) and item for item in raw):
        raise ValueError(f"{benchmark} profile entry must define fact_tables as a non-empty list of table names")
    return frozenset(item.upper() for item in raw)


def _parse_candidate(
    benchmark: str,
    data: Any,
    evidence_source: str,
    seen_keys: set[tuple[str, str, str]],
    fact_tables: frozenset[str],
) -> WorkloadTuningCandidate:
    if not isinstance(data, dict):
        raise ValueError(f"{benchmark} candidate must be a mapping")

    table = _required_str(data, "table").upper()
    column = _required_str(data, "column").upper()
    column_type = _required_str(data, "type").upper()
    roles = tuple(str(role) for role in data.get("roles", ()))
    status = _required_str(data, "status")
    rationale = _required_str(data, "rationale")
    query_count = data.get("query_count")

    key = (benchmark, table, column)
    if key in seen_keys:
        raise ValueError(f"duplicate tuning profile candidate: {benchmark}.{table}.{column}")
    seen_keys.add(key)

    if status not in VALID_STATUSES:
        raise ValueError(f"{benchmark}.{table}.{column} has invalid status {status!r}")
    if not roles:
        raise ValueError(f"{benchmark}.{table}.{column} must define at least one role")
    invalid_roles = sorted(set(roles) - VALID_ROLES)
    if invalid_roles:
        raise ValueError(f"{benchmark}.{table}.{column} has invalid roles: {invalid_roles}")
    if not isinstance(query_count, int) or query_count < 0:
        raise ValueError(f"{benchmark}.{table}.{column} query_count must be a non-negative integer")

    query_ids = tuple(str(query_id) for query_id in data.get("query_ids", ()))
    if len(query_ids) != query_count:
        raise ValueError(f"{benchmark}.{table}.{column} query_ids must match query_count")
    if status == ACCEPTED and query_count < 3:
        raise ValueError(f"{benchmark}.{table}.{column} accepted candidates require query_count >= 3")
    if status == DROPPED_LOW_EVIDENCE and query_count >= 3:
        raise ValueError(f"{benchmark}.{table}.{column} dropped candidates must have query_count < 3")

    return WorkloadTuningCandidate(
        benchmark=benchmark,
        table=table,
        column=column,
        type=column_type,
        roles=roles,
        query_count=query_count,
        query_ids=query_ids,
        status=status,
        rationale=rationale,
        evidence_source=evidence_source,
        fact_table=table in fact_tables,
    )


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required string field: {key}")
    return value.strip()
