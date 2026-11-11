# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json

import pytest

from benchbox.core.tuning import TableTuning
from benchbox.core.tuning.ddl_generator import ColumnDefinition, get_ddl_generator
from benchbox.core.tuning.generators.starrocks import DEFAULT_BUCKET_COUNT, StarRocksDDLGenerator
from benchbox.core.tuning.interface import TuningColumn

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def gen() -> StarRocksDDLGenerator:
    return StarRocksDDLGenerator()


class TestFactoryWiring:
    def test_get_ddl_generator_returns_real_generator(self):
        generator = get_ddl_generator("starrocks")
        assert isinstance(generator, StarRocksDDLGenerator)

    def test_platform_name(self, gen):
        assert gen.platform_name == "starrocks"

    def test_supported_tuning_types(self, gen):
        assert gen.supports_tuning_type("partitioning")
        assert gen.supports_tuning_type("sorting")
        assert gen.supports_tuning_type("distribution")
        assert not gen.supports_tuning_type("clustering")


class TestClauseRenderers:
    def test_distribution_backticks_column_and_defaults_to_8_buckets(self, gen):
        assert gen.render_distribution_clause("l_orderkey") == "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"

    def test_distribution_default_bucket_count_constant(self):
        assert DEFAULT_BUCKET_COUNT == 8

    def test_distribution_custom_bucket_count(self, gen):
        assert gen.render_distribution_clause("c_custkey", buckets=32) == "DISTRIBUTED BY HASH(`c_custkey`) BUCKETS 32"

    def test_distribution_generator_level_bucket_override(self):
        gen16 = StarRocksDDLGenerator(default_bucket_count=16)
        assert gen16.render_distribution_clause("ss_item_sk") == "DISTRIBUTED BY HASH(`ss_item_sk`) BUCKETS 16"

    def test_partition_clause(self, gen):
        assert gen.render_partition_clause("l_shipdate") == "PARTITION BY (l_shipdate)"

    def test_order_by_clause(self, gen):
        assert gen.render_order_by_clause("l_orderkey, l_linenumber") == "ORDER BY (l_orderkey, l_linenumber)"


class TestGenerateTuningClauses:
    def test_distribution_clause_rendered_into_distribute_by(self, gen):
        tt = TableTuning(table_name="lineitem", distribution=[TuningColumn("l_orderkey", "INT", 1)])
        clauses = gen.generate_tuning_clauses(tt)
        assert clauses.distribute_by == "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"
        assert clauses.additional_clauses == []
        assert clauses.platform == "starrocks"

    def test_single_distribution_column_when_multiple_configured(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[
                TuningColumn("l_suppkey", "INT", 2),
                TuningColumn("l_orderkey", "INT", 1),
            ],
        )
        clauses = gen.generate_tuning_clauses(tt)
        assert clauses.distribute_by == "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"

    def test_partition_by_ordered(self, gen):
        tt = TableTuning(
            table_name="store_sales",
            partitioning=[
                TuningColumn("ss_item_sk", "INT", 2),
                TuningColumn("ss_sold_date_sk", "INT", 1),
            ],
        )
        clauses = gen.generate_tuning_clauses(tt)
        assert clauses.partition_by == "PARTITION BY (ss_sold_date_sk, ss_item_sk)"

    def test_order_by_from_sorting(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            sorting=[
                TuningColumn("l_linenumber", "INT", 2),
                TuningColumn("l_orderkey", "INT", 1),
            ],
        )
        clauses = gen.generate_tuning_clauses(tt)
        assert clauses.order_by == "ORDER BY (l_orderkey, l_linenumber)"

    def test_all_three_tuning_types_together(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn("l_orderkey", "INT", 1)],
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
            sorting=[TuningColumn("l_linenumber", "INT", 1)],
        )
        clauses = gen.generate_tuning_clauses(tt)
        assert clauses.distribute_by == "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"
        assert clauses.partition_by == "PARTITION BY (l_shipdate)"
        assert clauses.order_by == "ORDER BY (l_linenumber)"

    def test_clustering_logs_but_emits_no_clause(self, gen, caplog):
        tt = TableTuning(table_name="lineitem", clustering=[TuningColumn("l_shipmode", "VARCHAR(10)", 1)])
        with caplog.at_level("INFO", logger="benchbox.core.tuning.generators.starrocks"):
            clauses = gen.generate_tuning_clauses(tt)
        assert clauses.is_empty()
        assert any("Clustering hint" in rec.message for rec in caplog.records)

    def test_none_table_tuning_returns_empty(self, gen):
        clauses = gen.generate_tuning_clauses(None)
        assert clauses.is_empty()
        assert clauses.distribute_by is None
        assert clauses.partition_by is None
        assert clauses.order_by is None


class TestDryRunShape:
    def test_to_dict_only_includes_set_fields(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn("l_orderkey", "INT", 1)],
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
            sorting=[TuningColumn("l_linenumber", "INT", 1)],
        )
        d = gen.generate_tuning_clauses(tt).to_dict()
        assert d == {
            "distribute_by": "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8",
            "partition_by": "PARTITION BY (l_shipdate)",
            "order_by": "ORDER BY (l_linenumber)",
            "platform": "starrocks",
        }

    def test_to_json_is_serializable(self, gen):
        tt = TableTuning(table_name="orders", distribution=[TuningColumn("o_orderkey", "INT", 1)])
        payload = gen.generate_tuning_clauses(tt).to_json()
        parsed = json.loads(payload)
        assert parsed["distribute_by"] == "DISTRIBUTED BY HASH(`o_orderkey`) BUCKETS 8"


class TestGenerateCreateTableDdl:
    def test_no_tuning_emits_engine_mandatory_distribution_only(self, gen):
        cols = [ColumnDefinition("r_regionkey", "INT"), ColumnDefinition("r_name", "VARCHAR(25)")]
        ddl = gen.generate_create_table_ddl("region", cols)
        assert "DISTRIBUTED BY HASH(`r_regionkey`) BUCKETS 8" in ddl
        assert "PARTITION BY" not in ddl
        assert "ORDER BY" not in ddl
        assert ddl.count("DISTRIBUTED BY") == 1

    def test_tuned_ddl_renders_all_clauses_in_order_without_duplication(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn("l_orderkey", "INT", 1)],
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
            sorting=[TuningColumn("l_linenumber", "INT", 1)],
        )
        clauses = gen.generate_tuning_clauses(tt)
        cols = [
            ColumnDefinition("l_orderkey", "BIGINT"),
            ColumnDefinition("l_linenumber", "INT"),
            ColumnDefinition("l_shipdate", "DATE"),
        ]
        ddl = gen.generate_create_table_ddl("lineitem", cols, clauses)

        assert "PARTITION BY (l_shipdate)" in ddl
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in ddl
        assert "ORDER BY (l_linenumber)" in ddl
        assert ddl.count("DISTRIBUTED BY") == 1
        assert ddl.index("PARTITION BY") < ddl.index("DISTRIBUTED BY") < ddl.index("ORDER BY")

    def test_tuned_distribution_overrides_first_column(self, gen):
        tt = TableTuning(table_name="orders", distribution=[TuningColumn("o_custkey", "INT", 1)])
        clauses = gen.generate_tuning_clauses(tt)
        cols = [ColumnDefinition("o_orderkey", "BIGINT"), ColumnDefinition("o_custkey", "INT")]
        ddl = gen.generate_create_table_ddl("orders", cols, clauses)
        assert "DISTRIBUTED BY HASH(`o_custkey`) BUCKETS 8" in ddl
        assert "HASH(`o_orderkey`)" not in ddl


class TestInlineClausesContract:
    def test_no_bare_column_and_single_distributed_by(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn("l_orderkey", "INT", 1)],
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
            sorting=[TuningColumn("l_linenumber", "INT", 1)],
        )
        inline = gen.generate_tuning_clauses(tt).get_inline_clauses()
        assert "l_orderkey" not in inline
        assert "l_shipdate" not in inline
        assert "l_linenumber" not in inline
        assert inline.count("PARTITION BY (l_shipdate)") == 1
        assert inline.count("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8") == 1
        assert inline.count("ORDER BY (l_linenumber)") == 1

    def test_distributed_by_after_partition_by(self, gen):
        tt = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn("l_orderkey", "INT", 1)],
            partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
        )
        inline = gen.generate_tuning_clauses(tt).get_inline_clauses()
        assert inline.index("PARTITION BY (l_shipdate)") < inline.index("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8")


