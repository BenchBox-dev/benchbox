from __future__ import annotations

import logging
import os
import re
from typing import Any

from ._dependencies import ClickHouseClient
from .client import ClickHouseCloudClient, ClickHouseLocalClient

logger = logging.getLogger(__name__)

_CLICKHOUSE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ClickHouseSetupMixin:
    def _setup_server_mode(self, config):
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 9000)
        self.database = config.get("database", "default")
        self.username = config.get("username", config.get("user", "default"))
        self.password = config.get("password", "")
        self.secure = config.get("secure", False)
        self.compression = config.get("compression", False)

        self.max_memory_usage = config.get("max_memory_usage", "8GB")
        self.max_execution_time = config.get("max_execution_time", 300)
        self.max_threads = config.get("max_threads", 8)
        insert_block_size = config.get("insert_block_size", 65536)
        if isinstance(insert_block_size, bool) or not isinstance(insert_block_size, int):
            raise ValueError("insert_block_size must be an integer")
        if insert_block_size <= 0 or insert_block_size == 1000:
            raise ValueError("insert_block_size must be a positive integer other than 1000")
        self.insert_block_size = insert_block_size
        self.send_receive_timeout = config.get("send_receive_timeout", 300)

        self.max_server_memory_usage_ratio = None

        self.disable_result_cache = config.get("disable_result_cache", True)

        self.strict_validation = config.get("strict_validation", True)

    def _setup_local_mode(self, config):
        self.data_path = config.get("data_path", None)
        self.database_path = config.get("database_path", None)

        self.max_memory_usage = config.get("max_memory_usage", "8GB")
        self.max_execution_time = config.get("max_execution_time", 300)
        self.max_threads = config.get("max_threads", 4)

        self.max_server_memory_usage_ratio = None

        self.disable_result_cache = config.get("disable_result_cache", True)

        self.strict_validation = config.get("strict_validation", True)

        self.host = None
        self.port = None
        self.database = None
        self.username = None
        self.password = None
        self.secure = None
        self.compression = None

    def _setup_cloud_mode(self, config):
        self.host = config.get("host") or os.environ.get("CLICKHOUSE_CLOUD_HOST")
        self.password = config.get("password") or os.environ.get("CLICKHOUSE_CLOUD_PASSWORD")
        self.username = config.get("username") or os.environ.get("CLICKHOUSE_CLOUD_USER", "default")
        self.database = config.get("database", "default")

        self.oauth_token = config.get("oauth_token") or os.environ.get("CLICKHOUSE_CLOUD_OAUTH_TOKEN")

        if not self.host:
            raise ValueError(
                "ClickHouse Cloud requires host configuration.\n"
                "Provide via --platform-option host=<hostname> or "
                "CLICKHOUSE_CLOUD_HOST environment variable.\n"
                "Example: abc123.us-east-2.aws.clickhouse.cloud"
            )
        if not self.password and not self.oauth_token:
            raise ValueError(
                "ClickHouse Cloud requires authentication.\n"
                "Provide one of:\n"
                "  - Password: --clickhouse-cloud-password or CLICKHOUSE_CLOUD_PASSWORD env var\n"
                "  - OAuth token: --clickhouse-cloud-oauth-token or CLICKHOUSE_CLOUD_OAUTH_TOKEN env var"
            )

        self.port = config.get("port", 8443)
        self.secure = True
        self.compression = config.get("compression", True)

        self.max_memory_usage = config.get("max_memory_usage", "0")
        self.max_execution_time = config.get("max_execution_time", 600)
        self.max_threads = config.get("max_threads", 0)
        self.send_receive_timeout = config.get("send_receive_timeout", 300)

        self.disable_result_cache = config.get("disable_result_cache", True)

        self.strict_validation = config.get("strict_validation", True)

        self.max_server_memory_usage_ratio = None

        self.s3_staging_url = config.get("s3_staging_url") or os.environ.get("CLICKHOUSE_CLOUD_S3_STAGING_URL")
        self.s3_region = config.get("s3_region") or os.environ.get("CLICKHOUSE_CLOUD_S3_REGION")
        self.gcs_staging_url = config.get("gcs_staging_url") or os.environ.get("CLICKHOUSE_CLOUD_GCS_STAGING_URL")

        if self.s3_staging_url:
            if not self.s3_staging_url.startswith("s3://"):
                from benchbox.core.exceptions import ConfigurationError

                raise ConfigurationError(
                    f"Invalid S3 staging URL: '{self.s3_staging_url}'. "
                    "Must start with 's3://' (e.g., s3://my-bucket/benchbox-staging/)"
                )
            if not self.s3_staging_url.endswith("/"):
                self.s3_staging_url += "/"

        if self.gcs_staging_url:
            if not self.gcs_staging_url.startswith("gs://"):
                from benchbox.core.exceptions import ConfigurationError

                raise ConfigurationError(
                    f"Invalid GCS staging URL: '{self.gcs_staging_url}'. "
                    "Must start with 'gs://' (e.g., gs://my-bucket/benchbox-staging/)"
                )
            if not self.gcs_staging_url.endswith("/"):
                self.gcs_staging_url += "/"

    def _get_connection_params(self, **connection_config) -> dict[str, Any]:
        return {
            "host": connection_config.get("host", self.host),
            "port": connection_config.get("port", self.port),
            "user": connection_config.get("username", connection_config.get("user", self.username)),
            "password": connection_config.get("password", self.password),
            "secure": connection_config.get("secure", self.secure),
            "compression": connection_config.get("compression", self.compression),
        }

    def _create_admin_client(self, **connection_config) -> Any:
        params = self._get_connection_params(**connection_config)
        params.pop("database", None)

        return ClickHouseClient(
            **params,
            connect_timeout=30,
            send_receive_timeout=self.send_receive_timeout,
            sync_request_timeout=self.send_receive_timeout,
        )

    def _quote_database_identifier(self, database: str) -> str:
        if not isinstance(database, str) or not _CLICKHOUSE_IDENTIFIER_RE.fullmatch(database):
            raise ValueError(f"Invalid database identifier: {database}")
        return f"`{database}`"

    def _ensure_server_database_exists(self, database: str, **connection_config) -> None:
        admin_client = self._create_admin_client(**connection_config)
        try:
            admin_client.execute(f"CREATE DATABASE IF NOT EXISTS {self._quote_database_identifier(database)}")
        finally:
            admin_client.disconnect()

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("ClickHouse connection", f"mode: {self.deployment_mode}")

        if self.deployment_mode == "server":
            return self._create_server_connection(**connection_config)
        elif self.deployment_mode == "local":
            return self._create_local_connection(**connection_config)
        elif self.deployment_mode == "cloud":
            return self._create_cloud_connection(**connection_config)
        else:
            raise ValueError(f"Unknown ClickHouse deployment mode: {self.deployment_mode}")

    def _create_server_connection(self, **connection_config) -> Any:
        self.handle_existing_database(**connection_config)

        params = self._get_connection_params(**connection_config)
        database = connection_config.get("database", self.database)

        try:
            admin_kwargs = {key: value for key, value in connection_config.items() if key != "database"}
            self._ensure_server_database_exists(database, **admin_kwargs)
            client = ClickHouseClient(
                **params,
                database=database,
                connect_timeout=30,
                send_receive_timeout=self.send_receive_timeout,
                sync_request_timeout=self.send_receive_timeout,
            )

            client.execute("SELECT 1")
            self.logger.info(f"Connected to ClickHouse server at {params['host']}:{params['port']}")

            return client

        except Exception as e:
            self.logger.error(f"Failed to connect to ClickHouse server: {e}")
            raise

    def _create_local_connection(self, **connection_config) -> Any:
        self.handle_existing_database(**connection_config)

        try:
            db_path = self.get_database_path(**connection_config)

            local_client = ClickHouseLocalClient(db_path=db_path)

            local_client.execute("SELECT 1")

            if db_path:
                self.logger.info(f"Connected to ClickHouse local mode with persistent storage: {db_path}")
            else:
                self.logger.info("Connected to ClickHouse local mode (in-memory)")

            return local_client

        except Exception as e:
            self.logger.error(f"Failed to initialize ClickHouse local mode: {e}")
            raise

    def _create_cloud_connection(self, **connection_config) -> Any:
        self.handle_existing_database(**connection_config)

        host = connection_config.get("host", self.host)
        port = connection_config.get("port", self.port)
        username = connection_config.get("username", self.username)
        password = connection_config.get("password", self.password)
        database = connection_config.get("database", self.database)
        oauth_token = connection_config.get("oauth_token", getattr(self, "oauth_token", None))

        try:
            client = ClickHouseCloudClient(
                host=host,
                port=port,
                user=username,
                password=password,
                database=database,
                secure=True,
                compress=self.compression,
                access_token=oauth_token,
            )

            client.execute("SELECT 1")
            auth_mode = "OAuth token" if oauth_token else "password"
            self.logger.info(f"Connected to ClickHouse Cloud at {host}:{port} (auth: {auth_mode})")

            return client

        except Exception as e:
            self.logger.error(f"Failed to connect to ClickHouse Cloud: {e}")
            raise

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "disconnect"):
                connection.disconnect()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")


__all__ = ["ClickHouseSetupMixin"]
