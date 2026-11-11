from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from scripts import (
    analyze_duckdb_version_matrix,
    analyze_version_matrix as analyzer,
    run_duckdb_version_matrix,
    run_version_matrix as matrix,
)
from scripts.version_matrix_specs import DUCKDB, SPECS, is_prerelease

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GOLDEN_DRY_RUN = Path(__file__).resolve().parents[2] / "fixtures" / "version_matrix" / "duckdb_dry_run.txt"


def _golden_lines() -> list[str]:
    return GOLDEN_DRY_RUN.read_text(encoding="utf-8").replace("{python}", shlex.quote(sys.executable)).splitlines()


def test_prerelease_detection_covers_dev_and_stable_versions() -> None:
    assert is_prerelease("1.6.0.dev365") is True
    assert is_prerelease("1.5.5") is False


def test_matrix_uses_sf10_for_all_four_workloads() -> None:
    assert DUCKDB.benchmarks == (("tpch", 10.0), ("tpcds", 10.0), ("clickbench", 10.0), ("ssb", 10.0))


def test_benchbox_command_uses_package_option_for_stable_versions() -> None:
    command = matrix.benchbox_command(DUCKDB, phase="power", benchmark="tpch", scale=10.0, version="1.5.5")

    assert command[command.index("--platform") + 1] == "duckdb"
    assert command[-2:] == ["--platform-option", "driver_version=1.5.5"]


def test_benchbox_command_omits_driver_option_for_dev_versions() -> None:
    command = matrix.benchbox_command(DUCKDB, phase="power", benchmark="tpch", scale=10.0, version="1.6.0.dev365")

    assert "--platform-option" not in command


@pytest.mark.parametrize(
    "entry",
    [
        lambda argv: run_duckdb_version_matrix.main(argv),
        lambda argv: matrix.main(["--engine", "duckdb", *argv]),
    ],
    ids=["duckdb-wrapper", "engine-neutral"],
)
def test_duckdb_dry_run_matches_golden_command_list(entry, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert entry(["--output-dir", str(tmp_path / "matrix"), "--dry-run"]) == 0

    lines = capsys.readouterr().out.splitlines()
    commands = [line for line in lines if line.startswith("$ ")]

    assert lines == _golden_lines()
    assert len(commands) == 123
    assert sum(" benchbox run " in command for command in commands) == 116
    assert sum(" pip install " in command for command in commands) == 7
    assert not (tmp_path / "matrix").exists()


def test_plan_counts_match_manifest_expectation() -> None:
    steps = matrix.plan_steps(DUCKDB)

    assert sum(step.kind != "install" for step in steps) == DUCKDB.expected_benchmark_runs == 116
    assert [step.version for step in steps if step.kind == "install"] == list(DUCKDB.versions)


def test_engine_registry_exposes_duckdb() -> None:
    assert SPECS["duckdb"] is DUCKDB
    assert DUCKDB.analysis_stem == "duckdb-version-matrix"
    assert DUCKDB.speedup_field == "speedup_vs_1.0.0"
    assert DUCKDB.bundle_filename("tpch", "1.6.0.dev365") == "tpch_sf10_duckdb_v1_6_0_dev365_median.json"


def test_dry_run_does_not_require_a_log_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status, output, elapsed = matrix._run_command(
        ["command-that-is-not-run"],
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "missing" / "matrix.log",
        dry_run=True,
    )

    assert (status, output, elapsed) == (0, "", 0.0)
    assert "$ command-that-is-not-run" in capsys.readouterr().out


def test_find_result_path_accepts_relative_path(tmp_path: Path) -> None:
    result = tmp_path / "results" / "run.json"
    result.parent.mkdir()
    result.write_text("{}", encoding="utf-8")

    assert matrix._find_result_path("ignored\nresults/run.json\n", cwd=tmp_path) == result.resolve()


def test_run_command_uses_repository_monotonic_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elapsed_starts: list[float] = []
    monkeypatch.setattr(matrix, "mono_time", lambda: 10.0)
    monkeypatch.setattr(matrix, "elapsed_seconds", lambda started: elapsed_starts.append(started) or 2.5)
    monkeypatch.setattr(
        matrix.subprocess,
        "run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    status, output, elapsed = matrix._run_command(
        ["command-that-runs"],
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "matrix.log",
        dry_run=False,
    )

    assert (status, output, elapsed) == (0, "", 2.5)
    assert elapsed_starts == [10.0]


def _matrix_payload(run_id: str, total_ms: float, query_ms: float, warmup_ms: float = 99.0) -> dict:
    return {
        "export": {"anonymized": True},
        "run": {"id": run_id, "query_time_ms": total_ms, "total_duration_ms": total_ms + 100},
        "summary": {
            "data": {"load_time_ms": total_ms + 10, "rows_loaded": 10},
            "queries": {"failed": 0, "passed": 1, "total": 1},
            "timing": {"avg_ms": query_ms, "total_ms": total_ms},
            "tpc_metrics": {"power_at_size": 1000 / total_ms},
            "validation": "passed",
        },
        "queries": [
            {"id": "1", "iter": 0, "ms": warmup_ms, "run_type": "warmup", "status": "SUCCESS", "stream": 0},
            {"id": "1", "iter": 1, "ms": query_ms, "run_type": "measurement", "status": "SUCCESS", "stream": 0},
        ],
    }


def test_aggregate_payloads_medians_measurements_and_run_metrics() -> None:
    aggregate = analyzer.aggregate_payloads(
        DUCKDB,
        [
            _matrix_payload("one", 30.0, 3.0, 900.0),
            _matrix_payload("two", 10.0, 1.0, 100.0),
            _matrix_payload("three", 20.0, 2.0, 500.0),
        ],
        version="1.5.5",
        benchmark="tpch",
    )

    assert aggregate["run"]["id"] != "one"
    assert aggregate["run"]["query_time_ms"] == 20.0
    assert aggregate["run"]["total_duration_ms"] == 120.0
    assert aggregate["summary"]["timing"] == {"avg_ms": 2.0, "total_ms": 20.0}
    assert aggregate["summary"]["data"]["load_time_ms"] == 30.0
    assert aggregate["summary"]["tpc_metrics"]["power_at_size"] == 50.0
    assert [query["ms"] for query in aggregate["queries"]] == [500.0, 2.0]
    assert aggregate["export"]["aggregation"] == {
        "method": "median",
        "repetitions": 3,
        "source_run_ids": ["one", "two", "three"],
    }


def test_duckdb_analyzer_wrapper_writes_outputs_to_requested_directory(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    records = []
    for version in DUCKDB.versions:
        for benchmark in DUCKDB.benchmark_ids:
            for repetition, total in enumerate((30.0, 10.0, 20.0), start=1):
                path = f"results/{benchmark}_{version}_{repetition}.json"
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(json.dumps(_matrix_payload(f"{version}-{repetition}", total, total / 10)))
                records.append({"phase": "power", "benchmark": benchmark, "requested_version": version, "path": path})
    manifest = root / "matrix-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "versions": list(DUCKDB.versions),
                "benchmarks": [{"id": name, "scale": scale} for name, scale in DUCKDB.benchmarks],
                "records": records,
            }
        )
    )
    out = tmp_path / "out"

    assert analyze_duckdb_version_matrix.main([str(manifest), "--output-dir", str(out)]) == 0

    assert (out / "duckdb-version-matrix-analysis.json").is_file()
    assert (out / "duckdb-version-matrix-analysis.csv").read_text().splitlines()[0].split(",") == [
        "version",
        "benchmark",
        "scale",
        "repetitions",
        "median_total_ms",
        "speedup_vs_1.0.0",
        "median_power_at_size",
    ]
    assert not (root / "duckdb-version-matrix-analysis.json").exists()


