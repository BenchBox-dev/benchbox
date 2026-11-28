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

DIMENSION_STEP_TABLES = ("DimCustomer", "DimAccount", "Prospect")
AUDITED_TABLES = ("DimCustomer", "DimAccount", "Prospect")
BATCHES = (1, 2, 3)
INCREMENTAL_BATCHES = (2, 3)

WHOLE_RUN_TESTS = {
    "DimCustomer distinct keys",
    "DimCustomer EndDate",
    "DimCustomer Overlap",
    "DimCustomer End of Time",
    "DimCustomer consolidation",
    "DimCustomer IsCurrent",
    "DimCustomer Status",
    "DimCustomer Gender",
    "DimCustomer TaxID",
    "DimCustomer Phone1",
    "DimCustomer Phone2",
    "DimCustomer Phone3",
    "DimCustomer Email1",
    "DimCustomer Email2",
    "DimCustomer LocalTaxRate",
    "DimCustomer NationalTaxRate",
    "DimCustomer demographic fields",
    "DimCustomer batches",
    "DimAccount distinct keys",
    "DimAccount EndDate",
    "DimAccount Overlap",
    "DimAccount End of Time",
    "DimAccount consolidation",
    "DimAccount IsCurrent",
    "DimAccount Status",
    "DimAccount SK_CustomerID",
    "DimAccount SK_BrokerID",
    "DimAccount inactive customers",
    "DimAccount batches",
    "Prospect SK_UpdateDateID",
    "Prospect Country",
    "Prospect MarketingNameplate",
    "Prospect batches",
}

PER_BATCH_TESTS = {
    "DimCustomer row count",
    "DimCustomer EffectiveDate",
    "DimCustomer inactive customers",
    "DimCustomer age range alerts",
    "DimCustomer customer tier alerts",
    "DimAccount row count",
    "DimAccount EffectiveDate",
    "Prospect SK_RecordDateID",
    "Batch row count: DimAccount",
    "Batch row count: DimCustomer",
    "Batch row count: Prospect",
    "DImessages validation reports",
}


@dataclass
class IncrementalWarehouse:
    connection: Any
    staging: Path
    audit: AuditOutcome
    after_historical: dict[str, list[tuple]]
    after_update_1: dict[str, list[tuple]]


def snapshot(connection: Any) -> dict[str, list[tuple]]:
    return {
        "DimCustomer": connection.execute("SELECT * FROM DimCustomer ORDER BY SK_CustomerID").fetchall(),
        "DimAccount": connection.execute("SELECT * FROM DimAccount ORDER BY SK_AccountID").fetchall(),
    }


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
    after_historical = snapshot(connection)
    run_incremental_update(connection, staging, 2)
    after_update_1 = snapshot(connection)
    run_incremental_update(connection, staging, 3)
    audit = run_audit(connection, staging, latest_batch=3)
    yield IncrementalWarehouse(connection, staging, audit, after_historical, after_update_1)
    connection.close()


def audited(audit: AuditOutcome) -> list:
    return [
        result
        for result in audit.results
        if result.test.split()[0] in AUDITED_TABLES
        or result.test.removeprefix("Batch row count: ") in AUDITED_TABLES
        or result.test == "DImessages validation reports"
    ]


def test_every_audited_row_of_the_dimensions_reads_ok_for_all_three_batches(warehouse):
    rows = audited(warehouse.audit)
    assert {(row.test, row.batch): row.result for row in rows if row.result != "OK"} == {}
    names = {row.test for row in rows}
    assert names >= WHOLE_RUN_TESTS
    assert names >= PER_BATCH_TESTS


def test_per_batch_rows_exist_for_batches_two_and_three(warehouse):
    seen = {(row.test, row.batch) for row in audited(warehouse.audit)}
    for test in PER_BATCH_TESTS:
        assert {batch for name, batch in seen if name == test} >= set(INCREMENTAL_BATCHES), test


def changed_only_in_validity(before: list[tuple], after: list[tuple], columns: list[str], key: str) -> None:
    validity = {columns.index("IsCurrent"), columns.index("EndDate")}
    current = columns.index("IsCurrent")
    key_index = columns.index(key)
    later = {row[key_index]: row for row in after}
    for row in before:
        kept = later[row[key_index]]
        for index, (old, new) in enumerate(zip(row, kept, strict=True)):
            if index not in validity or not row[current]:
                assert old == new, (columns[index], row[key_index])


@pytest.mark.parametrize(
    ("table", "key"), [("DimCustomer", "SK_CustomerID"), ("DimAccount", "SK_AccountID")], ids=["customer", "account"]
)
def test_history_is_never_changed_in_place(warehouse, table, key):
    columns = column_names(warehouse.connection, table)
    final = warehouse.connection.execute(f"SELECT * FROM {table}").fetchall()
    changed_only_in_validity(warehouse.after_historical[table], final, columns, key)
    changed_only_in_validity(warehouse.after_update_1[table], final, columns, key)
    assert len(warehouse.after_update_1[table]) > len(warehouse.after_historical[table])
    assert len(final) > len(warehouse.after_update_1[table])


def test_exactly_one_current_record_per_natural_key(warehouse):
    for table, key in (("DimCustomer", "CustomerID"), ("DimAccount", "AccountID")):
        assert warehouse.connection.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*) FILTER (WHERE IsCurrent) <> 1)"
        ).fetchone() == (0,)


def test_prospect_status_messages_follow_the_audit_answer_key(warehouse):
    connection = warehouse.connection
    for batch in INCREMENTAL_BATCHES:
        messages = dict(
            connection.execute(
                "SELECT MessageText, CAST(MessageData AS BIGINT) FROM DImessages WHERE MessageType = 'Status' "
                "AND MessageSource = 'Prospect' AND BatchID = ?",
                [batch],
            ).fetchall()
        )
        audit = dict(
            connection.execute(
                "SELECT Attribute, Value FROM Audit WHERE DataSet = 'Prospect' AND BatchID = ? "
                "AND Attribute IN ('P_NEW', 'P_RECORDS')",
                [batch],
            ).fetchall()
        )
        assert messages["Source rows"] == audit["P_RECORDS"]
        assert messages["Inserted rows"] == audit["P_NEW"]
        assert messages["Updated rows"] >= 0


def test_every_prospect_row_carries_the_batch_that_inserted_or_last_changed_it(warehouse):
    assert warehouse.connection.execute(
        "SELECT count(*) FROM Prospect WHERE SK_UpdateDateID > SK_RecordDateID OR BatchID NOT IN (1, 2, 3)"
    ).fetchone() == (0,)
    assert warehouse.connection.execute("SELECT count(DISTINCT BatchID) FROM Prospect").fetchone() == (3,)


def test_prospect_is_customer_requires_an_active_current_customer(warehouse):
    assert warehouse.connection.execute(
        """
        SELECT count(*)
        FROM Prospect AS p
        WHERE p.IsCustomer <> EXISTS (
          SELECT 1
          FROM DimCustomer AS c
          JOIN StatusType AS st ON st.ST_ID = 'ACTV' AND st.ST_NAME = c.Status
          WHERE c.IsCurrent
            AND upper(c.LastName) = upper(p.LastName)
            AND upper(c.FirstName) = upper(p.FirstName)
            AND upper(c.AddressLine1) = upper(p.AddressLine1)
            AND upper(coalesce(c.AddressLine2, '')) = upper(coalesce(p.AddressLine2, ''))
            AND upper(c.PostalCode) = upper(p.PostalCode)
        )
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
    assert connection.execute("SELECT count(DISTINCT batchid) FROM CustomerRaw").fetchone() == (1,)
    assert connection.execute("SELECT max(batchid) FROM ProspectRaw").fetchone() == (2,)
    connection.close()
