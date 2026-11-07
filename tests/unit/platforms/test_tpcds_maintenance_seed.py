from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.models import ExecutionPhases, SetupPhase
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tpcds.maintenance_operations import DEFAULT_MAINTENANCE_SEED
from benchbox.platforms.base.connection_wrappers import StreamConnectionCapability
from benchbox.platforms.base.execution import TestDriversMixin
from benchbox.platforms.base.result_capture import ResultCaptureMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Driver(TestDriversMixin, ResultCaptureMixin):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR
    platform_name = "stub"

    def __init__(self) -> None:
        self._last_maintenance_test_result = None

    def get_target_dialect(self):
        return None

    def new_stream_connection(self, connection, *, benchmark_type=None):
        return connection

    def _plan_capture_checkpoint(self, connection):
        return None


def _maintenance_result(seed):
    operation = SimpleNamespace(
        operation_type="INSERT",
        table_name="STORE_SALES",
        duration=0.25,
        rows_affected=12,
        success=True,
        error=None,
    )
    return {
        "success": True,
        "seed": seed,
        "start_time": "2026-01-01T00:00:00",
        "end_time": "2026-01-01T00:00:01",
        "total_time": 1.0,
        "insert_operations": 1,
        "update_operations": 0,
        "delete_operations": 0,
        "total_operations": 1,
        "successful_operations": 1,
        "overall_throughput": 1.0,
        "errors": [],
        "operations": [operation],
    }


def _run_phase(run_config):
    driver = _Driver()
    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as maintenance:
        maintenance.return_value.run.side_effect = lambda config: _maintenance_result(config.seed)
        rows = driver._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01, **run_config})
    return driver, maintenance.return_value.run.call_args.args[0], rows


def test_run_seed_reaches_the_maintenance_generator():
    driver, config, rows = _run_phase({"seed": 987})

    assert config.seed == 987
    assert driver._last_maintenance_test_result["seed"] == 987
    assert rows[0]["test_type"] == "maintenance"


def test_run_without_a_seed_records_the_fixed_default():
    driver, config, _ = _run_phase({})

    assert config.seed == DEFAULT_MAINTENANCE_SEED
    assert driver._last_maintenance_test_result["seed"] == DEFAULT_MAINTENANCE_SEED


def test_exported_result_round_trips_the_maintenance_seed(tmp_path):
    driver, _, _ = _run_phase({"seed": 987})
    phase = driver._create_maintenance_phase(driver._last_maintenance_test_result)
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-DS", scale_factor=0.01, test_type="maintenance", benchmark_id="tpcds"),
        platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
    )
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "insert_store_sales",
                "status": "SUCCESS",
                "execution_time_seconds": 0.25,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
                "test_type": "maintenance",
            }
        )
    )
    builder.set_execution_phases(ExecutionPhases(setup=SetupPhase(), maintenance_test=phase))

    payload = build_result_payload(builder.build())

    assert payload["phases"]["maintenance_test"]["seed"] == 987
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload))
    loaded, _ = load_result_file(path)
    assert loaded.execution_phases.maintenance_test.seed == 987
