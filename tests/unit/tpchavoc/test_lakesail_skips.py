from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.sql_compat.rules.execution_filter.lakesail_tpchavoc import LAKESAIL_TPCHAVOC_SKIPS

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_lakesail_skips_select_list_correlated_scalar_subquery_variant():
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    skipped = benchmark.get_platform_skip_queries("lakesail")

    assert "1_v1" in skipped
    assert set(skipped) == set(LAKESAIL_TPCHAVOC_SKIPS)
    assert "1_v1" in benchmark.get_queries(dialect="spark")
