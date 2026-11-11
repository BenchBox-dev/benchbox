import pytest

from benchbox.core.cost.calculator import CostCalculator, validate_resource_usage
from benchbox.core.cost.models import QueryCost
from benchbox.core.cost.pricing import (
    resolve_athena_price_per_tb,
    resolve_bigquery_price_per_tb,
    resolve_databricks_dbu_price,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCostCalculator:
    def test_snowflake_cost_calculation(self):

        calculator = CostCalculator()

        resource_usage = {"credits_used": 0.5}
        platform_config = {
            "edition": "standard",
            "cloud": "aws",
            "region": "us-east-1",
        }

        cost = calculator.calculate_query_cost("snowflake", resource_usage, platform_config)

        assert cost is not None
        assert isinstance(cost, QueryCost)
        price_per_credit = resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert cost.compute_cost == 0.5 * price_per_credit
        assert cost.currency == "USD"
        assert cost.pricing_details["credits_used"] == 0.5
        assert cost.pricing_details["price_per_credit"] == price_per_credit

    def test_bigquery_cost_calculation(self):

        calculator = CostCalculator()

        bytes_per_tib = 1024**4
        resource_usage = {"bytes_processed": bytes_per_tib}
        platform_config = {"location": "us"}

        cost = calculator.calculate_query_cost("bigquery", resource_usage, platform_config)

        assert cost is not None
        price_per_tb = resolve_bigquery_price_per_tb("us").value
        assert cost.compute_cost == price_per_tb
        assert cost.pricing_details["price_per_tb"] == price_per_tb

    def test_athena_cost_ignores_adapter_supplied_cost_usd(self):
        calculator = CostCalculator()

        resource_usage = {
            "data_scanned_bytes": 10**12,
            "cost_usd": 999.0,
        }

        cost = calculator.calculate_query_cost("athena", resource_usage, {"region": "us-east-1"})

        assert cost is not None
        assert cost.compute_cost == resolve_athena_price_per_tb("us-east-1").value
        assert cost.pricing_details["data_scanned_bytes"] == 10**12
        assert "source" not in cost.pricing_details

    def test_athena_cost_uses_decimal_terabyte(self):
        calculator = CostCalculator()

        cost = calculator.calculate_query_cost("athena", {"data_scanned_bytes": 1024**4}, {"region": "us-east-1"})

        assert cost is not None
        price_per_tb = resolve_athena_price_per_tb("us-east-1").value
        assert cost.compute_cost == (1024**4 / 10**12) * price_per_tb
        assert cost.pricing_details["unit"] == "terabyte"

    def test_athena_resource_usage_requires_data_scanned_bytes(self):
        is_valid, warnings = validate_resource_usage("athena", {"cost_usd": 5.0})

        assert is_valid is False
        assert any("data_scanned_bytes" in warning for warning in warnings)

    def test_redshift_cost_calculation(self):

        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 3600}
        platform_config = {
            "node_type": "dc2.large",
            "node_count": 2,
            "region": "us-east-1",
        }

        cost = calculator.calculate_query_cost("redshift", resource_usage, platform_config)

        assert cost is not None
        expected = 1.0 * 2 * resolve_redshift_node_price("dc2.large", "us-east-1").value
        assert cost.compute_cost == expected
        assert cost.pricing_details["node_type"] == "dc2.large"
        assert cost.pricing_details["node_count"] == 2

    def test_databricks_cost_calculation(self):

        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 1800}
        platform_config = {
            "cloud": "aws",
            "tier": "premium",
            "workload_type": "all_purpose",
            "cluster_size_dbu_per_hour": 4.0,
        }

        cost = calculator.calculate_query_cost("databricks", resource_usage, platform_config)

        assert cost is not None
        expected_cost = 0.5 * 4.0 * resolve_databricks_dbu_price("aws", "premium", "all_purpose").value
        assert abs(cost.compute_cost - expected_cost) < 0.001
        assert cost.pricing_details["is_estimated"] is True

    def test_duckdb_zero_cost(self):
        calculator = CostCalculator()

        resource_usage = {"execution_time_seconds": 10}
        platform_config = {}

        cost = calculator.calculate_query_cost("duckdb", resource_usage, platform_config)

        assert cost is not None
        assert cost.compute_cost == 0.0
        assert "Local execution" in cost.pricing_details["note"]

    def test_missing_resource_usage(self):

        calculator = CostCalculator()

        cost = calculator.calculate_query_cost("snowflake", {}, {"edition": "standard"})
        assert cost is None

        cost = calculator.calculate_query_cost("bigquery", {}, {"location": "us"})
        assert cost is None

    def test_phase_cost_calculation(self):

        calculator = CostCalculator()

        query_costs = [
            QueryCost(compute_cost=1.5, currency="USD"),
            QueryCost(compute_cost=2.3, currency="USD"),
            QueryCost(compute_cost=0.8, currency="USD"),
        ]

        phase_cost = calculator.calculate_phase_cost("power_test", query_costs)

        assert phase_cost.phase_name == "power_test"
        assert phase_cost.total_cost == 4.6
        assert phase_cost.query_count == 3
        assert phase_cost.currency == "USD"

    def test_benchmark_cost_from_phases(self):

        calculator = CostCalculator()

        phase_costs = [
            calculator.calculate_phase_cost("power_test", [QueryCost(1.0, "USD"), QueryCost(2.0, "USD")]),
            calculator.calculate_phase_cost("throughput_test", [QueryCost(3.0, "USD")]),
        ]

        benchmark_cost = calculator.calculate_benchmark_cost(phase_costs, {"platform": "snowflake"})

        assert benchmark_cost.total_cost == 6.0
        assert len(benchmark_cost.phase_costs) == 2
        assert benchmark_cost.platform_details["platform"] == "snowflake"

    def test_normalized_benchmark_cost_for_cloud_compute(self, monkeypatch):
        monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        calculator = CostCalculator()
        phase_cost = calculator.calculate_phase_cost("power_test", [QueryCost(1.0, "USD")])
        benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "snowflake"})

        normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
            "snowflake",
            benchmark_cost,
            {"edition": "standard", "cloud": "aws", "region": "us-east-1", "warehouse_size": "MEDIUM"},
        )

        assert warnings == []
        assert normalized_cost.cost_status == "normalized"
        assert normalized_cost.normalized_cost_usd is not None
        assert normalized_cost.to_dict()["normalized_cost_usd"] == "1.0"
        assert normalized_cost.to_dict()["cost_usd"] == "1.0"
        assert normalized_cost.deployment.cloud_provider == "aws"
        assert normalized_cost.deployment.cloud_region == "us-east-1"
        assert normalized_cost.deployment.warehouse_size == "MEDIUM"

    def test_normalized_benchmark_cost_for_local_platform(self):
        calculator = CostCalculator()
        phase_cost = calculator.calculate_phase_cost("power_test", [QueryCost(0.0, "USD")])
        benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "duckdb"})

        normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost("duckdb", benchmark_cost, {})

        assert warnings == []
        assert normalized_cost.cost_status == "not_applicable_local"
        assert normalized_cost.normalized_cost_usd == 0
        assert normalized_cost.cost_usd is None

    def test_dataframe_variant_local_platforms_have_not_applicable_cost(self):
        calculator = CostCalculator()
        phase_cost = calculator.calculate_phase_cost("power_test", [QueryCost(0.0, "USD")])
        for platform_id in [
            "datafusion",
            "datafusion-df",
            "polars",
            "polars-df",
            "pandas",
            "pandas-df",
            "cudf",
            "cudf-df",
            "dask",
            "dask-df",
            "pyspark",
            "pyspark-df",
            "lakesail",
            "lakesail-df",
            "duckdb",
            "sqlite",
        ]:
            assert calculator.is_local_platform(platform_id), (
                f"{platform_id!r} should be classified as a local platform"
            )
            benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": platform_id})
            normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(platform_id, benchmark_cost, {})
            assert warnings == []
            assert normalized_cost.cost_status == "not_applicable_local"

    def test_cloud_platforms_are_not_local(self):
        calculator = CostCalculator()
        for platform_id in ["snowflake", "bigquery", "redshift", "databricks", "athena"]:
            assert not calculator.is_local_platform(platform_id), (
                f"{platform_id!r} should NOT be classified as a local platform"
            )

    def test_normalized_benchmark_cost_unavailable_when_metadata_defaulted(self):
        calculator = CostCalculator()
        phase_cost = calculator.calculate_phase_cost("power_test", [QueryCost(1.0, "USD")])
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
        assert any("region metadata was defaulted" in warning for warning in warnings)


class TestBillingUnitContract:
    @staticmethod
    def _normalized_billing_unit(platform, platform_config, monkeypatch=None):
        calculator = CostCalculator()
        if monkeypatch is not None:
            monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        phase_cost = calculator.calculate_phase_cost("power_test", [QueryCost(1.0, "USD")])
        benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": platform})
        normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
            platform, benchmark_cost, platform_config
        )
        assert warnings == []
        assert normalized_cost.cost_status == "normalized"
        return normalized_cost.billing_unit

    def test_bigquery_reports_tib_scanned(self):
        assert self._normalized_billing_unit("bigquery", {"location": "us", "cloud": "gcp"}) == "tib_scanned"

    def test_athena_reports_tb_scanned(self):
        assert self._normalized_billing_unit("athena", {"region": "us-east-1", "cloud": "aws"}) == "tb_scanned"

    def test_synapse_serverless_reports_tb_scanned(self):
        assert (
            self._normalized_billing_unit("synapse", {"mode": "serverless", "region": "eastus", "cloud": "azure"})
            == "tb_scanned"
        )

    def test_synapse_dedicated_reports_dwu_hour(self):
        assert (
            self._normalized_billing_unit(
                "synapse",
                {"mode": "dedicated", "region": "eastus", "cloud": "azure", "dwu_level": "dw100c"},
            )
            == "dwu_hour"
        )

    def test_snowflake_reports_credit(self, monkeypatch):
        assert (
            self._normalized_billing_unit(
                "snowflake",
                {"edition": "standard", "cloud": "aws", "region": "us-east-1", "warehouse_size": "MEDIUM"},
                monkeypatch,
            )
            == "credit"
        )


