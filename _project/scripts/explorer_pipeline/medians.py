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
from benchbox.core.results.comparison_identity import NOT_RECORDED

_TIMING_KEYS = frozenset({"ms", "execution_time_ms", "duration_ms"})
_VERSION_PART = re.compile(r"(\d+)")
_POWER_LANES = frozenset({corpus_rule.LANE_POWER_ONLY, corpus_rule.LANE_SUBSCALE})

UNRECORDED_VERSION_SUPERSEDED = "unrecorded_version_superseded_by_release"

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


def _final_measured(data: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    queries = data.get("queries")
    legacy = corpus_rule.legacy_query_timing_defaults(data)
    default_run_type = "measurement" if legacy else None
    default_iteration = 1 if legacy else 0
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for query in queries if isinstance(queries, list) else []:
        if not isinstance(query, Mapping) or query.get("run_type", default_run_type) != "measurement":
            continue
        if _iteration(query, default_iteration) > 0:
            groups[str(query.get("test_type") or "power").lower()].append(query)
    return {
        group: [query for query in members if _iteration(query, default_iteration) == final]
        for group, members in groups.items()
        for final in [max(_iteration(query, default_iteration) for query in members)]
    }


def _iteration(query: Mapping[str, Any], default: int) -> int:
    value = query.get("iter", query.get("iteration", default))
    try:
        return 0 if isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return 0


def _median_queries(runs: Sequence[Mapping[str, Any]]) -> list[Any]:
    finals = [_final_measured(run) for run in runs]
    merged: list[Any] = []
    for group in dict.fromkeys(group for final in finals for group in final):
        peers: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for final in finals:
            for query in final.get(group, ()):
                peers[(str(query.get("stream")), str(query.get("id", query.get("query_id"))))].append(query)
        target = max(_iteration(query, 0) for members in peers.values() for query in members)
        for members in peers.values():
            query = copy.deepcopy(dict(members[0]))
            for key in _TIMING_KEYS:
                values = [member[key] for member in members if _numeric(member.get(key))]
                if values:
                    query[key] = statistics.median(values)
            query["iteration" if "iteration" in query and "iter" not in query else "iter"] = target
            merged.append(query)
    return merged


def _merge(nodes: Sequence[Any]) -> Any:
    first = nodes[0]
    if isinstance(first, Mapping):
        merged: dict[str, Any] = {}
        for key, value in first.items():
            peers = [node[key] for node in nodes if isinstance(node, Mapping) and key in node]
            if key in _TIMING_KEYS and all(_numeric(peer) for peer in peers):
                merged[key] = statistics.median(peers)
            else:
                merged[key] = _merge(peers)
        return merged
    if isinstance(first, list) and all(isinstance(node, list) and len(node) == len(first) for node in nodes):
        return [_merge([node[index] for node in nodes]) for index in range(len(first))]
    return copy.deepcopy(first)


def median_result(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    merged = _merge([{key: value for key, value in run.items() if key != "queries"} for run in results])
    merged["queries"] = _median_queries(results)
    return merged


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
    scored: dict[tuple[str, str, str], list[IdentityMedian]] = defaultdict(list)
    for median in medians:
        if median.median_score is not None:
            scored[(median.lane, median.platform_id, median.variant_id)].append(median)
    standing: dict[tuple[str, str, str], IdentityMedian] = {}
    for identity, candidates in scored.items():
        released = [median for median in candidates if median.version_is_release]
        unrecorded = [median for median in candidates if median.comparison_version == NOT_RECORDED]
        if released:
            standing[identity] = max(released, key=lambda median: version_order(median.comparison_version))
        elif unrecorded:
            standing[identity] = unrecorded[0]
    marked: list[IdentityMedian] = []
    for median in medians:
        identity = (median.lane, median.platform_id, median.variant_id)
        stands = standing.get(identity) is median
        superseded = (
            not stands
            and median.median_score is not None
            and median.comparison_version == NOT_RECORDED
            and standing.get(identity) is not None
            and standing[identity].version_is_release
        )
        marked.append(
            replace(
                median,
                is_leaderboard_version=stands,
                ranking_exclusion_reason=UNRECORDED_VERSION_SUPERSEDED if superseded else None,
            )
        )
    return marked


def _identity(median: IdentityMedian) -> tuple[str, str, str]:
    return (median.lane, median.variant_id, median.comparison_version)


def _exclusion_reason(median: IdentityMedian) -> str:
    if median.median_score is None:
        return "no_median_score"
    if not median.is_leaderboard_version:
        return median.ranking_exclusion_reason or "not_leaderboard_version"
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
