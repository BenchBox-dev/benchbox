from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.cli.benchmarks import prompt_execution_engine

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestPromptExecutionEngine:
    def test_platform_without_engines_returns_default_without_prompting(self):
        with patch("benchbox.cli.benchmarks.Prompt.ask") as ask:
            assert prompt_execution_engine("duckdb") == "default"
            ask.assert_not_called()

    def test_manifest_choices_are_offered_for_polars(self):
        with patch("benchbox.cli.benchmarks.Prompt.ask", return_value="streaming") as ask:
            assert prompt_execution_engine("polars-df") == "streaming"
            choices = ask.call_args.kwargs["choices"]
            assert choices == ["default", "auto", "in-memory", "streaming"]


class TestInteractiveEngineResolution:
    @staticmethod
    def _state(execution_engine=None):
        import types
        from unittest.mock import MagicMock

        return types.SimpleNamespace(
            parsed_platform_options={},
            execution_engine=execution_engine,
            database_config=types.SimpleNamespace(type="polars-df", options={}, execution_engine="default"),
            ctx=MagicMock(),
            logger=None,
            benchmark_config=None,
        )

    def test_explicit_engine_skips_the_prompt(self):
        from benchbox.cli.commands.run import _interactive_prompt_platform_options

        s = self._state(execution_engine="streaming")
        with (
            patch("benchbox.cli.benchmarks.prompt_platform_options", return_value={}),
            patch("benchbox.cli.benchmarks.prompt_execution_engine") as prompt,
        ):
            _interactive_prompt_platform_options(s)

        prompt.assert_not_called()
        assert s.resolved_execution_engine == "streaming"
        assert s.database_config.execution_engine == "streaming"

    def test_prompted_engine_is_recorded(self):
        from benchbox.cli.commands.run import _interactive_prompt_platform_options

        s = self._state()
        with (
            patch("benchbox.cli.benchmarks.prompt_platform_options", return_value={}),
            patch("benchbox.cli.benchmarks.prompt_execution_engine", return_value="in-memory"),
        ):
            _interactive_prompt_platform_options(s)

        assert s.execution_engine == "in-memory"
        assert s.resolved_execution_engine == "in-memory"

    def test_invalid_explicit_engine_exits(self):
        from benchbox.cli.commands.run import _interactive_prompt_platform_options

        s = self._state(execution_engine="gpu")
        with (
            patch("benchbox.cli.benchmarks.prompt_platform_options", return_value={}),
            patch("benchbox.cli.benchmarks.prompt_execution_engine") as prompt,
        ):
            _interactive_prompt_platform_options(s)

        prompt.assert_not_called()
        assert s.ctx.exit.called

    def test_explicit_engine_survives_populated_platform_options(self):
        from benchbox.cli.commands.run import _interactive_prompt_platform_options

        s = self._state(execution_engine="streaming")
        s.parsed_platform_options = {"rechunk": True}
        with (
            patch("benchbox.cli.benchmarks.prompt_platform_options") as options,
            patch("benchbox.cli.benchmarks.prompt_execution_engine") as prompt,
        ):
            _interactive_prompt_platform_options(s)

        options.assert_not_called()
        prompt.assert_not_called()
        assert s.resolved_execution_engine == "streaming"
        assert s.database_config.execution_engine == "streaming"


def test_polars_streaming_cli_alias_adds_the_legacy_streaming_option():
    import importlib

    from click.testing import CliRunner

    run_module = importlib.import_module("benchbox.cli.commands.run")
    captured = {}

    def prepare(state):
        captured["execution_engine"] = state.execution_engine
        captured["platform_option_pairs"] = state.platform_option_pairs
        state.dry_run = True

    with (
        patch.object(run_module, "_prepare_run_state", prepare),
        patch.object(run_module, "_run_dry_run"),
    ):
        result = CliRunner().invoke(
            run_module.run,
            ["--platform", "polars-df", "--benchmark", "tpch", "--polars-streaming"],
            obj={},
        )

    assert result.exit_code == 0, result.output
    assert captured["execution_engine"] is None
    assert ("streaming", "true") in captured["platform_option_pairs"]


def test_polars_streaming_cli_alias_rejects_conflicting_execution_engine():
    import importlib

    from click.testing import CliRunner

    run_module = importlib.import_module("benchbox.cli.commands.run")

    with patch.object(run_module, "_prepare_run_state") as prepare:
        result = CliRunner().invoke(
            run_module.run,
            [
                "--platform",
                "polars-df",
                "--benchmark",
                "tpch",
                "--execution-engine",
                "in-memory",
                "--polars-streaming",
            ],
            obj={},
        )

    assert result.exit_code == 2
    assert "conflicts with --execution-engine in-memory" in result.output
    prepare.assert_not_called()
