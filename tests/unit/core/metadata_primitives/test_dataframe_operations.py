# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import time
from collections import OrderedDict
from decimal import Decimal
from types import SimpleNamespace

import pytest

from benchbox.core.metadata_primitives import (
    DATAFUSION_METADATA_CAPABILITIES,
    OPERATION_CATEGORIES,
    PANDAS_METADATA_CAPABILITIES,
    POLARS_METADATA_CAPABILITIES,
    PYSPARK_METADATA_CAPABILITIES,
    DataFrameMetadataCapabilities,
    DataFrameMetadataOperationsManager,
    DataFrameMetadataResult,
    MetadataOperationCategory,
    MetadataOperationType,
    UnsupportedOperationError,
    get_dataframe_metadata_manager,
    get_platform_capabilities,
    get_unsupported_message,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestDataFrameMetadataCapabilities:
    def test_polars_capabilities(self):

        caps = POLARS_METADATA_CAPABILITIES
        assert caps.platform_name == "polars-df"
        assert caps.supports_schema_introspection is True
        assert caps.supports_describe is True
        assert caps.supports_catalog is False
        assert caps.supports_delta_lake is False

    def test_pandas_capabilities(self):

        caps = PANDAS_METADATA_CAPABILITIES
        assert caps.platform_name == "pandas-df"
        assert caps.supports_schema_introspection is True
        assert caps.supports_describe is True
        assert caps.supports_catalog is False
        assert caps.supports_complex_types is False

    def test_pyspark_capabilities(self):

        caps = PYSPARK_METADATA_CAPABILITIES
        assert caps.platform_name == "pyspark-df"
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is True
        assert caps.supports_partitions is True

    def test_datafusion_capabilities(self):

        caps = DATAFUSION_METADATA_CAPABILITIES
        assert caps.platform_name == "datafusion-df"
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_supports_operation_schema(self):

        caps = POLARS_METADATA_CAPABILITIES

        assert caps.supports_operation(MetadataOperationType.LIST_COLUMNS) is True
        assert caps.supports_operation(MetadataOperationType.GET_DTYPES) is True
        assert caps.supports_operation(MetadataOperationType.GET_SCHEMA) is True
        assert caps.supports_operation(MetadataOperationType.ROW_COUNT) is True

    def test_supports_operation_catalog(self):

        polars_caps = POLARS_METADATA_CAPABILITIES
        pyspark_caps = PYSPARK_METADATA_CAPABILITIES

        assert polars_caps.supports_operation(MetadataOperationType.LIST_DATABASES) is False
        assert polars_caps.supports_operation(MetadataOperationType.LIST_TABLES) is False

        assert pyspark_caps.supports_operation(MetadataOperationType.LIST_DATABASES) is True
        assert pyspark_caps.supports_operation(MetadataOperationType.LIST_TABLES) is True

    def test_supports_operation_lakehouse(self):

        caps = POLARS_METADATA_CAPABILITIES

        assert caps.supports_operation(MetadataOperationType.TABLE_HISTORY) is False
        assert caps.supports_operation(MetadataOperationType.TABLE_DETAIL) is False

    def test_get_supported_operations(self):

        caps = POLARS_METADATA_CAPABILITIES
        supported = caps.get_supported_operations()

        assert MetadataOperationType.LIST_COLUMNS in supported
        assert MetadataOperationType.GET_SCHEMA in supported
        assert MetadataOperationType.LIST_DATABASES not in supported

    def test_get_unsupported_operations(self):

        caps = POLARS_METADATA_CAPABILITIES
        unsupported = caps.get_unsupported_operations()

        assert MetadataOperationType.LIST_DATABASES in unsupported
        assert MetadataOperationType.TABLE_HISTORY in unsupported
        assert MetadataOperationType.LIST_COLUMNS not in unsupported

    def test_get_supported_categories(self):

        polars_caps = POLARS_METADATA_CAPABILITIES
        pyspark_caps = PYSPARK_METADATA_CAPABILITIES

        polars_categories = polars_caps.get_supported_categories()
        pyspark_categories = pyspark_caps.get_supported_categories()

        assert MetadataOperationCategory.SCHEMA in polars_categories
        assert MetadataOperationCategory.CATALOG not in polars_categories

        assert MetadataOperationCategory.SCHEMA in pyspark_categories
        assert MetadataOperationCategory.CATALOG in pyspark_categories


@pytest.mark.unit
class TestGetPlatformCapabilities:
    def test_get_polars_capabilities(self):

        caps = get_platform_capabilities("polars-df")
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_get_pandas_capabilities(self):

        caps = get_platform_capabilities("pandas-df")
        assert caps.supports_schema_introspection is True
        assert caps.supports_complex_types is False

    def test_get_pyspark_capabilities(self):

        caps = get_platform_capabilities("pyspark-df")
        assert caps.supports_catalog is True

    def test_get_unknown_platform(self):

        caps = get_platform_capabilities("unknown-platform")
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_get_capabilities_with_overrides(self):

        caps = get_platform_capabilities(
            "pyspark-df",
            supports_delta_lake=True,
            supports_iceberg=True,
        )
        assert caps.supports_delta_lake is True
        assert caps.supports_iceberg is True


@pytest.mark.unit
class TestDataFrameMetadataResult:
    def test_success_result_creation(self):

        start_time = time.time()
        result = DataFrameMetadataResult.success_result(
            operation_type=MetadataOperationType.LIST_COLUMNS,
            start_time=start_time,
            result_count=10,
            result_data=["col1", "col2", "col3"],
        )

        assert result.success is True
        assert result.operation_type == MetadataOperationType.LIST_COLUMNS
        assert result.result_count == 10
        assert result.result_data == ["col1", "col2", "col3"]
        assert result.error_message is None
        assert result.duration_ms >= 0

    def test_failure_result_creation(self):

        result = DataFrameMetadataResult.failure_result(
            operation_type=MetadataOperationType.LIST_DATABASES,
            error_message="Catalog not configured",
        )

        assert result.success is False
        assert result.operation_type == MetadataOperationType.LIST_DATABASES
        assert result.error_message == "Catalog not configured"
        assert result.result_count == 0

    def test_failure_result_with_start_time(self):

        start_time = time.time() - 0.1
        result = DataFrameMetadataResult.failure_result(
            operation_type=MetadataOperationType.TABLE_HISTORY,
            error_message="Delta Lake not available",
            start_time=start_time,
        )

        assert result.success is False
        assert result.duration_ms >= 99

    def test_result_metrics(self):

        start_time = time.time()
        result = DataFrameMetadataResult.success_result(
            operation_type=MetadataOperationType.ROW_COUNT,
            start_time=start_time,
            result_count=1,
            result_data=1000000,
            metrics={"row_count": 1000000, "scan_type": "metadata"},
        )

        assert result.metrics["row_count"] == 1000000
        assert result.metrics["scan_type"] == "metadata"


@pytest.mark.unit
class TestOperationCategories:
    def test_schema_operations_category(self):

        schema_ops = [
            MetadataOperationType.LIST_COLUMNS,
            MetadataOperationType.GET_DTYPES,
            MetadataOperationType.GET_SCHEMA,
            MetadataOperationType.DESCRIBE_STATS,
            MetadataOperationType.ROW_COUNT,
            MetadataOperationType.COLUMN_COUNT,
        ]

        for op in schema_ops:
            assert OPERATION_CATEGORIES[op] == MetadataOperationCategory.SCHEMA

    def test_catalog_operations_category(self):

        catalog_ops = [
            MetadataOperationType.LIST_DATABASES,
            MetadataOperationType.LIST_TABLES,
            MetadataOperationType.LIST_TABLE_COLUMNS,
            MetadataOperationType.TABLE_EXISTS,
            MetadataOperationType.GET_TABLE_INFO,
        ]

        for op in catalog_ops:
            assert OPERATION_CATEGORIES[op] == MetadataOperationCategory.CATALOG

    def test_lakehouse_operations_category(self):

        lakehouse_ops = [
            MetadataOperationType.TABLE_HISTORY,
            MetadataOperationType.TABLE_DETAIL,
            MetadataOperationType.FILE_METADATA,
            MetadataOperationType.PARTITION_INFO,
            MetadataOperationType.SNAPSHOT_INFO,
        ]

        for op in lakehouse_ops:
            assert OPERATION_CATEGORIES[op] == MetadataOperationCategory.LAKEHOUSE


@pytest.mark.unit
class TestUnsupportedOperationError:
    def test_error_message(self):

        error = UnsupportedOperationError(
            operation=MetadataOperationType.LIST_DATABASES,
            platform_name="polars-df",
        )

        assert "polars-df" in str(error)
        assert "list_databases" in str(error)

    def test_error_with_suggestion(self):

        error = UnsupportedOperationError(
            operation=MetadataOperationType.LIST_DATABASES,
            platform_name="polars-df",
            suggestion="Use pyspark-df with a configured catalog",
        )

        assert "Use pyspark-df" in str(error)


@pytest.mark.unit
class TestGetUnsupportedMessage:
    def test_catalog_operation_message(self):

        message = get_unsupported_message(MetadataOperationType.LIST_DATABASES, "polars-df")

        assert "catalog operations" in message.lower()
        assert "pyspark-df" in message.lower()
        assert "schema introspection" in message.lower()

    def test_lakehouse_delta_message(self):

        message = get_unsupported_message(MetadataOperationType.TABLE_HISTORY, "polars-df")

        assert "delta lake" in message.lower()
        assert "pyspark-df" in message.lower() or "polars" in message.lower()

    def test_lakehouse_iceberg_message(self):

        message = get_unsupported_message(MetadataOperationType.SNAPSHOT_INFO, "polars-df")

        assert "iceberg" in message.lower()


@pytest.mark.unit
class TestDataFrameMetadataOperationsManager:
    def test_manager_initialization_polars(self):

        manager = DataFrameMetadataOperationsManager("polars-df")
        assert manager.platform_name == "polars-df"

        caps = manager.get_capabilities()
        assert caps.supports_schema_introspection is True
        assert caps.supports_catalog is False

    def test_manager_initialization_pandas(self):

        manager = DataFrameMetadataOperationsManager("pandas-df")
        assert manager.platform_name == "pandas-df"

    def test_manager_initialization_pyspark_no_session(self):

        manager = DataFrameMetadataOperationsManager("pyspark-df")
        assert manager.spark_session is None

        caps = manager.get_capabilities()
        assert caps.supports_catalog is True

    def test_supports_operation(self):

        manager = DataFrameMetadataOperationsManager("polars-df")

        assert manager.supports_operation(MetadataOperationType.LIST_COLUMNS) is True
        assert manager.supports_operation(MetadataOperationType.LIST_DATABASES) is False

    def test_get_supported_operations(self):

        manager = DataFrameMetadataOperationsManager("polars-df")
        supported = manager.get_supported_operations()

        assert MetadataOperationType.GET_SCHEMA in supported
        assert MetadataOperationType.LIST_DATABASES not in supported

    def test_validate_operation_success(self):
        manager = DataFrameMetadataOperationsManager("polars-df")

        manager.validate_operation(MetadataOperationType.LIST_COLUMNS)

    def test_validate_operation_failure(self):

        manager = DataFrameMetadataOperationsManager("polars-df")

        with pytest.raises(UnsupportedOperationError) as exc_info:
            manager.validate_operation(MetadataOperationType.LIST_DATABASES)

        assert "polars-df" in str(exc_info.value)
        assert "list_databases" in str(exc_info.value)


@pytest.mark.unit
class TestGetDataFrameMetadataManager:
    def test_get_polars_manager(self):

        manager = get_dataframe_metadata_manager("polars-df")
        assert manager is not None
        assert manager.platform_name == "polars-df"

    def test_get_pandas_manager(self):

        manager = get_dataframe_metadata_manager("pandas-df")
        assert manager is not None
        assert manager.platform_name == "pandas-df"

    def test_get_non_dataframe_platform(self):

        manager = get_dataframe_metadata_manager("duckdb")
        assert manager is None

        manager = get_dataframe_metadata_manager("snowflake")
        assert manager is None


@pytest.mark.unit
class TestPolarsMetadataOperations:
    @pytest.fixture
    def sample_polars_df(self):
        try:
            import polars as pl
        except ImportError:
            pytest.skip("Polars not installed")

        return pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "David", "Eve"],
                "age": [25, 30, 35, 40, 45],
                "salary": [50000.0, 60000.0, 70000.0, 80000.0, 90000.0],
            }
        )

    @pytest.fixture
    def polars_manager(self):
        return DataFrameMetadataOperationsManager("polars-df")

    def test_execute_list_columns(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_list_columns(sample_polars_df)

        assert result.success is True
        assert result.result_count == 4
        assert result.result_data == ["id", "name", "age", "salary"]
        assert result.duration_ms >= 0

    def test_execute_get_dtypes(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_get_dtypes(sample_polars_df)

        assert result.success is True
        assert result.result_count == 4
        assert "id" in result.result_data
        assert "name" in result.result_data

    def test_execute_get_schema(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_get_schema(sample_polars_df)

        assert result.success is True
        assert result.result_count == 4
        assert isinstance(result.result_data, list)

        schema = result.result_data
        assert all("name" in col for col in schema)
        assert all("dtype" in col for col in schema)
        assert all("nullable" in col for col in schema)

    def test_execute_describe_stats(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_describe_stats(sample_polars_df)

        assert result.success is True
        assert result.result_count > 0

    def test_execute_row_count(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_row_count(sample_polars_df)

        assert result.success is True
        assert result.result_data == 5
        assert result.metrics.get("row_count") == 5

    def test_execute_column_count(self, polars_manager, sample_polars_df):

        result = polars_manager.execute_column_count(sample_polars_df)

        assert result.success is True
        assert result.result_data == 4
        assert result.metrics.get("column_count") == 4

    def test_catalog_operations_fail_on_polars(self, polars_manager):

        with pytest.raises(UnsupportedOperationError):
            polars_manager.execute_list_databases()


@pytest.mark.unit
class TestPandasMetadataOperations:
    @pytest.fixture
    def sample_pandas_df(self):
        try:
            import pandas as pd
        except ImportError:
            pytest.skip("Pandas not installed")

        return pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "David", "Eve"],
                "age": [25, 30, 35, 40, 45],
                "salary": [50000.0, 60000.0, 70000.0, 80000.0, 90000.0],
            }
        )

    @pytest.fixture
    def pandas_manager(self):
        return DataFrameMetadataOperationsManager("pandas-df")

    def test_execute_list_columns(self, pandas_manager, sample_pandas_df):

        result = pandas_manager.execute_list_columns(sample_pandas_df)

        assert result.success is True
        assert result.result_count == 4
        assert result.result_data == ["id", "name", "age", "salary"]

    def test_execute_get_dtypes(self, pandas_manager, sample_pandas_df):

        result = pandas_manager.execute_get_dtypes(sample_pandas_df)

        assert result.success is True
        assert result.result_count == 4
        assert "id" in result.result_data

    def test_execute_get_schema(self, pandas_manager, sample_pandas_df):

        result = pandas_manager.execute_get_schema(sample_pandas_df)

        assert result.success is True
        assert result.result_count == 4

        schema = result.result_data
        assert all("nullable" in col for col in schema)

    def test_execute_row_count(self, pandas_manager, sample_pandas_df):

        result = pandas_manager.execute_row_count(sample_pandas_df)

        assert result.success is True
        assert result.result_data == 5

    def test_execute_column_count(self, pandas_manager, sample_pandas_df):

        result = pandas_manager.execute_column_count(sample_pandas_df)

        assert result.success is True
        assert result.result_data == 4


@pytest.mark.unit
class TestDataFusionAndGenericMetadataOperations:
    @staticmethod
    def _datafusion_frame():
        import pyarrow as pa

        class _Frame:
            def schema(self):
                return pa.schema(
                    [
                        pa.field("id", pa.int64(), nullable=False),
                        pa.field("payload", pa.string(), nullable=True),
                    ]
                )

            def count(self):
                return 3

        return _Frame()

    def test_datafusion_schema_methods(self):
        manager = DataFrameMetadataOperationsManager("datafusion-df")
        dataframe = self._datafusion_frame()

        columns = manager.execute_list_columns(dataframe)
        dtypes = manager.execute_get_dtypes(dataframe)
        schema = manager.execute_get_schema(dataframe)
        describe = manager.execute_describe_stats(dataframe)
        row_count = manager.execute_row_count(dataframe)
        column_count = manager.execute_column_count(dataframe)

        assert columns.result_data == ["id", "payload"]
        assert dtypes.result_data == {"id": "int64", "payload": "string"}
        assert schema.result_data[0]["nullable"] is False
        assert describe.success is True and describe.result_count == 0
        assert row_count.result_data == 3
        assert column_count.result_data == 2

    def test_generic_fallback_paths(self):
        class _GenericFrame:
            columns = ["left", "right"]
            dtypes = OrderedDict([("left", "int64"), ("right", "string")])

            def __len__(self):
                return 7

        class _SchemaFallbackFrame:
            columns = ["left", "right"]
            dtypes = ["int64", "string"]

            def __len__(self):
                return 7

        manager = DataFrameMetadataOperationsManager("custom-df")

        dtypes = manager.execute_get_dtypes(_GenericFrame())
        schema = manager.execute_get_schema(_SchemaFallbackFrame())
        row_count = manager.execute_row_count(_GenericFrame())
        column_count = manager.execute_column_count(_GenericFrame())

        assert dtypes.result_data == {"left": "int64", "right": "string"}
        assert schema.result_data == [
            {"name": "left", "dtype": "int64", "nullable": True},
            {"name": "right", "dtype": "string", "nullable": True},
        ]
        assert row_count.result_data == 7
        assert column_count.result_data == 2


@pytest.mark.unit
class TestPySparkCatalogAndLakehouseMetadataOperations:
    @staticmethod
    def _spark_session():
        class _Catalog:
            def listDatabases(self):
                return [
                    SimpleNamespace(name="default"),
                    SimpleNamespace(name="analytics"),
                    SimpleNamespace(name="broken"),
                ]

            def listTables(self, database=None):
                if database == "broken":
                    raise RuntimeError("catalog unavailable")
                if database == "analytics":
                    return [SimpleNamespace(name="facts", database="analytics", tableType="MANAGED")]
                return [SimpleNamespace(name="users", database="default", tableType="MANAGED")]

            def listColumns(self, table_name, database=None):
                assert table_name == "users"
                return [
                    SimpleNamespace(name="id", dataType="int", nullable=False, description="pk"),
                    SimpleNamespace(name="name", dataType="string", nullable=True, description="user name"),
                ]

            def tableExists(self, full_name):
                return full_name in {"users", "default.users"}

            def getTable(self, full_name):
                assert full_name == "default.users"
                return SimpleNamespace(
                    name="users",
                    database="default",
                    tableType="MANAGED",
                    description="user table",
                    isTemporary=False,
                )

        return SimpleNamespace(catalog=_Catalog(), conf=SimpleNamespace(get=lambda *_a, **_k: "iceberg_catalog"))

    @staticmethod
    def _install_delta_modules(monkeypatch):
        class _Row:
            def __init__(self, **payload):
                self.__dict__.update(payload)

            def asDict(self):
                return dict(self.__dict__)

        class _Collectable:
            def __init__(self, rows):
                self._rows = rows

            def collect(self):
                return self._rows

        class _SparkDeltaTable:
            def history(self):
                return _Collectable([_Row(version=1, operation="WRITE")])

            def detail(self):
                return _Collectable(
                    [
                        _Row(
                            numFiles=2,
                            sizeInBytes=128,
                            partitionColumns=["ds"],
                            name="events",
                            description="delta table",
                            id="tbl-1",
                            created_time=123,
                        )
                    ]
                )

        class _DeltaTableApi:
            @staticmethod
            def forPath(_spark_session, _table_path):
                return _SparkDeltaTable()

        class _StandaloneDeltaTable:
            def __init__(self, _table_path):
                self._table_path = _table_path

            def history(self):
                return [{"version": 2, "operation": "MERGE"}]

            def metadata(self):
                return SimpleNamespace(
                    id="tbl-standalone",
                    name="standalone",
                    description="standalone delta table",
                    partition_columns=["ds"],
                    created_time=456,
                )

            def file_uris(self):
                return ["part-000.parquet", "part-001.parquet"]

        monkeypatch.setitem(sys.modules, "delta", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "delta.tables", SimpleNamespace(DeltaTable=_DeltaTableApi))
        monkeypatch.setitem(sys.modules, "deltalake", SimpleNamespace(DeltaTable=_StandaloneDeltaTable))

    def test_catalog_operations_with_fake_spark_session(self):
        spark_session = self._spark_session()
        manager = DataFrameMetadataOperationsManager(
            "pyspark-df",
            spark_session=spark_session,
            delta_available=True,
            iceberg_available=True,
        )

        databases = manager.execute_list_databases()
        tables = manager.execute_list_tables(database="analytics")
        columns = manager.execute_list_table_columns("users", database="default")
        exists = manager.execute_table_exists("users", database="default")
        missing = manager.execute_table_exists("orders")
        table_info = manager.execute_get_table_info("users", database="default")
        large_catalog = manager.execute_large_catalog_list()

        assert databases.result_data == ["default", "analytics", "broken"]
        assert tables.result_data == [{"name": "facts", "database": "analytics", "tableType": "MANAGED"}]
        assert columns.result_count == 2
        assert columns.result_data[0]["description"] == "pk"
        assert exists.result_data is True and exists.result_count == 1
        assert missing.result_data is False and missing.result_count == 0
        assert table_info.result_data["name"] == "users"
        assert large_catalog.result_count == 2
        assert large_catalog.metrics["database_count"] == 3

    def test_catalog_operations_require_spark_session(self):
        manager = DataFrameMetadataOperationsManager("pyspark-df", delta_available=True, iceberg_available=True)

        assert manager.execute_list_databases().success is False
        assert manager.execute_list_tables().success is False
        assert manager.execute_list_table_columns("users").success is False
        assert manager.execute_table_exists("users").success is False
        assert manager.execute_get_table_info("users").success is False
        assert manager.execute_large_catalog_list().success is False

    def test_delta_lake_operations_with_fake_spark_delta_table(self, monkeypatch):
        self._install_delta_modules(monkeypatch)
        manager = DataFrameMetadataOperationsManager(
            "pyspark-df",
            spark_session=self._spark_session(),
            delta_available=True,
            iceberg_available=True,
        )

        history = manager.execute_table_history("/tmp/delta")
        detail = manager.execute_table_detail("/tmp/delta")
        files = manager.execute_file_metadata("/tmp/delta")
        partitions = manager.execute_partition_info("/tmp/delta")
        snapshot = manager.execute_snapshot_info("/tmp/delta")

        assert history.result_data[0]["operation"] == "WRITE"
        assert detail.result_data["name"] == "events"
        assert files.result_data["numFiles"] == 2
        assert partitions.result_data["partitionColumns"] == ["ds"]
        assert snapshot.success is False
        assert "catalog configuration" in snapshot.error_message

    def test_standalone_delta_lake_operations(self, monkeypatch):
        self._install_delta_modules(monkeypatch)
        manager = DataFrameMetadataOperationsManager(
            "pyspark-df",
            delta_available=True,
            iceberg_available=True,
        )

        history = manager.execute_table_history("/tmp/delta")
        detail = manager.execute_table_detail("/tmp/delta")
        files = manager.execute_file_metadata("/tmp/delta")
        partitions = manager.execute_partition_info("/tmp/delta")

        assert history.result_data[0]["operation"] == "MERGE"
        assert detail.result_data["partitionColumns"] == ["ds"]
        assert files.result_data["files"] == ["part-000.parquet", "part-001.parquet"]
        assert partitions.result_data["numPartitions"] == 1

    def test_lakehouse_failure_branches(self):
        no_lakehouse = DataFrameMetadataOperationsManager(
            "pyspark-df",
            delta_available=False,
            iceberg_available=False,
        )
        iceberg_only = DataFrameMetadataOperationsManager(
            "pyspark-df",
            delta_available=False,
            iceberg_available=True,
        )

        with pytest.raises(UnsupportedOperationError):
            no_lakehouse.execute_file_metadata("/tmp/table")

        partition_info = no_lakehouse.execute_partition_info("/tmp/table")

        iceberg_metadata = iceberg_only.execute_file_metadata("/tmp/table")

        assert partition_info.success is False
        assert "Delta Lake or Iceberg" in partition_info.error_message
        assert iceberg_metadata.success is False
        assert "catalog configuration" in iceberg_metadata.error_message

    def test_complex_type_helpers_cover_pandas_and_nested_spark_types(self):
        try:
            import polars as pl
        except ImportError:
            pytest.skip("Polars not installed")

        polars_df = pl.DataFrame({"payload": [{"k": 1}, {"k": 2}], "items": [[1, 2], [3, 4]]})
        polars_manager = DataFrameMetadataOperationsManager("polars-df")
        polars_result = polars_manager.execute_complex_type_introspection(polars_df)

        assert polars_result.metrics["complex_column_count"] == 2
        assert polars_result.metrics["has_structs"] is True
        assert polars_result.metrics["has_arrays"] is True

        class ArrayType:
            def __init__(self, elementType):
                self.elementType = elementType

        class MapType:
            def __init__(self, keyType, valueType):
                self.keyType = keyType
                self.valueType = valueType

        class StructType:
            def __init__(self, fields):
                self.fields = fields

        field = SimpleNamespace(
            name="nested",
            dataType=MapType("string", ArrayType(StructType([SimpleNamespace(dataType="int")]))),
        )
        spark_manager = DataFrameMetadataOperationsManager("pyspark-df")
        spark_info = spark_manager._analyze_spark_type(field, ArrayType, MapType, StructType)

        assert spark_info["complex_type"] == "map"
        assert spark_info["nested_depth"] >= 3


@pytest.mark.unit
class TestComplexityOperations:
    @pytest.fixture
    def wide_polars_df(self):
        try:
            import polars as pl
        except ImportError:
            pytest.skip("Polars not installed")

        data = {f"col_{i:03d}": list(range(10)) for i in range(150)}
        return pl.DataFrame(data)

    @pytest.fixture
    def complex_polars_df(self):
        try:
            import polars as pl
        except ImportError:
            pytest.skip("Polars not installed")

        return pl.DataFrame(
            {
                "id": [1, 2, 3],
                "simple_col": ["a", "b", "c"],
                "array_col": [[1, 2], [3, 4], [5, 6]],
                "struct_col": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}],
            }
        )

    @pytest.fixture
    def polars_manager(self):
        return DataFrameMetadataOperationsManager("polars-df")

    def test_execute_wide_table_schema(self, polars_manager, wide_polars_df):

        result = polars_manager.execute_wide_table_schema(wide_polars_df)

        assert result.success is True
        assert result.result_count == 150
        assert result.metrics["column_count"] == 150
        assert result.metrics["is_wide_table"] is True
        assert len(result.result_data) == 150

    def test_execute_complex_type_introspection(self, polars_manager, complex_polars_df):

        result = polars_manager.execute_complex_type_introspection(complex_polars_df)

        assert result.success is True
        assert result.result_count >= 2
        assert result.metrics["complex_column_count"] >= 2

    def test_wide_table_schema_pandas(self):

        try:
            import pandas as pd
        except ImportError:
            pytest.skip("Pandas not installed")

        data = {f"col_{i:03d}": range(10) for i in range(150)}
        df = pd.DataFrame(data)

        manager = DataFrameMetadataOperationsManager("pandas-df")
        result = manager.execute_wide_table_schema(df)

        assert result.success is True
        assert result.result_count == 150
        assert result.metrics["is_wide_table"] is True

    def test_large_catalog_list_requires_spark(self):

        manager = DataFrameMetadataOperationsManager("polars-df")

        with pytest.raises(UnsupportedOperationError):
            manager.execute_large_catalog_list()


@pytest.mark.unit
class TestBenchmarkDataFrameIntegration:
    def test_supports_dataframe_mode(self):

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        assert benchmark.supports_dataframe_mode() is True

    def test_skip_dataframe_data_loading(self):
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        assert benchmark.skip_dataframe_data_loading() is True

    def test_get_dataframe_operations(self):

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        ops = benchmark.get_dataframe_operations("polars-df")

        assert ops is not None
        assert isinstance(ops, DataFrameMetadataOperationsManager)

    def test_get_dataframe_operations_invalid_platform(self):

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()

        with pytest.raises(ValueError, match="does not support DataFrame"):
            benchmark.get_dataframe_operations("duckdb")

    def test_get_dataframe_capabilities(self):

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        caps = benchmark.get_dataframe_capabilities("polars-df")

        assert caps is not None
        assert caps.supports_schema_introspection is True

    def test_benchmark_registry_metadata_matches_metadata_catalog(self):
        from benchbox.core.benchmark_registry import BENCHMARK_METADATA
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        metadata = BENCHMARK_METADATA["metadata_primitives"]

        assert metadata["num_queries"] == len(benchmark.get_queries())
        assert "DataFrame mode runs platform-specific metadata operation subsets" in metadata["query_description"]

    def test_run_dataframe_benchmark_polars(self):

        try:
            import polars as pl
        except ImportError:
            pytest.skip("Polars not installed")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

        result = benchmark.run_dataframe_benchmark(
            platform_name="polars-df",
            dataframes={"test_table": df},
            categories=["schema"],
            iterations=1,
        )

        assert result.total_queries > 0
        assert result.successful_queries > 0
        assert "schema" in result.category_summary

    def test_run_dataframe_benchmark_catalog_category_executes_table_scoped_catalog_ops(self, monkeypatch):
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        benchmark = MetadataPrimitivesBenchmark()
        calls: list[tuple[str, str | None]] = []

        class FakeCapabilities:
            supported = {
                MetadataOperationType.LIST_DATABASES,
                MetadataOperationType.LIST_TABLES,
                MetadataOperationType.LIST_TABLE_COLUMNS,
                MetadataOperationType.TABLE_EXISTS,
                MetadataOperationType.GET_TABLE_INFO,
            }

            def supports_operation(self, operation: MetadataOperationType) -> bool:
                return operation in self.supported

        class FakeManager:
            def get_capabilities(self) -> FakeCapabilities:
                return FakeCapabilities()

            def execute_list_databases(self) -> DataFrameMetadataResult:
                calls.append(("list_databases", None))
                return DataFrameMetadataResult.success_result(
                    MetadataOperationType.LIST_DATABASES,
                    start_time=time.time(),
                    result_count=1,
                )

            def execute_list_tables(self) -> DataFrameMetadataResult:
                calls.append(("list_tables", None))
                return DataFrameMetadataResult.success_result(
                    MetadataOperationType.LIST_TABLES,
                    start_time=time.time(),
                    result_count=2,
                )

            def execute_list_table_columns(self, table_name: str) -> DataFrameMetadataResult:
                calls.append(("list_table_columns", table_name))
                return DataFrameMetadataResult.success_result(
                    MetadataOperationType.LIST_TABLE_COLUMNS,
                    start_time=time.time(),
                    result_count=3,
                )

            def execute_table_exists(self, table_name: str) -> DataFrameMetadataResult:
                calls.append(("table_exists", table_name))
                return DataFrameMetadataResult.success_result(
                    MetadataOperationType.TABLE_EXISTS,
                    start_time=time.time(),
                    result_count=1,
                    result_data=True,
                )

            def execute_get_table_info(self, table_name: str) -> DataFrameMetadataResult:
                calls.append(("get_table_info", table_name))
                return DataFrameMetadataResult.success_result(
                    MetadataOperationType.GET_TABLE_INFO,
                    start_time=time.time(),
                    result_count=1,
                )

        monkeypatch.setattr(benchmark, "get_dataframe_operations", lambda *_args, **_kwargs: FakeManager())

        result = benchmark.run_dataframe_benchmark(
            platform_name="pyspark-df",
            dataframes={"orders": object(), "lineitem": object()},
            categories=["catalog"],
            iterations=1,
        )

        assert result.total_queries == 8
        assert calls == [
            ("list_databases", None),
            ("list_tables", None),
            ("list_table_columns", "orders"),
            ("list_table_columns", "lineitem"),
            ("table_exists", "orders"),
            ("table_exists", "lineitem"),
            ("get_table_info", "orders"),
            ("get_table_info", "lineitem"),
        ]

    def test_execute_dataframe_workload_complexity_category_runs_complexity_ops(self):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.complexity import get_complexity_preset
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()

        rows = benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={"power_iterations": 1, "metadata_categories": ["complexity"]}),
        )

        preset = get_complexity_preset("wide_tables")
        expected_tables = len(benchmark.get_table_names()) + 1 + preset.catalog_size
        assert len(rows) == expected_tables * 2
        assert all(
            row["query_id"].startswith("df_wide_table_schema_")
            or row["query_id"].startswith("df_complex_type_introspection_")
            for row in rows
        )
        query_ids = [row["query_id"] for row in rows]
        assert any("stress_wide" in query_id for query_id in query_ids)
        assert any("stress_catalog_" in query_id for query_id in query_ids)

    def test_execute_dataframe_workload_uses_registered_context_tables(self, monkeypatch):
        import polars as pl

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.benchmark import MetadataBenchmarkResult
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        native_df = pl.DataFrame({"a": [1, 2, 3]})
        ctx.register_table("test_table", native_df)

        captured: dict[str, object] = {}

        def fake_run_dataframe_benchmark(
            platform_name: str,
            dataframes: dict[str, object],
            spark_session=None,
            categories=None,
            iterations: int = 1,
        ) -> MetadataBenchmarkResult:
            _ = (spark_session, categories, iterations)
            captured["platform_name"] = platform_name
            captured["dataframes"] = dataframes
            return MetadataBenchmarkResult(results=[], total_queries=0)

        monkeypatch.setattr(benchmark, "run_dataframe_benchmark", fake_run_dataframe_benchmark)

        results = benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={}),
            query_filter=None,
            monitor=None,
            run_options=None,
        )

        assert results == []
        assert captured["platform_name"] == adapter.platform_name
        assert captured["dataframes"] == {"test_table": native_df}

    def test_execute_dataframe_workload_bootstraps_schema_fixtures_for_empty_context(self):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()

        rows = benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={"power_iterations": 1}),
        )

        assert set(ctx.list_tables()) == set(benchmark.get_table_names())
        assert len(rows) == len(benchmark.get_table_names()) * 6
        assert all(row["status"] == "SUCCESS" for row in rows)

    def test_execute_dataframe_workload_returns_adapter_row_format(self, monkeypatch):
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.benchmark import MetadataBenchmarkResult, MetadataQueryResult
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()

        fake_results = [
            MetadataQueryResult(
                query_id="schema_list_tables",
                category="schema",
                execution_time_ms=5.0,
                row_count=3,
                success=True,
            ),
            MetadataQueryResult(
                query_id="schema_get_columns",
                category="schema",
                execution_time_ms=2.0,
                row_count=0,
                success=False,
                error="not supported",
            ),
        ]

        def fake_run(platform_name, dataframes, spark_session=None, categories=None, iterations=1):
            return MetadataBenchmarkResult(results=fake_results, total_queries=2)

        monkeypatch.setattr(benchmark, "run_dataframe_benchmark", fake_run)

        rows = benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={}),
        )

        assert len(rows) == 2

        row0 = rows[0]
        assert row0["query_id"] == "schema_list_tables"
        assert row0["status"] == "SUCCESS"
        assert abs(row0["execution_time_seconds"] - 0.005) < 1e-9
        assert row0["rows_returned"] == 3
        assert row0["iteration"] == 1
        assert row0["run_type"] == "measurement"
        assert row0["error"] is None

        row1 = rows[1]
        assert row1["query_id"] == "schema_get_columns"
        assert row1["status"] == "FAILED"
        assert row1["error"] == "not supported"
        assert row1["iteration"] == 1

    def test_execute_dataframe_workload_query_filter_excludes_non_matching(self, monkeypatch):
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.benchmark import MetadataBenchmarkResult, MetadataQueryResult
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()

        fake_results = [
            MetadataQueryResult(
                query_id="schema_list_tables",
                category="schema",
                execution_time_ms=1.0,
                row_count=1,
                success=True,
            ),
            MetadataQueryResult(
                query_id="schema_get_columns",
                category="schema",
                execution_time_ms=1.0,
                row_count=1,
                success=True,
            ),
        ]

        def fake_run(platform_name, dataframes, spark_session=None, categories=None, iterations=1):
            return MetadataBenchmarkResult(results=fake_results, total_queries=2)

        monkeypatch.setattr(benchmark, "run_dataframe_benchmark", fake_run)

        rows = benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={}),
            query_filter={"SCHEMA_LIST_TABLES"},
        )

        assert len(rows) == 1
        assert rows[0]["query_id"] == "schema_list_tables"


@pytest.mark.unit
class TestComplexityStressFixtures:
    def test_build_complexity_dataframes_uses_shared_ddl(self):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.complexity import MetadataComplexityConfig, TypeComplexity
        from benchbox.core.metadata_primitives.ddl import generate_wide_table_columns
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        config = MetadataComplexityConfig(
            width_factor=120,
            view_depth=1,
            type_complexity=TypeComplexity.SCALAR,
            catalog_size=5,
        )
        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        tables = benchmark.build_complexity_dataframes(adapter, config)

        expected = generate_wide_table_columns(
            width=120,
            dialect="duckdb",
            type_complexity=TypeComplexity.SCALAR,
        )
        wide = tables["stress_wide"]
        assert wide.width == len(expected)
        assert set(wide.columns) == {column.name for column in expected}
        assert len(tables) == 1 + config.catalog_size

    def test_wide_table_schema_op_on_shared_ddl_fixture(self):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        tables = benchmark.build_complexity_dataframes(adapter, "wide_tables")
        manager = benchmark.get_dataframe_operations(adapter.platform_name)

        result = manager.execute_wide_table_schema(tables["stress_wide"])
        assert result.success is True
        assert result.metrics["is_wide_table"] is True
        assert result.metrics["column_count"] >= 100

    @pytest.mark.parametrize(
        ("column_type", "expected"),
        [
            ("INTEGER[]", [3]),
            ("VARCHAR[]", ["t_c_3_item"]),
            ("ARRAY<INT64>", [3]),
            ("STRUCT(key VARCHAR, value VARCHAR)", {"key": "t_c_key_3", "value": "t_c_value_3"}),
            (
                "STRUCT(name VARCHAR, data STRUCT(x INTEGER, y INTEGER))",
                {"name": "t_c_name_3", "data": {"x": 3, "y": 3}},
            ),
            ("MAP(VARCHAR, INTEGER)", {"t_c_key_3": 3}),
            ("OBJECT", {"t_c_3_key": 3}),
            ("DECIMAL(18,4)", Decimal("3.25")),
        ],
    )
    def test_sample_dataframe_value_preserves_complex_types(self, column_type: str, expected: object) -> None:
        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

        assert (
            MetadataPrimitivesBenchmark._sample_dataframe_value(
                table_name="t", column_name="c", column_type=column_type, ordinal=3
            )
            == expected
        )

    def test_complex_type_introspection_sees_nested_fixture(self):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.complexity import MetadataComplexityConfig, TypeComplexity
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        config = MetadataComplexityConfig(
            width_factor=30,
            view_depth=1,
            type_complexity=TypeComplexity.NESTED,
            catalog_size=2,
        )
        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        tables = benchmark.build_complexity_dataframes(adapter, config)
        manager = benchmark.get_dataframe_operations(adapter.platform_name)

        result = manager.execute_complex_type_introspection(tables["stress_wide"])
        assert result.success is True
        assert result.metrics["complex_column_count"] > 0

    def test_execute_workload_bootstraps_stress_tables_for_complexity(self, monkeypatch):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.benchmark import MetadataBenchmarkResult
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        captured: dict[str, set[str]] = {}

        def fake_run(platform_name, dataframes, spark_session=None, categories=None, iterations=1):
            captured["keys"] = set(dataframes)
            return MetadataBenchmarkResult(results=[], total_queries=0)

        monkeypatch.setattr(benchmark, "run_dataframe_benchmark", fake_run)

        ctx = adapter.create_context()
        benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(
                options={"metadata_categories": ["complexity"], "metadata_complexity_preset": "minimal"}
            ),
        )
        assert "stress_wide" in captured["keys"]
        assert any(key.startswith("stress_catalog_") for key in captured["keys"])

    def test_execute_workload_skips_stress_tables_by_default(self, monkeypatch):
        pytest.importorskip("polars")

        from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.benchmark import MetadataBenchmarkResult
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        benchmark = MetadataPrimitivesBenchmark()
        adapter = PolarsDataFrameAdapter()
        captured: dict[str, set[str]] = {}

        def fake_run(platform_name, dataframes, spark_session=None, categories=None, iterations=1):
            captured["keys"] = set(dataframes)
            return MetadataBenchmarkResult(results=[], total_queries=0)

        monkeypatch.setattr(benchmark, "run_dataframe_benchmark", fake_run)

        ctx = adapter.create_context()
        benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=SimpleNamespace(options={}),
        )
        assert "stress_wide" not in captured["keys"]
