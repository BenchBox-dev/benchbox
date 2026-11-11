from __future__ import annotations

import sqlite3
import types
from pathlib import Path

import pytest

from benchbox.cli.tuning_runtime import build_baseline_unified_config, infer_runtime_tuning_mode
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.metadata import TuningMetadataManager
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REFUSAL = "Refusing to reuse a tuned database for a notuning run"


def _db_path(tmp_path: Path) -> str:
    return str(tmp_path / "marker.db")


def _tuned_adapter(db: str, config: UnifiedTuningConfiguration) -> SQLiteAdapter:
    return SQLiteAdapter(database_path=db, tuning_enabled=True, tuning_config=config)


def _notuning_adapter(db: str) -> SQLiteAdapter:
    return SQLiteAdapter(database_path=db)


def _column_tuned_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
    )
    return config


def _sorted_ingestion_only_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.platform_optimizations.sorted_ingestion_mode = "auto"
    return config


def _fresh_benchmark(tmp_path: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(output_dir=str(tmp_path))


def test_failed_metadata_save_still_refuses_notuning_reuse(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    try:
        assert tuned.ensure_tuned_run_marker(conn) is True
        monkeypatch.setattr(TuningMetadataManager, "save_unified_tunings", lambda self, config: False)
        assert tuned.save_tuning_metadata(conn) is False
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


def test_crash_between_apply_and_save_refuses_notuning_reuse(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    try:
        assert tuned.ensure_tuned_run_marker(conn) is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


def test_notuning_handle_existing_database_recreates_marker_db(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    try:
        assert tuned.ensure_tuned_run_marker(conn) is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    plain.handle_existing_database(database_path=db)
    assert plain.database_was_reused is False


def test_native_fresh_path_writes_marker_before_apply(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    tuned.create_schema = lambda benchmark, connection: 0.0
    tuned.load_data = lambda benchmark, connection, data_dir: ({"orders": 5}, 0.0, None)
    seen: dict[str, bool] = {}

    def _fake_apply(config, connection):
        seen["marker_at_apply"] = TuningMetadataManager(tuned, connection=connection).has_tuned_run_marker()

    tuned.apply_unified_tuning = _fake_apply
    try:
        tuned._setup_fresh_database_phases(_fresh_benchmark(tmp_path), conn, tuned.get_effective_tuning_configuration())
    finally:
        tuned.close_connection(conn)
    assert seen.get("marker_at_apply") is True


def test_marker_write_failure_fails_tuned_run_before_apply(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    tuned.create_schema = lambda benchmark, connection: 0.0
    applied: list[bool] = []
    tuned.apply_unified_tuning = lambda config, connection: applied.append(True)
    tuned.ensure_tuned_run_marker = lambda connection: False
    try:
        with pytest.raises(RuntimeError, match="tuned-run marker"):
            tuned._setup_fresh_database_phases(
                _fresh_benchmark(tmp_path), conn, tuned.get_effective_tuning_configuration()
            )
    finally:
        tuned.close_connection(conn)
    assert applied == []


def test_external_tuned_path_writes_marker_first(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    tuned.table_mode = "external"
    tuned.supports_external_tables = True
    created: list[bool] = []
    tuned.create_external_tables = lambda benchmark, connection, data_dir: (
        created.append(True),
        ({"orders": 5}, 0.0, None),
    )[1]
    conn = tuned.create_connection()
    try:
        tuned._setup_fresh_database_phases(_fresh_benchmark(tmp_path), conn, tuned.get_effective_tuning_configuration())
    finally:
        tuned.close_connection(conn)
    assert created == [True]
    check = sqlite3.connect(db)
    try:
        rows = check.execute(
            "SELECT COUNT(*) FROM benchbox_tuning_metadata "
            "WHERE table_name = '__benchbox_tuning_sections__' AND tuning_type = 'run_kind'"
        ).fetchone()
    finally:
        check.close()
    assert rows[0] == 1


def test_external_tuned_path_marker_failure_blocks_run(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    tuned.table_mode = "external"
    tuned.supports_external_tables = True
    created: list[bool] = []
    tuned.create_external_tables = lambda benchmark, connection, data_dir: (
        created.append(True),
        ({"orders": 5}, 0.0, None),
    )[1]
    tuned.ensure_tuned_run_marker = lambda connection: False
    conn = tuned.create_connection()
    try:
        with pytest.raises(RuntimeError, match="tuned-run marker"):
            tuned._setup_fresh_database_phases(
                _fresh_benchmark(tmp_path), conn, tuned.get_effective_tuning_configuration()
            )
    finally:
        tuned.close_connection(conn)
    assert created == []


def test_constraints_only_config_refused_when_section_markers_swallowed(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    config = UnifiedTuningConfiguration()
    assert config.get_enabled_tuning_types()
    tuned = _tuned_adapter(db, config)
    conn = tuned.create_connection()
    try:
        assert tuned.ensure_tuned_run_marker(conn) is True

        def _fail_section_markers(self, unified_config):
            self.marker_save_failed = True
            return False

        monkeypatch.setattr(TuningMetadataManager, "_save_section_markers", _fail_section_markers)
        assert tuned.save_tuning_metadata(conn) is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


def test_committed_clear_plus_failed_section_markers_rewrites_marker(tmp_path, monkeypatch):
    db = _db_path(tmp_path)
    config = UnifiedTuningConfiguration()
    tuned = _tuned_adapter(db, config)
    conn = tuned.create_connection()
    try:
        assert tuned.ensure_tuned_run_marker(conn) is True
        manager = TuningMetadataManager(tuned, connection=conn)
        assert manager.has_tuned_run_marker() is True
        assert manager.clear_tunings() is True
        conn.commit()
        assert manager.has_tuned_run_marker() is False

        def _fail_section_markers(self, unified_config):
            self.marker_save_failed = True
            return False

        monkeypatch.setattr(TuningMetadataManager, "_save_section_markers", _fail_section_markers)
        assert tuned.save_tuning_metadata(conn) is True
        assert manager.has_tuned_run_marker() is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


def test_sorted_ingestion_only_config_infers_tuned_and_refuses_reuse(tmp_path):
    config = _sorted_ingestion_only_config()
    assert not config.get_enabled_tuning_types()

    enabled, mode = infer_runtime_tuning_mode(config)
    assert enabled is True
    assert mode == "tuned"

    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, config)
    conn = tuned.create_connection()
    try:
        assert tuned.save_tuning_metadata(conn) is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


def test_baseline_needs_no_metadata_table(tmp_path):
    db = _db_path(tmp_path)
    baseline = SQLiteAdapter(database_path=db, tuning_enabled=False, tuning_config=build_baseline_unified_config())
    conn = baseline.create_connection()
    try:
        assert baseline.ensure_tuned_run_marker(conn) is True
    finally:
        baseline.close_connection(conn)

    check = sqlite3.connect(db)
    try:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        check.close()
    assert "benchbox_tuning_metadata" not in tables

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert not any(REFUSAL in error for error in result.errors)


def test_tuned_with_full_metadata_refusal_unchanged(tmp_path):
    db = _db_path(tmp_path)
    tuned = _tuned_adapter(db, _column_tuned_config())
    conn = tuned.create_connection()
    try:
        assert tuned.save_tuning_metadata(conn) is True
        assert TuningMetadataManager(tuned, connection=conn).has_tuned_run_marker() is True
    finally:
        tuned.close_connection(conn)

    plain = _notuning_adapter(db)
    result = plain._validate_database_tunings(database_path=db)
    assert any(REFUSAL in error for error in result.errors)


