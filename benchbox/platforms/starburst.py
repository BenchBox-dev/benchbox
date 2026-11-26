# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from benchbox.platforms.base.adapter import DriverIsolationCapability
from benchbox.platforms.base.config_utils import make_registered_platform_config_builder
from benchbox.platforms.base.remote_work import StartedRemoteWork, cancel_cursor_query

from .trino import TrinoAdapter

if TYPE_CHECKING:
    pass


class StarburstAdapter(TrinoAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def __init__(self, **config):
        self._configure_starburst_defaults(config)

        super().__init__(**config)
        self._started_work = StartedRemoteWork()

        self.role = config.get("role") or os.environ.get("STARBURST_ROLE")
        self._dialect = "trino"

    def _cursor_started(self, cursor: Any) -> None:
        self._started_work.add(str(id(cursor)), cursor)

    def _cursor_finished(self, cursor: Any) -> None:
        self._started_work.discard(str(id(cursor)))

    def cancel_remote_work(self) -> None:
        self._started_work.cancel_all("Starburst", cancel_cursor_query)

    def _configure_starburst_defaults(self, config: dict[str, Any]) -> None:
        if "host" not in config or not config["host"]:
            config["host"] = os.environ.get("STARBURST_HOST")

        if "port" not in config or config["port"] is None:
            env_port = os.environ.get("STARBURST_PORT")
            config["port"] = int(env_port) if env_port else 443

        if "username" not in config or not config["username"]:
            config["username"] = os.environ.get("STARBURST_USER") or os.environ.get("STARBURST_USERNAME")

        role = config.get("role") or os.environ.get("STARBURST_ROLE")
        if role and config.get("username") and "/" not in (config.get("username") or ""):
            config["username"] = f"{config['username']}/{role}"

        if "password" not in config or not config["password"]:
            config["password"] = os.environ.get("STARBURST_PASSWORD")

        if "catalog" not in config or not config["catalog"]:
            config["catalog"] = os.environ.get("STARBURST_CATALOG")

        if "http_scheme" not in config:
            config["http_scheme"] = "https"

        if "verify_ssl" not in config:
            config["verify_ssl"] = True

        if not config.get("host"):
            raise ValueError(
                "Starburst Galaxy requires host configuration.\n"
                "Provide STARBURST_HOST in the environment or in the platform configuration."
            )

        if not config.get("username"):
            raise ValueError(
                "Starburst Galaxy requires username configuration.\n"
                "Provide STARBURST_USER or STARBURST_USERNAME in the environment or in the platform configuration.\n"
                "Example format: joe@example.com/accountadmin"
            )

        if not config.get("password"):
            raise ValueError(
                "Starburst Galaxy requires password configuration.\n"
                "Provide STARBURST_PASSWORD in the environment or in the platform configuration."
            )

    @property
    def platform_name(self) -> str:
        return "Starburst"

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser

        return PrestoTrinoQueryPlanParser(platform_name="starburst")

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        info = super().get_platform_info(connection)
        info["platform_type"] = "starburst"
        info["platform_name"] = "Starburst Galaxy"
        info["connection_mode"] = "cloud"
        info["configuration"]["deployment"] = "managed"
        return info

    def get_target_dialect(self) -> str:
        return "trino"

    def _build_friendly_connection_error(self, exc: Exception) -> str | None:
        error_str = str(exc).lower()

        if "401" in error_str or "unauthorized" in error_str:
            return (
                "Starburst Galaxy authentication failed.\n"
                "Check your credentials:\n"
                "  - Username format: email/role (e.g., joe@example.com/accountadmin)\n"
                "  - Password: Your Starburst Galaxy password or API key\n"
                "  - Verify credentials at: https://galaxy.starburst.io"
            )

        if "connection refused" in error_str or "could not connect" in error_str:
            return (
                f"Cannot connect to Starburst Galaxy at {self.host}:{self.port}.\n"
                "Check:\n"
                "  - Host is correct: {cluster-name}.trino.galaxy.starburst.io\n"
                "  - Network connectivity to Starburst Galaxy\n"
                "  - Any firewall or proxy restrictions"
            )

        if "ssl" in error_str or "certificate" in error_str:
            return (
                "SSL certificate error connecting to Starburst Galaxy.\n"
                "Options:\n"
                "  - Check network proxy settings\n"
                "  - Verify the SSL certificate chain\n"
                "  - Adjust SSL verification in the platform configuration only when required"
            )

        return None

    @staticmethod
    def add_cli_arguments(parser) -> None:
        starburst_group = parser.add_argument_group("Starburst Arguments")
        starburst_group.add_argument(
            "--host", type=str, help="Starburst Galaxy hostname (e.g., my-cluster.trino.galaxy.starburst.io)"
        )
        starburst_group.add_argument("--port", type=int, default=443, help="Starburst Galaxy port (default: 443)")
        starburst_group.add_argument("--catalog", type=str, help="Default catalog for queries")
        starburst_group.add_argument("--schema", type=str, default="default", help="Default schema within the catalog")
        starburst_group.add_argument(
            "--username", type=str, help="Username in email/role format (e.g., joe@example.com/accountadmin)"
        )
        starburst_group.add_argument("--password", type=str, help="Password for Starburst Galaxy authentication")
        starburst_group.add_argument(
            "--role", type=str, help="Role name (appended to username if not already included)"
        )
        starburst_group.add_argument(
            "--table-format",
            type=str,
            choices=["memory", "hive", "iceberg", "delta"],
            default="iceberg",
            help="Table format for creating benchmark tables (default: iceberg)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="starburst",
                generated_key="schema",
                fields=[
                    "host",
                    "port",
                    "catalog",
                    "username",
                    "password",
                    "role",
                    "http_scheme",
                    "verify_ssl",
                    "ssl_cert_path",
                    "session_properties",
                    "query_timeout",
                    "timezone",
                    "encoding",
                    "disable_result_cache",
                    "table_format",
                    "staging_root",
                    "source_catalog",
                ],
            )
        )


_build_starburst_config = make_registered_platform_config_builder(
    "starburst",
    __name__,
    "Starburst",
    "trino",
    [
        "host",
        "port",
        "catalog",
        "username",
        "password",
        "role",
        "http_scheme",
        "verify_ssl",
        "ssl_cert_path",
        "session_properties",
        "query_timeout",
        "timezone",
        "table_format",
        "staging_root",
        "schema",
    ],
)
