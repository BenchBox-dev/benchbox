from __future__ import annotations

import pytest

import benchbox.platforms.dataframe as dataframe_pkg

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_dataframe_package_exports_core_symbols() -> None:
    assert "BenchmarkExecutionMixin" in dataframe_pkg.__all__
    assert "DataFramePlatformChecker" in dataframe_pkg.__all__
    assert "PolarsDataFrameAdapter" in dataframe_pkg.__all__
    assert "PandasDataFrameAdapter" in dataframe_pkg.__all__


def test_dataframe_package_availability_flags_are_booleans() -> None:
    for flag_name in (
        "POLARS_AVAILABLE",
        "PANDAS_AVAILABLE",
        "CUDF_AVAILABLE",
        "DASK_AVAILABLE",
        "DATAFUSION_DF_AVAILABLE",
        "PYSPARK_AVAILABLE",
        "DELTA_SPARK_AVAILABLE",
    ):
        assert isinstance(getattr(dataframe_pkg, flag_name), bool)


