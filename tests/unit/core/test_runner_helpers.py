from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.driver_metadata import apply_driver_metadata
from benchbox.core.runner.runner import (
    ValidationOptions,
    _emit_manifest_reuse_message,
    _enrich_driver_runtime_metadata,
    _execute_load_only_mode,
    _finalize_validation_metadata,
    _flatten_manifest_v2_entries,
    _get_table_schemas_from_benchmark,
    _read_datagen_stats_from_manifest,
    _resolve_manifest_allowed_names,
    _resolve_manifest_entry_paths,
    _resolve_output_dir_handler,
    _run_format_conversion,
    _run_manifest_validation,
    _run_postload_validation,
    _run_preflight_validation,
)
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.core.validation import ValidationResult
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _benchmark_config(
    name: str = "tpch", *, scale_factor: float = 0.01, options: dict | None = None
) -> BenchmarkConfig:
    return BenchmarkConfig(
        name=name,
        display_name=name.upper(),
        scale_factor=scale_factor,
        options=options or {},
    )


class TestResolveManifestAllowedNames:
    def test_includes_data_source_alias_when_present(self):
        benchmark = SimpleNamespace(get_data_source_benchmark=lambda: "TPC-H")

        allowed = _resolve_manifest_allowed_names(benchmark, _benchmark_config("primitives"))

        assert allowed == {"primitives", "tpc-h"}

    def test_ignores_not_implemented_and_non_string_aliases(self, caplog):
        class AliasBenchmark:
            def get_data_source_benchmark(self):
                raise NotImplementedError

        class NonStringAliasBenchmark:
            def get_data_source_benchmark(self):
                return 123

        assert _resolve_manifest_allowed_names(AliasBenchmark(), _benchmark_config("tpch")) == {"tpch"}

        with caplog.at_level("WARNING"):
            allowed = _resolve_manifest_allowed_names(NonStringAliasBenchmark(), _benchmark_config("tpch"))

        assert allowed == {"tpch"}
        assert "Unexpected alias type" in caplog.text


class _OutputDirTrackingBenchmark:
    def __init__(self, value):
        self._output_dir = value
        self.set_count = 0

    @property
    def output_dir(self):
        return self._output_dir

    @output_dir.setter
    def output_dir(self, value):
        self._output_dir = value
        self.set_count += 1


class TestOutputDirResolution:
    def test_resolve_output_dir_handler_skips_reassignment_when_already_resolved(self):
        benchmark = _OutputDirTrackingBenchmark(Path("/tmp/resolved"))

        result = _resolve_output_dir_handler(benchmark, "/tmp/resolved")

        assert result == Path("/tmp/resolved")
        assert benchmark.set_count == 0

    def test_resolve_output_dir_handler_assigns_when_root_differs(self):
        benchmark = _OutputDirTrackingBenchmark(Path("/tmp/original"))

        result = _resolve_output_dir_handler(benchmark, "/tmp/override")

        assert result == Path("/tmp/override")
        assert benchmark.output_dir == Path("/tmp/override")
        assert benchmark.set_count == 1

    def test_resolve_output_dir_handler_normalizes_existing_string(self):
        benchmark = _OutputDirTrackingBenchmark("/tmp/existing")

        result = _resolve_output_dir_handler(benchmark, None)

        assert result == Path("/tmp/existing")
        assert benchmark.output_dir == Path("/tmp/existing")
        assert benchmark.set_count == 1

    def test_resolve_output_dir_handler_assigns_databricks_handler_with_matching_local_cache(self):
        from benchbox.utils.cloud_storage import DatabricksPath

        local_cache = Path("/tmp/datagen/tpch_sf1")
        benchmark = _OutputDirTrackingBenchmark(local_cache)
        handler = DatabricksPath(str(local_cache), "dbfs:/Volumes/cat/schema/vol")
        assert handler == local_cache

        result = _resolve_output_dir_handler(benchmark, handler)

        assert result is handler
        assert benchmark.output_dir is handler
        assert benchmark.output_dir.dbfs_target == "dbfs:/Volumes/cat/schema/vol"
        assert benchmark.set_count == 1

    def test_resolve_output_dir_handler_prefers_output_root(self):
        benchmark = SimpleNamespace(output_dir=Path("/tmp/original"))

        with patch("benchbox.core.runner.runner.create_path_handler", return_value=Path("/tmp/override")) as mock_path:
            result = _resolve_output_dir_handler(benchmark, "/tmp/override")

        assert result == Path("/tmp/override")
        assert benchmark.output_dir == Path("/tmp/override")
        mock_path.assert_called_once_with("/tmp/override")

    def test_resolve_output_dir_handler_reuses_existing_benchmark_output_dir(self):
        benchmark = SimpleNamespace(output_dir=Path("/tmp/existing"))

        with patch("benchbox.core.runner.runner.create_path_handler", return_value=Path("/tmp/existing")) as mock_path:
            result = _resolve_output_dir_handler(benchmark, None)

        assert result == Path("/tmp/existing")
        mock_path.assert_called_once_with(Path("/tmp/existing"))


class TestValidationHelpers:
    def test_run_preflight_validation_uses_benchmark_helper_when_available(self):
        expected = ValidationResult(is_valid=True)
        benchmark = SimpleNamespace(validate_preflight=Mock(return_value=expected))
        config = _benchmark_config("tpch")

        result = _run_preflight_validation(benchmark, config, Path("/tmp/out"))

        assert result is expected
        benchmark.validate_preflight.assert_called_once_with(output_dir=Path("/tmp/out"), benchmark_name="tpch")

    def test_run_preflight_validation_uses_core_engine_and_requires_output_dir(self):
        benchmark = SimpleNamespace(scale_factor=0.25)
        config = _benchmark_config("tpch", scale_factor=0.5)

        with pytest.raises(RuntimeError, match="Output directory is required"):
            _run_preflight_validation(benchmark, config, None)

        expected = ValidationResult(is_valid=True)
        with patch(
            "benchbox.core.validation.DataValidationEngine.validate_preflight_conditions",
            return_value=expected,
        ) as mock_validate:
            result = _run_preflight_validation(benchmark, config, Path("/tmp/out"))

        assert result is expected
        mock_validate.assert_called_once_with("tpch", 0.5, Path("/tmp/out"))

    def test_run_manifest_validation_handles_helper_missing_output_dir_and_core_engine(self, tmp_path: Path):
        expected = ValidationResult(is_valid=True)
        helper_benchmark = SimpleNamespace(validate_manifest=Mock(return_value=expected))
        config = _benchmark_config("tpch")

        assert _run_manifest_validation(helper_benchmark, config) is expected
        helper_benchmark.validate_manifest.assert_called_once_with(benchmark_name="tpch")

        result = _run_manifest_validation(SimpleNamespace(output_dir=None), config)
        assert result.is_valid is False
        assert result.errors == ["Benchmark output directory not configured; cannot validate manifest"]

        benchmark = SimpleNamespace(output_dir=tmp_path)
        with patch(
            "benchbox.core.validation.DataValidationEngine.validate_generated_data",
            return_value=expected,
        ) as mock_validate:
            assert _run_manifest_validation(benchmark, config) is expected
        mock_validate.assert_called_once_with(tmp_path / "_datagen_manifest.json")

    def test_run_postload_validation_handles_missing_adapter_methods_and_success(self):
        config = _benchmark_config("tpch")

        assert _run_postload_validation(None, config, {}) is None
        assert _run_postload_validation(SimpleNamespace(), config, {}) is None

        connection = object()
        adapter = SimpleNamespace(create_connection=Mock(return_value=connection), close_connection=Mock())
        expected = ValidationResult(is_valid=True)
        with patch(
            "benchbox.core.validation.DatabaseValidationEngine.validate_loaded_data",
            return_value=expected,
        ) as mock_validate:
            result = _run_postload_validation(adapter, config, {"database": "bench"})

        assert result is expected
        adapter.create_connection.assert_called_once_with(database="bench")
        adapter.close_connection.assert_called_once_with(connection)
        mock_validate.assert_called_once_with(connection, "tpch", 0.01)

    def test_run_postload_validation_failure_after_attempt_returns_invalid(self):
        config = _benchmark_config("tpch")
        adapter = SimpleNamespace(
            create_connection=Mock(side_effect=RuntimeError("connect failed")), close_connection=Mock()
        )

        result = _run_postload_validation(adapter, config, {})

        assert result is not None
        assert result.is_valid is False
        assert result.errors == ["Post-load validation failed: connect failed"]
        adapter.close_connection.assert_not_called()

    def test_finalize_validation_metadata_handles_empty_and_aggregated_records(self):
        result = make_benchmark_results(validation_status="UNKNOWN", validation_details=None)

        finalized = _finalize_validation_metadata(result, [])
        assert finalized.validation_status == "NOT_RUN"
        assert finalized.validation_details == {"stages": [], "error_count": 0, "warning_count": 0}

        aggregated = make_benchmark_results(
            validation_status="FAILED",
            validation_details={"stages": [{"stage": "existing"}], "error_count": 1, "warning_count": 2},
        )
        records = [
            ("preflight", ValidationResult(is_valid=True, warnings=["minor"], details={"phase": "preflight"})),
            ("post_load", ValidationResult(is_valid=False, errors=["boom"], details={"phase": "load"})),
        ]

        finalized = _finalize_validation_metadata(aggregated, records)

        assert finalized.validation_status == "FAILED"
        assert finalized.validation_details["error_count"] == 2
        assert finalized.validation_details["warning_count"] == 3
        assert finalized.validation_details["stages"][1]["status"] == "WARNINGS"
        assert finalized.validation_details["stages"][2]["status"] == "FAILED"


class TestDriverMetadataHelpers:
    def test_runner_driver_metadata_matches_shared_helper_output(self):
        adapter = SimpleNamespace(
            driver_package="duckdb",
            driver_version_requested="1.1.0",
            driver_version_resolved="1.1.0",
            driver_version_actual="1.1.1",
            driver_runtime_strategy="bundled",
            driver_runtime_path="/tmp/driver",
            driver_runtime_python_executable="/usr/bin/python",
            driver_auto_install_used=True,
        )
        database_config = DatabaseConfig(type="duckdb", name="bench", driver_version="1.1.2", driver_auto_install=False)
        runner_result = make_benchmark_results(execution_metadata={}, platform_info={})
        shared_result = make_benchmark_results(execution_metadata={}, platform_info={})

        _enrich_driver_runtime_metadata(runner_result, adapter=adapter, database_config=database_config)
        apply_driver_metadata(shared_result, database_config=database_config, platform_adapter=adapter)

        fields = [
            "driver_package",
            "driver_version_requested",
            "driver_version_resolved",
            "driver_version_actual",
            "driver_runtime_strategy",
            "driver_runtime_path",
            "driver_runtime_python_executable",
            "driver_auto_install",
        ]
        for field in fields:
            assert getattr(runner_result, field) == getattr(shared_result, field)
        assert runner_result.execution_metadata == shared_result.execution_metadata
        assert runner_result.platform_info == shared_result.platform_info

    def test_enrich_driver_runtime_metadata_populates_result_and_dicts(self):
        result = make_benchmark_results(
            execution_metadata={"driver_package": "preserve-me"},
            platform_info={"platform_name": "duckdb", "driver_runtime_path": "preserve-path"},
        )
        adapter = SimpleNamespace(
            driver_package="duckdb",
            driver_version_requested="1.1.0",
            driver_version_resolved=None,
            driver_version_actual="1.1.1",
            driver_runtime_strategy="bundled",
            driver_runtime_path="/tmp/driver",
            driver_runtime_python_executable="/usr/bin/python",
            driver_auto_install_used=False,
        )
        database_config = DatabaseConfig(type="duckdb", name="bench", driver_version="1.1.2", driver_auto_install=True)

        enriched = _enrich_driver_runtime_metadata(result, adapter=adapter, database_config=database_config)

        assert enriched.driver_package == "duckdb"
        assert enriched.driver_version_requested == "1.1.0"
        assert enriched.driver_version_resolved == "1.1.2"
        assert enriched.driver_version_actual == "1.1.1"
        assert enriched.driver_runtime_strategy == "bundled"
        assert enriched.driver_runtime_path == "/tmp/driver"
        assert enriched.driver_runtime_python_executable == "/usr/bin/python"
        assert enriched.driver_auto_install is True
        assert enriched.execution_metadata["driver_package"] == "preserve-me"
        assert enriched.execution_metadata["driver_version_resolved"] == "1.1.2"
        assert enriched.execution_metadata["driver_auto_install_used"] is True
        assert enriched.platform_info["driver_package"] == "duckdb"
        assert enriched.platform_info["driver_runtime_path"] == "preserve-path"
        assert enriched.platform_info["driver_version_resolved"] == "1.1.2"
        assert database_config.driver_version_resolved == "1.1.2"


class TestLoadOnlyModeHelper:
    def test_execute_load_only_mode_native_path_collects_stats_and_validation(self, tmp_path: Path):
        benchmark = SimpleNamespace(output_dir=tmp_path)
        expected_result = make_benchmark_results()
        benchmark.create_enhanced_benchmark_result = Mock(return_value=expected_result)
        benchmark.validate_loaded_data = Mock(return_value=ValidationResult(is_valid=True))
        adapter = SimpleNamespace(
            platform_name="duckdb",
            create_connection=Mock(return_value="connection"),
            close_connection=Mock(),
            create_schema=Mock(return_value=0.25),
            load_data=Mock(return_value=({"customer": 2}, 0.5, {"customer": {"total_ms": 300}})),
            _calculate_data_size=Mock(return_value=1.5),
        )

        result, postload = _execute_load_only_mode(
            benchmark=benchmark,
            benchmark_config=_benchmark_config("tpch"),
            adapter=adapter,
            platform_config={"database_path": "bench.duckdb"},
            validation_opts=ValidationOptions(enable_postload_validation=True),
            table_mode="native",
        )

        assert result is expected_result
        assert postload is not None and postload.is_valid is True
        adapter.create_schema.assert_called_once_with(benchmark, "connection")
        adapter.load_data.assert_called_once_with(benchmark, "connection", tmp_path)
        benchmark.validate_loaded_data.assert_called_once_with("connection", benchmark_name="tpch")
        adapter.close_connection.assert_called_once_with("connection")
        assert benchmark.create_enhanced_benchmark_result.call_args.kwargs["table_statistics"] == {"customer": 2}
        assert benchmark.create_enhanced_benchmark_result.call_args.kwargs["data_size_mb"] == 1.5
        assert benchmark.create_enhanced_benchmark_result.call_args.kwargs["total_rows_loaded"] == 2

    def test_execute_load_only_mode_external_path_uses_adapter_external_table_flow(self, tmp_path: Path):
        benchmark = SimpleNamespace(output_dir=tmp_path)
        expected_result = make_benchmark_results()
        benchmark.create_enhanced_benchmark_result = Mock(return_value=expected_result)
        adapter = SimpleNamespace(
            platform_name="duckdb",
            supports_external_tables=True,
            validate_external_table_requirements=Mock(),
            create_connection=Mock(return_value="connection"),
            close_connection=Mock(),
            create_external_tables=Mock(return_value=({"lineitem": 5}, 0.4, {})),
        )

        result, postload = _execute_load_only_mode(
            benchmark=benchmark,
            benchmark_config=_benchmark_config("tpch"),
            adapter=adapter,
            platform_config=None,
            validation_opts=ValidationOptions(enable_postload_validation=False),
            table_mode="external",
        )

        assert result is expected_result
        assert postload is None
        adapter.validate_external_table_requirements.assert_called_once_with()
        adapter.create_external_tables.assert_called_once_with(benchmark, "connection", tmp_path)
        phases = benchmark.create_enhanced_benchmark_result.call_args.kwargs["phases"]
        assert phases["schema_creation"]["status"] == "SKIPPED"
        assert phases["data_loading"]["duration_ms"] == 400


class TestSchemaAndFormatHelpers:
    def test_get_table_schemas_from_benchmark_supports_dict_and_list_and_wraps_errors(self):
        dict_benchmark = SimpleNamespace(get_schema=lambda: {"customer": {"name": "customer", "columns": []}})
        list_benchmark = SimpleNamespace(get_schema=lambda: [{"name": "Customer", "columns": []}])

        assert _get_table_schemas_from_benchmark(dict_benchmark) == {"customer": {"name": "customer", "columns": []}}
        assert _get_table_schemas_from_benchmark(list_benchmark) == {"customer": {"name": "Customer", "columns": []}}

        with pytest.raises(RuntimeError, match="does not provide get_schema"):
            _get_table_schemas_from_benchmark(SimpleNamespace())
        with pytest.raises(RuntimeError, match="Failed to get schema"):
            _get_table_schemas_from_benchmark(
                SimpleNamespace(get_schema=lambda: (_ for _ in ()).throw(ValueError("bad")))
            )
        with pytest.raises(RuntimeError, match="Unexpected schema format"):
            _get_table_schemas_from_benchmark(SimpleNamespace(get_schema=lambda: "bad"))

    def test_run_format_conversion_handles_prerequisites_and_success(self, tmp_path: Path):
        benchmark = SimpleNamespace(
            output_dir=tmp_path, get_schema=lambda: {"customer": {"name": "customer", "columns": []}}
        )
        config = _benchmark_config("tpch")
        assert _run_format_conversion(benchmark, config) is None

        invalid_config = _benchmark_config("tpch", options={"table_format": "invalid"})
        assert _run_format_conversion(benchmark, invalid_config) is None

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text("{}")
        success_config = _benchmark_config(
            "tpch",
            options={
                "table_format": "vortex",
                "table_mode": "external",
                "table_format_compression": "zstd",
                "table_format_partition_cols": ["region"],
            },
        )
        with patch(
            "benchbox.core.runner.runner.FormatConversionOrchestrator.convert_benchmark_tables",
            return_value={"customer": {"format": "vortex"}},
        ) as mock_convert:
            result = _run_format_conversion(benchmark, success_config)

        assert result == {"customer": {"format": "vortex"}}
        kwargs = mock_convert.call_args.kwargs
        assert kwargs["manifest_path"] == manifest_path
        assert kwargs["target_format"] == "vortex"
        assert kwargs["options"].metadata["require_duckdb_writer"] is True

    def test_run_format_conversion_wraps_conversion_failures(self, tmp_path: Path):
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text("{}")
        benchmark = SimpleNamespace(
            output_dir=tmp_path, get_schema=lambda: {"customer": {"name": "customer", "columns": []}}
        )
        config = _benchmark_config("tpch", options={"table_format": "parquet"})

        with patch(
            "benchbox.core.runner.runner.FormatConversionOrchestrator.convert_benchmark_tables",
            side_effect=RuntimeError("convert failed"),
        ):
            with pytest.raises(RuntimeError, match="Format conversion to parquet failed"):
                _run_format_conversion(benchmark, config)

    def test_flatten_manifest_v2_entries_prefers_requested_format_then_defaults(self):
        table_formats = SimpleNamespace(formats={"parquet": [{"path": "a"}], "tbl": [{"path": "b"}]})

        assert _flatten_manifest_v2_entries(table_formats, preferred_formats=["parquet"]) == [{"path": "a"}]
        assert _flatten_manifest_v2_entries(table_formats) == [{"path": "b"}]
        assert _flatten_manifest_v2_entries(SimpleNamespace(formats={})) == []

    def test_read_datagen_stats_from_manifest_aggregates_entries(self, tmp_path: Path):
        (tmp_path / "_datagen_manifest.json").write_text("{}")
        benchmark = SimpleNamespace(output_dir=tmp_path)
        manifest = SimpleNamespace(
            tables={
                "customer": [SimpleNamespace(row_count=3, size_bytes=30)],
                "orders": SimpleNamespace(formats={"parquet": [SimpleNamespace(row_count=5, size_bytes=50)]}),
            },
            format_preference=["parquet"],
        )

        with patch("benchbox.core.manifest.io.load_manifest", return_value=manifest):
            stats = _read_datagen_stats_from_manifest(benchmark)

        assert stats == {"tables_generated": 2, "total_rows": 8, "total_size_bytes": 80}


class TestManifestPathHelpers:
    def test_resolve_manifest_entry_paths_warns_on_unflagged_directory(self, tmp_path: Path, caplog):
        (tmp_path / "table-dir").mkdir()

        with caplog.at_level("WARNING"):
            paths = _resolve_manifest_entry_paths(
                tmp_path,
                "lineitem",
                [{"path": "table-dir"}, {"path": "customer.tbl", "is_directory": False}, {"path": None}],
            )

        assert paths == [tmp_path / "table-dir", tmp_path / "customer.tbl"]
        assert "is a directory but not marked as is_directory" in caplog.text

    def test_emit_manifest_reuse_message_formats_pluralization(self):
        with patch("benchbox.core.runner.runner.emit") as mock_emit:
            _emit_manifest_reuse_message(
                object(), {"created_at": "2026-03-25T00:00:00Z", "table_count": 1, "file_count": 2}
            )

        mock_emit.assert_called_once_with("🔄 Reusing benchmark data (created 2026-03-25T00:00:00Z; 1 table, 2 files)")


