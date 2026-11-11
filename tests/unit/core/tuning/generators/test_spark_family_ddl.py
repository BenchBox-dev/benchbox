# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition
from benchbox.core.tuning.generators.spark_family import (
    DeltaDDLGenerator,
    HiveDDLGenerator,
    IcebergDDLGenerator,
    ParquetDDLGenerator,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDeltaDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = DeltaDDLGenerator()
        assert generator.platform_name == "delta"

    def test_supported_tuning_types(self) -> None:

        generator = DeltaDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("clustering")
        assert generator.supports_tuning_type("sorting")
        assert generator.supports_tuning_type("distribution")


class TestDeltaPartitioningGeneration:
    def test_basic_partitioning(self) -> None:

        generator = DeltaDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (o_orderdate)"
        assert "USING DELTA" in clauses.additional_clauses

    def test_multiple_partition_columns(self) -> None:

        generator = DeltaDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[
                TuningColumn(name="o_orderdate", type="DATE", order=2),
                TuningColumn(name="o_orderpriority", type="VARCHAR", order=1),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (o_orderpriority, o_orderdate)"


class TestDeltaClusteringGeneration:
    def test_liquid_clustering(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[
                TuningColumn(name="l_orderkey", type="BIGINT", order=1),
                TuningColumn(name="l_shipdate", type="DATE", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (l_orderkey, l_shipdate)"
        assert len(clauses.post_create_statements) == 0

    def test_zorder_fallback(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=False)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by is None
        assert len(clauses.post_create_statements) == 1
        assert "OPTIMIZE {table_name} ZORDER BY (l_orderkey)" in clauses.post_create_statements[0]

    def test_sorting_maps_to_clustering(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True)
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (l_shipdate)"


class TestDeltaDistributionGeneration:
    def test_distribution_generates_zorder(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert len(clauses.post_create_statements) == 1
        assert "ZORDER BY (l_orderkey)" in clauses.post_create_statements[0]

    def test_no_zorder_when_clustering_exists(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
            distribution=[TuningColumn(name="l_partkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert clauses.cluster_by == "CLUSTER BY (l_orderkey)"
        assert len(clauses.post_create_statements) == 0


class TestDeltaTableProperties:
    def test_auto_optimize_enabled(self) -> None:

        generator = DeltaDDLGenerator(enable_auto_optimize=True)
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["delta.autoOptimize.optimizeWrite"] == "true"
        assert clauses.table_properties["delta.autoOptimize.autoCompact"] == "true"

    def test_auto_optimize_disabled(self) -> None:

        generator = DeltaDDLGenerator(enable_auto_optimize=False)
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert not clauses.table_properties


class TestIcebergDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = IcebergDDLGenerator()
        assert generator.platform_name == "iceberg"

    def test_using_iceberg_in_clauses(self) -> None:

        generator = IcebergDDLGenerator()
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "USING ICEBERG" in clauses.additional_clauses


class TestIcebergPartitionTransforms:
    def test_date_column_uses_months(self) -> None:

        generator = IcebergDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (months(o_orderdate))"

    def test_timestamp_column_uses_days(self) -> None:

        generator = IcebergDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            partitioning=[TuningColumn(name="event_time", type="TIMESTAMP", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (days(event_time))"

    def test_integer_column_uses_bucket(self) -> None:

        generator = IcebergDDLGenerator(default_bucket_count=16)
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (bucket(16, l_orderkey))"

    def test_string_column_uses_identity(self) -> None:

        generator = IcebergDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderpriority", type="VARCHAR", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (o_orderpriority)"


class TestIcebergDistributionGeneration:
    def test_distribution_generates_bucket(self) -> None:

        generator = IcebergDDLGenerator(default_bucket_count=32)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (bucket(32, l_orderkey))"


class TestIcebergSortOrder:
    def test_sorting_generates_sort_order_property(self) -> None:

        generator = IcebergDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="DATE", order=1),
                TuningColumn(name="l_orderkey", type="BIGINT", order=2, sort_order="DESC"),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.table_properties["write.sort-order"] == "l_shipdate ASC, l_orderkey DESC"


class TestParquetDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = ParquetDDLGenerator()
        assert generator.platform_name == "parquet"

    def test_using_parquet_in_clauses(self) -> None:

        generator = ParquetDDLGenerator()
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "USING PARQUET" in clauses.additional_clauses


class TestParquetPartitioningGeneration:
    def test_basic_partitioning(self) -> None:

        generator = ParquetDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (o_orderdate)"


class TestParquetDistributionGeneration:
    def test_distribution_generates_clustered_by(self) -> None:

        generator = ParquetDDLGenerator(default_bucket_count=32)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.distribute_by == "CLUSTERED BY (l_orderkey) INTO 32 BUCKETS"


class TestParquetSortingGeneration:
    def test_sorting_generates_sorted_by(self) -> None:

        generator = ParquetDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.sort_by == "SORTED BY (l_shipdate)"

    def test_sorting_with_direction(self) -> None:

        generator = ParquetDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1, sort_order="DESC")],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.sort_by == "SORTED BY (l_shipdate DESC)"


class TestHiveDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = HiveDDLGenerator()
        assert generator.platform_name == "hive"

    def test_default_storage_format(self) -> None:

        generator = HiveDDLGenerator()
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "STORED AS PARQUET" in clauses.additional_clauses

    def test_custom_storage_format(self) -> None:

        generator = HiveDDLGenerator(storage_format="ORC")
        table_tuning = TableTuning(table_name="orders")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "STORED AS ORC" in clauses.additional_clauses


class TestHivePartitioningGeneration:
    def test_partitioning_includes_types(self) -> None:

        generator = HiveDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITIONED BY (o_orderdate DATE)"


class TestHiveDistributionGeneration:
    def test_distribution_with_sorting(self) -> None:

        generator = HiveDDLGenerator(default_bucket_count=16)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert "CLUSTERED BY (l_orderkey)" in clauses.distribute_by
        assert "SORTED BY (l_shipdate)" in clauses.distribute_by
        assert "INTO 16 BUCKETS" in clauses.distribute_by

    def test_distribution_without_sorting(self) -> None:

        generator = HiveDDLGenerator(default_bucket_count=16)
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.distribute_by == "CLUSTERED BY (l_orderkey) INTO 16 BUCKETS"


class TestCreateTableDDL:
    def test_delta_create_table(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True, enable_auto_optimize=True)
        columns = [
            ColumnDefinition("o_orderkey", "BIGINT"),
            ColumnDefinition("o_orderdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
            clustering=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("orders", columns, tuning)

        assert "CREATE TABLE orders" in ddl
        assert "USING DELTA" in ddl
        assert "PARTITIONED BY (o_orderdate)" in ddl
        assert "CLUSTER BY (o_orderkey)" in ddl
        assert "TBLPROPERTIES" in ddl
        assert ddl.endswith(";")

    def test_iceberg_create_table(self) -> None:

        generator = IcebergDDLGenerator(default_bucket_count=16)
        columns = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning)

        assert "CREATE TABLE lineitem" in ddl
        assert "USING ICEBERG" in ddl
        assert "PARTITIONED BY (months(l_shipdate))" in ddl
        assert ddl.endswith(";")

    def test_parquet_create_table(self) -> None:

        generator = ParquetDDLGenerator(default_bucket_count=32)
        columns = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
            sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning)

        assert "CREATE TABLE lineitem" in ddl
        assert "USING PARQUET" in ddl
        assert "CLUSTERED BY (l_orderkey) INTO 32 BUCKETS" in ddl
        assert "SORTED BY (l_shipdate)" in ddl
        assert ddl.endswith(";")

    def test_hive_create_table(self) -> None:

        generator = HiveDDLGenerator(storage_format="ORC", default_bucket_count=16)
        columns = [
            ColumnDefinition("o_orderkey", "BIGINT"),
            ColumnDefinition("o_orderdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
            distribution=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("orders", columns, tuning)

        assert "CREATE TABLE orders" in ddl
        assert "STORED AS ORC" in ddl
        assert "PARTITIONED BY (o_orderdate DATE)" in ddl
        assert "CLUSTERED BY (o_orderkey) INTO 16 BUCKETS" in ddl
        assert ddl.endswith(";")

    def test_create_table_if_not_exists(self) -> None:

        generator = DeltaDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = DeltaDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="tpch")
        assert "CREATE TABLE tpch.orders" in ddl


class TestPostLoadStatements:
    def test_delta_zorder_post_load(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=False)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        statements = generator.get_post_load_statements("lineitem", tuning)

        assert len(statements) == 1
        assert "OPTIMIZE lineitem ZORDER BY (l_orderkey)" in statements[0]

    def test_delta_zorder_with_schema(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=False)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        statements = generator.get_post_load_statements("lineitem", tuning, schema="tpch")

        assert len(statements) == 1
        assert "OPTIMIZE tpch.lineitem ZORDER BY (l_orderkey)" in statements[0]

    def test_no_post_load_for_liquid_clustering(self) -> None:

        generator = DeltaDDLGenerator(use_liquid_clustering=True)
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)
        statements = generator.get_post_load_statements("lineitem", tuning)

        assert len(statements) == 0


class TestNullTuningHandling:
    def test_delta_null_tuning(self) -> None:

        generator = DeltaDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert "USING DELTA" in clauses.additional_clauses

    def test_iceberg_null_tuning(self) -> None:

        generator = IcebergDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert "USING ICEBERG" in clauses.additional_clauses

    def test_parquet_null_tuning(self) -> None:

        generator = ParquetDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert "USING PARQUET" in clauses.additional_clauses

    def test_hive_null_tuning(self) -> None:

        generator = HiveDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert "STORED AS PARQUET" in clauses.additional_clauses


