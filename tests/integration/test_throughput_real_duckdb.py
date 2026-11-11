# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.streams import MULTI_PART_QUERY_IDS
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.streams import TPCHStreams
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

_SCALE_FACTOR = 0.01
_TPCH_NUM_STREAMS = 3
_TPCDS_NUM_STREAMS = 2
_TPCDS_QUERIES_PER_STREAM = 103
_TPCDS_EXPECTED_QUERY_IDS = Counter(
    f"{query_id}{variant}" if variant else str(query_id)
    for query_id in range(1, 100)
    for variant in (["a", "b"] if query_id in MULTI_PART_QUERY_IDS else [None])
)
_BASE_SEED = 7


@pytest.fixture(scope="module")
def tpch_real_duckdb(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[DuckDBAdapter, TPCHBenchmark, Any], None, None]:
    base = tmp_path_factory.mktemp("tpch_throughput_real")
    db_path = str(base / "tpch.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    conn = adapter.create_connection()
    bench = TPCHBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    bench.generate_data()
    adapter.create_schema(bench, conn)
    adapter.load_data(bench, conn, Path(base / "data"))
    yield adapter, bench, conn
    conn.close()


@pytest.fixture(scope="module")
def tpcds_real_duckdb(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[DuckDBAdapter, TPCDSBenchmark, Any], None, None]:
    base = tmp_path_factory.mktemp("tpcds_throughput_real")
    db_path = str(base / "tpcds.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    conn = adapter.create_connection()
    bench = TPCDSBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    bench.generate_data()
    adapter.create_schema(bench, conn)
    adapter.load_data(bench, conn, Path(base / "data"))
    yield adapter, bench, conn
    conn.close()


@pytest.fixture(scope="module")
def tpch_throughput_run(
    tpch_real_duckdb: tuple[DuckDBAdapter, TPCHBenchmark, Any],
) -> tuple[list[dict[str, Any]], Any]:
    adapter, bench, conn = tpch_real_duckdb
    run_config = {
        "benchmark_name": "tpch",
        "test_execution_type": "throughput",
        "scale_factor": _SCALE_FACTOR,
        "num_streams": _TPCH_NUM_STREAMS,
        "seed": _BASE_SEED,
        "verbose": False,
    }
    rows = adapter._execute_queries_by_type(bench, conn, run_config)
    return rows, adapter._last_throughput_test_result


@pytest.fixture(scope="module")
def tpcds_throughput_run(
    tpcds_real_duckdb: tuple[DuckDBAdapter, TPCDSBenchmark, Any],
) -> tuple[list[dict[str, Any]], Any]:
    adapter, bench, conn = tpcds_real_duckdb
    run_config = {
        "benchmark_name": "tpcds",
        "test_execution_type": "throughput",
        "scale_factor": _SCALE_FACTOR,
        "num_streams": _TPCDS_NUM_STREAMS,
        "seed": _BASE_SEED,
        "verbose": False,
    }
    rows = adapter._execute_queries_by_type(bench, conn, run_config)
    return rows, adapter._last_throughput_test_result


@pytest.mark.duckdb
class TestTPCHThroughputRealDuckDB:
    def test_all_streams_run_all_22_queries_successfully(
        self, tpch_throughput_run: tuple[list[dict[str, Any]], Any]
    ) -> None:
        _rows, result = tpch_throughput_run

        assert result is not None
        assert result.success is True
        assert result.streams_executed == _TPCH_NUM_STREAMS
        assert result.streams_successful == _TPCH_NUM_STREAMS
        assert result.throughput_at_size > 0

        assert len(result.stream_results) == _TPCH_NUM_STREAMS
        for stream_result in result.stream_results:
            assert stream_result.success is True
            assert stream_result.queries_executed == 22
            assert stream_result.queries_successful == 22
            assert stream_result.queries_failed == 0

    def test_no_cross_stream_result_bleed(self, tpch_throughput_run: tuple[list[dict[str, Any]], Any]) -> None:
        rows, result = tpch_throughput_run

        assert len(rows) == _TPCH_NUM_STREAMS * 22
        by_stream: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            assert row["test_type"] == "throughput"
            assert row["status"] == "SUCCESS"
            by_stream[row["stream_id"]].append(row["query_id"])

        assert set(by_stream.keys()) == set(range(1, _TPCH_NUM_STREAMS + 1))
        for stream_id, query_ids in by_stream.items():
            assert sorted(query_ids) == list(range(1, 23)), (
                f"stream {stream_id} must run each of the 22 TPC-H queries exactly once"
            )

        for stream_result in result.stream_results:
            assert len(stream_result.query_results) == 22

    def test_stream_positions_match_canonical_permutation(
        self, tpch_throughput_run: tuple[list[dict[str, Any]], Any]
    ) -> None:
        _rows, result = tpch_throughput_run

        assert result.stream_results, "no stream results to verify"
        for stream_result in result.stream_results:
            stream_id = stream_result.stream_id
            permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)]
            assert len(stream_result.query_results) == len(permutation)
            for qr in stream_result.query_results:
                expected_position = permutation.index(qr["query_id"]) + 1
                assert qr["position"] == expected_position, (
                    f"stream {stream_id} query {qr['query_id']}: recorded position "
                    f"{qr['position']} does not match canonical TPC-H permutation "
                    f"position {expected_position}"
                )

    def test_tpch_reports_truthful_row_counts(self, tpch_throughput_run: tuple[list[dict[str, Any]], Any]) -> None:
        rows, _result = tpch_throughput_run

        result_counts = [row["rows_returned"] for row in rows if row["status"] == "SUCCESS"]
        assert result_counts, "expected at least one successful TPC-H throughput row"
        assert all(isinstance(count, int) for count in result_counts)

        assert any(count > 1 for count in result_counts), (
            "expected at least one TPC-H query to report >1 row - if every "
            "result_count is <= 1, the first_row-collapse bug has regressed"
        )
        assert not all(count in (0, 1) for count in result_counts), (
            "TPC-H row counts must not be uniformly collapsed to the old 0/1 sentinel values"
        )


@pytest.mark.duckdb
class TestTPCDSThroughputRealDuckDB:
    def test_all_streams_run_permuted_set_successfully(
        self, tpcds_throughput_run: tuple[list[dict[str, Any]], Any]
    ) -> None:
        _rows, result = tpcds_throughput_run

        assert result is not None
        assert result.success is True
        assert result.streams_executed == _TPCDS_NUM_STREAMS
        assert result.streams_successful == _TPCDS_NUM_STREAMS
        assert result.throughput_at_size > 0

        assert len(result.stream_results) == _TPCDS_NUM_STREAMS
        for stream_result in result.stream_results:
            assert stream_result.success is True
            assert stream_result.queries_executed == _TPCDS_QUERIES_PER_STREAM
            assert stream_result.queries_successful == stream_result.queries_executed
            assert stream_result.queries_failed == 0

    def test_no_cross_stream_result_bleed(self, tpcds_throughput_run: tuple[list[dict[str, Any]], Any]) -> None:
        rows, result = tpcds_throughput_run

        by_stream: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            assert row["test_type"] == "throughput"
            assert row["status"] == "SUCCESS"
            by_stream[row["stream_id"]].append(row["query_id"])

        assert set(by_stream.keys()) == set(range(1, _TPCDS_NUM_STREAMS + 1))

        for stream_id, query_ids in by_stream.items():
            assert len(query_ids) == _TPCDS_QUERIES_PER_STREAM, (
                f"stream {stream_id} ran {len(query_ids)} queries, expected {_TPCDS_QUERIES_PER_STREAM}"
            )
            assert Counter(query_ids) == _TPCDS_EXPECTED_QUERY_IDS, (
                f"stream {stream_id} ran the wrong query/variant multiset: "
                f"missing={_TPCDS_EXPECTED_QUERY_IDS - Counter(query_ids)}, "
                f"unexpected={Counter(query_ids) - _TPCDS_EXPECTED_QUERY_IDS}"
            )

        for stream_result in result.stream_results:
            assert stream_result.queries_executed == _TPCDS_QUERIES_PER_STREAM

    def test_tpcds_reports_truthful_row_counts(self, tpcds_throughput_run: tuple[list[dict[str, Any]], Any]) -> None:
        rows, _result = tpcds_throughput_run

        result_counts = [row["rows_returned"] for row in rows if row["status"] == "SUCCESS"]
        assert result_counts, "expected at least one successful TPC-DS throughput row"
        assert all(isinstance(count, int) for count in result_counts)

        assert any(count > 0 for count in result_counts), (
            "expected at least one TPC-DS query to report a nonzero row count - "
            "if every result_count is 0, the hardcoded-zero bug has regressed"
        )
        assert any(count > 1 for count in result_counts), (
            "expected at least one TPC-DS query to report a genuinely multi-row result"
        )


