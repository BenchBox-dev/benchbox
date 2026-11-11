from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import benchbox
import benchbox.cli as cli_pkg
import benchbox.core.runner as runner_pkg
import benchbox.core.tpcds as core_tpcds_pkg
import benchbox.core.visualization as visualization_pkg
import benchbox.monitoring as monitoring_pkg
from benchbox.core.comparison.plotter import UnifiedComparisonPlotter
from benchbox.core.comparison.types import PlatformType
from benchbox.utils.scale_factor import (
    format_benchmark_name,
    format_data_directory,
    format_scale_factor,
    format_schema_name,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _raise_import_error_for(target: str):
    real_import = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == target:
            raise ImportError(f"missing {target}")
        return real_import(name, globals, locals, fromlist, level)

    return _import


def test_benchbox_lazy_helpers_cover_cache_reexports_and_fallback_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_error = ImportError("missing benchmark")

    def _missing_module(_module_path: str):
        raise missing_error

    benchbox._clear_lazy_cache()
    monkeypatch.setattr(benchbox, "_import_module", _missing_module)
    monkeypatch.setitem(
        benchbox._BENCHMARK_REGISTRY,
        "Synthetic",
        benchbox._BenchmarkSpec("synthetic", "Synthetic", ("synthetic",)),
    )
    monkeypatch.setitem(
        benchbox._BENCHMARK_REGISTRY,
        "SyntheticNoStore",
        benchbox._BenchmarkSpec("synthetic_no_store", "SyntheticNoStore", (), store_errors=False),
    )

    loaded, error = benchbox._load_benchmark_class("Synthetic")
    assert loaded is None
    assert error is missing_error
    assert benchbox._lazy_cache["Synthetic"] is missing_error

    loaded, error = benchbox._load_benchmark_class("SyntheticNoStore")
    assert loaded is None
    assert error is missing_error
    assert benchbox._lazy_cache["SyntheticNoStore"] is None

    benchbox._lazy_cache["temporary"] = object()
    benchbox._clear_lazy_cache()
    assert benchbox._lazy_cache == {}
    assert benchbox.__getattr__("platforms") is benchbox.platforms

    reexport_module = ModuleType("benchbox.core.tpch.dataframe_queries")
    reexport_module.TPCH_DATAFRAME_QUERIES = {"Q1": "df"}
    monkeypatch.setattr(benchbox, "import_module", lambda _name: reexport_module)
    assert benchbox.__getattr__("TPCH_DATAFRAME_QUERIES") == {"Q1": "df"}

    monkeypatch.setitem(
        benchbox._BENCHMARK_REGISTRY,
        "SyntheticFallback",
        benchbox._BenchmarkSpec("synthetic_fallback", "SyntheticFallback", ("fallback",)),
    )
    monkeypatch.setattr(benchbox, "_load_benchmark_class", lambda _name: (None, ImportError("boom")))

    with (
        patch("builtins.__import__", side_effect=_raise_import_error_for("benchbox.utils.version")),
        pytest.raises(ImportError, match="Could not import SyntheticFallback"),
    ):
        benchbox.__getattr__("SyntheticFallback")


def test_cli_package_lazy_getattr_supports_main_submodules_and_missing_attr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = cli_pkg.__getattr__("main")
    assert isinstance(main, ModuleType)
    assert callable(main)
    assert hasattr(main, "get_config_manager")

    commands_module = ModuleType("benchbox.cli.commands")

    def _import_module(name: str):
        if name == "benchbox.cli.commands":
            return commands_module
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(cli_pkg.importlib, "import_module", _import_module)
    assert cli_pkg.__getattr__("commands") is commands_module

    with pytest.raises(AttributeError, match="has no attribute 'missing_cli_attr'"):
        cli_pkg.__getattr__("missing_cli_attr")


def test_runner_monitoring_and_visualization_lazy_exports(monkeypatch: pytest.MonkeyPatch) -> None:
    runner_module = ModuleType("benchbox.core.runner.runner")
    runner_module.LifecyclePhases = object()
    runner_module.ValidationOptions = object()
    runner_module.run_benchmark_lifecycle = lambda: "ok"

    with patch.dict(sys.modules, {"benchbox.core.runner.runner": runner_module}):
        assert runner_pkg.__getattr__("run_benchmark_lifecycle") is runner_module.run_benchmark_lifecycle

    conversion_module = ModuleType("benchbox.core.runner.conversion")

    def _runner_import(name: str):
        if name == "benchbox.core.runner.conversion":
            return conversion_module
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(runner_pkg.importlib, "import_module", _runner_import)
    assert runner_pkg.__getattr__("conversion") is conversion_module
    with pytest.raises(AttributeError, match="has no attribute 'missing_runner_attr'"):
        runner_pkg.__getattr__("missing_runner_attr")
    assert "run_benchmark_lifecycle" in runner_pkg.__dir__()

    performance_module = ModuleType("benchbox.monitoring.performance")
    performance_module.PerformanceTracker = object()
    report_module = ModuleType("benchbox.monitoring.report")
    report_module.ResourceReporter = object()
    helper_module = ModuleType("benchbox.monitoring.helper")

    def _monitoring_import(name: str):
        mapping = {
            "benchbox.monitoring.performance": performance_module,
            "benchbox.monitoring.report": report_module,
            "benchbox.monitoring.helper": helper_module,
        }
        if name in mapping:
            return mapping[name]
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(monitoring_pkg.importlib, "import_module", _monitoring_import)
    assert monitoring_pkg.__getattr__("PerformanceTracker") is performance_module.PerformanceTracker
    assert monitoring_pkg.__getattr__("ResourceReporter") is report_module.ResourceReporter
    assert monitoring_pkg.__getattr__("helper") is helper_module
    with pytest.raises(AttributeError, match="has no attribute 'missing_monitoring_attr'"):
        monitoring_pkg.__getattr__("missing_monitoring_attr")
    assert "PerformanceTracker" in monitoring_pkg.__dir__()

    exporters_module = ModuleType("benchbox.core.visualization.exporters")
    exporters_module.export_ascii = object()
    utils_module = ModuleType("benchbox.core.visualization.utils")
    utils_module.slugify = object()
    theme_module = ModuleType("benchbox.core.visualization.theme")

    def _visualization_import(name: str):
        mapping = {
            "benchbox.core.visualization.exporters": exporters_module,
            "benchbox.core.visualization.utils": utils_module,
            "benchbox.core.visualization.theme": theme_module,
        }
        if name in mapping:
            return mapping[name]
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(visualization_pkg.importlib, "import_module", _visualization_import)
    assert visualization_pkg.__getattr__("export_ascii") is exporters_module.export_ascii
    assert visualization_pkg.__getattr__("slugify") is utils_module.slugify
    assert visualization_pkg.__getattr__("theme") is theme_module
    with pytest.raises(AttributeError, match="has no attribute 'missing_visualization_attr'"):
        visualization_pkg.__getattr__("missing_visualization_attr")
    assert "export_ascii" in visualization_pkg.__dir__()


def test_scale_factor_helpers_cover_edge_cases() -> None:
    assert format_scale_factor(1.0) == "sf1"
    assert format_scale_factor(10) == "sf10"
    assert format_scale_factor(1.5) == "sf15"
    assert format_scale_factor(0.01) == "sf001"
    assert format_scale_factor(0.0) == "sf0"
    assert format_benchmark_name("tpch", 0.1) == "tpch_sf01"
    assert format_data_directory("tpch", 0.1) == "tpch_sf01_data"
    assert format_schema_name("tpch", 0.1) == "tpch_sf01"


def test_unified_comparison_plotter_validates_inputs_and_delegates(tmp_path) -> None:
    with pytest.raises(ValueError, match="No results provided"):
        UnifiedComparisonPlotter([])

    result = SimpleNamespace(platform_type=PlatformType.SQL)

    with (
        patch(
            "benchbox.core.visualization.chart_generator.normalized_from_unified",
            return_value=["normalized"],
        ) as normalize,
        patch(
            "benchbox.core.visualization.chart_generator.generate_comparison_charts",
            return_value={"performance_bar": tmp_path / "chart.txt"},
        ) as generate,
    ):
        plotter = UnifiedComparisonPlotter([result], theme="dark")
        charts = plotter.generate_charts(tmp_path)

    assert plotter.platform_type is PlatformType.SQL
    assert charts == {"performance_bar": tmp_path / "chart.txt"}
    normalize.assert_called_once_with([result])
    generate.assert_called_once_with(["normalized"], tmp_path, theme="dark")


def test_core_tpcds_validate_c_tools_warns_for_missing_templates_and_runtime_errors(tmp_path) -> None:
    missing_templates = SimpleNamespace(templates_dir=tmp_path / "missing_templates")

    with (
        patch.object(core_tpcds_pkg, "DSQGenBinary", return_value=missing_templates),
        patch("warnings.warn") as warn_missing,
    ):
        core_tpcds_pkg._validate_c_tools()

    assert "query templates not found" in warn_missing.call_args.args[0]

    with (
        patch.object(core_tpcds_pkg, "DSQGenBinary", side_effect=RuntimeError("boom")),
        patch("warnings.warn") as warn_tools,
    ):
        core_tpcds_pkg._validate_c_tools()

    message = warn_tools.call_args.args[0]
    assert "TPC-DS tools unavailable: boom" in message
    assert "make dsqgen" in message


