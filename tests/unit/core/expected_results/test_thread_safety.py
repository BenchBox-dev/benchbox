# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import threading

import pytest

from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)
from benchbox.core.expected_results.registry import ExpectedResultsRegistry
from benchbox.core.validation.query_validation import QueryValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestThreadSafety:
    @pytest.fixture
    def fresh_registry(self):
        return ExpectedResultsRegistry()

    def test_concurrent_provider_registration(self, fresh_registry):

        registration_results = []

        def register_provider(thread_id):
            try:

                def provider(sf):
                    return BenchmarkExpectedResults(
                        benchmark_name=f"test_benchmark_{thread_id}",
                        scale_factor=sf,
                        query_results={
                            "1": ExpectedQueryResult(
                                query_id="1",
                                expected_row_count=100 * thread_id,
                                validation_mode=ValidationMode.EXACT,
                            )
                        },
                    )

                fresh_registry.register_provider(f"test_benchmark_{thread_id}", provider)
                registration_results.append((thread_id, "success"))
            except Exception as e:
                registration_results.append((thread_id, f"error: {e}"))

        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_provider, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=5)

        assert len(registration_results) == 10
        assert all(result[1] == "success" for result in registration_results)

        benchmarks = fresh_registry.list_available_benchmarks()
        assert len(benchmarks) == 10

    def test_concurrent_cache_access(self, fresh_registry):

        def slow_provider(sf):
            import time

            time.sleep(0.1)
            return BenchmarkExpectedResults(
                benchmark_name="test_benchmark",
                scale_factor=sf,
                query_results={
                    "1": ExpectedQueryResult(query_id="1", expected_row_count=100, validation_mode=ValidationMode.EXACT)
                },
            )

        fresh_registry.register_provider("test_benchmark", slow_provider)

        access_results = []

        def access_cache(thread_id):
            try:
                result = fresh_registry.get_expected_result("test_benchmark", "1", scale_factor=1.0)
                access_results.append((thread_id, result is not None))
            except Exception as e:
                access_results.append((thread_id, f"error: {e}"))

        threads = []
        for i in range(5):
            thread = threading.Thread(target=access_cache, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert len(access_results) == 5
        assert all(result[1] is True for result in access_results)

    def test_concurrent_validations(self):

        validator = QueryValidator()
        validation_results = []

        def validate_query(thread_id, query_id):
            try:
                result = validator.validate_query_result(
                    benchmark_type="tpch", query_id=query_id, actual_row_count=4, scale_factor=1.0
                )
                validation_results.append((thread_id, query_id, result.is_valid))
            except Exception as e:
                validation_results.append((thread_id, query_id, f"error: {e}"))

        threads = []
        for i in range(20):
            query_id = (i % 5) + 1
            thread = threading.Thread(target=validate_query, args=(i, query_id))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert len(validation_results) == 20
        q1_validations = [r for r in validation_results if r[1] == 1]
        assert all(r[2] is True for r in q1_validations)

    def test_cache_clear_thread_safety(self, fresh_registry):

        def provider(sf):
            return BenchmarkExpectedResults(
                benchmark_name="test_benchmark",
                scale_factor=sf,
                query_results={
                    "1": ExpectedQueryResult(query_id="1", expected_row_count=100, validation_mode=ValidationMode.EXACT)
                },
            )

        fresh_registry.register_provider("test_benchmark", provider)

        fresh_registry.get_expected_result("test_benchmark", "1", scale_factor=1.0)

        clear_results = []
        access_results = []

        def clear_cache(thread_id):
            try:
                fresh_registry.clear_cache()
                clear_results.append((thread_id, "success"))
            except Exception as e:
                clear_results.append((thread_id, f"error: {e}"))

        def access_cache(thread_id):
            try:
                result = fresh_registry.get_expected_result("test_benchmark", "1", scale_factor=1.0)
                access_results.append((thread_id, result is not None))
            except Exception as e:
                access_results.append((thread_id, f"error: {e}"))

        threads = []
        for i in range(5):
            if i % 2 == 0:
                thread = threading.Thread(target=clear_cache, args=(i,))
            else:
                thread = threading.Thread(target=access_cache, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all("success" in str(r[1]) or r[1] is True for r in clear_results + access_results)

    def test_list_benchmarks_thread_safety(self, fresh_registry):

        def provider(sf):
            return BenchmarkExpectedResults(
                benchmark_name="test_benchmark",
                scale_factor=sf,
                query_results={
                    "1": ExpectedQueryResult(query_id="1", expected_row_count=100, validation_mode=ValidationMode.EXACT)
                },
            )

        fresh_registry.register_provider("test_benchmark", provider)

        list_results = []

        def list_benchmarks(thread_id):
            try:
                benchmarks = fresh_registry.list_available_benchmarks()
                list_results.append((thread_id, len(benchmarks)))
            except Exception as e:
                list_results.append((thread_id, f"error: {e}"))

        threads = []
        for i in range(10):
            thread = threading.Thread(target=list_benchmarks, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=5)

        assert len(list_results) == 10
        assert all(isinstance(r[1], int) for r in list_results)
        assert all(r[1] >= 1 for r in list_results)

    def test_cache_prevents_redundant_provider_calls(self, fresh_registry):

        call_count = {"count": 0}

        def counting_provider(sf):
            call_count["count"] += 1
            return BenchmarkExpectedResults(
                benchmark_name="test_benchmark",
                scale_factor=sf,
                query_results={
                    "1": ExpectedQueryResult(query_id="1", expected_row_count=100, validation_mode=ValidationMode.EXACT)
                },
            )

        fresh_registry.register_provider("test_benchmark", counting_provider)

        for i in range(10):
            result = fresh_registry.get_expected_result("test_benchmark", "1", scale_factor=1.0)
            assert result is not None

        assert call_count["count"] == 1

    def test_provider_exception_thread_safety(self, fresh_registry):

        def failing_provider(sf):
            raise ValueError("Simulated provider failure")

        fresh_registry.register_provider("failing_benchmark", failing_provider)

        access_results = []

        def access_failing_provider(thread_id):
            try:
                result = fresh_registry.get_expected_result("failing_benchmark", "1", scale_factor=1.0)
                access_results.append((thread_id, result is None))
            except Exception as e:
                access_results.append((thread_id, f"error: {e}"))

        threads = []
        for i in range(5):
            thread = threading.Thread(target=access_failing_provider, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=5)

        assert len(access_results) == 5
        assert all(r[1] is True for r in access_results)


