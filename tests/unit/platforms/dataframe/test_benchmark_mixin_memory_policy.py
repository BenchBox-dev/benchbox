from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import (
    BenchmarkExecutionMixin,
    DataFramePhases,
    DataFrameRunOptions,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class MemoryPolicyAdapter(BenchmarkExecutionMixin):
    platform_name = "Polars"
    family = "expression"

    def __init__(self) -> None:
        self.context_created = False

    def create_context(self) -> SimpleNamespace:
        self.context_created = True
        return SimpleNamespace()

    def get_platform_info(self) -> dict[str, str]:
        return {"platform": self.platform_name, "family": self.family}


def _benchmark_config() -> BenchmarkConfig:
    return BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)


def _benchmark() -> SimpleNamespace:
    return SimpleNamespace(name="tpch", display_name="TPC-H", scale_factor=1.0, tables={})


def test_run_benchmark_rejects_unsafe_memory(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 0.01)
    adapter = MemoryPolicyAdapter()

    result = adapter.run_benchmark(
        _benchmark(),
        benchmark_config=_benchmark_config(),
        phases=DataFramePhases(load=False, execute=False),
    )

    assert result.validation_status == "FAILED"
    assert result.validation_details["error_type"] == "InsufficientMemoryError"
    assert "Insufficient memory" in result.validation_details["error"]
    assert adapter.context_created is False


def test_run_benchmark_ignore_memory_warning_reaches_execution(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 0.01)
    adapter = MemoryPolicyAdapter()

    result = adapter.run_benchmark(
        _benchmark(),
        benchmark_config=_benchmark_config(),
        phases=DataFramePhases(load=False, execute=False),
        options=DataFrameRunOptions(ignore_memory_warnings=True),
    )

    assert result.validation_status != "FAILED"
    assert adapter.context_created is True
