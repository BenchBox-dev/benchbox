from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tuning.coverage import (
    BASIC_CONSTRAINTS,
    DECISION_AUTHOR,
    DECISION_DONE,
    DECISION_WAIVED,
    MATRIX_COLUMNS,
    STATUS_RANK,
    TUNED_TEMPLATE,
    UNTUNED,
    VALID_DECISIONS,
    VALID_STATUSES,
    TuningCoverageRow,
    _coverage_spec,
    build_tuning_coverage_rows,
    classify_template,
    coverage_differences,
    parse_runtime_tuning_logs,
    read_tuning_coverage_tsv,
    runtime_mismatches,
    static_matrix_drift,
)
from tests.uat import _cli
from tests.uat.matrix import PLATFORM_GROUPS, load_benchmarks, resolve_benchmarks

pytestmark = pytest.mark.medium

MATRIX_PATH = Path(__file__).resolve().parent / "data" / "tuning_coverage.tsv"


def test_tuning_coverage_import_constants_match_yaml_spec():
    spec = _coverage_spec()
    statuses = {status["key"]: status for status in spec["statuses"]}
    decisions = {decision["key"]: decision for decision in spec["decisions"]}

    assert statuses["tuned_template"]["value"] == TUNED_TEMPLATE
    assert statuses["basic_constraints"]["value"] == BASIC_CONSTRAINTS
    assert statuses["untuned"]["value"] == UNTUNED
    assert {status["value"] for status in spec["statuses"]} == VALID_STATUSES
    assert {status["value"]: status["rank"] for status in spec["statuses"]} == STATUS_RANK
    assert decisions["author"]["value"] == DECISION_AUTHOR
    assert decisions["waived"]["value"] == DECISION_WAIVED
    assert decisions["done"]["value"] == DECISION_DONE
    assert {decision["value"] for decision in spec["decisions"]} == VALID_DECISIONS
    assert tuple(spec["matrix_columns"]) == MATRIX_COLUMNS


def test_tuning_coverage_matrix_is_checked_in_and_has_decisions():
    rows = read_tuning_coverage_tsv(MATRIX_PATH)
    assert rows
    assert all(row.decision in VALID_DECISIONS for row in rows)
    assert all(row.reason for row in rows)
    assert any(row.status == TUNED_TEMPLATE and row.decision == DECISION_DONE for row in rows)
    assert any(row.status == BASIC_CONSTRAINTS and row.decision == DECISION_AUTHOR for row in rows)


def test_checked_in_tuning_coverage_matches_generated_rows():
    recorded = read_tuning_coverage_tsv(MATRIX_PATH)
    current = build_tuning_coverage_rows(_uat_platforms(), _uat_benchmarks())
    differing = coverage_differences(recorded, current)
    assert not differing, (
        f"checked-in tuning coverage matrix differs in {len(differing)} rows "
        f"({', '.join(differing[:20])}): run "
        "`uv run -- python scripts/generate_tuning_coverage.py` to regenerate "
        "tests/uat/data/tuning_coverage.tsv, then review the diff"
    )


def _row(platform, benchmark, status, decision, reason="reason"):
    return TuningCoverageRow(
        platform=platform,
        benchmark=benchmark,
        status=status,
        decision=decision,
        reason=reason,
    )


def test_coverage_differences_flags_an_upgrade_as_drift():
    recorded = [_row("duckdb", "tpch", BASIC_CONSTRAINTS, DECISION_WAIVED)]
    current = [
        TuningCoverageRow(
            platform="duckdb",
            benchmark="tpch",
            status=TUNED_TEMPLATE,
            decision=DECISION_DONE,
            reason="benchmark-specific tuned template exists",
            template_path="examples/tunings/duckdb/tpch_tuned.yaml",
        )
    ]

    assert coverage_differences(recorded, current) == ["duckdb/tpch"]


def test_coverage_differences_flags_a_downgrade_as_drift():
    recorded = [_row("duckdb", "tpch", TUNED_TEMPLATE, DECISION_DONE)]
    current = [_row("duckdb", "tpch", BASIC_CONSTRAINTS, DECISION_WAIVED)]

    assert coverage_differences(recorded, current) == ["duckdb/tpch"]


def test_coverage_differences_flags_a_changed_decision_only():
    recorded = [_row("duckdb", "tpch", BASIC_CONSTRAINTS, DECISION_WAIVED)]
    current = [_row("duckdb", "tpch", BASIC_CONSTRAINTS, DECISION_AUTHOR)]

    assert coverage_differences(recorded, current) == ["duckdb/tpch"]


def test_coverage_differences_is_empty_for_identical_rows():
    recorded = [_row("duckdb", "tpch", TUNED_TEMPLATE, DECISION_DONE)]
    current = [_row("duckdb", "tpch", TUNED_TEMPLATE, DECISION_DONE)]

    assert coverage_differences(recorded, current) == []


def test_static_matrix_drift_flags_new_current_rows_missing_from_matrix():
    recorded = [
        TuningCoverageRow(
            platform="duckdb",
            benchmark="tpch",
            status=TUNED_TEMPLATE,
            decision=DECISION_DONE,
            reason="template exists",
        )
    ]
    current = [
        *recorded,
        TuningCoverageRow(
            platform="duckdb",
            benchmark="newbench",
            status=BASIC_CONSTRAINTS,
            decision=DECISION_WAIVED,
            reason="new coverage gap",
        ),
    ]

    assert static_matrix_drift(recorded, current) == ["new current row missing from matrix for duckdb/newbench"]


def test_runtime_log_parser_matches_matrix_rows(tmp_path: Path):
    log_path = tmp_path / "duckdb_tpch_0.01_20260505_010203.log"
    log_path.write_text("Tuning: auto-discovered template at examples/tunings/duckdb/tpch_tuned.yaml\n")

    rows = [
        row for row in read_tuning_coverage_tsv(MATRIX_PATH) if row.platform == "duckdb" and row.benchmark == "tpch"
    ]
    observations = parse_runtime_tuning_logs(tmp_path, platforms=_uat_platforms(), benchmarks=_uat_benchmarks())

    assert len(observations) == 1
    assert observations[0].status == TUNED_TEMPLATE
    assert runtime_mismatches(rows, observations) == []


def test_runtime_log_mismatch_reports_context(tmp_path: Path):
    log_path = tmp_path / "duckdb_tpch_0.01_20260505_010203.log"
    log_path.write_text("Tuning: using basic constraints (no optimized template available)\n")
    rows = [
        row for row in read_tuning_coverage_tsv(MATRIX_PATH) if row.platform == "duckdb" and row.benchmark == "tpch"
    ]

    observations = parse_runtime_tuning_logs(tmp_path, platforms=_uat_platforms(), benchmarks=_uat_benchmarks())
    mismatches = runtime_mismatches(rows, observations)

    assert len(mismatches) == 1
    assert "duckdb/tpch" in mismatches[0]
    assert str(log_path) in mismatches[0]


def test_verify_tuning_matrix_rejects_empty_observations(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = _cli.main(["verify-tuning-matrix", "--logs", str(tmp_path)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "No tuning observations parsed" in captured.err
    assert str(tmp_path) in captured.err


def _uat_platforms() -> list[str]:
    return list(PLATFORM_GROUPS["all"])


def _uat_benchmarks() -> list[str]:
    benchmarks = load_benchmarks()
    return resolve_benchmarks(groups=["all"], benchmarks=benchmarks)


@pytest.mark.parametrize("platform", ["clickhouse-local", "clickhouse-server", "clickhouse-cloud", "chdb"])
@pytest.mark.parametrize("benchmark_name", ["tpch", "ssb", "tpcds"])
def test_clickhouse_variants_share_the_curated_template_directory(platform, benchmark_name):
    status, template = classify_template(platform, benchmark_name)

    assert status == TUNED_TEMPLATE
    assert template == f"examples/tunings/clickhouse/{benchmark_name}_tuned.yaml"


def test_a_clickhouse_benchmark_without_a_template_stays_basic_constraints():
    assert classify_template("clickhouse-local", "clickbench") == (BASIC_CONSTRAINTS, "-")
