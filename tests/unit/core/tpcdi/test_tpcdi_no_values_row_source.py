from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from benchbox.core.tpcdi.queries import TPCDIQueryManager

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PACKAGE_ROOT = Path(__file__).parents[4] / "benchbox"
VALUES_ROW_SOURCE = re.compile(r"FROM\s*\(?\s*VALUES\s*\(", re.IGNORECASE)


def test_etl_validation_queries_have_no_values_row_source():
    manager = TPCDIQueryManager()
    for query_id in ("EQ3", "EQ7"):
        assert not VALUES_ROW_SOURCE.search(manager.get_query(query_id))


def test_eq7_base_query_executes_and_scores_each_table():
    sql = TPCDIQueryManager().get_query("EQ7")
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE DimCustomer(CustomerID INT, TaxID VARCHAR, Status VARCHAR, Gender VARCHAR, Tier INT, "
        "CreditRating INT, NetWorth INT, IsCurrent INT)"
    )
    connection.execute("INSERT INTO DimCustomer VALUES (1, 't', 'Active', 'M', 1, 700, 2000000, 1)")
    connection.execute(
        "CREATE TABLE DimAccount(AccountID INT, SK_CustomerID INT, Status VARCHAR, TaxStatus INT, IsCurrent INT)"
    )
    connection.execute("INSERT INTO DimAccount VALUES (1, 1, 'Active', 1, 1)")
    connection.execute(
        "CREATE TABLE FactTrade(TradeID INT, SK_CustomerID INT, SK_SecurityID INT, Status VARCHAR, Type VARCHAR, "
        "TradePrice DOUBLE, Quantity INT, Fee DOUBLE, Commission DOUBLE)"
    )
    connection.execute("INSERT INTO FactTrade VALUES (1, 1, 1, 'Completed', 'Buy', 10.0, 5, 1.0, 1.0)")
    rows = connection.execute(sql).fetchall()
    assert sorted(row[1] for row in rows) == ["DimAccount", "DimCustomer", "FactTrade"]


def test_no_source_in_benchbox_uses_a_values_row_source():
    offenders = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if "_vendor" not in path.parts and VALUES_ROW_SOURCE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
