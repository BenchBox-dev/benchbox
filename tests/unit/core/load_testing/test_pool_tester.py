# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import threading
import time

import pytest

from benchbox.experimental.load_testing.pool_tester import (
    ConnectionAttempt,
    ConnectionPoolTester,
    PoolTestConfig,
    PoolTestResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.fixture
def mock_pool_connection():

    class MockPoolConnection:
        connection_count = 0
        max_connections = 10
        current_connections = 0
        lock = threading.Lock()

        def __init__(self):
            with MockPoolConnection.lock:
                if MockPoolConnection.current_connections >= MockPoolConnection.max_connections:
                    raise RuntimeError("Connection pool exhausted")
                MockPoolConnection.current_connections += 1
                MockPoolConnection.connection_count += 1
            self.closed = False

        def close(self):
            if not self.closed:
                self.closed = True
                with MockPoolConnection.lock:
                    MockPoolConnection.current_connections -= 1

    MockPoolConnection.connection_count = 0
    MockPoolConnection.current_connections = 0

    return MockPoolConnection


@pytest.fixture
def mock_pool_factory(mock_pool_connection):

    def factory():
        return mock_pool_connection()

    return factory


@pytest.fixture
def mock_pool_execute():

    def execute(connection, sql):
        return True

    return execute


class TestConnectionAttempt:
    def test_basic_creation(self):
        attempt = ConnectionAttempt(
            attempt_id=1,
            start_time=1000.0,
            end_time=1001.0,
            success=True,
            connection_time_ms=1000.0,
        )
        assert attempt.attempt_id == 1
        assert attempt.success is True

    def test_failed_attempt(self):
        attempt = ConnectionAttempt(
            attempt_id=2,
            start_time=1000.0,
            end_time=1001.0,
            success=False,
            error="Connection refused",
        )
        assert attempt.success is False
        assert attempt.error == "Connection refused"


class TestPoolTestConfig:
    def test_default_values(self, mock_pool_factory):
        config = PoolTestConfig(connection_factory=mock_pool_factory)
        assert config.max_connections_to_test == 100
        assert config.health_check_query == "SELECT 1"
        assert config.ramp_step_size == 10

    def test_custom_values(self, mock_pool_factory, mock_pool_execute):
        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=50,
            ramp_step_size=5,
        )
        assert config.max_connections_to_test == 50
        assert config.ramp_step_size == 5


class TestPoolTestResult:
    def test_basic_creation(self):
        result = PoolTestResult(
            total_attempts=100,
            successful_connections=95,
            failed_connections=5,
            success_rate=95.0,
            min_connect_time_ms=10,
            max_connect_time_ms=500,
            avg_connect_time_ms=50,
            p95_connect_time_ms=200,
            p99_connect_time_ms=400,
            max_concurrent_connections=20,
            estimated_pool_size=20,
            pool_exhaustion_detected=False,
            exhaustion_threshold=None,
        )
        assert result.success_rate == 95.0
        assert result.estimated_pool_size == 20


class TestConnectionPoolTester:
    def test_pool_limit_test(self, mock_pool_factory, mock_pool_execute, mock_pool_connection):
        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=15,
            ramp_step_size=5,
            hold_connection_seconds=0.1,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert isinstance(result, PoolTestResult)
        assert result.total_attempts > 0
        assert result.max_concurrent_connections <= mock_pool_connection.max_connections

    def test_detects_pool_exhaustion(self, mock_pool_connection):
        mock_pool_connection.max_connections = 5

        def factory():
            return mock_pool_connection()

        config = PoolTestConfig(
            connection_factory=factory,
            max_connections_to_test=20,
            ramp_step_size=3,
            hold_connection_seconds=0.5,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.pool_exhaustion_detected is True
        assert result.estimated_pool_size <= 5

    def test_pool_under_load(self, mock_pool_factory, mock_pool_execute, mock_pool_connection):
        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=20,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_under_load(concurrency=5, duration_seconds=0.5)

        assert result.total_attempts > 0
        assert result.success_rate >= 0

    def test_connection_churn(self, mock_pool_factory, mock_pool_execute):
        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=mock_pool_execute,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_connection_churn(
            connections_per_second=5,
            duration_seconds=0.5,
        )

        assert result.total_attempts > 0

    def test_connection_time_tracking(self, mock_pool_factory, mock_pool_execute):
        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=10,
            ramp_step_size=5,
            hold_connection_seconds=0.1,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.min_connect_time_ms >= 0
        assert result.max_connect_time_ms >= result.min_connect_time_ms
        assert result.avg_connect_time_ms >= 0

    def test_recommendations_generated(self, mock_pool_connection):
        mock_pool_connection.max_connections = 3

        def factory():
            return mock_pool_connection()

        config = PoolTestConfig(
            connection_factory=factory,
            max_connections_to_test=20,
            ramp_step_size=5,
            hold_connection_seconds=0.1,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert len(result.recommendations) > 0


class TestConnectionPoolTesterEdgeCases:
    def test_immediate_connection_failure(self):

        def failing_factory():
            raise RuntimeError("Cannot connect")

        config = PoolTestConfig(
            connection_factory=failing_factory,
            max_connections_to_test=5,
            ramp_step_size=2,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.failed_connections > 0
        assert result.success_rate == 0

    def test_slow_connection(self, mock_pool_execute):

        def slow_factory():
            time.sleep(0.1)

            class SlowConn:
                def close(self):
                    pass

            return SlowConn()

        config = PoolTestConfig(
            connection_factory=slow_factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=3,
            ramp_step_size=1,
            hold_connection_seconds=0.05,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.avg_connect_time_ms >= 100

    def test_health_check_failure(self, mock_pool_factory):

        def failing_execute(connection, sql):
            return False

        config = PoolTestConfig(
            connection_factory=mock_pool_factory,
            execute_query=failing_execute,
            max_connections_to_test=5,
            ramp_step_size=2,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.failed_connections > 0

    def test_connection_close_error(self, mock_pool_execute):

        class BadCloseConn:
            def close(self):
                raise RuntimeError("Close failed")

        def factory():
            return BadCloseConn()

        config = PoolTestConfig(
            connection_factory=factory,
            execute_query=mock_pool_execute,
            max_connections_to_test=5,
            ramp_step_size=2,
            hold_connection_seconds=0.05,
        )
        tester = ConnectionPoolTester(config)
        result = tester.test_pool_limits()

        assert result.total_attempts > 0


class TestAcquireErrorCapture:
    def test_non_empty_exception_message_captured(self):
        def factory():
            raise RuntimeError("pool exhausted")

        tester = ConnectionPoolTester(PoolTestConfig(connection_factory=factory))
        attempt = tester._acquire_connection(1)

        assert attempt.success is False
        assert attempt.error == "pool exhausted"

    def test_empty_exception_message_falls_back_to_repr(self):
        class EmptyError(Exception):
            def __str__(self) -> str:
                return ""

        def factory():
            raise EmptyError()

        tester = ConnectionPoolTester(PoolTestConfig(connection_factory=factory))
        attempt = tester._acquire_connection(2)

        assert attempt.success is False
        assert attempt.error, "attempt.error must never be empty"
        assert "EmptyError" in attempt.error


