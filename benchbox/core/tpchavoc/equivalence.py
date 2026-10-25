# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.validation import ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from benchbox import TPCH

EQUIVALENCE_SCALE = 0.1
SUBSCALE_EQUIVALENCE_SCALE = 0.01

KNOWN_DIVERGENCES: dict[str, str] = {}

POSTGRES_KNOWN_DIVERGENCES: dict[str, str] = {}

DATAFUSION_KNOWN_DIVERGENCES: dict[str, str] = {
    "1_v4": "decimal-avg-scale-rounding",
}

DATAFUSION_KNOWN_DIVERGENCES_REVIEW_BY: dict[str, date] = {
    "1_v4": date(2027, 1, 24),
}

_TRAILING_LIMIT = re.compile(r"(?is)\s+limit\s+\d+\s*;?\s*$")


@dataclass(frozen=True)
class Divergence:
    query_id: int
    variant_id: int
    detail: str

    @property
    def key(self) -> str:
        return f"{self.query_id}_v{self.variant_id}"


def strip_top_n(sql: str) -> str:
    normalized = re.sub(r"\s*--[^\n]*$", "", sql.strip())
    normalized = normalized.rstrip().rstrip(";")
    return _TRAILING_LIMIT.sub("", normalized)


def _identity(sql: str) -> str:
    return sql


def _rstrip_char_cells(rows: list[tuple[Any, ...]], column_indexes: Collection[int]) -> list[tuple[Any, ...]]:
    indexes = set(column_indexes)
    return [
        tuple(
            cell.rstrip(" ") if index in indexes and isinstance(cell, str) else cell for index, cell in enumerate(row)
        )
        for row in rows
    ]


def find_divergences(
    connection: Any,
    benchmark: TPCHavocBenchmark,
    canonical_query: Callable[[int], str],
    *,
    query_ids: list[int] | None = None,
    translate_variant: Callable[[str], str] | None = None,
    variant_query: Callable[[int, int], str] | None = None,
    skip_variants: Collection[str] | None = None,
    execute_transform: Callable[[str], str] | None = None,
    char_padding_columns: Mapping[int, Collection[int]] | None = None,
) -> list[Divergence]:
    ids = query_ids if query_ids is not None else benchmark.get_implemented_queries()
    render_variant = translate_variant if translate_variant is not None else _identity
    transform_for_engine = execute_transform if execute_transform is not None else _identity
    excluded = set(skip_variants or ())
    divergences: list[Divergence] = []
    for query_id in ids:
        normalize = lambda rows: _rstrip_char_cells(rows, (char_padding_columns or {}).get(query_id, ()))
        try:
            original = normalize(
                connection.execute(transform_for_engine(strip_top_n(canonical_query(query_id)))).fetchall()
            )
        except Exception as exc:
            divergences.append(Divergence(query_id, 0, f"canonical query failed: {exc}"))
            continue
        if not original:
            divergences.append(
                Divergence(
                    query_id,
                    0,
                    "canonical query returned 0 rows - vacuous, would compare empty-vs-empty "
                    "for every variant; no TPC-H canonical query is expected to be empty at "
                    f"EQUIVALENCE_SCALE={EQUIVALENCE_SCALE}",
                )
            )
            continue
        for variant_id in range(1, 11):
            if f"{query_id}_v{variant_id}" in excluded:
                continue
            try:
                if variant_query is None:
                    rendered = render_variant(strip_top_n(benchmark.get_query(f"{query_id}_v{variant_id}")))
                else:
                    rendered = strip_top_n(variant_query(query_id, variant_id))
                variant_sql = transform_for_engine(rendered)
                variant_rows = normalize(connection.execute(variant_sql).fetchall())
                benchmark.validate_variant_equivalence(query_id, variant_id, original, variant_rows)
            except ValidationError as exc:
                divergences.append(Divergence(query_id, variant_id, str(exc)))
            except Exception as exc:
                divergences.append(Divergence(query_id, variant_id, f"error: {exc}"))
    return divergences


def _generate_tpch(scale_factor: float, output_dir: Path) -> tuple[Path, TPCHavocBenchmark, TPCH]:
    from benchbox import TPCH
    from benchbox.core.tpch.generator import TPCHDataGenerator

    generated = TPCHDataGenerator(scale_factor=scale_factor, output_dir=output_dir).generate()
    data_dir = next(Path(paths[0] if isinstance(paths, list) else paths).parent for paths in generated.values())
    tpchavoc = TPCHavocBenchmark(scale_factor=scale_factor, output_dir=output_dir)
    tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir)
    return data_dir, tpchavoc, tpch


def build_duckdb_with_tpch(scale_factor: float, output_dir: str | Path) -> tuple[Any, TPCHavocBenchmark, TPCH]:
    import duckdb

    from benchbox.platforms.duckdb import DuckDBAdapter

    data_dir, tpchavoc, tpch = _generate_tpch(scale_factor, Path(output_dir))

    connection = duckdb.connect(":memory:")
    try:
        for statement in tpchavoc.get_create_tables_sql(dialect="duckdb").strip().split(";"):
            if statement.strip():
                connection.execute(statement.strip())
        table_stats, _, _ = DuckDBAdapter(database=":memory:").load_data(tpchavoc, connection, data_dir)
        empty = [table for table in _TPCH_TABLES if table_stats.get(table, 0) <= 0]
        if empty:
            raise RuntimeError(f"DuckDB TPC-H load failed - no rows in {empty} (stats={table_stats})")
    except Exception:
        connection.close()
        raise
    return connection, tpchavoc, tpch


POSTGRES_TARGET_DIALECT = "postgres"

POSTGRES_STATEMENT_TIMEOUT_MS = 120_000

POSTGRES_TPCH_INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_region ON region(r_regionkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_nation ON nation(n_nationkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_part ON part(p_partkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_supplier ON supplier(s_suppkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_partsupp ON partsupp(ps_partkey, ps_suppkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_customer ON customer(c_custkey)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pk_orders ON orders(o_orderkey)",
    "CREATE INDEX IF NOT EXISTS ix_lineitem_orderkey ON lineitem(l_orderkey)",
    "CREATE INDEX IF NOT EXISTS ix_lineitem_partkey ON lineitem(l_partkey)",
    "CREATE INDEX IF NOT EXISTS ix_lineitem_suppkey ON lineitem(l_suppkey)",
    "CREATE INDEX IF NOT EXISTS ix_partsupp_suppkey ON partsupp(ps_suppkey)",
    "CREATE INDEX IF NOT EXISTS ix_orders_custkey ON orders(o_custkey)",
)

_TPCH_TABLES = ("lineitem", "orders", "partsupp", "part", "customer", "supplier", "nation", "region")


def postgres_connection_config() -> dict[str, Any]:
    import os

    return {
        "host": os.environ.get("PGHOST", os.environ.get("POSTGRES_HOST", "localhost")),
        "port": int(os.environ.get("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))),
        "username": os.environ.get("PGUSER", os.environ.get("POSTGRES_USER", "benchbox")),
        "password": os.environ.get("PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", "benchbox")),
        "database": os.environ.get("PGDATABASE", os.environ.get("POSTGRES_DB", "benchbox_test")),
    }


def build_postgres_with_tpch(
    scale_factor: float,
    output_dir: str | Path,
    *,
    connection_config: dict[str, Any] | None = None,
) -> tuple[Any, TPCHavocBenchmark, TPCH]:
    from benchbox.platforms.postgresql import PostgreSQLAdapter

    data_dir, tpchavoc, tpch = _generate_tpch(scale_factor, Path(output_dir))

    config = {
        **(connection_config or postgres_connection_config()),
        "statement_timeout": POSTGRES_STATEMENT_TIMEOUT_MS,
    }
    adapter = PostgreSQLAdapter(**config)
    adapter.skip_database_management = True
    connection = adapter.create_connection()

    try:
        cursor = connection.cursor()
        for table in _TPCH_TABLES:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        connection.commit()
        cursor.close()

        adapter.create_schema(tpchavoc, connection)
        table_stats, _, _ = adapter.load_data(tpchavoc, connection, data_dir)
        empty = [table for table in _TPCH_TABLES if table_stats.get(table, 0) <= 0]
        if empty:
            raise RuntimeError(f"PostgreSQL TPC-H load failed - no rows in {empty} (stats={table_stats})")

        connection.rollback()
        connection.autocommit = True
        for index_sql in POSTGRES_TPCH_INDEXES:
            connection.execute(index_sql)
        connection.execute("ANALYZE")
    except Exception:
        connection.close()
        raise
    return connection, tpchavoc, tpch


def _report(
    divergences: list[Divergence],
    total: int,
    known: dict[str, str],
    *,
    engine_label: str,
    baseline_name: str,
    review_by: dict[str, date] | None = None,
) -> int:
    found = {d.key for d in divergences}
    new = sorted(found - set(known), key=_sort_key)
    resolved = sorted(set(known) - found, key=_sort_key)
    review_by = review_by or {}
    review_due = sorted(key for key, due in review_by.items() if key in known and due < date.today())

    print(f"TPC-Havoc variant equivalence vs canonical TPC-H @ SF={EQUIVALENCE_SCALE} ({engine_label})")
    print(f"  checked {total} variants - {len(divergences)} divergent, {total - len(divergences)} equivalent\n")

    by_class: dict[str, list[Divergence]] = {}
    for divergence in sorted(divergences, key=lambda d: _sort_key(d.key)):
        klass = known.get(divergence.key, "UNCLASSIFIED")
        by_class.setdefault(klass, []).append(divergence)
    for klass in sorted(by_class):
        print(f"  [{klass}]")
        for divergence in by_class[klass]:
            print(f"    {divergence.key}: {divergence.detail}")
        print()

    if new:
        print(f"GATE FAILURE - unclassified divergences from canonical TPC-H: {new}")
    if resolved:
        print(
            f"GATE FAILURE - previously-known divergences now equivalent - "
            f"remove the stale baseline entry in {baseline_name}: {resolved}"
        )
    if review_due:
        for key in review_due:
            print(f"WAIVER REVIEW DUE - {key}: review_by {review_by[key]} has passed - {known[key]}")
    if not new and not resolved:
        print("All variants equivalent to canonical TPC-H (modulo classified exceptions).")
    return 1 if (new or resolved) else 0


def _merge_divergences(
    primary: list[Divergence],
    additional: list[Divergence],
    *,
    context_label: str,
) -> list[Divergence]:
    by_key = {divergence.key: divergence for divergence in primary}
    for divergence in additional:
        annotated = Divergence(
            divergence.query_id,
            divergence.variant_id,
            f"{divergence.detail} [{context_label}]",
        )
        if divergence.key not in by_key:
            by_key[divergence.key] = annotated
    return list(by_key.values())


def find_q16_cardinality_divergences(
    connection: Any,
    tpchavoc: TPCHavocBenchmark,
    canonical_query: Callable[[int], str],
    *,
    multi_complaint_count: int = 3,
    translate_variant: Callable[[str], str] | None = None,
    variant_query: Callable[[int, int], str] | None = None,
    skip_variants: Collection[str] | None = None,
    execute_transform: Callable[[str], str] | None = None,
) -> list[Divergence]:
    if multi_complaint_count < 2:
        raise ValueError("multi_complaint_count must be at least 2")

    existing_complaints = connection.execute(
        "SELECT s_suppkey, s_comment FROM supplier WHERE s_comment LIKE '%Customer%Complaints%' ORDER BY s_suppkey"
    ).fetchall()
    probe_suppliers = connection.execute(
        "SELECT s.s_suppkey, s.s_comment "
        "FROM supplier s "
        "WHERE EXISTS (SELECT 1 FROM partsupp ps WHERE ps.ps_suppkey = s.s_suppkey) "
        f"ORDER BY s.s_suppkey LIMIT {int(multi_complaint_count)}"
    ).fetchall()
    if len(probe_suppliers) < 2:
        return [
            Divergence(16, 0, f"insufficient partsupp suppliers ({len(probe_suppliers)}) for multi-complaint probe")
        ]

    saved_comments = {int(suppkey): str(comment) for suppkey, comment in (*existing_complaints, *probe_suppliers)}
    sweep_kwargs = {
        "query_ids": [16],
        "translate_variant": translate_variant,
        "variant_query": variant_query,
        "skip_variants": skip_variants,
        "execute_transform": execute_transform,
    }
    try:
        if existing_complaints:
            existing_ids = ", ".join(str(int(suppkey)) for suppkey, _ in existing_complaints)
            connection.execute(
                f"UPDATE supplier SET s_comment = 'regular supplier' WHERE s_suppkey IN ({existing_ids})"
            )
        zero_divergences = find_divergences(connection, tpchavoc, canonical_query, **sweep_kwargs)

        probe_ids = ", ".join(str(int(suppkey)) for suppkey, _ in probe_suppliers)
        connection.execute(
            f"UPDATE supplier SET s_comment = 'Customer Complaints probe' WHERE s_suppkey IN ({probe_ids})"
        )
        multi_divergences = find_divergences(connection, tpchavoc, canonical_query, **sweep_kwargs)
    finally:
        for suppkey, comment in saved_comments.items():
            escaped = comment.replace("'", "''")
            connection.execute(f"UPDATE supplier SET s_comment = '{escaped}' WHERE s_suppkey = {suppkey}")

    merged = _merge_divergences([], zero_divergences, context_label="|excluded|=0")
    return _merge_divergences(merged, multi_divergences, context_label=f"|excluded|={len(probe_suppliers)}")


def run_duckdb_gate() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        connection, tpchavoc, tpch = build_duckdb_with_tpch(EQUIVALENCE_SCALE, tmp)
        try:
            divergences = find_divergences(connection, tpchavoc, lambda q: tpch.get_query(q))
            q16_cardinality_divergences = find_q16_cardinality_divergences(
                connection, tpchavoc, lambda q: tpch.get_query(q)
            )
            divergences = _merge_divergences(
                divergences,
                q16_cardinality_divergences,
                context_label="q16-cardinality",
            )
        finally:
            connection.close()

    with tempfile.TemporaryDirectory() as tmp_subscale:
        sub_conn, sub_havoc, sub_tpch = build_duckdb_with_tpch(SUBSCALE_EQUIVALENCE_SCALE, tmp_subscale)
        try:
            subscale_divergences = find_divergences(sub_conn, sub_havoc, lambda q: sub_tpch.get_query(q))
            divergences = _merge_divergences(
                divergences,
                subscale_divergences,
                context_label=f"SF={SUBSCALE_EQUIVALENCE_SCALE}",
            )
        finally:
            sub_conn.close()

    total = len(tpchavoc.get_implemented_queries()) * 10
    return _report(
        divergences,
        total,
        KNOWN_DIVERGENCES,
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
    )


def _dialect_sample_divergences(
    connection: Any,
    tpchavoc: TPCHavocBenchmark,
    tpch: TPCH,
    target_dialect: str,
    skip_variants: Collection[str],
    *,
    query_ids: list[int] | None = None,
    **sweep_kwargs: Any,
) -> list[Divergence]:
    return find_divergences(
        connection,
        tpchavoc,
        lambda q: tpch.get_query(q, dialect=target_dialect),
        query_ids=query_ids,
        translate_variant=lambda sql: tpchavoc.translate_query_text(sql, "netezza", target_dialect),
        variant_query=lambda query_id, variant_id: tpchavoc.get_query(
            f"{query_id}_v{variant_id}", dialect=target_dialect
        ),
        skip_variants=set(skip_variants),
        **sweep_kwargs,
    )


def find_postgres_divergences(
    connection: Any,
    tpchavoc: TPCHavocBenchmark,
    tpch: TPCH,
    *,
    query_ids: list[int] | None = None,
) -> list[Divergence]:
    from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

    return _dialect_sample_divergences(
        connection,
        tpchavoc,
        tpch,
        POSTGRES_TARGET_DIALECT,
        POSTGRES_TPCHAVOC_SKIPS,
        query_ids=query_ids,
        char_padding_columns={4: (0,), 5: (0,), 7: (0, 1), 12: (0,)},
    )


def run_postgres_sample() -> int:
    import tempfile

    import psycopg

    from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

    try:
        with tempfile.TemporaryDirectory() as tmp:
            connection, tpchavoc, tpch = build_postgres_with_tpch(EQUIVALENCE_SCALE, tmp)
            try:
                divergences = find_postgres_divergences(connection, tpchavoc, tpch)
            finally:
                connection.close()
    except psycopg.OperationalError as exc:
        print(f"PostgreSQL equivalence sample SKIPPED - could not connect to PostgreSQL: {exc}")
        return 0

    total = len(tpchavoc.get_implemented_queries()) * 10 - len(POSTGRES_TPCHAVOC_SKIPS)
    return _report(
        divergences,
        total,
        POSTGRES_KNOWN_DIVERGENCES,
        engine_label="PostgreSQL",
        baseline_name="POSTGRES_KNOWN_DIVERGENCES",
    )


DATAFUSION_TARGET_DIALECT = "datafusion"


def _close_quietly(connection: Any) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def build_datafusion_with_tpch(scale_factor: float, output_dir: str | Path) -> tuple[Any, TPCHavocBenchmark, TPCH]:
    from benchbox.platforms.datafusion import DataFusionAdapter

    data_dir, tpchavoc, tpch = _generate_tpch(scale_factor, Path(output_dir))

    adapter = DataFusionAdapter(working_dir=str(Path(output_dir) / "datafusion_working"))
    connection = adapter.create_connection()
    try:
        adapter.create_schema(tpchavoc, connection)
        table_stats, _, _ = adapter.load_data(tpchavoc, connection, data_dir)
        empty = [table for table in _TPCH_TABLES if table_stats.get(table, 0) <= 0]
        if empty:
            raise RuntimeError(f"DataFusion TPC-H load failed - no rows in {empty} (stats={table_stats})")
    except Exception:
        _close_quietly(connection)
        raise
    return connection, tpchavoc, tpch


def find_datafusion_divergences(
    connection: Any,
    tpchavoc: TPCHavocBenchmark,
    tpch: TPCH,
    *,
    query_ids: list[int] | None = None,
) -> list[Divergence]:
    from benchbox.platforms.datafusion_tpch_rewrites import rewrite_datafusion_tpch_query
    from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS

    def _canonical_sql(query_id: int) -> str:
        sql = tpch.get_query(query_id, dialect=DATAFUSION_TARGET_DIALECT)
        return rewrite_datafusion_tpch_query(sql, query_id)

    return find_divergences(
        connection,
        tpchavoc,
        _canonical_sql,
        query_ids=query_ids,
        translate_variant=lambda sql: tpchavoc.translate_query_text(sql, "netezza", DATAFUSION_TARGET_DIALECT),
        variant_query=lambda query_id, variant_id: tpchavoc.get_query(
            f"{query_id}_v{variant_id}", dialect=DATAFUSION_TARGET_DIALECT
        ),
        skip_variants=set(DATAFUSION_TPCHAVOC_SKIPS),
    )


def run_datafusion_sample() -> int:
    import tempfile

    from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS

    try:
        import datafusion  # noqa: F401
    except ImportError as exc:
        print(f"DataFusion equivalence sample SKIPPED - DataFusion not installed: {exc}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        connection, tpchavoc, tpch = build_datafusion_with_tpch(EQUIVALENCE_SCALE, tmp)
        try:
            divergences = find_datafusion_divergences(connection, tpchavoc, tpch)
        finally:
            _close_quietly(connection)

    total = len(tpchavoc.get_implemented_queries()) * 10 - len(DATAFUSION_TPCHAVOC_SKIPS)
    return _report(
        divergences,
        total,
        DATAFUSION_KNOWN_DIVERGENCES,
        engine_label="DataFusion",
        baseline_name="DATAFUSION_KNOWN_DIVERGENCES",
        review_by=DATAFUSION_KNOWN_DIVERGENCES_REVIEW_BY,
    )


CLICKHOUSE_TARGET_DIALECT = "clickhouse"

CLICKHOUSE_BENCHMARK_TYPE = "tpch"

CLICKHOUSE_KNOWN_DIVERGENCES: dict[str, str] = {
    "2_v8": "correlated-subquery-decorrelation",
    "3_v7": "sum-empty-group-zero",
    "7_v7": "sum-empty-group-zero",
    "10_v7": "sum-empty-group-zero",
    "10_v8": "sum-empty-group-zero",
    "13_v1": "correlated-subquery-decorrelation",
}

CLICKHOUSE_KNOWN_DIVERGENCES_REVIEW_BY: dict[str, date] = {}


def build_clickhouse_with_tpch(scale_factor: float, output_dir: str | Path) -> tuple[Any, TPCHavocBenchmark, TPCH]:
    from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

    data_dir, tpchavoc, tpch = _generate_tpch(scale_factor, Path(output_dir))

    adapter = ClickHouseLocalAdapter(database_path=str(Path(output_dir) / "clickhouse_store"))
    adapter.skip_database_management = True
    connection = adapter.create_connection()
    try:
        adapter.create_schema(tpchavoc, connection)
        table_stats, _, _ = adapter.load_data(tpchavoc, connection, data_dir)
        empty = [table for table in _TPCH_TABLES if table_stats.get(table, 0) <= 0]
        if empty:
            raise RuntimeError(f"ClickHouse TPC-H load failed - no rows in {empty} (stats={table_stats})")
        adapter.configure_for_benchmark(connection, CLICKHOUSE_BENCHMARK_TYPE)
    except Exception:
        _close_quietly(connection)
        raise
    return connection, tpchavoc, tpch


def _clickhouse_execute_transform(sql: str) -> str:
    from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

    return rewrite_query_for_clickhouse(sql)


def find_clickhouse_divergences(
    connection: Any,
    tpchavoc: TPCHavocBenchmark,
    tpch: TPCH,
    *,
    query_ids: list[int] | None = None,
) -> list[Divergence]:
    from benchbox.sql_compat.rules.execution_filter.clickhouse_tpchavoc import CLICKHOUSE_TPCHAVOC_SKIPS

    return _dialect_sample_divergences(
        connection,
        tpchavoc,
        tpch,
        CLICKHOUSE_TARGET_DIALECT,
        CLICKHOUSE_TPCHAVOC_SKIPS,
        query_ids=query_ids,
        execute_transform=_clickhouse_execute_transform,
    )


def run_clickhouse_sample() -> int:
    import os
    import tempfile

    from benchbox.sql_compat.rules.execution_filter.clickhouse_tpchavoc import CLICKHOUSE_TPCHAVOC_SKIPS

    original_cwd = os.getcwd()
    try:
        import chdb  # noqa: F401
    except ImportError as exc:
        os.chdir(original_cwd)
        print(f"ClickHouse equivalence sample SKIPPED - chDB (clickhouse-local) not installed: {exc}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        connection, tpchavoc, tpch = build_clickhouse_with_tpch(EQUIVALENCE_SCALE, tmp)
        try:
            divergences = find_clickhouse_divergences(connection, tpchavoc, tpch)
        finally:
            _close_quietly(connection)

    total = len(tpchavoc.get_implemented_queries()) * 10 - len(CLICKHOUSE_TPCHAVOC_SKIPS)
    return _report(
        divergences,
        total,
        CLICKHOUSE_KNOWN_DIVERGENCES,
        engine_label="ClickHouse",
        baseline_name="CLICKHOUSE_KNOWN_DIVERGENCES",
        review_by=CLICKHOUSE_KNOWN_DIVERGENCES_REVIEW_BY,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="TPC-Havoc variant-equivalence oracle.")
    parser.add_argument(
        "--engine",
        choices=("duckdb", "postgres", "datafusion", "clickhouse"),
        default="duckdb",
        help="SQL engine to sample (default: duckdb, the hard gate).",
    )
    args = parser.parse_args(argv)
    if args.engine == "postgres":
        return run_postgres_sample()
    if args.engine == "datafusion":
        return run_datafusion_sample()
    if args.engine == "clickhouse":
        return run_clickhouse_sample()
    return run_duckdb_gate()


def _sort_key(key: str) -> tuple[int, int]:
    query, variant = key.split("_v")
    return int(query), int(variant)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
