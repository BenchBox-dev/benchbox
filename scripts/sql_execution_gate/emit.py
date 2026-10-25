from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

DIALECTS = ("sqlite", "duckdb", "datafusion", "postgres")
TPCH_SEED = None
TPCDS_SEED = 1001
MULTI_PART_IDS = (14, 23, 24)


def _runtime_pin() -> dict[str, str]:
    import benchbox

    pin_path = Path(benchbox.__file__).parent / "_vendor" / "sqlglot.json"
    return json.loads(pin_path.read_text(encoding="utf-8")) if pin_path.exists() else {}


def _adapter_stage(benchmark_name: str, dialect: str, query_id: int):
    if dialect == "datafusion" and benchmark_name == "tpch":
        from benchbox.platforms.datafusion_tpch_rewrites import rewrite_datafusion_tpch_query

        return lambda sql: rewrite_datafusion_tpch_query(sql, str(query_id))
    return None


def _emit(benchmark_name: str, benchmark, ids, seed, dialects, **options) -> dict[str, dict[str, str]]:
    emitted: dict[str, dict[str, str]] = {}
    for query_id in ids:
        per_dialect: dict[str, str] = {}
        for dialect in dialects:
            try:
                sql = benchmark.get_query(query_id, seed=seed, dialect=dialect, **options)
                stage = _adapter_stage(benchmark_name, dialect, query_id)
                per_dialect[dialect] = stage(sql) if stage else sql
            except Exception as error:
                per_dialect[dialect] = f"<{type(error).__name__}: {error}>"
        emitted[str(query_id)] = per_dialect
    return emitted


def _augmented(name: str, benchmark, ids, seeds, dialects, **options) -> dict[str, dict[str, str]]:
    augmented: dict[str, dict[str, str]] = {}
    for seed in seeds:
        for query, per_dialect in _emit(
            name, benchmark, ids, None if seed == "default" else int(seed), dialects, **options
        ).items():
            augmented[f"{query}.{seed}"] = per_dialect
    return augmented


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--scale", type=float, default=0.01)
    parser.add_argument("--benchmarks", default="tpch,tpcds")
    parser.add_argument("--dialects", default=",".join(DIALECTS))
    parser.add_argument("--augment-ids", default="")
    parser.add_argument("--augment-seeds", default="")
    args = parser.parse_args(argv)

    import benchbox

    dialects = tuple(args.dialects.split(","))
    wanted = set(args.benchmarks.split(","))
    scratch = Path(tempfile.mkdtemp(prefix="sql_gate_emit_"))
    queries: dict[str, dict[str, dict[str, str]]] = {}
    if "tpch" in wanted:
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        benchmark = TPCHBenchmark(scale_factor=args.scale, output_dir=scratch / "tpch")
        queries["tpch"] = _emit("tpch", benchmark, range(1, 23), TPCH_SEED, dialects)
    if "tpchavoc" in wanted:
        from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

        benchmark = TPCHavocBenchmark(scale_factor=args.scale, output_dir=scratch / "tpchavoc")
        queries["tpchavoc"] = _emit("tpchavoc", benchmark, list(benchmark.get_queries()), TPCH_SEED, dialects)
    ids = [int(query) for query in filter(None, args.augment_ids.split(","))]
    seeds = list(filter(None, args.augment_seeds.split(",")))
    if "tpcds" in wanted or "tpcds_obt" in wanted:
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        base = TPCDSBenchmark(scale_factor=args.scale, output_dir=scratch / "tpcds")
        if "tpcds" in wanted:
            queries["tpcds"] = _emit("tpcds", base, range(1, 100), TPCDS_SEED, dialects)
        augmented = _augmented("tpcds", base, ids, seeds, dialects)
        if augmented:
            queries["tpcds-augmented"] = augmented
    if "tpcds_obt" in wanted:
        from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark

        parts: dict[str, dict[str, str]] = {}
        for query_id in MULTI_PART_IDS:
            for letter in "ab":
                per_dialect = _emit("tpcds", base, [query_id], TPCDS_SEED, dialects, variant=letter)
                parts[f"{query_id}{letter}"] = per_dialect[str(query_id)]
        queries["tpcds-parts"] = parts
        obt = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=scratch / "tpcds_obt")
        statements = list(obt.query_manager.get_statement_queries(seed=TPCDS_SEED, scale_factor=args.scale))
        queries["tpcds_obt"] = _emit("tpcds_obt", obt, statements, TPCDS_SEED, dialects, scale_factor=args.scale)
        augmented = _augmented("tpcds_obt", obt, ids, seeds, dialects, scale_factor=args.scale)
        if augmented:
            queries["tpcds_obt-augmented"] = augmented
    payload = {
        "benchbox_root": str(Path(benchbox.__file__).resolve().parent.parent),
        "runtime_pin": _runtime_pin(),
        "queries": queries,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
