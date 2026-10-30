from __future__ import annotations

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _tables():
    from datetime import date

    import pandas as pd
    import pyarrow as pa

    order_lines = pd.DataFrame(
        {
            "order_id": ["o1", "o1", "o2", "o3"],
            "order_date": pd.array(
                [date(2023, 2, 1), date(2023, 2, 1), date(2023, 3, 5), date(2024, 1, 9)],
                dtype=pd.ArrowDtype(pa.date32()),
            ),
            "quantity": [2, 1, 4, 3],
            "total_price": [10.0, 5.0, 20.0, 25.0],
            "location_record_id": [1, 1, 2, 2],
        }
    )
    dim_locations = pd.DataFrame({"record_id": [1, 2], "region": ["east", "west"]})
    return {"order_lines": order_lines, "dim_locations": dim_locations}


@pytest.mark.parametrize("family", FAMILIES)
def test_order_line_averages_match_pandas(family):
    from benchbox.core.coffeeshop.dataframe_queries.queries import qc1_pandas_impl

    rows = result_rows(qc1_pandas_impl(build_context(family, _tables())))

    assert rows == [(4 / 3, 10 / 3)]


@pytest.mark.parametrize("family", FAMILIES)
def test_region_revenue_share_matches_pandas(family):
    from benchbox.core.coffeeshop.dataframe_queries.queries import sa4_pandas_impl

    rows = result_rows(sa4_pandas_impl(build_context(family, _tables())))

    assert rows == [("west", 2, 45.0, 0.75), ("east", 1, 15.0, 0.25)]
