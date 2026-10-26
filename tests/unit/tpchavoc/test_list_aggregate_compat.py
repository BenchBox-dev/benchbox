from __future__ import annotations

import os
import re

import duckdb
import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST, get_platform_sql_dialect
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dialect_compat import LIST_AGGREGATE_DIALECTS, LIST_AGGREGATE_VARIANT_IDS
from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.list_aggregate_tpchavoc import LIST_AGGREGATE_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

pytestmark = [pytest.mark.unit, pytest.mark.medium]

LINEITEM_DDL = (
    "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_returnflag VARCHAR, l_linestatus VARCHAR, "
    "l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), l_discount DECIMAL(15,2), l_tax DECIMAL(15,2), "
    "l_shipdate DATE)"
)
LINEITEM_ROWS = [
    (1, 1, "A", "F", 17.0, 21168.23, 0.04, 0.02, "1996-03-13"),
    (1, 2, "A", "F", 36.0, 45983.16, 0.09, 0.06, "1996-04-12"),
    (2, 1, "A", "F", 8.0, 13309.6, 0.10, 0.02, "1997-01-28"),
    (3, 1, "N", "O", 45.0, 54058.05, 0.06, 0.0, "1997-11-30"),
    (3, 2, "N", "O", 49.0, 46796.47, 0.10, 0.0, "1998-01-02"),
    (4, 1, "N", "O", 30.0, 41544.1, 0.03, 0.08, "1998-08-19"),
    (5, 1, "R", "F", 3.0, 4400.0, 0.05, 0.04, "1995-06-01"),
    (6, 1, "R", "F", 9.0, 9000.0, 0.07, 0.01, "1998-12-01"),
]
LIST_FUNCTION = re.compile(r"\bLIST(_\w+)?\(", re.IGNORECASE)


def _duckdb_rows(sql: str) -> list[tuple]:
    with duckdb.connect(":memory:") as connection:
        connection.execute(LINEITEM_DDL)
        connection.executemany("INSERT INTO lineitem VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", LINEITEM_ROWS)
        return connection.execute(sql).fetchall()


def _assert_same_rows(actual: list[tuple], expected: list[tuple]) -> None:
    assert [row[:2] for row in actual] == [row[:2] for row in expected] == [("A", "F"), ("N", "O"), ("R", "F")]
    for got_row, want_row in zip(actual, expected, strict=True):
        for got, want in zip(got_row[2:], want_row[2:], strict=True):
            assert float(got) == pytest.approx(float(want), rel=1e-9)


def test_postgres_and_datafusion_are_list_aggregate_dialects():
    assert {"postgres", "datafusion"} <= LIST_AGGREGATE_DIALECTS
    assert {"1_v7"} == LIST_AGGREGATE_VARIANT_IDS
    assert "1_v7" not in DATAFUSION_TPCHAVOC_SKIPS
    assert "1_v7" not in POSTGRES_TPCHAVOC_SKIPS


@pytest.mark.parametrize("dialect", ["postgres", "postgresql", "datafusion", "influxdb", "timescaledb"])
def test_postgres_and_datafusion_translate_the_list_aggregates(dialect):
    sql = TPCHavocBenchmark(scale_factor=0.01).get_query("1_v7", dialect=dialect)

    assert not LIST_FUNCTION.search(sql)
    if dialect in {"datafusion", "influxdb"}:
        assert "UNNEST" not in sql.upper()
    else:
        assert "UNNEST(prices, discounts, taxes)" in sql


def test_get_queries_agrees_with_get_query_for_postgres_and_datafusion():
    benchmark = TPCHavocBenchmark(scale_factor=0.01)

    for dialect in ("postgres", "datafusion"):
        assert benchmark.get_queries(dialect=dialect)["1_v7"] == benchmark.get_query("1_v7", dialect=dialect)


def test_datafusion_list_aggregate_variant_matches_duckdb():
    datafusion = pytest.importorskip("datafusion")
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    expected = _duckdb_rows(benchmark.get_query("1_v7", dialect="duckdb"))

    context = datafusion.SessionContext()
    context.sql(
        "CREATE TABLE lineitem AS SELECT * FROM (VALUES "
        + ", ".join(
            f"({o}, {n}, '{f}', '{s}', CAST({q} AS DECIMAL(15,2)), CAST({p} AS DECIMAL(15,2)), "
            f"CAST({d} AS DECIMAL(15,2)), CAST({t} AS DECIMAL(15,2)), CAST('{ship}' AS DATE))"
            for o, n, f, s, q, p, d, t, ship in LINEITEM_ROWS
        )
        + ") AS v(l_orderkey, l_linenumber, l_returnflag, l_linestatus, l_quantity, l_extendedprice, "
        "l_discount, l_tax, l_shipdate)"
    ).collect()
    batches = context.sql(benchmark.get_query("1_v7", dialect="datafusion")).collect()
    actual = [row for batch in batches for row in zip(*(batch.column(i).to_pylist() for i in range(batch.num_columns)))]

    _assert_same_rows(actual, expected)


@pytest.mark.skipif(not os.environ.get("BENCHBOX_POSTGRES_DSN"), reason="BENCHBOX_POSTGRES_DSN is not set")
def test_postgres_list_aggregate_variant_matches_duckdb():
    psycopg = pytest.importorskip("psycopg")
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    expected = _duckdb_rows(benchmark.get_query("1_v7", dialect="duckdb"))

    with psycopg.connect(os.environ["BENCHBOX_POSTGRES_DSN"], autocommit=True) as connection:
        connection.execute(LINEITEM_DDL.replace("VARCHAR", "TEXT").replace("CREATE TABLE", "CREATE TEMP TABLE"))
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO lineitem VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", LINEITEM_ROWS)
            cursor.execute(benchmark.get_query("1_v7", dialect="postgres"))
            actual = cursor.fetchall()

    _assert_same_rows(actual, expected)


def test_every_sql_platform_translates_or_documents_the_list_aggregate_variant():
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    translated: dict[str, bool] = {}
    undocumented: list[str] = []
    for entry in PLATFORM_MANIFEST:
        platform = entry.key
        dialect = get_platform_sql_dialect(platform)
        if dialect is None or dialect == "duckdb":
            continue
        if dialect not in translated:
            translated[dialect] = not LIST_FUNCTION.search(benchmark.get_query("1_v7", dialect=dialect))
        if not translated[dialect] and "1_v7" not in benchmark.get_platform_skip_queries(platform):
            undocumented.append(platform)

    assert undocumented == []


def test_list_aggregate_skip_reasons_name_a_platform_without_a_translation():
    benchmark = TPCHavocBenchmark(scale_factor=0.01)

    for platform, reason in LIST_AGGREGATE_TPCHAVOC_SKIPS.items():
        dialect = get_platform_sql_dialect(platform)
        assert dialect not in LIST_AGGREGATE_DIALECTS
        assert reason
        assert benchmark.get_platform_skip_queries(platform) == ["1_v7"]
