from __future__ import annotations

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import ExecutionPhases, MaintenanceOperation, MaintenanceTestPhase, SetupPhase
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceOperation
from benchbox.platforms.base.result_capture import ResultCaptureMixin
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

METHOD = "dsdgen_refresh_sets"


class _Host(ResultCaptureMixin):
    pass


def _operation() -> MaintenanceOperation:
    return MaintenanceOperation(
        operation="LF_SS",
        operation_type="INSERT",
        table="store_sales",
        execution_time_ms=12,
        rows_affected=144,
        status="SUCCESS",
        refresh_set=1,
        staged_rows={"s_purchase": 12, "s_purchase_lineitem": 144},
        rows_by_table={"store_sales": 144},
    )


def _results(method: str | None):
    phase = MaintenanceTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:01",
        duration_ms=1000,
        maintenance_operations=[_operation()],
        query_executions=[],
        maintenance_method=method,
        success=True,
    )
    return make_benchmark_results(
        platform="duckdb",
        benchmark_id="tpcds",
        benchmark_name="TPC-DS",
        execution_phases=ExecutionPhases(setup=SetupPhase(), maintenance_test=phase),
    )


def test_method_and_operation_evidence_appear_in_bundle():
    payload = build_result_payload(_results(METHOD))
    phase = payload["phases"]["maintenance_test"]
    assert payload["run"]["maintenance_method"] == METHOD
    assert phase["maintenance_method"] == METHOD
    operation = phase["operations"][0]
    assert operation["refresh_set"] == 1
    assert operation["staged_rows"] == {"s_purchase": 12, "s_purchase_lineitem": 144}
    assert operation["rows_by_table"] == {"store_sales": 144}


def test_bundle_loads_back_with_the_phase_intact():
    loaded = reconstruct_benchmark_results(build_result_payload(_results(METHOD)))
    phase = loaded.execution_phases.maintenance_test
    assert phase.maintenance_method == METHOD
    assert phase.success is True
    assert phase.maintenance_operations == [_operation()]


def test_absent_method_is_not_emitted():
    payload = build_result_payload(_results(None))
    assert "maintenance_method" not in payload["run"]
    assert "maintenance_method" not in payload["phases"]["maintenance_test"]
    loaded = reconstruct_benchmark_results(payload)
    assert loaded.execution_phases.maintenance_test.maintenance_method is None


def test_create_maintenance_phase_maps_operation_fields():
    operation = TPCDSMaintenanceOperation(
        operation_type="INSERT",
        table_name="store_sales",
        start_time=1.0,
        end_time=1.5,
        duration=0.5,
        rows_affected=144,
        success=True,
        function="LF_SS",
        refresh_set=2,
        staged_rows={"s_purchase": 12},
        rows_by_table={"store_sales": 144},
    )
    phase = _Host()._create_maintenance_phase(
        {
            "operations": [operation],
            "total_time": 1.25,
            "success": True,
            "maintenance_method": METHOD,
            "start_time": "2026-01-01T00:00:00",
            "end_time": "2026-01-01T00:00:02",
        }
    )
    assert phase.maintenance_method == METHOD
    assert phase.duration_ms == 1250
    assert phase.success is True
    assert phase.maintenance_operations == [
        MaintenanceOperation(
            operation="LF_SS",
            operation_type="INSERT",
            table="store_sales",
            execution_time_ms=500,
            rows_affected=144,
            status="SUCCESS",
            error_message=None,
            refresh_set=2,
            staged_rows={"s_purchase": 12},
            rows_by_table={"store_sales": 144},
        )
    ]


def test_create_maintenance_phase_marks_failed_operation():
    operation = TPCDSMaintenanceOperation(
        operation_type="DELETE",
        table_name="store_sales",
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        rows_affected=0,
        success=False,
        error="boom",
        function="DF_SS",
    )
    phase = _Host()._create_maintenance_phase({"operations": [operation], "success": False})
    assert phase.maintenance_method is None
    assert phase.success is False
    assert phase.maintenance_operations[0].status == "FAILED"
    assert phase.maintenance_operations[0].error_message == "boom"
