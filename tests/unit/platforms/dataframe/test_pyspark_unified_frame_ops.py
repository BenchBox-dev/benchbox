from __future__ import annotations

import os
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture(scope="module")
def spark_session():
    if sys.platform == "win32":
        pytest.skip("Local Spark on Windows requires Hadoop winutils.exe setup")
    pytest.importorskip("pyspark", reason="requires PySpark")
    from benchbox.platforms.pyspark import ensure_compatible_java, is_java_compatible

    with pytest.MonkeyPatch.context() as settings:
        settings.setenv("JAVA_HOME", os.environ.get("JAVA_HOME", ""))
        settings.setenv("PYSPARK_PYTHON", sys.executable)
        settings.setenv("SPARK_LOCAL_IP", "127.0.0.1")
        version, _ = ensure_compatible_java()
        if not is_java_compatible(version):
            pytest.skip("requires a compatible Java runtime")
        from pyspark.sql import SparkSession

        spark = (
            SparkSession.builder.master("local[1]")
            .appName("BenchBox-unified-frame-ops")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        try:
            yield spark
        finally:
            spark.stop()


@pytest.fixture(params=["pyspark", "lakesail"])
def ctx(request, spark_session):
    from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter
    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

    adapter = PySparkDataFrameAdapter() if request.param == "pyspark" else LakeSailDataFrameAdapter()
    adapter._spark = spark_session
    try:
        yield adapter.create_context(), spark_session
    finally:
        adapter._spark = None


def _frame(ctx, spark, rows, schema):
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    return UnifiedLazyFrame(spark.createDataFrame(rows, schema), ctx._adapter)


def test_chained_when_then_builds_one_case_expression(ctx):
    context, spark = ctx
    frame = _frame(context, spark, [(-1,), (0,), (5,)], "v INT")
    col, lit = context.col, context.lit
    label = (
        context.when(col("v") < lit(0))
        .then(lit("neg"))
        .when(col("v") == lit(0))
        .then(lit("zero"))
        .otherwise(lit("pos"))
    )
    rows = frame.with_columns(label.alias("label")).sort(["v"]).native.collect()
    assert [r["label"] for r in rows] == ["neg", "zero", "pos"]


def test_slice_applies_offset_then_length_after_sort(ctx):
    context, spark = ctx
    frame = _frame(context, spark, [(i,) for i in (5, 3, 9, 1, 7)], "v INT")
    rows = frame.sort(["v"]).slice(1, 2).native.collect()
    assert [r["v"] for r in rows] == [3, 5]
    assert [r["v"] for r in frame.sort(["v"]).slice(0, 2).native.collect()] == [1, 3]
    assert [r["v"] for r in frame.sort(["v"]).slice(3).native.collect()] == [7, 9]


def test_date_add_accepts_wrapped_column(ctx):
    context, spark = ctx
    frame = _frame(context, spark, [("2001-01-01",)], "d STRING")
    expr = context.date_add(context.cast_date(context.col("d")), 5)
    rows = frame.select(expr.alias("r")).native.collect()
    assert str(rows[0]["r"]) == "2001-01-06"


def test_create_dataframe_with_all_null_column(ctx):
    context, _ = ctx
    if context._adapter.platform_name != "PySpark":
        pytest.skip("LakeSail builds in-memory frames through a different path")
    frame = context.create_dataframe({"ratio": [None]})
    assert frame.native.collect()[0]["ratio"] is None
