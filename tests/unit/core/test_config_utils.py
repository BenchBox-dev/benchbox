# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from benchbox.core.config_utils import (
    _get_default_output_dir,
    build_benchmark_config,
    build_platform_adapter_config,
    deep_merge_dicts,
    extract_cli_config,
    get_builtin_defaults,
    load_config_file,
    load_platform_config,
    load_tuning_config,
    merge_all_configs,
    save_config_file,
    validate_config_sections,
    validate_numeric_config,
)
from benchbox.utils.path_utils import resolve_benchmark_runs_dir

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDeepMergeDicts:
    def test_simple_merge(self):

        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge_dicts(base, override)

        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):

        base = {"database": {"host": "localhost", "port": 5432}, "logging": {"level": "INFO"}}
        override = {"database": {"port": 3306, "username": "admin"}, "cache": {"enabled": True}}

        result = deep_merge_dicts(base, override)

        expected = {
            "database": {"host": "localhost", "port": 3306, "username": "admin"},
            "logging": {"level": "INFO"},
            "cache": {"enabled": True},
        }
        assert result == expected

    def test_original_unchanged(self):
        base = {"a": {"nested": 1}}
        override = {"a": {"nested": 2}}

        deep_merge_dicts(base, override)

        assert base == {"a": {"nested": 1}}
        assert override == {"a": {"nested": 2}}

    def test_override_non_dict_with_dict(self):

        base = {"config": "simple_value"}
        override = {"config": {"complex": "value"}}

        result = deep_merge_dicts(base, override)

        assert result == {"config": {"complex": "value"}}


class TestLoadConfigFile:
    def test_load_yaml_file(self, tmp_path):

        config_file = tmp_path / "config.yaml"
        config_data = {"key": "value", "nested": {"item": 123}}

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        result = load_config_file(config_file)
        assert result == config_data

    def test_load_json_file(self, tmp_path):

        config_file = tmp_path / "config.json"
        config_data = {"key": "value", "nested": {"item": 123}}

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        result = load_config_file(config_file)
        assert result == config_data

    def test_load_file_no_extension(self, tmp_path):
        config_file = tmp_path / "config"
        config_data = {"key": "value"}

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        result = load_config_file(config_file)
        assert result == config_data

    def test_load_nonexistent_file(self, tmp_path):

        config_file = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            load_config_file(config_file)

    def test_load_invalid_yaml(self, tmp_path):

        config_file = tmp_path / "invalid.yaml"

        with open(config_file, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: content: [")

        with pytest.raises(ValueError, match="Failed to parse configuration file"):
            load_config_file(config_file)

    def test_load_empty_file_returns_empty_dict(self, tmp_path):

        config_file = tmp_path / "empty.yaml"
        config_file.touch()

        result = load_config_file(config_file)
        assert result == {}


class TestSaveConfigFile:
    def test_save_yaml_file(self, tmp_path):

        config_file = tmp_path / "config.yaml"
        config_data = {"key": "value", "nested": {"item": 123}}

        save_config_file(config_data, config_file, "yaml")

        with open(config_file, encoding="utf-8") as f:
            loaded_data = yaml.safe_load(f)

        assert loaded_data == config_data

    def test_save_json_file(self, tmp_path):

        config_file = tmp_path / "config.json"
        config_data = {"key": "value", "nested": {"item": 123}}

        save_config_file(config_data, config_file, "json")

        with open(config_file, encoding="utf-8") as f:
            loaded_data = json.load(f)

        assert loaded_data == config_data

    def test_save_creates_directory(self, tmp_path):

        config_file = tmp_path / "nested" / "dir" / "config.yaml"
        config_data = {"key": "value"}

        save_config_file(config_data, config_file, "yaml")

        assert config_file.exists()
        assert config_file.parent.exists()

    def test_save_invalid_format(self, tmp_path):

        config_file = tmp_path / "config.txt"
        config_data = {"key": "value"}

        with pytest.raises(ValueError, match="Unsupported format"):
            save_config_file(config_data, config_file, "invalid")


class TestBuildBenchmarkConfig:
    def test_build_from_dict(self):

        config = {
            "scale_factor": 1.0,
            "very_verbose": True,
            "force": True,
            "benchmark": "tpch",
            "platform": "duckdb",
            "output": "/custom/path",
            "compress": True,
        }

        result = build_benchmark_config(config)

        expected = {
            "scale_factor": 1.0,
            "verbose": True,
            "force_regenerate": True,
            "output_dir": "/custom/path/tpch_sf1",
            "compress_data": True,
            "compression_type": "zstd",
        }
        assert result == expected

    def test_build_from_namespace(self):

        args = SimpleNamespace(
            scale=0.5, verbose=True, force=False, benchmark="tpcds", platform="duckdb", output=None, compress=False
        )

        result = build_benchmark_config(args, platform="duckdb")

        expected = {
            "scale_factor": 0.5,
            "verbose": True,
            "force_regenerate": False,
            "output_dir": str(resolve_benchmark_runs_dir() / "datagen" / "tpcds_sf05"),
        }
        assert result == expected

    def test_build_default_output_dir(self):

        config = {"scale_factor": 0.01, "benchmark": "tpch", "platform": "duckdb"}

        result = build_benchmark_config(config)

        assert result["output_dir"] == str(resolve_benchmark_runs_dir() / "datagen" / "tpch_sf001")

    def test_build_clickhouse_output_dir(self):

        config = {
            "scale_factor": 0.01,
            "benchmark": "tpch",
            "platform": "clickhouse",
            "data_path": "/custom/clickhouse/path",
        }

        result = build_benchmark_config(config)

        assert result["output_dir"] == "/custom/clickhouse/path"


class TestBuildPlatformAdapterConfig:
    def test_build_duckdb_config_from_dict(self):

        config = {"benchmark": "tpch", "scale_factor": 1.0, "memory_limit": "8GB", "force": True}

        result = build_platform_adapter_config("duckdb", config, scale_factor=1.0)

        assert result["memory_limit"] == "8GB"
        assert result["force_recreate"] is True
        assert "database_path" in result
        db_path = Path(result["database_path"])
        assert "benchmark_runs" in db_path.parts and "databases" in db_path.parts
        assert "tpch_sf1" in result["database_path"]

    def test_build_databricks_config_from_namespace(self):

        args = SimpleNamespace(
            server_hostname="test.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="token123",
            catalog="workspace",
            benchmark="tpcds",
        )

        result = build_platform_adapter_config("databricks", args, benchmark_name="tpcds")

        expected = {
            "server_hostname": "test.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "token123",
            "catalog": "workspace",
            "schema": "tpcds_schema",
        }
        assert result == expected

    def test_build_clickhouse_config(self):

        args = SimpleNamespace(
            deployment_mode="server",
            mode=None,
            data_path="/tmp/ch_data",
            host="clickhouse.example.com",
            port=9000,
            user="admin",
            password="secret",
            secure=True,
        )

        result = build_platform_adapter_config("clickhouse", args)

        expected = {
            "deployment_mode": "server",
            "mode": "server",
            "data_path": "/tmp/ch_data",
            "host": "clickhouse.example.com",
            "port": 9000,
            "user": "admin",
            "password": "secret",
            "secure": True,
        }
        assert result == expected

    def test_build_clickhouse_config_normalizes_legacy_mode_alias(self):
        args = SimpleNamespace(
            deployment_mode=None,
            mode="embedded",
            data_path="/tmp/ch_data",
            host="clickhouse.example.com",
            port=9000,
            user="admin",
            password="secret",
            secure=True,
        )

        result = build_platform_adapter_config("clickhouse", args)

        assert result["deployment_mode"] == "local"
        assert result["mode"] == "local"

    def test_build_clickhouse_config_rejects_cloud_mode(self):
        args = SimpleNamespace(
            deployment_mode="cloud",
            mode=None,
            embedded=None,
            data_path=None,
            host=None,
            port=None,
            user=None,
            password=None,
            secure=None,
        )

        with pytest.raises(ValueError, match="clickhouse-cloud"):
            build_platform_adapter_config("clickhouse", args)

    def test_build_unknown_platform_config(self):

        args = SimpleNamespace()

        result = build_platform_adapter_config("unknown", args)

        assert result == {}


class TestValidateConfigSections:
    def test_validate_all_sections_present(self):

        config = {"database": {}, "benchmarks": {}, "output": {}}
        required = ["database", "benchmarks", "output"]

        result = validate_config_sections(config, required)

        assert result is True

    def test_validate_missing_sections(self):

        config = {"database": {}, "output": {}}
        required = ["database", "benchmarks", "output"]

        result = validate_config_sections(config, required)

        assert result is False

    def test_validate_empty_config(self):

        config = {}
        required = ["database"]

        result = validate_config_sections(config, required)

        assert result is False


class TestValidateNumericConfig:
    def test_validate_valid_values(self):

        config = {"timeout": 30, "scale_factor": 1.5, "max_workers": 4}
        validations = {"timeout": (0, 300, True), "scale_factor": (0.01, 100.0, True), "max_workers": (1, 16, True)}

        errors = validate_numeric_config(config, validations)

        assert errors == []

    def test_validate_out_of_range_values(self):

        config = {"timeout": -5, "scale_factor": 200.0, "max_workers": 0}
        validations = {"timeout": (0, 300, True), "scale_factor": (0.01, 100.0, True), "max_workers": (1, 16, True)}

        errors = validate_numeric_config(config, validations)

        assert len(errors) == 3
        assert "timeout" in errors[0]
        assert "scale_factor" in errors[1]
        assert "max_workers" in errors[2]

    def test_validate_missing_required_values(self):
        config = {}
        validations = {"timeout": (0, 300, True), "optional_setting": (0, 100, False)}

        errors = validate_numeric_config(config, validations)

        assert len(errors) == 1
        assert "timeout" in errors[0]
        assert "missing" in errors[0]

    def test_validate_non_numeric_values(self):

        config = {"timeout": "invalid", "scale_factor": None}
        validations = {"timeout": (0, 300, True), "scale_factor": (0.01, 100.0, True)}

        errors = validate_numeric_config(config, validations)

        assert len(errors) == 2
        assert "numeric" in errors[0]
        assert "numeric" in errors[1]


class TestDefaultOutputDirHelper:
    def test_missing_platform_or_benchmark_returns_none(self):
        assert _get_default_output_dir(None, "tpch", 0.01) is None
        assert _get_default_output_dir("duckdb", None, 0.01) is None

    def test_duckdb_and_sqlite_use_benchmark_runs_path(self):
        duckdb_dir = _get_default_output_dir("duckdb", "tpch", 0.01)
        sqlite_dir = _get_default_output_dir("sqlite", "tpch", 0.01)

        expected_suffix = str(Path("benchmark_runs") / "datagen" / "tpch_sf001")
        assert duckdb_dir is not None and duckdb_dir.endswith(expected_suffix)
        assert sqlite_dir is not None and sqlite_dir.endswith(expected_suffix)

    def test_clickhouse_prefers_custom_data_path(self):
        assert _get_default_output_dir("clickhouse", "tpch", 0.01, "/tmp/custom") == "/tmp/custom"
        assert _get_default_output_dir("clickhouse", "tpch", 0.01) == "/tmp/benchbox_ch_local"

    def test_unknown_platform_returns_none(self):
        assert _get_default_output_dir("postgresql", "tpch", 0.01) is None


class TestLoadPlatformConfig:
    def test_load_platform_config_uses_built_in_defaults_when_default_file_missing(self, tmp_path):
        with patch("benchbox.core.config_utils.emit") as mock_emit:
            original_cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp_path)
                result = load_platform_config("missing-platform", verbose=True)
            finally:
                os.chdir(original_cwd)

        assert result == {}
        mock_emit.assert_called_once_with("Platform config using built-in defaults")

    def test_load_platform_config_merges_connection_extra_params_and_settings(self, tmp_path):
        config_path = tmp_path / "duckdb.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "connection": {
                        "database_path": "bench.duckdb",
                        "extra_params": {"memory_limit": "8GB"},
                    },
                    "settings": {"threads": 4},
                }
            )
        )

        with patch("benchbox.core.config_utils.emit") as mock_emit:
            result = load_platform_config("duckdb", str(config_path), verbose=True)

        assert result == {
            "database_path": "bench.duckdb",
            "extra_params": {"memory_limit": "8GB"},
            "memory_limit": "8GB",
            "threads": 4,
        }
        mock_emit.assert_called_once_with(f"Platform config loaded from file: {config_path}")

    def test_load_platform_config_warns_and_returns_empty_on_parse_failure(self, tmp_path):
        config_path = tmp_path / "broken.yaml"
        config_path.write_text("connection: [broken")

        with patch("benchbox.core.config_utils.emit") as mock_emit:
            result = load_platform_config("duckdb", str(config_path), verbose=True)

        assert result == {}
        assert mock_emit.call_count == 2
        assert "Warning: Failed to load platform config" in mock_emit.call_args_list[0].args[0]
        assert mock_emit.call_args_list[1].args[0] == "Using built-in defaults for duckdb"


class TestLoadTuningConfig:
    def test_load_tuning_config_warns_when_auto_selected_file_is_missing(self, tmp_path):
        with patch("benchbox.core.config_utils.emit") as mock_emit:
            original_cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp_path)
                result = load_tuning_config("duckdb", "tpch", "tuned", verbose=True)
            finally:
                os.chdir(original_cwd)

        assert result is None
        assert mock_emit.call_args_list == [
            ((f"Warning: Tuning config not found: {Path('examples/tunings/duckdb/tpch_tuned.yaml')}",),),
            (("Proceeding without tuning configuration",),),
        ]

    def test_load_tuning_config_loads_custom_file_and_reports_metadata(self, tmp_path):
        tuning_path = tmp_path / "custom.yaml"
        tuning_path.write_text(yaml.safe_dump({"threads": 8, "_metadata": {"configuration_type": "warehouse-profile"}}))

        with patch("benchbox.core.config_utils.emit") as mock_emit:
            result = load_tuning_config("duckdb", "tpch", str(tuning_path), verbose=True)

        assert result == {"threads": 8, "_metadata": {"configuration_type": "warehouse-profile"}}
        mock_emit.assert_called_once_with(
            f"Tuning config loaded from custom file: {tuning_path} (type: warehouse-profile)"
        )

    def test_load_tuning_config_raises_for_missing_custom_file(self):
        with pytest.raises(FileNotFoundError, match="Tuning config file not found"):
            load_tuning_config("duckdb", "tpch", "/tmp/missing.yaml")

    def test_load_tuning_config_warns_and_returns_none_on_parse_failure(self, tmp_path):
        tuning_path = tmp_path / "broken.yaml"
        tuning_path.write_text("[broken")

        with patch("benchbox.core.config_utils.emit") as mock_emit:
            result = load_tuning_config("duckdb", "tpch", str(tuning_path), verbose=True)

        assert result is None
        assert "Warning: Failed to load tuning config" in mock_emit.call_args.args[0]


class TestMergeAllConfigs:
    def test_merge_all_configs_applies_precedence_and_quiet_overrides(self):
        args = SimpleNamespace(scale=2, verbose=2, quiet=True, duckdb_database_path="/tmp/cli.duckdb")

        with (
            patch(
                "benchbox.core.config_utils.get_builtin_defaults", return_value={"memory_limit": "4GB", "verbose": 0}
            ),
            patch(
                "benchbox.core.config_utils.load_platform_config", return_value={"memory_limit": "8GB", "threads": 4}
            ),
            patch(
                "benchbox.core.config_utils.load_tuning_config",
                return_value={"memory_limit": "16GB", "tuning_only": True},
            ),
            patch(
                "benchbox.core.config_utils.extract_cli_config",
                return_value={"scale": 2, "verbose": 2, "quiet": True, "database_path": "/tmp/cli.duckdb"},
            ),
        ):
            result = merge_all_configs("duckdb", "tpch", args, verbose=False)

        assert result["memory_limit"] == "16GB"
        assert result["threads"] == 4
        assert result["tuning_only"] is True
        assert result["tuning_config"] == {"memory_limit": "16GB", "tuning_only": True}
        assert result["database_path"] == "/tmp/cli.duckdb"
        assert result["scale_factor"] == 2
        assert result["platform"] == "duckdb"
        assert result["benchmark"] == "tpch"
        assert result["verbose_enabled"] is False
        assert result["very_verbose"] is False


class TestBuiltinDefaults:
    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("duckdb", {"memory_limit": "4GB", "database_path": None}),
            ("databricks", {"catalog": "workspace", "schema": "benchbox"}),
            ("clickhouse", {"deployment_mode": "local", "data_path": "/tmp/benchbox_ch_local"}),
            ("sqlite", {"timeout": 30.0, "database_path": None}),
            ("bigquery", {"location": "US", "dataset_id": "benchbox"}),
            ("redshift", {"port": 5439, "database": "dev"}),
            ("snowflake", {"warehouse": "COMPUTE_WH", "database": "BENCHBOX"}),
        ],
    )
    def test_platform_specific_defaults(self, platform, expected):
        defaults = get_builtin_defaults(platform, "tpch")
        for key, value in expected.items():
            assert defaults[key] == value
        assert defaults["scale_factor"] == 0.01
        assert defaults["phases"] == "power"


class TestExtractCliConfig:
    def test_extract_cli_config_filters_none_and_maps_aliases(self):
        args = SimpleNamespace(
            duckdb_database_path="/tmp/duckdb.db",
            sqlite_database_path=None,
            data_path="/tmp/data",
            redshift_host="redshift.example.com",
            redshift_port=5439,
            redshift_database="dev",
            redshift_username="joe",
            redshift_password="secret",
            snowflake_database="BENCHBOX",
            snowflake_schema="PUBLIC",
            snowflake_username="user",
            snowflake_password="pw",
            server_hostname="dbc.example.com",
            http_path="/sql/warehouse",
            access_token="token",
            project_id="project",
            dataset_id="dataset",
            credentials_path="/tmp/creds.json",
            storage_bucket="bucket",
            storage_prefix="prefix",
            extra=None,
        )

        result = extract_cli_config(args)

        assert result["database_path"] == "/tmp/duckdb.db"
        assert result["data_path"] == "/tmp/data"
        assert result["host"] == "redshift.example.com"
        assert result["port"] == 5439
        assert result["database"] == "BENCHBOX"
        assert result["username"] == "user"
        assert result["password"] == "pw"
        assert result["schema"] == "PUBLIC"
        assert result["server_hostname"] == "dbc.example.com"
        assert result["http_path"] == "/sql/warehouse"
        assert result["access_token"] == "token"
        assert result["project_id"] == "project"
        assert result["dataset_id"] == "dataset"
        assert result["credentials_path"] == "/tmp/creds.json"
        assert result["storage_bucket"] == "bucket"
        assert result["storage_prefix"] == "prefix"
        assert "extra" not in result


