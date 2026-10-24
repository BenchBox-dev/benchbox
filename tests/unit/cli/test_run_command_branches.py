from __future__ import annotations

import logging
import os
import sys
import sys as _sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.run_resolution import ResolvedRunPlan, RunRequest

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]
__import__("benchbox.cli.run_platform_resolution")
_platform_module = _sys.modules["benchbox.cli.run_platform_resolution"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _minimal_resolved_run_plan() -> ResolvedRunPlan:
    request = RunRequest(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        phases=("power",),
        queries=None,
        tuning="notuning",
        table_mode="native",
        output=None,
        mode="sql",
        seed=None,
        compression_enabled=False,
        compression_type="none",
        compression_level=None,
    )
    return ResolvedRunPlan(
        request=request,
        platform_key="duckdb",
        benchmark="tpch",
        scale=0.01,
        phases=("power",),
        queries=None,
        test_execution_type="power",
        execution_mode="power",
        resolved_mode="sql",
        table_mode="native",
        tuning_resolution=MagicMock(canonical_mode=None),
        canonical_tuning_mode=None,
        tuning_enabled=False,
        tuning_config_file=None,
        use_auto_tuning=False,
        loaded_unified_config=None,
        data_organization=None,
        dataframe_tuning_config=None,
        compression_enabled=False,
        compression_type="none",
        compression_level=None,
        iterations=None,
        concurrency=1,
        seed=None,
        non_replayable_options=(),
    )


class TestNormalizeBenchmarkName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("tpch", "tpch"),
            ("TPC-H", "tpch"),
            ("tpc_h", "tpch"),
            ("TPC-DS", "tpcds"),
            ("tpcds-obt", "tpcds_obt"),
            ("star-schema", "ssb"),
        ],
    )
    def test_aliases(self, raw: str, expected: str):
        from benchbox.cli.commands.run import normalize_benchmark_name

        assert normalize_benchmark_name(raw) == expected

    def test_unknown_name_passthrough(self):
        from benchbox.cli.commands.run import normalize_benchmark_name

        assert normalize_benchmark_name("custom_benchmark") == "custom_benchmark"


class TestPlatformOptionParamType:
    def test_convert_valid(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        key, val = pt.convert("memory_limit=8GB", None, None)
        assert key == "memory_limit"
        assert val == "8GB"

    def test_convert_equals_in_value(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        key, val = pt.convert("setting=a=b=c", None, None)
        assert key == "setting"
        assert val == "a=b=c"

    def test_convert_no_equals_fails(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        with pytest.raises(Exception):
            pt.convert("no_equals_sign", None, MagicMock())

    def test_convert_empty_key_fails(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        with pytest.raises(Exception):
            pt.convert("=value", None, MagicMock())

    def test_convert_strips_whitespace(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        key, val = pt.convert("  key  =  value  ", None, None)
        assert key == "key"
        assert val == "value"

    def test_name_attribute(self):
        from benchbox.cli.commands.run import PlatformOptionParamType

        pt = PlatformOptionParamType()
        assert pt.name == "key=value"


class TestBuildExecutionContext:
    def test_basic(self):
        from benchbox.cli.commands.run import _build_execution_context
        from benchbox.cli.composite_params import CompressionConfig, ForceConfig, ValidationConfig

        ctx = _build_execution_context(
            phases_to_run=["load", "power"],
            seed=42,
            compression=CompressionConfig(enabled=True, type="zstd", level=3),
            mode="sql",
            official=False,
            validation=ValidationConfig(mode="exact"),
            force=ForceConfig(datagen=False, upload=False),
            queries_to_run=["Q1", "Q6"],
            capture_plans=True,
            strict_plan_capture=False,
            non_interactive=True,
            tuning="tuned",
        )
        assert ctx.entry_point == "cli"
        assert ctx.phases == ["load", "power"]
        assert ctx.seed == 42
        assert ctx.compression_enabled is True
        assert ctx.compression_type == "zstd"
        assert ctx.compression_level == 3
        assert ctx.mode == "sql"
        assert ctx.official is False
        assert ctx.validation_mode is None
        assert ctx.force_datagen is False
        assert ctx.query_subset == ["Q1", "Q6"]
        assert ctx.capture_plans is True
        assert ctx.non_interactive is True
        assert ctx.tuning_mode == "tuned"

    def test_mode_defaults_to_sql(self):
        from benchbox.cli.commands.run import _build_execution_context
        from benchbox.cli.composite_params import CompressionConfig, ForceConfig, ValidationConfig

        ctx = _build_execution_context(
            phases_to_run=["power"],
            seed=None,
            compression=CompressionConfig(enabled=False, type="none", level=None),
            mode=None,
            official=False,
            validation=ValidationConfig(mode="loose"),
            force=ForceConfig(datagen=True, upload=False),
            queries_to_run=None,
            capture_plans=False,
            strict_plan_capture=False,
            non_interactive=False,
            tuning="notuning",
        )
        assert ctx.mode == "sql"
        assert ctx.seed is None
        assert ctx.query_subset is None
        assert ctx.tuning_mode == "notuning"
        assert ctx.validation_mode == "loose"
        assert ctx.force_datagen is True

    def test_absent_tuning_stays_not_recorded(self):
        from benchbox.cli.commands.run import _build_execution_context
        from benchbox.cli.composite_params import CompressionConfig, ForceConfig, ValidationConfig

        ctx = _build_execution_context(
            phases_to_run=["power"],
            seed=None,
            compression=CompressionConfig(enabled=False, type="none", level=None),
            mode=None,
            official=False,
            validation=ValidationConfig(mode="exact"),
            force=ForceConfig(datagen=False, upload=False),
            queries_to_run=None,
            capture_plans=False,
            strict_plan_capture=False,
            non_interactive=False,
            tuning=None,
        )

        assert ctx.tuning_mode is None

    def test_official_mode(self):
        from benchbox.cli.commands.run import _build_execution_context
        from benchbox.cli.composite_params import CompressionConfig, ForceConfig, ValidationConfig

        ctx = _build_execution_context(
            phases_to_run=["power"],
            seed=12345,
            compression=CompressionConfig(enabled=True, type="gzip", level=6),
            mode="dataframe",
            official=True,
            validation=ValidationConfig(mode="full"),
            force=ForceConfig(datagen=False, upload=True),
            queries_to_run=None,
            capture_plans=False,
            strict_plan_capture=True,
            non_interactive=True,
            tuning="auto",
        )
        assert ctx.official is True
        assert ctx.mode == "dataframe"
        assert ctx.strict_plan_capture is True
        assert ctx.force_upload is True
        assert ctx.tuning_mode == "auto"


class TestApplyPlatformOptimizationOverrides:
    def test_databricks_platform_options_update_unified_tuning(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()

        _apply_platform_optimization_overrides(
            unified,
            sorted_ingestion_mode=None,
            sorted_ingestion_method=None,
            parsed_platform_options={
                "databricks_clustering_strategy": "liquid_clustering",
                "liquid_clustering_columns": "event_time,customer_id",
            },
        )

        assert unified.platform_optimizations.databricks_clustering_strategy == "liquid_clustering"
        assert unified.platform_optimizations.liquid_clustering_enabled is True
        assert unified.platform_optimizations.liquid_clustering_columns == ["event_time", "customer_id"]

    def test_clustering_strategy_via_platform_options(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()

        _apply_platform_optimization_overrides(
            unified,
            sorted_ingestion_mode=None,
            sorted_ingestion_method=None,
            parsed_platform_options={"databricks_clustering_strategy": "none"},
        )

        assert unified.platform_optimizations.databricks_clustering_strategy == "none"
        assert unified.platform_optimizations.liquid_clustering_enabled is False

    def test_liquid_auto_strategy_via_platform_options(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()

        _apply_platform_optimization_overrides(
            unified,
            sorted_ingestion_mode=None,
            sorted_ingestion_method=None,
            parsed_platform_options={"databricks_clustering_strategy": "liquid_clustering_auto"},
        )

        assert unified.platform_optimizations.databricks_clustering_strategy == "liquid_clustering_auto"
        assert unified.platform_optimizations.liquid_clustering_enabled is True

    def test_liquid_strategy_rejects_legacy_zorder_template_fields(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()
        unified.platform_optimizations.z_ordering_enabled = True

        with pytest.raises(ValueError, match="Liquid Clustering cannot be combined"):
            _apply_platform_optimization_overrides(
                unified,
                sorted_ingestion_mode=None,
                sorted_ingestion_method=None,
                parsed_platform_options={"databricks_clustering_strategy": "liquid_clustering_auto"},
            )

    def test_strategy_override_resets_stale_physical_rendering_id(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()
        unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering_auto"
        unified.platform_optimizations.liquid_clustering_enabled = True
        unified.platform_optimizations.physical_rendering_id = "databricks_liquid_auto"

        _apply_platform_optimization_overrides(
            unified,
            sorted_ingestion_mode=None,
            sorted_ingestion_method=None,
            parsed_platform_options={"databricks_clustering_strategy": "z_order"},
        )

        assert unified.platform_optimizations.databricks_clustering_strategy == "z_order"
        assert unified.platform_optimizations.liquid_clustering_enabled is False
        assert unified.platform_optimizations.physical_rendering_id is None

    def test_sorted_ingestion_validation_is_preserved(self):
        from benchbox.cli.commands.run import _apply_platform_optimization_overrides
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration

        unified = UnifiedTuningConfiguration()

        with pytest.raises(
            ValueError, match="--sorted-ingestion-method requires --sorted-ingestion-mode auto or force"
        ):
            _apply_platform_optimization_overrides(
                unified,
                sorted_ingestion_mode="off",
                sorted_ingestion_method="ctas",
                parsed_platform_options={},
            )


class TestRenderPostRunCharts:
    def test_quiet_mode_skips(self):
        from benchbox.cli.commands.run import _render_post_run_charts

        mock_result = MagicMock()
        mock_console = MagicMock()
        _render_post_run_charts(mock_result, mock_console, quiet=True)
        mock_console.print.assert_not_called()

    def test_empty_query_results_skips(self):
        from benchbox.cli.commands.run import _render_post_run_charts

        mock_result = MagicMock()
        mock_result.query_results = []
        mock_console = MagicMock()
        _render_post_run_charts(mock_result, mock_console, quiet=False)
        mock_console.print.assert_not_called()

    def test_renders_charts(self):
        from benchbox.cli.commands.run import _render_post_run_charts

        mock_result = MagicMock()
        mock_result.query_results = [MagicMock()]
        mock_console = MagicMock()

        mock_summary = MagicMock()
        mock_summary.charts = ["chart1", "chart2"]

        with patch(
            "benchbox.core.visualization.post_run_summary.generate_post_run_summary",
            return_value=mock_summary,
        ):
            _render_post_run_charts(mock_result, mock_console, quiet=False)
        assert mock_console.print.call_count == 2

    def test_exception_swallowed(self):
        from benchbox.cli.commands.run import _render_post_run_charts

        mock_result = MagicMock()
        mock_result.query_results = [MagicMock()]
        mock_console = MagicMock()

        with patch(
            "benchbox.core.visualization.post_run_summary.generate_post_run_summary",
            side_effect=ImportError("no module"),
        ):
            _render_post_run_charts(mock_result, mock_console, quiet=False)
        mock_console.print.assert_not_called()


class TestDirectHandleResult:
    def test_partial_query_failure_exports_but_exits_and_skips_publish(self):
        from benchbox.cli.commands.run import _direct_handle_result

        ctx = MagicMock()
        ctx.exit.side_effect = SystemExit
        settings = SimpleNamespace(
            ctx=ctx,
            quiet=False,
            benchmark="tpch",
            scale=0.01,
            platform="duckdb",
            resolved_mode="sql",
            publish=True,
            publish_target="benchmark_runs/published",
            publish_label="maintainer-run",
            tuning="notuning",
            phases_to_run=["power"],
            compress_data=False,
            compression_type="none",
            compression_level=None,
            test_execution_type="power",
            seed=None,
            output=None,
            table_mode="native",
            resolved_run_plan=_minimal_resolved_run_plan(),
        )
        result = SimpleNamespace(
            validation_status="PARTIAL",
            total_queries=2,
            successful_queries=1,
            failed_queries=1,
        )
        benchmark_config = SimpleNamespace(concurrency=1)

        with (
            patch.object(
                _run_module, "_export_orchestrated_result", return_value={"json": "/tmp/result.json"}
            ) as export,
            patch.object(_run_module, "_render_post_run_charts") as render_charts,
            patch("benchbox.cli.preferences.save_last_run_config") as save_last_run,
            patch("benchbox.cli.commands.publish.publish_bundle") as publish_bundle,
            pytest.raises(SystemExit),
        ):
            _direct_handle_result(settings, result, MagicMock(), benchmark_config)

        export.assert_called_once()
        render_charts.assert_called_once()
        save_last_run.assert_called_once()
        assert save_last_run.call_args.kwargs["iterations"] is None
        assert save_last_run.call_args.kwargs["non_replayable_options"] == []
        publish_bundle.assert_not_called()
        ctx.exit.assert_called_once_with(1)


class TestSetupVerboseLogging:
    def test_quiet_mode(self):
        from benchbox.cli.commands.run import setup_verbose_logging

        logger_result, settings = setup_verbose_logging(verbose=0, quiet=True)
        assert logger_result is None
        assert settings.quiet is True

    def test_verbose_level_1(self):
        from benchbox.cli.commands.run import setup_verbose_logging

        logger_result, settings = setup_verbose_logging(verbose=1, quiet=False)
        assert logger_result is not None
        assert settings.verbose_enabled is True
        assert settings.very_verbose is False

    def test_verbose_level_2(self):
        from benchbox.cli.commands.run import setup_verbose_logging

        logger_result, settings = setup_verbose_logging(verbose=2, quiet=False)
        assert logger_result is not None
        assert settings.very_verbose is True

    def test_no_verbose_no_quiet(self):
        from benchbox.cli.commands.run import setup_verbose_logging

        logger_result, settings = setup_verbose_logging(verbose=0, quiet=False)
        assert logger_result is None
        assert settings.quiet is False
        assert settings.verbose_enabled is False

    def test_verbosity_settings_passthrough(self):
        from benchbox.cli.commands.run import setup_verbose_logging
        from benchbox.utils.verbosity import VerbositySettings

        vs = VerbositySettings.from_flags(2, False)
        logger_result, settings = setup_verbose_logging(verbose=vs, quiet=False)
        assert settings is vs
        assert logger_result is not None

    def test_verbosity_settings_quiet_override(self):
        from benchbox.cli.commands.run import setup_verbose_logging
        from benchbox.utils.verbosity import VerbositySettings

        vs = VerbositySettings.from_flags(1, False)
        logger_result, settings = setup_verbose_logging(verbose=vs, quiet=True)
        assert settings.quiet is True


class TestErrorHandlerBranches:
    def test_handle_database_error(self):
        from benchbox.cli.exceptions import DatabaseError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = DatabaseError("connection refused", include_version=False)
        ctx = ErrorContext(operation="load", stage="connect", database_type="duckdb")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_database_error_postgresql(self):
        from benchbox.cli.exceptions import DatabaseError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = DatabaseError("connection refused", include_version=False)
        ctx = ErrorContext(operation="load", stage="connect", database_type="postgresql")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_execution_error_tpch(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler, ExecutionError

        handler = ErrorHandler(console=MagicMock())
        err = ExecutionError("out of memory", include_version=False)
        ctx = ErrorContext(operation="power", stage="query", benchmark_name="tpch")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_execution_error_no_benchmark(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler, ExecutionError

        handler = ErrorHandler(console=MagicMock())
        err = ExecutionError("timeout", include_version=False)
        ctx = ErrorContext(operation="power", stage="query")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_validation_error(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler, ValidationError

        handler = ErrorHandler(console=MagicMock())
        err = ValidationError("bad input", include_version=False)
        ctx = ErrorContext(operation="validate", stage="input")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_cloud_storage_error_s3(self):
        from benchbox.cli.exceptions import CloudStorageError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = CloudStorageError("access denied", details={"provider": "s3"}, include_version=False)
        ctx = ErrorContext(operation="upload", stage="transfer")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_cloud_storage_error_gcs(self):
        from benchbox.cli.exceptions import CloudStorageError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = CloudStorageError("not found", details={"provider": "gs"}, include_version=False)
        ctx = ErrorContext(operation="upload", stage="transfer")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_cloud_storage_error_azure(self):
        from benchbox.cli.exceptions import CloudStorageError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = CloudStorageError("forbidden", details={"provider": "azure"}, include_version=False)
        ctx = ErrorContext(operation="upload", stage="transfer")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_platform_error(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler, PlatformError

        handler = ErrorHandler(console=MagicMock())
        err = PlatformError("driver missing", include_version=False)
        ctx = ErrorContext(operation="connect", stage="init")
        handler.handle_error(err, ctx)
        assert handler.console.print.called

    def test_handle_generic_cli_error(self):
        from benchbox.cli.exceptions import BenchboxCLIError, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = BenchboxCLIError("generic", include_version=False)
        handler.handle_error(err)
        assert handler.console.print.called

    def test_handle_generic_error_with_traceback(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = RuntimeError("unexpected")
        ctx = ErrorContext(operation="run", stage="execute", include_version_info=False)
        handler.handle_error(err, ctx, show_traceback=True)
        assert handler.console.print.called

    def test_handle_cli_error_with_traceback(self):
        from benchbox.cli.exceptions import BenchboxCLIError, ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = BenchboxCLIError("test error", include_version=False)
        ctx = ErrorContext(operation="run", stage="test", include_version_info=False)
        handler.handle_error(err, ctx, show_traceback=True)
        assert handler.console.print.called

    def test_handle_error_with_version_details(self):
        from benchbox.cli.exceptions import BenchboxCLIError, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        err = BenchboxCLIError(
            "test",
            details={"version_warning": "mismatch", "version_info": "1.0"},
            include_version=False,
        )
        handler.handle_error(err)
        assert handler.console.print.called

    def test_show_context_with_source_location(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        ctx = ErrorContext(
            operation="run",
            stage="test",
            source_file="/path/to/benchbox/core/test.py",
            source_line=42,
            source_function="my_function",
            include_version_info=False,
        )
        handler._show_context_info(ctx)
        assert handler.console.print.called

    def test_show_context_non_benchbox_source(self):
        from benchbox.cli.exceptions import ErrorContext, ErrorHandler

        handler = ErrorHandler(console=MagicMock())
        ctx = ErrorContext(
            operation="run",
            stage="test",
            source_file="/some/other/path/module.py",
            source_line=10,
            include_version_info=False,
        )
        handler._show_context_info(ctx)
        assert handler.console.print.called


class TestValidationRules:
    def test_validate_scale_factor_negative(self):
        from benchbox.cli.exceptions import ValidationError, ValidationRules

        with pytest.raises(ValidationError):
            ValidationRules.validate_scale_factor(-1.0)

    def test_validate_scale_factor_zero(self):
        from benchbox.cli.exceptions import ValidationError, ValidationRules

        with pytest.raises(ValidationError):
            ValidationRules.validate_scale_factor(0.0)

    def test_validate_scale_factor_too_large(self):
        from benchbox.cli.exceptions import ValidationError, ValidationRules

        with pytest.raises(ValidationError, match="very large"):
            ValidationRules.validate_scale_factor(200.0)

    def test_validate_scale_factor_valid(self):
        from benchbox.cli.exceptions import ValidationRules

        assert ValidationRules.validate_scale_factor(1.0) is None

    def test_validate_benchmark_name_empty(self):
        from benchbox.cli.exceptions import ValidationError, ValidationRules

        with pytest.raises(ValidationError):
            ValidationRules.validate_benchmark_name("", ["tpch", "tpcds"])

    def test_validate_benchmark_name_unknown(self):
        from benchbox.cli.exceptions import ValidationError, ValidationRules

        with pytest.raises(ValidationError, match="Unknown benchmark"):
            ValidationRules.validate_benchmark_name("nonexistent", ["tpch", "tpcds"])

    def test_validate_benchmark_name_case_insensitive(self):
        from benchbox.cli.exceptions import ValidationRules

        assert ValidationRules.validate_benchmark_name("TPCH", ["tpch", "tpcds"]) is None

    def test_validate_output_directory_local(self, tmp_path):
        from benchbox.cli.exceptions import ValidationRules

        out = str(tmp_path / "new_dir")
        ValidationRules.validate_output_directory(out)
        assert (tmp_path / "new_dir").exists()

    def test_validate_output_directory_cloud(self):
        from benchbox.cli.exceptions import CloudStorageError, ValidationRules

        with patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=True):
            with patch(
                "benchbox.utils.cloud_storage.validate_cloud_credentials",
                return_value={"valid": False, "provider": "s3", "error": "no creds", "env_vars": ["AWS_ACCESS_KEY_ID"]},
            ):
                with pytest.raises(CloudStorageError):
                    ValidationRules.validate_output_directory("s3://bucket/path")


class TestErrorContext:
    def test_basic_creation(self):
        from benchbox.cli.exceptions import ErrorContext

        ctx = ErrorContext(operation="run", stage="init", include_version_info=False)
        assert ctx.operation == "run"
        assert ctx.stage == "init"
        assert ctx.benchmark_name is None

    def test_with_all_fields(self):
        from benchbox.cli.exceptions import ErrorContext

        ctx = ErrorContext(
            operation="run",
            stage="power",
            benchmark_name="tpch",
            database_type="duckdb",
            user_input={"scale": 1.0},
            include_version_info=False,
        )
        assert ctx.benchmark_name == "tpch"
        assert ctx.database_type == "duckdb"


class TestBenchboxCLIErrorSourceLocation:
    def test_captures_source_location(self):
        from benchbox.cli.exceptions import BenchboxCLIError

        err = BenchboxCLIError("test", include_version=False)
        assert err.source_file is not None
        assert err.source_line is not None
        assert os.path.basename(err.source_file) == "test_run_command_branches.py"

    def test_with_details(self):
        from benchbox.cli.exceptions import BenchboxCLIError

        err = BenchboxCLIError("test", details={"key": "value"}, include_version=False)
        assert err.details["key"] == "value"

    def test_configuration_error(self):
        from benchbox.cli.exceptions import ConfigurationError

        err = ConfigurationError("bad config", include_version=False)
        assert err.message == "bad config"
        assert err.source_file is not None


class TestCreateErrorHandler:
    def test_default(self):
        from benchbox.cli.exceptions import ErrorHandler, create_error_handler

        handler = create_error_handler()
        assert isinstance(handler, ErrorHandler)

    def test_with_console(self):
        from benchbox.cli.exceptions import ErrorHandler, create_error_handler

        mock = MagicMock()
        handler = create_error_handler(mock)
        assert isinstance(handler, ErrorHandler)
        assert handler.console is mock


class TestRunCommandBranchCoverage:
    def setup_method(self):
        self.runner = CliRunner()
        self._orig_non_interactive = os.environ.get("BENCHBOX_NON_INTERACTIVE")

    def teardown_method(self):
        if self._orig_non_interactive is None:
            os.environ.pop("BENCHBOX_NON_INTERACTIVE", None)
        else:
            os.environ["BENCHBOX_NON_INTERACTIVE"] = self._orig_non_interactive

    def test_official_with_seed_and_platform_option_without_platform(self):
        from benchbox.cli.commands.run import run

        result = self.runner.invoke(
            run,
            [
                "--official",
                "--scale",
                "1",
                "--seed",
                "7",
                "--benchmark",
                "tpch",
                "--phases",
                "generate",
                "--platform-option",
                "threads=4",
            ],
            obj={},
        )

        assert result.exit_code == 1
        assert "Platform options require a --platform selection" in result.output

    def test_platform_option_parse_error_logs_and_exits(self):
        from benchbox.cli.commands.run import PlatformOptionError, run

        with patch.object(_platform_module.PlatformHookRegistry, "parse_options") as parse_options:
            parse_options.side_effect = PlatformOptionError("invalid option")
            result = self.runner.invoke(
                run,
                [
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--phases",
                    "generate",
                    "--platform-option",
                    "threads=4",
                    "--verbose",
                ],
                obj={},
            )

        assert result.exit_code == 1
        assert "invalid option" in result.output

    def test_non_interactive_missing_required_args(self):
        from benchbox.cli.commands.run import run

        result = self.runner.invoke(
            run,
            [
                "--non-interactive",
                "--verbose",
                "--phases",
                "load",
            ],
            obj={},
        )

        assert result.exit_code == 2
        assert "Non-interactive mode requires all parameters" in result.output


class TestDeriveExecutionType:
    @pytest.fixture(autouse=True)
    def _import(self):
        from benchbox.cli.commands.run import _derive_execution_type

        self.fn = _derive_execution_type

    @pytest.mark.parametrize(
        ("phases", "expected"),
        [
            (["power"], "power"),
            (["throughput"], "throughput"),
            (["maintenance"], "maintenance"),
            (["power", "throughput"], "combined"),
            (["power", "maintenance"], "combined"),
        ],
    )
    def test_query_phases(self, phases, expected):
        assert self.fn(phases) == expected

    @pytest.mark.parametrize(
        "phases",
        [
            ["load"],
            ["generate", "load"],
            ["load", "warmup"],
            ["load", "statistics"],
            ["generate", "load", "statistics"],
        ],
    )
    def test_load_only(self, phases):
        assert self.fn(phases) == "load_only"

    @pytest.mark.parametrize(
        "phases",
        [
            ["generate"],
            ["generate", "warmup"],
        ],
    )
    def test_data_only(self, phases):
        assert self.fn(phases) == "data_only"

    @pytest.mark.parametrize(
        "phases",
        [
            ["generate", "load"],
            ["generate", "power"],
            ["generate", "throughput"],
            ["generate", "maintenance"],
        ],
    )
    def test_generate_with_load_or_query_not_data_only(self, phases):
        assert self.fn(phases) != "data_only"

    def test_standard_fallback(self):
        assert self.fn(["warmup"]) == "standard"
        assert self.fn(["load", "power"]) == "power"


class TestDataFrameSuffixModeResolution:
    def _resolved_state(self, platform: str, mode: str | None = None) -> SimpleNamespace:
        from benchbox.cli.commands.run import _apply_dataframe_suffix_mode, _resolve_platform_mode
        from benchbox.cli.platform import normalize_platform_name

        s = SimpleNamespace(
            platform=platform,
            mode=mode,
            dry_run=True,
            ctx=MagicMock(),
            logger=None,
            platform_manager=MagicMock(),
        )
        _apply_dataframe_suffix_mode(s)
        s.platform_key = normalize_platform_name(s.platform)
        _resolve_platform_mode(s)
        return s

    @pytest.mark.parametrize("platform", ["datafusion-df", "lakesail-df", "pyspark-df", "polars-df"])
    def test_df_suffix_resolves_dataframe_mode_without_mode_flag(self, platform: str):
        s = self._resolved_state(platform)
        assert s.resolved_mode == "dataframe"

    def test_df_suffix_is_case_insensitive(self):
        s = self._resolved_state("DataFusion-DF")
        assert s.resolved_mode == "dataframe"

    @pytest.mark.parametrize(("platform", "expected"), [("datafusion", "sql"), ("lakesail", "sql")])
    def test_base_name_keeps_registry_default_mode(self, platform: str, expected: str):
        s = self._resolved_state(platform)
        assert s.resolved_mode == expected

    def test_explicit_mode_flag_wins_over_df_suffix(self):
        s = self._resolved_state("datafusion-df", mode="sql")
        assert s.resolved_mode == "sql"

    def test_no_suffix_leaves_mode_unset(self):
        from benchbox.cli.commands.run import _apply_dataframe_suffix_mode

        s = SimpleNamespace(platform="duckdb", mode=None)
        _apply_dataframe_suffix_mode(s)
        assert s.mode is None

    def test_missing_platform_is_noop(self):
        from benchbox.cli.commands.run import _apply_dataframe_suffix_mode

        s = SimpleNamespace(platform=None, mode=None)
        _apply_dataframe_suffix_mode(s)
        assert s.mode is None


class TestPlatformDeploymentSelectorAvailability:
    @staticmethod
    def _resolve(platform: str, available: dict[str, bool], *, dry_run: bool = False, mode: str | None = None):
        from benchbox.cli.platform import PlatformManager, normalize_platform_name

        manager = PlatformManager()
        s = SimpleNamespace(
            platform=platform,
            platform_key=normalize_platform_name(platform),
            mode=mode,
            dry_run=dry_run,
            ctx=MagicMock(),
            logger=None,
            platform_manager=manager,
        )
        s.ctx.exit.side_effect = SystemExit
        printed = MagicMock()
        with (
            patch.object(_platform_module, "console", printed),
            patch(
                "benchbox.core.platform_registry.PlatformRegistry.get_platform_availability",
                return_value=available,
            ),
            patch(
                "benchbox.core.platform_registry.PlatformRegistry.is_platform_available",
                side_effect=lambda name: available.get(name, False),
            ),
        ):
            try:
                _run_module._resolve_platform_mode(s)
                exited = False
            except SystemExit:
                exited = True
        output = "\n".join(str(call.args[0]) for call in printed.print.call_args_list)
        return s, exited, output

    @pytest.mark.parametrize(
        ("selector", "resolved_platform"),
        [
            ("clickhouse:local", "clickhouse-local"),
            ("clickhouse:server", "clickhouse-server"),
            ("clickhouse:cloud", "clickhouse-cloud"),
            ("firebolt:core", "firebolt"),
            ("firebolt:cloud", "firebolt"),
            ("timescaledb:cloud", "timescaledb"),
            ("CLICKHOUSE:LOCAL", "clickhouse-local"),
        ],
    )
    def test_selector_passes_when_resolved_platform_is_available(self, selector: str, resolved_platform: str):
        s, exited, output = self._resolve(selector, {resolved_platform: True})
        assert not exited, output
        assert s.resolved_mode == "sql"

    @pytest.mark.parametrize(
        ("selector", "resolved_platform"),
        [("clickhouse:local", "clickhouse-local"), ("firebolt:core", "firebolt")],
    )
    def test_unavailable_selector_reports_install_hint_for_a_valid_extra(self, selector: str, resolved_platform: str):
        _s, exited, output = self._resolve(selector, {resolved_platform: False})
        assert exited
        assert f"Platform '{resolved_platform}' is not available" in output
        assert f"--extra {resolved_platform}" in output or f"benchbox[{resolved_platform}]" in output
        assert selector not in output

    def test_unavailable_selector_is_tolerated_in_dry_run(self):
        s, exited, _output = self._resolve("clickhouse:local", {"clickhouse-local": False}, dry_run=True)
        assert not exited
        assert s.resolved_mode == "sql"

    @pytest.mark.parametrize(
        ("selector", "resolved_platform"),
        [("timescaledb:cloud", "timescaledb"), ("pg-duckdb:motherduck", "pg-duckdb")],
    )
    def test_unavailable_psycopg_selector_names_the_postgresql_extra(self, selector: str, resolved_platform: str):
        _s, exited, output = self._resolve(selector, {resolved_platform: False})
        assert exited
        assert f"Platform '{resolved_platform}' is not available" in output
        assert "--extra postgresql" in output or "benchbox[postgresql]" in output
        assert f"--extra {resolved_platform}" not in output
        assert f"benchbox[{resolved_platform}]" not in output

    @pytest.mark.parametrize(
        ("selector", "resolved_platform", "mode"),
        [
            ("firebolt:core", "firebolt", "sql"),
            ("clickhouse:local", "clickhouse-local", "sql"),
            ("timescaledb:cloud", "timescaledb", "sql"),
        ],
    )
    def test_selector_with_a_supported_explicit_mode_passes(self, selector: str, resolved_platform: str, mode: str):
        s, exited, output = self._resolve(selector, {resolved_platform: True}, mode=mode)
        assert not exited, output
        assert s.resolved_mode == mode

    @pytest.mark.parametrize(
        ("selector", "resolved_platform"),
        [("firebolt:core", "firebolt"), ("clickhouse:local", "clickhouse-local"), ("timescaledb:cloud", "timescaledb")],
    )
    def test_selector_with_an_unsupported_explicit_mode_is_rejected(self, selector: str, resolved_platform: str):
        s, exited, output = self._resolve(selector, {resolved_platform: True}, mode="dataframe")
        assert exited
        assert f"Platform '{resolved_platform}' does not support dataframe mode" in output
        assert "Supported modes: sql" in output
        assert s.resolved_mode is None

    def test_unsupported_explicit_mode_is_rejected_before_availability(self):
        _s, exited, output = self._resolve("firebolt:core", {"firebolt": False}, mode="dataframe")
        assert exited
        assert "does not support dataframe mode" in output
        assert "not available" not in output

    def test_invalid_deployment_is_rejected_before_the_explicit_mode_is_checked(self):
        _s, exited, output = self._resolve("firebolt:bogus", {}, mode="dataframe")
        assert exited
        assert "does not support deployment mode 'bogus'" in output
        assert "dataframe mode" not in output

    @pytest.mark.parametrize("selector", ["clickhouse:bogus", "firebolt:bogus", "duckdb:bogus", "polars:managed"])
    def test_unknown_deployment_mode_is_rejected_even_in_dry_run(self, selector: str):
        _s, exited, output = self._resolve(selector, {}, dry_run=True)
        assert exited
        assert "does not support deployment mode" in output

    def test_unknown_deployment_mode_lists_available_modes(self):
        _s, _exited, output = self._resolve("firebolt:bogus", {})
        assert "core, cloud" in output

    def test_deployment_on_a_platform_without_modes_says_to_remove_the_suffix(self):
        _s, exited, output = self._resolve("polars:managed", {}, dry_run=True)
        assert exited
        assert "does not support deployment modes. Remove the ':managed' suffix." in output

    @pytest.mark.parametrize("selector", ["polars:local", "databricks:local", "snowflake:local", "sqlite:local"])
    def test_local_selector_on_a_platform_without_modes_agrees_with_the_adapter_factory(self, selector: str):
        from benchbox.cli.platform import resolve_platform_selector
        from benchbox.core.platform_registry import PlatformRegistry
        from benchbox.platforms.adapter_factory import _normalize_platform_name

        base, _df_implied, deployment = _normalize_platform_name(selector)
        assert PlatformRegistry.get_available_deployment_modes(base) == []
        assert PlatformRegistry.supports_deployment_mode(base, deployment)
        assert resolve_platform_selector(selector) == base

    @pytest.mark.parametrize("selector", ["polars:managed", "snowflake:cloud", "timescaledb:local", "firebolt:local"])
    def test_resolver_rejects_what_the_registry_rejects(self, selector: str):
        from benchbox.cli.platform import resolve_platform_selector
        from benchbox.core.platform_registry import PlatformRegistry
        from benchbox.platforms.adapter_factory import _normalize_platform_name

        base, _df_implied, deployment = _normalize_platform_name(selector)
        assert not PlatformRegistry.supports_deployment_mode(base, deployment)
        with pytest.raises(ValueError, match="does not support deployment mode"):
            resolve_platform_selector(selector)

    @pytest.mark.parametrize("platform_key", ["clickhouse-local", "clickhouse:local"])
    def test_benchmark_gate_applies_to_the_resolved_platform(self, platform_key: str):
        s = SimpleNamespace(platform_key=platform_key, benchmark="metadata_primitives", logger=None, ctx=MagicMock())
        printed = MagicMock()
        with patch.object(_platform_module, "console", printed):
            _run_module._check_benchmark_platform_compatibility(s)
        s.ctx.exit.assert_called_once_with(1)
