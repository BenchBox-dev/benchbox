from __future__ import annotations

import duckdb
import pytest

from benchbox.core.tpcdi.financial_data import FinancialDataPatterns
from benchbox.core.tpcdi.generator import TPCDIDataGenerator
from benchbox.core.tpcdi.query_analytics import TPCDIAnalyticalQueries
from benchbox.core.tpcdi.query_validation import TPCDIValidationQueries

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _trade_type_rows() -> list[tuple[str, str, int, int]]:
    return [
        (code, name, int(is_sell), int(is_market))
        for code, name, is_sell, is_market in FinancialDataPatterns(seed=1).trade_types
    ]


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE TradeType (TT_ID VARCHAR, TT_NAME VARCHAR, TT_IS_SELL INTEGER, TT_IS_MRKT INTEGER)"
    )
    connection.executemany("INSERT INTO TradeType VALUES (?, ?, ?, ?)", _trade_type_rows())
    connection.execute(
        "CREATE TABLE FactTrade (TradeID INTEGER, SK_CustomerID INTEGER, SK_SecurityID INTEGER, SK_BrokerID INTEGER, "
        "SK_CreateDateID INTEGER, Quantity INTEGER, TradePrice DOUBLE, Type VARCHAR, Status VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE FactHoldings (SK_CustomerID INTEGER, SK_SecurityID INTEGER, CurrentHolding INTEGER)"
    )
    connection.execute(
        "CREATE TABLE DimSecurity (SK_SecurityID INTEGER, Symbol VARCHAR, Name VARCHAR, IsCurrent INTEGER)"
    )
    connection.execute("CREATE TABLE FactMarketHistory (SK_SecurityID INTEGER, Volume INTEGER)")
    connection.execute("CREATE TABLE DimDate (SK_DateID INTEGER, CalendarYearID INTEGER)")
    connection.execute("INSERT INTO DimSecurity VALUES (1, 'ABC', 'Alpha', 1)")
    connection.execute("INSERT INTO FactMarketHistory VALUES (1, 1000)")
    connection.execute("INSERT INTO DimDate VALUES (1, 2020)")
    connection.executemany(
        "INSERT INTO FactTrade VALUES (?, 1, 1, ?, 1, ?, ?, ?, 'Completed')",
        [(1, 10, 500, 20.0, "Market Buy"), (2, 11, 200, 22.0, "Market Sell")],
    )
    return connection


def test_generated_trade_types_are_reference_table_names(tmp_path) -> None:
    written = set(TPCDIDataGenerator(scale_factor=0.01, output_dir=tmp_path)._trade_types)
    rows = _trade_type_rows()

    assert written == {name for _, name, _, _ in rows}
    assert written.isdisjoint({code for code, _, _, _ in rows})


def test_vq6_splits_buys_and_sells_for_trades_stored_by_type_name(warehouse) -> None:
    rows = warehouse.execute(TPCDIValidationQueries().get_query("VQ6")).fetchall()

    assert [row[1:8] for row in rows] == [(1, 1, 500, 200, 300, 0, 300)]


def test_aq9_returns_a_security_that_has_buys_and_sells(warehouse) -> None:
    query = TPCDIAnalyticalQueries().get_query("AQ9", {"start_year": 2020, "end_year": 2020, "min_trades": 1})

    rows = warehouse.execute(query).fetchall()

    assert [(row[1], row[4], row[5]) for row in rows] == [("ABC", 1, 1)]
