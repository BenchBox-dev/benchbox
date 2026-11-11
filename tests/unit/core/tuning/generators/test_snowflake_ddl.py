# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.snowflake import (
    SearchOptimizationType,
    SnowflakeDDLGenerator,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSnowflakeDDLGeneratorBasics:
    def test_platform_name(self) -> None:

        generator = SnowflakeDDLGenerator()
        assert generator.platform_name == "snowflake"

    def test_supported_tuning_types(self) -> None:

        generator = SnowflakeDDLGenerator()
        assert generator.supports_tuning_type("clustering")
        assert generator.supports_tuning_type("sorting")
        assert not generator.supports_tuning_type("partitioning")
        assert not generator.supports_tuning_type("distribution")


class TestClusterByGeneration:
    def test_basic_cluster_by(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            clustering=[
                TuningColumn(name="l_shipdate", type="DATE", order=1),
                TuningColumn(name="l_orderkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (l_shipdate, l_orderkey)"

    def test_cluster_by_respects_order(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            clustering=[
                TuningColumn(name="o_orderkey", type="BIGINT", order=3),
                TuningColumn(name="o_orderdate", type="DATE", order=1),
                TuningColumn(name="o_custkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (o_orderdate, o_custkey, o_orderkey)"

    def test_cluster_by_limit_enforced(self) -> None:

        generator = SnowflakeDDLGenerator(max_cluster_columns=3)
        table_tuning = TableTuning(
            table_name="wide_table",
            clustering=[
                TuningColumn(name="col1", type="VARCHAR", order=1),
                TuningColumn(name="col2", type="VARCHAR", order=2),
                TuningColumn(name="col3", type="VARCHAR", order=3),
                TuningColumn(name="col4", type="VARCHAR", order=4),
                TuningColumn(name="col5", type="VARCHAR", order=5),
            ],
        )

        with patch("benchbox.core.tuning.generators.snowflake.logger") as mock_logger:
            clauses = generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "max 3 clustering columns" in mock_logger.warning.call_args[0][0]

        assert clauses.cluster_by == "CLUSTER BY (col1, col2, col3)"

    def test_sorting_maps_to_clustering(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            sorting=[
                TuningColumn(name="event_date", type="DATE", order=1),
                TuningColumn(name="event_id", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (event_date, event_id)"

    def test_combined_clustering_and_sorting(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="events",
            clustering=[TuningColumn(name="event_date", type="DATE", order=1)],
            sorting=[TuningColumn(name="user_id", type="BIGINT", order=2)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by == "CLUSTER BY (event_date, user_id)"

    def test_no_clustering_produces_empty_clauses(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(table_name="simple_table")
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.cluster_by is None
        assert clauses.is_empty()


class TestDistributionWarning:
    def test_distribution_logs_warning(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_orderkey", type="BIGINT", order=1)],
        )

        with patch("benchbox.core.tuning.generators.snowflake.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.warning.assert_called_once()
            assert "not applicable for Snowflake" in mock_logger.warning.call_args[0][0]


class TestPartitioningInfo:
    def test_partitioning_logs_info(self) -> None:

        generator = SnowflakeDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
        )

        with patch("benchbox.core.tuning.generators.snowflake.logger") as mock_logger:
            generator.generate_tuning_clauses(table_tuning)
            mock_logger.info.assert_called_once()
            assert "automatic micro-partitioning" in mock_logger.info.call_args[0][0]


class TestCreateTableDDL:
    def test_basic_create_table(self) -> None:

        generator = SnowflakeDDLGenerator()
        columns = [
            ColumnDefinition("id", "NUMBER(38,0)", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "VARCHAR(100)"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id NUMBER(38,0) NOT NULL" in ddl
        assert "name VARCHAR(100)" in ddl
        assert ddl.endswith(";")

    def test_create_table_with_clustering(self) -> None:

        generator = SnowflakeDDLGenerator()
        columns = [
            ColumnDefinition("l_orderkey", "NUMBER(38,0)", ColumnNullability.NOT_NULL),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        tuning = generator.generate_tuning_clauses(
            TableTuning(
                table_name="lineitem",
                clustering=[
                    TuningColumn(name="l_shipdate", type="DATE", order=1),
                    TuningColumn(name="l_orderkey", type="NUMBER", order=2),
                ],
            )
        )
        ddl = generator.generate_create_table_ddl("lineitem", columns, tuning=tuning)
        assert "CREATE TABLE lineitem" in ddl
        assert "CLUSTER BY (l_shipdate, l_orderkey)" in ddl
        assert ddl.endswith(";")

    def test_create_table_if_not_exists(self) -> None:

        generator = SnowflakeDDLGenerator()
        columns = [ColumnDefinition("id", "NUMBER(38,0)")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_create_table_with_schema(self) -> None:

        generator = SnowflakeDDLGenerator()
        columns = [ColumnDefinition("id", "NUMBER(38,0)")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="tpch")
        assert "CREATE TABLE tpch.orders" in ddl


class TestSearchOptimization:
    def test_generate_search_optimization_equality(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_search_optimization(
            table_name="lineitem",
            column="l_orderkey",
            opt_type=SearchOptimizationType.EQUALITY,
        )
        assert "ALTER TABLE lineitem ADD SEARCH OPTIMIZATION ON EQUALITY(l_orderkey)" in sql
        assert sql.endswith(";")

    def test_generate_search_optimization_substring(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_search_optimization(
            table_name="customer",
            column="c_name",
            opt_type=SearchOptimizationType.SUBSTRING,
        )
        assert "ON SUBSTRING(c_name)" in sql

    def test_generate_search_optimization_with_schema(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_search_optimization(
            table_name="lineitem",
            column="l_orderkey",
            schema="tpch",
        )
        assert "ALTER TABLE tpch.lineitem" in sql


class TestClusteringInfoQuery:
    def test_generate_clustering_info_query(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_clustering_info_query(
            table_name="lineitem",
            cluster_columns=["l_shipdate", "l_orderkey"],
        )
        assert "SYSTEM$CLUSTERING_INFORMATION" in sql
        assert "'lineitem'" in sql
        assert "'(l_shipdate, l_orderkey)'" in sql


class TestResumeRecluster:
    def test_generate_resume_recluster(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_resume_recluster("lineitem")
        assert sql == "ALTER TABLE lineitem RESUME RECLUSTER;"

    def test_generate_resume_recluster_with_schema(self) -> None:

        generator = SnowflakeDDLGenerator()
        sql = generator.generate_resume_recluster("lineitem", schema="tpch")
        assert sql == "ALTER TABLE tpch.lineitem RESUME RECLUSTER;"


class TestSearchOptimizationEnum:
    def test_enum_values(self) -> None:

        assert SearchOptimizationType.EQUALITY.value == "EQUALITY"
        assert SearchOptimizationType.SUBSTRING.value == "SUBSTRING"
        assert SearchOptimizationType.GEO.value == "GEO"


