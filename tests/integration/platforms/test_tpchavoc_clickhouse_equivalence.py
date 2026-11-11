# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.equivalence import (
    CLICKHOUSE_KNOWN_DIVERGENCES,
    EQUIVALENCE_SCALE,
    _close_quietly,
    build_clickhouse_with_tpch,
    find_clickhouse_divergences,
)
from benchbox.sql_compat.rules.execution_filter.clickhouse_tpchavoc import CLICKHOUSE_TPCHAVOC_SKIPS
from tests.utilities.optional_engines import chdb_skip_reason

pytestmark = [
    pytest.mark.integration,
    pytest.mark.tpchavoc,
    pytest.mark.slow,
]


@pytest.fixture(scope="module")
def clickhouse_divergences(tmp_path_factory):
    reason = chdb_skip_reason()
    if reason is not None:
        pytest.skip(f"chDB (clickhouse-local) unavailable: {reason}")
    output_dir = tmp_path_factory.mktemp("tpchavoc_ch_equivalence")
    connection, tpchavoc, tpch = build_clickhouse_with_tpch(EQUIVALENCE_SCALE, output_dir)
    try:
        yield find_clickhouse_divergences(connection, tpchavoc, tpch)
    finally:
        _close_quietly(connection)


def test_all_executable_variants_match_classified_baseline(clickhouse_divergences):
    unexpected = {d.key for d in clickhouse_divergences} - set(CLICKHOUSE_KNOWN_DIVERGENCES)
    assert not unexpected, "Unclassified variant divergence(s) from canonical TPC-H on ClickHouse: " + ", ".join(
        f"{d.key} ({d.detail})" for d in clickhouse_divergences if d.key in unexpected
    )


def test_classified_divergences_still_diverge(clickhouse_divergences):
    observed = {d.key for d in clickhouse_divergences}
    stale = sorted(set(CLICKHOUSE_KNOWN_DIVERGENCES) - observed)
    assert not stale, f"CLICKHOUSE_KNOWN_DIVERGENCES entries no longer diverge - remove them: {stale}"


def test_skipped_variants_are_excluded_never_marked_equivalent(clickhouse_divergences):
    reported = {d.key for d in clickhouse_divergences}
    assert reported.isdisjoint(set(CLICKHOUSE_TPCHAVOC_SKIPS)), (
        "Un-executable (skip-list) variants must be excluded, not evaluated"
    )


def test_clickhouse_skip_list_wiring():
    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    benchmark = TPCHavocBenchmark(scale_factor=EQUIVALENCE_SCALE)
    selectors = ("clickhouse-local", "clickhouse-server", "clickhouse-cloud")
    first_class_display = ("ClickHouse Local", "ClickHouse Server", "ClickHouse Cloud")
    base_display = ("ClickHouse (Local)", "ClickHouse (Server)", "ClickHouse (Cloud)")
    for platform in (*selectors, *first_class_display, *base_display):
        assert set(benchmark.get_platform_skip_queries(platform)) == set(CLICKHOUSE_TPCHAVOC_SKIPS), platform
    assert benchmark.get_platform_skip_queries("duckdb") == []


