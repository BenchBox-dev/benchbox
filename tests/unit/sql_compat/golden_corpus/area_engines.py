from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from functools import cache, lru_cache

from benchbox.utils.dialect_utils import apply_rewrite

from . import Case, frozen_legacy

AREA = "area_engines"

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


def _production_clickhouse_pipeline(sql: str, additional: dict[str, str | int] | None = None) -> str:
    from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

    return rewrite_query_for_clickhouse(sql, additional)


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
    "select_one": "SELECT 1",
    "count_star": "SELECT count(*) FROM store_sales",
    "trailing_semicolon": "SELECT 1 FROM t;",
    "leading_whitespace": "  SELECT 1 FROM t  ",
    "lowercase_select": "select a from t",
    "with_cte": "WITH c AS (SELECT 1 AS x) SELECT x FROM c",
    "union": "SELECT a FROM t UNION ALL SELECT b FROM u",
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
        case_id = f"clickhouse.normalize_case.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: apply_rewrite("lowercase_identifiers", sql, "clickhouse", patterns=LOWERCASE_PATTERNS),
            )
        )

    for name, sql in TYPE_CAST_SQL.items():
        case_id = f"clickhouse.fix_type_casts.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: apply_rewrite("decimal_zero_literals", sql, "clickhouse", precision=15, scale=2),
            )
        )

    for name, sql in DECIMAL_DIVISION_SQL.items():
        case_id = f"clickhouse.fix_decimal_division_by_zero.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: apply_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for name, sql in SAFE_DIVISION_SQL.items():
        case_id = f"clickhouse.safe_division.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: apply_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for name, sql in SETTINGS_SQL.items():
        case_id = f"clickhouse.add_query_settings.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: apply_rewrite("add_query_settings", sql, "clickhouse", settings=BASE_SETTINGS),
            )
        )

    for index, additional in enumerate(ADDITIONAL_SETTINGS):
        case_id = f"clickhouse.add_query_settings.additional_{index}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda additional=additional: apply_rewrite(
                    "add_query_settings", "SELECT 1", "clickhouse", settings={**BASE_SETTINGS, **dict(additional)}
                ),
            )
        )

    existing_settings = "SELECT 1 FROM t SETTINGS joined_subquery_requires_alias = 1"
    case_id = "clickhouse.add_query_settings.existing_settings"
    cases.append(
        Case(
            case_id,
            frozen_legacy(AREA, case_id),
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
        case_id = f"clickhouse.pipeline.unit.{name}"
        cases.append(Case(case_id, frozen_legacy(AREA, case_id), lambda sql=sql: _production_clickhouse_pipeline(sql)))

    for query_id in TPCH_IDS:
        case_id = f"clickhouse.pipeline.tpch_q{query_id}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda query_id=query_id: _production_clickhouse_pipeline(_tpch_query(query_id, "clickhouse")),
            )
        )

    cases.append(
        Case(
            "clickhouse.pipeline.tpch_q5_grace_hash",
            frozen_legacy(AREA, "clickhouse.pipeline.tpch_q5_grace_hash"),
            lambda: _production_clickhouse_pipeline(_tpch_query(5, "clickhouse"), dict(ADDITIONAL_SETTINGS[0])),
        )
    )
    cases.append(
        Case(
            "clickhouse.pipeline.tpch_q21_hash",
            frozen_legacy(AREA, "clickhouse.pipeline.tpch_q21_hash"),
            lambda: _production_clickhouse_pipeline(_tpch_query(21, "clickhouse"), dict(ADDITIONAL_SETTINGS[1])),
        )
    )

    for query_id in TPCDS_CLICKHOUSE_IDS:
        case_id = f"clickhouse.pipeline.tpcds_q{query_id}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda query_id=query_id: _production_clickhouse_pipeline(_tpcds_query(query_id, "clickhouse")),
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


def _alias_derived_tables(sql: str) -> str:
    from benchbox.platforms.starrocks.workload import _alias_derived_tables as production

    return production(sql)


def _fork_starrocks_execute_rewrites(sql: str, read: str = "starrocks", identify: bool = False) -> str:
    expression = _sqlglot().parse_one(sql, read=read)
    expression = apply_rewrite("add_derived_table_aliases", expression, "starrocks", **STARROCKS_ALIAS_ROUTE_ARGS)
    return expression.sql(dialect="starrocks", identify=identify)


def _starrocks_cases() -> list[Case]:
    cases: list[Case] = []
    for alias in STARROCKS_RESERVED_ALIASES:
        sql = f"SELECT x AS {alias} FROM t"
        case_id = f"starrocks.reserved_alias.{alias}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: _fork_starrocks_execute_rewrites(sql, "postgres"),
            )
        )
    for name, sql in STARROCKS_SQL.items():
        case_id = f"starrocks.unit.{name}"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda sql=sql: _fork_starrocks_execute_rewrites(sql, "postgres"),
            )
        )
    for query_id in TPCH_IDS:
        translated_id = f"starrocks.tpch_q{query_id}.translated"
        cases.append(
            Case(
                translated_id,
                frozen_legacy(AREA, translated_id),
                lambda query_id=query_id: _alias_derived_tables(_tpch_query(query_id, "starrocks")),
            )
        )
        end_to_end_id = f"starrocks.tpch_q{query_id}.end_to_end"
        cases.append(
            Case(
                end_to_end_id,
                frozen_legacy(AREA, end_to_end_id),
                lambda query_id=query_id: _fork_starrocks_execute_rewrites(
                    _tpch_query(query_id, None), "postgres", identify=True
                ),
            )
        )
    return cases


def _production_clickhouse_override(query_id: int, query: str) -> str:
    return _tpcds()._apply_target_dialect_overrides(query_id, query, "clickhouse")


def _tpcds_override_cases() -> list[Case]:
    cases: list[Case] = []
    for query_id in (10, 35):
        for seed in (1, 2, 3):
            case_id = f"tpcds_clickhouse_override.q{query_id}.seed{seed}"
            cases.append(
                Case(
                    case_id,
                    frozen_legacy(AREA, case_id),
                    lambda query_id=query_id, seed=seed: _production_clickhouse_override(
                        query_id, _tpcds_clickhouse_translated(query_id, seed)
                    ),
                )
            )

    for query_id in (10, 35):
        case_id = f"tpcds_clickhouse_override.q{query_id}.fail_closed"
        cases.append(
            Case(
                case_id,
                frozen_legacy(AREA, case_id),
                lambda query_id=query_id: _production_clickhouse_override(query_id, "SELECT * FROM store_sales"),
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


def _production_tpcds_translation(sql: str, target: str) -> str:
    return _tpcds().translate_query_text(sql, "netezza", target)


@lru_cache(maxsize=1)
def _interval_query_ids() -> tuple[int, ...]:
    pattern = re.compile(r"[+\-]\s+\d+\s+days", re.IGNORECASE)
    return tuple(query_id for query_id in range(1, 100) if pattern.search(_tpcds_raw_netezza(query_id)))


def _tpcds_translation_cases() -> list[Case]:
    cases: list[Case] = []
    for target in INTERVAL_TARGETS:
        for name, sql in INTERVAL_UNIT_SQL.items():
            case_id = f"tpcds_interval.unit.{name}.{target}"
            cases.append(
                Case(
                    case_id,
                    frozen_legacy(AREA, case_id),
                    lambda sql=sql, target=target: _production_tpcds_translation(sql, target),
                )
            )
        for name, sql in Q58_UNIT_SQL.items():
            case_id = f"tpcds_q58.unit.{name}.{target}"
            cases.append(
                Case(
                    case_id,
                    frozen_legacy(AREA, case_id),
                    lambda sql=sql, target=target: _production_tpcds_translation(sql, target),
                )
            )
        for query_id in (*_interval_query_ids(), 58):
            case_id = f"tpcds_interval.q{query_id}.{target}"
            cases.append(
                Case(
                    case_id,
                    frozen_legacy(AREA, case_id),
                    lambda query_id=query_id, target=target: _production_tpcds_translation(
                        _tpcds_raw_netezza(query_id), target
                    ),
                )
            )
    return cases


def _norm(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def _same_ignoring_format(legacy: str, fork: str) -> bool:
    return _norm(legacy) == _norm(fork)


def _same_with_cross_join(legacy: str, fork: str) -> bool:
    return _norm(legacy) == _norm(fork.replace(" CROSS JOIN ", ", "))


def _literal_kept(literal: str):
    return lambda legacy, fork: literal in fork and literal not in legacy


def _divisor_guarded(legacy: str, fork: str) -> bool:
    return "nullif" not in legacy.lower() and "nullIf(" in fork


def _duplicate_settings(legacy: str, fork: str) -> bool:
    return legacy.count("SETTINGS") == 2 and fork.count("SETTINGS") == 1


def _zero_in_parens(legacy: str, fork: str) -> bool:
    return "(x + 0.0)" in legacy and "(x + CAST(0 AS Decimal(15, 2)))" in fork


def _implicit_alias(legacy: str, fork: str) -> bool:
    return _norm(legacy) == _norm(fork.replace(" AS sq", " sq"))


def _value_alias(legacy: str, fork: str) -> bool:
    return legacy == "SELECT x AS `value` FROM t" and fork == "SELECT x AS value FROM t"


def _join_alias(legacy: str, fork: str) -> bool:
    return "_sq" not in legacy and "AS `_sq0` ON" in fork


def _nested_alias(legacy: str, fork: str) -> bool:
    return legacy.count("_sq") == 1 and fork.count("_sq") == 2


def _nested_substring(legacy: str, fork: str) -> bool:
    return "FROM 1 FOR 3" in legacy and "LOWER(col), 1, 3" in fork


_FORMAT_ONLY = "Only spacing, keyword case or the NULLIF spelling differs; ClickHouse accepts both spellings."

CLASSIFICATIONS: list[tuple[str, str, str, Callable[[str, str], bool]]] = [
    (
        r"clickhouse\.fix_type_casts\.(coalesce_zero|else_zero|q6_between|then_else_zero|zero_arithmetic)",
        "equivalent",
        _FORMAT_ONLY,
        _same_ignoring_format,
    ),
    (
        r"clickhouse\.(fix_decimal_division_by_zero\.nullable_decimal_(18_2|divisor)|"
        r"safe_division\.(already_nullif|function_call|multiple|parenthesized_divisor|string_literal|"
        r"sum_function|window_divisor)|add_query_settings\.(lowercase_select|q66_like))",
        "equivalent",
        _FORMAT_ONLY,
        _same_ignoring_format,
    ),
    (
        r"clickhouse\.normalize_case\.already_lower",
        "equivalent",
        "The runtime renders the comma join as CROSS JOIN, which ClickHouse treats the same way.",
        _same_with_cross_join,
    ),
    (
        r"(clickhouse\.normalize_case\.string_literal|clickhouse\.pipeline\.unit\.normalize_case\.string_literal)",
        "legacy-bug",
        "The legacy regex lowercased column-like text inside a string literal and changed the compared value; "
        "the runtime rewrites identifiers only.",
        _literal_kept("'SR_FEE and D_YEAR'"),
    ),
    (
        r"(clickhouse\.fix_type_casts\.string_literal|clickhouse\.pipeline\.unit\.type_casts\.string_literal)",
        "legacy-bug",
        "The legacy regex rewrote 0.0 inside string literals; the runtime casts zero literals in expressions only.",
        _literal_kept("'THEN 0.0'"),
    ),
    (
        r"(clickhouse\.fix_type_casts\.zero_in_parens|clickhouse\.pipeline\.unit\.type_casts\.zero_in_parens)",
        "legacy-bug",
        "The legacy regex skipped a zero literal followed by a closing parenthesis and left a Float64 zero in a "
        "Decimal expression; the runtime casts it.",
        _zero_in_parens,
    ),
    (
        r"clickhouse\.(fix_decimal_division_by_zero\.(non_nullable_decimal|plain_division)|"
        r"safe_division\.nested_window_sum)",
        "legacy-bug",
        "The legacy method guarded only Nullable(Decimal) divisors (or missed a divisor after a scaled numerator) "
        "and the production pipeline had already moved to the runtime's safe_division; the runtime wraps every "
        "non-literal divisor as nullIf(divisor, 0).",
        _divisor_guarded,
    ),
    (
        r"clickhouse\.add_query_settings\.existing_settings",
        "legacy-bug",
        "The legacy method appended a second SETTINGS clause, which ClickHouse rejects; the runtime replaces the "
        "setting in the existing clause.",
        _duplicate_settings,
    ),
    (
        r"starrocks\.reserved_alias\.value",
        "equivalent",
        "StarRocks reserves VALUES but not VALUE (docs.starrocks.io keywords list), so the unquoted alias parses; "
        "the legacy list over-quoted it.",
        _value_alias,
    ),
    (
        r"starrocks\.unit\.subquery_exists",
        "equivalent",
        "Only the space after EXISTS differs.",
        _same_ignoring_format,
    ),
    (
        r"starrocks\.unit\.subquery_implicit_alias",
        "equivalent",
        "The runtime writes the implicit alias with AS.",
        _implicit_alias,
    ),
    (
        r"starrocks\.unit\.subquery_join",
        "legacy-bug",
        "The legacy scanner aliased only FROM and comma subqueries, so a JOIN-derived table reached StarRocks "
        "without an alias and failed to parse; the runtime aliases it.",
        _join_alias,
    ),
    (
        r"starrocks\.unit\.subquery_nested",
        "legacy-bug",
        "The legacy scanner aliased only the outer derived table; the runtime aliases every level.",
        _nested_alias,
    ),
    (
        r"starrocks\.unit\.substring_nested_function",
        "legacy-bug",
        "The legacy regex left SUBSTRING(... FROM ... FOR ...) around a function argument, which StarRocks "
        "rejects (it logged a warning); the runtime writes the comma form.",
        _nested_substring,
    ),
    (
        r"tpcds_clickhouse_override\.q(10|35)\.seed[123]",
        "equivalent",
        "The rewritten query is re-rendered by the runtime, which writes each comma join as CROSS JOIN; "
        "ClickHouse treats both the same way and the semi-join predicates are identical.",
        _same_with_cross_join,
    ),
]


def cases() -> list[Case]:
    return [*_clickhouse_cases(), *_starrocks_cases(), *_tpcds_override_cases(), *_tpcds_translation_cases()]
