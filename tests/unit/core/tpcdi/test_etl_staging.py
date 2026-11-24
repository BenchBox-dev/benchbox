from __future__ import annotations

import importlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from benchbox._vendor import sqlglot
from benchbox.core.benchmark_registry import get_benchmark_metadata
from benchbox.core.tpcdi import compliance
from benchbox.core.tpcdi.etl import staging
from benchbox.core.tpcdi.etl.staging import INGEST_STEPS, STEPS_BY_NAME, ThirdPartyExtensionRequired

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

REPO_ROOT = Path(__file__).resolve().parents[4]
ETL_DIR = REPO_ROOT / "benchbox" / "core" / "tpcdi" / "etl"
PROVENANCE = json.loads((ETL_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
DEPARTURES = yaml.safe_load((ETL_DIR / "DEPARTURES.yaml").read_text(encoding="utf-8"))["departures"]

OWNER_XML_REFUSAL = (
    'TPC-DI on DuckDB reads CustomerMgmt.xml with the community extension "webbed". '
    "Allow third-party extensions (allow_third_party_extensions) to continue."
)

DEPARTURE_KINDS = {"changed", "kept", "left_out"}
DEPARTURE_STATUSES = {"implemented", "pending"}
DEPARTURE_FIELDS = {"id", "kind", "clause", "project_behaviour", "benchbox_behaviour", "files", "status", "approval"}


def etl_sql_files() -> list[str]:
    return sorted(path.relative_to(REPO_ROOT).as_posix() for path in ETL_DIR.rglob("*.sql"))


def test_every_etl_sql_file_is_listed_in_the_provenance_record():
    listed = [entry["path"] for entry in PROVENANCE["files"]]
    assert len(listed) == len(set(listed))
    assert sorted(listed) == etl_sql_files()


def test_provenance_entries_name_the_project_commit_and_a_derived_kind():
    assert PROVENANCE["base"]["commit"].startswith("8e0f944")
    assert (ETL_DIR / PROVENANCE["base"]["licence_file"]).is_file()
    for entry in PROVENANCE["files"]:
        assert entry["kind"] in {"adapted", "rewritten"}
        assert entry["source"].startswith("src/incremental_batches/")
        assert not entry["source"].startswith("src/tools/")
        assert not entry["source"].startswith("src/incremental_batches/audit_validation/")
        assert entry["duckdb_implementation"]


@pytest.mark.parametrize("path", etl_sql_files())
def test_etl_sql_contains_no_comment(path):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    tokens = sqlglot.Dialect.get_or_raise("duckdb").tokenize(text)
    assert not [token for token in tokens if token.comments]
    assert "--" not in re.sub(r"'[^']*'", "", text)
    assert "/*" not in re.sub(r"'[^']*'", "", text)


def test_licence_text_is_the_recorded_notice():
    shipped = (ETL_DIR / "LICENSE-databricks-tpc-di.txt").read_text(encoding="utf-8")
    recorded = (REPO_ROOT / "_sources" / "tpc-di" / "databricks-tpc-di" / "LICENSE").read_text(encoding="utf-8")
    assert shipped == recorded
    assert "Copyright (c) 2023-2026 Shannon Barrow and the databricks-tpc-di contributors" in shipped


def test_every_ingest_step_has_one_sql_file_and_the_reverse():
    assert {step.path.name for step in INGEST_STEPS} == {path.name for path in (ETL_DIR / "ingest").glob("*.sql")}
    assert len({step.name for step in INGEST_STEPS}) == len(INGEST_STEPS)
    assert len({step.table for step in INGEST_STEPS}) == len(INGEST_STEPS)
    assert len({step.parameter for step in INGEST_STEPS}) == len(INGEST_STEPS)


def test_ingest_sql_reads_the_first_batch_only():
    for step in INGEST_STEPS:
        text = step.path.read_text(encoding="utf-8")
        assert not re.search(r"Batch\s*[23]|Batch\*|Batch\[", text)
        assert f"${step.parameter}" in text
        assert staging.step_parameters("out", step)[step.parameter] == str(
            Path("out").resolve() / "Batch1" / step.source
        )


def test_staging_reads_use_decimal_not_floating_point():
    for step in INGEST_STEPS:
        text = step.path.read_text(encoding="utf-8").upper()
        assert "DOUBLE" not in text
        assert "FLOAT" not in text
        assert "REAL" not in text


def test_departure_rows_follow_the_catalogue_shape():
    ids = [row["id"] for row in DEPARTURES]
    assert len(ids) == len(set(ids))
    for row in DEPARTURES:
        assert set(row) == DEPARTURE_FIELDS
        assert row["kind"] in DEPARTURE_KINDS
        assert row["status"] in DEPARTURE_STATUSES
        assert row["approval"] == "pending"
        assert row["clause"].strip()
        assert row["project_behaviour"].strip()
        assert row["benchbox_behaviour"].strip()
        for path in row["files"]:
            assert path in etl_sql_files()
        assert (row["status"] == "implemented") == bool(row["files"])


def test_every_expected_difference_cites_a_catalogue_row():
    harness = importlib.import_module("scripts.tpcdi.compare_warehouses")
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, harness.DEFAULT_DEPARTURES)
    assert entries
    assert {entry.departure for entry in entries} <= {row["id"] for row in DEPARTURES}
    assert all(entry.departure for entry in entries)


def test_registry_scales_are_the_compliance_values():
    entry = get_benchmark_metadata("tpcdi")
    assert entry["default_scale"] == float(compliance.DIGEN_DEFAULT_SCALE)
    assert entry["scale_options"] == [float(scale) for scale in compliance.DIGEN_STANDARD_SCALES]
    assert entry["min_scale"] == float(compliance.DIGEN_MIN_SCALE)
    assert entry["support_status"] == "document_only"


def test_xml_refusal_message_is_the_owners_text():
    assert staging.xml_refusal_message() == OWNER_XML_REFUSAL


class Recorder:
    def __init__(self):
        self.statements = []

    def execute(self, statement, *args):
        self.statements.append(statement)
        raise AssertionError(statement)


def test_loading_without_third_party_extensions_refuses_before_touching_the_connection(tmp_path):
    connection = Recorder()
    with pytest.raises(ThirdPartyExtensionRequired) as refusal:
        staging.load_batch1(connection, tmp_path, allow_third_party_extensions=False)
    assert str(refusal.value) == OWNER_XML_REFUSAL
    assert connection.statements == []


def test_xml_step_is_the_only_one_that_needs_the_extension():
    assert [step.name for step in INGEST_STEPS if step.requires_xml] == ["customer_mgmt"]
    assert STEPS_BY_NAME["customer_mgmt"].path.read_text(encoding="utf-8").count("read_xml_objects") == 1


FIXTURE = {
    "Date.txt": "19500101|1950-01-01|January 1, 1950|1950|1950|19501|1950 Q1|19501|1950 January|19501|1950-W1|7|Sunday|1950|1950|19503|1950 Q3|true\n",
    "Time.txt": "000000|00:00:00|00|00|00|00:00|00|00:00:00|false|false\n",
    "Industry.txt": "AM|Advertising|SC\n",
    "StatusType.txt": "ACTV|Active\n",
    "TaxRate.txt": "US1|U.S. Income Tax Bracket for the poor|0.15000\n",
    "TradeType.txt": "TMB|Market-Buy|0|1\n",
    "BatchDate.txt": "2017-07-07\n",
    "HR.csv": "0,211,Ozkan,Douglas,,647,Branch,OFFICE1,(726) 088-3331\n1,413,Suer,Candice,,314,Branch2,OFFICE2,(344) 999-2652\n",
    "FINWIRE1967Q1": "19670401-063705CMP19670401xx\n19670401-152037FIN19670401yy\n",
    "Prospect.csv": "PEL0,PELLAND,Netti,,f,21847 olympia street,,T6b 1i1,Fairbanks,MA,United States,1-712,368776,,3,W,20,760,O,Brink's,,1058868\n",
    "Trade.txt": "0|2012-07-07 00:02:34|CMPT|TMB|0|AAAAAAAAAAAACQP|2939|9.57|0|3160|10.02|58.95|27.31|1611.19\n",
    "TradeHistory.txt": "0|2012-07-07 00:02:34|SBMT\n",
    "HoldingHistory.txt": "1|2|0|10\n",
    "WatchHistory.txt": "5|AAAAAAAAAAAACQP|2012-07-07 00:02:34|ACTV\n",
    "CashTransaction.txt": "1|2012-07-07 00:02:34|123.45|Cash\n",
    "DailyMarket.txt": "2012-07-07|AAAAAAAAAAAACQP|9.57|10.01|9.11|1000\n",
}


@pytest.fixture
def source_dir(tmp_path):
    batch = tmp_path / "Batch1"
    batch.mkdir()
    for name, text in FIXTURE.items():
        (batch / name).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.fixture
def warehouse():
    from benchbox.core.tpcdi.schema import TPCDISchemaManager

    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    yield connection
    connection.close()


def test_ingest_loads_every_file_without_the_xml_step(source_dir, warehouse):
    steps = [step for step in INGEST_STEPS if not step.requires_xml]
    results = staging.load_batch1(warehouse, source_dir, allow_third_party_extensions=False, steps=steps)
    assert {result.step.table: result.rows for result in results} == {
        "DimDate": 1,
        "DimTime": 1,
        "Industry": 1,
        "StatusType": 1,
        "TaxRate": 1,
        "TradeType": 1,
        "BatchDate": 1,
        "DimBroker": 1,
        "FinWire": 2,
        "ProspectRaw": 1,
        "TradeRaw": 1,
        "TradeHistoryRaw": 1,
        "HoldingHistoryRaw": 1,
        "WatchHistoryRaw": 1,
        "CashTransactionRaw": 1,
        "DailyMarketRaw": 1,
    }


def test_ingested_values_keep_the_specification_types(source_dir, warehouse):
    steps = [step for step in INGEST_STEPS if not step.requires_xml]
    staging.load_batch1(warehouse, source_dir, allow_third_party_extensions=False, steps=steps)
    assert warehouse.execute("SELECT tx_rate FROM TaxRate").fetchone() == (Decimal("0.15000"),)
    assert warehouse.execute("SELECT typeof(tx_rate) FROM TaxRate").fetchone() == ("DECIMAL(6,5)",)
    assert warehouse.execute("SELECT bidprice, tradeprice, fee, commission, tax, batchid FROM TradeRaw").fetchone() == (
        Decimal("9.57"),
        Decimal("10.02"),
        Decimal("58.95"),
        Decimal("27.31"),
        Decimal("1611.19"),
        1,
    )
    assert warehouse.execute(
        "SELECT typeof(ct_amt), typeof(dm_close) FROM CashTransactionRaw, DailyMarketRaw"
    ).fetchone() == (
        "DECIMAL(15,2)",
        "DECIMAL(8,2)",
    )
    assert warehouse.execute("SELECT batchid FROM BatchDate").fetchone() == (1,)
    assert warehouse.execute("SELECT employeeid FROM (SELECT sk_brokerid AS employeeid FROM DimBroker)").fetchall() == [
        (1,)
    ]
    assert (
        warehouse.execute("SELECT rectype, recdate, value FROM FinWire ORDER BY recdate, rectype").fetchall()[0][0]
        == "CMP"
    )
    assert warehouse.execute("SELECT numbercars, income FROM ProspectRaw").fetchone() == (None, 368776)


def test_rendered_sql_goes_through_the_translator_and_splits(source_dir):
    for step in INGEST_STEPS:
        statements = staging.render_step(step)
        assert len(statements) == 1
        assert f"${step.parameter}" in statements[0]
