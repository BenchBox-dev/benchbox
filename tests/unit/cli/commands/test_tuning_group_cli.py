# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys as _sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.tuning_group import tuning_group
from benchbox.cli.config import ConfigManager

__import__("benchbox.cli.commands.tuning_group")
_tuning_group_module = _sys.modules["benchbox.cli.commands.tuning_group"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _obj():
    return {"config": ConfigManager()}


class TestTuningInitSqlMode:
    def test_datafusion_auto_mode_uses_manifest_sql_default(self):
        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console"),
            patch("benchbox.cli.config.ConfigManager.create_sample_unified_tuning_config") as create_sql,
        ):
            result = runner.invoke(tuning_group, ["init", "--platform", "datafusion"], obj=_obj())

        assert result.exit_code == 0
        create_sql.assert_called_once()

    def test_init_sql_exits_zero(self, tmp_path):
        runner = CliRunner()
        with patch.object(_tuning_group_module, "console"):
            mock_cm = MagicMock()
            with patch("benchbox.cli.config.ConfigManager.create_sample_unified_tuning_config", mock_cm):
                result = runner.invoke(
                    tuning_group,
                    ["init", "--platform", "duckdb"],
                    obj=_obj(),
                )
        assert result.exit_code == 0

    def test_init_sql_output_mentions_created(self):
        runner = CliRunner()
        with patch.object(_tuning_group_module, "console") as mock_console:
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            with patch("benchbox.cli.config.ConfigManager.create_sample_unified_tuning_config"):
                runner.invoke(
                    tuning_group,
                    ["init", "--platform", "duckdb"],
                    obj=_obj(),
                )
        assert any("created" in call.lower() or "Tuning" in call for call in calls)

    def test_init_sql_output_mentions_run_hint(self):
        runner = CliRunner()
        with patch.object(_tuning_group_module, "console") as mock_console:
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            with patch("benchbox.cli.config.ConfigManager.create_sample_unified_tuning_config"):
                runner.invoke(
                    tuning_group,
                    ["init", "--platform", "duckdb"],
                    obj=_obj(),
                )
        assert any("benchbox run" in call or "--tuning" in call for call in calls)

    def test_init_dataframe_mode_on_sql_platform_fails(self):
        runner = CliRunner()
        with patch.object(_tuning_group_module, "console") as mock_console:
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            result = runner.invoke(
                tuning_group,
                ["init", "--platform", "duckdb", "--mode", "dataframe"],
                obj=_obj(),
            )
        assert result.exit_code != 0 or any("does not support" in call or "DataFrame" in call for call in calls)

    def test_init_dataframe_mode_on_sql_platform_shows_df_platforms(self):
        runner = CliRunner()
        with patch.object(_tuning_group_module, "console") as mock_console:
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            runner.invoke(
                tuning_group,
                ["init", "--platform", "duckdb", "--mode", "dataframe"],
                obj=_obj(),
            )
        assert any("polars" in call.lower() for call in calls)


class TestTuningInitDataframeMode:
    def test_init_dataframe_alias_uses_base_platform(self):
        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "save_dataframe_tuning") as save_config,
        ):
            result = runner.invoke(tuning_group, ["init", "--platform", "datafusion-df"], obj=_obj())

        assert result.exit_code == 0
        save_config.assert_called_once()

    def test_init_datafusion_dataframe_mode_exits_zero(self):
        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "save_dataframe_tuning"),
        ):
            result = runner.invoke(
                tuning_group,
                ["init", "--platform", "datafusion", "--mode", "dataframe"],
                obj=_obj(),
            )
        assert result.exit_code == 0

    def test_defaults_accepts_datafusion(self):
        from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration

        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console") as mock_console,
            patch.object(
                _tuning_group_module,
                "get_smart_defaults",
                return_value=DataFrameTuningConfiguration(),
            ),
            patch.object(_tuning_group_module, "detect_system_profile", return_value=MagicMock()),
            patch.object(
                _tuning_group_module,
                "get_profile_summary",
                return_value={
                    "cpu_cores": 8,
                    "available_memory_gb": 16.0,
                    "memory_category": "high",
                    "has_gpu": False,
                },
            ),
        ):
            result = runner.invoke(tuning_group, ["defaults", "--platform", "datafusion"], obj=_obj())

        assert result.exit_code == 0
        assert any(
            "--platform datafusion --mode dataframe --tuning auto" in str(call)
            for call in mock_console.print.call_args_list
        )

    def test_init_polars_dataframe_mode_exits_zero(self):
        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "save_dataframe_tuning"),
        ):
            result = runner.invoke(
                tuning_group,
                ["init", "--platform", "polars", "--mode", "dataframe"],
                obj=_obj(),
            )
        assert result.exit_code == 0

    def test_init_polars_smart_defaults_prints_profile(self):
        runner = CliRunner()
        mock_profile = MagicMock()
        mock_summary = {
            "cpu_cores": 8,
            "available_memory_gb": 16.0,
            "memory_category": "high",
            "has_gpu": False,
        }
        with (
            patch.object(_tuning_group_module, "console") as mock_console,
            patch.object(_tuning_group_module, "save_dataframe_tuning"),
            patch.object(_tuning_group_module, "detect_system_profile", return_value=mock_profile),
            patch.object(_tuning_group_module, "get_profile_summary", return_value=mock_summary),
            patch.object(_tuning_group_module, "get_smart_defaults", return_value=MagicMock()),
        ):
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            result = runner.invoke(
                tuning_group,
                ["init", "--platform", "polars", "--mode", "dataframe", "--smart-defaults"],
                obj=_obj(),
            )
        assert result.exit_code == 0
        assert any("smart default" in call.lower() for call in calls)


class TestTuningValidate:
    def test_validate_valid_config_prints_valid(self, tmp_path):
        config_file = tmp_path / "tuning.yaml"
        config_file.write_text("parallelism:\n  worker_count: 4\n")

        runner = CliRunner()
        mock_config = MagicMock()
        mock_config.get_summary.return_value = {
            "setting_count": 1,
            "has_streaming": False,
            "has_gpu": False,
        }
        with (
            patch.object(_tuning_group_module, "console") as mock_console,
            patch.object(_tuning_group_module, "load_dataframe_tuning", return_value=mock_config),
            patch.object(_tuning_group_module, "validate_dataframe_tuning", return_value=[]),
        ):
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            result = runner.invoke(
                tuning_group,
                ["validate", str(config_file), "--platform", "polars"],
                obj=_obj(),
            )
        assert result.exit_code == 0
        assert any("valid" in call.lower() for call in calls)

    def test_validate_with_errors_shows_error_output(self, tmp_path):
        config_file = tmp_path / "tuning.yaml"
        config_file.write_text("parallelism:\n  worker_count: -1\n")

        runner = CliRunner()
        mock_config = MagicMock()
        mock_issues = [MagicMock(level="error", message="workers must be positive")]
        with (
            patch.object(_tuning_group_module, "console") as mock_console,
            patch.object(_tuning_group_module, "load_dataframe_tuning", return_value=mock_config),
            patch.object(_tuning_group_module, "validate_dataframe_tuning", return_value=mock_issues),
            patch.object(_tuning_group_module, "has_errors", return_value=True),
            patch.object(_tuning_group_module, "format_issues", return_value="error: workers must be positive"),
        ):
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            runner.invoke(
                tuning_group,
                ["validate", str(config_file), "--platform", "polars"],
                obj=_obj(),
            )
        assert any("error" in call.lower() for call in calls)


REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_validate(args):
    runner = CliRunner()
    calls = []
    with patch.object(_tuning_group_module, "console") as mock_console:
        mock_console.print.side_effect = lambda *a, **k: calls.append(" ".join(str(x) for x in a))
        result = runner.invoke(tuning_group, ["validate", *args], obj=_obj())
    return result, "\n".join(calls)


class TestTuningValidateSql:
    def test_shipped_duckdb_template_validates(self):
        template = REPO_ROOT / "examples/tunings/duckdb/tpch_tuned.yaml"

        result, output = _run_validate([str(template), "--platform", "duckdb"])

        assert result.exit_code == 0, output
        assert "Configuration is valid for duckdb" in output
        assert "Enabled tuning types: 4" in output

    def test_sql_validation_goes_through_validate_for_platform_detailed(self):
        template = REPO_ROOT / "examples/tunings/duckdb/tpch_tuned.yaml"

        with patch(
            "benchbox.core.tuning.interface.UnifiedTuningConfiguration.validate_for_platform_detailed",
            return_value=(["boom for duckdb"], ["careful"]),
        ) as detailed:
            result, output = _run_validate([str(template), "--platform", "duckdb"])

        detailed.assert_called_once_with("duckdb")
        assert result.exit_code != 0
        assert "boom for duckdb" in output
        assert "careful" in output

    def test_incompatible_tuning_type_is_an_error(self):
        template = REPO_ROOT / "examples/tunings/duckdb/tpch_tuned.yaml"

        result, output = _run_validate([str(template), "--platform", "bigquery"])

        assert result.exit_code != 0
        assert "Tuning type 'sorting' is not supported by platform 'bigquery'" in output
        assert "Configuration has errors" in output

    def test_registry_unrendered_type_is_reported_as_warning(self, tmp_path):
        template = tmp_path / "databricks_distribution.yaml"
        template.write_text(
            "primary_keys:\n"
            "  enabled: false\n"
            "foreign_keys:\n"
            "  enabled: false\n"
            "unique_constraints:\n"
            "  enabled: false\n"
            "check_constraints:\n"
            "  enabled: false\n"
            "platform_optimizations: {}\n"
            "table_tunings:\n"
            "  ORDERS:\n"
            "    table_name: ORDERS\n"
            "    distribution:\n"
            "    - name: O_ORDERKEY\n"
            "      type: INTEGER\n"
            "      order: 1\n"
        )

        result, output = _run_validate([str(template), "--platform", "databricks"])

        assert result.exit_code == 0, output
        assert "'distribution' is accepted by platform 'databricks' but not rendered yet" in output
        assert "Configuration is valid but has warnings" in output

    def test_platform_alias_resolves_to_registry_key(self):
        template = REPO_ROOT / "examples/tunings/duckdb/tpch_tuned.yaml"

        with patch(
            "benchbox.core.tuning.interface.UnifiedTuningConfiguration.validate_for_platform_detailed",
            return_value=([], []),
        ) as detailed:
            result, _ = _run_validate([str(template), "--platform", "clickhouse-local"])

        detailed.assert_called_once_with("clickhouse")
        assert result.exit_code == 0

    def test_unknown_platform_is_rejected_with_choices(self):
        template = REPO_ROOT / "examples/tunings/duckdb/tpch_tuned.yaml"

        result, _ = _run_validate([str(template), "--platform", "no-such-platform"])

        assert result.exit_code == 2
        assert "unknown platform 'no-such-platform'" in result.output
        assert "duckdb" in result.output

    def test_dataframe_file_is_rejected_for_sql_platform(self):
        template = REPO_ROOT / "examples/tunings/dataframe/polars_optimized.yaml"

        result, output = _run_validate([str(template), "--platform", "duckdb"])

        assert result.exit_code != 0
        assert "is a DataFrame tuning file" in output

    def test_empty_file_is_rejected(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("")

        result, output = _run_validate([str(empty), "--platform", "duckdb"])

        assert result.exit_code != 0
        assert "Configuration file is empty" in output


class TestTuningValidateMode:
    def test_sql_datafusion_file_validates_with_mode_sql(self, tmp_path):
        target = tmp_path / "datafusion_tuned.yaml"
        target.write_text("table_tunings: {}\n")

        result, output = _run_validate([str(target), "--platform", "datafusion", "--mode", "sql"])

        assert result.exit_code == 0, output
        assert "Enabled tuning types: 4" in output

    def test_dataframe_file_rejected_with_mode_sql(self):
        template = REPO_ROOT / "examples/tunings/dataframe/polars_optimized.yaml"

        result, output = _run_validate([str(template), "--platform", "datafusion", "--mode", "sql"])

        assert result.exit_code != 0
        assert "DataFrame tuning file" in output

    def test_auto_datafusion_validates_dataframe_file(self, tmp_path):
        from benchbox.core.dataframe.tuning import get_smart_defaults, save_dataframe_tuning

        target = tmp_path / "datafusion_df.yaml"
        save_dataframe_tuning(get_smart_defaults("datafusion"), target, platform="datafusion")

        result, output = _run_validate([str(target), "--platform", "datafusion"])

        assert result.exit_code == 0, output
        assert "Configuration is valid for datafusion" in output

    def test_dataframe_mode_rejected_for_sql_platform(self, tmp_path):
        target = tmp_path / "tuned.yaml"
        target.write_text("table_tunings: {}\n")

        result, output = _run_validate([str(target), "--platform", "duckdb", "--mode", "dataframe"])

        assert result.exit_code != 0
        assert "does not support DataFrame mode" in output


class TestTuningDefaults:
    def test_defaults_exits_zero(self):
        from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration

        runner = CliRunner()
        real_config = DataFrameTuningConfiguration()
        mock_summary = {
            "cpu_cores": 8,
            "available_memory_gb": 16.0,
            "memory_category": "high",
            "has_gpu": False,
        }
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "get_smart_defaults", return_value=real_config),
            patch.object(_tuning_group_module, "detect_system_profile", return_value=MagicMock()),
            patch.object(_tuning_group_module, "get_profile_summary", return_value=mock_summary),
        ):
            result = runner.invoke(
                tuning_group,
                ["defaults", "--platform", "polars"],
                obj=_obj(),
            )
        assert result.exit_code == 0


class TestTuningList:
    def test_list_exits_zero(self):
        runner = CliRunner()
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "display_tuning_list"),
        ):
            result = runner.invoke(
                tuning_group,
                ["list"],
                obj=_obj(),
            )
        assert result.exit_code == 0


class TestTuningShow:
    def test_show_calls_display(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("platform: duckdb\n")
        runner = CliRunner()
        mock_resolution = MagicMock()
        mock_resolution.config_file = None
        with (
            patch.object(_tuning_group_module, "console"),
            patch.object(_tuning_group_module, "display_tuning_show") as mock_show,
            patch.object(_tuning_group_module, "resolve_tuning", return_value=mock_resolution),
        ):
            result = runner.invoke(
                tuning_group,
                ["show", str(config_file)],
                obj=_obj(),
            )
        assert result.exit_code == 0
        mock_show.assert_called_once()
