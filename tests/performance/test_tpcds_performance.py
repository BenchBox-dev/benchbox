# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gc
import multiprocessing
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.queries import TPCDSQueryManager
from benchbox.core.tpcds.streams import create_standard_streams
from benchbox.monitoring import PerformanceMonitor

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


@pytest.mark.performance
@pytest.mark.tpcds
class TestTPCDSPerformance:
    @pytest.fixture
    def benchmark_instance(self):
        return TPCDSBenchmark(scale_factor=1.0, verbose=False)

    @pytest.fixture
    def query_manager(self):
        return TPCDSQueryManager()

    @pytest.fixture
    def performance_monitor(self):
        return PerformanceMonitor()

    def test_query_generation_performance_benchmark(self, query_manager, performance_monitor):
        test_queries = [1, 2, 3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
        iterations = 10

        for _ in range(3):
            query_manager.get_query(1)

        start_time = time.time()
        for _ in range(iterations):
            for query_id in test_queries:
                query_manager.get_query(query_id)
        raw_query_time = time.time() - start_time

        start_time = time.time()
        for _ in range(iterations):
            for query_id in test_queries:
                query_manager.get_query(query_id)
        param_query_time = time.time() - start_time

        queries_per_second_raw = (len(test_queries) * iterations) / raw_query_time
        queries_per_second_param = (len(test_queries) * iterations) / param_query_time

        assert queries_per_second_raw >= 100, f"Raw query generation too slow: {queries_per_second_raw:.2f} q/s"

        assert queries_per_second_param >= 50, (
            f"Parameterized query generation too slow: {queries_per_second_param:.2f} q/s"
        )

        performance_monitor.increment_counter("raw_queries_completed", len(test_queries) * iterations)
        performance_monitor.increment_counter("param_queries_completed", len(test_queries) * iterations)

        print(f"Raw queries per second: {queries_per_second_raw:.2f}")
        print(f"Parameterized queries per second: {queries_per_second_param:.2f}")

    def test_memory_usage_validation(self, benchmark_instance, performance_monitor):
        process = psutil.Process(os.getpid())
        system_memory_mb = psutil.virtual_memory().total / 1024 / 1024
        initial_memory = process.memory_info().rss / 1024 / 1024

        queries = []
        for i in range(1, 100):
            try:
                query = benchmark_instance.get_query(i, seed=42)
                queries.append(query)
            except ValueError:
                pass

        current_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = current_memory - initial_memory

        dynamic_limit_mb = max(300.0, min(system_memory_mb * 0.08, 768.0))
        assert memory_increase < dynamic_limit_mb, (
            f"Memory usage too high: increase={memory_increase:.2f}MB "
            f"(limit={dynamic_limit_mb:.2f}MB, initial={initial_memory:.2f}MB, current={current_memory:.2f}MB)"
        )

        del queries
        gc.collect()

        final_memory = process.memory_info().rss / 1024 / 1024
        memory_after_cleanup = final_memory - initial_memory

        cleanup_limit_mb = max(memory_increase * 0.95, dynamic_limit_mb)
        assert memory_after_cleanup < cleanup_limit_mb, (
            f"Memory leak detected: retained={memory_after_cleanup:.2f}MB "
            f"(limit={cleanup_limit_mb:.2f}MB, final={final_memory:.2f}MB)"
        )

        performance_monitor.increment_counter("memory_tests_completed", 1)

    def test_concurrent_query_generation_performance(self, query_manager, performance_monitor):
        test_queries = [1, 2, 3, 5, 10, 15, 20, 25, 30]
        num_threads = 4
        queries_per_thread = 25

        results = []
        errors = []

        def worker_function(worker_id: int):
            try:
                start_time = time.time()
                worker_queries = []

                for i in range(queries_per_thread):
                    query_id = test_queries[i % len(test_queries)]
                    query = query_manager.get_query(query_id)
                    worker_queries.append(query)

                end_time = time.time()

                results.append(
                    {
                        "worker_id": worker_id,
                        "execution_time_seconds": end_time - start_time,
                        "queries_generated": len(worker_queries),
                        "queries_per_second": len(worker_queries) / (end_time - start_time),
                    }
                )

            except Exception as e:
                errors.append({"worker_id": worker_id, "error": str(e)})

        threads = []
        start_time = time.time()

        for i in range(num_threads):
            thread = threading.Thread(target=worker_function, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        total_time = time.time() - start_time

        assert len(errors) == 0, f"Concurrent access errors: {errors}"

        assert len(results) == num_threads, f"Expected {num_threads} results, got {len(results)}"

        total_queries = sum(r["queries_generated"] for r in results)
        overall_qps = total_queries / total_time
        avg_worker_qps = statistics.mean([r["queries_per_second"] for r in results])

        assert overall_qps >= 50, f"Overall concurrent performance too low: {overall_qps:.2f} q/s"
        assert avg_worker_qps >= 25, f"Average worker performance too low: {avg_worker_qps:.2f} q/s"

        performance_monitor.increment_counter("concurrent_tests_completed", 1)

    def test_stream_generation_performance(self, benchmark_instance, performance_monitor):
        num_streams = 5
        query_range = (1, 20)

        start_time = time.time()

        stream_manager = create_standard_streams(
            benchmark_instance.query_manager,
            num_streams=num_streams,
            query_range=query_range,
            base_seed=42,
        )

        streams = stream_manager.generate_streams()

        end_time = time.time()
        generation_time = end_time - start_time

        assert len(streams) == num_streams, f"Expected {num_streams} streams, got {len(streams)}"

        total_queries = sum(len(stream) for stream in streams.values())

        queries_per_second = total_queries / generation_time

        assert queries_per_second >= 10, f"Stream generation too slow: {queries_per_second:.2f} q/s"

        performance_monitor.increment_counter("stream_tests_completed", 1)

    def test_performance_regression_detection(self, benchmark_instance, performance_monitor):
        baseline_metrics = {
            "single_query_time": 0.025,
            "parameterized_query_time": 0.04,
            "stream_query_time": 0.1,
            "memory_per_query": 0.1,
        }

        start_time = time.time()
        benchmark_instance.get_query(1)
        single_query_time = time.time() - start_time

        start_time = time.time()
        benchmark_instance.get_query(1)
        param_query_time = time.time() - start_time

        start_time = time.time()
        benchmark_instance.get_query(1, seed=42)
        seeded_query_time = time.time() - start_time

        assert single_query_time <= baseline_metrics["single_query_time"] * 2, (
            f"Single query regression: {single_query_time:.6f}s > {baseline_metrics['single_query_time'] * 2:.6f}s"
        )

        assert param_query_time <= baseline_metrics["parameterized_query_time"] * 2, (
            f"Parameterized query regression: {param_query_time:.6f}s > {baseline_metrics['parameterized_query_time'] * 2:.6f}s"
        )

        assert seeded_query_time <= baseline_metrics["stream_query_time"] * 2, (
            f"Seeded query regression: {seeded_query_time:.6f}s > {baseline_metrics['stream_query_time'] * 2:.6f}s"
        )

        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)

    def test_scalability_with_increasing_load(self, query_manager, performance_monitor):
        load_levels = [10, 50, 100, 200]
        results = {}
        baseline_qps = 0.0

        for load in load_levels:
            start_time = time.time()

            for i in range(load):
                query_id = (i % 50) + 1
                query_manager.get_query(query_id)

            end_time = time.time()
            execution_time = end_time - start_time
            qps = load / execution_time

            results[load] = {
                "execution_time_seconds": execution_time,
                "queries_per_second": qps,
            }

            if load == 10:
                baseline_qps = qps
            else:
                min_acceptable_qps = baseline_qps * 0.5
                assert qps >= min_acceptable_qps, (
                    f"Performance degradation at load {load}: {qps:.2f} < {min_acceptable_qps:.2f} q/s"
                )

        for load, _metrics in results.items():
            performance_monitor.increment_counter("test_completed", 1)
            performance_monitor.increment_counter("test_completed", 1)

    def test_memory_efficiency_under_load(self, benchmark_instance, performance_monitor):
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        memory_measurements = []
        queries_generated = 0

        for _batch in range(10):
            batch_queries = []

            for i in range(20):
                query_id = (i % 30) + 1
                query = benchmark_instance.get_query(query_id, seed=42 + i)
                batch_queries.append(query)
                queries_generated += 1

            current_memory = process.memory_info().rss / 1024 / 1024
            memory_increase = current_memory - initial_memory
            memory_measurements.append(memory_increase)

            del batch_queries
            gc.collect()

        max_memory_increase = max(memory_measurements)
        final_memory_increase = memory_measurements[-1]

        assert max_memory_increase < 350, f"Memory usage too high: {max_memory_increase:.2f}MB"

        memory_growth_rate = (final_memory_increase - memory_measurements[0]) / len(memory_measurements)
        assert memory_growth_rate < 35, f"Memory growth rate too high: {memory_growth_rate:.2f}MB/batch"

        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)

    @pytest.mark.parametrize("num_workers", [1, 2, 4, 8])
    def test_parallel_processing_scalability(self, query_manager, performance_monitor, num_workers):
        queries_per_worker = 25
        test_queries = list(range(1, 21))

        def worker_task(worker_queries):
            results = []
            for query_id in worker_queries:
                query = query_manager.get_query(query_id)
                results.append(len(query))
            return results

        work_chunks = []
        for i in range(num_workers):
            chunk = [
                test_queries[j % len(test_queries)] for j in range(i * queries_per_worker, (i + 1) * queries_per_worker)
            ]
            work_chunks.append(chunk)

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, chunk) for chunk in work_chunks]
            [future.result() for future in futures]
        end_time = time.time()

        execution_time = end_time - start_time
        total_queries = num_workers * queries_per_worker
        qps = total_queries / execution_time

        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)

        min_acceptable_qps = 10
        assert qps >= min_acceptable_qps, (
            f"Parallel processing performance too low: {qps:.2f} < {min_acceptable_qps:.2f} q/s"
        )

    def test_performance_monitoring_overhead(self, benchmark_instance, performance_monitor):
        start_time = time.time()
        for i in range(50):
            benchmark_instance.get_query((i % 20) + 1)
        time_without_monitoring = time.time() - start_time

        start_time = time.time()
        for i in range(50):
            with performance_monitor.time_operation(f"query_{i}"):
                benchmark_instance.get_query((i % 20) + 1)
        time_with_monitoring = time.time() - start_time

        overhead_ratio = time_with_monitoring / time_without_monitoring
        assert overhead_ratio < 1.5, f"Performance monitoring overhead too high: {overhead_ratio:.2f}x"

        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)
        performance_monitor.increment_counter("test_completed", 1)

    def test_detailed_performance_report(self, benchmark_instance, performance_monitor):
        report = {
            "timestamp": time.time(),
            "test_environment": {
                "cpu_count": multiprocessing.cpu_count(),
                "memory_total": psutil.virtual_memory().total / 1024 / 1024 / 1024,
                "python_version": f"{psutil.Process().python_version if hasattr(psutil.Process(), 'python_version') else 'unknown'}",
            },
            "performance_metrics": {},
            "benchmark_results": {},
        }

        test_queries = [1, 5, 10, 15, 20, 25, 30]

        start_time = time.time()
        for query_id in test_queries:
            query = benchmark_instance.get_query(query_id)
        raw_query_time = time.time() - start_time

        start_time = time.time()
        for query_id in test_queries:
            query = benchmark_instance.get_query(query_id)
        param_query_time = time.time() - start_time

        start_time = time.time()
        for query_id in test_queries:
            query = benchmark_instance.get_query(query_id, seed=42)
        seeded_query_time = time.time() - start_time

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        queries = []
        for i in range(100):
            query_id = (i % 50) + 1
            query = benchmark_instance.get_query(query_id, seed=42 + i)
            queries.append(query)

        peak_memory = process.memory_info().rss / 1024 / 1024
        memory_usage = peak_memory - initial_memory

        del queries
        gc.collect()

        report["performance_metrics"] = {
            "raw_query_time": raw_query_time,
            "param_query_time": param_query_time,
            "seeded_query_time": seeded_query_time,
            "memory_usage_mb": memory_usage,
            "queries_tested": len(test_queries),
            "raw_queries_per_second": len(test_queries) / raw_query_time,
            "param_queries_per_second": len(test_queries) / param_query_time,
            "seeded_queries_per_second": len(test_queries) / seeded_query_time,
        }

        report["benchmark_results"] = {
            "overall_performance": "PASS"
            if all(
                [
                    report["performance_metrics"]["raw_queries_per_second"] >= 5,
                    report["performance_metrics"]["param_queries_per_second"] >= 5,
                    report["performance_metrics"]["seeded_queries_per_second"] >= 2,
                    report["performance_metrics"]["memory_usage_mb"] < 300,
                ]
            )
            else "FAIL",
            "performance_grade": "A" if report["performance_metrics"]["raw_queries_per_second"] >= 10 else "B",
        }

        for _key, _value in report["performance_metrics"].items():
            performance_monitor.increment_counter("test_completed", 1)

        assert report["benchmark_results"]["overall_performance"] == "PASS", (
            f"Comprehensive performance test failed: {report['benchmark_results']}"
        )

        print(f"Performance Report: {report['performance_metrics']}")
        print(f"Performance Grade: {report['benchmark_results']['performance_grade']}")


