from __future__ import annotations

import pytest

from benchbox.platforms.base import (
    ConnectionConfig,
    DatabaseValidationResult,
    MaintenanceOperation,
    MaintenanceTestPhase,
    QueryExecution,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_connection_config_env_resolution(monkeypatch):
    cfg = ConnectionConfig(password="${BENCHBOX_TEST_SECRET}")
    monkeypatch.setenv("BENCHBOX_TEST_SECRET", "s3cr3t")

    assert cfg.get_env_value("password") == "s3cr3t"


def test_database_validation_result_defaults():
    result = DatabaseValidationResult(is_valid=False, can_reuse=False, issues=["missing"], warnings=["warn"])

    assert result.tuning_valid is None
    assert result.tables_valid is None
    assert result.row_counts_valid is None


def test_maintenance_phase_models_roundtrip():
    execution = QueryExecution(
        query_id="Q1",
        stream_id="power_test",
        execution_order=1,
        execution_time_ms=1200,
        status="SUCCESS",
    )
    maintenance = MaintenanceOperation(
        operation="RF1",
        operation_type="INSERT",
        table="lineitem",
        execution_time_ms=450,
        rows_affected=100,
        status="SUCCESS",
    )
    phase = MaintenanceTestPhase(
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:10:00Z",
        duration_ms=600000,
        maintenance_operations=[maintenance],
        query_executions=[execution],
    )

    assert phase.maintenance_operations[0].table == "lineitem"
    assert phase.query_executions[0].query_id == "Q1"


