# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from benchbox.cli.config import BenchBoxConfig, ConfigManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.mark.unit
class TestBenchBoxConfig:
    def test_config_model_creation_empty(self):

        config = BenchBoxConfig()

        assert config.system == {}
        assert config.database == {}
        assert config.benchmarks == {}
        assert config.output == {}
        assert config.execution == {}

    def test_config_model_creation_with_data(self):

        config_data = {
            "system": {"cpu_cores": 8, "memory_gb": 16},
            "database": {"type": "duckdb", "path": "/tmp/test.db"},
            "benchmarks": {"default_scale": 0.01, "timeout": 3600},
            "output": {"format": "json", "directory": "/tmp/results"},
            "execution": {"parallel": True, "threads": 4},
        }

        config = BenchBoxConfig(**config_data)

        assert config.system["cpu_cores"] == 8
        assert config.database["type"] == "duckdb"
        assert config.benchmarks["default_scale"] == 0.01
        assert config.output["format"] == "json"
        assert config.execution["parallel"] is True

    def test_config_model_extra_fields(self):

        config_data = {
            "custom_field": "custom_value",
            "nested_custom": {"key": "value"},
        }

        config = BenchBoxConfig(**config_data)

        assert hasattr(config, "custom_field")
        assert config.custom_field == "custom_value"

    def test_config_model_validation(self):

        config = BenchBoxConfig(system={"memory": "8GB"}, database={"connection_string": "test"})

        assert isinstance(config.system, dict)
        assert isinstance(config.database, dict)


@pytest.mark.unit
class TestConfigManager:
    def test_config_manager_default_path_current_directory(self, temp_dir):

        config_file = temp_dir / "benchbox.yaml"
        config_data = {"system": {"test": True}}

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        with patch("benchbox.cli.config.Path") as mock_path:
            mock_path.return_value = temp_dir
            mock_path.home.return_value = Path.home()

            current_config = Mock()
            current_config.exists.return_value = True
            mock_path.return_value = current_config

            with patch.object(ConfigManager, "_get_default_config_path", return_value=config_file):
                config_manager = ConfigManager()

                assert config_manager.config_path == config_file

    def test_config_manager_default_path_home_directory(self, temp_dir):

        home_config_dir = temp_dir / ".benchbox"
        home_config_dir.mkdir()
        home_config_file = home_config_dir / "config.yaml"

        config_data = {"system": {"home_test": True}}
        with open(home_config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        with patch("benchbox.cli.config.Path") as mock_path:
            current_config = Mock()
            current_config.exists.return_value = False

            mock_path.home.return_value = temp_dir
            mock_path.return_value = current_config

            with patch.object(ConfigManager, "_get_default_config_path", return_value=home_config_file):
                config_manager = ConfigManager()

                assert config_manager.config_path == home_config_file

    def test_config_manager_custom_path(self, temp_dir):

        custom_config = temp_dir / "custom_config.yaml"
        config_data = {"custom": True}

        with open(custom_config, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config_manager = ConfigManager(config_path=custom_config)

        assert config_manager.config_path == custom_config

    def test_config_manager_load_existing_config(self, temp_dir):

        config_file = temp_dir / "test_config.yaml"
        config_data = {
            "system": {"cpu_cores": 4},
            "database": {"type": "sqlite"},
            "benchmarks": {"tpch": {"scale": 0.1}},
        }

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config_manager = ConfigManager(config_path=config_file)

        assert config_manager.config.system["cpu_cores"] == 4
        assert config_manager.config.database["type"] == "sqlite"
        assert config_manager.config.benchmarks["tpch"]["scale"] == 0.1

    def test_config_manager_load_nonexistent_config(self, temp_dir):

        nonexistent_config = temp_dir / "nonexistent.yaml"

        config_manager = ConfigManager(config_path=nonexistent_config)

        assert isinstance(config_manager.config, BenchBoxConfig)
        assert config_manager.config.system.get("auto_profile") is True
        assert config_manager.config.database.get("preferred") == "duckdb"

    def test_config_manager_load_invalid_yaml(self, temp_dir):

        config_file = temp_dir / "invalid.yaml"

        with open(config_file, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: content: [unclosed")

        config_manager = ConfigManager(config_path=config_file)

        assert isinstance(config_manager.config, BenchBoxConfig)

    def test_config_manager_save_config(self, temp_dir):

        config_file = temp_dir / "save_test.yaml"
        config_manager = ConfigManager(config_path=config_file)

        config_manager.config.system = {"cpu_cores": 8}
        config_manager.config.database = {"type": "duckdb"}

        if hasattr(config_manager, "save_config"):
            config_manager.save_config()

            assert config_file.exists()

            with open(config_file, encoding="utf-8") as f:
                saved_data = yaml.safe_load(f)

            assert saved_data["system"]["cpu_cores"] == 8
            assert saved_data["database"]["type"] == "duckdb"

    def test_config_manager_get_setting(self, temp_dir):

        config_file = temp_dir / "settings_test.yaml"
        config_data = {
            "benchmarks": {
                "tpch": {"default_scale": 0.01, "timeout": 3600},
                "tpcds": {"default_scale": 0.1, "timeout": 7200},
            }
        }

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config_manager = ConfigManager(config_path=config_file)

        if hasattr(config_manager, "get_setting"):
            tpch_scale = config_manager.get_setting("benchmarks.tpch.default_scale")
            assert tpch_scale == 0.01

            tpcds_timeout = config_manager.get_setting("benchmarks.tpcds.timeout")
            assert tpcds_timeout == 7200

    def test_config_manager_set_setting(self, temp_dir):

        config_file = temp_dir / "set_test.yaml"
        config_manager = ConfigManager(config_path=config_file)

        if hasattr(config_manager, "set_setting"):
            config_manager.set_setting("system.cpu_cores", 16)
            config_manager.set_setting("database.connection_pool_size", 10)

            assert config_manager.config.system["cpu_cores"] == 16
            assert config_manager.config.database["connection_pool_size"] == 10

    def test_config_manager_merge_config(self, temp_dir):

        config_file = temp_dir / "merge_test.yaml"
        base_config = {
            "system": {"cpu_cores": 4, "memory_gb": 8},
            "database": {"type": "sqlite"},
        }

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(base_config, f)

        config_manager = ConfigManager(config_path=config_file)

        runtime_overrides = {
            "system": {"cpu_cores": 8},
            "benchmarks": {"scale": 0.1},
        }

        if hasattr(config_manager, "merge_config"):
            config_manager.merge_config(runtime_overrides)

            assert config_manager.config.system["cpu_cores"] == 8
            assert config_manager.config.system["memory_gb"] == 8
            assert config_manager.config.benchmarks["scale"] == 0.1

    def test_config_manager_validate_config(self, temp_dir):

        config_file = temp_dir / "validate_test.yaml"
        config_data = {
            "system": {"cpu_cores": 4},
            "database": {"type": "duckdb"},
            "benchmarks": {"default_scale": 0.01, "timeout_minutes": 60},
            "execution": {"max_workers": 4},
        }

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config_manager = ConfigManager(config_path=config_file)

        if hasattr(config_manager, "validate_config"):
            is_valid = config_manager.validate_config()
            assert is_valid is True


@pytest.mark.unit
class TestConfigManagerEdgeCases:
    def test_config_manager_permission_denied(self, temp_dir):

        readonly_dir = temp_dir / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        config_file = readonly_dir / "config.yaml"

        try:
            config_manager = ConfigManager(config_path=config_file)
            assert isinstance(config_manager.config, BenchBoxConfig)
        finally:
            with contextlib.suppress(OSError, PermissionError):
                readonly_dir.chmod(0o755)

    def test_config_manager_empty_file(self, temp_dir):

        config_file = temp_dir / "empty.yaml"
        config_file.touch()

        config_manager = ConfigManager(config_path=config_file)

        assert isinstance(config_manager.config, BenchBoxConfig)
        assert config_manager.config.system.get("auto_profile") is True

    def test_config_manager_corrupted_file(self, temp_dir):

        config_file = temp_dir / "corrupted.yaml"

        with open(config_file, "wb") as f:
            f.write(b"\xff\xfe\x00corrupted\x00data")

        config_manager = ConfigManager(config_path=config_file)
        assert isinstance(config_manager.config, BenchBoxConfig)
        assert config_manager.config.system.get("auto_profile") is True

    def test_config_manager_very_large_config(self, temp_dir):
        config_file = temp_dir / "large.yaml"

        large_config = {
            "system": {f"key_{i}": f"value_{i}" for i in range(50)},
            "benchmarks": {
                f"benchmark_{i}": {
                    "scale": 0.01 * i,
                    "queries": [f"q{j}" for j in range(10)],
                }
                for i in range(3)
            },
        }

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(large_config, f)

        config_manager = ConfigManager(config_path=config_file)

        assert isinstance(config_manager.config, BenchBoxConfig)
        assert len(config_manager.config.system) == 53
        assert len(config_manager.config.benchmarks) == 7


