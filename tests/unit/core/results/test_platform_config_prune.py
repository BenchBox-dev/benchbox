# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for
# details.

from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_CLICKHOUSE_COMPUTE_CONFIG = {
    "system_settings": {"max_threads": "10", "max_memory_usage": "17179869184"},
    "build_options": {"BUILD_TYPE": "RelWithDebInfo", "USE_JEMALLOC": "ON"},
}


def _results(**overrides: object) -> BenchmarkResults:
    base: dict[str, object] = {
        "benchmark_name": "TPC-H",
        "platform": "ClickHouse Local",
        "scale_factor": 1.0,
        "execution_id": "abc12345",
        "timestamp": datetime(2026, 9, 17, 12, 0, 0),
        "duration_seconds": 10.0,
        "total_queries": 22,
        "successful_queries": 22,
        "failed_queries": 0,
    }
    base.update(overrides)
    return BenchmarkResults(**base)


def _platform_config(result: BenchmarkResults) -> dict:
    return build_result_payload(result)["platform"].get("config", {})


class TestPruneRequiresANormalizedComputeBlock:
    def test_clickhouse_keeps_its_engine_settings_without_a_compute_block(self) -> None:
        result = _results(
            platform_info={
                "platform_name": "ClickHouse Local",
                "compute_configuration": _CLICKHOUSE_COMPUTE_CONFIG,
                "configuration": {"database": "default"},
            }
        )

        config = _platform_config(result)

        assert config["compute_configuration"] == _CLICKHOUSE_COMPUTE_CONFIG
        assert config["compute_configuration"]["system_settings"]["max_threads"] == "10"

    def test_an_empty_normalized_block_does_not_authorize_the_prune(self) -> None:
        result = _results(
            platform_info={"platform_name": "ClickHouse Local", "compute_configuration": _CLICKHOUSE_COMPUTE_CONFIG},
            platform_compute={"source": "unavailable", "collection_status": "unavailable"},
        )

        assert _platform_config(result)["compute_configuration"] == _CLICKHOUSE_COMPUTE_CONFIG

    def test_databricks_drops_the_duplicate_once_compute_is_normalized(self) -> None:
        result = _results(
            platform="Databricks",
            platform_info={
                "platform_name": "Databricks",
                "compute_configuration": {"warehouse_size": "2X-Small", "warehouse_type": "SERVERLESS"},
                "configuration": {"cluster_size": "Medium", "catalog": "workspace"},
            },
            platform_compute={
                "warehouse_size": "2X-Small",
                "warehouse_type": "SERVERLESS",
                "source": "observed",
                "collection_status": "available",
            },
        )

        config = _platform_config(result)

        assert "compute_configuration" not in config
        assert config["catalog"] == "workspace"


class TestLayoutLedgersAlwaysPrune:
    def test_layout_operations_leave_platform_config_but_survive_in_raw_config(self) -> None:
        operations = [{"mechanism": "optimize", "phase": "post_load", "statement": "OPTIMIZE LINEITEM"}]
        result = _results(
            platform="Databricks",
            platform_info={"platform_name": "Databricks", "configuration": {"applied_layout_operations": operations}},
        )

        payload = build_result_payload(result)

        assert "applied_layout_operations" not in payload["platform"].get("config", {})
        assert payload["platform"]["raw_config"]["applied_layout_operations"] == operations


