# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import json
import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from benchbox.core.primitives_benchmark_utils import failed_platform_error

from .interface import BenchmarkTunings, TableTuning, TuningColumn, TuningType, UnifiedTuningConfiguration

logger = logging.getLogger(__name__)

NO_TUNING_METADATA_ERROR = "No tuning metadata found in database"

_COLUMN_TUNING_TYPE_VALUES = frozenset(
    {
        TuningType.PARTITIONING.value,
        TuningType.CLUSTERING.value,
        TuningType.DISTRIBUTION.value,
        TuningType.SORTING.value,
    }
)


_PLATFORM_KEY_ALIASES = {
    "azure_synapse": "synapse",
    "fabric_warehouse": "fabric_dw",
}


def _normalize_platform_key(raw: object) -> str:
    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    return _PLATFORM_KEY_ALIASES.get(key, key)


@dataclass
class TuningMetadata:
    table_name: str
    tuning_type: str
    column_name: str
    column_order: int
    configuration_hash: str
    created_at: datetime
    platform: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "tuning_type": self.tuning_type,
            "column_name": self.column_name,
            "column_order": self.column_order,
            "configuration_hash": self.configuration_hash,
            "created_at": self.created_at.isoformat(),
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TuningMetadata":
        created_at = data["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return cls(
            table_name=data["table_name"],
            tuning_type=data["tuning_type"],
            column_name=data["column_name"],
            column_order=data["column_order"],
            configuration_hash=data["configuration_hash"],
            created_at=created_at,
            platform=data["platform"],
        )


@dataclass
class MetadataValidationResult:
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_tables: set[str] = field(default_factory=set)
    extra_tables: set[str] = field(default_factory=set)
    configuration_mismatches: dict[str, str] = field(default_factory=dict)
    drifted_sections: set[str] = field(default_factory=set)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def has_issues(self) -> bool:
        return len(self.errors) > 0 or len(self.warnings) > 0

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"is_valid": self.is_valid}
        if self.errors:
            payload["errors"] = list(self.errors)
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.missing_tables:
            payload["missing_tables"] = sorted(self.missing_tables)
        if self.extra_tables:
            payload["extra_tables"] = sorted(self.extra_tables)
        if self.configuration_mismatches:
            payload["configuration_mismatches"] = dict(sorted(self.configuration_mismatches.items()))
        if self.drifted_sections:
            payload["drifted_sections"] = sorted(self.drifted_sections)
        return payload


class TuningMetadataManager:
    _SECTION_MARKER_TABLE = "__benchbox_tuning_sections__"

    _TUNING_TYPE_SCHEMA_VERSION = "schema_version"
    _TUNING_TYPE_CONSTRAINTS_HASH = "constraints_hash"
    _TUNING_TYPE_PLATFORM_OPT_HASH = "platform_optimizations_hash"
    _TUNING_TYPE_TABLE_ATTRIBUTES_HASH = "table_attributes_hash"
    _TUNING_TYPE_RUN_KIND = "run_kind"
    _TUNED_RUN_VALUE = "tuned"

    _METADATA_SCHEMA_VERSION = 3

    _CONSTRAINTS_SECTION = "constraints"
    _PLATFORM_OPTIMIZATIONS_SECTION = "platform_optimizations"
    _TABLE_ATTRIBUTES_SECTION = "table_attributes"

    def __init__(
        self,
        platform_adapter,
        database_name: Optional[str] = None,
        connection_config: Optional[dict[str, Any]] = None,
        connection: Any = None,
    ):
        self.platform_adapter = platform_adapter
        self.database_name = database_name
        self.connection_config = dict(connection_config or {})
        self._shared_connection = connection
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self._metadata_table_name = "benchbox_tuning_metadata"
        self._table_exists = None
        self.last_load_error: str | None = None
        self.marker_save_failed = False

    @contextmanager
    def _managed_connection(self) -> Iterator[Any]:
        if self._shared_connection is not None:
            yield self._shared_connection
            return
        guard = getattr(self.platform_adapter, "non_destructive_connection_context", None)
        with ExitStack() as stack:
            if guard is not None:
                stack.enter_context(guard())
            temp_conn = self.platform_adapter.create_connection(**self._connection_kwargs())
            stack.callback(self.platform_adapter.close_connection, temp_conn)
            yield temp_conn

    def _connection_kwargs(self) -> dict[str, Any]:
        config = dict(self.platform_adapter.platform_config)
        config.update(self.connection_config)
        if self.database_name is not None:
            config["database"] = self.database_name
        return config

    def _is_missing_metadata_table_error(self, exc: Exception) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            message = str(current).lower()
            if self._metadata_table_name.lower() in message:
                code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
                errno = getattr(current, "errno", None) or getattr(current, "code", None)
                if code == "42P01" or errno in {60, 1146}:
                    return True
                if "sqlstate: 42p01" in message:
                    return True
                if any(
                    phrase in message
                    for phrase in (
                        "does not exist",
                        "no such table",
                        "unknown table",
                        "undefined table",
                        "not found",
                    )
                ):
                    return True
            current = getattr(current, "orig", None) or current.__cause__ or current.__context__
        return False

    def _platform_key(self) -> str:
        canonical = getattr(self.platform_adapter, "canonical_platform_type", None)
        if canonical:
            return _normalize_platform_key(canonical)
        return _normalize_platform_key(self.platform_adapter.platform_name)

    @staticmethod
    def _hash_section(payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _constraints_payload(unified_config: UnifiedTuningConfiguration, *, legacy: bool = False) -> dict[str, Any]:
        payload = {
            "unique_constraints": unified_config.unique_constraints.to_dict(),
            "check_constraints": unified_config.check_constraints.to_dict(),
        }
        if not legacy:
            payload = {
                "primary_keys": unified_config.primary_keys.to_dict(),
                "foreign_keys": unified_config.foreign_keys.to_dict(),
                **payload,
            }
        return payload

    @staticmethod
    def _table_attributes_payload(unified_config: UnifiedTuningConfiguration) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for table_name, table_tuning in sorted(unified_config.table_tunings.items()):
            sections: dict[str, Any] = {}
            for tuning_type in TuningType:
                columns = table_tuning.get_columns_by_type(tuning_type)
                if columns:
                    sections[tuning_type.value] = [
                        column.to_dict() for column in sorted(columns, key=lambda column: column.order)
                    ]
            payload[table_name] = sections
        return payload

    def _build_section_marker_records(
        self, unified_config: UnifiedTuningConfiguration, platform: str, created_at: datetime
    ) -> list["TuningMetadata"]:
        constraints_payload = self._constraints_payload(unified_config)
        platform_optimizations_payload = unified_config.platform_optimizations.to_dict()
        table_attributes_payload = self._table_attributes_payload(unified_config)

        return [
            TuningMetadata(
                table_name=self._SECTION_MARKER_TABLE,
                tuning_type=self._TUNING_TYPE_SCHEMA_VERSION,
                column_name="schema_version",
                column_order=self._METADATA_SCHEMA_VERSION,
                configuration_hash=str(self._METADATA_SCHEMA_VERSION),
                created_at=created_at,
                platform=platform,
            ),
            TuningMetadata(
                table_name=self._SECTION_MARKER_TABLE,
                tuning_type=self._TUNING_TYPE_CONSTRAINTS_HASH,
                column_name="constraints_hash",
                column_order=0,
                configuration_hash=self._hash_section(constraints_payload),
                created_at=created_at,
                platform=platform,
            ),
            TuningMetadata(
                table_name=self._SECTION_MARKER_TABLE,
                tuning_type=self._TUNING_TYPE_PLATFORM_OPT_HASH,
                column_name="platform_optimizations_hash",
                column_order=0,
                configuration_hash=self._hash_section(platform_optimizations_payload),
                created_at=created_at,
                platform=platform,
            ),
            TuningMetadata(
                table_name=self._SECTION_MARKER_TABLE,
                tuning_type=self._TUNING_TYPE_TABLE_ATTRIBUTES_HASH,
                column_name="table_attributes_hash",
                column_order=0,
                configuration_hash=self._hash_section(table_attributes_payload),
                created_at=created_at,
                platform=platform,
            ),
        ]

    def _save_section_markers(self, unified_config: UnifiedTuningConfiguration) -> bool:
        try:
            if not self.create_metadata_table():
                self.marker_save_failed = True
                return False

            platform = self._platform_key()
            created_at = datetime.now()
            records = self._build_section_marker_records(unified_config, platform, created_at)
            records.append(self._build_run_kind_record(created_at))
            self._batch_insert_records(records)
            self.marker_save_failed = False
            return True
        except Exception as e:
            self.marker_save_failed = True
            self.logger.warning(f"Failed to save tuning section markers (non-fatal): {e}")
            return False

    def write_tuned_run_marker(self) -> bool:
        try:
            if not self.create_metadata_table():
                return False
            self._batch_insert_records([self._build_run_kind_record(datetime.now())])
            return True
        except Exception as e:
            self.logger.error(f"Failed to write tuned-run marker: {e}")
            return False

    def _build_run_kind_record(self, created_at: datetime) -> "TuningMetadata":
        return TuningMetadata(
            table_name=self._SECTION_MARKER_TABLE,
            tuning_type=self._TUNING_TYPE_RUN_KIND,
            column_name="run_kind",
            column_order=0,
            configuration_hash=self._TUNED_RUN_VALUE,
            created_at=created_at,
            platform=self._platform_key(),
        )

    def has_tuned_run_marker(self) -> bool:
        try:
            if not self._table_exists_check():
                return False
            query_sql = (
                "SELECT 1 FROM "
                f"{self._metadata_table_name} "
                f"WHERE table_name = '{self._SECTION_MARKER_TABLE}' "
                f"AND tuning_type = '{self._TUNING_TYPE_RUN_KIND}' "
                "LIMIT 1"
            )
            with self._managed_connection() as conn:
                return self._fetch_one(conn, query_sql) is not None
        except Exception:
            return False

    def _load_section_markers(self) -> dict[str, str]:
        if not self._table_exists_check():
            return {}

        query_sql = f"""
        SELECT tuning_type, configuration_hash
        FROM {self._metadata_table_name}
        WHERE table_name = '{self._SECTION_MARKER_TABLE}'
        """
        with self._managed_connection() as conn:
            rows = self._fetch_all(conn, query_sql)

        return dict(rows)

    def _compare_section_hashes(
        self, unified_config: UnifiedTuningConfiguration, result: MetadataValidationResult
    ) -> None:
        existing_markers = self._load_section_markers()
        if not existing_markers:
            result.add_warning(
                "No section-hash metadata found in database (written by an older BenchBox "
                "version, or no tunings have been saved yet); unique/check constraint and "
                "platform-optimization drift cannot be detected for this database."
            )
            return

        try:
            schema_version = int(existing_markers.get(self._TUNING_TYPE_SCHEMA_VERSION, "1"))
        except (TypeError, ValueError):
            result.add_error("Unreadable tuning metadata schema version; database reuse is unsafe")
            return
        if schema_version > self._METADATA_SCHEMA_VERSION:
            result.add_error(
                f"Unsupported tuning metadata schema version {schema_version}; "
                f"this BenchBox supports up to {self._METADATA_SCHEMA_VERSION}"
            )
            return

        constraints_payload = self._constraints_payload(unified_config, legacy=schema_version < 3)
        if schema_version < 3:
            result.add_warning(
                "Legacy tuning metadata schema does not record primary/foreign-key or column-attribute drift"
            )
        expected_constraints_hash = self._hash_section(constraints_payload)
        expected_platform_opt_hash = self._hash_section(unified_config.platform_optimizations.to_dict())

        existing_constraints_hash = existing_markers.get(self._TUNING_TYPE_CONSTRAINTS_HASH)
        existing_platform_opt_hash = existing_markers.get(self._TUNING_TYPE_PLATFORM_OPT_HASH)
        existing_table_attributes_hash = existing_markers.get(self._TUNING_TYPE_TABLE_ATTRIBUTES_HASH)

        if schema_version >= 3:
            required_markers = {
                self._TUNING_TYPE_CONSTRAINTS_HASH,
                self._TUNING_TYPE_PLATFORM_OPT_HASH,
                self._TUNING_TYPE_TABLE_ATTRIBUTES_HASH,
            }
            missing_markers = sorted(required_markers - existing_markers.keys())
            if missing_markers:
                result.add_error(
                    "Incomplete tuning metadata section markers; database reuse is unsafe "
                    f"(missing: {', '.join(missing_markers)})"
                )
                return

        if existing_constraints_hash is not None and existing_constraints_hash != expected_constraints_hash:
            result.drifted_sections.add(self._CONSTRAINTS_SECTION)
            result.add_error(
                "Primary/foreign/unique/check constraint configuration drift detected: persisted database metadata "
                "does not match the expected configuration (constraint enablement changed since the database was tuned)."
            )

        if existing_platform_opt_hash is not None and existing_platform_opt_hash != expected_platform_opt_hash:
            result.drifted_sections.add(self._PLATFORM_OPTIMIZATIONS_SECTION)
            result.add_error(
                "Platform-optimization configuration drift detected: persisted database metadata "
                "does not match the expected configuration (e.g. z-ordering, liquid clustering, "
                "bloom filters, auto-optimize/compact, or materialized views changed since the "
                "database was tuned)."
            )

        if schema_version >= 3:
            table_attributes_payload = self._table_attributes_payload(unified_config)
            if existing_table_attributes_hash != self._hash_section(table_attributes_payload):
                result.drifted_sections.add(self._TABLE_ATTRIBUTES_SECTION)
                result.configuration_mismatches[self._TABLE_ATTRIBUTES_SECTION] = (
                    "Persisted column attributes do not match expected sort order, null placement, compression, or type"
                )
                result.add_error("Table tuning column attributes drifted from persisted database metadata")

    _INDEX_PLAIN_FORM_PLATFORMS = frozenset(
        {
            "duckdb",
            "motherduck",
            "sqlite",
            "postgresql",
            "timescaledb",
            "pg_duckdb",
            "pg_mooncake",
            "paradedb",
            "citus",
            "cedardb",
        }
    )
    _INDEX_TSQL_PLATFORMS = frozenset({"synapse", "fabric_dw"})

    def _metadata_unsupported_reason(self) -> Optional[str]:
        if self._platform_key() == "athena":
            return (
                "Tuning metadata is not supported on Athena: the metadata table "
                "needs an Iceberg table with an explicit warehouse LOCATION, which "
                "BenchBox does not provision. Runs continue without persisted tuning "
                "metadata (reuse and drift detection stay disabled)."
            )
        return None

    def create_metadata_table(self) -> bool:
        if self._table_exists:
            return True

        unsupported = self._metadata_unsupported_reason()
        if unsupported is not None:
            self.logger.error(unsupported)
            return False

        try:
            create_sql = self._get_create_table_sql()

            self.logger.info(f"Creating tuning metadata table: {self._metadata_table_name}")

            with self._managed_connection() as conn:
                self._execute_sql(conn, create_sql)
        except Exception as e:
            self.logger.error(f"Failed to create metadata table: {e}")
            return False

        self._table_exists = True

        index_sql = self._get_create_index_sql()
        if index_sql:
            try:
                with self._managed_connection() as conn:
                    self._execute_sql(conn, index_sql)
            except Exception as e:
                self.logger.warning(f"Failed to create tuning metadata index (non-fatal): {e}")
        return True

    def _get_create_table_sql(self) -> str:
        platform = self._platform_key()

        base_sql = f"""
        CREATE TABLE IF NOT EXISTS {self._metadata_table_name} (
            table_name VARCHAR(255) NOT NULL,
            tuning_type VARCHAR(50) NOT NULL,
            column_name VARCHAR(255) NOT NULL,
            column_order INTEGER NOT NULL,
            configuration_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            platform VARCHAR(50) NOT NULL
        )"""

        if platform == "bigquery":
            bigquery_sql = base_sql.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
            for narrow, broad in (
                ("VARCHAR(255)", "STRING"),
                ("VARCHAR(50)", "STRING"),
                ("VARCHAR(64)", "STRING"),
                ("INTEGER", "INT64"),
                ("TIMESTAMP", "TIMESTAMP"),
            ):
                bigquery_sql = bigquery_sql.replace(narrow, broad)
            return bigquery_sql
        elif platform == "snowflake":
            return base_sql.replace("TIMESTAMP", "TIMESTAMP_NTZ")
        elif platform == "redshift":
            return base_sql
        elif platform in {"clickhouse", "clickhouse_local", "clickhouse_server", "clickhouse_cloud"}:
            return base_sql + " ENGINE = MergeTree() ORDER BY (table_name, tuning_type)"
        else:
            return base_sql

    def _get_create_index_sql(self) -> Optional[str]:
        platform = self._platform_key()
        index_name = f"idx_{self._metadata_table_name}_lookup"

        if (
            platform in {"clickhouse", "clickhouse_local", "clickhouse_server", "clickhouse_cloud"}
            or platform == "bigquery"
        ):
            return None
        elif platform in self._INDEX_PLAIN_FORM_PLATFORMS:
            return f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {self._metadata_table_name} (table_name, configuration_hash)
            """
        elif platform in self._INDEX_TSQL_PLATFORMS:
            return f"""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = '{index_name}'
            AND object_id = OBJECT_ID('{self._metadata_table_name}'))
            CREATE INDEX {index_name}
            ON {self._metadata_table_name} (table_name, configuration_hash)
            """
        else:
            return None

    def save_tunings(self, benchmark_tunings: BenchmarkTunings) -> bool:
        try:
            if not self.create_metadata_table():
                return False

            self.clear_tunings(benchmark_tunings.benchmark_name)

            config_hash = benchmark_tunings.get_configuration_hash()
            platform = self._platform_key()
            current_time = datetime.now()

            records = []
            for table_name in benchmark_tunings.get_table_names():
                table_tuning = benchmark_tunings.get_table_tuning(table_name)
                if not table_tuning:
                    continue

                for tuning_type in TuningType:
                    columns = table_tuning.get_columns_by_type(tuning_type)
                    if not columns:
                        continue

                    for column in columns:
                        records.append(
                            TuningMetadata(
                                table_name=table_name,
                                tuning_type=tuning_type.value,
                                column_name=column.name,
                                column_order=column.order,
                                configuration_hash=config_hash,
                                created_at=current_time,
                                platform=platform,
                            )
                        )

            if records:
                self._batch_insert_records(records)
                self.logger.info(f"Saved {len(records)} tuning metadata records")

            return True

        except Exception as e:
            self.logger.error(f"Failed to save tunings: {e}")
            return False

    def _as_benchmark_tunings(
        self, unified_config: UnifiedTuningConfiguration, benchmark_name: str = "unified"
    ) -> BenchmarkTunings:
        return BenchmarkTunings(
            benchmark_name=benchmark_name,
            enable_primary_keys=unified_config.primary_keys.enabled,
            enable_foreign_keys=unified_config.foreign_keys.enabled,
            table_tunings=unified_config.table_tunings.copy(),
        )

    def _as_unified_tunings(self, benchmark_tunings: BenchmarkTunings) -> UnifiedTuningConfiguration:
        unified = UnifiedTuningConfiguration()
        unified.primary_keys.enabled = benchmark_tunings.enable_primary_keys
        unified.foreign_keys.enabled = benchmark_tunings.enable_foreign_keys
        unified.table_tunings.update(benchmark_tunings.table_tunings)
        return unified

    def save_unified_tunings(self, unified_config: UnifiedTuningConfiguration) -> bool:
        try:
            if not isinstance(unified_config, UnifiedTuningConfiguration):
                raise TypeError("Expected UnifiedTuningConfiguration")
            saved = self.save_tunings(self._as_benchmark_tunings(unified_config))
            if saved:
                if not self._save_section_markers(unified_config):
                    self.write_tuned_run_marker()
            else:
                self.write_tuned_run_marker()
            return saved
        except Exception as e:
            self.logger.error(f"Failed to save unified tunings: {e}")
            return False

    def load_unified_tunings(self) -> Optional[UnifiedTuningConfiguration]:
        try:
            benchmark_tunings = self.load_tunings()
            if benchmark_tunings is None:
                return None
            return self._as_unified_tunings(benchmark_tunings)
        except Exception as e:
            self.logger.error(f"Failed to load unified tunings: {e}")
            return None

    def validate_unified_tunings(self, unified_config: UnifiedTuningConfiguration) -> MetadataValidationResult:
        try:
            if not isinstance(unified_config, UnifiedTuningConfiguration):
                raise TypeError("Expected UnifiedTuningConfiguration")
            result = self.validate_tunings(self._as_benchmark_tunings(unified_config))
            if self._table_exists_check():
                self._compare_section_hashes(unified_config, result)
            return result
        except Exception as e:
            result = MetadataValidationResult(is_valid=False)
            result.add_error(f"Validation failed with error: {e}")
            return result

    def _format_literal(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return "'" + str(value).replace("'", "''") + "'"

    def _batch_insert_records(self, records: list[TuningMetadata]) -> None:
        if not records:
            return

        insert_sql = f"""
        INSERT INTO {self._metadata_table_name}
        (table_name, tuning_type, column_name, column_order,
         configuration_hash, created_at, platform)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """

        param_lists = []
        for record in records:
            param_lists.append(
                [
                    record.table_name,
                    record.tuning_type,
                    record.column_name,
                    record.column_order,
                    record.configuration_hash,
                    record.created_at,
                    record.platform,
                ]
            )

        with self._managed_connection() as conn:
            if not hasattr(conn, "cursor"):
                for params in param_lists:
                    values = ", ".join(self._format_literal(value) for value in params)
                    insert_sql = f"""
        INSERT INTO {self._metadata_table_name}
        (table_name, tuning_type, column_name, column_order,
         configuration_hash, created_at, platform)
        VALUES ({values})
        """
                    self._execute_sql(conn, insert_sql)
                return
            cursor = conn.cursor()
            for params in param_lists:
                res = cursor.execute(insert_sql, params)
                target = res if res is not None else cursor
                if (err := failed_platform_error(target)) is not None:
                    raise RuntimeError(f"Failed to insert tuning metadata: {err}")
            conn.commit()

    def load_tunings(self, benchmark_name: Optional[str] = None) -> Optional[BenchmarkTunings]:
        try:
            self.last_load_error = None
            if not self._table_exists_check():
                return None

            query_sql = f"""
            SELECT table_name, tuning_type, column_name, column_order,
                   configuration_hash, created_at, platform
            FROM {self._metadata_table_name}
            ORDER BY table_name, tuning_type, column_order
            """

            with self._managed_connection() as conn:
                results = self._fetch_all(conn, query_sql)
            if not results:
                return None

            return self._rebuild_tunings_from_records(results, benchmark_name or "loaded")

        except Exception as e:
            self.last_load_error = str(e)
            self.logger.error(f"Failed to load tunings: {e}")
            return None

    def _table_exists_check(self) -> bool:
        if self._table_exists is not None:
            return self._table_exists

        try:
            query_sql = f"SELECT COUNT(*) FROM {self._metadata_table_name} LIMIT 1"
            with self._managed_connection() as conn:
                self._fetch_one(conn, query_sql)
                self._table_exists = True
                return True
        except Exception as exc:
            if not self._is_missing_metadata_table_error(exc):
                self.last_load_error = str(exc)
            self._table_exists = False
            return False

    def _rebuild_tunings_from_records(self, records: list[tuple], benchmark_name: str) -> BenchmarkTunings:
        benchmark_tunings = BenchmarkTunings(benchmark_name=benchmark_name)

        tables = {}
        for record in records:
            (
                table_name,
                tuning_type,
                column_name,
                column_order,
                config_hash,
                created_at,
                platform,
            ) = record

            if table_name == self._SECTION_MARKER_TABLE:
                continue
            if tuning_type not in _COLUMN_TUNING_TYPE_VALUES:
                continue

            if table_name not in tables:
                tables[table_name] = {
                    TuningType.PARTITIONING.value: [],
                    TuningType.CLUSTERING.value: [],
                    TuningType.DISTRIBUTION.value: [],
                    TuningType.SORTING.value: [],
                }

            tables[table_name][tuning_type].append(
                TuningColumn(
                    name=column_name,
                    type="UNKNOWN",
                    order=column_order,
                )
            )

        for table_name, tuning_columns in tables.items():
            table_tuning = TableTuning(
                table_name=table_name,
                partitioning=tuning_columns[TuningType.PARTITIONING.value] or None,
                clustering=tuning_columns[TuningType.CLUSTERING.value] or None,
                distribution=tuning_columns[TuningType.DISTRIBUTION.value] or None,
                sorting=tuning_columns[TuningType.SORTING.value] or None,
            )

            if table_tuning.has_any_tuning():
                benchmark_tunings.add_table_tuning(table_tuning)

        return benchmark_tunings

    def validate_tunings(self, expected_tunings: BenchmarkTunings) -> MetadataValidationResult:
        result = MetadataValidationResult()

        try:
            existing_tunings = self.load_tunings(expected_tunings.benchmark_name)

            if existing_tunings is None:
                if self.last_load_error:
                    result.add_error(f"Failed to load tuning metadata: {self.last_load_error}")
                else:
                    result.add_error(NO_TUNING_METADATA_ERROR)
                return result

            self._compare_tuning_configurations(expected_tunings, existing_tunings, result)

            if result.is_valid:
                self.logger.info("Tuning configuration validation passed")
            else:
                self.logger.warning(f"Tuning validation failed with {len(result.errors)} errors")

            return result

        except Exception as e:
            result.add_error(f"Validation failed with error: {e}")
            return result

    def _compare_tuning_configurations(
        self,
        expected: BenchmarkTunings,
        existing: BenchmarkTunings,
        result: MetadataValidationResult,
    ) -> None:
        expected_hash = expected.get_configuration_hash()
        existing_hash = existing.get_configuration_hash()

        if expected_hash == existing_hash:
            return

        expected_tables = set(expected.get_table_names())
        existing_tables = set(existing.get_table_names())

        result.missing_tables = expected_tables - existing_tables
        result.extra_tables = existing_tables - expected_tables

        for table_name in result.missing_tables:
            result.add_error(f"Expected tuning for table '{table_name}' not found in database")

        for table_name in result.extra_tables:
            result.add_error(f"Unexpected tuning found for table '{table_name}' in database")

        common_tables = expected_tables & existing_tables
        for table_name in common_tables:
            self._compare_table_tunings(
                expected.get_table_tuning(table_name),
                existing.get_table_tuning(table_name),
                result,
            )

    def _compare_table_tunings(
        self,
        expected: Optional[TableTuning],
        existing: Optional[TableTuning],
        result: MetadataValidationResult,
    ) -> None:
        if not expected or not existing:
            return

        table_name = expected.table_name

        for tuning_type in TuningType:
            expected_columns = expected.get_columns_by_type(tuning_type)
            existing_columns = existing.get_columns_by_type(tuning_type)

            expected_spec = sorted([(col.name, col.order) for col in expected_columns])
            existing_spec = sorted([(col.name, col.order) for col in existing_columns])

            if expected_spec != existing_spec:
                result.configuration_mismatches[f"{table_name}.{tuning_type.value}"] = (
                    f"Expected: {expected_spec}, Found: {existing_spec}"
                )
                result.add_error(
                    f"Table '{table_name}' {tuning_type.value} tuning mismatch: "
                    f"expected {expected_spec}, found {existing_spec}"
                )

    def clear_tunings(self, benchmark_name: Optional[str] = None) -> bool:
        try:
            if not self._table_exists_check():
                return True

            delete_sql = f"DELETE FROM {self._metadata_table_name} WHERE TRUE"
            with self._managed_connection() as conn:
                self._execute_sql(conn, delete_sql)

            self.logger.info("Cleared tuning metadata")
            return True

        except Exception as e:
            self.logger.error(f"Failed to clear tunings: {e}")
            return False

    def get_metadata_summary(self) -> dict[str, Any]:
        try:
            if not self._table_exists_check():
                return {"table_exists": False}

            summary_sql = f"""
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT table_name) as unique_tables,
                COUNT(DISTINCT tuning_type) as unique_tuning_types,
                COUNT(DISTINCT platform) as unique_platforms,
                MIN(created_at) as oldest_record,
                MAX(created_at) as newest_record
            FROM {self._metadata_table_name}
            WHERE table_name != '{self._SECTION_MARKER_TABLE}'
            """

            with self._managed_connection() as conn:
                result = self._fetch_one(conn, summary_sql)
            if result:
                return {
                    "table_exists": True,
                    "total_records": result[0],
                    "unique_tables": result[1],
                    "unique_tuning_types": result[2],
                    "unique_platforms": result[3],
                    "oldest_record": result[4],
                    "newest_record": result[5],
                }

            return {"table_exists": True, "no_data": True}

        except Exception as e:
            return {"table_exists": False, "error": str(e)}

    def _execute_sql(self, connection, sql: str, params: Optional[list] = None) -> Any:
        if hasattr(self.platform_adapter, "execute_query"):
            if not hasattr(connection, "cursor"):
                sql = self._qualify_metadata_read_sql(sql)
            result = self.platform_adapter.execute_query(connection, sql, "metadata")
            if (err := failed_platform_error(result)) is not None:
                raise RuntimeError(f"Tuning metadata execution failed: {err}")
            return result.get("result")
        else:
            cursor = connection.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            if (err := failed_platform_error(cursor)) is not None:
                raise RuntimeError(f"Tuning metadata execution failed: {err}")

            try:
                return cursor.fetchall()
            except Exception:
                return None

    def _qualify_metadata_read_sql(self, sql: str) -> str:
        qualify = getattr(self.platform_adapter, "_qualify_table_names", None)
        if callable(qualify):
            return qualify(sql)
        return sql

    def _fetch_all(self, connection, sql: str) -> list[tuple]:
        if not hasattr(connection, "cursor") and hasattr(connection, "execute"):
            res = connection.execute(sql)
            if (err := failed_platform_error(res)) is not None:
                raise RuntimeError(f"Tuning metadata query failed: {err}")
            return list(res)

        if not hasattr(connection, "cursor"):
            query_fn = getattr(connection, "query", None)
            if callable(query_fn):
                return list(query_fn(self._qualify_metadata_read_sql(sql)).result())

        cursor = connection.cursor()
        cursor.execute(sql)
        if (err := failed_platform_error(cursor)) is not None:
            raise RuntimeError(f"Tuning metadata query failed: {err}")
        return cursor.fetchall()

    def _fetch_one(self, connection, sql: str) -> Optional[tuple]:
        if not hasattr(connection, "cursor") and hasattr(connection, "execute"):
            res = connection.execute(sql)
            if (err := failed_platform_error(res)) is not None:
                raise RuntimeError(f"Tuning metadata query failed: {err}")
            rows = list(res)
            return rows[0] if rows else None

        if not hasattr(connection, "cursor"):
            query_fn = getattr(connection, "query", None)
            if callable(query_fn):
                rows = list(query_fn(self._qualify_metadata_read_sql(sql)).result())
                return rows[0] if rows else None

        cursor = connection.cursor()
        cursor.execute(sql)
        if (err := failed_platform_error(cursor)) is not None:
            raise RuntimeError(f"Tuning metadata query failed: {err}")
        return cursor.fetchone()
