# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms import (
    CUDF_AVAILABLE,
    DASK_AVAILABLE,
    get_dataframe_requirements,
    is_dataframe_platform,
    list_available_dataframe_platforms,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPandasFamilyPlatformRegistration:
    def test_cudf_df_is_dataframe_platform(self):

        assert is_dataframe_platform("cudf-df") is True

    def test_dask_df_is_dataframe_platform(self):

        assert is_dataframe_platform("dask-df") is True

    def test_case_insensitive_platform_check(self):

        assert is_dataframe_platform("CUDF-DF") is True
        assert is_dataframe_platform("CuDF-Df") is True
        assert is_dataframe_platform("DASK-DF") is True
        assert is_dataframe_platform("Dask-Df") is True


class TestPandasFamilyPlatformListing:
    def test_list_includes_cudf_df(self):

        platforms = list_available_dataframe_platforms()

        assert "cudf-df" in platforms
        assert platforms["cudf-df"] == CUDF_AVAILABLE

    def test_list_includes_dask_df(self):

        platforms = list_available_dataframe_platforms()

        assert "dask-df" in platforms
        assert platforms["dask-df"] == DASK_AVAILABLE

    def test_list_has_all_expected_platforms(self):

        platforms = list_available_dataframe_platforms()

        expected = {
            "polars-df",
            "pandas-df",
            "cudf-df",
            "dask-df",
            "datafusion-df",
            "pyspark-df",
            "lakesail-df",
        }
        assert set(platforms.keys()) == expected


class TestPandasFamilyRequirements:
    def test_cudf_df_requirements_contain_cudf(self):

        req = get_dataframe_requirements("cudf-df")

        assert "cudf" in req.lower()

    def test_cudf_df_requirements_mention_gpu(self):

        req = get_dataframe_requirements("cudf-df")

        assert "gpu" in req.lower() or "cuda" in req.lower()

    def test_dask_df_requirements_contain_dask(self):

        req = get_dataframe_requirements("dask-df")

        assert "dask" in req.lower()

    def test_dask_df_requirements_mention_distributed(self):

        req = get_dataframe_requirements("dask-df")

        assert "distributed" in req.lower()


class TestPandasFamilyPlatformHooks:
    def test_cudf_df_device_id_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if CUDF_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("cudf")
            assert "device_id" in specs

    def test_cudf_df_spill_to_host_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if CUDF_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("cudf")
            assert "spill_to_host" in specs

    def test_dask_df_n_workers_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if DASK_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("dask")
            assert "n_workers" in specs

    def test_dask_df_threads_per_worker_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if DASK_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("dask")
            assert "threads_per_worker" in specs

    def test_dask_df_use_distributed_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if DASK_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("dask")
            assert "use_distributed" in specs

    def test_dask_df_scheduler_address_option_registered(self):

        from benchbox.cli.platform_hooks import PlatformHookRegistry

        if DASK_AVAILABLE:
            specs = PlatformHookRegistry.list_option_specs("dask")
            assert "scheduler_address" in specs


class TestPandasFamilyFactory:
    def test_unknown_platform_raises_value_error(self):

        from benchbox.platforms import get_dataframe_adapter

        with pytest.raises(ValueError, match="Unknown DataFrame platform"):
            get_dataframe_adapter("unknown-df")

    def test_cudf_df_in_factory_mapping(self):

        from benchbox.platforms import get_dataframe_adapter

        if not CUDF_AVAILABLE:
            with pytest.raises(ImportError, match="[Cc]u[Dd][Ff]"):
                get_dataframe_adapter("cudf-df")
        else:
            adapter = get_dataframe_adapter("cudf-df")
            assert adapter is not None

    def test_dask_df_in_factory_mapping(self, monkeypatch):

        from benchbox.platforms import get_dataframe_adapter

        if not DASK_AVAILABLE:
            with pytest.raises(ImportError, match="[Dd]ask"):
                get_dataframe_adapter("dask-df")
        else:
            from tests.utilities.session_isolation import own_environment

            own_environment(monkeypatch, ["MALLOC_TRIM_THRESHOLD_", "PYTHONHASHSEED", "__DASK_PARENT_PID"])
            adapter = get_dataframe_adapter("dask-df")
            try:
                assert adapter is not None
            finally:
                adapter.close()


class TestAdapterModuleAvailability:
    def test_cudf_available_flag_is_boolean(self):

        from benchbox.platforms.dataframe import CUDF_AVAILABLE as available

        assert isinstance(available, bool)

    def test_dask_available_flag_is_boolean(self):

        from benchbox.platforms.dataframe import DASK_AVAILABLE as available

        assert isinstance(available, bool)

    def test_cudf_adapter_class_import(self):

        from benchbox.platforms.dataframe import CuDFDataFrameAdapter

        if CUDF_AVAILABLE:
            assert CuDFDataFrameAdapter is not None
            adapter = CuDFDataFrameAdapter()
            assert adapter.platform_name == "cuDF"
        else:
            assert CuDFDataFrameAdapter is not None
            with pytest.raises(ImportError):
                CuDFDataFrameAdapter()

    def test_dask_adapter_class_import(self):

        from benchbox.platforms.dataframe import DaskDataFrameAdapter

        if DASK_AVAILABLE:
            assert DaskDataFrameAdapter is not None
            adapter = DaskDataFrameAdapter(execution_engine="threaded")
            assert adapter.platform_name == "Dask"
        else:
            assert DaskDataFrameAdapter is not None
            with pytest.raises(ImportError):
                DaskDataFrameAdapter()


class TestModuleExports:
    def test_platforms_init_exports_cudf_adapter(self):

        from benchbox import platforms

        assert hasattr(platforms, "CuDFDataFrameAdapter")
        assert hasattr(platforms, "CUDF_AVAILABLE")

    def test_platforms_init_exports_dask_adapter(self):

        from benchbox import platforms

        assert hasattr(platforms, "DaskDataFrameAdapter")
        assert hasattr(platforms, "DASK_AVAILABLE")

    def test_dataframe_init_exports_cudf_adapter(self):

        from benchbox.platforms import dataframe

        assert hasattr(dataframe, "CuDFDataFrameAdapter")
        assert hasattr(dataframe, "CUDF_AVAILABLE")

    def test_dataframe_init_exports_dask_adapter(self):

        from benchbox.platforms import dataframe

        assert hasattr(dataframe, "DaskDataFrameAdapter")
        assert hasattr(dataframe, "DASK_AVAILABLE")
