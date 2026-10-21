from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.tuning import TuningColumn
from benchbox.core.tuning.generators.cedardb import CedarDBDDLGenerator
from benchbox.core.tuning.interface import TableTuning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _column(name: str, kind: str, order: int = 1) -> TuningColumn:
    return TuningColumn(name=name, type=kind, order=order)


@pytest.mark.parametrize("kind", ["DATE", "TIMESTAMP", "INTEGER", "VARCHAR"])
def test_every_column_type_is_partitioned_by_hash(kind: str) -> None:
    generator = CedarDBDDLGenerator()

    clauses = generator.generate_tuning_clauses(TableTuning(table_name="orders", partitioning=[_column("o_key", kind)]))

    assert generator.platform_name == "cedardb"
    assert clauses.partition_by == "PARTITION BY HASH (o_key)"


def test_a_requested_range_strategy_is_ignored() -> None:
    generator = CedarDBDDLGenerator()
    options = SimpleNamespace(partition_strategy="range")

    clauses = generator.generate_tuning_clauses(
        TableTuning(table_name="orders", partitioning=[_column("o_orderdate", "DATE")]), options
    )

    assert clauses.partition_by == "PARTITION BY HASH (o_orderdate)"


def test_clustering_sorting_and_distribution_produce_no_clauses() -> None:
    generator = CedarDBDDLGenerator()

    clauses = generator.generate_tuning_clauses(
        TableTuning(
            table_name="orders",
            clustering=[_column("o_orderkey", "INTEGER")],
            sorting=[_column("o_orderdate", "DATE")],
            distribution=[_column("o_custkey", "INTEGER")],
        )
    )

    assert clauses.is_empty()
    assert generator.generate_tuning_clauses(None).is_empty()


def test_partitions_are_left_to_the_server() -> None:
    generator = CedarDBDDLGenerator()
    tuning = TableTuning(table_name="orders", partitioning=[_column("o_orderkey", "INTEGER")])
    clauses = generator.generate_tuning_clauses(tuning)

    assert generator.generate_partition_children("orders", [], clauses, table_tuning=tuning) == []
