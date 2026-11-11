from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.cost.integration import _extract_platform_config_from_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _results(platform: str, platform_info: dict, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(platform=platform, platform_info=platform_info, **extra)


class TestBigQueryLiveAndLoaderShapes:
    def test_live_builder_double_nested_resolves_gcp_us(self) -> None:
        info = {
            "platform_name": "BigQuery",
            "execution_mode": "sql",
            "configuration": {
                "platform_type": "bigquery",
                "platform_name": "BigQuery",
                "cloud_provider": "GCP",
                "configuration": {"location": "US", "project_id": "p"},
            },
        }
        config = _extract_platform_config_from_results(_results("BigQuery", info))
        assert config["platform_type"] == "bigquery"
        assert config["location"] == "US"
        assert config["cloud"] == "gcp"
        assert "_defaulted_fields" not in config

    def test_loader_flattened_resolves_gcp_us(self) -> None:
        info = {"name": "BigQuery", "version": "unknown", "cloud_provider": "GCP", "location": "US"}
        config = _extract_platform_config_from_results(_results("BigQuery", info))
        assert config["platform_type"] == "bigquery"
        assert config["location"] == "US"
        assert config["cloud"] == "gcp"
        assert "_defaulted_fields" not in config

    def test_platform_name_fallback_when_type_missing(self) -> None:
        info = {"platform_name": "BigQuery", "configuration": {"location": "EU"}}
        config = _extract_platform_config_from_results(_results("BigQuery", info))
        assert config["platform_type"] == "bigquery"
        assert config["location"] == "EU"
        assert config["cloud"] == "gcp"

    def test_padded_location_is_stripped(self) -> None:
        info = {"platform_name": "BigQuery", "configuration": {"location": " US "}}
        config = _extract_platform_config_from_results(_results("BigQuery", info))
        assert config["location"] == "US"
        assert config["cloud"] == "gcp"
        assert "_defaulted_fields" not in config


class TestSnowflakeNestedShapes:
    def test_live_nested_warehouse_and_cloud(self) -> None:
        info = {
            "platform_name": "Snowflake",
            "execution_mode": "sql",
            "configuration": {
                "platform_type": "snowflake",
                "cloud_provider": "AWS",
                "cloud_region": "us-east-1",
                "configuration": {"warehouse_size": "MEDIUM"},
            },
        }
        config = _extract_platform_config_from_results(_results("Snowflake", info))
        assert config["platform_type"] == "snowflake"
        assert config["cloud"] == "aws"
        assert config["region"] == "us-east-1"
        assert config["warehouse_size"] == "MEDIUM"

        assert config["_defaulted_fields"] == ["edition", "warehouse_size"]

    def test_padded_cloud_and_region_are_stripped(self) -> None:
        info = {
            "platform_name": "Snowflake",
            "configuration": {
                "platform_type": "snowflake",
                "edition": "enterprise",
                "cloud_provider": " AWS ",
                "cloud_region": " eu-west-1 ",
                "configuration": {"warehouse_size": "LARGE"},
            },
        }
        config = _extract_platform_config_from_results(_results("Snowflake", info))
        assert config["cloud"] == "aws"
        assert config["region"] == "eu-west-1"
        assert config["_defaulted_fields"] == ["warehouse_size"]

    def test_loader_flattened_snowflake(self) -> None:
        info = {
            "name": "Snowflake",
            "edition": "enterprise",
            "cloud_provider": "aws",
            "region": "eu-west-1",
            "warehouse_size": "LARGE",
        }
        config = _extract_platform_config_from_results(_results("Snowflake", info))
        assert config["edition"] == "enterprise"
        assert config["cloud"] == "aws"
        assert config["region"] == "eu-west-1"
        assert config["warehouse_size"] == "LARGE"


class TestGenericFallbackNormalizedKeys:
    def test_fabric_dw_defaults_to_azure(self) -> None:
        config = _extract_platform_config_from_results(_results("fabric_dw", {"platform_type": "fabric_dw"}))
        assert config["platform_type"] == "fabric_dw"
        assert config["cloud"] == "azure"
        assert "region" not in config
        assert config["_defaulted_fields"] == ["region"]

    def test_hyphenated_clickhouse_cloud_normalizes(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                "clickhouse-cloud",
                {"platform_type": "clickhouse-cloud", "cloud_provider": "aws", "region": "us-east-1"},
            )
        )
        assert config["platform_type"] == "clickhouse_cloud"
        assert config["cloud"] == "aws"
        assert config["region"] == "us-east-1"


class TestRedshiftNodeCountKeys:
    def test_num_nodes_key_resolves(self) -> None:
        info = {
            "platform_name": "Redshift",
            "cluster_info": {"num_nodes": 4},
            "configuration": {
                "platform_type": "redshift",
                "node_type": "ra3.xlplus",
                "region": "us-east-1",
            },
        }
        config = _extract_platform_config_from_results(_results("Redshift", info))
        assert config["node_count"] == 4
        assert "node_count" in config.get("_defaulted_fields", [])


class TestDatabricksNestedShapes:
    def test_live_nested_hostname_and_warehouse(self) -> None:
        info = {
            "platform_name": "Databricks",
            "configuration": {
                "platform_type": "databricks",
                "configuration": {"server_hostname": "dbc-123.cloud.databricks.com"},
                "compute_configuration": {"warehouse_size": "Medium", "warehouse_type": "PRO"},
            },
        }
        config = _extract_platform_config_from_results(_results("Databricks", info))
        assert config["platform_type"] == "databricks"
        assert config["cloud"] == "aws"
        assert config["workload_type"] == "sql_compute"
        assert config["cluster_size_dbu_per_hour"] == 8.0

    def test_uppercase_hostname_resolves_cloud(self) -> None:
        info = {
            "platform_name": "Databricks",
            "configuration": {
                "platform_type": "databricks",
                "configuration": {"server_hostname": "DBC-123.AZUREDATABRICKS.NET"},
                "compute_configuration": {"warehouse_size": "Medium", "warehouse_type": "PRO"},
            },
        }
        config = _extract_platform_config_from_results(_results("Databricks", info))
        assert config["cloud"] == "azure"
        assert "_defaulted_fields" not in config or "cloud" not in config["_defaulted_fields"]

    def test_warehouse_size_lookup_is_case_insensitive(self) -> None:
        for size in ("Medium", "MEDIUM", "medium"):
            info = {
                "platform_name": "Databricks",
                "configuration": {
                    "platform_type": "databricks",
                    "configuration": {"server_hostname": "dbc-123.cloud.databricks.com"},
                    "compute_configuration": {"warehouse_size": size, "warehouse_type": "PRO"},
                },
            }
            config = _extract_platform_config_from_results(_results("Databricks", info))
            assert config["cluster_size_dbu_per_hour"] == 8.0, size
            assert "cluster_size_dbu_per_hour" not in config.get("_defaulted_fields", [])

    def test_unknown_warehouse_size_fails_closed(self) -> None:
        info = {
            "platform_name": "Databricks",
            "configuration": {
                "platform_type": "databricks",
                "configuration": {"server_hostname": "dbc-123.cloud.databricks.com"},
                "compute_configuration": {"warehouse_size": "XXL", "warehouse_type": "PRO"},
            },
        }
        config = _extract_platform_config_from_results(_results("Databricks", info))
        assert config["cluster_size_dbu_per_hour"] == 2.0
        assert "cluster_size_dbu_per_hour" in config["_defaulted_fields"]


