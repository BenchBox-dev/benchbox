from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from tests.uat.phases import execute, report
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium


def _cell(platform, benchmark, scale, status="passed", elapsed=1.0, result=None, throughput_check=None):
    return CellResult(
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        status=status,
        exit_code=0 if status == "passed" else 1,
        elapsed_s=elapsed,
        log_path=Path(f"/tmp/{platform}_{benchmark}_{scale}.log"),
        result_path=Path(result) if result else None,
        throughput_check=throughput_check,
    )


def test_render_row_matches_header_columns():
    cell = _cell("duckdb", "tpch", 0.01, status="passed", result="/tmp/r.json")
    row = report.render_row(cell, validator_status="clean")
    fields = row.split("\t")
    expected_cols = report.REPORT_HEADER.split("\t")
    assert len(fields) == len(expected_cols)


def test_report_header_appends_throughput_check_last():
    columns = report.REPORT_HEADER.split("\t")
    assert columns[-1] == "throughput_check"
    assert columns[:-1] == [
        "platform",
        "benchmark",
        "scale",
        "status",
        "terminal_state",
        "elapsed_s",
        "log_path",
        "result_path",
        "submit_terminal_state",
        "validator_status",
        "source_commit_sha",
        "source_dirty",
    ]


def test_render_row_includes_throughput_check_as_last_column():
    cell = _cell(
        "duckdb",
        "tpch",
        0.01,
        status="failed",
        result="/tmp/r.json",
        throughput_check="throughput stream count mismatch: requested 3, executed 1",
    )
    row = report.render_row(cell, validator_status="clean")
    assert row.split("\t")[-1] == "throughput stream count mismatch: requested 3, executed 1"


def test_render_row_throughput_check_defaults_to_empty_string():
    cell = _cell("duckdb", "tpch", 0.01, status="passed", result="/tmp/r.json")
    row = report.render_row(cell, validator_status="clean")
    assert row.split("\t")[-1] == ""


def test_write_report_counts(tmp_path: Path):
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed"),
        _cell("duckdb", "tpch", 0.1, status="failed"),
        _cell("duckdb", "tpch", 1.0, status="timed-out"),
    ]
    summary = report.write_report(
        cells,
        output_path=tmp_path / "out.tsv",
        compatibility_pruned_count=2,
        early_stop_pruned_count=1,
    )
    assert summary.rows == 3
    assert summary.candidate_count == 6
    assert summary.executed_count == 3
    assert summary.compatibility_pruned_count == 2
    assert summary.early_stop_pruned_count == 1
    assert summary.pass_count == 1
    assert summary.fail_count == 1
    assert summary.timeout_count == 1
    text = (tmp_path / "out.tsv").read_text()
    assert text.startswith(report.REPORT_HEADER)
    assert "candidates=6 executed=3 compatibility_pruned=2 early_stop_pruned=1" in text
    assert "# run_status=COMPLETED" in text


def test_write_report_records_terminal_state_and_source_footer(tmp_path: Path):
    source_info = type("SourceInfo", (), {"commit_sha": "abc123", "dirty": True})()
    cell = _cell("duckdb", "write_primitives", 0.01, status="failed")

    summary = report.write_report(
        [cell],
        output_path=tmp_path / "out.tsv",
        source_info=source_info,
        run_status="ABORTED",
        abort_phase="execute",
        abort_reason="free space\nfloor",
    )

    text = (tmp_path / "out.tsv").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "terminal_state" in lines[0].split("\t")
    assert "source_commit_sha" in lines[0].split("\t")
    assert "failed\tno_json_nonzero\t" in lines[1]
    assert "abc123\ttrue" in lines[1]
    assert "# run_status=ABORTED source_commit_sha=abc123 source_dirty=true" in text
    assert "abort_phase=execute" in text
    assert "abort_reason=free space floor" in text
    assert summary.exit_code() == 2


def test_terminal_state_failed_cell_with_result_path_is_not_submittable():
    cell = _cell("duckdb", "tpch", 0.01, status="failed", result="/tmp/r.json")
    assert cell.submit_terminal_state == "submittable"
    assert report.terminal_state(cell) == "failed:submittable"


def test_terminal_state_failed_cell_preserves_submit_refusal_reason():
    from dataclasses import replace

    cell = replace(
        _cell("duckdb", "tpch", 0.01, status="failed", result="/tmp/r.json"),
        submit_terminal_state="query_failure",
    )
    assert report.terminal_state(cell) == "failed:query_failure"


def test_terminal_state_timed_out_cell_with_result_path_stays_timeout():
    cell = _cell("duckdb", "tpch", 0.01, status="timed-out", result="/tmp/r.json")
    assert report.terminal_state(cell) == "timeout"


def test_terminal_state_failed_cell_without_result_path_is_unchanged():
    cell = _cell("duckdb", "tpch", 0.01, status="failed")
    assert report.terminal_state(cell) == "no_json_nonzero"


def test_write_report_row_uses_failed_prefix_for_failed_cell_with_result(tmp_path: Path):
    cell = _cell("duckdb", "tpch", 0.01, status="failed", result="/tmp/r.json")
    report.write_report([cell], output_path=tmp_path / "out.tsv")
    lines = (tmp_path / "out.tsv").read_text(encoding="utf-8").splitlines()
    assert "failed\tfailed:submittable\t" in lines[1]


def test_cross_scale_clean_counts_full_ladder():
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed"),
        _cell("duckdb", "tpch", 0.1, status="passed"),
        _cell("duckdb", "tpch", 1.0, status="passed"),
        _cell("sqlite", "tpch", 0.01, status="passed"),
    ]
    n = report.cross_scale_clean_pair_count(cells, rungs=[0.01, 0.1, 1.0])
    assert n == 1


def test_cross_scale_clean_excludes_failed_rung():
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed"),
        _cell("duckdb", "tpch", 0.1, status="failed"),
        _cell("duckdb", "tpch", 1.0, status="passed"),
    ]
    n = report.cross_scale_clean_pair_count(cells, rungs=[0.01, 0.1, 1.0])
    assert n == 0


def test_cross_scale_floor_breach_sets_exit_code(tmp_path: Path):
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed"),
    ]
    summary = report.write_report(
        cells,
        output_path=tmp_path / "out.tsv",
        rungs=[0.01, 0.1, 1.0],
        cross_scale_floor=1,
    )
    assert summary.cross_scale_clean_pairs == 0
    assert summary.cross_scale_floor_breached is True
    assert summary.exit_code() == 1


def test_cross_scale_floor_default_off(tmp_path: Path):
    cells = [_cell("duckdb", "tpch", 0.01, status="passed")]
    summary = report.write_report(cells, output_path=tmp_path / "out.tsv")
    assert summary.cross_scale_floor_breached is False
    assert summary.exit_code() == 0


def test_cross_scale_validator_status_blocks_clean_pair():
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed", result="/tmp/r1.json"),
        _cell("duckdb", "tpch", 0.1, status="passed", result="/tmp/r2.json"),
    ]
    n = report.cross_scale_clean_pair_count(
        cells,
        rungs=[0.01, 0.1],
        validator_status_by_path={Path("/tmp/r1.json"): "clean", Path("/tmp/r2.json"): "error"},
    )
    assert n == 0


def test_cross_scale_validator_status_normalizes_lookup_paths(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result_root = tmp_path / "benchmark_runs" / "results"
    cells = [
        _cell("duckdb", "tpch", 0.01, status="passed", result=result_root / "r1.json"),
        _cell("duckdb", "tpch", 0.1, status="passed", result=result_root / "r2.json"),
    ]

    n = report.cross_scale_clean_pair_count(
        cells,
        rungs=[0.01, 0.1],
        validator_status_by_path={
            Path("benchmark_runs/results/r1.json").resolve(): "clean",
            Path("benchmark_runs/results/r2.json").resolve(): "error",
        },
    )

    assert n == 0


_NATIVE_LOG = """2026-05-30T01:00:00 [report] native+dataframe stage complete
"""

_DOCKER_LOG_OK = """2026-05-30T02:00:00 [docker] platform=lakesail action=up status=ok project=benchbox-uat-gate-lakesail command=['docker'] message=started
2026-05-30T02:30:00 [docker] platform=lakesail action=down status=ok project=benchbox-uat-gate-lakesail command=['docker'] message=removed
2026-05-30T02:31:00 [docker] platform=clickhouse-server action=up status=ok project=benchbox-uat-gate-ch command=['docker'] message=started
"""

_DOCKER_LOG_EARLY = """2026-05-30T00:30:00 [docker] platform=cedardb action=up status=ok project=benchbox-uat-gate-cedardb command=['docker'] message=started
"""


def test_parse_docker_up_events_extracts_timestamp_and_platform():
    events = report.parse_docker_up_events(_DOCKER_LOG_OK)
    assert [p for _, p in events] == ["lakesail", "clickhouse-server"]
    assert all(isinstance(ts, _dt.datetime) for ts, _ in events)


def test_parse_docker_up_events_ignores_non_docker_and_garbage_lines():
    assert report.parse_docker_up_events(_NATIVE_LOG) == []
    assert report.parse_docker_up_events("not-a-timestamp [docker] action=up platform=x") == []


def test_release_gate_ordering_no_violation_when_docker_starts_after_native():
    boundary = _dt.datetime(2026, 5, 30, 1, 0, 0)
    violations = report.release_gate_ordering_violations([_DOCKER_LOG_OK], native_stage_completed_at=boundary)
    assert violations == []


def test_release_gate_ordering_flags_docker_up_before_native_completion():
    boundary = _dt.datetime(2026, 5, 30, 1, 0, 0)
    violations = report.release_gate_ordering_violations(
        [_DOCKER_LOG_EARLY, _DOCKER_LOG_OK], native_stage_completed_at=boundary
    )
    assert len(violations) == 1
    assert "cedardb" in violations[0]
    assert "at/before native+dataframe stage completion" in violations[0]


def test_release_gate_ordering_does_not_raise_against_offset_aware_boundary():
    aware_boundary = _dt.datetime(2026, 5, 30, 1, 0, 0).astimezone()

    no_violation = report.release_gate_ordering_violations([_DOCKER_LOG_OK], native_stage_completed_at=aware_boundary)
    assert no_violation == []

    violations = report.release_gate_ordering_violations(
        [_DOCKER_LOG_EARLY, _DOCKER_LOG_OK], native_stage_completed_at=aware_boundary
    )
    assert len(violations) == 1
    assert "cedardb" in violations[0]


def test_release_gate_ordering_uses_boundary_offset_for_naive_docker_timestamps():
    pdt = _dt.timezone(_dt.timedelta(hours=-7))
    boundary = _dt.datetime(2026, 5, 30, 1, 0, 0, tzinfo=pdt)

    violations = report.release_gate_ordering_violations([_DOCKER_LOG_OK], native_stage_completed_at=boundary)

    assert violations == []


def test_release_gate_ordering_respects_each_events_own_offset_across_dst():
    pdt = _dt.timezone(_dt.timedelta(hours=-7))
    boundary = _dt.datetime(2026, 11, 1, 1, 30, 0, tzinfo=pdt)
    docker_log = (
        "2026-11-01T01:15:00-08:00 [docker] platform=lakesail action=up status=ok "
        "project=benchbox-uat-gate-lakesail command=['docker'] message=started\n"
    )

    violations = report.release_gate_ordering_violations([docker_log], native_stage_completed_at=boundary)

    assert violations == []


def test_append_lifecycle_log_writes_offset_aware_timestamp(tmp_path: Path):
    execute.append_lifecycle_log(tmp_path, "[docker] platform=duckdb action=up status=ok")

    line = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8").strip()
    timestamp_token = line.split(" ", 1)[0]
    parsed = _dt.datetime.fromisoformat(timestamp_token)

    assert parsed.tzinfo is not None
