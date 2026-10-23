from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

from benchbox.utils.dialect_utils import apply_rewrite, unqualified_table_names
from tests.unit.sql_compat.golden_corpus import Case, frozen_legacy

AREA = "area_adapters"

BIGQUERY_DIVISION = [
    "SELECT a / b FROM `T`",
    "SELECT a FROM `T` WHERE b = 1",
]

SNOWFLAKE_DIVISION = [
    "SELECT a / b FROM t",
    "SELECT a FROM t",
    (
        "SELECT CAST(amc AS DECIMAL(15, 4)) / CAST(pmc AS DECIMAL(15, 4)) AS am_pm_ratio "
        "FROM (SELECT COUNT(*) AS amc FROM web_sales, time_dim "
        "WHERE ws_sold_time_sk = time_dim.t_time_sk) AS at, "
        "(SELECT COUNT(*) AS pmc FROM web_sales) AS pm"
    ),
]

DATABRICKS_DIVISION = [
    "SELECT x / y FROM t",
    "SELECT a, b FROM t WHERE c = 1",
]

CLICKHOUSE_SAFE_DIVISION = [
    "SELECT revenue / ((store_sales + web_sales) / 2) FROM metrics",
    "SELECT total_sales / 12 FROM metrics",
    "SELECT total / COUNT(*) FROM t",
    "SELECT revenue / SUM(quantity) FROM t",
    "SELECT a / NULLIF(b, 0) FROM t",
    "SELECT a / b, c / d FROM t",
    "SELECT 'a/b' AS label, x / y FROM t",
    "SELECT mkt / SUM(volume) OVER (PARTITION BY o_year) FROM t",
    "SELECT a / b FROM t",
]

CLICKHOUSE_DECIMAL_DIVISION = [
    "SELECT a / CAST(b AS Nullable(Decimal(15, 4))) FROM t",
    "SELECT a / CAST(b AS Decimal(15, 4)) FROM t",
    "SELECT x / CAST(y AS Nullable(Decimal(18, 2))) FROM t",
]

CLICKHOUSE_DECIMAL_LITERALS = [
    "SELECT col + 0.06",
    "SELECT col - 0.01",
    "SELECT col * 0.05",
    "SELECT col / 0.07",
    "SELECT col + 0.0",
    "SELECT col - 0.00",
    "SELECT CASE WHEN x THEN 0.06 END",
    "SELECT CASE WHEN x THEN 0.05 END",
    "SELECT CASE WHEN x THEN 0.07 END",
    "SELECT CASE WHEN x THEN 0.0 END",
    "SELECT CASE WHEN x THEN 1 ELSE 0.06 END",
    "SELECT CASE WHEN x THEN 1 ELSE 0.07 END",
    "SELECT CASE WHEN x THEN 1 ELSE 0.01 END",
    "SELECT CASE WHEN x THEN 1 ELSE 0.0 END",
    "SELECT COALESCE(col, 0.0)",
    "SELECT COALESCE(col, 0.00)",
    "SELECT COALESCE(col, 0.5)",
    "SELECT * FROM t WHERE l_discount between 0.06 - 0.01 and 0.06 + 0.01",
    (
        "SELECT SUM(l_extendedprice * l_discount) AS revenue "
        "FROM lineitem "
        "WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
    ),
]

CLICKHOUSE_CASE_INPUTS = [
    "SELECT SUM(SR_FEE) AS ctr_total_return FROM store_returns",
    *(
        f"SELECT SUM({column}) FROM store_returns"
        for column in (
            "SR_RETURN_AMT",
            "SR_FEE",
            "SR_REFUNDED_CASH",
            "SR_RETURN_AMT_INC_TAX",
            "SR_REVERSED_CHARGE",
            "SR_STORE_CREDIT",
            "SR_RETURN_TAX",
        )
    ),
    "SELECT D_YEAR, D_MOY, SS_SOLD_DATE_SK, SR_STORE_SK, SR_CUSTOMER_SK, SR_RETURNED_DATE_SK, SS_QUANTITY_QTY FROM t",
    "SELECT 'SR_FEE' AS label, SR_FEE FROM t",
    "SELECT sr_fee FROM store_returns",
]

CLICKHOUSE_LOWERCASE_PATTERNS = [
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

CLICKHOUSE_SETTINGS = [
    ("CREATE TABLE foo (id Int32) ENGINE = MergeTree() ORDER BY id", ()),
    ("SELECT 1", ()),
    ("SELECT 1", (("join_algorithm", "grace_hash"), ("grace_hash_join_initial_buckets", 8))),
    ("SELECT count(*) FROM store_sales", ()),
    (
        "WITH v1 AS (SELECT d_year, SUM(ss_sales_price) AS sum_sales, "
        "AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_category, d_year) AS avg_sales "
        "FROM store_sales GROUP BY d_year, i_category) SELECT * FROM v1",
        (),
    ),
    ("SELECT a FROM t UNION ALL SELECT b FROM u", ()),
    ("SELECT a FROM t SETTINGS join_algorithm = 'hash'", (("join_algorithm", "grace_hash"),)),
]

DATABRICKS_DEDUPE = [
    "SELECT a.syear, b.syear, a.cnt FROM t AS a, t AS b",
    "SELECT a, b FROM t WHERE c = 1",
    (
        "SELECT a.syear AS syear, a.cnt AS cnt, b.syear AS syear, b.cnt AS cnt "
        "FROM (SELECT syear, COUNT(*) AS cnt FROM t GROUP BY syear) a "
        "JOIN (SELECT syear, COUNT(*) AS cnt FROM t GROUP BY syear) b ON a.syear = b.syear"
    ),
    "SELECT a, b, COUNT(*) AS cnt FROM t GROUP BY a, b",
    "SELECT x, x FROM t",
    "SELECT x AS v, y AS V, z AS v FROM t",
    "SELECT a.x, b.x FROM t a JOIN t b ON a.k = b.k ORDER BY x",
    "SELECT a.x, b.x FROM t a JOIN t b ON a.k = b.k ORDER BY a.x",
    "SELECT a.x, b.x FROM t a JOIN t b ON a.k = b.k GROUP BY x",
    "SELECT *, x FROM t",
    "SELECT t.*, x AS x FROM t",
    "SELECT x, COUNT(*) AS x FROM (SELECT a.v AS x, b.v AS x FROM a, b) s GROUP BY 1",
    "SELECT 1 AS n, 2 AS n UNION ALL SELECT 3 AS n, 4 AS n",
    "WITH c AS (SELECT a.k, b.k FROM a, b) SELECT * FROM c",
    "SELECT k, 1 AS k FROM t",
    "SELECT `a b`, `a b` FROM t",
    "INSERT INTO t SELECT a.k, b.k FROM a, b",
]

DATABRICKS_NORMALIZE = [
    "SELECT a.syear, b.syear, a.cnt FROM t AS a, t AS b",
    "SELECT a, b FROM t WHERE c = 1",
    "SELECT x / y FROM t",
    "SELECT a.x / b.x, a.x, b.x FROM t a, t b",
]

DATABRICKS_PK_DDL = [
    "CREATE TABLE t (id INT, PRIMARY KEY (id NULLS LAST))",
    "CREATE TABLE t (id INT, PRIMARY KEY (id NULLS FIRST))",
    "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a NULLS LAST, b NULLS FIRST))",
    "CREATE TABLE t (id INT, PRIMARY KEY (id))",
    "CREATE TABLE t (id INT)",
    "CREATE TABLE t (id INT, name VARCHAR(100))",
    "CREATE TABLE t (id INT) ORDER BY id NULLS LAST",
]

BIGQUERY_QUALIFY = [
    (
        "WITH first_cte AS (SELECT a FROM base_one), "
        "second_cte AS (SELECT f.a FROM orders o JOIN first_cte f ON o.a = f.a) "
        "SELECT * FROM second_cte"
    ),
    "SELECT SUM(`l_quantity`) FROM `lineitem`, `part` WHERE `p_partkey` = `lineitem`.`l_partkey`",
    "WITH orders AS (SELECT * FROM orders WHERE a > 1) SELECT * FROM orders",
    "SELECT * FROM customer JOIN orders ON customer.c_custkey = orders.o_custkey",
    (
        "MERGE INTO merge_ops_target AS target "
        "USING (SELECT * FROM orders WHERE o_orderkey BETWEEN 1 AND 10) AS source "
        "ON target.o_orderkey = source.o_orderkey "
        "WHEN NOT MATCHED THEN INSERT VALUES (source.o_orderkey)"
    ),
    (
        "BEGIN TRANSACTION;\n"
        "CREATE TEMP TABLE IF NOT EXISTS temp_orders_1 AS SELECT * FROM orders WHERE 1=0;\n"
        "INSERT INTO temp_orders_1 VALUES (1);\n"
        "INSERT INTO txn_orders SELECT * FROM temp_orders_1;\n"
        "DROP TABLE IF EXISTS temp_orders_1;\n"
        "COMMIT;\n"
    ),
    *(
        f"SELECT * FROM {table}"
        for table in ("REGION", "NATION", "CUSTOMER", "SUPPLIER", "PART", "PARTSUPP", "ORDERS", "LINEITEM")
    ),
    "SELECT * FROM customer",
    "SELECT * FROM lineitem l JOIN orders o ON l.l_orderkey = o.o_orderkey",
    "SELECT * FROM my_custom_table",
    "SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name",
    "SELECT table_name FROM INFORMATION_SCHEMA.COLUMNS",
    "INSERT INTO insert_ops_lineitem SELECT * FROM lineitem",
    "UPDATE update_ops_orders SET o_comment = 'x' WHERE 1=1",
    "DROP TABLE IF EXISTS txn_orders",
    "SELECT COUNT(*) FROM `ORDERS`",
    "SELECT COUNT(*) FROM `orders`",
    "INSERT INTO `update_ops_orders` SELECT * FROM `orders`",
    "SELECT `o_orderkey`, `o_custkey` FROM `orders`",
    "SELECT count(*) AS total, customer FROM lineitem JOIN customer ON lineitem.l_custkey = customer.c_custkey",
    "SELECT coalesce(c_name, customer) FROM customer",
    "INSERT INTO customer (c_custkey, customer) VALUES (1, 'v')",
    "SELECT customer.c_name FROM customer JOIN lineitem ON COALESCE(lineitem.x, customer) IS NOT NULL",
    "SELECT orders.o_orderkey FROM orders `o`",
    "SELECT orders.o_orderkey FROM orders `order` WHERE 1=1",
    "CREATE VIEW orders_view AS SELECT * FROM orders",
    "DROP VIEW IF EXISTS orders_view",
    "CREATE MATERIALIZED VIEW mv AS SELECT * FROM orders",
    "DROP MATERIALIZED VIEW IF EXISTS mv",
    "SELECT * FROM (customer JOIN orders ON customer.c_custkey = 1) JOIN nation ON 1=1",
    "SELECT * FROM (SELECT 1 FROM lineitem) sub, customer WHERE 1=1",
    "SELECT * FROM customer JOIN orders ON customer.c_custkey = 1, nation WHERE 1=1",
    "DELETE FROM customer USING orders, lineitem WHERE 1=1",
    "SELECT a, b FROM customer, orders",
    "SELECT * FROM `other.ds.orders`",
    "SELECT * FROM ds2.orders",
    "SELECT * FROM orders WHERE o_orderkey IN (SELECT l_orderkey FROM lineitem)",
]

BIGQUERY_EXTRACT = [BIGQUERY_QUALIFY[0], BIGQUERY_QUALIFY[2], BIGQUERY_QUALIFY[3], BIGQUERY_QUALIFY[4]]

TPCH_DIVISION_QUERIES = (8, 14, 17, 19, 22)


def _tpch(number: int) -> str:
    from benchbox.core.tpch.queries import TPCHQueries

    return TPCHQueries().get_query(number)


def _bigquery_adapter():
    from benchbox.platforms.bigquery import BigQueryAdapter

    return BigQueryAdapter(project_id="proj", dataset_id="ds")


def _bigquery_legacy(method: str, *args) -> Callable[[], str]:
    def run() -> str:
        with (
            patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.bigquery.bigquery"),
        ):
            return str(getattr(_bigquery_adapter(), method)(*args))

    return run


def _databricks_adapter():
    from benchbox.platforms.databricks import DatabricksAdapter

    with (
        patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.databricks.adapter.databricks_sql"),
    ):
        return DatabricksAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/abc123",
            access_token="tok",
        )


def _databricks_adapter_call(method: str, sql: str) -> Callable[[], str]:
    def run() -> str:
        return getattr(_databricks_adapter(), method)(sql)

    return run


def _transformer():
    from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

    return ClickHouseQueryTransformer()


def _clickhouse_legacy(method: str, sql: str, *args) -> Callable[[], str]:
    def run() -> str:
        return getattr(_transformer(), method)(sql, *args)

    return run


def _fork_rewrite(name: str, sql: str, dialect: str, **args) -> Callable[[], str]:
    def run() -> str:
        return apply_rewrite(name, sql, dialect, **args)

    return run


def _clickhouse_pipeline(sql: str) -> str:
    from benchbox._vendor import sqlglot

    expression = sqlglot.parse_one(sql, read="clickhouse")
    expression = apply_rewrite(
        "lowercase_identifiers", expression, "clickhouse", patterns=CLICKHOUSE_LOWERCASE_PATTERNS
    )
    expression = apply_rewrite("decimal_zero_literals", expression, "clickhouse", precision=15, scale=2)
    expression = apply_rewrite("safe_division", expression, "clickhouse")
    return expression.sql(dialect="clickhouse")


def _clickhouse_settings_fork(sql: str, extra: tuple) -> Callable[[], str]:
    def run() -> str:
        from benchbox._vendor import sqlglot

        settings = {"joined_subquery_requires_alias": 0, **dict(extra)}
        expression = sqlglot.parse_one(sql, read="clickhouse")
        return apply_rewrite("add_query_settings", expression, "clickhouse", settings=settings).sql(
            dialect="clickhouse"
        )

    return run


def _databricks_ddl_fork(sql: str) -> Callable[[], str]:
    def run() -> str:
        from benchbox._vendor import sqlglot

        return sqlglot.transpile(sql, read="databricks", write="databricks")[0]

    return run


def _unqualified_tables(expression):
    import importlib

    return importlib.import_module("benchbox._vendor.sqlglot.benchbox.rewrites").unqualified_tables(expression)


def _bigquery_qualify_fork(sql: str) -> Callable[[], str]:
    def run() -> str:
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        parts = []
        for statement in sqlglot.parse(sql, read="bigquery"):
            if statement is None:
                continue
            for table in _unqualified_tables(statement):
                table.set("this", exp.to_identifier(table.name.upper(), quoted=True))
                table.set("db", exp.to_identifier("ds", quoted=True))
                table.set("catalog", exp.to_identifier("proj", quoted=True))
            parts.append(statement.sql(dialect="bigquery"))
        return ";\n".join(parts)

    return run


def _bigquery_extract_fork(sql: str) -> Callable[[], str]:
    def run() -> str:
        return str(unqualified_table_names(sql, "bigquery"))

    return run


def cases() -> list[Case]:
    found: list[Case] = []

    for index, sql in enumerate(BIGQUERY_DIVISION):
        found.append(
            Case(
                f"bigquery_division_{index}",
                frozen_legacy(AREA, f"bigquery_division_{index}"),
                _fork_rewrite("safe_division", sql, "bigquery"),
            )
        )
    for number in TPCH_DIVISION_QUERIES:
        sql = _tpch(number)
        found.append(
            Case(
                f"bigquery_division_tpch_q{number}",
                frozen_legacy(AREA, f"bigquery_division_tpch_q{number}"),
                _fork_rewrite("safe_division", sql, "bigquery"),
            )
        )
        found.append(
            Case(
                f"snowflake_division_tpch_q{number}",
                frozen_legacy(AREA, f"snowflake_division_tpch_q{number}"),
                _fork_rewrite("safe_division", sql, "snowflake"),
            )
        )
        found.append(
            Case(
                f"databricks_division_tpch_q{number}",
                frozen_legacy(AREA, f"databricks_division_tpch_q{number}"),
                _fork_rewrite("safe_division", sql, "databricks"),
            )
        )
        found.append(
            Case(
                f"clickhouse_division_tpch_q{number}",
                _clickhouse_legacy("safe_division", sql),
                _fork_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for index, sql in enumerate(SNOWFLAKE_DIVISION):
        found.append(
            Case(
                f"snowflake_division_{index}",
                frozen_legacy(AREA, f"snowflake_division_{index}"),
                _fork_rewrite("safe_division", sql, "snowflake"),
            )
        )

    for index, sql in enumerate(DATABRICKS_DIVISION):
        found.append(
            Case(
                f"databricks_division_{index}",
                frozen_legacy(AREA, f"databricks_division_{index}"),
                _fork_rewrite("safe_division", sql, "databricks"),
            )
        )

    for index, sql in enumerate(CLICKHOUSE_SAFE_DIVISION):
        found.append(
            Case(
                f"clickhouse_safe_division_{index}",
                _clickhouse_legacy("safe_division", sql),
                _fork_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for index, sql in enumerate(CLICKHOUSE_DECIMAL_DIVISION):
        found.append(
            Case(
                f"clickhouse_decimal_division_{index}",
                _clickhouse_legacy("fix_decimal_division_by_zero", sql),
                _fork_rewrite("safe_division", sql, "clickhouse"),
            )
        )

    for index, sql in enumerate(CLICKHOUSE_DECIMAL_LITERALS):
        found.append(
            Case(
                f"clickhouse_decimal_literal_{index}",
                _clickhouse_legacy("fix_type_casts", sql),
                _fork_rewrite("decimal_zero_literals", sql, "clickhouse", precision=15, scale=2),
            )
        )

    for index, sql in enumerate(CLICKHOUSE_CASE_INPUTS):
        found.append(
            Case(
                f"clickhouse_lowercase_{index}",
                _clickhouse_legacy("normalize_case", sql),
                _fork_rewrite("lowercase_identifiers", sql, "clickhouse", patterns=CLICKHOUSE_LOWERCASE_PATTERNS),
            )
        )

    pipeline_inputs = [
        "SELECT revenue / ((store_sales + web_sales) / 2) FROM metrics",
        "SELECT COALESCE(SR_FEE, 0.0) / SR_RETURN_AMT FROM store_returns",
        CLICKHOUSE_DECIMAL_LITERALS[-1],
        *(_tpch(number) for number in range(1, 23)),
    ]
    for index, sql in enumerate(pipeline_inputs):
        found.append(
            Case(
                f"clickhouse_transform_{index}",
                _clickhouse_legacy("transform", sql),
                lambda sql=sql: _clickhouse_pipeline(sql),
            )
        )

    for index, (sql, extra) in enumerate(CLICKHOUSE_SETTINGS):
        found.append(
            Case(
                f"clickhouse_settings_{index}",
                _clickhouse_legacy("add_query_settings", sql, extra),
                _clickhouse_settings_fork(sql, extra),
            )
        )

    for index, sql in enumerate(DATABRICKS_DEDUPE):
        found.append(
            Case(
                f"databricks_dedupe_{index}",
                frozen_legacy(AREA, f"databricks_dedupe_{index}"),
                _fork_rewrite("deduplicate_output_aliases", sql, "databricks", nested=False),
            )
        )

    for index, sql in enumerate(DATABRICKS_NORMALIZE):
        found.append(
            Case(
                f"databricks_normalize_{index}",
                frozen_legacy(AREA, f"databricks_normalize_{index}"),
                _databricks_adapter_call("_normalize_databricks_query", sql),
            )
        )

    for index, sql in enumerate(DATABRICKS_PK_DDL):
        found.append(
            Case(
                f"databricks_pk_ddl_{index}",
                frozen_legacy(AREA, f"databricks_pk_ddl_{index}"),
                _databricks_ddl_fork(sql),
            )
        )

    for index, sql in enumerate(BIGQUERY_QUALIFY):
        found.append(
            Case(
                f"bigquery_qualify_{index}",
                _bigquery_legacy("_qualify_table_names", sql),
                _bigquery_qualify_fork(sql),
            )
        )

    for index, sql in enumerate(BIGQUERY_EXTRACT):
        found.append(
            Case(
                f"bigquery_extract_{index}",
                frozen_legacy(AREA, f"bigquery_extract_{index}"),
                _bigquery_extract_fork(sql),
            )
        )

    return found
