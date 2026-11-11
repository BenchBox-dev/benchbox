# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.coffeeshop.schema import (
    TABLES,
    get_all_create_table_sql,
    get_create_table_sql,
    get_table_loading_order,
    get_tunings,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTablesConstant:
    def test_has_exactly_three_tables(self):
        assert set(TABLES.keys()) == {"dim_locations", "dim_products", "order_lines"}

    def test_each_table_has_required_keys(self):
        for name, table in TABLES.items():
            assert "name" in table, f"{name} missing 'name'"
            assert "columns" in table, f"{name} missing 'columns'"
            assert "row_count_formula" in table, f"{name} missing 'row_count_formula'"

    def test_dim_locations_has_six_columns(self):
        cols = TABLES["dim_locations"]["columns"]
        assert len(cols) == 6

    def test_dim_locations_column_types(self):
        cols = {c["name"]: c for c in TABLES["dim_locations"]["columns"]}
        assert cols["record_id"]["type"] == "INTEGER"
        assert cols["location_id"]["type"] == "VARCHAR(16)"

    def test_order_lines_foreign_key(self):
        cols = TABLES["order_lines"]["columns"]
        fk_col = next(c for c in cols if c["name"] == "location_record_id")
        assert fk_col["foreign_key"] == "dim_locations.record_id"


class TestGetCreateTableSql:
    def test_dim_locations_starts_correctly(self):
        sql = get_create_table_sql("dim_locations")
        assert sql.startswith("CREATE TABLE dim_locations")

    def test_unknown_table_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown table"):
            get_create_table_sql("nonexistent")

    def test_includes_primary_key_by_default(self):
        sql = get_create_table_sql("dim_locations")
        assert "PRIMARY KEY" in sql

    def test_no_primary_key_when_disabled(self):
        sql = get_create_table_sql("dim_locations", enable_primary_keys=False)
        assert "PRIMARY KEY" not in sql

    def test_spark_family_uses_string_for_order_time(self):
        sql = get_create_table_sql("order_lines", dialect="spark")
        assert "order_time STRING" in sql
        assert "order_time TIME" not in sql


class TestGetAllCreateTableSql:
    def test_contains_all_three_create_table_headers(self):
        sql = get_all_create_table_sql()
        assert sql.count("CREATE TABLE") == 3
        assert "dim_locations" in sql
        assert "dim_products" in sql
        assert "order_lines" in sql

    def test_no_primary_key_when_disabled(self):
        sql = get_all_create_table_sql(enable_primary_keys=False)
        assert "PRIMARY KEY" not in sql

    def test_returns_string(self):
        assert isinstance(get_all_create_table_sql(), str)

    def test_spark_family_order_time_type_applies_to_all_tables_sql(self):
        sql = get_all_create_table_sql(dialect="lakesail")
        assert "order_time STRING" in sql
        assert "order_time TIME" not in sql


class TestGetTunings:
    def test_returns_benchmark_tunings(self):
        from benchbox.core.tuning import BenchmarkTunings

        tunings = get_tunings()
        assert isinstance(tunings, BenchmarkTunings)

    def test_contains_order_lines_entry(self):
        tunings = get_tunings()
        assert "order_lines" in tunings.table_tunings

    def test_order_lines_partitioned_by_order_date(self):
        tunings = get_tunings()
        order_lines = tunings.table_tunings["order_lines"]
        assert order_lines.partitioning is not None
        partition_cols = [c.name for c in order_lines.partitioning]
        assert "order_date" in partition_cols


class TestGetTableLoadingOrder:
    def test_includes_every_table_exactly_once(self):
        order = get_table_loading_order()
        assert sorted(order) == sorted(TABLES.keys())
        assert len(order) == len(set(order))

    def test_order_lines_loads_after_both_dimension_tables(self):
        order = get_table_loading_order()
        position = {name: i for i, name in enumerate(order)}
        assert position["dim_locations"] < position["order_lines"]
        assert position["dim_products"] < position["order_lines"]


