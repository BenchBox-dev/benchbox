from __future__ import annotations

import sys
from pathlib import Path

import pytest

from benchbox.utils.resource_limits import MemorySnapshot
from tests.uat.config import validate_config
from tests.uat.preflight_budget import (
    DATABASE_STATUS_MEASURED,
    DATABASE_STATUS_UNMEASURED,
    DEFAULT_TABLE_PATH,
    DiskBudget,
    DiskRootFreeSpace,
    assess_budget_coverage,
    check_disk_headroom,
    check_memory_headroom,
    estimate_peak_disk,
    format_budget_coverage,
    format_budget_verdict,
    format_disk_budget,
    format_disk_headroom_failure,
    format_memory_headroom_failure,
    largest_scale_cells,
    load_budget_table,
    predict_cell_disk_growth,
)

pytestmark = pytest.mark.medium


def test_estimate_peak_disk_counts_datagen_once_and_reports_unknown(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\t0.5\n"
        "sqlite\ttpch\t0.01\t1.5\t3.0\t0.25\n",
        encoding="utf-8",
    )
    cfg = validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb", "sqlite", "datafusion"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    budget = estimate_peak_disk(cfg, table_path=table)

    assert budget.cells == 3
    assert budget.est_steady_gib == pytest.approx(1.5 + 2.0 + 3.0)
    assert budget.est_peak_gib == pytest.approx(1.5 + 2.0 + 3.0 + 0.5 + 0.25)
    assert [cell.key for cell in budget.unknown_cells] == ["datafusion|tpch|0.01"]
    assert budget.database_by_platform_gib == (("duckdb", 2.0), ("sqlite", 3.0))
    assert budget.concurrent_database_gib == pytest.approx(5.0)
    assert budget.chunked_database_gib == pytest.approx(3.0)
    assert budget.platforms_total == 3
    assert budget.platforms_with_measured_database == 2


def test_predict_cell_disk_growth_discloses_lower_bound_and_reuses_datagen(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t1\t4.0\t9.0\tunmeasured\t0.5\n"
        "sqlite\ttpch\t1\t3.0\t8.0\tmeasured\t0.25\n",
        encoding="utf-8",
    )
    budget_table = load_budget_table(table)

    first = predict_cell_disk_growth("duckdb", "tpch", 1.0, table=budget_table)
    reused = predict_cell_disk_growth("duckdb", "tpch", 1.0, table=budget_table, datagen_already_present=True)
    measured = predict_cell_disk_growth("sqlite", "tpch", 1.0, table=budget_table)

    assert first is not None
    assert first.known_growth_gib == pytest.approx(4.5)
    assert first.database_gib is None
    assert first.is_lower_bound is True
    assert reused is not None
    assert reused.known_growth_gib == pytest.approx(0.5)
    assert measured is not None
    assert measured.known_growth_gib == pytest.approx(11.25)
    assert measured.is_lower_bound is False
    assert predict_cell_disk_growth("datafusion", "tpch", 1.0, table=budget_table) is None


def test_estimate_cells_models_measured_per_platform_database_only(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t1\t10.0\t20.0\tmeasured\t1.0\n"
        "duckdb\ttpcds\t1\t8.0\t12.0\tmeasured\t1.0\n"
        "sqlite\ttpch\t1\t11.0\t15.0\tmeasured\t1.0\n"
        "lakesail\ttpch\t1\t9.0\t0.0\tunmeasured\t1.0\n",
        encoding="utf-8",
    )
    cfg = validate_config(
        {
            "name": "per-platform-db",
            "platforms": {"include": ["duckdb", "sqlite", "lakesail", "datafusion"]},
            "benchmarks": {"include": ["tpch", "tpcds"]},
            "scales": {"rungs": [1.0]},
        }
    )

    budget = estimate_peak_disk(cfg, table_path=table)

    assert budget.database_by_platform_gib == (("duckdb", 32.0), ("sqlite", 15.0))
    assert budget.concurrent_database_gib == pytest.approx(47.0)
    assert budget.chunked_database_gib == pytest.approx(32.0)
    assert budget.platforms_total == 4
    assert budget.platforms_with_measured_database == 2
    assert "datafusion|tpch|1" in {cell.key for cell in budget.unknown_cells}
    assert "15 GiB" not in format_disk_budget(budget)


def test_format_disk_budget_includes_operator_fields(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\t0.5\n",
        encoding="utf-8",
    )
    cfg = validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    line = format_disk_budget(estimate_peak_disk(cfg, table_path=table))

    assert line.startswith("Disk budget estimate: ")
    assert "GiB peak" in line
    assert "cells=1" in line
    assert "unknown=0" in line
    assert "measured-db platforms=1/1" in line
    assert "database concurrent=2.00 GiB" in line
    assert "chunked_max=2.00 GiB" in line
    assert "basis=measured inventory rows" in line


def test_largest_scale_cells_selects_the_largest_rung_through_the_live_path(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "multi-rung-live-path-smoke",
            "platforms": {"include": ["duckdb", "sqlite"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 1, 100]},
        }
    )

    cells = largest_scale_cells(cfg)

    assert cells, "expected cells at the largest configured scale rung"
    assert {cell.scale for cell in cells} == {100.0}
    assert {cell.platform for cell in cells} == {"duckdb", "sqlite"}
    assert len(cells) == 2

    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\t0.5\n"
        "duckdb\ttpch\t1\t10.0\t20.0\t5.0\n"
        "duckdb\ttpch\t100\t100.0\t200.0\t50.0\n"
        "sqlite\ttpch\t100\t120.0\t300.0\t70.0\n",
        encoding="utf-8",
    )
    from tests.uat.preflight_budget import estimate_cells

    budget = estimate_cells(cells, table=load_budget_table(table))

    assert budget.cells == 2
    assert budget.est_steady_gib == pytest.approx(120.0 + 200.0 + 300.0)
    assert budget.est_peak_gib == pytest.approx(120.0 + 200.0 + 300.0 + 50.0 + 70.0)
    assert budget.unknown_cells == ()
    assert budget.database_by_platform_gib == (("duckdb", 200.0), ("sqlite", 300.0))
    assert budget.concurrent_database_gib == pytest.approx(500.0)
    assert budget.chunked_database_gib == pytest.approx(300.0)
    assert budget.platforms_total == 2
    assert budget.platforms_with_measured_database == 2


def test_disk_headroom_gate_reports_short_root(tmp_path: Path):
    budget = DiskBudget(cells=1, est_peak_gib=10.0, est_steady_gib=8.0, unknown_cells=())
    tmp_root = Path("/tmp")
    roots = (
        DiskRootFreeSpace("tmp", tmp_root, 4.0),
        DiskRootFreeSpace("output", tmp_path, 20.0),
    )

    check = check_disk_headroom(budget, roots, min_free_gib=5.0)

    assert check.required_gib == pytest.approx(10.0)
    assert len(check.shortfalls) == 1
    assert check.shortfalls[0].label == "tmp"
    assert f"tmp {tmp_root}: 4.0 GiB free < 10.0 GiB required" in format_disk_headroom_failure(check)
    named = format_disk_headroom_failure(
        check,
        DiskBudget(
            cells=2,
            est_peak_gib=10.0,
            est_steady_gib=8.0,
            unknown_cells=(),
            concurrent_database_gib=7.0,
            chunked_database_gib=4.0,
            platforms_total=2,
            platforms_with_measured_database=2,
        ),
    )
    assert "computed shortfall 6.0 GiB" in named
    assert "database concurrent=7.00 GiB" in named
    assert "chunked_max=4.00 GiB" in named
    assert "failing now rather than exhausting disk mid-sweep" in named


def test_disk_headroom_gate_enforces_configured_floor(tmp_path: Path):
    budget = DiskBudget(cells=1, est_peak_gib=1.0, est_steady_gib=1.0, unknown_cells=())
    roots = (DiskRootFreeSpace("output", tmp_path, 0.2),)

    check = check_disk_headroom(budget, roots, min_free_gib=5.0)

    assert check.required_gib == pytest.approx(5.0)
    assert len(check.shortfalls) == 1
    assert check.shortfalls[0].required_gib == pytest.approx(5.0)
    assert "0.2 GiB free < 5.0 GiB required" in format_disk_headroom_failure(check)


def test_disk_headroom_gate_uses_budget_when_it_exceeds_the_floor(tmp_path: Path):
    budget = DiskBudget(cells=1, est_peak_gib=40.0, est_steady_gib=35.0, unknown_cells=())
    roots = (DiskRootFreeSpace("output", tmp_path, 12.0),)

    check = check_disk_headroom(budget, roots, min_free_gib=5.0)

    assert check.required_gib == pytest.approx(40.0)
    assert len(check.shortfalls) == 1


def test_disk_headroom_gate_uses_chunked_envelope_when_platform_chunking(tmp_path: Path):
    budget = DiskBudget(
        cells=2,
        est_peak_gib=90.0,
        est_steady_gib=90.0,
        unknown_cells=(),
        concurrent_database_gib=70.0,
        chunked_database_gib=40.0,
        platforms_total=2,
        platforms_with_measured_database=2,
    )
    roots = (DiskRootFreeSpace("output", tmp_path, 65.0),)

    default_check = check_disk_headroom(budget, roots, min_free_gib=5.0)
    chunked_check = check_disk_headroom(budget, roots, min_free_gib=5.0, platform_chunking=True)

    assert default_check.required_gib == pytest.approx(90.0)
    assert len(default_check.shortfalls) == 1
    assert chunked_check.required_gib == pytest.approx(60.0)
    assert chunked_check.shortfalls == ()


def _coverage_table(tmp_path: Path) -> Path:
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\tmeasured\t0.5\n"
        "lakesail\ttpch\t0.01\t1.0\t0.0\tunmeasured\t0.5\n",
        encoding="utf-8",
    )
    return table


def _coverage_config():
    return validate_config(
        {
            "name": "coverage-smoke",
            "platforms": {"include": ["duckdb", "lakesail", "datafusion"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )


def test_load_budget_table_defaults_absent_status_column_to_measured(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\t0.5\n",
        encoding="utf-8",
    )

    rows = load_budget_table(table)

    assert rows[("duckdb", "tpch", 0.01)].database_status == DATABASE_STATUS_MEASURED
    assert rows[("duckdb", "tpch", 0.01)].database_measured is True


def test_assess_budget_coverage_separates_missing_rows_from_unmeasured_databases(tmp_path: Path):
    table = load_budget_table(_coverage_table(tmp_path))
    cells = largest_scale_cells(_coverage_config())

    coverage = assess_budget_coverage(cells, table=table)

    assert coverage.cells_total == 3
    assert coverage.cells_with_rows == 2
    assert coverage.cells_with_measured_database == 1
    assert coverage.platforms_total == 3
    assert coverage.measured_platforms == ("duckdb",)
    assert coverage.unmeasured_platforms == ("datafusion", "lakesail")
    assert coverage.is_lower_bound is True


def test_assess_budget_coverage_is_complete_when_every_cell_is_measured(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\tmeasured\t0.5\n",
        encoding="utf-8",
    )
    cfg = validate_config(
        {
            "name": "coverage-complete",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    coverage = assess_budget_coverage(largest_scale_cells(cfg), table=load_budget_table(table))

    assert coverage.is_lower_bound is False
    assert coverage.unmeasured_platforms == ()
    assert "COMPLETE" in format_budget_coverage(coverage)


def test_format_budget_coverage_partial_names_the_gap_and_never_claims_fit(tmp_path: Path):
    coverage = assess_budget_coverage(
        largest_scale_cells(_coverage_config()),
        table=load_budget_table(_coverage_table(tmp_path)),
    )

    message = format_budget_coverage(coverage)

    assert "LOWER BOUND" in message
    assert "1 of 3 platform(s)" in message
    assert "2 of 3 largest-scale cell(s) have any row" in message
    assert "1 of 3 have a measured loaded-database footprint" in message
    assert "datafusion" in message
    assert "lakesail" in message


def test_format_budget_verdict_partial_is_not_a_certification(tmp_path: Path):
    coverage = assess_budget_coverage(
        largest_scale_cells(_coverage_config()),
        table=load_budget_table(_coverage_table(tmp_path)),
    )
    check = check_disk_headroom(
        DiskBudget(cells=3, est_peak_gib=4.0, est_steady_gib=3.5, unknown_cells=()),
        (DiskRootFreeSpace("output", tmp_path, 100.0),),
        min_free_gib=5.0,
    )

    verdict = format_budget_verdict(check, coverage)

    assert "no shortfall detected" in verdict
    assert "lower-bound" in verdict
    assert "real demand may be higher" in verdict
    assert "fits" not in verdict


def test_format_budget_verdict_complete_may_state_the_requirement_fits(tmp_path: Path):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\tmeasured\t0.5\n",
        encoding="utf-8",
    )
    cfg = validate_config(
        {
            "name": "verdict-complete-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    coverage = assess_budget_coverage(largest_scale_cells(cfg), table=load_budget_table(table))
    check = check_disk_headroom(
        DiskBudget(cells=1, est_peak_gib=4.0, est_steady_gib=3.5, unknown_cells=()),
        (DiskRootFreeSpace("output", tmp_path, 100.0),),
        min_free_gib=5.0,
    )

    assert coverage.cells_total == 1
    assert coverage.is_lower_bound is False
    assert "fits" in format_budget_verdict(check, coverage)


def test_format_budget_verdict_zero_cells_never_claims_a_fit(tmp_path: Path):
    coverage = assess_budget_coverage((), table={})
    check = check_disk_headroom(
        DiskBudget(cells=0, est_peak_gib=0.0, est_steady_gib=0.0, unknown_cells=()),
        (DiskRootFreeSpace("output", tmp_path, 500.0),),
        min_free_gib=5.0,
    )

    assert coverage.cells_total == 0

    verdict = format_budget_verdict(check, coverage)

    assert "no cells enumerated" in verdict
    assert "fits" not in verdict
    assert "measured requirement" not in verdict


def test_checked_in_budget_table_declares_its_database_column_unmeasured():
    rows = load_budget_table(DEFAULT_TABLE_PATH)

    assert rows, "checked-in disk budget table is empty"
    zero_but_measured = [key for key, row in rows.items() if row.peak_database_gib == 0.0 and row.database_measured]
    assert zero_but_measured == [], (
        "rows claim a MEASURED zero loaded-database footprint: "
        f"{sorted(zero_but_measured)[:5]}. Mark them "
        f"{DATABASE_STATUS_UNMEASURED!r} until a real sweep measures them."
    )


def test_check_memory_headroom_shortfall_when_measured_below_floor():
    snapshot = MemorySnapshot(free_gib=1.0, swap_used_percent=50.0)
    check = check_memory_headroom(snapshot, min_free_gib=2.0)
    assert check.shortfall is True
    assert check.required_gib == pytest.approx(2.0)


def test_check_memory_headroom_no_shortfall_when_measured_above_floor():
    snapshot = MemorySnapshot(free_gib=4.0, swap_used_percent=0.0)
    check = check_memory_headroom(snapshot, min_free_gib=2.0)
    assert check.shortfall is False


def test_check_memory_headroom_unmeasured_is_never_a_shortfall():
    snapshot = MemorySnapshot(free_gib=None, swap_used_percent=None)
    check = check_memory_headroom(snapshot, min_free_gib=100.0)
    assert check.shortfall is False


def test_check_memory_headroom_zero_floor_never_shortfalls():
    snapshot = MemorySnapshot(free_gib=0.01, swap_used_percent=99.0)
    check = check_memory_headroom(snapshot, min_free_gib=0.0)
    assert check.shortfall is False


def test_format_memory_headroom_failure_includes_swap_note():
    check = check_memory_headroom(MemorySnapshot(free_gib=0.07, swap_used_percent=88.0), min_free_gib=2.0)
    message = format_memory_headroom_failure(check)
    assert "0.07 GiB free" in message
    assert "2.00 GiB required" in message
    assert "swap 88.0% used" in message


def test_format_memory_headroom_failure_omits_swap_note_when_unmeasured():
    check = check_memory_headroom(MemorySnapshot(free_gib=0.07, swap_used_percent=None), min_free_gib=2.0)
    assert "swap" not in format_memory_headroom_failure(check)
