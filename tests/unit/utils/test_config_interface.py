# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.utils.config_interface import (
    ConfigInterface,
    SimpleConfigProvider,
    get_config_provider,
    set_config_provider,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestConfigInterface:
    def test_config_interface_is_abstract(self):
        with pytest.raises(TypeError):
            ConfigInterface()

    def test_config_interface_methods_not_implemented(self):

        class TestConfig(ConfigInterface):
            pass

        with pytest.raises(TypeError):
            TestConfig()


class TestSimpleConfigProvider:
    def test_simple_config_provider_creation_empty(self):
        config = SimpleConfigProvider()

        assert isinstance(config._config, dict)
        assert config.get("nonexistent_key") is None

    def test_simple_config_provider_creation_with_data(self):
        initial_data = {"key1": "value1", "key2": 42, "key3": {"nested": "value"}}

        config = SimpleConfigProvider(initial_data)

        assert config.get("key1") == "value1"
        assert config.get("key2") == 42
        assert config.get("key3") == {"nested": "value"}

    def test_get_existing_key(self):
        config = SimpleConfigProvider({"test_key": "test_value", "number": 123})

        assert config.get("test_key") == "test_value"
        assert config.get("number") == 123

    def test_get_nonexistent_key_no_default(self):
        config = SimpleConfigProvider({"existing": "value"})

        result = config.get("nonexistent")
        assert result is None

    def test_get_nonexistent_key_with_default(self):
        config = SimpleConfigProvider({"existing": "value"})

        result = config.get("nonexistent", "default_value")
        assert result == "default_value"

    def test_get_with_various_types(self):
        config = SimpleConfigProvider(
            {
                "string": "hello",
                "integer": 42,
                "float": 3.14,
                "boolean": True,
                "list": [1, 2, 3],
                "dict": {"nested": "value"},
                "none": None,
            }
        )

        assert config.get("string") == "hello"
        assert config.get("integer") == 42
        assert config.get("float") == 3.14
        assert config.get("boolean") is True
        assert config.get("list") == [1, 2, 3]
        assert config.get("dict") == {"nested": "value"}
        assert config.get("none") is None

    def test_set_new_key(self):
        config = SimpleConfigProvider()

        config.set("new_key", "new_value")

        assert config.get("new_key") == "new_value"
        assert "new_key" in config._config

    def test_set_existing_key(self):
        config = SimpleConfigProvider({"existing": "old_value"})

        config.set("existing", "new_value")

        assert config.get("existing") == "new_value"
        assert config._config["existing"] == "new_value"

    def test_set_various_types(self):
        config = SimpleConfigProvider()

        config.set("string", "hello")
        config.set("integer", 42)
        config.set("float", 3.14)
        config.set("boolean", False)
        config.set("list", [1, 2, 3])
        config.set("dict", {"key": "value"})
        config.set("none", None)

        assert config.get("string") == "hello"
        assert config.get("integer") == 42
        assert config.get("float") == 3.14
        assert config.get("boolean") is False
        assert config.get("list") == [1, 2, 3]
        assert config.get("dict") == {"key": "value"}
        assert config.get("none") is None

    def test_has_existing_key(self):
        config = SimpleConfigProvider({"existing": "value", "none_value": None})

        assert config.get("existing") is not None or "existing" in config._config
        assert "none_value" in config._config

    def test_has_nonexistent_key(self):
        config = SimpleConfigProvider({"existing": "value"})

        assert "nonexistent" not in config._config

    def test_get_all_empty(self):
        config = SimpleConfigProvider()

        assert isinstance(config._config, dict)
        assert "execution.timeout_minutes" in config._config

    def test_get_all_with_data(self):
        data = {"key1": "value1", "key2": 42, "nested": {"inner": "value"}}
        config = SimpleConfigProvider(data)

        assert config.get("key1") == "value1"
        assert config.get("key2") == 42
        assert config.get("nested") == {"inner": "value"}

    def test_data_isolation(self):
        initial_data = {"key": "value"}
        config = SimpleConfigProvider(initial_data)

        initial_data["key"] = "changed"
        assert config.get("key") == "value"

        config._config["new_key"] = "new_value"
        assert config.get("new_key") == "new_value"

    def test_nested_data_handling(self):
        config = SimpleConfigProvider(
            {
                "level1": {"level2": {"level3": "deep_value"}},
                "list_of_dicts": [{"item": 1}, {"item": 2}],
            }
        )

        assert config.get("level1") == {"level2": {"level3": "deep_value"}}
        assert config.get("list_of_dicts") == [{"item": 1}, {"item": 2}]

    def test_key_types(self):
        config = SimpleConfigProvider()

        config.set("string_key", "value")
        assert config.get("string_key") == "value"

        config.set(123, "numeric_key_value")
        config.set(("tuple", "key"), "tuple_key_value")

        assert config.get(123) == "numeric_key_value"
        assert config.get(("tuple", "key")) == "tuple_key_value"


class TestCLIConfigProvider:
    def teardown_method(self):
        set_config_provider(None)

    @patch("benchbox.cli.config.ConfigManager")
    def test_cli_provider_is_a_config_interface(self, mock_config_manager_class):
        from benchbox.cli.config import CLIConfigProvider

        mock_config_manager_class.return_value = MagicMock()
        provider = CLIConfigProvider()

        assert isinstance(provider, ConfigInterface)
        mock_config_manager_class.assert_called_once()

    @patch("benchbox.cli.config.ConfigManager")
    def test_cli_provider_get_delegates(self, mock_config_manager_class):
        from benchbox.cli.config import CLIConfigProvider

        manager = MagicMock()
        manager.get.return_value = "test_value"
        mock_config_manager_class.return_value = manager

        assert CLIConfigProvider().get("test_key") == "test_value"
        manager.get.assert_called_once_with("test_key", None)

    @patch("benchbox.cli.config.ConfigManager")
    def test_cli_provider_get_passes_the_default(self, mock_config_manager_class):
        from benchbox.cli.config import CLIConfigProvider

        manager = MagicMock()
        manager.get.return_value = "default_value"
        mock_config_manager_class.return_value = manager

        assert CLIConfigProvider().get("missing_key", "default_value") == "default_value"
        manager.get.assert_called_once_with("missing_key", "default_value")

    @patch("benchbox.cli.config.ConfigManager")
    def test_cli_provider_set_delegates(self, mock_config_manager_class):
        from benchbox.cli.config import CLIConfigProvider

        manager = MagicMock()
        mock_config_manager_class.return_value = manager

        CLIConfigProvider().set("key", "value")
        manager.set.assert_called_once_with("key", "value")

    @patch("benchbox.cli.config.ConfigManager")
    def test_installing_the_cli_provider_makes_it_the_process_provider(self, mock_config_manager_class):
        from benchbox.cli.config import CLIConfigProvider, install_cli_config_provider

        mock_config_manager_class.return_value = MagicMock()
        installed = install_cli_config_provider()

        assert isinstance(installed, CLIConfigProvider)
        assert get_config_provider() is installed


class TestConfigProviderRegistration:
    def teardown_method(self):
        set_config_provider(None)

    def test_default_provider_is_the_simple_one(self):
        assert isinstance(get_config_provider(), SimpleConfigProvider)

    def test_registered_provider_wins(self):
        provider = SimpleConfigProvider({"execution.timeout_minutes": 7})
        set_config_provider(provider)

        assert get_config_provider() is provider

    def test_clearing_restores_the_default(self):
        set_config_provider(SimpleConfigProvider())
        set_config_provider(None)

        assert isinstance(get_config_provider(), SimpleConfigProvider)
        assert get_config_provider().get("execution.timeout_minutes") == 120

    def test_utils_does_not_import_the_cli_to_find_a_provider(self):
        import ast
        from pathlib import Path as _Path

        source = _Path("benchbox/utils/config_interface.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module} | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }

        assert not any(module.startswith("benchbox.cli") for module in imported)


