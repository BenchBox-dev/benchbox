from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.refresh import TPCHRefreshUpdateSet, generate_refresh_update_sets
from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig
from benchbox.platforms import sqlite as sqlite_module
from benchbox.platforms.base.connection_wrappers import (
    PlatformAdapterConnection,
    open_stream_connection,
    require_throughput_stream_capability,
)
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.sqlite,
]

_SCALE_FACTOR = 0.01
_NUM_STREAMS = 3
_FAILING_SQL = "SELECT * FROM table_that_does_not_exist"
_HANG_SQL = "SELECT strict_engine_hang()"
_FAILING_TPCH_QUERY = 6
_HUNG_TPCH_QUERY = 1


class _TPCHQueryOverride:
    def __init__(self, benchmark: TPCHBenchmark, overrides: dict[int, str]) -> None:
        self._benchmark = benchmark
        self._overrides = overrides
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._benchmark, name)

    def get_query(self, query_id: int, **kwargs: Any) -> str:
        self.calls.append({"query_id": query_id, **kwargs})
        if query_id in self._overrides:
            return self._overrides[query_id]
        return self._benchmark.get_query(query_id, **kwargs)


class _FirstTPCDSQueryFails:
    def __init__(self, benchmark: TPCDSBenchmark) -> None:
        self._benchmark = benchmark
        self._translations = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._benchmark, name)

    def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
        self._translations += 1
        if self._translations == 1:
            return _FAILING_SQL
        return self._benchmark.translate_query_text(query, source_dialect, target_dialect)


def _throughput_run_config(num_streams: int = _NUM_STREAMS) -> dict[str, Any]:
    return {
        "benchmark_name": "tpch",
        "test_execution_type": "throughput",
        "scale_factor": _SCALE_FACTOR,
        "num_streams": num_streams,
        "seed": 7,
    }


def _record_stream_connections(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    opened: list[sqlite3.Connection] = []
    original = SQLiteAdapter.new_stream_connection

    def recording(self: SQLiteAdapter, connection: Any, **kwargs: Any) -> Any:
        stream_connection = original(self, connection, **kwargs)
        opened.append(stream_connection)
        return stream_connection

    monkeypatch.setattr(SQLiteAdapter, "new_stream_connection", recording)
    return opened


def _stream_connection_factory(adapter: SQLiteAdapter, connection: Any, benchmark_type: str, scale_factor: float):
    def factory() -> PlatformAdapterConnection:
        wrapper = PlatformAdapterConnection(open_stream_connection(adapter, connection, "olap"), adapter)
        wrapper.benchmark_type = benchmark_type
        wrapper.scale_factor = scale_factor
        return wrapper

    return factory


@pytest.fixture(scope="module")
def tpch_data(tmp_path_factory: pytest.TempPathFactory) -> tuple[TPCHBenchmark, Path]:
    base = tmp_path_factory.mktemp("tpch_sqlite_data")
    benchmark = TPCHBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    benchmark.generate_data()
    return benchmark, base / "data"


def _load_tpch(adapter: SQLiteAdapter, benchmark: TPCHBenchmark, data_dir: Path) -> Any:
    connection = adapter.create_connection()
    adapter.create_schema(benchmark, connection)
    adapter.load_data(benchmark, connection, data_dir)
    return connection


@pytest.fixture(scope="module")
def tpch_refresh_sets(tmp_path_factory: pytest.TempPathFactory) -> dict[int, TPCHRefreshUpdateSet]:
    return generate_refresh_update_sets(_SCALE_FACTOR, 1, 1, tmp_path_factory.mktemp("tpch_refresh_sets") / "dbgen")


@pytest.fixture(scope="module")
def tpch_sqlite(
    tmp_path_factory: pytest.TempPathFactory, tpch_data: tuple[TPCHBenchmark, Path]
) -> Generator[tuple[SQLiteAdapter, TPCHBenchmark, Any], None, None]:
    benchmark, data_dir = tpch_data
    adapter = SQLiteAdapter(database_path=str(tmp_path_factory.mktemp("tpch_sqlite_throughput") / "tpch.sqlite"))
    connection = _load_tpch(adapter, benchmark, data_dir)
    yield adapter, benchmark, connection
    connection.close()


@pytest.fixture
def tpcds_sqlite(tmp_path: Path) -> Generator[tuple[SQLiteAdapter, TPCDSBenchmark, Any], None, None]:
    adapter = SQLiteAdapter(database_path=str(tmp_path / "tpcds.sqlite"))
    connection = adapter.create_connection()
    benchmark = TPCDSBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(tmp_path / "data"))
    adapter.create_schema(benchmark, connection)
    yield adapter, benchmark, connection
    connection.close()


class TestTPCHThroughputOnSQLite:
    def test_all_queries_succeed_on_isolated_stream_connections(
        self, tpch_sqlite: tuple[SQLiteAdapter, TPCHBenchmark, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter, benchmark, connection = tpch_sqlite
        opened = _record_stream_connections(monkeypatch)

        rows = adapter._execute_queries_by_type(benchmark, connection, _throughput_run_config())

        failures = [(row["stream_id"], row["query_id"], row.get("error")) for row in rows if row["status"] != "SUCCESS"]
        assert failures == []
        assert len(rows) == _NUM_STREAMS * 22
        result = adapter._last_throughput_test_result
        assert result.success is True
        assert result.streams_successful == _NUM_STREAMS
        assert len(opened) == _NUM_STREAMS
        assert len({id(stream_connection) for stream_connection in opened}) == _NUM_STREAMS
        assert all(stream_connection is not connection for stream_connection in opened)

    def test_throughput_sql_is_translated_to_the_sqlite_dialect(
        self, tpch_sqlite: tuple[SQLiteAdapter, TPCHBenchmark, Any]
    ) -> None:
        adapter, benchmark, connection = tpch_sqlite
        recording = _TPCHQueryOverride(benchmark, {})

        adapter._execute_queries_by_type(recording, connection, _throughput_run_config())

        assert len(recording.calls) == _NUM_STREAMS * 22
        assert {call["dialect"] for call in recording.calls} == {"sqlite"}
        untranslated = benchmark.get_query(1, seed=7)
        translated = benchmark.get_query(1, seed=7, dialect="sqlite")
        assert untranslated != translated

    def test_untranslated_sql_is_rejected_by_the_engine(
        self, tpch_sqlite: tuple[SQLiteAdapter, TPCHBenchmark, Any]
    ) -> None:
        _adapter, benchmark, connection = tpch_sqlite

        def rejected(dialect: str | None) -> list[int]:
            ids = []
            for query_id in range(1, 23):
                try:
                    connection.execute(benchmark.get_query(query_id, seed=7, dialect=dialect)).fetchall()
                except sqlite3.OperationalError:
                    ids.append(query_id)
            return ids

        assert rejected(None) != []
        assert rejected("sqlite") == []

    def test_failing_query_fails_the_stream_and_withholds_the_metric(
        self, tpch_sqlite: tuple[SQLiteAdapter, TPCHBenchmark, Any]
    ) -> None:
        adapter, benchmark, connection = tpch_sqlite
        injected = _TPCHQueryOverride(benchmark, {_FAILING_TPCH_QUERY: _FAILING_SQL})

        rows = adapter._execute_queries_by_type(injected, connection, _throughput_run_config())

        failed = [row for row in rows if row["status"] == "FAILED"]
        assert [row["query_id"] for row in failed] == [_FAILING_TPCH_QUERY] * _NUM_STREAMS
        assert all("table_that_does_not_exist" in row["error"] for row in failed)
        result = adapter._last_throughput_test_result
        assert result.success is False
        assert result.streams_successful == 0
        assert all(stream.queries_failed == 1 for stream in result.stream_results)
        assert result.throughput_at_size is None

    def test_in_memory_database_is_refused_before_streams_are_submitted(self) -> None:
        adapter = SQLiteAdapter(database_path=":memory:")
        connection = adapter.create_connection()
        try:
            with pytest.raises(RuntimeError, match="file-backed"):
                require_throughput_stream_capability(adapter, platform_name="SQLite", connection=connection)
        finally:
            connection.close()


class TestTPCHMaintenanceOnSQLite:
    @pytest.fixture(autouse=True)
    def _reuse_generated_refresh_sets(
        self, tpch_refresh_sets: dict[int, TPCHRefreshUpdateSet], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def generate(
            scale_factor: float, first_set: int, count: int, output_dir: Path
        ) -> dict[int, TPCHRefreshUpdateSet]:
            assert scale_factor == _SCALE_FACTOR
            return {set_id: replace(tpch_refresh_sets[set_id]) for set_id in range(first_set, first_set + count)}

        monkeypatch.setattr("benchbox.core.tpch.maintenance_test.generate_refresh_update_sets", generate)

    @staticmethod
    def _run(
        adapter: SQLiteAdapter, benchmark: TPCHBenchmark, connection: Any, output_dir: Path
    ) -> list[dict[str, Any]]:
        return adapter._execute_queries_by_type(
            benchmark,
            connection,
            {
                "benchmark_name": "tpch",
                "test_execution_type": "maintenance",
                "scale_factor": _SCALE_FACTOR,
                "output_dir": str(output_dir),
            },
        )

    @pytest.mark.parametrize("in_memory", [True, False], ids=["in_memory", "file_backed"])
    def test_refresh_functions_succeed(
        self, tpch_data: tuple[TPCHBenchmark, Path], tmp_path: Path, in_memory: bool
    ) -> None:
        benchmark, data_dir = tpch_data
        adapter = SQLiteAdapter(database_path=":memory:" if in_memory else str(tmp_path / "maintenance.sqlite"))
        connection = _load_tpch(adapter, benchmark, data_dir)
        try:
            rows = self._run(adapter, benchmark, connection, tmp_path / "maintenance")
        finally:
            connection.close()

        assert [(row["query_id"], row["status"]) for row in rows] == [("RF1", "SUCCESS"), ("RF2", "SUCCESS")]

    @pytest.mark.parametrize("in_memory", [True, False], ids=["in_memory", "file_backed"])
    def test_failed_refresh_function_row_carries_the_engine_error(
        self, tpch_data: tuple[TPCHBenchmark, Path], tmp_path: Path, in_memory: bool
    ) -> None:
        benchmark, data_dir = tpch_data
        adapter = SQLiteAdapter(database_path=":memory:" if in_memory else str(tmp_path / "maintenance.sqlite"))
        connection = _load_tpch(adapter, benchmark, data_dir)
        connection.execute(
            "CREATE TRIGGER block_orders BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT, 'insert blocked'); END"
        )
        connection.commit()
        try:
            rows = self._run(adapter, benchmark, connection, tmp_path / "maintenance")
        finally:
            connection.close()

        failed = [row for row in rows if row["status"] == "FAILED"]
        assert [row["query_id"] for row in failed] == ["RF1"]
        assert "insert blocked" in failed[0]["error"]


class TestHungStreamDeferredClose:
    def test_run_connection_stays_open_until_the_hung_stream_ends(
        self, tpch_sqlite: tuple[SQLiteAdapter, TPCHBenchmark, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, benchmark, _loaded = tpch_sqlite
        database_file = tmp_path / "hung.sqlite"
        database_file.write_bytes(Path(source.database_path).read_bytes())
        adapter = SQLiteAdapter(database_path=str(database_file))
        run_connection = adapter.create_connection()
        adapter.connection = run_connection
        release = threading.Event()
        original_register = sqlite_module._register_sqlite_compatibility_functions

        def register_with_hang(conn: sqlite3.Connection) -> None:
            original_register(conn)
            conn.create_function("strict_engine_hang", 0, lambda: 1 if release.wait(30) else 0)

        monkeypatch.setattr(sqlite_module, "_register_sqlite_compatibility_functions", register_with_hang)
        opened = _record_stream_connections(monkeypatch)
        hanging = _TPCHQueryOverride(benchmark, {_HUNG_TPCH_QUERY: _HANG_SQL})
        test = TPCHThroughputTest(
            benchmark=hanging,
            connection_factory=_stream_connection_factory(adapter, run_connection, "tpch", _SCALE_FACTOR),
            scale_factor=_SCALE_FACTOR,
            num_streams=2,
            dialect=adapter.get_target_dialect(),
        )
        config = TPCHThroughputTestConfig(scale_factor=_SCALE_FACTOR, num_streams=2, base_seed=7, stream_timeout=1)

        try:
            result = test.run(config)

            assert result.success is False
            assert sorted(result.outstanding_stream_ids) == [1, 2]
            adapter._last_throughput_test_result = result
            assert adapter._contain_outstanding_throughput_work({"stream_cleanup_timeout_seconds": 0.2}) is False
            adapter._close_run_connection()
            assert adapter.connection is None

            time.sleep(0.5)
            assert run_connection.execute("SELECT 1").fetchall() == [(1,)]
            assert len(opened) == 2
        finally:
            release.set()

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                run_connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("run connection was never closed after the hung streams ended")
        for stream_connection in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                stream_connection.execute("SELECT 1")


class TestTPCDSThroughputOnSQLite:
    @staticmethod
    def _run(
        adapter: SQLiteAdapter,
        benchmark: Any,
        connection: Any,
        num_streams: int = 2,
        scale_factor: float = _SCALE_FACTOR,
    ):
        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=_stream_connection_factory(adapter, connection, "tpcds", scale_factor),
            scale_factor=scale_factor,
            num_streams=num_streams,
            dialect=adapter.get_target_dialect(),
        )
        config = TPCDSThroughputTestConfig(
            scale_factor=scale_factor, num_streams=num_streams, base_seed=7, queries_per_stream=4
        )
        return test.run(config)

    def test_answer_set_row_counts_do_not_fail_seeded_streams_in_exact_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "exact")
        adapter = SQLiteAdapter(database_path=str(tmp_path / "tpcds_sf1.sqlite"))
        connection = adapter.create_connection()
        benchmark = TPCDSBenchmark(scale_factor=1.0, output_dir=str(tmp_path / "data"))
        adapter.create_schema(benchmark, connection)

        try:
            result = self._run(adapter, benchmark, connection, scale_factor=1.0)
        finally:
            connection.close()

        assert result.success is True
        assert [stream.queries_failed for stream in result.stream_results] == [0, 0]
        assert all(stream.queries_executed == 4 for stream in result.stream_results)

    def test_failing_query_is_counted_failed_and_withholds_the_metric(
        self, tpcds_sqlite: tuple[SQLiteAdapter, TPCDSBenchmark, Any]
    ) -> None:
        adapter, benchmark, connection = tpcds_sqlite

        result = self._run(adapter, _FirstTPCDSQueryFails(benchmark), connection)

        assert result.success is False
        assert result.throughput_at_size == 0.0
        failed_streams = [stream for stream in result.stream_results if stream.queries_failed]
        assert len(failed_streams) == 1
        assert failed_streams[0].queries_failed == 1
        failed_query = next(query for query in failed_streams[0].query_results if not query["success"])
        assert "table_that_does_not_exist" in failed_query["error"]
        assert [stream.success for stream in failed_streams] == [False]
        assert result.streams_successful == 1
