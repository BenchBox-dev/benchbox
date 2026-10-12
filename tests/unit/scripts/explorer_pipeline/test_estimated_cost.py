from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from _project.scripts.explorer_pipeline.transformer import (
    _estimated_normalized_cost,
    _median_measured_seconds_per_query,
    _normalized_cost,
    _parse_bundle,
)
from benchbox.core.cost.pricing import (
    resolve_snowflake_credit_price,
    resolve_snowflake_warehouse_credits_per_hour,
)
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _snowflake_bundle() -> dict:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["platform"] = {"name": "snowflake", "version": "8.0"}
    data["queries"] = [
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 200.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 300.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 110.0, "iter": 1, "stream": 1, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 210.0, "iter": 2, "stream": 1, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 310.0, "iter": 3, "stream": 1, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 400.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 999.0, "iter": 0, "stream": 0, "run_type": "warmup", "status": "SUCCESS"},
    ]
    return data


def test_runtime_collapses_to_one_median_per_query() -> None:
    runtime = _median_measured_seconds_per_query(_parse_bundle(_snowflake_bundle()))

    assert runtime is not None
    total_seconds, omitted = runtime
    assert total_seconds == Decimal("0.605")
    assert omitted == 0


def test_estimate_uses_per_query_medians_not_execution_count() -> None:
    cost = _estimated_normalized_cost(_parse_bundle(_snowflake_bundle()))

    assert cost is not None
    assert cost.cost_status == "estimated"
    size_rate = resolve_snowflake_warehouse_credits_per_hour("X-Small")
    price_rate = resolve_snowflake_credit_price("standard", "aws", "us-east-2")
    expected = (
        Decimal("0.605") * Decimal(str(size_rate.value)) * Decimal(str(price_rate.value)) / Decimal("3600")
    ).quantize(Decimal("0.000001"))
    assert cost.estimated_cost_usd == expected
    assert any("multiplicity does not scale" in assumption for assumption in cost.cost_estimate_assumptions)


def test_databricks_estimate_is_refused() -> None:
    data = _snowflake_bundle()
    data["platform"] = {"name": "databricks", "version": "1.0"}

    assert _estimated_normalized_cost(_parse_bundle(data)) is None


def test_non_verified_rate_cell_refuses_estimate() -> None:
    enterprise = _snowflake_bundle()
    enterprise["platform"] = {
        "name": "snowflake",
        "version": "8.0",
        "config": {"edition": "enterprise"},
    }
    assert _estimated_normalized_cost(_parse_bundle(enterprise)) is None

    eu_region = _snowflake_bundle()
    eu_region["platform"] = {
        "name": "snowflake",
        "version": "8.0",
        "cloud": {"provider": "aws", "region": "eu-west-1"},
    }
    assert _estimated_normalized_cost(_parse_bundle(eu_region)) is None


def test_pricing_table_drift_refuses_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchbox.core.cost.pricing as pricing_module

    monkeypatch.setitem(pricing_module.SNOWFLAKE_CREDIT_PRICES["standard"]["aws"], "us", 2.5)

    assert _estimated_normalized_cost(_parse_bundle(_snowflake_bundle())) is None


def test_failed_measurement_yields_no_estimate() -> None:
    data = _snowflake_bundle()
    data["queries"] = [
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 400.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "FAILED"},
    ]

    assert _estimated_normalized_cost(_parse_bundle(data)) is None


def test_prior_unavailable_reasons_are_preserved_alongside_estimate() -> None:
    data = _snowflake_bundle()
    data["normalized_cost"] = {
        "normalized_cost_usd": None,
        "cost_model_version": "2026.09",
        "cost_model_source": "benchbox.core.cost.pricing",
        "cost_scope": "compute_only",
        "cost_status": "unavailable",
        "billing_unit": "unknown",
        "pricing_region": "unknown",
        "unavailability_reasons": ["normalized cost unavailable: no metered credits recorded"],
    }

    cost = _normalized_cost(_parse_bundle(data))

    assert cost.cost_status == "estimated"
    assert cost.cost_estimate_assumptions[0].startswith("Recorded normalized-cost verdict was 'unavailable'")
    assert "no metered credits recorded" in cost.cost_estimate_assumptions[0]
