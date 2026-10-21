from __future__ import annotations

import importlib
import re
from functools import cache, lru_cache

from benchbox.utils.dialect_utils import NO_IDENTIFY_DIALECTS, apply_rewrite

from . import Case

LOWERCASE_PATTERNS = [
    r"SR_RETURN_AMT",
    r"SR_CUSTOMER_SK",
    r"SR_STORE_SK",
    r"SR_RETURNED_DATE_SK",
    r"[A-Z][A-Z_]+_SK",
    r"[A-Z][A-Z_]+_AMT",
    r"[A-Z][A-Z_]+_QTY",
    r"[A-Z][A-Z_]+_FEE",
    r"[A-Z][A-Z_]+_CASH",
    r"[A-Z][A-Z_]+_CREDIT",
    r"[A-Z][A-Z_]+_TAX",
    r"[A-Z][A-Z_]+_CHARGE",
    r"D_YEAR",
    r"D_MOY",
    r"D_DATE_SK",
]

BASE_SETTINGS = {"joined_subquery_requires_alias": 0}

TPCH_IDS = tuple(range(1, 23))
TPCDS_CLICKHOUSE_IDS = (1, 10, 35, 47, 57, 66, 90)
STARROCKS_ALIAS_ROUTE_ARGS = {"prefix": "_sq", "quoted": True}


def _sqlglot():
    return importlib.import_module("benchbox._vendor.sqlglot")


def _transformer():
    from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

    return ClickHouseQueryTransformer()


@cache
def _tpch_query(query_id: int, dialect: str | None) -> str:
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    return TPCHBenchmark().get_query(query_id, seed=1, dialect=dialect)


@lru_cache(maxsize=1)
def _tpcds():
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

    return TPCDSBenchmark()


@cache
def _tpcds_query(query_id: int, dialect: str | None, seed: int = 1) -> str:
    return _tpcds().get_query(query_id, seed=seed, dialect=dialect)


@cache
def _tpcds_raw_netezza(query_id: int, seed: int = 1) -> str:
    bench = _tpcds()
    return bench._generate_tpcds_query(query_id, None, seed, bench.data_generator.scale_factor, "netezza")


@cache
def _tpcds_clickhouse_translated(query_id: int, seed: int = 1) -> str:
    return _tpcds().translate_query_text(_tpcds_raw_netezza(query_id, seed), "netezza", "clickhouse")


def _fork_clickhouse_pipeline(sql: str, additional: dict[str, str | int] | None = None) -> str:
    expression = _sqlglot().parse_one(sql, read="clickhouse")
    expression = apply_rewrite("lowercase_identifiers", expression, "clickhouse", patterns=LOWERCASE_PATTERNS)
    expression = apply_rewrite("decimal_zero_literals", expression, "clickhouse", precision=15, scale=2)
    expression = apply_rewrite("safe_division", expression, "clickhouse")
    expression = apply_rewrite(
        "add_query_settings", expression, "clickhouse", settings={**BASE_SETTINGS, **(additional or {})}
    )
    return expression.sql(dialect="clickhouse")


def _legacy_clickhouse_pipeline(sql: str, additional: tuple[tuple[str, str | int], ...] = ()) -> str:
    transformer = _transformer()
    return transformer.add_query_settings(transformer.transform(sql), additional)


CASE_NORMALIZATION_SQL = {
    "q1_fee": "SELECT SUM(SR_FEE) AS ctr_total_return FROM store_returns",
    "sr_return_amt": "SELECT SUM(SR_RETURN_AMT) FROM store_returns",
    "sr_refunded_cash": "SELECT SUM(SR_REFUNDED_CASH) FROM store_returns",
    "sr_return_amt_inc_tax": "SELECT SUM(SR_RETURN_AMT_INC_TAX) FROM store_returns",
    "sr_reversed_charge": "SELECT SUM(SR_REVERSED_CHARGE) FROM store_returns",
    "sr_store_credit": "SELECT SUM(SR_STORE_CREDIT) FROM store_returns",
    "sr_return_tax": "SELECT SUM(SR_RETURN_TAX) FROM store_returns",
    "sr_customer_sk": "SELECT SR_CUSTOMER_SK, SR_STORE_SK, SR_RETURNED_DATE_SK FROM store_returns",
    "qty": "SELECT SUM(SS_QUANTITY_QTY), SS_ITEM_SK FROM store_sales GROUP BY SS_ITEM_SK",
    "date_dim_columns": "SELECT D_YEAR, D_MOY FROM date_dim WHERE D_DATE_SK = 1 AND D_YEAR = 2000",
    "string_literal": "SELECT SR_FEE FROM store_returns WHERE note = 'SR_FEE and D_YEAR'",
    "already_lower": "SELECT sr_fee, d_year FROM store_returns, date_dim",
    "mixed_case_untouched": "SELECT Sr_Fee, d_Year FROM store_returns",
}

TYPE_CAST_SQL = {
    "nonzero_arithmetic": "SELECT col + 0.06, col - 0.01, col * 0.05, col / 0.07 FROM t",
    "zero_arithmetic": "SELECT col + 0.0, col - 0.00 FROM t",
    "then_nonzero": "SELECT CASE WHEN a THEN 0.06 END, CASE WHEN a THEN 0.05 END, CASE WHEN a THEN 0.07 END FROM t",
    "else_nonzero": "SELECT CASE WHEN a THEN b ELSE 0.06 END, CASE WHEN a THEN b ELSE 0.07 END FROM t",
    "then_else_zero": "SELECT CASE WHEN a THEN 0.0 ELSE 0.00 END FROM t",
    "else_zero": "SELECT CASE WHEN a > 1 THEN b ELSE 0.0 END FROM t",
    "coalesce_zero": "SELECT COALESCE(x, 0.0), COALESCE(y.z, 0.00) FROM t",
    "coalesce_nonzero": "SELECT COALESCE(x, 0.5) FROM t",
    "q6_between": "SELECT 1 FROM lineitem WHERE l_discount between 0.06 - 0.01 and 0.06 + 0.01",
    "q6_full": (
        "SELECT SUM(l_extendedprice * l_discount) AS revenue FROM lineitem "
        "WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
    ),
    "zero_comparison": "SELECT COUNT(*) FROM t WHERE c_acctbal > 0.00",
    "zero_in_parens": "SELECT (x + 0.0) FROM t",
    "integer_zero": "SELECT CASE WHEN a THEN 0 ELSE b END FROM t",
    "string_literal": "SELECT 'THEN 0.0' AS s, 'a + 0.0' AS u FROM t",
}

DECIMAL_DIVISION_SQL = {
    "nullable_decimal_divisor": "SELECT a / CAST(b AS Nullable(Decimal(15, 4))) FROM t",
    "non_nullable_decimal": "SELECT a / CAST(b AS Decimal(15, 4)) FROM t",
    "nullable_decimal_18_2": "SELECT x / CAST(y AS Nullable(Decimal(18, 2))) FROM t",
    "plain_division": "SELECT a / b FROM t",
}

SAFE_DIVISION_SQL = {
    "parenthesized_divisor": "SELECT revenue / ((store_sales + web_sales) / 2) FROM metrics",
    "numeric_literal": "SELECT total_sales / 12 FROM metrics",
    "function_call": "SELECT total / COUNT(*) FROM t",
    "sum_function": "SELECT revenue / SUM(quantity) FROM t",
    "already_nullif": "SELECT a / NULLIF(b, 0) FROM t",
    "multiple": "SELECT a / b, c / d FROM t",
    "string_literal": "SELECT 'a/b' AS label, x / y FROM t",
    "window_divisor": "SELECT mkt / SUM(volume) OVER (PARTITION BY o_year) FROM t",
    "decimal_literal_divisor": "SELECT a / 0.5 FROM t",
    "no_division": "SELECT a + b FROM t",
    "literal_divisor_integer": "SELECT a / 12 FROM t",
    "nested_window_sum": "SELECT SUM(x) * 100 / SUM(SUM(x)) OVER (PARTITION BY c) FROM t GROUP BY c",
}

SETTINGS_SQL = {
    "ddl": "CREATE TABLE foo (id Int32) ENGINE = MergeTree() ORDER BY id",
    "select_one": "SELECT 1",
    "count_star": "SELECT count(*) FROM store_sales",
    "trailing_semicolon": "SELECT 1 FROM t;",
    "leading_whitespace": "  SELECT 1 FROM t  ",
    "lowercase_select": "select a from t",
    "with_cte": "WITH c AS (SELECT 1 AS x) SELECT x FROM c",
    "union": "SELECT a FROM t UNION ALL SELECT b FROM u",
    "insert": "INSERT INTO t VALUES (1)",
    "q47_like": (
        "WITH v1 AS (SELECT d_year, SUM(ss_sales_price) AS sum_sales, "
        "AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_category, d_year) AS avg_sales "
        "FROM store_sales GROUP BY d_year, i_category) SELECT * FROM v1"
    ),
    "q66_like": (
        "SELECT w_warehouse_name, sum(jan_sales) as jan_sales "
        "FROM (SELECT w_warehouse_name, sum(CASE WHEN d_moy=1 THEN ws_ext_list_price ELSE 0 END) as jan_sales "
        "FROM web_sales JOIN date_dim ON ws_sold_date_sk = d_date_sk "
        "UNION ALL SELECT w_warehouse_name, sum(CASE WHEN d_moy=1 THEN cs_ext_list_price ELSE 0 END) as jan_sales "
        "FROM catalog_sales JOIN date_dim ON cs_sold_date_sk = d_date_sk) "
        "GROUP BY w_warehouse_name"
    ),
}

ADDITIONAL_SETTINGS = (
    (("join_algorithm", "grace_hash"), ("grace_hash_join_initial_buckets", 8)),
    (("join_algorithm", "hash"),),
    (("note", "it's"),),
)


def _clickhouse_cases() -> list[Case]:
    cases: list[Case] = []

    for name, sql in CASE_NORMALIZATION_SQL.items():
        cases.append(
            Case(
                f"clickhouse.normalize_case.{name}",
                lambda sql=sql: _transformer().normalize_case(sql),
                lambda sql=sql: apply_rewrite("lowercase_identifiers", sql, "clickhouse", patterns=LOWERCASE_PATTERNS),
            )
        )

    for name, sql in TYPE_CAST_SQL.items():
        cases.append(
            Case(
                f"clickhouse.fix_type_casts.{name}",
                lambda sql=sql: _transformer().fix_type_casts(sql),
                lambda sql=sql: apply_rewrite("decimal_zero_literals", sql, "clickhouse", precision=15, scale=2),
            )
        )

    for name, sql in DECIMAL_DIVISION_SQL.items():
        cases.append(
            Case(
                f"clickhouse.fix_decimal_division_by_zero.{name}",
                lambda sql=sql: _transformer().fix_decimal_division_by_zero(sql),
                lambda sql=sql: apply_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for name, sql in SAFE_DIVISION_SQL.items():
        cases.append(
            Case(
                f"clickhouse.safe_division.{name}",
                lambda sql=sql: _transformer().safe_division(sql),
                lambda sql=sql: apply_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for name, sql in SETTINGS_SQL.items():
        cases.append(
            Case(
                f"clickhouse.add_query_settings.{name}",
                lambda sql=sql: _transformer().add_query_settings(sql),
                lambda sql=sql: apply_rewrite("add_query_settings", sql, "clickhouse", settings=BASE_SETTINGS),
            )
        )

    for index, additional in enumerate(ADDITIONAL_SETTINGS):
        cases.append(
            Case(
                f"clickhouse.add_query_settings.additional_{index}",
                lambda additional=additional: _transformer().add_query_settings("SELECT 1", additional),
                lambda additional=additional: apply_rewrite(
                    "add_query_settings", "SELECT 1", "clickhouse", settings={**BASE_SETTINGS, **dict(additional)}
                ),
            )
        )

    existing_settings = "SELECT 1 FROM t SETTINGS joined_subquery_requires_alias = 1"
    cases.append(
        Case(
            "clickhouse.add_query_settings.existing_settings",
            lambda: _transformer().add_query_settings(existing_settings),
            lambda: apply_rewrite("add_query_settings", existing_settings, "clickhouse", settings=BASE_SETTINGS),
        )
    )

    unit_inputs = {
        **{f"normalize_case.{k}": v for k, v in CASE_NORMALIZATION_SQL.items()},
        **{f"type_casts.{k}": v for k, v in TYPE_CAST_SQL.items()},
        **{f"decimal_division.{k}": v for k, v in DECIMAL_DIVISION_SQL.items()},
        **{f"safe_division.{k}": v for k, v in SAFE_DIVISION_SQL.items()},
    }
    for name, sql in unit_inputs.items():
        cases.append(
            Case(
                f"clickhouse.pipeline.unit.{name}",
                lambda sql=sql: _legacy_clickhouse_pipeline(sql),
                lambda sql=sql: _fork_clickhouse_pipeline(sql),
            )
        )

    for query_id in TPCH_IDS:
        cases.append(
            Case(
                f"clickhouse.pipeline.tpch_q{query_id}",
                lambda query_id=query_id: _legacy_clickhouse_pipeline(_tpch_query(query_id, "clickhouse")),
                lambda query_id=query_id: _fork_clickhouse_pipeline(_tpch_query(query_id, "clickhouse")),
            )
        )

    cases.append(
        Case(
            "clickhouse.pipeline.tpch_q5_grace_hash",
            lambda: _legacy_clickhouse_pipeline(_tpch_query(5, "clickhouse"), ADDITIONAL_SETTINGS[0]),
            lambda: _fork_clickhouse_pipeline(_tpch_query(5, "clickhouse"), dict(ADDITIONAL_SETTINGS[0])),
        )
    )
    cases.append(
        Case(
            "clickhouse.pipeline.tpch_q21_hash",
            lambda: _legacy_clickhouse_pipeline(_tpch_query(21, "clickhouse"), ADDITIONAL_SETTINGS[1]),
            lambda: _fork_clickhouse_pipeline(_tpch_query(21, "clickhouse"), dict(ADDITIONAL_SETTINGS[1])),
        )
    )

    for query_id in TPCDS_CLICKHOUSE_IDS:
        cases.append(
            Case(
                f"clickhouse.pipeline.tpcds_q{query_id}",
                lambda query_id=query_id: _legacy_clickhouse_pipeline(_tpcds_query(query_id, "clickhouse")),
                lambda query_id=query_id: _fork_clickhouse_pipeline(_tpcds_query(query_id, "clickhouse")),
            )
        )

    return cases


STARROCKS_RESERVED_ALIASES = ["character", "rank", "order", "group", "key", "value", "row_number", "dense_rank"]

STARROCKS_SQL = {
    "identifier_quote": 'SELECT "col" FROM t',
    "identifier_in_literal": "SELECT 'has \"active\" word' FROM t",
    "identifier_json_literal": 'SELECT 1 FROM t WHERE j = \'{"k": "v"}\'',
    "identifier_mixed": "SELECT \"col\" FROM t WHERE s = 'x'",
    "substring_digits": "SELECT SUBSTRING(c_phone FROM 1 FOR 2) FROM t",
    "substring_expressions": "SELECT SUBSTRING(col FROM offset + 1 FOR n - 1) FROM t",
    "substring_literal": ("SELECT 1 FROM t WHERE msg = 'SUBSTRING(x FROM 1 FOR 2)' AND t = SUBSTRING(y FROM 3 FOR 4)"),
    "substring_nested_function": "SELECT SUBSTRING(LOWER(col) FROM 1 FOR 3) FROM t",
    "alias_not_reserved": "SELECT x AS total FROM t",
    "alias_literal_contents": "SELECT 'prefix AS rank suffix' AS total FROM t",
    "subquery_from": "SELECT * FROM (SELECT 1 AS x)",
    "subquery_aliased": "SELECT * FROM (SELECT 1 AS x) AS sq",
    "subquery_implicit_alias": "SELECT * FROM (SELECT 1 AS x) sq",
    "subquery_in": "SELECT * FROM t WHERE id IN (SELECT id FROM s)",
    "subquery_comma": "SELECT * FROM t1, (SELECT id FROM t2)",
    "subquery_with_cte": "SELECT * FROM (WITH cte AS (SELECT 1) SELECT * FROM cte)",
    "subquery_union": "SELECT * FROM (SELECT 1) UNION SELECT 2",
    "subquery_multiple": "SELECT * FROM (SELECT 1), (SELECT 2)",
    "subquery_exists": "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM s WHERE s.id = t.id)",
    "subquery_scalar": "SELECT (SELECT MAX(x) FROM s) AS m FROM t",
    "subquery_join": "SELECT * FROM t JOIN (SELECT id FROM s) ON t.id = id",
    "subquery_nested": "SELECT * FROM (SELECT * FROM (SELECT 1 AS x))",
}


def _starrocks_mixin():
    from benchbox.platforms.starrocks.workload import StarRocksWorkloadMixin

    return StarRocksWorkloadMixin.__new__(StarRocksWorkloadMixin)


def _legacy_starrocks_execute_rewrites(sql: str) -> str:
    mixin = _starrocks_mixin()
    sql = mixin._quote_reserved_aliases(sql)
    sql = mixin._translate_ansi_substring(sql)
    sql = mixin._translate_ansi_identifiers(sql)
    return mixin._inject_missing_subquery_aliases(sql)


def _fork_starrocks_execute_rewrites(sql: str, read: str = "starrocks", identify: bool = False) -> str:
    expression = _sqlglot().parse_one(sql, read=read)
    expression = apply_rewrite("add_derived_table_aliases", expression, "starrocks", **STARROCKS_ALIAS_ROUTE_ARGS)
    return expression.sql(dialect="starrocks", identify=identify)


def _starrocks_cases() -> list[Case]:
    cases: list[Case] = []
    for alias in STARROCKS_RESERVED_ALIASES:
        sql = f"SELECT x AS {alias} FROM t"
        cases.append(
            Case(
                f"starrocks.reserved_alias.{alias}",
                lambda sql=sql: _legacy_starrocks_execute_rewrites(sql),
                lambda sql=sql: _fork_starrocks_execute_rewrites(sql, "postgres"),
            )
        )
    for name, sql in STARROCKS_SQL.items():
        cases.append(
            Case(
                f"starrocks.unit.{name}",
                lambda sql=sql: _legacy_starrocks_execute_rewrites(sql),
                lambda sql=sql: _fork_starrocks_execute_rewrites(sql, "postgres"),
            )
        )
    for query_id in TPCH_IDS:
        cases.append(
            Case(
                f"starrocks.tpch_q{query_id}.translated",
                lambda query_id=query_id: _legacy_starrocks_execute_rewrites(_tpch_query(query_id, "starrocks")),
                lambda query_id=query_id: _fork_starrocks_execute_rewrites(_tpch_query(query_id, "starrocks")),
            )
        )
        cases.append(
            Case(
                f"starrocks.tpch_q{query_id}.end_to_end",
                lambda query_id=query_id: _legacy_starrocks_execute_rewrites(_tpch_query(query_id, "starrocks")),
                lambda query_id=query_id: _fork_starrocks_execute_rewrites(
                    _tpch_query(query_id, None), "postgres", identify=True
                ),
            )
        )
    return cases


def _legacy_clickhouse_override(query_id: int, seed: int) -> str:
    return _tpcds().get_query(query_id, seed=seed, dialect="clickhouse")


def _fork_clickhouse_override(query_id: int, seed: int) -> str:
    return apply_rewrite(
        "correlated_exists_to_in", _tpcds_clickhouse_translated(query_id, seed), "clickhouse", expected=3
    )


def _tpcds_override_cases() -> list[Case]:
    cases: list[Case] = []
    for query_id in (10, 35):
        for seed in (1, 2, 3):
            cases.append(
                Case(
                    f"tpcds_clickhouse_override.q{query_id}.seed{seed}",
                    lambda query_id=query_id, seed=seed: _legacy_clickhouse_override(query_id, seed),
                    lambda query_id=query_id, seed=seed: _fork_clickhouse_override(query_id, seed),
                )
            )

    def legacy_fail_closed(name: str) -> str:
        from benchbox.core.tpcds.benchmark import clickhouse_overrides

        return getattr(clickhouse_overrides, name)("SELECT * FROM store_sales")

    for query_id in (10, 35):
        cases.append(
            Case(
                f"tpcds_clickhouse_override.q{query_id}.fail_closed",
                lambda query_id=query_id: legacy_fail_closed(f"rewrite_q{query_id}_for_clickhouse"),
                lambda: apply_rewrite("correlated_exists_to_in", "SELECT * FROM store_sales", "clickhouse", expected=3),
            )
        )
    return cases


INTERVAL_UNIT_SQL = {
    "plus_minus_days": "select cast('2000-01-01' as date) + 60 days - 30 days",
    "plus_days": "SELECT d_date + 30 days FROM date_dim",
    "minus_days": "SELECT d_date - 14 days FROM date_dim",
    "uppercase_days": "SELECT d_date + 30 DAYS FROM date_dim",
    "between_days": "SELECT 1 FROM date_dim WHERE d_date BETWEEN cast('2000-02-01' as date) AND (cast('2000-02-01' as date) + 60 days)",
    "no_interval": "SELECT d_date FROM date_dim",
}

Q58_UNIT_SQL = {
    "quoted_order_by": (
        "WITH ss_items AS (SELECT 1 AS item_id), cs_items AS (SELECT 1 AS item_id), "
        'ws_items AS (SELECT 1 AS item_id) SELECT * FROM ss_items ORDER BY "item_id"'
    ),
    "unquoted_order_by": (
        "WITH ss_items AS (SELECT 1 AS item_id), cs_items AS (SELECT 1 AS item_id), "
        "ws_items AS (SELECT 1 AS item_id) SELECT * FROM ss_items ORDER BY item_id"
    ),
    "already_qualified": (
        "WITH ss_items AS (SELECT 1 AS item_id), cs_items AS (SELECT 1 AS item_id), "
        "ws_items AS (SELECT 1 AS item_id) SELECT * FROM ss_items ORDER BY ss_items.item_id"
    ),
    "missing_cs_items": (
        "WITH ss_items AS (SELECT 1 AS item_id), ws_items AS (SELECT 1 AS item_id) "
        "SELECT * FROM ss_items ORDER BY item_id"
    ),
}

INTERVAL_TARGETS = ("duckdb", "clickhouse")


def _fork_netezza_transpile(sql: str, target: str, qualifiers: dict[str, str] | None = None) -> str:
    sg = _sqlglot()
    expression = sg.parse_one(sql, read="netezza")
    lowered = sql.lower()
    if qualifiers and all(name in lowered for name in ("ss_items", "cs_items", "ws_items")):
        expression = apply_rewrite("qualify_order_by_columns", expression, "netezza", qualifiers=qualifiers)
    return expression.sql(dialect=target, identify=target not in NO_IDENTIFY_DIALECTS)


@lru_cache(maxsize=1)
def _interval_query_ids() -> tuple[int, ...]:
    pattern = re.compile(r"[+\-]\s+\d+\s+days", re.IGNORECASE)
    return tuple(query_id for query_id in range(1, 100) if pattern.search(_tpcds_raw_netezza(query_id)))


def _tpcds_translation_cases() -> list[Case]:
    cases: list[Case] = []
    qualifiers = {"item_id": "ss_items"}
    for target in INTERVAL_TARGETS:
        for name, sql in INTERVAL_UNIT_SQL.items():
            cases.append(
                Case(
                    f"tpcds_interval.unit.{name}.{target}",
                    lambda sql=sql, target=target: _tpcds().translate_query_text(sql, "netezza", target),
                    lambda sql=sql, target=target: _fork_netezza_transpile(sql, target),
                )
            )
        for name, sql in Q58_UNIT_SQL.items():
            cases.append(
                Case(
                    f"tpcds_q58.unit.{name}.{target}",
                    lambda sql=sql, target=target: _tpcds().translate_query_text(sql, "netezza", target),
                    lambda sql=sql, target=target: _fork_netezza_transpile(sql, target, qualifiers),
                )
            )
        for query_id in (*_interval_query_ids(), 58):
            cases.append(
                Case(
                    f"tpcds_interval.q{query_id}.{target}",
                    lambda query_id=query_id, target=target: _tpcds().translate_query_text(
                        _tpcds_raw_netezza(query_id), "netezza", target
                    ),
                    lambda query_id=query_id, target=target: _fork_netezza_transpile(
                        _tpcds_raw_netezza(query_id), target, qualifiers
                    ),
                )
            )
    return cases


def cases() -> list[Case]:
    return [*_clickhouse_cases(), *_starrocks_cases(), *_tpcds_override_cases(), *_tpcds_translation_cases()]
