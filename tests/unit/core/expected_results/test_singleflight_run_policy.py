# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import threading
import time

import pytest

from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)
from benchbox.core.expected_results.registry import ExpectedResultsRegistry, LoadOutcome
from benchbox.core.validation.query_validation import (
    QueryValidator,
    clear_validation_mode_context,
    get_validation_mode_context,
    set_validation_mode_context,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def fresh_registry():
    return ExpectedResultsRegistry()


@pytest.fixture(autouse=True)
def _isolate_run_policy(monkeypatch):
    clear_validation_mode_context()
    monkeypatch.delenv("BENCHBOX_QUERY_VALIDATION_MODE", raising=False)
    yield
    clear_validation_mode_context()


def _answer_set(count=100):
    return BenchmarkExpectedResults(
        benchmark_name="policy_bench",
        scale_factor=1.0,
        query_results={
            "1": ExpectedQueryResult(
                query_id="1",
                expected_row_count=count,
                validation_mode=ValidationMode.SKIP,
            )
        },
    )


class TestAtomicPublication:
    def test_slow_success_reaches_every_waiter_identically(self, fresh_registry):
        release = threading.Event()
        calls = {"count": 0}

        def slow_provider(sf):
            calls["count"] += 1
            assert release.wait(timeout=10)
            return _answer_set()

        fresh_registry.register_provider("policy_bench", slow_provider)

        results = [None] * 5

        def wait_for_load(index):
            results[index] = fresh_registry.get_expected_result("policy_bench", "1", scale_factor=1.0)

        threads = [threading.Thread(target=wait_for_load, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        time.sleep(0.2)
        assert calls["count"] == 1
        release.set()
        for thread in threads:
            thread.join(timeout=10)

        assert all(result is not None for result in results)
        assert all(result is results[0] for result in results)

    def test_permanent_absence_publishes_once_and_stays_skippable(self, fresh_registry):
        calls = {"count": 0}

        def missing_provider(sf):
            calls["count"] += 1
            raise FileNotFoundError("no answers directory")

        fresh_registry.register_provider("policy_bench", missing_provider)

        first, outcome_first = fresh_registry.get_expected_result_detailed("policy_bench", "1")
        second, outcome_second = fresh_registry.get_expected_result_detailed("policy_bench", "1")

        assert first is None and second is None
        assert outcome_first is LoadOutcome.NO_ANSWER_SET
        assert outcome_second is LoadOutcome.NO_ANSWER_SET
        assert calls["count"] == 1

    def test_transient_failure_is_not_cached_and_allows_retry(self, fresh_registry):
        calls = {"count": 0}

        def flaky_provider(sf):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ValueError("transient boom")
            return _answer_set()

        fresh_registry.register_provider("policy_bench", flaky_provider)

        failed, failed_outcome = fresh_registry.get_expected_result_detailed("policy_bench", "1")
        recovered, recovered_outcome = fresh_registry.get_expected_result_detailed("policy_bench", "1")

        assert failed is None
        assert failed_outcome is LoadOutcome.PROVIDER_FAILED
        assert recovered is not None
        assert recovered_outcome is LoadOutcome.HIT
        assert calls["count"] == 2

    def test_waiter_timeout_reports_timeout_while_load_continues(self, fresh_registry):
        release = threading.Event()
        loader_outcome = {}

        def slow_provider(sf):
            assert release.wait(timeout=10)
            return _answer_set()

        fresh_registry.register_provider("policy_bench", slow_provider)
        fresh_registry.wait_timeout_seconds = 0.2

        def load_in_background():
            _, outcome = fresh_registry.get_expected_result_detailed("policy_bench", "1")
            loader_outcome["outcome"] = outcome

        loader = threading.Thread(target=load_in_background)
        loader.start()
        time.sleep(0.05)
        timed_out, waiter_outcome = fresh_registry.get_expected_result_detailed("policy_bench", "1")

        assert timed_out is None
        assert waiter_outcome is LoadOutcome.PROVIDER_TIMEOUT

        release.set()
        loader.join(timeout=10)
        assert loader_outcome["outcome"] is LoadOutcome.HIT

        result, outcome = fresh_registry.get_expected_result_detailed("policy_bench", "1")
        assert result is not None
        assert outcome is LoadOutcome.HIT

    def test_cache_clearing_forces_reload(self, fresh_registry):
        calls = {"count": 0}

        def counting_provider(sf):
            calls["count"] += 1
            return _answer_set()

        fresh_registry.register_provider("policy_bench", counting_provider)

        fresh_registry.get_expected_result("policy_bench", "1", scale_factor=1.0)
        fresh_registry.clear_cache()
        result = fresh_registry.get_expected_result("policy_bench", "1", scale_factor=1.0)

        assert result is not None
        assert calls["count"] == 2


def _tpcds_answer_set(count=100):
    return BenchmarkExpectedResults(
        benchmark_name="tpcds",
        scale_factor=1.0,
        query_results={
            "1": ExpectedQueryResult(
                query_id="1",
                expected_row_count=count,
                validation_mode=ValidationMode.SKIP,
            )
        },
    )


@pytest.fixture
def policy_validator(fresh_registry):
    calls = {"count": 0}

    def stub_tpcds(sf):
        calls["count"] += 1
        return _tpcds_answer_set()

    fresh_registry.register_provider("tpcds", stub_tpcds)
    validator = QueryValidator()
    validator.registry = fresh_registry
    validator.provider_calls = calls
    return validator


class TestRunLocalPolicy:
    def test_production_connection_wrappers_carry_policy_into_worker_threads(self, policy_validator):
        from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection

        barrier = threading.Barrier(2)
        outcomes = {}

        class _Adapter:
            @staticmethod
            def get_target_dialect():
                return "duckdb"

            @staticmethod
            def execute_query(connection, query, query_id, **kwargs):
                barrier.wait(timeout=10)
                outcome = policy_validator.validate_query_result(
                    benchmark_type="tpcds",
                    query_id=query_id,
                    actual_row_count=140,
                    scale_factor=kwargs["scale_factor"],
                )
                outcomes[connection] = outcome
                return {"rows_returned": 140}

        wrappers = {
            "exact": PlatformAdapterConnection("exact", _Adapter(), validation_mode=ValidationMode.EXACT),
            "skip": PlatformAdapterConnection("skip", _Adapter(), validation_mode=ValidationMode.SKIP),
        }
        for wrapper in wrappers.values():
            wrapper.benchmark_type = "tpcds"
            wrapper.scale_factor = 1.0
            wrapper.set_query_context("1")

        threads = [threading.Thread(target=wrapper.execute, args=("select 1",)) for wrapper in wrappers.values()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert outcomes["exact"].validation_mode == ValidationMode.EXACT
        assert outcomes["exact"].is_valid is False
        assert outcomes["skip"].validation_mode == ValidationMode.SKIP
        assert outcomes["skip"].is_valid is True

    def test_wrapper_without_policy_preserves_power_run_thread_context(self, policy_validator):
        from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection

        outcomes = []

        class _Adapter:
            @staticmethod
            def get_target_dialect():
                return "duckdb"

            @staticmethod
            def execute_query(connection, query, query_id, **kwargs):
                outcomes.append(
                    policy_validator.validate_query_result(
                        benchmark_type="tpcds",
                        query_id=query_id,
                        actual_row_count=140,
                        scale_factor=kwargs["scale_factor"],
                    )
                )
                return {"rows_returned": 140}

        wrapper = PlatformAdapterConnection("power", _Adapter())
        wrapper.benchmark_type = "tpcds"
        wrapper.scale_factor = 1.0
        wrapper.set_query_context("1")
        set_validation_mode_context(ValidationMode.EXACT)
        try:
            wrapper.execute("select 1")
        finally:
            clear_validation_mode_context()

        assert outcomes[0].validation_mode == ValidationMode.EXACT
        assert outcomes[0].is_valid is False

    def test_concurrent_modes_cannot_contaminate_one_another(self, policy_validator):
        outcomes = {}

        def run_with_mode(name, mode, actual):
            set_validation_mode_context(mode)
            try:
                outcomes[name] = policy_validator.validate_query_result(
                    benchmark_type="tpcds",
                    query_id="1",
                    actual_row_count=actual,
                    scale_factor=1.0,
                )
            finally:
                clear_validation_mode_context()

        threads = [
            threading.Thread(target=run_with_mode, args=("exact", ValidationMode.EXACT, 100)),
            threading.Thread(target=run_with_mode, args=("loose", ValidationMode.LOOSE, 140)),
            threading.Thread(target=run_with_mode, args=("skip", ValidationMode.SKIP, 999)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert outcomes["exact"].validation_mode == ValidationMode.EXACT
        assert outcomes["exact"].is_valid
        assert outcomes["loose"].validation_mode == ValidationMode.LOOSE
        assert outcomes["loose"].is_valid
        assert outcomes["skip"].validation_mode == ValidationMode.SKIP
        assert outcomes["skip"].is_valid
        assert policy_validator.provider_calls["count"] == 1

    def test_exact_mode_still_fails_mismatches(self, policy_validator):
        set_validation_mode_context(ValidationMode.EXACT)
        try:
            result = policy_validator.validate_query_result(
                benchmark_type="tpcds",
                query_id="1",
                actual_row_count=160,
                scale_factor=1.0,
            )
        finally:
            clear_validation_mode_context()

        assert result.validation_mode == ValidationMode.EXACT
        assert not result.is_valid

    def test_disabled_run_config_maps_to_skip(self, policy_validator):
        import benchbox.core.expected_results.tpcds_results as tpcds_results

        tpcds_results.set_config_validation_mode("disabled")

        result = policy_validator.validate_query_result(
            benchmark_type="tpcds",
            query_id="1",
            actual_row_count=160,
            scale_factor=1.0,
        )

        assert result.validation_mode == ValidationMode.SKIP
        assert result.is_valid

    def test_sequential_runs_do_not_bake_policy_into_cache(self, policy_validator, fresh_registry):
        import benchbox.core.expected_results.tpcds_results as tpcds_results

        tpcds_results.set_config_validation_mode("exact")
        exact_result = policy_validator.validate_query_result(
            benchmark_type="tpcds", query_id="1", actual_row_count=100, scale_factor=1.0
        )
        tpcds_results.set_config_validation_mode("skip")
        skip_result = policy_validator.validate_query_result(
            benchmark_type="tpcds", query_id="1", actual_row_count=100, scale_factor=1.0
        )

        assert exact_result.validation_mode == ValidationMode.EXACT
        assert skip_result.validation_mode == ValidationMode.SKIP
        cached = fresh_registry._cache["tpcds"][1.0].query_results["1"]
        assert cached.validation_mode == ValidationMode.SKIP
        assert policy_validator.provider_calls["count"] == 1

    def test_cached_objects_are_never_mutated_by_validation(self, policy_validator, fresh_registry):
        set_validation_mode_context(ValidationMode.EXACT)
        try:
            policy_validator.validate_query_result(
                benchmark_type="tpcds", query_id="1", actual_row_count=100, scale_factor=1.0
            )
        finally:
            clear_validation_mode_context()

        cached = fresh_registry._cache["tpcds"][1.0].query_results["1"]
        assert cached.validation_mode == ValidationMode.SKIP

    def test_environment_fallback_still_applies_without_context(self, policy_validator, monkeypatch):
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "loose")

        result = policy_validator.validate_query_result(
            benchmark_type="tpcds", query_id="1", actual_row_count=140, scale_factor=1.0
        )

        assert result.validation_mode == ValidationMode.LOOSE
        assert result.is_valid

    def test_run_context_beats_environment(self, policy_validator, monkeypatch):
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "loose")
        set_validation_mode_context(ValidationMode.EXACT)
        try:
            result = policy_validator.validate_query_result(
                benchmark_type="tpcds", query_id="1", actual_row_count=140, scale_factor=1.0
            )
        finally:
            clear_validation_mode_context()

        assert result.validation_mode == ValidationMode.EXACT
        assert not result.is_valid

    def test_validation_mode_context_round_trip(self):
        assert get_validation_mode_context() is None
        set_validation_mode_context(ValidationMode.LOOSE)
        assert get_validation_mode_context() == ValidationMode.LOOSE
        set_validation_mode_context(None)
        assert get_validation_mode_context() is None


class TestOutcomeDistinction:
    def test_no_answer_set_is_unvalidated_not_a_failure(self, policy_validator):
        result = policy_validator.validate_query_result(
            benchmark_type="no_such_benchmark", query_id="1", actual_row_count=5, scale_factor=1.0
        )

        assert result.is_valid
        assert result.unvalidated
        assert result.validation_mode == ValidationMode.SKIP
        assert result.warning_message is not None

    def test_query_missing_from_an_answer_set_is_a_named_failure(self, policy_validator):
        result = policy_validator.validate_query_result(
            benchmark_type="tpcds", query_id="no-such-query", actual_row_count=5, scale_factor=1.0
        )

        assert not result.is_valid
        assert not result.unvalidated
        assert result.error_message is not None
        assert "lookup failed" in result.error_message

    def test_provider_failure_is_never_a_normal_skip(self, fresh_registry):
        def failing_provider(sf):
            raise ValueError("simulated provider failure")

        fresh_registry.register_provider("boom_bench", failing_provider)
        validator = QueryValidator()
        validator.registry = fresh_registry

        result = validator.validate_query_result(
            benchmark_type="boom_bench", query_id="1", actual_row_count=5, scale_factor=1.0
        )

        assert not result.is_valid
        assert result.error_message is not None
        assert "not treated as a validation skip" in result.error_message
        assert result.warning_message is None

    def test_provider_timeout_is_never_a_normal_skip(self, fresh_registry):
        release = threading.Event()

        def slow_provider(sf):
            assert release.wait(timeout=10)
            return _answer_set()

        fresh_registry.register_provider("slow_bench", slow_provider)
        fresh_registry.wait_timeout_seconds = 0.2
        validator = QueryValidator()
        validator.registry = fresh_registry

        def load_in_background():
            fresh_registry.get_expected_result_detailed("slow_bench", "1")

        loader = threading.Thread(target=load_in_background)
        loader.start()
        time.sleep(0.05)
        try:
            result = validator.validate_query_result(
                benchmark_type="slow_bench", query_id="1", actual_row_count=5, scale_factor=1.0
            )
        finally:
            release.set()
            loader.join(timeout=10)

        assert not result.is_valid
        assert result.error_message is not None
        assert "Timed out" in result.error_message

    def test_non_reference_stream_keeps_stream_skip(self, policy_validator):
        result = policy_validator.validate_query_result(
            benchmark_type="tpcds", query_id="1", actual_row_count=5, scale_factor=1.0, stream_id=2
        )

        assert result.is_valid
        assert result.validation_mode == ValidationMode.SKIP
        assert "stream 2" in result.warning_message

    def test_sf1_fallback_failure_propagates_provider_outcome(self, fresh_registry):

        def sf_gapped_provider(sf):
            if sf == 1.0:
                raise ConnectionError("simulated SF=1 fetch failure")
            return None

        fresh_registry.register_provider("gapped_bench", sf_gapped_provider)

        result, outcome = fresh_registry.get_expected_result_detailed("gapped_bench", "1", scale_factor=10.0)

        assert result is None
        assert outcome is LoadOutcome.PROVIDER_FAILED
