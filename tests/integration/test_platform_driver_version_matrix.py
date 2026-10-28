from __future__ import annotations

import pytest

from benchbox.platforms.base.adapter import DriverIsolationCapability, check_isolation_capability

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


_SUPPORTED_ADAPTERS = [
    ("benchbox.platforms.duckdb", "DuckDBAdapter", DriverIsolationCapability.SUPPORTED),
    ("benchbox.platforms.datafusion", "DataFusionAdapter", DriverIsolationCapability.SUPPORTED),
    ("benchbox.platforms.dataframe.datafusion_df", "DataFusionDataFrameAdapter", DriverIsolationCapability.SUPPORTED),
]

_FEASIBLE_CLIENT_ONLY_ADAPTERS = [
    ("benchbox.platforms.snowflake", "SnowflakeAdapter"),
    ("benchbox.platforms.bigquery", "BigQueryAdapter"),
    ("benchbox.platforms.redshift", "RedshiftAdapter"),
    ("benchbox.platforms.clickhouse_cloud", "ClickHouseCloudAdapter"),
    ("benchbox.platforms.databricks.adapter", "DatabricksAdapter"),
    ("benchbox.platforms.firebolt", "FireboltAdapter"),
    ("benchbox.platforms.databend.adapter", "DatabendAdapter"),
    ("benchbox.platforms.trino", "TrinoAdapter"),
    ("benchbox.platforms.starburst", "StarburstAdapter"),
    ("benchbox.platforms.presto", "PrestoAdapter"),
    ("benchbox.platforms.athena", "AthenaAdapter"),
    ("benchbox.platforms.influxdb.adapter", "InfluxDBAdapter"),
    ("benchbox.platforms.postgresql", "PostgreSQLAdapter"),
    ("benchbox.platforms.doris", "DorisAdapter"),
    ("benchbox.platforms.starrocks.adapter", "StarRocksAdapter"),
    ("benchbox.platforms.questdb", "QuestDBAdapter"),
    ("benchbox.platforms.timescaledb", "TimescaleDBAdapter"),
    ("benchbox.platforms.snowpark_connect", "SnowparkConnectAdapter"),
    ("benchbox.platforms.motherduck", "MotherDuckAdapter"),
]

_NOT_FEASIBLE_ADAPTERS = [
    ("benchbox.platforms.spark", "SparkAdapter"),
    ("benchbox.platforms.lakesail", "LakeSailAdapter"),
    ("benchbox.platforms.clickhouse.adapter", "ClickHouseAdapter"),
    ("benchbox.platforms.azure_synapse", "AzureSynapseAdapter"),
    ("benchbox.platforms.fabric_warehouse", "FabricWarehouseAdapter"),
    ("benchbox.platforms.fabric_lakehouse", "FabricLakehouseAdapter"),
    ("benchbox.platforms.aws.emr_serverless_adapter", "EMRServerlessAdapter"),
    ("benchbox.platforms.aws.athena_spark_adapter", "AthenaSparkAdapter"),
    ("benchbox.platforms.aws.glue_adapter", "AWSGlueAdapter"),
    ("benchbox.platforms.gcp.dataproc_adapter", "DataprocAdapter"),
    ("benchbox.platforms.gcp.dataproc_serverless_adapter", "DataprocServerlessAdapter"),
    ("benchbox.platforms.azure.fabric_spark_adapter", "FabricSparkAdapter"),
    ("benchbox.platforms.azure.synapse_spark_adapter", "SynapseSparkAdapter"),
]

_NOT_APPLICABLE_ADAPTERS = [
    ("benchbox.platforms.sqlite", "SQLiteAdapter"),
    ("benchbox.platforms.polars_platform", "PolarsAdapter"),
]


def _import_adapter(module_path: str, class_name: str):

    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


@pytest.mark.parametrize(
    "module_path,class_name,expected",
    _SUPPORTED_ADAPTERS,
    ids=[t[1] for t in _SUPPORTED_ADAPTERS],
)
def test_supported_adapters_declare_supported(module_path, class_name, expected):
    adapter_cls = _import_adapter(module_path, class_name)
    assert adapter_cls.driver_isolation_capability == expected


@pytest.mark.parametrize(
    "module_path,class_name",
    _FEASIBLE_CLIENT_ONLY_ADAPTERS,
    ids=[t[1] for t in _FEASIBLE_CLIENT_ONLY_ADAPTERS],
)
def test_feasible_client_only_adapters(module_path, class_name):
    adapter_cls = _import_adapter(module_path, class_name)
    assert adapter_cls.driver_isolation_capability == DriverIsolationCapability.FEASIBLE_CLIENT_ONLY


@pytest.mark.parametrize(
    "module_path,class_name",
    _NOT_FEASIBLE_ADAPTERS,
    ids=[t[1] for t in _NOT_FEASIBLE_ADAPTERS],
)
def test_not_feasible_adapters(module_path, class_name):
    adapter_cls = _import_adapter(module_path, class_name)
    assert adapter_cls.driver_isolation_capability == DriverIsolationCapability.NOT_FEASIBLE


@pytest.mark.parametrize(
    "module_path,class_name",
    _NOT_APPLICABLE_ADAPTERS,
    ids=[t[1] for t in _NOT_APPLICABLE_ADAPTERS],
)
def test_not_applicable_adapters(module_path, class_name):
    adapter_cls = _import_adapter(module_path, class_name)
    assert adapter_cls.driver_isolation_capability == DriverIsolationCapability.NOT_APPLICABLE


class TestUnsupportedIsolationFailFast:
    @pytest.mark.parametrize(
        "module_path,class_name",
        _FEASIBLE_CLIENT_ONLY_ADAPTERS,
        ids=[t[1] for t in _FEASIBLE_CLIENT_ONLY_ADAPTERS],
    )
    def test_feasible_client_only_rejects_isolation(self, module_path, class_name):
        adapter_cls = _import_adapter(module_path, class_name)
        with pytest.raises(RuntimeError, match="independent from the engine"):
            check_isolation_capability(adapter_cls, class_name, "isolated-site-packages")

    @pytest.mark.parametrize(
        "module_path,class_name",
        _NOT_FEASIBLE_ADAPTERS,
        ids=[t[1] for t in _NOT_FEASIBLE_ADAPTERS],
    )
    def test_not_feasible_rejects_isolation(self, module_path, class_name):
        adapter_cls = _import_adapter(module_path, class_name)
        with pytest.raises(RuntimeError, match="technical constraints"):
            check_isolation_capability(adapter_cls, class_name, "isolated-site-packages")

    @pytest.mark.parametrize(
        "module_path,class_name,_expected",
        _SUPPORTED_ADAPTERS,
        ids=[t[1] for t in _SUPPORTED_ADAPTERS],
    )
    def test_supported_adapters_allow_isolation(self, module_path, class_name, _expected):
        adapter_cls = _import_adapter(module_path, class_name)

        check_isolation_capability(adapter_cls, class_name, "isolated-site-packages")


def test_datafusion_sql_adapter_populates_driver_version_actual():

    from benchbox.platforms.datafusion import DataFusionAdapter

    adapter = DataFusionAdapter(database_path=":memory:")
    info = adapter.get_platform_info()
    assert info.get("driver_version_actual") is not None
    assert info["driver_version_actual"] == info["platform_version"]


def test_datafusion_df_adapter_populates_driver_version_actual():

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter()
    info = adapter.get_platform_info()
    assert info.get("driver_version_actual") is not None
    assert info["driver_version_actual"] == info["version"]


def test_duckdb_adapter_populates_driver_version_actual():

    from benchbox.platforms.duckdb import DuckDBAdapter

    adapter = DuckDBAdapter(database_path=":memory:")
    info = adapter.get_platform_info()
    assert info.get("driver_version_actual") is not None
    assert info["driver_version_actual"] == info["platform_version"]


_ALL_KNOWN_ADAPTERS = (
    {class_name for _, class_name, *_ in _SUPPORTED_ADAPTERS}
    | {class_name for _, class_name in _FEASIBLE_CLIENT_ONLY_ADAPTERS}
    | {class_name for _, class_name in _NOT_FEASIBLE_ADAPTERS}
    | {class_name for _, class_name in _NOT_APPLICABLE_ADAPTERS}
)


def test_all_registered_adapters_declare_capability():

    from benchbox.core.platform_registry import PlatformRegistry

    missing = []
    for platform_name in PlatformRegistry.get_available_platforms():
        try:
            adapter_cls = PlatformRegistry.get_adapter_class(platform_name)
        except (ValueError, ImportError):
            continue

        has_explicit = any(
            "driver_isolation_capability" in vars(cls)
            for cls in type.mro(adapter_cls)
            if cls.__name__ not in ("PlatformAdapter", "object")
        )
        if not has_explicit:
            missing.append(f"{platform_name} ({adapter_cls.__name__})")

    assert not missing, (
        f"Adapters missing driver_isolation_capability declaration (own or inherited): "
        f"{', '.join(missing)}. See https://benchbox.dev/docs/development/adding-new-platforms.html for guidance."
    )
