from __future__ import annotations

import pytest

from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    ManifestEntry,
    PlatformRow,
    RankingConfig,
    is_ranking_eligible,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _rankable_entry(**overrides) -> ManifestEntry:

    base = {
        "result_id": "r1",
        "benchmark": "tpch",
        "scale_factor": 1.0,
        "platform": "duckdb",
        "driver_version": "1.0",
        "run_date": "2026-01-01",
        "power_score": 1.0,
        "total_duration_s": 1.0,
        "query_count": 22,
        "display_geomean_ms": 100.0,
        "trust_label": "maintainer-run",
        "visibility": "public-curated",
        "validation_status": "passed",
    }
    base.update(overrides)
    return ManifestEntry(**base)


def test_official_entry_is_ranking_eligible() -> None:
    assert is_ranking_eligible(_rankable_entry())
    assert ranking_exclusion_reason(_rankable_entry()) is None


@pytest.mark.parametrize("status", [None, "not_applicable", "noop", "failed", "not_validated"])
def test_custom_tuning_without_applied_evidence_is_not_ranked(status: str | None) -> None:
    entry = _rankable_entry(tuning_mode="custom", tuning_validation_status=status)

    assert not is_ranking_eligible(entry)
    assert ranking_exclusion_reason(entry) == "tuning_not_applied"


@pytest.mark.parametrize("status", ["applied_unverified", "applied_verified"])
def test_custom_tuning_with_applied_evidence_remains_rankable(status: str) -> None:
    entry = _rankable_entry(tuning_mode="custom", tuning_validation_status=status)

    assert is_ranking_eligible(entry)
    assert ranking_exclusion_reason(entry) is None


def test_nonstandard_compliance_is_never_ranked() -> None:
    entry = _rankable_entry(compliance_class="unofficial_nonstandard")

    assert not is_ranking_eligible(entry)
    assert ranking_exclusion_reason(entry) == "unofficial_compliance"


def test_subscale_compliance_is_ranked() -> None:
    entry = _rankable_entry(compliance_class="unofficial_subscale")

    assert is_ranking_eligible(entry)
    assert ranking_exclusion_reason(entry) is None


def _make_summary(
    *,
    primary_metric: str,
    primary_order: str,
    platforms: list[PlatformRow],
) -> BenchmarkSummary:
    return BenchmarkSummary(
        benchmark="tpch",
        scale_factor=0.01,
        phase="power",
        query_ids=[],
        platforms=platforms,
        ranking=RankingConfig(
            primary_metric=primary_metric,
            secondary_metric="display_geomean_ms",
            primary_order=primary_order,
        ),
    )


def _make_platform_row(
    platform_id: str,
    *,
    power_score: float | None = None,
    display_geomean_ms: float | None = None,
) -> PlatformRow:
    return PlatformRow(
        result_id=f"result-{platform_id}",
        short_id=platform_id,
        platform_id=platform_id,
        platform=platform_id,
        platform_version=None,
        tuning_mode=None,
        tuning_hash=None,
        execution_mode=None,
        trust_label="maintainer-run",
        run_date="2026-05-05T00:00:00Z",
        is_ranking_eligible=True,
        power_score=power_score,
        display_geomean_ms=display_geomean_ms,
        sample_geomean_ms=None,
        cost_usd=None,
        compliance_class=None,
        percentile_stats=None,
        phase_durations=None,
        timings={},
    )


def test_latency_speedup_references_ignore_non_positive_metric_values() -> None:
    ranked = rank_platforms(
        _make_summary(
            primary_metric="display_geomean_ms",
            primary_order="asc",
            platforms=[
                _make_platform_row("zero", display_geomean_ms=0.0),
                _make_platform_row("duckdb", display_geomean_ms=100.0),
                _make_platform_row("sqlite", display_geomean_ms=200.0),
            ],
        )
    )

    by_platform = {row.row.platform_id: row for row in ranked.rows}

    assert ranked.best_value == pytest.approx(100.0)
    assert ranked.slowest_value == pytest.approx(200.0)
    assert ranked.total_ranked == 2
    assert by_platform["zero"].rank is None
    assert by_platform["zero"].total_ranked == 2
    assert by_platform["zero"].ranking_exclusion_reason == "non_positive_primary_metric"
    assert by_platform["duckdb"].rank == 1
    assert by_platform["sqlite"].rank == 2
    assert by_platform["zero"].speedup_vs_best is None
    assert by_platform["duckdb"].speedup_vs_best == pytest.approx(1.0)
    assert by_platform["sqlite"].speedup_vs_slowest == pytest.approx(1.0)


def test_power_score_speedup_references_ignore_non_positive_metric_values() -> None:
    ranked = rank_platforms(
        _make_summary(
            primary_metric="power_score",
            primary_order="desc",
            platforms=[
                _make_platform_row("duckdb", power_score=3000.0),
                _make_platform_row("sqlite", power_score=1500.0),
                _make_platform_row("negative", power_score=-1.0),
            ],
        )
    )

    by_platform = {row.row.platform_id: row for row in ranked.rows}

    assert ranked.best_value == pytest.approx(3000.0)
    assert ranked.slowest_value == pytest.approx(1500.0)
    assert ranked.total_ranked == 2
    assert by_platform["duckdb"].rank == 1
    assert by_platform["sqlite"].rank == 2
    assert by_platform["negative"].rank is None
    assert by_platform["negative"].total_ranked == 2
    assert by_platform["negative"].ranking_exclusion_reason == "non_positive_primary_metric"
    assert by_platform["duckdb"].speedup_vs_best == pytest.approx(1.0)
    assert by_platform["sqlite"].speedup_vs_slowest == pytest.approx(1.0)
    assert by_platform["negative"].speedup_vs_slowest is None
