from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.tpcds.streams import StreamQuery
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.streams import TPCHStreams
from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig
from benchbox.core.validation.query_validation import (
    QueryValidator,
    _stream_seed_override_warned,
    clear_reference_seed_context,
    set_reference_seed_context,
    set_validation_mode_context,
)
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection, require_throughput_stream_capability
from benchbox.platforms.sqlite import SQLiteAdapter
from benchbox.utils.dialect_utils import SQLTranslationError, UnknownSqlDialectError, sql_translation_context

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_SNAPSHOT = Path(__file__).parents[2] / "platforms" / "throughput_session_capability_snapshot.json"
_SEED = 20260709


def _throughput_capable_platforms() -> list[str]:
    snapshot = json.loads(_SNAPSHOT.read_text())
    return sorted(
        key
        for key, record in snapshot.items()
        if record["status"] == "resolved" and record["capability"] != "unsupported"
    )


def _dialect_of(platform_key: str) -> str:
    adapter_class = PlatformRegistry.get_adapter_class(platform_key)
    return adapter_class.get_target_dialect(adapter_class.__new__(adapter_class))


class _RecordingBenchmark:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_query(self, query_id: int, **kwargs: Any) -> str:
        self.calls.append({"query_id": query_id, **kwargs})
        return f"SELECT {query_id}"


class TestTPCHThroughputDialect:
    def test_pregenerated_and_inline_queries_receive_the_target_dialect_and_stream_id(self) -> None:
        benchmark = _RecordingBenchmark()
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=2, dialect="sqlite")
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, base_seed=_SEED)

        test._pregenerate_stream_queries(config)
        pregenerated_calls = list(benchmark.calls)
        benchmark.calls.clear()
        test._resolve_query_text(None, 0, 2, _SEED, 5, config)

        assert len(pregenerated_calls) == 2 * 22
        for call in pregenerated_calls + benchmark.calls:
            assert call["dialect"] == "sqlite"
            assert call["params"] == {"stream_id": call["params"]["stream_id"]}
            assert call["params"]["stream_id"] in (1, 2)
            assert "stream_id" not in call

    def test_stream_id_beyond_the_permutation_matrix_wraps_like_the_permutation(self) -> None:
        benchmark = _RecordingBenchmark()
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, dialect="duckdb")
        stream_id = len(TPCHStreams.PERMUTATION_MATRIX) + 3

        test._get_stream_query(1, 7, stream_id, 0.01)

        assert benchmark.calls[0]["params"] == {"stream_id": 3}

    @pytest.mark.parametrize("platform_key", _throughput_capable_platforms())
    def test_stream_sql_equals_power_path_sql_for_the_same_query_seed_and_stream(self, platform_key: str) -> None:
        dialect = _dialect_of(platform_key)
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=2, dialect=dialect)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, base_seed=_SEED)

        streams = test._pregenerate_stream_queries(config)

        for stream_id, stream_sql in streams.items():
            permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id]
            for position, query_id in enumerate(permutation):
                seed = _SEED + stream_id + stream_id * 1000 + position
                power_sql = benchmark.get_query(
                    query_id, seed=seed, stream_id=stream_id, scale_factor=0.01, dialect=dialect
                )
                assert stream_sql[position] == power_sql, f"{platform_key} stream {stream_id} query {query_id}"

    @pytest.mark.parametrize("dialect", ["duckdb", "spark", "mysql", "doris", "sqlite"])
    def test_translation_changes_the_sql_for_dialects_that_need_it(self, dialect: str) -> None:
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=1, dialect=dialect)
        untranslated = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=1)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1, base_seed=_SEED)

        assert test._pregenerate_stream_queries(config) != untranslated._pregenerate_stream_queries(config)


class TestTPCHPregenerationTranslationContext:
    def test_pregeneration_records_one_outcome_per_stream_query(self) -> None:
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=3, dialect="sqlite")
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=3, base_seed=_SEED)

        with sql_translation_context(strict=False) as outcomes:
            test._pregenerate_stream_queries(config)

        assert len(outcomes) == 3 * 22
        assert {outcome.status for outcome in outcomes} == {"success"}

    def test_unknown_dialect_is_never_translated_or_recorded(self) -> None:
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(
            benchmark=benchmark, connection_factory=lambda: None, num_streams=2, dialect="no_such_dialect"
        )
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, base_seed=_SEED)

        for strict in (True, False):
            with sql_translation_context(strict=strict) as outcomes:
                streams = test._pregenerate_stream_queries(config)
            assert outcomes == []
            assert all(isinstance(entry, UnknownSqlDialectError) for sql_list in streams.values() for entry in sql_list)
            with pytest.raises(UnknownSqlDialectError):
                test._resolve_query_text(streams[1], 0, 1, _SEED, 1, config)

    def test_strict_translation_fails_the_query_when_pregeneration_falls_back(self) -> None:
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=2, dialect="duckdb")
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, base_seed=_SEED)

        with patch("benchbox._vendor.sqlglot.parse", side_effect=ValueError("synthetic parse failure")):
            with sql_translation_context(strict=True) as outcomes:
                streams = test._pregenerate_stream_queries(config)

        assert len(outcomes) == 2 * 22
        assert {outcome.status for outcome in outcomes} == {"failed"}
        assert all(isinstance(entry, SQLTranslationError) for sql_list in streams.values() for entry in sql_list)
        with pytest.raises(SQLTranslationError):
            test._resolve_query_text(streams[1], 0, 1, _SEED, 1, config)

    def test_non_strict_translation_falls_back_and_records_the_fallback(self) -> None:
        benchmark = TPCHBenchmark(scale_factor=0.01)
        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=lambda: None, num_streams=1, dialect="duckdb")
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1, base_seed=_SEED)

        with patch("benchbox._vendor.sqlglot.parse", side_effect=ValueError("synthetic parse failure")):
            with sql_translation_context(strict=False) as outcomes:
                streams = test._pregenerate_stream_queries(config)

        assert {outcome.status for outcome in outcomes} == {"fallback"}
        assert all(isinstance(entry, str) for entry in streams[1])


class TestTPCDSStreamSeedValidationPolicy:
    @pytest.fixture(autouse=True)
    def _exact_mode(self) -> Any:
        set_validation_mode_context(ValidationMode.EXACT)
        yield
        set_validation_mode_context(None)
        clear_reference_seed_context()

    def test_exact_mode_row_count_mismatch_fails_without_a_stream_seed_context(self) -> None:
        result = QueryValidator().validate_query_result("tpcds", "3", 70, scale_factor=1.0, stream_id=0)

        assert result.is_valid is False
        assert result.validation_mode is ValidationMode.EXACT

    def test_stream_seeded_queries_are_excluded_from_answer_set_row_counts(self) -> None:
        set_reference_seed_context(False)

        result = QueryValidator().validate_query_result("tpcds", "3", 70, scale_factor=1.0, stream_id=0)

        assert result.is_valid is True
        assert result.validation_mode is ValidationMode.SKIP
        assert "excluded" in result.warning_message

    def test_tpch_non_reference_seed_policy_is_unchanged(self) -> None:
        set_reference_seed_context(False)

        result = QueryValidator().validate_query_result("tpch", "1", 0, scale_factor=1.0, stream_id=0)

        assert result.is_valid is False


class TestTPCDSFailedPlatformResult:
    @staticmethod
    def _connection_factory() -> Any:
        adapter = SQLiteAdapter(database_path=":memory:")

        def factory() -> PlatformAdapterConnection:
            return PlatformAdapterConnection(sqlite3.connect(":memory:", check_same_thread=False), adapter)

        return factory

    @staticmethod
    def _benchmark_with_sql(monkeypatch: pytest.MonkeyPatch, *statements: str) -> Any:
        class _Benchmark:
            def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
                return query

        monkeypatch.setattr(
            "benchbox.core.tpcds.streams.generate_dsqgen_streams",
            lambda **_kwargs: {
                1: [StreamQuery(stream_id=1, position=i, query_id=i + 1, sql=sql) for i, sql in enumerate(statements)]
            },
        )
        return _Benchmark()

    def _run(self, benchmark: Any) -> Any:
        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=self._connection_factory(),
            scale_factor=1.0,
            num_streams=1,
            dialect="sqlite",
        )
        return test.run(TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=1, base_seed=7))

    def test_invalid_query_on_a_real_adapter_connection_fails_the_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        benchmark = self._benchmark_with_sql(monkeypatch, "SELECT * FROM no_such_table")

        result = self._run(benchmark)

        stream = result.stream_results[0]
        assert stream.queries_failed == 1
        assert stream.queries_successful == 0
        assert stream.success is False
        assert "no_such_table" in stream.query_results[0]["error"]
        assert result.success is False
        assert result.throughput_at_size == 0.0

    def test_one_failed_query_makes_the_stream_unsuccessful_even_with_many_successes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        benchmark = self._benchmark_with_sql(monkeypatch, *(["SELECT 1"] * 9 + ["SELECT * FROM no_such_table"]))

        result = self._run(benchmark)

        stream = result.stream_results[0]
        assert (stream.queries_successful, stream.queries_failed) == (9, 1)
        assert stream.success is False
        assert result.success is False
        assert result.throughput_at_size == 0.0

    def test_valid_query_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        benchmark = self._benchmark_with_sql(monkeypatch, "SELECT 1")

        result = self._run(benchmark)

        assert result.stream_results[0].success is True
        assert result.success is True
        assert result.throughput_at_size > 0


class TestSQLiteStreamConnections:
    def test_each_stream_gets_its_own_connection_to_the_database_file(self, tmp_path: Path) -> None:
        adapter = SQLiteAdapter(database_path=str(tmp_path / "streams.sqlite"))
        shared = adapter.create_connection()
        shared.execute("CREATE TABLE t (id INTEGER)")
        shared.execute("INSERT INTO t VALUES (1)")
        shared.commit()

        first = adapter.new_stream_connection(shared, benchmark_type="olap")
        second = adapter.new_stream_connection(shared, benchmark_type="olap")
        try:
            assert len({id(shared), id(first), id(second)}) == 3
            assert first.execute("SELECT id FROM t").fetchall() == [(1,)]
            first.close()
            assert second.execute("SELECT id FROM t").fetchall() == [(1,)]
            assert shared.execute("SELECT id FROM t").fetchall() == [(1,)]
        finally:
            second.close()
            shared.close()

    def test_in_memory_database_is_refused_for_throughput_and_shared_for_single_stream_use(self) -> None:
        adapter = SQLiteAdapter(database_path=":memory:")
        shared = adapter.create_connection()
        shared.execute("CREATE TABLE t (id INTEGER)")
        shared.execute("INSERT INTO t VALUES (1)")
        try:
            with pytest.raises(RuntimeError, match="file-backed"):
                adapter.ensure_stream_sessions_supported(shared)
            with pytest.raises(RuntimeError, match="file-backed"):
                require_throughput_stream_capability(adapter, platform_name="SQLite", connection=shared)
            handle = adapter.new_stream_connection(shared, benchmark_type="olap")
            assert handle is not shared
            assert handle.execute("SELECT id FROM t").fetchall() == [(1,)]
            handle.close()
            assert shared.execute("SELECT id FROM t").fetchall() == [(1,)]
        finally:
            shared.close()

    def test_support_check_follows_the_live_connection_not_the_configured_path(self, tmp_path: Path) -> None:
        adapter = SQLiteAdapter()
        connection = adapter.create_connection(database_path=str(tmp_path / "live.sqlite"))
        try:
            adapter.ensure_stream_sessions_supported(connection)
            require_throughput_stream_capability(adapter, platform_name="SQLite", connection=connection)
        finally:
            connection.close()


class TestStreamSeedOverrideWarning:
    @pytest.fixture(autouse=True)
    def _reset(self) -> Any:
        _stream_seed_override_warned.clear()
        set_reference_seed_context(False)
        yield
        clear_reference_seed_context()
        set_validation_mode_context(None)
        _stream_seed_override_warned.clear()

    @pytest.mark.parametrize("mode", [ValidationMode.EXACT, ValidationMode.LOOSE])
    def test_requested_mode_override_is_warned_once(
        self, mode: ValidationMode, caplog: pytest.LogCaptureFixture
    ) -> None:
        set_validation_mode_context(mode)

        with caplog.at_level(logging.WARNING, logger="benchbox.core.validation.query_validation"):
            for query_id in ("1", "2", "3"):
                QueryValidator().validate_query_result("tpcds", query_id, 5, scale_factor=1.0, stream_id=0)

        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert mode.value in warnings[0].getMessage()
        assert "stream-seeded" in warnings[0].getMessage()

    def test_default_skip_mode_does_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.validation.query_validation"):
            QueryValidator().validate_query_result("tpcds", "1", 5, scale_factor=1.0, stream_id=0)

        assert [record for record in caplog.records if record.levelno == logging.WARNING] == []

    def test_each_throughput_run_resets_the_once_only_record(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "exact")
        monkeypatch.setattr(
            "benchbox.core.tpcds.streams.generate_dsqgen_streams",
            lambda **_kwargs: {1: [StreamQuery(stream_id=1, position=0, query_id=1, sql="SELECT 1")]},
        )

        class _Benchmark:
            def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
                return query

        def run_once() -> None:
            connection = sqlite3.connect(":memory:", check_same_thread=False)

            def factory() -> PlatformAdapterConnection:
                wrapper = PlatformAdapterConnection(connection, SQLiteAdapter(database_path=":memory:"))
                wrapper.benchmark_type = "tpcds"
                wrapper.scale_factor = 1.0
                return wrapper

            test = TPCDSThroughputTest(
                benchmark=_Benchmark(), connection_factory=factory, scale_factor=1.0, num_streams=1, dialect="sqlite"
            )
            test.run(TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=1, base_seed=7))

        with caplog.at_level(logging.WARNING, logger="benchbox.core.validation.query_validation"):
            run_once()
            run_once()

        assert len([record for record in caplog.records if "stream-seeded" in record.getMessage()]) == 2
