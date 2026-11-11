# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    DataTypeConfiguration,
    ExecutionConfiguration,
    GPUConfiguration,
    IOConfiguration,
    MemoryConfiguration,
    ParallelismConfiguration,
    TuningMetadata,
)
from benchbox.core.dataframe.tuning.types import DataFrameTuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestParallelismConfiguration:
    def test_default_values(self):

        config = ParallelismConfiguration()
        assert config.thread_count is None
        assert config.worker_count is None
        assert config.threads_per_worker is None

    def test_custom_values(self):

        config = ParallelismConfiguration(
            thread_count=8,
            worker_count=4,
            threads_per_worker=2,
        )
        assert config.thread_count == 8
        assert config.worker_count == 4
        assert config.threads_per_worker == 2

    def test_validation_thread_count_negative(self):

        with pytest.raises(ValueError, match="thread_count must be >= 1"):
            ParallelismConfiguration(thread_count=0)

    def test_validation_worker_count_negative(self):

        with pytest.raises(ValueError, match="worker_count must be >= 1"):
            ParallelismConfiguration(worker_count=-1)

    def test_is_default(self):
        assert ParallelismConfiguration().is_default() is True
        assert ParallelismConfiguration(thread_count=4).is_default() is False

    def test_to_dict(self):
        config = ParallelismConfiguration(thread_count=8)
        result = config.to_dict()
        assert result["thread_count"] == 8
        assert result["worker_count"] is None

    def test_from_dict(self):
        data = {"thread_count": 8, "worker_count": 4}
        config = ParallelismConfiguration.from_dict(data)
        assert config.thread_count == 8
        assert config.worker_count == 4


class TestMemoryConfiguration:
    def test_default_values(self):

        config = MemoryConfiguration()
        assert config.memory_limit is None
        assert config.chunk_size is None
        assert config.spill_to_disk is False
        assert config.rechunk_after_filter is True

    def test_memory_limit_valid_formats(self):

        valid_formats = ["4GB", "2GiB", "512MB", "1.5GB", "100MiB"]
        for fmt in valid_formats:
            config = MemoryConfiguration(memory_limit=fmt)
            assert config.memory_limit == fmt

    def test_memory_limit_invalid_format(self):

        with pytest.raises(ValueError, match="Invalid memory_limit format"):
            MemoryConfiguration(memory_limit="4 gigabytes")

    def test_chunk_size_validation(self):

        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            MemoryConfiguration(chunk_size=0)


class TestExecutionConfiguration:
    def test_default_values(self):

        config = ExecutionConfiguration()
        assert config.streaming_mode is False
        assert config.engine_affinity is None
        assert config.lazy_evaluation is True
        assert config.collect_timeout is None

    def test_collect_timeout_validation(self):

        with pytest.raises(ValueError, match="collect_timeout must be >= 1"):
            ExecutionConfiguration(collect_timeout=0)


class TestDataTypeConfiguration:
    def test_default_values(self):

        config = DataTypeConfiguration()
        assert config.dtype_backend == "numpy_nullable"
        assert config.enable_string_cache is False
        assert config.auto_categorize_strings is False
        assert config.categorical_threshold == 0.5

    def test_dtype_backend_validation(self):

        with pytest.raises(ValueError, match="Invalid dtype_backend"):
            DataTypeConfiguration(dtype_backend="invalid")

    def test_categorical_threshold_validation(self):

        with pytest.raises(ValueError, match="categorical_threshold must be between"):
            DataTypeConfiguration(categorical_threshold=1.5)


class TestIOConfiguration:
    def test_default_values(self):

        config = IOConfiguration()
        assert config.memory_pool == "default"
        assert config.memory_map is False
        assert config.pre_buffer is True
        assert config.row_group_size is None

    def test_memory_pool_validation(self):

        with pytest.raises(ValueError, match="Invalid memory_pool"):
            IOConfiguration(memory_pool="invalid_pool")


class TestGPUConfiguration:
    def test_default_values(self):

        config = GPUConfiguration()
        assert config.enabled is False
        assert config.device_id == 0
        assert config.spill_to_host is True
        assert config.pool_type == "default"

    def test_device_id_validation(self):

        with pytest.raises(ValueError, match="device_id must be >= 0"):
            GPUConfiguration(device_id=-1)

    def test_pool_type_validation(self):

        with pytest.raises(ValueError, match="Invalid pool_type"):
            GPUConfiguration(pool_type="invalid")


class TestDataFrameTuningConfiguration:
    def test_default_configuration(self):

        config = DataFrameTuningConfiguration()
        assert config.is_default() is True

    def test_get_enabled_settings_empty(self):
        config = DataFrameTuningConfiguration()
        enabled = config.get_enabled_settings()
        assert len(enabled) == 0

    def test_get_enabled_settings_with_custom(self):
        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=8),
            execution=ExecutionConfiguration(streaming_mode=True),
        )
        enabled = config.get_enabled_settings()
        assert DataFrameTuningType.THREAD_COUNT in enabled
        assert DataFrameTuningType.STREAMING_MODE in enabled
        assert len(enabled) == 2

    def test_to_dict(self):
        config = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=8),
        )
        result = config.to_dict()
        assert "parallelism" in result
        assert result["parallelism"]["thread_count"] == 8

    def test_to_dict_omits_defaults(self):
        config = DataFrameTuningConfiguration()
        result = config.to_dict()
        assert "parallelism" not in result
        assert "memory" not in result

    def test_from_dict(self):
        data = {
            "parallelism": {"thread_count": 8},
            "execution": {"streaming_mode": True},
        }
        config = DataFrameTuningConfiguration.from_dict(data)
        assert config.parallelism.thread_count == 8
        assert config.execution.streaming_mode is True

    def test_to_full_dict(self):
        config = DataFrameTuningConfiguration()
        result = config.to_full_dict()
        assert "parallelism" in result
        assert "memory" in result
        assert "execution" in result
        assert "data_types" in result
        assert "io" in result
        assert "gpu" in result

    def test_get_summary(self):
        config = DataFrameTuningConfiguration(
            execution=ExecutionConfiguration(streaming_mode=True),
            gpu=GPUConfiguration(enabled=True),
        )
        summary = config.get_summary()
        assert summary["has_streaming"] is True
        assert summary["has_gpu"] is True
        assert summary["setting_count"] == 2


class TestTuningMetadata:
    def test_default_values(self):

        metadata = TuningMetadata()
        assert metadata.version == "1.0"
        assert metadata.format == "dataframe_tuning"
        assert metadata.platform is None
        assert metadata.description is None

    def test_to_dict(self):
        metadata = TuningMetadata(
            platform="polars",
            description="Test configuration",
        )
        result = metadata.to_dict()
        assert result["platform"] == "polars"
        assert result["description"] == "Test configuration"
        assert result["version"] == "1.0"

    def test_from_dict(self):
        data = {"platform": "dask", "description": "Test"}
        metadata = TuningMetadata.from_dict(data)
        assert metadata.platform == "dask"
        assert metadata.description == "Test"


