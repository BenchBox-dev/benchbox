from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.query_normalizer import format_query_id
from benchbox.core.tpcdi import phases, platform_power
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

EXCLUDED_AFTER_BATCH_1 = [
    "DImessages Phase complete records",
    "DImessages batches",
    "DimAccount batches",
    "DimCustomer batches",
    "DimTrade batches",
    "DimTrade canceled trades",
    "DimTrade charge alerts",
    "DimTrade commission alerts",
    "FactCashBalances batches",
    "FactHoldings batches",
    "FactMarketHistory batches",
    "FactWatches batches",
    "Prospect batches",
]

TRADE_FILE = Path("Batch1") / "Trade.txt"


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


def copy_without_one_trade(staging: Path, destination: Path) -> Path:
    for directory, _, files in os.walk(staging):
        relative = Path(directory).relative_to(staging)
        (destination / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            if relative / name != TRADE_FILE:
                (destination / relative / name).symlink_to(Path(directory) / name)
    lines = (staging / TRADE_FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    (destination / TRADE_FILE).write_text("".join(lines[1:]), encoding="utf-8")
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


def test_every_evaluable_audit_test_is_ok(run):
    assert failed_tests(run) == set()
    assert run.validation_status == "NOT_VALIDATED"


def test_excluded_tests_are_the_whole_run_tests_that_need_later_batches(run):
    assert audit_block(run)["excluded_tests"] == EXCLUDED_AFTER_BATCH_1


def test_phase_records_store_completion_timestamps_and_monotonic_times(run):
    initialization, historical = audit_block(run)["phases"]
    assert (initialization["batch_id"], historical["batch_id"]) == (0, 1)
    assert initialization["completion_record_timestamp"] < historical["completion_record_timestamp"]
    elapsed = historical["completion_monotonic_seconds"] - initialization["completion_monotonic_seconds"]
    assert elapsed > 0
    (measured,) = run.query_results
    assert measured["query_id"] == format_query_id(phases.HISTORICAL_PHASE)
    assert measured["execution_time_seconds"] == pytest.approx(elapsed)
    assert run.execution_phases.power_test.duration_ms == int(elapsed * 1000)


@pytest.mark.parametrize("scale", SCALES)
def test_the_audit_changes_no_warehouse_table(scale):
    staging = staging_for(scale)
    connection = duckdb.connect()
    phases.run_initialization(connection)
    phases.run_historical_load(connection, staging, allow_third_party_extensions=True)
    before = table_counts(connection)
    run_audit(connection, staging, latest_batch=phases.HISTORICAL_BATCH)
    after = table_counts(connection)
    assert before.pop("Audit") == 0
    after.pop("Audit")
    assert after == before


@pytest.mark.parametrize("scale", SCALES)
def test_a_changed_staging_file_is_refused_by_the_manifest_check(scale, tmp_path):
    staging = copy_without_one_trade(staging_for(scale), tmp_path / "staging")
    with pytest.raises(StagingTamperedError, match="changed Batch1/Trade.txt"):
        run_power(scale, staging, tmp_path / "tpcdi.duckdb")


def invalidate_one_trade_type(connection: Any) -> None:
    connection.execute("UPDATE DimTrade SET Type = 'Invalid' WHERE TradeID = (SELECT min(TradeID) FROM DimTrade)")


def suppress_one_alert(connection: Any) -> None:
    connection.execute(
        "DELETE FROM DImessages WHERE rowid = (SELECT min(rowid) FROM DImessages "
        "WHERE MessageType = 'Alert' AND BatchID = 1 AND MessageText = 'Invalid customer tier')"
    )


CONTROLS: dict[str, tuple[Callable[[Any], None] | None, str]] = {
    "invalid trade type": (invalidate_one_trade_type, "DimTrade Type"),
    "alert suppressed": (suppress_one_alert, "DimCustomer customer tier alerts"),
    "source row removed": (None, "DimTrade row count"),
}


@pytest.mark.parametrize("control", sorted(CONTROLS))
@pytest.mark.parametrize("scale", SCALES)
def test_negative_controls_fail_the_run_and_refuse_publication(scale, control, tmp_path, monkeypatch):
    staging = staging_for(scale)
    mutate, failing_test = CONTROLS[control]
    if mutate is None:
        staging = copy_without_one_trade(staging, tmp_path / "staging")
        monkeypatch.setattr(phases, "verify_staging", lambda _output_dir: {"scale_factor": scale})
    else:
        load = platform_power.run_historical_load

        def load_then_mutate(connection, *args, **kwargs):
            record = load(connection, *args, **kwargs)
            mutate(connection)
            return record

        monkeypatch.setattr(platform_power, "run_historical_load", load_then_mutate)

    result = run_power(scale, staging, tmp_path / "tpcdi.duckdb")

    assert failing_test in failed_tests(result)
    assert result.validation_status == "FAILED"
    decision = publish_admission(result, "unofficial-research")
    assert decision.allowed is False
