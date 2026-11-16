from __future__ import annotations

import os
import sys
from datetime import date

import pytest

from benchbox.core.tpch.dataframe_queries import get_query as get_tpch_query, get_tpch_parameters
from benchbox.core.tpchavoc.dataframe_queries import get_query as get_havoc_query

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ON_TIME = (date(1995, 1, 1), date(1995, 1, 5))
LATE = (date(1995, 1, 5), date(1995, 1, 1))
LINES = [
    (1, 1, LATE),
    (2, 1, LATE),
    (2, 2, ON_TIME),
    (3, 1, LATE),
    (3, 2, LATE),
    (4, 1, ON_TIME),
    (4, 2, LATE),
    (4, 3, ON_TIME),
]
EXPECTED = [("Supplier#1", 1), ("Supplier#2", 1)]
QUERY_IDS = ["Q21", *(f"Q21v{variant}" for variant in range(1, 11))]


@pytest.fixture(scope="module")
def spark_session():
    if sys.platform == "win32":
        pytest.skip("Local Spark on Windows requires Hadoop winutils.exe setup")
    pytest.importorskip("pyspark", reason="Q21 Spark execution requires PySpark")
    from benchbox.platforms.pyspark import ensure_compatible_java, is_java_compatible

    with pytest.MonkeyPatch.context() as settings:
        settings.setenv("JAVA_HOME", os.environ.get("JAVA_HOME", ""))
        settings.setenv("PYSPARK_PYTHON", sys.executable)
        settings.setenv("SPARK_LOCAL_IP", "127.0.0.1")
        version, _ = ensure_compatible_java()
        if not is_java_compatible(version):
            pytest.skip("Q21 Spark execution requires a compatible Java runtime")
        from pyspark.sql import SparkSession

        spark = (
            SparkSession.builder.master("local[1]")
            .appName("BenchBox-Q21-expression-regression")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        try:
            spark_context = getattr(spark, "sparkContext", None)
            assert spark_context is not None, "Q21 regression requires the owned local Spark session"
            spark_context.setLogLevel("ERROR")
            yield spark
        finally:
            spark.stop()


@pytest.fixture
def spark_ctx(spark_session, platform):
    from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter
    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

    adapter = PySparkDataFrameAdapter() if platform == "pyspark" else LakeSailDataFrameAdapter()
    adapter._spark = spark_session
    try:
        ctx = adapter.create_context()
        nation_name = get_tpch_parameters(21)["nation_name"]
        ctx.register_table(
            "nation", spark_session.createDataFrame([(0, nation_name)], "n_nationkey BIGINT, n_name STRING")
        )
        ctx.register_table(
            "supplier",
            spark_session.createDataFrame(
                [(1, "Supplier#1", 0), (2, "Supplier#2", 0), (3, "Supplier#3", 0)],
                "s_suppkey BIGINT, s_name STRING, s_nationkey BIGINT",
            ),
        )
        ctx.register_table(
            "orders",
            spark_session.createDataFrame(
                [(key, "F") for key in (1, 2, 3, 4)], "o_orderkey BIGINT, o_orderstatus STRING"
            ),
        )
        ctx.register_table(
            "lineitem",
            spark_session.createDataFrame(
                [(order, supp, dates[0], dates[1]) for order, supp, dates in LINES],
                "l_orderkey BIGINT, l_suppkey BIGINT, l_receiptdate DATE, l_commitdate DATE",
            ),
        )
        yield ctx
    finally:
        adapter._spark = None


@pytest.mark.parametrize("platform", ["pyspark", "lakesail"])
@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_q21_spark_inner_join_form_matches_semi_join_answer(spark_ctx, query_id):
    query = get_tpch_query(query_id) if query_id == "Q21" else get_havoc_query(query_id)

    result = query.get_impl_for_family("expression")(spark_ctx)
    frame = getattr(result, "native", result)

    assert [tuple(row) for row in frame.collect()] == EXPECTED
