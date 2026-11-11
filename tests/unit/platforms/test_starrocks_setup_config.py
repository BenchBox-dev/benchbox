# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def pymysql_stubs(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "pymysql", MagicMock())
    from benchbox.platforms.starrocks.adapter import StarRocksAdapter

    return StarRocksAdapter


class TestSetupConnectionConfig:
    def test_defaults_without_config_or_env(self, pymysql_stubs, monkeypatch) -> None:
        for key in ("STARROCKS_HOST", "STARROCKS_PORT", "STARROCKS_USER", "STARROCKS_PASSWORD", "STARROCKS_HTTP_PORT"):
            monkeypatch.delenv(key, raising=False)
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({})
        assert adapter.host == "localhost"
        assert adapter.port == 9030
        assert adapter.username == "root"
        assert adapter.http_port == 8040
        assert adapter.disable_result_cache is True
        assert adapter.deployment_mode == "self-hosted"

    def test_explicit_config_wins_over_env(self, pymysql_stubs, monkeypatch) -> None:
        monkeypatch.setenv("STARROCKS_HOST", "env-host")
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({"host": "cfg-host", "port": 9031})
        assert adapter.host == "cfg-host"
        assert adapter.port == 9031

    def test_env_fallback_when_config_empty(self, pymysql_stubs, monkeypatch) -> None:
        monkeypatch.setenv("STARROCKS_HOST", "env-host")
        monkeypatch.setenv("STARROCKS_HTTP_PORT", "18040")
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({})
        assert adapter.host == "env-host"
        assert adapter.http_port == 18040


class TestFromConfigDatabaseNaming:
    def test_explicit_database_wins(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.from_config({"database": "custom_db"})
        assert adapter.database == "custom_db"

    def test_benchmark_and_scale_generate_database(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.from_config({"benchmark": "tpch", "scale_factor": 0.01})
        assert adapter.database is not None
        assert adapter.database.startswith("benchbox_")

    def test_tuning_flags_forwarded(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.from_config(
            {"database": "db1", "disable_result_cache": False, "max_execution_time": 60}
        )
        assert adapter.disable_result_cache is False
        assert adapter.max_execution_time == 60


class TestBuildStarrocksConfig:
    def test_env_resolution(self, monkeypatch) -> None:
        from benchbox.platforms.starrocks.setup import _build_starrocks_config

        monkeypatch.setenv("STARROCKS_HOST", "cfg-host")
        monkeypatch.setenv("STARROCKS_PORT", "19030")
        monkeypatch.delenv("STARROCKS_USER", raising=False)
        monkeypatch.delenv("STARROCKS_HTTP_PORT", raising=False)
        with patch("benchbox.platforms.starrocks.setup.build_database_config") as mock_build:
            mock_build.side_effect = lambda **kwargs: kwargs
            built = _build_starrocks_config("starrocks", {}, {}, SimpleNamespace())
        fields = built["fields"]
        merged = dict(built, **{k: v({}) for k, v in fields.items()})
        assert merged["host"] == "cfg-host"
        assert merged["port"] == 19030
        assert merged["username"] == "root"
        assert merged["http_port"] == 8040

    def test_explicit_options_win(self) -> None:
        from benchbox.platforms.starrocks.setup import _build_starrocks_config

        with patch("benchbox.platforms.starrocks.setup.build_database_config") as mock_build:
            mock_build.side_effect = lambda **kwargs: kwargs
            built = _build_starrocks_config("starrocks", {}, {}, SimpleNamespace())
        fields = built["fields"]
        merged = dict(built, **{k: v({"host": "opt-host"}) for k, v in fields.items()})
        assert merged["host"] == "opt-host"


class TestCtasSortSql:
    def test_ctas_sort_returns_none(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        assert adapter._build_ctas_sort_sql("lineitem", []) is None


class TestEnvIsolation:
    def test_password_defaults_empty_without_env(self, pymysql_stubs, monkeypatch) -> None:
        monkeypatch.delenv("STARROCKS_PASSWORD", raising=False)
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({})
        assert adapter.password == ""

    def test_verify_ssl_defaults_true(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({"verify_ssl": None})
        assert adapter.verify_ssl is True

    def test_explicit_verify_ssl_false_kept(self, pymysql_stubs) -> None:
        adapter = pymysql_stubs.__new__(pymysql_stubs)
        adapter._setup_connection_config({"verify_ssl": False})
        assert adapter.verify_ssl is False


