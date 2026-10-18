from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from tests.uat import throughput_baseline
from tests.uat.throughput import (
    TPC_ALLOWED_SCALE_FACTORS,
    ThroughputGateError,
    evaluate_floor,
    load_cell_result,
    main,
    resolve_official_result_path,
    validate_result_identity,
    validate_stream_count,
    validate_stream_success,
    validate_throughput_metric,
    validate_throughput_result,
)

pytestmark = pytest.mark.medium


def test_resolve_official_result_path_returns_none_without_emitted_path(tmp_path: Path):
    results_dir = tmp_path / "shared-runs" / "results"
    assert resolve_official_result_path(results_dir) is None


def test_resolve_official_result_path_accepts_absolute_emitted_path(tmp_path: Path):
    results_dir = tmp_path / "shared-runs" / "results"
    result_path = results_dir / "tpch_sf1_duckdb_sql_20260822_130141_a3f0c570.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{}", encoding="utf-8")

    out = resolve_official_result_path(
        results_dir,
        emitted_path=str(result_path),
    )
    assert out == result_path


def test_resolve_official_result_path_resolves_runs_relative_path(tmp_path: Path):
    results_dir = tmp_path / "shared-runs" / "results"
    results_dir.mkdir(parents=True)
    out = resolve_official_result_path(
        results_dir,
        emitted_path="results/tpch_sf1_duckdb_sql_20260822_130141_a3f0c570.json",
    )
    assert out == results_dir / "tpch_sf1_duckdb_sql_20260822_130141_a3f0c570.json"


def test_resolve_official_result_path_resolves_benchmark_runs_prefixed_path(tmp_path: Path):
    results_dir = tmp_path / "shared-runs" / "results"
    results_dir.mkdir(parents=True)
    out = resolve_official_result_path(
        results_dir,
        emitted_path="benchmark_runs/results/tpch_sf1_duckdb_sql_20260822_130141_a3f0c570.json",
    )
    assert out == tmp_path / "benchmark_runs" / "results" / "tpch_sf1_duckdb_sql_20260822_130141_a3f0c570.json"


def _result_json(
    *,
    streams: dict[int, int],
    throughput_at_size: float | None,
    failed_stream_ids: frozenset[int] = frozenset(),
    duration_ms: int = 1_000_000,
    scale_factor: float = 1.0,
    platform_name: str = "DuckDB",
    benchmark_id: str = "tpch",
) -> dict:
    queries = []
    for stream_id, count in streams.items():
        status = "FAILED" if stream_id in failed_stream_ids else "SUCCESS"
        for i in range(count):
            queries.append(
                {
                    "id": str(i + 1),
                    "stream": stream_id,
                    "status": status,
                    "run_type": "measurement",
                    "test_type": "throughput",
                }
            )
    payload: dict = {
        "platform": {"name": platform_name},
        "benchmark": {"id": benchmark_id, "scale_factor": scale_factor},
        "phases": {"throughput_test": {"duration_ms": duration_ms}},
        "queries": queries,
        "summary": {},
    }
    if throughput_at_size is not None:
        payload["summary"]["tpc_metrics"] = {"throughput_at_size": throughput_at_size}
    return payload


def test_validate_throughput_result_accepts_correct_metric_and_rejects_old_formula():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=123.4)
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is True
    assert reason == "ok"

    result["summary"]["tpc_metrics"]["throughput_at_size"] = 10.8
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "plausibility band" in reason


def test_validate_throughput_result_fails_on_stream_count_mismatch():
    result = _result_json(streams={1: 22, 2: 22}, throughput_at_size=123.4)
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "requested 3, executed 2" in reason


def test_validate_throughput_result_fails_on_missing_throughput_metric():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=None)
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "Throughput@Size" in reason


def test_validate_throughput_result_fails_on_zero_throughput_metric():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=0)
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "Throughput@Size" in reason


def test_validate_throughput_result_ignores_queries_without_stream_id():
    result = _result_json(streams={1: 22, 2: 22}, throughput_at_size=100.0)
    result["queries"].append({"id": "99", "status": "SUCCESS", "test_type": "throughput"})
    ok, reason = validate_throughput_result(result, requested_streams=2)
    assert ok is True, reason


def test_validate_throughput_result_handles_empty_queries_list():
    ok, reason = validate_throughput_result({"queries": [], "summary": {}}, requested_streams=2)
    assert ok is False
    assert "requested 2, executed 0" in reason


def test_validate_throughput_result_rejects_all_queries_failed_stream():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=123.4, failed_stream_ids=frozenset({2}))
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "stream" in reason
    assert "[2]" in reason


def test_validate_stream_count_ignores_throughput_metric():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=None)
    ok, reason = validate_stream_count(result, requested_streams=3)
    assert ok is True
    assert reason == "ok"


def test_validate_stream_count_fails_on_mismatch():
    result = _result_json(streams={1: 22, 2: 22}, throughput_at_size=None)
    ok, reason = validate_stream_count(result, requested_streams=3)
    assert ok is False
    assert "requested 3, executed 2" in reason


def test_validate_stream_count_rejects_zero_based_stream_ids():
    result = _result_json(streams={0: 22, 1: 22}, throughput_at_size=None)
    ok, reason = validate_stream_count(result, requested_streams=2)
    assert ok is False
    assert "spec numbering [1, 2]" in reason


def test_validate_throughput_metric_ignores_stream_count():
    result = _result_json(streams={1: 22}, throughput_at_size=55.5)
    ok, reason = validate_throughput_metric(result)
    assert ok is True
    assert reason == "ok"


def test_validate_throughput_metric_fails_on_missing_metric():
    result = _result_json(streams={1: 22}, throughput_at_size=None)
    ok, reason = validate_throughput_metric(result)
    assert ok is False
    assert "Throughput@Size" in reason


def test_validate_throughput_result_composes_both_checks():
    result = _result_json(streams={1: 22}, throughput_at_size=None)
    ok, reason = validate_throughput_result(result, requested_streams=3)
    assert ok is False
    assert "stream count mismatch" in reason


def test_validate_throughput_result_composes_all_three_checks_stream_success_between_count_and_metric():
    result = _result_json(streams={1: 22, 2: 22}, throughput_at_size=None, failed_stream_ids=frozenset({1}))
    ok, reason = validate_throughput_result(result, requested_streams=2)
    assert ok is False
    assert "SUCCESSFUL" in reason
    assert "Throughput@Size" not in reason


def test_validate_stream_success_ok_when_every_stream_has_a_success():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=None)
    ok, reason = validate_stream_success(result)
    assert ok is True
    assert reason == "ok"


def test_validate_stream_success_rejects_stream_with_zero_successful_queries():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=None, failed_stream_ids=frozenset({1}))
    ok, reason = validate_stream_success(result)
    assert ok is False
    assert "[1]" in reason
    assert "SUCCESSFUL" in reason


def test_validate_stream_success_rejects_multiple_all_failed_streams():
    result = _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=None, failed_stream_ids=frozenset({1, 2}))
    ok, reason = validate_stream_success(result)
    assert ok is False
    assert "[1, 2]" in reason


def test_validate_stream_success_ok_when_stream_has_at_least_one_success_among_failures():
    result = _result_json(streams={0: 1}, throughput_at_size=None)
    result["queries"].append({"id": "2", "stream": 0, "status": "FAILED", "test_type": "throughput"})
    ok, reason = validate_stream_success(result)
    assert ok is True, reason


def test_validate_stream_success_is_case_insensitive_on_status():
    result = _result_json(streams={0: 1}, throughput_at_size=None)
    result["queries"][0]["status"] = "success"
    ok, reason = validate_stream_success(result)
    assert ok is True, reason


def test_validate_stream_success_ignores_queries_without_stream_id():
    result = _result_json(streams={1: 22, 2: 22}, throughput_at_size=None)
    result["queries"].append({"id": "99", "status": "FAILED", "test_type": "throughput"})
    ok, reason = validate_stream_success(result)
    assert ok is True, reason


def test_validate_stream_success_fails_when_no_throughput_rows_exist():
    ok, reason = validate_stream_success({"queries": [], "summary": {}})
    assert ok is False
    assert "no throughput rows" in reason


def test_validate_stream_success_ignores_throughput_metric():
    result = _result_json(streams={1: 22}, throughput_at_size=None)
    ok, reason = validate_stream_success(result)
    assert ok is True
    assert reason == "ok"


def test_tpc_allowed_scale_factors_matches_run_official_constant():
    from benchbox.cli.commands.run_official import TPC_ALLOWED_SCALE_FACTORS as cli_constant

    assert cli_constant == TPC_ALLOWED_SCALE_FACTORS


def _power_rows(stream_ids: tuple[int, ...]) -> list[dict]:
    return [
        {"id": "1", "stream": stream_id, "status": "SUCCESS", "run_type": run_type, "test_type": "power"}
        for stream_id in stream_ids
        for run_type in ("warmup", "measurement")
    ]


def test_validate_stream_count_ignores_power_rows_with_stream_numbers():
    result = _result_json(streams={0: 22, 1: 22}, throughput_at_size=100.0)
    result["queries"].extend(_power_rows((0, 1, 2)))
    ok, reason = validate_stream_count(result, requested_streams=3)
    assert ok is False
    assert "requested 3, executed 2" in reason


def test_validate_stream_count_ignores_rows_without_throughput_test_type():
    result = _result_json(streams={0: 22, 1: 22, 2: 22}, throughput_at_size=100.0)
    for query in result["queries"]:
        del query["test_type"]
    ok, reason = validate_stream_count(result, requested_streams=3)
    assert ok is False
    assert "requested 3, executed 0" in reason


def test_validate_stream_success_ignores_failed_power_rows():
    result = _result_json(streams={0: 22, 1: 22}, throughput_at_size=None)
    result["queries"].append({"id": "1", "stream": 7, "status": "FAILED", "test_type": "power"})
    ok, reason = validate_stream_success(result)
    assert ok is True, reason


def test_validate_throughput_metric_excludes_failed_queries_from_expected_total():
    all_rows_value = TPCMetricsCalculator.calculate_throughput_at_size(
        total_queries=66, total_time_seconds=1000.0, scale_factor=1.0, num_streams=3
    )
    result = _result_json(streams={0: 22, 1: 22, 2: 22}, throughput_at_size=all_rows_value)
    assert validate_throughput_metric(result)[0] is True

    for query in result["queries"]:
        if query["stream"] in (1, 2) and query["id"] != "1":
            query["status"] = "FAILED"
    ok, reason = validate_throughput_metric(result)
    assert ok is False
    assert "plausibility band" in reason


def test_validate_throughput_metric_ignores_power_rows():
    value = TPCMetricsCalculator.calculate_throughput_at_size(
        total_queries=66, total_time_seconds=1000.0, scale_factor=1.0, num_streams=3
    )
    result = _result_json(streams={0: 22, 1: 22, 2: 22}, throughput_at_size=value)
    result["queries"].extend(_power_rows((0, 1, 2)) * 40)
    assert validate_throughput_metric(result)[0] is True


def test_validate_result_identity_accepts_matching_cell():
    result = _result_json(streams={0: 1}, throughput_at_size=None, platform_name="PostgreSQL")
    ok, reason = validate_result_identity(result, platform="postgresql", benchmark="tpch", scale=1)
    assert ok is True, reason


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"platform": "postgresql", "benchmark": "tpch", "scale": 1}, "platform"),
        ({"platform": "duckdb", "benchmark": "tpcds", "scale": 1}, "benchmark"),
        ({"platform": "duckdb", "benchmark": "tpch", "scale": 10}, "scale factor"),
    ],
)
def test_validate_result_identity_rejects_other_cells(kwargs, expected):
    result = _result_json(streams={0: 1}, throughput_at_size=None)
    ok, reason = validate_result_identity(result, **kwargs)
    assert ok is False
    assert expected in reason


def test_validate_result_identity_rejects_missing_sections():
    ok, reason = validate_result_identity({}, platform="duckdb", benchmark="tpch", scale=1)
    assert ok is False
    assert "platform" in reason


def test_validate_throughput_result_checks_identity_before_stream_count():
    result = _result_json(streams={0: 22}, throughput_at_size=None, platform_name="PostgreSQL")
    ok, reason = validate_throughput_result(result, requested_streams=3, platform="duckdb", benchmark="tpch", scale=1)
    assert ok is False
    assert "platform" in reason


def _good_result(throughput_at_size: float | None = None) -> dict:
    value = (
        throughput_at_size
        if throughput_at_size is not None
        else TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=66, total_time_seconds=1000.0, scale_factor=1.0, num_streams=3
        )
    )
    return _result_json(streams={1: 22, 2: 22, 3: 22}, throughput_at_size=value)


def _write_cells(logs_dir: Path, rows: list[dict]) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    cells = logs_dir / "cells.jsonl"
    cells.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return cells


def _cell_row(
    result_path: Path | None, *, platform: str = "duckdb", scale: float = 1.0, status: str = "passed"
) -> dict:
    return {
        "platform": platform,
        "benchmark": "tpch",
        "scale": scale,
        "status": status,
        "result_path": str(result_path) if result_path else None,
    }


def _setup_cell(tmp_path: Path, payload: dict | str | None = None, *, status: str = "passed") -> str:
    result_path = tmp_path / "results" / "tpch_sf1_duckdb_sql_x.json"
    result_path.parent.mkdir(parents=True)
    if isinstance(payload, str):
        result_path.write_text(payload, encoding="utf-8")
    else:
        result_path.write_text(json.dumps(payload if payload is not None else _good_result()), encoding="utf-8")
    _write_cells(tmp_path / "logs" / "uat_throughput_duckdb_nightly_20261004", [_cell_row(result_path, status=status)])
    return str(tmp_path / "logs" / "uat_throughput_duckdb_nightly_*" / "cells.jsonl")


def test_load_cell_result_reads_result_path_recorded_for_the_cell(tmp_path: Path):
    glob_pattern = _setup_cell(tmp_path)
    cell = load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=1)
    assert cell.result_path == tmp_path / "results" / "tpch_sf1_duckdb_sql_x.json"
    assert cell.payload["platform"]["name"] == "DuckDB"


def test_load_cell_result_ignores_newer_files_in_the_results_directory(tmp_path: Path):
    glob_pattern = _setup_cell(tmp_path)
    stale = tmp_path / "results" / "tpch_sf1_duckdb_sql_newer.json"
    stale.write_text(json.dumps(_result_json(streams={0: 1}, throughput_at_size=1.0)), encoding="utf-8")
    cell = load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=1)
    assert cell.result_path.name == "tpch_sf1_duckdb_sql_x.json"


def test_load_cell_result_fails_when_no_cells_jsonl_matches(tmp_path: Path):
    with pytest.raises(ThroughputGateError, match="exactly one cells.jsonl"):
        load_cell_result(str(tmp_path / "none" / "cells.jsonl"), platform="duckdb", benchmark="tpch", scale=1)


def test_load_cell_result_fails_when_several_cells_jsonl_match(tmp_path: Path):
    glob_pattern = _setup_cell(tmp_path)
    _write_cells(tmp_path / "logs" / "uat_throughput_duckdb_nightly_20261005", [])
    with pytest.raises(ThroughputGateError, match="found 2"):
        load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=1)


def test_load_cell_result_fails_when_cell_row_is_missing(tmp_path: Path):
    glob_pattern = _setup_cell(tmp_path)
    with pytest.raises(ThroughputGateError, match="postgresql"):
        load_cell_result(glob_pattern, platform="postgresql", benchmark="tpch", scale=1)
    with pytest.raises(ThroughputGateError, match="sf10"):
        load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=10)


def test_load_cell_result_fails_when_cell_recorded_no_result_path(tmp_path: Path):
    _write_cells(tmp_path / "logs" / "run", [_cell_row(None)])
    with pytest.raises(ThroughputGateError, match="no result_path"):
        load_cell_result(str(tmp_path / "logs" / "*" / "cells.jsonl"), platform="duckdb", benchmark="tpch", scale=1)


def test_load_cell_result_fails_on_unreadable_result_json(tmp_path: Path):
    glob_pattern = _setup_cell(tmp_path, payload="{not json")
    with pytest.raises(ThroughputGateError, match="could not read result JSON"):
        load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=1)


def test_evaluate_floor_is_observe_only_without_median():
    ok, message = evaluate_floor(100.0, median=None)
    assert ok is True
    assert "observe-only" in message
    assert evaluate_floor(100.0, median="")[0] is True


def test_evaluate_floor_passes_at_the_floor_and_fails_below_it():
    assert evaluate_floor(80.0, median="100", max_drop="0.2")[0] is True
    ok, message = evaluate_floor(79.99, median="100", max_drop="0.2")
    assert ok is False
    assert "regression" in message


def test_evaluate_floor_uses_default_max_drop():
    assert evaluate_floor(80.0, median=100.0)[0] is True
    assert evaluate_floor(79.0, median=100.0)[0] is False


@pytest.mark.parametrize(
    ("observed", "median", "max_drop"),
    [
        (100.0, "abc", None),
        (100.0, "-5", None),
        (100.0, "100", "1.5"),
        (100.0, "100", "0"),
        (None, "100", None),
        (0.0, "100", None),
        (float("nan"), "100", None),
        (100.0, "nan", None),
    ],
)
def test_evaluate_floor_refuses_to_gate_on_bad_inputs(observed, median, max_drop):
    ok, message = evaluate_floor(observed, median=median, max_drop=max_drop)
    assert ok is False
    assert "refusing to gate" in message


_ENV = {
    "RUNNER_OS": "Linux",
    "RUNNER_ARCH": "X64",
    "ImageVersion": "20260930.1",
    "GITHUB_RUN_ID": "555",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SHA": "abc123",
}


def _assert_argv(glob_pattern: str, *extra: str) -> list[str]:
    return [
        "assert",
        "--cells-glob",
        glob_pattern,
        "--platform",
        "duckdb",
        "--benchmark",
        "tpch",
        "--scale",
        "1",
        "--streams",
        "3",
        *extra,
    ]


def test_cli_assert_passes_and_records_baseline(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(throughput_baseline, "detect_cpu_model", lambda: "AMD EPYC 7763 64-Core Processor")
    monkeypatch.setattr(throughput_baseline.os, "cpu_count", lambda: 4)
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    glob_pattern = _setup_cell(tmp_path)
    out_dir = tmp_path / "baseline"

    assert main(_assert_argv(glob_pattern, "--baseline-out", str(out_dir))) == 0

    output = capsys.readouterr().out
    assert "::notice::Throughput@Size observed" in output
    [record_path] = list(out_dir.glob("throughput-baseline-*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["runner_class"] == "amd-epyc-7763-64-core-processor-4cpu"
    assert record["run_id"] == "555"
    assert record["commit_sha"] == "abc123"
    assert record["runner_image"] == "20260930.1"


def test_cli_assert_fails_on_stream_count_and_records_no_baseline(tmp_path: Path, capsys):
    glob_pattern = _setup_cell(tmp_path, payload=_result_json(streams={0: 22, 1: 22}, throughput_at_size=100.0))
    out_dir = tmp_path / "baseline"

    assert main(_assert_argv(glob_pattern, "--baseline-out", str(out_dir))) == 1

    assert "::error::throughput stream count mismatch" in capsys.readouterr().out
    assert not out_dir.exists()


def test_cli_assert_fails_when_result_belongs_to_another_cell(tmp_path: Path, capsys):
    payload = _good_result()
    payload["platform"] = {"name": "PostgreSQL"}
    glob_pattern = _setup_cell(tmp_path, payload=payload)

    assert main(_assert_argv(glob_pattern)) == 1

    assert "does not match requested 'duckdb'" in capsys.readouterr().out


def test_cli_assert_fails_on_unreadable_result_json(tmp_path: Path, capsys):
    glob_pattern = _setup_cell(tmp_path, payload="{not json")

    assert main(_assert_argv(glob_pattern)) == 1

    assert "could not read result JSON" in capsys.readouterr().out


def test_cli_assert_floor_failure_blocks_baseline(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("THROUGHPUT_FLOOR_MEDIAN", "1000000000")
    monkeypatch.delenv("THROUGHPUT_FLOOR_MAX_DROP_FRACTION", raising=False)
    glob_pattern = _setup_cell(tmp_path)
    out_dir = tmp_path / "baseline"

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-out", str(out_dir))) == 1

    assert "Throughput@Size regression" in capsys.readouterr().out
    assert not out_dir.exists()


def test_cli_assert_ignores_floor_variables_unless_requested(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("THROUGHPUT_FLOOR_MEDIAN", "1000000000")
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern)) == 0


def test_cli_assert_floor_observe_only_without_median(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("THROUGHPUT_FLOOR_MEDIAN", raising=False)
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor")) == 0

    assert "observe-only" in capsys.readouterr().out


def test_cli_assert_records_no_baseline_when_the_sweep_cell_did_not_pass(tmp_path: Path, capsys):
    glob_pattern = _setup_cell(tmp_path, status="failed")
    out_dir = tmp_path / "baseline"

    assert main(_assert_argv(glob_pattern, "--baseline-out", str(out_dir))) == 0

    assert not out_dir.exists()
    assert load_cell_result(glob_pattern, platform="duckdb", benchmark="tpch", scale=1).passed is False


SLOW_CPU = "AMD EPYC 7763 64-Core Processor"


def _history(tmp_path: Path, values: list[float], *, cpu: str = SLOW_CPU) -> str:
    history = tmp_path / "history"
    for day, value in enumerate(values, start=1):
        throughput_baseline.record_baseline(
            history / str(day),
            {"summary": {"tpc_metrics": {"throughput_at_size": value}}},
            platform="duckdb",
            benchmark="tpch",
            scale=1,
            streams=3,
            env={"GITHUB_RUN_ID": str(day)},
            cpu_model=cpu,
            cpu_count=4,
            recorded_at=datetime(2026, 10, day, 6, 0, tzinfo=timezone.utc),
        )
    return str(history)


@pytest.fixture
def slow_runner(monkeypatch):
    monkeypatch.setattr(throughput_baseline, "detect_cpu_model", lambda: SLOW_CPU)
    monkeypatch.setattr(throughput_baseline.os, "cpu_count", lambda: 4)
    monkeypatch.delenv("THROUGHPUT_FLOOR_MEDIAN", raising=False)
    monkeypatch.delenv("THROUGHPUT_FLOOR_MAX_DROP_FRACTION", raising=False)


def _observed() -> float:
    return _good_result()["summary"]["tpc_metrics"]["throughput_at_size"]


def test_cli_assert_gates_on_the_rolling_median_of_the_runner_class(tmp_path: Path, slow_runner, capsys):
    history = _history(tmp_path, [_observed() * 2] * 5)
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 1

    output = capsys.readouterr().out
    assert "5 of 5 required baseline samples" in output
    assert "rolling median" in output
    assert "Throughput@Size regression" in output


def test_cli_assert_passes_when_observed_clears_the_median_floor(tmp_path: Path, slow_runner, capsys):
    history = _history(tmp_path, [_observed() * 1.1] * 5)
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 0

    assert "clears floor" in capsys.readouterr().out


def test_cli_assert_median_uses_only_the_current_runner_class(tmp_path: Path, slow_runner, capsys):
    history = _history(tmp_path, [_observed() * 100] * 5, cpu="Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz")
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 0

    output = capsys.readouterr().out
    assert "0 of 5 required baseline samples" in output
    assert "observe-only" in output


def test_cli_assert_stays_observe_only_below_the_minimum_samples(tmp_path: Path, slow_runner, capsys):
    history = _history(tmp_path, [_observed() * 100] * (throughput_baseline.MIN_FLOOR_SAMPLES - 1))
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 0

    output = capsys.readouterr().out
    assert f"{throughput_baseline.MIN_FLOOR_SAMPLES - 1} of {throughput_baseline.MIN_FLOOR_SAMPLES} required" in output
    assert "floor stays observe-only" in output


def test_cli_assert_cold_start_without_any_history_is_observe_only(tmp_path: Path, slow_runner, capsys):
    glob_pattern = _setup_cell(tmp_path)
    missing = str(tmp_path / "never-downloaded")

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", missing)) == 0

    assert "0 of 5 required baseline samples" in capsys.readouterr().out


def test_cli_assert_median_override_takes_precedence_over_history(tmp_path: Path, slow_runner, monkeypatch, capsys):
    history = _history(tmp_path, [_observed() * 0.5] * 5)
    monkeypatch.setenv("THROUGHPUT_FLOOR_MEDIAN", str(_observed() * 2))
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 1

    output = capsys.readouterr().out
    assert "Throughput@Size regression" in output
    assert "rolling median" not in output


def test_cli_assert_override_applies_even_when_history_is_too_short(tmp_path: Path, slow_runner, monkeypatch, capsys):
    monkeypatch.setenv("THROUGHPUT_FLOOR_MEDIAN", str(_observed() * 2))
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", _history(tmp_path, [1.0]))) == 1

    assert "Throughput@Size regression" in capsys.readouterr().out


def test_cli_assert_median_floor_honors_the_max_drop_fraction(tmp_path: Path, slow_runner, monkeypatch):
    history = _history(tmp_path, [_observed() * 1.5] * 5)
    glob_pattern = _setup_cell(tmp_path)
    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 1

    monkeypatch.setenv("THROUGHPUT_FLOOR_MAX_DROP_FRACTION", "0.4")

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 0


def test_cli_assert_ignores_history_unless_the_floor_is_requested(tmp_path: Path, slow_runner):
    history = _history(tmp_path, [_observed() * 100] * 5)

    assert main(_assert_argv(_setup_cell(tmp_path), "--baseline-history", history)) == 0


def test_cli_assert_ignores_forged_history_records(tmp_path: Path, slow_runner, capsys):
    history = tmp_path / "history"
    for index in range(10):
        throughput_baseline.record_baseline(
            history / "300",
            {"summary": {"tpc_metrics": {"throughput_at_size": _observed() * 100}}},
            platform="duckdb",
            benchmark="tpch",
            scale=1,
            streams=3,
            env={"GITHUB_RUN_ID": f"forged-{index}", "GITHUB_RUN_ATTEMPT": "1"},
            cpu_model=SLOW_CPU,
            cpu_count=4,
            recorded_at=datetime(9999, 1, 1, tzinfo=timezone.utc),
        )
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", str(history))) == 0

    assert "0 of 5 required baseline samples" in capsys.readouterr().out


def test_cli_assert_min_recorded_at_resets_a_runner_class_baseline(tmp_path: Path, slow_runner, monkeypatch, capsys):
    history = _history(tmp_path, [_observed() * 2] * 5)
    glob_pattern = _setup_cell(tmp_path)
    argv = _assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)
    assert main(argv) == 1

    monkeypatch.setenv("THROUGHPUT_BASELINE_MIN_RECORDED_AT", "2026-10-04")

    assert main(argv) == 0
    assert "2 of 5 required baseline samples" in capsys.readouterr().out
    monkeypatch.setenv("THROUGHPUT_BASELINE_MIN_RECORDED_AT", "2026-10-06")
    assert main(argv) == 0
    assert "0 of 5 required baseline samples" in capsys.readouterr().out


def test_cli_assert_fails_loudly_on_a_malformed_reset_date(tmp_path: Path, slow_runner, monkeypatch, capsys):
    monkeypatch.setenv("THROUGHPUT_BASELINE_MIN_RECORDED_AT", "10/01/2026")
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", _history(tmp_path, [1.0]))) == 1

    output = capsys.readouterr().out
    assert "::error::THROUGHPUT_BASELINE_MIN_RECORDED_AT '10/01/2026' is not an ISO date" in output


def test_cli_assert_warns_when_a_populated_history_still_leaves_the_floor_observe_only(
    tmp_path: Path, slow_runner, capsys
):
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", _history(tmp_path, [1.0]))) == 0

    assert "::warning::1 of 5 required baseline samples" in capsys.readouterr().out


def test_cli_assert_only_notices_when_the_history_is_empty(tmp_path: Path, slow_runner, capsys):
    (tmp_path / "history").mkdir()
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", str(tmp_path / "history"))) == 0

    output = capsys.readouterr().out
    assert "::notice::0 of 5 required baseline samples" in output
    assert "::warning::" not in output


def test_cli_assert_notices_when_the_median_gates(tmp_path: Path, slow_runner, capsys):
    history = _history(tmp_path, [_observed()] * 5)
    glob_pattern = _setup_cell(tmp_path)

    assert main(_assert_argv(glob_pattern, "--evaluate-floor", "--baseline-history", history)) == 0

    assert "::notice::5 of 5 required baseline samples" in capsys.readouterr().out
