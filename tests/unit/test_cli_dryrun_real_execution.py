# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.cli.dryrun import DryRunExecutor
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.platforms.datafusion import DataFusionAdapter
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestPlatformAdapterDryRun:
    def test_platform_adapter_dry_run_mode_activation(self):
        adapter = DuckDBAdapter()

        assert adapter.dry_run_mode is False
        assert adapter.captured_sql == []

        adapter.enable_dry_run()
        assert adapter.dry_run_mode is True
        assert adapter.captured_sql == []
        assert adapter.query_counter == 0

    def test_platform_adapter_sql_capture(self):
        adapter = DuckDBAdapter()
        adapter.enable_dry_run()

        adapter.capture_sql("SELECT * FROM table1", "query", None)
        adapter.capture_sql("SELECT * FROM table2", "query", None)

        assert len(adapter.captured_sql) == 2
        assert adapter.captured_sql[0]["sql"] == "SELECT * FROM table1"
        assert adapter.captured_sql[1]["sql"] == "SELECT * FROM table2"
        assert adapter.captured_sql[0]["order"] == 1
        assert adapter.captured_sql[1]["order"] == 2

    def test_get_captured_sql_dict(self):
        adapter = DuckDBAdapter()
        adapter.enable_dry_run()

        adapter.capture_sql("SELECT 1", "query")
        adapter.capture_sql("SELECT 2", "query")

        queries_dict = adapter.get_captured_sql()
        assert queries_dict == {"1": "SELECT 1", "2": "SELECT 2"}

    def test_connection_wrapper_sql_interception(self):
        adapter = DuckDBAdapter()
        adapter.enable_dry_run()

        connection = adapter.create_connection()

        result = connection.execute("SELECT 42 as test")

        captured = adapter.get_captured_sql()
        assert len(captured) > 0
        assert "SELECT 42 as test" in str(captured.values())

        assert result.fetchall() == []
        assert result.fetchone() is None


class TestDryRunExecutor:
    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_extract_queries_load_only_returns_empty(self, mock_orchestrator):
        executor = DryRunExecutor()

        benchmark_config = Mock()
        benchmark_config.test_execution_type = "load_only"

        mock_benchmark = Mock()

        result = executor._extract_queries(mock_benchmark, benchmark_config)
        assert result == {}

    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_extract_queries_standard_fallback(self, mock_orchestrator):
        executor = DryRunExecutor()

        benchmark_config = Mock()
        benchmark_config.name = "test_benchmark"
        benchmark_config.test_execution_type = "standard"

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {"1": "SELECT 1", "2": "SELECT 2"}

        result = executor._extract_queries(mock_benchmark, benchmark_config)
        assert result == {"1": "SELECT 1", "2": "SELECT 2"}

    def test_extract_queries_via_real_test_execution_integration(self):
        executor = DryRunExecutor()

        benchmark_config = Mock()
        benchmark_config.name = "tpcds"
        benchmark_config.scale_factor = 0.01

        mock_benchmark = Mock()
        mock_benchmark.get_query.return_value = "SELECT 1 as test_query"

        mock_benchmark.get_queries.return_value = {"1": "SELECT 1 as captured_query"}
        result = executor._extract_queries_via_real_test_execution(mock_benchmark, benchmark_config, "power")
        assert result == {"1": "SELECT 1 as captured_query"}


class TestDryRunExecutorPlatformIntegration:
    @pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
    def test_test_mode_query_extraction_never_opens_platform_connection(self, benchmark_name):
        executor = DryRunExecutor()
        benchmark = Mock()
        benchmark.get_queries.return_value = {1: "SELECT 1"}
        benchmark_config = Mock(name=benchmark_name, scale_factor=0.01, test_execution_type="power")
        benchmark_config.name = benchmark_name
        adapter = Mock()
        adapter._get_dialect_queries.return_value = {1: "SELECT 1"}
        adapter._filter_queries.return_value = {1: "SELECT 1"}
        adapter.create_connection.side_effect = AssertionError("query extraction opened a platform connection")

        assert executor._extract_queries(benchmark, benchmark_config, adapter) == {"1": "SELECT 1"}
        adapter._get_dialect_queries.assert_called_once_with(
            benchmark,
            benchmark_slug=benchmark_name,
            connection=None,
            strict_translation=True,
        )
        adapter.create_connection.assert_not_called()
        adapter.enable_dry_run.assert_not_called()

    def test_extract_queries_with_platform_adapter(self):
        executor = DryRunExecutor()

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {"1": "SELECT 1"}

        mock_adapter = Mock()
        mock_adapter._get_dialect_queries.return_value = {"1": "SELECT 1 /* translated */"}
        mock_adapter._filter_queries.return_value = {"1": "SELECT 1 /* translated */"}

        benchmark_config = Mock()
        benchmark_config.name = "tpcds"
        benchmark_config.test_execution_type = "standard"

        result = executor._extract_queries(mock_benchmark, benchmark_config, mock_adapter)

        assert result == {"1": "SELECT 1 /* translated */"}
        mock_adapter._get_dialect_queries.assert_called_once_with(
            mock_benchmark,
            benchmark_slug="tpcds",
            connection=None,
            strict_translation=True,
        )

    def test_invalid_query_subset_fails_instead_of_saving_empty_preview(self):
        executor = DryRunExecutor()
        benchmark = TPCHavocBenchmark(scale_factor=0.01)
        benchmark_config = BenchmarkConfig(
            name="tpchavoc",
            display_name="TPC-Havoc",
            scale_factor=0.01,
            queries=["definitely-invalid"],
            test_execution_type="power",
        )

        with pytest.raises(RuntimeError, match="Dry-run query extraction failed.*Invalid query IDs"):
            executor._extract_queries(benchmark, benchmark_config, DataFusionAdapter())

    def test_adapter_translation_fallback_is_rejected_for_dry_run(self):
        executor = DryRunExecutor()
        benchmark = Mock()
        benchmark_config = Mock()
        benchmark_config.name = "tpchavoc"
        benchmark_config.test_execution_type = "standard"
        adapter = Mock()
        adapter._get_dialect_queries.side_effect = RuntimeError("translation failed")

        with pytest.raises(RuntimeError, match="Dry-run query extraction failed: translation failed"):
            executor._extract_queries(benchmark, benchmark_config, adapter)

    def test_extract_queries_standard_without_platform_adapter(self):
        executor = DryRunExecutor()

        mock_benchmark = Mock()
        mock_benchmark.get_queries.return_value = {"1": "SELECT 1", "2": "SELECT 2"}

        benchmark_config = Mock()
        benchmark_config.name = "test_benchmark"
        benchmark_config.test_execution_type = "standard"

        result = executor._extract_queries(mock_benchmark, benchmark_config)
        assert result == {"1": "SELECT 1", "2": "SELECT 2"}

    def test_load_only_mode_returns_empty_with_platform_adapter(self):
        executor = DryRunExecutor()

        benchmark_config = Mock()
        benchmark_config.test_execution_type = "load_only"

        mock_benchmark = Mock()
        mock_adapter = Mock()

        result = executor._extract_queries(mock_benchmark, benchmark_config, mock_adapter)
        assert result == {}
