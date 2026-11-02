from __future__ import annotations

from collections.abc import Callable
from functools import cache

MOONCAKE_STAGING_PREFIX = "__bb_moon_src_"
STAGING_MANIFEST_TABLES = frozenset(
    {"benchbox_staging_manifest", "benchbox_staging_manifest_v2", "benchbox_staging_manifest_v3"}
)
WRITE_PRIMITIVES_SETUP_LOCK_TABLE = "write_primitives_setup_lock"
_PRIMITIVES_LOADED_STAGE_TABLES = ("orders_stage", "lineitem_stage")


def _tpch_tables() -> set[str]:
    from benchbox.core.tpch.schema import TABLES_BY_NAME

    return {name.lower() for name in TABLES_BY_NAME}


def _tpcds_tables() -> set[str]:
    from benchbox.core.tpcds.schema import TABLES_BY_NAME

    return {name.lower() for name in TABLES_BY_NAME}


def _read_primitives_tables() -> set[str]:
    from benchbox.core.read_primitives.schema import TABLES

    return {name.lower() for name in TABLES}


def _write_primitives_tables() -> set[str]:
    from benchbox.core.write_primitives.schema import STAGING_TABLES

    return {name.lower() for name in STAGING_TABLES} | {
        WRITE_PRIMITIVES_SETUP_LOCK_TABLE,
        *_PRIMITIVES_LOADED_STAGE_TABLES,
    }


def _transaction_primitives_tables() -> set[str]:
    from benchbox.core.transaction_primitives.schema import STAGING_TABLES

    return {name.lower() for name in STAGING_TABLES} | set(_PRIMITIVES_LOADED_STAGE_TABLES)


def _tpcds_obt_tables() -> set[str]:
    from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME

    return {OBT_TABLE_NAME.lower()}


_TABLE_PROVIDERS: dict[str, Callable[[], set[str]]] = {
    "tpch": _tpch_tables,
    "tpcds": _tpcds_tables,
    "read_primitives": _read_primitives_tables,
    "write_primitives": _write_primitives_tables,
    "transaction_primitives": _transaction_primitives_tables,
    "ai_primitives": _tpch_tables,
    "tpcds_obt": _tpcds_obt_tables,
}


def table_providers() -> dict[str, Callable[[], set[str]]]:
    return dict(_TABLE_PROVIDERS)


@cache
def tables_owned_by_benchmark_family(family: str) -> frozenset[str]:
    from benchbox.core.benchmark_registry import get_all_benchmarks

    family = family.lower()
    members = {family}
    members.update(
        benchmark_id
        for benchmark_id, metadata in get_all_benchmarks().items()
        if str(metadata.get("data_source") or "").lower() == family
    )
    owned: set[str] = set()
    for member in members:
        provider = _TABLE_PROVIDERS.get(member)
        if provider is not None:
            owned |= provider()
    return frozenset(owned)


def is_benchbox_staging_table(table_name: str) -> bool:
    name = table_name.lower()
    return name.startswith(MOONCAKE_STAGING_PREFIX) or name in STAGING_MANIFEST_TABLES


def benchmark_family_of(benchmark: object) -> str | None:
    data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
    if data_source:
        return str(data_source).lower()
    resolver = getattr(benchmark, "_get_benchmark_name", None)
    if callable(resolver):
        return str(resolver()).lower()
    return None
