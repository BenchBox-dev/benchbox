from decimal import Decimal

import duckdb
import pytest

from benchbox.utils.duckdb_lambda import (
    PythonLambdaConnection,
    duckdb_python_lambda_supported,
    duckdb_query_text,
    rewrite_arrow_lambdas,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SETUP = (
    "CREATE TABLE part AS SELECT * FROM (VALUES ('Brand#1', 10.5), ('Brand#1', 20.25), ('Brand#2', 7.0)) "
    "AS t(p_brand, p_retailprice)",
    "CREATE TABLE lineitem AS SELECT * FROM (VALUES (1, 100.0, 0.1), (1, 50.0, 0.0), (2, 80.0, 0.25)) "
    "AS t(l_orderkey, l_extendedprice, l_discount)",
)

QUERIES = {
    "list_transform": (
        "WITH part_prices AS (SELECT p_brand, ARRAY_AGG(p_retailprice) AS prices FROM part GROUP BY p_brand) "
        "SELECT p_brand, LIST_TRANSFORM(prices, x -> x * 1.1) AS prices_with_tax FROM part_prices ORDER BY p_brand"
    ),
    "list_filter": (
        "WITH part_prices AS (SELECT p_brand, ARRAY_AGG(p_retailprice) AS prices FROM part GROUP BY p_brand) "
        "SELECT p_brand, LIST_FILTER(prices, x -> x > 10) AS expensive FROM part_prices ORDER BY p_brand"
    ),
    "havoc_zip_discount": (
        "SELECT l_orderkey, LIST_SUM(LIST_TRANSFORM(LIST_ZIP(ARRAY_AGG(l_extendedprice), ARRAY_AGG(l_discount)), "
        "x -> x[1] * (1 - x[2]))) AS disc FROM lineitem GROUP BY l_orderkey ORDER BY l_orderkey"
    ),
}

NO_LAMBDA = (
    "SELECT 1",
    "SELECT doc -> 'key' FROM docs",
    "SELECT doc->'a'->'b' FROM docs",
    "SELECT count(doc -> 'k'), lower(doc -> '$.a'), coalesce(a, doc -> 'k') FROM docs",
    "SELECT json_extract_string(doc -> 'k', '$.x') FROM docs",
    "SELECT f(doc -> 0) FROM docs",
    "SELECT LIST_TRANSFORM(xs, lambda x: x + 1) FROM t",
)


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("0.9.2", False),
        ("1.0.0", False),
        ("1.2.2", False),
        ("1.3.0", True),
        ("1.5.5", True),
        ("2.0.0", True),
        ("v3.1.0", True),
        ("garbage", False),
    ],
)
def test_python_lambda_form_is_used_from_the_first_accepting_version(version, supported):
    assert duckdb_python_lambda_supported(version) is supported


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("SELECT list_transform(a, x -> x + 1)", "SELECT list_transform(a, lambda x: x + 1)"),
        ("SELECT list_transform(a,x->x+1)", "SELECT list_transform(a,lambda x:x+1)"),
        ("SELECT list_filter(a, (x, i) -> x > i)", "SELECT list_filter(a, lambda x, i: x > i)"),
        (
            "SELECT doc -> 'k', list_transform(a, x -> x[1])",
            "SELECT doc -> 'k', list_transform(a, lambda x: x[1])",
        ),
        (
            "SELECT list_filter(a, x -> list_sum(list_transform(x, y -> y -> 'k')) > 1)",
            "SELECT list_filter(a, lambda x: list_sum(list_transform(x, lambda y: y -> 'k')) > 1)",
        ),
        (
            "SELECT list_transform(a, x -> x + 1), f(g(y -> y)) -> 'k'",
            "SELECT list_transform(a, lambda x: x + 1), f(g(lambda y: y)) -> 'k'",
        ),
        (
            "SELECT list_transform(a, (x) -> x + 1), json_extract(f(a, b), '$') -> 'k'",
            "SELECT list_transform(a, lambda x: x + 1), json_extract(f(a, b), '$') -> 'k'",
        ),
    ],
)
def test_arrow_lambdas_become_python_lambdas_and_json_arrows_are_untouched(source, expected):
    assert rewrite_arrow_lambdas(source) == expected


@pytest.mark.parametrize("sql", NO_LAMBDA)
def test_sql_without_arrow_lambdas_is_returned_unchanged(sql):
    assert rewrite_arrow_lambdas(sql) == sql


def test_unparseable_sql_is_returned_unchanged():
    sql = "SELECT list_transform(a, x -> ) FROM ("
    assert rewrite_arrow_lambdas(sql) == sql


def test_older_runtimes_keep_the_arrow_form():
    sql = QUERIES["list_filter"]
    assert duckdb_query_text(sql, "1.2.2") == sql
    assert "lambda x:" in duckdb_query_text(sql, "1.3.0")


def _plain(value):
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return round(float(value), 6)
    return value


@pytest.fixture(scope="module")
def connection():
    with duckdb.connect() as conn:
        for statement in SETUP:
            conn.execute(statement)
        yield conn


@pytest.mark.skipif(not duckdb_python_lambda_supported(), reason="installed DuckDB predates the lambda keyword form")
@pytest.mark.parametrize("name", sorted(QUERIES))
def test_rewritten_statements_run_with_default_settings_and_the_same_results(connection, name):
    source = QUERIES[name]
    rewritten = rewrite_arrow_lambdas(source)

    assert "lambda " in rewritten
    expected = {
        "list_transform": [["Brand#1", [11.55, 22.275]], ["Brand#2", [7.7]]],
        "list_filter": [["Brand#1", [10.5, 20.25]], ["Brand#2", []]],
        "havoc_zip_discount": [[1, 140.0], [2, 60.0]],
    }[name]
    assert _plain(connection.execute(rewritten).fetchall()) == expected


@pytest.mark.skipif(not duckdb_python_lambda_supported(), reason="installed DuckDB predates the lambda keyword form")
def test_connection_wrapper_rewrites_execute_and_sql_and_delegates_the_rest(connection):
    wrapped = PythonLambdaConnection(connection, duckdb.__version__)
    source = QUERIES["list_filter"]

    assert wrapped.execute(source).fetchall() == connection.execute(rewrite_arrow_lambdas(source)).fetchall()
    assert wrapped.sql(source).fetchall() == connection.execute(rewrite_arrow_lambdas(source)).fetchall()
    assert wrapped.execute("SELECT ?", [3]).fetchall() == [(3,)]
    assert wrapped.description is not None
