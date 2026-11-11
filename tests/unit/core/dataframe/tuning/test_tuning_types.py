# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.dataframe.tuning.types import (
    DataFrameTuningType,
    get_all_platforms,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFrameTuningType:
    def test_enum_values_exist(self):

        assert DataFrameTuningType.THREAD_COUNT.value == "thread_count"
        assert DataFrameTuningType.WORKER_COUNT.value == "worker_count"
        assert DataFrameTuningType.STREAMING_MODE.value == "streaming_mode"
        assert DataFrameTuningType.MEMORY_LIMIT.value == "memory_limit"
        assert DataFrameTuningType.GPU_DEVICE.value == "gpu_device"

    def test_is_compatible_with_platform_polars(self):

        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("polars") is True
        assert DataFrameTuningType.STREAMING_MODE.is_compatible_with_platform("polars") is True
        assert DataFrameTuningType.CHUNK_SIZE.is_compatible_with_platform("polars") is True

        assert DataFrameTuningType.WORKER_COUNT.is_compatible_with_platform("polars") is False
        assert DataFrameTuningType.GPU_DEVICE.is_compatible_with_platform("polars") is False

    def test_is_compatible_with_platform_pandas(self):

        assert DataFrameTuningType.DTYPE_BACKEND.is_compatible_with_platform("pandas") is True
        assert DataFrameTuningType.CHUNK_SIZE.is_compatible_with_platform("pandas") is True

        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("pandas") is False
        assert DataFrameTuningType.GPU_DEVICE.is_compatible_with_platform("pandas") is False

    def test_is_compatible_with_platform_datafusion(self):
        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("datafusion") is True
        assert DataFrameTuningType.CHUNK_SIZE.is_compatible_with_platform("datafusion") is True
        assert DataFrameTuningType.STREAMING_MODE.is_compatible_with_platform("datafusion") is False

    def test_is_compatible_with_platform_dask(self):

        assert DataFrameTuningType.WORKER_COUNT.is_compatible_with_platform("dask") is True
        assert DataFrameTuningType.THREADS_PER_WORKER.is_compatible_with_platform("dask") is True
        assert DataFrameTuningType.MEMORY_LIMIT.is_compatible_with_platform("dask") is True
        assert DataFrameTuningType.SPILL_TO_DISK.is_compatible_with_platform("dask") is True

    def test_is_compatible_with_platform_cudf(self):

        assert DataFrameTuningType.GPU_DEVICE.is_compatible_with_platform("cudf") is True
        assert DataFrameTuningType.GPU_POOL_TYPE.is_compatible_with_platform("cudf") is True
        assert DataFrameTuningType.GPU_SPILL_TO_HOST.is_compatible_with_platform("cudf") is True

    def test_is_compatible_with_platform_case_insensitive(self):

        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("POLARS") is True
        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("Polars") is True

    def test_is_compatible_with_platform_strips_df_suffix(self):

        assert DataFrameTuningType.THREAD_COUNT.is_compatible_with_platform("polars-df") is True
        assert DataFrameTuningType.WORKER_COUNT.is_compatible_with_platform("dask-df") is True

    def test_compatible_with_multiple_platforms(self):

        assert DataFrameTuningType.DTYPE_BACKEND.is_compatible_with_platform("pandas") is True
        assert DataFrameTuningType.DTYPE_BACKEND.is_compatible_with_platform("dask") is True

        assert DataFrameTuningType.GPU_DEVICE.is_compatible_with_platform("cudf") is True
        assert DataFrameTuningType.GPU_DEVICE.is_compatible_with_platform("polars") is False


class TestGetAllPlatforms:
    def test_returns_all_platforms(self):

        platforms = get_all_platforms()
        assert "polars" in platforms
        assert "datafusion" in platforms
        assert "pandas" in platforms
        assert "dask" in platforms
        assert "cudf" in platforms

    def test_returns_list(self):

        platforms = get_all_platforms()
        assert isinstance(platforms, list)


