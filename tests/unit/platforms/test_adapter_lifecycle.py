from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.platforms.base import PlatformAdapter
from benchbox.platforms.base.models import ValidationPhase

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class _LifecycleBenchmark:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.scale_factor = 0.01
        self.tables = {"lineitem": [{"l_orderkey": 1}]}
        self.results: list[SimpleNamespace] = []

    def get_table_names(self) -> list[str]:
        return ["lineitem"]

    def get_schema(self) -> dict[str, dict[str, str]]:
        return {"lineitem": {"l_orderkey": "INTEGER"}}

    def get_queries(self, **_kwargs: Any) -> dict[str, str]:
        return {"Q1": "SELECT 1"}

    def create_enhanced_benchmark_result(self, **kwargs: Any) -> SimpleNamespace:
        result = SimpleNamespace(**kwargs)
        self.results.append(result)
        return result


class _LifecycleAdapter(PlatformAdapter):
    def __init__(self, *, existing_databases: list[bool] | None = None, **config: Any) -> None:
        super().__init__(**config)
        self.existing_databases = list(existing_databases or [])
        self.schema_creations = 0
        self.data_loads = 0
        self.closed_connections = 0

    @staticmethod
    def add_cli_arguments(_parser) -> None:
        pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "lifecycle"

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config: Any) -> Any:
        self.handle_existing_database(**connection_config)
        return SimpleNamespace(closed=False)

    def close_connection(self, connection: Any) -> None:
        connection.closed = True
        self.closed_connections += 1

    def check_database_exists(self, **_connection_config: Any) -> bool:
        return self.existing_databases.pop(0) if self.existing_databases else False

    def _validate_database_compatibility(self, **_connection_config: Any) -> SimpleNamespace:
        return SimpleNamespace(is_valid=True, warnings=[], issues=[], can_reuse=True)

    def get_table_row_count(self, _connection: Any, _table_name: str) -> int:
        return 1

    def _create_enhanced_validation_phase(
        self, _benchmark: Any, _connection: Any, _table_stats: dict[str, int]
    ) -> ValidationPhase:
        return ValidationPhase(
            duration_ms=0,
            row_count_validation="PASSED",
            schema_validation="PASSED",
            data_integrity_checks="PASSED",
            validation_details={},
        )

    def create_schema(self, _benchmark: Any, _connection: Any) -> float:
        self.schema_creations += 1
        return 0.0

    def load_data(self, _benchmark: Any, _connection: Any, _data_dir: Path) -> tuple[dict[str, int], float, None]:
        self.data_loads += 1
        return {"lineitem": 1}, 0.0, None

    def configure_for_benchmark(self, _connection: Any, _benchmark_type: str) -> None:
        pass

    def execute_query(
        self,
        _connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        if self.dry_run_mode:
            self.capture_sql(query, "query")
            return self._build_dry_run_result(query_id)
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.01,
            "rows_returned": 1,
        }

    def apply_table_tunings(self, _table_tuning: Any, _connection: Any) -> None:
        pass

    def generate_tuning_clause(self, _table_tuning: Any) -> str:
        return ""

    def apply_unified_tuning(self, _unified_config: Any, _connection: Any) -> None:
        pass

    def apply_platform_optimizations(self, _platform_config: Any, _connection: Any) -> None:
        pass

    def apply_constraint_configuration(
        self, _primary_key_config: Any, _foreign_key_config: Any, _connection: Any
    ) -> None:
        pass


class _DriftLifecycleAdapter(_LifecycleAdapter):
    def _validate_database_compatibility(self, **connection_config: Any) -> Any:
        from benchbox.core.tuning.metadata import MetadataValidationResult

        drift = MetadataValidationResult()
        drift.add_error("constraints section drifted from expected tuning")
        drift.configuration_mismatches["constraints"] = "hash a != b"
        drift.drifted_sections.add("constraints")
        self._drift_validation_result = drift
        return super()._validate_database_compatibility(**connection_config)


def test_reused_db_drift_check_survives_reset_into_applied_ledger(tmp_path: Path) -> None:

    adapter = _DriftLifecycleAdapter(existing_databases=[True])
    adapter.tuning_enabled = True
    benchmark = _LifecycleBenchmark(tmp_path)

    result = adapter.run_benchmark(benchmark, benchmark_name="tpch")

    assert adapter.database_was_reused is True
    ledger = result.applied_tuning_ledger
    assert ledger is not None, "reused-DB drift_check was dropped before reaching the companion"
    assert ledger["drift_check"]["is_valid"] is False
    assert ledger["drift_check"]["drifted_sections"] == ["constraints"]


def test_sequential_reuse_resets_run_scoped_adapter_state(tmp_path: Path) -> None:
    adapter = _LifecycleAdapter(existing_databases=[True, False])
    benchmark = _LifecycleBenchmark(tmp_path)

    first = adapter.run_benchmark(
        benchmark,
        benchmark_name="tpch",
        capture_plans=True,
        strict_plan_capture=True,
        plan_capture_timeout_seconds=3,
    )

    assert first.table_statistics == {"lineitem": 1}
    assert adapter.database_was_reused is True
    assert adapter.schema_creations == 0
    assert adapter.data_loads == 0
    assert adapter.capture_plans is False
    assert adapter.strict_plan_capture is False
    assert adapter.plan_capture_timeout_seconds == 30

    adapter._last_power_test_result = SimpleNamespace(power_at_size=999.0)
    adapter._last_throughput_test_result = object()
    adapter._sorted_ingestion_applied_tables = ["stale_table"]
    adapter._sorted_ingestion_total_apply_seconds = 12.5
    adapter.query_plans_captured = 7
    adapter.plan_capture_failures = 3
    adapter.plan_capture_errors = [{"query_id": "Q-stale", "message": "stale"}]

    second = adapter.run_benchmark(benchmark, benchmark_name="tpch")

    assert adapter.database_was_reused is False
    assert adapter.schema_creations == 1
    assert adapter.data_loads == 1
    assert adapter.closed_connections == 2
    assert second.power_at_size == 0.0
    assert second.throughput_at_size is None
    assert second.execution_metadata["sorted_ingestion"]["applied_tables"] == []
    assert adapter.query_plans_captured == 0
    assert adapter.plan_capture_failures == 0
    assert adapter.plan_capture_errors == []


def test_dry_run_mode_keeps_mode_but_clears_stale_captured_sql(tmp_path: Path) -> None:
    adapter = _LifecycleAdapter(existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)
    adapter.enable_dry_run()
    adapter.capture_sql("SELECT stale", "query")

    adapter.run_benchmark(benchmark, benchmark_name="tpch")

    assert adapter.dry_run_mode is True
    assert adapter.get_captured_sql() == {"1": "SELECT 1"}


def test_run_benchmark_applies_and_restores_analyze_plans(tmp_path: Path) -> None:

    adapter = _LifecycleAdapter(existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)
    assert adapter.analyze_plans is False

    adapter.run_benchmark(
        benchmark,
        benchmark_name="tpch",
        capture_plans=True,
        analyze_plans=True,
    )

    assert adapter.analyze_plans is False


def test_run_benchmark_leaves_analyze_plans_default_when_run_config_omits_it(tmp_path: Path) -> None:

    adapter = _LifecycleAdapter(existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)

    adapter.run_benchmark(benchmark, benchmark_name="tpch", capture_plans=True, analyze_plans=None)

    assert adapter.analyze_plans is False


class _ThroughputLifecycleAdapter(_LifecycleAdapter):
    def __init__(self, stream_fn: Any, *, stream_timeout: float, **config: Any) -> None:
        super().__init__(**config)
        self._stream_fn = stream_fn
        self._stream_timeout = stream_timeout

    def _dispatch_queries_by_type(self, benchmark: Any, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        import logging

        from benchbox.core.throughput.runner import StreamRunner
        from benchbox.core.tpch.throughput_test import TPCHThroughputTestConfig, TPCHThroughputTestResult

        config = TPCHThroughputTestConfig(num_streams=2, stream_timeout=self._stream_timeout, max_workers=2)
        result = TPCHThroughputTestResult(
            config=config,
            start_time="",
            end_time="",
            total_time=0.0,
            throughput_at_size=0.0,
            streams_executed=0,
            streams_successful=0,
        )
        StreamRunner.execute(self._stream_fn, config, result, logging.getLogger("lifecycle-throughput"))
        self._last_throughput_test_result = result
        return []


def _stream_result(stream_id: int):
    from benchbox.core.throughput.result import ThroughputStreamResult

    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        queries_executed=1,
        queries_successful=1,
        queries_failed=0,
        success=True,
    )


def _wait_for(predicate: Any, timeout: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_connection_stays_open_until_a_timed_out_stream_finishes(tmp_path: Path) -> None:
    import threading

    release = threading.Event()

    def stream_fn(stream_id: int, seed: int, config: Any):
        if stream_id == 1:
            release.wait(timeout=30)
        return _stream_result(stream_id)

    adapter = _ThroughputLifecycleAdapter(stream_fn, stream_timeout=0.2, existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)

    try:
        adapter.run_benchmark(benchmark, benchmark_name="tpch", test_execution_type="throughput")

        assert adapter._post_measurement_contained is True
        assert adapter.closed_connections == 0
        assert adapter.connection is None
        release.set()
        assert _wait_for(lambda: adapter.closed_connections == 1)
    finally:
        release.set()


def test_connection_closes_synchronously_when_no_stream_times_out(tmp_path: Path) -> None:
    def stream_fn(stream_id: int, seed: int, config: Any):
        return _stream_result(stream_id)

    adapter = _ThroughputLifecycleAdapter(stream_fn, stream_timeout=5, existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)

    adapter.run_benchmark(benchmark, benchmark_name="tpch", test_execution_type="throughput")

    assert adapter._post_measurement_contained is False
    assert adapter.closed_connections == 1
