from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.tuning.interface import TuningType
from benchbox.platforms.starrocks.tuning import StarRocksTuningMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _StarRocksHarness(StarRocksTuningMixin):
    def __init__(self) -> None:
        self.max_execution_time = 45
        self.disable_result_cache = True
        self.logger = Mock(spec=logging.Logger)


def test_configure_for_benchmark_applies_olap_settings_and_closes_cursor() -> None:
    adapter = _StarRocksHarness()
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor

    adapter.configure_for_benchmark(connection, "tpch")

    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert "SET query_timeout = 45" in executed
    assert "SET enable_query_cache = false" in executed
    assert "SET new_planner_optimize_timeout = 30000" in executed
    assert "SET enable_profile = false" in executed
    cursor.close.assert_called_once()


def test_configure_for_benchmark_tolerates_setting_failures() -> None:
    adapter = _StarRocksHarness()
    cursor = Mock()
    cursor.execute.side_effect = [RuntimeError("timeout"), RuntimeError("cache"), None, None]
    connection = Mock()
    connection.cursor.return_value = cursor

    adapter.configure_for_benchmark(connection, "analytics")

    assert adapter.logger.debug.call_count >= 2
    cursor.close.assert_called_once()


def test_apply_platform_optimizations_validates_setting_names_and_values() -> None:
    adapter = _StarRocksHarness()
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    platform_config = SimpleNamespace(
        additional_settings={
            "query_timeout": 60,
            "bad name": 1,
            "unsafe_value": "value with spaces",
        }
    )

    adapter.apply_platform_optimizations(platform_config, connection)

    cursor.execute.assert_called_once_with("SET query_timeout = 60")
    assert adapter.logger.warning.call_count >= 2
    cursor.close.assert_called_once()


def test_apply_constraint_configuration_logs_enabled_flags() -> None:
    adapter = _StarRocksHarness()

    adapter.apply_constraint_configuration(
        SimpleNamespace(enabled=True),
        SimpleNamespace(enabled=True),
        connection=None,
    )

    messages = [call.args[0] for call in adapter.logger.info.call_args_list]
    assert any("Primary key constraints enabled" in message for message in messages)
    assert any("Foreign key constraints noted" in message for message in messages)


def test_supports_tuning_type_reports_supported_values() -> None:
    adapter = _StarRocksHarness()

    assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
    assert adapter.supports_tuning_type(TuningType.SORTING) is True
    assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is True
    assert adapter.supports_tuning_type(object()) is False


def test_apply_table_tunings_logs_partition_sort_and_distribution() -> None:
    adapter = _StarRocksHarness()
    table_tuning = Mock()
    table_tuning.table_name = "lineitem"
    table_tuning.has_any_tuning.return_value = True

    partition_col = SimpleNamespace(name="l_shipdate", order=2)
    sort_col = SimpleNamespace(name="l_orderkey", order=1)
    distribution_col = SimpleNamespace(name="l_partkey", order=1)

    def _columns_by_type(tuning_type):
        if tuning_type == TuningType.PARTITIONING:
            return [partition_col]
        if tuning_type == TuningType.SORTING:
            return [sort_col]
        if tuning_type == TuningType.DISTRIBUTION:
            return [distribution_col]
        return []

    table_tuning.get_columns_by_type.side_effect = _columns_by_type

    adapter.apply_table_tunings(table_tuning, connection=None)

    messages = [call.args[0] for call in adapter.logger.info.call_args_list]
    assert any("Partitioning for lineitem: l_shipdate" in message for message in messages)
    assert any("Sort key for lineitem: l_orderkey" in message for message in messages)
    assert any("Distribution for lineitem: l_partkey" in message for message in messages)


def test_apply_table_tunings_wraps_unexpected_errors() -> None:
    adapter = _StarRocksHarness()
    table_tuning = Mock()
    table_tuning.table_name = "orders"
    table_tuning.has_any_tuning.return_value = True
    table_tuning.get_columns_by_type.side_effect = RuntimeError("boom")

    with pytest.raises(ValueError, match="Failed to apply tunings to StarRocks table orders: boom"):
        adapter.apply_table_tunings(table_tuning, connection=None)


def test_apply_unified_tuning_delegates_to_all_subcomponents() -> None:
    adapter = _StarRocksHarness()
    adapter.apply_constraint_configuration = Mock()
    adapter.apply_platform_optimizations = Mock()
    adapter.apply_table_tunings = Mock()
    unified_config = SimpleNamespace(
        primary_keys=SimpleNamespace(enabled=True),
        foreign_keys=SimpleNamespace(enabled=False),
        platform_optimizations=SimpleNamespace(additional_settings={"query_timeout": 60}),
        table_tunings={"orders": SimpleNamespace(table_name="orders")},
    )

    adapter.apply_unified_tuning(unified_config, connection="conn")

    adapter.apply_constraint_configuration.assert_called_once_with(
        unified_config.primary_keys,
        unified_config.foreign_keys,
        "conn",
    )
    adapter.apply_platform_optimizations.assert_called_once_with(unified_config.platform_optimizations, "conn")
    adapter.apply_table_tunings.assert_called_once_with(unified_config.table_tunings["orders"], "conn")


