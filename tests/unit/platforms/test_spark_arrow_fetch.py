from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.core.results.result_digest import compute_result_digest
from benchbox.platforms.base.arrow_result import ArrowRows
from benchbox.platforms.base.spark_execution_mixin import (
    SPARK_ARROW_PANDAS_CONF,
    SparkQueryExecutionMixin,
    spark_arrow_converters,
    spark_dataframe_supports_arrow,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Host(SparkQueryExecutionMixin):
    dry_run_mode = False
    disable_cache = False

    def __init__(self, fetch_mode: str) -> None:
        self.fetch_mode = fetch_mode
        self.built: dict[str, Any] = {}

    def log_verbose(self, message: str) -> None:
        return None

    def log_very_verbose(self, message: str) -> None:
        return None

    def _build_query_result_with_validation(self, **kwargs: Any) -> dict[str, Any]:
        self.built = kwargs
        return {"status": "SUCCESS", **kwargs}

    def _merge_plan_capture_into_result(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _build_query_failure_result(self, query_id: str, start_time: float, exception: Exception) -> dict[str, Any]:
        raise exception


def _schema(*type_names: str) -> SimpleNamespace:
    return SimpleNamespace(
        fields=[
            SimpleNamespace(name=f"c{index}", dataType=SimpleNamespace(typeName=lambda name=name: name))
            for index, name in enumerate(type_names)
        ]
    )


ROWS = [(1, "a"), (2, "b"), (3, None)]


class _CollectFrame:
    schema = _schema("long", "string")

    def collect(self) -> list[tuple[Any, ...]]:
        return list(ROWS)


class _ToArrowFrame(_CollectFrame):
    def __init__(self) -> None:
        self.to_arrow_calls = 0

    def toArrow(self) -> pa.Table:
        self.to_arrow_calls += 1
        return pa.table({"c0": [1, 2, 3], "c1": ["a", "b", None]})

    def toPandas(self) -> Any:
        raise AssertionError("toPandas must not run when toArrow exists")


class _ToPandasFrame(_CollectFrame):
    def __init__(self, time_zone: str = "UTC") -> None:
        self.to_pandas_calls = 0
        self.sparkSession = SimpleNamespace(conf=SimpleNamespace(get=lambda key: time_zone))

    def toPandas(self) -> Any:
        import pandas as pd

        self.to_pandas_calls += 1
        return pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": ["a", "b", None]})

    def collect(self) -> list[tuple[Any, ...]]:
        raise AssertionError("collect must not run when toPandas is usable")


def _session(frame: Any, conf_error: Exception | None = None) -> MagicMock:
    spark = MagicMock()
    spark.sql.return_value = frame
    if conf_error is not None:
        spark.conf.set.side_effect = conf_error
    return spark


def _run(host: _Host, spark: Any) -> dict[str, Any]:
    return host._execute_query_spark(spark, "select 1", "Q1", validate_row_count=False)


def test_rows_mode_collects_without_touching_session_conf() -> None:
    host = _Host(FETCH_MODE_ROWS)
    frame = _ToArrowFrame()
    spark = _session(frame)

    _run(host, spark)

    assert frame.to_arrow_calls == 0
    spark.conf.set.assert_not_called()
    assert host.built["fetch_mode"] == FETCH_MODE_ROWS
    assert host.built["materialized_rows"] == ROWS
    assert host.built["actual_row_count"] == 3
    assert host.built["first_row"] == (1, "a")


def test_arrow_mode_prefers_to_arrow() -> None:
    host = _Host(FETCH_MODE_ARROW)
    frame = _ToArrowFrame()

    _run(host, _session(frame))

    assert frame.to_arrow_calls == 1
    assert host.built["fetch_mode"] == FETCH_MODE_ARROW
    assert isinstance(host.built["materialized_rows"], ArrowRows)
    assert list(host.built["materialized_rows"]) == ROWS
    assert host.built["actual_row_count"] == 3
    assert host.built["first_row"] == (1, "a")


def test_arrow_mode_without_to_arrow_uses_arrow_enabled_to_pandas() -> None:
    host = _Host(FETCH_MODE_ARROW)
    frame = _ToPandasFrame()
    spark = _session(frame)

    _run(host, spark)

    spark.conf.set.assert_called_once_with(SPARK_ARROW_PANDAS_CONF, "true")
    assert frame.to_pandas_calls == 1
    assert host.built["fetch_mode"] == FETCH_MODE_ARROW
    assert list(host.built["materialized_rows"]) == ROWS
    assert [type(row[0]) for row in host.built["materialized_rows"]] == [int, int, int]


def test_arrow_mode_without_to_arrow_or_to_pandas_falls_back_to_collect() -> None:
    host = _Host(FETCH_MODE_ARROW)

    _run(host, _session(_CollectFrame()))

    assert host.built["fetch_mode"] == FETCH_MODE_ROWS
    assert host.built["materialized_rows"] == ROWS


def test_arrow_mode_falls_back_to_collect_when_arrow_conf_cannot_be_set() -> None:
    host = _Host(FETCH_MODE_ARROW)
    frame = _ToPandasFrame()
    frame.toPandas = lambda: (_ for _ in ()).throw(AssertionError("toPandas must not run"))
    frame.collect = lambda: list(ROWS)

    _run(host, _session(frame, conf_error=RuntimeError("static conf")))

    assert host.built["fetch_mode"] == FETCH_MODE_ROWS
    assert host.built["materialized_rows"] == ROWS


def test_session_conf_is_set_once_per_session() -> None:
    host = _Host(FETCH_MODE_ARROW)
    spark = _session(_ToPandasFrame())

    _run(host, spark)
    _run(host, spark)

    spark.conf.set.assert_called_once_with(SPARK_ARROW_PANDAS_CONF, "true")


def test_to_pandas_timestamps_follow_the_session_time_zone() -> None:
    import pandas as pd

    frame = _ToPandasFrame(time_zone="America/Los_Angeles")
    frame.schema = _schema("timestamp")
    frame.toPandas = lambda: pd.DataFrame({"t": pd.to_datetime(["2024-02-29 12:34:56.789"])})
    host = _Host(FETCH_MODE_ARROW)

    _run(host, _session(frame))

    rows = host.built["materialized_rows"]
    assert rows.table.column(0).type.tz == "America/Los_Angeles"
    assert rows.table.column(0).to_pylist()[0].isoformat() == "2024-02-29T12:34:56.789000-08:00"


def test_query_failure_surfaces_from_arrow_fetch() -> None:
    host = _Host(FETCH_MODE_ARROW)
    frame = _ToArrowFrame()
    frame.toArrow = MagicMock(side_effect=RuntimeError("arrow fetch failed"))

    with pytest.raises(RuntimeError, match="arrow fetch failed"):
        _run(host, _session(frame))


def test_dataframe_arrow_support_follows_the_installed_pyspark() -> None:
    class _Spark35DataFrame:
        pass

    class _Spark4DataFrame:
        def toArrow(self) -> None:
            return None

    with patch.dict(sys.modules, {"pyspark.sql": SimpleNamespace(DataFrame=_Spark35DataFrame)}):
        assert spark_dataframe_supports_arrow() is False
    with patch.dict(sys.modules, {"pyspark.sql": SimpleNamespace(DataFrame=_Spark4DataFrame)}):
        assert spark_dataframe_supports_arrow() is True


def test_spark_default_fetch_mode_tracks_to_arrow_support() -> None:
    from benchbox.platforms.spark import SparkAdapter

    with patch("benchbox.platforms.spark.spark_dataframe_supports_arrow", return_value=True):
        assert SparkAdapter.default_fetch_mode.fget(None) == FETCH_MODE_ARROW
    with patch("benchbox.platforms.spark.spark_dataframe_supports_arrow", return_value=False):
        assert SparkAdapter.default_fetch_mode.fget(None) == FETCH_MODE_ROWS


def test_adapters_without_a_parity_run_keep_rows_by_default() -> None:
    from benchbox.platforms.lakesail import LakeSailAdapter
    from benchbox.platforms.velox import VeloxAdapter

    assert LakeSailAdapter.default_fetch_mode == FETCH_MODE_ROWS
    assert VeloxAdapter.default_fetch_mode == FETCH_MODE_ROWS


def test_binary_converter_follows_the_classic_collect_type() -> None:
    from pyspark.sql.types import BinaryType, StructField, StructType

    schema = StructType([StructField("b", BinaryType())])

    with patch("benchbox.platforms.base.spark_execution_mixin.spark_dataframe_supports_arrow", return_value=False):
        assert spark_arrow_converters(schema)[0](b"ab") == bytearray(b"ab")
    with patch("benchbox.platforms.base.spark_execution_mixin.spark_dataframe_supports_arrow", return_value=True):
        assert spark_arrow_converters(schema) == {}


def test_to_pandas_refuses_integers_that_float_conversion_would_round() -> None:
    import pandas as pd

    frame = _ToPandasFrame()
    frame.schema = _schema("long")
    frame.toPandas = lambda: pd.DataFrame({"x": [float(2**60), float("nan")]})
    host = _Host(FETCH_MODE_ARROW)

    with pytest.raises(ValueError, match="fetch_mode platform option to rows"):
        _run(host, _session(frame))


def test_to_pandas_keeps_small_integers_that_have_nulls_exact() -> None:
    import pandas as pd

    frame = _ToPandasFrame()
    frame.schema = _schema("long")
    frame.toPandas = lambda: pd.DataFrame({"x": [7.0, float("nan")]})
    host = _Host(FETCH_MODE_ARROW)

    _run(host, _session(frame))

    assert list(host.built["materialized_rows"]) == [(7,), (None,)]


def test_converters_cover_nested_timestamps_maps_and_structs() -> None:
    from pyspark.sql.types import (
        ArrayType,
        IntegerType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    schema = StructType(
        [
            StructField("plain", IntegerType()),
            StructField("ts", ArrayType(TimestampType())),
            StructField("m", MapType(StringType(), IntegerType())),
            StructField("s", StructType([StructField("p", IntegerType())])),
        ]
    )

    converters = spark_arrow_converters(schema)

    assert sorted(converters) == [1, 2, 3]
    assert converters[2]([("a", 1)]) == {"a": 1}
    assert converters[3]({"p": 1}) == (1,)
    assert converters[3]({"p": 1}).p == 1


@pytest.fixture(scope="module")
def local_spark():
    if sys.platform == "win32":
        pytest.skip("Local Spark on Windows requires Hadoop winutils.exe setup")
    pytest.importorskip("pyspark")
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
            .appName("BenchBox-spark-arrow-fetch")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        try:
            spark.sparkContext.setLogLevel("ERROR")
            yield spark
        finally:
            spark.stop()


PARITY_QUERIES = {
    "scalars": (
        "select cast(1 as tinyint) a, cast(null as int) b, cast(3 as bigint) c, cast(1.5 as float) d, "
        "cast(2.25 as double) e, cast('12.345' as decimal(10,3)) f, 'x' g, true h, date'2024-02-29' i, "
        "timestamp'2024-02-29 12:34:56.789' j, timestamp_ntz'2024-02-29 12:34:56.789' k, "
        "cast('ab' as binary) l, interval '1 02:03:04' day to second m"
    ),
    "nulls": "select * from values (1, cast(null as string)), (cast(null as int), 'a'), (3, 'b') t(x, y)",
    "nested": (
        "select array(1, 2, 3) a, map('a', 1, 'b', 2) m, named_struct('p', 1, 'q', 'z') s, "
        "array(named_struct('p', 1)) sa"
    ),
    "aggregate": (
        "select id % 7 k, sum(id) s, avg(id) a, count(*) c, max(cast(id as decimal(20, 2))) d "
        "from range(20000) group by id % 7 order by k"
    ),
    "empty": "select id from range(10) where id > 100",
}


@pytest.mark.medium
@pytest.mark.parametrize("fetch_path", ["toArrow", "toPandas"])
@pytest.mark.parametrize("query_name", sorted(PARITY_QUERIES))
@pytest.mark.parametrize("session_time_zone", ["UTC", "America/Los_Angeles"])
def test_arrow_fetch_digest_matches_collect(
    local_spark, fetch_path: str, query_name: str, session_time_zone: str
) -> None:
    pytest.importorskip("pandas")
    if fetch_path == "toArrow" and not spark_dataframe_supports_arrow():
        pytest.skip("DataFrame.toArrow requires pyspark 4.0 or newer")
    local_spark.conf.set("spark.sql.session.timeZone", session_time_zone)
    local_spark.conf.set(SPARK_ARROW_PANDAS_CONF, "true")
    frame = local_spark.sql(PARITY_QUERIES[query_name])
    collected = frame.collect()

    host = _Host(FETCH_MODE_ARROW)
    if fetch_path == "toPandas":
        frame_view = SimpleNamespace(
            collect=frame.collect,
            toPandas=frame.toPandas,
            schema=frame.schema,
            sparkSession=frame.sparkSession,
        )
        spark = SimpleNamespace(sql=lambda query: frame_view, conf=local_spark.conf)
    else:
        spark = SimpleNamespace(sql=lambda query: frame, conf=local_spark.conf)

    _run(host, spark)

    assert host.built["fetch_mode"] == FETCH_MODE_ARROW
    arrow_rows = host.built["materialized_rows"]
    assert compute_result_digest(arrow_rows) == compute_result_digest(collected)
    assert [tuple(row) for row in arrow_rows] == [tuple(row) for row in collected]
