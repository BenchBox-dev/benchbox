import pytest

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_union_variant_averages_guard_the_zero_count_branch():
    sql = TPCHavocBenchmark(scale_factor=0.01).get_query_variant(1, 4)

    for numerator in ("sum_qty", "sum_base_price", "sum_disc"):
        assert f"final.{numerator} / nullif(final.count_order, 0)" in sql
    assert "/ final.count_order" not in sql
