from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from _project.scripts.explorer_pipeline.transformer import (
    _environment_facets,
    _estimated_normalized_cost,
    _median_measured_seconds_per_query,
    _normalized_cost,
    _parse_bundle,
)
from benchbox.core.cost.pricing import (
    DATABRICKS_DBU_PRICES,
    resolve_databricks_dbu_price,
    resolve_databricks_serverless_sql_dbu_per_hour,
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
    assert runtime == Decimal("0.605")


def test_incomplete_measured_timing_refuses_estimate() -> None:
    zero_duration = _snowflake_bundle()
    zero_duration["queries"] = [
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 0.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]
    assert _median_measured_seconds_per_query(_parse_bundle(zero_duration)) is None
    assert _estimated_normalized_cost(_parse_bundle(zero_duration)) is None

    missing_duration = _snowflake_bundle()
    missing_duration["queries"] = [
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]
    assert _median_measured_seconds_per_query(_parse_bundle(missing_duration)) is None
    assert _estimated_normalized_cost(_parse_bundle(missing_duration)) is None


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


def _databricks_bundle(
    *,
    warehouse_type: str | None = "SERVERLESS",
    warehouse_size: str | None = "2X-Small",
    provider: str | None = "aws",
) -> dict:
    data = _snowflake_bundle()
    compute: dict[str, str] = {}
    if warehouse_type is not None:
        compute["warehouse_type"] = warehouse_type
    if warehouse_size is not None:
        compute["warehouse_size"] = warehouse_size
    cloud: dict[str, str] = {"region": "us-east-2"}
    if provider is not None:
        cloud["provider"] = provider
    data["platform"] = {"name": "databricks", "version": "4.5.0", "compute": compute, "cloud": cloud}
    return data


def test_databricks_estimate_is_refused() -> None:
    data = _snowflake_bundle()
    data["platform"] = {"name": "databricks", "version": "1.0"}

    assert _estimated_normalized_cost(_parse_bundle(data)) is None


def test_serverless_databricks_estimate_uses_sql_serverless_dbu_rate() -> None:
    cost = _estimated_normalized_cost(_parse_bundle(_databricks_bundle()))

    assert cost is not None
    assert cost.cost_status == "estimated"
    assert cost.billing_unit == "dbu"
    dbu_per_hour = resolve_databricks_serverless_sql_dbu_per_hour("2X-Small")
    expected = (Decimal("0.605") * Decimal(str(dbu_per_hour.value)) * Decimal("0.70") / Decimal("3600")).quantize(
        Decimal("0.000001")
    )
    assert cost.estimated_cost_usd == expected
    assert cost.normalized_cost_usd is None
    assert cost.cost_estimate_method == "sum of per-query median runtime hours × warehouse DBU per hour × USD per DBU"
    assert cost.cost_estimate_source == (
        "https://www.databricks.com/product/pricing/databricks-sql; "
        "https://docs.databricks.com/aws/en/resources/pricing (SQL Serverless SKU DBU/hour)"
    )
    assert cost.cost_estimate_pricing_date == "2026-10-13"
    assert any("$0.70/DBU for every plan tier" in assumption for assumption in cost.cost_estimate_assumptions)
    assert any("no separate cloud instance charge" in assumption for assumption in cost.cost_estimate_assumptions)


def test_databricks_estimate_assumes_aws_when_cloud_is_unrecorded() -> None:
    cost = _estimated_normalized_cost(_parse_bundle(_databricks_bundle(provider=None)))

    assert cost is not None
    assert any("AWS is used as the reference cloud" in assumption for assumption in cost.cost_estimate_assumptions)


@pytest.mark.parametrize("warehouse_type", ["PRO", "CLASSIC"])
def test_classic_or_pro_databricks_warehouse_has_no_estimate(warehouse_type: str) -> None:
    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle(warehouse_type=warehouse_type))) is None


def test_databricks_warehouse_type_unrecorded_has_no_estimate() -> None:
    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle(warehouse_type=None))) is None


def test_databricks_non_aws_cloud_has_no_estimate() -> None:
    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle(provider="azure"))) is None


def test_databricks_unknown_warehouse_size_has_no_estimate() -> None:
    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle(warehouse_size="Gigantic"))) is None
    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle(warehouse_size=None))) is None


def test_databricks_incomplete_measured_queries_have_no_estimate() -> None:
    data = _databricks_bundle()
    data["summary"]["queries"]["total"] = 22

    assert _estimated_normalized_cost(_parse_bundle(data)) is None


def test_databricks_pricing_table_drift_refuses_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DATABRICKS_DBU_PRICES["aws"]["premium"], "sql_serverless", 0.9)

    assert _estimated_normalized_cost(_parse_bundle(_databricks_bundle())) is None


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


def test_partial_query_set_refuses_estimate() -> None:
    data = _snowflake_bundle()
    data["summary"]["queries"]["total"] = 22

    assert _estimated_normalized_cost(_parse_bundle(data)) is None


def test_estimated_cost_does_not_publish_assumed_environment_facets() -> None:
    bundle = _parse_bundle(_snowflake_bundle())
    cost = _estimated_normalized_cost(bundle)

    assert cost is not None
    assert cost.cost_status == "estimated"
    facets = _environment_facets(bundle, normalized_cost=cost)

    assert facets["deployment_class"] is None
    assert facets["cloud_provider"] is None
    assert facets["cloud_region"] is None
    assert facets["instance_or_warehouse"] is None


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
