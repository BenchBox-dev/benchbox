# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.duckdb import DuckDBDDLGenerator
from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestDuckDBTuningConfigFlow:
    def test_tuning_config_generates_sorted_ddl(self):

        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="date", order=1, sort_order="ASC"),
                TuningColumn(name="l_orderkey", type="integer", order=2, sort_order="ASC"),
            ],
        )

        generator = DuckDBDDLGenerator()
        clauses = generator.generate_tuning_clauses(table_tuning)

        assert clauses.sort_by is not None
        assert "l_shipdate" in clauses.sort_by
        assert "l_orderkey" in clauses.sort_by

    def test_tuning_config_executes_in_duckdb(self):

        pytest.importorskip("duckdb")
        import duckdb

        table_tuning = TableTuning(
            table_name="test_table",
            sorting=[
                TuningColumn(name="id", type="integer", order=1, sort_order="ASC"),
            ],
        )

        generator = DuckDBDDLGenerator()
        clauses = generator.generate_tuning_clauses(table_tuning)

        columns = [
            ColumnDefinition(name="id", data_type="INTEGER", nullable=ColumnNullability.NOT_NULL),
            ColumnDefinition(name="name", data_type="VARCHAR"),
            ColumnDefinition(name="value", data_type="DOUBLE"),
        ]

        ddl = generator.generate_create_table_ddl("test_table", columns, clauses)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = duckdb.connect(str(db_path))
            try:
                conn.execute(ddl)
                result = conn.execute("SELECT * FROM test_table").fetchall()
                assert result == []
            finally:
                conn.close()

    def test_unified_config_to_table_tuning(self):

        config = UnifiedTuningConfiguration()

        table_tuning = TableTuning(
            table_name="orders",
            sorting=[TuningColumn(name="o_orderdate", type="date", order=1, sort_order="ASC")],
            partitioning=[TuningColumn(name="o_orderdate", type="date", order=1)],
        )
        config.table_tunings["orders"] = table_tuning

        retrieved = config.table_tunings.get("orders")
        assert retrieved is not None
        assert retrieved.table_name == "orders"
        assert len(retrieved.sorting) == 1
        assert retrieved.sorting[0].name == "o_orderdate"

    def test_duckdb_parquet_sorting_post_load(self):

        pytest.importorskip("duckdb")

        generator = DuckDBDDLGenerator()

        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="date", order=1, sort_order="ASC"),
            ],
        )

        clauses = generator.generate_tuning_clauses(table_tuning)

        post_load = generator.get_post_load_statements("lineitem", clauses)

        assert isinstance(post_load, list)


class TestDuckDBDDLGeneratorIntegration:
    @pytest.fixture
    def duckdb_conn(self):
        duckdb = pytest.importorskip("duckdb")
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    def test_create_table_with_sorting(self, duckdb_conn):

        generator = DuckDBDDLGenerator()

        table_tuning = TableTuning(
            table_name="test_sorted",
            sorting=[TuningColumn(name="created_at", type="timestamp", order=1, sort_order="DESC")],
        )

        columns = [
            ColumnDefinition(name="id", data_type="BIGINT", nullable=ColumnNullability.NOT_NULL, primary_key=True),
            ColumnDefinition(name="created_at", data_type="TIMESTAMP"),
            ColumnDefinition(name="data", data_type="VARCHAR"),
        ]

        clauses = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("test_sorted", columns, clauses)

        duckdb_conn.execute(ddl)

        result = duckdb_conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'test_sorted' ORDER BY ordinal_position"
        ).fetchall()

        assert len(result) == 3
        assert result[0][0] == "id"
        assert result[1][0] == "created_at"
        assert result[2][0] == "data"

    def test_insert_and_query_sorted_table(self, duckdb_conn):

        generator = DuckDBDDLGenerator()

        table_tuning = TableTuning(
            table_name="events",
            sorting=[TuningColumn(name="event_time", type="timestamp", order=1, sort_order="ASC")],
        )

        columns = [
            ColumnDefinition(name="id", data_type="INTEGER"),
            ColumnDefinition(name="event_time", data_type="TIMESTAMP"),
            ColumnDefinition(name="event_type", data_type="VARCHAR"),
        ]

        clauses = generator.generate_tuning_clauses(table_tuning)
        ddl = generator.generate_create_table_ddl("events", columns, clauses)

        duckdb_conn.execute(ddl)

        duckdb_conn.execute(
            "INSERT INTO events VALUES "
            "(1, '2024-01-01 10:00:00', 'click'), "
            "(2, '2024-01-01 09:00:00', 'view'), "
            "(3, '2024-01-01 11:00:00', 'purchase')"
        )

        result = duckdb_conn.execute("SELECT id FROM events ORDER BY event_time").fetchall()
        assert [r[0] for r in result] == [2, 1, 3]


class TestTuningConfigIntegration:
    def test_config_serialization_roundtrip(self):

        config = UnifiedTuningConfiguration()

        config.primary_keys.enabled = True
        config.foreign_keys.enabled = False

        config.table_tunings["lineitem"] = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="date", order=1, sort_order="ASC"),
                TuningColumn(name="l_orderkey", type="integer", order=2, sort_order="ASC"),
            ],
        )

        config_dict = config.to_dict()

        restored = UnifiedTuningConfiguration.from_dict(config_dict)

        assert restored.primary_keys.enabled is True
        assert restored.foreign_keys.enabled is False

        lineitem_tuning = restored.table_tunings.get("lineitem")
        assert lineitem_tuning is not None
        assert len(lineitem_tuning.sorting) == 2

    def test_multiple_table_tunings(self):

        config = UnifiedTuningConfiguration()

        for table_name in ["lineitem", "orders", "customer"]:
            config.table_tunings[table_name] = TableTuning(
                table_name=table_name,
                sorting=[TuningColumn(name=f"{table_name[0]}_key", type="integer", order=1, sort_order="ASC")],
            )

        assert len(config.table_tunings) == 3
        assert config.table_tunings.get("lineitem") is not None
        assert config.table_tunings.get("orders") is not None
        assert config.table_tunings.get("customer") is not None
