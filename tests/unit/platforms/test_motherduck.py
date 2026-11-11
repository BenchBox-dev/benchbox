# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import os
from unittest.mock import Mock, patch

import pytest

from benchbox.core.config_inheritance import resolve_dialect_for_query_translation
from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestMotherDuckRegistration:
    def test_platform_registered_in_registry(self):
        caps = PlatformRegistry.get_platform_capabilities("motherduck")
        assert caps is not None
        assert caps.supports_sql is True

    def test_inherits_from_duckdb(self):
        caps = PlatformRegistry.get_platform_capabilities("motherduck")
        assert caps.inherits_from == "duckdb"
        assert caps.platform_family == "duckdb"

    def test_dialect_resolves_to_duckdb(self):
        dialect = resolve_dialect_for_query_translation("motherduck")
        assert dialect == "duckdb"

    def test_deployment_mode_is_managed(self):
        caps = PlatformRegistry.get_platform_capabilities("motherduck")
        assert "managed" in caps.deployment_modes
        assert caps.default_deployment == "managed"

    def test_requires_credentials(self):
        caps = PlatformRegistry.get_platform_capabilities("motherduck")
        managed_cap = caps.deployment_modes["managed"]
        assert managed_cap.requires_credentials is True
        assert managed_cap.requires_network is True
        assert "token" in managed_cap.auth_methods


class TestMotherDuckAdapter:
    def test_requires_token(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MOTHERDUCK_TOKEN", None)

            with pytest.raises(ValueError) as exc_info:
                MotherDuckAdapter()

            assert "authentication token" in str(exc_info.value).lower()
            assert "MOTHERDUCK_TOKEN" in str(exc_info.value)

    def test_token_from_config(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("MOTHERDUCK_TOKEN", None)

            adapter = MotherDuckAdapter(token="test-token-12345")
            assert adapter.token == "test-token-12345"

    def test_token_from_environment(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        with patch.dict(os.environ, {"MOTHERDUCK_TOKEN": "env-token-67890"}):
            adapter = MotherDuckAdapter()
            assert adapter.token == "env-token-67890"

    def test_default_database(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token")
        assert adapter.database == "benchbox"

    def test_custom_database(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token", database="my_custom_db")
        assert adapter.database == "my_custom_db"

    def test_platform_name(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token")
        assert adapter.platform_name == "MotherDuck"

    def test_dialect_is_duckdb(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token")
        assert adapter._dialect == "duckdb"

    def test_get_target_dialect(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token")
        dialect = adapter.get_target_dialect()
        assert dialect == "duckdb"

    def test_get_platform_metadata(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token", database="test_db")
        metadata = adapter.get_platform_metadata()

        assert metadata["platform_type"] == "motherduck"
        assert metadata["platform_name"] == "MotherDuck"
        assert metadata["dialect"] == "duckdb"
        assert metadata["database"] == "test_db"
        assert metadata["inherits_from"] == "duckdb"

    def test_create_external_tables_uses_duckdb_view_registration(self, tmp_path):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="test-token")
        parquet_path = tmp_path / "customer.parquet"
        parquet_path.touch()

        benchmark = Mock()
        benchmark.tables = {"customer": parquet_path}
        benchmark.get_table_loading_order.return_value = ["customer"]

        executed_sql: list[str] = []
        connection = Mock()

        def _execute(sql, *args, **kwargs):
            executed_sql.append(str(sql))
            result = Mock()
            if str(sql).strip().upper().startswith("SELECT COUNT(*)"):
                result.fetchone.return_value = (3,)
            else:
                result.fetchone.return_value = None
            return result

        connection.execute.side_effect = _execute

        table_stats, _, per_table_timings = adapter.create_external_tables(benchmark, connection, tmp_path)

        assert adapter.supports_external_tables is True
        assert table_stats == {"customer": 3}
        assert per_table_timings is None
        assert any("CREATE VIEW customer AS SELECT * FROM read_parquet(" in sql for sql in executed_sql)


class TestMotherDuckAdapterFactory:
    def test_factory_can_resolve_motherduck(self):
        from benchbox.platforms.adapter_factory import _normalize_platform_name, get_available_deployments

        name, df_mode, deployment = _normalize_platform_name("motherduck")
        assert name == "motherduck"
        assert df_mode is False
        assert deployment is None

        deployments = get_available_deployments("motherduck")
        assert "managed" in deployments

    def test_case_insensitive_name(self):
        from benchbox.platforms.adapter_factory import _normalize_platform_name

        name1, _, _ = _normalize_platform_name("MotherDuck")
        name2, _, _ = _normalize_platform_name("MOTHERDUCK")
        name3, _, _ = _normalize_platform_name("motherduck")

        assert name1 == name2 == name3 == "motherduck"


class TestMotherDuckFromConfig:
    def test_from_config_sets_database(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter.from_config(
            {
                "motherduck_database": "test_db",
                "motherduck_token": "tok123",
            }
        )
        assert adapter.database == "test_db"
        assert adapter.token == "tok123"

    def test_from_config_with_memory_limit(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter.from_config(
            {
                "motherduck_token": "tok123",
                "memory_limit": "8GB",
            }
        )
        assert adapter.memory_limit == "8GB"


class TestMotherDuckConfigBuilder:
    def test_config_builder_pulls_database_from_credential_manager(self, monkeypatch):
        from benchbox.platforms.motherduck import _build_motherduck_config

        class _FakeCredentialManager:
            def get_platform_credentials(self, key: str):
                assert key == "motherduck"
                return {"database": "wizard_saved_db", "token_env_var": "MOTHERDUCK_TOKEN"}

        monkeypatch.setattr(
            "benchbox.security.credentials.CredentialManager",
            lambda: _FakeCredentialManager(),
        )

        config = _build_motherduck_config(
            "motherduck",
            options={},
            overrides={},
            info=None,
        )

        assert config.options["database"] == "wizard_saved_db"
        assert config.options["token_env_var"] == "MOTHERDUCK_TOKEN"

    def test_explicit_cli_database_overrides_credential_value(self, monkeypatch):
        from benchbox.platforms.motherduck import _build_motherduck_config

        class _FakeCredentialManager:
            def get_platform_credentials(self, key: str):
                return {"database": "saved_db"}

        monkeypatch.setattr(
            "benchbox.security.credentials.CredentialManager",
            lambda: _FakeCredentialManager(),
        )

        config = _build_motherduck_config(
            "motherduck",
            options={},
            overrides={"_explicit_platform_options": {"database": "cli_db"}},
            info=None,
        )

        assert config.options["database"] == "cli_db"

    def test_wizard_database_reaches_adapter_from_runtime_config(self, monkeypatch):
        from benchbox.core.platform_config import get_platform_config
        from benchbox.platforms.motherduck import MotherDuckAdapter, _build_motherduck_config

        class _FakeCredentialManager:
            def get_platform_credentials(self, key: str):
                assert key == "motherduck"
                return {"database": "wizard_saved_db", "token_env_var": "MOTHERDUCK_TOKEN"}

        monkeypatch.setattr(
            "benchbox.security.credentials.CredentialManager",
            lambda: _FakeCredentialManager(),
        )
        monkeypatch.setenv("MOTHERDUCK_TOKEN", "env-token")

        database_config = _build_motherduck_config(
            "motherduck",
            options={},
            overrides={"benchmark": "tpch", "scale_factor": 0.01},
            info=None,
        )
        runtime_config = get_platform_config(database_config, None, benchmark_name="tpch", scale_factor=0.01)

        adapter = MotherDuckAdapter.from_config(runtime_config)

        assert runtime_config["database"] == "wizard_saved_db"
        assert adapter.database == "wizard_saved_db"


class TestMotherDuckCreateConnection:
    def test_create_connection_uses_md_scheme(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="mytoken", database="mydb")

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (1,)

        with patch("benchbox.platforms.motherduck.duckdb") as mock_duckdb:
            mock_duckdb.connect.return_value = mock_conn
            conn = adapter.create_connection()

        call_args = mock_duckdb.connect.call_args[0][0]
        assert "md:mydb" in call_args
        assert "mytoken" in call_args
        assert conn is mock_conn
        assert adapter.connection is mock_conn

    def test_create_connection_cached(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="mytoken")
        mock_conn = MagicMock()
        adapter.connection = mock_conn

        with patch("benchbox.platforms.motherduck.duckdb") as mock_duckdb:
            conn = adapter.create_connection()

        mock_duckdb.connect.assert_not_called()
        assert conn is mock_conn

    def test_create_connection_redacts_token_from_error_and_log(self, caplog):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="secret-token", database="benchbox")

        with (
            patch("benchbox.platforms.motherduck.duckdb") as mock_duckdb,
            caplog.at_level(logging.ERROR, logger="benchbox.platforms.motherduck"),
            pytest.raises(ConnectionError) as exc_info,
        ):
            mock_duckdb.connect.side_effect = RuntimeError(
                "failed to connect to md:benchbox?motherduck_token=secret-token"
            )
            adapter.create_connection()

        assert "secret-token" not in str(exc_info.value)
        assert "secret-token" not in caplog.text
        assert "motherduck_token=****" in str(exc_info.value)
        assert "motherduck_token=****" in caplog.text

    def test_create_connection_does_not_leak_token_through_chained_cause(self):
        import traceback

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="secret-token", database="benchbox")

        with (
            patch("benchbox.platforms.motherduck.duckdb") as mock_duckdb,
            pytest.raises(ConnectionError) as exc_info,
        ):
            mock_duckdb.connect.side_effect = RuntimeError(
                "failed to connect to md:benchbox?motherduck_token=secret-token"
            )
            adapter.create_connection()

        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True

        rendered = "".join(
            traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.value.__traceback__)
        )
        assert "secret-token" not in rendered
        assert "motherduck_token=****" in rendered


class TestMotherDuckAddCliArguments:
    def test_motherduck_database_arg_added(self):
        import argparse

        from benchbox.platforms.motherduck import MotherDuckAdapter

        parser = argparse.ArgumentParser()
        MotherDuckAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--motherduck-database", "mydb"])
        assert args.motherduck_database == "mydb"

    def test_motherduck_token_arg_added(self):
        import argparse

        from benchbox.platforms.motherduck import MotherDuckAdapter

        parser = argparse.ArgumentParser()
        MotherDuckAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--motherduck-token", "tok"])
        assert args.motherduck_token == "tok"


class TestMotherDuckGetPlatformInfo:
    def test_get_platform_info_has_name_and_version(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        info = adapter.get_platform_info()
        assert info["platform_name"] == "MotherDuck"

    def test_get_platform_info_with_active_connection(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("v24.1.0",)

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_version"] == "v24.1.0"
        assert info["engine_version"] == "v24.1.0"

    def test_get_platform_info_version_probe_exception_silenced(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("no response")

        info = adapter.get_platform_info(connection=mock_conn)
        assert info["platform_name"] == "MotherDuck"


class TestMotherDuckExecuteQuery:
    def test_execute_query_success(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [(1,), (2,)]

        result = adapter.execute_query(mock_conn, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["query_id"] == "Q1"
        assert result["execution_time_seconds"] >= 0.0

    def test_execute_query_creates_connection_when_none(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch.object(adapter, "create_connection", return_value=mock_conn) as mock_create:
            result = adapter.execute_query(None, "SELECT 1", "Q1")

        mock_create.assert_called_once()
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0

    def test_execute_query_returns_failed_dict_on_exception(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("query failed")

        result = adapter.execute_query(mock_conn, "SELECT bad", "Q_err")

        assert result["status"] == "FAILED"
        assert "query failed" in result["error"]
        assert result["error_type"] == "RuntimeError"


class TestMotherDuckTestConnection:
    def test_test_connection_success(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (1,)

        with patch.object(adapter, "create_connection", return_value=mock_conn):
            assert adapter.test_connection() is True

    def test_test_connection_failure(self):
        from unittest.mock import patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")

        with patch.object(adapter, "create_connection", side_effect=ConnectionError("refused")):
            assert adapter.test_connection() is False


class TestMotherDuckConfigureForBenchmark:
    def test_configure_sets_memory_limit(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok", memory_limit="8GB")
        mock_conn = MagicMock()

        adapter.configure_for_benchmark(mock_conn, "olap")

        mock_conn.execute.assert_called_once_with("SET memory_limit = '8GB'")

    def test_configure_silences_set_error(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("read-only")

        adapter.configure_for_benchmark(mock_conn, "olap")


class TestMotherDuckCloseConnection:
    def test_close_external_connection_does_not_clear_self(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        internal_conn = MagicMock()
        adapter.connection = internal_conn
        external_conn = MagicMock()

        adapter.close_connection(connection=external_conn)

        external_conn.close.assert_called_once()
        assert adapter.connection is internal_conn

    def test_close_self_connection_clears_state(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        adapter.connection = mock_conn

        adapter.close_connection()

        mock_conn.close.assert_called_once()
        assert adapter.connection is None


class TestMotherDuckCoverageW15:
    def test_create_connection_failure_raises_connection_error(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="bad_token")

        with patch("benchbox.platforms.motherduck.duckdb") as mock_duckdb:
            mock_duckdb.connect.side_effect = RuntimeError("invalid token")
            with pytest.raises(ConnectionError, match="Failed to connect to MotherDuck"):
                adapter.create_connection()

    def test_close_connection_logs_on_error(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("already closed")
        adapter.connection = mock_conn
        adapter.close_connection()
        assert adapter.connection is None

    def test_get_platform_info_with_connection_version_probe(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("v1.2.3",)

        info = adapter.get_platform_info(connection=mock_conn)
        assert info["platform_version"] == "v1.2.3"

    def test_get_platform_info_version_probe_exception_ignored(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("no connection")

        info = adapter.get_platform_info(connection=mock_conn)
        assert info["platform_type"] == "motherduck"

    def test_get_platform_metadata_with_connection(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("v23.1.0",)
        adapter.connection = mock_conn

        metadata = adapter.get_platform_metadata()
        assert metadata["duckdb_version"] == "v23.1.0"

    def test_get_platform_metadata_version_error_silenced(self):
        from unittest.mock import MagicMock

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("fail")
        adapter.connection = mock_conn

        metadata = adapter.get_platform_metadata()
        assert "duckdb_version" not in metadata

    def test_load_data_inserts_parquet_files(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter(token="tok")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (42,)

        (tmp_path / "lineitem.parquet").write_bytes(b"fake")
        (tmp_path / "orders.parquet").write_bytes(b"fake")

        mock_benchmark = MagicMock()

        row_counts, load_time, manifest = adapter.load_data(mock_benchmark, mock_conn, tmp_path)

        assert "lineitem" in row_counts
        assert "orders" in row_counts
        assert row_counts["lineitem"] == 42
        assert load_time >= 0.0

    def test_from_config_maps_token_and_database(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        adapter = MotherDuckAdapter.from_config(
            {
                "motherduck_token": "my_token",
                "motherduck_database": "my_db",
            }
        )
        assert adapter.token == "my_token"
        assert adapter.database == "my_db"


