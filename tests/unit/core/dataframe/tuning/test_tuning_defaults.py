# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    SystemProfile,
    detect_system_profile,
    get_profile_summary,
    get_smart_defaults,
)
from benchbox.core.dataframe.tuning.profiles import create_profile_config

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize("profile", ["streaming", "memory-constrained", "gpu"])
def test_datafusion_rejects_unsupported_profiles(profile: str) -> None:
    with pytest.raises(ValueError, match="DataFusion does not support"):
        create_profile_config("datafusion", profile)


class TestSystemProfile:
    def test_default_values(self):

        profile = SystemProfile(cpu_cores=4, available_memory_gb=8.0)
        assert profile.cpu_cores == 4
        assert profile.available_memory_gb == 8.0
        assert profile.has_gpu is False
        assert profile.gpu_memory_gb == 0.0
        assert profile.gpu_device_count == 0

    def test_with_gpu(self):

        profile = SystemProfile(
            cpu_cores=8,
            available_memory_gb=32.0,
            has_gpu=True,
            gpu_memory_gb=16.0,
            gpu_device_count=2,
        )
        assert profile.has_gpu is True
        assert profile.gpu_memory_gb == 16.0
        assert profile.gpu_device_count == 2


class TestDetectSystemProfile:
    def test_returns_system_profile(self):

        profile = detect_system_profile()
        assert isinstance(profile, SystemProfile)
        assert profile.cpu_cores >= 1
        assert profile.available_memory_gb > 0


class TestGetProfileSummary:
    def test_low_memory_category(self):

        profile = SystemProfile(cpu_cores=4, available_memory_gb=2.0)
        summary = get_profile_summary(profile)
        assert summary["memory_category"] == "very_low"

    def test_medium_memory_category(self):

        profile = SystemProfile(cpu_cores=8, available_memory_gb=16.0)
        summary = get_profile_summary(profile)
        assert summary["memory_category"] == "medium"

    def test_high_memory_category(self):

        profile = SystemProfile(cpu_cores=16, available_memory_gb=64.0)
        summary = get_profile_summary(profile)
        assert summary["memory_category"] == "high"

    def test_gpu_info_in_summary(self):

        profile = SystemProfile(
            cpu_cores=8,
            available_memory_gb=16.0,
            has_gpu=True,
            gpu_memory_gb=8.0,
            gpu_device_count=1,
        )
        summary = get_profile_summary(profile)
        assert summary["has_gpu"] is True
        assert summary["gpu_memory_gb"] == 8.0


class TestGetSmartDefaults:
    def test_returns_configuration(self):

        config = get_smart_defaults("polars")
        assert isinstance(config, DataFrameTuningConfiguration)

    def test_datafusion_defaults_use_only_supported_controls(self):
        profile = SystemProfile(cpu_cores=6, available_memory_gb=2.0)
        config = get_smart_defaults("datafusion", profile)

        assert config.parallelism.thread_count == 6
        assert config.memory.chunk_size == 50_000
        assert config.execution.streaming_mode is False

    def test_polars_defaults(self):

        profile = SystemProfile(cpu_cores=8, available_memory_gb=16.0)
        config = get_smart_defaults("polars", profile)

        assert config.execution.lazy_evaluation is True

    def test_polars_low_memory_defaults(self):

        profile = SystemProfile(cpu_cores=4, available_memory_gb=2.0)
        config = get_smart_defaults("polars", profile)

        assert config.execution.engine_affinity is None
        assert config.execution.streaming_mode is False
        assert config.memory.chunk_size is not None

    def test_low_memory_log_does_not_claim_streaming(self, caplog):
        with caplog.at_level("INFO", logger="benchbox.core.dataframe.tuning.defaults"):
            get_smart_defaults("polars", SystemProfile(cpu_cores=4, available_memory_gb=2.0))

        assert "Low memory detected" in caplog.text
        assert "streaming" not in caplog.text

    def test_polars_high_memory_defaults(self):

        profile = SystemProfile(cpu_cores=32, available_memory_gb=128.0)
        config = get_smart_defaults("polars", profile)

        assert config.execution.engine_affinity is None
        assert config.execution.streaming_mode is False

    def test_pandas_defaults(self):

        profile = SystemProfile(cpu_cores=4, available_memory_gb=16.0)
        config = get_smart_defaults("pandas", profile)

        assert config.data_types.dtype_backend == "pyarrow"

    def test_pandas_low_memory_defaults(self):

        profile = SystemProfile(cpu_cores=4, available_memory_gb=4.0)
        config = get_smart_defaults("pandas", profile)

        assert config.data_types.dtype_backend == "numpy_nullable"
        assert config.io.memory_map is True

    def test_dask_defaults(self):

        profile = SystemProfile(cpu_cores=8, available_memory_gb=32.0)
        config = get_smart_defaults("dask", profile)

        assert config.parallelism.worker_count is not None
        assert config.parallelism.threads_per_worker is not None
        assert config.memory.memory_limit is not None

    def test_cudf_defaults(self):

        profile = SystemProfile(
            cpu_cores=8,
            available_memory_gb=32.0,
            has_gpu=True,
            gpu_memory_gb=16.0,
            gpu_device_count=1,
        )
        config = get_smart_defaults("cudf", profile)

        assert config.gpu.enabled is True
        assert config.gpu.device_id == 0

    def test_cudf_small_gpu_defaults(self):

        profile = SystemProfile(
            cpu_cores=8,
            available_memory_gb=32.0,
            has_gpu=True,
            gpu_memory_gb=4.0,
            gpu_device_count=1,
        )
        config = get_smart_defaults("cudf", profile)

        assert config.gpu.spill_to_host is True
        assert config.gpu.pool_type == "managed"

    def test_platform_normalization(self):

        config1 = get_smart_defaults("POLARS")
        config2 = get_smart_defaults("polars-df")

        assert isinstance(config1, DataFrameTuningConfiguration)
        assert isinstance(config2, DataFrameTuningConfiguration)

    def test_auto_detect_profile(self):

        config = get_smart_defaults("polars")
        assert config.metadata is not None
        assert config.metadata.platform == "polars"

    def test_metadata_included(self):

        config = get_smart_defaults("dask")
        assert config.metadata is not None
        assert config.metadata.generated_by == "benchbox-smart-defaults"
