from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import benchbox.core.run_service as run_service
import benchbox.core.runner.runner as runner
from benchbox.core.run_service import SilentVerbosity
from benchbox.core.schemas import BenchmarkConfig

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_run_service_adapter_factory_is_a_core_composition_boundary() -> None:
    config = MagicMock(spec=BenchmarkConfig)
    config.options = {}
    adapter = object()
    result = MagicMock()
    factory = MagicMock(return_value=adapter)
    database_config = SimpleNamespace(execution_mode="sql")

    with (
        patch.object(run_service, "run_benchmark_lifecycle", return_value=result) as run_lifecycle,
        patch.object(run_service, "apply_driver_metadata") as apply_metadata,
    ):
        returned = run_service.execute_run(
            config=config,
            benchmark_instance=object(),
            database_config=database_config,
            system_profile=object(),
            platform_config={"database_path": ":memory:"},
            output_root=None,
            phases_to_run=["execute"],
            adapter_factory=factory,
            verbosity=SilentVerbosity(),
        )

    assert returned is result
    factory.assert_called_once()
    assert factory.call_args.kwargs["execution_mode"] == "sql"
    run_lifecycle.assert_called_once()
    assert run_lifecycle.call_args.kwargs["platform_adapter"] is adapter
    apply_metadata.assert_called_once_with(result, database_config=database_config, platform_adapter=adapter)


def test_runner_accepts_prebuilt_adapter_without_factory_lookup() -> None:
    adapter = SimpleNamespace(is_dataframe_adapter=False)
    benchmark = object()
    benchmark_config = SimpleNamespace(scale_factor=0.01)

    with patch.object(runner, "get_platform_adapter", side_effect=AssertionError("fallback was used")):
        configured, is_dataframe = runner._configure_lifecycle_adapter(
            adapter=adapter,
            database_config=SimpleNamespace(type="duckdb"),
            platform_config={},
            benchmark=benchmark,
            benchmark_config=benchmark_config,
            validation_opts=runner.ValidationOptions(),
            verbosity_settings=None,
            table_mode="native",
        )

    assert configured is adapter
    assert is_dataframe is False
    assert adapter.benchmark_instance is benchmark
    assert adapter.scale_factor == 0.01


def test_runner_fallback_looks_up_adapter_when_none_is_supplied() -> None:
    adapter = SimpleNamespace(is_dataframe_adapter=False)
    benchmark = object()
    benchmark_config = SimpleNamespace(scale_factor=0.01)

    with patch.object(runner, "get_platform_adapter", return_value=adapter) as factory:
        configured, is_dataframe = runner._configure_lifecycle_adapter(
            adapter=None,
            database_config=SimpleNamespace(type="duckdb"),
            platform_config={"database_path": ":memory:"},
            benchmark=benchmark,
            benchmark_config=benchmark_config,
            validation_opts=runner.ValidationOptions(),
            verbosity_settings=None,
            table_mode="native",
        )

    factory.assert_called_once_with("duckdb", database_path=":memory:")
    assert configured is adapter
    assert is_dataframe is False


