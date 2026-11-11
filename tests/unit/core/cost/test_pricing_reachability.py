import pytest

from benchbox.core.cost.calculator import CostCalculator
from benchbox.core.cost.integration import _resolve_databricks_compute
from benchbox.core.cost.pricing import (
    BIGQUERY_ON_DEMAND_PRICES,
    DATABRICKS_DBU_PRICES,
    FABRIC_SKU_CU_MAP,
    FIREBOLT_NODE_FBU_RATES,
    REDSHIFT_NODE_PRICES,
    SNOWFLAKE_CREDIT_PRICES,
    resolve_bigquery_price_per_tb,
    resolve_databricks_dbu_price,
    resolve_fabric_sku_cu_count,
    resolve_firebolt_fbu_rate,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _emitted_databricks_workload_types() -> set[str]:
    emitted = set()
    scenarios = [
        {"warehouse_type": "SERVERLESS", "warehouse_size": "2X-Small"},
        {"warehouse_type": "PRO", "warehouse_size": "Small"},
        {"warehouse_type": "CLASSIC", "warehouse_size": "Small"},
        {},
    ]
    for compute in scenarios:
        config: dict = {}
        _resolve_databricks_compute(config, compute, {}, [])
        emitted.add(config["workload_type"])
    return emitted


def _canonical_databricks_workload(workload_type: str) -> str:
    if workload_type == "serverless_sql":
        return "sql_serverless"
    if workload_type == "sql_compute":
        return "sql_pro"
    return workload_type.lower().replace("-", "_").replace(" ", "_")


def test_extraction_emits_only_known_workload_types():
    assert _emitted_databricks_workload_types() == {"serverless_sql", "sql_classic", "sql_compute", "all_purpose"}


def test_every_emitted_databricks_workload_resolves_without_fallback():
    for cloud, tiers in DATABRICKS_DBU_PRICES.items():
        for tier, workloads in tiers.items():
            for emitted in _emitted_databricks_workload_types():
                canonical = _canonical_databricks_workload(emitted)
                assert canonical in workloads, f"{emitted!r} (as {canonical!r}) missing for {cloud}/{tier}"
                resolution = resolve_databricks_dbu_price(cloud, tier, emitted)
                assert resolution.value == workloads[canonical]
                assert resolution.fallback_used is False


def test_every_snowflake_edition_resolves_without_fallback():
    for edition, clouds in SNOWFLAKE_CREDIT_PRICES.items():
        for cloud in clouds:
            expected = SNOWFLAKE_CREDIT_PRICES[edition][cloud]["us"]
            resolution = resolve_snowflake_credit_price(edition, cloud, "us-east-1")
            assert resolution.value == expected
            assert resolution.fallback_used is False


def test_every_redshift_node_type_resolves_without_fallback():
    for node_type, regions in REDSHIFT_NODE_PRICES.items():
        expected = regions["us-east-1"]
        resolution = resolve_redshift_node_price(node_type, "us-east-1")
        assert resolution.value == expected
        assert resolution.fallback_used is False


def test_bigquery_matcher_covers_current_locations():
    for location in [
        "africa-south1",
        "northamerica-south1",
        "asia-southeast3",
        "asia-southeast4",
        "europe-north2",
        "europe-west10",
        "europe-west12",
    ]:
        assert location in BIGQUERY_ON_DEMAND_PRICES, location
        resolution = resolve_bigquery_price_per_tb(location)
        assert resolution.value == BIGQUERY_ON_DEMAND_PRICES[location], location
        assert resolution.fallback_used is False, location


def test_bigquery_unlisted_locations_route_to_buckets():

    assert resolve_bigquery_price_per_tb("europe-west5").value == BIGQUERY_ON_DEMAND_PRICES["eu-single"]
    assert resolve_bigquery_price_per_tb("us-central2").value == BIGQUERY_ON_DEMAND_PRICES["us-single"]
    unknown = resolve_bigquery_price_per_tb("unknown-future-region")
    assert unknown.value == BIGQUERY_ON_DEMAND_PRICES["other"]
    assert unknown.fallback_used is True


def test_fabric_sku_map_is_fully_resolvable():
    for sku, cu_count in FABRIC_SKU_CU_MAP.items():
        resolution = resolve_fabric_sku_cu_count(sku)
        assert resolution.value == cu_count
        assert resolution.fallback_used is False


def test_firebolt_node_types_are_fully_resolvable():
    for node_type, rate in FIREBOLT_NODE_FBU_RATES.items():
        resolution = resolve_firebolt_fbu_rate(node_type)
        assert resolution.value == rate
        assert resolution.fallback_used is False


def test_unknown_platform_yields_no_value():
    calculator = CostCalculator()
    assert calculator.calculate_query_cost("definitely-not-a-platform", {"credits_used": 1.0}, {}) is None


def test_empty_run_resolves_to_unavailable_end_to_end():
    calculator = CostCalculator()
    benchmark_cost = calculator.calculate_benchmark_cost([], {"platform": "snowflake"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "snowflake",
        benchmark_cost,
        {"edition": "standard", "cloud": "aws", "region": "us-east-1", "warehouse_size": "MEDIUM"},
    )
    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert warnings


def test_defaulted_metadata_resolves_to_unavailable_end_to_end():
    calculator = CostCalculator()
    phase_cost = calculator.calculate_phase_cost(
        "power_test",
        [calculator.calculate_query_cost("snowflake", {"credits_used": 0.5}, {"edition": "standard"})],
    )
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "snowflake"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "snowflake",
        benchmark_cost,
        {
            "edition": "standard",
            "cloud": "aws",
            "region": "us-east-1",
            "warehouse_size": "MEDIUM",
            "_defaulted_fields": ["region"],
        },
    )
    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None


