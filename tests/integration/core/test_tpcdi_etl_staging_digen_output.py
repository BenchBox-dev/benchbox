from __future__ import annotations

import csv
import hashlib
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from benchbox.core.tpcdi.etl import staging
from benchbox.core.tpcdi.etl.staging import INGEST_STEPS, ThirdPartyExtensionRequired
from benchbox.core.tpcdi.schema import TPCDISchemaManager
from tests.integration._tpcdi_digen_outputs import OUTPUTS_ENV, digen_outputs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.tpcdi,
]

duckdb = pytest.importorskip("duckdb")

AUDIT_COUNTS = {
    "CashTransactionRaw": ("CashTransaction_audit.csv", ("CT_Records",)),
    "DailyMarketRaw": ("DailyMarket_audit.csv", ("DM_RECORDS",)),
    "HoldingHistoryRaw": ("HoldingHistory_audit.csv", ("HH_RECORDS",)),
    "ProspectRaw": ("Prospect_audit.csv", ("P_RECORDS",)),
    "TradeRaw": ("Trade_audit.csv", ("T_Records",)),
    "TradeHistoryRaw": ("TradeHistory_audit.csv", ("TH_Records",)),
    "WatchHistoryRaw": ("WatchHistory_audit.csv", ("WH_RECORDS",)),
    "DimBroker": ("HR_audit.csv", ("HR_BROKERS",)),
    "CustomerMgmtRaw": (
        "CustomerMgmt_audit.csv",
        ("C_NEW", "C_UPDCUST", "C_INACT", "CA_ADDACCT", "CA_CLOSEACCT", "CA_UPDACCT"),
    ),
}

FILE_LINE_COUNTS = {
    "DimDate": "Date.txt",
    "DimTime": "Time.txt",
    "Industry": "Industry.txt",
    "StatusType": "StatusType.txt",
    "TaxRate": "TaxRate.txt",
    "TradeType": "TradeType.txt",
    "BatchDate": "BatchDate.txt",
}

FINWIRE_ATTRIBUTES = ("FW_CMP", "FW_SEC", "FW_FIN")


OUTPUTS = digen_outputs()

requires_output = pytest.mark.skipif(not OUTPUTS, reason=f"{OUTPUTS_ENV} lists no DIGen output")


def audit_total(path: Path, attributes: tuple[str, ...]) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))[1:]
    return sum(int(row[4]) for row in rows if row[3].strip() in attributes)


def expected_counts(source_dir: Path) -> dict[str, int]:
    batch = source_dir / "Batch1"
    expected = {
        table: audit_total(batch / audit_file, attributes) for table, (audit_file, attributes) in AUDIT_COUNTS.items()
    }
    expected["FinWire"] = sum(
        audit_total(path, FINWIRE_ATTRIBUTES)
        for path in sorted(batch.glob("FINWIRE[0-9][0-9][0-9][0-9]Q[1-4]_audit.csv"))
    )
    for table, name in FILE_LINE_COUNTS.items():
        with (batch / name).open("rb") as handle:
            expected[table] = sum(1 for _ in handle)
    return expected


def python_action_count(path: Path) -> int:
    count = 0
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag.rpartition("}")[2] == "Action":
            count += 1
        element.clear()
    return count


def _integer(value: str | None) -> int | None:
    return int(value) if value else None


def python_actions(path: Path) -> Counter[tuple]:
    actions: Counter[tuple] = Counter()
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag.rpartition("}")[2] != "Action":
            continue
        customer = element.find("Customer")
        account = customer.find("Account") if customer is not None else None
        customer_attributes = customer.attrib if customer is not None else {}
        account_attributes = account.attrib if account is not None else {}
        broker = account.findtext("CA_B_ID") if account is not None else None
        dob = customer_attributes.get("C_DOB")
        actions[
            (
                element.get("ActionType"),
                datetime.fromisoformat(element.get("ActionTS")),
                _integer(customer_attributes.get("C_ID")),
                _integer(account_attributes.get("CA_ID")),
                _integer(broker),
                _integer(account_attributes.get("CA_TAX_ST")),
                _integer(customer_attributes.get("C_TIER")),
                date.fromisoformat(dob) if dob else None,
            )
        ] += 1
        element.clear()
    return actions


def staging_digests(source_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(source_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source_dir.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module", params=sorted(OUTPUTS) or [None])
def loaded(request):
    if request.param is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    source_dir = OUTPUTS[request.param]
    before = staging_digests(source_dir)
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    results = staging.load_batch1(connection, source_dir, allow_third_party_extensions=True)
    yield source_dir, connection, results, before
    connection.close()


@requires_output
def test_every_batch_one_file_loads_the_row_count_digen_records(loaded):
    source_dir, _, results, _ = loaded
    expected = expected_counts(source_dir)
    assert {step.table for step in INGEST_STEPS} == set(expected)
    assert {result.step.table: result.rows for result in results} == expected


@requires_output
def test_staged_tables_hold_exactly_the_loaded_rows(loaded):
    source_dir, connection, _, _ = loaded
    expected = expected_counts(source_dir)
    for table, count in expected.items():
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (count,)


@requires_output
def test_xml_action_count_equals_the_python_parser_count(loaded):
    source_dir, connection, _, _ = loaded
    staged = connection.execute("SELECT count(*) FROM CustomerMgmtRaw").fetchone()[0]
    assert staged == python_action_count(source_dir / "Batch1" / "CustomerMgmt.xml")


@requires_output
def test_xml_values_equal_the_python_parser_values(loaded):
    source_dir, connection, _, _ = loaded
    staged = Counter(
        connection.execute(
            "SELECT actiontype, update_ts, customerid, accountid, brokerid, taxstatus, tier, dob FROM CustomerMgmtRaw"
        ).fetchall()
    )
    assert staged == python_actions(source_dir / "Batch1" / "CustomerMgmt.xml")


@requires_output
def test_every_raw_row_carries_batch_one(loaded):
    _, connection, results, _ = loaded
    for table in sorted({result.step.table for result in results}):
        columns = {row[0].lower() for row in connection.execute(f"DESCRIBE {table}").fetchall()}
        if "batchid" in columns:
            assert connection.execute(f"SELECT DISTINCT batchid FROM {table}").fetchall() == [(1,)], table


@requires_output
def test_staging_files_are_unchanged_by_the_load(loaded):
    source_dir, _, _, before = loaded
    assert (source_dir / "_datagen_manifest.json").is_file()
    assert staging_digests(source_dir) == before


@requires_output
def test_loading_without_third_party_extensions_refuses_on_real_output():
    source_dir = OUTPUTS[sorted(OUTPUTS)[0]]
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    with pytest.raises(ThirdPartyExtensionRequired, match="Allow third-party extensions"):
        staging.load_batch1(connection, source_dir, allow_third_party_extensions=False)
    assert connection.execute("SELECT count(*) FROM DimDate").fetchone() == (0,)
    connection.close()
