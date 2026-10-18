# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _q_strip(df_id: str) -> str:

    assert df_id.startswith("Q"), f"DataFrame ID {df_id!r} does not use the Q-prefix convention"
    return df_id[1:]


def test_dataframe_ids_map_to_sql_ids():
    import benchbox.core.dataframe.benchmark_suite
    from benchbox.core.equivalence.builders.nyctaxi import NYCTAXI_SQL_TO_DF_IDS
    from benchbox.core.equivalence.builders.tsbs_devops import TSBS_DEVOPS_SQL_TO_DF_IDS
    from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark
    from benchbox.core.nyctaxi.dataframe_queries import NYCTAXI_DATAFRAME_QUERIES
    from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
    from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries
    from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
    from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
    from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES
    from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark
    from benchbox.core.tsbs_devops.dataframe_queries import TSBS_DEVOPS_DATAFRAME_QUERIES
    from benchbox.tpcds import TPCDS
    from benchbox.tpch import TPCH
    from benchbox.tpch_skew import TPCHSkew

    probe_dir = "/tmp/df-id-map-probe"

    nyctaxi_sql = set(NYCTaxiBenchmark(scale_factor=0.01, output_dir=probe_dir).get_queries().keys())
    nyctaxi_df = set(NYCTAXI_DATAFRAME_QUERIES.get_query_ids())
    assert set(NYCTAXI_SQL_TO_DF_IDS.keys()) == nyctaxi_sql, (
        f"nyctaxi map keys diverge from SQL IDs: missing={sorted(nyctaxi_sql - set(NYCTAXI_SQL_TO_DF_IDS))} "
        f"extra={sorted(set(NYCTAXI_SQL_TO_DF_IDS) - nyctaxi_sql)}"
    )
    assert set(NYCTAXI_SQL_TO_DF_IDS.values()) == nyctaxi_df, (
        f"nyctaxi map values diverge from DF IDs: missing={sorted(nyctaxi_df - set(NYCTAXI_SQL_TO_DF_IDS.values()))} "
        f"extra={sorted(set(NYCTAXI_SQL_TO_DF_IDS.values()) - nyctaxi_df)}"
    )

    tsbs_sql = set(TSBSDevOpsBenchmark(scale_factor=0.01, output_dir=probe_dir).get_queries().keys())
    tsbs_df = set(TSBS_DEVOPS_DATAFRAME_QUERIES.get_query_ids())
    assert set(TSBS_DEVOPS_SQL_TO_DF_IDS.keys()) == tsbs_sql, (
        f"tsbs_devops map keys diverge from SQL IDs: missing={sorted(tsbs_sql - set(TSBS_DEVOPS_SQL_TO_DF_IDS))} "
        f"extra={sorted(set(TSBS_DEVOPS_SQL_TO_DF_IDS) - tsbs_sql)}"
    )
    assert set(TSBS_DEVOPS_SQL_TO_DF_IDS.values()) == tsbs_df, (
        f"tsbs_devops map values diverge from DF IDs: missing={sorted(tsbs_df - set(TSBS_DEVOPS_SQL_TO_DF_IDS.values()))} "
        f"extra={sorted(set(TSBS_DEVOPS_SQL_TO_DF_IDS.values()) - tsbs_df)}"
    )

    for label, benchmark, registry in [
        ("tpch", TPCH(scale_factor=0.01, output_dir=probe_dir), TPCH_DATAFRAME_QUERIES),
        ("tpcds", TPCDS(scale_factor=0.01, output_dir=probe_dir), TPCDS_DATAFRAME_QUERIES),
        ("tpch_skew", TPCHSkew(scale_factor=0.01, output_dir=probe_dir), TPCH_SKEW_DATAFRAME_QUERIES),
    ]:
        sql_ids = set(benchmark.get_queries().keys())
        if label == "tpcds":
            assert sql_ids == {str(number) for number in range(1, 100)}
            variants = {"14b", "23b", "24b", "39b"}
            for variant in variants:
                number = int(variant[:-1])
                sql = benchmark.get_query(number, variant="b")
                assert sql and sql != benchmark.get_query(number, variant="a")
            sql_ids |= variants
        df_ids = set(registry.get_query_ids())
        assert {_q_strip(df_id) for df_id in df_ids} == sql_ids, (
            f"{label} Q-prefix strip diverges: "
            f"unmapped SQL={sorted(sql_ids - {_q_strip(d) for d in df_ids})} "
            f"unmapped DF={sorted(df_ids - {f'Q{s}' for s in sql_ids})}"
        )

    rp_benchmark = ReadPrimitivesBenchmark(scale_factor=0.05, output_dir=probe_dir)
    rp_sql_duckdb = set(rp_benchmark.get_queries(dialect="duckdb").keys())
    rp_df = set(get_dataframe_queries().get_query_ids())
    classified_df_only = {
        "fulltext_boolean_search",
        "fulltext_phrase_search",
        "fulltext_simple_search",
        "json_extract_simple",
    }
    assert classified_df_only <= rp_df, (
        f"classified exclusions missing from DF registry: {sorted(classified_df_only - rp_df)}"
    )
    assert rp_df - rp_sql_duckdb == classified_df_only, (
        f"read_primitives DF-only drift: {sorted(rp_df - rp_sql_duckdb - classified_df_only)}"
    )
    gateable = rp_df & rp_sql_duckdb
    assert len(gateable) == 148, f"read_primitives gateable count drifted: {len(gateable)} != 148"
