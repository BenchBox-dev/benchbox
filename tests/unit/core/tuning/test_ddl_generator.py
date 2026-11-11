# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import (
    BaseDDLGenerator,
    ColumnDefinition,
    ColumnNullability,
    DDLGenerator,
    NoOpDDLGenerator,
    TuningClauses,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestColumnDefinition:
    def test_basic_column(self) -> None:

        col = ColumnDefinition(name="id", data_type="BIGINT")
        assert col.name == "id"
        assert col.data_type == "BIGINT"
        assert col.nullable == ColumnNullability.DEFAULT
        assert col.default_value is None
        assert col.primary_key is False

    def test_not_null_column(self) -> None:

        col = ColumnDefinition(
            name="customer_id",
            data_type="INTEGER",
            nullable=ColumnNullability.NOT_NULL,
        )
        assert col.nullable == ColumnNullability.NOT_NULL

    def test_primary_key_column(self) -> None:

        col = ColumnDefinition(
            name="id",
            data_type="BIGINT",
            nullable=ColumnNullability.NOT_NULL,
            primary_key=True,
        )
        assert col.primary_key is True

    def test_column_with_default(self) -> None:

        col = ColumnDefinition(
            name="status",
            data_type="VARCHAR(50)",
            default_value="'active'",
        )
        assert col.default_value == "'active'"

    def test_column_to_dict(self) -> None:

        col = ColumnDefinition(
            name="price",
            data_type="DECIMAL(10,2)",
            nullable=ColumnNullability.NOT_NULL,
            default_value="0.00",
            comment="Product price",
        )
        d = col.to_dict()
        assert d["name"] == "price"
        assert d["data_type"] == "DECIMAL(10,2)"
        assert d["nullable"] == "not_null"
        assert d["default_value"] == "0.00"
        assert d["comment"] == "Product price"

    def test_column_from_dict(self) -> None:

        data = {
            "name": "quantity",
            "data_type": "INTEGER",
            "nullable": "not_null",
            "primary_key": True,
        }
        col = ColumnDefinition.from_dict(data)
        assert col.name == "quantity"
        assert col.data_type == "INTEGER"
        assert col.nullable == ColumnNullability.NOT_NULL
        assert col.primary_key is True


class TestTuningClauses:
    def test_empty_clauses(self) -> None:

        clauses = TuningClauses()
        assert clauses.is_empty()

    def test_non_empty_clauses(self) -> None:

        clauses = TuningClauses(partition_by="PARTITION BY DATE(order_date)")
        assert not clauses.is_empty()

    def test_inline_clauses_order(self) -> None:

        clauses = TuningClauses(
            distribute_by="DISTSTYLE KEY DISTKEY(customer_id)",
            partition_by="PARTITION BY order_date",
            sort_by="SORTKEY(order_date, customer_id)",
        )
        inline = clauses.get_inline_clauses()
        assert inline[0] == "DISTSTYLE KEY DISTKEY(customer_id)"
        assert inline[1] == "PARTITION BY order_date"
        assert inline[2] == "SORTKEY(order_date, customer_id)"

    def test_inline_clauses_distribute_after_partition_for_starrocks(self) -> None:
        for platform in ("starrocks", "doris"):
            clauses = TuningClauses(
                platform=platform,
                partition_by="l_shipdate",
                distribute_by="DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8",
                order_by="l_linenumber",
            )
            inline = clauses.get_inline_clauses()
            assert inline.index("l_shipdate") < inline.index("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8")

    def test_platform_marker_ignored_by_is_empty(self) -> None:
        assert TuningClauses(platform="starrocks").is_empty()

    def test_empty_but_marked_serializes_as_empty(self) -> None:
        assert TuningClauses(platform="starrocks").to_dict() == {}

    def test_inline_clauses_doris_duplicate_key_first(self) -> None:
        clauses = TuningClauses(
            platform="doris",
            sort_by="l_orderkey, l_linenumber",
            partition_by="l_shipdate",
            distribute_by="DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 10",
        )
        inline = clauses.get_inline_clauses()
        assert inline.index("l_orderkey, l_linenumber") < inline.index("l_shipdate")
        assert inline.index("l_shipdate") < inline.index("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 10")

    def test_platform_marker_serialized_only_when_set(self) -> None:
        assert "platform" not in TuningClauses(distribute_by="DISTSTYLE KEY").to_dict()
        clauses = TuningClauses(distribute_by="DISTRIBUTED BY HASH(`c`) BUCKETS 8", platform="starrocks")
        assert clauses.to_dict()["platform"] == "starrocks"
        assert TuningClauses.from_dict(clauses.to_dict()).platform == "starrocks"

    def test_merge_propagates_platform_marker(self) -> None:
        base = TuningClauses(partition_by="l_shipdate")
        overlay = TuningClauses(
            distribute_by="DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8",
            platform="starrocks",
        )
        merged = base.merge(overlay)
        assert merged.platform == "starrocks"
        inline = merged.get_inline_clauses()
        assert inline.index("l_shipdate") < inline.index("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8")

    def test_table_properties_clause(self) -> None:

        clauses = TuningClauses(
            table_properties={
                "delta.autoOptimize.optimizeWrite": "true",
                "delta.autoOptimize.autoCompact": "true",
            }
        )
        props = clauses.get_table_properties_clause()
        assert props is not None
        assert "TBLPROPERTIES" in props
        assert "delta.autoOptimize.optimizeWrite" in props

    def test_table_options_clause(self) -> None:
        clauses = TuningClauses(
            table_options={
                "require_partition_filter": True,
                "description": "Order data",
            }
        )
        opts = clauses.get_table_options_clause()
        assert opts is not None
        assert "OPTIONS" in opts
        assert "require_partition_filter = true" in opts
        assert "description = 'Order data'" in opts

    def test_to_dict_serialization(self) -> None:

        clauses = TuningClauses(
            partition_by="PARTITION BY DATE(ts)",
            cluster_by="CLUSTER BY (id, category)",
            post_create_statements=["ANALYZE {table_name}"],
        )
        d = clauses.to_dict()
        assert d["partition_by"] == "PARTITION BY DATE(ts)"
        assert d["cluster_by"] == "CLUSTER BY (id, category)"
        assert d["post_create_statements"] == ["ANALYZE {table_name}"]

    def test_to_json_serialization(self) -> None:

        clauses = TuningClauses(
            distribute_by="DISTSTYLE KEY",
            table_properties={"key": "value"},
        )
        json_str = clauses.to_json()
        data = json.loads(json_str)
        assert data["distribute_by"] == "DISTSTYLE KEY"
        assert data["table_properties"]["key"] == "value"

    def test_from_dict_deserialization(self) -> None:

        data = {
            "partition_by": "PARTITION BY l_shipdate",
            "order_by": "ORDER BY (l_orderkey)",
            "table_properties": {"setting": "value"},
        }
        clauses = TuningClauses.from_dict(data)
        assert clauses.partition_by == "PARTITION BY l_shipdate"
        assert clauses.order_by == "ORDER BY (l_orderkey)"
        assert clauses.table_properties == {"setting": "value"}

    def test_merge_clauses(self) -> None:

        base = TuningClauses(
            partition_by="PARTITION BY date",
            table_properties={"a": "1"},
            post_create_statements=["ANALYZE"],
        )
        overlay = TuningClauses(
            cluster_by="CLUSTER BY (id)",
            table_properties={"b": "2"},
            post_create_statements=["OPTIMIZE"],
        )
        merged = base.merge(overlay)

        assert merged.partition_by == "PARTITION BY date"
        assert merged.cluster_by == "CLUSTER BY (id)"
        assert merged.table_properties == {"a": "1", "b": "2"}
        assert merged.post_create_statements == ["ANALYZE", "OPTIMIZE"]


class TestNoOpDDLGenerator:
    def test_returns_empty_clauses(self) -> None:

        generator = NoOpDDLGenerator(platform="sqlite")
        table_tuning = TableTuning(
            table_name="test",
            sorting=[TuningColumn(name="id", type="INTEGER", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.is_empty()

    def test_platform_name(self) -> None:

        generator = NoOpDDLGenerator(platform="datafusion")
        assert generator.platform_name == "datafusion"

    def test_supports_no_tuning_types(self) -> None:

        generator = NoOpDDLGenerator(platform="sqlite")
        assert not generator.supports_tuning_type("partitioning")
        assert not generator.supports_tuning_type("clustering")
        assert not generator.supports_tuning_type("distribution")
        assert not generator.supports_tuning_type("sorting")


class MockDDLGenerator(BaseDDLGenerator):
    SUPPORTED_TUNING_TYPES = frozenset({"partitioning", "sorting", "clustering"})

    @property
    def platform_name(self) -> str:
        return "mock"

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts=None,
    ) -> TuningClauses:
        if not table_tuning:
            return TuningClauses()

        clauses = TuningClauses()

        if table_tuning.partitioning:
            cols = [c.name for c in sorted(table_tuning.partitioning, key=lambda x: x.order)]
            clauses.partition_by = f"PARTITION BY ({', '.join(cols)})"

        if table_tuning.sorting:
            cols = [c.name for c in sorted(table_tuning.sorting, key=lambda x: x.order)]
            clauses.order_by = f"ORDER BY ({', '.join(cols)})"

        if table_tuning.clustering:
            cols = [c.name for c in sorted(table_tuning.clustering, key=lambda x: x.order)]
            clauses.cluster_by = f"CLUSTER BY ({', '.join(cols)})"

        return clauses


class TestBaseDDLGenerator:
    def test_quote_identifier_simple(self) -> None:

        generator = MockDDLGenerator()
        assert generator.quote_identifier("orders") == "orders"

    def test_quote_identifier_special(self) -> None:

        generator = MockDDLGenerator()
        assert generator.quote_identifier("Order") == '"Order"'
        assert generator.quote_identifier("order-items") == '"order-items"'

    def test_format_qualified_name_without_schema(self) -> None:

        generator = MockDDLGenerator()
        assert generator.format_qualified_name("orders") == "orders"

    def test_format_qualified_name_with_schema(self) -> None:

        generator = MockDDLGenerator()
        result = generator.format_qualified_name("orders", schema="sales")
        assert result == "sales.orders"

    def test_generate_column_list(self) -> None:

        generator = MockDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL, primary_key=True),
            ColumnDefinition("name", "VARCHAR(100)", ColumnNullability.NULLABLE),
            ColumnDefinition("price", "DECIMAL(10,2)", ColumnNullability.NOT_NULL, default_value="0.00"),
        ]
        col_list = generator.generate_column_list(columns)
        assert "id BIGINT NOT NULL PRIMARY KEY" in col_list
        assert "name VARCHAR(100) NULL" in col_list
        assert "price DECIMAL(10,2) NOT NULL DEFAULT 0.00" in col_list

    def test_generate_create_table_basic(self) -> None:

        generator = MockDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
            ColumnDefinition("name", "VARCHAR(100)"),
        ]
        ddl = generator.generate_create_table_ddl("customers", columns)
        assert "CREATE TABLE customers" in ddl
        assert "id BIGINT NOT NULL" in ddl
        assert "name VARCHAR(100)" in ddl
        assert ddl.endswith(";")

    def test_generate_create_table_with_if_not_exists(self) -> None:

        generator = MockDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("test", columns, if_not_exists=True)
        assert "CREATE TABLE IF NOT EXISTS test" in ddl

    def test_generate_create_table_with_schema(self) -> None:

        generator = MockDDLGenerator()
        columns = [ColumnDefinition("id", "BIGINT")]
        ddl = generator.generate_create_table_ddl("orders", columns, schema="sales")
        assert "CREATE TABLE sales.orders" in ddl

    def test_generate_create_table_with_tuning(self) -> None:

        generator = MockDDLGenerator()
        columns = [
            ColumnDefinition("id", "BIGINT"),
            ColumnDefinition("date", "DATE"),
        ]
        tuning = TuningClauses(
            partition_by="PARTITION BY (date)",
            order_by="ORDER BY (id)",
        )
        ddl = generator.generate_create_table_ddl("events", columns, tuning=tuning)
        assert "CREATE TABLE events" in ddl
        assert "PARTITION BY (date)" in ddl
        assert "ORDER BY (id)" in ddl

    def test_generate_tuning_clauses_with_partitioning(self) -> None:

        generator = MockDDLGenerator()
        table_tuning = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.partition_by == "PARTITION BY (order_date)"

    def test_generate_tuning_clauses_with_sorting(self) -> None:

        generator = MockDDLGenerator()
        table_tuning = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn(name="l_shipdate", type="DATE", order=1),
                TuningColumn(name="l_orderkey", type="BIGINT", order=2),
            ],
        )
        clauses = generator.generate_tuning_clauses(table_tuning)
        assert clauses.order_by == "ORDER BY (l_shipdate, l_orderkey)"

    def test_generate_tuning_clauses_with_none(self) -> None:

        generator = MockDDLGenerator()
        clauses = generator.generate_tuning_clauses(None)
        assert clauses.is_empty()

    def test_supports_tuning_type(self) -> None:

        generator = MockDDLGenerator()
        assert generator.supports_tuning_type("partitioning")
        assert generator.supports_tuning_type("sorting")
        assert generator.supports_tuning_type("CLUSTERING")
        assert not generator.supports_tuning_type("distribution")

    def test_get_post_load_statements(self) -> None:

        generator = MockDDLGenerator()
        tuning = TuningClauses(
            post_create_statements=[
                "ANALYZE {table_name}",
                "OPTIMIZE {table_name}",
            ]
        )
        stmts = generator.get_post_load_statements("orders", tuning)
        assert stmts == ["ANALYZE orders", "OPTIMIZE orders"]

    def test_get_post_load_statements_with_schema(self) -> None:

        generator = MockDDLGenerator()
        tuning = TuningClauses(post_create_statements=["ANALYZE {table_name}"])
        stmts = generator.get_post_load_statements("orders", tuning, schema="sales")
        assert stmts == ["ANALYZE sales.orders"]


class TestDDLGeneratorProtocol:
    def test_protocol_runtime_checkable(self) -> None:

        generator = MockDDLGenerator()
        assert isinstance(generator, DDLGenerator)

    def test_noop_generator_is_protocol_compliant(self) -> None:

        generator = NoOpDDLGenerator(platform="test")
        assert isinstance(generator, DDLGenerator)

    def test_protocol_methods_exist(self) -> None:

        generator = MockDDLGenerator()

        assert callable(generator.generate_tuning_clauses)
        assert callable(generator.generate_create_table_ddl)
        assert callable(generator.get_post_load_statements)
        assert callable(generator.supports_tuning_type)

        assert generator.platform_name == "mock"


