#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

REFERENCE_PATH = (
    _REPO_ROOT / "benchbox" / "core" / "expected_results" / "reference_digests" / "tpcds_subscale_row_counts.json"
)

SCALES = (0.01, 0.1)
ENGINES = ("duckdb", "datafusion")
REFERENCE_RNGSEED = 1001
ALT_RNGSEEDS = (1002, 1003, 1004, 1042, 7)
MULTI_PART_VARIANTS = {14: ("a", "b"), 23: ("a", "b"), 24: ("a", "b"), 39: ("a", "b")}


def _scale_tag(scale_factor: float) -> str:
    return f"{scale_factor}".replace(".", "")


def _query_ids() -> list[tuple[int, str | None, str]]:
    ids: list[tuple[int, str | None, str]] = []
    for number in range(1, 100):
        variants = MULTI_PART_VARIANTS.get(number)
        if variants:
            for variant in variants:
                ids.append((number, variant, f"{number}{variant}"))
        else:
            ids.append((number, None, str(number)))
    return ids


def _run_cli(work_dir: Path, platform: str, scale_factor: float) -> None:
    env = os.environ.copy()
    env["BENCHBOX_OUTPUT_DIR"] = str(work_dir)
    env["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        "-m",
        "benchbox.cli.main",
        "run",
        "--platform",
        platform,
        "--benchmark",
        "tpcds",
        "--scale",
        str(scale_factor),
        "--phases",
        "generate,load,power",
        "--queries",
        "Q1",
        "--non-interactive",
    ]
    proc = subprocess.run(
        command, cwd=str(_REPO_ROOT), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"{platform} SF={scale_factor} run failed (exit {proc.returncode})")


def _count_on_engines(scale_factor: float, work_root: Path) -> dict[str, dict[str, int]]:
    import duckdb
    from datafusion import SessionContext

    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

    tag = _scale_tag(scale_factor)
    bench = TPCDSBenchmark(scale_factor=scale_factor, output_dir=str(work_root / f"bench_{tag}"))
    duckdb_path = work_root / "duckdb" / "databases" / f"tpcds_sf{tag}" / f"tpcds_sf{tag}_notuning_noconstraints.duckdb"
    datafusion_dir = (
        work_root / "datafusion" / "databases" / f"tpcds_sf{tag}" / f"tpcds_sf{tag}_notuning_noconstraints.datafusion"
    )
    connection = duckdb.connect(str(duckdb_path), read_only=True)
    context = SessionContext()
    for parquet_file in sorted(datafusion_dir.glob("*.parquet")):
        context.register_parquet(parquet_file.stem, str(parquet_file))
    counts: dict[str, dict[str, int]] = {}
    try:
        for number, variant, query_id in _query_ids():
            entry: dict[str, int] = {}
            for engine, dialect in (("duckdb", "duckdb"), ("datafusion", "datafusion")):
                sql = bench.get_query(
                    number, seed=REFERENCE_RNGSEED, scale_factor=scale_factor, dialect=dialect, variant=variant
                )
                if engine == "duckdb":
                    entry["duckdb"] = len(connection.execute(sql).fetchall())
                else:
                    entry["datafusion"] = sum(batch.num_rows for batch in context.sql(sql).collect())
            alt_counts: dict[str, list[int]] = {"duckdb": [], "datafusion": []}
            for alt_seed in ALT_RNGSEEDS:
                for engine, dialect in (("duckdb", "duckdb"), ("datafusion", "datafusion")):
                    alt_sql = bench.get_query(
                        number, seed=alt_seed, scale_factor=scale_factor, dialect=dialect, variant=variant
                    )
                    if engine == "duckdb":
                        alt_counts["duckdb"].append(len(connection.execute(alt_sql).fetchall()))
                    else:
                        alt_counts["datafusion"].append(sum(batch.num_rows for batch in context.sql(alt_sql).collect()))
            entry["alt_seed_counts"] = alt_counts
            counts[query_id] = entry
    finally:
        connection.close()
    return counts


def _build_reference(work_root: Path) -> dict:
    import datafusion
    import duckdb

    scales: dict[str, dict[str, int]] = {}
    sensitive: set[str] = set()
    excluded: dict[str, list[str]] = {}
    for scale_factor in SCALES:
        for platform in ENGINES:
            _run_cli(work_root / platform, platform, scale_factor)
        counts = _count_on_engines(scale_factor, work_root)
        agreed: dict[str, int] = {}
        scale_excluded: list[str] = []
        for query_id, entry in counts.items():
            alt_counts_by_engine = entry.get("alt_seed_counts", {})
            is_sensitive = any(
                entry[engine] != alt_count
                for engine, alt_counts in alt_counts_by_engine.items()
                for alt_count in alt_counts
            )
            if is_sensitive:
                sensitive.add(query_id)
            if entry["duckdb"] == entry["datafusion"]:
                agreed[query_id] = entry["duckdb"]
            else:
                scale_excluded.append(query_id)
        scales[str(scale_factor)] = agreed
        excluded[str(scale_factor)] = scale_excluded
    return {
        "benchmark": "tpcds",
        "reference": {
            "dsqgen_rngseed": REFERENCE_RNGSEED,
            "alt_dsqgen_rngseeds": list(ALT_RNGSEEDS),
            "dsqgen_stream": 0,
            "run_seed_values_giving_reference_rngseed": [None, 1],
            "power_parameter_seed_formula": "(seed or 1) + stream_id + 1000",
        },
        "scales": scales,
        "parameter_sensitive_query_ids": sorted(sensitive, key=lambda value: (len(value), value)),
        "provenance": {
            "generated_with": {
                "duckdb": duckdb.__version__,
                "datafusion": datafusion.__version__,
            },
            "engines": list(ENGINES),
            "data_generator": "bundled dsdgen via benchbox TPC-DS generate phase",
            "query_generator": "bundled dsqgen via TPCDSBenchmark.get_query (netezza base dialect, per-engine dialect translation)",
            "agreement": "a count is recorded only where the DuckDB and DataFusion row counts are equal",
            "sensitivity": "a query is parameter-sensitive where the DuckDB count at any alt RNGSEED differs from the reference RNGSEED count at either recorded scale",
            "excluded_engine_disagreements": excluded,
            "regenerate": "uv run -- python _project/scripts/generate_tpcds_subscale_row_counts.py",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/tpcds_subscale_gen"))
    parser.add_argument("--output", type=Path, default=REFERENCE_PATH)
    args = parser.parse_args()
    reference = _build_reference(args.work_dir)
    args.output.write_text(json.dumps(reference, indent=1) + "\n", encoding="utf-8")
    for scale_factor, counts in reference["scales"].items():
        print(f"SF {scale_factor}: {len(counts)} agreed row counts")
    print(f"Sensitive queries: {len(reference['parameter_sensitive_query_ids'])}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
