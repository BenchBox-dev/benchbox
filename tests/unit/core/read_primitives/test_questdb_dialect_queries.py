import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.variant_contracts import collect_variant_contract_issues, extract_projection_names

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUESTDB_VARIANTS = (
    "min_by_simple",
    "min_by_complex",
    "min_by_with_ties",
    "statistical_percentiles",
    "approx_quantiles_array",
    "timeseries_trend_analysis",
    "array_agg_simple",
    "array_agg_distinct",
    "array_unnest",
    "array_contains",
    "array_length",
    "array_slice",
    "list_transform",
    "list_reduce",
    "unpivot_basic",
    "asof_join_basic",
    "json_extract_simple",
    "json_extract_nested",
)


@pytest.fixture(scope="module")
def catalog():
    return load_primitives_catalog()


@pytest.fixture(scope="module")
def questdb_queries() -> dict[str, str]:
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="questdb")


@pytest.mark.parametrize("query_id", QUESTDB_VARIANTS)
def test_questdb_variant_is_served_for_the_questdb_dialect(catalog, questdb_queries, query_id):
    assert questdb_queries[query_id] == catalog.queries[query_id].variants["questdb"]


@pytest.mark.parametrize("query_id", QUESTDB_VARIANTS)
def test_questdb_variant_projects_the_contract_columns(catalog, query_id):
    entry = catalog.queries[query_id]
    expected = tuple(column.name for column in entry.result_contract.columns) if entry.result_contract else None
    if expected is None:
        expected = extract_projection_names(entry.sql, dialect="duckdb")

    assert extract_projection_names(entry.variants["questdb"], dialect="questdb") == expected


def test_questdb_variants_satisfy_the_variant_contract_check(catalog):
    issues = [issue for issue in collect_variant_contract_issues(catalog) if issue.dialect == "questdb"]

    assert issues == []


@pytest.mark.parametrize("query_id", ("array_agg_simple", "array_agg_distinct", "array_slice"))
def test_ordered_array_variants_sort_the_aggregated_array_explicitly(catalog, query_id):
    sql = catalog.queries[query_id].variants["questdb"].upper()

    assert "ARRAY_SORT(ARRAY_AGG(" in sql
    assert "LIMIT 9223372036854775807" not in sql


def test_percentile_variant_matches_exact_percentile_cont(catalog):
    duckdb = pytest.importorskip("duckdb")
    entry = catalog.queries["statistical_percentiles"]
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE lineitem AS SELECT "
        "CASE WHEN i % 3 = 0 THEN 'A' ELSE 'R' END AS l_returnflag, "
        "CASE WHEN i % 5 = 0 THEN 'F' ELSE 'O' END AS l_linestatus, "
        "CAST((i * 7) % 50 AS DOUBLE) AS l_quantity, "
        "CAST((i * 131) % 9973 AS DOUBLE) / 7 AS l_extendedprice "
        "FROM range(1, 1000) t(i)"
    )

    expected = sorted(connection.execute(entry.sql).fetchall())
    actual = sorted(connection.execute(entry.variants["questdb"]).fetchall())

    assert len(actual) == len(expected) > 0
    for expected_row, actual_row in zip(expected, actual, strict=True):
        assert expected_row[:3] == actual_row[:3]
        assert actual_row[3:] == pytest.approx(expected_row[3:])
