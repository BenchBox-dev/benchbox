# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from benchbox.platforms.base.adapter import DriverIsolationCapability
from benchbox.platforms.base.config_utils import make_registered_platform_config_builder
from benchbox.platforms.base.runtime_metadata import build_default_normalized_result_metadata, with_canonical_compute
from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import TuningColumn


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


def _names_contain_glob_syntax(names: list[str]) -> bool:
    return any(char in name for name in names for char in ("*", "?", "{", "["))


class ClickHouseCloudAdapter(ClickHouseAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True

    def __init__(self, **config):
        config["deployment_mode"] = "cloud"
        config["_is_cloud_subclass"] = True

        self._apply_cloud_defaults(config)

        super().__init__(**config)
        self.deployment_selected = "managed"
        self.deployment_selected_class = "managed"
        config.pop("_is_cloud_subclass", None)

        logger.info(f"ClickHouse Cloud adapter initialized for host: {self.host}")

    def _apply_cloud_defaults(self, config: dict[str, Any]) -> None:
        if "host" not in config or not config["host"]:
            config["host"] = os.environ.get("CLICKHOUSE_CLOUD_HOST")

        if "password" not in config or not config["password"]:
            config["password"] = os.environ.get("CLICKHOUSE_CLOUD_PASSWORD")

        if "oauth_token" not in config or not config["oauth_token"]:
            config["oauth_token"] = os.environ.get("CLICKHOUSE_CLOUD_OAUTH_TOKEN")

        if "username" not in config or not config["username"]:
            config["username"] = os.environ.get("CLICKHOUSE_CLOUD_USER", "default")

        if "s3_staging_url" not in config or not config["s3_staging_url"]:
            config["s3_staging_url"] = os.environ.get("CLICKHOUSE_CLOUD_S3_STAGING_URL")

        if "s3_region" not in config or not config["s3_region"]:
            config["s3_region"] = os.environ.get("CLICKHOUSE_CLOUD_S3_REGION")

        if "gcs_staging_url" not in config or not config["gcs_staging_url"]:
            config["gcs_staging_url"] = os.environ.get("CLICKHOUSE_CLOUD_GCS_STAGING_URL")

    @property
    def platform_name(self) -> str:
        return "ClickHouse Cloud"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        try:
            mode, _method = self.resolve_sorted_ingestion_strategy()
        except ValueError as exc:
            raise ValueError(
                "ClickHouse Cloud does not support post-load sorted ingestion. "
                "Define table ORDER BY keys at CREATE TABLE time instead."
            ) from exc

        if mode == "off":
            return None
        raise ValueError(
            "ClickHouse Cloud does not support post-load sorted ingestion. "
            "Define table ORDER BY keys at CREATE TABLE time instead."
        )

    @staticmethod
    def add_cli_arguments(parser) -> None:
        cloud_group = parser.add_argument_group("ClickHouse Cloud Arguments")
        cloud_group.add_argument(
            "--host",
            type=str,
            help="ClickHouse Cloud hostname (e.g., abc123.us-east-2.aws.clickhouse.cloud)",
        )
        cloud_group.add_argument(
            "--password",
            type=str,
            help="ClickHouse Cloud password (or use CLICKHOUSE_CLOUD_PASSWORD env)",
        )
        cloud_group.add_argument(
            "--clickhouse-cloud-oauth-token",
            type=str,
            help="OAuth/bearer token for authentication (or use CLICKHOUSE_CLOUD_OAUTH_TOKEN env). "
            "When provided, this is used instead of username/password.",
        )
        cloud_group.add_argument(
            "--username",
            type=str,
            default="default",
            help="Username (default: 'default')",
        )
        cloud_group.add_argument(
            "--database",
            type=str,
            default="default",
            help="Database name (default: 'default')",
        )

        staging_group = parser.add_argument_group("ClickHouse Cloud Storage Staging")
        staging_group.add_argument(
            "--clickhouse-cloud-s3-staging-url",
            type=str,
            help="S3 URL for staging data files during loading (e.g., s3://bucket/benchbox-staging/). "
            "Or use CLICKHOUSE_CLOUD_S3_STAGING_URL env var.",
        )
        staging_group.add_argument(
            "--clickhouse-cloud-s3-region",
            type=str,
            help="AWS region for the S3 staging bucket (e.g., us-east-1). Or use CLICKHOUSE_CLOUD_S3_REGION env var.",
        )
        staging_group.add_argument(
            "--clickhouse-cloud-gcs-staging-url",
            type=str,
            help="GCS URL for staging data files during loading (e.g., gs://bucket/benchbox-staging/). "
            "Or use CLICKHOUSE_CLOUD_GCS_STAGING_URL env var.",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.core.compute_resource import normalize_compute_options

        config = normalize_compute_options("clickhouse-cloud", config)
        adapter_config: dict[str, Any] = {}

        for key in [
            "compute_resource",
            "host",
            "password",
            "username",
            "database",
            "oauth_token",
            "region",
            "cloud_region",
            "cloud_provider",
            "service_id",
            "service_name",
            "service_tier",
            "compute_size",
        ]:
            if key in config and config[key] is not None:
                adapter_config[key] = config[key]

        for key in [
            "max_memory_usage",
            "max_execution_time",
            "max_threads",
            "disable_result_cache",
            "compression",
            "optimize_after_load",
        ]:
            if key in config and config[key] is not None:
                adapter_config[key] = config[key]

        for key in ["s3_staging_url", "s3_region", "gcs_staging_url"]:
            if key in config and config[key] is not None:
                adapter_config[key] = config[key]

        if "benchmark" in config:
            adapter_config["benchmark"] = config["benchmark"]

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        info = super().get_platform_info(connection)
        info.setdefault("configuration", {})
        info["platform_type"] = "clickhouse-cloud"
        info["platform_name"] = "ClickHouse Cloud"
        info["connection_mode"] = "cloud"
        info["configuration"]["deployment"] = "managed"
        info["configuration"]["host"] = getattr(self, "host", None)
        info["configuration"]["port"] = getattr(self, "port", None)
        info["configuration"]["database"] = getattr(self, "database", None)
        info["configuration"]["secure"] = getattr(self, "secure", None)
        info["configuration"]["region"] = self.platform_config.get("region") or self.platform_config.get("cloud_region")
        info["configuration"]["cloud_provider"] = self.platform_config.get("cloud_provider")
        info["configuration"]["service_id"] = self.platform_config.get("compute_resource") or self.platform_config.get(
            "service_id"
        )
        info["configuration"]["service_name"] = self.platform_config.get("service_name")
        info["configuration"]["service_tier"] = self.platform_config.get("service_tier")
        info["configuration"]["compute_size"] = self.platform_config.get("compute_size")

        if getattr(self, "oauth_token", None):
            info["configuration"]["auth_method"] = "oauth"
        else:
            info["configuration"]["auth_method"] = "password"

        if getattr(self, "s3_staging_url", None):
            info["configuration"]["s3_staging_url"] = self.s3_staging_url
        if getattr(self, "s3_region", None):
            info["configuration"]["s3_region"] = self.s3_region
        if getattr(self, "gcs_staging_url", None):
            info["configuration"]["gcs_staging_url"] = self.gcs_staging_url

        return info

    def get_normalized_result_metadata(
        self,
        *,
        connection: Any | None = None,
        platform_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        info = dict(platform_info) if isinstance(platform_info, Mapping) else self.get_platform_info(connection)
        metadata = build_default_normalized_result_metadata(self, connection=connection, platform_info=info)
        config = info.get("configuration") if isinstance(info.get("configuration"), Mapping) else {}
        compute = info.get("compute_configuration") if isinstance(info.get("compute_configuration"), Mapping) else {}
        host_metadata = self._clickhouse_cloud_host_metadata(config.get("host"))

        metadata["platform_deployment"] = self._clickhouse_cloud_deployment_metadata(config)
        metadata["platform_cloud"] = self._clickhouse_cloud_cloud_metadata(config, host_metadata)
        metadata["platform_compute"] = with_canonical_compute(
            metadata.get("platform_compute"),
            self._clickhouse_cloud_compute_metadata(config, compute, host_metadata),
        )
        metadata["platform_storage"] = self._clickhouse_cloud_storage_metadata(config)
        return metadata

    @staticmethod
    def _clickhouse_cloud_host_metadata(host: Any) -> dict[str, Any]:
        host_value = str(host or "").strip().lower()
        if not host_value:
            return {}
        parsed = urlparse(host_value if "://" in host_value else f"//{host_value}")
        hostname = parsed.hostname or host_value.split("/", 1)[0].split(":", 1)[0]
        labels = hostname.split(".")
        metadata: dict[str, Any] = {"service_endpoint": hostname}
        if len(labels) >= 5 and labels[-2:] == ["clickhouse", "cloud"]:
            provider = labels[-3]
            if provider in {"aws", "gcp", "azure"}:
                metadata["provider"] = provider
                metadata["region"] = labels[-4]
                metadata["service_id"] = ".".join(labels[:-4]) or None
        return _compact_metadata(metadata)

    def _clickhouse_cloud_deployment_metadata(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return _compact_metadata(
            {
                "deployment_type": "managed_cloud",
                "connection_mode": "cloud",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "requested",
                "collection_status": "partial",
                "selected": self.deployment_selected,
                "selected_class": self.deployment_selected_class,
                "service_endpoint": config.get("host"),
                "port": config.get("port"),
                "secure": config.get("secure"),
                "auth_method": config.get("auth_method"),
                "database": config.get("database"),
            }
        )

    @staticmethod
    def _clickhouse_cloud_cloud_metadata(
        config: Mapping[str, Any],
        host_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider = config.get("cloud_provider") or host_metadata.get("provider")
        provider = str(provider).lower() if provider else None
        region = config.get("region") or config.get("cloud_region") or host_metadata.get("region")
        explicit_location = bool(config.get("cloud_provider") or config.get("region") or config.get("cloud_region"))
        inferred_location = bool(host_metadata.get("provider") or host_metadata.get("region"))
        source = "requested" if explicit_location else "inferred" if inferred_location else "requested"
        has_cloud_metadata = bool(provider or region or config.get("host"))
        return _compact_metadata(
            {
                "provider": provider,
                "region": region,
                "workspace": config.get("service_id") or host_metadata.get("service_id"),
                "service_endpoint": config.get("host"),
                "region_collection_status": "available" if region else "unavailable",
                "source": source if has_cloud_metadata else "unavailable",
                "collection_status": "partial" if has_cloud_metadata else "unavailable",
            }
        )

    @staticmethod
    def _clickhouse_cloud_compute_metadata(
        config: Mapping[str, Any],
        compute: Mapping[str, Any],
        host_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        observed = bool(compute.get("system_settings") or compute.get("build_options"))
        service_id = config.get("compute_resource") or config.get("service_id") or host_metadata.get("service_id")
        has_compute_metadata = bool(
            observed
            or service_id
            or config.get("service_name")
            or config.get("service_tier")
            or config.get("compute_size")
            or config.get("result_cache_enabled") is not None
        )
        return _compact_metadata(
            {
                "service_model": "managed",
                "service_id": service_id,
                "service_name": config.get("service_name"),
                "service_tier": config.get("service_tier"),
                "compute_size": config.get("compute_size"),
                "result_cache_enabled": config.get("result_cache_enabled"),
                "system_settings": compute.get("system_settings"),
                "build_options": compute.get("build_options"),
                "source": "observed" if observed else "requested" if has_compute_metadata else "unavailable",
                "collection_status": "available" if observed else "partial" if has_compute_metadata else "unavailable",
            }
        )

    @classmethod
    def _clickhouse_cloud_storage_metadata(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        staging_url = config.get("s3_staging_url") or config.get("gcs_staging_url")
        staging = cls._clickhouse_cloud_staging_metadata(staging_url)
        if config.get("s3_region"):
            staging["region"] = config.get("s3_region")
        has_staging = bool(staging_url)
        return _compact_metadata(
            {
                "table_format": "MergeTree",
                "staging_location": staging_url,
                "staging_url_type": staging.get("type"),
                "staging_url_type_status": "available" if has_staging else "unavailable",
                "bucket": staging.get("bucket"),
                "prefix": staging.get("prefix"),
                "region": staging.get("region"),
                "source": "requested" if has_staging else "inferred",
                "collection_status": "partial",
            }
        )

    @staticmethod
    def _clickhouse_cloud_staging_metadata(staging_url: Any) -> dict[str, Any]:
        url = str(staging_url or "")
        if url.startswith("s3://"):
            bucket, prefix = ClickHouseCloudAdapter._parse_s3_url(url)
            return {"type": "s3", "bucket": bucket, "prefix": prefix}
        if url.startswith("gs://"):
            bucket, prefix = ClickHouseCloudAdapter._parse_gcs_url(url)
            return {"type": "gcs", "bucket": bucket, "prefix": prefix}
        return {}

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        if getattr(self, "s3_staging_url", None):
            return self._load_data_via_s3(benchmark, connection, data_dir)
        elif getattr(self, "gcs_staging_url", None):
            return self._load_data_via_gcs(benchmark, connection, data_dir)
        else:
            return super().load_data(benchmark, connection, data_dir)

    def validate_external_table_requirements(self) -> None:
        if not getattr(self, "s3_staging_url", None) and not getattr(self, "gcs_staging_url", None):
            raise ValueError(
                "ClickHouse Cloud external mode requires cloud staging URL. "
                "Set --platform-option s3_staging_url=s3://bucket/prefix/ or "
                "--platform-option gcs_staging_url=gs://bucket/prefix/."
            )

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()
        if getattr(self, "s3_staging_url", None):
            return self._create_external_tables_via_s3(benchmark, connection, data_dir)
        return self._create_external_tables_via_gcs(benchmark, connection, data_dir)

    @staticmethod
    def _normalize_external_file_inputs(file_paths: Any) -> tuple[list[Path], list[str]]:
        normalized_paths = file_paths if isinstance(file_paths, list) else [file_paths]
        local_paths: list[Path] = []
        cloud_uris: list[str] = []

        for file_path in normalized_paths:
            file_str = str(file_path)
            if file_str.startswith(("s3://", "gs://", "https://")):
                cloud_uris.append(file_str)
                continue

            path = Path(file_path)
            if path.exists() and (path.is_dir() or path.stat().st_size > 0):
                local_paths.append(path)

        return local_paths, cloud_uris

    def _create_external_tables_via_s3(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for ClickHouse Cloud external mode with S3 staging.\nInstall with: uv add boto3"
            ) from None

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        data_files = self._resolve_cloud_data_files(benchmark, data_dir)
        s3_bucket, s3_prefix = self._parse_s3_url(self.s3_staging_url)

        s3_client_kwargs: dict[str, Any] = {}
        if getattr(self, "s3_region", None):
            s3_client_kwargs["region_name"] = self.s3_region
        s3_client = boto3.client("s3", **s3_client_kwargs)

        aws_key_id = os.environ.get("AWS_ACCESS_KEY_ID", "").replace("'", "''")
        aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").replace("'", "''")
        if aws_key_id or aws_secret_key:
            self.logger.warning(
                "AWS credentials will be embedded in ClickHouse VIEW definitions and may "
                "appear in server query logs. Consider using a named S3 connection instead."
            )

        for table_name, file_paths in data_files.items():
            table_name_lower = table_name.lower()
            local_paths, cloud_uris = self._normalize_external_file_inputs(file_paths)
            iceberg_locals = [path for path in local_paths if path.is_dir() and (path / "metadata").is_dir()]
            parquet_locals = [path for path in local_paths if path.suffix.lower() == ".parquet"]
            parquet_cloud_uris = [
                uri for uri in cloud_uris if uri.lower().endswith(".parquet") and uri.startswith("s3://")
            ]
            iceberg_cloud_uris = [uri for uri in cloud_uris if uri.startswith("s3://") and "/metadata" in uri]

            if iceberg_locals:
                table_prefix = f"{s3_prefix}{table_name_lower}/external/"
                for local_path in iceberg_locals:
                    for source_file in local_path.rglob("*"):
                        if source_file.is_file():
                            relative = source_file.relative_to(local_path)
                            s3_client.upload_file(str(source_file), s3_bucket, f"{table_prefix}{relative.as_posix()}")
                source_expr = f"iceberg('s3://{s3_bucket}/{table_prefix}', '{aws_key_id}', '{aws_secret_key}')"
            elif iceberg_cloud_uris:
                root = iceberg_cloud_uris[0].split("/metadata", 1)[0] + "/"
                escaped_root = root.replace("'", "''")
                source_expr = f"iceberg('{escaped_root}', '{aws_key_id}', '{aws_secret_key}')"
            elif parquet_locals:
                table_prefix = f"{s3_prefix}{table_name_lower}/external/"
                for local_path in parquet_locals:
                    s3_client.upload_file(str(local_path), s3_bucket, f"{table_prefix}{local_path.name}")
                parquet_glob = f"s3://{s3_bucket}/{table_prefix}*.parquet"
                escaped_glob = parquet_glob.replace("'", "''")
                source_expr = f"s3('{escaped_glob}', '{aws_key_id}', '{aws_secret_key}', 'Parquet')"
            elif parquet_cloud_uris:
                dirs = {uri.rsplit("/", 1)[0] for uri in parquet_cloud_uris}
                if len(dirs) == 1:
                    parquet_glob = dirs.pop() + "/*.parquet"
                else:
                    common = os.path.commonprefix(list(dirs)).rsplit("/", 1)[0]
                    parquet_glob = common + "/**/*.parquet"
                escaped_glob = parquet_glob.replace("'", "''")
                source_expr = f"s3('{escaped_glob}', '{aws_key_id}', '{aws_secret_key}', 'Parquet')"
            else:
                raise ValueError(
                    f"ClickHouse external mode requires Iceberg directories or Parquet files for table "
                    f"'{table_name_lower}'. No supported sources were found."
                )

            create_view_sql = f"CREATE OR REPLACE VIEW {table_name_lower} AS SELECT * FROM {source_expr}"
            connection.execute(create_view_sql)
            count_result = connection.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
            table_stats[table_name_lower] = count_result[0][0] if count_result and count_result[0] else 0

        total_time = elapsed_seconds(start_time)
        metadata = {
            "loading_method": "s3_external_views",
            "s3_staging_url": self.s3_staging_url,
        }
        return table_stats, total_time, metadata

    def _create_external_tables_via_gcs(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        try:
            from google.cloud import storage as gcs_storage
        except ImportError:
            raise ImportError(
                "google-cloud-storage is required for ClickHouse Cloud external mode with GCS staging.\n"
                "Install with: uv add google-cloud-storage"
            ) from None

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        data_files = self._resolve_cloud_data_files(benchmark, data_dir)
        gcs_bucket_name, gcs_prefix = self._parse_gcs_url(self.gcs_staging_url)

        gcs_client = gcs_storage.Client()
        gcs_bucket = gcs_client.bucket(gcs_bucket_name)

        gcs_hmac_key = os.environ.get("GCS_HMAC_ACCESS_KEY", "").replace("'", "''")
        gcs_hmac_secret = os.environ.get("GCS_HMAC_SECRET", "").replace("'", "''")
        if gcs_hmac_key or gcs_hmac_secret:
            self.logger.warning(
                "GCS HMAC credentials will be embedded in ClickHouse VIEW definitions and may "
                "appear in server query logs. Consider using a named GCS connection instead."
            )

        for table_name, file_paths in data_files.items():
            table_name_lower = table_name.lower()
            local_paths, cloud_uris = self._normalize_external_file_inputs(file_paths)
            iceberg_locals = [path for path in local_paths if path.is_dir() and (path / "metadata").is_dir()]
            parquet_locals = [path for path in local_paths if path.suffix.lower() == ".parquet"]
            parquet_cloud_uris = [
                uri for uri in cloud_uris if uri.lower().endswith(".parquet") and uri.startswith("gs://")
            ]
            iceberg_cloud_uris = [uri for uri in cloud_uris if uri.startswith("gs://") and "/metadata" in uri]

            if iceberg_locals:
                table_prefix = f"{gcs_prefix}{table_name_lower}/external/"
                for local_path in iceberg_locals:
                    for source_file in local_path.rglob("*"):
                        if source_file.is_file():
                            relative = source_file.relative_to(local_path)
                            blob = gcs_bucket.blob(f"{table_prefix}{relative.as_posix()}")
                            blob.upload_from_filename(str(source_file))
                root_url = f"https://storage.googleapis.com/{gcs_bucket_name}/{table_prefix}"
                escaped_root_url = root_url.replace("'", "''")
                source_expr = f"iceberg('{escaped_root_url}', '{gcs_hmac_key}', '{gcs_hmac_secret}')"
            elif iceberg_cloud_uris:
                root = iceberg_cloud_uris[0].split("/metadata", 1)[0] + "/"
                root_url = root.replace("gs://", "https://storage.googleapis.com/")
                escaped_root_url = root_url.replace("'", "''")
                source_expr = f"iceberg('{escaped_root_url}', '{gcs_hmac_key}', '{gcs_hmac_secret}')"
            elif parquet_locals:
                table_prefix = f"{gcs_prefix}{table_name_lower}/external/"
                for local_path in parquet_locals:
                    blob = gcs_bucket.blob(f"{table_prefix}{local_path.name}")
                    blob.upload_from_filename(str(local_path))
                parquet_glob = f"https://storage.googleapis.com/{gcs_bucket_name}/{table_prefix}*.parquet"
                escaped_glob = parquet_glob.replace("'", "''")
                source_expr = f"gcs('{escaped_glob}', '{gcs_hmac_key}', '{gcs_hmac_secret}', 'Parquet')"
            elif parquet_cloud_uris:
                dirs = {uri.rsplit("/", 1)[0] for uri in parquet_cloud_uris}
                if len(dirs) == 1:
                    cloud_prefix = dirs.pop()
                else:
                    cloud_prefix = os.path.commonprefix(list(dirs)).rsplit("/", 1)[0]
                parquet_glob = cloud_prefix.replace("gs://", "https://storage.googleapis.com/") + "/*.parquet"
                escaped_glob = parquet_glob.replace("'", "''")
                source_expr = f"gcs('{escaped_glob}', '{gcs_hmac_key}', '{gcs_hmac_secret}', 'Parquet')"
            else:
                raise ValueError(
                    f"ClickHouse external mode requires Iceberg directories or Parquet files for table "
                    f"'{table_name_lower}'. No supported sources were found."
                )

            create_view_sql = f"CREATE OR REPLACE VIEW {table_name_lower} AS SELECT * FROM {source_expr}"
            connection.execute(create_view_sql)
            count_result = connection.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
            table_stats[table_name_lower] = count_result[0][0] if count_result and count_result[0] else 0

        total_time = elapsed_seconds(start_time)
        metadata = {
            "loading_method": "gcs_external_views",
            "gcs_staging_url": self.gcs_staging_url,
        }
        return table_stats, total_time, metadata

    def _resolve_cloud_data_files(self, benchmark, data_dir: Path) -> dict[str, list[Path]]:
        from benchbox.platforms.base.data_loading import resolve_adapter_data_source

        data_source = resolve_adapter_data_source(self, benchmark, data_dir)
        return {table: [Path(p) for p in paths] for table, paths in data_source.tables.items()}

    def _ingest_staged_glob(
        self,
        connection: Any,
        kind: str,
        sql_fn: str,
        safe_key: str,
        safe_secret: str,
        table_name_lower: str,
        valid_files: list[Path],
        upload_fn: Any,
        glob_url_fn: Any,
    ) -> int:
        for file_path in valid_files:
            upload_start = mono_time()
            self.log_verbose(f"Uploading {file_path.name} to {kind.upper()} staging")
            upload_fn(file_path, table_name_lower, file_path.name)
            upload_time = elapsed_seconds(upload_start)
            self.log_verbose(f"Uploaded {file_path.name} in {upload_time:.2f}s")

        glob_url = glob_url_fn(table_name_lower)
        ingest_sql = (
            f"INSERT INTO {table_name_lower} "
            f"SELECT * FROM {sql_fn}("
            f"'{glob_url}', "
            f"'{safe_key}', "
            f"'{safe_secret}', "
            f"'CSVWithNames'"
            f")"
        )

        ingest_start = mono_time()
        connection.execute(ingest_sql)
        ingest_time = elapsed_seconds(ingest_start)

        count_result = connection.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
        total_rows_loaded = count_result[0][0] if count_result and count_result[0] else 0
        self.log_verbose(
            f"Ingested {len(valid_files)} file(s) via {kind.upper()} glob in {ingest_time:.2f}s",
        )
        return total_rows_loaded

    def _load_data_via_object_storage(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
        *,
        kind: str,
        staging_url: str,
        sql_fn: str,
        creds: tuple[str, str],
        upload_fn: Any,
        glob_url_fn: Any | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats: dict[str, int] = {}
        safe_key, safe_secret = creds

        try:
            data_files = self._resolve_cloud_data_files(benchmark, data_dir)

            for table_name, file_paths in data_files.items():
                if not isinstance(file_paths, list):
                    file_paths = [file_paths]

                valid_files = [Path(fp) for fp in file_paths if Path(fp).exists() and Path(fp).stat().st_size > 0]

                if not valid_files:
                    self.logger.warning(f"Skipping {table_name} - no valid data files")
                    table_stats[table_name.lower()] = 0
                    continue

                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.log_verbose(f"Loading data for table ({kind.upper()} staging): {table_name}{chunk_info}")

                try:
                    load_start = mono_time()
                    table_name_lower = table_name.lower()
                    total_rows_loaded = 0

                    if glob_url_fn is not None and not _names_contain_glob_syntax(
                        [table_name_lower, *(file_path.name for file_path in valid_files)]
                    ):
                        total_rows_loaded = self._ingest_staged_glob(
                            connection,
                            kind,
                            sql_fn,
                            safe_key,
                            safe_secret,
                            table_name_lower,
                            valid_files,
                            upload_fn,
                            glob_url_fn,
                        )
                    else:
                        for file_path in valid_files:
                            upload_start = mono_time()
                            self.log_verbose(f"Uploading {file_path.name} to {kind.upper()} staging")
                            sql_url = upload_fn(file_path, table_name_lower, file_path.name)
                            upload_time = elapsed_seconds(upload_start)
                            self.log_verbose(f"Uploaded {file_path.name} in {upload_time:.2f}s")

                            ingest_sql = (
                                f"INSERT INTO {table_name_lower} "
                                f"SELECT * FROM {sql_fn}("
                                f"'{sql_url}', "
                                f"'{safe_key}', "
                                f"'{safe_secret}', "
                                f"'CSVWithNames'"
                                f")"
                            )

                            ingest_start = mono_time()
                            connection.execute(ingest_sql)
                            ingest_time = elapsed_seconds(ingest_start)

                            count_result = connection.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
                            total_rows_loaded = count_result[0][0] if count_result and count_result[0] else 0
                            self.log_verbose(f"Ingested from {file_path.name} via {kind.upper()} in {ingest_time:.2f}s")

                    table_stats[table_name_lower] = total_rows_loaded

                    effective_tuning = self.get_effective_tuning_configuration()
                    if effective_tuning is not None:
                        self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
                        self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

                    load_time = elapsed_seconds(load_start)
                    self.logger.info(
                        f"Loaded {total_rows_loaded:,} rows into {table_name_lower}{chunk_info} "
                        f"via {kind.upper()} staging in {load_time:.2f}s"
                    )

                except Exception as e:
                    self.logger.error(f"Failed to load {table_name} via {kind.upper()}: {str(e)[:200]}...")
                    table_stats[table_name.lower()] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows via {kind.upper()} staging in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"{kind.upper()} staging data loading failed: {e}")
            raise

        loading_metadata: dict[str, Any] = {
            "loading_method": f"{kind}_staging",
            f"{kind}_staging_url": staging_url,
        }
        if extra_metadata:
            loading_metadata.update(extra_metadata)

        return table_stats, total_time, loading_metadata

    def _load_data_via_s3(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 staging data loading.\n"
                "Install with: uv add boto3\n"
                "Alternatively, remove --platform-option s3_staging_url to use INSERT batching."
            ) from None

        aws_key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
        aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

        s3_client_kwargs: dict[str, Any] = {}
        if getattr(self, "s3_region", None):
            s3_client_kwargs["region_name"] = self.s3_region
        s3_client = boto3.client("s3", **s3_client_kwargs)
        s3_bucket, s3_prefix = self._parse_s3_url(self.s3_staging_url)
        s3_load_token = uuid.uuid4().hex[:12]

        def upload_to_s3(file_path: Path, table_name_lower: str, file_name: str) -> str:
            s3_key = f"{s3_prefix}{table_name_lower}/{s3_load_token}/{file_name}"
            s3_url = f"s3://{s3_bucket}/{s3_key}"
            s3_client.upload_file(str(file_path), s3_bucket, s3_key)
            return s3_url

        def s3_glob_url(table_name_lower: str) -> str:
            return f"s3://{s3_bucket}/{s3_prefix}{table_name_lower}/{s3_load_token}/*"

        return self._load_data_via_object_storage(
            benchmark,
            connection,
            data_dir,
            kind="s3",
            staging_url=self.s3_staging_url,
            sql_fn="s3",
            creds=(aws_key_id.replace("'", "''"), aws_secret_key.replace("'", "''")),
            upload_fn=upload_to_s3,
            glob_url_fn=s3_glob_url,
            extra_metadata={"s3_region": getattr(self, "s3_region", None)},
        )

    def _load_data_via_gcs(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        try:
            from google.cloud import storage as gcs_storage
        except ImportError:
            raise ImportError(
                "google-cloud-storage is required for GCS staging data loading.\n"
                "Install with: uv add google-cloud-storage\n"
                "Alternatively, remove --platform-option gcs_staging_url to use INSERT batching."
            ) from None

        gcs_hmac_key = os.environ.get("GCS_HMAC_ACCESS_KEY", "")
        gcs_hmac_secret = os.environ.get("GCS_HMAC_SECRET", "")

        gcs_client = gcs_storage.Client()
        gcs_bucket_name, gcs_prefix = self._parse_gcs_url(self.gcs_staging_url)
        gcs_bucket_obj = gcs_client.bucket(gcs_bucket_name)
        gcs_load_token = uuid.uuid4().hex[:12]

        def upload_to_gcs(file_path: Path, table_name_lower: str, file_name: str) -> str:
            blob_name = f"{gcs_prefix}{table_name_lower}/{gcs_load_token}/{file_name}"
            blob = gcs_bucket_obj.blob(blob_name)
            blob.upload_from_filename(str(file_path))
            return f"https://storage.googleapis.com/{gcs_bucket_name}/{blob_name}"

        def gcs_glob_url(table_name_lower: str) -> str:
            return f"https://storage.googleapis.com/{gcs_bucket_name}/{gcs_prefix}{table_name_lower}/{gcs_load_token}/*"

        return self._load_data_via_object_storage(
            benchmark,
            connection,
            data_dir,
            kind="gcs",
            staging_url=self.gcs_staging_url,
            sql_fn="gcs",
            creds=(gcs_hmac_key.replace("'", "''"), gcs_hmac_secret.replace("'", "''")),
            upload_fn=upload_to_gcs,
            glob_url_fn=gcs_glob_url,
        )

    @staticmethod
    def _parse_s3_url(s3_url: str) -> tuple[str, str]:
        from benchbox.utils.cloud_urls import parse_s3_url

        return parse_s3_url(s3_url)

    @staticmethod
    def _parse_gcs_url(gcs_url: str) -> tuple[str, str]:
        from benchbox.utils.cloud_urls import parse_gcs_url

        return parse_gcs_url(gcs_url)


_build_clickhouse_cloud_config = make_registered_platform_config_builder(
    "clickhouse-cloud",
    __name__,
    "ClickHouse Cloud",
    "clickhouse-connect",
    [
        "host",
        "password",
        "username",
        "database",
        "oauth_token",
        "region",
        "cloud_region",
        "cloud_provider",
        "service_id",
        "service_name",
        "service_tier",
        "compute_size",
        "s3_staging_url",
        "s3_region",
        "gcs_staging_url",
        "max_memory_usage",
        "max_execution_time",
        "disable_result_cache",
        "compression",
        "optimize_after_load",
    ],
)


__all__ = ["ClickHouseCloudAdapter"]
