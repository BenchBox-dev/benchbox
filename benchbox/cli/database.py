# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from rich.prompt import Prompt
from rich.table import Table

import benchbox.cli.platform_defaults as _platform_defaults  # noqa: F401
from benchbox.core.databases.manager import check_connection as core_check_connection
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.schemas import DatabaseConfig, SystemProfile
from benchbox.utils.printing import quiet_console
from benchbox.utils.runtime_env import ensure_driver_version
from benchbox.utils.verbosity import VerbositySettings

from .platform import get_platform_manager
from .platform_hooks import PlatformHookRegistry, PlatformOptionError

console = quiet_console
logger = logging.getLogger(__name__)


LOCAL_CATEGORIES = {"analytical", "embedded", "distributed", "relational", "timeseries", "dataframe"}
CLOUD_CATEGORIES = {"cloud"}
_DRIVER_OPTION_FIELDS = (
    "driver_version",
    "driver_version_resolved",
    "driver_version_actual",
    "driver_runtime_strategy",
    "driver_runtime_path",
    "driver_runtime_python_executable",
)


@dataclass
class ExecutionStyleFilter:
    execution_mode: Literal["sql", "dataframe", "all"] = "all"
    location: Literal["local", "cloud", "all"] = "all"


def _sync_driver_options(config: DatabaseConfig, driver_package: str) -> None:
    if config.options is None:
        config.options = {}

    config.options["driver_package"] = driver_package
    for field in _DRIVER_OPTION_FIELDS:
        value = getattr(config, field)
        if value:
            config.options[field] = value
    config.options["driver_auto_install"] = config.driver_auto_install
    config.options["driver_auto_install_used"] = config.driver_auto_install_used


def _sync_platform_driver_info(platform_info: Any, config: DatabaseConfig) -> None:
    if platform_info is None:
        return
    platform_info.driver_version_requested = config.driver_version
    platform_info.driver_version_resolved = config.driver_version_resolved
    platform_info.driver_version_actual = config.driver_version_actual
    platform_info.driver_runtime_strategy = config.driver_runtime_strategy


class DatabaseManager:
    def __init__(self):
        self.console = quiet_console
        self.available_databases = self._detect_databases()
        self.platform_manager = get_platform_manager()
        self.verbosity = VerbositySettings.default()

    def set_verbosity(self, settings: VerbositySettings) -> None:

        self.verbosity = settings

    def _detect_databases(self) -> dict[str, dict[str, Any]]:
        from benchbox.core.platform_registry import PlatformRegistry

        logger.debug("Starting database detection")

        databases = {}
        available_platforms = PlatformRegistry.get_available_platforms()

        for platform_name in available_platforms:
            platform_info = PlatformRegistry.get_platform_info(platform_name)
            if platform_info and platform_info.available:
                version_parts = []
                for lib in platform_info.libraries:
                    if lib.installed and lib.version:
                        version_parts.append(f"{lib.name} {lib.version}")

                version_str = " + ".join(version_parts) if version_parts else "Available"

                databases[platform_name] = {
                    "name": platform_info.display_name,
                    "version": version_str,
                    "description": platform_info.description,
                    "adoption": platform_info.adoption,
                    "supports_olap": "olap" in platform_info.supports
                    or platform_info.category in ["analytical", "cloud"],
                }

                if platform_name in {"clickhouse", "clickhouse-local", "clickhouse-server"}:
                    has_server = any(
                        lib.name == "clickhouse_driver" and lib.installed for lib in platform_info.libraries
                    )
                    has_embedded = any(lib.name == "chdb" and lib.installed for lib in platform_info.libraries)

                    databases[platform_name].update(
                        {
                            "supports_server": has_server,
                            "supports_embedded": has_embedded,
                        }
                    )

        logger.debug(f"Database detection completed: {list(databases.keys())}")
        return databases

    def prompt_execution_style(self) -> ExecutionStyleFilter:
        console.print("\n[bold cyan]Execution Style[/bold cyan]")
        console.print("[dim]Choose how you want to run benchmarks to narrow down platform options.[/dim]\n")

        console.print("[bold]Execution Mode:[/bold]")
        console.print("  1. SQL [dim](Traditional SQL queries - most platforms)[/dim] (Recommended)")
        console.print("  2. DataFrame [dim](Pandas-like operations - Polars, PySpark, etc.)[/dim]")
        console.print("  3. All [dim](Show all platforms)[/dim]")

        mode_choice = Prompt.ask("Select execution mode", choices=["1", "2", "3"], default="1")
        execution_mode: Literal["sql", "dataframe", "all"]
        if mode_choice == "1":
            execution_mode = "sql"
        elif mode_choice == "2":
            execution_mode = "dataframe"
        else:
            execution_mode = "all"

        console.print("\n[bold]Platform Location:[/bold]")
        console.print("  1. Local [dim](DuckDB, SQLite, Polars - runs on your machine)[/dim]")
        console.print("  2. Cloud [dim](BigQuery, Snowflake, Databricks - requires credentials)[/dim]")
        console.print("  3. All [dim](Show all platforms)[/dim] (Recommended)")

        location_choice = Prompt.ask("Select platform location", choices=["1", "2", "3"], default="3")
        location: Literal["local", "cloud", "all"]
        if location_choice == "1":
            location = "local"
        elif location_choice == "2":
            location = "cloud"
        else:
            location = "all"

        mode_display = {"sql": "SQL", "dataframe": "DataFrame", "all": "All modes"}[execution_mode]
        location_display = {"local": "Local", "cloud": "Cloud", "all": "All locations"}[location]
        console.print(f"\n[green]✓ Filter: {mode_display} + {location_display}[/green]")

        return ExecutionStyleFilter(execution_mode=execution_mode, location=location)

    def filter_platforms(self, platforms: list[str], style_filter: ExecutionStyleFilter) -> list[str]:
        if style_filter.execution_mode == "all" and style_filter.location == "all":
            return platforms

        filtered = []
        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform in platforms:
            platform_meta = metadata.get(platform, {})
            caps = platform_meta.get("capabilities", {})
            category = platform_meta.get("category", "")

            mode_ok = True
            if style_filter.execution_mode != "all":
                if style_filter.execution_mode == "sql":
                    mode_ok = caps.get("supports_sql", False)
                elif style_filter.execution_mode == "dataframe":
                    mode_ok = caps.get("supports_dataframe", False)

            location_ok = True
            if style_filter.location != "all":
                if style_filter.location == "local":
                    location_ok = category in LOCAL_CATEGORIES
                elif style_filter.location == "cloud":
                    location_ok = category in CLOUD_CATEGORIES

            if mode_ok and location_ok:
                filtered.append(platform)

        return filtered

    def _get_additional_matching_platforms(
        self, enabled_platforms: list[str], style_filter: ExecutionStyleFilter
    ) -> list[str]:
        all_platforms = PlatformRegistry.get_platform_names()

        all_matching = self.filter_platforms(all_platforms, style_filter)

        enabled_set = set(enabled_platforms)
        additional = [p for p in all_matching if p not in enabled_set]

        return additional

    def select_database(self, style_filter: ExecutionStyleFilter | None = None) -> DatabaseConfig:
        available_platforms = self.platform_manager.get_enabled_platforms()
        if not available_platforms:
            console.print("[red]❌ No database platforms are enabled![/red]")
            console.print("\nTo set up database platforms:")
            console.print("• [cyan]benchbox platforms setup[/cyan] (interactive setup)")
            console.print("• [cyan]benchbox platforms list[/cyan] (see all platforms)")
            console.print("• [cyan]benchbox platforms enable duckdb[/cyan] (enable specific platform)")
            raise RuntimeError("No platforms enabled")

        if style_filter is not None:
            available_platforms = self.filter_platforms(available_platforms, style_filter)
            if not available_platforms:
                console.print("[yellow]⚠️ No platforms match your filter criteria.[/yellow]")
                console.print("[dim]Showing all available platforms instead.[/dim]\n")
                available_platforms = self.platform_manager.get_enabled_platforms()

        self._display_platform_table(available_platforms)

        if style_filter is not None:
            additional_platforms = self._get_additional_matching_platforms(available_platforms, style_filter)
            if additional_platforms:
                platform_names = ", ".join(sorted(additional_platforms)[:5])
                more_count = len(additional_platforms) - 5 if len(additional_platforms) > 5 else 0
                more_text = f" (+{more_count} more)" if more_count > 0 else ""

                console.print(f"\n[dim]💡 Additional platforms matching your filter: {platform_names}{more_text}[/dim]")
                console.print("[dim]   Run [cyan]benchbox platforms enable <name>[/cyan] to add them.[/dim]")

        choice_map = {str(i + 1): platform for i, platform in enumerate(available_platforms)}

        if "duckdb" in available_platforms:
            duckdb_idx = available_platforms.index("duckdb") + 1
            console.print(f"\n[green]💡 Recommended:[/green] DuckDB (choice {duckdb_idx}) - Best for getting started")

        selection = Prompt.ask(
            "Select platform by ID",
            choices=list(choice_map.keys()),
            default="1",
        )
        selected_platform = choice_map[selection]

        platforms_info = self.platform_manager.detect_platforms()
        platform_info = platforms_info[selected_platform]

        execution_mode: str | None = None
        caps = PlatformRegistry.get_platform_capabilities(selected_platform)

        if style_filter is not None and style_filter.execution_mode in ("sql", "dataframe"):
            execution_mode = style_filter.execution_mode
        elif caps and caps.supports_sql and caps.supports_dataframe:
            execution_mode = self._prompt_execution_mode(selected_platform, caps.default_mode)
        elif caps:
            execution_mode = caps.default_mode

        config = DatabaseConfig(
            type=selected_platform,
            name=platform_info.display_name,
            connection_string=None,
            options={},
        )

        if execution_mode and execution_mode in ("sql", "dataframe"):
            config.execution_mode = execution_mode

        config.options.update(self.verbosity.to_config())
        return config

    def _prompt_execution_mode(self, platform: str, default_mode: str) -> str:
        from rich.prompt import Prompt

        console.print("\n[bold cyan]Execution Mode[/bold cyan]")
        console.print(f"[dim]{platform.title()} supports both SQL and DataFrame execution modes.[/dim]")

        console.print(f"  1. SQL mode {' (recommended)' if default_mode == 'sql' else ''}")
        console.print(f"  2. DataFrame mode {' (recommended)' if default_mode == 'dataframe' else ''}")

        default_choice = "1" if default_mode == "sql" else "2"
        choice = Prompt.ask("Select mode", choices=["1", "2"], default=default_choice)

        selected_mode = "sql" if choice == "1" else "dataframe"
        console.print(f"[green]✓ Using {selected_mode.upper()} mode[/green]")
        return selected_mode

    def _display_platform_table(self, enabled_platforms: list[str]) -> None:
        platforms_info = self.platform_manager.detect_platforms()

        table = Table(title="Available Database Platforms", show_header=True)
        table.add_column("ID", style="cyan bold", width=3, justify="right")
        table.add_column("Platform", style="green bold", width=18)
        table.add_column("Description", style="white", width=55)
        table.add_column("Category", style="blue", width=12)

        for i, platform_name in enumerate(enabled_platforms, start=1):
            platform_info = platforms_info[platform_name]

            category_display = platform_info.category.replace("_", " ").title()

            table.add_row(
                str(i),
                platform_info.display_name,
                platform_info.description,
                category_display,
            )

        console.print(table)

    def _display_available_databases(self):
        table = Table(title="Available Databases")
        table.add_column("ID", style="cyan", width=4)
        table.add_column("Database", style="green")
        table.add_column("Version", style="yellow")
        table.add_column("Description", style="white")
        table.add_column("OLAP", style="blue", width=6)
        table.add_column("Adoption", style="magenta", width=12)

        for i, (_db_key, db_info) in enumerate(self.available_databases.items()):
            table.add_row(
                str(i + 1),
                db_info["name"],
                db_info["version"],
                db_info["description"],
                "✓" if db_info["supports_olap"] else "✗",
                db_info.get("adoption", "niche"),
            )

        console.print(table)

    def create_config(
        self,
        platform: str,
        platform_options: dict[str, Any] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
        deployment_selector: str | None = None,
        gateway_selector: str | None = None,
    ) -> DatabaseConfig:

        platform_lower = platform.lower()
        logger.debug(
            "Building database config",
            extra={
                "platform": platform_lower,
                "platform_options": sanitize_platform_options(platform_options or {}),
                "runtime_overrides": sanitize_platform_options(runtime_overrides or {}),
            },
        )

        defaults = {
            name: value
            for name, value in PlatformHookRegistry.get_default_options(platform_lower).items()
            if value is not None
        }
        options = defaults
        if platform_options:
            options = {**defaults, **platform_options}

        overrides = self.verbosity.to_config()
        if runtime_overrides:
            overrides.update(runtime_overrides)
        explicit_only = {k: v for k, v in platform_options.items() if defaults.get(k) != v} if platform_options else {}
        overrides["_explicit_platform_options"] = explicit_only
        if deployment_selector is not None:
            overrides["_deployment_selector"] = deployment_selector

        try:
            config = PlatformHookRegistry.build_database_config(platform_lower, options, overrides)
        except PlatformOptionError as exc:
            raise PlatformOptionError(str(exc)) from exc
        if gateway_selector is not None:
            config.gateway = gateway_selector

        platform_info = PlatformRegistry.get_platform_info(platform_lower)
        driver_package = config.driver_package or (platform_info.driver_package if platform_info else None)
        config.driver_package = driver_package
        driver_version = config.driver_version
        auto_install = config.driver_auto_install or bool(config.options.get("driver_auto_install"))

        if driver_package:
            resolution = ensure_driver_version(
                package_name=driver_package,
                requested_version=driver_version,
                auto_install=auto_install,
                install_hint=platform_info.installation_command if platform_info else None,
            )
            config.driver_version = resolution.requested or driver_version
            config.driver_version_resolved = resolution.resolved
            config.driver_version_actual = resolution.actual
            config.driver_runtime_strategy = resolution.runtime_strategy
            config.driver_runtime_path = resolution.runtime_path
            config.driver_runtime_python_executable = resolution.runtime_python_executable
            config.driver_auto_install = auto_install or resolution.auto_install_used
            config.driver_auto_install_used = resolution.auto_install_used

            _sync_driver_options(config, driver_package)
            _sync_platform_driver_info(platform_info, config)
        else:
            config.driver_version_resolved = config.driver_version

        logger.debug(
            "Database configuration built",
            extra={
                "platform": platform_lower,
                "config": sanitize_platform_options(config.model_dump()),
                "options": sanitize_platform_options(config.options),
            },
        )
        return config

    def test_connection(self, config: DatabaseConfig, system_profile: SystemProfile | None = None) -> bool:
        logger.debug(f"Testing connection for {config.type} (core utility)")
        try:
            return core_check_connection(config, system_profile)
        except Exception as e:
            console.print(f"[red]Connection test failed: {e}[/red]")
            logger.error(f"Connection test failed for {config.type}: {e}", exc_info=True)
            return False

    def _display_available_databases_with_recommendations(self):
        table = Table(title="Available Databases")
        table.add_column("ID", style="cyan", width=4)
        table.add_column("Database", style="green")
        table.add_column("Version", style="yellow")
        table.add_column("Description", style="white")
        table.add_column("OLAP", style="blue", width=6)
        table.add_column("Performance", style="magenta", width=12)
        table.add_column("Recommendation", style="white")

        for i, (db_key, db_info) in enumerate(self.available_databases.items()):
            perf_rating = self._get_performance_rating(db_key)

            recommendation = self._get_database_recommendation(db_key)

            table.add_row(
                str(i + 1),
                db_info["name"],
                db_info["version"],
                db_info["description"],
                "✓" if db_info["supports_olap"] else "✗",
                perf_rating,
                recommendation,
            )

        console.print(table)

        console.print("\n[bold cyan]Selection Guide:[/bold cyan]")
        console.print("• [green]DuckDB[/green]: Best for analytics, fast in-memory processing")
        console.print("• [yellow]PostgreSQL[/yellow]: Full-featured OLTP/OLAP, requires setup")
        console.print("• [blue]SQLite[/blue]: Simple file-based, limited analytics performance")

    def _get_recommended_database(self) -> str:
        db_choices = list(self.available_databases.keys())

        priority_order = ["duckdb", "postgresql", "sqlite3"]

        for preferred in priority_order:
            if preferred in db_choices:
                return preferred

        return db_choices[0] if db_choices else "sqlite3"

    def _get_performance_rating(self, db_key: str) -> str:
        ratings = {"duckdb": "Excellent", "postgresql": "Very Good", "sqlite3": "Basic"}
        return ratings.get(db_key, "Unknown")

    def _get_database_recommendation(self, db_key: str) -> str:
        recommendations = {
            "duckdb": "Best choice",
            "postgresql": "🏢 Production ready",
            "sqlite3": "Simple testing",
        }
        return recommendations.get(db_key, "Available")
