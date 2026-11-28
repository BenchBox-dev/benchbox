#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import row_count_integrity_sample as sample
import row_count_reference_generation as gen

from benchbox.core.expected_results.row_count_references import QUERY_IDS
from benchbox.core.tpcds.power_test import power_parameter_seed

_REPO_ROOT = Path(__file__).resolve().parents[2]

BENCHMARK = "tpcds"
DEFAULT_SCALE_FACTORS = (0.01, 0.1, 1.0)
DEFAULT_STREAMS = (0, 1, 2, 3)
REGENERATE = "make row-count-references-regen BENCHMARK=tpcds SCALE=<scale factor> WORK_DIR=<durable directory>"
_QUERY_ID = re.compile(r"(\d+)([ab]?)")


def _split(query_id: str) -> tuple[int, str | None]:
    match = _QUERY_ID.fullmatch(query_id)
    return int(match.group(1)), match.group(2) or None


def generate(scale_factor: float, streams: list[int], work_dir: Path) -> Path:
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

    work_dir = work_dir / f"{BENCHMARK}-sf{gen.scale_tag(scale_factor)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    roots = {engine: gen.load_engine(work_dir, engine, BENCHMARK, scale_factor) for engine in gen.REFERENCE_ENGINES}
    checksums = {engine: gen.data_checksum(gen.datagen_directory(root)) for engine, root in roots.items()}
    if len({checksum["sha256"] for checksum in checksums.values()}) != 1:
        raise SystemExit(f"tpcds SF {scale_factor:g}: the engines loaded data with different checksums: {checksums}")
    checksum = next(iter(checksums.values()))

    benchmark = TPCDSBenchmark(scale_factor=scale_factor, output_dir=str(work_dir / "bench"))
    counters, close = gen.open_counters(roots)
    cache_path = work_dir / f"counts-{BENCHMARK}-sf{gen.scale_tag(scale_factor)}.json"
    cache = gen.load_cache(cache_path)
    query_ids = QUERY_IDS[BENCHMARK]
    disagreements: list[str] = []
    stream_entries: dict[int, dict] = {}
    try:
        for stream in streams:
            rngseed = power_parameter_seed(None, stream)

            def sql_for(query_id: str, dialect: str, rngseed: int = rngseed) -> str:
                number, variant = _split(query_id)
                kwargs = {"variant": variant} if variant else {}
                return benchmark.get_query(number, seed=rngseed, scale_factor=scale_factor, dialect=dialect, **kwargs)

            by_engine = {
                engine: gen.counts_for_stream(
                    counters,
                    cache,
                    cache_path,
                    engine,
                    f"stream{stream}",
                    query_ids,
                    sql_for,
                    data_sha256=checksum["sha256"],
                )
                for engine in gen.REFERENCE_ENGINES
            }
            agreed, found = gen.agree(f"stream {stream}", query_ids, by_engine)
            disagreements += found
            stream_entries[stream] = {
                "query_parameters": f"dsqgen RNGSEED {rngseed} (power_parameter_seed(None, {stream}))",
                "seed": rngseed,
                "streams_index": None,
                "row_counts": agreed,
            }
        generator = gen.generator_identity(
            BENCHMARK,
            "bundled dsdgen via the BenchBox TPC-DS generate phase",
            "bundled dsqgen via TPCDSBenchmark.get_query (netezza base dialect, per-engine dialect translation)",
            gen.datagen_directory(roots[gen.REFERENCE_ENGINES[0]]),
        )
        versions = gen.engine_versions()
        integrity, _, draws = sample.integrity_section(
            BENCHMARK,
            scale_factor,
            benchmark,
            counters,
            cache,
            cache_path,
            work_dir,
            versions=versions,
            generator=generator,
            checksum=checksum,
            regenerate=REGENERATE,
        )
        seedless = [draw for draw in draws if draw.group == "seedless"]
        engine_counts = {
            engine: sample.counts_for_draws(
                counters,
                cache,
                cache_path,
                engine,
                seedless,
                query_ids,
                sample.sql_factory(BENCHMARK, benchmark, scale_factor, sample.sample_groups(BENCHMARK)),
                data_sha256=checksum["sha256"],
            )
            for engine in gen.REFERENCE_ENGINES
        }
        for draw in seedless:
            agreed, found = gen.agree(
                draw.label, query_ids, {engine: counts[draw.label] for engine, counts in engine_counts.items()}
            )
            disagreements += found
            stream_entries[len(stream_entries)] = {
                "query_parameters": f"dsqgen -STREAMS RNGSEED {draw.seed}, stream file query_{draw.stream}.sql",
                "seed": draw.seed,
                "streams_index": draw.stream,
                "row_counts": agreed,
            }
    finally:
        close()
    if disagreements:
        gen.report_disagreements(
            BENCHMARK, scale_factor, disagreements, work_dir / f"disagreements-sf{scale_factor:g}.txt"
        )

    payload = gen.build_payload(
        benchmark=BENCHMARK,
        scale_factor=scale_factor,
        streams=stream_entries,
        versions=versions,
        generator=generator,
        checksum=checksum,
        agreement=gen.agreement_record(BENCHMARK, sorted(stream_entries)),
        regenerate=REGENERATE,
        integrity=integrity,
    )
    return gen.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-factor", type=float, action="append", dest="scale_factors")
    parser.add_argument("--stream", type=int, action="append", dest="streams")
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    for scale_factor in args.scale_factors or DEFAULT_SCALE_FACTORS:
        path = generate(scale_factor, sorted(set(args.streams or DEFAULT_STREAMS)), args.work_dir)
        print(f"Wrote {path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
