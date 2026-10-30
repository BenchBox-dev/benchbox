from __future__ import annotations

import re
from typing import Any

from ._dependencies import import_chdb
from .deployment_mode import resolve_clickhouse_deployment_mode


class ClickHouseMetadataMixin:
    @property
    def platform_name(self) -> str:
        return f"ClickHouse ({self.deployment_mode.title()})"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        ch_group = parser.add_argument_group("ClickHouse Arguments")
        ch_group.add_argument(
            "--data-path", type=str, default="/tmp/benchbox_ch_local", help="Path for local mode data"
        )
        ch_group.add_argument(
            "--deployment-mode",
            type=str,
            default="local",
            help="ClickHouse deployment mode (server or local)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        platform_options = config.get("options")
        if isinstance(platform_options, dict):
            config = {**platform_options, **config}

        deployment_mode = resolve_clickhouse_deployment_mode(config)
        adapter_config = {
            "deployment_mode": deployment_mode,
            "data_path": config.get("data_path", "/tmp/benchbox_ch_local"),
        }

        if deployment_mode == "local":
            if config.get("database_path"):
                adapter_config["database_path"] = config["database_path"]
            elif config.get("benchmark") and config.get("scale_factor") is not None:
                from benchbox.utils.database_naming import generate_database_filename
                from benchbox.utils.path_utils import get_benchmark_runs_databases_path

                data_dir = get_benchmark_runs_databases_path(config["benchmark"], config["scale_factor"])
                db_filename = generate_database_filename(
                    benchmark_name=config["benchmark"],
                    scale_factor=config["scale_factor"],
                    platform="clickhouse",
                    tuning_config=config.get("tuning_config"),
                )
                db_path = str(data_dir / db_filename)
                if not db_path.endswith(".chdb"):
                    db_path += ".chdb"
                data_dir.mkdir(parents=True, exist_ok=True)
                adapter_config["database_path"] = db_path

        elif deployment_mode == "server":
            if config.get("database"):
                adapter_config["database"] = config["database"]
            elif config.get("benchmark") and config.get("scale_factor") is not None:
                from benchbox.utils.scale_factor import format_benchmark_name

                benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
                adapter_config["database"] = re.sub(r"[^a-z0-9_]", "_", f"benchbox_{benchmark_name}".lower())

        for key in [
            "host",
            "port",
            "username",
            "user",
            "password",
            "secure",
            "compression",
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
            "optimize_after_load",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def get_database_path(self, **connection_config) -> str | None:
        if self.deployment_mode == "local":
            db_path = connection_config.get("database_path")
            if db_path:
                if db_path.endswith(".duckdb"):
                    db_path = db_path.replace(".duckdb", ".chdb")
                elif not db_path.endswith(".chdb"):
                    db_path += ".chdb"
                return db_path

            if getattr(self, "database_path", None):
                return self.database_path

            return None

        return None

    def get_target_dialect(self) -> str:
        return "clickhouse"

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = self._build_base_platform_info()
        self._apply_clickhouse_mode_configuration(platform_info)
        platform_info["client_library_version"] = self._detect_clickhouse_client_version()

        if not connection:
            platform_info["platform_version"] = None
            return platform_info

        try:
            if self.deployment_mode == "local":
                self._collect_local_clickhouse_metadata(connection, platform_info)
            else:
                self._collect_server_clickhouse_metadata(connection, platform_info)
        except Exception as e:
            self.logger.debug(f"Error collecting ClickHouse platform info: {e}")
            platform_info["platform_version"] = None

        return platform_info

    def _build_base_platform_info(self) -> dict[str, Any]:
        return {
            "platform_type": "clickhouse",
            "platform_name": f"ClickHouse ({self.deployment_mode.title()})",
            "connection_mode": self.deployment_mode,
            "configuration": {
                "deployment_mode": self.deployment_mode,
                "mode": self.deployment_mode,
                "result_cache_enabled": not getattr(self, "disable_result_cache", True),
            },
        }

    def _apply_clickhouse_mode_configuration(self, platform_info: dict[str, Any]) -> None:
        config = platform_info["configuration"]
        if self.deployment_mode == "local":
            config["data_path"] = getattr(self, "data_path", None)
        elif self.deployment_mode == "server":
            config["host"] = getattr(self, "host", None)
            config["port"] = getattr(self, "port", None)
            config["database"] = getattr(self, "database", None)

    def _detect_clickhouse_client_version(self) -> str | None:
        try:
            if self.deployment_mode == "local":
                return getattr(import_chdb(), "__version__", None)
            from clickhouse_driver import __version__ as ch_version

            return ch_version
        except (ImportError, AttributeError):
            return None

    def _collect_local_clickhouse_metadata(self, connection: Any, platform_info: dict[str, Any]) -> None:
        try:
            result = connection.query("SELECT version()")
            if result and len(result) > 0:
                version_line = result.split("\n")[0] if isinstance(result, str) else str(result)
                platform_info["platform_version"] = version_line.strip()
                platform_info["engine_version"] = platform_info["platform_version"]
                platform_info["engine_version_source"] = "sql_query"
        except Exception as e:
            self.logger.debug(f"Could not query ClickHouse version in local mode: {e}")
            platform_info["platform_version"] = None

    def _collect_server_clickhouse_metadata(self, connection: Any, platform_info: dict[str, Any]) -> None:
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT version()")
            result = cursor.fetchone()
            platform_info["platform_version"] = result[0] if result else None
            platform_info["engine_version"] = platform_info["platform_version"]
            platform_info["engine_version_source"] = "sql_query"

            self._collect_clickhouse_system_settings(cursor, platform_info)
            self._collect_clickhouse_build_options(cursor, platform_info)
            cursor.close()
        except Exception as e:
            self.logger.debug(f"Error collecting ClickHouse platform info in server mode: {e}")
            if platform_info.get("platform_version") is None:
                platform_info["platform_version"] = None

    def _collect_clickhouse_system_settings(self, cursor: Any, platform_info: dict[str, Any]) -> None:
        try:
            cursor.execute("""
                SELECT name, value
                FROM system.settings
                WHERE name IN (
                    'max_threads',
                    'max_memory_usage',
                    'max_execution_time',
                    'merge_tree_max_rows_to_use_cache',
                    'allow_experimental_analyzer'
                )
                ORDER BY name
            """)
            settings_results = cursor.fetchall()
            if not settings_results:
                return
            platform_info["compute_configuration"] = {"system_settings": {}}
            for row in settings_results:
                setting_name = row[0] if len(row) > 0 else None
                setting_value = row[1] if len(row) > 1 else None
                if setting_name:
                    platform_info["compute_configuration"]["system_settings"][setting_name] = setting_value
            self.logger.debug("Successfully captured ClickHouse system settings")
        except Exception as e:
            self.logger.debug(f"Could not query ClickHouse system settings: {e}")

    def _collect_clickhouse_build_options(self, cursor: Any, platform_info: dict[str, Any]) -> None:
        try:
            cursor.execute("""
                SELECT name, value
                FROM system.build_options
                WHERE name IN ('CXX_FLAGS', 'BUILD_TYPE', 'USE_JEMALLOC', 'USE_SIMDJSON')
                ORDER BY name
            """)
            build_results = cursor.fetchall()
            if not build_results:
                return
            if "compute_configuration" not in platform_info:
                platform_info["compute_configuration"] = {}
            platform_info["compute_configuration"]["build_options"] = {}
            for row in build_results:
                option_name = row[0] if len(row) > 0 else None
                option_value = row[1] if len(row) > 1 else None
                if option_name:
                    platform_info["compute_configuration"]["build_options"][option_name] = option_value
            self.logger.debug("Successfully captured ClickHouse build options")
        except Exception as e:
            self.logger.debug(f"Could not query ClickHouse build options: {e}")


__all__ = ["ClickHouseMetadataMixin"]
