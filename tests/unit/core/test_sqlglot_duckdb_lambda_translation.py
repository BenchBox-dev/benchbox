import duckdb
import pytest

from benchbox._vendor import sqlglot

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


@pytest.fixture(scope="module")
def connection():
    with duckdb.connect() as conn:
        for statement in SETUP:
            conn.execute(statement)
        yield conn


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_generated_duckdb_lambda_sql_runs_with_the_same_results(connection, name):
    source = QUERIES[name]
    [translated] = sqlglot.transpile(source, read="duckdb", write="duckdb")

    assert "LAMBDA " in translated
    assert connection.execute(translated).fetchall() == connection.execute(source).fetchall()
