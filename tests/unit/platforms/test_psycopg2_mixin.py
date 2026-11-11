# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from benchbox.platforms.base.psycopg2_mixin import PsycopgConnectionMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _MinimalAdapter(PsycopgConnectionMixin):
    schema = "public"

    def __init__(self, **kwargs):
        self.logger = Mock()
        for k, v in kwargs.items():
            setattr(self, k, v)


class _QuestDBLikeAdapter(PsycopgConnectionMixin):
    _max_identifier_length = 127
    schema = "public"

    def __init__(self):
        self.logger = Mock()


class TestValidateIdentifier:
    def test_valid_simple_name(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("lineitem") is True

    def test_valid_with_underscore(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("my_table") is True

    def test_valid_starts_with_underscore(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("_private") is True

    def test_valid_mixed_case(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("MyTable") is True

    def test_valid_with_digits(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("table1") is True

    def test_rejects_empty_string(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("") is False

    def test_rejects_none(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier(None) is False

    def test_rejects_non_string(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier(42) is False

    def test_rejects_starts_with_digit(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("1table") is False

    def test_rejects_hyphen(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("my-table") is False

    def test_rejects_space(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("my table") is False

    def test_rejects_semicolon(self):
        adapter = _MinimalAdapter()
        assert adapter._validate_identifier("table; DROP TABLE") is False

    def test_postgresql_default_limit_at_boundary(self):

        adapter = _MinimalAdapter()
        name_63 = "a" * 63
        assert adapter._validate_identifier(name_63) is True

    def test_postgresql_default_limit_exceeds(self):

        adapter = _MinimalAdapter()
        name_64 = "a" * 64
        assert adapter._validate_identifier(name_64) is False

    def test_questdb_limit_accepts_127(self):

        adapter = _QuestDBLikeAdapter()
        name_127 = "a" * 127
        assert adapter._validate_identifier(name_127) is True

    def test_questdb_limit_accepts_64(self):

        adapter = _QuestDBLikeAdapter()
        name_64 = "a" * 64
        assert adapter._validate_identifier(name_64) is True

    def test_questdb_limit_rejects_128(self):

        adapter = _QuestDBLikeAdapter()
        name_128 = "a" * 128
        assert adapter._validate_identifier(name_128) is False


class TestCloseConnection:
    def test_closes_connection(self):
        adapter = _MinimalAdapter()
        mock_conn = Mock()
        adapter.close_connection(mock_conn)
        mock_conn.close.assert_called_once()

    def test_none_connection_is_noop(self):
        adapter = _MinimalAdapter()

        adapter.close_connection(None)

    def test_close_exception_is_swallowed(self):
        adapter = _MinimalAdapter()
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("already closed")

        adapter.close_connection(mock_conn)
        adapter.logger.debug.assert_called_once()


class TestGetExistingTables:
    def _make_conn(self, rows: list[tuple]) -> Mock:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn

    def test_returns_lowercase_table_names(self):
        adapter = _MinimalAdapter()
        conn = self._make_conn([("LineItem",), ("Orders",), ("Customer",)])
        result = adapter._get_existing_tables(conn)
        assert result == ["lineitem", "orders", "customer"]

    def test_returns_empty_list_when_no_tables(self):
        adapter = _MinimalAdapter()
        conn = self._make_conn([])
        result = adapter._get_existing_tables(conn)
        assert result == []

    def test_queries_information_schema(self):
        adapter = _MinimalAdapter()
        conn = self._make_conn([])
        adapter._get_existing_tables(conn)
        cursor = conn.cursor.return_value
        sql_called = cursor.execute.call_args[0][0]
        assert "information_schema.tables" in sql_called

    def test_passes_schema_as_parameter(self):
        adapter = _MinimalAdapter(schema="analytics")
        conn = self._make_conn([])
        adapter._get_existing_tables(conn)
        cursor = conn.cursor.return_value
        params = cursor.execute.call_args[0][1]
        assert params == ("analytics",)

    def test_returns_empty_list_on_exception(self):
        adapter = _MinimalAdapter()
        mock_conn = Mock()
        mock_conn.cursor.side_effect = Exception("connection lost")
        result = adapter._get_existing_tables(mock_conn)
        assert result == []
        adapter.logger.warning.assert_called_once()

    def test_closes_cursor_on_success(self):
        adapter = _MinimalAdapter()
        conn = self._make_conn([("orders",)])
        adapter._get_existing_tables(conn)
        conn.cursor.return_value.close.assert_called_once()

    def test_closes_cursor_on_execute_exception(self):
        adapter = _MinimalAdapter()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("syntax error")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        result = adapter._get_existing_tables(mock_conn)
        assert result == []
        mock_cursor.close.assert_called_once()


class TestClassConstants:
    def test_default_is_63(self):
        assert PsycopgConnectionMixin._max_identifier_length == 63

    def test_subclass_can_override(self):
        assert _QuestDBLikeAdapter._max_identifier_length == 127

    def test_instance_sees_override(self):
        adapter = _QuestDBLikeAdapter()
        assert adapter._max_identifier_length == 127


