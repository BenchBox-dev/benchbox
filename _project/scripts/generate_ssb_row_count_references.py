#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import row_count_reference_generation as gen

from benchbox.core.expected_results.row_count_references import QUERY_IDS

_REPO_ROOT = Path(__file__).resolve().parents[2]

BENCHMARK = "ssb"
DEFAULT_SCALE_FACTORS = (0.01, 0.1, 1.0, 10.0)
REGENERATE = "make row-count-references-regen BENCHMARK=ssb SCALE=<scale factor> WORK_DIR=<durable directory>"


def generate(scale_factor: float, work_dir: Path) -> Path:
    from benchbox.core.ssb.benchmark import SSBBenchmark

    work_dir = work_dir / f"{BENCHMARK}-sf{gen.scale_tag(scale_factor)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    roots = {engine: gen.load_engine(work_dir, engine, BENCHMARK, scale_factor) for engine in gen.REFERENCE_ENGINES}
    checksums = {engine: gen.data_checksum(gen.datagen_directory(root)) for engine, root in roots.items()}
    if len({checksum["sha256"] for checksum in checksums.values()}) != 1:
        raise SystemExit(f"ssb SF {scale_factor:g}: the engines loaded data with different checksums: {checksums}")
    checksum = next(iter(checksums.values()))
    stamp = gen.generator_stamp(gen.datagen_directory(roots[gen.REFERENCE_ENGINES[0]]))

    benchmark = SSBBenchmark(scale_factor=scale_factor, output_dir=str(work_dir / "bench"))
    queries = {engine: benchmark.get_queries(engine) for engine in gen.REFERENCE_ENGINES}
    counters, close = gen.open_counters(roots)
    cache_path = work_dir / f"counts-{BENCHMARK}-sf{gen.scale_tag(scale_factor)}.json"
    cache = gen.load_cache(cache_path)
    query_ids = QUERY_IDS[BENCHMARK]
    try:
        by_engine = {
            engine: gen.counts_for_stream(
                counters,
                cache,
                cache_path,
                engine,
                "stream0",
                query_ids,
                lambda query_id, dialect: queries[dialect][f"Q{query_id}"],
                data_sha256=checksum["sha256"],
            )
            for engine in gen.REFERENCE_ENGINES
        }
    finally:
        close()
    agreed, disagreements = gen.agree("stream 0", query_ids, by_engine)
    if disagreements:
        gen.report_disagreements(
            BENCHMARK, scale_factor, disagreements, work_dir / f"disagreements-sf{scale_factor:g}.txt"
        )

    payload = gen.build_payload(
        benchmark=BENCHMARK,
        scale_factor=scale_factor,
        streams={
            0: {
                "query_parameters": "SSB fixed query text (no substitution parameters)",
                "seed": None,
                "row_counts": agreed,
            }
        },
        versions=gen.engine_versions(),
        generator={
            "data_generator": "BenchBox SSB data generator (benchbox/core/ssb/generator.py) via the generate phase",
            "query_generator": "SSBBenchmark.get_queries, per-engine dialect translation",
            "data_stamp": stamp,
        },
        checksum=checksum,
        agreement=gen.agreement_record(BENCHMARK, [0]),
        regenerate=REGENERATE,
    )
    return gen.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-factor", type=float, action="append", dest="scale_factors")
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    for scale_factor in args.scale_factors or DEFAULT_SCALE_FACTORS:
        path = generate(scale_factor, args.work_dir)
        print(f"Wrote {path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
