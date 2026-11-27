from __future__ import annotations

from dataclasses import asdict

import pytest

from benchbox.core.execution_engine import (
    MIXED,
    NOT_CAPTURED,
    ExecutionEngineReceipt,
    summarize_observed_engines,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _receipt(**kwargs) -> ExecutionEngineReceipt:
    return ExecutionEngineReceipt(
        requested="default", applied=None, applied_class=None, resolution="platform_default", **kwargs
    )


def test_default_receipt_is_not_captured_without_source_or_reason():
    receipt = _receipt()
    assert (receipt.observed, receipt.observed_source, receipt.observed_reason) == (NOT_CAPTURED, "none", None)


def test_not_captured_keeps_the_reason_it_was_given():
    assert _receipt(observed_reason="library cannot report").observed_reason == "library cannot report"


@pytest.mark.parametrize("observed", ["in-memory", MIXED])
def test_observed_engine_needs_a_source(observed):
    with pytest.raises(ValueError, match="needs the source"):
        _receipt(observed=observed)


def test_not_captured_cannot_name_a_source():
    with pytest.raises(ValueError, match="cannot name source"):
        _receipt(observed_source="explain")


def test_observed_engine_cannot_carry_a_not_captured_reason():
    with pytest.raises(ValueError, match="cannot carry"):
        _receipt(observed="in-memory", observed_source="explain", observed_reason="why")


def test_receipt_round_trips_through_its_dict_form():
    for receipt in (
        _receipt(observed=MIXED, observed_source="runtime_state"),
        _receipt(observed_reason="no query completed"),
    ):
        assert ExecutionEngineReceipt(**asdict(receipt)) == receipt


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        (["streaming", "streaming"], ("streaming", "explain", None)),
        (["streaming", "in-memory"], (MIXED, "explain", None)),
        (["streaming", "streaming", "mixed"], (MIXED, "explain", None)),
        ([], (NOT_CAPTURED, "none", "no query completed")),
        (["streaming", None], (NOT_CAPTURED, "none", "the library reported no engine for 1 of 2 queries")),
    ],
)
def test_run_observation_summarises_the_per_query_observations(observations, expected):
    summary = summarize_observed_engines(_receipt(), observations, "explain")
    assert (summary.observed, summary.observed_source, summary.observed_reason) == expected


def test_declared_gap_overrides_per_query_observations():
    summary = summarize_observed_engines(_receipt(), ["streaming"], "explain", unavailable="no API")
    assert (summary.observed, summary.observed_source, summary.observed_reason) == (NOT_CAPTURED, "none", "no API")
