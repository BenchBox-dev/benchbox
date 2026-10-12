import pytest

from benchbox.core.cost import pricing
from benchbox.core.cost.calculator import CostCalculator
from benchbox.core.cost.integration import _resolve_databricks_compute
from benchbox.core.cost.pricing import (
    BIGQUERY_ON_DEMAND_PRICES,
    PriceResolution,
    resolve_athena_price_per_tb,
    resolve_bigquery_price_per_tb,
    resolve_databricks_dbu_price,
    resolve_databricks_serverless_sql_dbu_per_hour,
    resolve_databricks_warehouse_dbu_per_hour,
    resolve_fabric_cu_price,
    resolve_fabric_sku_cu_count,
    resolve_firebolt_fbu_rate,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
    resolve_synapse_dedicated_price,
    resolve_synapse_serverless_price_per_tb,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_no_float_returning_lookup_api_remains():
    for name in [
        "get_athena_price_per_tb",
        "get_snowflake_credit_price",
        "get_bigquery_price_per_tb",
        "get_redshift_node_price",
        "get_databricks_dbu_price",
        "get_synapse_serverless_price_per_tb",
        "get_synapse_dedicated_price",
        "get_fabric_cu_price",
        "get_fabric_sku_cu_count",
        "get_firebolt_fbu_rate",
        "get_firebolt_fbu_price",
    ]:
        assert not hasattr(pricing, name), f"{name} must not remain alongside resolve_*"


def test_quantity_lookups_return_price_resolution():
    assert isinstance(resolve_fabric_sku_cu_count("f64"), PriceResolution)
    assert isinstance(resolve_firebolt_fbu_rate("m"), PriceResolution)
    assert isinstance(resolve_databricks_warehouse_dbu_per_hour("Medium"), PriceResolution)
    assert isinstance(resolve_databricks_serverless_sql_dbu_per_hour("Medium"), PriceResolution)
    assert resolve_fabric_sku_cu_count("f64").value == 64
    assert resolve_databricks_warehouse_dbu_per_hour("Medium").value == 8.0
    assert resolve_databricks_serverless_sql_dbu_per_hour("Medium").value == 24.0


def test_every_lookup_strips_whitespace():
    assert resolve_fabric_sku_cu_count("F2048 ").value == 2048
    assert resolve_fabric_sku_cu_count("F2048 ").fallback_used is False
    assert resolve_firebolt_fbu_rate(" m ").fallback_used is False
    assert resolve_redshift_node_price(" dc2.large ", " us-east-1 ").fallback_used is False
    assert resolve_snowflake_credit_price(" standard ", " aws ", " us-east-1 ").fallback_used is False
    assert resolve_databricks_dbu_price(" aws ", " premium ", " all_purpose ").fallback_used is False
    assert resolve_bigquery_price_per_tb(" us ").fallback_used is False
    assert resolve_athena_price_per_tb(" us-east-1 ").fallback_used is False
    assert resolve_synapse_serverless_price_per_tb(" eastus ").fallback_used is False
    assert resolve_synapse_dedicated_price(" dw100c ", " eastus ").fallback_used is False
    assert resolve_databricks_warehouse_dbu_per_hour(" Medium ").value == 8.0


def test_designed_bucket_is_not_a_fallback():
    snowflake = resolve_snowflake_credit_price("standard", "aws", "sa-east-1")
    assert snowflake.fallback_used is False
    redshift = resolve_redshift_node_price("dc2.large", "sa-east-1")
    assert redshift.resolved_key == ("dc2.large", "other")
    assert redshift.fallback_used is False
    fabric = resolve_fabric_cu_price("sa-east-1")
    assert fabric.fallback_used is False


def test_catch_all_guess_is_a_fallback():
    africa = resolve_bigquery_price_per_tb("africa-south1")
    assert africa.value == BIGQUERY_ON_DEMAND_PRICES["africa-south1"]
    assert africa.fallback_used is False
    unknown = resolve_bigquery_price_per_tb("moon-central9")
    assert unknown.fallback_used is True


def test_valid_non_us_regions_stay_available():
    assert resolve_snowflake_credit_price("standard", "aws", "eu-west-1").fallback_used is False
    assert resolve_redshift_node_price("ra3.4xlarge", "eu-west-1").fallback_used is False
    assert resolve_bigquery_price_per_tb("europe-west1").fallback_used is False
    assert resolve_synapse_dedicated_price("dw100c", "brazilsouth").fallback_used is False
    assert resolve_fabric_cu_price("westeurope").fallback_used is False
    assert resolve_databricks_dbu_price("aws", "premium", "serverless_sql").fallback_used is False


def test_unknown_keys_are_flagged():
    snowflake = resolve_snowflake_credit_price("nonexistent", "aws", "us-east-1")
    assert snowflake.fallback_used is True and snowflake.reason
    redshift = resolve_redshift_node_price("xx.mega", "us-east-1")
    assert redshift.value == 1.00 and redshift.fallback_used is True
    databricks = resolve_databricks_dbu_price("aws", "premium", "nope")
    assert databricks.fallback_used is True and databricks.reason
    sku = resolve_fabric_sku_cu_count("f4096")
    assert sku.value == 2 and sku.fallback_used is True
    node = resolve_firebolt_fbu_rate("xxl")
    assert node.fallback_used is True and node.reason
    size = resolve_databricks_warehouse_dbu_per_hour("9X-Large")
    assert size.value == 2.0 and size.fallback_used is True
    serverless_size = resolve_databricks_serverless_sql_dbu_per_hour("9X-Large")
    assert serverless_size.value is None and serverless_size.fallback_used is True
    dwu = resolve_synapse_dedicated_price("dw999c", "eastus")
    assert dwu.fallback_used is True and dwu.reason


def test_athena_and_synapse_serverless_regional_rates():
    assert resolve_athena_price_per_tb("us-east-1").value == 5.0
    assert resolve_athena_price_per_tb("eu-west-1").fallback_used is False
    assert resolve_athena_price_per_tb("ap-northeast-1").fallback_used is False
    sao_paulo = resolve_athena_price_per_tb("sa-east-1")
    assert sao_paulo.value == 9.0 and sao_paulo.fallback_used is False
    assert sao_paulo.resolved_key == ("sa-east-1",)
    assert resolve_synapse_serverless_price_per_tb("eastus").value == 5.0
    assert resolve_synapse_serverless_price_per_tb("westeurope").fallback_used is False
    southeastasia = resolve_synapse_serverless_price_per_tb("southeastasia")
    assert southeastasia.value == 6.75 and southeastasia.fallback_used is False
    canadacentral = resolve_synapse_serverless_price_per_tb("canadacentral")
    assert canadacentral.value == 5.5 and canadacentral.fallback_used is False
    brazil = resolve_synapse_serverless_price_per_tb("brazilsouth")
    assert brazil.value == 9.0 and brazil.fallback_used is False
    omitted = resolve_athena_price_per_tb()
    assert omitted.value == 5.0 and omitted.fallback_used is True and omitted.reason
    unknown = resolve_synapse_serverless_price_per_tb("moon-central9")
    assert unknown.value == 5.0 and unknown.fallback_used is True and unknown.reason


def test_unknown_databricks_warehouse_size_is_defaulted_at_extraction():
    config: dict = {}
    defaulted: list = []
    _resolve_databricks_compute(
        config,
        {"warehouse_size": "9X-Large", "warehouse_type": "PRO", "source": "observed"},
        {},
        defaulted,
    )
    assert config["cluster_size_dbu_per_hour"] == 2.0
    assert "cluster_size_dbu_per_hour" in defaulted


@pytest.mark.parametrize(
    ("platform", "resource_usage", "platform_config"),
    [
        ("snowflake", {"credits_used": 1.0}, {"edition": "nonexistent", "cloud": "aws", "region": "us-east-1"}),
        ("bigquery", {"bytes_processed": 1024**4}, {"location": "moon-central9"}),
        ("redshift", {"execution_time_seconds": 3600.0}, {"node_type": "xx.mega", "node_count": 1}),
        (
            "databricks",
            {"execution_time_seconds": 3600.0, "dbu_consumed": 2.0},
            {"cloud": "aws", "tier": "premium", "workload_type": "nope"},
        ),
        ("fabric_dw", {"execution_time_seconds": 3600.0}, {"sku": "f4096", "region": "eastus"}),
        ("firebolt", {"execution_time_seconds": 3600.0}, {"node_type": "xxl", "node_count": 1}),
        ("athena", {"data_scanned_bytes": 1024**4}, {"region": "moon-east-1"}),
        (
            "synapse",
            {"execution_time_seconds": 3600.0},
            {"mode": "dedicated", "dwu_level": "dw999c", "region": "eastus"},
        ),
    ],
)
def test_fallback_still_returns_flagged_query_cost(platform, resource_usage, platform_config):
    calculator = CostCalculator()
    cost = calculator.calculate_query_cost(platform, resource_usage, platform_config)
    assert cost is not None
    assert cost.compute_cost != 0.0
    marker = cost.pricing_details.get("price_unavailable")
    assert isinstance(marker, dict), f"{platform} fallback must stamp pricing_details['price_unavailable']"
    assert marker["table"] and marker["reason"]


def test_known_keys_leave_no_unavailable_marker():
    calculator = CostCalculator()
    cost = calculator.calculate_query_cost(
        "snowflake",
        {"credits_used": 1.0},
        {"edition": "standard", "cloud": "aws", "region": "us-east-1"},
    )
    assert cost is not None
    assert "price_unavailable" not in cost.pricing_details
    fabric = calculator.calculate_query_cost(
        "fabric_dw",
        {"execution_time_seconds": 3600.0},
        {"sku": "F2048 ", "region": "eastus"},
    )
    assert fabric is not None
    assert "price_unavailable" not in fabric.pricing_details
