# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging

logger = logging.getLogger(__name__)

_providers_registered = False

from benchbox.core.expected_results import ssb_results, tpcds_results, tpch_results
from benchbox.core.expected_results.registry import get_registry


def has_row_count_reference(benchmark_name: str, scale_factor: float | None = None) -> bool:
    register_all_providers()
    return get_registry().has_row_count_reference(benchmark_name, scale_factor)


def register_all_providers():
    global _providers_registered

    registry = get_registry()
    benchmarks = registry.list_available_benchmarks()

    if not _providers_registered:
        if benchmarks:
            logger.debug(f"Expected results providers registered for: {', '.join(benchmarks)}")
        else:
            logger.warning(
                "No expected results providers registered. "
                "Validation will be skipped for all benchmarks. "
                "This may indicate a module import issue."
            )
        _providers_registered = True

    return benchmarks
