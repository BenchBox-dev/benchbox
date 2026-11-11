from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _make_sort_column(name: str, order: int) -> Mock:
    column = Mock()
    column.name = name
    column.order = order
    return column


def _make_tuning_config(table_name: str, sort_columns: list[Mock]) -> Mock:
    from benchbox.core.tuning.interface import TuningType

    table_tuning = Mock()

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.SORTING:
            return sort_columns
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type

    config = Mock()
    config.table_tunings = {table_name: table_tuning}
    return config


def _set_strategy(adapter, mode: str, method: str) -> None:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.sorted_ingestion_mode = mode
    unified.platform_optimizations.sorted_ingestion_method = method
    adapter.unified_tuning_configuration = unified


def _prepare_adapter(adapter):
    adapter.logger = logging.getLogger("test-cloud-sorted-ingestion")
    adapter.captured_sql = []
    adapter.query_counter = 0
    adapter.log_verbose = Mock()
    return adapter


def test_clickhouse_cloud_force_mode_returns_actionable_error():
    adapter = _prepare_adapter(ClickHouseCloudAdapter.__new__(ClickHouseCloudAdapter))
    _set_strategy(adapter, mode="force", method="ctas")

    with pytest.raises(ValueError, match="does not support post-load sorted ingestion"):
        adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])


def test_snowflake_force_mode_dry_run_captures_sorted_ingestion_sql():
    adapter = _prepare_adapter(SnowflakeAdapter.__new__(SnowflakeAdapter))
    _set_strategy(adapter, mode="force", method="ctas")
    adapter.dry_run_mode = True
    connection = Mock()
    tuning_config = _make_tuning_config("lineitem", [_make_sort_column("l_shipdate", 1)])

    assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True
    captured_sql = [entry["sql"] for entry in adapter.captured_sql]
    assert captured_sql == ["CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate"]


