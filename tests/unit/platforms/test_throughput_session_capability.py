# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.base.connection_wrappers import (
    StreamConnectionCapability,
    open_stream_connection,
    require_throughput_stream_capability,
    resolve_stream_connection_capability,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _BareAdapter(PlatformAdapter):
    @staticmethod
    def add_cli_arguments(parser) -> None:
        pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls()

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config: Any) -> Any:
        return Mock()

    def close_connection(self, connection: Any) -> None:
        pass

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        return 0.0

    def load_data(self, benchmark: Any, connection: Any, data_dir: Any) -> Any:
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        pass

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        pass

    def apply_constraint_configuration(self, primary_key_config: Any, foreign_key_config: Any, connection: Any) -> None:
        pass

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0}


class _SharedAdapter(_BareAdapter):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR


class _IndependentAdapter(_BareAdapter):
    stream_connection_capability = StreamConnectionCapability.INDEPENDENT_CONNECTION

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        handle = Mock()
        handle.benchmark_type = benchmark_type
        return handle


class _IndependentWithoutOverride(_BareAdapter):
    stream_connection_capability = StreamConnectionCapability.INDEPENDENT_CONNECTION


class _UnsupportedAdapter(_BareAdapter):
    stream_connection_capability = StreamConnectionCapability.UNSUPPORTED


class _ChildOfIndependent(_IndependentAdapter):
    pass


class TestResolveStreamConnectionCapability:
    def test_undeclared_resolves_to_shared_default(self):
        capability, declared = resolve_stream_connection_capability(_BareAdapter())
        assert capability is StreamConnectionCapability.SHARED_CURSOR
        assert declared is False

    def test_explicit_shared_declaration_is_detected(self):
        capability, declared = resolve_stream_connection_capability(_SharedAdapter())
        assert capability is StreamConnectionCapability.SHARED_CURSOR
        assert declared is True

    def test_explicit_independent_declaration_is_detected(self):
        capability, declared = resolve_stream_connection_capability(_IndependentAdapter())
        assert capability is StreamConnectionCapability.INDEPENDENT_CONNECTION
        assert declared is True

    def test_deliberate_inheritance_counts_as_declared(self):
        capability, declared = resolve_stream_connection_capability(_ChildOfIndependent())
        assert capability is StreamConnectionCapability.INDEPENDENT_CONNECTION
        assert declared is True

    def test_unsupported_declaration_is_detected(self):
        capability, declared = resolve_stream_connection_capability(_UnsupportedAdapter())
        assert capability is StreamConnectionCapability.UNSUPPORTED
        assert declared is True

    def test_accepts_classes_as_well_as_instances(self):
        assert resolve_stream_connection_capability(_SharedAdapter) == (
            StreamConnectionCapability.SHARED_CURSOR,
            True,
        )

    def test_non_capability_declaration_fails_loudly(self):
        class _Broken(_BareAdapter):
            stream_connection_capability = "independent_connection"

        with pytest.raises(RuntimeError, match="not a StreamConnectionCapability"):
            resolve_stream_connection_capability(_Broken())


class TestRequireThroughputStreamCapability:
    def test_shared_declaration_passes(self):
        assert (
            require_throughput_stream_capability(_SharedAdapter(), platform_name="Shared")
            is StreamConnectionCapability.SHARED_CURSOR
        )

    def test_undeclared_fails_closed_before_submission(self):
        with pytest.raises(RuntimeError, match="no explicit stream_connection_capability"):
            require_throughput_stream_capability(_BareAdapter(), platform_name="Bare")

    def test_independent_with_override_passes(self):
        assert (
            require_throughput_stream_capability(_IndependentAdapter(), platform_name="Indie")
            is StreamConnectionCapability.INDEPENDENT_CONNECTION
        )

    def test_unsupported_fails_before_submission_with_remediation(self):
        with pytest.raises(RuntimeError) as excinfo:
            require_throughput_stream_capability(_UnsupportedAdapter(), platform_name="Nope")
        message = str(excinfo.value)
        assert "Nope" in message
        assert "UNSUPPORTED" in message
        assert "new_stream_connection" in message
        assert "power/single-stream" in message

    def test_independent_without_override_fails_before_submission(self):
        adapter = _IndependentWithoutOverride()
        with pytest.raises(RuntimeError, match="does not override new_stream_connection"):
            require_throughput_stream_capability(adapter, platform_name="HalfWired")
        with pytest.raises(NotImplementedError):
            adapter.new_stream_connection(Mock())

    def test_benchmark_type_threads_to_independent_override(self):
        adapter = _IndependentAdapter()
        handle = adapter.new_stream_connection(Mock(), benchmark_type="olap")
        assert handle.benchmark_type == "olap"

    def test_legacy_positional_override_signature_keeps_working(self):
        adapter = _IndependentAdapter()
        handle = adapter.new_stream_connection(Mock())
        assert handle.benchmark_type is None

    def test_legacy_override_is_called_without_new_keyword(self):
        class _Legacy(_SharedAdapter):
            def new_stream_connection(self, connection: Any) -> Any:
                return connection

        shared = Mock()
        assert open_stream_connection(_Legacy(), shared, "olap") is shared


class TestMySQLWireStreamOverride:
    def test_doris_stream_opens_fresh_connection_with_tuning(self, monkeypatch):
        pytest.importorskip("pymysql")
        import benchbox.platforms.doris as doris_module
        from benchbox.platforms.doris import DorisAdapter

        mock_pymysql = Mock()
        monkeypatch.setattr(doris_module, "pymysql", mock_pymysql)
        stream_connection = Mock()
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        stream_cursor.fetchone.return_value = ("enable_sql_cache", "false")
        mock_pymysql.connect.return_value = stream_connection

        adapter = DorisAdapter()
        shared_connection = Mock()
        with (
            patch.object(adapter, "handle_existing_database") as handle,
            patch.object(adapter, "_create_database") as create,
            patch.object(adapter, "check_server_database_exists") as exists,
        ):
            result = adapter.new_stream_connection(shared_connection, benchmark_type="olap")

        handle.assert_not_called()
        create.assert_not_called()
        exists.assert_not_called()
        mock_pymysql.connect.assert_called_once()
        executed = [str(call) for call in stream_cursor.execute.call_args_list]
        assert any("enable_sql_cache" in call for call in executed), executed
        assert any("exec_mem_limit" in call for call in executed), executed
        shared_connection.cursor.assert_not_called()
        assert result is not stream_connection
        assert result is not shared_connection

    def test_stream_without_benchmark_type_skips_tuning_replay(self, monkeypatch):
        pytest.importorskip("pymysql")
        import benchbox.platforms.doris as doris_module
        from benchbox.platforms.doris import DorisAdapter

        mock_pymysql = Mock()
        monkeypatch.setattr(doris_module, "pymysql", mock_pymysql)
        stream_connection = Mock()
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        stream_cursor.fetchone.return_value = ("enable_sql_cache", "false")
        mock_pymysql.connect.return_value = stream_connection

        adapter = DorisAdapter()
        adapter.new_stream_connection(Mock())

        executed = [str(call) for call in stream_cursor.execute.call_args_list]
        assert executed == ["call('SELECT 1')"], executed

    def test_stream_setup_failure_closes_nothing_shared(self, monkeypatch):
        pytest.importorskip("pymysql")
        import benchbox.platforms.doris as doris_module
        from benchbox.platforms.doris import DorisAdapter

        mock_pymysql = Mock()
        monkeypatch.setattr(doris_module, "pymysql", mock_pymysql)
        mock_pymysql.connect.side_effect = RuntimeError("connection refused")

        adapter = DorisAdapter()
        with pytest.raises(RuntimeError, match="connection refused"):
            adapter.new_stream_connection(Mock())


class TestQuestDBStreamOverride:
    def test_stream_connection_restores_autocommit_and_tuning(self, monkeypatch):
        import benchbox.platforms.questdb as questdb_module
        from benchbox.platforms.questdb import QuestDBAdapter

        mock_psycopg = Mock()
        monkeypatch.setattr(questdb_module, "psycopg", mock_psycopg)
        stream_connection = Mock()
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        mock_psycopg.connect.return_value = stream_connection

        adapter = QuestDBAdapter()
        result = adapter.new_stream_connection(Mock(), benchmark_type="olap")

        assert result is stream_connection
        assert stream_connection.autocommit is True
        executed = [str(call) for call in stream_cursor.execute.call_args_list]
        assert any("SELECT 1" in call for call in executed), executed
        assert any("cairo.sql.parallel.filter.enabled" in call for call in executed), executed

    def test_stream_without_benchmark_type_restores_autocommit_only(self, monkeypatch):
        import benchbox.platforms.questdb as questdb_module
        from benchbox.platforms.questdb import QuestDBAdapter

        mock_psycopg = Mock()
        monkeypatch.setattr(questdb_module, "psycopg", mock_psycopg)
        stream_connection = Mock()
        stream_connection.autocommit = False
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        mock_psycopg.connect.return_value = stream_connection

        adapter = QuestDBAdapter()
        result = adapter.new_stream_connection(Mock())

        assert result is stream_connection
        assert stream_connection.autocommit is True
        executed = [str(call) for call in stream_cursor.execute.call_args_list]
        assert executed == ["call('SELECT 1')"], executed


class TestTimescaleDBStreamHook:
    def test_hook_delegates_to_parent_path(self, monkeypatch):
        import benchbox.platforms.postgresql as postgresql_module
        from benchbox.platforms.timescaledb import TimescaleDBAdapter

        mock_psycopg = Mock()
        monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

        assert "_apply_stream_session_state" in TimescaleDBAdapter.__dict__
        adapter = TimescaleDBAdapter()
        connection = Mock()
        assert adapter._apply_stream_session_state(connection) is None
        connection.cursor.assert_not_called()
        connection.execute.assert_not_called()
