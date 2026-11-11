# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.databricks import dataframe_adapter as mod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _reload_adapter():
    return importlib.reload(mod)


def _stage(entries: dict[str, object]) -> dict[str, object]:
    saved = {name: sys.modules[name] for name in entries if name in sys.modules}
    sys.modules.update(entries)
    return saved


def _unstage(entries: dict[str, object], saved: dict[str, object]) -> None:
    for name in entries:
        if name in saved:
            sys.modules[name] = saved[name]
        else:
            sys.modules.pop(name, None)


def test_reload_with_working_databricks_connect():

    initial_available = mod.DATABRICKS_CONNECT_AVAILABLE
    initial_error = mod._databricks_connect_error
    pkg = types.ModuleType("databricks")
    pkg.__path__ = []
    connect = types.ModuleType("databricks.connect")
    connect.DatabricksSession = MagicMock(name="DatabricksSession")
    staged = {"databricks": pkg, "databricks.connect": connect}
    saved = _stage(staged)
    try:
        _reload_adapter()
        assert mod.DATABRICKS_CONNECT_AVAILABLE is True
        assert mod._databricks_connect_error is None
    finally:
        _unstage(staged, saved)
        _reload_adapter()
    assert initial_available == mod.DATABRICKS_CONNECT_AVAILABLE
    assert mod._databricks_connect_error == initial_error


def test_reload_with_crashing_databricks_connect():

    def _raise(_name):
        raise RuntimeError("shim boom")

    pkg = types.ModuleType("databricks")
    pkg.__path__ = []
    connect = types.ModuleType("databricks.connect")
    connect.__getattr__ = _raise
    staged = {"databricks": pkg, "databricks.connect": connect}
    saved = _stage(staged)
    try:
        _reload_adapter()
        assert mod.DATABRICKS_CONNECT_AVAILABLE is False
        assert mod._databricks_connect_error == "shim boom"
    finally:
        _unstage(staged, saved)
        _reload_adapter()


def test_reload_without_pyspark():

    import typing

    staged = {"pyspark.sql": None}
    saved = _stage(staged)
    try:
        _reload_adapter()
        assert mod.PYSPARK_AVAILABLE is False
        assert mod.SparkDataFrame is typing.Any
        assert mod.F is None
    finally:
        _unstage(staged, saved)
        _reload_adapter()
    assert mod.PYSPARK_AVAILABLE is True


