from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from benchbox.core.tpcdi.etl import historical, staging
from benchbox.core.tpcdi.etl.historical import HISTORICAL_STEPS
from benchbox.core.tpcdi.etl.staging import STEPS_BY_NAME

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

STATUS_NAMES = ("Active", "Inactive", "Completed", "Canceled", "Pending", "Submitted")
CREATED_TABLE = re.compile(r"CREATE TEMPORARY TABLE (\w+)")
DROPPED_TABLE = re.compile(r"DROP TABLE (\w+)")

BATCH_FILES = {
    "Date.txt": "20170707|2017-07-07|July 7, 2017|2017|2017|20173|2017 Q3|201707|2017 July|201727|2017-W27|5|Friday|2017|2017|20173|2017 Q3|false\n",
    "StatusType.txt": "ACTV|Active\nINAC|Inactive\n",
    "Industry.txt": "AM|Advertising|SC\n",
    "TaxRate.txt": "US1|US rate|0.15000\nCN1|CN rate|0.05000\n",
    "BatchDate.txt": "2017-07-07\n",
    "HR.csv": "7,1,Kim,Sam,,314,Branch,OFFICE1,(726) 088-3331\n",
    "Prospect.csv": (
        "AG1,Lee,Ann,,F,1 Main St,,A1A,Town,ON,Canada,555,250000,1,0,S,30,700,O,Acme,2,50000\n"
        "AG2,Nobody,Bob,,M,2 Side St,,B2B,Town,ON,Canada,555,60000,1,0,S,50,700,O,Acme,2,200000\n"
    ),
}

INGEST = ("dim_date", "status_type", "industry", "tax_rate", "batch_date", "dim_broker", "prospect")
DIMENSION_TABLES = ("DimCompany", "DimSecurity", "Financial", "DimCustomer", "DimAccount", "Prospect")
DIMENSION_STEPS = tuple(step for step in HISTORICAL_STEPS if step.table in DIMENSION_TABLES)


def company(name: str, cik: str, rating: str) -> str:
    return (
        name.ljust(60)
        + cik
        + "ACTV"
        + "AM"
        + rating.ljust(4)
        + "19800101"
        + "1 Road".ljust(80)
        + "".ljust(80)
        + "A1A".ljust(12)
        + "Town".ljust(25)
        + "ON".ljust(20)
        + "Canada".ljust(24)
        + "Chief".ljust(46)
        + "Makes things".ljust(150)
    )


def security(symbol: str, company_key: str) -> str:
    return (
        symbol.ljust(15)
        + "COMMON"
        + "ACTV"
        + "Shares".ljust(70)
        + "NYSE".ljust(6)
        + "1000".rjust(13)
        + "19900101"
        + "19900102"
        + "1.25".rjust(12)
        + company_key.ljust(60)
    )


def financial(company_key: str) -> str:
    return (
        "2002"
        + "1"
        + "20020101"
        + "20020401"
        + "1000.00".rjust(17)
        + "100.00".rjust(17)
        + "0.10".rjust(12)
        + "0.09".rjust(12)
        + "0.10".rjust(12)
        + "5.00".rjust(17)
        + "50.00".rjust(17)
        + "20.00".rjust(17)
        + "1000".rjust(13)
        + "1100".rjust(13)
        + company_key.ljust(60)
    )


FINWIRE = [
    ("CMP", date(2000, 1, 3), company("Alpha", "0000000001", "AAA")),
    ("CMP", date(2005, 6, 1), company("Alpha", "0000000001", "ZZ")),
    ("CMP", date(2006, 1, 1), company("Beta", "0000000002", "BB")),
    ("SEC", date(2001, 1, 1), security("SYM1", "0000000001")),
    ("SEC", date(2006, 2, 1), security("SYM2", "Beta")),
    ("FIN", date(2002, 4, 1), financial("0000000001")),
    ("FIN", date(2006, 3, 1), financial("Beta")),
]

CUSTOMER_MGMT_COLUMNS = (
    "customerid BIGINT, accountid BIGINT, brokerid BIGINT, taxid VARCHAR, accountdesc VARCHAR, taxstatus SMALLINT, "
    "lastname VARCHAR, firstname VARCHAR, middleinitial VARCHAR, gender VARCHAR, tier SMALLINT, dob DATE, "
    "addressline1 VARCHAR, addressline2 VARCHAR, postalcode VARCHAR, city VARCHAR, stateprov VARCHAR, "
    "country VARCHAR, phone1 VARCHAR, phone2 VARCHAR, phone3 VARCHAR, email1 VARCHAR, email2 VARCHAR, "
    "lcl_tx_id VARCHAR, nat_tx_id VARCHAR, update_ts TIMESTAMP, actiontype VARCHAR"
)


def action(action_type: str, ts: str, customer: int, **fields) -> dict:
    return {"actiontype": action_type, "update_ts": ts, "customerid": customer, **fields}


ANN = {
    "taxid": "111",
    "lastname": "Lee",
    "firstname": "Ann",
    "gender": "f",
    "dob": "1980-01-01",
    "addressline1": "1 Main St",
    "postalcode": "A1A",
    "city": "Town",
    "stateprov": "ON",
    "nat_tx_id": "US1",
    "lcl_tx_id": "CN1",
}
BOB = {**ANN, "taxid": "222", "lastname": "Ray", "firstname": "Bob", "addressline1": "9 Far Rd", "dob": "1900-01-01"}

ACTIONS = [
    action(
        "NEW",
        "2010-01-01 10:00:00",
        1,
        accountid=11,
        brokerid=7,
        accountdesc="Ann main",
        taxstatus=1,
        tier=1,
        email1="a@x",
        **ANN,
    ),
    action("UPDCUST", "2012-05-05 09:00:00", 1, email1="b@x"),
    action("UPDCUST", "2012-05-05 18:00:00", 1, tier=7),
    action("NEW", "2011-01-01 10:00:00", 2, accountid=21, brokerid=7, accountdesc="Bob main", taxstatus=2, **BOB),
    action("ADDACCT", "2013-01-01 10:00:00", 2, accountid=22, brokerid=7, accountdesc="Bob extra", taxstatus=2),
    action("CLOSEACCT", "2014-01-01 10:00:00", 2, accountid=22),
    action("INACT", "2015-01-01 10:00:00", 2),
]


@pytest.fixture
def warehouse(tmp_path):
    from benchbox.core.tpcdi.schema import TPCDISchemaManager

    batch = tmp_path / "Batch1"
    batch.mkdir()
    for name, text in BATCH_FILES.items():
        (batch / name).write_text(text, encoding="utf-8")
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    staging.load_batch1(
        connection, tmp_path, allow_third_party_extensions=False, steps=[STEPS_BY_NAME[name] for name in INGEST]
    )
    connection.executemany("INSERT INTO FinWire VALUES (?, ?, ?)", FINWIRE)
    connection.execute(f"CREATE TABLE CustomerMgmtRaw ({CUSTOMER_MGMT_COLUMNS})")
    for row in ACTIONS:
        columns = ", ".join(row)
        connection.execute(
            f"INSERT INTO CustomerMgmtRaw ({columns}) VALUES ({', '.join('?' for _ in row)})", list(row.values())
        )
    results = historical.load_historical(connection, steps=DIMENSION_STEPS)
    yield connection, results
    connection.close()


def test_every_historical_step_has_one_sql_file_and_the_reverse():
    directory = Path(historical.HISTORICAL_DIR)
    assert {step.path.name for step in HISTORICAL_STEPS} == {path.name for path in directory.glob("*.sql")}
    assert len({step.table for step in HISTORICAL_STEPS}) == len(HISTORICAL_STEPS)


@pytest.mark.parametrize("step", HISTORICAL_STEPS, ids=lambda step: step.name)
def test_historical_sql_names_no_status_literal(step):
    text = step.path.read_text(encoding="utf-8")
    literals = set(re.findall(r"'([^']*)'", text))
    assert not literals & set(STATUS_NAMES)


def test_historical_sql_uses_no_floating_point():
    for step in HISTORICAL_STEPS:
        text = step.path.read_text(encoding="utf-8").upper()
        assert "DOUBLE" not in text
        assert "FLOAT" not in text


def test_steps_report_the_rows_they_insert(warehouse):
    _, results = warehouse
    assert {result.step.table: result.rows for result in results} == {
        "DimCompany": 3,
        "DimSecurity": 3,
        "Financial": 2,
        "DimCustomer": 4,
        "DimAccount": 7,
        "Prospect": 2,
    }


def test_alerts_are_written_per_source_record_with_batch_one(warehouse):
    connection, _ = warehouse
    rows = connection.execute(
        "SELECT BatchID, MessageSource, MessageText, MessageType, MessageData FROM DImessages "
        "WHERE MessageType = 'Alert' ORDER BY MessageText, MessageData"
    ).fetchall()
    assert rows == [
        (1, "DimCustomer", "DOB out of range", "Alert", "C_ID = 2, C_DOB = 1900-01-01"),
        (1, "DimCompany", "Invalid SPRating", "Alert", "CO_ID = 0000000001, CO_SP_RATE = ZZ"),
        (1, "DimCustomer", "Invalid customer tier", "Alert", "C_ID = 1, C_TIER = 7"),
        (1, "DimCustomer", "Invalid customer tier", "Alert", "C_ID = 2, C_TIER = "),
    ]


def test_company_history_and_rating(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_CompanyID, Status, SPrating, isLowGrade, IsCurrent, EffectiveDate, EndDate "
        "FROM DimCompany ORDER BY SK_CompanyID"
    ).fetchall() == [
        (200001031, "Active", "AAA", False, False, date(2000, 1, 3), date(2005, 6, 1)),
        (200506011, "Active", None, None, True, date(2005, 6, 1), date(9999, 12, 31)),
        (200601012, "Active", "BB", True, True, date(2006, 1, 1), date(9999, 12, 31)),
    ]


def test_securities_follow_their_company_by_identifier_or_name(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_SecurityID, Symbol, SK_CompanyID, Dividend, EffectiveDate, EndDate FROM DimSecurity "
        "ORDER BY SK_SecurityID"
    ).fetchall() == [
        (1, "SYM1", 200001031, Decimal("1.25"), date(2001, 1, 1), date(2005, 6, 1)),
        (2, "SYM1", 200506011, Decimal("1.25"), date(2005, 6, 1), date(9999, 12, 31)),
        (3, "SYM2", 200601012, Decimal("1.25"), date(2006, 2, 1), date(9999, 12, 31)),
    ]
    assert connection.execute(
        "SELECT SK_CompanyID, FI_REVENUE, FI_BASIC_EPS, typeof(FI_REVENUE) FROM Financial ORDER BY SK_CompanyID"
    ).fetchall() == [
        (200001031, Decimal("1000.00"), Decimal("0.10"), "DECIMAL(15,2)"),
        (200601012, Decimal("1000.00"), Decimal("0.10"), "DECIMAL(15,2)"),
    ]


def test_customer_history_keeps_the_last_change_of_each_day(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, Status, Gender, Tier, Email1, LastName, NationalTaxRate, AgencyID, MarketingNameplate, "
        "IsCurrent, EndDate FROM DimCustomer ORDER BY SK_CustomerID"
    ).fetchall() == [
        (
            201001011,
            "Active",
            "F",
            1,
            "a@x",
            "Lee",
            Decimal("0.15000"),
            "AG1",
            "HighValue+MoneyAlert",
            False,
            date(2012, 5, 5),
        ),
        (201101012, "Active", "F", None, None, "Ray", Decimal("0.15000"), None, None, False, date(2015, 1, 1)),
        (
            201205051,
            "Active",
            "F",
            7,
            "b@x",
            "Lee",
            Decimal("0.15000"),
            "AG1",
            "HighValue+MoneyAlert",
            True,
            date(9999, 12, 31),
        ),
        (201501012, "Inactive", "F", None, None, "Ray", Decimal("0.15000"), None, None, True, date(9999, 12, 31)),
    ]


def test_accounts_split_where_their_customer_changes(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT AccountID, SK_CustomerID, SK_BrokerID, Status, EffectiveDate, EndDate FROM DimAccount "
        "ORDER BY AccountID, EffectiveDate"
    ).fetchall() == [
        (11, 201001011, 7, "Active", date(2010, 1, 1), date(2012, 5, 5)),
        (11, 201205051, 7, "Active", date(2012, 5, 5), date(9999, 12, 31)),
        (21, 201101012, 7, "Active", date(2011, 1, 1), date(2015, 1, 1)),
        (21, 201501012, 7, "Active", date(2015, 1, 1), date(9999, 12, 31)),
        (22, 201101012, 7, "Active", date(2013, 1, 1), date(2014, 1, 1)),
        (22, 201101012, 7, "Inactive", date(2014, 1, 1), date(2015, 1, 1)),
        (22, 201501012, 7, "Inactive", date(2015, 1, 1), date(9999, 12, 31)),
    ]


def test_prospect_marks_customers_and_records_its_insert_count(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT AgencyID, SK_RecordDateID, SK_UpdateDateID, BatchID, IsCustomer, MarketingNameplate "
        "FROM Prospect ORDER BY AgencyID"
    ).fetchall() == [
        ("AG1", 20170707, 20170707, 1, True, "HighValue+MoneyAlert"),
        ("AG2", 20170707, 20170707, 1, False, "Boomer"),
    ]
    assert connection.execute(
        "SELECT BatchID, MessageSource, MessageText, MessageType, MessageData FROM DImessages "
        "WHERE MessageType = 'Status'"
    ).fetchall() == [(1, "Prospect", "Inserted rows", "Status", "2")]


def test_rendered_historical_sql_goes_through_the_translator():
    for step in HISTORICAL_STEPS:
        statements = staging.render_step(step)
        assert statements
        created = {match.group(1) for match in map(CREATED_TABLE.match, statements) if match}
        dropped = {match.group(1) for match in map(DROPPED_TABLE.match, statements) if match}
        assert created == dropped
        assert all(
            f"INTO {step.table}" in statement
            or "INTO DImessages" in statement
            or statement.startswith(("CREATE TEMPORARY TABLE", "DROP TABLE"))
            for statement in statements
        )
