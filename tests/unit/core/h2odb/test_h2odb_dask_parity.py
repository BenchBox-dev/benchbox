from __future__ import annotations

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.mark.parametrize("family", FAMILIES)
def test_fare_quantiles_per_passenger_count_match_pandas(family):
    import pandas as pd

    from benchbox.core.h2odb.dataframe_queries.queries import q9_pandas_impl

    trips = pd.DataFrame(
        {
            "passenger_count": [2, 1, 1, 1, 2, 1, 2, 2, 1, 1],
            "fare_amount": [10.0, 1.0, 2.0, 3.0, 20.0, 4.0, 30.0, None, 5.0, 6.0],
        }
    )

    rows = result_rows(q9_pandas_impl(build_context(family, {"trips": trips}, npartitions=3)))

    assert rows == [(1, 3.5, 5.5), (2, 20.0, 28.0)]
