#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import row_count_integrity_sample as sample
import row_count_reference_generation as gen

from benchbox.core.expected_results.loader import load_tpch_expected_results
from benchbox.core.expected_results.row_count_references import QUERY_IDS
from benchbox.core.tpch.benchmark import describe_query_parameters, power_stream_seed

_REPO_ROOT = Path(__file__).resolve().parents[2]

BENCHMARK = "tpch"
DEFAULT_SCALE_FACTORS = (0.01, 0.1, 1.0)
DEFAULT_STREAMS = (0, 1, 2)
REGENERATE = "make row-count-references-regen BENCHMARK=tpch SCALE=<scale factor> WORK_DIR=<durable directory>"


def generate(scale_factor: float, streams: list[int], work_dir: Path) -> Path:
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    work_dir = work_dir / f"{BENCHMARK}-sf{gen.scale_tag(scale_factor)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    roots = {engine: gen.load_engine(work_dir, engine, BENCHMARK, scale_factor) for engine in gen.REFERENCE_ENGINES}
    checksums = {engine: gen.data_checksum(gen.datagen_directory(root)) for engine, root in roots.items()}
    if len({checksum["sha256"] for checksum in checksums.values()}) != 1:
        raise SystemExit(f"tpch SF {scale_factor:g}: the engines loaded data with different checksums: {checksums}")
    checksum = next(iter(checksums.values()))

    benchmark = TPCHBenchmark(scale_factor=scale_factor, output_dir=str(work_dir / "bench"))
    counters, close = gen.open_counters(roots)
    cache_path = work_dir / f"counts-{BENCHMARK}-sf{gen.scale_tag(scale_factor)}.json"
    cache = gen.load_cache(cache_path)
    query_ids = QUERY_IDS[BENCHMARK]
    disagreements: list[str] = []
    stream_entries: dict[int, dict] = {}
    try:
        for stream in streams:
            seed = power_stream_seed(None, stream)
            by_engine = {
                engine: gen.counts_for_stream(
                    counters,
                    cache,
                    cache_path,
                    engine,
                    f"stream{stream}",
                    query_ids,
                    lambda query_id, dialect, seed=seed: benchmark.get_query(
                        int(query_id), seed=seed, scale_factor=scale_factor, dialect=dialect
                    ),
                    data_sha256=checksum["sha256"],
                )
                for engine in gen.REFERENCE_ENGINES
            }
            agreed, found = gen.agree(f"stream {stream}", query_ids, by_engine)
            disagreements += found
            stream_entries[stream] = {
                "query_parameters": describe_query_parameters(seed, execution_type="power"),
                "seed": seed,
                "streams_index": None,
                "row_counts": agreed,
            }
        generator = gen.generator_identity(
            BENCHMARK,
            "_sources/tpc-h/dbgen (dbgen) via the BenchBox TPC-H generate phase",
            "_sources/tpc-h/dbgen (qgen) via TPCHBenchmark.get_query, per-engine dialect translation",
            gen.datagen_directory(roots[gen.REFERENCE_ENGINES[0]]),
        )
        versions = gen.engine_versions()
        integrity, _, _ = sample.integrity_section(
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
    finally:
        close()
    if disagreements:
        gen.report_disagreements(
            BENCHMARK, scale_factor, disagreements, work_dir / f"disagreements-sf{scale_factor:g}.txt"
        )

    answer_check = None
    if scale_factor == 1.0:
        answers = load_tpch_expected_results(1.0)
        mismatched = {
            qid: {"answer_set": answers.get(qid), "agreed": stream_entries[0]["row_counts"][qid]}
            for qid in query_ids
            if answers.get(qid) != stream_entries[0]["row_counts"][qid]
        }
        if mismatched:
            raise SystemExit(f"tpch SF 1: stream 0 disagrees with the TPC answer set: {mismatched}")
        answer_check = {"stream": 0, "result": "every query row count equals the TPC answer set"}

    payload = gen.build_payload(
        benchmark=BENCHMARK,
        scale_factor=scale_factor,
        streams=stream_entries,
        versions=versions,
        generator=generator,
        checksum=checksum,
        agreement=gen.agreement_record(BENCHMARK, streams),
        regenerate=REGENERATE,
        answer_set_check=answer_check,
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
