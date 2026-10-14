from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.panel import Panel

from benchbox.cli.commands.tuning_group import _create_profile_config
from benchbox.cli.onboarding import (
    _create_benchmark_help,
    _create_concurrency_help,
    _create_scale_factor_help,
    _create_tuning_help,
    _get_first_run_marker_path,
    _get_help_content,
    _run_interactive_tour,
    _show_benchmarks_overview,
    _show_key_concepts,
    _show_scale_factor_guide,
    _show_tuning_modes,
    _show_welcome_message,
    check_and_run_first_time_setup,
    show_contextual_help,
)
from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration

__import__("benchbox.cli.commands.tuning_group")
_tuning_group_module = sys.modules["benchbox.cli.commands.tuning_group"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFirstRunOnboarding:
    def test_non_interactive_terminal_skips_onboarding(self):
        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(sys.stdout, "isatty", return_value=False),
        ):
            assert check_and_run_first_time_setup() is False

    def test_first_run_creates_marker_and_runs_tour(self, tmp_path: Path):
        marker_path = tmp_path / "first_run_complete"

        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("benchbox.cli.onboarding._get_first_run_marker_path", return_value=marker_path),
            patch("benchbox.cli.onboarding._show_welcome_message") as mock_welcome,
            patch("benchbox.cli.onboarding._run_interactive_tour") as mock_tour,
            patch("benchbox.cli.onboarding.Confirm.ask", return_value=True),
        ):
            assert check_and_run_first_time_setup() is True

        assert marker_path.exists()
        mock_welcome.assert_called_once()
        mock_tour.assert_called_once()

    def test_existing_marker_skips_first_run_flow(self, tmp_path: Path):
        marker_path = tmp_path / "first_run_complete"
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("done\n", encoding="utf-8")

        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("benchbox.cli.onboarding._get_first_run_marker_path", return_value=marker_path),
            patch("benchbox.cli.onboarding._show_welcome_message") as mock_welcome,
        ):
            assert check_and_run_first_time_setup() is False

        mock_welcome.assert_not_called()

    def test_interactive_tour_stops_when_user_declines_next_step(self):
        with (
            patch("benchbox.cli.onboarding._show_key_concepts") as show_key,
            patch("benchbox.cli.onboarding._show_benchmarks_overview") as show_benchmarks,
            patch("benchbox.cli.onboarding._show_tuning_modes") as show_tuning,
            patch("benchbox.cli.onboarding._show_scale_factor_guide") as show_scale,
            patch("benchbox.cli.onboarding.Confirm.ask", side_effect=[False]),
        ):
            _run_interactive_tour()

        show_key.assert_called_once()
        show_benchmarks.assert_not_called()
        show_tuning.assert_not_called()
        show_scale.assert_not_called()

    def test_interactive_tour_stops_after_benchmarks_overview_when_second_prompt_declined(self):
        with (
            patch("benchbox.cli.onboarding._show_key_concepts") as show_key,
            patch("benchbox.cli.onboarding._show_benchmarks_overview") as show_benchmarks,
            patch("benchbox.cli.onboarding._show_tuning_modes") as show_tuning,
            patch("benchbox.cli.onboarding._show_scale_factor_guide") as show_scale,
            patch("benchbox.cli.onboarding.Confirm.ask", side_effect=[True, False]),
        ):
            _run_interactive_tour()

        show_key.assert_called_once()
        show_benchmarks.assert_called_once()
        show_tuning.assert_not_called()
        show_scale.assert_not_called()

    def test_interactive_tour_stops_after_tuning_modes_when_third_prompt_declined(self):
        with (
            patch("benchbox.cli.onboarding._show_key_concepts") as show_key,
            patch("benchbox.cli.onboarding._show_benchmarks_overview") as show_benchmarks,
            patch("benchbox.cli.onboarding._show_tuning_modes") as show_tuning,
            patch("benchbox.cli.onboarding._show_scale_factor_guide") as show_scale,
            patch("benchbox.cli.onboarding.Confirm.ask", side_effect=[True, True, False]),
        ):
            _run_interactive_tour()

        show_key.assert_called_once()
        show_benchmarks.assert_called_once()
        show_tuning.assert_called_once()
        show_scale.assert_not_called()

    def test_contextual_help_known_and_unknown_contexts(self):
        with patch("benchbox.cli.onboarding.console.print") as mock_print:
            show_contextual_help("benchmark_selection")

        assert mock_print.called
        assert _get_help_content("benchmark_selection") is not None
        assert _get_help_content("unknown-context") is None

        with patch("benchbox.cli.onboarding.console.print") as mock_print:
            show_contextual_help("unknown-context")

        mock_print.assert_not_called()

    def test_first_run_marker_path_uses_home_directory(self):
        with patch("benchbox.cli.onboarding.Path.home", return_value=Path("/tmp/home")):
            marker = _get_first_run_marker_path()

        assert marker == Path("/tmp/home/.benchbox/first_run_complete")

    def test_show_welcome_message_renders_getting_started_panel(self):
        with patch("benchbox.cli.onboarding.console.print") as mock_print:
            _show_welcome_message()

        panel = mock_print.call_args.args[0]
        assert isinstance(panel, Panel)
        assert "Getting Started" in panel.title
        assert "BenchBox" in panel.renderable.plain

    @pytest.mark.parametrize(
        ("helper", "expected_title"),
        [
            (_show_key_concepts, "Key Concepts"),
            (_show_benchmarks_overview, "Popular Benchmarks"),
            (_show_tuning_modes, "Tuning Modes"),
            (_show_scale_factor_guide, "Scale Factor Guide"),
        ],
    )
    def test_onboarding_panels_render_expected_titles(self, helper, expected_title):
        with patch("benchbox.cli.onboarding.console.print") as mock_print:
            helper()

        panels = [call.args[0] for call in mock_print.call_args_list if call.args and isinstance(call.args[0], Panel)]
        assert panels, "expected helper to print a Panel"
        panel = panels[0]
        assert isinstance(panel, Panel)
        assert expected_title in panel.title

    def test_interactive_tour_runs_all_steps_and_completion_message(self):
        with (
            patch("benchbox.cli.onboarding.console.print") as mock_print,
            patch("benchbox.cli.onboarding.Confirm.ask", side_effect=[True, True, True]),
        ):
            _run_interactive_tour()

        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        assert "Tour complete" in printed
        assert "ready to run your first benchmark" in printed

    @pytest.mark.parametrize(
        ("context", "expected_text"),
        [
            ("benchmark_selection", "Choosing a Benchmark"),
            ("scale_factor", "Scale Factor Selection"),
            ("tuning_mode", "Tuning Modes"),
            ("concurrency", "Concurrent Streams"),
        ],
    )
    def test_help_content_helpers_return_expected_text(self, context: str, expected_text: str):
        help_text = _get_help_content(context)

        assert help_text is not None
        assert expected_text in help_text.plain

    def test_help_content_factory_functions_include_key_guidance(self):
        assert "TPC-H" in _create_benchmark_help().plain
        assert "0.01" in _create_scale_factor_help().plain
        assert "notuning" in _create_tuning_help().plain
        assert "1 stream" in _create_concurrency_help().plain

    def test_show_contextual_help_wraps_known_context_in_panel(self):
        with patch("benchbox.cli.onboarding.console.print") as mock_print:
            show_contextual_help("tuning_mode")

        panels = [call.args[0] for call in mock_print.call_args_list if call.args and isinstance(call.args[0], Panel)]
        assert len(panels) == 1
        assert panels[0].title == "Help: Tuning Mode"


class TestCreateProfileConfigHelper:
    def test_create_profile_config_for_polars_optimized(self):
        config = _create_profile_config("polars", "optimized")

        assert isinstance(config, DataFrameTuningConfiguration)
        assert config.execution.lazy_evaluation is True
        assert config.execution.engine_affinity is None
        assert config.execution.streaming_mode is False

    @pytest.mark.parametrize("profile", ["optimized", "streaming", "memory-constrained"])
    def test_create_profile_config_never_chooses_a_polars_engine(self, profile):
        config = _create_profile_config("polars", profile)

        assert config.execution.engine_affinity is None
        assert config.execution.streaming_mode is False

    @pytest.mark.parametrize("platform", ["dask", "pandas", "cudf"])
    def test_create_profile_config_streaming_profile_is_unchanged_for_other_platforms(self, platform):
        assert _create_profile_config(platform, "streaming").execution.streaming_mode is True

    def test_create_profile_config_memory_constrained_dask(self):
        config = _create_profile_config("dask", "memory-constrained")

        assert config.execution.streaming_mode is True
        assert config.memory.spill_to_disk is True
        assert config.memory.memory_limit == "2GB"

    def test_create_profile_config_for_datafusion_optimized(self):
        config = _create_profile_config("datafusion", "optimized")

        assert config.parallelism.thread_count == 4

    def test_create_profile_config_gpu_warns_for_non_cudf(self):
        with patch.object(_tuning_group_module.console, "print") as mock_print:
            config = _create_profile_config("polars", "gpu")

        assert config.gpu.enabled is True
        assert any("GPU profile is only applicable to cuDF" in str(call) for call in mock_print.call_args_list)
