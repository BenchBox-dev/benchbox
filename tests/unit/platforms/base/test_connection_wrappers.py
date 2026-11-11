from __future__ import annotations

import logging

import pytest

from benchbox.platforms.base.connection_wrappers import PlatformAdapterCursor, count_query_rows

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_LOGGER_NAME = "benchbox.platforms.base.connection_wrappers"


def test_preserves_first_row_when_padding_from_rows_returned():
    cursor = PlatformAdapterCursor({"rows_returned": 1, "first_row": (7,)})
    assert cursor.fetchall() == [(7,)]
    assert cursor.fetchone() == (7,)


def test_pads_remaining_cardinality_with_none_after_first_row():
    cursor = PlatformAdapterCursor({"rows_returned": 3, "first_row": (7,)})
    assert cursor.fetchall() == [(7,), (None,), (None,)]


def test_falls_back_to_none_placeholders_without_first_row():
    cursor = PlatformAdapterCursor({"rows_returned": 2})
    assert cursor.fetchall() == [(None,), (None,)]


def test_rows_returned_zero_is_empty_regardless_of_first_row():

    cursor = PlatformAdapterCursor({"rows_returned": 0, "first_row": (7,)})
    assert cursor.fetchall() == []
    assert cursor.fetchone() is None


def test_explicit_rows_list_takes_priority_over_first_row():
    cursor = PlatformAdapterCursor({"rows": [(1,), (2,)], "rows_returned": 1, "first_row": (99,)})
    assert cursor.fetchall() == [(1,), (2,)]


def test_first_row_alone_without_rows_returned():
    cursor = PlatformAdapterCursor({"first_row": (7,)})
    assert cursor.fetchall() == [(7,)]


def test_placeholder_only_fetch_warns_once_and_has_real_rows_is_false(caplog):
    cursor = PlatformAdapterCursor({"query_id": "q_placeholder", "rows_returned": 2})
    assert cursor.has_real_rows is False

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(None,), (None,)]

        assert cursor.fetchall() == [(None,), (None,)]
        assert cursor.rows == [(None,), (None,)]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "q_placeholder" in message
    assert "rows_returned_only" in message


def test_explicit_rows_fetch_never_warns_and_has_real_rows_is_true(caplog):
    cursor = PlatformAdapterCursor({"query_id": "q_explicit", "rows": [(1,), (2,)]})
    assert cursor.has_real_rows is True

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(1,), (2,)]
        assert cursor.fetchone() == (1,)
        assert cursor.rows == [(1,), (2,)]

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_count_only_paths_never_warn_even_with_placeholder_cardinality(caplog):

    cursor = PlatformAdapterCursor({"query_id": "q_count_only", "rows_returned": 5})

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.row_count() == 5
        assert count_query_rows(cursor) == 5

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    assert cursor._rows is None


def test_has_real_rows_access_alone_does_not_warn(caplog):
    cursor = PlatformAdapterCursor({"query_id": "q_inspect", "rows_returned": 3, "first_row": (7,)})

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.has_real_rows is False

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(7,), (None,), (None,)]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_fetchone_on_placeholder_cursor_warns_once_and_shares_budget_with_fetchall(caplog):

    cursor = PlatformAdapterCursor({"query_id": "q_fetchone", "rows_returned": 2})

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchone() == (None,)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(None,), (None,)]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_first_row_present_case_element_zero_real_but_still_warns_on_padding(caplog):

    cursor = PlatformAdapterCursor({"query_id": "q_padded", "rows_returned": 3, "first_row": (7,)})
    assert cursor.has_real_rows is False

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        rows = cursor.fetchall()

    assert rows == [(7,), (None,), (None,)]
    assert rows[0] == (7,)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "q_padded" in message
    assert "first_row+padding" in message


def test_first_row_only_single_real_row_does_not_warn_no_padding_fabricated(caplog):

    cursor = PlatformAdapterCursor({"query_id": "q_first_row_only", "first_row": (7,)})
    assert cursor.has_real_rows is False

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(7,)]

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert cursor._has_placeholder_padding is False
    assert cursor._warned_placeholder_materialization is False


def test_no_padding_when_rows_returned_equals_one_with_first_row(caplog):

    cursor = PlatformAdapterCursor({"query_id": "q_exact_one", "rows_returned": 1, "first_row": (7,)})

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert cursor.fetchall() == [(7,)]

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert cursor._has_placeholder_padding is False
    assert cursor._warned_placeholder_materialization is False


