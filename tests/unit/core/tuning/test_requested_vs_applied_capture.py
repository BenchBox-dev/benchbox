# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    EXECUTED,
    PHASE_DDL,
    AppliedTuningLedger,
    recording_connection,
)
from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _RecordingConnection:
    def __init__(self) -> None:
        self.executed_statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed_statements.append(sql)


class _PartiallyApplyingStubAdapter(PlatformAdapter):
    def __init__(self, **config: Any):
        super().__init__(**config)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "StubPlatform"

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config):
        return _RecordingConnection()

    def create_schema(self, benchmark, connection):
        connection.execute("CREATE TABLE schema_table (id INTEGER)")
        return 0.0

    def load_data(self, benchmark, connection, data_dir):
        return {"orders": 1}, 0.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        return None

    def execute_query(self, connection, query, query_id, **kwargs):
        return {"query_id": query_id, "status": "SUCCESS"}

    def apply_platform_optimizations(self, platform_config, connection):
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        return None

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        recording = recording_connection(connection, getattr(self, "_applied_tuning_ledger", None), PHASE_DDL)
        recording.execute("APPLY CONSTRAINTS")

    def save_tuning_metadata(self, connection: Any) -> bool:
        return True


def _requested_config_with_table_tunings() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["orders"] = TableTuning(
        table_name="orders",
        sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
    )
    config.table_tunings["customer"] = TableTuning(
        table_name="customer",
        sorting=[TuningColumn(name="c_custkey", type="INTEGER", order=1)],
    )
    return config


class TestAppliedLedgerCapturesExecutedStatements:
    def _run_setup_phase(self, effective_config: UnifiedTuningConfiguration, tmp_path):
        adapter = _PartiallyApplyingStubAdapter(
            tuning_enabled=True,
            unified_tuning_configuration=effective_config,
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = adapter.create_connection()
        benchmark = SimpleNamespace(output_dir=tmp_path)

        (
            _schema_time,
            _schema_creation_phase,
            _loading_time,
            _table_stats,
            _data_loading_phase,
            tuning_metadata_saved,
        ) = adapter._setup_fresh_database_phases(benchmark, connection, effective_config)

        return adapter, connection, tuning_metadata_saved

    def test_only_the_executed_statement_reaches_the_connection(self, tmp_path) -> None:
        effective_config = _requested_config_with_table_tunings()
        _adapter, connection, _saved = self._run_setup_phase(effective_config, tmp_path)

        assert connection.executed_statements == ["CREATE TABLE schema_table (id INTEGER)", "APPLY CONSTRAINTS"]

    def test_ledger_captures_exactly_what_executed(self, tmp_path) -> None:
        effective_config = _requested_config_with_table_tunings()
        adapter, connection, _saved = self._run_setup_phase(effective_config, tmp_path)

        ledger = adapter._applied_tuning_ledger
        assert [s.statement for s in ledger.executed_statements] == ["APPLY CONSTRAINTS"]
        assert all(s.phase == PHASE_DDL and s.status == EXECUTED for s in ledger.executed_statements)

    def test_status_is_applied_unverified_from_the_ledger(self, tmp_path) -> None:
        effective_config = _requested_config_with_table_tunings()
        adapter, _connection, _saved = self._run_setup_phase(effective_config, tmp_path)

        status = adapter._applied_tuning_ledger.overall_status(
            tuning_enabled=adapter.tuning_enabled,
            has_config=bool(effective_config),
        )
        assert status == APPLIED_UNVERIFIED

    def test_requested_config_export_is_preserved_additively(self, tmp_path) -> None:
        effective_config = _requested_config_with_table_tunings()
        adapter, connection, _saved = self._run_setup_phase(effective_config, tmp_path)

        tunings_applied_dict = effective_config.to_dict()
        assert set(tunings_applied_dict["table_tunings"]) == {"orders", "customer"}
        assert connection.executed_statements == ["CREATE TABLE schema_table (id INTEGER)", "APPLY CONSTRAINTS"]
        assert [s.statement for s in adapter._applied_tuning_ledger.executed_statements] == ["APPLY CONSTRAINTS"]

    def test_metadata_save_does_not_drive_the_tuning_status(self, tmp_path) -> None:
        effective_config = _requested_config_with_table_tunings()
        adapter, connection, tuning_metadata_saved = self._run_setup_phase(effective_config, tmp_path)

        assert tuning_metadata_saved is True
        assert len(connection.executed_statements) == 2
        assert adapter._applied_tuning_ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED


class _SelfRecordingSchemaAdapter(_PartiallyApplyingStubAdapter):
    def _record_tuned_sort_key_op(self, *args: Any, **kwargs: Any) -> None:
        return None

    def create_schema(self, benchmark, connection):
        statement = "CREATE TABLE schema_table (id INTEGER)"
        connection.execute(statement)
        self._applied_tuning_ledger.record(statement, PHASE_DDL)
        return 0.0


def test_schema_renderer_ledger_owner_prevents_duplicate_capture(tmp_path) -> None:
    config = _requested_config_with_table_tunings()
    adapter = _SelfRecordingSchemaAdapter(tuning_enabled=True, unified_tuning_configuration=config)
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    connection = adapter.create_connection()

    adapter._setup_fresh_database_phases(SimpleNamespace(output_dir=tmp_path), connection, config)

    assert connection.executed_statements == ["CREATE TABLE schema_table (id INTEGER)", "APPLY CONSTRAINTS"]
    assert [statement.statement for statement in adapter._applied_tuning_ledger.executed_statements] == [
        "CREATE TABLE schema_table (id INTEGER)",
        "APPLY CONSTRAINTS",
    ]
