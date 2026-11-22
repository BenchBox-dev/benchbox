from __future__ import annotations

import duckdb
import pytest

from benchbox.core.query_catalog import _benchmark_instance

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CLICKBENCH_Q43_TAIL = (
    " AS M, {count} AS PageViews FROM hits WHERE CounterID = 62 AND EventDate >= '2013-07-14' "
    "AND EventDate <= '2013-07-15' AND IsRefresh = 0 AND DontCountHits = 0 GROUP BY {minute} ORDER BY {order}"
)

EXPECTED_CLICKBENCH_Q43 = {
    "tsql": "SELECT DATETRUNC(MINUTE, EventTime)"
    + CLICKBENCH_Q43_TAIL.format(
        count="COUNT_BIG(*)",
        minute="DATETRUNC(MINUTE, EventTime)",
        order="CASE WHEN DATETRUNC(MINUTE, EventTime) IS NULL THEN 1 ELSE 0 END, DATETRUNC(MINUTE, EventTime)",
    ),
    "oracle": "SELECT TRUNC(EventTime, 'MI')"
    + CLICKBENCH_Q43_TAIL.format(count="COUNT(*)", minute="TRUNC(EventTime, 'MI')", order="TRUNC(EventTime, 'MI')"),
    "teradata": "SELECT TRUNC(EventTime, 'MI')"
    + CLICKBENCH_Q43_TAIL.format(
        count="COUNT(*)", minute="TRUNC(EventTime, 'MI')", order="TRUNC(EventTime, 'MI') NULLS LAST"
    ),
}

EXPECTED_ORACLE_READ_PRIMITIVES = {
    "approx_count_distinct_simple": (
        'SELECT APPROX_COUNT_DISTINCT("o_custkey") AS "unique_customers" FROM "orders" '
        "WHERE \"o_orderdate\" >= CAST('1995-01-01' AS DATE)"
    ),
    "approx_count_distinct_groupby": (
        'SELECT "l_returnflag", "l_linestatus", APPROX_COUNT_DISTINCT("l_orderkey") AS "unique_orders", '
        'APPROX_COUNT_DISTINCT("l_partkey") AS "unique_parts" FROM "lineitem" GROUP BY "l_returnflag", "l_linestatus"'
    ),
    "statistical_variance_stddev": (
        'SELECT "o_orderpriority", COUNT(*) AS "order_count", AVG("o_totalprice") AS "avg_price", '
        'VAR_SAMP("o_totalprice") AS "price_variance", STDDEV("o_totalprice") AS "price_stddev", '
        'STDDEV_POP("o_totalprice") AS "price_stddev_pop", STDDEV_SAMP("o_totalprice") AS "price_stddev_samp" '
        'FROM "orders" WHERE "o_orderdate" >= CAST(\'1995-01-01\' AS DATE) GROUP BY "o_orderpriority"'
    ),
}


@pytest.fixture(scope="module")
def clickbench():
    return _benchmark_instance("clickbench")


@pytest.fixture(scope="module")
def read_primitives():
    return _benchmark_instance("read_primitives")


@pytest.fixture(scope="module")
def tpchavoc():
    return _benchmark_instance("tpchavoc")


@pytest.mark.parametrize("dialect", sorted(EXPECTED_CLICKBENCH_Q43))
def test_clickbench_q43_minute_truncation(clickbench, dialect):
    assert clickbench.get_queries(dialect=dialect)["Q43"] == EXPECTED_CLICKBENCH_Q43[dialect]


@pytest.mark.parametrize("query_id", sorted(EXPECTED_ORACLE_READ_PRIMITIVES))
def test_oracle_read_primitives_translation(read_primitives, query_id):
    assert read_primitives.get_queries(dialect="oracle")[query_id] == EXPECTED_ORACLE_READ_PRIMITIVES[query_id]


@pytest.mark.parametrize("dialect", ["postgres", "datafusion"])
def test_tpchavoc_1_v7_count_uses_cardinality(tpchavoc, dialect):
    sql = tpchavoc.get_queries(dialect=dialect)["1_v7"]
    assert sql.endswith("CARDINALITY(quantities) AS count_order FROM aggregated ORDER BY l_returnflag, l_linestatus")
    assert "ARRAY_LENGTH" not in sql
    assert "GROUP BY l_returnflag, l_linestatus" in sql


def test_tpchavoc_1_v7_arrays_are_never_empty():
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE lineitem (l_returnflag VARCHAR, l_linestatus VARCHAR, l_quantity INTEGER)")
        connection.execute("INSERT INTO lineitem VALUES ('A', 'F', 1), ('A', 'F', NULL), ('N', 'O', 7)")
        rows = connection.execute(
            "SELECT l_returnflag, LEN(ARRAY_AGG(l_quantity)), LENGTH(LIST(l_quantity)) "
            "FROM lineitem GROUP BY l_returnflag, l_linestatus ORDER BY l_returnflag"
        ).fetchall()
        empty = connection.execute(
            "SELECT LEN(ARRAY_AGG(l_quantity)) FROM lineitem WHERE FALSE GROUP BY l_returnflag"
        ).fetchall()
    assert rows == [("A", 2, 2), ("N", 1, 1)]
    assert empty == []
