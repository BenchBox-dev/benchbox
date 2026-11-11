# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.cloud_shared import validate_session_cache_control

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_validate_session_cache_control_success():

    mock_conn = Mock()
    mock_cursor = Mock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = ("off",)

    result = validate_session_cache_control(
        connection=mock_conn,
        query="SHOW enable_result_cache_for_session",
        setting_key="enable_result_cache_for_session",
        disabled_value="off",
        enabled_value="on",
        normalize="lower",
        platform_name="Redshift",
        disable_result_cache=True,
        strict_validation=True,
    )

    assert result["validated"] is True
    assert result["cache_disabled"] is True
    assert result["settings"]["enable_result_cache_for_session"] == "off"
    assert not result["errors"]
    assert not result["warnings"]


def test_validate_session_cache_control_with_value_column_index():

    mock_conn = Mock()
    mock_cursor = Mock()
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = ("USE_CACHED_RESULT", "FALSE", "TRUE", "BOOLEAN")

    result = validate_session_cache_control(
        connection=mock_conn,
        query="SHOW PARAMETERS LIKE 'USE_CACHED_RESULT' IN SESSION",
        setting_key="USE_CACHED_RESULT",
        disabled_value="FALSE",
        enabled_value="TRUE",
        normalize="upper",
        platform_name="Snowflake",
        disable_result_cache=True,
        strict_validation=True,
        value_column_index=1,
    )

    assert result["validated"] is True
    assert result["cache_disabled"] is True
    assert result["settings"]["USE_CACHED_RESULT"] == "FALSE"
    assert not result["errors"]


def test_validate_session_cache_control_mismatch_strict():

    mock_conn = Mock()
    mock_cursor = Mock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = ("on",)

    with pytest.raises(ConfigurationError) as exc_info:
        validate_session_cache_control(
            connection=mock_conn,
            query="SHOW enable_result_cache_for_session",
            setting_key="enable_result_cache_for_session",
            disabled_value="off",
            enabled_value="on",
            normalize="lower",
            platform_name="Redshift",
            disable_result_cache=True,
            strict_validation=True,
        )

    assert "Redshift session cache control validation failed" in str(exc_info.value)


def test_validate_session_cache_control_no_row():

    mock_conn = Mock()
    mock_cursor = Mock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None

    result = validate_session_cache_control(
        connection=mock_conn,
        query="SHOW enable_result_cache_for_session",
        setting_key="enable_result_cache_for_session",
        disabled_value="off",
        enabled_value="on",
        normalize="lower",
        platform_name="Redshift",
        disable_result_cache=True,
        strict_validation=False,
    )

    assert result["validated"] is False
    assert len(result["warnings"]) == 1
    assert "Could not retrieve enable_result_cache_for_session" in result["warnings"][0]


class TestSanitizeCacheControlReceipt:
    def test_happy_path_passes_through(self):
        from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

        receipt = {
            "validated": True,
            "cache_disabled": True,
            "settings": {"USE_CACHED_RESULT": "off"},
            "warnings": [],
            "errors": [],
        }
        assert sanitize_cache_control_receipt(receipt) == receipt

        assert sanitize_cache_control_receipt(receipt) is not receipt

    def test_truthy_strings_never_read_as_disabled(self):
        from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

        clean = sanitize_cache_control_receipt({"validated": "yes", "cache_disabled": "False"})
        assert clean is not None
        assert clean["validated"] is False
        assert clean["cache_disabled"] is False

    def test_non_dict_returns_none(self):
        from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

        assert sanitize_cache_control_receipt(None) is None
        assert sanitize_cache_control_receipt("disabled") is None
        assert sanitize_cache_control_receipt(True) is None

    def test_extra_keys_dropped_and_values_stringified(self):
        from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

        clean = sanitize_cache_control_receipt(
            {
                "validated": True,
                "cache_disabled": True,
                "settings": {"k": 1},
                "warnings": [1],
                "errors": None,
                "connection": object(),
            }
        )
        assert clean is not None
        assert "connection" not in clean
        assert clean["settings"] == {"k": "1"}
        assert clean["warnings"] == ["1"]
        assert clean["errors"] == []


class TestExplicitCacheEnabledReceipt:
    def test_records_confirmed_enabled_state(self):
        from benchbox.platforms.cloud_shared import (
            explicit_cache_enabled_receipt,
            sanitize_cache_control_receipt,
        )

        receipt = explicit_cache_enabled_receipt("USE_CACHED_RESULT", "TRUE")
        assert receipt["validated"] is True
        assert receipt["cache_disabled"] is False
        assert receipt["settings"] == {"USE_CACHED_RESULT": "TRUE"}
        assert receipt["errors"] == []
        assert any("explicitly left enabled" in w for w in receipt["warnings"])

        assert sanitize_cache_control_receipt(receipt) == receipt


class TestSplitLeadingSqlComments:
    def test_plain_statement_has_empty_prefix(self):
        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        prefix, body = split_leading_sql_comments("CREATE TABLE t (id INT)")
        assert prefix == ""
        assert body == "CREATE TABLE t (id INT)"

    def test_comment_headers_split_off(self):
        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        chunk = "-- header line\n-- second line\nCREATE TABLE orders_stage (id INT)"
        prefix, body = split_leading_sql_comments(chunk)
        assert prefix == "-- header line\n-- second line\n"
        assert body == "CREATE TABLE orders_stage (id INT)"

    def test_pure_comment_chunk_has_empty_body(self):
        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        prefix, body = split_leading_sql_comments("-- just a comment\n-- another")
        assert body.strip() == ""
        assert prefix != ""

    def test_block_comments_split_off(self):
        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        chunk = "/* header */ /* second */\nCREATE TABLE orders_stage (id INT)"
        prefix, body = split_leading_sql_comments(chunk)
        assert body == "CREATE TABLE orders_stage (id INT)"
        assert prefix == "/* header */ /* second */\n"


