# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.equivalence import (
    DATAFUSION_KNOWN_DIVERGENCES,
    EQUIVALENCE_SCALE,
    _close_quietly,
    build_datafusion_with_tpch,
    find_datafusion_divergences,
)
from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.tpchavoc,
    pytest.mark.slow,
]


@pytest.fixture(scope="module")
def datafusion_divergences(tmp_path_factory):
    pytest.importorskip("datafusion", reason="DataFusion not installed")
    output_dir = tmp_path_factory.mktemp("tpchavoc_df_equivalence")
    connection, tpchavoc, tpch = build_datafusion_with_tpch(EQUIVALENCE_SCALE, output_dir)
    try:
        yield find_datafusion_divergences(connection, tpchavoc, tpch)
    finally:
        _close_quietly(connection)


def test_all_executable_variants_equivalent_on_datafusion(datafusion_divergences):
    unexpected = {d.key for d in datafusion_divergences} - set(DATAFUSION_KNOWN_DIVERGENCES)
    assert not unexpected, "Variant(s) diverge from canonical TPC-H on DataFusion: " + ", ".join(
        f"{d.key} ({d.detail})" for d in datafusion_divergences if d.key in unexpected
    )


def test_skipped_variants_are_excluded_never_marked_equivalent(datafusion_divergences):
    reported = {d.key for d in datafusion_divergences}
    assert reported.isdisjoint(set(DATAFUSION_TPCHAVOC_SKIPS)), (
        "Un-executable (skip-list) variants must be excluded, not evaluated"
    )


def test_datafusion_skip_list_wiring_and_lakesail_subset():
    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
    from benchbox.sql_compat.rules.execution_filter.lakesail_tpchavoc import LAKESAIL_TPCHAVOC_SKIPS

    benchmark = TPCHavocBenchmark(scale_factor=EQUIVALENCE_SCALE)
    assert set(benchmark.get_platform_skip_queries("datafusion")) == set(DATAFUSION_TPCHAVOC_SKIPS)
    assert benchmark.get_platform_skip_queries("duckdb") == []
    assert set(DATAFUSION_TPCHAVOC_SKIPS) <= set(LAKESAIL_TPCHAVOC_SKIPS)


