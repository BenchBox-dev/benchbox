from __future__ import annotations

import pytest

from _project.scripts.explorer_pipeline.medians import IdentityMedian, cohort_key_text, rank_leaderboard_versions
from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    PlatformRow,
    RankingConfig,
    get_ranking_config,
    primary_metric_value,
)
from _project.scripts.explorer_pipeline.pipeline import _build_meta_leaderboard as _meta_from_medians

pytestmark = [pytest.mark.unit, pytest.mark.fast]


_GENERATED_AT = "2026-04-17T00:00:00Z"


def _single_run_medians(summaries: list) -> list[IdentityMedian]:
    medians = []
    for key, summary in summaries:
        ranking = summary.ranking or get_ranking_config(summary.benchmark, summary.phase)
        cohort = []
        for row in summary.platforms:
            score = primary_metric_value(row, ranking.primary_metric) if row.is_ranking_eligible else None
            cohort.append(
                IdentityMedian(
                    key=key,
                    cohort_key=cohort_key_text(key),
                    lane="power_only",
                    platform_id=row.platform_id,
                    variant_id=row.variant_id or row.platform_id,
                    comparison_version="1.0.0",
                    version_is_release=True,
                    run_count=1,
                    median_score=score,
                    spread=0.0,
                    is_leaderboard_version=score is not None,
                )
            )
        medians.extend(rank_leaderboard_versions(cohort, higher_is_better=ranking.primary_order == "desc"))
    return medians


def _build_meta_leaderboard(summaries: list, generated_at: str) -> dict:
    return _meta_from_medians(summaries, generated_at, medians=_single_run_medians(summaries))


def _make_summary(
    benchmark: str = "tpch",
    scale_factor: float = 0.1,
    phase: str = "power",
    platforms: list[PlatformRow] | None = None,
    ranking: RankingConfig | None = None,
) -> BenchmarkSummary:
    return BenchmarkSummary(
        benchmark=benchmark,
        scale_factor=scale_factor,
        phase=phase,
        query_ids=["Q1", "Q2"],
        platforms=platforms or [],
        cell_reduction="median",
        ranking=ranking,
    )


def _make_platform_row(
    platform_id: str,
    platform: str = "",
    power_score: float | None = None,
    display_geomean_ms: float | None = None,
    is_ranking_eligible: bool = True,
) -> PlatformRow:
    return PlatformRow(
        result_id=f"result-{platform_id}",
        short_id="",
        platform_id=platform_id,
        platform=platform or platform_id,
        platform_version=None,
        tuning_mode=None,
        tuning_hash=None,
        execution_mode=None,
        trust_label="maintainer-run",
        run_date="2026-04-01",
        is_ranking_eligible=is_ranking_eligible,
        power_score=power_score,
        display_geomean_ms=display_geomean_ms,
        sample_geomean_ms=None,
        cost_usd=None,
        compliance_class=None,
        percentile_stats=None,
        phase_durations=None,
        timings={},
    )


class TestMetaRankWideningLatency:
    def _build(self) -> dict:
        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("sqlite", display_geomean_ms=200.0),
            _make_platform_row("polars", display_geomean_ms=400.0),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        summaries = [(("clickbench", 0.1, "power", None), summary)]
        return _build_meta_leaderboard(summaries, _GENERATED_AT)

    def _cohort_rank(self, data: dict, platform_id: str) -> dict:
        cohort_key = "clickbench-sf0.1-power"
        p = next(p for p in data["platforms"] if p["platform_id"] == platform_id)
        return p["ranks"][cohort_key]

    def test_rank_and_total_present(self) -> None:
        data = self._build()
        r = self._cohort_rank(data, "duckdb")
        assert r["rank"] == 1
        assert r["total"] == 3

    def test_metric_value_matches_platform(self) -> None:
        data = self._build()
        assert self._cohort_rank(data, "duckdb")["metric_value"] == pytest.approx(100.0)
        assert self._cohort_rank(data, "sqlite")["metric_value"] == pytest.approx(200.0)

    def test_speedup_best_is_1x(self) -> None:

        data = self._build()
        assert self._cohort_rank(data, "duckdb")["speedup_vs_best"] == pytest.approx(1.0)

    def test_speedup_slower_platform_less_than_1x(self) -> None:

        data = self._build()
        assert self._cohort_rank(data, "sqlite")["speedup_vs_best"] == pytest.approx(0.5)

    def test_speedup_three_times_slower(self) -> None:
        data = self._build()
        assert self._cohort_rank(data, "polars")["speedup_vs_best"] == pytest.approx(0.25)

    def test_primary_metric_and_order_on_cohort(self) -> None:

        data = self._build()
        cohort = next(c for c in data["cohorts"] if c["key"] == "clickbench-sf0.1-power")
        assert cohort["primary_metric"] == "display_geomean_ms"
        assert cohort["primary_order"] == "asc"


class TestMetaRankWideningThroughput:
    def _build(self) -> dict:
        ranking = RankingConfig(
            primary_metric="power_score",
            secondary_metric="display_geomean_ms",
            primary_order="desc",
        )
        rows = [
            _make_platform_row("duckdb", power_score=3000.0),
            _make_platform_row("sqlite", power_score=1500.0),
        ]
        summary = _make_summary(benchmark="tpch", ranking=ranking, platforms=rows)
        summaries = [(("tpch", 0.1, "power", None), summary)]
        return _build_meta_leaderboard(summaries, _GENERATED_AT)

    def _rank(self, data: dict, platform_id: str) -> dict:
        return data["platforms"][next(i for i, p in enumerate(data["platforms"]) if p["platform_id"] == platform_id)][
            "ranks"
        ]["tpch-sf0.1-power"]

    def test_best_platform_speedup_is_1x(self) -> None:

        data = self._build()
        assert self._rank(data, "duckdb")["speedup_vs_best"] == pytest.approx(1.0)

    def test_slower_platform_less_than_1x(self) -> None:

        data = self._build()
        assert self._rank(data, "sqlite")["speedup_vs_best"] == pytest.approx(0.5)

    def test_primary_order_desc_on_cohort(self) -> None:

        data = self._build()
        cohort = next(c for c in data["cohorts"] if c["key"] == "tpch-sf0.1-power")
        assert cohort["primary_metric"] == "power_score"
        assert cohort["primary_order"] == "desc"


class TestMetaRankNullMetric:
    def test_null_metric_excluded_from_ranks(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("failed", display_geomean_ms=None),
            _make_platform_row("sqlite", display_geomean_ms=200.0),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)

        cohort_key = "clickbench-sf0.1-power"
        failed_platform = next((p for p in data["platforms"] if p["platform_id"] == "failed"), None)
        if failed_platform:
            assert cohort_key not in failed_platform["ranks"]

    def test_null_metric_excluded_from_total_count(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("failed", display_geomean_ms=None),
            _make_platform_row("sqlite", display_geomean_ms=200.0),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)

        cohort_key = "clickbench-sf0.1-power"
        duckdb = next(p for p in data["platforms"] if p["platform_id"] == "duckdb")

        assert duckdb["ranks"][cohort_key]["total"] == 2


class TestMetaRankEligibility:
    def test_ineligible_platform_does_not_affect_rank_total_or_speedup(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("community", display_geomean_ms=50.0, is_ranking_eligible=False),
            _make_platform_row("sqlite", display_geomean_ms=200.0),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)

        cohort_key = "clickbench-sf0.1-power"
        cohort = next(c for c in data["cohorts"] if c["key"] == cohort_key)
        assert cohort["platform_count"] == 2

        by_platform = {p["platform_id"]: p for p in cohort["platforms"]}
        assert by_platform["community"]["rank"] is None
        assert by_platform["community"]["metric_value"] == pytest.approx(50.0)
        assert by_platform["community"]["speedup_vs_best"] is None

        duckdb = next(p for p in data["platforms"] if p["platform_id"] == "duckdb")
        sqlite = next(p for p in data["platforms"] if p["platform_id"] == "sqlite")
        assert duckdb["ranks"][cohort_key]["rank"] == 1
        assert duckdb["ranks"][cohort_key]["total"] == 2
        assert duckdb["ranks"][cohort_key]["speedup_vs_best"] == pytest.approx(1.0)
        assert sqlite["ranks"][cohort_key]["rank"] == 2
        assert sqlite["ranks"][cohort_key]["total"] == 2
        assert sqlite["ranks"][cohort_key]["speedup_vs_best"] == pytest.approx(0.5)

        assert not any(p["platform_id"] == "community" for p in data["platforms"])

    def test_cohort_with_fewer_than_two_rankable_platforms_is_excluded(self) -> None:
        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("community", display_geomean_ms=50.0, is_ranking_eligible=False),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)

        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)

        assert data["cohorts"] == []
        assert data["platforms"] == []


class TestMetaLeaderboardEdgeCases:
    def test_empty_summaries_yields_empty_artifact(self) -> None:
        data = _build_meta_leaderboard([], _GENERATED_AT)
        assert data["cohorts"] == []
        assert data["platforms"] == []

    def test_single_platform_cohort_excluded_from_output(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [_make_platform_row("duckdb", display_geomean_ms=100.0)]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)
        assert data["cohorts"] == []
        assert data["platforms"] == []

    def test_avg_rank_computed_from_participated_cohorts_only(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )

        cb_rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("sqlite", display_geomean_ms=200.0),
        ]

        ny_rows = [
            _make_platform_row("duckdb", display_geomean_ms=50.0),
            _make_platform_row("sqlite", display_geomean_ms=None),
        ]
        cb_summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=cb_rows)
        ny_summary = _make_summary(benchmark="nyctaxi", ranking=ranking, platforms=ny_rows)
        data = _build_meta_leaderboard(
            [(("clickbench", 0.1, "power", None), cb_summary), (("nyctaxi", 0.1, "power", None), ny_summary)],
            _GENERATED_AT,
        )

        sqlite_row = next(p for p in data["platforms"] if p["platform_id"] == "sqlite")
        assert sqlite_row["avg_rank"] == pytest.approx(2.0)

        duckdb_row = next(p for p in data["platforms"] if p["platform_id"] == "duckdb")
        assert duckdb_row["avg_rank"] == pytest.approx(1.0)


class TestMetaLeaderboardTieHandling:
    def test_equal_metric_values_get_same_rank(self) -> None:

        ranking = RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        )
        rows = [
            _make_platform_row("duckdb", display_geomean_ms=100.0),
            _make_platform_row("sqlite", display_geomean_ms=100.0),
            _make_platform_row("polars", display_geomean_ms=200.0),
        ]
        summary = _make_summary(benchmark="clickbench", ranking=ranking, platforms=rows)
        data = _build_meta_leaderboard([(("clickbench", 0.1, "power", None), summary)], _GENERATED_AT)

        cohort_key = "clickbench-sf0.1-power"
        duckdb_rank = next(p for p in data["platforms"] if p["platform_id"] == "duckdb")["ranks"][cohort_key]["rank"]
        sqlite_rank = next(p for p in data["platforms"] if p["platform_id"] == "sqlite")["ranks"][cohort_key]["rank"]
        polars_rank = next(p for p in data["platforms"] if p["platform_id"] == "polars")["ranks"][cohort_key]["rank"]

        assert duckdb_rank == 1
        assert sqlite_rank == 1
        assert polars_rank == 3
