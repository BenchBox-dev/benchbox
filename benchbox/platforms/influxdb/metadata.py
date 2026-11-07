# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any


class InfluxDBMetadataMixin:
    @property
    def platform_name(self) -> str:
        mode = getattr(self, "mode", "cloud")
        return f"InfluxDB ({mode.title()})"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        influx_group = parser.add_argument_group("InfluxDB Arguments")
        influx_group.add_argument(
            "--host",
            type=str,
            default="localhost",
            help="InfluxDB server hostname",
        )
        influx_group.add_argument(
            "--port",
            type=int,
            default=None,
            help="InfluxDB server port (default: 8181 for core mode, 443 for cloud mode; InfluxDB 2 uses 8086)",
        )
        influx_group.add_argument(
            "--token",
            type=str,
            help="InfluxDB authentication token",
        )
        influx_group.add_argument(
            "--org",
            type=str,
            help="InfluxDB organization name",
        )
        influx_group.add_argument(
            "--database",
            type=str,
            default="benchbox",
            help="InfluxDB database (bucket) name",
        )
        influx_group.add_argument(
            "--ssl",
            action="store_true",
            default=True,
            help="Use SSL/TLS connection",
        )
        influx_group.add_argument(
            "--no-ssl",
            dest="ssl",
            action="store_false",
            help="Disable SSL/TLS connection",
        )
        influx_group.add_argument(
            "--mode",
            type=str,
            choices=["core", "cloud"],
            default="cloud",
            help="InfluxDB deployment mode: 'core' for local/OSS, 'cloud' for managed",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        adapter_config = {
            "host": config.get("host", "localhost"),
            "port": config.get("port"),
            "token": config.get("token"),
            "org": config.get("org"),
            "database": config.get("database", "benchbox"),
            "ssl": config.get("ssl", True),
            "verify_ssl": config.get("verify_ssl", True),
            "ca_cert_path": config.get("ca_cert_path"),
            "mode": config.get("mode", "cloud"),
        }

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def get_target_dialect(self) -> str:
        return "influxdb"

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        mode = getattr(self, "mode", "cloud")

        platform_info = {
            "platform_type": "influxdb",
            "platform_name": f"InfluxDB ({mode.title()})",
            "connection_mode": mode,
            "configuration": {
                "mode": mode,
                "host": getattr(self, "host", None),
                "port": getattr(self, "port", None),
                "database": getattr(self, "database", None),
                "ssl": getattr(self, "ssl", True),
                "org": getattr(self, "org", None),
            },
        }

        try:
            from ._dependencies import FLIGHTSQL_AVAILABLE, INFLUXDB3_AVAILABLE

            if INFLUXDB3_AVAILABLE:
                try:
                    import influxdb3

                    platform_info["client_library"] = "influxdb3-python"
                    platform_info["client_library_version"] = getattr(influxdb3, "__version__", None)
                except (ImportError, AttributeError):
                    pass
            elif FLIGHTSQL_AVAILABLE:
                platform_info["client_library"] = "flightsql-dbapi"
                platform_info["client_library_version"] = None
        except ImportError:
            platform_info["client_library_version"] = None

        if connection is not None:
            try:
                platform_info["platform_version"] = None
            except Exception:
                platform_info["platform_version"] = None
        else:
            platform_info["platform_version"] = None

        return platform_info


__all__ = ["InfluxDBMetadataMixin"]
