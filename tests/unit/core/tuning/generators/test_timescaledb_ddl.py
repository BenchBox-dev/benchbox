# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.timescaledb import TimescaleDBDDLGenerator
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTimescaleDBDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = TimescaleDBDDLGenerator()
        assert generator.platform_name == "timescaledb"

    def test_inherits_postgresql_tuning_types(self) -> None:

        generator = TimescaleDBDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("clustering")
        assert generator.supports_tuning_type("sorting")


class TestHypertableGeneration:
    def test_basic_hypertable(self) -> None:

        generator = TimescaleDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 1
        stmt = clauses.post_create_statements[0]
        assert "create_hypertable('{table_name}', 'time'" in stmt
        assert "chunk_time_interval" in stmt

    def test_custom_chunk_interval(self) -> None:

        generator = TimescaleDBDDLGenerator(default_chunk_interval="INTERVAL '1 week'")
        table_tuning = TableTuning(
            table_name="events",
            partitioning=[TuningColumn(name="event_time", type="TIMESTAMPTZ", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert "INTERVAL '1 week'" in clauses.post_create_statements[0]

    def test_space_partitioning(self) -> None:

        generator = TimescaleDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
            distribution=[TuningColumn(name="device_id", type="INTEGER", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        stmt = clauses.post_create_statements[0]
        assert "partitioning_column => 'device_id'" in stmt
        assert "number_partitions => 4" in stmt

    def test_no_hypertable_without_partitioning(self) -> None:

        generator = TimescaleDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="metrics",
            clustering=[TuningColumn(name="device_id", type="INTEGER", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 0


class TestCompressionGeneration:
    def test_compression_disabled_by_default(self) -> None:

        generator = TimescaleDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 1
        assert "compress" not in clauses.post_create_statements[0].lower()

    def test_compression_enabled(self) -> None:

        generator = TimescaleDBDDLGenerator(enable_compression=True)
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert len(clauses.post_create_statements) == 3
        assert "timescaledb.compress" in clauses.post_create_statements[1]
        assert "add_compression_policy" in clauses.post_create_statements[2]

    def test_compression_with_segmentby(self) -> None:

        generator = TimescaleDBDDLGenerator(enable_compression=True)
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
            distribution=[TuningColumn(name="device_id", type="INTEGER", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        alter_stmt = clauses.post_create_statements[1]
        assert "compress_segmentby = 'device_id'" in alter_stmt

    def test_compression_with_orderby(self) -> None:

        generator = TimescaleDBDDLGenerator(enable_compression=True)
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
            sorting=[
                TuningColumn(name="value", type="DOUBLE PRECISION", order=1, sort_order="DESC"),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        alter_stmt = clauses.post_create_statements[1]
        assert "compress_orderby = 'value DESC'" in alter_stmt

    def test_custom_compression_after(self) -> None:

        generator = TimescaleDBDDLGenerator(
            enable_compression=True,
            compression_after="INTERVAL '30 days'",
        )
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)

        policy_stmt = clauses.post_create_statements[2]
        assert "INTERVAL '30 days'" in policy_stmt


class TestPartitionChildren:
    def test_no_partition_children(self) -> None:

        generator = TimescaleDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
        )
        columns = [ColumnDefinition("time", "TIMESTAMPTZ")]
        tuning = generator.generate_tuning_clauses(table_tuning)

        children = generator.generate_partition_children("metrics", columns, tuning, table_tuning)

        assert len(children) == 0


class TestCreateTableDDL:
    def test_basic_create_table(self) -> None:

        generator = TimescaleDBDDLGenerator()
        columns = [
            ColumnDefinition("time", "TIMESTAMPTZ", ColumnNullability.NOT_NULL),
            ColumnDefinition("device_id", "INTEGER", ColumnNullability.NOT_NULL),
            ColumnDefinition("temperature", "DOUBLE PRECISION"),
        ]
        ddl = generator.generate_create_table_ddl("metrics", columns)
        assert "CREATE TABLE metrics" in ddl
        assert "time TIMESTAMPTZ NOT NULL" in ddl
        assert ddl.endswith(";")

    def test_full_workflow(self) -> None:

        generator = TimescaleDBDDLGenerator(
            default_chunk_interval="INTERVAL '1 day'",
            enable_compression=True,
        )
        columns = [
            ColumnDefinition("time", "TIMESTAMPTZ", ColumnNullability.NOT_NULL),
            ColumnDefinition("device_id", "INTEGER"),
            ColumnDefinition("value", "DOUBLE PRECISION"),
        ]
        table_tuning = TableTuning(
            table_name="metrics",
            partitioning=[TuningColumn(name="time", type="TIMESTAMPTZ", order=1)],
            distribution=[TuningColumn(name="device_id", type="INTEGER", order=1)],
        )

        tuning = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("metrics", columns)

        assert "CREATE TABLE metrics" in ddl

        assert len(tuning.post_create_statements) == 3

        assert "create_hypertable" in tuning.post_create_statements[0]
        assert "device_id" in tuning.post_create_statements[0]

        assert "timescaledb.compress" in tuning.post_create_statements[1]
        assert "add_compression_policy" in tuning.post_create_statements[2]


