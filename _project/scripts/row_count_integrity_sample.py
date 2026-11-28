from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import row_count_reference_generation as gen

from benchbox.core.expected_results.row_count_references import (
    MIN_AGREEMENT_DRAWS,
    QUERY_IDS,
)
from benchbox.core.expected_results.structural_bounds import structural_bound

SAMPLE_RANDOM_SEED = 20261125
TPCDS_SEEDLESS_STREAMS = 9
TPCDS_SPREAD_PER_TEMPLATE = 188
TPCDS_SPREAD_STREAM_SEEDS = 64
TPCDS_SPREAD_STREAMS_PER_SEED = 4
TPCDS_LOAD_END_TIME_SEEDS = 8
TPCDS_LOAD_END_TIME_STREAMS = 8
TPCH_PROBE_SEEDS = (1, 7, 42, 99, 123, 2024, 31337, 65537, 1000003, 17039360)
TPCH_SPREAD_SEEDS = 500
TPCH_LOAD_END_TIME_SEEDS = 10
TPCDS_STREAM_SEED = 42
_MAX_SEED = 2**31 - 1


@dataclass(frozen=True)
class Draw:
    group: str
    path: str
    seed: int | None
    stream: int | None

    @property
    def label(self) -> str:
        suffix = "" if self.stream is None else f"/stream{self.stream}"
        return f"{self.path}/seed{self.seed}{suffix}"


def _spread(rng: random.Random, count: int, exclude: set[int]) -> list[int]:
    seeds: list[int] = []
    while len(seeds) < count:
        seed = rng.randint(1, _MAX_SEED)
        if seed not in exclude:
            exclude.add(seed)
            seeds.append(seed)
    return seeds


def _load_end_times(rng: random.Random, count: int, digits: int) -> list[int]:
    seeds: list[int] = []
    while len(seeds) < count:
        month, day = rng.randint(1, 12), rng.randint(1, 28)
        hour, minute, second = rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
        stamp = f"{month:02d}{day:02d}{hour:02d}{minute:02d}{second:02d}"
        if digits == 11:
            stamp += str(rng.randint(0, 9))
        seed = int(stamp)
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def sample_groups(benchmark: str) -> list[dict[str, Any]]:
    rng = random.Random(f"{SAMPLE_RANDOM_SEED}-{benchmark}")
    if benchmark == "tpcds":
        reference = [1001, 1002, 1003, 1004]
        used = {*reference, TPCDS_STREAM_SEED}
        return [
            {"group": "reference", "path": "per_template", "seeds": reference},
            {"group": "seedless", "path": "streams", "seeds": [TPCDS_STREAM_SEED], "streams": TPCDS_SEEDLESS_STREAMS},
            {"group": "spread", "path": "per_template", "seeds": _spread(rng, TPCDS_SPREAD_PER_TEMPLATE, used)},
            {
                "group": "spread",
                "path": "streams",
                "seeds": _spread(rng, TPCDS_SPREAD_STREAM_SEEDS, used),
                "streams": TPCDS_SPREAD_STREAMS_PER_SEED,
            },
            {
                "group": "load_end_time",
                "path": "streams",
                "seeds": _load_end_times(rng, TPCDS_LOAD_END_TIME_SEEDS, 11),
                "streams": TPCDS_LOAD_END_TIME_STREAMS,
            },
        ]
    used = set(TPCH_PROBE_SEEDS)
    return [
        {"group": "seedless", "path": "qgen", "seeds": [None]},
        {"group": "reference", "path": "qgen", "seeds": list(TPCH_PROBE_SEEDS)},
        {"group": "spread", "path": "qgen", "seeds": _spread(rng, TPCH_SPREAD_SEEDS, used)},
        {"group": "load_end_time", "path": "qgen", "seeds": _load_end_times(rng, TPCH_LOAD_END_TIME_SEEDS, 10)},
    ]


def draws_of(groups: list[dict[str, Any]]) -> list[Draw]:
    draws = []
    for group in groups:
        for seed in group["seeds"]:
            if group["path"] == "streams":
                draws.extend(Draw(group["group"], "streams", seed, index) for index in range(group["streams"]))
            else:
                draws.append(Draw(group["group"], group["path"], seed, None))
    return draws


def agreement_subset(draws: list[Draw], size: int = MIN_AGREEMENT_DRAWS) -> list[Draw]:
    always = [draw for draw in draws if draw.group in {"seedless", "reference"}]
    rest = [draw for draw in draws if draw not in always]
    need = max(0, size - len(always))
    picked = {int(index * len(rest) / need) for index in range(need)} if need else set()
    return always + [rest[index] for index in sorted(picked)]


class TpcdsSql:
    def __init__(self, benchmark: Any, scale_factor: float) -> None:
        self.benchmark = benchmark
        self.scale_factor = scale_factor
        self._streams: dict[tuple[int, int], dict[int, list[Any]]] = {}
        self._translated: dict[tuple[int, int, str], dict[str, str]] = {}

    def _stream_set(self, seed: int, count: int) -> dict[int, list[Any]]:
        key = (seed, count)
        if key not in self._streams:
            from benchbox.core.tpcds.streams import generate_dsqgen_streams

            self._streams[key] = generate_dsqgen_streams(num_streams=count, scale_factor=self.scale_factor, seed=seed)
        return self._streams[key]

    def stream_sql(self, draw: Draw, group_streams: int, dialect: str) -> dict[str, str]:
        key = (draw.seed, draw.stream, dialect)
        if key not in self._translated:
            statements: dict[str, str] = {}
            for stream_query in self._stream_set(draw.seed, group_streams)[draw.stream]:
                translated = self.benchmark.translate_query_text(stream_query.sql, "netezza", dialect)
                query_id = f"{stream_query.query_id}{stream_query.variant or ''}"
                statements[query_id] = self.benchmark._apply_target_dialect_overrides(
                    int(stream_query.query_id), translated, dialect, variant=stream_query.variant
                )
            self._translated[key] = statements
        return self._translated[key]

    def per_template_sql(self, query_id: str, seed: int, dialect: str) -> str:
        import re

        match = re.fullmatch(r"(\d+)([ab]?)", query_id)
        kwargs = {"variant": match.group(2)} if match.group(2) else {}
        return self.benchmark.get_query(
            int(match.group(1)), seed=seed, scale_factor=self.scale_factor, dialect=dialect, **kwargs
        )


def sql_factory(benchmark_id: str, benchmark: Any, scale_factor: float, groups: list[dict[str, Any]]):
    if benchmark_id == "tpch":

        def tpch_sql(draw: Draw) -> Callable[[str, str], str]:
            return lambda query_id, dialect: benchmark.get_query(
                int(query_id), seed=draw.seed, scale_factor=scale_factor, dialect=dialect
            )

        return tpch_sql

    helper = TpcdsSql(benchmark, scale_factor)
    stream_counts = {
        seed: group["streams"] for group in groups if group["path"] == "streams" for seed in group["seeds"]
    }

    def tpcds_sql(draw: Draw) -> Callable[[str, str], str]:
        if draw.path == "per_template":
            return lambda query_id, dialect: helper.per_template_sql(query_id, draw.seed, dialect)
        return lambda query_id, dialect: helper.stream_sql(draw, stream_counts[draw.seed], dialect)[query_id]

    return tpcds_sql


def counts_for_draws(
    counters: Mapping[str, gen.CountQuery],
    cache: dict[str, int],
    cache_path: Path,
    engine: str,
    draws: list[Draw],
    query_ids: tuple[str, ...],
    sql_for_draw: Callable[[Draw], Callable[[str, str], str]],
    *,
    data_sha256: str,
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for position, draw in enumerate(draws):
        label = draw.label
        sql_for = sql_for_draw(draw)
        counts: dict[str, int] = {}
        dirty = False
        for query_id in query_ids:
            sql = sql_for(query_id, engine)
            key = gen.count_cache_key(engine, data_sha256, sql, f"sample|{label}", query_id)
            if key not in cache:
                cache[key] = counters[engine](sql)
                dirty = True
            counts[query_id] = cache[key]
        if dirty:
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
        result[label] = counts
        print(f"{engine} sample {position + 1}/{len(draws)} {label}", file=sys.stderr, flush=True)
    return result


def integrity_section(
    benchmark_id: str,
    scale_factor: float,
    benchmark: Any,
    counters: Mapping[str, gen.CountQuery],
    cache: dict[str, int],
    cache_path: Path,
    work_dir: Path,
    *,
    versions: Mapping[str, str],
    generator: dict[str, Any],
    checksum: dict[str, Any],
    regenerate: str,
) -> tuple[dict[str, Any], dict[str, dict[str, int]], list[Draw]]:
    import datetime

    import benchbox

    query_ids = QUERY_IDS[benchmark_id]
    groups = sample_groups(benchmark_id)
    draws = draws_of(groups)
    sql_for_draw = sql_factory(benchmark_id, benchmark, scale_factor, groups)
    duckdb_counts = counts_for_draws(
        counters, cache, cache_path, "duckdb", draws, query_ids, sql_for_draw, data_sha256=checksum["sha256"]
    )
    subset = agreement_subset(draws)
    others = [engine for engine in gen.REFERENCE_ENGINES if engine != "duckdb"]
    other_counts = {
        engine: counts_for_draws(
            counters, cache, cache_path, engine, subset, query_ids, sql_for_draw, data_sha256=checksum["sha256"]
        )
        for engine in others
    }
    disagreements = [
        f"{label} Q{query_id}: duckdb={duckdb_counts[label][query_id]} {engine}={counts[query_id]}"
        for engine, per_draw in other_counts.items()
        for label, counts in per_draw.items()
        for query_id in query_ids
        if counts[query_id] != duckdb_counts[label][query_id]
    ]
    if disagreements:
        gen.report_disagreements(
            benchmark_id, scale_factor, disagreements, work_dir / f"sample-disagreements-sf{scale_factor:g}.txt"
        )
    violations = [
        f"{label} Q{query_id}: {count} against {bound.describe()}"
        for label, counts in duckdb_counts.items()
        for query_id, count in counts.items()
        if (bound := structural_bound(benchmark_id, query_id))
        and ((bound.limit is not None and count > bound.limit) or (bound.one_row and count != 1))
    ]
    if violations:
        raise SystemExit(
            f"{benchmark_id} SF {scale_factor:g}: the structural bounds table disagrees with the recorded sample:\n"
            + "\n".join(violations[:20])
        )
    invariant: dict[str, int] = {}
    never_empty: list[str] = []
    for query_id in query_ids:
        observed = {counts[query_id] for counts in duckdb_counts.values()}
        if len(observed) == 1:
            invariant[query_id] = next(iter(observed))
        if min(observed) >= 1:
            never_empty.append(query_id)
    integrity = {
        "sample": {
            "groups": groups,
            "draws": len(draws),
            "engines": {engine: versions[engine] for engine in gen.REFERENCE_ENGINES},
            "agreement": agreement_record(list(gen.REFERENCE_ENGINES), len(subset), len(subset) * len(query_ids)),
            "agreement_draws": [draw.label for draw in subset],
            "structural_check": {"statements": len(draws) * len(query_ids), "violations": 0},
            "data_stamp": generator["data_stamp"],
            "query_generation": generator["query_generation"],
            "data_checksum": checksum["sha256"],
            "benchbox_version": benchbox.__version__,
            "generated_on": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
            "regenerate": regenerate,
        },
        "invariant_counts": invariant,
        "never_empty": never_empty,
    }
    return integrity, duckdb_counts, draws


def agreement_record(engines: list[str], draws: int, statements: int) -> dict[str, Any]:
    return {
        "result": "identical",
        "engines": engines,
        "draws": draws,
        "statements": statements,
        "rule": "every statement of every agreement draw returned the same row count on every listed engine",
    }
