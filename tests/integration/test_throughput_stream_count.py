# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


def _run_tpch_throughput(
    tmp_path: Path,
    *,
    concurrent_streams: int | None = None,
    num_streams: int | None = None,
):
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    db_path = str(tmp_path / "tpch.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    conn = adapter.create_connection()
    try:
        bench = TPCHBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"))
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        run_config: dict = {
            "benchmark_name": "tpch",
            "test_execution_type": "throughput",
            "scale_factor": 0.01,
        }
        if concurrent_streams is not None:
            run_config["concurrent_streams"] = concurrent_streams
        if num_streams is not None:
            run_config["num_streams"] = num_streams

        results = adapter._execute_queries_by_type(bench, conn, run_config)
        return adapter._last_throughput_test_result, results
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (4, 4),
        (8, 8),
    ],
)
def test_throughput_stream_count_tpch_matches_requested(tmp_path, requested, expected):
    result, results = _run_tpch_throughput(tmp_path, concurrent_streams=requested)

    assert result.streams_executed == expected
    assert result.streams_successful == expected
    assert sorted({r.get("stream_id") for r in results}) == list(range(1, expected + 1))


def test_throughput_stream_count_tpcds_matches_requested(tmp_path):
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark

    db_path = str(tmp_path / "tpcds.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    conn = adapter.create_connection()
    try:
        bench = TPCDSBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"), verbose=False)
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        run_config = {
            "benchmark_name": "tpcds",
            "test_execution_type": "throughput",
            "scale_factor": 0.01,
            "concurrent_streams": 2,
            "validation_mode": "skip",
            "query_subset": ["1", "3"],
        }
        results = adapter._execute_queries_by_type(bench, conn, run_config)
        result = adapter._last_throughput_test_result

        assert result.streams_executed == 2
        assert sorted({r.get("stream_id") for r in results}) == [1, 2]
        assert {str(r.get("query_id")) for r in results} == {"1", "3"}
        assert len(results) == 4
    finally:
        conn.close()


def _run_tpch_throughput_via_real_pipeline_shape(tmp_path: Path):
    from benchbox.core.schemas import BenchmarkConfig, RunConfig
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    db_path = str(tmp_path / "tpch.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    conn = adapter.create_connection()
    try:
        bench = TPCHBenchmark(scale_factor=0.01, output_dir=str(tmp_path / "data"))
        bench.generate_data()
        adapter.create_schema(bench, conn)
        adapter.load_data(bench, conn, str(tmp_path / "data"))

        benchmark_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="throughput",
        )
        run_config_model = RunConfig(
            benchmark=benchmark_config.name,
            concurrent_streams=benchmark_config.concurrency,
            test_execution_type="throughput",
            scale_factor=benchmark_config.scale_factor,
        )
        run_config = {k: v for k, v in run_config_model.__dict__.items() if k != "benchmark"}
        run_config.setdefault("benchmark_name", run_config_model.benchmark)

        assert "concurrent_streams" in run_config
        assert run_config["concurrent_streams"] is None

        results = adapter._execute_queries_by_type(bench, conn, run_config)
        return adapter._last_throughput_test_result, results
    finally:
        conn.close()


def test_throughput_stream_count_default_preserved_via_real_pipeline_shape(tmp_path):
    result, _results = _run_tpch_throughput_via_real_pipeline_shape(tmp_path)

    assert result.streams_executed == 2


@pytest.mark.parametrize("key", ["num_streams", "concurrent_streams"])
def test_throughput_stream_count_explicit_single_stream_is_rejected(tmp_path, key):
    result, results = _run_tpch_throughput(tmp_path, **{key: 1})

    assert result is None
    assert results[0]["query_id"] == "throughput_test_error"
    assert "at least 2" in results[0]["error"]


def test_throughput_stream_count_legacy_num_streams_key_still_wins(tmp_path):
    result, _results = _run_tpch_throughput(tmp_path, concurrent_streams=8, num_streams=3)

    assert result.streams_executed == 3


def test_run_official_rejects_explicit_streams_one():
    from click.testing import CliRunner

    from benchbox.cli.commands.run_official import run_official

    runner = CliRunner()
    result = runner.invoke(
        run_official,
        ["tpch", "--platform", "duckdb", "--scale", "1", "--phases", "throughput", "--streams", "1", "--seed", "42"],
    )

    assert result.exit_code != 0
    assert "streams must be >= 2" in result.output


