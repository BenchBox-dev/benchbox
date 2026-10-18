from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.validation.bundle import ValidationResult
from tests.uat import orchestrator
from tests.uat.config import validate_config
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium


def _runner_factory(invocations: list[tuple[str, str, float]]):

    def fake(platform, benchmark, scale, **kwargs):
        invocations.append((platform, benchmark, scale))
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=Path("/tmp/log.txt"),
            result_path=Path(f"/tmp/{platform}_{benchmark}_{scale}.json"),
        )

    return fake


def test_e2e_two_platforms_three_benchmarks_with_cleanup_and_report(
    tmp_path: Path,
    monkeypatch,
):

    cfg = validate_config(
        {
            "name": "e2e",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb", "sqlite"]},
            "benchmarks": {"include": ["read_primitives", "write_primitives", "tpch"]},
            "scales": {"rungs": [0.01]},
            "execute": {"skip_unreachable": False},
        }
    )

    db_root = tmp_path / "databases"
    for platform in ("duckdb", "sqlite"):
        (db_root / platform / "tpch" / "0.01").mkdir(parents=True)
        (db_root / platform / "tpch" / "0.01" / "data.duckdb").write_text("stub")

    invocations: list[tuple[str, str, float]] = []
    runner = _runner_factory(invocations)

    monkeypatch.setattr(
        "tests.uat.phases.execute.run_cell",
        runner,
    )

    result = orchestrator.run_sweep(
        cfg,
        log_dir_override=tmp_path,
        databases_root=db_root,
    )

    assert result.aborted_phase is None, result.abort_reason
    assert result.phase_exit_codes == {"execute": 0, "report": 0}

    benches_per_platform: dict[str, list[str]] = {}
    for plat, bench, _scale in invocations:
        benches_per_platform.setdefault(plat, []).append(bench)
    for plat, benches in benches_per_platform.items():
        assert benches.index("tpch") < benches.index("read_primitives"), (
            f"tpch must precede read_primitives on {plat}; saw {benches}"
        )
        assert benches.index("tpch") < benches.index("write_primitives"), (
            f"tpch must precede write_primitives on {plat}; saw {benches}"
        )

    for plat in ("duckdb", "sqlite"):
        assert not (db_root / plat / "tpch" / "0.01").exists(), (
            f"tpch DB for {plat} should have been pruned once both consumers completed"
        )

    cells_jsonl = tmp_path / "cells.jsonl"
    assert cells_jsonl.exists(), "execute phase must emit cells.jsonl"
    cells = [json.loads(line) for line in cells_jsonl.read_text().splitlines() if line.strip()]
    assert len(cells) == 6, f"expected 6 cells (2 platforms × 3 benchmarks), got {len(cells)}"
    assert all(c["status"] == "passed" for c in cells)

    tsv_path = tmp_path / "matrix_summary.tsv"
    assert tsv_path.exists()
    tsv_lines = tsv_path.read_text().splitlines()
    assert tsv_lines[0].startswith("platform\tbenchmark\tscale\tstatus")
    body_lines = [line for line in tsv_lines[1:] if not line.startswith("#")]
    assert len(body_lines) == 6
    footer = [line for line in tsv_lines if line.startswith("#")]
    assert any("passed=6" in f for f in footer)


def test_e2e_validator_status_reaches_cross_scale_check(tmp_path: Path, monkeypatch):

    cfg = validate_config(
        {
            "name": "e2e-validator",
            "phases": ["execute", "validate", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1]},
            "execute": {"skip_unreachable": False},
            "report": {"cross_scale_coverage_min_pairs": 1},
        }
    )

    invocations: list[tuple[str, str, float]] = []
    runner = _runner_factory(invocations)
    monkeypatch.setattr("tests.uat.phases.execute.run_cell", runner)

    validated_paths: list[Path] = []

    def fake_validate_bundles(paths):
        result_path = paths[0]
        validated_paths.append(result_path)
        scale = float(result_path.stem.rsplit("_", 1)[-1])
        validation_result = ValidationResult(str(result_path))
        if scale != 0.01:
            validation_result.error("forced validator failure")
        return [validation_result]

    monkeypatch.setattr(
        "tests.uat.phases.validate.validate_bundles",
        fake_validate_bundles,
    )

    result = orchestrator.run_sweep(
        cfg,
        log_dir_override=tmp_path,
        databases_root=tmp_path / "databases",
    )

    assert result.phase_exit_codes["validate"] == 1
    assert result.phase_exit_codes["report"] == 1
    assert validated_paths == [Path(f"/tmp/{plat}_{bench}_{scale}.json") for plat, bench, scale in invocations]
