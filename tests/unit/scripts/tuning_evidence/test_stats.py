from __future__ import annotations

import math

import pytest

from scripts.tuning_evidence import stats

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROUNDS = 9


def constant(value: float, rounds: int = ROUNDS) -> list[float | None]:
    return [value] * rounds


def jittered(value: float, rounds: int = ROUNDS) -> list[float | None]:
    return [value * (1.0 + 0.04 * ((index * 7) % 5 - 2)) for index in range(rounds)]


def test_geometric_mean_of_ratios_equals_ratio_of_geometric_means() -> None:
    base = {"1": jittered(0.2), "2": jittered(3.0), "3": jittered(0.05), "4": jittered(12.0)}
    candidate = {"1": jittered(0.25), "2": jittered(2.1), "3": jittered(0.06), "4": jittered(15.0)}

    comparison = stats.compare("N", base, "T", candidate, seed=7, resamples=50)

    base_medians = [stats.median([value for value in base[query] if value is not None]) for query in comparison.common]
    candidate_medians = [
        stats.median([value for value in candidate[query] if value is not None]) for query in comparison.common
    ]
    assert comparison.g == pytest.approx(stats.geometric_mean(candidate_medians) / stats.geometric_mean(base_medians))
    assert comparison.g == pytest.approx(stats.geometric_mean([c / b for c, b in zip(candidate_medians, base_medians)]))


def test_median_and_percentile_follow_the_usual_definitions() -> None:
    assert stats.median([3.0, 1.0, 2.0]) == 2.0
    assert stats.median([4.0, 1.0, 2.0, 3.0]) == 2.5
    assert stats.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.25) == pytest.approx(2.0)
    assert stats.percentile([10.0, 20.0], 0.5) == pytest.approx(15.0)
    assert stats.percentile([10.0, 20.0, 30.0], 0.975) == pytest.approx(29.5)
    with pytest.raises(ValueError):
        stats.geometric_mean([1.0, 0.0])


def test_bootstrap_ci_collapses_on_a_constant_ratio() -> None:
    base = {"1": jittered(1.0), "2": jittered(4.0)}
    candidate = {query: [2.0 * value for value in series if value is not None] for query, series in base.items()}

    comparison = stats.compare("N", base, "T", candidate, seed=1, resamples=200)

    assert comparison.g == pytest.approx(2.0)
    assert comparison.g_ci_low == pytest.approx(2.0)
    assert comparison.g_ci_high == pytest.approx(2.0)
    for query in comparison.per_query:
        assert (query.ci_low, query.ratio, query.ci_high) == pytest.approx((2.0, 2.0, 2.0))


def test_bootstrap_resamples_rounds_with_a_recorded_seed() -> None:
    base = {"1": [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 1.0, 1.05], "2": jittered(2.0)}
    candidate = {"1": [1.1, 0.9, 1.0, 1.2, 1.0, 0.95, 1.1, 0.9, 1.0], "2": jittered(2.1)}

    first = stats.compare("N", base, "T", candidate, seed=11)
    second = stats.compare("N", base, "T", candidate, seed=11)

    assert first == second
    assert stats.resample_rounds(ROUNDS, 5, 11) != stats.resample_rounds(ROUNDS, 5, 12)
    assert first.resamples == stats.DEFAULT_RESAMPLES == 2000
    assert first.seed == 11
    assert first.g_ci_low is not None and first.g_ci_high is not None
    assert first.g_ci_low <= first.g <= first.g_ci_high
    assert stats.resample_rounds(ROUNDS, 3, 5) == stats.resample_rounds(ROUNDS, 3, 5)
    assert all(0 <= index < ROUNDS for indices in stats.resample_rounds(ROUNDS, 20, 5) for index in indices)


def test_bootstrap_ci_matches_a_hand_computed_percentile() -> None:
    base = {"1": [1.0, 2.0, 4.0]}
    candidate = {"1": [1.0, 1.0, 1.0]}
    comparison = stats.compare("N", base, "T", candidate, seed=3, resamples=400)

    expected = []
    for indices in stats.resample_rounds(3, 400, 3):
        expected.append(1.0 / stats.median([base["1"][index] for index in indices]))
    assert comparison.g_ci_low == pytest.approx(stats.percentile(expected, 0.025))
    assert comparison.g_ci_high == pytest.approx(stats.percentile(expected, 0.975))


def test_failures_are_reported_and_penalized_at_the_timeout() -> None:
    base = {"1": constant(1.0), "2": constant(2.0), "3": constant(4.0)}
    candidate = {"1": constant(1.0), "2": constant(2.0), "3": [4.0] * (ROUNDS - 1) + [None]}

    comparison = stats.compare("N", base, "T", candidate, seed=1, resamples=20, penalty_seconds=300.0)

    assert comparison.candidate_failures == ("3",)
    assert comparison.base_failures == ()
    assert comparison.common == ("1", "2")
    assert comparison.g == pytest.approx(1.0)
    assert comparison.g_penalized == pytest.approx((300.0 / 4.0) ** (1 / 3))
    assert comparison.queries == ("1", "2", "3")


def test_query_missing_from_an_arm_counts_as_failed() -> None:
    comparison = stats.compare(
        "N", {"1": constant(1.0), "2": constant(1.0)}, "T", {"1": constant(1.0)}, seed=1, resamples=10
    )
    assert comparison.candidate_failures == ("2",)
    assert comparison.g_penalized == pytest.approx(math.sqrt(300.0))


def test_samples_must_be_round_aligned() -> None:
    with pytest.raises(ValueError, match="round-aligned"):
        stats.compare("N", {"1": constant(1.0, 9)}, "T", {"1": constant(1.0, 8)}, seed=1)


def test_query_sort_key_orders_numerically() -> None:
    assert sorted(["10", "2", "1", "14a", "14"], key=stats.query_sort_key) == ["1", "2", "10", "14", "14a"]


def test_calibration_passes_for_identical_arms_and_fails_outside_the_band() -> None:
    thresholds = stats.RuleThresholds()
    base = {"1": jittered(1.0), "2": jittered(2.0)}
    same = stats.compare("A", base, "B", dict(base), seed=1, resamples=100)
    assert stats.calibration_check(same, thresholds).passed

    slower = {query: [1.08 * value for value in series if value is not None] for query, series in base.items()}
    drifted = stats.calibration_check(stats.compare("A", base, "B", slower, seed=1, resamples=100), thresholds)
    assert not drifted.passed
    assert any("does not contain 1.0" in reason for reason in drifted.reasons)
    assert any("outside [0.95, 1.05]" in reason for reason in drifted.reasons)


def test_calibration_fails_on_too_few_rounds_mismatches_or_unsettled_arms() -> None:
    thresholds = stats.RuleThresholds()
    base = {"1": constant(1.0, 5)}
    result = stats.calibration_check(
        stats.compare("A", base, "B", dict(base), seed=1, resamples=10),
        thresholds,
        answer_mismatches=["1"],
        unsettled_arms=["B"],
    )
    assert not result.passed
    assert len(result.reasons) == 3


def test_calibration_fails_when_arms_fail_different_queries() -> None:
    base = {"1": constant(1.0), "2": constant(1.0)}
    candidate = {"1": constant(1.0), "2": [None] * ROUNDS}
    result = stats.calibration_check(
        stats.compare("A", base, "B", candidate, seed=1, resamples=10), stats.RuleThresholds()
    )
    assert not result.passed


def _verdict(comparison: stats.Comparison, **overrides: object) -> stats.Verdict:
    arguments: dict = {
        "calibrated": True,
        "answer_mismatches": [],
        "base_load_settle_seconds": 10.0,
        "candidate_load_settle_seconds": 10.0,
    }
    arguments.update(overrides)
    return stats.verdict(comparison, stats.RuleThresholds(), **arguments)


def test_verdict_benefit_neutral_and_regression() -> None:
    base = {"1": jittered(1.0), "2": jittered(2.0)}
    faster = {query: [0.7 * value for value in series if value is not None] for query, series in base.items()}
    slower = {query: [1.3 * value for value in series if value is not None] for query, series in base.items()}

    assert _verdict(stats.compare("N", base, "T", faster, seed=1, resamples=100)).label == "benefit"
    assert _verdict(stats.compare("N", base, "T", slower, seed=1, resamples=100)).label == "regression"
    neutral = stats.compare("N", base, "T", dict(base), seed=1, resamples=100)
    assert _verdict(neutral).label == "neutral"
    assert _verdict(neutral, candidate_load_settle_seconds=15.1).label == "regression"
    assert _verdict(neutral, candidate_load_settle_seconds=15.0).label == "neutral"


def test_load_settle_rule_applies_only_when_otherwise_neutral() -> None:
    base = {"1": jittered(1.0), "2": jittered(2.0)}
    faster = {query: [0.7 * value for value in series if value is not None] for query, series in base.items()}
    outcome = _verdict(
        stats.compare("N", base, "T", faster, seed=1, resamples=100), candidate_load_settle_seconds=100.0
    )
    assert outcome.label == "benefit"


def test_verdict_regression_on_new_failure_or_answer_mismatch() -> None:
    base = {"1": constant(1.0), "2": constant(1.0)}
    broken = {"1": constant(1.0), "2": [None] * ROUNDS}
    assert _verdict(stats.compare("N", base, "T", broken, seed=1, resamples=10)).label == "regression"
    same = stats.compare("N", base, "T", dict(base), seed=1, resamples=10)
    mismatch = _verdict(same, answer_mismatches=["2"])
    assert mismatch.label == "regression"
    assert any("answers differ" in finding for finding in mismatch.findings)


def test_no_verdict_from_an_uncalibrated_or_blocked_cell() -> None:
    base = {"1": jittered(1.0)}
    slower = {"1": [2.0 * value for value in base["1"] if value is not None]}
    comparison = stats.compare("N", base, "T", slower, seed=1, resamples=50)

    uncalibrated = _verdict(comparison, calibrated=False)
    assert uncalibrated.label == "none"
    assert any("uncalibrated" in reason for reason in uncalibrated.blocked_by)
    assert uncalibrated.findings

    blocked = _verdict(comparison, blockers=["arms not settled: ['T']"])
    assert blocked.label == "none"


def test_slow_queries_need_ratio_and_ci_lower_bound() -> None:
    base = {"1": jittered(1.0), "2": jittered(1.0), "3": jittered(1.0)}
    candidate = {
        "1": [1.6 * value for value in base["1"] if value is not None],
        "2": [1.2 * value for value in base["2"] if value is not None],
        "3": list(base["3"]),
    }
    comparison = stats.compare("N", base, "T", candidate, seed=1, resamples=100)
    assert [query.query for query in stats.slow_queries(comparison, stats.RuleThresholds())] == ["1"]


def test_compare_totals_pairs_rounds() -> None:
    result = stats.compare_totals("N", [10.0, 11.0, 9.0], "T", [5.0, 5.5, 4.5], seed=2, resamples=100)
    assert result.ratio == pytest.approx(0.5)
    assert result.ci_low == pytest.approx(0.5)
    assert result.ci_high == pytest.approx(0.5)
    with pytest.raises(ValueError):
        stats.compare_totals("N", [1.0], "T", [1.0, 2.0], seed=1)


