# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from benchbox.experimental.gpu.benchmark import (
    GPU_BENCHMARK_QUERIES,
    GPUBenchmark,
    GPUBenchmarkResults,
    GPUQueryResult,
)
from benchbox.experimental.gpu.capabilities import GPUDevice, GPUInfo, GPUVendor
from benchbox.experimental.gpu.metrics import GPUMetricsAggregate

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import pandas

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class TestGPUQueryResult:
    def test_basic_creation(self):
        result = GPUQueryResult(
            query_id="aggregation_simple",
            success=True,
            execution_time_ms=150.5,
            row_count=1000,
        )
        assert result.query_id == "aggregation_simple"
        assert result.success is True
        assert result.execution_time_ms == 150.5
        assert result.row_count == 1000

    def test_failed_result(self):
        result = GPUQueryResult(
            query_id="test",
            success=False,
            execution_time_ms=50.0,
            error_message="Out of memory",
        )
        assert result.success is False
        assert result.error_message == "Out of memory"

    def test_with_gpu_metrics(self):
        result = GPUQueryResult(
            query_id="test",
            success=True,
            execution_time_ms=100.0,
            row_count=500,
            memory_used_mb=8000,
            peak_memory_mb=12000,
            gpu_utilization_percent=85.0,
        )
        assert result.memory_used_mb == 8000
        assert result.peak_memory_mb == 12000
        assert result.gpu_utilization_percent == 85.0

    def test_to_dict(self):
        result = GPUQueryResult(
            query_id="test",
            success=True,
            execution_time_ms=100.0,
            row_count=100,
        )
        d = result.to_dict()
        assert d["query_id"] == "test"
        assert d["success"] is True
        assert d["execution_time_ms"] == 100.0
        assert "timestamp" in d


class TestGPUBenchmarkResults:
    @pytest.fixture
    def gpu_info(self):
        return GPUInfo(
            available=True,
            device_count=1,
            devices=[
                GPUDevice(
                    index=0,
                    name="Test GPU",
                    vendor=GPUVendor.NVIDIA,
                    memory_total_mb=16000,
                )
            ],
        )

    def test_basic_creation(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        assert results.gpu_info == gpu_info
        assert results.total_queries == 0
        assert results.successful_queries == 0

    def test_add_successful_result(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            GPUQueryResult(
                query_id="q1",
                success=True,
                execution_time_ms=100.0,
                peak_memory_mb=10000,
            )
        )
        assert results.total_queries == 1
        assert results.successful_queries == 1
        assert results.total_execution_time_ms == 100.0
        assert results.peak_memory_mb == 10000

    def test_add_failed_result(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(
            GPUQueryResult(
                query_id="q1",
                success=False,
                execution_time_ms=50.0,
            )
        )
        assert results.total_queries == 1
        assert results.failed_queries == 1

    def test_add_multiple_results(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        for i in range(5):
            results.add_result(
                GPUQueryResult(
                    query_id=f"q{i}",
                    success=i % 2 == 0,
                    execution_time_ms=100.0,
                    peak_memory_mb=8000 + i * 1000,
                )
            )
        assert results.total_queries == 5
        assert results.successful_queries == 3
        assert results.failed_queries == 2
        assert results.peak_memory_mb == 12000

    def test_success_rate(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        for i in range(4):
            results.add_result(GPUQueryResult(query_id=f"q{i}", success=i < 3, execution_time_ms=100.0))
        assert results.success_rate == 0.75

    def test_avg_execution_time(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        for time_ms in [100, 200, 300]:
            results.add_result(GPUQueryResult(query_id="q", success=True, execution_time_ms=time_ms))
        assert results.avg_execution_time_ms == 200.0

    def test_complete(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        assert results.completed_at is None
        results.complete()
        assert results.completed_at is not None

    def test_complete_with_metrics(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        aggregate = GPUMetricsAggregate(
            device_index=0,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            avg_utilization_percent=75.0,
        )
        results.complete(aggregate)
        assert results.gpu_metrics_aggregate == aggregate
        assert results.avg_gpu_utilization == 75.0

    def test_to_dict(self, gpu_info):
        results = GPUBenchmarkResults(
            gpu_info=gpu_info,
            started_at=datetime.now(timezone.utc),
        )
        results.add_result(GPUQueryResult(query_id="q1", success=True, execution_time_ms=100.0))
        results.complete()
        d = results.to_dict()
        assert "gpu_info" in d
        assert d["total_queries"] == 1
        assert d["success_rate"] == 1.0
        assert "query_results" in d


class TestGPUBenchmarkQueries:
    def test_queries_defined(self):
        assert len(GPU_BENCHMARK_QUERIES) > 0
        assert "aggregation_simple" in GPU_BENCHMARK_QUERIES
        assert "join_inner" in GPU_BENCHMARK_QUERIES

    def test_query_structure(self):
        for query_id, query_def in GPU_BENCHMARK_QUERIES.items():
            assert "name" in query_def
            assert "description" in query_def
            assert "sql_template" in query_def
            assert "category" in query_def

    def test_query_categories(self):
        categories = set(q["category"] for q in GPU_BENCHMARK_QUERIES.values())
        assert "aggregation" in categories
        assert "join" in categories
        assert "window" in categories


class TestGPUBenchmark:
    @pytest.fixture
    def gpu_benchmark(self):
        return GPUBenchmark(scale_factor=0.1, seed=42)

    def test_basic_creation(self, gpu_benchmark):
        assert gpu_benchmark.name == "GPU Acceleration Benchmark"
        assert gpu_benchmark.version == "1.0"
        assert gpu_benchmark.scale_factor == 0.1

    def test_supported_platforms(self, gpu_benchmark):
        platforms = gpu_benchmark.get_supported_platforms()
        assert "cudf" in platforms
        assert "dask_cudf" in platforms

    def test_get_queries(self, gpu_benchmark):
        queries = gpu_benchmark.get_queries()
        assert len(queries) > 0
        assert "aggregation_simple" in queries

    def test_get_query_categories(self, gpu_benchmark):
        categories = gpu_benchmark.get_query_categories()
        assert len(categories) > 0
        assert "aggregation" in categories

    def test_get_queries_by_category(self, gpu_benchmark):
        agg_queries = gpu_benchmark.get_queries_by_category("aggregation")
        assert len(agg_queries) >= 2
        assert "aggregation_simple" in agg_queries

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_is_gpu_available_true(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(
            available=True,
            device_count=1,
            cudf_available=True,
        )
        assert gpu_benchmark.is_gpu_available() is True

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_is_gpu_available_false(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(available=False)
        assert gpu_benchmark.is_gpu_available() is False

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_get_gpu_info(self, mock_detect, gpu_benchmark):
        expected_info = GPUInfo(available=True, device_count=2)
        mock_detect.return_value = expected_info
        info = gpu_benchmark.get_gpu_info()
        assert info == expected_info

    @pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")
    def test_generate_data(self, gpu_benchmark, tmp_path):
        gpu_benchmark.output_dir = tmp_path
        files = gpu_benchmark.generate_data(output_format="csv")
        assert "gpu_benchmark_main" in files
        assert "gpu_benchmark_dim" in files

    @pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")
    def test_generate_data_parquet(self, gpu_benchmark, tmp_path):
        gpu_benchmark.output_dir = tmp_path
        files = gpu_benchmark.generate_data(output_format="parquet")
        assert "gpu_benchmark_main" in files

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_export_benchmark_spec(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(available=True, device_count=1)
        spec = gpu_benchmark.export_benchmark_spec()
        assert spec["name"] == "GPU Acceleration Benchmark"
        assert "supported_platforms" in spec
        assert "categories" in spec
        assert "queries" in spec
        assert "gpu_info" in spec


class TestGPUBenchmarkScaling:
    @pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")
    def test_scale_factor_affects_data_size(self, tmp_path):
        small_bm = GPUBenchmark(scale_factor=0.1)
        small_bm.output_dir = tmp_path / "small"
        small_bm.output_dir.mkdir()

        large_bm = GPUBenchmark(scale_factor=1.0)
        large_bm.output_dir = tmp_path / "large"
        large_bm.output_dir.mkdir()

        small_files = small_bm.generate_data(output_format="csv")
        large_files = large_bm.generate_data(output_format="csv")

        import os

        small_size = os.path.getsize(small_files["gpu_benchmark_main"])
        large_size = os.path.getsize(large_files["gpu_benchmark_main"])
        assert large_size > small_size


class TestGPUBenchmarkExtraCoverage:
    @pytest.fixture
    def gpu_benchmark(self):
        return GPUBenchmark(scale_factor=0.01, seed=42)

    def test_get_query_with_params(self, gpu_benchmark):
        sql = gpu_benchmark.get_query(
            "aggregation_simple",
            params={"table": "my_table", "group_col": "cat", "sum_col": "val"},
        )
        assert "my_table" in sql
        assert "cat" in sql

    def test_get_query_invalid_raises(self, gpu_benchmark):
        with pytest.raises(ValueError, match="Unknown query ID"):
            gpu_benchmark.get_query("nonexistent_query_xyz")

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_is_gpu_available_uses_cache(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(available=True, cudf_available=True, device_count=1)
        gpu_benchmark._gpu_info = None
        _ = gpu_benchmark.is_gpu_available()
        _ = gpu_benchmark.is_gpu_available()
        assert mock_detect.call_count == 1

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_get_gpu_info_uses_cache(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(available=False)
        gpu_benchmark._gpu_info = None
        _ = gpu_benchmark.get_gpu_info()
        _ = gpu_benchmark.get_gpu_info()
        assert mock_detect.call_count == 1

    def test_execute_gpu_query_success(self, gpu_benchmark):
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)

        result = gpu_benchmark.execute_gpu_query(mock_df, "aggregation_simple", "SELECT COUNT(*) FROM data")
        assert isinstance(result, GPUQueryResult)

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_run_benchmark_no_gpu_raises(self, mock_detect, gpu_benchmark):
        mock_detect.return_value = GPUInfo(available=False)

        with pytest.raises(RuntimeError, match="No GPU available"):
            gpu_benchmark.run_benchmark(MagicMock())

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_run_benchmark_with_gpu(self, mock_detect, gpu_benchmark):
        gpu_info = GPUInfo(
            available=True,
            device_count=1,
            cudf_available=True,
            devices=[GPUDevice(index=0, name="Test GPU", vendor=GPUVendor.NVIDIA, memory_total_mb=8000)],
        )
        mock_detect.return_value = gpu_info
        gpu_benchmark._gpu_info = gpu_info
        gpu_benchmark.collect_metrics = False

        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=10)

        results = gpu_benchmark.run_benchmark(mock_df)
        assert isinstance(results, GPUBenchmarkResults)
        assert results.total_queries > 0

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_run_benchmark_with_query_ids(self, mock_detect, gpu_benchmark):
        gpu_info = GPUInfo(available=True, device_count=1, cudf_available=True)
        mock_detect.return_value = gpu_info
        gpu_benchmark._gpu_info = gpu_info
        gpu_benchmark.collect_metrics = False

        mock_df = MagicMock()
        results = gpu_benchmark.run_benchmark(mock_df, query_ids=["aggregation_simple"])
        assert results.total_queries == 1

    @patch("benchbox.experimental.gpu.benchmark.detect_gpu")
    def test_run_benchmark_with_categories(self, mock_detect, gpu_benchmark):
        gpu_info = GPUInfo(available=True, device_count=1, cudf_available=True)
        mock_detect.return_value = gpu_info
        gpu_benchmark._gpu_info = gpu_info
        gpu_benchmark.collect_metrics = False

        mock_df = MagicMock()
        results = gpu_benchmark.run_benchmark(mock_df, categories=["aggregation"])
        assert results.total_queries >= 2

    def test_compare_cpu_vs_gpu(self, gpu_benchmark):
        import numpy as np
        import pandas as pd

        from benchbox.experimental.gpu.benchmark import compare_cpu_vs_gpu

        pandas_df = pd.DataFrame({"category": list("ABCDE") * 20, "value": np.random.uniform(0, 100, 100)})
        mock_gpu_df = MagicMock()
        mock_gpu_df.__len__ = MagicMock(return_value=100)

        result = compare_cpu_vs_gpu(pandas_df, mock_gpu_df, gpu_benchmark, query_ids=["aggregation_simple"])
        assert "queries" in result
        assert "summary" in result
        assert "aggregation_simple" in result["queries"]


from unittest.mock import MagicMock


