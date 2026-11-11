# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for
# details.

from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.cost.integration import (
    _extract_platform_config_from_results,
    add_cost_estimation_to_results,
    canonical_cost_platform_key,
)
from benchbox.core.cost.pricing import resolve_databricks_dbu_price
from benchbox.core.results.models import BenchmarkResults

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _results(**overrides: object) -> BenchmarkResults:
    base: dict[str, object] = {
        "benchmark_name": "TPC-H",
        "platform": "Databricks",
        "scale_factor": 0.1,
        "execution_id": "deadbeef",
        "timestamp": datetime(2026, 9, 17, 13, 48, 51),
        "duration_seconds": 160.76,
        "total_queries": 22,
        "successful_queries": 22,
        "failed_queries": 0,
    }
    base.update(overrides)
    return BenchmarkResults(**base)


class TestCanonicalPlatformKey:
    def test_reads_platform_type_nested_under_configuration(self) -> None:
        results = _results(
            platform_info={"platform_name": "Databricks", "configuration": {"platform_type": "databricks"}}
        )
        assert canonical_cost_platform_key(results) == "databricks"

    def test_falls_back_to_display_name_when_no_platform_type_survives(self) -> None:
        results = _results(platform="Databricks", platform_info={"name": "Databricks"})
        assert canonical_cost_platform_key(results) == "databricks"

    @pytest.mark.parametrize(
        ("display_name", "expected"),
        [
            ("ClickHouse Local", "clickhouse-local"),
            ("ClickHouse Server", "clickhouse-server"),
            ("Snowflake", "snowflake"),
            ("DuckDB (DataFrame)", "duckdb"),
            ("azure_synapse", "synapse"),
            ("fabric-dw", "fabric_dw"),
        ],
    )
    def test_normalizes_display_names_to_registry_keys(self, display_name: str, expected: str) -> None:
        assert canonical_cost_platform_key(_results(platform=display_name)) == expected


class TestLocalPlatformClassification:
    def test_clickhouse_local_is_classified_as_local_not_a_cloud_run(self) -> None:
        results = _results(platform="ClickHouse Local", platform_info={"name": "ClickHouse Local"})
        add_cost_estimation_to_results(results)

        normalized = (results.cost_summary or {})["normalized_cost"]
        assert normalized["cost_status"] == "not_applicable_local"
        assert normalized["billing_unit"] == "not_applicable"
        assert normalized["deployment"]["cloud_provider"] is None
        assert normalized["deployment"]["cloud_region"] is None


class TestDatabricksSizingFromNormalizedCompute:
    def test_observed_warehouse_size_reaches_the_cost_model(self) -> None:
        results = _results(
            platform_info={"name": "Databricks", "configuration": {"platform_type": "databricks"}},
            platform_compute={
                "warehouse_size": "2X-Small",
                "warehouse_type": "SERVERLESS",
                "source": "observed",
                "collection_status": "available",
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["warehouse_size"] == "2X-Small"
        assert config["cluster_size_dbu_per_hour"] == 1.0
        assert config["workload_type"] == "serverless_sql"
        assert "warehouse_size" not in config.get("_defaulted_fields", [])

    def test_requested_warehouse_size_is_used_but_marked_defaulted(self) -> None:
        results = _results(
            platform_info={"name": "Databricks", "configuration": {"platform_type": "databricks"}},
            platform_compute={
                "warehouse_size": "Medium",
                "source": "requested",
                "collection_status": "partial",
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["warehouse_size"] == "Medium"
        assert config["cluster_size_dbu_per_hour"] == 8.0
        assert "warehouse_size" in config["_defaulted_fields"]

    def test_published_deployment_carries_the_observed_size(self) -> None:
        results = _results(
            platform_info={"name": "Databricks", "configuration": {"platform_type": "databricks"}},
            platform_cloud={"provider": "aws", "region": "us-east-1", "source": "observed"},
            platform_compute={
                "warehouse_size": "2X-Small",
                "warehouse_type": "SERVERLESS",
                "source": "observed",
                "collection_status": "available",
            },
        )
        add_cost_estimation_to_results(results)

        deployment = (results.cost_summary or {})["normalized_cost"]["deployment"]
        assert deployment["warehouse_size"] == "2X-Small"
        assert deployment["cluster_size"] == "1.0"
        assert deployment["cloud_region"] == "us-east-1"

    def test_observed_serverless_run_with_runtime_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("benchbox.core.cost.calculator.get_pricing_age_days", lambda table=None: 1)
        results = _results(
            platform_info={"name": "Databricks", "configuration": {"platform_type": "databricks"}},
            platform_cloud={"provider": "aws", "region": "us-east-1", "source": "observed"},
            platform_compute={
                "warehouse_size": "2X-Small",
                "warehouse_type": "SERVERLESS",
                "source": "observed",
                "collection_status": "available",
            },
            query_results=[
                {"query_id": "Q1", "resource_usage": {"execution_time_seconds": 60.0}},
            ],
        )
        add_cost_estimation_to_results(results)

        normalized = (results.cost_summary or {})["normalized_cost"]
        assert normalized["cost_status"] == "normalized"
        price = resolve_databricks_dbu_price("aws", "premium", "serverless_sql").value
        assert price is not None
        assert float(normalized["normalized_cost_usd"]) == pytest.approx((60.0 / 3600.0) * 1.0 * price)


class TestSnowflakeSizingFromNormalizedCompute:
    def test_observed_warehouse_size_reaches_the_cost_model(self) -> None:
        results = _results(
            platform="Snowflake",
            platform_info={"name": "Snowflake", "configuration": {"platform_type": "snowflake"}},
            platform_compute={
                "warehouse_size": "X-Small",
                "source": "observed",
                "collection_status": "available",
            },
        )

        config = _extract_platform_config_from_results(results)

        assert config["warehouse_size"] == "X-Small"
        assert "warehouse_size" not in config.get("_defaulted_fields", [])


class TestRegionIsNeverInvented:
    def test_unobserved_region_stays_absent_from_the_published_deployment(self) -> None:
        results = _results(
            platform_info={"name": "Databricks", "configuration": {"platform_type": "databricks"}},
            platform_cloud={
                "provider": "aws",
                "region_collection_status": "unavailable",
                "source": "inferred",
            },
            platform_compute={
                "warehouse_size": "2X-Small",
                "source": "observed",
                "collection_status": "available",
            },
        )
        add_cost_estimation_to_results(results)

        normalized = (results.cost_summary or {})["normalized_cost"]
        assert normalized["deployment"]["cloud_region"] is None
        assert normalized["cost_status"] == "unavailable"
        assert normalized["deployment"]["warehouse_size"] == "2X-Small"


class TestLocationMetadataIsNotSizingMetadata:
    def test_inferred_cloud_metadata_is_not_penalized(self) -> None:
        platform_info = {"name": "Athena", "configuration": {"platform_type": "athena", "region": "us-west-2"}}
        without_block = _extract_platform_config_from_results(_results(platform="Athena", platform_info=platform_info))
        with_block = _extract_platform_config_from_results(
            _results(
                platform="Athena",
                platform_info=platform_info,
                platform_cloud={"provider": "aws", "region": "us-west-2", "source": "inferred"},
            )
        )

        assert without_block["cloud"] == with_block["cloud"] == "aws"
        assert without_block["region"] == with_block["region"] == "us-west-2"
        assert "_defaulted_fields" not in without_block
        assert "_defaulted_fields" not in with_block

    def test_a_fully_observed_run_has_nothing_defaulted(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                platform_info={
                    "name": "Databricks",
                    "configuration": {"platform_type": "databricks", "server_hostname": "dbc-1.cloud.databricks.com"},
                },
                platform_cloud={"provider": "aws", "region": "us-east-1", "source": "inferred"},
                platform_compute={
                    "warehouse_size": "Large",
                    "warehouse_type": "PRO",
                    "source": "observed",
                    "collection_status": "available",
                },
            )
        )

        assert config["warehouse_size"] == "Large"
        assert config["cluster_size_dbu_per_hour"] == 16.0
        assert "_defaulted_fields" not in config


class TestComputeBlocksMergePerField:
    def test_a_partial_normalized_block_does_not_shadow_legacy_sizing(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                platform_info={
                    "name": "Databricks",
                    "configuration": {"platform_type": "databricks"},
                    "compute_configuration": {
                        "warehouse_size": "Large",
                        "warehouse_metadata_collection_status": "available",
                    },
                },
                platform_compute={
                    "warehouse_type": "PRO",
                    "source": "observed",
                    "collection_status": "available",
                },
            )
        )

        assert config["warehouse_size"] == "Large"
        assert config["cluster_size_dbu_per_hour"] == 16.0
        assert "warehouse_size" not in config.get("_defaulted_fields", [])

    def test_a_legacy_value_keeps_its_own_provenance(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                platform_info={
                    "name": "Databricks",
                    "configuration": {"platform_type": "databricks"},
                    "compute_configuration": {
                        "warehouse_size": "Large",
                        "warehouse_metadata_collection_status": "unavailable",
                    },
                },
                platform_compute={"warehouse_type": "PRO", "source": "observed", "collection_status": "available"},
            )
        )

        assert config["warehouse_size"] == "Large"
        assert "warehouse_size" in config["_defaulted_fields"]


class TestConfiguredSizingIsRecordedAsDefaulted:
    def test_snowflake_configured_warehouse_size_is_marked(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                platform="Snowflake",
                platform_info={
                    "name": "Snowflake",
                    "configuration": {"platform_type": "snowflake", "warehouse_size": "X-Large"},
                },
            )
        )

        assert config["warehouse_size"] == "X-Large"
        assert "warehouse_size" in config["_defaulted_fields"]

    def test_redshift_node_count_from_cluster_info_is_marked(self) -> None:
        config = _extract_platform_config_from_results(
            _results(
                platform="Redshift",
                platform_info={
                    "name": "Redshift",
                    "configuration": {"platform_type": "redshift"},
                    "cluster_info": {"node_type": "ra3.4xlarge", "number_of_nodes": 4},
                },
            )
        )

        assert config["node_type"] == "ra3.4xlarge"
        assert config["node_count"] == 4
        assert "node_type" in config["_defaulted_fields"]
        assert "node_count" in config["_defaulted_fields"]


class TestPlatformResolvedFromTypeAloneIsCosted:
    def test_a_result_without_a_display_name_is_not_skipped(self) -> None:
        results = _results(
            platform="",
            platform_info={"platform_type": "clickhouse-local"},
        )
        add_cost_estimation_to_results(results)

        assert results.cost_summary is not None
        assert results.cost_summary["normalized_cost"]["cost_status"] == "not_applicable_local"


class TestConfiguredOnlySizingDoesNotPublish:
    def test_configured_only_warehouse_size_does_not_publish_a_total(self) -> None:
        results = _results(
            platform="Snowflake",
            platform_info={
                "platform_type": "snowflake",
                "edition": "standard",
                "cloud_provider": "aws",
                "region": "us-east-1",
                "configuration": {"warehouse_size": "MEDIUM"},
            },
            query_results=[{"query_id": "Q1", "resource_usage": {"credits_used": 0.5}}],
        )
        add_cost_estimation_to_results(results)

        normalized = (results.cost_summary or {})["normalized_cost"]
        assert normalized["cost_status"] == "unavailable"
        assert normalized["normalized_cost_usd"] is None
        assert normalized["deployment"]["warehouse_size"] == "MEDIUM"


