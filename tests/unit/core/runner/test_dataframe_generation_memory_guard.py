from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.exceptions import InsufficientMemoryError
from benchbox.core.runner.runner import LifecyclePhases, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class AdapterReached(Exception):
    pass


class RecordingDataFrameAdapter(BenchmarkExecutionMixin):
    platform_name = "Polars"
    family = "expression"

    def create_context(self) -> SimpleNamespace:
        return SimpleNamespace()

    def get_platform_info(self) -> dict[str, str]:
        return {"platform": self.platform_name, "family": self.family}

    def run_benchmark(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AdapterReached


def _config(**options: Any) -> BenchmarkConfig:
    return BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, options=options)


def _benchmark(events: list[str], tables: dict[str, Any], workers: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        tables=tables,
        parallel=workers,
        output_dir=None,
        generate_data=lambda: events.append("generate_data"),
    )


ALL_PHASES = LifecyclePhases(generate=True, load=True, execute=True)


def _run(
    benchmark: SimpleNamespace,
    config: BenchmarkConfig,
    phases: LifecyclePhases = ALL_PHASES,
    **kwargs: Any,
) -> Any:
    return run_benchmark_lifecycle(
        benchmark_config=config,
        database_config=None,
        system_profile=None,
        platform_adapter=RecordingDataFrameAdapter(),
        benchmark_instance=benchmark,
        phases=phases,
        enable_resource_monitoring=False,
        **kwargs,
    )


def test_uncached_run_is_refused_before_generate_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 3.5)
    events: list[str] = []

    with pytest.raises(InsufficientMemoryError, match="data generation"):
        _run(_benchmark(events, {}), _config())

    assert events == []


@pytest.mark.parametrize(
    "phases",
    [
        LifecyclePhases(generate=False, load=False, execute=True),
        LifecyclePhases(generate=True, load=False, execute=False),
        LifecyclePhases(generate=True, load=False, execute=True),
    ],
    ids=["power-only", "generate-only", "generate-and-power"],
)
def test_uncached_run_is_refused_when_the_load_phase_is_not_requested(
    monkeypatch: pytest.MonkeyPatch, phases: LifecyclePhases
) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 3.5)
    events: list[str] = []

    with pytest.raises(InsufficientMemoryError, match="data generation"):
        _run(_benchmark(events, {}), _config(), phases=phases)

    assert events == []


def test_cached_run_is_not_refused_for_generation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 3.5)
    cached = tmp_path / "lineitem.tbl"
    cached.write_text("1|\n", encoding="utf-8")
    events: list[str] = []

    with pytest.raises(AdapterReached):
        _run(_benchmark(events, {"lineitem": cached}), _config())

    assert events == []


def test_force_regenerate_is_refused_even_with_cached_data(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 3.5)
    cached = tmp_path / "lineitem.tbl"
    cached.write_text("1|\n", encoding="utf-8")
    events: list[str] = []

    with pytest.raises(InsufficientMemoryError, match="data generation"):
        _run(_benchmark(events, {"lineitem": cached}), _config(force_regenerate=True))

    assert events == []


def test_ignore_memory_warnings_lets_generation_proceed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 3.5)
    events: list[str] = []

    with pytest.raises(AdapterReached):
        _run(_benchmark(events, {}), _config(ignore_memory_warnings=True))

    assert events == ["generate_data"]


def test_generation_proceeds_when_memory_is_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: 64.0)
    events: list[str] = []

    with pytest.raises(AdapterReached):
        _run(_benchmark(events, {}), _config())

    assert events == ["generate_data"]
