from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.query_normalizer import format_query_id
from benchbox.core.tpcdi import phases, platform_power, validity, visibility
from benchbox.core.tpcdi.audit import run_audit
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.sources.digen import StagingTamperedError
from benchbox.platforms.duckdb import DuckDBAdapter
from tests.integration._tpcdi_digen_outputs import OUTPUTS_ENV, digen_outputs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.tpcdi,
]

duckdb = pytest.importorskip("duckdb")

OUTPUTS = digen_outputs()
SCALES = sorted(OUTPUTS) or [None]

TRADE_FILE = Path("Batch1") / "Trade.txt"
CUSTOMER_FILE = Path("Batch2") / "Customer.txt"

TIMED_ROWS = ["TDI_HISTORICAL", "TDI_INCREMENTAL_1", "TDI_INCREMENTAL_2"]


def staging_for(scale: int | None) -> Path:
    if scale is None:
        pytest.skip(f"{OUTPUTS_ENV} lists no DIGen output")
    return OUTPUTS[scale]


def run_power(scale: int, staging: Path, database: Path) -> Any:
    benchmark = TPCDIBenchmark(scale_factor=scale, output_dir=database.parent / "tpcdi-output")
    benchmark.digen_staging_dir = staging
    adapter = DuckDBAdapter(database_path=str(database), allow_third_party_extensions=True)
    return adapter.run_benchmark(benchmark, benchmark_name="tpcdi", test_execution_type="power")


def audit_block(result: Any) -> dict[str, Any]:
    return result.validation_details["tpcdi_audit"]


def failed_tests(result: Any) -> set[str]:
    return {test["test"] for test in audit_block(result)["tests"] if test["evaluable"] and test["result"] != "OK"}


def copy_with_lines(staging: Path, destination: Path, relative_file: Path, lines_kept: slice) -> Path:
    for directory, _, files in os.walk(staging):
        relative = Path(directory).relative_to(staging)
        (destination / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            if relative / name != relative_file:
                (destination / relative / name).symlink_to(Path(directory) / name)
    lines = (staging / relative_file).read_text(encoding="utf-8").splitlines(keepends=True)
    (destination / relative_file).write_text("".join(lines[lines_kept]), encoding="utf-8")
    return destination


def table_counts(connection: Any) -> dict[str, int]:
    tables = [
        name
        for (name,) in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    ]
    return {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in tables}


@pytest.fixture(scope="module", params=SCALES)
def run(request, tmp_path_factory):
    staging = staging_for(request.param)
    return run_power(request.param, staging, tmp_path_factory.mktemp(f"sf{request.param}") / "tpcdi.duckdb")


def test_every_audit_test_is_evaluable_and_ok(run):
    assert failed_tests(run) == set()
    assert audit_block(run)["excluded_tests"] == []
    assert all(test["evaluable"] for test in audit_block(run)["tests"])
    assert run.validation_status == "NOT_VALIDATED"
    assert audit_block(run)["status"] == "NOT_VALIDATED"
    assert audit_block(run)["latest_batch"] == 3


def test_the_data_visibility_audit_rows_are_present_and_ok(run):
    rows = [test for test in audit_block(run)["tests"] if test["test"].startswith("Data visibility")]
    assert rows
    assert {test["result"] for test in rows} == {"OK"}


def test_the_run_is_valid_with_on_time_visibility_and_no_transaction_conflict(run):
    block = audit_block(run)["validity"]
    assert block["valid"] is True
    assert block["reasons"] == []
    evidence = block["visibility"]
    assert evidence["missing"] == []
    assert evidence["late"] == []
    assert all(gap["seconds"] <= 65.0 for gap in evidence["gaps"])
    executions = evidence["executions"]
    assert all(execution["error"] is None and execution["retried"] is False for execution in executions)
    assert [execution["trigger"] for execution in executions][:2] == ["phase_start", "boundary"]
    assert executions[-1]["trigger"] == "audit"
    assert [update["batch_id"] for update in block["incremental_updates"]] == [2, 3]
    assert not any(update["over_limit"] for update in block["incremental_updates"])


def test_four_phase_records_store_completion_timestamps_and_monotonic_times(run):
    records = audit_block(run)["phases"]
    assert [record["batch_id"] for record in records] == [0, 1, 2, 3]
    stamps = [record["completion_record_timestamp"] for record in records]
    assert stamps == sorted(stamps)
    monotonic = [record["completion_monotonic_seconds"] for record in records]
    assert monotonic == sorted(monotonic)
    rows = {row["query_id"]: row for row in run.query_results}
    assert list(rows) == [format_query_id(name) for name in TIMED_ROWS]
    for index, name in enumerate(TIMED_ROWS):
        assert rows[format_query_id(name)]["execution_time_seconds"] == pytest.approx(
            monotonic[index + 1] - monotonic[index]
        )
    assert run.execution_phases.power_test.duration_ms == int((monotonic[3] - monotonic[0]) * 1000)


@pytest.mark.parametrize("scale", SCALES)
def test_the_audit_changes_no_warehouse_table(scale):
    staging = staging_for(scale)
    connection = duckdb.connect()
    phases.run_initialization(connection)
    phases.run_historical_load(connection, staging, allow_third_party_extensions=True)
    phases.run_incremental_update(connection, staging, 2)
    phases.run_incremental_update(connection, staging, 3)
    before = table_counts(connection)
    run_audit(connection, staging, latest_batch=3)
    after = table_counts(connection)
    assert before.pop("Audit") == 0
    after.pop("Audit")
    assert after == before


@pytest.mark.parametrize("scale", SCALES)
def test_a_changed_staging_file_is_refused_by_the_manifest_check(scale, tmp_path):
    staging = copy_with_lines(staging_for(scale), tmp_path / "staging", TRADE_FILE, slice(1, None))
    with pytest.raises(StagingTamperedError, match="changed Batch1/Trade.txt"):
        run_power(scale, staging, tmp_path / "tpcdi.duckdb")


def invalidate_one_trade_type(connection: Any) -> None:
    connection.execute("UPDATE DimTrade SET Type = 'Invalid' WHERE TradeID = (SELECT min(TradeID) FROM DimTrade)")


def suppress_one_alert(batch: int, text: str) -> Callable[[Any], None]:
    def suppress(connection: Any) -> None:
        connection.execute(
            "DELETE FROM DImessages WHERE rowid = (SELECT min(rowid) FROM DImessages "
            f"WHERE MessageType = 'Alert' AND BatchID = {batch} AND MessageText = '{text}')"
        )

    return suppress


def add_one_tier_alert(batch: int) -> Callable[[Any], None]:
    def add(connection: Any) -> None:
        connection.execute(
            "INSERT INTO DImessages SELECT CURRENT_TIMESTAMP, "
            f"{batch}, 'DimCustomer', 'Invalid customer tier', 'Alert', 'C_ID = 0, C_TIER = '"
        )

    return add


def mutate_after_phase(monkeypatch, batch: int, mutate: Callable[[Any], None]) -> None:
    if batch == phases.HISTORICAL_BATCH:
        load = platform_power.run_historical_load

        def load_then_mutate(connection, *args, **kwargs):
            record = load(connection, *args, **kwargs)
            mutate(connection)
            return record

        monkeypatch.setattr(platform_power, "run_historical_load", load_then_mutate)
        return
    update = platform_power.run_incremental_update

    def update_then_mutate(connection, staging_dir, batch_id, **kwargs):
        record = update(connection, staging_dir, batch_id, **kwargs)
        if batch_id == batch:
            mutate(connection)
        return record

    monkeypatch.setattr(platform_power, "run_incremental_update", update_then_mutate)


AUDIT_CONTROLS: dict[str, tuple[int, Callable[[Any], None], str]] = {
    "invalid trade type": (1, invalidate_one_trade_type, "DimTrade Type"),
    "alert suppressed": (1, suppress_one_alert(1, "Invalid customer tier"), "DimCustomer customer tier alerts"),
    "batch 2 alert added": (2, add_one_tier_alert(2), "DimCustomer customer tier alerts"),
}


@pytest.mark.parametrize("control", sorted(AUDIT_CONTROLS))
@pytest.mark.parametrize("scale", SCALES)
def test_audit_negative_controls_fail_the_run_and_refuse_publication(scale, control, tmp_path, monkeypatch):
    staging = staging_for(scale)
    batch, mutate, failing_test = AUDIT_CONTROLS[control]
    mutate_after_phase(monkeypatch, batch, mutate)

    result = run_power(scale, staging, tmp_path / "tpcdi.duckdb")

    assert failing_test in failed_tests(result)
    assert result.validation_status == "FAILED"
    assert publish_admission(result, "unofficial-research").allowed is False


SOURCE_CONTROLS = {
    "source row removed": (TRADE_FILE, slice(1, None), "DimTrade row count"),
    "batch 2 customer file cut to one row": (CUSTOMER_FILE, slice(0, 1), "DimCustomer row count"),
}


@pytest.mark.parametrize("control", sorted(SOURCE_CONTROLS))
@pytest.mark.parametrize("scale", SCALES)
def test_source_negative_controls_fail_the_run_and_refuse_publication(scale, control, tmp_path, monkeypatch):
    relative_file, lines_kept, failing_test = SOURCE_CONTROLS[control]
    staging = copy_with_lines(staging_for(scale), tmp_path / "staging", relative_file, lines_kept)
    monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": scale})

    result = run_power(scale, staging, tmp_path / "tpcdi.duckdb")

    assert failing_test in failed_tests(result)
    assert result.validation_status == "FAILED"
    assert publish_admission(result, "unofficial-research").allowed is False


def skip_visibility_2(monkeypatch) -> None:
    monkeypatch.setattr(visibility, "VISIBILITY_INTERVAL_SECONDS", 3600.0)
    monkeypatch.setattr(visibility.VisibilityRunner, "boundary", lambda _self: None)


def make_every_gap_late(monkeypatch) -> None:
    monkeypatch.setattr(visibility, "ALLOWED_LATENESS_SECONDS", -59.99)


def shrink_the_incremental_limit(monkeypatch) -> None:
    monkeypatch.setattr(validity, "INCREMENTAL_LIMIT_SECONDS", 0.001)


VALIDITY_CONTROLS: dict[str, tuple[Callable[[Any], None], str]] = {
    "visibility 2 never runs": (skip_visibility_2, "no Visibility_2 ran before"),
    "every visibility gap is late": (make_every_gap_late, "visibility gap from"),
    "incremental limit exceeded": (shrink_the_incremental_limit, "over the 0.001 s limit"),
}


@pytest.mark.parametrize("control", sorted(VALIDITY_CONTROLS))
@pytest.mark.parametrize("scale", SCALES)
def test_validity_negative_controls_fail_the_run_with_their_reason(scale, control, tmp_path, monkeypatch):
    staging = staging_for(scale)
    arrange, reason = VALIDITY_CONTROLS[control]
    arrange(monkeypatch)

    result = run_power(scale, staging, tmp_path / "tpcdi.duckdb")

    block = audit_block(result)["validity"]
    assert failed_tests(result) == set()
    assert block["valid"] is False
    assert any(reason in text for text in block["reasons"])
    assert result.validation_status == "FAILED"
    assert audit_block(result)["status"] == "FAILED"
    assert publish_admission(result, "unofficial-research").allowed is False
