from __future__ import annotations

from dataclasses import dataclass

from benchbox.core.platform_registry import PlatformRegistry
from tests.uat.matrix import DATAFRAME_PLATFORMS, BenchmarkInfo


@dataclass(frozen=True)
class CompatibilityRule:
    rule_id: str
    status: str
    reason: str
    evidence: str


DATAFRAME_SQL_ONLY_RULE = CompatibilityRule(
    rule_id="uat.compat.dataframe.sql_only_benchmark",
    status="blocked",
    reason="DataFrame platforms cannot execute SQL-only benchmark implementations.",
    evidence="benchmark registry `supports_dataframe` metadata",
)

_NATIVE_MODEL_BENCHMARK_GATES = {
    ("sqlite", "vector_search"): (
        "SQLite lacks vector similarity/ANN support; vector_search has no SQLite query variant and uses "
        "DuckDB-specific array syntax."
    ),
    ("datafusion", "write_primitives"): (
        "DataFusion UAT runs through the in-memory SessionContext adapter; the adapter only rewrites "
        "write_primitives bulk-load operations and does not expose the durable row-level DML contract "
        "needed by the full write-primitives workload."
    ),
    ("datafusion", "transaction_primitives"): (
        "Transaction primitives requires ACID transaction semantics. The DataFusion UAT adapter is an "
        "in-memory analytical query path with no DBAPI transaction contract."
    ),
    ("datafusion", "ai_primitives"): (
        "AI primitives requires platform AI/ML SQL functions; DataFusion is listed in the AI primitives "
        "unsupported platform set."
    ),
    ("datafusion", "vector_search"): (
        "Vector search requires vector/array similarity functions and ANN-index semantics. The DataFusion "
        "UAT adapter has no vector-search dialect implementation."
    ),
    ("clickhouse-local", "write_primitives"): (
        "clickhouse-local uses embedded chDB in-process mode; the UAT local path has no durable server-side "
        "write contract for the full write-primitives staging workload."
    ),
    ("clickhouse-local", "transaction_primitives"): (
        "Transaction primitives requires BEGIN/COMMIT/ROLLBACK-style ACID semantics; clickhouse-local "
        "uses embedded chDB analytical execution, not a transactional DBAPI contract."
    ),
    ("clickhouse-local", "ai_primitives"): (
        "AI primitives requires platform AI/ML SQL functions; ClickHouse is listed in the AI primitives "
        "unsupported platform set."
    ),
}

_NATIVE_MODEL_BENCHMARK_EVIDENCE = (
    "benchbox/core/ai_primitives/specs.yaml unsupported_platforms; "
    "benchbox/platforms/datafusion.py DataFusionConnectionCompat; "
    "benchbox/platforms/clickhouse_local.py embedded chDB local adapter; "
    "benchbox/core/vector_search/queries.py and schema_specs.yaml"
)

_DATAFRAME_TRANSACTIONAL_BENCHMARK_GATES = {
    "transaction_primitives": (
        "Transaction primitives requires ACID transaction semantics. The UAT DataFrame selector set does "
        "not include a Delta Lake or Iceberg transactional table-format platform."
    ),
    "tpcdi": (
        "TPC-DI DataFrame ETL requires transactional maintenance capabilities for incremental/SCD2 writes; "
        "the UAT DataFrame selector set does not provide a transactional table-format backend."
    ),
}

_DATAFRAME_TRANSACTIONAL_EVIDENCE = (
    "benchbox/core/transaction_primitives/dataframe_operations.py "
    "validate_transaction_primitives_platform; "
    "benchbox/core/tpcdi/benchmark.py execute_dataframe_workload transactional capability gate; "
    "benchbox/platforms/dataframe/pyspark_maintenance.py PySpark Parquet capability profile"
)

_DATAFRAME_WRITE_MANAGER_UNSUPPORTED = frozenset({"dask-df", "datafusion-df"})
_DATAFRAME_WRITE_MANAGER_REASON = (
    "Write primitives DataFrame mode is currently implemented only for Polars, Pandas, and PySpark managers; "
    "this UAT platform has no write manager for the benchmark."
)
_DATAFRAME_WRITE_MANAGER_EVIDENCE = (
    "benchbox/core/write_primitives/dataframe_operations.py get_dataframe_write_manager supported_platforms"
)

_JOINORDER_RUNTIME_ENVELOPE_REASON = (
    "Canonical JoinOrder uses the full IMDb 2013 dataset at scale 1.0 only. A targeted 2026-05-14 "
    "pg-duckdb UAT rerun loaded and validated the dataset, then timed out at the 1200s release-gate "
    "budget on warm-up query 7a after the first 23 queries completed. The benchmark has no smaller "
    "public scale; use joinorder_synthetic for PostgreSQL-family smoke coverage."
)

_TPCDS_OBT_RUNTIME_ENVELOPE_REASON = (
    "TPC-DS OBT is currently scale-1.0-only and materializes a very wide sales/returns table. A targeted "
    "2026-05-14 pg-duckdb UAT rerun with delimited output loaded and validated the table, but query 1 "
    "took 5:14, query 2 failed on PostgreSQL round(double precision, integer), and query 4 timed out at "
    "the 2400s diagnostic budget. The parquet path also requires PostgreSQL-family loader fallbacks. "
    "Until the benchmark has a subscale or PostgreSQL-family OBT query rewrites, it is outside the local "
    "enabled-platform release-gate envelope."
)

_TIMESCALEDB_DATAVAULT_RUNTIME_ENVELOPE_REASON = (
    "TimescaleDB loaded and row-count validated DataVault SF 0.01 in the 2026-05-14 full UAT gate, "
    "but timed out at the 1200s release-gate budget during warm-up query 17 after query 5 alone took "
    "2:26.8. LakeSail, pg-duckdb, and pg-mooncake cleared the same DataVault cell, so this is a "
    "TimescaleDB-specific runtime envelope failure for the current release gate."
)

_SQLITE_TPCDS_OBT_RUNTIME_ENVELOPE_REASON = (
    "SQLite TPC-DS OBT SF1 cannot meet the 1200s release-gate cell contract on the native UAT host. "
    "The 2026-08-25 probe loaded 1,325,000 of 5,041,336 rows from the 518-column artifact in 304.9s, "
    "projecting 1,391s for loading alone. This is the SQLite bind/WAL path, not an index or SQL-compatibility "
    "issue. Keep it pruned until a bounded atomic bulk loader fits the declared budget."
)

_SQLITE_TPCDS_RUNTIME_ENVELOPE_REASON = (
    "SQLite TPC-DS missed the 1200s release-gate contract at all three scales on 2026-08-25. "
    "At SF0.01, query 13 and query 48 exceeded 300s; eleven queries also need unsupported ROLLUP semantics. "
    "Keep the cells pruned until bounded query rewrites or planner improvements fit the budget."
)
_DATAFUSION_DATAVAULT_RUNTIME_ENVELOPE_REASON = (
    "DataFusion DataVault query 18 at SF1 is outside the 16 GiB release-host envelope measured on 2026-08-25. "
    "Stage 1 passed SF0.01 and SF0.1, but SF1 was killed; with PR #1914 spilling active, a 12 GiB pool "
    "was also host-killed. Diagnostic runs retain every scale."
)

_PG_FAMILY_RELEASE_GATE_RUNTIME_ENVELOPES = (
    {
        (platform, "joinorder"): _JOINORDER_RUNTIME_ENVELOPE_REASON
        for platform in ("pg-duckdb", "pg-mooncake", "timescaledb")
    }
    | {
        (platform, "tpcds_obt"): _TPCDS_OBT_RUNTIME_ENVELOPE_REASON
        for platform in ("pg-duckdb", "pg-mooncake", "timescaledb")
    }
    | {("timescaledb", "datavault"): _TIMESCALEDB_DATAVAULT_RUNTIME_ENVELOPE_REASON}
)

_SQLITE_DATAVAULT_SF1_RUNTIME_ENVELOPE_REASON = (
    "SQLite DataVault SF1 cannot meet the 1200s release-gate cell contract. With hash-key indexes, a profile of "
    "the 22 queries at a 40s per-query cap took at least 475s per pass: queries 3, 5, 7, 9, 14, 15 and 18 each hit "
    "the cap, and 1, 10 and 19 took about 32s. Three iterations exceed 1425s before load time, against the 1200s "
    "limit; a full run was killed at 27 minutes. Larger mmap and cache settings did not help query 14. SF0.01 and "
    "SF0.1 stay in the gate."
)

_SQLITE_TPCHAVOC_SF1_RUNTIME_ENVELOPE_REASON = (
    "SQLite TPC-Havoc SF1 cannot meet the 1200s release-gate cell contract. SF0.1 already takes about 100s per "
    "pass over the non-skipped variants (4m21s for the full cell), and the 1200s limit was exceeded at SF1 in "
    "release-v2 run 40701870861. SF1 scales the same joins roughly tenfold. SF0.01 and SF0.1 stay in the gate."
)

_RELEASE_GATE_RUNTIME_ENVELOPES = _PG_FAMILY_RELEASE_GATE_RUNTIME_ENVELOPES | {
    ("sqlite", "datavault", 1.0): _SQLITE_DATAVAULT_SF1_RUNTIME_ENVELOPE_REASON,
    ("sqlite", "tpchavoc", 1.0): _SQLITE_TPCHAVOC_SF1_RUNTIME_ENVELOPE_REASON,
    ("datafusion", "datavault"): _DATAFUSION_DATAVAULT_RUNTIME_ENVELOPE_REASON,
    ("sqlite", "tpcds"): _SQLITE_TPCDS_RUNTIME_ENVELOPE_REASON,
    ("sqlite", "tpcds_obt"): _SQLITE_TPCDS_OBT_RUNTIME_ENVELOPE_REASON,
}

_RELEASE_GATE_STAGES_UNUSED_RUNTIME_ENVELOPES = frozenset(_PG_FAMILY_RELEASE_GATE_RUNTIME_ENVELOPES)

_RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE = {
    ("datafusion", "datavault"): "docs/internal/uat-release-campaign.md: DataFusion DataVault (2026-08-25)",
    ("sqlite", "tpcds"): "docs/internal/uat-release-campaign.md: SQLite TPC-DS (2026-08-25)",
    ("sqlite", "tpcds_obt"): "PR #1904; docs/internal/uat-release-campaign.md: SQLite TPC-DS OBT (2026-08-25)",
    ("sqlite", "datavault", 1.0): "release-v2 run 40701870861 stage 1 (1200s timeout); local SF1 profile 2026-10-29",
    ("sqlite", "tpchavoc", 1.0): "release-v2 run 40701870861 stage 1 (1200s timeout); local SF0.1 timing 2026-10-29",
}


def compatibility_rule_for(
    platform: str,
    benchmark: str,
    info: BenchmarkInfo,
    *,
    include_release_gate_runtime_envelopes: bool = False,
    scale: float | None = None,
) -> CompatibilityRule | None:
    native_reason = _NATIVE_MODEL_BENCHMARK_GATES.get((platform, benchmark))
    if native_reason:
        return _benchmark_gate_rule(
            platform,
            benchmark,
            reason=native_reason,
            evidence=_NATIVE_MODEL_BENCHMARK_EVIDENCE,
        )
    if platform in DATAFRAME_PLATFORMS and _is_sql_only_benchmark(info):
        return DATAFRAME_SQL_ONLY_RULE
    if platform in DATAFRAME_PLATFORMS:
        transactional_reason = _DATAFRAME_TRANSACTIONAL_BENCHMARK_GATES.get(benchmark)
        if transactional_reason:
            return _benchmark_gate_rule(
                platform,
                benchmark,
                reason=transactional_reason,
                evidence=_DATAFRAME_TRANSACTIONAL_EVIDENCE,
            )
        if platform in _DATAFRAME_WRITE_MANAGER_UNSUPPORTED and benchmark == "write_primitives":
            return _benchmark_gate_rule(
                platform,
                benchmark,
                reason=_DATAFRAME_WRITE_MANAGER_REASON,
                evidence=_DATAFRAME_WRITE_MANAGER_EVIDENCE,
            )
    if include_release_gate_runtime_envelopes:
        envelope_key: tuple = (platform, benchmark)
        scale_suffix = ""
        runtime_envelope_reason = _RELEASE_GATE_RUNTIME_ENVELOPES.get(envelope_key)
        if runtime_envelope_reason is None and scale is not None:
            envelope_key = (platform, benchmark, float(scale))
            scale_suffix = f".sf{float(scale):g}"
            runtime_envelope_reason = _RELEASE_GATE_RUNTIME_ENVELOPES.get(envelope_key)
        if runtime_envelope_reason:
            runtime_envelope_evidence = _RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE.get(
                envelope_key,
                "2026-05-14 enabled-platform UAT release-gate audit; not a runtime compatibility gate",
            )
            return CompatibilityRule(
                rule_id=f"uat.compat.{platform}.{benchmark}{scale_suffix}.release_gate_runtime_envelope",
                status="blocked",
                reason=runtime_envelope_reason,
                evidence=runtime_envelope_evidence,
            )
    caps = PlatformRegistry.get_platform_capabilities(platform)
    unsupported_benchmarks = getattr(caps, "unsupported_benchmarks", {}) if caps else {}
    reason = unsupported_benchmarks.get(benchmark)
    if reason:
        return _benchmark_gate_rule(
            platform,
            benchmark,
            reason=reason,
            evidence="benchbox.sql_compat benchmark_gate registry via PlatformRegistry.unsupported_benchmarks",
        )
    return None


def _benchmark_gate_rule(platform: str, benchmark: str, *, reason: str, evidence: str) -> CompatibilityRule:
    return CompatibilityRule(
        rule_id=f"uat.compat.{platform}.{benchmark}.benchmark_gate",
        status="blocked",
        reason=reason,
        evidence=evidence,
    )


def _is_sql_only_benchmark(info: BenchmarkInfo) -> bool:
    return not info.supports_dataframe
