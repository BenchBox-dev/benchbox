# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


EXPECTED_MAPPING = {
    "Q1": "single-host-12-hr",
    "Q2": "single-host-1-hr",
    "Q3": "cpu-max-all-1-hr",
    "Q4": "cpu-max-all-8-hr",
    "Q5": "double-groupby-1-hr",
    "Q6": "double-groupby-5-min",
    "Q7": "high-cpu-1-hr",
    "Q8": "high-cpu-12-hr",
    "Q9": "mem-by-host-1-hr",
    "Q10": "low-memory-hosts",
    "Q11": "disk-iops-1-hr",
    "Q12": "disk-latency",
    "Q13": "net-throughput-1-hr",
    "Q14": "net-errors",
    "Q15": "resource-utilization",
    "Q16": "lastpoint",
    "Q17": "by-region",
    "Q18": "by-service",
}


def test_sql_numeric_ids_bridge_to_dataframe_ids() -> None:

    from benchbox.core.benchmark_loader import get_core_benchmark_class
    from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

    bench = get_core_benchmark_class("tsbs_devops")(scale_factor=1.0)
    sql_ids = {str(q) for q in bench.get_queries()}
    assert len(sql_ids) == 18

    bridged = {f"Q{bench.get_query_info(slug)['id']}" for slug in sql_ids}
    df_ids = {str(q) for q in TSBS_DEVOPS_DATAFRAME_QUERIES.get_query_ids()}
    assert bridged == df_ids == set(EXPECTED_MAPPING)


def test_mapping_table_is_exact_and_total() -> None:

    from benchbox.core.benchmark_loader import get_core_benchmark_class
    from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES

    bench = get_core_benchmark_class("tsbs_devops")(scale_factor=1.0)
    sql_ids = {str(q) for q in bench.get_queries()}
    df_ids = {str(q) for q in TSBS_DEVOPS_DATAFRAME_QUERIES.get_query_ids()}
    assert set(EXPECTED_MAPPING) == df_ids
    assert sorted(EXPECTED_MAPPING.values()) == sorted(sql_ids)
    for df_id, slug in EXPECTED_MAPPING.items():
        assert bench.get_query_info(slug)["id"] == df_id[1:]


