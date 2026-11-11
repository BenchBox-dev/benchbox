# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock

import pytest

from benchbox.core.tuning import TableTuning
from benchbox.core.tuning.ddl_generator import ColumnDefinition
from benchbox.core.tuning.generators.questdb import QuestDBDDLGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

TPCH_TABLES = ["lineitem", "orders", "customer", "part", "partsupp", "supplier", "nation", "region"]


@pytest.fixture
def gen():
    return QuestDBDDLGenerator()


class TestGenerateTuningClausesNoTableTuning:
    def test_none_returns_empty_clauses(self, gen):
        clauses = gen.generate_tuning_clauses(None)
        assert not clauses.table_properties

    def test_table_tuning_without_timestamp_returns_empty(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="partsupp"))
        assert not clauses.table_properties


class TestGenerateTuningClausesLineitem:
    def test_lineitem_table_properties_exact(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="lineitem"))
        assert clauses.table_properties == "timestamp(l_shipdate) PARTITION BY MONTH"

    def test_lineitem_contains_timestamp_prefix(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="lineitem"))
        assert "timestamp(l_shipdate)" in clauses.table_properties

    def test_lineitem_contains_partition_by_month(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="lineitem"))
        assert "PARTITION BY MONTH" in clauses.table_properties


class TestGenerateTuningClausesOrders:
    def test_orders_timestamp_on_orderdate(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="orders"))
        assert "timestamp(o_orderdate)" in clauses.table_properties

    def test_orders_partition_by_month(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="orders"))
        assert "PARTITION BY MONTH" in clauses.table_properties


class TestGenerateTuningClausesPartsuppNoTimestamp:
    def test_partsupp_has_no_timestamp(self, gen):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name="partsupp"))
        assert not clauses.table_properties


class TestCustomPartitionByViaPlatformOpts:
    def test_platform_opts_partition_by_day(self, gen):
        from benchbox.core.tuning import TableTuning, TuningColumn
        from benchbox.core.tuning.interface import TuningType

        tt = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
        )
        platform_opts = MagicMock()
        platform_opts.partition_by = "DAY"

        clauses = gen.generate_tuning_clauses(tt, platform_opts)
        assert "PARTITION BY DAY" in clauses.table_properties


class TestGenerateCreateTableDdl:
    def test_lineitem_ddl_contains_timestamp_suffix(self, gen):
        tt = TableTuning(table_name="lineitem")
        clauses = gen.generate_tuning_clauses(tt)
        cols = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        ddl = gen.generate_create_table_ddl("lineitem", cols, clauses)
        assert "timestamp(l_shipdate) PARTITION BY MONTH" in ddl

    def test_partsupp_ddl_has_no_partition_by(self, gen):
        tt = TableTuning(table_name="partsupp")
        clauses = gen.generate_tuning_clauses(tt)
        cols = [ColumnDefinition("ps_partkey", "BIGINT")]
        ddl = gen.generate_create_table_ddl("partsupp", cols, clauses)
        assert "PARTITION BY" not in ddl


class TestApplyTypeMappings:
    def test_returnflag_varchar_mapped_to_symbol(self, gen):
        cols = [ColumnDefinition("l_returnflag", "VARCHAR")]
        mapped = gen._apply_type_mappings("lineitem", cols)
        assert mapped[0].data_type == "SYMBOL"

    def test_shipdate_date_mapped_to_timestamp(self, gen):
        cols = [ColumnDefinition("l_shipdate", "DATE")]
        mapped = gen._apply_type_mappings("lineitem", cols)
        assert mapped[0].data_type == "TIMESTAMP"

    def test_orderkey_bigint_unchanged(self, gen):
        cols = [ColumnDefinition("l_orderkey", "BIGINT")]
        mapped = gen._apply_type_mappings("lineitem", cols)
        assert mapped[0].data_type == "BIGINT"


class TestAllTpchTablesParametrized:
    @pytest.mark.parametrize("table", TPCH_TABLES)
    def test_generate_tuning_clauses_does_not_raise(self, gen, table):
        clauses = gen.generate_tuning_clauses(TableTuning(table_name=table))
        assert clauses is not None


