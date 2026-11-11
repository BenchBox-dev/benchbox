from __future__ import annotations

import pytest

import benchbox.platforms.dataframe as dataframe_pkg

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_dataframe_package_exports_and_flags():
    assert "PolarsDataFrameAdapter" in dataframe_pkg.__all__
    assert "PandasDataFrameAdapter" in dataframe_pkg.__all__
    assert hasattr(dataframe_pkg, "POLARS_AVAILABLE")
    assert hasattr(dataframe_pkg, "PANDAS_AVAILABLE")
    assert hasattr(dataframe_pkg, "DATAFRAME_PLATFORMS")


def test_dataframe_package_optional_exports_are_defined():

    for name in (
        "CuDFDataFrameAdapter",
        "DaskDataFrameAdapter",
        "DataFusionDataFrameAdapter",
        "PySparkDataFrameAdapter",
    ):
        attr = getattr(dataframe_pkg, name, _SENTINEL := object())
        assert attr is not _SENTINEL, f"{name} not exported from dataframe package"
        assert attr is None or callable(attr), f"{name} should be None or a class, got {type(attr)}"


