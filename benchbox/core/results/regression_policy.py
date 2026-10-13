# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Final, Literal

DEFAULT_REGRESSION_THRESHOLD_PERCENT: Final = 10.0

TREND_SIGNIFICANCE_PERCENT: Final = 5.0

SEVERITY_LADDER: Final = (
    ("critical", 100.0),
    ("high", 50.0),
    ("medium", 25.0),
)
DEFAULT_SEVERITY: Final = "low"

ChangeClass = Literal["regression", "improvement", "stable"]
TrendDirection = Literal["improving", "degrading", "stable", "insufficient_data", "unknown"]

CHANGE_CLASSES: Final = ("regression", "improvement", "stable")
TREND_DIRECTIONS: Final = ("improving", "degrading", "stable", "insufficient_data", "unknown")
SEVERITIES: Final = ("critical", "high", "medium", "low")


def percent_change(baseline: float, current: float) -> float | None:
    if baseline <= 0:
        return None
    return ((current - baseline) / baseline) * 100.0


def classify_severity(delta_percent: float) -> str:
    for severity, lower_bound in SEVERITY_LADDER:
        if delta_percent >= lower_bound:
            return severity
    return DEFAULT_SEVERITY


def classify_change(
    delta_percent: float,
    threshold_percent: float = DEFAULT_REGRESSION_THRESHOLD_PERCENT,
) -> ChangeClass:
    if delta_percent > threshold_percent:
        return "regression"
    if delta_percent < -threshold_percent:
        return "improvement"
    return "stable"


def is_regression(
    delta_percent: float | None,
    threshold_percent: float = DEFAULT_REGRESSION_THRESHOLD_PERCENT,
) -> bool:
    if delta_percent is None:
        return False
    return classify_change(delta_percent, threshold_percent) == "regression"


def classify_trend(values: list[float]) -> tuple[TrendDirection, float]:
    if len(values) < 2:
        return "insufficient_data", 0.0

    change = percent_change(values[0], values[-1])
    if change is None:
        return "unknown", 0.0

    if change < -TREND_SIGNIFICANCE_PERCENT:
        return "improving", change
    if change > TREND_SIGNIFICANCE_PERCENT:
        return "degrading", change
    return "stable", change


def is_meaningful_improvement(change_percent: float | None) -> bool:
    if change_percent is None:
        return False
    return change_percent < -TREND_SIGNIFICANCE_PERCENT


__all__ = [
    "CHANGE_CLASSES",
    "DEFAULT_REGRESSION_THRESHOLD_PERCENT",
    "DEFAULT_SEVERITY",
    "SEVERITIES",
    "SEVERITY_LADDER",
    "TREND_DIRECTIONS",
    "TREND_SIGNIFICANCE_PERCENT",
    "ChangeClass",
    "TrendDirection",
    "classify_change",
    "classify_severity",
    "classify_trend",
    "is_meaningful_improvement",
    "is_regression",
    "percent_change",
]
