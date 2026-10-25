from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from benchbox._vendor.sqlglot import ErrorLevel
from benchbox.utils.dialect_utils import (
    NO_IDENTIFY_DIALECTS,
    apply_rewrite,
    resolve_sql_dialect,
    translate_sql_query,
)

from . import Case, frozen_legacy

AREA = "area_wrapper"
ROOT = Path(__file__).resolve().parents[4]
TPCDS_TEMPLATES = ROOT / "benchbox" / "_binaries" / "tpc-ds" / "templates" / "query_templates"
BASELINE = ROOT / "scripts" / "sqlglot_runtime_baseline.json"

QUERY58_QUALIFIERS = {"item_id": "ss_items"}


@dataclass(frozen=True)
class WrapperCase(Case):
    current: Callable[[], str]


def _tpcds_date_columns() -> list[str]:
    from benchbox.core.tpcds.benchmark.runner import _DATE_COLUMNS

    return list(_DATE_COLUMNS)


def _current(sql: str, src: str, tgt: str, identify: bool = True):
    return lambda: translate_sql_query(sql, tgt, source_dialect=src, identify=identify, strict=True)


def _fork(sql: str, src: str, tgt: str, identify: bool = True, rewrites=()):
    import benchbox._vendor.sqlglot as sqlglot

    def produce() -> str:
        read, write = resolve_sql_dialect(src), resolve_sql_dialect(tgt)
        expression = sqlglot.parse_one(sql, read=read)
        for name, args in rewrites:
            expression = apply_rewrite(name, expression, write, **args)
        should_identify = identify and write not in NO_IDENTIFY_DIALECTS
        return expression.sql(dialect=write, identify=should_identify, unsupported_level=ErrorLevel.RAISE)

    return produce


def _case(case_id: str, sql: str, src: str, tgt: str, identify: bool = True) -> Case:
    rewrites = [("fold_between_literal_arithmetic", {})] if tgt == "sqlite" else []
    return WrapperCase(
        case_id,
        frozen_legacy(AREA, case_id),
        _fork(sql, src, tgt, identify, rewrites),
        _current(sql, src, tgt, identify),
    )


SQLITE_DISCOUNT = [
    (
        "qualified-nested",
        "SELECT 0.06 + 0.01 FROM lineitem AS l "
        "WHERE l.l_discount BETWEEN (0.06 - 0.02 + 0.01) AND (0.06 + 0.02 - 0.01)",
    ),
    (
        "only-discount-006",
        "SELECT 0.06 + 0.01 AS untouched FROM lineitem "
        "WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01 AND l_quantity < 25",
    ),
    (
        "only-discount-007",
        "SELECT 0.06 + 0.01 AS untouched FROM lineitem "
        "WHERE l_discount BETWEEN 0.07 - 0.01 AND 0.07 + 0.01 AND l_quantity < 25",
    ),
    ("q6-literal", "SELECT * FROM lineitem WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"),
    (
        "huge-cancellation",
        "SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN "
        "(10000000000000000000000000000.0 + 9.0 - 10000000000000000000000000000.0) AND (20.0 + 0.0)",
    ),
    (
        "tiny-cancellation",
        "SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN "
        "(100000000.0 + 0.0000000001 - 100000000.0) AND (0.06 + 0.01)",
    ),
    (
        "overflow-literal",
        f"SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN (1{'0' * 400}.0 - 1{'0' * 400}.0) AND (0.06 + 0.01)",
    ),
    (
        "five-leaves",
        "SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN 0.01 + 0.01 + 0.01 + 0.01 + 0.01 AND 0.06 + 0.01",
    ),
    (
        "seven-fraction-digits",
        "SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN 0.0000001 + 0.0000001 AND 0.06 + 0.01",
    ),
    (
        "three-integer-digits",
        "SELECT 0.06 + 0.01 FROM lineitem WHERE l_discount BETWEEN 100.0 - 99.0 AND 0.06 + 0.01",
    ),
    (
        "integer-overflow-cte",
        "WITH lineitem AS (SELECT 0 AS l_discount) SELECT * FROM lineitem "
        "WHERE l_discount BETWEEN (9223372036854775807 + 1 - 9223372036854775807) AND (1 + 1)",
    ),
    (
        "bare-dot-decimals",
        "SELECT l_discount FROM lineitem WHERE l_discount BETWEEN .06 - .01 AND .06 + .01 ORDER BY l_discount",
    ),
    (
        "noise-band-cte",
        "WITH lineitem AS (SELECT 0.00000000000000001 AS l_discount) SELECT l_discount FROM lineitem "
        "WHERE l_discount BETWEEN (0.0 + 0.0) AND (0.1 + 0.2 - 0.3)",
    ),
    ("not-lineitem-orders", "SELECT * FROM orders WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"),
    ("not-lineitem-t", "SELECT * FROM t WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"),
    (
        "lineitem-string-only",
        "SELECT 'lineitem' AS note FROM t WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "lineitem-comment-only",
        "SELECT * FROM t /* lineitem */ WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "join-lineitem",
        "SELECT 1 FROM orders o JOIN lineitem l ON l.l_orderkey = o.o_orderkey "
        "WHERE l.l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "subquery-lineitem",
        "SELECT * FROM (SELECT l_discount FROM lineitem) WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "negative-low",
        "SELECT * FROM lineitem WHERE l_discount BETWEEN 0.01 - 0.02 AND 0.01 + 0.02",
    ),
    (
        "one-bound-literal",
        "SELECT * FROM lineitem WHERE l_discount BETWEEN 0.05 AND 0.06 + 0.01",
    ),
    (
        "other-column",
        "SELECT * FROM lineitem WHERE l_tax BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "mixed-case-names",
        "SELECT * FROM LINEITEM WHERE L_Discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
    ),
    (
        "q6-full",
        "SELECT SUM(l_extendedprice * l_discount) AS revenue FROM lineitem "
        "WHERE l_shipdate >= DATE '1994-01-01' AND l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR "
        "AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01 AND l_quantity < 24",
    ),
    (
        "q6-full-day-interval",
        "SELECT SUM(l_extendedprice * l_discount) AS revenue FROM lineitem "
        "WHERE l_shipdate >= DATE '1994-01-01' AND l_shipdate < DATE '1994-01-01' + INTERVAL '365' DAY "
        "AND l_discount BETWEEN 0.07 - 0.01 AND 0.07 + 0.01 AND l_quantity < 24",
    ),
    (
        "between-aggregate-select",
        "SELECT SUM(100 * l_discount) FROM lineitem WHERE l_discount BETWEEN 0.07 - 0.01 AND 0.07 + 0.01",
    ),
    (
        "between-select-list",
        "SELECT l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01 FROM lineitem ORDER BY l_discount",
    ),
]

SQLITE_SYNTAX = [
    ("date-plus-day", "SELECT DATE '1994-01-01' + INTERVAL '1' DAY AS result"),
    ("date-minus-day", "SELECT DATE '1994-01-02' - INTERVAL '1' DAY AS result"),
    ("cast-date-plus-7-day", "SELECT CAST('1994-01-01' AS DATE) + INTERVAL '7' DAY AS result"),
    ("date-plus-month", "SELECT DATE '1994-01-01' + INTERVAL '1' MONTH AS result"),
    ("date-minus-month", "SELECT DATE '1994-03-01' - INTERVAL '3' MONTH AS result"),
    ("date-plus-year", "SELECT DATE '1994-01-01' + INTERVAL '1' YEAR AS result"),
    ("date-minus-year", "SELECT DATE '1994-01-01' - INTERVAL '2' YEAR AS result"),
    ("date-month-end-month", "SELECT DATE '2024-01-31' + INTERVAL '1' MONTH"),
    ("date-leap-day-year", "SELECT DATE '2024-02-29' + INTERVAL '1' YEAR"),
    ("date-leap-day-day", "SELECT DATE '2024-02-29' + INTERVAL '1' DAY"),
    ("date-plus-hour", "SELECT DATE '2024-02-29' + INTERVAL '1' HOUR"),
    ("date-plus-minute", "SELECT DATE '2024-02-29' + INTERVAL '30' MINUTE"),
    ("date-plus-second", "SELECT DATE '2024-02-29' + INTERVAL '45' SECOND"),
    ("date-plus-week", "SELECT DATE '2024-02-29' + INTERVAL '2' WEEK"),
    ("date-plus-quarter", "SELECT DATE '2024-02-29' + INTERVAL '1' QUARTER"),
    ("date-plus-zero-day", "SELECT DATE '2024-02-29' + INTERVAL '0' DAY"),
    ("date-plus-negative-day", "SELECT DATE '2024-02-29' + INTERVAL '-5' DAY"),
    ("date-plus-large-day", "SELECT DATE '1998-12-01' - INTERVAL '90' DAY"),
    (
        "interval-day-in-where",
        "SELECT l_orderkey FROM lineitem WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL '90' DAY",
    ),
    ("null-date-interval", "SELECT CAST(NULL AS DATE) + INTERVAL '1' DAY"),
    ("column-plus-interval-day", "SELECT o_orderdate + INTERVAL '30' DAY FROM orders"),
    ("column-plus-interval-month", "SELECT o_orderdate + INTERVAL '3' MONTH FROM orders"),
    ("column-minus-interval-year", "SELECT o_orderdate - INTERVAL '1' YEAR FROM orders"),
    ("timestamp-plus-day", "SELECT TIMESTAMP '2024-01-01 10:00:00' + INTERVAL '1' DAY"),
    ("date-plus-interval-day-lowercase", "select date '1994-01-01' + interval '1' day"),
    ("date-plus-interval-day-nested", "SELECT EXTRACT(DAY FROM DATE '1994-01-01' + INTERVAL '1' DAY)"),
    ("extract-year-date", "SELECT EXTRACT(YEAR FROM DATE '1994-06-15')"),
    ("extract-month-date", "SELECT EXTRACT(MONTH FROM DATE '1994-06-15')"),
    ("extract-day-date", "SELECT EXTRACT(DAY FROM DATE '1994-06-15')"),
    ("extract-year-cast", "SELECT EXTRACT(YEAR FROM CAST('1994-06-15' AS DATE))"),
    ("extract-month-cast", "SELECT EXTRACT(MONTH FROM CAST('1994-06-15' AS DATE))"),
    ("extract-day-cast", "SELECT EXTRACT(DAY FROM CAST('1994-06-15' AS DATE))"),
    ("extract-hour-date", "SELECT EXTRACT(HOUR FROM TIMESTAMP '1994-06-15 10:20:30')"),
    ("extract-minute-ts", "SELECT EXTRACT(MINUTE FROM TIMESTAMP '1994-06-15 10:20:30')"),
    ("extract-second-ts", "SELECT EXTRACT(SECOND FROM TIMESTAMP '1994-06-15 10:20:30')"),
    ("extract-year-column", "SELECT EXTRACT(YEAR FROM l_shipdate) AS ship_year FROM lineitem"),
    ("extract-unknown-column", "SELECT EXTRACT(YEAR FROM d) FROM t"),
    ("extract-year-qualified-column", "SELECT EXTRACT(YEAR FROM l.l_shipdate) FROM lineitem l"),
    ("extract-year-group", "SELECT EXTRACT(YEAR FROM o_orderdate) AS y, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1"),
    ("extract-dow", "SELECT EXTRACT(DOW FROM o_orderdate) FROM orders"),
    ("extract-doy", "SELECT EXTRACT(DOY FROM o_orderdate) FROM orders"),
    ("extract-quarter", "SELECT EXTRACT(QUARTER FROM o_orderdate) FROM orders"),
    ("extract-week", "SELECT EXTRACT(WEEK FROM o_orderdate) FROM orders"),
    ("extract-epoch", "SELECT EXTRACT(EPOCH FROM o_orderdate) FROM orders"),
    ("extract-year-lowercase", "select extract(year from o_orderdate) from orders"),
    ("extract-year-fractional", "SELECT EXTRACT(YEAR FROM DATE '2020-06-01') / 3 > 673"),
    ("extract-year-cast-int-division", "SELECT CAST(EXTRACT(YEAR FROM DATE '2020-06-01') AS INTEGER) / 3 > 673"),
    ("integer-division", "SELECT 2020 / 3 > 673"),
    ("extract-year-negated-division", "SELECT -EXTRACT(YEAR FROM DATE '2020-06-01') / 3 < -673"),
    (
        "extract-year-subquery-division",
        "SELECT (SELECT y / 3 > 673 FROM (SELECT EXTRACT(YEAR FROM DATE '2020-06-01') AS y) AS t)",
    ),
    ("extract-year-nested-expression", "SELECT EXTRACT(YEAR FROM o_orderdate + 1) FROM orders"),
    ("extract-year-from-interval-sum", "SELECT EXTRACT(YEAR FROM o_orderdate + INTERVAL '1' YEAR) FROM orders"),
    ("date-trunc-month-column", "SELECT DATE_TRUNC('MONTH', o_orderdate) FROM orders"),
    ("date-trunc-year-column", "SELECT DATE_TRUNC('YEAR', o_orderdate) FROM orders"),
    ("date-trunc-day-column", "SELECT DATE_TRUNC('DAY', o_orderdate) FROM orders"),
    ("date-trunc-hour-column", "SELECT DATE_TRUNC('HOUR', EventTime) FROM hits"),
    ("date-trunc-minute-column", "SELECT DATE_TRUNC('MINUTE', EventTime) FROM hits"),
    ("date-trunc-second-column", "SELECT DATE_TRUNC('SECOND', EventTime) FROM hits"),
    ("date-trunc-week-column", "SELECT DATE_TRUNC('WEEK', EventTime) FROM hits"),
    ("date-trunc-quarter-column", "SELECT DATE_TRUNC('QUARTER', EventTime) FROM hits"),
    ("date-trunc-lowercase-unit", "SELECT DATE_TRUNC('month', o_orderdate) FROM orders"),
    ("date-trunc-literal", "SELECT DATE_TRUNC('MONTH', DATE '1994-06-15')"),
    ("date-trunc-expression", "SELECT DATE_TRUNC('MONTH', o_orderdate + INTERVAL '1' DAY) FROM orders"),
    ("date-trunc-group-by", "SELECT DATE_TRUNC('MONTH', o_orderdate) AS m, COUNT(*) FROM orders GROUP BY 1"),
    ("date-part-year", "SELECT DATE_PART('year', o_orderdate) FROM orders"),
    ("date-part-month", "SELECT DATE_PART('month', o_orderdate) FROM orders"),
    ("cast-date-as-text", "SELECT CAST(DATE '1994-01-01' AS TEXT)"),
    ("date-literal", "SELECT DATE '1994-01-01'"),
    ("cast-string-as-date", "SELECT CAST('1994-01-01' AS DATE)"),
    (
        "interval-and-extract-select",
        "SELECT EXTRACT(YEAR FROM l_shipdate) AS ship_year FROM lineitem "
        "WHERE l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR",
    ),
    ("group-by-all-to-sqlite", "WITH t AS (SELECT 1 AS x) SELECT x FROM t GROUP BY ALL"),
    ("plain-select", "SELECT 1"),
]

SQLITE_CLICKHOUSE = [
    (
        "clickhouse-time-extract-date-trunc",
        "SELECT EXTRACT(HOUR FROM EventTime), EXTRACT(MINUTE FROM EventTime), DATE_TRUNC('MINUTE', EventTime) FROM hits",
    ),
    ("clickhouse-to-start-of-month", "SELECT toStartOfMonth(EventDate) FROM hits"),
    ("clickhouse-date-trunc-day", "SELECT DATE_TRUNC('DAY', EventTime) FROM hits"),
    ("clickhouse-extract-year", "SELECT EXTRACT(YEAR FROM EventDate) FROM hits"),
]

DUCKDB_ALL = [
    (
        "order-by-all-regions",
        "SELECT r_name, n_name, COUNT(*) AS supplier_count FROM region r JOIN nation n ON n.n_regionkey = r.r_regionkey "
        "GROUP BY r_name, n_name ORDER BY ALL",
    ),
    (
        "group-by-all-nations",
        "SELECT n_name, r_name, COUNT(*) AS nation_count FROM nation n JOIN region r ON n.n_regionkey = r.r_regionkey "
        "GROUP BY ALL ORDER BY n_name",
    ),
    ("group-by-all-cte", "WITH t AS (SELECT 1 AS x) SELECT x FROM t GROUP BY ALL"),
    ("order-by-all-cte", "WITH t AS (SELECT 1 AS x) SELECT x FROM t ORDER BY ALL"),
    ("order-by-all-desc", "SELECT x, y FROM t ORDER BY ALL DESC"),
    ("group-and-order-by-all", "SELECT x, COUNT(*) FROM t GROUP BY ALL ORDER BY ALL"),
    ("lowercase-order-by-all", "select x from t order by all"),
    ("quoted-all-alias-order", 'SELECT 1 AS "ALL" ORDER BY "ALL"'),
    ("quoted-all-alias-group", 'SELECT 1 AS "ALL" GROUP BY "ALL"'),
    ("order-by-all-newline", "SELECT x FROM t\nORDER   BY\n ALL"),
    ("order-by-all-in-subquery", "SELECT * FROM (SELECT x FROM t ORDER BY ALL) AS s"),
    ("no-all-keyword", "SELECT x FROM t ORDER BY x"),
]

DIALECT_QUERIES = [
    (
        "tpch-q1-shape",
        "SELECT l_returnflag, l_linestatus, SUM(l_quantity) AS sum_qty, AVG(l_discount) AS avg_disc, COUNT(*) AS cnt "
        "FROM lineitem WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL '90' DAY "
        "GROUP BY l_returnflag, l_linestatus ORDER BY l_returnflag, l_linestatus",
    ),
    (
        "tpch-q6",
        "SELECT SUM(l_extendedprice * l_discount) AS revenue FROM lineitem "
        "WHERE l_shipdate >= DATE '1994-01-01' AND l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR "
        "AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01 AND l_quantity < 24",
    ),
    (
        "extract-substring-limit",
        "SELECT EXTRACT(YEAR FROM o_orderdate) AS y, SUBSTRING(o_comment FROM 1 FOR 5) AS c, "
        "o_comment || 'x' AS d FROM orders ORDER BY 1 LIMIT 10",
    ),
    (
        "date-columns-and-casts",
        "SELECT CAST(o_totalprice AS DECIMAL(15, 2)), CAST(o_orderdate AS VARCHAR(10)), COALESCE(o_comment, 'n') "
        "FROM orders WHERE o_orderdate BETWEEN DATE '1994-01-01' AND DATE '1994-12-31'",
    ),
    (
        "case-and-join",
        "SELECT CASE WHEN o_orderpriority IN ('1-URGENT', '2-HIGH') THEN 1 ELSE 0 END AS high "
        "FROM orders o LEFT OUTER JOIN customer c ON o.o_custkey = c.c_custkey",
    ),
]

DIALECT_NAMES = ["netezza", "greenplum", "vertica", "datafusion", "ansi", "standard"]


def _tpcds_template(number: int) -> str:
    text = (TPCDS_TEMPLATES / f"query{number}.tpl").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("define", "--")))
    text = re.sub(r"\[_LIMIT[A-C]\]", "", text)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if "DATE" in name or name in {"SDATE", "INVDATE"}:
            return "2000-02-01"
        if name == "YEAR":
            return "2000"
        if name == "MONTH":
            return "2"
        return "1"

    text = re.sub(r"\[([A-Za-z_0-9]+)\]", replace, text)
    return re.sub(r"\[[^\]]*\]", "1", text).strip().rstrip(";")


def _tpcds_current(sql: str, src: str, tgt: str):
    def produce() -> str:
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        return TPCDSBenchmark.translate_query_text(SimpleNamespace(), sql, src, tgt)

    return produce


def _tpcds_fork(sql: str, src: str, tgt: str):
    def produce() -> str:
        import benchbox._vendor.sqlglot as sqlglot

        read, write = resolve_sql_dialect(src), resolve_sql_dialect(tgt)
        expression = sqlglot.parse_one(sql, read=read)
        if tgt == "postgres":
            expression = apply_rewrite(
                "date_integer_arithmetic_to_interval", expression, write, date_columns=_tpcds_date_columns()
            )
        expression = apply_rewrite("qualify_order_by_columns", expression, write, qualifiers=QUERY58_QUALIFIERS)
        return expression.sql(
            dialect=write,
            identify=write not in NO_IDENTIFY_DIALECTS,
            unsupported_level=ErrorLevel.RAISE,
        )

    return produce


TPCDS_SNIPPETS = [
    ("date-plus-5-gt", "SELECT * FROM t WHERE d_date + 5 > NOW()"),
    ("qualified-date-plus-30", "SELECT * FROM t WHERE d1.d_date + 30 > d2.d_date"),
    ("date-minus-7", "SELECT * FROM t WHERE d_date - 7 < NOW()"),
    ("non-date-column", "SELECT * FROM t WHERE order_id + 5 > 100"),
    ("q72-pattern", "SELECT * FROM a, b d1, b d3 WHERE d3.d_date > d1.d_date + 5"),
    ("multiple-date-arithmetic", "SELECT * FROM t WHERE d_date + 5 > NOW() AND d_date - 10 < NOW()"),
    ("preserves-other-arithmetic", "SELECT price * 2 + tax FROM t WHERE d_date + 5 > NOW()"),
    ("date-sk-plus-integer", "SELECT d_date_sk + 1 FROM date_dim"),
    ("date-sk-minus-integer", "SELECT d_date_sk - 7 FROM date_dim WHERE d_date_sk - 7 > 0"),
    ("sold-date-sk-arithmetic", "SELECT ss_sold_date_sk + 30 FROM store_sales"),
    ("returned-date-sk-diff", "SELECT sr_returned_date_sk - ss_sold_date_sk FROM store_returns, store_sales"),
    ("date-plus-non-literal", "SELECT d_date + n FROM date_dim"),
    ("date-plus-expression", "SELECT d_date + 5 * 2 FROM date_dim"),
    ("date-plus-decimal", "SELECT d_date + 5.5 FROM date_dim"),
    ("date-plus-interval-already", "SELECT d_date + INTERVAL '5' DAY FROM date_dim"),
    (
        "cast-date-plus-30-days",
        "SELECT * FROM t WHERE d_date BETWEEN CAST('2000-02-01' AS DATE) AND (CAST('2000-02-01' AS DATE) + 30 days)",
    ),
    (
        "cast-date-minus-30-days",
        "SELECT * FROM t WHERE d_date BETWEEN (CAST ('2000-02-01' AS DATE) - 30 days) AND CAST('2000-02-01' AS DATE)",
    ),
    (
        "date-column-in-select-and-where",
        "SELECT d_date + 1 AS nd FROM date_dim WHERE d_date - 1 >= CAST('2000-01-01' AS DATE)",
    ),
    ("date-column-in-string", "SELECT 'd_date + 5' AS note FROM date_dim"),
    ("date-column-uppercase", "SELECT D_DATE + 5 FROM date_dim"),
    ("date-column-case-when", "SELECT CASE WHEN d_date + 5 > d_date THEN 1 ELSE 0 END FROM date_dim"),
]


def _tpcds_case(case_id: str, sql: str, tgt: str) -> Case:
    return WrapperCase(
        case_id,
        frozen_legacy(AREA, case_id),
        _tpcds_fork(sql, "netezza", tgt),
        _tpcds_current(sql, "netezza", tgt),
    )


def _tpcds_cases() -> list[Case]:
    cases: list[Case] = []
    for case_id, sql in TPCDS_SNIPPETS:
        for tgt in ("postgres", "duckdb"):
            cases.append(_tpcds_case(f"tpcds-{case_id}-netezza-{tgt}", sql, tgt))
    for number in (12, 16, 20, 21, 32, 37, 40, 72, 77, 80, 82, 92, 94, 95, 98, 99, 62, 50, 58, 5):
        sql = _tpcds_template(number)
        for tgt in ("postgres", "duckdb"):
            cases.append(_tpcds_case(f"tpcds-template-q{number}-netezza-{tgt}", sql, tgt))
    return cases


def _baseline_cases() -> list[Case]:
    cases = []
    for entry in json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]:
        if entry["target"] == "questdb":
            continue
        source, target = entry["source"], entry["target"]
        cases.append(_case(f"baseline-{entry['id']}", entry["sql"], source, target, entry["identify"]))
    return cases


def cases() -> list[Case]:
    result: list[Case] = []
    for case_id, sql in SQLITE_DISCOUNT:
        for src in ("netezza", "postgres"):
            result.append(_case(f"sqlite-discount-{case_id}-{src}", sql, src, "sqlite"))
    for case_id, sql in SQLITE_SYNTAX:
        for src in ("netezza", "postgres"):
            result.append(_case(f"sqlite-syntax-{case_id}-{src}", sql, src, "sqlite"))
    for case_id, sql in SQLITE_CLICKHOUSE:
        result.append(_case(f"sqlite-{case_id}", sql, "clickhouse", "sqlite"))
    for case_id, sql in SQLITE_SYNTAX[:3] + SQLITE_DISCOUNT[:1]:
        result.append(_case(f"sqlite-noidentify-{case_id}", sql, "postgres", "sqlite", identify=False))
    for case_id, sql in DUCKDB_ALL:
        for src in ("netezza", "postgres"):
            result.append(_case(f"duckdb-all-{case_id}-{src}", sql, src, "duckdb"))
    for case_id, sql in DIALECT_QUERIES:
        for name in DIALECT_NAMES:
            result.append(_case(f"normalize-source-{name}-{case_id}", sql, name, "duckdb"))
            result.append(_case(f"normalize-target-{name}-{case_id}", sql, "postgres", name))
    result.extend(_baseline_cases())
    result.extend(_tpcds_cases())
    return result
