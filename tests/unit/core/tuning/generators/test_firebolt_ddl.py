from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.firebolt import FireboltDDLGenerator
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_generate_tuning_clauses_handles_distribution_partition_and_logs():
    generator = FireboltDDLGenerator()
    table_tuning = TableTuning(
        table_name="lineitem",
        distribution=[
            TuningColumn(name="l_orderkey", type="BIGINT", order=2),
            TuningColumn(name="l_partkey", type="BIGINT", order=1),
        ],
        partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        sorting=[TuningColumn(name="l_quantity", type="INTEGER", order=1)],
        clustering=[TuningColumn(name="l_suppkey", type="BIGINT", order=1)],
    )

    with patch("benchbox.core.tuning.generators.firebolt.logger") as mock_logger:
        clauses = generator.generate_tuning_clauses(table_tuning)

    assert clauses.distribute_by == "PRIMARY INDEX (l_partkey, l_orderkey)"
    assert clauses.partition_by == "l_shipdate"
    assert mock_logger.info.call_count >= 2


def test_generate_create_table_ddl_and_column_mapping():
    generator = FireboltDDLGenerator()
    columns = [
        ColumnDefinition("id", "INTEGER", ColumnNullability.NOT_NULL),
        ColumnDefinition("name", "VARCHAR", ColumnNullability.NULLABLE, default_value="'n'"),
    ]

    tuning = generator.generate_tuning_clauses(
        TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="id", type="INTEGER", order=1)],
            partitioning=[TuningColumn(name="name", type="VARCHAR", order=1)],
        )
    )
    ddl = generator.generate_create_table_ddl("orders", columns, tuning=tuning, if_not_exists=True, schema="bench")

    assert "CREATE TABLE IF NOT EXISTS bench.orders" in ddl
    assert "PRIMARY INDEX (id)" in ddl
    assert "PARTITION BY name" in ddl
    assert ddl.endswith(";")

    col_list = generator.generate_column_list(columns)
    assert '"id" INT NOT NULL' in col_list
    assert '"name" TEXT DEFAULT ' in col_list


def test_firebolt_type_mapping_falls_back_to_original_for_unknown():
    generator = FireboltDDLGenerator()
    assert generator._map_to_firebolt_type("BOOLEAN") == "BOOLEAN"
    assert generator._map_to_firebolt_type("DECIMAL(10,2)") == "DECIMAL(38, 9)"
    assert generator._map_to_firebolt_type("CUSTOM") == "CUSTOM"


