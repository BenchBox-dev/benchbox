import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGetPlatformAdapterArgs:
    def test_sql_platform_receives_benchmark_and_scale_factor(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()
        mock_adapter.driver_package = None
        mock_adapter.driver_version_requested = None
        mock_adapter.driver_version_resolved = None
        mock_adapter.driver_auto_install_used = False

        with patch("benchbox.platforms.get_platform_adapter", return_value=mock_adapter) as mock_get:
            _get_platform_adapter("duckdb", benchmark="tpch", scale_factor=0.01)

        mock_get.assert_called_once_with("duckdb", benchmark="tpch", scale_factor=0.01)

    def test_dataframe_platform_does_not_receive_benchmark_or_scale_factor(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()

        with (
            patch("benchbox.platforms.is_dataframe_platform", return_value=True),
            patch("benchbox.platforms.get_dataframe_adapter", return_value=mock_adapter) as mock_get,
        ):
            _get_platform_adapter("polars-df", benchmark="tpch", scale_factor=0.01)

        mock_get.assert_called_once_with("polars-df")

    def test_dataframe_platform_forwards_other_config(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()

        with (
            patch("benchbox.platforms.is_dataframe_platform", return_value=True),
            patch("benchbox.platforms.get_dataframe_adapter", return_value=mock_adapter) as mock_get,
        ):
            _get_platform_adapter("polars-df", benchmark="tpch", scale_factor=1.0, streaming=True)

        mock_get.assert_called_once_with("polars-df", streaming=True)

    def test_sql_platform_forwards_all_config(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()
        mock_adapter.driver_package = None
        mock_adapter.driver_version_requested = None
        mock_adapter.driver_version_resolved = None
        mock_adapter.driver_auto_install_used = False

        with patch("benchbox.platforms.get_platform_adapter", return_value=mock_adapter) as mock_get:
            _get_platform_adapter("datafusion", benchmark="tpch", scale_factor=1.0, memory_limit="8G")

        mock_get.assert_called_once_with("datafusion", benchmark="tpch", scale_factor=1.0, memory_limit="8G")


class TestRunBenchmarkAdapterCreation:
    def test_run_benchmark_passes_benchmark_to_adapter_factory(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()
        mock_adapter.driver_package = None
        mock_adapter.driver_version_requested = None
        mock_adapter.driver_version_resolved = None
        mock_adapter.driver_auto_install_used = False

        with patch("benchbox.platforms.get_platform_adapter", return_value=mock_adapter) as mock_get:
            _get_platform_adapter("duckdb", benchmark="tpcds", scale_factor=10.0)

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["benchmark"] == "tpcds"
        assert call_kwargs["scale_factor"] == 10.0

    def test_sql_adapter_from_config_receives_required_keys(self):
        from benchbox.platforms import get_platform_adapter

        mock_adapter_class = MagicMock()
        mock_adapter_class.from_config.return_value = MagicMock(
            driver_package=None,
            driver_version_requested=None,
            driver_version_resolved=None,
            driver_auto_install_used=False,
        )

        with (
            patch("benchbox.platforms.PlatformRegistry.resolve_platform_name", return_value="duckdb"),
            patch("benchbox.platforms.PlatformRegistry.get_adapter_class", return_value=mock_adapter_class),
            patch(
                "benchbox.platforms.PlatformRegistry.get_platform_info",
                return_value=MagicMock(installation_command="pip install duckdb", driver_package="duckdb"),
            ),
            patch(
                "benchbox.platforms.ensure_driver_version",
                return_value=MagicMock(resolved=None, requested=None, package=None, auto_install_used=False),
            ),
        ):
            get_platform_adapter("duckdb", benchmark="tpch", scale_factor=0.01)

        config_passed = mock_adapter_class.from_config.call_args[0][0]
        assert "benchmark" in config_passed
        assert config_passed["benchmark"] == "tpch"
        assert "scale_factor" in config_passed
        assert config_passed["scale_factor"] == 0.01


class TestDataFrameAdapterSafety:
    @pytest.mark.parametrize("platform", ["polars-df", "pandas-df"])
    def test_dataframe_adapter_not_passed_benchmark_kwargs(self, platform):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        mock_adapter = MagicMock()

        with (
            patch("benchbox.platforms.is_dataframe_platform", return_value=True),
            patch("benchbox.platforms.get_dataframe_adapter", return_value=mock_adapter) as mock_get,
        ):
            _get_platform_adapter(platform, benchmark="tpch", scale_factor=0.01)

        _, kwargs = mock_get.call_args
        assert "benchmark" not in kwargs
        assert "scale_factor" not in kwargs

    def test_real_polars_adapter_rejects_benchmark_kwarg(self):
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            PolarsDataFrameAdapter(benchmark="tpch", scale_factor=0.01)

    def test_real_pandas_adapter_rejects_benchmark_kwarg(self):
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            PandasDataFrameAdapter(benchmark="tpch", scale_factor=0.01)

    def test_get_platform_adapter_succeeds_for_polars_with_benchmark_kwargs(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        adapter = _get_platform_adapter("polars-df", benchmark="tpch", scale_factor=0.01)
        assert adapter is not None

    def test_get_platform_adapter_succeeds_for_pandas_with_benchmark_kwargs(self):
        from benchbox.mcp.tools.benchmark import _get_platform_adapter

        adapter = _get_platform_adapter("pandas-df", benchmark="tpch", scale_factor=0.01)
        assert adapter is not None


