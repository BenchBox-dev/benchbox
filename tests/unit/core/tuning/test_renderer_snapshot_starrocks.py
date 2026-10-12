# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.starrocks.workload import StarRocksWorkloadMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _preview_clauses(table_tuning: TableTuning):
    generator = get_ddl_generator("starrocks")
    return generator.generate_tuning_clauses(table_tuning)


class _HostAdapter(StarRocksWorkloadMixin):
    def __init__(self):
        self.logger = Mock()


_BASELINE_REGION_DDL = (
    "CREATE TABLE region (r_regionkey INT, r_name VARCHAR(25))\n"
    "DUPLICATE KEY(`r_regionkey`)\n"
    "DISTRIBUTED BY HASH(`r_regionkey`) BUCKETS 8"
)


class TestStarRocksEngineMandatoryBaselineUnchanged:
    def test_untuned_output_is_byte_identical(self):
        adapter = _HostAdapter()
        statement = "CREATE TABLE region (r_regionkey INT, r_name VARCHAR(25))"
        assert adapter._optimize_table_definition(statement, None) == _BASELINE_REGION_DDL

    def test_tuning_disabled_passes_none_and_gets_baseline(self):
        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_shipdate DATE)"
        rendered = adapter._optimize_table_definition(statement, None)
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in rendered
        assert "PARTITION BY" not in rendered
        assert rendered.count("DISTRIBUTED BY") == 1

    def test_table_absent_from_tunings_gets_baseline(self):
        adapter = _HostAdapter()
        statement = "CREATE TABLE untuned_table (id INT, payload VARCHAR(20))"
        rendered = adapter._optimize_table_definition(statement, {"lineitem": Mock()})
        assert "DISTRIBUTED BY HASH(`id`) BUCKETS 8" in rendered
        assert "PARTITION BY" not in rendered
        assert rendered.count("DISTRIBUTED BY") == 1


class TestStarRocksPreviewExecutionParity:
    def test_partitioning_distribution_and_sorting_render_identically(self):
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_linenumber", type="INTEGER", order=1)],
        )
        table_tunings = {"lineitem": table_tuning}

        preview = _preview_clauses(table_tuning)
        assert preview.partition_by == "PARTITION BY (l_shipdate)"
        assert preview.distribute_by == "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"
        assert preview.order_by == "ORDER BY (l_linenumber)"
        assert preview.additional_clauses == []

        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_linenumber INT, l_shipdate DATE)"
        rendered = adapter._optimize_table_definition(statement, table_tunings)

        assert preview.partition_by in rendered
        assert preview.distribute_by in rendered
        assert preview.order_by in rendered

        assert rendered.count("DISTRIBUTED BY") == 1
        assert rendered.index("PARTITION BY") < rendered.index("DISTRIBUTED BY") < rendered.index("ORDER BY")

    def test_tuned_distribution_overrides_first_column_in_execution(self):
        table_tuning = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_custkey", type="INTEGER", order=1)],
        )
        preview = _preview_clauses(table_tuning)
        assert preview.distribute_by == "DISTRIBUTED BY HASH(`o_custkey`) BUCKETS 8"

        adapter = _HostAdapter()
        statement = "CREATE TABLE orders (o_orderkey INT, o_custkey INT, o_orderdate DATE)"
        rendered = adapter._optimize_table_definition(statement, {"orders": table_tuning})

        assert "DISTRIBUTED BY HASH(`o_custkey`) BUCKETS 8" in rendered
        assert "HASH(`o_orderkey`)" not in rendered
        assert rendered.count("DISTRIBUTED BY") == 1

    def test_case_insensitive_table_lookup(self):
        table_tuning = TableTuning(
            table_name="LINEITEM",
            distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        )
        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_shipdate DATE)"
        rendered = adapter._optimize_table_definition(statement, {"LINEITEM": table_tuning})
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in rendered


class TestStarRocksAppliedLedgerInstrumentation:
    def _host_with_ledger(self):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger

        adapter = _HostAdapter()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        return adapter

    def test_tuned_clauses_recorded_with_mechanism_and_table(self):
        from benchbox.core.tuning.applied_ledger import APPLIED_UNVERIFIED, PHASE_DDL

        adapter = self._host_with_ledger()
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_linenumber", type="INTEGER", order=1)],
        )
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_linenumber INT, l_shipdate DATE)"
        adapter._optimize_table_definition(statement, {"lineitem": table_tuning})

        ledger = adapter._applied_tuning_ledger
        recorded = [s.statement for s in ledger.statements]
        assert "PARTITION BY (l_shipdate)" in recorded
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in recorded
        assert "ORDER BY (l_linenumber)" in recorded
        assert all(s.phase == PHASE_DDL and s.mechanism == "starrocks_ddl_generator" for s in ledger.statements)
        assert all(s.table == "lineitem" for s in ledger.statements)
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_untuned_records_nothing(self):
        adapter = self._host_with_ledger()
        statement = "CREATE TABLE region (r_regionkey INT, r_name VARCHAR(25))"
        adapter._optimize_table_definition(statement, None)
        assert adapter._applied_tuning_ledger.is_empty()

    def test_engine_mandatory_distribution_not_recorded_as_tuning(self):
        adapter = self._host_with_ledger()
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
        )
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_shipdate DATE)"
        adapter._optimize_table_definition(statement, {"lineitem": table_tuning})
        recorded = [s.statement for s in adapter._applied_tuning_ledger.statements]
        assert recorded == ["PARTITION BY (l_shipdate)"]

    def test_tuned_run_records_the_rendered_primary_key_clause(self):
        adapter = self._host_with_ledger()
        statement = "CREATE TABLE nation (\n  n_nationkey INT,\n  n_name VARCHAR(25),\n  PRIMARY KEY (n_nationkey)\n)"

        rendered = adapter._optimize_table_definition(statement, {})

        assert "PRIMARY KEY (n_nationkey)" in rendered
        [recorded] = adapter._applied_tuning_ledger.statements
        assert (recorded.statement, recorded.table) == ("PRIMARY KEY (n_nationkey)", "nation")

    def test_untuned_primary_key_clause_is_not_recorded(self):
        adapter = self._host_with_ledger()
        statement = "CREATE TABLE nation (\n  n_nationkey INT,\n  n_name VARCHAR(25),\n  PRIMARY KEY (n_nationkey)\n)"

        rendered = adapter._optimize_table_definition(statement, None)

        assert "PRIMARY KEY (n_nationkey)" in rendered
        assert adapter._applied_tuning_ledger.is_empty()

    def test_no_ledger_attribute_is_a_safe_noop(self):
        adapter = _HostAdapter()
        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        )
        statement = "CREATE TABLE lineitem (l_orderkey INT)"
        rendered = adapter._optimize_table_definition(statement, {"lineitem": table_tuning})
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in rendered


class TestStarRocksDryRunEntryParity:
    def test_dry_run_entry_matches_execution(self):
        from benchbox.core.dryrun import _build_table_ddl_entry

        table_tuning = TableTuning(
            table_name="lineitem",
            distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_linenumber", type="INTEGER", order=1)],
        )
        preview = _preview_clauses(table_tuning)
        entry = _build_table_ddl_entry(preview)
        ddl_clauses = entry["ddl_clauses"] or ""

        assert "l_orderkey" not in ddl_clauses.splitlines()
        assert "l_shipdate" not in ddl_clauses.splitlines()
        assert "l_linenumber" not in ddl_clauses.splitlines()
        assert ddl_clauses.count("DISTRIBUTED BY") == 1
        assert "PARTITION BY (l_shipdate)" in ddl_clauses
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in ddl_clauses
        assert "ORDER BY (l_linenumber)" in ddl_clauses

        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_linenumber INT, l_shipdate DATE)"
        rendered = adapter._optimize_table_definition(statement, {"lineitem": table_tuning})
        assert "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in rendered
        assert rendered.count("DISTRIBUTED BY") == 1
        assert rendered.index("PARTITION BY") < rendered.index("DISTRIBUTED BY") < rendered.index("ORDER BY")
