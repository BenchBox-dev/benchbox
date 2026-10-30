import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]

UNSUPPORTED_ON_SPARK = (
    "fulltext_simple_search",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
    "asof_join_basic",
)


@pytest.fixture(scope="module")
def spark_queries() -> dict[str, str]:
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="spark")


@pytest.mark.parametrize("query_id", UNSUPPORTED_ON_SPARK)
def test_syntax_spark_cannot_parse_is_excluded(spark_queries, query_id):
    assert query_id not in spark_queries


def test_json_extraction_uses_spark_json_path_function(spark_queries):
    sql = spark_queries["json_extract_nested"].lower()

    assert "get_json_object(c_comment, '$.profile.segment')" in sql
    assert "json_valid" not in sql
    assert "json_extract(" not in sql


def test_json_aggregates_use_to_json_over_collected_values(spark_queries):
    sql = spark_queries["json_aggregates"].lower()

    assert "to_json(collect_list(p_name))" in sql
    assert "map_from_entries(" in sql
    assert "json_arrayagg" not in sql
    assert "json_objectagg" not in sql


def test_trend_analysis_derives_epoch_without_extract_epoch(spark_queries):
    sql = spark_queries["timeseries_trend_analysis"].upper()

    assert "EXTRACT(EPOCH" not in sql
    assert "DATEDIFF(" in sql
    assert "REGR_SLOPE(MONTHLY_REVENUE, MONTH_EPOCH) OVER ()" in sql


def test_list_reduce_seeds_the_accumulator_with_a_decimal(spark_queries):
    sql = spark_queries["list_reduce"].upper()

    assert "AGGREGATE(QTYS, CAST(0 AS DECIMAL(38, 2))" in sql
    assert "REDUCE(" not in sql
