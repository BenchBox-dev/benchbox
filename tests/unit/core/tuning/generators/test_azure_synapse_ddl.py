from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.ddl_generator import ColumnDefinition, ColumnNullability
from benchbox.core.tuning.generators.azure_synapse import (
    AzureSynapseDDLGenerator,
    DistributionType,
    IndexType,
)
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_generate_tuning_clauses_defaults_and_populated():
    generator = AzureSynapseDDLGenerator(distribution_default=DistributionType.ROUND_ROBIN)

    empty = generator.generate_tuning_clauses(None)
    assert empty.distribute_by == "ROUND_ROBIN"

    table_tuning = TableTuning(
        table_name="lineitem",
        distribution=[TuningColumn(name="l_orderkey", type="BIGINT", order=2)],
        partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        sorting=[TuningColumn(name="l_partkey", type="BIGINT", order=1)],
        clustering=[TuningColumn(name="l_suppkey", type="BIGINT", order=1)],
    )
    with patch("benchbox.core.tuning.generators.azure_synapse.logger") as mock_logger:
        clauses = generator.generate_tuning_clauses(table_tuning)
    assert clauses.distribute_by == "HASH([l_orderkey])"
    assert clauses.partition_by == "[l_shipdate]"
    assert mock_logger.info.call_count >= 2


def test_generate_create_table_ddl_and_post_load_statements():
    generator = AzureSynapseDDLGenerator(index_type=IndexType.HEAP)
    columns = [
        ColumnDefinition("id", "INTEGER", ColumnNullability.NOT_NULL),
        ColumnDefinition("name", "VARCHAR(20)", ColumnNullability.NULLABLE),
    ]
    ddl = generator.generate_create_table_ddl(
        "customers",
        columns,
        tuning=generator.generate_tuning_clauses(TableTuning(table_name="customers")),
        if_not_exists=True,
        schema="dbo",
    )

    assert "CREATE TABLE [dbo].[customers]" in ddl
    assert "DISTRIBUTION = ROUND_ROBIN" in ddl
    assert "HEAP" in ddl
    assert ddl.endswith(";")

    statements = generator.get_post_load_statements("customers", schema="dbo")
    assert statements == ["UPDATE STATISTICS [dbo].[customers]"]


def test_column_type_mapping_and_column_list_generation():
    generator = AzureSynapseDDLGenerator()
    assert generator._map_to_synapse_type("BOOLEAN") == "BIT"
    assert generator._map_to_synapse_type("TIMESTAMP") == "DATETIME2"
    assert generator._map_to_synapse_type("CUSTOM") == "CUSTOM"

    columns = [
        ColumnDefinition("flag", "BOOLEAN", ColumnNullability.NULLABLE, default_value="0"),
        ColumnDefinition("id", "BIGINT", ColumnNullability.NOT_NULL),
    ]
    col_list = generator.generate_column_list(columns)
    assert "[flag] BIT NULL DEFAULT 0" in col_list
    assert "[id] BIGINT NOT NULL" in col_list


