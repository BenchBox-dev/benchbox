from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import ClassVar
from unittest.mock import Mock

import pytest
from rich.console import Console

import benchbox.cli.benchmarks as bench_mod
from benchbox.cli.benchmarks import BenchmarkManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _capture_console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return Console(file=stream, force_terminal=False, color_system=None, width=120), stream


def _sample_benchmarks() -> dict[str, dict[str, object]]:
    return {
        "tpch": {
            "display_name": "TPC-H",
            "description": "Decision support benchmark",
            "query_description": "22 SQL queries",
            "category": "TPC",
            "num_queries": 22,
            "support_status": "stable",
            "complexity": "medium",
            "estimated_time_range": (2, 10),
            "base_memory_gb": 1.0,
            "scale_options": [0.01, 0.1, 1.0],
            "default_scale": 0.01,
            "supports_streams": True,
        },
        "clickbench": {
            "display_name": "ClickBench",
            "description": "Wide analytics benchmark",
            "query_description": "43 SQL queries",
            "category": "Industry",
            "num_queries": 43,
            "support_status": "stable",
            "complexity": "high",
            "estimated_time_range": (5, 15),
            "base_memory_gb": 15.0,
            "scale_options": [0.01, 0.1],
            "default_scale": 0.01,
            "supports_streams": False,
        },
        "metadata_primitives": {
            "display_name": "Metadata Primitives",
            "description": "Catalog inspection primitives",
            "query_description": "Metadata primitives",
            "category": "Primitives",
            "num_queries": 0,
            "support_status": "beta",
            "complexity": "low",
            "estimated_time_range": (1, 2),
            "base_memory_gb": 0.01,
            "scale_options": [1.0],
            "default_scale": 1.0,
            "supports_streams": False,
        },
        "joinorder": {
            "display_name": "JoinOrder",
            "description": "Canonical IMDb Join Order Benchmark",
            "query_description": "113 queries",
            "category": "Academic",
            "num_queries": 113,
            "support_status": "stable",
            "complexity": "high",
            "estimated_time_range": (30, 90),
            "base_memory_gb": 5.0,
            "scale_options": [1.0],
            "default_scale": 1.0,
            "supports_streams": False,
            "surface": "public",
        },
        "joinorder_synthetic": {
            "display_name": "JoinOrder Synthetic",
            "description": "Internal synthetic JoinOrder smoke-test data",
            "query_description": "13 synthetic smoke queries",
            "category": "Academic",
            "num_queries": 13,
            "support_status": "repo_only",
            "complexity": "medium",
            "estimated_time_range": (2, 10),
            "base_memory_gb": 1.0,
            "scale_options": [0.01, 0.1, 1.0],
            "default_scale": 1.0,
            "supports_streams": False,
            "surface": "internal",
        },
    }


@pytest.fixture
def manager() -> BenchmarkManager:
    mgr = BenchmarkManager()
    mgr.benchmarks = _sample_benchmarks()
    return mgr


def test_display_all_benchmarks_shows_controls_and_filters(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    shown = manager._display_all_benchmarks(filter_category="TPC")

    assert list(shown) == ["tpch"]
    output = stream.getvalue()
    assert "Available Benchmarks - TPC" in output
    assert "Controls:" in output
    assert "[p]review" in output


def test_tree_benchmark_listing_labels_support_status(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    manager.list_available_benchmarks()

    output = stream.getvalue()
    assert "Support: Stable" in output
    assert "Support: Beta" in output
    assert "JoinOrder Synthetic" not in output


def test_display_all_benchmarks_labels_support_status(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    shown = manager._display_all_benchmarks(filter_category="Primitives")

    assert list(shown) == ["metadata_primitives"]
    output = stream.getvalue()
    assert "Status" in output
    assert "Beta" in output


def test_display_all_benchmarks_hides_internal_surfaces(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    shown = manager._display_all_benchmarks(filter_category="Academic")

    assert list(shown) == ["joinorder"]
    output = stream.getvalue()
    assert "JoinOrder" in output
    assert "JoinOrder Synthetic" not in output


def test_preview_labels_support_status(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    manager._show_benchmark_preview("metadata_primitives", manager.benchmarks["metadata_primitives"])

    output = stream.getvalue()
    assert "Support Status" in output
    assert "Beta" in output


def test_internal_benchmarks_remain_directly_addressable(manager: BenchmarkManager):
    assert "joinorder_synthetic" in manager.benchmarks
    assert "joinorder_synthetic" not in manager._get_public_benchmarks()


def test_resource_estimates_use_registry_metadata() -> None:
    manager = BenchmarkManager()

    assert manager._estimate_memory_usage("vector_search", 0.1) == pytest.approx(0.1)
    assert manager._estimate_execution_time("coffeeshop", 1.0) == pytest.approx(7.5)


def test_display_all_benchmarks_warns_when_filters_match_nothing(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    shown = manager._display_all_benchmarks(search_term="does-not-exist")

    assert shown == {}
    assert "No benchmarks match the current filters." in stream.getvalue()


def test_show_benchmark_selection_help_renders_commands(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    manager._show_benchmark_selection_help()

    output = stream.getvalue()
    assert "Benchmark Selection Help" in output
    assert "Category filter" in output
    assert "Preview" in output


def test_show_sample_queries_handles_empty_query_registry(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    @dataclass
    class _Benchmark:
        scale_factor: float
        queries: ClassVar[dict[str, str]] = {}

    monkeypatch.setattr("benchbox.core.benchmark_loader.get_core_benchmark_class", lambda _bench: _Benchmark)

    manager._show_sample_queries("tpch")

    assert "No queries available for preview" in stream.getvalue()


def test_show_sample_queries_truncates_long_sql(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    @dataclass
    class _Benchmark:
        scale_factor: float
        queries: ClassVar[dict[str, str]] = {"Q1": "SELECT 1\n" * 200}

    monkeypatch.setattr("benchbox.core.benchmark_loader.get_core_benchmark_class", lambda _bench: _Benchmark)

    manager._show_sample_queries("tpch", limit=1)

    output = stream.getvalue()
    assert "Sample Queries" in output
    assert "(truncated)" in output


def test_prompt_category_selection_uses_prompt_choice(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)
    monkeypatch.setattr(bench_mod.Prompt, "ask", lambda *_args, **_kwargs: "1")

    selected = manager._prompt_category_selection()

    assert selected == "TPC"
    assert "Benchmark Categories" in stream.getvalue()


def test_prompt_benchmark_in_category_returns_selected_metadata(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)
    monkeypatch.setattr(bench_mod.Prompt, "ask", lambda *_args, **_kwargs: "1")

    benchmark_id, benchmark_info = manager._prompt_benchmark_in_category("TPC")

    assert benchmark_id == "tpch"
    assert benchmark_info["display_name"] == "TPC-H"
    assert "TPC Benchmarks" in stream.getvalue()


def test_select_specific_benchmark_short_circuits_single_option(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    selected = manager._select_specific_benchmark({"tpch": manager.benchmarks["tpch"]})

    assert selected == "tpch"
    assert "Selected TPC-H" in stream.getvalue()


def test_select_specific_benchmark_table_labels_support_status(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)
    monkeypatch.setattr(bench_mod.Prompt, "ask", lambda *_args, **_kwargs: "2")

    selected = manager._select_specific_benchmark(
        {
            "tpch": manager.benchmarks["tpch"],
            "metadata_primitives": manager.benchmarks["metadata_primitives"],
        }
    )

    assert selected == "metadata_primitives"
    output = stream.getvalue()
    assert "Status" in output
    assert "Stable" in output
    assert "Beta" in output


def test_configure_benchmark_builds_config_from_prompted_values(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    monkeypatch.setattr(
        manager, "_get_system_profile", lambda: {"memory_gb": 16, "cpu_cores": 8, "architecture": "arm"}
    )
    monkeypatch.setattr(manager, "_prompt_scale", lambda *_args: 0.1)
    monkeypatch.setattr(manager, "_prompt_concurrency", lambda *_args: 2)
    monkeypatch.setattr(manager, "_prompt_compression", lambda: (True, "gzip", 6))
    monkeypatch.setattr(manager, "_display_configuration_summary", lambda *_args, **_kwargs: None)

    config = manager._configure_benchmark("tpch", manager.benchmarks["tpch"])

    assert config.name == "tpch"
    assert config.scale_factor == 0.1
    assert config.concurrency == 2
    assert config.compress_data is True
    assert config.compression_type == "gzip"
    assert config.compression_level == 6
    assert config.test_execution_type == "standard"
    assert config.options["recommended_scale"] == 0.1


def test_prompt_scale_single_option_skips_prompt(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    scale = manager._prompt_scale(
        "metadata_primitives",
        manager.benchmarks["metadata_primitives"],
        {"memory_gb": 8, "cpu_cores": 4},
    )

    assert scale == 1.0
    assert "fixed for this benchmark" in stream.getvalue()


def test_prompt_scale_retries_after_validation_failure(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    monkeypatch.setattr(manager, "_display_scale_estimates", lambda *_args, **_kwargs: None)
    answers = iter([1.0, 0.1])
    monkeypatch.setattr(bench_mod.FloatPrompt, "ask", lambda *_args, **_kwargs: next(answers))

    attempts = {"count": 0}

    def _validate(scale_factor: float, *_args, **_kwargs) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("too large")

    monkeypatch.setattr(manager, "_validate_scale_choice", _validate)

    scale = manager._prompt_scale("tpch", manager.benchmarks["tpch"], {"memory_gb": 8, "cpu_cores": 4})

    assert scale == 0.1
    assert attempts["count"] == 2


def test_prompt_concurrency_returns_unset_when_streams_not_supported(manager: BenchmarkManager):
    concurrency = manager._prompt_concurrency(manager.benchmarks["clickbench"], {"cpu_cores": 8})
    assert concurrency is None


def test_prompt_concurrency_warns_when_user_exceeds_cpu_count(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)
    monkeypatch.setattr(bench_mod.Confirm, "ask", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bench_mod.IntPrompt, "ask", lambda *_args, **_kwargs: 8)

    concurrency = manager._prompt_concurrency(manager.benchmarks["tpch"], {"cpu_cores": 4})

    assert concurrency == 8
    assert "may exceed your 4 CPU cores" in stream.getvalue()


def test_prompt_compression_disabled_uses_default_type(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    monkeypatch.setattr(bench_mod.Confirm, "ask", lambda *_args, **_kwargs: False)

    enabled, compression_type, compression_level = manager._prompt_compression()

    assert enabled is False
    assert compression_type == "zstd"
    assert compression_level is None


def test_prompt_compression_gzip_prompts_for_level(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    monkeypatch.setattr(bench_mod.Confirm, "ask", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bench_mod.Prompt, "ask", lambda *_args, **_kwargs: "gzip")
    monkeypatch.setattr(bench_mod.IntPrompt, "ask", lambda *_args, **_kwargs: 6)

    enabled, compression_type, compression_level = manager._prompt_compression()

    assert enabled is True
    assert compression_type == "gzip"
    assert compression_level == 6


def test_prompt_execution_type_method_removed(manager: BenchmarkManager):
    assert not hasattr(manager, "_prompt_execution_type")
    assert not hasattr(type(manager), "TPC_EXECUTION_TYPES")


def test_display_configuration_summary_includes_subset_and_compression(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    manager._display_configuration_summary(
        "tpch",
        manager.benchmarks["tpch"],
        scale_factor=0.1,
        concurrency=2,
        queries=["Q1", "Q6"],
        compress_data=True,
        compression_type="zstd",
        compression_level=3,
    )

    output = stream.getvalue()
    assert "Configuration Summary" in output
    assert "2 of 22 (subset)" in output
    assert "zstd (level 3)" in output
    assert "~0.1GB" in output
    assert "Execution Type" not in output


def test_get_system_profile_falls_back_when_profiler_errors(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    profiler = Mock()
    profiler.get_system_profile.side_effect = RuntimeError("boom")
    monkeypatch.setattr(bench_mod, "SystemProfiler", lambda: profiler)

    profile = manager._get_system_profile()

    assert profile == {"memory_gb": 8, "cpu_cores": 4, "architecture": "unknown"}
    assert "System profiling failed" in stream.getvalue()


@pytest.mark.parametrize(
    ("memory_gb", "expected"),
    [
        (64, 1.0),
        (24, 0.1),
        (8, 0.01),
    ],
)
def test_get_recommended_scale_uses_memory_tiers(manager: BenchmarkManager, memory_gb: int, expected: float):
    benchmark_info = manager.benchmarks["tpch"]
    assert manager._get_recommended_scale(benchmark_info, {"memory_gb": memory_gb}) == expected


@pytest.mark.parametrize("memory_gb", [8, 16, 24, 64])
def test_get_recommended_scale_returns_value_in_options(manager: BenchmarkManager, memory_gb: int):
    benchmark_info = {"scale_options": [1.0, 10.0, 100.0]}
    recommended = manager._get_recommended_scale(benchmark_info, {"memory_gb": memory_gb})
    assert recommended in benchmark_info["scale_options"]


def test_validate_scale_choice_rejects_below_minimum(monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    benchmark_info = dict(manager.benchmarks["tpch"])
    benchmark_info["min_scale"] = 1.0

    with pytest.raises(ValueError, match="below minimum"):
        manager._validate_scale_choice(0.1, "tpch", benchmark_info, {"memory_gb": 32})

    assert "requires scale_factor >= 1.0" in stream.getvalue()


def test_validate_scale_choice_requires_confirmation_for_risky_memory(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)
    monkeypatch.setattr(bench_mod.Confirm, "ask", lambda *_args, **_kwargs: False)

    with pytest.raises(ValueError, match="exceeds safe memory limits"):
        manager._validate_scale_choice(
            2.0,
            "clickbench",
            manager.benchmarks["clickbench"],
            {"memory_gb": 10.0},
        )

    output = stream.getvalue()
    assert "WARNING" in output
    assert "likely to cause out-of-memory errors" in output


def test_validate_scale_choice_emits_info_for_high_but_safe_usage(
    monkeypatch: pytest.MonkeyPatch, manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    manager._validate_scale_choice(8.0, "tpch", manager.benchmarks["tpch"], {"memory_gb": 10.0})

    assert "This will use ~8.0GB of your 10.0GB memory" in stream.getvalue()


def _future_status_benchmark(name: str, support_status: str, surface: str) -> dict[str, object]:
    return {
        "display_name": name,
        "description": f"{support_status} fixture",
        "query_description": f"{support_status} queries",
        "category": f"Cat-{support_status}",
        "num_queries": 1,
        "support_status": support_status,
        "complexity": "low",
        "estimated_time_range": (1, 2),
        "base_memory_gb": 0.1,
        "scale_options": [1.0],
        "default_scale": 1.0,
        "supports_streams": False,
        "surface": surface,
    }


@pytest.fixture
def future_status_manager() -> BenchmarkManager:
    mgr = BenchmarkManager()
    mgr.benchmarks = {
        "f_stable": _future_status_benchmark("F Stable", "stable", "public"),
        "f_beta": _future_status_benchmark("F Beta", "beta", "public"),
        "f_experimental": _future_status_benchmark("F Experimental", "experimental", "public"),
        "f_deprecated": _future_status_benchmark("F Deprecated", "deprecated", "public"),
        "f_document_only": _future_status_benchmark("F DocOnly", "document_only", "public"),
        "f_internal_beta": _future_status_benchmark("F Internal Beta", "beta", "internal"),
    }
    return mgr


def test_cli_labels_every_public_support_status(
    monkeypatch: pytest.MonkeyPatch, future_status_manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    future_status_manager.list_available_benchmarks()

    output = stream.getvalue()
    assert "Support: Stable" in output
    assert "Support: Beta" in output
    assert "Support: Experimental" in output
    assert "Support: Deprecated" in output
    assert "Support: Document-only" in output


def test_cli_hides_internal_benchmark_regardless_of_support_status(
    monkeypatch: pytest.MonkeyPatch, future_status_manager: BenchmarkManager
):
    console, stream = _capture_console()
    monkeypatch.setattr(bench_mod, "console", console)

    public = future_status_manager._get_public_benchmarks()
    assert "f_internal_beta" not in public
    assert "f_beta" in public

    future_status_manager.list_available_benchmarks()
    assert "F Internal Beta" not in stream.getvalue()
