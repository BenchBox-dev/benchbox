# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _trino_stubs():
    return patch.dict("sys.modules", {"trino": MagicMock(), "trino.auth": MagicMock()})


def _presto_stubs():
    return patch.dict("sys.modules", {"prestodb": MagicMock(), "prestodb.dbapi": MagicMock()})


class TestTrinoFromConfig:
    def test_core_fields_mapped(self) -> None:
        with _trino_stubs():
            from benchbox.platforms.trino import TrinoAdapter

            try:
                adapter = TrinoAdapter.from_config(
                    {
                        "host": "coord.example.com",
                        "port": 8081,
                        "catalog": "iceberg",
                        "schema": "tpch",
                        "username": "analyst",
                        "password": "secret",
                        "benchmark": "tpch",
                        "scale_factor": 0.01,
                    }
                )
            except ImportError:
                pytest.skip("Trino drivers not installed")
        assert adapter.host == "coord.example.com"
        assert adapter.port == 8081
        assert adapter.catalog == "iceberg"
        assert adapter.username == "analyst"

    def test_timezone_optional_field_mapped(self) -> None:
        with _trino_stubs():
            from benchbox.platforms.trino import TrinoAdapter

            try:
                adapter = TrinoAdapter.from_config(
                    {"catalog": "hive", "timezone": "America/New_York", "benchmark": "tpch", "scale_factor": 0.01}
                )
            except ImportError:
                pytest.skip("Trino drivers not installed")
        assert adapter.timezone == "America/New_York"

    def test_unknown_keys_do_not_leak(self) -> None:
        with _trino_stubs():
            from benchbox.platforms.trino import TrinoAdapter

            try:
                adapter = TrinoAdapter.from_config(
                    {"catalog": "hive", "not_a_field": "x", "benchmark": "tpch", "scale_factor": 0.01}
                )
            except ImportError:
                pytest.skip("Trino drivers not installed")
        assert not hasattr(adapter, "not_a_field")


class TestPrestoFromConfig:
    def test_core_fields_mapped(self) -> None:
        with _presto_stubs():
            from benchbox.platforms.presto import PrestoAdapter

            try:
                adapter = PrestoAdapter.from_config(
                    {
                        "host": "presto.example.com",
                        "port": 8085,
                        "catalog": "hive",
                        "username": "analyst",
                        "benchmark": "tpch",
                        "scale_factor": 0.01,
                    }
                )
            except ImportError:
                pytest.skip("Presto drivers not installed")
        assert adapter.host == "presto.example.com"
        assert adapter.port == 8085
        assert adapter.catalog == "hive"
        assert adapter.username == "analyst"

    def test_source_optional_field_mapped(self) -> None:
        with _presto_stubs():
            from benchbox.platforms.presto import PrestoAdapter

            try:
                adapter = PrestoAdapter.from_config(
                    {"catalog": "hive", "source": "benchbox", "benchmark": "tpch", "scale_factor": 0.01}
                )
            except ImportError:
                pytest.skip("Presto drivers not installed")
        assert adapter.source == "benchbox"


