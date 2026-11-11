from __future__ import annotations

import copy
import json
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.explorer_pipeline.transformer import (
    BundleTransformer,
    _compute_basis_availability,
    _parse_bundle,
    _query_display_ms,
    _query_timings,
)
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_warmup_plus_three_measurement_passes_yields_four_executions_and_measurement_median(
    tmp_path: Path,
) -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "Q1", "ms": 999.0, "iter": 0, "stream": 0, "run_type": "warmup", "status": "SUCCESS"},
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 200.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 300.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 400.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 410.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 420.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]

    timings = _query_timings(_parse_bundle(data))
    q1_timings = [t for t in timings if t.query_id == "Q1"]
    assert len(q1_timings) == 4

    display_ms, sample_count = _query_display_ms(q1_timings)
    assert display_ms == pytest.approx(200.0)
    assert sample_count == 3

    avail = _compute_basis_availability(timings)
    assert avail.has_warmup is True
    assert avail.warmup_status == "available"
    assert avail.measurement_pass_count == 3
    assert "warmup" in avail.available_bases
    assert "all_warm" in avail.available_bases
    assert "warm_pass_1" in avail.available_bases
    assert "warm_pass_2" in avail.available_bases
    assert "warm_pass_3" in avail.available_bases
    assert "warmup" not in avail.unavailable_bases

    data_dir = tmp_path / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "bundle.json").write_text(json.dumps(data), encoding="utf-8")

    out_dir = tmp_path / "out"
    ExplorerPipeline().run(data_dir, out_dir, bundle_url_prefix="/results/data/bundles")
    db_path = out_dir / "results.duckdb"

    with duckdb.connect(str(db_path), read_only=True) as con:
        exec_rows = con.execute(
            "SELECT duration_ms, run_type, iter FROM query_executions WHERE query_id = 'Q1' ORDER BY iter"
        ).fetchall()
        assert len(exec_rows) == 4
        assert exec_rows[0] == (999.0, "warmup", 0)
        assert exec_rows[1] == (100.0, "measurement", 1)
        assert exec_rows[2] == (200.0, "measurement", 2)
        assert exec_rows[3] == (300.0, "measurement", 3)

        dt_row = con.execute(
            "SELECT display_ms, sample_count FROM query_display_timings WHERE query_id = 'Q1'"
        ).fetchone()
        assert dt_row == (pytest.approx(200.0), 3)

        rba = con.execute(
            "SELECT has_warmup, measurement_pass_count, warmup_status, available_bases, varying_pass_queries "
            "FROM result_basis_availability"
        ).fetchone()
        assert rba[0] is True
        assert rba[1] == 3
        assert rba[2] == "available"
        assert "warmup" in rba[3].split(",")
        assert rba[4] is None


def test_bundle_with_no_warmup_ingests_cleanly_and_reports_warmup_unavailable(
    tmp_path: Path,
) -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 200.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 300.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 400.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 410.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q6", "ms": 420.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]

    timings = _query_timings(_parse_bundle(data))
    avail = _compute_basis_availability(timings)
    assert avail.has_warmup is False
    assert avail.warmup_status == "no_warmup_recorded"
    assert "warmup" in avail.unavailable_bases
    assert avail.unavailable_bases["warmup"] == "no_warmup_recorded"
    assert "warmup" not in avail.available_bases
    assert avail.measurement_pass_count == 3

    data_dir = tmp_path / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "bundle.json").write_text(json.dumps(data), encoding="utf-8")

    out_dir = tmp_path / "out"
    ExplorerPipeline().run(data_dir, out_dir, bundle_url_prefix="/results/data/bundles")
    db_path = out_dir / "results.duckdb"

    with duckdb.connect(str(db_path), read_only=True) as con:
        rba = con.execute(
            "SELECT has_warmup, measurement_pass_count, warmup_status, available_bases FROM result_basis_availability"
        ).fetchone()
        assert rba[0] is False
        assert rba[1] == 3
        assert rba[2] == "no_warmup_recorded"
        assert "warmup" not in rba[3].split(",")


def test_query_with_fewer_measurement_passes_reports_own_pass_count(tmp_path: Path) -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "Q1", "ms": 10.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 11.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 12.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q2", "ms": 20.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q2", "ms": 21.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q2", "ms": 22.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q3", "ms": 30.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]

    timings = _query_timings(_parse_bundle(data))
    avail = _compute_basis_availability(timings)
    assert avail.measurement_pass_count == 3
    assert avail.query_pass_counts["Q1"] == 3
    assert avail.query_pass_counts["Q2"] == 3
    assert avail.query_pass_counts["Q3"] == 1
    assert avail.varying_pass_queries == {"Q3": 1}

    data_dir = tmp_path / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "bundle.json").write_text(json.dumps(data), encoding="utf-8")

    out_dir = tmp_path / "out"
    ExplorerPipeline().run(data_dir, out_dir, bundle_url_prefix="/results/data/bundles")
    db_path = out_dir / "results.duckdb"

    with duckdb.connect(str(db_path), read_only=True) as con:
        rba = con.execute(
            "SELECT measurement_pass_count, varying_pass_queries FROM result_basis_availability"
        ).fetchone()
        assert rba[0] == 3
        assert json.loads(rba[1]) == {"Q3": 1}

        q_counts = dict(
            con.execute("SELECT query_id, sample_count FROM query_display_timings ORDER BY query_id").fetchall()
        )
        assert q_counts == {"Q1": 3, "Q2": 3, "Q3": 1}


def test_display_ms_does_not_move_when_warmup_rows_present() -> None:
    base_measurements = [
        {"id": "Q1", "ms": 10.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 25.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 40.0, "iter": 3, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]

    bundle_without_warmup = copy.deepcopy(MINIMAL_BUNDLE)
    bundle_without_warmup["queries"] = list(base_measurements)

    bundle_with_warmup = copy.deepcopy(MINIMAL_BUNDLE)
    bundle_with_warmup["queries"] = [
        {"id": "Q1", "ms": 9999.0, "iter": 0, "stream": 0, "run_type": "warmup", "status": "SUCCESS"},
        *base_measurements,
    ]

    timings_no_warm = _query_timings(_parse_bundle(bundle_without_warmup))
    timings_with_warm = _query_timings(_parse_bundle(bundle_with_warmup))

    d_no_warm, s_no_warm = _query_display_ms(timings_no_warm)
    d_with_warm, s_with_warm = _query_display_ms(timings_with_warm)

    assert d_no_warm == pytest.approx(25.0)
    assert d_with_warm == pytest.approx(25.0)
    assert s_no_warm == 3
    assert s_with_warm == 3


def test_metadata_and_summary_rows_are_filtered_out() -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "meta_1", "ms": 0.0, "run_type": "metadata", "status": "SKIPPED"},
        {"id": "summary_1", "ms": 0.0, "run_type": "summary", "status": "SKIPPED"},
        {"id": "Q1", "ms": 50.0, "run_type": "measurement", "status": "SUCCESS"},
    ]
    timings = _query_timings(_parse_bundle(data))
    assert len(timings) == 1
    assert timings[0].query_id == "Q1"


def test_failed_warmup_query_does_not_claim_warmup_available() -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "Q1", "ms": 0.0, "iter": 0, "stream": 0, "run_type": "warmup", "status": "FAILED"},
        {"id": "Q1", "ms": 100.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
    ]
    timings = _query_timings(_parse_bundle(data))
    avail = _compute_basis_availability(timings)
    assert avail.has_warmup is False
    assert avail.warmup_status == "no_warmup_recorded"
    assert "warmup" not in avail.available_bases
    assert "warmup" in avail.unavailable_bases


def test_completely_failed_measurement_query_reported_in_varying_pass_queries() -> None:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["queries"] = [
        {"id": "Q1", "ms": 10.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q1", "ms": 11.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q2", "ms": 20.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q2", "ms": 21.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "Q3", "ms": 0.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "FAILED"},
        {"id": "Q3", "ms": 0.0, "iter": 2, "stream": 0, "run_type": "measurement", "status": "FAILED"},
    ]
    timings = _query_timings(_parse_bundle(data))
    avail = _compute_basis_availability(timings)
    assert avail.measurement_pass_count == 2
    assert avail.query_pass_counts["Q3"] == 0
    assert avail.varying_pass_queries == {"Q3": 0}


