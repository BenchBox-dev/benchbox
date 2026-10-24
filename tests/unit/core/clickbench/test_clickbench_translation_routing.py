from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
from benchbox.core.clickbench.queries import ClickBenchQueryManager
from benchbox.utils.dialect_utils import UnknownSqlDialectError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

UNCHANGED_BY_ROUTING_DIALECTS = (
    "duckdb",
    "snowflake",
    "bigquery",
    "postgres",
    "clickhouse",
    "databricks",
    "spark",
    "redshift",
    "trino",
    "presto",
    "athena",
    "mysql",
    "starrocks",
    "doris",
    "tsql",
    "oracle",
    "exasol",
    "sqlite",
    "datafusion",
    "netezza",
    "vertica",
    "greenplum",
)

SQLITE_STRFTIME_QUERY_IDS = frozenset({"Q19", "Q43"})
ALIAS_DIALECTS_UNKNOWN_TO_SQLGLOT = ("ansi", "standard")
ALIAS_DIALECTS_MAPPED_TO_POSTGRES = ("ansi", "standard", "postgresql")


def translate_before_routing(query: str, dialect: str) -> str:
    try:
        return sqlglot.transpile(query, read="clickhouse", write=dialect.lower())[0]
    except Exception:
        return query


@pytest.fixture(scope="module")
def clickbench_queries() -> dict[str, str]:
    queries = ClickBenchQueryManager().get_all_queries()
    assert len(queries) == 43
    return queries


@pytest.fixture(scope="module")
def clickbench(tmp_path_factory) -> ClickBenchBenchmark:
    return ClickBenchBenchmark(output_dir=tmp_path_factory.mktemp("clickbench"))


def differing_query_ids(benchmark: ClickBenchBenchmark, queries: dict[str, str], dialect: str) -> set[str]:
    return {
        query_id
        for query_id, query in queries.items()
        if translate_before_routing(query, dialect) != benchmark.translate_query_text(query, dialect)
    }


@pytest.mark.parametrize("dialect", UNCHANGED_BY_ROUTING_DIALECTS)
def test_routing_leaves_sqlglot_dialect_output_unchanged(clickbench, clickbench_queries, dialect):
    assert differing_query_ids(clickbench, clickbench_queries, dialect) == set()


def test_sqlite_output_uses_strftime_for_date_truncation(clickbench, clickbench_queries):
    for query_id in SQLITE_STRFTIME_QUERY_IDS:
        translated = clickbench.translate_query_text(clickbench_queries[query_id], "sqlite")
        assert "STRFTIME" in translated


@pytest.mark.parametrize("dialect", ALIAS_DIALECTS_UNKNOWN_TO_SQLGLOT)
def test_routing_translates_aliases_previously_returned_untranslated(clickbench, clickbench_queries, dialect):
    assert differing_query_ids(clickbench, clickbench_queries, dialect) == set(clickbench_queries)
    for query_id, query in clickbench_queries.items():
        assert translate_before_routing(query, dialect) == query
        assert clickbench.translate_query_text(query, dialect) == clickbench.translate_query_text(query, "postgres")


@pytest.mark.parametrize("dialect", ALIAS_DIALECTS_MAPPED_TO_POSTGRES)
def test_alias_dialects_translate_like_postgres(clickbench, clickbench_queries, dialect):
    for query in clickbench_queries.values():
        assert clickbench.translate_query_text(query, dialect) == clickbench.translate_query_text(query, "postgres")


def test_unknown_dialect_raises_typed_error_instead_of_returning_original(clickbench):
    with pytest.raises(UnknownSqlDialectError):
        clickbench.translate_query_text("SELECT COUNT(*) FROM hits", "no_such_dialect")


def test_get_queries_propagates_unknown_dialect_error(clickbench):
    with pytest.raises(UnknownSqlDialectError):
        clickbench.get_queries(dialect="no_such_dialect")


def test_snowflake_regex_group_rewrite_still_applies(clickbench, clickbench_queries):
    translated = clickbench.translate_query_text(clickbench_queries["Q29"], "snowflake")
    assert "(?:www" not in translated
