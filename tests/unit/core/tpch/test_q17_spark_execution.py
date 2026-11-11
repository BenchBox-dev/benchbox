from __future__ import annotations

import os
import sys

import pytest

from benchbox.core.tpch.dataframe_queries import get_query

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture(scope="module")
def spark_session():
    if sys.platform == "win32":
        pytest.skip("Local Spark on Windows requires Hadoop winutils.exe setup")
    pytest.importorskip("pyspark", reason="Q17 Spark execution requires PySpark")
    from benchbox.platforms.pyspark import ensure_compatible_java, is_java_compatible

    with pytest.MonkeyPatch.context() as settings:
        settings.setenv("JAVA_HOME", os.environ.get("JAVA_HOME", ""))
        settings.setenv("PYSPARK_PYTHON", sys.executable)
        settings.setenv("SPARK_LOCAL_IP", "127.0.0.1")
        version, _ = ensure_compatible_java()
        if not is_java_compatible(version):
            pytest.skip("Q17 Spark execution requires a compatible Java runtime")
        from pyspark.sql import SparkSession

        spark = (
            SparkSession.builder.master("local[1]")
            .appName("BenchBox-Q17-expression-regression")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        try:
            spark_context = getattr(spark, "sparkContext", None)
            assert spark_context is not None, "Q17 regression requires the owned local Spark session"
            spark_context.setLogLevel("ERROR")
            yield spark
        finally:
            spark.stop()


@pytest.fixture
def spark_adapter(spark_session, platform):
    from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter
    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

    adapter = PySparkDataFrameAdapter() if platform == "pyspark" else LakeSailDataFrameAdapter()

    adapter._spark = spark_session
    try:
        yield adapter
    finally:
        adapter._spark = None


@pytest.mark.parametrize("platform", ["pyspark", "lakesail"])
@pytest.mark.parametrize("case", ["empty", "all_null", "nonempty"])
def test_q17_spark_expression_client_matches_sql(spark_session, spark_adapter, case):
    import duckdb
    import pyarrow as pa
    from pyspark.sql.types import DoubleType

    part = pa.table({"p_partkey": [1], "p_brand": ["Brand#23"], "p_container": ["MED BOX"]})
    lineitem = pa.table(
        {
            "l_partkey": [1, 1],
            "l_quantity": [10.0, 10.0] if case == "empty" else [1.0, 19.0],
            "l_extendedprice": pa.array([None, None] if case == "all_null" else [70.0, 140.0], type=pa.float64()),
        }
    )
    ctx = spark_adapter.create_context()
    ctx.register_table(
        "part", spark_session.createDataFrame(part.to_pylist(), "p_partkey BIGINT, p_brand STRING, p_container STRING")
    )
    ctx.register_table(
        "lineitem",
        spark_session.createDataFrame(
            lineitem.to_pylist(), "l_partkey BIGINT, l_quantity DOUBLE, l_extendedprice DOUBLE"
        ),
    )
    query = get_query("Q17")
    assert query.expression_impl is not None
    lazy_result = query.expression_impl(ctx)
    assert lazy_result.native.schema.fieldNames() == ["avg_yearly"]
    assert lazy_result.native.schema["avg_yearly"].dataType == DoubleType()
    with duckdb.connect() as oracle:
        oracle.register("part", part)
        oracle.register("lineitem", lineitem)
        expected = oracle.execute(
            "SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly "
            "FROM lineitem, part WHERE p_partkey = l_partkey "
            "AND p_brand = 'Brand#23' AND p_container = 'MED BOX' "
            "AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem WHERE l_partkey = p_partkey)"
        ).fetchone()
    result = spark_adapter.execute_query(ctx, query)
    assert result["status"] == "SUCCESS", result.get("error")
    assert result["rows_returned"] == 1
    assert result["first_row"] == expected
    assert expected == ((10.0,) if case == "nonempty" else (None,))


