from __future__ import annotations

from functools import cache, lru_cache

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpchavoc import cloud_compat, dialect_compat
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.spark_query_transformer import (
    DUAL_VARIANT_IDS,
    GROUP_BY_EMPTY_VARIANT_IDS,
    SCALAR_GROUP_BY_VARIANT_IDS,
    SparkTPCHavocQueryTransformer,
)
from benchbox.core.tpchavoc.variants import VARIANT_REGISTRY
from benchbox.platforms.datafusion_query_transformer import DataFusionQueryTransformer
from benchbox.utils.dialect_utils import apply_rewrite, normalize_dialect_for_sqlglot
from tests.unit.platforms.test_datafusion_query_transformer import Q11_SQL, Q16_SQL, Q18_SQL, Q20_SQL
from tests.unit.sql_compat.golden_corpus import Case

CLOUD_DIALECTS = ("bigquery", "databricks", "snowflake")
PRESTO_DIALECTS = ("presto", "trino", "athena")
ARRAY_ORDER = ["l_orderkey", "l_linenumber"]
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


def _tpch_schema() -> dict[str, dict[str, str]]:
    from benchbox.core.tpch.schema import TABLES

    return {table.name: {column.name: column.get_sql_type() for column in table.columns} for table in TABLES}


def _fork(sql: str, dialect: str, *steps: tuple[str, dict]) -> str:
    resolved = "datafusion" if dialect == "datafusion" else normalize_dialect_for_sqlglot(dialect)
    tree = sqlglot.parse_one(sql, read=resolved)
    for name, args in steps:
        tree = apply_rewrite(name, tree, dialect, **args)
    return tree.sql(dialect=resolved)


def _bigquery_steps(query_id: str) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    if query_id == "1_v7":
        steps.append(("order_list_aggregates", {"order_by": ARRAY_ORDER}))
    if query_id in cloud_compat.BIGQUERY_FILTER_IDS:
        steps.append(("filtered_aggregates_to_conditional", {}))
    if query_id in cloud_compat._BIGQUERY_FROMLESS_IDS:
        steps.append(("add_row_source_to_fromless_selects", {"source": "UNNEST([1])"}))
    if query_id == BIGQUERY_ORDER_ID:
        steps.append(("strip_window_null_ordering", {"columns": ["l_orderkey"]}))
    if column := cloud_compat._BIGQUERY_HAVING_COLUMNS.get(query_id):
        steps.append(
            ("qualify_columns_by_name", {"tables": {column: "combined"}, "clauses": ["having"], "quoted": True})
        )
    return steps


def _databricks_steps(query_id: str) -> list[tuple[str, dict]]:
    if query_id == "1_v7":
        return [("order_list_aggregates", {"order_by": ARRAY_ORDER})]
    if query_id in SCALAR_GROUP_BY_VARIANT_IDS:
        return [("wrap_grouped_scalar_subqueries", {"aggregate": exp.First})]
    if query_id in DUAL_VARIANT_IDS:
        return [("name_literal_subquery_columns", {"column": "dual_col"})]
    return []


def _presto_steps(query_id: str) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    if query_id in dialect_compat.PRESTO_ALIAS_VARIANT_IDS:
        steps.append(("inline_select_aliases", {}))
    if query_id in dialect_compat.PRESTO_WINDOW_FILTER_VARIANT_IDS:
        steps.append(("filtered_aggregates_to_conditional", {"windows_only": True}))
    if query_id in dialect_compat.PRESTO_ARRAY_AGGREGATION_VARIANT_IDS:
        steps.append(("order_list_aggregates", {"order_by": ARRAY_ORDER}))
    return steps


def _postgres_steps(query_id: str) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    if query_id in dialect_compat.POSTGRES_ALIAS_VARIANT_IDS:
        steps.append(("inline_select_aliases", {}))
    if columns := dialect_compat.POSTGRES_QUALIFIED_COLUMNS.get(query_id):
        steps.append(("qualify_columns_by_name", {"tables": columns}))
    if query_id in dialect_compat.POSTGRES_DUAL_VARIANT_IDS:
        steps.append(("literal_subquery_to_values", {"alias": "dual", "column": "dual_col"}))
    return steps


def _fork_text(query_id: str, query: str, target: str) -> str:
    if target == "bigquery":
        steps = _bigquery_steps(query_id)
    elif target == "databricks":
        steps = _databricks_steps(query_id)
    elif target == "clickhouse":
        steps = (
            [("filtered_aggregates_to_conditional", {})]
            if query_id in dialect_compat.CLICKHOUSE_FILTER_VARIANT_IDS
            else []
        )
    elif target in PRESTO_DIALECTS:
        steps = _presto_steps(query_id)
    elif target == "postgres":
        steps = _postgres_steps(query_id)
    else:
        steps = []
    return _fork(query, target, *steps)


def _variant_ids(target: str) -> list[str]:
    if target == "bigquery":
        ids = set(cloud_compat.BIGQUERY_FILTER_IDS) | set(cloud_compat._BIGQUERY_FROMLESS_IDS)
        ids |= set(cloud_compat._BIGQUERY_HAVING_COLUMNS) | {BIGQUERY_ORDER_ID, "1_v7"}
    elif target == "databricks":
        ids = set(SparkTPCHavocQueryTransformer.known_variant_ids()) | {"1_v7"}
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
            | set(dialect_compat.PRESTO_ARRAY_AGGREGATION_VARIANT_IDS)
        )
    else:
        ids = set(POSTGRES_ID_SETS)
    return sorted(ids | {CONTROL_ID})


def _havoc_target_cases() -> list[Case]:
    cases = []
    for target in (*CLOUD_DIALECTS, "clickhouse", "datafusion", *PRESTO_DIALECTS, "postgres"):
        for query_id in _variant_ids(target):
            query = _translated(query_id, target)
            cases.append(
                Case(
                    f"havoc:{target}:{query_id}",
                    lambda q=query, i=query_id, t=target: dialect_compat.rewrite_dialect_variant(i, q, t),
                    lambda q=query, i=query_id, t=target: _fork_text(i, q, t),
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
                lambda q=query, i=query_id: SparkTPCHavocQueryTransformer().transform(q, query_id=i),
                lambda q=query, i=query_id: _fork_text(i, q, "databricks"),
            )
        )
    control = _variant_sql("2_v1")
    cases.append(
        Case(
            "spark_raw:2_v1",
            lambda: SparkTPCHavocQueryTransformer().transform(control, query_id="2_v1"),
            lambda: _fork_text("2_v1", control, "databricks"),
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
                lambda s=sql: dialect_compat._rewrite_clickhouse_filters(s),
                lambda s=sql: _fork(s, "clickhouse", ("filtered_aggregates_to_conditional", {})),
            )
        )
    for dialect in PRESTO_DIALECTS:
        for index, sql in enumerate(WINDOW_FILTER_REJECTED):
            cases.append(
                Case(
                    f"havoc_helper:window_filter:{dialect}:{index}",
                    lambda s=sql, d=dialect: dialect_compat._rewrite_window_filters(s, d),
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
                lambda c=candidate: cloud_compat.rewrite_cloud_variant("1_v6", c, "bigquery"),
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


def _datafusion_fork(query_id: str, sql: str) -> str:
    schema = _tpch_schema()
    if query_id in {"11", "20"}:
        return _fork(sql, "datafusion", ("decorrelate_subqueries", {"schema": schema}))
    if query_id == "16":
        return _fork(
            sql, "datafusion", ("not_in_subquery_to_not_exists", {"non_null_columns": ["ps_suppkey", "s_suppkey"]})
        )
    if query_id == "18":
        return _fork(sql, "datafusion", ("in_aggregate_subquery_to_exists", {"alias": "t"}))
    return sql


def _datafusion_cases() -> list[Case]:
    cases = []
    for label, (query_id, sql) in _datafusion_inputs().items():
        cases.append(
            Case(
                f"datafusion:{label}",
                lambda s=sql, i=query_id: DataFusionQueryTransformer().transform(s, query_id=i),
                lambda s=sql, i=query_id: _datafusion_fork(i, s),
            )
        )
    return cases


def cases() -> list[Case]:
    return [*_havoc_target_cases(), *_spark_raw_cases(), *_helper_cases(), *_datafusion_cases()]
