# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.duckdb import (
    DuckDBDDLGenerator,
    get_duckdb_version,
    parse_version,
    supports_order_by,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestVersionParsing:
    def test_parse_simple_version(self) -> None:

        assert parse_version("0.10.0") == (0, 10, 0)
        assert parse_version("1.0.0") == (1, 0, 0)
        assert parse_version("0.9.2") == (0, 9, 2)

    def test_parse_version_with_v_prefix(self) -> None:

        assert parse_version("v0.10.0") == (0, 10, 0)
        assert parse_version("v1.2.3") == (1, 2, 3)

    def test_parse_dev_version(self) -> None:

        assert parse_version("0.10.2-dev123") == (0, 10, 2)
        assert parse_version("1.0.0-alpha") == (1, 0, 0)

    def test_parse_invalid_version(self) -> None:

        assert parse_version("invalid") == (0, 0, 0)
        assert parse_version("") == (0, 0, 0)

    def test_get_duckdb_version_returns_tuple(self) -> None:

        version = get_duckdb_version()
        assert isinstance(version, tuple)
        assert len(version) >= 3

    def test_supports_order_by_always_true(self) -> None:
        assert supports_order_by() is True


class TestDuckDBDDLGenerator:
    def test_platform_name(self) -> None:

        generator = DuckDBDDLGenerator()
        assert generator.platform_name == "duckdb"

    def test_supported_tuning_types(self) -> None:

        generator = DuckDBDDLGenerator()
        assert generator.supports_tuning_type("sorting")
        assert generator.supports_tuning_type("partitioning")
        assert not generator.supports_tuning_type("distribution")
        assert not generator.supports_tuning_type("clustering")

    def test_generate_tuning_clauses_with_sorting(self) -> None:

        generator = DuckDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="DATE", order=1),
                TuningColumn(name="l_orderkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.sort_by == "ORDER BY l_shipdate, l_orderkey"
        assert clauses.order_by is None

    def test_generate_tuning_clauses_respects_order(self) -> None:

        generator = DuckDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            sorting=[
                TuningColumn(name="o_orderkey", type="BIGINT", order=3),
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_custkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.sort_by == "ORDER BY o_orderdate, o_custkey, o_orderkey"

    def test_generate_tuning_clauses_with_none(self) -> None:

        generator = DuckDBDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert clauses.is_empty()

    def test_generate_tuning_clauses_empty_tuning(self) -> None:

        generator = DuckDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="test",
            partitioning=[TuningColumn(name="date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.sort_by is None
        assert clauses.order_by is None

    def test_distribution_warning_logged(self) -> None:

        generator = DuckDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="test",
            distribution=[TuningColumn(name="id", type="BIGINT", order=1)],
        )

        with patch("benchbox.core.tuning.generators.duckdb.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "Distribution tuning not applicable" in mock_logger.warning.call_args[0][0]

    def test_supports_order_by_clause_property(self) -> None:
        generator = DuckDBDDLGenerator()
        assert generator.supports_order_by_clause is True

        generator_with_check = DuckDBDDLGenerator(check_version=True)
        assert generator_with_check.supports_order_by_clause is True


class TestDuckDBDDLGeneratorCreateTable:
    def test_basic_create_table(self) -> None:

        generator = DuckDBDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "VARCHAR(100)"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id BIGINT NOT NULL" in ddl
        assert "name VARCHAR(100)" in ddl
        assert ddl.endswith(";")

    def test_create_table_without_order_by(self) -> None:
        generator = DuckDBDDLGenerator()
        columns = [
            ColumnDefinition("order_id", "BIGINT"),
            ColumnDefinition("order_date", "DATE"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="orders",
                sorting=[TuningColumn(name="order_date", type="DATE", order=1)],
            )
        )
        ddl = generator.generate_create_table_ddl("orders", columns, tuning=tuning)
        assert "CREATE TABLE orders" in ddl
        assert "ORDER BY" not in ddl
        assert tuning.sort_by == "ORDER BY order_date"

    def test_create_table_if_not_exists(self) -> None:

        generator = DuckDBDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = DuckDBDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="sales")
        assert "CREATE TABLE sales.orders" in ddl


class TestDuckDBCTAS:
    def test_basic_ctas(self) -> None:

        generator = DuckDBDDLGenerator()
        ddl = generator.generate_ctas_ddl(
            table_name="sorted_orders",
            source_query="SELECT * FROM raw_orders",
        )
        assert ddl == "CREATE TABLE sorted_orders AS SELECT * FROM raw_orders;"

    def test_ctas_with_sorting(self) -> None:

        generator = DuckDBDDLGenerator()
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="orders",
                sorting=[
                    TuningColumn(name="order_date", type="DATE", order=1),
                    TuningColumn(name="order_id", type="BIGINT", order=2),
                ],
            )
        )
        ddl = generator.generate_ctas_ddl(
            table_name="sorted_orders",
            source_query="SELECT * FROM raw_orders",
            tuning=tuning,
        )
        assert "CREATE TABLE sorted_orders AS SELECT * FROM raw_orders" in ddl
        assert "ORDER BY order_date, order_id" in ddl
        assert ddl.endswith(";")

    def test_ctas_with_or_replace(self) -> None:
        generator = DuckDBDDLGenerator()
        ddl = generator.generate_ctas_ddl(
            table_name="test",
            source_query="SELECT 1 AS a",
            or_replace=True,
        )
        assert "CREATE OR REPLACE TABLE test" in ddl

    def test_ctas_with_schema(self) -> None:

        generator = DuckDBDDLGenerator()
        ddl = generator.generate_ctas_ddl(
            table_name="orders",
            source_query="SELECT * FROM staging.orders",
            schema="production",
        )
        assert "CREATE TABLE production.orders" in ddl


class TestDuckDBPartitionedExport:
    def test_generate_copy_to_partitioned(self) -> None:

        generator = DuckDBDDLGenerator()
        sql = generator.generate_copy_to_partitioned(
            source_query="SELECT * FROM lineitem",
            destination_path="/data/tpch/lineitem",
            partition_columns=["l_shipdate"],
        )
        assert "COPY (SELECT * FROM lineitem) TO '/data/tpch/lineitem'" in sql
        assert "FORMAT PARQUET" in sql
        assert "PARTITION_BY (l_shipdate)" in sql

    def test_generate_copy_to_multiple_partitions(self) -> None:

        generator = DuckDBDDLGenerator()
        sql = generator.generate_copy_to_partitioned(
            source_query="SELECT * FROM orders",
            destination_path="/data/orders",
            partition_columns=["order_year", "order_month"],
        )
        assert "PARTITION_BY (order_year, order_month)" in sql

    def test_generate_copy_to_csv_format(self) -> None:

        generator = DuckDBDDLGenerator()
        sql = generator.generate_copy_to_partitioned(
            source_query="SELECT * FROM data",
            destination_path="/output",
            partition_columns=["category"],
            file_format="CSV",
        )
        assert "FORMAT CSV" in sql


class TestDuckDBIntegration:
    @pytest.mark.integration
    def test_create_table_executes(self) -> None:

        import duckdb

        generator = DuckDBDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("created_at", "TIMESTAMP"),
            ColumnDefinition("value", "DOUBLE"),
        ]
        ddl = generator.generate_create_table_ddl("events", columns)

        conn = duckdb.connect(":memory:")
        try:
            conn.execute(ddl)
            result = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            assert result[0] == 0
        finally:
            conn.close()

    @pytest.mark.integration
    def test_ctas_with_sorting_executes(self) -> None:

        import duckdb

        generator = DuckDBDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            sorting=[
                TuningColumn(name="created_at", type="TIMESTAMP", order=1),
                TuningColumn(name="id", type="BIGINT", order=2),
            ],
        )
        tuning = generator.generate_tuning_clauses(table_tuning)

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("""
                CREATE TABLE raw_events (
                    id BIGINT,
                    created_at TIMESTAMP,
                    value DOUBLE
                )
            """)
            conn.execute("""
                INSERT INTO raw_events VALUES
                (3, '2024-01-03 10:00:00', 30.0),
                (1, '2024-01-01 10:00:00', 10.0),
                (2, '2024-01-02 10:00:00', 20.0)
            """)

            ctas_ddl = generator.generate_ctas_ddl(
                table_name="sorted_events",
                source_query="SELECT * FROM raw_events",
                tuning=tuning,
            )
            conn.execute(ctas_ddl)

            result = conn.execute("SELECT COUNT(*) FROM sorted_events").fetchone()
            assert result[0] == 3

            rows = conn.execute("SELECT id FROM sorted_events").fetchall()
            assert [r[0] for r in rows] == [1, 2, 3]
        finally:
            conn.close()

    @pytest.mark.integration
    def test_copy_to_partitioned_executes(self) -> None:

        import tempfile
        from pathlib import Path

        import duckdb

        generator = DuckDBDDLGenerator()

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("""
                CREATE TABLE orders (
                    order_id INTEGER,
                    order_date DATE,
                    amount DOUBLE
                )
            """)
            conn.execute("""
                INSERT INTO orders VALUES
                (1, '2024-01-15', 100.0),
                (2, '2024-01-16', 200.0),
                (3, '2024-02-01', 150.0)
            """)

            with tempfile.TemporaryDirectory() as tmpdir:
                output_path = Path(tmpdir) / "orders"
                sql = generator.generate_copy_to_partitioned(
                    source_query="SELECT order_id, order_date, amount FROM orders",
                    destination_path=str(output_path),
                    partition_columns=["order_date"],
                )
                conn.execute(sql)

                assert output_path.exists()
                parquet_files = list(output_path.rglob("*.parquet"))
                assert len(parquet_files) > 0
        finally:
            conn.close()


