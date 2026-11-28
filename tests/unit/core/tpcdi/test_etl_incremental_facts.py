from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from benchbox.core.tpcdi.etl import staging
from benchbox.core.tpcdi.etl.incremental import INCREMENTAL_INGEST_STEPS, INCREMENTAL_STEPS, load_incremental
from benchbox.core.tpcdi.schema import TPCDISchemaManager

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

FACT_STEP_NAMES = ("dim_trade", "fact_cash_balances", "fact_holdings", "fact_watches", "fact_market_history")
FACT_STEPS = [step for step in INCREMENTAL_STEPS if step.name in FACT_STEP_NAMES]
FACT_INGEST_STEPS = [
    step
    for step in INCREMENTAL_INGEST_STEPS
    if step.name in ("batch_date", "trade", "cash_transaction", "holding_history", "watch_history", "daily_market")
]
RAW_READ = re.compile(
    r"\b(?:FROM|JOIN) (?:TradeRaw|CashTransactionRaw|HoldingHistoryRaw|WatchHistoryRaw|DailyMarketRaw)\b"
)
BATCH_GUARD = re.compile(r"\bbatchid = \$batch_id")

TRADE_ONE_CLOSE = "2017-07-08 00:59:31"
TRADE_TWO_CREATE = "2017-07-08 01:00:00"
TRADE_TWO_CLOSE = "2017-07-08 01:30:00"
TRADE_THREE_CREATE = "2017-07-08 02:00:00"

BATCH_TWO_TRADES = (
    f"U|1|1|{TRADE_ONE_CLOSE}|CMPT|TMB|0|AAA|100|4.06|100|exec|4.01|1.00|1.00|50.00\n"
    f"I|2|2|{TRADE_TWO_CREATE}|SBMT|TMB|1|AAA|10|4.06|100|exec||||\n"
    f"I|3|3|{TRADE_THREE_CREATE}|SBMT|TMB|1|AAA|10|4.06|100|exec||||\n"
    f"U|4|2|{TRADE_TWO_CLOSE}|CMPT|TMB|1|AAA|10|4.06|100|exec|4.00|500.00|1.00|2.00\n"
)
BATCH_TWO_CASH = "I|1|100|2017-07-08 10:00:00|25.50|deposit\nI|2|100|2017-07-08 10:05:00|-10.00|withdrawal\n"
BATCH_TWO_HOLDINGS = "I|1|900|1|5|0\n"
BATCH_TWO_WATCHES = (
    "I|1|5|AAA|2017-07-08 11:00:00|CNCL\nI|2|6|AAA|2017-07-08 11:01:00|ACTV\nI|3|6|AAA|2017-07-08 11:02:00|CNCL\n"
)
BATCH_TWO_MARKET = "I|1|2017-07-07|AAA|20.00|50.00|5.00|1000\nI|2|2017-07-07|NEPS|10.00|11.00|9.00|50\n"


def required_default(column_type: str):
    if column_type == "VARCHAR":
        return ""
    if column_type == "BOOLEAN":
        return False
    if column_type == "DATE":
        return date(2010, 1, 1)
    return 0


def insert(connection, table: str, **row) -> None:
    for name, column_type, nullable, *_ in connection.execute(f"DESCRIBE {table}").fetchall():
        if nullable == "NO" and name not in row:
            row[name] = required_default(column_type)
    columns = ", ".join(row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))


def write_batch(root: Path, number: int, files: dict[str, str]) -> None:
    directory = root / f"Batch{number}"
    directory.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")


def build_batch_one(connection) -> None:
    connection.execute(
        "INSERT INTO DimDate "
        "SELECT CAST(strftime(d, '%Y%m%d') AS BIGINT), d, '', 0, '', 0, '', 0, '', 0, '', 0, '', 0, '', 0, '', false "
        "FROM (SELECT CAST(range AS DATE) AS d FROM range(DATE '2016-01-01', DATE '2017-08-01', INTERVAL 1 DAY))"
    )
    for time_id, value in ((5931, "00:59:31"), (10000, "01:00:00"), (13000, "01:30:00"), (20000, "02:00:00")):
        connection.execute("INSERT INTO DimTime VALUES (?, ?, 0, '', 0, '', 0, '', false, false)", [time_id, value])
    for status, name in (("SBMT", "Submitted"), ("CMPT", "Completed"), ("CNCL", "Canceled")):
        insert(connection, "StatusType", ST_ID=status, ST_NAME=name)
    insert(connection, "TradeType", TT_ID="TMB", TT_NAME="Market Buy", TT_IS_SELL=0, TT_IS_MRKT=1)
    insert(connection, "BatchDate", batchdate=date(2017, 7, 7), batchid=1)
    for company in (10, 11):
        insert(connection, "DimCompany", SK_CompanyID=company, CompanyID=company, IsCurrent=True, BatchID=1)
    securities = (
        (1, "AAA", 10, False, date(2017, 7, 8), Decimal("1.71")),
        (2, "AAA", 10, True, date(9999, 12, 31), Decimal("1.71")),
        (3, "NEPS", 11, True, date(9999, 12, 31), Decimal("1.00")),
    )
    for key, symbol, company, current, end, dividend in securities:
        insert(
            connection,
            "DimSecurity",
            SK_SecurityID=key,
            Symbol=symbol,
            SK_CompanyID=company,
            Dividend=dividend,
            IsCurrent=current,
            BatchID=1,
            EffectiveDate=date(2010, 1, 1),
            EndDate=end,
        )
    for quarter_start in (date(2016, 7, 1), date(2016, 10, 1), date(2017, 1, 1), date(2017, 4, 1)):
        insert(connection, "Financial", SK_CompanyID=10, FI_BASIC_EPS=Decimal("1.50"), FI_QTR_START_DATE=quarter_start)
    for key, customer, current, end in ((501, 5, False, date(2017, 7, 8)), (502, 5, True, date(9999, 12, 31))):
        insert(
            connection,
            "DimCustomer",
            SK_CustomerID=key,
            CustomerID=customer,
            IsCurrent=current,
            BatchID=1,
            EffectiveDate=date(2010, 1, 1),
            EndDate=end,
        )
    insert(
        connection,
        "DimCustomer",
        SK_CustomerID=601,
        CustomerID=6,
        IsCurrent=True,
        BatchID=1,
        EffectiveDate=date(2010, 1, 1),
        EndDate=date(9999, 12, 31),
    )
    for key, customer, current, end in ((1001, 501, False, date(2017, 7, 8)), (1002, 502, True, date(9999, 12, 31))):
        insert(
            connection,
            "DimAccount",
            SK_AccountID=key,
            AccountID=100,
            SK_BrokerID=7,
            SK_CustomerID=customer,
            IsCurrent=current,
            BatchID=1,
            EffectiveDate=date(2010, 1, 1),
            EndDate=end,
        )
    insert(
        connection,
        "DimTrade",
        TradeID=1,
        SK_BrokerID=7,
        SK_CreateDateID=20170707,
        SK_CreateTimeID=5931,
        Status="Submitted",
        Type="Market Buy",
        CashFlag=False,
        SK_SecurityID=1,
        SK_CompanyID=10,
        Quantity=100,
        BidPrice=Decimal("4.06"),
        SK_CustomerID=501,
        SK_AccountID=1001,
        ExecutedBy="exec",
        BatchID=1,
    )
    insert(
        connection, "FactCashBalances", SK_CustomerID=501, SK_AccountID=1001, SK_DateID=20170707, Cash=100, BatchID=1
    )
    insert(
        connection,
        "FactWatches",
        SK_CustomerID=501,
        SK_SecurityID=1,
        SK_DateID_DatePlaced=20170601,
        BatchID=1,
    )
    history = (
        (date(2016, 7, 7), Decimal("99.00"), Decimal("0.50")),
        (date(2016, 7, 8), Decimal("50.00"), Decimal("3.00")),
        (date(2016, 9, 1), Decimal("50.00"), Decimal("3.00")),
    )
    for day, high, low in history:
        insert(
            connection,
            "FactMarketHistory",
            SK_SecurityID=1,
            SK_CompanyID=10,
            SK_DateID=int(day.strftime("%Y%m%d")),
            DayHigh=high,
            DayLow=low,
            BatchID=1,
        )


@pytest.fixture
def warehouse(tmp_path):
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    build_batch_one(connection)
    write_batch(
        tmp_path,
        2,
        {
            "BatchDate.txt": "2017-07-08\n",
            "Trade.txt": BATCH_TWO_TRADES,
            "CashTransaction.txt": BATCH_TWO_CASH,
            "HoldingHistory.txt": BATCH_TWO_HOLDINGS,
            "WatchHistory.txt": BATCH_TWO_WATCHES,
            "DailyMarket.txt": BATCH_TWO_MARKET,
        },
    )
    staging.load_batch(connection, tmp_path, 2, steps=FACT_INGEST_STEPS, allow_third_party_extensions=False)
    results = load_incremental(connection, 2, steps=FACT_STEPS)
    yield connection, results
    connection.close()


def test_the_fact_steps_run_after_the_dimensions_in_dependency_order():
    assert [step.name for step in INCREMENTAL_STEPS][3:] == list(FACT_STEP_NAMES)
    assert [step.name for step in FACT_INGEST_STEPS][:1] == ["batch_date"]


@pytest.mark.parametrize("step", FACT_STEPS, ids=lambda step: step.name)
def test_every_raw_table_read_of_a_fact_step_carries_the_batch_guard(step):
    for statement in staging.render_step(step):
        assert len(BATCH_GUARD.findall(statement)) >= len(RAW_READ.findall(statement))
        if RAW_READ.search(statement):
            assert "$batch_id" in statement


def test_the_steps_report_the_rows_they_insert(warehouse):
    _, results = warehouse
    assert {result.step.table: result.rows for result in results} == {
        "DimTrade": 2,
        "FactCashBalances": 1,
        "FactHoldings": 1,
        "FactWatches": 1,
        "FactMarketHistory": 2,
    }


def test_a_trade_created_earlier_and_completed_now_keeps_its_keys_and_moves_its_batch(warehouse):
    connection, _ = warehouse
    rows = connection.execute(
        "SELECT SK_CreateDateID, SK_CreateTimeID, SK_CloseDateID, SK_CloseTimeID, SK_SecurityID, SK_AccountID, "
        "SK_CustomerID, Status, Quantity, TradePrice, Fee, Commission, Tax, BatchID FROM DimTrade WHERE TradeID = 1"
    ).fetchall()
    assert rows == [
        (
            20170707,
            5931,
            20170708,
            5931,
            1,
            1001,
            501,
            "Completed",
            Decimal("100"),
            Decimal("4.01"),
            Decimal("1.00"),
            Decimal("1.00"),
            Decimal("50.00"),
            2,
        )
    ]


def test_a_new_trade_takes_the_current_security_and_account_and_its_last_row(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT TradeID, SK_CreateDateID, SK_CreateTimeID, SK_CloseDateID, SK_CloseTimeID, SK_SecurityID, SK_AccountID, "
        "SK_CustomerID, Status, BatchID FROM DimTrade WHERE TradeID > 1 ORDER BY TradeID"
    ).fetchall() == [
        (2, 20170708, 10000, 20170708, 13000, 2, 1002, 502, "Completed", 2),
        (3, 20170708, 20000, None, None, 2, 1002, 502, "Submitted", 2),
    ]


def test_a_trade_alert_is_written_per_source_row_with_the_batch_of_the_phase(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT BatchID, MessageSource, MessageText, MessageData FROM DImessages WHERE MessageType = 'Alert' "
        "AND MessageSource = 'DimTrade'"
    ).fetchall() == [(2, "DimTrade", "Invalid trade fee", "T_ID = 2, T_CHRG = 500.00")]


def test_a_holding_of_a_trade_created_in_an_earlier_batch_is_loaded_with_its_close_date(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT TradeID, CurrentTradeID, SK_CustomerID, SK_AccountID, SK_SecurityID, SK_CompanyID, SK_DateID, SK_TimeID, "
        "CurrentPrice, CurrentHolding, BatchID FROM FactHoldings"
    ).fetchall() == [(900, 1, 501, 1001, 1, 10, 20170708, 5931, Decimal("4.01"), 0, 2)]


def test_a_cash_balance_continues_from_the_balance_of_an_earlier_surrogate_key_of_the_account(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, SK_AccountID, SK_DateID, Cash, BatchID FROM FactCashBalances ORDER BY BatchID"
    ).fetchall() == [
        (501, 1001, 20170707, Decimal("100.00"), 1),
        (502, 1002, 20170708, Decimal("115.50"), 2),
    ]


def test_a_cancelled_watch_updates_the_row_of_the_earlier_customer_record_instead_of_adding_one(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, SK_SecurityID, SK_DateID_DatePlaced, SK_DateID_DateRemoved, BatchID FROM FactWatches "
        "ORDER BY SK_CustomerID"
    ).fetchall() == [
        (501, 1, 20170601, 20170708, 2),
        (601, 2, 20170708, 20170708, 2),
    ]


def test_the_fifty_two_weeks_are_read_back_from_the_warehouse_and_exclude_the_day_a_year_earlier(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_SecurityID, SK_CompanyID, SK_DateID, FiftyTwoWeekHigh, SK_FiftyTwoWeekHighDate, FiftyTwoWeekLow, "
        "SK_FiftyTwoWeekLowDate, ClosePrice, BatchID FROM FactMarketHistory WHERE SK_SecurityID = 2"
    ).fetchall() == [(2, 10, 20170707, Decimal("50.00"), 20160708, Decimal("3.00"), 20160708, Decimal("20.00"), 2)]


def test_the_ratios_use_the_current_security_and_the_four_calendar_quarters_before_the_batch_day(warehouse):
    connection, _ = warehouse
    assert connection.execute("SELECT PERatio, Yield FROM FactMarketHistory WHERE SK_SecurityID = 2").fetchall() == [
        (Decimal("3.33"), Decimal("8.55"))
    ]


def test_a_company_without_earnings_gets_no_ratio_and_an_alert_with_the_batch_of_the_phase(warehouse):
    connection, _ = warehouse
    assert connection.execute("SELECT PERatio FROM FactMarketHistory WHERE SK_SecurityID = 3").fetchall() == [(None,)]
    assert connection.execute(
        "SELECT BatchID, MessageText, MessageData FROM DImessages WHERE MessageSource = 'FactMarketHistory'"
    ).fetchall() == [(2, "No earnings for company", "DM_S_SYMB = NEPS")]


def test_a_later_update_of_the_same_trade_moves_the_batch_again_and_keeps_every_key(warehouse, tmp_path):
    connection, _ = warehouse
    write_batch(
        tmp_path,
        3,
        {
            "BatchDate.txt": "2017-07-09\n",
            "Trade.txt": "U|1|3|2017-07-08 02:00:00|CNCL|TMB|1|AAA|10|4.06|100|exec|4.00|1.00|1.00|2.00\n",
        },
    )
    trade_ingest = [step for step in FACT_INGEST_STEPS if step.name in ("batch_date", "trade")]
    staging.load_batch(connection, tmp_path, 3, steps=trade_ingest, allow_third_party_extensions=False)
    load_incremental(connection, 3, steps=[step for step in FACT_STEPS if step.name == "dim_trade"])
    assert connection.execute(
        "SELECT Status, SK_CreateDateID, SK_CloseDateID, SK_AccountID, BatchID FROM DimTrade WHERE TradeID = 3"
    ).fetchall() == [("Canceled", 20170708, 20170708, 1002, 3)]
    assert connection.execute("SELECT count(*) FROM DimTrade").fetchone() == (3,)
