from __future__ import annotations

import os
from typing import Any

from benchbox.cli.platform_hooks import (
    PlatformHookRegistry,
    PlatformOptionError,
    PlatformOptionSpec,
    parse_bool,
)
from benchbox.core.platform_registry import PlatformInfo, PlatformRegistry
from benchbox.core.schemas import DatabaseConfig
from benchbox.utils.path_utils import resolve_benchmark_runs_dir


def _parse_clickhouse_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"server"}:
        return "server"
    if normalized in {"local"}:
        return "local"
    raise PlatformOptionError(f"Invalid ClickHouse mode '{value}'. Expected 'server' or 'local'.")


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise PlatformOptionError(f"Invalid integer value '{value}'") from exc


_ENABLE_EXPERIMENTAL = os.getenv("BENCHBOX_ENABLE_EXPERIMENTAL", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _register_clickhouse() -> None:
    PlatformHookRegistry.register_option_specs(
        "clickhouse",
        PlatformOptionSpec(
            name="deployment_mode",
            parser=_parse_clickhouse_mode,
            default="server",
            help="Deprecated alias of the platform selector: deployment mode for ClickHouse (server or local).",
            choices=["server", "local"],
            aliases=("mode",),
        ),
        PlatformOptionSpec(
            name="host",
            parser=str,
            default="localhost",
            help="ClickHouse server hostname (server mode only).",
        ),
        PlatformOptionSpec(
            name="port",
            parser=_parse_int,
            default=9000,
            help="ClickHouse native protocol port.",
        ),
        PlatformOptionSpec(
            name="username",
            parser=str,
            default="default",
            help="ClickHouse username for authentication.",
            aliases=("user",),
        ),
        PlatformOptionSpec(
            name="password",
            parser=str,
            default="",
            help="ClickHouse password for authentication.",
        ),
        PlatformOptionSpec(
            name="secure",
            parser=parse_bool,
            default=False,
            help="Enable TLS for ClickHouse connections (server mode).",
        ),
        PlatformOptionSpec(
            name="data_path",
            parser=str,
            default=None,
            help="Data path for ClickHouse local mode (optional).",
        ),
    )

    def _builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: PlatformInfo | None,
    ) -> DatabaseConfig:
        name = info.display_name if info else "ClickHouse"
        if options.get("deployment_mode") == "local" and not options.get("data_path"):
            db_dir = resolve_benchmark_runs_dir() / "databases"
            db_dir.mkdir(parents=True, exist_ok=True)
            options["data_path"] = str(db_dir / "clickhouse_local.chdb")
        driver_package = info.driver_package if info else None
        driver_version = overrides.get("driver_version") or options.get("driver_version")
        auto_install = overrides.get("driver_auto_install")
        if auto_install is None:
            auto_install = options.get("driver_auto_install", False)
        return DatabaseConfig(
            type=platform,
            name=name,
            options=options,
            driver_package=driver_package,
            driver_version=driver_version,
            driver_auto_install=bool(auto_install),
        )

    PlatformHookRegistry.register_config_builder("clickhouse", _builder)


def _register_clickhouse_local() -> None:
    PlatformHookRegistry.register_option_specs(
        "clickhouse-local",
        PlatformOptionSpec(
            name="data_path",
            parser=str,
            default=None,
            help="Path to chDB database directory for persistent local storage (optional).",
        ),
    )

    def _local_builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: PlatformInfo | None,
    ) -> DatabaseConfig:
        name = info.display_name if info else "ClickHouse Local"
        if not options.get("data_path"):
            db_dir = resolve_benchmark_runs_dir() / "databases"
            db_dir.mkdir(parents=True, exist_ok=True)
            options["data_path"] = str(db_dir / "clickhouse_local.chdb")
        driver_package = info.driver_package if info else "chdb"
        driver_version = overrides.get("driver_version") or options.get("driver_version")
        auto_install = overrides.get("driver_auto_install")
        if auto_install is None:
            auto_install = options.get("driver_auto_install", False)
        return DatabaseConfig(
            type=platform,
            name=name,
            options=options,
            driver_package=driver_package,
            driver_version=driver_version,
            driver_auto_install=bool(auto_install),
        )

    PlatformHookRegistry.register_config_builder("clickhouse-local", _local_builder)


def _register_clickhouse_server() -> None:
    PlatformHookRegistry.register_option_specs(
        "clickhouse-server",
        PlatformOptionSpec(
            name="host",
            parser=str,
            default="localhost",
            help="ClickHouse server hostname.",
        ),
        PlatformOptionSpec(
            name="port",
            parser=_parse_int,
            default=9000,
            help="ClickHouse native protocol port.",
        ),
        PlatformOptionSpec(
            name="username",
            parser=str,
            default="default",
            help="ClickHouse username for authentication.",
            aliases=("user",),
        ),
        PlatformOptionSpec(
            name="password",
            parser=str,
            default="",
            help="ClickHouse password for authentication.",
        ),
        PlatformOptionSpec(
            name="secure",
            parser=parse_bool,
            default=False,
            help="Enable TLS for ClickHouse server connections.",
        ),
    )

    def _server_builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: PlatformInfo | None,
    ) -> DatabaseConfig:
        name = info.display_name if info else "ClickHouse Server"
        driver_package = info.driver_package if info else "clickhouse-driver"
        driver_version = overrides.get("driver_version") or options.get("driver_version")
        auto_install = overrides.get("driver_auto_install")
        if auto_install is None:
            auto_install = options.get("driver_auto_install", False)
        return DatabaseConfig(
            type=platform,
            name=name,
            options=options,
            driver_package=driver_package,
            driver_version=driver_version,
            driver_auto_install=bool(auto_install),
        )

    PlatformHookRegistry.register_config_builder("clickhouse-server", _server_builder)


def _register_duckdb() -> None:
    PlatformHookRegistry.register_option_specs(
        "duckdb",
        PlatformOptionSpec(
            name="memory_limit",
            parser=str,
            default="8GB",
            help="DuckDB memory limit (e.g. '4GB').",
        ),
        PlatformOptionSpec(
            name="threads",
            parser=_parse_int,
            default=None,
            help="Thread pool size for DuckDB (None uses auto detection).",
        ),
        PlatformOptionSpec(
            name="temp_directory",
            parser=str,
            default=None,
            help="Temporary directory for DuckDB spill files.",
        ),
    )

    def _builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: PlatformInfo | None,
    ) -> DatabaseConfig:
        name = info.display_name if info else "DuckDB"
        driver_package = info.driver_package if info else None
        driver_version = overrides.get("driver_version") or options.get("driver_version")
        auto_install = overrides.get("driver_auto_install")
        if auto_install is None:
            auto_install = options.get("driver_auto_install", False)
        return DatabaseConfig(
            type=platform,
            name=name,
            options=options,
            driver_package=driver_package,
            driver_version=driver_version,
            driver_auto_install=bool(auto_install),
        )

    PlatformHookRegistry.register_config_builder("duckdb", _builder)


def _register_sqlite() -> None:
    PlatformHookRegistry.register_option_specs(
        "sqlite",
        PlatformOptionSpec(
            name="database_name",
            parser=str,
            default="benchbox",
            help="Base filename for SQLite database files.",
        ),
    )

    def _builder(
        platform: str,
        options: dict[str, Any],
        overrides: dict[str, Any],
        info: PlatformInfo | None,
    ) -> DatabaseConfig:
        db_dir = resolve_benchmark_runs_dir() / "databases"
        db_dir.mkdir(parents=True, exist_ok=True)
        base_name = options.get("database_name") or "benchbox"
        db_path = db_dir / f"{base_name}.db"
        name = info.display_name if info else "SQLite"
        driver_package = info.driver_package if info else None
        driver_version = overrides.get("driver_version") or options.get("driver_version")
        auto_install = overrides.get("driver_auto_install")
        if auto_install is None:
            auto_install = options.get("driver_auto_install", False)
        return DatabaseConfig(
            type=platform,
            name=name,
            connection_string=str(db_path),
            options=options,
            driver_package=driver_package,
            driver_version=driver_version,
            driver_auto_install=bool(auto_install),
        )

    PlatformHookRegistry.register_config_builder("sqlite", _builder)
    PlatformHookRegistry.register_config_builder("sqlite3", _builder)


_register_clickhouse()
_register_clickhouse_local()
_register_clickhouse_server()
_register_duckdb()
_register_sqlite()


def _register_specs_on_all_platforms(*specs: PlatformOptionSpec) -> None:
    metadata = PlatformRegistry.get_all_platform_metadata()
    for platform_name in metadata:
        existing_specs = PlatformHookRegistry.list_option_specs(platform_name)
        missing = [spec for spec in specs if spec.name not in existing_specs]
        if missing:
            PlatformHookRegistry.register_option_specs(platform_name, *missing)


def _register_well_known_options() -> None:
    _register_specs_on_all_platforms(
        PlatformOptionSpec(
            name="driver_version",
            parser=str,
            default=None,
            help="Requested driver package version (e.g. '1.2.0').",
        ),
        PlatformOptionSpec(
            name="driver_auto_install",
            parser=parse_bool,
            default=False,
            help="Automatically install the requested driver version using uv if missing.",
        ),
        PlatformOptionSpec(
            name="plan_max_depth",
            parser=_parse_int,
            default=None,
            help="Maximum query-plan tree depth persisted to the plans companion "
            "and used for the capture-time size estimate.",
        ),
        PlatformOptionSpec(
            name="plan_capture_timeout_seconds",
            parser=_parse_int,
            default=None,
            help="Timeout in seconds for EXPLAIN plan-capture queries.",
        ),
        PlatformOptionSpec(
            name="force_recreate",
            parser=parse_bool,
            default=None,
            help="Drop and recreate the benchmark database before loading, reusing the existing data files "
            "(true or false).",
        ),
    )


_register_well_known_options()

__all__ = [
    "PlatformHookRegistry",
]
