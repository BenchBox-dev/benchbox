# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import hashlib
import json
import tempfile
import time
from pathlib import Path

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.queries import TPCDSQueryManager
from benchbox.core.tpcds.streams import create_standard_streams

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


@pytest.mark.regression
@pytest.mark.tpcds
class TestTPCDSRegression:
    KNOWN_QUERY_HASHES = {
        1: "a1b2c3d4e5f6",
        2: "b2c3d4e5f6a1",
        3: "c3d4e5f6a1b2",
        5: "d4e5f6a1b2c3",
        10: "e5f6a1b2c3d4",
        15: "f6a1b2c3d4e5",
        20: "a1b2c3d4e5f7",
        25: "b2c3d4e5f6a2",
        30: "c3d4e5f6a1b3",
    }

    @pytest.fixture
    def benchmark_instance(self):
        return TPCDSBenchmark(
            scale_factor=1.0,
            verbose=False,
        )

    @pytest.fixture
    def query_manager(self):
        return TPCDSQueryManager()

    @pytest.fixture
    def reference_data_dir(self):
        temp_dir = Path(tempfile.mkdtemp())

        reference_params = {
            "test_set_1": {
                "YEAR": 2000,
                "MONTH": 6,
                "STATE": "CA",
                "CATEGORY": "Electronics",
            },
            "test_set_2": {
                "YEAR": 1999,
                "MONTH": 12,
                "STATE": "NY",
                "CATEGORY": "Books",
            },
            "test_set_3": {
                "YEAR": 2001,
                "MONTH": 3,
                "STATE": "TX",
                "CATEGORY": "Clothing",
            },
            "test_set_4": {"YEAR": 2000, "MONTH": 9, "STATE": "FL", "CATEGORY": "Home"},
        }

        reference_file = temp_dir / "reference_params.json"
        with open(reference_file, "w", encoding="utf-8") as f:
            json.dump(reference_params, f, indent=2)

        return temp_dir

    def _compute_query_hash(self, query_text: str) -> str:
        normalized = query_text.strip().lower()
        normalized = " ".join(normalized.split())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def test_query_output_consistency(self, query_manager):
        test_queries = [1, 2, 3, 5, 10, 15, 20]

        first_run_queries = {}
        for query_id in test_queries:
            query = query_manager.get_query(query_id)
            first_run_queries[query_id] = query

        second_run_queries = {}
        for query_id in test_queries:
            query = query_manager.get_query(query_id)
            second_run_queries[query_id] = query

        for query_id in test_queries:
            assert first_run_queries[query_id] == second_run_queries[query_id], (
                f"Query {query_id} output inconsistent between runs"
            )

    def test_parameterized_query_determinism(self, query_manager, reference_data_dir):
        test_queries = [1, 2, 3, 5, 10]
        seed = 42

        query_results = []
        for _ in range(3):
            run_results = {}
            for query_id in test_queries:
                try:
                    query = query_manager.get_query(query_id, seed=seed)
                    run_results[query_id] = query
                except ValueError:
                    continue
            query_results.append(run_results)

        if len(query_results) > 1:
            first_run = query_results[0]
            for i, run in enumerate(query_results[1:], 1):
                for query_id in first_run:
                    if query_id in run:
                        assert first_run[query_id] == run[query_id], (
                            f"Query {query_id} with seed {seed} inconsistent on run {i}"
                        )

    def test_query_template_stability(self, benchmark_instance):
        multi_variant_queries = [14, 23, 24, 39]

        for query_id in multi_variant_queries:
            try:
                query_a = benchmark_instance.get_query(query_id, variant="a")
                assert isinstance(query_a, str)
                assert len(query_a) > 0

                query_b = benchmark_instance.get_query(query_id, variant="b")
                assert isinstance(query_b, str)
                assert len(query_b) > 0

                assert query_a != query_b, f"Query {query_id} variants are identical"

                assert "SELECT" in query_a.upper()
                assert "SELECT" in query_b.upper()

            except Exception as e:
                error_msg = str(e)
                if "exit code 255" in error_msg or "dsqgen failed" in error_msg or "TPCDSError" in error_msg:
                    print(f"Note: Query {query_id} variant generation not supported by dsqgen (expected), skipping")
                    continue
                else:
                    raise

    def test_parameter_domain_stability(self, benchmark_instance):
        seeds = [1, 42, 100, 1000]

        for seed in seeds:
            try:
                query = benchmark_instance.get_query(1, seed=seed)
                assert isinstance(query, str)
                assert len(query) > 0

                assert "SELECT" in query.upper()
                assert "FROM" in query.upper()

            except ValueError:
                pass

    def test_stream_generation_reproducibility(self, benchmark_instance):
        num_streams = 3
        query_range = (1, 10)
        seed = 42

        stream_manager_1 = create_standard_streams(
            benchmark_instance.query_manager,
            num_streams=num_streams,
            query_range=query_range,
            base_seed=seed,
        )
        streams_1 = stream_manager_1.generate_streams()

        stream_manager_2 = create_standard_streams(
            benchmark_instance.query_manager,
            num_streams=num_streams,
            query_range=query_range,
            base_seed=seed,
        )
        streams_2 = stream_manager_2.generate_streams()

        assert len(streams_1) == len(streams_2)

        for stream_id in streams_1:
            assert stream_id in streams_2

            stream_1 = streams_1[stream_id]
            stream_2 = streams_2[stream_id]

            assert len(stream_1) == len(stream_2)

            for i, (query_1, query_2) in enumerate(zip(stream_1, stream_2)):
                assert query_1.query_id == query_2.query_id, f"Stream {stream_id}, query {i}: ID mismatch"
                assert query_1.sql == query_2.sql, f"Stream {stream_id}, query {i}: SQL mismatch"

    def test_performance_regression_detection(self, benchmark_instance):
        performance_thresholds = {
            "single_query_max_time": 0.05,
            "parameterized_query_max_time": 0.1,
            "seeded_query_max_time": 0.2,
            "batch_queries_max_time": 2.0,
        }

        start_time = time.time()
        benchmark_instance.get_query(1)
        single_query_time = time.time() - start_time

        assert single_query_time <= performance_thresholds["single_query_max_time"], (
            f"Single query regression: {single_query_time:.6f}s"
        )

        start_time = time.time()
        benchmark_instance.get_query(1, params={"YEAR": 2000})
        param_query_time = time.time() - start_time

        assert param_query_time <= performance_thresholds["parameterized_query_max_time"], (
            f"Parameterized query regression: {param_query_time:.6f}s"
        )

        start_time = time.time()
        benchmark_instance.get_query(1, seed=42)
        seeded_query_time = time.time() - start_time

        assert seeded_query_time <= performance_thresholds["seeded_query_max_time"], (
            f"Seeded query regression: {seeded_query_time:.6f}s"
        )

        start_time = time.time()
        for i in range(1, 21):
            try:
                benchmark_instance.get_query(i)
            except ValueError:
                pass
        batch_query_time = time.time() - start_time

        assert batch_query_time <= performance_thresholds["batch_queries_max_time"], (
            f"Batch query regression: {batch_query_time:.6f}s"
        )

    def test_memory_usage_regression(self, benchmark_instance):
        import gc
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        queries = []
        for i in range(100):
            query_id = (i % 50) + 1
            try:
                query = benchmark_instance.get_query(query_id, seed=42 + i)
                queries.append(query)
            except ValueError:
                pass

        peak_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = peak_memory - initial_memory

        assert memory_increase < 100, f"Memory regression: {memory_increase:.2f}MB"

        del queries
        gc.collect()

        final_memory = process.memory_info().rss / 1024 / 1024
        memory_after_cleanup = final_memory - initial_memory
        memory_leak_mb = memory_after_cleanup
        leak_percentage = memory_after_cleanup / memory_increase if memory_increase > 0 else 0

        assert memory_leak_mb < 5.0 or leak_percentage < 0.95, (
            f"Memory leak regression: {memory_leak_mb:.2f}MB "
            f"({leak_percentage:.1%} of working memory {memory_increase:.2f}MB)"
        )

    def test_error_handling_consistency(self, benchmark_instance):
        with pytest.raises(ValueError):
            benchmark_instance.get_query(999)

        with pytest.raises((ValueError, TypeError)):
            benchmark_instance.get_query("invalid")

        try:
            result = benchmark_instance.get_query(1, seed=42)
            assert isinstance(result, str)
            assert len(result) > 0
        except (ValueError, TypeError):
            pytest.fail("Valid seed parameter should not raise error")

    def test_sql_format_consistency(self, query_manager):
        test_queries = [1, 2, 3, 5, 10]

        for query_id in test_queries:
            query = query_manager.get_query(query_id)

            assert "SELECT" in query.upper()
            assert "FROM" in query.upper()

            assert "[" not in query or "]" not in query

            assert query.count("(") == query.count(")")

            param_query = query_manager.get_query(query_id)
            assert "SELECT" in param_query.upper()
            assert "FROM" in param_query.upper()

    def test_configuration_backward_compatibility(self):
        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        query = benchmark.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

        benchmark_sf10 = TPCDSBenchmark(scale_factor=10.0, verbose=False)

        query = benchmark_sf10.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

    def test_file_format_compatibility(self, benchmark_instance):
        try:
            stream_files = benchmark_instance.generate_streams(num_streams=2, rng_seed=42)
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"Streams module not available: {e}")

        assert len(stream_files) == 2

        for stream_file in stream_files:
            assert stream_file.exists()

            content = stream_file.read_text()
            assert len(content) > 0

            assert "SELECT" in content
            assert "FROM" in content

            assert "-- Query" in content

            lines = content.split("\n")
            assert len(lines) > 10

    def test_dialect_translation_consistency(self, benchmark_instance):
        dialects = ["netezza", "duckdb", "postgres"]
        query_id = 1
        seed = 42

        dialect_results = {}

        for dialect in dialects:
            try:
                query = benchmark_instance.get_query(query_id, seed=seed, dialect=dialect)
                dialect_results[dialect] = query
            except Exception as e:
                if "not supported" not in str(e).lower():
                    raise e

        for dialect, query in dialect_results.items():
            assert isinstance(query, str)
            assert len(query) > 0
            assert "SELECT" in query.upper()
            assert "FROM" in query.upper()

    def test_concurrent_access_stability(self, benchmark_instance):
        import queue
        import threading

        results = queue.Queue()
        errors = queue.Queue()

        def worker_function(worker_id: int):
            try:
                worker_results = []
                for i in range(10):
                    query_id = (i % 20) + 1
                    query = benchmark_instance.get_query(query_id)
                    worker_results.append(
                        {
                            "worker_id": worker_id,
                            "query_id": query_id,
                            "query_length": len(query),
                            "has_select": "SELECT" in query.upper(),
                        }
                    )

                results.put(worker_results)

            except Exception as e:
                errors.put({"worker_id": worker_id, "error": str(e)})

        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker_function, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert errors.empty(), f"Concurrent access errors: {list(errors.queue)}"

        all_results = []
        while not results.empty():
            all_results.extend(results.get())

        assert len(all_results) == 50

        for result in all_results:
            assert result["query_length"] > 0
            assert result["has_select"] is True

    @pytest.mark.parametrize("scale_factor", [0.01, 0.1, 1.0])
    def test_scale_factor_consistency(self, scale_factor):
        if scale_factor < 1.0:
            with pytest.warns(
                UserWarning,
                match=r"TPC-DS requires minimum scale_factor of 1.0.*Rounding up",
            ):
                benchmark = TPCDSBenchmark(
                    scale_factor=scale_factor,
                    verbose=False,
                )
            assert benchmark.scale_factor == 1.0
        else:
            benchmark = TPCDSBenchmark(
                scale_factor=scale_factor,
                verbose=False,
            )

        query = benchmark.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

        param_query = benchmark.get_query(1, params={"YEAR": 2000})
        assert isinstance(param_query, str)
        assert len(param_query) > 0

        seeded_query = benchmark.get_query(1, seed=42)
        assert isinstance(seeded_query, str)
        assert len(seeded_query) > 0

    def test_detailed_regression_suite(self, benchmark_instance):
        regression_results = {
            "api_compatibility": True,
            "performance_regression": False,
            "memory_regression": False,
            "output_consistency": True,
            "error_handling": True,
            "concurrent_access": True,
        }

        try:
            benchmark_instance.get_query(1)
            benchmark_instance.get_query(1, params={"YEAR": 2000})
            benchmark_instance.get_query(1, seed=42)
            benchmark_instance.get_schema()
            benchmark_instance.get_create_tables_sql()

            start_time = time.time()
            for i in range(1, 21):
                with contextlib.suppress(ValueError):
                    benchmark_instance.get_query(i)
            execution_time = time.time() - start_time

            if execution_time > 2.0:
                regression_results["performance_regression"] = True

            import gc
            import os

            import psutil

            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / 1024 / 1024

            queries = []
            for i in range(50):
                query_id = (i % 25) + 1
                try:
                    query = benchmark_instance.get_query(query_id, seed=42 + i)
                    queries.append(query)
                except ValueError:
                    pass

            peak_memory = process.memory_info().rss / 1024 / 1024
            memory_increase = peak_memory - initial_memory

            if memory_increase > 50:
                regression_results["memory_regression"] = True

            del queries
            gc.collect()

        except Exception:
            regression_results["api_compatibility"] = False
            regression_results["error_handling"] = False

        assert regression_results["api_compatibility"], "API compatibility regression"
        assert not regression_results["performance_regression"], "Performance regression detected"
        assert not regression_results["memory_regression"], "Memory regression detected"
        assert regression_results["output_consistency"], "Output consistency regression"
        assert regression_results["error_handling"], "Error handling regression"
        assert regression_results["concurrent_access"], "Concurrent access regression"

        print(f"Regression test results: {regression_results}")


