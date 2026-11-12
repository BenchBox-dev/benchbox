from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.tuning.applied_ledger import HARNESS_DEFAULT, PHASE_SESSION

GRACE_HASH_JOIN: Mapping[str, str | int] = {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8}


@dataclass(frozen=True)
class StatementSettingRequirement:
    benchmark: frozenset[str]
    query_ids: frozenset[str]
    deployment_mode: str
    settings: Mapping[str, str | int]
    reason: str


HARNESS_STATEMENT_SETTINGS: tuple[StatementSettingRequirement, ...] = (
    StatementSettingRequirement(
        benchmark=frozenset({"tpchavoc"}),
        query_ids=frozenset({"5_v7", "5_v8"}),
        deployment_mode="local",
        settings=GRACE_HASH_JOIN,
        reason="5_v8 exceeds the 8 GiB clickhouse-local query memory limit (Code 241) without a spilling join",
    ),
    StatementSettingRequirement(
        benchmark=frozenset({"tpch"}),
        query_ids=frozenset({"21"}),
        deployment_mode="server",
        settings={"join_algorithm": "hash"},
        reason="Q21 exceeds the 5.25 GiB server memory rung (Code 241) with the default parallel hash join",
    ),
    StatementSettingRequirement(
        benchmark=frozenset({"datavault", "data_vault"}),
        query_ids=frozenset({"5", "21"}),
        deployment_mode="server",
        settings=GRACE_HASH_JOIN,
        reason="Data Vault Q5 and Q21 need a spilling join at the server memory rung",
    ),
)


def _normalize_query_id(query_id: object) -> str:
    return str(query_id).lower().removeprefix("q")


def required_statement_settings(
    benchmark_type: str | None, query_id: object, deployment_mode: str
) -> dict[str, str | int]:
    benchmark = (benchmark_type or "").lower()
    normalized = _normalize_query_id(query_id)
    for requirement in HARNESS_STATEMENT_SETTINGS:
        if (
            requirement.deployment_mode == deployment_mode
            and benchmark in requirement.benchmark
            and normalized in requirement.query_ids
        ):
            return dict(requirement.settings)
    return {}


def record_harness_statement_settings(
    ledger: Any, benchmark_type: str | None, query_id: object, settings: Mapping[str, str | int]
) -> None:
    if ledger is None:
        return
    rendered = ", ".join(
        f"{name} = {value!r}" if isinstance(value, str) else f"{name} = {value}" for name, value in settings.items()
    )
    statement = f"{benchmark_type} {query_id} SETTINGS {rendered}"
    if any(entry.statement == statement for entry in ledger.statements):
        return
    ledger.record(statement, PHASE_SESSION, status=HARNESS_DEFAULT, mechanism="harness_required")
