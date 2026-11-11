from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from benchbox.core.contracts import (
    CapabilityContractError,
    CategorizedQueryBenchmark,
    DataFrameBenchmarkExecutor,
    SQLBenchmarkExecutor,
    as_connection_lifecycle,
    as_dataframe_benchmark_executor,
    as_dataframe_query_runtime,
    as_external_table_loader,
    as_native_table_loader,
    as_plan_capture_runtime,
    as_sql_benchmark_executor,
    as_statistics_phase_runner,
    as_tuning_ledger_writer,
    run_categorized_query_benchmark,
)
from benchbox.core.plan_capture_phase import run_plan_capture_phase

if TYPE_CHECKING:
    from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _requires_sql_executor(candidate: SQLBenchmarkExecutor) -> None:
    pass


def _requires_dataframe_executor(candidate: DataFrameBenchmarkExecutor) -> None:
    pass


def _requires_categorized_query_benchmark(candidate: CategorizedQueryBenchmark) -> None:
    pass


if TYPE_CHECKING:

    def _specialized_benchmark_type_island(read_primitives: ReadPrimitivesBenchmark) -> None:
        _requires_categorized_query_benchmark(read_primitives)


class _ThirdPartySQLAdapter:
    def run_benchmark(self, benchmark: Any, **run_config: Any) -> dict[str, Any]:
        return {"benchmark": benchmark, "config": run_config}


class _ThirdPartyDataFrameAdapter:
    is_dataframe_adapter = True
    platform_name = "third-party-frame"

    def run_benchmark(
        self,
        benchmark: Any,
        *,
        benchmark_config: Any = None,
        system_profile: Any = None,
        data_dir: Path | None = None,
        phases: Any = None,
        options: Any = None,
        monitor: Any = None,
        **run_config: Any,
    ) -> dict[str, Any]:
        return {"benchmark": benchmark, "config": benchmark_config, "extra": run_config}

    def create_context(self) -> dict[str, Any]:
        return {}

    def load_table(
        self,
        ctx: Any,
        table_name: str,
        file_paths: list[Path],
        column_names: list[str] | None = None,
        delimiter: str | None = None,
        format_hint: str | None = None,
        *,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        ctx[table_name] = file_paths
        return len(file_paths)

    def execute_query(self, ctx: Any, query: Any, query_id: str | None = None) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS"}


class _LoadAndEvidenceAdapter:
    supports_external_tables = True

    def create_connection(self, **connection_config: Any) -> object:
        return object()

    def close_connection(self, connection: Any) -> None:
        pass

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        return 0.0

    def load_data(
        self, benchmark: Any, connection: Any, data_dir: Any
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return {"table": 1}, 0.0, None

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Any
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return {"external": 1}, 0.0, None

    def run_statistics_phase(self, benchmark: Any, connection: Any, **options: Any) -> object:
        return object()

    def _write_applied_tuning_ledger(self, builder: Any) -> None:
        builder["tuning"] = True


class _CloudPlanCaptureAdapter:
    analyze_plans = True
    capture_plans = False
    normalize_plan_literals = True
    plan_capture_errors: list[dict[str, Any]] = []

    class _Connection:
        def close(self) -> None:
            pass

    def create_connection(self, **connection_config: Any) -> _Connection:
        return self._Connection()

    def capture_query_plan(self, connection: Any, query: str, query_id: str) -> tuple[Any, float]:
        return SimpleNamespace(plan_fingerprint=f"fp:{query_id}", normalized_fingerprint="normalized"), 1.0


class _MinimalSuppliedConnectionCapture:
    analyze_plans = True
    capture_plans = False

    def capture_query_plan(self, connection: Any, query: str, query_id: str) -> tuple[Any, float]:
        return SimpleNamespace(plan_fingerprint=query_id, normalized_fingerprint="unused"), 0.5


class _CategorizedDirectRunner:
    def run_benchmark(
        self,
        connection: Any,
        queries: list[str] | None = None,
        iterations: int = 1,
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"connection": connection, "queries": queries, "iterations": iterations, "categories": categories}


def test_structural_sql_and_dataframe_extensions_need_no_protocol_inheritance() -> None:
    sql_adapter = _ThirdPartySQLAdapter()
    dataframe_adapter = _ThirdPartyDataFrameAdapter()

    _requires_sql_executor(sql_adapter)
    _requires_dataframe_executor(dataframe_adapter)
    assert as_sql_benchmark_executor(sql_adapter).run_benchmark("tpch")["benchmark"] == "tpch"
    assert as_dataframe_benchmark_executor(dataframe_adapter).run_benchmark("tpch")["benchmark"] == "tpch"
    assert as_dataframe_query_runtime(dataframe_adapter).platform_name == "third-party-frame"


def test_dataframe_execution_mode_selection_stays_a_caller_responsibility() -> None:
    assert as_dataframe_benchmark_executor(_ThirdPartySQLAdapter()) is not None


def test_loading_statistics_and_tuning_remain_independent_capabilities() -> None:
    adapter = _LoadAndEvidenceAdapter()
    builder: dict[str, Any] = {}

    assert as_connection_lifecycle(adapter).create_connection() is not None
    assert as_native_table_loader(adapter).load_data(None, None, None)[0] == {"table": 1}
    assert as_external_table_loader(adapter).create_external_tables(None, None, None)[0] == {"external": 1}
    assert as_statistics_phase_runner(adapter).run_statistics_phase(None, None) is not None
    as_tuning_ledger_writer(adapter)._write_applied_tuning_ledger(builder)
    assert builder == {"tuning": True}


def test_specialized_categorized_direct_runner_has_a_production_boundary() -> None:
    runner = _CategorizedDirectRunner()
    _requires_categorized_query_benchmark(runner)

    result = run_categorized_query_benchmark(runner, "connection", "window", 3)

    assert result == {
        "connection": "connection",
        "queries": None,
        "iterations": 3,
        "categories": ["window"],
    }


def test_cloud_plan_capture_consumer_accepts_structural_adapter_and_restores_flags() -> None:
    adapter = _CloudPlanCaptureAdapter()
    assert as_plan_capture_runtime(adapter) is adapter

    result = run_plan_capture_phase(adapter, {"q1": "SELECT 1"}, analyze_plans=False)

    assert result.fingerprints == {"q1": "fp:q1"}
    assert result.normalized_fingerprints == {"q1": "normalized"}
    assert adapter.analyze_plans is True
    assert adapter.capture_plans is False


def test_plan_capture_with_supplied_connection_keeps_optional_members_optional() -> None:
    adapter = _MinimalSuppliedConnectionCapture()

    result = run_plan_capture_phase(adapter, {"q1": "SELECT 1"}, connection=object())

    assert result.fingerprints == {"q1": "q1"}
    assert result.normalized_fingerprints == {}


def test_plan_capture_without_supplied_connection_requires_factory() -> None:
    adapter = _MinimalSuppliedConnectionCapture()

    with pytest.raises(CapabilityContractError, match=r"connection creation; missing: create_connection"):
        run_plan_capture_phase(adapter, {"q1": "SELECT 1"})


def test_missing_capability_reports_exact_members() -> None:
    with pytest.raises(CapabilityContractError, match=r"missing: create_connection, close_connection"):
        as_connection_lifecycle(object())


def test_non_callable_operation_fails_at_capability_boundary() -> None:
    candidate = SimpleNamespace(run_benchmark=None)

    with pytest.raises(CapabilityContractError, match=r"SQL benchmark execution; not callable: run_benchmark"):
        as_sql_benchmark_executor(candidate)


