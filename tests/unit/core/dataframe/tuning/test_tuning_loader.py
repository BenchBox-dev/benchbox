# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest
import yaml

from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    DataFrameTuningLoader,
    DataFrameTuningLoadError,
    ExecutionConfiguration,
    ParallelismConfiguration,
    load_dataframe_tuning,
    save_dataframe_tuning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFrameTuningLoader:
    def test_load_config_yaml(self, tmp_path):

        config_path = tmp_path / "config.yaml"
        config_content = {
            "parallelism": {"thread_count": 8},
            "execution": {"streaming_mode": True},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_content, f)

        loader = DataFrameTuningLoader()
        config = loader.load_config(config_path)

        assert config.parallelism.thread_count == 8
        assert config.execution.streaming_mode is True

    def test_load_config_json(self, tmp_path):

        import json

        config_path = tmp_path / "config.json"
        config_content = {
            "parallelism": {"worker_count": 4},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_content, f)

        loader = DataFrameTuningLoader()
        config = loader.load_config(config_path)

        assert config.parallelism.worker_count == 4

    def test_load_config_file_not_found(self):

        loader = DataFrameTuningLoader()
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.load_config("/nonexistent/path/config.yaml")

    def test_load_config_invalid_yaml(self, tmp_path):

        config_path = tmp_path / "invalid.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: content: [")

        loader = DataFrameTuningLoader()
        with pytest.raises(DataFrameTuningLoadError, match="parse"):
            loader.load_config(config_path)

    def test_save_config_yaml(self, tmp_path):

        config_path = tmp_path / "output.yaml"
        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=8),
        )

        loader = DataFrameTuningLoader()
        loader.save_config(config, config_path)

        assert config_path.exists()
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["parallelism"]["thread_count"] == 8

    def test_save_config_json(self, tmp_path):

        import json

        config_path = tmp_path / "output.json"
        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(streaming_mode=True),
        )

        loader = DataFrameTuningLoader()
        loader.save_config(config, config_path)

        assert config_path.exists()
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["execution"]["streaming_mode"] is True

    def test_get_template(self):
        loader = DataFrameTuningLoader()
        config = loader.get_template("polars")

        assert isinstance(config, DataFrameTuningConfiguration)
        assert config.metadata is not None
        assert config.metadata.platform == "polars"

    def test_get_optimized_template(self):
        loader = DataFrameTuningLoader()
        config = loader.get_optimized_template("polars")

        assert not config.is_default() or config.metadata is not None

    @pytest.mark.parametrize("template", ["get_template", "get_optimized_template", "get_memory_constrained_template"])
    def test_polars_templates_do_not_choose_an_execution_engine(self, template):
        config = getattr(DataFrameTuningLoader(), template)("polars")

        assert config.execution.engine_affinity is None
        assert config.execution.streaming_mode is False

    def test_merge_configs(self):
        loader = DataFrameTuningLoader()

        base = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=4),
        )
        override = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=8),
            execution=ExecutionConfiguration(streaming_mode=True),
        )

        merged = loader.merge_configs(base, override)

        assert merged.parallelism.thread_count == 8
        assert merged.execution.streaming_mode is True


class TestModuleFunctions:
    def test_load_dataframe_tuning(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump({"parallelism": {"thread_count": 16}}, f)

        config = load_dataframe_tuning(config_path)
        assert config.parallelism.thread_count == 16

    def test_save_dataframe_tuning(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(lazy_evaluation=False),
        )

        save_dataframe_tuning(config, config_path)

        assert config_path.exists()
        loaded = load_dataframe_tuning(config_path)
        assert loaded.execution.lazy_evaluation is False


class TestConfigWithMetadata:
    def test_load_config_with_metadata(self, tmp_path):

        config_path = tmp_path / "config.yaml"
        config_content = {
            "_metadata": {
                "version": "1.0",
                "platform": "polars",
                "description": "Test configuration",
            },
            "parallelism": {"thread_count": 8},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_content, f)

        config = load_dataframe_tuning(config_path)
        assert config.metadata is not None
        assert config.metadata.platform == "polars"
        assert config.metadata.description == "Test configuration"

    def test_save_config_with_metadata(self, tmp_path):

        from benchbox.core.dataframe.tuning import TuningMetadata

        config_path = tmp_path / "config.yaml"
        config = DataFrameTuningConfiguration(
            metadata=TuningMetadata(
                platform="dask",
                description="Dask config",
            ),
            parallelism=ParallelismConfiguration(worker_count=4),
        )

        save_dataframe_tuning(config, config_path)
        loaded = load_dataframe_tuning(config_path)

        assert loaded.metadata is not None
        assert loaded.metadata.platform == "dask"
