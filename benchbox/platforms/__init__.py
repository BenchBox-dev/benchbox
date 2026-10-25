# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import ast
import importlib
import importlib.util
from collections.abc import Callable, Iterable
from typing import Optional, Type

from benchbox.core.execution_engine import resolve_requested
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.utils.runtime_env import DriverResolution, DriverRuntimeStrategy, ensure_driver_version

from .base import BenchmarkResults, ConnectionConfig, DriverIsolationCapability, PlatformAdapter
from .base.adapter import check_isolation_capability

_lazy_adapter_cache: dict[str, Optional[Type[PlatformAdapter]]] = {}
_lazy_constant_cache: dict[str, bool] = {}
_lazy_adapter_diagnostics: dict[str, dict[str, object]] = {}

_LAZY_ADAPTER_ROWS = """\
DuckDBAdapter|.duckdb
MotherDuckAdapter|.motherduck
DuckLakeAdapter|.ducklake
SQLiteAdapter|.sqlite
PostgreSQLAdapter|.postgresql
TimescaleDBAdapter|.timescaledb
PgDuckDBAdapter|.pg_duckdb
PgMooncakeAdapter|.pg_mooncake
ParadeDBAdapter|.paradedb
CitusAdapter|.citus
CedarDBAdapter|.cedardb
QuestDBAdapter|.questdb
GizmoSQLAdapter|.gizmosql
DatabricksAdapter|.databricks
BigQueryAdapter|.bigquery
SnowflakeAdapter|.snowflake
RedshiftAdapter|.redshift
ClickHouseAdapter|.clickhouse
ClickHouseLocalAdapter|.clickhouse_local
ClickHouseServerAdapter|.clickhouse_server
ClickHouseCloudAdapter|.clickhouse_cloud
TrinoAdapter|.trino
AthenaAdapter|.athena
SparkAdapter|.spark
PySparkSQLAdapter|.pyspark
FireboltAdapter|.firebolt
DatabendAdapter|.databend
InfluxDBAdapter|.influxdb
PrestoAdapter|.presto
AzureSynapseAdapter|.azure_synapse
FabricWarehouseAdapter|.fabric_warehouse
FabricLakehouseAdapter|.fabric_lakehouse
FabricSparkAdapter|.fabric_spark
StarRocksAdapter|.starrocks
SingleStoreAdapter|.singlestore
QuantonAdapter|.onehouse
LakeSailAdapter|.lakesail
VeloxAdapter|.velox
DorisAdapter|.doris
DataFusionAdapter|.datafusion
PolarsAdapter|.polars_platform
PolarsDataFrameAdapter|.dataframe
PandasDataFrameAdapter|.dataframe
CuDFDataFrameAdapter|.dataframe
DaskDataFrameAdapter|.dataframe
DataFusionDataFrameAdapter|.dataframe
PySparkDataFrameAdapter|.dataframe
LakeSailDataFrameAdapter|.dataframe
DataFramePlatformChecker|.dataframe
"""
_LAZY_ADAPTERS = dict(row.split("|", 1) for row in _LAZY_ADAPTER_ROWS.splitlines())

_LAZY_CONSTANT_ROWS = """\
POLARS_AVAILABLE|.dataframe
PANDAS_AVAILABLE|.dataframe
CUDF_AVAILABLE|.dataframe
DASK_AVAILABLE|.dataframe
DATAFUSION_DF_AVAILABLE|.dataframe
PYSPARK_AVAILABLE|.dataframe
"""
_LAZY_CONSTANTS = {
    name: (module_path, False) for name, module_path in (row.split("|", 1) for row in _LAZY_CONSTANT_ROWS.splitlines())
}

_clickhouse_module_cache = None


def _resolve_lazy_module_path(module_path: str) -> str:
    if module_path.startswith(".") and __package__ is not None:
        return importlib.util.resolve_name(module_path, __package__)
    return module_path


def _load_lazy_adapter(name: str) -> Optional[Type[PlatformAdapter]]:
    if name in _lazy_adapter_cache:
        return _lazy_adapter_cache[name]

    module_path = _LAZY_ADAPTERS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    diagnostic_module_path = _resolve_lazy_module_path(module_path)

    try:
        module = importlib.import_module(module_path, __package__)
        adapter_class = getattr(module, name, None)
        _lazy_adapter_cache[name] = adapter_class
        if adapter_class is None:
            _lazy_adapter_diagnostics[name] = {
                "adapter": name,
                "module_path": module_path,
                "status": "broken_adapter_import",
                "error_type": "AttributeError",
                "error_message": f"{module_path} does not expose {name}",
            }
        else:
            _lazy_adapter_diagnostics.pop(name, None)
        return adapter_class
    except (ImportError, OSError) as exc:
        _lazy_adapter_diagnostics[name] = {
            "adapter": name,
            "module_path": module_path,
            "status": PlatformRegistry.classify_optional_import_error(exc, module_path=diagnostic_module_path),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _lazy_adapter_cache[name] = None
        return None


def _load_lazy_constant(name: str) -> bool:
    if name in _lazy_constant_cache:
        return _lazy_constant_cache[name]

    module_path, default = _LAZY_CONSTANTS.get(name, (None, False))
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        module = importlib.import_module(module_path, __package__)
        value = getattr(module, name, default)
        _lazy_constant_cache[name] = value
        return value
    except (ImportError, OSError) as exc:
        _lazy_adapter_diagnostics[name] = {
            "adapter": name,
            "module_path": module_path,
            "status": PlatformRegistry.classify_optional_import_error(
                exc, module_path=_resolve_lazy_module_path(module_path)
            ),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _lazy_constant_cache[name] = default
        return default


def __getattr__(name: str):
    global _clickhouse_module_cache

    if name in _LAZY_ADAPTERS:
        return _load_lazy_adapter(name)

    if name in _LAZY_CONSTANTS:
        return _load_lazy_constant(name)

    if name == "clickhouse":
        if _clickhouse_module_cache is None:
            try:
                _clickhouse_module_cache = importlib.import_module(".clickhouse", __package__)
            except (ImportError, OSError):
                pass
        return _clickhouse_module_cache

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_lazy_adapter_diagnostics() -> dict[str, dict[str, object]]:
    return {name: diagnostic.copy() for name, diagnostic in _lazy_adapter_diagnostics.items()}


def diagnose_optional_adapter_imports(platform_names: Optional[Iterable[str]] = None) -> dict[str, dict[str, object]]:
    return PlatformRegistry.diagnose_optional_adapter_imports(platform_names)


def _load_optional_adapter(module_path: str, class_name: str) -> Optional[Type[PlatformAdapter]]:
    try:
        module = importlib.import_module(module_path, __package__)
    except (ImportError, OSError):
        return None
    return getattr(module, class_name, None)


_EXPORT_NAMES = """\
PlatformAdapter ConnectionConfig BenchmarkResults DuckDBAdapter MotherDuckAdapter DuckLakeAdapter DataFusionAdapter PolarsAdapter
ClickHouseAdapter ClickHouseCloudAdapter DatabricksAdapter BigQueryAdapter RedshiftAdapter SnowflakeAdapter
TrinoAdapter AthenaAdapter SparkAdapter PySparkSQLAdapter FireboltAdapter DatabendAdapter InfluxDBAdapter
PrestoAdapter PostgreSQLAdapter PgDuckDBAdapter PgMooncakeAdapter CedarDBAdapter QuestDBAdapter GizmoSQLAdapter
AzureSynapseAdapter
FabricWarehouseAdapter FabricLakehouseAdapter FabricSparkAdapter StarRocksAdapter SingleStoreAdapter QuantonAdapter
LakeSailAdapter DorisAdapter PolarsDataFrameAdapter PandasDataFrameAdapter CuDFDataFrameAdapter
DaskDataFrameAdapter DataFusionDataFrameAdapter PySparkDataFrameAdapter LakeSailDataFrameAdapter POLARS_AVAILABLE
PANDAS_AVAILABLE CUDF_AVAILABLE DASK_AVAILABLE DATAFUSION_DF_AVAILABLE PYSPARK_AVAILABLE
DataFramePlatformChecker get_adapter is_dataframe_mode get_available_modes get_available_deployments
get_default_deployment get_platform_adapter get_dataframe_adapter list_available_platforms
list_available_dataframe_platforms get_platform_requirements get_dataframe_requirements check_platform_connectivity
is_dataframe_platform diagnose_optional_adapter_imports get_lazy_adapter_diagnostics
"""
__all__ = _EXPORT_NAMES.split()


from benchbox.platforms.adapter_factory import (
    get_adapter,
    get_available_deployments,
    get_available_modes,
    get_default_deployment,
    is_dataframe_mode,
)


def _deployment_selector_config(config: dict) -> tuple[str | None, dict[str, object], set[str] | None]:
    nested = config.get("options")
    nested_options = dict(nested) if isinstance(nested, dict) else {}
    values: dict[str, object] = {**nested_options, **config}
    marker = values.get("_explicit_platform_options")
    explicit = set(marker.keys()) if isinstance(marker, dict) else None
    raw_selector = values.get("_deployment_selector")
    selector = str(raw_selector) if raw_selector is not None else None
    return selector, values, explicit


def _apply_deployment_selector(platform_name: str, config: dict) -> tuple[str, dict]:
    from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS, resolve_deployment

    lowered = platform_name.lower().strip()
    if ":" in lowered:
        from benchbox.platforms.clickhouse.deployment_mode import (
            CLICKHOUSE_LEGACY_SELECTOR_MAP,
            clickhouse_legacy_selector_warning,
        )

        if lowered in CLICKHOUSE_LEGACY_SELECTOR_MAP:
            import warnings

            target = CLICKHOUSE_LEGACY_SELECTOR_MAP[lowered]
            warnings.warn(clickhouse_legacy_selector_warning(platform_name, target), DeprecationWarning, stacklevel=2)
            return target, config
        base, suffix = lowered.rsplit(":", 1)
        if base in DEPLOYMENT_ALIAS_KEYS:
            platform_name = base
            if config.get("_deployment_selector") is None:
                config = {**config, "_deployment_selector": suffix}
            elif str(config["_deployment_selector"]).strip().lower() != suffix:
                raise ValueError(
                    f"Deployment selector '{lowered}' conflicts with "
                    f"explicit deployment '{config['_deployment_selector']}'. "
                    "Use one deployment selection."
                )
    selector, values, explicit = _deployment_selector_config(config)
    base_key = platform_name.lower().strip()
    if base_key not in DEPLOYMENT_ALIAS_KEYS:
        return platform_name, config
    resolution = resolve_deployment(base_key, selector, values, explicit)
    if resolution.explicit and resolution.selected is not None:
        config = {**config, "deployment_mode": resolution.selected}
    return platform_name, config


def get_platform_adapter(platform_name: str, **config) -> PlatformAdapter:
    platform_name, config = _apply_deployment_selector(platform_name, config)
    canonical_name = PlatformRegistry.resolve_platform_name(platform_name)
    resolve_requested(canonical_name, config.get("execution_engine") or "default")

    try:
        adapter_class = PlatformRegistry.get_adapter_class(canonical_name)
    except ValueError:
        available = ", ".join(PlatformRegistry.get_available_platforms())
        raise ValueError(f"Unsupported platform: {platform_name}. Available: {available}") from None

    if adapter_class is None:
        platform_info = PlatformRegistry.get_platform_info(canonical_name)
        install_cmd = platform_info.installation_command if platform_info else "unknown"
        raise ImportError(f"Platform '{platform_name}' is not available. Install required dependencies: {install_cmd}")

    already_resolved = config.get("driver_runtime_strategy") is not None

    driver_package = config.pop("driver_package", None)
    driver_version = config.pop("driver_version", None)
    driver_version_requested = config.pop("driver_version_requested", None)
    driver_version_resolved = config.pop("driver_version_resolved", None)
    driver_version_actual = config.pop("driver_version_actual", None)
    driver_auto_install = bool(config.pop("driver_auto_install", False))
    driver_auto_install_used = bool(config.pop("driver_auto_install_used", False))

    platform_info = PlatformRegistry.get_platform_info(canonical_name)
    install_hint = platform_info.installation_command if platform_info else "unknown"
    package_hint = driver_package or (platform_info.driver_package if platform_info else None)
    explicit_requested_version = driver_version_requested or driver_version
    requested_version = explicit_requested_version

    if already_resolved:
        resolution = DriverResolution(
            package=driver_package or package_hint or "",
            requested=explicit_requested_version,
            resolved=driver_version_resolved,
            actual=driver_version_actual,
            auto_install_used=driver_auto_install_used,
            runtime_strategy=config.get("driver_runtime_strategy"),
            runtime_path=config.get("driver_runtime_path"),
            runtime_python_executable=config.get("driver_runtime_python_executable"),
        )
    else:
        resolution = ensure_driver_version(
            package_name=package_hint,
            requested_version=requested_version,
            auto_install=driver_auto_install,
            install_hint=install_hint,
        )

    check_isolation_capability(adapter_class, canonical_name, resolution.runtime_strategy)

    resolved_version = resolution.resolved or driver_version_resolved
    requested = explicit_requested_version or resolution.requested

    config.setdefault("driver_package", resolution.package or package_hint)
    config.setdefault("driver_version_requested", requested)
    config.setdefault("driver_version_resolved", resolved_version)
    config.setdefault("driver_version_actual", resolution.actual)
    config.setdefault("driver_runtime_strategy", resolution.runtime_strategy)
    config.setdefault("driver_runtime_path", resolution.runtime_path)
    config.setdefault("driver_runtime_python_executable", resolution.runtime_python_executable)
    config.setdefault("driver_auto_install", resolution.auto_install_used or driver_auto_install)
    config.setdefault("driver_auto_install_used", resolution.auto_install_used)

    config["type"] = canonical_name
    if hasattr(adapter_class, "from_config") and callable(adapter_class.from_config):
        adapter_instance = adapter_class.from_config(config)
    else:
        adapter_instance = adapter_class(**config)

    if isinstance(getattr(adapter_instance, "platform_config", None), dict):
        adapter_instance.platform_config.setdefault("type", canonical_name)

    adapter_instance.driver_package = resolution.package or package_hint
    adapter_instance.driver_version_requested = requested
    adapter_instance.driver_version_resolved = resolved_version
    adapter_instance.driver_version_actual = resolution.actual or driver_version_actual
    adapter_instance.driver_runtime_strategy = resolution.runtime_strategy
    adapter_instance.driver_runtime_path = resolution.runtime_path
    adapter_instance.driver_runtime_python_executable = resolution.runtime_python_executable
    adapter_instance.driver_auto_install_used = resolution.auto_install_used

    return adapter_instance


def list_available_platforms() -> dict[str, bool]:
    return PlatformRegistry.get_platform_availability()


def get_platform_requirements(platform_name: str) -> str:
    return PlatformRegistry.get_platform_requirements(platform_name)


def check_platform_connectivity(platform_name: str, **config) -> bool:
    try:
        adapter = get_platform_adapter(platform_name, **config)
        return adapter.test_connection()
    except Exception:
        return False


_DATAFRAME_PLATFORM_ROWS = """\
polars-df|PolarsDataFrameAdapter|POLARS_AVAILABLE|pip install polars (core dependency - should be installed)
pandas-df|PandasDataFrameAdapter|PANDAS_AVAILABLE|pip install pandas  # standalone\\n  uv add benchbox --extra pandas  # inside a project
cudf-df|CuDFDataFrameAdapter|CUDF_AVAILABLE|pip install cudf-cu12 (requires NVIDIA GPU with CUDA)
dask-df|DaskDataFrameAdapter|DASK_AVAILABLE|pip install dask[distributed]  # standalone\\n  uv add benchbox --extra dask  # inside a project
datafusion-df|DataFusionDataFrameAdapter|DATAFUSION_DF_AVAILABLE|pip install datafusion  # standalone\\n  uv add benchbox --extra datafusion  # inside a project
pyspark-df|PySparkDataFrameAdapter|PYSPARK_AVAILABLE|pip install pyspark  # standalone\\n  uv add benchbox --extra pyspark  # inside a project
lakesail-df|LakeSailDataFrameAdapter|PYSPARK_AVAILABLE|pip install pyspark  # standalone (LakeSail Sail uses PySpark Spark Connect client)
"""
_DATAFRAME_PLATFORM_INFO = {
    platform: (adapter, availability, requirements.replace("\\n", "\n"))
    for platform, adapter, availability, requirements in (
        row.split("|", 3) for row in _DATAFRAME_PLATFORM_ROWS.splitlines()
    )
}


def get_dataframe_adapter(platform_name: str, **config):
    platform_lower = platform_name.lower()

    if platform_lower not in _DATAFRAME_PLATFORM_INFO:
        available = ", ".join(sorted(_DATAFRAME_PLATFORM_INFO))
        raise ValueError(f"Unknown DataFrame platform: {platform_name}. Available: {available}")

    adapter_name = _DATAFRAME_PLATFORM_INFO[platform_lower][0]
    adapter_class = _load_lazy_adapter(adapter_name)

    if adapter_class is None:
        requirements = get_dataframe_requirements(platform_lower)
        raise ImportError(
            f"DataFrame platform '{platform_name}' is not available. Install required dependencies: {requirements}"
        )

    return adapter_class(**config)


def list_available_dataframe_platforms(platform_names: Optional[Iterable[str]] = None) -> dict[str, bool]:
    requested = None if platform_names is None else set(platform_names)
    return {
        platform: _load_lazy_constant(info[1])
        for platform, info in _DATAFRAME_PLATFORM_INFO.items()
        if requested is None or platform in requested
    }


def get_dataframe_requirements(platform_name: str) -> str:
    info = _DATAFRAME_PLATFORM_INFO.get(platform_name.lower())
    return info[2] if info is not None else "Unknown DataFrame platform"


def is_dataframe_platform(platform_name: str) -> bool:
    return platform_name.lower() in _DATAFRAME_PLATFORM_INFO


def _make_lazy_config_builder(module_path: str, builder_name: str):
    from typing import Any

    def lazy_builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: Any,
    ):
        module = importlib.import_module(module_path, __package__)
        real_builder = getattr(module, builder_name)
        return real_builder(platform, options, overrides, info)

    return lazy_builder


try:
    from benchbox.core.hooks.platform_hooks import (
        PlatformHookRegistry,
        PlatformOptionError,
        PlatformOptionSpec,
        parse_bool,
    )

    def _spec(name: str, help_text: str, **kwargs) -> PlatformOptionSpec:
        return PlatformOptionSpec(name=name, help=help_text, **kwargs)

    def _register_specs(platform: str, *specs) -> None:
        PlatformHookRegistry.register_option_specs(
            platform,
            *(_spec(spec[0], spec[1], **(spec[2] if len(spec) > 2 else {})) for spec in specs),
        )

    _OPTION_SPEC_ROWS = """\
databricks|uc_catalog|Unity Catalog catalog name for staging data|{}
databricks|uc_schema|Unity Catalog schema name for staging data|{}
databricks|uc_volume|Unity Catalog volume name for staging data|{}
databricks|staging_root|Cloud storage path for staging data (e.g., dbfs:/Volumes/..., s3://..., abfss://...)|{}
databricks|databricks_clustering_strategy|Databricks SQL tuning strategy override (z_order, liquid_clustering, liquid_clustering_auto, none)|{'choices': ('z_order', 'liquid_clustering', 'liquid_clustering_auto', 'none')}
databricks|liquid_clustering_columns|Comma-separated Databricks liquid clustering columns|{}
databricks|disable_result_cache|Disable Databricks serverless result cache during benchmark execution|{'parser': 'parse_bool', 'default': True}
bigquery|staging_root|GCS path for staging data (e.g., gs://bucket/path)|{}
bigquery|storage_bucket|GCS bucket name for data staging (alternative to staging_root)|{}
bigquery|storage_prefix|GCS path prefix within bucket for data staging|{}
bigquery|biglake_connection|BigLake connection for external table modes, as project.region.name|{}
trino|catalog|Trino catalog to use (e.g., hive, iceberg, memory). Auto-discovered if not specified.|{}
trino|staging_root|Cloud storage path for staging data (e.g., s3://..., gs://..., abfss://...)|{}
trino|table_format|Table format for creating tables (memory, hive, iceberg, delta)|{'default': 'memory'}
trino|source_catalog|Source catalog for external data loading (e.g., hive connector)|{}
firebolt|deployment_mode|Deprecated alias of the platform selector: 'core' for local Docker, 'cloud' for managed Firebolt|{'aliases': ('firebolt_mode',), 'choices': ('core', 'cloud')}
firebolt|url|Firebolt Core endpoint URL (default: http://localhost:3473)|{'default': 'http://localhost:3473'}
firebolt|client_id|Firebolt Cloud OAuth client ID|{}
firebolt|client_secret|Firebolt Cloud OAuth client secret|{}
firebolt|account_name|Firebolt Cloud account name|{}
firebolt|api_endpoint|Firebolt Cloud API endpoint|{'default': 'api.app.firebolt.io'}
firebolt|database|Firebolt database name|{}
firebolt|region|Firebolt Cloud region when known|{'aliases': ('cloud_region',)}
firebolt|cloud_provider|Firebolt Cloud provider when known|{}
firebolt|engine_type|Firebolt Cloud engine type when known|{}
firebolt|s3_staging_url|S3 URL for Firebolt Cloud data staging|{}
firebolt|s3_region|AWS region for Firebolt S3 staging|{}
firebolt|disable_result_cache|Disable Firebolt Cloud result cache during benchmark execution|{'parser': 'parse_bool', 'default': True}
firebolt|strict_validation|Fail when Firebolt cache-control validation cannot prove expected state|{'parser': 'parse_bool', 'default': False}
presto|catalog|Presto catalog to use (e.g., hive, memory). Auto-discovered if not specified.|{}
presto|staging_root|Cloud storage path for staging data (e.g., s3://..., gs://...)|{}
presto|table_format|Table format for creating tables (memory, hive)|{'default': 'memory'}
presto|source_catalog|Source catalog for external data loading (e.g., hive connector)|{}
postgresql|host|PostgreSQL server hostname|{'default': 'localhost'}
postgresql|port|PostgreSQL server port|{'parser': 'int', 'default': 5432}
postgresql|database|PostgreSQL database name (auto-generated if not specified)|{}
postgresql|username|PostgreSQL username|{'default': 'postgres'}
postgresql|password|PostgreSQL password|{}
postgresql|schema|PostgreSQL schema name|{'default': 'public'}
postgresql|work_mem|PostgreSQL work_mem setting for queries|{'default': '256MB'}
postgresql|statement_timeout|PostgreSQL per-statement timeout in milliseconds (0 disables)|{'parser': 'int'}
postgresql|enable_timescale|Enable TimescaleDB extensions if available|{'default': 'false'}
timescaledb|deployment_mode|Deprecated alias of the platform selector: 'self-hosted' for a user-managed server, 'cloud' for managed TigerData|{'choices': ('self-hosted', 'cloud')}
timescaledb|host|TimescaleDB server hostname|{'default': 'localhost'}
timescaledb|port|TimescaleDB server port|{'parser': 'int', 'default': 5432}
timescaledb|database|TimescaleDB database name (auto-generated if not specified)|{}
timescaledb|username|TimescaleDB username|{'default': 'postgres'}
timescaledb|password|TimescaleDB password|{}
timescaledb|schema|TimescaleDB schema name|{'default': 'public'}
timescaledb|admin_database|Database used for CREATE/DROP DATABASE operations|{'default': 'postgres'}
timescaledb|sslmode|PostgreSQL SSL mode|{'default': 'prefer'}
timescaledb|work_mem|TimescaleDB work_mem setting for queries|{'default': '256MB'}
timescaledb|maintenance_work_mem|TimescaleDB maintenance_work_mem for VACUUM/CREATE INDEX|{'default': '512MB'}
timescaledb|effective_cache_size|TimescaleDB effective_cache_size planner hint|{'default': '1GB'}
timescaledb|max_parallel_workers_per_gather|TimescaleDB max_parallel_workers_per_gather setting|{'parser': 'int', 'default': 2}
timescaledb|chunk_interval|Chunk time interval for hypertables (e.g., '1 day', '1 week')|{'default': '1 day'}
timescaledb|compression_enabled|Enable compression on hypertables|{'default': 'false'}
timescaledb|compression_after|Compress chunks older than this interval (e.g., '7 days')|{'default': '7 days'}
pg-duckdb|host|PostgreSQL server hostname (with pg_duckdb installed)|{'default': 'localhost'}
pg-duckdb|port|PostgreSQL server port|{'parser': 'int', 'default': 5432}
pg-duckdb|database|PostgreSQL database name (auto-generated if not specified)|{}
pg-duckdb|username|PostgreSQL username|{'default': 'postgres'}
pg-duckdb|password|PostgreSQL password|{}
pg-duckdb|schema|PostgreSQL schema name|{'default': 'public'}
pg-duckdb|admin_database|Database used for CREATE/DROP DATABASE operations|{'default': 'postgres'}
pg-duckdb|sslmode|PostgreSQL SSL mode|{'default': 'prefer'}
pg-duckdb|work_mem|PostgreSQL work_mem setting for queries|{'default': '256MB'}
pg-duckdb|maintenance_work_mem|PostgreSQL maintenance_work_mem for VACUUM/CREATE INDEX|{'default': '512MB'}
pg-duckdb|effective_cache_size|PostgreSQL effective_cache_size planner hint|{'default': '1GB'}
pg-duckdb|max_parallel_workers_per_gather|PostgreSQL max_parallel_workers_per_gather setting|{'parser': 'int', 'default': 2}
pg-duckdb|force_execution|Deprecated alias: true selects duckdb, false selects postgres|{'parser': 'parse_bool'}
pg-duckdb|postgres_scan_threads|Threads for parallel PostgreSQL table scanning (0 = auto)|{'parser': 'int', 'default': 0}
pg-duckdb|compare_native|Run native DuckDB comparison for matched queries|{'parser': 'parse_bool', 'default': False}
pg-duckdb|deployment_mode|Deprecated alias of the platform selector: 'self-hosted' for local PostgreSQL, 'motherduck' for MotherDuck cloud offload|{'choices': ('self-hosted', 'motherduck')}
pg-duckdb|duckdb_db_path|Path to the DuckDB database file pg_duckdb attaches|{}
ducklake|metadata_path|DuckLake catalog metadata file path (.ducklake)|{}
ducklake|data_path|DuckLake Parquet data directory (local path or cloud URI: s3://, gs://, az://)|{}
ducklake|deployment_mode|Deprecated alias of the platform selector: local, local_catalog_s3, postgres_catalog, or postgres_catalog_s3|{'choices': ('local', 'local_catalog_s3', 'postgres_catalog', 'postgres_catalog_s3')}
ducklake|catalog|DuckLake catalog backend: duckdb, sqlite, or postgres|{'choices': ('duckdb', 'sqlite', 'postgres'), 'default': 'duckdb'}
ducklake|pg_host|PostgreSQL hostname for the postgres catalog backend|{'default': 'localhost'}
ducklake|pg_port|PostgreSQL port for the postgres catalog backend|{'parser': 'int', 'default': 5432}
ducklake|pg_database|PostgreSQL database name for the postgres catalog backend (must already exist; DuckLake does not CREATE DATABASE)|{}
ducklake|pg_user|PostgreSQL username for the postgres catalog backend|{'default': 'postgres'}
ducklake|pg_password|PostgreSQL password for the postgres catalog backend|{}
ducklake|s3_key_id|AWS access key ID for S3 DATA_PATH (omit to use the credential_chain provider)|{}
ducklake|s3_secret|AWS secret access key for S3 DATA_PATH|{}
ducklake|s3_region|AWS region for S3 DATA_PATH|{}
ducklake|gcs_key_id|GCS HMAC access ID for gs:// DATA_PATH (both HMAC keys required)|{}
ducklake|gcs_secret|GCS HMAC secret for gs:// DATA_PATH (both HMAC keys required)|{}
ducklake|azure_connection_string|Azure connection string for az:// DATA_PATH (wins over account-name auth)|{}
ducklake|azure_account_name|Azure storage account name for az:// DATA_PATH ambient credential_chain auth|{}
pg-mooncake|host|PostgreSQL server hostname (with pg_mooncake installed)|{'default': 'localhost'}
pg-mooncake|port|PostgreSQL server port|{'parser': 'int', 'default': 5432}
pg-mooncake|database|PostgreSQL database name (auto-generated if not specified)|{}
pg-mooncake|username|PostgreSQL username|{'default': 'postgres'}
pg-mooncake|password|PostgreSQL password|{}
pg-mooncake|schema|PostgreSQL schema name|{'default': 'public'}
pg-mooncake|admin_database|Database used for CREATE/DROP DATABASE operations|{'default': 'postgres'}
pg-mooncake|sslmode|PostgreSQL SSL mode|{'default': 'prefer'}
pg-mooncake|work_mem|PostgreSQL work_mem setting for queries|{'default': '256MB'}
pg-mooncake|maintenance_work_mem|PostgreSQL maintenance_work_mem for VACUUM/CREATE INDEX|{'default': '512MB'}
pg-mooncake|effective_cache_size|PostgreSQL effective_cache_size planner hint|{'default': '1GB'}
pg-mooncake|max_parallel_workers_per_gather|PostgreSQL max_parallel_workers_per_gather setting|{'parser': 'int', 'default': 2}
pg-mooncake|storage_mode|Storage backend: local (disk) or s3 (object storage)|{'choices': ('local', 's3'), 'default': 'local'}
pg-mooncake|mooncake_bucket|S3/GCS bucket URL for columnstore data (required when storage_mode=s3)|{}
paradedb|host|PostgreSQL server hostname (with pg_search installed)|{'default': 'localhost'}
paradedb|port|PostgreSQL server port|{'parser': 'int', 'default': 5432}
paradedb|database|PostgreSQL database name (auto-generated if not specified)|{}
paradedb|username|PostgreSQL username|{'default': 'postgres'}
paradedb|password|PostgreSQL password|{}
paradedb|schema|PostgreSQL schema name|{'default': 'public'}
paradedb|admin_database|Database used for CREATE/DROP DATABASE operations|{'default': 'postgres'}
paradedb|sslmode|PostgreSQL SSL mode|{'default': 'prefer'}
paradedb|work_mem|PostgreSQL work_mem setting for queries|{'default': '256MB'}
paradedb|maintenance_work_mem|PostgreSQL maintenance_work_mem for VACUUM/CREATE INDEX|{'default': '512MB'}
paradedb|effective_cache_size|PostgreSQL effective_cache_size planner hint|{'default': '1GB'}
paradedb|max_parallel_workers_per_gather|PostgreSQL max_parallel_workers_per_gather setting|{'parser': 'int', 'default': 2}
citus|host|Citus coordinator hostname (with citus extension installed)|{'default': 'localhost'}
citus|port|Citus coordinator port|{'parser': 'int', 'default': 5432}
citus|database|PostgreSQL database name (auto-generated if not specified)|{}
citus|username|PostgreSQL username|{'default': 'postgres'}
citus|password|PostgreSQL password|{}
citus|schema|PostgreSQL schema name|{'default': 'public'}
citus|admin_database|Database used for CREATE/DROP DATABASE operations|{'default': 'postgres'}
citus|sslmode|PostgreSQL SSL mode|{'default': 'prefer'}
citus|work_mem|PostgreSQL work_mem setting for queries|{'default': '256MB'}
citus|maintenance_work_mem|PostgreSQL maintenance_work_mem for VACUUM/CREATE INDEX|{'default': '512MB'}
citus|effective_cache_size|PostgreSQL effective_cache_size planner hint|{'default': '1GB'}
citus|max_parallel_workers_per_gather|PostgreSQL max_parallel_workers_per_gather setting|{'parser': 'int', 'default': 2}
citus|distribution_column|Distribute every benchmark table on this column (unset keeps coordinator-local tables)|{}
questdb|host|QuestDB server hostname|{'default': 'localhost'}
questdb|pg_port|QuestDB PostgreSQL wire protocol port|{'default': '8812'}
questdb|http_port|QuestDB REST API HTTP port (BenchBox Docker uses 19000; native default is 9000)|{'default': '9000'}
questdb|ilp_port|QuestDB InfluxDB Line Protocol port|{'default': '9009'}
questdb|username|QuestDB username|{'default': 'admin'}
questdb|password|QuestDB password|{'default': 'quest'}
questdb|database|QuestDB database name|{'default': 'qdb'}
questdb|loading_method|Data loading method: 'rest' (CSV import, default) or 'ilp' (InfluxDB Line Protocol)|{'default': 'rest'}
cedardb|host|CedarDB server hostname|{'default': 'localhost'}
cedardb|port|CedarDB server port|{'default': '5432'}
cedardb|database|CedarDB database name (auto-generated if not specified)|{}
cedardb|username|CedarDB username|{'default': 'postgres'}
cedardb|password|CedarDB password|{}
cedardb|schema|CedarDB schema name|{'default': 'public'}
gizmosql|host|GizmoSQL server hostname|{'default': 'localhost'}
gizmosql|port|GizmoSQL Flight SQL port|{'parser': 'int', 'default': 31337}
gizmosql|username|GizmoSQL username (or set GIZMOSQL_USERNAME)|{}
gizmosql|password|GizmoSQL password (or set GIZMOSQL_PASSWORD)|{}
gizmosql|auth_token|GizmoSQL bearer token used instead of a password (or set GIZMOSQL_AUTH_TOKEN)|{}
gizmosql|catalog|Attached DuckDB catalog to use; it must already be attached on the server|{}
gizmosql|schema|Schema holding the benchmark tables (default: one schema per benchmark and scale factor)|{}
gizmosql|tls|Connect with TLS (disable only for local development with allow_insecure_connection)|{'parser': 'parse_bool', 'default': True}
gizmosql|tls_skip_verify|Skip TLS certificate verification (development only; needs allow_insecure_connection)|{'parser': 'parse_bool', 'default': False}
gizmosql|tls_ca_cert|Path to a PEM CA bundle used to verify the server certificate|{}
gizmosql|allow_insecure_connection|Permit plaintext or unverified TLS connections for local development|{'parser': 'parse_bool', 'default': False}
gizmosql|autocommit|Run the ADBC session in autocommit mode|{'parser': 'parse_bool', 'default': True}
gizmosql|query_timeout|Per-query timeout in seconds; the query is cancelled on the server (0 disables)|{'parser': 'int', 'default': 0}
gizmosql|ingest_batch_bytes|Target size in bytes of each Arrow ingest batch (below the 16 MiB Flight message limit)|{'parser': 'int', 'default': 8388608}
gizmosql|session_settings|Session-scoped DuckDB settings as name=value pairs separated by commas; server-wide settings are rejected|{}
gizmosql|allow_unverified_reuse|Query preloaded tables on a read-only server without comparing row counts to local source files|{'parser': 'parse_bool', 'default': False}
synapse|server|Azure Synapse server endpoint (e.g., myworkspace.sql.azuresynapse.net)|{}
synapse|database|Azure Synapse database name (auto-generated if not specified)|{}
synapse|username|Azure Synapse username|{}
synapse|password|Azure Synapse password|{}
synapse|auth_method|Authentication method: sql, aad_password, or aad_msi|{'default': 'sql'}
synapse|storage_account|Azure storage account for data staging|{}
synapse|container|Azure blob container name|{}
synapse|storage_sas_token|SAS token for Azure storage access|{}
synapse|resource_class|Workload resource class (e.g., staticrc20, staticrc30)|{'default': 'staticrc20'}
synapse|staging_root|Azure staging path, including the storage account (e.g., abfss://container@account.dfs.core.windows.net/path)|{}
fabric_dw|server|Fabric warehouse endpoint (e.g., workspace-guid.datawarehouse.fabric.microsoft.com)|{}
fabric_dw|workspace|Fabric workspace name or GUID|{}
fabric_dw|database|Database/warehouse name|{'aliases': ('warehouse',)}
fabric_dw|auth_method|Authentication method: service_principal, default_credential, or interactive|{'default': 'default_credential'}
fabric_dw|tenant_id|Azure tenant ID for service principal auth|{}
fabric_dw|client_id|Service principal client ID|{}
fabric_dw|client_secret|Service principal client secret|{}
fabric_dw|staging_path|OneLake staging path for data loading|{'default': 'benchbox-staging'}
quanton|api_key|Onehouse API key (or set ONEHOUSE_API_KEY env var)|{}
quanton|s3_staging_dir|S3 path for data staging (e.g., s3://bucket/path)|{}
quanton|region|AWS region for cluster deployment|{'default': 'us-east-1'}
quanton|database|Database name for benchmarks|{'default': 'benchbox'}
quanton|table_format|Table format: iceberg, hudi, or delta|{'choices': ('iceberg', 'hudi', 'delta'), 'default': 'iceberg'}
clickhouse-cloud|host|ClickHouse Cloud hostname (e.g., abc123.us-east-2.aws.clickhouse.cloud)|{}
clickhouse-cloud|password|ClickHouse Cloud password (or set CLICKHOUSE_CLOUD_PASSWORD env var)|{}
clickhouse-cloud|username|Username (default: 'default')|{'default': 'default'}
clickhouse-cloud|database|Database name|{'default': 'default'}
clickhouse-cloud|region|ClickHouse Cloud service region when it cannot be inferred from the host|{}
clickhouse-cloud|cloud_provider|ClickHouse Cloud provider when it cannot be inferred from the host|{}
clickhouse-cloud|service_name|ClickHouse Cloud service display name for result metadata|{}
clickhouse-cloud|service_tier|ClickHouse Cloud service tier for result metadata|{}
clickhouse-cloud|oauth_token|OAuth token for keyless authentication (alternative to password)|{}
clickhouse-cloud|s3_staging_url|S3 URL for bulk data loading (e.g., s3://my-bucket/benchbox-staging/)|{}
clickhouse-cloud|s3_region|AWS region for the S3 staging bucket|{'default': 'us-east-1'}
clickhouse-cloud|gcs_staging_url|GCS URL for bulk data loading (e.g., gs://my-bucket/benchbox-staging/)|{}
clickhouse-local|optimize_after_load|Run OPTIMIZE TABLE FINAL on each table after it loads, on tuned runs only; off by default, and its time is reported separately from load time|{'parser': 'parse_bool', 'default': False}
clickhouse-server|optimize_after_load|Run OPTIMIZE TABLE FINAL on each table after it loads, on tuned runs only; off by default, and its time is reported separately from load time|{'parser': 'parse_bool', 'default': False}
clickhouse-cloud|optimize_after_load|Run OPTIMIZE TABLE FINAL on each table after it loads, on tuned runs only; off by default, and its time is reported separately from load time|{'parser': 'parse_bool', 'default': False}
starrocks|host|StarRocks FE hostname|{'default': 'localhost'}
starrocks|port|StarRocks FE MySQL protocol port|{'default': '9030'}
starrocks|username|StarRocks username|{'default': 'root'}
starrocks|password|StarRocks password|{}
starrocks|database|StarRocks database name (auto-generated if not specified)|{}
starrocks|http_port|StarRocks BE HTTP port for Stream Load|{'default': '8040'}
databend|deployment_mode|Deprecated alias of the platform selector: 'cloud' for Databend Cloud, 'self-hosted' for a user-managed cluster|{'choices': ('cloud', 'self-hosted')}
databend|host|Databend host (or set DATABEND_HOST env var)|{}
databend|port|Databend port (default: 443 for cloud, 8000 for self-hosted)|{}
databend|username|Databend username (default: benchbox)|{'default': 'benchbox'}
databend|password|Databend password (or set DATABEND_PASSWORD env var)|{}
databend|database|Database name (default: benchbox)|{'default': 'benchbox'}
databend|dsn|Full Databend DSN (overrides individual connection params)|{}
databend|ssl|Enable SSL/TLS for Databend connections (default: enabled for cloud, disabled for self-hosted)|{'parser': 'parse_bool'}
databend|disable_result_cache|Disable Databend query result cache during benchmark execution|{'parser': 'parse_bool', 'default': True}
doris|host|Doris FE node hostname|{'default': 'localhost'}
doris|port|Doris MySQL protocol port|{'parser': 'int', 'default': 9030}
doris|http_port|Doris Stream Load HTTP port (FE)|{'parser': 'int', 'default': 8030}
doris|be_http_port|Doris BE HTTP port for stream load redirects|{'parser': 'int', 'default': 8040}
doris|database|Doris database name (auto-generated if not specified)|{}
doris|username|Doris username|{'default': 'root'}
doris|password|Doris password|{}
singlestore|host|SingleStore server hostname or Helios endpoint|{'default': 'localhost'}
singlestore|port|SingleStore MySQL protocol port|{'default': '3306'}
singlestore|database|SingleStore database name (auto-generated if not specified)|{}
singlestore|username|SingleStore username|{'default': 'root'}
singlestore|password|SingleStore password|{}
polars|streaming|Enable streaming mode for large datasets|{'parser': 'parse_bool', 'default': 'false'}
polars|rechunk|Rechunk data for better memory layout|{'parser': 'parse_bool', 'default': 'true'}
polars|n_rows|Limit number of rows to read (for testing)|{'parser': 'int'}
pandas|dtype_backend|Backend for nullable dtypes|{'choices': ('numpy', 'numpy_nullable', 'pyarrow'), 'default': 'numpy_nullable'}
cudf|device_id|CUDA device ID to use|{'parser': 'int', 'default': '0'}
cudf|spill_to_host|Enable GPU memory spilling to host RAM|{'parser': 'parse_bool', 'default': 'true'}
dask|n_workers|Number of worker processes|{'parser': 'int'}
dask|threads_per_worker|Threads per worker process|{'parser': 'int'}
dask|use_distributed|Deprecated alias: true selects distributed, false selects threaded|{'parser': 'parse_bool'}
dask|scheduler_address|Connect to existing scheduler (e.g., 'tcp://...')|{}
dask|memory_limit|Memory limit per local Dask worker (e.g., '4GB')|{}
dask|spill_directory|Directory for Dask spill files; explicit directories are not deleted by close()|{}
datafusion|target_partitions|Number of target partitions for parallelism (default: CPU count)|{'parser': 'int'}
datafusion|repartition_joins|Enable automatic repartitioning for joins|{'parser': 'parse_bool', 'default': 'true'}
datafusion|parquet_pushdown|Enable predicate/projection pushdown for Parquet files|{'parser': 'parse_bool', 'default': 'true'}
datafusion|batch_size|Batch size for query execution|{'parser': 'int', 'default': '8192'}
datafusion|memory_limit|Memory limit for fair spill pool (e.g., '8G', '16GB')|{}
datafusion|temp_dir|Temporary directory for disk spilling (default: system temp)|{}
clickhouse|deployment_mode|Deprecated alias of the platform selector: deployment mode for ClickHouse (server or local).|{'parser': 'clickhouse_mode', 'default': 'server', 'choices': ('server', 'local'), 'aliases': ('mode',)}
clickhouse|host|ClickHouse server hostname (server mode only).|{'default': 'localhost'}
clickhouse|port|ClickHouse native protocol port.|{'parser': 'int', 'default': 9000}
clickhouse|username|ClickHouse username for authentication.|{'default': 'default', 'aliases': ('user',)}
clickhouse|password|ClickHouse password for authentication.|{'default': ''}
clickhouse|secure|Enable TLS for ClickHouse connections (server mode).|{'parser': 'parse_bool', 'default': False}
clickhouse|data_path|Data path for ClickHouse local mode (optional).|{}
clickhouse-local|data_path|Path to chDB database directory for persistent local storage (optional).|{}
clickhouse-server|host|ClickHouse server hostname.|{'default': 'localhost'}
clickhouse-server|port|ClickHouse native protocol port.|{'parser': 'int', 'default': 9000}
clickhouse-server|username|ClickHouse username for authentication.|{'default': 'default', 'aliases': ('user',)}
clickhouse-server|password|ClickHouse password for authentication.|{'default': ''}
clickhouse-server|secure|Enable TLS for ClickHouse server connections.|{'parser': 'parse_bool', 'default': False}
duckdb|memory_limit|DuckDB memory limit (e.g. '4GB').|{'default': '8GB'}
duckdb|threads|Thread pool size for DuckDB (None uses auto detection).|{'parser': 'int'}
duckdb|temp_directory|Temporary directory for DuckDB spill files.|{}
sqlite|database_name|Base filename for SQLite database files.|{'default': 'benchbox'}
sqlite|database_path|Path to the SQLite database file (auto-generated from --benchmark/--scale when omitted)|{}
sqlite|timeout|SQLite connection timeout in seconds|{'parser': 'float', 'default': '30.0'}
sqlite|check_same_thread|Enforce that connections are used on the creating thread only|{'parser': 'parse_bool', 'default': 'false'}
spark|adaptive_enabled|Enable or disable Spark Adaptive Query Execution (AQE)|{'parser': 'parse_bool'}
synapse-spark|adaptive_enabled|Enable or disable Synapse Spark Adaptive Query Execution (AQE)|{'parser': 'parse_bool', 'default': 'true'}
fabric-spark|adaptive_enabled|Enable or disable Fabric Spark Adaptive Query Execution (AQE)|{'parser': 'parse_bool', 'default': 'true'}
spark|java_home|Path to the JDK Spark should run under|{}
spark|driver_memory|Spark driver JVM heap, e.g. 4g or 8g (default 4g)|{}
pyspark|driver_memory|PySpark driver JVM heap, e.g. 4g or 8g (default 4g)|{}
pyspark|shuffle_partitions|Number of PySpark shuffle partitions|{'parser': 'int'}
athena|aws_profile|Named AWS CLI profile to authenticate with|{}
athena|s3_bucket|S3 bucket for query results and data staging|{}
athena|s3_staging_dir|S3 URI Athena writes query results to (e.g., s3://bucket/path)|{}
athena|staging_root|S3 path for staging data (alternative to s3_bucket)|{}
athena-spark|s3_staging_dir|S3 path for data staging (e.g., s3://bucket/path)|{}
athena-spark|region|AWS region (default: us-east-1)|{}
athena-spark|database|Glue Data Catalog database (default: benchbox)|{}
athena-spark|runtime_version|Spark engine version expected on the workgroup, recorded in results as requested; the workgroup sets the engine version|{'aliases': ('engine_version',)}
athena-spark|coordinator_dpu_size|Coordinator DPU size (default: 1)|{'parser': 'int'}
athena-spark|max_concurrent_dpus|Maximum concurrent DPUs (default: 20)|{'parser': 'int'}
athena-spark|default_executor_dpu_size|Default executor DPU size (default: 1)|{'parser': 'int'}
athena-spark|session_idle_timeout_minutes|Session idle timeout in minutes (default: 15)|{'parser': 'int'}
athena-spark|timeout_minutes|Calculation timeout in minutes (default: 60)|{'parser': 'int'}
motherduck|database|MotherDuck database name|{}
redshift|iam_role|IAM role ARN Redshift COPY assumes to read from S3|{}
redshift|s3_bucket|S3 bucket for data staging|{}
redshift|staging_root|S3 path for staging data (e.g., s3://bucket/path)|{}
snowflake|staging_root|Cloud storage path for staging data|{}
snowflake|iceberg_external_volume|Snowflake EXTERNAL VOLUME name for Iceberg tables|{}
influxdb|deployment_mode|Deprecated alias of the platform selector: 'core' for self-hosted OSS, 'cloud' for the managed service|{'choices': ('core', 'cloud'), 'default': 'cloud', 'aliases': ('mode',)}
influxdb|host|InfluxDB server hostname|{'default': 'localhost'}
influxdb|port|InfluxDB server port|{'default': '8086'}
influxdb|token|InfluxDB authentication token (or set INFLUXDB_TOKEN env var)|{}
influxdb|org|InfluxDB organization name|{}
influxdb|database|InfluxDB database (bucket) name|{'default': 'benchbox'}
influxdb|ssl|Use SSL/TLS connection (true/false)|{'default': 'true'}
lakesail|endpoint|Spark Connect server endpoint (for example, sc://localhost:50051)|{'default': 'sc://localhost:50051'}
lakesail|adaptive_enabled|Enable or disable LakeSail Adaptive Query Execution (AQE)|{'parser': 'parse_bool'}
lakesail|deployment_mode|Deprecated alias of the platform selector: 'local' for single-node, 'distributed' to connect to an external Sail server without starting one|{'choices': ('local', 'distributed'), 'aliases': ('sail_mode',)}
velox|deployment|Deprecated alias of the platform selector: 'local' (in-process SparkSession, Linux only) or 'remote' (Spark-Connect server)|{'choices': ('local', 'remote'), 'default': 'local'}
velox|endpoint|Spark-Connect endpoint for remote mode (e.g., sc://localhost:50051)|{'default': 'sc://localhost:50051'}
velox|gluten_jar_path|Absolute path to the Gluten Velox bundle jar (required for local mode)|{'aliases': ('jar',)}
velox|offheap_size|Off-heap memory for Velox native engine (e.g., '8g', '16g')|{'default': '8g'}
velox|driver_memory|Spark driver JVM heap memory (e.g., '4g')|{'default': '4g'}
velox|shuffle_partitions|Number of shuffle partitions|{'parser': 'int'}
velox|adaptive_enabled|Enable Spark Adaptive Query Execution|{'parser': 'parse_bool'}
velox|table_format|Lakehouse table format for accelerated reads (parquet, orc, delta, iceberg, hudi)|{'choices': ('parquet', 'orc', 'delta', 'iceberg', 'hudi'), 'default': 'parquet'}
velox|lakehouse_jars|Comma-separated connector jars for delta/iceberg/hudi reads (local paths, URIs, or Maven coordinates; required for lakehouse formats in local mode)|{}
"""

    def _numeric_parser(kind: type, label: str) -> Callable[[str], object]:
        def parse(value: str) -> object:
            try:
                return kind(value)
            except ValueError as exc:
                raise PlatformOptionError(f"Invalid {label} value '{value}'") from exc

        return parse

    def _parse_clickhouse_mode(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"server", "local"}:
            return normalized
        raise PlatformOptionError(f"Invalid ClickHouse mode '{value}'. Expected 'server' or 'local'.")

    _SPEC_PARSERS = {
        "int": _numeric_parser(int, "integer"),
        "float": _numeric_parser(float, "number"),
        "parse_bool": parse_bool,
        "clickhouse_mode": _parse_clickhouse_mode,
    }

    def _spec_kwargs(text: str) -> dict[str, object]:
        kwargs = ast.literal_eval(text)
        parser = kwargs.get("parser")
        if isinstance(parser, str):
            kwargs["parser"] = _SPEC_PARSERS[parser]
        return kwargs

    def _register_spec_rows(rows: str) -> None:
        for row in rows.splitlines():
            platform, name, help_text, kwargs_text = row.split("|", 3)
            _register_specs(platform, (name, help_text, _spec_kwargs(kwargs_text)))

    _register_spec_rows(_OPTION_SPEC_ROWS)

    _WELL_KNOWN_SPEC_ROWS = """\
driver_version|Requested driver package version (e.g. '1.2.0').|{}
driver_auto_install|Automatically install the requested driver version using uv if missing.|{'parser': 'parse_bool', 'default': False}
plan_max_depth|Maximum query-plan tree depth persisted to the plans companion and used for the capture-time size estimate.|{'parser': 'int'}
plan_capture_timeout_seconds|Timeout in seconds for EXPLAIN plan-capture queries.|{'parser': 'int'}
force_recreate|Drop and recreate the benchmark database before loading, reusing the existing data files (true or false).|{'parser': 'parse_bool'}
"""

    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    for _entry in PLATFORM_MANIFEST:
        _register_spec_rows("\n".join(f"{_entry.key}|{row}" for row in _WELL_KNOWN_SPEC_ROWS.splitlines()))

    from benchbox.platforms.config_builders import LOCAL_CONFIG_BUILDERS

    for _builder_platform, _builder in LOCAL_CONFIG_BUILDERS.items():
        PlatformHookRegistry.register_config_builder(_builder_platform, _builder)

    from benchbox.core.compute_resource import register_compute_specs
    from benchbox.core.gateway import register_gateway_connection_specs

    register_compute_specs()
    register_gateway_connection_specs()

    PlatformHookRegistry.register_config_builder(
        "motherduck", _make_lazy_config_builder(".motherduck", "_build_motherduck_config")
    )

    PlatformHookRegistry.register_config_builder(
        "databricks", _make_lazy_config_builder(".databricks", "_build_databricks_config")
    )

    PlatformHookRegistry.register_config_builder(
        "bigquery", _make_lazy_config_builder(".bigquery", "_build_bigquery_config")
    )

    PlatformHookRegistry.register_config_builder("trino", _make_lazy_config_builder(".trino", "_build_trino_config"))

    PlatformHookRegistry.register_config_builder(
        "firebolt", _make_lazy_config_builder(".firebolt", "_build_firebolt_config")
    )

    PlatformHookRegistry.register_config_builder("presto", _make_lazy_config_builder(".presto", "_build_presto_config"))

    PlatformHookRegistry.register_config_builder(
        "postgresql", _make_lazy_config_builder(".postgresql", "_build_postgresql_config")
    )
    PlatformHookRegistry.register_config_builder(
        "timescaledb", _make_lazy_config_builder(".timescaledb", "_build_timescaledb_config")
    )
    PlatformHookRegistry.register_config_builder(
        "pg-duckdb", _make_lazy_config_builder(".pg_duckdb", "_build_pg_duckdb_config")
    )
    PlatformHookRegistry.register_config_builder(
        "pg-mooncake", _make_lazy_config_builder(".pg_mooncake", "_build_pg_mooncake_config")
    )
    PlatformHookRegistry.register_config_builder(
        "paradedb", _make_lazy_config_builder(".paradedb", "_build_paradedb_config")
    )
    PlatformHookRegistry.register_config_builder("citus", _make_lazy_config_builder(".citus", "_build_citus_config"))
    PlatformHookRegistry.register_config_builder(
        "questdb", _make_lazy_config_builder(".questdb", "_build_questdb_config")
    )
    PlatformHookRegistry.register_config_builder(
        "cedardb", _make_lazy_config_builder(".cedardb", "_build_cedardb_config")
    )
    PlatformHookRegistry.register_config_builder(
        "gizmosql", _make_lazy_config_builder(".gizmosql", "_build_gizmosql_config")
    )

    PlatformHookRegistry.register_config_builder(
        "synapse", _make_lazy_config_builder(".azure_synapse", "_build_synapse_config")
    )

    PlatformHookRegistry.register_config_builder(
        "quanton", _make_lazy_config_builder(".onehouse", "_build_quanton_config")
    )

    PlatformHookRegistry.register_config_builder(
        "athena-spark", _make_lazy_config_builder(".aws.athena_spark_adapter", "_build_athena_spark_config")
    )

    PlatformHookRegistry.register_config_builder(
        "clickhouse-cloud", _make_lazy_config_builder(".clickhouse_cloud", "_build_clickhouse_cloud_config")
    )

    PlatformHookRegistry.register_config_builder(
        "starrocks", _make_lazy_config_builder(".starrocks.setup", "_build_starrocks_config")
    )

    PlatformHookRegistry.register_config_builder(
        "databend", _make_lazy_config_builder(".databend", "_build_databend_config")
    )

    PlatformHookRegistry.register_config_builder("doris", _make_lazy_config_builder(".doris", "_build_doris_config"))

    PlatformHookRegistry.register_config_builder(
        "singlestore", _make_lazy_config_builder(".singlestore", "_build_singlestore_config")
    )


except ImportError:
    pass
