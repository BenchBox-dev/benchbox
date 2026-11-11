from types import SimpleNamespace

import pytest

from benchbox.platforms import get_platform_adapter
from benchbox.platforms.adapter_factory import _get_dataframe_adapter, get_adapter
from benchbox.platforms.base.adapter import (
    DriverIsolationCapability,
    check_isolation_capability,
)
from benchbox.utils.runtime_env import DriverResolution, ensure_driver_version

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_DUMMY_PLATFORM_INFO = SimpleNamespace(
    installation_command="uv add dummy-driver",
    driver_package="dummy-driver",
)


def _make_resolution_factory(
    *,
    runtime_strategy: str = "isolated-site-packages",
    runtime_path: str | None = "/tmp/dummy-runtime",
    resolved_version: str = "1.0.0",
):

    def _resolve(package_name, requested_version, auto_install=False, install_hint=None):
        return DriverResolution(
            package=package_name or "dummy-driver",
            requested=requested_version,
            resolved=requested_version or resolved_version,
            actual=requested_version or resolved_version,
            auto_install_used=False,
            runtime_strategy=runtime_strategy,
            runtime_path=runtime_path,
            runtime_python_executable="/tmp/dummy-runtime/bin/python",
        )

    return _resolve


def _mock_sql_registry(monkeypatch, adapter_cls, *, runtime_strategy="isolated-site-packages"):

    monkeypatch.setattr("benchbox.platforms.PlatformRegistry.resolve_platform_name", lambda _: "dummy")
    monkeypatch.setattr("benchbox.platforms.PlatformRegistry.get_adapter_class", lambda _: adapter_cls)
    monkeypatch.setattr("benchbox.platforms.PlatformRegistry.get_platform_info", lambda _: _DUMMY_PLATFORM_INFO)
    monkeypatch.setattr(
        "benchbox.platforms.ensure_driver_version",
        _make_resolution_factory(
            runtime_strategy=runtime_strategy,
            runtime_path="/tmp/dummy-runtime" if runtime_strategy != "current-process" else None,
        ),
    )


def test_ensure_driver_version_rejects_requested_version_without_package():
    with pytest.raises(RuntimeError, match="does not declare a driver package"):
        ensure_driver_version(
            package_name=None,
            requested_version="1.2.3",
            auto_install=False,
        )


def test_dataframe_adapter_rejects_requested_version_for_non_package_platform():

    with pytest.raises(RuntimeError, match="does not declare a driver package"):
        get_adapter(
            "pandas-df",
            driver_version="2.0.0",
        )


def test_sql_adapter_contract_propagates_runtime_metadata(monkeypatch):
    class DummyAdapter:
        driver_isolation_capability = DriverIsolationCapability.SUPPORTED

        def __init__(self, **config):
            self.platform_config = dict(config)

        @classmethod
        def from_config(cls, config):
            return cls(**config)

    _mock_sql_registry(monkeypatch, DummyAdapter)

    adapter = get_platform_adapter("dummy", driver_version="1.0.0")

    assert adapter.driver_package == "dummy-driver"
    assert adapter.driver_version_requested == "1.0.0"
    assert adapter.driver_version_resolved == "1.0.0"
    assert adapter.driver_version_actual == "1.0.0"
    assert adapter.driver_runtime_strategy == "isolated-site-packages"
    assert adapter.driver_runtime_path == "/tmp/dummy-runtime"
    assert adapter.driver_runtime_python_executable == "/tmp/dummy-runtime/bin/python"
    assert adapter.platform_config["driver_runtime_strategy"] == "isolated-site-packages"


def test_sql_adapter_contract_rejects_isolated_runtime_without_binding_support(monkeypatch):
    class DummyAdapter:
        def __init__(self, **config):
            self.platform_config = dict(config)

        @classmethod
        def from_config(cls, config):
            return cls(**config)

    _mock_sql_registry(monkeypatch, DummyAdapter)

    with pytest.raises(RuntimeError, match="does not support isolated driver runtime binding"):
        get_platform_adapter("dummy", driver_version="1.0.0")


def test_sql_adapter_contract_does_not_infer_requested_version(monkeypatch):
    class DummyAdapter:
        def __init__(self, **config):
            self.platform_config = dict(config)

        @classmethod
        def from_config(cls, config):
            return cls(**config)

    _mock_sql_registry(monkeypatch, DummyAdapter, runtime_strategy="current-process")

    adapter = get_platform_adapter("dummy", driver_version_resolved="1.0.0")

    assert adapter.driver_version_requested is None
    assert adapter.driver_version_resolved == "1.0.0"
    assert adapter.driver_version_actual == "1.0.0"


def _mock_dataframe_registry(monkeypatch, adapter_cls, *, runtime_strategy="current-process", resolved_version="2.0.0"):

    monkeypatch.setattr(
        "benchbox.platforms.adapter_factory.PlatformRegistry.get_platform_info",
        lambda _: _DUMMY_PLATFORM_INFO,
    )
    resolve_fn = _make_resolution_factory(
        runtime_strategy=runtime_strategy,
        runtime_path="/tmp/dummy-runtime" if runtime_strategy != "current-process" else None,
        resolved_version=resolved_version,
    )
    monkeypatch.setattr("benchbox.platforms.adapter_factory.ensure_driver_version", resolve_fn)
    monkeypatch.setattr("benchbox.platforms.dataframe.DATAFUSION_DF_AVAILABLE", True)
    monkeypatch.setattr("benchbox.platforms.dataframe.DataFusionDataFrameAdapter", adapter_cls)


def test_dataframe_adapter_contract_does_not_infer_requested_version(monkeypatch):
    class DummyDataFrameAdapter:
        def __init__(self, **config):
            self.platform_config = dict(config)

    _mock_dataframe_registry(monkeypatch, DummyDataFrameAdapter)

    adapter = _get_dataframe_adapter("datafusion", driver_version_resolved="2.0.0")

    assert adapter.driver_version_requested is None
    assert adapter.driver_version_resolved == "2.0.0"
    assert adapter.driver_version_actual == "2.0.0"


def test_dataframe_adapter_contract_rejects_isolated_runtime_without_binding_support(monkeypatch):
    class DummyDataFrameAdapter:
        def __init__(self, **config):
            self.platform_config = dict(config)

    _mock_dataframe_registry(monkeypatch, DummyDataFrameAdapter, runtime_strategy="isolated-site-packages")

    with pytest.raises(RuntimeError, match="does not support isolated driver runtime binding"):
        _get_dataframe_adapter("datafusion", driver_version="2.0.0")


class TestDriverIsolationCapabilityDeclarations:
    def test_duckdb_declares_supported(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        assert DuckDBAdapter.driver_isolation_capability == DriverIsolationCapability.SUPPORTED

    def test_datafusion_sql_declares_supported(self):
        from benchbox.platforms.datafusion import DataFusionAdapter

        assert DataFusionAdapter.driver_isolation_capability == DriverIsolationCapability.SUPPORTED

    def test_datafusion_df_declares_supported(self):
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        assert DataFusionDataFrameAdapter.driver_isolation_capability == DriverIsolationCapability.SUPPORTED

    def test_snowflake_declares_feasible_client_only(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        assert SnowflakeAdapter.driver_isolation_capability == DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def test_spark_declares_not_feasible(self):
        from benchbox.platforms.spark import SparkAdapter

        assert SparkAdapter.driver_isolation_capability == DriverIsolationCapability.NOT_FEASIBLE

    def test_clickhouse_local_declares_not_feasible(self):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        assert ClickHouseAdapter.driver_isolation_capability == DriverIsolationCapability.NOT_FEASIBLE

    def test_clickhouse_cloud_declares_feasible_client_only(self):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        assert ClickHouseCloudAdapter.driver_isolation_capability == DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def test_base_adapter_defaults_to_not_applicable(self):
        from benchbox.platforms.base.adapter import PlatformAdapter

        assert PlatformAdapter.driver_isolation_capability == DriverIsolationCapability.NOT_APPLICABLE

    def test_dataframe_mixin_defaults_to_not_applicable(self):
        from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

        assert BenchmarkExecutionMixin.driver_isolation_capability == DriverIsolationCapability.NOT_APPLICABLE


class TestCheckIsolationCapability:
    def test_supported_does_not_raise(self):
        class Adapter:
            driver_isolation_capability = DriverIsolationCapability.SUPPORTED

        check_isolation_capability(Adapter, "test", "isolated-site-packages")

    def test_not_applicable_raises_with_correct_message(self):
        class Adapter:
            driver_isolation_capability = DriverIsolationCapability.NOT_APPLICABLE

        with pytest.raises(RuntimeError, match="no versioned driver package"):
            check_isolation_capability(Adapter, "test", "isolated-site-packages")

    def test_not_feasible_raises_with_correct_message(self):
        class Adapter:
            driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

        with pytest.raises(RuntimeError, match="technical constraints"):
            check_isolation_capability(Adapter, "test", "isolated-site-packages")

    def test_feasible_client_only_raises_with_correct_message(self):
        class Adapter:
            driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

        with pytest.raises(RuntimeError, match="independent from the engine"):
            check_isolation_capability(Adapter, "test", "isolated-site-packages")

    def test_current_process_strategy_never_raises(self):
        class Adapter:
            driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

        check_isolation_capability(Adapter, "test", "current-process")


