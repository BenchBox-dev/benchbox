from __future__ import annotations

from typing import NamedTuple

import pytest

from benchbox.core.schema_primitives import (
    BaseSchemaTable,
    get_fk_ordered_table_names_from_column_specs,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Col(NamedTuple):
    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: tuple[str, str] | None = None

    def get_sql_type(self) -> str:
        return self.sql_type


def _table(*cols: _Col, name: str = "test_table") -> BaseSchemaTable:
    return BaseSchemaTable(name=name, columns=list(cols))


class TestGetPrimaryKey:
    def test_no_pk_returns_empty(self) -> None:
        t = _table(_Col("id", "INTEGER"), _Col("name", "VARCHAR(25)"))
        assert t.get_primary_key() == []

    def test_single_pk(self) -> None:
        t = _table(_Col("id", "INTEGER", primary_key=True), _Col("name", "VARCHAR(25)"))
        assert t.get_primary_key() == ["id"]

    def test_composite_pk(self) -> None:
        t = _table(
            _Col("a", "INTEGER", primary_key=True),
            _Col("b", "INTEGER", primary_key=True),
            _Col("c", "VARCHAR(10)"),
        )
        assert t.get_primary_key() == ["a", "b"]

    def test_pk_order_matches_column_order(self) -> None:
        t = _table(
            _Col("z", "INTEGER", primary_key=True),
            _Col("a", "INTEGER", primary_key=True),
        )
        assert t.get_primary_key() == ["z", "a"]


class TestGetForeignKeys:
    def test_no_fk_returns_empty(self) -> None:
        t = _table(_Col("id", "INTEGER"), _Col("name", "VARCHAR(25)"))
        assert t.get_foreign_keys() == {}

    def test_single_fk(self) -> None:
        t = _table(
            _Col("order_key", "INTEGER", foreign_key=("orders", "o_orderkey")),
            _Col("name", "VARCHAR(25)"),
        )
        assert t.get_foreign_keys() == {"order_key": ("orders", "o_orderkey")}

    def test_multiple_fks(self) -> None:
        t = _table(
            _Col("order_key", "INTEGER", foreign_key=("orders", "o_orderkey")),
            _Col("part_key", "INTEGER", foreign_key=("part", "p_partkey")),
        )
        fks = t.get_foreign_keys()
        assert fks == {
            "order_key": ("orders", "o_orderkey"),
            "part_key": ("part", "p_partkey"),
        }

    def test_nullable_fk_included(self) -> None:
        t = _table(_Col("supplier_key", "INTEGER", nullable=True, foreign_key=("supplier", "s_suppkey")))
        assert "supplier_key" in t.get_foreign_keys()


class TestGetCreateTableSQL:
    def test_minimal_table(self) -> None:
        t = _table(_Col("id", "INTEGER"), name="orders")
        sql = t.get_create_table_sql()
        assert sql.startswith("CREATE TABLE orders (")
        assert "id INTEGER" in sql
        assert sql.strip().endswith(");")

    def test_not_null_added_for_non_nullable_column(self) -> None:
        t = _table(_Col("id", "INTEGER", nullable=False))
        sql = t.get_create_table_sql()
        assert "id INTEGER NOT NULL" in sql

    def test_nullable_column_has_no_not_null(self) -> None:
        t = _table(_Col("comment", "VARCHAR(79)", nullable=True))
        sql = t.get_create_table_sql()
        assert "NOT NULL" not in sql

    def test_primary_key_constraint_present_by_default(self) -> None:
        t = _table(_Col("id", "INTEGER", nullable=False, primary_key=True))
        sql = t.get_create_table_sql()
        assert "PRIMARY KEY (id)" in sql

    def test_composite_primary_key(self) -> None:
        t = _table(
            _Col("a", "INTEGER", primary_key=True),
            _Col("b", "INTEGER", primary_key=True),
        )
        sql = t.get_create_table_sql()
        assert "PRIMARY KEY (a, b)" in sql

    def test_enable_primary_keys_false_suppresses_pk(self) -> None:
        t = _table(_Col("id", "INTEGER", primary_key=True))
        sql = t.get_create_table_sql(enable_primary_keys=False)
        assert "PRIMARY KEY" not in sql

    def test_foreign_key_constraint_present_by_default(self) -> None:
        t = _table(_Col("order_key", "INTEGER", foreign_key=("orders", "o_orderkey")))
        sql = t.get_create_table_sql()
        assert "FOREIGN KEY (order_key) REFERENCES orders(o_orderkey)" in sql

    def test_enable_foreign_keys_false_suppresses_fk(self) -> None:
        t = _table(_Col("order_key", "INTEGER", foreign_key=("orders", "o_orderkey")))
        sql = t.get_create_table_sql(enable_foreign_keys=False)
        assert "FOREIGN KEY" not in sql

    def test_both_pk_and_fk_disabled(self) -> None:
        t = _table(
            _Col("id", "INTEGER", primary_key=True),
            _Col("ref", "INTEGER", foreign_key=("other", "id")),
        )
        sql = t.get_create_table_sql(enable_primary_keys=False, enable_foreign_keys=False)
        assert "PRIMARY KEY" not in sql
        assert "FOREIGN KEY" not in sql

    def test_table_name_appears_in_sql(self) -> None:
        t = BaseSchemaTable(name="lineitem", columns=[])
        sql = t.get_create_table_sql()
        assert "CREATE TABLE lineitem" in sql

    def test_multiple_columns_separated(self) -> None:
        t = _table(
            _Col("a", "INTEGER", nullable=False),
            _Col("b", "VARCHAR(10)", nullable=True),
        )
        sql = t.get_create_table_sql()
        assert "a INTEGER NOT NULL" in sql
        assert "b VARCHAR(10)" in sql


class TestGetFkOrderedTableNamesFromColumnSpecs:
    def test_orders_star_schema_fact_table_last(self) -> None:
        tables = {
            "date": {"columns": [{"name": "d_datekey"}]},
            "customer": {"columns": [{"name": "c_custkey"}]},
            "supplier": {"columns": [{"name": "s_suppkey"}]},
            "part": {"columns": [{"name": "p_partkey"}]},
            "lineorder": {
                "columns": [
                    {"name": "lo_custkey", "foreign_key": "customer.c_custkey"},
                    {"name": "lo_partkey", "foreign_key": "part.p_partkey"},
                    {"name": "lo_suppkey", "foreign_key": "supplier.s_suppkey"},
                    {"name": "lo_orderdate", "foreign_key": "date.d_datekey"},
                ]
            },
        }
        order = get_fk_ordered_table_names_from_column_specs(tables)
        assert set(order) == set(tables)
        assert order[-1] == "lineorder"

    def test_tables_with_no_foreign_keys_keep_input_order(self) -> None:
        tables = {
            "z_table": {"columns": [{"name": "id"}]},
            "a_table": {"columns": [{"name": "id"}]},
        }
        assert get_fk_ordered_table_names_from_column_specs(tables) == ["z_table", "a_table"]

    def test_missing_columns_key_is_tolerated(self) -> None:
        tables = {"empty_table": {}}
        assert get_fk_ordered_table_names_from_column_specs(tables) == ["empty_table"]


