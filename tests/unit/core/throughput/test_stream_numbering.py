from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.throughput.result import (
    THROUGHPUT_FIRST_STREAM_ID,
    THROUGHPUT_STREAM_NUMBERING_BASIS,
    throughput_stream_ids,
    throughput_stream_numbering,
)
from benchbox.core.tpcds.streams import StreamQuery, generate_dsqgen_streams
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch.platform_power import execute_tpch_power_test
from benchbox.core.tpch.queries import QGenBinary
from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_SEED = 20261005
_NUMBERING = {"basis": THROUGHPUT_STREAM_NUMBERING_BASIS, "first_stream_id": 1}


class _RecordingBenchmark:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_query(self, query_id: int, **kwargs: Any) -> str:
        self.calls.append({"query_id": query_id, **kwargs})
        return f"SELECT {query_id}"


class _Connection:
    def execute(self, sql: str) -> Any:
        return SimpleNamespace(rowcount=1)

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


def _normalized(text: str) -> str:
    return re.sub(r"revenue\d+", "revenueN", re.sub(r"\s+", " ", text)).strip()


def _qgen_stream_order(stream: int) -> list[int]:
    qgen = QGenBinary()
    env = {**os.environ, "DSS_QUERY": str(qgen.templates_dir / "queries")}
    env["PATH"] = os.pathsep.join((str(Path(qgen.qgen_path).parent), env.get("PATH", "")))
    cwd = qgen._ensure_work_dir()

    def run(*arguments: str) -> str:
        completed = subprocess.run(
            ["qgen", "-a", "-d", *arguments], cwd=cwd, env=env, capture_output=True, text=True, check=True
        )
        return _normalized(completed.stdout.replace("-- using default substitutions", ""))

    stream_text = run("-p", str(stream))
    positions = {}
    for query_id in range(1, 23):
        single = run(str(query_id)).replace("where rownum <= -1;", "").strip()
        positions[query_id] = stream_text.index(_normalized(single)[:200])
    return sorted(positions, key=positions.__getitem__)


def _stream_query_ids(stream_result: Any) -> list[int]:
    return [int(row["query_id"]) for row in stream_result.query_results]


def _tpcds_ids(stream: list[StreamQuery]) -> list[tuple[int, str | None]]:
    return [(query.query_id, query.variant) for query in stream]


class _TPCDSBenchmark:
    def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
        return query


class TestStreamIdHelpers:
    def test_first_throughput_stream_is_one(self) -> None:
        assert THROUGHPUT_FIRST_STREAM_ID == 1
        assert list(throughput_stream_ids(3)) == [1, 2, 3]
        assert list(throughput_stream_ids(0)) == []

    def test_numbering_basis_is_recorded(self) -> None:
        assert throughput_stream_numbering() == _NUMBERING


class TestTPCHThroughputStreamNumbering:
    @pytest.fixture
    def result(self) -> Any:
        benchmark = _RecordingBenchmark()
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=_Connection, num_streams=2)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, base_seed=_SEED)
        outcome = test.run(config)
        outcome.benchmark = benchmark
        return outcome

    def test_streams_are_numbered_one_to_s(self, result: Any) -> None:
        assert sorted(stream.stream_id for stream in result.stream_results) == [1, 2]
        assert {row["stream_id"] for stream in result.stream_results for row in stream.query_results} == {1, 2}

    def test_no_stream_repeats_the_power_ordering(self, result: Any) -> None:
        from benchbox.core.tpch.streams import TPCHStreams

        power_order = TPCHStreams.PERMUTATION_MATRIX[0]
        for stream in result.stream_results:
            assert _stream_query_ids(stream) != power_order

    def test_stream_ordering_matches_qgen_permutation_for_the_same_stream_number(self, result: Any) -> None:
        for stream in result.stream_results:
            assert _stream_query_ids(stream) == _qgen_stream_order(stream.stream_id)

    def test_stream_parameters_use_the_numbered_stream_seed(self, result: Any) -> None:
        by_stream: dict[int, list[int]] = {}
        for call in result.benchmark.calls:
            by_stream.setdefault(call["params"]["stream_id"], []).append(call["seed"])

        assert sorted(by_stream) == [1, 2]
        for stream_id, seeds in by_stream.items():
            assert sorted(seeds) == [_SEED + stream_id + stream_id * 1000 + position for position in range(22)]

    def test_result_records_the_numbering_basis(self, result: Any) -> None:
        assert result.stream_numbering == _NUMBERING


class TestTPCDSThroughputStreamNumbering:
    def test_dsqgen_is_asked_for_the_power_stream_plus_every_throughput_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[int] = []

        def fake_generate(**kwargs: Any) -> dict[int, list[StreamQuery]]:
            requested.append(kwargs["num_streams"])
            return {
                stream: [StreamQuery(stream_id=stream, position=0, query_id=stream + 1, sql=f"SELECT {stream}")]
                for stream in range(kwargs["num_streams"])
            }

        monkeypatch.setattr("benchbox.core.tpcds.streams.generate_dsqgen_streams", fake_generate)
        executed: list[str] = []

        class _RecordingConnection(_Connection):
            def execute(self, sql: str) -> Any:
                executed.append(sql)
                return super().execute(sql)

        test = TPCDSThroughputTest(
            benchmark=_TPCDSBenchmark(), connection_factory=_RecordingConnection, scale_factor=1.0, num_streams=2
        )
        result = test.run(TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=2, base_seed=7))

        assert requested == [3]
        assert sorted(stream.stream_id for stream in result.stream_results) == [1, 2]
        assert sorted(executed) == ["SELECT 1", "SELECT 2"]
        assert result.stream_numbering == _NUMBERING

    def test_stream_ordering_matches_dsqgen_stream_files_and_excludes_the_power_stream(self) -> None:
        dsqgen = generate_dsqgen_streams(num_streams=3, scale_factor=1.0, seed=7)
        test = TPCDSThroughputTest(
            benchmark=_TPCDSBenchmark(), connection_factory=_Connection, scale_factor=1.0, num_streams=2
        )

        pregenerated = test._pregenerate_stream_queries(
            TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=2, base_seed=7)
        )

        assert sorted(pregenerated) == [1, 2]
        for stream_id in (1, 2):
            assert _tpcds_ids([query for query, _sql in pregenerated[stream_id]]) == _tpcds_ids(dsqgen[stream_id])
            assert _tpcds_ids([query for query, _sql in pregenerated[stream_id]]) != _tpcds_ids(dsqgen[0])


class TestTPCHPowerStreamSemantics:
    @staticmethod
    def _run(run_config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[int, bool]]]:
        executions: list[tuple[int, bool]] = []
        benchmark = _RecordingBenchmark()

        class _Adapter:
            _validate_row_count = False

            def set_query_context(self, query_id: Any, stream_id: Any = None) -> None:
                self.stream_id = stream_id

            def execute(self, sql: str) -> Any:
                executions.append((self.stream_id, self._validate_row_count))
                return SimpleNamespace(platform_result={"status": "SUCCESS", "rows_returned": 1})

            def commit(self) -> None:
                return None

        results = execute_tpch_power_test(
            SimpleNamespace(get_target_dialect=lambda: "duckdb"),
            benchmark,
            object(),
            {"scale_factor": 1.0, "verbose": False, **run_config},
            make_connection_adapter=lambda connection, benchmark_id, scale_factor: _Adapter(),
            console=SimpleNamespace(print=lambda *args, **kwargs: None),
        )
        return results, executions

    def test_warmup_and_measured_iterations_run_stream_zero_with_row_count_validation(self) -> None:
        results, executions = self._run({"iterations": 3, "warm_up_iterations": 1})

        assert {row["stream_id"] for row in results} == {0}
        assert {row["run_type"] for row in results} == {"warmup", "measurement"}
        assert len(executions) == 4 * 22
        assert {stream_id for stream_id, _ in executions} == {0}
        assert all(validated for _, validated in executions)

    def test_measured_iterations_use_the_stream_zero_ordering(self) -> None:
        from benchbox.core.tpch.streams import TPCHStreams

        results, _ = self._run({"iterations": 2, "warm_up_iterations": 0})

        for iteration in (1, 2):
            order = [int(row["query_id"]) for row in results if row["iteration"] == iteration]
            assert order == TPCHStreams.PERMUTATION_MATRIX[0]

    def test_a_configured_stream_id_is_honored_for_every_iteration(self) -> None:
        results, _ = self._run({"iterations": 2, "warm_up_iterations": 1, "stream_id": 2})

        assert {row["stream_id"] for row in results} == {2}


def test_tpch_power_uses_platform_refresh_connection_factory_only_for_final_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchbox.core.tpch import power_test as power_test_module, refresh

    update_set = SimpleNamespace(set_id=1)
    monkeypatch.setattr(refresh, "generate_refresh_update_sets", lambda *_args: {1: update_set})
    monkeypatch.setattr(refresh, "require_refresh_support", lambda _adapter: None)
    calls = []

    class PowerTest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(validation=True)
            calls.append(self)

        def run(self):
            factory = self.kwargs["refresh_connection_factory"]
            if factory is not None:
                assert factory() is refresh_connection
            return SimpleNamespace(
                query_results=[],
                success=True,
                power_at_size=1.0,
                queries_successful=22,
                queries_executed=22,
                total_time=1.0,
                errors=[],
            )

    monkeypatch.setattr(power_test_module, "TPCHPowerTest", PowerTest)
    adapter = SimpleNamespace(supports_refresh_functions=True, get_target_dialect=lambda: "duckdb")
    connection = object()
    refresh_connection = object()
    connection_adapter = SimpleNamespace(_validate_row_count=True)

    execute_tpch_power_test(
        adapter,
        _RecordingBenchmark(),
        connection,
        {
            "scale_factor": 0.01,
            "tpch_refresh_functions": True,
            "iterations": 2,
            "warm_up_iterations": 0,
        },
        make_connection_adapter=lambda *_args: connection_adapter,
        make_refresh_connection_adapter=lambda given_connection, scale: (
            calls.append((given_connection, scale)) or refresh_connection
        ),
        console=SimpleNamespace(print=lambda *args, **kwargs: None),
    )

    power_tests = [call for call in calls if isinstance(call, PowerTest)]
    assert [test.kwargs["refresh_update_set"] for test in power_tests] == [None, update_set]
    assert [test.kwargs["refresh_connection_factory"] is not None for test in power_tests] == [False, True]
    assert (connection, 0.01) in calls
