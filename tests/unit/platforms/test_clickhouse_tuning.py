from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from benchbox.core.tuning.interface import PlatformOptimizationConfiguration
from benchbox.platforms.clickhouse.tuning import ClickHouseTuningMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _ClickHouseTuningHarness(ClickHouseTuningMixin):
    def __init__(self) -> None:
        self.logger = Mock(spec=logging.Logger)


def test_apply_platform_optimizations_is_noop_for_real_config() -> None:

    adapter = _ClickHouseTuningHarness()
    connection = Mock()

    adapter.apply_platform_optimizations(PlatformOptimizationConfiguration(), connection)

    connection.execute.assert_not_called()


def test_apply_platform_optimizations_noop_with_nondefault_fields_set() -> None:

    adapter = _ClickHouseTuningHarness()
    connection = Mock()
    platform_config = PlatformOptimizationConfiguration(
        z_ordering_enabled=True,
        databricks_clustering_strategy="z_order",
        bloom_filters_enabled=True,
    )

    adapter.apply_platform_optimizations(platform_config, connection)

    connection.execute.assert_not_called()


def test_apply_platform_optimizations_returns_early_for_falsy_config() -> None:
    adapter = _ClickHouseTuningHarness()
    connection = Mock()

    adapter.apply_platform_optimizations(None, connection)

    connection.execute.assert_not_called()


