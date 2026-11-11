# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.operations.base import OperationExecutor
from benchbox.platforms.base import PlatformAdapter, StreamConnectionCapability
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _MockOpAdapter(PlatformAdapter):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR

    def add_cli_arguments(self):
        pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    def get_target_dialect(self) -> str | None:
        return None

    def get_platform_info(self, connection=None):
        return {"platform_type": "mock", "platform_name": "Mock Platform"}

    def create_connection(self, **connection_config):
        return Mock()

    def create_schema(self, benchmark, connection):
        return 0.1

    def load_data(self, benchmark, connection, data_dir):
        return {"table1": 100}, 0.5, None

    def configure_for_benchmark(self, connection, benchmark_type):
        pass

    def execute_query(self, connection, query, query_id, **kwargs):
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.1,
            "rows_returned": 10,
        }

    def apply_platform_optimizations(self, platform_config, connection):
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        pass


class _FakeOperationBenchmark(OperationExecutor):
    def __init__(self):
        self.seen_connections: list[Any] = []

    def execute_operation(self, operation_id: str, connection: Any, **kwargs: Any) -> Any:
        self.seen_connections.append(connection)
        return SimpleNamespace(
            status="SUCCESS",
            success=True,
            write_duration_ms=4.0,
            rows_affected=1,
            error=None,
            validation_duration_ms=0.0,
            validation_passed=True,
            cleanup_duration_ms=0.0,
            skip_reason=None,
        )

    def get_all_operations(self) -> dict[str, Any]:
        return {}

    def get_operation_categories(self) -> list[str]:
        return []


class _DbapiStyleConnection:
    def cursor(self):
        return Mock()


class TestExecuteOperationQueryConnectionAdaptation:
    def test_execute_capable_connection_passes_through_unwrapped(self):
        adapter = _MockOpAdapter()
        benchmark = _FakeOperationBenchmark()
        raw = Mock()
        result = adapter._execute_operation_query(benchmark, raw, "op_1")
        assert benchmark.seen_connections == [raw]
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1

    def test_connection_without_execute_is_wrapped(self):
        adapter = _MockOpAdapter()
        benchmark = _FakeOperationBenchmark()
        raw = _DbapiStyleConnection()
        assert not hasattr(raw, "execute")
        result = adapter._execute_operation_query(benchmark, raw, "op_1")
        (seen,) = benchmark.seen_connections
        assert isinstance(seen, PlatformAdapterConnection)
        assert seen.connection is raw
        assert result["status"] == "SUCCESS"

    def test_client_style_handle_without_execute_is_wrapped(self):

        adapter = _MockOpAdapter()
        benchmark = _FakeOperationBenchmark()

        class _ClientStyle:
            def query(self, sql):
                raise AssertionError("must go through the wrapper, not the raw client")

        raw = _ClientStyle()
        result = adapter._execute_operation_query(benchmark, raw, "op_1")
        (seen,) = benchmark.seen_connections
        assert isinstance(seen, PlatformAdapterConnection)
        assert result["status"] == "SUCCESS"


