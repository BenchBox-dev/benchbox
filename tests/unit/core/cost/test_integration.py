from unittest.mock import MagicMock

import pytest

from benchbox.core.cost.calculator import CostCalculator
from benchbox.core.cost.integration import (
    _calculate_fallback_costs,
    _calculate_phase_costs,
    _extract_platform_config_from_results,
    add_cost_estimation_to_results,
)
from benchbox.core.cost.pricing import (
    resolve_databricks_dbu_price,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def create_test_results(**kwargs):
    defaults = {
        "benchmark_name": "Test",
        "platform": "snowflake",
        "scale_factor": 1,
        "duration_seconds": 10.0,
        "total_queries": 2,
        "successful_queries": 2,
    }
    defaults.update(kwargs)
    return make_benchmark_results(**defaults)


class TestAddCostEstimationToResults:
    def test_adds_cost_summary_to_results(self, monkeypatch):
        monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        results = create_test_results(
            benchmark_name="TPC-H",
            platform_compute={
                "warehouse_size": "MEDIUM",
                "source": "observed",
                "collection_status": "available",
            },
            platform_info={
                "platform_type": "snowflake",
                "edition": "standard",
                "cloud_provider": "aws",
                "region": "us-east-1",
                "configuration": {"warehouse_size": "MEDIUM"},
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "execution_time": 1.5,
                    "resource_usage": {"credits_used": 0.5},
                },
                {
                    "query_id": "Q2",
                    "execution_time": 2.0,
                    "resource_usage": {"credits_used": 0.8},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert hasattr(updated_results, "cost_summary")
        assert updated_results.cost_summary is not None
        assert "total_cost" in updated_results.cost_summary
        assert "currency" in updated_results.cost_summary
        assert "phase_costs" in updated_results.cost_summary
        assert "normalized_cost" in updated_results.cost_summary
        assert updated_results.cost_summary["currency"] == "USD"
        normalized_cost = updated_results.cost_summary["normalized_cost"]
        assert normalized_cost["cost_status"] == "normalized"

        expected_total = str((0.5 + 0.8) * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value)
        assert normalized_cost["normalized_cost_usd"] == expected_total
        assert normalized_cost["deployment"]["cloud_provider"] == "aws"
        assert normalized_cost["deployment"]["cloud_region"] == "us-east-1"
        assert normalized_cost["deployment"]["warehouse_size"] == "MEDIUM"

    def test_adds_per_query_costs(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="snowflake",
            scale_factor=1,
            platform_info={
                "platform_type": "snowflake",
                "edition": "standard",
                "cloud_provider": "aws",
                "region": "us-east-1",
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"credits_used": 0.5},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert "cost" in updated_results.query_results[0]

        expected = 0.5 * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert updated_results.query_results[0]["cost"] == expected

    def test_handles_missing_platform(self):
        results = create_test_results(benchmark_name="Test", platform=None, scale_factor=1)

        updated_results = add_cost_estimation_to_results(results)

        assert not hasattr(updated_results, "cost_summary") or updated_results.cost_summary is None

    def test_handles_missing_resource_usage(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="snowflake",
            scale_factor=1,
            platform_info={"platform_type": "snowflake"},
            query_results=[
                {"query_id": "Q1", "execution_time": 1.5},
                {"query_id": "Q2", "resource_usage": {"credits_used": 0.5}},
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert "cost" not in updated_results.query_results[0]
        assert "cost" in updated_results.query_results[1]

    def test_platform_config_override(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="snowflake",
            scale_factor=1,
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 1.0}},
            ],
        )

        platform_config = {
            "edition": "business_critical",
            "cloud": "aws",
            "region": "us-east-1",
        }

        updated_results = add_cost_estimation_to_results(results, platform_config)

        expected = 1.0 * resolve_snowflake_credit_price("business_critical", "aws", "us-east-1").value
        assert updated_results.query_results[0]["cost"] == expected

    def test_handles_exception_gracefully(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="snowflake",
            scale_factor=1,
            platform_info=None,
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 0.5}},
            ],
        )

        updated_results = add_cost_estimation_to_results(results)
        assert "query_id" in updated_results.query_results[0]


class TestExtractPlatformConfigFromResults:
    def test_extracts_snowflake_config(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="snowflake",
            scale_factor=1,
            platform_info={
                "platform_type": "snowflake",
                "edition": "enterprise",
                "cloud_provider": "azure",
                "region": "eu-west-1",
                "configuration": {"warehouse_size": "LARGE"},
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["platform_type"] == "snowflake"
        assert config["edition"] == "enterprise"
        assert config["cloud"] == "azure"
        assert config["region"] == "eu-west-1"
        assert config["warehouse_size"] == "LARGE"

    def test_extracts_bigquery_config(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="bigquery",
            scale_factor=1,
            platform_info={
                "platform_type": "bigquery",
                "configuration": {"location": "europe-west1"},
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["platform_type"] == "bigquery"
        assert config["location"] == "europe-west1"

    def test_extracts_redshift_config(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="redshift",
            scale_factor=1,
            platform_info={
                "platform_type": "redshift",
                "region": "us-west-2",
                "cluster_info": {
                    "node_type": "ra3.4xlarge",
                    "number_of_nodes": 4,
                },
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["platform_type"] == "redshift"
        assert config["node_type"] == "ra3.4xlarge"
        assert config["node_count"] == 4
        assert config["region"] == "us-west-2"

    def test_extracts_databricks_config(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "tier": "standard",
                "configuration": {
                    "server_hostname": "dbc-12345.cloud.databricks.com",
                },
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["platform_type"] == "databricks"
        assert config["cloud"] == "aws"
        assert config["tier"] == "standard"
        assert config["workload_type"] == "all_purpose"
        assert config["cluster_size_dbu_per_hour"] == 2.0

    def test_databricks_cloud_inference_azure(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "configuration": {
                    "server_hostname": "adb-1234.12.azuredatabricks.net",
                },
            },
        )

        config = _extract_platform_config_from_results(results)
        assert config["cloud"] == "azure"

    def test_databricks_cloud_inference_gcp(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "configuration": {
                    "server_hostname": "12345.gcp.databricks.com",
                },
            },
        )

        config = _extract_platform_config_from_results(results)
        assert config["cloud"] == "gcp"

    def test_extracts_databricks_warehouse_size(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "host": "abc.cloud.databricks.com",
                "compute_configuration": {
                    "warehouse_size": "Medium",
                    "warehouse_type": "PRO",
                },
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["cluster_size_dbu_per_hour"] == 8.0
        assert config["warehouse_size"] == "Medium"
        assert config["workload_type"] == "sql_compute"

    def test_databricks_workload_type_serverless(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "host": "abc.cloud.databricks.com",
                "compute_configuration": {
                    "warehouse_type": "SERVERLESS",
                },
            },
        )

        config = _extract_platform_config_from_results(results)
        assert config["workload_type"] == "serverless_sql"

    def test_databricks_various_warehouse_sizes(self):
        test_cases = [
            ("2X-Small", 1.0),
            ("X-Small", 2.0),
            ("Small", 4.0),
            ("Medium", 8.0),
            ("Large", 16.0),
            ("X-Large", 32.0),
            ("2X-Large", 64.0),
            ("3X-Large", 128.0),
            ("4X-Large", 256.0),
        ]

        for warehouse_size, expected_dbu in test_cases:
            results = create_test_results(
                benchmark_name="Test",
                platform="databricks",
                scale_factor=1,
                platform_info={
                    "platform_type": "databricks",
                    "host": "abc.cloud.databricks.com",
                    "compute_configuration": {
                        "warehouse_size": warehouse_size,
                    },
                },
            )

            config = _extract_platform_config_from_results(results)
            assert config["cluster_size_dbu_per_hour"] == expected_dbu, (
                f"Expected {expected_dbu} DBU/hour for {warehouse_size}, got {config['cluster_size_dbu_per_hour']}"
            )

    def test_handles_missing_platform_info(self):
        results = create_test_results(benchmark_name="Test", platform="snowflake", scale_factor=1, platform_info=None)

        config = _extract_platform_config_from_results(results)
        assert config == {}

    def test_omits_unobservable_fields_and_records_them_as_defaulted(self):
        results = create_test_results(
            benchmark_name="Test",
            platform="snowflake",
            scale_factor=1,
            platform_info={
                "platform_type": "snowflake",
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["edition"] == "standard"
        assert "cloud" not in config
        assert "region" not in config
        assert config["_defaulted_fields"] == ["cloud", "edition", "region"]


class TestCalculatePhaseCosts:
    def test_calculates_costs_from_execution_phases(self):

        power_test = MagicMock()
        power_test.query_executions = [
            MagicMock(resource_usage={"credits_used": 0.5}),
            MagicMock(resource_usage={"credits_used": 0.8}),
        ]

        execution_phases = MagicMock()
        execution_phases.power_test = power_test
        execution_phases.throughput_test = None
        execution_phases.maintenance_test = None

        results = create_test_results(
            benchmark_name="TPC-H", platform="snowflake", scale_factor=1, execution_phases=execution_phases
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert len(phase_costs) == 1
        assert phase_costs[0].phase_name == "power_test"

        expected = (0.5 + 0.8) * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert phase_costs[0].total_cost == pytest.approx(expected)
        assert phase_costs[0].query_count == 2

    def test_calculates_throughput_test_costs(self):
        stream1 = MagicMock()
        stream1.query_executions = [
            MagicMock(resource_usage={"credits_used": 0.3}),
            MagicMock(resource_usage={"credits_used": 0.4}),
        ]

        stream2 = MagicMock()
        stream2.query_executions = [
            MagicMock(resource_usage={"credits_used": 0.5}),
        ]

        throughput_test = MagicMock()
        throughput_test.streams = [stream1, stream2]

        execution_phases = MagicMock()
        execution_phases.power_test = None
        execution_phases.throughput_test = throughput_test
        execution_phases.maintenance_test = None

        results = create_test_results(
            benchmark_name="TPC-H", platform="snowflake", scale_factor=1, execution_phases=execution_phases
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert len(phase_costs) == 1
        assert phase_costs[0].phase_name == "throughput_test"

        expected = (0.3 + 0.4 + 0.5) * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert phase_costs[0].total_cost == pytest.approx(expected)
        assert phase_costs[0].query_count == 3

    def test_fallback_to_query_results(self):
        results = create_test_results(
            benchmark_name="Custom",
            platform="snowflake",
            scale_factor=1,
            execution_phases=None,
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 0.5}},
                {"query_id": "Q2", "resource_usage": {"credits_used": 0.3}},
            ],
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert len(phase_costs) == 1
        assert phase_costs[0].phase_name == "all_queries"

        expected = (0.5 + 0.3) * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert phase_costs[0].total_cost == pytest.approx(expected)
        assert phase_costs[0].query_count == 2

    def test_fallback_with_object_query_results(self):
        from benchbox.core.cost.models import PhaseCost

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()
        phase_costs: list[PhaseCost] = []

        _calculate_fallback_costs(
            [
                MagicMock(resource_usage={"credits_used": 0.5}),
                MagicMock(resource_usage={"credits_used": 0.3}),
            ],
            "snowflake",
            platform_config,
            calculator,
            phase_costs,
        )

        assert len(phase_costs) == 1
        assert phase_costs[0].total_cost == 1.6
        assert phase_costs[0].query_count == 2

    def test_handles_missing_resource_usage_in_phases(self):
        power_test = MagicMock()
        power_test.query_executions = [
            MagicMock(resource_usage={"credits_used": 0.5}),
            MagicMock(resource_usage=None),
            MagicMock(spec=[]),
        ]

        execution_phases = MagicMock()
        execution_phases.power_test = power_test
        execution_phases.throughput_test = None
        execution_phases.maintenance_test = None

        results = create_test_results(
            benchmark_name="TPC-H", platform="snowflake", scale_factor=1, execution_phases=execution_phases
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert len(phase_costs) == 1
        assert phase_costs[0].query_count == 1
        expected = 0.5 * resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        assert phase_costs[0].total_cost == pytest.approx(expected)

    def test_returns_empty_list_when_no_costs(self):
        results = create_test_results(
            benchmark_name="Test", platform="snowflake", scale_factor=1, execution_phases=None, query_results=[]
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert phase_costs == []

    def test_multiple_phases_calculated(self):
        power_test = MagicMock()
        power_test.query_executions = [MagicMock(resource_usage={"credits_used": 1.0})]

        stream = MagicMock()
        stream.query_executions = [MagicMock(resource_usage={"credits_used": 2.0})]
        throughput_test = MagicMock()
        throughput_test.streams = [stream]

        maintenance_test = MagicMock()
        maintenance_test.query_executions = [MagicMock(resource_usage={"credits_used": 0.5})]

        execution_phases = MagicMock()
        execution_phases.power_test = power_test
        execution_phases.throughput_test = throughput_test
        execution_phases.maintenance_test = maintenance_test

        results = create_test_results(
            benchmark_name="TPC-H", platform="snowflake", scale_factor=1, execution_phases=execution_phases
        )

        platform_config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
        calculator = CostCalculator()

        phase_costs = _calculate_phase_costs(results, "snowflake", platform_config, calculator)

        assert len(phase_costs) == 3
        phase_names = {pc.phase_name for pc in phase_costs}
        assert "power_test" in phase_names
        assert "throughput_test" in phase_names
        assert "data_maintenance" in phase_names


class TestMultiPlatformIntegration:
    def test_bigquery_integration(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="bigquery",
            scale_factor=1,
            platform_info={
                "platform_type": "bigquery",
                "configuration": {"location": "us"},
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"bytes_billed": 1024**4},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert updated_results.query_results[0]["cost"] == 6.25
        assert updated_results.cost_summary["total_cost"] == 6.25

    def test_redshift_integration(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="redshift",
            scale_factor=1,
            platform_info={
                "platform_type": "redshift",
                "region": "us-east-1",
                "cluster_info": {
                    "node_type": "dc2.large",
                    "number_of_nodes": 2,
                },
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"execution_time_seconds": 3600},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        expected = 1.0 * 2 * resolve_redshift_node_price("dc2.large", "us-east-1").value
        assert updated_results.query_results[0]["cost"] == pytest.approx(expected)
        assert updated_results.cost_summary["total_cost"] == pytest.approx(expected)

    def test_databricks_integration(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="databricks",
            scale_factor=1,
            platform_info={
                "platform_type": "databricks",
                "tier": "premium",
                "configuration": {
                    "server_hostname": "abc.cloud.databricks.com",
                },
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"execution_time_seconds": 1800},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        expected_cost = 0.5 * 2.0 * resolve_databricks_dbu_price("aws", "premium", "all_purpose").value
        assert abs(updated_results.query_results[0]["cost"] - expected_cost) < 0.001
        assert abs(updated_results.cost_summary["total_cost"] - expected_cost) < 0.001

    def test_duckdb_zero_cost_integration(self):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="duckdb",
            scale_factor=1,
            platform_info={"platform_type": "duckdb"},
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"execution_time_seconds": 10},
                },
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert updated_results.query_results[0]["cost"] == 0.0
        assert updated_results.cost_summary["total_cost"] == 0.0
        normalized_cost = updated_results.cost_summary["normalized_cost"]
        assert normalized_cost["cost_status"] == "not_applicable_local"
        assert normalized_cost["normalized_cost_usd"] == "0"
        assert normalized_cost["cost_usd"] is None

    @pytest.mark.parametrize("platform", ["snowflake", "databricks"])
    def test_gateway_routed_run_suppresses_cost(self, monkeypatch, platform):
        monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        results = create_test_results(
            benchmark_name="TPC-H",
            platform=platform,
            platform_info={
                "platform_type": platform,
                "edition": "standard",
                "region": "us-east-1",
                "gateway": {"name": "espresso", "routed": True},
            },
            query_results=[{"query_id": "Q1", "resource_usage": {"credits_used": 0.5, "dbu_used": 0.5}}],
        )

        updated = add_cost_estimation_to_results(results)

        normalized_cost = updated.cost_summary["normalized_cost"]
        assert normalized_cost["cost_status"] == "not_comparable_gateway"
        assert normalized_cost["normalized_cost_usd"] is None
        assert normalized_cost["cost_usd"] is None
        assert "estimated_cost_usd" not in normalized_cost
        assert updated.query_results[0].get("cost") is None

    def test_unrouted_gateway_block_does_not_suppress_cost(self, monkeypatch):
        monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        results = create_test_results(
            benchmark_name="TPC-H",
            platform_info={
                "platform_type": "snowflake",
                "edition": "standard",
                "region": "us-east-1",
                "gateway": {"name": "espresso", "routed": False},
            },
            query_results=[{"query_id": "Q1", "resource_usage": {"credits_used": 0.5}}],
        )

        updated = add_cost_estimation_to_results(results)

        assert updated.cost_summary["normalized_cost"]["cost_status"] != "not_comparable_gateway"

    def test_missing_cloud_metadata_marks_normalized_cost_unavailable(self, caplog):
        results = create_test_results(
            benchmark_name="TPC-H",
            platform="snowflake",
            scale_factor=1,
            platform_info={
                "platform_type": "snowflake",
                "configuration": {"warehouse_size": "MEDIUM"},
            },
            query_results=[
                {
                    "query_id": "Q1",
                    "resource_usage": {"credits_used": 0.5},
                },
            ],
        )

        with caplog.at_level("WARNING"):
            updated_results = add_cost_estimation_to_results(results)

        normalized_cost = updated_results.cost_summary["normalized_cost"]
        assert normalized_cost["cost_status"] == "unavailable"
        assert normalized_cost["normalized_cost_usd"] is None
        assert any("metadata was defaulted" in record.message for record in caplog.records)
