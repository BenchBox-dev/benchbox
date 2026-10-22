from __future__ import annotations

import inspect
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from benchbox.utils.printing import quiet_console

if TYPE_CHECKING:
    from benchbox.platforms.base.models import ConnectionConfig


class ConnectionLifecycleMixin:
    platform_name: str
    logger: logging.Logger
    connection_pool: Any
    platform_config: dict[str, Any]

    def test_connection(self, connection_config: ConnectionConfig | None = None) -> bool:
        try:
            test_conn = self.create_connection(**(connection_config.__dict__ if connection_config else {}))
            self.close_connection(test_conn)
            return True
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    @staticmethod
    def validate_platform_dependencies() -> dict[str, bool]:
        return {
            "duckdb": ConnectionLifecycleMixin._check_import("duckdb"),
            "databricks": ConnectionLifecycleMixin._check_databricks_dependencies(),
            "clickhouse": ConnectionLifecycleMixin._check_import("clickhouse_driver"),
            "cloudpathlib": ConnectionLifecycleMixin._check_import("cloudpathlib"),
            "snowflake": ConnectionLifecycleMixin._check_import("snowflake.connector"),
            "psutil": ConnectionLifecycleMixin._check_import("psutil"),
        }

    @staticmethod
    def _check_import(module_name: str) -> bool:
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_databricks_dependencies() -> bool:
        required_modules = ["databricks.sql", "databricks.sdk"]
        return all(ConnectionLifecycleMixin._check_import(module) for module in required_modules)

    @staticmethod
    def require_dependencies(required: list[str], exit_on_missing: bool = True) -> dict[str, bool]:
        available_deps = ConnectionLifecycleMixin.validate_platform_dependencies()
        missing_deps = [dep for dep in required if not available_deps.get(dep, False)]

        if missing_deps:
            quiet_console.print("❌ Missing required dependencies:")
            for dep in missing_deps:
                quiet_console.print(f"   - {dep}")

            quiet_console.print("\n💡 Installation instructions:")
            for dep in missing_deps:
                install_cmd = ConnectionLifecycleMixin._get_install_command(dep)
                if install_cmd:
                    quiet_console.print(f"   {dep}: {install_cmd}")

            if exit_on_missing:
                sys.exit(1)
        else:
            quiet_console.print("✅ All required dependencies are available")

        return available_deps

    @staticmethod
    def _get_install_command(dependency: str) -> str | None:
        install_commands = {
            "duckdb": "uv add duckdb",
            "databricks": "uv add databricks-sql-connector databricks-sdk",
            "clickhouse": "uv add clickhouse-driver",
            "cloudpathlib": "uv add cloudpathlib",
            "snowflake": "uv add snowflake-connector-python",
            "psutil": "uv add psutil",
        }
        return install_commands.get(dependency)

    def get_connection_from_pool(self) -> Any:
        if self.connection_pool:
            return self.connection_pool.get_connection()
        return self._create_unpooled_connection()

    def _create_unpooled_connection(self) -> Any:
        try:
            parameters = inspect.signature(self.create_connection).parameters
        except (TypeError, ValueError):
            return self.create_connection(**self.platform_config)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
            return self.create_connection(**self.platform_config)
        if "connection_config" in parameters:
            return self.create_connection(connection_config=dict(self.platform_config))
        return self.create_connection()

    def get_database_path(self, **connection_config) -> str | None:
        return None

    def check_database_exists(self, **connection_config) -> bool:
        db_path = self.get_database_path(**connection_config)
        if db_path and db_path != ":memory:":
            return Path(db_path).exists()

        return self.check_server_database_exists(**connection_config)

    def check_server_database_exists(self, **connection_config) -> bool:
        return False

    def _validate_database_compatibility(self, **connection_config):
        from benchbox.platforms.base.validation import DatabaseValidator

        validator = DatabaseValidator(adapter=self, connection_config=connection_config)
        return validator.validate()

    def check_benchmark_tables_exist(self, **connection_config) -> bool | None:
        return None

    @contextmanager
    def non_destructive_connection_context(self) -> Iterator[None]:
        prior = getattr(self, "_validating_database", False)
        self._validating_database = True
        try:
            yield
        finally:
            self._validating_database = prior

    def handle_existing_database(self, **connection_config) -> None:
        self.log_operation_start("Database validation", "Checking existing database compatibility")

        if self.dry_run or getattr(self, "dry_run_mode", False):
            self.log_verbose("Database validation skipped (dry run mode)")
            return

        if getattr(self, "skip_database_management", False):
            self.log_verbose("Database management skipped (managed cloud database)")
            tables_exist = self.check_benchmark_tables_exist(**connection_config)
            if tables_exist is False:
                self.log_verbose("Managed database cannot be safely reused - treating as fresh database")
                self.database_was_reused = False
                return
            if tables_exist is True:
                self.log_verbose("Managed database has required benchmark tables")
            self.database_was_reused = True
            return

        if getattr(self, "_validating_database", False):
            self.log_very_verbose("Inside validation context - skipping reuse/recreate logic.")
            return

        if getattr(self, "_existing_db_decided", False):
            self.log_very_verbose("Existing-database decision already made for this run - skipping.")
            return
        self._existing_db_decided = True

        self.log_very_verbose("Checking if database exists...")
        if not self.check_database_exists(**connection_config):
            self.log_very_verbose("Database does not exist. Returning.")
            return
        self.log_verbose("Existing database found")

        db_path = self.get_database_path(**connection_config)
        is_file_based = db_path and db_path != ":memory:"

        if is_file_based:
            file_size = Path(db_path).stat().st_size
            size_mb = file_size / (1024 * 1024)
            db_info = f"{Path(db_path).name} ({size_mb:.1f} MB)"
        else:
            db_name = connection_config.get("database", "default")
            db_info = f"'{db_name}'"

        if self.force_recreate:
            self.log_verbose(f"Force recreate enabled - removing existing database: {db_info}")
            self._remove_database(is_file_based, db_path, **connection_config)
            return

        self.log_verbose(f"Database {db_info} already exists, validating compatibility...")
        validation_result = self._validate_database_compatibility(**connection_config)

        if validation_result.warnings:
            for warning in validation_result.warnings:
                self.logger.warning(f"⚠️ {warning}")

        if validation_result.issues:
            from benchbox.core.tuning.metadata import NO_TUNING_METADATA_ERROR

            fresh_database_issue = f"Tuning: {NO_TUNING_METADATA_ERROR}"
            for issue in validation_result.issues:
                if getattr(validation_result, "database_empty", False) and (
                    issue == fresh_database_issue or issue.startswith("Missing tables:")
                ):
                    self.log_verbose(issue)
                else:
                    self.logger.error(f"❌ {issue}")

        if validation_result.is_valid:
            self.log_verbose("Database is configured for this run")
            self.log_verbose(f"Using existing database: {db_info}")
            self.log_verbose("Database being reused - skipping schema creation and data loading")
            self.database_was_reused = True
            self.log_operation_complete(
                "Database validation", details="Database reused - compatible with current configuration"
            )
        else:
            if validation_result.can_reuse:
                self.log_verbose("Database has compatibility issues - recreating for reliable results")
            else:
                self.log_verbose("Database is not configured for this run - recreating")

            self.log_verbose("Recreating database...")
            self.database_was_reused = False
            self._remove_database(
                is_file_based,
                db_path,
                foreign_tables=tuple(getattr(validation_result, "foreign_tables", ()) or ()),
                **connection_config,
            )
            self.log_operation_complete("Database validation", details="Database recreated due to incompatibility")

    def _remove_database(
        self, is_file_based: bool, db_path: str, *, foreign_tables: tuple[str, ...] = (), **connection_config
    ) -> None:
        try:
            if is_file_based:
                db_path_obj = Path(db_path)
                if db_path_obj.is_file():
                    db_path_obj.unlink()
                    self.logger.warning(f"Deleted database file: {db_path_obj}")
                elif db_path_obj.is_dir():
                    import shutil

                    shutil.rmtree(db_path_obj)
                    self.logger.warning(f"Deleted database directory: {db_path_obj}")
                else:
                    self.logger.warning("Database path exists but is neither file nor directory")
            elif self.reset_database_in_place(**connection_config):
                self.logger.warning("Reset the existing tables in place; the database was kept for reload")
            elif foreign_tables:
                shown = ", ".join(foreign_tables[:10]) + (
                    f" and {len(foreign_tables) - 10} more" if len(foreign_tables) > 10 else ""
                )
                raise RuntimeError(
                    f"refusing to drop the database: it holds tables that do not belong to this benchmark ({shown}), "
                    "and dropping it would delete them. Remove them yourself, or re-run with --force-recreate "
                    "to drop the whole database."
                )
            else:
                self.drop_database(**connection_config)
                self.logger.warning("Dropped database")
        except Exception as e:
            self.logger.error(f"Failed to remove database: {e}")
            raise RuntimeError(f"Could not remove existing database: {e}") from e

    def reset_database_in_place(self, **connection_config) -> bool:
        return False

    def fail_closed_on_force_recreate(self, *, platform_label: str, database: str | None, manual_hint: str) -> None:
        if not getattr(self, "force_recreate", False):
            return
        if getattr(self, "dry_run", False) or getattr(self, "dry_run_mode", False):
            self.log_verbose(f"--force-recreate has no automatic drop on {platform_label} (dry run: nothing to drop)")
            return
        name = database or "default"
        raise RuntimeError(
            f"{platform_label} does not support --force-recreate: BenchBox cannot drop "
            f"database '{name}' automatically. {manual_hint} "
            "Re-run without --force-recreate once the database has been removed."
        )

    def drop_database(self, **connection_config) -> None:
        raise NotImplementedError("drop_database not implemented for this platform")
