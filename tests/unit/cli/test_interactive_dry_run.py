# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from benchbox.cli.benchmarks import (
    BenchmarkConfig,
    get_platform_format_recommendation,
    prompt_capture_plans,
    prompt_force_regeneration,
    prompt_official_mode,
    prompt_output_location,
    prompt_phases,
    prompt_platform_options,
    prompt_query_subset,
    prompt_seed,
    prompt_table_format,
    prompt_table_mode,
    prompt_validation_mode,
    prompt_verbose_output,
)
from benchbox.cli.dryrun import display_interactive_preview, generate_cli_command

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGenerateCLICommand:
    def test_basic_command(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
        )
        assert "benchbox run" in cmd
        assert "--platform duckdb" in cmd
        assert "--benchmark tpch" in cmd
        assert "--scale" not in cmd

    def test_non_default_scale(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
        )
        assert "--scale 0.1" in cmd

    def test_phases_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases=["generate", "load", "power"],
        )
        assert "--phases generate,load,power" in cmd

    def test_default_phases_omitted(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases=["power"],
        )
        assert "--phases" not in cmd

    def test_queries_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            queries=["Q1", "Q6", "Q17"],
        )
        assert "--queries Q1,Q6,Q17" in cmd

    def test_tuning_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            tuning="tuned",
        )
        assert "--tuning tuned" in cmd

    def test_default_tuning_omitted(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            tuning="notuning",
        )
        assert "--tuning" not in cmd

    def test_seed_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            seed=42,
        )
        assert "--seed 42" in cmd

    def test_output_included(self):

        cmd = generate_cli_command(
            platform="snowflake",
            benchmark="tpch",
            scale=1.0,
            output="s3://bucket/path",
        )
        assert "--output s3://bucket/path" in cmd

    def test_table_format_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            table_format="iceberg",
        )
        assert "--table-format iceberg" in cmd

    def test_compression_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            compression="zstd:9",
        )
        assert "--compression zstd:9" in cmd

    def test_mode_included(self):

        cmd = generate_cli_command(
            platform="polars",
            benchmark="tpch",
            scale=0.1,
            mode="dataframe",
        )
        assert "--mode dataframe" in cmd

    def test_table_mode_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            table_mode="external",
        )
        assert "--table-mode external" in cmd

    def test_table_mode_native_omitted(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            table_mode="native",
        )
        assert "--table-mode" not in cmd

    def test_force_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            force="datagen",
        )
        assert "--force datagen" in cmd

    def test_official_flag(self):

        cmd = generate_cli_command(
            platform="snowflake",
            benchmark="tpch",
            scale=100,
            official=True,
        )
        assert "--official" in cmd

    def test_capture_plans_flag(self):
        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            capture_plans=True,
        )
        assert "--capture-plans" in cmd

    def test_validation_included(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            validation="full",
        )
        assert "--validation full" in cmd

    def test_full_command_with_all_options(self):

        cmd = generate_cli_command(
            platform="snowflake",
            benchmark="tpcds",
            scale=10,
            phases=["generate", "load", "power", "throughput"],
            queries=["Q1", "Q2", "Q3"],
            tuning="tuned",
            seed=12345,
            output="s3://bucket/benchmark-data",
            table_format="iceberg:zstd",
            compression="zstd:9",
            mode="sql",
            force="all",
            official=True,
            capture_plans=True,
            validation="full",
        )
        assert "--platform snowflake" in cmd
        assert "--benchmark tpcds" in cmd
        assert "--scale 10" in cmd
        assert "--phases generate,load,power,throughput" in cmd
        assert "--queries Q1,Q2,Q3" in cmd
        assert "--tuning tuned" in cmd
        assert "--seed 12345" in cmd
        assert "--output s3://bucket/benchmark-data" in cmd
        assert "--table-format iceberg:zstd" in cmd
        assert "--compression zstd:9" in cmd
        assert "--mode sql" in cmd
        assert "--force all" in cmd
        assert "--official" in cmd
        assert "--capture-plans" in cmd
        assert "--validation full" in cmd

    def test_multiline_formatting(self):

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.1,
            tuning="tuned",
            seed=42,
        )
        assert "\\\n" in cmd


class TestDisplayInteractivePreview:
    def _create_mock_database_config(self, platform_type="duckdb", execution_mode="sql"):

        return SimpleNamespace(type=platform_type, execution_mode=execution_mode)

    def _create_mock_benchmark_config(
        self,
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.1,
        queries=None,
        compress_data=True,
        compression_type="zstd",
        compression_level=None,
        options=None,
    ):
        config = BenchmarkConfig(
            name=name,
            display_name=display_name,
            scale_factor=scale_factor,
            queries=queries,
            compress_data=compress_data,
            compression_type=compression_type,
            compression_level=compression_level,
            options=options or {},
        )
        return config

    def test_basic_preview_output(self):

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config()

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = output.getvalue()
        assert "Configuration Preview" in result
        assert "DUCKDB" in result
        assert "TPC-H" in result
        assert "power" in result
        assert "Table Mode:" in result
        assert "native" in result
        assert "benchbox run" in result

    def test_preview_with_all_options(self):

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config(platform_type="snowflake")
        bench_config = self._create_mock_benchmark_config(
            name="tpcds",
            display_name="TPC-DS",
            scale_factor=10,
            options={"estimated_time_range": (30, 60)},
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["generate", "load", "power"],
            output="s3://bucket/path",
            tuning="tuned",
            seed=42,
            console_obj=console,
        )

        result = output.getvalue()
        assert "SNOWFLAKE" in result
        assert "TPC-DS" in result
        assert "10" in result
        assert "generate" in result
        assert "tuned" in result
        assert "42" in result
        assert "s3://bucket/path" in result
        assert "30-60 minutes" in result

    def test_preview_shows_cli_command(self):

        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        db_config.gateway = "espresso"
        bench_config = self._create_mock_benchmark_config(scale_factor=0.5)

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "Equivalent CLI command" in result
        assert "--platform duckdb" in result
        assert "--benchmark tpch" in result
        assert "--scale 0.5" in result
        assert "--gateway espresso" in result
        assert "--table-mode" not in result

    def test_preview_external_table_mode_reflected(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(scale_factor=0.5)

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            table_mode="external",
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "Table Mode:" in result
        assert "external" in result
        assert "--table-mode external" in result

    def test_preview_with_query_subset(self):

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            queries=["Q1", "Q6", "Q17"],
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = output.getvalue()
        assert "3 selected" in result

    def test_preview_with_compression(self):

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            compress_data=True,
            compression_type="zstd",
            compression_level=9,
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = output.getvalue()
        assert "zstd:9" in result

    def test_preview_with_execution_mode(self):

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config(
            platform_type="polars",
            execution_mode="dataframe",
        )
        bench_config = self._create_mock_benchmark_config()

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = output.getvalue()
        assert "POLARS" in result
        assert "dataframe mode" in result

    def test_preview_shows_table_format_in_table(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            options={"table_format": "iceberg", "table_format_compression": "zstd"},
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["generate", "load", "power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "Table Format:" in result
        assert "Iceberg" in result
        assert "zstd" in result

    def test_preview_shows_table_format_in_cli_command(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            options={"table_format": "parquet", "table_format_compression": "snappy"},
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--table-format parquet" in result

    def test_preview_omits_format_when_not_set(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config()

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "Table Format:" not in result
        assert "--table-format" not in result

    def test_preview_cli_command_includes_tuning_when_set(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config()

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["generate", "load", "power"],
            tuning="tuned",
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--tuning tuned" in result

    def test_preview_cli_command_omits_tuning_when_none(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config()

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            tuning=None,
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--tuning" not in result

    def test_preview_cli_command_includes_compression(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            compress_data=True,
            compression_type="zstd",
            compression_level=3,
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--compression zstd:3" in result

    def test_preview_cli_command_includes_all_configured_options(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        db_config = self._create_mock_database_config()
        bench_config = self._create_mock_benchmark_config(
            scale_factor=1.0,
            compress_data=True,
            compression_type="zstd",
            compression_level=3,
            options={"table_format": "iceberg"},
        )

        display_interactive_preview(
            database_config=db_config,
            benchmark_config=bench_config,
            phases=["generate", "load", "power"],
            table_mode="external",
            tuning="tuned",
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--tuning tuned" in result
        assert "--table-mode external" in result
        assert "--table-format iceberg" in result
        assert "--compression zstd:3" in result


class TestDisplayInteractivePreviewAllParams:
    def test_preview_cli_command_includes_all_new_params(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=200)

        class MockDB:
            type = "databricks"
            execution_mode = "sql"

        bench_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            compress_data=True,
            compression_type="zstd",
            compression_level=9,
            options={"table_format": "delta", "table_format_compression": "zstd"},
        )

        display_interactive_preview(
            database_config=MockDB(),
            benchmark_config=bench_config,
            phases=["generate", "load", "power"],
            output="s3://bucket/data",
            table_mode="external",
            tuning="tuned",
            seed=42,
            force="all",
            official=True,
            capture_plans=True,
            validation="full",
            verbose=2,
            console_obj=console,
            platform_options={
                "driver_version": "1.2.0",
                "databricks_clustering_strategy": "liquid_clustering",
                "liquid_clustering_columns": "col1,col2",
            },
            plan_config="sample:0.1,first:5",
            presort="delta-sorted",
            sorted_ingestion_mode="force",
            sorted_ingestion_method="z_order",
            global_cache=True,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())

        assert "--platform databricks" in result
        assert "--benchmark tpch" in result
        assert "--scale 1.0" in result
        assert "--phases generate,load,power" in result
        assert "--tuning tuned" in result
        assert "--seed 42" in result
        assert "--table-mode external" in result
        assert "--table-format delta:zstd" in result
        assert "--compression zstd:9" in result
        assert "--force all" in result
        assert "--official" in result
        assert "--capture-plans" in result
        assert "--validation full" in result
        assert "-vv" in result

        assert "--platform-option driver_version=1.2.0" in result
        assert "--plan-config sample:0.1,first:5" in result
        assert "--presort delta-sorted" in result
        assert "--sorted-ingestion-mode force" in result
        assert "--sorted-ingestion-method z_order" in result
        assert "--platform-option databricks_clustering_strategy=liquid_clustering" in result
        assert "--platform-option liquid_clustering_columns=col1,col2" in result
        assert "--global-cache" in result

    def test_preview_compression_zstd_not_dropped(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=200)

        class MockDB:
            type = "duckdb"
            execution_mode = "sql"

        bench_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            compress_data=True,
            compression_type="zstd",
            compression_level=None,
        )

        display_interactive_preview(
            database_config=MockDB(),
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--compression zstd" in result

    def test_preview_table_format_with_compression_suffix(self):
        import re

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=200)

        class MockDB:
            type = "duckdb"
            execution_mode = "sql"

        bench_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            compress_data=False,
            options={"table_format": "iceberg", "table_format_compression": "zstd"},
        )

        display_interactive_preview(
            database_config=MockDB(),
            benchmark_config=bench_config,
            phases=["power"],
            console_obj=console,
        )

        result = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
        assert "--table-format iceberg:zstd" in result


class TestPromptPhases:
    @pytest.mark.parametrize(
        ("responses", "expected"),
        [
            (["1"], ["power"]),
            (["2"], ["generate", "load", "power"]),
            (["3"], ["generate"]),
            (["4"], ["load"]),
            (["5"], ["generate", "load", "warmup", "power", "throughput", "maintenance"]),
            (["6", "1,2,5"], ["generate", "load", "power"]),
            (["6", "1,2,3"], ["generate", "load", "statistics"]),
            (["6", "generate,power,throughput"], ["generate", "power", "throughput"]),
            (["6", "power,power,load,power"], ["power", "load"]),
            (["6", ""], ["power"]),
        ],
    )
    def test_prompt_phases_mappings(self, monkeypatch, responses, expected):

        response_iter = iter(responses)
        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", lambda *_args, **_kwargs: next(response_iter))
        assert prompt_phases() == expected

    def test_phases_out_of_range_input_explains_and_points_to_query_step(self, monkeypatch):
        rendered_lines: list[str] = []
        monkeypatch.setattr(
            "benchbox.cli.benchmarks.console.print",
            lambda *args, **_kwargs: rendered_lines.append(str(args[0]) if args else ""),
        )
        response_iter = iter(["6", "37"])
        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", lambda *_args, **_kwargs: next(response_iter))

        result = prompt_phases()

        assert result == ["power"]
        joined = "\n".join(rendered_lines)
        assert "not query numbers" in joined
        assert "power or standard" in joined.lower()
        assert "'37'" in joined
        assert "query selection" in joined.lower()


class TestPromptTableMode:
    def test_select_native(self, monkeypatch):
        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", lambda *_args, **_kwargs: "1")
        assert prompt_table_mode() == "native"

    def test_select_external(self, monkeypatch):
        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", lambda *_args, **_kwargs: "2")
        assert prompt_table_mode() == "external"

    def test_default_fallback_for_invalid_default(self, monkeypatch):
        def _ask(*_args, **kwargs):
            assert kwargs.get("default") == "1"
            return "1"

        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", _ask)
        assert prompt_table_mode(default_mode="unknown") == "native"

    def test_external_default_label_rendered(self, monkeypatch):
        rendered_lines = []

        monkeypatch.setattr(
            "benchbox.cli.benchmarks.console.print",
            lambda *args, **_kwargs: rendered_lines.append(str(args[0]) if args else ""),
        )
        monkeypatch.setattr("benchbox.cli.benchmarks.Prompt.ask", lambda *_args, **_kwargs: "2")

        assert prompt_table_mode(default_mode="external") == "external"
        assert any("external (default)" in line for line in rendered_lines)
        assert not any("native (default)" in line for line in rendered_lines)


class TestPromptQuerySubset:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_run_all_queries(self, mock_confirm):

        mock_confirm.return_value = True
        result = prompt_query_subset("tpch", 22)
        assert result is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_query_subset(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = False
        mock_prompt.return_value = "Q1,Q6,Q17"
        result = prompt_query_subset("tpch", 22)
        assert result == ["Q1", "Q6", "Q17"]

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_query_subset_with_numbers(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = False
        mock_prompt.return_value = "1,6,17"
        result = prompt_query_subset("tpch", 22)
        assert result == ["Q1", "Q6", "Q17"]

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_empty_selection_returns_none(self, mock_confirm, mock_prompt):
        mock_confirm.return_value = False
        mock_prompt.return_value = ""
        result = prompt_query_subset("tpch", 22)
        assert result is None


class TestPromptOfficialMode:
    def test_non_tpc_benchmark_returns_false(self):

        result, adjusted_scale = prompt_official_mode("ssb", 0.1)
        assert result is False
        assert adjusted_scale is None

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_official_mode(self, mock_confirm):

        mock_confirm.return_value = False
        result, adjusted_scale = prompt_official_mode("tpch", 0.1)
        assert result is False
        assert adjusted_scale is None

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_enable_with_compliant_scale(self, mock_confirm):

        mock_confirm.return_value = True
        result, adjusted_scale = prompt_official_mode("tpch", 10.0)
        assert result is True
        assert adjusted_scale is None

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_non_compliant_scale_adjusts_to_nearest(self, mock_confirm):

        mock_confirm.side_effect = [True, True]
        result, adjusted_scale = prompt_official_mode("tpch", 0.1)
        assert result is True
        assert adjusted_scale == 1.0

    @patch("benchbox.cli.benchmarks.FloatPrompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_non_compliant_scale_custom_entry(self, mock_confirm, mock_float):

        mock_confirm.side_effect = [True, False]
        mock_float.return_value = 100.0
        result, adjusted_scale = prompt_official_mode("tpch", 0.1)
        assert result is True
        assert adjusted_scale == 100.0


class TestPromptSeed:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_no_seed_selected(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_seed()
        assert result is None

    @patch("benchbox.cli.benchmarks.IntPrompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_seed_selected(self, mock_confirm, mock_int_prompt):

        mock_confirm.return_value = True
        mock_int_prompt.return_value = 42
        result = prompt_seed()
        assert result == 42

    @patch("benchbox.cli.benchmarks.IntPrompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_custom_seed_value(self, mock_confirm, mock_int_prompt):

        mock_confirm.return_value = True
        mock_int_prompt.return_value = 12345
        result = prompt_seed()
        assert result == 12345


class TestPromptForceRegeneration:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_force_regeneration(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_force_regeneration()
        assert result is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_force_all(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "1"
        result = prompt_force_regeneration()
        assert result == "all"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_force_datagen(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "2"
        result = prompt_force_regeneration()
        assert result == "datagen"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_force_upload(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "3"
        result = prompt_force_regeneration()
        assert result == "upload"


class TestPromptValidationMode:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_validation_config(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_validation_mode()
        assert result is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_exact_validation(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "1"
        result = prompt_validation_mode()
        assert result == "exact"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_loose_validation(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "2"
        result = prompt_validation_mode()
        assert result == "loose"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_range_validation(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "3"
        result = prompt_validation_mode()
        assert result == "range"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_disabled_validation(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "4"
        result = prompt_validation_mode()
        assert result == "disabled"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_full_validation(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "5"
        result = prompt_validation_mode()
        assert result == "full"


class TestPromptCapturePlans:
    def test_unsupported_platform_returns_false(self):

        result = prompt_capture_plans("snowflake")
        assert result is False

    def test_unsupported_platform_sqlite(self):
        result = prompt_capture_plans("sqlite")
        assert result is False

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_supported_platform_decline(self, mock_confirm):
        mock_confirm.return_value = False
        result = prompt_capture_plans("duckdb")
        assert result is False

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_supported_platform_accept(self, mock_confirm):
        mock_confirm.return_value = True
        result = prompt_capture_plans("duckdb")
        assert result is True

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_postgresql_supported(self, mock_confirm):

        mock_confirm.return_value = True
        result = prompt_capture_plans("postgresql")
        assert result is True

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_datafusion_supported(self, mock_confirm):

        mock_confirm.return_value = True
        result = prompt_capture_plans("datafusion")
        assert result is True

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_case_insensitive(self, mock_confirm):

        mock_confirm.return_value = True
        result = prompt_capture_plans("DuckDB")
        assert result is True


class TestPromptOutputLocation:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_custom_output(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_output_location()
        assert result is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_empty_path_returns_none(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = ""
        result = prompt_output_location()
        assert result is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_valid_existing_path(self, mock_confirm, mock_prompt):

        from pathlib import Path

        mock_confirm.return_value = True
        mock_prompt.return_value = "/tmp"
        result = prompt_output_location()
        expected = str(Path("/tmp").resolve())
        assert result == expected

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_tilde_expansion(self, mock_confirm, mock_prompt):
        import os

        mock_confirm.return_value = True
        mock_prompt.return_value = "~"
        result = prompt_output_location()
        assert result == os.path.expanduser("~")

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_path_with_subdirectory(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "/tmp/benchbox_test_output"
        result = prompt_output_location()
        assert "benchbox_test_output" in result

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_nonexistent_parent_declined(self, mock_confirm, mock_prompt):

        mock_confirm.side_effect = [True, False]
        mock_prompt.return_value = "/nonexistent_parent_xyz/output"
        result = prompt_output_location()
        assert result is None


class TestGetPlatformFormatRecommendation:
    def test_databricks_recommends_delta(self):

        fmt, reason = get_platform_format_recommendation("databricks")
        assert fmt == "delta"
        assert "Delta" in reason

    def test_snowflake_recommends_iceberg(self):

        fmt, reason = get_platform_format_recommendation("snowflake")
        assert fmt == "iceberg"
        assert "Iceberg" in reason

    def test_duckdb_recommends_parquet(self):

        fmt, reason = get_platform_format_recommendation("duckdb")
        assert fmt == "parquet"
        assert "Parquet" in reason

    def test_postgresql_no_format_support(self):

        fmt, reason = get_platform_format_recommendation("postgresql")
        assert fmt is None
        assert "native" in reason.lower()

    def test_sqlite_no_format_support(self):

        fmt, reason = get_platform_format_recommendation("sqlite")
        assert fmt is None

    def test_case_insensitive(self):

        fmt1, _ = get_platform_format_recommendation("DuckDB")
        fmt2, _ = get_platform_format_recommendation("duckdb")
        assert fmt1 == fmt2 == "parquet"

    def test_unknown_platform_defaults_to_parquet(self):

        fmt, reason = get_platform_format_recommendation("unknown_platform")
        assert fmt == "parquet"
        assert "widely compatible" in reason


class TestPromptTableFormat:
    def test_unsupported_platform_returns_none(self):

        result = prompt_table_format("postgresql")
        assert result == (None, None)

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_configure_format(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_table_format("duckdb")
        assert result == (None, None)

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_csv(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.return_value = "1"
        fmt, compression = prompt_table_format("duckdb")
        assert fmt is None
        assert compression is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_parquet_with_compression(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = ["2", "2"]
        fmt, compression = prompt_table_format("duckdb")
        assert fmt == "parquet"
        assert compression == "zstd"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_vortex(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = ["3", "1"]
        fmt, compression = prompt_table_format("duckdb")
        assert fmt == "vortex"
        assert compression == "zstd"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_delta(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = ["4", "1"]
        fmt, compression = prompt_table_format("databricks")
        assert fmt == "delta"
        assert compression == "snappy"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_select_iceberg(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = ["5", "1"]
        fmt, compression = prompt_table_format("snowflake")
        assert fmt == "iceberg"
        assert compression == "zstd"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_no_compression(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = ["2", "4"]
        fmt, compression = prompt_table_format("duckdb")
        assert fmt == "parquet"
        assert compression is None

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_format_and_compression_returned_separately(self, mock_confirm, mock_prompt):
        mock_confirm.return_value = True
        mock_prompt.side_effect = ["5", "1"]
        fmt, compression = prompt_table_format("snowflake")

        assert fmt == "iceberg"
        assert ":" not in fmt
        assert compression == "zstd"

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_all_formats_return_valid_runner_names(self, mock_confirm, mock_prompt):
        allowed_formats = {"parquet", "vortex", "delta", "iceberg"}
        format_choices = {"2": "parquet", "3": "vortex", "4": "delta", "5": "iceberg"}

        for choice, expected_fmt in format_choices.items():
            mock_confirm.return_value = True
            mock_prompt.side_effect = [choice, "1"]
            fmt, _ = prompt_table_format("duckdb")
            assert fmt in allowed_formats, f"Format '{fmt}' from choice {choice} not in runner's allowed set"
            assert fmt == expected_fmt


class TestPromptVerboseOutput:
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_verbose(self, mock_confirm):
        mock_confirm.return_value = False
        result = prompt_verbose_output()
        assert result == 0

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_verbose_level_1(self, mock_confirm, mock_prompt):
        mock_confirm.return_value = True
        mock_prompt.return_value = "1"
        result = prompt_verbose_output()
        assert result == 1

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_verbose_level_2_debug(self, mock_confirm, mock_prompt):
        mock_confirm.return_value = True
        mock_prompt.return_value = "2"
        result = prompt_verbose_output()
        assert result == 2


class TestPromptPlatformOptions:
    def test_unknown_platform_returns_empty(self):

        result = prompt_platform_options("unknown_platform_xyz")
        assert result == {}

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_decline_configure(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_platform_options("duckdb")
        assert result == {}

    @patch("benchbox.cli.benchmarks.Prompt.ask")
    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_duckdb_memory_limit(self, mock_confirm, mock_prompt):

        mock_confirm.return_value = True
        mock_prompt.side_effect = [
            "16GB",
            "",
            "",
            "",
            "",
        ]
        result = prompt_platform_options("duckdb")
        assert "memory_limit" in result
        assert result["memory_limit"] == "16GB"

    @patch("benchbox.cli.benchmarks.Confirm.ask")
    def test_case_insensitive_platform(self, mock_confirm):

        mock_confirm.return_value = False
        result = prompt_platform_options("DuckDB")
        assert isinstance(result, dict)


class TestExecutionModeSelection:
    @patch("benchbox.cli.database.Prompt.ask")
    def test_sql_mode_selection(self, mock_ask):

        from benchbox.cli.database import DatabaseManager

        manager = DatabaseManager()
        mock_ask.return_value = "1"
        mode = manager._prompt_execution_mode("polars", "sql")
        assert mode == "sql"

    @patch("benchbox.cli.database.Prompt.ask")
    def test_dataframe_mode_selection(self, mock_ask):

        from benchbox.cli.database import DatabaseManager

        manager = DatabaseManager()
        mock_ask.return_value = "2"
        mode = manager._prompt_execution_mode("polars", "sql")
        assert mode == "dataframe"

    @patch("benchbox.cli.database.Prompt.ask")
    def test_default_mode_used(self, mock_ask):

        from benchbox.cli.database import DatabaseManager

        manager = DatabaseManager()
        mock_ask.return_value = "2"
        mode = manager._prompt_execution_mode("pyspark", "dataframe")
        assert mode == "dataframe"


class TestRunCommandImportIntegrity:
    def test_composite_param_classes_accessible(self):

        from benchbox.cli.commands.run import (
            CompressionConfig,
            ForceConfig,
            PlanCaptureConfig,
            TableFormatConfig,
            ValidationConfig,
        )

        force = ForceConfig()
        assert force.datagen is False
        assert force.upload is False

        compression = CompressionConfig()
        assert compression.enabled is True

        convert = TableFormatConfig()
        assert convert.format == "parquet"

        plan = PlanCaptureConfig()
        assert plan.strict is False

        validation = ValidationConfig()
        assert validation.mode == "exact"

    def test_run_function_importable(self):

        from benchbox.cli.commands.run import run

        assert hasattr(run, "callback")
        assert callable(run.callback)

    def test_no_self_referential_imports_in_run_module(self):
        import ast
        from pathlib import Path

        run_path = Path(__file__).parent.parent.parent.parent / "benchbox/cli/commands/run.py"
        source = run_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        self_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "benchbox.cli.commands.run" in node.module:
                    self_imports.append(f"line {node.lineno}: from {node.module} import ...")

        assert not self_imports, f"Self-referential imports found: {self_imports}"
