from __future__ import annotations

import importlib
import re
from pathlib import Path

import duckdb
import pytest
import yaml

from benchbox.core.tpcdi import schema
from benchbox.core.tpcdi.schema import (
    CORE_TABLE_ORDER,
    STAGING_TABLE_ORDER,
    TABLES,
    TPCDISchemaManager,
    get_all_create_table_sql,
    get_create_table_sql,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCHEMA_DIR = Path(schema.__file__).parent

META_TYPES = {
    "BALANCE_T": "SNUM(12,2)",
    "FIN_AGG_T": "SNUM(15,2)",
    "IDENT_T": "NUM(11)",
    "S_COUNT_T": "NUM(12)",
    "S_PRICE_T": "SNUM(8,2)",
    "SK_T": "NUM(11)",
    "TRADE_T": "NUM(15)",
    "VALUE_T": "SNUM(10,2)",
}

SPECIFICATION_TABLES = """
DimAccount
SK_AccountID SK_T NN
AccountID IDENT_T NN
SK_BrokerID SK_T NN
SK_CustomerID SK_T NN
Status CHAR(10) NN
AccountDesc CHAR(50)
TaxStatus NUM(1)
IsCurrent BOOLEAN NN
BatchID NUM(5) NN
EffectiveDate DATE NN
EndDate DATE NN

DimBroker
SK_BrokerID SK_T NN
BrokerID IDENT_T NN
ManagerID IDENT_T
FirstName CHAR(50) NN
LastName CHAR(50) NN
MiddleInitial CHAR(1)
Branch CHAR(50)
Office CHAR(50)
Phone CHAR(14)
IsCurrent BOOLEAN NN
BatchID NUM(5) NN
EffectiveDate DATE NN
EndDate DATE NN

DimCompany
SK_CompanyID SK_T NN
CompanyID IDENT_T NN
Status CHAR(10) NN
Name CHAR(60) NN
Industry CHAR(50) NN
SPrating CHAR(4)
isLowGrade BOOLEAN
CEO CHAR(100) NN
AddressLine1 CHAR(80)
AddressLine2 CHAR(80)
PostalCode CHAR(12) NN
City CHAR(25) NN
StateProv CHAR(20) NN
Country CHAR(24)
Description CHAR(150) NN
FoundingDate DATE
IsCurrent BOOLEAN NN
BatchID NUM(5) NN
EffectiveDate DATE NN
EndDate DATE NN

DimCustomer
SK_CustomerID SK_T NN
CustomerID IDENT_T NN
TaxID CHAR(20) NN
Status CHAR(10) NN
LastName CHAR(30) NN
FirstName CHAR(30) NN
MiddleInitial CHAR(1)
Gender CHAR(1)
Tier NUM(1)
DOB DATE NN
AddressLine1 CHAR(80) NN
AddressLine2 CHAR(80)
PostalCode CHAR(12) NN
City CHAR(25) NN
StateProv CHAR(20) NN
Country CHAR(24)
Phone1 CHAR(30)
Phone2 CHAR(30)
Phone3 CHAR(30)
Email1 CHAR(50)
Email2 CHAR(50)
NationalTaxRateDesc CHAR(50)
NationalTaxRate NUM(6,5)
LocalTaxRateDesc CHAR(50)
LocalTaxRate NUM(6,5)
AgencyID CHAR(30)
CreditRating NUM(5)
NetWorth SNUM(10)
MarketingNameplate CHAR(100)
IsCurrent BOOLEAN NN
BatchID NUM(5) NN
EffectiveDate DATE NN
EndDate DATE NN

DimDate
SK_DateID SK_T NN
DateValue DATE NN
DateDesc CHAR(20) NN
CalendarYearID NUM(4) NN
CalendarYearDesc CHAR(20) NN
CalendarQtrID NUM(5) NN
CalendarQtrDesc CHAR(20) NN
CalendarMonthID NUM(6) NN
CalendarMonthDesc CHAR(20) NN
CalendarWeekID NUM(6) NN
CalendarWeekDesc CHAR(20) NN
DayOfWeekNum NUM(1) NN
DayOfWeekDesc CHAR(10) NN
FiscalYearID NUM(4) NN
FiscalYearDesc CHAR(20) NN
FiscalQtrID NUM(5) NN
FiscalQtrDesc CHAR(20) NN
HolidayFlag BOOLEAN

DimSecurity
SK_SecurityID SK_T NN
Symbol CHAR(15) NN
Issue CHAR(6) NN
Status CHAR(10) NN
Name CHAR(70) NN
ExchangeID CHAR(6) NN
SK_CompanyID SK_T NN
SharesOutstanding S_COUNT_T NN
FirstTrade DATE NN
FirstTradeOnExchange DATE NN
Dividend VALUE_T NN
IsCurrent BOOLEAN NN
BatchID NUM(5) NN
EffectiveDate DATE NN
EndDate DATE NN

DimTime
SK_TimeID SK_T NN
TimeValue TIME NN
HourID NUM(2) NN
HourDesc CHAR(20) NN
MinuteID NUM(2) NN
MinuteDesc CHAR(20) NN
SecondID NUM(2) NN
SecondDesc CHAR(20) NN
MarketHoursFlag BOOLEAN
OfficeHoursFlag BOOLEAN

DimTrade
TradeID IDENT_T NN
SK_BrokerID SK_T
SK_CreateDateID SK_T NN
SK_CreateTimeID SK_T NN
SK_CloseDateID SK_T
SK_CloseTimeID SK_T
Status CHAR(10) NN
Type CHAR(12) NN
CashFlag BOOLEAN NN
SK_SecurityID SK_T NN
SK_CompanyID SK_T NN
Quantity NUM(6,0) NN
BidPrice NUM(8,2) NN
SK_CustomerID SK_T NN
SK_AccountID SK_T NN
ExecutedBy CHAR(64) NN
TradePrice NUM(8,2)
Fee NUM(10,2)
Commission NUM(10,2)
Tax NUM(10,2)
BatchID NUM(5) NN

DImessages
MessageDateAndTime DATETIME NN
BatchID NUM(5) NN
MessageSource CHAR(30)
MessageText CHAR(50) NN
MessageType CHAR(12) NN
MessageData CHAR(100)

FactCashBalances
SK_CustomerID SK_T NN
SK_AccountID SK_T NN
SK_DateID SK_T NN
Cash SNUM(15,2) NN
BatchID NUM(5) NN

FactHoldings
TradeID IDENT_T NN
CurrentTradeID IDENT_T NN
SK_CustomerID SK_T NN
SK_AccountID SK_T NN
SK_SecurityID SK_T NN
SK_CompanyID SK_T NN
SK_DateID SK_T NN
SK_TimeID SK_T NN
CurrentPrice S_PRICE_T
CurrentHolding SNUM(6) NN
BatchID NUM(5) NN

FactMarketHistory
SK_SecurityID SK_T NN
SK_CompanyID SK_T NN
SK_DateID SK_T NN
PERatio NUM(10,2)
Yield NUM(5,2) NN
FiftyTwoWeekHigh NUM(8,2) NN
SK_FiftyTwoWeekHighDate SK_T NN
FiftyTwoWeekLow NUM(8,2) NN
SK_FiftyTwoWeekLowDate SK_T NN
ClosePrice NUM(8,2) NN
DayHigh NUM(8,2) NN
DayLow NUM(8,2) NN
Volume NUM(12) NN
BatchID NUM(5) NN

FactWatches
SK_CustomerID SK_T NN
SK_SecurityID SK_T NN
SK_DateID_DatePlaced SK_T NN
SK_DateID_DateRemoved SK_T
BatchID NUM(5) NN

Industry
IN_ID CHAR(2) NN
IN_NAME CHAR(50) NN
IN_SC_ID CHAR(4) NN

Financial
SK_CompanyID IDENT_T NN
FI_YEAR NUM(4) NN
FI_QTR NUM(1) NN
FI_QTR_START_DATE DATE NN
FI_REVENUE SNUM(15,2) NN
FI_NET_EARN SNUM(15,2) NN
FI_BASIC_EPS SNUM(10,2) NN
FI_DILUT_EPS SNUM(10,2) NN
FI_MARGIN SNUM(10,2) NN
FI_INVENTORY SNUM(15,2) NN
FI_ASSETS SNUM(15,2) NN
FI_LIABILITY SNUM(15,2) NN
FI_OUT_BASIC SNUM(12) NN
FI_OUT_DILUT SNUM(12) NN

Prospect
AgencyID CHAR(30) NN
SK_RecordDateID SK_T NN
SK_UpdateDateID SK_T NN
BatchID NUM(5) NN
IsCustomer BOOLEAN NN
LastName CHAR(30) NN
FirstName CHAR(30) NN
MiddleInitial CHAR(1)
Gender CHAR(1)
AddressLine1 CHAR(80)
AddressLine2 CHAR(80)
PostalCode CHAR(12)
City CHAR(25) NN
State CHAR(20) NN
Country CHAR(24)
Phone CHAR(30)
Income NUM(9)
NumberCars NUM(2)
NumberChildren NUM(2)
MaritalStatus CHAR(1)
Age NUM(3)
CreditRating NUM(4)
OwnOrRentFlag CHAR(1)
Employer CHAR(30)
NumberCreditCards NUM(2)
NetWorth NUM(12)
MarketingNameplate CHAR(100)

StatusType
ST_ID CHAR(4) NN
ST_NAME CHAR(10) NN

TaxRate
TX_ID CHAR(4) NN
TX_NAME CHAR(50) NN
TX_RATE NUM(6,5) NN

TradeType
TT_ID CHAR(3) NN
TT_NAME CHAR(12) NN
TT_IS_SELL NUM(1) NN
TT_IS_MRKT NUM(1) NN

Audit
DataSet CHAR(20) NN
BatchID NUM(5)
Date DATE
Attribute CHAR(50) NN
Value SNUM(15)
DValue SNUM(15,5)
"""

STAGING_TABLE_NAMES = [
    "FinWire",
    "WatchIncremental",
    "DailyMarketIncremental",
    "CashTransactionIncremental",
    "HoldingIncremental",
    "TradeIncremental",
    "AccountIncremental",
    "CustomerIncremental",
    "ProspectIncremental",
    "BatchDate",
]

FLOAT_TYPES = re.compile(r"\b(DOUBLE|FLOAT|REAL)\b", re.IGNORECASE)


def parse_transcription(text: str) -> dict[str, list[tuple[str, str, bool]]]:
    tables: dict[str, list[tuple[str, str, bool]]] = {}
    for block in text.strip().split("\n\n"):
        lines = block.strip().splitlines()
        columns = []
        for line in lines[1:]:
            parts = line.split()
            columns.append((parts[0], parts[1], len(parts) == 3 and parts[2] == "NN"))
        tables[lines[0]] = columns
    return tables


EXPECTED = parse_transcription(SPECIFICATION_TABLES)


def expected_ddl_type(spec_type: str) -> str:
    spec_type = META_TYPES.get(spec_type, spec_type)
    char = re.fullmatch(r"CHAR\((\d+)\)", spec_type)
    if char:
        return f"VARCHAR({char.group(1)})"
    number = re.fullmatch(r"S?NUM\((\d+)(?:,(\d+))?\)", spec_type)
    if number:
        digits, scale = int(number.group(1)), number.group(2)
        if scale is not None:
            return f"DECIMAL({digits},{scale})"
        if digits <= 4:
            return "SMALLINT"
        if digits <= 9:
            return "INTEGER"
        return "BIGINT"
    return {"DATETIME": "TIMESTAMP"}.get(spec_type, spec_type)


def actual_columns(table_name: str) -> list[tuple[str, str, bool]]:
    return [(col["name"], col["type"], bool(col.get("not_null"))) for col in TABLES[table_name]["columns"]]


def expected_columns(table_name: str) -> list[tuple[str, str, bool]]:
    return [(name, expected_ddl_type(spec_type), not_null) for name, spec_type, not_null in EXPECTED[table_name]]


def test_core_tables_are_the_specification_tables_in_specification_order():
    assert list(EXPECTED) == CORE_TABLE_ORDER


@pytest.mark.parametrize("table_name", list(EXPECTED))
def test_table_matches_the_transcription_column_for_column(table_name):
    assert actual_columns(table_name) == expected_columns(table_name)


def test_tables_the_old_schema_lacked_are_present_and_fact_trade_is_gone():
    assert "FactTrade" not in TABLES
    assert {"DimTrade", "DImessages", "Audit", "Prospect", "Financial"} <= set(TABLES)
    assert [name for name, _, _ in actual_columns("FactHoldings")][:2] == ["TradeID", "CurrentTradeID"]


def test_staging_tables_follow_the_core_tables():
    assert STAGING_TABLE_ORDER == STAGING_TABLE_NAMES


def test_one_schema_source_remains():
    assert not (SCHEMA_DIR / "schema_extensions.py").exists()
    assert not (SCHEMA_DIR / "schema_extension_specs.yaml").exists()
    assert sorted(path.name for path in SCHEMA_DIR.glob("*schema*.yaml")) == ["schema_specs.yaml"]


def test_no_column_uses_a_floating_point_type():
    offenders = [
        f"{table}.{name}: {column_type}"
        for table, columns in ((t, actual_columns(t)) for t in TABLES)
        for name, column_type, _ in columns
        if FLOAT_TYPES.search(column_type)
    ]
    assert offenders == []


@pytest.mark.parametrize("dialect", ["standard", "duckdb", "postgres", "databricks", "spark", "clickhouse"])
def test_generated_ddl_has_no_floating_point_column(dialect):
    ddl = "\n".join(TPCDISchemaManager().get_create_table_ddl(dialect))
    assert FLOAT_TYPES.search(ddl) is None


def test_money_and_quantity_columns_are_exact_numerics():
    money = {
        "DimTrade": ["BidPrice", "TradePrice", "Fee", "Commission", "Tax", "Quantity"],
        "FactCashBalances": ["Cash"],
        "FactHoldings": ["CurrentPrice", "CurrentHolding"],
        "FactMarketHistory": ["Yield", "ClosePrice", "DayHigh", "DayLow", "Volume", "PERatio"],
        "DimSecurity": ["Dividend"],
        "Financial": ["FI_REVENUE", "FI_BASIC_EPS", "FI_OUT_BASIC"],
        "TradeIncremental": ["bidprice", "tradeprice", "fee", "commission", "tax"],
        "DailyMarketIncremental": ["dm_close", "dm_high", "dm_low"],
        "CashTransactionIncremental": ["cash"],
    }
    types = {(t, name): column_type for t in TABLES for name, column_type, _ in actual_columns(t)}
    for table, names in money.items():
        for name in names:
            assert re.match(r"(DECIMAL|SMALLINT|INTEGER|BIGINT)", types[(table, name)]), (table, name)


def test_vocabulary_values_are_not_in_the_schema_source():
    text = (SCHEMA_DIR / "schema_specs.yaml").read_text(encoding="utf-8")
    for value in ("Market Buy", "Limit Sell", "Stop Loss", "ACTV", "INAC", "SBMT", "CMPT", "Agriculture"):
        assert value not in text
    spec = yaml.safe_load(text)
    assert set(spec) == {"core_tables", "staging_tables"}


@pytest.mark.parametrize("dialect", ["standard", "duckdb", "postgres", "databricks", "spark", "clickhouse"])
def test_flag_columns_follow_the_per_platform_rule(dialect):
    flag_type = "BOOLEAN" if dialect == "duckdb" else "SMALLINT"
    assert f"IsCurrent {flag_type} NOT NULL" in get_create_table_sql("DimAccount", dialect)
    assert f"CashFlag {flag_type} NOT NULL" in get_create_table_sql("DimTrade", dialect)
    assert f"isLowGrade {flag_type}" in get_create_table_sql("DimCompany", dialect)
    assert f"cashflag {flag_type}" in get_create_table_sql("TradeIncremental", dialect)


def test_time_columns_follow_the_platform_rule():
    assert "TimeValue TIME NOT NULL" in get_create_table_sql("DimTime", "duckdb")
    assert "TimeValue STRING NOT NULL" in get_create_table_sql("DimTime", "databricks")
    assert "TimeValue String NOT NULL" in get_create_table_sql("DimTime", "clickhouse")


def test_tpcdi_rules_are_registered_under_ddl_optimize():
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.tpcdi_ddl")
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    entries = [
        (phase, entry.rule_id) for (phase, _, benchmark, _), entry in REGISTRY.all_rules() if benchmark == "tpcdi"
    ]
    rules = {rule_id for _, rule_id in entries}
    assert "ddl_optimize.duckdb.tpcdi.flag_columns_boolean" in rules
    assert "ddl_optimize.databricks.tpcdi.time_as_string" in rules
    assert {phase for phase, _ in entries} == {Phase.DDL_OPTIMIZE}


def test_duckdb_evaluates_true_equals_one():
    assert duckdb.connect().execute("SELECT true = 1").fetchone() == (True,)


def test_ddl_executes_on_duckdb_and_flags_compare_with_one():
    connection = duckdb.connect()
    for statement in get_all_create_table_sql("duckdb").split("\n\n"):
        connection.execute(statement)
    created = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert {name.lower() for name in created} == {name.lower() for name in TABLES}
    assert len(created) == len(TABLES)
    connection.execute(
        "INSERT INTO DimAccount VALUES (1, 1, 1, 1, 'Active', NULL, NULL, true, 1, DATE '2020-01-01', DATE '9999-12-31')"
    )
    assert connection.execute("SELECT COUNT(*) FROM DimAccount WHERE IsCurrent = 1").fetchone() == (1,)


def test_schema_manager_creates_and_drops_the_schema_on_duckdb():
    connection = duckdb.connect()
    manager = TPCDISchemaManager()
    manager.create_schema(connection, "duckdb")
    assert len(connection.execute("SHOW TABLES").fetchall()) == len(TABLES)
    manager.drop_schema(connection)
    assert connection.execute("SHOW TABLES").fetchall() == []


def test_unknown_table_is_refused():
    with pytest.raises(ValueError):
        get_create_table_sql("FactTrade")


@pytest.mark.parametrize(
    "module",
    [
        "benchbox.core.tpcdi",
        "benchbox.core.tpcdi.benchmark",
        "benchbox.core.tpcdi.loader",
        "benchbox.core.tpcdi.validation",
        "benchbox.core.tpcdi.metrics",
        "benchbox.core.tpcdi.source_generators",
        "benchbox.core.tpcdi.etl.pipeline",
        "benchbox.core.tpcdi.etl.sql_backend",
        "benchbox.core.tpcdi.etl.scd_processor",
        "benchbox.core.tpcdi.etl.incremental_loader",
        "benchbox.core.tpcdi.etl.finwire_processor",
        "benchbox.core.tpcdi.etl.customer_mgmt_processor",
        "benchbox.core.tpcdi.etl.data_quality_monitor",
        "benchbox.core.tpcdi.generator",
    ],
)
def test_retained_old_modules_still_import(module):
    pytest.importorskip("pandas")
    importlib.import_module(module)
