# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from benchbox.core.dataframe_manager_factory import get_dataframe_manager

logger = logging.getLogger(__name__)


def _module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


class MetadataOperationType(Enum):
    LIST_COLUMNS = "list_columns"
    GET_DTYPES = "get_dtypes"
    GET_SCHEMA = "get_schema"
    DESCRIBE_STATS = "describe_stats"
    ROW_COUNT = "row_count"
    COLUMN_COUNT = "column_count"

    LIST_DATABASES = "list_databases"
    LIST_TABLES = "list_tables"
    LIST_TABLE_COLUMNS = "list_table_columns"
    TABLE_EXISTS = "table_exists"
    GET_TABLE_INFO = "get_table_info"

    TABLE_HISTORY = "table_history"
    TABLE_DETAIL = "table_detail"
    FILE_METADATA = "file_metadata"
    PARTITION_INFO = "partition_info"
    SNAPSHOT_INFO = "snapshot_info"

    WIDE_TABLE_SCHEMA = "wide_table_schema"
    LARGE_CATALOG_LIST = "large_catalog_list"
    COMPLEX_TYPE_INTROSPECTION = "complex_type_introspection"


class MetadataOperationCategory(Enum):
    SCHEMA = "schema"
    CATALOG = "catalog"
    LAKEHOUSE = "lakehouse"
    COMPLEXITY = "complexity"


_Op = MetadataOperationType
_Cat = MetadataOperationCategory
_SCHEMA_OPS = (_Op.LIST_COLUMNS, _Op.GET_DTYPES, _Op.GET_SCHEMA, _Op.DESCRIBE_STATS, _Op.ROW_COUNT, _Op.COLUMN_COUNT)
_CATALOG_OPS = (_Op.LIST_DATABASES, _Op.LIST_TABLES, _Op.LIST_TABLE_COLUMNS, _Op.TABLE_EXISTS, _Op.GET_TABLE_INFO)
_LAKEHOUSE_OPS = (_Op.TABLE_HISTORY, _Op.TABLE_DETAIL, _Op.FILE_METADATA, _Op.PARTITION_INFO, _Op.SNAPSHOT_INFO)
_COMPLEXITY_OPS = (_Op.WIDE_TABLE_SCHEMA, _Op.LARGE_CATALOG_LIST, _Op.COMPLEX_TYPE_INTROSPECTION)
OPERATION_CATEGORIES = {
    op: category
    for category, operations in {
        _Cat.SCHEMA: _SCHEMA_OPS,
        _Cat.CATALOG: _CATALOG_OPS,
        _Cat.LAKEHOUSE: _LAKEHOUSE_OPS,
        _Cat.COMPLEXITY: _COMPLEXITY_OPS,
    }.items()
    for op in operations
}


@dataclass
class DataFrameMetadataCapabilities:
    platform_name: str
    supports_schema_introspection: bool = True
    supports_describe: bool = True
    supports_catalog: bool = False
    supports_delta_lake: bool = False
    supports_iceberg: bool = False
    supports_partitions: bool = False
    supports_complex_types: bool = True
    notes: str = ""

    def supports_operation(self, operation: MetadataOperationType) -> bool:
        checks = {
            MetadataOperationType.DESCRIBE_STATS: self.supports_describe,
            MetadataOperationType.TABLE_HISTORY: self.supports_delta_lake,
            MetadataOperationType.TABLE_DETAIL: self.supports_delta_lake,
            MetadataOperationType.FILE_METADATA: self.supports_delta_lake or self.supports_iceberg,
            MetadataOperationType.PARTITION_INFO: self.supports_partitions,
            MetadataOperationType.SNAPSHOT_INFO: self.supports_iceberg,
            MetadataOperationType.WIDE_TABLE_SCHEMA: self.supports_schema_introspection,
            MetadataOperationType.LARGE_CATALOG_LIST: self.supports_catalog,
            MetadataOperationType.COMPLEX_TYPE_INTROSPECTION: self.supports_complex_types,
        }
        if operation in checks:
            return checks[operation]
        if operation in _SCHEMA_OPS:
            return self.supports_schema_introspection
        return self.supports_catalog if operation in _CATALOG_OPS else False

    def get_supported_operations(self) -> list[MetadataOperationType]:
        return [op for op in MetadataOperationType if self.supports_operation(op)]

    def get_unsupported_operations(self) -> list[MetadataOperationType]:
        return [op for op in MetadataOperationType if not self.supports_operation(op)]

    def get_supported_categories(self) -> list[MetadataOperationCategory]:
        categories = {OPERATION_CATEGORIES[op] for op in self.get_supported_operations() if op in OPERATION_CATEGORIES}
        return sorted(categories, key=lambda c: c.value)


def _metadata_capabilities(platform_name: str, notes: str, **overrides: Any) -> DataFrameMetadataCapabilities:
    return DataFrameMetadataCapabilities(platform_name=platform_name, notes=notes, **overrides)


POLARS_METADATA_CAPABILITIES = _metadata_capabilities(
    "polars-df",
    "Schema introspection via df.schema, df.dtypes. No catalog support.",
)
PANDAS_METADATA_CAPABILITIES = _metadata_capabilities(
    "pandas-df",
    "Schema introspection via df.dtypes, df.info(). No catalog support.",
    supports_complex_types=False,
)
DASK_METADATA_CAPABILITIES = _metadata_capabilities(
    "dask-df",
    "Schema introspection via df.dtypes; describe, null and row counts compute lazily. No catalog support.",
    supports_complex_types=False,
)
PYSPARK_METADATA_CAPABILITIES = _metadata_capabilities(
    "pyspark-df",
    "Full catalog support via spark.catalog. Delta Lake/Iceberg require packages.",
    supports_catalog=True,
    supports_partitions=True,
)
DATAFUSION_METADATA_CAPABILITIES = _metadata_capabilities(
    "datafusion-df",
    "Schema introspection via DataFrame schema. Limited catalog support.",
)


def get_platform_capabilities(platform_name: str, **kwargs: Any) -> DataFrameMetadataCapabilities:
    platform_lower = platform_name.lower()

    if "polars" in platform_lower:
        base = POLARS_METADATA_CAPABILITIES
    elif "dask" in platform_lower:
        base = DASK_METADATA_CAPABILITIES
    elif "pandas" in platform_lower:
        base = PANDAS_METADATA_CAPABILITIES
    elif "pyspark" in platform_lower or "spark" in platform_lower:
        base = PYSPARK_METADATA_CAPABILITIES
    elif "datafusion" in platform_lower:
        base = DATAFUSION_METADATA_CAPABILITIES
    else:
        base = DataFrameMetadataCapabilities(
            platform_name=platform_name,
            supports_schema_introspection=True,
            supports_describe=True,
            notes="Unknown platform - basic schema introspection only.",
        )

    return replace(base, platform_name=platform_name, **kwargs) if kwargs else base


@dataclass
class DataFrameMetadataResult:
    operation_type: MetadataOperationType
    success: bool
    start_time: float
    end_time: float
    duration_ms: float
    result_count: int = 0
    result_data: Any = None
    error_message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success_result(
        cls,
        operation_type: MetadataOperationType,
        start_time: float,
        result_count: int,
        result_data: Any = None,
        metrics: dict[str, Any] | None = None,
    ) -> DataFrameMetadataResult:
        end_time = time.time()
        return cls(
            operation_type=operation_type,
            success=True,
            start_time=start_time,
            end_time=end_time,
            duration_ms=(end_time - start_time) * 1000,
            result_count=result_count,
            result_data=result_data,
            metrics=metrics or {},
        )

    @classmethod
    def failure_result(
        cls,
        operation_type: MetadataOperationType,
        error_message: str,
        start_time: float | None = None,
    ) -> DataFrameMetadataResult:
        now = time.time()
        return cls(
            operation_type=operation_type,
            success=False,
            start_time=start_time or now,
            end_time=now,
            duration_ms=0.0 if start_time is None else (now - start_time) * 1000,
            result_count=0,
            error_message=error_message,
        )


class UnsupportedOperationError(Exception):
    def __init__(
        self,
        operation: MetadataOperationType,
        platform_name: str,
        suggestion: str | None = None,
    ):
        self.operation = operation
        self.platform_name = platform_name
        self.suggestion = suggestion

        message = f"{platform_name} does not support {operation.value} operations."
        if suggestion:
            message += f"\n{suggestion}"

        super().__init__(message)


def get_unsupported_message(operation: MetadataOperationType, platform_name: str) -> str:
    category = OPERATION_CATEGORIES.get(operation, MetadataOperationCategory.SCHEMA)

    if category == MetadataOperationCategory.CATALOG:
        return (
            f"{platform_name} does not support catalog operations ({operation.value}).\n"
            f"Catalog operations require PySpark with a configured catalog.\n"
            f"Alternatives:\n"
            f"  - Use schema introspection operations (df.schema, df.dtypes)\n"
            f"  - Switch to pyspark-df platform with Hive metastore or Unity Catalog"
        )

    if category == MetadataOperationCategory.LAKEHOUSE:
        if "delta" in operation.value.lower() or operation in (
            MetadataOperationType.TABLE_HISTORY,
            MetadataOperationType.TABLE_DETAIL,
        ):
            return (
                f"{platform_name} does not support Delta Lake metadata operations ({operation.value}).\n"
                f"Delta Lake operations require:\n"
                f"  - pyspark-df with delta-spark package, or\n"
                f"  - polars with delta support (read-only)"
            )
        if "iceberg" in operation.value.lower() or operation == MetadataOperationType.SNAPSHOT_INFO:
            return (
                f"{platform_name} does not support Iceberg metadata operations ({operation.value}).\n"
                f"Iceberg operations require:\n"
                f"  - pyspark-df with iceberg-spark package, or\n"
                f"  - pyiceberg library"
            )
        return (
            f"{platform_name} does not support lakehouse metadata operations ({operation.value}).\n"
            f"Use pyspark-df with Delta Lake or Iceberg table format."
        )

    return f"{platform_name} does not support {operation.value} operations."


class DataFrameMetadataOperationsManager:
    def __init__(
        self,
        platform_name: str,
        spark_session: Any = None,
        delta_available: bool | None = None,
        iceberg_available: bool | None = None,
    ) -> None:
        self.platform_name = platform_name.lower()
        self.spark_session = spark_session
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        overrides = {}

        if "pyspark" in self.platform_name or "spark" in self.platform_name:
            if delta_available is None:
                delta_available = self._detect_delta_lake()
            if iceberg_available is None:
                iceberg_available = self._detect_iceberg()

            overrides["supports_delta_lake"] = delta_available
            overrides["supports_iceberg"] = iceberg_available

        self._capabilities = get_platform_capabilities(self.platform_name, **overrides)

    def _detect_delta_lake(self) -> bool:
        return _module_available("delta") or _module_available("deltalake")

    def _detect_iceberg(self) -> bool:
        if _module_available("pyiceberg"):
            return True

        if self.spark_session is not None:
            try:
                catalogs = self.spark_session.conf.get("spark.sql.catalog", "")
                if "iceberg" in catalogs.lower():
                    return True
            except Exception:
                pass

        return False

    def get_capabilities(self) -> DataFrameMetadataCapabilities:
        return self._capabilities

    def supports_operation(self, operation: MetadataOperationType) -> bool:
        return self._capabilities.supports_operation(operation)

    def get_supported_operations(self) -> list[MetadataOperationType]:
        return self._capabilities.get_supported_operations()

    def validate_operation(self, operation: MetadataOperationType) -> None:
        if not self.supports_operation(operation):
            raise UnsupportedOperationError(
                operation, self.platform_name, get_unsupported_message(operation, self.platform_name)
            )

    def _run_operation(
        self, operation: MetadataOperationType, action: Any, unavailable_prefix: str | None = None
    ) -> DataFrameMetadataResult:
        start_time = time.time()
        try:
            self.validate_operation(operation)
            return action(start_time)
        except UnsupportedOperationError:
            raise
        except ImportError as e:
            if unavailable_prefix is None:
                raise
            return DataFrameMetadataResult.failure_result(operation, f"{unavailable_prefix}: {e}", start_time)
        except Exception as e:
            self.logger.error(f"{operation.name} failed: {e}")
            return DataFrameMetadataResult.failure_result(operation, str(e), start_time)

    def _success(
        self,
        operation: MetadataOperationType,
        start_time: float,
        result_count: int,
        result_data: Any = None,
        metrics: dict[str, Any] | None = None,
    ) -> DataFrameMetadataResult:
        return DataFrameMetadataResult.success_result(operation, start_time, result_count, result_data, metrics)

    def _catalog_failure(self, operation: MetadataOperationType, start_time: float) -> DataFrameMetadataResult | None:
        if self.spark_session is not None:
            return None
        return DataFrameMetadataResult.failure_result(
            operation, "SparkSession is required for catalog operations.", start_time
        )

    @staticmethod
    def _catalog_name(table_name: str, database: str | None = None) -> str:
        return f"{database}.{table_name}" if database else table_name

    def execute_list_columns(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                columns = dataframe.columns
            elif "pandas" in self.platform_name or "dask" in self.platform_name:
                columns = list(dataframe.columns)
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                columns = dataframe.columns
            elif "datafusion" in self.platform_name:
                columns = [field.name for field in dataframe.schema()]
            else:
                columns = list(getattr(dataframe, "columns", []))

            return self._success(MetadataOperationType.LIST_COLUMNS, start_time, len(columns), columns)

        return self._run_operation(MetadataOperationType.LIST_COLUMNS, action)

    def execute_get_dtypes(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                dtypes = {col: str(dtype) for col, dtype in zip(dataframe.columns, dataframe.dtypes)}
            elif "pandas" in self.platform_name or "dask" in self.platform_name:
                dtypes = {col: str(dtype) for col, dtype in dataframe.dtypes.items()}
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                dtypes = {field.name: str(field.dataType) for field in dataframe.schema.fields}
            elif "datafusion" in self.platform_name:
                dtypes = {field.name: str(field.type) for field in dataframe.schema()}
            else:
                dtypes = {}
                if hasattr(dataframe, "dtypes"):
                    dtypes = dict(dataframe.dtypes)

            return self._success(MetadataOperationType.GET_DTYPES, start_time, len(dtypes), dtypes)

        return self._run_operation(MetadataOperationType.GET_DTYPES, action)

    def execute_get_schema(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            schema_info: list[dict[str, Any]] = []

            if "polars" in self.platform_name:
                for col, dtype in zip(dataframe.columns, dataframe.dtypes):
                    schema_info.append(
                        {
                            "name": col,
                            "dtype": str(dtype),
                            "nullable": True,
                        }
                    )
            elif "dask" in self.platform_name:
                nullable = dataframe.isna().any().compute()
                for col, dtype in dataframe.dtypes.items():
                    schema_info.append({"name": col, "dtype": str(dtype), "nullable": bool(nullable[col])})
            elif "pandas" in self.platform_name:
                for col, dtype in dataframe.dtypes.items():
                    schema_info.append(
                        {
                            "name": col,
                            "dtype": str(dtype),
                            "nullable": dataframe[col].isna().any(),
                        }
                    )
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                for field in dataframe.schema.fields:
                    schema_info.append(
                        {
                            "name": field.name,
                            "dtype": str(field.dataType),
                            "nullable": field.nullable,
                        }
                    )
            elif "datafusion" in self.platform_name:
                for field in dataframe.schema():
                    schema_info.append(
                        {
                            "name": field.name,
                            "dtype": str(field.type),
                            "nullable": field.nullable,
                        }
                    )
            else:
                if hasattr(dataframe, "columns") and hasattr(dataframe, "dtypes"):
                    for col, dtype in zip(dataframe.columns, dataframe.dtypes):
                        schema_info.append({"name": col, "dtype": str(dtype), "nullable": True})

            return self._success(MetadataOperationType.GET_SCHEMA, start_time, len(schema_info), schema_info)

        return self._run_operation(MetadataOperationType.GET_SCHEMA, action)

    def execute_describe_stats(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                stats_df = dataframe.describe()
                result_data = stats_df.to_dicts()
                result_count = stats_df.height
            elif "pandas" in self.platform_name:
                stats_df = dataframe.describe()
                result_data = stats_df.to_dict()
                result_count = len(stats_df)
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                stats_df = dataframe.describe()
                result_data = stats_df.collect()
                result_count = stats_df.count()
            elif "dask" in self.platform_name:
                stats_df = dataframe.describe().compute()
                result_data = stats_df.to_dict()
                result_count = len(stats_df)
            elif "datafusion" in self.platform_name:
                result_data = None
                result_count = 0
            else:
                result_data = None
                result_count = 0

            return self._success(MetadataOperationType.DESCRIBE_STATS, start_time, result_count, result_data)

        return self._run_operation(MetadataOperationType.DESCRIBE_STATS, action)

    def execute_row_count(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                count = dataframe.height
            elif "pandas" in self.platform_name or "dask" in self.platform_name:
                count = len(dataframe)
            elif "pyspark" in self.platform_name or "spark" in self.platform_name or "datafusion" in self.platform_name:
                count = dataframe.count()
            else:
                count = len(dataframe) if hasattr(dataframe, "__len__") else 0

            return self._success(MetadataOperationType.ROW_COUNT, start_time, 1, count, {"row_count": count})

        return self._run_operation(MetadataOperationType.ROW_COUNT, action)

    def execute_column_count(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                count = dataframe.width
            elif (
                "pandas" in self.platform_name
                or "dask" in self.platform_name
                or "pyspark" in self.platform_name
                or "spark" in self.platform_name
            ):
                count = len(dataframe.columns)
            elif "datafusion" in self.platform_name:
                count = len(dataframe.schema())
            else:
                count = len(getattr(dataframe, "columns", []))

            return self._success(MetadataOperationType.COLUMN_COUNT, start_time, 1, count, {"column_count": count})

        return self._run_operation(MetadataOperationType.COLUMN_COUNT, action)

    def execute_list_databases(self) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.LIST_DATABASES, start_time)
            if failure is not None:
                failure.error_message = (
                    "SparkSession is required for catalog operations. "
                    "Pass spark_session to DataFrameMetadataOperationsManager."
                )
                return failure
            databases = self.spark_session.catalog.listDatabases()
            db_names = [db.name for db in databases]
            return self._success(MetadataOperationType.LIST_DATABASES, start_time, len(db_names), db_names)

        return self._run_operation(MetadataOperationType.LIST_DATABASES, action)

    def execute_list_tables(self, database: str | None = None) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.LIST_TABLES, start_time)
            if failure is not None:
                return failure
            tables = (
                self.spark_session.catalog.listTables(database) if database else self.spark_session.catalog.listTables()
            )
            table_info = [{"name": t.name, "database": t.database, "tableType": t.tableType} for t in tables]
            return self._success(MetadataOperationType.LIST_TABLES, start_time, len(table_info), table_info)

        return self._run_operation(MetadataOperationType.LIST_TABLES, action)

    def execute_list_table_columns(self, table_name: str, database: str | None = None) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.LIST_TABLE_COLUMNS, start_time)
            if failure is not None:
                return failure
            columns = (
                self.spark_session.catalog.listColumns(table_name, database)
                if database
                else self.spark_session.catalog.listColumns(table_name)
            )
            column_info = [
                {
                    "name": c.name,
                    "dataType": c.dataType,
                    "nullable": c.nullable,
                    "description": c.description,
                }
                for c in columns
            ]
            return self._success(
                MetadataOperationType.LIST_TABLE_COLUMNS,
                start_time,
                len(column_info),
                column_info,
            )

        return self._run_operation(MetadataOperationType.LIST_TABLE_COLUMNS, action)

    def execute_table_exists(self, table_name: str, database: str | None = None) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.TABLE_EXISTS, start_time)
            if failure is not None:
                return failure
            exists = self.spark_session.catalog.tableExists(self._catalog_name(table_name, database))
            return self._success(MetadataOperationType.TABLE_EXISTS, start_time, 1 if exists else 0, exists)

        return self._run_operation(MetadataOperationType.TABLE_EXISTS, action)

    def execute_get_table_info(self, table_name: str, database: str | None = None) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.GET_TABLE_INFO, start_time)
            if failure is not None:
                return failure
            table = self.spark_session.catalog.getTable(self._catalog_name(table_name, database))
            table_info = {
                "name": table.name,
                "database": table.database,
                "tableType": table.tableType,
                "description": table.description,
                "isTemporary": table.isTemporary,
            }
            return self._success(MetadataOperationType.GET_TABLE_INFO, start_time, 1, table_info)

        return self._run_operation(MetadataOperationType.GET_TABLE_INFO, action)

    def execute_table_history(self, table_path: str) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if self.spark_session is not None:
                from delta.tables import DeltaTable

                delta_table = DeltaTable.forPath(self.spark_session, table_path)
                history = delta_table.history().collect()
                return self._success(
                    MetadataOperationType.TABLE_HISTORY,
                    start_time,
                    len(history),
                    [row.asDict() for row in history],
                )
            else:
                from deltalake import DeltaTable as PyDeltaTable

                dt = PyDeltaTable(table_path)
                history = list(dt.history())
                return self._success(MetadataOperationType.TABLE_HISTORY, start_time, len(history), history)

        return self._run_operation(MetadataOperationType.TABLE_HISTORY, action, "Delta Lake library not available")

    def execute_table_detail(self, table_path: str) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if self.spark_session is not None:
                from delta.tables import DeltaTable

                delta_table = DeltaTable.forPath(self.spark_session, table_path)
                detail = delta_table.detail().collect()[0].asDict()
                return self._success(MetadataOperationType.TABLE_DETAIL, start_time, 1, detail)
            else:
                from deltalake import DeltaTable as PyDeltaTable

                dt = PyDeltaTable(table_path)
                metadata = dt.metadata()

                detail = {
                    "id": metadata.id,
                    "name": metadata.name,
                    "description": metadata.description,
                    "partitionColumns": metadata.partition_columns,
                    "createdTime": metadata.created_time,
                }
                return self._success(MetadataOperationType.TABLE_DETAIL, start_time, 1, detail)

        return self._run_operation(MetadataOperationType.TABLE_DETAIL, action, "Delta Lake library not available")

    def execute_file_metadata(self, table_path: str) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if self._capabilities.supports_delta_lake:
                if self.spark_session is not None:
                    from delta.tables import DeltaTable

                    delta_table = DeltaTable.forPath(self.spark_session, table_path)
                    detail = delta_table.detail().collect()[0]
                    files = {
                        "numFiles": detail.numFiles if hasattr(detail, "numFiles") else None,
                        "sizeInBytes": detail.sizeInBytes if hasattr(detail, "sizeInBytes") else None,
                    }
                else:
                    from deltalake import DeltaTable as PyDeltaTable

                    dt = PyDeltaTable(table_path)
                    file_uris = dt.file_uris()
                    files = {
                        "numFiles": len(file_uris),
                        "files": file_uris[:100],
                    }
                return self._success(
                    MetadataOperationType.FILE_METADATA,
                    start_time,
                    files.get("numFiles", 0),
                    files,
                )

            elif self._capabilities.supports_iceberg:
                return DataFrameMetadataResult.failure_result(
                    MetadataOperationType.FILE_METADATA,
                    "Iceberg file metadata requires catalog configuration",
                    start_time,
                )

            else:
                return DataFrameMetadataResult.failure_result(
                    MetadataOperationType.FILE_METADATA,
                    "No lakehouse format available for file metadata",
                    start_time,
                )

        return self._run_operation(MetadataOperationType.FILE_METADATA, action, "Lakehouse library not available")

    def execute_partition_info(self, table_path: str) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if self.spark_session is not None and self._capabilities.supports_delta_lake:
                from delta.tables import DeltaTable

                delta_table = DeltaTable.forPath(self.spark_session, table_path)
                detail = delta_table.detail().collect()[0]
                partitions = {
                    "partitionColumns": list(detail.partitionColumns) if detail.partitionColumns else [],
                    "numPartitions": len(detail.partitionColumns) if detail.partitionColumns else 0,
                }
                return self._success(
                    MetadataOperationType.PARTITION_INFO,
                    start_time,
                    partitions["numPartitions"],
                    partitions,
                )

            elif self._capabilities.supports_delta_lake:
                from deltalake import DeltaTable as PyDeltaTable

                dt = PyDeltaTable(table_path)
                metadata = dt.metadata()
                partitions = {
                    "partitionColumns": metadata.partition_columns,
                    "numPartitions": len(metadata.partition_columns),
                }
                return self._success(
                    MetadataOperationType.PARTITION_INFO,
                    start_time,
                    partitions["numPartitions"],
                    partitions,
                )

            else:
                return DataFrameMetadataResult.failure_result(
                    MetadataOperationType.PARTITION_INFO,
                    "Partition info requires Delta Lake or Iceberg table format",
                    start_time,
                )

        return self._run_operation(MetadataOperationType.PARTITION_INFO, action, "Lakehouse library not available")

    def execute_snapshot_info(self, table_path: str) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            return DataFrameMetadataResult.failure_result(
                MetadataOperationType.SNAPSHOT_INFO,
                "Iceberg snapshot info requires catalog configuration. "
                "Configure Iceberg catalog in your Spark session or use pyiceberg with proper catalog.",
                start_time,
            )

        return self._run_operation(MetadataOperationType.SNAPSHOT_INFO, action)

    def execute_wide_table_schema(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            if "polars" in self.platform_name:
                column_count = dataframe.width
                schema_info = [
                    {"name": col, "dtype": str(dtype)} for col, dtype in zip(dataframe.columns, dataframe.dtypes)
                ]
            elif "pandas" in self.platform_name or "dask" in self.platform_name:
                column_count = len(dataframe.columns)
                schema_info = [{"name": col, "dtype": str(dtype)} for col, dtype in dataframe.dtypes.items()]
            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                column_count = len(dataframe.columns)
                schema_info = [{"name": f.name, "dtype": str(f.dataType)} for f in dataframe.schema.fields]
            else:
                column_count = len(getattr(dataframe, "columns", []))
                schema_info = []

            return self._success(
                MetadataOperationType.WIDE_TABLE_SCHEMA,
                start_time,
                column_count,
                schema_info,
                {
                    "column_count": column_count,
                    "is_wide_table": column_count >= 100,
                },
            )

        return self._run_operation(MetadataOperationType.WIDE_TABLE_SCHEMA, action)

    def execute_large_catalog_list(self) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            failure = self._catalog_failure(MetadataOperationType.LARGE_CATALOG_LIST, start_time)
            if failure is not None:
                return failure
            tables_result = []
            databases = self.spark_session.catalog.listDatabases()

            for db in databases:
                try:
                    tables = self.spark_session.catalog.listTables(db.name)
                    for t in tables:
                        tables_result.append({"name": t.name, "database": db.name, "tableType": t.tableType})
                except Exception as e:
                    self.logger.debug(f"Skipping database {db.name}: {e}")

            table_count = len(tables_result)

            return self._success(
                MetadataOperationType.LARGE_CATALOG_LIST,
                start_time,
                table_count,
                tables_result,
                {
                    "table_count": table_count,
                    "database_count": len(databases),
                    "is_large_catalog": table_count >= 100,
                },
            )

        return self._run_operation(MetadataOperationType.LARGE_CATALOG_LIST, action)

    def execute_complex_type_introspection(self, dataframe: Any) -> DataFrameMetadataResult:

        def action(start_time: float) -> DataFrameMetadataResult:
            complex_types = []
            nested_depth = 0

            if "polars" in self.platform_name:
                import polars as pl

                for col, dtype in zip(dataframe.columns, dataframe.dtypes):
                    type_info = self._analyze_polars_type(col, dtype, pl)
                    if type_info.get("is_complex"):
                        complex_types.append(type_info)
                        nested_depth = max(nested_depth, type_info.get("nested_depth", 0))

            elif "pyspark" in self.platform_name or "spark" in self.platform_name:
                from pyspark.sql.types import ArrayType, MapType, StructType

                for field in dataframe.schema.fields:
                    type_info = self._analyze_spark_type(field, ArrayType, MapType, StructType)
                    if type_info.get("is_complex"):
                        complex_types.append(type_info)
                        nested_depth = max(nested_depth, type_info.get("nested_depth", 0))

            elif "pandas" in self.platform_name:
                for col, dtype in dataframe.dtypes.items():
                    if str(dtype) == "object":
                        complex_types.append(
                            {
                                "name": col,
                                "dtype": str(dtype),
                                "is_complex": True,
                                "complex_type": "object",
                                "nested_depth": 1,
                            }
                        )

            return self._success(
                MetadataOperationType.COMPLEX_TYPE_INTROSPECTION,
                start_time,
                len(complex_types),
                complex_types,
                {
                    "complex_column_count": len(complex_types),
                    "max_nested_depth": nested_depth,
                    "has_arrays": any(t.get("complex_type") == "array" for t in complex_types),
                    "has_structs": any(t.get("complex_type") == "struct" for t in complex_types),
                    "has_maps": any(t.get("complex_type") == "map" for t in complex_types),
                },
            )

        return self._run_operation(MetadataOperationType.COMPLEX_TYPE_INTROSPECTION, action)

    def _analyze_polars_type(self, col_name: str, dtype: Any, pl: Any) -> dict[str, Any]:
        dtype_str = str(dtype)
        is_complex = False
        complex_type = None
        nested_depth = 0

        if dtype_str.startswith("List"):
            is_complex = True
            complex_type = "array"
            nested_depth = dtype_str.count("List") + dtype_str.count("Struct")
        elif dtype_str.startswith("Struct"):
            is_complex = True
            complex_type = "struct"
            nested_depth = dtype_str.count("Struct")

        return {
            "name": col_name,
            "dtype": dtype_str,
            "is_complex": is_complex,
            "complex_type": complex_type,
            "nested_depth": nested_depth,
        }

    def _analyze_spark_type(self, field: Any, ArrayType: type, MapType: type, StructType: type) -> dict[str, Any]:
        dtype = field.dataType
        is_complex = False
        complex_type = None
        nested_depth = 0

        def count_depth(t: Any, depth: int = 0) -> int:
            if isinstance(t, ArrayType):
                return count_depth(t.elementType, depth + 1)
            elif isinstance(t, MapType):
                return max(count_depth(t.keyType, depth + 1), count_depth(t.valueType, depth + 1))
            elif isinstance(t, StructType):
                if t.fields:
                    return max(count_depth(f.dataType, depth + 1) for f in t.fields)
                return depth + 1
            return depth

        if isinstance(dtype, ArrayType):
            is_complex = True
            complex_type = "array"
            nested_depth = count_depth(dtype)
        elif isinstance(dtype, MapType):
            is_complex = True
            complex_type = "map"
            nested_depth = count_depth(dtype)
        elif isinstance(dtype, StructType):
            is_complex = True
            complex_type = "struct"
            nested_depth = count_depth(dtype)

        return {
            "name": field.name,
            "dtype": str(dtype),
            "is_complex": is_complex,
            "complex_type": complex_type,
            "nested_depth": nested_depth,
        }


def get_dataframe_metadata_manager(
    platform_name: str,
    spark_session: Any = None,
) -> DataFrameMetadataOperationsManager | None:
    return get_dataframe_manager(
        platform_name,
        manager_class=DataFrameMetadataOperationsManager,
        supported_platforms=(
            "polars-df",
            "polars",
            "pandas-df",
            "pandas",
            "dask-df",
            "dask",
            "pyspark-df",
            "pyspark",
            "datafusion",
        ),
        logger=logger,
        manager_label="metadata",
        spark_session=spark_session,
    )


__all__ = [
    "DASK_METADATA_CAPABILITIES",
    "MetadataOperationType",
    "MetadataOperationCategory",
    "OPERATION_CATEGORIES",
    "DataFrameMetadataCapabilities",
    "get_platform_capabilities",
    "POLARS_METADATA_CAPABILITIES",
    "PANDAS_METADATA_CAPABILITIES",
    "PYSPARK_METADATA_CAPABILITIES",
    "DATAFUSION_METADATA_CAPABILITIES",
    "DataFrameMetadataResult",
    "UnsupportedOperationError",
    "get_unsupported_message",
    "DataFrameMetadataOperationsManager",
    "get_dataframe_metadata_manager",
]
