import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.variant_contracts import collect_variant_contract_issues, extract_projection_names

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPARK_VARIANTS = ("json_aggregates", "timeseries_trend_analysis", "list_reduce")
UNSUPPORTED_ON_SPARK = (
    "json_extract_nested",
    "fulltext_simple_search",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
    "asof_join_basic",
)


@pytest.fixture(scope="module")
def catalog():
    return load_primitives_catalog()


@pytest.fixture(scope="module")
def spark_queries() -> dict[str, str]:
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="spark")


@pytest.mark.parametrize("query_id", UNSUPPORTED_ON_SPARK)
def test_syntax_spark_cannot_parse_is_excluded(spark_queries, query_id):
    assert query_id not in spark_queries


@pytest.mark.parametrize("query_id", SPARK_VARIANTS)
def test_spark_variant_is_served_for_the_spark_dialect(catalog, spark_queries, query_id):
    assert spark_queries[query_id] == catalog.queries[query_id].variants["spark"]
    assert spark_queries[query_id] != catalog.queries[query_id].sql


@pytest.mark.parametrize("query_id", SPARK_VARIANTS)
def test_spark_variant_projects_the_contract_columns(catalog, query_id):
    entry = catalog.queries[query_id]
    expected = tuple(column.name for column in entry.result_contract.columns)

    assert extract_projection_names(entry.variants["spark"], dialect="spark") == expected


def test_spark_variants_satisfy_the_variant_contract_check(catalog):
    issues = [issue for issue in collect_variant_contract_issues(catalog) if issue.dialect == "spark"]

    assert issues == []
