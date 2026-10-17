# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.postgresql import (
    PartitionStrategy,
    PostgreSQLDDLGenerator,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPostgreSQLDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = PostgreSQLDDLGenerator()
        assert generator.platform_name == "postgresql"

    def test_supported_tuning_types(self) -> None:

        generator = PostgreSQLDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("clustering")
        assert generator.supports_tuning_type("sorting")
        assert not generator.supports_tuning_type("distribution")


class TestPartitioningGeneration:
    def test_range_partitioning_date(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY RANGE (o_orderdate)"

    def test_hash_partitioning_integer(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY HASH (l_orderkey)"

    def test_list_partitioning_varchar(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderpriority", type="VARCHAR", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY LIST (o_orderpriority)"

    def test_multiple_partition_columns(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_custkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY RANGE (o_orderdate, o_custkey)"


class TestClusteringGeneration:
    def test_clustering_creates_index_and_cluster(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            clustering=[
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_orderkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 2
        assert "CREATE INDEX IF NOT EXISTS idx_orders_cluster" in clauses.post_create_statements[0]
        assert "o_orderdate, o_orderkey" in clauses.post_create_statements[0]
        assert "CLUSTER {table_name} USING idx_orders_cluster" in clauses.post_create_statements[1]

    def test_sorting_maps_to_clustering(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 2
        assert "l_shipdate" in clauses.post_create_statements[0]


class TestDistributionWarning:
    def test_distribution_logs_warning(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )

        with patch("benchbox.core.tuning.generators.postgresql.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "single-node" in mock_logger.warning.call_args[0][0]


class TestPartitionChildGeneration:
    def test_hash_partition_children(self) -> None:

        generator = PostgreSQLDDLGenerator(default_hash_partitions=4)
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        columns = [ColumnDefinition("l_orderkey", "BIGINT")]
        tuning = generator.generate_tuning_clauses(table_tuning)

        children = generator.generate_partition_children("lineitem", columns, tuning, table_tuning)

        assert len(children) == 4
        assert "lineitem_p0 PARTITION OF lineitem" in children[0]
        assert "FOR VALUES WITH (MODULUS 4, REMAINDER 0)" in children[0]
        assert "lineitem_p3 PARTITION OF lineitem" in children[3]
        assert "FOR VALUES WITH (MODULUS 4, REMAINDER 3)" in children[3]

    def test_range_partition_children_yearly(self) -> None:

        generator = PostgreSQLDDLGenerator(default_date_granularity="YEARLY")
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        columns = [ColumnDefinition("o_orderdate", "DATE")]
        tuning = generator.generate_tuning_clauses(table_tuning)

        children = generator.generate_partition_children("orders", columns, tuning, table_tuning)

        assert len(children) == 7
        assert children[-1] == "CREATE TABLE orders_default PARTITION OF orders DEFAULT;"
        assert "orders_2020 PARTITION OF orders" in children[0]
        assert "FOR VALUES FROM ('2020-01-01') TO ('2021-01-01')" in children[0]
        assert "orders_2025 PARTITION OF orders" in children[5]

    def test_no_children_without_partitioning(self) -> None:

        generator = PostgreSQLDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            clustering=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )
        columns = [ColumnDefinition("o_orderkey", "BIGINT")]
        tuning = generator.generate_tuning_clauses(table_tuning)

        children = generator.generate_partition_children("orders", columns, tuning, table_tuning)

        assert len(children) == 0


class TestCreateTableDDL:
    def test_basic_create_table(self) -> None:

        generator = PostgreSQLDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "VARCHAR"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id BIGINT NOT NULL" in ddl
        assert "name VARCHAR" in ddl
        assert ddl.endswith(";")

    def test_create_table_with_partitioning(self) -> None:

        generator = PostgreSQLDDLGenerator()
        columns = [
            ColumnDefinition("o_orderkey", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("o_orderdate", "DATE"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="orders",
                partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
            )
        )
        ddl = generator.generate_create_table_ddl("orders", columns, tuning=tuning)

        assert "CREATE TABLE orders" in ddl
        assert "PARTITION BY RANGE (o_orderdate)" in ddl
        assert ddl.endswith(";")

    def test_create_table_if_not_exists(self) -> None:

        generator = PostgreSQLDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = PostgreSQLDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="tpch")
        assert "CREATE TABLE tpch.orders" in ddl


class TestPartitionStrategyEnum:
    def test_enum_values(self) -> None:

        assert PartitionStrategy.RANGE.value == "RANGE"
        assert PartitionStrategy.LIST.value == "LIST"
        assert PartitionStrategy.HASH.value == "HASH"
