# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import Optional
from unittest.mock import Mock, patch

import pytest

from benchbox.base import BaseBenchmark
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.benchmark_loader import instantiate_benchmark_class
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.scale_factor import format_scale_factor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _assert_tpch_default_path(path: Path, scale_factor: float) -> None:
    expected_suffix = Path("benchmark_runs") / "datagen" / f"tpch_{format_scale_factor(scale_factor)}"
    actual_suffix = Path(*Path(path).parts[-3:])
    assert actual_suffix == expected_suffix
    assert "primitives_sf" not in str(path)


@pytest.mark.unit
class TestOrchestratorDataSharing:
    def test_primitives_uses_tpch_path(self):

        orchestrator = BenchmarkOrchestrator()

        config = BenchmarkConfig(
            name="read_primitives",
            display_name="Primitives",
            scale_factor=1.0,
            compress_data=False,
            compression_type="none",
        )

        benchmark = orchestrator._get_benchmark_instance(config, None)

        data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
        assert data_source == "tpch", "Primitives should declare 'tpch' as data source"

        _assert_tpch_default_path(Path(benchmark.output_dir), 1.0)

    def test_orchestrator_detects_data_sharing(self):

        orchestrator = BenchmarkOrchestrator()

        config = BenchmarkConfig(
            name="read_primitives",
            display_name="Primitives",
            scale_factor=1.0,
            compress_data=False,
            compression_type="none",
        )
        benchmark = orchestrator._get_benchmark_instance(config, None)

        data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()

        if data_source:
            output_root = None
        else:
            output_root = str(orchestrator.directory_manager.get_datagen_path(config.name.lower(), config.scale_factor))

        assert output_root is None, "Orchestrator should not override path for data-sharing benchmarks"

    def test_orchestrator_does_not_interfere_with_regular_benchmarks(self):

        BenchmarkOrchestrator()

        mock_benchmark = Mock()
        mock_benchmark.get_data_source_benchmark.return_value = None
        mock_benchmark.output_dir = "/some/path"

        data_source = getattr(mock_benchmark, "get_data_source_benchmark", lambda: None)()

        if data_source:
            output_root = None
        else:
            output_root = "benchmark_runs/datagen/tpch_sf1"

        assert output_root is not None, "Orchestrator should provide path for non-sharing benchmarks"
        assert "benchmark_runs/datagen" in output_root

    def test_custom_output_overrides_data_sharing(self):

        orchestrator = BenchmarkOrchestrator()
        custom_path = "/custom/data/path"
        orchestrator.set_custom_output_dir(custom_path)

        BenchmarkConfig(
            name="read_primitives",
            display_name="Primitives",
            scale_factor=1.0,
            compress_data=False,
        )

        output_root = orchestrator.custom_output_dir if orchestrator.custom_output_dir else None

        assert output_root == custom_path, "Custom output path should override data sharing"

    def test_get_platform_config_resolves_data_source_benchmark(self):
        orchestrator = BenchmarkOrchestrator()
        benchmark = Mock()
        benchmark.get_data_source_benchmark.return_value = "tpch"

        with patch("benchbox.cli.orchestrator._core_get_platform_config", return_value={}) as mock_core:
            orchestrator._get_platform_config(
                database_config=Mock(),
                system_profile=Mock(),
                benchmark_name="read_primitives",
                scale_factor=1.0,
                benchmark=benchmark,
            )

        assert mock_core.call_args.kwargs["benchmark_name"] == "tpch"

    def test_get_platform_config_passes_through_when_no_data_source(self):
        orchestrator = BenchmarkOrchestrator()
        benchmark = Mock()
        benchmark.get_data_source_benchmark.return_value = None

        with patch("benchbox.cli.orchestrator._core_get_platform_config", return_value={}) as mock_core:
            orchestrator._get_platform_config(
                database_config=Mock(),
                system_profile=Mock(),
                benchmark_name="tpch",
                scale_factor=1.0,
                benchmark=benchmark,
            )

        assert mock_core.call_args.kwargs["benchmark_name"] == "tpch"

    def test_data_sharing_with_different_scale_factors(self):
        orchestrator = BenchmarkOrchestrator()

        for scale_factor in [0.1, 1.0, 10.0]:
            config = BenchmarkConfig(
                name="read_primitives",
                display_name="Primitives",
                scale_factor=scale_factor,
                compress_data=False,
                compression_type="none",
            )
            benchmark = orchestrator._get_benchmark_instance(config, None)

            _assert_tpch_default_path(Path(benchmark.output_dir), scale_factor)


class _MethodOnlySharingBenchmark(BaseBenchmark):
    def __init__(self, scale_factor: float = 1.0, output_dir=None, **kwargs):
        super().__init__(scale_factor=scale_factor, output_dir=output_dir, **kwargs)
        self._name = "Method Only Sharing"

    def get_data_source_benchmark(self) -> Optional[str]:
        return "tpch"

    def generate_data(self):
        return []

    def get_queries(self):
        return {}

    def get_query(self, query_id, *, params=None):
        raise ValueError(f"Query {query_id} not found")


@pytest.mark.unit
class TestConstructionBoundaryInjection:
    def _construct_with_spy(self, orchestrator, config):
        captured = {}

        def _spy(benchmark_class, required_kwargs, optional_kwargs):
            instance = instantiate_benchmark_class(benchmark_class, required_kwargs, optional_kwargs)
            captured["optional_kwargs"] = optional_kwargs
            captured["handler_at_construction"] = instance.output_dir
            return instance

        with patch("benchbox.cli.orchestrator.instantiate_benchmark_class", side_effect=_spy):
            benchmark = orchestrator._get_benchmark_instance(config, None)
        return benchmark, captured

    def test_own_data_benchmark_constructed_with_managed_root(self):
        orchestrator = BenchmarkOrchestrator()
        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, compress_data=False)

        benchmark, captured = self._construct_with_spy(orchestrator, config)

        expected = orchestrator.directory_manager.get_datagen_path("tpch", 1.0)
        assert Path(str(captured["optional_kwargs"]["output_dir"])) == Path(str(expected))
        assert Path(str(benchmark.output_dir)) == Path(str(expected))
        assert benchmark.output_dir is captured["handler_at_construction"]

    def test_data_sharing_benchmark_constructed_with_shared_root(self):
        orchestrator = BenchmarkOrchestrator()
        config = BenchmarkConfig(
            name="read_primitives", display_name="Primitives", scale_factor=1.0, compress_data=False
        )

        benchmark, captured = self._construct_with_spy(orchestrator, config)

        expected = orchestrator.directory_manager.get_datagen_path("tpch", 1.0)
        assert Path(str(captured["optional_kwargs"]["output_dir"])) == Path(str(expected))
        assert benchmark.output_dir is captured["handler_at_construction"]
        assert Path(str(benchmark.data_generator.output_dir)) == Path(str(expected))
        assert Path(str(benchmark.data_generator.tpch_generator.output_dir)) == Path(str(expected))

    def test_method_only_data_sharing_falls_back_to_redirect(self):
        orchestrator = BenchmarkOrchestrator()
        config = BenchmarkConfig(name="tpch", display_name="Stub", scale_factor=1.0, compress_data=False)

        with patch.object(orchestrator, "_get_benchmark_class", return_value=_MethodOnlySharingBenchmark):
            benchmark = orchestrator._get_benchmark_instance(config, None)

        expected = orchestrator.directory_manager.get_datagen_path("tpch", 1.0)
        assert Path(str(benchmark.output_dir)) == Path(str(expected))

    def test_own_output_benchmark_keeps_constructed_output_dir(self):

        class _OwnOutputBenchmark(_MethodOnlySharingBenchmark):
            GENERATES_OWN_OUTPUT = True

        orchestrator = BenchmarkOrchestrator()
        config = BenchmarkConfig(name="tpcds_obt", display_name="Stub", scale_factor=1.0, compress_data=False)

        with patch.object(orchestrator, "_get_benchmark_class", return_value=_OwnOutputBenchmark):
            benchmark = orchestrator._get_benchmark_instance(config, None)

        expected = orchestrator.directory_manager.get_datagen_path("tpcds_obt", 1.0)
        assert Path(str(benchmark.output_dir)) == Path(str(expected))

    def test_cloud_custom_output_defers_resolution(self):
        orchestrator = BenchmarkOrchestrator()
        orchestrator.set_custom_output_dir("s3://bucket/prefix")
        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, compress_data=False)

        benchmark, captured = self._construct_with_spy(orchestrator, config)

        assert "output_dir" not in captured["optional_kwargs"]
        assert benchmark.output_dir is not None

    def test_local_custom_output_injected_at_construction(self, tmp_path):
        orchestrator = BenchmarkOrchestrator()
        orchestrator.set_custom_output_dir(str(tmp_path / "custom-root"))
        config = BenchmarkConfig(
            name="read_primitives", display_name="Primitives", scale_factor=1.0, compress_data=False
        )

        benchmark, captured = self._construct_with_spy(orchestrator, config)

        assert Path(str(captured["optional_kwargs"]["output_dir"])) == tmp_path / "custom-root"
        assert Path(str(benchmark.output_dir)) == tmp_path / "custom-root"
        assert benchmark.output_dir is captured["handler_at_construction"]


