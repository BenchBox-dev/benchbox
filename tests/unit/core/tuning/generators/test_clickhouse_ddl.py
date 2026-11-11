from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.clickhouse import ClickHouseDDLGenerator
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_generate_tuning_clauses_combines_cluster_sort_and_partition():
    generator = ClickHouseDDLGenerator()
    table_tuning = TableTuning(
        table_name="events",
        clustering=[TuningColumn(name="user_id", type="BIGINT", order=2)],
        sorting=[
            TuningColumn(name="event_ts", type="TIMESTAMP", order=1),
            TuningColumn(name="user_id", type="BIGINT", order=3),
        ],
        partitioning=[TuningColumn(name="event_ts", type="TIMESTAMP", order=1)],
        distribution=[TuningColumn(name="tenant_id", type="BIGINT", order=1)],
    )

    with patch("benchbox.core.tuning.generators.clickhouse.logger") as mock_logger:
        clauses = generator.generate_tuning_clauses(table_tuning)

    assert clauses.sort_by == "user_id, event_ts"
    assert clauses.partition_by == "toYYYYMM(event_ts)"
    mock_logger.info.assert_called_once()


def test_generate_create_table_ddl_with_and_without_tuning():
    generator = ClickHouseDDLGenerator(index_granularity=4096)
    columns = [ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL)]

    tuned = generator.generate_create_table_ddl(
        "orders",
        columns,
        tuning=generator.generate_tuning_clauses(
            TableTuning(table_name="orders", sorting=[TuningColumn(name="id", type="BIGINT", order=1)])
        ),
        if_not_exists=True,
        schema="analytics",
    )
    assert "CREATE TABLE IF NOT EXISTS analytics.orders" in tuned
    assert "ORDER BY (id)" in tuned
    assert "SETTINGS index_granularity = 4096" in tuned

    untuned = generator.generate_create_table_ddl("orders", columns, tuning=None)
    assert "ORDER BY tuple()" in untuned


def test_generate_create_table_ddl_wraps_partition_by_in_parens():

    generator = ClickHouseDDLGenerator()
    columns = [ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL)]

    single = generator.generate_create_table_ddl(
        "orders",
        columns,
        tuning=generator.generate_tuning_clauses(
            TableTuning(
                table_name="orders",
                partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
            )
        ),
    )
    assert "PARTITION BY (toYYYYMM(order_date))" in single

    multi = generator.generate_create_table_ddl(
        "orders",
        columns,
        tuning=generator.generate_tuning_clauses(
            TableTuning(
                table_name="orders",
                partitioning=[
                    TuningColumn(name="region", type="VARCHAR", order=1),
                    TuningColumn(name="shard", type="INTEGER", order=2),
                ],
            )
        ),
    )
    assert "PARTITION BY (region, shard)" in multi


def test_column_mapping_generation_and_post_load_statement():
    generator = ClickHouseDDLGenerator()
    assert generator._map_to_clickhouse_type("DOUBLE") == "Float64"
    assert generator._map_to_clickhouse_type("VARCHAR(10)") == "String"
    assert generator._map_to_clickhouse_type("CUSTOM") == "CUSTOM"

    cols = [
        ColumnDefinition("name", "VARCHAR", ColumnNullability.NULLABLE, default_value="'x'"),
        ColumnDefinition("amount", "DECIMAL", ColumnNullability.NOT_NULL),
    ]
    col_list = generator.generate_column_list(cols)
    assert "`name` Nullable(String) DEFAULT 'x'" in col_list
    assert "`amount` Decimal(18, 2)" in col_list

    assert generator.get_post_load_statements("orders", schema="analytics") == ["OPTIMIZE TABLE analytics.orders"]


