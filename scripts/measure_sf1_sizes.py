#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import inspect
import json
import sys
import tempfile
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

CLI_DESCRIPTION = (
    "Measure actual uncompressed SF=1 source-data sizes for all benchmarks.\n"
    "\n"
    "Runs each benchmark's generator at scale_factor=1.0 with compression disabled\n"
    "and sums the emitted file bytes. Real-data benchmarks (nyctaxi, flightdata,\n"
    "clickbench) download or sample from finite corpora, so the script measures\n"
    "what the generator actually emits rather than projecting from row counts.\n"
    "\n"
    "Results are written as JSON to stdout (or --output): one record per\n"
    "benchmark with total bytes, file count, row counts where the manifest\n"
    "reports them, elapsed seconds, method (generated | manifest | spec), and\n"
    "any error.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python scripts/measure_sf1_sizes.py --output /tmp/sf1.json\n"
    "    uv run -- python scripts/measure_sf1_sizes.py --benchmark tpch,ssb\n"
    "\n"
    "Parallel runs (--jobs N) execute benchmarks concurrently, one process per\n"
    "benchmark. Keep the default sequential mode unless the scratch filesystem\n"
    "has ample space: several SF=1 datasets exceed 1 GB.\n"
)

_MANIFEST_TABLES_KEY = "tables"


_ARCHIVE_SUFFIXES = (".tar.zst", ".tar.gz", ".tgz", ".tar", ".zip")
_COMPRESSED_SUFFIXES = (".gz", ".zst", ".bz2", ".snappy", ".lz4", ".parquet", ".lock")
_METADATA_FILENAMES = {".bulk_load_metadata.json"}


@dataclass
class SizeRecord:
    benchmark: str
    scale_factor: float = 1.0
    method: str = "generated"
    total_bytes: int = 0
    file_count: int = 0
    total_rows: int | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None
    notes: str = ""


_RECORD_CACHE: dict[str, SizeRecord] = {}


GENERATORS: list[tuple[str, str, str, dict]] = [
    ("tpch", "benchbox.core.tpch.generator", "TPCHDataGenerator", {}),
    ("tpcds", "benchbox.core.tpcds.generator.manager", "TPCDSDataGenerator", {}),
    ("ssb", "benchbox.core.ssb.generator", "SSBDataGenerator", {}),
    ("tpch_skew", "benchbox.core.tpch_skew.generator", "TPCHSkewDataGenerator", {}),
    ("tpchavoc", "__alias__:tpch", "", {}),
    ("amplab", "benchbox.core.amplab.generator", "AMPLabDataGenerator", {}),
    ("h2odb", "benchbox.core.h2odb.generator", "H2ODataGenerator", {}),
    ("coffeeshop", "benchbox.core.coffeeshop.generator", "CoffeeShopDataGenerator", {}),
    ("tsbs_devops", "benchbox.core.tsbs_devops.generator", "TSBSDevOpsDataGenerator", {}),
    ("joinorder_synthetic", "benchbox.core.joinorder_synthetic.generator", "JoinOrderGenerator", {}),
    ("read_primitives", "benchbox.core.read_primitives.generator", "ReadPrimitivesDataGenerator", {}),
    ("write_primitives", "benchbox.core.write_primitives.generator", "WritePrimitivesDataGenerator", {}),
    (
        "transaction_primitives",
        "benchbox.core.transaction_primitives.generator",
        "TransactionPrimitivesDataGenerator",
        {},
    ),
    ("vector_search", "benchbox.core.vector_search.generator", "VectorSearchDataGenerator", {}),
    ("clickbench", "benchbox.core.clickbench.generator", "ClickBenchDataGenerator", {}),
]

BENCHMARK_LEVEL: list[tuple[str, str, str, dict]] = [
    ("tpcdi", "benchbox.core.tpcdi.benchmark", "TPCDIBenchmark", {}),
    ("nyctaxi", "benchbox.core.nyctaxi.benchmark", "NYCTaxiBenchmark", {}),
    ("flightdata", "benchbox.core.flightdata.benchmark", "FlightDataBenchmark", {}),
    ("joinorder", "benchbox.core.joinorder.benchmark", "JoinOrderBenchmark", {"count_parquet": True}),
    ("tpcds_obt", "benchbox.core.tpcds_obt.benchmark", "TPCDSOBTBenchmark", {"output_format": "dat"}),
    ("datavault", "benchbox.core.datavault.benchmark", "DataVaultBenchmark", {}),
]


def _is_counted_file(path: Path, *, count_parquet: bool = False) -> bool:
    name = path.name
    if name == MANIFEST_FILENAME or name in _METADATA_FILENAMES:
        return False
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return False
    if count_parquet and lowered.endswith(".parquet"):
        return True
    return not any(lowered.endswith(suffix) for suffix in _COMPRESSED_SUFFIXES)


def _sum_tree(root: Path, seen: set[str], *, count_parquet: bool = False) -> tuple[int, int]:
    total = 0
    count = 0
    for child in sorted(root.rglob("*")):
        child_key = str(child)
        if child.is_file() and child_key not in seen and _is_counted_file(child, count_parquet=count_parquet):
            seen.add(child_key)
            total += child.stat().st_size
            count += 1
    return total, count


def sum_directory_bytes(root: Path, *, count_parquet: bool = False) -> tuple[int, int]:
    return _sum_tree(root, set(), count_parquet=count_parquet)


def _sum_paths(paths: object, *, count_parquet: bool = False) -> tuple[int, int]:
    total = 0
    count = 0
    stack: list[object] = [paths]
    seen: set[str] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, (str, Path)):
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            candidate = Path(key)
            if candidate.is_file() and _is_counted_file(candidate, count_parquet=count_parquet):
                total += candidate.stat().st_size
                count += 1
            elif candidate.is_dir():
                dir_total, dir_count = _sum_tree(candidate, seen, count_parquet=count_parquet)
                total += dir_total
                count += dir_count
    return total, count


def _flatten_paths(paths: object) -> list[str | Path]:
    leaves: list[str | Path] = []
    stack: list[object] = [paths]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, (str, Path)):
            leaves.append(item)
    return leaves


def _sum_tree_excluding(root: Path, skip: set[Path], seen: set[str], *, count_parquet: bool = False) -> tuple[int, int]:
    total = 0
    count = 0
    skip_resolved = {path.resolve() for path in skip}
    for child in sorted(root.rglob("*")):
        resolved = child.resolve()
        if any(resolved == skipped or resolved.is_relative_to(skipped) for skipped in skip_resolved):
            continue
        child_key = str(child)
        if child.is_file() and child_key not in seen and _is_counted_file(child, count_parquet=count_parquet):
            seen.add(child_key)
            total += child.stat().st_size
            count += 1
    return total, count


def _sum_manifest_rows(root: Path) -> int | None:
    manifest_path = root / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tables = manifest.get(_MANIFEST_TABLES_KEY)
    if not isinstance(tables, dict):
        return None
    manifest_rows = 0
    found = False
    for table_data in tables.values():
        if not isinstance(table_data, dict):
            continue
        formats = table_data.get("formats")
        entries_iter: list[object] = []
        if isinstance(formats, dict):
            for entries in formats.values():
                if isinstance(entries, list):
                    entries_iter.extend(entries)
                    break
        elif isinstance(table_data.get("files"), list):
            entries_iter.extend(table_data["files"])
        for entry in entries_iter:
            if isinstance(entry, dict) and isinstance(entry.get("row_count"), int):
                manifest_rows += entry["row_count"]
                found = True
    return manifest_rows if found else None


def _run_generator(module: str, cls_name: str, extra: dict) -> tuple[int, int, int | None, float]:
    module_obj = __import__(module, fromlist=[cls_name])
    cls = getattr(module_obj, cls_name)
    extra = dict(extra)
    count_parquet = bool(extra.pop("count_parquet", False))
    with tempfile.TemporaryDirectory(prefix="sf1measure-") as tmp:
        start = mono_time()
        tmp_path = Path(tmp)
        source_dir = tmp_path / "source_cache"
        if "tpcds_source_dir" not in extra:
            try:
                _params = inspect.signature(cls.__init__).parameters
            except (TypeError, ValueError):
                _params = {}
            if "tpcds_source_dir" in _params:
                extra = {**extra, "tpcds_source_dir": str(source_dir / "tpcds_sf1")}
        try:
            instance = cls(scale_factor=1.0, output_dir=tmp, compress_data=False, **extra)
        except TypeError:
            try:
                instance = cls(scale_factor=1.0, output_dir=tmp, **extra)
            except TypeError:
                narrowed = {k: v for k, v in extra.items() if k != "tpcds_source_dir"}
                instance = cls(scale_factor=1.0, output_dir=tmp, **narrowed)
        if hasattr(instance, "_tpch_source_dir"):
            instance._tpch_source_dir = source_dir / "tpch_sf1"
        meth = (
            getattr(instance, "generate_data", None)
            or getattr(instance, "generate", None)
            or getattr(instance, "generate_all_source_data", None)
        )
        if meth is None:
            raise AttributeError(f"{cls_name} has no known generate method")
        paths = meth()
        _reject_synthetic_fallback(instance, cls_name)
        if isinstance(paths, dict) and not paths:
            paths = tmp_path
        elapsed = elapsed_seconds(start)
        total, count = _sum_paths(paths, count_parquet=count_parquet)
        seen: set[str] = set()
        for leaf in _flatten_paths(paths):
            candidate = Path(leaf)
            if candidate.is_dir():
                seen.update(str(child) for child in sorted(candidate.rglob("*")) if child.is_file())
            else:
                seen.add(str(leaf))
        aux_total, aux_count = _sum_tree_excluding(tmp_path, {source_dir}, seen, count_parquet=count_parquet)
        total += aux_total
        count += aux_count
        rows = _sum_manifest_rows(tmp_path)
        return total, count, rows, elapsed


def _reject_synthetic_fallback(instance: object, cls_name: str) -> None:
    seen: set[int] = set()
    stack: list[object] = [instance]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        fallback = getattr(current, "_synthetic_fallback_months", None)
        if fallback:
            raise RuntimeError(f"{cls_name} used synthetic fallback months {sorted(fallback)}; refusing to calibrate")
        for attr in getattr(current, "OUTPUT_DIR_GENERATOR_ATTRS", ()) or ():
            stack.append(getattr(current, attr, None))
        for name in ("downloader", "green_downloader", "hvfhv_downloader"):
            child = getattr(current, name, None)
            if child is not None:
                stack.append(child)


def measure_one(benchmark: str) -> SizeRecord:
    cached = _RECORD_CACHE.get(benchmark)
    if cached is not None:
        return cached
    record = SizeRecord(benchmark=benchmark)
    start = mono_time()
    try:
        entry = next((g for g in GENERATORS if g[0] == benchmark), None)
        if entry is None:
            entry = next((g for g in BENCHMARK_LEVEL if g[0] == benchmark), None)
        if entry is None:
            record.error = f"no measurement entry for {benchmark}"
            return record
        _, module, cls_name, extra = entry
        if module.startswith("__alias__:"):
            target = module.split(":", 1)[1]
            source = measure_one(target)
            record.total_bytes = source.total_bytes
            record.file_count = source.file_count
            record.total_rows = source.total_rows
            record.elapsed_seconds = source.elapsed_seconds
            record.error = source.error
            record.notes = f"alias of {target}: identical generator output"
            if record.error is None:
                _RECORD_CACHE[benchmark] = record
            return record
        total, count, rows, elapsed = _run_generator(module, cls_name, extra)
        record.total_bytes = total
        record.file_count = count
        record.total_rows = rows
        record.elapsed_seconds = round(elapsed, 1)
        if total == 0:
            record.error = "generator emitted no measurable files"
        else:
            _RECORD_CACHE[benchmark] = record
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.elapsed_seconds = round(elapsed_seconds(start), 1)
        traceback.print_exc()
    return record


def all_benchmark_ids() -> list[str]:
    return [g[0] for g in GENERATORS] + [g[0] for g in BENCHMARK_LEVEL]


def measure_many(benchmarks: list[str], jobs: int = 1) -> list[SizeRecord]:
    if jobs < 1:
        raise ValueError(f"--jobs must be >= 1, got {jobs}")
    if jobs == 1 or len(benchmarks) <= 1:
        return [measure_one(benchmark) for benchmark in benchmarks]
    aliased = {b for b in benchmarks if _is_alias(b)}
    real = [b for b in benchmarks if b not in aliased]
    results: dict[str, SizeRecord] = {}
    if real:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
            for benchmark, record in zip(real, pool.map(measure_one, real), strict=True):
                results[benchmark] = record
                if record.error is None:
                    _RECORD_CACHE[benchmark] = record
    for benchmark in aliased:
        results[benchmark] = measure_one(benchmark)
    return [results[b] for b in benchmarks]


def _is_alias(benchmark: str) -> bool:
    entry = next((g for g in (*GENERATORS, *BENCHMARK_LEVEL) if g[0] == benchmark), None)
    return entry is not None and entry[1].startswith("__alias__:")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--benchmark",
        default="",
        help="Comma-separated subset to measure (default: all).",
    )
    parser.add_argument("--output", default="", help="Write JSON records here (default: stdout).")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Parallel benchmark processes (default: 1, sequential). Use only with ample scratch space.",
    )
    args = parser.parse_args(argv)

    if args.jobs < 1:
        print(f"--jobs must be >= 1, got {args.jobs}", file=sys.stderr)
        return 2

    wanted = all_benchmark_ids()
    if args.benchmark:
        subset = [b.strip() for b in args.benchmark.split(",") if b.strip()]
        unknown = sorted(set(subset) - set(wanted))
        if unknown:
            print(f"unknown benchmarks: {', '.join(unknown)}", file=sys.stderr)
            return 2
        wanted = subset

    records: list[dict] = []
    failed = 0
    if args.jobs == 1:
        measured = [measure_one(benchmark) for benchmark in wanted]
        for benchmark, record in zip(wanted, measured, strict=True):
            print(f"measuring {benchmark} at SF=1 (uncompressed) ...", file=sys.stderr)
            _report_record(record)
            if record.error:
                failed += 1
            records.append(asdict(record))
    else:
        print(
            f"measuring {len(wanted)} benchmarks at SF=1 (uncompressed) with --jobs {args.jobs} ...",
            file=sys.stderr,
        )
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
            future_map = {pool.submit(measure_one, benchmark): benchmark for benchmark in wanted}
            done: dict[str, dict] = {}
            for future in concurrent.futures.as_completed(future_map):
                record = future.result()
                done[record.benchmark] = asdict(record)
                _report_record(record)
                if record.error:
                    failed += 1
        records = [done[benchmark] for benchmark in wanted]

    payload = {"scale_factor": 1.0, "compression": "none", "records": records}
    text = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if failed else 0


def _report_record(record: SizeRecord) -> None:
    if record.error:
        print(f"  {record.benchmark} FAILED: {record.error}", file=sys.stderr)
    else:
        gib = record.total_bytes / (1024**3)
        print(
            f"  {record.benchmark}: {record.total_bytes} bytes ({gib:.3f} GiB) in {record.elapsed_seconds}s",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
