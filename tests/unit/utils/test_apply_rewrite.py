import pytest

from benchbox._vendor import sqlglot
from benchbox.utils.dialect_utils import apply_rewrite

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
