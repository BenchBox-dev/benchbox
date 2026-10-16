from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from benchbox.core.platform_registry import PlatformInfo
from benchbox.core.schemas import DatabaseConfig
from benchbox.utils.path_utils import resolve_benchmark_runs_dir

ConfigBuilder = Callable[[str, dict[str, Any], dict[str, Any], PlatformInfo | None], DatabaseConfig]

CHDB_FILENAME = "clickhouse_local.chdb"


def _databases_dir():
    db_dir = resolve_benchmark_runs_dir() / "databases"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


def _local_chdb_path() -> str:
    return str(_databases_dir() / CHDB_FILENAME)


def _build(
    platform: str,
    options: dict[str, Any],
    overrides: dict[str, Any],
    info: PlatformInfo | None,
    *,
    display_name: str,
    fallback_driver: str | None = None,
    connection_string: str | None = None,
) -> DatabaseConfig:
    auto_install = overrides.get("driver_auto_install")
    if auto_install is None:
        auto_install = options.get("driver_auto_install", False)
    return DatabaseConfig(
        type=platform,
        name=info.display_name if info else display_name,
        connection_string=connection_string,
        options=options,
        driver_package=info.driver_package if info else fallback_driver,
        driver_version=overrides.get("driver_version") or options.get("driver_version"),
        driver_auto_install=bool(auto_install),
    )


def _build_clickhouse_config(platform, options, overrides, info) -> DatabaseConfig:
    if options.get("deployment_mode") == "local" and not options.get("data_path"):
        options["data_path"] = _local_chdb_path()
    return _build(platform, options, overrides, info, display_name="ClickHouse")


def _build_clickhouse_local_config(platform, options, overrides, info) -> DatabaseConfig:
    if not options.get("data_path"):
        options["data_path"] = _local_chdb_path()
    return _build(platform, options, overrides, info, display_name="ClickHouse Local", fallback_driver="chdb")


def _build_sqlite_config(platform, options, overrides, info) -> DatabaseConfig:
    db_path = _databases_dir() / f"{options.get('database_name') or 'benchbox'}.db"
    return _build(platform, options, overrides, info, display_name="SQLite", connection_string=str(db_path))


LOCAL_CONFIG_BUILDERS: dict[str, ConfigBuilder] = {
    "clickhouse": _build_clickhouse_config,
    "clickhouse-local": _build_clickhouse_local_config,
    "clickhouse-server": partial(_build, display_name="ClickHouse Server", fallback_driver="clickhouse-driver"),
    "duckdb": partial(_build, display_name="DuckDB"),
    "sqlite": _build_sqlite_config,
    "sqlite3": _build_sqlite_config,
}
