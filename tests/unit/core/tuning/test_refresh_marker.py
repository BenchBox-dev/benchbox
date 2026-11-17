from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from benchbox.core.tuning.metadata import TuningMetadataManager
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _manager(path: Path) -> tuple[TuningMetadataManager, sqlite3.Connection]:
    connection = sqlite3.connect(str(path))
    return TuningMetadataManager(SQLiteAdapter(database_path=str(path)), connection=connection), connection


def test_a_database_with_no_metadata_table_is_not_marked(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        assert manager.has_refresh_marker() is False
    finally:
        connection.close()


def test_the_marker_is_written_once_and_read_back(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        assert manager.write_refresh_marker() is True
        assert manager.write_refresh_marker() is True
        assert manager.has_refresh_marker() is True
        rows = connection.execute("SELECT COUNT(*) FROM benchbox_tuning_metadata").fetchone()[0]
        assert rows == 1
    finally:
        connection.close()


def test_the_marker_is_not_mistaken_for_tuning_metadata(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        manager.write_refresh_marker()
        assert manager.load_tunings() is None
        assert manager.load_unified_tunings() is None
        assert manager.has_tuned_run_marker() is False
    finally:
        connection.close()


def test_clearing_the_marker_leaves_the_database_unmarked(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        manager.write_refresh_marker()
        assert manager.clear_refresh_marker() is True
        assert manager.has_refresh_marker() is False
    finally:
        connection.close()


def test_clearing_a_database_without_a_metadata_table_is_a_no_op(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        assert manager.clear_refresh_marker() is True
    finally:
        connection.close()


def test_an_unrelated_row_with_the_marker_table_name_does_not_count(tmp_path: Path) -> None:
    manager, connection = _manager(tmp_path / "a.sqlite")
    try:
        manager.create_metadata_table()
        connection.execute(
            "INSERT INTO benchbox_tuning_metadata VALUES "
            "('__benchbox_refresh_state__', 'refresh_state', 'refresh_state', 0, 'something-else', "
            "'2026-11-17 00:00:00', 'sqlite')"
        )
        connection.commit()
        assert manager.has_refresh_marker() is False
    finally:
        connection.close()


def test_the_adapter_drops_a_stale_marker_when_a_managed_database_has_no_benchmark_tables(tmp_path: Path) -> None:
    path = tmp_path / "managed.sqlite"
    manager, connection = _manager(path)
    manager.write_refresh_marker()
    connection.close()
    adapter = SQLiteAdapter(database_path=str(path))
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = lambda **_: False

    adapter.handle_existing_database()

    assert adapter.database_was_reused is False
    after, after_connection = _manager(path)
    try:
        assert after.has_refresh_marker() is False
    finally:
        after_connection.close()


def test_the_adapter_refuses_to_reuse_a_managed_database_a_refresh_run_modified(tmp_path: Path) -> None:
    path = tmp_path / "managed.sqlite"
    manager, connection = _manager(path)
    manager.write_refresh_marker()
    connection.close()
    adapter = SQLiteAdapter(database_path=str(path))
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = lambda **_: True

    with pytest.raises(RuntimeError, match="refresh functions"):
        adapter.handle_existing_database()
