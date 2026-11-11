# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.tpcdi.schema_extensions import (
    EXTENSION_TABLES,
    get_all_extended_create_table_sql,
    get_extended_create_table_sql,
    get_extended_table_order,
    get_foreign_key_constraints,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestExtensionTablesConstant:
    def test_has_nine_tables(self):
        assert len(EXTENSION_TABLES) == 9

    def test_expected_keys(self):
        expected = {
            "DimBroker",
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
            "Industry",
            "StatusType",
            "TaxRate",
            "TradeType",
        }
        assert set(EXTENSION_TABLES.keys()) == expected

    def test_dimbroker_has_sk_broker_id(self):
        col_names = [c["name"] for c in EXTENSION_TABLES["DimBroker"]["columns"]]
        assert "SK_BrokerID" in col_names

    def test_dimbroker_name_field(self):
        assert EXTENSION_TABLES["DimBroker"]["name"] == "DimBroker"

    def test_fact_tables_have_batch_id(self):
        for fact in ("FactCashBalances", "FactHoldings", "FactMarketHistory", "FactWatches"):
            col_names = [c["name"] for c in EXTENSION_TABLES[fact]["columns"]]
            assert "BatchID" in col_names, f"{fact} missing BatchID"

    def test_lookup_tables_have_no_batch_id(self):
        for lookup in ("Industry", "StatusType", "TaxRate", "TradeType"):
            col_names = [c["name"] for c in EXTENSION_TABLES[lookup]["columns"]]
            assert "BatchID" not in col_names, f"{lookup} should not have BatchID"


class TestGetExtendedTableOrder:
    def test_returns_list(self):
        assert isinstance(get_extended_table_order(), list)

    def test_industry_before_fact_tables(self):
        order = get_extended_table_order()
        assert order.index("Industry") < order.index("FactCashBalances")

    def test_all_nine_tables_present(self):
        order = get_extended_table_order()
        assert set(order) == set(EXTENSION_TABLES.keys())


class TestGetExtendedCreateTableSql:
    def test_fact_cash_balances_contains_if_not_exists(self):
        sql = get_extended_create_table_sql("FactCashBalances")
        assert "CREATE TABLE IF NOT EXISTS FactCashBalances" in sql

    def test_unknown_table_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown extended table"):
            get_extended_create_table_sql("NotATable")

    def test_returns_string(self):
        assert isinstance(get_extended_create_table_sql("Industry"), str)


class TestGetAllExtendedCreateTableSql:
    def test_contains_nine_create_table_blocks(self):
        sql = get_all_extended_create_table_sql()
        assert sql.count("CREATE TABLE IF NOT EXISTS") == 9

    def test_returns_string(self):
        assert isinstance(get_all_extended_create_table_sql(), str)


class TestGetForeignKeyConstraints:
    def test_returns_dict(self):
        fks = get_foreign_key_constraints()
        assert isinstance(fks, dict)

    def test_dimbroker_has_self_reference(self):
        fks = get_foreign_key_constraints()
        assert "DimBroker" in fks
        dimbroker_fks = fks["DimBroker"]
        self_ref = next(
            (fk for fk in dimbroker_fks if fk["references_table"] == "DimBroker"),
            None,
        )
        assert self_ref is not None, "DimBroker should have a self-referencing FK"
        assert self_ref["column"] == "ManagerID"
        assert self_ref["references_column"] == "BrokerID"


