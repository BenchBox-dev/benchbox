from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcdi.audit import AuditOutcome, run_audit
from benchbox.core.tpcdi.phases import run_historical_load, run_incremental_update, run_initialization
from tests.integration._tpcdi_digen_outputs import OUTPUTS_ENV, digen_outputs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.tpcdi,
]

duckdb = pytest.importorskip("duckdb")

OUTPUTS = digen_outputs()

FACT_TABLES = ("DimTrade", "FactWatches", "FactMarketHistory", "FactHoldings", "FactCashBalances")
DIMENSION_TABLES = ("DimCustomer", "DimAccount", "Prospect")
BATCHES = (1, 2, 3)
INCREMENTAL_BATCHES = (2, 3)
CREATED_AT_BATCH_ONE = {3: 372101}
HOLDING_OF_THAT_TRADE = {3: (353232, 372101)}
SURROGATE_KEYS = (
    "SK_BrokerID",
    "SK_CreateDateID",
    "SK_CreateTimeID",
    "SK_SecurityID",
    "SK_CompanyID",
    "SK_CustomerID",
    "SK_AccountID",
)

WHOLE_RUN_TESTS = {
    "DimTrade canceled trades",
    "DimTrade commission alerts",
    "DimTrade charge alerts",
    "DimTrade batches",
    "DimTrade distinct keys",
    "DimTrade SK_BrokerID",
    "DimTrade SK_CompanyID",
    "DimTrade SK_SecurityID",
    "DimTrade SK_CustomerID",
    "DimTrade SK_AccountID",
    "DimTrade Status",
    "DimTrade Type",
    "FactWatches batches",
    "FactWatches SK_CustomerID",
    "FactWatches SK_SecurityID",
    "FactMarketHistory batches",
    "FactMarketHistory SK_CompanyID",
    "FactMarketHistory SK_SecurityID",
    "FactMarketHistory relative dates",
    "FactHoldings batches",
    "FactHoldings SK_CustomerID",
    "FactHoldings SK_AccountID",
    "FactHoldings SK_CompanyID",
    "FactHoldings SK_SecurityID",
    "FactHoldings CurrentTradeID",
    "FactCashBalances batches",
    "FactCashBalances SK_CustomerID",
    "FactCashBalances SK_AccountID",
}

PER_BATCH_TESTS = {
    "DimTrade row count",
    "DimTrade date check",
    "FactWatches row count",
    "FactWatches active watches",
    "FactWatches date check",
    "FactMarketHistory row count",
    "FactMarketHistory SK_DateID",
    "FactHoldings row count",
    "FactHoldings SK_DateID",
    "FactCashBalances SK_DateID",
    "Batch row count: DimTrade",
    "Batch row count: FactWatches",
    "Batch row count: FactMarketHistory",
    "Batch row count: FactHoldings",
    "Batch row count: FactCashBalances",
}


@dataclass
class FactsWarehouse:
    connection: Any
    staging: Path
    audit: AuditOutcome
    scale: int
    trades_after_historical: list[tuple]
    trades_columns: list[str]


def column_names(connection: Any, table: str) -> list[str]:
    return [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]


@pytest.fixture(scope="module", params=sorted(OUTPUTS) or [None], ids=lambda scale: f"sf{scale}")
def warehouse(request):
    if request.param is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    staging = OUTPUTS[request.param]
    connection = duckdb.connect()
    run_initialization(connection)
    run_historical_load(connection, staging, allow_third_party_extensions=True)
    trades = connection.execute("SELECT * FROM DimTrade ORDER BY TradeID").fetchall()
    run_incremental_update(connection, staging, 2)
    run_incremental_update(connection, staging, 3)
    audit = run_audit(connection, staging, latest_batch=3)
    yield FactsWarehouse(connection, staging, audit, request.param, trades, column_names(connection, "DimTrade"))
    connection.close()


def audited(audit: AuditOutcome) -> list:
    tables = (*FACT_TABLES, *DIMENSION_TABLES)
    return [
        result
        for result in audit.results
        if result.test.split()[0] in tables
        or result.test.removeprefix("Batch row count: ").removeprefix("Batch joined row count: ") in tables
        or result.test == "DImessages validation reports"
    ]


def test_every_audited_row_of_the_facts_and_dimensions_reads_ok_for_all_three_batches(warehouse):
    rows = audited(warehouse.audit)
    assert {(row.test, row.batch): row.result for row in rows if row.result != "OK"} == {}
    names = {row.test for row in rows}
    assert names >= WHOLE_RUN_TESTS
    assert names >= PER_BATCH_TESTS


def test_per_batch_rows_exist_for_batches_two_and_three(warehouse):
    seen = {(row.test, row.batch) for row in audited(warehouse.audit)}
    for test in PER_BATCH_TESTS:
        assert {batch for name, batch in seen if name == test} >= set(INCREMENTAL_BATCHES), test


def test_the_date_check_reads_ok_for_every_batch(warehouse):
    checks = {
        (row.test, row.batch): row.result
        for row in warehouse.audit.results
        if row.test in ("DimTrade date check", "FactWatches date check")
    }
    assert {batch for test, batch in checks if test == "DimTrade date check"} >= set(BATCHES)
    assert set(checks.values()) == {"OK"}


def test_a_trade_keeps_every_surrogate_key_and_moves_its_batch_with_its_last_update(warehouse):
    columns = warehouse.trades_columns
    final = {row[0]: row for row in warehouse.connection.execute("SELECT * FROM DimTrade").fetchall()}
    assert len(final) > len(warehouse.trades_after_historical)
    moved = 0
    for before in warehouse.trades_after_historical:
        after = final[before[0]]
        for name in SURROGATE_KEYS:
            assert before[columns.index(name)] == after[columns.index(name)], (name, before[0])
        if before[columns.index("SK_CloseDateID")] is not None:
            assert before[columns.index("SK_CloseDateID")] == after[columns.index("SK_CloseDateID")], before[0]
        moved += after[columns.index("BatchID")] != before[columns.index("BatchID")]
    assert moved > 0
    assert warehouse.connection.execute("SELECT count(*) FROM DimTrade WHERE BatchID NOT IN (1, 2, 3)").fetchone() == (
        0,
    )


def test_a_trade_created_in_batch_one_and_completed_in_batch_two(warehouse):
    trade = CREATED_AT_BATCH_ONE.get(warehouse.scale)
    if trade is None:
        pytest.skip("no reference trade is recorded for this scale factor")
    columns = warehouse.trades_columns
    before = next(row for row in warehouse.trades_after_historical if row[0] == trade)
    rows = warehouse.connection.execute("SELECT * FROM DimTrade WHERE TradeID = ?", [trade]).fetchall()
    assert len(rows) == 1
    after = rows[0]
    for name in SURROGATE_KEYS:
        assert before[columns.index(name)] == after[columns.index(name)], name
    assert before[columns.index("BatchID")] == 1
    assert after[columns.index("BatchID")] == 2
    assert after[columns.index("Status")] == "Completed"
    assert warehouse.connection.execute(
        "SELECT d.DateValue FROM DimTrade AS t JOIN DimDate AS d ON d.SK_DateID = t.SK_CloseDateID WHERE t.TradeID = ?",
        [trade],
    ).fetchone() == (warehouse.connection.execute("SELECT batchdate FROM BatchDate WHERE batchid = 2").fetchone()[0],)


def test_a_holding_of_a_trade_created_earlier_is_loaded_with_the_close_date_of_that_trade(warehouse):
    keys = HOLDING_OF_THAT_TRADE.get(warehouse.scale)
    if keys is None:
        pytest.skip("no reference holding is recorded for this scale factor")
    rows = warehouse.connection.execute(
        "SELECT h.BatchID, h.SK_DateID = t.SK_CloseDateID, h.SK_TimeID = t.SK_CloseTimeID, h.CurrentPrice = t.TradePrice "
        "FROM FactHoldings AS h JOIN DimTrade AS t ON t.TradeID = h.CurrentTradeID "
        "WHERE h.TradeID = ? AND h.CurrentTradeID = ? AND h.BatchID = 2",
        list(keys),
    ).fetchall()
    assert rows == [(2, True, True, True)]


def test_every_cancelled_watch_updates_a_row_instead_of_adding_one(warehouse):
    connection = warehouse.connection
    assert connection.execute(
        "SELECT count(*) FROM (SELECT SK_CustomerID, SK_SecurityID, SK_DateID_DatePlaced FROM FactWatches "
        "GROUP BY ALL HAVING count(*) > 1)"
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT count(*) FROM FactWatches WHERE SK_DateID_DateRemoved < SK_DateID_DatePlaced"
    ).fetchone() == (0,)


def test_every_incremental_cash_balance_continues_from_the_previous_balance(warehouse):
    assert warehouse.connection.execute(
        """
        WITH ordered AS (
          SELECT a.AccountID, f.SK_DateID, f.Cash, f.BatchID,
                 lag(f.Cash) OVER (PARTITION BY a.AccountID ORDER BY f.SK_DateID) AS previous
          FROM FactCashBalances AS f JOIN DimAccount AS a ON a.SK_AccountID = f.SK_AccountID
        ),
        daily AS (
          SELECT accountid, batchid, sum(ct_amt) AS amount FROM CashTransactionRaw GROUP BY accountid, batchid
        )
        SELECT count(*)
        FROM ordered AS o
        JOIN daily AS d ON d.accountid = o.AccountID AND d.batchid = o.BatchID
        WHERE o.BatchID = (SELECT max(batchid) FROM CashTransactionRaw) AND o.previous IS NOT NULL
          AND o.Cash <> o.previous + d.amount
        """
    ).fetchone() == (0,)


@pytest.mark.parametrize("scale", sorted(OUTPUTS) or [None])
def test_an_update_reads_only_its_own_batch_directory(scale, tmp_path):
    if scale is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    staging = tmp_path / "staging"
    staging.mkdir()
    for entry in OUTPUTS[scale].iterdir():
        if entry.name != "Batch3":
            (staging / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    connection = duckdb.connect()
    run_initialization(connection)
    run_historical_load(connection, staging, allow_third_party_extensions=True)
    run_incremental_update(connection, staging, 2)
    for table in ("TradeRaw", "CashTransactionRaw", "HoldingHistoryRaw", "WatchHistoryRaw", "DailyMarketRaw"):
        assert connection.execute(f"SELECT count(DISTINCT batchid), max(batchid) FROM {table}").fetchone() == (1, 2)
    connection.close()
