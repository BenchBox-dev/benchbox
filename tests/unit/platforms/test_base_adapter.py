# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import inspect
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.builder import benchmark_family as _benchmark_family, normalize_benchmark_id
from benchbox.core.schemas import QueryResult
from benchbox.platforms.base import (
    BenchmarkResults,
    ConnectionConfig,
    PlatformAdapter,
    PlatformAdapterConnection,
    StreamConnectionCapability,
)
from benchbox.platforms.base.execution import _power_query_result, _power_test_error_result
from benchbox.utils.dialect_utils import SQLTranslationError, SqlTranslationOutcome
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class MockPlatformAdapter(PlatformAdapter):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR

    def add_cli_arguments(self):
        pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    def get_target_dialect(self) -> str | None:
        return None

    def get_platform_info(self, connection=None):
        return {
            "platform_type": "mock",
            "platform_name": "Mock Platform",
            "platform_version": "1.0.0",
            "connection_mode": "test",
            "host": None,
            "port": None,
            "configuration": {},
            "client_library_version": "1.0.0",
            "embedded_library_version": None,
        }

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

    def apply_table_tunings(self, table_tuning, connection):
        pass

    def generate_tuning_clause(self, table_tuning):
        return ""

    def apply_unified_tuning(self, unified_config, connection):
        pass

    def apply_platform_optimizations(self, platform_config, connection):
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        pass


class MockPlatformAdapterWithDialect(MockPlatformAdapter):
    def get_target_dialect(self) -> str:
        return "mock_dialect"

    def get_platform_info(self, connection=None):
        return {
            "platform_type": "mock_dialect",
            "platform_name": "Mock Platform with Dialect",
            "platform_version": "1.0.0",
            "connection_mode": "test",
            "host": None,
            "port": None,
            "configuration": {"dialect": "mock_dialect"},
            "client_library_version": "1.0.0",
            "embedded_library_version": None,
        }

    def run_power_test(self, benchmark, **kwargs):
        return {
            "test_type": "power",
            "total_execution_time": 5.0,
            "query_count": 22,
            "successful_queries": 22,
            "failed_queries": 0,
            "query_results": [
                {
                    "query_id": f"q{i}",
                    "status": "SUCCESS",
                    "execution_time_seconds": 0.2,
                    "rows_returned": 100,
                }
                for i in range(1, 23)
            ],
            "geometric_mean": 0.2,
            "validation_status": "PASSED",
        }

    def run_throughput_test(self, benchmark, **kwargs):
        stream_count = kwargs.get("stream_count", 2)
        return {
            "test_type": "throughput",
            "stream_count": stream_count,
            "total_execution_time": 10.0,
            "aggregate_query_time": 18.0,
            "queries_per_hour": 7920,
            "stream_results": [
                {
                    "stream_id": i,
                    "execution_time": 9.0,
                    "query_count": 22,
                    "successful_queries": 22,
                    "failed_queries": 0,
                }
                for i in range(stream_count)
            ],
            "validation_status": "PASSED",
        }

    def run_maintenance_test(self, benchmark, **kwargs):
        return {
            "test_type": "maintenance",
            "operations_executed": 4,
            "total_execution_time": 2.5,
            "operation_results": [
                {
                    "operation": "INSERT",
                    "status": "SUCCESS",
                    "execution_time": 0.5,
                    "rows_affected": 1000,
                },
                {
                    "operation": "UPDATE",
                    "status": "SUCCESS",
                    "execution_time": 0.8,
                    "rows_affected": 500,
                },
                {
                    "operation": "DELETE",
                    "status": "SUCCESS",
                    "execution_time": 0.7,
                    "rows_affected": 200,
                },
                {
                    "operation": "REFRESH",
                    "status": "SUCCESS",
                    "execution_time": 0.5,
                    "rows_affected": 0,
                },
            ],
            "concurrent_query_impact": 0.1,
            "data_integrity_status": "PASSED",
            "validation_status": "PASSED",
        }

    def apply_table_tunings(self, table_tuning, connection):
        pass

    def generate_tuning_clause(self, table_tuning):
        return ""

    def apply_unified_tuning(self, unified_config, connection):
        pass

    def apply_platform_optimizations(self, platform_config, connection):
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        pass


class BenchmarkWithVersionAwareQueries:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def get_queries(self, dialect: str | None = None, platform_version: str | None = None) -> dict[str, str]:
        self.calls.append(
            {
                "dialect": dialect,
                "platform_version": platform_version,
            }
        )
        return {"Q1": "SELECT 1"}


class BenchmarkWithStrictTranslationFailure:
    def get_queries(self, dialect: str | None = None, base_dialect: str | None = None) -> dict[str, str]:
        if dialect is None:
            return {"Q1": "SELECT fallback"}
        outcome = SqlTranslationOutcome(
            source_dialect=base_dialect or "ansi",
            target_dialect=dialect,
            translator="test",
            status="failed",
            strict_mode=True,
            error_category="translation_failed",
        )
        raise SQLTranslationError("strict translation failed", outcome)


class BenchmarkWithUnexpectedTranslationFailure:
    def get_queries(self, dialect: str | None = None, base_dialect: str | None = None) -> dict[str, str]:
        if dialect is None:
            return {"Q1": "SELECT fallback"}
        raise ValueError("unexpected translation failure")


class TestPowerResultConversionHelpers:
    def test_power_query_result_preserves_warmup_fields(self):
        result = _power_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 1.25,
                "success": True,
                "result_count": 10,
                "stream_id": 3,
                "position": 7,
            },
            stream_id=99,
            iteration=0,
            run_type="warmup",
        )

        assert result == {
            "query_id": "Q1",
            "execution_time_seconds": 1.25,
            "status": "SUCCESS",
            "rows_returned": 10,
            "test_type": "power",
            "stream_id": 3,
            "position": 7,
            "iteration": 0,
            "run_type": "warmup",
        }

    def test_power_query_result_preserves_measurement_failure_fields(self):
        result = _power_query_result(
            {
                "query_id": "Q2",
                "execution_time_seconds": 0.0,
                "success": False,
                "error": "boom",
            },
            stream_id=5,
            iteration=2,
            run_type="measurement",
        )

        assert result == {
            "query_id": "Q2",
            "execution_time_seconds": 0.0,
            "status": "FAILED",
            "rows_returned": 0,
            "test_type": "power",
            "stream_id": 5,
            "position": 0,
            "iteration": 2,
            "run_type": "measurement",
            "error": "boom",
        }

    def test_power_error_sentinel_keeps_optional_iteration_context(self):
        assert _power_test_error_result("factory failed", iteration=4, run_type="measurement") == {
            "query_id": "power_test_error",
            "execution_time_seconds": 0.0,
            "status": "FAILED",
            "rows_returned": 0,
            "error": "factory failed",
            "test_type": "power",
            "iteration": 4,
            "run_type": "measurement",
        }
        assert _power_test_error_result("init failed") == {
            "query_id": "power_test_error",
            "execution_time_seconds": 0.0,
            "status": "FAILED",
            "rows_returned": 0,
            "error": "init failed",
            "test_type": "power",
        }


class TestConnectionConfig:
    def test_default_values(self):
        config = ConnectionConfig()
        assert config.host is None
        assert config.port is None
        assert config.auth_method == "password"
        assert config.ssl_enabled is False
        assert config.pool_size == 5
        assert config.max_overflow == 10
        assert config.pool_timeout == 30

    def test_env_value_resolution(self):
        config = ConnectionConfig(password="${TEST_PASSWORD}")

        with patch.dict("os.environ", {"TEST_PASSWORD": "secret123"}):
            assert config.get_env_value("password") == "secret123"

    def test_env_value_no_expansion(self):
        config = ConnectionConfig(password="regular_password")
        assert config.get_env_value("password") == "regular_password"

    def test_env_value_missing_variable(self):
        config = ConnectionConfig(password="${MISSING_VAR}")
        assert config.get_env_value("password", "default") == "default"


class TestPlatformAdapter:
    def test_initialization(self):
        adapter = MockPlatformAdapter()
        adapter.platform_config["test_param"] = "value"
        assert adapter.platform_config["test_param"] == "value"
        assert adapter.connection is None
        assert adapter.connection_pool is None
        assert adapter.dialect is None

    def test_external_table_capability_defaults_disabled(self):
        adapter = MockPlatformAdapter()
        assert adapter.supports_external_tables is False
        assert PlatformAdapter.supports_external_tables is False

    def test_create_external_tables_raises_by_default(self, tmp_path):
        adapter = MockPlatformAdapter()
        with pytest.raises(NotImplementedError, match="does not support external table mode"):
            adapter.create_external_tables(benchmark=Mock(), connection=Mock(), data_dir=tmp_path)

    def test_sql_translation_no_dialect(self):
        adapter = MockPlatformAdapter()
        sql = "SELECT * FROM table"
        assert adapter.translate_sql(sql) == sql

    def test_sql_translation_with_dialect(self):
        adapter = MockPlatformAdapter()
        adapter._dialect = "postgresql"

        result = adapter.translate_sql("SELECT * FROM orders", "duckdb")
        assert result == "SELECT * FROM orders;"

    @patch("benchbox._vendor.sqlglot.parse")
    def test_sql_translation_error_handling(self, mock_parse):
        adapter = MockPlatformAdapter()
        adapter._dialect = "postgresql"

        mock_parse.side_effect = Exception("Translation error")

        sql = "SELECT * FROM table"
        result = adapter.translate_sql(sql, "duckdb")
        assert result == sql

    def test_connection_test_success(self):
        adapter = MockPlatformAdapter()
        assert adapter.test_connection() is True

    def test_connection_test_failure(self):
        adapter = MockPlatformAdapter()

        adapter.create_connection = Mock(side_effect=Exception("Connection failed"))

        assert adapter.test_connection() is False

    def test_get_connection_from_pool_no_pool(self):
        adapter = MockPlatformAdapter()
        adapter.platform_config["test_param"] = "value"
        connection = adapter.get_connection_from_pool()
        assert connection is not None

    def test_get_connection_from_pool_with_pool(self):
        adapter = MockPlatformAdapter()
        mock_pool = Mock()
        mock_connection = Mock()
        mock_pool.get_connection.return_value = mock_connection
        adapter.connection_pool = mock_pool

        connection = adapter.get_connection_from_pool()
        assert connection == mock_connection
        mock_pool.get_connection.assert_called_once()

    def test_execute_all_queries_with_dialect_support(self):
        adapter = MockPlatformAdapterWithDialect()

        class DummyBenchmark:
            def __init__(self):
                self.calls = []

            def get_queries(self, dialect=None, base_dialect=None):
                self.calls.append((dialect, base_dialect))
                return {
                    "1": "SELECT * FROM table1 LIMIT 100",
                    "2": "SELECT * FROM table2 LIMIT 50",
                }

        dummy_benchmark = DummyBenchmark()
        mock_connection = Mock()
        run_config = {"benchmark_name": "tpch"}

        with patch("rich.console.Console"):
            results = adapter._execute_all_queries(dummy_benchmark, mock_connection, run_config)

        assert isinstance(results, list)
        assert dummy_benchmark.calls
        dialect, base_dialect = dummy_benchmark.calls[0]
        assert dialect == "mock_dialect"
        assert base_dialect == "netezza"

    def test_execute_all_queries_without_dialect_support(self):
        adapter = MockPlatformAdapter()

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {
            "1": "SELECT TOP 100 * FROM table1",
            "2": "SELECT TOP 50 * FROM table2",
        }
        mock_benchmark.get_platform_skip_queries.return_value = []

        mock_connection = Mock()

        run_config = {"benchmark_name": "tpch"}

        with patch("rich.console.Console"):
            results = adapter._execute_all_queries(mock_benchmark, mock_connection, run_config)

        mock_benchmark.get_queries.assert_called_once()
        args, kwargs = mock_benchmark.get_queries.call_args
        assert "dialect" not in kwargs
        assert isinstance(results, list)
        assert all(r.get("run_type") == "measurement" for r in results)

    def test_execute_single_query_applies_benchmark_materialized_result_oracle(self):
        class MaterializedAdapter(MockPlatformAdapter):
            def execute_query(self, connection, query, query_id, **kwargs):
                rows = [(1, 0.8), (2, 0.9)]
                return self._build_query_result_with_validation(
                    query_id=query_id,
                    execution_time=0.125,
                    actual_row_count=len(rows),
                    first_row=rows[0],
                    materialized_rows=rows,
                )

        class Benchmark:
            scale_factor = 0.01

            @staticmethod
            def validate_query_result(query_id, rows):
                from benchbox.core.vector_search.metrics import validate_search_result

                validate_search_result(query_id, rows)

        result = MaterializedAdapter()._execute_single_query(
            Benchmark(),
            connection=object(),
            query_id="q1",
            query_sql="SELECT 1",
            benchmark_type="vector_search",
        )

        assert result["status"] == "FAILED"
        assert result["execution_time_seconds"] == 0.125
        assert "not descending" in result["error"]

    def test_execute_all_queries_benchmark_no_dialect_parameter(self):
        adapter = MockPlatformAdapterWithDialect()

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {"1": "SELECT TOP 100 * FROM table1"}
        mock_benchmark.get_platform_skip_queries.return_value = []

        mock_connection = Mock()
        run_config = {"benchmark_name": "tpch"}

        with patch("inspect.signature") as mock_signature:
            mock_signature.return_value.parameters = {}

            with patch("rich.console.Console"):
                adapter._execute_all_queries(mock_benchmark, mock_connection, run_config)

            mock_benchmark.get_queries.assert_called_once()
            args, kwargs = mock_benchmark.get_queries.call_args
            assert "dialect" not in kwargs

    def test_execute_all_queries_dialect_exception_handling(self):
        adapter = MockPlatformAdapterWithDialect()

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {"1": "SELECT * FROM table1"}
        mock_benchmark.get_platform_skip_queries.return_value = []

        mock_connection = Mock()
        run_config = {"benchmark_name": "tpch"}

        with patch("inspect.signature", side_effect=Exception("Signature error")):
            with patch("rich.console.Console"):
                adapter._execute_all_queries(mock_benchmark, mock_connection, run_config)

            mock_benchmark.get_queries.assert_called_once()
            args, kwargs = mock_benchmark.get_queries.call_args
            assert "dialect" not in kwargs

    def test_execute_queries_by_type_sets_run_type_for_throughput(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"test_execution_type": "throughput"}

        adapter._execute_throughput_test = Mock(
            return_value=[{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1}]
        )

        results = adapter._execute_queries_by_type(benchmark, connection, run_config)
        assert results[0]["run_type"] == "measurement"

    def test_execute_queries_by_type_sets_run_type_for_power(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"test_execution_type": "power"}

        adapter._execute_power_test = Mock(
            return_value=[{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1}]
        )

        results = adapter._execute_queries_by_type(benchmark, connection, run_config)
        assert results[0]["run_type"] == "measurement"

    def test_execute_queries_by_type_sets_run_type_for_maintenance(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"test_execution_type": "maintenance"}

        adapter._execute_maintenance_test = Mock(
            return_value=[{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1}]
        )

        results = adapter._execute_queries_by_type(benchmark, connection, run_config)
        assert results[0]["run_type"] == "measurement"

    def test_execute_queries_by_type_combined_preserves_explicit_run_type(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"test_execution_type": "combined"}

        adapter._execute_combined_test = Mock(
            return_value=[
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "run_type": "warmup"},
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.09},
            ]
        )

        results = adapter._execute_queries_by_type(benchmark, connection, run_config)
        assert results[0]["run_type"] == "warmup"
        assert results[1]["run_type"] == "measurement"

    def test_ensure_query_results_run_type_infers_warmup_from_iteration(self):
        results = MockPlatformAdapter._ensure_query_results_run_type(
            [{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "iteration": 0}]
        )
        assert results[0]["run_type"] == "warmup"

    def test_ensure_query_results_run_type_infers_warmup_from_flag(self):
        results = MockPlatformAdapter._ensure_query_results_run_type(
            [{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "is_warmup": True}]
        )
        assert results[0]["run_type"] == "warmup"


class TestBenchmarkResults:
    def test_benchmark_results_creation(self):
        results = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="test_platform",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.5,
            total_queries=5,
            successful_queries=4,
            failed_queries=1,
            total_execution_time=8.0,
            average_query_time=2.0,
            data_loading_time=1.5,
            schema_creation_time=1.0,
            total_rows_loaded=1000,
            data_size_mb=10.5,
            table_statistics={"table1": 1000},
            validation_status="PASSED",
        )
        results.validation_details = None

        assert results.benchmark_name == "test_benchmark"
        assert results.platform == "test_platform"
        assert results.scale_factor == 1.0
        assert results.validation_status == "PASSED"
        assert results.validation_details is None


@pytest.fixture
def mock_benchmark():
    benchmark = Mock()
    benchmark.scale_factor = 1.0
    benchmark._name = "test_benchmark"
    benchmark.output_dir = Path("/tmp/test_data")
    benchmark.generate_data = Mock()
    benchmark.get_queries = Mock(return_value={"q1": "SELECT 1", "q2": "SELECT 2"})
    benchmark.get_create_tables_sql = Mock(return_value="CREATE TABLE test (id INT)")
    benchmark.get_platform_skip_queries = Mock(return_value=[])
    benchmark.tables = None
    benchmark._impl = None
    benchmark.create_enhanced_benchmark_result = Mock()
    return benchmark


class TestPlatformAdapterWorkflow:
    @pytest.mark.parametrize(
        "requested_type,effective_type,dry_run,capture_plans,expected_power_ms",
        [
            ("standard", "standard", False, False, 12000),
            ("power", "power", False, False, 12000),
            ("throughput", "power", False, False, 12000),
            ("combined", "power", False, False, 12000),
            ("maintenance", "power", False, False, 12000),
            ("combined", "combined", False, False, 2500),
            ("maintenance", "maintenance", False, False, 2500),
            ("throughput", "throughput", False, False, None),
            ("power", "power", True, False, 2500),
            ("power", "power", False, True, 12000),
        ],
    )
    def test_power_wall_boundary_preserves_query_metrics_and_probe_exclusion(
        self, mock_benchmark, tmp_path, requested_type, effective_type, dry_run, capture_plans, expected_power_ms
    ):
        from datetime import datetime, timedelta

        from benchbox.core.results.result_factory import build_enhanced_benchmark_result
        from benchbox.core.results.schema import build_result_payload

        adapter = MockPlatformAdapter()
        adapter.dry_run = dry_run
        adapter.capture_plans = capture_plans
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.benchmark_name = "ClickBench"
        mock_benchmark.compliance_class = None
        mock_benchmark.create_enhanced_benchmark_result.side_effect = lambda **kwargs: build_enhanced_benchmark_result(
            benchmark=mock_benchmark, **kwargs
        )
        clock = [100.0]
        epoch = datetime(2026, 9, 30, 10, 0, 0)
        boundaries = []
        rows = [
            {"query_id": "q1", "status": "SUCCESS", "execution_time_seconds": 2.0, "run_type": "warmup"},
            {"query_id": "q1", "status": "SUCCESS", "execution_time_seconds": 0.5, "run_type": "measurement"},
            {"query_id": "q2", "status": "FAILED", "execution_time_seconds": 1.0, "run_type": "measurement"},
        ]
        for row in rows:
            row["resource_usage"] = {"execution_time_seconds": row["execution_time_seconds"], "bytes_billed": 100}

        def execute(_benchmark, _connection, config):
            boundaries.append(clock[0])
            config["_effective_execution_type"] = effective_type
            for seconds in (3.0, 2.0, 0.5, 5.5, 1.0):
                clock[0] += seconds
            boundaries.append(clock[0])
            return rows

        def capture(_connection, _queries, _results):
            assert clock[0] == boundaries[1]
            assert adapter._plan_capture_phase_active is False
            clock[0] += 13.0

        def probe(_connection, _config):
            assert clock[0] == boundaries[1] + (13.0 if capture_plans else 0.0)
            clock[0] += 7.0
            return 7.0

        def definitions(*_args, **_kwargs):
            clock[0] += 4.0
            return {}

        with (
            patch("benchbox.platforms.base.adapter.mono_time", side_effect=lambda: clock[0]),
            patch("benchbox.platforms.base.adapter.elapsed_seconds", side_effect=lambda start: clock[0] - start),
            patch("benchbox.platforms.base.execution.mono_time", side_effect=lambda: clock[0]),
            patch("benchbox.platforms.base.execution.elapsed_seconds", side_effect=lambda start: clock[0] - start),
            patch("benchbox.platforms.base.execution.datetime") as wall_clock,
            patch.object(adapter, "_dispatch_queries_by_type", side_effect=execute),
            patch.object(adapter, "_capture_plans_post_measurement", side_effect=capture) as capture_phase,
            patch.object(adapter, "_collect_post_measurement_metadata", side_effect=probe),
            patch.object(adapter, "_get_dialect_queries", side_effect=definitions),
        ):
            wall_clock.now.side_effect = lambda: epoch + timedelta(seconds=clock[0] - 100.0)
            result = adapter.run_benchmark(mock_benchmark, test_execution_type=requested_type)

        phase = result.execution_phases.power_test
        if expected_power_ms is None:
            assert phase is None
        else:
            assert phase.duration_ms == expected_power_ms
            if effective_type in {"standard", "power"} and not dry_run:
                assert phase.start_time == epoch.isoformat()
                assert phase.end_time == (epoch + timedelta(seconds=12)).isoformat()
            assert phase.geometric_mean_time == 1.25
        payload = build_result_payload(result)
        assert payload["run"]["total_duration_ms"] == (29000 if capture_plans else 16000)
        assert capture_phase.call_count == int(capture_plans)
        assert payload["run"]["query_time_ms"] == 500
        assert payload["summary"]["timing"]["total_ms"] == 500
        assert [q["ms"] for q in payload["queries"]] == [2000, 500, 1000]
        assert [q["resource_usage"]["execution_time_seconds"] for q in result.query_results] == [2.0, 0.5, 1.0]
        assert payload["summary"]["cost"]["total_bytes_billed"] == 300
        if expected_power_ms is not None:
            assert payload["phases"]["power_test"]["duration_ms"] == expected_power_ms

    def test_run_benchmark_success(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        mock_benchmark.output_dir = tmp_path

        test_file = tmp_path / "test.csv"
        test_file.write_text("test,data\n1,value")

        mock_result = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="mock",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"test_table": 100},
        )
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_result

        result = adapter.run_benchmark(mock_benchmark)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_name == "test_benchmark"
        assert result.platform == "mock"
        assert result.total_queries == 2
        assert result.successful_queries == 2
        assert result.failed_queries == 0

    def test_run_benchmark_with_query_subset(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        mock_benchmark.output_dir = tmp_path

        mock_result = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="mock",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            total_execution_time=0.1,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"test_table": 100},
        )
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_result

        result = adapter.run_benchmark(mock_benchmark, query_subset=["q1"])

        assert result.total_queries == 1
        assert result.successful_queries == 1

    def test_run_benchmark_with_categories(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.get_queries_by_category = Mock(return_value={"q1": "SELECT 1"})

        mock_result = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="mock",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            total_execution_time=0.1,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"test_table": 100},
        )
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_result

        result = adapter.run_benchmark(mock_benchmark, categories=["category1"])

        assert result.total_queries == 1
        mock_benchmark.get_queries_by_category.assert_called_once_with("category1")

    def test_run_benchmark_with_no_tables(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.tables = None
        mock_benchmark._impl = None

        mock_result = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="mock",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"test_table": 100},
        )
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_result

        result = adapter.run_benchmark(mock_benchmark)

        mock_benchmark.generate_data.assert_not_called()
        assert isinstance(result, BenchmarkResults)

    def test_run_benchmark_query_failure(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        mock_benchmark.output_dir = tmp_path

        def mock_execute_query(connection, query, query_id):
            if query_id == "q1":
                raise Exception("Query failed")
            return {
                "query_id": query_id,
                "status": "SUCCESS",
                "execution_time_seconds": 0.1,
                "rows_returned": 10,
            }

        adapter.execute_query = mock_execute_query

        mock_result = make_benchmark_results(
            benchmark_name="test_benchmark",
            platform="mock",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=2,
            successful_queries=1,
            failed_queries=1,
            query_results=[
                {"query_id": "q1", "status": "FAILED", "error": "Query failed"},
                {"query_id": "q2", "status": "SUCCESS"},
            ],
            total_execution_time=0.1,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"test_table": 100},
        )
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_result

        result = adapter.run_benchmark(mock_benchmark)

        assert result.total_queries == 2
        assert result.successful_queries == 1
        assert result.failed_queries == 1

        failed_query = next(q for q in result.query_results if q["status"] == "FAILED")
        assert "error" in failed_query
        assert failed_query["query_id"] == "q1"


def test_collect_resource_utilization_without_psutil(monkeypatch):
    adapter = MockPlatformAdapter()

    original_import = __import__

    def fake_import(name, *args, **kwargs):  # pragma: no cover
        if name == "psutil":
            raise ImportError("psutil not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    snapshot = adapter._collect_resource_utilization()
    assert snapshot["available"] is False
    assert "cpu_count" in snapshot


def test_collect_resource_utilization_with_stub(monkeypatch):  # noqa: C901
    adapter = MockPlatformAdapter()

    mb = 1024 * 1024

    class DummyProcess:
        def __init__(self, *_args, **_kwargs):
            self.pid = 4321

        class _OneShot:
            def __init__(self, process):
                self._process = process

            def __enter__(self):  # pragma: no cover
                return self._process

            def __exit__(self, exc_type, exc, tb):  # pragma: no cover
                return False

        def oneshot(self):
            return DummyProcess._OneShot(self)

        def memory_full_info(self):
            return SimpleNamespace(rss=256 * mb, vms=512 * mb)

        def memory_percent(self):
            return 7.5

        def cpu_percent(self, interval=None):
            return 12.5

        def num_threads(self):
            return 8

        def num_fds(self):
            return 24

        def num_handles(self):  # pragma: no cover
            return 30

        def io_counters(self):
            return SimpleNamespace(read_bytes=25 * mb, write_bytes=10 * mb, read_count=120, write_count=45)

        def open_files(self):
            return [SimpleNamespace(path="/tmp/query.sql")]

        def num_ctx_switches(self):
            return SimpleNamespace(voluntary=5, involuntary=1)

        def create_time(self):
            return 1_700_000_000.0

        def name(self):
            return "dummy-process"

        def status(self):
            return "running"

    def cpu_percent(interval=None, percpu=False):
        if percpu:
            return [20.0, 22.0]
        return 21.5

    dummy_psutil = SimpleNamespace(
        cpu_count=lambda: 16,
        virtual_memory=lambda: SimpleNamespace(total=64 * mb, available=40 * mb, percent=37.5),
        swap_memory=lambda: SimpleNamespace(total=8 * mb, used=2 * mb, percent=25.0),
        cpu_percent=cpu_percent,
        getloadavg=lambda: (0.5, 0.4, 0.3),
        cpu_freq=lambda: SimpleNamespace(current=3200.0),
        disk_usage=lambda path: SimpleNamespace(total=120 * mb, used=60 * mb, free=60 * mb, percent=50.0),
        disk_io_counters=lambda: SimpleNamespace(
            read_bytes=600 * mb,
            write_bytes=150 * mb,
            read_count=1200,
            write_count=300,
        ),
        net_io_counters=lambda: SimpleNamespace(
            bytes_sent=800 * mb,
            bytes_recv=400 * mb,
            packets_sent=500,
            packets_recv=250,
        ),
        boot_time=lambda: 1_699_000_000.0,
        Process=lambda pid=None: DummyProcess(),
        __version__="6.0.0",
    )

    monkeypatch.setitem(sys.modules, "psutil", dummy_psutil)

    snapshot = adapter._collect_resource_utilization()
    assert snapshot["available"] is True
    assert snapshot["psutil_version"] == "6.0.0"
    assert snapshot["cpu_count"] == 16
    assert snapshot["cpu_percent"] == pytest.approx(21.5)
    assert snapshot["cpu"]["per_cpu_percent"] == [20.0, 22.0]
    assert snapshot["memory"]["total_mb"] == pytest.approx(64.0)
    assert snapshot["memory"]["used_mb"] == pytest.approx(24.0)
    assert snapshot["disk_io"]["read_mb"] == pytest.approx(600.0)
    assert snapshot["network_io"]["sent_mb"] == pytest.approx(800.0)
    assert snapshot["process_memory_mb"] == pytest.approx(256.0)
    assert snapshot["process_cpu_percent"] == pytest.approx(12.5)
    assert snapshot["process"]["num_threads"] == 8
    assert snapshot["process"]["io_counters"]["write_mb"] == pytest.approx(10.0)
    assert snapshot["process"]["context_switches"]["involuntary"] == 1


def test_summarize_performance_characteristics():
    adapter = MockPlatformAdapter()

    results = [
        QueryResult(
            query_id="q1",
            query_name="Q1",
            sql_text="SELECT 1",
            execution_time_ms=100.0,
            rows_returned=10,
            status="SUCCESS",
        ),
        QueryResult(
            query_id="q2",
            query_name="Q2",
            sql_text="SELECT 2",
            execution_time_ms=200.0,
            rows_returned=5,
            status="ERROR",
            error_message="boom",
        ),
    ]

    summary = adapter._summarize_performance_characteristics(
        results,
        total_duration=5.0,
        total_rows_loaded=123,
    )

    assert summary["total_queries"] == 2
    assert summary["successful_queries"] == 1
    assert summary["failed_queries"] == 1
    assert summary["rows_returned_total"] == 15
    assert summary["average_query_time_ms"] == pytest.approx(150.0)
    assert summary["average_success_query_time_ms"] == pytest.approx(100.0)
    assert summary["throughput_qps"] == pytest.approx(0.4)
    assert summary["successful_throughput_qps"] == pytest.approx(0.2)
    assert summary["rows_returned_per_second"] == pytest.approx(3.0)
    assert summary["rows_returned_average"] == pytest.approx(7.5)
    assert summary["total_rows_loaded"] == 123
    assert summary["success_rate"] == pytest.approx(0.5)
    assert summary["execution_time_stats"]["all"]["seconds"]["max"] == pytest.approx(0.2)
    assert summary["execution_time_stats"]["successful"]["milliseconds"]["min"] == pytest.approx(100.0)
    assert summary["rows_returned_stats"]["successful"]["min"] == pytest.approx(10.0)
    assert summary["error_breakdown"] == {"boom": 1}
    assert summary["failure_samples"][0]["status"] == "ERROR"
    assert summary["failure_samples"][0]["rows_returned"] == 5


def test_summarize_performance_characteristics_failure_samples_limited():
    adapter = MockPlatformAdapter()

    error_results = [
        QueryResult(
            query_id=f"q{i}",
            query_name=f"Q{i}",
            sql_text=f"SELECT {i}",
            execution_time_ms=50.0 + i,
            rows_returned=0,
            status="ERROR",
            error_message=f"err{i}",
        )
        for i in range(6)
    ]

    summary = adapter._summarize_performance_characteristics(
        error_results,
        total_duration=12.0,
        total_rows_loaded=0,
    )

    assert summary["failed_queries"] == 6
    assert len(summary["failure_samples"]) == 5
    assert summary["error_breakdown"]["err5"] == 1
    assert summary["throughput_qps"] == pytest.approx(0.5)


class TestApplyQuerySubsetNormalization:
    def test_query_subset_accepts_q_prefixed_ids(self):
        adapter = MockPlatformAdapter()
        queries = {"1": "SELECT 1", "37": "SELECT 37", "100": "SELECT 100"}
        result = adapter._apply_query_subset(queries, ["Q37", "Q1"], "test")
        assert list(result.keys()) == ["37", "1"]
        assert result["37"] == "SELECT 37"

    def test_query_subset_accepts_bare_integer_ids(self):
        adapter = MockPlatformAdapter()
        queries = {"1": "SELECT 1", "37": "SELECT 37"}
        result = adapter._apply_query_subset(queries, ["37", "1"], "test")
        assert list(result.keys()) == ["37", "1"]

    def test_query_subset_accepts_mixed_q_and_bare_ids(self):
        adapter = MockPlatformAdapter()
        queries = {"1": "SELECT 1", "37": "SELECT 37"}
        result = adapter._apply_query_subset(queries, ["Q37", "1"], "test")
        assert list(result.keys()) == ["37", "1"]

    def test_query_subset_lowercase_q_prefix_normalized(self):
        adapter = MockPlatformAdapter()
        queries = {"1": "SELECT 1"}
        result = adapter._apply_query_subset(queries, ["q1"], "test")
        assert list(result.keys()) == ["1"]

    def test_query_subset_invalid_id_still_rejected(self):
        adapter = MockPlatformAdapter()
        queries = {"1": "SELECT 1"}
        with pytest.raises(ValueError, match="Invalid query IDs"):
            adapter._apply_query_subset(queries, ["Q99"], "test")

    def test_query_subset_preserves_non_numeric_ids(self):
        adapter = MockPlatformAdapter()
        queries = {"q-aggregation-1": "SELECT 1"}
        result = adapter._apply_query_subset(queries, ["q-aggregation-1"], "test")
        assert list(result.keys()) == ["q-aggregation-1"]


class TestConsolidatedFunctionality:
    def test_tpc_methods_consolidation(self):
        adapter = MockPlatformAdapter()
        mock_benchmark = Mock(spec=[])
        mock_connection = Mock()
        assert not hasattr(mock_benchmark, "run_throughput_test")

        assert hasattr(adapter, "run_power_test")
        assert hasattr(adapter, "run_throughput_test")
        assert hasattr(adapter, "run_maintenance_test")

        with patch.object(adapter, "run_power_test") as mock_run_power_test:
            mock_run_power_test.return_value = {"test": "throughput"}

            result = adapter.run_throughput_test(mock_benchmark, connection=mock_connection, test_param="value")
            mock_run_power_test.assert_called_with(mock_benchmark, connection=mock_connection, test_param="value")
            assert result == {"test": "throughput"}

    def test_create_schema_with_tuning_helper(self):
        adapter = MockPlatformAdapterWithDialect()
        mock_benchmark = Mock()

        with patch.object(adapter, "get_effective_tuning_configuration") as mock_get_tuning:
            with patch.object(adapter, "get_target_dialect", return_value="postgres"):
                with patch.object(adapter, "translate_sql", return_value="translated_sql"):
                    mock_tuning_config = Mock()
                    mock_get_tuning.return_value = mock_tuning_config
                    mock_benchmark.get_create_tables_sql.return_value = "original_sql"

                    result = adapter._create_schema_with_tuning(mock_benchmark)

                    mock_benchmark.get_create_tables_sql.assert_called_with(
                        dialect="postgres", tuning_config=mock_tuning_config
                    )
                    adapter.translate_sql.assert_called_with("original_sql", "standard")
                    assert result == "translated_sql"

        adapter2 = MockPlatformAdapterWithDialect()
        mock_benchmark2 = Mock()

        with patch.object(adapter2, "get_effective_tuning_configuration") as mock_get_tuning:
            with patch.object(adapter2, "get_target_dialect", return_value="postgres"):
                with patch.object(adapter2, "translate_sql", return_value="fallback_sql"):
                    mock_tuning_config = Mock()
                    mock_get_tuning.return_value = mock_tuning_config

                    mock_benchmark2.get_create_tables_sql.side_effect = [
                        TypeError("Signature not supported"),
                        "fallback_sql_original",
                    ]

                    result = adapter2._create_schema_with_tuning(mock_benchmark2)

                    assert mock_benchmark2.get_create_tables_sql.call_count == 2
                    calls = mock_benchmark2.get_create_tables_sql.call_args_list
                    assert calls[0].kwargs == {
                        "dialect": "postgres",
                        "tuning_config": mock_tuning_config,
                    }
                    assert calls[1].args == ()
                    assert calls[1].kwargs == {}

                    adapter2.translate_sql.assert_called_with("fallback_sql_original", "standard")
                    assert result == "fallback_sql"

    def test_get_constraint_configuration_helper(self):
        adapter = MockPlatformAdapter()

        with patch.object(adapter, "get_effective_tuning_configuration") as mock_get_tuning:
            mock_tuning_config = Mock()
            mock_tuning_config.primary_keys.enabled = True
            mock_tuning_config.foreign_keys.enabled = False
            mock_get_tuning.return_value = mock_tuning_config

            primary_keys, foreign_keys = adapter._get_constraint_configuration()

            assert primary_keys is True
            assert foreign_keys is False

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            primary_keys, foreign_keys = adapter._get_constraint_configuration()

            assert primary_keys is False
            assert foreign_keys is False

    def test_log_constraint_configuration_helper(self):
        adapter = MockPlatformAdapter()

        with patch.object(adapter.logger, "info") as mock_info:
            with patch.object(adapter.logger, "debug") as mock_debug:
                adapter._log_constraint_configuration(True, True)

                info_messages = [args[0] for args, _ in mock_info.call_args_list]
                assert any("Primary key constraints" in msg for msg in info_messages)
                assert any("Foreign key constraints" in msg for msg in info_messages)
                mock_debug.assert_called_with(
                    "Schema constraints from tuning config: primary_keys=True, foreign_keys=True"
                )

        with patch.object(adapter.logger, "info") as mock_info:
            with patch.object(adapter.logger, "debug") as mock_debug:
                adapter._log_constraint_configuration(False, False)

                mock_info.assert_not_called()
                debug_messages = [args[0] for args, _ in mock_debug.call_args_list]
                assert any("No constraints enabled" in msg for msg in debug_messages)
                assert any(
                    "Schema constraints from tuning config: primary_keys=False, foreign_keys=False" in msg
                    for msg in debug_messages
                )

    def test_schema_creation_no_translation_needed(self):
        adapter = MockPlatformAdapterWithDialect()
        mock_benchmark = Mock()

        with patch.object(adapter, "get_effective_tuning_configuration", return_value=None):
            with patch.object(adapter, "get_target_dialect", return_value="duckdb"):
                with patch.object(adapter, "translate_sql") as mock_translate:
                    mock_benchmark.get_create_tables_sql.return_value = "schema_sql"

                    result = adapter._create_schema_with_tuning(mock_benchmark, source_dialect="duckdb")

                    mock_translate.assert_not_called()
                    assert result == "schema_sql"


class TestTimestampFormatting:
    def test_format_timestamp_string_passthrough(self):
        adapter = MockPlatformAdapter()
        result = adapter._format_timestamp("2025-01-01T12:00:00")
        assert result == "2025-01-01T12:00:00"

    def test_format_timestamp_numeric(self):
        adapter = MockPlatformAdapter()
        timestamp = 1704110400.0
        result = adapter._format_timestamp(timestamp)
        assert "2024" in result or "T" in result

    def test_format_timestamp_zero(self):
        adapter = MockPlatformAdapter()
        result = adapter._format_timestamp(0)
        assert "T" in result

    def test_format_timestamp_empty_string(self):
        adapter = MockPlatformAdapter()
        result = adapter._format_timestamp("")
        assert "T" in result


class TestValidationStatusDetermination:
    def test_determine_status_all_passed(self):
        adapter = MockPlatformAdapter()

        validation_phase = Mock()
        validation_phase.row_count_validation = "PASSED"
        validation_phase.schema_validation = "PASSED"
        validation_phase.data_integrity_checks = "PASSED"

        result = adapter._determine_overall_validation_status(validation_phase)
        assert result == "PASSED"

    def test_determine_status_row_count_failed(self):
        adapter = MockPlatformAdapter()

        validation_phase = Mock()
        validation_phase.row_count_validation = "FAILED"
        validation_phase.schema_validation = "PASSED"
        validation_phase.data_integrity_checks = "PASSED"

        result = adapter._determine_overall_validation_status(validation_phase)
        assert result == "FAILED"

    def test_determine_status_schema_failed(self):
        adapter = MockPlatformAdapter()

        validation_phase = Mock()
        validation_phase.row_count_validation = "PASSED"
        validation_phase.schema_validation = "FAILED"
        validation_phase.data_integrity_checks = "PASSED"

        result = adapter._determine_overall_validation_status(validation_phase)
        assert result == "FAILED"

    def test_determine_status_integrity_failed(self):
        adapter = MockPlatformAdapter()

        validation_phase = Mock()
        validation_phase.row_count_validation = "PASSED"
        validation_phase.schema_validation = "PASSED"
        validation_phase.data_integrity_checks = "FAILED"

        result = adapter._determine_overall_validation_status(validation_phase)
        assert result == "FAILED"

    def test_determine_status_partial(self):
        adapter = MockPlatformAdapter()

        validation_phase = Mock()
        validation_phase.row_count_validation = "PARTIAL"
        validation_phase.schema_validation = "PASSED"
        validation_phase.data_integrity_checks = "PASSED"

        result = adapter._determine_overall_validation_status(validation_phase)
        assert result == "PARTIAL"


class TestQueryDefinitionExtraction:
    def test_extract_query_definitions(self):
        adapter = MockPlatformAdapter()

        queries = {
            "Q1": "SELECT * FROM orders",
            "Q2": "SELECT * FROM customers",
        }
        mock_benchmark = Mock()

        result = adapter._extract_query_definitions(mock_benchmark, queries, "power")

        assert "power" in result
        assert "Q1" in result["power"]
        assert "Q2" in result["power"]
        assert result["power"]["Q1"].sql == "SELECT * FROM orders"
        assert result["power"]["Q2"].sql == "SELECT * FROM customers"

    def test_extract_query_definitions_default_stream(self):
        adapter = MockPlatformAdapter()

        queries = {"Q1": "SELECT 1"}
        mock_benchmark = Mock()

        result = adapter._extract_query_definitions(mock_benchmark, queries)

        assert "standard" in result
        assert "Q1" in result["standard"]


class TestStandardExecutionPhase:
    def test_create_standard_execution_phase(self):
        adapter = MockPlatformAdapter()

        query_results = [
            {"query_id": "Q1", "execution_time_seconds": 1.5, "status": "SUCCESS", "rows_returned": 100},
            {"query_id": "Q2", "execution_time_seconds": 2.0, "status": "SUCCESS", "rows_returned": 50},
        ]

        result = adapter._create_standard_execution_phase(query_results, "test_stream")

        assert len(result) == 2
        assert result[0].query_id == "Q1"
        assert result[0].stream_id == "test_stream"
        assert result[0].execution_order == 1
        assert result[0].execution_time_ms == 1500.0
        assert result[0].run_type == "measurement"
        assert result[1].query_id == "Q2"
        assert result[1].execution_order == 2
        assert result[1].run_type == "measurement"

    def test_create_standard_execution_phase_defaults(self):
        adapter = MockPlatformAdapter()

        query_results = [{"execution_time_seconds": 0.5}]

        result = adapter._create_standard_execution_phase(query_results)

        assert len(result) == 1
        assert result[0].stream_id == "standard"
        assert result[0].query_id == "Q1"
        assert result[0].run_type == "measurement"

    def test_create_standard_execution_phase_keeps_global_order_when_position_is_present(self):
        adapter = MockPlatformAdapter()

        result = adapter._create_standard_execution_phase(
            [
                {
                    "query_id": "Q19",
                    "execution_order": 19,
                    "position": 1,
                    "execution_time_seconds": 0.25,
                    "status": "SUCCESS",
                }
            ]
        )

        assert result[0].execution_order == 19


class TestThroughputPhaseCreation:
    def test_create_throughput_phase_none(self):
        adapter = MockPlatformAdapter()
        result = adapter._create_throughput_phase(None)
        assert result is None

    def test_create_throughput_phase_with_streams(self):
        adapter = MockPlatformAdapter()

        stream1 = Mock()
        stream1.stream_id = 0
        stream1.start_time = "2025-01-01T10:00:00"
        stream1.end_time = "2025-01-01T10:01:00"
        stream1.duration = 60.0
        stream1.query_results = [
            {"query_id": "Q1", "execution_time_seconds": 1.0, "success": True},
            {"query_id": "Q2", "execution_time_seconds": 2.0, "success": True},
        ]
        stream1.queries_executed = 2
        stream1.success = False
        stream1.error = "stream stopped"

        throughput_result = Mock()
        throughput_result.stream_results = [stream1]
        throughput_result.total_time = 60.0
        throughput_result.start_time = "2025-01-01T10:00:00"
        throughput_result.end_time = "2025-01-01T10:01:00"
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 0
        throughput_result.errors = ["Stream 0 failed: stream stopped"]

        result = adapter._create_throughput_phase(throughput_result)

        assert result is not None
        assert len(result.streams) == 1
        assert result.streams[0].stream_id == 0
        assert len(result.streams[0].query_executions) == 2
        assert result.total_queries_executed == 2
        assert result.success is False
        assert result.errors == ["Stream 0 failed: stream stopped"]
        assert result.streams[0].success is False
        assert result.streams[0].error_message == "stream stopped"
        assert result.streams[0].query_executions[0].run_type == "measurement"
        assert result.streams[0].query_executions[1].run_type == "measurement"

    def test_create_throughput_phase_records_the_stream_numbering_basis(self):
        adapter = MockPlatformAdapter()
        stream = Mock()
        stream.stream_id = 1
        stream.start_time = "2025-01-01T10:00:00"
        stream.end_time = "2025-01-01T10:01:00"
        stream.duration = 60.0
        stream.query_results = [{"query_id": "Q1", "execution_time_seconds": 1.0, "success": True}]
        stream.queries_executed = 1
        stream.queries_successful = 1
        stream.queries_failed = 0
        stream.success = True
        stream.error = None
        numbering = {"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": 1}

        throughput_result = Mock()
        throughput_result.stream_results = [stream]
        throughput_result.total_time = 60.0
        throughput_result.start_time = "2025-01-01T10:00:00"
        throughput_result.end_time = "2025-01-01T10:01:00"
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 1
        throughput_result.errors = []
        throughput_result.stream_numbering = numbering

        result = adapter._create_throughput_phase(throughput_result)

        assert result.stream_numbering == numbering
        assert result.streams[0].stream_id == 1

    def test_create_throughput_phase_infers_warmup_run_type(self):
        adapter = MockPlatformAdapter()

        stream1 = Mock()
        stream1.stream_id = 0
        stream1.start_time = "2025-01-01T10:00:00"
        stream1.end_time = "2025-01-01T10:01:00"
        stream1.duration = 60.0
        stream1.query_results = [{"query_id": "Q1", "execution_time_seconds": 1.0, "success": True, "iteration": 0}]
        stream1.queries_executed = 1
        stream1.success = True
        stream1.error = None

        throughput_result = Mock()
        throughput_result.stream_results = [stream1]
        throughput_result.total_time = 60.0
        throughput_result.start_time = "2025-01-01T10:00:00"
        throughput_result.end_time = "2025-01-01T10:01:00"
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 1
        throughput_result.errors = []

        result = adapter._create_throughput_phase(throughput_result)

        assert result is not None
        assert result.streams[0].query_executions[0].run_type == "warmup"

    def test_create_throughput_phase_ignores_stream_local_position_for_persistence(self):
        adapter = MockPlatformAdapter()
        stream = Mock()
        stream.stream_id = 0
        stream.start_time = "2025-01-01T10:00:00"
        stream.end_time = "2025-01-01T10:01:00"
        stream.duration = 60.0
        stream.query_results = [{"query_id": "Q1", "position": 0, "execution_time_seconds": 1.0, "success": True}]
        stream.queries_executed = 1
        stream.success = True
        stream.error = None

        throughput_result = Mock()
        throughput_result.stream_results = [stream]
        throughput_result.total_time = 60.0
        throughput_result.start_time = stream.start_time
        throughput_result.end_time = stream.end_time
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 1
        throughput_result.errors = []

        result = adapter._create_throughput_phase(throughput_result)

        assert result.streams[0].query_executions[0].execution_order == 1

    def test_create_throughput_phase_persists_global_order_across_streams(self):
        adapter = MockPlatformAdapter()

        def _stream(stream_id):
            stream = Mock()
            stream.stream_id = stream_id
            stream.start_time = "2025-01-01T10:00:00"
            stream.end_time = "2025-01-01T10:01:00"
            stream.duration = 60.0
            stream.query_results = [
                {"query_id": "Q1", "position": 1, "execution_time_seconds": 1.0, "success": True},
                {"query_id": "Q2", "position": 2, "execution_time_seconds": 2.0, "success": True},
            ]
            stream.queries_executed = 2
            stream.success = True
            stream.error = None
            return stream

        throughput_result = Mock()
        throughput_result.stream_results = [_stream(0), _stream(1)]
        throughput_result.total_time = 60.0
        throughput_result.start_time = "2025-01-01T10:00:00"
        throughput_result.end_time = "2025-01-01T10:01:00"
        throughput_result.config = Mock(num_streams=2)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 2
        throughput_result.streams_successful = 2
        throughput_result.errors = []

        result = adapter._create_throughput_phase(throughput_result)

        orders = [execution.execution_order for stream in result.streams for execution in stream.query_executions]
        assert orders == [1, 2, 3, 4]

    def test_create_throughput_phase_preserves_explicit_execution_order(self):
        adapter = MockPlatformAdapter()
        stream = Mock()
        stream.stream_id = 0
        stream.start_time = "2025-01-01T10:00:00"
        stream.end_time = "2025-01-01T10:01:00"
        stream.duration = 60.0
        stream.query_results = [
            {"query_id": "Q19", "execution_order": 19, "position": 1, "execution_time_seconds": 0.25, "success": True}
        ]
        stream.queries_executed = 1
        stream.success = True
        stream.error = None

        throughput_result = Mock()
        throughput_result.stream_results = [stream]
        throughput_result.total_time = 60.0
        throughput_result.start_time = stream.start_time
        throughput_result.end_time = stream.end_time
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 1
        throughput_result.errors = []

        result = adapter._create_throughput_phase(throughput_result)

        assert result.streams[0].query_executions[0].execution_order == 19

    @pytest.mark.parametrize(
        ("query_result", "expected_duration_ms"),
        [
            ({"query_id": "Q1", "execution_time_seconds": 0.0009, "success": True}, 0.9),
            ({"query_id": "Q1", "execution_time_seconds": 0.0, "success": True}, 0.0),
            ({"query_id": "Q1", "success": True}, None),
        ],
    )
    def test_create_throughput_phase_preserves_duration_precision_and_missing_values(
        self, query_result, expected_duration_ms
    ):
        adapter = MockPlatformAdapter()
        stream = Mock()
        stream.stream_id = 0
        stream.start_time = "2025-01-01T10:00:00"
        stream.end_time = "2025-01-01T10:01:00"
        stream.duration = 60.0
        stream.query_results = [query_result]
        stream.queries_executed = 1
        stream.success = True
        stream.error = None

        throughput_result = Mock()
        throughput_result.stream_results = [stream]
        throughput_result.total_time = 60.0
        throughput_result.start_time = stream.start_time
        throughput_result.end_time = stream.end_time
        throughput_result.config = Mock(num_streams=1)
        throughput_result.throughput_at_size = 720.0
        throughput_result.streams_executed = 1
        throughput_result.streams_successful = 1
        throughput_result.errors = []

        result = adapter._create_throughput_phase(throughput_result)

        assert result is not None
        assert result.streams[0].query_executions[0].execution_time_ms == expected_duration_ms


class TestGetExistingTables:
    def test_get_existing_tables_handles_exception(self):
        adapter = MockPlatformAdapter()
        mock_conn = Mock()

        mock_conn.execute.side_effect = Exception("Database error")

        result = adapter._get_existing_tables(mock_conn)

        assert result == []


class TestCalculateDataSize:
    def test_calculate_data_size_with_files(self, tmp_path):
        adapter = MockPlatformAdapter()

        test_file = tmp_path / "test.csv"
        test_file.write_text("a,b,c\n1,2,3\n4,5,6\n")

        result = adapter._calculate_data_size(tmp_path)

        assert result >= 0

    def test_calculate_data_size_nonexistent_path(self):
        adapter = MockPlatformAdapter()

        result = adapter._calculate_data_size(Path("/nonexistent/path"))

        assert result == 0.0


class TestConnectionFactoryCursorPattern:
    def test_default_connection_factory_creates_cursor(self):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        def _default_connection_factory():
            stream_cursor = mock_connection.cursor()
            return stream_cursor

        result = _default_connection_factory()

        mock_connection.cursor.assert_called_once()
        assert result is mock_cursor

    def test_connection_factory_creates_unique_cursors_per_call(self):
        mock_connection = Mock()
        cursors = [Mock() for _ in range(3)]
        mock_connection.cursor.side_effect = cursors

        def _default_connection_factory():
            stream_cursor = mock_connection.cursor()
            return stream_cursor

        results = [_default_connection_factory() for _ in range(3)]

        assert mock_connection.cursor.call_count == 3
        assert results == cursors
        assert len(set(id(r) for r in results)) == 3

    def test_tpch_throughput_connection_factory_uses_cursor(self):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_adapter = Mock()

        scale_factor = 0.01

        def connection_factory():
            stream_cursor = mock_connection.cursor()
            conn_wrapper = PlatformAdapterConnection(stream_cursor, mock_adapter)
            conn_wrapper.benchmark_type = "tpch"
            conn_wrapper.scale_factor = scale_factor
            return conn_wrapper

        result = connection_factory()

        mock_connection.cursor.assert_called_once()
        assert isinstance(result, PlatformAdapterConnection)
        assert result.benchmark_type == "tpch"
        assert result.scale_factor == scale_factor

    def test_tpcds_throughput_connection_factory_uses_cursor(self):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_adapter = Mock()

        scale_factor = 1.0

        def connection_factory():
            stream_cursor = mock_connection.cursor()
            conn_wrapper = PlatformAdapterConnection(stream_cursor, mock_adapter)
            conn_wrapper.benchmark_type = "tpcds"
            conn_wrapper.scale_factor = scale_factor
            return conn_wrapper

        result = connection_factory()

        mock_connection.cursor.assert_called_once()
        assert isinstance(result, PlatformAdapterConnection)
        assert result.benchmark_type == "tpcds"
        assert result.scale_factor == scale_factor

    def test_maintenance_test_connection_factory_uses_cursor(self):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_adapter = Mock()

        scale_factor = 0.1

        def connection_factory():
            stream_cursor = mock_connection.cursor()
            conn_wrapper = PlatformAdapterConnection(stream_cursor, mock_adapter)
            conn_wrapper.benchmark_type = "tpch"
            conn_wrapper.scale_factor = scale_factor
            return conn_wrapper

        results = []
        for _ in range(3):
            mock_cursor = Mock()
            mock_connection.cursor.return_value = mock_cursor
            results.append(connection_factory())

        assert mock_connection.cursor.call_count == 3
        assert all(isinstance(r, PlatformAdapterConnection) for r in results)

    def test_cursor_pattern_prevents_shared_connection_state(self):
        from concurrent.futures import ThreadPoolExecutor

        mock_connection = Mock()
        cursors_created = []

        def create_cursor():
            cursor = Mock()
            cursor.cursor_id = len(cursors_created)
            cursors_created.append(cursor)
            return cursor

        mock_connection.cursor.side_effect = create_cursor

        def stream_work():
            cursor = mock_connection.cursor()
            cursor.execute("SELECT 1")
            return cursor.cursor_id

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(stream_work) for _ in range(4)]
            cursor_ids = [f.result() for f in futures]

        assert len(set(cursor_ids)) == 4, "Each stream should get its own cursor"
        assert mock_connection.cursor.call_count == 4
        assert len(cursors_created) == 4


class TestMonotonicPhaseTiming:
    def test_data_generation_phase_does_not_require_wall_clock(self):
        adapter = MockPlatformAdapter()
        benchmark = SimpleNamespace(tables={"orders": [{"id": 1}, {"id": 2}]})

        mock_clock = Mock(side_effect=[10.0, 10.1, 10.3, 10.8])
        with (
            patch("benchbox.platforms.base.phase_tracking.mono_time", mock_clock),
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            phase = adapter._create_enhanced_data_generation_phase(benchmark)

        assert phase is not None
        assert phase.duration_ms == 800
        assert phase.tables_generated == 1
        assert phase.per_table_stats["orders"].generation_time_ms == 200


class TestReusedDatabaseExternalMode:
    def test_reused_db_external_mode_calls_create_external_tables(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "external"
        adapter.supports_external_tables = True
        adapter.create_external_tables = Mock(return_value=({"t1": 100}, 0.3, None))

        benchmark = Mock()
        benchmark.output_dir = "/tmp/test"
        connection = Mock()

        result = adapter._setup_reused_database_phases(benchmark, connection)
        schema_time, schema_phase, loading_time, table_stats, data_phase, tuning_saved = result

        adapter.create_external_tables.assert_called_once()
        assert table_stats == {"t1": 100}
        assert loading_time == 0.3
        assert tuning_saved is False

    def test_reused_db_native_mode_skips_external_tables(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "native"
        adapter.create_external_tables = Mock()

        benchmark = Mock()
        benchmark.get_schema.return_value = {"t1": {}}
        connection = Mock()

        adapter._setup_reused_database_phases(benchmark, connection)
        adapter.create_external_tables.assert_not_called()

    def test_reused_db_external_raises_for_unsupported_platform(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "external"
        adapter.supports_external_tables = False

        benchmark = Mock()
        connection = Mock()

        with pytest.raises(RuntimeError, match="does not support"):
            adapter._setup_reused_database_phases(benchmark, connection)

    def test_reused_db_external_calls_validate_requirements(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "external"
        adapter.supports_external_tables = True
        adapter.validate_external_table_requirements = Mock()
        adapter.create_external_tables = Mock(return_value=({"t1": 100}, 0.1, None))

        benchmark = Mock()
        benchmark.output_dir = "/tmp/test"
        connection = Mock()

        adapter._setup_reused_database_phases(benchmark, connection)
        adapter.validate_external_table_requirements.assert_called_once()


class TestExecutionMetadataTableMode:
    def test_external_mode_in_execution_metadata(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "external"
        metadata, _, _ = adapter._build_execution_metadata({"benchmark": "tpch", "scale_factor": 0.01})
        run_config = metadata.get("run_config", {})
        assert run_config.get("table_mode") == "external"

    def test_native_mode_omitted_from_execution_metadata(self):
        adapter = MockPlatformAdapter()
        adapter.table_mode = "native"
        metadata, _, _ = adapter._build_execution_metadata({"benchmark": "tpch", "scale_factor": 0.01})
        run_config = metadata.get("run_config", {})
        assert run_config.get("table_mode") is None

    def test_table_format_settings_in_execution_metadata(self):
        adapter = MockPlatformAdapter()
        metadata, _, _ = adapter._build_execution_metadata(
            {
                "benchmark": "tpch",
                "scale_factor": 0.01,
                "table_format": "parquet",
                "table_format_compression": "zstd",
                "table_format_partition_cols": ["region"],
            }
        )
        run_config = metadata.get("run_config", {})
        assert run_config.get("table_format") == "parquet"
        assert run_config.get("table_format_compression") == "zstd"
        assert run_config.get("table_format_partition_cols") == ["region"]


class TestBenchmarkIdentityInExecutionMetadata:
    def test_benchmark_name_key_sets_benchmark_id(self):
        adapter = MockPlatformAdapter()
        metadata, _, _ = adapter._build_execution_metadata({"benchmark_name": "tpch", "scale_factor": 0.01})
        assert metadata.get("benchmark_id") == "tpch"

    def test_no_benchmark_identity_in_run_config_produces_none(self):
        adapter = MockPlatformAdapter()
        metadata, _, _ = adapter._build_execution_metadata({"scale_factor": 0.01})
        assert metadata.get("benchmark_id") is None


class TestTPCExecutionRouting:
    def test_dispatch_paths_have_no_literal_tpc_benchmark_names(self):
        from benchbox.platforms.base.execution import TestDriversMixin

        dispatch_methods = (
            TestDriversMixin._execute_power_test,
            TestDriversMixin._execute_throughput_test,
            TestDriversMixin._execute_maintenance_test,
            TestDriversMixin._execute_combined_test,
        )
        for method in dispatch_methods:
            source = inspect.getsource(method)
            assert '"tpch"' not in source, f"{method.__name__} routes on a literal benchmark name"
            assert '"tpcds"' not in source, f"{method.__name__} routes on a literal benchmark name"

    def test_execute_power_test_routes_tpch_via_run_config_benchmark_name(self):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_power_test = Mock(return_value=[{"query_id": "Q1"}])
        benchmark = SimpleNamespace(_name="adhoc", display_name="adhoc", scale_factor=1.0)
        connection = Mock()
        run_config = {"benchmark_name": "tpch", "iterations": 2}

        results = adapter._execute_power_test(benchmark, connection, run_config)

        adapter._execute_tpch_power_test.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "Q1"}]

    def test_execute_throughput_test_routes_tpcds_via_run_config_benchmark_name(self):
        adapter = MockPlatformAdapter()
        adapter._execute_tpcds_throughput_test = Mock(return_value=[{"query_id": "Q99"}])
        benchmark = SimpleNamespace(_name="custom", display_name="custom benchmark")

        results = adapter._execute_throughput_test(benchmark, Mock(), {"benchmark_name": "tpcds", "num_streams": 4})

        adapter._execute_tpcds_throughput_test.assert_called_once()
        assert results == [{"query_id": "Q99"}]

    def test_execute_power_test_routes_tpcds_obt_to_generic_power(self):
        adapter = MockPlatformAdapter()
        adapter._execute_tpcds_power_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_generic_power_test = Mock(return_value=[{"query_id": "generic"}])
        benchmark = SimpleNamespace(_name="custom", display_name="custom benchmark")
        connection = Mock()
        run_config = {"benchmark_name": "tpcds_obt", "iterations": 1}

        results = adapter._execute_power_test(benchmark, connection, run_config)

        adapter._execute_tpcds_power_test.assert_not_called()
        adapter._execute_generic_power_test.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "generic"}]

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_throughput_test_falls_back_for_unsupported_benchmark(self, mock_console):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "clickbench", "num_streams": 4}
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])

        results = adapter._execute_throughput_test(benchmark, connection, run_config)

        adapter._execute_all_queries.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "fallback"}]
        assert any("Throughput test not supported" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_throughput_test_tpcds_obt_falls_back_to_all_queries(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_tpcds_throughput_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "tpcds_obt", "num_streams": 4}

        results = adapter._execute_throughput_test(benchmark, connection, run_config)

        adapter._execute_tpcds_throughput_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "fallback"}]
        assert any("Throughput test not supported" in str(call) for call in mock_console.print.call_args_list)

    def test_execute_maintenance_test_routes_tpch_via_run_config_benchmark_name(self):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_maintenance_test = Mock(return_value=[{"query_id": "RF1"}])

        class SomeBenchmark:
            display_name = ""
            _name = "some_benchmark"

        benchmark = SomeBenchmark()
        results = adapter._execute_maintenance_test(
            benchmark, Mock(), {"benchmark_name": "tpch", "maintenance_pairs": 2}
        )

        adapter._execute_tpch_maintenance_test.assert_called_once()
        assert results == [{"query_id": "RF1"}]

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_maintenance_test_tpcds_obt_falls_back_to_all_queries(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_tpcds_maintenance_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "tpcds_obt", "maintenance_pairs": 2}

        results = adapter._execute_maintenance_test(benchmark, connection, run_config)

        adapter._execute_tpcds_maintenance_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "fallback"}]
        assert any("Maintenance test not supported" in str(call) for call in mock_console.print.call_args_list)

    def test_execute_combined_test_defaults_to_all_tpch_phases(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "tpch"}
        adapter._execute_tpch_power_test = Mock(return_value=[{"query_id": "power"}])
        adapter._execute_tpch_throughput_test = Mock(return_value=[{"query_id": "throughput"}])
        adapter._execute_tpch_maintenance_test = Mock(return_value=[{"query_id": "maintenance"}])

        results = adapter._execute_combined_test(benchmark, connection, run_config)

        adapter._execute_tpch_power_test.assert_called_once_with(benchmark, connection, run_config)
        adapter._execute_tpch_throughput_test.assert_called_once_with(benchmark, connection, run_config)
        adapter._execute_tpch_maintenance_test.assert_called_once_with(benchmark, connection, run_config)
        assert [row["query_id"] for row in results] == ["power", "throughput", "maintenance"]

    def test_execute_combined_test_respects_requested_tpcds_phase_subset(self):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "tpcds", "options": {"requested_phases": ["throughput"]}}
        adapter._execute_tpcds_power_test = Mock()
        adapter._execute_tpcds_throughput_test = Mock(return_value=[{"query_id": "stream-1"}])
        adapter._execute_tpcds_maintenance_test = Mock()

        results = adapter._execute_combined_test(benchmark, connection, run_config)

        adapter._execute_tpcds_power_test.assert_not_called()
        adapter._execute_tpcds_throughput_test.assert_called_once_with(benchmark, connection, run_config)
        adapter._execute_tpcds_maintenance_test.assert_not_called()
        assert results == [{"query_id": "stream-1"}]

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_combined_test_tpcds_obt_falls_back_to_all_queries(self, mock_console):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "tpcds_obt"}
        adapter._execute_tpcds_power_test = Mock(return_value=[{"query_id": "power"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])

        results = adapter._execute_combined_test(benchmark, connection, run_config)

        adapter._execute_tpcds_power_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "fallback"}]
        assert any("Combined test not supported" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_combined_test_falls_back_for_unsupported_benchmark(self, mock_console):
        adapter = MockPlatformAdapter()
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": "ssb", "options": {"requested_phases": ["power"]}}
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "standard"}])

        results = adapter._execute_combined_test(benchmark, connection, run_config)

        adapter._execute_all_queries.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "standard"}]
        assert any("Combined test not supported" in str(call) for call in mock_console.print.call_args_list)


class TestNormalizeBenchmarkIdDerivedBenchmarks:
    @pytest.mark.parametrize(
        "input_name,expected_id",
        [
            ("tpchavoc", "tpchavoc"),
            ("tpch-avoc", "tpchavoc"),
            ("tpch_avoc", "tpchavoc"),
            ("tpc-havoc", "tpchavoc"),
            ("tpc-h avoc", "tpchavoc"),
            ("tpch_skew", "tpch_skew"),
            ("tpch-skew", "tpch_skew"),
            ("tpcds_obt", "tpcds_obt"),
            ("tpcds-obt", "tpcds_obt"),
            ("tpch", "tpch"),
            ("tpc-h", "tpch"),
            ("TPC-H Benchmark", "tpch"),
            ("tpcds", "tpcds"),
            ("tpc-ds", "tpcds"),
            ("TPC-H Skew Benchmark (heavy)", "tpch_skew"),
            ("TPC-Havoc Benchmark", "tpchavoc"),
            ("TPC-DS One Big Table Benchmark", "tpcds_obt"),
            ("Star Schema Benchmark", "ssb"),
        ],
    )
    def test_normalize_benchmark_id_derived(self, input_name, expected_id):
        assert normalize_benchmark_id(input_name) == expected_id


class TestDerivedBenchmarkRouting:
    @pytest.mark.parametrize("benchmark_name", ["tpchavoc", "tpch_skew"])
    def test_power_test_routes_tpch_derived_to_generic(self, benchmark_name):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_power_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_generic_power_test = Mock(return_value=[{"query_id": "generic"}])
        benchmark = SimpleNamespace(_name="custom", display_name="custom benchmark")
        connection = Mock()
        run_config = {"benchmark_name": benchmark_name, "iterations": 1}

        results = adapter._execute_power_test(benchmark, connection, run_config)

        adapter._execute_tpch_power_test.assert_not_called()
        adapter._execute_generic_power_test.assert_called_once_with(benchmark, connection, run_config)
        assert results == [{"query_id": "generic"}]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    @pytest.mark.parametrize("benchmark_name", ["tpchavoc", "tpch_skew"])
    def test_throughput_test_routes_tpch_derived_to_fallback(self, mock_console, benchmark_name):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_throughput_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": benchmark_name, "num_streams": 4}

        results = adapter._execute_throughput_test(benchmark, connection, run_config)

        adapter._execute_tpch_throughput_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once()
        assert results == [{"query_id": "fallback"}]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    @pytest.mark.parametrize("benchmark_name", ["tpchavoc", "tpch_skew"])
    def test_maintenance_test_routes_tpch_derived_to_fallback(self, mock_console, benchmark_name):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_maintenance_test = Mock(return_value=[{"query_id": "specialized"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": benchmark_name, "maintenance_pairs": 2}

        results = adapter._execute_maintenance_test(benchmark, connection, run_config)

        adapter._execute_tpch_maintenance_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once()
        assert results == [{"query_id": "fallback"}]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    @pytest.mark.parametrize("benchmark_name", ["tpchavoc", "tpch_skew"])
    def test_combined_test_routes_tpch_derived_to_fallback(self, mock_console, benchmark_name):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_power_test = Mock(return_value=[{"query_id": "power"}])
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "fallback"}])
        benchmark = Mock()
        connection = Mock()
        run_config = {"benchmark_name": benchmark_name}

        results = adapter._execute_combined_test(benchmark, connection, run_config)

        adapter._execute_tpch_power_test.assert_not_called()
        adapter._execute_all_queries.assert_called_once()
        assert results == [{"query_id": "fallback"}]


class TestFallbackEffectiveExecutionType:
    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_throughput_fallback_sets_effective_type_to_power(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "Q1"}])
        run_config = {"benchmark_name": "tpcds_obt", "num_streams": 4}

        adapter._execute_throughput_test(Mock(), Mock(), run_config)

        assert run_config["_effective_execution_type"] == "power"

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_maintenance_fallback_sets_effective_type_to_power(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "Q1"}])
        run_config = {"benchmark_name": "tpcds_obt", "maintenance_pairs": 2}

        adapter._execute_maintenance_test(Mock(), Mock(), run_config)

        assert run_config["_effective_execution_type"] == "power"

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_combined_fallback_sets_effective_type_to_power(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_all_queries = Mock(return_value=[{"query_id": "Q1"}])
        run_config = {"benchmark_name": "tpcds_obt"}

        adapter._execute_combined_test(Mock(), Mock(), run_config)

        assert run_config["_effective_execution_type"] == "power"

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_throughput_fallback_does_not_set_flag_for_tpch(self, mock_console):
        adapter = MockPlatformAdapter()
        adapter._execute_tpch_throughput_test = Mock(return_value=[])
        run_config = {"benchmark_name": "tpch", "num_streams": 4}

        adapter._execute_throughput_test(Mock(), Mock(), run_config)

        assert "_effective_execution_type" not in run_config


class TestKnownBenchmarkIds:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("tpch", "tpch"),
            ("tpcds", "tpcds"),
            ("tpcds_obt", "tpcds_obt"),
            ("tpch_skew", "tpch_skew"),
            ("tpchavoc", "tpchavoc"),
            ("ssb", "ssb"),
            ("clickbench", "clickbench"),
        ],
    )
    def test_known_ids_recognized(self, name, expected):
        assert MockPlatformAdapter._normalize_known_benchmark_id(name) == expected

    @pytest.mark.parametrize("name", ["nyctaxi", "h2odb", "unknown_bench"])
    def test_unknown_ids_return_none(self, name):
        assert MockPlatformAdapter._normalize_known_benchmark_id(name) is None


class TestBenchmarkFamily:
    @pytest.mark.parametrize(
        "bench_id,expected_family",
        [
            ("tpch", "tpch"),
            ("tpch_skew", "tpch"),
            ("tpchavoc", "tpch"),
            ("tpcds", "tpcds"),
            ("tpcds_obt", "tpcds"),
            ("ssb", "generic"),
            ("clickbench", "generic"),
            ("nyctaxi", "generic"),
        ],
    )
    def test_family_mapping(self, bench_id, expected_family):
        assert _benchmark_family(bench_id) == expected_family


class TestNormalizeBenchmarkIdSubstringSafety:
    @pytest.mark.parametrize(
        "input_name",
        [
            "tpchavoc2",
            "tpch_skewed",
            "tpcds_obt_v2",
            "mytpch",
            "sstpch",
        ],
    )
    def test_substring_names_do_not_match_parents(self, input_name):
        result = normalize_benchmark_id(input_name)
        assert result not in ("tpch", "tpcds", "tpchavoc", "tpch_skew", "tpcds_obt")


class TestBuildExecutionPhasesWithEffectiveType:
    def test_fallback_produces_power_test_phase(self):
        adapter = MockPlatformAdapter()
        adapter.capture_plans = False
        adapter.plan_capture_failures = 0
        adapter._last_power_test_result = None
        adapter._last_throughput_test_result = None

        query_results = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.5},
            {"query_id": "Q2", "status": "SUCCESS", "execution_time_seconds": 0.3},
        ]
        query_executions = []

        from benchbox.platforms.base.models import SetupPhase

        setup_phase = SetupPhase(
            schema_creation=None,
            data_generation=None,
            data_loading=None,
        )

        run_config = {
            "test_execution_type": "throughput",
            "_effective_execution_type": "power",
        }

        execution_phases, total_exec_time, power_test_phase, throughput_test_phase = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase
        )

        assert power_test_phase is not None, "Fallback should produce a PowerTestPhase"
        assert execution_phases.power_test is not None
        assert throughput_test_phase is None
        assert total_exec_time == pytest.approx(0.8)

    def test_throughput_type_without_fallback_skips_power_phase(self):
        adapter = MockPlatformAdapter()
        adapter.capture_plans = False
        adapter.plan_capture_failures = 0
        adapter._last_power_test_result = None
        adapter._last_throughput_test_result = None

        query_results = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.5},
        ]

        from benchbox.platforms.base.models import SetupPhase

        setup_phase = SetupPhase(
            schema_creation=None,
            data_generation=None,
            data_loading=None,
        )

        run_config = {"test_execution_type": "throughput"}

        execution_phases, _, power_test_phase, _ = adapter._build_execution_phases(
            query_results, [], run_config, setup_phase
        )

        assert power_test_phase is None
        assert execution_phases.power_test is None


class TestTPCHAndTPCDSExecutionHelpers:
    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    def test_execute_tpch_power_test_records_warmup_and_failures(self, mock_power_test_cls, mock_console):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = Mock()
        connection = Mock()
        run_config = {
            "scale_factor": 0.01,
            "seed": 11,
            "validation_mode": "strict",
            "query_subset": ["1", "2"],
            "timeout": 12,
            "iterations": 2,
            "warm_up_iterations": 1,
            "power_fail_fast": True,
        }

        warmup_result = SimpleNamespace(
            success=True,
            queries_successful=1,
            queries_executed=1,
            power_at_size=10.0,
            total_time=1.0,
            errors=[],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.1,
                    "success": True,
                    "result_count": 5,
                    "stream_id": 0,
                    "position": 1,
                }
            ],
        )
        measurement_result = SimpleNamespace(
            success=False,
            queries_successful=1,
            queries_executed=2,
            power_at_size=8.5,
            total_time=2.0,
            errors=["query 2 failed"],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.2,
                    "success": True,
                    "result_count": 7,
                    "stream_id": 1,
                    "position": 1,
                },
                {
                    "query_id": "2",
                    "execution_time_seconds": 0.4,
                    "success": False,
                    "result_count": 0,
                    "stream_id": 1,
                    "position": 2,
                    "error": "timeout",
                },
            ],
        )
        mock_power_test_cls.return_value.run.side_effect = [warmup_result, measurement_result]

        results = adapter._execute_tpch_power_test(benchmark, connection, run_config)

        assert mock_power_test_cls.call_count == 2
        first_kwargs = mock_power_test_cls.call_args_list[0].kwargs
        second_kwargs = mock_power_test_cls.call_args_list[1].kwargs
        assert first_kwargs["stream_id"] == 0
        assert second_kwargs["stream_id"] == 0
        assert first_kwargs["query_subset"] == ["1", "2"]
        assert second_kwargs["dialect"] == "mock_dialect"
        assert results[0]["run_type"] == "warmup"
        assert results[0]["iteration"] == 0
        assert results[-1]["run_type"] == "measurement"
        assert results[-1]["iteration"] == 1
        assert results[-1]["status"] == "FAILED"
        assert results[-1]["error"] == "timeout"
        assert adapter._last_power_test_result is measurement_result
        assert any("fail_fast enabled" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.tpch.power_test.TPCHPowerTest", side_effect=RuntimeError("power init boom"))
    def test_execute_tpch_power_test_returns_error_result_on_exception(self, _mock_power_test_cls, _mock_console):
        adapter = MockPlatformAdapterWithDialect()

        results = adapter._execute_tpch_power_test(Mock(), Mock(), {"scale_factor": 0.01})

        assert results == [
            {
                "query_id": "power_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "power init boom",
                "test_type": "power",
            }
        ]

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_tpch_power_test_uses_resolved_validation_policy(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = Mock()
        benchmark._name = "tpch"
        connection = Mock()
        run_config = {
            "scale_factor": 1.0,
            "iterations": 1,
            "warm_up_iterations": 1,
        }

        warmup_result = SimpleNamespace(
            success=True,
            queries_successful=1,
            queries_executed=1,
            power_at_size=10.0,
            total_time=1.0,
            errors=[],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.1,
                    "success": True,
                    "result_count": 5,
                    "stream_id": 0,
                    "position": 1,
                }
            ],
        )
        measurement_result = SimpleNamespace(
            success=True,
            queries_successful=1,
            queries_executed=1,
            power_at_size=9.5,
            total_time=1.0,
            errors=[],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.2,
                    "success": True,
                    "result_count": 6,
                    "stream_id": 1,
                    "position": 1,
                }
            ],
        )

        observed_flags: list[tuple[int, bool]] = []
        power_results = [warmup_result, measurement_result]

        class FakeTPCHPowerTest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.config = SimpleNamespace(validation=len(power_results) == 1)

            def run(self):
                observed_flags.append((self.kwargs["stream_id"], self.kwargs["connection"]._validate_row_count))
                return power_results.pop(0)

        with patch("benchbox.core.tpch.power_test.TPCHPowerTest", FakeTPCHPowerTest):
            adapter._execute_tpch_power_test(benchmark, connection, run_config)

        assert observed_flags == [(0, False), (0, True)]

    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.expected_results.tpcds_results.set_config_validation_mode")
    def test_execute_tpcds_power_test_uses_connection_factory_and_fail_fast(
        self, mock_set_validation_mode, mock_console
    ):
        adapter = MockPlatformAdapterWithDialect()
        adapter.very_verbose = True
        benchmark = Mock()
        benchmark._name = "tpcds"
        connection = Mock()
        connection.cursor.side_effect = [Mock(name="warmup_cursor"), Mock(name="measurement_cursor")]
        run_config = {
            "scale_factor": 0.01,
            "seed": 7,
            "validation_mode": "strict",
            "query_subset": ["1", "2"],
            "timeout": 30,
            "iterations": 3,
            "warm_up_iterations": 1,
            "power_fail_fast": True,
        }

        warmup_result = SimpleNamespace(
            success=True,
            queries_successful=1,
            queries_executed=1,
            power_at_size=42.0,
            total_time=1.0,
            errors=[],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.1,
                    "success": True,
                    "result_count": 3,
                    "stream_id": 0,
                    "position": 1,
                }
            ],
        )
        measurement_result = SimpleNamespace(
            success=False,
            queries_successful=1,
            queries_executed=2,
            power_at_size=21.0,
            total_time=2.0,
            errors=["query 2 failed"],
            query_results=[
                {
                    "query_id": "1",
                    "execution_time_seconds": 0.2,
                    "success": True,
                    "result_count": 4,
                    "stream_id": 1,
                    "position": 1,
                },
                {
                    "query_id": "2",
                    "execution_time_seconds": 0.3,
                    "success": False,
                    "result_count": 0,
                    "stream_id": 1,
                    "position": 2,
                    "error": "timeout",
                },
            ],
        )

        created_kwargs: list[dict[str, Any]] = []
        power_results = [warmup_result, measurement_result]

        class FakeTPCDSPowerTest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                created_kwargs.append(kwargs)

            def run(self):
                wrapper = self.kwargs["connection_factory"]()
                self.kwargs["wrapped_connection"] = wrapper
                return power_results.pop(0)

        with patch("benchbox.core.tpcds.power_test.TPCDSPowerTest", FakeTPCDSPowerTest):
            results = adapter._execute_tpcds_power_test(benchmark, connection, run_config)

        assert mock_set_validation_mode.call_args.args == ("strict",)
        assert len(created_kwargs) == 2
        assert created_kwargs[0]["stream_id"] == 0
        assert created_kwargs[1]["stream_id"] == 1
        assert created_kwargs[0]["query_subset"] == ["1", "2"]
        assert created_kwargs[1]["dialect"] == "mock_dialect"
        assert created_kwargs[0]["wrapped_connection"].benchmark_type == "tpcds"
        assert created_kwargs[1]["wrapped_connection"].scale_factor == 0.01
        assert connection.cursor.call_count == 2
        assert results[0]["run_type"] == "warmup"
        assert results[-1]["run_type"] == "measurement"
        assert results[-1]["error"] == "timeout"
        assert adapter._last_power_test_result is measurement_result
        assert any("Target dialect" in str(call) for call in mock_console.print.call_args_list)
        assert any("fail_fast enabled" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.expected_results.tpcds_results.set_config_validation_mode")
    def test_execute_tpcds_power_test_returns_error_result_on_exception(self, _mock_set_validation_mode, _mock_console):
        adapter = MockPlatformAdapterWithDialect()

        class BrokenTPCDSPowerTest:
            def __init__(self, **kwargs):
                raise RuntimeError("tpcds power boom")

        with patch("benchbox.core.tpcds.power_test.TPCDSPowerTest", BrokenTPCDSPowerTest):
            results = adapter._execute_tpcds_power_test(Mock(), Mock(), {"scale_factor": 0.01})

        assert results == [
            {
                "query_id": "power_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "tpcds power boom",
                "test_type": "power",
            }
        ]

    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.expected_results.tpcds_results.set_config_validation_mode")
    def test_execute_tpcds_throughput_test_uses_seeded_config_and_preserves_errors(
        self, mock_set_validation_mode, mock_console
    ):
        adapter = MockPlatformAdapterWithDialect()
        adapter.very_verbose = True
        benchmark = Mock()
        connection = Mock()
        connection.cursor.return_value = Mock(name="stream_cursor")
        run_config = {"scale_factor": 1.0, "validation_mode": "off", "num_streams": 2, "seed": 17, "verbose": True}

        stream_result = SimpleNamespace(
            stream_id=3,
            queries_successful=1,
            queries_executed=2,
            query_results=[
                {"query_id": "7", "execution_time_seconds": 0.6, "success": True, "result_count": 8},
                {
                    "query_id": "9",
                    "execution_time_seconds": 1.4,
                    "success": False,
                    "result_count": 0,
                    "error": "stream timeout",
                },
            ],
        )
        throughput_result = SimpleNamespace(
            success=True,
            throughput_at_size=123.4,
            streams_executed=2,
            streams_successful=1,
            total_time=9.5,
            errors=[],
            stream_results=[stream_result],
        )

        captured: dict[str, Any] = {}

        class FakeTPCDSThroughputTest:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def run(self, config=None):
                captured["config"] = config
                captured["wrapped_connection"] = captured["init"]["connection_factory"]()
                return throughput_result

        with patch("benchbox.core.tpcds.throughput_test.TPCDSThroughputTest", FakeTPCDSThroughputTest):
            results = adapter._execute_tpcds_throughput_test(benchmark, connection, run_config)

        assert mock_set_validation_mode.call_args.args == ("off",)
        assert captured["init"]["num_streams"] == 2
        assert captured["init"]["dialect"] == "mock_dialect"
        assert captured["config"].base_seed == 17
        assert captured["wrapped_connection"].benchmark_type == "tpcds"
        assert connection.cursor.call_count == 1
        assert results == [
            {
                "query_id": "7",
                "execution_time_seconds": 0.6,
                "status": "SUCCESS",
                "rows_returned": 8,
                "test_type": "throughput",
                "stream_id": 3,
            },
            {
                "query_id": "9",
                "execution_time_seconds": 1.4,
                "status": "FAILED",
                "rows_returned": 0,
                "test_type": "throughput",
                "stream_id": 3,
                "error": "stream timeout",
            },
        ]
        assert adapter._last_throughput_test_result is throughput_result
        assert throughput_result.success is False
        assert throughput_result.throughput_at_size is None
        assert any("Target dialect" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    @patch("benchbox.core.expected_results.tpcds_results.set_config_validation_mode")
    def test_execute_tpcds_throughput_test_resets_cached_result_on_exception(
        self, _mock_set_validation_mode, _mock_console
    ):
        adapter = MockPlatformAdapterWithDialect()
        adapter._last_throughput_test_result = object()

        class BrokenTPCDSThroughputTest:
            def __init__(self, **kwargs):
                pass

            def run(self, config=None):
                raise RuntimeError("throughput boom")

        with patch("benchbox.core.tpcds.throughput_test.TPCDSThroughputTest", BrokenTPCDSThroughputTest):
            results = adapter._execute_tpcds_throughput_test(Mock(), Mock(), {"scale_factor": 1.0})

        assert adapter._last_throughput_test_result is None
        assert results == [
            {
                "query_id": "throughput_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "throughput boom",
                "test_type": "throughput",
            }
        ]

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_tpcds_maintenance_test_converts_output_dir_and_operation_errors(self, mock_console, tmp_path):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = Mock()
        connection = Mock()
        connection.cursor.return_value = Mock(name="maintenance_cursor")
        run_config = {"scale_factor": 1.0, "verbose": True, "output_dir": str(tmp_path / "maintenance")}

        maintenance_result = {
            "success": False,
            "insert_operations": 1,
            "update_operations": 0,
            "delete_operations": 1,
            "total_operations": 2,
            "successful_operations": 1,
            "total_time": 5.0,
            "overall_throughput": 0.4,
            "errors": ["delete failed"],
            "operations": [
                SimpleNamespace(
                    operation_type="INSERT",
                    table_name="catalog_sales",
                    duration=1.5,
                    success=True,
                    rows_affected=10,
                    error=None,
                ),
                SimpleNamespace(
                    operation_type="DELETE",
                    table_name="store_sales",
                    duration=3.5,
                    success=False,
                    rows_affected=0,
                    error="lock timeout",
                ),
            ],
        }

        captured: dict[str, Any] = {}

        class FakeTPCDSMaintenanceTest:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def run(self):
                captured["wrapped_connection"] = captured["init"]["connection_factory"]()
                return maintenance_result

        with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", FakeTPCDSMaintenanceTest):
            results = adapter._execute_tpcds_maintenance_test(benchmark, connection, run_config)

        assert captured["init"]["output_dir"] == Path(run_config["output_dir"])
        assert captured["init"]["dialect"] == "mock_dialect"
        assert captured["wrapped_connection"].benchmark_type == "tpcds"
        assert connection.cursor.call_count == 1
        assert results == [
            {
                "query_id": "insert_catalog_sales",
                "execution_time_seconds": 1.5,
                "status": "SUCCESS",
                "rows_returned": 10,
                "test_type": "maintenance",
                "operation_type": "INSERT",
                "table_name": "catalog_sales",
            },
            {
                "query_id": "delete_store_sales",
                "execution_time_seconds": 3.5,
                "status": "FAILED",
                "rows_returned": 0,
                "test_type": "maintenance",
                "operation_type": "DELETE",
                "table_name": "store_sales",
                "error": "lock timeout",
            },
        ]
        assert any("delete failed" in str(call) for call in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_tpch_throughput_test_uses_seeded_config(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = Mock()
        connection = Mock()
        connection.cursor.return_value = Mock(name="tpch_stream_cursor")
        run_config = {"scale_factor": 0.01, "num_streams": 3, "seed": 23, "verbose": True}

        stream_result = SimpleNamespace(
            stream_id=2,
            query_results=[
                {"query_id": "1", "execution_time_seconds": 0.5, "success": True, "result_count": 9},
                {
                    "query_id": "2",
                    "execution_time_seconds": 0.7,
                    "success": False,
                    "result_count": 0,
                    "error": "tpch timeout",
                },
            ],
        )
        throughput_result = SimpleNamespace(
            success=True,
            throughput_at_size=98.7,
            streams_executed=3,
            streams_successful=2,
            total_time=12.0,
            errors=[],
            stream_results=[stream_result],
        )

        captured: dict[str, Any] = {}

        class FakeTPCHThroughputTest:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def run(self, config=None):
                captured["config"] = config
                captured["wrapped_connection"] = captured["init"]["connection_factory"]()
                return throughput_result

        with patch("benchbox.core.tpch.throughput_test.TPCHThroughputTest", FakeTPCHThroughputTest):
            results = adapter._execute_tpch_throughput_test(benchmark, connection, run_config)

        assert captured["config"].base_seed == 23
        assert captured["wrapped_connection"].benchmark_type == "tpch"
        assert connection.cursor.call_count == 1
        assert results[-1]["error"] == "tpch timeout"
        assert adapter._last_throughput_test_result is throughput_result
        assert throughput_result.success is False
        assert throughput_result.throughput_at_size is None

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_execute_tpch_maintenance_test_passes_rf_intervals_and_integrity_flag(self, _mock_console, tmp_path):
        adapter = MockPlatformAdapterWithDialect()
        adapter.supports_refresh_functions = True
        connection = Mock()
        connection.cursor.return_value = Mock(name="tpch_maintenance_cursor")
        run_config = {
            "scale_factor": 0.1,
            "verbose": True,
            "maintenance_pairs": 3,
            "rf1_interval": 0.25,
            "rf2_interval": 0.75,
            "validate_integrity": False,
            "output_dir": str(tmp_path / "tpch-maint"),
        }

        result = SimpleNamespace(
            success=False,
            total_operations=2,
            successful_operations=1,
            failed_operations=1,
            total_time=4.0,
            overall_throughput=0.5,
            errors=["rf2 failed"],
            operations=[
                SimpleNamespace(operation_type="RF1", duration=1.0, success=True, rows_affected=100),
                SimpleNamespace(operation_type="RF2", duration=3.0, success=False, rows_affected=0),
            ],
        )

        captured: dict[str, Any] = {}

        class FakeTPCHMaintenanceTest:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def run_maintenance_test(self, **kwargs):
                captured["run_kwargs"] = kwargs
                captured["wrapped_connection"] = captured["init"]["connection_factory"]()
                return result

        with patch("benchbox.core.tpch.maintenance_test.TPCHMaintenanceTest", FakeTPCHMaintenanceTest):
            results = adapter._execute_tpch_maintenance_test(Mock(), connection, run_config)

        assert captured["init"]["output_dir"] == Path(run_config["output_dir"])
        assert captured["run_kwargs"] == {
            "maintenance_pairs": 3,
            "concurrent_with_queries": False,
            "rf1_interval": 0.25,
            "rf2_interval": 0.75,
            "validate_integrity": False,
        }
        assert captured["wrapped_connection"].benchmark_type == "tpch"
        assert captured["wrapped_connection"]._maintenance_mode is True
        assert connection.cursor.call_count == 1
        assert results == [
            {
                "query_id": "RF1",
                "execution_time_seconds": 1.0,
                "status": "SUCCESS",
                "rows_returned": 100,
                "test_type": "maintenance",
            },
            {
                "query_id": "RF2",
                "execution_time_seconds": 3.0,
                "status": "FAILED",
                "rows_returned": 0,
                "test_type": "maintenance",
                "error": "Unknown error",
            },
        ]


class TestExecuteGenericPowerTest:
    def _make_adapter(self):
        return MockPlatformAdapter()

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_warmup_and_measurement_iterations_tagged_correctly(self, _mock_console):
        adapter = self._make_adapter()
        call_count = [0]

        def fake_execute_all(benchmark, connection, run_config):
            call_count[0] += 1
            return [
                {"query_id": "Q1", "execution_time_seconds": 0.5, "status": "SUCCESS", "rows_returned": 10},
            ]

        adapter._execute_all_queries = fake_execute_all
        run_config = {"iterations": 2, "warm_up_iterations": 1}

        results = adapter._execute_generic_power_test(Mock(), Mock(), run_config)

        assert call_count[0] == 3
        warmup_results = [r for r in results if r["run_type"] == "warmup"]
        measurement_results = [r for r in results if r["run_type"] == "measurement"]
        assert len(warmup_results) == 1
        assert len(measurement_results) == 2
        assert warmup_results[0]["iteration"] == 0
        assert measurement_results[0]["iteration"] == 1
        assert measurement_results[1]["iteration"] == 2

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_aborts_remaining_iterations_when_all_queries_fail(self, mock_console):
        adapter = self._make_adapter()
        call_count = [0]

        def fake_execute_all(benchmark, connection, run_config):
            call_count[0] += 1
            return [{"query_id": "Q1", "execution_time_seconds": 0.0, "status": "FAILED", "rows_returned": 0}]

        adapter._execute_all_queries = fake_execute_all
        run_config = {"iterations": 3, "warm_up_iterations": 0}

        results = adapter._execute_generic_power_test(Mock(), Mock(), run_config)

        assert call_count[0] == 1
        assert all(r["status"] == "FAILED" for r in results)
        assert any("All queries failed" in str(c) for c in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_fail_fast_aborts_on_partial_failure(self, mock_console):
        adapter = self._make_adapter()
        call_count = [0]

        def fake_execute_all(benchmark, connection, run_config):
            call_count[0] += 1
            return [
                {"query_id": "Q1", "execution_time_seconds": 0.3, "status": "SUCCESS", "rows_returned": 5},
                {"query_id": "Q2", "execution_time_seconds": 0.0, "status": "FAILED", "rows_returned": 0},
            ]

        adapter._execute_all_queries = fake_execute_all
        run_config = {"iterations": 5, "warm_up_iterations": 0, "power_fail_fast": True}

        adapter._execute_generic_power_test(Mock(), Mock(), run_config)

        assert call_count[0] == 1
        assert any("fail_fast" in str(c) for c in mock_console.print.call_args_list)

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_summary_printed_with_success_rate(self, mock_console):
        adapter = self._make_adapter()

        def fake_execute_all(benchmark, connection, run_config):
            return [{"query_id": "Q1", "execution_time_seconds": 1.0, "status": "SUCCESS", "rows_returned": 3}]

        adapter._execute_all_queries = fake_execute_all
        run_config = {"iterations": 1, "warm_up_iterations": 0}

        results = adapter._execute_generic_power_test(Mock(), Mock(), run_config)

        assert len(results) == 1
        assert results[0]["status"] == "SUCCESS"
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "100.0%" in printed

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_no_summary_for_empty_results(self, _mock_console):
        adapter = self._make_adapter()
        adapter._execute_all_queries = lambda b, c, r: []
        run_config = {"iterations": 1, "warm_up_iterations": 0}

        results = adapter._execute_generic_power_test(Mock(), Mock(), run_config)

        assert results == []


class TestBuildExecutionPhasesVariants:
    def _make_query_results(self, n=2, status="SUCCESS"):
        return [
            {"query_id": f"Q{i}", "execution_time_seconds": float(i), "status": status, "rows_returned": 10}
            for i in range(1, n + 1)
        ]

    def _make_query_executions(self):
        from benchbox.platforms.base.models import QueryExecution

        return [
            QueryExecution(
                query_id="Q1",
                stream_id="standard",
                execution_order=1,
                execution_time_ms=1000.0,
                status="SUCCESS",
                rows_returned=10,
            )
        ]

    def test_throughput_execution_type_skips_power_test_phase(self):
        adapter = MockPlatformAdapter()
        query_results = self._make_query_results()
        query_executions = self._make_query_executions()
        run_config = {"test_execution_type": "throughput"}

        execution_phases, total_exec_time, power_test_phase, throughput_test_phase = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase=None
        )

        assert power_test_phase is None
        assert execution_phases.power_test is None

    def test_standard_execution_type_creates_power_test_phase(self):
        adapter = MockPlatformAdapter()
        query_results = self._make_query_results()
        query_executions = self._make_query_executions()
        run_config = {"test_execution_type": "standard"}

        execution_phases, total_exec_time, power_test_phase, throughput_test_phase = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase=None
        )

        assert power_test_phase is not None
        assert execution_phases.power_test is power_test_phase

    def test_captures_last_power_test_result_power_at_size(self):
        adapter = MockPlatformAdapter()
        power_result = SimpleNamespace(power_at_size=99.5)
        adapter._last_power_test_result = power_result
        query_results = self._make_query_results()
        query_executions = self._make_query_executions()
        run_config = {}

        execution_phases, _, power_test_phase, _ = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase=None
        )

        assert power_test_phase.power_at_size == 99.5
        assert adapter._last_power_test_result is None

    def test_captures_last_throughput_result(self):
        adapter = MockPlatformAdapter()
        stream = SimpleNamespace(
            stream_id=0,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:01:00",
            duration=60.0,
            queries_executed=1,
            query_results=[{"query_id": "Q1", "execution_time_seconds": 1.0, "success": True}],
        )
        throughput_result = SimpleNamespace(
            stream_results=[stream],
            total_time=60.0,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:01:00",
            config=SimpleNamespace(num_streams=1),
            throughput_at_size=720.0,
        )
        adapter._last_throughput_test_result = throughput_result
        query_results = self._make_query_results()
        query_executions = self._make_query_executions()
        run_config = {"test_execution_type": "throughput"}

        execution_phases, _, _, throughput_test_phase = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase=None
        )

        assert throughput_test_phase is not None
        assert adapter._last_throughput_test_result is None

    @pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
    def test_captures_outstanding_throughput_work_without_completed_queries(self, benchmark_name):
        adapter = MockPlatformAdapter()
        adapter._last_throughput_test_result = SimpleNamespace(
            stream_results=[],
            total_time=60.0,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:01:00",
            config=SimpleNamespace(num_streams=2),
            throughput_at_size=None,
            success=False,
            errors=["deadline expired"],
            outstanding_stream_ids=[0, 1],
            cleanup_state="outstanding",
        )

        _, _, _, throughput_test_phase = adapter._build_execution_phases(
            [],
            [],
            {"test_execution_type": "throughput", "benchmark_name": benchmark_name},
            setup_phase=None,
        )

        assert throughput_test_phase is not None
        assert throughput_test_phase.streams == []
        assert throughput_test_phase.total_queries_executed == 0
        assert throughput_test_phase.outstanding_work == {
            "stream_ids": [0, 1],
            "cleanup_state": "outstanding",
        }
        assert adapter._last_throughput_test_result is None

    def test_capture_plans_path_does_not_raise(self):
        adapter = MockPlatformAdapter()
        adapter.capture_plans = True
        adapter.query_plans_captured = 3
        adapter.plan_capture_failures = 0
        query_results = self._make_query_results()
        query_executions = self._make_query_executions()
        run_config = {}

        execution_phases, _, _, _ = adapter._build_execution_phases(
            query_results, query_executions, run_config, setup_phase=None
        )

        assert execution_phases is not None

    def test_total_exec_time_sums_only_successful_queries(self):
        adapter = MockPlatformAdapter()
        query_results = [
            {"query_id": "Q1", "execution_time_seconds": 2.0, "status": "SUCCESS", "rows_returned": 5},
            {"query_id": "Q2", "execution_time_seconds": 3.0, "status": "FAILED", "rows_returned": 0},
        ]
        query_executions = self._make_query_executions()

        _, total_exec_time, _, _ = adapter._build_execution_phases(
            query_results, query_executions, {}, setup_phase=None
        )

        assert total_exec_time == 2.0


class TestExceptionPathsForThroughputAndMaintenance:
    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_execute_tpch_throughput_test_resets_cached_result_on_exception(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()
        adapter._last_throughput_test_result = object()

        class BrokenTPCHThroughputTest:
            def __init__(self, **kwargs):
                pass

            def run(self, config=None):
                raise RuntimeError("tpch throughput boom")

        with patch("benchbox.core.tpch.throughput_test.TPCHThroughputTest", BrokenTPCHThroughputTest):
            results = adapter._execute_tpch_throughput_test(Mock(), Mock(), {"scale_factor": 1.0})

        assert adapter._last_throughput_test_result is None
        assert results == [
            {
                "query_id": "throughput_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "tpch throughput boom",
                "test_type": "throughput",
            }
        ]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_execute_tpcds_maintenance_test_returns_error_on_exception(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()

        class BrokenTPCDSMaintenanceTest:
            def __init__(self, **kwargs):
                raise RuntimeError("tpcds maintenance boom")

        with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", BrokenTPCDSMaintenanceTest):
            results = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 1.0})

        assert results == [
            {
                "query_id": "maintenance_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "tpcds maintenance boom",
                "test_type": "maintenance",
            }
        ]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_execute_tpch_maintenance_test_returns_error_on_exception(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()
        adapter.supports_refresh_functions = True

        class BrokenTPCHMaintenanceTest:
            def __init__(self, **kwargs):
                raise RuntimeError("tpch maintenance boom")

        with patch("benchbox.core.tpch.maintenance_test.TPCHMaintenanceTest", BrokenTPCHMaintenanceTest):
            results = adapter._execute_tpch_maintenance_test(Mock(), Mock(), {"scale_factor": 1.0})

        assert results == [
            {
                "query_id": "maintenance_test_error",
                "execution_time_seconds": 0.0,
                "status": "FAILED",
                "rows_returned": 0,
                "error": "tpch maintenance boom",
                "test_type": "maintenance",
            }
        ]

    @patch("benchbox.platforms.base.adapter.quiet_console")
    def test_execute_tpch_maintenance_test_reports_not_supported_platform(self, _mock_console):
        adapter = MockPlatformAdapterWithDialect()
        adapter.supports_refresh_functions = False
        connection = Mock()

        with patch("benchbox.core.tpch.maintenance_test.TPCHMaintenanceTest") as maintenance_test:
            results = adapter._execute_tpch_maintenance_test(Mock(), connection, {"scale_factor": 1.0})

        maintenance_test.assert_not_called()
        connection.execute.assert_not_called()
        connection.cursor.assert_not_called()
        assert results == [
            {
                "query_id": "maintenance_not_supported",
                "execution_time_seconds": 0.0,
                "status": "SKIPPED",
                "rows_returned": 0,
                "error": "TPC-H refresh functions are not supported by platform 'MockPlatformAdapterWithDialect'",
                "test_type": "maintenance",
            }
        ]

    @pytest.mark.parametrize(
        ("module_name", "class_name", "expected"),
        [
            ("benchbox.platforms.duckdb", "DuckDBAdapter", True),
            ("benchbox.platforms.sqlite", "SQLiteAdapter", True),
            ("benchbox.platforms.postgresql", "PostgreSQLAdapter", True),
            ("benchbox.platforms.singlestore", "SingleStoreAdapter", True),
            ("benchbox.platforms.snowflake", "SnowflakeAdapter", True),
            ("benchbox.platforms.redshift", "RedshiftAdapter", False),
            ("benchbox.platforms.cedardb", "CedarDBAdapter", False),
            ("benchbox.platforms.citus", "CitusAdapter", False),
            ("benchbox.platforms.paradedb", "ParadeDBAdapter", False),
            ("benchbox.platforms.pg_duckdb", "PgDuckDBAdapter", False),
            ("benchbox.platforms.pg_mooncake", "PgMooncakeAdapter", False),
            ("benchbox.platforms.timescaledb", "TimescaleDBAdapter", False),
        ],
    )
    def test_refresh_capability_matches_verified_platforms(self, module_name, class_name, expected):
        import importlib

        adapter_class = getattr(importlib.import_module(module_name), class_name)

        assert adapter_class.supports_refresh_functions is expected


class TestDataGenerationPhaseEdgeCases:
    def test_returns_none_when_benchmark_has_no_tables_attr(self):
        adapter = MockPlatformAdapter()
        benchmark = SimpleNamespace()

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is None

    def test_returns_none_when_tables_dict_empty(self):
        adapter = MockPlatformAdapter()
        benchmark = SimpleNamespace(tables={})

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is None

    def test_returns_none_when_tables_is_not_mapping(self):
        adapter = MockPlatformAdapter()
        benchmark = SimpleNamespace(tables="not_a_dict")

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is None

    def test_partial_failure_when_one_table_raises(self):
        adapter = MockPlatformAdapter()

        class BadTable:
            def __iter__(self):
                raise RuntimeError("disk full")

        benchmark = SimpleNamespace(tables={"good": [{"id": 1}, {"id": 2}], "bad": BadTable()})

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is not None
        assert result.status == "PARTIAL_FAILURE"
        assert result.tables_generated == 1
        assert result.per_table_stats["good"].status == "SUCCESS"
        assert result.per_table_stats["bad"].status == "FAILED"
        assert "disk full" in result.per_table_stats["bad"].error_message

    def test_table_with_string_data_skipped(self):
        adapter = MockPlatformAdapter()
        benchmark = SimpleNamespace(tables={"t": "some_string_path"})

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is not None
        assert result.tables_generated == 0

    def test_uses_impl_tables_when_no_direct_tables(self):
        adapter = MockPlatformAdapter()
        impl = SimpleNamespace(tables={"lineitem": [{"l_orderkey": 1}]})
        benchmark = SimpleNamespace(_impl=impl)

        result = adapter._create_enhanced_data_generation_phase(benchmark)

        assert result is not None
        assert result.tables_generated == 1
        assert "lineitem" in result.per_table_stats


class TestDialectQuerySelection:
    def test_get_dialect_queries_passes_platform_version_when_supported(self):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = BenchmarkWithVersionAwareQueries()

        queries = adapter._get_dialect_queries(
            benchmark,
            benchmark_slug="vector_search",
            connection=Mock(name="versioned_connection"),
        )

        assert queries == {"Q1": "SELECT 1"}
        assert benchmark.calls == [{"dialect": "mock_dialect", "platform_version": "1.0.0"}]

    def test_get_dialect_queries_preserves_strict_translation_failures(self):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = BenchmarkWithStrictTranslationFailure()

        with pytest.raises(SQLTranslationError):
            adapter._get_dialect_queries(
                benchmark,
                benchmark_slug="tpch",
                connection=Mock(name="strict_translation_connection"),
            )

    def test_get_dialect_queries_can_reject_generic_fallbacks(self):
        adapter = MockPlatformAdapterWithDialect()
        benchmark = BenchmarkWithUnexpectedTranslationFailure()

        with pytest.raises(RuntimeError, match="Dialect query extraction failed for benchmark tpchavoc"):
            adapter._get_dialect_queries(
                benchmark,
                benchmark_slug="tpchavoc",
                strict_translation=True,
            )

        assert adapter._get_dialect_queries(benchmark, benchmark_slug="tpchavoc") == {"Q1": "SELECT fallback"}


class TestStatisticsPhase:
    def test_gather_statistics_prefers_whole_database_analyze(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_tables = Mock()
        connection = Mock()

        stats_mode, tables_analyzed = adapter.gather_statistics(connection, ["a", "b"])

        adapter.analyze_tables.assert_called_once_with(connection)
        assert (stats_mode, tables_analyzed) == ("explicit", 2)

    def test_gather_statistics_falls_back_to_per_table_analyze(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        connection = Mock()

        stats_mode, tables_analyzed = adapter.gather_statistics(connection, ["a", "b"])

        assert adapter.analyze_table.call_args_list == [((connection, "a"),), ((connection, "b"),)]
        assert (stats_mode, tables_analyzed) == ("explicit", 2)

    def test_gather_statistics_reports_unsupported_without_analyze_surface(self):
        adapter = MockPlatformAdapter()

        assert adapter.gather_statistics(Mock(), ["a"]) == ("unsupported", 0)

    def test_run_statistics_phase_skips_benchmark_without_opt_in(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(Mock(), Mock(), benchmark_name="ssb", table_names=["customer"])

        assert phase is None
        adapter.analyze_table.assert_not_called()

    def test_run_statistics_phase_completed_for_opted_in_benchmark(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(Mock(), Mock(), benchmark_name="joinorder", table_names=["title", "name"])

        assert phase is not None
        assert phase.status == "COMPLETED"
        assert phase.stats_mode == "explicit"
        assert phase.tables_analyzed == 2
        assert phase.error_message is None

    def test_run_statistics_phase_records_failure_without_raising(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock(side_effect=RuntimeError("ANALYZE exploded"))

        phase = adapter.run_statistics_phase(Mock(), Mock(), benchmark_name="tpch", table_names=["lineitem"])

        assert phase is not None
        assert phase.status == "FAILED"
        assert phase.tables_analyzed == 0
        assert "ANALYZE exploded" in (phase.error_message or "")

    def test_run_statistics_phase_resolves_table_names_from_benchmark(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        benchmark = Mock()
        benchmark.get_table_names = Mock(return_value=["lineitem", "orders"])

        phase = adapter.run_statistics_phase(benchmark, Mock(), benchmark_name="tpch")

        assert phase is not None and phase.tables_analyzed == 2
        assert adapter.analyze_table.call_count == 2

    def test_reset_statistics_default_is_safe_noop(self):
        adapter = MockPlatformAdapter()

        assert adapter.reset_statistics(Mock(), ["a", "b"]) == "unsupported"

    def test_run_statistics_phase_reset_not_requested_omits_lifecycle_marker(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(Mock(), Mock(), benchmark_name="joinorder", table_names=["title"])

        assert phase is not None
        assert phase.stats_lifecycle is None

    def test_run_statistics_phase_reset_true_invokes_reset_hook_and_records_mode(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        adapter.reset_statistics = Mock(return_value="reset")
        connection = Mock()

        phase = adapter.run_statistics_phase(
            Mock(), connection, benchmark_name="joinorder", table_names=["title"], reset=True
        )

        adapter.reset_statistics.assert_called_once_with(connection, ["title"])
        assert phase is not None
        assert phase.stats_lifecycle == "reset"
        assert phase.status == "COMPLETED"

    def test_run_statistics_phase_reset_true_falls_back_safely_when_unsupported(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(
            Mock(), Mock(), benchmark_name="joinorder", table_names=["title"], reset=True
        )

        assert phase is not None
        assert phase.status == "COMPLETED"
        assert phase.stats_lifecycle == "unsupported"

    def test_run_statistics_phase_reset_hook_exception_does_not_abort_run(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        adapter.reset_statistics = Mock(side_effect=RuntimeError("cannot drop stats here"))

        phase = adapter.run_statistics_phase(
            Mock(), Mock(), benchmark_name="joinorder", table_names=["title"], reset=True
        )

        assert phase is not None
        assert phase.status == "COMPLETED"
        assert phase.stats_lifecycle == "unsupported"
        adapter.analyze_table.assert_called_once()

    def test_run_statistics_phase_reset_false_records_explicit_persist_marker(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        adapter.reset_statistics = Mock()

        phase = adapter.run_statistics_phase(
            Mock(), Mock(), benchmark_name="joinorder", table_names=["title"], reset=False
        )

        adapter.reset_statistics.assert_not_called()
        assert phase is not None
        assert phase.stats_lifecycle == "persist"

    def test_run_statistics_phase_per_table_timing_omitted_by_default(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(Mock(), Mock(), benchmark_name="joinorder", table_names=["title", "name"])

        assert phase is not None
        assert phase.per_table_ms is None

    def test_run_statistics_phase_per_table_timing_collects_breakdown_when_opted_in(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(
            Mock(),
            Mock(),
            benchmark_name="joinorder",
            table_names=["title", "name"],
            collect_per_table_timing=True,
        )

        assert phase is not None
        assert phase.per_table_ms is not None
        assert set(phase.per_table_ms) == {"title", "name"}
        assert all(isinstance(v, int) and v >= 0 for v in phase.per_table_ms.values())
        assert phase.stats_mode == "explicit"
        assert phase.tables_analyzed == 2

    def test_run_statistics_phase_per_table_timing_unavailable_for_whole_database_analyze(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_tables = Mock()

        phase = adapter.run_statistics_phase(
            Mock(),
            Mock(),
            benchmark_name="joinorder",
            table_names=["title", "name"],
            collect_per_table_timing=True,
        )

        assert phase is not None
        assert phase.per_table_ms is None
        adapter.analyze_tables.assert_called_once()

    def test_run_statistics_phase_per_table_timing_unavailable_when_gather_statistics_overridden(self):

        class AutoOnLoadAdapter(MockPlatformAdapter):
            def gather_statistics(self, connection, table_names):
                return "auto-on-load", 0

        adapter = AutoOnLoadAdapter()
        adapter.analyze_table = Mock()

        phase = adapter.run_statistics_phase(
            Mock(),
            Mock(),
            benchmark_name="joinorder",
            table_names=["title", "name"],
            collect_per_table_timing=True,
        )

        assert phase is not None
        assert phase.per_table_ms is None
        assert phase.stats_mode == "auto-on-load"
        adapter.analyze_table.assert_not_called()

    def test_run_statistics_phase_failed_build_still_records_reset_marker(self):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock(side_effect=RuntimeError("ANALYZE exploded"))
        adapter.reset_statistics = Mock(return_value="reset")

        phase = adapter.run_statistics_phase(
            Mock(), Mock(), benchmark_name="joinorder", table_names=["title"], reset=True
        )

        assert phase is not None
        assert phase.status == "FAILED"
        assert phase.stats_lifecycle == "reset"

    def test_run_benchmark_gathers_statistics_between_load_and_query(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.create_enhanced_benchmark_result.return_value = make_benchmark_results(
            benchmark_name="tpch",
            platform="mock",
            scale_factor=1.0,
            execution_id="stats_001",
            duration_seconds=1.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"table1": 100},
        )

        adapter.run_benchmark(mock_benchmark, gather_statistics=True, benchmark_name="tpch")

        adapter.analyze_table.assert_called_once()
        assert adapter.analyze_table.call_args[0][1] == "table1"
        phases = mock_benchmark.create_enhanced_benchmark_result.call_args.kwargs["phases"]
        stats = phases.setup.statistics_gathering
        assert stats is not None
        assert stats.status == "COMPLETED"
        assert stats.stats_mode == "explicit"
        assert stats.tables_analyzed == 1

    def test_run_benchmark_statistics_benchmark_name_gates_without_setting_benchmark_name(
        self, mock_benchmark, tmp_path
    ):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.create_enhanced_benchmark_result.return_value = make_benchmark_results(
            benchmark_name="tpch",
            platform="mock",
            scale_factor=1.0,
            execution_id="stats_003",
            duration_seconds=1.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"table1": 100},
        )

        adapter.run_benchmark(mock_benchmark, gather_statistics=True, statistics_benchmark_name="tpch")

        adapter.analyze_table.assert_called_once()
        phases = mock_benchmark.create_enhanced_benchmark_result.call_args.kwargs["phases"]
        stats = phases.setup.statistics_gathering
        assert stats is not None
        assert stats.status == "COMPLETED"
        assert stats.tables_analyzed == 1

    def test_run_benchmark_without_flag_keeps_legacy_semantics(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.create_enhanced_benchmark_result.return_value = make_benchmark_results(
            benchmark_name="tpch",
            platform="mock",
            scale_factor=1.0,
            execution_id="stats_002",
            duration_seconds=1.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"table1": 100},
        )

        adapter.run_benchmark(mock_benchmark, benchmark_name="tpch")

        adapter.analyze_table.assert_not_called()
        phases = mock_benchmark.create_enhanced_benchmark_result.call_args.kwargs["phases"]
        assert phases.setup.statistics_gathering is None

    def test_run_benchmark_threads_stats_reset_through_run_config(self, mock_benchmark, tmp_path):
        adapter = MockPlatformAdapter()
        adapter.analyze_table = Mock()
        adapter.reset_statistics = Mock(return_value="reset")
        mock_benchmark.output_dir = tmp_path
        mock_benchmark.create_enhanced_benchmark_result.return_value = make_benchmark_results(
            benchmark_name="tpch",
            platform="mock",
            scale_factor=1.0,
            execution_id="stats_004",
            duration_seconds=1.0,
            total_queries=2,
            successful_queries=2,
            total_execution_time=0.2,
            average_query_time=0.1,
            data_loading_time=0.5,
            schema_creation_time=0.1,
            total_rows_loaded=100,
            data_size_mb=1.0,
            table_statistics={"table1": 100},
        )

        adapter.run_benchmark(mock_benchmark, gather_statistics=True, stats_reset=True, benchmark_name="tpch")

        adapter.reset_statistics.assert_called_once()
        phases = mock_benchmark.create_enhanced_benchmark_result.call_args.kwargs["phases"]
        stats = phases.setup.statistics_gathering
        assert stats is not None
        assert stats.stats_lifecycle == "reset"


class TestDeferredConnectionClose:
    def test_close_waits_for_leaked_workers_after_result_capture_consumed_the_result(self):
        release = threading.Event()
        closed = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        adapter = MockPlatformAdapter()
        adapter._last_throughput_test_result = SimpleNamespace(
            stream_results=[],
            total_time=60.0,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:01:00",
            config=SimpleNamespace(num_streams=1),
            throughput_at_size=None,
            success=False,
            errors=["deadline expired"],
            outstanding_stream_ids=[0],
            cleanup_state="outstanding",
            outstanding_notes=[],
            _outstanding_futures={0: pool.submit(release.wait, 5.0)},
        )
        adapter.connection = Mock()
        adapter.close_connection = lambda _connection: closed.set()

        try:
            assert adapter._contain_outstanding_throughput_work({"stream_cleanup_timeout_seconds": 0.05}) is False
            adapter._build_execution_phases(
                [], [], {"test_execution_type": "throughput", "benchmark_name": "tpch"}, setup_phase=None
            )
            adapter._close_run_connection()

            assert not closed.wait(0.3)
            release.set()
            assert closed.wait(5.0)
        finally:
            release.set()
            pool.shutdown(wait=True)


class TestTPCHRefreshPowerConnectionAdapter:
    def test_wraps_a_separate_session_for_maintenance_statements(self):
        adapter = MockPlatformAdapter()
        connection = object()
        stream_connection = object()

        with patch(
            "benchbox.platforms.base.execution.open_stream_connection", return_value=stream_connection
        ) as open_connection:
            refresh_connection = adapter._make_tpch_refresh_connection_adapter(connection, 0.01)

        open_connection.assert_called_once_with(adapter, connection, "olap")
        assert refresh_connection.connection is stream_connection
        assert refresh_connection._maintenance_mode is True
        assert refresh_connection.benchmark_type == "tpch"
        assert refresh_connection.scale_factor == 0.01

    def test_maintenance_statements_use_a_cursor_when_the_session_has_no_execute(self):
        cursor = Mock()
        session = Mock(spec=["cursor", "commit", "rollback"])
        session.cursor.return_value = cursor

        wrapper = PlatformAdapterConnection(session, MockPlatformAdapter(), maintenance_mode=True)
        result = wrapper.execute("SELECT COUNT(*) FROM orders")
        wrapper.execute("DELETE FROM orders WHERE o_orderkey = %s", (1,))

        assert result is cursor
        cursor.execute.assert_any_call("SELECT COUNT(*) FROM orders")
        cursor.execute.assert_any_call("DELETE FROM orders WHERE o_orderkey = %s", (1,))

    def test_rejects_a_shared_session_for_refresh_statements(self):
        adapter = MockPlatformAdapter()
        connection = object()

        with patch("benchbox.platforms.base.execution.open_stream_connection", return_value=connection):
            with pytest.raises(RuntimeError, match="require a separate database session"):
                adapter._make_tpch_refresh_connection_adapter(connection, 0.01)

    def test_unquiesced_work_blocks_post_measurement_probes_on_the_shared_connection(self):
        release = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        adapter = MockPlatformAdapter()
        adapter._last_throughput_test_result = SimpleNamespace(
            outstanding_stream_ids=[0],
            cleanup_state="outstanding",
            outstanding_notes=[],
            _outstanding_futures={0: pool.submit(release.wait, 5.0)},
        )
        connection = Mock()
        probe = Mock()

        try:
            assert adapter._contain_outstanding_throughput_work({"stream_cleanup_timeout_seconds": 0.05}) is False
            assert adapter._post_measurement_contained is True
            with patch.object(adapter, "_collect_client_link_metadata", probe):
                assert adapter._collect_post_measurement_metadata(connection, {}) == 0.0
            probe.assert_not_called()
            connection.cursor.assert_not_called()
            assert adapter._client_link_metadata["collection_error_class"] == "OutstandingThroughputWork"
        finally:
            release.set()
            pool.shutdown(wait=True)
