from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

DIALECTS = ("sqlite", "duckdb", "datafusion", "postgres")
TPCH_SEED = None
TPCDS_SEED = 1001


def _runtime_pin() -> dict[str, str]:
    import benchbox

    pin_path = Path(benchbox.__file__).parent / "_vendor" / "sqlglot.json"
    return json.loads(pin_path.read_text(encoding="utf-8")) if pin_path.exists() else {}


def _adapter_stage(benchmark_name: str, dialect: str, query_id: int):
    if dialect == "datafusion" and benchmark_name == "tpch":
        from benchbox.platforms.datafusion_tpch_rewrites import rewrite_datafusion_tpch_query

        return lambda sql: rewrite_datafusion_tpch_query(sql, str(query_id))
    return None


def _emit(benchmark_name: str, benchmark, ids, seed, dialects) -> dict[str, dict[str, str]]:
    emitted: dict[str, dict[str, str]] = {}
    for query_id in ids:
        per_dialect: dict[str, str] = {}
        for dialect in dialects:
            try:
                sql = benchmark.get_query(query_id, seed=seed, dialect=dialect)
                stage = _adapter_stage(benchmark_name, dialect, query_id)
                per_dialect[dialect] = stage(sql) if stage else sql
            except Exception as error:
                per_dialect[dialect] = f"<{type(error).__name__}: {error}>"
        emitted[str(query_id)] = per_dialect
    return emitted


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
    if "tpcds" in wanted:
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        benchmark = TPCDSBenchmark(scale_factor=args.scale, output_dir=scratch / "tpcds")
        queries["tpcds"] = _emit("tpcds", benchmark, range(1, 100), TPCDS_SEED, dialects)
        augmented: dict[str, dict[str, str]] = {}
        for seed in filter(None, args.augment_seeds.split(",")):
            ids = [int(query) for query in filter(None, args.augment_ids.split(","))]
            for query, per_dialect in _emit(
                "tpcds", benchmark, ids, None if seed == "default" else int(seed), dialects
            ).items():
                augmented[f"{query}.{seed}"] = per_dialect
        if augmented:
            queries["tpcds-augmented"] = augmented
    payload = {
        "benchbox_root": str(Path(benchbox.__file__).resolve().parent.parent),
        "runtime_pin": _runtime_pin(),
        "queries": queries,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
