from __future__ import annotations

import pytest

from benchbox.core.tpcdi.appendix import load_appendix_sql
from benchbox.core.tpcdi.audit import load_audit_table
from benchbox.core.tpcdi.phases import run_historical_load, run_initialization
from tests.integration._tpcdi_digen_outputs import OUTPUTS_ENV, digen_outputs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.tpcdi,
]

duckdb = pytest.importorskip("duckdb")


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

OUTPUTS = digen_outputs()


@pytest.fixture(scope="module", params=sorted(OUTPUTS) or [None])
def warehouse(request):
    if request.param is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    source_dir = OUTPUTS[request.param]
    connection = duckdb.connect()
    run_initialization(connection)
    run_historical_load(connection, source_dir, allow_third_party_extensions=True)
    _, not_stored = load_audit_table(connection, source_dir)
    assert [row["attribute"] for row in not_stored] == VALUES_WIDER_THAN_DVALUE
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
