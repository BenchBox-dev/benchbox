# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.bigquery import (
    BigQueryDDLGenerator,
    PartitionGranularity,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBigQueryDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = BigQueryDDLGenerator()
        assert generator.platform_name == "bigquery"

    def test_supported_tuning_types(self) -> None:

        generator = BigQueryDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("clustering")
        assert generator.supports_tuning_type("sorting")
        assert not generator.supports_tuning_type("distribution")


class TestPartitioningGeneration:
    def test_date_partitioning_day(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY l_shipdate"

    def test_date_partitioning_month(self) -> None:

        generator = BigQueryDDLGenerator(default_partition_granularity=PartitionGranularity.MONTH)
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY DATE_TRUNC(o_orderdate, MONTH)"

    def test_timestamp_partitioning(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            partitioning=[TuningColumn(name="event_ts", type="TIMESTAMP", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY TIMESTAMP_TRUNC(event_ts, DAY)"

    def test_datetime_partitioning(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="logs",
            partitioning=[TuningColumn(name="log_datetime", type="DATETIME", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY DATETIME_TRUNC(log_datetime, DAY)"

    def test_integer_range_partitioning(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="customers",
            partitioning=[TuningColumn(name="customer_id", type="INT64", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "PARTITION BY RANGE_BUCKET(customer_id, GENERATE_ARRAY(" in clauses.partition_by

    def test_multiple_partition_columns_uses_first(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_custkey", type="INT64", order=2),
            ],
        )

        with patch("benchbox.core.tuning.generators.bigquery.logger") as mock_logger:
            clauses = generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "single-column partitioning" in mock_logger.warning.call_args[0][0]

        assert "o_orderdate" in clauses.partition_by


class TestClusteringGeneration:
    def test_basic_clustering(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[
                TuningColumn(name="l_orderkey", type="INT64", order=1),
                TuningColumn(name="l_partkey", type="INT64", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY l_orderkey, l_partkey"

    def test_clustering_respects_order(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            clustering=[
                TuningColumn(name="o_orderkey", type="INT64", order=3),
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_custkey", type="INT64", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY o_orderdate, o_custkey, o_orderkey"

    def test_clustering_limit_enforced(self) -> None:
        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="wide_table",
            clustering=[
                TuningColumn(name="col1", type="INT64", order=1),
                TuningColumn(name="col2", type="INT64", order=2),
                TuningColumn(name="col3", type="INT64", order=3),
                TuningColumn(name="col4", type="INT64", order=4),
                TuningColumn(name="col5", type="INT64", order=5),
            ],
        )

        with patch("benchbox.core.tuning.generators.bigquery.logger") as mock_logger:
            clauses = generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "max 4 clustering columns" in mock_logger.warning.call_args[0][0]

        assert clauses.cluster_by == "CLUSTER BY col1, col2, col3, col4"

    def test_sorting_maps_to_clustering(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            sorting=[
                TuningColumn(name="event_date", type="DATE", order=1),
                TuningColumn(name="event_id", type="INT64", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY event_date, event_id"

    def test_combined_clustering_and_sorting(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            clustering=[TuningColumn(name="event_date", type="DATE", order=1)],
            sorting=[TuningColumn(name="user_id", type="INT64", order=2)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY event_date, user_id"


class TestDistributionWarning:
    def test_distribution_logs_warning(self) -> None:

        generator = BigQueryDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_orderkey", type="INT64", order=1)],
        )

        with patch("benchbox.core.tuning.generators.bigquery.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "not applicable for BigQuery" in mock_logger.warning.call_args[0][0]


class TestRequirePartitionFilter:
    def test_require_partition_filter_enabled(self) -> None:

        generator = BigQueryDDLGenerator(require_partition_filter=True)
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_options.get("require_partition_filter") is True

    def test_require_partition_filter_disabled(self) -> None:

        generator = BigQueryDDLGenerator(require_partition_filter=False)
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "require_partition_filter" not in clauses.table_options


class TestCreateTableDDL:
    def test_basic_create_table(self) -> None:

        generator = BigQueryDDLGenerator()
        columns = [
            ColumnDefinition("id", "INT64", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "STRING"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id INT64 NOT NULL" in ddl
        assert "name STRING" in ddl
        assert ddl.endswith(";")

    def test_create_table_with_partitioning(self) -> None:

        generator = BigQueryDDLGenerator()
        columns = [
            ColumnDefinition("l_orderkey", "INT64", ColumnNullability.NOT_NULL),
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
        assert "PARTITION BY l_shipdate" in ddl

    def test_create_table_with_clustering(self) -> None:

        generator = BigQueryDDLGenerator()
        columns = [
            ColumnDefinition("l_orderkey", "INT64"),
            ColumnDefinition("l_partkey", "INT64"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="lineitem",
                clustering=[
                    TuningColumn(name="l_orderkey", type="INT64", order=1),
                    TuningColumn(name="l_partkey", type="INT64", order=2),
                ],
            )
        )
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning=tuning)
        assert "CLUSTER BY l_orderkey, l_partkey" in ddl

    def test_create_table_with_all_tuning(self) -> None:

        generator = BigQueryDDLGenerator(require_partition_filter=True)
        columns = [
            ColumnDefinition("l_orderkey", "INT64", ColumnNullability.NOT_NULL),
            ColumnDefinition("l_shipdate", "DATE"),
            ColumnDefinition("l_partkey", "INT64"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="lineitem",
                partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
                clustering=[
                    TuningColumn(name="l_orderkey", type="INT64", order=1),
                    TuningColumn(name="l_partkey", type="INT64", order=2),
                ],
            )
        )
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning=tuning)

        assert "CREATE TABLE lineitem" in ddl
        assert "PARTITION BY l_shipdate" in ddl
        assert "CLUSTER BY l_orderkey, l_partkey" in ddl
        assert "OPTIONS" in ddl
        assert "require_partition_filter = true" in ddl.lower()
        assert ddl.endswith(";")

    def test_create_table_if_not_exists(self) -> None:

        generator = BigQueryDDLGenerator()
        columns = [ColumnDefinition("id", "INT64")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = BigQueryDDLGenerator()
        columns = [ColumnDefinition("id", "INT64")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="tpch")
        assert "CREATE TABLE tpch.orders" in ddl


class TestPartitionGranularityEnum:
    def test_enum_values(self) -> None:

        assert PartitionGranularity.DAY.value == "DAY"
        assert PartitionGranularity.MONTH.value == "MONTH"
        assert PartitionGranularity.YEAR.value == "YEAR"
        assert PartitionGranularity.HOUR.value == "HOUR"


