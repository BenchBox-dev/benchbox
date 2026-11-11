from __future__ import annotations

import time
from threading import Lock
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.throughput_test import (
    TPCDSThroughputTest,
    TPCDSThroughputTestConfig,
)
from benchbox.core.tpch.throughput_test import (
    TPCHThroughputTest,
    TPCHThroughputTestConfig,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


class TestTimingMeasurementAccuracy:
    def test_tpch_ttt_excludes_setup_overhead(self):

        execution_times = []
        execution_lock = Lock()

        def mock_get_query(*args, **kwargs):
            with execution_lock:
                execution_times.append(time.time())
            return "SELECT 1"

        benchmark = Mock()
        benchmark.get_query = mock_get_query

        connection_creation_times = []
        connection_lock = Lock()

        def connection_factory():
            with connection_lock:
                connection_creation_times.append(time.time())

            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []
            conn.execute.return_value = cursor
            conn.close.return_value = None
            return conn

        config = TPCHThroughputTestConfig(
            scale_factor=1.0,
            num_streams=2,
            stream_timeout=10,
            verbose=False,
        )

        test = TPCHThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        test_start = time.time()
        result = test.run(config)
        test_end = time.time()

        assert result.success
        assert result.streams_executed == 2
        assert len(result.stream_results) == 2

        wall_clock_time = test_end - test_start
        measured_ttt = result.total_time

        assert measured_ttt > 0
        assert measured_ttt <= wall_clock_time

        max_stream_duration = max(sr.duration for sr in result.stream_results)

        assert abs(measured_ttt - max_stream_duration) < 0.5

    def test_tpcds_ttt_excludes_setup_overhead(self):

        execution_times = []
        execution_lock = Lock()

        def mock_get_query(*args, **kwargs):
            with execution_lock:
                execution_times.append(time.time())
            return "SELECT 1"

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []
            conn.execute.return_value = cursor
            conn.close.return_value = None
            conn.commit.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query = mock_get_query
        benchmark.get_queries.return_value = {"1": "SELECT 1", "2": "SELECT 2"}
        benchmark.query_manager = Mock()

        config = TPCDSThroughputTestConfig(
            scale_factor=1.0,
            num_streams=2,
            stream_timeout=10,
            verbose=False,
            queries_per_stream=2,
            enable_preflight=False,
        )

        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        from unittest.mock import patch

        with patch("benchbox.core.tpcds.streams.create_standard_streams") as mock_create:
            mock_manager = Mock()
            mock_streams = {}
            for stream_id in range(3):
                mock_streams[stream_id] = [
                    Mock(stream_id=stream_id, query_id=1, position=1, variant=None, sql="SELECT 1"),
                    Mock(stream_id=stream_id, query_id=2, position=2, variant=None, sql="SELECT 2"),
                ]
            mock_manager.generate_streams.return_value = mock_streams
            mock_create.return_value = mock_manager

            test_start = time.time()
            result = test.run(config)
            test_end = time.time()

        assert result.success
        assert result.streams_executed == 2

        wall_clock_time = test_end - test_start
        measured_ttt = result.total_time

        assert measured_ttt > 0
        assert measured_ttt <= wall_clock_time

        if result.stream_results:
            max_stream_duration = max(sr.duration for sr in result.stream_results)
            assert abs(measured_ttt - max_stream_duration) < 0.5


class TestGenerationExcludedFromTiming:
    GENERATION_SLEEP = 0.3

    def test_tpch_generation_time_excluded_from_timing(self):

        def slow_get_query(*args, **kwargs):
            time.sleep(self.GENERATION_SLEEP)
            return "SELECT 1"

        benchmark = Mock()
        benchmark.get_query = slow_get_query

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []
            conn.execute.return_value = cursor
            conn.close.return_value = None
            return conn

        config = TPCHThroughputTestConfig(
            scale_factor=0.01,
            num_streams=1,
            stream_timeout=30,
            verbose=False,
        )

        test = TPCHThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        result = test.run(config)

        assert result.success
        assert result.stream_results[0].queries_executed == 22

        total_generation_time = 22 * self.GENERATION_SLEEP
        assert result.total_time < total_generation_time
        for query_result in result.stream_results[0].query_results:
            assert query_result["execution_time_seconds"] < self.GENERATION_SLEEP

    def test_tpcds_generation_time_excluded_from_timing(self):

        def slow_get_query(*args, **kwargs):
            time.sleep(self.GENERATION_SLEEP)
            return "SELECT 1"

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []
            conn.execute.return_value = cursor
            conn.close.return_value = None
            conn.commit.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query = slow_get_query
        benchmark.get_queries.return_value = {"1": "SELECT 1", "2": "SELECT 2", "3": "SELECT 3"}
        benchmark.query_manager = Mock()

        config = TPCDSThroughputTestConfig(
            scale_factor=1.0,
            num_streams=1,
            stream_timeout=30,
            verbose=False,
            queries_per_stream=3,
            enable_preflight=True,
        )

        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        from unittest.mock import patch

        with patch("benchbox.core.tpcds.streams.create_standard_streams") as mock_create:
            mock_manager = Mock()
            mock_manager.generate_streams.return_value = {
                0: [
                    Mock(stream_id=0, query_id=1, position=0, variant=None, sql="SELECT 1"),
                    Mock(stream_id=0, query_id=2, position=1, variant=None, sql="SELECT 2"),
                    Mock(stream_id=0, query_id=3, position=2, variant=None, sql="SELECT 3"),
                ]
            }
            mock_create.return_value = mock_manager

            result = test.run(config)

        assert result.success
        assert result.stream_results[0].queries_executed == 3

        total_generation_time = 3 * self.GENERATION_SLEEP
        assert result.total_time < total_generation_time
        for query_result in result.stream_results[0].query_results:
            assert query_result["execution_time_seconds"] < self.GENERATION_SLEEP


class TestConnectionCleanup:
    def test_tpch_connection_closed_on_query_failure(self):

        connections_created = []
        connections_closed = []
        creation_lock = Lock()
        close_lock = Lock()

        def connection_factory():
            conn = Mock()

            with creation_lock:
                connections_created.append(conn)

            original_close = conn.close

            def tracked_close():
                with close_lock:
                    connections_closed.append(conn)
                return original_close()

            conn.close = tracked_close

            def failing_execute(*args, **kwargs):
                raise RuntimeError("Simulated query failure")

            conn.execute = failing_execute

            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"

        config = TPCHThroughputTestConfig(
            scale_factor=1.0,
            num_streams=2,
            stream_timeout=5,
            verbose=False,
        )

        test = TPCHThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        result = test.run(config)

        assert not result.success

        assert len(connections_created) == 2

        deadline = time.time() + 0.5
        while len(connections_closed) < 2 and time.time() < deadline:
            time.sleep(0.01)

        assert len(connections_closed) == 2

        assert set(connections_created) == set(connections_closed)

    def test_tpcds_connection_closed_on_stream_failure(self):

        connections_created = []
        connections_closed = []
        creation_lock = Lock()
        close_lock = Lock()

        def connection_factory():
            conn = Mock()

            with creation_lock:
                connections_created.append(conn)

            original_close = conn.close

            def tracked_close():
                with close_lock:
                    connections_closed.append(conn)
                return original_close()

            conn.close = tracked_close

            def failing_execute(*args, **kwargs):
                raise RuntimeError("Simulated failure")

            conn.execute = failing_execute
            conn.commit = Mock()

            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"
        benchmark.get_queries.return_value = {"1": "SELECT 1"}
        benchmark.query_manager = Mock()

        config = TPCDSThroughputTestConfig(
            scale_factor=1.0,
            num_streams=2,
            stream_timeout=5,
            verbose=False,
            queries_per_stream=1,
            enable_preflight=False,
        )

        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        from unittest.mock import patch

        with patch("benchbox.core.tpcds.streams.create_standard_streams") as mock_create:
            mock_manager = Mock()
            mock_streams = {
                0: [Mock(stream_id=0, query_id=1, position=1, variant=None, sql="SELECT 1")],
                1: [Mock(stream_id=1, query_id=1, position=1, variant=None, sql="SELECT 1")],
            }
            mock_manager.generate_streams.return_value = mock_streams
            mock_create.return_value = mock_manager

            result = test.run(config)

        assert result.streams_executed == 2

        assert len(connections_created) == 2

        deadline = time.time() + 0.5
        while len(connections_closed) < 2 and time.time() < deadline:
            time.sleep(0.01)

        assert len(connections_closed) == 2
        assert set(connections_created) == set(connections_closed)


class TestTimeoutDetectionAndCooperativeCancellation:
    def test_tpch_timed_out_stream_is_surfaced_as_leaked(self):

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []
            call_count = {"n": 0}

            def slow_execute(_query_text):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    time.sleep(1.5)
                return cursor

            conn.execute.side_effect = slow_execute
            conn.close.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"

        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1, stream_timeout=1, verbose=False)
        test = TPCHThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        result = test.run(config)

        assert result.success is False
        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert any("timed out" in e.lower() and "leaked" in e.lower() for e in result.errors)

    def test_tpch_cooperative_cancel_stops_a_hung_stream_promptly(self):
        executed_queries: list[str] = []
        lock = Lock()

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []

            def slow_execute(query_text):
                with lock:
                    executed_queries.append(query_text)
                time.sleep(0.3)
                return cursor

            conn.execute.side_effect = slow_execute
            conn.close.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"

        config = TPCHThroughputTestConfig(
            scale_factor=0.01,
            num_streams=1,
            stream_timeout=1,
            cancel_on_timeout=True,
            verbose=False,
        )
        test = TPCHThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        start = time.time()
        result = test.run(config)
        elapsed = time.time() - start

        assert elapsed < 3.0
        assert len(executed_queries) < 22
        assert result.success is False

    def test_tpcds_timed_out_stream_is_surfaced_as_leaked(self):
        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []

            def slow_execute(_query_text):
                time.sleep(1.5)
                return cursor

            conn.execute.side_effect = slow_execute
            conn.close.return_value = None
            conn.commit.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"
        benchmark.get_queries.return_value = {"1": "SELECT 1"}
        benchmark.query_manager = Mock()

        config = TPCDSThroughputTestConfig(
            scale_factor=1.0,
            num_streams=1,
            stream_timeout=1,
            verbose=False,
            queries_per_stream=1,
            enable_preflight=False,
        )

        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        with patch("benchbox.core.tpcds.streams.create_standard_streams") as mock_create:
            mock_manager = Mock()
            mock_manager.generate_streams.return_value = {
                stream_id: [Mock(stream_id=stream_id, query_id=1, position=1, variant=None, sql="SELECT 1")]
                for stream_id in range(2)
            }
            mock_create.return_value = mock_manager

            result = test.run(config)

        assert result.success is False
        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert any("timed out" in e.lower() and "leaked" in e.lower() for e in result.errors)

    def test_tpcds_cooperative_cancel_stops_a_hung_stream_promptly(self):
        executed_queries: list[str] = []
        lock = Lock()

        def connection_factory():
            conn = Mock()
            cursor = Mock()
            cursor.fetchall.return_value = []

            def slow_execute(query_text):
                with lock:
                    executed_queries.append(query_text)
                time.sleep(0.3)
                return cursor

            conn.execute.side_effect = slow_execute
            conn.close.return_value = None
            conn.commit.return_value = None
            return conn

        benchmark = Mock()
        benchmark.get_query.return_value = "SELECT 1"
        benchmark.get_queries.return_value = {str(i): f"SELECT {i}" for i in range(1, 11)}
        benchmark.query_manager = Mock()

        config = TPCDSThroughputTestConfig(
            scale_factor=1.0,
            num_streams=1,
            stream_timeout=1,
            cancel_on_timeout=True,
            verbose=False,
            queries_per_stream=10,
            enable_preflight=False,
        )

        test = TPCDSThroughputTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
            verbose=config.verbose,
        )

        with patch("benchbox.core.tpcds.streams.create_standard_streams") as mock_create:
            mock_manager = Mock()
            mock_manager.generate_streams.return_value = {
                0: [
                    Mock(stream_id=0, query_id=i, position=i - 1, variant=None, sql=f"SELECT {i}") for i in range(1, 11)
                ],
            }
            mock_create.return_value = mock_manager

            start = time.time()
            result = test.run(config)
            elapsed = time.time() - start

        assert elapsed < 3.0
        assert len(executed_queries) < 10
        assert result.success is False


