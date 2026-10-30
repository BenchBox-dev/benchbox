from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark

    return CoffeeShopBenchmark()


def test_tm1_questdb_casts_varchar_time_to_timestamp():
    q = _bench().get_queries(dialect="questdb")["TM1"]
    assert q.count("hour(CAST('1970-01-01T' || ol.order_time AS TIMESTAMP))") == 3, q
    assert "EXTRACT" not in q.upper(), q


def test_tm1_questdb_preserves_day_part_categories():
    q = _bench().get_queries(dialect="questdb")["TM1"]
    for part in ("Morning", "Midday", "Afternoon", "Evening"):
        assert f"'{part}'" in q, q


def test_tm1_base_dialect_keeps_extract():
    q = _bench().get_queries()["TM1"]
    assert "EXTRACT(HOUR FROM ol.order_time)" in q, q
