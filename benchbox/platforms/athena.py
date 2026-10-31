# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from ..core.exceptions import ConfigurationError
from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.data_loading import DataSourceResolver, FileFormatRegistry
from .base.runtime_metadata import build_default_normalized_result_metadata
from .presto_trino_utils import normalize_existing_files, show_tables_lower

try:
    import boto3
    from pyathena import connect as athena_connect
except ImportError:
    boto3 = None
    athena_connect = None

_BARE_FLOAT_ARRAY = re.compile(r"\bFLOAT\s*\[", re.IGNORECASE)


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


class AthenaAdapter(PlatformAdapter):
    physical_identifier_case = "lower"

    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True

    def __init__(self, **config):
        super().__init__(**config)

        if not boto3 or not athena_connect:
            available, missing = check_platform_dependencies("athena")
            if not available:
                error_msg = get_dependency_error_message("athena", missing)
                raise ImportError(error_msg)

        self.region = config.get("region") or config.get("aws_region") or "us-east-1"
        self.aws_access_key_id = config.get("aws_access_key_id")
        self.aws_secret_access_key = config.get("aws_secret_access_key")
        self.aws_profile = config.get("aws_profile")

        self.workgroup = config.get("compute_resource") or config.get("workgroup") or "primary"
        self.database = config.get("database") or "default"
        self.catalog = config.get("catalog") or "AwsDataCatalog"

        self.s3_output_location = config.get("s3_output_location")
        self.s3_staging_dir = config.get("s3_staging_dir") or config.get("staging_root")

        if self.s3_staging_dir and self.s3_staging_dir.startswith("s3://"):
            parts = self.s3_staging_dir[5:].split("/", 1)
            self.s3_bucket = parts[0]
            self.s3_prefix = parts[1].rstrip("/") if len(parts) > 1 else "benchbox-data"
        else:
            self.s3_bucket = config.get("s3_bucket")
            prefix = config.get("s3_prefix") or "benchbox-data"
            self.s3_prefix = prefix.rstrip("/") if prefix else "benchbox-data"

        if not self.s3_output_location and self.s3_bucket:
            self.s3_output_location = f"s3://{self.s3_bucket}/athena-results/"

        self.query_timeout = config.get("query_timeout") if config.get("query_timeout") is not None else 0
        self.encryption = config.get("encryption")

        self.data_format = (config.get("data_format") or "parquet").lower()
        self.default_format = config.get("default_format") or "PARQUET"
        self.compression = config.get("compression") or "SNAPPY"
        self.cleanup_staging = config.get("cleanup_staging", True)

        self._total_data_scanned_bytes = 0
        self._query_count = 0

        self._s3_client = None

        self._validate_configuration()

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        if method != "ctas":
            raise ValueError(f"Sorted ingestion method '{method}' is not supported for Athena.")

        return None

    def _ctas_sort_unsupported_reason(self, method: str) -> str | None:
        return (
            "Athena CTAS sort is disabled: a CTAS cannot target its own source table, and a sorted copy "
            "needs its own external_location plus a catalog swap that BenchBox does not implement"
        )

    def _validate_configuration(self) -> None:
        import os

        errors = []

        s3_path_pattern = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](?:/.*)?$")

        if self.s3_output_location and not s3_path_pattern.match(self.s3_output_location):
            errors.append(
                f"Invalid S3 output location format: '{self.s3_output_location}'\n"
                "  Expected format: s3://bucket-name/optional/path/\n"
                "  Example: s3://my-athena-results/benchbox/"
            )

        if self.s3_staging_dir and not s3_path_pattern.match(self.s3_staging_dir):
            errors.append(
                f"Invalid S3 staging directory format: '{self.s3_staging_dir}'\n"
                "  Expected format: s3://bucket-name/optional/path/\n"
                "  Example: s3://my-data-bucket/benchbox-staging/"
            )

        if not self.s3_bucket and not self.s3_staging_dir:
            errors.append(
                "No S3 location configured. Athena requires S3 for data storage.\n"
                "  Configure via one of:\n"
                "    --platform-option s3_staging_dir=s3://bucket/path/\n"
                "    --platform-option s3_bucket=bucket-name\n"
                "    benchbox setup --platform athena (setup reads ATHENA_S3_STAGING_DIR when it is set)"
            )

        has_explicit_creds = bool(self.aws_access_key_id and self.aws_secret_access_key)
        has_profile = bool(self.aws_profile)
        has_env_creds = bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        has_default_profile = os.path.exists(os.path.expanduser("~/.aws/credentials"))
        has_instance_role = self._check_instance_metadata_available()

        if not any([has_explicit_creds, has_profile, has_env_creds, has_default_profile, has_instance_role]):
            errors.append(
                "No AWS credentials found. Athena requires AWS authentication.\n"
                "  Configure via one of:\n"
                "    1. AWS CLI: aws configure\n"
                "    2. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n"
                "    3. Profile: --platform-option aws_profile=your-profile\n"
                "    4. IAM role (when running on AWS EC2/ECS/Lambda)"
            )

        if self.workgroup:
            workgroup_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
            if not workgroup_pattern.match(self.workgroup):
                errors.append(
                    f"Invalid workgroup name: '{self.workgroup}'\n"
                    "  Workgroup must start with a letter, contain only letters, numbers,\n"
                    "  underscores, and hyphens, and be 1-128 characters.\n"
                    "  Example: primary, analytics-team, prod_benchmarks"
                )

        if self.region:
            region_pattern = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
            if not region_pattern.match(self.region):
                errors.append(
                    f"Invalid AWS region format: '{self.region}'\n"
                    "  Expected format: xx-xxxx-N (e.g., us-east-1, eu-west-2)\n"
                    "  Common regions: us-east-1, us-west-2, eu-west-1, ap-southeast-1"
                )

        if errors:
            error_message = "Athena configuration validation failed:\n\n" + "\n\n".join(errors)
            raise ConfigurationError(
                error_message,
                details={
                    "platform": "athena",
                    "region": self.region,
                    "workgroup": self.workgroup,
                    "s3_bucket": self.s3_bucket,
                    "validation_errors": len(errors),
                },
            )

    def _check_instance_metadata_available(self) -> bool:
        import socket

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            result = sock.connect_ex(("169.254.169.254", 80))
            sock.close()
            return result == 0
        except Exception:
            return False

    @property
    def platform_name(self) -> str:
        return "Athena"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        athena_group = parser.add_argument_group("Athena Arguments")
        athena_group.add_argument("--region", type=str, default="us-east-1", help="AWS region for Athena")
        athena_group.add_argument("--workgroup", type=str, default="primary", help="Athena workgroup")
        athena_group.add_argument("--database", type=str, default="default", help="Athena database/schema")
        athena_group.add_argument("--s3-output-location", type=str, help="S3 location for query results")
        athena_group.add_argument("--s3-staging-dir", type=str, help="S3 location for data staging")
        athena_group.add_argument("--aws-profile", type=str, help="AWS profile name for credentials")

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="athena",
                fields=[
                    "region",
                    "aws_region",
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_profile",
                    "compute_resource",
                    "workgroup",
                    "catalog",
                    "s3_output_location",
                    "s3_staging_dir",
                    "staging_root",
                    "s3_bucket",
                    "s3_prefix",
                    "query_timeout",
                    "encryption",
                    "data_format",
                    "default_format",
                    "compression",
                    "cleanup_staging",
                ],
            )
        )

    def _get_s3_client(self):
        if self._s3_client is None:
            session_kwargs = {}
            if self.aws_profile:
                session_kwargs["profile_name"] = self.aws_profile
            if self.region:
                session_kwargs["region_name"] = self.region

            session = boto3.Session(**session_kwargs)

            client_kwargs = {}
            if self.aws_access_key_id and self.aws_secret_access_key:
                client_kwargs["aws_access_key_id"] = self.aws_access_key_id
                client_kwargs["aws_secret_access_key"] = self.aws_secret_access_key

            self._s3_client = session.client("s3", **client_kwargs)

        return self._s3_client

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "athena",
            "platform_name": "AWS Athena",
            "connection_mode": "serverless",
            "configuration": {
                "region": self.region,
                "workgroup": self.workgroup,
                "database": self.database,
                "catalog": self.catalog,
                "s3_output_location": self.s3_output_location,
                "s3_staging_dir": self.s3_staging_dir,
                "s3_bucket": self.s3_bucket,
                "s3_prefix": self.s3_prefix,
                "data_format": self.data_format,
                "default_format": self.default_format,
                "compression": self.compression,
                "cleanup_staging": self.cleanup_staging,
                "query_timeout": self.query_timeout,
                "encryption": self.encryption,
            },
        }

        if connection:
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                if result:
                    platform_info["platform_version"] = result[0]
                cursor.close()
            except Exception as e:
                self.logger.debug(f"Could not get Athena version: {e}")
                platform_info["platform_version"] = "Athena (version unknown)"
        else:
            platform_info["platform_version"] = None

        try:
            import pyathena

            platform_info["client_library_version"] = pyathena.__version__
        except Exception:
            platform_info["client_library_version"] = None

        return platform_info

    def get_normalized_result_metadata(
        self,
        *,
        connection: Any | None = None,
        platform_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        info = dict(platform_info) if isinstance(platform_info, Mapping) else self.get_platform_info(connection)
        metadata = build_default_normalized_result_metadata(self, connection=connection, platform_info=info)
        config = info.get("configuration") if isinstance(info.get("configuration"), Mapping) else {}

        metadata["platform_deployment"] = self._athena_deployment_metadata(config)
        metadata["platform_cloud"] = self._athena_cloud_metadata(config)
        metadata["platform_compute"] = self._athena_compute_metadata(config)
        metadata["platform_storage"] = self._athena_storage_metadata(config)
        return metadata

    @staticmethod
    def _athena_deployment_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        return _compact_metadata(
            {
                "deployment_type": "serverless",
                "connection_mode": "serverless",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "requested",
                "collection_status": "partial",
                "workgroup": config.get("workgroup"),
                "catalog": config.get("catalog"),
                "database": config.get("database"),
            }
        )

    @staticmethod
    def _athena_cloud_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        region = config.get("region") or config.get("aws_region")
        return _compact_metadata(
            {
                "provider": "aws",
                "region": region,
                "source": "requested" if region else "unavailable",
                "collection_status": "partial" if region else "unavailable",
            }
        )

    @staticmethod
    def _athena_compute_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        has_compute_config = bool(config.get("workgroup") or config.get("query_timeout") is not None)
        return _compact_metadata(
            {
                "service_model": "serverless",
                "product": "athena",
                "workgroup": config.get("workgroup"),
                "query_timeout": config.get("query_timeout"),
                "source": "requested" if has_compute_config else "unavailable",
                "collection_status": "partial" if has_compute_config else "unavailable",
            }
        )

    @staticmethod
    def _athena_storage_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        bucket = config.get("s3_bucket")
        prefix = config.get("s3_prefix") if bucket or config.get("s3_staging_dir") else None
        query_output_location = config.get("s3_output_location")
        staging_location = config.get("s3_staging_dir")
        if not staging_location and bucket:
            staging_location = f"s3://{bucket}/{prefix or ''}".rstrip("/")
        cleanup_staging = config.get("cleanup_staging")
        cleanup_policy = None
        if cleanup_staging is not None:
            cleanup_policy = "drop_staging" if cleanup_staging else "preserve_staging"

        has_storage_config = bool(
            query_output_location
            or staging_location
            or bucket
            or config.get("data_format")
            or config.get("default_format")
            or config.get("compression")
            or config.get("cleanup_staging") is not None
        )
        return _compact_metadata(
            {
                "table_format": config.get("default_format"),
                "data_format": config.get("data_format"),
                "staging_location": staging_location,
                "query_output_location": query_output_location,
                "bucket": bucket,
                "prefix": prefix,
                "compression": config.get("compression"),
                "cleanup_policy": cleanup_policy,
                "encryption": config.get("encryption"),
                "query_output_location_status": "available" if query_output_location else "unavailable",
                "staging_location_status": "available" if staging_location else "unavailable",
                "cleanup_policy_status": "available" if cleanup_policy else "unavailable",
                "encryption_status": "available" if config.get("encryption") else "unavailable",
                "source": "requested" if has_storage_config else "unavailable",
                "collection_status": "partial" if has_storage_config else "unavailable",
            }
        )

    def get_target_dialect(self) -> str:
        return "athena"

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            database = connection_config.get("database", self.database)

            session_kwargs = {}
            if self.aws_profile:
                session_kwargs["profile_name"] = self.aws_profile
            if self.region:
                session_kwargs["region_name"] = self.region

            session = boto3.Session(**session_kwargs)
            glue_client = session.client("glue")

            try:
                glue_client.get_database(Name=database)
                return True
            except glue_client.exceptions.EntityNotFoundException:
                return False

        except Exception as e:
            self.logger.debug(f"Error checking database existence: {e}")
            return False

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        try:
            session_kwargs = {}
            if self.aws_profile:
                session_kwargs["profile_name"] = self.aws_profile
            if self.region:
                session_kwargs["region_name"] = self.region

            session = boto3.Session(**session_kwargs)
            glue_client = session.client("glue")

            paginator = glue_client.get_paginator("get_tables")
            tables_to_delete = [
                table["Name"]
                for page in paginator.paginate(DatabaseName=database)
                for table in page.get("TableList", [])
            ]
            self.log_notice(f"Deleting {len(tables_to_delete)} table(s) from {database}")
            for table_name in tables_to_delete:
                self.logger.debug(f"Deleting table {database}.{table_name}")
                glue_client.delete_table(DatabaseName=database, Name=table_name)

            glue_client.delete_database(Name=database)
            self.logger.info(f"Dropped database {database}")

        except Exception as e:
            raise RuntimeError(f"Failed to drop Athena database {database}: {e}") from e

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Athena connection")

        self.handle_existing_database(**connection_config)

        connect_kwargs: dict[str, Any] = {
            "s3_staging_dir": self.s3_output_location,
            "region_name": self.region,
            "work_group": self.workgroup,
            "catalog_name": self.catalog,
            "schema_name": connection_config.get("database", self.database),
        }

        if self.aws_access_key_id and self.aws_secret_access_key:
            connect_kwargs["aws_access_key_id"] = self.aws_access_key_id
            connect_kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        elif self.aws_profile:
            connect_kwargs["profile_name"] = self.aws_profile

        target_database = connect_kwargs["schema_name"]

        if not self.database_was_reused and not self.check_server_database_exists(database=target_database):
            self.log_verbose(f"Creating database: {target_database}")
            self._create_database(target_database)

        try:
            connection = athena_connect(**connect_kwargs)

            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            self.logger.info(f"Connected to Athena in {self.region}")
            self.log_operation_complete("Athena connection", details=f"Database: {target_database}")

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Athena: {e}")
            raise

    def _create_database(self, database_name: str) -> None:
        try:
            session_kwargs = {}
            if self.aws_profile:
                session_kwargs["profile_name"] = self.aws_profile
            if self.region:
                session_kwargs["region_name"] = self.region

            session = boto3.Session(**session_kwargs)
            glue_client = session.client("glue")

            location_uri = f"s3://{self.s3_bucket}/{self.s3_prefix}/databases/{database_name}/"

            glue_client.create_database(
                DatabaseInput={
                    "Name": database_name,
                    "Description": "BenchBox benchmark database",
                    "LocationUri": location_uri,
                }
            )
            self.logger.info(f"Created database {database_name}")

        except Exception as e:
            if "AlreadyExistsException" in str(type(e).__name__):
                self.logger.debug(f"Database {database_name} already exists")
            else:
                raise RuntimeError(f"Failed to create database {database_name}: {e}") from e

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        cursor = connection.cursor()
        is_staging = self.data_format == "parquet"

        try:
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())

            for statement in self._external_table_ddl(schema_sql, is_staging=is_staging):
                try:
                    cursor.execute(statement)
                    self.logger.debug(f"Executed: {statement[:100]}...")
                except Exception as e:
                    if "already exists" in str(e).lower():
                        table_name = self._extract_table_name(statement)
                        if table_name:
                            self.log_notice(f"Dropping existing Athena table before recreate: {table_name}")
                            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
                            cursor.execute(statement)
                    else:
                        raise

            mode_desc = "staging tables (parquet mode)" if is_staging else "text tables"
            self.logger.info(f"Schema created ({mode_desc})")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

        return elapsed_seconds(start_time)

    def _external_table_layout(self, table_name: str, is_staging: bool) -> dict[str, Any]:
        root = f"s3://{self.s3_bucket}/{self.s3_prefix}"
        if is_staging:
            return {
                "external": True,
                "stored_as": "TEXTFILE",
                "location": f"{root}/{self.database}_staging/{table_name}/",
                "field_delimiter": "|",
                "line_delimiter": "\n",
            }
        location = f"{root}/{self.database}/{table_name}/"
        if self.data_format == "parquet" or self.default_format.upper() == "PARQUET":
            return {"external": True, "stored_as": "PARQUET", "location": location}
        text_layout = {
            "external": True,
            "stored_as": "TEXTFILE",
            "location": location,
            "line_delimiter": "\n",
        }
        if self.default_format.upper() in ("CSV", "TEXTFILE"):
            return {**text_layout, "field_delimiter": ","}
        return {**text_layout, "field_delimiter": "|", "properties": {"skip.header.line.count": "0"}}

    def _external_table_ddl(self, schema_sql: str, is_staging: bool = False) -> list[str]:
        from benchbox.utils.dialect_utils import translate_ddl

        statements: list[str] = []
        for statement in (part.strip() for part in schema_sql.split(";")):
            if not statement:
                continue
            table_name = self._extract_table_name(statement)
            layout = self._external_table_layout(table_name.strip('"`'), is_staging) if table_name else None
            statements.extend(
                translate_ddl(
                    _BARE_FLOAT_ARRAY.sub("DOUBLE[", statement),
                    "athena",
                    "athena",
                    strip_primary_keys=True,
                    strip_foreign_keys=True,
                    strip_not_null=True,
                    if_not_exists=True,
                    table_case="lower",
                    table_suffix="_staging" if is_staging else "",
                    type_map={"VARCHAR": "STRING", "CHAR": "STRING"},
                    drop_type_parameters=["STRING"],
                    default_layout=layout,
                )
            )
        return statements

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats = {}
        total_time = 0.0

        if not self.s3_bucket:
            raise ValueError(
                "S3 bucket not configured. Athena requires S3 for data storage.\n"
                "Configure via: --platform-option s3_bucket=your-bucket\n"
                "Or set staging_root: s3://your-bucket/path"
            )

        s3_client = self._get_s3_client()
        cursor = connection.cursor()

        try:
            data_files = self._resolve_data_files(benchmark, data_dir)

            is_parquet_mode = self.data_format == "parquet"

            tables_to_convert = []
            effective_tuning = self.get_effective_tuning_configuration()

            for table_name, file_paths in data_files.items():
                table_name_lower = table_name.lower()
                uploaded_rows, file_count = self._upload_files_to_s3(
                    s3_client=s3_client,
                    table_name=table_name,
                    table_name_lower=table_name_lower,
                    file_paths=file_paths,
                    is_parquet_mode=is_parquet_mode,
                )
                chunk_info = f" from {file_count} file(s)" if file_count > 1 else ""
                self.log_verbose(f"Uploading data for table: {table_name}{chunk_info}")

                if is_parquet_mode:
                    tables_to_convert.append((table_name_lower, uploaded_rows))
                    self.logger.info(f"📤 Uploaded {uploaded_rows:,} rows to staging for {table_name_lower}")
                else:
                    row_count, count_verified = self._load_text_mode_table(
                        cursor=cursor,
                        table_name_lower=table_name_lower,
                        uploaded_rows=uploaded_rows,
                    )
                    table_stats[table_name_lower] = row_count
                    if count_verified and effective_tuning is not None:
                        self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
                        self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

                    self.logger.info(
                        f"✅ Loaded {table_stats[table_name_lower]:,} rows into {table_name_lower}{chunk_info}"
                    )

            if is_parquet_mode and tables_to_convert:
                self.logger.info("🔄 Converting staging tables to Parquet format...")
                table_stats = self._convert_staging_to_parquet(cursor, tables_to_convert, s3_client)
                if effective_tuning is not None:
                    for table_name_lower in table_stats:
                        self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
                        self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            mode_desc = "Parquet" if is_parquet_mode else "text"
            self.logger.info(f"✅ Loaded {total_rows:,} total rows ({mode_desc} format) in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, total_time, None

    def validate_external_table_requirements(self) -> None:
        if not self.s3_bucket:
            raise ValueError(
                "Athena external mode requires S3 bucket configuration. "
                "Set --platform-option s3_bucket=your-bucket or "
                "--platform-option staging_root=s3://your-bucket/path."
            )

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()
        start_time = mono_time()
        table_stats: dict[str, int] = {}

        s3_client = self._get_s3_client()
        cursor = connection.cursor()
        try:
            data_files = self._resolve_data_files(benchmark, data_dir)
            external_table_sql = self._build_external_table_statements(benchmark)

            for table_name, file_paths in data_files.items():
                table_name_lower = table_name.lower()
                parquet_files = self._normalize_parquet_files(file_paths)
                if not parquet_files:
                    raise ValueError(
                        f"Athena external mode requires Parquet source files for table '{table_name_lower}'. "
                        "Generate or convert data to Parquet before using --table-mode external."
                    )

                self._upload_external_parquet_files_to_s3(s3_client, table_name_lower, parquet_files)

                create_table_sql = external_table_sql.get(table_name_lower)
                if not create_table_sql:
                    raise ValueError(f"No CREATE TABLE statement found for table '{table_name_lower}'")

                try:
                    cursor.execute(create_table_sql)
                except Exception as exc:
                    if "already exists" in str(exc).lower():
                        self.log_notice(f"Dropping existing Athena table before recreate: {table_name_lower}")
                        cursor.execute(f"DROP TABLE IF EXISTS {table_name_lower}")
                        cursor.execute(create_table_sql)
                    else:
                        raise

                cursor.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
                result = cursor.fetchone()
                table_stats[table_name_lower] = int(result[0]) if result else 0

        finally:
            cursor.close()

        total_time = elapsed_seconds(start_time)
        return table_stats, total_time, None

    def _normalize_parquet_files(self, file_paths: Any) -> list[Path]:
        valid_files = self._normalize_existing_files(file_paths)
        return [path for path in valid_files if path.suffix.lower() == ".parquet"]

    def _upload_external_parquet_files_to_s3(
        self,
        s3_client: Any,
        table_name_lower: str,
        parquet_files: list[Path],
    ) -> None:
        s3_table_path = f"{self.s3_prefix}/{self.database}/{table_name_lower}/"
        for file_path in parquet_files:
            s3_key = f"{s3_table_path}{file_path.name}"
            try:
                s3_client.upload_file(str(file_path), self.s3_bucket, s3_key)
                self.logger.debug(f"Uploaded {file_path.name} to s3://{self.s3_bucket}/{s3_key}")
            except Exception as exc:
                self.logger.error(f"Failed to upload {file_path}: {exc}")
                raise

    def _build_external_table_statements(self, benchmark: Any) -> dict[str, str]:
        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())
        table_sql: dict[str, str] = {}

        for statement in self._external_table_ddl(schema_sql):
            table_name = self._extract_table_name(statement)
            if table_name:
                table_sql[table_name.strip('"`')] = statement

        return table_sql

    def _resolve_data_files(self, benchmark: Any, data_dir: Path) -> dict[str, Any]:
        resolver = DataSourceResolver(
            platform_name=self.platform_name,
            table_mode=self.table_mode,
            platform_config=self.platform_config,
            requested_format=self.requested_table_format,
        )
        data_source = resolver.resolve(benchmark, data_dir)
        if not data_source or not data_source.tables:
            raise ValueError("No data files found")
        return data_source.tables

    _normalize_existing_files = staticmethod(normalize_existing_files)

    def _build_s3_table_path(self, table_name_lower: str, is_parquet_mode: bool) -> str:
        if is_parquet_mode:
            return f"{self.s3_prefix}/{self.database}_staging/{table_name_lower}/"
        return f"{self.s3_prefix}/{self.database}/{table_name_lower}/"

    def _upload_files_to_s3(
        self,
        s3_client: Any,
        table_name: str,
        table_name_lower: str,
        file_paths: Any,
        is_parquet_mode: bool,
    ) -> tuple[int, int]:
        valid_files = self._normalize_existing_files(file_paths)
        file_count = len(valid_files)
        s3_table_path = self._build_s3_table_path(table_name_lower, is_parquet_mode)
        uploaded_rows = 0

        for file_path in valid_files:
            s3_key = f"{s3_table_path}{file_path.name}"
            try:
                s3_client.upload_file(str(file_path), self.s3_bucket, s3_key)

                compression_handler = FileFormatRegistry.get_compression_handler(file_path)
                with compression_handler.open(file_path) as file_handle:
                    uploaded_rows += sum(1 for line in file_handle if line.strip())

                self.logger.debug(f"Uploaded {file_path.name} to s3://{self.s3_bucket}/{s3_key}")
            except Exception as e:
                self.logger.error(f"Failed to upload {file_path}: {e}")
                raise

        return uploaded_rows, file_count

    def _load_text_mode_table(self, cursor: Any, table_name_lower: str, uploaded_rows: int) -> tuple[int, bool]:
        try:
            cursor.execute(f"MSCK REPAIR TABLE {table_name_lower}")
            self.logger.debug(f"Repaired table {table_name_lower}")
        except Exception as e:
            self.logger.debug(f"MSCK REPAIR for {table_name_lower}: {e}")

        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name_lower}")
            result = cursor.fetchone()
            actual_row_count = result[0] if result else 0
            if actual_row_count != uploaded_rows:
                self.logger.warning(
                    f"Row count mismatch for {table_name_lower}: "
                    f"uploaded {uploaded_rows:,}, table has {actual_row_count:,}"
                )
            return actual_row_count, True
        except Exception as e:
            self.logger.warning(f"Could not verify row count for {table_name_lower}: {e}")
            return uploaded_rows, False

    def _convert_staging_to_parquet(
        self,
        cursor: Any,
        tables_to_convert: list[tuple[str, int]],
        s3_client: Any,
    ) -> dict[str, int]:
        table_stats = {}

        for table_name, expected_rows in tables_to_convert:
            staging_table = f"{table_name}_staging"
            parquet_location = f"s3://{self.s3_bucket}/{self.s3_prefix}/{self.database}/{table_name}/"

            try:
                self.log_notice(f"Dropping existing Athena table before CTAS conversion: {table_name}")
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

                ctas_sql = f"""
                CREATE TABLE {table_name}
                WITH (
                    format = 'PARQUET',
                    external_location = '{parquet_location}',
                    parquet_compression = '{self.compression}'
                )
                AS SELECT * FROM {staging_table}
                """
                self.logger.debug(f"Executing CTAS for {table_name}")
                cursor.execute(ctas_sql)

                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                result = cursor.fetchone()
                actual_row_count = result[0] if result else 0
                table_stats[table_name] = actual_row_count

                if actual_row_count != expected_rows:
                    self.logger.warning(
                        f"Row count mismatch for {table_name}: expected {expected_rows:,}, got {actual_row_count:,}"
                    )

                self.logger.info(f"✅ Converted {table_name} to Parquet ({actual_row_count:,} rows)")

                if self.cleanup_staging:
                    self._cleanup_staging(cursor, s3_client, table_name, staging_table)

            except Exception as e:
                self.logger.error(f"Failed to convert {table_name} to Parquet: {e}")
                table_stats[table_name] = expected_rows
                raise

        return table_stats

    def _cleanup_staging(
        self,
        cursor: Any,
        s3_client: Any,
        table_name: str,
        staging_table: str,
    ) -> None:
        try:
            self.log_notice(f"Dropping staging table {staging_table}")
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")

            staging_prefix = f"{self.s3_prefix}/{self.database}_staging/{table_name}/"
            paginator = s3_client.get_paginator("list_objects_v2")

            objects_to_delete = []
            for page in paginator.paginate(Bucket=self.s3_bucket, Prefix=staging_prefix):
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})

            if objects_to_delete:
                self.log_notice(f"Deleting {len(objects_to_delete)} staging files for {table_name}")
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i : i + 1000]
                    s3_client.delete_objects(Bucket=self.s3_bucket, Delete={"Objects": batch})

        except Exception as e:
            self.logger.warning(f"Failed to cleanup staging for {table_name}: {e}")

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_verbose(f"Configuring Athena for {benchmark_type} benchmark")

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        start_time = mono_time()
        cursor = connection.cursor()

        try:
            cursor.execute(query)
            result = cursor.fetchall()

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0

            data_scanned_bytes = 0
            query_execution_id = None

            if hasattr(cursor, "query_id"):
                query_execution_id = cursor.query_id

            if hasattr(cursor, "data_scanned_in_bytes"):
                data_scanned_bytes = cursor.data_scanned_in_bytes or 0
                self._total_data_scanned_bytes += data_scanned_bytes

            self._query_count += 1

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

            from benchbox.core.cost.pricing import resolve_athena_price_per_tb

            cost_per_tb = resolve_athena_price_per_tb(self.region or "us-east-1").value or 5.0
            cost = (data_scanned_bytes / (10**12)) * cost_per_tb

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=result[0] if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )

            result_dict["data_scanned_bytes"] = data_scanned_bytes
            result_dict["cost"] = cost
            result_dict["query_execution_id"] = query_execution_id
            result_dict["resource_usage"] = {
                "data_scanned_bytes": data_scanned_bytes,
                "cost_usd": cost,
            }

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }
        finally:
            cursor.close()

        self._merge_plan_capture_into_result(result_dict, connection, query, query_id)

        return result_dict

    def get_cost_summary(self) -> dict[str, Any]:
        from benchbox.core.cost.pricing import resolve_athena_price_per_tb

        cost_per_tb = resolve_athena_price_per_tb(self.region or "us-east-1").value or 5.0
        total_tb = self._total_data_scanned_bytes / (10**12)
        total_cost = total_tb * cost_per_tb

        return {
            "total_data_scanned_bytes": self._total_data_scanned_bytes,
            "total_data_scanned_tb": total_tb,
            "query_count": self._query_count,
            "cost_per_tb_usd": cost_per_tb,
            "total_cost_usd": total_cost,
            "average_cost_per_query_usd": total_cost / max(self._query_count, 1),
        }

    def _extract_table_name(self, statement: str) -> str | None:

        match = re.search(
            r"CREATE\s+(?:EXTERNAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)",
            statement,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip().lower()
        return None

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

        return get_query_plan_from_cursor(connection, query, explain_prefix="EXPLAIN (FORMAT JSON)", logger=self.logger)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser

        return PrestoTrinoQueryPlanParser(platform_name="athena")

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    def test_connection(self) -> bool:
        try:
            connect_kwargs: dict[str, Any] = {
                "s3_staging_dir": self.s3_output_location,
                "region_name": self.region,
                "work_group": self.workgroup,
                "catalog_name": self.catalog,
                "schema_name": "default",
            }

            if self.aws_access_key_id and self.aws_secret_access_key:
                connect_kwargs["aws_access_key_id"] = self.aws_access_key_id
                connect_kwargs["aws_secret_access_key"] = self.aws_secret_access_key
            elif self.aws_profile:
                connect_kwargs["profile_name"] = self.aws_profile

            conn = athena_connect(**connect_kwargs)
            cursor = conn.cursor()

            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return True
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            self.logger.debug(f"Connection test failed: {e}")
            return False

    _supported_tuning_type_names = ("PARTITIONING",)

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name.lower() for col in sorted_cols]
                clauses.append(f"PARTITIONED BY ({', '.join(column_names)})")

        except ImportError:
            pass

        return " ".join(clauses)

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Athena tunings for {table_name} applied at table creation time")

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        if not unified_config:
            return

        for _table_name, table_tuning in unified_config.table_tunings.items():
            self.apply_table_tunings(table_tuning, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return
        self.logger.info("Athena optimizations applied via workgroup settings")

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        if primary_key_config and primary_key_config.enabled:
            self.logger.info("Primary key constraints noted (Athena does not enforce constraints)")

    _get_existing_tables = staticmethod(show_tables_lower)

    def analyze_table(self, connection: Any, table_name: str) -> None:
        self.logger.debug(f"ANALYZE not needed for Athena - statistics are auto-collected for {table_name}")


def _apply_athena_config_fields(config: Any) -> None:
    config.region = config.options.get("region") or config.options.get("aws_region")


_build_athena_config = make_registered_platform_config_builder(
    "athena",
    __name__,
    "AWS Athena",
    "pyathena",
    [
        "workgroup",
        "database",
        "catalog",
        "s3_output_location",
        "s3_staging_dir",
        "staging_root",
        "s3_bucket",
        "s3_prefix",
        "aws_profile",
        "aws_access_key_id",
        "aws_secret_access_key",
        "data_format",
        "default_format",
        "compression",
        "cleanup_staging",
        "query_timeout",
        "encryption",
    ],
    postprocess=_apply_athena_config_fields,
)
