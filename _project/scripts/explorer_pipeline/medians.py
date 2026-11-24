from __future__ import annotations

import copy
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    DetailResult,
    PlatformRow,
    canonical_benchmark_slug,
    get_ranking_config,
    primary_metric_value,
)
from _project.scripts.explorer_pipeline.ranking import MINIMUM_RANKED_COHORT, rank_scores
from benchbox.core.results import corpus_rule, corpus_score

_TIMING_KEYS = frozenset({"ms", "execution_time_ms", "duration_ms"})
_VERSION_PART = re.compile(r"(\d+)")
_POWER_LANES = frozenset({corpus_rule.LANE_POWER_ONLY, corpus_rule.LANE_SUBSCALE})

_SummaryKey = tuple[str, float, str, int | None]


@dataclass(frozen=True)
class IdentityMedian:
    key: _SummaryKey
    cohort_key: str
    lane: str
    platform_id: str
    variant_id: str
    comparison_version: str
    version_is_release: bool
    run_count: int
    median_score: float | None
    spread: float | None
    is_leaderboard_version: bool
    rank: int | None = None
    total_in_cohort: int = 0
    ranking_exclusion_reason: str | None = None


def cohort_key_text(key: _SummaryKey) -> str:
    benchmark, scale_factor, phase, streams = key
    label = f"{benchmark}/sf{scale_factor:g}/{phase}"
    return label if streams is None else f"{label}/streams={streams}"


def version_order(version: str) -> tuple[tuple[int, int, str], ...]:
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in _VERSION_PART.split(version) if part
    )


def _numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _query_key(query: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(query.get("test_type")),
        str(query.get("stream")),
        str(query.get("id", query.get("query_id"))),
        str(query.get("iter", query.get("iteration"))),
        str(query.get("run_type")),
    )


def _merge_queries(lists: Sequence[Sequence[Any]]) -> list[Any]:
    indexed = [{_query_key(query): query for query in queries if isinstance(query, Mapping)} for queries in lists]
    merged: list[Any] = []
    for query in lists[0]:
        if not isinstance(query, Mapping):
            merged.append(copy.deepcopy(query))
            continue
        peers = [index[_query_key(query)] for index in indexed if _query_key(query) in index]
        merged.append(_merge(peers))
    return merged


def _merge(nodes: Sequence[Any]) -> Any:
    first = nodes[0]
    if isinstance(first, Mapping):
        merged: dict[str, Any] = {}
        for key, value in first.items():
            peers = [node[key] for node in nodes if isinstance(node, Mapping) and key in node]
            if key in _TIMING_KEYS and all(_numeric(peer) for peer in peers):
                merged[key] = statistics.median(peers)
            elif key == "queries" and all(isinstance(peer, list) for peer in peers):
                merged[key] = _merge_queries(peers)
            else:
                merged[key] = _merge(peers)
        return merged
    if isinstance(first, list) and all(isinstance(node, list) and len(node) == len(first) for node in nodes):
        return [_merge([node[index] for node in nodes]) for index in range(len(first))]
    return copy.deepcopy(first)


def median_result(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _merge(list(results))


def _lane_score(lane: str, data: Mapping[str, Any]) -> float | None:
    if lane in _POWER_LANES:
        return corpus_score.power_at_size(data)
    if lane == corpus_rule.LANE_THROUGHPUT:
        return corpus_score.throughput_at_size(data)
    return corpus_score.combined_score(data)


def _own_lane_score(details: Sequence[DetailResult]) -> float | None:
    by_query: dict[str, list[float]] = defaultdict(list)
    for detail in details:
        for timing in detail.display_timings:
            if timing.display_ms is not None and timing.display_ms > 0:
                by_query[timing.query_id].append(timing.display_ms)
    medians = [statistics.median(values) for values in by_query.values()]
    if not medians:
        return None
    return statistics.geometric_mean(medians)


def _median_score(
    lane: str,
    key: _SummaryKey,
    rows: Sequence[PlatformRow],
    data_by_result: Mapping[str, Mapping[str, Any]],
    details_by_result: Mapping[str, DetailResult],
) -> float | None:
    if lane == corpus_rule.LANE_OWN or canonical_benchmark_slug(key[0]) not in corpus_score.POWER_QUERY_COUNTS:
        return _own_lane_score([details_by_result[row.result_id] for row in rows])
    return _lane_score(lane, median_result([data_by_result[row.result_id] for row in rows]))


def _spread(scores: Sequence[float], median_score: float | None) -> float | None:
    if median_score is None or median_score == 0:
        return None
    return (max(scores) - min(scores)) / median_score


def build_identity_medians(
    summaries: Sequence[tuple[_SummaryKey, BenchmarkSummary]],
    data_by_result: Mapping[str, Mapping[str, Any]],
    details_by_result: Mapping[str, DetailResult],
) -> list[IdentityMedian]:
    medians: list[IdentityMedian] = []
    for key, summary in summaries:
        ranking = get_ranking_config(summary.benchmark, summary.phase)
        metric = ranking.primary_metric
        groups: dict[tuple[str, str, str, str, bool], list[PlatformRow]] = defaultdict(list)
        for row in summary.platforms:
            if row.lane is None:
                continue
            group = (row.lane, row.platform_id, row.variant_id, row.comparison_version, row.version_is_release)
            groups[group].append(row)
        cohort_medians: list[IdentityMedian] = []
        for (lane, platform_id, variant_id, version, is_release), rows in groups.items():
            ranked = [row for row in rows if row.is_ranking_eligible]
            scores = [value for row in ranked if (value := primary_metric_value(row, metric)) is not None]
            median_score = _median_score(lane, key, ranked, data_by_result, details_by_result) if ranked else None
            cohort_medians.append(
                IdentityMedian(
                    key=key,
                    cohort_key=cohort_key_text(key),
                    lane=lane,
                    platform_id=platform_id,
                    variant_id=variant_id,
                    comparison_version=version,
                    version_is_release=is_release,
                    run_count=len(ranked),
                    median_score=median_score,
                    spread=_spread(scores, median_score) if len(ranked) > 1 else (0.0 if ranked else None),
                    is_leaderboard_version=False,
                )
            )
        marked = _mark_leaderboard_versions(cohort_medians)
        medians.extend(rank_leaderboard_versions(marked, higher_is_better=ranking.primary_order == "desc"))
    return medians


def _mark_leaderboard_versions(medians: list[IdentityMedian]) -> list[IdentityMedian]:
    standing: dict[tuple[str, str, str], IdentityMedian] = {}
    for median in medians:
        if median.median_score is None or not median.version_is_release:
            continue
        identity = (median.lane, median.platform_id, median.variant_id)
        current = standing.get(identity)
        if current is None or version_order(median.comparison_version) > version_order(current.comparison_version):
            standing[identity] = median
    return [
        replace(
            median,
            is_leaderboard_version=standing.get((median.lane, median.platform_id, median.variant_id)) is median,
        )
        for median in medians
    ]


def _identity(median: IdentityMedian) -> tuple[str, str, str]:
    return (median.lane, median.variant_id, median.comparison_version)


def _exclusion_reason(median: IdentityMedian) -> str:
    if median.median_score is None:
        return "no_median_score"
    if not median.is_leaderboard_version:
        return "not_leaderboard_version"
    return "below_cohort_minimum"


def rank_leaderboard_versions(medians: list[IdentityMedian], *, higher_is_better: bool) -> list[IdentityMedian]:
    standing_by_lane: dict[str, list[IdentityMedian]] = defaultdict(list)
    for median in medians:
        if median.is_leaderboard_version and median.median_score is not None:
            standing_by_lane[median.lane].append(median)
    ranks: dict[tuple[str, str, str], int] = {}
    for standing in standing_by_lane.values():
        if len(standing) < MINIMUM_RANKED_COHORT:
            continue
        scores = [median.median_score for median in standing if median.median_score is not None]
        for median, rank in zip(standing, rank_scores(scores, higher_is_better=higher_is_better), strict=True):
            ranks[_identity(median)] = rank
    return [
        replace(
            median,
            rank=ranks.get(_identity(median)),
            total_in_cohort=len(standing_by_lane.get(median.lane, [])),
            ranking_exclusion_reason=None if _identity(median) in ranks else _exclusion_reason(median),
        )
        for median in medians
    ]
