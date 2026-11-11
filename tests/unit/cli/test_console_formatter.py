from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

import pytest

from benchbox.core.schemas import QueryResult
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    from benchbox.cli.output import ConsoleResultFormatter

    IMPORTS_AVAILABLE = True
    skip_reason = None
except ImportError as e:
    IMPORTS_AVAILABLE = False
    skip_reason = f"CLI modules not available: {e}"


@dataclass
class MockBenchmarkResult:
    benchmark_name: str = "test-benchmark"
    benchmark_id: str = "test-id"
    scale_factor: float = 1.0
    duration_seconds: float = 10.0
    query_results: list = None
    power_at_size: Optional[float] = None
    throughput_at_size: Optional[float] = None
    geometric_mean_execution_time: Optional[float] = None

    def __post_init__(self):
        if self.query_results is None:
            self.query_results = []


@dataclass
class MockQueryResult:
    query_id: str
    status: str
    execution_time_ms: float
    error_message: Optional[str] = None


@dataclass
class MockBenchmarkResults:
    scale_factor: float = 1.0
    platform: str = "test-platform"
    database_name: str = "test-db"
    successful_queries: int = 5
    total_queries: int = 5
    total_execution_time: float = 10.0
    average_query_time: float = 2.0
    benchmark_name: str = "test-benchmark"
    data_size_mb: Optional[float] = None
    schema_creation_time: Optional[float] = None
    data_loading_time: Optional[float] = None
    tuning_enabled: bool = False
    constraints_applied: Optional[str] = None
    query_results: Optional[list] = None


@pytest.mark.skipif(not IMPORTS_AVAILABLE, reason=skip_reason or "CLI modules not available")
class TestConsoleResultFormatter:
    @patch("benchbox.cli.output.console")
    def test_display_benchmark_summary_cli_result(self, mock_console):

        query_results = [
            QueryResult(
                query_id="Q1",
                query_name="Query 1",
                sql_text="SELECT 1",
                execution_time_ms=1000,
                rows_returned=1,
                status="SUCCESS",
                error_message=None,
            ),
            QueryResult(
                query_id="Q2",
                query_name="Query 2",
                sql_text="SELECT 2",
                execution_time_ms=2000,
                rows_returned=2,
                status="SUCCESS",
                error_message=None,
            ),
        ]

        result = make_benchmark_results(
            benchmark_name="TPC-H",
            benchmark_id="tpch",
            scale_factor=1.0,
            duration_seconds=10.5,
            total_queries=2,
            successful_queries=2,
            query_results=query_results,
            power_at_size=1234.56,
        )
        result.summary_metrics = {
            "total_queries": 2,
            "successful_queries": 2,
            "success_rate": 1.0,
        }

        ConsoleResultFormatter.display_benchmark_summary(result, verbose=False)

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]

        assert any("TPC-H" in call for call in calls)
        assert any("1.0" in call for call in calls)
        assert any("2/2 queries successful" in call for call in calls)

    @patch("benchbox.cli.output.console")
    def test_display_benchmark_summary_platform_result(self, mock_console):
        result = MockBenchmarkResults(
            scale_factor=0.1,
            platform="DuckDB",
            successful_queries=3,
            total_queries=5,
            total_execution_time=25.5,
            average_query_time=8.5,
            data_size_mb=100.0,
            schema_creation_time=1.5,
        )

        with patch(
            "builtins.hasattr",
            side_effect=lambda obj, attr: attr in ["data_size_mb", "schema_creation_time"],
        ):
            ConsoleResultFormatter.display_benchmark_summary(result, verbose=False)

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]

        assert any("0.1" in call for call in calls)
        assert any("DuckDB" in call for call in calls)
        assert any("3/5 successful" in call for call in calls)

    @patch("benchbox.cli.output.console")
    def test_display_query_performance_verbose(self, mock_console):
        query_results = [
            QueryResult(
                query_id="Q1",
                query_name="Query 1",
                sql_text="SELECT 1",
                status="SUCCESS",
                execution_time_ms=1000,
                rows_returned=1,
            ),
            QueryResult(
                query_id="Q2",
                query_name="Query 2",
                sql_text="SELECT 2",
                status="ERROR",
                execution_time_ms=0,
                rows_returned=0,
                error_message="Syntax error",
            ),
            QueryResult(
                query_id="Q3",
                query_name="Query 3",
                sql_text="SELECT 3",
                status="SUCCESS",
                execution_time_ms=3000,
                rows_returned=3,
            ),
        ]

        result = make_benchmark_results(
            benchmark_name="TPC-H",
            benchmark_id="tpch",
            scale_factor=1.0,
            duration_seconds=10.0,
            total_queries=3,
            successful_queries=2,
            failed_queries=1,
            query_results=query_results,
        )

        ConsoleResultFormatter.display_query_performance(result)

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]

        assert any("Query Details" in call for call in calls)

    def test_format_execution_statistics_cli_result(self):
        query_results = [
            QueryResult(
                query_id="Q1",
                query_name="Query 1",
                sql_text="SELECT 1",
                status="SUCCESS",
                execution_time_ms=1000,
                rows_returned=1,
            ),
            QueryResult(
                query_id="Q2",
                query_name="Query 2",
                sql_text="SELECT 2",
                status="SUCCESS",
                execution_time_ms=2000,
                rows_returned=2,
            ),
        ]

        result = make_benchmark_results(
            benchmark_name="TPC-H",
            benchmark_id="tpch",
            scale_factor=2.0,
            duration_seconds=15.5,
            total_queries=2,
            successful_queries=2,
            query_results=query_results,
            power_at_size=987.65,
        )

        stats = ConsoleResultFormatter.format_execution_statistics(result)

        assert isinstance(stats, dict)
        assert stats["benchmark"] == "TPC-H"
        assert stats["scale_factor"] == "2.0"
        assert stats["queries_total"] == "2"
        assert stats["queries_successful"] == "2"
        assert stats["power_at_size"] == "987.65"

    def test_format_execution_statistics_platform_result(self):
        result = MockBenchmarkResults(
            platform="DuckDB",
            scale_factor=1.5,
            total_queries=10,
            successful_queries=8,
            total_execution_time=45.2,
            average_query_time=5.65,
        )

        with patch("builtins.hasattr", side_effect=lambda obj, attr: attr == "platform"):
            stats = ConsoleResultFormatter.format_execution_statistics(result)

        assert isinstance(stats, dict)
        assert stats["platform"] == "DuckDB"
        assert stats["scale_factor"] == "1.5"
        assert stats["queries_total"] == "10"
        assert stats["queries_successful"] == "8"
        assert "total_execution_time" in stats
        assert "average_query_time" in stats

    @patch("benchbox.cli.output.console")
    def test_display_enhanced_benchmark_result(self, mock_console):
        from datetime import datetime

        from benchbox.core.results.models import (
            BenchmarkResults,
            ExecutionPhases,
            SetupPhase,
        )

        result = BenchmarkResults(
            benchmark_name="TPC-H Enhanced",
            platform="TestDB",
            scale_factor=1.0,
            execution_id="test_123",
            timestamp=datetime.now(),
            duration_seconds=15.5,
            query_definitions={},
            execution_phases=ExecutionPhases(setup=SetupPhase()),
            total_queries=2,
            successful_queries=2,
            failed_queries=0,
            total_execution_time=5.0,
            average_query_time=2.5,
        )

        result.phases = {"data_generation": {"status": "COMPLETED", "duration": 1.0}}
        result.resource_utilization = {"peak_memory_mb": 128.5, "avg_cpu_percent": 45.2}
        result.performance_characteristics = {"parallel_efficiency": 0.87}

        ConsoleResultFormatter.display_benchmark_summary(result, verbose=False)

        assert mock_console.print.called
        calls = [str(call) for call in mock_console.print.call_args_list]

        all_calls = " ".join(calls)
        assert "Phase Execution Summary" in all_calls
        assert "Resource Utilization" in all_calls
        assert "Performance Characteristics" in all_calls


class TestConsoleFormatterMocked:
    def test_mock_result_creation(self):
        if IMPORTS_AVAILABLE:
            assert ConsoleResultFormatter is not None
            return
        result = MockBenchmarkResult(benchmark_name="Test", scale_factor=1.0, duration_seconds=10.0)

        assert result.benchmark_name == "Test"
        assert result.scale_factor == 1.0
        assert result.duration_seconds == 10.0
        assert result.query_results == []

    def test_mock_platform_result(self):
        if IMPORTS_AVAILABLE:
            assert ConsoleResultFormatter is not None
            return
        result = MockBenchmarkResults(platform="MockDB", successful_queries=3, total_queries=5)

        assert result.platform == "MockDB"
        assert result.successful_queries == 3
        assert result.total_queries == 5


class TestFormattingIntegration:
    def test_formatting_import(self):
        from benchbox.utils import format_bytes, format_duration

        assert format_duration(1.5) == "1.500s"
        assert format_bytes(2048) == "2.00 KB"

    def test_formatting_consistency(self):
        from benchbox.utils import format_bytes, format_duration

        durations = [0.001, 0.5, 1.0, 30.0, 60.0, 120.0]
        for duration in durations:
            result = format_duration(duration)
            assert isinstance(result, str)
            assert len(result) > 0

        byte_sizes = [0, 1024, 1048576, 1073741824]
        for size in byte_sizes:
            result = format_bytes(size)
            assert isinstance(result, str)
            assert len(result) > 0
            assert "B" in result


