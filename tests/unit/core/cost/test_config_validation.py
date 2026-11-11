import pytest

from benchbox.core.cost.integration import (
    PLATFORM_CONFIG_REQUIREMENTS,
    _extract_platform_config_from_results,
    add_cost_estimation_to_results,
    validate_platform_config,
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


class TestValidatePlatformConfig:
    def test_snowflake_valid_config(self):
        config = {
            "edition": "standard",
            "cloud": "aws",
            "region": "us-east-1",
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_snowflake_with_optional_fields(self):

        config = {
            "edition": "enterprise",
            "cloud": "azure",
            "region": "eu-west-1",
            "warehouse_size": "LARGE",
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_snowflake_missing_required_field(self):
        config = {
            "edition": "standard",
            "cloud": "aws",
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is False
        assert len(warnings) == 1
        assert "region" in warnings[0]
        assert "required" in warnings[0].lower()

    def test_snowflake_multiple_missing_fields(self):
        config = {
            "edition": "standard",
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is False
        assert len(warnings) == 2
        assert any("cloud" in w for w in warnings)
        assert any("region" in w for w in warnings)

    def test_snowflake_null_value_treated_as_missing(self):

        config = {
            "edition": "standard",
            "cloud": "aws",
            "region": None,
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is False
        assert len(warnings) == 1
        assert "region" in warnings[0]

    def test_bigquery_valid_config(self):
        config = {"location": "us"}
        is_valid, warnings = validate_platform_config("bigquery", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_bigquery_missing_location(self):

        config = {}
        is_valid, warnings = validate_platform_config("bigquery", config)

        assert is_valid is False
        assert len(warnings) == 1
        assert "location" in warnings[0]

    def test_redshift_valid_config(self):
        config = {
            "node_type": "ra3.4xlarge",
            "node_count": 4,
            "region": "us-west-2",
        }
        is_valid, warnings = validate_platform_config("redshift", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_redshift_missing_fields(self):
        config = {
            "node_type": "dc2.large",
        }
        is_valid, warnings = validate_platform_config("redshift", config)

        assert is_valid is False
        assert len(warnings) == 2
        assert any("node_count" in w for w in warnings)
        assert any("region" in w for w in warnings)

    def test_databricks_valid_config(self):
        config = {
            "cloud": "aws",
            "tier": "premium",
            "warehouse_type": "PRO",
            "workload_type": "sql_compute",
            "cluster_size_dbu_per_hour": 8.0,
        }
        is_valid, warnings = validate_platform_config("databricks", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_databricks_with_optional_warehouse_size(self):

        config = {
            "cloud": "azure",
            "tier": "standard",
            "warehouse_type": "CLASSIC",
            "workload_type": "all_purpose",
            "cluster_size_dbu_per_hour": 4.0,
            "warehouse_size": "Medium",
        }
        is_valid, warnings = validate_platform_config("databricks", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_databricks_missing_fields(self):
        config = {
            "cloud": "gcp",
            "tier": "premium",
        }
        is_valid, warnings = validate_platform_config("databricks", config)

        assert is_valid is False
        assert len(warnings) == 3
        assert any("warehouse_type" in w for w in warnings)
        assert any("workload_type" in w for w in warnings)
        assert any("cluster_size_dbu_per_hour" in w for w in warnings)

    def test_databricks_workload_type_does_not_require_warehouse_type(self):
        config = {
            "cloud": "aws",
            "tier": "premium",
            "workload_type": "all_purpose",
            "cluster_size_dbu_per_hour": 8.0,
        }
        is_valid, warnings = validate_platform_config("databricks", config)

        assert is_valid is True
        assert warnings == []

    def test_databricks_df_mirrors_databricks_requirements(self):
        config = {
            "cloud": "aws",
            "tier": "premium",
            "warehouse_type": "PRO",
            "workload_type": "sql_compute",
            "cluster_size_dbu_per_hour": 8.0,
        }
        is_valid, warnings = validate_platform_config("databricks-df", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_athena_requires_region(self):

        is_valid, warnings = validate_platform_config("athena", {"region": "us-east-1"})

        assert is_valid is True
        assert len(warnings) == 0

        is_valid, warnings = validate_platform_config("athena", {})

        assert is_valid is False
        assert len(warnings) == 1
        assert "region" in warnings[0]

    def test_synapse_requires_mode_and_region(self):
        config = {"mode": "serverless", "region": "eastus"}
        is_valid, warnings = validate_platform_config("synapse", config)

        assert is_valid is True
        assert len(warnings) == 0

        config = {"mode": "dedicated", "region": "eastus", "dwu_level": "dw100c"}
        is_valid, warnings = validate_platform_config("synapse", config)

        assert is_valid is True
        assert len(warnings) == 0

        is_valid, warnings = validate_platform_config("synapse", {"mode": "serverless"})

        assert is_valid is False
        assert len(warnings) == 1
        assert "region" in warnings[0]

        is_valid, warnings = validate_platform_config("synapse", {"region": "eastus"})

        assert is_valid is False
        assert len(warnings) == 1
        assert "mode" in warnings[0]

    def test_fabric_dw_requires_region_and_sku(self):

        config = {"region": "eastus", "sku": "f64"}
        is_valid, warnings = validate_platform_config("fabric_dw", config)

        assert is_valid is True
        assert len(warnings) == 0

        is_valid, warnings = validate_platform_config("fabric_dw", {"region": "eastus"})

        assert is_valid is False
        assert len(warnings) == 1
        assert "sku" in warnings[0]

        is_valid, warnings = validate_platform_config("fabric_dw", {"sku": "f64"})

        assert is_valid is False
        assert len(warnings) == 1
        assert "region" in warnings[0]

    def test_firebolt_requires_node_type_and_node_count(self):

        config = {"node_type": "m", "node_count": 1}
        is_valid, warnings = validate_platform_config("firebolt", config)

        assert is_valid is True
        assert len(warnings) == 0

        is_valid, warnings = validate_platform_config("firebolt", {"node_type": "m"})

        assert is_valid is False
        assert len(warnings) == 1
        assert "node_count" in warnings[0]

        is_valid, warnings = validate_platform_config("firebolt", {"node_count": 1})

        assert is_valid is False
        assert len(warnings) == 1
        assert "node_type" in warnings[0]

    def test_unknown_platform_always_valid(self):

        config = {}
        is_valid, warnings = validate_platform_config("postgres", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_duckdb_no_validation_required(self):
        config = {}
        is_valid, warnings = validate_platform_config("duckdb", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_case_insensitive_platform_names(self):

        config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}

        is_valid1, _ = validate_platform_config("SNOWFLAKE", config)
        is_valid2, _ = validate_platform_config("Snowflake", config)
        is_valid3, _ = validate_platform_config("snowflake", config)

        assert is_valid1 is True
        assert is_valid2 is True
        assert is_valid3 is True

    def test_config_requirements_schema_completeness(self):
        from benchbox.core.cost.calculator import CostCalculator

        priced_platforms = list(CostCalculator()._platform_calculators)

        assert len(priced_platforms) == 9

        for platform in priced_platforms:
            assert platform in PLATFORM_CONFIG_REQUIREMENTS, (
                f"Platform '{platform}' missing from PLATFORM_CONFIG_REQUIREMENTS"
            )

            requirements = PLATFORM_CONFIG_REQUIREMENTS[platform]
            assert "required" in requirements, f"Platform '{platform}' missing 'required' field in config requirements"
            assert "optional" in requirements, f"Platform '{platform}' missing 'optional' field in config requirements"


class TestConfigValidationIntegration:
    def test_valid_config_no_warnings(self, caplog):

        results = create_test_results(
            platform="snowflake",
            platform_info={
                "platform_type": "snowflake",
                "edition": "standard",
                "cloud_provider": "aws",
                "region": "us-east-1",
            },
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 0.5}},
            ],
        )

        with caplog.at_level("WARNING"):
            add_cost_estimation_to_results(results)

        config_warnings = [r for r in caplog.records if "config validation" in r.message.lower()]
        assert len(config_warnings) == 0

    def test_missing_config_field_logs_warning(self, caplog):

        results = create_test_results(
            platform="snowflake",
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 0.5}},
            ],
        )

        config_override = {
            "edition": "standard",
            "cloud": "aws",
        }

        with caplog.at_level("WARNING"):
            add_cost_estimation_to_results(results, config_override)

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("region" in w.lower() for w in warnings)
        assert any("config validation" in w.lower() for w in warnings)

    def test_incomplete_config_logs_error(self, caplog):

        results = create_test_results(
            platform="bigquery",
            query_results=[
                {"query_id": "Q1", "resource_usage": {"bytes_billed": 1024**4}},
            ],
        )

        config_override = {}

        with caplog.at_level("ERROR"):
            add_cost_estimation_to_results(results, config_override)

        errors = [r.message for r in caplog.records if r.levelname == "ERROR"]
        assert any("incomplete" in e.lower() for e in errors)
        assert any("bigquery" in e.lower() for e in errors)

    def test_config_override_bypasses_extraction(self, caplog):

        results = create_test_results(
            platform="snowflake",
            platform_info=None,
            query_results=[
                {"query_id": "Q1", "resource_usage": {"credits_used": 0.5}},
            ],
        )

        config_override = {
            "edition": "enterprise",
            "cloud": "azure",
            "region": "eu-west-1",
        }

        with caplog.at_level("WARNING"):
            updated_results = add_cost_estimation_to_results(results, config_override)

        config_warnings = [r for r in caplog.records if "config validation" in r.message.lower()]
        assert len(config_warnings) == 0

        assert "cost" in updated_results.query_results[0]

    def test_invalid_config_override_produces_warning(self, caplog):

        results = create_test_results(
            platform="redshift",
            query_results=[
                {"query_id": "Q1", "resource_usage": {"execution_time_seconds": 3600}},
            ],
        )

        config_override = {
            "node_type": "dc2.large",
        }

        with caplog.at_level("WARNING"):
            add_cost_estimation_to_results(results, config_override)

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("node_count" in w.lower() for w in warnings)
        assert any("region" in w.lower() for w in warnings)

    def test_databricks_config_validation_with_warehouse_metadata(self):

        results = create_test_results(
            platform="databricks",
            platform_info={
                "platform_type": "databricks",
                "tier": "premium",
                "host": "abc.cloud.databricks.com",
                "compute_configuration": {
                    "warehouse_size": "Large",
                    "warehouse_type": "PRO",
                },
            },
            query_results=[
                {"query_id": "Q1", "resource_usage": {"execution_time_seconds": 1800}},
            ],
        )

        updated_results = add_cost_estimation_to_results(results)

        assert "cost" in updated_results.query_results[0]
        assert updated_results.cost_summary is not None


class TestDatabricksWorkloadDefaultFailsClosed:
    def _results_without_warehouse_type(self):
        return create_test_results(
            platform="databricks",
            platform_info={
                "platform_type": "databricks",
                "tier": "premium",
                "host": "abc.cloud.databricks.com",
                "region": "us-east-1",
                "compute_configuration": {
                    "warehouse_size": "Medium",
                    "warehouse_metadata_collection_status": "available",
                },
            },
            query_results=[
                {"query_id": "Q1", "resource_usage": {"execution_time_seconds": 1800}},
            ],
        )

    def test_missing_warehouse_type_records_workload_default(self):
        config = _extract_platform_config_from_results(self._results_without_warehouse_type())

        assert config["workload_type"] == "all_purpose"
        assert config["warehouse_size"] == "Medium"
        assert "workload_type" in config["_defaulted_fields"]

    def test_missing_warehouse_type_does_not_publish_normalized(self):
        updated_results = add_cost_estimation_to_results(self._results_without_warehouse_type())

        assert "cost" in updated_results.query_results[0]
        normalized_cost = updated_results.cost_summary["normalized_cost"]
        assert normalized_cost["cost_status"] == "unavailable"
        assert normalized_cost["normalized_cost_usd"] is None

    def test_observed_warehouse_type_keeps_workload_clean(self):
        results = create_test_results(
            platform="databricks",
            platform_info={
                "platform_type": "databricks",
                "tier": "premium",
                "host": "abc.cloud.databricks.com",
                "region": "us-east-1",
                "compute_configuration": {
                    "warehouse_size": "Medium",
                    "warehouse_type": "PRO",
                    "warehouse_metadata_collection_status": "available",
                },
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["warehouse_type"] == "PRO"
        assert config["workload_type"] == "sql_compute"
        assert "workload_type" not in config.get("_defaulted_fields", [])


class TestConfigValidationEdgeCases:
    def test_empty_config_dict(self):

        config = {}
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is False
        assert len(warnings) == 3

    def test_config_with_extra_fields(self):

        config = {
            "edition": "standard",
            "cloud": "aws",
            "region": "us-east-1",
            "extra_field": "should_be_ignored",
            "another_extra": 123,
        }
        is_valid, warnings = validate_platform_config("snowflake", config)

        assert is_valid is True
        assert len(warnings) == 0

    def test_all_required_platforms_have_requirements(self):

        for platform, requirements in PLATFORM_CONFIG_REQUIREMENTS.items():
            assert isinstance(requirements, dict), f"Requirements for {platform} should be a dict"
            assert "required" in requirements, f"Platform {platform} missing 'required' key"
            assert "optional" in requirements, f"Platform {platform} missing 'optional' key"
            assert isinstance(requirements["required"], list), f"Platform {platform} 'required' should be a list"
            assert isinstance(requirements["optional"], list), f"Platform {platform} 'optional' should be a list"


