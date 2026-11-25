from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchbox.core.tpcdi.appendix import load_appendix_sql
from benchbox.core.tpcdi.etl.historical import load_historical
from benchbox.core.tpcdi.etl.staging import load_batch1
from benchbox.core.tpcdi.schema import TPCDISchemaManager
from benchbox.core.tpcdi.sources.recount import recount_answer_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.tpcdi,
]

duckdb = pytest.importorskip("duckdb")

OUTPUTS_ENV = "BENCHBOX_TPCDI_DIGEN_OUTPUTS"

TABLES = (
    "DimCompany",
    "DimSecurity",
    "Financial",
    "DimCustomer",
    "DimAccount",
    "Prospect",
    "DimTrade",
    "FactCashBalances",
    "FactHoldings",
    "FactWatches",
    "FactMarketHistory",
)

NEEDS_ALL_BATCHES = {
    "DimAccount batches",
    "DimCustomer batches",
    "Prospect batches",
    "DimTrade batches",
    "DimTrade canceled trades",
    "FactCashBalances batches",
    "FactHoldings batches",
    "FactWatches batches",
    "FactMarketHistory batches",
}

VALUES_WIDER_THAN_DVALUE = ["ONE_YEAR_IN_MILLISECONDS"]

AUDIT_FILES = """
read_csv(
  $paths,
  header = true,
  columns = {'DataSet': 'VARCHAR', 'BatchID': 'VARCHAR', 'Date': 'VARCHAR', 'Attribute': 'VARCHAR',
             'Value': 'VARCHAR', 'DValue': 'VARCHAR'}
)
"""

LOAD_AUDIT = f"""
INSERT INTO Audit
SELECT
  trim(DataSet),
  CAST(nullif(trim(BatchID), '') AS INTEGER),
  CAST(nullif(trim(Date), '') AS DATE),
  trim(Attribute),
  CAST(nullif(trim(Value), '') AS BIGINT),
  TRY_CAST(nullif(trim(DValue), '') AS DECIMAL(15, 5))
FROM {AUDIT_FILES}
"""

DROPPED_DVALUES = f"""
SELECT trim(Attribute)
FROM {AUDIT_FILES}
WHERE nullif(trim(DValue), '') IS NOT NULL AND TRY_CAST(trim(DValue) AS DECIMAL(15, 5)) IS NULL
ORDER BY 1
"""


def digen_outputs() -> dict[int, Path]:
    outputs = {}
    for item in filter(None, (part.strip() for part in os.environ.get(OUTPUTS_ENV, "").split(","))):
        scale, _, path = item.partition("=")
        outputs[int(scale)] = Path(path)
    return outputs


OUTPUTS = digen_outputs()


def load_corrected_audit(connection, source_dir: Path) -> None:
    paths = {"paths": [str(path) for path in sorted(source_dir.rglob("*_audit.csv"))]}
    assert [row[0] for row in connection.execute(DROPPED_DVALUES, paths).fetchall()] == VALUES_WIDER_THAN_DVALUE
    connection.execute(LOAD_AUDIT, paths)
    for difference in recount_answer_key(source_dir):
        connection.execute(
            "UPDATE Audit SET Value = ? WHERE DataSet = ? AND BatchID = ? AND Attribute = ?",
            [difference.recount_value, difference.data_set, difference.batch, difference.attribute],
        )


@pytest.fixture(scope="module", params=sorted(OUTPUTS) or [None])
def warehouse(request):
    if request.param is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    source_dir = OUTPUTS[request.param]
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    validation = load_appendix_sql("validation").statements()
    for statement in validation:
        connection.execute(statement)
    load_batch1(connection, source_dir, allow_third_party_extensions=True)
    load_historical(connection)
    for statement in validation:
        connection.execute(statement)
    load_corrected_audit(connection, source_dir)
    audit_rows = []
    for statement in load_appendix_sql("audit").statements():
        audit_rows = connection.execute(statement).fetchall()
    yield connection, audit_rows
    connection.close()


def historical_results(audit_rows) -> dict[tuple[str, int | None], str]:
    return {
        (test, batch): result
        for test, batch, result, _ in audit_rows
        if test.split()[0] in TABLES and batch in (None, 1) and test not in NEEDS_ALL_BATCHES
    }


def test_every_audit_test_of_the_historical_tables_is_ok(warehouse):
    _, audit_rows = warehouse
    results = historical_results(audit_rows)
    assert len(results) == 103
    assert {key: result for key, result in results.items() if result != "OK"} == {}


def test_alert_counts_equal_the_corrected_answer_key(warehouse):
    connection, _ = warehouse
    counts = dict(
        connection.execute(
            "SELECT MessageText, count(*) FROM DImessages WHERE MessageType = 'Alert' AND BatchID = 1 "
            "AND MessageSource = 'DimCustomer' GROUP BY MessageText"
        ).fetchall()
    )
    audit = dict(
        connection.execute(
            "SELECT Attribute, Value FROM Audit WHERE DataSet = 'DimCustomer' AND BatchID = 1 "
            "AND Attribute IN ('C_TIER_INV', 'C_DOB_TO', 'C_DOB_TY')"
        ).fetchall()
    )
    assert counts["Invalid customer tier"] == audit["C_TIER_INV"]
    assert counts["DOB out of range"] == audit["C_DOB_TO"] + audit["C_DOB_TY"]


def test_prospect_status_message_counts_the_inserted_rows(warehouse):
    connection, _ = warehouse
    assert connection.execute(
        "SELECT MessageData FROM DImessages WHERE MessageType = 'Status' AND MessageSource = 'Prospect' "
        "AND MessageText = 'Inserted rows' AND BatchID = 1"
    ).fetchall() == [(str(connection.execute("SELECT count(*) FROM Prospect").fetchone()[0]),)]
