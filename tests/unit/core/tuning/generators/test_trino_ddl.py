# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.trino import (
    AthenaDDLGenerator,
    ConnectorType,
    FileFormat,
    TrinoDDLGenerator,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTrinoDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = TrinoDDLGenerator()
        assert generator.platform_name == "trino"

    def test_supported_tuning_types(self) -> None:

        generator = TrinoDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("distribution")
        assert generator.supports_tuning_type("sorting")
        assert generator.supports_tuning_type("clustering")

    def test_connector_property(self) -> None:

        generator = TrinoDDLGenerator(connector="iceberg")
        assert generator.connector == ConnectorType.ICEBERG

        generator2 = TrinoDDLGenerator(connector=ConnectorType.DELTA)
        assert generator2.connector == ConnectorType.DELTA


class TestHiveConnectorPartitioning:
    def test_basic_partitioning(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE)
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["partitioned_by"] == "ARRAY['l_shipdate']"

    def test_multiple_partition_columns(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE)
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[
                TuningColumn(name="order_year", type="INTEGER", order=1),
                TuningColumn(name="order_month", type="INTEGER", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["partitioned_by"] == "ARRAY['order_year', 'order_month']"

    def test_bucketing(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE, default_bucket_count=16)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["bucketed_by"] == "ARRAY['l_orderkey']"
        assert clauses.table_properties["bucket_count"] == "16"

    def test_sorting_within_buckets(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE)
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_orderkey", type="BIGINT", order=1),
                TuningColumn(name="l_linenumber", type="INTEGER", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["sorted_by"] == "ARRAY['l_orderkey', 'l_linenumber']"

    def test_complete_tuning(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE, default_bucket_count=32)
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
            sorting=[TuningColumn(name="l_linenumber", type="INTEGER", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "partitioned_by" in clauses.table_properties
        assert "bucketed_by" in clauses.table_properties
        assert "bucket_count" in clauses.table_properties
        assert "sorted_by" in clauses.table_properties


class TestIcebergConnectorPartitioning:
    def test_date_partition_transform(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.ICEBERG)
        table_tuning = TableTuning(
            table_name="events",
            partitioning=[TuningColumn(name="event_date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "partitioning" in clauses.table_properties
        assert "month(event_date)" in clauses.table_properties["partitioning"]

    def test_non_date_partition(self) -> None:
        generator = TrinoDDLGenerator(connector=ConnectorType.ICEBERG)
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="region", type="VARCHAR", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["partitioning"] == "ARRAY['region']"

    def test_iceberg_sorted_by(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.ICEBERG)
        table_tuning = TableTuning(
            table_name="events",
            sorting=[TuningColumn(name="event_id", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["sorted_by"] == "ARRAY['event_id']"

    def test_iceberg_distribution_logs_info(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.ICEBERG)
        table_tuning = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="order_id", type="BIGINT", order=1)],
        )

        with patch("benchbox.core.tuning.generators.trino.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.info.assert_called_once()
            assert "bucket() transform" in mock_logger.info.call_args[0][0]


class TestDeltaConnector:
    def test_delta_partitioning(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.DELTA)
        table_tuning = TableTuning(
            table_name="events",
            partitioning=[TuningColumn(name="event_date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["partitioned_by"] == "ARRAY['event_date']"

    def test_delta_location(self) -> None:

        generator = TrinoDDLGenerator(
            connector=ConnectorType.DELTA,
            location="s3://bucket/delta/events",
        )
        clauses = generator.generate_tuning_clauses(TableTuning(table_name="events"))
        assert clauses.table_properties["location"] == "'s3://bucket/delta/events'"


class TestFileFormat:
    def test_default_parquet_format(self) -> None:

        generator = TrinoDDLGenerator()
        clauses = generator.generate_tuning_clauses(TableTuning(table_name="test"))
        assert clauses.table_properties["format"] == "'PARQUET'"

    def test_orc_format(self) -> None:

        generator = TrinoDDLGenerator(default_format=FileFormat.ORC)
        clauses = generator.generate_tuning_clauses(TableTuning(table_name="test"))
        assert clauses.table_properties["format"] == "'ORC'"


class TestCreateTableDDL:
    def test_basic_create_table(self) -> None:

        generator = TrinoDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "VARCHAR"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id BIGINT NOT NULL" in ddl
        assert "name VARCHAR" in ddl
        assert ddl.endswith(";")

    def test_create_table_with_tuning(self) -> None:

        generator = TrinoDDLGenerator(connector=ConnectorType.HIVE)
        columns = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="lineitem",
                partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            )
        )
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning=tuning)
        assert "CREATE TABLE lineitem" in ddl
        assert "WITH (" in ddl
        assert "format = 'PARQUET'" in ddl
        assert "partitioned_by = ARRAY['l_shipdate']" in ddl

    def test_create_table_if_not_exists(self) -> None:

        generator = TrinoDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = TrinoDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="hive.tpch")
        assert "CREATE TABLE hive.tpch.orders" in ddl

    def test_external_table(self) -> None:

        generator = TrinoDDLGenerator(external=True)
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("events", columns)
        assert "CREATE EXTERNAL TABLE events" in ddl


class TestAthenaDDLGenerator:
    def test_platform_name(self) -> None:

        generator = AthenaDDLGenerator()
        assert generator.platform_name == "athena"

    def test_creates_external_table(self) -> None:

        generator = AthenaDDLGenerator(location="s3://bucket/data/")
        columns = [
            ColumnDefinition("id", "BIGINT"),
            ColumnDefinition("name", "STRING"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE EXTERNAL TABLE" in ddl
        assert "STORED AS PARQUET" in ddl
        assert "LOCATION 's3://bucket/data/'" in ddl

    def test_athena_with_different_format(self) -> None:

        generator = AthenaDDLGenerator(
            location="s3://bucket/orc/",
            default_format=FileFormat.ORC,
        )
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("events", columns)
        assert "STORED AS ORC" in ddl


class TestEnums:
    def test_connector_type_values(self) -> None:

        assert ConnectorType.HIVE.value == "hive"
        assert ConnectorType.ICEBERG.value == "iceberg"
        assert ConnectorType.DELTA.value == "delta"
        assert ConnectorType.MEMORY.value == "memory"

    def test_file_format_values(self) -> None:

        assert FileFormat.PARQUET.value == "PARQUET"
        assert FileFormat.ORC.value == "ORC"
        assert FileFormat.AVRO.value == "AVRO"


