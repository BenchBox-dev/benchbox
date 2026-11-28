from __future__ import annotations

from typing import Any


class StarRocksMetadataMixin:
    @property
    def platform_name(self) -> str:
        return "StarRocks"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        sr_group = parser.add_argument_group("StarRocks Arguments")
        sr_group.add_argument(
            "--host",
            type=str,
            default="localhost",
            help="StarRocks FE hostname",
        )
        sr_group.add_argument(
            "--port",
            type=int,
            default=9030,
            help="StarRocks FE MySQL protocol port (default: 9030)",
        )
        sr_group.add_argument(
            "--username",
            type=str,
            default="root",
            help="StarRocks username",
        )
        sr_group.add_argument(
            "--password",
            type=str,
            default="",
            help="StarRocks password",
        )
        sr_group.add_argument(
            "--database",
            type=str,
            default=None,
            help="StarRocks database name (auto-generated if not specified)",
        )
        sr_group.add_argument(
            "--http-port",
            type=int,
            default=8040,
            help="StarRocks BE HTTP port for Stream Load (default: 8040)",
        )

    def get_target_dialect(self) -> str:
        return "starrocks"

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "starrocks",
            "platform_name": "StarRocks",
            "deployment_mode": getattr(self, "deployment_mode", "self-hosted"),
            "configuration": {
                "host": getattr(self, "host", None),
                "port": getattr(self, "port", None),
                "database": getattr(self, "database", None),
                "http_port": getattr(self, "http_port", None),
            },
        }

        try:
            from benchbox.platforms.starrocks._dependencies import pymysql as _pymysql

            if _pymysql:
                platform_info["client_library_version"] = getattr(_pymysql, "__version__", None)
            else:
                platform_info["client_library_version"] = None
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None

        if connection:
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT current_version()")
                result = cursor.fetchone()
                if result:
                    version_str = result[0] if isinstance(result, (list, tuple)) else result
                    platform_info["platform_version"] = str(version_str)
                else:
                    platform_info["platform_version"] = None
                cursor.close()
            except Exception:
                platform_info["platform_version"] = None
        else:
            platform_info["platform_version"] = None

        return platform_info


__all__ = ["StarRocksMetadataMixin"]
