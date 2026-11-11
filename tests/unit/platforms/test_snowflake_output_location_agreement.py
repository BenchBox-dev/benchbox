# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import inspect
from pathlib import Path

import pytest

from benchbox.core.platform_registry import SNOWFLAKE_DEFAULT_OUTPUT_LOCATION, PlatformRegistry
from benchbox.utils.cloud_storage import create_path_handler, is_cloud_path

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SNOWFLAKE_EXAMPLES = PlatformRegistry.get_cloud_path_examples("snowflake")


class TestPromptDefaultIsAcceptedByTheRunPath:
    def test_prompt_default_classifies_as_cloud(self):
        assert is_cloud_path(SNOWFLAKE_DEFAULT_OUTPUT_LOCATION)

    def test_prompt_default_never_becomes_a_relative_local_path(self):
        handler = create_path_handler(SNOWFLAKE_DEFAULT_OUTPUT_LOCATION)
        assert not (isinstance(handler, Path) and not handler.is_absolute()), handler

    def test_prompt_default_is_offered_as_a_registry_example(self):
        assert SNOWFLAKE_DEFAULT_OUTPUT_LOCATION in SNOWFLAKE_EXAMPLES

    def test_prompt_default_is_listed_first(self):
        assert SNOWFLAKE_EXAMPLES[0] == SNOWFLAKE_DEFAULT_OUTPUT_LOCATION


class TestRegistryExamplesAreAllUsable:
    @pytest.mark.parametrize("example", SNOWFLAKE_EXAMPLES)
    def test_every_snowflake_example_classifies_as_cloud(self, example):
        assert is_cloud_path(example), example

    @pytest.mark.parametrize("example", SNOWFLAKE_EXAMPLES)
    def test_no_example_resolves_to_a_relative_local_path(self, example):
        handler = create_path_handler(example)
        assert not (isinstance(handler, Path) and not handler.is_absolute()), (example, handler)

    def test_examples_still_cover_external_stages(self):
        schemes = {e.split("://")[0] for e in SNOWFLAKE_EXAMPLES if "://" in e}
        assert {"s3", "azure", "gcs"} <= schemes, schemes


class TestPromptUsesTheSharedSources:
    def _prompt_source(self) -> str:
        from benchbox.platforms.credentials import snowflake as snowflake_credentials

        return inspect.getsource(snowflake_credentials._prompt_default_output_location)

    def test_prompt_defaults_to_the_shared_constant(self):
        source = self._prompt_source()
        assert "SNOWFLAKE_DEFAULT_OUTPUT_LOCATION" in source
        assert 'default="@~/benchbox"' not in source

    def test_prompt_renders_examples_from_the_registry(self):
        source = self._prompt_source()
        assert 'get_cloud_path_examples("snowflake")' in source

    def test_prompt_validates_with_the_run_path_classifier(self):
        source = self._prompt_source()
        assert "is_cloud_path(" in source
        assert 'startswith("@~")' not in source


class TestOutputDirectoryConfigKeyIsGone:
    def test_default_config_has_no_output_directory_key(self):
        from benchbox.cli.config import ConfigManager

        default_config = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
        assert "directory" not in default_config.output

    def test_output_dir_env_var_is_not_mapped_into_config(self):
        from benchbox.cli import config as config_module

        source = inspect.getsource(config_module)
        assert '"BENCHBOX_OUTPUT_DIR": ("output", "directory", str)' not in source

    def test_legacy_config_containing_the_key_still_loads(self, tmp_path):
        import yaml

        from benchbox.cli.config import ConfigManager

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "output": {"formats": ["json"], "directory": "./benchmark_runs/results"},
                    "database": {"preferred": "duckdb"},
                }
            )
        )

        manager = ConfigManager(config_path=config_path)

        assert manager.config.output["directory"] == "./benchmark_runs/results"
        assert manager.config.database["preferred"] == "duckdb"


