from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.generators.clickhouse import ClickHousePrimaryKeyPrefixError
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class RecordingClient:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, *args, **kwargs):
        self.statements.append(statement)
        return []


@pytest.fixture
def adapter(chdb_probe_satisfied):
    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        yield ClickHouseLocalAdapter()


def _config(primary_keys_enabled: bool, sorted_tables: bool = True) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.primary_keys.enabled = primary_keys_enabled
    if sorted_tables:
        config.table_tunings["ORDERS"] = TableTuning(
            table_name="ORDERS",
            sorting=[
                TuningColumn(name="O_ORDERDATE", type="DATE", order=1),
                TuningColumn(name="O_ORDERKEY", type="INTEGER", order=2),
            ],
        )
    return config


def _create_schema(adapter, tmp_path, config) -> list[str]:
    adapter.unified_tuning_configuration = config
    adapter.tuning_enabled = True
    client = RecordingClient()
    adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), client)
    return client.statements


def _orders(statements: list[str]) -> str:
    return next(s for s in statements if re.match(r"\s*CREATE TABLE orders\b", s))


class TestEffectiveConfiguration:
    def test_an_explicit_disable_is_honored_when_a_sort_key_is_tuned(self, adapter):
        adapter.unified_tuning_configuration = _config(primary_keys_enabled=False)
        adapter.tuning_enabled = True

        assert adapter.get_effective_tuning_configuration().primary_keys.enabled is False

    def test_a_sort_key_is_ignored_when_tuning_is_disabled(self, adapter):
        adapter.unified_tuning_configuration = _config(primary_keys_enabled=False)
        adapter.tuning_enabled = False

        assert adapter.get_effective_tuning_configuration().primary_keys.enabled is True

    def test_primary_keys_stay_forced_on_without_a_tuned_sort_key(self, adapter):
        adapter.unified_tuning_configuration = _config(primary_keys_enabled=False, sorted_tables=False)
        adapter.tuning_enabled = True

        assert adapter.get_effective_tuning_configuration().primary_keys.enabled is True

    def test_primary_keys_stay_on_when_the_config_asks_for_them(self, adapter):
        adapter.unified_tuning_configuration = _config(primary_keys_enabled=True)

        assert adapter.get_effective_tuning_configuration().primary_keys.enabled is True


class TestDateFirstSortKey:
    def test_disabled_primary_keys_with_a_date_first_sort_key_create_valid_tables(self, adapter, tmp_path):
        statements = _create_schema(adapter, tmp_path, _config(primary_keys_enabled=False))

        orders = _orders(statements)
        assert "ORDER BY (O_ORDERDATE, O_ORDERKEY)" in orders.replace("o_orderdate", "O_ORDERDATE").replace(
            "o_orderkey", "O_ORDERKEY"
        )
        assert "PRIMARY KEY" not in orders.upper()

    def test_enabled_primary_keys_that_are_not_a_prefix_fail_before_any_table_is_created(self, adapter, tmp_path):
        adapter.unified_tuning_configuration = _config(primary_keys_enabled=True)
        adapter.tuning_enabled = True
        client = RecordingClient()

        with pytest.raises(ClickHousePrimaryKeyPrefixError, match="disable primary_keys"):
            adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), client)

        assert [s for s in client.statements if re.match(r"\s*CREATE TABLE", s)] == []

    def test_tables_without_a_tuned_sort_key_keep_the_baseline_layout_when_primary_keys_are_disabled(
        self, adapter, tmp_path
    ):
        baseline = _create_schema(adapter, tmp_path, UnifiedTuningConfiguration())
        tuned = _create_schema(adapter, tmp_path, _config(primary_keys_enabled=False))

        for table in ("region", "nation", "supplier", "customer", "part", "partsupp"):
            baseline_statement = next(s for s in baseline if re.match(rf"\s*CREATE TABLE {table}\b", s))
            tuned_statement = next(s for s in tuned if re.match(rf"\s*CREATE TABLE {table}\b", s))
            assert tuned_statement == baseline_statement
            assert "ORDER BY tuple()" not in tuned_statement


