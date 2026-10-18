from __future__ import annotations

import pytest

from benchbox.core.joinorder.benchmark import JoinOrderBenchmark
from benchbox.core.joinorder.queries import JoinOrderQueryManager
from benchbox.utils.dialect_utils import sql_translation_context

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

TRANSLATION_ENGINES = ["duckdb", "postgres", "sqlite", "spark", "clickhouse", "trino"]


@pytest.fixture(scope="module")
def job_benchmark(tmp_path_factory: pytest.TempPathFactory) -> JoinOrderBenchmark:
    return JoinOrderBenchmark(output_dir=str(tmp_path_factory.mktemp("joinorder")))


@pytest.mark.parametrize("dialect", TRANSLATION_ENGINES)
def test_get_queries_translates_all_113(job_benchmark: JoinOrderBenchmark, dialect: str) -> None:
    with sql_translation_context() as outcomes:
        translated = job_benchmark.get_queries(dialect=dialect)

    assert sorted(translated) == JoinOrderQueryManager().get_query_ids()
    assert len(outcomes) == len(translated)
    assert all(outcome.status == "success" for outcome in outcomes), [
        (outcome.target_dialect, outcome.message) for outcome in outcomes if outcome.status != "success"
    ]


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_translated_queries_explain_against_engine_ddl(job_benchmark: JoinOrderBenchmark, dialect: str) -> None:
    translated = job_benchmark.get_queries(dialect=dialect)
    ddl_statements = job_benchmark.get_create_tables_sql(dialect=dialect)
    _assert_explain_all(translated, ddl_statements, dialect)


def _assert_explain_all(translated: dict[str, str], ddl_statements: str, dialect: str) -> None:
    if dialect == "duckdb":
        duckdb = pytest.importorskip("duckdb")
        connection = duckdb.connect()
        try:
            connection.execute(ddl_statements)
            for query_id, sql in translated.items():
                connection.execute(f"EXPLAIN {sql}")
        finally:
            connection.close()
    elif dialect == "sqlite":
        import sqlite3

        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(ddl_statements)
            for query_id, sql in translated.items():
                connection.execute(f"EXPLAIN QUERY PLAN {sql}")
        finally:
            connection.close()
    else:  # pragma: no cover
        raise AssertionError(f"No EXPLAIN harness for {dialect}")


def test_get_query_spark_translation_uses_backtick_identifiers(job_benchmark: JoinOrderBenchmark) -> None:
    canonical = JoinOrderQueryManager().get_query("1a")
    translated = job_benchmark.get_query("1a", dialect="spark")

    assert translated != canonical
    assert "`company_type`" in translated


def test_get_query_duckdb_translation_quotes_identifiers(job_benchmark: JoinOrderBenchmark) -> None:
    translated = job_benchmark.get_query("1a", dialect="duckdb")

    assert '"company_type"' in translated


@pytest.mark.parametrize("dialect", ["postgres", "clickhouse", "snowflake"])
def test_get_query_fold_case_engines_leave_table_unquoted(job_benchmark: JoinOrderBenchmark, dialect: str) -> None:
    translated = job_benchmark.get_query("1a", dialect=dialect)

    assert "company_type" in translated
    assert '"company_type"' not in translated
    assert "`company_type`" not in translated


def test_translation_preserves_portable_alias_for_query_15(job_benchmark: JoinOrderBenchmark) -> None:
    canonical = JoinOrderQueryManager().get_query("15a")
    translated = job_benchmark.get_query("15a", dialect="duckdb")

    assert translated != canonical
    assert '"at1"' in translated
    assert '"at"' not in translated


@pytest.mark.parametrize("dialect", ["bogus-engine", "", "postgresql"])
def test_unknown_dialect_falls_back_to_canonical_text(job_benchmark: JoinOrderBenchmark, dialect: str) -> None:
    canonical = JoinOrderQueryManager().get_all_queries()

    assert job_benchmark.get_queries(dialect=dialect) == canonical


def test_adapter_runtime_receives_translated_queries(job_benchmark: JoinOrderBenchmark) -> None:
    from benchbox.platforms.duckdb import DuckDBAdapter

    canonical = JoinOrderQueryManager().get_all_queries()
    runtime_queries = DuckDBAdapter()._get_dialect_queries(job_benchmark, "joinorder")

    assert sorted(runtime_queries) == sorted(canonical)
    assert runtime_queries["1a"] != canonical["1a"]
    assert runtime_queries["1a"] == job_benchmark.get_query("1a", dialect="duckdb")


def test_get_query_with_dialect_rejects_unknown_id(job_benchmark: JoinOrderBenchmark) -> None:
    with pytest.raises(ValueError):
        job_benchmark.get_query("not-a-query", dialect="duckdb")


def test_get_query_with_dialect_still_rejects_params(job_benchmark: JoinOrderBenchmark) -> None:
    with pytest.raises(ValueError):
        job_benchmark.get_query("1a", params={"x": 1}, dialect="duckdb")


def test_facade_passes_dialect_through(tmp_path) -> None:
    from benchbox.joinorder import JoinOrder

    facade = JoinOrder(output_dir=str(tmp_path))
    job_benchmark = JoinOrderBenchmark(output_dir=str(tmp_path))

    assert facade.get_queries() == job_benchmark.get_queries()
    assert facade.get_queries(dialect="spark") == job_benchmark.get_queries(dialect="spark")
    assert facade.get_query("1a", dialect="spark") == job_benchmark.get_query("1a", dialect="spark")
