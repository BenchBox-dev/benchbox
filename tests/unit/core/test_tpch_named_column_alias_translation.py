import duckdb
import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.medium]

AFFECTED_DIALECTS = ["bigquery", "doris", "mysql", "sqlite"]


def _alias_column_lists(sql: str, dialect: str) -> list[str]:
    return [alias.sql() for alias in sqlglot.parse_one(sql, read=dialect).find_all(exp.TableAlias) if alias.columns]


@pytest.fixture(scope="module")
def sf001_connection(tmp_path_factory):
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("tpch_sf001"))
    paths = {path.stem: path for path in benchmark.generate_data()}

    def read(table):
        return f"read_csv('{paths[table]}', delim='|', header=false, all_varchar=true)"

    connection = duckdb.connect()
    connection.execute(f"create table customer as select cast(column0 as bigint) as c_custkey from {read('customer')}")
    connection.execute(
        "create table orders as select cast(column0 as bigint) as o_orderkey, "
        f"cast(column1 as bigint) as o_custkey, column8 as o_comment from {read('orders')}"
    )
    yield connection
    connection.close()


def _run_on_duckdb(connection, sql: str, dialect: str):
    return connection.execute(sqlglot.transpile(sql, read=dialect, write="duckdb")[0]).fetchall()


@pytest.fixture(scope="module")
def reference_q13_rows(sf001_connection):
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=None)
    original = benchmark.get_query(13, scale_factor=0.01, dialect="duckdb")
    assert _alias_column_lists(original, "duckdb")
    rows = sf001_connection.execute(original).fetchall()
    assert len(rows) > 10
    return rows


@pytest.mark.parametrize("dialect", AFFECTED_DIALECTS)
def test_q13_has_no_derived_table_column_alias_list(dialect):
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=None)
    translated = benchmark.get_query(13, scale_factor=0.01, dialect=dialect)
    assert _alias_column_lists(translated, dialect) == []
    assert "c_count" in translated.lower()


@pytest.mark.parametrize("dialect", AFFECTED_DIALECTS)
def test_q13_translation_returns_same_rows_as_original(dialect, sf001_connection, reference_q13_rows):
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=None)
    translated = benchmark.get_query(13, scale_factor=0.01, dialect=dialect)
    assert _run_on_duckdb(sf001_connection, translated, dialect) == reference_q13_rows


@pytest.mark.parametrize("dialect", AFFECTED_DIALECTS)
def test_havoc_q13_variants_have_no_derived_table_column_alias_list(dialect):
    benchmark = TPCHavocBenchmark(scale_factor=0.01, output_dir=None)
    checked = 0
    for variant_id in range(1, 11):
        translated = benchmark.get_query(f"13_v{variant_id}", scale_factor=0.01, dialect=dialect)
        assert _alias_column_lists(translated, dialect) == [], variant_id
        checked += 1
    assert checked == 10


def test_unaffected_dialect_keeps_derived_table_column_alias_list():
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=None)
    translated = benchmark.get_query(13, scale_factor=0.01, dialect="starrocks")
    assert _alias_column_lists(translated, "starrocks")
