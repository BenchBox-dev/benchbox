from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.uat import _cli as uat_cli, cells_io, docker_assets, orchestrator
from tests.uat.config import validate_config
from tests.uat.conftest import docker_verb, platform_reachability
from tests.uat.docker_path_helpers import compose_path_ends_with
from tests.uat.phases import execute as exec_phase
from tests.uat.phases.enumerate import CompatibilityPrunedCell
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium


def test_dry_run_records_zero_per_phase(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "smoke",
            "dry_run": True,
            "phases": ["preflight", "execute", "report"],
        }
    )
    result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)
    assert result.aborted_phase is None
    assert all(c == 0 for c in result.phase_exit_codes.values())
    assert set(result.phase_exit_codes) == {"preflight", "execute", "report"}


def test_preflight_abort_short_circuits(tmp_path: Path, capsys):
    cfg = validate_config(
        {
            "name": "smoke",
            "phases": ["preflight", "execute"],
        }
    )
    fake_result = type(
        "Stub",
        (),
        {
            "aborted": True,
            "abort_reason": "no disk",
            "warnings": ("disk budget gate has 1 unknown largest-scale cell(s); estimate may be low",),
            "disk_budget_summary": "Disk budget estimate: 12.34 GiB peak",
            "free_space_report": ("Free space: tmp 10.00 GiB (required 12.34 GiB) /tmp",),
            "exit_code": lambda self: 2,
        },
    )()
    with patch.object(
        orchestrator.preflight_phase,
        "run_preflight",
        return_value=fake_result,
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)
    assert result.aborted_phase == "preflight"
    assert "execute" not in result.phase_exit_codes
    assert result.exit_code() == 2
    captured = capsys.readouterr()
    assert "Disk budget estimate: 12.34 GiB peak" in captured.err
    assert "Free space: tmp 10.00 GiB (required 12.34 GiB) /tmp" in captured.err
    assert "[preflight warn] disk budget gate has 1 unknown largest-scale cell(s); estimate may be low" in captured.err


def test_stress_default_yaml_loads():
    p = Path(__file__).resolve().parent / "configs" / "stress-default.yaml"
    assert p.exists()
    from tests.uat.config import load_config

    cfg = load_config(p)
    assert cfg.name == "stress-default"
    assert "package" not in cfg.phases


def test_stress_docker_managed_yaml_loads():
    p = Path(__file__).resolve().parent / "configs" / "stress-docker-managed.yaml"
    assert p.exists()
    from tests.uat.config import load_config

    cfg = load_config(p)
    assert cfg.name == "stress-docker-managed"
    assert cfg.cleanup.docker_manage_platforms is True
    assert cfg.cleanup.docker_platform_switch == "volumes"
    assert cfg.preflight.docker_required is True


def test_enumerate_is_not_a_public_phase():
    with pytest.raises(ValueError, match="Unknown phase"):
        validate_config({"name": "smoke", "phases": ["enumerate", "execute"]})


def test_dry_run_passes_without_public_enumerate(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "smoke",
            "dry_run": True,
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)
    assert result.aborted_phase is None
    assert all(c == 0 for c in result.phase_exit_codes.values())
    assert "enumerate" not in result.phase_exit_codes


def test_report_phase_aborts_when_execute_outcome_missing(tmp_path: Path):
    cfg = validate_config({"name": "report-only", "phases": ["report"]})

    result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "report"
    assert "execute phase" in (result.abort_reason or "")
    assert result.exit_code() == 2
    partial_text = (tmp_path / "logs" / "matrix_summary.partial.tsv").read_text(encoding="utf-8")
    assert "# run_status=ABORTED" in partial_text
    assert "abort_phase=report" in partial_text


def test_dry_run_report_only_sweep_stays_exit_zero(tmp_path: Path):
    cfg = validate_config({"name": "report-only-dry-run", "dry_run": True, "phases": ["report"]})

    result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase is None
    assert result.exit_code() == 0
    assert result.phase_exit_codes == {"report": 0}


def test_package_phase_excludes_failed_cells_result_paths(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "smoke",
            "phases": ["execute", "package"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "package": {"submit_terminal_state": "local-stage"},
            "output": {"submissions_dir_template": str(tmp_path / "submissions" / "{name}")},
        }
    )
    passed_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "passed.log",
        result_path=tmp_path / "passed.json",
    )
    failed_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.1,
        status="failed",
        exit_code=1,
        elapsed_s=1.0,
        log_path=tmp_path / "failed.log",
        result_path=tmp_path / "failed.json",
    )
    execute_outcome = type(
        "ExecuteOutcome",
        (),
        {"results": (passed_cell, failed_cell), "aborted": False, "abort_reason": None, "exit_code": lambda self: 1},
    )()
    captured: dict[str, list[Path]] = {}

    def fake_package(config, *, result_paths, submissions_dir):
        captured["result_paths"] = list(result_paths)
        return type("PackageResult", (), {"aborted": False, "abort_reason": None, "exit_code": lambda self: 0})()

    with (
        patch.object(orchestrator.exec_phase, "run_execute", return_value=execute_outcome),
        patch("tests.uat.phases.package.run_package", side_effect=fake_package),
    ):
        orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert captured["result_paths"] == [passed_cell.result_path]
    assert failed_cell.result_path not in captured["result_paths"]


def test_write_cells_jsonl_persists_throughput_check(tmp_path: Path):
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="failed",
        exit_code=1,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
        throughput_check="throughput stream count mismatch: requested 3, executed 1",
    )
    source_info = orchestrator.RunSourceInfo(commit_sha="abc123", commit_short_sha="abc123", dirty=False)

    cells_io.write_cells_jsonl(tmp_path / "cells.jsonl", (cell,), source_info=source_info)

    lines = [json.loads(line) for line in (tmp_path / "cells.jsonl").read_text().splitlines()]
    assert lines[0]["throughput_check"] == "throughput stream count mismatch: requested 3, executed 1"


def test_orchestrator_uses_output_root_for_preflight_execute_and_cleanup(tmp_path: Path):
    root = tmp_path / "shared-runs"
    cfg = validate_config(
        {
            "name": "smoke",
            "phases": ["preflight", "execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "output": {"benchmark_runs_dir_template": str(root)},
        }
    )
    fake_preflight = type(
        "Preflight",
        (),
        {"aborted": False, "abort_reason": None, "warnings": (), "free_space_report": (), "exit_code": lambda self: 0},
    )()
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()
    captured: dict[str, Path | str] = {}

    def fake_run_preflight(**kwargs):
        captured["free_space_path"] = kwargs["free_space_path"]
        return fake_preflight

    def fake_run_execute(config, **kwargs):
        captured["benchmark_runs_dir"] = kwargs["benchmark_runs_dir"]
        captured["databases_root"] = kwargs["databases_root"]
        captured["cleanup_enabled"] = kwargs["cleanup_enabled"]
        captured["free_space_checks_enabled"] = kwargs["free_space_checks_enabled"]
        return fake_execute

    with (
        patch.object(orchestrator.preflight_phase, "run_preflight", side_effect=fake_run_preflight),
        patch.object(orchestrator.exec_phase, "run_execute", side_effect=fake_run_execute),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase is None
    assert captured["free_space_path"] == str(root)
    assert captured["benchmark_runs_dir"] == root
    assert captured["databases_root"] == root / "databases"
    assert captured["cleanup_enabled"] is True
    assert captured["free_space_checks_enabled"] is True


def test_orchestrator_cells_jsonl_marks_timed_out_cells(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "timeout-smoke",
            "phases": ["execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell_log = tmp_path / "cell.log"
    cell_log.write_text("# benchbox run\nstderr tail line\n", encoding="utf-8")
    timed_out_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="timed-out",
        exit_code=124,
        elapsed_s=1.0,
        log_path=cell_log,
        result_path=None,
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (timed_out_cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 1,
        },
    )()

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.phase_exit_codes["execute"] == 1
    cells = [json.loads(line) for line in (tmp_path / "logs" / "cells.jsonl").read_text().splitlines()]
    assert cells[0]["status"] == "timed-out"
    assert cells[0]["terminal_state"] == "timeout"
    assert cells[0]["timed_out"] is True
    assert cells[0]["exit_code"] == 124
    assert cells[0]["source_commit_sha"]
    assert isinstance(cells[0]["source_dirty"], bool)
    assert cells[0]["failure_tail"] == "stderr tail line"
    assert "# UAT_TERMINAL_STATE terminal_state=timeout" in cell_log.read_text(encoding="utf-8")


def test_cells_jsonl_terminal_marker_is_idempotent_after_timeout_marker(tmp_path: Path):
    cell_log = tmp_path / "cell.log"
    cell_log.write_text("# benchbox run\nstderr tail line\n# UAT_TIMEOUT timeout_s=1 exit_code=124\n", encoding="utf-8")
    timed_out_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="timed-out",
        exit_code=124,
        elapsed_s=1.0,
        log_path=cell_log,
        result_path=None,
    )
    source_info = orchestrator.RunSourceInfo(commit_sha="abc123", commit_short_sha="abc123", dirty=False)

    cells_io.write_cells_jsonl(tmp_path / "cells.jsonl", (timed_out_cell,), source_info=source_info)
    cells_io.write_cells_jsonl(tmp_path / "cells.jsonl", (timed_out_cell,), source_info=source_info)

    text = cell_log.read_text(encoding="utf-8")
    assert text.count("# UAT_TERMINAL_STATE terminal_state=timeout") == 1
    assert text.count("# UAT_FAILURE_TAIL_START") == 1


def test_orchestrator_writes_compatibility_pruned_jsonl_and_report_count(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "compat-smoke",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=None,
    )
    pruned = CompatibilityPrunedCell(
        platform="polars-df",
        benchmark="vector_search",
        scale=0.01,
        rule_id="uat.compat.dataframe.sql_only_benchmark",
        status="blocked",
        reason="not supported",
        evidence="test evidence",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "compatibility_pruned": (pruned,),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.phase_exit_codes == {"execute": 0, "report": 0}
    pruned_rows = [
        json.loads(line) for line in (tmp_path / "logs" / "compatibility_pruned.jsonl").read_text().splitlines()
    ]
    assert pruned_rows[0]["status"] == "compatibility-pruned"
    assert pruned_rows[0]["rule_id"] == "uat.compat.dataframe.sql_only_benchmark"
    assert "compatibility_pruned=1" in (tmp_path / "logs" / "matrix_summary.tsv").read_text()


def test_predictive_disk_floor_aborts_before_launching_oversized_known_cell(tmp_path: Path):
    row = orchestrator.preflight_budget.DiskBudgetRow(
        platform="duckdb",
        benchmark="tpch",
        scale_factor=0.01,
        peak_datagen_gib=2.0,
        peak_database_gib=0.0,
        transient_growth_gib=0.5,
        database_status=orchestrator.preflight_budget.DATABASE_STATUS_UNMEASURED,
    )
    attempted: list[tuple[str, str, float]] = []

    def base_runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        attempted.append((platform, benchmark, scale))
        raise AssertionError("predictive disk guard must run before the cell subprocess")

    runner = orchestrator._build_disk_floor_runner(
        base_runner,
        attempted_cells=[],
        watch_disk_floor=True,
        free_space_path=tmp_path,
        free_space_min_gib=3.0,
        budget_table={("duckdb", "tpch", 0.01): row},
        free_space_reader=lambda _path: 4.0,
        benchmark_runs_dir=None,
    )

    with pytest.raises(orchestrator.DiskFloorAbort, match="predictive disk check failed"):
        runner("duckdb", "tpch", 0.01, log_dir=tmp_path)

    assert attempted == []


def _datagen_budget_row(benchmark: str) -> orchestrator.preflight_budget.DiskBudgetRow:
    return orchestrator.preflight_budget.DiskBudgetRow(
        platform="duckdb",
        benchmark=benchmark,
        scale_factor=0.01,
        peak_datagen_gib=2.0,
        peak_database_gib=0.0,
        transient_growth_gib=0.0,
        database_status=orchestrator.preflight_budget.DATABASE_STATUS_UNMEASURED,
    )


def _passed_cell(platform: str, benchmark: str, scale: float, tmp_path: Path) -> CellResult:
    return CellResult(
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )


def _complete_datagen(runs_dir: Path, benchmark: str, scale: float) -> Path:
    import json

    from benchbox.utils.datagen_version import current_datagen_stamp

    path = orchestrator._cell_datagen_dir(runs_dir, benchmark, scale)
    assert path is not None
    path.mkdir(parents=True, exist_ok=True)
    (path / "lineitem.tbl").write_text("data")
    (path / "_datagen_manifest.json").write_text(
        json.dumps({"benchmark": benchmark, **current_datagen_stamp(benchmark)})
    )
    return path


def test_cell_datagen_dir_matches_the_cells_own_output_resolution(tmp_path: Path):
    tpch = orchestrator._cell_datagen_dir(tmp_path, "tpch", 0.01)
    alias = orchestrator._cell_datagen_dir(tmp_path, "read_primitives", 0.01)

    assert tpch == tmp_path / "datagen" / "tpch_sf001"
    assert alias == tmp_path / "datagen" / "read_primitives_sf001"
    assert tpch != alias
    assert orchestrator._cell_datagen_dir(None, "tpch", 0.01) is None


def test_partial_datagen_cache_does_not_drop_the_reserve(tmp_path: Path):
    partial = orchestrator._cell_datagen_dir(tmp_path, "tpch", 0.01)
    assert partial is not None
    partial.mkdir(parents=True)
    (partial / "lineitem.tbl.1").write_text("partial")

    assert orchestrator._datagen_cache_complete(partial) is False

    (partial / "_datagen_manifest.json").write_text("{}")
    assert orchestrator._datagen_cache_complete(partial) is False


def test_stamped_datagen_cache_marks_dataset_reusable(tmp_path: Path):
    import json

    from benchbox.utils.datagen_version import current_datagen_stamp

    complete = orchestrator._cell_datagen_dir(tmp_path, "tpch", 0.01)
    assert complete is not None
    complete.mkdir(parents=True)
    (complete / "_datagen_manifest.json").write_text(json.dumps({"benchmark": "tpch", **current_datagen_stamp("tpch")}))

    assert orchestrator._datagen_cache_complete(complete) is True


def test_failed_cell_does_not_mark_its_datagen_available(tmp_path: Path):
    cells = [
        CellResult(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            status="failed",
            exit_code=1,
            elapsed_s=1.0,
            log_path=tmp_path / "cell.log",
            result_path=None,
        ),
        _passed_cell("clickhouse", "tpch", 0.01, tmp_path),
    ]

    def base_runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        return cells.pop(0)

    free_readings = iter((3.5, 3.5, 2.5, 2.5))

    def free_space(_path) -> float:
        return next(free_readings, 2.5)

    runner = orchestrator._build_disk_floor_runner(
        base_runner,
        attempted_cells=[],
        watch_disk_floor=True,
        free_space_path=tmp_path,
        free_space_min_gib=1.0,
        budget_table={
            ("duckdb", "tpch", 0.01): _datagen_budget_row("tpch"),
            ("clickhouse", "tpch", 0.01): _datagen_budget_row("tpch"),
        },
        free_space_reader=free_space,
        benchmark_runs_dir=tmp_path,
    )

    runner("duckdb", "tpch", 0.01, log_dir=tmp_path)

    with pytest.raises(orchestrator.DiskFloorAbort, match="predictive disk check failed"):
        runner("clickhouse", "tpch", 0.01, log_dir=tmp_path)


def test_existing_datagen_cache_is_not_reserved_again(tmp_path: Path):
    _complete_datagen(tmp_path, "tpch", 0.01)

    def base_runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        return _passed_cell(platform, benchmark, scale, tmp_path)

    runner = orchestrator._build_disk_floor_runner(
        base_runner,
        attempted_cells=[],
        watch_disk_floor=True,
        free_space_path=tmp_path,
        free_space_min_gib=1.0,
        budget_table={("duckdb", "tpch", 0.01): _datagen_budget_row("tpch")},
        free_space_reader=lambda _path: 1.5,
        benchmark_runs_dir=tmp_path,
    )

    assert runner("duckdb", "tpch", 0.01, log_dir=tmp_path).status == "passed"


def test_alias_workload_keeps_its_own_datagen_reserve(tmp_path: Path):
    _complete_datagen(tmp_path, "tpch", 0.01)

    def base_runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        return _passed_cell(platform, benchmark, scale, tmp_path)

    runner = orchestrator._build_disk_floor_runner(
        base_runner,
        attempted_cells=[],
        watch_disk_floor=True,
        free_space_path=tmp_path,
        free_space_min_gib=1.0,
        budget_table={
            ("duckdb", "tpch", 0.01): _datagen_budget_row("tpch"),
            ("duckdb", "read_primitives", 0.01): _datagen_budget_row("read_primitives"),
        },
        free_space_reader=lambda _path: 1.5,
        benchmark_runs_dir=tmp_path,
    )

    assert runner("duckdb", "tpch", 0.01, log_dir=tmp_path).status == "passed"

    with pytest.raises(orchestrator.DiskFloorAbort, match="predictive disk check failed"):
        runner("duckdb", "read_primitives", 0.01, log_dir=tmp_path)


def test_datagen_probe_uses_the_sweeps_configured_root(tmp_path: Path):
    configured = tmp_path / "configured"
    unrelated = tmp_path / "unrelated"
    _complete_datagen(unrelated, "tpch", 0.01)

    def base_runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        return _passed_cell(platform, benchmark, scale, tmp_path)

    runner = orchestrator._build_disk_floor_runner(
        base_runner,
        attempted_cells=[],
        watch_disk_floor=True,
        free_space_path=tmp_path,
        free_space_min_gib=1.0,
        budget_table={("duckdb", "tpch", 0.01): _datagen_budget_row("tpch")},
        free_space_reader=lambda _path: 1.5,
        benchmark_runs_dir=configured,
    )

    with pytest.raises(orchestrator.DiskFloorAbort, match="predictive disk check failed"):
        runner("duckdb", "tpch", 0.01, log_dir=tmp_path)

    _complete_datagen(configured, "tpch", 0.01)
    assert runner("duckdb", "tpch", 0.01, log_dir=tmp_path).status == "passed"


def test_disk_floor_abort_emits_partial_artifacts(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "disk-floor-smoke",
            "phases": ["preflight", "execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_preflight = type(
        "Preflight",
        (),
        {
            "aborted": False,
            "abort_reason": None,
            "warnings": (),
            "disk_budget_summary": None,
            "exit_code": lambda self: 0,
        },
    )()

    with (
        patch.object(orchestrator.preflight_phase, "run_preflight", return_value=fake_preflight),
        patch.object(orchestrator.exec_phase, "run_cell", return_value=cell),
        patch.object(orchestrator.exec_phase, "default_free_space_reader", return_value=100.0),
        patch.object(orchestrator.preflight_budget, "free_space_gib", return_value=1.0),
        patch.object(orchestrator.preflight_budget, "load_budget_table", return_value={}),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "execute"
    cells = [json.loads(line) for line in (tmp_path / "logs" / "cells.jsonl").read_text().splitlines()]
    assert cells[0]["platform"] == "duckdb"
    assert cells[0]["source_commit_sha"]
    partial_report = tmp_path / "logs" / "matrix_summary.partial.tsv"
    assert partial_report.exists()
    partial_text = partial_report.read_text(encoding="utf-8")
    assert "# run_status=ABORTED" in partial_text
    assert "abort_phase=execute" in partial_text


def test_disk_floor_abort_threads_real_compatibility_pruned_without_reenumerating(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "disk-floor-compat-smoke",
            "phases": ["execute"],
            "platforms": {"include": ["polars-df"]},
            "benchmarks": {"include": ["vector_search", "tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="polars-df",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    reenumerate_calls: list[object] = []
    real_compat_for_config = orchestrator._compatibility_pruned_for_config

    def spy_compat_for_config(config):
        reenumerate_calls.append(config)
        return real_compat_for_config(config)

    with (
        patch.object(orchestrator.exec_phase, "run_cell", return_value=cell),
        patch.object(orchestrator.exec_phase, "default_free_space_reader", return_value=100.0),
        patch.object(orchestrator.preflight_budget, "free_space_gib", return_value=1.0),
        patch.object(orchestrator, "_compatibility_pruned_for_config", side_effect=spy_compat_for_config),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "execute"
    assert reenumerate_calls == []
    assert result.execute_outcome is not None
    assert result.execute_outcome.abort_kind == "disk_floor"

    pruned_rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "compatibility_pruned.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert pruned_rows[0]["benchmark"] == "vector_search"
    assert pruned_rows[0]["rule_id"] == "uat.compat.dataframe.sql_only_benchmark"


def test_execute_only_config_still_gets_disk_floor_watch(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "execute-only-disk-smoke",
            "phases": ["execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )

    with (
        patch.object(orchestrator.exec_phase, "run_cell", return_value=cell),
        patch.object(orchestrator.exec_phase, "default_free_space_reader", return_value=100.0),
        patch.object(orchestrator.preflight_budget, "free_space_gib", return_value=1.0),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "execute"
    assert "free space" in (result.abort_reason or "")
    partial_report = tmp_path / "logs" / "matrix_summary.partial.tsv"
    assert partial_report.exists()
    assert "# run_status=ABORTED" in partial_report.read_text(encoding="utf-8")


def test_zero_floor_warns_loudly_and_flags_accounting_sidecar(tmp_path: Path, capsys):
    cfg = validate_config(
        {
            "name": "zero-floor-smoke",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_space_min_gib": 0},
        }
    )
    assert cfg.disk_gate_enabled is False
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase is None
    assert "[disk-gate] DISABLED by config" in capsys.readouterr().err
    accounting = json.loads((tmp_path / "logs" / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert accounting["disk_gate_disabled"] is True
    assert accounting["memory_gate_disabled"] is False


def test_zero_memory_floor_warns_loudly_and_flags_accounting_sidecar(tmp_path: Path, capsys):
    cfg = validate_config(
        {
            "name": "zero-memory-floor-smoke",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_memory_min_gib": 0},
        }
    )
    assert cfg.memory_gate_enabled is False
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase is None
    stderr = capsys.readouterr().err
    assert "[memory-gate] DISABLED by config" in stderr
    assert "[disk-gate] DISABLED by config" not in stderr
    accounting = json.loads((tmp_path / "logs" / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert accounting["memory_gate_disabled"] is True
    assert accounting["disk_gate_disabled"] is False


def test_memory_floor_abort_records_memory_floor_abort_kind_in_gate_summary(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "memory-floor-abort",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    aborted_outcome = exec_phase.ExecuteOutcome(
        phase="execute",
        results=(),
        pruned=(),
        skipped_unreachable=(),
        startup_failed=(),
        compatibility_pruned=(),
        aborted=True,
        abort_reason="memory headroom gate failed: 0.07 GiB free < 2.00 GiB required",
        abort_kind="memory_floor",
    )

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=aborted_outcome):
        orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    payload = json.loads((tmp_path / "logs" / "uat_gate_summary.json").read_text(encoding="utf-8"))
    assert payload["abort_kind"] == "memory_floor"
    assert payload["abort_phase"] == "execute"
    assert payload["verdict"] == "red"


def test_gate_summary_abort_kind_is_none_on_a_clean_sweep(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "clean-abort-kind",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    payload = json.loads((tmp_path / "logs" / "uat_gate_summary.json").read_text(encoding="utf-8"))
    assert payload["abort_kind"] is None


def test_execute_free_space_abort_emits_partial_artifacts(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "free-space-smoke",
            "phases": ["preflight", "execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_preflight = type(
        "Preflight",
        (),
        {
            "aborted": False,
            "abort_reason": None,
            "warnings": (),
            "disk_budget_summary": None,
            "exit_code": lambda self: 0,
        },
    )()
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "aborted": True,
            "abort_reason": "free space 1.0 GiB < cutoff 5.0 GiB after Docker teardown",
        },
    )()

    with (
        patch.object(orchestrator.preflight_phase, "run_preflight", return_value=fake_preflight),
        patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "execute"
    cells = [json.loads(line) for line in (tmp_path / "logs" / "cells.jsonl").read_text().splitlines()]
    assert cells[0]["platform"] == "duckdb"
    assert (tmp_path / "logs" / "matrix_summary.partial.tsv").exists()


def test_two_sweeps_in_one_process_land_in_distinct_log_dirs(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "collision-smoke",
            "dry_run": True,
            "output": {"logs_dir_template": str(tmp_path / "uat_{date}_{time}")},
        }
    )
    fixed_times = iter(
        [
            _dt.datetime(2026, 5, 5, 9, 0, 0),
            _dt.datetime(2026, 5, 5, 9, 0, 0, 500000),
            _dt.datetime(2026, 5, 5, 9, 0, 1),
            _dt.datetime(2026, 5, 5, 9, 0, 1, 500000),
        ]
    )
    with patch.object(orchestrator, "_dt") as mock_dt:
        mock_dt.datetime.now.side_effect = lambda: next(fixed_times)
        result1 = orchestrator.run_sweep(cfg)
        result2 = orchestrator.run_sweep(cfg)

    assert result1.log_dir != result2.log_dir


def _source_info() -> orchestrator.RunSourceInfo:
    return orchestrator.RunSourceInfo(commit_sha="deadbeef", commit_short_sha="deadbee", dirty=False)


def test_sweep_records_container_engine_identity_and_sidecar_field(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "engine-identity",
            "phases": ["execute"],
            "platforms": {"include": []},
        }
    )
    with patch.object(docker_assets, "container_engine_identity", return_value=("mocker", "mocker 0.5.4")):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.aborted_phase is None
    lifecycle_log = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[engine] resolved_container_cli=mocker version=mocker 0.5.4" in lifecycle_log

    sidecar = json.loads((tmp_path / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert sidecar["container_engine"] == "mocker"


def test_sweep_records_engine_resolution_failure_without_aborting(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "engine-identity-missing",
            "phases": ["execute"],
            "platforms": {"include": []},
        }
    )
    with patch.object(
        docker_assets,
        "container_engine_identity",
        side_effect=docker_assets.DockerAssetError("no engine on PATH"),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.aborted_phase is None
    lifecycle_log = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[engine] resolution failed: no engine on PATH" in lifecycle_log
    sidecar = json.loads((tmp_path / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert sidecar["container_engine"] is None


def test_write_cells_jsonl_persists_skipped_unreachable_sidecar(tmp_path: Path):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(
        cells_jsonl,
        (),
        source_info=_source_info(),
        skipped_unreachable_count=3,
    )
    sidecar = cells_jsonl.with_name("cells.jsonl.accounting.json")
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["skipped_unreachable_count"] == 3


def test_write_cells_jsonl_writes_cell_stream_before_accounting_sidecar(tmp_path: Path):
    cells_jsonl = tmp_path / "cells.jsonl"
    written_paths: list[Path] = []
    real_atomic_write_text = cells_io.atomic_write_text

    def recording_atomic_write_text(path: Path, text: str) -> None:
        written_paths.append(path)
        real_atomic_write_text(path, text)

    with patch.object(cells_io, "atomic_write_text", side_effect=recording_atomic_write_text):
        cells_io.write_cells_jsonl(
            cells_jsonl,
            (),
            source_info=_source_info(),
            skipped_unreachable_count=1,
        )

    sidecar = cells_jsonl.with_name("cells.jsonl.accounting.json")
    finalized = cells_jsonl.with_name("cells.jsonl.finalized")
    assert written_paths == [cells_jsonl, sidecar, finalized]


def test_abort_artifacts_thread_skipped_unreachable_when_outcome_missing(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "abort-accounting",
            "phases": ["execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    orchestrator._emit_abort_artifacts(
        config=cfg,
        log_dir=log_dir,
        attempted=(),
        execute_outcome=None,
        source_info=_source_info(),
        aborted_phase="execute",
        abort_reason="free space 1.0 GiB < cutoff 5.0 GiB",
        skipped_unreachable_count=2,
    )
    partial = log_dir / "matrix_summary.partial.tsv"
    text = partial.read_text(encoding="utf-8")
    assert "unreachable=2" in text
    assert "# UNREACHABLE_CELLS=2 release_gate_attention=required" in text
    sidecar = (log_dir / "cells.jsonl").with_name("cells.jsonl.accounting.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["skipped_unreachable_count"] == 2


def test_disk_floor_abort_carries_skipped_unreachable_count():
    cfg = validate_config(
        {
            "name": "disk-floor-annotate",
            "platforms": {"include": ["duckdb", "clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    def runner(platform, benchmark, scale, **kwargs):
        raise orchestrator.DiskFloorAbort("free space 1.0 GiB < cutoff 5.0 GiB")

    with (
        patch.object(exec_phase, "platform_is_reachable", side_effect=lambda p, **_: p != "duckdb"),
        patch.object(exec_phase, "probe_platform_reachability", side_effect=lambda p, **_: p != "duckdb"),
    ):
        with pytest.raises(orchestrator.DiskFloorAbort) as excinfo:
            exec_phase.run_execute(
                cfg,
                log_dir=Path("/tmp"),
                databases_root=Path("/tmp/databases"),
                runner=runner,
            )
    assert getattr(excinfo.value, "skipped_unreachable_count", None) == 1


def _startup_fail_clickhouse_docker(argv, **kwargs):
    action = docker_verb(argv)
    compose_file = argv[argv.index("-f") + 1] if "-f" in argv else ""
    if action == "up" and compose_path_ends_with(compose_file, "docker", "clickhouse", "docker-compose.yml"):
        return docker_assets.DockerCommandResult(tuple(argv), 1, "", "compose up failed")
    return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")


def test_disk_floor_abort_carries_startup_failed_count(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "disk-floor-annotate-startup-failed",
            "platforms": {"include": ["clickhouse-server", "duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def runner(platform, benchmark, scale, **kwargs):
        raise orchestrator.DiskFloorAbort("free space 1.0 GiB < cutoff 5.0 GiB")

    with platform_reachability(True):
        with pytest.raises(orchestrator.DiskFloorAbort) as excinfo:
            exec_phase.run_execute(
                cfg,
                log_dir=tmp_path,
                databases_root=tmp_path / "databases",
                runner=runner,
                docker_runner=_startup_fail_clickhouse_docker,
            )
    assert getattr(excinfo.value, "startup_failed_count", None) == 1


def test_disk_floor_abort_threads_startup_failed_count_into_sidecar_and_partial_report(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "disk-floor-startup-failed-artifacts",
            "phases": ["execute"],
            "platforms": {"include": ["clickhouse-server", "duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )

    with (
        platform_reachability(True),
        patch.object(exec_phase.docker_assets, "run_docker_command", side_effect=_startup_fail_clickhouse_docker),
        patch.object(orchestrator.exec_phase, "run_cell", return_value=cell),
        patch.object(orchestrator.exec_phase, "default_free_space_reader", return_value=100.0),
        patch.object(orchestrator.preflight_budget, "free_space_gib", return_value=1.0),
        patch.object(orchestrator.preflight_budget, "load_budget_table", return_value={}),
    ):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    assert result.aborted_phase == "execute"
    assert result.execute_outcome is not None
    assert result.execute_outcome.abort_kind == "disk_floor"
    accounting = json.loads((tmp_path / "logs" / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert accounting["startup_failed_count"] == 1
    partial_text = (tmp_path / "logs" / "matrix_summary.partial.tsv").read_text(encoding="utf-8")
    assert "startup_failed=1" in partial_text
    assert "attempted=1 skipped=0 unreachable=0 startup_failed=1 died_mid_platform=0 total_defined=2" in partial_text


def _read_gate_summary(log_dir: Path) -> dict:
    return json.loads((log_dir / "uat_gate_summary.json").read_text(encoding="utf-8"))


def test_dry_run_sweep_writes_gate_summary_with_dry_run_verdict(tmp_path: Path):
    cfg = validate_config({"name": "gate-dry", "dry_run": True, "phases": ["preflight", "execute", "report"]})

    orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    payload = _read_gate_summary(tmp_path)
    assert payload["version"] == 1
    assert payload["verdict"] == "dry_run"
    assert payload["dry_run"] is True
    assert payload["config_name"] == "gate-dry"
    assert payload["phase_exit_codes"] == {"preflight": 0, "execute": 0, "report": 0}
    assert payload["completed_at"]
    assert payload["source_commit_sha"]


def test_gate_summary_completed_at_is_offset_aware(tmp_path: Path):
    cfg = validate_config({"name": "gate-tz", "dry_run": True, "phases": ["preflight", "execute", "report"]})

    orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    payload = _read_gate_summary(tmp_path)
    completed_at = _dt.datetime.fromisoformat(payload["completed_at"])
    assert completed_at.tzinfo is not None


def test_green_sweep_writes_green_gate_summary_with_accounting(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "gate-green",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "report": {"cross_scale_coverage_min_pairs": 1},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "startup_failed": (),
            "compatibility_pruned": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()
    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.exit_code() == 0
    payload = _read_gate_summary(tmp_path)
    assert payload["verdict"] == "green"
    assert payload["aborted"] is False
    assert payload["accounting"]["passed"] == 1
    assert payload["accounting"]["attempted"] == 1
    assert payload["accounting"]["total_defined"] == 1
    assert payload["cross_scale_clean_pairs"] == 1
    assert payload["cross_scale_floor"] == 1
    assert payload["cross_scale_floor_breached"] is False
    assert payload["unreachable_is_estimated"] is False


def test_green_sweep_with_unvalidated_cells_reports_unvalidated_count_not_zero(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "gate-green-unvalidated",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb", "polars-df"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "report": {"cross_scale_coverage_min_pairs": 1},
        }
    )
    clean_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "duckdb.log",
        result_path=tmp_path / "duckdb_result.json",
        submit_terminal_state="submittable",
    )
    unvalidated_cell = CellResult(
        platform="polars-df",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "polars_df.log",
        result_path=tmp_path / "polars_df_result.json",
        submit_terminal_state="unvalidated",
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (clean_cell, unvalidated_cell),
            "pruned": (),
            "skipped_unreachable": (),
            "startup_failed": (),
            "compatibility_pruned": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 0,
        },
    )()
    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.exit_code() == 0
    payload = _read_gate_summary(tmp_path)
    assert payload["verdict"] == "green"
    assert payload["accounting"]["passed"] == 2
    assert payload["accounting"]["attempted"] == 2
    assert payload["accounting"]["unvalidated"] == 1

    tsv_text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "# UNVALIDATED_CELLS=1 release_gate_attention=required" in tsv_text


def test_failed_cell_sweep_writes_red_gate_summary(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "gate-red",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="failed",
        exit_code=1,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=None,
    )
    fake_execute = type(
        "ExecuteOutcome",
        (),
        {
            "results": (cell,),
            "pruned": (),
            "skipped_unreachable": (),
            "startup_failed": (),
            "compatibility_pruned": (),
            "aborted": False,
            "abort_reason": None,
            "exit_code": lambda self: 1,
        },
    )()
    with patch.object(orchestrator.exec_phase, "run_execute", return_value=fake_execute):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.exit_code() == 1
    payload = _read_gate_summary(tmp_path)
    assert payload["verdict"] == "red"
    assert payload["accounting"]["failed"] == 1
    assert payload["phase_exit_codes"]["execute"] == 1


def test_aborted_sweep_writes_red_gate_summary_with_abort_fields(tmp_path: Path):
    cfg = validate_config({"name": "gate-abort", "phases": ["report"]})

    result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    assert result.aborted_phase == "report"
    payload = _read_gate_summary(tmp_path)
    assert payload["verdict"] == "red"
    assert payload["aborted"] is True
    assert payload["abort_phase"] == "report"
    assert "execute phase" in payload["abort_reason"]


def test_derive_verdict_matrix():
    from tests.uat import gate_summary

    assert gate_summary.derive_verdict(dry_run=True, aborted=False, phase_exit_codes={"execute": 0}) == "dry_run"
    assert gate_summary.derive_verdict(dry_run=False, aborted=True, phase_exit_codes={}) == "red"
    assert gate_summary.derive_verdict(dry_run=False, aborted=False, phase_exit_codes={"execute": 1}) == "red"
    assert (
        gate_summary.derive_verdict(dry_run=False, aborted=False, phase_exit_codes={"execute": 0, "report": 0})
        == "green"
    )


def test_gate_summary_round_trips_and_ignores_unknown_keys(tmp_path: Path):
    from tests.uat import gate_summary

    cfg = validate_config({"name": "gate-rt", "dry_run": True, "phases": ["execute"]})
    orchestrator.run_sweep(cfg, log_dir_override=tmp_path)

    path = tmp_path / "uat_gate_summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["a_future_field"] = "ignored"
    payload["accounting"]["a_future_count"] = 7
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = gate_summary.read_gate_summary(path)
    assert summary.config_name == "gate-rt"
    assert summary.verdict == "dry_run"


def _stage_summary(name: str, completed_at: str, **overrides):
    from tests.uat import gate_summary

    kwargs = {
        "config_name": name,
        "source_commit_sha": "abc123",
        "source_dirty": False,
        "container_engine": "docker",
        "completed_at": completed_at,
        "dry_run": False,
        "aborted": False,
        "abort_phase": None,
        "abort_reason": None,
        "phase_exit_codes": {"execute": 0, "validate": 0, "report": 0},
        "accounting": gate_summary.PhaseAccounting(attempted=5, passed=5, total_defined=5),
        "unreachable_is_estimated": False,
        "validator_clean_rate": 1.0,
        "validator_clean_rate_floor": 1.0,
        "validator_floor_breached": False,
        "cross_scale_clean_pairs": 10,
        "cross_scale_floor": 8,
        "cross_scale_floor_breached": False,
        "artifact_digests": {
            "cells_jsonl": "a" * 64,
            "accounting_sidecar": "b" * 64,
            "lifecycle_log": "c" * 64,
        },
        "verdict": "green",
    }
    kwargs.update(overrides)
    return gate_summary.GateSummary(**kwargs)


def test_combined_evidence_green_when_all_stages_clean():
    from tests.uat import gate_summary

    stages = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[1], "2026-07-10T12:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[2], "2026-07-10T14:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages], ordering_violations=(), generated_at=_dt.datetime(2026, 7, 10, 15)
    )
    assert evidence.verdict == "green"
    assert evidence.reasons == ()
    assert evidence.source_commit_sha == "abc123"
    assert evidence.completed_at == "2026-07-10T10:00:00"
    assert evidence.stage_verdicts == dict.fromkeys(gate_summary.EXPECTED_RELEASE_GATE_STAGES, "green")


@pytest.mark.parametrize(
    ("overrides", "reason_fragment"),
    [
        ({"verdict": "red"}, "not green"),
        ({"dry_run": True, "verdict": "dry_run"}, "dry-run summary"),
        ({"unreachable_is_estimated": True}, "sidecar missing"),
        ({"source_dirty": True}, "dirty source tree"),
        ({"source_commit_sha": "other"}, "source_commit_sha differs"),
    ],
)
def test_combined_evidence_red_on_each_hold_condition(overrides: dict, reason_fragment: str):
    from tests.uat import gate_summary

    stages = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[1], "2026-07-10T12:00:00", **overrides),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[2], "2026-07-10T14:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages], ordering_violations=(), generated_at=_dt.datetime(2026, 7, 10, 15)
    )
    assert evidence.verdict == "red"
    assert any(reason_fragment in reason for reason in evidence.reasons), evidence.reasons


def test_combined_evidence_red_on_wrong_stage_count_and_ordering_violation():
    from tests.uat import gate_summary

    stages = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[1], "2026-07-10T12:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages],
        ordering_violations=("stage1 boundary: Docker stack 'x' started early",),
        generated_at=_dt.datetime(2026, 7, 10, 15),
    )
    assert evidence.verdict == "red"
    assert any("expected 3" in reason for reason in evidence.reasons)
    assert any("started early" in reason for reason in evidence.reasons)


def test_combined_evidence_red_when_stage_names_are_not_the_expected_set():
    from tests.uat import gate_summary

    same_stage_thrice = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T12:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T14:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in same_stage_thrice], ordering_violations=(), generated_at=_dt.datetime(2026, 7, 10, 15)
    )
    assert evidence.verdict == "red"
    assert any("do not match the expected" in reason for reason in evidence.reasons), evidence.reasons

    hollow_substitute = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary("my-adhoc-config", "2026-07-10T12:00:00"),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[2], "2026-07-10T14:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in hollow_substitute], ordering_violations=(), generated_at=_dt.datetime(2026, 7, 10, 15)
    )
    assert evidence.verdict == "red"
    assert any("do not match the expected" in reason for reason in evidence.reasons), evidence.reasons


def test_combined_evidence_red_when_a_stage_has_no_floor_gates():
    from tests.uat import gate_summary

    stages = [
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[0], "2026-07-10T10:00:00"),
        _stage_summary(
            gate_summary.EXPECTED_RELEASE_GATE_STAGES[1],
            "2026-07-10T12:00:00",
            validator_clean_rate_floor=None,
            cross_scale_floor=None,
        ),
        _stage_summary(gate_summary.EXPECTED_RELEASE_GATE_STAGES[2], "2026-07-10T14:00:00"),
    ]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages], ordering_violations=(), generated_at=_dt.datetime(2026, 7, 10, 15)
    )
    assert evidence.verdict == "red"
    assert any("floor gates were not configured" in reason for reason in evidence.reasons), evidence.reasons


def test_mid_platform_death_surfaces_in_every_machine_readable_rollup(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "mid-platform-death-rollup",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    ran = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=tmp_path / "result.json",
    )
    died = tuple(
        exec_phase.Cell(platform="clickhouse-server", benchmark="tpch", scale=scale) for scale in (0.1, 1.0, 10.0)
    )
    outcome = exec_phase.ExecuteOutcome(
        phase="execute",
        results=(ran,),
        pruned=(),
        skipped_unreachable=(),
        startup_failed=(),
        died_mid_platform=died,
        compatibility_pruned=(),
    )

    with patch.object(orchestrator.exec_phase, "run_execute", return_value=outcome):
        result = orchestrator.run_sweep(cfg, log_dir_override=tmp_path / "logs")

    tsv = (tmp_path / "logs" / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "died_mid_platform=3" in tsv
    assert "# DIED_MID_PLATFORM_CELLS=3 release_gate_attention=required" in tsv

    gate = json.loads((tmp_path / "logs" / "uat_gate_summary.json").read_text(encoding="utf-8"))
    assert gate["accounting"]["died_mid_platform"] == 3
    assert gate["accounting"]["total_defined"] == 4
    assert gate["verdict"] == "red"

    accounting = json.loads((tmp_path / "logs" / "cells.jsonl.accounting.json").read_text(encoding="utf-8"))
    assert accounting["died_mid_platform_count"] == 3
    assert accounting["startup_failed_count"] == 0
    assert accounting["skipped_unreachable_count"] == 0

    assert result.exit_code() != 0


def test_report_cli_json_surfaces_died_mid_platform_from_the_sidecar(tmp_path: Path, capsys):
    cells_jsonl = tmp_path / "cells.jsonl"
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=None,
    )
    cells_io.write_cells_jsonl(
        cells_jsonl,
        (cell,),
        source_info=_source_info(),
        died_mid_platform_count=171,
    )

    exit_code = uat_cli.main(
        [
            "report",
            "--cells-jsonl",
            str(cells_jsonl),
            "--output-tsv",
            str(tmp_path / "matrix_summary.tsv"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["died_mid_platform"] == 171
    assert payload["total_defined"] == 172
    assert exit_code == 1


def _release_gate_stages(**second_stage_overrides):
    from tests.uat import gate_summary

    names = gate_summary.EXPECTED_RELEASE_GATE_STAGES
    return [
        _stage_summary(names[0], "2026-07-10T10:00:00"),
        _stage_summary(names[1], "2026-07-10T12:00:00", **second_stage_overrides),
        _stage_summary(names[2], "2026-07-10T14:00:00"),
    ]


@pytest.mark.parametrize(
    ("fields", "reason_fragment"),
    [
        ({"skipped": 1}, "skipped=1"),
        ({"unreachable": 1}, "unreachable=1"),
        ({"startup_failed": 1}, "startup_failed=1"),
        ({"died_mid_platform": 1}, "died_mid_platform=1"),
        ({"skipped": 2, "early_stop_pruned": 2}, "early_stop_pruned=2"),
        ({"skipped": 3, "compatibility_pruned": 2}, "skipped=1"),
    ],
)
def test_combined_evidence_red_when_a_required_cell_was_not_executed(fields: dict, reason_fragment: str):
    from tests.uat import gate_summary

    accounting = gate_summary.PhaseAccounting(attempted=5, passed=5, total_defined=6, **fields)
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in _release_gate_stages(accounting=accounting)],
        ordering_violations=(),
        generated_at=_dt.datetime(2026, 7, 10, 15),
    )
    assert evidence.verdict == "red"
    assert any("required cell" in reason and reason_fragment in reason for reason in evidence.reasons)


def test_combined_evidence_allows_cells_the_config_prunes_by_design():
    from tests.uat import gate_summary

    accounting = gate_summary.PhaseAccounting(
        attempted=5, passed=5, skipped=7, compatibility_pruned=4, registry_pruned=3, total_defined=12
    )
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in _release_gate_stages(accounting=accounting)],
        ordering_violations=(),
        generated_at=_dt.datetime(2026, 7, 10, 15),
    )
    assert evidence.verdict == "green"


def test_combined_evidence_red_when_a_stage_attempted_no_cells():
    from tests.uat import gate_summary

    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in _release_gate_stages(accounting=gate_summary.PhaseAccounting())],
        ordering_violations=(),
        generated_at=_dt.datetime(2026, 7, 10, 15),
    )
    assert evidence.verdict == "red"
    assert any("no cells attempted" in reason for reason in evidence.reasons)


def test_combined_evidence_binds_every_stage_to_the_admitted_distribution():
    from tests.uat import gate_summary

    digest = "d" * 64
    stages = [replace(stage, distribution_digest=digest) for stage in _release_gate_stages()]
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages],
        ordering_violations=(),
        generated_at=_dt.datetime(2026, 7, 10, 15),
        expected_distribution_digest=digest,
    )
    assert evidence.verdict == "green"
    assert evidence.distribution_digest == digest


@pytest.mark.parametrize("recorded", [None, "e" * 64])
def test_combined_evidence_red_when_a_stage_ran_against_another_distribution(recorded):
    from tests.uat import gate_summary

    digest = "d" * 64
    stages = [replace(stage, distribution_digest=digest) for stage in _release_gate_stages()]
    stages[1] = replace(stages[1], distribution_digest=recorded)
    evidence = gate_summary.build_combined_evidence(
        [[stage] for stage in stages],
        ordering_violations=(),
        generated_at=_dt.datetime(2026, 7, 10, 15),
        expected_distribution_digest=digest,
    )
    assert evidence.verdict == "red"
    assert evidence.distribution_digest is None
    assert any("distribution digest" in reason for reason in evidence.reasons)


def test_gate_summary_records_the_digest_of_the_admitted_distribution_file(tmp_path, monkeypatch):
    from tests.uat import orchestrator

    digest_file = tmp_path / "dist-digest.txt"
    digest_file.write_bytes(b"abc  benchbox-0.5.0rc1-py3-none-any.whl\n")
    monkeypatch.setenv("RELEASE_DIST_DIGEST_FILE", str(digest_file))
    assert orchestrator._distribution_digest() == hashlib.sha256(digest_file.read_bytes()).hexdigest()

    monkeypatch.delenv("RELEASE_DIST_DIGEST_FILE")
    assert orchestrator._distribution_digest() is None


def _exported_result_payload(failures: dict[str, str]) -> dict:
    from types import SimpleNamespace

    from benchbox.core.results.schema import build_result_payload

    def query(query_id: str, status: str, error: str | None = None) -> dict:
        return {
            "query_id": query_id,
            "status": status,
            "execution_time_seconds": 0.5,
            "rows_returned": 0 if error else 3,
            "iteration": 1,
            "stream_id": 0,
            "run_type": "measurement",
            "error_message": error,
        }

    results = [query("1", "SUCCESS")] + [query(qid, "FAILED", err) for qid, err in failures.items()]
    result = SimpleNamespace(
        query_results=results,
        total_rows_loaded=0,
        data_loading_time=0.0,
        validation_status=None,
        execution_id="run_1",
        timestamp=_dt.datetime(2026, 2, 12),
        duration_seconds=1.5,
        query_subset=None,
        benchmark_name="TPC-H Benchmark",
        benchmark_id="tpch",
        scale_factor=0.01,
        test_execution_type="power",
        platform="duckdb",
        platform_info=None,
        system_profile=None,
        table_statistics=None,
        execution_phases=None,
        cost_summary=None,
        execution_context=None,
        execution_metadata=None,
        tuning_config=None,
        power_at_size=None,
        throughput_at_size=None,
        qph_at_size=None,
        throughput_qph=None,
        composite_qph=None,
        geometric_mean_execution_time=None,
        tunings_applied=None,
        tuning_source_file=None,
        tuning_config_hash=None,
    )
    return build_result_payload(result)


def test_cells_jsonl_records_failed_query_ids_and_errors_for_query_failure(tmp_path: Path):
    result_path = tmp_path / "result.json"
    failures = {"7": "boom\n" + "x" * 2000}
    failures.update({str(100 + i): f"err{i}" for i in range(100)})
    payload = _exported_result_payload(failures)
    assert all("error" not in q and "error_message" not in q for q in payload["queries"])
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    cell_log = tmp_path / "cell.log"
    cell_log.write_text("# benchbox run\n", encoding="utf-8")
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="failed",
        exit_code=1,
        elapsed_s=1.0,
        log_path=cell_log,
        result_path=result_path,
        submit_terminal_state="query_failure",
    )
    source_info = orchestrator.RunSourceInfo(commit_sha="abc123", commit_short_sha="abc123", dirty=False)

    row = json.loads(cells_io._render_cell_row(cell, source_info=source_info))

    failed = row["failed_queries"]
    assert len(failed) == cells_io.FAILED_QUERY_MAX_COUNT
    assert failed[0]["query_id"] == "7"
    assert failed[0]["error"].startswith("boom")
    assert len(failed[0]["error"]) == cells_io.FAILED_QUERY_ERROR_CHARS
    assert failed[1] == {"query_id": "100", "status": "FAILED", "error": "err0"}
    log_text = cell_log.read_text(encoding="utf-8")
    assert "# UAT_FAILED_QUERY id=7 status=FAILED error=boom" in log_text
    assert "id=1 " not in log_text


def _failed_details_for(tmp_path: Path, failures: dict[str, str]) -> list[dict[str, str]]:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_exported_result_payload(failures)), encoding="utf-8")
    cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="failed",
        exit_code=1,
        elapsed_s=1.0,
        log_path=tmp_path / "cell.log",
        result_path=result_path,
        submit_terminal_state="query_failure",
    )
    return cells_io._failed_query_details(cell)


def test_cells_jsonl_repeated_query_id_same_error_type_keeps_each_execution_message(tmp_path: Path):
    failed = _failed_details_for(tmp_path, {"7": "Binder Error: first", "7 ": "Binder Error: second"})

    assert [(f["query_id"], f["error"]) for f in failed] == [
        ("7", "Binder Error: first"),
        ("7", "Binder Error: second"),
    ]


def test_cells_jsonl_repeated_query_id_mixed_error_types_never_misattributes(tmp_path: Path):
    failed = _failed_details_for(tmp_path, {"7": "Timeout: slow", "7 ": "Binder Error: bad column"})

    assert [f["error"] for f in failed] == ["Binder Error: bad column | Timeout: slow"] * 2
