import types
from unittest.mock import Mock, patch

import pytest

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.orchestrator import BenchmarkOrchestrator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DummySystem:
    cpu_cores_logical = 4
    memory_total_gb = 8


def _mk_config(name="tpcds", scale=0.01, **opts):
    return BenchmarkConfig(
        name=name,
        display_name=name.upper(),
        scale_factor=scale,
        compress_data=True,
        compression_type="zstd",
        compression_level=None,
        test_execution_type="standard",
        options=opts,
    )


def test_no_regenerate_fails_when_data_invalid():
    orchestrator = BenchmarkOrchestrator()

    dummy_benchmark = types.SimpleNamespace()
    dummy_benchmark._name = "TPC-DS Benchmark"
    dummy_benchmark.scale_factor = 0.01
    dummy_benchmark.output_dir = None
    dummy_benchmark.tables = None
    dummy_benchmark.generate_data = Mock()

    dummy_validator = Mock()

    dv = types.SimpleNamespace(valid=False, issues=["missing tables"])
    dummy_validator.should_regenerate_data.return_value = (True, dv)

    dummy_generator = types.SimpleNamespace(validator=dummy_validator, force_regenerate=False)
    dummy_benchmark.data_generator = dummy_generator

    with (
        patch.object(
            BenchmarkOrchestrator,
            "_get_benchmark_instance",
            return_value=dummy_benchmark,
        ),
        patch("benchbox.cli.orchestrator.get_platform_adapter", return_value=Mock()),
    ):
        cfg = _mk_config(no_regenerate=True)
        result = orchestrator.execute_benchmark(
            cfg,
            _DummySystem(),
            types.SimpleNamespace(type="duckdb", name="duckdb", options={}, connection_params={}),
        )

        assert getattr(result, "validation_status", "FAILED") == "FAILED"


def test_force_regenerate_flag_passed_to_benchmark():
    orchestrator = BenchmarkOrchestrator()

    created_instances = []
    constructed_objects = []

    class DummyBenchmark:
        def __init__(self, *args, **kwargs):
            created_instances.append(kwargs)
            constructed_objects.append(self)
            self._name = "Dummy"
            self.scale_factor = kwargs.get("scale_factor", 0.01)
            self.output_dir = None
            self.tables = {}
            self.data_generator = types.SimpleNamespace(validator=Mock(), force_regenerate=False)

        def generate_data(self):
            return {}

    lifecycle_calls = []

    with (
        patch.object(BenchmarkOrchestrator, "_get_benchmark_instance", return_value=DummyBenchmark()),
        patch.object(BenchmarkOrchestrator, "_get_platform_config", return_value={"type": "duckdb"}),
        patch("benchbox.cli.orchestrator.get_platform_adapter", return_value=Mock()),
        patch(
            "benchbox.core.run_service.run_benchmark_lifecycle",
            side_effect=lambda **kwargs: lifecycle_calls.append(kwargs) or Mock(),
        ),
    ):
        cfg = _mk_config(name="tpcds", force_regenerate=True)
        orchestrator.execute_benchmark(
            cfg,
            _DummySystem(),
            types.SimpleNamespace(type="duckdb", name="duckdb", options={}, connection_params={}),
        )

    assert lifecycle_calls, "Lifecycle not invoked"
    passed_config = lifecycle_calls[0]["benchmark_config"]
    assert passed_config.options.get("force_regenerate") is True


