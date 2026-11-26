from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.tpcdi import audit, benchmark as benchmark_module, phases
from benchbox.core.tpcdi.audit import AuditOutcome, AuditResult, audit_test_specs, is_evaluable
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.etl import staging
from benchbox.core.tpcdi.etl.staging import ThirdPartyExtensionRequired
from benchbox.core.tpcdi.phases import POWER_WITHOUT_INITIALIZATION_MESSAGE, PhaseOrderError, PhaseRecord
from benchbox.core.tpcdi.platform_power import execute_tpcdi_power_test
from benchbox.core.tpcdi.schema import TPCDISchemaManager
from benchbox.core.tpcdi.sources.recount import Difference

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcdi]

duckdb = pytest.importorskip("duckdb")

TABLES_COUNTED_OVER_EVERY_BATCH = {
    "DImessages Phase complete records",
    "DImessages batches",
    "DimAccount batches",
    "DimCustomer batches",
    "DimTrade batches",
    "FactCashBalances batches",
    "FactHoldings batches",
    "FactMarketHistory batches",
    "FactWatches batches",
    "Prospect batches",
}

AUDIT_HEADER = "DataSet, BatchID, Date, Attribute, Value, DValue\n"


class Console:
    def print(self, *_: object) -> None:
        pass


class DuckDBTarget:
    allow_third_party_extensions = False

    def get_target_dialect(self) -> str:
        return "duckdb"


class XmlReadingTarget(DuckDBTarget):
    allow_third_party_extensions = True


def warehouse():
    connection = duckdb.connect()
    TPCDISchemaManager().create_schema(connection, "duckdb")
    return connection


def outcome_with(results: tuple[AuditResult, ...]) -> AuditOutcome:
    return AuditOutcome(
        results=results,
        corrections=(),
        dvalues_not_stored=(),
        appendix_sha256="0" * 64,
        pre_steps=(),
        latest_batch=1,
    )


def record(name: str, batch: int, second: int) -> PhaseRecord:
    return PhaseRecord(name, batch, datetime(2026, 11, 25, 12, 0, second), float(second))


def test_tests_that_fix_the_batch_count_are_found_in_the_shipped_audit_sql():
    specs = audit_test_specs()
    assert {name for name, spec in specs.items() if spec.counts_every_batch} == TABLES_COUNTED_OVER_EVERY_BATCH
    assert not specs["Audit table sources"].per_batch
    assert not specs["Audit table sources"].counts_every_batch


def test_whole_run_sums_name_the_audit_attributes_they_add_up():
    specs = audit_test_specs()
    assert ("DimTrade", "T_CanceledTrades") in specs["DimTrade canceled trades"].summed
    assert specs["DimTrade row count"].per_batch
    assert specs["DimTrade row count"].summed == ()


@pytest.mark.parametrize(
    ("batch", "later", "expected"),
    [(1, set(), True), (2, set(), False), (None, set(), False)],
)
def test_per_batch_rows_are_evaluable_up_to_the_latest_batch(batch, later, expected):
    spec = audit_test_specs()["DimTrade row count"]
    assert is_evaluable(spec, batch, 1, later) is expected


def test_a_whole_run_sum_is_evaluable_only_without_rows_for_later_batches():
    spec = audit_test_specs()["DimTrade canceled trades"]
    assert is_evaluable(spec, None, 1, set()) is True
    assert is_evaluable(spec, None, 1, {("DimTrade", "T_CanceledTrades")}) is False


def test_status_is_failed_only_for_an_evaluable_test_that_is_not_ok():
    passing = AuditResult("DimTrade row count", 1, "OK", "", True)
    excluded = AuditResult("DimTrade batches", None, "Not 3 batches", "", False)
    failing = AuditResult("DimTrade Type", None, "Bad value", "", True)
    assert outcome_with((passing, excluded)).status == "NOT_VALIDATED"
    assert outcome_with((passing, excluded, failing)).status == "FAILED"
    assert outcome_with((passing, excluded)).excluded_tests == ("DimTrade batches",)


def test_loading_the_audit_table_applies_and_lists_each_answer_key_correction(tmp_path, monkeypatch):
    (tmp_path / "Batch1").mkdir()
    (tmp_path / "Batch1" / "CustomerMgmt_audit.csv").write_text(
        AUDIT_HEADER + "DimCustomer,1,,C_TIER_INV,251,\nDimCustomer,1,,C_NEW,900,\n", encoding="utf-8"
    )
    (tmp_path / "Generator_audit.csv").write_text(
        AUDIT_HEADER + "Generator,,,ONE_YEAR_IN_MILLISECONDS,,31557600000\nGenerator,,,SCALE,,3\n",
        encoding="utf-8",
    )
    correction = Difference("DimCustomer", 1, "C_TIER_INV", 251, 252, "2.2.2.3")
    monkeypatch.setattr(audit, "recount_answer_key", lambda _staging: [correction])
    connection = warehouse()

    corrections, not_stored = audit.load_audit_table(connection, tmp_path)

    assert corrections == [correction.as_record()]
    assert not_stored == [
        {"data_set": "Generator", "batch": None, "attribute": "ONE_YEAR_IN_MILLISECONDS", "dvalue": "31557600000"}
    ]
    assert connection.execute("SELECT Attribute, Value, DValue FROM Audit ORDER BY Attribute").fetchall() == [
        ("C_NEW", 900, None),
        ("C_TIER_INV", 252, None),
        ("ONE_YEAR_IN_MILLISECONDS", None, None),
        ("SCALE", None, pytest.approx(3)),
    ]


def test_initialization_writes_the_batch_zero_completion_record():
    connection = duckdb.connect()

    initialization = phases.run_initialization(connection)

    assert initialization.batch_id == phases.INITIALIZATION_BATCH
    assert connection.execute(
        "SELECT count(*) FROM DImessages WHERE MessageType = 'PCR' AND BatchID = 0"
    ).fetchone() == (1,)


def test_staging_generated_at_another_scale_is_refused_before_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": 3})
    benchmark = TPCDIBenchmark(scale_factor=10, output_dir=tmp_path)
    connection = duckdb.connect()

    with pytest.raises(phases.StagingScaleError, match="scale factor 3; this run is scale factor 10"):
        benchmark.run_skipped_data_load(DuckDBTarget(), connection)

    assert benchmark.take_initialization_record() is None
    assert connection.execute("SELECT count(*) FROM information_schema.tables").fetchone() == (0,)


def test_a_phase_whose_batch_is_not_the_latest_is_refused():
    connection = warehouse()
    connection.execute("INSERT INTO DImessages VALUES (now(), 2, 'Phase', 'Batch two', 'Status', 'x')")
    with pytest.raises(PhaseOrderError, match="BatchID 1 as the latest"):
        phases.complete_phase(connection, phases.HISTORICAL_PHASE, phases.HISTORICAL_BATCH)


def test_power_without_initialization_is_refused_with_the_owner_message(tmp_path):
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    with pytest.raises(PhaseOrderError) as raised:
        execute_tpcdi_power_test(DuckDBTarget(), benchmark, duckdb.connect(), {}, console=Console())
    assert str(raised.value) == POWER_WITHOUT_INITIALIZATION_MESSAGE


def test_an_initialization_serves_one_power_phase_only(tmp_path, monkeypatch):
    monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": 3})
    monkeypatch.setattr(staging, "install_webbed", lambda _connection: None)
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    connection = duckdb.connect()

    assert benchmark.run_skipped_data_load(XmlReadingTarget(), connection) == {}

    assert benchmark.take_initialization_record() is not None
    assert benchmark.take_initialization_record() is None


def test_the_xml_reader_is_ready_before_initialization_starts(tmp_path, monkeypatch):
    events: list[str] = []
    initialize = benchmark_module.run_initialization
    monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": 3})
    monkeypatch.setattr(staging, "install_webbed", lambda _connection: events.append("install"))
    monkeypatch.setattr(
        benchmark_module,
        "run_initialization",
        lambda connection, platform: events.append("initialization") or initialize(connection, platform=platform),
    )

    TPCDIBenchmark(scale_factor=3, output_dir=tmp_path).run_skipped_data_load(XmlReadingTarget(), duckdb.connect())

    assert events == ["install", "initialization"]


def test_a_run_without_the_extension_option_is_refused_before_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": 3})
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    connection = duckdb.connect()

    with pytest.raises(ThirdPartyExtensionRequired):
        benchmark.run_skipped_data_load(DuckDBTarget(), connection)

    assert benchmark.take_initialization_record() is None
    assert connection.execute("SELECT count(*) FROM information_schema.tables").fetchone() == (0,)


def test_the_result_carries_the_audit_block_and_its_status(tmp_path):
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    failing = AuditResult("DimTrade Type", None, "Bad value", "", True)
    benchmark.record_power_phases(
        record(phases.INITIALIZATION_PHASE, 0, 1), (record(phases.HISTORICAL_PHASE, 1, 9),), outcome_with((failing,))
    )

    result = benchmark.create_enhanced_benchmark_result(
        "DuckDB", [], validation_status="PASSED", validation_details={"row_counts": "ok"}
    )

    block = result.validation_details["tpcdi_audit"]
    assert result.validation_status == "FAILED"
    assert result.validation_details["row_counts"] == "ok"
    assert [entry["batch_id"] for entry in block["phases"]] == [0, 1]
    assert block["tests"][0]["test"] == "DimTrade Type"
    assert publish_admission(result, "unofficial-research").allowed is False


def test_a_failed_run_stays_failed_and_the_audit_outcome_is_used_once(tmp_path):
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    passing = AuditResult("DimTrade row count", 1, "OK", "", True)
    benchmark.record_power_phases(
        record(phases.INITIALIZATION_PHASE, 0, 1), (record(phases.HISTORICAL_PHASE, 1, 9),), outcome_with((passing,))
    )

    failed = benchmark.create_enhanced_benchmark_result("DuckDB", [], validation_status="FAILED")
    later = benchmark.create_enhanced_benchmark_result("DuckDB", [], validation_status="FAILED")

    assert failed.validation_status == "FAILED"
    assert "tpcdi_audit" in failed.validation_details
    assert later.validation_status == "FAILED"
    assert "tpcdi_audit" not in (later.validation_details or {})


def test_the_staging_directory_defaults_to_the_digen_output_and_can_be_named(tmp_path):
    benchmark = TPCDIBenchmark(scale_factor=3, output_dir=tmp_path)
    assert benchmark.digen_staging_dir == tmp_path / "source" / "digen"
    benchmark.digen_staging_dir = tmp_path / "elsewhere"
    assert benchmark.digen_staging_dir == Path(tmp_path / "elsewhere")
