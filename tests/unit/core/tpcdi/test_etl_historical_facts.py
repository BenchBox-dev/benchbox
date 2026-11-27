from __future__ import annotations

import re
from decimal import Decimal

import pytest

from benchbox.core.tpcdi.etl import historical
from benchbox.core.tpcdi.etl.historical import HISTORICAL_STEPS

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

STEPS = {step.name: step for step in HISTORICAL_STEPS}

LATER_BATCH = re.compile(r"Batch\s*[23]|Batch\*|Batch\[|batchid\s*(=|IN)\s*\(?\s*[23]", re.IGNORECASE)

DAILY_MARKET = "dm_date DATE, dm_s_symb VARCHAR, dm_close DECIMAL(8,2), dm_high DECIMAL(8,2), dm_low DECIMAL(8,2), dm_vol BIGINT, batchid INTEGER"


def names_a_later_batch(text: str) -> bool:
    return bool(LATER_BATCH.search(text))


@pytest.fixture
def market():
    from benchbox.core.tpcdi.schema import TPCDISchemaManager

    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    connection.execute(
        """
        INSERT INTO DimDate
        SELECT CAST(strftime(d, '%Y%m%d') AS BIGINT), d, '', 0, '', 0, '', 0, '', 0, '', 0, '', 0, '', 0, '', false
        FROM (SELECT CAST(range AS DATE) AS d FROM range(DATE '2014-01-01', DATE '2017-01-01', INTERVAL 1 DAY))
        """
    )
    connection.execute(
        "INSERT INTO DimCompany VALUES "
        "(1, 1, 'Active', 'One', 'Ind', 'AAA', false, 'C', '', '', 'P', 'C', 'S', '', 'D', NULL, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(2, 2, 'Active', 'Two', 'Ind', 'AAA', false, 'C', '', '', 'P', 'C', 'S', '', 'D', NULL, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(3, 3, 'Active', 'Three', 'Ind', 'AAA', false, 'C', '', '', 'P', 'C', 'S', '', 'D', NULL, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(4, 4, 'Active', 'Four', 'Ind', 'AAA', false, 'C', '', '', 'P', 'C', 'S', '', 'D', NULL, true, 1, DATE '2000-01-01', DATE '9999-12-31')"
    )
    connection.execute(
        "INSERT INTO DimSecurity VALUES "
        "(1, 'EDGE', 'COMMON', 'Active', 'Edge', 'NYSE', 1, 1, DATE '1990-01-01', DATE '1990-01-01', 2.00, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(2, 'TIES', 'COMMON', 'Active', 'Ties', 'NYSE', 2, 1, DATE '1990-01-01', DATE '1990-01-01', 1.00, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(3, 'NOPE', 'COMMON', 'Active', 'Nope', 'NYSE', 3, 1, DATE '1990-01-01', DATE '1990-01-01', 1.00, true, 1, DATE '2000-01-01', DATE '9999-12-31'), "
        "(4, 'HALF', 'COMMON', 'Active', 'Half', 'NYSE', 4, 1, DATE '1990-01-01', DATE '1990-01-01', 1.71, true, 1, DATE '2000-01-01', DATE '9999-12-31')"
    )
    connection.execute(
        "INSERT INTO Financial "
        "SELECT 1, year(q), quarter(q), q, 100, 10, 1.00, 1.00, 0.10, 0, 0, 0, 10, 10 "
        "FROM (SELECT CAST(range AS DATE) AS q FROM range(DATE '2015-01-01', DATE '2016-01-01', INTERVAL 3 MONTH))"
    )
    connection.execute(
        "INSERT INTO Financial "
        "SELECT 4, year(q), quarter(q), q, 100, 10, 1.50, 1.50, 0.10, 0, 0, 0, 10, 10 "
        "FROM (SELECT CAST(range AS DATE) AS q FROM range(DATE '2015-01-01', DATE '2016-01-01', INTERVAL 3 MONTH))"
    )
    connection.execute(f"CREATE TABLE DailyMarketRaw ({DAILY_MARKET})")
    connection.execute(
        """
        INSERT INTO DailyMarketRaw
        SELECT d, 'EDGE', 40.00,
               CASE WHEN d = DATE '2015-03-02' THEN 50.00 ELSE 10.00 END,
               CASE WHEN d = DATE '2015-03-01' THEN 1.00 ELSE 5.00 END,
               100, 1
        FROM (SELECT CAST(range AS DATE) AS d FROM range(DATE '2015-03-01', DATE '2016-03-02', INTERVAL 1 DAY))
        """
    )
    connection.execute(
        """
        INSERT INTO DailyMarketRaw
        SELECT d, 'TIES', 10.00,
               CASE WHEN d IN (DATE '2016-01-10', DATE '2016-01-20') THEN 30.00 ELSE 20.00 END,
               CASE WHEN d IN (DATE '2016-01-12', DATE '2016-01-22') THEN 2.00 ELSE 3.00 END,
               100, 1
        FROM (SELECT CAST(range AS DATE) AS d FROM range(DATE '2016-01-01', DATE '2016-02-01', INTERVAL 1 DAY))
        """
    )
    connection.execute("INSERT INTO DailyMarketRaw VALUES (DATE '2016-02-01', 'NOPE', 10.00, 11.00, 9.00, 100, 1)")
    connection.execute(
        "INSERT INTO DailyMarketRaw VALUES "
        "(DATE '2016-02-01', 'HALF', 600.00, 600.00, 600.00, 100, 1), "
        "(DATE '2016-02-02', 'HALF', 1.71, 1.71, 1.71, 100, 1)"
    )
    historical.load_historical(connection, steps=[STEPS["fact_market_history"]])
    yield connection
    connection.close()


def row(connection, symbol_key: int, day: str):
    return connection.execute(
        "SELECT PERatio, Yield, FiftyTwoWeekHigh, SK_FiftyTwoWeekHighDate, FiftyTwoWeekLow, SK_FiftyTwoWeekLowDate "
        "FROM FactMarketHistory WHERE SK_SecurityID = ? AND SK_DateID = ?",
        [symbol_key, int(day.replace("-", ""))],
    ).fetchone()


def test_fifty_two_weeks_are_the_dates_after_the_same_day_a_year_earlier(market):
    _, _, high, high_date, low, low_date = row(market, 1, "2016-03-01")
    assert (high, high_date) == (Decimal("50.00"), 20150302)
    assert (low, low_date) == (Decimal("5.00"), 20150302)


def test_ties_store_the_earliest_date(market):
    _, _, high, high_date, low, low_date = row(market, 2, "2016-01-31")
    assert (high, high_date) == (Decimal("30.00"), 20160110)
    assert (low, low_date) == (Decimal("2.00"), 20160112)


def test_yield_is_the_dividend_over_the_close_times_one_hundred(market):
    assert row(market, 1, "2016-03-01")[1] == Decimal("5.00")


def test_price_earnings_uses_the_four_calendar_quarters_before_the_date(market):
    assert row(market, 1, "2016-03-01")[0] == Decimal("10.00")
    assert row(market, 1, "2015-07-01")[0] == Decimal("20.00")


def test_half_cent_quotients_round_half_away_from_zero(market):
    assert row(market, 4, "2016-02-01")[1] == Decimal("0.29")
    assert row(market, 4, "2016-02-02")[0] == Decimal("0.29")


def test_a_company_without_earnings_gets_no_ratio_and_an_alert(market):
    assert row(market, 3, "2016-02-01")[0] is None
    assert market.execute(
        "SELECT BatchID, MessageSource, MessageText, MessageType, MessageData FROM DImessages "
        "WHERE MessageData = 'DM_S_SYMB = NOPE'"
    ).fetchall() == [(1, "FactMarketHistory", "No earnings for company", "Alert", "DM_S_SYMB = NOPE")]


@pytest.mark.parametrize("step", HISTORICAL_STEPS, ids=lambda step: step.name)
def test_historical_sql_reads_no_later_batch(step):
    assert not names_a_later_batch(step.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        "FROM read_csv('out/Batch2/Trade.txt')",
        "FROM read_csv('out/Batch*/Trade.txt')",
        "FROM TradeRaw WHERE batchid = 3",
        "FROM TradeRaw WHERE batchid IN (2, 3)",
    ],
)
def test_the_later_batch_check_catches_later_batch_reads(text):
    assert names_a_later_batch(text)


@pytest.mark.parametrize("text", ["FROM read_csv('out/Batch1/Trade.txt')", "FROM TradeRaw WHERE batchid = 1"])
def test_the_later_batch_check_accepts_batch_one_reads(text):
    assert not names_a_later_batch(text)
