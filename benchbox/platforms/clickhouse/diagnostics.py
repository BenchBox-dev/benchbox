from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ClickHouseDiagnosticsMixin:
    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata = {
            "platform": self.platform_name,
            "deployment_mode": self.deployment_mode,
            "mode": self.deployment_mode,
            "result_cache_enabled": not getattr(self, "disable_result_cache", True),
        }

        if self.deployment_mode == "server":
            metadata.update({"host": self.host, "port": self.port, "database": self.database})
        elif self.deployment_mode == "local":
            metadata.update({"data_path": getattr(self, "data_path", None)})

        try:
            version_result = connection.execute("SELECT version()")
            metadata["clickhouse_version"] = version_result[0][0] if version_result else "unknown"

            settings_result = connection.execute("""
                SELECT name, value
                FROM system.settings
                WHERE name IN ('max_memory_usage', 'max_execution_time', 'max_threads')
            """)
            metadata["current_settings"] = dict(settings_result)

            size_result = connection.execute("""
                SELECT
                    database,
                    sum(bytes_on_disk) as total_bytes,
                    count() as table_count
                FROM system.parts
                WHERE database = currentDatabase()
                GROUP BY database
            """)

            if size_result:
                metadata["database_stats"] = {
                    "total_bytes": size_result[0][1],
                    "total_mb": size_result[0][1] / (1024 * 1024),
                    "table_count": size_result[0][2],
                }

        except Exception as e:
            metadata["metadata_error"] = str(e)

        return metadata

    def check_server_database_exists(self, **connection_config) -> bool:
        if self.deployment_mode == "local":
            db_path = self.get_database_path(**connection_config)
            if db_path:
                return Path(db_path).exists()
            return False

        try:
            client = self._create_admin_client(**connection_config)
            try:
                db_name = connection_config.get("database", self.database)

                result = client.execute("SHOW DATABASES")
                databases = [row[0] for row in result]

                return db_name in databases
            finally:
                disconnect = getattr(client, "disconnect", None)
                if callable(disconnect):
                    disconnect()

        except AttributeError:
            raise
        except Exception:
            return False

    def drop_database(self, **connection_config) -> None:
        if self.deployment_mode == "local":
            return

        try:
            client = self._create_admin_client(**connection_config)
            try:
                db_name = connection_config.get("database", self.database)
                client.execute(f"DROP DATABASE IF EXISTS {db_name}")
            finally:
                disconnect = getattr(client, "disconnect", None)
                if callable(disconnect):
                    disconnect()

        except Exception as e:
            raise RuntimeError(f"Failed to drop ClickHouse database: {e}") from e

    def get_table_info(self, connection: Any, table_name: str) -> dict[str, Any]:
        try:
            schema_result = connection.execute(f"""
                SELECT name, type
                FROM system.columns
                WHERE database = currentDatabase() AND table = '{table_name}'
                ORDER BY position
            """)

            stats_result = connection.execute(f"""
                SELECT
                    count() as row_count,
                    sum(bytes_on_disk) as bytes_on_disk,
                    sum(compressed_size) as compressed_size
                FROM system.parts
                WHERE database = currentDatabase() AND table = '{table_name}'
            """)

            return {
                "columns": [(name, type_) for name, type_ in schema_result],
                "row_count": stats_result[0][0] if stats_result else 0,
                "bytes_on_disk": stats_result[0][1] if stats_result else 0,
                "compressed_size": stats_result[0][2] if stats_result else 0,
            }

        except Exception as e:
            return {"error": str(e)}

    def optimize_table(self, connection: Any, table_name: str) -> bool:
        try:
            connection.execute(f"OPTIMIZE TABLE {table_name} FINAL")
            self.logger.info(f"Optimized table {table_name}")
        except Exception as e:
            self.logger.warning(f"Failed to optimize table {table_name}: {e}")
            return False
        return True


__all__ = ["ClickHouseDiagnosticsMixin"]
