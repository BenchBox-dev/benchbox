from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from benchbox.core.tpcdi import phases
from benchbox.core.tpcdi.etl import incremental, staging
from benchbox.core.tpcdi.etl.historical import HISTORICAL_STEPS, TransformStep, load_historical
from benchbox.core.tpcdi.etl.incremental import INCREMENTAL_INGEST_STEPS, INCREMENTAL_STEPS, load_incremental
from benchbox.core.tpcdi.etl.staging import STEPS_BY_NAME
from benchbox.core.tpcdi.schema import TPCDISchemaManager

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

STATUS_NAMES = ("Active", "Inactive", "Completed", "Canceled", "Pending", "Submitted")
CREATED_TABLE = re.compile(r"CREATE TEMPORARY TABLE (\w+)")
DROPPED_TABLE = re.compile(r"DROP TABLE (\w+)")
LATER_BATCH = re.compile(r"Batch\s*[0-9]|Batch\*|Batch\[|batchid\s*=\s*[0-9]|BatchID\s*=\s*[0-9]")
RAW_READ = re.compile(r"\b(?:FROM|JOIN) (?:CustomerRaw|AccountRaw|ProspectRaw)\b")
BATCH_GUARD = re.compile(r"\bbatchid = \$batch_id")

BATCH_ONE_DATE = "20170707|2017-07-07|July 7, 2017|2017|2017|20173|2017 Q3|201707|2017 July|201727|2017-W27|5|Friday|2017|2017|20173|2017 Q3|false\n"
BATCH_TWO_DATE = "20170708|2017-07-08|July 8, 2017|2017|2017|20173|2017 Q3|201707|2017 July|201727|2017-W27|6|Saturday|2017|2017|20173|2017 Q3|false\n"

PROSPECT_AG1 = "AG1,Lee,Ann,,F,1 Main St,,A1A,Town,ON,Canada,555,250000,1,0,S,30,700,O,Acme,2,50000\n"
PROSPECT_AG2 = "AG2,Ray,Bob,,M,9 Far Rd,,A1A,Town,ON,Canada,555,60000,1,0,S,50,700,O,Acme,2,200000\n"
PROSPECT_AG3 = "AG3,Poe,Cat,,F,3 Elm St,,C3C,Town,ON,Canada,555,60000,1,0,S,30,700,O,Acme,2,200000\n"
PROSPECT_AG3_RICHER = "AG3,Poe,Cat,,F,3 Elm St,,C3C,Town,ON,Canada,555,60000,1,0,S,30,700,O,Acme,2,300000\n"
PROSPECT_AG5 = "AG5,Fox,Dan,,M,4 Oak Rd,,D4D,Town,ON,Canada,555,60000,1,0,S,30,700,O,Acme,2,200000\n"

BATCH_ONE_FILES = {
    "Date.txt": BATCH_ONE_DATE + BATCH_TWO_DATE,
    "StatusType.txt": "ACTV|Active\nINAC|Inactive\n",
    "TaxRate.txt": "US1|US rate|0.15000\nCN1|CN rate|0.05000\n",
    "BatchDate.txt": "2017-07-07\n",
    "HR.csv": "7,1,Kim,Sam,,314,Branch,OFFICE1,(726) 088-3331\n",
    "Prospect.csv": PROSPECT_AG1 + PROSPECT_AG2 + PROSPECT_AG3,
}

CUSTOMER_FIELDS = (
    "cdc_flag",
    "cdc_dsn",
    "customerid",
    "taxid",
    "status",
    "lastname",
    "firstname",
    "middleinitial",
    "gender",
    "tier",
    "dob",
    "addressline1",
    "addressline2",
    "postalcode",
    "city",
    "stateprov",
    "country",
    "phone1_ctry",
    "phone1_area",
    "phone1_local",
    "phone1_ext",
    "phone2_ctry",
    "phone2_area",
    "phone2_local",
    "phone2_ext",
    "phone3_ctry",
    "phone3_area",
    "phone3_local",
    "phone3_ext",
    "email1",
    "email2",
    "lcl_tx_id",
    "nat_tx_id",
)

ANN = {
    "taxid": "111",
    "status": "ACTV",
    "lastname": "Lee",
    "firstname": "Ann",
    "gender": "F",
    "tier": "1",
    "dob": "1980-01-01",
    "addressline1": "1 Main St",
    "postalcode": "A1A",
    "city": "Town",
    "stateprov": "ON",
    "country": "Canada",
    "nat_tx_id": "US1",
    "lcl_tx_id": "CN1",
}
BOB = {**ANN, "taxid": "222", "lastname": "Ray", "firstname": "Bob", "gender": "M", "addressline1": "9 Far Rd"}
CAT = {**ANN, "taxid": "333", "lastname": "Poe", "firstname": "Cat", "addressline1": "3 Elm St", "postalcode": "C3C"}
DAN = {
    **ANN,
    "taxid": "444",
    "lastname": "Fox",
    "firstname": "Dan",
    "gender": "M",
    "addressline1": "4 Oak Rd",
    "postalcode": "D4D",
}
EVE = {
    **ANN,
    "taxid": "666",
    "lastname": "Moe",
    "firstname": "Eve",
    "addressline1": "6 Pine Rd",
    "postalcode": "F6F",
    "lcl_tx_id": "XX9",
}

HISTORICAL_CUSTOMERS = (
    (1, ANN, "AG1", 700, 50000, "HighValue+MoneyAlert"),
    (2, BOB, "AG2", 700, 200000, "Boomer"),
    (3, CAT, "AG3", 700, 200000, None),
    (4, DAN, None, None, None, None),
)
HISTORICAL_ACCOUNTS = (
    (11, 1, "ACTV", "Ann main", 1),
    (21, 2, "ACTV", "Bob main", 2),
    (22, 2, "INAC", "Bob extra", 2),
    (31, 3, "ACTV", "Cat main", 1),
)


def customer_line(**values: object) -> str:
    return "|".join(str(values.get(field, "")) for field in CUSTOMER_FIELDS) + "\n"


def customer_image(cdc: str, dsn: int, customerid: int, base: dict, **changes: object) -> str:
    return customer_line(cdc_flag=cdc, cdc_dsn=dsn, customerid=customerid, **{**base, **changes})


BATCH_TWO_CUSTOMERS = (
    customer_image("U", 1, 1, ANN, email1="new@x", phone1_ctry="1", phone1_area="726", phone1_local="555-1234")
    + customer_image("U", 2, 2, BOB, status="INAC", tier="2")
    + customer_image("I", 3, 6, EVE, tier="", dob="2099-01-01", gender="x")
    + customer_image("U", 4, 6, EVE, tier="2", email1="eve@x")
)
BATCH_TWO_ACCOUNTS = "U|1|11|7|1|Ann renamed|2|ACTV\nI|2|61|7|6||1|ACTV\n"
BATCH_TWO_PROSPECT = PROSPECT_AG1 + PROSPECT_AG3_RICHER + PROSPECT_AG5


def write_batch(root: Path, number: int, files: dict[str, str]) -> None:
    directory = root / f"Batch{number}"
    directory.mkdir()
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")


def insert_row(connection, table: str, row: dict) -> None:
    columns = ", ".join(row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))


def insert_historical_customer(connection, customerid: int, base: dict, agency, rating, worth, nameplate) -> None:
    names = {"ACTV": "Active", "INAC": "Inactive"}
    insert_row(
        connection,
        "DimCustomer",
        {
            "SK_CustomerID": int(f"20100101{customerid}"),
            "CustomerID": customerid,
            "TaxID": base["taxid"],
            "Status": names[base["status"]],
            "LastName": base["lastname"],
            "FirstName": base["firstname"],
            "Gender": base["gender"],
            "Tier": 1,
            "DOB": date(1980, 1, 1),
            "AddressLine1": base["addressline1"],
            "PostalCode": base["postalcode"],
            "City": "Town",
            "StateProv": "ON",
            "Country": "Canada",
            "NationalTaxRateDesc": "US rate",
            "NationalTaxRate": Decimal("0.15000"),
            "LocalTaxRateDesc": "CN rate",
            "LocalTaxRate": Decimal("0.05000"),
            "AgencyID": agency,
            "CreditRating": rating,
            "NetWorth": worth,
            "MarketingNameplate": nameplate,
            "IsCurrent": True,
            "BatchID": 1,
            "EffectiveDate": date(2010, 1, 1),
            "EndDate": date(9999, 12, 31),
        },
    )


def build_warehouse(root: Path, customers=HISTORICAL_CUSTOMERS, accounts=HISTORICAL_ACCOUNTS):
    write_batch(root, 1, BATCH_ONE_FILES)
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    ingest = [STEPS_BY_NAME[name] for name in ("dim_date", "status_type", "tax_rate", "batch_date", "dim_broker")]
    staging.load_batch(connection, root, 1, steps=ingest, allow_third_party_extensions=False)
    for customer in customers:
        insert_historical_customer(connection, *customer)
    broker = connection.execute("SELECT SK_BrokerID FROM DimBroker").fetchone()[0]
    names = {"ACTV": "Active", "INAC": "Inactive"}
    for accountid, customerid, status, description, taxstatus in accounts:
        insert_row(
            connection,
            "DimAccount",
            {
                "SK_AccountID": int(f"20100101{accountid}"),
                "AccountID": accountid,
                "SK_BrokerID": broker,
                "SK_CustomerID": int(f"20100101{customerid}"),
                "Status": names[status],
                "AccountDesc": description,
                "TaxStatus": taxstatus,
                "IsCurrent": True,
                "BatchID": 1,
                "EffectiveDate": date(2010, 1, 1),
                "EndDate": date(9999, 12, 31),
            },
        )
    staging.load_batch(connection, root, 1, steps=[STEPS_BY_NAME["prospect"]], allow_third_party_extensions=False)
    load_historical(connection, steps=[step for step in HISTORICAL_STEPS if step.name == "prospect"])
    return connection


def update_batch_two(root: Path, connection) -> list:
    write_batch(
        root,
        2,
        {
            "BatchDate.txt": "2017-07-08\n",
            "Prospect.csv": BATCH_TWO_PROSPECT,
            "Customer.txt": BATCH_TWO_CUSTOMERS,
            "Account.txt": BATCH_TWO_ACCOUNTS,
        },
    )
    staging.load_batch(connection, root, 2, steps=INCREMENTAL_INGEST_STEPS, allow_third_party_extensions=False)
    return load_incremental(connection, 2)


@pytest.fixture
def warehouse(tmp_path):
    connection = build_warehouse(tmp_path)
    before = {
        "DimCustomer": connection.execute("SELECT * FROM DimCustomer ORDER BY SK_CustomerID").fetchall(),
        "DimAccount": connection.execute("SELECT * FROM DimAccount ORDER BY SK_AccountID").fetchall(),
    }
    results = update_batch_two(tmp_path, connection)
    yield connection, results, before
    connection.close()


def test_every_incremental_step_has_one_sql_file_and_the_reverse():
    directory = Path(incremental.INCREMENTAL_DIR)
    assert {step.path.name for step in INCREMENTAL_STEPS} == {path.name for path in directory.glob("*.sql")}
    ingest = Path(incremental.INCREMENTAL_INGEST_DIR)
    own = [step for step in INCREMENTAL_INGEST_STEPS if step.directory == ingest]
    assert {step.path.name for step in own} == {path.name for path in ingest.glob("*.sql")}
    assert len({step.table for step in INCREMENTAL_STEPS}) == len(INCREMENTAL_STEPS)
    assert all(step.atomic for step in INCREMENTAL_STEPS)


def test_the_incremental_phase_order_follows_the_dependencies():
    assert [step.name for step in INCREMENTAL_STEPS] == ["dim_customer", "dim_account", "prospect"]
    assert [step.name for step in INCREMENTAL_INGEST_STEPS][:2] == ["batch_date", "prospect"]


@pytest.mark.parametrize("step", INCREMENTAL_STEPS, ids=lambda step: step.name)
def test_incremental_sql_names_no_status_literal_or_floating_type(step):
    text = step.path.read_text(encoding="utf-8")
    assert not set(re.findall(r"'([^']*)'", text)) & set(STATUS_NAMES)
    assert "DOUBLE" not in text.upper()
    assert "FLOAT" not in text.upper()


@pytest.mark.parametrize("step", (*INCREMENTAL_STEPS, *INCREMENTAL_INGEST_STEPS[2:]), ids=lambda step: step.name)
def test_incremental_sql_names_no_batch_directory_or_literal_batch_id(step):
    assert not LATER_BATCH.search(step.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("step", INCREMENTAL_STEPS, ids=lambda step: step.name)
def test_every_raw_table_read_carries_the_batch_guard(step):
    for statement in staging.render_step(step):
        assert len(BATCH_GUARD.findall(statement)) >= len(RAW_READ.findall(statement))
        if RAW_READ.search(statement):
            assert "$batch_id" in statement


def test_ingest_parameters_name_only_the_batch_directory_of_the_phase():
    for batch in (2, 3):
        for step in INCREMENTAL_INGEST_STEPS:
            value = staging.step_parameters("out", step, batch)[step.parameter]
            assert value == str(Path("out").resolve() / f"Batch{batch}" / step.source)


def test_rendered_incremental_sql_goes_through_the_translator():
    for step in INCREMENTAL_STEPS:
        statements = staging.render_step(step)
        created = {match.group(1) for match in map(CREATED_TABLE.match, statements) if match}
        dropped = {match.group(1) for match in map(DROPPED_TABLE.match, statements) if match}
        assert created == dropped


def test_each_statement_binds_only_the_parameters_it_names():
    connection = duckdb.connect()
    connection.execute("CREATE TABLE seen (batch_id INTEGER, other INTEGER)")
    sql = "INSERT INTO seen (batch_id) VALUES ($batch_id);\nINSERT INTO seen (other) VALUES (7)"
    for statement in staging.render_sql(sql):
        bound = staging.statement_parameters(statement, "duckdb", {"batch_id": 2, "unused": 9})
        if "$batch_id" in statement:
            assert bound == {"batch_id": 2}
            connection.execute(statement, bound)
        else:
            assert bound == {}
            connection.execute(statement)
    assert connection.execute("SELECT batch_id, other FROM seen ORDER BY other NULLS FIRST").fetchall() == [
        (2, None),
        (None, 7),
    ]
    connection.close()


def test_a_missing_parameter_is_refused():
    with pytest.raises(KeyError):
        staging.statement_parameters("SELECT $batch_id", "duckdb", {})


def test_an_atomic_step_rolls_back_a_partial_expire(tmp_path):
    connection = build_warehouse(tmp_path)
    write_batch(
        tmp_path,
        2,
        {
            "BatchDate.txt": "2017-07-08\n",
            "Prospect.csv": BATCH_TWO_PROSPECT,
            "Customer.txt": BATCH_TWO_CUSTOMERS,
            "Account.txt": BATCH_TWO_ACCOUNTS,
        },
    )
    staging.load_batch(connection, tmp_path, 2, steps=INCREMENTAL_INGEST_STEPS, allow_third_party_extensions=False)
    connection.execute("DROP TABLE StatusType")
    with pytest.raises(duckdb.Error):
        load_incremental(connection, 2, steps=[INCREMENTAL_STEPS[0]])
    assert connection.execute("SELECT count(*) FROM DimCustomer WHERE IsCurrent").fetchone() == (4,)
    assert connection.execute("SELECT count(*) FROM DimCustomer").fetchone() == (4,)
    connection.close()


def test_an_update_is_refused_without_the_previous_completion_record(tmp_path):
    connection = build_warehouse(tmp_path)
    with pytest.raises(phases.PhaseOrderError):
        phases.run_incremental_update(connection, tmp_path, 3, steps=())
    connection.close()


def test_steps_report_the_rows_they_insert(warehouse):
    _, results, _ = warehouse
    assert {result.step.table: result.rows for result in results} == {"DimCustomer": 5, "DimAccount": 5, "Prospect": 1}


def test_customer_alerts_are_written_per_source_row_with_the_batch_of_the_phase(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT BatchID, MessageText, MessageData FROM DImessages WHERE MessageType = 'Alert' ORDER BY MessageText"
    ).fetchall() == [
        (2, "DOB out of range", "C_ID = 6, C_DOB = 2099-01-01"),
        (2, "Invalid customer tier", "C_ID = 6, C_TIER = "),
    ]


def test_the_last_cdc_row_of_a_customer_decides_the_new_record(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, Status, Gender, Tier, DOB, Email1, LocalTaxRate, AgencyID, IsCurrent, BatchID, "
        "EffectiveDate, EndDate FROM DimCustomer WHERE CustomerID = 6"
    ).fetchall() == [
        (
            201707086,
            "Active",
            "F",
            2,
            date(1980, 1, 1),
            "eve@x",
            None,
            None,
            True,
            2,
            date(2017, 7, 8),
            date(9999, 12, 31),
        )
    ]


def test_a_customer_update_expires_the_old_record_and_builds_the_phone(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, Email1, Phone1, AgencyID, MarketingNameplate, IsCurrent, EffectiveDate, EndDate "
        "FROM DimCustomer WHERE CustomerID = 1 ORDER BY SK_CustomerID"
    ).fetchall() == [
        (201001011, None, None, "AG1", "HighValue+MoneyAlert", False, date(2010, 1, 1), date(2017, 7, 8)),
        (
            201707081,
            "new@x",
            "+1 (726) 555-1234",
            "AG1",
            "HighValue+MoneyAlert",
            True,
            date(2017, 7, 8),
            date(9999, 12, 31),
        ),
    ]


def test_a_prospect_only_change_expires_and_inserts_a_new_customer_record(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, NetWorth, AgencyID, TaxID, IsCurrent, BatchID, EffectiveDate, EndDate "
        "FROM DimCustomer WHERE CustomerID = 3 ORDER BY SK_CustomerID"
    ).fetchall() == [
        (201001013, 200000, "AG3", "333", False, 1, date(2010, 1, 1), date(2017, 7, 8)),
        (201707083, 300000, "AG3", "333", True, 2, date(2017, 7, 8), date(9999, 12, 31)),
    ]
    assert connection.execute(
        "SELECT SK_CustomerID, AgencyID, CreditRating, NetWorth, IsCurrent FROM DimCustomer WHERE CustomerID = 4 "
        "ORDER BY SK_CustomerID"
    ).fetchall() == [(201001014, None, None, None, False), (201707084, "AG5", 700, 200000, True)]


def test_an_unchanged_prospect_leaves_its_customer_alone(warehouse):
    connection, _, _ = warehouse
    assert connection.execute("SELECT count(*) FROM DimCustomer WHERE CustomerID = 1 AND IsCurrent").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM DimCustomer").fetchone() == (9,)


def test_an_inactive_customer_makes_its_accounts_inactive(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT Status, IsCurrent FROM DimCustomer WHERE CustomerID = 2 ORDER BY SK_CustomerID"
    ).fetchall() == [("Active", False), ("Inactive", True)]
    assert connection.execute(
        "SELECT AccountID, SK_CustomerID, Status, IsCurrent, BatchID, EffectiveDate, EndDate FROM DimAccount "
        "WHERE AccountID IN (21, 22) ORDER BY AccountID, SK_AccountID"
    ).fetchall() == [
        (21, 201001012, "Active", False, 1, date(2010, 1, 1), date(2017, 7, 8)),
        (21, 201707082, "Inactive", True, 2, date(2017, 7, 8), date(9999, 12, 31)),
        (22, 201001012, "Inactive", False, 1, date(2010, 1, 1), date(2017, 7, 8)),
        (22, 201707082, "Inactive", True, 2, date(2017, 7, 8), date(9999, 12, 31)),
    ]


def test_accounts_follow_a_customer_whose_only_change_comes_from_prospect(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT SK_CustomerID, Status, AccountDesc, IsCurrent, BatchID FROM DimAccount WHERE AccountID = 31 "
        "ORDER BY SK_AccountID"
    ).fetchall() == [(201001013, "Active", "Cat main", False, 1), (201707083, "Active", "Cat main", True, 2)]


def test_account_images_are_copied_as_given_and_a_new_account_gets_its_keys(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT SK_AccountID, SK_CustomerID, AccountDesc, TaxStatus, Status, IsCurrent FROM DimAccount "
        "WHERE AccountID IN (11, 61) ORDER BY SK_AccountID"
    ).fetchall() == [
        (2010010111, 201001011, "Ann main", 1, "Active", False),
        (2017070811, 201707081, "Ann renamed", 2, "Active", True),
        (2017070861, 201707086, None, 1, "Active", True),
    ]


def test_history_is_never_changed_in_place(warehouse):
    connection, _, before = warehouse
    for table, key, validity in (
        ("DimCustomer", "SK_CustomerID", {"IsCurrent", "EndDate"}),
        ("DimAccount", "SK_AccountID", {"IsCurrent", "EndDate"}),
    ):
        columns = [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
        after = {row[columns.index(key)]: row for row in connection.execute(f"SELECT * FROM {table}").fetchall()}
        for old in before[table]:
            kept = after[old[columns.index(key)]]
            for index, column in enumerate(columns):
                if column not in validity:
                    assert old[index] == kept[index], (table, column)
        assert len(after) > len(before[table])
        assert connection.execute(
            f"SELECT count(*) FROM {table} WHERE EffectiveDate = DATE '2017-07-08' AND NOT IsCurrent"
        ).fetchone() == (0,)


def test_new_changed_and_seen_prospects_carry_their_batch_and_dates(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT AgencyID, SK_RecordDateID, SK_UpdateDateID, BatchID, IsCustomer, NetWorth FROM Prospect "
        "ORDER BY AgencyID"
    ).fetchall() == [
        ("AG1", 20170708, 20170707, 1, True, 50000),
        ("AG2", 20170707, 20170707, 1, False, 200000),
        ("AG3", 20170708, 20170708, 2, True, 300000),
        ("AG5", 20170708, 20170708, 2, True, 200000),
    ]


def test_an_agency_missing_from_the_file_is_kept_and_follows_its_customer_status(warehouse):
    connection, _, _ = warehouse
    assert connection.execute("SELECT IsCustomer, BatchID FROM Prospect WHERE AgencyID = 'AG2'").fetchall() == [
        (False, 1)
    ]


def test_prospect_writes_its_three_counts_with_the_batch_of_the_phase(warehouse):
    connection, _, _ = warehouse
    assert connection.execute(
        "SELECT BatchID, MessageText, MessageType, MessageData FROM DImessages WHERE MessageSource = 'Prospect' "
        "AND BatchID = 2 ORDER BY MessageText"
    ).fetchall() == [
        (2, "Inserted rows", "Status", "1"),
        (2, "Source rows", "Status", "3"),
        (2, "Updated rows", "Status", "1"),
    ]


def test_a_customer_matching_two_prospects_takes_the_highest_agency_deterministically(tmp_path):
    ann = HISTORICAL_CUSTOMERS[0]
    connection = build_warehouse(
        tmp_path, customers=(ann, (4, DAN, None, None, None, None)), accounts=((11, 1, "ACTV", "Ann main", 1),)
    )
    write_batch(
        tmp_path,
        2,
        {
            "BatchDate.txt": "2017-07-08\n",
            "Prospect.csv": PROSPECT_AG5 + PROSPECT_AG5.replace("AG5", "AG6").replace("200000", "900000"),
            "Customer.txt": customer_image("U", 1, 1, ANN, email1="new@x"),
            "Account.txt": "U|1|11|7|1|Ann main|1|ACTV\n",
        },
    )
    staging.load_batch(connection, tmp_path, 2, steps=INCREMENTAL_INGEST_STEPS, allow_third_party_extensions=False)
    load_incremental(connection, 2)
    assert connection.execute(
        "SELECT AgencyID, NetWorth FROM DimCustomer WHERE CustomerID = 4 AND IsCurrent"
    ).fetchall() == [("AG6", 900000)]
    connection.close()


def test_initialization_drops_the_raw_tables_of_the_incremental_updates():
    connection = duckdb.connect()
    for table in ("CustomerRaw", "AccountRaw", "ProspectRaw"):
        connection.execute(f"CREATE TABLE {table} (batchid INTEGER)")
    phases.run_initialization(connection)
    leftover = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ('CustomerRaw', 'AccountRaw', 'ProspectRaw')"
    ).fetchall()
    assert leftover == []
    connection.close()
