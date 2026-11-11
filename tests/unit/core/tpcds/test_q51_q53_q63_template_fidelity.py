from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]

_SPECS = Path(__file__).parents[4] / "benchbox/core/tpcds/dataframe_queries/helper_query_specs.yaml"


@pytest.mark.parametrize("query_id", [53, 63])
def test_brand_names_keep_their_hash_suffix(query_id):
    specs = {spec["query_id"]: spec for spec in yaml.safe_load(_SPECS.read_text())["helper_queries"]}
    brands = [value for arg in specs[query_id]["args"] if isinstance(arg, list) for value in arg if "#" in str(value)]
    assert sorted(brands) == sorted(
        [
            "scholaramalgamalg #14",
            "scholaramalgamalg #7",
            "exportiunivamalg #9",
            "scholaramalgamalg #9",
            "amalgimporto #1",
            "edu packscholar #1",
            "exportiimporto #1",
            "importoamalg #1",
        ]
    )


def test_q51_running_total_repeats_the_total_across_a_null_day():
    from benchbox.core.tpcds.dataframe_queries.queries import _q51_running_total_pandas

    base = pd.DataFrame(
        {
            "item": [1, 1, 1, 1, 2, 2],
            "daily_sales": [None, 5.0, None, 2.0, None, None],
        }
    )
    totals = _q51_running_total_pandas(base, "item")
    assert totals.isna().tolist() == [True, False, False, False, True, True]
    assert totals.dropna().tolist() == [5.0, 5.0, 7.0]


