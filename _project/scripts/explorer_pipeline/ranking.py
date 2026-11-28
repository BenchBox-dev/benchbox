from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from _project.scripts.explorer_pipeline.compare_math import speedup_vs_best, speedup_vs_slowest
from _project.scripts.explorer_pipeline.models import BenchmarkSummary, PlatformRow, primary_metric_value

MINIMUM_RANKED_COHORT = 2


@dataclass(frozen=True)
class RankedPlatform:
    row: PlatformRow
    metric_value: float | None
    rank: int | None
    total_ranked: int
    speedup_vs_best: float | None
    speedup_vs_slowest: float | None
    ranking_exclusion_reason: str | None


@dataclass(frozen=True)
class RankedCohort:
    rows: list[RankedPlatform]
    primary_metric: str
    primary_order: str
    higher_is_better: bool
    total_ranked: int
    best_value: float | None
    slowest_value: float | None


def rank_scores(scores: Sequence[float], *, higher_is_better: bool) -> list[int]:
    order = sorted(range(len(scores)), key=lambda index: -scores[index] if higher_is_better else scores[index])
    ranks = [0] * len(scores)
    current_rank = 1
    for position, index in enumerate(order, start=1):
        if position > 1 and scores[index] != scores[order[position - 2]]:
            current_rank = position
        ranks[index] = current_rank
    return ranks


def rank_platforms(summary: BenchmarkSummary) -> RankedCohort:

    ranking = summary.ranking
    primary_metric = ranking.primary_metric if ranking else "display_geomean_ms"
    primary_order = ranking.primary_order if ranking else "asc"
    higher_is_better = primary_order == "desc"

    def metric_value(row: PlatformRow) -> float | None:
        return primary_metric_value(row, primary_metric)

    def ranking_exclusion_reason(row: PlatformRow, value: float | None) -> str | None:
        if not row.is_ranking_eligible:
            return row.ranking_exclusion_reason or "not_ranking_eligible"
        if value is None:
            return "missing_primary_metric"
        if not math.isfinite(float(value)) or float(value) <= 0:
            return "non_positive_primary_metric"
        return None

    def is_rankable(row: PlatformRow, value: float | None) -> bool:
        return ranking_exclusion_reason(row, value) is None

    def sort_key(row: PlatformRow) -> tuple[bool, float, str, str]:
        value = metric_value(row)
        if not is_rankable(row, value):
            return (True, 0.0, row.platform_id, row.result_id)
        assert value is not None
        return (False, -value if higher_is_better else value, row.platform_id, row.result_id)

    sorted_rows = sorted(summary.platforms, key=sort_key)
    rankable_values: list[float] = []
    for row in sorted_rows:
        value = metric_value(row)
        if is_rankable(row, value):
            assert value is not None
            rankable_values.append(value)
    best_value = (max(rankable_values) if higher_is_better else min(rankable_values)) if rankable_values else None
    slowest_value = (min(rankable_values) if higher_is_better else max(rankable_values)) if rankable_values else None

    ranked_rows: list[RankedPlatform] = []
    current_rank = 1
    rankable_index = 0
    previous_value: float | None = None
    for row in sorted_rows:
        value = metric_value(row)
        row_exclusion_reason = ranking_exclusion_reason(row, value)
        if row_exclusion_reason is not None:
            rank = None
            rank_speedup_vs_best = None
            rank_speedup_vs_slowest = None
        else:
            assert value is not None
            rankable_index += 1
            if previous_value is not None and value != previous_value:
                current_rank = rankable_index
            rank = current_rank
            previous_value = value
            rank_speedup_vs_best = speedup_vs_best(value, best_value, higher_is_better=higher_is_better)
            rank_speedup_vs_slowest = speedup_vs_slowest(value, slowest_value, higher_is_better=higher_is_better)
        ranked_rows.append(
            RankedPlatform(
                row=row,
                metric_value=value,
                rank=rank,
                total_ranked=len(rankable_values),
                speedup_vs_best=rank_speedup_vs_best,
                speedup_vs_slowest=rank_speedup_vs_slowest,
                ranking_exclusion_reason=row_exclusion_reason,
            )
        )

    return RankedCohort(
        rows=ranked_rows,
        primary_metric=primary_metric,
        primary_order=primary_order,
        higher_is_better=higher_is_better,
        total_ranked=len(rankable_values),
        best_value=best_value,
        slowest_value=slowest_value,
    )
