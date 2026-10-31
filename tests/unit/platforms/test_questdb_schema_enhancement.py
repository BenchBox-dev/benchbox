# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

import benchbox.platforms.questdb as questdb_module
from benchbox.platforms.questdb import QuestDBAdapter
from benchbox.utils.dialect_utils import translate_ddl

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def questdb_stubs(monkeypatch):

    from unittest.mock import Mock

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"
    monkeypatch.setattr(questdb_module, "psycopg", mock_psycopg)
    return mock_psycopg


LINEITEM_DDL = """CREATE TABLE lineitem (
  l_orderkey BIGINT,
  l_partkey BIGINT,
  l_returnflag VARCHAR(1),
  l_linestatus VARCHAR(1),
  l_quantity DOUBLE,
  l_extendedprice DOUBLE,
  l_shipdate DATE,
  l_commitdate DATE,
  l_receiptdate DATE,
  l_shipinstruct VARCHAR(25),
  l_shipmode VARCHAR(10)
)"""

ORDERS_DDL = """CREATE TABLE orders (
  o_orderkey BIGINT,
  o_custkey BIGINT,
  o_orderstatus VARCHAR(1),
  o_orderpriority VARCHAR(15),
  o_orderdate DATE,
  o_totalprice DOUBLE
)"""

PART_DDL = """CREATE TABLE part (
  p_partkey BIGINT,
  p_brand VARCHAR(10),
  p_type VARCHAR(25),
  p_retailprice DOUBLE
)"""


def _render(adapter: QuestDBAdapter, ddl: str) -> str:
    return " ".join(
        translate_ddl(
            ddl,
            "questdb",
            strip_primary_keys=True,
            strip_foreign_keys=True,
            default_layout=adapter._questdb_table_layout(ddl),
        )
    )


class TestLineitemEnhancement:
    def test_lineitem_symbols_timestamps_and_partition(self, questdb_stubs) -> None:
        result = _render(QuestDBAdapter(), LINEITEM_DDL)
        assert "l_returnflag SYMBOL" in result
        assert "l_linestatus SYMBOL" in result
        assert "l_shipinstruct SYMBOL" in result
        assert "l_shipmode SYMBOL" in result
        assert "VARCHAR" not in result
        assert "l_shipdate TIMESTAMP" in result
        assert "l_commitdate TIMESTAMP" in result
        assert "l_receiptdate TIMESTAMP" in result
        assert "timestamp(l_shipdate)" in result
        assert "PARTITION BY MONTH" in result

    def test_lineitem_numeric_columns_untouched(self, questdb_stubs) -> None:
        result = _render(QuestDBAdapter(), LINEITEM_DDL)
        assert "l_orderkey BIGINT" in result
        assert "l_quantity DOUBLE" in result


class TestOrdersEnhancement:
    def test_orders_symbols_timestamps_and_partition(self, questdb_stubs) -> None:
        result = _render(QuestDBAdapter(), ORDERS_DDL)
        assert "o_orderstatus SYMBOL" in result
        assert "o_orderpriority SYMBOL" in result
        assert "o_orderdate TIMESTAMP" in result
        assert "timestamp(o_orderdate)" in result
        assert "PARTITION BY MONTH" in result


class TestDimensionTables:
    def test_part_symbols_without_timestamp_or_partition(self, questdb_stubs) -> None:
        result = _render(QuestDBAdapter(), PART_DDL)
        assert "p_brand SYMBOL" in result
        assert "p_type SYMBOL" in result
        assert "timestamp(" not in result
        assert "PARTITION BY" not in result

    def test_adapter_partition_override_wins(self, questdb_stubs) -> None:
        adapter = QuestDBAdapter(partition_by="YEAR")
        result = _render(adapter, ORDERS_DDL)
        assert "PARTITION BY YEAR" in result
        assert "PARTITION BY MONTH" not in result


class TestTpcdsObtSymbolColumns:
    def test_every_text_column_of_the_obt_is_a_symbol(self, questdb_stubs) -> None:
        from benchbox.core.tpcds.schema.models import DataType
        from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_obt_columns

        text_columns = [c.name for c in get_obt_columns() if c.data_type in (DataType.CHAR, DataType.VARCHAR)]
        ddl = (
            f"CREATE TABLE {OBT_TABLE_NAME} (channel VARCHAR(10) NOT NULL, ss_ticket_number INT, "
            "sold_date_d_date_id CHAR(16), item_i_brand_id INT)"
        )
        result = _render(QuestDBAdapter(), ddl)
        assert "channel SYMBOL" in result
        assert "sold_date_d_date_id SYMBOL" in result
        assert "item_i_brand_id INT" in result
        assert "ss_ticket_number INT" in result
        assert "VARCHAR" not in result
        assert len(text_columns) == 345
        assert QuestDBAdapter._symbol_columns_for_table(OBT_TABLE_NAME) == text_columns

    def test_other_tables_keep_their_static_symbol_columns(self, questdb_stubs) -> None:
        assert QuestDBAdapter._symbol_columns_for_table("nation") == ["n_name"]
        assert QuestDBAdapter._symbol_columns_for_table("unknown_table") == []
