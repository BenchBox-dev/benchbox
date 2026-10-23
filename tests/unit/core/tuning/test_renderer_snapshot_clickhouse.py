# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _preview_clauses(table_tuning: TableTuning):
    generator = get_ddl_generator("clickhouse")
    return generator.generate_tuning_clauses(table_tuning)


class _HostAdapter(ClickHouseWorkloadMixin):
    def __init__(self):
        self.logger = Mock()


class TestClickHousePreviewExecutionParity:
    def test_partitioning_and_sorting_render_identically(self):
        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        )
        table_tunings = {"lineitem": table_tuning}

        preview = _preview_clauses(table_tuning)
        assert preview.partition_by == "toYYYYMM(l_shipdate)"
        assert preview.sort_by == "l_orderkey"

        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT, l_shipdate DATE)"
        rendered = adapter._translate_schema_statement(statement, table_tunings)

        assert f"PARTITION BY ({preview.partition_by})" in rendered
        assert f"ORDER BY ({preview.sort_by})" in rendered

    def test_no_table_tuning_falls_back_to_engine_mandatory_baseline(self):
        adapter = _HostAdapter()
        statement = "CREATE TABLE untuned_table (id INT)"

        rendered = adapter._translate_schema_statement(statement, {"lineitem": Mock()})

        assert "ORDER BY tuple()" in rendered

    def test_tuning_disabled_falls_back_to_engine_mandatory_baseline(self):
        adapter = _HostAdapter()
        statement = "CREATE TABLE lineitem (l_orderkey INT PRIMARY KEY, l_shipdate DATE)"

        rendered = adapter._translate_schema_statement(statement, None)

        assert "ORDER BY (l_orderkey)" in rendered
        assert "PARTITION BY" not in rendered
