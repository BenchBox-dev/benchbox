from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestQueryExecution:
    def test_creation(self):
        from benchbox.experimental.load_testing.executor import QueryExecution

        qe = QueryExecution(
            query_id="Q1",
            stream_id=0,
            start_time=100.0,
            end_time=100.05,
            success=True,
            rows_returned=42,
        )
        assert qe.query_id == "Q1"
        assert qe.success is True
        assert qe.rows_returned == 42
        assert qe.queue_wait_time == 0.0

    def test_with_error(self):
        from benchbox.experimental.load_testing.executor import QueryExecution

        qe = QueryExecution(
            query_id="Q2",
            stream_id=1,
            start_time=200.0,
            end_time=200.1,
            success=False,
            error="timeout",
        )
        assert qe.success is False
        assert qe.error == "timeout"


class TestStreamResult:
    def test_success_rate(self):
        from benchbox.experimental.load_testing.executor import StreamResult

        sr = StreamResult(
            stream_id=0,
            queries_executed=10,
            queries_succeeded=8,
            queries_failed=2,
            total_time_seconds=5.0,
        )
        assert sr.success_rate == 80.0

    def test_success_rate_zero_queries(self):
        from benchbox.experimental.load_testing.executor import StreamResult

        sr = StreamResult(
            stream_id=0,
            queries_executed=0,
            queries_succeeded=0,
            queries_failed=0,
            total_time_seconds=0.0,
        )
        assert sr.success_rate == 0.0

    def test_throughput(self):
        from benchbox.experimental.load_testing.executor import StreamResult

        sr = StreamResult(
            stream_id=0,
            queries_executed=20,
            queries_succeeded=20,
            queries_failed=0,
            total_time_seconds=4.0,
        )
        assert sr.throughput == 5.0

    def test_throughput_zero_time(self):
        from benchbox.experimental.load_testing.executor import StreamResult

        sr = StreamResult(
            stream_id=0,
            queries_executed=5,
            queries_succeeded=5,
            queries_failed=0,
            total_time_seconds=0.0,
        )
        assert sr.throughput == 0.0


class TestConcurrentLoadConfig:
    def test_defaults(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=lambda: None,
            execute_query=lambda conn, sql: (True, 1, None),
        )
        assert config.queries_per_stream == 10
        assert config.query_timeout_seconds == 300.0
        assert config.collect_resource_metrics is True
        assert config.track_queue_times is True


class TestConcurrentLoadResult:
    def test_success_rate(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadResult

        result = ConcurrentLoadResult(
            start_time=0.0,
            end_time=10.0,
            total_duration_seconds=10.0,
            streams=[],
            total_streams_executed=2,
            total_streams_succeeded=2,
            total_queries_executed=20,
            total_queries_succeeded=18,
            total_queries_failed=2,
            overall_throughput=2.0,
        )
        assert result.success_rate == 90.0

    def test_success_rate_zero(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadResult

        result = ConcurrentLoadResult(
            start_time=0.0,
            end_time=0.0,
            total_duration_seconds=0.0,
            streams=[],
            total_streams_executed=0,
            total_streams_succeeded=0,
            total_queries_executed=0,
            total_queries_succeeded=0,
            total_queries_failed=0,
            overall_throughput=0.0,
        )
        assert result.success_rate == 0.0

    def test_get_percentile_latency(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadResult, QueryExecution, StreamResult

        executions = [
            QueryExecution(
                query_id=f"Q{i}", stream_id=0, start_time=float(i), end_time=float(i) + 0.1 * (i + 1), success=True
            )
            for i in range(10)
        ]
        sr = StreamResult(
            stream_id=0,
            queries_executed=10,
            queries_succeeded=10,
            queries_failed=0,
            total_time_seconds=5.0,
            query_executions=executions,
        )
        result = ConcurrentLoadResult(
            start_time=0.0,
            end_time=5.0,
            total_duration_seconds=5.0,
            streams=[sr],
            total_streams_executed=1,
            total_streams_succeeded=1,
            total_queries_executed=10,
            total_queries_succeeded=10,
            total_queries_failed=0,
            overall_throughput=2.0,
        )
        p50 = result.get_percentile_latency(50)
        assert p50 > 0.0
        p99 = result.get_percentile_latency(99)
        assert p99 >= p50

    def test_get_percentile_latency_empty(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadResult

        result = ConcurrentLoadResult(
            start_time=0.0,
            end_time=0.0,
            total_duration_seconds=0.0,
            streams=[],
            total_streams_executed=0,
            total_streams_succeeded=0,
            total_queries_executed=0,
            total_queries_succeeded=0,
            total_queries_failed=0,
            overall_throughput=0.0,
        )
        assert result.get_percentile_latency(50) == 0.0


class TestConcurrentLoadExecutor:
    def _make_config(self, *, queries_per_stream=2, duration=0.3, concurrency=1):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig
        from benchbox.experimental.load_testing.patterns import SteadyPattern

        conn_mock = MagicMock()
        conn_mock.close = MagicMock()

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=lambda: conn_mock,
            execute_query=lambda conn, sql: (True, 1, None),
            pattern=SteadyPattern(concurrency, duration),
            queries_per_stream=queries_per_stream,
            collect_resource_metrics=False,
            track_queue_times=True,
        )
        return config

    def test_run_basic(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadExecutor

        config = self._make_config()
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()
        assert result.total_queries_executed > 0
        assert result.total_queries_succeeded > 0
        assert result.total_duration_seconds > 0
        assert result.pattern_name == "SteadyPattern"

    def test_run_with_failures(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig, ConcurrentLoadExecutor
        from benchbox.experimental.load_testing.patterns import SteadyPattern

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=lambda: MagicMock(),
            execute_query=lambda conn, sql: (False, 0, "simulated error"),
            pattern=SteadyPattern(1, 0.3),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()
        assert result.total_queries_failed > 0

    def test_run_connection_factory_failure(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig, ConcurrentLoadExecutor
        from benchbox.experimental.load_testing.patterns import SteadyPattern

        def bad_factory():
            raise ConnectionError("connection refused")

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=bad_factory,
            execute_query=lambda conn, sql: (True, 1, None),
            pattern=SteadyPattern(1, 0.3),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_streams_executed > 0
        errored = [s for s in result.streams if s.error is not None]
        assert len(errored) > 0

    def test_run_query_execution_exception(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig, ConcurrentLoadExecutor
        from benchbox.experimental.load_testing.patterns import SteadyPattern

        def exploding_execute(conn, sql):
            raise RuntimeError("query exploded")

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=lambda: MagicMock(),
            execute_query=exploding_execute,
            pattern=SteadyPattern(1, 0.3),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()
        assert result.total_queries_failed > 0

    def test_queue_metrics(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadExecutor

        config = self._make_config(concurrency=2, duration=0.5, queries_per_stream=3)
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert isinstance(result.queue_metrics, dict)

    def test_resource_monitoring(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadConfig, ConcurrentLoadExecutor
        from benchbox.experimental.load_testing.patterns import SteadyPattern

        config = ConcurrentLoadConfig(
            query_factory=lambda i: (f"Q{i}", f"SELECT {i}"),
            connection_factory=lambda: MagicMock(),
            execute_query=lambda conn, sql: (True, 1, None),
            pattern=SteadyPattern(1, 0.5),
            queries_per_stream=2,
            collect_resource_metrics=True,
            resource_sample_interval=0.1,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert isinstance(result.resource_metrics, dict)

    def test_calculate_queue_metrics_empty(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadExecutor

        config = self._make_config()
        executor = ConcurrentLoadExecutor(config)

        metrics = executor._calculate_queue_metrics()
        assert metrics == {}

    def test_calculate_resource_metrics_empty(self):
        from benchbox.experimental.load_testing.executor import ConcurrentLoadExecutor

        config = self._make_config()
        executor = ConcurrentLoadExecutor(config)
        metrics = executor._calculate_resource_metrics()
        assert metrics == {}


class TestJoinOrderGenerator:
    def test_init_defaults(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        assert gen.scale_factor == 0.001
        assert gen.force_regenerate is False
        assert gen.quiet is False

    def test_init_verbose_bool(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, verbose=True, output_dir=tmp_path)
        assert gen.verbose_level == 1
        assert gen.verbose_enabled is True

    def test_init_verbose_int(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, verbose=2, output_dir=tmp_path)
        assert gen.verbose_level == 2
        assert gen.very_verbose is True

    def test_init_quiet_overrides_verbose(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, verbose=2, quiet=True, output_dir=tmp_path)
        assert gen.verbose_enabled is False
        assert gen.very_verbose is False

    def test_get_table_row_count(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        count = gen.get_table_row_count("title")
        assert count == int(500_000 * 0.001)
        assert gen.get_table_row_count("nonexistent") == 0

    def test_get_total_size_estimate(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        size = gen.get_total_size_estimate()
        assert size > 0

    def test_generate_lookup_tables(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = gen._generate_lookup_tables()
        assert "kind_type" in data
        assert len(data["kind_type"]) == 7
        assert "company_type" in data
        assert len(data["company_type"]) == 4
        assert "info_type" in data
        assert "role_type" in data
        assert len(data["role_type"]) == 12
        assert "comp_cast_type" in data
        assert "link_type" in data
        assert len(data["link_type"]) == 18

    def test_generate_titles(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        titles = gen._generate_titles(10)
        assert len(titles) == 10

        assert len(titles[0]) == 12
        assert titles[0][0] == 1
        assert isinstance(titles[0][1], str)

    def test_generate_names(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        names = gen._generate_names(10)
        assert len(names) == 10
        assert len(names[0]) == 9
        assert names[0][0] == 1

    def test_generate_companies(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        companies = gen._generate_companies(10)
        assert len(companies) == 10
        assert len(companies[0]) == 7

    def test_generate_keywords(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        keywords = gen._generate_keywords(30)
        assert len(keywords) == 30
        assert [row[0] for row in keywords] == list(range(1, 31))

        seeds = set(JoinOrderGenerator._SEED_KEYWORDS_EXTRA)
        assert keywords[0][1] in {"action", *seeds}
        assert keywords[29][1] in {"keyword_30", *seeds}

    def test_generate_character_names(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        chars = gen._generate_character_names(10)
        assert len(chars) == 10
        assert chars[0][1] == "John Doe"
        assert chars[9][1] == "Character 10"

    def test_generate_cast_info(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        cast = gen._generate_cast_info(20, max_name_id=100, max_title_id=50, max_char_id=30)
        assert len(cast) == 20
        assert len(cast[0]) == 7

        for row in cast:
            assert 1 <= row[1] <= 100
            assert 1 <= row[2] <= 50
            assert 1 <= row[6] <= 12

    def test_generate_movie_companies(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        mc = gen._generate_movie_companies(20, max_title_id=50, max_company_id=30)
        assert len(mc) == 20
        assert len(mc[0]) == 5

    def test_generate_movie_info(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        mi = gen._generate_movie_info(20, max_title_id=50)
        assert len(mi) == 20
        assert len(mi[0]) == 5

    def test_generate_movie_info_idx(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        mi_idx = gen._generate_movie_info_idx(20, max_title_id=50)
        assert len(mi_idx) == 20
        assert len(mi_idx[0]) == 5

    def test_generate_movie_keyword(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        mk = gen._generate_movie_keyword(20, max_title_id=50, max_keyword_id=30)
        assert len(mk) == 20
        assert len(mk[0]) == 3

    def test_generate_dimension_tables(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        lookup = gen._generate_lookup_tables()
        dims = gen._generate_dimension_tables(lookup)
        assert "title" in dims
        assert "name" in dims
        assert "company_name" in dims
        assert "keyword" in dims
        assert "char_name" in dims
        assert len(dims["title"]) == int(500_000 * 0.001)

    def test_generate_relationship_tables(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.0001, output_dir=tmp_path)
        lookup = gen._generate_lookup_tables()
        dims = gen._generate_dimension_tables(lookup)
        rels = gen._generate_relationship_tables(dims)
        assert "cast_info" in rels
        assert "movie_companies" in rels
        assert "movie_info" in rels
        assert "movie_info_idx" in rels
        assert "movie_keyword" in rels

    def test_write_table_data(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = [(1, "value with, comma", None), (2, 'value with "quote"', 42)]
        file_path = gen._write_table_data("test_table", data)
        assert file_path.exists()
        content = file_path.read_text()
        assert "1" in content
        assert '"value with, comma"' in content

    def test_write_manifest(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        gen._manifest_row_counts = {"test": 100}
        test_file = tmp_path / "test.csv"
        test_file.write_text("data")
        gen._write_manifest({"test": test_file})

        manifest_files = list(tmp_path.glob("*.json"))
        assert len(manifest_files) > 0

    def test_write_manifest_empty(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        gen._write_manifest({})

        manifest_files = list(tmp_path.glob("*.json"))
        assert len(manifest_files) == 0

    def test_generate_data_local(self, tmp_path):
        from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

        gen = JoinOrderGenerator(scale_factor=0.0001, output_dir=tmp_path)
        result = gen._generate_data_local(tmp_path)
        assert isinstance(result, dict)
        assert len(result) > 0

        for table_name, path in result.items():
            assert path.exists(), f"Missing file for table {table_name}"


