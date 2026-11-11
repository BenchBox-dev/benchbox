# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys

import pytest

from benchbox.platforms.pyspark import PYSPARK_AVAILABLE
from tests.utilities.optional_engines import pyspark_skip_reason, pyspark_usable

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.usefixtures("spark_runtime_environment"),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="PySpark tests skipped on Windows - Hadoop requires winutils.exe setup",
    ),
]

_SKIP_PYSPARK = not pyspark_usable()
_SKIP_REASON = pyspark_skip_reason() or "PySpark is usable"

if PYSPARK_AVAILABLE:
    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter
else:
    PySparkDataFrameAdapter = None


@pytest.mark.skipif(_SKIP_PYSPARK, reason=_SKIP_REASON)
class TestPySparkSessionBranches:
    @pytest.fixture(scope="class")
    def adapter(self, pyspark_test_environment):
        adapter = PySparkDataFrameAdapter(
            master="local[2]",
            app_name="BenchBox-Branch-Tests",
            driver_memory="1g",
            shuffle_partitions=2,
            verbose=False,
        )
        yield adapter
        adapter.close()

    def test_read_csv_trailing_delimiter_drops_dummy(self, adapter, tmp_path):

        csv_path = tmp_path / "region.tbl"
        csv_path.write_text("r_regionkey|r_name|\n1|AFRICA|\n2|AMERICA|\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            has_header=True,
            column_names=["r_regionkey", "r_name"],
            null_marker="NULL",
        )

        assert df.columns == ["r_regionkey", "r_name"]
        assert adapter.get_row_count(df) == 2

    def test_read_csv_restores_empty_strings(self, adapter, tmp_path):

        csv_path = tmp_path / "strings.tbl"
        csv_path.write_text("a|b|\n1|x|\n2||\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            has_header=True,
            column_names=["a", "b"],
            null_marker="NULL",
            string_columns=["b"],
        )

        rows = sorted((r["a"], r["b"]) for r in df.collect())
        assert rows == [("1", "x"), ("2", "")]

    def test_window_count_star(self, adapter):

        df = adapter.spark.createDataFrame([(1, "x"), (1, "y"), (2, "z")], ["g", "v"])
        expr = adapter.window_count(None, partition_by=["g"])
        out = sorted(r["n"] for r in df.withColumn("n", expr).collect())
        assert out == [1, 2, 2]

    def test_to_polars_via_arrow(self, adapter):

        pl = pytest.importorskip("polars")
        df = adapter.spark.createDataFrame([(1, "a"), (2, "b")], ["id", "name"])
        out = adapter.to_polars(df)
        assert isinstance(out, pl.DataFrame)
        assert out.sort("id").to_dicts() == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]

    def test_get_platform_info_reports_version(self, adapter):

        from benchbox.platforms.pyspark import PYSPARK_VERSION

        assert adapter.get_platform_info()["version"] == PYSPARK_VERSION


