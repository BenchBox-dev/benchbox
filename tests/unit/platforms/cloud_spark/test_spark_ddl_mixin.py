# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import ClassVar

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition
from benchbox.core.tuning.interface import TableTuning
from benchbox.platforms.base.cloud_spark.mixins import (
    SparkDDLGeneratorMixin,
    SparkTableFormat,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockSparkAdapter(SparkDDLGeneratorMixin):
    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.DELTA


class MockParquetAdapter(SparkDDLGeneratorMixin):
    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.PARQUET


class MockIcebergAdapter(SparkDDLGeneratorMixin):
    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.ICEBERG


class MockHiveAdapter(SparkDDLGeneratorMixin):
    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.HIVE


class TestSparkTableFormatEnum:
    def test_enum_values(self) -> None:

        assert SparkTableFormat.DELTA.value == "delta"
        assert SparkTableFormat.ICEBERG.value == "iceberg"
        assert SparkTableFormat.PARQUET.value == "parquet"
        assert SparkTableFormat.HIVE.value == "hive"


class TestGetTableFormat:
    def test_default_format(self) -> None:

        adapter = MockSparkAdapter()
        assert adapter.get_table_format() == SparkTableFormat.DELTA

    def test_parquet_default(self) -> None:

        adapter = MockParquetAdapter()
        assert adapter.get_table_format() == SparkTableFormat.PARQUET


class TestMixinSupportsTuningTypes:
    def test_supports_all_tuning_types(self) -> None:
        adapter = MockSparkAdapter()
        assert adapter.supports_tuning_type("partitioning")
        assert adapter.supports_tuning_type("clustering")
        assert adapter.supports_tuning_type("sorting")
        assert adapter.supports_tuning_type("distribution")


class TestMixinDeltaTuning:
    def test_delta_partitioning(self) -> None:
        adapter = MockSparkAdapter()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        layout = adapter.generate_table_layout(table_tuning)
        assert layout["format"] == "delta"
        assert layout["partition_by"] == ["o_orderdate"]

    def test_delta_clustering(self) -> None:
        adapter = MockSparkAdapter()
        table_tuning = TableTuning(
            table_name="orders",
            clustering=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )
        assert adapter.generate_table_layout(table_tuning)["cluster_by"] == ["o_orderkey"]


class TestMixinIcebergTuning:
    def test_iceberg_partitioning(self) -> None:
        adapter = MockIcebergAdapter()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        layout = adapter.generate_table_layout(table_tuning)
        assert layout["format"] == "iceberg"
        assert layout["partition_by"] == ["months(o_orderdate)"]


class TestMixinParquetTuning:
    def test_parquet_distribution(self) -> None:
        adapter = MockParquetAdapter()
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        layout = adapter.generate_table_layout(table_tuning)
        assert layout["format"] == "parquet"
        assert layout["distribute_by"] == ["l_orderkey"]
        assert layout["buckets"] == 32


class TestMixinHiveTuning:
    def test_hive_storage_format(self) -> None:
        adapter = MockHiveAdapter()
        layout = adapter.generate_table_layout(TableTuning(table_name="orders"))
        assert layout == {"stored_as": "PARQUET"}


class TestMixinCreateTableDDL:
    def test_delta_create_table(self) -> None:
        adapter = MockSparkAdapter()
        columns = [
            ColumnDefinition("o_orderkey", "BIGINT"),
            ColumnDefinition("o_orderdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )
        ddl = adapter.generate_create_table_ddl("orders", columns, adapter.generate_table_layout(table_tuning))

        assert "CREATE TABLE orders" in ddl
        assert "USING DELTA" in ddl
        assert "PARTITIONED BY (o_orderdate)" in ddl

    def test_iceberg_create_table(self) -> None:
        adapter = MockIcebergAdapter()
        columns = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        ddl = adapter.generate_create_table_ddl("lineitem", columns, adapter.generate_table_layout(table_tuning))

        assert "CREATE TABLE lineitem" in ddl
        assert "USING ICEBERG" in ddl
        assert "PARTITIONED BY (MONTHS(l_shipdate))" in ddl

    def test_create_table_with_schema(self) -> None:
        adapter = MockSparkAdapter()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = adapter.generate_create_table_ddl("orders", columns, schema="tpch")
        assert "CREATE TABLE tpch.orders" in ddl

    def test_create_table_if_not_exists(self) -> None:
        adapter = MockSparkAdapter()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = adapter.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl


class TestMixinPostLoadStatements:
    def test_delta_zorder(self) -> None:
        adapter = MockSparkAdapter()
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        statements = adapter.get_post_load_statements("lineitem", table_tuning)

        assert statements == ["OPTIMIZE lineitem ZORDER BY (l_orderkey)"]

    def test_post_load_with_schema(self) -> None:
        adapter = MockSparkAdapter()
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=1)],
        )
        statements = adapter.get_post_load_statements("lineitem", table_tuning, schema="tpch")

        assert statements == ["OPTIMIZE tpch.lineitem ZORDER BY (l_orderkey)"]


class TestMixinNullHandling:
    def test_null_tuning_returns_format(self) -> None:
        adapter = MockSparkAdapter()
        assert adapter.generate_table_layout(None)["format"] == "delta"

    def test_null_tuning_empty_partitioning(self) -> None:
        adapter = MockSparkAdapter()
        layout = adapter.generate_table_layout(None)
        assert "partition_by" not in layout
        assert "cluster_by" not in layout
