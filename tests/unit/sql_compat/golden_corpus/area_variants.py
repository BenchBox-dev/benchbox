from __future__ import annotations

from functools import cache, lru_cache

from benchbox._vendor import sqlglot
from benchbox.core.tpchavoc import cloud_compat, dialect_compat
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dialect_compat import (
    DUAL_VARIANT_IDS,
    GROUP_BY_EMPTY_VARIANT_IDS,
    SCALAR_GROUP_BY_VARIANT_IDS,
    rewrite_spark_variant,
)
from benchbox.core.tpchavoc.variants import VARIANT_REGISTRY
from benchbox.platforms.datafusion_tpch_rewrites import rewrite_datafusion_tpch_query
from benchbox.utils.dialect_utils import apply_rewrite, resolve_sql_dialect
from tests.unit.sql_compat.golden_corpus import Case, frozen_legacy

AREA = "area_variants"

Q11_SQL = """\
select
ps_partkey,
sum(ps_supplycost * ps_availqty) as value
from
partsupp,
supplier,
nation
where
ps_suppkey = s_suppkey
and s_nationkey = n_nationkey
and n_name = 'GERMANY'
group by
ps_partkey having
sum(ps_supplycost * ps_availqty) > (
select
sum(ps_supplycost * ps_availqty) * 0.0001000000
from
partsupp,
supplier,
nation
where
ps_suppkey = s_suppkey
and s_nationkey = n_nationkey
and n_name = 'GERMANY'
)
order by
value desc;"""

Q16_SQL = """\
select
p_brand,
p_type,
p_size,
count(distinct ps_suppkey) as supplier_cnt
from
partsupp,
part
where
p_partkey = ps_partkey
and p_brand <> 'Brand#45'
and p_type not like 'MEDIUM POLISHED%'
and p_size in (49, 14, 23, 45, 19, 3, 36, 9)
and ps_suppkey not in (
select
s_suppkey
from
supplier
where
s_comment like '%Customer%Complaints%'
)
group by
p_brand,
p_type,
p_size
order by
supplier_cnt desc,
p_brand,
p_type,
p_size;"""

Q18_SQL = """\
select
c_name,
c_custkey,
o_orderkey,
o_orderdate,
o_totalprice,
sum(l_quantity)
from
customer,
orders,
lineitem
where
o_orderkey in (
select
l_orderkey
from
lineitem
group by
l_orderkey having
sum(l_quantity) > 300
)
and c_custkey = o_custkey
and o_orderkey = l_orderkey
group by
c_name,
c_custkey,
o_orderkey,
o_orderdate,
o_totalprice
order by
o_totalprice desc,
o_orderdate
LIMIT 100;"""

Q20_SQL = """\
select
s_name,
s_address
from
supplier,
nation
where
s_suppkey in (
select
ps_suppkey
from
partsupp
where
ps_partkey in (
select
p_partkey
from
part
where
p_name like 'forest%'
)
and ps_availqty > (
select
0.5 * sum(l_quantity)
from
lineitem
where
l_partkey = ps_partkey
and l_suppkey = ps_suppkey
and l_shipdate >= date '1994-01-01'
and l_shipdate < date '1994-01-01' + interval '1' year
)
)
and s_nationkey = n_nationkey
and n_name = 'CANADA'
order by
s_name;"""
CLOUD_DIALECTS = ("bigquery", "databricks", "snowflake")
PRESTO_DIALECTS = ("presto", "trino", "athena")
POSTGRES_ID_SETS = (
    dialect_compat.POSTGRES_ALIAS_VARIANT_IDS
    | set(dialect_compat.POSTGRES_QUALIFIED_COLUMNS)
    | dialect_compat.POSTGRES_DUAL_VARIANT_IDS
)
CONTROL_ID = "2_v1"
BIGQUERY_ORDER_ID = "3_v9"


@lru_cache(maxsize=1)
def _benchmark() -> TPCHavocBenchmark:
    return TPCHavocBenchmark(scale_factor=0.1)


@cache
def _translated(query_id: str, target: str) -> str:
    benchmark = _benchmark()
    return benchmark.translate_query_text(benchmark.get_query(query_id), "netezza", target)


def _variant_sql(variant_id: str) -> str:
    query, _, variant = variant_id.partition("_v")
    return VARIANT_REGISTRY[int(query)][int(variant)].generate("base_query")


def _fork(sql: str, dialect: str, *steps: tuple[str, dict]) -> str:
    resolved = resolve_sql_dialect(dialect)
    tree = sqlglot.parse_one(sql, read=resolved)
    for name, args in steps:
        tree = apply_rewrite(name, tree, dialect, **args)
    return tree.sql(dialect=resolved)


def _variant_ids(target: str) -> list[str]:
    if target == "bigquery":
        ids = set(cloud_compat.BIGQUERY_FILTER_IDS) | set(cloud_compat._BIGQUERY_FROMLESS_IDS)
        ids |= set(cloud_compat._BIGQUERY_HAVING_COLUMNS) | {BIGQUERY_ORDER_ID, "1_v7"}
    elif target == "databricks":
        ids = set(dialect_compat.SPARK_VARIANT_IDS) | {"1_v7"}
    elif target == "snowflake":
        ids = set(cloud_compat._SNOWFLAKE_EMPTY_GROUP_IDS) | {"1_v7"}
    elif target == "clickhouse":
        ids = set(dialect_compat.CLICKHOUSE_FILTER_VARIANT_IDS)
    elif target == "datafusion":
        ids = set(dialect_compat.DATAFUSION_EMPTY_GROUP_VARIANT_IDS)
    elif target in PRESTO_DIALECTS:
        ids = (
            set(dialect_compat.PRESTO_ALIAS_VARIANT_IDS)
            | set(dialect_compat.PRESTO_WINDOW_FILTER_VARIANT_IDS)
            | set(dialect_compat.LIST_AGGREGATE_VARIANT_IDS)
        )
    else:
        ids = set(POSTGRES_ID_SETS)
    return sorted(ids | {CONTROL_ID})


def _havoc_output(query_id: str, translated: str, target: str) -> str:
    rewritten = dialect_compat.translate_list_aggregate_variant(query_id, _benchmark().get_query(query_id), target)
    return rewritten if rewritten is not None else dialect_compat.rewrite_dialect_variant(query_id, translated, target)


def _havoc_target_cases() -> list[Case]:
    cases = []
    for target in (*CLOUD_DIALECTS, "clickhouse", "datafusion", *PRESTO_DIALECTS, "postgres"):
        for query_id in _variant_ids(target):
            query = _translated(query_id, target)
            cases.append(
                Case(
                    f"havoc:{target}:{query_id}",
                    frozen_legacy(AREA, f"havoc:{target}:{query_id}"),
                    lambda q=query, i=query_id, t=target: _havoc_output(i, q, t),
                )
            )
    return cases


def _spark_raw_cases() -> list[Case]:
    cases = []
    ids = sorted(SCALAR_GROUP_BY_VARIANT_IDS | GROUP_BY_EMPTY_VARIANT_IDS | DUAL_VARIANT_IDS)
    for query_id in ids:
        query = _variant_sql(query_id)
        cases.append(
            Case(
                f"spark_raw:{query_id}",
                frozen_legacy(AREA, f"spark_raw:{query_id}"),
                lambda q=query, i=query_id: rewrite_spark_variant(i, q),
            )
        )
    control = _variant_sql("2_v1")
    cases.append(
        Case(
            "spark_raw:2_v1",
            frozen_legacy(AREA, "spark_raw:2_v1"),
            lambda: rewrite_spark_variant("2_v1", control),
        )
    )
    return cases


CLICKHOUSE_UNHANDLED = ("COUNT(l_orderkey)", "MIN(l_orderkey)", "MAX(l_orderkey)")
WINDOW_FILTER_REJECTED = (
    "SELECT COUNT(*) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
    "SELECT SUM(DISTINCT y) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
)
FILTER_SEMANTICS_SOURCE = """
    SELECT SUM(x) FILTER (WHERE keep), AVG(x) FILTER (WHERE keep),
           MIN(x) FILTER (WHERE keep), COUNT(*) FILTER (WHERE keep),
           COUNT(x) FILTER (WHERE keep), COUNT(DISTINCT x) FILTER (WHERE keep)
    FROM (
        SELECT 1 AS x, TRUE AS keep
        UNION ALL SELECT 1, TRUE
        UNION ALL SELECT NULL, TRUE
        UNION ALL SELECT 2, FALSE
        UNION ALL SELECT 3, NULL
    ) AS t
"""


def _helper_cases() -> list[Case]:
    cases = []
    for aggregate in CLICKHOUSE_UNHANDLED:
        sql = f"SELECT {aggregate} FILTER (WHERE l_orderkey > 0) FROM lineitem"
        cases.append(
            Case(
                f"havoc_helper:clickhouse_filter:{aggregate}",
                frozen_legacy(AREA, f"havoc_helper:clickhouse_filter:{aggregate}"),
                lambda s=sql: _fork(s, "clickhouse", ("filtered_aggregates_to_conditional", {})),
            )
        )
    for dialect in PRESTO_DIALECTS:
        for index, sql in enumerate(WINDOW_FILTER_REJECTED):
            cases.append(
                Case(
                    f"havoc_helper:window_filter:{dialect}:{index}",
                    frozen_legacy(AREA, f"havoc_helper:window_filter:{dialect}:{index}"),
                    lambda s=sql, d=dialect: _fork(
                        s, d, ("filtered_aggregates_to_conditional", {"windows_only": True})
                    ),
                )
            )
    bigquery_source = sqlglot.transpile(FILTER_SEMANTICS_SOURCE, read="duckdb", write="bigquery")[0]
    for label, candidate in (("duckdb_text", FILTER_SEMANTICS_SOURCE), ("bigquery_text", bigquery_source)):
        cases.append(
            Case(
                f"cloud_helper:filter_semantics:{label}",
                frozen_legacy(AREA, f"cloud_helper:filter_semantics:{label}"),
                lambda c=candidate: _fork(c, "bigquery", ("filtered_aggregates_to_conditional", {})),
            )
        )
    return cases


def _datafusion_inputs() -> dict[str, tuple[str, str]]:
    cast_q20 = Q20_SQL.replace("date '1994-01-01'", "CAST('1994-01-01' AS DATE)")
    inputs = {
        "11": ("11", Q11_SQL),
        "16": ("16", Q16_SQL),
        "18": ("18", Q18_SQL),
        "20": ("20", Q20_SQL),
        "20_cast_date": ("20", cast_q20),
        "11_nomatch": ("11", "SELECT 1;"),
        "16_nomatch": ("16", "SELECT 1;"),
        "18_nomatch": ("18", "SELECT 1;"),
        "20_nomatch": ("20", "SELECT 1;"),
        "5_passthrough": ("5", "SELECT * FROM lineitem WHERE l_orderkey = 5;"),
    }
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    tpch = TPCHBenchmark(scale_factor=0.1)
    for number in (11, 16, 18, 20):
        inputs[f"{number}_shipped"] = (str(number), tpch.get_query(number, dialect="datafusion"))
    return inputs


def _datafusion_cases() -> list[Case]:
    cases = []
    for label, (query_id, sql) in _datafusion_inputs().items():
        cases.append(
            Case(
                f"datafusion:{label}",
                frozen_legacy(AREA, f"datafusion:{label}"),
                lambda s=sql, i=query_id: rewrite_datafusion_tpch_query(s, i),
            )
        )
    return cases


def cases() -> list[Case]:
    return [*_havoc_target_cases(), *_spark_raw_cases(), *_helper_cases(), *_datafusion_cases()]
