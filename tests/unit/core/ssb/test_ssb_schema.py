# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.ssb.benchmark import SSBBenchmark
from benchbox.core.ssb.schema import TABLES, get_table_loading_order

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGetTableLoadingOrder:
    def test_includes_every_table_exactly_once(self):
        order = get_table_loading_order()
        assert sorted(order) == sorted(TABLES.keys())
        assert len(order) == len(set(order))

    def test_lineorder_loads_after_every_table_it_references(self):
        order = get_table_loading_order()
        position = {name: i for i, name in enumerate(order)}
        assert position["customer"] < position["lineorder"]
        assert position["part"] < position["lineorder"]
        assert position["supplier"] < position["lineorder"]
        assert position["date"] < position["lineorder"]

    def test_lineorder_sorts_last(self):

        order = get_table_loading_order()
        assert order[-1] == "lineorder"

    def test_alphabetical_order_would_not_be_fk_safe(self):

        order = get_table_loading_order()
        assert order != sorted(order)


class TestSSBBenchmarkTableLoadingOrder:
    def test_orders_a_shuffled_available_subset_fk_safely(self, tmp_path):
        bench = SSBBenchmark(scale_factor=0.001, output_dir=tmp_path)
        available = ["supplier", "lineorder", "date", "part", "customer"]

        order = bench.get_table_loading_order(available)

        assert set(order) == set(available)
        position = {name: i for i, name in enumerate(order)}
        assert position["customer"] < position["lineorder"]
        assert position["part"] < position["lineorder"]
        assert position["supplier"] < position["lineorder"]
        assert position["date"] < position["lineorder"]

    def test_unknown_table_is_preserved_not_dropped(self, tmp_path):
        bench = SSBBenchmark(scale_factor=0.001, output_dir=tmp_path)
        order = bench.get_table_loading_order(["date", "some_extra_table"])
        assert "some_extra_table" in order


class TestLoOrderdateNonNullable:
    def test_lo_orderdate_declared_non_nullable(self):
        columns = TABLES["lineorder"]["columns"]
        spec = next(col for col in columns if col["name"] == "lo_orderdate")

        assert spec.get("nullable") is False

    def test_ssb_tuned_sort_key_passes_nullable_guard(self):
        from types import SimpleNamespace

        from benchbox.core.tuning.interface import TableTuning, TuningColumn
        from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

        benchmark = SimpleNamespace(get_schema=lambda: TABLES)
        nullable_by_table = ClickHouseWorkloadMixin._get_nullable_columns_by_table(benchmark)
        nullable_columns = nullable_by_table.get("lineorder", set())

        assert "lo_orderdate" not in nullable_columns

        statement = "CREATE TABLE lineorder (lo_orderkey INTEGER, lo_linenumber INTEGER, lo_orderdate INTEGER)"
        tunings = {
            "lineorder": TableTuning(
                table_name="lineorder",
                sorting=[
                    TuningColumn(name=name, type="INTEGER", order=order)
                    for order, name in enumerate(("lo_orderdate", "lo_orderkey", "lo_linenumber"), start=1)
                ],
            )
        }
        rendered = ClickHouseWorkloadMixin()._translate_schema_statement(
            statement, tunings, nullable_columns=nullable_columns
        )

        assert "ORDER BY (lo_orderdate, lo_orderkey, lo_linenumber)" in rendered
