from __future__ import annotations

from dataclasses import dataclass

from benchbox.core.ai_primitives.benchmark import SUPPORTED_PLATFORMS as AI_PRIMITIVES_SUPPORTED_PLATFORMS
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

_DELTA_ICEBERG_REQUIRED_REASON = (
    "{engine}'s default parquet tables reject row-level DML: UPDATE, DELETE, and MERGE fail, and the staging "
    "manifest cannot be maintained (the staging `Count probe` fails). The release UAT runs the default table "
    "format and fetches no Delta Lake or Iceberg packages, so the benchmark cannot run in the gate. A run that "
    "configures a delta or iceberg table format with the connector jars is not blocked."
)

_MEMORY_CONNECTOR_DML_REASON = (
    "{engine}'s memory catalog, the only data catalog in the release UAT stack, rejects UPDATE, DELETE, and "
    "MERGE (`This connector does not support modifying table rows`), so the staging manifest cannot be "
    "maintained and the benchmark has no statement it can run. A run against a Hive, Iceberg, or Delta catalog "
    "is not blocked."
)

_NATIVE_MODEL_BENCHMARK_GATES = {
    **{
        (platform, benchmark): _MEMORY_CONNECTOR_DML_REASON.format(engine=engine)
        for platform, engine in (("presto", "Presto"), ("trino", "Trino"))
        for benchmark in ("write_primitives", "transaction_primitives")
    },
    **{
        (platform, benchmark): _DELTA_ICEBERG_REQUIRED_REASON.format(engine=engine)
        for platform, engine in (("spark", "Apache Spark"), ("velox", "Velox"))
        for benchmark in ("write_primitives", "transaction_primitives")
    },
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
    ("clickhouse-server", "transaction_primitives"): (
        "ClickHouse 25.8.33.6 MergeTree tables have no multi-statement transactions: every transaction_primitives "
        "operation opens with BEGIN, which the server rejects with Code 48 NOT_IMPLEMENTED (`Transactions are not "
        "supported`), and `SET TRANSACTION ISOLATION LEVEL ...` fails with Code 62 SYNTAX_ERROR (`Expected "
        "SNAPSHOT`). All 24 operations fail, so the benchmark has nothing to measure."
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
    "benchbox/core/transaction_primitives/catalog operations (BEGIN on ClickHouse Server 25.8.33.6, Code 48); "
    "benchbox/core/vector_search/queries.py and schema_specs.yaml"
)

_AI_PRIMITIVES_NO_AI_FUNCTIONS_REASON = (
    "AI primitives executes platform-hosted AI/ML SQL functions (Snowflake Cortex, BigQuery ML, Databricks AI "
    "functions) and a run on any other platform executes zero queries, so the benchmark has nothing to measure."
)
_AI_PRIMITIVES_NO_AI_FUNCTIONS_EVIDENCE = (
    "benchbox/core/ai_primitives/specs.yaml supported_platforms; "
    "benchbox/platforms/base/execution.py zero-query run failure"
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

_SQLITE_TPCDS_OBT_RUNTIME_ENVELOPE_REASON = (
    "SQLite TPC-DS OBT SF1 cannot meet the 1200s release-gate cell contract on the native UAT host. "
    "The 2026-08-25 probe loaded 1,325,000 of 5,041,336 rows from the 518-column artifact in 304.9s, "
    "projecting 1,391s for loading alone. This is the SQLite bind/WAL path, not an index or SQL-compatibility "
    "issue. Keep it pruned until a bounded atomic bulk loader fits the declared budget."
)

_SQLITE_TPCDS_OBT_SF01_RUNTIME_ENVELOPE_REASON = (
    "SQLite TPC-DS OBT SF0.1 cannot meet the 1200s release-gate cell contract on the native UAT host. "
    "The 2026-11-11 run loaded 503,753 rows in 103.7s, then query 35 was still running when the cell reached "
    "1200s; SF0.01 completed in 123s. Keep SF0.1 pruned until the OBT queries fit the budget on SQLite."
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

_PYSPARK_DF_TPCHAVOC_SF01_RUNTIME_ENVELOPE_REASON = (
    "pyspark-df TPC-Havoc SF0.1 cannot reliably meet the 1200s release-gate cell contract: passing cells took "
    "692-966s and two runs timed out at 1200s. SF1 is already outside the envelope. SF0.01 stays in the gate."
)

_SF1_TIMEOUT_RUNNER_CLAUSE = "measured on the 4 vCPU / 16 GB release runner (release UAT dry run 2, run 41163626947). SF0.01 and SF0.1 stay in the gate."

_DASK_DF_SF1_RUNTIME_ENVELOPE_REASON = (
    "dask-df SF1 cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s, "
    + _SF1_TIMEOUT_RUNNER_CLAUSE
)

_PYSPARK_DF_SF1_RUNTIME_ENVELOPE_REASON = (
    "pyspark-df SF1 cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s, "
    + _SF1_TIMEOUT_RUNNER_CLAUSE
)

_SPARK_SF1_RUNTIME_ENVELOPE_REASON = (
    "spark SF1 cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s with peak RSS "
    "near the 13 GiB cell cap, " + _SF1_TIMEOUT_RUNNER_CLAUSE
)

_PANDAS_DF_SF1_MEMORY_RUNTIME_ENVELOPE_REASON = (
    "pandas-df SF1 cannot stay under the 13 GiB per-cell memory guard on the 4 vCPU / 16 GB release runner: the guard "
    "killed flightdata at 13.09 GiB and datavault at 13.22 GiB (release UAT dry run 2, run 41163626947). "
    "SF0.01 and SF0.1 stay in the gate."
)

_PANDAS_DF_SF1_MEMORY_RUNTIME_ENVELOPES = {
    ("pandas-df", benchmark, 1.0): _PANDAS_DF_SF1_MEMORY_RUNTIME_ENVELOPE_REASON
    for benchmark in ("flightdata", "datavault")
}


def _sf1_timeout_reason(platform: str) -> str:
    return (
        f"{platform} SF1 cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s, "
        + _SF1_TIMEOUT_RUNNER_CLAUSE
    )


_SINGLESTORE_JOINORDER_RUNTIME_ENVELOPE_REASON = (
    "singlestore JoinOrder SF1 cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s, "
    "measured on the 4 vCPU / 16 GB release runner (release UAT dry run 2, run 41163626947). On the 4 GiB "
    "SingleStore 10.9.0 stack the 74.19M-row load now completes in about 7.5 minutes, but query 7c's join "
    "exceeds the container memory limit and the kernel OOM-kills memsqld (anon-rss 2.87 GB), so every later "
    "query fails with 1735/1777. JoinOrder is fixed at the IMDb data set, so no smaller scale exists."
)

_CEDARDB_TPCDS_OBT_SF1_DISK_ENVELOPE_REASON = (
    "cedardb TPC-DS OBT SF1 cannot be loaded within the release-host disk: the 5,041,336-row, 715-column table "
    "exhausted the 144 GiB runner disk in release UAT dry runs 3 and 4. A local SF1 Parquet COPY with the pinned "
    "image and an 8 GiB memory limit grew the data directory to 17.6 GB at 1,048,577 rows and kept growing, so the "
    "full load needs well over 80 GB. SF0.01 and SF0.1 stay in the gate."
)

_CEDARDB_RUNTIME_ENVELOPE_REASON = (
    "cedardb {benchmark} cannot meet the 1200s release-gate cell contract: the cell timed out at 1200s at SF0.01 "
    "and SF0.1 on the 8 GiB container stack on the 4 vCPU / 16 GB release runner (release UAT dry run 5, run "
    "41309974530), so every scale is out of the envelope."
)

_MEMORY_CATALOG_JOINORDER_RUNTIME_ENVELOPE_REASON = (
    "{engine} JoinOrder cannot meet the 1200s release-gate cell contract on the memory catalog, the only data "
    "catalog in the release UAT stack: the IMDb data set is about 74M rows at every scale factor, beyond the "
    "connector's per-node data cap, and row-wise inserts cannot finish within 1200s. A Hive or Iceberg catalog "
    "is not blocked."
)

_SF1_TIMEOUT_PRUNED_CELLS = (
    (
        "dask-df",
        _DASK_DF_SF1_RUNTIME_ENVELOPE_REASON,
        (
            "joinorder",
            "tpchavoc",
            "tpch_skew",
            "flightdata",
            "tpcds",
            "clickbench",
            "datavault",
            "coffeeshop",
            "tpch",
        ),
    ),
    (
        "pyspark-df",
        _PYSPARK_DF_SF1_RUNTIME_ENVELOPE_REASON,
        (
            "datavault",
            "tpchavoc",
            "tpcds",
            "joinorder",
            "flightdata",
        ),
    ),
    ("spark", _SPARK_SF1_RUNTIME_ENVELOPE_REASON, ("joinorder", "tpchavoc", "tpcds_obt")),
    ("pandas-df", _sf1_timeout_reason("pandas-df"), ("joinorder",)),
    ("sqlite", _sf1_timeout_reason("sqlite"), ("joinorder", "flightdata", "tsbs_devops")),
    ("lakesail", _sf1_timeout_reason("lakesail"), ("datavault",)),
    ("pg-duckdb", _sf1_timeout_reason("pg-duckdb"), ("joinorder",)),
    ("postgresql", _sf1_timeout_reason("postgresql"), ("joinorder",)),
    ("questdb", _sf1_timeout_reason("questdb"), ("joinorder",)),
)

_SF1_ONLY_BENCHMARKS = frozenset({"joinorder"})

_SF1_TIMEOUT_RUNTIME_ENVELOPES = {
    ((platform, benchmark) if benchmark in _SF1_ONLY_BENCHMARKS else (platform, benchmark, 1.0)): reason
    for platform, reason, benchmarks in _SF1_TIMEOUT_PRUNED_CELLS
    for benchmark in benchmarks
}

_SF1_TIMEOUT_RUNTIME_ENVELOPE_EVIDENCE = dict.fromkeys(
    _SF1_TIMEOUT_RUNTIME_ENVELOPES, "release UAT dry run 2 (run 41163626947) SF1 timing: terminal=timeout at 1200s"
)

_LATER_SF1_TIMEOUT_PRUNED_CELLS = (
    (
        "release UAT dry run 4 (run 41255388284) SF1 timing: terminal=timeout at 1200s",
        (
            ("sqlite", ("tpch", "tpch_skew")),
            ("dask-df", ("nyctaxi",)),
            ("starrocks", ("tpcds_obt", "write_primitives")),
        ),
    ),
    (
        "release UAT dry runs 5 (run 41309974530) and 6 (run 41317250643) SF1 timing: terminal=timeout at 1200s",
        (
            ("dask-df", ("amplab", "tsbs_devops")),
            ("clickhouse-server", ("tpcds_obt", "write_primitives", "flightdata")),
            ("starrocks", ("joinorder",)),
            ("velox", ("joinorder",)),
        ),
    ),
    (
        "release UAT dry run 7 (run 41364936974) SF1 timing: terminal=timeout at 1200s",
        (("timescaledb", ("joinorder",)),),
    ),
    (
        "release-v2 run 41738182507 for v0.5.0rc3 SF1 timing: terminal=timeout at 1200s "
        "(passing dry runs took 1028-1057s)",
        (("spark", ("datavault",)),),
    ),
)

_LATER_SF1_TIMEOUT_CELL_EVIDENCE = {
    ((platform, benchmark) if benchmark in _SF1_ONLY_BENCHMARKS else (platform, benchmark, 1.0)): evidence
    for evidence, groups in _LATER_SF1_TIMEOUT_PRUNED_CELLS
    for platform, benchmarks in groups
    for benchmark in benchmarks
}

_LATER_SF1_TIMEOUT_RUNTIME_ENVELOPES = {key: _sf1_timeout_reason(key[0]) for key in _LATER_SF1_TIMEOUT_CELL_EVIDENCE}

_RELEASE_GATE_RUNTIME_ENVELOPES = (
    _SF1_TIMEOUT_RUNTIME_ENVELOPES
    | _PANDAS_DF_SF1_MEMORY_RUNTIME_ENVELOPES
    | _LATER_SF1_TIMEOUT_RUNTIME_ENVELOPES
    | {
        ("sqlite", "datavault", 1.0): _SQLITE_DATAVAULT_SF1_RUNTIME_ENVELOPE_REASON,
        ("sqlite", "tpchavoc", 1.0): _SQLITE_TPCHAVOC_SF1_RUNTIME_ENVELOPE_REASON,
        ("datafusion", "datavault"): _DATAFUSION_DATAVAULT_RUNTIME_ENVELOPE_REASON,
        ("sqlite", "tpcds"): _SQLITE_TPCDS_RUNTIME_ENVELOPE_REASON,
        ("sqlite", "tpcds_obt", 0.1): _SQLITE_TPCDS_OBT_SF01_RUNTIME_ENVELOPE_REASON,
        ("sqlite", "tpcds_obt", 1.0): _SQLITE_TPCDS_OBT_RUNTIME_ENVELOPE_REASON,
        ("cedardb", "tpcds_obt", 1.0): _CEDARDB_TPCDS_OBT_SF1_DISK_ENVELOPE_REASON,
        **{
            ("cedardb", benchmark): _CEDARDB_RUNTIME_ENVELOPE_REASON.format(benchmark=label)
            for benchmark, label in (("tpchavoc", "TPC-Havoc"), ("tpch_skew", "TPC-H Skew"))
        },
        **{
            (platform, "joinorder"): _MEMORY_CATALOG_JOINORDER_RUNTIME_ENVELOPE_REASON.format(engine=engine)
            for platform, engine in (("presto", "Presto"), ("trino", "Trino"))
        },
        ("singlestore", "joinorder"): _SINGLESTORE_JOINORDER_RUNTIME_ENVELOPE_REASON,
        ("pyspark-df", "tpchavoc", 0.1): _PYSPARK_DF_TPCHAVOC_SF01_RUNTIME_ENVELOPE_REASON,
    }
)

_RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE = (
    _SF1_TIMEOUT_RUNTIME_ENVELOPE_EVIDENCE
    | _LATER_SF1_TIMEOUT_CELL_EVIDENCE
    | dict.fromkeys(
        _PANDAS_DF_SF1_MEMORY_RUNTIME_ENVELOPES,
        "release UAT dry run 2 (run 41163626947) SF1 memory: UAT_MEMORY_LIMIT limit_gib=13 peak_gib=13.09 / 13.22",
    )
    | {
        ("datafusion", "datavault"): "docs/internal/uat-release-campaign.md: DataFusion DataVault (2026-08-25)",
        ("sqlite", "tpcds"): "docs/internal/uat-release-campaign.md: SQLite TPC-DS (2026-08-25)",
        ("sqlite", "tpcds_obt", 0.1): "docs/internal/uat-release-campaign.md: SQLite TPC-DS OBT (2026-11-11)",
        ("sqlite", "tpcds_obt", 1.0): "PR #1904; docs/internal/uat-release-campaign.md: SQLite TPC-DS OBT (2026-08-25)",
        (
            "singlestore",
            "joinorder",
        ): "release UAT dry run 2 (run 41163626947) terminal=timeout at 1200s; local SF1 rerun 2026-11-07: kernel oom-kill of memsqld during query 7c on the 4 GiB stack",
        (
            "sqlite",
            "datavault",
            1.0,
        ): "release-v2 run 40701870861 stage 1 (1200s timeout); local SF1 profile 2026-10-29",
        (
            "sqlite",
            "tpchavoc",
            1.0,
        ): "release-v2 run 40701870861 stage 1 (1200s timeout); local SF0.1 timing 2026-10-29",
        (
            "pyspark-df",
            "tpchavoc",
            0.1,
        ): "release-v2 run 41842316367 for v0.5.0rc4 and a release UAT dry run: terminal=timeout at 1200s",
        (
            "cedardb",
            "tpcds_obt",
            1.0,
        ): "release UAT dry runs 3 (run 41243890531) and 4 (run 41255388284): runner disk exhausted; local SF1 Parquet COPY reproduction: 17.6 GB at 1,048,577 rows",
        ("cedardb", "tpchavoc"): "release UAT dry run 5 (run 41309974530) stage 2 cedardb: terminal=timeout at 1200s",
        ("cedardb", "tpch_skew"): "release UAT dry run 5 (run 41309974530) stage 2 cedardb: terminal=timeout at 1200s",
        (
            "presto",
            "joinorder",
        ): "release UAT dry runs 2 (run 41163626947) and 3 (run 41243890531); local Presto 0.298.1 load profile 2026-11-01",
        (
            "trino",
            "joinorder",
        ): "release UAT dry runs 2 (run 41163626947) and 3 (run 41243890531); local Trino 484 load profile 2026-11-01",
    }
)


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
    if benchmark == "ai_primitives" and platform not in AI_PRIMITIVES_SUPPORTED_PLATFORMS:
        return _benchmark_gate_rule(
            platform,
            benchmark,
            reason=_AI_PRIMITIVES_NO_AI_FUNCTIONS_REASON,
            evidence=_AI_PRIMITIVES_NO_AI_FUNCTIONS_EVIDENCE,
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
