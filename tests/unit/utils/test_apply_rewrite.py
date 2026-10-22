import pytest

from benchbox._vendor import sqlglot
from benchbox.utils.dialect_utils import FORK_REWRITE_MODULES, _fork_rewrite, apply_rewrite

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DISCOUNT_BOUNDS = "SELECT 1 FROM {table} WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"


def test_discount_bound_fold_applies_to_the_lineitem_gate():
    result = apply_rewrite(
        "fold_between_literal_arithmetic",
        DISCOUNT_BOUNDS.format(table="lineitem"),
        "sqlite",
        columns=["l_discount"],
        tables=["lineitem"],
    )
    assert result == "SELECT 1 FROM lineitem WHERE l_discount BETWEEN 0.05 AND 0.07"


def test_discount_bound_fold_leaves_other_tables_alone():
    sql = DISCOUNT_BOUNDS.format(table="orders")
    result = apply_rewrite(
        "fold_between_literal_arithmetic", sql, "sqlite", columns=["l_discount"], tables=["lineitem"]
    )
    assert result == sql


def test_string_input_returns_sql_and_expression_input_returns_expression():
    sql = "SELECT a / b FROM t"
    assert apply_rewrite("safe_division", sql, "snowflake") == "SELECT a / NULLIF(b, 0) FROM t"
    expression = sqlglot.parse_one(sql, read="snowflake")
    rewritten = apply_rewrite("safe_division", expression, "snowflake")
    assert isinstance(rewritten, sqlglot.exp.Expr)
    assert rewritten.sql(dialect="snowflake") == "SELECT a / NULLIF(b, 0) FROM t"
    assert expression.sql(dialect="snowflake") == sql


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [("bigquery", "SELECT SAFE_DIVIDE(a, b) FROM t"), ("databricks", "SELECT TRY_DIVIDE(a, b) FROM t")],
)
def test_dialect_is_forwarded_to_rewrites_that_take_one(dialect: str, expected: str):
    assert apply_rewrite("safe_division", "SELECT a / b FROM t", dialect) == expected


def test_rewrite_without_a_dialect_parameter_receives_only_its_arguments():
    result = apply_rewrite("flag_comparisons_to_boolean", "SELECT 1 FROM t WHERE f = 1", "duckdb", columns=["f"])
    assert result == "SELECT 1 FROM t WHERE f = TRUE"


def test_unsupported_dialect_for_safe_division_raises():
    with pytest.raises(ValueError):
        apply_rewrite("safe_division", "SELECT a / b FROM t", "duckdb")


@pytest.mark.parametrize("name", ["not_a_rewrite", "_deduplicate_select", "unqualified_tables", "transpile_ddl", ""])
def test_only_public_expression_rewrites_resolve(name: str):
    with pytest.raises(ValueError, match="Unknown fork rewrite"):
        apply_rewrite(name, "SELECT 1", "duckdb")


HAVOC_REWRITES = (
    "inline_select_aliases",
    "qualify_columns_by_name",
    "add_row_source_to_fromless_selects",
    "filtered_aggregates_to_conditional",
    "drop_literal_row_source",
    "flatten_count_of_count",
    "name_literal_subquery_columns",
    "wrap_grouped_scalar_subqueries",
    "order_list_aggregates",
)
SUBQUERY_REWRITES = ("not_in_subquery_to_not_exists", "in_aggregate_subquery_to_exists", "decorrelate_subqueries")
ENGINE_REWRITES = (
    "lowercase_identifiers",
    "decimal_zero_literals",
    "add_query_settings",
    "correlated_exists_to_in",
    "add_derived_table_aliases",
    "qualify_order_by_columns",
)


@pytest.mark.parametrize("name", [*HAVOC_REWRITES, *SUBQUERY_REWRITES, *ENGINE_REWRITES])
def test_named_registry_resolves_every_moved_rewrite(name: str):
    assert callable(_fork_rewrite(name))


def test_mark_date_columns_resolves_from_the_registry():
    assert callable(_fork_rewrite("mark_date_columns"))


def test_registry_exposes_no_unlisted_rewrites():
    import importlib
    import inspect

    exposed = set()
    for module_name in FORK_REWRITE_MODULES:
        module = importlib.import_module(f"benchbox._vendor.sqlglot.benchbox.{module_name}")
        for name, function in vars(module).items():
            if (
                not name.startswith("_")
                and inspect.isfunction(function)
                and function.__module__ == module.__name__
                and "copy" in inspect.signature(function).parameters
            ):
                exposed.add(name)
    listed = {
        "fold_between_literal_arithmetic",
        "date_integer_arithmetic_to_interval",
        "safe_division",
        "deduplicate_output_aliases",
        "flag_comparisons_to_boolean",
        "mark_date_columns",
        *HAVOC_REWRITES,
        *SUBQUERY_REWRITES,
        *ENGINE_REWRITES,
    }
    assert exposed == listed


@pytest.mark.parametrize(
    ("name", "sql", "dialect", "args", "expected"),
    [
        (
            "flatten_count_of_count",
            "SELECT COUNT(*) AS source_records FROM (SELECT COUNT(*) AS c FROM t WHERE p > 1)",
            "postgres",
            {},
            "SELECT COUNT(*) AS source_records FROM t WHERE p > 1",
        ),
        (
            "add_row_source_to_fromless_selects",
            "SELECT 1 WHERE 1 = 1",
            "bigquery",
            {"source": "UNNEST([1])"},
            "SELECT 1 FROM UNNEST([1]) WHERE 1 = 1",
        ),
        (
            "deduplicate_output_aliases",
            "SELECT a, a FROM t",
            "databricks",
            {},
            "SELECT a, a AS a_2 FROM t",
        ),
        (
            "date_integer_arithmetic_to_interval",
            "SELECT d + 30 FROM t",
            "datafusion",
            {"date_columns": ["d"]},
            "SELECT d + INTERVAL '30 DAY' FROM t",
        ),
        (
            "inline_select_aliases",
            "SELECT a + 1 AS x FROM t WHERE x > 2 ORDER BY x",
            "postgres",
            {},
            "SELECT a + 1 AS x FROM t WHERE a + 1 > 2 ORDER BY x",
        ),
        (
            "qualify_columns_by_name",
            "SELECT c FROM t GROUP BY c HAVING c > 1",
            "postgres",
            {"tables": {"c": "t"}},
            "SELECT t.c FROM t GROUP BY t.c HAVING t.c > 1",
        ),
        (
            "filtered_aggregates_to_conditional",
            "SELECT SUM(x) FILTER (WHERE y) FROM t",
            "bigquery",
            {},
            "SELECT SUM(IF(y, x, NULL)) FROM t",
        ),
        (
            "drop_literal_row_source",
            "SELECT 1 AS c FROM (SELECT 1 AS dual) AS dual",
            "postgres",
            {"alias": "dual"},
            "SELECT 1 AS c",
        ),
        (
            "name_literal_subquery_columns",
            "SELECT * FROM (SELECT 1) AS d",
            "databricks",
            {"column": "dual_col"},
            "SELECT * FROM (SELECT 1 AS dual_col) AS d",
        ),
        (
            "order_list_aggregates",
            "SELECT ARRAY_AGG(x) FROM t",
            "presto",
            {"order_by": ["a", "b"]},
            "SELECT ARRAY_AGG(x ORDER BY a, b) FROM t",
        ),
        (
            "not_in_subquery_to_not_exists",
            "SELECT 1 FROM t WHERE a NOT IN (SELECT b FROM u)",
            "datafusion",
            {"non_null_columns": ["a", "b"]},
            "SELECT 1 FROM t WHERE NOT EXISTS(SELECT 1 FROM u WHERE b = a)",
        ),
        (
            "in_aggregate_subquery_to_exists",
            "SELECT 1 FROM t WHERE a IN (SELECT k FROM u GROUP BY k HAVING SUM(v) > 1)",
            "datafusion",
            {"alias": "t"},
            "SELECT 1 FROM t WHERE EXISTS(SELECT 1 FROM (SELECT k FROM u GROUP BY k HAVING SUM(v) > 1) AS t "
            "WHERE t.k = a)",
        ),
        (
            "decorrelate_subqueries",
            "SELECT a FROM t WHERE b > (SELECT AVG(y) FROM u WHERE u.k = t.k)",
            "datafusion",
            {"schema": {"t": {"a": "INT", "b": "INT", "k": "INT"}, "u": {"y": "INT", "k": "INT"}}},
            "SELECT t.a AS a FROM t AS t LEFT JOIN (SELECT AVG(u.y) AS _col_0, u.k AS _u_1 FROM u AS u WHERE TRUE "
            "GROUP BY u.k) AS _u_0 ON _u_0._u_1 = t.k WHERE t.b > _u_0._col_0",
        ),
    ],
)
def test_moved_rewrites_produce_their_documented_output(name: str, sql: str, dialect: str, args: dict, expected: str):
    assert apply_rewrite(name, sql, dialect, **args) == expected


def test_wrap_grouped_scalar_subqueries_takes_the_aggregate_class():
    result = apply_rewrite(
        "wrap_grouped_scalar_subqueries",
        "SELECT (SELECT 1) FROM t GROUP BY a",
        "databricks",
        aggregate=sqlglot.exp.First,
    )
    assert result == "SELECT FIRST((SELECT 1)) FROM t GROUP BY a"
