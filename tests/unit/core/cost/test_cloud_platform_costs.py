import pytest

from benchbox.core.cost.calculator import RESOURCE_USAGE_SCHEMA, CostCalculator, validate_resource_usage
from benchbox.core.cost.models import QueryCost
from benchbox.core.cost.pricing import (
    resolve_databricks_dbu_price,
    resolve_fabric_cu_price,
    resolve_fabric_sku_cu_count,
    resolve_firebolt_fbu_price,
    resolve_firebolt_fbu_rate,
    resolve_synapse_dedicated_price,
    resolve_synapse_serverless_price_per_tb,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSynapseCostCalculation:
    def test_synapse_serverless_cost(self):
        calculator = CostCalculator()

        bytes_per_tb = 10**12
        resource_usage = {"bytes_processed": bytes_per_tb}
        platform_config = {"mode": "serverless", "region": "eastus"}

        cost = calculator.calculate_query_cost("synapse", resource_usage, platform_config)

        price_per_tb = resolve_synapse_serverless_price_per_tb().value
        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == price_per_tb
        assert cost.currency == "USD"
        assert cost.pricing_details["mode"] == "serverless"
        assert cost.pricing_details["price_per_tb"] == price_per_tb
        assert cost.pricing_details["tb_processed"] == 1.0

    def test_synapse_serverless_fractional_tb(self):
        calculator = CostCalculator()

        bytes_per_tb = 10**12
        resource_usage = {"bytes_processed": bytes_per_tb // 2}
        platform_config = {"mode": "serverless", "region": "westus"}

        cost = calculator.calculate_query_cost("synapse", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == 0.5 * resolve_synapse_serverless_price_per_tb().value

    def test_synapse_dedicated_cost(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 3600}
        platform_config = {
            "mode": "dedicated",
            "dwu_level": "dw1000c",
            "region": "eastus",
        }

        cost = calculator.calculate_query_cost("synapse", resource_usage, platform_config)

        price_per_hour = resolve_synapse_dedicated_price("dw1000c", "eastus").value
        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == price_per_hour
        assert cost.pricing_details["mode"] == "dedicated"
        assert cost.pricing_details["dwu_level"] == "dw1000c"
        assert cost.pricing_details["price_per_hour"] == price_per_hour

    def test_synapse_dedicated_minute_billing(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 60}
        platform_config = {
            "mode": "dedicated",
            "dwu_level": "dw100c",
            "region": "eastus",
        }

        cost = calculator.calculate_query_cost("synapse", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)

        expected = 60 / 3600 * resolve_synapse_dedicated_price("dw100c", "eastus").value
        assert abs(cost.compute_cost - expected) < 0.001

    def test_synapse_defaults_to_serverless(self):
        calculator = CostCalculator()

        bytes_per_tb = 10**12
        resource_usage = {"bytes_processed": bytes_per_tb}
        platform_config = {}

        cost = calculator.calculate_query_cost("synapse", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)
        assert cost.pricing_details["mode"] == "serverless"

    def test_synapse_missing_data_returns_none(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost("synapse", {}, {"mode": "serverless"})
        assert cost is None

        cost = calculator.calculate_query_cost("synapse", {}, {"mode": "dedicated"})
        assert cost is None


class TestFabricCostCalculation:
    def test_fabric_cost_with_cu_seconds(self):
        calculator = CostCalculator()

        resource_usage = {"cu_seconds": 3600}
        platform_config = {"region": "eastus", "sku": "f64"}

        cost = calculator.calculate_query_cost("fabric_dw", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == resolve_fabric_cu_price("eastus").value
        assert cost.pricing_details["cu_hours"] == 1.0
        assert cost.pricing_details["is_estimated"] is False

    def test_fabric_cost_estimated_from_execution_time(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 60}
        platform_config = {"region": "eastus", "sku": "f64"}

        cost = calculator.calculate_query_cost("fabric_dw", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)

        expected_cu_seconds = 60 * 64
        expected_cu_hours = expected_cu_seconds / 3600
        expected_cost = expected_cu_hours * resolve_fabric_cu_price("eastus").value
        assert abs(cost.compute_cost - expected_cost) < 0.001
        assert cost.pricing_details["is_estimated"] is True
        assert "estimated" in cost.pricing_details.get("note", "").lower()

    def test_fabric_different_sku_sizes(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 60}

        cost_f2 = calculator.calculate_query_cost("fabric_dw", resource_usage, {"sku": "f2", "region": "eastus"})
        cost_f2048 = calculator.calculate_query_cost("fabric_dw", resource_usage, {"sku": "f2048", "region": "eastus"})

        assert isinstance(cost_f2, QueryCost)
        assert isinstance(cost_f2048, QueryCost)

        assert abs(cost_f2048.compute_cost / cost_f2.compute_cost - 1024) < 0.001

    def test_fabric_regional_pricing(self):
        calculator = CostCalculator()

        resource_usage = {"cu_seconds": 3600}

        cost_us = calculator.calculate_query_cost("fabric_dw", resource_usage, {"region": "eastus"})
        cost_eu = calculator.calculate_query_cost("fabric_dw", resource_usage, {"region": "westeurope"})
        cost_ap = calculator.calculate_query_cost("fabric_dw", resource_usage, {"region": "japaneast"})

        assert isinstance(cost_us, QueryCost)
        assert isinstance(cost_eu, QueryCost)
        assert isinstance(cost_ap, QueryCost)

        assert cost_us.compute_cost < cost_eu.compute_cost
        assert cost_eu.compute_cost <= cost_ap.compute_cost

    def test_fabric_missing_data_returns_none(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost("fabric_dw", {}, {"sku": "f64"})
        assert cost is None


class TestFireboltCostCalculation:
    def test_firebolt_cost_with_fbu_consumed(self):
        calculator = CostCalculator()

        resource_usage = {"fbu_consumed": 10.0}
        platform_config = {"node_type": "m", "node_count": 1}

        cost = calculator.calculate_query_cost("firebolt", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)

        expected = 10.0 * resolve_firebolt_fbu_price().value
        assert abs(cost.compute_cost - expected) < 0.001
        assert cost.pricing_details["is_estimated"] is False

    def test_firebolt_cost_estimated_from_execution_time(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 3600}
        platform_config = {"node_type": "m", "node_count": 1}

        cost = calculator.calculate_query_cost("firebolt", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)

        expected_fbu = resolve_firebolt_fbu_rate("m").value
        expected_cost = expected_fbu * resolve_firebolt_fbu_price().value
        assert abs(cost.compute_cost - expected_cost) < 0.001
        assert cost.pricing_details["is_estimated"] is True

    def test_firebolt_multi_node_scaling(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 3600}

        cost_1_node = calculator.calculate_query_cost("firebolt", resource_usage, {"node_type": "m", "node_count": 1})
        cost_4_nodes = calculator.calculate_query_cost("firebolt", resource_usage, {"node_type": "m", "node_count": 4})

        assert isinstance(cost_1_node, QueryCost)
        assert isinstance(cost_4_nodes, QueryCost)

        assert abs(cost_4_nodes.compute_cost / cost_1_node.compute_cost - 4.0) < 0.001

    def test_firebolt_node_type_pricing(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 3600}

        cost_s = calculator.calculate_query_cost("firebolt", resource_usage, {"node_type": "s", "node_count": 1})
        cost_xl = calculator.calculate_query_cost("firebolt", resource_usage, {"node_type": "xl", "node_count": 1})

        assert isinstance(cost_s, QueryCost)
        assert isinstance(cost_xl, QueryCost)

        assert abs(cost_xl.compute_cost / cost_s.compute_cost - 8.0) < 0.001

    def test_firebolt_missing_data_returns_none(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost("firebolt", {}, {"node_type": "m"})
        assert cost is None


class TestDatabricksDFAlias:
    def test_databricks_df_uses_databricks_pricing(self):
        calculator = CostCalculator()

        resource_usage = {"dbu_consumed": 1.0}
        platform_config = {
            "cloud": "aws",
            "tier": "premium",
            "workload_type": "sql_warehouse",
        }

        cost = calculator.calculate_query_cost("databricks-df", resource_usage, platform_config)

        assert isinstance(cost, QueryCost)
        expected_price = resolve_databricks_dbu_price("aws", "premium", "sql_warehouse").value
        assert cost.compute_cost == expected_price
        assert cost.pricing_details["price_per_dbu"] == expected_price

    def test_databricks_df_schema_exists(self):
        assert "databricks-df" in RESOURCE_USAGE_SCHEMA


class TestResourceUsageValidation:
    def test_synapse_schema_exists(self):
        assert "synapse" in RESOURCE_USAGE_SCHEMA
        schema = RESOURCE_USAGE_SCHEMA["synapse"]
        assert "bytes_processed" in schema["optional"]
        assert "execution_time_seconds" in schema["optional"]

    def test_fabric_schema_exists(self):
        assert "fabric_dw" in RESOURCE_USAGE_SCHEMA
        schema = RESOURCE_USAGE_SCHEMA["fabric_dw"]
        assert "cu_seconds" in schema["optional"]
        assert "execution_time_seconds" in schema["optional"]

    def test_firebolt_schema_exists(self):
        assert "firebolt" in RESOURCE_USAGE_SCHEMA
        schema = RESOURCE_USAGE_SCHEMA["firebolt"]
        assert "fbu_consumed" in schema["optional"]
        assert "execution_time_seconds" in schema["optional"]

    def test_synapse_validation_with_valid_data(self):
        is_valid, warnings = validate_resource_usage("synapse", {"bytes_processed": 1000})
        assert is_valid
        assert len([w for w in warnings if "Unexpected" not in w]) == 0

    def test_fabric_validation_with_valid_data(self):
        is_valid, warnings = validate_resource_usage("fabric_dw", {"cu_seconds": 100})
        assert is_valid
        assert len([w for w in warnings if "Unexpected" not in w]) == 0

    def test_firebolt_validation_with_valid_data(self):
        is_valid, warnings = validate_resource_usage("firebolt", {"fbu_consumed": 5.0})
        assert is_valid
        assert len([w for w in warnings if "Unexpected" not in w]) == 0


class TestPricingHelperFunctions:
    def test_synapse_dedicated_price_by_dwu(self):
        price_100 = resolve_synapse_dedicated_price("dw100c", "eastus").value
        price_1000 = resolve_synapse_dedicated_price("dw1000c", "eastus").value
        price_30000 = resolve_synapse_dedicated_price("dw30000c", "eastus").value

        assert price_100 < price_1000 < price_30000

        assert price_1000 == pytest.approx(10 * price_100)
        assert price_30000 == pytest.approx(300 * price_100)

    def test_fabric_cu_price_by_region(self):
        price_us = resolve_fabric_cu_price("eastus").value
        price_eu = resolve_fabric_cu_price("westeurope").value
        price_ap = resolve_fabric_cu_price("japaneast").value

        assert price_us < price_eu
        assert price_eu == price_ap

    def test_fabric_sku_cu_count(self):
        assert resolve_fabric_sku_cu_count("f2").value == 2
        assert resolve_fabric_sku_cu_count("f64").value == 64
        assert resolve_fabric_sku_cu_count("f2048").value == 2048

        unknown_sku = resolve_fabric_sku_cu_count("unknown")
        assert unknown_sku.value == 2
        assert unknown_sku.fallback_used is True

    def test_firebolt_fbu_rate_by_node_type(self):
        rate_s = resolve_firebolt_fbu_rate("s").value
        assert rate_s > 0
        assert resolve_firebolt_fbu_rate("m").value == 2 * rate_s
        assert resolve_firebolt_fbu_rate("l").value == 2 * resolve_firebolt_fbu_rate("m").value
        assert resolve_firebolt_fbu_rate("xl").value == 2 * resolve_firebolt_fbu_rate("l").value

        unknown_node = resolve_firebolt_fbu_rate("unknown")
        assert unknown_node.value == resolve_firebolt_fbu_rate("m").value
        assert unknown_node.fallback_used is True


class TestEdgeCases:
    def test_zero_execution_time(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost(
            "synapse",
            {"execution_time_seconds": 0},
            {"mode": "dedicated", "dwu_level": "dw1000c", "region": "eastus"},
        )
        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == 0.0

        cost = calculator.calculate_query_cost(
            "firebolt",
            {"execution_time_seconds": 0},
            {"node_type": "m", "node_count": 1},
        )
        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == 0.0

    def test_zero_bytes_processed(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost(
            "synapse",
            {"bytes_processed": 0},
            {"mode": "serverless", "region": "eastus"},
        )
        assert isinstance(cost, QueryCost)
        assert cost.compute_cost == 0.0

    def test_very_large_values(self):
        calculator = CostCalculator()

        petabyte = 10**15

        cost = calculator.calculate_query_cost(
            "synapse",
            {"bytes_processed": petabyte},
            {"mode": "serverless", "region": "eastus"},
        )
        assert isinstance(cost, QueryCost)

        expected = 1000 * resolve_synapse_serverless_price_per_tb().value
        assert abs(cost.compute_cost - expected) < 0.01

    def test_fractional_byte_values(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost(
            "synapse",
            {"bytes_processed": 1},
            {"mode": "serverless", "region": "eastus"},
        )
        assert isinstance(cost, QueryCost)

        assert cost.compute_cost < 0.00001

    def test_very_large_execution_time(self):
        calculator = CostCalculator()

        thirty_days = 30 * 24 * 3600

        cost = calculator.calculate_query_cost(
            "synapse",
            {"execution_time_seconds": thirty_days},
            {"mode": "dedicated", "dwu_level": "dw100c", "region": "eastus"},
        )
        assert isinstance(cost, QueryCost)

        expected = 30 * 24 * resolve_synapse_dedicated_price("dw100c", "eastus").value
        assert abs(cost.compute_cost - expected) < 0.01

    def test_negative_execution_time_returns_none(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost(
            "synapse",
            {"execution_time_seconds": -100},
            {"mode": "dedicated", "dwu_level": "dw1000c"},
        )

        assert isinstance(cost, QueryCost)
        assert cost.compute_cost < 0

    def test_very_large_node_count(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost(
            "firebolt",
            {"execution_time_seconds": 3600},
            {"node_type": "m", "node_count": 1000},
        )
        assert isinstance(cost, QueryCost)

        expected = resolve_firebolt_fbu_rate("m").value * 1000 * resolve_firebolt_fbu_price().value
        assert abs(cost.compute_cost - expected) < 0.01

    def test_case_insensitive_platform_names(self):
        calculator = CostCalculator()

        resource_usage = {"bytes_processed": 1024**4}
        config = {"mode": "serverless", "region": "eastus"}

        cost_lower = calculator.calculate_query_cost("synapse", resource_usage, config)
        cost_upper = calculator.calculate_query_cost("SYNAPSE", resource_usage, config)
        cost_mixed = calculator.calculate_query_cost("SyNaPsE", resource_usage, config)

        assert isinstance(cost_lower, QueryCost)
        assert isinstance(cost_upper, QueryCost)
        assert isinstance(cost_mixed, QueryCost)
        assert cost_lower.compute_cost == cost_upper.compute_cost == cost_mixed.compute_cost


