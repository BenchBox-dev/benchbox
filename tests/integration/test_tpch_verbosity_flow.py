from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.utils.verbosity import VerbosityMixin, VerbositySettings, compute_verbosity

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class _NoopConsole:
    def print(self, *args, **kwargs):
        return None


class _StubGenerator(VerbosityMixin):
    def __init__(self, *, verbose: int | bool = 0, quiet: bool = False) -> None:
        self.logger = logging.getLogger("benchbox.test.stub.generator")
        self.apply_verbosity(compute_verbosity(verbose, quiet))


class _StubBenchmark(VerbosityMixin):
    def __init__(
        self,
        *,
        scale_factor: float,
        output_dir: Any | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        **_: Any,
    ) -> None:
        self.logger = logging.getLogger("benchbox.test.stub.benchmark")
        self._name = "StubBenchmark"
        self.data_generator = _StubGenerator(verbose=verbose, quiet=quiet)
        settings = compute_verbosity(verbose, quiet)
        self.apply_verbosity(settings)
        self.output_dir = output_dir

    def apply_verbosity(self, settings: VerbositySettings) -> None:
        super().apply_verbosity(settings)
        if hasattr(self, "data_generator"):
            self.data_generator.apply_verbosity(settings)

    def create_enhanced_benchmark_result(self, **_: Any):
        return SimpleNamespace()


class _StubAdapter(VerbosityMixin):
    platform_name = "stub"

    def __init__(self) -> None:
        self.logger = logging.getLogger("benchbox.test.stub.adapter")
        self.apply_verbosity(VerbositySettings.default())

    def run_benchmark(self, *args: Any, **kwargs: Any):
        return SimpleNamespace()


def test_verbosity_settings_flow_through_orchestrator(monkeypatch, tmp_path):

    captured: dict[str, VerbositySettings] = {}

    def fake_run_benchmark_lifecycle(**kwargs):

        benchmark_instance = kwargs.get("benchmark_instance")
        platform_adapter = kwargs.get("platform_adapter")
        verbosity = kwargs.get("verbosity")

        captured["_called"] = True

        if benchmark_instance and isinstance(benchmark_instance, VerbosityMixin):
            benchmark_instance.apply_verbosity(verbosity)
        if platform_adapter and isinstance(platform_adapter, VerbosityMixin):
            platform_adapter.apply_verbosity(verbosity)

        captured["cli"] = verbosity
        if benchmark_instance:
            captured["benchmark"] = benchmark_instance.verbosity_settings
            if hasattr(benchmark_instance, "data_generator"):
                captured["generator"] = benchmark_instance.data_generator.verbosity_settings
        if platform_adapter:
            captured["adapter"] = platform_adapter.verbosity_settings

        return SimpleNamespace(validation_status="PASSED", total_queries=0, successful_queries=0, failed_queries=0)

    monkeypatch.setattr(BenchmarkOrchestrator, "_get_benchmark_class", lambda self, name: _StubBenchmark)

    monkeypatch.setattr(
        BenchmarkOrchestrator, "_get_platform_config", lambda self, db_config, system_profile, **kwargs: {}
    )
    monkeypatch.setattr("benchbox.cli.orchestrator.get_platform_adapter", lambda *a, **k: _StubAdapter())

    monkeypatch.setattr("benchbox.core.run_service.run_benchmark_lifecycle", fake_run_benchmark_lifecycle)

    def mock_get_benchmark_instance(self, config, system_profile):
        return _StubBenchmark(scale_factor=config.scale_factor, output_dir=tmp_path)

    monkeypatch.setattr(BenchmarkOrchestrator, "_get_benchmark_instance", mock_get_benchmark_instance)

    orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
    orchestrator.console = _NoopConsole()
    orchestrator.directory_manager.get_datagen_path = lambda *a, **k: tmp_path

    verbosity_settings = compute_verbosity(2, False)
    orchestrator.set_verbosity(verbosity_settings)

    config = SimpleNamespace(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        options={},
        queries=None,
        concurrency=1,
    )

    database_config = SimpleNamespace(type="duckdb")

    try:
        result = orchestrator.execute_benchmark(
            config=config,
            system_profile=None,
            database_config=database_config,
            phases_to_run=["generate", "load"],
        )
    except Exception as e:
        pytest.fail(f"execute_benchmark raised exception: {type(e).__name__}: {e}")

    assert "_called" in captured, (
        f"Mock function was never called. Captured keys: {list(captured.keys())}, result={result}"
    )
    assert captured["cli"] == verbosity_settings
    assert captured["benchmark"] == verbosity_settings
    assert captured["generator"] == verbosity_settings
    assert captured["adapter"] == verbosity_settings


def _patch_dbgen_path(monkeypatch):
    from pathlib import Path

    original_exists = Path.exists

    def patched_exists(self: Path) -> bool:
        if "_sources/tpc-h/dbgen" in str(self):
            return True
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", patched_exists)


def test_tpch_generator_emits_debug_when_very_verbose(monkeypatch, tmp_path, caplog):

    _patch_dbgen_path(monkeypatch)

    caplog.set_level(logging.DEBUG, logger="benchbox.core.tpch.generator")

    from benchbox.core.tpch.generator import TPCHDataGenerator

    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, verbose=2, quiet=False)

    generator.log_very_verbose("diagnostic message")

    assert any(record.levelno == logging.DEBUG and record.message == "diagnostic message" for record in caplog.records)


def test_tpch_generator_quiet_supresses_logs(monkeypatch, tmp_path, caplog, capsys):

    _patch_dbgen_path(monkeypatch)

    caplog.set_level(logging.DEBUG, logger="benchbox.core.tpch.generator")

    from benchbox.core.tpch.generator import TPCHDataGenerator

    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, verbose=2, quiet=True)

    generator.log_verbose("should not appear")
    generator.log_very_verbose("suppressed debug")

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ""
    assert not caplog.records
