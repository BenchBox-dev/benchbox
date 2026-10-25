from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from benchbox.utils.printing import emit

SCHEMA_VERSION = 1
DEFAULT_OUTPUT = "_project/compat/baseline.v1.jsonl"


@dataclass
class BaselineRecord:
    schema_version: int
    platform: str
    benchmark: str | None
    query_id: str | None
    phase: str
    mode: str
    source_sql_hash: str | None
    decision: str
    rule_id: str | None
    final_sql_hash: str | None
    benchmark_gate_outcome: str | None


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rec(
    platform: str,
    benchmark: str | None,
    query_id: str | None,
    phase: str,
    mode: str,
    decision: str,
    *,
    source_sql: str | None = None,
    final_sql: str | None = None,
    gate: str | None = None,
) -> BaselineRecord:
    return BaselineRecord(
        schema_version=SCHEMA_VERSION,
        platform=platform,
        benchmark=benchmark,
        query_id=query_id,
        phase=phase,
        mode=mode,
        source_sql_hash=_sha256(source_sql) if source_sql is not None else None,
        decision=decision,
        rule_id=None,
        final_sql_hash=_sha256(final_sql) if final_sql is not None else None,
        benchmark_gate_outcome=gate,
    )


def _h2o_q9_records() -> list[BaselineRecord]:
    from benchbox.core.h2odb.benchmark import H2OBenchmark
    from benchbox.core.h2odb.queries import H2OQueryManager

    default_q9 = H2OQueryManager().get_query("Q9")
    return [
        _rec(
            "clickhouse",
            "h2odb",
            "Q9",
            "query_source",
            "sql",
            "REWRITTEN",
            source_sql=default_q9,
            final_sql=H2OBenchmark._CLICKHOUSE_Q9,
        ),
        _rec(
            "starrocks",
            "h2odb",
            "Q9",
            "query_source",
            "sql",
            "REWRITTEN",
            source_sql=default_q9,
            final_sql=H2OBenchmark._STARROCKS_Q9,
        ),
    ]


def _vector_search_records() -> list[BaselineRecord]:
    from benchbox.core.vector_search.queries import _QUERIES, QUERY_VARIANTS

    records: list[BaselineRecord] = []
    for platform in sorted(QUERY_VARIANTS):
        for query_id in sorted(QUERY_VARIANTS[platform]):
            default_sql = _QUERIES[query_id]
            variant_sql = QUERY_VARIANTS[platform][query_id]
            records.append(
                _rec(
                    platform,
                    "vector_search",
                    query_id,
                    "query_source",
                    "sql",
                    "REWRITTEN",
                    source_sql=default_sql,
                    final_sql=variant_sql,
                )
            )
    return records


def _benchmark_gate_records() -> list[BaselineRecord]:
    from benchbox.core.platform_registry import PlatformRegistry

    records: list[BaselineRecord] = []
    for platform_key in sorted(PlatformRegistry.get_platform_names()):
        unsupported = PlatformRegistry.get_unsupported_benchmarks(platform_key)
        if not unsupported:
            continue
        for benchmark in sorted(unsupported):
            records.append(
                _rec(
                    platform_key,
                    benchmark,
                    None,
                    "benchmark_gate",
                    "sql",
                    "BLOCKED",
                    gate="blocked",
                )
            )
    return records


def _schema_emit_records() -> list[BaselineRecord]:
    sites: list[tuple[str, str]] = [
        ("clickhouse", "write_primitives"),
        ("datafusion", "write_primitives"),
        ("doris", "write_primitives"),
        ("starrocks", "write_primitives"),
        ("clickhouse", "transaction_primitives"),
        ("datafusion", "transaction_primitives"),
        ("doris", "transaction_primitives"),
        ("starrocks", "transaction_primitives"),
        ("clickhouse", "nyctaxi"),
        ("duckdb", "nyctaxi"),
        ("postgresql", "nyctaxi"),
        ("singlestore", "joinorder"),
        ("postgresql", "joinorder"),
        ("clickhouse", "tsbs_devops"),
    ]
    return [_rec(platform, benchmark, None, "schema_emit", "sql", "REWRITTEN") for platform, benchmark in sites]


def _ddl_optimize_records() -> list[BaselineRecord]:
    platforms = [
        "athena",
        "azure_synapse",
        "bigquery",
        "clickhouse",
        "databend",
        "databricks",
        "doris",
        "ducklake",
        "fabric_dw",
        "firebolt",
        "lakesail",
        "pg_mooncake",
        "postgresql",
        "presto",
        "questdb",
        "redshift",
        "singlestore",
        "snowflake",
        "spark",
        "starrocks",
        "trino",
        "velox",
    ]
    return [_rec(p, None, None, "ddl_optimize", "sql", "REWRITTEN") for p in platforms]


def _execution_skip_records() -> list[BaselineRecord]:
    return [
        _rec("lakesail", "read_primitives", None, "execution_filter", "sql", "SKIPPED"),
        _rec("datafusion", "read_primitives", None, "dataframe_filter", "dataframe", "SKIPPED"),
        _rec("polars", "read_primitives", None, "dataframe_filter", "dataframe", "SKIPPED"),
    ]


def _query_adapter_records() -> list[BaselineRecord]:
    return [
        _rec("clickhouse", None, None, "query_adapter", "sql", "REWRITTEN"),
        _rec("starrocks", None, None, "query_adapter", "sql", "REWRITTEN"),
        _rec("firebolt", None, None, "query_adapter", "sql", "REWRITTEN"),
        _rec("redshift", None, None, "query_adapter", "sql", "REWRITTEN"),
    ]


def generate() -> list[BaselineRecord]:
    records: list[BaselineRecord] = []
    records.extend(_h2o_q9_records())
    records.extend(_vector_search_records())
    records.extend(_benchmark_gate_records())
    records.extend(_schema_emit_records())
    records.extend(_ddl_optimize_records())
    records.extend(_execution_skip_records())
    records.extend(_query_adapter_records())

    seen: set[tuple[str, str | None, str | None, str, str]] = set()
    unique: list[BaselineRecord] = []
    for r in records:
        key = (r.platform, r.benchmark, r.query_id, r.phase, r.mode)
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(
        key=lambda r: (
            r.platform,
            r.benchmark or "",
            r.query_id or "",
            r.phase,
            r.mode,
        )
    )
    return unique


def write_jsonl(records: list[BaselineRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(asdict(record)) + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="BenchBox sql_compat baseline snapshot tool")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of emitted records after writing",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    emit("Generating baseline records ...", stderr=True)

    records = generate()
    write_jsonl(records, output)
    emit(f"Wrote {len(records)} records to {output}", stderr=True)

    if args.summary:
        from collections import Counter

        phase_counts = Counter(r.phase for r in records)
        decision_counts = Counter(r.decision for r in records)
        emit("\nPhase distribution:")
        for phase, count in sorted(phase_counts.items()):
            emit(f"  {phase:20s} {count}")
        emit("\nDecision distribution:")
        for decision, count in sorted(decision_counts.items()):
            emit(f"  {decision:20s} {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
