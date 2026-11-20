from __future__ import annotations

import argparse
import inspect
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

DIALECTS = ("sqlite", "duckdb", "datafusion", "postgres")
TPCH_SEED = None
TPCDS_SEED = 1001
MULTI_PART_IDS = (14, 23, 24)
TRANSLATION_SEED = 20260101


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


class PlatformGroup(NamedTuple):
    dialect: str
    adapter: Any
    platforms: list[str]


def _platform_groups() -> dict[str, PlatformGroup]:
    from benchbox.core.platform_registry import PlatformRegistry

    keyed: dict[tuple, PlatformGroup] = {}
    for platform in sorted(PlatformRegistry.get_sql_platforms()):
        adapter_class = PlatformRegistry.get_adapter_class(platform)
        adapter = object.__new__(adapter_class)
        dialect = adapter.get_target_dialect()
        key = (dialect, adapter_class._get_dialect_queries, adapter_class.get_tpc_base_dialect)
        keyed.setdefault(key, PlatformGroup(dialect, adapter, [])).platforms.append(platform)
    return {",".join(group.platforms): group for group in sorted(keyed.values(), key=lambda g: g.platforms)}


def _benchmark_instance(benchmark_id: str, scale: float, scratch: Path):
    from benchbox.core.benchmark_registry import get_benchmark_class

    benchmark_class = get_benchmark_class(benchmark_id)
    parameters = inspect.signature(benchmark_class.__init__).parameters
    options: dict[str, object] = {}
    if "scale_factor" in parameters:
        options["scale_factor"] = 1.0 if benchmark_id == "joinorder" else scale
    if "output_dir" in parameters:
        options["output_dir"] = scratch / benchmark_id
    try:
        return benchmark_class(**options, seed=TRANSLATION_SEED)
    except TypeError:
        return benchmark_class(**options)


def _emit_translations(scale: float, scratch: Path, shard: tuple[int, int]) -> dict[str, dict[str, dict[str, str]]]:
    from benchbox.core.benchmark_registry import list_benchmark_ids

    logging.disable(logging.CRITICAL)
    groups = list(_platform_groups().items())
    selected = [item for index, item in enumerate(groups) if index % shard[1] == shard[0]]
    instances: dict[str, object] = {}
    emitted: dict[str, dict[str, dict[str, str]]] = {}
    for benchmark_id in sorted(list_benchmark_ids()):
        try:
            instances[benchmark_id] = _benchmark_instance(benchmark_id, scale, scratch)
        except Exception as error:
            instances[benchmark_id] = f"<{type(error).__name__}: {error}>"
    for label, group in selected:
        per_benchmark: dict[str, dict[str, str]] = {}
        for benchmark_id, instance in instances.items():
            if isinstance(instance, str):
                per_benchmark[benchmark_id] = {"*": instance}
                continue
            try:
                queries = group.adapter._get_dialect_queries(instance, benchmark_id, None, strict_translation=True)
                per_benchmark[benchmark_id] = {}
                for query_id, sql in queries.items():
                    stage = _adapter_stage(benchmark_id, group.dialect, query_id)
                    per_benchmark[benchmark_id][str(query_id)] = str(stage(sql) if stage else sql)
            except Exception as error:
                per_benchmark[benchmark_id] = {"*": f"<{type(error).__name__}: {error}>"}
        emitted[label] = per_benchmark
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
    parser.add_argument("--translations", action="store_true")
    parser.add_argument("--shard", default="0/1")
    args = parser.parse_args(argv)

    import benchbox

    if args.translations:
        index, _, count = args.shard.partition("/")
        translations = _emit_translations(
            args.scale, Path(tempfile.mkdtemp(prefix="sql_gate_emit_")), (int(index), int(count))
        )
        payload = {
            "benchbox_root": str(Path(benchbox.__file__).resolve().parent.parent),
            "runtime_pin": _runtime_pin(),
            "translations": translations,
        }
        Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        return 0

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
