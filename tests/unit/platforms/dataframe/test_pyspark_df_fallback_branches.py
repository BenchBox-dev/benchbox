# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

import benchbox.platforms.dataframe.pyspark_df as mod
from tests.utilities.optional_engines import pyspark_skip_reason, pyspark_usable

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_REQUIRES_PYSPARK_SESSION = not pyspark_usable()
_SESSION_SKIP_REASON = pyspark_skip_reason() or "PySpark is usable"


def _adapter_without_session():

    return mod.PySparkDataFrameAdapter(master="local[1]", app_name="BenchBox-Fallback-Tests")


def test_to_polars_falls_back_to_pandas_without_to_arrow():
    pytest.importorskip("polars")
    adapter = _adapter_without_session()
    df = MagicMock(spec=["toPandas"])
    pandas_df = MagicMock()
    df.toPandas.return_value = pandas_df
    with patch("polars.from_pandas", return_value="POLARS_DF") as from_pandas:
        assert adapter.to_polars(df) == "POLARS_DF"
    from_pandas.assert_called_once_with(pandas_df)


def test_to_polars_falls_back_when_to_arrow_raises():
    pytest.importorskip("polars")
    adapter = _adapter_without_session()
    df = MagicMock()
    df.toArrow.side_effect = RuntimeError("arrow off")
    df.toPandas.return_value = MagicMock()
    with patch("polars.from_pandas", return_value="POLARS_DF"):
        assert adapter.to_polars(df) == "POLARS_DF"
    df.toPandas.assert_called_once()


def test_to_polars_without_polars_installed(monkeypatch: pytest.MonkeyPatch):
    adapter = _adapter_without_session()
    monkeypatch.setitem(sys.modules, "polars", None)
    with pytest.raises(ImportError, match="Polars not installed"):
        adapter.to_polars(MagicMock())


def test_init_raises_without_pyspark(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "PYSPARK_AVAILABLE", False)
    with pytest.raises(ImportError, match="PySpark not installed"):
        mod.PySparkDataFrameAdapter()


def test_module_fallback_aliases_without_pyspark(monkeypatch: pytest.MonkeyPatch):

    import typing

    monkeypatch.setattr("benchbox.platforms.pyspark.PYSPARK_AVAILABLE", False)
    try:
        importlib.reload(mod)
        assert mod.F is None
        assert mod.PySparkDF is typing.Any
        assert mod.PySparkExpr is typing.Any
    finally:
        monkeypatch.setattr("benchbox.platforms.pyspark.PYSPARK_AVAILABLE", True)
        importlib.reload(mod)
    assert mod.F is not None


@pytest.mark.skipif(_REQUIRES_PYSPARK_SESSION, reason=_SESSION_SKIP_REASON)
def test_window_count_star_uses_lit_one(pyspark_test_environment, monkeypatch):

    pytest.importorskip("pyspark")
    from pyspark.sql.column import Column

    from tests.utilities.session_isolation import own_environment

    own_environment(monkeypatch, ["SPARK_AUTH_SOCKET_TIMEOUT", "SPARK_BUFFER_SIZE"])
    adapter = _adapter_without_session()
    try:
        expr = adapter.window_count(None, partition_by=["g"])
        assert isinstance(expr, Column)
        assert "count(1)" in str(expr)
    finally:
        adapter.close()
