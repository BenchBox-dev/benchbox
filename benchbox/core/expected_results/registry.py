# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum
from typing import Dict

from benchbox.core.expected_results.models import BenchmarkExpectedResults, ExpectedQueryResult

logger = logging.getLogger(__name__)


class LoadOutcome(str, Enum):
    HIT = "hit"
    NO_ANSWER_SET = "no_answer_set"
    STREAM_NOT_REFERENCE_VALIDATED = "stream_not_reference_validated"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    QUERY_NOT_FOUND = "query_not_found"


class ExpectedResultsRegistry:
    def __init__(self):
        self._cache: Dict[str, Dict[float, BenchmarkExpectedResults]] = {}
        self._providers: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._loading: Dict[str, Dict[float, dict]] = {}
        self._logged_stream_skips: set = set()
        self.wait_timeout_seconds = 30.0

    def register_provider(self, benchmark_name: str, provider: Callable) -> None:
        benchmark_key = benchmark_name.lower()
        with self._lock:
            self._providers[benchmark_key] = provider
            logger.debug(f"Registered expected results provider for benchmark: {benchmark_name}")

    def get_expected_result(
        self,
        benchmark_name: str,
        query_id: str,
        scale_factor: float | None = None,
        stream_id: int | None = None,
    ) -> ExpectedQueryResult | None:
        expected_result, _ = self.get_expected_result_detailed(benchmark_name, query_id, scale_factor, stream_id)
        return expected_result

    def get_expected_result_detailed(
        self,
        benchmark_name: str,
        query_id: str,
        scale_factor: float | None = None,
        stream_id: int | None = None,
    ) -> tuple[ExpectedQueryResult | None, LoadOutcome]:
        benchmark_key = benchmark_name.lower()
        sf = scale_factor or 1.0

        if stream_id is not None and stream_id > 0 and benchmark_key in ("tpch", "tpcds"):
            skip_key = (benchmark_key, stream_id)
            if skip_key not in self._logged_stream_skips:
                self._logged_stream_skips.add(skip_key)
                logger.debug(
                    f"Stream {stream_id} requested for {benchmark_name}. "
                    f"Answer files only available for stream 0. Validation will be skipped for this stream."
                )
            return None, LoadOutcome.STREAM_NOT_REFERENCE_VALIDATED

        benchmark_results, outcome = self._load_benchmark_results(benchmark_key, sf)

        if outcome in (LoadOutcome.PROVIDER_FAILED, LoadOutcome.PROVIDER_TIMEOUT):
            return None, outcome

        if benchmark_results is None and sf != 1.0:
            logger.debug(
                f"No expected results at SF={sf} for benchmark '{benchmark_name}'. "
                f"Trying SF=1.0 for scale-independent queries..."
            )
            benchmark_results_sf1, outcome_sf1 = self._load_benchmark_results(benchmark_key, 1.0)

            if benchmark_results_sf1 is not None:
                expected_result_sf1 = benchmark_results_sf1.get_expected_result(query_id)
                if expected_result_sf1 and expected_result_sf1.scale_independent:
                    logger.debug(f"Query '{query_id}' is scale-independent. Using SF=1.0 expectation at SF={sf}.")
                    return expected_result_sf1, outcome_sf1

            if outcome_sf1 in (LoadOutcome.PROVIDER_FAILED, LoadOutcome.PROVIDER_TIMEOUT):
                return None, outcome_sf1

            logger.debug(
                f"No expected results found for benchmark '{benchmark_name}' at scale factor {sf}. "
                f"Validation will be skipped for this query."
            )
            return None, LoadOutcome.NO_ANSWER_SET

        if benchmark_results is None:
            logger.debug(
                f"No expected results found for benchmark '{benchmark_name}' at scale factor {sf}. "
                f"Validation will be skipped for this query."
            )
            return None, LoadOutcome.NO_ANSWER_SET

        expected_result = benchmark_results.get_expected_result(query_id)
        if expected_result is None:
            if benchmark_results.known_query_ids is not None and query_id in benchmark_results.known_query_ids:
                logger.debug(
                    f"No expected result recorded for query '{query_id}' in benchmark '{benchmark_name}' "
                    f"at scale factor {sf}."
                )
                return None, LoadOutcome.NO_ANSWER_SET
            logger.debug(
                f"Query '{query_id}' is not in the reference answers of benchmark '{benchmark_name}' "
                f"at scale factor {sf}."
            )
            return None, LoadOutcome.QUERY_NOT_FOUND

        return expected_result, LoadOutcome.HIT

    def _load_benchmark_results(
        self, benchmark_key: str, scale_factor: float
    ) -> tuple[BenchmarkExpectedResults | None, LoadOutcome]:
        should_load = False

        with self._lock:
            if benchmark_key in self._cache and scale_factor in self._cache[benchmark_key]:
                return self._cache[benchmark_key][scale_factor], LoadOutcome.HIT

            if benchmark_key not in self._providers:
                logger.debug(f"No expected results provider registered for benchmark: {benchmark_key}")
                return None, LoadOutcome.NO_ANSWER_SET

            slot = self._loading.get(benchmark_key, {}).get(scale_factor)
            if slot is not None and slot["outcome"] is not None:
                if benchmark_key in self._cache and scale_factor in self._cache[benchmark_key]:
                    return self._cache[benchmark_key][scale_factor], slot["outcome"]
                del self._loading[benchmark_key][scale_factor]
                if not self._loading[benchmark_key]:
                    del self._loading[benchmark_key]
                slot = None

            if slot is None:
                slot = {"event": threading.Event(), "outcome": None, "result": None}
                if benchmark_key not in self._loading:
                    self._loading[benchmark_key] = {}
                self._loading[benchmark_key][scale_factor] = slot
                should_load = True

            provider = self._providers[benchmark_key]
            event = slot["event"]

        if not should_load:
            signaled = event.wait(timeout=self.wait_timeout_seconds)
            with self._lock:
                if slot["outcome"] is not None:
                    return slot["result"], slot["outcome"]
                if not signaled:
                    logger.debug(
                        f"Timed out waiting for {benchmark_key} SF={scale_factor} load; "
                        f"the load continues and later callers observe its outcome"
                    )
                    return None, LoadOutcome.PROVIDER_TIMEOUT
                logger.debug(f"Load signal without published outcome for {benchmark_key} SF={scale_factor}")
                return None, LoadOutcome.PROVIDER_FAILED

        permanent_absence = False
        transient_error: Exception | None = None
        try:
            results = provider(scale_factor)
        except FileNotFoundError as e:
            logger.info(
                f"Expected results not available for benchmark '{benchmark_key}' "
                f"at scale factor {scale_factor}: {e}. Validation will be skipped."
            )
            results = None
            permanent_absence = True
        except Exception as e:
            logger.warning(
                f"Failed to load expected results for benchmark '{benchmark_key}' "
                f"at scale factor {scale_factor}: {e}. Will not cache this failure; retry allowed."
            )
            results = None
            transient_error = e

        with self._lock:
            if permanent_absence:
                outcome = LoadOutcome.NO_ANSWER_SET
                if benchmark_key not in self._cache:
                    self._cache[benchmark_key] = {}
                self._cache[benchmark_key][scale_factor] = None
                published = None
            elif transient_error is not None:
                outcome = LoadOutcome.PROVIDER_FAILED
                published = None
            else:
                outcome = LoadOutcome.HIT
                if benchmark_key not in self._cache:
                    self._cache[benchmark_key] = {}
                self._cache[benchmark_key][scale_factor] = results
                published = results
                logger.debug(
                    f"Loaded and cached expected results for benchmark '{benchmark_key}' at scale factor {scale_factor}"
                )
            slot["result"] = published
            slot["outcome"] = outcome
            slot["event"].set()

        return published, outcome

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            for benchmark_key in list(self._loading):
                for scale_factor in list(self._loading[benchmark_key]):
                    if self._loading[benchmark_key][scale_factor]["outcome"] is not None:
                        del self._loading[benchmark_key][scale_factor]
                if benchmark_key in self._loading and not self._loading[benchmark_key]:
                    del self._loading[benchmark_key]
            self._logged_stream_skips.clear()
            logger.debug("Cleared expected results cache")

    def list_available_benchmarks(self) -> list[str]:
        with self._lock:
            return list(self._providers.keys())


_global_registry = ExpectedResultsRegistry()


def get_registry() -> ExpectedResultsRegistry:
    return _global_registry


def register_benchmark_provider(benchmark_name: str, provider: Callable) -> None:
    _global_registry.register_provider(benchmark_name, provider)
